"""Portable update policy checks, without a Windows process or registry write.

These verify that the GUI cannot confuse a separately downloaded copy with an
upgrade. Native Windows installation, DLL locks and shortcut behavior require
the acceptance checks in docs/BUILDING.md.
"""
from __future__ import annotations

from pathlib import Path
import time
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QWidget

from luma import updater
from luma.update_installation import InstallationContext


def new_release(*, packages=True):
    version = updater.Version.parse("0.1.4")
    url = f"{updater.RELEASES_URL}/tag/v{version}"
    download = f"https://github.com/{updater.REPOSITORY}/releases/download/v{version}/"
    def asset(name):
        return updater.ReleaseAsset(name, download + name, 123)
    return updater.Release(
        version, f"v{version}", url,
        asset(f"Luma-{version}-windows-x64.zip") if packages else None,
        asset("SHA256SUMS") if packages else None,
        asset(f"Luma-{version}-windows-x64-setup.exe") if packages else None,
    )


def wait_idle(qapp, dialog):
    deadline = time.monotonic() + 3
    while dialog._busy and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.005)
    qapp.processEvents()
    assert not dialog._busy


def files_in(directory):
    return {path.relative_to(directory).as_posix(): path.read_bytes()
            for path in directory.rglob("*") if path.is_file()}


@pytest.fixture
def original(tmp_path):
    directory = tmp_path / "Original portable 图像"
    contents = {
        "Luma.exe": b"MZ-original-0.1.3",
        "LumaThumbnail.dll": b"MZ-original-registered-thumbnail-bridge",
        "_internal/runtime.dll": b"original-runtime",
        "_internal/user-photo.png": b"user-image-inside-runtime-folder",
        "holiday.exr": b"user-original-photo",
        "settings.json": b'{"keep":"original user settings"}',
        "unrelated/notes.txt": b"unrelated file",
    }
    for relative, value in contents.items():
        path = directory / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
    return directory


@pytest.fixture
def blocked_install_actions(monkeypatch):
    calls = []
    def deny(name):
        def called(*args, **kwargs):
            calls.append(name)
            raise AssertionError(f"Manual portable policy must not call {name}")
        return called
    for name in ("install_release", "download_setup", "apply_verified_setup",
                 "default_install_root", "launch_verified_update"):
        monkeypatch.setattr(updater, name, deny(name))
    monkeypatch.setattr(updater.subprocess, "Popen", deny("Popen"))
    return calls


@pytest.fixture
def portable_dialog(qapp, original, monkeypatch):
    context = InstallationContext("portable", original / "Luma.exe")
    monkeypatch.setattr(updater, "sys", SimpleNamespace(
        platform="win32", executable=str(context.executable), frozen=True))
    monkeypatch.setattr(updater, "detect_installation", lambda: context)
    parent = QWidget()
    dialog = updater.open_update_dialog(parent, "0.1.3")
    yield dialog
    dialog.reject()
    qapp.processEvents()
    parent.close()


def assert_manual_portable_state(dialog):
    assert dialog.installed is None
    assert dialog.download_button.isHidden()
    assert dialog.launch_button.isHidden()
    assert not dialog.release_button.isHidden()
    guidance = dialog.status_label.text()
    assert "portable" in guidance.casefold()
    assert "Setup" in guidance and "ZIP" in guidance
    assert "new folder" in guidance.casefold() and "shortcut" in guidance.casefold()
    assert "portable folder is unchanged" in guidance.casefold()
    assert "is installed" not in guidance.casefold()
    assert "ready to launch" not in guidance.casefold()


@pytest.mark.parametrize("packages", [True, False])
def test_portable_check_offers_manual_release_without_install_even_when_assets_exist(
        qapp, original, portable_dialog, blocked_install_actions, monkeypatch, packages):
    before = files_in(original)
    release = new_release(packages=packages)
    monkeypatch.setattr(updater, "check_for_update", lambda **kwargs: release)
    portable_dialog.check()
    wait_idle(qapp, portable_dialog)
    assert_manual_portable_state(portable_dialog)
    assert blocked_install_actions == []
    assert files_in(original) == before


def test_direct_portable_download_cannot_bypass_the_hidden_button(
        qapp, original, portable_dialog, blocked_install_actions):
    before = files_in(original)
    portable_dialog.release = new_release()
    portable_dialog.download()
    wait_idle(qapp, portable_dialog)
    assert not portable_dialog._busy
    assert portable_dialog.installed is None
    assert portable_dialog.download_button.isHidden()
    assert portable_dialog.launch_button.isHidden()
    assert blocked_install_actions == []
    assert files_in(original) == before


def test_download_rechecks_portable_identity_instead_of_trusting_cached_managed_mode(
        qapp, original, portable_dialog, blocked_install_actions):
    before = files_in(original)
    portable_dialog.release = new_release()
    portable_dialog.installation = InstallationContext(
        "managed", original / "Luma.exe", original, "0.1.3")
    portable_dialog.download()
    wait_idle(qapp, portable_dialog)
    assert portable_dialog.installation.mode == "portable"
    assert portable_dialog.installed is None
    assert blocked_install_actions == []
    assert files_in(original) == before


def test_repeated_check_discards_stale_staged_copy_and_preserves_original_entry(
        qapp, original, portable_dialog, blocked_install_actions, monkeypatch, tmp_path):
    before = files_in(original)
    release = new_release()
    monkeypatch.setattr(updater, "check_for_update", lambda **kwargs: release)
    separate = tmp_path / "AppData" / "updates" / "Luma-0.1.4"
    separate.mkdir(parents=True)
    (separate / "Luma.exe").write_bytes(b"MZ-separate-copy")
    portable_dialog.installed = updater.InstalledUpdate("0.1.4", separate, separate / "Luma.exe")
    for _ in range(2):
        portable_dialog.check()
        wait_idle(qapp, portable_dialog)
        assert_manual_portable_state(portable_dialog)
        assert files_in(original) == before
    assert (separate / "Luma.exe").read_bytes() == b"MZ-separate-copy"
    assert blocked_install_actions == []


def test_legacy_staged_completion_cannot_claim_installation_or_offer_launch(
        qapp, original, portable_dialog, blocked_install_actions, tmp_path):
    before = files_in(original)
    separate = tmp_path / "Separate download"
    separate.mkdir()
    executable = separate / "Luma.exe"
    executable.write_bytes(b"MZ-downloaded-but-not-applied")
    result = updater.InstalledUpdate("0.1.4", separate, executable)
    portable_dialog._completed(portable_dialog._serial, result)
    assert portable_dialog.installed is None
    assert portable_dialog.launch_button.isHidden()
    assert "is installed" not in portable_dialog.status_label.text().casefold()
    assert "ready to launch" not in portable_dialog.status_label.text().casefold()
    assert blocked_install_actions == []
    assert files_in(original) == before


def test_legacy_staged_result_cannot_be_launched_through_a_direct_call(
        qapp, original, portable_dialog, blocked_install_actions, tmp_path):
    before = files_in(original)
    separate = tmp_path / "Separate download"
    separate.mkdir()
    executable = separate / "Luma.exe"
    executable.write_bytes(b"MZ-downloaded-but-not-applied")
    portable_dialog.installed = updater.InstalledUpdate("0.1.4", separate, executable)
    portable_dialog.launch()
    wait_idle(qapp, portable_dialog)
    assert portable_dialog.isVisible()
    assert blocked_install_actions == []
    assert files_in(original) == before


def test_manual_release_button_only_opens_the_official_checked_release(
        qapp, original, portable_dialog, blocked_install_actions, monkeypatch):
    before = files_in(original)
    release = new_release()
    monkeypatch.setattr(updater, "check_for_update", lambda **kwargs: release)
    opened = []
    monkeypatch.setattr(updater, "QDesktopServices", SimpleNamespace(
        openUrl=lambda url: opened.append(url.toString()) or True))
    portable_dialog.check()
    wait_idle(qapp, portable_dialog)
    portable_dialog.release_button.click()
    assert opened == ["https://github.com/Biscuits-Lab/Luma/releases/tag/v0.1.4"]
    assert blocked_install_actions == []
    assert files_in(original) == before


def test_source_mode_is_also_read_only_and_cannot_install_from_direct_call(
        qapp, original, portable_dialog, blocked_install_actions, monkeypatch):
    before = files_in(original)
    monkeypatch.setattr(updater, "detect_installation", lambda: InstallationContext(
        "source", Path("python")))
    monkeypatch.setattr(updater, "check_for_update", lambda **kwargs: new_release())
    portable_dialog.check()
    wait_idle(qapp, portable_dialog)
    assert portable_dialog.download_button.isHidden()
    assert portable_dialog.launch_button.isHidden()
    assert not portable_dialog.release_button.isHidden()
    portable_dialog.download()
    wait_idle(qapp, portable_dialog)
    assert portable_dialog.installed is None
    assert blocked_install_actions == []
    assert files_in(original) == before
