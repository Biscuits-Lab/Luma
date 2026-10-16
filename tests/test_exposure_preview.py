"""Bounded interactive HDR updates, exact refinement and interrupted UI flows."""
from pathlib import Path
import threading
import time

import numpy as np
import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest

from luma import workers
from luma.app import ViewerWindow
from luma.canvas import ImageCanvas
from luma.decoders import DecodeCancelled, ImageData, render_rgba


def wait_for(app, predicate, timeout=6):
    until = time.monotonic() + timeout
    while not predicate() and time.monotonic() < until:
        app.processEvents()
        time.sleep(.002)
    assert predicate()


@pytest.mark.parametrize('shape', [(1200, 1500), (4, 9000), (9000, 4), (12, 16)])
def test_preview_bound_and_original_linear_samples(shape):
    pixels = np.random.default_rng(17).random((*shape, 4), dtype=np.float32)
    original = pixels.copy()
    source = ImageData(Path('synthetic.exr'), pixels, True, {'Alpha mode': 'premultiplied'})
    sample = workers.exposure_preview(source, lambda: False)
    assert sample.width <= workers.EXPOSURE_PREVIEW_EDGE
    assert sample.height <= workers.EXPOSURE_PREVIEW_EDGE
    assert sample.width * sample.height <= workers.EXPOSURE_PREVIEW_PIXELS
    assert sample.nbytes <= workers.EXPOSURE_PREVIEW_PIXELS * 16
    assert sample.metadata is source.metadata and sample.linear
    assert np.array_equal(source.pixels, original)
    if sample is not source:
        x = ((np.arange(sample.width) + .5) * source.width / sample.width).astype(int)
        y = ((np.arange(sample.height) + .5) * source.height / sample.height).astype(int)
        np.testing.assert_array_equal(sample.pixels, pixels[y[:, None], x[None, :]])


@pytest.mark.parametrize('tone', ['Reinhard', 'ACES', 'Clip'])
@pytest.mark.parametrize('channel', ['RGBA', 'RGB', 'R', 'G', 'B', 'A'])
def test_preview_then_exact_is_unchanged_reuses_decode_and_counts_native_cache(tmp_path, monkeypatch, tone, channel):
    path = tmp_path / 'linear.exr'; path.touch()
    source = ImageData(path, np.random.default_rng(42).uniform(-2, 8, (32, 48, 4)).astype(np.float32),
                       True, {'Alpha mode': 'premultiplied'})
    calls = []
    monkeypatch.setattr(workers, 'decode_image', lambda *a, **k: calls.append(1) or source)
    service = workers.ImageService(budget=1, render_budget=100_000)
    preview = service((path, -1.7, channel, tone, True), lambda: False)
    result = service((path, -1.7, channel, tone), lambda: False)
    expected = render_rgba(source, -1.7, channel, tone)
    np.testing.assert_array_equal(result['rgba'], expected)
    assert calls == [1]
    assert preview['preview'] and not result['preview']
    assert result['image'].format() == QImage.Format.Format_ARGB32_Premultiplied
    expected_image = QImage(expected.data, 48, 32, expected.strides[0],
                           QImage.Format.Format_RGBA8888).convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    assert result['image'] == expected_image
    assert service.render_cache.bytes == result['rgba'].nbytes + result['image'].sizeInBytes()
    assert service.render_cache.bytes <= service.render_cache.budget


def test_cancel_during_exact_keeps_decoded_source_for_next_preview(tmp_path, monkeypatch):
    path = tmp_path / 'linear.exr'; path.touch()
    source = ImageData(path, np.ones((64, 64, 4), np.float32), True)
    calls = []
    monkeypatch.setattr(workers, 'decode_image', lambda *a, **k: calls.append(1) or source)
    service = workers.ImageService(budget=1)
    original = workers.render_display_rgba
    def cancel_render(*a, **k):
        raise DecodeCancelled('superseded')
    monkeypatch.setattr(workers, 'render_display_rgba', cancel_render)
    with pytest.raises(DecodeCancelled):
        service((path, 1, 'RGBA', 'Reinhard'), lambda: False)
    assert service.current_image is source
    monkeypatch.setattr(workers, 'render_display_rgba', original)
    result = service((path, 2, 'RGBA', 'Reinhard', True), lambda: False)
    assert result['preview'] and calls == [1]


def test_qimage_preparation_is_owning_cancellable_and_worker_side(qapp):
    rgba = np.random.default_rng(12).integers(0, 255, (300, 64, 4), np.uint8)
    worker_threads = []
    def handler(payload, cancelled):
        worker_threads.append(threading.get_ident())
        return workers.prepare_display_image(payload, cancelled)
    worker = workers.LatestWorker(handler)
    results = []
    worker.completed.connect(lambda token, image: results.append(image))
    try:
        worker.submit(rgba)
        wait_for(qapp, lambda: bool(results))
        assert worker_threads[0] != threading.get_ident()
        before = results[0].copy()
        rgba.fill(0)
        assert results[0] == before
    finally:
        worker.close()
    checks = []
    with pytest.raises(DecodeCancelled):
        workers.prepare_display_image(rgba, lambda: checks.append(1) or len(checks) >= 2)
    assert len(checks) == 2


def test_preview_canvas_preserves_source_geometry_rotation_zoom_and_pan(qapp):
    canvas = ImageCanvas(); canvas.resize(800, 600); canvas.show(); qapp.processEvents()
    full = QImage(2400, 1200, QImage.Format.Format_ARGB32_Premultiplied); full.fill(Qt.GlobalColor.red)
    sample = full.scaled(600, 300)
    try:
        canvas.set_image(full); canvas.rotate_clockwise(); canvas.set_zoom_percent(250)
        canvas.horizontalScrollBar().setValue(canvas.horizontalScrollBar().value() + 55)
        canvas.verticalScrollBar().setValue(canvas.verticalScrollBar().value() + 40)
        before = (canvas.item.sceneBoundingRect(), canvas.transform(), canvas.zoom_percent,
                  canvas.horizontalScrollBar().value(), canvas.verticalScrollBar().value())
        for image in (sample, full, sample, full):
            canvas.set_image(image, reset=False, source_size=(2400, 1200))
            assert canvas.item.rotation() == 90
            assert (canvas.item.sceneBoundingRect(), canvas.transform(), canvas.zoom_percent,
                    canvas.horizontalScrollBar().value(), canvas.verticalScrollBar().value()) == before
        canvas.actual_size(); scale = canvas.transform()
        canvas.set_image(sample, reset=False, source_size=(2400, 1200))
        assert canvas.transform() == scale
    finally:
        canvas.close()


@pytest.fixture
def hdr_window(qapp, tmp_path, monkeypatch):
    path = tmp_path / 'interactive.exr'; path.touch()
    source = ImageData(path, np.ones((1100, 1800, 4), np.float32), True)
    source.pixels[..., :3] = np.linspace(0, 8, 1800, dtype=np.float32)[None, :, None]
    calls = []
    monkeypatch.setattr(workers, 'decode_image', lambda *a, **k: calls.append(1) or source)
    window = ViewerWindow(); window.show(); window.open_path(path)
    wait_for(qapp, lambda: window.displayed_path == path)
    window.sidebar.show(); QTest.qWait(100)
    yield window, path, calls
    window.close(); qapp.processEvents()


def test_drag_preview_refines_exactly_without_histogram_glass_or_decode(qapp, hdr_window):
    window, path, calls = hdr_window
    histogram_runs = window.histogram.sample_runs
    assert window.sidebar.glass is None  # Opaque docked card has no blur worker.
    window.canvas.set_zoom_percent(200)
    geometry = window.canvas.item.sceneBoundingRect()
    window.exposure.setSliderDown(True)
    window.exposure.setValue(15)
    wait_for(qapp, lambda: window._display_is_preview)
    assert window._pending_render and not window.save_action.isEnabled()
    assert not window.copy_action.isEnabled() and not window._context_image_ready()
    assert window.canvas.item.sceneBoundingRect() == geometry
    assert window.canvas.zoom_percent == 200
    assert window.dimension_label.text() == '1,800 × 1,100'
    QTest.qWait(240)
    assert window._display_is_preview  # Holding the mouse prevents exact refinement.
    assert window.histogram.sample_runs == histogram_runs
    assert window.sidebar.glass is None
    window.exposure.setSliderDown(False)
    wait_for(qapp, lambda: not window._pending_render)
    assert not window._display_is_preview
    assert window.canvas.item.pixmap().width() == 1800
    assert window.save_action.isEnabled() and window.copy_action.isEnabled()
    assert calls == [1]
    wait_for(qapp, lambda: window.histogram.sample_runs > histogram_runs)


def test_cancel_preview_restores_exact_export_and_controls(qapp, hdr_window):
    window, path, _ = hdr_window
    original = window.canvas.item.pixmap().toImage()
    window.exposure.setSliderDown(True); window.exposure.setValue(20)
    wait_for(qapp, lambda: window._display_is_preview)
    window.cancel_load(); window.exposure.setSliderDown(False)
    assert window.canvas.item.pixmap().toImage() == original
    assert window.exposure.value() == 0
    assert not window._display_is_preview and not window._pending_render
    assert window.save_action.isEnabled() and window.copy_action.isEnabled()
    assert window._context_image_ready()
    QTest.qWait(220)
    assert not window.render_timer.isActive() and window.exposure.value() == 0


def test_latest_input_supersedes_running_full_and_close_cancels_timers(qapp, hdr_window, monkeypatch):
    window, _, calls = hdr_window
    started, cancelled_seen = threading.Event(), threading.Event()
    original = workers.render_display_rgba
    def delayed(data, exposure, channel, tone, cancelled):
        if exposure == 1 and data.width == 1800:
            started.set()
            until = time.monotonic() + 3
            while not cancelled() and time.monotonic() < until:
                time.sleep(.002)
            if cancelled():
                cancelled_seen.set()
                raise DecodeCancelled('superseded')
        return original(data, exposure, channel, tone, cancelled)
    monkeypatch.setattr(workers, 'render_display_rgba', delayed)
    window.exposure.setValue(10)
    wait_for(qapp, started.is_set)
    window.exposure.setSliderDown(True)
    for value in range(11, 31):
        window.exposure.setValue(value)
    wait_for(qapp, cancelled_seen.is_set)
    wait_for(qapp, lambda: window._display_is_preview)
    window.exposure.setSliderDown(False)
    wait_for(qapp, lambda: not window._pending_render)
    assert window.exposure.value() == 30 and calls == [1]
    image = window.canvas.item.pixmap().toImage()
    expected = render_rgba(window.service.current_image, 3)
    expected_image = workers.prepare_display_image(expected, lambda: False)
    assert image == expected_image
    window.exposure.setValue(-10); window.close()
    assert not window.preview_timer.isActive() and not window.render_timer.isActive()


def test_cancel_before_first_preview_restores_exact_control_values(hdr_window):
    window, _, _ = hdr_window
    window.exposure.setValue(13)
    assert window._pending_render and not window._display_is_preview
    window.cancel_load()
    assert window.exposure.value() == 0 and window.save_action.isEnabled()
    assert not window.preview_timer.isActive() and not window.render_timer.isActive()


def test_refinement_failure_restores_exact_frame_and_enables_retry(qapp, hdr_window, monkeypatch):
    window, _, calls = hdr_window
    original_image = window.canvas.item.pixmap().toImage()
    original_render = workers.render_display_rgba
    def fail_full(data, exposure, channel, tone, cancelled):
        if exposure == 1.9 and data.width == 1800:
            raise RuntimeError('synthetic render failure')
        return original_render(data, exposure, channel, tone, cancelled)
    monkeypatch.setattr(workers, 'render_display_rgba', fail_full)
    window.exposure.setValue(19)
    wait_for(qapp, lambda: window.exposure.value() == 0)
    assert window.canvas.item.pixmap().toImage() == original_image
    assert window.save_action.isEnabled() and window.exposure.isEnabled()
    assert not window._pending_render and not window._display_is_preview
    window.exposure.setValue(15)
    wait_for(qapp, lambda: not window._pending_render)
    assert window.exposure.value() == 15 and calls == [1]


def test_idle_worker_does_not_retain_delivered_output(qapp):
    import gc
    import weakref
    refs = []
    worker = workers.LatestWorker(lambda value, cancelled: np.zeros((16, 16, 4), np.uint8))
    worker.completed.connect(lambda token, value: refs.append(weakref.ref(value)))
    try:
        worker.submit(None)
        wait_for(qapp, lambda: bool(refs))
        gc.collect()
        assert refs[0]() is None
    finally:
        worker.close()
