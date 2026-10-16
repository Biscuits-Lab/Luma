# Native thumbnail verification

The provider is a Windows x64 C++17 COM DLL. It uses IInitializeWithStream and
IThumbnailProvider, returns a top-down straight BGRA DIB with WTSAT_ARGB, and
runs the fixed sibling Luma.exe as a bounded thumbnail worker.

## Build and direct COM checks

On a Windows x64 MSVC runner:

    cmake -S native/thumbnail -B build/thumbnail -A x64 -DBUILD_TESTING=ON
    cmake --build build/thumbnail --config Release
    ctest --test-dir build/thumbnail -C Release --output-on-failure

CTest loads a DLL-only fixture with no sibling Luma.exe. The tests cover COM
identity/lifetimes, all four classes, initialization-once, cx=0 rejection,
malformed streams, avoiding IStream::Stat, and the missing-helper failure path.
CTest bounds the ordinary/protocol tests to 60 seconds and the timeout test to
35 seconds. A separate test-only Luma.exe emits malformed magic, dimensions,
truncated/extra/oversized output, and a nonzero exit code. Valid fixtures check
exact straight-alpha bytes and the 1024-pixel clamp. The timeout fixture signals
its PID to a watcher before sleeping; the harness pins a process handle and
asserts the helper is terminated after the provider's ten-second timeout.
A read-only-pin fixture opens the input with LibRaw's READ/share-READ mode.
Before and after that open, WRITE and DELETE probe opens must fail specifically
with sharing violations, verifying the provider's retained pin.

These synthetic tests do not replace real decoder or surrogate smoke checks.

After the one-directory application has been built, LumaThumbnail.dll must be
beside Luma.exe, not inside _internal. Real-helper checks use:

    build/thumbnail/Release/thumbnail_tests.exe dist/Luma/LumaThumbnail.dll raster path/to/image.png

A 1x1 PNG with straight RGBA=(255,0,0,128) can additionally be checked with
--expect-alpha. This requires the returned provider DIB to contain exact
BGRA=(0,0,255,128), retaining straight alpha.

## Isolated CI surrogate smoke

Run scripts/smoke_thumbnails.py only on a disposable Windows CI runner, before
persistent installer-registration tests. The Python entry point launches the
PowerShell 7.2 script with the current Python interpreter. It uses the built harness and bundle,
prepares the hash-pinned fixture set, and covers PNG/JPEG, HDR, EXR, real NEF,
a Unicode source name, the explicit alpha fixture, and compressible synthetic
4K/8K/16K HDR environment-map dimensions, plus six bounded-strip generated
EXRs covering HALF/FLOAT, ZIP/PIZ/DWAA, RGB(A), layered RGB(A), and Y. EXR
dimensions include 8K-square textures and the exact 16K-square preview limit;
one EXR filename contains Unicode and spaces. Those large fixtures exercise the
unchanged ten-second/768-MiB worker envelope; they are not worst-case benchmarks.

Every EXR case also requires sixteen interior BGRA/alpha probes in all three
native routes. The Python preparation step decodes each compressed file to
record its actual displayed values (DWA is lossy), then supplies the bounded
probe file through the test-only `LUMA_THUMBNAIL_EXPECTED` environment variable.
The standard-token surrogate client inherits that variable. Probe files are
retained with the manifest and diagnostic logs. Independent Python tests check
known source bands, channel selection, alpha unpremultiplication, and the full
small HALF/FLOAT × ZIP/PIZ/DWAA × channel-layout matrix. This verifies synthetic
fixtures only; a private user sample and manual Explorer inspection remain
separate validation steps.

The native harness first runs an exact-byte bitmap-readback regression with
both negative-height (top-down) and positive-height (bottom-up) 32-bit DIBs.
Each row and alpha value differs. Readback always requests top-down output
from `GetDIBits`; it never infers the source row order from the height returned
by `GetObject`, which can discard the creation-height sign. This catches a
harness-only vertical flip without accepting reversed EXR probes or relaxing
color/alpha checks. The logs include both created and reported heights.

The same three extraction routes also require a seeded moderate-detail
8192-square HALF RGBA ZIP fixture (about 66 MB on the pinned Linux build).
It is generated in 32-row strips by `scripts/generate_exr_moderate_fixture.py`;
the fixture manifest records its exact runtime size, digest and recipe. Native
pixel probes still apply, and neither the 256 MiB input limit nor the ten-second
worker deadline is increased. Compressed bytes may differ across codec builds.

The script refuses existing provider CLSID and Applications\\Luma.exe registrations.
It registers only previously absent provider CLSIDs, that owned application key,
and unique temporary file extensions under
HKCU, sets ThreadingModel=Apartment, and never disables process isolation. It
cleans only its created registration roots and temporary fixture paths, preserving
diagnostic artifacts. It does not change normal image-extension associations,
the default viewer, or registrations on the user's computer.

Each real fixture has a bounded direct-COM preflight, a per-format cache request,
and an Applications\\Luma.exe content-aware cache request. The last route uses
a disposable extension default naming the application ProgID, without touching
UserChoice or real image-extension defaults:

    thumbnail_tests.exe --surrogate path/to/fixture

The latter calls IThumbnailCache with WTS_FORCEEXTRACTION and
WTS_REQUIRESURROGATE. It does not load the provider DLL directly, does not accept
an icon fallback, and fails if Windows cannot provide a surrogate. The harness
reports bitmap dimensions/format and the calling host PID. That PID is not
presented as the surrogate or helper PID.

Surrogate tests log the process token's user SID, integrity, elevation,
Administrators membership, restrictions, session, and read-only EnableLUA
policy, plus association and HKCU/HKCR registration lookup results. An ordinary
medium, unelevated client runs directly. An elevated CI client creates a
same-user LUA restricted token, lowers its integrity to medium, and verifies
that it is unelevated and has no enabled Administrators group before spawning
only the test client. The child rechecks its token before any COM initialization.
If token reduction or process creation fails, the smoke fails visibly.
No system policy, account, profile, or machine registration is changed.

The test client is created suspended with only its redirected standard handles
inherited, enters a kill-on-close job before resuming, and has a 44-second wait
plus at most one second of cleanup. The outer script also bounds all invocations.
The launcher never elevates, requests breakaway, takes another process's token,
or substitutes an in-process thumbnail fallback. A built-in PNG surrogate
baseline helps separate OS hosting failures from temporary provider registration.

The direct path reports the exact provider HRESULT, including helper launch or
job assignment failure. Windows can wrap a surrogate-side provider failure as
WTS_E_FAILEDEXTRACTION; the cache HRESULT is reported separately. A failed
surrogate test must not be relabeled as a passing in-process test.

## Limits and remaining coverage

The helper is created suspended without inherited handles and must enter a
kill-on-close, single-process, 768 MiB job before resuming. There is no breakaway
or elevation fallback. A one-file launcher that spawns a second process is
incompatible with this process limit.

The 10-second deadline is checked around stream/file work and bounds the helper
wait. Arbitrary synchronous IStream or filesystem calls cannot be absolutely
time-bounded inside the COM DLL. The CI harness supplies an outer process timeout.

The install and temporary-directory ancestor checks reject UNC paths and
reparse-point ancestors. Temp cleanup is non-recursive and limited to the paths
created by that request.

Additional max-source-boundary and independent job-limit tests remain useful. Source preparation alone is not evidence of a successful
Windows build, Explorer extraction, or a passing surrogate run; retain the
actual CI logs and result artifacts.
