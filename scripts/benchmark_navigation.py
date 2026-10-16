#!/usr/bin/env python3
"""Generate fixed JPEG fixtures and measure full-resolution Qt image switching.

Run baseline and candidate in separate processes with the same --fixtures folder.
No disk caches are evicted. --pause allows the viewer's bounded idle prefetch to
run between user actions. Report that condition rather than calling prefetch an
uncached decode. Timings are local observations, never a target-hardware promise.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import statistics
import sys
import time


def generate(directory):
    import numpy as np
    from PIL import Image
    directory.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(41016)
    for width, height in ((3840, 2160), (6000, 4000)):
        for index in range(3):
            x = np.linspace(0, 1, width, dtype=np.float32)[None, :]
            y = np.linspace(0, 1, height, dtype=np.float32)[:, None]
            pixels = np.empty((height, width, 3), np.uint8)
            for channel in range(3):
                value = (110 + 60 * np.sin((x * 11 + y * 7 + index + channel) * 3)
                         + 40 * np.cos((x * 3 - y * 8 + channel) * 2))
                noise = rng.normal(0, 3, (height, width)).astype(np.float32)
                pixels[..., channel] = np.clip(value + noise, 0, 255).astype(np.uint8)
            path = directory / f'photo-{width}x{height}-{index}.jpg'
            Image.fromarray(pixels).save(path, quality=92, subsampling=0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--fixtures', type=Path, required=True)
    parser.add_argument('--generate', action='store_true')
    parser.add_argument('--pause', type=float, default=0.4)
    parser.add_argument('--mode', choices=('open', 'navigate'), default='navigate')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.generate:
        generate(args.fixtures)
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    imported = time.perf_counter()
    sys.path.insert(0, str(args.repo.resolve()))
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QImage, QPixmap
    from luma.decoders import decode_image, render_rgba
    from luma.app import ViewerWindow
    import_seconds = time.perf_counter() - imported
    app = QApplication.instance() or QApplication([])
    directory = args.fixtures.resolve()
    report = {'python': platform.python_version(), 'platform': platform.platform(),
              'qt_platform': os.environ['QT_QPA_PLATFORM'], 'imports_seconds': import_seconds,
              'conditions': 'fresh Python process; OS-warm files, no disk-cache eviction',
              'pause_seconds': args.pause, 'operation': args.mode, 'fixtures': {},
              'stage_times': {}, 'ui_switches': {}}
    def measure(function):
        start = time.perf_counter(); value = function()
        return value, time.perf_counter() - start
    def pump_until(predicate):
        deadline = time.perf_counter() + 30
        while not predicate():
            app.processEvents(); time.sleep(0.001)
            if time.perf_counter() > deadline:
                raise RuntimeError('Timed out waiting for image display')
        app.processEvents()
    for size in ('3840x2160', '6000x4000'):
        for index in range(3):
            path = directory / f'photo-{size}-{index}.jpg'
            report['fixtures'][path.name] = {'bytes': path.stat().st_size,
                                             'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
        path = directory / f'photo-{size}-0.jpg'
        data, decoded = measure(lambda: decode_image(path))
        rgba, rendered = measure(lambda: render_rgba(data))
        height, width = rgba.shape[:2]
        image, copied = measure(lambda: QImage(rgba.data, width, height, rgba.strides[0], QImage.Format.Format_RGBA8888).copy())
        pixmap, converted = measure(lambda: QPixmap.fromImage(image))
        report['stage_times'][size] = dict(decode=decoded, render=rendered, qimage_copy=copied, qpixmap=converted)
        del data, rgba, image, pixmap
        window = ViewerWindow(); window.show(); app.processEvents()
        samples = []
        sequence = [0, 1, 2, 1, 0, 1, 2, 1, 0]
        try:
            for sample, index in enumerate(sequence):
                path = directory / f'photo-{size}-{index}.jpg'
                start = time.perf_counter()
                if sample == 0 or args.mode == 'open':
                    window.open_path(path)
                else:
                    window.select_index(window.files.index(path))
                pump_until(lambda: window.displayed_path == path)
                samples.append(time.perf_counter() - start)
                pump_until(lambda: path in window.files)
                deadline = time.perf_counter() + args.pause
                while time.perf_counter() < deadline:
                    app.processEvents(); time.sleep(0.005)
        finally:
            window.close(); app.processEvents()
        report['ui_switches'][size] = {'sequence': sequence, 'seconds': samples, 'first': samples[0],
            'next_after_pause_median': statistics.median(samples[1:3]),
            'repeat_median': statistics.median(samples[3:])}
    text = json.dumps(report, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + '\n', encoding='utf-8')
    print(text)


if __name__ == '__main__':
    main()
