"""Headless Explorer thumbnail decoder. Never imports Qt or alters source images.

The native bridge owns the process deadline/job limits and private scratch files.
Explorer RAW thumbnails prefer embedded previews; the interactive RAW viewer
continues to develop the full sensor image. Output is SDR, straight RGBA8.
"""
from __future__ import annotations

from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
import struct
import sys
import warnings

MAX_FILE_BYTES = 256 * 1024 * 1024
MAX_RASTER_PIXELS = 64_000_000
MAX_LINEAR_PIXELS = 16_000_000
MAX_DIMENSION = 32768
MAX_EDGE = 1024
MAGIC = b"LUMATHM1"
MODES = {"raster": ".png", "hdr": ".hdr", "exr": ".exr", "raw": ".nef"}


def _dimensions(width, height, ceiling=MAX_RASTER_PIXELS):
    if not (0 < width <= MAX_DIMENSION and 0 < height <= MAX_DIMENSION and width * height <= ceiling):
        raise ValueError("Image exceeds Explorer thumbnail limits")


def _raster(source, edge, fallback_flip=0):
    from PIL import Image, ImageCms
    _dimensions(*source.size)
    icc = source.info.get("icc_profile")
    if icc and len(icc) > 4 * 1024 * 1024:
        raise ValueError("ICC profile exceeds thumbnail limit")
    try:
        orientation = source.getexif().get(274, 1)
    except (OSError, ValueError, TypeError, SyntaxError):
        orientation = 1
    operation = {
        2: Image.Transpose.FLIP_LEFT_RIGHT, 3: Image.Transpose.ROTATE_180,
        4: Image.Transpose.FLIP_TOP_BOTTOM, 5: Image.Transpose.TRANSPOSE,
        6: Image.Transpose.ROTATE_270, 7: Image.Transpose.TRANSVERSE,
        8: Image.Transpose.ROTATE_90,
    }.get(orientation)
    if operation is None:
        operation = {3: Image.Transpose.ROTATE_180, 5: Image.Transpose.ROTATE_90,
                     6: Image.Transpose.ROTATE_270}.get(fallback_flip)
    source.draft("RGB", (edge, edge))
    source.thumbnail((edge, edge), Image.Resampling.LANCZOS, reducing_gap=3.0)
    # Pillow applies TIFF orientation during load; do not transform it twice.
    working = (source.transpose(operation)
               if operation is not None and source.format != "TIFF" else source)
    rgba = working.convert("RGBA")
    if icc:
        try:
            profile = ImageCms.ImageCmsProfile(BytesIO(icc))
            cms_source = working if working.mode in ("RGB", "RGBA", "CMYK", "LAB", "L") else working.convert("RGB")
            converted = ImageCms.profileToProfile(cms_source, profile, ImageCms.createProfile("sRGB"), outputMode="RGB")
            converted.putalpha(rgba.getchannel("A"))
            rgba = converted
        except (ImageCms.PyCMSError, OSError, ValueError, TypeError):
            pass  # Invalid/unsupported profile: same assumed-sRGB fallback as viewer.
    return rgba


@contextmanager
def _decoder_limits():
    from . import decoders
    limits = {"MAX_FILE_BYTES": MAX_FILE_BYTES, "MAX_PIXELS": MAX_LINEAR_PIXELS,
              "MAX_DECODE_BYTES": 512 * 1024 * 1024}
    previous = {name: getattr(decoders, name) for name in limits}
    try:
        for name, value in limits.items():
            setattr(decoders, name, value)
        yield decoders
    finally:
        for name, value in previous.items():
            setattr(decoders, name, value)


def _linear_or_high_precision(path, edge):
    from PIL import Image
    with _decoder_limits() as decoders:
        decoded = decoders.decode_image(path)
        image = Image.fromarray(decoders.render_rgba(decoded))
    image.thumbnail((edge, edge), Image.Resampling.LANCZOS, reducing_gap=3.0)
    return image


def _raw(path, edge):
    import rawpy
    from PIL import Image
    with rawpy.RawPy() as raw:
        raw.open_file(str(path))
        _dimensions(raw.sizes.raw_width, raw.sizes.raw_height)
        try:
            preview = raw.extract_thumb()
        except (rawpy.LibRawNoThumbnailError, rawpy.LibRawUnsupportedThumbnailError):
            raw.unpack()
            rgb = raw.postprocess(half_size=True, use_camera_wb=True,
                                  output_color=rawpy.ColorSpace.sRGB, output_bps=8)
            _dimensions(rgb.shape[1], rgb.shape[0], MAX_LINEAR_PIXELS)
            return _raster(Image.fromarray(rgb), edge)
        if preview.format == rawpy.ThumbFormat.JPEG:
            if len(preview.data) > MAX_FILE_BYTES:
                raise ValueError("Embedded preview exceeds limit")
            with Image.open(BytesIO(preview.data)) as image:
                return _raster(image, edge, raw.sizes.flip)
        if preview.format == rawpy.ThumbFormat.BITMAP:
            data = preview.data
            if data.ndim != 3 or data.shape[2] not in (3, 4) or str(data.dtype) != "uint8":
                raise ValueError("Unsupported RAW preview layout")
            _dimensions(data.shape[1], data.shape[0], MAX_LINEAR_PIXELS)
            return _raster(Image.fromarray(data), edge, raw.sizes.flip)
        raise ValueError("Unsupported RAW preview format")


def generate_thumbnail(path, mode, edge):
    """Return a bounded Pillow RGBA image; no disk writes or Qt initialization."""
    from PIL import Image
    path = Path(path)
    if mode not in MODES or not isinstance(edge, int) or not 1 <= edge <= MAX_EDGE:
        raise ValueError("Invalid thumbnail request")
    if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= MAX_FILE_BYTES:
        raise ValueError("Invalid or oversized thumbnail input")
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        if mode in ("hdr", "exr"):
            result = _linear_or_high_precision(path, edge)
        elif mode == "raw":
            result = _raw(path, edge)
        else:
            with Image.open(path) as image:
                if image.mode in ("I", "F") or image.mode.startswith("I;16"):
                    result = _linear_or_high_precision(path, edge)
                else:
                    result = _raster(image, edge)
    if result.mode != "RGBA" or not 1 <= result.width <= edge or not 1 <= result.height <= edge:
        raise ValueError("Invalid thumbnail result")
    return result


def main(argv=None):
    """Private bridge entry point; write only its pre-created scratch output."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 4:
        return 2
    mode, source_name, output_name, edge_text = args
    try:
        edge = int(edge_text)
        source, output = Path(source_name), Path(output_name)
        if (mode not in MODES or not source.is_absolute() or not output.is_absolute()
                or source.parent != output.parent or not source.parent.name.startswith("LumaThumb-")
                or source.name != "input" + MODES[mode] or output.name != "thumbnail.bin"
                or output.is_symlink() or not output.is_file() or output.stat().st_nlink != 1
                or source.samefile(output)):
            return 2
        image = generate_thumbnail(source, mode, edge)
        payload = MAGIC + struct.pack("<II", image.width, image.height) + image.tobytes()
        if len(payload) > 16 + MAX_EDGE * MAX_EDGE * 4:
            return 2
        # Native bridge pins this pre-created file against rename/delete.
        with output.open("r+b") as stream:
            stream.write(payload)
            stream.truncate()
            stream.flush()
        return 0
    except (Exception, MemoryError):
        return 2  # The native bridge turns this into a failed thumbnail HRESULT.


if __name__ == "__main__":
    raise SystemExit(main())
