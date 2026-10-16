"""Settings UI contracts: real bindings, offline opening, errors and keyboard."""
from __future__ import annotations

import ast
from math import ceil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QAccessible, QFont, QFontDatabase, QFontInfo, QIcon, QPainter, QPixmap, QTextLayout
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QCheckBox, QLabel, QPushButton, QStyle, QStyleOptionButton

from luma import shell_settings, updater, settings_widgets
from luma.app import ViewerWindow
from luma.i18n import set_language, tr
from luma.i18n_dialogs import TRANSLATIONS


@pytest.fixture(autouse=True)
def offline_release_notes(monkeypatch):
    from luma import release_notes
    monkeypatch.setattr(release_notes, 'fetch_notes', lambda *args, **kwargs: '')


def open_settings(qapp, monkeypatch, state='unavailable', detail=''):
    monkeypatch.setattr(shell_settings.shell, 'status', lambda: shell_settings.shell.Status(state, detail=detail))
    window = ViewerWindow()
    window.show()
    dialog = updater.open_update_dialog(window, '0.1.3')
    qapp.processEvents()
    return window, dialog


def close(qapp, window, dialog):
    dialog.close()
    window.close()
    qapp.processEvents()


@pytest.mark.parametrize('language', ('en', 'zh-CN', 'zh-TW'))
def test_settings_open_preserves_viewer_and_has_no_side_effects(qapp, monkeypatch, language):
    set_language(language)
    monkeypatch.setattr(shell_settings.shell, 'status', lambda: shell_settings.shell.Status('installed'))
    install = Mock()
    check = Mock()
    monkeypatch.setattr(shell_settings.shell, 'install', install)
    monkeypatch.setattr(updater, 'check_for_update', check)
    window = ViewerWindow()
    window.show()
    window.background.setCurrentIndex(2)
    window.strip_button.click()
    window.info_button.click()
    thumbs = Mock()
    render = Mock()
    window._build_filmstrip = thumbs
    window._schedule_render = render
    dialog = updater.open_update_dialog(window, '0.1.3')
    try:
        qapp.processEvents()
        display = dialog.display_panel
        assert display.background.currentData() == window.canvas.background == 'Black'
        assert window.strip_button.isChecked() and window.filmstrip.isVisible()
        assert window.info_button.isChecked() and window.sidebar.isVisible()
        assert dialog.findChildren(QCheckBox) == []
        assert not hasattr(display, 'strip_switch')
        assert not hasattr(display, 'info_switch')
        assert not hasattr(display, 'scope_badge')
        check.assert_not_called()
        install.assert_not_called()
        thumbs.assert_not_called()
        render.assert_not_called()
    finally:
        close(qapp, window, dialog)


def test_background_binds_both_ways_and_viewer_visibility_controls_still_work(qapp, monkeypatch):
    window, dialog = open_settings(qapp, monkeypatch)
    try:
        display = dialog.display_panel
        calls = []
        window.background.currentIndexChanged.connect(calls.append)
        display.background.setCurrentIndex(3)
        assert window.canvas.background == 'White'
        assert calls == [3]
        window.background.setCurrentIndex(1)
        assert display.background.currentData() == 'Checkerboard'
        assert calls == [3, 1]
        build = Mock(wraps=window._build_filmstrip)
        window._build_filmstrip = build
        for button, panel in ((window.strip_button, window.filmstrip),
                              (window.info_button, window.sidebar)):
            button.click()
            assert button.isChecked() and panel.isVisible()
            button.click()
            assert not button.isChecked() and not panel.isVisible()
            button.setFocus()
            QTest.keyClick(button, Qt.Key.Key_Space)
            assert button.isChecked() and panel.isVisible()
        assert build.call_count == 2
    finally:
        close(qapp, window, dialog)


def test_disclosures_and_background_are_accessible_keyboard_controls(qapp, monkeypatch):
    window, dialog = open_settings(qapp, monkeypatch)
    try:
        background = dialog.display_panel.background
        interface = QAccessible.queryAccessibleInterface(background)
        assert interface.role() == QAccessible.Role.ComboBox
        assert background.accessibleName() == tr('Canvas background')
        # Qt's native combo adapter reports the selected item as Name/Value;
        # the explicit field name is exposed through its Label relation.
        assert interface.text(QAccessible.Text.Value) == tr('Graphite')
        assert any(relation & QAccessible.RelationFlag.Label
                   and label.text(QAccessible.Text.Name) == tr('Canvas background')
                   for label, relation in interface.relations())
        background.setFocus()
        QTest.keyClick(background, Qt.Key.Key_Down)
        assert background.currentIndex() == 1
        assert window.canvas.background == 'Checkerboard'
        details = dialog.update_details
        assert details.content.isHidden()
        details.toggle.setFocus()
        QTest.keyClick(details.toggle, Qt.Key.Key_Space)
        assert details.content.isVisible()
        QTest.keyClick(details.toggle, Qt.Key.Key_Space)
        assert details.content.isHidden()
        assert all(not button.autoDefault() for button in dialog.findChildren(QPushButton))
    finally:
        close(qapp, window, dialog)


@pytest.mark.parametrize('state, detail, expanded', (
    ('unavailable', 'Explorer integration is available only on Windows.', False),
    ('installed', '', False),
    ('installed', 'Windows reported a conflicting .hdr provider.', True),
    ('blocked', '<provider> C:/原始/{file}.exr', True),
    ('recovery_required', '', True),
))
def test_integration_status_keeps_actionable_diagnostics_visible(qapp, monkeypatch, state, detail, expanded):
    monkeypatch.setattr(shell_settings.shell, 'status', lambda: shell_settings.shell.Status(state, detail=detail))
    panel = shell_settings.ExplorerIntegrationPanel()
    panel.show()
    try:
        assert panel.details.toggle.isChecked() == expanded
        assert detail in panel.status_label.text()
        assert panel.status_label.textFormat() == Qt.TextFormat.PlainText
        assert panel.status_badge.toolTip() == panel.status_label.text()
        if state == 'installed':
            assert panel.status_badge.property('state') == ('attention' if expanded else 'ready')
    finally:
        panel.close()


@pytest.mark.parametrize('conflict', (False, True))
def test_production_association_details_fold_only_healthy_status(qapp, monkeypatch, conflict):
    shell = shell_settings.shell
    registry = SimpleNamespace(effective_provider_for=lambda extension:
                               '{foreign-provider}' if conflict and extension == '.hdr'
                               else shell.PROVIDERS[extension[1:]])
    monkeypatch.setattr(shell, '_association_string', lambda *args: None)
    detail = '\n'.join(shell._thumbnail_association_detail(registry, extension)
                       for extension in ('.hdr', '.exr'))
    monkeypatch.setattr(shell, 'status', lambda: shell.Status('installed', detail=detail))
    panel = shell_settings.ExplorerIntegrationPanel()
    panel.show()
    try:
        assert panel.details.toggle.isChecked() == conflict
        assert panel.status_badge.text() == tr('Check associations' if conflict else 'Registered')
        assert panel.status_badge.property('state') == ('attention' if conflict else 'ready')
        assert panel.status_label.text().endswith(detail)
    finally:
        panel.close()


@pytest.mark.parametrize('language', ('en', 'zh-CN', 'zh-TW'))
@pytest.mark.parametrize('width,height', ((480, 240), (620, 310)))
def test_settings_cards_wrap_and_scroll_without_horizontal_clipping(qapp, monkeypatch, language, width, height):
    set_language(language)
    window, dialog = open_settings(qapp, monkeypatch)
    try:
        dialog.resize(width, height)
        qapp.processEvents()
        qapp.processEvents()
        assert dialog.scroll_area.horizontalScrollBar().maximum() == 0
        for label in dialog.findChildren(QLabel):
            if label.wordWrap() and label.isVisible():
                assert label.height() >= label.heightForWidth(label.width()), label.text()
        assert dialog.close_button.geometry().bottom() < dialog.height()
        if height == 310:
            assert dialog.scroll_area.verticalScrollBar().maximum() == 0
        else:
            assert dialog.scroll_area.verticalScrollBar().maximum() > 0
    finally:
        close(qapp, window, dialog)



@pytest.mark.parametrize('language', ('en', 'zh-CN', 'zh-TW'))
@pytest.mark.parametrize('width', (480, 620))
def test_settings_header_reflows_for_large_native_font_metrics(qapp, monkeypatch, language, width):
    # Windows' offscreen plugin can report much wider glyph metrics than the
    # Linux fallback. Stress the measured layout independently of the platform
    # and DPI backend, without substituting or downloading system fonts.
    import re
    set_language(language)
    large_style = re.sub(r'font-size: (\d+)px',
                         lambda match: f'font-size: {2 * int(match[1])}px',
                         settings_widgets.SETTINGS_STYLE)
    large_style += '\nQDialog#luma_settings QWidget { font-size: 24px; }\n'
    monkeypatch.setattr(settings_widgets, 'SETTINGS_STYLE', large_style)
    window, dialog = open_settings(qapp, monkeypatch)
    try:
        dialog.resize(width, 310)
        for _ in range(3):
            qapp.processEvents()
        assert dialog.scroll_area.horizontalScrollBar().maximum() == 0
        assert dialog.scroll_area.widget().width() <= dialog.scroll_area.viewport().width()
        heading = dialog.update_card.findChild(QLabel, 'settings_heading')
        heading_rect = QRect(heading.mapTo(dialog.update_card, QPoint()), heading.size())
        actions = dialog.check_button.parentWidget()
        actions_rect = QRect(actions.mapTo(dialog.update_card, QPoint()), actions.size())
        assert not heading_rect.intersects(actions_rect)
        assert dialog.update_card.rect().contains(heading_rect)
        assert dialog.update_card.rect().contains(actions_rect)
        assert dialog.check_button.width() >= dialog.check_button.minimumSizeHint().width()
        header = dialog.update_card.body.itemAt(0).layout()
        if header.geometry().width() < header.sizeHint().width():
            assert actions_rect.top() > heading_rect.bottom()
        dialog.update_details.setExpanded(True)
        raw = 'C:/原始/raw {version} <b>diagnostic</b>.exr\n' * 30
        dialog._failed(dialog._serial, raw)
        for _ in range(3):
            qapp.processEvents()
        assert dialog.status_label.text() == raw
        assert dialog.scroll_area.horizontalScrollBar().maximum() == 0
        assert dialog.scroll_area.verticalScrollBar().maximum() > 0
        for label in dialog.findChildren(QLabel):
            if label.wordWrap() and label.isVisible():
                assert label.height() >= label.heightForWidth(label.width()), label.text()
        # Exercise the real non-window layout directly: native Windows caps
        # top-level resize requests to the actual desktop, which can be only
        # 512 logical pixels wide on a 200% CI runner. An overscreen dialog is
        # not a valid way to force the wide branch of this responsive layout.
        actual_geometry = QRect(header.geometry())
        try:
            for header_width, inline in ((header.sizeHint().width(), True),
                                         (header.minimumSize().width(), False),
                                         (header.sizeHint().width(), True)):
                header.setGeometry(QRect(actual_geometry.x(), actual_geometry.y(),
                                         header_width, header.heightForWidth(header_width)))
                heading.parentWidget().layout().activate()
                actions.layout().activate()
                heading_rect = QRect(heading.mapTo(dialog.update_card, QPoint()), heading.size())
                actions_rect = QRect(actions.mapTo(dialog.update_card, QPoint()), actions.size())
                assert not heading_rect.intersects(actions_rect)
                assert dialog.check_button.width() >= dialog.check_button.minimumSizeHint().width()
                if inline:
                    heading_center = heading.mapTo(dialog.update_card, heading.rect().center()).y()
                    button_center = dialog.check_button.mapTo(dialog.update_card, dialog.check_button.rect().center()).y()
                    assert abs(heading_center - button_center) <= 1
                else:
                    assert actions_rect.top() > heading_rect.bottom()
        finally:
            header.setGeometry(actual_geometry)
        for _ in range(3):
            qapp.processEvents()
        assert dialog.scroll_area.horizontalScrollBar().maximum() == 0
        assert dialog.status_label.height() >= dialog.status_label.heightForWidth(dialog.status_label.width())
    finally:
        close(qapp, window, dialog)


def test_available_update_keeps_safety_explanation_visible(qapp, monkeypatch):
    window, dialog = open_settings(qapp, monkeypatch)
    monkeypatch.setattr(updater, 'sys', SimpleNamespace(platform='win32'))
    from luma.update_installation import InstallationContext
    monkeypatch.setattr(updater, 'detect_installation', lambda: InstallationContext('managed', Path('/test/Luma.exe'), Path('/test'), '0.1.3'))
    release = updater.Release(updater.Version.parse('0.1.4'), 'v0.1.4', updater.RELEASES_URL + '/tag/v0.1.4',
                              updater.ReleaseAsset('Luma-0.1.4-windows-x64.zip', 'unused', 2048),
                              updater.ReleaseAsset('SHA256SUMS', 'unused', 128),
                              updater.ReleaseAsset('Luma-0.1.4-windows-x64-setup.exe', 'unused', 2048))
    try:
        dialog._completed(dialog._serial, release)
        qapp.processEvents()
        assert dialog.status_label.isVisible()
        for phrase in ('SHA-256', 'this installation and its shortcuts', 'Previous payloads', 'confirms startup'):
            assert phrase in dialog.status_label.text()
        assert dialog.download_button.isVisible()
        assert dialog.update_details.content.isHidden()
    finally:
        close(qapp, window, dialog)


def test_reopen_disconnects_old_controls_and_preserves_state(qapp, monkeypatch):
    window, dialog = open_settings(qapp, monkeypatch)
    window.info_button.click()
    dialog.display_panel.background.setCurrentIndex(2)
    dialog.close()
    qapp.processEvents()
    reopened = updater.open_update_dialog(window, '0.1.3')
    try:
        assert reopened is not dialog
        assert window.info_button.isChecked() and window.sidebar.isVisible()
        assert reopened.display_panel.background.currentData() == 'Black'
        window.info_button.click()
        assert not window.sidebar.isVisible()
        reopened.display_panel.background.setCurrentIndex(3)
        assert window.canvas.background == 'White'
        assert not window.info_button.isChecked()
    finally:
        close(qapp, window, reopened)


def test_new_settings_text_has_complete_localization():
    tree = ast.parse(Path(settings_widgets.__file__).read_text())
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == 'tr' and isinstance(node.args[0], ast.Constant)):
            for language in ('zh-CN', 'zh-TW'):
                assert node.args[0].value in TRANSLATIONS[language]


@pytest.mark.parametrize('available, expected', (
    ((1600, 1000), (620, 310)),
    ((1280, 690), (620, 310)),  # 1080p around 150%, excluding the taskbar
    ((960, 500), (620, 310)),   # 1080p around 200%, excluding the taskbar
    ((540, 650), (508, 310)),
    ((960, 300), (620, 252)),
))
def test_initial_settings_size_fits_usable_screen_with_native_frame_allowance(qapp, monkeypatch, available, expected):
    monkeypatch.setattr(shell_settings.shell, 'status', lambda: shell_settings.shell.Status('unavailable'))
    window = ViewerWindow()
    window.show()
    screen = SimpleNamespace(availableGeometry=lambda: QRect(0, 0, *available))
    monkeypatch.setattr(window, 'screen', lambda: screen)
    dialog = updater.UpdateDialog(window, '0.1.3')
    try:
        # Check the sizing policy before the native window manager is allowed
        # to constrain this simulated screen to the runner's actual desktop.
        assert (dialog.width(), dialog.height()) == expected
        dialog.show()
        qapp.processEvents()
        qapp.processEvents()
        assert dialog.width() <= expected[0]
        assert dialog.height() <= expected[1]
        assert dialog.width() >= 480
        assert dialog.scroll_area.widget().width() <= dialog.scroll_area.viewport().width()
        assert dialog.width() + 32 <= available[0]
        assert dialog.height() + 48 <= available[1]
        assert dialog.scroll_area.horizontalScrollBar().maximum() == 0
        assert dialog.close_button.isVisible()
        assert dialog.close_button.geometry().bottom() < dialog.height()
        if available[1] <= 300:
            assert dialog.scroll_area.verticalScrollBar().maximum() > 0
    finally:
        close(qapp, window, dialog)


@pytest.mark.parametrize('language, first_family, regional_family', (
    ('en', 'Segoe UI', 'Inter'),
    ('zh-CN', 'Microsoft YaHei', 'Noto Sans CJK SC'),
    ('zh-TW', 'Microsoft JhengHei', 'Noto Sans CJK TC'),
))
def test_settings_font_follows_shared_display_language_policy(language, first_family, regional_family):
    families = settings_widgets.settings_font_families(language)
    assert families[0] == first_family
    assert regional_family in families
    stylesheet = settings_widgets.settings_stylesheet(language)
    assert 'QDialog#luma_settings, QDialog#luma_settings QWidget' in stylesheet
    assert stylesheet.index(repr(first_family)) < stylesheet.index(repr(regional_family))


@pytest.mark.parametrize('language, title, headings', (
    ('en', 'Settings', ('Appearance', 'Version & updates')),
    ('zh-CN', '设置', ('外观', '版本与更新')),
    ('zh-TW', '設定', ('外觀', '版本與更新')),
))
def test_settings_actual_glyph_faces_are_region_correct_and_do_not_change_language_or_viewer(qapp, monkeypatch, language, title, headings):
    set_language(language)
    installed = {family.casefold() for family in QFontDatabase.families()}
    candidates = settings_widgets.settings_font_families(language)
    expected = next((family for family in candidates[:4] if family.casefold() in installed), None)
    if language == 'en':
        expected = QFontInfo(qapp.font()).family()
    if expected is None:
        pytest.skip('No YaHei or Noto CJK family installed for deterministic glyph inspection')
    window, dialog = open_settings(qapp, monkeypatch)
    try:
        assert dialog.findChild(QLabel, 'settings_title').text() == title
        assert tuple(label.text() for label in dialog.findChildren(QLabel, 'settings_heading')) == headings
        assert window.font().families() == list(candidates)
        inspected = []
        for label in dialog.findChildren(QLabel):
            if not label.text():
                continue
            text_layout = QTextLayout(label.text(), label.font())
            text_layout.beginLayout()
            line = text_layout.createLine()
            line.setLineWidth(10000)
            text_layout.endLayout()
            for run in text_layout.glyphRuns():
                actual = run.rawFont().familyName()
                assert actual.casefold() == expected.casefold(), (label.text(), actual, expected)
                inspected.append(actual)
            if label.objectName() in ('settings_title', 'settings_heading'):
                assert label.font().weight() == QFont.Weight.DemiBold
        assert inspected
    finally:
        close(qapp, window, dialog)


@pytest.mark.parametrize('language', ('en', 'zh-CN', 'zh-TW'))
def test_compact_update_header_has_no_idle_copy_or_removed_display_options(qapp, monkeypatch, language):
    set_language(language)
    window, dialog = open_settings(qapp, monkeypatch)
    try:
        texts = [label.text() for label in dialog.findChildren(QLabel)]
        for removed in ('Official stable releases', 'Updates are checked only on request.',
                        'Thumbnail strip', 'Image information'):
            assert tr(removed) not in texts
        assert dialog.status_label.text() == ''
        assert dialog.status_label.isHidden()
        assert dialog.version_badge.text() == '0.1.3'
        heading = dialog.update_card.findChild(QLabel, 'settings_heading')
        heading_center = heading.mapTo(dialog.update_card, heading.rect().center()).y()
        button_center = dialog.check_button.mapTo(dialog.update_card, dialog.check_button.rect().center()).y()
        assert abs(heading_center - button_center) <= 1
        assert dialog.check_button.height() <= 30
        assert dialog.check_button.width() <= dialog.check_button.sizeHint().width()
        assert dialog.check_button.width() < dialog.update_card.width() // 2
    finally:
        close(qapp, window, dialog)


def test_update_activity_and_errors_reveal_status_without_idle_placeholder(qapp, monkeypatch):
    window, dialog = open_settings(qapp, monkeypatch)
    try:
        assert dialog.status_label.isHidden()
        dialog._status(dialog._serial, tr('Downloading update…'))
        assert dialog.status_label.isVisible()
        assert dialog.status_label.text() == tr('Downloading update…')
        dialog._failed(dialog._serial, 'A diagnostic with {raw} <details>')
        assert dialog.status_label.isVisible()
        assert dialog.status_label.text() == 'A diagnostic with {raw} <details>'
        assert dialog.check_button.isEnabled()
    finally:
        close(qapp, window, dialog)


def test_explicit_check_reveals_checking_progress_and_success_only_after_click(qapp, monkeypatch):
    import threading
    import time
    entered, finish = threading.Event(), threading.Event()
    result = updater.Release(updater.Version.parse('0.1.3'), 'v0.1.3',
                             updater.RELEASES_URL + '/tag/v0.1.3', None, None)
    def check(*, cancelled):
        entered.set()
        finish.wait(3)
        return result
    check_mock = Mock(side_effect=check)
    monkeypatch.setattr(updater, 'check_for_update', check_mock)
    window, dialog = open_settings(qapp, monkeypatch)
    try:
        check_mock.assert_not_called()
        assert dialog.status_label.isHidden()
        dialog.check_button.click()
        assert entered.wait(1)
        assert dialog.status_label.isVisible()
        assert dialog.status_label.text() == tr('Checking for a stable release…')
        assert dialog.progress_bar.isVisible()
        assert not dialog.check_button.isEnabled()
        dialog._progress(dialog._serial, 1, 4)
        assert dialog.progress_bar.value() == 25
        finish.set()
        deadline = time.monotonic() + 3
        while dialog._busy and time.monotonic() < deadline:
            QTest.qWait(10)
        assert not dialog._busy
        assert dialog.status_label.isVisible()
        assert dialog.status_label.text() == tr("You're up to date. Latest stable release: {version}", version='0.1.3')
        assert dialog.progress_bar.isHidden()
        assert dialog.check_button.isEnabled()
        check_mock.assert_called_once()
    finally:
        finish.set()
        close(qapp, window, dialog)


def test_install_and_launch_failure_keep_real_status_visible(qapp, monkeypatch, tmp_path):
    window, dialog = open_settings(qapp, monkeypatch)
    monkeypatch.setattr(updater, 'sys', SimpleNamespace(platform='win32'))
    executable = tmp_path / 'Luma.exe'
    executable.write_bytes(b'MZ synthetic test fixture')
    installed = updater.ManagedUpdate('0.1.4', tmp_path, executable, tmp_path / 'old.exe', tmp_path)
    launch = Mock(side_effect=OSError('Synthetic launch failure {details}'))
    monkeypatch.setattr(updater, 'launch_verified_update', launch)
    try:
        assert dialog.status_label.isHidden()
        dialog._completed(dialog._serial, installed)
        assert dialog.status_label.isVisible()
        assert str(executable) in dialog.status_label.text()
        assert dialog.launch_button.isVisible()
        assert dialog.download_button.isHidden()
        dialog.launch_button.click()
        for _ in range(100):
            QTest.qWait(10)
            if not dialog._busy:
                break
        assert not dialog._busy
        launch.assert_called_once()
        assert dialog.status_label.isVisible()
        assert 'Synthetic launch failure {details}' in dialog.status_label.text()
        assert dialog.isVisible()
    finally:
        close(qapp, window, dialog)


def test_stale_update_callbacks_do_not_reveal_an_idle_status(qapp, monkeypatch):
    window, dialog = open_settings(qapp, monkeypatch)
    try:
        dialog._status(dialog._serial + 1, 'Stale activity')
        dialog._failed(dialog._serial + 1, 'Stale failure')
        assert dialog.status_label.isHidden()
        assert dialog.status_label.text() == ''
    finally:
        close(qapp, window, dialog)


@pytest.mark.parametrize('language', ('en', 'zh-CN', 'zh-TW'))
def test_settings_omits_integration_and_extra_labels_with_accessible_top_close(qapp, monkeypatch, language):
    set_language(language)
    status = Mock()
    monkeypatch.setattr(shell_settings.shell, 'status', status)
    window = ViewerWindow()
    window.show()
    dialog = updater.open_update_dialog(window, '0.1.3')
    try:
        qapp.processEvents()
        texts = [label.text() for label in dialog.findChildren(QLabel)]
        assert 'Luma' not in texts
        assert tr('Windows integration') not in texts
        assert tr('Language follows your system') not in texts
        assert not dialog.findChildren(shell_settings.ExplorerIntegrationPanel)
        status.assert_not_called()
        button = dialog.close_button
        interface = QAccessible.queryAccessibleInterface(button)
        assert button.text() == ''
        assert not button.icon().isNull()
        assert interface.role() == QAccessible.Role.Button
        assert interface.text(QAccessible.Text.Name) == tr('Close')
        assert button.toolTip() == tr('Close')
        assert button.width() >= 32 and button.height() >= 32
        assert button.geometry().top() < 60
        assert button.geometry().right() > dialog.width() - 60
        assert button.focusPolicy() == Qt.FocusPolicy.StrongFocus
    finally:
        close(qapp, window, dialog)


@pytest.mark.parametrize('busy', (False, True))
@pytest.mark.parametrize('method', ('x', 'escape', 'native', 'space'))
def test_top_close_escape_and_native_close_cancel_work_and_release_dialog(qapp, monkeypatch, busy, method):
    import threading
    entered, finish, stopped = threading.Event(), threading.Event(), threading.Event()
    def pending(cancelled, progress, status):
        entered.set()
        finish.wait(3)
        stopped.set()
        return None
    window, dialog = open_settings(qapp, monkeypatch)
    try:
        if busy:
            dialog._start(pending)
            assert entered.wait(1)
        cancelled = dialog._cancel
        assert dialog.close_button.text() == ''
        assert dialog.close_button.accessibleName() == tr('Cancel' if busy else 'Close')
        if method == 'x':
            dialog.close_button.click()
        elif method == 'escape':
            QTest.keyClick(dialog, Qt.Key.Key_Escape)
        elif method == 'space':
            dialog.close_button.setFocus()
            QTest.keyClick(dialog.close_button, Qt.Key.Key_Space)
        else:
            dialog.close()
        assert cancelled.is_set()
        assert window._luma_update_dialog is None
        finish.set()
        if busy:
            assert stopped.wait(1)
    finally:
        finish.set()
        window.close()
        qapp.processEvents()


def test_close_is_borderless_until_keyboard_navigation(qapp, monkeypatch):
    window, dialog = open_settings(qapp, monkeypatch)
    try:
        button = dialog.close_button

        def top_edge():
            image = dialog.grab().toImage()
            point = button.mapTo(dialog, QPoint(button.width() // 2, 0))
            ratio = image.devicePixelRatio()
            # At fractional DPI the logical top can fall between pixel rows;
            # sample the first inside row rather than rounding outside it.
            return image.pixelColor(round(point.x() * ratio), ceil(point.y() * ratio)).name()

        assert button.hasFocus()  # Initial programmatic focus must not add a box.
        assert button.property('keyboard_focus') is False
        assert top_edge() == '#191919'
        QTest.keyClick(button, Qt.Key.Key_Tab)
        assert not button.hasFocus()
        QTest.keyClick(qapp.focusWidget(), Qt.Key.Key_Backtab)
        assert button.hasFocus()
        assert button.property('keyboard_focus') is True
        assert top_edge() == '#b5b5b5'
        # A pointer press removes the keyboard-only cue before activation.
        QTest.mousePress(button, Qt.MouseButton.LeftButton)
        assert button.property('keyboard_focus') is False
        QTest.mouseRelease(button, Qt.MouseButton.LeftButton,
                           Qt.KeyboardModifier.NoModifier, QPoint(-1, -1))
        dialog.display_panel.background.setFocus()
        assert button.property('keyboard_focus') is False
        button.setFocus(Qt.FocusReason.MouseFocusReason)
        assert button.property('keyboard_focus') is False
    finally:
        close(qapp, window, dialog)


def test_close_hover_uses_subtle_fill_without_outline(qapp, monkeypatch):
    window, dialog = open_settings(qapp, monkeypatch)
    try:
        button = dialog.close_button
        # Offscreen does not reliably generate native pointer hover. Render the
        # real widget's hovered style option to check the actual QSS treatment.
        option = QStyleOptionButton()
        button.initStyleOption(option)
        option.state |= QStyle.StateFlag.State_MouseOver
        option.state &= ~QStyle.StateFlag.State_HasFocus
        rendered = QPixmap(button.size())
        rendered.fill(Qt.GlobalColor.transparent)
        painter = QPainter(rendered)
        button.style().drawControl(QStyle.ControlElement.CE_PushButton, option, painter, button)
        painter.end()
        image = rendered.toImage()
        assert image.pixelColor(button.width() // 2, 0).name() == '#373737'
        assert image.pixelColor(button.width() // 2, 3).name() == '#373737'
        assert button.size().width() == button.size().height() == 32
    finally:
        close(qapp, window, dialog)


@pytest.mark.parametrize('kind, width', (('close', 2.25), ('display', 1.5), ('update', 1.5)))
def test_settings_close_stroke_is_thicker_without_changing_other_icons(qapp, kind, width):
    painter = Mock()
    settings_widgets._SettingsIconEngine(kind).paint(
        painter, QRect(0, 0, 24, 24), QIcon.Mode.Normal, QIcon.State.Off)
    assert painter.setPen.call_args.args[0].widthF() == width


@pytest.mark.parametrize('language', ['en', 'zh-CN', 'zh-TW'])
def test_version_badge_is_next_to_heading_and_appearance_has_no_scope_label(qapp, language):
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QLabel
    set_language(language)
    window = ViewerWindow()
    window.show()
    dialog = updater.UpdateDialog(window, '0.1.4')
    dialog.show()
    try:
        qapp.processEvents()
        heading = dialog.update_card.findChild(QLabel, 'settings_heading')
        position = heading.mapTo(dialog, QPoint(heading.width(), heading.height() // 2))
        badge_position = dialog.version_badge.mapTo(dialog, QPoint(0, dialog.version_badge.height() // 2))
        assert 0 < badge_position.x() - position.x() <= 12
        assert abs(badge_position.y() - position.y()) <= 1
        assert not hasattr(dialog.display_panel, 'scope_badge')
        assert dialog.check_button.isVisible()
    finally:
        close(qapp, window, dialog)
