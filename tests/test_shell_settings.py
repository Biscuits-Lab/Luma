from unittest.mock import Mock

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox, QCheckBox
import pytest

from luma import shell_settings as settings


def setup_panel(monkeypatch, state="not_installed", detail=""):
    current = {"state": state}
    monkeypatch.setattr(settings.shell, "status", lambda: settings.shell.Status(
        current["state"], detail=detail,
        thumbnails_enabled=current["state"] in ("installed", "modified", "stale")))
    install = Mock(side_effect=lambda path: current.update(state="installed"))
    uninstall = Mock()
    monkeypatch.setattr(settings.shell, "install", install)
    monkeypatch.setattr(settings.shell, "disable_thumbnails", uninstall)
    panel = settings.ExplorerIntegrationPanel()
    panel.show()
    return panel, install, uninstall, current


def test_open_settings_has_no_opt_in_and_does_not_register(qapp, monkeypatch):
    opener = Mock()
    monkeypatch.setattr(settings.QDesktopServices, "openUrl", opener)
    panel, install, uninstall, _ = setup_panel(monkeypatch)
    try:
        assert panel.findChildren(QCheckBox) == []
        install.assert_not_called()
        uninstall.assert_not_called()
        opener.assert_not_called()
    finally:
        panel.close()


def test_cancel_preserves_registration_state(qapp, monkeypatch):
    monkeypatch.setattr(settings.QMessageBox, "question", lambda *a: QMessageBox.StandardButton.No)
    panel, install, uninstall, _ = setup_panel(monkeypatch)
    try:
        QTest.mouseClick(panel.repair, Qt.MouseButton.LeftButton)
        install.assert_not_called()
        uninstall.assert_not_called()
        assert "not ready" in panel.status_label.text()
    finally:
        panel.close()


def test_explicit_repair_is_available_without_thumbnail_toggle(qapp, monkeypatch):
    monkeypatch.setattr(settings.QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Yes)
    panel, install, uninstall, _ = setup_panel(monkeypatch, "stale")
    try:
        QTest.mouseClick(panel.repair, Qt.MouseButton.LeftButton)
        install.assert_called_once()
        uninstall.assert_not_called()
        assert "registered" in panel.status_label.text()
    finally:
        panel.close()


def test_failure_reports_error_without_changing_state(qapp, monkeypatch):
    monkeypatch.setattr(settings.QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Yes)
    warning = Mock()
    monkeypatch.setattr(settings.QMessageBox, "warning", warning)
    panel, install, _, _ = setup_panel(monkeypatch)
    install.side_effect = settings.shell.ShellIntegrationError("Conflict")
    try:
        QTest.mouseClick(panel.repair, Qt.MouseButton.LeftButton)
        assert "Conflict" in panel.status_label.text()
        warning.assert_called_once()
    finally:
        panel.close()


@pytest.mark.parametrize("state", ["unavailable", "blocked"])
def test_unsupported_or_unreadable_state_disables_mutation(qapp, monkeypatch, state):
    panel, install, uninstall, _ = setup_panel(monkeypatch, state)
    try:
        assert not panel.repair.isEnabled()
        install.assert_not_called()
        uninstall.assert_not_called()
    finally:
        panel.close()


def test_default_apps_is_separate_user_action(qapp, monkeypatch):
    monkeypatch.setattr(settings.sys, "platform", "win32")
    opener = Mock(return_value=True)
    monkeypatch.setattr(settings.QDesktopServices, "openUrl", opener)
    panel, install, uninstall, _ = setup_panel(monkeypatch)
    try:
        QTest.mouseClick(panel.defaults, Qt.MouseButton.LeftButton)
        assert opener.call_args.args[0].toString() == "ms-settings:defaultapps"
        install.assert_not_called()
        uninstall.assert_not_called()
    finally:
        panel.close()


def test_effective_association_diagnostic_is_visible(qapp, monkeypatch):
    diagnostic = "Windows did not report a thumbnail provider for .hdr."
    panel, install, _, _ = setup_panel(monkeypatch, "installed", diagnostic)
    try:
        assert diagnostic in panel.status_label.text()
        install.assert_not_called()
    finally:
        panel.close()


def wait_until(qapp, predicate, timeout=3):
    import time
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not predicate():
        qapp.processEvents()
        time.sleep(0.005)
    qapp.processEvents()
    assert predicate()


def setup_automatic_registration(monkeypatch):
    monkeypatch.setattr(settings.sys, "platform", "win32")
    monkeypatch.setattr(settings.sys, "frozen", True, raising=False)
    monkeypatch.setattr(settings, "_startup_registration_started", False)
    monkeypatch.setattr(settings, "_startup_registration_error", "")


def test_automatic_registration_runs_off_gui_thread_once(qapp, monkeypatch):
    import threading
    from PySide6.QtWidgets import QWidget
    setup_automatic_registration(monkeypatch)
    called = []
    main_thread = threading.get_ident()
    monkeypatch.setattr(settings.shell, "prepare_startup_registration",
                        lambda path: called.append(threading.get_ident()))
    window = QWidget()
    window.show()
    try:
        controller = settings.start_automatic_registration(window)
        assert controller is not None
        assert settings.start_automatic_registration(window) is None
        wait_until(qapp, lambda: controller.worker._closed)
        assert len(called) == 1 and called[0] != main_thread
        assert not window.findChildren(QMessageBox)
    finally:
        window.close()


def test_close_during_registration_suppresses_late_error_dialog(qapp, monkeypatch):
    import threading
    from PySide6.QtWidgets import QWidget
    setup_automatic_registration(monkeypatch)
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    def register(path):
        started.set()
        release.wait(3)
        finished.set()
        raise settings.shell.ShellIntegrationError("Late failure")
    monkeypatch.setattr(settings.shell, "prepare_startup_registration", register)
    window = QWidget()
    window.show()
    controller = settings.start_automatic_registration(window)
    try:
        wait_until(qapp, started.is_set)
        window.close()
        assert controller.closed and controller.worker._closed
        release.set()
        wait_until(qapp, finished.is_set)
        controller.worker._thread.join(timeout=1)
        qapp.processEvents()
        assert not window.findChildren(QMessageBox)
    finally:
        release.set()
        window.close()


def test_registration_failure_is_nonmodal_and_retained_for_settings(qapp, monkeypatch):
    from PySide6.QtWidgets import QWidget
    setup_automatic_registration(monkeypatch)
    def fail(path):
        raise settings.shell.ShellIntegrationError("Foreign provider preserved")
    monkeypatch.setattr(settings.shell, "prepare_startup_registration", fail)
    window = QWidget()
    window.show()
    try:
        settings.start_automatic_registration(window)
        wait_until(qapp, lambda: bool(window.findChildren(QMessageBox)))
        message = window.findChildren(QMessageBox)[0]
        assert message.windowModality() == Qt.WindowModality.NonModal
        assert "Foreign provider preserved" in settings._startup_registration_error
        assert "Foreign provider preserved" in message.text()
        message.close()
    finally:
        window.close()
