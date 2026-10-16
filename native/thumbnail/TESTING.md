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
prepares the existing hash-pinned fixture set, and covers PNG, HDR, EXR, real NEF,
a Unicode source name, and the explicit alpha fixture.

The script refuses existing provider CLSID registrations. It registers only
previously absent provider CLSIDs and unique temporary file extensions under
HKCU, sets ThreadingModel=Apartment, and never disables process isolation. It
cleans only its created registration roots and temporary fixture paths, preserving
diagnostic artifacts. It does not change normal image-extension associations,
the default viewer, or registrations on the user's computer.

Each real fixture has a bounded direct-COM preflight and a separate cache request:

    thumbnail_tests.exe --surrogate path/to/fixture

The latter calls IThumbnailCache with WTS_FORCEEXTRACTION and
WTS_REQUIRESURROGATE. It does not load the provider DLL directly, does not accept
an icon fallback, and fails if Windows cannot provide a surrogate. The harness
reports bitmap dimensions/format and the calling host PID. That PID is not
presented as the surrogate or helper PID.

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
