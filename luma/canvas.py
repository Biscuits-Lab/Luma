from __future__ import annotations

import math
import ctypes
import sys

from PySide6.QtCore import Qt, Signal, QRectF, QEvent, QPointF, QVariantAnimation, QEasingCurve
from PySide6.QtGui import QColor, QBrush, QPainter, QPixmap, QImage, QTransform
from PySide6.QtWidgets import QApplication, QGraphicsView, QGraphicsScene, QGraphicsPixmapItem
from shiboken6 import isValid

from .minification import MinificationCache


def motion_allowed():
    """Honor Qt effects and Windows' read-only client-area animation setting.

    https://learn.microsoft.com/windows/win32/api/winuser/nf-winuser-systemparametersinfow
    Unknown Windows accessibility state fails safely to immediate placement.
    """
    if not QApplication.isEffectEnabled(Qt.UIEffect.UI_General):
        return False
    if sys.platform != 'win32':
        return True
    try:
        query = ctypes.WinDLL('user32', use_last_error=True).SystemParametersInfoW
        query.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32]
        query.restype = ctypes.c_int32
        enabled = ctypes.c_int32()
        return bool(query(0x1042, 0, ctypes.byref(enabled), 0) and enabled.value)
    except (AttributeError, OSError, ValueError):
        return False


class FilteredPixmapItem(QGraphicsPixmapItem):
    """Keep exact source pixels while painting a bounded filtered display level.

    Changing the painted level never changes the item's pixmap, bounds or
    transform, so copy/save, rotation, panning and actual-pixel zoom still use
    the original full-quality frame.
    """

    def __init__(self, cache):
        super().__init__()
        self.cache = cache
        # QGraphicsPixmapItem.paint overrides the view's render hint. Setting
        # only QGraphicsView.SmoothPixmapTransform leaves the item unsmoothed.
        self.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        cache.changed.connect(self._update_if_alive)

    def _update_if_alive(self):
        # Graphics items are not QObjects: a bare native update callback does
        # not get QObject's receiver-destruction disconnect protection.
        if isValid(self):
            self.update()

    def setPixmap(self, pixmap):
        super().setPixmap(pixmap)
        self.cache.set_source(pixmap.toImage())

    def paint(self, painter, option, widget=None):
        pixmap = self.pixmap()
        if pixmap.isNull():
            return
        # deviceTransform includes both the view/item transforms and the actual
        # output device DPR. The pixmap's DPR maps its pixels to item units.
        transform = painter.deviceTransform()
        scale = max(math.hypot(transform.m11(), transform.m12()),
                    math.hypot(transform.m21(), transform.m22())) / pixmap.devicePixelRatioF()
        self.cache.request(scale)
        image = self.cache.choose(scale)
        if image is None:
            super().paint(painter, option, widget)
            return
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.drawImage(QRectF(self.offset(), pixmap.deviceIndependentSize()), image,
                          QRectF(0, 0, image.width(), image.height()))


class ImageCanvas(QGraphicsView):
    # Percentages are relative to the current fit-to-window scale, not pixels.
    zoomChanged = Signal(float)
    fitAnimationChanged = Signal(bool)
    MIN_ZOOM = 0.005
    MAX_ZOOM = 64.0

    def __init__(self, parent=None):
        super().__init__(parent)
        # File drops belong to the window; keep the scene from claiming them.
        self.setAcceptDrops(False)
        self.setScene(QGraphicsScene(self))
        self.minification = MinificationCache(self)
        self.item = FilteredPixmapItem(self.minification)
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
        self._fit_animating = False
        self._fit_animation = QVariantAnimation(self)
        self._fit_animation.setDuration(220)
        self._fit_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fit_animation.setStartValue(0.)
        self._fit_animation.setEndValue(1.)
        self._fit_animation.valueChanged.connect(self._animate_fit_step)
        self._fit_animation.finished.connect(self._finish_animated_fit)
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

    def set_image(self, image: QImage, reset=True, source_size=None):
        old_bounds = self.item.sceneBoundingRect()
        if reset:
            self.cancel_fit_animation()
        self.item.setPixmap(QPixmap.fromImage(image, Qt.ImageConversionFlag.NoFormatConversion))
        # A temporary display sample occupies the exact same source-pixel scene
        # rectangle. Refinement must not change fit, zoom, pan or rotation.
        self.item.setTransform(QTransform.fromScale(
            source_size[0] / image.width(), source_size[1] / image.height())
            if source_size and not image.isNull() else QTransform())
        if reset:
            self.item.setRotation(0)
        if self.item.sceneBoundingRect() != old_bounds:
            self.cancel_fit_animation()
        self.scene().setSceneRect(self.item.sceneBoundingRect())
        if not image.isNull():
            if reset or self.fitted:
                self.fit()
            elif not self._fit_animating:
                self._refresh_scale()
        self.viewport().update()

    def clear_image(self):
        self.cancel_fit_animation()
        self.item.setPixmap(QPixmap())
        self.item.setTransform(QTransform())
        self.item.setRotation(0)
        self.scene().setSceneRect(QRectF())
        # Do not flash an intermediate zoom value while the next image loads.
        self.viewport().update()

    def fit(self):
        self.cancel_fit_animation()
        if self.item.pixmap().isNull():
            return
        self.fitted = True
        self._actual_pixels = False
        self._relative_zoom = 1.0
        self._fit_scale = self._fitted_scale()
        self._apply_scale(self._fit_scale, center=True)
        self._changed()

    @property
    def fit_animation_running(self):
        return self._fit_animating

    def animate_fit(self):
        """Explicit Fit action only: move the existing scene, never its pixels."""
        if self.item.pixmap().isNull():
            return
        self.cancel_fit_animation()
        if not motion_allowed():
            self.fit()
            return
        scale = self._fitted_scale()
        center = self.item.sceneBoundingRect().center()
        start_center = self.mapToScene(self.viewport().rect().center())
        distance = math.hypot(center.x() - start_center.x(), center.y() - start_center.y())
        if math.isclose(self.transform().m11(), scale, rel_tol=1e-12) and distance * scale <= 1:
            self.fit()
            return
        self._fit_from_scale = self.transform().m11()
        self._fit_from_center, self._fit_to_center = start_center, center
        self._fit_scale = scale
        self.fitted = False
        self._actual_pixels = False
        self._fit_animating = True
        self.fitAnimationChanged.emit(True)
        self._fit_animation.start()

    def _animate_fit_step(self, progress):
        if not self._fit_animating:
            return
        scale = self._fit_from_scale + (self._fit_scale - self._fit_from_scale) * progress
        start, end = self._fit_from_center, self._fit_to_center
        self._apply_scale(scale)
        self.centerOn(QPointF(start.x() + (end.x() - start.x()) * progress,
                             start.y() + (end.y() - start.y()) * progress))
        self._relative_zoom = scale / self._fit_scale
        self._changed()

    def _finish_animated_fit(self):
        if not self._fit_animating:
            return
        self._fit_animating = False
        # Land exactly on the existing immediate-fit geometry and 100% state.
        self.fit()
        self.fitAnimationChanged.emit(False)

    def cancel_fit_animation(self):
        if not getattr(self, '_fit_animating', False):
            return
        self._fit_animation.stop()
        self._fit_animating = False
        self.fitAnimationChanged.emit(False)

    def rotate_clockwise(self):
        if self.item.pixmap().isNull():
            return
        self.item.setRotation((self.item.rotation() + 90) % 360)
        self.scene().setSceneRect(self.item.sceneBoundingRect())
        self.fit()

    def displayed_image(self, *, detach=True):
        image = self.item.pixmap().toImage()
        if self.item.rotation():
            image = image.transformed(QTransform().rotate(self.item.rotation()))
        return image.copy() if detach else image

    def actual_size(self):
        self.cancel_fit_animation()
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
        self.cancel_fit_animation()
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

    def mousePressEvent(self, event):
        self.cancel_fit_animation()
        super().mousePressEvent(event)

    def resizeEvent(self, event):
        self.cancel_fit_animation()
        super().resizeEvent(event)
        if hasattr(self, 'item'):
            self._refresh_scale()

    def event(self, event):
        result = super().event(event)
        if event.type() == QEvent.Type.DevicePixelRatioChange and hasattr(self, 'item'):
            self.cancel_fit_animation()
            self._refresh_scale()
        return result

    def hideEvent(self, event):
        self.cancel_fit_animation()
        super().hideEvent(event)

    def release_resources(self):
        self.cancel_fit_animation()
        self.minification.close()

    def closeEvent(self, event):
        self.release_resources()
        super().closeEvent(event)

    def drawBackground(self, painter, rect):
        painter.save()
        painter.resetTransform()
        colors = {'Graphite': '#191919', 'Black': '#000000', 'White': '#ffffff'}
        brush = self.checker if self.background == 'Checkerboard' else QBrush(QColor(colors[self.background]))
        painter.fillRect(self.viewport().rect(), brush)
        painter.restore()
