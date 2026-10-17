"""The viewer enters editing only from an exact display, retaining its source."""
from types import SimpleNamespace
from unittest.mock import Mock
from dataclasses import replace
import time

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage

from luma.app import ViewerWindow
from luma import editor


@pytest.mark.parametrize('linear', [False, True])
def test_edit_button_uses_current_full_display_without_mutating_source(qapp, tmp_path, monkeypatch, linear):
    path = tmp_path / ('source.exr' if linear else 'source.png')
    path.write_bytes(b'original synthetic image bytes')
    window = ViewerWindow()
    window.show()
    image = QImage(80, 60, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.blue)
    window.canvas.set_image(image)
    window.stack.setCurrentWidget(window.canvas)
    window.path = window.displayed_path = path
    window.image_linear = linear
    window._set_controls(True, linear)
    window._metadata_status('EXR | 80 × 60 | 29 B')
    geometry, maximized = window.geometry(), window.isMaximized()
    try:
        qapp.processEvents()
        assert window.edit_button.isEnabled()
        assert window.rotate_button.x() < window.edit_button.x() < window.delete_button.x()
        assert window.edit_button.accessibleName()
        window.edit_button.click()
        active = window._editor
        assert active is not None and not active.isWindow()
        assert active.window() is window and active.source_path == path
        assert active.display_referred == linear
        assert active._source.size == (80, 60)
        assert active._source.getpixel((0, 0)) == (0, 0, 255, 255)
        assert window.stack.currentWidget() is active
        assert window.geometry() == geometry and window.isMaximized() == maximized
        window.edit_image()
        assert window._editor is active
        active.exported.emit(str(tmp_path / 'copy.png'))
        assert 'copy.png' in window.toast.text()
        assert window.status_label.toolTip() == 'EXR | 80 × 60 | 29 B'
        assert window.canvas.displayed_image() == image
        assert path.read_bytes() == b'original synthetic image bytes'
        active.reject()
        assert window._editor is None and window.stack.currentWidget() is window.canvas
        assert window.geometry() == geometry and window.isMaximized() == maximized
    finally:
        window.close()


@pytest.mark.parametrize('state', ['empty', 'loading', 'preview', 'stale'])
def test_editor_never_opens_from_incomplete_or_stale_display(qapp, tmp_path, monkeypatch, state):
    window = ViewerWindow()
    path = tmp_path / 'source.png'
    factory = Mock()
    monkeypatch.setattr(editor, 'ImageEditorDialog', factory)
    try:
        if state != 'empty':
            window.path = window.displayed_path = path
        if state == 'loading': window._pending_render = True
        if state == 'preview': window._display_is_preview = True
        if state == 'stale': window.displayed_path = tmp_path / 'previous.png'
        window.edit_image()
        factory.assert_not_called()
    finally:
        window.close()


def wait(qapp, predicate):
    deadline = time.monotonic() + 5
    while not predicate():
        qapp.processEvents()
        assert time.monotonic() < deadline
        time.sleep(.002)


def ready_window(qapp, tmp_path):
    from PIL import Image
    path = tmp_path / 'original.png'
    Image.new('RGBA', (640, 480), (20, 80, 160, 180)).save(path)
    window = ViewerWindow()
    window.resize(900, 620)
    window.show()
    window.open_path(path)
    wait(qapp, lambda: window.displayed_path == path and len(window.files) == 1)
    return window, path


@pytest.mark.parametrize('maximized', [False, True])
def test_inline_cancel_preserves_window_view_and_has_only_one_editor(qapp, tmp_path, maximized):
    window, path = ready_window(qapp, tmp_path)
    try:
        if maximized:
            window.showMaximized()
            qapp.processEvents()
        window.canvas.set_zoom_percent(230)
        window.canvas.centerOn(240, 190)
        qapp.processEvents()
        transform = window.canvas.transform()
        center = window.canvas.mapToScene(window.canvas.viewport().rect().center())
        geometry, state = window.geometry(), window.windowState()
        original = path.read_bytes()
        window.edit_image()
        active = window._editor
        wait(qapp, lambda: active._ready)
        assert not active.isWindow() and active.window() is window
        assert window.geometry() == geometry and window.windowState() == state
        assert window.stack.count() == 3
        for _ in range(4):
            window.edit_image()
        assert window._editor is active and window.stack.count() == 3
        active.reject()
        qapp.processEvents()
        assert window._editor is None and window.stack.count() == 2
        assert window.geometry() == geometry and window.windowState() == state
        assert window.canvas.transform() == transform
        now = window.canvas.mapToScene(window.canvas.viewport().rect().center())
        assert abs(now.x()-center.x()) <= 1 and abs(now.y()-center.y()) <= 1
        assert path.read_bytes() == original
    finally:
        window.close()


def test_inline_dirty_cancel_navigation_close_and_stale_callbacks_are_guarded(qapp, tmp_path, monkeypatch):
    from PIL import Image
    window, path = ready_window(qapp, tmp_path)
    second = tmp_path / 'second.png'
    Image.new('RGB', (64, 48), 'red').save(second)
    try:
        old_load, old_scan = window._load_token, window._scan_token
        window.edit_image()
        active = window._editor
        active._commit(replace(active.state, filter_name='vivid'))
        questions = []
        def cancel(*args, **kwargs):
            questions.append(True)
            return editor.QMessageBox.StandardButton.Cancel
        monkeypatch.setattr(editor.QMessageBox, 'question', cancel)
        active.reject()
        window.open_path(second)
        window.close()
        assert len(questions) == 3
        assert window.isVisible() and not window._closing
        assert window._editor is active and window.path == path
        window._loaded(old_load, {'path': path})
        window._load_failed(old_load, 'obsolete')
        window._scanned(old_scan, (tmp_path, [second], second))
        window._schedule_render()
        window._request_render()
        assert window._editor is active and window.stack.currentWidget() is active
        assert not window._pending_render and not window.render_timer.isActive()
        monkeypatch.setattr(editor.QMessageBox, 'question', lambda *a, **k: editor.QMessageBox.StandardButton.Discard)
        window.cancel_load()
        assert window._editor is None and window.stack.currentWidget() is window.canvas
        window.edit_image()
        current = window._editor
        window._editor_finished(active)
        assert window._editor is current
        current.reject()
        window.open_path(second)
        wait(qapp, lambda: window.displayed_path == second)
    finally:
        monkeypatch.setattr(editor.QMessageBox, 'question', lambda *a, **k: editor.QMessageBox.StandardButton.Discard)
        window.close()


def test_inline_save_is_async_protects_close_and_returns_to_viewer(qapp, tmp_path, monkeypatch):
    import threading
    from PIL import Image
    from luma import actions, editor_worker
    window, path = ready_window(qapp, tmp_path)
    original = path.read_bytes()
    destination = tmp_path / 'edited.png'
    class ChoiceDialog:
        def __init__(self, *a, **k):
            self.choice = actions.ExportChoice(destination, 'PNG')
            self.filename = SimpleNamespace(setText=lambda text: None)
        def exec(self):
            return editor.ImageEditorDialog.DialogCode.Accepted
    monkeypatch.setattr(actions, 'ExportDialog', ChoiceDialog)
    started, release = threading.Event(), threading.Event()
    export = editor_worker.export_image
    def delayed(*args, **kwargs):
        started.set()
        assert release.wait(3)
        return export(*args, **kwargs)
    monkeypatch.setattr(editor_worker, 'export_image', delayed)
    try:
        geometry = window.geometry()
        window.edit_image()
        active = window._editor
        active._commit(replace(active.state, crop=(.25, 0, .75, 1)))
        active._save_as()
        wait(qapp, started.is_set)
        window.close()
        active.reject()
        assert window._editor is active and not window._closing
        release.set()
        wait(qapp, lambda: window._editor is None)
        assert window.stack.currentWidget() is window.canvas
        assert window.geometry() == geometry
        with Image.open(destination) as saved:
            assert saved.size == (320, 480)
        assert path.read_bytes() == original
        assert window.displayed_path == path
    finally:
        release.set()
        window.close()


@pytest.mark.parametrize('language', ['en', 'zh-CN', 'zh-TW'])
def test_inline_editor_fits_minimum_window_without_clipping_actions(qapp, tmp_path, language):
    from PySide6.QtCore import QPoint, QRect
    from PySide6.QtWidgets import QPushButton
    from luma.i18n import set_language
    set_language(language)
    window, path = ready_window(qapp, tmp_path)
    try:
        window.resize(800, 560)
        qapp.processEvents()
        geometry = window.geometry()
        window.edit_image()
        active = window._editor
        qapp.processEvents()
        assert window.geometry() == geometry
        actions = active.tab_buttons + [active.save_button]
        actions += [b for b in active.findChildren(QPushButton) if b.text() == editor.et('Cancel')]
        assert len(actions) == 6
        for button in actions:
            bounds = QRect(button.mapTo(active, QPoint()), button.size())
            assert active.rect().contains(bounds), (language, button.text(), bounds)
        active.reject()
    finally:
        window.close()
