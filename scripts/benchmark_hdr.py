#!/usr/bin/env python3
"""Reproducible HDR fixtures and decode/render timings (no disk-cache eviction).

Run with ``python scripts/benchmark_hdr.py --fixtures /tmp/luma-hdr --generate``.
Use ``--decoder /path/to/baseline_decoders.py`` for an unmodified-release module.
Every invocation is a fresh Python process; repeated samples reuse the OS file
cache. Fixture generation happens outside timings. No network inputs are used.
"""
from __future__ import annotations
import argparse
import gc
import hashlib
import importlib.util
import json
from pathlib import Path
import platform
import subprocess
import sys
import time

import numpy as np

try:
    import resource
except ImportError:  # Windows has no stdlib resource module.
    resource = None

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def write_fixture(path, width, height, rle):
    """A varied environment-map-like gradient, constant exponent runs and highlights.

    The deliberately independent writer uses literal RGB packets and exponent
    repeat packets. Flat/RLE fixtures encode identical RGBE pixels.
    """
    x = np.arange(width, dtype=np.uint32)
    with path.open('wb') as output:
        output.write(f'#?RADIANCE\nFORMAT=32-bit_rle_rgbe\n\n-Y {height} +X {width}\n'.encode())
        for y in range(height):
            scan = np.empty((width, 4), dtype=np.uint8)
            scan[:, 0] = 32 + ((x * 191 // width + y * 67 // height) % 224)
            scan[:, 1] = 16 + ((x * 131 // width + y * 101 // height) % 240)
            scan[:, 2] = 64 + ((x * 127 // width + y * 79 // height) % 192)
            scan[:, 3] = 124 + ((x // 256 + y // 128) % 12)
            if not rle:
                output.write(scan.tobytes())
                continue
            output.write(bytes((2, 2, width >> 8, width & 255)))
            for channel in range(4):
                for start in range(0, width, 127):
                    values = scan[start:start + 127, channel]
                    if np.all(values == values[0]):
                        output.write(bytes((128 + len(values), int(values[0]))))
                    else:
                        output.write(bytes((len(values),)) + values.tobytes())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixtures', type=Path, required=True)
    parser.add_argument('--generate', action='store_true')
    parser.add_argument('--sizes', nargs='+', choices=['4k', '8k'], default=['4k', '8k'])
    parser.add_argument('--encoding', choices=['flat', 'rle', 'both'], default='both')
    parser.add_argument('--decoder', type=Path)
    parser.add_argument('--repeat', type=int, default=3)
    parser.add_argument('--fresh-process', action='store_true',
                        help='Launch an isolated child per sample; wall time includes imports and checksum validation.')
    parser.add_argument('--profile', type=Path)
    args = parser.parse_args()
    args.fixtures.mkdir(parents=True, exist_ok=True)
    has_decoded = False
    if args.decoder:
        spec = importlib.util.spec_from_file_location('benchmark_decoders', args.decoder)
        decoder = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = decoder
        spec.loader.exec_module(decoder)
    else:
        from luma import decoders as decoder
    for size in args.sizes:
        width, height = {'4k': (3840, 2160), '8k': (7680, 4320)}[size]
        for encoding in ('flat', 'rle'):
            if args.encoding not in ('both', encoding):
                continue
            path = args.fixtures / f'gradient-{size}-{encoding}.hdr'
            if args.generate and not path.exists():
                write_fixture(path, width, height, encoding == 'rle')
            if not path.exists():
                raise FileNotFoundError(path)
            for repeat in range(args.repeat):
                if args.fresh_process:
                    command = [sys.executable, str(Path(__file__).resolve()), '--fixtures',
                               str(args.fixtures), '--sizes', size, '--encoding', encoding,
                               '--repeat', '1']
                    if args.decoder:
                        command.extend(['--decoder', str(args.decoder)])
                    start = time.perf_counter()
                    completed = subprocess.run(command, check=True, capture_output=True, text=True)
                    wall = time.perf_counter() - start
                    result = json.loads(completed.stdout)
                    result.update(sample=repeat, process_wall_s=wall,
                                  process_state='fresh isolated process; process_wall_s includes imports and checksum validation')
                    print(json.dumps(result), flush=True)
                    continue
                gc.collect()
                profiler = None
                if args.profile:
                    import cProfile
                    profiler = cProfile.Profile()
                    profiler.enable()
                start = time.perf_counter()
                decoded = decoder.decode_image(path)
                decoded_at = time.perf_counter()
                rgba = decoder.render_rgba(decoded)
                rendered_at = time.perf_counter()
                if profiler:
                    profiler.disable()
                    profiler.dump_stats(str(args.profile.with_name(f'{args.profile.stem}-{size}-{encoding}-{repeat}.prof')))
                print(json.dumps({'fixture': path.name, 'size_bytes': path.stat().st_size,
                    'sample': repeat, 'process_state': 'first decode in process' if not has_decoded else 'warm process',
                    'disk_cache': 'OS cache not evicted; fixtures may be warm',
                    'python': platform.python_version(), 'numpy': np.__version__, 'platform': platform.platform(),
                    'decode_s': decoded_at - start, 'render_s': rendered_at - decoded_at,
                    'total_s': rendered_at - start, 'rgba_sha256': hashlib.sha256(rgba).hexdigest(),
                    'source_sample': decoded.pixels[height // 2, width // 2].tolist(),
                    'peak_rss_mib': (resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                                     / (1024 ** 2 if sys.platform == 'darwin' else 1024)) if resource else None}), flush=True)
                has_decoded = True
                del decoded, rgba


if __name__ == '__main__':
    main()
