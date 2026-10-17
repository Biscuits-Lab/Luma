"""Display filtering preserves exact pixels, physical density and bounded work."""
import threading
import time

import numpy as np
import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QColorSpace, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QStyleOptionGraphicsItem

from luma import minification as m
from luma.canvas import FilteredPixmapItem, ImageCanvas


def pump(app, predicate, timeout=4):
    deadline = time.monotonic() + timeout
    while not predicate():
        app.processEvents()
        assert time.monotonic() < deadline, 'asynchronous reduction did not complete'
        time.sleep(.001)
    app.processEvents()


def image(values):
    values = np.ascontiguousarray(values, dtype=np.uint8)
    h, w = values.shape[:2]
    return QImage(values.data, w, h, values.strides[0], QImage.Format.Format_RGBA8888).convertToFormat(
        QImage.Format.Format_ARGB32_Premultiplied)


def pixels(source):
    source = source.convertToFormat(QImage.Format.Format_RGBA8888_Premultiplied)
    return np.frombuffer(source.constBits(), np.uint8).reshape(source.height(), source.bytesPerLine())[
        :, :source.width() * 4].reshape(source.height(), source.width(), 4).copy()


def solid(w=256, h=256, color='#d03218'):
    result = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    result.fill(QColor(color))
    return result


def test_area_reduction_matches_independent_block_average_and_preserves_source():
    rng = np.random.default_rng(12)
    values = rng.integers(0, 256, (64, 96, 4), dtype=np.uint8)
    values[..., 3] = 255
    source = image(values)
    source.setDevicePixelRatio(2)
    source.setColorSpace(QColorSpace(QColorSpace.NamedColorSpace.SRgb))
    before = source.copy()
    reduced = m.area_downsample(source, 12, 8)
    expected = values.astype(float).reshape(8, 8, 12, 8, 4).mean(axis=(1, 3))
    assert np.max(np.abs(pixels(reduced).astype(float) - expected)) <= 1
    assert reduced.devicePixelRatio() == 2
    assert reduced.colorSpace() == source.colorSpace()
    assert source == before


def test_alpha_is_filtered_premultiplied_without_hidden_color_bleed():
    values = np.zeros((32, 32, 4), np.uint8)
    values[..., 2] = 255
    values[::2, ::2] = [255, 0, 0, 128]
    reduced = m.area_downsample(image(values), 4, 4)
    expected = np.array([32, 0, 0, 32])
    assert np.max(np.abs(pixels(reduced).astype(int) - expected)) <= 1


def test_odd_dimensions_keep_full_coverage_and_cancellation_does_not_mutate():
    source = solid(13, 9)
    reduced = m.area_downsample(source, 3, 2)
    assert reduced.size().width() == 3 and reduced.height() == 2
    assert reduced.pixelColor(2, 1) == source.pixelColor(12, 8)
    assert m.area_downsample(source, 13, 9).cacheKey() == source.cacheKey()
    with pytest.raises(m.ReductionCancelled):
        m.area_downsample(source, 3, 2, cancelled=lambda: True)
    with pytest.raises(ValueError):
        m.area_downsample(source, 14, 9)


def test_lazy_levels_obey_budget_and_never_supply_undersized_or_actual_pixel_level(qapp):
    cache = m.MinificationCache(budget=4096)
    try:
        cache.set_source(solid(257, 129))
        cache.request(.125)
        pump(qapp, lambda: cache._active is None and cache.choose(.125) is not None)
        assert cache.cached_bytes <= 4096
        assert cache.cached_bytes == sum(i.sizeInBytes() for i in cache._levels.values())
        assert cache.cached_levels == (3,)
        assert cache.choose(.125).size().width() == 33
        for scale in (1, 2, .5, .25, 0, -1, float('nan'), float('inf')):
            assert cache.choose(scale) is None
        cache.request(.03125)
        pump(qapp, lambda: cache._active is None)
        assert cache.cached_bytes <= 4096
        assert all(level <= cache._level_for(.03125) for level in cache.cached_levels)
        # Returned wrappers detach without corrupting the cache's shared pixels.
        selected = cache.choose(.03125)
        selected.fill(Qt.GlobalColor.black)
        assert cache.choose(.03125).pixelColor(0, 0) == QColor('#d03218')
    finally:
        cache.close()


def test_worker_does_not_block_and_new_image_discards_inflight_old_exposure(qapp, monkeypatch):
    started, release = threading.Event(), threading.Event()
    original = m.area_downsample
    threads = []

    def delayed(source, width, height, cancelled):
        threads.append(threading.current_thread().ident)
        if not started.is_set():
            started.set()
            assert release.wait(3)
        return original(source, width, height, cancelled)

    monkeypatch.setattr(m, 'area_downsample', delayed)
    cache = m.MinificationCache(budget=8192)
    try:
        cache.set_source(solid())
        start = time.monotonic()
        cache.request(.125)
        assert time.monotonic() - start < .1
        pump(qapp, started.is_set)
        ticks = []
        QTimer.singleShot(0, lambda: ticks.append(True))
        pump(qapp, lambda: bool(ticks))
        # Same geometry, different pixels: models an exposure replacement too.
        cache.set_source(solid(color='#17ad53'))
        for scale in [.25, .125, .5, .0625] * 20:
            cache.request(scale)
        assert cache.cached_bytes == 0 and cache.choose(.125) is None
        release.set()
        pump(qapp, lambda: cache._active is None and cache.choose(.0625) is not None)
        assert cache.choose(.0625).pixelColor(0, 0) == QColor('#17ad53')
        assert all(t != threading.current_thread().ident for t in threads)
        assert cache.cached_bytes <= cache.budget
    finally:
        release.set()
        cache.close()


def test_close_is_immediate_and_late_completion_cannot_repopulate(qapp, monkeypatch):
    started, release = threading.Event(), threading.Event()
    original = m.area_downsample

    def delayed(*args):
        started.set()
        assert release.wait(3)
        return original(*args)

    monkeypatch.setattr(m, 'area_downsample', delayed)
    cache = m.MinificationCache()
    cache.set_source(solid())
    cache.request(.125)
    pump(qapp, started.is_set)
    start = time.monotonic()
    cache.close()
    assert time.monotonic() - start < .1
    release.set()
    pump(qapp, lambda: not cache._worker._thread.is_alive())
    assert cache.cached_bytes == 0 and cache.choose(.125) is None


@pytest.mark.parametrize('display_dpr', [1, 1.25, 1.5, 2])
@pytest.mark.parametrize('source_dpr', [1, 2])
@pytest.mark.parametrize('angle', [0, 90])
def test_item_uses_physical_pixel_density_with_rotation_and_source_dpr(qapp, display_dpr, source_dpr, angle):
    cache = m.MinificationCache()
    source = solid(256, 256)
    source.setDevicePixelRatio(source_dpr)
    item = FilteredPixmapItem(cache)
    item.setPixmap(QPixmap.fromImage(source))
    before = item.pixmap().cacheKey(), item.boundingRect()

    def paint(physical_scale):
        output = solid(256, 256, '#000000')
        output.setDevicePixelRatio(display_dpr)
        painter = QPainter(output)
        painter.scale(physical_scale * source_dpr / display_dpr,
                      physical_scale * source_dpr / display_dpr)
        if angle:
            painter.translate(256 / source_dpr, 0)
            painter.rotate(angle)
        item.paint(painter, QStyleOptionGraphicsItem())
        painter.end()
        return output

    try:
        assert item.transformationMode() == Qt.TransformationMode.SmoothTransformation
        paint(.125)
        assert cache._desired == 3
        pump(qapp, lambda: cache._active is None)
        paint(.125)
        assert cache.choose(.125) is not None
        result = paint(1)
        assert cache._desired == 0 and cache.choose(1) is None
        assert result.pixelColor(30, 30) == QColor('#d03218')
        assert before == (item.pixmap().cacheKey(), item.boundingRect())
    finally:
        cache.close()


def test_canvas_replacement_clear_export_and_close_preserve_exact_source(qapp):
    canvas = ImageCanvas()
    canvas.resize(400, 300)
    try:
        first = solid(512, 512)
        canvas.set_image(first)
        canvas.minification.request(.125)
        pump(qapp, lambda: canvas.minification._active is None)
        assert canvas.displayed_image() == canvas.item.pixmap().toImage()
        second = solid(512, 512, '#17ad53')
        canvas.set_image(second, reset=False)
        assert canvas.minification.cached_bytes == 0
        assert canvas.displayed_image().pixelColor(0, 0) == QColor('#17ad53')
        canvas.clear_image()
        assert canvas.minification.choose(.125) is None
        assert canvas.minification.cached_bytes == 0
    finally:
        canvas.close()
    assert canvas.minification._closed


def test_deleted_graphics_item_is_not_called_by_later_cache_change(qapp):
    from PySide6.QtWidgets import QGraphicsScene
    from shiboken6 import isValid
    scene = QGraphicsScene()
    cache = m.MinificationCache()
    item = FilteredPixmapItem(cache)
    scene.addItem(item)
    scene.clear()
    assert not isValid(item)
    cache.set_source(solid())
    cache.close()


def test_destroying_canvas_during_native_reduction_never_signals_deleted_qobjects(qapp, monkeypatch):
    from PySide6.QtCore import QEvent
    from shiboken6 import isValid
    started, release = threading.Event(), threading.Event()
    original = m.area_downsample
    def delayed(*args):
        started.set()
        assert release.wait(3)
        return original(*args)
    monkeypatch.setattr(m, 'area_downsample', delayed)
    canvas = ImageCanvas()
    canvas.set_image(solid())
    cache = canvas.minification
    cache.request(.125)
    pump(qapp, started.is_set)
    worker = cache._worker
    canvas.deleteLater()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not isValid(canvas) and not isValid(cache)
    release.set()
    pump(qapp, lambda: not worker._thread.is_alive())
    assert worker._result is None


def test_repeated_canvas_destruction_leaves_no_background_workers(qapp):
    from PySide6.QtCore import QEvent
    workers = []
    for _ in range(40):
        canvas = ImageCanvas()
        canvas.set_image(solid(64, 64))
        canvas.minification.request(.125)
        workers.append(canvas.minification._worker)
        canvas.deleteLater()
        qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    pump(qapp, lambda: all(not worker._thread.is_alive() for worker in workers))
    assert all(worker._result is None for worker in workers)
