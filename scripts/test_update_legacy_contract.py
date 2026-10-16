"""Portable contracts for the explicitly historical frozen-GUI diagnostic."""
import importlib.util
from pathlib import Path
import unittest

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


if __name__ == "__main__":
    unittest.main()
