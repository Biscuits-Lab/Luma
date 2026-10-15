# Third-party notices

Luma uses Qt for Python (PySide6/Shiboken6) and Qt under their available LGPL terms, and relies on the native and Python libraries below. Their copyrights and full license terms remain with the respective authors. Luma's original application code has no selected open-source license; these dependency notices do not license it.

| Component | Pinned version | License / role |
|---|---:|---|
| PySide6 Essentials and Shiboken6 | 6.12.0 | LGPL-3.0 option; Python bindings and runtime |
| Qt Core, Gui, Widgets and platform dependencies | matching Qt runtime | LGPL-3.0 option plus bundled third-party licenses |
| Pillow | 12.3.0 | MIT-CMU plus bundled codec notices |
| NumPy | 2.5.4 | BSD and bundled numerical/runtime notices |
| OpenEXR | 3.5.2 | BSD-3-Clause; native EXR decoding |
| OpenEXR bundled codecs/bindings | OpenEXR 3.5.2 source; Imath 3.2.3; pybind11 2.13.6 | Full Imath, OpenJPH, libdeflate, Zstd and pybind11 BSD/MIT notices |
| rawpy | 0.27.1 | MIT; Python RAW binding |
| LibRaw, bundled by rawpy | inspect installed runtime | LGPL-2.1 option; RAW development |
| CPython | build interpreter | PSF/Python terms and historical notices |
| PyInstaller | 6.22.3 | GPL with bootloader/distribution exception; build tooling |

The authoritative versions, license declarations, full text paths and hashes for each build are in `_internal/licenses/manifest.json`. The collection contains full license files copied from the installed distributions, including Pillow's bundled codec and NumPy's numerical-library notices. Exact-source supplemental notices cover OpenEXR's statically bundled codecs/bindings, LibRaw copyright material, and CPython's documented OpenSSL/libffi/Expat/zlib notices. Qt/PySide notice gaps in wheels are filled by versioned official source-tree license texts and attributions, with source revisions and SHA-256 hashes in `_internal/licenses/upstream/manifest.json`. An upstream repository-wide notice collection may include components the minimal application does not ship.

Build tools and tests are not intended as runtime features. PyInstaller's bootloader exception allows applications to retain their own licensing subject to their dependencies. See its [official licensing statement](https://pyinstaller.org/en/stable/license.html).

## LGPL components and replacement

This folder bundle uses external native shared libraries. Recipients must retain the ability to use modified compatible LGPL components, including any reverse engineering needed to debug those modifications. Do not add a license term, technical lock or distribution condition that removes these rights. See [REDISTRIBUTION.md](docs/REDISTRIBUTION.md) for replacement instructions and the distributor's corresponding-source obligations.

Full license texts alone do not fulfill corresponding-source obligations. A distributor must provide complete corresponding source for the exact LGPL components and applicable modifications using a compliant fulfillment method under its control. An upstream homepage link is not, by itself, such a method. This repository provides source locations and build guidance, not an invented legal offer from its owner.

Official references:
- [Qt open-source obligations](https://www.qt.io/development/open-source-lgpl-obligations)
- [Qt licensing FAQ](https://www.qt.io/faq/qt-open-source-licensing)
- [Qt for Python license information](https://doc.qt.io/qtforpython-6/licenses.html)
- [Qt source archive](https://download.qt.io/official_releases/qt/)
- [Qt for Python source](https://code.qt.io/cgit/pyside/pyside-setup.git/)
- [LibRaw source and license information](https://www.libraw.org/about)
- [rawpy source](https://github.com/letmaik/rawpy)

Before external distribution, audit the actual platform bundle, including any Microsoft/runtime/system-derived DLLs and native libraries, and complete the release checklist. The tooling checks for notice files and hashes; it is not a legal compliance certification.
