"""A scrollable information overlay that never changes the canvas layout."""
from __future__ import annotations

import math

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QTextOption
from PySide6.QtWidgets import QFrame, QLabel, QScrollArea, QTextBrowser, QVBoxLayout, QWidget

from .i18n import tr


class DetailsOverlay(QScrollArea):
    """Right-aligned canvas child; only its visible body receives input.

    This is deliberately not added to the middle layout. Toggling information
    must not resize QGraphicsView or alter its fit baseline, transform or center.
    A separate mouse-transparent sibling paints the exterior glass shadow.
    """
    visibilityChanged = Signal(bool)
    PANEL_WIDTH = 304
    SHADOW_WIDTH = 12

    def __init__(self, canvas, parent):
        super().__init__(parent)
        self.canvas = canvas
        self.glass = None
        self.setObjectName('sidebar')
        self.setAccessibleName(tr('Image information'))
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setStyleSheet('''
            QWidget { background: transparent; }
            QScrollArea#sidebar { border: 0; background: transparent; }
            QComboBox { background: rgba(255,255,255,15); border-color: rgba(255,255,255,24); }
            QComboBox QAbstractItemView { background: #2a2a2a; }
            QPushButton:hover { background: rgba(255,255,255,22); }
            QPushButton:pressed { background: rgba(255,255,255,35); }
            QScrollBar:vertical { background: transparent; width: 6px; }
            QScrollBar::handle:vertical { background: rgba(255,255,255,64); border-radius: 3px; }
        ''')
        self.viewport().setAutoFillBackground(False)
        self.content = QWidget(objectName='detailsContent')
        self.setWidget(self.content)
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(20, 22, 20, 18)
        self.content_layout.setSpacing(11)
        parent.installEventFilter(self)
        self.hide()
        self._place()

    def attach_glass(self):
        from .glass_backdrop import GlassBackdrop
        self.glass = GlassBackdrop(self.canvas, self.parentWidget(), shadow_width=self.SHADOW_WIDTH)
        self._place()
        self.glass.setVisible(self.isVisible())
        if self.isVisible():
            self.glass.raise_()
            self.raise_()

    def _place(self):
        parent = self.parentWidget()
        width = min(self.PANEL_WIDTH, parent.width())
        self.setGeometry(max(0, parent.width() - width), 0, width, parent.height())
        if self.glass is not None:
            self.glass.setGeometry(self.x() - self.SHADOW_WIDTH, self.y(),
                                   self.width() + self.SHADOW_WIDTH, self.height())
            if self.isVisible():
                self.glass.raise_()
                self.raise_()
                self.glass.invalidate()

    def invalidate_backdrop(self):
        if self.glass is not None and self.isVisible():
            self.glass.invalidate()

    def eventFilter(self, watched, event):
        if watched is self.parentWidget() and event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            self._place()
        return super().eventFilter(watched, event)

    def showEvent(self, event):
        super().showEvent(event)
        self._place()
        if self.glass is not None:
            self.glass.show()
            self.glass.raise_()
        self.raise_()
        self.visibilityChanged.emit(True)

    def hideEvent(self, event):
        if self.glass is not None:
            self.glass.hide()
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
