"""Full-resolution rendering acceleration must preserve pixels and ownership."""
from __future__ import annotations

import gc
from pathlib import Path
import threading
import time
import weakref

import numpy as np
import pytest
from PySide6.QtGui import QImage, QPixmap

from luma.decoders import DecodeCancelled, ImageData, render_rgba
from luma.workers import ImageService, LatestWorker, render_display_rgba
import luma.workers as workers


@pytest.fixture
def force_parallel(monkeypatch):
    monkeypatch.setattr(workers, '_PARALLEL_RENDER_MIN_PIXELS', 0)
    monkeypatch.setattr(workers.os, 'cpu_count', lambda: 4)


@pytest.fixture
def linear_data():
    rng = np.random.default_rng(1216)
    pixels = rng.uniform(-.3, 5, (515, 33, 4)).astype(np.float32)
    pixels[..., 3] = rng.uniform(-.1, 1.1, pixels.shape[:2])
    pixels[0, :5] = [[np.nan, np.inf, -np.inf, 0],
                    [0, 1, 2, np.nan], [1, 2, 3, np.inf],
                    [1e20, 1e-20, -2, 1e-9], [1, 2, 3, -np.inf]]
    data = ImageData(Path('generated.exr'), pixels, True)
    data.pixels.setflags(write=False)
    return data


@pytest.mark.parametrize('channel', ['RGBA', 'RGB', 'R', 'G', 'B', 'A'])
@pytest.mark.parametrize('exposure,tone', [(-3, 'Reinhard'), (0, 'Reinhard'),
                                         (2.5, 'Clip'), (1, 'ACES')])
@pytest.mark.parametrize('alpha', ['straight', 'premultiplied (OpenEXR)'])
def test_parallel_float_render_is_byte_identical(force_parallel, linear_data,
                                                 channel, exposure, tone, alpha):
    linear_data.metadata['Alpha mode'] = alpha
    original = linear_data.pixels.tobytes()
    expected = render_rgba(linear_data, exposure, channel, tone)
    actual = render_display_rgba(linear_data, exposure, channel, tone, lambda: False)
    assert np.array_equal(actual, expected)
    assert actual.flags.c_contiguous
    assert linear_data.pixels.tobytes() == original


def test_parallel_bands_share_source_and_limit_tasks(force_parallel, linear_data, monkeypatch):
    original = workers.render_rgba
    bands, names = [], set()
    lock = threading.Lock()

    def track(data, *args, **kwargs):
        assert np.shares_memory(data.pixels, linear_data.pixels)
        assert not data.pixels.flags.writeable
        with lock:
            bands.append(data.pixels.shape)
            names.add(threading.current_thread().name)
        return original(data, *args, **kwargs)

    monkeypatch.setattr(workers, 'render_rgba', track)
    render_display_rgba(linear_data, 0, 'RGBA', 'Reinhard', lambda: False)
    assert sum(shape[0] for shape in bands) == linear_data.height
    assert max(shape[0] for shape in bands) <= 128
    assert len(names) <= 4
    assert not any(thread.name.startswith('Luma render') for thread in threading.enumerate())


def test_parallel_cancel_discards_output_and_joins_threads(force_parallel, linear_data, monkeypatch):
    original = workers.render_rgba
    cancelled = threading.Event()
    completed_bands = []

    def stop_after_band(data, *args, **kwargs):
        result = original(data, *args, **kwargs)
        completed_bands.append(True)
        cancelled.set()
        return result

    monkeypatch.setattr(workers, 'render_rgba', stop_after_band)
    with pytest.raises(DecodeCancelled):
        render_display_rgba(linear_data, 0, 'RGBA', 'Reinhard', cancelled.is_set)
    assert completed_bands
    assert not any(thread.name.startswith('Luma render') for thread in threading.enumerate())


@pytest.mark.parametrize('exposure,channel,tone', [(float('nan'), 'RGBA', 'Reinhard'),
                                                 (0, 'invalid', 'Reinhard'),
                                                 (0, 'RGBA', 'invalid')])
def test_parallel_keeps_original_validation_error(force_parallel, linear_data,
                                                  exposure, channel, tone):
    with pytest.raises(ValueError) as serial:
        render_rgba(linear_data, exposure, channel, tone)
    with pytest.raises(ValueError) as parallel:
        render_display_rgba(linear_data, exposure, channel, tone, lambda: False)
    assert str(parallel.value) == str(serial.value)


def test_small_and_sdr_images_do_not_spawn_render_threads(linear_data, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Small/SDR image launched an executor')

    monkeypatch.setattr(workers, 'ThreadPoolExecutor', forbidden)
    render_display_rgba(linear_data, 0, 'RGBA', 'Reinhard', lambda: False)
    linear_data.linear = False
    monkeypatch.setattr(workers, '_PARALLEL_RENDER_MIN_PIXELS', 0)
    render_display_rgba(linear_data, 0, 'RGBA', 'Reinhard', lambda: False)


def test_executor_start_failure_is_not_masked(force_parallel, linear_data, monkeypatch):
    class FailedExecutor:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def map(self, *args):
            raise RuntimeError('Unable to start a worker thread')

    monkeypatch.setattr(workers, 'ThreadPoolExecutor', FailedExecutor)
    with pytest.raises(RuntimeError, match='Unable to start a worker thread'):
        render_display_rgba(linear_data, 0, 'RGBA', 'Reinhard', lambda: False)


@pytest.mark.parametrize('outcome', ['success', 'cancel', 'fail'])
def test_navigation_releases_uncached_old_source_before_decode(tmp_path, monkeypatch, outcome):
    first, second = tmp_path / 'first.exr', tmp_path / 'second.exr'
    first.write_bytes(b'first')
    second.write_bytes(b'second')
    service = ImageService(budget=1, render_budget=1)
    source_refs = []
    cancelled = threading.Event()

    def decode(path, **kwargs):
        if path == second:
            assert source_refs[0]() is None
            assert service.current_image is None
            assert service.current_key is None
            if outcome == 'cancel':
                cancelled.set()
            elif outcome == 'fail':
                raise ValueError('Broken second image')
        data = ImageData(path, np.ones((4, 6, 4), np.float32), True)
        source_refs.append(weakref.ref(data))
        return data

    monkeypatch.setattr(workers, 'decode_image', decode)
    service((first, 0, 'RGBA', 'Reinhard'), cancelled.is_set)
    assert source_refs[0]() is service.current_image
    # Same-key exposure edits must still reuse that uncached original.
    service((first, 1, 'RGBA', 'Reinhard'), cancelled.is_set)
    assert len(source_refs) == 1
    if outcome == 'fail':
        with pytest.raises(ValueError, match='Broken second image'):
            service((second, 0, 'RGBA', 'Reinhard'), cancelled.is_set)
    else:
        result = service((second, 0, 'RGBA', 'Reinhard'), cancelled.is_set)
        assert (result is None) == (outcome == 'cancel')
    assert source_refs[0]() is None
    assert service.cache.bytes == service.render_cache.bytes == 0
    if outcome != 'success':
        assert service.current_image is None and service.current_key is None
    else:
        assert service.current_image.path == second


def test_parallel_render_still_obeys_latest_worker(qapp, force_parallel, linear_data, monkeypatch):
    started, release = threading.Event(), threading.Event()
    original = workers.render_rgba

    def pause(data, exposure, *args, **kwargs):
        if exposure == 0:
            started.set()
            release.wait(3)
        return original(data, exposure, *args, **kwargs)

    monkeypatch.setattr(workers, 'render_rgba', pause)
    worker = LatestWorker(lambda exposure, cancel: render_display_rgba(
        linear_data, exposure, 'RGBA', 'Reinhard', cancel))
    completed, failed = [], []
    worker.completed.connect(lambda token, value: completed.append((token, value)))
    worker.failed.connect(lambda token, error: failed.append(error))
    try:
        worker.submit(0)
        assert started.wait(3)
        for exposure in range(1, 8):
            token = worker.submit(exposure)
        release.set()
        deadline = time.monotonic() + 5
        while not completed and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(.005)
        assert not failed
        assert len(completed) == 1 and completed[0][0] == token
        assert np.array_equal(completed[0][1], original(linear_data, 7))
    finally:
        release.set()
        worker.close()


def test_native_qimage_conversion_preserves_pixels_and_owns_storage(qapp):
    # Every alpha value plus RGB extremes exercise Qt's premultiplication roundoff.
    pixels = np.empty((2, 256, 4), np.uint8)
    pixels[..., 0] = np.arange(256)
    pixels[..., 1] = 255 - np.arange(256)
    pixels[0, :, 2] = 255
    pixels[1, :, 2] = 17
    pixels[..., 3] = np.arange(256)
    reference = weakref.ref(pixels)
    wrapped = QImage(pixels.data, 256, 2, pixels.strides[0], QImage.Format.Format_RGBA8888)
    baseline = QPixmap.fromImage(wrapped.copy()).toImage()
    native = wrapped.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    candidate = QPixmap.fromImage(native)
    pixels[:] = 0
    del wrapped, pixels
    gc.collect()
    assert reference() is None
    assert candidate.toImage() == baseline
    assert native.width() == 256 and native.height() == 2
