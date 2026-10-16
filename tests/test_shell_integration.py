import contextlib
import copy
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from luma import shell_integration as si

EXE = r"C:\Portable Apps\Luma 日本語\Luma.exe"
OTHER_EXE = r"D:\Pictures Tools\Luma\Luma.exe"
THIRD_PARTY = "{11111111-2222-3333-4444-555555555555}"


class SimulatedCrash(BaseException):
    pass


class FakeRegistry:
    def __init__(self):
        self.values = {}
        self.keys = set()
        self.handlers = {}
        self.log = []
        self.notifications = 0
        self.target_calls = 0
        self.fail_target_calls = set()
        self.crash_on = None
        self.flush_calls = 0
        self.fail_flush_calls = set()
        self.manifest_writes = 0
        self.fail_manifest_writes = set()

    @staticmethod
    def address(key, name):
        # A fake must reject malformed Windows paths instead of mirroring them.
        si._key(key)
        return key.casefold(), name.casefold()

    def read(self, key, name):
        return self.values.get(self.address(key, name))

    def _before_write(self, key):
        if key == si.METADATA_KEY:
            self.manifest_writes += 1
            if self.manifest_writes in self.fail_manifest_writes:
                self.fail_manifest_writes.remove(self.manifest_writes)
                raise PermissionError("Injected manifest failure")
            return
        self.target_calls += 1
        if self.crash_on == self.target_calls:
            self.crash_on = None
            raise SimulatedCrash()
        if self.target_calls in self.fail_target_calls:
            self.fail_target_calls.remove(self.target_calls)
            raise PermissionError("Injected target failure")

    def write(self, key, name, value):
        self.address(key, name)
        self._before_write(key)
        for ancestor in si._ancestors(key):
            self.keys.add(ancestor.casefold())
        self.values[self.address(key, name)] = value
        self.log.append(("write", key, name))

    def delete_value(self, key, name):
        self._before_write(key)
        self.values.pop(self.address(key, name), None)
        self.log.append(("delete", key, name))

    def key_exists(self, key):
        si._key(key)
        return key.casefold() in self.keys

    def delete_empty_key(self, key):
        assert key in si._ALLOWED_KEYS or key == si.METADATA_KEY
        normalized = key.casefold()
        if any(address[0] == normalized for address in self.values):
            return
        if any(other.startswith(normalized + "\\") for other in self.keys):
            return
        self.keys.discard(normalized)
        self.log.append(("delete_empty", key))

    def flush(self):
        self.flush_calls += 1
        self.log.append(("flush",))
        if self.flush_calls in self.fail_flush_calls:
            self.fail_flush_calls.remove(self.flush_calls)
            raise PermissionError("Injected flush failure")

    def provider_for(self, extension):
        return self.handlers.get(extension)

    def notify_associations_changed(self):
        self.notifications += 1

    @contextlib.contextmanager
    def locked(self):
        yield

    def seed(self, key, name, value):
        for ancestor in si._ancestors(key):
            self.keys.add(ancestor.casefold())
        self.values[self.address(key, name)] = value


def install(registry, exe=EXE):
    with (
        patch.object(si, "_runtime_guard"),
        patch.object(si, "_checked_executable", return_value=exe),
    ):
        return si.install(Path(exe), registry=registry)


def uninstall(registry):
    with patch.object(si, "_runtime_guard"):
        return si.uninstall(registry=registry)


def plan_map(exe=EXE, handlers=None):
    return {(w.key, w.name): w.value for w in si.registration_plan(exe, handlers)}


def test_plan_has_no_repeated_or_empty_registry_separators():
    for write in si.registration_plan(EXE):
        assert "\\\\" not in write.key
        assert not write.key.startswith("\\")
        assert not write.key.endswith("\\")
        assert all(write.key.split("\\"))
        assert "/" not in write.key
    for key in si._ALLOWED_KEYS:
        assert "\\\\" not in key
        assert all(key.split("\\"))


@pytest.mark.parametrize("fragment", [
    "", "\\leading", "trailing\\", "repeated\\\\separator", "slash/path", "nul\0part",
])
def test_registry_key_join_rejects_malformed_fragments(fragment):
    with pytest.raises(si.ShellIntegrationError):
        si._key(si.CLASSES, fragment)


def test_pure_plan_uses_quoted_unicode_paths_and_fixed_clsids():
    plan = plan_map()
    command = si._key(si.CLASSES, "Luma.Image.png", "shell", "open", "command")
    assert plan[command, ""] == si.Value(si.REG_SZ, f'"{EXE}" "%1"')
    for clsid in si.PROVIDERS.values():
        key = si._key(si.CLASSES, "CLSID", clsid, "InprocServer32")
        assert plan[key, "ThreadingModel"] == si.Value(si.REG_SZ, "Apartment")
        assert plan[key, ""].data.endswith(r"\LumaThumbnail.dll")
    assert len(si.EXTENSIONS) == len(set(si.EXTENSIONS))


def test_plan_never_changes_defaults_or_system_handlers():
    plan = si.registration_plan(EXE)
    extensions = {si._key(si.CLASSES, ext) for ext in si.EXTENSIONS}
    for write in plan:
        lowered = write.key.casefold()
        assert "userchoice" not in lowered
        assert "systemfileassociations" not in lowered
        assert write.name != "DisableProcessIsolation"
        assert not (write.key in extensions and write.name == "")
        assert write.key.startswith("Software\\")
    assert (si.REGISTERED_APPS, "Luma") in plan_map()
    assert len(plan) <= si.MAX_ENTRIES


def test_existing_common_and_custom_raster_providers_are_preserved():
    plan = plan_map(handlers={".png": THIRD_PARTY, ".webp": THIRD_PARTY})

    def provider(extension):
        return plan[
            si._key(si.CLASSES, "Luma.Image." + extension, "shellex", si.THUMBNAIL_IID),
            "",
        ].data

    assert provider("png") == THIRD_PARTY
    assert provider("webp") == THIRD_PARTY
    assert provider("jpeg") == si.PHOTO_PROVIDER
    assert provider("dds") == si.PROVIDERS["raster"]
    assert provider("nef") == si.PROVIDERS["raw"]
    assert provider("pic") == si.PROVIDERS["hdr"]
    assert provider("exr") == si.PROVIDERS["exr"]


@pytest.mark.parametrize("path", [
    r"\\server\share\Luma.exe", r"\\?\C:\Luma\Luma.exe",
    r"C:Luma.exe", r"relative\Luma.exe", r'C:\Bad"Path\Luma.exe',
    r"C:\Luma\..\Other\Luma.exe", r"C:\Luma\Another.exe", r"C:\NUL\Luma.exe",
])
def test_invalid_bundle_paths_are_rejected_by_pure_plan(path):
    with pytest.raises(si.ShellIntegrationError):
        si.registration_plan(path)


def test_install_persists_and_flushes_journal_before_target_writes():
    registry = FakeRegistry()
    result = install(registry)
    assert result.changed_values > 0
    first_target = next(
        i for i, event in enumerate(registry.log)
        if event[0] == "write" and event[1] != si.METADATA_KEY
    )
    assert registry.log[0][:2] == ("write", si.METADATA_KEY)
    assert ("flush",) in registry.log[:first_target]
    assert si.status(registry).state == "installed"
    assert registry.notifications == 1


def test_idempotent_install_does_not_rewrite_owned_values():
    registry = FakeRegistry()
    install(registry)
    before = registry.target_calls
    assert install(registry).changed_values == 0
    assert registry.target_calls == before


def test_update_changes_bundle_paths_then_uninstall_removes_only_owned_values():
    registry = FakeRegistry()
    install(registry)
    document, _ = si._read_manifest(registry)
    assert not si._stale_paths(document["data"]["entries"], EXE)
    assert si._stale_paths(document["data"]["entries"], OTHER_EXE)
    assert install(registry, OTHER_EXE).changed_values > 0
    command = si._key(si.CLASSES, "Luma.Image.png", "shell", "open", "command")
    assert registry.read(command, "").data == f'"{OTHER_EXE}" "%1"'
    uninstall(registry)
    assert registry.values == {}
    assert si.status(registry).state == "not_installed"


def test_identical_preexisting_values_are_not_adopted():
    registry = FakeRegistry()
    key = si._key(si.CLASSES, "Luma.Image.png")
    original = si.Value(si.REG_SZ, "Luma image")
    registry.seed(key, "", original)
    install(registry)
    uninstall(registry)
    assert registry.read(key, "") == original
    assert len(registry.values) == 1


def test_different_preexisting_values_block_all_install_writes():
    registry = FakeRegistry()
    key = si._key(si.CLASSES, "Luma.Image.png")
    registry.seed(key, "", si.Value(si.REG_SZ, "Another owner"))
    snapshot = copy.deepcopy(registry.values)
    with pytest.raises(si.RegistrationConflict):
        install(registry)
    assert registry.values == snapshot
    assert registry.log == []


def test_update_does_not_clobber_third_party_changes():
    registry = FakeRegistry()
    install(registry)
    key = si._key(si.CLASSES, "Luma.Image.png", "shell", "open", "command")
    other = si.Value(si.REG_SZ, '"C:\\Other\\Reader.exe" "%1"')
    registry.seed(key, "", other)
    snapshot = copy.deepcopy(registry.values)
    with pytest.raises(si.RegistrationConflict):
        install(registry, OTHER_EXE)
    assert registry.values == snapshot
    assert si.status(registry).state == "modified"
    assert uninstall(registry).preserved_values == 1
    assert registry.read(key, "") == other
    assert len(registry.values) == 1


def test_partial_write_failure_is_rolled_back():
    registry = FakeRegistry()
    registry.fail_target_calls = {3}
    with pytest.raises(si.ShellIntegrationError):
        install(registry)
    assert registry.values == {}
    assert si.status(registry).state == "not_installed"


def test_failed_rollback_keeps_pending_journal_for_next_explicit_action():
    registry = FakeRegistry()
    registry.fail_target_calls = {3, 4}
    with pytest.raises(si.RecoveryRequired):
        install(registry)
    assert si.status(registry).state == "recovery_required"
    assert uninstall(registry).recovered
    assert registry.values == {}


def test_initial_journal_flush_failure_performs_no_target_writes():
    registry = FakeRegistry()
    registry.fail_flush_calls = {1}
    with pytest.raises(si.RecoveryRequired):
        install(registry)
    assert registry.target_calls == 0
    assert si.status(registry).state == "recovery_required"
    assert uninstall(registry).recovered
    assert registry.values == {}


def test_final_manifest_failure_rolls_back_targets():
    registry = FakeRegistry()
    registry.fail_manifest_writes = {2}
    with pytest.raises(si.ShellIntegrationError):
        install(registry)
    assert registry.values == {}


def test_crash_leaves_journal_and_readonly_status_does_not_recover():
    registry = FakeRegistry()
    registry.crash_on = 3
    with pytest.raises(SimulatedCrash):
        install(registry)
    snapshot = copy.deepcopy(registry.values)
    assert si.status(registry).state == "recovery_required"
    assert registry.values == snapshot
    assert uninstall(registry).recovered
    assert registry.values == {}


def test_crash_recovery_preserves_a_later_third_party_write():
    registry = FakeRegistry()
    registry.crash_on = 3
    with pytest.raises(SimulatedCrash):
        install(registry)
    first = si.registration_plan(EXE)[0]
    other = si.Value(si.REG_SZ, "Changed after interruption")
    registry.seed(first.key, first.name, other)
    uninstall(registry)
    assert registry.read(first.key, first.name) == other
    assert len(registry.values) == 1


def test_uninstall_failure_restores_previous_registration():
    registry = FakeRegistry()
    install(registry)
    snapshot = copy.deepcopy(registry.values)
    registry.fail_target_calls = {registry.target_calls + 3}
    with pytest.raises(si.ShellIntegrationError):
        uninstall(registry)
    assert registry.values == snapshot
    assert si.status(registry).state == "installed"


def test_cleanup_never_recursively_deletes_an_unknown_child():
    registry = FakeRegistry()
    install(registry)
    key = si._key(si.CLASSES, "Luma.Image.png", "ThirdPartyChild")
    other = si.Value(si.REG_SZ, "keep")
    registry.seed(key, "External", other)
    uninstall(registry)
    assert registry.read(key, "External") == other
    assert registry.key_exists(si._key(si.CLASSES, "Luma.Image.png"))


@pytest.mark.parametrize("raw", [
    si.Value(si.REG_SZ, '{"version":999,"state":"active"}'),
    si.Value(si.REG_SZ, '{"version":1,"version":1,"state":"active"}'),
    si.Value(3, b"not a string"),
    si.Value(si.REG_SZ, "{" * 1000),
    si.Value(si.REG_SZ, " " * (si.MAX_MANIFEST_BYTES + 1)),
])
def test_unknown_or_invalid_manifest_is_never_overwritten(raw):
    registry = FakeRegistry()
    registry.seed(si.METADATA_KEY, si.MANIFEST_NAME, raw)
    snapshot = copy.deepcopy(registry.values)
    assert si.status(registry).state == "blocked"
    with pytest.raises(si.InvalidManifest):
        install(registry)
    with pytest.raises(si.InvalidManifest):
        uninstall(registry)
    assert registry.values == snapshot


def test_tampered_manifest_cannot_target_extension_defaults():
    registry = FakeRegistry()
    install(registry)
    document, _ = si._read_manifest(registry)
    document["data"]["entries"][0]["key"] = si._key(si.CLASSES, ".png")
    registry.seed(si.METADATA_KEY, si.MANIFEST_NAME,
                  si.Value(si.REG_SZ, json.dumps(document)))
    snapshot = copy.deepcopy(registry.values)
    with pytest.raises(si.InvalidManifest):
        uninstall(registry)
    assert registry.values == snapshot
    assert registry.read(si._key(si.CLASSES, ".png"), "") is None


def test_tampered_manifest_cannot_restore_an_arbitrary_command():
    registry = FakeRegistry()
    install(registry)
    document, _ = si._read_manifest(registry)
    entry = next(e for e in document["data"]["entries"]
                 if e["key"].endswith(r"\shell\open\command"))
    entry["written"]["data"] = '"C:\\Windows\\System32\\cmd.exe" /c something'
    registry.seed(si.METADATA_KEY, si.MANIFEST_NAME,
                  si.Value(si.REG_SZ, json.dumps(document)))
    snapshot = copy.deepcopy(registry.values)
    with pytest.raises(si.InvalidManifest):
        uninstall(registry)
    assert registry.values == snapshot


def test_public_api_wraps_permission_errors():
    registry = FakeRegistry()
    with (
        patch.object(si, "_runtime_guard"),
        patch.object(si, "_checked_executable", side_effect=PermissionError("blocked")),
    ):
        with pytest.raises(si.ShellIntegrationError, match="No elevation"):
            si.install(Path(EXE), registry)
    assert registry.values == {}


def test_source_run_is_rejected_even_with_injected_registry():
    registry = FakeRegistry()
    with (
        patch.object(si.sys, "platform", "win32"),
        patch.object(si.sys, "frozen", False, create=True),
    ):
        with pytest.raises(si.ShellIntegrationError, match="packaged"):
            si.install(Path(EXE), registry)
    assert registry.values == {}


def test_non_x64_runtime_is_rejected():
    with (
        patch.object(si.sys, "platform", "win32"),
        patch.object(si.sys, "frozen", True, create=True),
        patch.object(si.platform, "machine", return_value="ARM64"),
    ):
        with pytest.raises(si.ShellIntegrationError, match="x64"):
            si._runtime_guard()


def test_non_windows_runtime_is_rejected():
    with patch.object(si.sys, "platform", "linux"):
        with pytest.raises(si.ShellIntegrationError, match="Windows"):
            si._runtime_guard()


_REAL_REGISTRY_CI = (
    sys.platform == "win32"
    and os.environ.get("GITHUB_ACTIONS") == "true"
    and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted"
    and os.environ.get("LUMA_SHELL_TEST_REAL_REGISTRY") == "1"
)


@pytest.mark.skipif(
    not _REAL_REGISTRY_CI,
    reason="Explicitly enabled disposable GitHub-hosted Windows runner only",
)
def test_real_hkcu_registration_lifecycle_on_disposable_runner():
    # No normal developer/user-PC test invocation enables this mutation test.
    # Refuse to start if any Luma registration already exists on the runner.
    registry = si.WinRegistry()
    if registry.read(si.METADATA_KEY, si.MANIFEST_NAME) is not None:
        pytest.skip("Runner already has a shell-integration owner")
    for write in si.registration_plan(EXE):
        if registry.read(write.key, write.name) is not None:
            pytest.skip("Runner already has a Luma registration value")
    defaults = {
        ext: registry.read(si._key(si.CLASSES, ext), "")
        for ext in si.EXTENSIONS
    }
    try:
        assert install(registry).changed_values > 0
        assert si.status(registry).state == "installed"
        assert install(registry).changed_values == 0
        assert install(registry, OTHER_EXE).changed_values > 0
        assert uninstall(registry).changed_values > 0
        assert registry.read(si.METADATA_KEY, si.MANIFEST_NAME) is None
        for write in si.registration_plan(EXE):
            assert registry.read(write.key, write.name) is None
        assert defaults == {
            ext: registry.read(si._key(si.CLASSES, ext), "")
            for ext in si.EXTENSIONS
        }
    finally:
        # Uses the normal validated journal and exact compare-before-restore.
        # Never recursively deletes test keys or restores unchecked snapshots.
        uninstall(registry)


@pytest.mark.skipif(
    not _REAL_REGISTRY_CI,
    reason="Explicitly enabled disposable GitHub-hosted Windows runner only",
)
def test_real_named_mutex_serializes_processes_without_waiting():
    registry = si.WinRegistry()
    code = (
        "import sys\n"
        "from luma.shell_integration import WinRegistry, ShellIntegrationError\n"
        "try:\n"
        "    with WinRegistry().locked():\n"
        "        sys.exit(3)\n"
        "except ShellIntegrationError:\n"
        "    sys.exit(0)\n"
    )
    with registry.locked():
        result = subprocess.run(
            [sys.executable, "-c", code], timeout=15, capture_output=True, text=True
        )
    assert result.returncode == 0, result.stdout + result.stderr


def uninstall_if_executable(registry, executable=EXE):
    with patch.object(si, "_runtime_guard"):
        return si.uninstall_if_executable(Path(executable), registry)


def test_exact_owner_uninstall_leaves_another_portable_copy_untouched():
    registry = FakeRegistry()
    install(registry, OTHER_EXE)
    snapshot = copy.deepcopy(registry.values)
    log = list(registry.log)
    result = uninstall_if_executable(registry, EXE)
    assert result == si.Result(0)
    assert registry.values == snapshot
    assert registry.log == log
    assert uninstall_if_executable(registry, OTHER_EXE).changed_values > 0
    assert registry.values == {}


def test_exact_owner_uninstall_preserves_modified_values():
    registry = FakeRegistry()
    install(registry)
    key = si._key(si.CLASSES, "Luma.Image.png", "shell", "open", "command")
    external = si.Value(si.REG_SZ, '"C:\\Other\\App.exe" "%1"')
    registry.seed(key, "", external)
    assert uninstall_if_executable(registry).preserved_values == 1
    assert registry.read(key, "") == external


def test_exact_owner_mismatch_never_recovers_pending_install():
    registry = FakeRegistry()
    registry.crash_on = 3
    with pytest.raises(SimulatedCrash):
        install(registry)
    snapshot = copy.deepcopy(registry.values)
    log = list(registry.log)
    assert uninstall_if_executable(registry, OTHER_EXE) == si.Result(0)
    assert registry.values == snapshot
    assert registry.log == log
    assert uninstall_if_executable(registry, EXE).recovered
    assert registry.values == {}


def test_exact_owner_refuses_ambiguous_pending_move_between_bundles():
    registry = FakeRegistry()
    install(registry, EXE)
    registry.crash_on = registry.target_calls + 2
    with pytest.raises(SimulatedCrash):
        install(registry, OTHER_EXE)
    snapshot = copy.deepcopy(registry.values)
    for executable in (EXE, OTHER_EXE):
        with pytest.raises(si.RegistrationConflict, match="different bundles"):
            uninstall_if_executable(registry, executable)
        assert registry.values == snapshot
    # Explicit Settings uninstall can still recover and remove the old owner.
    assert uninstall(registry).recovered
    assert registry.values == {}


def disable_thumbnails(registry):
    with patch.object(si, "_runtime_guard"):
        return si.disable_thumbnails(registry)


def test_disable_retains_open_commands_capabilities_and_common_image_handlers():
    registry = FakeRegistry()
    registry.handlers[".webp"] = THIRD_PARTY
    install(registry)
    document, _ = si._read_manifest(registry)
    retained = {
        (entry["key"], entry["name"]): registry.read(entry["key"], entry["name"])
        for entry in document["data"]["entries"]
        if not si._is_luma_thumbnail_entry(entry)
    }
    result = disable_thumbnails(registry)
    assert result.changed_values > 0
    state = si.status(registry)
    assert state.state == "association_only"
    assert state.thumbnails_enabled is False
    for address, value in retained.items():
        assert registry.read(*address) == value
    for extension in si.EXTENSIONS:
        command = si._key(si.CLASSES, "Luma.Image." + extension[1:], "shell", "open", "command")
        assert registry.read(command, "").data == f'"{EXE}" "%1"'
        assert registry.read(si._key(si.CLASSES, extension), "") is None
    png_handler = si._key(si.CLASSES, "Luma.Image.png", "shellex", si.THUMBNAIL_IID)
    webp_handler = si._key(si.CLASSES, "Luma.Image.webp", "shellex", si.THUMBNAIL_IID)
    assert registry.read(png_handler, "").data == si.PHOTO_PROVIDER
    assert registry.read(webp_handler, "").data == THIRD_PARTY
    for clsid in si.PROVIDERS.values():
        assert registry.read(si._key(si.CLASSES, "CLSID", clsid, "InprocServer32"), "") is None


def test_enable_disable_reenable_and_full_uninstall():
    registry = FakeRegistry()
    install(registry)
    assert si.status(registry).thumbnails_enabled is True
    disable_thumbnails(registry)
    assert disable_thumbnails(registry).changed_values == 0
    assert si.status(registry).state == "association_only"
    assert install(registry).changed_values > 0
    assert si.status(registry).thumbnails_enabled is True
    assert si.status(registry).state == "installed"
    disable_thumbnails(registry)
    assert uninstall_if_executable(registry).changed_values > 0
    assert registry.values == {}


def test_disable_preserves_external_handler_change_and_reports_disabled_intent():
    registry = FakeRegistry()
    install(registry)
    handler = si._key(si.CLASSES, "Luma.Image.nef", "shellex", si.THUMBNAIL_IID)
    external = si.Value(si.REG_SZ, THIRD_PARTY)
    registry.seed(handler, "", external)
    assert disable_thumbnails(registry).preserved_values == 1
    state = si.status(registry)
    assert state.state == "modified"
    assert state.thumbnails_enabled is False
    assert state.modified_values == 1
    assert registry.read(handler, "") == external
    with pytest.raises(si.RegistrationConflict):
        install(registry)
    uninstall(registry)
    assert registry.read(handler, "") == external


def test_disabled_status_stays_unchecked_when_open_command_changes():
    registry = FakeRegistry()
    install(registry)
    disable_thumbnails(registry)
    command = si._key(si.CLASSES, "Luma.Image.png", "shell", "open", "command")
    registry.seed(command, "", si.Value(si.REG_SZ, '"C:\\Other\\Reader.exe" "%1"'))
    state = si.status(registry)
    assert state.state == "modified"
    assert state.thumbnails_enabled is False


def test_disable_failure_rolls_back_and_keeps_enabled_state():
    registry = FakeRegistry()
    install(registry)
    snapshot = copy.deepcopy(registry.values)
    registry.fail_target_calls = {registry.target_calls + 3}
    with pytest.raises(si.ShellIntegrationError):
        disable_thumbnails(registry)
    assert registry.values == snapshot
    assert si.status(registry).thumbnails_enabled is True


def test_interrupted_disable_recovers_then_disables_on_explicit_retry():
    registry = FakeRegistry()
    install(registry)
    registry.crash_on = registry.target_calls + 3
    with pytest.raises(SimulatedCrash):
        disable_thumbnails(registry)
    state = si.status(registry)
    assert state.state == "recovery_required"
    assert state.thumbnails_enabled is True
    assert disable_thumbnails(registry).recovered
    assert si.status(registry).state == "association_only"
    assert si.status(registry).thumbnails_enabled is False


def test_executable_association_has_owned_content_aware_handler():
    key = si._key(si.CLASSES, "Applications", "Luma.exe", "shellex", si.THUMBNAIL_IID)
    assert plan_map()[key, ""] == si.Value(si.REG_SZ, si.PROVIDERS["raster"])
    registry = FakeRegistry()
    foreign = si.Value(si.REG_SZ, THIRD_PARTY)
    registry.seed(key, "", foreign)
    before = copy.deepcopy(registry.values)
    with pytest.raises(si.RegistrationConflict):
        install(registry)
    assert registry.values == before


def restricted_install(registry, exe=EXE, allowed=()):
    with patch.object(si, "_runtime_guard"), patch.object(si, "_checked_executable", return_value=exe):
        return si.install(Path(exe), registry=registry, allowed_previous_executables=allowed)


def test_automatic_registration_accepts_same_copy_and_explicit_upgrade_owners():
    registry = FakeRegistry()
    assert restricted_install(registry).changed_values > 0
    assert restricted_install(registry).changed_values == 0
    assert restricted_install(registry, OTHER_EXE, (EXE,)).changed_values > 0
    assert si.status(registry).state == "installed"


def test_automatic_registration_refuses_other_portable_owner_without_mutation():
    registry = FakeRegistry()
    install(registry)
    before = copy.deepcopy(registry.values)
    log = list(registry.log)
    with pytest.raises(si.RegistrationConflict, match="another Luma copy"):
        restricted_install(registry, OTHER_EXE)
    assert registry.values == before
    assert registry.log == log


def test_automatic_registration_does_not_recover_another_owners_pending_move():
    registry = FakeRegistry()
    install(registry)
    registry.crash_on = registry.target_calls + 2
    with pytest.raises(SimulatedCrash):
        install(registry, OTHER_EXE)
    before = copy.deepcopy(registry.values)
    log = list(registry.log)
    with pytest.raises(si.RegistrationConflict, match="another Luma copy"):
        restricted_install(registry, OTHER_EXE)
    assert registry.values == before
    assert registry.log == log
    # The validated same-installation allowlist can recover both snapshots.
    assert restricted_install(registry, OTHER_EXE, (EXE,)).recovered


def startup(registry, monkeypatch, exe=EXE):
    monkeypatch.setattr(si.sys, "platform", "win32")
    monkeypatch.setattr(si.sys, "frozen", True, raising=False)
    with patch.object(si, "_runtime_guard"), patch.object(si, "_checked_executable", return_value=exe):
        return si.prepare_startup_registration(Path(exe), registry)


def test_packaged_startup_registers_once_and_healthy_reopens_are_read_only(monkeypatch):
    registry = FakeRegistry()
    assert startup(registry, monkeypatch).changed_values > 0
    before = list(registry.log)
    assert startup(registry, monkeypatch).changed_values == 0
    assert registry.log == before


def test_startup_does_not_adopt_foreign_changes_or_another_copy(monkeypatch):
    registry = FakeRegistry()
    install(registry)
    with pytest.raises(si.RegistrationConflict):
        startup(registry, monkeypatch, OTHER_EXE)
    key = si._key(si.CLASSES, "Luma.Image.hdr", "shellex", si.THUMBNAIL_IID)
    registry.seed(key, "", si.Value(si.REG_SZ, THIRD_PARTY))
    before = copy.deepcopy(registry.values)
    with pytest.raises(si.RegistrationConflict, match="preserved"):
        startup(registry, monkeypatch)
    assert registry.values == before


def test_source_startup_never_mutates_registry(monkeypatch):
    registry = FakeRegistry()
    monkeypatch.setattr(si.sys, "frozen", False, raising=False)
    assert si.prepare_startup_registration(Path(EXE), registry).changed_values == 0
    assert not registry.log


def test_effective_association_query_preserves_provider_before_manual_fallback(monkeypatch):
    registry = object.__new__(si.WinRegistry)
    monkeypatch.setattr(si, "_association_string", lambda extension, kind, extra=None: THIRD_PARTY)
    registry._read = lambda *args: pytest.fail("Effective Shell provider must take precedence")
    assert registry.provider_for(".png") == THIRD_PARTY
    assert registry.effective_provider_for(".hdr") == THIRD_PARTY


@pytest.mark.parametrize("value", [None, "not-a-guid", "{broken}"])
def test_effective_association_rejects_malformed_provider(monkeypatch, value):
    registry = object.__new__(si.WinRegistry)
    monkeypatch.setattr(si, "_association_string", lambda *args: value)
    assert registry.effective_provider_for(".hdr") is None


def test_hdr_status_discloses_executable_association_and_missing_provider(monkeypatch):
    registry = object.__new__(si.WinRegistry)
    monkeypatch.setattr(si, "_association_string",
                        lambda extension, kind, extra=None: r"Applications\Luma.exe" if kind == 20 else None)
    detail = si._hdr_association_detail(registry)
    assert "did not report" in detail and "Open With" in detail and "Default Apps" in detail
    monkeypatch.setattr(si, "_association_string", lambda *args: si.PROVIDERS["raster"])
    assert "selects Luma" in si._hdr_association_detail(registry)


@pytest.mark.parametrize("disabled", [False, True])
def test_automatic_upgrade_accepts_old_plan_without_executable_thumbnail_handler(disabled):
    registry = FakeRegistry()
    install(registry)
    key = si._key(si.CLASSES, "Applications", "Luma.exe", "shellex", si.THUMBNAIL_IID)
    if disabled:
        disable_thumbnails(registry)
    document, _ = si._read_manifest(registry)
    document["data"]["entries"] = [e for e in document["data"]["entries"] if e["key"] != key]
    registry.values.pop(registry.address(key, ""), None)
    registry.seed(si.METADATA_KEY, si.MANIFEST_NAME, si._manifest_value(document))
    assert restricted_install(registry, OTHER_EXE, (EXE,)).changed_values > 0
    assert registry.read(key, "") == si.Value(si.REG_SZ, si.PROVIDERS["raster"])
    assert si.status(registry).thumbnails_enabled
    uninstall(registry)
    assert registry.values == {}


def test_association_query_is_bounded_and_disables_registry_fixups(monkeypatch):
    calls = []
    class Query:
        def __call__(self, flags, kind, extension, extra, buffer, length):
            calls.append((flags, kind, extension, extra, len(buffer)))
            buffer.value = THIRD_PARTY
            length._obj.value = len(THIRD_PARTY) + 1
            return 0
    class Library:
        AssocQueryStringW = Query()
    monkeypatch.setattr(si.ctypes, "WinDLL", lambda name: Library(), raising=False)
    assert si._association_string(".hdr", 16, si.THUMBNAIL_IID) == THIRD_PARTY
    assert calls == [(0x120, 16, ".hdr", si.THUMBNAIL_IID, si.MAX_VALUE_CHARS)]
    with pytest.raises(si.ShellIntegrationError):
        si._association_string(".unexpected", 16)


@pytest.mark.parametrize("hr,length", [(1, 39), (-1, 39), (0, 0), (0, 4097)])
def test_association_query_never_accepts_failed_or_unbounded_results(monkeypatch, hr, length):
    class Query:
        def __call__(self, flags, kind, extension, extra, buffer, output_length):
            buffer.value = THIRD_PARTY
            output_length._obj.value = length
            return hr
    class Library:
        AssocQueryStringW = Query()
    monkeypatch.setattr(si.ctypes, "WinDLL", lambda name: Library(), raising=False)
    assert si._association_string(".hdr", 16, si.THUMBNAIL_IID) is None


def test_repair_after_executable_selection_preserves_per_format_common_provider():
    registry = FakeRegistry()
    registry.handlers[".png"] = THIRD_PARTY
    install(registry)
    registry.handlers[".png"] = si.PROVIDERS["raster"]
    assert install(registry).changed_values == 0
    key = si._key(si.CLASSES, "Luma.Image.png", "shellex", si.THUMBNAIL_IID)
    assert registry.read(key, "") == si.Value(si.REG_SZ, THIRD_PARTY)


def test_executable_handler_uninstall_keeps_later_foreign_change():
    registry = FakeRegistry()
    install(registry)
    key = si._key(si.CLASSES, "Applications", "Luma.exe", "shellex", si.THUMBNAIL_IID)
    foreign = si.Value(si.REG_SZ, THIRD_PARTY)
    registry.seed(key, "", foreign)
    assert uninstall(registry).preserved_values == 1
    assert registry.read(key, "") == foreign


MANAGED_OLD = r"C:\Users\Test\Luma\versions\0.1.1-aaaaaaaaaaaaaaaaaaaaaaaa\Luma.exe"
MANAGED_NEW = r"C:\Users\Test\Luma\versions\0.1.2-bbbbbbbbbbbbbbbbbbbbbbbb\Luma.exe"
MANAGED_OTHER = r"D:\Other Luma\versions\0.1.1-aaaaaaaaaaaaaaaaaaaaaaaa\Luma.exe"
APP_COMMAND = si._key(si.CLASSES, "Applications", "Luma.exe", "shell", "open", "command")


def seed_browsed_command(registry, executable=MANAGED_OLD, command=None):
    value = si.Value(si.REG_SZ, command if command is not None else f'"{executable}" "%1"')
    registry.seed(APP_COMMAND, "", value)
    return value


def test_adopt_exact_unowned_prior_installer_command_then_uninstall_removes_it():
    registry = FakeRegistry()
    seed_browsed_command(registry)
    restricted_install(registry, MANAGED_NEW, (MANAGED_OLD,))
    assert registry.read(APP_COMMAND, "") == si.Value(si.REG_SZ, f'"{MANAGED_NEW}" "%1"')
    document, _ = si._read_manifest(registry)
    assert document["version"] == 1
    entry = next(e for e in document["data"]["entries"] if e["key"] == APP_COMMAND)
    assert entry["previous"] is None
    uninstall(registry)
    assert not registry.values


def test_adopt_prior_unowned_command_when_old_manifest_owns_other_entries():
    registry = FakeRegistry()
    seed_browsed_command(registry)
    restricted_install(registry, MANAGED_OLD)
    document, _ = si._read_manifest(registry)
    assert all(e["key"] != APP_COMMAND for e in document["data"]["entries"])
    restricted_install(registry, MANAGED_NEW, (MANAGED_OLD,))
    uninstall(registry)
    assert not registry.values


@pytest.mark.parametrize("executable,allowed,command", [
    (MANAGED_OTHER, (MANAGED_OTHER,), None),  # An allowlist alone cannot cross roots.
    (MANAGED_OLD, (), None),  # The prior marker-bearing payload must be explicitly allowed.
    (EXE, (EXE,), None),  # Portable paths cannot be adopted.
    (MANAGED_OLD, (MANAGED_OLD,), f'"{MANAGED_OLD}" --anything "%1"'),
    (MANAGED_OLD, (MANAGED_OLD,), f'"{MANAGED_OLD}" %1'),
    (MANAGED_OLD, (MANAGED_OLD,), f'"{MANAGED_OLD}" "%1" '),
])
def test_browsed_command_migration_refuses_foreign_paths_or_arguments(executable, allowed, command):
    registry = FakeRegistry()
    seed_browsed_command(registry, executable, command)
    before = copy.deepcopy(registry.values)
    with pytest.raises(si.RegistrationConflict):
        restricted_install(registry, MANAGED_NEW, allowed)
    assert registry.values == before and not registry.log


def test_explicit_repair_without_validated_allowlist_does_not_adopt_old_command():
    registry = FakeRegistry()
    seed_browsed_command(registry)
    with pytest.raises(si.RegistrationConflict):
        install(registry, MANAGED_NEW)


def test_adoption_preserves_later_foreign_change_on_uninstall():
    registry = FakeRegistry()
    seed_browsed_command(registry)
    restricted_install(registry, MANAGED_NEW, (MANAGED_OLD,))
    later = seed_browsed_command(registry, OTHER_EXE)
    assert uninstall(registry).preserved_values == 1
    assert registry.read(APP_COMMAND, "") == later


def adoption_write_index():
    return next(index for index, write in enumerate(si.registration_plan(MANAGED_NEW), 1)
                if (write.key, write.name) == (APP_COMMAND, ""))


def test_adoption_failure_restores_exact_old_unowned_command():
    registry = FakeRegistry()
    seed_browsed_command(registry)
    before = copy.deepcopy(registry.values)
    registry.fail_target_calls = {adoption_write_index() + 1}
    with pytest.raises(si.ShellIntegrationError, match="rolled back"):
        restricted_install(registry, MANAGED_NEW, (MANAGED_OLD,))
    assert registry.values == before


def interrupted_adoption():
    registry = FakeRegistry()
    original = seed_browsed_command(registry)
    registry.crash_on = adoption_write_index() + 1
    with pytest.raises(SimulatedCrash):
        restricted_install(registry, MANAGED_NEW, (MANAGED_OLD,))
    document, _ = si._read_manifest(registry)
    assert document["version"] == 2 and document["state"] == "pending"
    return registry, original, document


def test_interrupted_adoption_recovery_restores_old_bytes_before_retry():
    registry, original, _ = interrupted_adoption()
    with registry.locked():
        base, raw, recovered = si._recover(registry)
    assert recovered and base is None and raw is None
    assert registry.values == {registry.address(APP_COMMAND, ""): original}
    restricted_install(registry, MANAGED_NEW, (MANAGED_OLD,))
    uninstall(registry)
    assert not registry.values


def test_pending_adoption_is_not_recovered_without_both_validated_payloads():
    registry, _, _ = interrupted_adoption()
    before = copy.deepcopy(registry.values)
    with pytest.raises(si.RegistrationConflict, match="prior payload"):
        restricted_install(registry, MANAGED_NEW)
    assert registry.values == before
    assert restricted_install(registry, MANAGED_NEW, (MANAGED_OLD,)).recovered


def test_uninstall_of_interrupted_adoption_requires_repair_without_mutation():
    registry, _, _ = interrupted_adoption()
    before = copy.deepcopy(registry.values)
    with pytest.raises(si.RecoveryRequired, match="repair before uninstalling"):
        uninstall(registry)
    assert registry.values == before
    with patch.object(si, "_runtime_guard"):
        with pytest.raises(si.RecoveryRequired):
            si.uninstall_if_executable(Path(MANAGED_NEW), registry)
    assert registry.values == before


@pytest.mark.parametrize("tamper", ["arguments", "foreign_root", "portable", "address", "missing_flag", "false_flag", "active", "missing_change"])
def test_forged_adoption_journals_are_rejected_without_mutation(tamper):
    registry, _, document = interrupted_adoption()
    change = next(c for c in document["changes"] if c["key"] == APP_COMMAND)
    if tamper == "arguments":
        change["before"]["data"] += " --extra"
    elif tamper == "foreign_root":
        change["before"]["data"] = f'"{MANAGED_OTHER}" "%1"'
    elif tamper == "portable":
        change["before"]["data"] = f'"{EXE}" "%1"'
    elif tamper == "address":
        change["key"] = si._key(si.CLASSES, "Luma.Image.hdr", "shell", "open", "command")
    elif tamper == "missing_flag":
        del document["adopted_application_command"]
    elif tamper == "false_flag":
        document["adopted_application_command"] = False
    elif tamper == "active":
        document = {"version": 2, "state": "active", "data": document["target"]}
    elif tamper == "missing_change":
        document["changes"].remove(change)
    registry.seed(si.METADATA_KEY, si.MANIFEST_NAME, si.Value(si.REG_SZ, json.dumps(document)))
    before = copy.deepcopy(registry.values)
    with pytest.raises(si.InvalidManifest):
        restricted_install(registry, MANAGED_NEW, (MANAGED_OLD,))
    assert registry.values == before


def test_adoption_rollback_preserves_later_third_party_command_change():
    registry, _, _ = interrupted_adoption()
    later = seed_browsed_command(registry, OTHER_EXE)
    with registry.locked():
        base, raw, recovered = si._recover(registry)
    assert recovered and base is None and raw is None
    assert registry.values == {registry.address(APP_COMMAND, ""): later}
    with pytest.raises(si.RegistrationConflict):
        restricted_install(registry, MANAGED_NEW, (MANAGED_OLD,))
    assert registry.read(APP_COMMAND, "") == later
