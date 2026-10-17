"""Display-only area-filtered mip levels; the original image is never replaced.

Qt 6.12 routes positive SmoothTransformation scales of premultiplied ARGB32
through QImage::smoothScaled / qt_qimageScaleAARGBA. Its reduction kernel sums
the complete source footprint, including fractional edges and premultiplied
alpha, rather than using QPainter's four-sample bilinear minification:
https://github.com/qt/qtbase/blob/v6.12.0/src/gui/image/qimage.cpp
https://github.com/qt/qtbase/blob/v6.12.0/src/gui/painting/qimagescale.cpp

Filtering intentionally stays in the already-rendered display color space.
There is no new tone map, gamma conversion, sharpening, or source-file edit.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
import math
import threading

from PySide6.QtCore import QObject, Qt, Signal, Slot, QTimer
from PySide6.QtGui import QImage


DEFAULT_CACHE_BYTES = 64 * 1024 * 1024


class ReductionCancelled(Exception):
    """Cooperative cancellation at a native reduction boundary."""


def area_downsample(source: QImage, width: int, height: int,
                    cancelled=lambda: False) -> QImage:
    """Area-average into an owning, premultiplied display image.

The renderer supplies owning ARGB32_Premultiplied pixels. On that native path
Qt allocates only the target and small scaling tables, not a converted full
source. Other Qt formats are accepted for small/general-purpose callers, but
may require Qt's internal source conversion. A same-size request is a shared,
copy-on-write no-op. The input's pixels, color-space tag and DPR are untouched.

Native scaling itself cannot be interrupted. Check cancellation on both sides
of that bounded stage; the cache also checks it between successive levels.
"""
    if cancelled():
        raise ReductionCancelled()
    if source.isNull():
        return QImage()
    if not 1 <= width <= source.width() or not 1 <= height <= source.height():
        raise ValueError('Area reduction requires positive, non-enlarged dimensions.')
    if (width, height) == (source.width(), source.height()):
        return QImage(source)
    result = source.scaled(width, height, Qt.AspectRatioMode.IgnoreAspectRatio,
                           Qt.TransformationMode.SmoothTransformation)
    if cancelled():
        raise ReductionCancelled()
    if result.isNull():
        raise MemoryError('Could not allocate display minification level.')
    if result.format() != QImage.Format.Format_ARGB32_Premultiplied:
        result = result.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        if result.isNull():
            raise MemoryError('Could not prepare display minification level.')
    if cancelled():
        raise ReductionCancelled()
    return result


@dataclass
class _Job:
    generation: int
    level: int
    source: QImage
    width: int
    height: int
    cancel: threading.Event = field(default_factory=threading.Event)


class _ReductionWorker:
    """One plain Python worker/result slot; never owns or signals a QObject."""

    def __init__(self):
        # A native call may outlive its canvas. No QObject wrapper crosses that
        # lifetime boundary; the GUI owns a bounded polling timer instead.
        self._condition = threading.Condition()
        self._job = None
        self._result = None
        self._closed = False
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name='Luma display minification')
        self._thread.start()

    def submit(self, job):
        with self._condition:
            if not self._closed:
                self._job = job
                self._condition.notify()

    def take_result(self):
        with self._condition:
            result, self._result = self._result, None
            return result

    def close(self):
        with self._condition:
            self._closed = True
            if self._job is not None:
                self._job.cancel.set()
            self._job = self._result = None
            self._condition.notify()

    def _run(self):
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._closed or self._job is not None)
                if self._closed:
                    return
                job, self._job = self._job, None
            try:
                image = area_downsample(job.source, job.width, job.height, job.cancel.is_set)
            except Exception:
                # An allocation failure leaves the original display available.
                # Cancellation is also represented by a missing result.
                image = None
            outcome = (job.generation, job.level, image)
            # Never retain an obsolete full source in an idle thread frame.
            del job, image
            with self._condition:
                if self._closed:
                    return
                self._result = outcome
            del outcome


class MinificationCache(QObject):
    """Lazy, asynchronous 1/2-scale hierarchy with an exact byte-bounded LRU.

Call all public methods on the GUI thread. ``scale`` is physical output pixels
per original image pixel, after display DPR and any source DPR adjustment.
``choose`` never selects a level smaller than the required physical density;
it returns None at actual size/upscaling and until a usable level is ready.

Only requested levels and their useful ancestors are generated. Oversized
ancestors are skipped using one direct *area-filtered* reduction from source.
Retained levels use at most ``budget`` bytes, plus at most one in-flight or
awaiting-delivery target of at most ``budget`` bytes. The original is shared
read-only. Replacing it may retain one previous source until the current
native stage returns; replacement requests do not form a queue. Qt's small
native scaling tables and the original display's storage are outside budget.
"""
    changed = Signal()

    def __init__(self, parent=None, *, budget=DEFAULT_CACHE_BYTES):
        super().__init__(parent)
        self.budget = max(0, int(budget))
        self._source = QImage()
        self._generation = 0
        self._desired = 0
        self._levels = OrderedDict()
        self._bytes = 0
        self._failed = set()
        self._active = None
        self._worker = None
        self._closed = False
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(5)
        self._poll_timer.timeout.connect(self._completed)

    @property
    def cached_bytes(self):
        return self._bytes

    @property
    def cached_levels(self):
        """Available hierarchy indices (1 means half size), finest first."""
        return tuple(sorted(self._levels))

    def set_source(self, image: QImage):
        if self._closed or image.cacheKey() == self._source.cacheKey():
            return
        self._generation += 1
        self._source = QImage(image)
        self._desired = 0
        self._levels.clear()
        self._bytes = 0
        self._failed.clear()
        if self._active is not None:
            self._active.cancel.set()
        self.changed.emit()

    def _level_for(self, scale):
        if self._closed or self._source.isNull():
            return 0
        try:
            scale = float(scale)
        except (TypeError, ValueError):
            return 0
        if not math.isfinite(scale) or not 0 < scale < 1:
            return 0
        maximum = (max(self._source.width(), self._source.height()) - 1).bit_length()
        return min(maximum, max(0, math.floor(-math.log2(scale))))

    def _size(self, level):
        divisor = 1 << level
        # Ceil retains the last source row/column and never invents zero axes.
        return ((self._source.width() + divisor - 1) // divisor,
                (self._source.height() + divisor - 1) // divisor)

    def request(self, scale):
        self._desired = self._level_for(scale)
        self._schedule()

    def choose(self, scale) -> QImage | None:
        level = self._level_for(scale)
        candidates = [key for key in self._levels if key <= level]
        if not candidates:
            return None
        key = max(candidates)
        self._levels.move_to_end(key)
        # A shared wrapper protects the cached QImage object from a caller's
        # later detach/mutation without duplicating backing pixel storage.
        return QImage(self._levels[key])

    def _schedule(self):
        if self._closed:
            return
        if self._active is not None:
            if (self._active.generation != self._generation or not self._desired
                    or self._desired in self._levels or self._active.level > self._desired):
                self._active.cancel.set()
            return
        if not self._desired or self._desired in self._levels:
            return
        ancestors = [key for key in self._levels if key < self._desired]
        ancestor = max(ancestors, default=0)
        level = ancestor + 1
        while level <= self._desired:
            width, height = self._size(level)
            if width * height * 4 <= self.budget and level not in self._failed:
                break
            level += 1
        else:
            return
        source = self._levels[ancestor] if ancestor else self._source
        self._active = _Job(self._generation, level, QImage(source), width, height)
        if self._worker is None:
            self._worker = _ReductionWorker()
            self.destroyed.connect(self._worker.close)
        self._worker.submit(self._active)
        self._poll_timer.start()

    @Slot()
    def _completed(self):
        outcome = self._worker.take_result()
        if outcome is None:
            return
        generation, level, image = outcome
        current = self._active
        self._active = None
        valid = (not self._closed and current is not None and not current.cancel.is_set()
                 and generation == self._generation)
        notify = False
        if valid and image is not None:
            size = image.sizeInBytes()
            if size <= self.budget:
                while self._levels and self._bytes + size > self.budget:
                    _, removed = self._levels.popitem(last=False)
                    self._bytes -= removed.sizeInBytes()
                    del removed
                self._levels[level] = image
                self._bytes += size
                notify = True
        elif valid:
            self._failed.add(level)
        # Release a stale result and its old source before allowing a new job.
        del image, outcome, current
        # Listeners may request or process another frame reentrantly. Drop old
        # buffers before publishing the completed level or scheduling more work.
        if notify:
            self.changed.emit()
        self._schedule()
        if self._active is None:
            self._poll_timer.stop()

    def close(self):
        """Cancel without waiting on native code or delaying window shutdown."""
        if self._closed:
            return
        self._closed = True
        self._poll_timer.stop()
        if self._active is not None:
            self._active.cancel.set()
        self._active = None
        self._source = QImage()
        self._levels.clear()
        self._bytes = 0
        if self._worker is not None:
            self._worker.close()
