"""Acknowledge an update only after the real viewer has painted a first frame."""
from PySide6.QtCore import QObject, QEvent, QTimer

from .update_probe import acknowledge_startup, validate_startup_endpoint


class StartupAcknowledgment(QObject):
    def __init__(self, window, port, nonce):
        super().__init__(window)
        self.port, self.nonce = validate_startup_endpoint(port, nonce)
        self.window = window
        self.sent = False
        window.installEventFilter(self)

    def eventFilter(self, watched, event):
        if watched is self.window and event.type() == QEvent.Type.Paint and not self.sent:
            self.sent = True
            self.window.removeEventFilter(self)
            # The queued callback runs after the paint event has completed.
            QTimer.singleShot(0, self._acknowledge)
        return False

    def _acknowledge(self):
        if self.window.isVisible():
            self.thread = acknowledge_startup(self.port, self.nonce)
