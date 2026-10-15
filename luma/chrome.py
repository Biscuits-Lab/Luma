"""Compact native-feeling window chrome using Qt's OS move/resize requests."""
from __future__ import annotations
from PySide6.QtCore import QObject, QEvent, Qt
from PySide6.QtWidgets import QApplication, QFrame, QWidget


def resize_edges(point, width, height, margin=6):
    """Coordinates and margins are device-independent, including on HiDPI."""
    edges = Qt.Edge(0)
    if point.x() < margin:
        edges |= Qt.Edge.LeftEdge
    elif point.x() >= width - margin:
        edges |= Qt.Edge.RightEdge
    if point.y() < margin:
        edges |= Qt.Edge.TopEdge
    elif point.y() >= height - margin:
        edges |= Qt.Edge.BottomEdge
    return edges


class TitleBar(QFrame):
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and not self.window().isFullScreen():
            handle = self.window().windowHandle()
            if handle and handle.startSystemMove():
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and not self.window().isFullScreen():
            window = self.window()
            window.showNormal() if window.isMaximized() else window.showMaximized()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class WindowChrome(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.cursor_widget = None
        for widget in [window, *window.findChildren(QWidget)]:
            widget.setMouseTracking(True)
        QApplication.instance().installEventFilter(self)

    def close(self):
        QApplication.instance().removeEventFilter(self)

    def eventFilter(self, watched, event):
        if (not isinstance(watched, QWidget) or watched.window() is not self.window
                or self.window.isMaximized() or self.window.isFullScreen()
                or event.type() not in (QEvent.Type.MouseMove, QEvent.Type.MouseButtonPress)):
            return False
        point = self.window.mapFromGlobal(event.globalPosition().toPoint())
        edges = resize_edges(point, self.window.width(), self.window.height())
        if event.type() == QEvent.Type.MouseButtonPress:
            if edges and event.button() == Qt.MouseButton.LeftButton:
                handle = self.window.windowHandle()
                if handle and handle.startSystemResize(edges):
                    return True
            return False
        if self.cursor_widget is not None:
            try:
                self.cursor_widget.unsetCursor()
            except RuntimeError:
                pass
            self.cursor_widget = None
        if edges:
            diagonal_a = Qt.Edge.LeftEdge | Qt.Edge.TopEdge
            diagonal_b = Qt.Edge.RightEdge | Qt.Edge.BottomEdge
            if edges in (diagonal_a, diagonal_b):
                cursor = Qt.CursorShape.SizeFDiagCursor
            elif edges in (Qt.Edge.RightEdge | Qt.Edge.TopEdge, Qt.Edge.LeftEdge | Qt.Edge.BottomEdge):
                cursor = Qt.CursorShape.SizeBDiagCursor
            elif edges & (Qt.Edge.LeftEdge | Qt.Edge.RightEdge):
                cursor = Qt.CursorShape.SizeHorCursor
            else:
                cursor = Qt.CursorShape.SizeVerCursor
            watched.setCursor(cursor)
            self.cursor_widget = watched
        return False
