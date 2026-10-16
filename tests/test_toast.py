"""Canvas notifications preserve layout/input and safely replace older notices."""
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QPushButton, QSizePolicy, QVBoxLayout, QWidget

from luma.toast import CanvasToast


@pytest.fixture
def host(qapp):
    parent = QWidget()
    parent.resize(800, 500)
    layout = QVBoxLayout(parent)
    button = QPushButton('Canvas stand-in')
    button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
    layout.addWidget(button)
    parent.show()
    button.setFocus()
    qapp.processEvents()
    yield parent, button
    parent.close()
    parent.deleteLater()
    qapp.processEvents()


def test_notice_does_not_change_layout_or_focus(host, qapp):
    parent, button = host
    before = button.geometry()
    toast = CanvasToast(parent, reduced_motion=True)
    toast.show_message('Displayed image copied to clipboard')
    qapp.processEvents()
    assert button.geometry() == before
    assert button.hasFocus()
    assert parent.layout().indexOf(toast) == -1
    assert toast.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    assert toast.focusPolicy() == Qt.FocusPolicy.NoFocus
    assert toast.textFormat() == Qt.TextFormat.PlainText
    assert toast.accessibleName() == toast.text()
    assert toast._timer.interval() == 3000
    assert toast.y() == toast.MARGIN
    assert abs(toast.geometry().center().x() - parent.rect().center().x()) <= 1
    # Hit-testing passes through the overlay to the image area underneath.
    assert parent.childAt(toast.geometry().center()) is button


def test_repeated_message_restarts_owned_timer(host):
    toast = CanvasToast(host[0], reduced_motion=True)
    toast.show_message('First', duration_ms=80)
    QTest.qWait(50)
    toast.show_message('Second', duration_ms=180)
    QTest.qWait(65)
    assert toast.isVisible()
    assert toast.text() == 'Second'
    QTest.qWait(150)
    assert not toast.isVisible()


def test_replacement_during_exit_cannot_hide_new_notice(host):
    toast = CanvasToast(host[0], reduced_motion=False)
    toast.ENTER_MS = 20
    toast.EXIT_MS = 100
    toast.show_message('Old', duration_ms=10)
    QTest.qWait(70)
    assert toast._phase == 'exit'
    toast.show_message('New', duration_ms=250)
    QTest.qWait(130)
    assert toast.isVisible()
    assert toast.text() == 'New'
    assert toast._phase == 'hold'
    QTest.qWait(300)
    assert not toast.isVisible()


def test_resize_long_plain_text_and_dismiss(host, qapp):
    parent, _ = host
    toast = CanvasToast(parent, reduced_motion=True)
    message = '<b>Copied</b> ' + 'long Unicode 文件名 ' * 8
    toast.show_message(message)
    parent.resize(280, 400)
    qapp.processEvents()
    assert toast.width() <= 248
    assert toast.height() > 36
    assert toast.text() == message
    assert parent.rect().contains(toast.geometry())
    assert abs(toast.geometry().center().x() - parent.rect().center().x()) <= 1
    toast.dismiss()
    assert not toast._timer.isActive()
    assert not toast.isVisible()
    toast.show_message('Again')
    toast.show_message('   ')
    assert toast.text() == 'Again'
    assert toast.isVisible()


def test_reduced_motion_skips_both_animations(host):
    toast = CanvasToast(host[0], reduced_motion=True)
    toast.show_message('Copied', duration_ms=20)
    assert toast._phase == 'hold'
    assert toast._opacity.opacity() == 1.0
    assert toast._animation.state() == toast._animation.State.Stopped
    QTest.qWait(50)
    assert toast._phase == 'hidden'
    assert not toast.isVisible()


def test_viewer_success_notices_leave_footer_and_canvas_unchanged(qapp, tmp_path, monkeypatch):
    from pathlib import Path
    import numpy as np
    from luma.app import ViewerWindow
    from luma import actions
    path = tmp_path / 'source.png'
    path.write_bytes(b'synthetic source, never altered')
    window = ViewerWindow()
    window.show()
    window.path = path
    window._load_token = 7
    window._loaded(7, {'path': path, 'rgba': np.full((30, 40, 4), 255, dtype=np.uint8),
                       'linear': False, 'metadata': {}})
    qapp.processEvents()
    metadata = window.status_label.toolTip()
    geometry = window.canvas.geometry()
    controls = (window.zoom_combo.geometry(), window.info_button.geometry())
    monkeypatch.setattr(actions, 'copy_displayed_image', lambda image: True)
    monkeypatch.setattr(actions, 'save_displayed_image', lambda *args: Path('saved.png'))
    try:
        for action, phrase in [(window.copy_image, 'copied'),
                               (window.copy_image_path, 'path copied'),
                               (window.save_as, 'Saved')]:
            action()
            qapp.processEvents()
            assert phrase in window.toast.text()
            assert window.toast.isVisible()
            assert window.status_label.toolTip() == metadata
            assert window.canvas.geometry() == geometry
            assert (window.zoom_combo.geometry(), window.info_button.geometry()) == controls
        window.rotate_image()
        assert 'rotated' in window.toast.text()
        assert window.status_label.toolTip() == metadata
        window.toast.dismiss()
        assert window.status_label.toolTip() == metadata
        assert path.read_bytes() == b'synthetic source, never altered'
    finally:
        window.close()


def test_viewer_critical_errors_remain_outside_toast_and_footer(qapp, tmp_path):
    from luma.app import ViewerWindow
    window = ViewerWindow()
    window.show()
    try:
        window.open_path(tmp_path / 'missing.png')
        qapp.processEvents()
        assert window._error_dialog.isVisible()
        assert 'File not found' in window._error_dialog.text()
        assert window._error_dialog.textFormat() == Qt.TextFormat.PlainText
        assert window.status_label.toolTip() == ''
        assert not window.toast.isVisible()
        assert not window.toast._timer.isActive()
        window._error_dialog.accept()
        assert not window._error_dialog.isVisible()
    finally:
        window.close()
