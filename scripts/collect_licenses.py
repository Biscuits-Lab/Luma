#!/usr/bin/env python3
"""Copy complete installed dependency notices, with versioned upstream fallback.

Nothing is downloaded or guessed here. Missing/changed texts fail closed unless
--allow-incomplete explicitly labels a private development smoke build.
"""
from __future__ import annotations

import argparse
import hashlib
from importlib.metadata import distribution
import json
from pathlib import Path
import platform
import re
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ("PySide6_Essentials", "shiboken6", "Pillow", "numpy", "OpenEXR", "rawpy", "pyinstaller")
LICENSE_NAME = re.compile(r"(?:licen[cs]e|copying|copyright|notice)", re.I)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def collect(output: Path, allow_incomplete: bool = False) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    failures = []
    manifest = {"python": platform.python_version(), "platform": platform.platform(), "packages": {}, "issues": failures}
    import rawpy
    from PySide6.QtCore import QLibraryInfo, qVersion
    manifest["native_components"] = {"Qt": qVersion(), "LibRaw": ".".join(map(str, rawpy.libraw_version)), "rawpy_features": rawpy.flags}
    if qVersion() != distribution("PySide6_Essentials").version:
        failures.append("Qt runtime does not match the reviewed PySide source version")
    if tuple(rawpy.libraw_version) != (0, 22, 1):
        failures.append("LibRaw runtime does not match the supplied 0.22.1 source snapshot")
    if rawpy.flags.get("DEMOSAIC_PACK_GPL2") or rawpy.flags.get("DEMOSAIC_PACK_GPL3"):
        failures.append("Optional GPL demosaic packs are enabled; this release configuration does not include them")
    upstream = ROOT / "packaging/licenses/upstream"
    upstream_manifest = {}
    try:
        upstream_manifest = json.loads((upstream / "manifest.json").read_text(encoding="utf-8"))
        for key, component in upstream_manifest["components"].items():
            entries = component.get("files", {})
            for relative, record in entries.items():
                path = upstream / key / relative
                if not path.is_file() or digest(path) != record["sha256"]:
                    failures.append(f"Upstream license integrity failure: {key}/{relative}")
            if key == "python":
                path = upstream / component["file"]
                if not path.is_file() or digest(path) != component["sha256"]:
                    failures.append("Python license integrity failure")
        shutil.copytree(upstream, output / "upstream", dirs_exist_ok=True)
    except (OSError, KeyError, ValueError) as exc:
        failures.append(f"Upstream license manifest unavailable: {exc}")

    for name in PACKAGES:
        dist = distribution(name)
        target = output / name
        target.mkdir(parents=True, exist_ok=True)
        files = []
        for entry in dist.files or ():
            relative = Path(str(entry))
            if ".." in relative.parts:
                continue
            # Include complete license directories, plus standalone notice files.
            is_license = LICENSE_NAME.search(relative.name) or any(p.lower() in {"licenses", "licences"} for p in relative.parts)
            source = Path(dist.locate_file(entry))
            if not is_license or not source.is_file():
                continue
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            files.append({"path": str(destination.relative_to(output)), "sha256": digest(destination)})
        metadata = dist.read_text("METADATA")
        if metadata:
            (target / "METADATA.txt").write_text(metadata, encoding="utf-8")
        record = {
            "version": dist.version,
            "declared_license": dist.metadata.get("License-Expression") or dist.metadata.get("License"),
            "project_urls": dist.metadata.get_all("Project-URL", []), "files": files,
        }
        if name in {"PySide6_Essentials", "shiboken6"}:
            required = ("pyside", "qtbase", "qttranslations") if name == "PySide6_Essentials" else ("pyside",)
            for key in required:
                component = upstream_manifest.get("components", {}).get(key, {})
                if component.get("tag") != f"v{dist.version}" or not component.get("files"):
                    failures.append(f"Need verified {key} license texts matching {name} {dist.version}")
            record["upstream_notices"] = list(required)
        elif not files:
            failures.append(f"No full license texts found in installed {name} {dist.version}")
        manifest["packages"][name] = record

    translation_component = upstream_manifest.get("components", {}).get("qttranslations", {})
    for path in ("LICENSES/BSD-3-Clause.txt", "LICENSES/GPL-3.0-only.txt",
                 "LICENSES/Qt-GPL-exception-1.0.txt", "translations/CMakeLists.txt",
                 "translations/README", "translations/split-qt-ts.pl", "licenseRule.json"):
        if path not in translation_component.get("files", {}):
            failures.append(f"Missing Qt translation license/evidence text: {path}")
    if (not re.fullmatch(r"[0-9a-f]{40}", translation_component.get("revision", ""))
            or not re.fullmatch(r"[0-9a-f]{64}", translation_component.get("source_archive_sha256", ""))):
        failures.append("Missing pinned Qt translation source revision/archive hash")
    translations = translation_component.get("translations", {})
    if translations.get("license_expression") != "BSD-3-Clause":
        failures.append("Qt translation catalog license review is missing or has changed")
    translation_root = Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath))
    manifest["translation_catalogs"] = {}
    for filename in ("qtbase_zh_CN.qm", "qtbase_zh_TW.qm"):
        source = translations.get("catalogs", {}).get(filename, {})
        binary = translation_root / filename
        expected_source = "translations/" + filename.removesuffix(".qm") + ".ts"
        if source.get("source") != expected_source or not re.fullmatch(r"[0-9a-f]{64}", source.get("source_sha256", "")):
            failures.append(f"Missing source provenance for Qt translation catalog: {filename}")
        if not binary.is_file():
            failures.append(f"Missing Qt translation catalog: {filename}")
            continue
        manifest["translation_catalogs"][filename] = {
            "qt_version": qVersion(), "sha256": digest(binary),
            "source": source.get("source"), "source_sha256": source.get("source_sha256"),
            "source_component": "qttranslations", "license_expression": translations.get("license_expression"),
        }

    # An installed Python distribution normally has its full license locally.
    candidates = [Path(sys.base_prefix) / "LICENSE.txt", Path(sys.base_prefix) / "LICENSE", Path(sys.base_prefix) / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "LICENSE.txt"]
    python_license = next((path for path in candidates if path.is_file()), None)
    if python_license:
        shutil.copy2(python_license, output / "Python-LICENSE.txt")
    elif upstream_manifest.get("components", {}).get("python", {}).get("version") == platform.python_version():
        shutil.copy2(upstream / "Python-LICENSE.txt", output / "Python-LICENSE.txt")
    else:
        failures.append("Full license for this Python version is missing; refresh upstream notices with scripts/fetch_upstream_licenses.py")
    manifest["license_texts_complete"] = not failures
    manifest["redistribution_review_required"] = True
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    shutil.copy2(ROOT / "THIRD_PARTY_NOTICES.md", output / "THIRD_PARTY_NOTICES.md")
    if failures:
        print("\n".join(f"LICENSE CHECK: {issue}" for issue in failures), file=sys.stderr)
        if not allow_incomplete:
            raise RuntimeError("Missing license material. Build stopped; use --allow-incomplete only for development smoke tests.")
        (output / "INCOMPLETE-DEVELOPMENT-ONLY.txt").write_text("\n".join(failures) + "\n", encoding="utf-8")
    else:
        (output / "INCOMPLETE-DEVELOPMENT-ONLY.txt").unlink(missing_ok=True)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "build/licenses")
    parser.add_argument("--allow-incomplete", action="store_true")
    args = parser.parse_args()
    collect(args.output.resolve(), args.allow_incomplete)
    print(f"License material copied to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
