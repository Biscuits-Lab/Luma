"""Small, optional photographic metadata from already-open decoder objects.

No file is opened here, no pixels are decoded, and no location is inferred or
looked up. Only explicitly embedded values are exposed. EXIF nested IFD reads
have independent byte/read budgets; MakerNotes and thumbnails are not followed.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
import math
from numbers import Real
import re
import struct
import warnings


MAX_TEXT = 200
MAX_EXIF_BYTES = 1024 * 1024
MAX_IFD_BYTES = 256 * 1024
MAX_IFD_READS = 2048
_ERRORS = (AttributeError, KeyError, TypeError, ValueError, OSError, EOFError,
           OverflowError, ZeroDivisionError, struct.error, SyntaxError)


def _get(mapping, key, default=None):
    try:
        return mapping.get(key, default)
    except _ERRORS:
        return default


def _attribute(value, key):
    try:
        return getattr(value, key, None)
    except _ERRORS:
        return None


def _text(value, limit=MAX_TEXT):
    if isinstance(value, bytes):
        value = value[:limit * 4].decode('utf-8', errors='replace')
    if not isinstance(value, str):
        return None
    # Strip NUL padding and control characters; never stringify arbitrary
    # arrays, mappings, or other potentially enormous metadata objects.
    value = ' '.join(''.join(c if c.isprintable() else ' ' for c in value[:limit * 4]).split())
    value = value[:limit].strip()
    if not value or value.casefold() in {'unknown', 'none', 'n/a', 'null', '0', '-'}:
        return None
    return value


def _number(value):
    """Accept native numbers, Pillow rationals, and explicit rational pairs."""
    if isinstance(value, (tuple, list)):
        if len(value) == 1:
            value = value[0]
        elif len(value) == 2:
            numerator, denominator = value
            # No recursive structures, strings or bools are interpreted.
            if (isinstance(numerator, bool) or isinstance(denominator, bool)
                    or not isinstance(numerator, Real) or not isinstance(denominator, Real)):
                return None
            try:
                value = float(numerator) / float(denominator)
            except _ERRORS:
                return None
        else:
            return None
    if isinstance(value, bool) or not isinstance(value, Real):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except _ERRORS:
        return None


def _positive(value, maximum):
    number = _number(value)
    return number if number is not None and 0 < number <= maximum else None


def _decimal(value):
    # Four significant digits retain normal camera settings and discard
    # unhelpful binary float noise from EXR/LibRaw metadata.
    return str(int(value)) if value.is_integer() else f'{value:.4g}'


def _exposure(value):
    number = _positive(value, 86400)
    if number is None or number < 1e-12:
        return None
    if number < 1:
        inverse = 1 / number
        rounded = round(inverse)
        if rounded >= 2 and abs(inverse - rounded) <= max(.00001, inverse * .001):
            return f'1/{rounded} s'
    return f'{_decimal(number)} s'


def _capture_time(value, offset=None, subsecond=None):
    value = _text(value, 40)
    if value is None:
        return None
    match = re.fullmatch(r'(\d{4})[:-](\d{2})[:-](\d{2})[ T](\d{2}):(\d{2}):(\d{2})', value)
    if not match:
        return None
    try:
        stamp = datetime(*(int(piece) for piece in match.groups()))
    except ValueError:
        return None
    result = stamp.isoformat(sep=' ', timespec='seconds')
    fraction = _text(subsecond, 12)
    if fraction is not None and re.fullmatch(r'\d{1,9}', fraction):
        result += '.' + fraction
    timezone = _text(offset, 8)
    if timezone and re.fullmatch(r'[+-]\d{2}:\d{2}', timezone):
        if int(timezone[1:3]) <= 23 and int(timezone[4:]) < 60:
            result += timezone
    return result


def _coordinate(parts, hemisphere, *, latitude):
    if not isinstance(parts, (tuple, list)) or len(parts) != 3:
        return None
    values = [_number(item) for item in parts]
    if any(item is None for item in values):
        return None
    degrees, minutes, seconds = values
    limit = 90 if latitude else 180
    if not (0 <= degrees <= limit and 0 <= minutes < 60 and 0 <= seconds < 60):
        return None
    result = degrees + minutes / 60 + seconds / 3600
    if result > limit:
        return None
    reference = _text(hemisphere, 4)
    reference = reference.upper() if reference else ''
    allowed = ('N', 'S') if latitude else ('E', 'W')
    if reference not in allowed:
        return None
    if reference in ('S', 'W'):
        result = -result
    return result


def _add_coordinates(result, latitude, longitude):
    # Do not expose a misleading partial location. Zero is a valid coordinate.
    latitude, longitude = _number(latitude), _number(longitude)
    if (latitude is not None and longitude is not None
            and -90 <= latitude <= 90 and -180 <= longitude <= 180):
        result['gps_latitude'] = f'{latitude if latitude else 0:.6f}'
        result['gps_longitude'] = f'{longitude if longitude else 0:.6f}'


class _BoundedExifStream:
    """Temporary view of Pillow's own EXIF stream; never owns or closes it."""
    def __init__(self, stream):
        self.stream = stream
        self.remaining = MAX_IFD_BYTES
        self.reads = MAX_IFD_READS

    def read(self, size=-1):
        if not isinstance(size, int) or size < 0 or size > self.remaining or self.reads <= 0:
            raise OSError('Optional EXIF metadata exceeds its read budget')
        self.reads -= 1
        data = self.stream.read(size)
        self.remaining -= len(data)
        return data

    def seek(self, offset, whence=0):
        if not isinstance(offset, int) or abs(offset) > (1 << 40):
            raise OSError('Invalid optional EXIF offset')
        return self.stream.seek(offset, whence)

    def tell(self):
        return self.stream.tell()


def _ifd(exif, tag):
    direct = _get(exif, tag)
    if isinstance(direct, Mapping):
        return direct
    get_ifd = _attribute(exif, 'get_ifd')
    if not callable(get_ifd):
        return {}
    stream = _attribute(exif, 'fp')
    position = None
    try:
        if stream is not None:
            position = stream.tell()
            exif.fp = _BoundedExifStream(stream)
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)
            result = get_ifd(tag)
        return result if isinstance(result, Mapping) else {}
    except _ERRORS:
        return {}
    finally:
        if stream is not None:
            exif.fp = stream
            if position is not None:
                try:
                    stream.seek(position)
                except _ERRORS:
                    pass


def extract_exif_metadata(exif) -> dict[str, str]:
    """Read IFD0 plus EXIF/GPS IFDs from the already obtained Pillow EXIF."""
    raw = _attribute(exif, '_loaded_exif')
    if isinstance(raw, (bytes, bytearray)) and len(raw) > MAX_EXIF_BYTES:
        return {}
    details = _ifd(exif, 34665)
    def value(tag):
        candidate = _get(details, tag)
        return _get(exif, tag) if candidate is None else candidate
    result = {}
    for key, tag in (('camera_make', 271), ('camera_model', 272), ('lens_model', 42036)):
        text = _text(value(tag))
        if text:
            result[key] = text
    exposure = _exposure(value(33434))
    if exposure is None:
        apex = _number(value(37377))
        if apex is not None and -16 <= apex <= 40:
            exposure = _exposure(2.0 ** -apex)
    if exposure:
        result['exposure_time'] = exposure
    aperture = _positive(value(33437), 1024)
    if aperture is None:
        apex = _number(value(37378))
        if apex is not None and -10 <= apex <= 20:
            aperture = _positive(2.0 ** (apex / 2), 1024)
    if aperture:
        result['aperture'] = 'f/' + _decimal(aperture)
    iso = _positive(value(34855), 100_000_000)
    if iso is None or iso == 65535:
        iso = _positive(value(34867), 100_000_000)
    if iso is not None:
        result['iso'] = _decimal(iso)
    focal = _positive(value(37386), 1_000_000)
    if focal:
        result['focal_length'] = _decimal(focal) + ' mm'
    # IFD0 DateTime is modification time, so do not call it capture time.
    for tag, offset, subsecond in ((36867, 36881, 37521), (36868, 36882, 37522)):
        captured = _capture_time(value(tag), value(offset), value(subsecond))
        if captured:
            result['captured_at'] = captured
            break
    gps = _ifd(exif, 34853)
    _add_coordinates(result,
        _coordinate(_get(gps, 2), _get(gps, 1), latitude=True),
        _coordinate(_get(gps, 4), _get(gps, 3), latitude=False))
    return result


def extract_raw_metadata(raw) -> dict[str, str]:
    """Use LibRaw's already-unpacked public metadata, never reopen the RAW.

    rawpy 0.27.1 exposes lens/exposure data but not body make/model or GPS.
    Those unavailable fields stay absent rather than using filenames or lens
    manufacturer as a substitute for camera identity.
    """
    result = {}
    lens = _attribute(raw, 'lens')
    lens_model = _text(_attribute(lens, 'model'))
    if lens_model:
        result['lens_model'] = lens_model
    other = _attribute(raw, 'other')
    exposure = _exposure(_attribute(other, 'shutter_speed'))
    if exposure:
        result['exposure_time'] = exposure
    for key, attribute, maximum, prefix, suffix in (
        ('aperture', 'aperture', 1024, 'f/', ''),
        ('iso', 'iso_speed', 100_000_000, '', ''),
        ('focal_length', 'focal_length', 1_000_000, '', ' mm'),
    ):
        number = _positive(_attribute(other, attribute), maximum)
        if number:
            result[key] = prefix + _decimal(number) + suffix
    stamp = _attribute(other, 'timestamp')
    if isinstance(stamp, datetime):
        try:
            # LibRaw uses a zero timestamp for missing capture information.
            if stamp.timestamp() != 0:
                result['captured_at'] = stamp.isoformat(sep=' ', timespec='seconds')
        except _ERRORS:
            pass
    return result


def extract_exr_metadata(header) -> dict[str, str]:
    """Read explicit standard/custom photo attributes from the native header."""
    result = {}
    for key, tag in (('camera_make', 'cameraMake'), ('camera_model', 'cameraModel'),
                     ('lens_model', 'lensModel')):
        text = _text(_get(header, tag))
        if text:
            result[key] = text
    exposure = _exposure(_get(header, 'expTime'))
    if exposure:
        result['exposure_time'] = exposure
    for key, tag, maximum, prefix, suffix in (
        ('aperture', 'aperture', 1024, 'f/', ''),
        ('iso', 'isoSpeed', 100_000_000, '', ''),
        ('focal_length', 'focalLength', 1_000_000, '', ' mm'),
    ):
        number = _positive(_get(header, tag), maximum)
        if number:
            result[key] = prefix + _decimal(number) + suffix
    captured = _capture_time(_get(header, 'capDate'))
    if captured:
        result['captured_at'] = captured
    _add_coordinates(result, _get(header, 'latitude'), _get(header, 'longitude'))
    return result
