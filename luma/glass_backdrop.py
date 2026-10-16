"""Bounded, demand-driven scene glass behind the Details overlay's crisp controls.

Only QGraphicsView.render() samples the existing viewport region. We never
capture the containing widget (which would include this overlay recursively),
copy the full image, reopen its source, or ask a decoder for another frame.
"""
from __future__ import annotations

import ctypes
import math
import sys
import time

from PIL import Image, ImageFilter
from PySide6.QtCore import QEvent, QPoint, QRect, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter
from PySide6.QtWidgets import QApplication, QWidget

MAX_SAMPLE_EDGE = 256
MAX_SAMPLE_PIXELS = 32768
REFRESH_INTERVAL_MS = 50


class _HighContrast(ctypes.Structure):
    _fields_ = [('cbSize', ctypes.c_uint32), ('dwFlags', ctypes.c_uint32),
                ('lpszDefaultScheme', ctypes.c_wchar_p)]


def _windows_transparency_allowed():
    """Read-only Windows accessibility/transparency settings, fail solid.

    https://learn.microsoft.com/windows/win32/winauto/high-contrast-parameter
    https://learn.microsoft.com/windows/apps/develop/settings/settings-common
    """
    try:
        query = ctypes.WinDLL('user32', use_last_error=True).SystemParametersInfoW
        query.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32]
        query.restype = ctypes.c_int32
        contrast = _HighContrast()
        contrast.cbSize = ctypes.sizeof(contrast)
        if not query(0x0042, contrast.cbSize, ctypes.byref(contrast), 0) or contrast.dwFlags & 1:
            return False  # SPI_GETHIGHCONTRAST / HCF_HIGHCONTRASTON
        disable_overlap = ctypes.c_int32()
        if not query(0x1040, 0, ctypes.byref(disable_overlap), 0) or disable_overlap.value:
            return False  # SPI_GETDISABLEOVERLAPPEDCONTENT
        import winreg
        path = r'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize'
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_READ) as key:
            value, kind = winreg.QueryValueEx(key, 'EnableTransparency')
        return kind == winreg.REG_DWORD and bool(value)
    except (AttributeError, ImportError, OSError, ValueError):
        return False


def transparency_allowed():
    return _windows_transparency_allowed() if sys.platform == 'win32' else True


def bounded_sample_size(size):
    """Bounds allocation independently of source resolution and device DPI."""
    width, height = max(1, size.width()), max(1, size.height())
    scale = min(1 / 3, MAX_SAMPLE_EDGE / max(width, height),
                math.sqrt(MAX_SAMPLE_PIXELS / (width * height)))
    return QSize(max(1, int(width * scale)), max(1, int(height * scale)))


def render_scene_sample(canvas, source_rect):
    """Render only the visible scene into a small owned RGBA image.

    Source uses viewport coordinates; QGraphicsView.render keeps the current
    pan, zoom, rotation and drawBackground, and excludes overlay child widgets.
    https://doc.qt.io/qt-6/qgraphicsview.html#render
    """
    size = bounded_sample_size(source_rect.size())
    image = QImage(size, QImage.Format.Format_RGBA8888)
    image.fill(QColor('#242424'))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    canvas.render(painter, QRectF(0, 0, size.width(), size.height()), source_rect,
                  Qt.AspectRatioMode.IgnoreAspectRatio)
    painter.end()
    return image


def blur_and_tint(sample):
    """Blur only the bounded sample, retaining genuine scene variation."""
    rgba = sample.convertToFormat(QImage.Format.Format_RGBA8888)
    bounded = Image.frombytes('RGBA', (rgba.width(), rgba.height()), bytes(rgba.constBits()),
                              'raw', 'RGBA', rgba.bytesPerLine())
    blurred = bounded.filter(ImageFilter.GaussianBlur(radius=5))
    # Neutral dark-gray glass: subdued scene colors, blurred edges, legible UI.
    blurred = Image.alpha_composite(blurred, Image.new('RGBA', blurred.size, (30, 31, 33, 188)))
    pixels = blurred.tobytes()
    return QImage(pixels, rgba.width(), rgba.height(), rgba.width() * 4,
                  QImage.Format.Format_RGBA8888).copy()


class GlassBackdrop(QWidget):
    """Background-only child; place it below controls and size to the overlay.

    Public API: body_rect() excludes the left shadow gutter; invalidate() queues
    a bounded refresh; refresh_effects_policy() rechecks read-only OS settings.
    No work runs while hidden. Changes are coalesced to at most 20 captures/s.
    """
    def __init__(self, canvas, parent=None, shadow_width=12):
        super().__init__(parent)
        self.canvas = canvas
        self.shadow_width = max(0, int(shadow_width))
        self.setObjectName('glass_backdrop')
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setAutoFillBackground(False)
        self.setStyleSheet('QWidget#glass_backdrop { background: transparent; border: 0; }')
        self._image = QImage()
        self._last_signature = None
        self._effects_allowed = transparency_allowed()
        self._rendering = False
        self._suspended = False
        self.capture_count = 0
        self.last_capture_ms = 0.
        self.last_capture_size = QSize()
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._refresh_timer.setInterval(REFRESH_INTERVAL_MS)
        self._refresh_timer.timeout.connect(self._refresh)
        self._policy_timer = QTimer(self)
        self._policy_timer.setInterval(1000)
        self._policy_timer.timeout.connect(self.refresh_effects_policy)
        canvas.installEventFilter(self)
        canvas.viewport().installEventFilter(self)
        canvas.horizontalScrollBar().valueChanged.connect(self.invalidate)
        canvas.verticalScrollBar().valueChanged.connect(self.invalidate)
        # Paint-state signatures prevent a repaint caused by this widget from
        # causing another capture, even when this child lives in the viewport.
        if canvas.scene() is not None:
            canvas.scene().changed.connect(self.invalidate)

    @property
    def solid_fallback(self):
        return not self._effects_allowed

    def body_rect(self):
        return self.rect().adjusted(self.shadow_width, 0, 0, 0)

    def _source_rect(self):
        origin = self.canvas.viewport().mapFromGlobal(self.mapToGlobal(self.body_rect().topLeft()))
        return QRect(origin, self.body_rect().size())

    def _signature(self):
        item = getattr(self.canvas, 'item', None)
        item_state = None if item is None else (
            item.pixmap().cacheKey(), item.rotation(), item.pos().x(), item.pos().y(),
            item.opacity(), item.isVisible())
        transform = self.canvas.viewportTransform()
        matrix = tuple(getattr(transform, name)() for name in
                       ('m11', 'm12', 'm13', 'm21', 'm22', 'm23', 'm31', 'm32', 'm33'))
        source = self._source_rect()
        return (item_state, matrix, source.x(), source.y(), source.width(), source.height(),
                self.canvas.viewport().width(), self.canvas.viewport().height(),
                getattr(self.canvas, 'background', None), self.canvas.palette().cacheKey())

    def invalidate(self, *unused):
        if self._suspended or self._rendering or not self.isVisible() or not self._effects_allowed:
            return
        signature = self._signature()
        if signature == self._last_signature:
            return
        # Never stretch stale pixels from a previous source/zoom/position while
        # the coalesced refresh is pending. A solid panel is the safe interim.
        if not self._image.isNull():
            self._image = QImage()
            self.update()
        if not self._refresh_timer.isActive():
            self._refresh_timer.start()

    def set_suspended(self, suspended):
        """Keep expensive decorative captures out of interactive image changes."""
        self._suspended = bool(suspended)
        self._refresh_timer.stop()
        if suspended:
            self._image = QImage()
            self._last_signature = None
            self.update()
        else:
            self.invalidate()

    def _refresh(self):
        if self._suspended or not self.isVisible() or not self._effects_allowed or self._rendering:
            return
        source = self._source_rect()
        if source.isEmpty():
            return
        signature = self._signature()
        if signature == self._last_signature and not self._image.isNull():
            return
        self._rendering = True
        started = time.perf_counter()
        try:
            sample = render_scene_sample(self.canvas, source)
            image = blur_and_tint(sample)
            # Rendering stays synchronous/bounded, but still discard a changed
            # scene token rather than showing another image's backdrop.
            if self.isVisible() and self._effects_allowed and signature == self._signature():
                self._image = image
                self._last_signature = signature
                self.last_capture_size = sample.size()
                self.capture_count += 1
                self.update()
            else:
                self._last_signature = None
        finally:
            self.last_capture_ms = (time.perf_counter() - started) * 1000
            self._rendering = False
        if self.isVisible() and self._last_signature != self._signature():
            self.invalidate()

    def refresh_effects_policy(self):
        if not self.isVisible():
            return
        allowed = transparency_allowed()
        if allowed != self._effects_allowed:
            self._effects_allowed = allowed
            self._refresh_timer.stop()
            self._last_signature = None
            self._image = QImage()
            self.update()
            self.invalidate()

    def eventFilter(self, watched, event):
        if self.isVisible() and event.type() in (QEvent.Type.Paint, QEvent.Type.Resize,
                                                QEvent.Type.Move, QEvent.Type.Show):
            self.invalidate()
        return False

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh_effects_policy()
        self._policy_timer.start()
        self.invalidate()

    def hideEvent(self, event):
        self._refresh_timer.stop()
        self._policy_timer.stop()
        super().hideEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.invalidate()

    def moveEvent(self, event):
        super().moveEvent(event)
        self.invalidate()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        body = self.body_rect()
        if self.shadow_width:
            gradient = QLinearGradient(0, 0, self.shadow_width, 0)
            gradient.setColorAt(0, QColor(0, 0, 0, 0))
            gradient.setColorAt(.5, QColor(0, 0, 0, 14))
            gradient.setColorAt(1, QColor(0, 0, 0, 66))
            painter.fillRect(QRect(0, 0, self.shadow_width, self.height()), gradient)
        painter.fillRect(body, QColor('#242424'))
        if self._effects_allowed and not self._image.isNull():
            painter.drawImage(QRectF(body), self._image)
        painter.setPen(QColor(255, 255, 255, 15))
        painter.drawLine(body.topLeft(), body.bottomLeft())
        painter.end()
