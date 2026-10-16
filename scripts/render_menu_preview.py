#!/usr/bin/env python3
"""Capture actual rounded-menu widgets and their live Qt fade using a fixture.

Offscreen mode captures QWidget pixels, not a Windows desktop. Viewer+popup
images and GIF frames composite the captured transparent popup at its actual
mapped position over the captured viewer. No replacement UI or interpolated
animation frames are drawn. The JSON labels this distinction explicitly.

QT_QPA_PLATFORM=offscreen python -m scripts.render_menu_preview --output /tmp/luma-menu
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time

import numpy as np
from PIL import Image
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QFontInfo, QImage, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from luma.app import STYLE, ViewerWindow
from luma.i18n import set_language


def _composite(window, menu, backdrop):
    result = backdrop.copy()
    painter = QPainter(result)
    painter.drawPixmap(window.mapFromGlobal(menu.pos()), menu.grab())
    painter.end()
    return result


def _pil(image):
    image = image.convertToFormat(QImage.Format.Format_RGBA8888)
    return Image.frombytes('RGBA', (image.width(), image.height()), bytes(image.constBits()), 'raw', 'RGBA', image.bytesPerLine())


def capture(language, output, width=1280, animation=True):
    output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    app.setStyle('Fusion')
    app.setStyleSheet(STYLE)
    set_language(language)
    y, x = np.mgrid[:600, :1000]
    pixels = np.empty((600, 1000, 3), dtype=np.uint8)
    pixels[..., 0] = 27 + x * 65 // 1000
    pixels[..., 1] = 37 + y * 72 // 600
    pixels[..., 2] = 46 + (x + y) * 69 // 1600
    pixels[135:465, 160:360] = (172, 156, 129)
    pixels[135:465, 400:600] = (117, 148, 155)
    pixels[135:465, 640:840] = (157, 122, 117)
    fixture = output / 'synthetic-menu-preview.png'
    Image.fromarray(pixels).save(fixture)
    window = ViewerWindow()
    window.resize(width, 740 if width >= 1000 else 560)
    window.move(0, 0)
    window.show()
    window.open_path(fixture)
    deadline = time.monotonic() + 5
    while window.displayed_path != fixture and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.005)
    if window.displayed_path != fixture:
        window.close()
        raise RuntimeError('Synthetic preview failed to load')
    app.processEvents()
    suffix = f'{language}-{width}-{window.devicePixelRatioF():g}x'
    screenshots = {}
    records = {}
    for name, widget in [('viewer', window), ('footer', window.viewbar)]:
        path = output / f'{name}-{suffix}.png'
        widget.grab().save(str(path))
        screenshots[name] = str(path)
    backdrop = window.grab()
    animation_record = None
    for name, menu in [('ellipsis', window.app_menu), ('context', window.image_context_menu)]:
        if name == 'ellipsis':
            anchor = window.more_button.mapToGlobal(QPoint(0, window.more_button.height() + 4))
            start = time.monotonic()
            menu.popup(anchor)
        else:
            anchor = window.canvas.viewport().mapToGlobal(window.canvas.viewport().rect().center())
            start = time.monotonic()
            window._show_image_context_menu(anchor)
        frames, times, opacity = [], [], []
        while time.monotonic() - start < .38:
            app.processEvents()
            if name == 'ellipsis' and animation:
                frames.append(_pil(_composite(window, menu, backdrop).toImage()).convert('RGB'))
                times.append(round((time.monotonic() - start) * 1000, 2))
                opacity.append(round(menu.fade_effect.opacity(), 5))
            time.sleep(.016)
        for key, pixmap in [(name, menu.grab()), (f'viewer-{name}', _composite(window, menu, backdrop))]:
            path = output / f'{key}-{suffix}.png'
            pixmap.save(str(path))
            screenshots[key] = str(path)
        grabbed = menu.grab().toImage()
        records[name] = {
            'size': [menu.width(), menu.height()],
            'position': [menu.x(), menu.y()],
            'transparent_corners': [grabbed.pixelColor(x, y).alpha() for x, y in
                                    [(0, 0), (grabbed.width()-1, 0), (0, grabbed.height()-1), (grabbed.width()-1, grabbed.height()-1)]],
            'inside_available_screen': menu.screen().availableGeometry().contains(menu.geometry()),
            'font_family': QFontInfo(menu.font()).family(),
        }
        if frames:
            # Require observed rendered transparency before claiming a fade capture.
            varied = any(0 < value < 1 for value in opacity) and opacity[-1] == 1
            if not varied:
                animation_record = {'captured': False, 'reason': 'Animation disabled by system/style, or no intermediate frame was observed'}
            else:
                path = output / f'ellipsis-open-{suffix}.gif'
                durations = [max(10, round(times[i+1] - times[i])) for i in range(len(times)-1)] + [900]
                frames[0].save(path, save_all=True, append_images=frames[1:], duration=durations, loop=0)
                animation_record = {'captured': True, 'path': str(path), 'frame_times_ms': times,
                                    'observed_opacity': opacity, 'interpolated_frames': False,
                                    'capture': 'Live Qt QWidget capture composited over its viewer backdrop'}
        menu.close()
        QTest.qWait(20)
    result = {
        'language': language, 'platform': app.platformName(), 'device_pixel_ratio': window.devicePixelRatioF(),
        'capture': 'Actual Qt widget pixels; viewer+popup views are offscreen composites, not native Windows desktop captures',
        'footer_height': window.viewbar.height(), 'details_icon_size': window.info_button.iconSize().width(),
        'details_hit_target': [window.info_button.width(), window.info_button.height()],
        'info_gap': window.info_button.x() - window.status_label.geometry().right(),
        'screenshots': screenshots, 'menus': records, 'animation': animation_record,
    }
    window.close()
    app.processEvents()
    manifest = output / f'menu-preview-{suffix}.json'
    manifest.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--language', choices=('zh-CN', 'zh-TW', 'en'), default='zh-CN')
    parser.add_argument('--width', type=int, default=1280)
    parser.add_argument('--no-animation', action='store_true')
    args = parser.parse_args()
    print(json.dumps(capture(args.language, args.output.resolve(), args.width, not args.no_animation), ensure_ascii=False))


if __name__ == '__main__':
    main()
