"""Portable tests for the installer ownership boundary; no registry writes."""
import importlib.util
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
        self.assertIn("#if Ver < EncodeVer(6, 3, 0)", script)
        self.assertIn("#error Inno Setup 6.3 or newer is required", script)
        self.assertNotIn(".VersionInfo.FileVersion", build)

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
