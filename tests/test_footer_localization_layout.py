"""Localized footer remains compact; metadata/info track text, zoom stays fixed."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QPushButton

from luma.app import ViewerWindow, symbol_icon
from luma.i18n import set_language


@pytest.mark.parametrize('language', ['en', 'zh-CN', 'zh-TW'])
@pytest.mark.parametrize('width', [800, 1280])
def test_metadata_and_info_follow_text_without_moving_zoom(qapp, language, width):
    set_language(language)
    window = ViewerWindow()
    window.resize(width, 560)
    window.show()
    image = QImage(640, 480, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.blue)
    window.canvas.set_image(image)
    window._set_controls(True)
    qapp.processEvents()
    try:
        assert window.viewbar.height() == 36
        assert window.titlebar.height() == 34
        fixed = [window.metadata_group, window.fit_button, window.zoom_combo, window.zoom_slider]
        initial = [widget.geometry() for widget in fixed]
        info_positions = []
        for text in ('PNG | 1 × 1 | 1 B', 'PNG   |   4096 × 4096   |   12.1 MB',
                     '正在加载很长的文件名 Loading {image} <b> untouched ' * 30):
            window._status(text)
            qapp.processEvents()
            assert [widget.geometry() for widget in fixed] == initial
            assert window.status_label.toolTip() == text
            assert 0 < window.info_button.x() - window.status_label.geometry().right() <= 8
            assert window.info_button.geometry().right() < window.metadata_group.width()
            info_positions.append(window.info_button.x())
            if width == 1280 and len(text) < 60:
                assert window.status_label.text() == text
        assert info_positions[0] < info_positions[-1]
        for percent in (.5, 1, 25, 100, 200, 6400):
            window.canvas.set_zoom_percent(percent)
            qapp.processEvents()
            assert [widget.geometry() for widget in fixed] == initial
            assert '⌄' not in window.zoom_combo.text()
            assert window.zoom_combo.fontMetrics().horizontalAdvance(window.zoom_combo.text()) < window.zoom_combo.width() - 25
        for button in window.viewbar.findChildren(QPushButton):
            assert button.height() <= 32
            if not button.icon().isNull():
                assert button.iconSize() == (QSize(20, 20) if button is window.info_button else QSize(24, 24))
        assert window.info_button.size() == QSize(30, 30)
        assert window.info_button.accessibleName()
        assert window.info_button.toolTip()
    finally:
        window.close()


@pytest.mark.parametrize('factor', [1, 1.25, 1.5, 2])
def test_icons_render_at_requested_device_pixel_ratio(qapp, factor):
    for kind in ('rotate', 'delete', 'strip', 'info', 'fit', 'minus', 'plus', 'full'):
        pixmap = symbol_icon(kind).pixmap(QSize(24, 24), factor)
        assert not pixmap.isNull()
        assert pixmap.width() >= int(24 * factor)
        assert pixmap.devicePixelRatioF() == pytest.approx(factor)


@pytest.mark.parametrize('factor, language', [('1.25', 'en'), ('1.5', 'zh-CN'), ('2', 'zh-TW')])
def test_fresh_process_high_dpi_layout_and_screenshots(tmp_path, factor, language):
    env = os.environ.copy()
    env['QT_QPA_PLATFORM'] = 'offscreen'
    env['QT_SCALE_FACTOR'] = factor
    env['XDG_CACHE_HOME'] = str(tmp_path / 'cache')
    result = subprocess.run([sys.executable, '-m', 'scripts.render_ui_preview', '--language', language,
                             '--width', '800', '--output', str(tmp_path)],
                            env=env, cwd=Path(__file__).resolve().parents[1],
                            capture_output=True, text=True, timeout=20, check=True)
    record = json.loads(result.stdout)
    assert record['device_pixel_ratio'] == float(factor)
    assert record['width'] == 800
    assert record['footer_height'] == 36
    assert record['header_height'] == 34
    assert record['zoom_text'] == '100%'
    assert 0 < record['info_gap'] <= 8
    assert all(Path(path).is_file() for path in record['screenshots'].values())


@pytest.mark.parametrize('language', ['en', 'zh-CN', 'zh-TW'])
def test_settings_wrapped_text_never_overlaps_and_long_details_scroll(qapp, language):
    from PySide6.QtWidgets import QLabel
    from luma.updater import UpdateDialog
    set_language(language)
    dialog = UpdateDialog(None, '0.1.3')
    dialog.resize(480, 400)
    dialog.show()
    qapp.processEvents()
    try:
        for label in dialog.findChildren(QLabel):
            if label.wordWrap() and label.isVisible():
                assert label.height() >= label.heightForWidth(label.width()), label.text()
        raw = 'C:\\超长文件夹\\raw {version} <b>diagnostic</b>.exr\n' * 80
        dialog._failed(dialog._serial, raw)
        qapp.processEvents()
        qapp.processEvents()
        assert dialog.status_label.text() == raw
        assert dialog.status_label.height() >= dialog.status_label.heightForWidth(dialog.status_label.width())
        assert dialog.scroll_area.verticalScrollBar().maximum() > 0
        assert dialog.close_button.isVisible()
        assert dialog.close_button.geometry().bottom() < dialog.height()
        assert dialog.scroll_area.horizontalScrollBar().maximum() == 0
    finally:
        dialog.close()
