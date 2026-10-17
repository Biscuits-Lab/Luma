[CmdletBinding()]
param(
    [ValidateRange(1, 64)][int]$Jobs = 4,
    [switch]$Contracts,
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$TestArguments
)
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Push-Location $Root
try {
    $Python = $null
    foreach ($Name in @('.venv-build', '.venv', '.venv-preview')) {
        $Candidate = Join-Path $Root "$Name\Scripts\python.exe"
        if (Test-Path -LiteralPath $Candidate) {
            & $Candidate -c 'import pytest, PySide6, numpy, PIL, OpenEXR, rawpy' 2>$null
            if ($LASTEXITCODE -eq 0) { $Python = $Candidate; break }
        }
    }
    if (!$Python) {
        throw 'No project test environment found. Install requirements-dev.txt in .venv first.'
    }
    $RunnerArguments = @('scripts/run_tests.py', '--jobs', "$Jobs")
    if ($Contracts) { $RunnerArguments += '--contracts' }
    & $Python @RunnerArguments @TestArguments
    $Result = $LASTEXITCODE
} finally { Pop-Location }
exit $Result
