"""Compact native-feeling window chrome using Qt's OS move/resize requests."""
from __future__ import annotations
import ctypes
from functools import lru_cache
import sys

from PySide6.QtCore import QObject, QEvent, Qt, QTimer
from PySide6.QtWidgets import QApplication, QFrame, QWidget


class _Margins(ctypes.Structure):
    # Win32 LONG is always 32-bit, including on 64-bit Windows.
    _fields_ = [(name, ctypes.c_int32) for name in ('left', 'right', 'top', 'bottom')]


@lru_cache(maxsize=1)
def _dwm_functions():
    dll = ctypes.WinDLL('dwmapi', use_last_error=True)
    attribute = dll.DwmSetWindowAttribute
    attribute.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32]
    attribute.restype = ctypes.c_int32  # HRESULT; do not truncate HWND to int.
    extend = dll.DwmExtendFrameIntoClientArea
    extend.argtypes = [ctypes.c_void_p, ctypes.POINTER(_Margins)]
    extend.restype = ctypes.c_int32
    return attribute, extend


def apply_native_corners(hwnd, rounded=True):
    """Request native Win11 corners while leaving unsupported systems untouched.

    The opaque rectangular Qt surface keeps the existing custom titlebar and
    system move/resize path. A one-physical-pixel DWM frame supplies a native
    border; no bitmap mask, translucent layer, or replacement caption is used.
    Maximized/fullscreen windows remove it; DWM handles snapped-window policy.
    https://learn.microsoft.com/windows/apps/desktop/modernize/ui/apply-rounded-corners
    https://learn.microsoft.com/windows/win32/dwm/customframe
    """
    if sys.platform != 'win32' or not hwnd:
        return False
    try:
        if sys.getwindowsversion().build < 22000:
            return False
        attribute, extend = _dwm_functions()
        preference = ctypes.c_int32(2 if rounded else 1)  # DWMWCP_ROUND / DONOTROUND
        result = attribute(hwnd, 33, ctypes.byref(preference), ctypes.sizeof(preference))
        if int(result) & 0x80000000:  # FAILED(HRESULT), including unsigned test doubles.
            return False
        margin = 1 if rounded else 0
        result = extend(hwnd, ctypes.byref(_Margins(margin, margin, margin, margin)))
        return not bool(int(result) & 0x80000000)
    except (AttributeError, OSError):
        # Older Windows, unavailable DWM, or a missing optional entry point.
        return False


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
        self._closed = False
        self.native_corners_applied = False
        self._native_timer = QTimer(self)
        self._native_timer.setSingleShot(True)
        self._native_timer.timeout.connect(self._update_native_corners)
        for widget in [window, *window.findChildren(QWidget)]:
            widget.setMouseTracking(True)
        QApplication.instance().installEventFilter(self)

    def close(self):
        self._closed = True
        self._native_timer.stop()
        QApplication.instance().removeEventFilter(self)

    def _update_native_corners(self):
        if self._closed or self.window.windowHandle() is None:
            return
        self.native_corners_applied = apply_native_corners(
            int(self.window.winId()),
            rounded=not (self.window.isMaximized() or self.window.isFullScreen()))

    def eventFilter(self, watched, event):
        if self._closed:
            return False
        if (watched is self.window and sys.platform == 'win32' and not self._closed
                and event.type() in (QEvent.Type.Show, QEvent.Type.WinIdChange,
                                     QEvent.Type.WindowStateChange, QEvent.Type.ActivationChange)):
            # Qt may recreate its HWND during state changes. Wait until the
            # transition completes, then opt in again using the current handle.
            self._native_timer.start(0)
        if (event.type() not in (QEvent.Type.MouseMove, QEvent.Type.MouseButtonPress)
                or not isinstance(watched, QWidget) or watched.window() is not self.window
                or self.window.isMaximized() or self.window.isFullScreen()):
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
