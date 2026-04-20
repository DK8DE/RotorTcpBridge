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
; ── com0com-Lizenzseite ──────────────────────────────────────────────────
german.Com0ComPageCaption=com0com Null-Modem-Treiber (optional)
english.Com0ComPageCaption=com0com Null-Modem Driver (optional)
german.Com0ComPageDescription=Virtuelle COM-Port-Paare für die PST-Serial-Brücke. Bitte lies und bestätige die Lizenzbedingungen.
english.Com0ComPageDescription=Virtual COM-port pairs for the PST serial bridge. Please read and confirm the license terms.
german.Com0ComLicenseHeader=com0com © Vyacheslav Frolov — GNU General Public License v2.0%nQuellcode: https://sourceforge.net/projects/com0com/
english.Com0ComLicenseHeader=com0com © Vyacheslav Frolov — GNU General Public License v2.0%nSource code: https://sourceforge.net/projects/com0com/
german.Com0ComAccept=com0com mitinstallieren – Ich akzeptiere die Bedingungen der GNU GPL v2
english.Com0ComAccept=Install com0com — I accept the terms of the GNU GPL v2
german.Com0ComDecline=com0com nicht installieren
english.Com0ComDecline=Do not install com0com
german.Com0ComAlreadyInstalled=com0com ist bereits installiert – eine erneute Installation wird übersprungen.
english.Com0ComAlreadyInstalled=com0com is already installed — reinstallation will be skipped.
german.Com0ComInstalling=Installiere com0com Null-Modem-Treiber...
english.Com0ComInstalling=Installing com0com null-modem driver...
german.Com0ComRestartNote=Für com0com wird nach der Installation ein Windows-Neustart empfohlen. Sie können den Neustart am Ende des Setups auswählen.
english.Com0ComRestartNote=A Windows restart is recommended after installing com0com. You can choose to restart at the end of setup.

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
; Alle vom PyInstaller gebauten Dateien (EXE, DLLs, _internal/...)
; Enthält bereits: Antenne.png, Antenne_T.png, windPfeil.png, rotor.ico,
;                  locales/, static/ (Leaflet), KartenLight/, KartenDark/
Source: "{#MySourceDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

; ── com0com (Null-Modem-Treiber, optional) ────────────────────────────────
; Die signierten Original-Installer von Vyacheslav Frolov (GPLv2) werden bei
; Zustimmung auf der Lizenzseite ins TEMP-Verzeichnis kopiert, von dort silent
; ausgeführt (/S) und danach wieder gelöscht. Unverändert — so bleibt die
; Kernel-Treiber-Signatur intakt.
Source: "{#MyProjDir}\comOcom\Setup_com0com_v3.0.0.0_W7_x64_signed.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall; Check: ShouldInstallCom0Com and Is64BitInstallMode
Source: "{#MyProjDir}\comOcom\Setup_com0com_v3.0.0.0_W7_x86_signed.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall; Check: ShouldInstallCom0Com and (not Is64BitInstallMode)
; Lizenz-Nachweis (GPL-Pflicht): immer mit ausliefern, unabhängig von der Wahl.
Source: "{#MyProjDir}\comOcom\license.txt"; DestDir: "{app}\licenses\com0com"; DestName: "LICENSE.txt"; Flags: ignoreversion
; Kopie der Lizenzdatei zur Laufzeit des Setups (für die custom Lizenzseite).
Source: "{#MyProjDir}\comOcom\license.txt"; Flags: dontcopy

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyExeName}"; IconFilename: "{app}\_internal\rotortcpbridge\rotor.ico"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyExeName}"; Tasks: desktopicon; IconFilename: "{app}\_internal\rotortcpbridge\rotor.ico"

[Run]
; com0com silent installieren (/S) — nur wenn vom Nutzer akzeptiert und nicht
; bereits installiert. Dank {code:...} wählt Inno automatisch die x64- oder
; x86-Variante passend zur Installations-Architektur.
; AfterInstall: setzt ein Flag, damit NeedRestart() Inno automatisch die
; "Jetzt / später neu starten"-Seite am Ende des Setups zeigen lässt.
Filename: "{code:GetCom0ComSetupExe}"; Parameters: "/S"; StatusMsg: "{cm:Com0ComInstalling}"; Flags: waituntilterminated; Check: ShouldInstallCom0Com; AfterInstall: MarkCom0ComInstalled

Filename: "{app}\{#MyExeName}"; Description: "{cm:RunAfterInstall}"; Flags: nowait postinstall skipifsilent

[Code]

var
  Com0ComPage:         TWizardPage;
  Com0ComLicenseMemo:  TNewMemo;
  Com0ComRadioInstall: TNewRadioButton;
  Com0ComRadioSkip:    TNewRadioButton;
  Com0ComHeaderLbl:    TNewStaticText;
  Com0ComRestartLbl:   TNewStaticText;
  Com0ComInstallDone:  Boolean;

{ ── com0com: Hilfsfunktionen ──────────────────────────────────────────────── }

function Com0ComAlreadyInstalled(): Boolean;
begin
  { Beide Registry-Sichten (64-bit + 32-bit/WOW64) prüfen – identisch zu
    find_setupc() in rotortcpbridge/com0com.py. }
  Result := RegKeyExists(HKLM, 'SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\com0com') or
            RegKeyExists(HKLM, 'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\com0com');
end;

function ShouldInstallCom0Com(): Boolean;
begin
  { Wird von [Files]/[Run]-Check aufgerufen. Nur installieren, wenn:
    - die Lizenzseite überhaupt existiert (bei /VERYSILENT kann sie fehlen)
    - der Accept-Radio gewählt wurde
    - com0com nicht ohnehin schon installiert ist. }
  Result := Assigned(Com0ComRadioInstall) and
            Com0ComRadioInstall.Checked and
            (not Com0ComAlreadyInstalled());
end;

function GetCom0ComSetupExe(Param: String): String;
begin
  if Is64BitInstallMode then
    Result := ExpandConstant('{tmp}\Setup_com0com_v3.0.0.0_W7_x64_signed.exe')
  else
    Result := ExpandConstant('{tmp}\Setup_com0com_v3.0.0.0_W7_x86_signed.exe');
end;

procedure MarkCom0ComInstalled();
begin
  { Wird via [Run] AfterInstall: aufgerufen, nachdem der com0com-Installer
    durchgelaufen ist. Setzt das Flag, das NeedRestart() unten auswertet,
    damit Inno automatisch die Neustart-Auswahl auf der letzten Seite zeigt. }
  Com0ComInstallDone := True;
end;

function NeedRestart(): Boolean;
begin
  { Event-Function von Inno Setup. True -> Setup zeigt auf der finalen Seite
    die Optionen "Jetzt neu starten" / "Später neu starten".
    Der com0com-Kernel-Treiber wird zwar von Windows-PnP oft schon ohne
    Reboot geladen, aber erst nach einem Neustart ist er zuverlaessig aktiv
    — deshalb fragen wir sicherheitshalber nach. }
  Result := Com0ComInstallDone;
end;

function CleanLicenseText(const S: String): String;
var
  Anchor: Integer;
begin
  { Die mitgelieferte license.txt ist eine CVS/RCS-Datei des com0com-Projekts
    mit RCS-Header (head, access, symbols, ... ,text @). Wir zeigen nur den
    eigentlichen GPL-Text ab "GNU GENERAL PUBLIC LICENSE" an und
    entfernen den trailing "@@"-Marker, damit das Memo nicht verwirrt. }
  Result := S;
  Anchor := Pos('GNU GENERAL PUBLIC LICENSE', Result);
  if Anchor > 1 then
    Result := Copy(Result, Anchor, MaxInt);
  { Trailing RCS-"@@" — falls vorhanden — inkl. restlichem "@"-Rauschen kappen. }
  Anchor := Pos(#10'@@', Result);
  if Anchor > 0 then
    Result := Copy(Result, 1, Anchor - 1);
end;

procedure LoadCom0ComLicense();
var
  LicPath: String;
  Buf:     AnsiString;
begin
  LicPath := ExpandConstant('{tmp}\license.txt');
  ExtractTemporaryFile('license.txt');
  if LoadStringFromFile(LicPath, Buf) then
    Com0ComLicenseMemo.Lines.Text := CleanLicenseText(String(Buf))
  else
    Com0ComLicenseMemo.Lines.Text :=
      'com0com is distributed under the GNU General Public License v2.0.' + #13#10 +
      'Full text: https://www.gnu.org/licenses/old-licenses/gpl-2.0.html';
end;

procedure InitializeWizard();
var
  RestartTop, RadioTop, RadioHeight, RestartHeight: Integer;
begin
  { Eigene Lizenzseite direkt hinter der Haupt-Lizenzseite einhängen. }
  Com0ComPage := CreateCustomPage(wpLicense,
    ExpandConstant('{cm:Com0ComPageCaption}'),
    ExpandConstant('{cm:Com0ComPageDescription}'));

  Com0ComHeaderLbl := TNewStaticText.Create(Com0ComPage);
  Com0ComHeaderLbl.Parent  := Com0ComPage.Surface;
  Com0ComHeaderLbl.Left    := 0;
  Com0ComHeaderLbl.Top     := 0;
  Com0ComHeaderLbl.Width   := Com0ComPage.SurfaceWidth;
  Com0ComHeaderLbl.AutoSize := False;
  Com0ComHeaderLbl.Height  := ScaleY(32);
  Com0ComHeaderLbl.Caption := ExpandConstant('{cm:Com0ComLicenseHeader}');

  RadioHeight   := ScaleY(22);
  RestartHeight := ScaleY(32);

  { Layout von unten nach oben: Restart-Hinweis ganz unten, darueber die
    beiden Radios, darueber das Lizenz-Memo, darueber der Header. }
  RestartTop := Com0ComPage.SurfaceHeight - RestartHeight;
  RadioTop   := RestartTop - (RadioHeight * 2) - ScaleY(4);

  Com0ComLicenseMemo := TNewMemo.Create(Com0ComPage);
  Com0ComLicenseMemo.Parent      := Com0ComPage.Surface;
  Com0ComLicenseMemo.Left        := 0;
  Com0ComLicenseMemo.Top         := Com0ComHeaderLbl.Top + Com0ComHeaderLbl.Height + ScaleY(4);
  Com0ComLicenseMemo.Width       := Com0ComPage.SurfaceWidth;
  Com0ComLicenseMemo.Height      := RadioTop - Com0ComLicenseMemo.Top - ScaleY(8);
  Com0ComLicenseMemo.ScrollBars  := ssVertical;
  Com0ComLicenseMemo.ReadOnly    := True;
  Com0ComLicenseMemo.WantReturns := False;
  Com0ComLicenseMemo.WordWrap    := True;

  Com0ComRadioInstall := TNewRadioButton.Create(Com0ComPage);
  Com0ComRadioInstall.Parent  := Com0ComPage.Surface;
  Com0ComRadioInstall.Left    := 0;
  Com0ComRadioInstall.Top     := RadioTop;
  Com0ComRadioInstall.Width   := Com0ComPage.SurfaceWidth;
  Com0ComRadioInstall.Height  := RadioHeight;
  Com0ComRadioInstall.Caption := ExpandConstant('{cm:Com0ComAccept}');
  Com0ComRadioInstall.Checked := True;

  Com0ComRadioSkip := TNewRadioButton.Create(Com0ComPage);
  Com0ComRadioSkip.Parent  := Com0ComPage.Surface;
  Com0ComRadioSkip.Left    := 0;
  Com0ComRadioSkip.Top     := RadioTop + RadioHeight;
  Com0ComRadioSkip.Width   := Com0ComPage.SurfaceWidth;
  Com0ComRadioSkip.Height  := RadioHeight;
  Com0ComRadioSkip.Caption := ExpandConstant('{cm:Com0ComDecline}');

  Com0ComRestartLbl := TNewStaticText.Create(Com0ComPage);
  Com0ComRestartLbl.Parent  := Com0ComPage.Surface;
  Com0ComRestartLbl.Left    := 0;
  Com0ComRestartLbl.Top     := RestartTop;
  Com0ComRestartLbl.Width   := Com0ComPage.SurfaceWidth;
  Com0ComRestartLbl.AutoSize := False;
  Com0ComRestartLbl.Height  := RestartHeight;
  Com0ComRestartLbl.Caption := ExpandConstant('{cm:Com0ComRestartNote}');
  Com0ComRestartLbl.Font.Style := [fsItalic];

  LoadCom0ComLicense();

  { Wenn com0com schon installiert ist: Hinweis einblenden und Default auf
    "nicht installieren" setzen, damit der Nested-Installer nicht nochmal
    losläuft. Der User kann das trotzdem umstellen — der Run-Eintrag wird
    zusätzlich durch ShouldInstallCom0Com abgesichert. Der Neustart-Hinweis
    ist in diesem Fall nicht relevant und wird versteckt. }
  if Com0ComAlreadyInstalled() then
  begin
    Com0ComRadioInstall.Checked := False;
    Com0ComRadioSkip.Checked    := True;
    Com0ComHeaderLbl.Caption :=
      Com0ComHeaderLbl.Caption + #13#10 + ExpandConstant('{cm:Com0ComAlreadyInstalled}');
    Com0ComHeaderLbl.Height   := ScaleY(48);
    Com0ComLicenseMemo.Top    := Com0ComHeaderLbl.Top + Com0ComHeaderLbl.Height + ScaleY(4);
    Com0ComLicenseMemo.Height := RadioTop - Com0ComLicenseMemo.Top - ScaleY(8);
    Com0ComRestartLbl.Visible := False;
  end;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  { Auf der com0com-Seite muss genau eine Option gewählt sein. Durch das
    Default-Setting in InitializeWizard ist das praktisch immer der Fall,
    aber sicherheitshalber prüfen. }
  if (Com0ComPage <> nil) and (CurPageID = Com0ComPage.ID) then
  begin
    if (not Com0ComRadioInstall.Checked) and (not Com0ComRadioSkip.Checked) then
    begin
      MsgBox('Please choose whether to install com0com or skip it.',
             mbInformation, MB_OK);
      Result := False;
      Exit;
    end;
  end;
  Result := True;
end;

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
