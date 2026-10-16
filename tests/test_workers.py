from pathlib import Path
import threading
import time

import numpy as np
from luma.workers import ByteCache, LatestWorker, folder_images


def wait_for(app, predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.005)
    app.processEvents()
    assert predicate()


def test_byte_cache_respects_budget_and_lru():
    cache = ByteCache(24)
    a = np.zeros(12, dtype=np.uint8)
    cache.put('a', a)
    cache.put('b', a)
    cache.get('a')
    cache.put('c', a)
    assert cache.get('b') is None
    assert cache.bytes == 24
    cache.put('a', np.zeros(30, dtype=np.uint8))
    assert cache.get('a') is None
    assert cache.bytes == 12
    assert list(cache.entries) == ['c']


def test_latest_worker_drops_running_result_and_pending_jobs(qapp):
    started = threading.Event()
    release = threading.Event()
    performed = []
    def handle(value, cancelled):
        performed.append(value)
        if value == 'first':
            started.set()
            release.wait(2)
        return value
    worker = LatestWorker(handle)
    completed = []
    worker.completed.connect(lambda token, value: completed.append(value))
    worker.submit('first')
    assert started.wait(2)
    for i in range(100):
        worker.submit(i)
    release.set()
    wait_for(qapp, lambda: len(completed) == 1)
    assert completed == [99]
    assert performed == ['first', 99]
    worker.close()


def test_cancel_discards_in_flight_result(qapp):
    started, release = threading.Event(), threading.Event()
    def handle(value, cancelled):
        started.set()
        release.wait(2)
        return value
    worker = LatestWorker(handle)
    completed = []
    worker.completed.connect(lambda token, value: completed.append(value))
    worker.submit('image')
    assert started.wait(2)
    worker.cancel()
    release.set()
    time.sleep(.03)
    qapp.processEvents()
    assert completed == []
    worker.close()


def test_folder_natural_order_and_unicode(tmp_path):
    for name in ['图10.PNG', '图2.png', '图1.jpg', 'ignored.txt']:
        (tmp_path / name).touch()
    (tmp_path / 'directory.jpg').mkdir()
    assert [p.name for p in folder_images(tmp_path)] == ['图1.jpg', '图2.png', '图10.PNG']
    assert folder_images(tmp_path, lambda: True) == []


def test_image_service_reuses_active_image_above_cache_budget(tmp_path, monkeypatch):
    from luma.decoders import ImageData
    from luma.workers import ImageService
    import luma.workers as workers
    path = tmp_path / 'large.png'
    path.write_bytes(b'fixture')
    calls = []
    data = ImageData(path, np.zeros((10, 10, 4), dtype=np.uint8), False)
    def decode(path, **kwargs):
        calls.append(path)
        return data
    monkeypatch.setattr(workers, 'decode_image', decode)
    service = ImageService(budget=10)
    one = service((path, 0, 'RGBA', 'Reinhard'), lambda: False)
    two = service((path, 0, 'A', 'Reinhard'), lambda: False)
    assert len(calls) == 1
    assert one['width'] == two['width'] == 10
    assert service.cache.bytes == 0
    assert two['rgba'][0, 0, 3] == 255
    path.write_bytes(b'fixture changed')
    service((path, 0, 'RGBA', 'Reinhard'), lambda: False)
    assert len(calls) == 2


def test_render_cache_avoids_repeat_decode_and_tonemap(tmp_path, monkeypatch):
    from luma.decoders import ImageData
    from luma.workers import ImageService
    import luma.workers as workers
    path = tmp_path / 'frame.png'; path.write_bytes(b'fixture')
    data = ImageData(path, np.ones((5, 6, 4), np.uint8), False)
    calls = {'decode': 0, 'render': 0}
    def decode(path, **kwargs):
        calls['decode'] += 1
        return data
    original = workers.render_rgba
    def render(*args, **kwargs):
        calls['render'] += 1
        return original(*args, **kwargs)
    monkeypatch.setattr(workers, 'decode_image', decode)
    monkeypatch.setattr(workers, 'render_rgba', render)
    service = ImageService(budget=200, render_budget=240)
    request = (path, 0, 'RGBA', 'Reinhard')
    first = service(request, lambda: False)
    second = service(request, lambda: False)
    assert calls == {'decode': 1, 'render': 1}
    assert first['rgba'] is second['rgba']
    service((path, 0, 'A', 'Reinhard'), lambda: False)
    assert calls == {'decode': 1, 'render': 2}
    assert service.render_cache.bytes <= service.render_cache.budget
    path.write_bytes(b'new fixture')
    service(request, lambda: False)
    assert calls == {'decode': 2, 'render': 3}


def test_idle_prefetch_never_emits_and_yields_to_latest_foreground(qapp):
    started, release = threading.Event(), threading.Event()
    cancelled_seen = []
    def idle(value, cancelled):
        started.set(); release.wait(2)
        cancelled_seen.append(cancelled())
        return 'must not display'
    worker = LatestWorker(lambda value, cancelled: value, idle_handler=idle)
    completed = []
    worker.completed.connect(lambda token, value: completed.append(value))
    try:
        worker.submit_idle('neighbor')
        assert started.wait(2)
        worker.submit('first')
        worker.submit('newest')
        release.set()
        wait_for(qapp, lambda: completed == ['newest'])
        assert cancelled_seen == [True]
    finally:
        release.set(); worker.close()


def test_prefetch_preserves_active_large_image_and_limits(tmp_path, monkeypatch):
    from PIL import Image
    from luma.workers import ImageService
    import luma.workers as workers
    paths = [tmp_path / f'{i}.jpg' for i in range(2)]
    for path in paths: Image.new('RGB', (10, 10), 'red').save(path)
    service = ImageService(budget=1000, render_budget=1000)
    service((paths[0], 0, 'RGBA', 'Reinhard'), lambda: False)
    active_key, active_image = service.current_key, service.current_image
    service.prefetch((paths[1], 0, 'RGBA', 'Reinhard'), lambda: False)
    assert service.current_key == active_key and service.current_image is active_image
    def forbidden(*args, **kwargs): raise AssertionError('Prefetched image decoded twice')
    monkeypatch.setattr(workers, 'decode_image', forbidden)
    assert service((paths[1], 0, 'RGBA', 'Reinhard'), lambda: False)['width'] == 10
    assert service.cache.bytes <= 1000 and service.render_cache.bytes <= 1000
    assert service.prefetch((tmp_path / 'large.exr', 0, 'RGBA', 'Reinhard'), lambda: False) is None


def test_cancelled_service_does_not_populate_caches(tmp_path, monkeypatch):
    from luma.workers import ImageService
    path = tmp_path / 'cancel.png'; path.write_bytes(b'fixture')
    service = ImageService()
    assert service((path, 0, 'RGBA', 'Reinhard'), lambda: True) is None
    assert service.cache.bytes == service.render_cache.bytes == 0
    assert service.current_image is None


def test_filmstrip_cache_is_bounded_and_reuses_bytes(tmp_path, monkeypatch):
    from PIL import Image
    from luma.workers import ThumbnailService
    path = tmp_path / 'thumb.jpg'; Image.new('RGB', (400, 300), 'blue').save(path)
    service = ThumbnailService(budget=100_000)
    first = service([path], lambda: False)
    def forbidden(*args, **kwargs): raise AssertionError('Cached thumbnail decoded again')
    monkeypatch.setattr(Image, 'open', forbidden)
    assert service([path], lambda: False) == first
    assert service.cache.bytes <= 100_000
