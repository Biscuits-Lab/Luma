; Compile through scripts/build_installer.ps1 using Inno Setup 6.7+.
; Payload paths are immutable: never replace a DLL already mapped by Explorer.
; Check the compiler engine, not ISCC.exe's often-zero file version resource.
#if Ver < EncodeVer(6, 7, 0)
  #error Inno Setup 6.7 or newer is required
#endif
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
WizardStyle=modern dark hidebevels
WizardBackColor=#222222
WizardImageFile=
WizardSmallImageFile=..\..\luma\assets\icon.png
DisableWelcomePage=yes
DisableDirPage=no
DisableReadyPage=yes
SetupIconFile={#AppIcon}
UninstallDisplayIcon={app}\versions\{#PayloadId}\Luma.exe
UninstallDisplayName=Luma
OutputDir={#ArtifactDir}
#ifdef LumaUiFontScaleTest
OutputBaseFilename=Luma-{#AppVersion}-windows-x64-ui-font-test
#else
OutputBaseFilename=Luma-{#AppVersion}-windows-x64-setup
#endif
Compression=lzma2
SolidCompression=yes
CloseApplications=no
RestartApplications=no
AllowNoIcons=yes
UsePreviousAppDir=yes
UsePreviousTasks=yes
; Use the current Windows display language, not regional date/number formats.
LanguageDetectionMethod=uilanguage
ShowLanguageDialog=no
UsePreviousLanguage=no
UninstallLogging=yes
UninstallLogMode=append

[Languages]
; English is the fallback for Windows UI languages not supplied below.
Name: "en"; MessagesFile: "compiler:Default.isl"
Name: "zhCN"; MessagesFile: "compiler:Default.isl,languages\ChineseSimplified.isl"
Name: "zhTW"; MessagesFile: "compiler:Default.isl,languages\ChineseTraditional.isl,languages\ChineseTraditionalOverrides.isl"

#ifdef LumaUiFontScaleTest
; Compile-time-only stress build, never used for a release asset.
[LangOptions]
en.DialogFontSize=18
zhCN.DialogFontSize=18
zhTW.DialogFontSize=18
#endif

#include "languages\LumaMessages.iss"

[Tasks]
Name: "desktopicon"; Description: "{cm:DesktopShortcut}"; GroupDescription: "{cm:ShortcutsGroup}"; Flags: unchecked

[Files]
Source: "..\..\luma\assets\icon.png"; DestName: "luma-setup-icon.png"; Flags: dontcopy
Source: "{#BundleDir}\*"; DestDir: "{app}\versions\{#PayloadId}"; Flags: ignoreversion recursesubdirs createallsubdirs onlyifdoesntexist
Source: "{#MetadataFile}"; DestDir: "{app}\versions\{#PayloadId}"; DestName: "luma-install.json"; Flags: ignoreversion onlyifdoesntexist

[Icons]
Name: "{autoprograms}\Luma"; Filename: "{app}\versions\{#PayloadId}\Luma.exe"; WorkingDir: "{app}\versions\{#PayloadId}"
Name: "{autodesktop}\Luma"; Filename: "{app}\versions\{#PayloadId}\Luma.exe"; WorkingDir: "{app}\versions\{#PayloadId}"; Tasks: desktopicon

[Run]
Filename: "{app}\versions\{#PayloadId}\Luma.exe"; Description: "{cm:LaunchLuma}"; Flags: nowait postinstall skipifsilent unchecked; Check: ShellRegistrationSucceeded

; No [Registry], [InstallDelete] or [UninstallDelete] entries: shell registration
; uses the application's journaled current-user helper; images/settings are never purged.
[Code]
var
  ShellRegistrationComplete: Boolean;
  ShellRegistrationFailure: String;

#include "LumaUI.iss"
#include IntegrityFile

function ShellRegistrationSucceeded: Boolean;
begin
  Result := ShellRegistrationComplete;
end;

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
    Result := CustomMessage('UnsafeInstallPath');
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
            Result := FmtMessage(CustomMessage('ChangedPayload'), [Path]);
            Exit;
          end;
        except
          Result := FmtMessage(CustomMessage('CannotVerifyPayload'), [Path]);
          Exit;
        end;
      end;
    end;
  finally
    Manifest.Free;
  end;
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  LayoutLumaWizard(CurPageID);
  if CurPageID <> wpFinished then Exit;
  if ShellRegistrationComplete then
    WizardForm.FinishedLabel.Caption :=
      CustomMessage('InstallComplete')
  else
    WizardForm.FinishedLabel.Caption := ShellRegistrationFailure;
end;

function UpdateReadyMemo(Space, NewLine, MemoUserInfoInfo, MemoDirInfo,
  MemoTypeInfo, MemoComponentsInfo, MemoGroupInfo, MemoTasksInfo: String): String;
begin
  Result := MemoDirInfo + NewLine + NewLine + MemoGroupInfo + NewLine +
    MemoTasksInfo + NewLine + NewLine +
    CustomMessage('ReadyIntegration');
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Exe: String;
  ResultCode: Integer;
begin
  if CurStep <> ssPostInstall then Exit;
  ShellRegistrationComplete := False;
  Exe := ExpandConstant('{app}\versions\{#PayloadId}\Luma.exe');
  ResultCode := -1;
  if Exec(Exe, '--shell-register-current-user', ExtractFileDir(Exe),
          SW_HIDE, ewWaitUntilTerminated, ResultCode) then
    ShellRegistrationComplete := ResultCode = 0;
  Log(Format('Luma shell registration helper result: %d', [ResultCode]));
  if not ShellRegistrationComplete then begin
    { Keep every payload: an older DLL can still be mapped by Explorer. The
      registrar rolls back its own transaction; never delete files on failure. }
    ShellRegistrationFailure := CustomMessage('RegistrationFailed');
    Log(ShellRegistrationFailure);
    SuppressibleMsgBox(ShellRegistrationFailure, mbError, MB_OK, IDOK);
  end;
end;

function GetCustomSetupExitCode: Integer;
begin
  { Nonzero even for /VERYSILENT: automation must not report registration as done. }
  if ShellRegistrationComplete then Result := 0 else Result := 10;
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
    StopUninstall(CustomMessage('UninstallIncomplete'));
  if not Exec(Exe, '--shell-unregister-if-owned', ExtractFileDir(Exe),
              SW_HIDE, ewWaitUntilTerminated, ResultCode) then
    StopUninstall(CustomMessage('UninstallCannotCheck'));
  if ResultCode <> 0 then
    StopUninstall(CustomMessage('UninstallCannotRemove'));
  BusyFile := FirstBusyBinary(ExpandConstant('{app}\versions'));
  if BusyFile <> '' then
    StopUninstall(FmtMessage(CustomMessage('UninstallBusy'), [BusyFile]));
end;
