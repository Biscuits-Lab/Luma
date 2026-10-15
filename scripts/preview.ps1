[CmdletBinding()]
param([Parameter(ValueFromRemainingArguments = $true)][string[]]$ImagePaths)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Push-Location $Root
try {
    if (!(Test-Path ".venv\Scripts\python.exe")) {
        & py -3.12 -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw "Install 64-bit Python 3.12 from python.org, then run this launcher again." }
    }
    $Python = Join-Path $Root ".venv\Scripts\python.exe"
    $Hash = (Get-FileHash requirements.txt -Algorithm SHA256).Hash
    $Stamp = ".venv\.luma-requirements.sha256"
    $Saved = if (Test-Path $Stamp) { (Get-Content $Stamp -Raw).Trim() } else { "" }
    if ($Hash -ne $Saved) {
        & $Python -m pip install --only-binary=:all: -r requirements.txt
        if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }
        Set-Content $Stamp $Hash
    }
    & $Python -m luma @ImagePaths
    if ($LASTEXITCODE -ne 0) { throw "Luma exited with code $LASTEXITCODE." }
} finally { Pop-Location }
