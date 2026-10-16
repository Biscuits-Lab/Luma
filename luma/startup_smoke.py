"""Paint evidence for the bounded packaged-window smoke check."""
from PySide6.QtCore import QEvent, QObject


class StartupPaintProbe(QObject):
    """Observe real widget paints without changing their timing or visibility."""

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.initial_page_is_canvas = window.stack.currentWidget() is window.canvas
        self.welcome_paints = 0
        self.image_paints = 0
        window.welcome.installEventFilter(self)
        window.canvas.viewport().installEventFilter(self)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Paint:
            if watched is self.window.welcome:
                self.welcome_paints += 1
            elif (self.window.displayed_path is not None
                  and not self.window.canvas.item.pixmap().isNull()):
                self.image_paints += 1
        return False
