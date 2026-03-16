#define MyAppName "RotorTcpBridge"
#define MyAppVersion "1.0"
#define MyAppPublisher "Joerg Koerner DK8DE"
#define MyAppURL "https://github.com/dk8de/RotorTcpBridge"
#define MySourceDir "D:\\Rotor\\RotorTcpBridge\\dist\\RotorTcpBridge"
#define MyProjDir "D:\\Rotor\\RotorTcpBridge"
#define MyExeName "RotorTcpBridge.exe"
#define MyAppIcon "D:\\Rotor\\RotorTcpBridge\\dist\\RotorTcpBridge\\_internal\\rotortcpbridge\\rotor.ico"
#define MyInternalDir MySourceDir + "\\_internal\\rotortcpbridge"

[Setup]
AppId={{A219D4FA-6E44-4A8E-A4D8-7DF7799632F8}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
VersionInfoVersion={#MyAppVersion}.0.0
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersion}.0.0
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} Setup
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=D:\Rotor\RotorTcpBridge\dist\installer
OutputBaseFilename=RotorTcpBridge-Setup-{#MyAppVersion}
Compression=lzma
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
SetupIconFile={#MyAppIcon}
UninstallDisplayIcon={app}\{#MyExeName}
ArchitecturesInstallIn64BitMode=x64

[Languages]
Name: "german";  MessagesFile: "compiler:Languages\German.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
german.CreateDesktopIcon=Desktop-Verknüpfung erstellen
english.CreateDesktopIcon=Create desktop shortcut
german.AdditionalIcons=Zusätzliche Symbole:
english.AdditionalIcons=Additional icons:
german.RunAfterInstall={#MyAppName} starten
english.RunAfterInstall=Launch {#MyAppName}

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
; Alle vom PyInstaller gebauten Dateien (EXE, DLLs, _internal/...)
; Enthält bereits: Antenne.png, Antenne_T.png, windPfeil.png, rotor.ico,
;                  locales/, static/ (Leaflet), KartenLight/, KartenDark/
Source: "{#MySourceDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyExeName}"; IconFilename: "{app}\_internal\rotortcpbridge\rotor.ico"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyExeName}"; Tasks: desktopicon; IconFilename: "{app}\_internal\rotortcpbridge\rotor.ico"

[Run]
Filename: "{app}\{#MyExeName}"; Description: "{cm:RunAfterInstall}"; Flags: nowait postinstall skipifsilent

[Code]
{ Setzt die gewählte Installationssprache in der App-Konfiguration.
  Nur bei Erstinstallation (keine config.json vorhanden).
  Bei Upgrade bleibt die bestehende Spracheinstellung des Benutzers erhalten. }
procedure SetLanguageInConfig();
var
  ConfigDir: String;
  ConfigFile: String;
  LangCode: String;
  Content: String;
begin
  if ActiveLanguage = 'english' then
    LangCode := 'en'
  else
    LangCode := 'de';

  ConfigDir := ExpandConstant('{userappdata}\RotorTcpBridge');
  ConfigFile := ConfigDir + '\config.json';

  ForceDirectories(ConfigDir);

  if not FileExists(ConfigFile) then
  begin
    Content := '{' + #13#10 +
               '  "ui": {' + #13#10 +
               '    "language": "' + LangCode + '"' + #13#10 +
               '  }' + #13#10 +
               '}';
    SaveStringToFile(ConfigFile, Content, False);
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    SetLanguageInConfig();
end;
