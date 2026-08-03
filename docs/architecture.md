# Lumina Architecture

## Product Shape

Lumina is a native Windows image previewer focused on quick one-file launch and folder navigation. The UI is intentionally minimal: image canvas first, subtle metadata overlays, and a compact zoom control.

## Layers

### UI Layer

`core-ui` owns the window, Fluent-style controls, GPU-accelerated image canvas, and input callbacks.

Main widgets:

- `ImageView`: preview canvas for zoom and pan.
- `Slider`: zoom control in the bottom-right overlay.
- `TitleBar` or top overlay label: file name.
- Labels: dimensions and file size in the bottom-left overlay.

### Controller Layer

`ViewerController` coordinates the current file, folder image order, decoder selection, UI state, and input behavior.

Responsibilities:

- Open a file and index sibling images in the same folder.
- Navigate with left/right arrow keys.
- Push decoded BGRA pixels into `core-ui`.
- Synchronize slider percentage with the active `ImageView` zoom.
- Keep metadata overlays updated.

### Image Layer

The image layer is dependency-inversion friendly. `IImageDecoder` hides decoder-specific details and returns a normalized `ImageFrame`.

Pixel contract:

- Format: BGRA 8-bit premultiplied or straight alpha, depending on backend capability.
- Stride: bytes per row.
- Color: display-referred sRGB for common formats; RAW/EXR backends tone-map before returning.

## Decoder Roadmap

1. `WicDecoder` for common image formats supported by Windows Imaging Component.
2. `LibRawDecoder` for `nef` and other camera RAW files.
3. `OpenExrDecoder` for `exr`, converting HDR data to displayable BGRA.
4. Optional tiled decoder path for very large images.

## Memory Strategy

- Decode only the current image at full preview size in MVP.
- Keep metadata for folder entries, not full pixels.
- Later add a two-entry neighbor cache: previous and next low-resolution previews.
- Add tiled rendering only when large images exceed practical memory limits.

## Input Model

- Mouse wheel zooms around the cursor when using `ImageViewPlus`/advanced image view APIs.
- `Left` opens the previous image in folder order.
- `Right` opens the next image in folder order.
- Slider changes set image zoom directly.
- Viewport callback updates the slider and percentage text.

