"""Small, keyboard-accessible controls for the modeless Settings window.

The background selector mirrors the existing viewer control. Nothing in this
module persists preferences or starts work when the window is opened.
All painted decoration uses logical coordinates, including fractional DPI.
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QIconEngine, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel, QLayout, QPushButton, QSizePolicy,
    QVBoxLayout, QWidget,
)

from .i18n import get_language, tr


SETTINGS_STYLE = """
QDialog#luma_settings { background: #191919; }
QDialog#luma_settings QWidget { background: transparent; }
QDialog#luma_settings QScrollArea { border: 0; }
QDialog#luma_settings QFrame#settings_card { background: #242424; border: 1px solid #333333; border-radius: 10px; }
QDialog#luma_settings QLabel#settings_title { color: #f3f3f3; font-size: 23px; font-weight: 600; }
QDialog#luma_settings QLabel#settings_heading { color: #efefef; font-size: 13px; font-weight: 600; }
QDialog#luma_settings QLabel#settings_muted { color: #ababab; font-size: 11px; }
QDialog#luma_settings QLabel#settings_badge { color: #bcbcbc; font-size: 10px; padding: 3px 7px; background: #303030; border: 1px solid #414141; border-radius: 5px; }
QDialog#luma_settings QLabel#settings_badge[state="ready"] { color: #d2e5da; background: #2b3730; border-color: #3d5044; }
QDialog#luma_settings QLabel#settings_badge[state="attention"] { color: #ead9b6; background: #393429; border-color: #554a35; }
QDialog#luma_settings QFrame#settings_separator { background: #343434; border: 0; border-radius: 0; max-height: 1px; }
QDialog#luma_settings QPushButton { color: #e4e4e4; background: #303030; border: 1px solid #484848; border-radius: 5px; padding: 7px 12px; }
QDialog#luma_settings QPushButton:hover { background: #3b3b3b; border-color: #636363; }
QDialog#luma_settings QPushButton:pressed { background: #454545; }
QDialog#luma_settings QPushButton:focus { border-color: #b5b5b5; }
QDialog#luma_settings QPushButton:disabled { color: #747474; background: #292929; border-color: #383838; }
QDialog#luma_settings QPushButton#check_updates { color: #202020; background: #e1e1e1; border-color: #e1e1e1; font-weight: 600; font-size: 11px; padding: 4px 9px; }
QDialog#luma_settings QPushButton#check_updates:hover { background: #f5f5f5; }
QDialog#luma_settings QPushButton#check_updates:disabled { color: #747474; background: #383838; border-color: #484848; }
QDialog#luma_settings QPushButton#settings_close { background: transparent; border: 0; border-radius: 6px; padding: 0; }
QDialog#luma_settings QPushButton#settings_close:hover { background: #373737; }
QDialog#luma_settings QPushButton#settings_close:pressed { background: #484848; }
QDialog#luma_settings QPushButton#settings_close:focus { border: 0; }
QDialog#luma_settings QPushButton#settings_close[keyboard_focus="true"]:focus { border: 1px solid #b5b5b5; }
QDialog#luma_settings QPushButton#settings_disclosure { text-align: left; color: #b7b7b7; background: transparent; border: 1px solid transparent; padding: 4px 2px; font-size: 11px; }
QDialog#luma_settings QPushButton#settings_disclosure:hover { color: #eeeeee; }
QDialog#luma_settings QPushButton#settings_disclosure:focus { border-color: #999999; }
QDialog#luma_settings QComboBox { background: #303030; color: #e2e2e2; border: 1px solid #484848; border-radius: 5px; padding: 7px 10px; }
QDialog#luma_settings QComboBox:focus { border-color: #b5b5b5; }
QDialog#luma_settings QComboBox QAbstractItemView { background: #303030; color: #e2e2e2; selection-background-color: #494949; }
QDialog#luma_settings QProgressBar { background: #353535; border: 0; border-radius: 3px; min-height: 6px; max-height: 6px; }
QDialog#luma_settings QProgressBar::chunk { background: #cccccc; border-radius: 3px; }
"""


def settings_font_families(language=None):
    """Prefer the user's requested Windows face, then the correct CJK region.

    Generic sans-serif fallback can choose Japanese glyph variants even when
    the translated characters are Simplified Chinese. Never change UI language
    to compensate for a font mismatch, and never bundle/download system fonts.
    """
    traditional = (language or get_language()) == 'zh-TW'
    return ('Microsoft YaHei', 'Microsoft YaHei UI',
            'Noto Sans CJK TC' if traditional else 'Noto Sans CJK SC',
            'Noto Sans TC' if traditional else 'Noto Sans SC',
            'Segoe UI', 'sans-serif')


def settings_stylesheet(language=None):
    # A scoped QSS rule is necessary: QWidget's application-level font-family
    # rule overrides setFont() on the dialog and its child widgets.
    families = ', '.join(f"'{family}'" for family in settings_font_families(language))
    return SETTINGS_STYLE + ("\nQDialog#luma_settings, QDialog#luma_settings QWidget "
                             "{ font-family: " + families + "; }\n")


class _SettingsIconEngine(QIconEngine):
    def __init__(self, kind):
        super().__init__()
        self.kind = kind

    def clone(self):
        return _SettingsIconEngine(self.kind)

    def paint(self, painter, rect, mode, state):
        painter.save()
        painter.translate(rect.x(), rect.y())
        painter.scale(rect.width() / 24, rect.height() / 24)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setOpacity(.4 if mode == QIcon.Mode.Disabled else 1)
        pen = QPen(QColor('#bfbfbf'), 2.25 if self.kind == 'close' else 1.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if self.kind == 'display':
            painter.drawRoundedRect(QRectF(3, 4, 18, 13), 2, 2)
            painter.drawLine(QPointF(12, 17), QPointF(12, 21))
            painter.drawLine(QPointF(8, 21), QPointF(16, 21))
        elif self.kind == 'windows':
            for x, y in ((3, 3), (14, 3), (3, 14), (14, 14)):
                painter.drawRoundedRect(QRectF(x, y, 7, 7), 1, 1)
        elif self.kind == 'update':
            path = QPainterPath()
            path.arcMoveTo(QRectF(4, 4, 16, 16), 35)
            path.arcTo(QRectF(4, 4, 16, 16), 35, 285)
            painter.drawPath(path)
            painter.drawLine(QPointF(20, 3), QPointF(20, 9))
            painter.drawLine(QPointF(20, 9), QPointF(14, 9))
        elif self.kind == 'close':
            painter.drawLine(QPointF(6, 6), QPointF(18, 18))
            painter.drawLine(QPointF(18, 6), QPointF(6, 18))
        elif self.kind in ('right', 'down', 'external'):
            path = QPainterPath()
            if self.kind == 'right':
                path.moveTo(9, 6); path.lineTo(15, 12); path.lineTo(9, 18)
            elif self.kind == 'down':
                path.moveTo(6, 9); path.lineTo(12, 15); path.lineTo(18, 9)
            else:
                path.moveTo(7, 17); path.lineTo(17, 7); path.lineTo(9, 7)
                path.moveTo(17, 7); path.lineTo(17, 15)
            painter.drawPath(path)
        painter.restore()

    def pixmap(self, size, mode, state):
        result = QPixmap(size)
        result.fill(Qt.GlobalColor.transparent)
        painter = QPainter(result)
        self.paint(painter, result.rect(), mode, state)
        painter.end()
        return result


def settings_icon(kind):
    return QIcon(_SettingsIconEngine(kind))


class SettingsCloseButton(QPushButton):
    """Borderless pointer target with a focus cue for keyboard navigation only."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('settings_close')
        self.setIcon(settings_icon('close'))
        self.setIconSize(QSize(18, 18))
        self.setFixedSize(32, 32)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setProperty('keyboard_focus', False)

    def _set_keyboard_focus(self, visible):
        self.setProperty('keyboard_focus', visible)
        # Dynamic selector changes do not automatically invalidate Qt's QSS.
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def focusInEvent(self, event):
        self._set_keyboard_focus(event.reason() in (
            Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason,
            Qt.FocusReason.ShortcutFocusReason,
        ))
        super().focusInEvent(event)

    def focusOutEvent(self, event):
        self._set_keyboard_focus(False)
        super().focusOutEvent(event)

    def mousePressEvent(self, event):
        self._set_keyboard_focus(False)
        super().mousePressEvent(event)


class OutlineIcon(QWidget):
    def __init__(self, kind, parent=None):
        super().__init__(parent)
        self.icon = settings_icon(kind)
        self.setFixedSize(22, 22)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def paintEvent(self, event):
        painter = QPainter(self)
        self.icon.paint(painter, self.rect())


def plain_label(text='', *, muted=False, wrap=False):
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(wrap)
    if muted:
        label.setObjectName('settings_muted')
    if wrap:
        label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Minimum)
    return label


def badge(text):
    label = plain_label(text)
    label.setObjectName('settings_badge')
    label.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
    return label


def separator():
    line = QFrame()
    line.setObjectName('settings_separator')
    line.setFixedHeight(1)
    return line



class _SettingsHeaderLayout(QLayout):
    """Keep compact headers inline, flowing actions below only when needed.

    The minimum width is the wider of the two groups, not their sum. Otherwise
    QScrollArea enlarges its content before a narrow header can ever reflow.
    Height-for-width lets the card reserve both rows without resize callbacks
    or a platform-dependent font/DPI breakpoint.
    """
    def __init__(self, title, icon, accessory):
        super().__init__()
        self._items = []
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(9)
        heading = QWidget()
        row = QHBoxLayout(heading)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(9)
        row.addWidget(OutlineIcon(icon))
        label = plain_label(title)
        label.setObjectName('settings_heading')
        row.addWidget(label)
        self.addWidget(heading)
        if accessory is not None:
            self.addWidget(accessory)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def sizeHint(self):
        sizes = [item.sizeHint() for item in self._items]
        return QSize(sum(size.width() for size in sizes) + self.spacing() * (len(sizes) - 1),
                     max((size.height() for size in sizes), default=0))

    def minimumSize(self):
        return QSize(max((item.minimumSize().width() for item in self._items), default=0),
                     max((item.minimumSize().height() for item in self._items), default=0))

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        sizes = [item.sizeHint() for item in self._items]
        if len(sizes) == 2 and width < self.sizeHint().width():
            return sum(size.height() for size in sizes) + self.spacing()
        return max((size.height() for size in sizes), default=0)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        if not self._items:
            return
        leading = self._items[0]
        if len(self._items) == 1:
            leading.setGeometry(rect)
            return
        trailing = self._items[1]
        left, right = leading.sizeHint(), trailing.sizeHint()
        if rect.width() < self.sizeHint().width():
            leading.setGeometry(QRect(rect.x(), rect.y(), rect.width(), left.height()))
            trailing.setGeometry(QRect(rect.right() - right.width() + 1,
                                       rect.y() + left.height() + self.spacing(),
                                       right.width(), right.height()))
        else:
            height = max(left.height(), right.height())
            leading.setGeometry(QRect(rect.x(), rect.y() + (height - left.height()) // 2,
                                      rect.width() - right.width() - self.spacing(), left.height()))
            trailing.setGeometry(QRect(rect.right() - right.width() + 1,
                                       rect.y() + (height - right.height()) // 2,
                                       right.width(), right.height()))


class SettingsCard(QFrame):
    def __init__(self, title, icon, parent=None, accessory=None):
        super().__init__(parent)
        self.setObjectName('settings_card')
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(18, 16, 18, 14)
        self.body.setSpacing(8)
        self.body.addLayout(_SettingsHeaderLayout(title, icon, accessory))

    def minimumSizeHint(self):
        hint = super().minimumSizeHint()
        # A wrapped row's minimum-width height can be much taller than its
        # actual allocated-width height (especially with CJK labels). Feeding
        # that artificial height to QScrollArea creates an unnecessary rail.
        if self.body.hasHeightForWidth():
            hint.setHeight(self.body.totalHeightForWidth(max(self.width(), hint.width())))
        return hint


class SettingsRow(QWidget):
    def __init__(self, title, control, *, note='', parent=None):
        super().__init__(parent)
        self.setMinimumHeight(32)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 1, 0, 1)
        row.setSpacing(16)
        labels = QVBoxLayout()
        labels.setSpacing(3)
        self.label = plain_label(title, wrap=True)
        self.label.setBuddy(control)
        labels.addWidget(self.label)
        if note:
            labels.addWidget(plain_label(note, muted=True, wrap=True))
        row.addLayout(labels, 1)
        row.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter)
        if not control.accessibleName():
            control.setAccessibleName(title)


class ActivityStatusLabel(QLabel):
    """No idle placeholder; every real updater message becomes visible."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setWordWrap(True)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Minimum)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.hide()

    def setText(self, text):
        super().setText(text)
        self.setVisible(bool(text))


class DetailsPanel(QWidget):
    """A real disclosure button; hidden explanations remain keyboard reachable."""
    def __init__(self, title, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.toggle = QPushButton(title)
        self.toggle.setObjectName('settings_disclosure')
        self.toggle.setAccessibleName(title)
        self.toggle.setCheckable(True)
        self.toggle.setIconSize(QSize(13, 13))
        self.toggle.setIcon(settings_icon('right'))
        self.toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        layout.addWidget(self.toggle)
        self.content = QWidget()
        self.body = QVBoxLayout(self.content)
        self.body.setContentsMargins(2, 4, 2, 2)
        self.body.setSpacing(9)
        layout.addWidget(self.content)
        self.content.hide()
        self.toggle.toggled.connect(self._toggle)

    def _toggle(self, checked):
        self.content.setVisible(checked)
        self.toggle.setIcon(settings_icon('down' if checked else 'right'))

    def setExpanded(self, expanded):
        self.toggle.setChecked(expanded)


class DisplaySettingsPanel(SettingsCard):
    """Two-way binding to this viewer's existing canvas background."""
    def __init__(self, viewer):
        self.scope_badge = badge(tr('Current window'))
        super().__init__(tr('Appearance'), 'display', accessory=self.scope_badge)
        self.viewer = viewer
        self.scope_badge.setToolTip(tr('These display options apply immediately to this window.'))
        self.background = QComboBox()
        self.background.setObjectName('settings_background')
        self.background.setAccessibleName(tr('Canvas background'))
        self.background.setMinimumWidth(150)
        self.background.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        for index in range(viewer.background.count()):
            self.background.addItem(viewer.background.itemText(index), viewer.background.itemData(index))
        self.background.setCurrentIndex(viewer.background.currentIndex())
        self.background.currentIndexChanged.connect(viewer.background.setCurrentIndex)
        viewer.background.currentIndexChanged.connect(self.background.setCurrentIndex)
        self.body.addWidget(SettingsRow(tr('Canvas background'), self.background))
