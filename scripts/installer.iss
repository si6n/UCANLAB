; ==============================================================================
; uCAN Lab - Professional Windows Installer Script (Inno Setup 6)
; Compiles standalone ucanlab.exe, launcher, and full DBC databases into
; UCanLab_Setup_v1.exe with LZMA2 ultra-compression and pre-flight checks.
; ==============================================================================

#define MyAppName "uCAN Lab"
#define MyAppVersion "13.0.0"
#define MyAppPublisher "uCAN Lab Diagnostic Systems"
#define MyAppURL "https://github.com/canak/Universal-CAN-BUS-Tool"
#define MyAppExeName "ucanlab.exe"
#define MyAppLauncherExeName "ucanlab_launcher.exe"

[Setup]
; Unique AppId for Windows GUID identification (DO NOT change between versions)
AppId={{D38A4927-64C2-4E90-B871-33B857A18910}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} v{#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
LicenseFile=..\LICENSE
OutputDir=..\dist
OutputBaseFilename=UCanLab_Setup_v1
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=commandline
DisableProgramGroupPage=auto
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "turkish"; MessagesFile: "compiler:Languages\Turkish.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; Main Executable and Launcher
Source: "..\dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\{#MyAppLauncherExeName}"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist

; Curated Automotive/Marine/Heavy-Duty DBC Knowledge Catalog (Offline Ready)
Source: "..\data\dbc\*"; DestDir: "{app}\data\dbc"; Flags: ignoreversion recursesubdirs createallsubdirs

; Documentation and legal
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist

[Icons]
Name: "{autoprograms}\{#MyAppName}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autoprograms}\{#MyAppName}\uCAN Launcher & Diagnostics"; Filename: "{app}\{#MyAppLauncherExeName}"; Check: LauncherExists
Name: "{autoprograms}\{#MyAppName}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[Code]
// Pre-flight checks for Edge WebView2 and Visual C++ 2015-2022 Redistributable

function LauncherExists: Boolean;
begin
  Result := FileExists(ExpandConstant('{app}\{#MyAppLauncherExeName}'));
end;

function IsWebView2Installed: Boolean;
var
  InstalledVersion: String;
begin
  Result := False;
  if RegQueryStringValue(HKLM, 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', InstalledVersion) then
  begin
    if Length(InstalledVersion) > 0 then
      Result := True;
  end;
  if (not Result) and RegQueryStringValue(HKCU, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', InstalledVersion) then
  begin
    if Length(InstalledVersion) > 0 then
      Result := True;
  end;
end;

function IsVCRedistInstalled: Boolean;
var
  Installed: Cardinal;
begin
  Result := False;
  if RegQueryDWordValue(HKLM, 'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64', 'Installed', Installed) then
  begin
    Result := (Installed = 1);
  end;
end;

function InitializeSetup: Boolean;
var
  Msg: String;
begin
  Result := True;

  // 1. Check Microsoft Edge WebView2 Runtime
  if not IsWebView2Installed then
  begin
    Msg := 'Uyarı: Microsoft Edge WebView2 Runtime sisteminizde tespit edilemedi.' + #13#10 +
           'uCAN Lab React arayüzünün düzgün görüntülenmesi için WebView2 gereklidir.' + #13#10#13#10 +
           'Kuruluma yine de devam etmek istiyor musunuz?';
    if MsgBox(Msg, mbConfirmation, MB_YESNO) = IDNO then
    begin
      Result := False;
      Exit;
    end;
  end;

  // 2. Check Visual C++ 2015-2022 Redistributable (x64)
  if not IsVCRedistInstalled then
  begin
    Msg := 'Bilgi: Visual C++ 2015-2022 x64 Redistributable tespit edilemedi.' + #13#10 +
           'C++ donanım sürücülerinin (PCAN, RP1210) çalışması için bu bileşen önerilir.' + #13#10#13#10 +
           'Kuruluma devam edilsin mi?';
    if MsgBox(Msg, mbConfirmation, MB_YESNO) = IDNO then
    begin
      Result := False;
      Exit;
    end;
  end;
end;
