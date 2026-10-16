#!/usr/bin/env python3
"""Create updater-compatible portable ZIP and SHA256SUMS after Windows smoke."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default="0.1.0")
    parser.add_argument("--bundle", type=Path, default=ROOT / "dist/Luma")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts")
    args = parser.parse_args()
    if sys.platform != "win32":
        raise RuntimeError("A Windows release must be packaged and tested on Windows")
    bundle = args.bundle.resolve()
    if not (bundle / "Luma.exe").is_file() or (bundle / "DEVELOPMENT_ONLY.txt").exists():
        raise RuntimeError("Missing Windows executable or development-only bundle")
    manifest = json.loads((bundle / "_internal/licenses/manifest.json").read_text(encoding="utf-8"))
    if not manifest.get("license_texts_complete"):
        raise RuntimeError("Incomplete license material")
    smoke = json.loads((ROOT / "build/smoke-report.json").read_text(encoding="utf-8"))
    if smoke.get("platform") != "win32" or smoke.get("qt_platform") != "windows" or not smoke.get("raw_source"):
        raise RuntimeError("Native Windows packaged smoke, including real RAW, must pass first")
    expected = {"empty-window", "smoke.png", "smoke.tga", "smoke.dds", "图像 测试.png", "smoke.hdr", "smoke.exr", "图像 8192-square.exr", "iss030e122639.NEF", "相机 测试.NEF"}
    passed = {r["fixture"] for r in smoke.get("results", []) if r["exit_code"] == 0}
    if not expected.issubset(passed):
        raise RuntimeError("Incomplete Windows packaged format checks")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    source_archive = output / f"Luma-{args.version}-dependency-sources.zip"
    if not source_archive.is_file():
        raise RuntimeError("Matching dependency source artifact is required")
    asset = output / f"Luma-{args.version}-windows-x64.zip"
    with zipfile.ZipFile(asset, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(bundle.rglob("*")):
            if path.is_symlink():
                raise RuntimeError(f"Symlink is not allowed in the portable package: {path}")
            if path.is_file():
                archive.write(path, f"Luma/{path.relative_to(bundle).as_posix()}")
    hashes = []
    for path in (asset, source_archive):
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        hashes.append(f"{digest.hexdigest()}  {path.name}")
    (output / "SHA256SUMS").write_text("\n".join(hashes) + "\n", encoding="ascii")
    (output / "WINDOWS-SMOKE-REPORT.json").write_text(json.dumps(smoke, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Prepared {asset} ({asset.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
