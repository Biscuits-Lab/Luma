"""Bounded, latest-request-wins image work. No source file is ever changed."""
from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
import re
import threading
from typing import Callable

from PIL import Image, ImageOps
from PySide6.QtCore import QObject, Signal

from .decoders import ImageData, SUPPORTED_EXTENSIONS, RAW_EXTENSIONS, decode_image, render_rgba


class ByteCache:
    """LRU with an exact ndarray-byte budget; oversized entries are never cached."""
    def __init__(self, budget: int = 256 * 1024 * 1024):
        self.budget = budget
        self.bytes = 0
        self.entries = OrderedDict()

    def get(self, key):
        if key not in self.entries:
            return None
        self.entries.move_to_end(key)
        return self.entries[key]

    def put(self, key, value):
        old = self.entries.pop(key, None)
        if old is not None:
            self.bytes -= old.nbytes
        if value.nbytes > self.budget:
            return
        while self.entries and self.bytes + value.nbytes > self.budget:
            _, removed = self.entries.popitem(last=False)
            self.bytes -= removed.nbytes
        self.entries[key] = value
        self.bytes += value.nbytes


def natural_key(path: Path):
    return tuple((1, int(p)) if p.isdigit() else (0, p.casefold())
                 for p in re.split(r"(\d+)", path.name))


def folder_images(path: Path, cancelled: Callable[[], bool] = lambda: False):
    result = []
    for item in path.iterdir():
        if cancelled():
            return []
        if item.suffix.lower() in SUPPORTED_EXTENSIONS and item.is_file():
            result.append(item)
            if len(result) >= 100_000:
                raise ValueError("This folder exceeds the 100,000-image safety limit.")
    return sorted(result, key=natural_key)


class LatestWorker(QObject):
    """One executing task and one pending slot, instead of an unbounded task queue.

    Native decoders cannot safely be interrupted in-process. Cancel discards their
    result and replaces pending work; it does not claim to terminate native code.
    Threads are daemonized so a stuck decoder cannot hold application shutdown.
    """
    completed = Signal(int, object)
    failed = Signal(int, str)

    def __init__(self, handler, parent=None):
        super().__init__(parent)
        self.handler = handler
        self._condition = threading.Condition()
        self._pending = None
        self._generation = 0
        self._closed = False
        self._thread = threading.Thread(target=self._run, daemon=True, name="Luma worker")
        self._thread.start()

    def submit(self, payload) -> int:
        with self._condition:
            self._generation += 1
            self._pending = (self._generation, payload)
            self._condition.notify()
            return self._generation

    def cancel(self):
        with self._condition:
            self._generation += 1
            self._pending = None

    def current(self, token):
        with self._condition:
            return not self._closed and token == self._generation

    def close(self):
        with self._condition:
            self._closed = True
            self._pending = None
            self._condition.notify()

    def _run(self):
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._pending is not None or self._closed)
                if self._closed:
                    return
                token, payload = self._pending
                self._pending = None
            try:
                result = self.handler(payload, lambda: not self.current(token))
                if self.current(token):
                    self.completed.emit(token, result)
            except Exception as error:
                if self.current(token):
                    self.failed.emit(token, str(error) or type(error).__name__)


class ImageService:
    def __init__(self, budget=256 * 1024 * 1024):
        self.cache = ByteCache(budget)
        self.current_key = None
        self.current_image = None

    def __call__(self, request, cancelled):
        path, exposure, channel, tone = request
        stat = path.stat()
        key = (str(path), stat.st_mtime_ns, stat.st_size)
        data = self.current_image if key == self.current_key else self.cache.get(key)
        if data is None:
            data = decode_image(path)
            if cancelled():
                return None
            self.cache.put(key, data)
        # One active image is separate from the bounded navigation cache.
        # This lets exposure changes reuse a large RAW that exceeds that cache.
        self.current_key, self.current_image = key, data
        if cancelled():
            return None
        rgba = render_rgba(data, exposure, channel, tone)
        return dict(path=path, rgba=rgba, width=data.width, height=data.height,
                    linear=data.linear, metadata=data.metadata,
                    channel_names=data.channel_names, cache_bytes=self.cache.bytes)


def scan_folder(request, cancelled):
    directory, selected = request
    files = folder_images(directory, cancelled)
    return directory, files, selected


def thumbnail_batch(paths, cancelled):
    """Small thumbnails for Pillow formats. RAW/HDR get a labelled placeholder.

    Loading a second full-resolution RAW/EXR solely for a thumbnail is deliberately
    avoided. The selected image receives its real rendered thumbnail in the UI.
    """
    result = {}
    for path in paths:
        if cancelled():
            break
        if path.suffix.lower() in RAW_EXTENSIONS | {'.exr', '.hdr', '.rgbe', '.pic'}:
            continue
        try:
            with Image.open(path) as source:
                if source.width * source.height > 32_000_000:
                    continue
                source.draft('RGB', (160, 110))
                thumb = ImageOps.exif_transpose(source)
                thumb.thumbnail((160, 110))
                rgba = thumb.convert('RGBA')
                result[str(path)] = (rgba.size, rgba.tobytes())
        except (OSError, ValueError, Image.DecompressionBombError):
            pass
    return result
