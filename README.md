# RotorTcpBridge

**RotorTcpBridge** ist eine Desktop-Anwendung (Python, **Qt/PySide6**), die als **Brücke** zwischen eurer **Rotor-Hardware** (typisch **RS485** / SPID-kompatibles Protokoll) und **PC-Software** fungiert. Ihr könnt **Azimut- und Elevationsachsen** ansteuern, den Status einsehen, **Karten und Kompass** nutzen und mit Programmen wie **PstRotator** oder **UcxLog** zusammenarbeiten – ohne jedes Mal dieselbe Schnittstelle neu zu bauen.

---

## Was das Programm macht (Überblick)

| Bereich | Kurzbeschreibung |
|--------|-------------------|
| **Hardware** | Verbindung per **TCP** oder **seriell (COM)** zum RS485-Bus; konfigurierbare **Master-/Slave-IDs** für AZ und EL. |
| **Rotor steuern** | Positionsvorgaben, Referenz (**SETREF**), Stopp, PWM, Befehle aus dem Protokoll – zentral über den **RotorController** und das **Hardware-Backend**. |
| **Oberfläche** | Hauptfenster mit Status (Verbindung, PST, ggf. UDP), Achsen-Anzeigen, Menüs zu **Kompass**, **Karte**, **Einstellungen**, **Befehle**, **Statistik**, **Wetter**, **Log**. |
| **Kompass** | Visuelle **IST-/SOLL-Anzeige**, Favoriten, mehrere **Antennen** mit Versatz, optional **Windrose** / Strom-Heatmap. |
| **Landkarte** | **Leaflet**-Karte mit Standort, **Antennen-Beams** (Öffnung, Reichweite), Grayline, optional **Offline-Karten**, Klick → **Rotor auf Peilung**. |
| **Fremdsoftware** | **PST-TCP-Server** (wie PstRotatorAz), **UDP-PST-Emulation**, **UDP UcxLog**-Anbindung. |
| **Konfiguration** | Persistente **`config.json`** unter den Anwendungsdaten; Sprache **DE/EN**. |

---

## Hardware und Bus

- Anbindung an den Rotor über **`hardware_link`**: **TCP** (IP + Port) oder **COM** (Port + Baudrate).
- **`rotor_bus`**: Master-ID, Slave-IDs für **Azimut** und **Elevations**-Achse; Achsen können einzeln deaktiviert werden (`enable_az` / `enable_el`).
- Das Programm sendet **Telegramme im RS485-ASCII-Format** (`#SRC:DST:CMD:PARAMS:CS$`) und pollt regelmäßig Position, Fehler, Warnungen, Telemetrie (konfigurierbare Intervalle in **`polling_ms`**).

---

## Rotor bedienen und einstellen

- **Hauptfenster**: Verbindungs-LEDs (Hardware, PST, optional UcxLog- und PST-UDP-LED), Textfelder für Server/Ports, **AZ/EL-Gruppen** mit Ist-, Soll- und Statusanzeigen (je nach Konfiguration sichtbar).
- **Einstellungen**: Hardware, PST-Ports, UI (Sprache, Dark Mode, Karte, UDP-Features), Antennen-Namen und **Versätze**, Standort für Karte/Kompass, u. a.
- **Befehle** („Command Buttons“): Schnellzugriff auf frei konfigurierbare **RS485-Befehle** (Ziel-Slave, Kommando, Parameter).
- **Referenz / Homing**, Fehler- und Warnungs-Popups, Logging in Datei und **Log-Fenster**.

---

## Kompass

- Eigens **Kompass-Fenster** mit Peilung, Antennenwahl (bis zu **drei Antennen** mit individuellem **Azimut-Versatz** in der Anzeige).
- **Favoriten** (gespeicherte Ziele), Abgleich mit der echten Rotorposition.
- Optional: **Wind**-Einbindung, Darstellung von **Last/Strom** (Bins) am Kompassrand.

---

## Landkarte (Map)

- **Interaktive Karte** (OpenStreetMap/CARTO oder **Offline-Tiles** aus lokalen Ordnern).
- **Antennenstandort**, **Beam**-Darstellung (Sektor) für die gewählte Antenne, **Grayline** (Tag-/Nacht-Grenze).
- **Klick auf die Karte**: Berechnung der **Peilung** zum Punkt und Vorgabe an den Rotor (Azimut).
- Optional: **Maidenhead-Locator**-Overlay, **Höhenprofil**-Fenster (Terrain, Sichtlinie – je nach Konfiguration und Datenquellen).

---

## Kommunikation mit anderen Programmen

### PST-TCP-Server (PstRotatorAz-kompatibel)

- Die Bridge kann einen **TCP-Server** starten (getrennte Ports für **AZ** und **EL**), der das erwartete **binäre PST-Protokoll** spricht – vergleichbar mit **PstRotatorAz**.
- So können **PstRotator** oder andere Clients den Rotor **über localhost** steuern, während die Bridge die **echte RS485-Verbindung** zum Antrieb übernimmt.
- In den **Einstellungen** kann der PST-Server **ein- und ausgeschaltet** werden.

### UDP – PstRotatorAz-Emulation („UDP PST-Rotator“)

- Optional: **UDP**-Listener auf konfigurierbarem Port (Standard z. B. **12000**).
- Versteht typische **PST-XML**-Telegramme (`<PST><AZIMUTH>…</AZIMUTH></PST>`, STOP, PARK, Abfragen `AZ?` / `TGA?` …).
- Sendet Positionsmeldungen im Stil **`AZ:xxx` / `TGA:xxx`** an **Ziel-IP : Port+1**.
  - **Nichts konfiguriert / leeres Feld**: automatisch **Subnetz-Broadcast** in der Form **`x.y.z.255`** (aus der lokalen IPv4 abgeleitet, typisch /24-Heimnetz).
  - **`127.0.0.1`** – nur der **gleiche PC** wie die Bridge.
  - **Anderer Rechner im LAN**: Ziel-IP **manuell eintragen** (IPv4 des Empfängers), oder **`255.255.255.255`** für **Broadcast** im lokalen Subnetz (Router leiten Broadcast in der Regel **nicht** zwischen Subnetzen).
- **Aktivierung**, **Port** und **Ziel-IP** in den Einstellungen (`udp_pst_enabled`, `udp_pst_port`, `udp_pst_send_host`).

### UDP – UcxLog

- Optional: Listener für **XML-Positionsdaten** von **UcxLog** (konfigurierbarer Port, z. B. **12040**), um den Rotor aus der Log-/Contest-Software heraus zu fahren.

---

## Technische Grundlagen

- **Sprache**: Python 3.10+ empfohlen.
- **GUI**: **PySide6** (Qt6).
- **Konfiguration**: `%APPDATA%\RotorTcpBridge\config.json` (Windows) bzw. entsprechend unter Linux/macOS.
- **Protokoll / Logik**: u. a. `rotor_controller`, `rs485_protocol`, `hardware_client`.

---

## Start

Voraussetzung: Abhängigkeiten installieren:

```bash
pip install -r requirements.txt
```

Applikation starten (Projektroot):

```bash
python run.py
```

Oder Modul:

```bash
python -m rotortcpbridge
```

---

## Tests (Entwicklung)

```bash
pip install -r requirements-dev.txt
pytest
```

Alle Tests in einem Aufruf:

```bash
python run_tests.py
python run_tests.py -v
```

Die Tests prüfen u. a. Winkel-Hilfen, Geografie, PST-UDP-Positionslogik, RS485-Telegramme und Parser – **ohne** echte Hardware und **ohne** GUI.

---

## Code-Qualität (optional, vor Commit)

Konfiguration in **`pyproject.toml`** (Tool: **Ruff**):

```bash
ruff check rotortcpbridge tests
```

Optional formatieren:

```bash
ruff format rotortcpbridge tests
```

---

## Lizenz

Siehe **`LICENSE.txt`** im Projektverzeichnis.
