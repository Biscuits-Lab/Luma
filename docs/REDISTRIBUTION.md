# Redistribution and library replacement

This document accompanies the portable folder bundle. It is technical guidance and a release checklist, not a statement that every legal obligation has automatically been satisfied.

## Included notices and source material

The application uses the LGPL options for Qt/PySide/Shiboken and LibRaw. Full applicable upstream license texts and copyright/third-party notices are in `_internal/licenses`. Keep them with the application.

The release workflow publishes `Luma-<version>-dependency-sources.zip` alongside the Windows package. It contains exact-version Qt Base and Qt for Python source snapshots and rawpy source with its exact LibRaw and LibRaw-cmake submodule revisions, plus provenance, hashes and build references. The distributor hosts these artifacts in the same public release, rather than merely referring users to an upstream site. Keep them available with binary releases and satisfy any further obligations of the relevant licenses. If these libraries are modified, include those modifications and the corresponding build information as well. A release without its required source material must not be treated as ready for distribution.

Runtime inventories vary by platform. Check the final binary inventory for any added libraries, platform runtimes or components outside this list, and include their required notices/source. Optional GPL demosaic packs, Qt WebEngine, QML and Qt Multimedia are not features of this build. Repository-wide upstream notice files can mention optional components that are not shipped.

## Replacing Qt / PySide / Shiboken

1. Exit Luma and make a backup of the complete application folder
2. Obtain or build a compatible modified Qt/PySide/Shiboken set for the same OS, CPU architecture, Python ABI and Qt minor version; use the matching source material and upstream build instructions
3. Replace the corresponding native libraries/extensions under `Luma/_internal/PySide6`, `Luma/_internal/shiboken6` and any matching top-level runtime libraries; preserve the folder layout and Qt platform plugin paths
4. Keep the binding libraries, Qt libraries and platform plugins ABI-compatible with each other; mismatched libraries can prevent startup
5. Launch `Luma.exe` from the writable folder. Luma adds no component signature check or anti-replacement lock. To roll back, restore the backed-up folder

If direct replacement is unsuitable, rebuild the application with the supplied `Luma.spec` and build script against the compatible modified dependencies. The application is folder-based, not statically linked to Qt and not a one-file self-extractor.

## Replacing rawpy / LibRaw

Exit Luma and back up the bundle. A wheel may expose LibRaw as a separate shared library alongside the `rawpy` extension. Replace it with a compatible rebuilt library, keeping its filename and directory. If rebuilding the binding is needed, build rawpy using the matching source tree and its LibRaw/LibRaw-cmake submodules, then replace the matching `rawpy` extension and its shared libraries together. Preserve Python ABI and architecture. The source tree's `setup.py`, `pyproject.toml` and upstream CI configuration describe the build; do not enable optional GPL demosaic packs unless their licensing is separately handled.

## Recipient rights

A distributor must not forbid recipients from replacing/relinking the LGPL components or reverse engineering the application as needed to debug those modifications. Do not add a signature policy, agreement or distribution restriction that removes applicable LGPL rights. Instructions and source material must be sufficient for recipients to build, install and run compatible modified libraries. The LGPL text controls the exact requirements.

## Before publishing a release

- Run tests and packaged smoke checks on native 64-bit Windows; inspect the actual ZIP contents and launch it after extracting to a new path
- Keep full license texts, copyright notices, the source/provenance asset and this guide available with the binary
- Check the exact wheel/native runtime versions, source snapshots, submodule revisions and any distributor modifications
- Audit additional native libraries and platform runtime distribution rights; the inventory is evidence, not a license verdict
- Preserve library replacement and debugging rights; source licensing decisions for Luma itself remain the owner's decision
- State tested formats and known limitations accurately, especially real RAW camera coverage and SDR tone mapping
- Do not imply code signing: these portable executables are unsigned unless a later release explicitly adds signing

Official guidance: [Qt's LGPL obligations](https://www.qt.io/development/open-source-lgpl-obligations), [Qt licensing FAQ](https://www.qt.io/faq/qt-open-source-licensing), [Qt for Python build instructions](https://doc.qt.io/qtforpython-6/building_from_source/index.html), [rawpy source/build notes](https://github.com/letmaik/rawpy), [LibRaw source](https://github.com/LibRaw/LibRaw).
