"""Read-only integration status and explicit repair for Settings."""
from pathlib import Path
import sys

from PySide6.QtCore import Qt, QUrl, QObject, QEvent, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QMessageBox

from . import shell_integration as shell

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
        notice = QMessageBox(QMessageBox.Icon.Warning, "Luma · Explorer thumbnails",
                             "Explorer thumbnail setup could not finish. You can keep viewing images. "
                             "Open Settings and use Register this copy / repair.\n\n" + error,
                             QMessageBox.StandardButton.Ok, self.window)
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
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 12, 0, 0)
        layout.setSpacing(9)
        description = QLabel(
            "Choose Luma in Windows Default Apps for the formats you want. Thumbnail "
            "support is prepared automatically by the Windows app. Existing common-image "
            "thumbnail providers are preserved. RAW thumbnails prefer embedded previews; "
            "HDR / EXR thumbnails are tone-mapped SDR."
        )
        description.setWordWrap(True)
        layout.addWidget(description)
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        self.status_label.setTextFormat(Qt.TextFormat.PlainText)
        self.status_label.setObjectName("explorer_status")
        layout.addWidget(self.status_label)
        row = QHBoxLayout()
        self.repair = QPushButton("Register this copy / repair")
        self.repair.setObjectName("repair_explorer")
        self.defaults = QPushButton("Windows Default Apps")
        self.defaults.setObjectName("windows_default_apps")
        row.addWidget(self.repair)
        row.addWidget(self.defaults)
        layout.addLayout(row)
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
        message = messages.get(state.state, state.state)
        if state.detail:
            message += "\n" + state.detail
        if _startup_registration_error and state.state != "installed":
            message += "\nAutomatic setup: " + _startup_registration_error
        self.status_label.setText(message)

    def _confirm(self):
        message = (
            "Repair Explorer thumbnail support and Luma's Open With / Default Apps entries "
            "for your Windows account?\n\nNo administrator access is required. Windows defaults "
            "are not changed automatically. Keep this portable folder in place; use repair "
            "after moving it. Registration owned by another Luma copy will move to this copy. "
            "Changes made by other apps are preserved."
        )
        return QMessageBox.question(
            self, "Luma · Explorer integration", message,
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
        try:
            shell.install(Path(sys.executable))
            _startup_registration_error = ""
        except (shell.ShellIntegrationError, OSError) as exception:
            error = str(exception)
        finally:
            self.refresh()
        if error:
            self.status_label.setText("Could not complete registration.\n" + error)
            QMessageBox.warning(self, "Luma · Explorer integration", error)

    def _repair_clicked(self):
        self._apply()

    def _open_defaults(self):
        if sys.platform == "win32":
            if not QDesktopServices.openUrl(QUrl("ms-settings:defaultapps")):
                self.status_label.setText("Open Windows Settings → Apps → Default apps and choose Luma.")
