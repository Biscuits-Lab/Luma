# Performance changes and measured limits

These are observations from the same Linux x64 cloud machine on 2026-10-16,
Python 3.12.14 and the pinned dependencies, comparing unmodified v0.1.1 commit
`ada8d4beceedb7ef336401c80c546f5f3e635f08` with this candidate. They are not
measurements of a user's PC, Windows startup/Explorer latency, or a frame-rate
promise. The OS file cache was **not** evicted. Generated fixtures are fixed and
identical between versions; no user photographs were used.

## Full-resolution HDR decode and SDR rendering

Median of three samples per encoding/resolution in a warm process, seconds:

| Fixture | v0.1.1 | Candidate | Speedup |
| --- | ---: | ---: | ---: |
| 3840 × 2160 flat RGBE | 4.273 | 0.361 | 11.85× |
| 7680 × 4320 flat RGBE | 18.157 | 1.457 | 12.47× |
| 3840 × 2160 modern RLE | 0.686 | 0.477 | 1.44× |
| 7680 × 4320 modern RLE | 2.797 | 1.987 | 1.41× |

Flat decoding formerly read and assigned each pixel in Python. It now reads
bounded scanlines and vectorizes the common case, retaining legacy repeat-packet
validation and all RGBE exponent values. Modern RLE uses efficient byte buffers.
Rendering retains the existing exposure/channel/tone-map equations while avoiding
unneeded full-image copies. Every benchmark's rendered SHA-256 matches the
release byte-for-byte. Regression tests cover all exponents, orientations,
malformed packets, cancellation, and SDR/linear render combinations.

Fresh isolated-process 8K flat decode/render measured 18.510→1.490 seconds;
whole-process wall time including imports and checksum validation was
18.750→1.685 seconds. 8K RLE was 2.723→1.772 seconds for decode/render.
Fresh-process 8K peak RSS fell from about 747 to 696 MiB. The full-resolution
viewer still has its existing 64 MP / allocation limits and retains float32 HDR
precision; this is not a downsampled viewer or native HDR-monitor pipeline.
[Stage timings, checksums and memory evidence](benchmarks/hdr-2026-10-16.json)

## JPEG opening and navigation

Qt offscreen, a fresh application per resolution, three alternating independent
process runs per version. Each action is followed by 400 ms of idle event
processing, allowing the candidate's prefetch to run. Values below are medians of
per-run metrics. Files are deterministic textured gradient JPEGs, quality 92,
4:4:4, approximately 1.8 MB (4K) and 4.9 MB (24 MP).

| Operation | v0.1.1 | Candidate |
| --- | ---: | ---: |
| First 3840 × 2160 image | 399 ms | 127 ms |
| Next 4K image after idle pause | 332 ms | 35 ms |
| Repeated 4K navigation | 286 ms | 33 ms |
| First 6000 × 4000 image | 593 ms | 355 ms |
| Next 24 MP image after idle pause | 613 ms | 77 ms |
| Repeated 24 MP navigation | 473 ms | 79 ms |

This is end-to-end request-to-displayed-frame event processing, not just the
JPEG codec. Imports alone were about 165→161 ms, so startup import speed is not
claimed as the improvement. Direct JPEG decoding remains similar; the gains
come from removing synchronous layout/scroll work on the hidden filmstrip,
reusing display-ready frames, and bounded idle prefetch. A hidden QListWidget's
`scrollToItem` caused roughly 200 ms of unnecessary synchronous work per switch.
Images stay full-resolution for actual-pixel zoom; no preview/full-resolution
swap is hidden in these numbers. A very rapid uncached switch, cache eviction,
large/slow native decoder, or no idle interval can take longer.
[Fixture hashes and three-run medians](benchmarks/jpg-2026-10-16.json)

## Explorer HDR thumbnail worker

Median of three fresh subprocesses per case, Linux source-worker wall time,
256 px requested edge unless stated. Process RSS is **not** Windows Job committed
memory. Real packaged Windows required-surrogate checks remain mandatory.

| Source | v0.1.1 | Candidate | Candidate peak RSS |
| --- | --- | ---: | ---: |
| 4096 × 2048 RLE | 0.849 s / 235 MiB | 0.260 s | ~34 MiB |
| 8192 × 4096 RLE | Rejected: over 16 MP | 0.613 s | ~34 MiB |
| 16384 × 8192 RLE | Rejected: over 16 MP | 1.900 s | ~34 MiB |
| 16384 × 8192 RLE, 1024 px output | Rejected | 1.957 s | ~47 MiB |
| 7680 × 4320 flat, 132.7 MB | Rejected: over 16 MP | 0.217 s | ~35 MiB |

The thumbnail path validates every encoded scanline but retains only a bounded,
pixel-center-sampled float preview before tone mapping. It deliberately trades
fine-detail reconstruction for memory and latency. The interactive viewer uses
its separate full-resolution path. The 256 MiB input, 768 MiB Windows process,
ten-second worker, and 1024 px output limits are unchanged. Compact 16K RLE can
work; flat 16K over 256 MiB cannot. HDR sampling supports up to 256 MP, each axis
at most 32768. EXR still uses its 16 MP full-float limit. Camera RAW coverage is
model/variant-dependent. [Raw worker observations](benchmarks/thumbnails-2026-10-16.json)

## Bounds and responsiveness

- One executing image task, one replaceable foreground pending slot and one
  lower-priority pending prefetch slot. No unbounded thread/task queue
- Cooperative cancellation checks in HDR decoding/rendering. Native Pillow,
  OpenEXR and LibRaw calls cannot safely be interrupted mid-call; stale results
  are discarded, and latest foreground work runs next
- Decoded cache: 256 MiB; rendered RGBA cache: 128 MiB; filmstrip cache: 8 MiB.
  These are cache budgets, not a total-process memory cap. Active image, Qt image
  copies and decoder temporary buffers are additional
- Prefetch only eligible JPEG/PNG/WebP, at most 24 MP and 32 MiB compressed,
  and only if they fit both cache budgets. It shares the foreground worker;
  HDR/EXR/RAW are not speculatively decoded
- Cache keys include path, mtime in nanoseconds and file size; render keys also
  include exposure, channel and tone map. File changes invalidate entries
- Fast switches do not flash a temporary loading/cancel indicator. Footer
  geometry is stable, signal feedback is blocked, each new fitted image reports
  100%, and actual source pixels remain a separate explicitly named action

## Reproduce

Use the pinned environment, an unchanged baseline worktree, and the same fixture
folder. Run measurements serially rather than competing with builds/tests.

```sh
git worktree add ../luma-baseline ada8d4beceedb7ef336401c80c546f5f3e635f08
python scripts/benchmark_hdr.py --fixtures ../hdr-fixtures --generate --repeat 3
python scripts/benchmark_hdr.py --fixtures ../hdr-fixtures --decoder ../luma-baseline/luma/decoders.py --repeat 3
# Add --fresh-process to isolate each HDR sample and report full process wall time.
python scripts/benchmark_navigation.py --fixtures ../jpeg-fixtures --generate --repo . --output candidate.json
python scripts/benchmark_navigation.py --fixtures ../jpeg-fixtures --repo ../luma-baseline --output baseline.json
# Repeat in alternating order three times; default --pause 0.4 permits prefetch.
# --pause 0 measures rapid navigation without an intentional idle interval.
python scripts/benchmark_thumbnails.py --fixtures ../thumbnail-fixtures --generate --baseline-dir ../luma-baseline/luma --output thumbnails.json
# Add --edge 1024 or --extra-fixture ../hdr-fixtures/gradient-8k-flat.hdr as needed.
```

The benchmark scripts do not evict OS caches and do not claim cold-disk results.
Full Windows build, native surrogate/installer lifecycle validation, plus manual
Windows 11 rounding/snap and Explorer visual checks are separate evidence.

## Display minification, local Windows offscreen measurements

On 2026-10-17, Biscuits-Titan / Python 3.13.12 / pinned Qt 6.12.0 ran
`scripts/benchmark_minification.py` using Qt **offscreen**, with 3840x2160 and
7680x4320 seeded synthetic noise/checkerboards, plus Matplotlib's unchanged
512x600 public-domain Grace Hopper photo. This is headless CPU rendering,
not interactive Windows desktop validation. Private user photographs were not
exported or used as benchmark output. Fixture creation, reference integration,
PNG encoding and I/O were excluded from timing. Each paint used three repeats;
cache construction timings are single cold requests, not a latency guarantee.

The initial cache timings below precede later lifecycle hardening. That hardening
replaced a QObject worker signal bridge with one GUI-owned 5-ms result timer and
checks graphics-item validity before updating. The area kernel, level selection
and byte budget are unchanged; cold-cache delivery latency for the hardened
version has not been remeasured. Idle caches have no active result timer.

| Measurement | Result |
|---|---:|
| 8K noise, 12.5% physical scale, cache ready | 25.9 ms |
| 8K checkerboard, 12.5% physical scale, cache ready | 27.3 ms |
| 8K noise, 37% physical scale, cache ready | 18.2 ms |
| Retained cache, 8K at 12.5% | 41.5 MiB (64 MiB limit) |
| Warm request/selection, 8K noise at 12.5% | 0.0024 ms |
| Noise standard deviation, old fast / smooth only / area cache, 12.5% | 23.97 / 12.00 / 3.01 display-code levels |
| 8K checkerboard RMSE against independent area reference, same methods | 120.30 / 74.94 / 0.50 display-code levels |
| Public photo RMSE against independent area reference, same methods | 18.38 / 15.07 / 1.18 display-code levels |

DPR 1 and 2 were tested at the same physical scale. Physical 1:1 crops remained
byte exact across every fixture and both DPRs, even after caching. A burst of 84
zoom requests followed by source replacement retained only current-image pixels
(maximum channel error zero). All measured retained caches stayed below 64 MiB.
The synthetic noise measure is not a denoising claim for natural photographs.

The item explicitly enables smooth transformation by default. Strong reductions
use lazy, asynchronous area-filtered half-size levels; selection uses the actual
paint device transform, including display DPI, image DPR and item transforms.
The original full-resolution pixmap, scene geometry, copy/save data and source
file remain unchanged. Filtering operates on premultiplied display-code pixels;
it does not add a linear-light color pipeline. Quantization across levels can
introduce approximately one display-code level of mean drift.

Retained levels are bounded to 64 MiB, plus one native in-flight/result target
of at most 64 MiB. Source replacement can retain one old shared source until the
current native scaling stage returns. Native scaling is canceled at stage
boundaries; it is not forcibly interrupted. Original display storage and Qt's
small scaling tables are outside that cache allowance.

Reproduce with an unchanged public Matplotlib `grace_hopper.jpg` fixture:

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
.\.venv\Scripts\python.exe scripts/benchmark_minification.py --photo C:\Fixtures\grace_hopper.jpg --sizes 3840x2160 7680x4320 --scales 0.125 0.37 --repeats 3
```

The script refuses a non-offscreen platform. Optional 16K stress is separate
(`--include-16k`) and was not part of these measurements.

## Large-image thumbnails and embedded editing follow-up

The same Windows host ran a separate headless matrix after the large-raster
fix. Each source helper ran with the production envelope: one process, 768 MiB
committed-memory limit, ten seconds, and no Qt import. The source helper used
the base Python interpreter directly with the pinned project wheels; Windows'
venv redirector would create an extra process and is unsuitable for this
single-process harness. This is source validation, not a new frozen bundle.

| Source | 256-edge output | Worker time | Peak process commit |
|---|---:|---:|---:|
| Synthetic progressive JPEG, 7680x4320 | 256x144 | 0.37 s | 208 MiB |
| Synthetic PNG, 7680x4320 | 256x144 | 0.25 s | 145 MiB |
| Synthetic RLE HDR, 7680x4320 | 256x144 | 0.82 s | 23 MiB |
| Synthetic ZIP HALF Y EXR, 7680x4320 | 256x144 | 0.32 s | 24 MiB |
| Synthetic progressive JPEG, 8192x8192 | 256x256 | 0.53 s | 404 MiB |
| Synthetic PNG, 8192x8192 | 256x256 | 0.29 s | 276 MiB |
| Synthetic RLE HDR, 8192x8192 | 256x256 | 1.07 s | 24 MiB |
| Synthetic ZIP HALF Y EXR, 8192x8192 | 256x256 | 0.31 s | 25 MiB |
| Explicit local progressive 4:4:4 JPEG, 8736x11648 | 192x256 | 0.66 s | 605 MiB |

These are single requests on compressible synthetic fixtures and one explicit
local diagnostic file, not worst-case bounds for arbitrary 8K content. Source
hashes were unchanged. The private helper now caps BLAS/OpenMP worker pools in
its own process before NumPy import; HDR/EXR peak commit dropped from roughly
763-764 MiB to 23-25 MiB for these requests. System environment settings were
not changed. JPEG progressive coefficient storage remains significant even
with native 1/8 spatial decoding, so it has its own pre-load workspace guard.

A direct class-factory call to the currently installed 0.1.3-directory DLL
succeeded for standard-8K JPEG/PNG and both HDR/EXR sizes, but failed with
HRESULT 0x8007042b for square-8K JPEG/PNG and the 101.8-MP JPEG. No registration,
Explorer cache API, Explorer restart, or global cache reset was performed.
Source changes cannot update that installed helper or establish Explorer-cache
behavior. A future authorized component update needs its own frozen validation.

The explicit 8736x11648 file also entered the main-window editor in offscreen
mode, retaining the full-resolution source and producing a 1095x1461 preview;
cancel returned to viewing with unchanged file bytes. Snapshot preparation was
about 0.52 seconds in that single probe. Automated synthetic tests cover embedded
save/cancel, dirty-exit and saving guards, stale callbacks, repeated entry,
window/maximized state, manual zoom/pan, and three languages at 800x560.
