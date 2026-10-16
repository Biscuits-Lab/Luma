"""Demand-only RGB statistics from the current display-rendered Qt preview.

No decoder or filesystem access. A fast nearest-neighbour sample is bounded to
256 × 256 pixels *before* QPixmap becomes a QImage or changes pixel format. Qt
pixel work and NumPy counts are therefore bounded even for large EXR images.
Only tiny bin arrays are cached, never full-size images or worker RGBA buffers.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass

import numpy as np
from PySide6.QtCore import Qt, QTimer, QRectF, QPointF, QSize
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget, QLabel, QVBoxLayout

from .i18n import tr

SAMPLE_EDGE = 256
MAX_SAMPLES = SAMPLE_EDGE * SAMPLE_EDGE
CACHE_ENTRIES = 12


@dataclass(frozen=True)
class HistogramData:
    counts: np.ndarray
    sampled_pixels: int
    visible_pixels: int


def bounded_preview(source):
    """Accept QPixmap on the GUI thread or QImage; return only a small QImage.

    Do not use SmoothTransformation: averaging changes histogram distributions.
    Do not call toImage() before scaling: native pixmap conversion may otherwise
    allocate another full-frame image on some platform backends.
    """
    if source is None or source.isNull():
        return QImage()
    width, height = source.width(), source.height()
    scale = min(1.0, SAMPLE_EDGE / max(width, height))
    size = QSize(max(1, int(width * scale)), max(1, int(height * scale)))
    sample = source.scaled(size, Qt.AspectRatioMode.IgnoreAspectRatio,
                           Qt.TransformationMode.FastTransformation)
    image = sample if isinstance(sample, QImage) else sample.toImage()
    return image.convertToFormat(QImage.Format.Format_RGBA8888)


def preview_histogram(image: QImage) -> HistogramData:
    """Count straight displayed RGB, excluding alpha == 0 (not alpha-weighted).

    Partially transparent pixels retain their displayed, unpremultiplied RGB.
    The canvas background/checkerboard is intentionally not part of the image.
    This helper bounds arbitrary inputs too, making accidental full-image work
    impossible when called independently from the panel.
    """
    sample = bounded_preview(image)
    if sample.isNull():
        return HistogramData(np.zeros((3, 256), dtype=np.uint32), 0, 0)
    # constBits does not detach Qt's pixel buffer. Account for scanline padding.
    rows = np.frombuffer(sample.constBits(), dtype=np.uint8,
                         count=sample.sizeInBytes()).reshape(sample.height(), sample.bytesPerLine())
    rgba = rows[:, :sample.width() * 4].reshape(-1, 4)
    visible = rgba[rgba[:, 3] != 0, :3]
    counts = np.zeros((3, 256), dtype=np.uint32)
    for channel in range(3):
        if len(visible):
            counts[channel] = np.bincount(visible[:, channel], minlength=256)
    counts.flags.writeable = False
    return HistogramData(counts, len(rgba), len(visible))


class HistogramPlot(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.data = None
        self.message = tr('No image selected')
        self.setFixedHeight(110)
        self.setAccessibleName(tr('RGB HISTOGRAM'))

    def set_state(self, message, data=None):
        self.message, self.data = message, data
        self.setAccessibleDescription(message)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(.5, .5, -.5, -17)
        painter.setPen(QPen(QColor(255, 255, 255, 20), 1))
        painter.setBrush(QColor(0, 0, 0, 35))
        painter.drawRoundedRect(rect, 4, 4)
        inner = rect.adjusted(4, 5, -4, -3)
        painter.setPen(QPen(QColor(255, 255, 255, 14), 1))
        for fraction in (.25, .5, .75):
            x = inner.left() + inner.width() * fraction
            painter.drawLine(QPointF(x, inner.top()), QPointF(x, inner.bottom()))
        if self.data is None or self.data.visible_pixels == 0:
            painter.setPen(QColor('#a0a0a0'))
            painter.drawText(inner, Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self.message)
        else:
            maximum = int(self.data.counts.max())
            # Shared linear frequency scale keeps channel heights comparable.
            for bins, color in zip(self.data.counts, ('#ee7776', '#80cc95', '#80aef2')):
                path = QPainterPath(QPointF(inner.left(), inner.bottom()))
                for index, value in enumerate(bins):
                    path.lineTo(inner.left() + index / 255 * inner.width(),
                                inner.bottom() - int(value) / maximum * inner.height())
                path.lineTo(inner.right(), inner.bottom())
                path.closeSubpath()
                fill = QColor(color)
                fill.setAlpha(47)
                painter.fillPath(path, fill)
                painter.setPen(QPen(QColor(color), 1.0))
                painter.drawPath(path)
        painter.setPen(QColor('#989898'))
        font = painter.font()
        font.setPointSizeF(max(7, font.pointSizeF() - 1))
        painter.setFont(font)
        painter.drawText(QRectF(0, rect.bottom() + 3, 30, 13), Qt.AlignmentFlag.AlignLeft, '0')
        painter.drawText(QRectF(self.width() - 30, rect.bottom() + 3, 30, 13), Qt.AlignmentFlag.AlignRight, '255')
        for index, (label, color) in enumerate(zip('RGB', ('#ee7776', '#80cc95', '#80aef2'))):
            painter.setPen(QColor(color))
            painter.drawText(QRectF(self.width() / 2 - 27 + index * 20, rect.bottom() + 3, 15, 13),
                             Qt.AlignmentFlag.AlignCenter, label)


class PreviewHistogram(QWidget):
    """Lazy latest-state controller; hidden navigation never touches pixels.

    Work is one queued bounded sample (~256 KiB), not an unbounded background
    full-image task. Qt pixmaps stay on their owning GUI thread. Coalescing and
    generation checks prevent queued old states from being presented or cached.
    """
    def __init__(self, source_provider, parent=None):
        super().__init__(parent)
        self.source_provider = source_provider
        self._active = False
        self._key = None
        self._generation = 0
        self._cache = OrderedDict()
        self.sample_runs = 0
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._calculate)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        layout.addWidget(QLabel(tr('RGB HISTOGRAM'), objectName='section'))
        self.plot = HistogramPlot()
        layout.addWidget(self.plot)
        self.caption = QLabel(tr('Sampled display preview · 0–255') + '\n' +
                             tr('After display adjustments; not linear HDR.'), objectName='muted')
        self.caption.setWordWrap(True)
        self.caption.setStyleSheet('font-size: 10px;')
        self.caption.setToolTip(tr('After display adjustments; not linear HDR. Transparent pixels excluded.'))
        layout.addWidget(self.caption)
        self.setToolTip(self.caption.toolTip())

    def set_active(self, active):
        self._active = bool(active)
        self._generation += 1
        self._timer.stop()
        if active and self._key is not None:
            self._schedule()

    def set_preview(self, key):
        self._generation += 1
        self._timer.stop()
        self._key = key
        # Never show the previous image's bins while navigation is unresolved.
        self.plot.set_state(tr('Calculating histogram…'))
        if self._active:
            self._schedule()

    def clear(self, state='empty'):
        self._generation += 1
        self._timer.stop()
        self._key = None
        message = {'empty': tr('No image selected'), 'loading': tr('Waiting for preview…'),
                   'error': tr('Histogram unavailable')}[state]
        self.plot.set_state(message)
        self.caption.setToolTip(tr('After display adjustments; not linear HDR. Transparent pixels excluded.'))

    def _schedule(self):
        if self._key in self._cache:
            data = self._cache[self._key]
            self._cache.move_to_end(self._key)
            self._show(data)
        else:
            self.plot.set_state(tr('Calculating histogram…'))
            self._timer.start(0)

    def _calculate(self):
        if not self._active or self._key is None:
            return
        generation, key = self._generation, self._key
        try:
            source = self.source_provider()
            sample = bounded_preview(source)
            self.sample_runs += 1
            data = preview_histogram(sample)
            if sample.isNull():
                raise ValueError('No rendered preview')
        except Exception:
            if self._active and generation == self._generation:
                self.plot.set_state(tr('Histogram unavailable'))
            return
        if not self._active or generation != self._generation or key != self._key:
            return
        self._cache[key] = data
        self._cache.move_to_end(key)
        while len(self._cache) > CACHE_ENTRIES:
            self._cache.popitem(last=False)
        self._show(data)

    def _show(self, data):
        message = tr('{count:,} sampled pixels', count=data.visible_pixels) if data.visible_pixels else tr('No visible pixels in sample')
        self.plot.set_state(message, data)
        self.caption.setToolTip(tr('After display adjustments; not linear HDR. Transparent pixels excluded.') + '\n' + message)
