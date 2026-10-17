"""Theme the real QToolTip, preserving its content, timeout and accessibility."""
import ctypes
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QAccessible
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QToolTip

from luma import tooltip_theme
from luma.i18n import set_language
from luma.ui_fonts import ui_font_families


@pytest.fixture(autouse=True)
def stable_native_tooltip_surface(qapp):
    # Inspect the final native surface and requested lifetime independently of
    # the desktop's optional fade-in, which can outlast a 100-ms test tooltip.
    effects = (Qt.UIEffect.UI_AnimateTooltip, Qt.UIEffect.UI_FadeTooltip)
    previous = [(effect, QApplication.isEffectEnabled(effect)) for effect in effects]
    for effect in effects:
        QApplication.setEffectEnabled(effect, False)
    yield
    for effect, enabled in previous:
        QApplication.setEffectEnabled(effect, enabled)


def visible_tip(qapp):
    qapp.processEvents()
    return next(widget for widget in qapp.topLevelWidgets()
                if isinstance(widget, QLabel) and widget.windowType() == Qt.WindowType.ToolTip
                and widget.isVisible())


@pytest.mark.parametrize('language', ['en', 'zh-CN', 'zh-TW'])
def test_native_tooltip_content_accessibility_corners_fonts_and_reuse(qapp, language, monkeypatch):
    monkeypatch.setattr(tooltip_theme, 'high_contrast_enabled', lambda: False)
    set_language(language)
    manager = tooltip_theme.install_tooltip_theme(qapp)
    button = QPushButton('Test')
    button.setToolTip('缩放到窗口 · Fit to window <raw>')
    button.show()
    qapp.processEvents()
    try:
        QToolTip.showText(QPoint(100, 100), button.toolTip(), button)
        tip = visible_tip(qapp)
        assert QToolTip.text() == button.toolTip()
        assert QAccessible.queryAccessibleInterface(tip) is not None
        assert tip.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        assert tip.font().families() == list(ui_font_families(language))
        image = tip.grab().toImage()
        assert image.pixelColor(0, 0).alpha() == 0
        assert image.pixelColor(image.width() - 1, image.height() - 1).alpha() == 0
        assert image.pixelColor(image.width() // 2, 2).alpha() == 255
        assert tip.height() <= tip.fontMetrics().height() + 14
        QToolTip.showText(QPoint(110, 110), 'Different text', button)
        assert QToolTip.text() == 'Different text'
        assert visible_tip(qapp) is tip
        assert qapp.styleSheet().count(tooltip_theme._START) == 1
        assert tooltip_theme.install_tooltip_theme(qapp) is manager
        assert qapp.styleSheet().count(tooltip_theme._START) == 1
    finally:
        QToolTip.hideText()
        QTest.qWait(320)
        button.close()


def test_native_tooltip_bounds_and_requested_duration_are_preserved(qapp, monkeypatch):
    monkeypatch.setattr(tooltip_theme, 'high_contrast_enabled', lambda: False)
    tooltip_theme.install_tooltip_theme(qapp)
    button = QPushButton('Test')
    button.show()
    qapp.processEvents()
    try:
        area = QApplication.primaryScreen().availableGeometry()
        QToolTip.showText(area.bottomRight(), 'Edge tooltip', button, QRect(), 100)
        tip = visible_tip(qapp)
        assert area.contains(tip.geometry())
        QTest.qWait(160)
        assert not QToolTip.isVisible()
    finally:
        QToolTip.hideText()
        button.close()


def test_high_contrast_uses_platform_palette_without_custom_alpha_paint(qapp, monkeypatch):
    monkeypatch.setattr(tooltip_theme, 'high_contrast_enabled', lambda: True)
    manager = tooltip_theme.install_tooltip_theme(qapp)
    assert manager.high_contrast
    assert 'palette(tool-tip-base)' in qapp.styleSheet()
    assert '#eeeeee' not in tooltip_theme.tooltip_stylesheet(True)
    # No replacement event/tooltip timer is installed. Native tooltip drawing
    # is left to the system palette when accessibility contrast is enabled.
    button = QPushButton('Test')
    button.show()
    qapp.processEvents()
    QToolTip.showText(QPoint(40, 40), 'High contrast', button)
    tip = visible_tip(qapp)
    assert tip.grab().toImage().pixelColor(0, 0).alpha() == 255
    QToolTip.hideText()
    QTest.qWait(320)
    button.close()
    monkeypatch.setattr(tooltip_theme, 'high_contrast_enabled', lambda: False)
    manager.refresh()


def test_unbroken_tooltip_stays_on_screen_without_rewriting_content(qapp, monkeypatch):
    monkeypatch.setattr(tooltip_theme, 'high_contrast_enabled', lambda: False)
    tooltip_theme.install_tooltip_theme(qapp)
    button = QPushButton('Test')
    button.show()
    qapp.processEvents()
    text = 'C:/' + 'long-unbroken-filename' * 100 + '.exr'
    try:
        area = QApplication.primaryScreen().availableGeometry()
        QToolTip.showText(area.bottomRight(), text, button)
        tip = visible_tip(qapp)
        assert area.contains(tip.geometry())
        assert QToolTip.text() == text and tip.text() == text
    finally:
        QToolTip.hideText()
        QTest.qWait(320)
        button.close()


@pytest.mark.parametrize('flag,success,expected', [(0, True, False), (1, True, True), (0, False, True)])
def test_windows_high_contrast_query_is_read_only_and_fail_safe(monkeypatch, flag, success, expected):
    class Query:
        def __call__(self, action, size, pointer, update):
            assert action == 0x42 and update == 0
            values = ctypes.cast(pointer, ctypes.POINTER(ctypes.c_uint32))
            assert values[0] == size
            values[1] = flag
            return success
    query = Query()
    monkeypatch.setattr(tooltip_theme.sys, 'platform', 'win32')
    monkeypatch.setattr(tooltip_theme.ctypes, 'WinDLL', lambda *a, **k: SimpleNamespace(SystemParametersInfoW=query), raising=False)
    assert tooltip_theme.high_contrast_enabled() is expected
