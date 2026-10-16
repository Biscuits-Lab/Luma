"""Selected-channel EXR strips retain the full-resolution viewer contract."""
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from luma import decoders as d

exr = pytest.importorskip('OpenEXR')


def write(path, channels, **header):
    with exr.File(header, channels) as source:
        source.write(str(path))
    return path


def native_pixels(path):
    with exr.File(str(path), separate_channels=True) as source:
        return {name: channel.pixels.copy() for name, channel in source.channels().items()}


@pytest.mark.parametrize('compression', ['ZIP', 'PIZ', 'DWAA'])
@pytest.mark.parametrize('dtype', ['float16', 'float32', 'uint32'])
@pytest.mark.parametrize('prefix', ['', 'beauty.'])
def test_full_decode_matches_official_native_values(tmp_path, compression, dtype, prefix):
    rng = np.random.default_rng(20261016)
    values = (rng.random((45, 37)) * 6).astype(dtype)
    channels = {prefix + key: values * factor for key, factor in [('R', 1), ('G', 2), ('B', 3)]}
    channels[prefix + 'A'] = np.full(values.shape, .5, np.float16)
    channels['unused.Z'] = np.full(values.shape, 555, np.float32)
    minimum = np.array([-17, 23], np.int32)
    path = write(tmp_path / 'exact.exr', channels,
                 compression=getattr(exr, compression + '_COMPRESSION'),
                 dataWindow=(minimum, minimum + np.array([36, 44], np.int32)))
    native = native_pixels(path)
    with patch.object(exr, 'File', side_effect=AssertionError('Eager File must not decode')):
        decoded = d.decode_image(path)
    expected = np.stack([native[prefix + key] for key in 'RGBA'], axis=-1).astype(np.float32)
    np.testing.assert_array_equal(decoded.pixels, expected)
    assert decoded.metadata['Data window'] == '(-17, 23)–(19, 67)'
    assert decoded.metadata['Displayed channels'] == ', '.join(prefix + k for k in 'RGB')
    assert 'unused.Z' in decoded.metadata['Channels']
    assert 'uint32' in decoded.metadata['Source depth'] if dtype == 'uint32' else dtype in decoded.metadata['Source depth']


@pytest.mark.parametrize('values', [np.array([[-np.inf, -1, -0., 0., np.nan, np.inf]], np.float32),
                                  np.array([[0, 16777215, 16777216, 16777217, 4294967295]], np.uint32)])
def test_nonfinite_and_uint_scalar_conversion_matches_prior_contract(tmp_path, values):
    path = write(tmp_path / 'scalar.exr', {'Z': values})
    decoded = d.decode_image(path)
    for channel in range(3):
        np.testing.assert_array_equal(decoded.pixels[..., channel], values.astype(np.float32))
    assert np.all(decoded.pixels[..., 3] == 1)


def test_only_displayed_channels_are_read_in_bounded_strips(tmp_path, monkeypatch):
    values = np.ones((300, 64), np.float32)
    path = write(tmp_path / 'layered.exr', {**{k: values for k in 'RGBA'},
                 **{f'unused.{i}': values for i in range(20)}})
    native = exr.InputFile
    calls = []
    class Spy:
        def __init__(self, filename):
            self.source = native(filename)
        def header(self): return self.source.header()
        def close(self): self.source.close()
        def channels(self, names, **kwargs):
            calls.append((names, kwargs))
            return self.source.channels(names, **kwargs)
    monkeypatch.setattr(exr, 'InputFile', Spy)
    monkeypatch.setattr(d, 'MAX_EXR_STRIP_BYTES', 64 * 4 * 4 * 17)
    decoded = d.decode_image(path)
    assert decoded.height == 300 and len(calls) > 1
    assert all(names == list('RGBA') for names, _ in calls)
    assert all(bounds['scanLine2'] - bounds['scanLine1'] < 17 for _, bounds in calls)


def test_cancellation_between_strips_closes_file(tmp_path, monkeypatch):
    path = write(tmp_path / 'cancel.exr', {'Y': np.ones((50, 20), np.float32)})
    native = exr.InputFile
    closed = []
    reads = []
    class Spy:
        def __init__(self, filename): self.source = native(filename)
        def header(self): return self.source.header()
        def close(self):
            self.source.close()
            closed.append(True)
        def channels(self, *args, **kwargs):
            reads.append(True)
            return self.source.channels(*args, **kwargs)
    monkeypatch.setattr(exr, 'InputFile', Spy)
    monkeypatch.setattr(d, 'MAX_EXR_STRIP_BYTES', 20 * 4 * 4)
    with pytest.raises(d.DecodeCancelled):
        d.decode_image(path, cancelled=lambda: bool(reads))
    assert len(reads) == 1 and closed == [True]


def test_change_during_read_is_rejected(tmp_path, monkeypatch):
    path = write(tmp_path / 'changed.exr', {'Y': np.ones((30, 20), np.float32)})
    original = d._exr_file_stamp
    count = 0
    def stamp(p):
        nonlocal count
        count += 1
        data = original(p)
        return (*data[:-1], data[-1] + (1 if count >= 3 else 0))
    monkeypatch.setattr(d, '_exr_file_stamp', stamp)
    with pytest.raises(d.DecodeError, match='changed'):
        d.decode_image(path)


def test_preview_skips_distant_unused_compression_chunks(tmp_path, monkeypatch):
    path = write(tmp_path / 'sparse.exr', {'Y': np.ones((2048, 16), np.float16)},
                 compression=exr.ZIP_COMPRESSION)
    native = exr.InputFile
    rows = []
    class Spy:
        def __init__(self, filename): self.source = native(filename)
        def header(self): return self.source.header()
        def close(self): self.source.close()
        def channels(self, *args, **kwargs):
            rows.append(kwargs['scanLine2'] - kwargs['scanLine1'] + 1)
            return self.source.channels(*args, **kwargs)
    monkeypatch.setattr(exr, 'InputFile', Spy)
    preview = d.decode_exr_thumbnail(path, 16)
    assert preview.height == 16 and sum(rows) == 16


def test_full_exr_limit_is_separate_and_image_data_override_is_capped(monkeypatch):
    d._check_size(8192, 8192, pixel_limit=d.MAX_EXR_PIXELS)
    with pytest.raises(d.ImageTooLargeError):
        d._check_size(8192, 8192)
    with pytest.raises(d.ImageTooLargeError):
        d._check_size(8193, 8192, pixel_limit=10**12)


def test_all_source_channels_count_toward_native_workspace(tmp_path, monkeypatch):
    path = write(tmp_path / 'many.exr', {str(i): np.ones((32, 64), np.float32) for i in range(64)},
                 compression=exr.PIZ_COMPRESSION)
    monkeypatch.setattr(d, 'MAX_EXR_NATIVE_BYTES', 1_000_000)
    with patch.object(d.np, 'empty', side_effect=AssertionError('No output allocation')):
        with pytest.raises(d.ImageTooLargeError, match='workspace'):
            d.decode_image(path)


def replace_header_attribute(data, name, value):
    import struct
    cursor = 8
    while data[cursor]:
        name_end = data.index(0, cursor)
        key = data[cursor:name_end]
        type_end = data.index(0, name_end + 1)
        size = struct.unpack_from('<I', data, type_end + 1)[0]
        payload = type_end + 5
        if key == name:
            assert len(value) == size
            return data[:payload] + value + data[payload + size:]
        cursor = payload + size
    raise AssertionError(name)


@pytest.mark.parametrize('mode', [0, 1, 2, 16, 17, 18])
def test_hostile_tile_offset_tables_rejected_before_native_open(tmp_path, monkeypatch, mode):
    import struct
    tile = exr.TileDescription()
    tile.xSize = tile.ySize = 1
    path = write(tmp_path / 'offsets.exr', {'Y': np.ones((2, 2), np.float16)},
                 type=exr.tiledimage, tiles=tile)
    data = replace_header_attribute(path.read_bytes(), b'dataWindow', struct.pack('<4i', 0, 0, 16383, 16383))
    data = replace_header_attribute(data, b'tiles', struct.pack('<IIB', 1, 1, mode))
    path.write_bytes(data)
    monkeypatch.setattr(exr, 'InputFile', lambda *a, **kw: pytest.fail('Native open must not allocate the offset table'))
    with pytest.raises(d.ImageTooLargeError, match='offset table'):
        d.decode_exr_thumbnail(path, 256)


def test_unselected_subsampled_channels_rejected_before_native_open(tmp_path, monkeypatch):
    import struct
    path = write(tmp_path / 'subsampling.exr', {'Y': np.ones((2, 2), np.float16)})
    data = path.read_bytes()
    marker = b'channels\0chlist\0'
    start = data.index(marker) + len(marker) + 4
    channels_end = data.index(0, start)
    data = data[:channels_end + 9] + struct.pack('<i', 2) + data[channels_end + 13:]
    path.write_bytes(data)
    monkeypatch.setattr(exr, 'InputFile', lambda *a, **kw: pytest.fail('Native open must not be reached'))
    with pytest.raises(d.UnsupportedImageError, match='Subsampled'):
        d.decode_image(path)



def test_large_compressed_file_explains_separate_thumbnail_cap(tmp_path, monkeypatch):
    path = write(tmp_path / 'large-file.exr', {'Y': np.ones((2, 2), np.float16)})
    stamp = d._exr_file_stamp(path)
    monkeypatch.setattr(d, '_exr_file_stamp', lambda p: (*stamp[:2], d.MAX_EXR_PREVIEW_FILE_BYTES + 1, stamp[3]))
    decoded = d.decode_image(path)
    assert '256 MiB compressed-file limit' in decoded.metadata['Limitations']


def test_small_file_does_not_promise_thumbnail_eligibility(tmp_path):
    path = write(tmp_path / 'small.exr', {'Y': np.ones((2, 2), np.float16)})
    decoded = d.decode_image(path)
    assert 'Explorer' not in decoded.metadata['Limitations']
