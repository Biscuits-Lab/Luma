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
