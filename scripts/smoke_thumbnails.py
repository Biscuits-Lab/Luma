"""Run the guarded native-thumbnail smoke on a disposable Windows CI runner."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


# Representative large cases; the small unit matrix covers the full cross
# product. Every file below is required in the direct and both isolated Shell
# association routes. These synthetic bands are not worst-case speed claims.
EXR_THUMBNAIL_CASES = (
    (4096, 2048, "HALF", "ZIP", ("R", "G", "B", "A")),
    (4096, 2048, "FLOAT", "PIZ", ("R", "G", "B")),
    (8192, 4096, "FLOAT", "ZIP", ("beauty.R", "beauty.G", "beauty.B", "beauty.A", "Z")),
    (8192, 8192, "HALF", "DWAA", ("R", "G", "B", "A")),
    (16384, 16384, "HALF", "PIZ", ("Y",)),
    (16384, 8192, "FLOAT", "DWAA", ("R", "G", "B")),
)


def write_exr_thumbnail_fixture(path: Path, width: int, height: int, *,
                                sample_type: str = "HALF", compression: str = "ZIP",
                                channels: tuple[str, ...] = ("R", "G", "B", "A"),
                                strip_rows: int = 32) -> None:
    """Write compressible EXR bands using a bounded official-library strip.

    No full-source image is allocated, including for the 16K fixtures. Keep
    this legacy writer isolated: OpenEXR 3.5.2 still ships OutputFile for
    scanline access, whereas File.write requires full-source arrays.
    """
    import Imath
    import numpy as np
    import OpenEXR

    if (not 1 <= width <= 32768 or not 1 <= height <= 32768
            or width * height > 268_435_456 or not 1 <= strip_rows <= 64
            or sample_type not in {"HALF", "FLOAT"}
            or compression not in {"ZIP", "PIZ", "DWAA"}
            or not channels or len(channels) > 64):
        raise ValueError("Invalid synthetic EXR fixture")
    dtype = np.dtype("float16" if sample_type == "HALF" else "float32")
    pixel_type = Imath.PixelType(getattr(Imath.PixelType, sample_type))
    header = OpenEXR.Header(width, height)
    header["channels"] = {name: Imath.Channel(pixel_type) for name in channels}
    header["compression"] = Imath.Compression(getattr(Imath.Compression, compression + "_COMPRESSION"))
    x_band = np.minimum(np.arange(width) * 4 // width, 3)
    horizontal = np.asarray((0.125, 0.25, 0.5, 1.0), dtype=dtype)[x_band]
    output = OpenEXR.OutputFile(str(path), header)
    try:
        for start in range(0, height, strip_rows):
            count = min(strip_rows, height - start)
            vertical = (0.125 + np.minimum(np.arange(start, start + count) * 4 // height, 3) * 0.125).astype(dtype)
            payload = {}
            for name in channels:
                component = name.rsplit(".", 1)[-1]
                prefix = name[: -len(component)]
                alpha = 0.5 if prefix + "A" in channels else 1.0
                if component in {"R", "Y"}:
                    data = np.broadcast_to(horizontal * alpha, (count, width))
                elif component == "G":
                    data = np.broadcast_to((vertical * alpha)[:, None], (count, width))
                elif component == "B":
                    data = np.full((count, width), 0.0625 * alpha, dtype=dtype)
                elif component == "A":
                    data = np.full((count, width), 0.5, dtype=dtype)
                else:
                    data = np.broadcast_to(horizontal, (count, width))
                payload[name] = data.astype(dtype, copy=False).tobytes()
            output.writePixels(payload, count)
    finally:
        output.close()
    if path.stat().st_size > 256 * 1024 * 1024:
        raise RuntimeError("Synthetic EXR exceeds the unchanged native input ceiling")


def prepare_exr_thumbnail_fixtures(destination: Path) -> list[Path]:
    """Bounded 4K/8K/16K + 8K-square native-surrogate regressions."""
    destination.mkdir(parents=True, exist_ok=True)
    files = []
    for width, height, sample_type, compression, channels in EXR_THUMBNAIL_CASES:
        prefix = "图像 测试-" if (width, sample_type, compression) == (4096, "HALF", "ZIP") else "thumbnail-"
        path = destination / f"{prefix}{width}x{height}-{sample_type}-{compression}.exr"
        write_exr_thumbnail_fixture(path, width, height, sample_type=sample_type,
                                    compression=compression, channels=channels)
        files.append(path)
    return files


def write_exr_thumbnail_expectations(path: Path, destination: Path) -> None:
    """Record decoded-file interior probes for the real native Shell tests.

    DWA is lossy: native comparisons use actual decoded values, while unit
    tests independently check channel selection and expected scene values.
    """
    from luma.decoders import decode_exr_thumbnail, render_rgba

    pixels = render_rgba(decode_exr_thumbnail(path, 256))
    height, width = pixels.shape[:2]
    probes = []
    for y_band in range(4):
        for x_band in range(4):
            x = min(width - 1, (x_band * 2 + 1) * width // 8)
            y = min(height - 1, (y_band * 2 + 1) * height // 8)
            red, green, blue, alpha = map(int, pixels[y, x])
            probes.append(f"{x} {y} {blue} {green} {red} {alpha}")
    destination.write_text(
        f"LUMA_EXR_PROBES_1 {width} {height} {len(probes)}\n"
        + "\n".join(probes) + "\n", encoding="ascii")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--harness", type=Path)
    parser.add_argument("--artifacts", type=Path)
    parser.add_argument("--timeout", type=int, default=60)
    args = parser.parse_args()

    if os.name != "nt":
        parser.error("This smoke requires a disposable Windows x64 CI runner")
    if not any(
        os.environ.get(key, "").lower() in {"true", "1"}
        for key in ("CI", "GITHUB_ACTIONS")
    ):
        parser.error("CI=true or GITHUB_ACTIONS=true is required")
    if not 5 <= args.timeout <= 600:
        parser.error("--timeout must be between 5 and 600 seconds")

    powershell = shutil.which("pwsh")
    if not powershell:
        parser.error("PowerShell 7.2 or newer (pwsh) is required")

    command = [
        powershell, "-NoProfile", "-NonInteractive",
        "-File", str(ROOT / "scripts" / "smoke_thumbnails.ps1"),
        "-RepoRoot", str(ROOT),
        "-Python", sys.executable,
        "-TimeoutSeconds", str(args.timeout),
    ]
    for option, value in (
        ("-Bundle", args.bundle),
        ("-Harness", args.harness),
        ("-Artifacts", args.artifacts),
    ):
        if value is not None:
            command.extend((option, str(value.resolve())))

    # The PowerShell script bounds each child and owns registry cleanup.
    # Do not impose a shorter wrapper timeout that could bypass its finally.
    return subprocess.run(command, cwd=ROOT, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
