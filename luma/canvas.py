from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QRectF
from PySide6.QtGui import QColor, QBrush, QPainter, QPixmap, QImage, QTransform
from PySide6.QtWidgets import QGraphicsView, QGraphicsScene, QGraphicsPixmapItem


class ImageCanvas(QGraphicsView):
    zoomChanged = Signal(float)

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
        self.background = 'Graphite'
        tile = QPixmap(24, 24)
        tile.fill(QColor('#2a2a2a'))
        painter = QPainter(tile)
        painter.fillRect(0, 0, 12, 12, QColor('#373737'))
        painter.fillRect(12, 12, 12, 12, QColor('#373737'))
        painter.end()
        self.checker = QBrush(tile)

    def set_image(self, image: QImage, reset=True):
        self.item.setPixmap(QPixmap.fromImage(image))
        if reset:
            self.item.setRotation(0)
        self.scene().setSceneRect(self.item.sceneBoundingRect())
        if reset or self.fitted:
            self.fit()
        self.viewport().update()

    def clear_image(self):
        self.item.setPixmap(QPixmap())
        self.item.setRotation(0)
        self.scene().setSceneRect(QRectF())
        self.viewport().update()

    def fit(self):
        if self.item.pixmap().isNull():
            return
        self.fitted = True
        self.resetTransform()
        self.fitInView(self.item.sceneBoundingRect().adjusted(-12, -12, 12, 12), Qt.AspectRatioMode.KeepAspectRatio)
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
        self.resetTransform()
        self.scale(1.0 / self.viewport().devicePixelRatioF(), 1.0 / self.viewport().devicePixelRatioF())
        self.centerOn(self.item)
        self._changed()

    def zoom(self, factor):
        if self.item.pixmap().isNull():
            return
        self.fitted = False
        current = self.transform().m11() * self.viewport().devicePixelRatioF()
        target = max(0.005, min(64.0, current * factor))
        self.scale(target / current, target / current)
        self._changed()

    def _changed(self):
        self.zoomChanged.emit(self.transform().m11() * self.viewport().devicePixelRatioF() * 100)

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
        if self.fitted:
            self.fit()

    def drawBackground(self, painter, rect):
        painter.save()
        painter.resetTransform()
        colors = {'Graphite': '#191919', 'Black': '#000000', 'White': '#ffffff'}
        brush = self.checker if self.background == 'Checkerboard' else QBrush(QColor(colors[self.background]))
        painter.fillRect(self.viewport().rect(), brush)
        painter.restore()
