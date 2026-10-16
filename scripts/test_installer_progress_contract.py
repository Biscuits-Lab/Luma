"""Portable regression checks for the real native-progress observer."""
from pathlib import Path
import unittest

from smoke_installer_progress import progress_complete


class ProgressObserverContracts(unittest.TestCase):
    def test_completion_requires_engine_and_native_control(self):
        row = {"current": 969, "maximum": 969}
        self.assertTrue(progress_complete(row, 65535, 0, 65535))
        self.assertFalse(progress_complete(row, 65534, 0, 65535))
        self.assertFalse(progress_complete({"current": 968, "maximum": 969}, 65535, 0, 65535))
        self.assertFalse(progress_complete({"current": 0, "maximum": 0}, 65535, 0, 65535))

    def test_invalid_native_range_and_value_still_fail_with_evidence(self):
        for position, low, high in ((0, 0, 0), (20, 0, 100), (10, 1, 65535), (65536, 0, 65535)):
            with self.subTest(position=position, low=low, high=high):
                with self.assertRaisesRegex(AssertionError, "position=.*minimum=.*maximum="):
                    progress_complete({"current": 969, "maximum": 969}, position, low, high)

    def test_completed_page_is_not_sampled_but_exit_and_real_capture_required(self):
        source = Path(__file__).with_name("smoke_installer_progress.py").read_text()
        self.assertIn("if rows and not observed_complete:", source)
        self.assertIn("if code.value != 0:", source)
        self.assertIn("if not captured:", source)
        self.assertIn("positions != sorted(positions)", source)
        self.assertIn('report["engine_telemetry"] = progress_rows(log)', source)


if __name__ == "__main__":
    unittest.main()
