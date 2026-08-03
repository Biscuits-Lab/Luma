<# Lumina Viewer preview bootstrap. Outputs to ./output. #>
[CmdletBinding()]
param(
    [switch]$Clean,
    [switch]$SkipInstall,
    [switch]$NoRun,
    [switch]$KeepOpen,
    [string]$Image,
    [string]$CoreUiRoot
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$ThirdParty = Join-Path $RepoRoot 'third_party'
$BuildDir = Join-Path $RepoRoot 'build'
$OutputDir = Join-Path $RepoRoot 'output'
$LogFile = Join-Path $OutputDir 'preview.log'
$CoreUiSource = if ($CoreUiRoot) { $CoreUiRoot } else { Join-Path $ThirdParty 'core-ui-src' }
$CoreUiSdk = Join-Path $ThirdParty 'core-ui-sdk'

function Init-Log {
    New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
    Set-Content -LiteralPath $LogFile -Value "Lumina preview log - $(Get-Date -Format o)" -Encoding UTF8
}
function Log([string]$Text) { Add-Content -LiteralPath $LogFile -Value $Text -Encoding UTF8 }
function Step([string]$Text) { Write-Host "`n==> $Text" -ForegroundColor Cyan; Log "`n==> $Text" }
function Ok([string]$Text) { Write-Host "[ok] $Text" -ForegroundColor Green; Log "[ok] $Text" }
function Warn([string]$Text) { Write-Host "[warn] $Text" -ForegroundColor Yellow; Log "[warn] $Text" }

function Run([string]$Exe, [string[]]$RunArgs, [string]$Fail, [string]$Cwd = '') {
    $line = "$Exe $($RunArgs -join ' ')"
    Log "> $line"
    if ($Cwd) { Push-Location $Cwd }
    try {
        $nativeOutput = & $Exe @RunArgs 2>&1
        $exitCode = $LASTEXITCODE
        if ($nativeOutput) {
            $nativeOutput | Tee-Object -FilePath $LogFile -Append | Out-Host
        }
        if ($exitCode -ne 0) { throw "$Fail Exit code: $exitCode" }
    } finally {
        if ($Cwd) { Pop-Location }
    }
}

function Cmd([string]$Name) {
    $command = Get-Command $Name -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($command) { return $command.Source }
    return $null
}

function Refresh-Path {
    $env:Path = ([Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User'))
}

function Winget-Install([string]$Id, [string]$Name, [string]$Override = '') {
    if ($SkipInstall) { throw "$Name is missing. Install manually or rerun without -SkipInstall." }
    $winget = Cmd 'winget.exe'
    if (-not $winget) { throw 'winget not found. Install dependencies manually or install App Installer from Microsoft Store.' }
    Step "Installing $Name with winget"
    $wingetArgs = @('install', '--id', $Id, '--exact', '--accept-package-agreements', '--accept-source-agreements')
    if ($Override) { $wingetArgs += @('--override', $Override) }
    Run $winget $wingetArgs "winget failed to install $Name."
    Refresh-Path
}

function Ensure-Git {
    Step 'Checking Git'
    $git = Cmd 'git.exe'
    if (-not $git) { Winget-Install 'Git.Git' 'Git for Windows'; $git = Cmd 'git.exe' }
    if (-not $git) { throw 'Git is unavailable.' }
    Run $git @('--version') 'git --version failed.'
    Ok "Git: $git"
    return $git
}

function Ensure-CMake {
    Step 'Checking CMake'
    $cmake = Cmd 'cmake.exe'
    if (-not $cmake) { Winget-Install 'Kitware.CMake' 'CMake'; $cmake = Cmd 'cmake.exe' }
    if (-not $cmake) { throw 'CMake is unavailable.' }
    Run $cmake @('--version') 'cmake --version failed.'
    Ok "CMake: $cmake"
    return $cmake
}

function VsWhere {
    $candidates = @("${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe", "$env:ProgramFiles\Microsoft Visual Studio\Installer\vswhere.exe")
    foreach ($candidate in $candidates) { if ($candidate -and (Test-Path $candidate)) { return $candidate } }
    return $null
}

function Ensure-VS {
    Step 'Checking Visual Studio C++ Build Tools'
    $vswhere = VsWhere
    $vs = $null
    if ($vswhere) { $vs = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath | Select-Object -First 1 }
    if (-not $vs) {
        Winget-Install 'Microsoft.VisualStudio.2022.BuildTools' 'Visual Studio 2022 Build Tools' '--wait --quiet --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended'
        $vswhere = VsWhere
        if ($vswhere) { $vs = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath | Select-Object -First 1 }
    }
    if (-not $vs) { throw 'Visual Studio C++ Build Tools unavailable.' }
    Ok "Visual Studio toolchain: $vs"
    return $vs
}

function Find-ClangCl {
    $clang = Cmd 'clang-cl.exe'
    if ($clang) { return $clang }
    $roots = @("$env:ProgramFiles\LLVM\bin\clang-cl.exe", "${env:ProgramFiles(x86)}\LLVM\bin\clang-cl.exe")
    foreach ($root in $roots) { if ($root -and (Test-Path $root)) { return $root } }
    return $null
}

function Ensure-LLVM {
    Step 'Checking LLVM clang-cl'
    $clang = Find-ClangCl
    if (-not $clang) {
        try { Winget-Install 'LLVM.LLVM' 'LLVM' } catch { Warn "LLVM auto-install failed: $($_.Exception.Message)" }
        $clang = Find-ClangCl
    }
    if ($clang) { Ok "clang-cl: $clang" } else { Warn 'clang-cl not found; core-ui build may fail.' }
    return $clang
}

function Is-CoreUiSdk([string]$Path) {
    return $Path -and (Test-Path (Join-Path $Path 'include\ui_core.h')) -and (Test-Path (Join-Path $Path 'lib\core-ui.lib')) -and (Test-Path (Join-Path $Path 'bin\core-ui.dll'))
}

function Download-CoreUiZip([string]$Dest) {
    Step 'Downloading core-ui zip fallback'
    $zip = Join-Path $ThirdParty 'core-ui-main.zip'
    $extract = Join-Path $ThirdParty 'core-ui-zip'
    if (Test-Path $zip) { Remove-Item -LiteralPath $zip -Force }
    if (Test-Path $extract) { Remove-Item -LiteralPath $extract -Recurse -Force }
    Invoke-WebRequest -Uri 'https://github.com/ghboke/core-ui/archive/refs/heads/main.zip' -OutFile $zip -UseBasicParsing
    Expand-Archive -LiteralPath $zip -DestinationPath $extract -Force
    $folder = Get-ChildItem -LiteralPath $extract -Directory | Select-Object -First 1
    if (-not $folder) { throw 'core-ui zip did not contain a folder.' }
    if (Test-Path $Dest) { Remove-Item -LiteralPath $Dest -Recurse -Force }
    Move-Item -LiteralPath $folder.FullName -Destination $Dest
}

function Package-CoreUi([string]$Source) {
    Step 'Packaging local core-ui SDK'
    New-Item -ItemType Directory -Force -Path (Join-Path $CoreUiSdk 'include'), (Join-Path $CoreUiSdk 'lib'), (Join-Path $CoreUiSdk 'bin') | Out-Null
    $header = Get-ChildItem -Path $Source -Filter ui_core.h -Recurse -File | Select-Object -First 1
    $lib = Get-ChildItem -Path $Source -Filter core-ui.lib -Recurse -File | Select-Object -First 1
    $dll = Get-ChildItem -Path $Source -Filter core-ui.dll -Recurse -File | Select-Object -First 1
    if (-not $header -or -not $lib -or -not $dll) { throw 'core-ui build completed but SDK files were not found.' }
    Copy-Item -LiteralPath $header.FullName -Destination (Join-Path $CoreUiSdk 'include\ui_core.h') -Force
    Copy-Item -LiteralPath $lib.FullName -Destination (Join-Path $CoreUiSdk 'lib\core-ui.lib') -Force
    Copy-Item -LiteralPath $dll.FullName -Destination (Join-Path $CoreUiSdk 'bin\core-ui.dll') -Force
    Ok "core-ui SDK: $CoreUiSdk"
    return $CoreUiSdk
}

function Ensure-CoreUi([string]$Git, [string]$CMake, [string]$VsPath, [string]$ClangPath) {
    Step 'Checking core-ui SDK'
    if (Is-CoreUiSdk $CoreUiSource) { Ok "core-ui SDK: $CoreUiSource"; return $CoreUiSource }
    if (Is-CoreUiSdk $CoreUiSdk) { Ok "core-ui SDK: $CoreUiSdk"; return $CoreUiSdk }
    New-Item -ItemType Directory -Force -Path $ThirdParty | Out-Null
    if (-not (Test-Path $CoreUiSource)) {
        Step 'Cloning core-ui'
        try { Run $Git @('clone', '--depth', '1', 'https://github.com/ghboke/core-ui.git', $CoreUiSource) 'Failed to clone core-ui.' }
        catch { Warn "Git clone failed: $($_.Exception.Message)"; Download-CoreUiZip $CoreUiSource }
    } else { Ok "core-ui source exists: $CoreUiSource" }

    $buildScript = Join-Path $CoreUiSource 'scripts\build-clang-cl.ps1'
    if (Test-Path $buildScript) {
        Step 'Building core-ui with upstream script'
        $buildArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $buildScript, '-Target', 'core-ui', '-VsPath', $VsPath)
        if ($ClangPath) { $buildArgs += @('-ClangClPath', $ClangPath) }
        Run 'powershell.exe' $buildArgs 'core-ui upstream build script failed.' $CoreUiSource
    } else {
        Step 'Building core-ui with CMake fallback'
        $coreBuild = Join-Path $CoreUiSource 'build'
        $command = "call `"$(Join-Path $VsPath 'Common7\Tools\VsDevCmd.bat')`" -arch=x64 -host_arch=x64 && `"$CMake`" -S `"$CoreUiSource`" -B `"$coreBuild`" -DCMAKE_BUILD_TYPE=Release && `"$CMake`" --build `"$coreBuild`" --config Release --target core-ui"
        Run 'cmd.exe' @('/d', '/s', '/c', $command) 'core-ui CMake build failed.'
    }
    return Package-CoreUi $CoreUiSource
}

function Build-Lumina([string]$CMake, [string]$VsPath, [string]$SdkRoot) {
    Step 'Building Lumina Viewer'
    if ($Clean -and (Test-Path $BuildDir)) { Remove-Item -LiteralPath $BuildDir -Recurse -Force }
    $vcvars = Join-Path $VsPath 'Common7\Tools\VsDevCmd.bat'
    $cmdFile = Join-Path $OutputDir 'build-lumina.cmd'
    $cmdLines = @(
        '@echo off',
        "call `"$vcvars`" -arch=x64 -host_arch=x64",
        "if errorlevel 1 exit /b %errorlevel%",
        "`"$CMake`" -S `"$RepoRoot`" -B `"$BuildDir`" -DCORE_UI_ROOT=`"$SdkRoot`" -DLUMINA_OUTPUT_DIR=`"$OutputDir`"",
        "if errorlevel 1 exit /b %errorlevel%",
        "`"$CMake`" --build `"$BuildDir`" --config Release",
        "if errorlevel 1 exit /b %errorlevel%"
    )
    Set-Content -LiteralPath $cmdFile -Value $cmdLines -Encoding ASCII
    Run 'cmd.exe' @('/d', '/c', $cmdFile) 'Lumina build failed.'
    $exe = Join-Path $OutputDir 'lumina-viewer.exe'
    if (-not (Test-Path $exe)) { throw 'Build finished but output\lumina-viewer.exe was not found.' }
    Ok "Lumina Viewer: $exe"
    return $exe
}

try {
    if ($Clean) {
        if (Test-Path $BuildDir) { Remove-Item -LiteralPath $BuildDir -Recurse -Force }
        if (Test-Path $OutputDir) { Remove-Item -LiteralPath $OutputDir -Recurse -Force }
    }
    Init-Log
    Step "Repository: $RepoRoot"
    Ok "Output folder: $OutputDir"
    $git = Ensure-Git
    $cmake = Ensure-CMake
    $vs = Ensure-VS
    $clang = Ensure-LLVM
    $sdk = Ensure-CoreUi $git $cmake $vs $clang
    $exe = Build-Lumina $cmake $vs $sdk
    if (-not $NoRun) {
        Step 'Launching preview'
        if ($Image) { Start-Process -FilePath $exe -ArgumentList @($Image) } else { Start-Process -FilePath $exe }
    }
    Ok 'Preview workflow completed.'
    Ok "Log: $LogFile"
    if ($KeepOpen) { Read-Host 'Press Enter to close' | Out-Null }
} catch {
    Write-Host "`n[error] $($_.Exception.Message)" -ForegroundColor Red
    if (Test-Path $OutputDir) { Add-Content -LiteralPath $LogFile -Value "[error] $($_.Exception.Message)" -Encoding UTF8; Write-Host "Log: $LogFile" -ForegroundColor Yellow }
    Write-Host 'Tip: check output\preview.log for the full command output.' -ForegroundColor Yellow
    if ($KeepOpen) { Read-Host 'Press Enter to close' | Out-Null }
    exit 1
}


