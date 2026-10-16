from __future__ import annotations

import math

from PySide6.QtCore import Qt, Signal, QRectF, QEvent
from PySide6.QtGui import QColor, QBrush, QPainter, QPixmap, QImage, QTransform
from PySide6.QtWidgets import QGraphicsView, QGraphicsScene, QGraphicsPixmapItem


class ImageCanvas(QGraphicsView):
    # Percentages are relative to the current fit-to-window scale, not pixels.
    zoomChanged = Signal(float)
    MIN_ZOOM = 0.005
    MAX_ZOOM = 64.0

    def __init__(self, parent=None):
        super().__init__(parent)
        # File drops belong to the window; keep the scene from claiming them.
        self.setAcceptDrops(False)
        self.setScene(QGraphicsScene(self))
        self.item = QGraphicsPixmapItem()
        self.scene().addItem(self.item)
        self.setFrameShape(QGraphicsView.Shape.NoFrame)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.fitted = True
        self._fit_scale = 1.0
        self._relative_zoom = 1.0
        self._actual_pixels = False
        self._last_reported_zoom = None
        self.background = 'Graphite'
        tile = QPixmap(24, 24)
        tile.fill(QColor('#2a2a2a'))
        painter = QPainter(tile)
        painter.fillRect(0, 0, 12, 12, QColor('#373737'))
        painter.fillRect(12, 12, 12, 12, QColor('#373737'))
        painter.end()
        self.checker = QBrush(tile)

    @property
    def zoom_percent(self):
        return self._relative_zoom * 100.0

    def _fitted_scale(self):
        # Match fitInView's two-pixel viewport inset and existing image margin,
        # but calculate the final transform without first resetting the view.
        bounds = self.item.sceneBoundingRect().adjusted(-12, -12, 12, 12)
        return min(max(1, self.viewport().width() - 4) / bounds.width(),
                   max(1, self.viewport().height() - 4) / bounds.height())

    def _pixel_scale(self):
        # A tagged HiDPI pixmap has fewer scene units than source pixels.
        return self.item.pixmap().devicePixelRatioF() / self.viewport().devicePixelRatioF()

    def _apply_scale(self, scale, center=False):
        self.setTransform(QTransform.fromScale(scale, scale))
        if center:
            self.centerOn(self.item)

    def set_image(self, image: QImage, reset=True):
        self.item.setPixmap(QPixmap.fromImage(image))
        if reset:
            self.item.setRotation(0)
        self.scene().setSceneRect(self.item.sceneBoundingRect())
        if not image.isNull():
            if reset or self.fitted:
                self.fit()
            else:
                self._refresh_scale()
        self.viewport().update()

    def clear_image(self):
        self.item.setPixmap(QPixmap())
        self.item.setRotation(0)
        self.scene().setSceneRect(QRectF())
        # Do not flash an intermediate zoom value while the next image loads.
        self.viewport().update()

    def fit(self):
        if self.item.pixmap().isNull():
            return
        self.fitted = True
        self._actual_pixels = False
        self._relative_zoom = 1.0
        self._fit_scale = self._fitted_scale()
        self._apply_scale(self._fit_scale, center=True)
        self._changed()

    def rotate_clockwise(self):
        if self.item.pixmap().isNull():
            return
        self.item.setRotation((self.item.rotation() + 90) % 360)
        self.scene().setSceneRect(self.item.sceneBoundingRect())
        self.fit()

    def displayed_image(self):
        image = self.item.pixmap().toImage()
        if self.item.rotation():
            image = image.transformed(QTransform().rotate(self.item.rotation()))
        return image.copy()

    def actual_size(self):
        if self.item.pixmap().isNull():
            return
        self.fitted = False
        self._actual_pixels = True
        self._fit_scale = self._fitted_scale()
        scale = self._pixel_scale()
        self._relative_zoom = scale / self._fit_scale
        self._apply_scale(scale, center=True)
        self._changed()

    def set_zoom_percent(self, percent):
        if self.item.pixmap().isNull() or not math.isfinite(percent) or percent <= 0:
            return
        target = max(self.MIN_ZOOM, min(self.MAX_ZOOM, percent / 100.0))
        if math.isclose(target, 1.0, rel_tol=0.0, abs_tol=1e-12):
            self.fit()
            return
        self.fitted = False
        self._actual_pixels = False
        self._relative_zoom = target
        self._fit_scale = self._fitted_scale()
        self._apply_scale(self._fit_scale * target)
        self._changed()

    def zoom(self, factor):
        if not math.isfinite(factor) or factor <= 0:
            return
        self.set_zoom_percent(self.zoom_percent * factor)

    def _refresh_scale(self):
        if self.item.pixmap().isNull():
            return
        self._fit_scale = self._fitted_scale()
        if self._actual_pixels:
            scale = self._pixel_scale()
            self._relative_zoom = scale / self._fit_scale
        else:
            scale = self._fit_scale * self._relative_zoom
        self._apply_scale(scale, center=self.fitted)
        self._changed()

    def _changed(self):
        percent = self.zoom_percent
        if (self._last_reported_zoom is None or
                not math.isclose(percent, self._last_reported_zoom, rel_tol=1e-12, abs_tol=1e-9)):
            self._last_reported_zoom = percent
            self.zoomChanged.emit(percent)

    def wheelEvent(self, event):
        delta = event.angleDelta().y() or event.pixelDelta().y()
        if delta:
            self.zoom(1.2 ** (delta / 120))
        event.accept()

    def mouseDoubleClickEvent(self, event):
        self.actual_size() if self.fitted else self.fit()
        event.accept()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'item'):
            self._refresh_scale()

    def event(self, event):
        result = super().event(event)
        if event.type() == QEvent.Type.DevicePixelRatioChange and hasattr(self, 'item'):
            self._refresh_scale()
        return result

    def drawBackground(self, painter, rect):
        painter.save()
        painter.resetTransform()
        colors = {'Graphite': '#191919', 'Black': '#000000', 'White': '#ffffff'}
        brush = self.checker if self.background == 'Checkerboard' else QBrush(QColor(colors[self.background]))
        painter.fillRect(self.viewport().rect(), brush)
        painter.restore()
