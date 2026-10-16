# Interactive HDR exposure and exact refinement

Local development measurements, 2026-10-16. No release is implied.

## Behavior

Exposure/channel/tone edits coalesce into one next-event-loop preview request.
Changing a control immediately invalidates the old worker generation; one running
request and one replaceable pending slot prevent an accumulating queue. A cached
center sample of the decoded **linear** image is tone-mapped for feedback. The
sample is at most 393,216 pixels and 1024 pixels on either axis. Sampling does not
apply exposure to an already tone-mapped 8-bit image, modify the original, or
reopen the source for each edit.

A 180 ms idle timer requests the exact full-resolution rendering. Holding the
slider down suppresses refinement; release restarts the idle timer. A newer
control change cancels an in-progress refinement cooperatively at bounded band
boundaries. The initial file open remains exact/full resolution.

A temporary sample occupies the original source-pixel scene rectangle. Dimensions,
fit-relative zoom, actual-pixel scale, rotation and pan therefore remain stable
across preview/full swaps. During preview, the status explicitly identifies the
preview and Copy/Save are unavailable. Escape restores the last exact image and
its controls. A failed refinement restores the last exact image and allows a
retry. Navigation and Close invalidate pending work. Keyboard and context export
paths cannot accidentally export a reduced preview.

Qt RGBA-to-native-premultiplied conversion now runs in cancellable worker-side
bands. The GUI installs an owning, already prepared image with
`NoFormatConversion`; normal production results never perform full-image color
conversion in the GUI callback. QPixmap installation/painting remains subject to
the platform's graphics implementation. Histogram, metadata, thumbnail refresh
and frosted-glass captures stay out of the drag path; histogram and glass resume
on the exact result. The last worker result is released after its queued signal,
rather than retained during idle or the next large render.

## Same-file measurements

Three fresh processes per variant and source size, with three isolated exposure
changes and one 30-event drag at 33 ms spacing in each process. Details/histogram/
glass were visible in both variants. The baseline is the existing working-tree
code immediately before this exposure change, including the earlier parallel
renderer. The user file was unavailable: all inputs are generated synthetic EXRs.

Times below are input-to-canvas-install, not monitor scanout. They include the
idle timer for exact refinement. The comparison uses the same input file bytes
for each pair. The initial sweep alternated baseline/candidate processes; the
final candidate-only sweep was rerun serially after correctness fixes. OS caches
were not evicted. Linux, Python 3.12.14, NumPy 2.5.4, Qt offscreen. Native Windows
GPU latency, smoothness and the user's production file are **not certified**.

| Synthetic input | Baseline first/exact | Candidate first preview | Candidate exact | Baseline / candidate maximum GUI install |
| --- | ---: | ---: | ---: | ---: |
| 4096 × 2048 HALF ZIP, structured detail/fractional alpha | 219 ms | 25 ms | 338 ms | 8.8 / 4.0 ms |
| 8192 × 8192 HALF ZIP, moderate entropy | 1330 ms | 32 ms | 1409 ms | 175.5 / 29.3 ms |
| 16384 × 4096 HALF ZIP, moderate-entropy panorama | 1323 ms | 26 ms | 1447 ms | 156.8 / 12.1 ms |

First/exact values are medians of nine changes per case. The bounded preview
improves feedback by roughly 9–51× here; it is not a claim that exact rendering
itself became faster. Delaying expensive work until idle can make the final exact
result slightly later, especially on the small case.

During each 30-event drag, the baseline displayed zero intermediate updates.
Candidate runs displayed 25–26 updates at 4K, 22–26 at 8K square, and 20–27 at the
16K panorama. Accepted drag previews had about 22 ms median input latency. Newer
inputs discard superseded work instead of presenting an old result after the
latest settings. Final RGBA output is byte-identical to both the unchanged public
renderer and the baseline for every sample. Each complete edit sequence decoded
the file exactly once.

`16K` here means **16384 × 4096**, within the existing 67,108,864-pixel EXR limit.
16384-square files still exceed the full viewer safety limits; this change does
not enable them. A separate thumbnail allowance is not full-viewer support.

## Memory bounds

- Linear interaction sample: at most 6 MiB; preview RGBA plus prepared Qt image:
  at most 3 MiB, with bounded sample/coordinate/conversion temporaries
- Exactly one active decoded source is reused. The existing 256 MiB decoded LRU
  remains bounded; oversized active sources are held outside that LRU
- Exact frame cache stays within its 128 MiB budget and now counts **both** RGBA
  and the prepared Qt image, rather than overlooking the second pixel buffer
- Preview renders are not added to the exact-frame LRU. One prior exact native
  image is retained for cancellation recovery, not a history of slider frames
- Full rendering keeps the existing at-most-four tasks, each with no more than a
  1,048,576-pixel band and its temporary workspace. Native conversion uses at most
  128 rows per temporary band. Full-resolution source/output surfaces remain
  necessary for exact display and are not part of the tiny preview allowance

Maximum observed pre-validation process peak RSS (baseline → candidate): 4K
503 → 486 MiB; 8K square 2250 → 2030 MiB; 16K panorama 2306 → 2009 MiB. These
include the entire process and allocator behavior, not just live arrays. The
independent final equality check intentionally allocates another exact output;
its separate peak is recorded as `validation_peak_rss_mib`. The viewer does not
claim that a 1 GiB decoded source fits within a 1 GiB application memory budget.

## Reproduce and audit

Run serially against a pre-change package snapshot and candidate package:

```sh
QT_QPA_PLATFORM=offscreen python scripts/benchmark_exposure.py synthetic.exr \
  --repo /path/to/pre-change-snapshot --label baseline
QT_QPA_PLATFORM=offscreen python scripts/benchmark_exposure.py synthetic.exr \
  --repo /path/to/candidate-snapshot --label candidate
```

The script records file SHA-256, exact final RGBA SHA-256, decode count, render
stage/cancellation records, first/preview/exact latency, GUI callback time,
event-loop heartbeat gaps, drag update counts and peak RSS (where available).
Generation and equality verification are outside the timed interaction region.
Do not substitute different files for baseline and candidate.

[Full measurements, recipes, source hashes and individual samples](benchmarks/exposure-preview-2026-10-16.json)
record the exact snapshots. Local audit copies are under the ignored
`build/exposure-preview/` directory. The benchmark snapshots exclude the separate
explicit-Fit animation; its geometry-only tests are described below.

## Explicit Fit animation (separate from the exposure benchmark)

Only the explicit footer Fit button animates: 220 ms OutCubic easing interpolates
view scale and scene center, ending on the existing immediate-fit geometry and
exactly 100% fit-relative zoom. Initial file opening, automatic fit, regular
programmatic fitting and export are unchanged. The animation does not decode,
tone-map, resample, or copy image pixels per frame.

Repeated Fit retargets from the current geometry. Wheel zoom, pan, presets,
actual-pixel mode, new images, Clear, resize, DPI changes and hiding the canvas
cancel the animation so a stale completion cannot move the new view. Same-source
preview/refinement swaps preserve the animation. Histogram/glass work is
suspended during the motion and restored afterward, while an overlapping pending
exposure render keeps them suspended until it finishes.

Qt-disabled effects and Windows `SPI_GETCLIENTAREAANIMATION` are honored; disabled
or unreadable Windows accessibility state uses an immediate fit. Tests cover
mid-animation geometry, exact endpoint/100%, repeated Fit, interruptions,
reduced-motion behavior, unchanged pixmap identity and zero image/decode calls.
These are offscreen functional tests, not a native Windows visual smoothness claim.

References: [Qt QVariantAnimation](https://doc.qt.io/qt-6.12/qvariantanimation.html),
[Windows SystemParametersInfoW](https://learn.microsoft.com/windows/win32/api/winuser/nf-winuser-systemparametersinfow).

## Final local verification

The complete working-tree suite passed: **1626 passed, 2 Windows-only skipped**
(260.15 seconds); the 12 warnings are existing Qt drag/drop deprecations. The
focused exposure/Fit/zoom/Details/glass group passed all 101 tests. `git diff
--check` is clean. Synthetic offscreen Fit frames at 0, 110 and 220 ms were also
inspected: geometry moves toward the centered full image and ends at 100%.
Native Windows accessibility settings, actual window animation and GPU display
remain platform validation, not claims inferred from these cloud tests.
