#requires -Version 7.2
# Run only on a disposable, isolated Windows x64 CI runner.
[CmdletBinding()]
param(
    [string]$RepoRoot = (Split-Path $PSScriptRoot -Parent),
    [string]$Bundle = "",
    [string]$Harness = "",
    [string]$Python = "python",
    [string]$Artifacts = "",
    [ValidateRange(5, 600)][int]$TimeoutSeconds = 60
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not $IsWindows -or
    [Runtime.InteropServices.RuntimeInformation]::ProcessArchitecture -ne
    [Runtime.InteropServices.Architecture]::X64) {
    throw "This smoke requires Windows and an x64 PowerShell process."
}
if ($env:CI -notmatch '^(?i:true|1)$' -and
    $env:GITHUB_ACTIONS -notmatch '^(?i:true|1)$') {
    throw "Disposable CI only: CI=true or GITHUB_ACTIONS=true is required."
}

$RepoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path
if (-not $Bundle) { $Bundle = Join-Path $RepoRoot "dist/Luma" }
if (-not $Harness) {
    $Harness = Join-Path $RepoRoot "build/thumbnail/Release/thumbnail_tests.exe"
}
$Dll = Join-Path $Bundle "LumaThumbnail.dll"
foreach ($file in @($Dll, $Harness)) {
    if (-not [IO.File]::Exists($file)) { throw "Missing required binary: $file" }
}
$Dll = (Resolve-Path -LiteralPath $Dll).Path
$Harness = (Resolve-Path -LiteralPath $Harness).Path
$Python = (Get-Command $Python -CommandType Application -ErrorAction Stop).Source

$id = [guid]::NewGuid().ToString("N")
$work = Join-Path ([IO.Path]::GetTempPath()) "luma-thumb-ci-$id"
$fixtures = Join-Path $RepoRoot "build/testfixtures/luma-thumb-ci-$id"
if (-not $Artifacts) {
    $Artifacts = Join-Path $RepoRoot "build/thumbnail-artifacts"
}
$Artifacts = [IO.Path]::GetFullPath((Join-Path $Artifacts $id))
if (Test-Path -LiteralPath $Artifacts) { throw "Artifact directory already exists." }
[void][IO.Directory]::CreateDirectory($Artifacts)

$modes = [ordered]@{
    raster = "{A1D48846-7E23-4C76-B5A7-88FE510AEB06}"
    hdr    = "{5C7F845B-D0D3-4CC7-8A47-E240EBD08C56}"
    exr    = "{97144AB9-77CE-4B28-B32D-0A7A80DC57AA}"
    raw    = "{C9B8C1F5-D343-45D7-A5BA-44AA071D1D2B}"
}
$extensions = @{}
foreach ($mode in $modes.Keys) {
    $extensions[$mode] = ".luma-thumb-ci-$id-$mode"
}
$handlerId = "{E357FCCD-A995-4576-B01F-234630154E96}"
$applicationKey = "Software\Classes\Applications\Luma.exe"
$applicationExtension = ".luma-thumb-ci-$id-application"
$ownedKeys = [Collections.Generic.List[string]]::new()
$ownedDirs = [Collections.Generic.List[string]]::new()
$failures = [Collections.Generic.List[string]]::new()
$runs = [Collections.Generic.List[object]]::new()
$user = [Microsoft.Win32.RegistryKey]::OpenBaseKey(
    [Microsoft.Win32.RegistryHive]::CurrentUser,
    [Microsoft.Win32.RegistryView]::Registry64
)
$mutex = [Threading.Mutex]::new($false, "Local\LumaThumbnailCi")
$locked = $false

if (-not ("LumaThumbCi.Native" -as [type])) {
    Add-Type @'
using System;
using System.Runtime.InteropServices;
namespace LumaThumbCi {
    public static class Native {
        [DllImport("shell32.dll")]
        public static extern void SHChangeNotify(
            int eventId, uint flags, IntPtr item1, IntPtr item2);
    }
}
'@
}

function Notify-Associations {
    # SHCNE_ASSOCCHANGED, SHCNF_IDLIST; both item pointers must be null.
    [LumaThumbCi.Native]::SHChangeNotify(
        0x08000000, 0, [IntPtr]::Zero, [IntPtr]::Zero)
}

function New-OwnedKey([string]$Path) {
    $existing = $user.OpenSubKey($Path)
    if ($null -ne $existing) {
        $existing.Dispose()
        throw "Refusing to overwrite existing HKCU key: $Path"
    }
    $key = $user.CreateSubKey($Path)
    $ownedKeys.Add($Path)
    return $key
}

function Invoke-Captured(
    [string]$Label, [string]$Exe, [string[]]$Arguments, [int]$Seconds
) {
    $stdout = Join-Path $Artifacts "$Label.stdout.log"
    $stderr = Join-Path $Artifacts "$Label.stderr.log"
    $record = [ordered]@{
        label = $Label; executable = $Exe; arguments = $Arguments
        exit_code = $null; seconds = 0; status = "failed"; error = $null
    }
    $timer = [Diagnostics.Stopwatch]::StartNew()
    $process = $null
    try {
        # Arguments here are fixed switches or complete file paths, never
        # embedded command text. Windows file paths cannot contain quotes.
        $quoted = ($Arguments | ForEach-Object { '"' + $_ + '"' }) -join " "
        $process = Start-Process -FilePath $Exe -ArgumentList $quoted `
            -WorkingDirectory $RepoRoot -NoNewWindow -PassThru `
            -RedirectStandardOutput $stdout -RedirectStandardError $stderr
        $null = $process.Handle
        if (-not $process.WaitForExit($Seconds * 1000)) {
            throw "$Label timed out after $Seconds seconds."
        }
        $record.exit_code = $process.ExitCode
        if ($null -eq $process.ExitCode -or $process.ExitCode -ne 0) {
            throw "$Label failed; exit code: $($process.ExitCode)."
        }
        $record.status = "passed"
    }
    catch {
        $record.error = $_.Exception.Message
        foreach ($log in @($stdout, $stderr)) {
            if (Test-Path -LiteralPath $log) {
                Write-Host "=== $log ==="
                Get-Content -LiteralPath $log | ForEach-Object { Write-Host $_ }
            }
        }
        throw
    }
    finally {
        try {
            if ($null -ne $process -and -not $process.HasExited) {
                # Only this launched process tree; never kill shared dllhost.
                $process.Kill($true)
                if (-not $process.WaitForExit(5000)) {
                    throw "Unable to terminate timed-out process: $Label"
                }
            }
        }
        finally {
            $record.seconds = [math]::Round($timer.Elapsed.TotalSeconds, 3)
            $runs.Add([pscustomobject]$record)
            if ($null -ne $process) { $process.Dispose() }
        }
    }
}

try {
    try { $locked = $mutex.WaitOne(0) }
    catch [Threading.AbandonedMutexException] { $locked = $true }
    if (-not $locked) { throw "Another thumbnail CI smoke is running." }

    # Check both hives and both views before touching any registration.
    $paths = @()
    foreach ($mode in $modes.Keys) {
        $paths += "Software\Classes\CLSID\$($modes[$mode])"
        $paths += "Software\Classes\$($extensions[$mode])"
    }
    $paths += $applicationKey
    $paths += "Software\Classes\$applicationExtension"
    foreach ($hive in @(
        [Microsoft.Win32.RegistryHive]::CurrentUser,
        [Microsoft.Win32.RegistryHive]::LocalMachine
    )) {
        foreach ($view in @(
            [Microsoft.Win32.RegistryView]::Registry64,
            [Microsoft.Win32.RegistryView]::Registry32
        )) {
            $base = [Microsoft.Win32.RegistryKey]::OpenBaseKey($hive, $view)
            try {
                foreach ($path in $paths) {
                    $existing = $base.OpenSubKey($path)
                    if ($null -ne $existing) {
                        $existing.Dispose()
                        throw "Existing registration: $hive/$view/$path"
                    }
                }
            }
            finally { $base.Dispose() }
        }
    }

    foreach ($dir in @($work, $fixtures)) {
        if (Test-Path -LiteralPath $dir) { throw "Working directory exists: $dir" }
        [void][IO.Directory]::CreateDirectory($dir)
        $ownedDirs.Add($dir)
    }

    $prepare = Join-Path $work "prepare.py"
    $manifestPath = Join-Path $Artifacts "fixture-manifest.json"
    @'
import hashlib
import json
from pathlib import Path
import struct
import sys
import zlib

root, destination, manifest = map(Path, sys.argv[1:])
sys.path.insert(0, str(root))
from scripts.smoke_bundle import prepare_fixtures, prepare_hdr_thumbnail_fixtures, RAW_BLOB, RAW_NAME, RAW_URL

expected_raw = "e03d193927a604de40fd0fd7a71e33fde878a0e1"
if RAW_BLOB != expected_raw:
    raise RuntimeError("RAW fixture pin changed; review before accepting it")
files = prepare_fixtures(destination, include_raw=True)
files += prepare_hdr_thumbnail_fixtures(destination)
# The content-aware executable association must cover common JPEG as well.
from PIL import Image
jpeg = destination / "common-image.jpg"
Image.new("RGB", (80, 40), (180, 90, 30)).save(jpeg)
files.append(jpeg)
suffix_modes = {
    ".png": "raster", ".jpg": "raster", ".tga": "raster", ".dds": "raster",
    ".hdr": "hdr", ".exr": "exr", ".nef": "raw",
}
required = {"smoke.png", "smoke.hdr", "smoke.exr", RAW_NAME}
if not required.issubset({p.name for p in files}):
    raise RuntimeError("Missing required PNG/HDR/EXR/real-NEF fixtures")
if not any(any(ord(c) > 127 for c in p.name) for p in files):
    raise RuntimeError("Missing Unicode filename fixture")

def chunk(kind, data):
    return (struct.pack(">I", len(data)) + kind + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff))

# PNG color type 6: one straight RGBA pixel, exactly (255, 0, 0, 128).
alpha = destination / "alpha.png"
png = (
    b"\x89PNG\r\n\x1a\n"
    + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
    + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00\x80"))
    + chunk(b"IEND", b"")
)
alpha.write_bytes(png)

cases = []
for path in [alpha, *files]:
    if not path.is_file():
        raise RuntimeError(f"Missing fixture: {path}")
    mode = suffix_modes[path.suffix.lower()]  # Unknown formats fail, not skip.
    cases.append({
        "path": str(path.resolve()),
        "mode": mode,
        "expect_alpha": path == alpha,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    })
manifest.write_text(json.dumps({
    "raw_source": RAW_URL, "raw_git_blob": RAW_BLOB, "cases": cases,
}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
'@ | Set-Content -LiteralPath $prepare -Encoding utf8

    Invoke-Captured "prepare" $Python `
        @($prepare, $RepoRoot, $fixtures, $manifestPath) 180
    $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json

    foreach ($mode in $modes.Keys) {
        $root = New-OwnedKey "Software\Classes\CLSID\$($modes[$mode])"
        try {
            $server = $root.CreateSubKey("InprocServer32")
            try {
                $server.SetValue("", $Dll, [Microsoft.Win32.RegistryValueKind]::String)
                $server.SetValue("ThreadingModel", "Apartment",
                    [Microsoft.Win32.RegistryValueKind]::String)
            }
            finally { $server.Dispose() }
        }
        finally { $root.Dispose() }

        $root = New-OwnedKey "Software\Classes\$($extensions[$mode])"
        try {
            $handler = $root.CreateSubKey("shellex\$handlerId")
            try {
                $handler.SetValue("", $modes[$mode],
                    [Microsoft.Win32.RegistryValueKind]::String)
            }
            finally { $handler.Dispose() }
        }
        finally { $root.Dispose() }
    }
    # Exercise the real owned executable-association lookup as well as the
    # per-format fixture path. Only this disposable extension default is set;
    # no real extension default or UserChoice is touched.
    $root = New-OwnedKey $applicationKey
    try {
        $handler = $root.CreateSubKey("shellex\$handlerId")
        try {
            $handler.SetValue("", $modes["raster"],
                [Microsoft.Win32.RegistryValueKind]::String)
        }
        finally { $handler.Dispose() }
    }
    finally { $root.Dispose() }
    $root = New-OwnedKey "Software\Classes\$applicationExtension"
    try {
        $root.SetValue("", "Applications\Luma.exe", [Microsoft.Win32.RegistryValueKind]::String)
    }
    finally { $root.Dispose() }
    # Do not create DisableProcessIsolation, AppID overrides, or global mappings.
    Notify-Associations

    # A built-in PNG association distinguishes OS cache/surrogate availability
    # from a problem in our disposable extension/CLSID registration.
    try {
        Invoke-Captured "builtin-png-surrogate" $Harness `
            @("--surrogate", [string]$manifest.cases[0].path) $TimeoutSeconds
    }
    catch {
        $failures.Add("builtin-png-surrogate: $($_.Exception.Message)")
    }

    $index = 0
    foreach ($case in $manifest.cases) {
        $index++
        $source = Get-Item -LiteralPath $case.path
        $label = "{0:d2}-{1}" -f $index, $case.mode
        # Preserve Unicode stems while using the disposable mode association.
        $copy = Join-Path $work (
            "$index-$($source.BaseName)$($extensions[$case.mode])")
        Copy-Item -LiteralPath $source.FullName -Destination $copy

        $direct = @($Dll, [string]$case.mode, $copy)
        if ($case.expect_alpha) { $direct += "--expect-alpha" }

        # Keep direct GetThumbnail diagnostics separate from cache/surrogate
        # errors such as WTS_E_FAILEDEXTRACTION. Exercise both even if one fails.
        foreach ($phase in @("direct", "surrogate")) {
            $arguments = if ($phase -eq "direct") {
                $direct
            } else {
                @("--surrogate", $copy)
            }
            try {
                Invoke-Captured "$label-$phase" $Harness $arguments $TimeoutSeconds
            }
            catch {
                $failures.Add("$label-$phase`: $($_.Exception.Message)")
            }
        }
        $applicationCopy = Join-Path $work ("$index-$($source.BaseName)$applicationExtension")
        Copy-Item -LiteralPath $source.FullName -Destination $applicationCopy
        try {
            Invoke-Captured "$label-application-surrogate" $Harness `
                @("--surrogate", $applicationCopy) $TimeoutSeconds
        }
        catch {
            $failures.Add("$label-application-surrogate`: $($_.Exception.Message)")
        }
        foreach ($checked in @($source.FullName, $copy, $applicationCopy)) {
            if ((Get-FileHash -LiteralPath $checked -Algorithm SHA256).Hash -ne $case.sha256) {
                $failures.Add("$label`: source bytes changed: $checked")
            }
        }
    }
}
catch {
    $failures.Add($_.Exception.Message)
}
finally {
    # Only roots created after absence checks are eligible for removal.
    for ($i = $ownedKeys.Count - 1; $i -ge 0; $i--) {
        try { $user.DeleteSubKeyTree($ownedKeys[$i], $false) }
        catch { $failures.Add("Registry cleanup failed: $($_.Exception.Message)") }
    }
    if ($ownedKeys.Count) {
        try { Notify-Associations }
        catch { $failures.Add("Shell notification failed: $($_.Exception.Message)") }
    }
    foreach ($dir in $ownedDirs) {
        try { Remove-Item -LiteralPath $dir -Recurse -Force -ErrorAction Stop }
        catch { $failures.Add("Fixture cleanup failed: $($_.Exception.Message)") }
    }
    $user.Dispose()
    if ($locked) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}

[ordered]@{
    status = $(if ($failures.Count) { "failed" } else { "passed" })
    dll = $Dll
    harness = $Harness
    runs = @($runs.ToArray())
    errors = @($failures.ToArray())
} | ConvertTo-Json -Depth 8 |
    Set-Content -LiteralPath (Join-Path $Artifacts "result.json") -Encoding utf8

Write-Host "Thumbnail smoke artifacts: $Artifacts"
if ($failures.Count) { throw ($failures -join [Environment]::NewLine) }
