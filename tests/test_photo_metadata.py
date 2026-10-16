"""Synthetic metadata only; no source photographs or private locations."""
from __future__ import annotations

from datetime import datetime
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image
from PIL.TiffImagePlugin import IFDRational
import pytest

from luma import photo_metadata as pm
from luma.decoders import decode_image, ImageData


def synthetic_exif(*, orientation=1):
    exif = Image.Exif()
    exif[271] = 'Synthetic Camera Co.'
    exif[272] = 'Fixture One'
    exif[274] = orientation
    exif[306] = '2026:10:12 08:09:10'  # modification time is not capture time
    exif[34665] = {
        33434: IFDRational(1, 125), 33437: IFDRational(28, 10),
        34855: 400, 37386: IFDRational(50, 1),
        42036: 'Synthetic 50 mm lens', 36867: '2026:01:02 03:04:05',
        36881: '+02:00', 37521: '125',
    }
    exif[34853] = {
        1: 'N', 2: (IFDRational(0), IFDRational(30), IFDRational(0)),
        3: 'W', 4: (IFDRational(1), IFDRational(30), IFDRational(0)),
    }
    return exif


EXPECTED = {
    'camera_make': 'Synthetic Camera Co.', 'camera_model': 'Fixture One',
    'lens_model': 'Synthetic 50 mm lens', 'exposure_time': '1/125 s',
    'aperture': 'f/2.8', 'iso': '400', 'focal_length': '50 mm',
    'captured_at': '2026-01-02 03:04:05.125+02:00',
    'gps_latitude': '0.500000', 'gps_longitude': '-1.500000',
}


def test_nested_exif_and_gps_from_loaded_pillow_object():
    loaded = Image.Exif()
    loaded.load(synthetic_exif().tobytes())
    assert pm.extract_exif_metadata(loaded) == EXPECTED


@pytest.mark.parametrize('extension', ['png', 'jpg', 'tiff', 'webp'])
def test_decoders_preserve_optional_metadata_and_source_bytes(tmp_path, extension):
    path = tmp_path / f'generated.{extension}'
    Image.new('RGB', (8, 4), 'orange').save(path, exif=synthetic_exif().tobytes())
    before = path.read_bytes()
    result = decode_image(path)
    assert result.photo_metadata == EXPECTED
    assert path.read_bytes() == before
    assert result.metadata['Camera make'] == 'Synthetic Camera Co.'


def test_metadata_absent_means_empty_mapping(tmp_path):
    path = tmp_path / 'empty.png'
    Image.new('RGB', (4, 2), 'red').save(path)
    assert decode_image(path).photo_metadata == {}
    assert pm.extract_exif_metadata({}) == {}
    assert pm.extract_raw_metadata(SimpleNamespace()) == {}
    assert pm.extract_exr_metadata({}) == {}
    data = ImageData(path, np.zeros((2, 2, 4), np.uint8), False)
    other = ImageData(path, np.zeros((2, 2, 4), np.uint8), False)
    data.photo_metadata['camera_make'] = 'Fixture'
    assert other.photo_metadata == {}


def test_optional_nested_metadata_does_not_change_orientation(tmp_path):
    path = tmp_path / 'rotated.png'
    array = np.arange(18, dtype=np.uint8).reshape(2, 3, 3)
    exif = synthetic_exif(orientation=6)
    Image.fromarray(array).save(path, exif=exif)
    result = decode_image(path)
    assert (result.width, result.height) == (2, 3)
    np.testing.assert_array_equal(result.pixels[..., :3], np.rot90(array, k=3))
    assert result.metadata['Orientation'] == 'EXIF 6; applied'
    assert result.photo_metadata == EXPECTED


@pytest.mark.parametrize('reference,expected', [('N', '0.500000'), (b'S\0', '-0.500000')])
def test_gps_hemispheres_and_explicit_rational_pairs(reference, expected):
    exif = {34853: {1: reference, 2: ((0, 1), (30, 1), (0, 1)),
                    3: b'E\0', 4: ((1, 1), (0, 1), (0, 1))}}
    result = pm.extract_exif_metadata(exif)
    assert result == {'gps_latitude': expected, 'gps_longitude': '1.000000'}


@pytest.mark.parametrize('latitude,reference,longitude,lon_reference', [
    ((0, 0, 0), None, (1, 0, 0), 'E'),
    ((0, 0), 'N', (1, 0, 0), 'E'),
    (((0, 0), 0, 0), 'N', (1, 0, 0), 'E'),
    ((float('nan'), 0, 0), 'N', (1, 0, 0), 'E'),
    ((float('inf'), 0, 0), 'N', (1, 0, 0), 'E'),
    ((91, 0, 0), 'N', (1, 0, 0), 'E'),
    ((90, 0, 1), 'N', (1, 0, 0), 'E'),
    ((0, 60, 0), 'N', (1, 0, 0), 'E'),
    ((0, 0, 60), 'N', (1, 0, 0), 'E'),
    ((-1, 0, 0), 'N', (1, 0, 0), 'E'),
    ((0, 0, 0), 'E', (1, 0, 0), 'E'),
    ((0, 0, 0), 'N', (181, 0, 0), 'E'),
    ((0, 0, 0), 'N', (1, 0, 0), 'N'),
    ((0, 0, 0), 'N', None, 'E'),
])
def test_invalid_or_incomplete_gps_is_omitted(latitude, reference, longitude, lon_reference):
    result = pm.extract_exif_metadata({271: 'Fixture', 34853: {
        1: reference, 2: latitude, 3: lon_reference, 4: longitude}})
    assert result == {'camera_make': 'Fixture'}


def test_valid_zero_and_coordinate_boundaries_are_kept():
    for lat, lon in ((0, 0), (90, 180)):
        result = pm.extract_exif_metadata({34853: {1: 'S', 2: (lat, 0, 0),
                                                  3: 'W', 4: (lon, 0, 0)}})
        assert result['gps_latitude'] == f'{-lat if lat else 0:.6f}'
        assert result['gps_longitude'] == f'{-lon if lon else 0:.6f}'


def test_malformed_values_are_omitted_without_losing_valid_camera():
    exif = {271: b'Fixture\0', 272: ['not', 'text'], 34665: {
        33434: IFDRational(1, 0), 33437: float('nan'), 34855: -100,
        37386: 0, 42036: object(), 36867: '2026:99:99 00:00:00'}}
    assert pm.extract_exif_metadata(exif) == {'camera_make': 'Fixture'}
    assert pm.extract_exif_metadata({34665: {33434: 1e-320}}) == {}


def test_capture_time_requires_recorded_capture_tag():
    assert pm.extract_exif_metadata({306: '2026:01:02 03:04:05'}) == {}
    result = pm.extract_exif_metadata({34665: {36867: 'bad', 36868: '2026:01:02 03:04:05'}})
    assert result == {'captured_at': '2026-01-02 03:04:05'}
    # Invalid optional timezone does not destroy valid explicitly embedded time.
    result = pm.extract_exif_metadata({36867: '2026:01:02 03:04:05', 36881: '+99:00'})
    assert result == {'captured_at': '2026-01-02 03:04:05'}


def test_apex_fallback_and_modern_iso():
    result = pm.extract_exif_metadata({34665: {37377: 7, 37378: 4, 34855: 65535, 34867: 102400}})
    assert result == {'exposure_time': '1/128 s', 'aperture': 'f/4', 'iso': '102400'}


def test_text_values_are_bounded_and_control_characters_removed():
    result = pm.extract_exif_metadata({271: 'F\x00oo\nCamera\tCo.', 272: 'x' * 10_000,
                                       42036: 'unknown'})
    assert result == {'camera_make': 'F oo Camera Co.', 'camera_model': 'x' * pm.MAX_TEXT}


def test_nested_ifd_has_read_budget_and_restores_original_stream():
    class CountingStream(BytesIO):
        requests = []
        def read(self, size=-1):
            self.requests.append(size)
            return super().read(size)
    class Exif(dict):
        def get_ifd(self, tag):
            self.fp.seek(0)
            self.fp.read(pm.MAX_IFD_BYTES + 1)
            raise AssertionError('Oversized read must be stopped first')
    stream = CountingStream(b'fixture')
    stream.seek(3)
    exif = Exif({271: 'Fixture'})
    exif.fp = stream
    assert pm.extract_exif_metadata(exif) == {'camera_make': 'Fixture'}
    assert stream.requests == []
    assert exif.fp is stream and stream.tell() == 3
    assert not stream.closed


def test_nested_ifd_caps_many_empty_reads():
    stream = pm._BoundedExifStream(BytesIO())
    for _ in range(pm.MAX_IFD_READS):
        assert stream.read(0) == b''
    with pytest.raises(OSError, match='read budget'):
        stream.read(0)


def test_oversized_embedded_exif_is_skipped_before_nested_parse():
    class Exif(dict):
        _loaded_exif = b'x' * (pm.MAX_EXIF_BYTES + 1)
        def get_ifd(self, tag):
            raise AssertionError('Oversized EXIF must not be parsed again')
    assert pm.extract_exif_metadata(Exif({271: 'Fixture'})) == {}


def test_invalid_nested_offset_keeps_valid_top_level_values():
    exif = Image.Exif()
    exif[271] = 'Fixture'
    exif[34665] = 2 ** 48
    exif[34853] = 'invalid offset'
    exif.fp = BytesIO(b'test')
    exif.head = b'II*\0\x08\0\0\0'
    assert pm.extract_exif_metadata(exif) == {'camera_make': 'Fixture'}


def test_raw_metadata_reuses_exposed_values_only():
    raw = SimpleNamespace(
        lens=SimpleNamespace(model='Synthetic Zoom', make='Lens manufacturer'),
        other=SimpleNamespace(shutter_speed=1 / 60, aperture=4., iso_speed=12800.,
                              focal_length=35., timestamp=datetime(2026, 1, 2, 3, 4, 5)))
    assert pm.extract_raw_metadata(raw) == {
        'lens_model': 'Synthetic Zoom', 'exposure_time': '1/60 s',
        'aperture': 'f/4', 'iso': '12800', 'focal_length': '35 mm',
        'captured_at': '2026-01-02 03:04:05'}
    # A lens manufacturer is not evidence of camera body make/model.
    assert not any(key.startswith('camera_') for key in pm.extract_raw_metadata(raw))
    raw.other.timestamp = datetime.fromtimestamp(0)
    assert 'captured_at' not in pm.extract_raw_metadata(raw)


def test_raw_optional_metadata_error_does_not_propagate():
    class Raw:
        @property
        def other(self):
            raise ValueError('broken timestamp')
    assert pm.extract_raw_metadata(Raw()) == {}


def test_exr_metadata_uses_only_embedded_header_attributes():
    header = {'cameraMake': b'Synthetic', 'cameraModel': b'EXR Fixture',
              'lensModel': b'Fixture Lens', 'expTime': .5, 'aperture': 8.,
              'isoSpeed': 200., 'focalLength': 24., 'capDate': b'2026:01:02 03:04:05',
              'latitude': -.5, 'longitude': 1.5, 'owner': b'Not camera metadata'}
    assert pm.extract_exr_metadata(header) == {
        'camera_make': 'Synthetic', 'camera_model': 'EXR Fixture',
        'lens_model': 'Fixture Lens', 'exposure_time': '1/2 s', 'aperture': 'f/8',
        'iso': '200', 'focal_length': '24 mm', 'captured_at': '2026-01-02 03:04:05',
        'gps_latitude': '-0.500000', 'gps_longitude': '1.500000'}
    assert pm.extract_exr_metadata({'latitude': 1.5}) == {}
    assert pm.extract_exr_metadata({'latitude': float('nan'), 'longitude': 0}) == {}


def test_exr_decoder_preserves_optional_header_metadata(tmp_path):
    exr = pytest.importorskip('OpenEXR')
    path = tmp_path / 'synthetic.exr'
    pixels = np.ones((2, 3), dtype=np.float32)
    exr.File({'capDate': '2026:01:02 03:04:05', 'expTime': .25,
              'latitude': -.5, 'longitude': 1.5}, {'R': pixels, 'G': pixels, 'B': pixels}).write(str(path))
    assert decode_image(path).photo_metadata == {
        'captured_at': '2026-01-02 03:04:05', 'exposure_time': '1/4 s',
        'gps_latitude': '-0.500000', 'gps_longitude': '1.500000'}


def test_image_service_and_render_cache_forward_same_photo_metadata(tmp_path, monkeypatch):
    from luma import workers
    path = tmp_path / 'frame.png'
    path.write_bytes(b'fixture')
    data = ImageData(path, np.ones((3, 4, 4), np.uint8), False, photo_metadata=dict(EXPECTED))
    calls = []
    def decode(*args, **kwargs):
        calls.append(args)
        return data
    monkeypatch.setattr(workers, 'decode_image', decode)
    service = workers.ImageService(budget=200, render_budget=240)
    request = (path, 0, 'RGBA', 'Reinhard')
    first = service(request, lambda: False)
    cached = service(request, lambda: False)
    rerendered = service((path, 1, 'RGBA', 'Reinhard'), lambda: False)
    assert len(calls) == 1
    assert first['photo_metadata'] == cached['photo_metadata'] == rerendered['photo_metadata'] == EXPECTED
    assert first['render_key'] == cached['render_key']
    assert first['render_key'] != rerendered['render_key']


def test_optional_parser_failure_does_not_reset_valid_orientation(tmp_path, monkeypatch):
    from luma import decoders
    path = tmp_path / 'orientation.png'
    array = np.arange(18, dtype=np.uint8).reshape(2, 3, 3)
    Image.fromarray(array).save(path, exif=synthetic_exif(orientation=6).tobytes())
    def malformed(_):
        raise ValueError('optional IFD damaged')
    monkeypatch.setattr(decoders, 'extract_exif_metadata', malformed)
    result = decode_image(path)
    np.testing.assert_array_equal(result.pixels[..., :3], np.rot90(array, k=3))
    assert result.photo_metadata == {}
    assert result.metadata['Orientation'] == 'EXIF 6; applied'


def test_malformed_embedded_rational_does_not_discard_image(tmp_path):
    path = tmp_path / 'bad-coordinate.jpg'
    exif = synthetic_exif()
    exif[34853][2] = (IFDRational(1, 0), IFDRational(0), IFDRational(0))
    Image.new('RGB', (4, 3), 'blue').save(path, exif=exif.tobytes())
    result = decode_image(path)
    assert result.photo_metadata['camera_model'] == 'Fixture One'
    assert 'gps_latitude' not in result.photo_metadata
    assert 'gps_longitude' not in result.photo_metadata
    assert result.pixels.shape == (3, 4, 4)


def test_pillow_optional_metadata_does_not_reopen_image(tmp_path, monkeypatch):
    path = tmp_path / 'single-open.jpg'
    Image.new('RGB', (4, 3), 'blue').save(path, exif=synthetic_exif().tobytes())
    original = Image.open
    calls = []
    def open_image(*args, **kwargs):
        calls.append(args)
        return original(*args, **kwargs)
    monkeypatch.setattr(Image, 'open', open_image)
    result = decode_image(path)
    assert result.photo_metadata == EXPECTED
    assert len(calls) == 1


def test_raw_decoder_extracts_metadata_after_single_unpack(tmp_path, monkeypatch):
    rawpy = pytest.importorskip('rawpy')
    path = tmp_path / 'synthetic.nef'
    path.write_bytes(b'generated API fixture')
    calls = []
    class Raw:
        white_level = 16383
        sizes = SimpleNamespace(raw_width=4, raw_height=2, width=4, height=2, flip=0)
        unpacked = False
        def __enter__(self):
            return self
        def __exit__(self, *args):
            calls.append('close')
        def open_file(self, filename):
            calls.append('open')
        def unpack(self):
            calls.append('unpack')
            self.unpacked = True
        def postprocess(self, **kwargs):
            calls.append('pixels')
            return np.ones((2, 4, 3), np.uint16)
        @property
        def lens(self):
            assert self.unpacked
            calls.append('lens')
            return SimpleNamespace(model='Synthetic Lens')
        @property
        def other(self):
            assert self.unpacked
            calls.append('other')
            return SimpleNamespace(iso_speed=100., shutter_speed=.25, aperture=4., focal_length=24.)
    monkeypatch.setattr(rawpy, 'RawPy', Raw)
    result = decode_image(path)
    assert result.photo_metadata == {'lens_model': 'Synthetic Lens', 'iso': '100',
                                     'exposure_time': '1/4 s', 'aperture': 'f/4', 'focal_length': '24 mm'}
    assert calls == ['open', 'unpack', 'pixels', 'lens', 'other', 'close']
