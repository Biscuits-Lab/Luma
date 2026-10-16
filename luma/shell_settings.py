"""Explicit, current-user Explorer integration controls for Settings."""
from pathlib import Path
import sys

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QCheckBox, QPushButton, QMessageBox

from . import shell_integration as shell


class ExplorerIntegrationPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 12, 0, 0)
        layout.setSpacing(9)
        self.toggle = QCheckBox("Explorer thumbnails")
        self.toggle.setObjectName("explorer_thumbnails")
        layout.addWidget(self.toggle)
        description = QLabel(
            "Optional Windows x64 integration for this user. After enabling, choose Luma "
            "in Windows Default Apps for the formats you want. Existing common-image "
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
        self.toggle.clicked.connect(self._toggle_clicked)
        self.repair.clicked.connect(self._repair_clicked)
        self.defaults.clicked.connect(self._open_defaults)
        self.refresh()

    def refresh(self):
        try:
            state = shell.status()
        except (shell.ShellIntegrationError, OSError) as error:
            state = shell.Status("blocked", detail=str(error))
        self._state = state.state
        self.toggle.setChecked(state.thumbnails_enabled)
        available = state.state not in ("unavailable", "blocked")
        self.toggle.setEnabled(available)
        self.repair.setEnabled(available)
        self.defaults.setEnabled(sys.platform == "win32")
        messages = {
            "unavailable": "Available in the packaged Windows x64 app only.",
            "not_installed": "Off. No Explorer integration has been registered by Luma.",
            "association_only": "Thumbnails are off. Luma remains available as your default viewer.",
            "installed": "Enabled for this user. Keep this portable folder in its current location.",
            "stale": "Registration points at another copy. Register this copy to use its current location.",
            "modified": "Another app changed some registered values. Luma will preserve those changes.",
            "recovery_required": "An interrupted registration needs recovery. Use repair or turn integration off.",
            "blocked": "Registration cannot be read safely. No changes were made.",
        }
        message = messages.get(state.state, state.state)
        if state.detail:
            message += "\n" + state.detail
        self.status_label.setText(message)

    def _confirm(self, enabling):
        message = (
            "Register Explorer thumbnail support and Luma's Open With / Default Apps entries "
            "for your Windows account?\n\nNo administrator access is required. Windows defaults "
            "are not changed automatically. Keep this portable folder in place; use repair "
            "after updating or moving it. You can turn this off here."
            if enabling else
            "Turn off Luma\'s Explorer thumbnail providers for your account?\n\n"
            "Your default-viewer choice and file-opening commands stay available. Existing common-image "
            "providers and later changes by other apps are preserved. Windows manages cached thumbnails separately."
        )
        return QMessageBox.question(
            self, "Luma · Explorer integration", message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) == QMessageBox.StandardButton.Yes

    def _apply(self, enabling):
        if not self._confirm(enabling):
            self.refresh()
            return
        self.toggle.setEnabled(False)
        self.repair.setEnabled(False)
        error = None
        try:
            if enabling:
                shell.install(Path(sys.executable))
            else:
                shell.disable_thumbnails()
        except (shell.ShellIntegrationError, OSError) as exception:
            error = str(exception)
        finally:
            self.refresh()
        if error:
            self.status_label.setText("Could not complete registration.\n" + error)
            QMessageBox.warning(self, "Luma · Explorer integration", error)

    def _toggle_clicked(self, enabled):
        self._apply(bool(enabled))

    def _repair_clicked(self):
        self._apply(True)

    def _open_defaults(self):
        if sys.platform == "win32":
            if not QDesktopServices.openUrl(QUrl("ms-settings:defaultapps")):
                self.status_label.setText("Open Windows Settings → Apps → Default apps and choose Luma.")
