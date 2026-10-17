"""Image loading is delayed, generation-safe, accessible, and non-interactive."""
import pytest
from PySide6.QtCore import QEvent, QPoint, Qt, QTimer
from PySide6.QtGui import QAccessible, QColor, QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QPushButton, QSizePolicy, QVBoxLayout, QWidget
from shiboken6 import isValid

from luma import loading
from luma.i18n import set_language, tr
from luma.loading import CanvasLoadingIndicator


@pytest.fixture
def host(qapp):
    parent = QWidget()
    parent.resize(800, 500)
    layout = QVBoxLayout(parent)
    button = QPushButton('Image area')
    button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
    layout.addWidget(button)
    parent.show()
    button.setFocus()
    qapp.processEvents()
    yield parent, button
    parent.close()
    parent.deleteLater()
    qapp.processEvents()


def reveal(indicator, token=1):
    indicator.begin(token)
    QTest.qWait(indicator._delay_ms + 40)
    assert indicator.isVisible()


def test_default_delay_and_fast_completion_never_flash(host):
    indicator = CanvasLoadingIndicator(host[0], reduced_motion=False)
    assert indicator.SHOW_DELAY_MS == 180
    indicator.begin(1)
    assert indicator.isHidden()
    assert indicator._delay_timer.isActive()
    assert not indicator._frame_timer.isActive()
    QTest.qWait(60)
    assert indicator.isHidden()
    indicator.finish(1)
    QTest.qWait(180)
    assert indicator.isHidden()
    assert indicator.active_token is None
    assert not indicator._delay_timer.isActive()
    assert not indicator._frame_timer.isActive()


def test_visible_indicator_animates_without_blocking_event_loop(host):
    indicator = CanvasLoadingIndicator(host[0], reduced_motion=False, delay_ms=20)
    reveal(indicator)
    assert indicator._frame_timer.isActive()
    assert indicator._frame_timer.interval() >= 30
    angle = indicator._angle
    probe = []
    QTimer.singleShot(10, lambda: probe.append(True))
    QTest.qWait(90)
    assert probe == [True]
    assert indicator._angle != angle
    indicator.finish(1)
    assert not indicator.isVisible()
    assert not indicator._frame_timer.isActive()


def test_replacing_pending_token_restarts_delay_and_ignores_old_finish(host):
    indicator = CanvasLoadingIndicator(host[0], delay_ms=100, reduced_motion=False)
    indicator.begin(1)
    QTest.qWait(65)
    indicator.begin(2)
    indicator.finish(1)
    # Simulate a timeout already queued for the superseded request.
    indicator._reveal()
    QTest.qWait(55)
    assert indicator.active_token == 2
    assert indicator.isHidden()
    QTest.qWait(85)
    assert indicator.isVisible()
    indicator.finish(2)
    assert indicator.isHidden()


def test_visible_replacement_and_duplicate_begin_do_not_flash_or_restart(host):
    indicator = CanvasLoadingIndicator(host[0], reduced_motion=False, delay_ms=10)
    reveal(indicator, 10)
    indicator.begin(11)
    indicator.begin(11)
    indicator.finish(10)
    assert indicator.active_token == 11
    assert indicator.isVisible()
    assert indicator._frame_timer.isActive()
    assert not indicator._delay_timer.isActive()
    indicator.finish(11)
    indicator.begin(12)
    assert indicator.isHidden()
    indicator.finish(11)
    assert indicator.active_token == 12


@pytest.mark.parametrize('method', ['cancel', 'reset', 'close'])
@pytest.mark.parametrize('visible', [False, True])
def test_cancel_reset_close_stop_both_timers_and_stale_callbacks(host, method, visible):
    indicator = CanvasLoadingIndicator(host[0], reduced_motion=False, delay_ms=10)
    indicator.begin(2)
    if visible:
        QTest.qWait(50)
    getattr(indicator, method)()
    indicator._reveal()
    indicator._advance()
    QTest.qWait(50)
    assert indicator.active_token is None
    assert indicator.isHidden()
    assert not indicator._delay_timer.isActive()
    assert not indicator._frame_timer.isActive()


def test_resize_center_and_pointer_keyboard_layout_passthrough(host, qapp):
    parent, button = host
    indicator = CanvasLoadingIndicator(parent, reduced_motion=False, delay_ms=0)
    before = button.geometry()
    reveal(indicator)
    assert button.geometry() == before
    assert button.hasFocus()
    assert indicator.focusPolicy() == Qt.FocusPolicy.NoFocus
    assert indicator.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    assert parent.layout().indexOf(indicator) == -1
    assert parent.childAt(indicator.geometry().center()) is button
    clicked = []
    button.clicked.connect(lambda: clicked.append(True))
    target = parent.childAt(indicator.geometry().center())
    QTest.mouseClick(target, Qt.MouseButton.LeftButton,
                     pos=target.mapFrom(parent, indicator.geometry().center()))
    assert clicked == [True]
    for size in [(901, 601), (300, 240), (90, 80)]:
        parent.setMinimumSize(0, 0)
        parent.resize(*size)
        qapp.processEvents()
        assert parent.rect().contains(indicator.geometry())
        difference = indicator.geometry().center() - parent.rect().center()
        assert abs(difference.x()) <= 1 and abs(difference.y()) <= 1
    assert button.hasFocus()


@pytest.mark.parametrize('language', ['en', 'zh-CN', 'zh-TW'])
def test_static_ring_has_localized_accessible_loading_status(host, language):
    set_language(language)
    indicator = CanvasLoadingIndicator(host[0], reduced_motion=True, delay_ms=0)
    reveal(indicator)
    assert indicator._static
    assert not indicator._frame_timer.isActive()
    assert indicator.accessibleName() == tr('Loading…')
    assert indicator.accessibleDescription() == tr('Loading…')
    interface = QAccessible.queryAccessibleInterface(indicator)
    assert interface is not None
    assert interface.text(QAccessible.Text.Name) == tr('Loading…')
    assert '%' not in indicator.accessibleName()
    before = indicator.grab().toImage()
    QTest.qWait(80)
    assert indicator.grab().toImage() == before
    assert indicator._angle == 0


def test_os_motion_preference_can_stop_an_existing_animation(host, monkeypatch, qapp):
    monkeypatch.setattr(loading, 'motion_allowed', lambda: True)
    indicator = CanvasLoadingIndicator(host[0], delay_ms=0)
    reveal(indicator)
    assert indicator._frame_timer.isActive()
    monkeypatch.setattr(loading, 'motion_allowed', lambda: False)
    qapp.sendEvent(indicator, QEvent(QEvent.Type.StyleChange))
    assert indicator._static
    assert not indicator._frame_timer.isActive()
    assert indicator.isVisible()
    assert indicator._text == 'Loading…'


def test_high_contrast_uses_palette_and_explicit_text(host, monkeypatch, qapp):
    monkeypatch.setattr(loading, 'high_contrast_enabled', lambda: True)
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor('#000000'))
    palette.setColor(QPalette.ColorRole.WindowText, QColor('#ffff00'))
    monkeypatch.setattr(loading.QApplication, 'palette', lambda: palette)
    indicator = CanvasLoadingIndicator(host[0], reduced_motion=True, delay_ms=0)
    reveal(indicator)
    assert indicator._high_contrast
    assert indicator._colors() == (QColor('#000000'),) + (QColor('#ffff00'),) * 3
    assert indicator._text == 'Loading…'
    # Paint in the same device-independent coordinates at scale factors 1 and 2.
    image = indicator.grab().toImage()
    scale = image.devicePixelRatio()
    center = indicator.rect().center()
    # The ring floats without a badge fill, so the middle stays transparent.
    assert image.pixelColor(round(center.x() * scale), round(center.y() * scale)).alpha() == 0
    radius = CanvasLoadingIndicator.RING_DIAMETER // 2
    assert image.pixelColor(round(center.x() * scale),
                            round((center.y() - radius) * scale)).name() == '#ffff00'
    assert image.width() == round(indicator.width() * scale)


def test_ring_is_open_larger_and_thicker(host):
    indicator = CanvasLoadingIndicator(host[0], reduced_motion=False, delay_ms=0)
    reveal(indicator)
    assert CanvasLoadingIndicator.RING_DIAMETER > 32
    assert CanvasLoadingIndicator.RING_WIDTH > 2.5
    image = indicator.grab().toImage()
    scale = image.devicePixelRatio()
    center = indicator.rect().center()
    # The former badge disc filled the middle; the ring now floats free.
    assert image.pixelColor(round(center.x() * scale), round(center.y() * scale)).alpha() == 0
    radius = CanvasLoadingIndicator.RING_DIAMETER // 2
    assert image.pixelColor(round((center.x() - radius) * scale),
                            round(center.y() * scale)).alpha() == 255


def test_parent_hide_pauses_and_show_resumes_without_losing_token(host, qapp):
    parent, _ = host
    indicator = CanvasLoadingIndicator(parent, reduced_motion=False, delay_ms=0)
    reveal(indicator)
    parent.hide()
    qapp.processEvents()
    assert not indicator._frame_timer.isActive()
    assert indicator.active_token == 1
    parent.show()
    qapp.processEvents()
    assert indicator.isVisible()
    assert indicator._frame_timer.isActive()
    parent.close()
    assert indicator.active_token is None
    assert not indicator._frame_timer.isActive()


def test_parent_close_before_delay_does_not_resurrect_indicator(host):
    parent, _ = host
    indicator = CanvasLoadingIndicator(parent, reduced_motion=False, delay_ms=40)
    indicator.begin(1)
    parent.close()
    QTest.qWait(90)
    assert indicator.active_token is None
    assert indicator.isHidden()
    assert not indicator._delay_timer.isActive()


def test_destroying_parent_also_destroys_owned_timers(qapp):
    parent = QWidget()
    parent.show()
    indicator = CanvasLoadingIndicator(parent, reduced_motion=False, delay_ms=0)
    reveal(indicator)
    delay, frame = indicator._delay_timer, indicator._frame_timer
    parent.deleteLater()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not isValid(indicator)
    assert not isValid(delay)
    assert not isValid(frame)


@pytest.mark.parametrize('outcome', ['success', 'error', 'cancel'])
def test_viewer_ring_tracks_current_image_and_preserves_footer(qapp, tmp_path, monkeypatch, outcome):
    import threading
    import time
    from PIL import Image
    from luma.app import ViewerWindow

    first, second = tmp_path / 'first.png', tmp_path / 'second.png'
    Image.new('RGB', (64, 48), 'red').save(first)
    Image.new('RGB', (64, 48), 'green').save(second)
    if outcome == 'error':
        second.write_bytes(b'corrupt')
    window = ViewerWindow()
    window.loading_indicator._delay_ms = 10
    window.show()
    qapp.processEvents()
    window.stack.setCurrentWidget(window.canvas)
    qapp.processEvents()
    footer = window.findChild(QWidget, 'viewbar')
    assert footer.isVisible()
    geometry = footer.geometry()
    started, release = threading.Event(), threading.Event()
    original = window.loader.handler
    errors = []
    monkeypatch.setattr(window, '_persistent_error', errors.append)

    def delayed(payload, cancelled):
        started.set()
        while not release.wait(.005):
            if cancelled():
                return None
        return original(payload, cancelled)

    def wait(predicate):
        deadline = time.monotonic() + 4
        while not predicate():
            qapp.processEvents()
            assert time.monotonic() < deadline, (
                window._load_token, window.loader._generation,
                window.loader._pending, window._pending_render,
                window.displayed_path, window.dimension_label.text(),
                window.loader._thread.is_alive())
            # Yield the Python GIL as well as processing Qt events: Pillow may
            # lazily import its Python format plugins on a corrupt first file.
            time.sleep(.002)

    window.loader.handler = delayed
    try:
        window._open_image(first)
        old = window._load_token
        wait(lambda: started.is_set() and window.loading_indicator.isVisible())
        window._open_image(second)
        current = window._load_token
        window._loaded(old, {'path': first})
        window._load_failed(old, 'obsolete error')
        assert window.loading_indicator.active_token == current
        assert window.loading_indicator.isVisible()
        assert not errors
        assert footer.geometry() == geometry
        if outcome == 'cancel':
            window.cancel_load(silent=True)
        release.set()
        if outcome == 'success':
            wait(lambda: window.displayed_path == second)
        elif outcome == 'error':
            wait(lambda: bool(errors))
        wait(lambda: window.loading_indicator.isHidden())
        assert window.loading_indicator.active_token is None
        assert not window.loading_indicator._frame_timer.isActive()
        assert not window.loading_indicator._delay_timer.isActive()
        assert footer.geometry() == geometry
    finally:
        release.set()
        window.close()
    assert window.canvas.minification._closed
