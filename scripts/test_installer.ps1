[CmdletBinding()]
param([Parameter(Mandatory = $true)][string]$Installer)
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Push-Location $Root
try {
    & ./.venv-build/Scripts/python.exe scripts/smoke_installer.py --installer $Installer
    if ($LASTEXITCODE -ne 0) { throw 'Windows installer smoke failed. See build/installer-smoke/report.json and logs.' }
} finally { Pop-Location }
