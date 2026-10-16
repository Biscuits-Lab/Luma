"""Bounded, latest-request-wins image work. No source file is ever changed."""
from __future__ import annotations

from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import os
from pathlib import Path
import re
import threading
from typing import Callable

import numpy as np
from PIL import Image, ImageOps
from PySide6.QtCore import QObject, Signal

from .decoders import (DecodeCancelled, ImageData, SUPPORTED_EXTENSIONS,
                       RAW_EXTENSIONS, decode_image, render_rgba)


_PARALLEL_RENDER_MIN_PIXELS = 8_000_000


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

    def __init__(self, handler, parent=None, *, idle_handler=None):
        super().__init__(parent)
        self.handler = handler
        self.idle_handler = idle_handler
        self._idle = None
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
            self._idle = None
            self._condition.notify()
            return self._generation

    def submit_idle(self, payload):
        """One replaceable, lower-priority prefetch slot; never emits UI results."""
        if self.idle_handler is None:
            return
        with self._condition:
            if not self._closed and self._pending is None:
                self._idle = (self._generation, payload)
                self._condition.notify()

    def cancel(self):
        with self._condition:
            self._generation += 1
            self._pending = None
            self._idle = None

    def current(self, token):
        with self._condition:
            return not self._closed and token == self._generation

    def close(self):
        with self._condition:
            self._closed = True
            self._pending = None
            self._idle = None
            self._condition.notify()

    def _run(self):
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._pending is not None or self._idle is not None or self._closed)
                if self._closed:
                    return
                idle = self._pending is None
                token, payload = self._idle if idle else self._pending
                if idle:
                    self._idle = None
                else:
                    self._pending = None
            try:
                handler = self.idle_handler if idle else self.handler
                result = handler(payload, lambda: not self.current(token))
                if not idle and self.current(token):
                    self.completed.emit(token, result)
            except Exception as error:
                if not idle and self.current(token):
                    self.failed.emit(token, str(error) or type(error).__name__)


@dataclass
class RenderedFrame:
    rgba: object
    width: int
    height: int
    linear: bool
    metadata: dict
    channel_names: tuple

    @property
    def nbytes(self):
        return self.rgba.nbytes


def render_display_rgba(data, exposure, channel, tone, cancelled):
    """Render exact full-resolution pixels with bounded parallel float bands.

    NumPy releases the GIL for these transforms. Large scene-linear images can
    use at most four render tasks, after native decoding has finished. Each task
    holds only one small output band plus the renderer's temporary workspace;
    the float source is shared read-only and the destination is allocated once.
    Small/SDR images keep the cheaper serial path. There is no task per scanline
    and no second full-size output or speculative background rendering.
    """
    workers = min(4, os.cpu_count() or 1)
    if not data.linear or data.width * data.height < _PARALLEL_RENDER_MIN_PIXELS or workers == 1:
        return render_rgba(data, exposure, channel, tone, cancelled=cancelled)
    if cancelled():
        raise DecodeCancelled('Image operation cancelled.')
    output = np.empty((data.height, data.width, 4), dtype=np.uint8)
    # At most one megapixel per task, including unusually wide source images.
    rows = max(1, min(128, 1_048_576 // data.width))
    stopped = threading.Event()
    failures = []
    failure_lock = threading.Lock()

    def is_cancelled():
        return stopped.is_set() or cancelled()

    def render_bands(index):
        try:
            for start in range(index * rows, data.height, workers * rows):
                if is_cancelled():
                    raise DecodeCancelled('Image operation cancelled.')
                block = ImageData(data.path, data.pixels[start:start + rows],
                                  data.linear, data.metadata, data.channel_names)
                output[start:start + rows] = render_rgba(
                    block, exposure, channel, tone, cancelled=is_cancelled)
        except Exception as error:
            # Preserve the original error, rather than a sibling's secondary
            # cancellation when one renderer fails parameter validation.
            with failure_lock:
                if not failures:
                    failures.append(error)
            stopped.set()
            raise

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix='Luma render') as executor:
        # Exactly one task per thread; pending image requests remain solely in
        # LatestWorker's replaceable slot. Cancellation joins only active bands.
        try:
            list(executor.map(render_bands, range(workers)))
        except Exception:
            if failures:
                raise failures[0]
            raise
    if cancelled():
        raise DecodeCancelled('Image operation cancelled.')
    return output


class ImageService:
    """Single-worker owned caches: decoded bytes plus separately bounded RGBA frames.

    Prefetch runs in the same worker only while foreground work is idle, so cache
    operations are serialized. A new request cancels cooperative work and replaces
    the one pending prefetch. Native Pillow loads cannot be interrupted mid-call.
    """
    def __init__(self, budget=256 * 1024 * 1024, render_budget=None):
        self.cache = ByteCache(budget)
        self.render_cache = ByteCache(budget // 2 if render_budget is None else render_budget)
        self.current_key = None
        self.current_image = None

    @staticmethod
    def _key(path):
        stat = path.stat()
        return str(path), stat.st_mtime_ns, stat.st_size

    def __call__(self, request, cancelled):
        return self._load(request, cancelled, activate=True)

    def _load(self, request, cancelled, *, activate):
        path, exposure, channel, tone = request
        if cancelled():
            return None
        key = self._key(path)
        render_key = (key, float(exposure), channel.upper(), tone.casefold())
        frame = self.render_cache.get(render_key)
        data = self.current_image if key == self.current_key else self.cache.get(key)
        if frame is None:
            if data is None:
                if activate and key != self.current_key:
                    # Navigation already cleared the old canvas. An uncached
                    # 8K float source can occupy a GiB; release it before the
                    # next native decode, rather than retaining two originals.
                    # Same-key edits and idle prefetch preserve the active data.
                    self.current_key, self.current_image = None, None
                data = decode_image(path, cancelled=cancelled)
                if cancelled() or self._key(path) != key:
                    return None
                self.cache.put(key, data)
            if cancelled():
                return None
            rgba = render_display_rgba(data, exposure, channel, tone, cancelled)
            if cancelled() or self._key(path) != key:
                return None
            frame = RenderedFrame(rgba, data.width, data.height, data.linear,
                                  data.metadata, data.channel_names)
            self.render_cache.put(render_key, frame)
        if activate:
            # Active oversized images stay available for repeated exposure edits.
            self.current_key, self.current_image = key, data
        if cancelled():
            return None
        return dict(path=path, rgba=frame.rgba, width=frame.width, height=frame.height,
                    linear=frame.linear, metadata=frame.metadata,
                    channel_names=frame.channel_names, cache_bytes=self.cache.bytes,
                    render_cache_bytes=self.render_cache.bytes)

    def prefetch(self, request, cancelled):
        path = request[0]
        # Keep speculative work small and quick. Never launch a full RAW/EXR/HDR
        # decode in front of the user's next request or retain two giant float images.
        if path.suffix.lower() not in {'.jpg', '.jpeg', '.jpe', '.jfif', '.png', '.webp'}:
            return None
        if cancelled() or path.stat().st_size > 32 * 1024 * 1024:
            return None
        with Image.open(path) as source:
            width, height = source.size
            byte_size = width * height * 4
            if width * height > 24_000_000 or byte_size > min(self.cache.budget, self.render_cache.budget):
                return None
        self._load(request, cancelled, activate=False)
        return None


def scan_folder(request, cancelled):
    directory, selected = request
    files = folder_images(directory, cancelled)
    return directory, files, selected


@dataclass
class ThumbnailData:
    size: tuple
    pixels: bytes

    @property
    def nbytes(self):
        return len(self.pixels)


class ThumbnailService:
    def __init__(self, budget=8 * 1024 * 1024):
        self.cache = ByteCache(budget)

    def __call__(self, paths, cancelled):
        return thumbnail_batch(paths, cancelled, cache=self.cache)


def thumbnail_batch(paths, cancelled, *, cache=None):
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
            stat = path.stat()
            key = (str(path), stat.st_mtime_ns, stat.st_size)
            cached = cache.get(key) if cache is not None else None
            if cached is not None:
                result[str(path)] = (cached.size, cached.pixels)
                continue
            with Image.open(path) as source:
                if source.width * source.height > 32_000_000:
                    continue
                source.draft('RGB', (160, 110))
                thumb = ImageOps.exif_transpose(source)
                thumb.thumbnail((160, 110))
                rgba = thumb.convert('RGBA')
                pixels = rgba.tobytes()
                result[str(path)] = (rgba.size, pixels)
                if cache is not None and not cancelled():
                    cache.put(key, ThumbnailData(rgba.size, pixels))
        except (OSError, ValueError, Image.DecompressionBombError):
            pass
    return result
