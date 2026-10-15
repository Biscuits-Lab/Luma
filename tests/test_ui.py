from pathlib import Path
import time

import numpy as np
from PIL import Image
from PySide6.QtCore import Qt, QPointF, QMimeData, QUrl
from PySide6.QtGui import QDropEvent
from PySide6.QtTest import QTest

from luma.app import ViewerWindow


def wait_for(app, predicate, timeout=6):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.008)
    app.processEvents()
    assert predicate()


def test_open_navigation_channels_panels_fullscreen_and_read_only(qapp, tmp_path):
    directory = tmp_path / '中文 照片'
    directory.mkdir()
    pixels = np.zeros((80, 120, 4), dtype=np.uint8)
    pixels[..., 0] = 255
    pixels[..., 3] = 128
    first, second = directory / '图2.png', directory / '图10.png'
    Image.fromarray(pixels).save(first)
    Image.new('RGB', (240, 160), 'blue').save(second)
    originals = {p: p.read_bytes() for p in (first, second)}
    win = ViewerWindow()
    win.show()
    win.open_path(first)
    wait_for(qapp, lambda: win.displayed_path == first and len(win.files) == 2)
    assert win.index == 0
    assert win.dimension_label.text() == '120 × 80'
    assert not win.exposure.isEnabled()
    assert win.next.isEnabled() and not win.previous.isEnabled()
    assert win.filmstrip.count() == 2
    win.canvas.actual_size()
    assert abs(win.canvas.transform().m11() * win.canvas.viewport().devicePixelRatioF() - 1) < .001
    win.canvas.zoom(2)
    assert abs(win.canvas.transform().m11() * win.canvas.viewport().devicePixelRatioF() - 2) < .001
    win.zoom_slider.setValue(20)
    assert abs(win.canvas.transform().m11() * win.canvas.viewport().devicePixelRatioF() - 2) < .001
    win.canvas.fit()
    assert win.canvas.fitted
    assert abs(win.filename.mapTo(win, win.filename.rect().center()).x() - win.rect().center().x()) <= 2
    assert any('Settings' in action.text() for action in win.app_menu.actions())
    win.channel.setCurrentText('A')
    wait_for(qapp, lambda: not win.render_timer.isActive() and not win.cancel_button.isVisible())
    image = win.canvas.item.pixmap().toImage()
    assert 126 <= image.pixelColor(0, 0).red() <= 129
    assert image.pixelColor(0, 0).alpha() == 255
    assert not win.sidebar.isVisible()
    win.info_button.click()
    assert win.sidebar.isVisible()
    win.info_button.click()
    assert not win.sidebar.isVisible()
    assert not win.filmstrip.isVisible()
    win.strip_button.click()
    assert win.filmstrip.isVisible()
    win.strip_button.click()
    assert not win.filmstrip.isVisible()
    win.toggle_fullscreen()
    assert win.isFullScreen()
    win.escape()
    assert not win.isFullScreen()
    win.navigate(1)
    wait_for(qapp, lambda: win.displayed_path == second)
    assert win.index == 1 and not win.next.isEnabled()
    assert win.channel.currentText() == 'RGBA'
    assert win.canvas.item.pixmap().toImage().pixelColor(0, 0).blue() == 255
    win.navigate(-1)
    wait_for(qapp, lambda: win.displayed_path == first)
    win.close()
    assert all(p.read_bytes() == b for p, b in originals.items())


def test_rapid_navigation_newest_wins_and_corrupt_recovers(qapp, tmp_path):
    for i in range(8):
        Image.new('RGB', (64, 48), (i * 20, 30, 40)).save(tmp_path / f'{i}.png')
    bad = tmp_path / '8.png'
    bad.write_bytes(b'not a png')
    win = ViewerWindow()
    win.show()
    win.open_path(tmp_path)
    wait_for(qapp, lambda: len(win.files) == 9 and win.displayed_path == tmp_path / '0.png')
    for i in range(8):
        win.select_index(i)
    wait_for(qapp, lambda: win.displayed_path == tmp_path / '7.png')
    assert win.filename.text() == '7.png'
    win.select_index(8)
    wait_for(qapp, lambda: win.dimension_label.text() == 'Unable to open')
    assert win.canvas.item.pixmap().isNull()
    assert win.displayed_path is None
    win.select_index(0)
    wait_for(qapp, lambda: win.displayed_path == tmp_path / '0.png')
    win.close()


def test_drop_file_and_empty_folder(qapp, tmp_path):
    file = tmp_path / 'drop.png'
    Image.new('RGB', (10, 20), 'green').save(file)
    win = ViewerWindow()
    win.show()
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(file))])
    event = QDropEvent(QPointF(100, 100), Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    win.dropEvent(event)
    assert event.isAccepted()
    wait_for(qapp, lambda: win.displayed_path == file)
    empty = tmp_path / 'empty'
    empty.mkdir()
    win.open_path(empty)
    wait_for(qapp, lambda: win.filename.text() == 'No images in this folder')
    assert win.path is None
    assert win.filmstrip.count() == 0
    win.close()


def test_hdr_exposure_roundtrip(qapp, tmp_path):
    file = tmp_path / 'test.hdr'
    file.write_bytes(b'#?RADIANCE\nFORMAT=32-bit_rle_rgbe\n\n-Y 2 +X 2\n' + bytes([128, 64, 32, 130]) * 4)
    win = ViewerWindow()
    win.show()
    win.open_path(file)
    wait_for(qapp, lambda: win.displayed_path == file)
    assert win.exposure.isEnabled()
    before = win.canvas.item.pixmap().toImage().pixelColor(0, 0).red()
    win.exposure.setValue(-30)
    wait_for(qapp, lambda: not win.render_timer.isActive() and not win.cancel_button.isVisible())
    after = win.canvas.item.pixmap().toImage().pixelColor(0, 0).red()
    assert after < before
    assert win.canvas.item.pixmap().toImage().pixelColor(0, 0).alpha() == 255
    win.close()
