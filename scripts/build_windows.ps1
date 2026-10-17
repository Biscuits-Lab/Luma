[CmdletBinding()]
param(
    [string]$PythonVersion = "3.12",
    [switch]$SkipTests,
    [switch]$DevelopmentOnly
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
    & $Python -m pip install --only-binary=:all: -r requirements-dev.txt
    if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed. No source compilation fallback was attempted." }
    & $Python -m pip install --no-build-isolation --no-deps -e .
    if ($LASTEXITCODE -ne 0) { throw "Luma installation failed." }
    if (!$SkipTests) {
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
        & $Python -m PyInstaller --clean --noconfirm Luma.spec
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
