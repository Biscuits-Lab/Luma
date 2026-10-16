"""Portable tests for the installer ownership boundary; no registry writes."""
import hashlib
import importlib.util
import re
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

MODULE = Path(__file__).resolve().parents[1] / "luma" / "installer.py"
spec = importlib.util.spec_from_file_location("luma.installer_contract", MODULE)
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)
smoke_spec = importlib.util.spec_from_file_location("smoke_installer_contract", MODULE.parents[1] / "scripts/smoke_installer.py")
smoke = importlib.util.module_from_spec(smoke_spec)
smoke_spec.loader.exec_module(smoke)


def message_section(path, section):
    result = {}
    current = None
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if line.startswith("["):
            current = line
        elif current == section and line and not line.startswith(";") and "=" in line:
            key, value = line.split("=", 1)
            if key in result:
                raise AssertionError(f"Duplicate message: {path.name}: {key}")
            result[key] = value
    return result


class InstallerLanguageTests(unittest.TestCase):
    root = MODULE.parents[1]
    languages = root / "packaging/windows/languages"

    def test_pinned_upstream_files_keep_exact_bytes(self):
        manifest = json.loads((self.languages / "UPSTREAM.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["commit"], "cfdf48923178df4b4f040e038b423aa555a61ffc")
        attributes = (self.root / ".gitattributes").read_text(encoding="utf-8")
        for name, entry in manifest["files"].items():
            with self.subTest(name=name):
                data = (self.languages / name).read_bytes()
                self.assertEqual(len(data), entry["bytes"])
                self.assertEqual(hashlib.sha256(data).hexdigest(), entry["sha256"])
                self.assertIn("/packaging/windows/languages/" + name + " -text", attributes)
                self.assertTrue(entry["url"].startswith("https://raw.githubusercontent.com/jrsoftware/issrc/" + manifest["commit"] + "/"))

    def test_chinese_native_messages_cover_reference_and_placeholders(self):
        expected = message_section(self.languages / "Reference-Default.isl", "[Messages]")
        self.assertEqual(len(expected), 281)
        for name in ("ChineseSimplified.isl", "ChineseTraditional.isl"):
            actual = message_section(self.languages / name, "[Messages]")
            if "Traditional" in name:
                actual.update(message_section(self.languages / "ChineseTraditionalOverrides.isl", "[Messages]"))
            with self.subTest(name=name):
                self.assertFalse(set(expected) - set(actual))
                for key, text in expected.items():
                    self.assertEqual(sorted(re.findall(r"%[1-9]|\[[a-z/]+\]", text)),
                                     sorted(re.findall(r"%[1-9]|\[[a-z/]+\]", actual[key])), key)

    def test_custom_messages_complete_for_all_languages(self):
        messages = message_section(self.languages / "LumaMessages.iss", "[CustomMessages]")
        grouped = {lang: {k.split(".", 1)[1]: v for k, v in messages.items() if k.startswith(lang + ".")}
                   for lang in ("en", "zhCN", "zhTW")}
        self.assertEqual(len(grouped["en"]), 19)
        for lang in ("zhCN", "zhTW"):
            self.assertEqual(set(grouped[lang]), set(grouped["en"]))
            for key, original in grouped["en"].items():
                self.assertNotEqual(grouped[lang][key], original)
                self.assertEqual(sorted(re.findall(r"%[1-9n]", original)),
                                 sorted(re.findall(r"%[1-9n]", grouped[lang][key])), key)
        script = (self.root / "packaging/windows/Luma.iss").read_text(encoding="utf-8")
        script += (self.root / "packaging/windows/LumaUI.iss").read_text(encoding="utf-8")
        referenced = set(re.findall(r"CustomMessage\('([^']+)'\)", script))
        referenced.update(re.findall(r"\{cm:([^},]+)", script))
        self.assertEqual(referenced, set(grouped["en"]))

    def test_setup_uses_current_windows_ui_language_and_english_fallback(self):
        script = (self.root / "packaging/windows/Luma.iss").read_text(encoding="utf-8")
        for directive in ("LanguageDetectionMethod=uilanguage", "ShowLanguageDialog=no", "UsePreviousLanguage=no"):
            self.assertIn(directive, script)
        languages = script.split("[Languages]", 1)[1].split("[Tasks]", 1)[0]
        self.assertEqual(re.findall(r'Name: "([^"]+)";', languages), ["en", "zhCN", "zhTW"])
        self.assertIn('languages\\ChineseTraditionalOverrides.isl', languages)
        # Approved native dark design keeps the operating-system title bar.
        self.assertIn("WizardStyle=modern dark hidebevels\n", script)
        self.assertNotIn("includetitlebar", script)
        for name, langid, codepage in (("ChineseSimplified.isl", "$0804", "936"),
                                       ("ChineseTraditional.isl", "$0404", "950")):
            options = {k.lower(): v for k, v in message_section(self.languages / name, "[LangOptions]").items()}
            self.assertEqual(options["languageid"], langid)
            self.assertEqual(options["languagecodepage"], codepage)

    def test_custom_error_paths_keep_parameters_out_of_message_templates(self):
        script = (self.root / "packaging/windows/Luma.iss").read_text(encoding="utf-8")
        for key, variable in (("ChangedPayload", "Path"), ("CannotVerifyPayload", "Path"), ("UninstallBusy", "BusyFile")):
            self.assertIn(f"FmtMessage(CustomMessage('{key}'), [{variable}])", script)
        self.assertIn("CustomMessage('RegistrationFailed')", script)
        self.assertIn("StopUninstall(CustomMessage('UninstallCannotRemove'))", script)


    def test_native_design_preserves_engine_and_accessibility(self):
        ui = (self.root / "packaging/windows/LumaUI.iss").read_text(encoding="utf-8")
        script = (self.root / "packaging/windows/Luma.iss").read_text(encoding="utf-8")
        self.assertIn("IsDarkInstallMode and not HighContrastActive", ui)
        self.assertIn("LumaFooter.Color := $191919", ui)
        self.assertIn("WizardBackColor=#222222", script)
        self.assertIn("BodyHeight + ScaleY(12)", ui)
        self.assertIn("ScaleX(112), ScaleY(36)", ui)
        self.assertIn("ScaleX(82), ScaleY(36)", ui)
        self.assertIn("ClientHeight - ScaleY(60)", ui)
        self.assertIn("LumaFailureMemo.ScrollBars := ssVertical", ui)
        for control in ("NextButton", "CancelButton", "DirBrowseButton"):
            self.assertNotIn(control + ".OnClick :=", ui)
        self.assertNotIn("BorderStyle := bsNone", ui)
        self.assertNotIn("SuppressibleMsgBox", ui)
        self.assertIn("LayoutLumaWizard(CurPageID)", script)
        self.assertIn("DisableDirPage=no", script)

    def test_compact_task_choice_preserves_native_silent_and_previous_defaults(self):
        ui = (self.root / "packaging/windows/LumaUI.iss").read_text(encoding="utf-8")
        self.assertIn("LumaDesktopTouched and not WizardSilent", ui)
        self.assertIn("PageID = wpSelectTasks", ui)
        self.assertIn("WizardSelectTasks('desktopicon')", ui)
        self.assertIn("WizardSelectTasks('!desktopicon')", ui)
        self.assertIn("'Inno Setup: Selected Tasks'", ui)
        self.assertIn("'/TASKS='", ui)
        self.assertIn("'/MERGETASKS='", ui)
        self.assertNotIn("RegWrite", ui)
        self.assertNotIn("RegDelete", ui)

    def test_ui_font_stress_build_cannot_replace_release_output(self):
        script = (self.root / "packaging/windows/Luma.iss").read_text(encoding="utf-8")
        build = (self.root / "scripts/build_installer.ps1").read_text(encoding="utf-8")
        self.assertIn("#ifdef LumaUiFontScaleTest", script)
        self.assertIn("en.DialogFontSize=18", script)
        self.assertIn("$UiFontScaleTest -and !$Artifacts.StartsWith($UiTestRoot", build)
        self.assertIn("windows-x64-ui-font-test", script)
        self.assertIn("never release artifacts", build)


class InstallerBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def payload(self, root, digest="a" * 64):
        payload_id = "0.1.1-" + digest[:24]
        directory = root / "versions" / payload_id
        directory.mkdir(parents=True)
        exe = directory / "Luma.exe"
        exe.write_bytes(b"MZ-test-only")
        (directory / installer.MARKER_NAME).write_text(json.dumps({
            "managed_install": True, "layout": "versions-v1", "version": "0.1.1",
            "payload_id": payload_id, "bundle_sha256": digest,
        }), encoding="utf-8")
        return exe

    def test_arp_uninstaller_allows_incremented_filename(self):
        for name in ("unins000.exe", "unins001.exe"):
            exe = self.root / name
            exe.write_bytes(b"test-only")
            for command in (f'"{exe}"', str(exe), f'"{exe}" /LOG'):
                self.assertEqual(smoke.registered_uninstaller(command, self.root), exe.resolve(strict=True))

    def test_arp_uninstaller_refuses_other_paths_or_arguments(self):
        root = self.root / "installed"
        root.mkdir()
        foreign = self.root / "unins001.exe"
        foreign.write_bytes(b"test-only")
        with self.assertRaises(ValueError):
            smoke.registered_uninstaller(f'"{foreign}"', root)
        exe = root / "Luma.exe"
        exe.write_bytes(b"test-only")
        with self.assertRaises(ValueError):
            smoke.registered_uninstaller(f'"{exe}"', root)
        with self.assertRaises(ValueError):
            smoke.registered_uninstaller(f'"{exe}" /arbitrary', root)

    def test_arp_uninstaller_rejects_altered_or_injected_log_suffix(self):
        exe = self.root / "unins001.exe"
        exe.write_bytes(b"test-only")
        for suffix in (" /LOG=other.log", " /LOG /SILENT", " /LOG & calc.exe",
                       " /LOG\n", " /LOGGING", " /SILENT", " /LOG "):
            with self.subTest(suffix=suffix), self.assertRaises(ValueError):
                smoke.registered_uninstaller(f'"{exe}"{suffix}', self.root)

    def test_compiler_minimum_uses_inno_engine_version(self):
        root = MODULE.parents[1]
        script = (root / "packaging/windows/Luma.iss").read_text(encoding="utf-8")
        build = (root / "scripts/build_installer.ps1").read_text(encoding="utf-8")
        self.assertIn("#if Ver < EncodeVer(6, 7, 0)", script)
        self.assertIn("#error Inno Setup 6.7 or newer is required", script)
        self.assertNotIn(".VersionInfo.FileVersion", build)

    def test_registration_is_automatic_checked_and_has_no_thumbnail_task(self):
        script = (MODULE.parents[1] / "packaging/windows/Luma.iss").read_text(encoding="utf-8")
        self.assertIn("if CurStep <> ssPostInstall then Exit;", script)
        self.assertIn("'--shell-register-current-user'", script)
        self.assertIn("ShellRegistrationComplete := ResultCode = 0;", script)
        self.assertIn("if ShellRegistrationComplete then Result := 0 else Result := 10;", script)
        self.assertIn("Check: ShellRegistrationSucceeded", script)
        tasks = script.split("[Tasks]", 1)[1].split("[Files]", 1)[0]
        self.assertNotIn("thumbnail", tasks.lower())
        self.assertNotIn("shell", tasks.lower())
        self.assertIn("PrivilegesRequired=lowest", script)

    def test_only_same_installation_marker_bearing_payloads(self):
        first = self.payload(self.root / "installed")
        second = self.payload(self.root / "installed", "b" * 64)
        foreign = self.payload(self.root / "portable")
        unknown = first.parent.parent / "user-images"
        unknown.mkdir()
        self.assertEqual(set(installer.installed_executables(second)), {first, second})
        self.assertNotIn(foreign, installer.installed_executables(second))

    def test_missing_or_tampered_current_marker_fails(self):
        exe = self.payload(self.root)
        marker = exe.parent / installer.MARKER_NAME
        marker.write_text('{"managed_install":true}', encoding="utf-8")
        with self.assertRaises(installer.InstallerError):
            installer.installed_executables(exe)
        marker.unlink()
        with self.assertRaises(installer.InstallerError):
            installer.installed_executables(exe)

    def test_bad_sibling_marker_is_ignored(self):
        exe = self.payload(self.root)
        other = self.payload(self.root, "c" * 64)
        (other.parent / installer.MARKER_NAME).write_text("x" * 4097)
        self.assertEqual(installer.installed_executables(exe), (exe,))

    def test_bounded_directory_enumeration(self):
        exe = self.payload(self.root)
        self.payload(self.root, "d" * 64)
        with patch.object(installer, "MAX_INSTALLED_PAYLOADS", 1):
            with self.assertRaises(installer.InstallerError):
                installer.installed_executables(exe)

    def test_symlink_payload_is_not_accepted(self):
        exe = self.payload(self.root / "real")
        link = self.root / "link"
        try:
            link.symlink_to(self.root / "real", target_is_directory=True)
        except OSError:
            self.skipTest("Creating symlinks is not permitted on this host")
        with self.assertRaises(installer.InstallerError):
            installer.installed_executables(link / exe.relative_to(self.root / "real"))

    def test_uninstall_calls_only_validated_exact_owners(self):
        first = self.payload(self.root / "installed")
        second = self.payload(self.root / "installed", "b" * 64)
        self.payload(self.root / "portable")
        called = []
        fake = types.SimpleNamespace(uninstall_if_executable=called.append)
        with patch.object(installer, "__package__", "luma"), patch.dict("sys.modules", {"luma.shell_integration": fake}):
            installer.uninstall_managed_installation(second)
        self.assertEqual(set(called), {first, second})

    def test_automatic_registration_allows_only_validated_sibling_owners(self):
        first = self.payload(self.root / "installed")
        second = self.payload(self.root / "installed", "b" * 64)
        foreign = self.payload(self.root / "portable")
        called = []
        def register(exe, *, allowed_previous_executables):
            called.append((exe, allowed_previous_executables))
        fake = types.SimpleNamespace(install=register)
        with patch.object(installer, "__package__", "luma"), patch.dict("sys.modules", {"luma.shell_integration": fake}):
            installer.register_managed_installation(second)
        self.assertEqual(len(called), 1)
        self.assertEqual(called[0][0], second.resolve(strict=True))
        self.assertEqual(set(called[0][1]), {first.resolve(strict=True), second.resolve(strict=True)})
        self.assertNotIn(foreign.resolve(strict=True), called[0][1])

    def test_invalid_marker_blocks_automatic_registration(self):
        exe = self.payload(self.root)
        (exe.parent / installer.MARKER_NAME).unlink()
        called = []
        fake = types.SimpleNamespace(install=lambda *args, **kwargs: called.append(args))
        with patch.object(installer, "__package__", "luma"), patch.dict("sys.modules", {"luma.shell_integration": fake}):
            with self.assertRaises(installer.InstallerError):
                installer.register_managed_installation(exe)
        self.assertEqual(called, [])

    def test_registration_conflict_preserves_all_payloads(self):
        first = self.payload(self.root)
        second = self.payload(self.root, "b" * 64)
        def conflict(*args, **kwargs):
            raise RuntimeError("Unrelated registration was preserved")
        fake = types.SimpleNamespace(install=conflict)
        with patch.object(installer, "__package__", "luma"), patch.dict("sys.modules", {"luma.shell_integration": fake}):
            with self.assertRaises(RuntimeError):
                installer.register_managed_installation(second)
        self.assertTrue(first.is_file())
        self.assertTrue(second.is_file())

    def test_invalid_marker_prevents_any_registrar_call(self):
        exe = self.payload(self.root)
        (exe.parent / installer.MARKER_NAME).unlink()
        called = []
        fake = types.SimpleNamespace(uninstall_if_executable=called.append)
        with patch.object(installer, "__package__", "luma"), patch.dict("sys.modules", {"luma.shell_integration": fake}):
            with self.assertRaises(installer.InstallerError):
                installer.uninstall_managed_installation(exe)
        self.assertEqual(called, [])

    def test_registrar_conflict_propagates_without_deleting_files(self):
        exe = self.payload(self.root)
        def conflict(path):
            raise RuntimeError("ambiguous pending ownership")
        fake = types.SimpleNamespace(uninstall_if_executable=conflict)
        with patch.object(installer, "__package__", "luma"), patch.dict("sys.modules", {"luma.shell_integration": fake}):
            with self.assertRaises(RuntimeError):
                installer.uninstall_managed_installation(exe)
        self.assertTrue(exe.is_file())


if __name__ == "__main__":
    unittest.main()
