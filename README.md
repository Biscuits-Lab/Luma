# Luma

A focused, dark desktop image viewer for photography, rendered images and production textures. Built with Python and Qt Widgets, without a browser runtime.

## What it does

- Open an image or folder; drop files or folders into the window
- Browse neighboring images and a thumbnail filmstrip
- Fit at 100%, zoom relative to the fitted size, view actual pixels, pan and enter fullscreen
- Rotate the display, copy the displayed image, or explicitly save an SDR copy
- Move a selected file to the recycle bin after confirmation
- Inspect image information and RGBA channels
- View JPEG, PNG, TIFF, WebP, BMP, GIF (first frame), TGA and supported DDS textures
- Decode Radiance HDR and flat OpenEXR, with exposure and display tone mapping
- Develop camera RAW through LibRaw/rawpy, rather than showing only an embedded JPEG

**HDR here means reading high-dynamic-range files and tone-mapping them for the ordinary desktop. This version does not enable native Windows HDR output or drive an HDR display pipeline.**

Opening, browsing and display rotation do not change the original file. Copy/Save As are explicit actions; Save As creates a display-rendered, tone-mapped SDR copy rather than preserving scene-linear HDR/RAW data. Recycle-bin deletion asks for confirmation and never falls back to permanent deletion. Luma is a viewer rather than a full photo/RAW editor. Camera and texture support depends on the decoder and variant; see [format details](docs/FORMATS.md).

## Windows 快速开始

- 双击 `预览.bat`：首次创建项目内虚拟环境并安装依赖，然后打开 Luma
- 双击 `打包.bat`：在 Windows 上测试并生成 `dist\Luma\Luma.exe`
- 首次运行需要安装 64 位 Python 3.12，并联网下载依赖；不会安装到系统目录

## Run from source

Use 64-bit Python 3.12 or newer on a platform for which the pinned packages provide wheels. Python 3.12 is the baseline build interpreter. From this folder on Windows:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m luma
# Optional: open a file or folder at launch
.\.venv\Scripts\python.exe -m luma "C:\Pictures"
```

On Linux, use `python3 -m venv .venv`, `.venv/bin/python -m pip install -r requirements.txt`, then `.venv/bin/python -m luma`. A graphical session and Qt's system libraries are needed for an interactive window. The Linux path is for development and smoke validation, not a substitute for Windows testing.

## Build a Windows folder bundle

Run this **on a 64-bit Windows machine** with Python 3.12 installed:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\build_windows.ps1
```

This creates a project-local virtual environment, runs tests, collects full dependency license texts and builds `dist\Luma\Luma.exe`. Keep and distribute the entire `Luma` folder, including `_internal`; the EXE alone cannot run. The command's execution-policy override applies only to that PowerShell process. It does not install an application, change file associations, enable startup tasks, or modify a system policy.

PyInstaller builds for its host OS. A Linux smoke bundle is **not** a Windows EXE. The candidate workflow uses the standard GitHub-hosted Windows runner to test and package both a portable ZIP and a current-user Windows installer. See [installer details](packaging/windows/README.md). It does not publish a release automatically. Native Windows launch, icons, file dialogs, drag/drop and decoder verification remain mandatory before calling a package a Windows release. See [build and test instructions](docs/BUILDING.md).

## Tests

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
$env:QT_QPA_PLATFORM = "offscreen"
.\.venv\Scripts\python.exe -m pytest
Remove-Item Env:QT_QPA_PLATFORM
```

Offscreen tests can exercise logic and widgets; they do not certify native Windows behavior. Decoder regression coverage is described in [FORMATS.md](docs/FORMATS.md). A real camera RAW fixture and a camera-model matrix are separate from unit tests that mock the LibRaw interface.

## Interface

Use the ellipsis menu at the upper left for Save As, Copy, Open, and Settings. Rotation and confirmed recycle-bin deletion have adjacent buttons. The compact Windows title bar keeps the filename centered. Information and the thumbnail filmstrip start hidden. The lower-left controls show the filmstrip toggle, compact format/dimensions/file-size details, and then the information toggle. The 34 px title bar shares the canvas’s neutral dark gray background; the 36 px bottom bar is slightly lighter, without dividing lines. Zoom and fullscreen controls are at the lower right.

## Explorer thumbnails

The Windows installer prepares Luma-owned thumbnail handlers automatically. A packaged portable copy performs a safe background registration check on ordinary first launch. Choose Luma yourself in Windows Default Apps; no extra thumbnail checkbox is needed. Settings offers status and repair if a bundle moved or registration conflicts. Existing unrelated providers/default choices are preserved. See [supported limits and troubleshooting](docs/EXPLORER_THUMBNAILS.md). Source/development runs and headless worker/smoke modes never auto-register.

## Keyboard shortcuts

Ctrl+O opens an image; Ctrl+Shift+O opens a folder. Left/Right browse, Home/End jump to the first/last item. F fits, 1 shows actual pixels, +/− or the wheel zooms, drag pans, and double-click toggles fit/actual pixels. I toggles information, T toggles thumbnails, F11 toggles fullscreen, Esc leaves fullscreen/cancels work, Ctrl+0 resets the display, and Ctrl+Q quits.

## Memory use

Decoded navigation caching is bounded to 256 MiB, display-ready RGBA caching to 128 MiB, and filmstrip caching to 8 MiB. The active decoded image, Qt display pixels and temporary decoding/rendering arrays need additional memory. One bounded idle prefetch runs on the same worker for eligible JPEG/PNG/WebP images; foreground requests take priority and obsolete cooperative work stops. HDR/EXR/RAW are never speculatively decoded. Display transforms run in strips. See [measured performance and reproduction](docs/PERFORMANCE.md).

## Packaging and licensing

The build is intentionally folder-based: Qt, Python and native decoder components remain accessible rather than being sealed into a one-file self-extractor. License and replacement/source guidance is included in the bundle. Read [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) and the [redistribution checklist](docs/REDISTRIBUTION.md) before distributing a binary.

No open-source license has been selected for Luma's original application code. Dependency licenses do not choose a license for this source. The approved icon lives in `luma/assets/icon.png`; replace it and regenerate `icon.ico` when changing branding.
