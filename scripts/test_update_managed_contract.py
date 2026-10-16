"""Portable checks for the disposable native updater integration gate."""
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import smoke_update_managed as smoke


class UpdateSmokeContractTests(unittest.TestCase):
    def test_historical_release_pin_is_exact_and_official(self):
        self.assertEqual(smoke.BASELINE_VERSION, "0.1.3")
        self.assertEqual(smoke.BASELINE_SIZE, 35761843)
        self.assertEqual(smoke.BASELINE_SHA256, "c6622670955db4e25c02ceaeb42d432e2b8add73bba1777bbba09188d0ea5f4f")
        self.assertEqual(smoke.BASELINE_URL, "https://github.com/Biscuits-Lab/Luma/releases/download/v0.1.3/" + smoke.BASELINE_NAME)
        self.assertEqual(smoke.CANDIDATE_VERSION, "0.1.4")

    def test_refuses_local_or_self_hosted_windows_and_nonwindows(self):
        for platform, actions, environment in (("linux", "true", "github-hosted"),
                                               ("win32", "false", "github-hosted"),
                                               ("win32", "true", "self-hosted"),
                                               ("win32", "true", "")):
            with self.subTest(platform=platform, actions=actions, environment=environment):
                with patch.object(smoke.sys, "platform", platform), patch.dict(os.environ, {
                        "GITHUB_ACTIONS": actions, "RUNNER_ENVIRONMENT": environment}):
                    with self.assertRaises(RuntimeError):
                        smoke.require_disposable_runner()

    def test_allows_only_exact_disposable_runner_class(self):
        with patch.object(smoke.sys, "platform", "win32"), patch.dict(os.environ, {
                "GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "github-hosted"}):
            smoke.require_disposable_runner()

    def test_main_guard_runs_before_input_or_output_mutation(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "must-not-be-created"
            with patch.object(smoke, "require_disposable_runner", side_effect=RuntimeError("denied")):
                with self.assertRaisesRegex(RuntimeError, "denied"):
                    smoke.main(["--installer", "missing.exe", "--checksums", "missing", "--output", str(output)])
            self.assertFalse(output.exists())

    def test_baseline_rejects_changed_size_or_hash_and_arbitrary_name(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / smoke.BASELINE_NAME
            path.write_bytes(b"test")
            with self.assertRaises(ValueError):
                smoke.check_baseline(path)
            with patch.object(smoke, "BASELINE_SIZE", 4), patch.object(smoke, "BASELINE_SHA256", hashlib.sha256(b"test").hexdigest()):
                self.assertEqual(smoke.check_baseline(path), path.resolve())
                foreign = path.with_name("unrelated.exe")
                path.rename(foreign)
                with self.assertRaises(ValueError):
                    smoke.check_baseline(foreign)

    def test_candidate_requires_explicit_exact_filename_and_manifest_digest(self):
        checksum_calls = []
        verify_calls = []
        fake_updater = types.SimpleNamespace(
            MAX_MANIFEST_BYTES=256 * 1024,
            checksum_for=lambda manifest, name: checksum_calls.append((manifest, name)) or "a" * 64,
            verify_checksum=lambda path, digest: verify_calls.append((path, digest)))
        with tempfile.TemporaryDirectory() as temporary, patch.dict(sys.modules, {"luma.updater": fake_updater}):
            path = Path(temporary) / "Luma-0.1.4-windows-x64-setup.exe"
            path.write_bytes(b"MZ-fixture")
            manifest = Path(temporary) / "SHA256SUMS"
            manifest.write_bytes(b"manifest fixture")
            self.assertEqual(smoke.check_candidate(path, manifest), (path.resolve(), "a" * 64))
            self.assertEqual(checksum_calls, [(b"manifest fixture", path.name)])
            self.assertEqual(verify_calls, [(path.resolve(), "a" * 64)])
            wrong = path.with_name("Luma-0.1.3-windows-x64-setup.exe")
            path.rename(wrong)
            with self.assertRaises(ValueError):
                smoke.check_candidate(wrong, manifest)

    def test_cleanup_is_exact_owned_uninstaller_and_captured_processes(self):
        source = (ROOT / "scripts/smoke_update_managed.py").read_text(encoding="utf-8")
        self.assertIn("registered_uninstaller(value[0], install_root)", source)
        self.assertIn("for purpose, process in reversed(captured):", source)
        self.assertIn('if "setup" in purpose and process.poll() is None:', source)
        self.assertIn("leave its files/state for disposable-runner teardown", source)
        self.assertNotIn("taskkill", source.casefold())
        self.assertNotIn("rmtree", source)
        self.assertNotIn("UserChoice\",", source)  # UserChoice is inspected, never a write target.
        self.assertIn("actual frozen GUI", smoke.__doc__)
        self.assertIn("NOT a test of the old frozen", smoke.__doc__)
        self.assertIn("wait_for_binary_release(install_root, timeout=10)", source)
        self.assertIn("acknowledgment.os_executable == updated.executable", source)
        self.assertIn('assert observed == ""', source)
        self.assertIn("restored == bundled_dll_directory", source)


if __name__ == "__main__":
    unittest.main()
