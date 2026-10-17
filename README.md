# Luma

A focused, dark desktop image viewer for photography, rendered images and production textures. Built with Python and Qt Widgets, without a browser runtime.

## What it does

- Open an image or folder; drop files or folders into the window
- Browse neighboring images and a thumbnail filmstrip
- Fit at 100%, zoom relative to the fitted size, view actual pixels, pan and enter fullscreen
- Rotate the display, copy the displayed image, or explicitly save an SDR copy
- Edit a separate copy with Crop, Adjust, Filters and Markup; undo changes without changing the source
- Move a selected file to the recycle bin after confirmation
- Inspect image information and RGBA channels
- View JPEG, PNG, TIFF, WebP, BMP, GIF (first frame), TGA and supported DDS textures
- Decode Radiance HDR and flat OpenEXR, with exposure and display tone mapping
- Develop camera RAW through LibRaw/rawpy, rather than showing only an embedded JPEG

**HDR here means reading high-dynamic-range files and tone-mapping them for the ordinary desktop. This version does not enable native Windows HDR output or drive an HDR display pipeline.**

Opening, browsing and display rotation do not change the original file. Copy/Save As are explicit actions; Save As creates a display-rendered, tone-mapped SDR copy rather than preserving scene-linear HDR/RAW data. Recycle-bin deletion asks for confirmation and never falls back to permanent deletion. The editor works on the current 8-bit SDR display, not the original scene-linear HDR or RAW sensor data. See [editing scope and limits](docs/EDITING.md). Camera and texture support depends on the decoder and variant; see [format details](docs/FORMATS.md).

## Windows 快速开始

- 双击 `预览.bat`：首次创建项目内虚拟环境并安装依赖，然后打开 Luma
- 双击 `打包.bat`：在 Windows 上测试并生成 `dist\Luma\Luma.exe`
- 双击 `测试.bat`：复用现有环境，以 4 个独立离屏进程并行跑全部源码测试，不打包
- 首次运行需要已安装的 64 位 Python 3.12 或更新的兼容版本，并联网下载依赖；预览脚本优先复用项目虚拟环境，不会安装系统运行时
- 只检查运行环境、不打开窗口：`powershell -NoProfile -File .\scripts\preview.ps1 -CheckOnly`

## 更新与旧版本迁移

当前开发版修复了旧更新器仅解压另一份程序、原快捷方式仍指向旧版本的问题。安装目录第一层的固定 Luma.exe 入口选择当前版本，快捷方式和打开方式使用同一入口。安装版更新会校验官方 Setup，在原安装目录升级，并等待新版窗口确认真实进程路径和内置版本后再退出旧窗口。安装失败或无法确认启动时，旧窗口保持打开。

已有 0.1.2 / 0.1.3 用户需要先从 [Releases](https://github.com/Biscuits-Lab/Luma/releases) 手动安装一次包含此修复的安装包；新包无法修改正在运行的旧更新器。免安装版暂时仅提供手动更新入口，请将 ZIP 解压到新文件夹并更新自己的快捷方式，避免覆盖运行中的文件。详见 [更新与回滚说明](docs/BUILDING.md#update-and-upgrade-policy)。开发候选版本不等于已经发布的 Release。

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
# Daily source regression: all tests, parallel offscreen workers, no packaging
.\.venv\Scripts\python.exe scripts/run_tests.py
# Focus on the changed feature
.\.venv\Scripts\python.exe scripts/run_tests.py tests/test_editor_ui.py
# Include installer/release policy unit tests too
.\.venv\Scripts\python.exe scripts/run_tests.py --contracts
```

`测试.bat` discovers an existing project test environment; it does not install
dependencies on every run. Use `--jobs 1` for a serial comparison or a low-memory
machine. Each run writes worker logs, JUnit XML, and `report.json` under
`build/test-runs/run-*`. Timing history balances the work; every selected test
is executed again. Native Windows release validation remains in `打包.bat` and CI.

Offscreen tests can exercise logic and widgets; they do not certify native Windows behavior. Decoder regression coverage is described in [FORMATS.md](docs/FORMATS.md). A real camera RAW fixture and a camera-model matrix are separate from unit tests that mock the LibRaw interface.

## Interface

Use the ellipsis menu at the upper left for Save As, Copy, Open, and Settings. Rotation, local editing and confirmed recycle-bin deletion have adjacent buttons. The compact Windows title bar keeps the filename centered. Information and the thumbnail filmstrip start hidden. Details opens as a rounded inset panel beside the image; fit mode adapts to the remaining canvas. The lower-left controls show the filmstrip toggle, compact format/dimensions/file-size details, and then the information toggle. The 34 px title bar shares the canvas’s neutral dark gray background; the 36 px bottom bar is slightly lighter, without dividing lines. Zoom and fullscreen controls are at the lower right.

The interface follows the system display language: Simplified Chinese, Traditional Chinese, or English fallback. Settings, menus, tooltips, confirmations and errors are localized; filenames and original metadata are preserved. Restart Luma after changing the OS display language. See [language selection and UI verification](docs/LOCALIZATION.md).

## Explorer thumbnails

The Windows installer prepares Luma-owned thumbnail handlers automatically. A packaged portable copy performs a safe background registration check on ordinary first launch. Choose Luma yourself in Windows Default Apps; no extra thumbnail checkbox is needed. Settings does not expose Windows integration controls. Keep a registered portable folder in its original location; installed copies use Setup for registration recovery. Existing unrelated providers/default choices are preserved. See [supported limits and troubleshooting](docs/EXPLORER_THUMBNAILS.md). Source/development runs and headless worker/smoke modes never auto-register.

## Keyboard shortcuts

Ctrl+O opens an image; Ctrl+Shift+O opens a folder. Left/Right browse, Home/End jump to the first/last item. F fits, 1 shows actual pixels, +/− or the wheel zooms, drag pans, and double-click toggles fit/actual pixels. I toggles information, T toggles thumbnails, F11 toggles fullscreen, Esc leaves fullscreen/cancels work, Ctrl+0 resets the display, and Ctrl+Q quits.

## Memory use

Decoded navigation caching is bounded to 256 MiB, display-ready RGBA caching to 128 MiB, and filmstrip caching to 8 MiB. The active decoded image, Qt display pixels and temporary decoding/rendering arrays need additional memory. One bounded idle prefetch runs on the same worker for eligible JPEG/PNG/WebP images; foreground requests take priority and obsolete cooperative work stops. HDR/EXR/RAW are never speculatively decoded. Display transforms run in strips. See [measured performance and reproduction](docs/PERFORMANCE.md) and the
[EXR-specific measurements and limits](docs/EXR_PERFORMANCE.md).

## Packaging and licensing

The build is intentionally folder-based: Qt, Python and native decoder components remain accessible rather than being sealed into a one-file self-extractor. License and replacement/source guidance is included in the bundle. Read [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) and the [redistribution checklist](docs/REDISTRIBUTION.md) before distributing a binary.

No open-source license has been selected for Luma's original application code. Dependency licenses do not choose a license for this source. The approved icon lives in `luma/assets/icon.png`; replace it and regenerate `icon.ico` when changing branding.
