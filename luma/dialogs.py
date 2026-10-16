"""Plain-text message boxes: filenames and diagnostics must never become HTML."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

from .i18n import install_qt_translations, localize_buttons


class PlainMessageBox(QMessageBox):
    """Same static-call shape as QMessageBox, with explicit plain-text rendering."""
    @classmethod
    def _show(cls, icon, parent, title, text, buttons, default_button):
        install_qt_translations()
        box = cls(icon, title, text, buttons, parent)
        box.setTextFormat(Qt.TextFormat.PlainText)
        if default_button != cls.StandardButton.NoButton:
            box.setDefaultButton(default_button)
        localize_buttons(box)
        return cls.StandardButton(box.exec())

    @classmethod
    def information(cls, parent, title, text, buttons=QMessageBox.StandardButton.Ok,
                    defaultButton=QMessageBox.StandardButton.NoButton):
        return cls._show(cls.Icon.Information, parent, title, text, buttons, defaultButton)

    @classmethod
    def warning(cls, parent, title, text, buttons=QMessageBox.StandardButton.Ok,
                defaultButton=QMessageBox.StandardButton.NoButton):
        return cls._show(cls.Icon.Warning, parent, title, text, buttons, defaultButton)

    @classmethod
    def critical(cls, parent, title, text, buttons=QMessageBox.StandardButton.Ok,
                 defaultButton=QMessageBox.StandardButton.NoButton):
        return cls._show(cls.Icon.Critical, parent, title, text, buttons, defaultButton)

    @classmethod
    def question(cls, parent, title, text,
                 buttons=QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                 defaultButton=QMessageBox.StandardButton.NoButton):
        return cls._show(cls.Icon.Question, parent, title, text, buttons, defaultButton)
