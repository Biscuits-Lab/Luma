"""Portable wiring contracts; native launcher and full lifecycle tests remain required."""
from pathlib import Path
import re
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / 'packaging/windows/Luma.iss').read_text(encoding='utf-8')
BUILD = (ROOT / 'scripts/build_installer.ps1').read_text(encoding='utf-8')


class InstallerLauncherContracts(unittest.TestCase):
    def test_exact_private_finalize_route_uses_current_payload_and_reports_errors(self):
        sys.path.insert(0, str(ROOT))
        from luma import __main__ as entry
        from luma.installer import InstallerError
        with patch('luma.installer.finalize_managed_uninstall', create=True) as finalize:
            self.assertEqual(entry.main(['--finalize-managed-uninstall']), 0)
            finalize.assert_called_once_with(Path(sys.executable))
        for error in (InstallerError('unsafe owner'), OSError('busy')):
            with patch('luma.installer.finalize_managed_uninstall', create=True, side_effect=error):
                self.assertEqual(entry.main(['--finalize-managed-uninstall']), 2)

    def test_root_entry_is_real_immutable_native_file(self):
        files = SOURCE.split('[Files]', 1)[1].split('[Icons]', 1)[0]
        root_entries = [line for line in files.splitlines() if 'DestDir: "{app}"' in line]
        self.assertEqual(len(root_entries), 2)
        self.assertTrue(any('DestName: "Luma.exe"' in line for line in root_entries))
        self.assertTrue(any('DestName: "launcher-owner.txt"' in line for line in root_entries))
        self.assertTrue(all('onlyifdoesntexist' in line for line in root_entries))
        self.assertNotIn('active-payload.txt', files)
        self.assertNotRegex(SOURCE, r'(?m)^\[UninstallDelete\]')

    def test_shortcuts_icon_and_finish_launch_use_root(self):
        self.assertIn('UninstallDisplayIcon={app}\\Luma.exe', SOURCE)
        entries = SOURCE.split('[Icons]', 1)[1].split('[Code]', 1)[0]
        self.assertEqual(entries.count('Filename: "{app}\\Luma.exe"'), 3)
        self.assertNotIn('Filename: "{app}\\versions', entries)
        self.assertEqual(entries.count('WorkingDir: "{app}"'), 2)

    def test_existing_launcher_requires_bounded_exact_owner_and_hash(self):
        guard = SOURCE.split('function ExistingLauncherFailure:', 1)[1].split('function PrepareToInstall', 1)[0]
        self.assertIn("'LUMA-LAUNCHER-1'#10", guard)
        self.assertIn('FileSize64(Owner, Size)', guard)
        self.assertIn('Size <> Length', guard)
        self.assertIn('GetSHA256OfFile(Launcher), Digest', guard)
        for path in ('Launcher', 'Owner', 'Pointer'):
            self.assertIn('HasReparseAncestor(' + path + ')', guard)
            self.assertIn('DirExists(' + path + ')', guard)
        self.assertIn("'0123456789abcdef'", guard)
        prepare = SOURCE.split('function PrepareToInstall', 1)[1].split('procedure CurPageChanged', 1)[0]
        self.assertLess(prepare.index('ExistingLauncherFailure'), prepare.index('AddPayloadHashes'))

    def test_dynamic_state_is_finalized_only_after_root_and_payload_busy_checks(self):
        uninstall = SOURCE.split('procedure CurUninstallStepChanged', 1)[1]
        root = uninstall.index('LumaCanExclusivelyOpenBinary(Launcher, RemainingRetries)')
        payload = uninstall.index("FirstBusyBinaryWithBudget(ExpandConstant('{app}\\versions'), RemainingRetries)")
        finalize = uninstall.index("'--finalize-managed-uninstall'")
        self.assertLess(root, payload)
        self.assertLess(payload, finalize)
        self.assertIn('ewWaitUntilTerminated', uninstall[finalize:])
        self.assertIn('if ResultCode <> 0 then', uninstall[finalize:])
        self.assertEqual(uninstall.count('RemainingRetries := 200;'), 1)

    def test_finalizer_keeps_pointer_until_retained_post_helper_guard(self):
        uninstall = SOURCE.split('procedure CurUninstallStepChanged', 1)[1]
        self.assertLess(uninstall.index("'--finalize-managed-uninstall'"), uninstall.index('CommitLumaUninstall(RemainingRetries);'))
        commit = SOURCE.split('procedure CommitLumaUninstall(var RemainingRetries: Integer);', 1)[1].split('procedure DeinitializeUninstall', 1)[0]
        self.assertNotIn('RemainingRetries :=', commit)
        self.assertIn('Lines.Count <> 6', commit)
        self.assertIn('(Size > 512)', commit)
        self.assertIn("'LUMA-UNINSTALL-1'", commit)
        self.assertIn("'Local\\LumaManagedRoot-'", commit)
        self.assertIn('LumaWaitForSingleObject(MutexHandle, 0)', commit)
        self.assertIn('LumaUninstallMutex := MutexHandle;', commit)
        self.assertLess(commit.index('LumaUninstallMutex := MutexHandle;'), commit.index('LumaHoldBinaryForDeletion('))
        self.assertLess(commit.index('LumaProtocolHashMatches(Launcher'), commit.index('LumaHoldBinaryForDeletion('))
        self.assertLess(commit.index('PinInstalledBinaries('), commit.index('DeleteFile(Ready)'))
        self.assertLess(commit.index('DeleteFile(Ready)'), commit.index('DeleteFile(Pending)'))
        self.assertLess(commit.index('DeleteFile(Pending)'), commit.index('DeleteFile(Pointer)'))
        self.assertEqual(commit.count('LumaProtocolHashMatches(Pointer'), 2)
        self.assertNotIn('Exec(', commit)

    def test_retained_handles_block_new_launches_and_allow_inno_delete(self):
        pin = SOURCE.split('function LumaHoldBinaryForDeletion', 1)[1].split('function PinInstalledBinaries', 1)[0]
        self.assertRegex(pin, r'LumaBusyCreateFile\(Path, LumaBusyReadWriteAccess, 4, 0,')
        self.assertIn('Count >= 16384', pin)
        self.assertIn('LumaDeletionHandles[Count] := FileHandle;', pin)
        self.assertNotIn('LumaBusyCloseHandle(', pin)
        self.assertIn('(ErrorCode <> 32) and (ErrorCode <> 33)', pin)
        self.assertIn('RemainingRetries := RemainingRetries - 1;', pin)
        cleanup = SOURCE.split('procedure ReleaseLumaDeletionFence;', 1)[1].split('function LumaHoldBinaryForDeletion', 1)[0]
        self.assertIn('LumaBusyCloseHandle(LumaDeletionHandles[I])', cleanup)
        self.assertIn('LumaReleaseMutex(LumaUninstallMutex)', cleanup)
        self.assertIn('procedure DeinitializeUninstall;\nbegin\n  ReleaseLumaDeletionFence;', SOURCE)
        self.assertIn('procedure StopUninstall(const Reason: String);\nbegin\n  ReleaseLumaDeletionFence;', SOURCE)

    def test_build_tests_native_launcher_and_generates_exact_owner(self):
        self.assertIn('& ./scripts/build_launcher.ps1', BUILD)
        self.assertNotIn('build_launcher.ps1 -SkipTests', BUILD)
        self.assertIn('build/launcher/Release/Luma.exe', BUILD)
        self.assertIn('LUMA-LAUNCHER-1`n$LauncherHash`n', BUILD)
        self.assertIn('[Text.Encoding]::ASCII', BUILD)
        self.assertIn('"/DLauncherFile=$LauncherFile"', BUILD)
        self.assertIn('"/DLauncherOwnerFile=$LauncherOwnerFile"', BUILD)
        preflight = (ROOT / 'scripts/preflight_installer.py').read_text()
        self.assertIn('/DLauncherFile=', preflight)
        self.assertIn('/DLauncherOwnerFile=', preflight)
        self.assertIn('compile-only launcher placeholder; not executable', preflight)
        self.assertIn('"/O-"', preflight)


if __name__ == '__main__':
    unittest.main()
