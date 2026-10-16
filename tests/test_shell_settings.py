from types import SimpleNamespace
from unittest.mock import Mock

from PySide6.QtCore import Qt, QPoint
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox
import pytest

from luma import shell_settings as settings


def setup_panel(monkeypatch, state="not_installed"):
    current = {"state": state}
    monkeypatch.setattr(settings.shell, "status", lambda: settings.shell.Status(current["state"], thumbnails_enabled=current["state"] in ("installed", "modified", "stale")))
    install = Mock(side_effect=lambda path: current.update(state="installed"))
    uninstall = Mock(side_effect=lambda: current.update(state="association_only"))
    monkeypatch.setattr(settings.shell, "install", install)
    monkeypatch.setattr(settings.shell, "disable_thumbnails", uninstall)
    panel = settings.ExplorerIntegrationPanel()
    panel.show()
    return panel, install, uninstall, current


def test_open_settings_does_not_register_or_open_system_settings(qapp, monkeypatch):
    opener = Mock()
    monkeypatch.setattr(settings.QDesktopServices, "openUrl", opener)
    panel, install, uninstall, _ = setup_panel(monkeypatch)
    try:
        assert not panel.toggle.isChecked()
        install.assert_not_called()
        uninstall.assert_not_called()
        opener.assert_not_called()
    finally:
        panel.close()


def test_cancel_preserves_registration_state(qapp, monkeypatch):
    monkeypatch.setattr(settings.QMessageBox, "question", lambda *a: QMessageBox.StandardButton.No)
    panel, install, uninstall, _ = setup_panel(monkeypatch)
    try:
        QTest.mouseClick(panel.toggle, Qt.MouseButton.LeftButton, pos=QPoint(8, panel.toggle.height() // 2))
        assert not panel.toggle.isChecked()
        install.assert_not_called()
        uninstall.assert_not_called()
    finally:
        panel.close()


def test_explicit_enable_disable_and_repair(qapp, monkeypatch):
    monkeypatch.setattr(settings.QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Yes)
    panel, install, uninstall, current = setup_panel(monkeypatch)
    try:
        QTest.mouseClick(panel.toggle, Qt.MouseButton.LeftButton, pos=QPoint(8, panel.toggle.height() // 2))
        assert panel.toggle.isChecked() and install.call_count == 1
        QTest.mouseClick(panel.toggle, Qt.MouseButton.LeftButton, pos=QPoint(8, panel.toggle.height() // 2))
        assert not panel.toggle.isChecked() and uninstall.call_count == 1
        current["state"] = "stale"
        panel.refresh()
        QTest.mouseClick(panel.repair, Qt.MouseButton.LeftButton)
        assert panel.toggle.isChecked() and install.call_count == 2
    finally:
        panel.close()


def test_failure_keeps_toggle_off_and_reports_error(qapp, monkeypatch):
    monkeypatch.setattr(settings.QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Yes)
    warning = Mock()
    monkeypatch.setattr(settings.QMessageBox, "warning", warning)
    panel, install, _, _ = setup_panel(monkeypatch)
    install.side_effect = settings.shell.ShellIntegrationError("Conflict")
    try:
        QTest.mouseClick(panel.toggle, Qt.MouseButton.LeftButton, pos=QPoint(8, panel.toggle.height() // 2))
        assert not panel.toggle.isChecked()
        assert "Conflict" in panel.status_label.text()
        warning.assert_called_once()
    finally:
        panel.close()


@pytest.mark.parametrize("state", ["unavailable", "blocked"])
def test_unsupported_or_unreadable_state_disables_mutation(qapp, monkeypatch, state):
    panel, install, uninstall, _ = setup_panel(monkeypatch, state)
    try:
        assert not panel.toggle.isEnabled() and not panel.repair.isEnabled()
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

def test_association_only_modified_state_keeps_thumbnail_toggle_off(qapp, monkeypatch):
    monkeypatch.setattr(settings.shell, "status",
                        lambda: settings.shell.Status("modified", modified_values=1, thumbnails_enabled=False))
    panel = settings.ExplorerIntegrationPanel()
    try:
        assert not panel.toggle.isChecked()
        assert "Another app" in panel.status_label.text()
    finally:
        panel.close()
