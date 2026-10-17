"""Filesystem and fake-HKCU contracts for the stable installed launch entry."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath

import pytest

from luma import managed_launch as managed, shell_integration as shell
from test_shell_integration import FakeRegistry, SimulatedCrash


def payload(root, version, char):
    digest = char * 64
    directory = root / "versions" / f"{version}-{digest[:24]}"
    directory.mkdir(parents=True)
    (directory / "Luma.exe").write_bytes(b"MZ actual test payload " + version.encode())
    (directory / "LumaThumbnail.dll").write_bytes(b"MZ test DLL")
    (directory / "luma-install.json").write_text(json.dumps({
        "managed_install": True, "layout": "versions-v1", "version": version,
        "payload_id": directory.name, "bundle_sha256": digest}), encoding="ascii")
    return directory / "Luma.exe"


@pytest.fixture
def installation(tmp_path, monkeypatch):
    root = tmp_path / "Installed Luma"
    root.mkdir()
    (root / "Luma.exe").write_bytes(b"MZ immutable launcher")
    owner = b"LUMA-LAUNCHER-1\n" + hashlib.sha256((root / "Luma.exe").read_bytes()).hexdigest().encode() + b"\n"
    (root / managed.OWNER_NAME).write_bytes(owner)
    old = payload(root, "0.1.3", "a")
    new = payload(root, "0.1.4", "b")
    registry = FakeRegistry()

    def windows(path):
        value = str(path)
        if value.startswith(str(root)):
            return str(PureWindowsPath(r"C:\Test\Installed Luma") / Path(path).relative_to(root))
        return value

    windows_path = shell._windows_path
    monkeypatch.setattr(managed, "_registry_path", windows)
    monkeypatch.setattr(shell, "_windows_path", lambda value, filename: windows_path(windows(value), filename))
    monkeypatch.setattr(shell, "_runtime_guard", lambda: None)
    monkeypatch.setattr(shell, "_checked_executable", windows)
    return root, old, new, registry, windows


def activate(installation, *, old=False):
    root, previous, current, registry, _ = installation
    return managed.activate_managed_installation(previous if old else current, registry)


def snapshot(installation):
    root, _, _, registry, _ = installation
    return (managed._read(root / managed.POINTER_NAME, 512, missing=True),
            copy.deepcopy(registry.values), managed._read(root / managed.JOURNAL_NAME, managed.MAX_JOURNAL, missing=True))


def test_fresh_activation_owns_payload_but_launches_stable_root(installation):
    root, _, current, registry, windows = installation
    assert activate(installation).changed_values > 0
    selected = managed.read_active_payload(root)
    assert selected.executable == current
    assert selected.version == "0.1.4"
    assert shell.registration_owner(registry).executable == windows(current)
    data = shell._read_manifest(registry)[0]["data"]
    assert data["managed_payload"] == windows(current)
    assert shell._owned_by_executable(data, windows(current))
    assert registry.read(*shell._APPLICATION_COMMAND).data == f'"{windows(root / "Luma.exe")}" "%1"'
    for entry in data["entries"]:
        if shell._path_role(shell._address(entry)) == "dll":
            assert str(PureWindowsPath(windows(current)).parent) in entry["written"]["data"]
    assert not (root / managed.JOURNAL_NAME).exists()
    assert not any("userchoice" in event[1].lower() for event in registry.log if event[0] in ("write", "delete"))


def test_upgrade_preserves_old_payload_and_root_bytes(installation):
    root, old, new, registry, windows = installation
    activate(installation, old=True)
    before = {path: path.read_bytes() for path in (root / "Luma.exe", old, old.parent / "LumaThumbnail.dll")}
    activate(installation)
    assert managed.read_active_payload(root).executable == new
    assert all(path.read_bytes() == value for path, value in before.items())
    assert shell.registration_owner(registry).executable == windows(new)


def test_legacy_direct_registration_remains_explicit_and_migrates(installation):
    root, old, new, registry, windows = installation
    shell.install(old, registry)
    assert registry.read(*shell._APPLICATION_COMMAND).data == f'"{windows(old)}" "%1"'
    assert "managed_payload" not in shell._read_manifest(registry)[0]["data"]
    activate(installation)
    assert managed.read_active_payload(root).executable == new
    assert shell.registration_owner(registry).executable == windows(new)


def removed_command(installation):
    _, old, _, registry, windows = installation
    command = shell.Value(shell.REG_SZ, f'"{windows(old)}" "%1"')
    registry.seed(*shell._APPLICATION_COMMAND, command)
    for path in old.parent.iterdir():
        path.unlink()
    old.parent.rmdir()
    return command


def test_reinstall_after_uninstall_repairs_removed_payload_open_with_command(installation):
    root, _, new, registry, windows = installation
    removed_command(installation)
    choice = (shell.CLASSES + r"\.png", "")
    registry.seed(*choice, shell.Value(shell.REG_SZ, "Other.Image"))
    activate(installation)
    assert managed.read_active_payload(root).executable == new
    assert shell.registration_owner(registry).executable == windows(new)
    assert registry.read(*choice).data == "Other.Image"
    shell.uninstall(registry)
    assert registry.read(*shell._APPLICATION_COMMAND) is None


@pytest.mark.parametrize("directory", ["existing", "empty", "symlink", "foreign", "arguments"])
def test_reinstall_does_not_adopt_ambiguous_or_changed_command(installation, tmp_path, directory):
    root, old, _, registry, windows = installation
    removed_command(installation)
    if directory == "existing":
        old.parent.mkdir()
        old.write_bytes(b"unvalidated executable")
    elif directory == "empty":
        old.parent.mkdir()
    elif directory == "symlink":
        try:
            old.parent.symlink_to(tmp_path, target_is_directory=True)
        except OSError:
            pytest.skip("Creating directory symlinks requires Windows developer mode")
    else:
        command = (f'"D:\\Other Luma\\versions\\{old.parent.name}\\Luma.exe" "%1"'
                   if directory == "foreign" else f'"{windows(old)}" --extra "%1"')
        registry.seed(*shell._APPLICATION_COMMAND, shell.Value(shell.REG_SZ, command))
    before = snapshot(installation)
    with pytest.raises(shell.RegistrationConflict):
        activate(installation)
    assert snapshot(installation) == before


def test_removed_command_transaction_failure_restores_old_bytes_and_pointer(installation):
    root, _, _, registry, _ = installation
    original = removed_command(installation)
    registry.fail_target_calls = {22}
    with pytest.raises(shell.ShellIntegrationError, match="rolled back"):
        activate(installation)
    assert registry.read(*shell._APPLICATION_COMMAND) == original
    assert shell._read_manifest(registry)[0] is None
    assert not (root / managed.POINTER_NAME).exists()
    activate(installation)


def test_removed_command_crash_can_recover_and_retry(installation):
    root, _, new, registry, _ = installation
    removed_command(installation)
    registry.crash_on = 22
    with pytest.raises(SimulatedCrash):
        activate(installation)
    activate(installation)
    assert managed.read_active_payload(root).executable == new
    assert not (root / managed.JOURNAL_NAME).exists()


def test_same_target_repair_and_disabled_thumbnails_keep_payload_owner(installation):
    root, _, new, registry, windows = installation
    activate(installation)
    before = snapshot(installation)
    assert activate(installation).changed_values == 0
    assert snapshot(installation) == before
    shell.disable_thumbnails(registry)
    assert shell.registration_owner(registry).executable == windows(new)
    assert managed.read_active_payload(root).executable == new
    activate(installation)
    assert shell._read_manifest(registry)[0]["data"]["thumbnails_enabled"] is True


@pytest.mark.parametrize("change", ["missing", "crlf", "truncated", "foreign_hash", "traversal", "extra", "bom", "leading_zero"])
def test_pointer_invalidity_fails_closed(installation, change):
    root, _, _, _, _ = installation
    activate(installation)
    path = root / managed.POINTER_NAME
    original = path.read_bytes()
    edits = {"crlf": original.replace(b"\n", b"\r\n"), "truncated": original[:-1],
             "foreign_hash": original.replace(b"b" * 64, b"c" * 64),
             "traversal": original.replace(b"0.1.4-", b"../0.1.4-"),
             "extra": original + b"another\n", "bom": b"\xef\xbb\xbf" + original,
             "leading_zero": original.replace(b"0.1.4-", b"00.1.4-")}
    if change == "missing":
        path.unlink()
    else:
        path.write_bytes(edits[change])
    with pytest.raises(managed.ManagedLaunchError):
        managed.read_active_payload(root)


@pytest.mark.parametrize("name", ["Luma.exe", managed.OWNER_NAME, managed.POINTER_NAME,
                                  "versions/0.1.4-" + "b" * 24 + "/Luma.exe",
                                  "versions/0.1.4-" + "b" * 24 + "/luma-install.json"])
def test_hardlinked_protocol_or_executable_rejected(installation, tmp_path, name):
    root, *_ = installation
    activate(installation)
    os.link(root / name, tmp_path / "other-link")
    with pytest.raises(managed.ManagedLaunchError, match="links"):
        managed.read_active_payload(root)


@pytest.mark.parametrize("change", ["duplicate", "extra", "escape", "digest", "root_hash", "payload_hash"])
def test_marker_and_binary_mismatch_rejected(installation, change):
    root, _, current, _, _ = installation
    activate(installation)
    marker = current.parent / "luma-install.json"
    if change == "root_hash":
        (root / "Luma.exe").write_bytes(b"MZ foreign launcher")
    elif change == "payload_hash":
        current.write_bytes(b"MZ changed payload")
    else:
        raw = marker.read_text()
        if change == "duplicate": raw = raw[:-1] + ',"version":"0.1.4"}'
        if change == "extra": raw = raw[:-1] + ',"unknown":true}'
        if change == "escape": raw = raw.replace('"version"', '"vers\\u0069on"')
        if change == "digest": raw = raw.replace("b" * 64, "c" * 64)
        marker.write_text(raw)
    with pytest.raises(managed.ManagedLaunchError):
        managed.read_active_payload(root)


def test_foreign_owner_never_creates_pointer_or_journal(installation):
    root, _, _, registry, _ = installation
    shell.install(Path(r"D:\Portable\Luma.exe"), registry)
    before = snapshot(installation)
    with pytest.raises(shell.RegistrationConflict, match="another Luma copy"):
        activate(installation)
    assert snapshot(installation) == before
    assert not (root / managed.POINTER_NAME).exists()


def test_confirmed_foreign_owner_migration_keeps_exact_receipt_boundary(installation):
    root, _, new, registry, _ = installation
    foreign = r"D:\Portable\Luma.exe"
    shell.install(Path(foreign), registry)
    owner = shell.registration_owner(registry)
    managed.activate_managed_installation(new, registry,
        allowed_previous_executables=(foreign,), expected_registration=owner)
    assert managed.read_active_payload(root).executable == new


def test_confirmed_owner_changed_before_activation_is_preserved(installation):
    _, _, new, registry, _ = installation
    foreign = r"D:\Portable\Luma.exe"
    shell.install(Path(foreign), registry)
    owner = shell.registration_owner(registry)
    shell.install(Path(r"D:\Another\Luma.exe"), registry)
    before = snapshot(installation)
    with pytest.raises(shell.RegistrationConflict, match="changed after confirmation"):
        managed.activate_managed_installation(new, registry,
            allowed_previous_executables=(foreign,), expected_registration=owner)
    assert snapshot(installation) == before


@pytest.mark.parametrize("stage", ["prepared", "registry_partial", "pointer_before", "pointer_after", "receipt_after"])
def test_process_crash_recovers_only_known_owned_states(installation, monkeypatch, stage):
    root, old, new, registry, _ = installation
    activate(installation, old=True)
    before = snapshot(installation)
    if stage == "registry_partial":
        registry.crash_on = registry.target_calls + 3
    else:
        method = {"prepared": "prepare", "pointer_before": "activate", "pointer_after": "activate", "receipt_after": "finish"}[stage]
        original = getattr(managed._Activation, method)
        def crash(self, *args):
            if stage in ("prepared", "pointer_after"):
                original(self, *args)
            raise SimulatedCrash()
        monkeypatch.setattr(managed._Activation, method, crash)
    with pytest.raises(SimulatedCrash):
        activate(installation)
    assert (root / managed.JOURNAL_NAME).is_file()
    assert managed.recover_managed_activation(root, registry)
    if stage == "receipt_after":
        assert managed.read_active_payload(root).executable == new
    else:
        assert snapshot(installation) == before
        assert managed.read_active_payload(root).executable == old
    assert not managed.recover_managed_activation(root, registry)


@pytest.mark.parametrize("failure", ["registry", "pointer", "commit_receipt"])
def test_exception_rolls_back_pointer_and_registry_together(installation, monkeypatch, failure):
    root, _, _, registry, _ = installation
    activate(installation, old=True)
    before = snapshot(installation)
    if failure == "registry":
        registry.fail_target_calls.add(registry.target_calls + 2)
    elif failure == "commit_receipt":
        registry.fail_manifest_writes.add(registry.manifest_writes + 2)
    else:
        original = managed._cas_file
        def fail(path, expected, replacement, limit=managed.MAX_JOURNAL):
            if path.name == managed.POINTER_NAME and replacement != before[0]:
                raise OSError("Injected pointer write failure")
            return original(path, expected, replacement, limit)
        monkeypatch.setattr(managed, "_cas_file", fail)
    with pytest.raises(shell.ShellIntegrationError):
        activate(installation)
    assert snapshot(installation) == before


@pytest.mark.parametrize("change", ["pointer", "receipt", "value", "owner"])
def test_recovery_preserves_foreign_change_without_any_writes(installation, monkeypatch, change):
    root, old, new, registry, _ = installation
    activate(installation, old=True)
    original = managed._Activation.activate
    def crash(self, *args):
        original(self, *args)
        raise SimulatedCrash()
    monkeypatch.setattr(managed._Activation, "activate", crash)
    with pytest.raises(SimulatedCrash): activate(installation)
    if change == "pointer":
        (root / managed.POINTER_NAME).write_bytes(b"foreign pointer\n")
    elif change == "receipt":
        registry.seed(shell.METADATA_KEY, shell.MANIFEST_NAME, shell.Value(shell.REG_SZ, "foreign receipt"))
    elif change == "value":
        registry.seed(shell.CAPABILITIES, "ApplicationName", shell.Value(shell.REG_SZ, "foreign app"))
    else:
        (root / managed.OWNER_NAME).write_bytes(b"foreign owner")
    before = snapshot(installation)
    before_log = list(registry.log)
    with pytest.raises(managed.ManagedLaunchError):
        managed.recover_managed_activation(root, registry)
    assert snapshot(installation) == before
    assert registry.log == before_log


def test_busy_abort_retains_pointer_until_explicit_post_guard_finalize(installation):
    root, _, new, registry, _ = installation
    activate(installation)
    pointer = (root / managed.POINTER_NAME).read_bytes()
    managed.prepare_managed_uninstall(new, registry)
    assert managed.read_active_payload(root).executable == new
    assert (root / managed.POINTER_NAME).read_bytes() == pointer
    assert (root / managed.UNINSTALL_NAME).is_file()
    managed.finalize_managed_uninstall(new, registry)
    assert managed.read_active_payload(root).executable == new
    assert (root / managed.UNINSTALL_NAME).is_file()
    ready = (root / managed.READY_NAME).read_bytes()
    assert ready == managed._ready_bytes(root, managed._owner(root), pointer,
                                         (root / managed.UNINSTALL_NAME).read_bytes())
    assert len(ready) <= 512
    assert new.is_file() and (root / "Luma.exe").is_file()


@pytest.mark.parametrize("change", ["pointer", "root", "registry"])
def test_finalize_refuses_changed_ownership(installation, change):
    root, old, new, registry, _ = installation
    activate(installation)
    owned_registry = copy.deepcopy(registry.values)
    managed.prepare_managed_uninstall(new, registry)
    if change == "pointer":
        (root / managed.POINTER_NAME).write_bytes(managed._payload(root, old.parent.name).pointer())
    elif change == "root":
        (root / "Luma.exe").write_bytes(b"MZ foreign launcher")
    else:
        registry.values = owned_registry
    before = snapshot(installation)
    with pytest.raises(managed.ManagedLaunchError):
        managed.finalize_managed_uninstall(new, registry)
    assert snapshot(installation) == before


def test_uninstall_preserves_foreign_portable_registration(installation):
    root, _, new, registry, _ = installation
    activate(installation)
    shell.install(Path(r"D:\Portable\Luma.exe"), registry)
    before = copy.deepcopy(registry.values)
    managed.prepare_managed_uninstall(new, registry)
    managed.finalize_managed_uninstall(new, registry)
    assert registry.values == before
    assert managed.read_active_payload(root).executable == new
    assert (root / managed.READY_NAME).is_file()


def test_finalization_crash_preserves_usable_pointer_and_can_retry(installation, monkeypatch):
    root, _, new, registry, _ = installation
    activate(installation)
    managed.prepare_managed_uninstall(new, registry)
    original = managed._cas_file
    def crash(path, expected, replacement, limit=managed.MAX_JOURNAL):
        original(path, expected, replacement, limit)
        if path.name == managed.READY_NAME:
            raise SimulatedCrash()
    with monkeypatch.context() as scoped:
        scoped.setattr(managed, "_cas_file", crash)
        with pytest.raises(SimulatedCrash): managed.finalize_managed_uninstall(new, registry)
    assert managed.read_active_payload(root).executable == new
    managed.finalize_managed_uninstall(new, registry)
    assert managed.read_active_payload(root).executable == new
    assert (root / managed.READY_NAME).is_file()


@pytest.mark.parametrize("failure", [False, True])
def test_reinstall_cancels_only_unchanged_prepared_uninstall_transactionally(installation, failure):
    root, _, new, registry, _ = installation
    activate(installation)
    managed.prepare_managed_uninstall(new, registry)
    receipt = (root / managed.UNINSTALL_NAME).read_bytes()
    before = snapshot(installation)
    if failure:
        registry.fail_target_calls.add(registry.target_calls + 2)
        with pytest.raises(shell.ShellIntegrationError):
            activate(installation)
        assert snapshot(installation) == before
        assert (root / managed.UNINSTALL_NAME).read_bytes() == receipt
    else:
        activate(installation)
        assert not (root / managed.UNINSTALL_NAME).exists()
        assert managed.read_active_payload(root).executable == new
        assert shell._read_manifest(registry)[0]["state"] == "active"


def test_unconfirmed_allowlist_cannot_authorize_foreign_owner(installation):
    root, _, new, registry, _ = installation
    foreign = r"D:\Portable\Luma.exe"
    shell.install(Path(foreign), registry)
    before = snapshot(installation)
    with pytest.raises(shell.RegistrationConflict, match="unrelated owner"):
        managed.activate_managed_installation(new, registry, allowed_previous_executables=(foreign,))
    assert snapshot(installation) == before


@pytest.mark.parametrize("stage", ["pointer_after", "commit_after"])
def test_reinstall_crash_recovers_pending_uninstall_intent(installation, monkeypatch, stage):
    root, _, new, registry, _ = installation
    activate(installation)
    managed.prepare_managed_uninstall(new, registry)
    before = snapshot(installation)
    receipt = (root / managed.UNINSTALL_NAME).read_bytes()
    method = "activate" if stage == "pointer_after" else "finish"
    original = getattr(managed._Activation, method)
    def crash(self, *args):
        if stage == "pointer_after": original(self, *args)
        raise SimulatedCrash()
    monkeypatch.setattr(managed._Activation, method, crash)
    with pytest.raises(SimulatedCrash): activate(installation)
    managed.recover_managed_activation(root, registry)
    if stage == "pointer_after":
        assert snapshot(installation) == before
        assert (root / managed.UNINSTALL_NAME).read_bytes() == receipt
    else:
        assert not (root / managed.UNINSTALL_NAME).exists()
        assert shell._read_manifest(registry)[0]["state"] == "active"


def test_direct_same_root_install_cannot_remove_managed_launch_topology(installation):
    _, _, new, registry, _ = installation
    activate(installation)
    before = snapshot(installation)
    with pytest.raises(shell.RegistrationConflict, match="stable launch entry"):
        shell.install(new, registry)
    assert snapshot(installation) == before


def test_managed_startup_with_missing_receipt_keeps_root_entry(installation, monkeypatch):
    root, _, new, registry, windows = installation
    activate(installation)
    managed.prepare_managed_uninstall(new, registry)
    monkeypatch.setattr(shell.sys, "platform", "win32")
    monkeypatch.setattr(shell.sys, "frozen", True, raising=False)
    shell.prepare_startup_registration(new, registry)
    assert shell.registration_owner(registry).executable == windows(new)
    assert registry.read(*shell._APPLICATION_COMMAND).data == f'"{windows(root / "Luma.exe")}" "%1"'
    assert not (root / managed.UNINSTALL_NAME).exists()


def test_unselected_managed_startup_never_downgrades_pointer(installation, monkeypatch):
    root, old, _, registry, _ = installation
    activate(installation)
    before = snapshot(installation)
    monkeypatch.setattr(shell.sys, "platform", "win32")
    monkeypatch.setattr(shell.sys, "frozen", True, raising=False)
    with pytest.raises(shell.RegistrationConflict, match="not the selected"):
        shell.prepare_startup_registration(old, registry)
    assert snapshot(installation) == before


def test_reinstall_after_final_busy_abort_cancels_exact_ready_receipt(installation):
    root, _, new, registry, _ = installation
    activate(installation)
    managed.prepare_managed_uninstall(new, registry)
    managed.finalize_managed_uninstall(new, registry)
    assert managed.read_active_payload(root).executable == new
    activate(installation)
    assert managed.read_active_payload(root).executable == new
    assert not (root / managed.READY_NAME).exists()
    assert not (root / managed.UNINSTALL_NAME).exists()


def test_changed_ready_receipt_never_authorizes_reinstall(installation):
    root, _, new, registry, _ = installation
    activate(installation)
    managed.prepare_managed_uninstall(new, registry)
    managed.finalize_managed_uninstall(new, registry)
    (root / managed.READY_NAME).write_bytes(b"LUMA-UNINSTALL-1\nforeign\n")
    before = snapshot(installation)
    with pytest.raises(managed.ManagedLaunchError, match="receipt changed"):
        activate(installation)
    assert snapshot(installation) == before


def test_startup_selection_is_rechecked_under_activation_lock(installation, monkeypatch):
    root, old, new, registry, _ = installation
    activate(installation, old=True)
    managed.prepare_managed_uninstall(old, registry)
    real_activate = managed.activate_managed_installation
    def concurrent_setup(executable, *args, **kwargs):
        real_activate(new, registry)
        return real_activate(executable, *args, **kwargs)
    monkeypatch.setattr(managed, "activate_managed_installation", concurrent_setup)
    monkeypatch.setattr(shell.sys, "platform", "win32")
    monkeypatch.setattr(shell.sys, "frozen", True, raising=False)
    with pytest.raises(shell.RegistrationConflict, match="changed before startup repair"):
        shell.prepare_startup_registration(old, registry)
    assert managed.read_active_payload(root).executable == new


@pytest.mark.parametrize("deleted", [(), ("ready",), ("ready", "pending"), ("ready", "pending", "pointer")])
def test_each_inno_protocol_delete_prefix_is_reinstall_recoverable(installation, deleted):
    root, _, new, registry, _ = installation
    activate(installation)
    managed.prepare_managed_uninstall(new, registry)
    managed.finalize_managed_uninstall(new, registry)
    names = {"ready": managed.READY_NAME, "pending": managed.UNINSTALL_NAME, "pointer": managed.POINTER_NAME}
    # Inno holds the root mutex and retained binary handles at this commit point.
    # Simulate a process crash after each permitted prefix of its deletion order.
    for name in deleted:
        (root / names[name]).unlink()
    if "pointer" not in deleted:
        assert managed.read_active_payload(root).executable == new
    activate(installation)
    assert managed.read_active_payload(root).executable == new
    assert not (root / managed.READY_NAME).exists()
    assert not (root / managed.UNINSTALL_NAME).exists()


@pytest.mark.parametrize("crash", [False, True])
def test_noncanonical_prior_receipt_is_restored_byte_exactly(installation, monkeypatch, crash):
    root, old, _, registry, _ = installation
    shell.install(old, registry)
    document, _ = shell._read_manifest(registry)
    original = shell.Value(shell.REG_SZ, json.dumps(document, indent=3))
    registry.seed(shell.METADATA_KEY, shell.MANIFEST_NAME, original)
    before = snapshot(installation)
    if crash:
        registry.crash_on = registry.target_calls + 3
        with pytest.raises(SimulatedCrash): activate(installation)
        assert managed.recover_managed_activation(root, registry)
    else:
        registry.fail_target_calls.add(registry.target_calls + 3)
        with pytest.raises(shell.ShellIntegrationError): activate(installation)
    assert snapshot(installation) == before
    assert registry.read(shell.METADATA_KEY, shell.MANIFEST_NAME) == original


@pytest.mark.parametrize("operation", ["install", "uninstall", "disable"])
def test_generic_registry_recovery_cannot_split_a_managed_transaction(installation, monkeypatch, operation):
    _, _, _, registry, _ = installation
    activate(installation, old=True)
    original = managed._Activation.activate
    def crash(self, *args):
        original(self, *args)
        raise SimulatedCrash()
    monkeypatch.setattr(managed._Activation, "activate", crash)
    with pytest.raises(SimulatedCrash): activate(installation)
    before = snapshot(installation)
    with pytest.raises(shell.RecoveryRequired, match="pointer and registry together"):
        if operation == "install": shell.install(Path(r"D:\Portable\Luma.exe"), registry)
        elif operation == "uninstall": shell.uninstall(registry)
        else: shell.disable_thumbnails(registry)
    assert snapshot(installation) == before


def test_unowned_identical_registration_cannot_report_managed_activation_success(installation):
    root, _, new, registry, windows = installation
    for write in shell.registration_plan(windows(new), launch_executable=windows(root / "Luma.exe")):
        registry.seed(write.key, write.name, write.value)
    before = snapshot(installation)
    with pytest.raises(shell.RegistrationConflict, match="actual owned registration"):
        activate(installation)
    assert snapshot(installation) == before
