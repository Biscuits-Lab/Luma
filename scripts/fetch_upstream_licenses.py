#!/usr/bin/env python3
"""Refresh missing upstream notices from official, exact-version source trees.

This reads official source archives, extracts text notices/evidence, and never executes upstream code. The checked-in manifest
records immutable source revisions, paths, URLs and SHA-256 hashes. This is a
notice collection, NOT the corresponding source required by LGPL distribution.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import posixpath
import re
import sys
import time
import tarfile
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
QT_TRANSLATION_CATALOGS = ("qtbase_zh_CN", "qtbase_zh_TW")
QT_TRANSLATION_EVIDENCE = (
    "translations/CMakeLists.txt", "translations/README", "translations/split-qt-ts.pl",
)


def translation_metadata(content, tag: str) -> dict:
    """Review the actual catalog declaration, independently of tool licenses."""
    declaration = content("translations/CMakeLists.txt").decode("utf-8")
    if not re.search(
            r'qt_internal_add_sbom\(\s*Translations\s+TYPE\s+QT_TRANSLATIONS'
            r'\s+QT_LICENSE_ID\s+"QT_COMMERCIAL_OR_BSD3"\s*\)', declaration):
        raise RuntimeError("Qt translation catalog license declaration changed; review before distribution")
    return {
        "license_expression": "BSD-3-Clause",
        "license_basis": "translations/CMakeLists.txt",
        "qtbase_license_mapping": (
            f"https://github.com/qt/qtbase/blob/{tag}/cmake/QtPublicSbomLicenseHelpers.cmake"
        ),
        "catalogs": {
            name + ".qm": {
                "source": f"translations/{name}.ts",
                "source_sha256": hashlib.sha256(content(f"translations/{name}.ts")).hexdigest(),
            } for name in QT_TRANSLATION_CATALOGS
        },
    }


def fetch(url: str) -> bytes:
    for attempt in range(3):
        try:
            with urlopen(Request(url, headers={"User-Agent": "Luma-license-collector/0.1"}), timeout=45) as response:
                return response.read()
        except Exception:
            if attempt == 2:
                raise
            time.sleep(1 + attempt)
    raise AssertionError("unreachable")


def collect_repo(repo: str, tag: str, destination: Path) -> dict:
    archive_ref = tag if len(tag) == 40 else f"refs/tags/{tag}"
    archive_url = f"https://codeload.github.com/{repo}/tar.gz/{archive_ref}"
    cache = ROOT / "build/source-cache" / f"{repo.rsplit('/', 1)[1]}-{tag}.tar.gz"
    cache.parent.mkdir(parents=True, exist_ok=True)
    if not cache.is_file():
        cache.write_bytes(fetch(archive_url))
    archive_hash = hashlib.sha256(cache.read_bytes()).hexdigest()
    result = {}
    with tarfile.open(cache, "r:gz") as archive:
        members = {m.name.split("/", 1)[1]: m for m in archive.getmembers()
                   if m.isfile() and "/" in m.name}
        paths = {
            p for p in members
            if p.startswith("LICENSES/")
            or p.endswith("qt_attribution.json")
            or (("3rdparty/" in p or "external/" in p) and PurePosixPath(p).name.lower().startswith(("license", "copying", "copyright", "notice")))
            or PurePosixPath(p).name.lower().startswith(("license", "copying", "copyright", "notice"))
        }
        if repo == "qt/qttranslations":
            paths.update(QT_TRANSLATION_EVIDENCE)
        if not paths:
            raise RuntimeError(f"No licenses discovered in {repo}@{tag}")
        def content(path):
            if path not in members:
                raise RuntimeError(f"Missing source/license evidence: {repo}/{path}")
            stream = archive.extractfile(members[path])
            if stream is None:
                raise RuntimeError(f"Not a regular file: {path}")
            return stream.read()
        for path in list(paths):
            if not path.endswith("qt_attribution.json"):
                continue
            entries = json.loads(content(path), strict=False)
            for entry in entries if isinstance(entries, list) else [entries]:
                files = entry.get("LicenseFile", [])
                if isinstance(files, str):
                    files = [files]
                for file in files:
                    candidate = posixpath.normpath(posixpath.join(posixpath.dirname(path), file))
                    if candidate not in members:
                        raise RuntimeError(f"Missing attribution license: {repo}/{path}: {file}")
                    paths.add(candidate)
        for path in sorted(paths):
            if ".." in PurePosixPath(path).parts or path.startswith("/"):
                raise RuntimeError(f"Unsafe archive path: {path}")
            data = content(path)
            local = destination / path
            local.parent.mkdir(parents=True, exist_ok=True)
            local.write_bytes(data)
            result[path] = {"url": f"https://github.com/{repo}/blob/{tag}/{path}",
                            "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
        revision = archive.pax_headers.get("comment", tag)
        translations = translation_metadata(content, tag) if repo == "qt/qttranslations" else None
    component = {"repository": f"https://github.com/{repo}", "tag": tag, "revision": revision,
                 "source_archive": archive_url, "source_archive_sha256": archive_hash, "files": result}
    if translations is not None:
        component["translations"] = translations
    return component


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qt-version", default="6.12.0")
    parser.add_argument("--python-version", default=".".join(map(str, sys.version_info[:3])))
    args = parser.parse_args()
    destination = ROOT / "packaging/licenses/upstream"
    destination.mkdir(parents=True, exist_ok=True)
    manifest = {"purpose": "Full upstream license texts and attributions; not complete corresponding source", "components": {}}
    for key, repo, version in [
        ("qtbase", "qt/qtbase", args.qt_version),
        ("qttranslations", "qt/qttranslations", args.qt_version),
        ("pyside", "pyside/pyside-setup", args.qt_version),
        ("openexr", "AcademySoftwareFoundation/openexr", "3.5.2"),
        ("imath", "AcademySoftwareFoundation/Imath", "3.2.3"),
        ("pybind11", "pybind/pybind11", "2.13.6"),
        ("libraw", "LibRaw/LibRaw", "b860248a89d9082b8e0a1e202e516f46af9adb29"),
    ]:
        print(f"Collecting {repo} v{version}…", flush=True)
        manifest["components"][key] = collect_repo(repo, version if len(version) == 40 else f"v{version}", destination / key)
    python_url = f"https://raw.githubusercontent.com/python/cpython/v{args.python_version}/LICENSE"
    data = fetch(python_url)
    (destination / "Python-LICENSE.txt").write_bytes(data)
    manifest["components"]["python"] = {
        "version": args.python_version, "url": python_url,
        "file": "Python-LICENSE.txt", "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
    }
    doc_url = f"https://raw.githubusercontent.com/python/cpython/v{args.python_version}/Doc/license.rst"
    doc_data = fetch(doc_url)
    (destination / "python-notices").mkdir(exist_ok=True)
    (destination / "python-notices" / "Doc-license.rst").write_bytes(doc_data)
    manifest["components"]["python-notices"] = {"version": args.python_version, "files": {"Doc-license.rst": {"url": doc_url, "sha256": hashlib.sha256(doc_data).hexdigest(), "bytes": len(doc_data)}}}
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Saved license texts and integrity manifest to {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
