"""Portable source contracts for the fail-closed Inno binary-access probe.

These checks do not execute Pascal or Windows APIs. Native Inno compilation,
sharing/lock failures, and installer lifecycle verification remain required.
"""
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "packaging/windows/Luma.iss").read_text(encoding="utf-8")


def routine(name: str) -> str:
    start = re.search(rf"(?m)^(?:function|procedure) {re.escape(name)}\b", SOURCE)
    if start is None:
        raise AssertionError(f"Missing Pascal routine {name}")
    rest = SOURCE[start.start():]
    following = re.search(r"(?m)^(?:function|procedure) \w+\b", rest[1:])
    return rest if following is None else rest[:following.start() + 1]


def without_comments(text: str) -> str:
    # Preserve Pascal strings, including doubled apostrophes and {app} constants.
    tokens = re.compile(r"'(?:''|[^'])*'|\{[^}]*\}|\(\*.*?\*\)|//[^\n]*", re.S)
    return tokens.sub(lambda match: match[0] if match[0].startswith("'") else " ", text)


class InstallerBusyGuardTests(unittest.TestCase):
    def setUp(self):
        self.probe = without_comments(routine("LumaCanExclusivelyOpenBinary"))
        self.scan = without_comments(routine("FirstBusyBinaryWithBudget"))
        self.wrapper = without_comments(routine("FirstBusyBinary"))
        self.uninstall = without_comments(routine("CurUninstallStepChanged"))

    def test_native_signatures_keep_inno_67_handle_and_bool_widths(self):
        declaration = routine("LumaBusyCreateFile")
        self.assertIn("FileName: String; DesiredAccess, ShareMode: DWORD", declaration)
        self.assertIn("SecurityAttributes: DWORD_PTR", declaration)
        self.assertIn("TemplateFile: THandle): THandle", declaration)
        self.assertIn("external 'CreateFileW@kernel32.dll stdcall'", declaration)
        close = routine("LumaBusyCloseHandle")
        self.assertIn("(Handle: THandle): BOOL", close)
        self.assertIn("external 'CloseHandle@kernel32.dll stdcall'", close)

    def test_open_is_existing_read_write_exclusive_without_side_effect_flags(self):
        self.assertRegex(self.probe, r"ReadWriteAccess\s*=\s*\$C0000000;")
        self.assertRegex(self.probe, r"OpenExisting\s*=\s*3;")
        self.assertRegex(self.probe, r"NormalAttributes\s*=\s*\$80;")
        self.assertRegex(self.probe, r"LumaBusyCreateFile\(Path, ReadWriteAccess, 0, 0,\s*"
                                    r"OpenExisting, NormalAttributes, 0\)")

    def test_error_is_captured_before_logging_or_another_dll_call(self):
        self.assertRegex(self.probe, r"FileHandle := LumaBusyCreateFile\([^;]+;\s*"
                                    r"ErrorCode := DLLGetLastError;")
        self.assertNotRegex(self.probe, r"external ['\"]GetLastError@")

    def test_successful_handle_is_closed_before_reporting_success(self):
        self.assertRegex(self.probe, r"if FileHandle <> -1 then begin\s*"
                                    r"if not LumaBusyCloseHandle\(FileHandle\) then begin\s*"
                                    r"ErrorCode := DLLGetLastError;")
        close_failure = self.probe.split("if not LumaBusyCloseHandle", 1)[1].split("end;", 1)[0]
        self.assertIn("Exit;", close_failure)
        self.assertIn("Result := False;", self.probe)
        self.assertEqual(self.probe.count("Result := True;"), 1)
        self.assertLess(self.probe.index("LumaBusyCloseHandle(FileHandle)"),
                        self.probe.index("Result := True;"))

    def test_only_sharing_and_lock_errors_are_retryable(self):
        self.assertRegex(self.probe, r"if \(\(ErrorCode <> 32\) and \(ErrorCode <> 33\)\) "
                                    r"or \(RemainingRetries <= 0\) then Exit;\s*"
                                    r"RemainingRetries := RemainingRetries - 1;\s*"
                                    r"Sleep\(RetryDelayMilliseconds\);")
        self.assertEqual(self.probe.count("Sleep("), 1)
        self.assertNotRegex(self.probe, r"ErrorCode\s*(?:=|<>)\s*5\b")

    def test_wait_budget_is_initialized_once_for_the_whole_scan(self):
        self.assertEqual(self.wrapper.count("RemainingRetries := 20;"), 1)
        self.assertIn("FirstBusyBinaryWithBudget(Directory, RemainingRetries)", self.wrapper)
        self.assertNotIn("Sleep(", self.wrapper)
        self.assertNotIn("RemainingRetries :=", self.scan)
        self.assertEqual(self.probe.count("RemainingRetries :="), 1)
        self.assertIn("RetryDelayMilliseconds = 50;", self.probe)
        self.assertLessEqual(20 * 50, 1000)

    def test_nested_directories_and_files_share_the_same_budget(self):
        self.assertIn("var RemainingRetries: Integer", self.scan)
        self.assertIn("var RemainingRetries: Integer", self.probe)
        self.assertIn("Result := FirstBusyBinaryWithBudget(Path, RemainingRetries);", self.scan)
        self.assertIn("if not LumaCanExclusivelyOpenBinary(Path, RemainingRetries) then begin", self.scan)

    def test_reparse_rejection_happens_before_recursion_or_native_open(self):
        reject = self.scan.index("if (Entry.Attributes and $400) <> 0 then begin")
        recurse = self.scan.index("FirstBusyBinaryWithBudget(Path, RemainingRetries)")
        probe = self.scan.index("LumaCanExclusivelyOpenBinary(Path, RemainingRetries)")
        self.assertLess(reject, recurse)
        self.assertLess(reject, probe)
        rejection = self.scan[reject:].split("end;", 1)[0]
        self.assertIn("Result := Path;", rejection)
        self.assertIn("Exit;", rejection)

    def test_all_existing_binary_extensions_remain_checked(self):
        for extension in (".dll", ".exe", ".pyd"):
            self.assertIn(f"(Extension = '{extension}')", self.scan)
        self.assertIn("repeat", self.scan)
        self.assertIn("until not FindNext(Entry);", self.scan)
        self.assertRegex(self.scan, r"finally\s*FindClose\(Entry\);")
        failed_probe = self.scan.split("if not LumaCanExclusivelyOpenBinary", 1)[1].split("end;", 1)[0]
        self.assertIn("Result := Path;", failed_probe)
        self.assertIn("Exit;", failed_probe)

    def test_diagnostics_include_native_error_and_exact_path(self):
        self.assertIn("open_failed;win32_error=%d;attempt=%d;retries_remaining=%d;path=%s", self.probe)
        self.assertRegex(self.probe, r"\[\s*ErrorCode, Attempt, RemainingRetries, Path\]")
        self.assertIn("close_failed;win32_error=%d;path=%s", self.probe)
        self.assertIn("[ErrorCode, Path]", self.probe)

    def test_probe_only_runs_after_successful_waited_unregister(self):
        helper = self.uninstall.index("'--shell-unregister-if-owned'")
        wait = self.uninstall.index("ewWaitUntilTerminated", helper)
        checked = self.uninstall.index("if ResultCode <> 0 then", wait)
        probe = self.uninstall.index("BusyFile := FirstBusyBinary", checked)
        self.assertLess(helper, wait)
        self.assertLess(wait, checked)
        self.assertLess(checked, probe)
        self.assertIn("if CurUninstallStep <> usUninstall then Exit;", self.uninstall)
        self.assertIn("StopUninstall(FmtMessage(CustomMessage('UninstallBusy'), [BusyFile]));", self.uninstall)
        self.assertIn("Abort;", routine("StopUninstall"))

    def test_guard_does_not_write_delete_skip_or_kill(self):
        guard = self.probe + self.scan + self.wrapper
        self.assertNotRegex(guard, r"\b(?:WriteFile|DeleteFile|DeleteFileW|DeleteFileA|DelTree|"
                                   r"RemoveDirectory|TerminateProcess|TerminateJobObject|Exec|"
                                   r"ShellExec|SetEndOfFile|WriteBuffer|Continue)\s*(?:\(|;)")
        self.assertNotIn("TFileStream", guard)
        self.assertNotRegex(SOURCE, r"(?m)^\[(?:InstallDelete|UninstallDelete)\]")
        self.assertIn("CloseApplications=no", SOURCE)
        self.assertIn("RestartApplications=no", SOURCE)


if __name__ == "__main__":
    unittest.main()
