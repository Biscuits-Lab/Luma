"""One-off actual frozen 0.1.2 -> public 0.1.3 updater reproduction.

Ephemeral GitHub-hosted Windows only. Exercises the unmodified old GUI's real
check/download/install/launch buttons. This is intentionally pinned historical
reproduction, not a permanent latest-release gate or a candidate-update test.
No source-module replacement, TLS interception, synthetic acknowledgement,
installer execution on a user desktop, or update installation is hidden here.
"""
from __future__ import annotations

import argparse
import ctypes as C
from ctypes import wintypes as W
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request
from unittest.mock import patch

from smoke_installer import ARP, JOURNAL, registered_uninstaller
from smoke_installer_ui import Native, InstallerJob, WM_CLOSE

ROOT = Path(__file__).resolve().parents[1]
OLD_SETUP_NAME = "Luma-0.1.2-windows-x64-setup.exe"
OLD_SETUP_SIZE = 34552184
OLD_SETUP_SHA = "ce6902745fbc914663e6000f586798ee031f4e46b29133ffc2fe95e90cf547d6"
NEW_EXE_SHA = "4830949dbf2093b25281a59534e82b23ad3ca2e2a8a4545da951d690270527f7"
NEW_ZIP_SHA = "00f7cd8eaec6b46783a3d5a1e7fa547d638910ceac217eb181751d1417d9034f"
NEW_MANIFEST_SHA = "5317034cdd0d1244e44db588da57fcc75357b68b39aac9ff82501477b19d80ae"
BASE = "https://github.com/Biscuits-Lab/Luma/releases/download/"

# No external UI automation package. Inspect/invoke only an exact PID whose
# current executable path is checked again inside the native UIA observer.
UIA_SCRIPT = r'''
param([int]$TargetProcess, [string]$ExpectedExe, [string]$Operation, [string]$Target, [string]$SnapshotFile)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$p = Get-Process -Id $TargetProcess
if ([IO.Path]::GetFullPath($p.Path) -ine [IO.Path]::GetFullPath($ExpectedExe)) { throw 'Executable identity changed' }
if ($Operation -eq 'shortcut') {
    if (-not (Test-Path -LiteralPath $Target -PathType Leaf) -or [IO.Path]::GetExtension($Target) -ine '.lnk') {
      throw 'Missing test-owned shortcut'
    }
    $shell = New-Object -ComObject WScript.Shell
    @{ target=$shell.CreateShortcut($Target).TargetPath } | ConvertTo-Json -Compress
    exit
}
# .NET Framework UIA does not expose FullDescription (30159). Read only that
# native property at the owned control's point, verifying the same runtime ID
# and PID. This never moves the pointer, focuses a window, or sends input.
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
[StructLayout(LayoutKind.Sequential)] public struct LegacyUiaPoint { public int X; public int Y; }
[ComImport, Guid("30cbe57d-d9d0-452a-ab13-7ac5ac4825ee"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface ILegacyUia {
    void UnusedCompareElements(); void UnusedCompareRuntimeIds(); void UnusedGetRootElement();
    void UnusedElementFromHandle();
    ILegacyUiaElement ElementFromPoint(LegacyUiaPoint point);
}
[ComImport, Guid("d22108aa-8ac5-49a5-837b-37bbb3d7591e"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface ILegacyUiaElement {
    void UnusedSetFocus();
    [return: MarshalAs(UnmanagedType.SafeArray, SafeArraySubType=VarEnum.VT_I4)] int[] GetRuntimeId();
    void UnusedFindFirst(); void UnusedFindAll(); void UnusedFindFirstBuildCache();
    void UnusedFindAllBuildCache(); void UnusedBuildUpdatedCache();
    [return: MarshalAs(UnmanagedType.Struct)] object GetCurrentPropertyValue(int property);
}
public static class LegacyUiaDescription {
    public static string Read(int x, int y, int process, int[] expectedId) {
        ILegacyUia client = null; ILegacyUiaElement element = null;
        try {
            client = (ILegacyUia)Activator.CreateInstance(Type.GetTypeFromCLSID(new Guid("ff48dba4-60ef-4201-aa87-54103eef594e")));
            element = client.ElementFromPoint(new LegacyUiaPoint { X=x, Y=y });
            if (Convert.ToInt32(element.GetCurrentPropertyValue(30002)) != process) return "";
            int[] actualId = element.GetRuntimeId();
            if (actualId == null || expectedId == null || actualId.Length != expectedId.Length) return "";
            for (int i=0; i<actualId.Length; i++) if (actualId[i] != expectedId[i]) return "";
            return element.GetCurrentPropertyValue(30159) as string ?? "";
        } finally {
            if (element != null) Marshal.ReleaseComObject(element);
            if (client != null) Marshal.ReleaseComObject(client);
        }
    }
}
'@
$condition = [Windows.Automation.PropertyCondition]::new([Windows.Automation.AutomationElement]::ProcessIdProperty, $TargetProcess)
$roots = [Windows.Automation.AutomationElement]::RootElement.FindAll([Windows.Automation.TreeScope]::Children, $condition)
$elements = @()
foreach ($root in $roots) {
    $elements += $root
    $children = $root.FindAll([Windows.Automation.TreeScope]::Descendants, [Windows.Automation.Condition]::TrueCondition)
    if ($children.Count -gt 3000) { throw 'Unexpectedly large owned UI tree' }
    foreach ($child in $children) { $elements += $child }
}
$descriptions = @{}
foreach ($element in $elements) {
    if ($element.Current.ProcessId -eq $TargetProcess -and -not $element.Current.IsOffscreen -and
        $element.Current.ClassName -ceq 'QPushButton' -and [string]::IsNullOrEmpty($element.Current.Name)) {
        $rect = $element.Current.BoundingRectangle
        if ($rect.Width -gt 0 -and $rect.Height -gt 0) {
            $runtimeId = $element.GetRuntimeId()
            $descriptions[($runtimeId -join ',')] = [LegacyUiaDescription]::Read(
                [int]($rect.X + $rect.Width / 2), [int]($rect.Y + $rect.Height / 2), $TargetProcess, $runtimeId)
        }
    }
}
$rows = @($elements | ForEach-Object { @{
  name=$_.Current.Name; help=$_.Current.HelpText; id=$_.Current.AutomationId;
  class=$_.Current.ClassName; type=$_.Current.ControlType.ProgrammaticName;
  description=$descriptions[($_.GetRuntimeId() -join ',')];
  enabled=$_.Current.IsEnabled; offscreen=$_.Current.IsOffscreen; process=$_.Current.ProcessId
} })
$snapshot = @{ process=$TargetProcess; path=$p.Path; elements=$rows;
  modules=@($p.Modules | ForEach-Object { $_.FileName }) }
if ($SnapshotFile) { [IO.File]::WriteAllText($SnapshotFile, ($snapshot | ConvertTo-Json -Depth 6), [Text.UTF8Encoding]::new($false)) }
if ($Operation -eq 'invoke') {
    $names = switch ($Target) {
      'more' { @('Save As, copy, open and settings') }
      'settings' { @('Settings and updates…', 'Settings', '设置', '設定') }
      'check' { @('Check for updates') }
      'download' { @('Download and install update') }
      'launch' { @('Launch new version and close Luma') }
      default { throw 'Unknown bounded UI action' }
    }
    $matches = @($elements | Where-Object {
      $_.Current.ProcessId -eq $TargetProcess -and $_.Current.IsEnabled -and -not $_.Current.IsOffscreen -and
      (($names -ccontains $_.Current.Name) -or ($Target -eq 'more' -and (
        ($names -ccontains $_.Current.HelpText) -or
        ([string]::IsNullOrEmpty($_.Current.Name) -and $_.Current.ClassName -ceq 'QPushButton' -and
         $names -ccontains $descriptions[($_.GetRuntimeId() -join ',')])))) -and
      ($_.Current.ControlType -eq [Windows.Automation.ControlType]::Button -or
       $_.Current.ControlType -eq [Windows.Automation.ControlType]::MenuItem)
    })
    if ($matches.Count -ne 1) { throw "Expected one exact owned $Target action, found $($matches.Count)" }
    $pattern = $null
    if ($Target -eq 'more' -and $matches[0].TryGetCurrentPattern([Windows.Automation.ExpandCollapsePattern]::Pattern, [ref]$pattern)) {
      $pattern.Expand()
    } elseif ($matches[0].TryGetCurrentPattern([Windows.Automation.InvokePattern]::Pattern, [ref]$pattern)) {
      $pattern.Invoke()
    } else { throw 'Owned control exposes no supported accessible action' }
    @{ invoked=$Target; process=$TargetProcess; path=$p.Path } | ConvertTo-Json -Depth 4 -Compress
} else {
    $snapshot | ConvertTo-Json -Depth 6 -Compress
}
'''


def read_public(url: str, maximum: int) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "Luma-historical-updater-verification"})
    with urllib.request.urlopen(request, timeout=60) as response:
        data = response.read(maximum + 1)
    if len(data) > maximum:
        raise ValueError("Public response exceeds its bounded expected size")
    return data


def validate_old_setup(data: bytes) -> None:
    if len(data) != OLD_SETUP_SIZE or hashlib.sha256(data).hexdigest() != OLD_SETUP_SHA:
        raise ValueError("Old installer is not the exact publicly verified 0.1.2 artifact")


def visible_names(snapshot: dict) -> set[str]:
    return {row["name"] for row in snapshot["elements"] if not row["offscreen"]}


ACTION_NAMES = {
    "more": {"Save As, copy, open and settings"},
    "settings": {"Settings and updates…", "Settings", "设置", "設定"},
    "check": {"Check for updates"},
    "download": {"Download and install update"},
    "launch": {"Launch new version and close Luma"},
}


def action_ready(snapshot: dict, target: str) -> bool:
    """Match the same bounded native controls used by the invocation observer.

    Public 0.1.2 has one unnamed menu-bearing QPushButton. Its tooltip is Qt
    FullDescription, not HelpText, so the legacy .NET observer identifies that
    exact description, class and role combination. Never choose an arbitrary button.
    """
    matches = []
    for row in snapshot["elements"]:
        if (row["process"] != snapshot["process"] or not row["enabled"] or row["offscreen"]
                or row["type"] not in {"ControlType.Button", "ControlType.MenuItem"}):
            continue
        named = row["name"] in ACTION_NAMES[target]
        legacy_more = target == "more" and (
            row.get("help") in ACTION_NAMES[target] or (
                not row["name"] and row.get("class") == "QPushButton"
                and row.get("description") in ACTION_NAMES[target]))
        if named or legacy_more:
            matches.append(row)
    if len(matches) > 1:
        raise AssertionError(f"Ambiguous owned {target} controls: {len(matches)}")
    return len(matches) == 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-installer", type=Path, help="Optional cached exact official 0.1.2 Setup")
    args = parser.parse_args()
    if (sys.platform != "win32" or os.environ.get("GITHUB_ACTIONS") != "true"
            or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"):
        raise SystemExit("Historical apply/relaunch requires an ephemeral GitHub-hosted Windows runner")
    import winreg
    sys.path.insert(0, str(ROOT))
    from luma import shell_integration as shell
    output = ROOT / "build/update-legacy-012-013"
    output.mkdir(parents=True, exist_ok=True)
    report = {"passed": False, "scope": "unmodified public frozen 0.1.2 GUI -> public 0.1.3",
              "actions": [], "no_user_computer": True}
    work = Path(tempfile.mkdtemp(prefix="luma-legacy-update-", dir=os.environ["RUNNER_TEMP"]))
    install_root = work / "installed"
    updates = Path(os.environ["LOCALAPPDATA"]) / "Biscuits Lab/Luma/updates"
    script = output / "owned-uia.ps1"
    script.write_text(UIA_SCRIPT, encoding="utf-8-sig")
    shortcut = Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/Luma.lnk"
    desktop = Path(os.environ["USERPROFILE"]) / "Desktop/Luma.lnk"
    api, job, old_exe, new_exe = Native(), None, None, None

    def value(key, name="", root=winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(root, key, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as handle:
                return winreg.QueryValueEx(handle, name)
        except FileNotFoundError:
            return None

    def defaults():
        result = {}
        for extension in shell.EXTENSIONS:
            result[extension] = value("Software\\Classes\\" + extension)
            key = "Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\FileExts\\" + extension + r"\UserChoice"
            for name in ("ProgId", "Hash"):
                result[extension + name] = value(key, name)
        return result

    def uia(pid: int, executable: Path, operation="snapshot", target="") -> dict:
        command = ["powershell.exe", "-NoProfile", "-NonInteractive", "-File", str(script),
                   "-TargetProcess", str(pid), "-ExpectedExe", str(executable),
                   "-Operation", operation, "-Target", target,
                   "-SnapshotFile", str(output / f"last-{operation}-{target or 'tree'}.json")]
        completed = subprocess.run(command, capture_output=True, timeout=25, encoding="utf-8", errors="replace")
        if completed.returncode:
            raise AssertionError("Owned UIA action failed: " + completed.stderr[-4000:])
        return json.loads(completed.stdout.lstrip("\ufeff"))

    def wait_ui(pid: int, executable: Path, predicate, name: str, seconds=60) -> dict:
        deadline, last = time.monotonic() + seconds, None
        while time.monotonic() < deadline:
            last = uia(pid, executable)
            (output / f"{name}.json").write_text(json.dumps(last, indent=2, ensure_ascii=False), encoding="utf-8")
            if predicate(last):
                return last
            time.sleep(0.5)
        raise TimeoutError(f"Owned UI did not reach {name}; see its last snapshot")

    def invoke(pid, executable, target):
        # A visible top-level HWND does not prove the Qt accessibility tree is
        # ready. Save every bounded readiness snapshot before invoking once.
        wait_ui(pid, executable, lambda state: action_ready(state, target),
                f"before-{len(report['actions']):02d}-{target}")
        report["actions"].append(uia(pid, executable, "invoke", target))

    def process_path(pid: int) -> Path:
        handle = api.check(api.k.OpenProcess(0x1000, False, pid), "Inspect owned process")
        try:
            buffer, length = C.create_unicode_buffer(32768), W.DWORD(32768)
            function = api.k.QueryFullProcessImageNameW
            function.argtypes, function.restype = [C.c_void_p, W.DWORD, W.LPWSTR, C.POINTER(W.DWORD)], W.BOOL
            api.check(function(handle, 0, buffer, C.byref(length)), "Read owned process image")
            return Path(buffer.value).resolve(strict=True)
        finally:
            api.k.CloseHandle(handle)

    def wait_window(executable: Path):
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            for window in api.windows(job.job):
                if api.u.IsWindowVisible(window):
                    control = api.control(window)
                    if process_path(control["pid"]) == executable.resolve(strict=True):
                        return window, control["pid"]
            time.sleep(0.1)
        raise TimeoutError("No visible owned GUI from the expected executable")

    def capture_owned(window: int, destination: Path) -> None:
        # A newly visible native Qt HWND can precede its first painted frame.
        # Retry only that observed blank-frame condition; other errors and a
        # persistently blank image remain failures, never synthetic evidence.
        deadline = time.monotonic() + 10
        while True:
            try:
                api.screenshot(window, destination)
                return
            except AssertionError as error:
                if str(error) != "PrintWindow returned a blank image" or time.monotonic() >= deadline:
                    raise
                time.sleep(0.25)

    def inspect_version(executable: Path, expected: str, label: str) -> dict:
        window, pid = wait_window(executable)
        wait_ui(pid, executable, lambda state: action_ready(state, "more"), label + "-main-ready")
        capture_owned(window, output / f"{label}-before-settings.png")
        invoke(pid, executable, "more")
        wait_ui(pid, executable, lambda s: bool(visible_names(s) & {"Settings and updates…", "Settings", "设置", "設定"}), label + "-menu")
        invoke(pid, executable, "settings")
        snapshot = wait_ui(pid, executable, lambda s: bool(visible_names(s) & {expected, "Luma " + expected}), label + "-settings")
        modules = [Path(path).resolve(strict=True) for path in snapshot["modules"]
                   if Path(path).name.lower().startswith("python") or "pyside6" in path.lower() or Path(path).name == "Qt6Widgets.dll"]
        if not modules or not any(path.name.lower().startswith("python") for path in modules):
            raise AssertionError("No actual loaded Python runtime evidence")
        if any(not path.is_relative_to(executable.parent.resolve(strict=True)) for path in modules):
            raise AssertionError("Frozen GUI reused runtime modules from a different payload")
        capture_owned(window, output / f"{label}-main.png")
        return {"pid": pid, "executable": str(process_path(pid)), "embedded_ui_version": expected,
                "modules": [str(path) for path in modules], "snapshot": str(output / f"{label}-settings.json")}

    def close_owned_job(current):
        for window in api.windows(current.job):
            if api.u.IsWindowVisible(window):
                api.u.PostMessageW(window, WM_CLOSE, 0, 0)
        deadline = time.monotonic() + 20
        while current.active() and time.monotonic() < deadline:
            time.sleep(0.1)
        current.close()
        report.setdefault("process_cleanup_events", []).append({"forced": current.forced_cleanup})
        if current.forced_cleanup:
            raise AssertionError("Owned GUI did not exit normally before its private job closed")

    before_defaults = defaults()
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        if value(ARP, "DisplayName", root) is not None:
            raise SystemExit("Refusing to overwrite an existing managed Luma installation")
    if value(JOURNAL, "Manifest") is not None or shortcut.exists() or desktop.exists() or updates.exists():
        raise SystemExit("Refusing to touch preexisting Luma ownership, shortcuts or update data")
    try:
        latest = json.loads(read_public("https://api.github.com/repos/Biscuits-Lab/Luma/releases/latest", 2_000_000))
        if latest.get("id") != 415136172 or latest.get("tag_name") != "v0.1.3":
            raise RuntimeError("Historical reproduction requires latest to remain exact public 0.1.3; do not redirect old binary networking")
        manifest = read_public(BASE + "v0.1.3/SHA256SUMS", 292)
        if hashlib.sha256(manifest).hexdigest() != NEW_MANIFEST_SHA or NEW_ZIP_SHA.encode() not in manifest:
            raise ValueError("Public0.1.3 manifest changed")
        old_setup = args.old_installer or work / OLD_SETUP_NAME
        data = old_setup.read_bytes() if args.old_installer else read_public(BASE + "v0.1.2/" + OLD_SETUP_NAME, OLD_SETUP_SIZE)
        validate_old_setup(data)
        if not args.old_installer:
            old_setup.write_bytes(data)
        result = subprocess.run([str(old_setup.resolve(strict=True)), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
            "/SP-", "/LANG=en", "/TASKS=", f"/DIR={install_root}", f"/LOG={output / 'install012.log'}"], timeout=240)
        if result.returncode != 0 or value(ARP, "DisplayVersion")[0] != "0.1.2":
            raise AssertionError("Exact old installer did not install 0.1.2 successfully")
        old_exe, = install_root.glob("versions/*/Luma.exe")
        old_exe = old_exe.resolve(strict=True)
        shortcut_before = shortcut.read_bytes()
        job = InstallerJob(api, [str(old_exe)])
        report["before"] = inspect_version(old_exe, "0.1.2", "old")
        old_pid = report["before"]["pid"]
        invoke(old_pid, old_exe, "check")
        wait_ui(old_pid, old_exe, lambda s: any("Luma 0.1.3 is available" in text for text in visible_names(s)), "available")
        invoke(old_pid, old_exe, "download")
        wait_ui(old_pid, old_exe, lambda s: "Launch new version and close Luma" in visible_names(s), "downloaded", 240)
        candidates = list(updates.glob("Luma-0.1.3-*/Luma/Luma.exe"))
        if len(candidates) != 1 or hashlib.sha256(candidates[0].read_bytes()).hexdigest() != NEW_EXE_SHA:
            raise AssertionError("The real old updater did not produce the exact released new executable")
        new_exe = candidates[0].resolve(strict=True)
        invoke(old_pid, old_exe, "launch")
        report["after"] = inspect_version(new_exe, "0.1.3", "new")
        report["shortcut_unchanged"] = shortcut.read_bytes() == shortcut_before
        report["arp_version_after_portable_update"] = value(ARP, "DisplayVersion")[0]
        if not report["shortcut_unchanged"] or report["arp_version_after_portable_update"] != "0.1.2":
            raise AssertionError("Historical expected managed-entry behavior changed")
        if defaults() != before_defaults:
            raise AssertionError("Historical update changed Windows defaults")
        target = Path(uia(report["after"]["pid"], new_exe, "shortcut", str(shortcut))["target"]).resolve(strict=True)
        if target != old_exe.resolve(strict=True):
            raise AssertionError("Retained Start menu entry no longer targets the old managed payload")
        report["shortcut_target"] = str(target)
        close_owned_job(job)
        job = None
        # Launch exactly the target resolved from the real .lnk, without invoking
        # Explorer or a global Start menu. Record this scope honestly.
        job = InstallerJob(api, [str(target)])
        report["shortcut_target_relaunch"] = inspect_version(target, "0.1.2", "shortcut-old")
        report["shortcut_target_relaunch"]["scope"] = "resolved real Start-menu .lnk target, directly launched in private job"
        report["finding"] = "Immediate real launch runs 0.1.3; managed shortcut and Installed apps remain 0.1.2"
        report["passed"] = True
    except BaseException as error:
        report["error"] = f"{type(error).__name__}: {error}"
    finally:
        if job and job.job:
            try:
                close_owned_job(job)
            except Exception as error:
                report["process_cleanup_error"], report["passed"] = str(error), False
            report["forced_owned_process_cleanup"] = job.forced_cleanup
        try:
            # Never broadly clear registration; only these known test-owned EXEs.
            with patch.object(shell, "_runtime_guard", return_value=None):
                for executable in (new_exe, old_exe):
                    if executable is not None:
                        shell.uninstall_if_executable(executable)
            command = value(ARP, "UninstallString")
            if command:
                executable = registered_uninstaller(command[0], install_root)
                result = subprocess.run([str(executable), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
                    f"/LOG={output / 'uninstall012.log'}"], timeout=240)
                if result.returncode:
                    raise AssertionError(f"Old owned uninstaller returned {result.returncode}")
            if defaults() != before_defaults or value(JOURNAL, "Manifest") is not None or value(ARP, "DisplayName") is not None:
                raise AssertionError("Historical test did not restore isolated ownership/defaults")
            report["cleanup_passed"] = True
        except Exception as error:
            report["cleanup_error"], report["passed"] = f"{type(error).__name__}: {error}", False
        (output / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "report": str(output / "report.json")}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
