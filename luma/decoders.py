"""Read-only, bounded image decoding and SDR display transforms for Luma.

Pixels are straight/associated as documented in ``metadata['Alpha mode']``.
Display output is always *straight* 8-bit sRGB RGBA. Source arrays are never
changed by exposure, channel isolation, or tone mapping. Optional format
libraries are imported only when their format is opened.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from io import BytesIO
import math
import os
from pathlib import Path
import re
import struct
import threading
import warnings
from typing import BinaryIO, Callable

import numpy as np
from PIL import Image, ImageCms, UnidentifiedImageError

from .photo_metadata import extract_exif_metadata, extract_exr_metadata, extract_raw_metadata


# These ceilings are deliberately independent of compressed file size. They are
# safety bounds, not a promise that every accepted file fits available system RAM.
MAX_FILE_BYTES = 1024 * 1024 * 1024
MAX_PIXELS = 64_000_000
MAX_DIMENSION = 32768
MAX_DECODE_BYTES = 1536 * 1024 * 1024
MAX_HEADER_BYTES = 65536
MAX_ICC_BYTES = 4 * 1024 * 1024
MAX_EXR_CHANNELS = 64
MAX_EXR_PIXELS = 8192 * 8192  # Narrow EXR-only allowance for square 8K textures.
MAX_EXR_PREVIEW_PIXELS = 16384 * 16384
MAX_EXR_PREVIEW_EDGE = 1024
MAX_EXR_PREVIEW_FILE_BYTES = 256 * 1024 * 1024
MAX_EXR_STRIP_BYTES = 16 * 1024 * 1024
MAX_EXR_NATIVE_BYTES = 384 * 1024 * 1024
MAX_EXR_CHUNK_TABLE_BYTES = 16 * 1024 * 1024
EXR_THREADS = min(4, max(1, os.cpu_count() or 1))
_exr_configuration_lock = threading.Lock()
_exr_configured = False
# Thumbnails never allocate full-source float pixels. This separate ceiling
# admits common 16K environment maps while bounding parsing work.
MAX_RADIANCE_PREVIEW_PIXELS = 256_000_000
MAX_RADIANCE_PREVIEW_EDGE = 1024

RAW_EXTENSIONS = frozenset({
    '.nef', '.nrw', '.cr2', '.cr3', '.crw', '.arw', '.srf', '.sr2', '.dng',
    '.raf', '.orf', '.rw2', '.raw', '.pef', '.ptx', '.srw', '.kdc', '.dcr',
    '.mrw', '.mos', '.3fr', '.fff', '.iiq', '.rwl', '.erf',
})
PILLOW_EXTENSIONS = frozenset({
    '.jpg', '.jpeg', '.jpe', '.jfif', '.png', '.webp', '.bmp', '.dib',
    '.tif', '.tiff', '.tga', '.dds', '.gif', '.ico', '.ppm', '.pgm', '.pbm',
    '.pnm',
})
SUPPORTED_EXTENSIONS = PILLOW_EXTENSIONS | RAW_EXTENSIONS | {'.exr', '.hdr', '.rgbe', '.pic'}


class DecodeError(ValueError):
    """The image cannot be decoded safely or correctly by this viewer."""


class UnsupportedImageError(DecodeError):
    """The file or one of its layouts is outside the supported formats."""


class ImageTooLargeError(DecodeError):
    """A compressed input, image dimension, or allocation exceeds a ceiling."""


class DecodeCancelled(RuntimeError):
    """Cooperative image work was superseded; this is not a damaged-file error."""


def _check_cancelled(cancelled: Callable[[], bool] | None) -> None:
    if cancelled is not None and cancelled():
        raise DecodeCancelled('Image operation cancelled.')


@dataclass
class ImageData:
    path: Path
    pixels: np.ndarray
    linear: bool
    metadata: dict[str, str] = field(default_factory=dict)
    channel_names: tuple[str, ...] = ('R', 'G', 'B', 'A')
    _pixel_limit: int | None = field(default=None, repr=False)
    photo_metadata: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        if (not isinstance(self.pixels, np.ndarray) or self.pixels.ndim != 3
                or self.pixels.shape[2] != 4
                or self.pixels.dtype not in (np.dtype('float32'), np.dtype('uint8'))):
            raise DecodeError('Image pixels must be an H×W×4 float32 or uint8 array.')
        _check_size(self.width, self.height, self.pixels.dtype.itemsize * 4,
                    pixel_limit=self._pixel_limit)
        self.pixels = np.ascontiguousarray(self.pixels)

    @property
    def width(self) -> int:
        return int(self.pixels.shape[1])

    @property
    def height(self) -> int:
        return int(self.pixels.shape[0])

    @property
    def nbytes(self) -> int:
        return int(self.pixels.nbytes)


def _check_size(width: int, height: int, bytes_per_pixel: int = 16, *,
                pixel_limit: int | None = None) -> None:
    width, height = int(width), int(height)
    pixel_limit = MAX_PIXELS if pixel_limit is None else min(pixel_limit, MAX_EXR_PIXELS)
    if width <= 0 or height <= 0:
        raise DecodeError(f'Invalid image dimensions: {width} × {height}.')
    if width > MAX_DIMENSION or height > MAX_DIMENSION or width * height > pixel_limit:
        raise ImageTooLargeError(
            f'Image is too large: {width:,} × {height:,}. The safety limit is '
            f'{pixel_limit:,} pixels and {MAX_DIMENSION:,} pixels per side.')
    if width * height * bytes_per_pixel > MAX_DECODE_BYTES:
        raise ImageTooLargeError(
            f'Image requires too much decoded memory (limit '
            f'{MAX_DECODE_BYTES // (1024 * 1024):,} MiB).')


def _limited_text(value: object, limit: int = 500) -> str:
    text = str(value)
    return text if len(text) <= limit else text[:limit] + '…'


def _add_limitation(metadata: dict[str, str], text: str) -> None:
    prior = metadata.get('Limitations', '')
    metadata['Limitations'] = f'{prior}; {text}' if prior else text


def decode_image(path: Path, *, cancelled: Callable[[], bool] | None = None) -> ImageData:
    """Decode a local image without modifying it, with helpful bounded errors.

    RAW camera support depends on the installed LibRaw version and camera model.
    Animated/multipage images display only their first frame/page. EXR supports
    one flat part, displaying an RGB(A), Y, or scalar channel set.
    """
    path = Path(path).expanduser()
    try:
        _check_cancelled(cancelled)
        if not path.is_file():
            raise DecodeError(f'Image file does not exist or is not a regular file: {path.name}')
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            raise ImageTooLargeError(
                f'File is too large ({size / (1024**2):,.1f} MiB); '
                f'the safety limit is {MAX_FILE_BYTES // (1024**2):,} MiB.')
        if not size:
            raise DecodeError('The image file is empty.')
        suffix = path.suffix.lower()
        if suffix in RAW_EXTENSIONS:
            result = _decode_raw(path)
        elif suffix == '.exr':
            result = _decode_exr(path, cancelled=cancelled)
        elif suffix in {'.hdr', '.rgbe', '.pic'}:
            result = _decode_radiance(path, cancelled=cancelled)
        elif suffix in PILLOW_EXTENSIONS:
            result = _decode_pillow(path)
        else:
            raise UnsupportedImageError(f'Unsupported image extension: {suffix or "(none)"}.')
        _check_cancelled(cancelled)
        result.metadata.setdefault('File', path.name)
        result.metadata.setdefault('File size', f'{size:,} bytes')
        result.metadata.setdefault('Dimensions', f'{result.width:,} × {result.height:,}')
        result.metadata.setdefault('Display', '8-bit sRGB; scene-linear sources are tone-mapped for SDR')
        return result
    except (DecodeError, DecodeCancelled):
        raise
    except MemoryError as exc:
        raise ImageTooLargeError('There is not enough memory to decode this image safely.') from exc
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ImageTooLargeError('Image dimensions exceed the decoder’s safety limit.') from exc
    except (OSError, ValueError, RuntimeError, EOFError, struct.error, OverflowError) as exc:
        raise DecodeError(f'Could not read {path.name}: {_limited_text(exc, 300)}') from exc


def _pillow_source_depth(source: Image.Image, path: Path) -> str:
    if source.format == 'PNG':
        with path.open('rb') as stream:
            header = stream.read(29)
        if len(header) >= 25 and header[:8] == b'\x89PNG\r\n\x1a\n':
            return f'{header[24]}-bit per source sample'
    if source.format == 'TIFF':
        bits = getattr(source, 'tag_v2', {}).get(258)
        if bits:
            return f'{", ".join(map(str, bits if isinstance(bits, tuple) else (bits,)))}-bit source samples'
    if source.mode == 'F':
        return '32-bit floating-point'
    if source.mode == 'I':
        return '32-bit integer'
    if source.mode.startswith('I;16'):
        return '16-bit integer'
    if source.mode == '1':
        return '1-bit'
    if source.mode == 'P':
        return 'Indexed palette; 8-bit color components'
    return '8-bit per decoded channel'


def _decode_pillow(path: Path) -> ImageData:
    # A stream avoids Pillow's filename-mmap path, which uses already-oriented
    # TIFF dimensions as raw storage geometry for mapped grayscale modes.
    with warnings.catch_warnings(), path.open('rb') as stream:
        warnings.simplefilter('error', Image.DecompressionBombWarning)
        try:
            source = Image.open(stream)
        except UnidentifiedImageError as exc:
            raise DecodeError('Unrecognized or damaged image data.') from exc
        with source:
            _check_size(*source.size, 16 if source.mode in ('I', 'F') or source.mode.startswith('I;16') else 4)
            metadata = {
                'Format': str(source.format or path.suffix[1:].upper()),
                'Decoder': f'Pillow {Image.__version__}',
                'Source mode': source.mode,
                'Source depth': _pillow_source_depth(source, path),
                'Color profile': 'Untagged; assumed sRGB',
                'Alpha mode': 'straight',
            }
            # Pillow's TIFF n_frames can scan the entire file. is_animated alone
            # is enough for GIF/WebP; detect a second TIFF page without counting.
            if source.format in ('GIF', 'WEBP', 'PNG') and getattr(source, 'is_animated', False):
                _add_limitation(metadata, 'First animation frame only; animation is not played')
            if source.format == 'TIFF':
                try:
                    source.seek(1)
                except EOFError:
                    pass
                else:
                    _add_limitation(metadata, 'First TIFF page only')
                finally:
                    source.seek(0)
            icc_bytes = source.info.get('icc_profile')
            if icc_bytes and len(icc_bytes) > MAX_ICC_BYTES:
                raise ImageTooLargeError('Embedded ICC profile exceeds the 4 MiB safety limit.')
            photo_metadata = {}
            exif = None
            try:
                exif = source.getexif()
                for tag, title in ((271, 'Camera make'), (272, 'Camera model'),
                                   (306, 'Date'), (33434, 'Exposure time'),
                                   (33437, 'Aperture'), (34855, 'ISO'), (42036, 'Lens')):
                    if tag in exif:
                        metadata[title] = _limited_text(exif[tag])
                orientation = exif.get(274, 1)
            except (ValueError, TypeError, OSError, SyntaxError):
                orientation = 1
                _add_limitation(metadata, 'Malformed EXIF metadata could not be read')
            if exif is not None:
                try:
                    photo_metadata = extract_exif_metadata(exif)
                except (ValueError, TypeError, OSError, SyntaxError, OverflowError,
                        ZeroDivisionError, struct.error, AttributeError):
                    # Optional detail parsing cannot alter a valid orientation.
                    pass
            # Capture orientation before loading; Pillow consumes TIFF orientation.
            # Handle other formats directly without parsing malformed EXIF again.
            operation = {
                2: Image.Transpose.FLIP_LEFT_RIGHT, 3: Image.Transpose.ROTATE_180,
                4: Image.Transpose.FLIP_TOP_BOTTOM, 5: Image.Transpose.TRANSPOSE,
                6: Image.Transpose.ROTATE_270, 7: Image.Transpose.TRANSVERSE,
                8: Image.Transpose.ROTATE_90,
            }.get(orientation)
            source.load()
            working = (source.transpose(operation)
                       if operation is not None and source.format != 'TIFF' else source)
            metadata['Orientation'] = f'EXIF {orientation}; applied' if operation is not None else 'As stored'
            _check_size(*working.size, 16 if working.mode in ('I', 'F') or working.mode.startswith('I;16') else 4)

            # Pillow retains high precision for single-channel sources. RGB
            # PNG/TIFF above 8 bits is decoded at 8 bits by Pillow, explicitly
            # disclosed below instead of claiming original precision.
            high_precision = working.mode in ('I', 'F') or working.mode.startswith('I;16')
            if high_precision:
                samples = np.asarray(working)
                pixels = np.empty((working.height, working.width, 4), dtype=np.float32)
                linear = working.mode == 'F'
                scale = 1.0
                if not linear:
                    max_value = 65535 if '16-bit' in metadata['Source depth'] or working.mode.startswith('I;16') else np.iinfo(samples.dtype).max
                    scale = 1.0 / float(max_value)
                np.multiply(samples[..., None], scale, out=pixels[..., :3], casting='unsafe')
                pixels[..., 3] = 1.0
                metadata['Decoded depth'] = 'float32 grayscale expanded to RGBA'
                if linear:
                    metadata['Color profile'] = 'Floating grayscale; assumed scene-linear'
                    _add_limitation(metadata, 'Floating TIFF transfer function is not inferred from image contents')
                else:
                    metadata['Color profile'] = 'High-bit-depth grayscale; assumed sRGB transfer'
                if icc_bytes:
                    _add_limitation(metadata, 'Embedded ICC not applied to high-bit-depth grayscale')
                return ImageData(path, pixels, linear, metadata, photo_metadata=photo_metadata)

            rgba = working.convert('RGBA')
            if icc_bytes:
                try:
                    profile = ImageCms.ImageCmsProfile(BytesIO(icc_bytes))
                    profile_name = ImageCms.getProfileDescription(profile).strip()
                    cms_source = working if working.mode in ('RGB', 'RGBA', 'CMYK', 'LAB', 'L') else working.convert('RGB')
                    converted = ImageCms.profileToProfile(
                        cms_source, profile, ImageCms.createProfile('sRGB'), outputMode='RGB')
                    converted.putalpha(rgba.getchannel('A'))
                    rgba = converted
                    metadata['Color profile'] = f'{_limited_text(profile_name, 180)} → sRGB'
                except (ImageCms.PyCMSError, OSError, ValueError, TypeError) as exc:
                    metadata['Color profile'] = 'Embedded ICC invalid/unsupported; assumed sRGB'
                    _add_limitation(metadata, f'ICC conversion failed: {_limited_text(exc, 160)}')
            elif working.mode in ('CMYK', 'LAB'):
                _add_limitation(metadata, 'Untagged CMYK/Lab converted using Pillow defaults; colors may differ')
            depth = metadata['Source depth']
            if ('16-bit' in depth or '32-bit' in depth) and working.mode in ('RGB', 'RGBA'):
                _add_limitation(metadata, 'Pillow reduces this multichannel source to 8 bits per channel')
            metadata['Decoded depth'] = '8-bit RGBA'
            return ImageData(path, np.array(rgba, dtype=np.uint8), False, metadata,
                             photo_metadata=photo_metadata)


def _read_exact(stream: BinaryIO, count: int) -> bytes:
    data = stream.read(count)
    if len(data) != count:
        raise DecodeError('Truncated Radiance HDR pixel data.')
    return data


def _read_rgbe_scanline(stream: BinaryIO, length: int) -> np.ndarray:
    first = _read_exact(stream, 4)
    scan = np.empty((length, 4), dtype=np.uint8)
    modern = 8 <= length <= 32767 and first[:2] == b'\x02\x02' and not first[2] & 128
    if modern:
        if (first[2] << 8 | first[3]) != length:
            raise DecodeError('Radiance HDR RLE scanline width does not match its header.')
        for component in range(4):
            # Expand packets into a byte buffer, not a strided NumPy slice for
            # every tiny run. NumPy receives one contiguous plane per channel.
            plane = bytearray(length)
            cursor = 0
            while cursor < length:
                code = _read_exact(stream, 1)[0]
                count = code - 128 if code > 128 else code
                if not count or cursor + count > length:
                    raise DecodeError('Invalid or oversized Radiance HDR RLE run.')
                if code > 128:
                    plane[cursor:cursor + count] = _read_exact(stream, 1) * count
                else:
                    plane[cursor:cursor + count] = _read_exact(stream, count)
                cursor += count
            scan[:, component] = np.frombuffer(plane, dtype=np.uint8)
        return scan

    # The overwhelmingly common non-RLE case is an entire literal scanline.
    # Read it in one block and locate legacy markers with vectorized comparisons
    # rather than doing a Python read and assignment for every source pixel.
    # A legacy run may mean this read includes later scanlines; rewind before
    # parsing the first marker so the stream remains positioned exactly.
    payload = first + stream.read((length - 1) * 4)
    available = len(payload) // 4
    literals = np.frombuffer(payload, dtype=np.uint8, count=available * 4).reshape(-1, 4)
    markers = np.flatnonzero((literals[:, 0] == 1) & (literals[:, 1] == 1) & (literals[:, 2] == 1))
    if not markers.size:
        if len(payload) != length * 4:
            raise DecodeError('Truncated Radiance HDR pixel data.')
        return literals
    prefix = int(markers[0])
    scan[:prefix] = literals[:prefix]
    stream.seek(prefix * 4 - len(payload), 1)

    # Old RGBE run markers are (1,1,1,count). Consecutive markers add high
    # bytes to the repeat count. Bounds are checked before filling any run.
    cursor, shift = prefix, 0
    pixel = _read_exact(stream, 4)
    while cursor < length:
        if pixel[:3] == b'\x01\x01\x01':
            if cursor == 0 or shift > 24:
                raise DecodeError('Invalid legacy Radiance HDR run marker.')
            count = pixel[3] << shift
            if cursor + count > length:
                raise DecodeError('Legacy Radiance HDR run exceeds its scanline.')
            scan[cursor:cursor + count] = scan[cursor - 1]
            cursor += count
            shift += 8
        else:
            scan[cursor] = tuple(pixel)
            cursor += 1
            shift = 0
        if cursor < length:
            pixel = _read_exact(stream, 4)
    return scan


def _decode_radiance(path: Path, *, cancelled: Callable[[], bool] | None = None,
                     preview_edge: int | None = None) -> ImageData:
    with path.open('rb') as stream:
        signature = stream.readline(4097).rstrip(b'\r\n')
        if signature not in (b'#?RADIANCE', b'#?RGBE'):
            raise DecodeError('Not a Radiance HDR/RGBE file (missing signature).')
        header: dict[str, str] = {}
        consumed = len(signature)
        while True:
            line = stream.readline(4097)
            consumed += len(line)
            if not line:
                raise DecodeError('Truncated Radiance HDR header.')
            if len(line) > 4096 or consumed > MAX_HEADER_BYTES:
                raise ImageTooLargeError('Radiance HDR header exceeds its safety limit.')
            if not line.strip():
                break
            if b'=' in line:
                key, value = line.strip().split(b'=', 1)
                header[key.decode('ascii', errors='replace')] = value.decode('ascii', errors='replace')
        encoding = header.get('FORMAT', '32-bit_rle_rgbe')
        if encoding not in ('32-bit_rle_rgbe', '32-bit_rgbe'):
            raise UnsupportedImageError(f'Radiance encoding {encoding!r} is unsupported; RGBE is required (not XYZE).')
        line = stream.readline(4097)
        match = re.fullmatch(rb'\s*([+-])([XY])\s+(\d+)\s+([+-])([XY])\s+(\d+)\s*', line)
        if not match or match[2] == match[5]:
            raise DecodeError('Invalid Radiance HDR resolution/orientation line.')
        major_sign, major_axis, major_count, minor_sign, minor_axis, minor_count = match.groups()
        major_count, minor_count = int(major_count), int(minor_count)
        width, height = (major_count, minor_count) if major_axis == b'X' else (minor_count, major_count)
        if preview_edge is None:
            _check_size(width, height, 16)
            output_width, output_height = width, height
        else:
            if width <= 0 or height <= 0:
                raise DecodeError(f'Invalid image dimensions: {width} × {height}.')
            if max(width, height) > MAX_DIMENSION or width * height > MAX_RADIANCE_PREVIEW_PIXELS:
                raise ImageTooLargeError('Radiance HDR source exceeds the bounded-preview safety limit.')
            scale = min(1.0, preview_edge / max(width, height))
            output_width, output_height = max(1, int(width * scale)), max(1, int(height * scale))
            _check_size(output_width, output_height, 16)
        _check_cancelled(cancelled)
        pixels = np.empty((output_height, output_width, 4), dtype=np.float32)
        pixels[..., 3] = 1.0
        output_major, output_minor = ((output_height, output_width) if major_axis == b'Y'
                                      else (output_width, output_height))
        # Sample only selected RGBE pixels before float conversion. Work memory
        # is one encoded scanline plus the small output, even for 16K sources.
        major_indices = ((np.arange(output_major) + 0.5) * major_count / output_major).astype(np.int32)
        minor_indices = ((np.arange(output_minor) + 0.5) * minor_count / output_minor).astype(np.int32)
        major_forward = major_sign == (b'-' if major_axis == b'Y' else b'+')
        minor_forward = minor_sign == (b'+' if minor_axis == b'X' else b'-')
        if not major_forward:
            major_indices = major_count - 1 - major_indices[::-1]
        if not minor_forward:
            minor_indices = minor_count - 1 - minor_indices[::-1]
        next_output = 0
        for index in range(major_count):
            _check_cancelled(cancelled)
            rgbe = _read_rgbe_scanline(stream, minor_count)
            if next_output >= output_major or index != major_indices[next_output]:
                continue
            if output_minor != minor_count:
                rgbe = rgbe[minor_indices]
            exponent = rgbe[:, 3].astype(np.int32)
            # Decode the center of each quantization bin, as Radiance's
            # reference colr_color implementation does; exponent zero is black.
            rgb = np.ldexp(rgbe[:, :3].astype(np.float32) + 0.5, exponent[:, None] - 136)
            rgb[exponent == 0] = 0
            if major_axis == b'Y':
                row = next_output if major_sign == b'-' else output_height - 1 - next_output
                pixels[row, :, :3] = rgb if minor_sign == b'+' else rgb[::-1]
            else:
                column = next_output if major_sign == b'+' else output_width - 1 - next_output
                pixels[:, column, :3] = rgb if minor_sign == b'-' else rgb[::-1]
            next_output += 1
        metadata = {
            'Format': 'Radiance HDR / RGBE', 'Decoder': 'Luma native RGBE',
            'Source depth': '8-bit RGB mantissas + shared 8-bit exponent',
            'Decoded depth': '32-bit floating-point RGBA',
            'Color profile': 'Assumed linear sRGB primaries', 'Alpha mode': 'opaque',
            'Orientation': line.decode('ascii').strip() + '; applied',
            'Limitations': 'Radiance primaries are not color-managed; exposure/color-correction header values are preserved, not undone',
        }
        for key in ('EXPOSURE', 'GAMMA', 'PRIMARIES', 'COLORCORR', 'PIXASPECT', 'SOFTWARE'):
            if key in header:
                metadata[f'Radiance {key}'] = _limited_text(header[key])
        if preview_edge is not None:
            metadata['Source dimensions'] = f'{width:,} × {height:,}'
            _add_limitation(metadata, 'Bounded pixel-center sampled thumbnail; full-resolution source is not retained')
        return ImageData(path, pixels, True, metadata)


def decode_radiance_thumbnail(path: Path, edge: int, *,
                               cancelled: Callable[[], bool] | None = None) -> ImageData:
    """Read a bounded HDR thumbnail without allocating full-source RGB floats.

    All encoded scanlines are validated, but only selected pixel centers are
    converted. Source dimensions may exceed the full-image pixel ceiling;
    independent source/file/output limits still apply. This is a preview only.
    """
    if isinstance(edge, bool) or not isinstance(edge, int) or not 1 <= edge <= MAX_RADIANCE_PREVIEW_EDGE:
        raise ValueError(f'Thumbnail edge must be between 1 and {MAX_RADIANCE_PREVIEW_EDGE}.')
    _check_cancelled(cancelled)
    path = Path(path).expanduser()
    try:
        if not path.is_file():
            raise DecodeError(f'Image file does not exist or is not a regular file: {path.name}')
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ImageTooLargeError('Radiance HDR file exceeds its safety limit.')
        result = _decode_radiance(path, cancelled=cancelled, preview_edge=edge)
        _check_cancelled(cancelled)
        return result
    except (DecodeError, DecodeCancelled):
        raise
    except MemoryError as exc:
        raise ImageTooLargeError('There is not enough memory to decode this thumbnail safely.') from exc
    except (OSError, ValueError, RuntimeError, EOFError, struct.error, OverflowError) as exc:
        raise DecodeError(f'Could not read {path.name}: {_limited_text(exc, 300)}') from exc


def _configure_exr():
    """Configure the pinned official library once, before opening native files."""
    global _exr_configured
    try:
        import OpenEXR
    except ImportError as exc:
        raise UnsupportedImageError('OpenEXR support is not installed. Install the OpenEXR package.') from exc
    with _exr_configuration_lock:
        if not _exr_configured:
            # The selected-channel adapter was allocation-hardened in 3.4.3;
            # the 3.5 API also supplies native image/tile safety ceilings.
            if not all(hasattr(OpenEXR, name) for name in
                       ('InputFile', 'setMaxImageSize', 'setMaxTileSize', 'set_global_thread_count')):
                raise UnsupportedImageError('EXR decoding requires the pinned OpenEXR 3.5.2 package.')
            OpenEXR.setMaxImageSize(MAX_DIMENSION, MAX_DIMENSION)
            OpenEXR.setMaxTileSize(4096, 4096)
            OpenEXR.set_global_thread_count(EXR_THREADS)
            _exr_configured = True
    return OpenEXR


def _exr_file_stamp(path: Path) -> tuple[int, int, int, int]:
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns


def _bounded_exr_header(path: Path) -> bytes:
    """Bound header metadata before native parsing; never decode pixel blocks.

    Attribute payloads are skipped, not interpreted here. All actual EXR
    parsing and decompression remains in the official OpenEXR library.
    """
    with path.open('rb') as stream:
        header = stream.read(MAX_HEADER_BYTES + 1)
    if len(header) < 8 or header[:4] != b'\x76\x2f\x31\x01':
        raise DecodeError('Not an OpenEXR image (missing signature).')
    version = struct.unpack_from('<I', header, 4)[0]
    if version & 0x1000:
        raise UnsupportedImageError('Multipart EXR is not supported. Export a single flat RGB(A) part.')
    if version & 0x800:
        raise UnsupportedImageError('Deep EXR is not supported. Flatten the image to a regular RGB(A) EXR.')
    cursor = 8
    while cursor < len(header):
        if header[cursor] == 0:
            return header[:cursor + 1]
        for _ in range(2):  # attribute name and type, each at most 255 bytes
            end = header.find(b'\0', cursor, min(cursor + 256, len(header)))
            if end < 0:
                raise DecodeError('Invalid or truncated EXR header attribute.')
            cursor = end + 1
        if cursor + 4 > len(header):
            raise DecodeError('Truncated EXR header attribute size.')
        count = struct.unpack_from('<I', header, cursor)[0]
        cursor += 4 + count
        if cursor >= MAX_HEADER_BYTES:
            raise ImageTooLargeError('EXR header exceeds the 64 KiB safety limit.')
    raise DecodeError('Truncated EXR header.')


def _check_exr_chunk_table(header: bytes, *, preview: bool) -> None:
    """Validate allocation-relevant attributes before constructing InputFile."""
    attributes = {}
    cursor = 8
    while header[cursor]:
        end = header.index(b'\0', cursor)
        name = header[cursor:end]
        cursor = end + 1
        end = header.index(b'\0', cursor)
        kind = header[cursor:end]
        cursor = end + 1
        count = struct.unpack_from('<I', header, cursor)[0]
        cursor += 4
        if name in attributes:
            raise DecodeError('EXR contains duplicate header attributes.')
        attributes[name] = (kind, header[cursor:cursor + count])
        cursor += count
    kind, compression = attributes.get(b'compression', (b'', b''))
    if kind != b'compression' or len(compression) != 1 or compression[0] > 11:
        raise UnsupportedImageError('Unsupported or invalid EXR compression method.')
    if b'type' in attributes:
        kind, storage = attributes[b'type']
        if kind != b'string' or storage not in (b'scanlineimage', b'tiledimage'):
            if storage in (b'deepscanline', b'deeptile'):
                raise UnsupportedImageError('Deep EXR is not supported. Flatten the image first.')
            raise UnsupportedImageError('Unsupported EXR storage layout.')
    kind, channels = attributes.get(b'channels', (b'', b''))
    if kind != b'chlist':
        raise DecodeError('EXR has an invalid or missing channel list.')
    cursor, channel_count = 0, 0
    while cursor < len(channels) and channels[cursor]:
        end = channels.find(b'\0', cursor, min(cursor + 256, len(channels)))
        if end < 0 or end + 17 > len(channels):
            raise DecodeError('EXR has a truncated channel description.')
        sample_type, x_sampling, y_sampling = struct.unpack_from('<i4xii', channels, end + 1)
        if sample_type not in (0, 1, 2):
            raise UnsupportedImageError('Unsupported EXR channel sample type.')
        if x_sampling != 1 or y_sampling != 1:
            raise UnsupportedImageError('Subsampled EXR channels are not supported. Export full-resolution channels.')
        channel_count += 1
        if channel_count > MAX_EXR_CHANNELS:
            raise ImageTooLargeError(f'EXR must contain between 1 and {MAX_EXR_CHANNELS} channels.')
        cursor = end + 17
    if not channel_count:
        raise ImageTooLargeError(f'EXR must contain between 1 and {MAX_EXR_CHANNELS} channels.')
    if cursor != len(channels) - 1 or channels[cursor] != 0:
        raise DecodeError('EXR has an invalid channel-list terminator.')
    kind, window = attributes.get(b'dataWindow', (b'', b''))
    if kind != b'box2i' or len(window) != 16:
        raise DecodeError('EXR has an invalid or missing data window.')
    x0, y0, x1, y1 = struct.unpack('<4i', window)
    width, height = x1 - x0 + 1, y1 - y0 + 1
    _check_exr_dimensions(width, height, preview=preview)
    if b'tiles' not in attributes:
        return  # At most 32,768 scanline offsets under the dimension limit.
    kind, tile = attributes[b'tiles']
    if kind != b'tiledesc' or len(tile) != 9:
        raise DecodeError('EXR has an invalid tile description.')
    tw, th, mode = struct.unpack('<IIB', tile)
    if not (0 < tw <= 4096 and 0 < th <= 4096):
        raise ImageTooLargeError('EXR tile dimensions exceed the safety limit.')
    if mode & 0xEF not in (0, 1, 2):
        raise UnsupportedImageError('EXR has an unsupported tile level mode.')
    def levels(size):
        result = [size]
        while result[-1] > 1:
            result.append((result[-1] + bool(mode & 0x10)) // 2)
        return result
    widths, heights = levels(width), levels(height)
    if mode & 0xF == 0:
        sizes = [(width, height)]
    elif mode & 0xF == 1:
        sizes = [(widths[min(i, len(widths) - 1)], heights[min(i, len(heights) - 1)])
                 for i in range(max(len(widths), len(heights)))]
    else:
        sizes = [(w, h) for w in widths for h in heights]
    offsets = sum(((w + tw - 1) // tw) * ((h + th - 1) // th) for w, h in sizes)
    if offsets * 8 > MAX_EXR_CHUNK_TABLE_BYTES:
        raise ImageTooLargeError('EXR tile offset table exceeds the native memory safety limit.')


def _check_exr_dimensions(width: int, height: int, *, preview: bool) -> None:
    if preview:
        if not (0 < width <= MAX_DIMENSION and 0 < height <= MAX_DIMENSION
                and width * height <= MAX_EXR_PREVIEW_PIXELS):
            raise ImageTooLargeError('EXR dimensions exceed the thumbnail safety limit.')
    else:
        _check_size(width, height, 16, pixel_limit=MAX_EXR_PIXELS)


def _exr_channel_selection(names: tuple[str, ...]) -> tuple[list[str], str]:
    prefixes = sorted({name.rsplit('.', 1)[0] + '.' for name in names if '.' in name})
    prefix = next((candidate for candidate in ('', *prefixes)
                   if all(candidate + color in names for color in ('R', 'G', 'B'))), None)
    if prefix is not None:
        selected = [prefix + color for color in ('R', 'G', 'B')]
    else:
        name = next((name for name in ('Y', 'R', 'G', 'B', *names)
                     if name in names and name != 'A'), None)
        if name is None:
            name = 'A' if 'A' in names else names[0]
        prefix = name.rsplit('.', 1)[0] + '.' if '.' in name else ''
        selected = [name]
    return selected, prefix + 'A'


def _exr_layout(header: dict, *, preview: bool) -> tuple:
    storage = header.get('type', b'tiledimage' if 'tiles' in header else b'scanlineimage')
    if isinstance(storage, bytes):
        storage = storage.decode('ascii', errors='replace')
    if storage in ('deepscanline', 'deeptile'):
        raise UnsupportedImageError('Deep EXR is not supported. Flatten the image to a regular RGB(A) EXR.')
    if storage not in ('scanlineimage', 'tiledimage'):
        raise UnsupportedImageError(f'Unsupported EXR storage layout: {storage}.')
    window = header['dataWindow']
    minimum = (int(window.min.x), int(window.min.y))
    maximum = (int(window.max.x), int(window.max.y))
    width, height = maximum[0] - minimum[0] + 1, maximum[1] - minimum[1] + 1
    _check_exr_dimensions(width, height, preview=preview)
    descriptors = header['channels']
    if not descriptors or len(descriptors) > MAX_EXR_CHANNELS:
        raise ImageTooLargeError(f'EXR must contain between 1 and {MAX_EXR_CHANNELS} channels.')
    dtypes = {}
    for name, descriptor in descriptors.items():
        if descriptor.xSampling != 1 or descriptor.ySampling != 1:
            raise UnsupportedImageError('Subsampled EXR channels are not supported. Export full-resolution channels.')
        pixel_type = int(descriptor.type.v)
        if pixel_type not in (0, 1, 2):
            raise UnsupportedImageError('Unsupported EXR channel sample type.')
        dtypes[name] = np.dtype({0: 'uint32', 1: 'float16', 2: 'float32'}[pixel_type])
    names = tuple(sorted(descriptors))
    selected, alpha_name = _exr_channel_selection(names)
    read_names = list(dict.fromkeys([*selected, *([alpha_name] if alpha_name in names else [])]))
    # Compression units are native OpenEXR chunks, not Luma compression code.
    compression = int(header['compression'].v)
    chunk_rows = {0: 1, 1: 1, 2: 1, 3: 16, 4: 32, 5: 16,
                  6: 32, 7: 32, 8: 32, 9: 256, 10: 256, 11: 32}.get(compression)
    if chunk_rows is None:
        raise UnsupportedImageError('Unsupported EXR compression method.')
    sample_bytes = sum(dtype.itemsize for dtype in dtypes.values())
    native_chunk_bytes = width * min(height, chunk_rows) * sample_bytes
    tile_cache_bytes = 0
    if storage == 'tiledimage':
        tiles = header['tiles']
        if not (0 < tiles.xSize <= 4096 and 0 < tiles.ySize <= 4096):
            raise ImageTooLargeError('EXR tile dimensions exceed the safety limit.')
        chunk_rows = int(tiles.ySize)
        native_chunk_bytes = min(width, tiles.xSize) * min(height, tiles.ySize) * sample_bytes
        # Official InputFile's tiled adapter caches one row of selected tiles.
        tile_cache_bytes = width * tiles.ySize * sum(dtypes[n].itemsize for n in read_names)
    # Conservative codec scratch/packed/unpacked allowance per worker. The
    # Explorer bridge's 768 MiB Windows job limit remains the hard backstop.
    native_estimate = (native_chunk_bytes * EXR_THREADS * 4 + tile_cache_bytes
                       + MAX_EXR_CHUNK_TABLE_BYTES)
    if native_estimate > MAX_EXR_NATIVE_BYTES:
        raise ImageTooLargeError('EXR compression workspace exceeds the bounded decoder safety limit.')
    if not preview and width * height * 16 + MAX_EXR_STRIP_BYTES + native_estimate > MAX_DECODE_BYTES:
        raise ImageTooLargeError('EXR decoded pixels and compression workspace exceed the memory safety limit.')
    return (width, height, minimum, maximum, names, selected, alpha_name,
            read_names, dtypes, chunk_rows, storage, native_estimate)


def _exr_metadata(header: dict, layout: tuple, version: str) -> dict[str, str]:
    (width, height, minimum, maximum, names, selected, alpha_name,
     read_names, dtypes, chunk_rows, storage, native_estimate) = layout
    types = ', '.join(f'{name}: {dtypes[name]}' for name in names)
    metadata = {
        'Format': 'OpenEXR', 'Decoder': f'OpenEXR {version} (selected-channel strips)',
        'Source depth': _limited_text(types, 1000), 'Decoded depth': '32-bit floating-point RGBA',
        'Color profile': 'Assumed linear sRGB; EXR chromaticities are not transformed',
        'Alpha mode': ('premultiplied (OpenEXR convention)' if selected != [alpha_name] else 'straight') if alpha_name in names else 'opaque',
        'Channels': ', '.join(names), 'Displayed channels': ', '.join(selected),
        'Storage': storage, 'Compression': str(header.get('compression')),
        'Data window': f'{minimum}–{maximum}',
        'Limitations': 'Single flat part; only one RGB/Y/scalar layer displayed; no chromaticity conversion or HDR monitor output',
    }
    for key in ('chromaticities', 'owner', 'comments', 'capDate', 'pixelAspectRatio', 'displayWindow'):
        if key in header:
            metadata[f'EXR {key}'] = _limited_text(header[key])
    return metadata


def _exr_read_ranges(sample_y: np.ndarray, strip_rows: int, chunk_rows: int):
    """Batch adjacent compression chunks, skipping unused distant chunks."""
    first = 0
    while first < len(sample_y):
        start = int(sample_y[first])
        stop = first + 1
        while stop < len(sample_y):
            y = int(sample_y[stop])
            if y - start >= strip_rows or y // chunk_rows > int(sample_y[stop - 1]) // chunk_rows + 1:
                break
            stop += 1
        yield first, stop, start, int(sample_y[stop - 1])
        first = stop


def _decode_exr(path: Path, *, cancelled: Callable[[], bool] | None = None,
                preview_edge: int | None = None) -> ImageData:
    """Read selected channels through official, allocation-bounded strip I/O.

    OpenEXR.File eagerly allocates every channel. The pinned 3.5.2 release's
    InputFile adapter exposes the official scanline reader, including tiled
    input, without those whole-source channel allocations. HALF/FLOAT/UINT
    are read in their native sample types and converted exactly as before.
    """
    path = Path(path)
    _check_cancelled(cancelled)
    original_stamp = _exr_file_stamp(path)
    file_limit = min(MAX_FILE_BYTES, MAX_EXR_PREVIEW_FILE_BYTES) if preview_edge is not None else MAX_FILE_BYTES
    if original_stamp[2] > file_limit:
        raise ImageTooLargeError('EXR file exceeds the compressed input safety limit.')
    header_bytes = _bounded_exr_header(path)
    _check_exr_chunk_table(header_bytes, preview=preview_edge is not None)
    OpenEXR = _configure_exr()
    source = OpenEXR.InputFile(str(path))
    try:
        header = source.header()
        layout = _exr_layout(header, preview=preview_edge is not None)
        if _exr_file_stamp(path) != original_stamp or _bounded_exr_header(path) != header_bytes:
            raise DecodeError('EXR file changed while it was being read.')
        (width, height, minimum, maximum, names, selected, alpha_name,
         read_names, dtypes, chunk_rows, storage, native_estimate) = layout
        if preview_edge is None:
            out_width, out_height = width, height
        else:
            scale = min(1.0, preview_edge / max(width, height))
            out_width, out_height = max(1, round(width * scale)), max(1, round(height * scale))
        pixels = np.empty((out_height, out_width, 4), dtype=np.float32)
        pixels[..., 3] = 1.0
        sample_x = np.minimum(width - 1, ((np.arange(out_width) + .5) * width / out_width).astype(np.int64))
        sample_y = np.minimum(height - 1, ((np.arange(out_height) + .5) * height / out_height).astype(np.int64))
        bytes_per_row = width * sum(dtypes[name].itemsize for name in read_names)
        strip_rows = max(1, min(512, MAX_EXR_STRIP_BYTES // bytes_per_row))
        for first, stop, y0, y1 in _exr_read_ranges(sample_y, strip_rows, chunk_rows):
            _check_cancelled(cancelled)
            # Keyword bounds are absolute dataWindow coordinates; a negative
            # or nonzero origin never shifts the returned NumPy pixel array.
            buffers = source.channels(read_names, scanLine1=minimum[1] + y0,
                                      scanLine2=minimum[1] + y1)
            if len(buffers) != len(read_names):
                raise DecodeError('OpenEXR returned an incomplete channel set.')
            for name, buffer in zip(read_names, buffers):
                expected = (y1 - y0 + 1) * width * dtypes[name].itemsize
                if len(buffer) != expected:
                    raise DecodeError('OpenEXR returned an invalid strip size.')
                array = np.frombuffer(buffer, dtype=dtypes[name]).reshape(y1 - y0 + 1, width)
                if preview_edge is not None:
                    array = array[np.ix_(sample_y[first:stop] - y0, sample_x)]
                if name in selected:
                    if len(selected) == 1:
                        pixels[first:stop, :, :3] = array[..., None]
                    else:
                        pixels[first:stop, :, selected.index(name)] = array
                if name == alpha_name:
                    pixels[first:stop, :, 3] = array
            # Do not retain the prior buffers during the next native read.
            del array, buffer, buffers
        _check_cancelled(cancelled)
        if _exr_file_stamp(path) != original_stamp:
            raise DecodeError('EXR file changed while it was being read.')
        metadata = _exr_metadata(header, layout, OpenEXR.__version__)
        if original_stamp[2] > MAX_EXR_PREVIEW_FILE_BYTES:
            _add_limitation(metadata, 'Explorer thumbnails are unavailable above the 256 MiB compressed-file limit')
        if preview_edge is not None:
            metadata['Source dimensions'] = f'{width:,} × {height:,}'
            metadata['Thumbnail'] = 'Bounded center-sampled selected-channel strips; no embedded preview'
        return ImageData(path, pixels, True, metadata, _pixel_limit=MAX_EXR_PIXELS,
                         photo_metadata=extract_exr_metadata(header))
    finally:
        source.close()


def decode_exr_thumbnail(path: Path, edge: int, *,
                         cancelled: Callable[[], bool] | None = None) -> ImageData:
    """Return a bounded linear EXR preview; never allocate whole-source pixels.

    The native Explorer bridge also enforces its unchanged 256 MiB input,
    768 MiB job memory and 10-second deadline. These Python checks do not
    replace those hard process limits, nor do they expand full-viewer limits.
    """
    path = Path(path).expanduser()
    try:
        _check_cancelled(cancelled)
        if isinstance(edge, bool) or not isinstance(edge, int) or not 1 <= edge <= MAX_EXR_PREVIEW_EDGE:
            raise ValueError('EXR thumbnail edge must be an integer between 1 and 1024.')
        if not path.is_file():
            raise DecodeError(f'Image file does not exist or is not a regular file: {path.name}')
        size = path.stat().st_size
        if not size:
            raise DecodeError('The image file is empty.')
        if size > min(MAX_FILE_BYTES, MAX_EXR_PREVIEW_FILE_BYTES):
            raise ImageTooLargeError('EXR file exceeds the 256 MiB thumbnail input limit.')
        return _decode_exr(path, cancelled=cancelled, preview_edge=edge)
    except (DecodeError, DecodeCancelled):
        raise
    except MemoryError as exc:
        raise ImageTooLargeError('There is not enough memory to decode this EXR preview safely.') from exc
    except (OSError, ValueError, RuntimeError, EOFError, struct.error, OverflowError) as exc:
        raise DecodeError(f'Could not read {path.name}: {_limited_text(exc, 300)}') from exc

def _decode_raw(path: Path) -> ImageData:
    try:
        import rawpy
    except ImportError as exc:
        raise UnsupportedImageError('Camera RAW support is not installed. Install the rawpy package.') from exc
    # open_file reads metadata before unpack allocates the sensor mosaic. This
    # deliberately avoids imread's eager unpack and all embedded-preview APIs.
    try:
        with rawpy.RawPy() as raw:
            raw.open_file(str(path))
            sizes = raw.sizes
            _check_size(sizes.raw_width, sizes.raw_height, 2)
            _check_size(sizes.width, sizes.height, 24)
            raw.unpack()
            rgb = raw.postprocess(
                half_size=False, use_camera_wb=True, use_auto_wb=False,
                output_color=rawpy.ColorSpace.sRGB, output_bps=16,
                gamma=(1.0, 1.0), no_auto_bright=True,
            )
            if rgb.ndim != 3 or rgb.shape[2] != 3:
                raise UnsupportedImageError('LibRaw returned an unsupported demosaiced channel layout.')
            _check_size(rgb.shape[1], rgb.shape[0], 16)
            pixels = np.empty((*rgb.shape[:2], 4), dtype=np.float32)
            np.multiply(rgb, 1.0 / 65535.0, out=pixels[..., :3], casting='unsafe')
            pixels[..., 3] = 1.0
            metadata = {
                'Format': f'{path.suffix[1:].upper()} camera RAW',
                'Decoder': f'rawpy {rawpy.__version__} / LibRaw {getattr(rawpy, "libraw_version", "unknown")}',
                'Source depth': f'Camera sensor RAW; white level {raw.white_level}',
                'Decoded depth': '16-bit demosaic normalized to float32 RGBA',
                'Color profile': 'Linear sRGB via LibRaw camera color conversion',
                'Alpha mode': 'opaque', 'White balance': 'As-shot camera white balance (LibRaw fallback if unavailable)',
                'RAW processing': 'Full-resolution demosaic; no embedded preview; automatic brightness disabled',
                'Orientation': f'Camera orientation applied by LibRaw (flip {sizes.flip})',
                'Limitations': 'Camera/model support depends on LibRaw; demosaic is 16-bit and clips out-of-range highlights; first RAW image only',
            }
            try:
                photo_metadata = extract_raw_metadata(raw)
            except rawpy.LibRawError:
                # Optional camera metadata must not discard decoded pixels.
                photo_metadata = {}
            return ImageData(path, pixels, True, metadata, photo_metadata=photo_metadata)
    except rawpy.LibRawError as exc:
        raise DecodeError(
            f'LibRaw could not decode this RAW file: {_limited_text(exc, 240)}. '
            'The file may be damaged or this camera/compression may not be supported.') from exc


def _srgb_to_linear(rgb: np.ndarray) -> np.ndarray:
    return np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)


def _linear_to_srgb(rgb: np.ndarray) -> np.ndarray:
    return np.where(rgb <= 0.0031308, rgb * 12.92, 1.055 * np.power(rgb, 1.0 / 2.4) - 0.055)


def _linear_to_srgb_inplace(rgb: np.ndarray) -> None:
    """The same float32 transfer without several full RGB strip temporaries."""
    low = rgb <= 0.0031308
    dark = rgb[low] * 12.92
    np.power(rgb, 1.0 / 2.4, out=rgb)
    rgb *= 1.055
    rgb -= 0.055
    rgb[low] = dark


def render_rgba(image: ImageData, exposure: float = 0.0, channel: str = 'RGBA',
                tone_map: str = 'Reinhard', *,
                cancelled: Callable[[], bool] | None = None) -> np.ndarray:
    """Return contiguous 8-bit straight sRGB RGBA without editing source pixels.

    Exposure is in stops (bounded to ±32). Tone mapping is applied only to
    scene-linear sources, so opening an ordinary JPEG does not darken it.
    R/G/B and A views are opaque grayscale; A bypasses exposure and all color
    transforms. OpenEXR associated color is unpremultiplied before display.
    Processing strips avoids whole-image float temporary allocations.
    """
    _check_cancelled(cancelled)
    if not math.isfinite(float(exposure)):
        raise ValueError('Exposure must be a finite number of stops.')
    channel = channel.upper()
    if channel not in ('RGBA', 'RGB', 'R', 'G', 'B', 'A'):
        raise ValueError(f'Unknown display channel: {channel}')
    tone = tone_map.casefold()
    if tone not in ('reinhard', 'aces', 'aces filmic', 'filmic', 'none', 'linear', 'clip'):
        raise ValueError(f'Unknown tone map: {tone_map}')
    factor = float(2.0 ** min(32.0, max(-32.0, float(exposure))))
    output = np.empty((image.height, image.width, 4), dtype=np.uint8)
    associated = image.metadata.get('Alpha mode', '').startswith('premultiplied')
    if not image.linear and image.pixels.dtype == np.uint8 and factor == 1.0 and not associated:
        # Identity LDR display needs neither gamma round-trips nor float copies.
        if channel in ('RGBA', 'RGB'):
            output[:] = image.pixels
            if channel == 'RGB':
                output[..., 3] = 255
        else:
            index = ('R', 'G', 'B', 'A').index(channel)
            output[..., :3] = image.pixels[..., index:index + 1]
            output[..., 3] = 255
        _check_cancelled(cancelled)
        return output
    for start in range(0, image.height, 128):
        _check_cancelled(cancelled)
        block = image.pixels[start:start + 128]
        destination = output[start:start + 128]
        # Copy only channels we actually display. Keeping separate contiguous
        # RGB/alpha workspaces avoids the former RGBA copy followed by another
        # RGB copy; in-place transforms never touch the source array.
        alpha = block[..., 3].astype(np.float32)
        if image.pixels.dtype == np.uint8:
            alpha /= 255.0
        np.nan_to_num(alpha, copy=False, nan=0.0, posinf=1.0, neginf=0.0)
        np.clip(alpha, 0.0, 1.0, out=alpha)
        if channel == 'A':
            destination[..., :3] = np.rint(alpha[..., None] * 255.0).astype(np.uint8)
            destination[..., 3] = 255
            continue
        if channel in ('R', 'G', 'B'):
            index = ('R', 'G', 'B').index(channel)
            rgb = block[..., index:index + 1].astype(np.float32)
        else:
            rgb = block[..., :3].astype(np.float32)
        if image.pixels.dtype == np.uint8:
            rgb /= 255.0
        np.nan_to_num(rgb, copy=False, nan=0.0, posinf=1e20, neginf=0.0)
        np.clip(rgb, 0, 1e20, out=rgb)
        if associated:
            np.divide(rgb, alpha[..., None], out=rgb, where=alpha[..., None] > 1e-8)
            rgb[alpha <= 1e-8] = 0
        if not image.linear:
            rgb = _srgb_to_linear(np.minimum(rgb, 1.0))
        rgb *= factor
        if image.linear:
            if tone == 'reinhard':
                np.divide(rgb, 1.0 + rgb, out=rgb)
            elif tone in ('aces', 'aces filmic', 'filmic'):
                # Narkowicz's ACES-fit curve, not a full ACES/OCIO transform.
                rgb = np.minimum(rgb, 1e6)
                rgb = (rgb * (2.51 * rgb + 0.03)) / (rgb * (2.43 * rgb + 0.59) + 0.14)
        np.clip(rgb, 0.0, 1.0, out=rgb)
        _linear_to_srgb_inplace(rgb)
        np.clip(rgb, 0.0, 1.0, out=rgb)
        rgb *= 255.0
        np.rint(rgb, out=rgb)
        destination[..., :3] = rgb
        destination[..., 3] = np.rint(alpha * 255.0).astype(np.uint8) if channel == 'RGBA' else 255
    _check_cancelled(cancelled)
    return np.ascontiguousarray(output)
