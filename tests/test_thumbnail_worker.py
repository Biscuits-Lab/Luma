"""Headless thumbnail protocol and resource-limit regressions."""
from pathlib import Path
import struct
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from PIL import Image
import pytest

from luma import thumbnail_worker as thumbnails


def test_raster_preserves_aspect_alpha_and_source(tmp_path):
    path = tmp_path / "image.png"
    Image.new("RGBA", (80, 40), (210, 20, 30, 90)).save(path)
    original = path.read_bytes()
    result = thumbnails.generate_thumbnail(path, "raster", 32)
    assert result.size == (32, 16) and result.mode == "RGBA"
    assert result.getpixel((10, 8))[3] == 90
    assert path.read_bytes() == original


@pytest.mark.parametrize("mode,edge", [("other", 32), ("raster", 0), ("raster", 1025), ("raster", 1.5)])
def test_invalid_request_rejected(tmp_path, mode, edge):
    with pytest.raises(ValueError):
        thumbnails.generate_thumbnail(tmp_path / "missing.png", mode, edge)


def test_empty_corrupt_and_oversize_inputs_rejected(tmp_path, monkeypatch):
    path = tmp_path / "image.png"
    path.write_bytes(b"")
    with pytest.raises(ValueError):
        thumbnails.generate_thumbnail(path, "raster", 32)
    path.write_bytes(b"corrupt")
    with pytest.raises(Exception):
        thumbnails.generate_thumbnail(path, "raster", 32)
    monkeypatch.setattr(thumbnails, "MAX_FILE_BYTES", 3)
    with pytest.raises(ValueError):
        thumbnails.generate_thumbnail(path, "raster", 32)


def test_exif_orientation_is_applied(tmp_path):
    path = tmp_path / "rotated.jpg"
    image = Image.new("RGB", (80, 40), "red")
    exif = Image.Exif()
    exif[274] = 6
    image.save(path, exif=exif)
    assert thumbnails.generate_thumbnail(path, "raster", 32).size == (16, 32)


@pytest.mark.parametrize("suffix", [".tga", ".dds", ".bmp", ".webp", ".tiff"])
def test_raster_modes_use_content_not_synthetic_extension(tmp_path, suffix):
    original = tmp_path / ("source" + suffix)
    Image.new("RGB", (60, 30), "orange").save(original)
    path = tmp_path / "input.png"
    path.write_bytes(original.read_bytes())
    assert thumbnails.generate_thumbnail(path, "raster", 24).size == (24, 12)


def test_hdr_is_tonemapped_and_limits_restored(tmp_path):
    from luma import decoders
    path = tmp_path / "input.hdr"
    path.write_bytes(b"#?RADIANCE\nFORMAT=32-bit_rle_rgbe\n\n-Y 1 +X 2\n" + bytes([128, 64, 32, 129]) * 2)
    old = decoders.MAX_PIXELS
    result = thumbnails.generate_thumbnail(path, "hdr", 32)
    assert result.size == (2, 1)
    assert result.getpixel((0, 0))[0] > result.getpixel((0, 0))[1] > 0
    assert decoders.MAX_PIXELS == old


def test_flat_exr_preserves_non_square_shape(tmp_path):
    import OpenEXR
    path = tmp_path / "input.exr"
    with OpenEXR.File({}, {"R": np.ones((10, 20), dtype=np.float32),
                          "G": np.full((10, 20), 0.25, dtype=np.float32),
                          "B": np.zeros((10, 20), dtype=np.float32)}) as image:
        image.write(str(path))
    assert thumbnails.generate_thumbnail(path, "exr", 8).size == (8, 4)


def test_worker_writes_only_existing_private_output(tmp_path):
    folder = tmp_path / "LumaThumb-test"
    folder.mkdir()
    source, output = folder / "input.png", folder / "thumbnail.bin"
    Image.new("RGBA", (12, 6), (9, 20, 31, 180)).save(source)
    before = source.read_bytes()
    assert thumbnails.main(["raster", str(source), str(output), "8"]) == 2
    output.write_bytes(b"old")
    assert thumbnails.main(["raster", str(source), str(output), "8"]) == 0
    payload = output.read_bytes()
    assert payload[:8] == thumbnails.MAGIC
    assert struct.unpack("<II", payload[8:16]) == (8, 4)
    assert len(payload) == 16 + 8 * 4 * 4
    assert source.read_bytes() == before
    assert thumbnails.main(["raster", str(source), str(source), "8"]) == 2


def test_worker_refuses_output_outside_request_folder(tmp_path):
    source = tmp_path / "input.png"
    output = tmp_path / "thumbnail.bin"
    Image.new("RGB", (8, 8), "red").save(source)
    output.write_bytes(b"untouched")
    assert thumbnails.main(["raster", str(source), str(output), "8"]) == 2
    assert output.read_bytes() == b"untouched"


def test_raw_prefers_embedded_preview_without_unpacking_sensor(tmp_path):
    import rawpy
    path = tmp_path / "input.nef"
    path.write_bytes(b"fixture")
    class Raw:
        sizes = SimpleNamespace(raw_width=6000, raw_height=4000, flip=0)
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def open_file(self, path): pass
        def extract_thumb(self):
            return SimpleNamespace(format=rawpy.ThumbFormat.BITMAP,
                                   data=np.full((30, 60, 3), 140, dtype=np.uint8))
        def unpack(self): raise AssertionError("Embedded preview must avoid sensor unpack")
    with patch.object(rawpy, "RawPy", Raw):
        assert thumbnails.generate_thumbnail(path, "raw", 20).size == (20, 10)


def test_importing_thumbnail_worker_does_not_load_qt():
    code = "import sys; import luma.thumbnail_worker; assert not any(k.startswith('PySide6') for k in sys.modules)"
    subprocess.run([sys.executable, "-c", code], check=True, timeout=30)

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
              if sixteen_bit else np.arange(18, dtype=np.uint8).reshape(2, 3, 3) * 13)
    if pixel_mode == "gray8":
        values = np.array([[0, 255, 48], [156, 78, 4]], dtype=np.uint8)
    source = Image.fromarray(values)
    expected_samples = np.asarray(source.transpose(operation))
    if sixteen_bit:
        gray = np.rint(expected_samples.astype(np.float64) * (255.0 / 65535.0)).astype(np.uint8)
        expected_rgb = np.repeat(gray[..., None], 3, axis=2)
    else:
        expected_rgb = (np.repeat(expected_samples[..., None], 3, axis=2)
                        if pixel_mode == "gray8" else expected_samples)
    exif = Image.Exif()
    exif[274] = orientation
    path = tmp_path / "input.png"  # Native bridge's synthetic raster name.
    source.save(path, format="TIFF", exif=exif, compression=compression)
    original = path.read_bytes()
    _assert_tiff_storage(path, original, values, orientation, compression)
    result = thumbnails.generate_thumbnail(path, "raster", 32)
    assert result.size == (expected_rgb.shape[1], expected_rgb.shape[0])
    np.testing.assert_array_equal(np.asarray(result)[..., :3], expected_rgb)
    assert np.all(np.asarray(result)[..., 3] == 255)
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


@pytest.mark.parametrize("size", [(4096, 2048), (8192, 4096), (16384, 8192)])
def test_large_hdr_uses_bounded_preview_instead_of_full_decode(tmp_path, monkeypatch, size):
    import hashlib
    from luma import decoders
    from scripts.smoke_bundle import write_hdr_thumbnail_fixture
    path = tmp_path / "input.hdr"
    write_hdr_thumbnail_fixture(path, *size)
    original = hashlib.sha256(path.read_bytes()).digest()
    monkeypatch.setattr(decoders, "decode_image", lambda *a, **k: pytest.fail("Full float decode must not run"))
    original_render = decoders.render_rgba
    seen = []

    def bounded_render(image, *args, **kwargs):
        seen.append(image.pixels.nbytes)
        assert image.width <= 256 and image.height <= 256
        return original_render(image, *args, **kwargs)

    monkeypatch.setattr(decoders, "render_rgba", bounded_render)
    original_limits = (decoders.MAX_PIXELS, decoders.MAX_DECODE_BYTES)
    result = thumbnails.generate_thumbnail(path, "hdr", 256)
    assert result.size == (256, 128)
    assert seen == [256 * 128 * 16]
    assert result.getpixel((32, 64)) != result.getpixel((224, 64))
    assert hashlib.sha256(path.read_bytes()).digest() == original
    assert (decoders.MAX_PIXELS, decoders.MAX_DECODE_BYTES) == original_limits


@pytest.mark.parametrize("dimensions", [(32769, 1), (20000, 20000)])
def test_hdr_preview_still_rejects_excessive_source_dimensions(tmp_path, dimensions):
    path = tmp_path / "input.hdr"
    width, height = dimensions
    path.write_bytes(f"#?RADIANCE\nFORMAT=32-bit_rle_rgbe\n\n-Y {height} +X {width}\n".encode())
    with pytest.raises(ValueError, match="safety limit"):
        thumbnails.generate_thumbnail(path, "hdr", 32)


def test_generic_executable_provider_sniffs_hdr_and_exr_under_synthetic_png_name(tmp_path):
    import OpenEXR
    from scripts.smoke_bundle import write_hdr_thumbnail_fixture
    path = tmp_path / "input.png"
    write_hdr_thumbnail_fixture(path, 128, 64)
    assert thumbnails.generate_thumbnail(path, "raster", 32).size == (32, 16)
    OpenEXR.File({}, {"RGB": np.ones((12, 24, 3), dtype=np.float32)}).write(str(path))
    assert thumbnails.generate_thumbnail(path, "raster", 8).size == (8, 4)


@pytest.mark.parametrize("format", ["PNG", "JPEG"])
def test_generic_executable_provider_retains_common_images(tmp_path, format):
    path = tmp_path / "input.png"
    Image.new("RGB", (30, 20), "orange").save(path, format=format)
    original = path.read_bytes()
    assert thumbnails.generate_thumbnail(path, "raster", 12).size == (12, 8)
    assert path.read_bytes() == original


def test_generic_executable_provider_tries_raw_only_when_pillow_cannot_identify(tmp_path, monkeypatch):
    path = tmp_path / "input.png"
    path.write_bytes(b"unknown raw format")
    calls = []
    def raw_preview(source, edge):
        calls.append((source, edge))
        return Image.new("RGBA", (edge, edge), "red")
    monkeypatch.setattr(thumbnails, "_raw", raw_preview)
    assert thumbnails.generate_thumbnail(path, "raster", 12).size == (12, 12)
    assert calls == [(path, 12)]


def test_headless_entry_dispatches_without_importing_qt(tmp_path):
    folder = tmp_path / "LumaThumb-entry"
    folder.mkdir()
    source, output = folder / "input.png", folder / "thumbnail.bin"
    source.write_bytes(b"#?RADIANCE\nFORMAT=32-bit_rle_rgbe\n\n-Y 1 +X 2\n" + bytes([128, 64, 32, 129]) * 2)
    output.write_bytes(b"")
    code = (
        "import sys; from luma.__main__ import main; "
        "result=main(sys.argv[1:]); "
        "assert not any(k.startswith('PySide6') for k in sys.modules); "
        "raise SystemExit(result)"
    )
    subprocess.run([sys.executable, "-c", code, "--thumbnail-worker", "raster",
                    str(source), str(output), "32"], check=True, timeout=10)
    assert output.read_bytes()[:8] == thumbnails.MAGIC


def test_generic_raw_tiff_uses_embedded_preview_instead_of_first_tiff_image(tmp_path, monkeypatch):
    path = tmp_path / "input.png"
    exif = Image.Exif()
    exif[50706] = bytes((1, 4, 0, 0))
    Image.new("RGB", (16, 12), "red").save(path, format="TIFF", exif=exif)
    called = []
    def preview(source, edge):
        called.append((source, edge))
        return Image.new("RGBA", (edge, edge // 2), "blue")
    monkeypatch.setattr(thumbnails, "_raw", preview)
    assert thumbnails.generate_thumbnail(path, "raster", 32).size == (32, 16)
    assert called == [(path, 32)]


def test_generic_nonraw_tagged_tiff_retains_pillow_fallback(tmp_path, monkeypatch):
    import rawpy
    path = tmp_path / "input.png"
    exif = Image.Exif()
    exif[50706] = bytes((1, 4, 0, 0))
    Image.new("RGB", (16, 12), "red").save(path, format="TIFF", exif=exif)
    def unsupported(*args):
        raise rawpy.LibRawFileUnsupportedError()
    monkeypatch.setattr(thumbnails, "_raw", unsupported)
    assert thumbnails.generate_thumbnail(path, "raster", 32).size == (16, 12)
