"""Local, display-referred image editing without source mutation or AI services.

The source is an already-oriented RGBA display snapshot, never a decoder's RAW
or floating-point buffer. Recipes and undo commands contain only small immutable
parameters and vector annotations, not image copies. Preview work is bounded;
full-size adjustments use scanline bands to avoid full-size float intermediates.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import math
import os
from pathlib import Path
import tempfile
from typing import Callable

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from .decoders import MAX_DIMENSION, MAX_EXR_PIXELS

PREVIEW_EDGE = 1600
PREVIEW_PIXELS = 1_600_000
MAX_HISTORY = 60
MAX_HISTORY_POINTS = 200_000
MAX_STROKES = 200
MAX_STROKE_POINTS = 2048
MAX_TOTAL_POINTS = 100_000
FULL_CROP = (0.0, 0.0, 1.0, 1.0)


class EditorCancelled(RuntimeError):
    pass


def _check(cancelled: Callable[[], bool] | None) -> None:
    if cancelled and cancelled():
        raise EditorCancelled()


@dataclass(frozen=True)
class Adjustments:
    brightness: float = 0.0
    exposure: float = 0.0
    contrast: float = 0.0
    highlights: float = 0.0
    shadows: float = 0.0
    vignette: float = 0.0
    saturation: float = 0.0
    warmth: float = 0.0
    tint: float = 0.0
    clarity: float = 0.0


@dataclass(frozen=True)
class Stroke:
    """Points in original source coordinates; width relative to its short side."""
    points: tuple[tuple[float, float], ...]
    color: tuple[int, int, int, int] = (255, 214, 64, 255)
    width: float = 0.006
    tool: str = 'pen'
    shape: str = 'freehand'


@dataclass(frozen=True)
class EditState:
    turns: int = 0
    flip_x: bool = False
    flip_y: bool = False
    straighten: float = 0.0
    crop: tuple[float, float, float, float] = FULL_CROP
    adjustments: Adjustments = Adjustments()
    filter_name: str = 'original'
    filter_strength: float = 1.0
    auto_enhance: bool = False
    strokes: tuple[Stroke, ...] = ()


@dataclass(frozen=True)
class EditCommand:
    before: EditState
    after: EditState
    label: str


class EditHistory:
    """Bounded recipe commands; immutable strokes are structurally shared."""
    def __init__(self, limit: int = MAX_HISTORY):
        self.state = EditState()
        self.limit = max(1, min(int(limit), MAX_HISTORY))
        self.undo_commands: list[EditCommand] = []
        self.redo_commands: list[EditCommand] = []

    def commit(self, state: EditState, label: str = '') -> bool:
        validate_state(state)
        if state == self.state:
            return False
        self.undo_commands.append(EditCommand(self.state, state, label))
        del self.undo_commands[:-self.limit]
        self.redo_commands.clear()
        self.state = state
        self._trim_points()
        return True

    def _trim_points(self):
        # Count unique immutable strokes, not references shared by recipes.
        # This also bounds repeated clear/repaint cycles, not just command count.
        while self.undo_commands:
            strokes = {id(stroke): stroke for state in
                       [self.state] + [s for c in self.undo_commands + self.redo_commands
                                       for s in (c.before, c.after)] for stroke in state.strokes}
            if sum(len(stroke.points) for stroke in strokes.values()) <= MAX_HISTORY_POINTS:
                break
            self.undo_commands.pop(0)

    def undo(self) -> EditState:
        if self.undo_commands:
            command = self.undo_commands.pop()
            self.redo_commands.append(command)
            self.state = command.before
        return self.state

    def redo(self) -> EditState:
        if self.redo_commands:
            command = self.redo_commands.pop()
            self.undo_commands.append(command)
            self.state = command.after
        return self.state

    def reset(self) -> bool:
        return self.commit(EditState(), 'Reset')


def validate_state(state: EditState) -> None:
    if not isinstance(state.turns, int) or not math.isfinite(state.straighten) or abs(state.straighten) > 45:
        raise ValueError('Invalid rotation.')
    left, top, right, bottom = state.crop
    if not all(math.isfinite(v) for v in state.crop) or not (0 <= left < right <= 1 and 0 <= top < bottom <= 1):
        raise ValueError('Crop must be inside the image.')
    for name, value in vars(state.adjustments).items():
        limit = 4 if name == 'exposure' else 100
        if not math.isfinite(value) or abs(value) > limit:
            raise ValueError(f'Invalid {name}.')
    if state.filter_name not in FILTERS or not 0 <= state.filter_strength <= 1:
        raise ValueError('Invalid filter.')
    if len(state.strokes) > MAX_STROKES or sum(len(s.points) for s in state.strokes) > MAX_TOTAL_POINTS:
        raise ValueError('The annotation limit was reached. Save a copy before adding more.')
    for stroke in state.strokes:
        if (not 1 <= len(stroke.points) <= MAX_STROKE_POINTS or not 0 < stroke.width <= .25
                or stroke.tool not in ('pen', 'pencil', 'highlighter')
                or stroke.shape not in ('freehand', 'line', 'arrow', 'double_arrow')
                or len(stroke.color) != 4 or any(not 0 <= c <= 255 for c in stroke.color)
                or any(not math.isfinite(v) or abs(v) > 4 for p in stroke.points for v in p)):
            raise ValueError('Invalid annotation.')


def oriented_size(size: tuple[int, int], state: EditState) -> tuple[int, int]:
    return size[::-1] if state.turns % 2 else size


def transform_point(point, state: EditState, source_size, *, inverse=False):
    """Map normalized source↔geometry coordinates, including aspect-correct tilt."""
    x, y = point
    width, height = oriented_size(source_size, state)
    angle = math.radians(state.straighten)
    c, s = math.cos(angle), math.sin(angle)
    if inverse:
        dx, dy = (x - .5) * width, (y - .5) * height
        x, y = (c * dx + s * dy) / width + .5, (-s * dx + c * dy) / height + .5
        x = 1 - x if state.flip_x else x
        y = 1 - y if state.flip_y else y
        for _ in range(state.turns % 4):
            x, y = y, 1 - x
    else:
        for _ in range(state.turns % 4):
            x, y = 1 - y, x
        x = 1 - x if state.flip_x else x
        y = 1 - y if state.flip_y else y
        dx, dy = (x - .5) * width, (y - .5) * height
        x, y = (c * dx - s * dy) / width + .5, (s * dx + c * dy) / height + .5
    return x, y


def change_geometry(state: EditState, source_size, **changes) -> EditState:
    """Keep crop framing attached to image for quarter turns and flips."""
    updated = replace(state, **changes)
    if 'straighten' not in changes:
        l, t, r, b = state.crop
        points = [transform_point(transform_point(p, state, source_size, inverse=True), updated, source_size)
                  for p in ((l, t), (r, t), (r, b), (l, b))]
        xs, ys = zip(*points)
        updated = replace(updated, crop=(max(0., min(xs)), max(0., min(ys)), min(1., max(xs)), min(1., max(ys))))
    return updated


def crop_box(size, crop):
    w, h = size
    left, top = min(w-1, max(0, round(crop[0] * w))), min(h-1, max(0, round(crop[1] * h)))
    right, bottom = min(w, round(crop[2] * w)), min(h, round(crop[3] * h))
    return left, top, max(left + 1, right), max(top + 1, bottom)


def bounded_size(size, edge=PREVIEW_EDGE, pixels=PREVIEW_PIXELS):
    w, h = size
    scale = min(1., edge / max(w, h), math.sqrt(pixels / (w * h)))
    return max(1, round(w * scale)), max(1, round(h * scale))


# Fixed local recipes. None relies on network services or downloaded models.
FILTERS = {
    'original': ('Original', Adjustments(), None),
    'vivid': ('Vivid', Adjustments(contrast=12, saturation=28), None),
    'golden': ('Golden', Adjustments(warmth=30, saturation=12, highlights=-12), None),
    'radiant': ('Radiant', Adjustments(brightness=8, shadows=15, saturation=12), None),
    'warm_contrast': ('Warm contrast', Adjustments(warmth=15, contrast=22), None),
    'soft': ('Soft', Adjustments(contrast=-20, saturation=-10, brightness=5), None),
    'cool': ('Cool', Adjustments(warmth=-25, saturation=-5), None),
    'dramatic': ('Dramatic', Adjustments(contrast=30, shadows=-15, saturation=12), None),
    'muted': ('Muted', Adjustments(saturation=-38, contrast=-8), None),
    'mono': ('Mono', Adjustments(saturation=-100), None),
    'silver': ('Silver', Adjustments(saturation=-100, contrast=25, shadows=8), None),
    'warm_mono': ('Warm mono', Adjustments(saturation=-100), 'sepia'),
    'noir': ('Noir', Adjustments(saturation=-100, contrast=45, vignette=25), None),
    'vintage': ('Vintage', Adjustments(saturation=-24, warmth=15, contrast=-10), 'fade'),
    'film': ('Film', Adjustments(saturation=-12, contrast=10, tint=8), 'fade'),
}


def _adjust_rgb(rgb: np.ndarray, adjustment: Adjustments, y0: int, size) -> np.ndarray:
    a = adjustment
    if a.exposure:
        rgb *= 2.0 ** a.exposure
    if a.brightness:
        rgb += a.brightness / 400
    if a.contrast:
        rgb = (rgb - .5) * (2.0 ** (a.contrast / 70)) + .5
    if a.highlights or a.shadows:
        lum = np.clip(rgb @ np.array([.2126, .7152, .0722], dtype=np.float32), 0, 1)[..., None]
        rgb += a.highlights / 300 * lum ** 2 + a.shadows / 300 * (1 - lum) ** 2
    if a.saturation:
        lum = rgb @ np.array([.2126, .7152, .0722], dtype=np.float32)
        rgb = lum[..., None] + (rgb - lum[..., None]) * (1 + a.saturation / 100)
    if a.warmth:
        rgb += np.array([1, .12, -1], dtype=np.float32) * (a.warmth / 500)
    if a.tint:
        rgb += np.array([.5, -1, .5], dtype=np.float32) * (a.tint / 650)
    if a.vignette:
        width, height = size
        x = np.linspace(-1, 1, width, dtype=np.float32)[None, :]
        y = ((np.arange(y0, y0 + len(rgb), dtype=np.float32) + .5) / height * 2 - 1)[:, None]
        mask = np.clip((x * x + y * y) / 2, 0, 1)[..., None]
        rgb *= 1 - mask * (a.vignette / 125)
    return np.clip(rgb, 0, 1)


def _color(image: Image.Image, state, cancelled=None):
    a = state.adjustments
    if a == Adjustments() and state.filter_name == 'original' and not state.auto_enhance:
        return image
    result = Image.new('RGBA', image.size)
    filter_adjustment, special = FILTERS[state.filter_name][1:]
    levels = None
    if state.auto_enhance:
        sample = image.resize(bounded_size(image.size, 256, 65536), Image.Resampling.BILINEAR)
        values = np.asarray(sample)
        visible = values[..., 3] > 0
        if visible.any():
            levels = np.percentile(values[..., :3][visible], [1, 99], axis=0).astype(np.float32) / 255
            if np.any(levels[1] - levels[0] < .04):
                levels = None
    width, height = image.size
    # A radius proportional to source short side keeps preview/export consistent.
    radius = max(.5, min(width, height) / 180)
    overlap = math.ceil(radius * 4)
    for y in range(0, height, 128):
        _check(cancelled)
        end = min(height, y + 128)
        pixels = np.asarray(image.crop((0, y, width, end)))
        rgb = pixels[..., :3].astype(np.float32) / 255
        if levels is not None:
            enhanced = np.clip((rgb - levels[0]) / (levels[1] - levels[0]), 0, 1)
            rgb = rgb * .25 + enhanced * .75
        rgb = _adjust_rgb(rgb, a, y, image.size)
        if a.clarity:
            start, stop = max(0, y - overlap), min(height, end + overlap)
            # Blur color only; alpha is never edited by photographic adjustments.
            blurred = image.crop((0, start, width, stop)).convert('RGB').filter(ImageFilter.GaussianBlur(radius))
            blur = np.asarray(blurred)[y - start:end - start].astype(np.float32) / 255
            original = pixels[..., :3].astype(np.float32) / 255
            rgb = np.clip(rgb + (original - blur) * a.clarity / 65, 0, 1)
        if state.filter_name != 'original' and state.filter_strength:
            filtered = _adjust_rgb(rgb.copy(), filter_adjustment, y, image.size)
            if special == 'sepia':
                filtered *= np.array([1.08, 1., .82], dtype=np.float32)
            elif special == 'fade':
                filtered = filtered * .9 + .055
            rgb = rgb * (1 - state.filter_strength) + np.clip(filtered, 0, 1) * state.filter_strength
        rgba = np.empty_like(pixels)
        rgba[..., :3] = np.clip(np.rint(rgb * 255), 0, 255).astype(np.uint8)
        rgba[..., 3] = pixels[..., 3]
        result.paste(Image.fromarray(rgba), (0, y))
    return result


def _draw_strokes(image, strokes, state, source_size, cancelled=None):
    if not strokes:
        return image
    width, height = image.size
    overlay = Image.new('RGBA', image.size, (0, 0, 0, 0))
    # Separate per-stroke tile makes highlighter overlaps proper alpha composites
    # without allocating a full-size image for every annotation.
    for stroke in strokes:
        _check(cancelled)
        points = [transform_point(p, state, source_size) for p in stroke.points]
        points = [(x * width, y * height) for x, y in points]
        line_width = max(1, round(stroke.width * min(image.size)))
        padding = max(4, line_width * 5)
        x0 = max(0, int(min(p[0] for p in points) - padding))
        y0 = max(0, int(min(p[1] for p in points) - padding))
        x1 = min(width, math.ceil(max(p[0] for p in points) + padding + 1))
        y1 = min(height, math.ceil(max(p[1] for p in points) + padding + 1))
        if x1 <= x0 or y1 <= y0:
            continue
        tile = Image.new('RGBA', (x1 - x0, y1 - y0), (0, 0, 0, 0))
        draw = ImageDraw.Draw(tile)
        local = [(x - x0, y - y0) for x, y in points]
        color = stroke.color[:3] + (round(stroke.color[3] * (.32 if stroke.tool == 'highlighter' else .65 if stroke.tool == 'pencil' else 1)),)
        if stroke.shape != 'freehand' and len(local) > 1:
            local = [local[0], local[-1]]
        if len(local) > 1:
            draw.line(local, fill=color, width=line_width, joint='curve')
        r = line_width / 2
        for x, y in (local if len(local) == 1 else (local[0], local[-1])):
            draw.ellipse((x-r, y-r, x+r, y+r), fill=color)
        if stroke.shape in ('arrow', 'double_arrow') and len(local) > 1:
            ends = [(local[-1], local[0])]
            if stroke.shape == 'double_arrow':
                ends.append((local[0], local[-1]))
            for (x, y), (ox, oy) in ends:
                angle = math.atan2(y-oy, x-ox)
                length = max(8, line_width * 4)
                for delta in (-.55, .55):
                    tip = (x - math.cos(angle + delta) * length, y - math.sin(angle + delta) * length)
                    draw.line([(x, y), tip], fill=color, width=line_width)
        overlay.alpha_composite(tile, (x0, y0))
    return Image.alpha_composite(image, overlay)


def render(source: Image.Image, state: EditState, *, preview=False, include_crop=True, cancelled=None) -> Image.Image:
    """Render RGBA without mutating source; geometry uses original pixel counts."""
    validate_state(state)
    if source.mode != 'RGBA' or source.width * source.height > MAX_EXR_PIXELS or max(source.size) > MAX_DIMENSION:
        raise ValueError('Editor needs an RGBA image within the viewer safety limit.')
    _check(cancelled)
    size = bounded_size(source.size) if preview else source.size
    image = source.resize(size, Image.Resampling.LANCZOS) if size != source.size else source.copy()
    turns = state.turns % 4
    if turns:
        image = image.transpose({1: Image.Transpose.ROTATE_270, 2: Image.Transpose.ROTATE_180, 3: Image.Transpose.ROTATE_90}[turns])
    if state.flip_x:
        image = image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    if state.flip_y:
        image = image.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    if state.straighten:
        # Fixed framing, transparent corners. PNG/TIFF/WebP preserve these pixels.
        image = image.rotate(-state.straighten, resample=Image.Resampling.BICUBIC, expand=False, fillcolor=(0, 0, 0, 0))
    _check(cancelled)
    image = _color(image, state, cancelled)
    image = _draw_strokes(image, state.strokes, state, source.size, cancelled)
    if include_crop and state.crop != FULL_CROP:
        image = image.crop(crop_box(image.size, state.crop))
    _check(cancelled)
    return image


def export_image(source: Image.Image, state: EditState, destination: Path, original_path: Path,
                 format: str, *, expected=None, cancelled=None) -> Path:
    """Atomic explicit Save As, reusing the viewer's alias/path safety checks."""
    from .actions import validated_destination, _file_signature
    destination = validated_destination(destination, original_path, format)
    if _file_signature(destination) != expected:
        raise FileExistsError('The destination changed. Choose Save As again.')
    pixels = render(source, state, cancelled=cancelled)
    options = {}
    if format == 'JPEG':
        flattened = Image.new('RGB', pixels.size, 'white')
        flattened.paste(pixels, mask=pixels.getchannel('A'))
        pixels = flattened
        options = {'quality': 95, 'subsampling': 0}
    elif format == 'WEBP':
        options = {'lossless': True, 'exact': True}
    elif format == 'TIFF':
        options = {'compression': 'tiff_deflate'}
    temporary = None
    try:
        fd, name = tempfile.mkstemp(prefix='.luma-edit-', suffix='.tmp', dir=destination.parent)
        temporary = Path(name)
        with os.fdopen(fd, 'w+b') as stream:
            pixels.save(stream, format=format, **options)
            stream.flush()
            os.fsync(stream.fileno())
        _check(cancelled)
        validated_destination(destination, original_path, format)
        if _file_signature(destination) != expected:
            raise FileExistsError('The destination changed. Choose Save As again.')
        if expected is None:
            if os.name == 'nt':
                os.rename(temporary, destination)
            else:
                os.link(temporary, destination)
        else:
            os.replace(temporary, destination)
        return destination
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
