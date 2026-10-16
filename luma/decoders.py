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
from pathlib import Path
import re
import struct
import warnings
from typing import BinaryIO

import numpy as np
from PIL import Image, ImageCms, UnidentifiedImageError


# These ceilings are deliberately independent of compressed file size. They are
# safety bounds, not a promise that every accepted file fits available system RAM.
MAX_FILE_BYTES = 1024 * 1024 * 1024
MAX_PIXELS = 64_000_000
MAX_DIMENSION = 32768
MAX_DECODE_BYTES = 1536 * 1024 * 1024
MAX_HEADER_BYTES = 65536
MAX_ICC_BYTES = 4 * 1024 * 1024
MAX_EXR_CHANNELS = 64

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


@dataclass
class ImageData:
    path: Path
    pixels: np.ndarray
    linear: bool
    metadata: dict[str, str] = field(default_factory=dict)
    channel_names: tuple[str, ...] = ('R', 'G', 'B', 'A')

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        if (not isinstance(self.pixels, np.ndarray) or self.pixels.ndim != 3
                or self.pixels.shape[2] != 4
                or self.pixels.dtype not in (np.dtype('float32'), np.dtype('uint8'))):
            raise DecodeError('Image pixels must be an H×W×4 float32 or uint8 array.')
        _check_size(self.width, self.height, self.pixels.dtype.itemsize * 4)
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


def _check_size(width: int, height: int, bytes_per_pixel: int = 16) -> None:
    width, height = int(width), int(height)
    if width <= 0 or height <= 0:
        raise DecodeError(f'Invalid image dimensions: {width} × {height}.')
    if width > MAX_DIMENSION or height > MAX_DIMENSION or width * height > MAX_PIXELS:
        raise ImageTooLargeError(
            f'Image is too large: {width:,} × {height:,}. The safety limit is '
            f'{MAX_PIXELS:,} pixels and {MAX_DIMENSION:,} pixels per side.')
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


def decode_image(path: Path) -> ImageData:
    """Decode a local image without modifying it, with helpful bounded errors.

    RAW camera support depends on the installed LibRaw version and camera model.
    Animated/multipage images display only their first frame/page. EXR supports
    one flat part, displaying an RGB(A), Y, or scalar channel set.
    """
    path = Path(path).expanduser()
    try:
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
            result = _decode_exr(path)
        elif suffix in {'.hdr', '.rgbe', '.pic'}:
            result = _decode_radiance(path)
        elif suffix in PILLOW_EXTENSIONS:
            result = _decode_pillow(path)
        else:
            raise UnsupportedImageError(f'Unsupported image extension: {suffix or "(none)"}.')
        result.metadata.setdefault('File', path.name)
        result.metadata.setdefault('File size', f'{size:,} bytes')
        result.metadata.setdefault('Dimensions', f'{result.width:,} × {result.height:,}')
        result.metadata.setdefault('Display', '8-bit sRGB; scene-linear sources are tone-mapped for SDR')
        return result
    except DecodeError:
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
    with warnings.catch_warnings():
        warnings.simplefilter('error', Image.DecompressionBombWarning)
        try:
            source = Image.open(path)
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
                return ImageData(path, pixels, linear, metadata)

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
            return ImageData(path, np.array(rgba, dtype=np.uint8), False, metadata)


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
            cursor = 0
            while cursor < length:
                code = _read_exact(stream, 1)[0]
                count = code - 128 if code > 128 else code
                if not count or cursor + count > length:
                    raise DecodeError('Invalid or oversized Radiance HDR RLE run.')
                if code > 128:
                    scan[cursor:cursor + count, component] = _read_exact(stream, 1)[0]
                else:
                    scan[cursor:cursor + count, component] = np.frombuffer(_read_exact(stream, count), dtype=np.uint8)
                cursor += count
        return scan

    # Old RGBE run markers are (1,1,1,count). Consecutive markers add high
    # bytes to the repeat count. Bounds are checked before filling any run.
    cursor, shift = 0, 0
    pixel = first
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


def _decode_radiance(path: Path) -> ImageData:
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
        _check_size(width, height, 16)
        pixels = np.empty((height, width, 4), dtype=np.float32)
        pixels[..., 3] = 1.0
        for index in range(major_count):
            rgbe = _read_rgbe_scanline(stream, minor_count)
            exponent = rgbe[:, 3].astype(np.int32)
            # Decode the center of each quantization bin, as Radiance's
            # reference colr_color implementation does; exponent zero is black.
            rgb = np.ldexp(rgbe[:, :3].astype(np.float32) + 0.5, exponent[:, None] - 136)
            rgb[exponent == 0] = 0
            if major_axis == b'Y':
                row = index if major_sign == b'-' else height - 1 - index
                pixels[row, :, :3] = rgb if minor_sign == b'+' else rgb[::-1]
            else:
                column = index if major_sign == b'+' else width - 1 - index
                pixels[:, column, :3] = rgb if minor_sign == b'-' else rgb[::-1]
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
        return ImageData(path, pixels, True, metadata)


def _decode_exr(path: Path) -> ImageData:
    try:
        import OpenEXR
    except ImportError as exc:
        raise UnsupportedImageError('OpenEXR support is not installed. Install the OpenEXR package.') from exc
    # Recent official wheels enforce these limits inside the native parser too.
    if hasattr(OpenEXR, 'setMaxImageSize'):
        OpenEXR.setMaxImageSize(MAX_DIMENSION, MAX_DIMENSION)
        OpenEXR.setMaxTileSize(4096, 4096)
    with OpenEXR.File(str(path), separate_channels=True, header_only=True) as preview:
        if len(preview.parts) != 1:
            raise UnsupportedImageError('Multipart EXR is not supported. Export a single flat RGB(A) part.')
        header = dict(preview.header())
        storage = header.get('type')
        if storage in (OpenEXR.deepscanline, OpenEXR.deeptile):
            raise UnsupportedImageError('Deep EXR is not supported. Flatten the image to a regular RGB(A) EXR.')
        if storage not in (OpenEXR.scanlineimage, OpenEXR.tiledimage):
            raise UnsupportedImageError(f'Unsupported EXR storage layout: {storage}.')
        minimum, maximum = header['dataWindow']
        width = int(maximum[0]) - int(minimum[0]) + 1
        height = int(maximum[1]) - int(minimum[1]) + 1
        descriptors = header['channels']
        if not descriptors or len(descriptors) > MAX_EXR_CHANNELS:
            raise ImageTooLargeError(f'EXR must contain between 1 and {MAX_EXR_CHANNELS} channels.')
        _check_size(width, height, 16 + 4 * len(descriptors))
        for descriptor in descriptors:
            if descriptor.xSampling != 1 or descriptor.ySampling != 1:
                raise UnsupportedImageError('Subsampled EXR channels are not supported. Export full-resolution channels.')
    with OpenEXR.File(str(path), separate_channels=True) as source:
        if len(source.parts) != 1:
            raise DecodeError('EXR file changed while it was being read.')
        channels = source.channels()
        names = tuple(sorted(channels))
        # Favor the unlayered beauty channels, then a complete named RGB layer.
        prefixes = sorted({name.rsplit('.', 1)[0] + '.' for name in names if '.' in name})
        prefix = next((candidate for candidate in ('', *prefixes)
                       if all(candidate + color in channels for color in ('R', 'G', 'B'))), None)
        pixels = np.zeros((height, width, 4), dtype=np.float32)
        pixels[..., 3] = 1.0
        selected: list[str] = []
        if prefix is not None:
            selected = [prefix + color for color in ('R', 'G', 'B')]
            for index, name in enumerate(selected):
                array = channels[name].pixels
                if array.dtype == object or array.shape != (height, width):
                    raise UnsupportedImageError('EXR channel has unsupported deep/subsampled data.')
                pixels[..., index] = array
        else:
            name = next((name for name in ('Y', 'R', 'G', 'B', *names) if name in channels and name != 'A'), None)
            if name is None:
                name = 'A' if 'A' in channels else names[0]
            prefix = name.rsplit('.', 1)[0] + '.' if '.' in name else ''
            array = channels[name].pixels
            if array.dtype == object or array.shape != (height, width):
                raise UnsupportedImageError('EXR channel has unsupported deep/subsampled data.')
            pixels[..., :3] = array[..., None]
            selected = [name]
        alpha_name = prefix + 'A'
        if alpha_name in channels:
            alpha = channels[alpha_name].pixels
            if alpha.dtype == object or alpha.shape != (height, width):
                raise UnsupportedImageError('EXR alpha channel has unsupported data.')
            pixels[..., 3] = alpha
        types = ', '.join(f'{name}: {channels[name].pixels.dtype}' for name in names)
        metadata = {
            'Format': 'OpenEXR', 'Decoder': f'OpenEXR {OpenEXR.__version__}',
            'Source depth': _limited_text(types, 1000), 'Decoded depth': '32-bit floating-point RGBA',
            'Color profile': 'Assumed linear sRGB; EXR chromaticities are not transformed',
            'Alpha mode': ('premultiplied (OpenEXR convention)' if selected != [alpha_name] else 'straight') if alpha_name in channels else 'opaque',
            'Channels': ', '.join(names), 'Displayed channels': ', '.join(selected),
            'Storage': str(header.get('type')), 'Compression': str(header.get('compression')),
            'Data window': f'{tuple(map(int, minimum))}–{tuple(map(int, maximum))}',
            'Limitations': 'Single flat part; only one RGB/Y/scalar layer displayed; no chromaticity conversion or HDR monitor output',
        }
        for key in ('chromaticities', 'owner', 'comments', 'capDate', 'pixelAspectRatio', 'displayWindow'):
            if key in header:
                metadata[f'EXR {key}'] = _limited_text(header[key])
        return ImageData(path, pixels, True, metadata)


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
            return ImageData(path, pixels, True, metadata)
    except rawpy.LibRawError as exc:
        raise DecodeError(
            f'LibRaw could not decode this RAW file: {_limited_text(exc, 240)}. '
            'The file may be damaged or this camera/compression may not be supported.') from exc


def _srgb_to_linear(rgb: np.ndarray) -> np.ndarray:
    return np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)


def _linear_to_srgb(rgb: np.ndarray) -> np.ndarray:
    return np.where(rgb <= 0.0031308, rgb * 12.92, 1.055 * np.power(rgb, 1.0 / 2.4) - 0.055)


def render_rgba(image: ImageData, exposure: float = 0.0, channel: str = 'RGBA',
                tone_map: str = 'Reinhard') -> np.ndarray:
    """Return contiguous 8-bit straight sRGB RGBA without editing source pixels.

    Exposure is in stops (bounded to ±32). Tone mapping is applied only to
    scene-linear sources, so opening an ordinary JPEG does not darken it.
    R/G/B and A views are opaque grayscale; A bypasses exposure and all color
    transforms. OpenEXR associated color is unpremultiplied before display.
    Processing strips avoids whole-image float temporary allocations.
    """
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
        return output
    for start in range(0, image.height, 128):
        block = image.pixels[start:start + 128]
        destination = output[start:start + 128]
        samples = block.astype(np.float32)
        if image.pixels.dtype == np.uint8:
            samples /= 255.0
        alpha = np.nan_to_num(samples[..., 3], nan=0.0, posinf=1.0, neginf=0.0)
        alpha = np.clip(alpha, 0.0, 1.0)
        if channel == 'A':
            destination[..., :3] = np.rint(alpha[..., None] * 255.0).astype(np.uint8)
            destination[..., 3] = 255
            continue
        rgb = np.nan_to_num(samples[..., :3], nan=0.0, posinf=1e20, neginf=0.0)
        np.clip(rgb, 0, 1e20, out=rgb)
        if associated:
            np.divide(rgb, alpha[..., None], out=rgb, where=alpha[..., None] > 1e-8)
            rgb[alpha <= 1e-8] = 0
        if not image.linear:
            rgb = _srgb_to_linear(np.minimum(rgb, 1.0))
        rgb *= factor
        if image.linear:
            if tone == 'reinhard':
                rgb = rgb / (1.0 + rgb)
            elif tone in ('aces', 'aces filmic', 'filmic'):
                # Narkowicz's ACES-fit curve, not a full ACES/OCIO transform.
                rgb = np.minimum(rgb, 1e6)
                rgb = (rgb * (2.51 * rgb + 0.03)) / (rgb * (2.43 * rgb + 0.59) + 0.14)
        rgb = _linear_to_srgb(np.clip(rgb, 0.0, 1.0))
        if channel in ('R', 'G', 'B'):
            index = ('R', 'G', 'B').index(channel)
            rgb = rgb[..., index:index + 1]
        destination[..., :3] = np.rint(np.clip(rgb, 0.0, 1.0) * 255.0).astype(np.uint8)
        destination[..., 3] = np.rint(alpha * 255.0).astype(np.uint8) if channel == 'RGBA' else 255
    return np.ascontiguousarray(output)
