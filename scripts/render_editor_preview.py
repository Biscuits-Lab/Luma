#!/usr/bin/env python3
"""Capture the real four-tab editor using a self-created synthetic landscape.

Example: python -m scripts.render_editor_preview --language zh-CN --output build/editor-preview
Set QT_SCALE_FACTOR to exercise another DPI. On Windows, additional HWND PNGs
capture native windows. Offscreen widget grabs are not native Windows evidence.
No user image, private reference, network access or image-generation model is used.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path
import time

from PIL import Image, ImageDraw
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QFontInfo, QTextLayout
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QAbstractButton, QComboBox, QLabel

from luma.app import STYLE
from luma.editor import ImageEditorDialog, _qimage
from luma.editor_core import Adjustments, Stroke
from luma.i18n import set_language


def synthetic_landscape():
    """Generate portable fixture pixels with Pillow only, never source photos."""
    image = Image.new('RGBA', (1600, 1000))
    draw = ImageDraw.Draw(image)
    sky, horizon = (55, 92, 113), (208, 183, 144)
    for y in range(image.height):
        color = tuple(round(a + (b-a)*y/image.height) for a, b in zip(sky, horizon))
        draw.line((0, y, image.width, y), fill=color+(255,))
    draw.ellipse((1130, 110, 1290, 270), fill=(247, 210, 140, 255))
    shapes = [
        ([(0, 540), (200, 370), (380, 480), (640, 270), (960, 550), (1150, 405),
          (1600, 580), (1600, 1000), (0, 1000)], (63, 87, 97, 255)),
        ([(540, 390), (640, 270), (765, 395), (661, 350), (610, 382)], (207, 213, 203, 255)),
        ([(0, 700), (255, 490), (470, 665), (780, 490), (1040, 730), (1260, 540),
          (1600, 770), (1600, 1000), (0, 1000)], (39, 69, 75, 255)),
        ([(0, 795), (400, 742), (730, 790), (1150, 720), (1600, 805),
          (1600, 1000), (0, 1000)], (88, 125, 128, 255)),
    ]
    for points, color in shapes:
        draw.polygon(points, fill=color)
    for y in range(808, 1000, 17):
        draw.line([(max(0, 900-(y-800)*2), y), (min(1599, 1100+(y-800)), y)],
                  fill=(164, 175, 151, 255), width=3)
    draw.polygon([(0, 880), (240, 800), (530, 910), (720, 1000), (0, 1000)],
                 fill=(30, 55, 57, 255))
    for x, y in [(110, 800), (180, 810), (70, 830), (275, 840), (360, 870)]:
        draw.line((x, y-100, x, y+20), fill=(24, 45, 46, 255), width=7)
        for shift in (0, 25, 50):
            draw.polygon([(x, y-130+shift), (x-28, y-70+shift), (x+28, y-70+shift)],
                         fill=(25, 48, 47, 255))
    return image


def rect_values(rect):
    return [rect.x(), rect.y(), rect.width(), rect.height()]


def glyph_font_runs(owner):
    """Record actual shaped glyph fonts and missing glyphs for visible controls."""
    samples = []
    for widget in owner.findChildren(QLabel) + owner.findChildren(QAbstractButton):
        if widget.isVisible() and widget.text():
            samples.append((widget.text(), widget.font(), widget.objectName()))
    for combo in owner.findChildren(QComboBox):
        if combo.isVisible():
            samples.extend((combo.itemText(i), combo.font(), combo.objectName())
                           for i in range(combo.count()))
    records = []
    for text, font, name in samples:
        layout = QTextLayout(text, font)
        layout.beginLayout()
        line = layout.createLine()
        if line.isValid():
            line.setLineWidth(10000)
        layout.endLayout()
        runs = layout.glyphRuns()
        records.append({
            'text': text, 'object_name': name,
            'families': sorted({run.rawFont().familyName() for run in runs}),
            'missing_glyphs': sum(sum(index == 0 for index in run.glyphIndexes()) for run in runs),
        })
    return records


def wait_for_preview(app, dialog, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        QTest.qWait(10)
        if dialog._ready and not dialog._preview_timer.isActive():
            return
    raise RuntimeError(f'Editor preview did not settle: {dialog.status.text()}')


def capture(language, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    app.setStyle('Fusion')
    app.setStyleSheet(STYLE)
    set_language(language)
    dialog = ImageEditorDialog(_qimage(synthetic_landscape()), output / 'synthetic-alpine-study.png')
    dialog.resize(1280, 860)
    dialog.move(30, 30)
    dialog.show()
    wait_for_preview(app, dialog)
    suffix = f'{language}-{dialog.devicePixelRatioF():g}x'
    screenshots, records = {}, {}

    def save(name, pixmap):
        path = output / f'{name}-{suffix}.png'
        if pixmap.isNull() or not pixmap.save(str(path)):
            raise RuntimeError(f'Could not capture {path}')
        screenshots[name] = str(path)
        return pixmap

    def native_capture(name, widget):
        if app.platformName() != 'windows':
            return None
        # This is a real WId/HWND capture, not an offscreen widget composite.
        handle = int(widget.winId())
        pixmap = widget.screen().grabWindow(handle)
        save(name + '-windows-hwnd', pixmap)
        return {'hwnd': handle, 'pixel_size': [pixmap.width(), pixmap.height()]}

    def snapshot(mode):
        app.processEvents()
        QTest.qWait(80)
        name = 'editor-' + mode.lower()
        pixmap = save(name, dialog.grab())
        tabs = {button.text(): rect_values(button.geometry()) for button in dialog.tab_buttons}
        tab_rects = [button.geometry() for button in dialog.tab_buttons]
        controls_overlap = any(a.intersects(b) for index, a in enumerate(tab_rects)
                               for b in tab_rects[index+1:])
        glyphs = glyph_font_runs(dialog)
        record = {
            'window_geometry': rect_values(dialog.geometry()),
            'available_screen': rect_values(dialog.screen().availableGeometry()),
            'window_inside_available_screen': dialog.screen().availableGeometry().contains(dialog.frameGeometry()),
            'pixel_size': [pixmap.width(), pixmap.height()],
            'device_pixel_ratio': pixmap.devicePixelRatio(),
            'font_family': QFontInfo(dialog.font()).family(),
            'glyph_font_runs': glyphs,
            'tab_geometry': tabs, 'tabs_overlap': controls_overlap,
            'canvas_geometry': rect_values(dialog.canvas.geometry()),
            'image_rect': rect_values(dialog.canvas.image_rect()),
            'preview_pixel_size': [dialog.canvas.image.width(), dialog.canvas.image.height()],
            'source_pixel_size': list(dialog.canvas.source_size),
            'save_button_geometry': rect_values(dialog.save_button.geometry()),
            'crop_tools_visible': dialog.crop_tools.isVisible(),
            'markup_tools_visible': dialog.markup_tools.isVisible(),
            'side_panel_visible': dialog.panels.isVisible(),
            'status': dialog.status.text(),
            'native_capture': native_capture(name, dialog),
        }
        if controls_overlap or any(row['missing_glyphs'] for row in glyphs):
            raise RuntimeError(f'{mode}: overlapping tabs or missing text glyphs')
        records[mode] = record

    def dropdown_snapshot(name, combo):
        combo.showPopup()
        app.processEvents()
        QTest.qWait(100)
        popup = combo.view().window()
        panel = combo.popup_panel_rect()
        pixmap = save(name, popup.grab())
        # Explicitly named composite: its popup pixels come from the real widget.
        composite = dialog.grab()
        from PySide6.QtGui import QPainter
        painter = QPainter(composite)
        painter.drawPixmap(dialog.mapFromGlobal(popup.pos()), pixmap)
        painter.end()
        save(name + '-in-window', composite)
        record = {
            'trigger_class': type(combo).__name__,
            'trigger_geometry': rect_values(combo.geometry()),
            'popup_geometry': rect_values(popup.geometry()),
            'panel_width': panel.width(), 'trigger_width': combo.width(),
            'panel_left': popup.x()+panel.left(),
            'trigger_left': combo.mapToGlobal(QPoint()).x(),
            'trigger_inside_available_screen': combo.screen().availableGeometry().contains(
                combo.rect().translated(combo.mapToGlobal(QPoint()))),
            'device_pixel_ratio': pixmap.devicePixelRatio(),
            'native_capture': native_capture(name, popup),
        }
        if panel.width() != combo.width():
            raise RuntimeError(f'{name}: popup panel width differs from its trigger')
        if record['trigger_inside_available_screen'] and record['panel_left'] != record['trigger_left']:
            raise RuntimeError(f'{name}: visible popup panel is not aligned to its trigger')
        records[name] = record
        combo.hidePopup()
        app.processEvents()

    try:
        for mode in ('Crop', 'Adjust', 'Filters', 'Markup'):
            dialog._tab_changed(mode)
            if mode == 'Crop':
                dialog._commit(replace(dialog.state, crop=(.06, .07, .94, .92)))
            elif mode == 'Adjust':
                dialog._commit(replace(dialog.state, adjustments=Adjustments(
                    brightness=8, contrast=10, highlights=-18, shadows=12, warmth=8)))
            elif mode == 'Filters':
                dialog._set_filter('golden')
            else:
                dialog._commit(replace(dialog.state, strokes=(Stroke(
                    ((.66, .33), (.44, .41)), (255, 214, 64, 255), .004, 'pen', 'arrow'),)))
            wait_for_preview(app, dialog)
            snapshot(mode)
            if mode == 'Crop':
                dropdown_snapshot('editor-aspect-dropdown', dialog.aspect)
            elif mode == 'Markup':
                dropdown_snapshot('editor-shape-dropdown', dialog.shape_combo)
    finally:
        dialog._shutdown()
        dialog.hide()
        dialog._renderer._thread.join(5)
        app.processEvents()
    result = {
        'language': language, 'platform': app.platformName(),
        'device_pixel_ratio': records['Crop']['device_pixel_ratio'],
        'capture': 'Actual Qt editor widgets with synthetic pixels. Only windows-hwnd PNGs are native Windows evidence; in-window dropdowns are explicitly widget composites.',
        'screenshots': screenshots, 'widgets': records,
    }
    report = output / f'editor-preview-{suffix}.json'
    report.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--language', choices=('en', 'zh-CN', 'zh-TW'), default='zh-CN')
    args = parser.parse_args()
    print(json.dumps(capture(args.language, args.output.resolve()), ensure_ascii=False))


if __name__ == '__main__':
    main()
