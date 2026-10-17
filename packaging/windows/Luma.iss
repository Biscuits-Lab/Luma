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
#ifndef LauncherFile
  #error LauncherFile is required
#endif
#ifndef LauncherOwnerFile
  #error LauncherOwnerFile is required
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
UninstallDisplayIcon={app}\Luma.exe
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
Source: "..\..\luma\assets\welcome-icon.png"; DestName: "luma-setup-icon.png"; Flags: dontcopy
Source: "..\..\luma\assets\setup-folder.png"; DestName: "luma-setup-folder.png"; Flags: dontcopy
Source: "..\..\luma\assets\setup-minimize.png"; DestName: "luma-setup-minimize.png"; Flags: dontcopy
Source: "..\..\luma\assets\setup-close.png"; DestName: "luma-setup-close.png"; Flags: dontcopy
Source: "{#BundleDir}\*"; DestDir: "{app}\versions\{#PayloadId}"; Flags: ignoreversion recursesubdirs createallsubdirs onlyifdoesntexist
Source: "{#MetadataFile}"; DestDir: "{app}\versions\{#PayloadId}"; DestName: "luma-install.json"; Flags: ignoreversion onlyifdoesntexist
; Protocol-v1 launcher is immutable across payload upgrades and may be running.
; PrepareToInstall refuses an unowned/mismatched root before these entries run.
Source: "{#LauncherFile}"; DestDir: "{app}"; DestName: "Luma.exe"; Flags: ignoreversion onlyifdoesntexist
Source: "{#LauncherOwnerFile}"; DestDir: "{app}"; DestName: "launcher-owner.txt"; Flags: ignoreversion onlyifdoesntexist

[Icons]
Name: "{autoprograms}\Luma"; Filename: "{app}\Luma.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\Luma"; Filename: "{app}\Luma.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\Luma.exe"; Description: "{cm:LaunchLuma}"; Flags: nowait postinstall skipifsilent unchecked; Check: ShellRegistrationSucceeded

; No [Registry], [InstallDelete] or [UninstallDelete] entries: shell registration
; uses the application's journaled current-user helper; images/settings are never purged.
[Code]
const
  LumaBusyReadWriteAccess = $C0000000; { GENERIC_READ or GENERIC_WRITE }
  LumaBusyOpenExisting = 3;
  LumaBusyNormalAttributes = $80;
  LumaBusyRetryDelayMilliseconds = 50;

var
  ShellRegistrationComplete: Boolean;
  ShellRegistrationFailure: String;
  LumaUninstallMutex: THandle;
  LumaDeletionHandles: array of THandle;

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

function ExistingLauncherFailure: String;
var
  Launcher, Owner, Pointer, Digest: String;
  Data: AnsiString;
  Size: Int64;
  I: Integer;
begin
  Result := '';
  Launcher := ExpandConstant('{app}\Luma.exe');
  Owner := ExpandConstant('{app}\launcher-owner.txt');
  Pointer := ExpandConstant('{app}\active-payload.txt');
  Result := FmtMessage(CustomMessage('ChangedPayload'), [Launcher]);
  if HasReparseAncestor(Launcher) or HasReparseAncestor(Owner) or
     HasReparseAncestor(Pointer) then Exit;
  if DirExists(Launcher) or DirExists(Owner) or DirExists(Pointer) then Exit;
  if not FileExists(Launcher) and not FileExists(Owner) and not FileExists(Pointer) then begin
    Result := '';
    Exit;
  end;
  if not FileExists(Launcher) or not FileExists(Owner) then Exit;
  if not FileSize64(Owner, Size) then Exit;
  if Size <> Length('LUMA-LAUNCHER-1'#10) + 64 + 1 then Exit;
  if not LoadStringFromFile(Owner, Data) then Exit;
  if Copy(Data, 1, Length('LUMA-LAUNCHER-1'#10)) <> 'LUMA-LAUNCHER-1'#10 then Exit;
  if Copy(Data, Length(Data), 1) <> #10 then Exit;
  Digest := Copy(Data, Length('LUMA-LAUNCHER-1'#10) + 1, 64);
  for I := 1 to 64 do
    if Pos(Digest[I], '0123456789abcdef') = 0 then Exit;
  try
    if CompareText(GetSHA256OfFile(Launcher), Digest) <> 0 then Exit;
  except
    Result := FmtMessage(CustomMessage('CannotVerifyPayload'), [Launcher]);
    Exit;
  end;
  Result := '';
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
  Result := ExistingLauncherFailure;
  if Result <> '' then Exit;
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
  ResultCode, RepairResult: Integer;
begin
  if CurStep <> ssPostInstall then Exit;
  ShellRegistrationComplete := False;
  Exe := ExpandConstant('{app}\versions\{#PayloadId}\Luma.exe');
  ResultCode := -1;
  if Exec(Exe, '--shell-register-current-user', ExtractFileDir(Exe),
          SW_HIDE, ewWaitUntilTerminated, ResultCode) then
    ShellRegistrationComplete := ResultCode = 0;
  Log(Format('Luma shell registration helper result: %d', [ResultCode]));
  if not ShellRegistrationComplete and not WizardSilent then begin
    { The packaged helper owns a default-No confirmation naming the exact old
      registration owner. It may migrate only that intact, expected owner.
      Silent installs and in-app managed updates never enter this path. }
    RepairResult := -1;
    if Exec(Exe, '--shell-repair-registration', ExtractFileDir(Exe),
            SW_SHOWNORMAL, ewWaitUntilTerminated, RepairResult) then
      ShellRegistrationComplete := RepairResult = 0;
    Log(Format('Luma interactive registration repair result: %d', [RepairResult]));
  end;
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

function LumaBusyCreateFile(FileName: String; DesiredAccess, ShareMode: DWORD;
  SecurityAttributes: DWORD_PTR; CreationDisposition, FlagsAndAttributes: DWORD;
  TemplateFile: THandle): THandle;
  external 'CreateFileW@kernel32.dll stdcall';
function LumaBusyCloseHandle(Handle: THandle): BOOL;
  external 'CloseHandle@kernel32.dll stdcall';

function LumaCreateMutex(SecurityAttributes: DWORD_PTR; InitialOwner: BOOL;
  Name: String): THandle;
  external 'CreateMutexW@kernel32.dll stdcall';
function LumaWaitForSingleObject(Handle: THandle; Milliseconds: DWORD): DWORD;
  external 'WaitForSingleObject@kernel32.dll stdcall';
function LumaReleaseMutex(Handle: THandle): BOOL;
  external 'ReleaseMutex@kernel32.dll stdcall';

procedure ReleaseLumaDeletionFence;
var
  I: Integer;
begin
  for I := 0 to GetArrayLength(LumaDeletionHandles) - 1 do
    if not LumaBusyCloseHandle(LumaDeletionHandles[I]) then
      Log('LUMA_UNINSTALL could not close an owned deletion guard');
  SetArrayLength(LumaDeletionHandles, 0);
  if LumaUninstallMutex <> 0 then begin
    LumaReleaseMutex(LumaUninstallMutex);
    LumaBusyCloseHandle(LumaUninstallMutex);
    LumaUninstallMutex := 0;
  end;
end;

function LumaHoldBinaryForDeletion(const Path: String;
  var RemainingRetries: Integer): Boolean;
var
  FileHandle: THandle;
  ErrorCode, Count: Integer;
begin
  Result := False;
  Count := GetArrayLength(LumaDeletionHandles);
  if Count >= 16384 then begin
    Log('LUMA_UNINSTALL binary guard count exceeded;path=' + Path);
    Exit;
  end;
  repeat
    { Keep this handle, allowing only deletion. Existing image readers fail
      this open, and new image/DLL readers stay blocked through file removal. }
    FileHandle := LumaBusyCreateFile(Path, LumaBusyReadWriteAccess, 4, 0,
      LumaBusyOpenExisting, LumaBusyNormalAttributes, 0);
    ErrorCode := DLLGetLastError;
    if FileHandle <> -1 then begin
      SetArrayLength(LumaDeletionHandles, Count + 1);
      LumaDeletionHandles[Count] := FileHandle;
      Result := True;
      Exit;
    end;
    Log(Format('LUMA_BUSY final_guard_failed;win32_error=%d;retries_remaining=%d;path=%s', [
      ErrorCode, RemainingRetries, Path]));
    if ((ErrorCode <> 32) and (ErrorCode <> 33)) or (RemainingRetries <= 0) then Exit;
    RemainingRetries := RemainingRetries - 1;
    Sleep(LumaBusyRetryDelayMilliseconds);
  until False;
end;

function PinInstalledBinaries(const Directory: String;
  var RemainingRetries: Integer): String;
var
  Entry: TFindRec;
  Path, Extension: String;
begin
  Result := '';
  if not FindFirst(AddBackslash(Directory) + '*', Entry) then Exit;
  try
    repeat
      if (Entry.Name <> '.') and (Entry.Name <> '..') then begin
        Path := AddBackslash(Directory) + Entry.Name;
        if (Entry.Attributes and $400) <> 0 then begin
          Result := Path;
          Exit;
        end;
        if (Entry.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0 then begin
          Result := PinInstalledBinaries(Path, RemainingRetries);
          if Result <> '' then Exit;
        end else begin
          Extension := Lowercase(ExtractFileExt(Path));
          if (Extension = '.dll') or (Extension = '.exe') or (Extension = '.pyd') then
            if not LumaHoldBinaryForDeletion(Path, RemainingRetries) then begin
              Result := Path;
              Exit;
            end;
        end;
      end;
    until not FindNext(Entry);
  finally
    FindClose(Entry);
  end;
end;

function IsLumaDigest(const Value: String): Boolean;
var
  I: Integer;
begin
  Result := False;
  if Length(Value) <> 64 then Exit;
  for I := 1 to 64 do
    if Pos(Value[I], '0123456789abcdef') = 0 then Exit;
  Result := True;
end;

function LumaProtocolHashMatches(const Path, Digest: String; Limit: Int64): Boolean;
var
  Size: Int64;
begin
  Result := False;
  if HasReparseAncestor(Path) or DirExists(Path) then Exit;
  if Digest = '-' then begin
    Result := not FileExists(Path);
    Exit;
  end;
  if not IsLumaDigest(Digest) then Exit;
  if not FileExists(Path) or not FileSize64(Path, Size) then Exit;
  if (Size < 1) or (Size > Limit) then Exit;
  try
    Result := CompareText(GetSHA256OfFile(Path), Digest) = 0;
  except
    Result := False;
  end;
end;

function LumaCanExclusivelyOpenBinary(const Path: String;
  var RemainingRetries: Integer): Boolean;
var
  FileHandle: THandle;
  ErrorCode, Attempt: Integer;
begin
  Result := False;
  Attempt := 1;
  repeat
    { Probe only: never create, truncate, write bytes, or delete a binary.
      No sharing preserves the existing mapped/in-use binary safety check. }
    FileHandle := LumaBusyCreateFile(Path, LumaBusyReadWriteAccess, 0, 0,
      LumaBusyOpenExisting, LumaBusyNormalAttributes, 0);
    { Inno captures GetLastError when the DLL returns, before its own API calls
      can overwrite it. Do not log or call another DLL before reading this. }
    ErrorCode := DLLGetLastError;
    if FileHandle <> -1 then begin
      if not LumaBusyCloseHandle(FileHandle) then begin
        ErrorCode := DLLGetLastError;
        Log(Format('LUMA_BUSY close_failed;win32_error=%d;path=%s', [ErrorCode, Path]));
        Exit;
      end;
      Result := True;
      Exit;
    end;
    Log(Format('LUMA_BUSY open_failed;win32_error=%d;attempt=%d;retries_remaining=%d;path=%s', [
      ErrorCode, Attempt, RemainingRetries, Path]));
    { Only sharing/lock contention can be transient after our helper exits.
      Access denied and every other failure remain immediately fail-closed. }
    if ((ErrorCode <> 32) and (ErrorCode <> 33)) or (RemainingRetries <= 0) then Exit;
    RemainingRetries := RemainingRetries - 1;
    Sleep(LumaBusyRetryDelayMilliseconds);
    Attempt := Attempt + 1;
  until False;
end;

function FirstBusyBinaryWithBudget(const Directory: String;
  var RemainingRetries: Integer): String;
var
  Entry: TFindRec;
  Path, Extension: String;
begin
  Result := '';
  if not FindFirst(AddBackslash(Directory) + '*', Entry) then Exit;
  try
    repeat
      if (Entry.Name <> '.') and (Entry.Name <> '..') then begin
        Path := AddBackslash(Directory) + Entry.Name;
        { Never follow a junction/symlink outside the installed payload. }
        if (Entry.Attributes and $400) <> 0 then begin
          Log('LUMA_BUSY reparse_point_rejected;path=' + Path);
          Result := Path;
          Exit;
        end;
        if (Entry.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0 then begin
          Result := FirstBusyBinaryWithBudget(Path, RemainingRetries);
          if Result <> '' then Exit;
        end else begin
          Extension := Lowercase(ExtractFileExt(Path));
          if (Extension = '.dll') or (Extension = '.exe') or (Extension = '.pyd') then begin
            if not LumaCanExclusivelyOpenBinary(Path, RemainingRetries) then begin
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

function FirstBusyBinary(const Directory: String): String;
var
  RemainingRetries: Integer;
begin
  { One total wait budget for every file and recursive directory, not one per
    binary: at most 200 waits of 50 ms after the unregister helper has exited. }
  RemainingRetries := 200;
  Result := FirstBusyBinaryWithBudget(Directory, RemainingRetries);
end;

procedure StopUninstall(const Reason: String);
begin
  ReleaseLumaDeletionFence;
  Log(Reason);
  SuppressibleMsgBox(Reason, mbError, MB_OK, IDOK);
  { Documented abort point: no installed files have been removed yet. }
  Abort;
end;

procedure CommitLumaUninstall(var RemainingRetries: Integer);
var
  Ready, Pending, Pointer, Launcher, Owner, MutexName, BusyFile: String;
  Data, CheckedData, Canonical: AnsiString;
  Size: Int64;
  Lines: TStringList;
  I: Integer;
  MutexHandle: THandle;
  WaitResult: DWORD;
begin
  Ready := ExpandConstant('{app}\managed-uninstall.ready');
  Pending := ExpandConstant('{app}\managed-uninstall.pending.json');
  Pointer := ExpandConstant('{app}\active-payload.txt');
  Launcher := ExpandConstant('{app}\Luma.exe');
  Owner := ExpandConstant('{app}\launcher-owner.txt');
  if HasReparseAncestor(Ready) or DirExists(Ready) or
     not FileSize64(Ready, Size) or (Size < 1) or (Size > 512) or
     not LoadStringFromFile(Ready, Data) then
    StopUninstall(CustomMessage('UninstallCannotRemove'));
  Lines := TStringList.Create;
  try
    Lines.Text := String(Data);
    if Lines.Count <> 6 then StopUninstall(CustomMessage('UninstallCannotRemove'));
    Canonical := '';
    for I := 0 to Lines.Count - 1 do Canonical := Canonical + AnsiString(Lines[I]) + #10;
    if (Data <> Canonical) or (Lines[0] <> 'LUMA-UNINSTALL-1') or
       not IsLumaDigest(Lines[1]) or not IsLumaDigest(Lines[2]) or
       ((Lines[3] <> '-') and not IsLumaDigest(Lines[3])) or not IsLumaDigest(Lines[4]) then
      StopUninstall(CustomMessage('UninstallCannotRemove'));
    MutexName := Lines[5];
    if (Copy(MutexName, 1, Length('Local\LumaManagedRoot-')) <> 'Local\LumaManagedRoot-') or
       not IsLumaDigest(Copy(MutexName, Length('Local\LumaManagedRoot-') + 1, MaxInt)) then
      StopUninstall(CustomMessage('UninstallCannotRemove'));
    MutexHandle := LumaCreateMutex(0, False, MutexName);
    if MutexHandle = 0 then StopUninstall(CustomMessage('UninstallCannotCheck'));
    WaitResult := LumaWaitForSingleObject(MutexHandle, 0);
    if (WaitResult <> 0) and (WaitResult <> $80) then begin
      LumaBusyCloseHandle(MutexHandle);
      StopUninstall(CustomMessage('UninstallCannotCheck'));
    end;
    LumaUninstallMutex := MutexHandle;
    { Match the exact helper receipt again after acquiring its root mutex. }
    if HasReparseAncestor(Ready) or not FileSize64(Ready, Size) or
       (Size <> Length(Data)) or not LoadStringFromFile(Ready, CheckedData) or
       (CheckedData <> Data) or
       not LumaProtocolHashMatches(Launcher, Lines[1], 536870912) or
       not LumaProtocolHashMatches(Owner, Lines[2], 512) or
       not LumaProtocolHashMatches(Pointer, Lines[3], 512) or
       not LumaProtocolHashMatches(Pending, Lines[4], 8388608) or
       FileExists(ExpandConstant('{app}\active-payload.pending.json')) or
       DirExists(ExpandConstant('{app}\active-payload.pending.json')) then
      StopUninstall(CustomMessage('UninstallCannotRemove'));
    { Final helper has now exited. Retain every successful binary guard so no
      root/payload launch can enter between this scan and Inno file removal. }
    if not LumaHoldBinaryForDeletion(Launcher, RemainingRetries) then
      StopUninstall(FmtMessage(CustomMessage('UninstallBusy'), [Launcher]));
    BusyFile := PinInstalledBinaries(ExpandConstant('{app}\versions'), RemainingRetries);
    if BusyFile <> '' then
      StopUninstall(FmtMessage(CustomMessage('UninstallBusy'), [BusyFile]));
    { Hash protocol files again at the commit point. The root EXE was hashed
      before its deny-read guard, which now prevents another independent read. }
    if HasReparseAncestor(Ready) or not FileSize64(Ready, Size) or
       (Size <> Length(Data)) or not LoadStringFromFile(Ready, CheckedData) or
       (CheckedData <> Data) or
       not LumaProtocolHashMatches(Owner, Lines[2], 512) or
       not LumaProtocolHashMatches(Pointer, Lines[3], 512) or
       not LumaProtocolHashMatches(Pending, Lines[4], 8388608) then
      StopUninstall(CustomMessage('UninstallCannotRemove'));
    { Only these exact unchanged protocol files are ours. Keep the usable
      pointer LAST, so a failed earlier delete is repairable by Setup. }
    if not DeleteFile(Ready) then StopUninstall(CustomMessage('UninstallCannotRemove'));
    if not DeleteFile(Pending) then StopUninstall(CustomMessage('UninstallCannotRemove'));
    if (Lines[3] <> '-') and not DeleteFile(Pointer) then
      StopUninstall(CustomMessage('UninstallCannotRemove'));
    Log('LUMA_UNINSTALL protocol commit complete; binary guards and root mutex retained');
  finally
    Lines.Free;
  end;
end;

procedure DeinitializeUninstall;
begin
  ReleaseLumaDeletionFence;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Exe, BusyFile, Launcher: String;
  ResultCode, RemainingRetries: Integer;
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
  Launcher := ExpandConstant('{app}\Luma.exe');
  RemainingRetries := 200;
  if FileExists(Launcher) and not LumaCanExclusivelyOpenBinary(Launcher, RemainingRetries) then
    StopUninstall(FmtMessage(CustomMessage('UninstallBusy'), [Launcher]));
  BusyFile := FirstBusyBinaryWithBudget(ExpandConstant('{app}\versions'), RemainingRetries);
  if BusyFile <> '' then
    StopUninstall(FmtMessage(CustomMessage('UninstallBusy'), [BusyFile]));
  { The finalizer only prepares a hash-bound receipt; it keeps the pointer.
    A second retained-handle guard follows its own process teardown. }
  if not Exec(Exe, '--finalize-managed-uninstall', ExtractFileDir(Exe),
              SW_HIDE, ewWaitUntilTerminated, ResultCode) then
    StopUninstall(CustomMessage('UninstallCannotCheck'));
  if ResultCode <> 0 then
    StopUninstall(CustomMessage('UninstallCannotRemove'));
  CommitLumaUninstall(RemainingRetries);
end;
