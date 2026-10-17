"""Real popup interaction and alpha/geometry contracts shared by viewer/settings."""
from types import SimpleNamespace
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QAccessible, QColor, QCursor, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel

from luma.app import ViewerWindow
from luma.dropdown_widgets import DropdownComboBox, SHADOW_MARGIN, anchored_popup_geometry
from luma.i18n import set_language, tr
from luma.menu_widgets import DropdownMenu
from luma.updater import UpdateDialog
from scripts.render_dropdown_preview import expose_capture_target, wait_for_capture_animation


@pytest.fixture
def combo(qapp):
    widget = DropdownComboBox()
    widget.setAccessibleName('Background')
    for name in ('Graphite', 'Checkerboard', 'Black', 'White'):
        widget.addItem(name, name.lower())
    widget.resize(180, 32)
    widget.move(100, 100)
    widget.show()
    qapp.processEvents()
    yield widget
    widget.hidePopup()
    widget.close()
    qapp.processEvents()


def open_combo(qapp, combo):
    combo.showPopup()
    qapp.processEvents()
    popup = combo.view().window()
    assert popup.isVisible()
    return popup


def assert_alpha_surface(widget, panel):
    image = widget.grab().toImage()
    dpr = image.devicePixelRatio()
    for x, y in ((0, 0), (image.width() - 1, 0), (0, image.height() - 1),
                 (image.width() - 1, image.height() - 1)):
        assert image.pixelColor(x, y).alpha() == 0
    # Rounded panel corners are transparent/soft shadow, not an opaque black
    # rectangle. The top center is opaque; the lower gutter has soft alpha.
    assert image.pixelColor(round(panel.left() * dpr), round(panel.top() * dpr)).alpha() < 80
    assert image.pixelColor(image.width() // 2, round((panel.top() + 1) * dpr)).alpha() == 255
    assert 0 < image.pixelColor(image.width() // 2, image.height() - round(4 * dpr)).alpha() < 100


def test_combo_keeps_native_model_signals_accessibility_and_cancel(qapp, combo):
    changes, activations = [], []
    combo.currentIndexChanged.connect(changes.append)
    combo.activated.connect(activations.append)
    interface = QAccessible.queryAccessibleInterface(combo)
    assert interface.role() == QAccessible.Role.ComboBox
    assert interface.text(QAccessible.Text.Value) == 'Graphite'
    popup = open_combo(qapp, combo)
    assert interface.state().expanded
    QTest.keyClick(combo.view(), Qt.Key.Key_Down)
    assert combo.view().currentIndex().row() == 1
    assert combo.currentIndex() == 0 and not changes
    QTest.keyClick(combo.view(), Qt.Key.Key_Escape)
    assert not popup.isVisible() and not interface.state().expanded
    assert combo.currentIndex() == 0 and not changes and not activations
    open_combo(qapp, combo)
    QTest.keyClick(combo.view(), Qt.Key.Key_End)
    QTest.keyClick(combo.view(), Qt.Key.Key_Return)
    assert combo.currentData() == 'white'
    assert changes == [3] and activations == [3]
    assert interface.text(QAccessible.Text.Value) == 'White'
    # Re-choosing the same value preserves QComboBox's activated-vs-changed
    # distinction; currentIndexChanged must not duplicate a render request.
    open_combo(qapp, combo)
    QTest.keyClick(combo.view(), Qt.Key.Key_Return)
    assert changes == [3] and activations == [3, 3]


def test_combo_mouse_hover_click_disabled_row_and_shadow_dismiss(qapp, combo):
    combo.model().item(1).setEnabled(False)
    popup = open_combo(qapp, combo)
    QTest.keyClick(combo.view(), Qt.Key.Key_Down)
    assert combo.view().currentIndex().row() == 2
    index = combo.model().index(3, 0)
    point = combo.view().visualRect(index).center()
    QTest.mouseMove(combo.view().viewport(), point)
    qapp.processEvents()
    QTest.mouseClick(combo.view().viewport(), Qt.MouseButton.LeftButton, pos=point)
    assert combo.currentIndex() == 3 and not popup.isVisible()
    open_combo(qapp, combo)
    QTest.mouseClick(popup, Qt.MouseButton.LeftButton, pos=QPoint(popup.width() // 2, 3))
    assert not popup.isVisible() and combo.currentIndex() == 3
    # Native popup outside-click dismissal and repeated opening still work.
    open_combo(qapp, combo)
    QTest.mouseClick(popup, Qt.MouseButton.LeftButton, pos=QPoint(-20, -20))
    assert not popup.isVisible()


def test_combo_popup_has_real_alpha_and_exact_panel_alignment(qapp, combo):
    popup = open_combo(qapp, combo)
    assert popup.windowFlags() & Qt.WindowType.FramelessWindowHint
    assert popup.windowFlags() & Qt.WindowType.NoDropShadowWindowHint
    assert popup.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    panel = combo.popup_panel_rect()
    assert panel.width() == combo.width()
    assert popup.x() + panel.left() == combo.mapToGlobal(QPoint()).x()
    assert popup.x() + panel.right() == combo.mapToGlobal(QPoint()).x() + combo.width()
    assert_alpha_surface(popup, panel)


def test_tall_combo_scrolls_last_option_into_view(qapp, combo, monkeypatch):
    for number in range(50):
        combo.addItem(f'Extra {number}', number)
    screen = QApplication.primaryScreen()
    area = QRect(0, 0, screen.availableGeometry().width(), 220)
    monkeypatch.setattr(QApplication, 'screenAt', lambda point: SimpleNamespace(availableGeometry=lambda: area))
    popup = open_combo(qapp, combo)
    assert popup.height() <= area.height()
    QTest.keyClick(combo.view(), Qt.Key.Key_End)
    qapp.processEvents()
    index = combo.view().currentIndex()
    assert index.row() == combo.count() - 1
    assert combo.view().viewport().rect().contains(combo.view().visualRect(index))
    QTest.keyClick(combo.view(), Qt.Key.Key_Return)
    assert combo.currentData() == 49 and not popup.isVisible()


@pytest.mark.parametrize('trigger,area,height', [
    ((20, 20, 82, 28), (0, 0, 800, 600), 237),
    ((600, 560, 82, 28), (0, 0, 800, 600), 237),
    ((-1250, -130, 180, 32), (-1280, -200, 1280, 900), 150),
    ((0, 40, 180, 32), (0, 0, 800, 600), 150),
    ((620, 40, 180, 32), (0, 0, 800, 600), 150),
    ((80, 20, 180, 32), (0, 0, 400, 200), 1000),
])
def test_popup_bounds_preserve_panel_alignment_at_screen_edges(trigger, area, height):
    trigger, area = QRect(*trigger), QRect(*area)
    geometry = anchored_popup_geometry(trigger, height, area)
    panel = geometry.adjusted(SHADOW_MARGIN, SHADOW_MARGIN, -SHADOW_MARGIN, -SHADOW_MARGIN)
    assert panel.width() == trigger.width()
    assert panel.left() == trigger.left() and panel.right() == trigger.right()
    assert area.contains(panel)


@pytest.mark.parametrize('language', ['en', 'zh-CN', 'zh-TW'])
def test_viewer_settings_use_shared_controls_and_zoom_options_keep_semantics(qapp, language):
    set_language(language)
    window = ViewerWindow()
    window.resize(800, 560)
    window.show()
    image = QImage(640, 480, QImage.Format.Format_RGB32)
    image.fill(QColor('#456789'))
    window.canvas.set_image(image)
    window._set_controls(True)
    settings = UpdateDialog(window, '0.1.4')
    settings.show()
    qapp.processEvents()
    try:
        assert isinstance(window.channel, DropdownComboBox)
        assert isinstance(window.tone, DropdownComboBox)
        assert isinstance(settings.display_panel.background, DropdownComboBox)
        assert window.channel.accessibleName() == tr('CHANNEL')
        assert window.tone.accessibleName() == tr('Tone mapping')
        assert [window.channel.itemText(index) for index in range(window.channel.count())] == ['RGBA', 'RGB', 'R', 'G', 'B', 'A']
        assert [window.tone.itemData(index) for index in range(window.tone.count())] == ['Reinhard', 'Clip']
        settings.hide()
        menu = window.zoom_combo.menu()
        assert isinstance(menu, DropdownMenu)
        assert [action.text() for action in menu.actions()[:6]] == ['25%', '50%', '100%', '200%', '400%', '800%']
        for font_size in (12, 20):
            window.zoom_combo.setStyleSheet(window.zoom_combo.styleSheet() + f' QPushButton {{font-size: {font_size}px;}}')
            qapp.processEvents()
            # Exact alignment requires a visible trigger. At 200% the native
            # runner's 512px desktop is narrower than the 800px viewer minimum.
            expose_capture_target(window, window.zoom_combo)
            trigger = QRect(window.zoom_combo.mapToGlobal(QPoint()), window.zoom_combo.size())
            assert window.screen().availableGeometry().contains(trigger)
            menu.popup(window.zoom_combo.mapToGlobal(QPoint()))
            qapp.processEvents()
            QTest.qWait(160)
            wait_for_capture_animation(menu)
            assert menu.panel_rect().width() == window.zoom_combo.width()
            assert window.screen().availableGeometry().contains(menu.panel_rect().translated(menu.pos()).toAlignedRect())
            assert menu.x() + menu.panel_rect().left() == window.zoom_combo.mapToGlobal(QPoint()).x()
            assert all(menu.rect().contains(menu.actionGeometry(action)) for action in menu.actions())
            assert_alpha_surface(menu, menu.panel_rect())
            assert menu.actions()[-1].text() == tr('Actual size')
            assert menu.actions()[-1].toolTip() == tr('Actual pixels · 1')
            action_rect = menu.actionGeometry(menu.actions()[-1]).adjusted(5, 0, -5, 0)
            assert menu.fontMetrics().horizontalAdvance(menu.actions()[-1].text()) <= action_rect.width()
            QTest.keyClick(menu, Qt.Key.Key_Escape)
        menu.actions()[3].trigger()
        assert window.canvas.zoom_percent == pytest.approx(200)
        menu.actions()[2].trigger()
        assert window.canvas.zoom_percent == pytest.approx(100)
        menu.actions()[-1].trigger()
        # Actual size means one image pixel per physical display pixel.
        assert (window.canvas.transform().m11() *
                window.canvas.viewport().devicePixelRatioF()) == pytest.approx(1)
    finally:
        settings.close()
        window.close()


def test_settings_shell_is_owned_frameless_rounded_and_header_moves_only_background(qapp, monkeypatch):
    window = ViewerWindow()
    window.show()
    dialog = UpdateDialog(window, '0.1.4')
    dialog.show()
    qapp.processEvents()
    try:
        assert dialog.parentWidget() is window
        assert dialog.windowFlags() & Qt.WindowType.FramelessWindowHint
        assert dialog.windowType() == Qt.WindowType.Dialog
        assert dialog.frameGeometry() == dialog.geometry()
        assert QAccessible.queryAccessibleInterface(dialog).role() == QAccessible.Role.Dialog
        assert dialog.grab().toImage().pixelColor(0, 0).alpha() == 0
        assert dialog.grab().toImage().pixelColor(dialog.width() // 2, 3).alpha() == 255
        title = dialog.header.findChild(QLabel, 'settings_title')
        assert title.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        calls = []
        monkeypatch.setattr(dialog, 'windowHandle', lambda: SimpleNamespace(startSystemMove=lambda: calls.append('move') or True))
        QTest.mouseClick(dialog.header, Qt.MouseButton.LeftButton, pos=QPoint(100, 15))
        assert calls == ['move']
        # An interactive child gets its own click; it cannot drag the dialog.
        dialog.close_button.blockSignals(True)
        QTest.mouseClick(dialog.close_button, Qt.MouseButton.LeftButton)
        assert calls == ['move']
        dialog.close_button.blockSignals(False)
        monkeypatch.undo()
        QTest.keyClick(dialog, Qt.Key.Key_Escape)
        assert not dialog.isVisible() and dialog._cancel.is_set()
    finally:
        dialog.close()
        window.close()


def test_settings_shell_fits_small_negative_origin_work_area(qapp, monkeypatch):
    window = ViewerWindow()
    dialog = UpdateDialog(window, '0.1.4')
    try:
        area = QRect(-640, -120, 400, 280)
        monkeypatch.setattr(window, 'screen', lambda: SimpleNamespace(availableGeometry=lambda: area))
        dialog.move(500, 500)
        dialog._fit_available_screen()
        assert area.contains(dialog.geometry())
        assert dialog.width() == 384
    finally:
        dialog.close()
        window.close()


@pytest.mark.parametrize('screen_size', [(3200, 2200), (1024, 720)])
@pytest.mark.parametrize('factor,language', [('1', 'en'), ('1.25', 'zh-CN'), ('1.5', 'zh-TW'), ('2', 'zh-CN')])
def test_fresh_high_dpi_real_popup_capture(tmp_path, factor, language, screen_size):
    # Windows already supplies a drive colon. Include one in the POSIX parent
    # directory too, so restoring an absolute QPA config path fails on Linux.
    tmp_path = tmp_path / ('capture files' if sys.platform == 'win32' else 'C: capture files')
    tmp_path.mkdir()
    # Cover both a roomy 3K desktop and the Windows runner's small work area.
    # The latter deliberately clips the owner at 200%; real controls must
    # still be exposed, aligned and captured without changing product sizing.
    config = tmp_path / 'screen.json'
    config.write_text(json.dumps({'screens': [{'name': 'QA', 'x': 0, 'y': 0,
        'width': screen_size[0], 'height': screen_size[1], 'logicalDpi': 96, 'logicalBaseDpi': 96, 'dpr': 1.0}]}))
    env = os.environ.copy()
    # Qt splits platform arguments on every ':', including a Windows drive
    # prefix. Run beside the config and pass only its relative name: the
    # repository and pytest temp directory can even be on different drives.
    env['QT_QPA_PLATFORM'] = f'offscreen:configfile={config.name}'
    root = Path(__file__).resolve().parents[1]
    env['PYTHONPATH'] = os.pathsep.join(filter(None, (str(root), env.get('PYTHONPATH'))))
    env['PYTHONFAULTHANDLER'] = '1'
    env['PYTHONUTF8'] = '1'
    env['QT_SCALE_FACTOR'] = factor
    env['XDG_CACHE_HOME'] = str(tmp_path / 'cache')
    result = subprocess.run([sys.executable, '-m', 'scripts.render_dropdown_preview',
                             '--language', language, '--output', str(tmp_path)],
                            cwd=tmp_path, env=env, capture_output=True,
                            text=True, encoding='utf-8', timeout=25)
    assert result.returncode == 0, (
        f'Capture exited {result.returncode} with {env["QT_QPA_PLATFORM"]!r} '
        f'at scale {factor}; stdout={result.stdout!r}; stderr={result.stderr!r}')
    record = json.loads(result.stdout)
    assert record['device_pixel_ratio'] == float(factor)
    assert record['platform'] == 'offscreen'
    assert record['screen']['name'] == 'QA'
    assert record['screen']['available_geometry'] == [
        0, 0, round(screen_size[0] / float(factor)), round(screen_size[1] / float(factor))]
    assert record['screen']['device_pixel_ratio'] == float(factor)
    if screen_size[0] / float(factor) < 800:
        assert not record['widgets']['footer-zoom']['owner_inside_available_screen']
    for name, widget in record['widgets'].items():
        assert all(dimension > 0 for dimension in widget['pixel_size']), name
        assert widget['corner_alpha'] == [0, 0, 0, 0], name
        if 'panel_width' in widget:
            assert widget['trigger_inside_available_screen'], name
            assert widget['panel_width'] == widget['trigger_width']
            assert widget['panel_left'] == widget['trigger_left']
            assert widget['panel_inside_available_screen']
    assert all(Path(path).is_file() for path in record['screenshots'].values())
