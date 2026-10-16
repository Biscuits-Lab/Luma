#!/usr/bin/env python3
"""Generate one deterministic, moderate-entropy EXR with bounded strip memory.

The default 8192-square HALF RGBA ZIP case is approximately 66 MB with the
pinned OpenEXR 3.5.2 library. It contains structured detail and independent
seeded RGB perturbations; it is not a substitute for an unavailable user file.
No full-resolution source array is allocated. No files are uploaded.
Compressed bytes can differ across codec builds; source values follow the recipe.

Example:
  python scripts/generate_exr_moderate_fixture.py --output moderate-8k.exr
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import time


DEFAULT_SEED = 20261016
DEFAULT_STRIP_ROWS = 32
MAX_EDGE = 8192


def write_moderate_exr_fixture(path: Path, *, width: int = MAX_EDGE,
                               height: int = MAX_EDGE,
                               strip_rows: int = DEFAULT_STRIP_ROWS,
                               seed: int = DEFAULT_SEED) -> dict:
    """Write HALF RGBA ZIP data; return its recipe identity, size and digest.

    A fixed seed, geometry and strip height produce identical source values.
    Changing strip height changes the RNG's per-channel interleaving and thus
    the fixture. Call serially, before benchmark measurements or native jobs.
    """
    if any(isinstance(v, bool) or not isinstance(v, int) for v in (width, height, strip_rows, seed)):
        raise ValueError('Fixture dimensions, strip height and seed must be integers')
    if not (1 <= width <= MAX_EDGE and 1 <= height <= MAX_EDGE
            and 1 <= strip_rows <= 64 and 0 <= seed < 2**64):
        raise ValueError('Fixture dimensions, strip height or seed exceed the bounded generator limits')
    import Imath
    import numpy as np
    import OpenEXR

    path = Path(path)
    header = OpenEXR.Header(width, height)
    header['compression'] = Imath.Compression(Imath.Compression.ZIP_COMPRESSION)
    header['channels'] = {name: Imath.Channel(Imath.PixelType(Imath.PixelType.HALF)) for name in 'RGBA'}
    rng = np.random.default_rng(seed)
    x = np.arange(width, dtype=np.int32)[None, :]
    started = time.perf_counter()
    output = OpenEXR.OutputFile(str(path), header)
    try:
        for y0 in range(0, height, strip_rows):
            y = np.arange(y0, min(height, y0 + strip_rows), dtype=np.int32)[:, None]
            bases = (
                .125 + ((x // 4 + y // 4) % 256) / 512,
                .125 + ((x // 16 + y // 16) % 128) / 256,
                .125 + ((x // 8 + y // 8) % 2) / 4 + (x // 256) / 128,
            )
            payload = {}
            for name, base in zip('RGB', bases):
                noise = rng.integers(0, 2, (len(y), width), dtype=np.uint8).astype(np.float32) / 2048
                payload[name] = np.asarray(base + noise, dtype=np.float16).tobytes()
            payload['A'] = np.ones((len(y), width), np.float16).tobytes()
            output.writePixels(payload, len(y))
    finally:
        output.close()
    with path.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    return {
        'fixture': path.name, 'width': width, 'height': height,
        'channels': 'RGBA', 'sample_type': 'HALF', 'compression': 'ZIP',
        'seed': seed, 'strip_rows': strip_rows, 'openexr': OpenEXR.__version__,
        'size_bytes': path.stat().st_size, 'sha256': digest,
        'generation_s': time.perf_counter() - started,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--width', type=int, default=MAX_EDGE)
    parser.add_argument('--height', type=int, default=MAX_EDGE)
    parser.add_argument('--strip-rows', type=int, default=DEFAULT_STRIP_ROWS)
    parser.add_argument('--seed', type=int, default=DEFAULT_SEED)
    args = parser.parse_args()
    import OpenEXR
    # This CLI is a standalone generator, never a concurrent viewer decode.
    OpenEXR.set_global_thread_count(4)
    report = write_moderate_exr_fixture(args.output, width=args.width,
        height=args.height, strip_rows=args.strip_rows, seed=args.seed)
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
