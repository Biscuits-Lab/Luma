"""Portable contracts for the explicitly historical frozen-GUI diagnostic."""
import importlib.util
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("legacy_update_contract", ROOT / "scripts/smoke_update_legacy.py")
legacy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(legacy)


class LegacyUpdateContracts(unittest.TestCase):
    def test_official_old_installer_pin_is_not_an_unverified_rebuild(self):
        self.assertEqual(legacy.OLD_SETUP_SIZE, 34552184)
        self.assertEqual(legacy.OLD_SETUP_SHA, "ce6902745fbc914663e6000f586798ee031f4e46b29133ffc2fe95e90cf547d6")
        with self.assertRaisesRegex(ValueError, "exact publicly verified"):
            legacy.validate_old_setup(b"not a setup executable")

    def test_native_actions_check_pid_executable_and_visibility(self):
        source = legacy.UIA_SCRIPT
        self.assertIn("Get-Process -Id $TargetProcess", source)
        self.assertIn("[IO.Path]::GetFullPath($ExpectedExe)", source)
        self.assertIn("$_.Current.ProcessId -eq $TargetProcess", source)
        self.assertIn("-not $_.Current.IsOffscreen", source)
        self.assertIn("$matches.Count -ne 1", source)
        self.assertIn("$pattern.Invoke()", source)
        for forbidden in ("SendKeys", "Invoke-Expression", "Start-Process", "Stop-Process", "SetValue"):
            self.assertNotIn(forbidden, source)

    def test_version_probe_cannot_pass_on_hidden_or_fake_ui_text(self):
        snapshot = {"elements": [{"name": "0.1.3", "offscreen": True},
                                  {"name": "Luma 0.1.2", "offscreen": False}]}
        self.assertEqual(legacy.visible_names(snapshot), {"Luma 0.1.2"})
        source = (ROOT / "scripts/smoke_update_legacy.py").read_text()
        self.assertIn('inspect_version(new_exe, "0.1.3", "new")', source)
        self.assertIn('hashlib.sha256(candidates[0].read_bytes()).hexdigest() != NEW_EXE_SHA', source)
        self.assertIn('if latest.get("id") != 415136172', source)
        self.assertIn('"RUNNER_ENVIRONMENT") != "github-hosted"', source)
        self.assertNotIn('import updater', source)
        self.assertNotIn('PYINSTALLER_RESET_ENVIRONMENT', source)

    def test_legacy_unnamed_more_requires_exact_description_class_and_process(self):
        row = {"name": "", "help": "", "class": "QPushButton",
               "description": "Save As, copy, open and settings",
               "type": "ControlType.Button", "enabled": True, "offscreen": False,
               "process": 42}
        snapshot = {"process": 42, "elements": [row]}
        self.assertTrue(legacy.action_ready(snapshot, "more"))
        for key, value in (("class", "Button"), ("description", "Other action"), ("process", 43),
                           ("enabled", False), ("offscreen", True),
                           ("type", "ControlType.Window"), ("name", "Unrelated")):
            with self.subTest(key=key):
                self.assertFalse(legacy.action_ready({"process": 42, "elements": [{**row, key: value}]}, "more"))
        self.assertFalse(legacy.action_ready(snapshot, "download"))
        with self.assertRaisesRegex(AssertionError, "Ambiguous"):
            legacy.action_ready({"process": 42, "elements": [row, row]}, "more")

    def test_observation_precedes_single_native_invocation(self):
        script = legacy.UIA_SCRIPT
        self.assertLess(script.index("[IO.File]::WriteAllText"), script.index("if ($Operation -eq 'invoke')"))
        self.assertIn("$_.Current.ClassName -ceq 'QPushButton'", script)
        self.assertIn("GetCurrentPropertyValue(30159)", script)
        self.assertIn("GetCurrentPropertyValue(30002)", script)
        self.assertIn("actualId[i] != expectedId[i]", script)
        self.assertIn("$names -ccontains $descriptions", script)
        source = (ROOT / "scripts/smoke_update_legacy.py").read_text()
        invocation = source.split("    def invoke(pid, executable, target):", 1)[1].split("    def process_path", 1)[0]
        self.assertLess(invocation.index("wait_ui("), invocation.index('"invoke", target'))
        self.assertEqual(invocation.count('"invoke", target'), 1)

    def test_destroyed_window_is_retried_but_other_errors_fail(self):
        api = SimpleNamespace(owned=Mock(return_value=True),
            u=SimpleNamespace(IsWindowVisible=Mock(return_value=True)),
            control=Mock(return_value={"pid": 42}))
        self.assertEqual(legacy.live_owned_window(api, 1, 2), {"pid": 42})
        error = OSError("destroyed HWND")
        error.winerror = 1400
        api.control.side_effect = error
        self.assertIsNone(legacy.live_owned_window(api, 1, 2))
        error.winerror = 5
        with self.assertRaises(OSError):
            legacy.live_owned_window(api, 1, 2)
        api.control.side_effect = None
        api.owned.side_effect = [True, False]
        self.assertIsNone(legacy.live_owned_window(api, 1, 2))
        api.owned.side_effect = None
        api.owned.return_value = False
        api.control.reset_mock()
        self.assertIsNone(legacy.live_owned_window(api, 1, 2))
        api.control.assert_not_called()

    def test_native_capture_waits_for_real_ui_and_only_retries_blank_frame(self):
        source = (ROOT / "scripts/smoke_update_legacy.py").read_text()
        probe = source.split("    def inspect_version(", 1)[1].split("    def close_owned_job", 1)[0]
        self.assertLess(probe.index('label + "-main-ready"'), probe.index("capture_owned("))
        capture = source.split("    def capture_owned(", 1)[1].split("    def inspect_version", 1)[0]
        self.assertIn("time.monotonic() + 10", capture)
        self.assertIn('str(error) != "PrintWindow returned a blank image"', capture)
        self.assertIn("api.screenshot(window, destination)", capture)
        self.assertIn("raise", capture)


if __name__ == "__main__":
    unittest.main()
