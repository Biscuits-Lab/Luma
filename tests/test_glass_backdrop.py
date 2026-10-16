"""Demand, bounded-allocation, scene-only and accessibility glass contracts."""
import ctypes
import sys
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QWidget

from luma.canvas import ImageCanvas
from luma import glass_backdrop as glass


@pytest.fixture
def backdrop(qapp, monkeypatch):
    monkeypatch.setattr(glass, 'transparency_allowed', lambda: True)
    root = QWidget()
    root.resize(800, 500)
    canvas = ImageCanvas(root)
    canvas.setGeometry(root.rect())
    image = QImage(1024, 768, QImage.Format.Format_RGB32)
    image.fill(QColor('#20a0dc'))
    painter = QPainter(image)
    for x in range(0, image.width(), 24):
        painter.fillRect(x, 0, 12, image.height(), QColor('#eeeeee'))
    painter.fillRect(600, 300, 400, 200, QColor('#ba722d'))
    painter.end()
    canvas.set_image(image)
    widget = glass.GlassBackdrop(canvas, root)
    widget.setGeometry(484, 0, 316, 500)
    widget.hide()
    root.show()
    qapp.processEvents()
    yield root, canvas, widget
    root.close()
    qapp.processEvents()


def wait_capture(widget, after=0):
    for _ in range(30):
        if widget.capture_count > after:
            return
        QTest.qWait(10)
    assert widget.capture_count > after


@pytest.mark.parametrize('size', [(316, 700), (10000, 10000), (10, 9000), (9000, 10), (1, 1)])
def test_sample_allocation_has_hard_pixel_and_edge_limits(size):
    sample = glass.bounded_sample_size(QSize(*size))
    assert 0 < sample.width() <= glass.MAX_SAMPLE_EDGE
    assert 0 < sample.height() <= glass.MAX_SAMPLE_EDGE
    assert sample.width() * sample.height() <= glass.MAX_SAMPLE_PIXELS


def test_hidden_no_capture_and_visible_coalescing_cache(backdrop, qapp):
    _, canvas, widget = backdrop
    for _ in range(100):
        widget.invalidate()
        canvas.viewport().update()
    QTest.qWait(80)
    assert widget.capture_count == 0
    assert not widget._refresh_timer.isActive() and not widget._policy_timer.isActive()
    widget.show()
    for _ in range(100):
        widget.invalidate()
    wait_capture(widget)
    assert widget.capture_count == 1
    count = widget.capture_count
    # Repainting the glass or scene without changing state must not recapture.
    for _ in range(5):
        widget.update()
        canvas.viewport().update()
        QTest.qWait(20)
    assert widget.capture_count == count
    widget.hide()
    canvas.zoom(2)
    widget.invalidate()
    QTest.qWait(80)
    assert widget.capture_count == count
    assert not widget._policy_timer.isActive()
    widget.show()
    wait_capture(widget, count)


def test_backdrop_is_real_scene_blur_tint_and_has_left_shadow(backdrop, qapp):
    _, canvas, widget = backdrop
    widget.show()
    wait_capture(widget)
    assert not widget._image.isNull()
    assert widget.body_rect().left() == 12
    assert widget.last_capture_size.width() <= 256 and widget.last_capture_size.height() <= 256
    sample = glass.render_scene_sample(canvas, widget._source_rect())
    frosted = widget._image
    colors = {frosted.pixelColor(x, y).rgb() for x in range(0, frosted.width(), 4)
              for y in range(0, frosted.height(), 4)}
    assert len(colors) > 10  # genuine scene variation, not a solid gray disguise
    assert frosted.pixelColor(20, 20).lightness() < sample.pixelColor(20, 20).lightness()
    image = widget.grab().toImage()
    dpr = image.devicePixelRatio()
    y = image.height() // 2
    assert image.hasAlphaChannel()
    assert image.pixelColor(0, y).alpha() < 10
    assert 0 < image.pixelColor(round(5 * dpr), y).alpha() < 50
    assert image.pixelColor(round(15 * dpr), y).alpha() == 255
    assert widget.graphicsEffect() is None  # no blur effect can smear sibling controls
    assert widget.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    assert widget.last_capture_ms < 250  # generous regression ceiling; timings reported separately


def test_capture_excludes_overlay_children_and_never_uses_full_image_copy(backdrop, monkeypatch):
    root, canvas, widget = backdrop
    monkeypatch.setattr(canvas, 'grab', lambda *a: pytest.fail('Recursive widget grab'))
    monkeypatch.setattr(canvas, 'displayed_image', lambda *a: pytest.fail('Full image copy'))
    blocker = QWidget(canvas.viewport())
    blocker.setStyleSheet('background: #ff00ff')
    blocker.setGeometry(canvas.viewport().rect())
    blocker.show()
    widget.show()
    wait_capture(widget)
    source = glass.render_scene_sample(canvas, QRect(0, 0, 800, 500))
    assert source.pixelColor(20, 20) != QColor('#ff00ff')
    assert widget.capture_count == 1


@pytest.mark.parametrize('change', ['image', 'zoom', 'pan', 'rotation', 'resize', 'background'])
def test_visual_changes_invalidate_and_stale_pixels_clear(backdrop, qapp, change):
    _, canvas, widget = backdrop
    widget.show()
    wait_capture(widget)
    before = widget.capture_count
    if change == 'image':
        image = QImage(500, 300, QImage.Format.Format_RGB32)
        image.fill(Qt.GlobalColor.green)
        canvas.set_image(image)
    elif change == 'zoom':
        canvas.zoom(2)
    elif change == 'pan':
        canvas.zoom(3)
        canvas.horizontalScrollBar().setValue(canvas.horizontalScrollBar().value() + 70)
    elif change == 'rotation':
        canvas.rotate_clockwise()
    elif change == 'resize':
        widget.resize(300, 450)
    else:
        canvas.background = 'White'
        canvas.viewport().update()
    widget.invalidate()
    assert widget._image.isNull()
    wait_capture(widget, before)
    assert widget._last_signature == widget._signature()


def test_reduced_transparency_solid_fallback_stops_work(backdrop, monkeypatch):
    _, canvas, widget = backdrop
    widget.show()
    wait_capture(widget)
    count = widget.capture_count
    monkeypatch.setattr(glass, 'transparency_allowed', lambda: False)
    widget.refresh_effects_policy()
    assert widget.solid_fallback and widget._image.isNull()
    assert not widget._refresh_timer.isActive()
    canvas.zoom(2)
    widget.invalidate()
    QTest.qWait(80)
    assert widget.capture_count == count
    monkeypatch.setattr(glass, 'transparency_allowed', lambda: True)
    widget.refresh_effects_policy()
    wait_capture(widget, count)
    assert not widget.solid_fallback


@pytest.mark.parametrize('state', ['normal', 'contrast', 'overlap', 'transparency_off', 'query_failed', 'registry_failed'])
def test_windows_accessibility_and_transparency_reads_only(monkeypatch, state):
    calls = []
    class Query:
        def __call__(self, flag, size, pointer, update):
            calls.append(flag)
            assert update == 0
            if flag == 0x42:
                info = ctypes.cast(pointer, ctypes.POINTER(glass._HighContrast))[0]
                assert size == info.cbSize == ctypes.sizeof(glass._HighContrast)
                info.dwFlags = int(state == 'contrast')
            else:
                assert flag == 0x1040 and size == 0
                ctypes.cast(pointer, ctypes.POINTER(ctypes.c_int32))[0] = int(state == 'overlap')
            return state != 'query_failed'
    query = Query()
    monkeypatch.setattr(glass.ctypes, 'WinDLL', lambda *a, **k: SimpleNamespace(SystemParametersInfoW=query), raising=False)
    class Key:
        def __enter__(self): return self
        def __exit__(self, *args): pass
    def open_key(hive, path, reserved, access):
        assert hive == 1 and reserved == 0 and access == 2
        if state == 'registry_failed':
            raise OSError('Registry not readable')
        return Key()
    registry = SimpleNamespace(HKEY_CURRENT_USER=1, KEY_READ=2, REG_DWORD=4, OpenKey=open_key,
                               QueryValueEx=lambda key, name: (int(state != 'transparency_off'), 4))
    monkeypatch.setitem(sys.modules, 'winreg', registry)
    assert glass._windows_transparency_allowed() is (state == 'normal')
    assert calls[0] == 0x42


def test_no_stale_result_published_if_scene_changes_during_capture(backdrop, monkeypatch):
    _, canvas, widget = backdrop
    original = glass.render_scene_sample
    changed = False
    def render(view, rect):
        nonlocal changed
        sample = original(view, rect)
        if not changed:
            changed = True
            canvas.zoom(2)
        return sample
    monkeypatch.setattr(glass, 'render_scene_sample', render)
    widget.show()
    wait_capture(widget)
    assert widget.capture_count == 1
    assert widget._last_signature == widget._signature()
