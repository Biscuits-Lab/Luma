"""A delayed, non-interactive busy ring for the image area.

The caller owns decode/render work and supplies its generation token. The
indicator never starts work, guesses a percentage, or pumps the event loop.
"""
from __future__ import annotations

from PySide6.QtCore import QElapsedTimer, QEvent, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QAccessible, QAccessibleAnnouncementEvent, QColor, QPainter, QPalette, QPen
from PySide6.QtWidgets import QApplication, QStyle, QWidget

from .canvas import motion_allowed
from .i18n import tr
from .tooltip_theme import high_contrast_enabled


class CanvasLoadingIndicator(QWidget):
    """Place outside the image container's layout and call ``begin(token)``.

    ``finish(token)`` dismisses only that request; ``cancel()`` / ``reset()``
    dismiss everything. Tokens must be unique to each image request. An already
    visible ring stays visible across replacements, while fast opens never
    display it. Both timers are owned, bounded, and stopped when not needed.
    All drawing and positioning use Qt logical pixels for native DPI scaling.
    """

    SHOW_DELAY_MS = 180
    FRAME_INTERVAL_MS = 33
    ROTATION_MS = 1000
    WIDTH = 208
    HEIGHT = 156
    RING_DIAMETER = 56
    RING_WIDTH = 5.5
    LABEL_GAP = 14

    def __init__(self, parent: QWidget, *, reduced_motion: bool | None = None,
                 delay_ms: int = SHOW_DELAY_MS):
        super().__init__(parent)
        self._active_token: int | tuple[str, int] | None = None
        self._motion_override = reduced_motion
        self._delay_ms = max(0, int(delay_ms))
        self._ready = False
        self._angle = 0.0
        self._static = True
        self._high_contrast = False
        self._text = ''
        self._delay_clock = QElapsedTimer()
        self._rotation_clock = QElapsedTimer()
        self.setObjectName('canvasLoadingIndicator')
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAutoFillBackground(False)
        # The app's QWidget rule supplies an opaque background otherwise.
        self.setStyleSheet('QWidget#canvasLoadingIndicator { background: transparent; border: none; }')

        self._delay_timer = QTimer(self)
        self._delay_timer.setSingleShot(True)
        self._delay_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._delay_timer.timeout.connect(self._reveal)
        self._frame_timer = QTimer(self)
        self._frame_timer.setInterval(self.FRAME_INTERVAL_MS)
        self._frame_timer.timeout.connect(self._advance)
        parent.installEventFilter(self)
        self._refresh_preferences()
        self._place()
        self.hide()

    @property
    def active_token(self) -> int | tuple[str, int] | None:
        return self._active_token

    def begin(self, token: int | tuple[str, int]):
        """Track a new image request without flashing for quick completions."""
        if token is None:
            raise ValueError('A loading request needs a non-None generation token')
        if token == self._active_token:
            return
        self._active_token = token
        self._delay_timer.stop()
        self._refresh_preferences()
        self._place()
        if self._ready:
            self.show()
            self.raise_()
            self._sync_animation()
        else:
            self._delay_clock.start()
            self._delay_timer.start(self._delay_ms)

    def finish(self, token: int | tuple[str, int]):
        """Ignore stale decode/render callbacks from superseded requests."""
        if token == self._active_token:
            self.cancel()

    def cancel(self):
        self._delay_timer.stop()
        self._frame_timer.stop()
        self._delay_clock.invalidate()
        self._rotation_clock.invalidate()
        self._active_token = None
        self._ready = False
        self._angle = 0.0
        self.hide()

    def reset(self):
        self.cancel()

    def _refresh_preferences(self):
        self._text = tr('Loading…')
        self.setAccessibleName(self._text)
        self.setAccessibleDescription(self._text)
        self._high_contrast = high_contrast_enabled()
        self._static = (self._motion_override if self._motion_override is not None else
                        not motion_allowed() or not bool(self.style().styleHint(
                            QStyle.StyleHint.SH_Widget_Animate, None, self)))
        self._sync_animation()
        self.update()

    def _reveal(self):
        if self._active_token is None or not self._delay_clock.isValid():
            return
        # Even a previously queued timeout cannot reveal a newer request early.
        remaining = self._delay_ms - self._delay_clock.elapsed()
        if remaining > 0:
            self._delay_timer.start(remaining)
            return
        self._ready = True
        self._place()
        self.show()
        self.raise_()
        self._sync_animation()
        # Announce once when the status actually appears, never on every frame.
        if self.isVisible():
            QAccessible.updateAccessibility(QAccessibleAnnouncementEvent(self, self._text))

    def _sync_animation(self):
        # Also pause when an ancestor is hidden/minimized. No background spin.
        if (self._active_token is not None and self._ready and self.isVisible()
                and not self._static):
            if not self._frame_timer.isActive():
                self._rotation_clock.start()
                self._frame_timer.start()
        else:
            self._frame_timer.stop()
            if self._static:
                self._angle = 0.0

    def _advance(self):
        if self._active_token is None or not self.isVisible() or self._static:
            self._frame_timer.stop()
            return
        self._angle = (self._rotation_clock.elapsed() % self.ROTATION_MS) * 360.0 / self.ROTATION_MS
        self.update()

    def _place(self):
        parent = self.parentWidget()
        if parent is not None:
            width, height = min(self.WIDTH, parent.width()), min(self.HEIGHT, parent.height())
            self.setGeometry((parent.width() - width) // 2, (parent.height() - height) // 2,
                             width, height)

    def _colors(self):
        if self._high_contrast:
            palette = QApplication.palette()
            background = palette.color(QPalette.ColorRole.Window)
            foreground = palette.color(QPalette.ColorRole.WindowText)
            return background, foreground, foreground, foreground
        # No badge fill: the ring (and its optional label) sit on the canvas.
        return QColor(32, 32, 32, 242), QColor('#4b4b4b'), QColor('#777777'), QColor('#eeeeee')

    def paintEvent(self, event):
        if not self._ready or self._active_token is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        background, border, track, foreground = self._colors()
        center = QPointF(self.rect().center())

        radius = self.RING_DIAMETER / 2
        ring = QRectF(center.x() - radius, center.y() - radius,
                      self.RING_DIAMETER, self.RING_DIAMETER)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(track, self.RING_WIDTH))
        if not self._high_contrast:
            painter.drawEllipse(ring)
        # A fixed-length arc is deliberately indeterminate, never a percentage.
        pen = QPen(foreground, self.RING_WIDTH)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawArc(ring, round((90.0 - self._angle) * 16), -110 * 16)

        if self._static or self._high_contrast:
            # The label is the only filled surface; keep the ring itself open.
            # A stationary glyph alone can look frozen; keep an explicit status.
            metrics = self.fontMetrics()
            width = min(self.width() - 2, metrics.horizontalAdvance(self._text) + 16)
            height = metrics.height() + 6
            label = QRectF(center.x() - width / 2,
                           center.y() + radius + self.LABEL_GAP, width, height)
            painter.setBrush(background)
            painter.setPen(QPen(border, 1.0))
            painter.drawRoundedRect(label, 5, 5)
            painter.setPen(foreground)
            painter.drawText(label, Qt.AlignmentFlag.AlignCenter, self._text)
        painter.end()

    def eventFilter(self, watched, event):
        if watched is self.parentWidget():
            if event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
                self._place()
            elif event.type() == QEvent.Type.Close:
                self.cancel()
        return super().eventFilter(watched, event)

    def showEvent(self, event):
        super().showEvent(event)
        self._sync_animation()

    def hideEvent(self, event):
        self._frame_timer.stop()
        super().hideEvent(event)

    def closeEvent(self, event):
        self.cancel()
        super().closeEvent(event)

    def changeEvent(self, event):
        super().changeEvent(event)
        if (hasattr(self, '_frame_timer') and event.type() in
                (QEvent.Type.StyleChange, QEvent.Type.PaletteChange,
                 QEvent.Type.ApplicationPaletteChange, QEvent.Type.LanguageChange)):
            self._refresh_preferences()
