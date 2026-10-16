#!/usr/bin/env python3
"""Reproducible synthetic EXR decode/render/peak-memory measurements.

Generation is strip-bounded and uses the official OpenEXR writer. Benchmarks
run one fresh child per sample, serially; imports and process startup are not
included in stage times. OS caches are not evicted. These synthetic inputs do
not establish performance for an unavailable user's file or for Windows Shell.

Example:
  python scripts/benchmark_exr_decode.py --fixtures /tmp/exr-bench --generate
  python scripts/benchmark_exr_decode.py --fixtures /tmp/exr-bench \
    --baseline /tmp/released-decoders.py --repeat 3 > /tmp/exr-results.jsonl
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CASES = (
    ('HALF', 'ZIP', ('R', 'G', 'B', 'A')),
    ('FLOAT', 'ZIP', ('beauty.R', 'beauty.G', 'beauty.B', 'beauty.A', 'depth.Z', 'normal.X', 'normal.Y', 'normal.Z')),
    ('HALF', 'PIZ', ('R', 'G', 'B')),
    ('FLOAT', 'PIZ', ('R', 'G', 'B', 'A')),
    ('HALF', 'DWAA', ('R', 'G', 'B', 'A')),
    ('FLOAT', 'DWAA', ('R', 'G', 'B')),
)


def generate(destination: Path, width=4096, height=2048):
    import Imath
    import numpy as np
    import OpenEXR
    destination.mkdir(parents=True, exist_ok=True)
    OpenEXR.set_global_thread_count(4)
    for sample_type, compression, channels in CASES:
        path = destination / f'structured-{width}x{height}-{sample_type}-{compression}.exr'
        dtype = np.dtype('float16' if sample_type == 'HALF' else 'float32')
        header = OpenEXR.Header(width, height)
        header['compression'] = Imath.Compression(getattr(Imath.Compression, compression + '_COMPRESSION'))
        header['channels'] = {name: Imath.Channel(Imath.PixelType(getattr(Imath.PixelType, sample_type))) for name in channels}
        # A nonzero, negative data-window origin tests coordinate handling.
        header['dataWindow'] = Imath.Box2i(Imath.V2i(-17, 23), Imath.V2i(width - 18, height + 22))
        rng = np.random.default_rng(20261016)
        xx = np.arange(width, dtype=np.float32)[None, :]
        output = OpenEXR.OutputFile(str(path), header)
        start = time.perf_counter()
        try:
            for y0 in range(0, height, 32):
                yy = np.arange(y0, min(height, y0 + 32), dtype=np.float32)[:, None]
                noise = rng.integers(0, 256, (len(yy), width), dtype=np.uint16).astype(np.float32) / 8192
                base = (.05 + xx / width * 3 + yy / height + .1 * np.sin(xx * .23)
                        + .125 * ((xx // 8 + yy // 8) % 2) + noise)
                payload = {}
                for index, name in enumerate(channels):
                    if name.rsplit('.', 1)[-1] == 'A':
                        plane = np.broadcast_to(.5 + .5 * yy / height, base.shape)
                    else:
                        plane = base * (1 + .125 * index)
                    payload[name] = plane.astype(dtype).tobytes()
                output.writePixels(payload, len(yy))
        finally:
            output.close()
        print(json.dumps({'generated': str(path), 'bytes': path.stat().st_size,
                          'seconds': time.perf_counter() - start}), flush=True)


def rss_mib():
    try:
        import resource
        value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return value / (1024 ** 2 if sys.platform == 'darwin' else 1024)
    except ImportError:
        return None


def sample(args):
    import numpy as np
    from PIL import Image
    import OpenEXR
    if args.variant == 'baseline':
        spec = importlib.util.spec_from_file_location('baseline_decoder', args.baseline)
        decoder = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = decoder
        spec.loader.exec_module(decoder)
    else:
        from luma import decoders as decoder
    record = {'file': str(args.path), 'variant': args.variant, 'mode': args.mode,
              'bytes': args.path.stat().st_size, 'python': platform.python_version(),
              'numpy': np.__version__, 'openexr': OpenEXR.__version__, 'platform': platform.platform(),
              'conditions': 'fresh process per sample; OS cache not evicted; seeded structured/noise synthetic input'}
    started = time.perf_counter()
    try:
        if args.mode == 'thumbnail' and args.variant == 'patched':
            image = decoder.decode_exr_thumbnail(args.path, args.edge)
        else:
            if args.mode == 'thumbnail':
                decoder.MAX_PIXELS = 16_000_000
                decoder.MAX_DECODE_BYTES = 512 * 1024 * 1024
            image = decoder.decode_image(args.path)
        decoded = time.perf_counter()
        record['decode_s'] = decoded - started
        record['decode_peak_rss_mib'] = rss_mib()
        render_started = time.perf_counter()
        rgba = decoder.render_rgba(image)
        rendered = time.perf_counter()
        record['render_s'] = rendered - render_started
        if args.mode == 'thumbnail':
            preview = Image.fromarray(rgba)
            preview.thumbnail((args.edge, args.edge), Image.Resampling.LANCZOS, reducing_gap=3.0)
            record['thumbnail_size'] = list(preview.size)
        finished = time.perf_counter()
        record['resize_s'] = finished - rendered
        record['total_s'] = finished - started
        record['peak_rss_mib'] = rss_mib()
        record['source_pixel_sha256'] = hashlib.sha256(image.pixels).hexdigest()
        record['rendered_sha256'] = hashlib.sha256(rgba).hexdigest()
        record['width'], record['height'] = image.width, image.height
        record['status'] = 'ok'
    except Exception as exc:
        record.update(status='rejected', error=f'{type(exc).__name__}: {exc}',
                      total_s=time.perf_counter() - started, peak_rss_mib=rss_mib())
    print(json.dumps(record), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixtures', type=Path)
    parser.add_argument('--generate', action='store_true')
    parser.add_argument('--width', type=int, default=4096)
    parser.add_argument('--height', type=int, default=2048)
    parser.add_argument('--baseline', type=Path)
    parser.add_argument('--repeat', type=int, default=3)
    parser.add_argument('--modes', nargs='+', choices=('full', 'thumbnail'), default=['full', 'thumbnail'])
    parser.add_argument('--edge', type=int, default=256)
    parser.add_argument('--path', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--variant', choices=('baseline', 'patched'), help=argparse.SUPPRESS)
    parser.add_argument('--mode', choices=('full', 'thumbnail'), help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.path:
        sample(args)
        return
    if not args.fixtures or not 1 <= args.repeat <= 20:
        parser.error('--fixtures and repeat between 1 and 20 are required')
    if args.generate:
        if not 1 <= args.width <= 8192 or not 1 <= args.height <= 8192:
            parser.error('generation dimensions must be between 1 and 8192')
        generate(args.fixtures, args.width, args.height)
        return
    if not args.baseline or not args.baseline.is_file():
        parser.error('--baseline must name an unmodified released decoder module')
    for path in sorted(args.fixtures.glob('*.exr')):
        for mode in args.modes:
            for iteration in range(args.repeat):
                for variant in ('baseline', 'patched'):
                    command = [sys.executable, str(Path(__file__).resolve()), '--path', str(path),
                               '--variant', variant, '--mode', mode, '--baseline', str(args.baseline),
                               '--edge', str(args.edge)]
                    result = subprocess.run(command, capture_output=True, text=True, timeout=120)
                    if result.returncode:
                        raise RuntimeError(f'Benchmark child failed: {result.stderr[-2000:]}')
                    record = json.loads(result.stdout)
                    record['sample'] = iteration
                    print(json.dumps(record), flush=True)


if __name__ == '__main__':
    main()
