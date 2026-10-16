from PIL import Image
from PySide6.QtTest import QTest
from luma.app import ViewerWindow
from test_ui import wait_for


def test_hidden_filmstrip_submits_no_thumbnail_decodes(qapp, tmp_path, monkeypatch):
    for i in range(3): Image.new('RGB', (80, 60), (i * 70, 10, 20)).save(tmp_path / f'{i}.jpg')
    win = ViewerWindow(); win.show()
    submitted = []
    original = win.thumbnails.submit
    original_scroll = win.filmstrip.scrollToItem
    def scroll(*args):
        assert win.filmstrip.isVisible(), 'Hidden filmstrip forced expensive item layout'
        return original_scroll(*args)
    monkeypatch.setattr(win.filmstrip, 'scrollToItem', scroll)
    def track(paths):
        submitted.append(paths)
        return original(paths)
    monkeypatch.setattr(win.thumbnails, 'submit', track)
    try:
        win.open_path(tmp_path / '0.jpg')
        wait_for(qapp, lambda: win.displayed_path == tmp_path / '0.jpg' and len(win.files) == 3)
        win.navigate(1)
        wait_for(qapp, lambda: win.displayed_path == tmp_path / '1.jpg')
        assert submitted == []
        assert win.filmstrip.count() == 3
        win.strip_button.click()
        wait_for(qapp, lambda: bool(submitted))
        assert win.filmstrip.isVisible()
    finally:
        win.close()


def test_idle_prefetch_does_not_replace_displayed_image(qapp, tmp_path):
    for i in range(2): Image.new('RGB', (80, 60), (i * 200, 10, 20)).save(tmp_path / f'{i}.jpg')
    win = ViewerWindow(); win.show()
    try:
        win.open_path(tmp_path / '0.jpg')
        wait_for(qapp, lambda: win.displayed_path == tmp_path / '0.jpg' and len(win.files) == 2)
        original = win.canvas.item.pixmap().toImage().copy()
        wait_for(qapp, lambda: len(win.service.render_cache.entries) == 2)
        assert win.displayed_path == win.path == tmp_path / '0.jpg'
        assert win.canvas.item.pixmap().toImage() == original
        win.navigate(1)
        wait_for(qapp, lambda: win.displayed_path == tmp_path / '1.jpg')
        assert win.dimension_label.text() == '80 × 60'
    finally:
        win.close()


def test_fast_cached_switch_has_no_loading_indicator_flash(qapp, tmp_path, monkeypatch):
    for i in range(2): Image.new('RGB', (80, 60), (i * 200, 10, 20)).save(tmp_path / f'{i}.jpg')
    win = ViewerWindow(); win.show()
    shown = []
    real_show = win.cancel_button.show
    monkeypatch.setattr(win.cancel_button, 'show', lambda: (shown.append(True), real_show())[-1])
    try:
        win.open_path(tmp_path / '0.jpg')
        wait_for(qapp, lambda: win.displayed_path == tmp_path / '0.jpg' and len(win.files) == 2)
        wait_for(qapp, lambda: len(win.service.render_cache.entries) == 2)
        shown.clear()
        win.navigate(1)
        wait_for(qapp, lambda: win.displayed_path == tmp_path / '1.jpg')
        assert shown == []
        assert not win.busy_timer.isActive() and not win._pending_render
        assert not win.cancel_button.isVisible()
    finally:
        win.close()
