"""EXR thumbnail routing, selected-channel bounds, and real-codec regressions."""
from pathlib import Path
import hashlib
import struct
import subprocess
import sys

import numpy as np
import OpenEXR
import pytest

from luma import decoders as d
from luma import thumbnail_worker as thumbnails
from scripts.smoke_thumbnails import (
    EXR_THUMBNAIL_CASES, prepare_exr_thumbnail_fixtures,
    write_exr_thumbnail_expectations, write_exr_thumbnail_fixture,
)


def _write(path, channels, **header):
    with OpenEXR.File(header, channels) as source:
        source.write(str(path))
    return path


def _center_indices(source, output):
    return ((np.arange(output, dtype=np.int64) * 2 + 1) * source // (output * 2))


def _decoded_reference(path, selected, alpha):
    # Independent eager decode is allowed only for these tiny test fixtures.
    with OpenEXR.File(str(path), separate_channels=True) as source:
        channels = source.channels()
        height, width = channels[selected[0]].pixels.shape
        pixels = np.ones((height, width, 4), dtype=np.float32)
        for index, name in enumerate(selected if len(selected) == 3 else selected * 3):
            pixels[..., index] = channels[name].pixels
        if alpha:
            pixels[..., 3] = channels[alpha].pixels
    return pixels


@pytest.mark.parametrize("mode", ["exr", "raster"])
def test_both_shell_routes_require_bounded_exr_api(tmp_path, monkeypatch, mode):
    path = tmp_path / ("input.exr" if mode == "exr" else "input.png")
    path.write_bytes(b"\x76\x2f\x31\x01placeholder")
    calls = []
    def preview(source, edge):
        calls.append((source, edge))
        return d.ImageData(source, np.ones((4, 8, 4), dtype=np.float32), True)
    monkeypatch.setattr(d, "decode_exr_thumbnail", preview)
    monkeypatch.setattr(d, "_decode_exr", lambda *a, **k: pytest.fail("Eager EXR decode called"))
    monkeypatch.setattr(d, "decode_image", lambda *a, **k: pytest.fail("Suffix/full decode called"))
    result = thumbnails.generate_thumbnail(path, mode, 8)
    assert result.size == (8, 4)
    assert calls == [(path, 8)]


CHANNEL_LAYOUTS = {
    "rgb": (("R", "G", "B"), None),
    "rgba": (("R", "G", "B"), "A"),
    "layered": (("beauty.R", "beauty.G", "beauty.B"), "beauty.A"),
    "y": (("Y",), None),
    "scalar": (("depth.Z",), None),
    "alpha_only": (("A",), "A"),
    "unlayered_preferred": (("R", "G", "B"), "A"),
}


@pytest.mark.parametrize("sample_type", [np.float16, np.float32])
@pytest.mark.parametrize("compression", [OpenEXR.ZIP_COMPRESSION, OpenEXR.PIZ_COMPRESSION, OpenEXR.DWAA_COMPRESSION])
@pytest.mark.parametrize("layout", CHANNEL_LAYOUTS)
def test_codec_channel_matrix_matches_independent_decoded_pixels(tmp_path, sample_type, compression, layout):
    selected, alpha = CHANNEL_LAYOUTS[layout]
    base = (np.arange(8 * 16).reshape(8, 16) / 256 + 0.125).astype(sample_type)
    channels = {name: base * (index + 1) for index, name in enumerate(selected)}
    if alpha:
        channels[alpha] = np.full(base.shape, 0.5, dtype=sample_type)
    if layout in {"layered", "unlayered_preferred"}:
        channels["Z"] = np.full(base.shape, 999.0, dtype=sample_type)
    if layout == "unlayered_preferred":
        channels.update({"other." + color: base * 7 for color in ("R", "G", "B")})
    path = _write(tmp_path / "matrix.EXR", channels, compression=compression)
    original = path.read_bytes()
    expected = _decoded_reference(path, selected, alpha)
    result = d.decode_exr_thumbnail(path, 4)
    assert (result.width, result.height) == (4, 2)
    expected = expected[_center_indices(8, 2)][:, _center_indices(16, 4)]
    np.testing.assert_array_equal(result.pixels, expected)
    assert result.metadata["Displayed channels"] == ", ".join(selected)
    assert result.metadata["Alpha mode"] == (
        "straight" if layout == "alpha_only" else
        "premultiplied (OpenEXR convention)" if alpha else "opaque")
    # Color/alpha must survive the actual thumbnail worker's tone-map path.
    rendered = np.asarray(thumbnails.generate_thumbnail(path, "exr", 4))
    reference = d.ImageData(path, expected, True, {"Alpha mode": result.metadata["Alpha mode"]})
    np.testing.assert_array_equal(rendered, d.render_rgba(reference))
    assert path.read_bytes() == original


@pytest.mark.parametrize("storage", [OpenEXR.scanlineimage, OpenEXR.tiledimage])
@pytest.mark.parametrize("origin", [(0, 0), (-17, 23), (100, -200)])
def test_data_window_offsets_and_flat_tiled_layout(tmp_path, storage, origin):
    values = np.arange(16 * 8, dtype=np.float32).reshape(8, 16) / 128
    minimum = np.asarray(origin, dtype=np.int32)
    maximum = minimum + np.array([15, 7], dtype=np.int32)
    header = {"type": storage, "dataWindow": (minimum, maximum)}
    if storage == OpenEXR.tiledimage:
        tile = OpenEXR.TileDescription()
        tile.xSize, tile.ySize = 4, 4
        header["tiles"] = tile
    path = _write(tmp_path / "offset.exr", {"Y": values}, **header)
    result = d.decode_exr_thumbnail(path, 4)
    np.testing.assert_array_equal(result.pixels[..., 0], values[[2, 6]][:, [2, 6, 10, 14]])


def test_decreasing_scanline_order_keeps_image_orientation(tmp_path):
    values = np.arange(8 * 16, dtype=np.float32).reshape(8, 16)
    path = _write(tmp_path / "descending.exr", {"Y": values}, lineOrder=OpenEXR.DECREASING_Y)
    result = d.decode_exr_thumbnail(path, 4)
    np.testing.assert_array_equal(result.pixels[..., 0], values[[2, 6]][:, [2, 6, 10, 14]])


def test_mixed_half_float_uint_and_nonfinite_samples(tmp_path):
    path = _write(tmp_path / "混合 通道.EXR", {
        "R": np.array([[1, 2, 3, 4], [0, 4, 8, 16]], dtype=np.uint32),
        "G": np.array([[0.125, 0.25, 0.5, 1], [0, 0.125, 0.25, 0.5]], dtype=np.float16),
        "B": np.array([[np.nan, np.inf, -np.inf, -1], [0, 1, 2, 3]], dtype=np.float32),
        "A": np.array([[0, 0.25, 0.5, 1], [1, 1, 1, 1]], dtype=np.float16),
    })
    expected = _decoded_reference(path, ("R", "G", "B"), "A")
    result = d.decode_exr_thumbnail(path, 32)
    assert (result.width, result.height) == (4, 2)  # Never upscale the source.
    np.testing.assert_array_equal(result.pixels, expected)
    rendered = np.asarray(thumbnails.generate_thumbnail(path, "exr", 32))
    assert rendered.dtype == np.uint8 and rendered.shape == (2, 4, 4)
    assert rendered[0, :, 3].tolist() == [0, 64, 128, 255]


@pytest.mark.parametrize("edge", [0, 1025, -1, 2.5, True, None])
def test_invalid_preview_edge_rejected_before_open(tmp_path, edge):
    with pytest.raises(ValueError):
        d.decode_exr_thumbnail(tmp_path / "missing.exr", edge)


def test_preview_cancellation_remains_distinct(tmp_path):
    with pytest.raises(d.DecodeCancelled):
        d.decode_exr_thumbnail(tmp_path / "missing.exr", 32, cancelled=lambda: True)


@pytest.mark.parametrize("layout", ["multipart", "deep", "subsampled", "too_many_channels"])
def test_unsafe_layouts_rejected_before_channel_reads(tmp_path, monkeypatch, layout):
    path = tmp_path / "unsupported.exr"
    values = np.ones((4, 8), dtype=np.float32)
    if layout == "multipart":
        with OpenEXR.File([OpenEXR.Part({"name": "a"}, {"Y": values}),
                           OpenEXR.Part({"name": "b"}, {"Y": values})]) as image:
            image.write(str(path))
    elif layout == "deep":
        deep = np.empty((1, 2), dtype=object)
        deep[0, 0], deep[0, 1] = np.array([1], dtype=np.float32), np.array([2], dtype=np.float32)
        _write(path, {"Z": deep}, type=OpenEXR.deepscanline, compression=OpenEXR.ZIPS_COMPRESSION)
    elif layout == "subsampled":
        _write(path, {"Y": OpenEXR.Channel(values, 2, 2)})
    else:
        _write(path, {f"plane{i:02}": values for i in range(65)})
    input_class = OpenEXR.InputFile
    class HeaderOnly:
        def __init__(self, *args, **kwargs):
            self.source = input_class(*args, **kwargs)
        def __getattr__(self, name):
            return getattr(self.source, name)
        def channels(self, *args, **kwargs):
            pytest.fail("Unsafe header reached channel reads")
    monkeypatch.setattr(OpenEXR, "InputFile", HeaderOnly)
    with pytest.raises(d.DecodeError):
        d.decode_exr_thumbnail(path, 32)


def _attribute_span(data, wanted):
    offset = 8
    while data[offset]:
        name_end = data.index(0, offset)
        name = data[offset:name_end].decode("ascii")
        type_end = data.index(0, name_end + 1)
        size_offset = type_end + 1
        size = struct.unpack_from("<I", data, size_offset)[0]
        payload = size_offset + 4
        if name == wanted:
            return payload, size
        offset = payload + size
    raise AssertionError(f"Missing test attribute: {wanted}")


@pytest.mark.parametrize("damage", ["truncated_header", "truncated_pixels", "bad_compression", "reversed_window", "huge_dimension", "pixel_limit", "invalid_channel_type", "oversize_attribute"])
def test_malformed_exr_header_and_chunks_fail_bounded(tmp_path, damage):
    path = _write(tmp_path / "broken.exr", {"Y": np.ones((4, 8), dtype=np.float32)})
    data = bytearray(path.read_bytes())
    if damage == "truncated_header":
        data = data[:30]
    elif damage == "truncated_pixels":
        data = data[:-12]
    elif damage == "bad_compression":
        offset, _ = _attribute_span(data, "compression")
        data[offset] = 255
    elif damage in {"reversed_window", "huge_dimension", "pixel_limit"}:
        offset, _ = _attribute_span(data, "dataWindow")
        window = {"reversed_window": (10, 0, 0, 3), "huge_dimension": (0, 0, 32768, 0),
                  "pixel_limit": (0, 0, 32767, 16383)}[damage]
        struct.pack_into("<iiii", data, offset, *window)
    elif damage == "invalid_channel_type":
        offset, _ = _attribute_span(data, "channels")
        channel_end = data.index(0, offset)
        struct.pack_into("<i", data, channel_end + 1, 99)
    else:
        offset, _ = _attribute_span(data, "channels")
        struct.pack_into("<I", data, offset - 4, 0x7fffffff)
    path.write_bytes(data)
    with pytest.raises(d.DecodeError):
        d.decode_exr_thumbnail(path, 32)


def test_exr_preview_file_ceiling_is_independent_of_full_viewer(tmp_path, monkeypatch):
    path = _write(tmp_path / "size.exr", {"Y": np.ones((2, 2), dtype=np.float32)})
    monkeypatch.setattr(d, "MAX_EXR_PREVIEW_FILE_BYTES", path.stat().st_size - 1)
    with pytest.raises(d.ImageTooLargeError):
        d.decode_exr_thumbnail(path, 2)


@pytest.fixture(scope="module")
def large_exr_fixtures(tmp_path_factory):
    return prepare_exr_thumbnail_fixtures(tmp_path_factory.mktemp("large-exr-thumbnails"))


@pytest.mark.parametrize("index", range(len(EXR_THUMBNAIL_CASES)))
def test_large_compressed_exr_both_routes_bounded_and_unchanged(large_exr_fixtures, monkeypatch, tmp_path, index):
    path = large_exr_fixtures[index]
    width, height, sample_type, compression, channels = EXR_THUMBNAIL_CASES[index]
    original = hashlib.sha256(path.read_bytes()).digest()
    assert path.stat().st_size < thumbnails.MAX_FILE_BYTES
    original_limits = d.MAX_PIXELS, d.MAX_DECODE_BYTES
    original_decode = d._decode_exr
    def preview_only(*args, **kwargs):
        assert kwargs.get("preview_edge") == 256, "Full EXR decode called"
        return original_decode(*args, **kwargs)
    monkeypatch.setattr(d, "_decode_exr", preview_only)
    monkeypatch.setattr(d, "decode_image", lambda *a, **k: pytest.fail("Full decode called"))
    file_class = OpenEXR.File
    def header_only(*args, **kwargs):
        assert kwargs.get("header_only") is True, "Full-source channel allocation"
        return file_class(*args, **kwargs)
    monkeypatch.setattr(OpenEXR, "File", header_only)
    input_class = OpenEXR.InputFile
    strip_reads = []
    class BoundedInput:
        def __init__(self, *args, **kwargs):
            self.source = input_class(*args, **kwargs)
        def __getattr__(self, name):
            return getattr(self.source, name)
        def channels(self, names, **kwargs):
            assert set(names) == set(channels) - {"Z"}
            buffers = self.source.channels(names, **kwargs)
            total = sum(len(buffer) for buffer in buffers)
            assert total <= d.MAX_EXR_STRIP_BYTES
            strip_reads.append(total)
            return buffers
    monkeypatch.setattr(OpenEXR, "InputFile", BoundedInput)
    original_render = d.render_rgba
    def bounded_render(image, *args, **kwargs):
        assert image.nbytes <= 256 * 256 * 16
        return original_render(image, *args, **kwargs)
    monkeypatch.setattr(d, "render_rgba", bounded_render)
    direct = thumbnails.generate_thumbnail(path, "exr", 256)
    synthetic = tmp_path / "input.png"
    synthetic.write_bytes(path.read_bytes())
    generic = thumbnails.generate_thumbnail(synthetic, "raster", 256)
    assert direct.size == (256, round(height * 256 / width))
    np.testing.assert_array_equal(np.asarray(direct), np.asarray(generic))
    assert direct.getpixel((32, direct.height // 2)) != direct.getpixel((224, direct.height // 2))
    alpha = 128 if any(name.endswith("A") for name in channels) else 255
    assert direct.getpixel((32, direct.height // 2))[3] == alpha
    # Independently known interior color bands verify selection and alpha
    # unpremultiplication. DWA's slight source loss allows three display codes.
    y_probe = direct.height // 8
    gray = channels == ("Y",)
    expected_rgb = [0.125, 0.125 if not gray else 0.125, 0.0625 if not gray else 0.125]
    expected = d.ImageData(path, np.array([[expected_rgb + [1.0]]], dtype=np.float32), True)
    expected_rgba = original_render(expected)[0, 0]
    actual = np.array(direct.getpixel((32, y_probe)))
    assert np.all(np.abs(actual[:3].astype(int) - expected_rgba[:3].astype(int)) <= 3)
    assert hashlib.sha256(path.read_bytes()).digest() == original
    assert (d.MAX_PIXELS, d.MAX_DECODE_BYTES) == original_limits
    assert strip_reads


def test_native_probe_file_contains_actual_decoded_bgra_and_alpha(tmp_path):
    path = tmp_path / "probe.exr"
    write_exr_thumbnail_fixture(path, 32, 16, compression="DWAA")
    output = tmp_path / "probe.txt"
    write_exr_thumbnail_expectations(path, output)
    lines = output.read_text(encoding="ascii").splitlines()
    assert lines[0] == "LUMA_EXR_PROBES_1 32 16 16"
    image = thumbnails.generate_thumbnail(path, "exr", 256)
    for line in lines[1:]:
        x, y, blue, green, red, alpha = map(int, line.split())
        assert image.getpixel((x, y)) == (red, green, blue, alpha)


@pytest.mark.parametrize("mode", ["exr", "raster"])
def test_private_worker_exr_protocol_stays_headless_and_preserves_source(tmp_path, mode):
    folder = tmp_path / "LumaThumb-exr-protocol"
    folder.mkdir()
    source = folder / ("input.exr" if mode == "exr" else "input.png")
    output = folder / "thumbnail.bin"
    write_exr_thumbnail_fixture(source, 32, 16, compression="PIZ")
    original = source.read_bytes()
    output.write_bytes(b"old payload")
    code = (
        "import sys; from luma.__main__ import main; "
        "status=main(sys.argv[1:]); "
        "assert not any(name.startswith('PySide6') for name in sys.modules); "
        "raise SystemExit(status)"
    )
    subprocess.run([sys.executable, "-c", code, "--thumbnail-worker", mode,
                    str(source), str(output), "16"], check=True, timeout=10)
    payload = output.read_bytes()
    assert payload[:8] == thumbnails.MAGIC
    assert struct.unpack("<II", payload[8:16]) == (16, 8)
    assert len(payload) == 16 + 16 * 8 * 4
    assert source.read_bytes() == original
    expected = thumbnails.generate_thumbnail(source, mode, 16)
    assert payload[16:] == expected.tobytes()


def test_required_windows_smoke_wires_every_exr_case_and_pixel_probes():
    root = Path(__file__).resolve().parents[1]
    smoke = (root / "scripts/smoke_thumbnails.ps1").read_text()
    harness = (root / "native/thumbnail/thumbnail_tests.cpp").read_text()
    assert "files += prepare_exr_thumbnail_fixtures(destination)" in smoke
    assert "moderate_exr_recipe = write_moderate_exr_fixture(moderate_exr)" in smoke
    assert "files.append(moderate_exr)" in smoke
    assert '"moderate_exr_recipe": moderate_exr_recipe' in smoke
    assert "write_exr_thumbnail_expectations(path, expected)" in smoke
    assert "Missing required EXR pixel expectations" in smoke
    assert 'foreach ($phase in @("direct", "surrogate"))' in smoke
    assert "application-surrogate" in smoke
    assert "LUMA_THUMBNAIL_EXPECTED" in smoke and "LUMA_THUMBNAIL_EXPECTED" in harness
    assert harness.count("TestExpectedBitmap(bitmap);") == 2


def test_native_probe_readback_normalizes_row_order_and_tests_both_dib_layouts():
    root = Path(__file__).resolve().parents[1]
    harness = (root / "native/thumbnail/thumbnail_tests.cpp").read_text()
    reader = harness.split("std::vector<BYTE> ReadTopDownBitmap", 1)[1].split("void TestBitmapReadback", 1)[0]
    assert "GetDIBits(dc, bitmap" in reader
    assert "info.bmiHeader.biHeight = -height" in reader
    assert "dsBmih" not in reader and "bmBits" not in reader.replace("bmBitsPixel", "")
    assert "const auto pixels = ReadTopDownBitmap(bitmap, width, height)" in harness
    assert "for (const LONG signedHeight : {-height, height})" in harness
    assert 'Require(actual == expected, "Bitmap readback changed orientation/color/alpha")' in harness
    assert "        TestBitmapReadback();" in harness
    # Keep real EXR probe checks strict; no tolerance inflation or y-mirror
    # retry is permitted to make the former harness-only flip appear valid.
    assert "if (difference < -2 || difference > 2)" in harness


def test_native_thumbnail_resource_envelope_is_unchanged():
    root = Path(__file__).resolve().parents[1]
    native = (root / "native/thumbnail/thumbnail.cpp").read_text()
    assert "kMaxSource = 256ull * 1024 * 1024" in native
    assert "kDeadlineMs = 10000" in native
    assert "kMemoryLimit = 768ull * 1024 * 1024" in native
    assert thumbnails.MAX_FILE_BYTES == 256 * 1024 * 1024
