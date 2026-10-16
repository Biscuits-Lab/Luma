[CmdletBinding()]
param(
    [string]$BundleDir = "dist/Luma",
    [string]$OutputDir = "artifacts",
    [string]$IsccPath = "",
    [switch]$UiFontScaleTest
)
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
if ([System.Environment]::OSVersion.Platform -ne [System.PlatformID]::Win32NT) {
    throw "Build the Windows installer on Windows with Inno Setup 6.7 or newer."
}
$Root = Split-Path -Parent $PSScriptRoot
Push-Location $Root
try {
    $Python = Join-Path $Root ".venv-build/Scripts/python.exe"
    if (!(Test-Path -LiteralPath $Python)) { throw "Run scripts/build_windows.ps1 first." }
    $Bundle = (Resolve-Path -LiteralPath $BundleDir).Path
    foreach ($Relative in @("Luma.exe", "LumaThumbnail.dll", "_internal/licenses/manifest.json")) {
        if (!(Test-Path -LiteralPath (Join-Path $Bundle $Relative) -PathType Leaf)) {
            throw "Incomplete Windows bundle: missing $Relative"
        }
    }
    foreach ($Forbidden in @("DEVELOPMENT_ONLY.txt", "luma-install.json")) {
        if (Test-Path -LiteralPath (Join-Path $Bundle $Forbidden)) { throw "Do not package $Forbidden." }
    }
    $LicenseManifest = Get-Content -LiteralPath (Join-Path $Bundle '_internal/licenses/manifest.json') -Raw | ConvertFrom-Json
    if ($LicenseManifest.license_texts_complete -ne $true) { throw 'The bundle license collection is incomplete.' }
    & $Python scripts/verify_bundle.py $Bundle
    if ($LASTEXITCODE -ne 0) { throw "Bundle verification failed." }
    $Version = (& $Python -c "import pathlib,tomllib; from luma import __version__; v=tomllib.loads(pathlib.Path('pyproject.toml').read_text(encoding='utf-8'))['project']['version']; assert v == __version__; print(v)").Trim()
    if ($LASTEXITCODE -ne 0 -or $Version -notmatch '^\d+\.\d+\.\d+$') { throw "A matching stable source version is required." }
    if (!$IsccPath) {
        $Command = Get-Command ISCC.exe -ErrorAction SilentlyContinue
        if ($Command) { $IsccPath = $Command.Source }
        else { $IsccPath = Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6/ISCC.exe" }
    }
    if (!(Test-Path -LiteralPath $IsccPath -PathType Leaf)) {
        throw "Install Inno Setup 6.7+ from https://jrsoftware.org/isdl.php, then pass -IsccPath if needed. GitHub Windows runners normally include it."
    }
    & ./scripts/build_launcher.ps1
    if ($LASTEXITCODE -ne 0) { throw "Native stable launcher build/tests failed." }
    $LauncherFile = Join-Path $Root "build/launcher/Release/Luma.exe"
    if (!(Test-Path -LiteralPath $LauncherFile -PathType Leaf)) { throw "Native stable launcher was not produced." }
    if ((Get-Item -LiteralPath $LauncherFile).Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw "Native stable launcher must not be a linked file."
    }
    # Luma.iss checks ISPP Ver against EncodeVer(6, 7, 0). ISCC.exe can report
    # FileVersion 0.0.0.0 even when the actual compiler engine is current.
    New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
    $Artifacts = (Resolve-Path -LiteralPath $OutputDir).Path
    $Entries = @(Get-ChildItem -LiteralPath $Bundle -Recurse -Force)
    if (((Get-Item -LiteralPath $Bundle).Attributes -band [IO.FileAttributes]::ReparsePoint) -or
        ($Entries | Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint })) {
        throw "Installer input must not contain junctions or symbolic links."
    }
    [string[]]$Paths = @($Entries | Where-Object { !$_.PSIsContainer } | ForEach-Object { $_.FullName })
    [Array]::Sort($Paths, [StringComparer]::Ordinal)
    $ManifestLines = foreach ($Path in $Paths) {
        $Relative = $Path.Substring($Bundle.TrimEnd('\').Length + 1).Replace('\','/')
        "$( (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() )  $Relative"
    }
    $Hasher = [Security.Cryptography.SHA256]::Create()
    try {
        $Bytes = [Text.Encoding]::UTF8.GetBytes(($ManifestLines -join "`n") + "`n")
        $BundleHash = ([BitConverter]::ToString($Hasher.ComputeHash($Bytes))).Replace('-','').ToLowerInvariant()
    } finally { $Hasher.Dispose() }
    $PayloadId = "$Version-$($BundleHash.Substring(0,24))"
    $BuildDir = Join-Path $Root "build/installer/$PayloadId"
    New-Item -ItemType Directory -Force -Path $BuildDir | Out-Null
    $LauncherOwnerFile = Join-Path $BuildDir "launcher-owner.txt"
    $LauncherHash = (Get-FileHash -LiteralPath $LauncherFile -Algorithm SHA256).Hash.ToLowerInvariant()
    [IO.File]::WriteAllText($LauncherOwnerFile, "LUMA-LAUNCHER-1`n$LauncherHash`n", [Text.Encoding]::ASCII)
    $MetadataFile = Join-Path $BuildDir "luma-install.json"
    $Metadata = [ordered]@{ managed_install = $true; layout = "versions-v1"; version = $Version; payload_id = $PayloadId; bundle_sha256 = $BundleHash }
    [IO.File]::WriteAllText($MetadataFile, ($Metadata | ConvertTo-Json), [Text.UTF8Encoding]::new($false))
    $IntegrityFile = Join-Path $BuildDir "payload-hashes.iss"
    $MetadataHash = (Get-FileHash -LiteralPath $MetadataFile -Algorithm SHA256).Hash.ToLowerInvariant()
    $IntegrityLines = @('procedure AddPayloadHashes(Manifest: TStrings);', 'begin')
    foreach ($Line in @($ManifestLines) + @("$MetadataHash  luma-install.json")) {
        if ($Line -match '[\r\n]') { throw "Payload paths must not contain line breaks." }
        $IntegrityLines += "  Manifest.Add('$($Line.Replace("'", "''"))');"
    }
    $IntegrityLines += 'end;'
    [IO.File]::WriteAllText($IntegrityFile, ($IntegrityLines -join "`r`n"), [Text.UTF8Encoding]::new($false))
    $UiTestRoot = [IO.Path]::GetFullPath((Join-Path $Root "build")) + [IO.Path]::DirectorySeparatorChar
    if ($UiFontScaleTest -and !$Artifacts.StartsWith($UiTestRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw "UI font-scale tests must use a separate -OutputDir under build, never release artifacts."
    }
    $Arguments = @(
        "/DAppVersion=$Version", "/DPayloadId=$PayloadId", "/DBundleDir=$Bundle",
        "/DMetadataFile=$MetadataFile", "/DArtifactDir=$Artifacts",
        "/DIntegrityFile=$IntegrityFile",
        "/DLauncherFile=$LauncherFile", "/DLauncherOwnerFile=$LauncherOwnerFile",
        "/DAppIcon=$(Join-Path $Root 'luma/assets/icon.ico')", "packaging/windows/Luma.iss"
    )
    if ($UiFontScaleTest) { $Arguments = @("/DLumaUiFontScaleTest=1") + $Arguments }
    & $IsccPath @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup compilation failed." }
    $Suffix = if ($UiFontScaleTest) { "ui-font-test" } else { "setup" }
    $Installer = Join-Path $Artifacts "Luma-$Version-windows-x64-$Suffix.exe"
    if (!(Test-Path -LiteralPath $Installer -PathType Leaf)) { throw "Expected installer was not produced." }
    $Sha256 = (Get-FileHash -LiteralPath $Installer -Algorithm SHA256).Hash.ToLowerInvariant()
    $InstallerName = [IO.Path]::GetFileName($Installer)
    $ChecksumFile = Join-Path $Artifacts 'SHA256SUMS'
    $ChecksumLines = @()
    if (Test-Path -LiteralPath $ChecksumFile) {
        $ChecksumLines = @(Get-Content -LiteralPath $ChecksumFile | Where-Object {
            $_ -and $_ -notmatch ('^[0-9a-fA-F]{64}\s+\*?' + [regex]::Escape($InstallerName) + '$')
        })
    }
    $ChecksumLines += "$Sha256  $InstallerName"
    [IO.File]::WriteAllText($ChecksumFile, ($ChecksumLines -join "`n") + "`n", [Text.UTF8Encoding]::new($false))
    Write-Host "Built installer: $Installer"
    Write-Host "Payload: $PayloadId; Inno Setup compiler: $IsccPath"
    Write-Host "Updated SHA256SUMS. Keep the portable ZIP and dependency sources alongside this unsigned installer."
} finally { Pop-Location }
