#!/usr/bin/env python3
"""Measure full-resolution EXR read, render and Qt conversion separately.

Supply generated or explicitly authorized local fixtures. This script never
writes inputs or evicts the OS file cache. Run samples serially on an idle host.
Qt offscreen measurements do not certify native Windows display latency.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import gc
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import sys
import time

import numpy as np

try:
    import resource
except ImportError:
    resource = None

ROOT = Path(__file__).resolve().parents[1]


def parallel_render(decoder, data, workers):
    """Experimental exact-output bands; not a production rendering path."""
    output = np.empty((data.height, data.width, 4), np.uint8)

    def bands(index):
        for start in range(index * 128, data.height, workers * 128):
            block = decoder.ImageData(data.path, data.pixels[start:start + 128],
                                      data.linear, data.metadata, data.channel_names)
            output[start:start + 128] = decoder.render_rgba(block)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        list(executor.map(bands, range(workers)))
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('paths', type=Path, nargs='+')
    parser.add_argument('--repo', type=Path, default=ROOT)
    parser.add_argument('--decoder', type=Path, help='Unmodified baseline decoder module')
    parser.add_argument('--repeat', type=int, default=3)
    parser.add_argument('--threads', type=int, nargs='*', default=[], choices=[2, 4])
    parser.add_argument('--viewer-render', action='store_true',
                        help='Also measure the production ImageService full-resolution renderer')
    parser.add_argument('--ui', action='store_true', help='Measure a fresh viewer opening each sample')
    parser.add_argument('--profile', type=Path)
    args = parser.parse_args()
    sys.path.insert(0, str(args.repo.resolve()))
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtGui import QImage, QPixmap
    from PySide6.QtWidgets import QApplication
    from PySide6 import __version__ as pyside_version
    import OpenEXR
    app = QApplication.instance() or QApplication([])
    if args.decoder:
        spec = importlib.util.spec_from_file_location('benchmark_decoder', args.decoder)
        decoder = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = decoder
        spec.loader.exec_module(decoder)
    else:
        from luma import decoders as decoder

    def measure(call):
        start = time.perf_counter()
        value = call()
        return value, time.perf_counter() - start

    for path in args.paths:
        path = path.resolve()
        fixture_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        for sample in range(args.repeat):
            gc.collect()
            profiler = None
            if args.profile:
                import cProfile
                profiler = cProfile.Profile()
                profiler.enable()
            data, decode_s = measure(lambda: decoder.decode_image(path))
            rgba, render_s = measure(lambda: decoder.render_rgba(data))
            if profiler:
                profiler.disable()
                profiler.dump_stats(str(args.profile.with_name(
                    f'{args.profile.stem}-{path.stem}-{sample}.prof')))
            height, width = rgba.shape[:2]

            def wrapped():
                return QImage(rgba.data, width, height, rgba.strides[0],
                              QImage.Format.Format_RGBA8888)

            image, qimage_copy_s = measure(lambda: wrapped().copy())
            pixmap, qpixmap_s = measure(lambda: QPixmap.fromImage(image))
            del image, pixmap
            borrowed, qimage_borrow_s = measure(wrapped)
            pixmap, qpixmap_borrow_s = measure(lambda: QPixmap.fromImage(borrowed))
            del borrowed, pixmap
            prepared, qimage_prepare_s = measure(lambda: wrapped().convertToFormat(
                QImage.Format.Format_ARGB32_Premultiplied))
            pixmap, qpixmap_prepared_s = measure(lambda: QPixmap.fromImage(prepared))
            del prepared, pixmap
            digest = hashlib.sha256(rgba).hexdigest()
            threaded = {}
            for threads in args.threads:
                candidate, elapsed = measure(lambda: parallel_render(decoder, data, threads))
                threaded[str(threads)] = {'render_s': elapsed,
                    'byte_identical': hashlib.sha256(candidate).hexdigest() == digest}
                del candidate
            viewer_render = None
            if args.viewer_render:
                from luma.workers import render_display_rgba
                candidate, elapsed = measure(lambda: render_display_rgba(
                    data, 0, 'RGBA', 'Reinhard', lambda: False))
                viewer_render = {'render_s': elapsed,
                    'byte_identical': hashlib.sha256(candidate).hexdigest() == digest}
                del candidate
            record = {'fixture': str(path), 'fixture_sha256': fixture_sha256, 'sample': sample,
                'size_bytes': path.stat().st_size, 'width': width, 'height': height,
                'python': platform.python_version(), 'numpy': np.__version__,
                'pyside': pyside_version, 'platform': platform.platform(),
                'qt_platform': os.environ['QT_QPA_PLATFORM'],
                'conditions': 'OS cache not evicted; same process; synthetic fixture unless explicitly supplied',
                'decoder': str(args.decoder) if args.decoder else 'working tree',
                'openexr_global_threads': OpenEXR.global_thread_count(),
                'decode_s': decode_s, 'render_s': render_s,
                'qimage_copy_s': qimage_copy_s, 'qpixmap_s': qpixmap_s,
                'qimage_borrow_s': qimage_borrow_s, 'qpixmap_borrow_s': qpixmap_borrow_s,
                'qimage_prepare_s': qimage_prepare_s, 'qpixmap_prepared_s': qpixmap_prepared_s,
                'total_s': decode_s + render_s + qimage_copy_s + qpixmap_s,
                'rgba_sha256': digest, 'parallel_prototype': threaded,
                'viewer_render': viewer_render}
            del data, rgba
            app.processEvents()
            if args.ui:
                from luma.app import ViewerWindow
                window = ViewerWindow()
                window.show()
                app.processEvents()
                start = time.perf_counter()
                try:
                    window.open_path(path)
                    while window.displayed_path != path:
                        app.processEvents()
                        if window.dimension_label.text() == 'Unable to open':
                            raise RuntimeError(window.details.toPlainText())
                        if time.perf_counter() - start > 60:
                            raise TimeoutError('Image was not displayed within 60 seconds')
                        time.sleep(.001)
                    app.processEvents()
                    record['ui_open_s'] = time.perf_counter() - start
                    record['ui_full_resolution'] = (window.canvas.item.pixmap().width() == width
                                                    and window.canvas.item.pixmap().height() == height)
                    record['ui_fit_percent'] = window.canvas.zoom_percent
                    window.canvas.actual_size()
                    record['ui_actual_pixel_scale'] = (window.canvas.transform().m11()
                        * window.canvas.viewport().devicePixelRatioF())
                finally:
                    window.close()
                    app.processEvents()
                    del window
            record['process_peak_rss_mib'] = (resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                / (1024 ** 2 if sys.platform == 'darwin' else 1024)) if resource else None
            print(json.dumps(record), flush=True)


if __name__ == '__main__':
    main()
