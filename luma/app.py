from __future__ import annotations

import argparse
import math
from pathlib import Path
import sys

from PySide6.QtCore import Qt, QSize, QTimer, QEvent, QSignalBlocker
from PySide6.QtGui import QIcon, QImage, QKeySequence, QShortcut, QPixmap, QFont, QPainter, QPen, QColor
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QFrame, QLabel, QPushButton,
    QHBoxLayout, QVBoxLayout, QFileDialog, QStackedWidget, QComboBox,
    QSlider, QListWidget, QListWidgetItem, QAbstractItemView, QTextBrowser,
    QSizePolicy, QMessageBox, QMenu,
)

from . import __version__
from .canvas import ImageCanvas
from .chrome import TitleBar, WindowChrome
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
    result.setCheckable(checkable)
    result.setCursor(Qt.CursorShape.PointingHandCursor)
    return result


def symbol_icon(kind):
    canvas = QPixmap(32, 32)
    canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor('#dddddd'), 2.0 if kind in ('rotate', 'delete', 'more') else 1.5))
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
        painter.drawArc(6, 5, 19, 19, 20 * 16, 290 * 16)
        painter.drawLine(24, 6, 25, 14)
        painter.drawLine(25, 14, 17, 12)
    elif kind == 'more':
        painter.setBrush(QColor('#dddddd'))
        painter.setPen(Qt.PenStyle.NoPen)
        for x in (7, 16, 25):
            painter.drawEllipse(x - 1.5, 14.5, 3, 3)
    elif kind == 'delete':
        painter.drawLine(7, 9, 25, 9)
        painter.drawLine(12, 5, 20, 5)
        painter.drawLine(9, 11, 11, 26)
        painter.drawLine(23, 11, 21, 26)
        painter.drawLine(11, 26, 21, 26)
        painter.drawLine(14, 13, 14, 22)
        painter.drawLine(18, 13, 18, 22)
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
    painter.end()
    return QIcon(canvas)


class FooterStatusLabel(QLabel):
    """Keep the footer geometry independent of loading/metadata text length."""
    def __init__(self):
        super().__init__('', objectName='muted')
        self._full_text = ''
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setMaximumWidth(280)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)

    def sizeHint(self):
        return QSize(280, super().sizeHint().height())

    def minimumSizeHint(self):
        return QSize(0, super().minimumSizeHint().height())

    def setText(self, text):
        self._full_text = text
        self.setToolTip(text)
        self._elide()

    def _elide(self):
        super().setText(self.fontMetrics().elidedText(
            self._full_text, Qt.TextElideMode.ElideRight, self.contentsRect().width()))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide()


class ViewerWindow(QMainWindow):
    def __init__(self):
        super().__init__()
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
        self._status('Drop an image to begin  ·  Ctrl+O to open')

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
        self.rotate_button = button('', self.rotate_image, 'Rotate clockwise · R')
        self.rotate_button.setIcon(symbol_icon('rotate'))
        self.rotate_button.setFixedWidth(34)
        left_row.addWidget(self.rotate_button)
        self.delete_button = button('', self.delete_image, 'Move image to recycle bin · Delete')
        self.delete_button.setIcon(symbol_icon('delete'))
        self.delete_button.setFixedWidth(34)
        left_row.addWidget(self.delete_button)
        self.app_menu = QMenu(self)
        self.save_action = self.app_menu.addAction('Save As…    Ctrl+S', self.save_as)
        self.copy_action = self.app_menu.addAction('Copy image    Ctrl+C', self.copy_image)
        self.app_menu.addSeparator()
        self.app_menu.addAction('Open image…    Ctrl+O', self.choose_file)
        self.app_menu.addAction('Open folder…    Ctrl+Shift+O', self.choose_folder)
        self.app_menu.addSeparator()
        self.app_menu.addAction('Settings and updates…', self.show_settings)
        self.app_menu.addAction('Keyboard shortcuts', self.show_shortcuts)
        self.app_menu.addSeparator()
        self.app_menu.addAction('Quit    Ctrl+Q', self.close)
        self.more_button = QPushButton('')
        self.more_button.setIcon(symbol_icon('more'))
        for control in (self.rotate_button, self.delete_button, self.more_button):
            control.setIconSize(QSize(17, 17))
        self.more_button.setMenu(self.app_menu)
        self.more_button.setToolTip('Save As, copy, open and settings')
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
        minimize = button('', self.showMinimized, 'Minimize')
        minimize.setAccessibleName('Minimize window')
        minimize.setFixedWidth(37)
        self.maximize_button = button('', self.toggle_maximized, 'Maximize / restore')
        self.maximize_button.setAccessibleName('Maximize or restore window')
        self.maximize_button.setFixedWidth(37)
        close_button = button('', self.close, 'Close')
        close_button.setAccessibleName('Close window')
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
        self.previous = button('‹', lambda: self.navigate(-1), 'Previous image · Left')
        self.previous.setFixedWidth(29)
        self.next = button('›', lambda: self.navigate(1), 'Next image · Right')
        self.next.setFixedWidth(29)
        self.counter = QLabel('—', objectName='muted')
        self.counter.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.counter.setMinimumWidth(45)
        layout.addWidget(top)

        middle = QHBoxLayout()
        middle.setSpacing(0)
        self.stack = QStackedWidget()
        self.canvas = ImageCanvas()
        self.canvas.zoomChanged.connect(self._zoom_changed)
        self.welcome = QWidget()
        welcome_layout = QVBoxLayout(self.welcome)
        welcome_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        welcome_layout.setSpacing(15)
        logo = QLabel()
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo.setPixmap(QPixmap(str(ASSETS / 'icon.png')).scaled(88, 88, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        welcome_layout.addWidget(logo)
        title = QLabel('Room for the image')
        title.setStyleSheet('font-size: 27px; font-weight: 400; color: #eeeeee;')
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        welcome_layout.addWidget(title)
        subtitle = QLabel('Drop an image or a folder to begin')
        subtitle.setObjectName('muted')
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        welcome_layout.addWidget(subtitle)
        open_btn = button('Open an image', self.choose_file)
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
        self.sidebar = QFrame(objectName='sidebar')
        self.sidebar.setFixedWidth(264)
        side = QVBoxLayout(self.sidebar)
        side.setContentsMargins(21, 24, 21, 16)
        side.setSpacing(12)
        side.addWidget(QLabel('IMAGE', objectName='section'))
        self.dimension_label = QLabel('No image selected')
        self.dimension_label.setStyleSheet('font-size: 19px; font-weight: 400;')
        side.addWidget(self.dimension_label)
        self.type_label = QLabel('')
        self.type_label.setObjectName('muted')
        self.type_label.setWordWrap(True)
        side.addWidget(self.type_label)
        side.addSpacing(14)
        side.addWidget(QLabel('CHANNEL', objectName='section'))
        self.channel = QComboBox()
        self.channel.addItems(['RGBA', 'RGB', 'R', 'G', 'B', 'A'])
        self.channel.currentTextChanged.connect(lambda _: self._schedule_render())
        side.addWidget(self.channel)
        side.addSpacing(10)
        exposure_row = QHBoxLayout()
        self.exposure_heading = QLabel('EXPOSURE', objectName='section')
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
        self.tone.addItems(['Reinhard', 'Clip'])
        self.tone.currentTextChanged.connect(lambda _: self._schedule_render())
        side.addWidget(self.tone)
        self.hdr_note = QLabel('Exposure is available for scene-linear HDR and RAW images. Output is SDR.')
        self.hdr_note.setWordWrap(True)
        self.hdr_note.setObjectName('muted')
        self.hdr_note.setStyleSheet('font-size: 10px; line-height: 150%;')
        side.addWidget(self.hdr_note)
        side.addWidget(button('Reset display', self.reset_display, 'Reset exposure, tone map and channels'))
        side.addSpacing(10)
        side.addWidget(QLabel('BACKGROUND', objectName='section'))
        self.background = QComboBox()
        self.background.addItems(['Graphite', 'Checkerboard', 'Black', 'White'])
        self.background.currentTextChanged.connect(self._background_changed)
        side.addWidget(self.background)
        side.addSpacing(10)
        side.addWidget(QLabel('DETAILS', objectName='section'))
        self.details = QTextBrowser()
        self.details.setOpenExternalLinks(False)
        self.details.setMinimumHeight(50)
        side.addWidget(self.details, 1)
        side.addWidget(button('Keyboard shortcuts', self.show_shortcuts))
        middle.addWidget(self.sidebar)
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
        self.strip_button = button('', self._toggle_strip, 'Show / hide thumbnails · T', True)
        self.strip_button.setIcon(symbol_icon('strip'))
        self.strip_button.setChecked(False)
        self.strip_button.setFixedWidth(35)
        controls.addWidget(self.strip_button)
        self.previous.hide()
        self.counter.hide()
        self.next.hide()
        controls.addSpacing(8)
        self.status_label = FooterStatusLabel()
        controls.addWidget(self.status_label)
        self.info_button = button('ⓘ', self._toggle_info, 'Show / hide image information · I', True)
        self.info_button.setChecked(False)
        self.info_button.setFixedSize(30, 30)
        controls.addWidget(self.info_button)
        controls.addStretch(1)
        self.cancel_button = button('Cancel', self.cancel_load, 'Cancel pending image · Escape')
        cancel_policy = self.cancel_button.sizePolicy()
        cancel_policy.setRetainSizeWhenHidden(True)
        self.cancel_button.setSizePolicy(cancel_policy)
        controls.addWidget(self.cancel_button)
        self.cancel_button.hide()
        self.fit_button = button('', lambda: self.canvas.fit(), 'Fit to window · F')
        self.fit_button.setIcon(symbol_icon('fit'))
        self.fit_button.setFixedWidth(34)
        controls.addWidget(self.fit_button)
        self.zoom_combo = QPushButton('—  ⌄')
        self.zoom_combo.setFixedWidth(82)
        self.zoom_combo.setToolTip('Zoom relative to fit · 100% fits the window')
        self.zoom_combo.setStyleSheet('QPushButton { background: #303030; padding: 5px 8px; } QPushButton::menu-indicator { image: none; width: 0px; }')
        zoom_menu = QMenu(self.zoom_combo)
        for percent in (25, 50, 100, 200, 400, 800):
            zoom_menu.addAction(f'{percent}%', lambda checked=False, value=percent: self._zoom_preset(value))
        zoom_menu.addSeparator()
        zoom_menu.addAction('Actual pixels (1:1)    1', self.canvas.actual_size)
        self.zoom_combo.setMenu(zoom_menu)
        controls.addWidget(self.zoom_combo)
        self.actual_button = button('1:1', lambda: self.canvas.actual_size(), 'Actual pixels · 1')
        self.actual_button.setFixedWidth(35)
        self.actual_button.hide()
        zoom_out = button('', lambda: self.canvas.zoom(0.8), 'Zoom out')
        zoom_out.setIcon(symbol_icon('minus'))
        zoom_out.setFixedWidth(32)
        controls.addWidget(zoom_out)
        self.zoom_slider = QSlider(Qt.Orientation.Horizontal)
        self.zoom_slider.setRange(-153, 120)
        self.zoom_slider.setValue(0)
        self.zoom_slider.setFixedWidth(100)
        self.zoom_slider.setToolTip('Zoom relative to fit · 100% fits the window')
        self.zoom_slider.valueChanged.connect(self._zoom_slider_changed)
        controls.addWidget(self.zoom_slider)
        zoom_in = button('', lambda: self.canvas.zoom(1.25), 'Zoom in')
        zoom_in.setIcon(symbol_icon('plus'))
        zoom_in.setFixedWidth(32)
        controls.addWidget(zoom_in)
        full_button = button('', self.toggle_fullscreen, 'Full screen · F11')
        full_button.setIcon(symbol_icon('full'))
        full_button.setFixedWidth(34)
        controls.addWidget(full_button)
        layout.addWidget(bar)
        self.statusBar().hide()

    def _status(self, text):
        self.status_label.setText(text)
        self.status_label.setToolTip(text)

    def _zoom_changed(self, percent):
        text = (f'{percent:.1f}%' if percent < 1 else f'{percent:.0f}%') + '  ⌄'
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
        name, _ = QFileDialog.getOpenFileName(self, 'Open image', str(self.directory or Path.home()), f'Images ({extensions});;All files (*)')
        if name:
            self.open_path(Path(name))

    def choose_folder(self):
        name = QFileDialog.getExistingDirectory(self, 'Open image folder', str(self.directory or Path.home()))
        if name:
            self.open_path(Path(name))

    def open_path(self, path: Path):
        path = Path(path).expanduser().absolute()
        if not path.exists():
            self._status(f'File not found: {path}')
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
            self.stack.setCurrentWidget(self.welcome)
            self._set_controls(False)
            self.dimension_label.setText('No image selected')
            self.type_label.clear()
            self.details.clear()
            self.filename.setText('Opening folder…')
            self._status('Looking for images…')
            self.cancel_button.show()

    def _scanned(self, token, result):
        if token != self._scan_token or self._closing or result is None:
            return
        directory, files, selected = result
        self.files = files
        if not files:
            self.cancel_button.hide()
            if selected is None:
                self.filename.setText('No images in this folder')
                self._status('No supported images found in this folder')
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
        if token != self._scan_token:
            return
        self.cancel_button.hide()
        self._status(f'Could not read folder: {message}')

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
        self.stack.setCurrentWidget(self.canvas)
        self.dimension_label.setText('Loading…')
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
        if self.path is None:
            return
        self._pending_render = True
        self.busy_timer.start()
        self._load_token = self.loader.submit((self.path, self.exposure.value() / 10, self.channel.currentText(), self.tone.currentText()))

    def _show_loading_indicator(self):
        if self._pending_render and self.path is not None and not self._closing:
            self.cancel_button.show()
            self._status(f'Loading {self.path.name}…')

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
        self._new_image = False
        self.displayed_path = self.path
        self.image_linear = result['linear']
        self._set_controls(True, result['linear'])
        self.dimension_label.setText(f'{w:,} × {h:,}')
        self.type_label.setText(f'{self.path.suffix[1:].upper()}  ·  {"Scene-linear → SDR" if result["linear"] else "SDR preview"}')
        metadata = result['metadata']
        self.details.setPlainText('\n\n'.join(f'{key}\n{value}' for key, value in metadata.items()))
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
            self.loader.submit_idle((self.files[candidate], 0.0, 'RGBA', self.tone.currentText()))

    def _load_failed(self, token, message):
        if token != self._load_token or self._closing:
            return
        self._pending_render = False
        self.busy_timer.stop()
        self.cancel_button.hide()
        self.canvas.clear_image()
        self.displayed_path = None
        self.dimension_label.setText('Unable to open')
        self.details.setPlainText(message)
        self._set_controls(False)
        self._status(f'Could not open {self.path.name if self.path else "image"}: {message}')

    def _set_controls(self, enabled, linear=False, preserve_zoom=False):
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
        self.tone.setCurrentText('Reinhard')

    def _background_changed(self, value):
        self.canvas.background = value
        self.canvas.viewport().update()

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
            self._status('Pending work cancelled. An active native decode may finish in the background.')
            if self.displayed_path != self.path:
                self.dimension_label.setText('Cancelled')

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
            self._status('Display rotated 90°  ·  Original unchanged; use Save As to export')

    def copy_image(self):
        if self.displayed_path != self.path or self.path is None:
            return
        from .actions import copy_displayed_image
        if copy_displayed_image(self.canvas.displayed_image()):
            self._status('Displayed image copied to clipboard')

    def save_as(self):
        if self.displayed_path != self.path or self.path is None:
            return
        from .actions import save_displayed_image
        result = save_displayed_image(self, self.canvas.displayed_image(), self.path, self.image_linear)
        if result:
            self._status(f'Saved displayed image to {result.name}  ·  Original unchanged')

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
        if self.files:
            self.select_index(min(max(0, old_index), len(self.files) - 1))
        else:
            self.index = -1
            self.filmstrip.clear()
            self.filename.setText('Luma')
            self.dimension_label.setText('No image selected')
            self.type_label.clear()
            self.details.clear()
            self._set_controls(False)
            self._update_navigation()
            self.stack.setCurrentWidget(self.welcome)
        self._status(f'Moved {path.name} to the recycle bin')

    def show_settings(self):
        from .updater import open_update_dialog
        open_update_dialog(self, __version__)

    def show_shortcuts(self):
        QMessageBox.information(self, 'Luma · Keyboard shortcuts',
            'Ctrl+S   Save displayed image as…\nCtrl+C   Copy displayed image\nR   Rotate display clockwise\nDelete   Confirm recycle-bin deletion\nCtrl+O   Open image\nCtrl+Shift+O   Open folder\nLeft / Right   Previous / next\nHome / End   First / last\nF   Fit to window\n1   Actual pixels\n+ / − / mouse wheel   Zoom\nDrag   Pan\nDouble-click   Fit / actual pixels\nI   Toggle information\nT   Toggle thumbnails\nF11   Full screen\nEsc   Leave full screen / cancel pending work\nCtrl+0   Reset display\nCtrl+Q   Quit\n\nLuma ' + __version__ + '\nDisplay changes preserve originals. HDR display/export is tone-mapped SDR.')

    def closeEvent(self, event):
        self._closing = True
        self.chrome.close()
        self.busy_timer.stop()
        self.prefetch_timer.stop()
        self.render_timer.stop()
        for worker in (self.loader, self.scanner, self.thumbnails):
            worker.close()
        event.accept()


def main(argv=None):
    parser = argparse.ArgumentParser(description='Luma read-only desktop image viewer')
    parser.add_argument('path', nargs='?', help='Image or folder to open')
    parser.add_argument('--version', action='version', version=__version__)
    parser.add_argument('--smoke-test', action='store_true', help='Run a bounded native-window/decode check and exit')
    args = parser.parse_args(argv)
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName('Luma')
    app.setOrganizationName('Biscuits Lab')
    app.setStyle('Fusion')
    app.setStyleSheet(STYLE)
    window = ViewerWindow()
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
            if not args.path or window.displayed_path == Path(args.path).expanduser().absolute():
                smoke_poll.stop()
                window.close()
                app.exit(0)
            elif window.dimension_label.text() == 'Unable to open':
                smoke_poll.stop()
                window.close()
                app.exit(2)
        smoke_poll.timeout.connect(finish_smoke)
        smoke_poll.start()
        QTimer.singleShot(30000, lambda: app.exit(3))
    return app.exec()
