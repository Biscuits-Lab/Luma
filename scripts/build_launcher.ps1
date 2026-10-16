[CmdletBinding()]
param([switch]$SkipTests)
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    throw "Build and validate the native Luma launcher on Windows with MSVC x64."
}
$Root = Split-Path -Parent $PSScriptRoot
Push-Location $Root
try {
    $Python = Join-Path $Root ".venv-build/Scripts/python.exe"
    if (!(Test-Path -LiteralPath $Python)) { throw "Run scripts/build_windows.ps1 first to prepare Python 3.12." }
    & cmake -S native/launcher -B build/launcher -A x64 -DBUILD_TESTING=ON "-DPython3_EXECUTABLE=$Python"
    if ($LASTEXITCODE -ne 0) { throw "Native launcher configuration failed." }
    & cmake --build build/launcher --config Release --parallel 2
    if ($LASTEXITCODE -ne 0) { throw "Native launcher build failed." }
    if (!$SkipTests) {
        & ctest --test-dir build/launcher -C Release --output-on-failure
        if ($LASTEXITCODE -ne 0) { throw "Native launcher safety and process-identity tests failed." }
    }
    Write-Host "Built stable installed entry: $Root\build\launcher\Release\Luma.exe"
} finally { Pop-Location }
