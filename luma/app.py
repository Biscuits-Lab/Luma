from __future__ import annotations

import argparse
import math
from pathlib import Path
import sys

from PySide6.QtCore import Qt, QSize, QTimer, QEvent, QSignalBlocker, QRectF, QPointF
from PySide6.QtGui import QIcon, QImage, QKeySequence, QShortcut, QPixmap, QFont, QPainter, QPen, QColor, QPainterPath, QIconEngine, QFontMetricsF, QPalette
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QFrame, QLabel, QPushButton,
    QHBoxLayout, QVBoxLayout, QFileDialog, QStackedWidget, QComboBox,
    QSlider, QListWidget, QListWidgetItem, QAbstractItemView,
    QSizePolicy, QMenu, QStyleOptionButton, QStylePainter, QStyle,
)

from . import __version__
from .canvas import ImageCanvas
from .details_overlay import DetailsOverlay, MetadataTextBrowser, PhotoMetadata
from .histogram import PreviewHistogram
from .i18n import tr, error_text, install_qt_translations, verify_packaged_qt_catalogs
from .dialogs import PlainMessageBox as QMessageBox
from .chrome import TitleBar, WindowChrome
from .menu_widgets import RoundedMenu, ImageContextMenu
from .decoders import SUPPORTED_EXTENSIONS
from .workers import LatestWorker, ImageService, scan_folder, ThumbnailService

ASSETS = Path(__file__).resolve().parent / 'assets'
STYLE = """
QWidget { background: #191919; color: #eeeeee; font-family: 'Segoe UI', 'Inter', sans-serif; font-size: 12px; }
QMainWindow { background: #191919; }
QFrame#toolbar { background: #191919; border: 0; }
QFrame#toolbar QPushButton { padding: 3px; }
QFrame#viewbar { background: #222222; border: 0; }
QFrame#viewbar QPushButton { padding: 3px; }
QFrame#viewbar QSlider { background: transparent; }
QFrame#sidebar { background: #222222; border: 0; }
QLabel { background: transparent; }
QLabel#muted { color: #979797; }
QLabel#section { color: #a0a0a0; font-size: 10px; font-weight: 600; letter-spacing: 1px; }
QLabel#brand { font-size: 14px; font-weight: 600; letter-spacing: 3px; }
QLabel#filename { font-size: 13px; font-weight: 500; }
QPushButton { border: 1px solid transparent; border-radius: 5px; padding: 7px 10px; background: transparent; }
QPushButton:hover { background: #373737; }
QPushButton:pressed { background: #484848; }
QPushButton:checked { background: #3c3c3c; border-color: #5a5a5a; }
QPushButton:disabled { color: #6b6b6b; }
QPushButton#primary { color: #1d1d1d; background: #f1f1f1; font-weight: 600; padding: 11px 22px; }
QPushButton#primary:hover { background: #ffffff; }
QComboBox { background: #2d2d2d; border: 1px solid #3d3d3d; border-radius: 4px; padding: 6px; }
QComboBox QAbstractItemView { selection-background-color: #494949; }
QSlider::groove:horizontal { height: 3px; background: #484848; border-radius: 1px; }
QSlider::sub-page:horizontal { background: #cdcdcd; }
QSlider::handle:horizontal { background: #f6f6f6; width: 12px; margin: -5px 0; border-radius: 6px; }
QListWidget { background: #191919; border: 0; outline: 0; padding: 5px 10px; }
QListWidget::item { background: transparent; border: 1px solid transparent; border-radius: 3px; padding: 3px; color: #a5a5a5; }
QListWidget::item:selected { border-color: #949494; background: transparent; color: #ffffff; }
QListWidget::item:hover { background: #222222; }
QTextBrowser { border: 0; background: transparent; color: #b7b7b7; padding: 0; }
QScrollBar:horizontal { background: #1e1e1e; height: 6px; }
QScrollBar:vertical { background: #222222; width: 6px; }
QScrollBar::handle { background: #4e4e4e; border-radius: 3px; min-width: 20px; min-height: 20px; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QStatusBar { background: #1d1d1d; color: #979797; border-top: 1px solid #2d2d2d; font-size: 11px; }
QMenu { background: #2a2a2a; border: 1px solid #484848; padding: 5px; }
QMenu::item { padding: 8px 22px; }
QMenu::item:selected { background: #414141; }
QMenu::separator { height: 1px; background: #484848; margin: 5px 8px; }
QToolTip { color: #eeeeee; background: #353535; border: 1px solid #5a5a5a; padding: 5px; }
"""


def button(text, callback, tip='', checkable=False):
    result = QPushButton(text)
    result.clicked.connect(callback)
    result.setToolTip(tip)
    if tip:
        result.setAccessibleName(tip)
    result.setCheckable(checkable)
    result.setCursor(Qt.CursorShape.PointingHandCursor)
    return result


def _paint_symbol(painter, kind):
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor('#eeeeee' if kind in ('rotate', 'delete') else '#dddddd'), 1.7)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    if kind == 'strip':
        painter.drawRoundedRect(5, 7, 22, 18, 1, 1)
        painter.drawLine(11, 7, 11, 25)
        painter.drawLine(21, 7, 21, 25)
        painter.drawLine(5, 12, 11, 12)
        painter.drawLine(5, 20, 11, 20)
        painter.drawLine(21, 12, 27, 12)
        painter.drawLine(21, 20, 27, 20)
    elif kind == 'fit':
        painter.drawRect(11, 11, 10, 10)
        for x, y, dx, dy in [(5, 5, 6, 6), (27, 5, -6, 6), (5, 27, 6, -6), (27, 27, -6, -6)]:
            painter.drawLine(x, y, x + dx, y)
            painter.drawLine(x, y, x, y + dy)
    elif kind in ('minus', 'plus'):
        painter.drawEllipse(6, 5, 15, 15)
        painter.drawLine(19, 19, 26, 26)
        painter.drawLine(10, 12, 17, 12)
        if kind == 'plus':
            painter.drawLine(14, 9, 14, 16)
    elif kind == 'rotate':
        # Reference: open circular arrow, hollow center pivot, lower-right head.
        arc = QPainterPath()
        arc.arcMoveTo(QRectF(3, 3, 25, 25), 180)
        arc.arcTo(QRectF(3, 3, 25, 25), 180, -250)
        arc.lineTo(20, 29)
        painter.drawPath(arc)
        painter.drawEllipse(QRectF(13, 13, 5, 5))
        arrow = QPainterPath(QPointF(20, 23))
        arrow.lineTo(20, 29)
        arrow.lineTo(26, 29)
        painter.drawPath(arrow)
    elif kind == 'more':
        painter.setBrush(QColor('#dddddd'))
        painter.setPen(Qt.PenStyle.NoPen)
        for x in (7, 16, 25):
            painter.drawEllipse(x - 1.5, 14.5, 3, 3)
    elif kind == 'delete':
        # Reference: separate handle/lid, tapered body and two inner stems.
        painter.drawLine(QPointF(3, 7), QPointF(29, 7))
        handle = QPainterPath(QPointF(12, 7))
        handle.lineTo(12, 4)
        handle.quadTo(12, 2.5, 14, 2.5)
        handle.lineTo(18, 2.5)
        handle.quadTo(20, 2.5, 20, 4)
        handle.lineTo(20, 7)
        painter.drawPath(handle)
        bin_path = QPainterPath(QPointF(6, 10))
        bin_path.lineTo(8, 29)
        bin_path.lineTo(24, 29)
        bin_path.lineTo(26, 10)
        painter.drawPath(bin_path)
        painter.drawLine(QPointF(13, 12), QPointF(13.5, 24))
        painter.drawLine(QPointF(19, 12), QPointF(18.5, 24))
    elif kind == 'info':
        painter.drawEllipse(QRectF(4, 4, 24, 24))
        painter.drawPoint(QPointF(16, 10))
        painter.drawLine(QPointF(16, 15), QPointF(16, 23))
    elif kind == 'window-minimize':
        painter.setPen(QPen(QColor('#dddddd'), 2.0))
        painter.drawLine(6, 16, 26, 16)
    elif kind == 'window-close':
        painter.setPen(QPen(QColor('#dddddd'), 2.0))
        painter.drawLine(7, 7, 25, 25)
        painter.drawLine(25, 7, 7, 25)
    elif kind in ('window-maximize', 'window-restore'):
        painter.setPen(QPen(QColor('#dddddd'), 2.0))
        if kind == 'window-restore':
            painter.drawRoundedRect(11, 5, 16, 16, 2, 2)
            painter.fillRect(5, 11, 17, 17, QColor('#191919'))
            painter.drawRoundedRect(5, 11, 16, 16, 2, 2)
        else:
            painter.drawRoundedRect(6, 6, 20, 20, 3, 3)
    elif kind == 'full':
        painter.drawLine(8, 24, 24, 8)
        painter.drawLine(16, 8, 24, 8)
        painter.drawLine(24, 8, 24, 16)
        painter.drawLine(8, 16, 8, 24)
        painter.drawLine(8, 24, 16, 24)


class SymbolIconEngine(QIconEngine):
    """Vector icons rasterize at Qt's requested size, including fractional DPI."""
    def __init__(self, kind):
        super().__init__()
        self.kind = kind

    def clone(self):
        return SymbolIconEngine(self.kind)

    def paint(self, painter, rect, mode, state):
        painter.save()
        painter.translate(rect.x(), rect.y())
        painter.scale(rect.width() / 32, rect.height() / 32)
        if mode == QIcon.Mode.Disabled:
            painter.setOpacity(0.4)
        _paint_symbol(painter, self.kind)
        painter.restore()

    def pixmap(self, size, mode, state):
        result = QPixmap(size)
        result.fill(Qt.GlobalColor.transparent)
        painter = QPainter(result)
        self.paint(painter, result.rect(), mode, state)
        painter.end()
        return result


def symbol_icon(kind):
    return QIcon(SymbolIconEngine(kind))


class ZoomMenuButton(QPushButton):
    """Stable percent text + a centered native chevron, independent of fonts."""
    def paintEvent(self, event):
        option = QStyleOptionButton()
        self.initStyleOption(option)
        # The style draws the button surface; labels/arrow use explicit geometry.
        option.text = ''
        option.features &= ~QStyleOptionButton.ButtonFeature.HasMenu
        painter = QStylePainter(self)
        painter.drawControl(QStyle.ControlElement.CE_PushButton, option)
        group = QPalette.ColorGroup.Active if self.isEnabled() else QPalette.ColorGroup.Disabled
        color = self.palette().color(group, QPalette.ColorRole.ButtonText)
        painter.setPen(color)
        painter.drawText(self.rect().adjusted(5, 0, -20, 0), Qt.AlignmentFlag.AlignCenter, self.text())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(color, 1.4)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        x, y = self.width() - 13, self.height() / 2
        arrow = QPainterPath(QPointF(x - 3, y - 1.5))
        arrow.lineTo(x, y + 1.5)
        arrow.lineTo(x + 3, y - 1.5)
        painter.drawPath(arrow)


class FooterInformation(QWidget):
    """A stable left region; the info button follows the actual text inside it."""
    def sizeHint(self):
        return QSize(316, 30)

    def minimumSizeHint(self):
        return QSize(40, 30)


class FooterStatusLabel(QLabel):
    """Natural-width text inside a bounded region, with lossless full tooltip."""
    def __init__(self):
        super().__init__('', objectName='muted')
        self._full_text = ''
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setMaximumWidth(280)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)

    def sizeHint(self):
        return QSize(min(280, math.ceil(QFontMetricsF(self.font()).horizontalAdvance(self._full_text)) + 2),
                     super().sizeHint().height())

    def minimumSizeHint(self):
        return QSize(0, super().minimumSizeHint().height())

    def setText(self, text):
        self._full_text = text
        self.setToolTip(text)
        self._elide()
        self.updateGeometry()

    def _elide(self):
        super().setText(self.fontMetrics().elidedText(
            self._full_text, Qt.TextElideMode.ElideRight, self.contentsRect().width()))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide()


class ViewerWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        install_qt_translations()
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setWindowTitle('Luma')
        self.setWindowIcon(QIcon(str(ASSETS / 'icon.ico')))
        self.resize(1280, 820)
        self.setMinimumSize(800, 560)
        self.setAcceptDrops(True)
        self.files: list[Path] = []
        self.index = -1
        self.path: Path | None = None
        self.displayed_path: Path | None = None
        self.directory: Path | None = None
        self._load_token = self._scan_token = self._thumb_token = 0
        self._new_image = True
        self._closing = False
        self.image_linear = False
        self.service = ImageService()
        self.loader = LatestWorker(self.service, self, idle_handler=self.service.prefetch)
        self._navigation_direction = 1
        self.scanner = LatestWorker(scan_folder, self)
        self.thumbnail_service = ThumbnailService()
        self.thumbnails = LatestWorker(self.thumbnail_service, self)
        self.loader.completed.connect(self._loaded)
        self.loader.failed.connect(self._load_failed)
        self.scanner.completed.connect(self._scanned)
        self.scanner.failed.connect(self._scan_failed)
        self.thumbnails.completed.connect(self._thumbs_ready)
        self._build_ui()
        self.chrome = WindowChrome(self)
        self._shortcuts()
        self.render_timer = QTimer(self)
        self.render_timer.setSingleShot(True)
        self.render_timer.setInterval(70)
        self.render_timer.timeout.connect(self._request_render)
        self.prefetch_timer = QTimer(self)
        self.prefetch_timer.setSingleShot(True)
        self.prefetch_timer.setInterval(40)
        self.prefetch_timer.timeout.connect(self._prefetch_next)
        self._pending_render = False
        self.busy_timer = QTimer(self)
        self.busy_timer.setSingleShot(True)
        self.busy_timer.setInterval(150)
        self.busy_timer.timeout.connect(self._show_loading_indicator)
        self._set_controls(False)
        self._status(tr('Drop an image to begin  ·  Ctrl+O to open'))

    def _build_ui(self):
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.setCentralWidget(root)
        top = TitleBar(objectName='toolbar')
        self.titlebar = top
        top.setFixedHeight(34)
        row = QHBoxLayout(top)
        row.setContentsMargins(10, 1, 10, 1)
        row.setSpacing(5)
        left = QWidget()
        left.setStyleSheet('background: transparent;')
        left.setFixedWidth(170)
        left_row = QHBoxLayout(left)
        left_row.setContentsMargins(0, 0, 0, 0)
        left_row.setSpacing(7)
        self.rotate_button = button('', self.rotate_image, tr('Rotate clockwise · R'))
        self.rotate_button.setIcon(symbol_icon('rotate'))
        self.rotate_button.setFixedWidth(34)
        left_row.addWidget(self.rotate_button)
        self.delete_button = button('', self.delete_image, tr('Move image to recycle bin · Delete'))
        self.delete_button.setIcon(symbol_icon('delete'))
        self.delete_button.setFixedWidth(34)
        left_row.addWidget(self.delete_button)
        self.app_menu = RoundedMenu(self)
        self.save_action = self.app_menu.add_icon_action(tr('Save As…    Ctrl+S'), 'save', self.save_as)
        self.copy_action = self.app_menu.add_icon_action(tr('Copy image    Ctrl+C'), 'copy', self.copy_image)
        self.app_menu.addSeparator()
        self.app_menu.add_icon_action(tr('Open image    Ctrl+O'), 'image', self.choose_file)
        self.app_menu.add_icon_action(tr('Open folder    Ctrl+Shift+O'), 'folder', self.choose_folder)
        self.app_menu.addSeparator()
        self.app_menu.add_icon_action(tr('Settings'), 'settings', self.show_settings)
        self.app_menu.add_icon_action(tr('Quit    Ctrl+Q'), 'quit', self.close)
        self.more_button = QPushButton('')
        self.more_button.setIcon(symbol_icon('more'))
        for control in (self.rotate_button, self.delete_button):
            control.setIconSize(QSize(20, 20))
        self.more_button.setIconSize(QSize(17, 17))
        self.more_button.setMenu(self.app_menu)
        self.more_button.setToolTip(tr('Save As, copy, open and settings'))
        self.more_button.setAccessibleName(tr('Save As, copy, open and settings'))
        self.more_button.setFixedWidth(38)
        self.more_button.setStyleSheet('QPushButton::menu-indicator { image: none; width: 0px; }')
        left_row.addWidget(self.more_button)
        left_row.addStretch()
        row.addWidget(left)
        self.filename = QLabel('Luma', objectName='filename')
        self.filename.setTextFormat(Qt.TextFormat.PlainText)
        self.filename.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.filename.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.filename.setMinimumWidth(80)
        self.filename.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        row.addWidget(self.filename, 1)
        right = QWidget()
        right.setStyleSheet('background: transparent;')
        right.setFixedWidth(170)
        system = QHBoxLayout(right)
        system.setContentsMargins(0, 0, 0, 0)
        system.setSpacing(2)
        system.addStretch()
        minimize = button('', self.showMinimized, tr('Minimize'))
        minimize.setAccessibleName(tr('Minimize window'))
        minimize.setFixedWidth(37)
        self.maximize_button = button('', self.toggle_maximized, tr('Maximize / restore'))
        self.maximize_button.setAccessibleName(tr('Maximize or restore window'))
        self.maximize_button.setFixedWidth(37)
        close_button = button('', self.close, tr('Close'))
        close_button.setAccessibleName(tr('Close window'))
        close_button.setFixedWidth(37)
        close_button.setStyleSheet('QPushButton:hover { background: #b63b44; }')
        for control, kind in ((minimize, 'window-minimize'), (self.maximize_button, 'window-maximize'), (close_button, 'window-close')):
            control.setFixedSize(38, 30)
            control.setIcon(symbol_icon(kind))
            control.setIconSize(QSize(17, 17))
        system.addWidget(minimize)
        system.addWidget(self.maximize_button)
        system.addWidget(close_button)
        row.addWidget(right)
        self.previous = button('‹', lambda: self.navigate(-1), tr('Previous image · Left'))
        self.previous.setFixedWidth(29)
        self.next = button('›', lambda: self.navigate(1), tr('Next image · Right'))
        self.next.setFixedWidth(29)
        self.counter = QLabel('—', objectName='muted')
        self.counter.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.counter.setMinimumWidth(45)
        layout.addWidget(top)

        middle = QHBoxLayout()
        middle.setSpacing(0)
        self.stack = QStackedWidget()
        self.canvas = ImageCanvas()
        self._build_image_context_menu()
        self.canvas.installEventFilter(self)
        self.canvas.viewport().installEventFilter(self)
        self.canvas.zoomChanged.connect(self._zoom_changed)
        self.welcome = QWidget()
        welcome_layout = QVBoxLayout(self.welcome)
        welcome_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        welcome_layout.setSpacing(15)
        logo = QLabel()
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo.setPixmap(QPixmap(str(ASSETS / 'icon.png')).scaled(88, 88, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        welcome_layout.addWidget(logo)
        title = QLabel(tr('Room for the image'))
        title.setStyleSheet('font-size: 27px; font-weight: 400; color: #eeeeee;')
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        welcome_layout.addWidget(title)
        subtitle = QLabel(tr('Drop an image or a folder to begin'))
        subtitle.setObjectName('muted')
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        welcome_layout.addWidget(subtitle)
        open_btn = button(tr('Open an image'), self.choose_file)
        open_btn.setObjectName('primary')
        welcome_layout.addWidget(open_btn, 0, Qt.AlignmentFlag.AlignCenter)
        formats = QLabel('JPG  ·  PNG  ·  WEBP  ·  TIFF  ·  RAW  ·  EXR  ·  HDR')
        formats.setObjectName('muted')
        formats.setStyleSheet('font-size: 10px; margin-top: 15px;')
        formats.setAlignment(Qt.AlignmentFlag.AlignCenter)
        welcome_layout.addWidget(formats)
        self.stack.addWidget(self.welcome)
        self.stack.addWidget(self.canvas)
        middle.addWidget(self.stack, 1)
        self.sidebar = DetailsOverlay(self.canvas, self.stack)
        side = self.sidebar.content_layout
        side.addWidget(QLabel(tr('IMAGE'), objectName='section'))
        self.dimension_label = QLabel(tr('No image selected'))
        self.dimension_label.setWordWrap(True)
        self.dimension_label.setStyleSheet('font-size: 19px; font-weight: 400;')
        side.addWidget(self.dimension_label)
        self.type_label = QLabel('')
        self.type_label.setTextFormat(Qt.TextFormat.PlainText)
        self.type_label.setObjectName('muted')
        self.type_label.setWordWrap(True)
        side.addWidget(self.type_label)
        side.addSpacing(4)
        self.histogram = PreviewHistogram(lambda: self.canvas.item.pixmap())
        side.addWidget(self.histogram)
        self.sidebar.visibilityChanged.connect(self.histogram.set_active)
        side.addSpacing(8)
        side.addWidget(QLabel(tr('CHANNEL'), objectName='section'))
        self.channel = QComboBox()
        self.channel.addItems(['RGBA', 'RGB', 'R', 'G', 'B', 'A'])
        self.channel.currentTextChanged.connect(lambda _: self._schedule_render())
        side.addWidget(self.channel)
        side.addSpacing(10)
        exposure_row = QHBoxLayout()
        self.exposure_heading = QLabel(tr('EXPOSURE'), objectName='section')
        exposure_row.addWidget(self.exposure_heading)
        exposure_row.addStretch()
        self.exposure_label = QLabel('0.0 EV', objectName='muted')
        exposure_row.addWidget(self.exposure_label)
        side.addLayout(exposure_row)
        self.exposure = QSlider(Qt.Orientation.Horizontal)
        self.exposure.setRange(-100, 100)
        self.exposure.setValue(0)
        self.exposure.valueChanged.connect(self._exposure_changed)
        side.addWidget(self.exposure)
        self.tone = QComboBox()
        self.tone.addItem('Reinhard', 'Reinhard')
        self.tone.addItem(tr('Clip'), 'Clip')
        self.tone.currentTextChanged.connect(lambda _: self._schedule_render())
        side.addWidget(self.tone)
        self.hdr_note = QLabel(tr('Exposure is available for scene-linear HDR and RAW images. Output is SDR.'))
        self.hdr_note.setWordWrap(True)
        self.hdr_note.setObjectName('muted')
        self.hdr_note.setStyleSheet('font-size: 10px; line-height: 150%;')
        side.addWidget(self.hdr_note)
        side.addWidget(button(tr('Reset display'), self.reset_display, tr('Reset exposure, tone map and channels')))
        side.addSpacing(10)
        side.addWidget(QLabel(tr('BACKGROUND'), objectName='section'))
        self.background = QComboBox()
        for key in ('Graphite', 'Checkerboard', 'Black', 'White'):
            self.background.addItem(tr(key), key)
        self.background.currentIndexChanged.connect(self._background_changed)
        side.addWidget(self.background)
        side.addSpacing(10)
        self.photo_metadata = PhotoMetadata()
        side.addWidget(self.photo_metadata)
        side.addWidget(QLabel(tr('DETAILS'), objectName='section'))
        self.details = MetadataTextBrowser()
        side.addWidget(self.details)
        side.addWidget(button(tr('Keyboard shortcuts'), self.show_shortcuts))
        side.addStretch(1)
        self.sidebar.attach_glass()
        self.sidebar.hide()
        layout.addLayout(middle, 1)

        self.filmstrip = QListWidget()
        self.filmstrip.setFixedHeight(84)
        self.filmstrip.setViewMode(QListWidget.ViewMode.IconMode)
        self.filmstrip.setFlow(QListWidget.Flow.LeftToRight)
        self.filmstrip.setWrapping(False)
        self.filmstrip.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.filmstrip.setMovement(QListWidget.Movement.Static)
        self.filmstrip.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.filmstrip.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.filmstrip.setIconSize(QSize(72, 46))
        self.filmstrip.setGridSize(QSize(91, 66))
        self.filmstrip.setSpacing(3)
        self.filmstrip.setStyleSheet('font-size: 9px;')
        self.filmstrip.itemClicked.connect(self._filmstrip_clicked)
        layout.addWidget(self.filmstrip)
        self.filmstrip.hide()
        bar = QFrame(objectName='viewbar')
        self.viewbar = bar
        bar.setFixedHeight(36)
        controls = QHBoxLayout(bar)
        controls.setContentsMargins(12, 2, 14, 2)
        controls.setSpacing(6)
        self.strip_button = button('', self._toggle_strip, tr('Show / hide thumbnails · T'), True)
        self.strip_button.setIcon(symbol_icon('strip'))
        self.strip_button.setChecked(False)
        self.strip_button.setFixedWidth(35)
        controls.addWidget(self.strip_button)
        self.previous.hide()
        self.counter.hide()
        self.next.hide()
        controls.addSpacing(8)
        self.metadata_group = FooterInformation()
        self.metadata_group.setMaximumWidth(316)
        metadata_row = QHBoxLayout(self.metadata_group)
        metadata_row.setContentsMargins(0, 0, 0, 0)
        metadata_row.setSpacing(6)
        self.status_label = FooterStatusLabel()
        metadata_row.addWidget(self.status_label)
        self.info_button = button('', self._toggle_info, tr('Show / hide image information · I'), True)
        self.info_button.setIcon(symbol_icon('info'))
        self.info_button.setChecked(False)
        self.info_button.setFixedSize(30, 30)
        metadata_row.addWidget(self.info_button)
        metadata_row.addStretch(1)
        controls.addWidget(self.metadata_group)
        controls.addStretch(1)
        self.cancel_button = button(tr('Cancel'), self.cancel_load, tr('Cancel pending image · Escape'))
        cancel_policy = self.cancel_button.sizePolicy()
        cancel_policy.setRetainSizeWhenHidden(True)
        self.cancel_button.setSizePolicy(cancel_policy)
        controls.addWidget(self.cancel_button)
        self.cancel_button.hide()
        self.fit_button = button('', lambda: self.canvas.fit(), tr('Fit to window · F'))
        self.fit_button.setIcon(symbol_icon('fit'))
        self.fit_button.setFixedWidth(34)
        controls.addWidget(self.fit_button)
        self.zoom_combo = ZoomMenuButton('—')
        self.zoom_combo.setFixedWidth(82)
        self.zoom_combo.setToolTip(tr('Zoom relative to fit · 100% fits the window'))
        self.zoom_combo.setFixedHeight(28)
        self.zoom_combo.setStyleSheet('QPushButton { background: #303030; padding: 0; } QPushButton:hover { background: #3b3b3b; }')
        zoom_menu = QMenu(self.zoom_combo)
        for percent in (25, 50, 100, 200, 400, 800):
            zoom_menu.addAction(f'{percent}%', lambda checked=False, value=percent: self._zoom_preset(value))
        zoom_menu.addSeparator()
        zoom_menu.addAction(tr('Actual pixels (1:1)    1'), self.canvas.actual_size)
        self.zoom_combo.setMenu(zoom_menu)
        controls.addWidget(self.zoom_combo)
        self.actual_button = button('1:1', lambda: self.canvas.actual_size(), tr('Actual pixels · 1'))
        self.actual_button.setFixedWidth(35)
        self.actual_button.hide()
        zoom_out = button('', lambda: self.canvas.zoom(0.8), tr('Zoom out'))
        zoom_out.setIcon(symbol_icon('minus'))
        zoom_out.setFixedWidth(32)
        controls.addWidget(zoom_out)
        self.zoom_slider = QSlider(Qt.Orientation.Horizontal)
        self.zoom_slider.setRange(-153, 120)
        self.zoom_slider.setValue(0)
        self.zoom_slider.setFixedWidth(100)
        self.zoom_slider.setToolTip(tr('Zoom relative to fit · 100% fits the window'))
        self.zoom_slider.valueChanged.connect(self._zoom_slider_changed)
        controls.addWidget(self.zoom_slider)
        zoom_in = button('', lambda: self.canvas.zoom(1.25), tr('Zoom in'))
        zoom_in.setIcon(symbol_icon('plus'))
        zoom_in.setFixedWidth(32)
        controls.addWidget(zoom_in)
        full_button = button('', self.toggle_fullscreen, tr('Full screen · F11'))
        full_button.setIcon(symbol_icon('full'))
        full_button.setFixedWidth(34)
        controls.addWidget(full_button)
        for control in (self.strip_button, self.info_button, self.fit_button,
                        zoom_out, zoom_in, full_button):
            control.setIconSize(QSize(24, 24))
        self.info_button.setIconSize(QSize(20, 20))
        layout.addWidget(bar)
        self.statusBar().hide()

    def _build_image_context_menu(self):
        self.image_context_menu = ImageContextMenu(self)
        self.image_context_menu.setAccessibleName(tr('Image actions'))
        self._image_context_source = None
        self.context_copy_action = self.image_context_menu.add_strip_action(
            tr('Copy'), 'copy', lambda: self._run_image_context_action(self.copy_image),
            tr('Copy image    Ctrl+C'))
        self.context_rotate_action = self.image_context_menu.add_strip_action(
            tr('Rotate'), 'rotate', lambda: self._run_image_context_action(self.rotate_image),
            tr('Rotate clockwise · R'))
        self.context_delete_action = self.image_context_menu.add_strip_action(
            tr('Delete'), 'delete', lambda: self._run_image_context_action(self.delete_image),
            tr('Move image to recycle bin · Delete'))
        self.context_save_action = self.image_context_menu.add_icon_action(
            tr('Save As    Ctrl+S'), 'save', lambda: self._run_image_context_action(self.save_as))
        self.context_path_action = self.image_context_menu.add_icon_action(
            tr('Copy image path'), 'path', lambda: self._run_image_context_action(self.copy_image_path))
        self.image_context_menu.image_actions.extend((self.context_save_action, self.context_path_action))

    def _context_image_ready(self):
        return (self.path is not None and self.displayed_path == self.path
                and not getattr(self, '_pending_render', False)
                and not self.canvas.item.pixmap().isNull())

    def _show_image_context_menu(self, global_position, keyboard=False):
        ready = self._context_image_ready()
        for action in self.image_context_menu.image_actions:
            action.setEnabled(ready)
        self._image_context_source = (self.path, self._load_token) if ready else None
        self.image_context_menu.popup(global_position)
        if keyboard and ready:
            self.image_context_menu._focus_strip()

    def _run_image_context_action(self, callback):
        # A popup must never act on a different image that arrived asynchronously
        # after it opened, especially when the chosen action is recycle/delete.
        valid = (self._context_image_ready() and self._image_context_source is not None
                 and self._image_context_source == (self.path, self._load_token))
        self.image_context_menu.close()
        self._image_context_source = None
        if valid:
            callback()

    def copy_image_path(self):
        if self._context_image_ready():
            QApplication.clipboard().setText(str(self.path))
            self._status(tr('Image path copied to clipboard'))

    def eventFilter(self, watched, event):
        if watched in (self.canvas, self.canvas.viewport()):
            if event.type() == QEvent.Type.ContextMenu:
                # Keyboard context events may carry an arbitrary cursor position.
                from PySide6.QtGui import QContextMenuEvent
                keyboard = event.reason() == QContextMenuEvent.Reason.Keyboard
                position = (self.canvas.viewport().mapToGlobal(self.canvas.viewport().rect().center())
                            if keyboard else event.globalPos())
                self._show_image_context_menu(position, keyboard)
                event.accept()
                return True
            if event.type() == QEvent.Type.KeyPress and (
                    event.key() == Qt.Key.Key_Menu or
                    (event.key() == Qt.Key.Key_F10 and event.modifiers() == Qt.KeyboardModifier.ShiftModifier)):
                self._show_image_context_menu(
                    self.canvas.viewport().mapToGlobal(self.canvas.viewport().rect().center()), True)
                event.accept()
                return True
        return super().eventFilter(watched, event)

    def _status(self, text):
        self.status_label.setText(text)
        self.status_label.setToolTip(text)

    def _zoom_changed(self, percent):
        text = f'{percent:.1f}%' if percent < 1 else f'{percent:.0f}%'
        if self.zoom_combo.text() != text:
            self.zoom_combo.setText(text)
        # Updating the slider must not feed its quantized value back to canvas.
        with QSignalBlocker(self.zoom_slider):
            self.zoom_slider.setValue(round(math.log2(max(0.5, percent) / 100) * 20))

    def _zoom_preset(self, percent):
        self.canvas.set_zoom_percent(percent)

    def _zoom_slider_changed(self, value):
        self.canvas.set_zoom_percent(100 * 2 ** (value / 20))

    def _shortcuts(self):
        shortcuts = {
            'Ctrl+O': self.choose_file, 'Ctrl+Shift+O': self.choose_folder,
            'Left': lambda: self.navigate(-1), 'Right': lambda: self.navigate(1),
            'Home': lambda: self.select_index(0), 'End': lambda: self.select_index(len(self.files) - 1),
            'F': self.canvas.fit, '1': self.canvas.actual_size,
            '+': lambda: self.canvas.zoom(1.25), '=': lambda: self.canvas.zoom(1.25),
            '-': lambda: self.canvas.zoom(0.8), 'F11': self.toggle_fullscreen,
            'Escape': self.escape, 'I': self.info_button.click, 'T': self.strip_button.click,
            'Ctrl+0': self.reset_display, 'Ctrl+Q': self.close,
            'R': self.rotate_image, 'Delete': self.delete_image,
            'Ctrl+S': self.save_as, 'Ctrl+C': self.copy_image,
        }
        self.shortcuts = []
        for key, callback in shortcuts.items():
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.activated.connect(callback)
            self.shortcuts.append(shortcut)

    def choose_file(self):
        extensions = ' '.join('*' + x for x in sorted(SUPPORTED_EXTENSIONS))
        name, _ = QFileDialog.getOpenFileName(self, tr('Open image'), str(self.directory or Path.home()), tr('Images ({extensions});;All files (*)', extensions=extensions))
        if name:
            self.open_path(Path(name))

    def choose_folder(self):
        name = QFileDialog.getExistingDirectory(self, tr('Open image folder'), str(self.directory or Path.home()))
        if name:
            self.open_path(Path(name))

    def prepare_initial_path(self, path: Path):
        """Stage the first frame without I/O or waiting for an image to decode.

        A file association already supplied a target, so the welcome/logo page
        must never be painted first. Keep the window visible and usable while
        the deferred open and worker perform the actual filesystem/decode work.
        """
        self.stack.setCurrentWidget(self.canvas)
        self.filename.setText(path.name or str(path))
        self.filename.setToolTip(str(path))
        self.dimension_label.setText(tr('Loading…'))
        self.histogram.clear('loading')
        self._status(tr('Loading {name}…', name=path.name or str(path)))

    def open_path(self, path: Path):
        if self._closing:
            return
        path = Path(path).expanduser().absolute()
        if not path.exists():
            self._status(tr('File not found: {path}', path=path))
            if self.path is None and self.displayed_path is None:
                self.stack.setCurrentWidget(self.welcome)
                self.dimension_label.setText(tr('No image selected'))
            return
        self.cancel_load(silent=True)
        self.scanner.cancel()
        self.thumbnails.cancel()
        directory = path if path.is_dir() else path.parent
        selected = None if path.is_dir() else path
        self.directory = directory
        self.files = []
        self.index = -1
        self.filmstrip.clear()
        self.counter.setText('—')
        self.previous.setEnabled(False)
        self.next.setEnabled(False)
        self._scan_token = self.scanner.submit((directory, selected))
        if selected:
            self._open_image(selected)
        else:
            self.path = None
            self.displayed_path = None
            self.canvas.clear_image()
            self.histogram.clear()
            self.photo_metadata.set_metadata({})
            # The folder is still opening; welcome is only a terminal empty
            # state, never a transient page on the way to its first image.
            self.stack.setCurrentWidget(self.canvas)
            self._set_controls(False)
            self.dimension_label.setText(tr('No image selected'))
            self.type_label.clear()
            self.details.clear()
            self.filename.setText(tr('Opening folder…'))
            self._status(tr('Looking for images…'))
            self.cancel_button.show()

    def _scanned(self, token, result):
        if token != self._scan_token or self._closing or result is None:
            return
        directory, files, selected = result
        self.files = files
        if not files:
            self.cancel_button.hide()
            if selected is None:
                self.stack.setCurrentWidget(self.welcome)
                self.filename.setText(tr('No images in this folder'))
                self._status(tr('No supported images found in this folder'))
            return
        if selected is not None:
            if selected in files:
                self.index = files.index(selected)
            else:
                self.files.insert(0, selected)
                self.index = 0
            self._update_navigation()
            self._build_filmstrip()
        else:
            self.select_index(0)

    def _scan_failed(self, token, message):
        if token != self._scan_token or self._closing:
            return
        self.cancel_button.hide()
        if self.path is None:
            self.stack.setCurrentWidget(self.welcome)
        self._status(error_text('Could not read folder.', message))

    def select_index(self, index):
        if not self.files or index < 0 or index >= len(self.files):
            return
        if self.index >= 0 and index != self.index:
            self._navigation_direction = 1 if index > self.index else -1
        self.index = index
        self._open_image(self.files[index])
        self._update_navigation()
        self._build_filmstrip()

    def navigate(self, direction):
        self.select_index(self.index + direction)

    def _update_navigation(self):
        self.counter.setText(f'{self.index + 1} / {len(self.files)}')
        self.previous.setEnabled(self.index > 0)
        self.next.setEnabled(0 <= self.index < len(self.files) - 1)

    def _open_image(self, path):
        self.prefetch_timer.stop()
        self.render_timer.stop()
        self.loader.cancel()
        self.path = path
        self.displayed_path = None
        self._new_image = True
        self.filename.setText(path.name)
        self.filename.setToolTip(str(path))
        self.setWindowTitle(f'{path.name} — Luma')
        # Clear the previous frame: a new filename must never label stale pixels.
        self.canvas.clear_image()
        self.histogram.clear('loading')
        self.photo_metadata.set_metadata({})
        self.stack.setCurrentWidget(self.canvas)
        self.dimension_label.setText(tr('Loading…'))
        self.type_label.setText(path.suffix.upper().lstrip('.'))
        self.details.clear()
        self.channel.blockSignals(True)
        self.channel.setCurrentText('RGBA')
        self.channel.blockSignals(False)
        self.exposure.blockSignals(True)
        self.exposure.setValue(0)
        self.exposure.blockSignals(False)
        self.exposure_label.setText('0.0 EV')
        self._set_controls(False, preserve_zoom=True)
        self._request_render()

    def _request_render(self):
        self.image_context_menu.close()
        self._image_context_source = None
        if self.path is None:
            return
        self._pending_render = True
        self.busy_timer.start()
        self._load_token = self.loader.submit((self.path, self.exposure.value() / 10, self.channel.currentText(), self.tone.currentData()))

    def _show_loading_indicator(self):
        if self._pending_render and self.path is not None and not self._closing:
            self.cancel_button.show()
            self._status(tr('Loading {name}…', name=self.path.name))

    def _schedule_render(self):
        if hasattr(self, 'render_timer') and self.path is not None:
            self.render_timer.start()

    def _loaded(self, token, result):
        if token != self._load_token or self._closing or result is None or result['path'] != self.path:
            return
        self._pending_render = False
        self.busy_timer.stop()
        rgba = result['rgba']
        h, w = rgba.shape[:2]
        # Make the owning native-format image in one pass. Copying RGBA first
        # made an extra full-size allocation before QPixmap converted the same
        # pixels to its premultiplied format. This conversion also detaches from
        # the worker/cache buffer, so the canvas never borrows ndarray storage.
        image = QImage(rgba.data, w, h, rgba.strides[0], QImage.Format.Format_RGBA8888).convertToFormat(
            QImage.Format.Format_ARGB32_Premultiplied)
        self.canvas.set_image(image, reset=self._new_image)
        self.histogram.set_preview(result.get('render_key', ('frame', self.canvas.item.pixmap().cacheKey())))
        self.sidebar.invalidate_backdrop()
        self._new_image = False
        self.displayed_path = self.path
        self.image_linear = result['linear']
        self._set_controls(True, result['linear'])
        self.dimension_label.setText(f'{w:,} × {h:,}')
        self.type_label.setText(f'{self.path.suffix[1:].upper()}  ·  {tr("Scene-linear → SDR") if result["linear"] else tr("SDR preview")}')
        metadata = result['metadata']
        self.photo_metadata.set_metadata(result.get('photo_metadata', {}))
        self.details.setPlainText(tr('Original metadata:') + '\n\n' +
                                  '\n\n'.join(f'{key}\n{value}' for key, value in metadata.items()))
        self.cancel_button.hide()
        try:
            byte_size = self.path.stat().st_size
            size_text = f'{byte_size / 1024 / 1024:.1f} MB' if byte_size >= 1024 * 1024 else (f'{byte_size / 1024:.1f} KB' if byte_size >= 1024 else f'{byte_size} B')
        except OSError:
            size_text = '—'
        self._status(f'{self.path.suffix[1:].upper()}   |   {w} × {h}   |   {size_text}')
        if self.filmstrip.isVisible():
            for i in range(self.filmstrip.count()):
                item = self.filmstrip.item(i)
                if item.data(Qt.ItemDataRole.UserRole) == str(self.path):
                    item.setIcon(QIcon(self.canvas.item.pixmap().scaled(72, 46, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)))
        self.prefetch_timer.start()

    def _prefetch_next(self):
        if self._closing or self.displayed_path != self.path or self.index < 0:
            return
        candidate = self.index + self._navigation_direction
        if not 0 <= candidate < len(self.files):
            candidate = self.index - self._navigation_direction
        if 0 <= candidate < len(self.files) and candidate != self.index:
            self.loader.submit_idle((self.files[candidate], 0.0, 'RGBA', self.tone.currentData()))

    def _load_failed(self, token, message):
        if token != self._load_token or self._closing:
            return
        self._pending_render = False
        self.busy_timer.stop()
        self.cancel_button.hide()
        self.canvas.clear_image()
        self.histogram.clear('error')
        self.photo_metadata.set_metadata({})
        self.displayed_path = None
        self.dimension_label.setText(tr('Unable to open'))
        self.details.setPlainText(error_text('Could not open {name}.', message,
                                              name=self.path.name if self.path else tr('image')))
        self._set_controls(False)
        self._status(error_text('Could not open {name}.', message,
                                name=self.path.name if self.path else tr('image')))

    def _set_controls(self, enabled, linear=False, preserve_zoom=False):
        if not enabled:
            self.image_context_menu.close()
            self._image_context_source = None
        for action in self.image_context_menu.image_actions:
            action.setEnabled(enabled)
        self.rotate_button.setEnabled(enabled)
        self.delete_button.setEnabled(enabled)
        self.save_action.setEnabled(enabled)
        self.copy_action.setEnabled(enabled)
        # During navigation the empty canvas safely ignores zoom commands; do
        # not flash the stable footer between disabled/enabled colors.
        if not preserve_zoom:
            self.fit_button.setEnabled(enabled)
            self.actual_button.setEnabled(enabled)
            self.zoom_combo.setEnabled(enabled)
            self.zoom_slider.setEnabled(enabled)
        self.channel.setEnabled(enabled)
        self.exposure.setEnabled(enabled and linear)
        self.tone.setEnabled(enabled and linear)
        for control in (self.exposure_heading, self.exposure_label, self.exposure, self.tone, self.hdr_note):
            control.setVisible(enabled and linear)

    def _exposure_changed(self, value):
        self.exposure_label.setText(f'{value / 10:+.1f} EV')
        self._schedule_render()

    def reset_display(self):
        self.channel.setCurrentText('RGBA')
        self.exposure.setValue(0)
        self.tone.setCurrentIndex(self.tone.findData('Reinhard'))

    def _background_changed(self, value):
        self.canvas.background = self.background.currentData()
        self.canvas.viewport().update()
        self.sidebar.invalidate_backdrop()

    def _build_filmstrip(self):
        self.filmstrip.clear()
        if self.index < 0:
            return
        # A sliding 31-item window bounds both widget and thumbnail allocations.
        start = max(0, self.index - 15)
        end = min(len(self.files), self.index + 16)
        paths = self.files[start:end]
        for path in paths:
            item = QListWidgetItem(path.name)
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            item.setToolTip(str(path))
            item.setIcon(QIcon(str(ASSETS / 'icon.png')))
            self.filmstrip.addItem(item)
            if path == self.path:
                self.filmstrip.setCurrentItem(item)
                if self.filmstrip.isVisible() and self.displayed_path == path and not self.canvas.item.pixmap().isNull():
                    item.setIcon(QIcon(self.canvas.item.pixmap().scaled(72, 46, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)))
        if self.filmstrip.isVisible():
            self.filmstrip.scrollToItem(self.filmstrip.currentItem(), QAbstractItemView.ScrollHint.PositionAtCenter)
            self._thumb_token = self.thumbnails.submit(paths)
        else:
            self.thumbnails.cancel()
            self._thumb_token = -1

    def _thumbs_ready(self, token, result):
        if token != self._thumb_token or self._closing:
            return
        for i in range(self.filmstrip.count()):
            item = self.filmstrip.item(i)
            data = result.get(item.data(Qt.ItemDataRole.UserRole))
            if data:
                (w, h), pixels = data
                image = QImage(pixels, w, h, w * 4, QImage.Format.Format_RGBA8888).copy()
                item.setIcon(QIcon(QPixmap.fromImage(image)))

    def _filmstrip_clicked(self, item):
        path = Path(item.data(Qt.ItemDataRole.UserRole))
        if path in self.files:
            self.select_index(self.files.index(path))

    def _toggle_strip(self, checked):
        self.filmstrip.setVisible(checked)
        if checked:
            self._build_filmstrip()
        else:
            self.thumbnails.cancel()
            self._thumb_token = -1

    def _toggle_info(self, checked):
        self.sidebar.setVisible(checked)
        if not checked:
            self.canvas.setFocus()

    def toggle_fullscreen(self):
        self.showNormal() if self.isFullScreen() else self.showFullScreen()

    def escape(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.cancel_load()

    def cancel_load(self, silent=False):
        self._pending_render = False
        self.busy_timer.stop()
        self.prefetch_timer.stop()
        self.loader.cancel()
        self.scanner.cancel()
        self.thumbnails.cancel()
        self.render_timer.stop()
        self._load_token = self._scan_token = self._thumb_token = -1
        self.cancel_button.hide()
        if not silent:
            self._status(tr('Pending work cancelled. An active native decode may finish in the background.'))
            if self.displayed_path != self.path:
                self.dimension_label.setText(tr('Cancelled'))
                self.histogram.clear()
            elif self.path is None:
                self.stack.setCurrentWidget(self.welcome)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and any(url.isLocalFile() for url in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            if url.isLocalFile():
                self.open_path(Path(url.toLocalFile()))
                event.acceptProposedAction()
                break

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange and hasattr(self, 'maximize_button'):
            self.maximize_button.setIcon(symbol_icon('window-restore' if self.isMaximized() else 'window-maximize'))

    def toggle_maximized(self):
        self.showNormal() if self.isMaximized() else self.showMaximized()

    def rotate_image(self):
        if self.displayed_path == self.path and self.path is not None:
            self.canvas.rotate_clockwise()
            self._status(tr('Display rotated 90°  ·  Original unchanged; use Save As to export'))

    def copy_image(self):
        if self.displayed_path != self.path or self.path is None:
            return
        from .actions import copy_displayed_image
        if copy_displayed_image(self.canvas.displayed_image()):
            self._status(tr('Displayed image copied to clipboard'))

    def save_as(self):
        if self.displayed_path != self.path or self.path is None:
            return
        from .actions import save_displayed_image
        result = save_displayed_image(self, self.canvas.displayed_image(), self.path, self.image_linear)
        if result:
            self._status(tr('Saved displayed image to {name}  ·  Original unchanged', name=result.name))

    def delete_image(self):
        if self.displayed_path != self.path or self.path is None:
            return
        from .actions import recycle_source
        path = self.path
        if not recycle_source(self, path):
            return
        self.cancel_load(silent=True)
        old_index = self.index
        self.files = [item for item in self.files if item != path]
        self.path = self.displayed_path = None
        self.canvas.clear_image()
        self.histogram.clear()
        self.photo_metadata.set_metadata({})
        if self.files:
            self.select_index(min(max(0, old_index), len(self.files) - 1))
        else:
            self.index = -1
            self.filmstrip.clear()
            self.filename.setText('Luma')
            self.dimension_label.setText(tr('No image selected'))
            self.type_label.clear()
            self.details.clear()
            self._set_controls(False)
            self._update_navigation()
            self.stack.setCurrentWidget(self.welcome)
        self._status(tr('Moved {name} to the recycle bin', name=path.name))

    def show_settings(self):
        from .updater import open_update_dialog
        open_update_dialog(self, __version__)

    def show_shortcuts(self):
        QMessageBox.information(self, tr('Luma · Keyboard shortcuts'),
                                tr('Ctrl+S   Save displayed image as…\nCtrl+C   Copy displayed image\nR   Rotate display clockwise\nDelete   Confirm recycle-bin deletion\nCtrl+O   Open image\nCtrl+Shift+O   Open folder\nLeft / Right   Previous / next\nHome / End   First / last\nF   Fit to window\n1   Actual pixels\n+ / − / mouse wheel   Zoom\nDrag   Pan\nDouble-click   Fit / actual pixels\nI   Toggle information\nT   Toggle thumbnails\nF11   Full screen\nEsc   Leave full screen / cancel pending work\nCtrl+0   Reset display\nCtrl+Q   Quit\n\nLuma {version}\nDisplay changes preserve originals. HDR display/export is tone-mapped SDR.', version=__version__))

    def closeEvent(self, event):
        self._closing = True
        self.sidebar.hide()
        self.histogram.clear()
        self.chrome.close()
        self.busy_timer.stop()
        self.prefetch_timer.stop()
        self.render_timer.stop()
        for worker in (self.loader, self.scanner, self.thumbnails):
            worker.close()
        event.accept()


def main(argv=None):
    parser = argparse.ArgumentParser(description=tr('Luma read-only desktop image viewer'))
    parser.add_argument('path', nargs='?', help=tr('Image or folder to open'))
    parser.add_argument('--version', action='version', version=__version__)
    parser.add_argument('--smoke-test', action='store_true', help=tr('Run a bounded native-window/decode check and exit'))
    args = parser.parse_args(argv)
    app = QApplication.instance() or QApplication(sys.argv[:1])
    install_qt_translations(app)
    if args.smoke_test and getattr(sys, 'frozen', False) and not verify_packaged_qt_catalogs():
        return 4  # Missing/corrupt packaged Qt translations must block release QA.
    app.setApplicationName('Luma')
    app.setOrganizationName('Biscuits Lab')
    app.setStyle('Fusion')
    app.setStyleSheet(STYLE)
    window = ViewerWindow()
    if args.path:
        window.prepare_initial_path(Path(args.path))
    if args.smoke_test:
        from .startup_smoke import StartupPaintProbe
        smoke_probe = StartupPaintProbe(window)
    window.show()
    if not args.smoke_test:
        from .shell_settings import start_automatic_registration
        start_automatic_registration(window)
    if args.path:
        QTimer.singleShot(0, lambda: window.open_path(Path(args.path)))
    if args.smoke_test:
        smoke_poll = QTimer(window)
        smoke_poll.setInterval(50)
        def finish_smoke():
            code = None
            if not args.path:
                if smoke_probe.welcome_paints:
                    code = 0
            elif window.displayed_path == Path(args.path).expanduser().absolute():
                # A decoded buffer alone does not prove a clean first frame.
                # The real packaged executable must paint the image without
                # selecting/painting welcome first. Keep the existing timeout.
                if not smoke_probe.initial_page_is_canvas or smoke_probe.welcome_paints:
                    code = 5
                elif smoke_probe.image_paints:
                    code = 0
            elif window.dimension_label.text() == tr('Unable to open'):
                code = 2
            if code is not None:
                smoke_poll.stop()
                window.close()
                app.exit(code)
        smoke_poll.timeout.connect(finish_smoke)
        smoke_poll.start()
        QTimer.singleShot(30000, lambda: app.exit(3))
    return app.exec()
