"""Synthetic fixtures for localized dialogs, with network and recycling mocked."""
from __future__ import annotations

import ast
import hashlib
from io import BytesIO
from pathlib import Path
from string import Formatter
import time
from types import SimpleNamespace
from urllib.error import HTTPError
import zipfile

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QMessageBox, QWidget

from luma import actions, shell_settings, updater
from luma.i18n import set_language, tr
from luma.i18n_dialogs import TRANSLATIONS


LANGUAGES = ('en', 'zh-CN', 'zh-TW')


def wait_for(qapp, predicate):
    deadline = time.monotonic() + 3
    while not predicate() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(.005)
    qapp.processEvents()
    assert predicate()


def translated(language, key, **values):
    return (key if language == 'en' else TRANSLATIONS[language][key]).format(**values)


def sample_release():
    data = BytesIO()
    with zipfile.ZipFile(data, 'w') as bundle:
        bundle.writestr('Luma/Luma.exe', b'MZ synthetic executable')
    package = data.getvalue()
    name = 'Luma-0.1.1-windows-x64.zip'
    manifest = f'{hashlib.sha256(package).hexdigest()}  {name}\n'.encode()
    prefix = updater.RELEASES_URL + '/download/v0.1.1/'
    release = updater.Release(
        updater.Version.parse('0.1.1'), 'v0.1.1', updater.RELEASES_URL + '/tag/v0.1.1',
        updater.ReleaseAsset(name, prefix + name, len(package)),
        updater.ReleaseAsset('SHA256SUMS', prefix + 'SHA256SUMS', len(manifest)),
    )
    return release, package, manifest


def test_dialog_catalogs_are_complete_and_preserve_placeholders():
    assert TRANSLATIONS['zh-CN'].keys() == TRANSLATIONS['zh-TW'].keys()
    fields = lambda text: {(name, spec, conversion) for _, name, spec, conversion
                           in Formatter().parse(text) if name is not None}
    for language in ('zh-CN', 'zh-TW'):
        for key, value in TRANSLATIONS[language].items():
            assert fields(key) == fields(value), (language, key)
            assert value != key, (language, key)
        for module in (actions, shell_settings, updater):
            tree = ast.parse(Path(module.__file__).read_text())
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                        and node.func.id in ('tr', 'error_text') and node.args
                        and isinstance(node.args[0], ast.Constant)):
                    assert node.args[0].value in TRANSLATIONS[language]


@pytest.mark.parametrize('language', LANGUAGES)
def test_export_dialog_labels_ids_and_filenames(qapp, tmp_path, language):
    set_language(language)
    source = tmp_path / '原始 red channel_{frame}_设置.exr'
    dialog = actions.ExportDialog(None, source, True)
    try:
        assert dialog.windowTitle() == translated(language, 'Save As · displayed image')
        assert [dialog.format_combo.itemData(index) for index in range(5)] == [None, 'PNG', 'JPEG', 'TIFF', 'WEBP']
        assert dialog.format_combo.itemText(1) == translated(language, actions.EXPORT_FORMATS[0].label)
        assert dialog.filename.text() == str(source.with_name(source.stem + '-display'))
        assert dialog.buttons.button(QDialogButtonBox.StandardButton.Cancel).text() == tr('Cancel')
        assert dialog.buttons.button(QDialogButtonBox.StandardButton.Save).text() == translated(language, 'Export displayed image')
        assert not dialog.buttons.button(QDialogButtonBox.StandardButton.Save).isEnabled()
        dialog.format_combo.setCurrentIndex(2)
        assert dialog.format_combo.currentData() == 'JPEG'
        assert dialog.filename.text() == str(source.with_name(source.stem + '-display.jpg'))
        assert dialog.format_note.text() == translated(language, 'JPEG uses lossy compression (quality 95). Transparent pixels are composited on white.')
        dialog.acknowledge.setChecked(True)
        dialog.accept()
        assert dialog.choice.format == 'JPEG'
        assert dialog.choice.path.stem == source.stem + '-display'
    finally:
        dialog.close()


@pytest.mark.parametrize('language', LANGUAGES)
def test_destination_validation_is_localized_and_ids_preserved(tmp_path, language):
    set_language(language)
    source = tmp_path / 'source.exr'
    with pytest.raises(ValueError) as caught:
        actions.validated_destination(tmp_path / 'bad.exr', source, 'JPEG')
    assert str(caught.value) == translated(
        language, 'The filename extension does not match {format}. Use {extensions} or choose another format.',
        format='JPEG', extensions=tr(' or ').join(('.jpg', '.jpeg')))
    with pytest.raises(ValueError) as caught:
        actions.validated_destination(tmp_path / 'out.png', source, 'invalid')
    assert str(caught.value) == translated(language, 'Choose PNG, JPEG, TIFF or WebP for this display export.')


@pytest.mark.parametrize('language', LANGUAGES)
def test_export_io_error_keeps_raw_diagnostic_and_source(qapp, tmp_path, monkeypatch, language):
    set_language(language)
    source = tmp_path / '输入 red {blue}.png'
    source.write_bytes(b'source contents and metadata remain unchanged')
    destination = tmp_path / '输出 copy.png'
    raw = f'ENOSPC: disk full at {destination}; <native channel="R"> {{details}}'
    class ChosenDialog:
        choice = actions.ExportChoice(destination, 'PNG')
        def __init__(self, *args):
            pass
        def exec(self):
            return QDialog.DialogCode.Accepted
    def fail(*args, **kwargs):
        raise OSError(raw)
    messages = []
    monkeypatch.setattr(actions, 'ExportDialog', ChosenDialog)
    monkeypatch.setattr(actions, '_write_export', fail)
    monkeypatch.setattr(actions.QMessageBox, 'critical', lambda *args: messages.append(args))
    image = QImage(2, 2, QImage.Format.Format_RGBA8888)
    image.fill(Qt.GlobalColor.red)
    assert actions.save_displayed_image(None, image, source, False) is None
    assert messages[0][1] == translated(language, 'Unable to export image')
    assert messages[0][2] == (translated(language, 'The displayed image could not be exported. Check the destination and try again.')
                               + '\n\n' + tr('Technical details:') + '\n' + raw)
    assert source.read_bytes() == b'source contents and metadata remain unchanged'
    assert not destination.exists()


@pytest.mark.parametrize('language', LANGUAGES)
def test_recycle_confirmation_localized_cancel_preserves_source(qapp, tmp_path, monkeypatch, language):
    set_language(language)
    source = tmp_path / '原图 red_{frame}.exr'
    source.write_bytes(b'original')
    seen = []
    monkeypatch.setattr(actions.QFile, 'supportsMoveToTrash', lambda: True)
    monkeypatch.setattr(actions.QFile, 'moveToTrash', lambda *_: pytest.fail('Cancellation must not recycle'))
    monkeypatch.setattr(actions.QMessageBox, 'question', lambda *args: seen.append(args) or QMessageBox.StandardButton.Cancel)
    assert not actions.recycle_source(None, source)
    assert seen[0][1] == translated(language, 'Move image to Recycle Bin?')
    assert seen[0][2] == translated(language, 'Move this original image to the system Recycle Bin / Trash?\n\n{source}\n\nYou can restore it using your operating system. Luma will not permanently delete it.', source=source)
    assert source.read_bytes() == b'original'


@pytest.mark.parametrize('language', LANGUAGES)
def test_shell_status_and_failure_keep_raw_detail(qapp, monkeypatch, language):
    set_language(language)
    raw = r'foreign provider: C:\Users\中文\red {channel}\Luma.exe; {0x80070005}'
    monkeypatch.setattr(shell_settings.shell, 'status', lambda: shell_settings.shell.Status('not_installed', detail=raw))
    monkeypatch.setattr(shell_settings, '_startup_registration_error', '')
    panel = shell_settings.ExplorerIntegrationPanel()
    try:
        assert panel.repair.text() == translated(language, 'Register this copy / repair')
        assert panel.status_label.text() == (translated(language, 'Thumbnail registration is not ready. Use Register this copy / repair.') + '\n\n' + tr('Technical details:') + '\n' + raw)
        questions, warnings = [], []
        monkeypatch.setattr(shell_settings.QMessageBox, 'question', lambda *args: questions.append(args) or QMessageBox.StandardButton.Yes)
        monkeypatch.setattr(shell_settings.QMessageBox, 'warning', lambda *args: warnings.append(args))
        def fail(*args):
            raise shell_settings.shell.ShellIntegrationError(raw)
        monkeypatch.setattr(shell_settings.shell, 'install', fail)
        panel._apply()
        assert questions[0][1] == translated(language, 'Luma · Explorer integration')
        assert tr('Register this copy / repair') in panel.status_label.text()
        assert panel.status_label.text().endswith('\n' + raw)
        assert warnings[0][2] == panel.status_label.text()
    finally:
        panel.close()


@pytest.mark.parametrize('language', LANGUAGES)
@pytest.mark.parametrize('code', (403, 404, 500))
def test_update_http_errors_are_localized_with_unchanged_details(language, code):
    set_language(language)
    original = HTTPError(updater.LATEST_URL, code, 'raw message {untouched}', {}, None)
    class FailedOpener:
        def open(self, *args, **kwargs):
            raise original
    with pytest.raises(updater.UpdateError) as caught:
        updater.check_for_update(opener=FailedOpener())
    key = ({403: 'GitHub temporarily limited update requests. Please try again later.',
            404: 'No published stable release is available yet.'}.get(code)
           or 'The update server returned HTTP {code}. Please try again later.')
    assert str(caught.value) == translated(language, key, code=code) + '\n\n' + tr('Technical details:') + '\n' + str(original)
    assert caught.value.__cause__ is original


@pytest.mark.parametrize('language', LANGUAGES)
def test_installer_progress_and_output_names_unchanged(tmp_path, monkeypatch, language):
    set_language(language)
    release, package, manifest = sample_release()
    monkeypatch.setattr(updater, '_fetch_bytes', lambda *args, **kwargs: manifest)
    monkeypatch.setattr(updater, '_transfer', lambda url, limit, sink, **kwargs: sink.write(package))
    statuses = []
    result = updater.install_release(release, tmp_path, status=statuses.append)
    assert statuses == [translated(language, key) for key in (
        'Downloading checksums…', 'Downloading update…', 'Verifying SHA-256…', 'Installing a separate copy…')]
    assert result.executable.name == 'Luma.exe'
    assert result.directory.name.startswith('Luma-0.1.1-')
    assert result.executable.read_bytes() == b'MZ synthetic executable'
    with pytest.raises(updater.UpdateCancelled) as caught:
        updater._check_cancel(lambda: True)
    assert str(caught.value) == translated(language, 'Update cancelled. Your existing copy is unchanged.')


@pytest.mark.parametrize('language', LANGUAGES)
def test_updater_dialog_labels_versions_and_raw_launch_failure(qapp, tmp_path, monkeypatch, language):
    set_language(language)
    monkeypatch.setattr(shell_settings.shell, 'status', lambda: shell_settings.shell.Status('unavailable'))
    dialog = updater.UpdateDialog(None, '0.1.0')
    try:
        assert dialog.windowTitle() == translated(language, 'Luma · Settings / Updates')
        assert dialog.check_button.text() == translated(language, 'Check for updates')
        release, _, _ = sample_release()
        monkeypatch.setattr(updater, 'sys', SimpleNamespace(platform='win32'))
        dialog._completed(0, release)
        assert dialog.status_label.text() == translated(language, 'Luma {version} is available ({size:.1f} MB).\nDownload and install will verify SHA-256 and install a separate copy in your local application-data folder. Your current copy stays available for rollback. The new version will run only when you click Launch.', version=release.version, size=release.archive.size / (1024 * 1024))
        executable = tmp_path / '原始 red_{channel}' / 'Luma.exe'
        executable.parent.mkdir()
        executable.write_bytes(b'MZ fake')
        installed = updater.InstalledUpdate('0.1.1', executable.parent, executable)
        dialog._completed(0, installed)
        assert str(executable) in dialog.status_label.text()
        assert dialog.launch_button.text() == translated(language, 'Launch new version and close Luma')
        raw = f'Cannot launch {executable}: native details {{raw}}'
        def fail(*args, **kwargs):
            raise OSError(raw)
        monkeypatch.setattr(updater.subprocess, 'Popen', fail)
        dialog.launch()
        assert dialog.status_label.text() == translated(language, 'The new version could not be launched. Your current Luma is still running; you can use it and try again later.') + '\n\n' + tr('Technical details:') + '\n' + raw
    finally:
        dialog.close()


@pytest.mark.parametrize('language', LANGUAGES)
def test_generic_updater_worker_error_is_actionable_and_verbatim(qapp, monkeypatch, language):
    set_language(language)
    monkeypatch.setattr(shell_settings.shell, 'status', lambda: shell_settings.shell.Status('unavailable'))
    raw = 'Unexpected exception <frame> C:/原图/red_{channel}.exr'
    def fail(*args):
        raise RuntimeError(raw)
    dialog = updater.UpdateDialog(None, '0.1.0')
    try:
        dialog._start(fail)
        wait_for(qapp, lambda: not dialog._busy)
        assert dialog.status_label.text() == translated(language, 'The update could not be completed. Your existing copy is unchanged.') + '\n\n' + tr('Technical details:') + '\n' + raw
        assert dialog.check_button.isEnabled()
        assert dialog.close_button.text() == ''
        assert dialog.close_button.accessibleName() == tr('Close')
    finally:
        dialog.close()


@pytest.mark.parametrize('language', LANGUAGES)
@pytest.mark.parametrize('state, key', (
    ('unavailable', 'Available in the packaged Windows x64 app only.'),
    ('not_installed', 'Thumbnail registration is not ready. Use Register this copy / repair.'),
    ('association_only', 'This older registration has no Luma thumbnail handlers. Use repair to prepare them.'),
    ('installed', 'Thumbnail support is registered for this user. Keep this folder in its current location.'),
    ('stale', 'Registration points at another copy. Register this copy to use its current location.'),
    ('modified', 'Another app changed some registered values. Luma will preserve those changes.'),
    ('recovery_required', 'An interrupted registration needs recovery. Use Register this copy / repair.'),
    ('blocked', 'Registration cannot be read safely. No changes were made.'),
))
def test_all_shell_integration_states_are_localized(qapp, monkeypatch, language, state, key):
    set_language(language)
    monkeypatch.setattr(shell_settings.shell, 'status', lambda: shell_settings.shell.Status(state))
    monkeypatch.setattr(shell_settings, '_startup_registration_error', '')
    panel = shell_settings.ExplorerIntegrationPanel()
    try:
        assert panel.status_label.text() == translated(language, key)
        assert panel.repair.isEnabled() == (state not in ('unavailable', 'blocked'))
    finally:
        panel.close()


@pytest.mark.parametrize('language', LANGUAGES)
def test_nonmodal_registration_notice_has_localized_buttons(qapp, monkeypatch, language):
    set_language(language)
    monkeypatch.setattr(shell_settings, '_startup_registration_error', '')
    window = QWidget()
    closed = []
    controller = SimpleNamespace(worker=SimpleNamespace(close=lambda: closed.append(True)),
                                 closed=False, window=window)
    raw = r'permission denied: C:\原始\{folder}\Luma.exe <provider>'
    try:
        shell_settings._StartupRegistration._failed(controller, 1, raw)
        notice = window.findChild(QMessageBox)
        assert notice is not None
        assert notice.windowTitle() == translated(language, 'Luma · Explorer thumbnails')
        assert notice.text().endswith('\n' + raw)
        assert notice.textFormat() == Qt.TextFormat.PlainText
        assert notice.windowModality() == Qt.WindowModality.NonModal
        assert notice.button(QMessageBox.StandardButton.Ok).text() == tr('OK')
        assert closed == [True]
        notice.close()
    finally:
        window.close()


@pytest.mark.parametrize('language', LANGUAGES)
@pytest.mark.parametrize('case', ('current', 'non_windows', 'missing_assets'))
def test_updater_result_variants_are_localized(qapp, monkeypatch, language, case):
    set_language(language)
    monkeypatch.setattr(shell_settings.shell, 'status', lambda: shell_settings.shell.Status('unavailable'))
    release, _, _ = sample_release()
    current = '0.1.1' if case == 'current' else '0.1.0'
    dialog = updater.UpdateDialog(None, current)
    try:
        monkeypatch.setattr(updater, 'sys', SimpleNamespace(platform='linux' if case == 'non_windows' else 'win32'))
        if case == 'missing_assets':
            release = updater.Release(release.version, release.tag, release.url, None, None)
        dialog._completed(0, release)
        key = {
            'current': "You're up to date. Latest stable release: {version}",
            'non_windows': 'Luma {version} is available.\nDownload and installation from this window require Windows x64.',
            'missing_assets': 'Luma {version} is available.\nIts Windows ZIP or SHA256SUMS is not published yet. Please check again later.',
        }[case]
        assert dialog.status_label.text() == translated(language, key, version=release.version)
        assert dialog.download_button.isHidden()
    finally:
        dialog.close()
