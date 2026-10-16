#!/usr/bin/env python3
"""Render reproducible Luma UI evidence using generated pixels, never user photos.

Example: QT_QPA_PLATFORM=offscreen QT_SCALE_FACTOR=1.5 python -m
scripts.render_ui_preview --language zh-CN --output /tmp/luma-ui
"""
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
from luma.updater import UpdateDialog


def render(language: str, output: Path, width: int = 1280) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    # A simple neutral gradient and colored panels make clear this is a fixture.
    y, x = np.mgrid[0:768, 0:1024]
    pixels = np.empty((768, 1024, 3), dtype=np.uint8)
    pixels[..., 0] = 30 + x * 100 // 1024
    pixels[..., 1] = 40 + y * 100 // 768
    pixels[..., 2] = 60 + (x + y) * 80 // (1024 + 768)
    pixels[180:588, 170:400] = (168, 154, 127)
    pixels[180:588, 430:660] = (121, 154, 161)
    pixels[180:588, 690:854] = (159, 123, 117)
    fixture = output / 'synthetic-ui-preview.png'
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
        raise RuntimeError('Synthetic preview failed to load')
    app.processEvents()
    suffix = f'{language}-{width}-{window.devicePixelRatioF():g}x'
    paths = {}
    for name, widget in [('viewer', window), ('header', window.titlebar), ('footer', window.viewbar)]:
        path = output / f'{name}-{suffix}.png'
        if not widget.grab().save(str(path)):
            raise RuntimeError(f'Could not save {path}')
        paths[name] = str(path)
    settings = UpdateDialog(window, '0.1.3')
    settings.show()
    app.processEvents()
    path = output / f'settings-{suffix}.png'
    settings.grab().save(str(path))
    paths['settings'] = str(path)
    result = {'language': language, 'device_pixel_ratio': window.devicePixelRatioF(),
              'width': window.width(), 'header_height': window.titlebar.height(),
              'footer_height': window.viewbar.height(), 'zoom_text': window.zoom_combo.text(),
              'metadata': window.status_label.toolTip(),
              'info_gap': window.info_button.x() - window.status_label.geometry().right(),
              'screenshots': paths}
    settings.close()
    window.close()
    app.processEvents()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--language', choices=('zh-CN', 'zh-TW', 'en'), default='zh-CN')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--width', type=int, default=1280)
    args = parser.parse_args()
    print(json.dumps(render(args.language, args.output.resolve(), args.width), ensure_ascii=False))


if __name__ == '__main__':
    main()
