[CmdletBinding()]
param(
    [string]$PythonVersion = "3.12",
    [switch]$SkipTests,
    [switch]$DevelopmentOnly,
    [switch]$Clean
)
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
if ([System.Environment]::OSVersion.Platform -ne [System.PlatformID]::Win32NT) {
    throw "Windows builds must run on Windows. PyInstaller is not a cross-compiler."
}
$Root = Split-Path -Parent $PSScriptRoot
Push-Location $Root
try {
    if (!(Test-Path ".venv-build\Scripts\python.exe")) {
        & py "-$PythonVersion" -m venv .venv-build
        if ($LASTEXITCODE -ne 0) { throw "Python $PythonVersion (64-bit) is required. Install it from python.org." }
    }
    $Python = Join-Path $Root ".venv-build\Scripts\python.exe"
    & $Python -c "import sys, struct; assert sys.version_info >= (3,12); assert struct.calcsize('P') == 8, 'Use 64-bit Python'"
    if ($LASTEXITCODE -ne 0) { throw "Unsupported Python interpreter." }
    $EnvironmentHash = (@('requirements.txt', 'requirements-dev.txt', 'pyproject.toml') |
        ForEach-Object { (Get-FileHash -LiteralPath $_ -Algorithm SHA256).Hash }) -join ':'
    $EnvironmentStamp = Join-Path $Root '.venv-build/.luma-build-environment.sha256'
    $SavedEnvironment = if (Test-Path -LiteralPath $EnvironmentStamp) {
        (Get-Content -LiteralPath $EnvironmentStamp -Raw).Trim()
    } else { '' }
    $EnvironmentValid = $false
    if ($SavedEnvironment -eq $EnvironmentHash) {
        # A hash alone must not hide missing, broken or manually changed wheels.
        $EnvironmentCheck = @'
import importlib.metadata as metadata
import json, tomllib
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import url2pathname
from PySide6 import QtCore, QtGui, QtWidgets
import numpy, PIL.Image, OpenEXR, rawpy, PyInstaller
for filename in ('requirements.txt', 'requirements-dev.txt'):
    for line in Path(filename).read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if line and not line.startswith(('#', '-r')):
            name, version = line.split('==')
            assert metadata.version(name) == version, name
project = tomllib.loads(Path('pyproject.toml').read_text(encoding='utf-8'))['project']
dist = metadata.distribution(project['name'])
assert dist.version == project['version']
source = json.loads(dist.read_text('direct_url.json'))
assert source['dir_info']['editable']
assert Path(url2pathname(urlsplit(source['url']).path)).resolve() == Path.cwd().resolve()
'@
        try {
            & $Python -c $EnvironmentCheck 2>$null
            $EnvironmentValid = $LASTEXITCODE -eq 0
        } catch { $EnvironmentValid = $false }
    }
    if (!$EnvironmentValid) {
        & $Python -m pip install --only-binary=:all: -r requirements-dev.txt
        if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed. No source compilation fallback was attempted." }
        & $Python -m pip install --no-build-isolation --no-deps -e .
        if ($LASTEXITCODE -ne 0) { throw "Luma installation failed." }
        Set-Content -LiteralPath $EnvironmentStamp -Value $EnvironmentHash
    } else {
        Write-Host 'Reusing verified pinned build dependencies; no pip installation needed.'
    }
    if (!$SkipTests) {
        if ($DevelopmentOnly) {
            & $Python scripts/run_tests.py
            if ($LASTEXITCODE -ne 0) { throw "Offscreen source tests failed; development packaging stopped." }
        } else {
            $PreviousQtPlatform = $env:QT_QPA_PLATFORM
            $env:QT_QPA_PLATFORM = "windows"
            try {
                & $Python -m pytest
                if ($LASTEXITCODE -ne 0) { throw "Tests failed; packaging stopped." }
            } finally {
                if ($null -eq $PreviousQtPlatform) { Remove-Item Env:QT_QPA_PLATFORM -ErrorAction SilentlyContinue }
                else { $env:QT_QPA_PLATFORM = $PreviousQtPlatform }
            }
        }
    }
    & cmake -S native/thumbnail -B build/thumbnail -A x64 -DBUILD_TESTING=ON
    if ($LASTEXITCODE -ne 0) { throw "Native thumbnail configuration failed. Install Visual Studio C++ build tools and CMake." }
    & cmake --build build/thumbnail --config Release --parallel 2
    if ($LASTEXITCODE -ne 0) { throw "Native thumbnail build failed." }
    if (!$SkipTests) {
        & ctest --test-dir build/thumbnail -C Release --output-on-failure
        if ($LASTEXITCODE -ne 0) { throw "Native thumbnail contract tests failed." }
    }
    $LicenseArgs = @("scripts/collect_licenses.py", "--output", "build/licenses")
    if ($DevelopmentOnly) { $LicenseArgs += "--allow-incomplete" }
    & $Python @LicenseArgs
    if ($LASTEXITCODE -ne 0) { throw "License collection failed; see docs/BUILDING.md." }
    # Resolve wheel libraries and Windows system DLLs without unrelated tools'
    # PATH entries (for example Poppler's incompatible ICU ABI).
    $BuildPythonBase = (& $Python -c "import sys; print(sys.base_prefix)").Trim()
    if ($LASTEXITCODE -ne 0) { throw "Could not locate the build Python runtime." }
    $PreviousBuildPath = $env:PATH
    try {
        $env:PATH = @(
            (Join-Path $Root '.venv-build/Scripts'),
            $BuildPythonBase,
            (Join-Path $BuildPythonBase 'DLLs'),
            (Join-Path $env:SystemRoot 'System32'),
            $env:SystemRoot
        ) -join ';'
        $BundleArguments = @('-m', 'PyInstaller', '--noconfirm', 'Luma.spec')
        if (!$DevelopmentOnly -or $Clean) { $BundleArguments += '--clean' }
        & $Python @BundleArguments
        if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed." }
    } finally { $env:PATH = $PreviousBuildPath }
    Copy-Item build/thumbnail/Release/LumaThumbnail.dll dist/Luma/LumaThumbnail.dll
    & $Python scripts/verify_bundle.py dist/Luma
    if ($LASTEXITCODE -ne 0) { throw "Bundle verification failed." }
    if ($DevelopmentOnly) {
        "DEVELOPMENT ONLY: license/source fulfillment and Windows QA must be completed before redistribution." |
            Set-Content dist/Luma/DEVELOPMENT_ONLY.txt
    }
    Write-Host "Built: $Root\dist\Luma\Luma.exe"
    Write-Host "Keep the entire Luma folder. Complete docs/BUILDING.md's release checklist before redistribution."
} finally { Pop-Location }
