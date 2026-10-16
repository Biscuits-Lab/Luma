# Build and test Luma

## Supported build path

Use 64-bit Windows and Python 3.12 to create the Windows package. All runtime dependencies are pinned. `scripts/build_windows.ps1` creates `.venv-build` inside the checkout, installs official binary wheels, runs tests using the Windows Qt platform plugin, copies full license texts, invokes PyInstaller in folder mode and checks the output inventory. It does not install the application or alter file associations/system settings.

```powershell
.\scripts\build_windows.ps1
# Equivalent double-click entry point: 打包.bat
```

Use `-SkipTests` only for diagnosis, never as release evidence. `-DevelopmentOnly` permits an incomplete notice collection, marks the result and prohibits treating it as a redistributable release. Do not use it for release workflow builds.

The full bundle is `dist\Luma`; the executable is `dist\Luma\Luma.exe`. `_internal` is required. Keep native DLLs external and replaceable. UPX and one-file mode are disabled. Qt imports/plugins are limited to the Widgets application; WebEngine, Multimedia and QML are excluded. The PNG/ICO icon is a normal replaceable asset.

## Source preview

Double-click `预览.bat` or run `scripts/preview.ps1`. The preview script creates `.venv`, installs pinned dependencies only when requirements change, and starts `python -m luma`. You may supply an image or directory path. The first setup needs network access. Both batch launchers keep errors visible; neither needs administrator rights.

## Linux development smoke

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest
bash scripts/build_linux_smoke.sh
QT_QPA_PLATFORM=offscreen dist/Luma/Luma --smoke-test
```

This creates a **Linux development bundle**. It cannot produce a Windows EXE. Offscreen rendering is useful for regression testing but does not certify Windows dialogs, drag/drop, display scaling, fullscreen or shell integration. A native interactive Linux session additionally needs Qt's OS libraries.

## License collection

`scripts/collect_licenses.py` reads installed distribution metadata, copies full notice files and bundled-codec licenses, adds the Python license, verifies the checked-in Qt source notice hashes, and writes a versioned manifest. Missing/changed texts fail the normal build. The raw metadata is included as provenance; an SPDX name is never substituted for full license text.

The `packaging/licenses/upstream` directory fills the PySide/Shiboken wheels' missing notices using official Qt Base and Qt for Python source snapshots. To intentionally refresh after changing pinned Qt/Python versions:

```sh
python scripts/fetch_upstream_licenses.py --qt-version 6.12.0 --python-version 3.12.14
```

This downloads source archives, copies license/attribution files, and records source revisions and SHA-256 hashes. Review the resulting changes before publishing. A different Windows Python patch release normally supplies its matching `LICENSE.txt` locally; the collector prefers that file. Full source fulfillment is a separate step from notice collection.

## Public Windows release workflow

`.github/workflows/windows-release.yml` uses standard `windows-latest`, not a larger/paid runner. It is bounded to the initial `0.1.0` release: only an intentional manual dispatch can retry that same release; automatic push/tag publication is disabled now that the initial release is complete. An already-published complete release is not overwritten. Future versions require an intentional workflow/version change.

The Windows job installs pinned wheels, runs native tests before packaging, builds the folder, runs the bundled EXE against generated format fixtures, creates a conventional ZIP with `Luma/Luma.exe`, and prepares the dependency-source material. Checksums cover both ZIP assets. Only the release job gets `contents: write`; build/test runs use read-only repository permissions. No workflow runs on a pull request with write privileges.

Published assets:

- `Luma-0.1.0-windows-x64.zip`
- `Luma-0.1.0-dependency-sources.zip`
- `SHA256SUMS`

The updater expects the exact Windows ZIP name and reads its SHA-256 from `SHA256SUMS`. Keep the ZIP ordinary stored/deflated with a single `Luma` folder, without symlinks or special filesystem entries.

## Current development candidate

`.github/workflows/thumbnail-validation.yml` validates the `main`
branch as version 0.1.3. It never publishes a release. The exact commit must pass
viewer/decoder regressions, 200% Qt scaling tests, native bridge tests, packaged
viewer smoke (including a full 8192-square Unicode EXR), real required-surrogate
extraction (including 4K/8K/16K HDR/EXR, EXR color/alpha probes and
Applications\Luma.exe association lookup), and installer lifecycle checks.
The candidate artifact contains setup EXE, portable ZIP, dependency-source ZIP,
SHA256SUMS and diagnostic evidence. Publication is a separate, intentional step
using those exact tested bytes; existing version tags/assets must not move.

The Linux measurements in [PERFORMANCE.md](PERFORMANCE.md) do not substitute for
Windows validation. DWM corner requests have unit coverage, but actual Windows 11
corner appearance/snap behavior still needs interactive visual checking.

## Release acceptance checklist

Automated checks are necessary but not a substitute for manual native UI validation:

- Open/drop a file and a folder; select thumbnails; navigate repeatedly while loads are pending
- Zoom/fit/100%, pan, fullscreen, resize and common Windows DPI settings
- Inspect alpha/R/G/B, reset display, rotate, adjust exposure/tone mapping and confirm the source file is unchanged
- Copy the display, Save As an explicit SDR copy, and test overwrite/cancel handling
- Confirm recycle-bin deletion, cancellation and unavailable-recycle handling; no permanent fallback
- Verify the compact custom Windows title bar, resize/maximize/restore/close and hidden-by-default filmstrip
- Exercise JPEG/PNG/TIFF/TGA/DDS, Radiance HDR, flat EXR and licensed real RAW camera examples; record actual coverage
- Test malformed/unsupported/large images and missing/inaccessible paths without stale-image or UI-state confusion
- Extract the release ZIP to a fresh writable directory and launch there without Python installed
- Verify the icon and all runtime components, read licenses/source guidance, and retain source artifacts with the release
- Test update download/checksum/extraction/launch separately; unsigned application warnings are an expected platform concern

The current validation report and CI run determine what has actually passed. Do not label a pending Windows build as a verified release merely because Linux tests passed.
