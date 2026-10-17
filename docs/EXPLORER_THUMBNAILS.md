# Explorer thumbnails: architecture and validation

This describes the current development candidate, including automatic setup and
bounded large-HDR/EXR previews. Earlier Windows validation of small fixtures does
not certify these changes. Required-surrogate extraction, installer lifecycle
checks and the enlarged fixture set must pass for the exact candidate before
distribution. CI API coverage does not replace manual Explorer checks.

## Activation and ownership

A file association selects the application that opens a file; a Shell thumbnail
handler supplies Explorer previews. Windows setup prepares Luma's owned handlers
automatically. A portable packaged Windows copy makes one background startup
check, registering a fresh copy or completing its own older registration.
Source/development, headless thumbnail and private smoke-test modes never do
this. The user's own selection of Luma in Windows Default Apps remains separate.
There is no additional thumbnail checkbox or Windows integration section in
Luma Settings. Use Windows Settings itself to choose the default image viewer.

Registration is per-user, x64-only, and scoped to Luma-owned CLSIDs/ProgIDs,
Applications\\Luma.exe and OpenWith/capabilities entries. Never write UserChoice, its hash, extension defaults,
HKLM, or DisableProcessIsolation. Do not replace another application's thumbnail
handler. Common raster formats should retain the effective existing provider or
Windows PhotoThumbnailProvider.

Per-format Luma.Image.* associations preserve effective common-image providers,
queried through Windows' association API. Open With can instead select
Applications\\Luma.exe; that owned route has a content-aware handler for
HDR/EXR/raster/RAW streams. It does not change unrelated extension/provider keys.
The read-only registration status API checks effective .hdr and .exr providers separately rather than
treating a successful registration receipt as proof that Windows selects either one.

Automatic setup refuses another portable copy's ownership or externally modified
values. Installer upgrades permit only validated sibling payloads belonging to
the same installation. Every pending journal snapshot must pass that owner check
before recovery. The explicit registration API can move this copy's registration;
conflicting third-party values are still preserved. Full uninstallation removes
only unchanged values owned by the application.

One bounded installer migration handles Windows' manual Browse/Open With entry:
an otherwise unowned Applications\\Luma.exe open command may be adopted only if
it is the exact conventional quoted command for an explicitly validated prior
payload in the same installation. No extra arguments or unrelated paths qualify.
Its original bytes are retained only in a narrowly scoped version-2 pending
journal for failure rollback; the successful version-1 receipt owns the command
and uninstall removes it. Interrupted adoption must be repaired before uninstall,
so cleanup cannot leave a command pointing to a payload it is about to delete.

Uninstall compares every value with the value Luma wrote before restoring it.
Later user/third-party changes are left untouched. No recursive deletion of shared
registry keys. Notify association changes; do not restart Explorer or clear the
user's thumbnail cache. Per-user COM may not activate in elevated applications.
Portable registration depends on the bundle's absolute location; keep that
folder in place, or restore its original location if it was moved. Installed
upgrades move the owned registration to the
new version while retaining old payloads. Automatic setup failure is visible and
does not prevent image viewing.

## Process architecture

Four native COM classes map content-aware raster, Radiance HDR, flat EXR and camera RAW to
IThumbnailProvider + IInitializeWithStream. Initialize retains only the supplied
stream; the handler never reopens a filename obtained from IStream::Stat.

Windows' default thumbnail surrogate isolation stays enabled. A small native DLL
launches the adjacent, known Luma.exe in headless mode. No CPython or Qt is loaded
into the COM DLL. The frozen entry point dispatches the worker before importing
GUI modules. No shell command, elevated broker, job breakaway, or persistent
service is used.

The worker is created suspended and must join a job successfully before resuming.
Limits: one process, 768 MiB committed process memory, kill-on-job-close, ten
seconds for the worker. Failure to establish restrictions fails the extraction.
A job is resource control, not a complete sandbox.

Path-only decoders require a bounded temporary copy in a unique LocalAppDataLow
request directory. Input/output files are pre-created and pinned against
rename/delete. Only these known files are cleaned up. Maximum compressed input:
256 MiB. Maximum requested output: 1024 pixels on either side. The protocol is
16-byte header (LUMATHM1 + uint32 width/height) and exact straight RGBA8 bytes.
The native side independently validates size/dimensions and creates straight
BGRA HBITMAP output, matching Microsoft's thumbnail-provider sample. File I/O avoids blocking pipes before the timed process wait.

An arbitrary incoming IStream::Read may block. A decoder deadline is therefore
not an absolute timeout for the entire COM call; Windows' surrogate timeout also
matters. Do not claim otherwise.

## Image semantics

- Explorer uses Windows' own persistent thumbnail cache, not another Luma cache
- Common raster files use first frame/page; EXIF orientation, alpha and bounded
  ICC handling are preserved
- HDR/EXR thumbnails use SDR tone mapping, not native HDR-monitor output
- Radiance thumbnails validate every encoded scanline, retaining only one
  scanline and a pixel-center-sampled linear float preview. Tone mapping happens
  at preview resolution. This deliberately trades fine-detail reconstruction
  for bounded memory; the interactive viewer retains full-resolution precision
- Flat EXR thumbnails read only the chosen RGB/Y/scalar layer and alpha through
  the pinned official OpenEXR selected-channel scanline adapter. They retain
  bounded strips and a pixel-center-sampled preview, then tone-map that preview.
  Compressed chunks still need decoding; this is not a promise to read only the
  output pixels. Tiled files use the official adapter, not custom decompression
- RAW thumbnails prefer embedded JPEG/bitmap previews for speed. A missing preview
  may use bounded half-size LibRaw processing. The main viewer still develops RAW
  rather than substituting an embedded preview
- Thumbnail source limits: ordinary raster up to 67,108,864 pixels (8192 square),
  JPEG up to 128 MP, RAW sensor up to 64 MP; other full-float paths
  up to 16 MP; bounded Radiance HDR preview up to 256 MP; bounded flat EXR
  preview up to 268,435,456 pixels (16,384 squared). Every source axis
  is limited to 32,768 pixels. HDR/EXR thumbnails can exceed their interactive full-image pixel
  ceilings because no full-size float image is allocated. EXR additionally has
  header, channel-count, native compression-workspace and strip-memory guards. The unchanged 256 MiB
  compressed-input, 768 MiB process-memory and ten-second worker limits still
  apply. A flat 16K HDR larger than 256 MiB remains unsupported; a compact
  16,384 × 8,192 RLE HDR can produce a bounded preview. Actual success also
  depends on compression, machine performance and camera model
- Raster memory is checked before metadata calls that may load pixels. JPEG
  uses native reduced decoding before loading; progressive DCT coefficients are
  separately estimated, including 4:4:4 files. Raster decoded buffers are bounded
  to 256 MiB and progressive JPEG coefficients plus decoded pixels to 640 MiB.
  These allowances do not relax the native 768-MiB/ten-second/single-process job.
  The private helper bounds BLAS/OpenMP thread pools in its own process before
  NumPy import; it does not change the user's system environment.
- The in-app filmstrip shares the bounded raster decoder and no longer skips
  every image above 32 MP. Its existing output and cache budgets remain bounded.
- Unsupported variants or any failed limit return no thumbnail. Explorer can
  display the file icon. Do not hang or crash Explorer to force a preview

## Required Windows CI and manual checks

1. Compile x64 native DLL/tests with MSVC; test COM identity, ownership,
   repeated initialization, empty/truncated/oversized streams, absent helper,
   malformed protocol output and timeout/kill behavior
2. Run headless Python tests without importing Qt, plus all existing viewer tests
3. Build the real one-directory bundle. One-file bootloaders are incompatible
   with the single-process job limit
4. Exercise actual bundled thumbnail extraction for PNG/JPEG/TGA/DDS/HDR/flat
   EXR, one real Nikon NEF, Unicode/spaced filenames, and synthetic 4K/8K/16K
   RLE HDR sources. Preserve source hashes. Exercise both per-format and actual
   Applications\\Luma.exe required-surrogate lookup using disposable extensions
5. Test registry planning, conflict protection, rollback, uninstall/reinstall,
   moved bundles and preservation of third-party changes
6. Use IThumbnailCache with surrogate required for real out-of-process activation.
   Direct DllGetClassObject tests alone do not prove Explorer compatibility
7. Record shell/helper process restrictions and failures. If per-user activation
   or child launch is denied, report it; never disable isolation or elevate
8. Manual Explorer visual checks: thumbnail sizes, transparent images, scrolling,
   repeated loads/cache reuse, network/removable inputs, concurrent folder loads,
   crash/hang recovery and unregister behavior. CI API tests are not a visual
   Explorer test

## If .hdr or .exr still displays an icon

Check any automatic-setup failure notice and the selected Windows default app.
Restore a moved portable folder to its registered location; for an installed
copy, close Luma and rerun its installer to retry recovery of its own registration.
Setup cannot take ownership from another Luma copy or overwrite unrelated changes
just by retrying. Selecting an executable in Open With
can use a different association than the registered per-format Default Apps
entry. Reopen the folder after changing the default app. Explorer's own view and
thumbnail settings, cache, the file's variant, and the fixed worker limits also
affect extraction. Do not silently clear the user's thumbnail cache, restart
Explorer, change defaults, or disable process isolation to force a result.

The v0.1.1 full-float thumbnail path rejected every HDR over 16 MP before decoding,
including ordinary 8K environment maps. The bounded preview path removes that
specific limitation without increasing the native process/input limits.
The v0.1.2 EXR path still used that 16 MP full-float route; the current EXR
candidate removes it through selected-channel strips. Neither change guarantees
that every compression/layout or damaged file can produce a thumbnail.

## Official references

- https://learn.microsoft.com/en-us/windows/win32/shell/building-thumbnail-providers
- https://learn.microsoft.com/en-us/windows/win32/shell/thumbnail-providers
- https://learn.microsoft.com/en-us/windows/win32/api/thumbcache/nn-thumbcache-ithumbnailprovider
- https://learn.microsoft.com/en-us/windows/win32/api/thumbcache/nf-thumbcache-ithumbnailcache-getthumbnail
- https://learn.microsoft.com/en-us/windows/win32/shell/fa-associationarray
- https://learn.microsoft.com/en-us/windows/win32/api/shlwapi/nf-shlwapi-assocquerystringw
- https://learn.microsoft.com/en-us/windows/win32/api/shlwapi/ne-shlwapi-assocstr
- https://learn.microsoft.com/en-us/windows/win32/shell/assocf_str
- https://learn.microsoft.com/en-us/windows/win32/sysinfo/hkey-classes-root-key
- https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects
- https://learn.microsoft.com/en-us/windows/win32/secauthz/mandatory-integrity-control
