; GoVoice installer — per-user install, no admin required.
; Bundles the frozen PyQt6 GUI (dist\GoVoice.exe) built by PyInstaller.
; Prompts for OpenAI/DeepSeek API keys on first install and writes
; %APPDATA%\voice-input\config so the app works immediately after setup.

#define MyAppName "GoVoice"
#define MyAppVersion "1.0.0"
#define MyAppExeName "GoVoice.exe"

[Setup]
AppId={{6F7B3B7E-6E77-4B6E-9C7C-3A6E7B0F2A11}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=GoVoice
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=installer_output
OutputBaseFilename=GoVoice-Setup
SetupIconFile=assets\icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "startupicon"; Description: "Start {#MyAppName} automatically when Windows starts"; Flags: checkedonce

[Files]
Source: "dist\GoVoice.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{userstartup}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: startupicon

[Run]
Filename: "taskkill.exe"; Parameters: "/IM {#MyAppExeName} /F"; Flags: runhidden skipifdoesntexist; StatusMsg: "Stopping any running instance..."
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName} now"; Flags: nowait postinstall skipifsilent

[Code]
var
  ApiPage: TInputQueryWizardPage;
  ConfigPath: String;

function GetConfigPath(): String;
begin
  Result := ExpandConstant('{userappdata}\voice-input\config');
end;

procedure InitializeWizard;
begin
  ConfigPath := GetConfigPath();

  ApiPage := CreateInputQueryPage(wpSelectTasks,
    'API Keys', 'GoVoice needs these to transcribe and clean up speech',
    'Enter your API keys below. Get an OpenAI key at platform.openai.com and a ' +
    'DeepSeek key at platform.deepseek.com. You can change these later from the ' +
    'app''s Settings menu.');
  ApiPage.Add('OpenAI API Key (required, for Whisper transcription):', True);
  ApiPage.Add('DeepSeek API Key (optional, for grammar cleanup):', True);
  ApiPage.Add('Context (optional — your role, tools, project names):', False);
  ApiPage.Add('Language code (optional, e.g. en, fr, de):', False);
  ApiPage.Values[3] := 'en';
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := False;
  if (ApiPage <> nil) and (PageID = ApiPage.ID) and FileExists(ConfigPath) then
    Result := True; // existing config found (upgrade) — don't overwrite keys
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if (ApiPage <> nil) and (CurPageID = ApiPage.ID) then
  begin
    if Trim(ApiPage.Values[0]) = '' then
    begin
      MsgBox('Please enter an OpenAI API key — it''s required for transcription.', mbError, MB_OK);
      Result := False;
    end;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ConfigDir: String;
  ConfigText: String;
begin
  if (CurStep = ssPostInstall) and not FileExists(ConfigPath) then
  begin
    ConfigDir := ExtractFileDir(ConfigPath);
    if not DirExists(ConfigDir) then
      ForceDirectories(ConfigDir);
    ConfigText :=
      'OPENAI_API_KEY=' + ApiPage.Values[0] + #13#10 +
      'DEEPSEEK_API_KEY=' + ApiPage.Values[1] + #13#10 +
      'CONTEXT=' + ApiPage.Values[2] + #13#10 +
      'LANGUAGE=' + ApiPage.Values[3] + #13#10 +
      'AUDIO_DEVICE=' + #13#10 +
      'PASTE_MODE=auto';
    SaveStringToFile(ConfigPath, ConfigText, False);
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  ResultCode: Integer;
begin
  if CurUninstallStep = usUninstall then
    Exec('taskkill.exe', '/IM {#MyAppExeName} /F', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;
