"""Rounded, translucent QMenus with vector icons and a short accessible fade.

QMenu continues to own action layout, shortcuts, popup placement, focus,
accessibility and outside-click dismissal. Only the surface is custom painted.
No native window-opacity assumption is required: the Qt graphics effect also
renders correctly on the offscreen platform used by the local preview.
"""
from __future__ import annotations

import ctypes
import math
import sys

from PySide6.QtCore import QEasingCurve, QEvent, QPropertyAnimation, QRectF, QPoint, QPointF, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QIcon, QIconEngine, QKeySequence, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QGraphicsOpacityEffect, QHBoxLayout, QMenu, QStyle, QToolButton, QWidget, QWidgetAction

from .i18n import get_language


SHADOW_MARGIN = 10  # logical pixels included in native popup geometry


MENU_STYLE = """
QMenu#luma_popup { background: transparent; border: 0; padding: 18px; menu-scrollable: 1; color: #eeeeee; font-size: 13px; icon-size: 20px; }
QMenu#luma_popup::item { background: transparent; padding: 9px 16px 9px 12px; border-radius: 5px; }
QMenu#luma_popup::item:selected { background: #404040; }
QMenu#luma_popup::item:disabled { color: #777777; }
QMenu#luma_popup::separator { height: 1px; background: #3c3c3c; margin: 5px 2px; }
QMenu#luma_popup QWidget#menu_action_strip { background: transparent; }
QMenu#luma_popup QToolButton { background: transparent; border: 1px solid transparent; border-radius: 5px; padding: 6px 10px; color: #eeeeee; font-size: 12px; }
QMenu#luma_popup QToolButton:hover, QMenu#luma_popup QToolButton:focus { background: #404040; border-color: #5b5b5b; }
QMenu#luma_popup QToolButton:pressed { background: #4b4b4b; }
QMenu#luma_popup QToolButton:disabled { color: #777777; }
"""


def menu_font_families():
    if get_language() == 'en':
        return ('Segoe UI', 'Inter', 'sans-serif')
    region = 'TC' if get_language() == 'zh-TW' else 'SC'
    return ('Microsoft YaHei', 'Microsoft YaHei UI', f'Noto Sans CJK {region}',
            f'Noto Sans {region}', 'Segoe UI', 'sans-serif')


def _windows_animations_enabled():
    """Read master, menu and accessibility animation settings on every open.

    https://learn.microsoft.com/windows/win32/api/winuser/nf-winuser-systemparametersinfow
    BOOL remains 32 bits on Win64. Unknown/failed settings conservatively skip
    animation; these calls never change a user's Windows preferences.
    """
    try:
        query = ctypes.WinDLL('user32', use_last_error=True).SystemParametersInfoW
        query.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32]
        query.restype = ctypes.c_int32
        for flag in (0x103E, 0x1002, 0x1042):  # UI effects, menu, client animation
            enabled = ctypes.c_int32()
            if not query(flag, 0, ctypes.byref(enabled), 0) or not enabled.value:
                return False
        return True
    except (AttributeError, OSError):
        return False


def menu_animations_enabled(widget):
    if sys.platform == 'win32' and not _windows_animations_enabled():
        return False
    return widget.style().styleHint(QStyle.StyleHint.SH_Widget_Animation_Duration, None, widget) > 0


def _paint_icon(painter, kind):
    pen = QPen(QColor('#eeeeee'), 1.6)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    if kind == 'save':
        path = QPainterPath(QPointF(5, 3))
        for x, y in ((18, 3), (21, 6), (21, 20), (3, 20), (3, 5), (5, 3)):
            path.lineTo(x, y)
        painter.drawPath(path)
        painter.drawRoundedRect(QRectF(7, 3, 9, 6), 1, 1)
        painter.drawRoundedRect(QRectF(7, 13, 10, 7), 1, 1)
    elif kind == 'copy':
        painter.drawRoundedRect(QRectF(9, 3, 10, 14), 2, 2)
        path = QPainterPath(QPointF(6, 7))
        path.lineTo(5, 7); path.quadTo(3, 7, 3, 9); path.lineTo(3, 19)
        path.quadTo(3, 21, 5, 21); path.lineTo(13, 21); path.quadTo(15, 21, 15, 19)
        painter.drawPath(path)
    elif kind == 'image':
        painter.drawRoundedRect(QRectF(3, 3, 18, 18), 2, 2)
        painter.drawEllipse(QRectF(7, 6, 3, 3))
        path = QPainterPath(QPointF(3, 17)); path.lineTo(9, 12); path.lineTo(13, 16); path.lineTo(17, 12); path.lineTo(21, 16)
        painter.drawPath(path)
    elif kind == 'folder':
        path = QPainterPath(QPointF(3, 7)); path.lineTo(3, 5); path.quadTo(3, 3, 5, 3)
        path.lineTo(10, 3); path.lineTo(12, 6); path.lineTo(19, 6); path.quadTo(21, 6, 21, 8)
        path.lineTo(21, 19); path.lineTo(3, 19); path.closeSubpath()
        painter.drawPath(path); painter.drawLine(3, 9, 21, 9)
    elif kind == 'settings':
        path = QPainterPath()
        for index in range(48):
            angle = math.pi * 2 * index / 48 - math.pi / 2
            radius = 10 if index % 6 in (1, 2, 3) else 8
            point = QPointF(12 + radius * math.cos(angle), 12 + radius * math.sin(angle))
            path.moveTo(point) if index == 0 else path.lineTo(point)
        path.closeSubpath(); painter.drawPath(path)
        painter.drawEllipse(QRectF(8.5, 8.5, 7, 7))
    elif kind == 'keyboard':
        painter.drawRoundedRect(QRectF(2, 5, 20, 14), 2, 2)
        for y in (9, 12):
            for x in (6, 10, 14, 18):
                painter.drawPoint(QPointF(x, y))
        painter.drawLine(7, 16, 17, 16)
    elif kind == 'quit':
        path = QPainterPath(QPointF(11, 3)); path.lineTo(4, 3); path.lineTo(4, 21); path.lineTo(11, 21)
        painter.drawPath(path); painter.drawLine(10, 12, 21, 12)
        path = QPainterPath(QPointF(17, 8)); path.lineTo(21, 12); path.lineTo(17, 16); painter.drawPath(path)
    elif kind == 'rotate':
        path = QPainterPath(); path.arcMoveTo(QRectF(3, 3, 17, 17), 180); path.arcTo(QRectF(3, 3, 17, 17), 180, -260)
        painter.drawPath(path); painter.drawEllipse(QRectF(10, 10, 3, 3))
        path = QPainterPath(QPointF(15, 16)); path.lineTo(15, 21); path.lineTo(20, 21); painter.drawPath(path)
    elif kind == 'delete':
        painter.drawLine(3, 6, 21, 6); painter.drawLine(9, 3, 15, 3)
        path = QPainterPath(QPointF(5, 8)); path.lineTo(7, 21); path.lineTo(17, 21); path.lineTo(19, 8); painter.drawPath(path)
        painter.drawLine(10, 10, 10, 17); painter.drawLine(14, 10, 14, 17)
    elif kind == 'path':
        painter.drawRoundedRect(QRectF(2.5, 5, 19, 14), 2, 2)
        painter.drawLine(7, 8, 10, 16); painter.drawLine(11, 8, 14, 16)
        for x in (17, 19):
            painter.drawPoint(QPointF(x, 16))
    else:
        raise ValueError(f'Unknown menu icon: {kind}')


class _MenuIconEngine(QIconEngine):
    def __init__(self, kind):
        super().__init__()
        self.kind = kind

    def clone(self):
        return _MenuIconEngine(self.kind)

    def paint(self, painter, rect, mode, state):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.translate(rect.x(), rect.y())
        painter.scale(rect.width() / 24, rect.height() / 24)
        if mode == QIcon.Mode.Disabled:
            painter.setOpacity(painter.opacity() * .38)
        _paint_icon(painter, self.kind)
        painter.restore()

    def pixmap(self, size, mode, state):
        pixmap = QPixmap(size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        self.paint(painter, pixmap.rect(), mode, state)
        painter.end()
        return pixmap


def menu_icon(kind):
    return QIcon(_MenuIconEngine(kind))


def clamp_popup_position(position, size, work_area):
    """Keep the complete popup (including its alpha shadow) in logical bounds.

    Do not scale these QScreen/QWidget coordinates by devicePixelRatio. A
    monitor may have negative coordinates or reserved bars on any edge. Native
    QMenu remains responsible for row layout/scrolling; never resize it here.
    """
    if work_area.isEmpty():
        return QPoint(position)
    rightmost = max(work_area.left(), work_area.right() - size.width() + 1)
    bottommost = max(work_area.top(), work_area.bottom() - size.height() + 1)
    return QPoint(max(work_area.left(), min(position.x(), rightmost)),
                  max(work_area.top(), min(position.y(), bottommost)))


class RoundedMenu(QMenu):
    """A real QMenu: translucent corners, native rows, 140ms paint-level fade."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('luma_popup')
        # QWidget requires BOTH flags before creation for alpha on Windows.
        # https://doc.qt.io/qt-6/qwidget.html#creating-translucent-windows
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAutoFillBackground(False)
        families = ', '.join(f"'{name}'" for name in menu_font_families())
        self.setStyleSheet(MENU_STYLE + '\nQMenu#luma_popup, QMenu#luma_popup QToolButton { font-family: ' + families + '; }')
        self.setMinimumWidth(246 + 2 * SHADOW_MARGIN)
        self.fade_effect = QGraphicsOpacityEffect(self)
        self.fade_effect.setOpacity(1)
        self.setGraphicsEffect(self.fade_effect)
        self.open_animation = QPropertyAnimation(self.fade_effect, b'opacity', self)
        self.open_animation.setDuration(140)
        self.open_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.open_animation.setStartValue(0.)
        self.open_animation.setEndValue(1.)
        self._adjusting_bounds = False
        self._scroll_layout_height = 0
        self.aboutToShow.connect(self._prepare_work_area)
        self._bounds_timer = QTimer(self)
        self._bounds_timer.setSingleShot(True)
        self._bounds_timer.timeout.connect(self._keep_in_work_area)

    def add_icon_action(self, text, kind, callback):
        # Keep translated keys and existing QShortcuts, but give Qt a real
        # shortcut column instead of locale/font-dependent padding spaces.
        action = self.addAction(menu_icon(kind), text.replace('    ', '\t'), callback)
        action.setIconVisibleInMenu(True)
        if "\t" in action.text():
            action.setProperty("luma_menu_shortcut", action.text().rsplit("\t", 1)[1])
        return action

    def _activate_shortcut(self, event):
        # The viewer owns its global QShortcuts. While a popup grabs keyboard
        # input, route its displayed equivalents here, without registering a
        # second WindowShortcut that would make the viewer's binding ambiguous.
        sequence = QKeySequence(event.keyCombination())
        for action in [*self.actions(), *getattr(self, 'image_actions', [])]:
            shortcut = action.property('luma_menu_shortcut')
            if action.isEnabled() and shortcut and sequence == QKeySequence(shortcut):
                self.close()
                action.trigger()
                event.accept()
                return True
        return False

    def keyPressEvent(self, event):
        if not self._activate_shortcut(event):
            super().keyPressEvent(event)

    def panel_rect(self):
        margin = SHADOW_MARGIN + .5
        return QRectF(self.rect()).adjusted(margin, margin, -margin, -margin)

    def _panel_path(self):
        path = QPainterPath()
        path.addRoundedRect(self.panel_rect(), 11, 11)
        return path

    def mousePressEvent(self, event):
        # The transparent shadow gutter is outside the interactive menu surface.
        if not self._panel_path().contains(event.position()):
            self.close()
            event.accept()
            return
        super().mousePressEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        panel = self.panel_rect()
        # Paint the soft exterior shadow in our own alpha gutter. The existing
        # opacity effect then fades panel, shadow and child buttons together;
        # a second QGraphicsEffect would replace that fade. No native shadow is
        # enabled, and even the outer blur + 2px offset fit inside the popup.
        painter.setPen(Qt.PenStyle.NoPen)
        shadow = panel.translated(0, 2)
        for spread, alpha in ((8, 1), (7, 2), (6, 3), (5, 4), (4, 5),
                              (3, 6), (2, 8), (1, 10), (0, 12)):
            painter.setBrush(QColor(0, 0, 0, alpha))
            painter.drawRoundedRect(shadow.adjusted(-spread, -spread, spread, spread),
                                    11 + spread, 11 + spread)
        painter.setPen(QPen(QColor('#171717'), 1))
        painter.setBrush(QColor('#2b2b2b'))
        painter.drawRoundedRect(panel, 11, 11)
        painter.end()
        # Native QMenu paints all action rows, scroll indicators, selection and
        # checkmarks on our transparent style surface. Do not replace this.
        super().paintEvent(event)

    def _prepare_work_area(self):
        # Qt selects the popup monitor before aboutToShow, including native
        # tool-button/exec() paths. A height cap alone clips rows: QMenu only
        # initializes its native scroll flags if its layout exceeds the screen
        # geometry (which on Windows includes the taskbar). Request that native
        # overflow layout only when content exceeds the usable work area; the
        # actual widget is capped to the work area, including its shadow.
        self._scroll_layout_height = 0
        screen = self.screen()
        if screen is None or screen.availableGeometry().isEmpty():
            self.setMaximumHeight(16777215)  # QWIDGETSIZE_MAX
            return
        available_height = screen.availableGeometry().height()
        self.setMaximumHeight(available_height)
        if super().sizeHint().height() > available_height:
            self._scroll_layout_height = screen.geometry().height() + 1

    def sizeHint(self):
        size = super().sizeHint()
        if getattr(self, '_scroll_layout_height', 0):
            size.setHeight(max(size.height(), self._scroll_layout_height))
        return size

    def _keep_in_work_area(self):
        if not self.isVisible() or self._adjusting_bounds:
            return
        screen = self.screen()
        if screen is None:
            return
        # Native Windows QMenu placement can use the full monitor geometry,
        # which includes the taskbar. Enforce the available work area after Qt
        # has picked the monitor and laid out the menu, including shadow gutter.
        # https://doc.qt.io/qt-6/qscreen.html#availableGeometry-prop
        position = clamp_popup_position(self.pos(), self.size(), screen.availableGeometry())
        if position != self.pos():
            self._adjusting_bounds = True
            try:
                self.move(position)
            finally:
                self._adjusting_bounds = False

    def _queue_bounds_check(self):
        if (self.isVisible() and hasattr(self, '_bounds_timer')
                and not self._adjusting_bounds):
            self._bounds_timer.start(0)

    def moveEvent(self, event):
        super().moveEvent(event)
        self._queue_bounds_check()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._queue_bounds_check()

    def showEvent(self, event):
        self.open_animation.stop()
        animate = menu_animations_enabled(self)
        self.fade_effect.setOpacity(0. if animate else 1.)
        super().showEvent(event)
        self._keep_in_work_area()
        # Qt can finalize its popup position after showEvent. Recheck on the
        # next event turn, also covering native button/exec() opening paths.
        self._queue_bounds_check()
        if animate:
            self.open_animation.start()

    def hideEvent(self, event):
        self._scroll_layout_height = 0
        self.setMaximumHeight(16777215)
        self._bounds_timer.stop()
        self.open_animation.stop()
        self.fade_effect.setOpacity(1.)
        super().hideEvent(event)


class ImageContextMenu(RoundedMenu):
    """Copy/rotate/delete strip followed by the two file-operation rows."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.strip = QWidget(objectName='menu_action_strip')
        row = QHBoxLayout(self.strip)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        self.strip_action = QWidgetAction(self)
        self.strip_action.setDefaultWidget(self.strip)
        self.addAction(self.strip_action)
        self.strip_buttons = []
        self.image_actions = []
        self.addSeparator()

    def add_strip_action(self, text, kind, callback, tooltip=''):
        action = QAction(menu_icon(kind), text, self)
        action.setProperty("luma_menu_shortcut", {"copy": "Ctrl+C", "rotate": "R", "delete": "Delete"}.get(kind))
        action.triggered.connect(callback)
        button = QToolButton(self.strip)
        button.setDefaultAction(action)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        button.setIconSize(QSize(20, 20))
        button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        button.setAccessibleName(text)
        button.setToolTip(tooltip or text)
        button.installEventFilter(self)
        button.clicked.connect(self.close)
        self.strip.layout().addWidget(button, 1)
        self.strip_buttons.append(button)
        self.image_actions.append(action)
        return action

    def _focus_strip(self, index=0):
        enabled = [button for button in self.strip_buttons if button.isEnabled()]
        if enabled:
            self.setActiveAction(self.strip_action)
            enabled[index % len(enabled)].setFocus(Qt.FocusReason.MenuBarFocusReason)
            return True
        return False

    def keyPressEvent(self, event):
        rows = [a for a in self.actions() if a is not self.strip_action and not a.isSeparator() and a.isEnabled()]
        if event.key() in (Qt.Key.Key_Home, Qt.Key.Key_Up) and (event.key() == Qt.Key.Key_Home or (rows and self.activeAction() is rows[0])):
            if self._focus_strip():
                event.accept(); return
        if self.activeAction() is self.strip_action and event.key() in (Qt.Key.Key_Down, Qt.Key.Key_Right, Qt.Key.Key_Left, Qt.Key.Key_Return, Qt.Key.Key_Space):
            if self._focus_strip():
                event.accept(); return
        super().keyPressEvent(event)

    def eventFilter(self, watched, event):
        if watched in self.strip_buttons and event.type() == QEvent.Type.KeyPress:
            if self._activate_shortcut(event):
                return True
            key = event.key()
            if key in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
                step = -1 if key in (Qt.Key.Key_Left, Qt.Key.Key_Backtab) else 1
                self._focus_strip(self.strip_buttons.index(watched) + step)
                return True
            if key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                rows = [a for a in self.actions() if a is not self.strip_action and not a.isSeparator() and a.isEnabled()]
                if rows:
                    watched.clearFocus()
                    self.setFocus(Qt.FocusReason.MenuBarFocusReason)
                    self.setActiveAction(rows[0 if key == Qt.Key.Key_Down else -1])
                return True
            if key == Qt.Key.Key_Escape:
                self.close()
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
                watched.click()
                return True
        return super().eventFilter(watched, event)
