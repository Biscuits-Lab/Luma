# EXR opening and Explorer previews

This is the 0.1.3 development candidate, based on released 0.1.2 commit
`f861e0574096d76ef6534571d1a6dd6c46d7a143`. A candidate is not a published release.
The user's original EXR was unavailable; a screenshot established an 8192 × 8192
image rejected by the old 64,000,000-pixel guard. No private user input is included
in this repository or the generated benchmark corpus.

## Changes

- Admit full-resolution flat EXR up to 67,108,864 pixels, with separate allocation
  guards. Other formats retain their existing pixel limits
- Read only the selected RGB/Y/scalar layer and alpha in bounded strips through
  the pinned official OpenEXR adapter; avoid retaining all source channels
- Use at most four OpenEXR decode workers, configured once per process
- Render large linear images with at most four fixed tasks. Each holds only a
  small band, shares read-only source pixels and writes disjoint output rows
- Avoid an extra full-size Qt RGBA copy. The final native-format QImage owns its
  storage; it does not depend on the lifetime of a worker/cache buffer
- Drop the previous uncached full-resolution source before decoding a different
  foreground image. Same-image adjustments and idle prefetch preserve active data
- Generate EXR thumbnails from a small center-sampled preview, rather than
  full-source float arrays. Dedicated EXR and executable-association routes agree
- Show effective `.exr` and `.hdr` thumbnail associations separately in Settings

The full viewer still retains every source pixel at float32 precision. There is
no preview-to-full-image swap and no reduced-resolution Copy/Save As fallback.
Channel/alpha/exposure/tone-map behavior is unchanged. HALF converts exactly to
float32; UINT has the same float32 integer-rounding limits as before.

## Measured full-window opening

Three samples per case, alternating **fresh baseline/candidate Python processes**
on the same Linux cloud host and the **same generated files**. Each process ran
stage probes before a fresh ViewerWindow open, so UI times exclude imports/window
creation and have warm library/OS state. OS disk caches were not evicted. Qt used
the offscreen platform. The files below contain highly compressible synthetic
bands, so these numbers are not predictions for a real production texture or the
user's hardware.

| Synthetic source | 0.1.2 median | Candidate median | Ratio |
| --- | ---: | ---: | ---: |
| 4096 × 2048 HALF/ZIP | 0.417 s | 0.227 s | 1.84× |
| 4096 × 2048 FLOAT/PIZ | 0.361 s | 0.208 s | 1.73× |
| 8192 × 4096 layered FLOAT/ZIP | 2.117 s | 0.882 s | 2.40× |
| 8192 × 4096 HALF/DWAA | 1.633 s | 0.854 s | 1.91× |

A separate fresh-process, UI-only 8192 × 8192 HALF/DWAA run had a 1.787 s median
and about 1727 MiB peak process RSS. Baseline 0.1.2 rejects that size; it has no
valid opening-time comparison. Full-resolution float32 RGBA alone is 1 GiB at
that size; display surfaces and workspace need additional RAM. Native decode
limits do not claim the whole application fits within 1.5 GiB.

The stage records distinguish compressed reads/conversion, serial and production
rendering, Qt conversion and actual UI completion. All full-resolution RGBA hashes
match the baseline wherever the baseline accepts the input. Every UI sample
checks complete resolution, settled 100% fit and actual-pixel 1:1 scaling.
See [the full report](benchmarks/exr-display-2026-10-16.json), including fixture
hashes, measured source hashes, individual samples and conditions.

## Structured/compression measurements

A second serial sweep uses six 4096 × 2048 HALF/FLOAT ZIP/PIZ/DWAA files with
seeded noise, gradients, high-frequency detail and a negative data-window origin.
The FLOAT/ZIP case has eight channels and is 222,051,248 bytes compressed. Three
fresh baseline/candidate samples per mode give 72 measurements. This sweep uses
the serial public renderer to isolate decoder changes from UI parallelism.

Full decoding is 1.45–2.83× faster across these fixtures. The eight-channel ZIP
case decodes in 0.294 s versus 0.832 s, with full pipeline peak RSS about 244 MiB
versus 426 MiB. Full pixel and rendered hashes are identical. Thumbnail memory
drops to roughly 57–82 MiB from 231–426 MiB, with complete thumbnail times
roughly 0.058–0.258 s. Thumbnail pixels intentionally differ from the old
full-size tone-map/Lanczos pipeline because the new path samples scene-linear
pixels before tone mapping. Memory does not fall in every full-view case: the
three-channel HALF/PIZ case rises from about 214 to 223 MiB due to thread/codec
workspace. See [all samples and file hashes](benchmarks/exr-structured-2026-10-16.json).

## Thumbnail and format limits

Flat HALF/FLOAT/UINT sources, ZIP/PIZ/DWAA, RGB/RGBA, named layers, luminance,
scalar channels, data-window offsets, descending scanlines and tiled layouts
have regression coverage. Deep, multipart and subsampled channels remain
unsupported. No OCIO/chromaticity conversion or material-channel interpretation
is inferred from a filename.

Thumbnail output is at most 1024 pixels per side. The preview source ceiling is
268,435,456 pixels, including a 16384-square image; compressed input remains
256 MiB, the helper job 768 MiB and its execution deadline ten seconds. Header,
channel, offset-table, strip and codec-workspace guards also apply. Compressed
chunks may require decompressing pixels not retained in the preview. Center
sampling trades fine-detail reconstruction for bounded memory; it is not an
embedded preview and does not validate every unrequested pixel chunk.

Generated large native cases cover six combinations across 4K, 8K, 8K square,
16K panorama and 16K square. The Windows gate must check direct extraction,
required Shell surrogate extraction and the Applications route, including color
and alpha probes, without disabling isolation. The packaged viewer must also
open a Unicode-named 8192-square FLOAT/ZIP image. Source hashes, installer
lifecycle, native Open With discovery and the full regression suite remain gates.
Actual interactive Explorer appearance and the unavailable user file are separate
validation, not implied by these synthetic tests.

## Reproduce

Generate bounded band fixtures with `prepare_exr_thumbnail_fixtures` from
`scripts/smoke_thumbnails.py`. `scripts/benchmark_exr_display.py` separates read,
render, Qt and UI times. Run the following alternately three times, each as a fresh
process on an otherwise idle machine, with `QT_QPA_PLATFORM=offscreen`:

```sh
python scripts/benchmark_exr_display.py generated.exr \
  --repo ../released-0.1.2 --decoder ../released-0.1.2/luma/decoders.py --repeat 1 --ui
python scripts/benchmark_exr_display.py generated.exr --repeat 1 --viewer-render --ui
```

The baseline checkout must be the exact commit identified above. Additional
seeded gradients, high-frequency detail, noise and eight-channel fixtures are
reproducible with `scripts/benchmark_exr_decode.py`; they are separate from the
compressible-band UI measurements. Generation is outside timed regions and inputs
are never modified by a timed decode.

## Maintenance references

- [Official Python API and compatibility limitations](https://openexr.com/en/latest/python.html)
- [Official reading, threading and compression guidance](https://openexr.com/en/latest/ReadingAndWritingImageFiles.html)
- [Pinned selected-channel Python adapter source](https://github.com/AcademySoftwareFoundation/openexr/blob/v3.5.2/src/wrappers/python/PyOpenEXR_old.cpp)

The legacy InputFile adapter is deprecated but still included in OpenEXR 3.5.2.
Do not upgrade the dependency without rerunning layout, allocation and native
Windows coverage. Actual compression remains in the official library.
