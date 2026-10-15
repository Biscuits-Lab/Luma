## Luma 0.1.0

Portable 64-bit Windows image viewer. Download **Luma-0.1.0-windows-x64.zip**, extract the entire `Luma` folder, then run `Luma.exe`. Keep `_internal` beside the EXE. No installer or Python installation is required for the portable bundle.

- Open/drop images and folders, thumbnails and navigation
- Compact Windows title bar, hidden-by-default filmstrip, fit/100%, zoom/pan/fullscreen
- Metadata/RGBA inspection, non-destructive display rotation, clipboard copy and explicit SDR Save As
- Confirmed recycle-bin deletion, with no permanent-delete fallback
- Pillow raster formats including TGA and supported DDS variants
- Radiance HDR and flat OpenEXR with SDR display tone mapping
- Actual LibRaw camera RAW development
- Settings and explicit, checksum-verified portable updates

This release is published only after native Windows automated tests and packaged-executable smoke checks pass, including PNG, TGA, DDS, Radiance HDR, flat EXR, Unicode PNG and RAW filenames, and one real Nikon NEF. This does not certify every camera/encoding or replace manual testing of every Windows desktop/DPI configuration.

HDR files are tone-mapped to an ordinary desktop display; native HDR-monitor output is not implemented. GIF/multipage files are static views. Deep EXR and general AOV/compositing workflows are outside this viewer's supported model. The portable executable is unsigned, so Windows may display a reputation warning.

`SHA256SUMS` contains checksums for the Windows ZIP and matching dependency-source ZIP. The source ZIP hosts the LGPL component source material alongside the binary; full dependency notices and replacement guidance are inside the application bundle. Photographic RAW test samples are not included in the release.
