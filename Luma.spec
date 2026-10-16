# -*- mode: python ; coding: utf-8 -*-
"""Folder-mode bundle. Build on the operating system/architecture being shipped.
Qt and LibRaw remain external shared libraries so users can replace them.
"""
from importlib.metadata import distribution
from pathlib import Path
import re
import sys

from PyInstaller.utils.hooks import collect_dynamic_libs, copy_metadata

ROOT = Path(SPECPATH)
LICENSES = ROOT / "build" / "licenses"
if not (LICENSES / "manifest.json").is_file():
    raise SystemExit("Run scripts/collect_licenses.py --output build/licenses first.")

binaries = collect_dynamic_libs("rawpy")
datas = [
    (str(ROOT / "luma" / "assets"), "luma/assets"),
    (str(LICENSES), "licenses"),
    (str(ROOT / "THIRD_PARTY_NOTICES.md"), "."),
    (str(ROOT / "docs" / "REDISTRIBUTION.md"), "docs"),
]
# Built-in Qt file dialogs/message buttons need the matching Qt catalog too.
# Keep explicit application-local destinations; frozen Qt layouts vary by OS.
from PySide6.QtCore import QLibraryInfo
qt_translations = Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath))
for language in ("zh_CN", "zh_TW"):
    catalog = qt_translations / f"qtbase_{language}.qm"
    if not catalog.is_file():
        raise SystemExit(f"Missing required Qt UI translation: {catalog}")
    datas.append((str(catalog), "luma/translations"))

# Native wheels can place DLLs in sibling .libs directories, outside packages.
# Distribution RECORD metadata covers both delvewheel and auditwheel layouts,
# including rawpy/libraw and OpenEXR's non-package extension dependencies.
for name in ("rawpy", "OpenEXR", "numpy", "Pillow"):
    dist = distribution(name)
    for entry in dist.files or ():
        path = Path(str(entry))
        if ".." in path.parts or len(path.parts) < 2:
            continue
        if re.search(r"(?:\.dll|\.dylib|\.so(?:\.[\d.]+)?)$", path.name, re.I):
            binaries.append((str(dist.locate_file(entry)), str(path.parent)))
        elif path.name.startswith((".load-order", ".load_order")):
            datas.append((str(dist.locate_file(entry)), str(path.parent)))
    datas += copy_metadata(name)

# Importing only the required modules keeps WebEngine, Multimedia, QML, etc.
# out of this desktop raster viewer. Do not use collect_all('PySide6').
import PySide6
qt_needed = {"QtCore", "QtGui", "QtWidgets", "QtDBus"}
qt_excludes = [f"PySide6.{name}" for name in PySide6.__all__ if name.startswith("Qt") and name not in qt_needed]
a = Analysis(
    [str(ROOT / "scripts" / "frozen_entry.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=["rawpy", "rawpy._rawpy", "OpenEXR", "Imath", "PIL.DdsImagePlugin", "PIL.TgaImagePlugin"],
    hookspath=[str(ROOT / "packaging" / "hooks")],
    hooksconfig={},
    runtime_hooks=[],
    excludes=qt_excludes + ["tkinter", "PyQt5", "PyQt6", "PySide2", "pytest", "IPython", "matplotlib", "scipy"],
    noarchive=False,
)

def keep_qt_binary(entry):
    destination = entry[0].replace("\\", "/")
    lower = destination.lower()
    # Defense in depth: unused plugin dependencies must not leak into the bundle.
    library = re.match(r"(?:lib)?qt6([a-z0-9]+)(?:\.dll|\.so(?:\.|$)|\.dylib)", Path(lower).name)
    if library and library.group(1) not in {"core", "gui", "widgets", "dbus", "xcbqpa", "opengl"}:
        return False
    if lower.endswith("opengl32sw.dll"):
        return False  # Raster QWidgets only; no bundled Mesa OpenGL renderer.
    marker = "/plugins/"
    if marker not in lower or "pyside6" not in lower:
        return True
    category, _, filename = lower.split(marker, 1)[1].partition("/")
    if category == "platforms":
        allowed = ("qwindows.dll", "qoffscreen.dll") if sys.platform == "win32" else ("libqxcb.so", "libqoffscreen.so")
        return filename in allowed
    return category in {"styles", "platformthemes", "xcbglintegrations"}

a.binaries = [entry for entry in a.binaries if keep_qt_binary(entry)]
pyz = PYZ(a.pure)
icon = ROOT / "luma" / "assets" / "icon.ico"
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="Luma",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=sys.platform != "win32",
    disable_windowed_traceback=False,
    contents_directory="_internal",
    icon=str(icon) if sys.platform == "win32" and icon.is_file() else None,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="Luma")
