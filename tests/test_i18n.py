"""UI-language selection and whole-catalog invariants, independent of host locale."""
import ast
import ctypes
from pathlib import Path
from string import Formatter

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QMessageBox

from luma import i18n
from luma.app import ViewerWindow


@pytest.mark.parametrize('tag, expected', [
    ('zh-CN', 'zh-CN'), ('zh_Hans_SG.UTF-8', 'zh-CN'), ('zh-SG', 'zh-CN'),
    ('zh-Hans-HK', 'zh-CN'), ('zh', 'zh-CN'), ('zh-CHS', 'zh-CN'),
    ('zh-TW', 'zh-TW'), ('zh-HK', 'zh-TW'), ('zh-MO', 'zh-TW'),
    ('zh-Hant-CN', 'zh-TW'), ('zh-CHT', 'zh-TW'),
    ('en-US', 'en'), ('en_GB', 'en'), ('de-DE', 'en'), ('ja-JP', 'en'),
    ('', 'en'), (None, 'en'), ('C.UTF-8', 'en'),
])
def test_language_tags(tag, expected):
    assert i18n.normalize_language(tag) == expected


@pytest.mark.parametrize('languages, expected', [
    (['zh-CN', 'en-US'], 'zh-CN'), (['zh-HK', 'en-US'], 'zh-TW'),
    (['en-US', 'zh-CN'], 'en'), (['fr-FR', 'zh-CN'], 'en'), ([], 'en'),
])
def test_windows_uses_primary_ui_language_never_regional_locale(monkeypatch, languages, expected):
    monkeypatch.setattr(i18n.sys, 'platform', 'win32')
    monkeypatch.setattr(i18n, '_windows_ui_languages', lambda: languages)
    monkeypatch.setattr(i18n, '_qt_ui_languages', lambda: pytest.fail('Windows must not use region fallback'))
    monkeypatch.setattr(i18n, '_posix_ui_languages', lambda: pytest.fail('Windows must not use region fallback'))
    monkeypatch.setenv('LANG', 'zh_CN.UTF-8')
    assert i18n.detect_system_language() == expected


class FakeFunction:
    def __init__(self, function):
        self.function = function

    def __call__(self, *args):
        return self.function(*args)


def test_windows_api_reads_full_multisz_and_sets_prototypes(monkeypatch):
    values = 'zh-Hant-HK\0en-US\0\0'
    calls = []

    def preferred(flags, count, buffer, size):
        calls.append((flags, buffer is None))
        ctypes.cast(count, ctypes.POINTER(ctypes.c_uint32))[0] = 2
        ctypes.cast(size, ctypes.POINTER(ctypes.c_uint32))[0] = len(values)
        if buffer is not None:
            for index, char in enumerate(values):
                buffer[index] = char
        return 1

    api = type('Kernel', (), {'GetUserPreferredUILanguages': FakeFunction(preferred)})()
    monkeypatch.setattr(ctypes, 'WinDLL', lambda *args, **kwargs: api, raising=False)
    assert i18n._windows_ui_languages() == ['zh-Hant-HK', 'en-US']
    assert calls == [(8, True), (8, False)]
    assert api.GetUserPreferredUILanguages.restype == ctypes.c_int32
    assert len(api.GetUserPreferredUILanguages.argtypes) == 4


@pytest.mark.parametrize('language_id, expected', [(0x0404, ['zh_TW']), (0x0804, ['zh_CN']),
                                                   (0x0409, ['en_US']), (0, [])])
def test_windows_api_fallback_is_ui_langid(monkeypatch, language_id, expected):
    api = type('Kernel', (), {
        'GetUserPreferredUILanguages': FakeFunction(lambda *args: 0),
        'GetUserDefaultUILanguage': FakeFunction(lambda: language_id),
    })()
    monkeypatch.setattr(ctypes, 'WinDLL', lambda *args, **kwargs: api, raising=False)
    assert i18n._windows_ui_languages() == expected
    assert api.GetUserDefaultUILanguage.restype == ctypes.c_uint16


def test_windows_optional_api_unavailable_is_safe(monkeypatch):
    def unavailable(*args, **kwargs):
        raise OSError('missing DLL')
    monkeypatch.setattr(ctypes, 'WinDLL', unavailable, raising=False)
    assert i18n._windows_ui_languages() == []


def test_non_windows_qt_ui_language_and_posix_message_fallback(monkeypatch):
    monkeypatch.setattr(i18n.sys, 'platform', 'linux')
    monkeypatch.setattr(i18n, '_qt_ui_languages', lambda: ['zh-Hant-TW'])
    monkeypatch.setenv('LANG', 'en_US.UTF-8')
    assert i18n.detect_system_language() == 'zh-TW'
    monkeypatch.setattr(i18n, '_qt_ui_languages', lambda: [])
    for key in ('LANGUAGE', 'LC_ALL', 'LC_MESSAGES', 'LANG'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('LANGUAGE', 'zh_CN:en_US')
    assert i18n.detect_system_language() == 'zh-CN'


def test_system_language_cached_until_restart_or_explicit_reset(monkeypatch):
    monkeypatch.setattr(i18n, 'detect_system_language', lambda: 'zh-TW')
    assert i18n.set_language() == 'zh-TW'
    monkeypatch.setattr(i18n, 'detect_system_language', lambda: 'en')
    assert i18n.get_language() == 'zh-TW'
    assert i18n.set_language() == 'en'


def test_catalogs_match_keys_and_named_placeholders():
    keys = set()
    for catalog in i18n.catalogs():
        assert catalog['zh-CN'].keys() == catalog['zh-TW'].keys()
        for key in catalog['zh-CN']:
            expected = [(name, spec, conversion) for _, name, spec, conversion in Formatter().parse(key) if name]
            for language in ('zh-CN', 'zh-TW'):
                translated = catalog[language][key]
                assert translated.strip(), (language, key)
                actual = [(name, spec, conversion) for _, name, spec, conversion in Formatter().parse(translated) if name]
                assert sorted(actual) == sorted(expected), (language, key)
            keys.add(key)
    # Catch future untranslated literal tr() call sites, including error wrappers.
    for filename in ('app.py', 'actions.py', 'updater.py', 'shell_settings.py'):
        tree = ast.parse((Path(i18n.__file__).parent / filename).read_text())
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id in ('tr', 'error_text') and node.args
                    and isinstance(node.args[0], ast.Constant)):
                assert node.args[0].value in keys, (filename, node.lineno, node.args[0].value)


@pytest.mark.parametrize('language, word', [('zh-CN', '技术详情'), ('zh-TW', '技術詳細資料'), ('en', 'Technical details')])
def test_raw_runtime_details_never_substituted(language, word):
    i18n.set_language(language)
    raw = 'C:\\Settings\\Black {name} <b>图像</b>.exr: Unable to open / errno=42'
    rendered = i18n.error_text('Could not open {name}.', raw, name='Black {path}.exr')
    assert rendered.endswith(raw)
    assert word in rendered
    assert 'Black {path}.exr' in rendered
    assert i18n.tr('Unknown {value}', value=raw) == 'Unknown ' + raw


@pytest.mark.parametrize('language', ['zh-CN', 'zh-TW', 'en'])
def test_qt_standard_buttons_and_fallback_file_dialog(qapp, language):
    i18n.set_language(language)
    assert i18n.install_qt_translations(qapp)
    box = QMessageBox(QMessageBox.Icon.Question, 'Luma', 'test',
                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
    yes = box.button(QMessageBox.StandardButton.Yes).text().replace('&', '')
    assert ('是' in yes) if language.startswith('zh') else ('Yes' in yes)
    dialog = QFileDialog()
    dialog.setOption(QFileDialog.Option.DontUseNativeDialog)
    label = dialog.labelText(QFileDialog.DialogLabel.FileName)
    assert ('文件' in label or '檔' in label) if language.startswith('zh') else ('File' in label)
    # The translator must survive collection, otherwise static button labels regress.
    import gc
    gc.collect()
    assert qapp._luma_qt_translator is not None if language.startswith('zh') else qapp._luma_qt_translator is None
    box.close()
    dialog.close()


@pytest.mark.parametrize('language, settings', [('zh-CN', '设置'), ('zh-TW', '設定'), ('en', 'Settings')])
def test_viewer_localized_controls_use_stable_data_and_raw_details(qapp, tmp_path, language, settings):
    import numpy as np
    i18n.set_language(language)
    window = ViewerWindow()
    window.show()
    qapp.processEvents()
    try:
        assert any(settings in action.text() for action in window.app_menu.actions())
        for index, key in enumerate(('Graphite', 'Checkerboard', 'Black', 'White')):
            window.background.setCurrentIndex(index)
            assert window.canvas.background == key
        assert window.tone.itemData(1) == 'Clip'
        window.tone.setCurrentIndex(1)
        window.reset_display()
        assert window.tone.currentData() == 'Reinhard'
        # Real fixture paths must also be legal on Windows; markup-like values
        # remain covered by the metadata and diagnostic strings below.
        path = tmp_path / 'Black {version} [raw] 中文.exr'
        path.write_bytes(b'x')
        window.path = path
        window.filename.setText(path.name)
        window._load_token = 42
        metadata = {'Original English key': 'RAW <tag> {value} 中文', 'channels': 'R, G, B'}
        window._loaded(42, {'path': path, 'rgba': np.zeros((2, 3, 4), dtype=np.uint8), 'linear': True, 'metadata': metadata})
        assert window.filename.text() == path.name
        assert window.canvas.zoom_percent == 100
        for key, value in metadata.items():
            assert f'{key}\n{value}' in window.details.toPlainText()
        diagnostic = 'C:\\Black {image}.exr: unsupported <layout> 123'
        window._load_failed(42, diagnostic)
        assert window.details.toPlainText().endswith(diagnostic)
        assert window.status_label.toolTip().endswith(diagnostic)
        assert window.dimension_label.text() == i18n.tr('Unable to open')
    finally:
        window.close()


@pytest.mark.parametrize('language', ['en', 'zh-CN', 'zh-TW'])
def test_message_boxes_preserve_markup_like_filenames_as_plain_text(qapp, monkeypatch, language):
    from luma.dialogs import PlainMessageBox
    i18n.set_language(language)
    observed = []
    raw = 'C:\\<b>Black {path}</b>.exr\nFailed <layout> & errno=42'

    def inspect(box):
        observed.append((box.text(), box.textFormat(), box.defaultButton().text(),
                         box.button(QMessageBox.StandardButton.Yes).text()))
        return QMessageBox.StandardButton.Cancel

    monkeypatch.setattr(PlainMessageBox, 'exec', inspect)
    answer = PlainMessageBox.question(None, 'Luma', raw,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel)
    assert answer == QMessageBox.StandardButton.Cancel
    assert observed == [(raw, Qt.TextFormat.PlainText, i18n.tr('Cancel'), i18n.tr('Yes'))]


def test_packaged_qt_catalog_presence_and_loading(qapp, tmp_path):
    import shutil
    from PySide6.QtCore import QLibraryInfo
    source = Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath))
    assert not i18n.verify_packaged_qt_catalogs(tmp_path)
    for language in ('zh_CN', 'zh_TW'):
        shutil.copyfile(source / f'qtbase_{language}.qm', tmp_path / f'qtbase_{language}.qm')
    assert i18n.verify_packaged_qt_catalogs(tmp_path)
    (tmp_path / 'qtbase_zh_TW.qm').write_bytes(b'corrupt QM')
    assert not i18n.verify_packaged_qt_catalogs(tmp_path)


def test_frozen_smoke_rejects_missing_qt_translations(qapp, monkeypatch):
    from luma import app
    monkeypatch.setattr(app.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(app, 'verify_packaged_qt_catalogs', lambda: False)
    assert app.main(['--smoke-test']) == 4
