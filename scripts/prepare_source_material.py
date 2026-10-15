#!/usr/bin/env python3
"""Prepare exact upstream LGPL source material for the same public release.

Reads official source archives only. Does not compile or run upstream source.
Photographic RAW test fixtures are intentionally not redistributed.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
from urllib.request import urlopen
import zipfile

ROOT = Path(__file__).resolve().parents[1]
RAWPY_REPO = "https://github.com/letmaik/rawpy.git"
RAWPY_TAG = "v0.27.1"
RAWPY_COMMIT = "a39c2e7a44911889c3360891012f862f904ba551"


def sha256(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def git(*args: str, cwd: Path | None = None) -> str:
    return subprocess.check_output(["git", *args], cwd=cwd, text=True).strip()


def prepare(output: Path, version: str) -> Path:
    output.mkdir(parents=True, exist_ok=True)
    cache = ROOT / "build/source-cache"
    cache.mkdir(parents=True, exist_ok=True)
    staging = ROOT / "build/dependency-sources"
    staging.mkdir(parents=True, exist_ok=True)
    license_manifest = json.loads((ROOT / "packaging/licenses/upstream/manifest.json").read_text(encoding="utf-8"))
    records = []
    for name in ("qtbase", "pyside"):
        component = license_manifest["components"][name]
        filename = f"{name}-{component['tag']}.tar.gz"
        cached = cache / filename
        # Reuse the license collector's downloaded source if available.
        existing = cache / f"{component['repository'].rsplit('/', 1)[1]}-{component['tag']}.tar.gz"
        if not cached.is_file() and existing.is_file():
            shutil.copy2(existing, cached)
        if not cached.is_file():
            with urlopen(component["source_archive"], timeout=120) as source, cached.open("wb") as target:
                shutil.copyfileobj(source, target)
        if sha256(cached) != component["source_archive_sha256"]:
            raise RuntimeError(f"Source archive hash mismatch: {name}")
        shutil.copy2(cached, staging / filename)
        records.append({"component": name, "version": component["tag"], "revision": component["revision"],
                        "url": component["source_archive"], "file": filename, "sha256": sha256(cached)})

    checkout = cache / "rawpy-source"
    if not checkout.is_dir():
        subprocess.run(["git", "clone", "--depth", "1", "--branch", RAWPY_TAG, RAWPY_REPO, str(checkout)], check=True)
    if git("rev-parse", "HEAD", cwd=checkout) != RAWPY_COMMIT:
        raise RuntimeError("rawpy source tag no longer matches the reviewed pinned revision")
    expected_urls = {"external/LibRaw": "https://github.com/LibRaw/LibRaw.git", "external/LibRaw-cmake": "https://github.com/LibRaw/LibRaw-cmake"}
    for path, expected_url in expected_urls.items():
        configured = git("config", "-f", ".gitmodules", f"submodule.{path}.url", cwd=checkout)
        if configured.removesuffix(".git") != expected_url.removesuffix(".git"):
            raise RuntimeError(f"Unexpected source repository for {path}")
    subprocess.run(["git", "submodule", "update", "--init", "--depth", "1", *expected_urls], cwd=checkout, check=True)
    submodules = {path: git("rev-parse", "HEAD", cwd=checkout / path) for path in expected_urls}
    raw_archive = staging / f"rawpy-{RAWPY_TAG}-with-LibRaw-source.tar.gz"
    def include_source(info: tarfile.TarInfo):
        parts = Path(info.name).parts
        if ".git" in parts or (len(parts) > 1 and parts[1] == "test"):
            return None
        return info
    with tarfile.open(raw_archive, "w:gz") as archive:
        archive.add(checkout, arcname=f"rawpy-{RAWPY_TAG}", filter=include_source)
    records.append({"component": "rawpy and LibRaw", "version": RAWPY_TAG, "revision": RAWPY_COMMIT,
                    "repository": RAWPY_REPO, "submodules": submodules, "file": raw_archive.name,
                    "sha256": sha256(raw_archive), "omissions": "Photographic test fixtures and .git data; optional GPL demosaic packs are disabled and not included"})
    (staging / "source-manifest.json").write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
    shutil.copy2(ROOT / "docs/REDISTRIBUTION.md", staging / "REDISTRIBUTION.md")
    shutil.copy2(ROOT / "THIRD_PARTY_NOTICES.md", staging / "THIRD_PARTY_NOTICES.md")
    (staging / "README.txt").write_text(
        "Luma dependency source material\n\n"
        "These are official matching source snapshots for the LGPL components shipped in Luma.\n"
        "See source-manifest.json for exact revisions, submodule revisions and SHA-256 hashes.\n"
        "Qt Base: unpack qtbase archive; build instructions and CMake scripts are included.\n"
        "Qt for Python: unpack pyside archive; use its build scripts with the matching Qt.\n"
        "rawpy: unpack rawpy archive; external/LibRaw and external/LibRaw-cmake are populated.\n"
        "Build against 64-bit Python 3.12 and the compatible native compiler/toolchain.\n"
        "Keep LGPL components ABI-compatible with the distributed bindings/platform plugins.\n"
        "No optional GPL demosaic packs were enabled by Luma. Photographic fixtures are omitted.\n"
        "No distributor modifications to these third-party sources are made by this project.\n"
        "Library replacement and recipient rights are described in REDISTRIBUTION.md.\n"
        "Qt build guidance: https://doc.qt.io/qt-6/build-sources.html\n"
        "PySide build guidance: https://doc.qt.io/qtforpython-6/building_from_source/index.html\n"
        "rawpy build guidance is in the included README/setup.py/pyproject.toml.\n",
        encoding="utf-8",
    )
    result = output / f"Luma-{version}-dependency-sources.zip"
    with zipfile.ZipFile(result, "w", compression=zipfile.ZIP_STORED) as archive:
        for path in sorted(staging.iterdir()):
            if path.is_file():
                archive.write(path, f"Luma-dependency-sources/{path.name}")
    print(f"Prepared {result} ({result.stat().st_size:,} bytes)")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts")
    parser.add_argument("--version", default="0.1.0")
    args = parser.parse_args()
    prepare(args.output.resolve(), args.version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
