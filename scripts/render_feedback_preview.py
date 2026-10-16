"""Capture real canvas notifications and safe, version-bound release notes."""
import argparse
import json
from pathlib import Path
import time

from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtGui import QFontInfo, QTextLayout
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from luma.app import STYLE, ViewerWindow
from luma.i18n import set_language
from luma.updater import UpdateDialog


def render(output, language, release_json=None):
    output.mkdir(parents=True, exist_ok=True)
    fixture = output / 'synthetic-feedback.png'
    image = Image.new('RGB', (1200, 800), '#52676a')
    image.paste('#b7a68b', (180, 170, 440, 630))
    image.paste('#809e9f', (470, 170, 730, 630))
    image.paste('#a28480', (760, 170, 1020, 630))
    image.save(fixture)
    app = QApplication.instance() or QApplication([])
    app.setStyle('Fusion')
    app.setStyleSheet(STYLE)
    set_language(language)
    window = ViewerWindow()
    window.resize(1100, 760)
    window.show()
    window.open_path(fixture)
    deadline = time.monotonic() + 8
    while window.displayed_path != fixture and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.005)
    if window.displayed_path != fixture:
        raise RuntimeError('Synthetic feedback fixture failed to load')
    before = window.status_label.toolTip(), window.viewbar.geometry(), window.canvas.geometry()
    window.copy_image_path()
    QTest.qWait(220)
    assert window.toast.isVisible()
    assert before == (window.status_label.toolTip(), window.viewbar.geometry(), window.canvas.geometry())
    suffix = f'{language}-{window.devicePixelRatioF():g}x'
    captures = {}

    def save(name, widget):
        path = output / f'{name}-{suffix}.png'
        if not widget.grab().save(str(path)):
            raise RuntimeError(f'Could not save {path}')
        captures[name] = str(path)
        if app.platformName() == 'windows':
            native = output / f'{name}-windows-desktop-{suffix}.png'
            if not widget.screen().grabWindow(0).save(str(native)):
                raise RuntimeError('Native Windows screenshot failed')
            captures[name + '-windows-desktop'] = str(native)

    save('canvas-toast', window)
    data = (json.loads(release_json.read_text(encoding='utf-8')) if release_json else
            {'tag_name': 'v0.1.4', 'body': 'Synthetic release-note UI fixture\n\n'
             '• Verified update behavior\n• Responsive exposure adjustment\n\n'
             '<img src="https://example.invalid/never-load.png"> remains plain text.'})
    version = data['tag_name'].removeprefix('v')
    settings = UpdateDialog(window, version)
    settings.update_details.set_release(version, data['body'])
    settings.update_details.toggle.setChecked(True)
    settings.show()
    app.processEvents()
    QTest.qWait(80)
    assert settings.windowFlags() & Qt.WindowType.FramelessWindowHint
    assert settings.update_details.version == version
    save('release-notes', settings)
    result = {'language': language, 'platform': app.platformName(),
              'dpi_scale': window.devicePixelRatioF(), 'notes_version': version,
              'notes_source': 'provided release JSON' if release_json else 'synthetic UI fixture',
              'settings_font': QFontInfo(settings.font()).family(),
              'footer_unchanged': True, 'screenshots': captures}
    # QFontInfo only identifies the default face: CJK may silently be shaped
    # by a Japanese fallback. Record the real glyph-run faces for this toast.
    toast_layout = QTextLayout(window.toast.text(), window.toast.font())
    toast_layout.beginLayout()
    toast_layout.createLine()
    toast_layout.endLayout()
    result['toast_text'] = window.toast.text()
    result['toast_glyph_fonts'] = sorted({run.rawFont().familyName()
                                         for run in toast_layout.glyphRuns()})
    notes_fonts = set()
    document = settings.update_details.text.document()
    # QTextBrowser does not retain glyph caches for every document block.
    # Its plain text has one effective font, so shape each paragraph directly.
    for paragraph in settings.update_details.text.toPlainText().splitlines():
        if not paragraph:
            continue
        layout = QTextLayout(paragraph, document.defaultFont())
        layout.beginLayout()
        layout.createLine()
        layout.endLayout()
        notes_fonts.update(run.rawFont().familyName() for run in layout.glyphRuns())
    result['notes_glyph_fonts'] = sorted(notes_fonts)
    settings.close()
    window.close()
    app.processEvents()
    (output / f'feedback-{suffix}.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--language', choices=('en', 'zh-CN', 'zh-TW'), default='zh-CN')
    parser.add_argument('--release-json', type=Path)
    args = parser.parse_args()
    print(json.dumps(render(args.output.resolve(), args.language, args.release_json), ensure_ascii=False))
