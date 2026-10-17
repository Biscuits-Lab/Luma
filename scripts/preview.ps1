[CmdletBinding()]
param(
    [switch]$CheckOnly,
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$ImagePaths
)
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot

function Test-LumaPython([string]$Executable) {
    if (!(Test-Path -LiteralPath $Executable -PathType Leaf)) { return $false }
    try {
        $ErrorActionPreference = 'Continue'
        & $Executable -c "import sys, struct; raise SystemExit(0 if sys.version_info >= (3,12) and struct.calcsize('P') == 8 else 1)" *> $null
        return $LASTEXITCODE -eq 0
    } catch { return $false }
}

function Test-LumaCommand([string]$Executable, [string[]]$Arguments) {
    $ErrorActionPreference = 'Continue'
    & $Executable @Arguments *> $null
    return $LASTEXITCODE -eq 0
}

Push-Location $Root
try {
    $Python = $null
    foreach ($Name in @('.venv', '.venv-preview', '.venv-build')) {
        $Candidate = Join-Path $Root "$Name\Scripts\python.exe"
        if (Test-LumaPython $Candidate) { $Python = $Candidate; break }
    }
    if (!$Python) {
        $Candidates = @()
        $Launcher = Get-Command py.exe -ErrorAction SilentlyContinue
        if ($Launcher) {
            try { $RuntimePaths = & $Launcher.Source -0p 2>$null }
            catch { $RuntimePaths = @() }
            foreach ($Line in $RuntimePaths) {
                if ("$Line" -match '(?i)([A-Z]:\\.*\\python(?:3)?\.exe)\s*$') {
                    $Candidates += $Matches[1].Trim()
                }
            }
        }
        foreach ($CommandName in @('python.exe', 'python3.exe')) {
            foreach ($Command in @(Get-Command $CommandName -All -ErrorAction SilentlyContinue)) {
                if ($Command.Source -and $Command.Source -notmatch '\\WindowsApps\\') {
                    $Candidates += $Command.Source
                }
            }
        }
        $Runtime = $Candidates | Select-Object -Unique | Where-Object { Test-LumaPython $_ } | Select-Object -First 1
        if (!$Runtime) {
            throw 'No compatible installed Python found. Luma needs 64-bit Python 3.12 or newer with wheels for requirements.txt. Install a compatible runtime yourself, then retry; no system changes were made.'
        }
        $EnvironmentPath = Join-Path $Root '.venv'
        if (Test-Path -LiteralPath $EnvironmentPath) {
            Write-Host 'Preserving the unusable .venv; using .venv-preview instead.'
            $EnvironmentPath = Join-Path $Root '.venv-preview'
            if (Test-Path -LiteralPath $EnvironmentPath) {
                throw "Both .venv and .venv-preview are unusable and were preserved. Repair or move one before retrying. Compatible installed runtime: $Runtime"
            }
        }
        Write-Host "Creating project environment with: $Runtime"
        & $Runtime -m venv $EnvironmentPath
        if ($LASTEXITCODE -ne 0) { throw 'Could not create the project virtual environment. See the Python error above.' }
        $Python = Join-Path $EnvironmentPath 'Scripts\python.exe'
    }
    Write-Host "Luma Python: $Python"
    & $Python -c "import sys, struct; print(sys.version.split()[0] + ' (' + str(struct.calcsize('P') * 8) + '-bit)')"
    if ($LASTEXITCODE -ne 0) { throw 'The selected interpreter could not start.' }
    if (!(Test-LumaCommand $Python @('-m', 'pip', '--version'))) {
        & $Python -m ensurepip --upgrade
        if ($LASTEXITCODE -ne 0) { throw 'pip is missing from the project environment and could not be restored.' }
    }
    $Hash = (Get-FileHash requirements.txt -Algorithm SHA256).Hash
    $Stamp = Join-Path (Split-Path -Parent (Split-Path -Parent $Python)) '.luma-requirements.sha256'
    $Saved = if (Test-Path -LiteralPath $Stamp) { (Get-Content -LiteralPath $Stamp -Raw).Trim() } else { '' }
    $ImportCheck = @'
import importlib.metadata as metadata
import struct, sys
from pathlib import Path
from PySide6 import QtCore, QtGui, QtWidgets
import numpy, PIL.Image, OpenEXR, rawpy
assert sys.version_info >= (3, 12) and struct.calcsize('P') == 8
for line in Path('requirements.txt').read_text(encoding='utf-8').splitlines():
    line = line.strip()
    if line and not line.startswith('#'):
        name, version = line.split('==')
        assert metadata.version(name) == version, name + ' does not match requirements.txt'
'@
    $ImportsValid = Test-LumaCommand $Python @('-c', $ImportCheck)
    if ($Hash -ne $Saved -or !$ImportsValid) {
        & $Python -m pip install --only-binary=:all: -r requirements.txt
        if ($LASTEXITCODE -ne 0) { throw 'Pinned dependency installation failed. A wheel may be unavailable for this interpreter; use 64-bit Python 3.12 as the baseline. No runtime was installed automatically.' }
        & $Python -c $ImportCheck
        if ($LASTEXITCODE -ne 0) { throw 'Dependency imports or pinned versions failed validation; Luma was not started.' }
        Set-Content -LiteralPath $Stamp -Value $Hash
    }
    if ($CheckOnly) {
        Write-Host 'Luma runtime and pinned dependency imports verified. CheckOnly: no GUI started.'
    } else {
        & $Python -m luma @ImagePaths
        if ($LASTEXITCODE -ne 0) { throw "Luma exited with code $LASTEXITCODE." }
    }
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
} finally { Pop-Location }
exit 0
