"""Explicit image actions on detached GUI display snapshots.

These helpers never read or mutate the worker's full-precision pixel arrays.
Save/Copy include the caller's displayed rotation, channel and exposure. Recycle
is the only source-changing action, and never falls back to permanent deletion.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import tempfile

from PIL import Image
from PySide6.QtCore import QFile, Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QVBoxLayout, QWidget,
)

from .decoders import RAW_EXTENSIONS
from .i18n import tr, error_text, localize_buttons
from .dialogs import PlainMessageBox as QMessageBox


@dataclass(frozen=True)
class ExportFormat:
    key: str
    label: str
    suffixes: tuple[str, ...]


EXPORT_FORMATS = (
    ExportFormat('PNG', 'Displayed SDR PNG (.png) · lossless', ('.png',)),
    ExportFormat('JPEG', 'Displayed SDR JPEG (.jpg) · lossy, white background', ('.jpg', '.jpeg')),
    ExportFormat('TIFF', 'Displayed SDR TIFF (.tif) · 8-bit, lossless', ('.tif', '.tiff')),
    ExportFormat('WEBP', 'Displayed SDR WebP (.webp) · lossless', ('.webp',)),
)
_FORMATS = {item.key: item for item in EXPORT_FORMATS}
_HDR_EXTENSIONS = RAW_EXTENSIONS | {'.exr', '.hdr', '.rgbe', '.pic'}


@dataclass(frozen=True)
class ExportChoice:
    path: Path
    format: str


def _same_source(destination: Path, source: Path) -> bool:
    """Protect source paths, symbolic aliases and hard-link aliases."""
    if destination.resolve() == source.resolve():
        return True
    try:
        return destination.samefile(source)
    except FileNotFoundError:
        return False


def validated_destination(path: Path, source: Path, format: str) -> Path:
    """Validate the chosen path; only an absent extension is appended."""
    if format not in _FORMATS:
        raise ValueError(tr('Choose PNG, JPEG, TIFF or WebP for this display export.'))
    destination = Path(path).expanduser().absolute()
    spec = _FORMATS[format]
    if not destination.suffix:
        destination = destination.with_suffix(spec.suffixes[0])
    elif destination.suffix.lower() not in spec.suffixes:
        raise ValueError(
            tr('The filename extension does not match {format}. '
               'Use {extensions} or choose another format.',
               format=format, extensions=tr(' or ').join(spec.suffixes))
        )
    if _same_source(destination, source):
        raise ValueError(tr('The original source is protected. Choose a different filename.'))
    if destination.is_symlink():
        raise ValueError(tr('Choose a regular destination file, not a symbolic link.'))
    if not destination.parent.is_dir():
        raise ValueError(tr('The destination folder does not exist. Choose an existing folder.'))
    if destination.exists() and not destination.is_file():
        raise ValueError(tr('The destination must be a file, not a folder.'))
    return destination


class ExportDialog(QDialog):
    """Review output meaning, format and filename before any bytes are written."""

    def __init__(self, parent: QWidget | None, source: Path, linear: bool = False):
        super().__init__(parent)
        self.source = Path(source)
        self.choice: ExportChoice | None = None
        self.setWindowTitle(tr('Save As · displayed image'))
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        explanation = QLabel(
            tr('Save a separate copy of the displayed image, including the current rotation, '
               'channel and exposure. Every format below is 8-bit SDR. Source metadata is '
               'stripped. Full-precision HDR, RAW and source-format export are not supported.')
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        if linear or self.source.suffix.lower() in _HDR_EXTENSIONS:
            warning = QLabel(
                tr('HDR / RAW: the displayed image has already been tone-mapped to 8-bit SDR. '
                   'This export cannot retain the original dynamic range or RAW sensor data.')
            )
            warning.setObjectName('exportHdrWarning')
            warning.setWordWrap(True)
            layout.addWidget(warning)
        form = QFormLayout()
        self.format_combo = QComboBox(objectName='exportFormat')
        self.format_combo.addItem(tr('Choose an output format…'), None)
        for spec in EXPORT_FORMATS:
            self.format_combo.addItem(tr(spec.label), spec.key)
        form.addRow(tr('Format'), self.format_combo)
        filename_row = QHBoxLayout()
        self.filename = QLineEdit(str(self.source.with_name(self.source.stem + '-display')))
        self.filename.setObjectName('exportFilename')
        filename_row.addWidget(self.filename)
        browse = QPushButton(tr('Folder…'))
        browse.clicked.connect(self._choose_folder)
        filename_row.addWidget(browse)
        form.addRow(tr('Save to'), filename_row)
        layout.addLayout(form)
        self.format_note = QLabel(tr('Select a format to continue.'))
        self.format_note.setWordWrap(True)
        layout.addWidget(self.format_note)
        self.acknowledge = QCheckBox(tr('Export the displayed 8-bit SDR image; leave the source unchanged'))
        self.acknowledge.setObjectName('exportDisplayAcknowledged')
        layout.addWidget(self.acknowledge)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        localize_buttons(self.buttons)
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setText(tr('Export displayed image'))
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self.format_combo.currentIndexChanged.connect(self._format_changed)
        self.acknowledge.toggled.connect(self._update_enabled)
        layout.addWidget(self.buttons)
        self._update_enabled()

    def _choose_folder(self):
        path = Path(self.filename.text()).expanduser()
        directory = QFileDialog.getExistingDirectory(self, tr('Choose export folder'), str(path.parent))
        if directory:
            self.filename.setText(str(Path(directory) / path.name))

    def _format_changed(self):
        key = self.format_combo.currentData()
        if key:
            spec = _FORMATS[key]
            path = Path(self.filename.text())
            # A format change is visible in the filename field before export.
            known_suffixes = {suffix for item in EXPORT_FORMATS for suffix in item.suffixes}
            if not path.suffix or path.suffix.lower() in known_suffixes:
                self.filename.setText(str(path.with_suffix(spec.suffixes[0])))
            self.format_note.setText(
                tr('JPEG uses lossy compression (quality 95). Transparent pixels are composited on white.')
                if key == 'JPEG' else
                tr('Lossless compression of the displayed 8-bit pixels. Transparency is preserved.')
            )
        self._update_enabled()

    def _update_enabled(self):
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(
            self.format_combo.currentData() is not None and self.acknowledge.isChecked()
        )

    def accept(self):
        key = self.format_combo.currentData()
        if key is None or not self.acknowledge.isChecked():
            return
        try:
            if not self.filename.text().strip():
                raise ValueError(tr('Enter an output filename.'))
            destination = validated_destination(Path(self.filename.text()), self.source, key)
        except (OSError, ValueError, RuntimeError) as error:
            QMessageBox.warning(
                self, tr('Choose a different destination'),
                error_text('Choose another destination and try again.', error),
            )
            return
        self.filename.setText(str(destination))
        self.choice = ExportChoice(destination, key)
        super().accept()


def _file_signature(path: Path) -> tuple[int, int, int, int, int] | None:
    try:
        result = path.stat()
    except FileNotFoundError:
        return None
    return result.st_dev, result.st_ino, result.st_size, result.st_mtime_ns, result.st_ctime_ns


def _display_pixels(image: QImage) -> Image.Image:
    if image.isNull():
        raise ValueError(tr('There is no displayed image to export.'))
    rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
    # Construct from bytes, never from an external QImage buffer or source metadata.
    return Image.frombytes('RGBA', (rgba.width(), rgba.height()),
                           bytes(rgba.constBits()), 'raw', 'RGBA', rgba.bytesPerLine(), 1)


def _write_export(image: QImage, destination: Path, source: Path, format: str,
                  expected: tuple[int, int, int, int, int] | None) -> Path:
    """Encode first, then atomically publish; refuse changed or new destinations."""
    destination = validated_destination(destination, source, format)
    pixels = _display_pixels(image)
    options: dict = {}
    if format == 'JPEG':
        flattened = Image.new('RGB', pixels.size, 'white')
        flattened.paste(pixels, mask=pixels.getchannel('A'))
        pixels = flattened
        options = {'quality': 95, 'subsampling': 0}
    elif format == 'WEBP':
        options = {'lossless': True, 'exact': True}
    elif format == 'TIFF':
        options = {'compression': 'tiff_deflate'}
    temporary: Path | None = None
    try:
        fd, name = tempfile.mkstemp(prefix='.luma-export-', suffix='.tmp', dir=destination.parent)
        temporary = Path(name)
        with os.fdopen(fd, 'w+b') as stream:
            pixels.save(stream, format=format, **options)
            stream.flush()
            os.fsync(stream.fileno())
        validated_destination(destination, source, format)
        if _file_signature(destination) != expected:
            raise FileExistsError(tr('The destination changed while exporting. Choose Save As again to review it.'))
        if expected is None:
            # Atomic no-clobber publication. Unlike replace(), a file appearing
            # after the check cannot be silently overwritten without consent.
            if os.name == 'nt':
                # Windows rename is atomic and refuses an existing target; it
                # also works on FAT/exFAT, which do not support hard links.
                os.rename(temporary, destination)
            else:
                os.link(temporary, destination)
        else:
            os.replace(temporary, destination)
        return destination
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def save_displayed_image(parent: QWidget | None, image: QImage, source: Path,
                         linear: bool) -> Path | None:
    """Prompt for an explicit display-only export. Call on the GUI thread."""
    if image.isNull():
        return None
    snapshot = image.copy()
    source = Path(source)
    dialog = ExportDialog(parent, source, linear)
    if dialog.exec() != QDialog.DialogCode.Accepted or dialog.choice is None:
        return None
    choice = dialog.choice
    try:
        destination = validated_destination(choice.path, source, choice.format)
        if linear or source.suffix.lower() in _HDR_EXTENSIONS:
            answer = QMessageBox.warning(
                parent, tr('Confirm 8-bit SDR export'),
                tr('Export the displayed image from “{source}” as {format}?\n\n'
                   'HDR / RAW data has already been tone-mapped to 8-bit SDR. Original dynamic '
                   'range, RAW sensor data and source metadata will not be included. '
                   'This is not a full-precision export. The source file stays unchanged.',
                   source=source.name, format=choice.format),
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Save:
                return None
        expected = _file_signature(destination)
        if expected is not None:
            answer = QMessageBox.question(
                parent, tr('Replace exported file?'),
                tr('An output file already exists:\n{destination}\n\n'
                   'Replace it with this displayed 8-bit SDR image? This cannot be undone.',
                   destination=destination),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return None
        return _write_export(snapshot, destination, source, choice.format, expected)
    except (OSError, ValueError, RuntimeError) as error:
        QMessageBox.critical(
            parent, tr('Unable to export image'),
            error_text('The displayed image could not be exported. Check the destination and try again.', error),
        )
        return None


def copy_displayed_image(image: QImage) -> bool:
    """Copy detached displayed SDR pixels, including caller-applied rotation."""
    if image.isNull() or QApplication.instance() is None:
        return False
    QApplication.clipboard().setImage(image.copy())
    return True


def recycle_source(parent: QWidget | None, source: Path) -> bool:
    """Confirm a named source and move it to the OS recycle bin only."""
    source = Path(source).absolute()
    try:
        if not source.is_file():
            raise OSError(tr('This source file no longer exists or is not a regular file.'))
        if not QFile.supportsMoveToTrash():
            raise OSError(tr('The OS recycle bin is unavailable. Luma will not permanently delete files.'))
        answer = QMessageBox.question(
            parent, tr('Move image to Recycle Bin?'),
            tr('Move this original image to the system Recycle Bin / Trash?\n\n{source}\n\n'
               'You can restore it using your operating system. Luma will not permanently delete it.',
               source=source),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return False
        # PySide6's static overload returns (success, pathInTrash). The latter
        # can be empty on Windows even when recycling succeeds.
        moved, _ = QFile.moveToTrash(source.as_posix())
        if not moved:
            raise OSError(tr('The OS could not move this file to its recycle bin. No permanent-delete fallback was used.'))
        return True
    except (OSError, RuntimeError) as error:
        QMessageBox.critical(
            parent, tr('Unable to recycle image'),
            error_text('The image could not be moved to the Recycle Bin. '
                       'The source file has not been permanently deleted.', error),
        )
        return False
