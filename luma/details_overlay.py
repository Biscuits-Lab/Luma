"""An inset information card that reserves layout space beside the image."""
from __future__ import annotations

import math

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QTextOption
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QScrollArea, QTextBrowser, QVBoxLayout, QWidget

from .i18n import tr


class _DetailsSurface(QWidget):
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(.5, .5, -.5, -.5)
        path = QPainterPath()
        path.addRoundedRect(rect, 8, 8)
        painter.setClipPath(path)
        painter.fillRect(self.rect(), QColor('#272727'))
        painter.fillRect(0, 0, self.width(), 44, QColor('#303030'))
        painter.setClipping(False)
        painter.setPen(QPen(QColor('#333333'), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)
        painter.end()


class DetailsOverlay(QWidget):
    """Compatibility name for the docked card, no longer a canvas overlay.

    The outer 10px gutter exposes the viewer background around real rounded
    corners. A fixed heading/close button sits above the scrolling metadata.
    The opaque reference surface no longer captures or blurs image pixels.
    """
    visibilityChanged = Signal(bool)
    closeRequested = Signal()
    PANEL_WIDTH = 324
    CARD_MARGIN = 10

    def __init__(self, canvas, parent):
        super().__init__(parent)
        self.canvas = canvas
        # Keep the optional interface used by display-work suspension; there
        # is intentionally no active backdrop worker for an opaque docked card.
        self.glass = None
        self.setObjectName('details_panel')
        self.setAccessibleName(tr('Image information'))
        self.setFixedWidth(self.PANEL_WIDTH)
        self.setStyleSheet('''
            QWidget { background: transparent; }
            QScrollArea { border: 0; background: transparent; }
            QLabel#details_card_title { font-size: 14px; font-weight: 600; }
            QPushButton#details_close { border: 0; padding: 0; border-radius: 5px; }
            QPushButton#details_close:hover { background: #424242; }
            QPushButton#details_close:focus { border: 1px solid #aaaaaa; }
            QScrollBar:vertical { background: transparent; width: 6px; }
            QScrollBar::handle:vertical { background: #595959; border-radius: 3px; }
        ''')
        outer = QVBoxLayout(self)
        outer.setContentsMargins(self.CARD_MARGIN, self.CARD_MARGIN, self.CARD_MARGIN, self.CARD_MARGIN)
        outer.setSpacing(0)
        self.card = _DetailsSurface(self)
        self.card.setObjectName('details_card')
        outer.addWidget(self.card)
        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(1, 0, 1, 1)
        card_layout.setSpacing(0)
        self.header = QWidget(self.card)
        self.header.setFixedHeight(44)
        header_layout = QHBoxLayout(self.header)
        header_layout.setContentsMargins(18, 0, 10, 0)
        title = QLabel(tr('Image information'), objectName='details_card_title')
        header_layout.addWidget(title)
        header_layout.addStretch()
        from .settings_widgets import SettingsCloseButton
        self.close_button = SettingsCloseButton(self.header)
        self.close_button.setObjectName('details_close')
        self.close_button.setFixedSize(28, 28)
        self.close_button.setIconSize(QSize(15, 15))
        self.close_button.setAccessibleName(tr('Close') + ' · ' + tr('Image information'))
        self.close_button.setToolTip(self.close_button.accessibleName())
        self.close_button.clicked.connect(self.closeRequested)
        header_layout.addWidget(self.close_button)
        card_layout.addWidget(self.header)
        self.scroll_area = QScrollArea(self.card)
        self.scroll_area.setObjectName('sidebar')
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll_area.viewport().setAutoFillBackground(False)
        card_layout.addWidget(self.scroll_area, 1)
        self.content = QWidget(objectName='detailsContent')
        self.scroll_area.setWidget(self.content)
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(18, 20, 18, 18)
        self.content_layout.setSpacing(11)
        self.hide()

    def verticalScrollBar(self):
        return self.scroll_area.verticalScrollBar()

    def horizontalScrollBar(self):
        return self.scroll_area.horizontalScrollBar()

    def invalidate_backdrop(self):
        # Existing display paths can invalidate this without scheduling work.
        self.card.update()

    def showEvent(self, event):
        super().showEvent(event)
        self.visibilityChanged.emit(True)

    def hideEvent(self, event):
        self.visibilityChanged.emit(False)
        super().hideEvent(event)


class MetadataTextBrowser(QTextBrowser):
    """Plain selectable metadata that normally scrolls with the whole panel."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setOpenExternalLinks(False)
        self.setOpenLinks(False)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.setMinimumHeight(50)
        self.document().documentLayout().documentSizeChanged.connect(self._fit_height)

    def _fit_height(self, *_):
        # Ordinary metadata is fully visible in the outer scroll area. Very long
        # technical payloads still have a bounded widget and an inner scrollbar.
        height = max(50, min(1200, math.ceil(self.document().size().height()) + 8))
        if height != self.height():
            self.setFixedHeight(height)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_height()


class PhotoMetadata(QWidget):
    FIELDS = (
        ('camera_make', 'Camera maker'), ('camera_model', 'Camera'),
        ('lens_model', 'Lens'), ('exposure_time', 'Shutter speed'),
        ('aperture', 'Aperture'), ('iso', 'ISO'),
        ('focal_length', 'Focal length'), ('captured_at', 'Captured'),
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self.layout_ = QVBoxLayout(self)
        self.layout_.setContentsMargins(0, 8, 0, 4)
        self.layout_.setSpacing(8)
        self.values = {}
        self.set_metadata({})

    def set_metadata(self, values):
        while self.layout_.count():
            item = self.layout_.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        self.values = {key: str(value) for key, value in (values or {}).items() if value is not None and str(value).strip()}
        rows = [(tr(label), self.values[key]) for key, label in self.FIELDS if key in self.values]
        gps = self.values.get('gps_latitude'), self.values.get('gps_longitude')
        if all(gps):
            rows.append((tr('GPS coordinates'), ', '.join(gps)))
        self.setVisible(bool(rows))
        if not rows:
            return
        self.layout_.addWidget(QLabel(tr('PHOTO'), objectName='section'))
        for title, value in rows:
            label = QLabel(title, objectName='muted')
            label.setStyleSheet('font-size: 10px;')
            self.layout_.addWidget(label)
            text = QLabel(value)
            text.setTextFormat(Qt.TextFormat.PlainText)
            text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            text.setWordWrap(True)
            self.layout_.addWidget(text)
        if all(gps):
            note = QLabel(tr('Coordinates embedded in the file; no place lookup.'), objectName='muted')
            note.setWordWrap(True)
            note.setStyleSheet('font-size: 10px;')
            self.layout_.addWidget(note)
