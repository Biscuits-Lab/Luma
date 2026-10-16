"""Portable contracts for the actual packaged confirmation GUI probe."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]

class ConfirmationProbeContracts(unittest.TestCase):
    def test_real_helper_is_owned_and_default_action_checked(self):
        source = (ROOT / "scripts/smoke_registration_confirmation.py").read_text()
        for required in ('"--shell-repair-registration"', 'api.windows(job.job)',
                         'api.text(hwnd) in titles', 'str(previous).casefold() not in text',
                         'str(executable).casefold() not in text', '0 if confirm else 1',
                         '"-ExpectedExe", str(executable)', '0x0100, 0x0D',
                         '"RUNNER_ENVIRONMENT") != "github-hosted"'):
            self.assertIn(required, source)
        for forbidden in ('SendKeys', 'Invoke-Expression', 'SetValueEx', 'RegDelete', 'SetForegroundWindow'):
            self.assertNotIn(forbidden, source)

    def test_foreign_preservation_precedes_explicit_confirmed_migration(self):
        source = (ROOT / "scripts/smoke_installer.py").read_text()
        self.assertLess(source.index('uninstall("uninstall-preserves-portable-owner")'),
                        source.index('"install-for-confirmed-repair"'))
        self.assertLess(source.index('confirm=False'), source.index('confirm=True'))
        self.assertIn('"Silent Setup invoked interactive owner repair"', source)
        self.assertIn('uninstall("uninstall-confirmed-repair")', source)

if __name__ == "__main__":
    unittest.main()
