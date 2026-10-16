"""Popup behavior is exercised with real Qt events and synthetic image pixels."""
import ctypes
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QPoint, QRect, QSize, Qt, QAbstractAnimation
from PySide6.QtGui import QAccessible, QContextMenuEvent, QCursor, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from luma import menu_widgets
from luma.app import ViewerWindow
from luma.i18n import set_language
from luma.menu_widgets import RoundedMenu, menu_icon, SHADOW_MARGIN


@pytest.fixture
def viewer(qapp, tmp_path):
    window = ViewerWindow()
    window.show()
    image = QImage(64, 48, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.blue)
    path = tmp_path / '合成 synthetic.png'
    image.save(str(path))
    window.path = window.displayed_path = path
    window.canvas.set_image(image)
    window.stack.setCurrentWidget(window.canvas)
    window._set_controls(True)
    window.activateWindow()
    qapp.processEvents()
    yield window
    window.image_context_menu.close()
    window.app_menu.close()
    window.close()
    qapp.processEvents()


def show_context(qapp, window, reason=QContextMenuEvent.Reason.Mouse):
    viewport = window.canvas.viewport()
    point = viewport.rect().center()
    event = QContextMenuEvent(reason, point, viewport.mapToGlobal(point))
    qapp.sendEvent(viewport, event)
    qapp.processEvents()
    assert event.isAccepted()
    assert window.image_context_menu.isVisible()
    return window.image_context_menu


@pytest.mark.parametrize('language', ['en', 'zh-CN', 'zh-TW'])
def test_all_ellipsis_actions_have_icons_and_native_shortcut_column(qapp, language):
    set_language(language)
    window = ViewerWindow()
    try:
        actions = [a for a in window.app_menu.actions() if not a.isSeparator()]
        assert len(actions) == 6
        assert all(not a.icon().isNull() and a.isIconVisibleInMenu() for a in actions)
        assert [a.text().split('\t')[-1] for a in actions if '\t' in a.text()] == [
            'Ctrl+S', 'Ctrl+C', 'Ctrl+O', 'Ctrl+Shift+O', 'Ctrl+Q']
        assert actions[4].text() == {'en': 'Settings', 'zh-CN': '设置', 'zh-TW': '設定'}[language]
        assert all(a.text() not in ('Keyboard shortcuts', '键盘快捷键', '鍵盤快速鍵') for a in actions)
        assert sum(a.isSeparator() for a in window.app_menu.actions()) == 2
        assert '…' not in actions[2].text() and '…' not in actions[3].text()
        assert '…' not in window.context_save_action.text()
        assert '…' in window.save_action.text()  # ellipsis-menu Save As is unchanged
        assert window.context_save_action.text().endswith('\tCtrl+S')
        assert not window.save_action.isEnabled() and not window.copy_action.isEnabled()
        assert window.more_button.accessibleName()
        assert window.more_button.menu() is window.app_menu
        assert window.info_button.iconSize() == QSize(20, 20)
        assert window.info_button.size() == QSize(30, 30)
        assert window.fit_button.iconSize() == QSize(24, 24)
        assert window.viewbar.height() == 36
    finally:
        window.close()


@pytest.mark.parametrize('factor', [1, 1.25, 1.5, 2])
def test_menu_vector_icons_high_dpi_and_disabled(qapp, factor):
    for kind in ('save', 'copy', 'image', 'folder', 'settings', 'keyboard', 'quit', 'rotate', 'delete', 'path'):
        icon = menu_icon(kind)
        pixmap = icon.pixmap(QSize(20, 20), factor)
        assert pixmap.width() >= int(20 * factor)
        assert pixmap.devicePixelRatioF() == pytest.approx(factor)
        image = pixmap.toImage()
        assert any(image.pixelColor(x, y).alpha() for y in range(image.height()) for x in range(image.width()))


def test_popup_alpha_fade_and_fast_reopen(qapp, monkeypatch):
    monkeypatch.setattr(menu_widgets, 'menu_animations_enabled', lambda widget: True)
    menu = RoundedMenu()
    menu.add_icon_action('Copy\tCtrl+C', 'copy', lambda: None)
    menu.popup(QPoint(40, 40))
    qapp.processEvents()
    assert menu.open_animation.state() == QAbstractAnimation.State.Running
    QTest.qWait(45)
    middle = menu.fade_effect.opacity()
    assert 0 < middle < 1
    image = menu.grab().toImage()
    assert image.pixelColor(0, 0).alpha() == 0
    assert 0 < image.pixelColor(image.width() // 2, image.height() - round((SHADOW_MARGIN + 3) * image.devicePixelRatio())).alpha() < 255
    QTest.qWait(160)
    image = menu.grab().toImage()
    for x, y in ((0, 0), (image.width() - 1, 0), (0, image.height() - 1), (image.width() - 1, image.height() - 1)):
        assert image.pixelColor(x, y).alpha() == 0
    assert image.pixelColor(image.width() // 2, image.height() - round((SHADOW_MARGIN + 3) * image.devicePixelRatio())).alpha() == 255
    # The soft shadow is present outside the opaque rounded surface, with room
    # before the actual window edge. It shares the existing opacity effect.
    assert 0 < image.pixelColor(image.width() // 2, image.height() - round(4 * image.devicePixelRatio())).alpha() < 100
    assert image.pixelColor(image.width() // 2, image.height() - 1).alpha() <= 2
    assert menu.graphicsEffect() is menu.fade_effect
    assert menu.windowFlags() & Qt.WindowType.NoDropShadowWindowHint
    assert menu.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    assert menu.windowFlags() & Qt.WindowType.FramelessWindowHint
    menu.close()
    assert menu.fade_effect.opacity() == 1
    assert menu.open_animation.state() == QAbstractAnimation.State.Stopped
    menu.popup(QPoint(40, 40))
    assert menu.fade_effect.opacity() < 1
    menu.close()


def test_reduced_motion_immediate_popup(qapp, monkeypatch):
    monkeypatch.setattr(menu_widgets, 'menu_animations_enabled', lambda widget: False)
    menu = RoundedMenu()
    menu.addAction('Example')
    menu.popup(QPoint(40, 40))
    assert menu.fade_effect.opacity() == 1
    assert menu.open_animation.state() == QAbstractAnimation.State.Stopped
    menu.close()


@pytest.mark.parametrize('disabled_flag', [None, 0x103E, 0x1002, 0x1042, 'error'])
def test_windows_motion_flags_are_read_only_and_fail_safe(monkeypatch, disabled_flag):
    calls = []
    class Query:
        def __call__(self, flag, size, pointer, update):
            calls.append(flag)
            assert size == 0 and update == 0
            ctypes.cast(pointer, ctypes.POINTER(ctypes.c_int32))[0] = int(flag != disabled_flag)
            return disabled_flag != 'error'
    query = Query()
    monkeypatch.setattr(menu_widgets.ctypes, 'WinDLL', lambda *a, **k: SimpleNamespace(SystemParametersInfoW=query), raising=False)
    assert menu_widgets._windows_animations_enabled() is (disabled_flag is None)
    assert calls[0] == 0x103E
    if disabled_flag is None:
        assert calls == [0x103E, 0x1002, 0x1042]
    assert query.restype is ctypes.c_int32


def test_native_menu_navigation_trigger_escape_outside_click_and_bounds(qapp):
    calls = []
    menu = RoundedMenu()
    disabled = menu.add_icon_action('Disabled', 'save', lambda: calls.append('bad'))
    disabled.setEnabled(False)
    first = menu.add_icon_action('Copy', 'copy', lambda: calls.append('copy'))
    menu.addSeparator()
    second = menu.add_icon_action('Open', 'image', lambda: calls.append('open'))
    menu.popup(QApplication.primaryScreen().availableGeometry().bottomRight())
    qapp.processEvents()
    assert QApplication.primaryScreen().availableGeometry().contains(menu.geometry())
    QTest.keyClick(menu, Qt.Key.Key_Down)
    assert menu.activeAction() is first
    QTest.keyClick(menu, Qt.Key.Key_Down)
    assert menu.activeAction() is second
    QTest.keyClick(menu, Qt.Key.Key_Return)
    assert calls == ['open'] and not menu.isVisible()
    menu.popup(QPoint(40, 40))
    QTest.keyClick(menu, Qt.Key.Key_Escape)
    assert not menu.isVisible()
    menu.popup(QPoint(40, 40))
    QTest.mouseClick(menu, Qt.MouseButton.LeftButton, pos=QPoint(-20, -20))
    assert not menu.isVisible()
    menu.popup(QPoint(40, 40))
    QTest.mouseClick(menu, Qt.MouseButton.LeftButton, pos=QPoint(menu.width() // 2, 3))
    assert not menu.isVisible()  # shadow padding is not a clickable menu row


def test_viewport_event_context_actions_and_exact_unicode_path(qapp, viewer, monkeypatch):
    original = viewer.path.read_bytes()
    menu = show_context(qapp, viewer)
    assert len(menu.strip_buttons) == 3
    assert all(action.isEnabled() and not action.icon().isNull() for action in menu.image_actions)
    viewer.context_path_action.trigger()
    assert QApplication.clipboard().text() == str(viewer.path)
    assert not menu.isVisible()
    show_context(qapp, viewer)
    viewer.context_copy_action.trigger()
    assert QApplication.clipboard().image().pixelColor(4, 4).blue() == 255
    show_context(qapp, viewer)
    viewer.context_rotate_action.trigger()
    assert viewer.canvas.item.rotation() == 90
    calls = []
    monkeypatch.setattr(viewer, 'save_as', lambda: calls.append('save'))
    show_context(qapp, viewer)
    viewer.context_save_action.trigger()
    assert calls == ['save']
    assert viewer.path.read_bytes() == original


def test_context_keyboard_strip_rows_escape_and_focus(qapp, viewer):
    menu = show_context(qapp, viewer, QContextMenuEvent.Reason.Keyboard)
    assert menu.strip_buttons[0].hasFocus()
    assert all(button.accessibleName() for button in menu.strip_buttons)
    interface = QAccessible.queryAccessibleInterface(menu)
    assert interface is not None
    QTest.keyClick(menu.strip_buttons[0], Qt.Key.Key_Right)
    assert menu.strip_buttons[1].hasFocus()
    QTest.keyClick(menu.strip_buttons[1], Qt.Key.Key_Down)
    assert menu.activeAction() is viewer.context_save_action
    QTest.keyClick(menu, Qt.Key.Key_Up)
    assert menu.strip_buttons[0].hasFocus()
    QTest.keyClick(menu.strip_buttons[0], Qt.Key.Key_Escape)
    assert not menu.isVisible()
    viewer.canvas.setFocus()
    QTest.keyClick(viewer.canvas, Qt.Key.Key_Menu)
    assert menu.isVisible() and menu.strip_buttons[0].hasFocus()
    QTest.keyClick(menu.strip_buttons[0], Qt.Key.Key_Escape)
    viewer.canvas.setFocus()
    QTest.keyClick(viewer.canvas, Qt.Key.Key_F10, Qt.KeyboardModifier.ShiftModifier)
    assert menu.isVisible()
    QTest.keyClick(menu.strip_buttons[0], Qt.Key.Key_Return)
    assert not menu.isVisible()
    assert QApplication.clipboard().image().pixelColor(4, 4).blue() == 255


@pytest.mark.parametrize('state', ['empty', 'loading', 'error', 'pending'])
def test_context_image_actions_disabled_for_invalid_state(qapp, viewer, state):
    if state == 'empty':
        viewer.path = viewer.displayed_path = None
        viewer.canvas.clear_image()
    elif state in ('loading', 'error'):
        viewer.displayed_path = None
        viewer.canvas.clear_image()
    else:
        viewer._pending_render = True
    menu = show_context(qapp, viewer)
    assert not any(action.isEnabled() for action in menu.image_actions)
    assert viewer._image_context_source is None


def test_stale_context_never_recycles_new_image(qapp, viewer, monkeypatch, tmp_path):
    from luma import actions
    calls = []
    monkeypatch.setattr(actions, 'recycle_source', lambda window, path: calls.append(path) or False)
    first = viewer.path
    show_context(qapp, viewer)
    viewer.path = viewer.displayed_path = tmp_path / 'new.png'
    viewer.context_delete_action.trigger()
    assert not calls
    viewer.path = viewer.displayed_path = first
    show_context(qapp, viewer)
    viewer._load_token += 1  # a new decode of even the same source is stale
    viewer.context_delete_action.trigger()
    assert not calls
    show_context(qapp, viewer)
    viewer.context_delete_action.trigger()
    assert calls == [first]  # reaches the existing safe confirmation/recycle flow
    show_context(qapp, viewer)
    viewer._set_controls(False)
    assert not viewer.image_context_menu.isVisible()
    assert viewer._image_context_source is None


def test_existing_copy_shortcut_still_works(qapp, viewer):
    viewer.canvas.setFocus()
    qapp.processEvents()
    QApplication.clipboard().clear()
    QTest.keyClick(viewer, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    qapp.processEvents()
    assert not QApplication.clipboard().image().isNull()


def test_copy_shortcut_inside_each_popup_and_focus_return(qapp, viewer):
    viewer.canvas.setFocus()
    viewer.app_menu.popup(QPoint(40, 40))
    qapp.processEvents()
    QApplication.clipboard().clear()
    QTest.keyClick(viewer.app_menu, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    assert not QApplication.clipboard().image().isNull()
    assert not viewer.app_menu.isVisible()
    menu = show_context(qapp, viewer, QContextMenuEvent.Reason.Keyboard)
    QApplication.clipboard().clear()
    QTest.keyClick(menu.strip_buttons[0], Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    assert not QApplication.clipboard().image().isNull()
    assert not menu.isVisible()
    menu = show_context(qapp, viewer, QContextMenuEvent.Reason.Keyboard)
    QTest.keyClick(menu.strip_buttons[0], Qt.Key.Key_Escape)
    qapp.processEvents()
    assert viewer.canvas.hasFocus()


@pytest.mark.parametrize('position,size,area,expected', [
    ((758, 607), (266, 161), (0, 0, 1024, 720), (758, 559)),  # actual Windows failure
    ((-10, -20), (266, 161), (48, 32, 976, 688), (48, 32)),  # left/top taskbars
    ((900, 600), (266, 161), (0, 0, 976, 720), (710, 559)),  # right taskbar
    ((-100, 680), (266, 161), (-1280, -200, 1280, 900), (-266, 539)),
    ((900, -80), (266, 161), (800, -200, 1280, 900), (900, -80)),  # already valid
])
def test_work_area_clamp_uses_logical_full_popup_bounds(position, size, area, expected):
    from luma.menu_widgets import clamp_popup_position
    result = clamp_popup_position(QPoint(*position), QSize(*size), QRect(*area))
    assert result == QPoint(*expected)
    assert QRect(*area).contains(QRect(result, QSize(*size)))


@pytest.mark.parametrize('edge', ['bottom', 'left', 'top', 'right'])
def test_native_popup_reclamps_reserved_work_area_without_resizing(qapp, monkeypatch, edge):
    menu = RoundedMenu()
    menu.add_icon_action('Copy', 'copy', lambda: None)
    menu.add_icon_action('Open', 'image', lambda: None)
    real = QApplication.primaryScreen().availableGeometry()
    insets = {'bottom': (0, 0, 0, -48), 'left': (48, 0, 0, 0),
              'top': (0, 48, 0, 0), 'right': (0, 0, -48, 0)}
    area = real.adjusted(*insets[edge])
    monkeypatch.setattr(menu, 'screen', lambda: SimpleNamespace(availableGeometry=lambda: area))
    anchor = real.topLeft() if edge in ('left', 'top') else real.bottomRight()
    menu.popup(anchor)
    qapp.processEvents()
    assert area.contains(menu.geometry())
    size = menu.size()
    # A late native placement/move must be corrected too, without changing
    # keyboard/action geometry or recursively scheduling itself indefinitely.
    menu.move(anchor)
    qapp.processEvents()
    assert area.contains(menu.geometry())
    assert menu.size() == size
    assert not menu._bounds_timer.isActive()
    first = menu.actions()[0]
    QTest.keyClick(menu, Qt.Key.Key_Down)
    assert menu.activeAction() is first
    QTest.keyClick(menu, Qt.Key.Key_Escape)
    assert not menu.isVisible() and not menu._bounds_timer.isActive()


@pytest.mark.parametrize('rows', [10, 40])
def test_over_tall_popup_uses_native_scroll_within_reserved_work_area(qapp, monkeypatch, request, rows):
    # Cover both the subtle case that fits the full monitor but not the work
    # area, and content taller than the full monitor. Height-only clipping
    # would keep geometry in bounds while making the final rows inaccessible.
    screen = QApplication.primaryScreen()
    full = screen.geometry()
    area = QRect(full.left(), full.top(), full.width(), min(220, full.height() - 40))
    # This is a keyboard-only test. Native popup exposure/placement can queue
    # mouse enter/leave events at the cursor left by an earlier mouse test;
    # QMenu correctly clears activeAction on Leave. Park below the simulated
    # work area before opening, then wait for exposure before sending keys.
    old_cursor = QCursor.pos()
    QCursor.setPos(full.bottomLeft())
    qapp.processEvents()
    menu = RoundedMenu()
    def cleanup():
        menu.close()
        QCursor.setPos(old_cursor)
        qapp.processEvents()
    # Clean up even if a geometry/keyboard assertion fails, so a failed popup
    # cannot grab input for the remaining native UI checks.
    request.addfinalizer(cleanup)
    monkeypatch.setattr(menu, 'screen', lambda: SimpleNamespace(
        availableGeometry=lambda: area, geometry=lambda: full))
    calls = []
    actions = [menu.add_icon_action(f'Item {index}', 'copy',
               lambda checked=False, index=index: calls.append(index)) for index in range(rows)]
    menu.popup(area.bottomRight())
    assert QTest.qWaitForWindowExposed(menu)
    qapp.processEvents()
    assert area.contains(menu.geometry())
    assert not menu.geometry().contains(QCursor.pos())
    assert menu.columnCount() == 1
    assert menu.actionGeometry(actions[-1]).bottom() >= menu.height()
    QTest.keyClick(menu, Qt.Key.Key_End)
    assert menu.activeAction() is actions[-1], 'End did not select the final row'
    qapp.processEvents()
    assert menu.activeAction() is actions[-1], (
        f'Selection changed after native events: visible={menu.isVisible()}, '
        f'cursor={QCursor.pos()}, geometry={menu.geometry()}')
    assert area.contains(menu.geometry())
    assert menu.rect().contains(menu.actionGeometry(actions[-1]))
    QTest.keyClick(menu, Qt.Key.Key_Home)
    qapp.processEvents()
    assert menu.activeAction() is actions[0]
    assert menu.rect().contains(menu.actionGeometry(actions[0]))
    # Native arrow navigation must bring every row fully into view, rather
    # than just moving an invisible active action beyond a clipped widget.
    for action in actions[1:]:
        QTest.keyClick(menu, Qt.Key.Key_Down)
        qapp.processEvents()
        assert menu.activeAction() is action
        assert menu.rect().contains(menu.actionGeometry(action))
        assert area.contains(menu.geometry())
    QTest.keyClick(menu, Qt.Key.Key_Return)
    assert calls == [rows - 1] and not menu.isVisible()
    # Reopening resets native scroll position and the work-area constraints.
    menu.popup(area.bottomRight())
    assert QTest.qWaitForWindowExposed(menu)
    qapp.processEvents()
    QTest.keyClick(menu, Qt.Key.Key_Home)
    assert menu.rect().contains(menu.actionGeometry(actions[0]))
    QTest.keyClick(menu, Qt.Key.Key_Escape)
    assert not menu.isVisible()
