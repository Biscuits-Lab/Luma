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
preflight_spec = importlib.util.spec_from_file_location("preflight_installer_contract", MODULE.parents[1] / "scripts/preflight_installer.py")
preflight = importlib.util.module_from_spec(preflight_spec)
preflight_spec.loader.exec_module(preflight)
ui_spec = importlib.util.spec_from_file_location("smoke_installer_ui_contract", MODULE.parents[1] / "scripts/smoke_installer_ui.py")
ui_smoke = importlib.util.module_from_spec(ui_spec)
ui_spec.loader.exec_module(ui_smoke)
progress_spec = importlib.util.spec_from_file_location("smoke_installer_progress_contract", MODULE.parents[1] / "scripts/smoke_installer_progress.py")
progress_smoke = importlib.util.module_from_spec(progress_spec)
progress_spec.loader.exec_module(progress_smoke)


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


    def test_confirmed_owner_repair_is_interactive_only_and_keeps_failure_exit(self):
        source = (self.root / "packaging/windows/Luma.iss").read_text(encoding="utf-8")
        block = source.split("procedure CurStepChanged(", 1)[1].split("function GetCustomSetupExitCode", 1)[0]
        guard = "if not ShellRegistrationComplete and not WizardSilent then begin"
        self.assertEqual(block.count("'--shell-repair-registration'"), 1)
        self.assertIn(guard, block)
        after_guard = block.split(guard, 1)[1]
        self.assertIn("'--shell-repair-registration'", after_guard.split("end;", 1)[0])
        self.assertIn("ShellRegistrationComplete := RepairResult = 0", after_guard)
        self.assertIn("ShellRegistrationFailure := CustomMessage('RegistrationFailed')", after_guard)
        self.assertIn("if ShellRegistrationComplete then Result := 0 else Result := 10", source)

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
        self.assertIn("WizardForm.SelectDirBrowseLabel.ShowAccelChar := True", ui)
        self.assertIn("WizardForm.SelectDirBrowseLabel.FocusControl := WizardForm.DirEdit", ui)

    def test_folder_button_keeps_accessibility_and_native_browse_action(self):
        ui = (self.root / "packaging/windows/LumaUI.iss").read_text(encoding="utf-8")
        for statement in ("LumaBrowseButton := TBitmapButton.Create(WizardForm)",
                          "LumaBrowseButton.Caption := CustomMessage('ChangeLocation')",
                          "LumaBrowseButton.Hint := CustomMessage('ChangeLocation')",
                          "LumaBrowseButton.TabStop := True",
                          "LumaBrowseButton.OnClick := WizardForm.DirBrowseButton.OnClick",
                          "ContentWidth := WizardForm.SelectDirPage.ClientWidth",
                          "LumaBrandIcon.BackColor := clNone"):
            self.assertIn(statement, ui)
        self.assertIn("ScaleX(38), ScaleY(38)", ui)
        self.assertIn("LumaBrowseButton.Bitmap.Canvas", ui)
        self.assertNotIn("LumaBrandIcon.Bitmap.Canvas", ui)

    def test_progress_stays_engine_owned_without_simulated_animation(self):
        ui = (self.root / "packaging/windows/LumaUI.iss").read_text(encoding="utf-8")
        self.assertIn("procedure CurInstallProgressChanged(CurProgress, MaxProgress: Integer)", ui)
        self.assertIn("LumaSetWindowTheme(Window, #0, #0)", ui)
        self.assertIn("LumaSetWindowRgn(Window, Region, True)", ui)
        self.assertIn("LumaDeleteObject(Region)", ui)
        self.assertIn("WizardForm.ProgressGauge.StyleElements := LumaProgressStyleElements", ui)
        self.assertIn("ContentWidth := WizardForm.InstallingPage.ClientWidth", ui)
        for property_name in ("Min", "Max", "Position", "State", "Style", "Visible"):
            self.assertNotRegex(ui, rf"ProgressGauge\.{property_name}\s*:=")
        for forbidden in ("SetTimer", "CreateTimer", "Sleep(", "OnPaint :="):
            self.assertNotIn(forbidden, ui)

    def test_pascal_arrays_cannot_be_misread_as_inno_sections(self):
        # Inno scans leading '[' before Pascal compilation, even inside [Code].
        # Keep array/set literals on the preceding expression's line.
        for name in ("Luma.iss", "LumaUI.iss"):
            source = (self.root / "packaging/windows" / name).read_text(encoding="utf-8")
            if name == "Luma.iss":
                source = source.split("[Code]", 1)[1]
            with self.subTest(name=name):
                self.assertIsNone(re.search(r"(?m)^\s*\[", source))

    def test_pascal_code_uses_pascal_comments(self):
        # Semicolon comments are Inno section syntax, not Pascal comments.
        # A code include is inserted literally into the surrounding [Code].
        for name in ("Luma.iss", "LumaUI.iss"):
            source = (self.root / "packaging/windows" / name).read_text(encoding="utf-8")
            if name == "Luma.iss":
                source = source.split("[Code]", 1)[1]
            with self.subTest(name=name):
                self.assertIsNone(re.search(r"(?m)^\s*;", source))

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


class InstallerNativeEvidenceTests(unittest.TestCase):
    def test_progress_parser_requires_actual_native_callback_rows(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "install.log"
            path.write_text("LUMA_PROGRESS rounded clipping unavailable\n"
                "LUMA_PROGRESS window=12;gauge=15;current=25;maximum=100;position=25;minimum=0;range=100\n",
                encoding="utf-16")
            self.assertEqual(progress_smoke.progress_rows(path), [{"window": 12, "gauge": 15,
                "current": 25, "maximum": 100, "position": 25, "minimum": 0, "range": 100}])

    def test_real_progress_observer_does_not_write_progress_or_click_install(self):
        source = (MODULE.parents[1] / "scripts/smoke_installer_progress.py").read_text(encoding="utf-8")
        self.assertIn('"/SILENT" not in command', source)
        self.assertIn("accessible_progress(gauge)", source)
        self.assertIn("api.owned(gauge, job.job)", source)
        self.assertIn("0 < position < high", source)
        for forbidden in ("PBM_SETPOS", "BM_CLICK", "SendKeys", "SetValue", "SetTimer"):
            self.assertNotIn(forbidden, source)

    def test_parent_clipping_is_rejected_even_inside_wizard(self):
        page = [236, 322, 788, 470]
        released_button = [696, 353, 800, 391]
        self.assertFalse(ui_smoke.contains_rect(page, released_button))
        self.assertTrue(ui_smoke.contains_rect(page, [750, 353, 788, 391]))

    def test_logo_proof_rejects_the_reported_rectangular_backing(self):
        from PIL import Image, ImageDraw
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "wizard.png"
            image = Image.new("RGB", (200, 160), "#222222")
            metrics = {"logo_left": 34, "logo_top": 31, "logo_width": 66, "logo_height": 66}
            image.save(path)
            self.assertTrue(ui_smoke.verify_logo_background(path, metrics, [0, 0, 200, 160],
                [0, 0, 200, 160])["passed"])
            ImageDraw.Draw(image).rectangle((34, 31, 99, 96), fill="#282828")
            image.save(path)
            with self.assertRaisesRegex(AssertionError, "rectangular backing"):
                ui_smoke.verify_logo_background(path, metrics, [0, 0, 200, 160], [0, 0, 200, 160])


class InstallerCompilePreflightTests(unittest.TestCase):
    def test_actual_script_is_compiled_with_output_disabled_for_both_variants(self):
        root, work = Path("repo"), Path("repo/build/temporary")
        for scaled in (False, True):
            command = preflight.compile_command(Path("ISCC.exe"), root, work, "0.1.3", scaled)
            self.assertEqual(command[1], "/O-")
            self.assertEqual(command[-1], str(root / "packaging/windows/Luma.iss"))
            self.assertIn(f"/DArtifactDir={work / 'output'}", command)
            self.assertEqual("/DLumaUiFontScaleTest=1" in command, scaled)
            self.assertNotIn("artifacts", " ".join(command))

    def test_success_never_runs_dummy_payload_and_removes_temporary_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "pyproject.toml").write_text('[project]\nversion = "0.1.3"\n')
            with patch.object(preflight.subprocess, "run", return_value=types.SimpleNamespace(
                    returncode=0, stdout="checked", stderr="")) as run:
                report = preflight.preflight(root, Path("ISCC.exe"))
            self.assertTrue(report["passed"])
            self.assertEqual(run.call_count, 2)
            for call in run.call_args_list:
                self.assertEqual(call.args[0][0], "ISCC.exe")
                self.assertEqual(call.kwargs["timeout"], 90)
            self.assertEqual(sorted(x.name for x in (root / "build/installer-preflight").iterdir()),
                             ["font-stress.log", "normal.log", "report.json"])

    def test_compile_failure_stops_and_records_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "pyproject.toml").write_text('[project]\nversion = "0.1.3"\n')
            with patch.object(preflight.subprocess, "run", return_value=types.SimpleNamespace(
                    returncode=2, stdout="compile error", stderr="")) as run:
                report = preflight.preflight(root, Path("ISCC.exe"))
            self.assertFalse(report["passed"])
            self.assertEqual(run.call_count, 1)
            self.assertEqual(report["cases"][0]["returncode"], 2)


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
