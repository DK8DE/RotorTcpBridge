#define MyAppName "RotorTcpBridge"
; MyAppVersion kann per Kommandozeile überschrieben werden:
;   ISCC.exe /DMyAppVersion=1.3 Installer.iss
#ifndef MyAppVersion
  #define MyAppVersion "1.0"
#endif
#define MyAppPublisher "Joerg Koerner DK8DE"
#define MyAppURL "https://github.com/dk8de/RotorTcpBridge"
; Projektroot: standardmäßig Ordner der Installer.iss (GitHub Actions, lokaler Build).
; Optional überschreiben: ISCC /DMyProjDir=C:\pfad\zum\repo Installer.iss
#ifndef MyProjDir
  #define MyProjDir SourcePath
#endif
#define MySourceDir MyProjDir + "/dist/RotorTcpBridge"
#define MyExeName "RotorTcpBridge.exe"
#define MyAppIcon MySourceDir + "/_internal/rotortcpbridge/rotor.ico"
#define MyInternalDir MySourceDir + "/_internal/rotortcpbridge"
; AppId NIEMALS ändern – wird für Upgrade/Deinstallation benötigt
#define MyAppId "A219D4FA-6E44-4A8E-A4D8-7DF7799632F8"

[Setup]
AppId={{{#MyAppId}}
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
OutputDir={#MyProjDir}\dist\installer
OutputBaseFilename=RotorTcpBridge-Setup-{#MyAppVersion}
Compression=lzma
SolidCompression=yes
WizardStyle=modern
; Linkes Bild nur Willkommen + Fertig: hohes Format (Seitenverhältnis ~164:314), kein Quadrat — sonst streckt Inno und wirkt verzerrt.
; Siehe https://jrsoftware.org/ishelp/topic_setup_wizardimagefile.htm (z. B. ≥240×459).
WizardImageFile={#MyProjDir}\Installer.png
; Oben rechts auf den übrigen Seiten (Lizenz, Ordner, …): quadratisch — https://jrsoftware.org/ishelp/topic_setup_wizardsmallimagefile.htm
WizardSmallImageFile={#MyProjDir}\InstallerSmall.png
PrivilegesRequired=admin
SetupIconFile={#MyAppIcon}
UninstallDisplayIcon={app}\{#MyExeName}
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile={#MyProjDir}\LICENSE.txt
; Ältere Version wird per Code-Abschnitt automatisch deinstalliert (siehe unten).
; InstallMode=upgrade würde Dateien überschreiben, aber nicht löschen.
CloseApplications=yes

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

{ ── Alte Version automatisch deinstallieren ─────────────────────────────────
  Wird aufgerufen bevor der Setup-Assistent startet.
  Sucht den Uninstaller der vorherigen Version über die Registry und
  führt ihn lautlos aus. Benutzerdaten (config.json in AppData) bleiben erhalten,
  da der Uninstaller nur das Programmverzeichnis bereinigt. }
procedure UninstallOldVersion();
var
  sRegKey:     String;
  sUninstall:  String;
  iResultCode: Integer;
begin
  sRegKey := 'Software\Microsoft\Windows\CurrentVersion\Uninstall\' +
             '{#MyAppId}' + '_is1';

  sUninstall := '';
  { 64-Bit-Registry bevorzugen (App wird im 64-Bit-Modus installiert) }
  if not RegQueryStringValue(HKLM64, sRegKey, 'UninstallString', sUninstall) then
    RegQueryStringValue(HKLM,   sRegKey, 'UninstallString', sUninstall);

  if sUninstall <> '' then
  begin
    sUninstall := RemoveQuotes(sUninstall);
    { /SILENT  – keine Benutzeroberfläche
      /NORESTART – kein automatischer Neustart
      ewWaitUntilTerminated – warten bis Deinstallation abgeschlossen }
    Exec(sUninstall, '/SILENT /NORESTART', '', SW_HIDE,
         ewWaitUntilTerminated, iResultCode);
  end;
end;

function InitializeSetup(): Boolean;
begin
  UninstallOldVersion();
  Result := True;
end;

{ ── Sprache in Konfiguration setzen ─────────────────────────────────────────
  Nur bei Erstinstallation (keine config.json vorhanden).
  Bei Upgrade bleibt die bestehende Spracheinstellung des Benutzers erhalten.
  Zusätzlich: Kompass-Stromanalyse-Ringe (AZ/EL) standardmäßig aus — vollständige
  Defaults ergänzt die Anwendung beim Start ohnehin per load_config. }
procedure SetLanguageInConfig();
var
  ConfigDir:  String;
  ConfigFile: String;
  LangCode:   String;
  Content:    String;
begin
  if ActiveLanguage = 'english' then
    LangCode := 'en'
  else
    LangCode := 'de';

  ConfigDir  := ExpandConstant('{userappdata}\RotorTcpBridge');
  ConfigFile := ConfigDir + '\config.json';

  ForceDirectories(ConfigDir);

  if not FileExists(ConfigFile) then
  begin
    Content := '{' + #13#10 +
               '  "ui": {' + #13#10 +
               '    "language": "' + LangCode + '",' + #13#10 +
               '    "compass_strom_az": false,' + #13#10 +
               '    "compass_strom_el": false,' + #13#10 +
               '    "compass_heatmap_az": "off",' + #13#10 +
               '    "compass_heatmap_el": "off",' + #13#10 +
               '    "compass_heatmap_az_modes": []' + #13#10 +
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
