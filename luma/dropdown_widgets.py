"""Shared dropdown decoration, retaining Qt's native combobox interaction.

The real QComboBox popup still owns selection, keyboard search, activation,
accessibility and dismissal. Its public view/window is decorated before first
show, rather than replacing it with a menu or a second selection model.
All geometry is in logical pixels; only the shadow extends past the panel.
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPalette, QPen
from PySide6.QtWidgets import (
    QApplication, QAbstractItemView, QComboBox, QFrame, QListView,
    QStyle, QStyledItemDelegate, QStyleOptionComboBox, QStyleOptionViewItem,
    QStylePainter,
)


SHADOW_MARGIN = 10
PANEL_RADIUS = 7
PANEL_PADDING = 5

COMBO_STYLE = """
QComboBox { background: #303030; color: #e2e2e2; border: 1px solid #484848;
    border-radius: 5px; padding: 6px 28px 6px 10px; combobox-popup: 0; }
QComboBox:hover { background: #383838; border-color: #626262; }
QComboBox:focus { border-color: #b5b5b5; }
QComboBox:disabled { color: #747474; background: #292929; border-color: #383838; }
QComboBox::drop-down { width: 0; border: 0; }
QComboBox::down-arrow { image: none; }
"""

VIEW_STYLE = """
QListView { background: transparent; border: 0; padding: 0; outline: 0;
    color: #eeeeee; selection-background-color: transparent; }
QListView::item { background: transparent; border: 0; padding: 0; }
QScrollBar:vertical { background: transparent; width: 6px; }
QScrollBar::handle:vertical { background: #585858; border-radius: 3px; min-height: 20px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
"""


def paint_chevron(painter, rect, color):
    """The footer's existing six-pixel vector chevron, shared by all triggers."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(color, 1.4)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    x, y = rect.right() - 13, rect.center().y()
    arrow = QPainterPath(QPointF(x - 3, y - 1.5))
    arrow.lineTo(x, y + 1.5)
    arrow.lineTo(x + 3, y - 1.5)
    painter.drawPath(arrow)
    painter.restore()


def popup_panel_rect(widget):
    # Integer outer edges allow panel-width == trigger-width exactly. Stroke
    # is inset below, so antialiasing never adds half a pixel to that contract.
    return QRectF(widget.rect()).adjusted(SHADOW_MARGIN, SHADOW_MARGIN,
                                         -SHADOW_MARGIN, -SHADOW_MARGIN)


def paint_popup_surface(painter, panel, radius=PANEL_RADIUS):
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    shadow = panel.translated(0, 2)
    for spread, alpha in ((8, 1), (7, 2), (6, 3), (5, 4), (4, 5),
                          (3, 6), (2, 8), (1, 10), (0, 12)):
        painter.setBrush(QColor(0, 0, 0, alpha))
        painter.drawRoundedRect(shadow.adjusted(-spread, -spread, spread, spread),
                                radius + spread, radius + spread)
    painter.setPen(QPen(QColor('#474747'), 1))
    painter.setBrush(QColor('#2b2b2b'))
    painter.drawRoundedRect(panel.adjusted(.5, .5, -.5, -.5), radius, radius)
    painter.restore()


def anchored_popup_geometry(trigger, height, work_area, *, gap=4, prefer_above=False):
    """Align the visible panel with its trigger and fit the usable monitor.

    Near a monitor edge, keep the panel aligned even when its non-interactive
    shadow is clipped by that monitor. A visible trigger in the work area always
    gets exactly its own panel width. Very tall lists scroll inside the panel.
    """
    width = trigger.width() + 2 * SHADOW_MARGIN
    height = min(height, work_area.height()) if not work_area.isEmpty() else height
    below = trigger.bottom() + 1 + gap - SHADOW_MARGIN
    above = trigger.top() - gap - height + SHADOW_MARGIN
    y = above if prefer_above and (work_area.isEmpty() or above >= work_area.top()) else below
    x = trigger.left()
    if not work_area.isEmpty():
        x = max(work_area.left(), min(x, work_area.right() + 1 - trigger.width()))
        if below + height > work_area.bottom() + 1 and above >= work_area.top():
            y = above
        y = max(work_area.top(), min(y, work_area.bottom() + 1 - height))
    return QRect(x - SHADOW_MARGIN, y, width, height)


class _DropdownDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        return QSize(size.width() + 20, max(30, option.fontMetrics.height() + 12))

    def paint(self, painter, option, index):
        option = QStyleOptionViewItem(option)
        self.initStyleOption(option, index)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        enabled = bool(option.state & QStyle.StateFlag.State_Enabled)
        selected = bool(option.state & (QStyle.StateFlag.State_Selected | QStyle.StateFlag.State_MouseOver))
        if selected and enabled:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor('#454545'))
            painter.drawRoundedRect(QRectF(option.rect).adjusted(0, 1, 0, -1), 4, 4)
        painter.setPen(QColor('#eeeeee' if enabled else '#777777'))
        painter.setFont(option.font)
        text_rect = option.rect.adjusted(10, 0, -10, 0)
        text = option.fontMetrics.elidedText(option.text, Qt.TextElideMode.ElideRight,
                                            text_rect.width())
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)
        painter.restore()


class _ComboPopupDecoration(QObject):
    """Paint only the real native popup container; children remain native."""
    def __init__(self, combo, popup):
        super().__init__(combo)
        self.combo = combo
        self.popup = popup
        popup.installEventFilter(self)

    def eventFilter(self, watched, event):
        # Qt can deliver final child teardown events while the Python wrapper
        # is being cleared. Do not dereference a partially destroyed owner.
        if watched is getattr(self, 'popup', None):
            if event.type() == QEvent.Type.Paint:
                painter = QPainter(watched)
                paint_popup_surface(painter, popup_panel_rect(watched))
                painter.end()
                return True
            if event.type() == QEvent.Type.MouseButtonPress:
                path = QPainterPath()
                path.addRoundedRect(popup_panel_rect(watched), PANEL_RADIUS, PANEL_RADIUS)
                if not path.contains(event.position()):
                    self.combo.hidePopup()
                    return True
            if event.type() == QEvent.Type.Hide:
                self.combo.update()
        return super().eventFilter(watched, event)


class DropdownComboBox(QComboBox):
    """Painted chevron plus a rounded native list popup, with unchanged API."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(COMBO_STYLE)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMaxVisibleItems(10)
        view = QListView(self)
        view.setFrameShape(QFrame.Shape.NoFrame)
        view.setStyleSheet(VIEW_STYLE)
        view.setMouseTracking(True)
        view.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        view.setTextElideMode(Qt.TextElideMode.ElideRight)
        view.setItemDelegate(_DropdownDelegate(view))
        view.setAutoFillBackground(False)
        view.viewport().setAutoFillBackground(False)
        self.setView(view)
        self._popup_decoration = None
        self._popup_position_timer = QTimer(self)
        self._popup_position_timer.setSingleShot(True)
        self._popup_position_timer.timeout.connect(self._position_popup)

    def paintEvent(self, event):
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        option.subControls &= ~QStyle.SubControl.SC_ComboBoxArrow
        painter = QStylePainter(self)
        painter.drawComplexControl(QStyle.ComplexControl.CC_ComboBox, option)
        group = QPalette.ColorGroup.Active if self.isEnabled() else QPalette.ColorGroup.Disabled
        color = self.palette().color(group, QPalette.ColorRole.ButtonText)
        painter.setPen(color)
        text_rect = self.rect().adjusted(10, 0, -28, 0)
        text = self.fontMetrics().elidedText(self.currentText(), Qt.TextElideMode.ElideRight,
                                            text_rect.width())
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)
        paint_chevron(painter, QRectF(self.rect()), color)

    def _prepare_popup(self):
        popup = self.view().window()
        if self._popup_decoration is None or self._popup_decoration.popup is not popup:
            popup.setWindowFlags(popup.windowFlags() | Qt.WindowType.FramelessWindowHint |
                                 Qt.WindowType.NoDropShadowWindowHint)
            popup.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
            popup.setAutoFillBackground(False)
            popup.setFrameShape(QFrame.Shape.NoFrame)
            popup.setStyleSheet('QFrame { background: transparent; border: 0; }')
            popup.setContentsMargins(0, 0, 0, 0)
            inset = SHADOW_MARGIN + PANEL_PADDING
            popup.layout().setContentsMargins(inset, inset, inset, inset)
            self._popup_decoration = _ComboPopupDecoration(self, popup)
        popup.setAccessibleName(self.accessibleName())
        return popup

    def popup_panel_rect(self):
        return popup_panel_rect(self.view().window())

    def _position_popup(self):
        popup = self.view().window()
        if not popup.isVisible():
            return
        trigger = QRect(self.mapToGlobal(QPoint()), self.size())
        screen = QApplication.screenAt(trigger.center()) or self.screen()
        area = screen.availableGeometry()
        rows = min(self.count(), self.maxVisibleItems())
        height = sum(self.view().sizeHintForRow(row) for row in range(rows))
        height += 2 * (SHADOW_MARGIN + PANEL_PADDING)
        popup.setGeometry(anchored_popup_geometry(trigger, height, area))
        self.view().scrollTo(self.view().currentIndex(), QAbstractItemView.ScrollHint.EnsureVisible)

    def showPopup(self):
        if not self.count() or not self.isEnabled():
            return
        self._prepare_popup()
        super().showPopup()
        self._position_popup()
        # Some Windows styles finish native placement after showPopup returns.
        self._popup_position_timer.start(0)
        self.update()

    def hidePopup(self):
        self._popup_position_timer.stop()
        super().hidePopup()
        self.update()
