"""Small, non-interactive canvas notifications; never part of the image layout."""
from __future__ import annotations

import sys

from PySide6.QtCore import QEvent, QEasingCurve, Qt, QTimer, QVariantAnimation
from PySide6.QtGui import QAccessible, QAccessibleAnnouncementEvent
from PySide6.QtWidgets import QGraphicsOpacityEffect, QLabel, QStyle, QWidget


def _reduced_motion(widget: QWidget) -> bool:
    """Respect Windows' animation preference and the Qt style's animation hint."""
    if sys.platform == 'win32':
        try:
            import ctypes
            enabled = ctypes.c_int(1)
            if ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(enabled), 0):
                return not bool(enabled.value)  # SPI_GETCLIENTAREAANIMATION
        except (AttributeError, OSError):
            pass
    return not bool(widget.style().styleHint(QStyle.StyleHint.SH_Widget_Animate, None, widget))


class CanvasToast(QLabel):
    """One replaceable message, held for three seconds after entering.

    Parent this to the canvas container, outside its layout. All geometry is in
    Qt logical pixels. A single owned timer and animation are stopped before
    replacement, so an earlier message cannot dismiss a newer one.
    """
    HOLD_MS = 3000
    ENTER_MS = 160
    EXIT_MS = 180
    MARGIN = 16
    MAX_WIDTH = 520

    def __init__(self, parent: QWidget, *, reduced_motion: bool | None = None):
        super().__init__(parent)
        self._motion_override = reduced_motion
        self._progress = 0.0
        self._phase = 'hidden'
        self._hold_ms = self.HOLD_MS
        self.setObjectName('canvasToast')
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setWordWrap(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setStyleSheet('''QLabel#canvasToast {
            color: #f5f5f5; background: rgba(39, 39, 42, 242);
            border: 1px solid rgba(255, 255, 255, 35); border-radius: 9px;
            padding: 9px 15px; font-size: 12px;
        }''')
        self._opacity = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._opacity)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._begin_exit)
        self._animation = QVariantAnimation(self)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animation.valueChanged.connect(self._set_progress)
        self._animation.finished.connect(self._animation_finished)
        parent.installEventFilter(self)
        self.hide()

    def show_message(self, text: str, *, duration_ms: int = HOLD_MS):
        """Replace/restart the current transient notice without queuing messages."""
        if not text.strip():
            return
        was_visible = not self.isHidden()
        self._timer.stop()
        self._animation.stop()
        self._hold_ms = max(0, int(duration_ms))
        self.setText(text)
        self.setAccessibleName(text)
        self._phase = 'enter'
        self._place()
        self.show()
        self.raise_()
        if self._motion_disabled():
            self._set_progress(1.0)
            self._start_hold()
        else:
            self._animate(self._progress if was_visible else 0.0, 1.0, self.ENTER_MS)
        # Announce the entire message without moving keyboard focus. Text is
        # plain, even when filenames happen to contain HTML-looking characters.
        QAccessible.updateAccessibility(QAccessibleAnnouncementEvent(self, text))

    def dismiss(self):
        self._timer.stop()
        self._animation.stop()
        self._phase = 'hidden'
        self.hide()
        self._set_progress(0.0)

    def _motion_disabled(self):
        return self._motion_override if self._motion_override is not None else _reduced_motion(self)

    def _animate(self, start, end, duration):
        self._set_progress(start)
        self._animation.setDuration(duration)
        self._animation.setStartValue(start)
        self._animation.setEndValue(end)
        self._animation.start()

    def _set_progress(self, value):
        self._progress = float(value)
        self._opacity.setOpacity(self._progress)
        self._place()

    def _start_hold(self):
        self._phase = 'hold'
        self._timer.start(self._hold_ms)

    def _begin_exit(self):
        if self._motion_disabled():
            self.dismiss()
        else:
            self._phase = 'exit'
            self._animate(self._progress, 0.0, self.EXIT_MS)

    def _animation_finished(self):
        if self._phase == 'enter':
            self._start_hold()
        elif self._phase == 'exit':
            self.dismiss()

    def _place(self):
        parent = self.parentWidget()
        if parent is None:
            return
        available = max(1, parent.width() - 2 * self.MARGIN)
        width = min(available, self.MAX_WIDTH,
                    self.fontMetrics().horizontalAdvance(self.text()) + 34)
        width = max(1, width)
        height = max(36, self.heightForWidth(width))
        top = min(self.MARGIN, max(0, parent.height() - height))
        top -= round((1.0 - self._progress) * 6)
        self.setGeometry((parent.width() - width) // 2, max(0, top), width, height)

    def eventFilter(self, watched, event):
        if watched is self.parentWidget() and event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            self._place()
        return super().eventFilter(watched, event)
