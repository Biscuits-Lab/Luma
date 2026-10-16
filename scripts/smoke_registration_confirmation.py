"""Exercise the real packaged migration confirmation in an isolated lifecycle."""
from __future__ import annotations

import ctypes as C
from ctypes import wintypes as W
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from smoke_installer_ui import Native, InstallerJob

# Fixed, owned-process UI Automation actions. No global keystrokes or input.
PROBE = r'''
param([int]$TargetProcess, [string]$ExpectedExe, [string]$Operation)
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding($false)
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$p=Get-Process -Id $TargetProcess
if ([IO.Path]::GetFullPath($p.Path) -ine [IO.Path]::GetFullPath($ExpectedExe)) { throw 'Process identity changed' }
$condition=[Windows.Automation.PropertyCondition]::new([Windows.Automation.AutomationElement]::ProcessIdProperty,$TargetProcess)
$roots=[Windows.Automation.AutomationElement]::RootElement.FindAll([Windows.Automation.TreeScope]::Children,$condition)
$all=@()
foreach ($root in $roots) {
  $children=$root.FindAll([Windows.Automation.TreeScope]::Descendants,[Windows.Automation.Condition]::TrueCondition)
  if ($children.Count -gt 500) { throw 'Unexpected confirmation tree size' }
  foreach ($child in $children) { $all+=$child }
}
if ($Operation -eq 'yes') {
  $buttons=@($all | Where-Object { $_.Current.ProcessId -eq $TargetProcess -and
    $_.Current.ControlType -eq [Windows.Automation.ControlType]::Button -and
    $_.Current.IsEnabled -and -not $_.Current.IsOffscreen -and
    @('Yes','是') -ccontains $_.Current.Name })
  if ($buttons.Count -ne 1) { throw 'Expected exactly one owned localized Yes button' }
  $pattern=$null
  if (-not $buttons[0].TryGetCurrentPattern([Windows.Automation.InvokePattern]::Pattern,[ref]$pattern)) { throw 'Yes action unavailable' }
  $pattern.Invoke()
  @{invoked='Yes';process=$TargetProcess;path=$p.Path} | ConvertTo-Json -Compress
} elseif ($Operation -eq 'snapshot') {
  $rows=@($all | ForEach-Object { @{name=$_.Current.Name;type=$_.Current.ControlType.ProgrammaticName;
    enabled=$_.Current.IsEnabled;offscreen=$_.Current.IsOffscreen;focused=$_.Current.HasKeyboardFocus} })
  @{process=$TargetProcess;path=$p.Path;elements=$rows} | ConvertTo-Json -Depth 5 -Compress
} else { throw 'Unknown confirmation action' }
'''


def exercise_confirmation(executable: Path, previous: Path, output: Path, *, confirm: bool) -> dict:
    if (sys.platform != "win32" or os.environ.get("GITHUB_ACTIONS") != "true"
            or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"):
        raise RuntimeError("Native confirmation test requires an ephemeral GitHub-hosted Windows runner")
    from luma import registration_repair
    executable, previous = executable.resolve(strict=True), previous.resolve(strict=True)
    script = output.with_suffix(".ps1")
    script.write_text(PROBE, encoding="utf-8-sig")
    row = {"passed": False, "confirmation": confirm, "executable": str(executable), "previous": str(previous)}
    api, job = Native(), None
    try:
        job = InstallerJob(api, [str(executable), "--shell-repair-registration"])
        titles = {registration_repair.TITLE, *(catalog[registration_repair.TITLE]
                  for catalog in registration_repair.TRANSLATIONS.values())}
        deadline, window = time.monotonic() + 60, None
        while time.monotonic() < deadline and job.active():
            found = [hwnd for hwnd in api.windows(job.job) if api.u.IsWindowVisible(hwnd) and api.text(hwnd) in titles]
            if len(found) == 1:
                window = found[0]
                break
            time.sleep(0.05)
        if window is None:
            raise AssertionError("Real packaged helper did not display its eligible migration confirmation")
        row["window"] = api.control(window)
        pid = row["window"]["pid"]
        def probe(operation):
            result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-File", str(script),
                "-TargetProcess", str(pid), "-ExpectedExe", str(executable), "-Operation", operation],
                capture_output=True, timeout=25, encoding="utf-8", errors="replace")
            if result.returncode:
                raise AssertionError("Owned confirmation UIA failed: " + result.stderr[-3000:])
            return json.loads(result.stdout.lstrip("\ufeff"))
        snapshot = probe("snapshot")
        row["uia"] = snapshot
        text = "\n".join(element["name"] for element in snapshot["elements"] if not element["offscreen"]).casefold()
        if str(previous).casefold() not in text or str(executable).casefold() not in text:
            raise AssertionError("Confirmation does not display both exact ownership paths")
        names = {element["name"] for element in snapshot["elements"]
                 if not element["offscreen"] and element["type"] == "ControlType.Button"}
        if not names & {"No", "否"} or not names & {"Yes", "是"}:
            raise AssertionError("Confirmation does not expose localized Yes and No controls")
        screenshot = output.with_suffix(".png")
        api.screenshot(window, screenshot)
        row["screenshot"] = str(screenshot)
        if confirm:
            row["action"] = probe("yes")
        else:
            # Deliver Return only to this exact owned modal dialog. Exit1 plus
            # unchanged journal verifies the real default action is No.
            api.check(api.u.PostMessageW(window, 0x0100, 0x0D, 0x001C0001), "Press Return in owned confirmation")
            api.check(api.u.PostMessageW(window, 0x0101, 0x0D, 0xC01C0001), "Release Return in owned confirmation")
            row["action"] = "Return on owned dialog; expect default No"
        deadline = time.monotonic() + 60
        while job.active() and time.monotonic() < deadline:
            time.sleep(0.05)
        if job.active():
            raise TimeoutError("Packaged confirmation did not finish after its bounded action")
        code = W.DWORD()
        api.check(api.k.GetExitCodeProcess(job.process, C.byref(code)), "Read confirmation helper exit")
        row["exit_code"] = code.value
        if code.value != (0 if confirm else 1):
            raise AssertionError(f"Unexpected confirmation result: {code.value}")
        row["passed"] = True
        return row
    except BaseException as error:
        row["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        if job:
            job.close()
            row["forced_process_cleanup"] = job.forced_cleanup
            if job.forced_cleanup:
                row["passed"] = False
        output.write_text(json.dumps(row, indent=2, ensure_ascii=False), encoding="utf-8")
