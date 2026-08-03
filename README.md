# Lumina Viewer

## Quick Preview

Run the bootstrap preview script from PowerShell:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/preview.ps1 -KeepOpen
```

The script checks Git, CMake, Visual Studio C++ Build Tools, LLVM, and `core-ui`. When a dependency is missing, it attempts to install it with `winget`, builds `core-ui`, builds Lumina Viewer, writes the packaged executable to `output\lumina-viewer.exe`, and launches the app.

Use `-KeepOpen` when double-clicking or launching from a temporary terminal so errors remain visible. Full logs are written to `output\preview.log`.

Useful options:

```powershell
# Rebuild everything local and launch
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/preview.ps1 -Clean -KeepOpen

# Build only, do not launch
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/preview.ps1 -NoRun -KeepOpen

# Open an image after launching
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/preview.ps1 -KeepOpen -Image "D:\\Pictures\\sample.nef"

# Use an existing core-ui checkout or SDK root
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/preview.ps1 -CoreUiRoot "D:\\libs\\core-ui" -KeepOpen

# Do not auto-install; fail if something is missing
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/preview.ps1 -SkipInstall -KeepOpen
```
Lumina Viewer is a lightweight Windows image previewer prototype. It is designed around a small native C++ core, a Fluent-style UI powered by [core-ui](https://github.com/ghboke/core-ui), and pluggable image decoders for common, RAW, and professional image formats.

## Goals

- Fast startup and low idle memory usage.
- Clean Windows 11 Photos-like preview UI.
- Mouse-wheel zoom and drag-to-pan image viewing.
- Left/right arrow navigation across images in the same folder.
- Bottom-right zoom slider synchronized with the current view.
- Top-center file name, bottom-left dimensions and file size.
- Decoder boundaries ready for WIC, LibRaw, and OpenEXR.

## Current Status

This repository currently contains the application skeleton, core-ui viewer shell, and a WIC decoder for common Windows image formats. RAW and EXR support are planned next.

## Planned Decoder Backends

- WIC implemented: `jpg`, `jpeg`, `png`, `bmp`, `gif`, `tif`, `tiff`, plus `webp`, `heic`, `heif`, `avif` when Windows has matching WIC codecs.
- LibRaw: `nef`, `cr2`, `cr3`, `arw`, `orf`, `rw2`, `dng`.
- OpenEXR: `exr`, `hdr` tone-mapped into displayable BGRA pixels.
- Optional fallback: `libvips` or ImageMagick for uncommon formats.

## Layout

```text
cmake/
  FindCoreUI.cmake
src/
  app/              Application shell and viewer controller
  image/            Image model, folder index, decoder interfaces
  platform/         Windows utility helpers
ui/
  main.uix          core-ui single-file UI mockup
docs/
  architecture.md   Implementation plan and integration notes
third_party/
  core-ui/          Optional local core-ui SDK drop location
```

## Build

Prerequisites:

- Visual Studio 2022/2026 Build Tools with the C++ desktop workload, or another MSVC-compatible C++20 toolchain.
- CMake 3.25+.
- A built core-ui SDK.

Install or build core-ui, then provide its SDK location to CMake:

```powershell
cmake -S . -B build -DCORE_UI_ROOT="D:\\path\\to\\core-ui-sdk"
cmake --build build --config Release
```

Expected SDK layout:

```text
core-ui-sdk/
  include/ui_core.h
  lib/core-ui.lib
  bin/core-ui.dll
```

The generated executable and runtime files are copied to `output/`. The preview script writes logs to `output/preview.log`.






