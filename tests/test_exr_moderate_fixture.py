"""Small deterministic cases for the native-smoke moderate-entropy generator."""
import hashlib

import numpy as np
import pytest

OpenEXR = pytest.importorskip('OpenEXR')

from luma import decoders as d
from scripts.generate_exr_moderate_fixture import write_moderate_exr_fixture


@pytest.mark.parametrize('width,height,strip_rows', [(17, 9, 1), (64, 33, 16), (127, 65, 32)])
def test_moderate_fixture_values_repeat_and_thumbnail(tmp_path, width, height, strip_rows):
    first = tmp_path / 'one.exr'
    second = tmp_path / 'two.exr'
    a = write_moderate_exr_fixture(first, width=width, height=height, strip_rows=strip_rows)
    b = write_moderate_exr_fixture(second, width=width, height=height, strip_rows=strip_rows)
    assert a['sha256'] == b['sha256'] == hashlib.sha256(first.read_bytes()).hexdigest()
    assert a['sample_type'] == 'HALF' and a['compression'] == 'ZIP'
    assert a['size_bytes'] == first.stat().st_size
    with OpenEXR.File(str(first), separate_channels=True) as source:
        assert source.header()['compression'] == OpenEXR.ZIP_COMPRESSION
        native = {name: channel.pixels.copy() for name, channel in source.channels().items()}
    assert set(native) == set('RGBA')
    assert all(p.dtype == np.float16 for p in native.values())

    # Independently spell out the seeded source recipe at this tiny size.
    expected = np.ones((height, width, 4), np.float32)
    rng = np.random.default_rng(20261016)
    for y0 in range(0, height, strip_rows):
        count = min(strip_rows, height - y0)
        for channel in range(3):
            noise = rng.integers(0, 2, (count, width), dtype=np.uint8)
            for row in range(count):
                y = row + y0
                for x in range(width):
                    base = (
                        .125 + ((x // 4 + y // 4) % 256) / 512,
                        .125 + ((x // 16 + y // 16) % 128) / 256,
                        .125 + ((x // 8 + y // 8) % 2) / 4 + (x // 256) / 128,
                    )[channel]
                    expected[y, x, channel] = np.float16(base + int(noise[row, x]) / 2048)
    for index, name in enumerate('RGBA'):
        np.testing.assert_array_equal(native[name], expected[:, :, index])
    np.testing.assert_array_equal(d.decode_image(first).pixels, expected)
    preview = d.decode_exr_thumbnail(first, 8)
    xs = ((np.arange(preview.width) + .5) * width / preview.width).astype(int)
    ys = ((np.arange(preview.height) + .5) * height / preview.height).astype(int)
    np.testing.assert_array_equal(preview.pixels, expected[np.ix_(ys, xs)])


@pytest.mark.parametrize('kwargs', [{'width': 8193}, {'height': 0}, {'strip_rows': 65},
                                   {'seed': -1}, {'width': True}, {'strip_rows': 1.5}])
def test_moderate_fixture_rejects_unsafe_requests_before_open(tmp_path, kwargs):
    path = tmp_path / 'not-created.exr'
    with pytest.raises(ValueError):
        write_moderate_exr_fixture(path, **kwargs)
    assert not path.exists()
