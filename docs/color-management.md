# Color Management and HDR Roadmap

Lumina should target accurate, predictable preview rather than merely fast bitmap display.

## Current Preview Path

- WIC and LibRaw decode into `BGRA8` preview frames.
- The viewer assumes an sRGB working preview path.
- The details pane reports this explicitly so users can distinguish it from a fully color-managed render path.

## Accurate Color Management Target

1. Read embedded ICC profiles from WIC frames and RAW metadata.
2. Resolve the active monitor ICC profile on the window's current display.
3. Convert source color space to display color space with LittleCMS or Windows Color System.
4. Keep the conversion pipeline explicit: source profile -> linear working space -> display profile.
5. Preserve metadata in `ImageFrame` so the details pane can show source profile, bit depth, transfer function, and display transform.

## HDR / EXR Target

1. Add an `OpenExrDecoder` that reads half/float RGB(A) channels.
2. Keep a float working buffer before producing the preview surface.
3. Add tone mapping options:
   - Reinhard for safe default previews.
   - ACES fitted curve for filmic rendering.
   - Exposure and gamma controls in settings.
4. Detect HDR transfer characteristics where available and label them in the details pane.
5. Later, if core-ui supports HDR swap chains, add true HDR output mode; otherwise preview via tone mapping to SDR.

## Near-Term Implementation Plan

- Add metadata fields to `ImageFrame`: color space name, bit depth, decoder name, HDR flag.
- Add `ColorManagedFrame` or a conversion service beside decoders.
- Add OpenEXR dependency automation after LibRaw is stable.
- Move histogram calculation to support linear or display-referred modes.
