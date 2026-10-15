"""Synthetic image fixtures only; OS recycling is always mocked."""
from pathlib import Path

from PIL import Image
import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QTransform
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QLabel, QMessageBox

from luma import actions


@pytest.fixture
def pixels(qapp):
    image = QImage(3, 2, QImage.Format.Format_RGBA8888)
    image.fill(QColor(200, 30, 10, 128))
    image.setPixelColor(0, 0, QColor(20, 40, 60, 255))
    image.setText('private source metadata', 'must not escape')
    return image


@pytest.fixture
def source(tmp_path):
    path = tmp_path / '原始 image.png'
    Image.new('RGB', (3, 2), 'green').save(path)
    return path


@pytest.fixture
def messages(monkeypatch):
    seen = []
    def question(*args):
        seen.append(('question', args))
        return QMessageBox.StandardButton.Yes
    def warning(*args):
        seen.append(('warning', args))
        return QMessageBox.StandardButton.Save
    monkeypatch.setattr(actions.QMessageBox, 'question', question)
    monkeypatch.setattr(actions.QMessageBox, 'warning', warning)
    monkeypatch.setattr(actions.QMessageBox, 'critical', lambda *args: seen.append(('critical', args)))
    return seen


def choose(monkeypatch, destination, format='PNG', accepted=True):
    class FakeDialog:
        def __init__(self, *args):
            self.choice = actions.ExportChoice(destination, format)
        def exec(self):
            return QDialog.DialogCode.Accepted if accepted else QDialog.DialogCode.Rejected
    monkeypatch.setattr(actions, 'ExportDialog', FakeDialog)


@pytest.mark.parametrize('format,extension', [('PNG', '.png'), ('JPEG', '.jpg'), ('TIFF', '.tif'), ('WEBP', '.webp')])
def test_export_safe_formats_source_immutable(qapp, tmp_path, source, pixels, monkeypatch, messages, format, extension):
    original = source.read_bytes()
    before = bytes(pixels.constBits())
    target = tmp_path / ('输出 copy' + extension)
    choose(monkeypatch, target, format)
    assert actions.save_displayed_image(None, pixels, source, False) == target
    assert source.read_bytes() == original
    assert bytes(pixels.constBits()) == before
    with Image.open(target) as exported:
        assert exported.format == format
        assert exported.size == (3, 2)
        assert 'private source metadata' not in exported.info
        # TIFF stores required image dimensions/layout in its IFD/Exif map.
        assert not ({270, 271, 272, 305, 306, 315, 34853} & set(exported.getexif()))
        if format != 'JPEG':
            assert exported.convert('RGBA').getpixel((1, 1)) == (200, 30, 10, 128)
    assert not list(tmp_path.glob('.luma-export-*'))
    assert not messages


def test_jpeg_alpha_composited_white(source, pixels, tmp_path):
    target = tmp_path / 'display.jpg'
    pixels = QImage(16, 16, QImage.Format.Format_RGBA8888)
    pixels.fill(QColor(200, 30, 10, 128))
    actions._write_export(pixels, target, source, 'JPEG', None)
    with Image.open(target) as exported:
        assert exported.mode == 'RGB'
        actual = exported.getpixel((1, 1))
        assert all(abs(a - b) <= 5 for a, b in zip(actual, (227, 142, 132)))


@pytest.mark.parametrize('alias', ['same', 'symlink', 'hardlink'])
def test_source_and_alias_overwrite_blocked(source, pixels, tmp_path, monkeypatch, messages, alias):
    original = source.read_bytes()
    target = source
    if alias != 'same':
        target = tmp_path / f'{alias}.png'
        try:
            if alias == 'symlink':
                target.symlink_to(source)
            else:
                target.hardlink_to(source)
        except OSError:
            pytest.skip('Filesystem does not permit this alias type')
    choose(monkeypatch, target)
    assert actions.save_displayed_image(None, pixels, source, False) is None
    assert source.read_bytes() == original
    assert any(kind == 'critical' and 'source is protected' in args[2] for kind, args in messages)
    assert not any(kind == 'question' for kind, _ in messages)


def test_extension_conflict_never_writes(source, pixels, tmp_path, monkeypatch, messages):
    target = tmp_path / 'wrong.jpg'
    choose(monkeypatch, target, 'PNG')
    assert actions.save_displayed_image(None, pixels, source, False) is None
    assert not target.exists()
    assert any('does not match PNG' in args[2] for _, args in messages)
    assert actions.validated_destination(tmp_path / 'no-extension', source, 'PNG').suffix == '.png'


def test_dialog_requires_explicit_format_and_display_ack(qapp, source, messages):
    dialog = actions.ExportDialog(None, source)
    save = dialog.buttons.button(QDialogButtonBox.StandardButton.Save)
    assert not save.isEnabled()
    dialog.accept()
    assert dialog.choice is None
    dialog.format_combo.setCurrentIndex(1)
    assert not save.isEnabled()
    assert dialog.filename.text().endswith('-display.png')
    dialog.acknowledge.setChecked(True)
    assert save.isEnabled()
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.choice.format == 'PNG'


def test_dialog_format_change_visible_and_conflicting_name_stays_open(qapp, source, messages):
    dialog = actions.ExportDialog(None, source)
    dialog.format_combo.setCurrentIndex(1)
    dialog.format_combo.setCurrentIndex(2)
    assert dialog.filename.text().endswith('-display.jpg')
    assert 'white' in dialog.format_note.text()
    dialog.filename.setText(str(source.parent / 'custom.exr'))
    dialog.acknowledge.setChecked(True)
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Rejected
    assert dialog.choice is None
    assert messages[-1][0] == 'warning'


def test_hdr_dialog_discloses_loss_and_unsupported_precision(qapp, source):
    dialog = actions.ExportDialog(None, source, True)
    text = '\n'.join(widget.text() for widget in dialog.findChildren(QLabel))
    assert 'Full-precision HDR' in text
    assert 'metadata' in text
    assert 'already been tone-mapped to 8-bit SDR' in text


@pytest.mark.parametrize('linear,suffix', [(True, '.tif'), (False, '.exr'), (False, '.cr3')])
def test_hdr_raw_requires_separate_confirmation(tmp_path, pixels, monkeypatch, messages, linear, suffix):
    source = tmp_path / ('source' + suffix)
    source.write_bytes(b'synthetic source, never decode')
    original = source.read_bytes()
    target = tmp_path / 'display.png'
    choose(monkeypatch, target)
    assert actions.save_displayed_image(None, pixels, source, linear) == target
    warnings = [args for kind, args in messages if kind == 'warning']
    assert len(warnings) == 1
    assert source.name in warnings[0][2]
    assert 'not a full-precision export' in warnings[0][2]
    assert source.read_bytes() == original


def test_hdr_warning_cancel_no_export(source, pixels, tmp_path, monkeypatch, messages):
    target = tmp_path / 'display.png'
    choose(monkeypatch, target)
    monkeypatch.setattr(actions.QMessageBox, 'warning', lambda *args: QMessageBox.StandardButton.Cancel)
    assert actions.save_displayed_image(None, pixels, source, True) is None
    assert not target.exists()


def test_save_cancel_no_export(source, pixels, tmp_path, monkeypatch, messages):
    target = tmp_path / 'display.png'
    choose(monkeypatch, target, accepted=False)
    assert actions.save_displayed_image(None, pixels, source, False) is None
    assert not target.exists()
    assert not messages


def test_overwrite_cancel_preserves_existing(source, pixels, tmp_path, monkeypatch, messages):
    target = tmp_path / 'existing.png'
    target.write_bytes(b'existing export')
    choose(monkeypatch, target)
    monkeypatch.setattr(actions.QMessageBox, 'question', lambda *args: QMessageBox.StandardButton.Cancel)
    assert actions.save_displayed_image(None, pixels, source, False) is None
    assert target.read_bytes() == b'existing export'


def test_overwrite_confirm_atomic_replace(source, pixels, tmp_path, monkeypatch, messages):
    target = tmp_path / 'existing.png'
    target.write_bytes(b'existing export')
    choose(monkeypatch, target)
    real_replace = actions.os.replace
    replaced = []
    def record_replace(temp, final):
        assert target.read_bytes() == b'existing export'
        with Image.open(temp) as encoded:
            assert encoded.size == (3, 2)
        replaced.append((temp, final))
        real_replace(temp, final)
    monkeypatch.setattr(actions.os, 'replace', record_replace)
    assert actions.save_displayed_image(None, pixels, source, False) == target
    assert len(replaced) == 1
    assert any(kind == 'question' and str(target) in args[2] for kind, args in messages)
    assert not list(tmp_path.glob('.luma-export-*'))


def test_encoding_failure_keeps_destination_and_cleans_temp(source, pixels, tmp_path, monkeypatch, messages):
    target = tmp_path / 'existing.png'
    target.write_bytes(b'existing export')
    choose(monkeypatch, target)
    def fail(*args, **kwargs):
        raise OSError('simulated disk full')
    monkeypatch.setattr(actions.Image.Image, 'save', fail)
    assert actions.save_displayed_image(None, pixels, source, False) is None
    assert target.read_bytes() == b'existing export'
    assert not list(tmp_path.glob('.luma-export-*'))
    assert any(kind == 'critical' and 'disk full' in args[2] for kind, args in messages)


def test_destination_created_during_encoding_not_overwritten(source, pixels, tmp_path, monkeypatch):
    target = tmp_path / 'new.png'
    real_save = actions.Image.Image.save
    def save_and_create(*args, **kwargs):
        real_save(*args, **kwargs)
        target.write_bytes(b'external file appeared')
    monkeypatch.setattr(actions.Image.Image, 'save', save_and_create)
    with pytest.raises(FileExistsError):
        actions._write_export(pixels, target, source, 'PNG', None)
    assert target.read_bytes() == b'external file appeared'
    assert not list(tmp_path.glob('.luma-export-*'))


def test_atomic_no_clobber_closes_check_publish_race(source, pixels, tmp_path, monkeypatch):
    target = tmp_path / 'new.png'
    publish_name = 'rename' if actions.os.name == 'nt' else 'link'
    real_link = getattr(actions.os, publish_name)
    def collide(temp, final):
        target.write_bytes(b'racing external file')
        return real_link(temp, final)
    monkeypatch.setattr(actions.os, publish_name, collide)
    with pytest.raises(FileExistsError):
        actions._write_export(pixels, target, source, 'PNG', None)
    assert target.read_bytes() == b'racing external file'
    assert not list(tmp_path.glob('.luma-export-*'))


def test_changed_overwrite_target_not_replaced(source, pixels, tmp_path, monkeypatch):
    target = tmp_path / 'existing.png'
    target.write_bytes(b'old')
    expected = actions._file_signature(target)
    target.write_bytes(b'new externally written content')
    with pytest.raises(FileExistsError):
        actions._write_export(pixels, target, source, 'PNG', expected)
    assert target.read_bytes() == b'new externally written content'


def test_copy_displayed_image_detached_rotated_snapshot(qapp, pixels):
    rotated = pixels.transformed(QTransform().rotate(90), Qt.TransformationMode.FastTransformation)
    assert actions.copy_displayed_image(rotated)
    clipboard = QApplication.clipboard().image()
    assert clipboard.size() == rotated.size()
    assert clipboard == rotated
    before = clipboard.pixelColor(0, 0)
    rotated.fill(Qt.GlobalColor.blue)
    assert QApplication.clipboard().image().pixelColor(0, 0) == before
    assert not actions.copy_displayed_image(QImage())
    QApplication.clipboard().clear()


@pytest.mark.parametrize('confirm', [QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.Cancel])
def test_recycle_confirmation_unicode_mock_only(qapp, source, monkeypatch, messages, confirm):
    original = source.read_bytes()
    seen = []
    monkeypatch.setattr(actions.QFile, 'supportsMoveToTrash', lambda: True)
    monkeypatch.setattr(actions.QFile, 'moveToTrash', lambda path: (seen.append(path) or True, ''))
    questions = []
    def question(*args):
        questions.append(args)
        return confirm
    monkeypatch.setattr(actions.QMessageBox, 'question', question)
    assert actions.recycle_source(None, source) == (confirm == QMessageBox.StandardButton.Yes)
    assert seen == ([source.as_posix()] if confirm == QMessageBox.StandardButton.Yes else [])
    assert source.name in questions[0][2]
    assert questions[0][-1] == QMessageBox.StandardButton.Cancel
    assert source.read_bytes() == original


@pytest.mark.parametrize('supported,success', [(False, False), (True, False)])
def test_recycle_unavailable_or_failed_never_permanently_deletes(qapp, source, monkeypatch, messages, supported, success):
    original = source.read_bytes()
    calls = []
    monkeypatch.setattr(actions.QFile, 'supportsMoveToTrash', lambda: supported)
    monkeypatch.setattr(actions.QFile, 'moveToTrash', lambda path: (calls.append(path) or success, ''))
    monkeypatch.setattr(actions.QFile, 'remove', lambda *args: pytest.fail('Permanent delete is forbidden'))
    assert not actions.recycle_source(None, source)
    assert source.read_bytes() == original
    assert len(calls) == int(supported)
    assert any(kind == 'critical' for kind, _ in messages)


def test_recycle_refuses_directory_or_missing_source(qapp, tmp_path, monkeypatch, messages):
    monkeypatch.setattr(actions.QFile, 'moveToTrash', lambda *args: pytest.fail('Must not recycle'))
    assert not actions.recycle_source(None, tmp_path)
    assert not actions.recycle_source(None, tmp_path / 'missing.png')
