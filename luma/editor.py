"""Four-tab offline editor for an immutable, already-rendered viewer image.

Integration: the viewer embeds ImageEditorDialog(..., embedded=True) as an image
area page and restores viewing on its finished signal. No editor exec() is used.
Connect exported(str) to show a status message. The viewer image is unchanged.
"""
from __future__ import annotations

from dataclasses import replace
import math
from pathlib import Path

from PIL import Image
from PySide6.QtCore import Qt, Signal, QPointF, QRectF, QSize, QTimer, QSignalBlocker
from PySide6.QtGui import QColor, QImage, QPixmap, QPainter, QPen, QBrush, QKeySequence, QShortcut, QIcon, QPainterPath
from PySide6.QtWidgets import (
    QDialog, QWidget, QPushButton, QHBoxLayout, QVBoxLayout, QGridLayout,
    QLabel, QSlider, QScrollArea, QStackedWidget, QButtonGroup,
    QSizePolicy, QToolButton, QFrame, QLayout,
)

from .editor_core import (
    EditState, Adjustments, EditHistory, Stroke, FILTERS, FULL_CROP,
    MAX_STROKE_POINTS, MAX_STROKES, bounded_size, crop_box, oriented_size,
    transform_point, change_geometry, render,
)
from .editor_worker import EditorRenderer
from .dropdown_widgets import DropdownComboBox
from .i18n import get_language
from .dialogs import PlainMessageBox as QMessageBox

# Kept independent so the editor can be integrated without altering existing catalogs.
_ZH = {
    'Edit image': ('编辑图像', '編輯影像'), 'Crop': ('裁剪', '裁剪'),
    'Adjust': ('调整', '調整'), 'Filters': ('滤镜', '濾鏡'), 'Markup': ('标记', '標記'),
    'Reset': ('重置', '重設'), 'Undo': ('撤销', '復原'), 'Redo': ('重做', '重做'),
    'Zoom in': ('放大', '放大'), 'Zoom out': ('缩小', '縮小'), 'Actual size': ('实际大小', '實際大小'),
    'Crop aspect ratio': ('裁剪宽高比', '裁剪長寬比'), 'Annotation shape': ('标记形状', '標記形狀'),
    'Replace exported file?': ('替换导出的文件？', '取代匯出的檔案？'),
    'An output copy already exists:\n{destination}\n\nReplace this output copy? The original source is protected.': ('输出副本已存在：\n{destination}\n\n要替换此输出副本吗？原始文件受到保护。', '輸出副本已存在：\n{destination}\n\n要取代此輸出副本嗎？原始檔案受到保護。'),
    'Fit': ('适应', '符合'), 'Save As…': ('另存为…', '另存新檔…'), 'Cancel': ('取消', '取消'),
    'Brightness': ('亮度', '亮度'), 'Exposure': ('曝光', '曝光'), 'Contrast': ('对比度', '對比'),
    'Highlights': ('高光', '亮部'), 'Shadows': ('阴影', '陰影'), 'Vignette': ('晕影', '暈影'),
    'Saturation': ('饱和度', '飽和度'), 'Warmth': ('色温', '色溫'), 'Tint': ('色调', '色調'),
    'Clarity': ('清晰度', '清晰度'), 'Light': ('光线', '光線'), 'Color': ('颜色', '顏色'),
    'Straighten': ('校正角度', '校正角度'), 'Free': ('自由', '自由'), 'Original ratio': ('原始比例', '原始比例'),
    'Rotate left': ('向左旋转', '向左旋轉'), 'Rotate right': ('向右旋转', '向右旋轉'),
    'Flip horizontal': ('水平翻转', '水平翻轉'), 'Flip vertical': ('垂直翻转', '垂直翻轉'),
    'Flip H': ('水平翻转', '水平翻轉'), 'Flip V': ('垂直翻转', '垂直翻轉'),
    'Auto enhance': ('自动增强', '自動增強'), 'Strength': ('强度', '強度'),
    'Original': ('原始', '原始'), 'Vivid': ('鲜明', '鮮明'), 'Golden': ('金色', '金色'),
    'Radiant': ('明亮', '明亮'), 'Warm contrast': ('暖对比', '暖對比'), 'Soft': ('柔和', '柔和'),
    'Cool': ('冷色', '冷色'), 'Dramatic': ('戏剧', '戲劇'), 'Muted': ('淡雅', '淡雅'),
    'Mono': ('黑白', '黑白'), 'Silver': ('银色', '銀色'), 'Warm mono': ('暖黑白', '暖黑白'),
    'Noir': ('高对比黑白', '高對比黑白'), 'Vintage': ('复古', '復古'), 'Film': ('胶片', '膠片'),
    'Pen': ('钢笔', '鋼筆'), 'Pencil': ('铅笔', '鉛筆'), 'Highlighter': ('荧光笔', '螢光筆'),
    'Annotation eraser': ('擦除标记', '擦除標記'), 'Clear markup': ('清除标记', '清除標記'),
    'Freehand': ('自由绘制', '自由繪製'), 'Line': ('直线', '直線'), 'Arrow': ('箭头', '箭頭'),
    'Double arrow': ('双向箭头', '雙向箭頭'), 'Thickness': ('粗细', '粗細'),
    'Local editing · 8-bit SDR copy · Original unchanged': ('本地编辑 · 8 位 SDR 副本 · 原图不变', '本機編輯 · 8 位 SDR 副本 · 原圖不變'),
    'HDR / RAW · Editing the tone-mapped 8-bit display, not the original precision': ('HDR / RAW · 编辑的是色调映射后的 8 位显示图像，非原始精度', 'HDR / RAW · 編輯的是色調映射後的 8 位顯示影像，非原始精度'),
    'Rendering…': ('正在渲染…', '正在算繪…'), 'Saving full resolution…': ('正在保存全分辨率图像…', '正在儲存完整解析度影像…'),
    'Saved': ('已保存', '已儲存'), 'Could not render': ('无法渲染', '無法算繪'),
    'Could not save': ('无法保存', '無法儲存'), 'Discard edits?': ('放弃编辑？', '捨棄編輯？'),
    'Your unsaved edits will be discarded. The original file is unchanged.': ('尚未保存的编辑将被放弃，原始文件不会改变。', '未儲存的編輯將被捨棄，原始檔案不會變更。'),
    'Crop handles · Drag inside to move · Middle-drag to pan': ('拖动边框裁剪 · 拖动内部移动 · 中键拖动平移', '拖曳邊框裁剪 · 拖曳內部移動 · 中鍵拖曳平移'),
    'Draw on the image · Eraser removes annotations only': ('在图像上绘制 · 橡皮擦仅移除标记', '在影像上繪製 · 橡皮擦僅移除標記'),
}


def et(text):
    language = get_language()
    pair = _ZH.get(text)
    return pair[1 if language == 'zh-TW' else 0] if pair and language.startswith('zh') else text


STYLE = '''
QDialog#imageEditor { background: #202020; color: #ededed; }
QDialog#imageEditor QWidget { font-size: 12px; color: #ededed; background: transparent; }
QDialog#imageEditor QLabel { background: transparent; }
QDialog#imageEditor QPushButton, QDialog#imageEditor QToolButton {
 background: transparent; border: 1px solid transparent; border-radius: 5px; padding: 7px 10px; }
QDialog#imageEditor QPushButton:hover, QDialog#imageEditor QToolButton:hover { background: #373737; }
QDialog#imageEditor QPushButton:checked, QDialog#imageEditor QToolButton:checked { background: #3b3b3b; border-color: #858585; }
QDialog#imageEditor QPushButton:disabled { color: #686868; }
QDialog#imageEditor QPushButton#editorSave { color: #191919; background: #dedede; font-weight: 600; padding: 8px 18px; }
QDialog#imageEditor QPushButton#editorSave:disabled { background: #666666; }
QDialog#imageEditor QWidget#editorPanel { background: #282828; border-radius: 8px; }
QDialog#imageEditor QLabel#editorMuted { color: #9d9d9d; font-size: 11px; }
QDialog#imageEditor QLabel#editorSection { font-weight: 600; color: #dddddd; padding: 10px 0; }
QDialog#imageEditor QScrollArea { border: 0; background: transparent; }
QDialog#imageEditor QScrollArea > QWidget > QWidget { background: #282828; }
QDialog#imageEditor QComboBox { background: #333333; border: 1px solid #484848; border-radius: 5px; padding: 6px; }
QDialog#imageEditor QSlider::groove:horizontal { height: 3px; background: #505050; border-radius: 1px; }
QDialog#imageEditor QSlider::sub-page:horizontal { background: #a6a6a6; }
QDialog#imageEditor QSlider::handle:horizontal { background: #e6e6e6; width: 12px; margin: -5px 0; border-radius: 6px; }
QDialog#imageEditor QScrollBar:vertical { background: #282828; width: 6px; }
QDialog#imageEditor QScrollBar::handle { background: #626262; min-height: 24px; border-radius: 3px; }
QDialog#imageEditor QScrollBar::add-line, QDialog#imageEditor QScrollBar::sub-line { height: 0; }
'''


def _qimage(image: Image.Image):
    data = image.tobytes('raw', 'RGBA')
    return QImage(data, image.width, image.height, image.width * 4, QImage.Format.Format_RGBA8888).copy()


def editor_icon():
    """A scale-independent pen icon usable beside the viewer's Rotate control."""
    pix = QPixmap(40, 40)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor('#eeeeee'), 2.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    painter.drawLine(QPointF(12, 28), QPointF(28, 12))
    painter.drawLine(QPointF(16, 31), QPointF(31, 16))
    painter.drawLine(QPointF(12, 28), QPointF(10, 34))
    painter.drawLine(QPointF(10, 34), QPointF(16, 31))
    painter.drawLine(QPointF(26, 14), QPointF(29, 17))
    painter.drawLine(QPointF(10, 36), QPointF(32, 36))
    painter.end()
    return QIcon(pix)


class EditorCanvas(QWidget):
    cropChanged = Signal(object, bool)
    strokeAdded = Signal(object)
    eraseAt = Signal(object)
    zoomChanged = Signal(float)

    def __init__(self, source_size, parent=None):
        super().__init__(parent)
        self.setObjectName('editorCanvas')
        self.setMinimumSize(220, 180)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.source_size = source_size
        self.state = EditState()
        self.mode = 'Crop'
        self.image = QImage()
        self.zoom = 1.0
        self.pan = QPointF()
        self.aspect_ratio = None
        self.tool, self.shape = 'pen', 'freehand'
        self.color = (255, 214, 64, 255)
        self.thickness = 5
        self._drag = None
        self._points = []
        tile = QPixmap(20, 20)
        tile.fill(QColor('#333333'))
        p = QPainter(tile)
        p.fillRect(0, 0, 10, 10, QColor('#3c3c3c'))
        p.fillRect(10, 10, 10, 10, QColor('#3c3c3c'))
        p.end()
        self._checker = QBrush(tile)

    def set_image(self, image):
        self.image = image
        self.update()
        self.zoomChanged.emit(self.actual_zoom())

    def set_state(self, state):
        self.state = state
        self.update()

    def display_size(self):
        size = oriented_size(self.source_size, self.state)
        if self.mode != 'Crop':
            l, t, r, b = crop_box(size, self.state.crop)
            return r-l, b-t
        return size

    def image_rect(self):
        if self.image.isNull():
            return QRectF()
        width, height = self.display_size()
        fit = min(max(1, self.width()-72) / width, max(1, self.height()-56) / height)
        w, h = width * fit * self.zoom, height * fit * self.zoom
        return QRectF((self.width()-w)/2 + self.pan.x(), (self.height()-h)/2 + self.pan.y(), w, h)

    def actual_zoom(self):
        return self.image_rect().width() * self.devicePixelRatioF() / max(1, self.display_size()[0]) * 100

    def set_zoom(self, zoom):
        self.zoom = max(.0001, min(128., zoom))
        if self.zoom <= 1:
            self.pan = QPointF()
        self.zoomChanged.emit(self.actual_zoom())
        self.update()

    def fit(self):
        self.pan = QPointF()
        self.set_zoom(1.)

    def actual_size(self):
        self.fit()
        self.set_zoom(100 / max(.01, self.actual_zoom()))

    def _normalized(self, point, clamp=True):
        r = self.image_rect()
        x, y = (point.x()-r.x())/max(1, r.width()), (point.y()-r.y())/max(1, r.height())
        return (max(0., min(1., x)), max(0., min(1., y))) if clamp else (x, y)

    def _source_point(self, point):
        x, y = self._normalized(point)
        l, t, r, b = self.state.crop
        return transform_point((l + x*(r-l), t + y*(b-t)), self.state, self.source_size, inverse=True)

    def source_to_screen(self, point):
        x, y = transform_point(point, self.state, self.source_size)
        if self.mode != 'Crop':
            l, t, r, b = self.state.crop
            x, y = (x-l)/(r-l), (y-t)/(b-t)
        rect = self.image_rect()
        return QPointF(rect.x()+x*rect.width(), rect.y()+y*rect.height())

    def _crop_rect(self):
        r = self.image_rect()
        l, t, right, bottom = self.state.crop
        return QRectF(r.x()+l*r.width(), r.y()+t*r.height(), (right-l)*r.width(), (bottom-t)*r.height())

    def _handles(self):
        r = self._crop_rect()
        return {'nw': r.topLeft(), 'ne': r.topRight(), 'sw': r.bottomLeft(), 'se': r.bottomRight(),
                'n': QPointF(r.center().x(), r.top()), 's': QPointF(r.center().x(), r.bottom()),
                'w': QPointF(r.left(), r.center().y()), 'e': QPointF(r.right(), r.center().y())}

    def _hit(self, point):
        for handle, position in self._handles().items():
            if (point-position).manhattanLength() <= 17:
                return handle
        return 'move' if self._crop_rect().contains(point) else 'new'

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor('#202020'))
        if self.image.isNull():
            return
        rect = self.image_rect()
        p.fillRect(rect, self._checker)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.drawImage(rect, self.image)
        if self.mode == 'Crop':
            crop = self._crop_rect()
            shade = QPainterPath()
            shade.addRect(rect)
            shade.addRect(crop)
            p.fillPath(shade, QColor(0, 0, 0, 145))
            p.setPen(QPen(QColor(255, 255, 255, 70), 1))
            for fraction in (1/3, 2/3):
                p.drawLine(QPointF(crop.left()+crop.width()*fraction, crop.top()), QPointF(crop.left()+crop.width()*fraction, crop.bottom()))
                p.drawLine(QPointF(crop.left(), crop.top()+crop.height()*fraction), QPointF(crop.right(), crop.top()+crop.height()*fraction))
            p.setPen(QPen(QColor('#f4f4f4'), 1.5))
            p.drawRect(crop)
            p.setPen(QPen(QColor('#ffffff'), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.SquareCap))
            for name, point in self._handles().items():
                if len(name) == 1:
                    if name in ('n', 's'):
                        p.drawLine(point-QPointF(10, 0), point+QPointF(10, 0))
                    else:
                        p.drawLine(point-QPointF(0, 10), point+QPointF(0, 10))
                else:
                    dx, dy = (1 if 'w' in name else -1), (1 if 'n' in name else -1)
                    p.drawLine(point, point+QPointF(dx*18, 0))
                    p.drawLine(point, point+QPointF(0, dy*18))
        if self._points:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            color = QColor(*self.color)
            color.setAlpha(82 if self.tool == 'highlighter' else 166 if self.tool == 'pencil' else 255)
            width = self.thickness/1000 * min(self.source_size) * self.image_rect().width() / self.display_size()[0]
            p.setPen(QPen(color, max(1, width), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            points = [self.source_to_screen(point) for point in self._points]
            if self.shape != 'freehand' and len(points) > 1:
                points = [points[0], points[-1]]
            if len(points) == 1:
                p.drawPoint(points[0])
            for a, b in zip(points, points[1:]):
                p.drawLine(a, b)
        p.end()

    def mousePressEvent(self, event):
        if self.image.isNull():
            return
        point = event.position()
        if event.button() == Qt.MouseButton.MiddleButton:
            self._drag = ('pan', point, QPointF(self.pan))
        elif event.button() == Qt.MouseButton.LeftButton and self.image_rect().adjusted(-12, -12, 12, 12).contains(point):
            if self.mode == 'Crop':
                self._drag = (self._hit(point), self._normalized(point), self.state.crop)
            elif self.mode == 'Markup':
                if self.tool == 'eraser':
                    self.eraseAt.emit(point)
                else:
                    self._points = [self._source_point(point)]
                    self._drag = ('stroke',)
            else:
                self._drag = ('pan', point, QPointF(self.pan))
        self.update()

    def mouseMoveEvent(self, event):
        point = event.position()
        if not self._drag:
            if self.mode == 'Crop' and not self.image.isNull():
                handle = self._hit(point)
                cursor = {'nw': Qt.CursorShape.SizeFDiagCursor, 'se': Qt.CursorShape.SizeFDiagCursor,
                          'ne': Qt.CursorShape.SizeBDiagCursor, 'sw': Qt.CursorShape.SizeBDiagCursor,
                          'n': Qt.CursorShape.SizeVerCursor, 's': Qt.CursorShape.SizeVerCursor,
                          'e': Qt.CursorShape.SizeHorCursor, 'w': Qt.CursorShape.SizeHorCursor,
                          'move': Qt.CursorShape.SizeAllCursor}.get(handle, Qt.CursorShape.CrossCursor)
                self.setCursor(cursor)
            else:
                self.setCursor(Qt.CursorShape.CrossCursor if self.mode == 'Markup' else Qt.CursorShape.OpenHandCursor)
            return
        kind = self._drag[0]
        if kind == 'pan':
            self.pan = self._drag[2] + point-self._drag[1]
        elif kind == 'stroke':
            source = self._source_point(point)
            if self.shape == 'freehand':
                if len(self._points) < MAX_STROKE_POINTS and (self.source_to_screen(source)-self.source_to_screen(self._points[-1])).manhattanLength() > 1.5:
                    self._points.append(source)
            else:
                self._points = [self._points[0], source]
        else:
            x, y = self._normalized(point)
            sx, sy = self._drag[1]
            l, t, r, b = self._drag[2]
            minimum_x, minimum_y = 4/self.source_size[0], 4/self.source_size[1]
            if kind == 'move':
                dx, dy = min(1-r, max(-l, x-sx)), min(1-b, max(-t, y-sy))
                l, r, t, b = l+dx, r+dx, t+dy, b+dy
            elif kind == 'new':
                l, r, t, b = min(sx, x), max(sx, x), min(sy, y), max(sy, y)
            else:
                if 'w' in kind: l = min(r-minimum_x, x)
                if 'e' in kind: r = max(l+minimum_x, x)
                if 'n' in kind: t = min(b-minimum_y, y)
                if 's' in kind: b = max(t+minimum_y, y)
            if self.aspect_ratio and kind != 'move':
                w, h = oriented_size(self.source_size, self.state)
                ratio = self.aspect_ratio*h/w
                # Anchor opposite corner, restrict ratio within the existing image.
                cw, ch = max(minimum_x, r-l), max(minimum_y, b-t)
                if cw/ch > ratio: ch = cw/ratio
                else: cw = ch*ratio
                cw, ch = min(cw, 1.), min(ch, 1.)
                if cw/ch > ratio: cw = ch*ratio
                else: ch = cw/ratio
                if 'w' in kind: l = max(0., r-cw); r = l+cw
                else: r = min(1., l+cw); l = r-cw
                if 'n' in kind: t = max(0., b-ch); b = t+ch
                else: b = min(1., t+ch); t = b-ch
            if r-l > 1e-6 and b-t > 1e-6:
                self.cropChanged.emit((max(0., l), max(0., t), min(1., r), min(1., b)), False)
        self.update()

    def mouseReleaseEvent(self, event):
        if self._drag:
            kind = self._drag[0]
            if kind == 'stroke' and self._points:
                self.strokeAdded.emit(Stroke(tuple(self._points), self.color, self.thickness/1000, self.tool, self.shape))
                self._points = []
            elif kind != 'pan':
                self.cropChanged.emit(self.state.crop, True)
            self._drag = None
        self.update()

    def wheelEvent(self, event):
        self.set_zoom(self.zoom * (1.12 if event.angleDelta().y() > 0 else 1/1.12))
        event.accept()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.zoomChanged.emit(self.actual_zoom())


class StraightenSlider(QSlider):
    """Slider with visible degree tick marks, including keyboard adjustment."""
    def __init__(self, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.setRange(-450, 450)
        self.setSingleStep(1)
        self.setPageStep(10)
        self.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.setTickInterval(50)
        self.setMinimumWidth(300)
        self.setMaximumWidth(680)
        self.setFixedHeight(38)
        self.setAccessibleName(et('Straighten'))


    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        for value in range(-450, 451, 10):
            x = 8 + (self.width()-16)*(value+450)/900
            height = 6 if value % 50 == 0 else 2
            painter.setPen(QPen(QColor('#a0a0a0' if value % 50 == 0 else '#606060'), 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawLine(QPointF(x, self.height()-3), QPointF(x, self.height()-3-height))
        painter.end()


class ImageEditorDialog(QDialog):
    exported = Signal(str)

    def __init__(self, source_image: QImage, source_path: Path, parent=None, *, display_referred=False, embedded=False):
        super().__init__(parent)
        self.embedded = embedded
        if embedded:
            self.setWindowFlags(Qt.WindowType.Widget)
            self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        if source_image.isNull():
            raise ValueError('There is no displayed image to edit.')
        from .actions import _HDR_EXTENSIONS
        from .editor_core import check_editor_size
        check_editor_size(source_image.width(), source_image.height())
        self.source_path = Path(source_path)
        self.display_referred = display_referred or self.source_path.suffix.lower() in _HDR_EXTENSIONS
        # Convert bounded strips instead of allocating a full converted QImage,
        # a full Python bytes object, and a Pillow copy at the same time.
        from PIL import Image
        self._source = Image.new('RGBA', (source_image.width(), source_image.height()))
        for top in range(0, source_image.height(), 128):
            band = source_image.copy(0, top, source_image.width(), min(128, source_image.height()-top)).convertToFormat(QImage.Format.Format_RGBA8888)
            tile = Image.frombytes('RGBA', (band.width(), band.height()), bytes(band.constBits()),
                                   'raw', 'RGBA', band.bytesPerLine(), 1)
            self._source.paste(tile, (0, top))
        self.history = EditHistory()
        self._draft = self.history.state
        self._saved_state = self.history.state
        self._saving = False
        self._closed = False
        self._token = 0
        self._ready = False
        self._filter_thumbnails_ready = False
        self.setObjectName('imageEditor')
        self.setWindowTitle(f'{et("Edit image")} · {self.source_path.name}')
        if not embedded:
            self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
            self.resize(1280, 860)
            self.setMinimumSize(900, 620)
        self.setStyleSheet(STYLE)
        self._renderer = EditorRenderer(self._source, self)
        self._renderer.finished.connect(self._rendered)
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(80)
        self._preview_timer.timeout.connect(self._request_preview)
        self._build_ui()
        if embedded:
            self.layout().setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self._connect_shortcuts()
        self.canvas.set_image(source_image.scaled(1600, 1000, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        self._sync()
        self._request_preview()

    @property
    def state(self):
        return self._draft

    def _button(self, text, callback, *, checkable=False, tip=None):
        b = QPushButton(et(text))
        b.setCheckable(checkable)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.clicked.connect(callback)
        b.setAccessibleName(et(text))
        b.setToolTip(et(tip or text))
        return b

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 10)
        layout.setSpacing(10)
        top = QHBoxLayout()
        top.setSpacing(3)
        self.zoom_label = QLabel('100%')
        self.zoom_label.setFixedWidth(48)
        self.zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        for text, callback, tip in (
            ('+', lambda: self.canvas.set_zoom(self.canvas.zoom*1.2), 'Zoom in'),
            ('−', lambda: self.canvas.set_zoom(self.canvas.zoom/1.2), 'Zoom out'),
            ('1:1', lambda: self.canvas.actual_size(), 'Actual size'),
            ('Fit', lambda: self.canvas.fit(), 'Fit')):
            button = self._button(text, callback, tip=tip)
            button.setFixedWidth(38 if text != 'Fit' else 42)
            top.addWidget(button)
        top.addWidget(self.zoom_label)
        self.reset_button = self._button('Reset', self._reset)
        self.undo_button = self._button('↶', self._undo, tip='Undo')
        self.redo_button = self._button('↷', self._redo, tip='Redo')
        top.addWidget(self.reset_button)
        top.addWidget(self.undo_button)
        top.addWidget(self.redo_button)
        top.addStretch(1)
        self.tab_group = QButtonGroup(self)
        self.tab_buttons = []
        for name in ('Crop', 'Adjust', 'Filters', 'Markup'):
            tab = self._button(name, lambda checked=False, n=name: self._tab_changed(n), checkable=True)
            tab.setMinimumWidth(72)
            self.tab_group.addButton(tab)
            self.tab_buttons.append(tab)
            top.addWidget(tab)
        self.tab_buttons[0].setChecked(True)
        top.addStretch(1)
        self._toolbar_balance = QWidget()
        self._toolbar_balance.setFixedWidth(200)
        top.addWidget(self._toolbar_balance)
        self.save_button = self._button('Save As…', self._save_as)
        self.save_button.setObjectName('editorSave')
        top.addWidget(self.save_button)
        top.addWidget(self._button('Cancel', self.reject))
        layout.addLayout(top)
        self.hdr_note = QLabel(et('HDR / RAW · Editing the tone-mapped 8-bit display, not the original precision') if self.display_referred else et('Local editing · 8-bit SDR copy · Original unchanged'))
        self.hdr_note.setObjectName('editorMuted')
        self.hdr_note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hdr_note.setWordWrap(True)
        layout.addWidget(self.hdr_note)
        body = QHBoxLayout()
        body.setSpacing(18)
        self.canvas = EditorCanvas(self._source.size)
        self.canvas.cropChanged.connect(self._crop_changed)
        self.canvas.strokeAdded.connect(self._add_stroke)
        self.canvas.eraseAt.connect(self._erase_at)
        self.canvas.zoomChanged.connect(lambda zoom: self.zoom_label.setText(f'{zoom:.0f}%'))
        body.addWidget(self.canvas, 1)
        self.panels = QStackedWidget(objectName='editorPanel')
        self.panels.setFixedWidth(292)
        self.panels.addWidget(self._adjust_panel())
        self.panels.addWidget(self._filter_panel())
        self.panels.hide()
        body.addWidget(self.panels)
        layout.addLayout(body, 1)
        self.crop_tools = self._crop_tools()
        layout.addWidget(self.crop_tools)
        self.markup_tools = self._markup_tools()
        self.markup_tools.hide()
        layout.addWidget(self.markup_tools)
        self.status = QLabel('')
        self.status.setObjectName('editorMuted')
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status.setMinimumHeight(20)
        layout.addWidget(self.status)

    def _scroll(self, widget):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(widget)
        return scroll

    def _slider_row(self, layout, key, label, minimum=-100, maximum=100):
        header = QHBoxLayout()
        header.addWidget(QLabel(et(label)))
        header.addStretch()
        value = QLabel('0')
        value.setMinimumWidth(38)
        value.setAlignment(Qt.AlignmentFlag.AlignRight)
        header.addWidget(value)
        layout.addLayout(header)
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(minimum, maximum)
        slider.setAccessibleName(et(label))
        slider.setObjectName('editor_'+key)
        slider.setMinimumHeight(25)
        slider.valueChanged.connect(lambda n, k=key, v=value, s=slider: self._slider_changed(k, n, v, s))
        slider.sliderReleased.connect(self._commit_draft)
        layout.addWidget(slider)
        return slider, value

    def _adjust_panel(self):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(22, 14, 22, 20)
        layout.setSpacing(9)
        self.adjust_sliders = {}
        for section, keys in (('Light', ('brightness', 'exposure', 'contrast', 'highlights', 'shadows', 'vignette')),
                              ('Color', ('saturation', 'warmth', 'tint', 'clarity'))):
            label = QLabel(et(section))
            label.setObjectName('editorSection')
            layout.addWidget(label)
            for key in keys:
                slider, value = self._slider_row(layout, key, key.title(), -40 if key == 'exposure' else -100, 40 if key == 'exposure' else 100)
                self.adjust_sliders[key] = slider, value
        layout.addStretch()
        return self._scroll(panel)

    def _filter_panel(self):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(15, 18, 15, 18)
        self.auto_button = self._button('Auto enhance', self._auto_enhance, checkable=True)
        self.auto_button.setMinimumHeight(42)
        layout.addWidget(self.auto_button)
        layout.addSpacing(10)
        grid = QGridLayout()
        grid.setSpacing(7)
        self.filter_buttons = {}
        self.filter_group = QButtonGroup(self)
        for index, (key, (name, _, __)) in enumerate(FILTERS.items()):
            button = QToolButton()
            button.setText(et(name))
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            button.setCheckable(True)
            button.setIconSize(QSize(66, 66))
            button.setMinimumSize(77, 98)
            button.setMaximumWidth(84)
            button.setStyleSheet('QToolButton { padding: 3px; font-size: 10px; }')
            button.setAccessibleName(et(name))
            button.clicked.connect(lambda checked=False, k=key: self._set_filter(k))
            self.filter_group.addButton(button)
            self.filter_buttons[key] = button
            grid.addWidget(button, index//3, index%3)
        layout.addLayout(grid)
        layout.addSpacing(12)
        self.strength_slider, self.strength_label = self._slider_row(layout, 'filter_strength', 'Strength', 0, 100)
        with QSignalBlocker(self.strength_slider): self.strength_slider.setValue(100)
        layout.addStretch()
        return self._scroll(panel)

    def _crop_tools(self):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        title = QHBoxLayout()
        title.addStretch()
        title.addWidget(QLabel(et('Straighten')))
        self.straighten_label = QLabel('0.0°')
        self.straighten_label.setFixedWidth(46)
        title.addWidget(self.straighten_label)
        title.addStretch()
        layout.addLayout(title)
        ruler = QHBoxLayout()
        ruler.addStretch()
        self.straighten_slider = StraightenSlider()
        self.straighten_slider.valueChanged.connect(lambda n: self._slider_changed('straighten', n, self.straighten_label, self.straighten_slider))
        self.straighten_slider.sliderReleased.connect(self._commit_draft)
        ruler.addWidget(self.straighten_slider, 1)
        ruler.addStretch()
        layout.addLayout(ruler)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(self._button('↶ 90°', lambda: self._geometry(turns=(self.state.turns-1)%4), tip='Rotate left'))
        row.addWidget(self._button('↷ 90°', lambda: self._geometry(turns=(self.state.turns+1)%4), tip='Rotate right'))
        row.addSpacing(30)
        self.aspect = DropdownComboBox()
        self.aspect.setAccessibleName(et('Crop aspect ratio'))
        for label, value in (('Free', None), ('Original ratio', 'original'), ('1:1', 1.), ('4:3', 4/3), ('3:2', 1.5), ('16:9', 16/9), ('9:16', 9/16)):
            self.aspect.addItem(et(label), value)
        self.aspect.setMinimumWidth(140)
        self.aspect.currentIndexChanged.connect(self._aspect_changed)
        row.addWidget(self.aspect)
        row.addSpacing(30)
        row.addWidget(self._button('Flip H', lambda: self._geometry(flip_x=not self.state.flip_x), tip='Flip horizontal'))
        row.addWidget(self._button('Flip V', lambda: self._geometry(flip_y=not self.state.flip_y), tip='Flip vertical'))
        row.addStretch()
        layout.addLayout(row)
        return panel

    def _markup_tools(self):
        panel = QWidget()
        panel.setObjectName('editorMarkupTools')
        outer = QVBoxLayout(panel)
        outer.setContentsMargins(0, 2, 0, 0)
        row = QHBoxLayout()
        row.addStretch()
        self.markup_group = QButtonGroup(self)
        for key, text in (('pen', 'Pen'), ('pencil', 'Pencil'), ('highlighter', 'Highlighter'), ('eraser', 'Annotation eraser')):
            b = self._button(text, lambda checked=False, k=key: self._choose_tool(k), checkable=True)
            self.markup_group.addButton(b)
            if key == 'pen': b.setChecked(True)
            row.addWidget(b)
        row.addWidget(self._button('Clear markup', lambda: self._commit(replace(self.state, strokes=()), 'Clear markup')))
        row.addSpacing(12)
        self.shape_combo = DropdownComboBox()
        for key, text in (('freehand', 'Freehand'), ('line', 'Line'), ('arrow', 'Arrow'), ('double_arrow', 'Double arrow')):
            self.shape_combo.addItem(et(text), key)
        self.shape_combo.setAccessibleName(et('Annotation shape'))
        self.shape_combo.currentIndexChanged.connect(lambda: setattr(self.canvas, 'shape', self.shape_combo.currentData()))
        row.addWidget(self.shape_combo)
        row.addStretch()
        outer.addLayout(row)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(QLabel(et('Thickness')))
        self.thickness = QSlider(Qt.Orientation.Horizontal)
        self.thickness.setRange(1, 35)
        self.thickness.setValue(5)
        self.thickness.setFixedWidth(145)
        self.thickness.setAccessibleName(et('Thickness'))
        self.thickness.valueChanged.connect(lambda n: setattr(self.canvas, 'thickness', n))
        row.addWidget(self.thickness)
        row.addSpacing(16)
        self.color_group = QButtonGroup(self)
        for hex_color in ('#ffd640', '#ffa718', '#fa6424', '#e72b41', '#673ba4', '#bd4cc9', '#d71e82', '#42c5ec', '#1983d7', '#204aab', '#7fba15', '#00ae6c', '#ffffff', '#b5b5b5', '#262626'):
            b = QPushButton('')
            b.setFixedSize(23, 23)
            b.setCheckable(True)
            b.setAccessibleName(hex_color)
            b.setToolTip(hex_color)
            b.setStyleSheet(f'QPushButton {{ background: {hex_color}; border: 2px solid #555555; border-radius: 11px; padding: 0; }} QPushButton:checked {{ border: 3px solid #ffffff; }}')
            b.clicked.connect(lambda checked=False, c=hex_color: setattr(self.canvas, 'color', QColor(c).getRgb()))
            self.color_group.addButton(b)
            if hex_color == '#ffd640': b.setChecked(True)
            row.addWidget(b)
        row.addStretch()
        outer.addLayout(row)
        return panel

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, '_toolbar_balance'):
            self._toolbar_balance.setFixedWidth(max(0, min(200, self.width()-1050)))

    def _connect_shortcuts(self):
        for sequence, callback in (('Ctrl+Z', self._undo), ('Ctrl+Y', self._redo), ('Ctrl+Shift+Z', self._redo),
                                   ('Ctrl+Shift+S', self._save_as), ('Ctrl+0', self.canvas.fit)):
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.activated.connect(callback)
        if self.embedded:
            QShortcut(QKeySequence('Escape'), self).activated.connect(self.reject)
            QShortcut(QKeySequence('Ctrl+Q'), self).activated.connect(self.window().close)

    def _tab_changed(self, name):
        self.tab_buttons[('Crop', 'Adjust', 'Filters', 'Markup').index(name)].setChecked(True)
        self._commit_draft()
        self.canvas.mode = name
        self.canvas.fit()
        self.panels.setVisible(name in ('Adjust', 'Filters'))
        self.panels.setCurrentIndex(0 if name == 'Adjust' else 1)
        self.crop_tools.setVisible(name == 'Crop')
        self.markup_tools.setVisible(name == 'Markup')
        if name == 'Filters' and not self._filter_thumbnails_ready:
            self._build_filter_thumbnails()
        self._request_preview()

    def _build_filter_thumbnails(self):
        # A total of 15 × 72px local thumbnails; no full-resolution filter passes.
        tiny = self._source.resize(bounded_size(self._source.size, 72, 72*72), Image.Resampling.LANCZOS)
        for name, button in self.filter_buttons.items():
            image = render(tiny, EditState(filter_name=name), preview=True)
            button.setIcon(QIcon(QPixmap.fromImage(_qimage(image))))
        self._filter_thumbnails_ready = True

    def _sync(self):
        state = self.state
        self.canvas.set_state(state)
        self.undo_button.setEnabled(bool(self.history.undo_commands) and not self._saving)
        self.redo_button.setEnabled(bool(self.history.redo_commands) and not self._saving)
        self.reset_button.setEnabled(state != EditState() and not self._saving)
        for key, (slider, label) in self.adjust_sliders.items():
            value = getattr(state.adjustments, key)
            with QSignalBlocker(slider): slider.setValue(round(value*10 if key == 'exposure' else value))
            label.setText(f'{value:+.1f} EV' if key == 'exposure' else f'{value:.0f}')
        with QSignalBlocker(self.straighten_slider): self.straighten_slider.setValue(round(state.straighten*10))
        self.straighten_label.setText(f'{state.straighten:+.1f}°')
        with QSignalBlocker(self.strength_slider): self.strength_slider.setValue(round(state.filter_strength*100))
        self.strength_label.setText(f'{state.filter_strength*100:.0f}')
        self.auto_button.setChecked(state.auto_enhance)
        self.filter_buttons[state.filter_name].setChecked(True)
        self.strength_slider.setEnabled(state.filter_name != 'original')

    def _commit(self, state, label='Edit'):
        if self._saving: return
        try:
            self.history.commit(state, label)
        except ValueError as error:
            QMessageBox.warning(self, et('Edit image'), str(error))
            self._draft = self.history.state
            self._sync()
            return
        self._draft = self.history.state
        self._sync()
        self._queue_preview()

    def _commit_draft(self):
        self._commit(self._draft)

    def _slider_changed(self, key, n, label, slider):
        if self._saving: return
        if key == 'straighten':
            self._draft = replace(self.state, straighten=n/10)
            label.setText(f'{n/10:+.1f}°')
        elif key == 'filter_strength':
            self._draft = replace(self.state, filter_strength=n/100)
            label.setText(str(n))
        else:
            value = n/10 if key == 'exposure' else n
            self._draft = replace(self.state, adjustments=replace(self.state.adjustments, **{key: value}))
            label.setText(f'{value:+.1f} EV' if key == 'exposure' else str(n))
        self.canvas.set_state(self.state)
        self._queue_preview()
        if not slider.isSliderDown():
            self._commit_draft()

    def _crop_changed(self, crop, final):
        self._draft = replace(self.state, crop=crop)
        self.canvas.set_state(self.state)
        if final: self._commit_draft()

    def _geometry(self, **changes):
        self._commit(change_geometry(self.state, self._source.size, **changes), 'Geometry')
        if self.aspect.currentData() == 'original': self._aspect_changed()

    def _aspect_changed(self):
        ratio = self.aspect.currentData()
        w, h = oriented_size(self._source.size, self.state)
        if ratio == 'original': ratio = w/h
        self.canvas.aspect_ratio = ratio
        if ratio is None: return
        normalized = ratio*h/w
        l, t, r, b = self.state.crop
        cw, ch = r-l, b-t
        if cw/ch > normalized: cw = ch*normalized
        else: ch = cw/normalized
        cx, cy = (l+r)/2, (t+b)/2
        self._commit(replace(self.state, crop=(cx-cw/2, cy-ch/2, cx+cw/2, cy+ch/2)), 'Aspect ratio')

    def _set_filter(self, key):
        self._commit(replace(self.state, filter_name=key), 'Filter')

    def _auto_enhance(self, checked):
        self._commit(replace(self.state, auto_enhance=checked), 'Auto enhance')

    def _choose_tool(self, key):
        self.canvas.tool = key
        self.shape_combo.setEnabled(key != 'eraser')
        self.thickness.setEnabled(key != 'eraser')
        if key == 'highlighter' and self.thickness.value() < 12:
            self.thickness.setValue(16)
        elif key in ('pen', 'pencil') and self.thickness.value() > 12:
            self.thickness.setValue(5)

    def _add_stroke(self, stroke):
        self._commit(replace(self.state, strokes=self.state.strokes+(stroke,)), 'Markup')

    def _erase_at(self, point):
        # Hit-test complete segments, so long straight lines erase anywhere.
        for index in range(len(self.state.strokes)-1, -1, -1):
            stroke = self.state.strokes[index]
            points = [self.canvas.source_to_screen(p) for p in stroke.points]
            if len(points) == 1: points *= 2
            radius = max(10., stroke.width*min(self._source.size)*self.canvas.actual_zoom()/self.canvas.devicePixelRatioF()/100/2+4)
            for start, end in zip(points, points[1:]):
                dx, dy = end.x()-start.x(), end.y()-start.y()
                length = dx*dx+dy*dy
                fraction = max(0., min(1., ((point.x()-start.x())*dx+(point.y()-start.y())*dy)/length)) if length else 0
                distance = math.hypot(point.x()-start.x()-fraction*dx, point.y()-start.y()-fraction*dy)
                if distance <= radius:
                    strokes = self.state.strokes[:index]+self.state.strokes[index+1:]
                    self._commit(replace(self.state, strokes=strokes), 'Erase annotation')
                    return

    def _undo(self):
        if self._saving: return
        self._draft = self.history.undo()
        self._sync()
        self._request_preview()

    def _redo(self):
        if self._saving: return
        self._draft = self.history.redo()
        self._sync()
        self._request_preview()

    def _reset(self):
        self._commit(EditState(), 'Reset')
        with QSignalBlocker(self.aspect): self.aspect.setCurrentIndex(0)
        self.canvas.aspect_ratio = None
        self.canvas.fit()

    def _queue_preview(self):
        # Throttle rather than debounce: continuous slider movement still shows
        # bounded coarse frames, then a sharper preview after release.
        if not self._preview_timer.isActive(): self._preview_timer.start()

    def _request_preview(self):
        if self._saving or self._closed: return
        self._preview_timer.stop()
        self._ready = False
        self.status.setText(et('Rendering…'))
        self._requested_state = (self.state, self.canvas.mode)
        interactive = self.straighten_slider.isSliderDown() or self.strength_slider.isSliderDown() or any(slider.isSliderDown() for slider, _ in self.adjust_sliders.values())
        self._token = self._renderer.request(self.state, include_crop=self.canvas.mode != 'Crop', interactive=interactive)

    def _rendered(self, token, result, error):
        if token != self._token or self._closed: return
        if self._saving:
            self._saving = False
            self.setEnabled(True)
            self._sync()
            if error:
                QMessageBox.warning(self, et('Could not save'), error)
                self.status.setText(et('Could not save'))
            else:
                self._saved_state = self.state
                self.exported.emit(str(result))
                self.status.setText(f'{et("Saved")} · {Path(result).name}')
                if self.embedded:
                    self.accept()
            return
        if self._requested_state[1] != self.canvas.mode:
            return
        if error:
            self._ready = False
            self.status.setText(f'{et("Could not render")} · {error}')
            return
        self._ready = True
        self.canvas.set_image(_qimage(result))
        size = oriented_size(self._source.size, self.state)
        l, t, r, b = crop_box(size, self.state.crop)
        hint = 'Crop handles · Drag inside to move · Middle-drag to pan' if self.canvas.mode == 'Crop' else 'Draw on the image · Eraser removes annotations only' if self.canvas.mode == 'Markup' else ''
        self.status.setText(f'{r-l:,} × {b-t:,}  ·  {et(hint)}' if hint else f'{r-l:,} × {b-t:,}  ·  8-bit SDR')

    def _save_as(self):
        if self._saving or self._closed: return
        self._commit_draft()
        self._preview_timer.stop()
        from .actions import ExportDialog, validated_destination, _file_signature
        dialog = ExportDialog(self, self.source_path, self.display_referred)
        dialog.filename.setText(str(self.source_path.with_name(self.source_path.stem+'-edited')))
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.choice is None:
            return
        choice = dialog.choice
        try:
            destination = validated_destination(choice.path, self.source_path, choice.format)
            expected = _file_signature(destination)
            if expected is not None:
                answer = QMessageBox.question(self, et('Replace exported file?'),
                    et('An output copy already exists:\n{destination}\n\nReplace this output copy? The original source is protected.').format(destination=destination),
                    QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Cancel)
                if answer != QMessageBox.StandardButton.Save: return
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, et('Could not save'), str(error))
            return
        self._saving = True
        self.status.setText(et('Saving full resolution…'))
        self.setEnabled(False)
        self._token = self._renderer.request(self.state, export=(destination, self.source_path, choice.format, expected))

    def reject(self):
        if self._saving: return
        if self.state != self._saved_state:
            answer = QMessageBox.question(self, et('Discard edits?'),
                et('Your unsaved edits will be discarded. The original file is unchanged.'),
                QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel)
            if answer != QMessageBox.StandardButton.Discard: return
        self._shutdown()
        super().reject()

    def _shutdown(self):
        if self._closed: return
        self._closed = True
        self._preview_timer.stop()
        self._renderer.close()
        # Parent-owned dialogs may live until the viewer closes; release large
        # detached snapshots immediately rather than retaining every edit session.
        self._source = None
        self.canvas.set_image(QImage())

    def done(self, result):
        self._shutdown()
        super().done(result)

    def closeEvent(self, event):
        event.ignore()
        self.reject()
