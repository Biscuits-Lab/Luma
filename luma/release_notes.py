"""Bounded, version-matched release notes; explicit disclosure starts network I/O."""
from collections import OrderedDict
import json
import threading
import time
from urllib.parse import quote

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QPushButton, QTextBrowser

from .i18n import tr
from .settings_widgets import DetailsPanel, plain_label

MAX_NOTE_CHARACTERS = 32_000
CACHE_TTL = 600
_CACHE = OrderedDict()
_CACHE_LOCK = threading.Lock()


class NotesError(Exception):
    pass


def bounded_body(body):
    """Never interpret HTML, links, image directives or control characters."""
    if body is None:
        return ''
    if not isinstance(body, str):
        raise NotesError('Invalid release body')
    return ''.join(c for c in body[:MAX_NOTE_CHARACTERS] if c in '\n\t' or ord(c) >= 32)


def cache_notes(version, body):
    body = bounded_body(body)
    with _CACHE_LOCK:
        _CACHE[version] = (time.monotonic(), body)
        _CACHE.move_to_end(version)
        while len(_CACHE) > 8:
            _CACHE.popitem(last=False)
    return body


def fetch_notes(version, *, cancelled=lambda: False, opener=None):
    from .updater import Version, REPOSITORY, RELEASES_URL, MAX_METADATA_BYTES, _fetch_bytes
    version = str(Version.parse(version))
    with _CACHE_LOCK:
        cached = _CACHE.get(version)
        if cached and time.monotonic() - cached[0] < CACHE_TTL:
            _CACHE.move_to_end(version)
            return cached[1]
    tag = 'v' + version
    url = f'https://api.github.com/repos/{REPOSITORY}/releases/tags/{quote(tag, safe="")}'
    raw = _fetch_bytes(url, MAX_METADATA_BYTES, cancelled=cancelled, opener=opener)
    try:
        data = json.loads(raw)
        if (not isinstance(data, dict) or data.get('tag_name') != tag
                or data.get('html_url') != f'{RELEASES_URL}/tag/{quote(tag, safe="")}'
                or data.get('draft') is not False or data.get('prerelease') is not False):
            raise ValueError('Mismatched or unpublished release notes')
        body = bounded_body(data.get('body'))
    except (ValueError, TypeError, UnicodeError) as error:
        raise NotesError('Invalid release notes response') from error
    if cancelled():
        raise NotesError('Release notes request cancelled')
    return cache_notes(version, body)


class _NotesSignals(QObject):
    done = Signal(int, str, object, bool)


class ReleaseNotesPanel(DetailsPanel):
    """Lazy, bounded plain-text viewer, independent of download/apply state."""
    def __init__(self, version, parent=None):
        super().__init__(tr('About updates'), parent)
        self.version = version
        self._serial = 0
        self._cancel = threading.Event()
        self._loaded = False
        self._busy = False
        self._signals = _NotesSignals(self)
        self._signals.done.connect(self._completed)
        self.version_label = plain_label(tr('Release notes · {version}', version=version), muted=True)
        self.body.addWidget(self.version_label)
        self.text = QTextBrowser()
        self.text.setAccessibleName(tr('About updates'))
        self.text.setOpenLinks(False)
        self.text.setOpenExternalLinks(False)
        self.text.setFixedHeight(160)
        self.text.setStyleSheet('QTextBrowser { border: 0; background: transparent; color: #bdbdbd; }')
        self.body.addWidget(self.text)
        self.retry = QPushButton(tr('Retry'))
        self.retry.setAutoDefault(False)
        self.retry.clicked.connect(self._request)
        self.retry.hide()
        self.body.addWidget(self.retry)
        self.toggle.toggled.connect(self._expanded)
        self._set_text(tr('Expand to load this version’s release notes.'))

    def _set_text(self, text):
        # setPlainText prevents all HTML/resource loading and link activation.
        self.text.setPlainText(text)

    def cancel_pending(self):
        self._cancel.set()
        self._serial += 1
        self._busy = False

    def set_release(self, version, body=None):
        self.cancel_pending()
        self.version = str(version)
        self.version_label.setText(tr('Release notes · {version}', version=self.version))
        self._loaded = False
        self.retry.hide()
        if body is not None:
            self._show_body(cache_notes(self.version, body))
        else:
            self._set_text(tr('Expand to load this version’s release notes.'))
            if self.toggle.isChecked():
                self._request()

    def _show_body(self, body):
        self._loaded = True
        self._set_text(body if body.strip() else tr('No release notes were provided for this version.'))
        if len(body) >= MAX_NOTE_CHARACTERS:
            self.text.append(tr('The notes were shortened for display.'))
        self.retry.hide()

    def _expanded(self, checked):
        if checked and not self._loaded:
            self._request()

    def _request(self):
        if self._busy:
            return
        self.cancel_pending()
        self._cancel = threading.Event()
        self._busy = True
        serial, version, cancelled = self._serial, self.version, self._cancel.is_set
        signals = self._signals
        self.retry.hide()
        self._set_text(tr('Loading release notes…'))
        def run():
            try:
                body = fetch_notes(version, cancelled=cancelled)
                failed = False
            except Exception:
                body, failed = None, True
            if not cancelled():
                try:
                    signals.done.emit(serial, version, body, failed)
                except RuntimeError:
                    pass  # The Settings dialog was closed during I/O.
        threading.Thread(target=run, daemon=True, name='Luma release notes').start()

    def _completed(self, serial, version, body, failed):
        if serial != self._serial or version != self.version or self._cancel.is_set():
            return
        self._busy = False
        if failed:
            self._set_text(tr('Release notes are unavailable. Check your connection or try again later.'))
            self.retry.show()
        else:
            self._show_body(body)
