# Image formats and display limits

An extension identifies a candidate file, not a promise that every variant can decode. Corrupt, encrypted, unsupported or excessively large inputs should report an error without changing the original. The authoritative extension lists and limits are in `luma/decoders.py`.

## Ordinary raster images

Pillow handles common raster formats, including JPEG, PNG, TIFF, WebP, BMP, GIF, TGA and DDS where supported by the pinned Pillow wheel. Static viewing uses one frame/page; this is not a GIF animation player or a multipage document viewer. Embedded EXIF orientation is applied where supported by the decoder. Display pixels are prepared for the viewer; high-bit-depth source data is not an editing or export buffer.

### Large photographs

JPEG photographs have a separate full-resolution allowance of 128,000,000
pixels, selected from the decoded format rather than the filename extension.
Other ordinary raster formats retain the 64,000,000-pixel ceiling. Both limits
are checked before pixel loading, alongside the 32,768-pixel side and 1.5 GiB
decoded-buffer limits. Pillow's generic decompression warning is handled locally;
its global limits are unchanged. A 100 MP photograph needs roughly 400 MiB per
RGBA8 surface, with additional decoder, display and temporary storage, and may
still fail cleanly if the machine cannot allocate enough memory. JPEGs are not
downsampled during decoding; actual-pixel viewing retains original detail.

### TGA

TGA is decoded through Pillow, including variants supported by its TGA plugin. Treat an alpha channel according to the file's actual data; not every TGA uses alpha. Production TGA variants should be represented in the acceptance corpus.

### DDS

DDS support is bounded by Pillow's decoder and the particular texture encoding. DDS is not a single compression format. The viewer presents the decoded base 2D image; it is not a cubemap, texture-array, volume, or mip-chain inspector. Do not advertise every DirectX/DXGI compression variant as tested merely because `.dds` is accepted. Test actual pipeline BC/DXT variants before relying on them.

## Radiance HDR / RGBE

The native Radiance decoder reads supported RGBE `.hdr`/`.pic` images into floating-point RGB. Exposure and display tone mapping make scene values viewable on an ordinary display. Radiance XYZE, unusual headers, corrupt scanline data or uncommon orientation variants may be rejected depending on the implementation's explicit validation. This is file decoding, not native monitor HDR output.

## OpenEXR

OpenEXR is decoded using the official OpenEXR Python binding. The supported viewing model is a **flat image with viewable color channels**. Floating-point radiance is tone-mapped for display. Deep data, arbitrary AOV/channel layouts, multipart compositing, texture arrays, and production color-management pipelines are outside this first version's guarantees. The decoder reports an explicit error where it cannot construct a viewable image.

The EXR-specific full-resolution ceiling is 67,108,864 pixels, allowing an
8192 × 8192 image; other viewer formats retain their existing ceilings. A full
8K square float32 RGBA source alone occupies 1 GiB. Decode workspace is checked
against the 1.5 GiB allowance; RGBA8 display pixels, Qt surfaces and caches need
additional RAM. The viewer retains full-resolution float32 values for zoom/channel
inspection. HALF samples convert exactly to float32; UINT channels can round above
float32's exact integer range, as in earlier versions. Explorer uses a separate bounded preview path, described in
[thumbnail limits](EXPLORER_THUMBNAILS.md), rather than decoding a full float image.

EXR channel names and a filename do not establish whether a texture stores color,
roughness, depth or other packed data. Luma preserves the decoded channel values
and shows the selected channels in Details. Its default SDR view remains a
scene-linear display transform with OpenEXR's associated-alpha convention; it
does not guess a material mapping or color space from a filename. Channel
inspection and exposure/tone-map controls are viewing tools, not a calibrated
material-data editor. Deep, multipart and subsampled channels remain unsupported.

The bounded reader uses `InputFile.channels` from the pinned official OpenEXR
3.5.2 wheel, because the modern `File` interface materializes whole channel
arrays. The upstream legacy adapter is deprecated but still shipped. Its use is
covered by scanline/tiled, compression, offset, alpha and malformed-header tests;
a future dependency upgrade must rerun those tests before changing this pin.
See [the official Python API documentation](https://openexr.com/en/latest/python.html).

A successfully decoded EXR is not evidence of color accuracy for a particular studio pipeline. No OCIO/ACES view transform, monitor calibration, HDR10 scRGB swapchain, mastering metadata or monitor-luminance control is provided.

## Camera RAW

RAW uses `rawpy.imread(...).postprocess(...)`, backed by LibRaw. The implementation performs actual RAW development/demosaicing and does not fall back to extracting a camera's embedded JPEG preview. Camera support follows the LibRaw version in the installed rawpy wheel and may vary by camera, firmware and RAW mode, even within a familiar extension family.

RAW white balance and output conversion are decoder settings, not an interactive full RAW editor. An explicit Save As exports the currently displayed SDR rendition; it is not a RAW export or scene-linear/HDR-preserving conversion. A camera-generated preview may differ in tone, crop, noise reduction, lens corrections or manufacturer's proprietary color rendering.

**Recorded cloud validation:** rawpy 0.27.1 / LibRaw 0.22.1 fully demosaiced the official rawpy test samples `iss030e122639.NEF` (4284×2844) and `iss042e297200.NEF` (4940×3292) into float32 linear RGBA and rendered them without changing source bytes. This verifies those two files on the cloud Linux environment, not every NEF camera or native Windows behavior. The Windows release workflow separately gates publication on decoding/rendering the first sample inside the packaged EXE. Samples are temporary validation inputs, not redistributed release assets.

**Testing distinction:** mocked rawpy tests can prove that Luma calls the demosaic path and handles errors. They cannot prove that a CR2, CR3, NEF, ARW, RAF, DNG or other camera file decodes correctly. A release report should name real camera files and versions actually exercised; do not infer a tested camera list from accepted extensions.

## Display, channels and color

- RGBA inspection and display rotation are viewing aids; they do not alter the source file
- Explicit Copy and Save As use the displayed SDR rendition, including display adjustments
- Explicit deletion requires confirmation and targets the recycle bin; permanent deletion is not a fallback
- Exposure/tone mapping is a display transform for HDR/RAW data, not an on-disk conversion
- The viewer does not claim a calibrated, end-to-end ICC/OCIO or wide-gamut/HDR monitor pipeline
- Memory and pixel-count guards are not a security sandbox; use current dependencies and avoid untrusted pathological images

## Evidence to record for a Windows release

For each tested format, record: fixture name and source/license, exact encoding, dimensions/bit depth, expected appearance, decoder version, native Windows launch path and result. Keep real RAW verification distinct from mocked API tests. At minimum include JPEG/PNG, transparency, TIFF, TGA, a compressed DDS used in the actual workflow, Radiance HDR, flat EXR with/without alpha, a real supported RAW file, corrupt files and cancellation during folder navigation.
