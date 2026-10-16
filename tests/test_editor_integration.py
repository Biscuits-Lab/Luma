"""The viewer enters editing only from an exact display, retaining its source."""
from types import SimpleNamespace
from unittest.mock import Mock

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
    executed = Mock()
    connect = Mock()
    factory = Mock(return_value=SimpleNamespace(exported=SimpleNamespace(connect=connect), exec=executed))
    monkeypatch.setattr(editor, 'ImageEditorDialog', factory)
    try:
        qapp.processEvents()
        assert window.edit_button.isEnabled()
        assert window.rotate_button.x() < window.edit_button.x() < window.delete_button.x()
        assert window.edit_button.accessibleName()
        window.edit_button.click()
        executed.assert_called_once_with()
        supplied, source, parent = factory.call_args.args
        assert supplied == image
        assert source == path and parent is window
        assert factory.call_args.kwargs == {'display_referred': linear}
        connect.call_args.args[0](str(tmp_path / 'copy.png'))
        assert 'copy.png' in window.toast.text()
        assert window.status_label.toolTip() == 'EXR | 80 × 60 | 29 B'
        assert window.canvas.displayed_image() == image
        assert path.read_bytes() == b'original synthetic image bytes'
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
