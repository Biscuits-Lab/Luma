#!/usr/bin/env python3
"""Capture the real docked Details card with a clearly synthetic image fixture."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time

import numpy as np
from PIL import Image
from PySide6.QtWidgets import QApplication

from luma.app import STYLE, ViewerWindow
from luma.i18n import set_language


def render(output, language='zh-CN', width=1280):
    output.mkdir(parents=True, exist_ok=True)
    y, x = np.mgrid[0:1080, 0:1920]
    pixels = np.empty((1080, 1920, 3), dtype=np.uint8)
    pixels[..., 0] = 15 + (x / 1920 * 155).astype(np.uint8)
    pixels[..., 1] = 25 + (y / 1080 * 135).astype(np.uint8)
    pixels[..., 2] = 45 + ((x + y) / 3000 * 150).astype(np.uint8)
    pixels[180:830, 220:600] = (180, 142, 105)
    pixels[180:830, 650:1030] = (85, 157, 146)
    pixels[180:830, 1080:1460] = (136, 118, 167)
    for position in range(1490, 1850, 24):
        pixels[180:830, position:position + 7] = (220, 190, 145)
    fixture = output / 'synthetic-details-preview.png'
    Image.fromarray(pixels).save(fixture)
    app = QApplication.instance() or QApplication([])
    app.setStyle('Fusion')
    app.setStyleSheet(STYLE)
    set_language(language)
    window = ViewerWindow()
    window.resize(width, 820 if width >= 1000 else 560)
    window.show()
    window.open_path(fixture)
    deadline = time.monotonic() + 5
    while window.displayed_path != fixture and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.005)
    if window.displayed_path != fixture:
        raise RuntimeError('Preview did not load')
    window.prefetch_timer.stop()
    app.processEvents()
    suffix = f'{language}-{width}-{window.devicePixelRatioF():g}x'
    before = window.canvas.viewport().size(), window.canvas.transform(), window.canvas.zoom_percent
    closed = output / f'details-closed-{suffix}.png'
    window.grab().save(str(closed))
    window.info_button.click()
    deadline = time.monotonic() + 2
    while window.histogram.plot.data is None and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.005)
    app.processEvents()
    opened = output / f'details-open-{suffix}.png'
    window.grab().save(str(opened))
    after = window.canvas.viewport().size(), window.canvas.transform(), window.canvas.zoom_percent
    assert after[0].width() == before[0].width() - window.sidebar.width()
    assert before[2] == after[2] == 100, 'Fitted image lost its fit-relative 100% state'
    result = {'language': language, 'width': window.width(),
              'zoom': window.canvas.zoom_percent,
              'canvas_width_closed': before[0].width(),
              'canvas_width_open': after[0].width(),
              'reserved_card_width': window.sidebar.width(),
              'card_insets': window.sidebar.CARD_MARGIN,
              'fit_relative_100_preserved': before[2] == after[2] == 100,
              'histogram_samples': window.histogram.plot.data.sampled_pixels,
              'histogram_visible_pixels': window.histogram.plot.data.visible_pixels,
              'histogram_runs': window.histogram.sample_runs,
              'backdrop_captures': 0,
              'screenshots': {'closed': str(closed), 'open': str(opened)}}
    window.close()
    app.processEvents()
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--language', choices=('en', 'zh-CN', 'zh-TW'), default='zh-CN')
    parser.add_argument('--width', type=int, default=1280)
    args = parser.parse_args()
    print(json.dumps(render(args.output.resolve(), args.language, args.width), ensure_ascii=False))
