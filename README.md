# RotorTcpBridge

**RotorTcpBridge** ist eine Desktop-Anwendung (**Python 3.10+**, **Qt 6 / PySide6**), die als **Brücke** zwischen **Rotor-Hardware** (typisch **RS485**, SPID-kompatibles ASCII-Telegramm-Protokoll) und **PC-Software** arbeitet. Azimut- und optional Elevationsachsen lassen sich steuern und überwachen; **Kompass**, **Karte**, **Statistik**, **Wetter**, **Logging** und Anbindungen an **UcxLog**, **AirScout/KST**, **PstRotator** (TCP oder UDP) sind integriert.

**Version:** siehe `rotortcpbridge/version.py` (z. B. für Anzeige/Installer).

---

## Inhaltsverzeichnis

1. [Funktionen im Überblick](#funktionen-im-überblick)  
2. [Systemvoraussetzungen](#systemvoraussetzungen)  
3. [Installation und Start](#installation-und-start)  
4. [Konfiguration (`config.json`)](#konfiguration-configjson)  
5. [Hardware](#hardware)  
6. [Schnittstellen zu anderer Software](#schnittstellen-zu-anderer-software)  
7. [Benutzeroberfläche](#benutzeroberfläche)  
8. [Kompass und Karte](#kompass-und-karte)  
9. [Entwicklung & Qualität](#entwicklung--qualität)  
10. [Windows-Build (Installer)](#windows-build-installer)  
11. [Lizenz](#lizenz)

---

## Funktionen im Überblick

| Bereich | Beschreibung |
|--------|----------------|
| **Hardware** | Verbindung per **TCP** (IP + Port) oder **seriell (COM)** zum Bus/Adapter; **Master-/Slave-IDs** für AZ und EL. |
| **Rotor steuern** | Position, Referenz (**SETREF**), Stopp, PWM, frei **konfigurierbare RS485-Befehle** (Befehlsfenster). |
| **Oberfläche** | Hauptfenster mit Status (Hardware, PST-TCP, UDP-Dienste), Achsen-AZ/EL, Menüs: **Kompass**, **Karte**, **Einstellungen**, **Befehle**, **Statistik**, **Wetter**, **Log**, **Warnungen/Fehler**. |
| **Kompass** | IST-/SOLL-Anzeige, mehrere **Antennen** mit Versatz/Öffnung/Reichweite, optional **Wind**, **Strom-Heatmap** (ACCBINS). |
| **Karte** | **Leaflet** (Qt **WebEngine**), Standort, **Antennen-Beams**, Grayline, **Offline-Karten**, Klick → **Peilung** an den Rotor, optional **Maidenhead-Locator**. |
| **PST (TCP)** | Integrierter **PST-kompatibler TCP-Server** (getrennte Ports AZ/EL) für Software wie **PstRotator** – in den Einstellungen **ein-/ausschaltbar**. |
| **UDP UcxLog** | Empfängt **XML** von UcxLog (Standard-Port **12040**), um den Rotor aus der Log-/Contest-Software anzufahren. |
| **UDP PST-Emulator** | **Ersatz für PstRotator** auf der UDP-Seite: dasselbe Protokoll wie **PstRotatorAz** – Programme, die per UDP mit **PstRotator** sprechen, funktionieren mit RotorTcpBridge **ohne** installierten PstRotator (Standard-Port **12000**). |
| **UDP AirScout/KST** | Empfängt **ASWATCHLIST** / **ASSETPATH** (z. B. AirScout), Anzeige auf der Karte. |
| **Internationalisierung** | Sprache **Deutsch** / **Englisch** (Einstellungen). |

---

## Systemvoraussetzungen

- **Python** ≥ **3.10** (siehe `pyproject.toml`: `requires-python = ">=3.10"`).
- **Abhängigkeiten:** siehe `requirements.txt` (u. a. **PySide6**, **pyserial**). Die Kartenansicht nutzt **Qt WebEngine** (Bestandteil der PySide6-Pakete).
- **Betrieb:** primär **Windows**; Konfigurationspfad nutzt u. a. `%APPDATA%` (siehe unten).

---

## Installation und Start

Abhängigkeiten installieren:

```bash
pip install -r requirements.txt
```

Applikation aus dem **Projektroot** starten:

```bash
python run.py
```

Alternativ als Modul:

```bash
python -m rotortcpbridge
```

---

## Konfiguration (`config.json`)

### Speicherort

- **Windows:** `%APPDATA%\RotorTcpBridge\config.json`  
- **Linux/macOS:** typisch `~/.config/RotorTcpBridge/config.json` (über `APPDATA` bzw. Home-Logik in `app_config.py`).

Die Datei wird beim **ersten Start** angelegt, falls sie nicht existiert. Anschließend werden **fehlende Schlüssel** bei jedem Laden mit den **aktuellen Programm-Defaults** ergänzt (Merge).

### Erste Installation (ohne bestehende `config.json`)

Beim **allerersten** Speichern werden u. a. gesetzt:

- Alle **UDP-Listen-Adressen** auf **`0.0.0.0`** (alle Schnittstellen).
- **Ziel-IP** für den **UDP PST-Emulator** (`udp_pst_send_host`) auf die **Subnetz-Broadcast-Adresse** des Rechners (typisch `x.y.z.255`, siehe `net_utils.ipv4_subnet_broadcast_default()`). Ohne nutzbares IPv4-Netzwerk kann das Fallback **`127.0.0.1`** sein.

### Standardwerte (Auszug aus `DEFAULT_CONFIG` in `app_config.py`)

Diese Werte gelten für **neue** Installationen bzw. fehlende Felder nach Updates (Stand Code – bei Abweichungen immer `app_config.py` als Quelle nutzen):

| Bereich | Einstellung | Standard (Kurz) |
|--------|-------------|-------------------|
| **UI** | `force_dark_mode` | **ein** |
| | `language` | `de` |
| | UDP **UcxLog** / **AirScout** / **PST-Emulator** | jeweils **aktiviert** (`*_enabled`: true) |
| **PST (TCP)** | `pst_server.enabled` | **aus** (SPID BIG-RAS-Emulation über TCP getrennt vom UDP-Emulator) |
| **Hardware** | `hardware_link.mode` | **`com`** (seriell) |
| **Rotor-Bus** | `enable_az` / `enable_el` | **AZ an**, **EL aus** |
| **UDP-Ports** | UcxLog / AirScout / PST-UDP | **12040** / **9872** / **12000** |

**Hinweis:** In den Einstellungen schließen sich **PST TCP-Server** (SPID BIG-RAS) und **UDP PST-Emulator** gegenseitig aus – es darf nur jeweils einer aktiv sein (oder beide aus).

---

## Hardware

- Konfiguration unter **`hardware_link`**: Modus **`tcp`** oder **`com`**, dazu IP/Port bzw. COM-Port und **Baudrate**.
- **`rotor_bus`**: **Master-ID**, Slave-IDs für **Azimut** und **Elevation**, **enable_az** / **enable_el**.
- Kommunikation über **RS485-ASCII-Telegramme** (`#…$`), Polling-Intervalle in **`polling_ms`**.

---

## Schnittstellen zu anderer Software

### PST-TCP-Server (PstRotator-kompatibel)

- **TCP-Server** mit getrennten Ports für **AZ** und **EL** (Defaults z. B. **4001** / **4002**, Host konfigurierbar).
- **Binäres PST-Protokoll** – Anpassung für Software wie **PstRotator** (Rotor-Typ z. B. **SPID BIG-RAS** in der PstRotator-Konfiguration).
- **Standard nach Erstinstallation:** **deaktiviert** (`pst_server.enabled`: false). In den Einstellungen einschalten, wenn benötigt.

### UDP PST-Emulator

Die Emulation ist **nicht** nur eine technische Nachbildung des PST-UDP-Protokolls – ihr **Zweck** ist: **Sie benötigen keinen PstRotator mehr**, um Software anzubinden, die für die Kommunikation mit **PstRotator** über **UDP** ausgelegt ist. **Jedes Programm**, das auf dieselbe Weise per UDP mit **PstRotator** spricht, ist mit **RotorTcpBridge** kompatibel; die Bridge übernimmt die Rolle des UDP-Gegenparts und leitet zur echten Rotor-Hardware.

Technisch:

- **UDP**-Listener auf **`udp_pst_port`** (Standard **12000**).
- Steuerbefehle und Antworten wie bei **PstRotatorAz**; Positionsmeldungen u. a. an **Ziel-IP:Port+1**.
- **Ziel-IP:** leer → Laufzeit **Subnetz-Broadcast**; manuell IPv4 oder `127.0.0.1` möglich.
- **Standard:** in den Defaults **aktiviert** (`udp_pst_enabled`), unabhängig vom PST-**TCP**-Server.

### UDP UcxLog

- Lauscht auf konfigurierbare **Listen-IP** und **Port** (`udp_ucxlog_listen_host`, `udp_ucxlog_port`; Standard **0.0.0.0:12040**).
- XML von UcxLog (z. B. `<Rotor><Azimut>…</Azimut></Rotor>`).

### UDP AirScout / KST

- Empfängt **ASWATCHLIST** / **ASSETPATH** (Standard **0.0.0.0:9872**).
- Stationen können auf der **Karte** dargestellt werden.

---

## Benutzeroberfläche

- **Hauptfenster:** LEDs und Statuszeilen für **Hardware**, **PST (TCP)**, **UDP UcxLog**, **UDP PST-Emulator**, **AirScout/KST**.
- **Hardware-Status:** bei TCP-Verbindung mit laufendem PST-TCP z. B. **„Verbunden über TCP“** + IP:Port (ohne veraltete „PST-Server“-Bezeichnung in dieser Zeile); bei nur Hardware **„verbunden“** / **„getrennt“** mit Details.
- **Einstellungen:** Tabs u. a. **Oberfläche** (Dark Mode, Sprache, UDP-Blöcke, Kalibrierung, Standort, Karte), **Verbindung** (PST-TCP, Hardware, Achsen aktiv), **Antenne** (Namen, Versätze).

---

## Kompass und Karte

- **Kompass:** eigenes Fenster, **Azimut** (und optional **Elevation**), Favoriten, Antennenwahl.
- **Karte:** OSM/CARTO oder **Offline-Tiles** (Ordner **KartenLight** / **KartenDark** unter `rotortcpbridge/` – je nach Theme/Dark-Mode).
- **Offline-Karten vorbereiten** (optional): Skripte im Ordner **`tools/`**:
  - `python tools/karten_download.py` – helle Tiles  
  - `python tools/karten_dark_download.py` – dunkle Tiles  
  - `python tools/leaflet_download.py` – Leaflet/Maidenhead-Assets in `rotortcpbridge/static`  
  - `python tools/make_backup.py` – Backup-Hilfe (siehe Skript)

---

## Entwicklung & Qualität

### Tests

```bash
pip install -r requirements-dev.txt
pytest
```

oder:

```bash
python run_tests.py
python run_tests.py -v
```

Konfiguration: **`pytest.ini`**, Tests unter **`test/`** (ohne echte Hardware/GUI).

### Linting (Ruff)

Konfiguration in **`pyproject.toml`**:

```bash
ruff check rotortcpbridge test
ruff format rotortcpbridge test
```

---

## Windows-Build (Installer)

- **PyInstaller**-Spezifikation: **`RotorTcpBridge.spec`** (Einstieg `run.py`).
- **Inno Setup** für das Setup-Programm – **ISCC** muss verfügbar sein (siehe Projekt-`build.ps1`).

```powershell
.\build.ps1
.\build.ps1 -SkipInstaller   # nur PyInstaller, kein Installer
```

Details und Versionen ggf. im Skript bzw. in der Inno-Setup-Datei nachlesen.

---

## Lizenz

Siehe **`LICENSE.txt`** im Projektverzeichnis.

---

## Projektstruktur (Kurz)

| Pfad | Inhalt |
|------|--------|
| `rotortcpbridge/` | Hauptcode (UI, Protokoll, UDP-Listener, PST-Server, …) |
| `rotortcpbridge/locales/` | `de.json` / `en.json` |
| `test/` | Unit-Tests |
| `tools/` | Hilfsskripte (Karten, Backup, …) |
| `run.py` | Startskript |

Bei Abweichungen zwischen dieser README und dem **Code** gilt immer der **Code** als maßgeblich.
