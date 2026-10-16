"""Neutral rounded decoration for the real Qt tooltip, without a new timer/UI.

Qt still owns tooltip text (including rich text), accessibility, hover delays,
timeouts and dismissal. We decorate its QLabel before native window creation.
High-contrast mode keeps the platform tooltip palette and native painting.
"""
from __future__ import annotations

import ctypes
import sys

from PySide6.QtCore import QEvent, QObject, QPoint, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QApplication, QLabel
from shiboken6 import isValid

from .ui_fonts import ui_font_family_css


def high_contrast_enabled():
    if sys.platform != 'win32':
        app = QApplication.instance()
        return bool(app and 'highcontrast' in app.style().objectName().lower())
    class HighContrast(ctypes.Structure):
        _fields_ = [('size', ctypes.c_uint32), ('flags', ctypes.c_uint32),
                    ('default_scheme', ctypes.c_wchar_p)]
    try:
        query = ctypes.WinDLL('user32', use_last_error=True).SystemParametersInfoW
        query.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32]
        query.restype = ctypes.c_int32
        value = HighContrast()
        value.size = ctypes.sizeof(value)
        # SPI_GETHIGHCONTRAST / HCF_HIGHCONTRASTON. Read only, no setting change.
        if not query(0x0042, value.size, ctypes.byref(value), 0):
            return True  # Unknown accessibility setting: retain platform colors.
        return bool(value.flags & 1)
    except (AttributeError, OSError):
        return True


def tooltip_stylesheet(high_contrast=False):
    if high_contrast:
        return ('QToolTip { background: palette(tool-tip-base); color: palette(tool-tip-text); '
                'border: 1px solid palette(tool-tip-text); padding: 3px; }')
    families = ui_font_family_css()
    return ('QToolTip { background: transparent; color: #eeeeee; border: 0; '
            'padding: 3px 6px; font-size: 12px; font-family: ' + families + '; }')


_START = '/* LUMA_TOOLTIP_THEME */'
_END = '/* END_LUMA_TOOLTIP_THEME */'


class TooltipTheme(QObject):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.high_contrast = False
        self._active = None
        self._adjusting = False
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.timeout.connect(self.refresh)
        self._bounds_timer = QTimer(self)
        self._bounds_timer.setSingleShot(True)
        self._bounds_timer.timeout.connect(self._keep_in_work_area)
        app.installEventFilter(self)
        self.refresh()

    def refresh(self):
        self.high_contrast = high_contrast_enabled()
        stylesheet = self.app.styleSheet()
        if _START in stylesheet:
            start = stylesheet.index(_START)
            end = stylesheet.index(_END, start) + len(_END)
            stylesheet = stylesheet[:start] + stylesheet[end:]
        target = stylesheet.rstrip() + '\n' + _START + '\n' + tooltip_stylesheet(self.high_contrast) + '\n' + _END
        if target != self.app.styleSheet():
            self.app.setStyleSheet(target)

    def _keep_in_work_area(self):
        widget = self._active
        if widget is None or not isValid(widget) or not widget.isVisible():
            return
        area = widget.screen().availableGeometry()
        # Native QLabel can size an unbroken filename wider than the monitor.
        # Keep the actual tooltip surface reachable without rewriting its text
        # (the full value remains exposed through native accessibility).
        if widget.width() > area.width() or widget.height() > area.height():
            self._adjusting = True
            try:
                widget.resize(min(widget.width(), area.width()), min(widget.height(), area.height()))
            finally:
                self._adjusting = False
        x = max(area.left(), min(widget.x(), area.right() + 1 - widget.width()))
        y = max(area.top(), min(widget.y(), area.bottom() + 1 - widget.height()))
        if widget.pos() != QPoint(x, y):
            self._adjusting = True
            try:
                widget.move(x, y)
            finally:
                self._adjusting = False

    def eventFilter(self, watched, event):
        kind = event.type()
        if watched is getattr(self, 'app', None) and kind == QEvent.Type.ApplicationPaletteChange:
            self._refresh_timer.start(0)
        if kind not in (QEvent.Type.Polish, QEvent.Type.Show, QEvent.Type.Paint,
                        QEvent.Type.Move, QEvent.Type.Resize):
            return False
        if not isinstance(watched, QLabel) or watched.windowType() != Qt.WindowType.ToolTip:
            return False
        if kind == QEvent.Type.Polish and not self.high_contrast:
            # QToolTip already uses FramelessWindowHint. Set the alpha attribute
            # at Polish, before its HWND is created, which Windows requires.
            watched.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
            watched.setAutoFillBackground(False)
        elif kind == QEvent.Type.Show and not self.high_contrast:
            # QTipLabel adds a native margin after Polish, on top of QSS
            # padding. Remove that double-padding once its native text/layout
            # exists; Qt continues to own wrapping and tooltip lifetime.
            watched.setMargin(0)
            watched.adjustSize()
        elif kind == QEvent.Type.Paint and not self.high_contrast:
            painter = QPainter(watched)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(QPen(QColor('#484848'), 1))
            painter.setBrush(QColor('#303030'))
            painter.drawRoundedRect(QRectF(watched.rect()).adjusted(.5, .5, -.5, -.5), 5, 5)
            painter.end()
            # Let QLabel draw the untouched native text on the transparent QSS
            # surface. Returning True would discard rich text/accessibility.
        if kind in (QEvent.Type.Show, QEvent.Type.Move, QEvent.Type.Resize) and not self._adjusting:
            self._active = watched
            self._bounds_timer.start(0)
        return False


def install_tooltip_theme(app=None):
    app = app or QApplication.instance()
    if app is None:
        return None
    manager = getattr(app, '_luma_tooltip_theme', None)
    if manager is None:
        manager = TooltipTheme(app)
        app._luma_tooltip_theme = manager
    else:
        manager.refresh()
    return manager
