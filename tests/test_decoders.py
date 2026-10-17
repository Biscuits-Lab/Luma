"""Generated-fixture tests. RAW tests exercise API use, not real camera support."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import struct
import zlib

import numpy as np
from PIL import Image, ImageCms
import pytest

from luma import decoders as d


def rgb_array(width=8, height=3):
    values = np.arange(width * height * 3, dtype=np.uint8).reshape(height, width, 3)
    return values * 3


def hdr_file(path, pixels, orientation=None, rle=False, extra_header=b''):
    """Write RGBE test data independently; pixels are already in file order."""
    nscan, length = pixels.shape[:2]
    if orientation is None:
        orientation = f'-Y {nscan} +X {length}'
    data = bytearray(b'#?RADIANCE\nFORMAT=32-bit_rle_rgbe\n' + extra_header + b'\n')
    data += orientation.encode('ascii') + b'\n'
    for scan in pixels:
        if not rle:
            data += scan.tobytes()
        else:
            data += bytes([2, 2, length >> 8, length & 255])
            for channel in range(4):
                values = scan[:, channel]
                if np.all(values == values[0]) and length <= 127:
                    data += bytes([128 + length, values[0]])
                else:
                    for offset in range(0, length, 128):
                        block = values[offset:offset + 128]
                        data += bytes([len(block)]) + block.tobytes()
    path.write_bytes(data)
    return path


def rgbe_float(pixels):
    result = np.ldexp(pixels[..., :3].astype(np.float32) + .5,
                      pixels[..., 3:4].astype(np.int32) - 136)
    result[pixels[..., 3] == 0] = 0
    return result


def image(pixels, linear=True, metadata=None):
    return d.ImageData(Path('test.exr'), np.asarray(pixels, dtype=np.float32), linear, metadata or {})


@pytest.mark.parametrize('extension,format_name', [
    ('png', 'PNG'), ('jpg', 'JPEG'), ('jpeg', 'JPEG'), ('webp', 'WEBP'),
    ('bmp', 'BMP'), ('tif', 'TIFF'), ('tiff', 'TIFF'), ('tga', 'TGA'),
    ('dds', 'DDS'), ('gif', 'GIF'), ('ppm', 'PPM'),
])
def test_common_formats_decode(tmp_path, extension, format_name):
    path = tmp_path / f'image.{extension}'
    Image.fromarray(rgb_array()).save(path, format=format_name)
    decoded = d.decode_image(path)
    assert (decoded.width, decoded.height) == (8, 3)
    assert decoded.pixels.shape == (3, 8, 4)
    assert decoded.pixels.dtype == np.uint8
    assert decoded.nbytes == 3 * 8 * 4
    assert not decoded.linear
    assert np.all(decoded.pixels[..., 3] == 255)
    assert decoded.metadata['Decoder'].startswith('Pillow')
    assert 'Source depth' in decoded.metadata


def test_png_alpha_exact_and_original_unchanged(tmp_path):
    values = np.array([[[32, 64, 128, 0], [255, 128, 0, 128], [5, 200, 18, 255]]], dtype=np.uint8)
    path = tmp_path / 'transparent.PNG'
    Image.fromarray(values).save(path)
    original = path.read_bytes()
    decoded = d.decode_image(path)
    np.testing.assert_array_equal(decoded.pixels, values)
    np.testing.assert_array_equal(d.render_rgba(decoded), values)
    assert path.read_bytes() == original
    assert decoded.channel_names == ('R', 'G', 'B', 'A')


@pytest.mark.parametrize('orientation,size', [(2, (3, 2)), (3, (3, 2)), (4, (3, 2)),
                                           (5, (2, 3)), (6, (2, 3)), (7, (2, 3)), (8, (2, 3))])
def test_exif_orientation(tmp_path, orientation, size):
    path = tmp_path / 'oriented.png'
    data = rgb_array(3, 2)
    source = Image.fromarray(data)
    exif = Image.Exif()
    exif[274] = orientation
    source.save(path, exif=exif)
    decoded = d.decode_image(path)
    assert (decoded.width, decoded.height) == size
    if orientation == 6:
        np.testing.assert_array_equal(decoded.pixels[..., :3], np.rot90(data, k=3))
    assert decoded.metadata['Orientation'] == f'EXIF {orientation}; applied'


def test_valid_icc_converted_and_alpha_retained(tmp_path):
    path = tmp_path / 'profile.png'
    values = np.array([[[40, 70, 190, 80], [0, 255, 20, 0]]], dtype=np.uint8)
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes()
    Image.fromarray(values).save(path, icc_profile=profile)
    decoded = d.decode_image(path)
    np.testing.assert_array_equal(decoded.pixels, values)
    assert '→ sRGB' in decoded.metadata['Color profile']


def test_invalid_icc_reported_without_losing_image(tmp_path):
    path = tmp_path / 'broken-profile.png'
    Image.fromarray(rgb_array()).save(path, icc_profile=b'not a real profile')
    decoded = d.decode_image(path)
    assert 'ICC conversion failed' in decoded.metadata['Limitations']
    assert 'invalid/unsupported' in decoded.metadata['Color profile']


def test_high_precision_grayscale_tiff(tmp_path):
    path = tmp_path / 'gray16.tiff'
    values = np.array([[0, 128, 255, 32768, 65535]], dtype=np.uint16)
    Image.fromarray(values).save(path)
    decoded = d.decode_image(path)
    assert decoded.pixels.dtype == np.float32
    assert not decoded.linear
    np.testing.assert_allclose(decoded.pixels[..., 0], values / 65535.0, rtol=1e-6)
    assert d.render_rgba(decoded)[0, 3, 0] == 128


def test_float_tiff_preserves_highlights_and_negative_values(tmp_path):
    path = tmp_path / 'float.tif'
    values = np.array([[-.2, 0, .5, 4, float('nan')]], dtype=np.float32)
    Image.fromarray(values).save(path)
    decoded = d.decode_image(path)
    assert decoded.linear
    np.testing.assert_array_equal(decoded.pixels[..., 0], values)
    assert d.render_rgba(decoded).dtype == np.uint8


def test_rgb16_png_discloses_pillow_precision_limit(tmp_path):
    path = tmp_path / 'rgb16.png'
    def chunk(name, data):
        return struct.pack('>I', len(data)) + name + data + struct.pack('>I', zlib.crc32(name + data))
    path.write_bytes(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 2, 1, 16, 2, 0, 0, 0))
                     + chunk(b'IDAT', zlib.compress(b'\0' + np.array([0, 32768, 65535, 100, 200, 300], dtype='>u2').tobytes()))
                     + chunk(b'IEND', b''))
    decoded = d.decode_image(path)
    assert decoded.pixels.dtype == np.uint8
    assert '16-bit' in decoded.metadata['Source depth']
    assert 'reduces' in decoded.metadata['Limitations']


def test_animation_first_frame_is_explicit(tmp_path):
    path = tmp_path / 'animated.gif'
    Image.new('RGB', (4, 3), 'red').save(path, save_all=True,
        append_images=[Image.new('RGB', (4, 3), 'blue')], duration=100, loop=0)
    decoded = d.decode_image(path)
    assert 'First animation frame' in decoded.metadata['Limitations']
    assert tuple(decoded.pixels[0, 0, :3]) == (255, 0, 0)


def test_multipage_tiff_first_page_explicit(tmp_path):
    path = tmp_path / 'pages.tif'
    Image.new('RGB', (4, 3), 'red').save(path, save_all=True,
        append_images=[Image.new('RGB', (4, 3), 'blue')])
    decoded = d.decode_image(path)
    assert 'First TIFF page' in decoded.metadata['Limitations']
    assert tuple(decoded.pixels[0, 0, :3]) == (255, 0, 0)


@pytest.mark.parametrize('rle', [False, True])
def test_rgbe_raw_and_rle_preserve_hdr(tmp_path, rle):
    values = np.empty((3, 12, 4), dtype=np.uint8)
    values[..., 0] = np.arange(12) * 8
    values[..., 1] = 128
    values[..., 2] = 64
    values[..., 3] = np.array([128, 132, 0])[:, None]
    path = hdr_file(tmp_path / 'image.hdr', values, rle=rle, extra_header=b'EXPOSURE=2\n')
    decoded = d.decode_image(path)
    assert decoded.linear and decoded.pixels.dtype == np.float32
    np.testing.assert_allclose(decoded.pixels[..., :3], rgbe_float(values))
    assert decoded.pixels[1, 0, 1] > 1
    assert np.all(decoded.pixels[..., 3] == 1)
    assert decoded.metadata['Radiance EXPOSURE'] == '2'


@pytest.mark.parametrize('major,sign1,sign2', [
    ('Y', '-', '+'), ('Y', '+', '+'), ('Y', '-', '-'), ('Y', '+', '-'),
    ('X', '+', '-'), ('X', '-', '-'), ('X', '+', '+'), ('X', '-', '+'),
])
def test_rgbe_all_eight_axis_orientations(tmp_path, major, sign1, sign2):
    canonical = np.array([[[12, 20, 30, 128], [40, 50, 60, 129], [70, 80, 90, 130]],
                          [[22, 30, 40, 128], [50, 60, 70, 129], [80, 90, 100, 130]]], dtype=np.uint8)
    if major == 'Y':
        file_pixels = canonical if sign1 == '-' else canonical[::-1]
        file_pixels = file_pixels if sign2 == '+' else file_pixels[:, ::-1]
        orientation = f'{sign1}Y 2 {sign2}X 3'
    else:
        file_pixels = canonical.transpose(1, 0, 2)
        file_pixels = file_pixels if sign1 == '+' else file_pixels[::-1]
        file_pixels = file_pixels if sign2 == '-' else file_pixels[:, ::-1]
        orientation = f'{sign1}X 3 {sign2}Y 2'
    path = hdr_file(tmp_path / 'axes.hdr', file_pixels, orientation=orientation)
    decoded = d.decode_image(path)
    np.testing.assert_allclose(decoded.pixels[..., :3], rgbe_float(canonical))
    assert decoded.pixels.flags.c_contiguous


def test_legacy_rgbe_run_supported(tmp_path):
    path = tmp_path / 'legacy.hdr'
    path.write_bytes(b'#?RGBE\nFORMAT=32-bit_rle_rgbe\n\n-Y 1 +X 5\n'
                     + bytes([128, 64, 32, 129, 1, 1, 1, 4]))
    decoded = d.decode_image(path)
    assert decoded.width == 5
    assert np.all(decoded.pixels == decoded.pixels[0, 0])


@pytest.mark.parametrize('payload,pattern', [
    (bytes([2, 2, 0, 8, 0]), 'RLE run'),
    (bytes([2, 2, 0, 8, 137, 20]), 'RLE run'),
    (bytes([2, 2, 0, 9]), 'width'),
    (bytes([2, 2, 0, 8, 8, 1]), 'Truncated'),
    (bytes([1, 1, 1, 8]), 'legacy'),
])
def test_corrupt_rgbe_rejected(tmp_path, payload, pattern):
    path = tmp_path / 'bad.hdr'
    path.write_bytes(b'#?RADIANCE\nFORMAT=32-bit_rle_rgbe\n\n-Y 1 +X 8\n' + payload)
    with pytest.raises(d.DecodeError, match=pattern):
        d.decode_image(path)


def test_xyze_explicitly_unsupported(tmp_path):
    path = tmp_path / 'xyz.hdr'
    path.write_bytes(b'#?RADIANCE\nFORMAT=32-bit_rle_xyze\n\n-Y 1 +X 1\n')
    with pytest.raises(d.UnsupportedImageError, match='XYZE'):
        d.decode_image(path)


def test_exr_float_channels_and_alpha_preserved(tmp_path):
    exr = pytest.importorskip('OpenEXR')
    path = tmp_path / 'float.exr'
    values = np.array([[0, .5, 2], [-1, np.inf, np.nan]], dtype=np.float32)
    alpha = np.full((2, 3), .25, dtype=np.float16)
    with exr.File({'type': exr.scanlineimage}, {'R': values, 'G': values * .5, 'B': values * .25, 'A': alpha}) as source:
        source.write(str(path))
    decoded = d.decode_image(path)
    assert decoded.linear
    np.testing.assert_array_equal(decoded.pixels[..., 0], values)
    np.testing.assert_array_equal(decoded.pixels[..., 3], alpha)
    assert 'float32' in decoded.metadata['Source depth']
    assert 'float16' in decoded.metadata['Source depth']
    assert d.render_rgba(decoded).shape == (2, 3, 4)


def test_exr_named_layer_selected(tmp_path):
    exr = pytest.importorskip('OpenEXR')
    path = tmp_path / 'layer.exr'
    values = np.ones((2, 3), dtype=np.float32)
    with exr.File({}, {'beauty.R': values, 'beauty.G': values * 2, 'beauty.B': values * 3, 'Z': values * 20}) as source:
        source.write(str(path))
    decoded = d.decode_image(path)
    np.testing.assert_array_equal(decoded.pixels[0, 0], [1, 2, 3, 1])
    assert decoded.metadata['Displayed channels'] == 'beauty.R, beauty.G, beauty.B'


def test_exr_y_expanded_to_rgb(tmp_path):
    exr = pytest.importorskip('OpenEXR')
    path = tmp_path / 'luma.exr'
    values = np.array([[0, 1, 4]], dtype=np.float32)
    with exr.File({}, {'Y': values}) as source:
        source.write(str(path))
    decoded = d.decode_image(path)
    for index in range(3):
        np.testing.assert_array_equal(decoded.pixels[..., index], values)


def test_exr_multipart_rejected_before_pixel_decode(tmp_path):
    exr = pytest.importorskip('OpenEXR')
    path = tmp_path / 'multipart.exr'
    values = np.ones((2, 3), dtype=np.float32)
    with exr.File([exr.Part({'name': 'one'}, {'Y': values}), exr.Part({'name': 'two'}, {'Y': values})]) as source:
        source.write(str(path))
    with pytest.raises(d.UnsupportedImageError, match='Multipart'):
        d.decode_image(path)


def test_exr_deep_rejected_before_pixel_decode(tmp_path):
    exr = pytest.importorskip('OpenEXR')
    path = tmp_path / 'deep.exr'
    values = np.empty((1, 2), dtype=object)
    values[0, 0] = np.array([1, 2], dtype=np.float32)
    values[0, 1] = np.array([3], dtype=np.float32)
    with exr.File({'type': exr.deepscanline, 'compression': exr.ZIPS_COMPRESSION}, {'Z': values}) as source:
        source.write(str(path))
    with pytest.raises(d.UnsupportedImageError, match='Deep'):
        d.decode_image(path)


def test_exr_dimension_checked_header_only(tmp_path, monkeypatch):
    exr = pytest.importorskip('OpenEXR')
    path = tmp_path / 'large.exr'
    with exr.File({}, {'Y': np.ones((3, 8), dtype=np.float32)}) as source:
        source.write(str(path))
    monkeypatch.setattr(d, 'MAX_EXR_PIXELS', 20)
    with pytest.raises(d.ImageTooLargeError):
        d.decode_image(path)


def test_hdr_size_checked_before_allocate(tmp_path, monkeypatch):
    path = tmp_path / 'large.hdr'
    path.write_bytes(b'#?RADIANCE\nFORMAT=32-bit_rle_rgbe\n\n-Y 100000 +X 100000\n')
    with patch.object(d.np, 'empty', side_effect=AssertionError('must not allocate')):
        with pytest.raises(d.ImageTooLargeError):
            d.decode_image(path)


def test_pillow_size_checked_before_load(tmp_path, monkeypatch):
    path = tmp_path / 'large.png'
    Image.new('RGB', (20, 20)).save(path)
    monkeypatch.setattr(d, 'MAX_PIXELS', 100)
    with pytest.raises(d.ImageTooLargeError):
        d.decode_image(path)


@pytest.mark.parametrize('suffix', ['.jpg', '.png'])
def test_large_jpeg_uses_detected_format_and_preserves_full_pixels(tmp_path, monkeypatch, suffix):
    # Scale resource limits down so routine regression tests allocate tiny images.
    monkeypatch.setattr(d, 'MAX_PIXELS', 100)
    monkeypatch.setattr(d, 'MAX_JPEG_PIXELS', 500)
    monkeypatch.setattr(Image, 'MAX_IMAGE_PIXELS', 250)
    path = tmp_path / ('photo' + suffix)
    Image.new('RGB', (20, 20), (90, 120, 150)).save(path, format='JPEG', progressive=True)
    original = path.read_bytes()
    decoded = d.decode_image(path)
    assert (decoded.width, decoded.height) == (20, 20)
    assert decoded.metadata['Format'] == 'JPEG'
    assert decoded.pixels.dtype == np.uint8 and decoded.nbytes == 1600
    # The local Pillow warning filter must not leak into other readers.
    with pytest.warns(Image.DecompressionBombWarning):
        with Image.open(path) as reference:
            np.testing.assert_array_equal(decoded.pixels, np.array(reference.convert('RGBA')))
    assert path.read_bytes() == original


def test_png_with_jpeg_extension_keeps_general_pixel_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(d, 'MAX_PIXELS', 100)
    path = tmp_path / 'not-really-a-jpeg.jpg'
    Image.new('RGB', (20, 20)).save(path, format='PNG')
    with patch.object(Image.Image, 'load', side_effect=AssertionError('must check before allocation')):
        with pytest.raises(d.ImageTooLargeError):
            d.decode_image(path)


@pytest.mark.parametrize('limit,value', [('MAX_JPEG_PIXELS', 399),
                                       ('MAX_DIMENSION', 19),
                                       ('MAX_DECODE_BYTES', 1599)])
def test_jpeg_resource_limits_checked_before_load(tmp_path, monkeypatch, limit, value):
    path = tmp_path / 'over-limit.jpg'
    Image.new('RGB', (20, 20)).save(path)
    monkeypatch.setattr(d, limit, value)
    with patch.object(Image.Image, 'load', side_effect=AssertionError('must check before allocation')):
        with pytest.raises(d.ImageTooLargeError):
            d.decode_image(path)


def test_file_size_limit(tmp_path, monkeypatch):
    path = tmp_path / 'size.png'
    path.write_bytes(b'0123456789')
    monkeypatch.setattr(d, 'MAX_FILE_BYTES', 5)
    with pytest.raises(d.ImageTooLargeError, match='File is too large'):
        d.decode_image(path)


@pytest.mark.parametrize('name,data', [('empty.png', b''), ('bad.png', b'not image'), ('bad.hdr', b'x'), ('bad.exr', b'x')])
def test_corrupt_input_clear_error(tmp_path, name, data):
    path = tmp_path / name
    path.write_bytes(data)
    with pytest.raises(d.DecodeError):
        d.decode_image(path)


def test_unknown_extension_and_missing_file(tmp_path):
    with pytest.raises(d.DecodeError, match='does not exist'):
        d.decode_image(tmp_path / 'missing.png')
    path = tmp_path / 'unknown.heic'
    path.write_bytes(b'example')
    with pytest.raises(d.UnsupportedImageError, match='Unsupported image extension'):
        d.decode_image(path)


def test_raw_api_is_full_demosaic_not_embedded_preview(tmp_path, monkeypatch):
    """Mock contract coverage only: this is NOT a real NEF compatibility test."""
    rawpy = pytest.importorskip('rawpy')
    path = tmp_path / 'camera.NEF'
    path.write_bytes(b'mock RAW for API test')
    calls = []
    class FakeRaw:
        white_level = 16383
        def __enter__(self):
            return self
        def __exit__(self, *args):
            calls.append('closed')
        def open_file(self, filename):
            calls.append(('open', filename))
        @property
        def sizes(self):
            calls.append('sizes')
            return SimpleNamespace(raw_width=6, raw_height=4, width=3, height=2, flip=0)
        def unpack(self):
            calls.append('unpack')
        def postprocess(self, **kwargs):
            calls.append(('postprocess', kwargs))
            return np.full((2, 3, 3), 32768, dtype=np.uint16)
        def extract_thumb(self):
            raise AssertionError('Embedded preview must never be used')
    monkeypatch.setattr(rawpy, 'RawPy', FakeRaw)
    decoded = d.decode_image(path)
    assert calls[0] == ('open', str(path))
    assert calls[1:3] == ['sizes', 'unpack']
    params = calls[3][1]
    assert params['half_size'] is False
    assert params['output_bps'] == 16
    assert params['gamma'] == (1.0, 1.0)
    assert params['no_auto_bright'] is True
    assert params['use_camera_wb'] is True
    assert params['output_color'] == rawpy.ColorSpace.sRGB
    np.testing.assert_allclose(decoded.pixels[..., :3], 32768 / 65535.0)
    assert decoded.linear
    assert 'Full-resolution demosaic' in decoded.metadata['RAW processing']
    assert calls[-1] == 'closed'


def test_raw_corrupt_file_has_useful_error(tmp_path):
    pytest.importorskip('rawpy')
    path = tmp_path / 'broken.nef'
    path.write_bytes(b'not a raw image at all')
    with pytest.raises(d.DecodeError, match='LibRaw could not decode'):
        d.decode_image(path)


@pytest.mark.parametrize('tone', ['Reinhard', 'ACES', 'Linear', 'None', 'Clip', 'Filmic'])
def test_alpha_never_tone_mapped_or_exposed(tone):
    source = image([[[.25, 1, 4, .25], [.5, 2, 8, .5]]])
    before = source.pixels.copy()
    baseline = d.render_rgba(source, exposure=-3, tone_map=tone)
    bright = d.render_rgba(source, exposure=3, tone_map=tone)
    np.testing.assert_array_equal(baseline[..., 3], [[64, 128]])
    np.testing.assert_array_equal(bright[..., 3], [[64, 128]])
    np.testing.assert_array_equal(source.pixels, before)
    assert bright.flags.c_contiguous


def test_aces_and_reinhard_monotonic():
    values = np.array([0, .01, .1, .18, .5, 1, 2, 4, 16], dtype=np.float32)
    source = image(np.stack([values, values, values, np.ones_like(values)], axis=-1)[None])
    for tone in ('Reinhard', 'ACES', 'Linear'):
        rendered = d.render_rgba(source, tone_map=tone)
        assert np.all(np.diff(rendered[0, :, 0].astype(int)) >= 0)
        assert rendered[0, 0, 0] == 0


def test_linear_srgb_transfer_and_exposure():
    source = image([[[.18, .18, .18, 1]]])
    assert int(d.render_rgba(source, tone_map='Linear')[0, 0, 0]) == 118
    assert d.render_rgba(source, exposure=1, tone_map='Linear')[0, 0, 0] > 118


def test_ldr_tone_map_does_not_change_default_display():
    values = np.arange(256, dtype=np.uint8)
    data = np.stack([values, values, values, values], axis=-1)[None]
    source = d.ImageData(Path('gradient.png'), data, False)
    for tone in ('Reinhard', 'ACES', 'Linear'):
        np.testing.assert_array_equal(d.render_rgba(source, tone_map=tone), data)


@pytest.mark.parametrize('channel,index', [('R', 0), ('G', 1), ('B', 2)])
def test_single_channels_are_opaque_grayscale(channel, index):
    source = d.ImageData(Path('x.png'), np.array([[[40, 80, 160, 90]]], dtype=np.uint8), False)
    shown = d.render_rgba(source, channel=channel)
    assert tuple(shown[0, 0]) == tuple([int(source.pixels[0, 0, index])] * 3 + [255])


def test_alpha_view_bypasses_all_color_controls():
    source = image([[[4, 2, .25, .25], [1, 1, 1, 1]]])
    first = d.render_rgba(source, exposure=-8, channel='A', tone_map='ACES')
    second = d.render_rgba(source, exposure=8, channel='A', tone_map='Linear')
    np.testing.assert_array_equal(first, second)
    assert tuple(first[0, 0]) == (64, 64, 64, 255)


def test_rgb_view_forces_opaque():
    source = image([[[.25, .5, 1, .1]]])
    assert d.render_rgba(source, channel='RGB')[0, 0, 3] == 255


def test_associated_exr_alpha_unpremultiplied_before_display():
    source = image([[[.25, .125, 0, .25]]], metadata={'Alpha mode': 'premultiplied (OpenEXR convention)'})
    result = d.render_rgba(source, tone_map='Linear')
    assert tuple(result[0, 0]) == (255, 188, 0, 64)


def test_nan_inf_negative_and_overrange_alpha_are_safe():
    source = image([[[np.nan, np.inf, -2, np.nan], [-np.inf, 1e30, .5, 5]]])
    before = source.pixels.copy()
    for tone in ('Reinhard', 'ACES', 'Linear'):
        result = d.render_rgba(source, exposure=32, tone_map=tone)
        assert result.dtype == np.uint8 and result.flags.c_contiguous
        assert tuple(result[0, 0]) == (0, 255, 0, 0)
        assert result[0, 1, 3] == 255
    np.testing.assert_array_equal(source.pixels, before)


@pytest.mark.parametrize('kwargs', [{'exposure': np.nan}, {'exposure': np.inf}, {'channel': 'Z'}, {'tone_map': 'Imaginary'}])
def test_invalid_render_controls_fail_clearly(kwargs):
    with pytest.raises(ValueError):
        d.render_rgba(image([[[1, 1, 1, 1]]]), **kwargs)


def test_image_data_rejects_invalid_arrays():
    with pytest.raises(d.DecodeError):
        d.ImageData(Path('bad.png'), np.zeros((2, 3, 3), dtype=np.uint8), False)
    with pytest.raises(d.DecodeError):
        d.ImageData(Path('bad.png'), np.zeros((2, 3, 4), dtype=np.float64), False)


def test_supported_extensions_cover_requested_formats():
    assert {'.jpg', '.png', '.webp', '.bmp', '.tiff', '.tga', '.dds', '.hdr', '.exr', '.nef'} <= d.SUPPORTED_EXTENSIONS


@pytest.mark.parametrize('extension', ['png', 'hdr', 'exr'])
def test_unicode_directory_and_filename(tmp_path, extension):
    directory = tmp_path / '摄影图像'
    directory.mkdir()
    path = directory / f'黄昏_旅行_é.{extension}'
    if extension == 'png':
        Image.new('RGB', (4, 3), (30, 80, 120)).save(path)
    elif extension == 'hdr':
        values = np.tile(np.array([128, 64, 32, 129], dtype=np.uint8), (3, 4, 1))
        hdr_file(path, values)
    else:
        exr = pytest.importorskip('OpenEXR')
        with exr.File({}, {'Y': np.ones((3, 4), dtype=np.float32)}) as source:
            source.write(str(path))
    decoded = d.decode_image(path)
    assert decoded.path == path
    assert (decoded.width, decoded.height) == (4, 3)
    assert decoded.metadata['File'] == path.name


def test_legacy_rgbe_extended_run_count(tmp_path):
    path = tmp_path / 'legacy-extended.hdr'
    path.write_bytes(b'#?RGBE\nFORMAT=32-bit_rle_rgbe\n\n-Y 1 +X 257\n'
                     + bytes([128, 64, 32, 129, 1, 1, 1, 0, 1, 1, 1, 1]))
    decoded = d.decode_image(path)
    assert decoded.width == 257
    assert np.all(decoded.pixels == decoded.pixels[0, 0])


def test_rle_x_major_orientation(tmp_path):
    canonical = np.tile(np.array([128, 64, 32, 129], dtype=np.uint8), (9, 3, 1))
    canonical[..., 0] = np.arange(9)[:, None] * 5
    ordered = canonical.transpose(1, 0, 2)[::-1, ::-1]
    path = hdr_file(tmp_path / 'rle-columns.hdr', ordered, '-X 3 +Y 9', rle=True)
    decoded = d.decode_image(path)
    np.testing.assert_allclose(decoded.pixels[..., :3], rgbe_float(canonical))


def test_raw_size_limit_prevents_unpack(tmp_path, monkeypatch):
    rawpy = pytest.importorskip('rawpy')
    path = tmp_path / 'too-large.nef'
    path.write_bytes(b'mock RAW')
    class FakeRaw:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return None
        def open_file(self, filename):
            pass
        sizes = SimpleNamespace(raw_width=100000, raw_height=100000, width=100000, height=100000)
        def unpack(self):
            raise AssertionError('Must check RAW size before unpacking')
    monkeypatch.setattr(rawpy, 'RawPy', FakeRaw)
    with pytest.raises(d.ImageTooLargeError):
        d.decode_image(path)


def test_render_multiple_strips_matches_single_rows():
    rng = np.random.default_rng(24)
    source = image(rng.random((257, 3, 4), dtype=np.float32) * 3)
    rendered = d.render_rgba(source, exposure=1.5, tone_map='ACES')
    for row in (0, 127, 128, 129, 255, 256):
        expected = d.render_rgba(image(source.pixels[row:row + 1]), exposure=1.5, tone_map='ACES')
        np.testing.assert_array_equal(rendered[row:row + 1], expected)

@pytest.mark.parametrize("orientation,operation", [
    (2, Image.Transpose.FLIP_LEFT_RIGHT), (3, Image.Transpose.ROTATE_180),
    (4, Image.Transpose.FLIP_TOP_BOTTOM), (5, Image.Transpose.TRANSPOSE),
    (6, Image.Transpose.ROTATE_270), (7, Image.Transpose.TRANSVERSE),
    (8, Image.Transpose.ROTATE_90),
])
@pytest.mark.parametrize("pixel_mode", ["rgb", "gray8", "gray16"])
@pytest.mark.parametrize("compression", ["raw", "tiff_lzw", "tiff_adobe_deflate"])
def test_tiff_orientation_applied_once_and_source_unchanged(tmp_path, orientation, operation, pixel_mode, compression):
    sixteen_bit = pixel_mode == "gray16"
    values = (np.array([[0, 65535, 12345], [40000, 20000, 1000]], dtype=np.uint16)
              if sixteen_bit else rgb_array(3, 2))
    if pixel_mode == "gray8":
        values = np.array([[0, 255, 48], [156, 78, 4]], dtype=np.uint8)
    source = Image.fromarray(values)
    expected = np.asarray(source.transpose(operation))
    exif = Image.Exif()
    exif[274] = orientation
    path = tmp_path / "oriented.tiff"
    source.save(path, exif=exif, compression=compression)
    original = path.read_bytes()
    _assert_tiff_storage(path, original, values, orientation, compression)
    decoded = d.decode_image(path)
    assert (decoded.width, decoded.height) == (expected.shape[1], expected.shape[0])
    assert not decoded.linear
    if sixteen_bit:
        assert decoded.pixels.dtype == np.float32
        expected_rgb = np.repeat((expected / 65535.0)[..., None], 3, axis=2)
        np.testing.assert_allclose(decoded.pixels[..., :3], expected_rgb, rtol=1e-6)
        assert np.all(decoded.pixels[..., 3] == 1)
        assert "16-bit" in decoded.metadata["Source depth"]
    else:
        if pixel_mode == "gray8":
            expected = np.repeat(expected[..., None], 3, axis=2)
        np.testing.assert_array_equal(decoded.pixels[..., :3], expected)
        assert np.all(decoded.pixels[..., 3] == 255)
    assert decoded.metadata["Orientation"] == f"EXIF {orientation}; applied"
    assert path.read_bytes() == original


def _assert_tiff_storage(path, original, values, orientation, compression):
    # Read only the header: encoded dimensions/pixels must be unrotated.
    with path.open("rb") as stream, Image.open(stream) as header:
        assert header.tag_v2[256] == values.shape[1]
        assert header.tag_v2[257] == values.shape[0]
        assert header.tag_v2[274] == orientation
        if compression == "raw":
            assert original[:2] == b"II"
            encoded = b"".join(original[offset:offset + count]
                               for offset, count in zip(header.tag_v2[273], header.tag_v2[279]))
            expected = values.astype("<u2").tobytes() if values.dtype == np.uint16 else values.tobytes()
            assert encoded == expected


@pytest.mark.parametrize('major,sign1,sign2', [
    ('Y', '-', '+'), ('Y', '+', '+'), ('Y', '-', '-'), ('Y', '+', '-'),
    ('X', '+', '-'), ('X', '-', '-'), ('X', '+', '+'), ('X', '-', '+'),
])
@pytest.mark.parametrize('rle', [False, True])
@pytest.mark.parametrize('edge', [1, 3, 7, 32])
def test_rgbe_bounded_preview_orientations(tmp_path, major, sign1, sign2, rle, edge):
    height, width = 11, 13
    canonical = np.arange(height * width * 4, dtype=np.uint8).reshape(height, width, 4)
    canonical[..., 3] = 130
    if major == 'Y':
        ordered = canonical if sign1 == '-' else canonical[::-1]
        ordered = ordered if sign2 == '+' else ordered[:, ::-1]
        orientation = f'{sign1}Y {height} {sign2}X {width}'
    else:
        ordered = canonical.transpose(1, 0, 2)
        ordered = ordered if sign1 == '+' else ordered[::-1]
        ordered = ordered if sign2 == '-' else ordered[:, ::-1]
        orientation = f'{sign1}X {width} {sign2}Y {height}'
    path = hdr_file(tmp_path / 'preview.hdr', ordered, orientation, rle=rle)
    preview = d.decode_radiance_thumbnail(path, edge)
    factor = min(1, edge / max(width, height))
    w, h = max(1, int(width * factor)), max(1, int(height * factor))
    xs = ((np.arange(w) + .5) * width / w).astype(int)
    ys = ((np.arange(h) + .5) * height / h).astype(int)
    expected = rgbe_float(canonical)[ys[:, None], xs[None, :]]
    np.testing.assert_array_equal(preview.pixels[..., :3], expected)
    assert preview.pixels.dtype == np.float32 and preview.linear
    assert (preview.width, preview.height) == (w, h)
    assert preview.metadata['Source dimensions'] == '13 × 11'
    assert 'sampled thumbnail' in preview.metadata['Limitations']


@pytest.mark.parametrize('rle', [False, True])
def test_rgbe_full_exponent_precision(tmp_path, rle):
    values = np.tile(np.array([0, 128, 255, 129], dtype=np.uint8), (2, 256, 1))
    values[..., 3] = np.arange(256, dtype=np.uint8)
    path = hdr_file(tmp_path / 'full-range.hdr', values, rle=rle)
    decoded = d.decode_image(path)
    np.testing.assert_array_equal(decoded.pixels[..., :3], rgbe_float(values))
    assert np.all(decoded.pixels[:, 0, :3] == 0)
    assert decoded.pixels[0, 255, 2] > 1e38
    assert np.isfinite(decoded.pixels).all()


def test_rgbe_legacy_readahead_preserves_following_scanlines(tmp_path):
    # The first scanline consumes only three RGBE records. Speculative block
    # reading must rewind without stealing bytes from the next modern scanline.
    path = tmp_path / 'mixed-legacy.hdr'
    path.write_bytes(b'#?RGBE\nFORMAT=32-bit_rle_rgbe\n\n-Y 2 +X 8\n'
                     + bytes([128, 64, 32, 129, 20, 40, 60, 130, 1, 1, 1, 6])
                     + bytes([2, 2, 0, 8, 136, 12, 136, 24, 136, 48, 136, 131]))
    decoded = d.decode_image(path)
    expected = np.empty((2, 8, 4), dtype=np.uint8)
    expected[0] = [20, 40, 60, 130]
    expected[0, 0] = [128, 64, 32, 129]
    expected[1] = [12, 24, 48, 131]
    np.testing.assert_array_equal(decoded.pixels[..., :3], rgbe_float(expected))


@pytest.mark.parametrize('payload', [b'\x10\x20\x30\x80', b'\x10\x20\x30\x80\x01',
    b'\x10\x20\x30\x80\x01\x01\x01\x02'])
def test_rgbe_truncated_flat_or_legacy_rejected(tmp_path, payload):
    path = tmp_path / 'truncated.hdr'
    path.write_bytes(b'#?RGBE\n\n-Y 1 +X 8\n' + payload)
    with pytest.raises(d.DecodeError, match='Truncated'):
        d.decode_image(path)


def test_rgbe_preview_uses_separate_source_pixel_limit(tmp_path, monkeypatch):
    values = np.tile(np.array([128, 64, 32, 129], dtype=np.uint8), (65, 65, 1))
    path = hdr_file(tmp_path / 'large-source.hdr', values, rle=True)
    monkeypatch.setattr(d, 'MAX_PIXELS', 4096)
    with pytest.raises(d.ImageTooLargeError):
        d.decode_image(path)
    preview = d.decode_radiance_thumbnail(path, 8)
    assert preview.pixels.shape == (8, 8, 4)
    assert preview.nbytes == 1024


@pytest.mark.parametrize('resolution', ['-Y 8192 +X 32768', '-Y 1 +X 32769', '-Y 0 +X 5'])
def test_rgbe_preview_checks_source_bounds_before_allocation(tmp_path, resolution):
    path = tmp_path / 'bad-preview.hdr'
    path.write_bytes(b'#?RGBE\n\n' + resolution.encode() + b'\n')
    with pytest.raises(d.DecodeError):
        d.decode_radiance_thumbnail(path, 64)


@pytest.mark.parametrize('edge', [0, -1, 1025, 1.5, True])
def test_rgbe_preview_rejects_bad_output_bounds(tmp_path, edge):
    with pytest.raises(ValueError, match='Thumbnail edge'):
        d.decode_radiance_thumbnail(tmp_path / 'unused.hdr', edge)


def test_rgbe_preview_validates_unselected_scanlines(tmp_path):
    values = np.tile(np.array([128, 64, 32, 129], dtype=np.uint8), (9, 12, 1))
    path = hdr_file(tmp_path / 'bad-unsampled.hdr', values, rle=True)
    # edge1 selects the center row; damage a later, unselected row.
    path.write_bytes(path.read_bytes()[:-1])
    with pytest.raises(d.DecodeError, match='Truncated'):
        d.decode_radiance_thumbnail(path, 1)


@pytest.mark.parametrize('preview', [False, True])
def test_rgbe_cancellation_stops_between_scanlines(tmp_path, monkeypatch, preview):
    values = np.tile(np.array([128, 64, 32, 129], dtype=np.uint8), (100, 12, 1))
    path = hdr_file(tmp_path / 'cancel.hdr', values, rle=True)
    scans = []
    original = d._read_rgbe_scanline
    def tracked(stream, length):
        scans.append(length)
        return original(stream, length)
    monkeypatch.setattr(d, '_read_rgbe_scanline', tracked)
    cancelled = lambda: len(scans) >= 3
    with pytest.raises(d.DecodeCancelled):
        if preview:
            d.decode_radiance_thumbnail(path, 2, cancelled=cancelled)
        else:
            d.decode_image(path, cancelled=cancelled)
    assert len(scans) == 3


def test_decode_precancelled_does_not_open_file(tmp_path):
    with pytest.raises(d.DecodeCancelled):
        d.decode_image(tmp_path / 'missing.hdr', cancelled=lambda: True)


def test_render_cancellation_and_source_immutability():
    source = image(np.ones((384, 32, 4), dtype=np.float32))
    before = source.pixels.copy()
    checks = []
    def cancelled():
        checks.append(True)
        return len(checks) >= 3
    with pytest.raises(d.DecodeCancelled):
        d.render_rgba(source, cancelled=cancelled)
    assert len(checks) == 3
    np.testing.assert_array_equal(source.pixels, before)


def test_ldr_fast_path_returns_independent_exact_array():
    rng = np.random.default_rng(3)
    pixels = rng.integers(0, 256, (32, 32, 4), dtype=np.uint8)
    source = d.ImageData(Path('ldr.png'), pixels, False)
    rendered = d.render_rgba(source)
    np.testing.assert_array_equal(rendered, pixels)
    assert not np.shares_memory(source.pixels, rendered)
    rendered[:] = 0
    np.testing.assert_array_equal(source.pixels, pixels)


def test_inplace_srgb_transfer_exactly_matches_reference():
    rng = np.random.default_rng(21)
    values = rng.random((257, 31, 3), dtype=np.float32)
    values[0, :4] = np.array([0, .00313079, .0031308, .00313081], dtype=np.float32)[:, None]
    expected = d._linear_to_srgb(values)
    d._linear_to_srgb_inplace(values)
    np.testing.assert_array_equal(values, expected)


@pytest.mark.parametrize('linear', [False, True])
@pytest.mark.parametrize('associated', [False, True])
@pytest.mark.parametrize('exposure', [-3, 0, 2])
@pytest.mark.parametrize('channel', ['RGBA', 'RGB', 'R', 'G', 'B', 'A'])
@pytest.mark.parametrize('tone', ['Reinhard', 'ACES', 'Linear'])
def test_render_exact_reference_formula(linear, associated, exposure, channel, tone):
    rng = np.random.default_rng(64)
    pixels = rng.normal(.2, 2, (7, 13, 4)).astype(np.float32)
    pixels[0, :4] = [np.nan, np.inf, -np.inf, 1e30]
    metadata = {'Alpha mode': 'premultiplied' if associated else 'straight'}
    source = image(pixels, linear, metadata)
    alpha = np.clip(np.nan_to_num(pixels[..., 3], nan=0, posinf=1, neginf=0), 0, 1)
    expected = np.empty(pixels.shape, dtype=np.uint8)
    if channel == 'A':
        expected[..., :3] = np.rint(alpha[..., None] * 255).astype(np.uint8)
        expected[..., 3] = 255
    else:
        # Release0.1.1 equations, kept out-of-place as an independent oracle for
        # the optimized renderer's in-place/channel-selective workspaces.
        rgb = np.clip(np.nan_to_num(pixels[..., :3], nan=0, posinf=1e20, neginf=0), 0, 1e20)
        if associated:
            np.divide(rgb, alpha[..., None], out=rgb, where=alpha[..., None] > 1e-8)
            rgb[alpha <= 1e-8] = 0
        if not linear:
            rgb = d._srgb_to_linear(np.minimum(rgb, 1))
        rgb *= 2.0 ** exposure
        if linear and tone == 'Reinhard':
            rgb = rgb / (1 + rgb)
        elif linear and tone == 'ACES':
            rgb = np.minimum(rgb, 1e6)
            rgb = (rgb * (2.51 * rgb + .03)) / (rgb * (2.43 * rgb + .59) + .14)
        rgb = d._linear_to_srgb(np.clip(rgb, 0, 1))
        if channel in ('R', 'G', 'B'):
            index = ('R', 'G', 'B').index(channel)
            rgb = rgb[..., index:index + 1]
        expected[..., :3] = np.rint(np.clip(rgb, 0, 1) * 255).astype(np.uint8)
        expected[..., 3] = np.rint(alpha * 255).astype(np.uint8) if channel == 'RGBA' else 255
    np.testing.assert_array_equal(d.render_rgba(source, exposure, channel, tone), expected)
    np.testing.assert_array_equal(source.pixels, pixels)


@pytest.mark.parametrize('length', [8, 127, 128, 129, 257, 1024])
def test_rgbe_variable_rle_packets_and_stream_position(length):
    from io import BytesIO
    rng = np.random.default_rng(length)
    expected = np.empty((length, 4), dtype=np.uint8)
    encoded = bytearray((2, 2, length >> 8, length & 255))
    for component in range(4):
        cursor = 0
        while cursor < length:
            repeated = bool(rng.integers(0, 2))
            count = int(rng.integers(1, min(length - cursor, 127 if repeated else 128) + 1))
            if repeated:
                value = int(rng.integers(0, 256))
                encoded.extend((128 + count, value))
                expected[cursor:cursor + count, component] = value
            else:
                values = rng.integers(0, 256, count, dtype=np.uint8)
                encoded.append(count)
                encoded.extend(values.tobytes())
                expected[cursor:cursor + count, component] = values
            cursor += count
    stream = BytesIO(encoded + b'tail')
    np.testing.assert_array_equal(d._read_rgbe_scanline(stream, length), expected)
    assert stream.read() == b'tail'
