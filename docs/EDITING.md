# Local image editing

Open the editing button beside Rotate. The four tabs are Crop, Adjust, Filters
and Markup. The editor starts from the currently displayed image; opening,
changing, undoing or canceling an edit never writes to the source file.

Editing replaces the image-area page inside the existing main window. It never
opens a separate top-level editing window or nested editor event loop. Save As
returns to viewing after a successful export; Cancel returns after the existing
unsaved-edit check. Window placement/maximized state and a manual viewer's zoom
and scene center are retained. Repeated Edit requests reuse the current session.
Navigation and window close honor the same unsaved/saving protections; obsolete
viewer and editor results cannot replace another mode or session.

- Crop: moveable frame and handles, aspect ratios, straightening, quarter turns,
  horizontal/vertical flips, zoom/fit and actual pixels
- Adjust: brightness, exposure, contrast, highlights, shadows, vignette,
  saturation, warmth, tint and clarity
- Filters: 15 local recipes with strength; alpha-aware automatic enhancement
- Markup: pen, pencil, translucent highlighter, freehand/line/arrows, annotation
  eraser and undoable clear. The eraser removes a whole touched annotation

There is no generative erasing, background removal, downloaded model or image
upload service. All editing runs locally with the existing Pillow/NumPy stack.

## Save As and precision

Save As creates a separate PNG, TIFF, lossless WebP or JPEG, after format review.
JPEG flattens transparency on white. The original path, symlink aliases and
hard-linked aliases are protected. A new output is published atomically; existing
outputs require explicit confirmation. Automated fixtures use synthetic images;
explicitly supplied local diagnostic files are read-only and never included in
the repository or uploaded.

Edits use **8-bit, display-referred SDR**, including when the original is HDR,
EXR or camera RAW. The viewer's current exposure, tone map, channel choice and
rotation are therefore already reflected in the editing input. This is not RAW
development or a full-precision scene-linear editing pipeline. Saving strips
source metadata, including camera and GPS information, as stated in the review.
Straightening retains transparent corners rather than automatically cropping
away image content. Annotations are rasterized in the exported copy; there is
no persistent project/sidecar format.

## Responsiveness and bounds

The original displayed resolution remains available for export. Interactive
preview is bounded to 800 pixels on its long side, with a 1600-pixel settled
preview. There is one active worker and one replaceable pending request; stale
results cannot replace the newest recipe. Color operations process full exports
in bounded row bands. Undo stores up to 60 compact recipes with shared strokes,
not full-resolution pixel copies per command. Annotation point/stroke limits
bound active and historical vector data.

The editor has a separate RGBA8 policy: up to 128,000,000 source pixels, 32,768
pixels per side, a 512-MiB source buffer and a conservative 3-GiB render-workspace
estimate. These are finite allocation bounds, not a guarantee of available RAM;
allocation failures retain the original file and fail the operation. They do
not change HDR/EXR full-float decoding limits. Snapshot conversion uses 128-row
bands, avoiding simultaneous full-frame QImage conversion and Python bytes
copies. A 101,756,928-pixel JPEG was locally checked entering this embedded mode
and rendering a 1095x1461 preview without reducing its full-resolution source.

Unit tests cover pixel transforms, alpha, malformed settings, crop bounds,
export safety, immutable input, stale results, history and three-language UI.
Offscreen 1x/2x captures use a synthetic landscape and do not certify native
Windows dialogs or display-driver behavior. The exact Windows candidate must
pass its native tests before delivery.
