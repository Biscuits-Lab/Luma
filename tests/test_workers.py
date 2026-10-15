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
    def decode(path):
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
