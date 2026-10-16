#!/usr/bin/env python3
"""Capture actual open dropdowns, the frameless Settings shell and QToolTips.

On Windows the optional desktop PNGs capture the real native windows. Offscreen
viewer+popup PNGs are explicitly labelled composites of real widget captures;
they do not certify Windows alpha composition or OS window decorations.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from PySide6.QtCore import QPoint, QRectF, Qt
from PySide6.QtGui import QColor, QFontInfo, QImage, QPainter, QTextLayout
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QAbstractItemView, QLabel, QMenu, QToolTip

from luma.app import STYLE, ViewerWindow
from luma.i18n import set_language
from luma.updater import UpdateDialog


def glyph_font_runs(widget):
    """Inspect actual shaped text, not just the resolved Latin default face."""
    samples = []
    if isinstance(widget, QMenu):
        samples.extend((action.text(), widget.font()) for action in widget.actions()
                       if action.text() and not action.isSeparator())
    labels = widget.findChildren(QLabel)
    if isinstance(widget, QLabel):
        labels = [widget] + labels
    samples.extend((label.text(), label.font()) for label in labels if label.text())
    for view in widget.findChildren(QAbstractItemView):
        model = view.model()
        if model is not None:
            samples.extend((str(model.data(model.index(row, 0)) or ''), view.font())
                           for row in range(model.rowCount()))
    records = []
    for text, font in samples:
        layout = QTextLayout(text, font)
        layout.beginLayout()
        line = layout.createLine()
        line.setLineWidth(10000)
        layout.endLayout()
        records.append({'text': text, 'families': sorted({run.rawFont().familyName()
                                                        for run in layout.glyphRuns()})})
    return records


def capture(language, output):
    output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    app.setStyle('Fusion')
    app.setStyleSheet(STYLE)
    set_language(language)
    window = ViewerWindow()
    window.resize(1000, 740)
    window.move(40, 30)
    window.show()
    fixture = QImage(800, 600, QImage.Format.Format_RGB32)
    fixture.fill(QColor('#546b77'))
    painter = QPainter(fixture)
    for x, color in [(120, '#b2a387'), (330, '#7b9a9e'), (540, '#a17f79')]:
        painter.fillRect(x, 140, 160, 320, QColor(color))
    painter.end()
    window.canvas.set_image(fixture)
    window.stack.setCurrentWidget(window.canvas)
    window._set_controls(True)
    window._metadata_status('Synthetic fixture · 800 × 600')
    window.dimension_label.setText('800 × 600')
    window.type_label.setText('Synthetic UI fixture')
    window.info_button.click()
    window.tone.setEnabled(True)  # Display-only fixture, no HDR decoder needed.
    for control in (window.exposure_heading, window.exposure_label, window.exposure,
                    window.tone, window.hdr_note):
        control.show()
    app.processEvents()
    suffix = f'{language}-{window.devicePixelRatioF():g}x'
    screenshots, records = {}, {}

    def save(name, pixmap):
        path = output / f'{name}-{suffix}.png'
        if not pixmap.save(str(path)):
            raise RuntimeError(f'Could not save screenshot: {path}')
        screenshots[name] = str(path)

    def snapshot(name, owner, popup, trigger=None, panel=None):
        app.processEvents()
        QTest.qWait(180)
        save(name, popup.grab())
        composite = owner.grab()
        painter = QPainter(composite)
        painter.drawPixmap(owner.mapFromGlobal(popup.pos()), popup.grab())
        painter.end()
        save(name + '-in-window', composite)
        image = popup.grab().toImage()
        record = {
            'geometry': [popup.x(), popup.y(), popup.width(), popup.height()],
            'device_pixel_ratio': image.devicePixelRatio(),
            'corner_alpha': [image.pixelColor(x, y).alpha() for x, y in
                             [(0, 0), (image.width()-1, 0), (0, image.height()-1), (image.width()-1, image.height()-1)]],
            'font_family': QFontInfo(popup.font()).family(),
            'glyph_font_runs': glyph_font_runs(popup),
        }
        if panel is not None and trigger is not None:
            visible = panel.translated(popup.x(), popup.y())
            record.update({
                'panel_width': panel.width(), 'trigger_width': trigger.width(),
                'panel_left': visible.left(), 'trigger_left': trigger.mapToGlobal(QPoint()).x(),
                'panel_inside_available_screen': QRectF(popup.screen().availableGeometry()).contains(visible),
            })
            if record['panel_width'] != record['trigger_width']:
                raise RuntimeError(f'{name}: panel width differs from trigger')
        if app.platformName() == 'windows':
            desktop = popup.screen().grabWindow(0)
            if desktop.isNull():
                raise RuntimeError('Native Windows desktop screenshot was unavailable')
            save(name + '-windows-desktop', desktop)
        records[name] = record

    for name, combo in [('details-channel', window.channel), ('details-tone', window.tone)]:
        combo.showPopup()
        snapshot(name, window, combo.view().window(), combo, combo.popup_panel_rect())
        combo.hidePopup()
    menu = window.zoom_combo.menu()
    menu.popup(window.zoom_combo.mapToGlobal(QPoint(0, window.zoom_combo.height())))
    snapshot('footer-zoom', window, menu, window.zoom_combo, menu.panel_rect())
    menu.close()
    settings = UpdateDialog(window, '0.1.4')
    settings.show()
    app.processEvents()
    snapshot('settings-frameless', settings, settings)
    background = settings.display_panel.background
    background.showPopup()
    snapshot('settings-background', settings, background.view().window(), background, background.popup_panel_rect())
    background.hidePopup()
    app.processEvents()
    QTest.qWait(30)
    QToolTip.showText(settings.close_button.mapToGlobal(QPoint(8, 24)), settings.close_button.toolTip(), settings.close_button)
    app.processEvents()
    tooltip = next(widget for widget in app.topLevelWidgets()
                   if widget.objectName() == 'qtooltip_label' and widget.isVisible())
    snapshot('settings-tooltip', settings, tooltip)
    QToolTip.hideText()
    QTest.qWait(320)
    settings.close()
    window.close()
    app.processEvents()
    result = {'language': language, 'platform': app.platformName(),
              'device_pixel_ratio': records['footer-zoom']['device_pixel_ratio'],
              'capture': 'Actual Qt widgets; in-window views composite real popup pixels. Only windows-desktop PNGs are native desktop evidence.',
              'screenshots': screenshots, 'widgets': records}
    path = output / f'dropdown-preview-{suffix}.json'
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--language', choices=('en', 'zh-CN', 'zh-TW'), default='zh-CN')
    args = parser.parse_args()
    print(json.dumps(capture(args.language, args.output.resolve()), ensure_ascii=False))


if __name__ == '__main__':
    main()
