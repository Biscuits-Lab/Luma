#!/usr/bin/env python3
"""Launch the real bundled UI/decoders on small fixtures and a real RAW file."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.request import urlopen

import numpy as np
from PIL import Image
import OpenEXR

ROOT = Path(__file__).resolve().parents[1]
RAW_NAME = "iss030e122639.NEF"
RAW_URL = f"https://raw.githubusercontent.com/letmaik/rawpy/main/test/{RAW_NAME}"
RAW_BLOB = "e03d193927a604de40fd0fd7a71e33fde878a0e1"


def write_hdr_thumbnail_fixture(path: Path, width: int, height: int) -> None:
    """Bounded independent RGBE encoder for large, compressible HDR fixtures.

    Four horizontal bands and a vertical green gradient exercise content and
    orientation. These are synthetic resource regressions, not photographic
    color-quality or worst-case compressed-data benchmarks.
    """
    if not 8 <= width <= 32767 or not 1 <= height <= 32768:
        raise ValueError("Invalid synthetic HDR dimensions")
    lengths = [width * (band + 1) // 4 - width * band // 4 for band in range(4)]

    def plane(values):
        data = bytearray()
        for value, count in zip(values, lengths):
            full, remainder = divmod(count, 127)
            data.extend(bytes((255, value)) * full)
            if remainder:
                data.extend((128 + remainder, value))
        return data

    red = plane((48, 96, 160, 224))
    blue = plane((64, 80, 96, 112))
    exponent = plane((128, 129, 130, 131))
    with path.open("wb") as output:
        output.write(f"#?RADIANCE\nFORMAT=32-bit_rle_rgbe\n\n-Y {height} +X {width}\n".encode())
        for row in range(height):
            output.write(bytes((2, 2, width >> 8, width & 255)))
            output.write(red)
            output.write(plane((32 + row * 160 // height,) * 4))
            output.write(blue)
            output.write(exponent)


def prepare_hdr_thumbnail_fixtures(destination: Path) -> list[Path]:
    """4K/8K/16K environment-map sizes, only for the bounded thumbnail path."""
    destination.mkdir(parents=True, exist_ok=True)
    files = []
    for width, height in ((4096, 2048), (8192, 4096), (16384, 8192)):
        path = destination / f"thumbnail-{width}x{height}.hdr"
        write_hdr_thumbnail_fixture(path, width, height)
        files.append(path)
    return files


def prepare_fixtures(destination: Path, include_raw: bool = True) -> list[Path]:
    destination.mkdir(parents=True, exist_ok=True)
    y, x = np.mgrid[0:48, 0:64]
    pixels = np.empty((48, 64, 4), dtype=np.uint8)
    pixels[..., 0] = x * 4
    pixels[..., 1] = y * 5
    pixels[..., 2] = 150
    pixels[..., 3] = 200
    image = Image.fromarray(pixels)
    files = []
    for suffix, fmt in (("png", "PNG"), ("tga", "TGA"), ("dds", "DDS")):
        path = destination / f"smoke.{suffix}"
        image.save(path, format=fmt)
        files.append(path)
    unicode_path = destination / "图像 测试.png"
    image.save(unicode_path)
    files.append(unicode_path)
    hdr = destination / "smoke.hdr"
    hdr.write_bytes(b"#?RADIANCE\nFORMAT=32-bit_rle_rgbe\n\n-Y 2 +X 4\n" + bytes([128, 96, 64, 131]) * 8)
    files.append(hdr)
    exr = destination / "smoke.exr"
    OpenEXR.File({}, {"RGB": np.ascontiguousarray(pixels[..., :3], dtype=np.float32) / 32.0}).write(str(exr))
    files.append(exr)
    if include_raw:
        raw_path = destination / RAW_NAME
        if not raw_path.is_file():
            existing = ROOT / "validation" / RAW_NAME
            if existing.is_file():
                data = existing.read_bytes()
            else:
                with urlopen(RAW_URL, timeout=90) as response:
                    data = response.read(30 * 1024 * 1024 + 1)
            raw_path.write_bytes(data)
        data = raw_path.read_bytes()
        actual = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
        if actual != RAW_BLOB:
            raise RuntimeError("Official RAW test fixture's Git blob hash changed; review before using it")
        files.append(raw_path)
        unicode_raw = destination / "相机 测试.NEF"
        unicode_raw.write_bytes(data)
        files.append(unicode_raw)
    return files


def configure_console_output() -> None:
    # Windows redirected consoles may use cp1252. Keep full Unicode paths in
    # the UTF-8 JSON report, while allowing logs to escape unsupported glyphs.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="backslashreplace")


def main() -> int:
    configure_console_output()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable", type=Path)
    parser.add_argument("--fixtures", type=Path, default=ROOT / "build/testfixtures")
    parser.add_argument("--skip-raw", action="store_true", help="Development only: release checks must include RAW")
    args = parser.parse_args()
    exe = args.executable.resolve()
    if not exe.is_file():
        parser.error(f"Missing executable: {exe}")
    fixtures = prepare_fixtures(args.fixtures.resolve(), not args.skip_raw)
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "windows" if sys.platform == "win32" else "offscreen"
    results = []
    for path in [None, *fixtures]:
        command = [str(exe), "--smoke-test"] + ([str(path)] if path else [])
        started = time.monotonic()
        result = subprocess.run(command, env=env, timeout=90, capture_output=True, text=True, errors="replace")
        record = {"fixture": path.name if path else "empty-window", "exit_code": result.returncode,
                  "seconds": round(time.monotonic() - started, 3), "stdout": result.stdout, "stderr": result.stderr}
        results.append(record)
        print(f"{record['fixture']}: exit {result.returncode}", flush=True)
        if result.returncode:
            print(result.stdout, result.stderr, file=sys.stderr)
            raise RuntimeError(f"Packaged native smoke failed: {record['fixture']}")
    report = {"platform": sys.platform, "qt_platform": env["QT_QPA_PLATFORM"], "executable": str(exe),
              "raw_source": RAW_URL if not args.skip_raw else None, "raw_git_blob": RAW_BLOB if not args.skip_raw else None,
              "results": results}
    (ROOT / "build/smoke-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
