"""Exact-version release notes, bounded plain-text rendering and lazy I/O."""
import json
import threading
import time

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from luma import release_notes as notes, updater
from luma.i18n import set_language


@pytest.fixture(autouse=True)
def empty_cache():
    with notes._CACHE_LOCK:
        notes._CACHE.clear()
    yield
    with notes._CACHE_LOCK:
        notes._CACHE.clear()
    set_language('en')


def payload(version='0.1.3', body='## Fixed\n- Image previews'):
    return dict(tag_name='v'+version, html_url=updater.RELEASES_URL+'/tag/v'+version,
                draft=False, prerelease=False, assets=[], body=body)


def wait(qapp, predicate):
    end = time.monotonic() + 3
    while not predicate() and time.monotonic() < end:
        qapp.processEvents()
        time.sleep(.005)
    assert predicate()


def test_notes_fetch_exact_official_version_and_cache(monkeypatch):
    calls = []
    def fetch(url, limit, **kwargs):
        calls.append((url, limit))
        return json.dumps(payload()).encode()
    monkeypatch.setattr(updater, '_fetch_bytes', fetch)
    assert notes.fetch_notes('0.1.3') == '## Fixed\n- Image previews'
    assert notes.fetch_notes('0.1.3') == '## Fixed\n- Image previews'
    assert calls == [('https://api.github.com/repos/Biscuits-Lab/Luma/releases/tags/v0.1.3', updater.MAX_METADATA_BYTES)]
    monkeypatch.setattr(notes.time, 'monotonic', lambda: 10**20)
    notes.fetch_notes('0.1.3')
    assert len(calls) == 2


@pytest.mark.parametrize('change', [dict(tag_name='v0.1.2'), dict(html_url='https://example.com'),
                                   dict(draft=True), dict(prerelease=True), dict(body=[])] )
def test_wrong_or_invalid_release_is_never_shown_or_cached(monkeypatch, change):
    data = payload(); data.update(change)
    monkeypatch.setattr(updater, '_fetch_bytes', lambda *a, **k: json.dumps(data).encode())
    with pytest.raises(notes.NotesError):
        notes.fetch_notes('0.1.3')
    assert not notes._CACHE


@pytest.mark.parametrize('body', [None, ''])
def test_empty_release_notes_are_real_empty_state(monkeypatch, body):
    monkeypatch.setattr(updater, '_fetch_bytes', lambda *a, **k: json.dumps(payload(body=body)).encode())
    assert notes.fetch_notes('0.1.3') == ''


def test_notes_cache_bounded_and_body_limited():
    for i in range(20):
        notes.cache_notes(f'0.0.{i}', 'x' * 100_000)
    assert len(notes._CACHE) == 8
    assert all(len(body) == notes.MAX_NOTE_CHARACTERS for _, body in notes._CACHE.values())
    assert notes.bounded_body('\x00\x01A\n\tB') == 'A\n\tB'


def test_no_network_until_explicit_expansion_and_readable_empty_state(qapp, monkeypatch):
    calls = []
    monkeypatch.setattr(notes, 'fetch_notes', lambda version, **kwargs: calls.append(version) or '')
    panel = notes.ReleaseNotesPanel('0.1.3')
    panel.show()
    try:
        qapp.processEvents(); assert calls == []
        panel.setExpanded(True)
        wait(qapp, lambda: panel._loaded)
        assert calls == ['0.1.3']
        assert 'No release notes' in panel.text.toPlainText()
        panel.setExpanded(False); panel.setExpanded(True)
        assert calls == ['0.1.3']
    finally:
        panel.cancel_pending(); panel.close()


def test_markup_and_remote_resources_remain_inert_plain_text(qapp, monkeypatch):
    panel = notes.ReleaseNotesPanel('0.1.3')
    raw = '<script>alert(1)</script>\n<img src="https://example.com/private.png">\n[file](file:///secret)'
    panel.set_release('0.1.3', raw)
    try:
        assert panel.text.toPlainText() == raw
        assert not panel.text.openLinks() and not panel.text.openExternalLinks()
        assert '&lt;img' in panel.text.toHtml()
        assert 'src="https://example.com/private.png"' not in panel.text.toHtml()
    finally:
        panel.close()


def test_failures_are_retryable_and_cannot_replace_newer_version(qapp, monkeypatch):
    entered, finish = threading.Event(), threading.Event()
    def fetch(version, **kwargs):
        entered.set(); finish.wait(2); raise OSError('private network diagnostic')
    monkeypatch.setattr(notes, 'fetch_notes', fetch)
    panel = notes.ReleaseNotesPanel('0.1.2')
    panel.setExpanded(True)
    assert entered.wait(1)
    old_serial = panel._serial
    panel.set_release('0.1.3', 'Actual new notes')
    finish.set()
    panel._completed(old_serial, '0.1.2', None, True)
    assert panel.text.toPlainText() == 'Actual new notes'
    assert panel.version_label.text().endswith('0.1.3')
    monkeypatch.setattr(notes, 'fetch_notes', lambda *a, **k: (_ for _ in ()).throw(OSError('private')))
    panel.set_release('0.1.4')
    wait(qapp, lambda: not panel._busy)
    assert 'unavailable' in panel.text.toPlainText()
    assert 'private' not in panel.text.toPlainText()
    assert not panel.retry.isHidden()
    panel.cancel_pending(); panel.close()


@pytest.mark.parametrize('language', ['en', 'zh-CN', 'zh-TW'])
def test_dialog_notes_match_discovered_target_without_additional_fetch(qapp, monkeypatch, language):
    set_language(language)
    monkeypatch.setattr(notes, 'fetch_notes', lambda *a, **k: pytest.fail('Unnecessary network'))
    dialog = updater.UpdateDialog(None, '0.1.2')
    try:
        release = updater.parse_release(payload('0.1.3', '# Actual release body'))
        dialog._completed(dialog._serial, release)
        assert dialog.update_details.version == '0.1.3'
        assert dialog.update_details.text.toPlainText() == '# Actual release body'
        dialog.update_details.setExpanded(True)
        qapp.processEvents()
        assert dialog.check_button.isEnabled()
    finally:
        dialog.close()


def test_older_latest_does_not_label_old_notes_as_installed_version(qapp, monkeypatch):
    dialog = updater.UpdateDialog(None, '0.1.4')
    try:
        dialog._completed(dialog._serial, updater.parse_release(payload('0.1.3', 'Old notes')))
        assert dialog.update_details.version == '0.1.4'
        assert dialog.update_details.text.toPlainText() != 'Old notes'
    finally:
        dialog.close()
