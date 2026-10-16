; Compile through scripts/build_installer.ps1 using Inno Setup 6.3+.
; Payload paths are immutable: never replace a DLL already mapped by Explorer.
#ifndef AppVersion
  #error AppVersion is required
#endif
#ifndef PayloadId
  #error PayloadId is required
#endif
#ifndef BundleDir
  #error BundleDir is required
#endif
#ifndef MetadataFile
  #error MetadataFile is required
#endif
#ifndef ArtifactDir
  #error ArtifactDir is required
#endif
#ifndef AppIcon
  #error AppIcon is required
#endif
#ifndef IntegrityFile
  #error IntegrityFile is required
#endif

[Setup]
AppId={{7F17F980-8965-4C81-8470-4880AD6B989F}
AppName=Luma
AppVersion={#AppVersion}
AppPublisher=Biscuits Lab
AppPublisherURL=https://github.com/Biscuits-Lab/Luma
AppSupportURL=https://github.com/Biscuits-Lab/Luma/issues
AppUpdatesURL=https://github.com/Biscuits-Lab/Luma/releases
DefaultDirName={localappdata}\Programs\Luma
DefaultGroupName=Luma
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64os
ArchitecturesInstallIn64BitMode=x64os
MinVersion=10.0
WizardStyle=modern
SetupIconFile={#AppIcon}
UninstallDisplayIcon={app}\versions\{#PayloadId}\Luma.exe
UninstallDisplayName=Luma
OutputDir={#ArtifactDir}
OutputBaseFilename=Luma-{#AppVersion}-windows-x64-setup
Compression=lzma2
SolidCompression=yes
CloseApplications=no
RestartApplications=no
AllowNoIcons=yes
UsePreviousAppDir=yes
UsePreviousTasks=yes
UninstallLogging=yes
UninstallLogMode=append

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked

[Files]
Source: "{#BundleDir}\*"; DestDir: "{app}\versions\{#PayloadId}"; Flags: ignoreversion recursesubdirs createallsubdirs onlyifdoesntexist
Source: "{#MetadataFile}"; DestDir: "{app}\versions\{#PayloadId}"; DestName: "luma-install.json"; Flags: ignoreversion onlyifdoesntexist

[Icons]
Name: "{autoprograms}\Luma"; Filename: "{app}\versions\{#PayloadId}\Luma.exe"; WorkingDir: "{app}\versions\{#PayloadId}"
Name: "{autodesktop}\Luma"; Filename: "{app}\versions\{#PayloadId}\Luma.exe"; WorkingDir: "{app}\versions\{#PayloadId}"; Tasks: desktopicon

[Run]
Filename: "{app}\versions\{#PayloadId}\Luma.exe"; Description: "Launch Luma"; Flags: nowait postinstall skipifsilent unchecked

; No [Registry], [InstallDelete] or [UninstallDelete] entries: shell registration
; requires the user's explicit Settings action; images/settings are never purged.
[Code]
#include IntegrityFile

function HasReparseAncestor(Path: String): Boolean;
var
  Entry: TFindRec;
  Parent: String;
begin
  Result := False;
  while Length(Path) > 3 do begin
    if FindFirst(Path, Entry) then begin
      try
        if (Entry.Attributes and $400) <> 0 then begin
          Result := True;
          Exit;
        end;
      finally
        FindClose(Entry);
      end;
    end;
    Parent := ExtractFileDir(Path);
    if Parent = Path then Exit;
    Path := Parent;
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Manifest: TStringList;
  I: Integer;
  Line, Path: String;
begin
  Result := '';
  if HasReparseAncestor(ExpandConstant('{app}\versions\{#PayloadId}')) then begin
    Result := 'Choose an installation path without symbolic links or junctions so Luma can safely identify its files during uninstall.';
    Exit;
  end;
  Manifest := TStringList.Create;
  try
    AddPayloadHashes(Manifest);
    for I := 0 to Manifest.Count - 1 do begin
      Line := Manifest[I];
      Path := ExpandConstant('{app}\versions\{#PayloadId}\') + Copy(Line, 67, MaxInt);
      if FileExists(Path) then begin
        try
          if CompareText(GetSHA256OfFile(Path), Copy(Line, 1, 64)) <> 0 then begin
            Result := 'An existing Luma package file has changed: ' + Path + #13#10 +
              'Setup will not overwrite a possibly loaded or intentionally replaced file. Back it up and remove that changed file before retrying, or choose a different installation folder.';
            Exit;
          end;
        except
          Result := 'Setup could not verify an existing Luma package file: ' + Path + #13#10 + 'Close Luma and retry.';
          Exit;
        end;
      end;
    end;
  finally
    Manifest.Free;
  end;
end;

procedure InitializeWizard;
begin
  WizardForm.FinishedLabel.Caption :=
    'Luma has been installed for your Windows account.' + #13#10#13#10 +
    'Explorer thumbnails and Open with integration are optional. Open Luma Settings to enable them. Your default image application is unchanged.';
end;

function FirstBusyBinary(const Directory: String): String;
var
  Entry: TFindRec;
  Path, Extension: String;
  Stream: TFileStream;
begin
  Result := '';
  if not FindFirst(AddBackslash(Directory) + '*', Entry) then Exit;
  try
    repeat
      if (Entry.Name <> '.') and (Entry.Name <> '..') then begin
        Path := AddBackslash(Directory) + Entry.Name;
        { Never follow a junction/symlink outside the installed payload. }
        if (Entry.Attributes and $400) <> 0 then begin
          Result := Path;
          Exit;
        end;
        if (Entry.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0 then begin
          Result := FirstBusyBinary(Path);
          if Result <> '' then Exit;
        end else begin
          Extension := Lowercase(ExtractFileExt(Path));
          if (Extension = '.dll') or (Extension = '.exe') or (Extension = '.pyd') then begin
            try
              { Open without modifying bytes; a mapped binary denies write access. }
              Stream := TFileStream.Create(Path, fmOpenReadWrite or fmShareExclusive);
              Stream.Free;
            except
              Result := Path;
              Exit;
            end;
          end;
        end;
      end;
    until not FindNext(Entry);
  finally
    FindClose(Entry);
  end;
end;

procedure StopUninstall(const Reason: String);
begin
  Log(Reason);
  SuppressibleMsgBox(Reason, mbError, MB_OK, IDOK);
  { Documented abort point: no installed files have been removed yet. }
  Abort;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Exe, BusyFile: String;
  ResultCode: Integer;
begin
  if CurUninstallStep <> usUninstall then Exit;
  Exe := ExpandConstant('{app}\versions\{#PayloadId}\Luma.exe');
  if not FileExists(Exe) then
    StopUninstall('Luma is incomplete. Reinstall this version, then retry uninstalling so its optional Explorer registration can be safely removed.');
  if not Exec(Exe, '--shell-unregister-if-owned', ExtractFileDir(Exe),
              SW_HIDE, ewWaitUntilTerminated, ResultCode) then
    StopUninstall('Luma could not check its Explorer registration. Close Luma, reinstall if necessary, and retry uninstalling. No application files have been removed.');
  if ResultCode <> 0 then
    StopUninstall('Luma could not safely remove its Explorer registration. Open Luma Settings to disable Explorer integration, then retry uninstalling. No application files have been removed.');
  BusyFile := FirstBusyBinary(ExpandConstant('{app}\versions'));
  if BusyFile <> '' then
    StopUninstall('Luma Explorer integration has been disabled where owned by this installation. A file is still in use or cannot be accessed:' + #13#10 + BusyFile + #13#10#13#10 +
      'Close Luma and Explorer windows, then retry. If Windows still holds the thumbnail DLL, sign out and back in before retrying. Application files and settings have been kept.');
end;
