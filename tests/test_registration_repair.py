"""Confirmed migration uses synthetic registries; no Windows state is changed."""
from __future__ import annotations

import contextlib
import copy
import json
from pathlib import Path
from string import Formatter
import subprocess
import sys
from unittest.mock import Mock

import pytest

from luma import registration_repair as repair, shell_integration as shell
from test_shell_integration import EXE, OTHER_EXE, FakeRegistry, install


CURRENT = r"D:\Program Files\Luma\versions\0.1.4-aaaaaaaaaaaaaaaaaaaaaaaa\Luma.exe"
SIBLING = r"D:\Program Files\Luma\versions\0.1.3-bbbbbbbbbbbbbbbbbbbbbbbb\Luma.exe"


@pytest.fixture
def managed(monkeypatch):
    monkeypatch.setattr(shell, "_runtime_guard", lambda: None)
    monkeypatch.setattr(shell, "_checked_executable", lambda path: str(path))
    validate = Mock(return_value=(CURRENT, (CURRENT, SIBLING)))
    monkeypatch.setattr(repair, "_validated_installation", validate)
    return validate


@pytest.fixture
def registry():
    registry = FakeRegistry()
    install(registry)
    return registry


def snapshot(registry):
    return copy.deepcopy((registry.values, registry.keys, registry.log))


def set_document(registry, document):
    registry.seed(shell.METADATA_KEY, shell.MANIFEST_NAME, shell._manifest_value(document))


def test_repair_migrates_only_confirmed_owner_and_keeps_defaults(registry, managed, monkeypatch):
    user_choice = r"Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\.exr\UserChoice"
    registry.seed(user_choice, "ProgId", shell.Value(shell.REG_SZ, "Other.Image"))
    registry.seed(user_choice, "Hash", shell.Value(shell.REG_SZ, "untouched hash"))
    registry.seed(shell.CLASSES + r"\.exr", "", shell.Value(shell.REG_SZ, "Other.Image"))
    defaults = {key: value for key, value in registry.values.items()
                if "userchoice" in key[0] or key == registry.address(shell.CLASSES + r"\.exr", "")}
    previous = shell.registration_owner(registry)
    observed = []
    original_install = shell.install

    def guarded_install(*args, **kwargs):
        observed.append(kwargs)
        return original_install(*args, **kwargs)

    monkeypatch.setattr(shell, "install", guarded_install)
    confirm = Mock(return_value=True)
    result = repair.repair_registration(Path(CURRENT), confirm, registry)
    assert result.changed_values > 0
    confirm.assert_called_once_with(EXE, CURRENT)
    assert managed.call_count == 2
    assert observed[0]["allowed_previous_executables"] == (EXE, CURRENT, SIBLING)
    assert observed[0]["expected_registration"] == previous
    assert shell.registration_owner(registry).executable == CURRENT
    assert all(registry.values[key] == value for key, value in defaults.items())
    assert not any("userchoice" in event[1].casefold() for event in registry.log
                   if event[0] in {"write", "delete"})


@pytest.mark.parametrize("answer", [False, None, 1, "yes"])
def test_cancel_or_non_boolean_confirmation_does_nothing(registry, managed, answer):
    before = snapshot(registry)
    assert repair.repair_registration(Path(CURRENT), lambda *_: answer, registry) is None
    assert snapshot(registry) == before
    assert managed.call_count == 1


def test_changed_owner_between_prompt_and_apply_is_not_adopted(registry, managed):
    after_change = []

    def confirm(*_):
        install(registry, OTHER_EXE)
        after_change.append(snapshot(registry))
        return True

    with pytest.raises(shell.RegistrationConflict, match="changed after confirmation"):
        repair.repair_registration(Path(CURRENT), confirm, registry)
    assert snapshot(registry) == after_change[0]
    assert shell.registration_owner(registry).executable == OTHER_EXE


def test_same_owner_with_changed_manifest_bytes_is_rejected(registry, managed):
    after_change = []

    def confirm(*_):
        document, _ = shell._read_manifest(registry)
        registry.seed(shell.METADATA_KEY, shell.MANIFEST_NAME,
                      shell.Value(shell.REG_SZ, json.dumps(document, indent=2)))
        after_change.append(snapshot(registry))
        return True

    with pytest.raises(shell.RegistrationConflict, match="changed after confirmation"):
        repair.repair_registration(Path(CURRENT), confirm, registry)
    assert snapshot(registry) == after_change[0]


@pytest.mark.parametrize("during_prompt", [False, True])
def test_modified_owned_values_are_preserved_without_any_takeover(registry, managed, during_prompt):
    address = (shell.CAPABILITIES, "ApplicationName")
    foreign = shell.Value(shell.REG_SZ, "Modified by another app")
    before = []

    def modify():
        registry.seed(*address, foreign)
        before.append(snapshot(registry))

    def confirm(*_):
        assert during_prompt
        modify()
        return True

    if not during_prompt:
        modify()
    with pytest.raises(shell.RegistrationConflict, match="changed Luma's"):
        repair.repair_registration(Path(CURRENT), confirm, registry)
    assert snapshot(registry) == before[0]
    assert registry.read(*address) == foreign


@pytest.mark.parametrize("during_prompt", [False, True])
def test_pending_journal_never_recovers_through_cross_copy_repair(registry, managed, during_prompt):
    before = []

    def interrupt():
        document, _ = shell._read_manifest(registry)
        data = document["data"]
        set_document(registry, {"version": 1, "state": "pending", "base": data,
                                "target": data, "changes": [], "created": []})
        before.append(snapshot(registry))

    def confirm(*_):
        assert during_prompt
        interrupt()
        return True

    if not during_prompt:
        interrupt()
    with pytest.raises(shell.RegistrationConflict, match="active Luma"):
        repair.repair_registration(Path(CURRENT), confirm, registry)
    assert snapshot(registry) == before[0]


@pytest.mark.parametrize("raw", ["not JSON", '{"version":1,"version":1}',
                                '{"version":99,"state":"active","data":{}}'])
def test_malformed_manifest_is_left_intact(registry, managed, raw):
    registry.seed(shell.METADATA_KEY, shell.MANIFEST_NAME, shell.Value(shell.REG_SZ, raw))
    before = snapshot(registry)
    confirm = Mock(return_value=True)
    with pytest.raises(shell.InvalidManifest):
        repair.repair_registration(Path(CURRENT), confirm, registry)
    confirm.assert_not_called()
    assert snapshot(registry) == before


@pytest.mark.parametrize("role", ["command", "icon", "dll"])
def test_every_path_role_must_agree_on_exact_owner(registry, managed, role):
    document, _ = shell._read_manifest(registry)
    entry = next(entry for entry in document["data"]["entries"]
                 if shell._path_role(shell._address(entry)) == role)
    foreign = {(write.key, write.name): write.value for write in shell.registration_plan(OTHER_EXE)}
    address = shell._address(entry)
    entry["written"] = shell._encoded_value(foreign[address])
    registry.seed(*address, foreign[address])
    set_document(registry, document)
    before = snapshot(registry)
    with pytest.raises(shell.RegistrationConflict, match="inconsistent"):
        repair.repair_registration(Path(CURRENT), lambda *_: pytest.fail("Must not prompt"), registry)
    assert snapshot(registry) == before


@pytest.mark.parametrize("path", [r"\\server\share\Luma.exe", r"C:\Old\..\Other\Luma.exe",
                                 r"C:\NUL\Luma.exe", r"C:Luma.exe"])
def test_invalid_old_path_cannot_be_confirmed(registry, managed, path):
    document, _ = shell._read_manifest(registry)
    entry = next(entry for entry in document["data"]["entries"]
                 if shell._path_role(shell._address(entry)) == "command")
    entry["written"]["data"] = f'"{path}" "%1"'
    registry.seed(shell.METADATA_KEY, shell.MANIFEST_NAME,
                  shell.Value(shell.REG_SZ, json.dumps(document)))
    before = snapshot(registry)
    with pytest.raises(shell.InvalidManifest):
        repair.repair_registration(Path(CURRENT), lambda *_: pytest.fail("Must not prompt"), registry)
    assert snapshot(registry) == before


def test_old_executable_is_not_required_to_exist(registry, managed, monkeypatch):
    # The synthetic old Windows path has no corresponding filesystem file.
    monkeypatch.setattr(Path, "exists", lambda *_: pytest.fail("Do not inspect the old copy"))
    assert repair.repair_registration(Path(CURRENT), lambda *_: True, registry).changed_values


def test_same_installation_needs_no_cross_copy_prompt(registry, managed):
    install(registry, SIBLING)
    before = snapshot(registry)
    with pytest.raises(shell.RegistrationConflict, match="already belongs"):
        repair.repair_registration(Path(CURRENT), lambda *_: pytest.fail("Must not prompt"), registry)
    assert snapshot(registry) == before


def test_new_payload_is_revalidated_after_confirmation(registry, managed):
    managed.side_effect = [(CURRENT, (CURRENT, SIBLING)),
                           repair.installer.InstallerError("Invalid installer marker")]
    before = snapshot(registry)
    with pytest.raises(repair.installer.InstallerError, match="Invalid installer marker"):
        repair.repair_registration(Path(CURRENT), lambda *_: True, registry)
    assert snapshot(registry) == before


def test_changed_new_executable_is_not_silently_substituted(registry, managed):
    managed.side_effect = [(CURRENT, (CURRENT, SIBLING)), (SIBLING, (CURRENT, SIBLING))]
    before = snapshot(registry)
    with pytest.raises(repair.installer.InstallerError, match="payload changed"):
        repair.repair_registration(Path(CURRENT), lambda *_: True, registry)
    assert snapshot(registry) == before


def test_invalid_managed_payload_is_rejected_before_inspecting_registry(monkeypatch):
    monkeypatch.setattr(shell, "_runtime_guard", lambda: None)
    validate = Mock(side_effect=repair.installer.InstallerError("Missing installer marker"))
    monkeypatch.setattr(repair.installer, "installed_executables", validate)
    owner = Mock(side_effect=AssertionError("Must not inspect a registry for an invalid payload"))
    monkeypatch.setattr(shell, "registration_owner", owner)
    with pytest.raises(repair.installer.InstallerError, match="Missing installer marker"):
        repair.repair_registration(Path(CURRENT), lambda *_: True, FakeRegistry())
    validate.assert_called_once_with(Path(CURRENT))
    owner.assert_not_called()


def test_confirmed_snapshot_comparison_is_inside_the_install_lock(managed, monkeypatch):
    class LockedRegistry(FakeRegistry):
        depth = 0

        @contextlib.contextmanager
        def locked(self):
            self.depth += 1
            try:
                yield
            finally:
                self.depth -= 1

    registry = LockedRegistry()
    install(registry)
    original = shell._registration_owner_locked
    checked = []

    def checked_owner(registry):
        assert registry.depth == 1
        checked.append(True)
        return original(registry)

    monkeypatch.setattr(shell, "_registration_owner_locked", checked_owner)
    assert repair.repair_registration(Path(CURRENT), lambda *_: True, registry).changed_values
    assert checked == [True, True]


def test_snapshot_guard_cannot_be_used_without_its_exact_owner_allowlist(registry, managed):
    previous = shell.registration_owner(registry)
    before = snapshot(registry)
    for allowlist in (None, (CURRENT, SIBLING)):
        with pytest.raises(shell.RegistrationConflict, match="exact confirmed owner"):
            shell.install(Path(CURRENT), registry, allowed_previous_executables=allowlist,
                          expected_registration=previous)
    assert snapshot(registry) == before


@pytest.mark.parametrize("replacement", ["sibling", "pending"])
def test_manifest_race_after_guard_never_recovers_or_adopts_another_snapshot(
        registry, managed, monkeypatch, replacement):
    previous = shell.registration_owner(registry)
    original = shell._registration_owner_locked
    after_change = []

    def change_after_read(registry):
        result = original(registry)
        if replacement == "sibling":
            install(registry, SIBLING)
        else:
            document, _ = shell._read_manifest(registry)
            data = document["data"]
            set_document(registry, {"version": 1, "state": "pending", "base": data,
                                    "target": data, "changes": [], "created": []})
        after_change.append(snapshot(registry))
        return result

    monkeypatch.setattr(shell, "_registration_owner_locked", change_after_read)
    with pytest.raises(shell.RegistrationConflict, match="changed after confirmation"):
        shell.install(Path(CURRENT), registry, allowed_previous_executables=(EXE, CURRENT, SIBLING),
                      expected_registration=previous)
    assert snapshot(registry) == after_change[0]


def test_target_path_changed_during_final_binary_check_is_rejected(registry, managed, monkeypatch):
    previous = shell.registration_owner(registry)
    before = snapshot(registry)
    monkeypatch.setattr(shell, "_checked_executable", lambda *_: SIBLING)
    with pytest.raises(shell.RegistrationConflict, match="executable path changed"):
        shell.install(Path(CURRENT), registry, allowed_previous_executables=(EXE, CURRENT, SIBLING),
                      expected_registration=previous)
    assert snapshot(registry) == before


def test_backend_module_import_does_not_import_qt():
    result = subprocess.run(
        [sys.executable, "-c", "import sys; import luma.registration_repair; "
         "assert not any(name.startswith('PySide6') for name in sys.modules)"],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("language", ["en", "zh-CN", "zh-TW"])
def test_confirmation_is_localized_plain_selectable_and_defaults_to_no(qapp, language):
    from PySide6.QtCore import Qt
    from luma.i18n import set_language, tr
    set_language(language)
    previous = r"C:\Photos & {frames}\Luma.exe"
    current = CURRENT.replace("Luma\\versions", "Luma & {current}\\versions")
    box = repair.confirmation_dialog(previous, current)
    try:
        assert box.textFormat() == Qt.TextFormat.PlainText
        assert box.textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByKeyboard
        assert box.defaultButton() == box.button(box.StandardButton.No)
        assert box.escapeButton() == box.button(box.StandardButton.No)
        assert box.button(box.StandardButton.Yes).text() == tr("Yes")
        assert box.button(box.StandardButton.No).text() == tr("No")
        assert previous in box.text() and current in box.text()
        assert box.accessibleName() == box.windowTitle()
        assert box.windowTitle() == repair.TRANSLATIONS.get(language, {}).get(repair.TITLE, repair.TITLE)
    finally:
        box.close()


def test_repair_catalogs_are_complete_and_preserve_named_paths():
    expected = {repair.TITLE, repair.QUESTION, repair.ERROR_TITLE, repair.ERROR_SUMMARY}
    fields = lambda text: {field for _, field, _, _ in Formatter().parse(text) if field}
    for language in ("zh-CN", "zh-TW"):
        assert set(repair.TRANSLATIONS[language]) == expected
        for template, text in repair.TRANSLATIONS[language].items():
            assert template != text
            assert fields(template) == fields(text)


@pytest.mark.parametrize("result,code", [(shell.Result(4), 0), (None, 1)])
def test_main_returns_distinct_success_and_cancel_codes(qapp, monkeypatch, result, code):
    monkeypatch.setattr(repair, "repair_registration", lambda *_: result)
    assert repair.main() == code


def test_main_returns_failure_and_displays_plain_localized_summary(qapp, monkeypatch):
    from luma.dialogs import PlainMessageBox
    from luma import i18n
    monkeypatch.setattr(i18n, "detect_system_language", lambda: "zh-CN")
    monkeypatch.setattr(repair, "repair_registration",
                        Mock(side_effect=shell.RegistrationConflict("Changed <owner> {value}")))
    shown = []
    monkeypatch.setattr(PlainMessageBox, "warning", lambda *args: shown.append(args))
    assert repair.main() == 2
    assert shown[0][1] == repair.TRANSLATIONS["zh-CN"][repair.ERROR_TITLE]
    assert shown[0][2].startswith(repair.TRANSLATIONS["zh-CN"][repair.ERROR_SUMMARY])
    assert shown[0][2].endswith("Changed <owner> {value}")
    assert qapp.font().families()[0] == "Microsoft YaHei"
