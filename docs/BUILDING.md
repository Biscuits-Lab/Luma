# Build and test Luma

## Supported build path

Use 64-bit Windows and Python 3.12 to create the Windows package. All runtime dependencies are pinned. `scripts/build_windows.ps1` creates `.venv-build` inside the checkout, installs official binary wheels, runs tests using the Windows Qt platform plugin, copies full license texts, invokes PyInstaller in folder mode and checks the output inventory. It does not install the application or alter file associations/system settings.

```powershell
.\scripts\build_windows.ps1
# Equivalent double-click entry point: 打包.bat
```

Use `-SkipTests` only for diagnosis, never as release evidence. `-DevelopmentOnly` permits an incomplete notice collection, marks the result and prohibits treating it as a redistributable release. Do not use it for release workflow builds.

The PyInstaller stage uses a process-local PATH containing only the build
environment, its base Python runtime and Windows system directories. This keeps
unrelated developer tools from supplying incompatible DLLs during dependency
analysis (notably a Poppler ICU DLL in place of Windows' ICU interface). The
original PATH is restored afterward; no user or system environment is changed.

The full bundle is `dist\Luma`; the executable is `dist\Luma\Luma.exe`. `_internal` is required. Keep native DLLs external and replaceable. UPX and one-file mode are disabled. Qt imports/plugins are limited to the Widgets application; WebEngine, Multimedia and QML are excluded. The PNG/ICO icon is a normal replaceable asset.

## Source preview

Double-click `预览.bat` or run `scripts/preview.ps1`. The preview script creates `.venv`, installs pinned dependencies when requirements change or import validation fails, and starts `python -m luma`. You may supply an image or directory path. The first setup needs network access. Both batch launchers keep errors visible; neither needs administrator rights.

The preview launcher first reuses a compatible project `.venv`, `.venv-preview`,
or `.venv-build`, then discovers installed 64-bit Python >=3.12 using the Python
launcher and PATH. It validates all pinned runtime imports before launching.
An unusable existing `.venv` is preserved; setup can use `.venv-preview` instead.
`powershell -NoProfile -File scripts/preview.ps1 -CheckOnly` prepares/checks the
project environment and exits without opening a GUI. It never installs a system
Python or opens the Store. Python 3.12 remains the packaging baseline.

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

Managed updates select the exact `Luma-<version>-windows-x64-setup.exe` and verify its SHA-256 against `SHA256SUMS`. The portable ZIP remains available for manual downloads and legacy compatibility. Keep it ordinary stored/deflated with a single `Luma` folder, without symlinks or special filesystem entries.

## Current development candidate

`.github/workflows/thumbnail-validation.yml` preserves the Windows validation
procedure for version 0.1.4. At the owner's request, this primary validation
workflow runs on pushes to `main` and also supports manual dispatch. Historical
diagnostic and release-publisher workflows remain manual-only. Local Linux
checks do not replace native Windows acceptance.
This validation definition never publishes a release. The exact commit must pass
viewer/decoder regressions, 200% Qt scaling tests, native bridge tests, packaged
viewer smoke (including a full 8192-square Unicode EXR), real required-surrogate
extraction (including 4K/8K/16K HDR/EXR, EXR color/alpha probes and
Applications\Luma.exe association lookup), and installer lifecycle checks, followed by production-helper Setup apply and actual frozen GUI relaunch identity verification.
The candidate artifact contains setup EXE, portable ZIP, dependency-source ZIP,
SHA256SUMS and diagnostic evidence. Publication is a separate, intentional step
using those exact tested bytes; existing version tags/assets must not move.

The Linux measurements in [PERFORMANCE.md](PERFORMANCE.md) do not substitute for
Windows validation. DWM corner requests have unit coverage, but actual Windows 11
corner appearance/snap behavior still needs interactive visual checking.

## Update and upgrade policy

The updater must distinguish a managed Setup installation from a portable ZIP
folder. Downloading and extracting another copy is **not** an upgrade of the
original executable, its shortcuts, or its Explorer registration.

### Existing 0.1.2 / 0.1.3 copies

The old updater places a separate copy under the user's local application-data
`updates` directory. Opening the original `Luma.exe` or an existing shortcut can
therefore still open the old version. A newly downloaded release cannot change
the updater already running inside an older executable.

An installed 0.1.3 copy needs **one manual Setup upgrade** to the first release
containing the corrected updater. Download the matching Windows Setup from the
[official releases page](https://github.com/Biscuits-Lab/Luma/releases), verify it
against that release's `SHA256SUMS`, close Luma, and run Setup in the existing
installation directory. Afterward, reopen the normal shortcut and verify the
version shown in Settings. Do not treat launching a temporary downloaded copy as
evidence that the original installation was upgraded.

### Managed Setup installations

The corrected updater uses the matching verified Setup executable and the
validated original installation directory. Setup owns installation and shortcut
changes; application payloads remain versioned so a DLL already loaded by
Explorer is not overwritten. The installation root contains a fixed native `Luma.exe`; shortcuts, Open with
commands and verified update restarts use that entry. A validated atomic pointer
selects the versioned payload, while a transaction journal binds pointer changes
to owned registry changes. Unknown or modified ownership fails closed.
See [launcher protocol](../native/launcher/README.md). A successful installer exit alone is insufficient:
the new application must confirm its actual embedded version, executable path,
and process ID before the old application closes. Cancellation, failed Setup,
or failed startup confirmation must not report a completed update.

### Portable ZIP folders

Portable updates are deliberately manual. Settings can check the official
release and open its page, but must not offer an automatic install or describe a
separate extracted copy as an installed update. The original portable
`Luma.exe` stays at its existing version and location until the user deliberately
changes how they launch Luma.

For a new portable version, download and verify the matching ZIP and extract the
**whole bundle into a new, empty folder**. Launch that folder's `Luma.exe`, check
its Settings version, then explicitly change any shortcuts used to open it.
Keep the old folder until the new copy has been checked. Do not extract over the
old folder, delete its contents, or copy only `Luma.exe`: `_internal` and native
DLLs belong to the same build, and the folder may also contain user images,
settings, modified dependencies, or unrelated files.

Setup is an alternative when a managed installation is desired. It must not
silently take over an Explorer registration owned by a different portable copy.
If an ownership conflict is reported, preserve both copies and rerun interactive Setup and review the explicit previous-owner/new-owner paths.
Only intact, fully validated Luma-owned registration can be transferred after an
explicit confirmation; canceling leaves that owner unchanged. Neither
the updater nor a recovery script may unregister or overwrite that owner's DLL
as a fallback. A portable copy without trustworthy ownership information is not
eligible for in-place replacement.

### Native Windows update acceptance

In addition to unit tests, validate the actual packaged build on Windows:

- Manually upgrade an installed 0.1.3 copy in a custom directory, then reopen its
  ordinary shortcut and confirm the new embedded version
- Update that corrected managed copy again; test Setup cancellation, failure,
  concurrent attempts, startup failure, and successful startup confirmation
- Keep Explorer thumbnails active during the managed upgrade and verify loaded
  old payload DLLs and a different portable registration are preserved
- Check a portable copy containing extra images, settings, and unrelated files;
  repeat Check, close/reopen Settings, and verify that no automatic install or
  launch action appears and every original file remains unchanged
- Open the offered official release link, extract a verified ZIP into a new
  folder, and confirm that reopening the original portable entry still reports
  its old version until its launch path is deliberately changed

Linux/offscreen tests cover policy and failure branches only. They do not prove
Windows process locking, Explorer DLL lifetime, installer behavior, shortcut
resolution, or native startup acknowledgment. Do not ship an in-place portable
apply/rollback helper without separate native tests establishing those safety
properties.

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
- Test update download/checksum, original-root Setup apply, real process/version acknowledgment, and reopen through updated shortcuts; unsigned application warnings are an expected platform concern

The current validation report and CI run determine what has actually passed. Do not label a pending Windows build as a verified release merely because Linux tests passed.
