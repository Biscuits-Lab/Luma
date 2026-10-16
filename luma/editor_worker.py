"""One cancellable renderer, with one latest pending recipe and no image queue."""
from dataclasses import dataclass
from pathlib import Path
import threading

from PySide6.QtCore import QObject, Signal

from PIL import Image
from .editor_core import EditorCancelled, EditState, render, export_image, bounded_size


@dataclass(frozen=True)
class RenderJob:
    token: int
    state: EditState
    include_crop: bool = True
    interactive: bool = False
    export: tuple[Path, Path, str, object] | None = None


class EditorRenderer(QObject):
    finished = Signal(int, object, str)

    def __init__(self, source, parent=None):
        super().__init__(parent)
        self._source = source
        self._preview_source = None
        self._interactive_source = None
        self._condition = threading.Condition()
        self._pending = None
        self._token = 0
        self._closed = False
        self._thread = threading.Thread(target=self._run, name='LumaLocalEditor', daemon=True)
        self._thread.start()

    @property
    def token(self):
        return self._token

    def request(self, state, *, include_crop=True, interactive=False, export=None):
        with self._condition:
            if self._closed:
                return self._token
            self._token += 1
            self._pending = RenderJob(self._token, state, include_crop, interactive, export)
            self._condition.notify()
            return self._token

    def close(self):
        with self._condition:
            self._closed = True
            self._token += 1
            self._pending = None
            self._condition.notify()

    def _cancelled(self, token):
        return self._closed or token != self._token

    def _run(self):
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._closed or self._pending is not None)
                if self._closed:
                    self._source = None
                    self._preview_source = None
                    self._interactive_source = None
                    return
                job, self._pending = self._pending, None
            try:
                cancelled = lambda: self._cancelled(job.token)
                if job.export:
                    destination, source_path, format, expected = job.export
                    result = export_image(self._source, job.state, destination, source_path,
                                          format, expected=expected, cancelled=cancelled)
                else:
                    if self._preview_source is None:
                        self._preview_source = self._source.resize(bounded_size(self._source.size), Image.Resampling.LANCZOS)
                    source = self._preview_source
                    if job.interactive:
                        if self._interactive_source is None:
                            self._interactive_source = self._preview_source.resize(bounded_size(self._preview_source.size, 800, 480_000), Image.Resampling.LANCZOS)
                        source = self._interactive_source
                    result = render(source, job.state, preview=True,
                                    include_crop=job.include_crop, cancelled=cancelled)
                if not cancelled():
                    self.finished.emit(job.token, result, '')
            except EditorCancelled:
                pass
            except Exception as error:
                if not self._cancelled(job.token):
                    self.finished.emit(job.token, None, str(error))
