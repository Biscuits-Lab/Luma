# Explorer thumbnails: design and validation plan (draft)

This feature is not in the published v0.1.0 bundle. These drafts have not been
compiled or run. Do not distribute an Explorer extension until the Windows
checks below pass.

## Activation and ownership

A file association only selects the application that opens a file. A separate
Shell thumbnail handler supplies Explorer previews. Enabling integration must be
an explicit Settings action, followed by the user's own choice of Luma in Windows
Default Apps. Opening a photo must never install/register a handler.

Registration is per-user, x64-only, and scoped to Luma-owned CLSIDs/ProgIDs and
OpenWith/capabilities entries. Never write UserChoice, its hash, extension defaults,
HKLM, or DisableProcessIsolation. Do not replace another application's thumbnail
handler. Common raster formats should retain the effective existing provider or
Windows PhotoThumbnailProvider.

Turning thumbnails off preserves the file-opening ProgIDs, Open With entries,
capabilities, and existing common-image providers so the selected default viewer
still works. Full application uninstallation removes the values it owns.

Uninstall compares every value with the value Luma wrote before restoring it.
Later user/third-party changes are left untouched. No recursive deletion of shared
registry keys. Notify association changes; do not restart Explorer or clear the
user's thumbnail cache. Per-user COM may not activate in elevated applications.
Portable registration depends on the bundle's absolute location; unregister
before moving/removing it. Updates retain the old bundle until explicit repair/
registration points at the new bundle.

## Process architecture

Four native COM classes map raster, Radiance HDR, flat EXR and camera RAW to
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
- RAW thumbnails prefer embedded JPEG/bitmap previews for speed. A missing preview
  may use bounded half-size LibRaw processing. The main viewer still develops RAW
  rather than substituting an embedded preview
- Thumbnail safety limits are deliberately tighter than interactive viewing:
  raster/sensor dimensions up to 64 MP, float/HDR/EXR up to 16 MP; actual success
  also depends on memory, time, compression and camera model
- Unsupported variants or any failed limit return no thumbnail. Explorer can
  display the file icon. Do not hang or crash Explorer to force a preview

## Required Windows CI and manual checks

1. Compile x64 native DLL/tests with MSVC; test COM identity, ownership,
   repeated initialization, empty/truncated/oversized streams, absent helper,
   malformed protocol output and timeout/kill behavior
2. Run headless Python tests without importing Qt, plus all existing viewer tests
3. Build the real one-directory bundle. One-file bootloaders are incompatible
   with the single-process job limit
4. Exercise actual bundled thumbnail extraction for PNG/TGA/DDS/HDR/flat EXR,
   one real Nikon NEF, and Unicode/spaced filenames; preserve source hashes
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

## Official references

- https://learn.microsoft.com/en-us/windows/win32/shell/building-thumbnail-providers
- https://learn.microsoft.com/en-us/windows/win32/shell/thumbnail-providers
- https://learn.microsoft.com/en-us/windows/win32/api/thumbcache/nn-thumbcache-ithumbnailprovider
- https://learn.microsoft.com/en-us/windows/win32/api/thumbcache/nf-thumbcache-ithumbnailcache-getthumbnail
- https://learn.microsoft.com/en-us/windows/win32/shell/fa-associationarray
- https://learn.microsoft.com/en-us/windows/win32/sysinfo/hkey-classes-root-key
- https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects
- https://learn.microsoft.com/en-us/windows/win32/secauthz/mandatory-integrity-control
