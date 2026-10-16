#!/usr/bin/env python3
"""Static sanity checks for a PyInstaller folder bundle; this is not UI testing."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys


def verify(bundle: Path) -> list[str]:
    failures = []
    internal = bundle / "_internal"
    executable = bundle / ("Luma.exe" if sys.platform == "win32" else "Luma")
    for path in (executable, internal / "luma/assets/icon.png", internal / "licenses/manifest.json", internal / "THIRD_PARTY_NOTICES.md", internal / "docs/REDISTRIBUTION.md"):
        if not path.is_file():
            failures.append(f"Missing required bundle file: {path}")
    if sys.platform == "win32" and not (bundle / "LumaThumbnail.dll").is_file():
        failures.append("Missing adjacent native Explorer thumbnail bridge")
    paths = [str(p.relative_to(bundle)).replace("\\", "/") for p in bundle.rglob("*") if p.is_file()]
    lower_paths = [p.lower() for p in paths]
    if any(any(name in p for name in ("webengine", "qt6qml", "qt6quick", "qt6pdf", "qt6virtualkeyboard", "qt6svg", "qt6multimedia")) and "/licenses/" not in p and
           (p.endswith((".exe", ".dll", ".pyd", ".py", ".pyc", ".so", ".dylib")) or ".so." in p)
           for p in lower_paths):
        failures.append("Unexpected non-Widgets Qt dependency in the bundle")
    for label, token in (("OpenEXR native module", "openexr"), ("rawpy native module", "_rawpy"), ("Qt Core dynamic library", "qt6core")):
        if not any(token in p and (p.endswith((".dll", ".pyd", ".dylib", ".so")) or ".so." in p) for p in lower_paths):
            failures.append(f"Missing {label}")
    platform_plugin = "qwindows.dll" if sys.platform == "win32" else "libqoffscreen.so"
    if not any(p.endswith("/platforms/" + platform_plugin) for p in lower_paths):
        failures.append(f"Missing Qt platform plugin: {platform_plugin}")
    manifest_file = internal / "licenses/manifest.json"
    if manifest_file.is_file():
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        if not manifest.get("license_texts_complete"):
            print("WARNING: incomplete development license collection; do not redistribute", file=sys.stderr)
    (bundle / "bundle-inventory.txt").write_text("\n".join(sorted(paths)) + "\n", encoding="utf-8")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    args = parser.parse_args()
    failures = verify(args.bundle.resolve())
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    print("Static bundle checks passed. Native Windows launch/format tests are still required for a Windows release.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
