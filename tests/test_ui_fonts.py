"""Inspect shaped CJK glyphs across real UI surfaces and fractional DPI."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from PySide6.QtCore import QPoint
from PySide6.QtGui import QFont, QFontDatabase, QFontInfo, QTextLayout
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel, QMessageBox, QToolTip

from luma.app import STYLE, ViewerWindow
from luma.i18n import catalogs, get_language, set_language, tr
from luma.ui_fonts import install_ui_font, ui_font_families


def glyph_families(text, font):
    layout = QTextLayout(text, font)
    layout.beginLayout()
    line = layout.createLine()
    line.setLineWidth(10000)
    layout.endLayout()
    return {run.rawFont().familyName() for run in layout.glyphRuns()}


def expected_family(language, font):
    if language == 'en':
        return QFontInfo(font).family()
    installed = {family.casefold() for family in QFontDatabase.families()}
    return next((family for family in ui_font_families(language)[:4]
                 if family.casefold() in installed), None)


@pytest.mark.parametrize('language, families', [
    ('en', ('Segoe UI', 'Inter')),
    ('zh-CN', ('Microsoft YaHei', 'Microsoft YaHei UI')),
    ('zh-TW', ('Microsoft JhengHei', 'Microsoft JhengHei UI')),
])
def test_shared_font_policy_preserves_language_size_and_weight(qapp, language, families):
    set_language(language)
    font = QFont(qapp.font())
    font.setPointSizeF(11.5)
    font.setWeight(QFont.Weight.Medium)
    original = qapp.font()
    qapp.setFont(font)
    try:
        assert install_ui_font(qapp)
        assert tuple(qapp.font().families()[:2]) == families
        assert qapp.font().pointSizeF() == 11.5
        assert qapp.font().weight() == QFont.Weight.Medium
        assert get_language() == language
        assert 'font-family' not in STYLE
    finally:
        qapp.setFont(original)


@pytest.mark.parametrize('language, toast_text, settings_text', [
    ('zh-CN', '图像路径已复制到剪贴板', '设置'),
    ('zh-TW', '影像路徑已複製到剪貼簿', '設定'),
    ('en', 'Image path copied to clipboard', 'Settings'),
])
def test_actual_strings_and_shaped_faces_across_viewer_popup_and_dialogs(qapp, monkeypatch, language, toast_text, settings_text):
    from luma import release_notes, tooltip_theme
    from luma.updater import UpdateDialog
    monkeypatch.setattr(release_notes, 'fetch_notes', lambda *args, **kwargs: toast_text)
    monkeypatch.setattr(tooltip_theme, 'high_contrast_enabled', lambda: False)
    set_language(language)
    expected = expected_family(language, qapp.font())
    if expected is None:
        pytest.skip('No installed regional CJK face for deterministic glyph validation')
    window = ViewerWindow()
    window.show()
    settings = UpdateDialog(window, '0.1.4')
    box = QMessageBox(QMessageBox.Icon.Information, settings_text, toast_text,
                      QMessageBox.StandardButton.Ok, window)
    try:
        settings.show()
        window.toast.show_message(tr('Image path copied to clipboard'))
        assert window.toast.text() == toast_text
        assert settings.findChild(QLabel, 'settings_title').text() == settings_text
        settings.update_details.set_release('0.1.4', toast_text)
        settings.update_details.setExpanded(True)
        qapp.processEvents()
        settings_action = next(action for action in window.app_menu.actions()
                               if action.text() == settings_text)
        samples = [('toast', window.toast.text(), window.toast.font()),
                   ('main menu', settings_action.text(), window.app_menu.font()),
                   ('footer menu', window.zoom_combo.menu().actions()[-1].text(), window.zoom_combo.menu().font()),
                   ('message dialog', box.text(), box.font()),
                   ('notes browser', settings.update_details.text.toPlainText(), settings.update_details.text.font())]
        for name, combo in [('viewer dropdown', window.background),
                            ('settings dropdown', settings.display_panel.background)]:
            combo.showPopup()
            qapp.processEvents()
            samples.append((name, combo.itemText(1), combo.view().font()))
            combo.hidePopup()
        qapp.processEvents()
        QTest.qWait(50)
        QToolTip.showText(settings.close_button.mapToGlobal(QPoint(8, 24)), tr('Close'), settings.close_button)
        tip = None
        for _ in range(200):
            qapp.processEvents()
            tip = next((widget for widget in qapp.topLevelWidgets()
                        if widget.objectName() == 'qtooltip_label' and widget.isVisible()), None)
            if tip is not None:
                break
            QTest.qWait(10)
        assert tip is not None, 'Native tooltip did not finish appearing'
        samples.append(('tooltip', tip.text(), tip.font()))
        for name, text, font in samples:
            # Validate the actual owned text before isolating its Han glyph run.
            assert not any('\u3040' <= char <= '\u30ff' for char in text), (name, text)
            sample = ''.join(char for char in text if '\u3400' <= char <= '\u9fff') if language != 'en' else text
            assert sample, (name, text)
            actual = glyph_families(sample, font)
            assert {family.casefold() for family in actual} == {expected.casefold()}, (name, text, actual, expected)
        assert not window.toast.grab().isNull()
        assert get_language() == language
    finally:
        QToolTip.hideText()
        QTest.qWait(320)
        box.close()
        settings.close()
        window.close()
        qapp.processEvents()


def test_chinese_catalogs_do_not_contain_japanese_kana():
    for catalog in catalogs():
        for language in ('zh-CN', 'zh-TW'):
            for source, translated in catalog[language].items():
                assert not any('\u3040' <= char <= '\u30ff' for char in translated), (language, source)


@pytest.mark.parametrize('scale', ['1.5', '2'])
@pytest.mark.parametrize('language', ['zh-CN', 'zh-TW', 'en'])
def test_actual_text_render_and_glyph_faces_at_high_dpi(tmp_path, language, scale):
    # Qt fixes its DPI at QApplication creation, so use an isolated process.
    source = '''
import json, sys
from PySide6.QtGui import QFontDatabase, QFontInfo, QTextLayout
from PySide6.QtWidgets import QApplication, QLabel
from luma.app import STYLE
from luma.i18n import set_language, tr
from luma.ui_fonts import ui_font_families
app = QApplication([])
app.setStyle('Fusion'); app.setStyleSheet(STYLE); set_language(sys.argv[1])
label = QLabel(tr('Image path copied to clipboard')); label.show(); app.processEvents()
layout = QTextLayout(label.text(), label.font())
layout.beginLayout(); layout.createLine(); layout.endLayout()
picture = label.grab(); assert picture.save(sys.argv[2])
installed = {name.casefold() for name in QFontDatabase.families()}
expected = QFontInfo(label.font()).family() if sys.argv[1] == 'en' else next((name for name in ui_font_families()[:4] if name.casefold() in installed), None)
print(json.dumps({'dpr': picture.devicePixelRatio(), 'families': sorted({run.rawFont().familyName() for run in layout.glyphRuns()}), 'expected': expected, 'width': picture.width(), 'logical_width': label.width(), 'text': label.text()}))
'''
    result = subprocess.run([sys.executable, '-c', source, language, str(tmp_path / 'font.png')],
                            cwd=Path(__file__).resolve().parents[1],
                            env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen', 'QT_SCALE_FACTOR': scale},
                            text=True, capture_output=True, check=True, timeout=20)
    record = json.loads(result.stdout)
    assert record['dpr'] == float(scale)
    assert abs(record['width'] - record['logical_width'] * float(scale)) <= 1
    if record['expected'] is None:
        pytest.skip('No installed regional CJK face for deterministic glyph validation')
    assert {name.casefold() for name in record['families']} == {record['expected'].casefold()}
