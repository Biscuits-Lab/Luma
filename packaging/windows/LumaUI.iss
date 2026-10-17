{ Native Inno controls only. Keep the engine's validation, handlers and modal flow. }
var
  LumaLayoutActive: Boolean;
  LumaFooter: TPanel;
  LumaBrandIcon: TBitmapImage;
  LumaDirFrame, LumaDirField: TPanel;
  LumaBrowseButton: TBitmapButton;
  LumaTitleDrag: TNewStaticText;
  LumaMinimizeButton, LumaCloseButton: TBitmapButton;
  LumaBrandName, LumaBrandVersion: TNewStaticText;
  LumaDesktop: TNewCheckBox;
  LumaDesktopTouched: Boolean;
  LumaFailureMemo: TNewMemo;
  LumaProgressDecorationDisabled: Boolean;
  LumaProgressHandle: HWND;
  LumaProgressStyleElements: TStyleElements;
  LumaProgressWidth, LumaProgressHeight, LumaProgressBucket: Integer;

function LumaSetWindowTheme(Window: HWND; SubAppName, SubIdList: String): Integer;
  external 'SetWindowTheme@uxtheme.dll stdcall';
function LumaCreateRoundRectRgn(Left, Top, Right, Bottom, EllipseWidth, EllipseHeight: Integer): THandle;
  external 'CreateRoundRectRgn@gdi32.dll stdcall';
function LumaSetWindowRgn(Window: HWND; Region: THandle; Redraw: BOOL): Integer;
  external 'SetWindowRgn@user32.dll stdcall';
function LumaDeleteObject(ObjectHandle: THandle): BOOL;
  external 'DeleteObject@gdi32.dll stdcall';
function LumaGetWindowLong(Window: HWND; Index: Integer): Longint;
  external 'GetWindowLongW@user32.dll stdcall';
function LumaSetWindowLong(Window: HWND; Index: Integer; Value: Longint): Longint;
  external 'SetWindowLongW@user32.dll stdcall';
function LumaSetWindowPos(Window, InsertAfter: HWND; X, Y, Width, Height: Integer;
  Flags: Cardinal): BOOL;
  external 'SetWindowPos@user32.dll stdcall';
function LumaShowWindow(Window: HWND; Command: Integer): BOOL;
  external 'ShowWindow@user32.dll stdcall';
function LumaPostMessage(Window: HWND; Message: Cardinal; WParam, LParam: Longint): BOOL;
  external 'PostMessageW@user32.dll stdcall';
function LumaReleaseCapture: BOOL;
  external 'ReleaseCapture@user32.dll stdcall';

procedure StyleLumaProgress;
var
  Region: THandle;
  Window: HWND;
  Width, Height: Integer;
begin
  if not LumaLayoutActive or LumaProgressDecorationDisabled then Exit;
  { Decorate the real engine-owned progress control. Min/Max/Position/State,
    accessibility, status text and taskbar progress remain owned by Inno. }
  Window := WizardForm.ProgressGauge.Handle;
  Width := WizardForm.ProgressGauge.Width;
  Height := WizardForm.ProgressGauge.Height;
  if (Window = LumaProgressHandle) and (Width = LumaProgressWidth) and
     (Height = LumaProgressHeight) then Exit;
  LumaProgressHandle := Window;
  LumaProgressWidth := Width;
  LumaProgressHeight := Height;
  { Nonempty Delphi strings containing NUL pass real empty UTF-16 strings.
    Literal empty strings marshal to NULL, which would restore OS theming. }
  if LumaSetWindowTheme(Window, #0, #0) <> 0 then begin
    LumaProgressDecorationDisabled := True;
    WizardForm.ProgressGauge.StyleElements := LumaProgressStyleElements;
    Log('LUMA_PROGRESS native theme could not be customized; using native fallback');
    Exit;
  end;
  SendMessage(Window, $0409, 0, $D0D0D0); { PBM_SETBARCOLOR }
  SendMessage(Window, $2001, 0, $414141); { PBM_SETBKCOLOR }
  Region := LumaCreateRoundRectRgn(0, 0, Width + 1, Height + 1, Height, Height);
  if Region <> 0 then begin
    if LumaSetWindowRgn(Window, Region, True) = 0 then begin
      LumaDeleteObject(Region);
      Log('LUMA_PROGRESS rounded clipping unavailable; using native rectangular fallback');
    end;
    { After success the window owns Region. Do not delete it. }
  end;
  LumaProgressHandle := Window;
  LumaProgressWidth := Width;
  LumaProgressHeight := Height;
end;

procedure StyleLumaDirectoryField;
var
  Region: THandle;
begin
  if not LumaLayoutActive then Exit;
  { The native edit sits inside a one-pixel rounded frame; the frame and its
    inner fill are real windows so child controls remain clipped to the curve. }
  Region := LumaCreateRoundRectRgn(0, 0, LumaDirFrame.Width + 1, LumaDirFrame.Height + 1,
    ScaleX(16), ScaleY(16));
  if Region <> 0 then begin
    if LumaSetWindowRgn(LumaDirFrame.Handle, Region, True) = 0 then
      LumaDeleteObject(Region);
  end;
  Region := LumaCreateRoundRectRgn(0, 0, LumaDirField.Width + 1, LumaDirField.Height + 1,
    ScaleX(14), ScaleY(14));
  if Region <> 0 then begin
    if LumaSetWindowRgn(LumaDirField.Handle, Region, True) = 0 then
      LumaDeleteObject(Region);
  end;
end;

procedure RemoveLumaTitleBar;
var
  Style: Longint;
begin
  { The wizard is a fixed-size window; hide the native caption and frame and
    provide the move/minimize/close affordances in the custom top strip. }
  Style := LumaGetWindowLong(WizardForm.Handle, -16);
  Style := Style and not $00C00000; { WS_CAPTION (includes WS_BORDER/DLGFRAME) }
  Style := Style and not $00040000; { WS_THICKFRAME }
  Style := Style and not $00080000; { WS_SYSMENU }
  Style := Style and not $00020000; { WS_MINIMIZEBOX }
  Style := Style and not $00010000; { WS_MAXIMIZEBOX }
  Style := Style and not $00800000; { WS_BORDER }
  Style := Style and not $00400000; { WS_DLGFRAME }
  LumaSetWindowLong(WizardForm.Handle, -16, Style);
  LumaSetWindowPos(WizardForm.Handle, 0, 0, 0, 0, 0, $0027);
  Log('LUMA_CHROME native title bar removed');
end;

procedure DragLumaSetup(Sender: TObject);
begin
  LumaReleaseCapture;
  SendMessage(WizardForm.Handle, $A1, 2, 0); { WM_NCLBUTTONDOWN, HTCAPTION }
end;

procedure MinimizeLumaSetup(Sender: TObject);
begin
  LumaShowWindow(WizardForm.Handle, 6); { SW_MINIMIZE }
end;

procedure CloseLumaSetup(Sender: TObject);
begin
  LumaPostMessage(WizardForm.Handle, $0010, 0, 0); { WM_CLOSE }
end;

procedure CurInstallProgressChanged(CurProgress, MaxProgress: Integer);
var
  Bucket: Integer;
begin
  if not LumaLayoutActive then Exit;
  StyleLumaProgress;
  if MaxProgress > 0 then begin
    Bucket := (Int64(CurProgress) * 20) div MaxProgress;
    if Bucket <> LumaProgressBucket then begin
      LumaProgressBucket := Bucket;
      Log('LUMA_PROGRESS ' + Format('window=%d;gauge=%d;current=%d;maximum=%d;position=%d;minimum=%d;range=%d', [
        WizardForm.Handle, WizardForm.ProgressGauge.Handle, CurProgress, MaxProgress,
        WizardForm.ProgressGauge.Position, WizardForm.ProgressGauge.Min, WizardForm.ProgressGauge.Max]));
    end;
  end;
end;

function DesktopTaskValue(const Value: String; const DefaultValue: Boolean): Boolean;
var
  Items: TStringList;
begin
  Result := DefaultValue;
  Items := TStringList.Create;
  try
    Items.CommaText := Lowercase(Value);
    if (Items.IndexOf('desktopicon') >= 0) or (Items.IndexOf('*desktopicon') >= 0) then
      Result := True
    else if Items.IndexOf('!desktopicon') >= 0 then
      Result := False;
  finally
    Items.Free;
  end;
end;

function InitialDesktopTask: Boolean;
var
  Value, Argument: String;
  I: Integer;
begin
  { Read only our existing Inno task preference for the displayed checkbox.
    The engine remains authoritative unless the user explicitly changes it. }
  Result := False;
  if RegQueryStringValue(HKCU64,
    'Software\Microsoft\Windows\CurrentVersion\Uninstall\{7F17F980-8965-4C81-8470-4880AD6B989F}_is1',
    'Inno Setup: Selected Tasks', Value) then
    Result := DesktopTaskValue(Value, False);
  for I := 1 to ParamCount do begin
    Argument := ParamStr(I);
    if CompareText(Copy(Argument, 1, 7), '/TASKS=') = 0 then
      Result := DesktopTaskValue(Copy(Argument, 8, MaxInt), False)
    else if CompareText(Copy(Argument, 1, 12), '/MERGETASKS=') = 0 then
      Result := DesktopTaskValue(Copy(Argument, 13, MaxInt), Result);
  end;
end;

procedure DesktopChoiceChanged(Sender: TObject);
begin
  LumaDesktopTouched := True;
end;

procedure LayoutLumaWizard(const PageID: Integer);
var
  BodyHeight, Width, Margin, ButtonTop, ContentWidth: Integer;
begin
  if not LumaLayoutActive then Exit;
  Width := WizardForm.ClientWidth;
  Margin := ScaleX(34);
  { Errors retain a larger readable area; they are never hidden to fit the design. }
  if (PageID = wpPreparing) or
     ((PageID = wpFinished) and not ShellRegistrationComplete) then
    WizardForm.ClientHeight := ScaleY(480)
  else
    WizardForm.ClientHeight := ScaleY(354);
  BodyHeight := WizardForm.ClientHeight - ScaleY(60);
  LumaTitleDrag.SetBounds(0, 0, Width, ScaleY(28));
  LumaMinimizeButton.SetBounds(Width - ScaleX(60), ScaleY(1), ScaleX(26), ScaleY(26));
  LumaCloseButton.SetBounds(Width - ScaleX(30), ScaleY(1), ScaleX(26), ScaleY(26));
  LumaFooter.SetBounds(0, BodyHeight, Width, ScaleY(60));
  LumaFooter.SendToBack;
  WizardForm.OuterNotebook.SetBounds(0, 0, Width, BodyHeight);
  WizardForm.InnerNotebook.SetBounds(Margin, ScaleY(128), Width - 2 * Margin,
    BodyHeight - ScaleY(146));
  ButtonTop := BodyHeight + ScaleY(12);
  WizardForm.NextButton.SetBounds(Width - Margin - ScaleX(112), ButtonTop,
    ScaleX(112), ScaleY(36));
  if WizardForm.NextButton.Visible then
    WizardForm.CancelButton.SetBounds(Width - Margin - ScaleX(204), ButtonTop,
      ScaleX(82), ScaleY(36))
  else
    WizardForm.CancelButton.SetBounds(Width - Margin - ScaleX(82), ButtonTop,
      ScaleX(82), ScaleY(36));
  WizardForm.BackButton.SetBounds(Margin, ButtonTop, ScaleX(82), ScaleY(36));
  WizardForm.NextButton.BringToFront;
  WizardForm.CancelButton.BringToFront;
  WizardForm.BackButton.BringToFront;
  LumaBrandIcon.BringToFront;
  LumaBrandName.BringToFront;
  LumaBrandVersion.BringToFront;
  if PageID = wpSelectDir then begin
    { Use the final page client, after Inno has resized its notebooks. Anchored
      controls laid out before this point previously extended beyond the page. }
    ContentWidth := WizardForm.SelectDirPage.ClientWidth;
    WizardForm.SelectDirBrowseLabel.SetBounds(0, 0, ContentWidth, ScaleY(22));
    LumaDirFrame.SetBounds(0, ScaleY(31), ContentWidth, ScaleY(38));
    LumaDirField.SetBounds(ScaleX(1), ScaleY(1), ContentWidth - ScaleX(2), ScaleY(36));
    WizardForm.DirEdit.SetBounds(ScaleX(12), ScaleY(39), ContentWidth - ScaleX(50), ScaleY(22));
    LumaBrowseButton.SetBounds(LumaDirField.Width - ScaleX(35), ScaleY(3), ScaleX(30), ScaleY(30));
    StyleLumaDirectoryField;
    LumaDesktop.SetBounds(0, ScaleY(85), ContentWidth, ScaleY(26));
    if WizardForm.PrevAppDir <> '' then
      WizardForm.NextButton.Caption := CustomMessage('UpdateAction')
    else
      WizardForm.NextButton.Caption := SetupMessage(msgButtonInstall);
  end;
  if PageID = wpInstalling then begin
    ContentWidth := WizardForm.InstallingPage.ClientWidth;
    WizardForm.StatusLabel.SetBounds(0, 0, ContentWidth, ScaleY(24));
    WizardForm.ProgressGauge.SetBounds(0, ScaleY(40), ContentWidth, ScaleY(6));
    WizardForm.FilenameLabel.SetBounds(0, ScaleY(62), ContentWidth, ScaleY(44));
    StyleLumaProgress;
  end;
  if PageID = wpFinished then begin
    WizardForm.FinishedHeadingLabel.Caption := CustomMessage('CompleteHeading');
    WizardForm.FinishedHeadingLabel.SetBounds(Margin, ScaleY(128), Width - 2 * Margin, ScaleY(30));
    WizardForm.FinishedLabel.SetBounds(Margin, ScaleY(170), Width - 2 * Margin, ScaleY(68));
    WizardForm.RunList.SetBounds(Margin, ScaleY(248), Width - 2 * Margin, ScaleY(32));
    if not ShellRegistrationComplete then begin
      WizardForm.FinishedHeadingLabel.Caption := CustomMessage('IncompleteHeading');
      WizardForm.FinishedLabel.Visible := False;
      WizardForm.RunList.Visible := False;
      LumaFailureMemo.Lines.Text := ShellRegistrationFailure;
      LumaFailureMemo.SetBounds(Margin, ScaleY(172), Width - 2 * Margin, BodyHeight - ScaleY(194));
      LumaFailureMemo.Visible := True;
    end;
  end;
  Log('LUMA_UI ' + Format(
    'page=%d;footer=%d;button_height=%d;primary_width=%d;secondary_width=%d;scale_x_100=%d;scale_y_100=%d;window=%d;directory=%d;desktop=%d;next=%d;cancel=%d;browse=%d;directory_page=%d;field=%d;logo_left=%d;logo_top=%d;logo_width=%d;logo_height=%d', [
     PageID, LumaFooter.Height, WizardForm.NextButton.Height, WizardForm.NextButton.Width,
     WizardForm.CancelButton.Width, ScaleX(100), ScaleY(100), WizardForm.Handle,
     WizardForm.DirEdit.Handle, LumaDesktop.Handle, WizardForm.NextButton.Handle,
     WizardForm.CancelButton.Handle, LumaBrowseButton.Handle,
     WizardForm.SelectDirPage.Handle, LumaDirFrame.Handle,
     LumaBrandIcon.Left, LumaBrandIcon.Top,
     LumaBrandIcon.Width, LumaBrandIcon.Height]));
end;

procedure BrowseLumaDirectory(Sender: TObject);
begin
  { Native Delphi events cannot be copied into a Pascal Script event slot:
    its setter resolves script procedure numbers only. Invoke the existing
    engine callback from an explicit script wrapper instead. }
  Log('LUMA_BROWSE invoking native directory chooser');
  WizardForm.DirBrowseButton.OnClick(WizardForm.DirBrowseButton);
  Log('LUMA_BROWSE native directory chooser closed');
end;

procedure InitializeWizard;
var
  Width, Margin: Integer;
begin
  { Inno turns off dark styling for High Contrast and /NOSTYLE. Keep its stock
    accessible layout in those modes, including the native task-selection page. }
  LumaLayoutActive := IsDarkInstallMode and not HighContrastActive;
  if not LumaLayoutActive then Exit;
  { The primary stays a standard Windows button, with its native light face. }
  WizardForm.NextButton.StyleElements := [];
  WizardForm.NextButton.Font.Color := clBlack;
  WizardForm.ClientWidth := ScaleX(620);
  WizardForm.ClientHeight := ScaleY(354);
  Width := WizardForm.ClientWidth;
  Margin := ScaleX(34);
  RemoveLumaTitleBar;
  WizardForm.MainPanel.Visible := False;
  WizardForm.Bevel.Visible := False;
  WizardForm.Bevel1.Visible := False;
  WizardForm.BeveledLabel.Visible := False;
  WizardForm.WizardBitmapImage.Visible := False;
  WizardForm.WizardBitmapImage2.Visible := False;
  WizardForm.WizardSmallBitmapImage.Visible := False;

  LumaFooter := TPanel.Create(WizardForm);
  LumaFooter.Parent := WizardForm;
  LumaFooter.BevelOuter := bvNone;
  LumaFooter.ParentBackground := False;
  LumaFooter.StyleElements := [seFont, seBorder];
  LumaFooter.Color := $191919;
  LumaFooter.TabStop := False;

  ExtractTemporaryFile('luma-setup-icon.png');
  LumaBrandIcon := TBitmapImage.Create(WizardForm);
  LumaBrandIcon.Parent := WizardForm;
  LumaBrandIcon.SetBounds(Margin, ScaleY(31), ScaleX(66), ScaleY(66));
  LumaBrandIcon.Stretch := True;
  LumaBrandIcon.BackColor := clNone;
  LumaBrandIcon.PngImage.LoadFromFile(ExpandConstant('{tmp}\luma-setup-icon.png'));
  LumaBrandName := TNewStaticText.Create(WizardForm);
  LumaBrandName.Parent := WizardForm;
  LumaBrandName.AutoSize := False;
  LumaBrandName.Caption := 'Luma';
  LumaBrandName.Font.Size := (WizardForm.Font.Size * 22) div 9;
  LumaBrandName.SetBounds(ScaleX(118), ScaleY(29), ScaleX(430), ScaleY(42));
  LumaBrandVersion := TNewStaticText.Create(WizardForm);
  LumaBrandVersion.Parent := WizardForm;
  LumaBrandVersion.AutoSize := False;
  LumaBrandVersion.Caption := FmtMessage(CustomMessage('VersionPlatform'), ['{#AppVersion}']);
  LumaBrandVersion.SetBounds(ScaleX(118), ScaleY(76), ScaleX(430), ScaleY(22));

  LumaTitleDrag := TNewStaticText.Create(WizardForm);
  LumaTitleDrag.Parent := WizardForm;
  LumaTitleDrag.Caption := '';
  LumaTitleDrag.AutoSize := False;
  LumaTitleDrag.Color := $222222;
  LumaTitleDrag.ParentColor := False;
  LumaTitleDrag.OnClick := @DragLumaSetup;
  LumaTitleDrag.SetBounds(0, 0, Width, ScaleY(28));

  LumaMinimizeButton := TBitmapButton.Create(WizardForm);
  LumaMinimizeButton.Parent := LumaTitleDrag;
  LumaMinimizeButton.Caption := CustomMessage('MinimizeWindow');
  LumaMinimizeButton.Hint := CustomMessage('MinimizeWindow');
  LumaMinimizeButton.ShowHint := True;
  LumaMinimizeButton.TabStop := False;
  LumaMinimizeButton.BackColor := clNone;
  LumaMinimizeButton.Stretch := True;
  LumaMinimizeButton.OnClick := @MinimizeLumaSetup;
  LumaMinimizeButton.SetBounds(Width - ScaleX(60), ScaleY(1), ScaleX(26), ScaleY(26));
  ExtractTemporaryFile('luma-setup-minimize.png');
  LumaMinimizeButton.PngImage.LoadFromFile(ExpandConstant('{tmp}\luma-setup-minimize.png'));

  LumaCloseButton := TBitmapButton.Create(WizardForm);
  LumaCloseButton.Parent := LumaTitleDrag;
  LumaCloseButton.Caption := CustomMessage('CloseWindow');
  LumaCloseButton.Hint := CustomMessage('CloseWindow');
  LumaCloseButton.ShowHint := True;
  LumaCloseButton.TabStop := False;
  LumaCloseButton.BackColor := clNone;
  LumaCloseButton.Stretch := True;
  LumaCloseButton.OnClick := @CloseLumaSetup;
  LumaCloseButton.SetBounds(Width - ScaleX(30), ScaleY(1), ScaleX(26), ScaleY(26));
  ExtractTemporaryFile('luma-setup-close.png');
  LumaCloseButton.PngImage.LoadFromFile(ExpandConstant('{tmp}\luma-setup-close.png'));

  WizardForm.SelectDirBitmapImage.Visible := False;
  WizardForm.SelectDirLabel.Visible := False;
  WizardForm.DiskSpaceLabel.Visible := False;
  LumaDirFrame := TPanel.Create(WizardForm);
  LumaDirFrame.Parent := WizardForm.SelectDirPage;
  LumaDirFrame.BevelOuter := bvNone;
  LumaDirFrame.ParentBackground := False;
  LumaDirFrame.StyleElements := [seFont, seBorder];
  LumaDirFrame.Color := $3D3D3D;
  LumaDirFrame.TabStop := False;
  LumaDirField := TPanel.Create(WizardForm);
  LumaDirField.Parent := LumaDirFrame;
  LumaDirField.BevelOuter := bvNone;
  LumaDirField.ParentBackground := False;
  LumaDirField.StyleElements := [seFont, seBorder];
  LumaDirField.Color := $2D2D2D;
  LumaDirField.TabStop := False;
  LumaDirFrame.SetBounds(0, ScaleY(31), Width - 2 * Margin, ScaleY(38));
  LumaDirField.SetBounds(ScaleX(1), ScaleY(1), LumaDirFrame.Width - ScaleX(2), ScaleY(36));
  LumaDirFrame.SendToBack;
  WizardForm.SelectDirBrowseLabel.Caption := CustomMessage('InstallLocation');
  WizardForm.SelectDirBrowseLabel.ShowAccelChar := True;
  WizardForm.SelectDirBrowseLabel.FocusControl := WizardForm.DirEdit;
  WizardForm.SelectDirBrowseLabel.SetBounds(0, 0, Width - 2 * Margin, ScaleY(22));
  WizardForm.DirEdit.AutoSize := False;
  WizardForm.DirEdit.BorderStyle := bsNone;
  WizardForm.DirEdit.Color := $2D2D2D;
  WizardForm.DirEdit.Font.Color := clWhite;
  WizardForm.DirEdit.SetBounds(ScaleX(12), ScaleY(39), Width - 2 * Margin - ScaleX(50), ScaleY(22));
  { TBitmapButton subclasses the native BUTTON class and retains keyboard
    activation, focus and its accessible caption, without drawing text. }
  LumaBrowseButton := TBitmapButton.Create(WizardForm);
  LumaBrowseButton.Parent := LumaDirField;
  LumaBrowseButton.Caption := CustomMessage('ChangeLocation');
  LumaBrowseButton.Hint := CustomMessage('ChangeLocation');
  LumaBrowseButton.ShowHint := True;
  LumaBrowseButton.TabStop := True;
  LumaBrowseButton.TabOrder := WizardForm.DirBrowseButton.TabOrder;
  LumaBrowseButton.BackColor := clNone;
  LumaBrowseButton.Stretch := True;
  LumaBrowseButton.OnClick := @BrowseLumaDirectory;
  ExtractTemporaryFile('luma-setup-folder.png');
  LumaBrowseButton.PngImage.LoadFromFile(ExpandConstant('{tmp}\luma-setup-folder.png'));
  WizardForm.DirBrowseButton.Visible := False;
  WizardForm.DirBrowseButton.TabStop := False;
  LumaDesktop := TNewCheckBox.Create(WizardForm);
  LumaDesktop.Parent := WizardForm.SelectDirPage;
  LumaDesktop.Caption := CustomMessage('DesktopShortcut');
  LumaDesktop.SetBounds(0, ScaleY(85), Width - 2 * Margin, ScaleY(26));
  LumaDesktop.Checked := InitialDesktopTask;
  LumaDesktop.OnClick := @DesktopChoiceChanged;
  WizardForm.DirEdit.TabOrder := 0;
  LumaBrowseButton.TabOrder := 0;
  LumaDesktop.TabOrder := 1;

  WizardForm.StatusLabel.SetBounds(0, 0, Width - 2 * Margin, ScaleY(24));
  LumaProgressStyleElements := WizardForm.ProgressGauge.StyleElements;
  WizardForm.ProgressGauge.StyleElements := [];
  WizardForm.ProgressGauge.SetBounds(0, ScaleY(40), Width - 2 * Margin, ScaleY(6));
  LumaProgressBucket := -1;
  WizardForm.FilenameLabel.SetBounds(0, ScaleY(62), Width - 2 * Margin, ScaleY(44));
  WizardForm.PreparingLabel.SetBounds(ScaleX(44), 0, Width - 2 * Margin - ScaleX(44), ScaleY(112));
  WizardForm.PreparingMemo.SetBounds(0, ScaleY(120), Width - 2 * Margin, ScaleY(108));
  WizardForm.PreparingYesRadio.Top := ScaleY(232);
  WizardForm.PreparingNoRadio.Top := ScaleY(254);
  LumaFailureMemo := TNewMemo.Create(WizardForm);
  LumaFailureMemo.Parent := WizardForm.FinishedPage;
  LumaFailureMemo.ReadOnly := True;
  LumaFailureMemo.WordWrap := True;
  LumaFailureMemo.ScrollBars := ssVertical;
  LumaFailureMemo.Visible := False;
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := False;
  if LumaLayoutActive and (PageID = wpSelectTasks) then begin
    { Native Inno builds TasksList just before this hook. Only explicit UI edits
      override its previous/command-line choices; silent setup stays untouched. }
    if LumaDesktopTouched and not WizardSilent then begin
      if LumaDesktop.Checked then WizardSelectTasks('desktopicon')
      else WizardSelectTasks('!desktopicon');
    end;
    Result := True;
  end;
end;

procedure InitializeUninstallProgressForm;
var
  Footer: TPanel;
  BodyHeight, Width: Integer;
begin
  if not IsDarkInstallMode or HighContrastActive then Exit;
  Width := UninstallProgressForm.ClientWidth;
  BodyHeight := UninstallProgressForm.ClientHeight - ScaleY(60);
  Footer := TPanel.Create(UninstallProgressForm);
  Footer.Parent := UninstallProgressForm;
  Footer.SetBounds(0, BodyHeight, Width, ScaleY(60));
  Footer.BevelOuter := bvNone;
  Footer.ParentBackground := False;
  Footer.StyleElements := [seFont, seBorder];
  Footer.Color := $191919;
  Footer.TabStop := False;
  Footer.SendToBack;
  UninstallProgressForm.OuterNotebook.Height := BodyHeight;
  UninstallProgressForm.CancelButton.SetBounds(Width - ScaleX(34 + 82), BodyHeight + ScaleY(12), ScaleX(82), ScaleY(36));
  UninstallProgressForm.CancelButton.BringToFront;
end;
