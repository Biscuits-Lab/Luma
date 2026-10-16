"""Automatic Windows integration and reusable diagnostic controls."""
from pathlib import Path
import sys

from PySide6.QtCore import Qt, QUrl, QObject, QEvent, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QSizePolicy

from . import shell_integration as shell
from .i18n import tr, error_text, localize_buttons
from .dialogs import PlainMessageBox as QMessageBox

_startup_registration_started = False
_startup_registration_error = ""


class _StartupRegistration(QObject):
    """One background transaction; window closure suppresses all UI callbacks."""

    def __init__(self, window):
        super().__init__(window)
        from .workers import LatestWorker
        self.window = window
        self.closed = False
        self.worker = LatestWorker(self._prepare, self)
        self.worker.completed.connect(self._finished)
        self.worker.failed.connect(self._failed)
        window.installEventFilter(self)
        # Capture the Python worker itself: QObject teardown can clear this
        # controller's attributes before a bound destruction slot is invoked.
        window.destroyed.connect(lambda *_args, worker=self.worker: worker.close())
        self.worker.submit(Path(sys.executable))

    @staticmethod
    def _prepare(executable, cancelled):
        if cancelled():
            return None
        # Do not interrupt a registry transaction midway through its journal.
        return shell.prepare_startup_registration(executable)

    @Slot()
    def _close(self):
        self.closed = True
        self.worker.close()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Close:
            self._close()
        return False

    @Slot(int, object)
    def _finished(self, token, result):
        self.worker.close()

    @Slot(int, str)
    def _failed(self, token, error):
        global _startup_registration_error
        self.worker.close()
        _startup_registration_error = error
        if self.closed:
            return
        # A nonmodal notice does not replace image metadata or interrupt use.
        notice = QMessageBox(QMessageBox.Icon.Warning, tr("Luma · Explorer thumbnails"),
                             error_text("Explorer thumbnail setup could not finish. You can keep viewing images; "
                                        "see the technical details below. Your default image application has not been changed.", error),
                             QMessageBox.StandardButton.Ok, self.window)
        localize_buttons(notice)
        notice.setTextFormat(Qt.TextFormat.PlainText)
        notice.setWindowModality(Qt.WindowModality.NonModal)
        notice.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        notice.show()


def start_automatic_registration(window):
    """Call once after showing the app; source/non-Windows runs are read-only."""
    global _startup_registration_started
    if (_startup_registration_started or sys.platform != "win32"
            or not getattr(sys, "frozen", False)):
        return None
    _startup_registration_started = True
    controller = _StartupRegistration(window)
    # Keep Python ownership until the window closes, including while work runs.
    window._luma_startup_registration = controller
    return controller


class ExplorerIntegrationPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        from .settings_widgets import DetailsPanel, SettingsRow, badge, plain_label, separator
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.defaults = QPushButton(tr("Windows Default Apps"))
        self.defaults.setObjectName("windows_default_apps")
        self.defaults.setToolTip(tr("Choose which file formats Windows opens with Luma."))
        layout.addWidget(SettingsRow(tr("Default image viewer"), self.defaults))
        layout.addWidget(separator())
        thumbnail_row = QHBoxLayout()
        thumbnail_row.setSpacing(10)
        labels_widget = QWidget()
        labels = QVBoxLayout(labels_widget)
        labels.setContentsMargins(0, 0, 0, 0)
        labels.setSpacing(4)
        labels.addWidget(plain_label(tr("Explorer thumbnails")))
        self.status_badge = badge("")
        labels.addWidget(self.status_badge)
        thumbnail_row.addWidget(labels_widget, 1)
        self.repair = QPushButton(tr("Register this copy / repair"))
        self.repair.setObjectName("repair_explorer")
        self.repair.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.repair.setToolTip(tr("Thumbnail support is prepared automatically. Repair only if needed."))
        thumbnail_row.addWidget(self.repair, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(thumbnail_row)
        self.details = DetailsPanel(tr("Integration details"))
        self.status_label = plain_label("", wrap=True)
        self.status_label.setObjectName("explorer_status")
        self.status_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.details.body.addWidget(self.status_label)
        self.description = plain_label(
            tr("Choose Luma in Windows Default Apps for the formats you want. Thumbnail "
               "support is prepared automatically by the Windows app. Existing common-image "
               "thumbnail providers are preserved. RAW thumbnails prefer embedded previews; "
               "HDR / EXR thumbnails are tone-mapped SDR."), muted=True, wrap=True
        )
        self.details.body.addWidget(self.description)
        layout.addWidget(self.details)
        self.repair.clicked.connect(self._repair_clicked)
        self.defaults.clicked.connect(self._open_defaults)
        self.refresh()

    def refresh(self):
        try:
            state = shell.status()
        except (shell.ShellIntegrationError, OSError) as error:
            state = shell.Status("blocked", detail=str(error))
        self._state = state.state
        available = state.state not in ("unavailable", "blocked")
        self.repair.setEnabled(available)
        self.defaults.setEnabled(sys.platform == "win32")
        messages = {
            "unavailable": "Available in the packaged Windows x64 app only.",
            "not_installed": "Thumbnail registration is not ready. Use Register this copy / repair.",
            "association_only": "This older registration has no Luma thumbnail handlers. Use repair to prepare them.",
            "installed": "Thumbnail support is registered for this user. Keep this folder in its current location.",
            "stale": "Registration points at another copy. Register this copy to use its current location.",
            "modified": "Another app changed some registered values. Luma will preserve those changes.",
            "recovery_required": "An interrupted registration needs recovery. Use Register this copy / repair.",
            "blocked": "Registration cannot be read safely. No changes were made.",
        }
        message = tr(messages.get(state.state, "Could not determine the Explorer integration status."))
        if state.detail:
            message += "\n\n" + tr("Technical details:") + "\n" + state.detail
        if _startup_registration_error and state.state != "installed":
            message += "\n\n" + error_text("Automatic thumbnail setup did not finish. Use Register this copy / repair.", _startup_registration_error)
        self.status_label.setText(message)
        states = {
            "unavailable": "Windows app only",
            "not_installed": "Setup needed",
            "association_only": "Repair needed",
            "installed": "Registered",
            "stale": "Different location",
            "modified": "Changes preserved",
            "recovery_required": "Recovery needed",
            "blocked": "Unable to read",
        }
        # Native status includes successful association checks as well as
        # diagnostics. Only exact known success lines are safe to fold; new or
        # unrecognized diagnostics remain visible without parsing user paths.
        success_details = {f"Windows selects Luma's thumbnail handler for {extension}."
                           for extension in (".hdr", ".exr")}
        ordinary_associations = (state.state == "installed" and state.detail
                                 and all(line in success_details for line in state.detail.splitlines()))
        actionable_detail = bool(state.detail and state.state != "unavailable" and not ordinary_associations)
        association_attention = state.state == "installed" and actionable_detail
        self.status_badge.setText(tr("Check associations") if association_attention
                                  else tr(states.get(state.state, "Status unavailable")))
        self.status_badge.setToolTip(message)
        level = "ready" if state.state == "installed" and not association_attention else ("neutral" if state.state == "unavailable" else "attention")
        self.status_badge.setProperty("state", level)
        self.status_badge.style().unpolish(self.status_badge)
        self.status_badge.style().polish(self.status_badge)
        # Diagnostics and failure/recovery guidance must not disappear behind a
        # collapsed disclosure. Ordinary setup detail stays out of the main UI.
        if actionable_detail or (_startup_registration_error and state.state != "installed") or state.state in ("blocked", "recovery_required", "modified"):
            self.details.setExpanded(True)

    def _confirm(self):
        message = (
            "Repair Explorer thumbnail support and Luma's Open With / Default Apps entries "
            "for your Windows account?\n\nNo administrator access is required. Windows defaults "
            "are not changed automatically. Keep this portable folder in place; use repair "
            "after moving it. Registration owned by another Luma copy will move to this copy. "
            "Changes made by other apps are preserved."
        )
        return QMessageBox.question(
            self, tr("Luma · Explorer integration"), tr(message),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) == QMessageBox.StandardButton.Yes

    def _apply(self):
        global _startup_registration_error
        if not self._confirm():
            self.refresh()
            return
        self.repair.setEnabled(False)
        error = None
        from .installer import InstallerError, register_managed_installation
        from .update_installation import InstallationError, detect_installation
        try:
            installation = detect_installation()
            if installation.mode == "managed":
                register_managed_installation(installation.executable)
            else:
                shell.install(Path(sys.executable))
            _startup_registration_error = ""
        except (shell.ShellIntegrationError, InstallerError, InstallationError, OSError) as exception:
            error = str(exception)
        finally:
            self.refresh()
        if error:
            message = error_text("Could not complete registration. "
                                 "Use Register this copy / repair to try again.", error)
            self.status_label.setText(message)
            self.status_badge.setText(tr("Repair failed"))
            self.status_badge.setToolTip(message)
            self.status_badge.setProperty("state", "attention")
            self.status_badge.style().unpolish(self.status_badge)
            self.status_badge.style().polish(self.status_badge)
            self.details.setExpanded(True)
            QMessageBox.warning(self, tr("Luma · Explorer integration"), message)

    def _repair_clicked(self):
        self._apply()

    def _open_defaults(self):
        if sys.platform == "win32":
            if not QDesktopServices.openUrl(QUrl("ms-settings:defaultapps")):
                self.status_label.setText(tr("Open Windows Settings → Apps → Default apps and choose Luma."))
                self.details.setExpanded(True)
