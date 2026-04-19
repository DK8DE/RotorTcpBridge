# Rotor‑Controller – RS485‑Anleitung & Firmware‑Dokumentation (DE)

Stand: 2026-04-16 • Fokus: RS485‑Befehle, Einstellungen (INO), Kalibrierung/Statistik, Fehler & Warnungen. Die **Befehlstabelle** ist mit dem RotorTcpBridge‑Katalog abgeglichen (`command_catalog.py` / Befehlsfenster). Diese Datei ist mit `RotorTcpBridge/Protokoll/RotorController_RS485.html` abgestimmt.

## Inhaltsverzeichnis {#toc}

- [1. RS485‑Protokoll (Format, Dezimal, Checksumme)](#protocol)
- [2. Encoder‑Varianten (fest in der Firmware)](#encoders)
- [3. RS485‑Befehle – Tabelle](#cmd-table)
- [Hardwarecontroller Configuration (Display‑Controller)](#hw-controller-config)
- [4. RS485‑Befehle – Erklärung & Empfehlungen](#cmd-details)
- [5. INO‑Konfig‑Variablen – Tabelle](#vars-table)
- [6. INO‑Variablen – Erklärung & gute Werte](#vars-details)
- [7. Kalibrierung/Lastprofil/Temperatur – Tuning‑Anleitung](#tuning)
- [8. Warnungen](#warns)
- [9. Fehler](#errors)
- [10. Hinweise](#notes)

## 1. RS485‑Protokoll {#protocol}

Alle RS485‑Telegramme haben dieses Grundformat:

```
#<SRC>:<DST>:<CMD>:<PARAMS>:<CS>$
```

- **SRC** = Sender‑ID (Master)
- **DST** = Empfänger‑ID (Slave, z.B. 20)
- **CMD** = Befehl (Großbuchstaben)
- **PARAMS** = Nutzdaten. Mehrere Werte werden (wo sinnvoll) mit `;` getrennt.
- **CS** = Checksumme (einfache Plausibilität, nicht kryptografisch)

### Dezimalzahlen

Der Master darf Dezimalzahlen mit **Komma** oder **Punkt** senden. In Antworten verwendet die Firmware in der Regel das **Komma** (z.B. `10,5`).

### Checksumme (CS)

Die Checksumme ist bewusst einfach gehalten. Sie setzt sich aus **SRC + DST** und dem **letzten Zahlenwert** in `PARAMS` zusammen.

- Intern wird mit **0,01‑Auflösung** gerechnet (scaled100).
- Wenn `PARAMS` mehrere Zahlen hat (z.B. Listen), zählt **nur der letzte Wert** für die Checksumme.
- Wenn `PARAMS` gar keine Zahl enthält (z.B. `NOHW`), wird der Wert als 0 behandelt.

### Ablauf (Request/Response)

Das Protokoll ist als **Frage/Antwort** aufgebaut:

1. Der Master sendet ein Telegramm an eine Slave‑ID.
2. Der Slave prüft Format und Checksumme.
3. Wenn alles passt: Antwort mit `ACK_*`. Bei ungültigen Parametern: `NAK_*` mit einer kurzen Begründung.
4. Bei Befehlen, die eine Bewegung starten (z.B. `SETPOSDG`): das `ACK` kommt sofort, die Bewegung läuft danach in der Hauptschleife weiter.

### IDs (SRC/DST)

- **Slave‑ID**: ist die Zieladresse (`DST`) und kann mit `SETID` geändert werden.
- **Master‑ID**: ist immer die Quelladresse (`SRC`) der Anfrage. Der Slave antwortet an genau diese ID (die Antwort hat dann `SRC=Slave` und `DST=Master`).
- Die Master‑ID muss also nicht `0` sein. Wenn mehrere Master existieren, antwortet der Slave jeweils an den Master, der die Anfrage geschickt hat.
- **Zwei Master auf derselben RS485‑Leitung** (z.B. PC mit eigenem USB‑RS485‑Adapter am gleichen Bus wie der Display‑Controller — der Datenverkehr des PC muss dafür **nicht** „durch den Controller“ geroutet sein): Der PC soll eine **andere Master‑ID** nutzen als der Controller (`config.json` `master_id` am Display). Sonst sind `ACK_GETPOSDG`/`ACK_SETPOSDG` mit gleichem `DST` nicht dem richtigen Gerät zuordenbar — Pending und Ist‑Anzeige können stolpern, wenn beide viel abfragen.
- **Nur Mitlausch per USB** (PC liest nur den Mitschnitt dessen, was der Controller ohnehin sendet/empfängt, ohne selbst auf dieselbe RS485‑Leitung zu senden): Dann betrifft das obige Zuordnungsproblem den PC‑Pfad nicht; die Ursachen für abweichendes UI‑Verhalten liegen woanders (z.B. Buslast, Timing, SETPOSCC).

### Beispiele

**Beispiel 1: Abfrage der Position** (Master‑ID = 7, Slave‑ID = 20). In der Anfrage ist der letzte Zahlenwert `0`, daher ist `CS = 7 + 20 + 0 = 27`:

```
#7:20:GETPOSDG:0:27$
```

Antwort (Beispielwert 160,00°). Letzter Zahlenwert ist `160,00` → `CS = 20 + 7 + 160 = 187`:

```
#20:7:ACK_GETPOSDG:160,00:187,00$
```

**Beispiel 2: Positionsfahrt starten** (Ziel 160,00°). Letzter Zahlenwert ist `160,00` → `CS = 7 + 20 + 160 = 187`:

```
#7:20:SETPOSDG:160,00:187,00$
```

Antwort: `1` bedeutet „angenommen“. Letzter Zahlenwert ist `1` → `CS = 20 + 7 + 1 = 28`:

```
#20:7:ACK_SETPOSDG:1:28$
```

### Asynchrone Meldungen (ERR)

Wenn ein **Fehler** auftritt (z.B. Blockade, Not‑Stop, Stall), stoppt der Motor und der Slave sendet ein `ERR`‑Telegramm asynchron. Neuer Standard ist Broadcast (DST=255):

```
#20:255:ERR:16:291$
```

Im Beispiel ist der Fehlercode `16`. Die Checksumme ist `20 + 255 + 16 = 291`. Der Fehler bleibt aktiv, bis er quittiert wird (siehe `SETREF`).

**Wichtig:** Die Controller-Firmware behandelt `ERR` an `DST=255` gleich wie bisherige ERR-Meldungen. Zyklisches `GETERR` ist dafür nicht mehr erforderlich.

**Tipp:** Wenn von der Master‑Seite aus geparst werden soll, ist die Trennung so gedacht: Felder mit `:`, und innerhalb von `PARAMS` (falls mehrere Werte) mit `;`.

[↑ Inhaltsverzeichnis](#toc)

## 2. Encoder‑Varianten {#encoders}

Es gibt zwei Hardware‑Varianten, wie der Encoder montiert ist. Die Auswahl ist über RS485 möglich über **SETENCTYPE 1/2**. Axis hat die Id1 und Ring hat Id2.
Bei einem Encoder‑Wechsel müss einige andere Werte angepasst werden.

- **Ring‑Encoder an der Ausgangswelle**: typisch ca. **160000 Counts pro 360°**. Sehr feine Positionsauflösung, da direkt an der Rotor‑Achse gemessen wird.
- **Encoder an der Motorachse**: typisch ca. **50000 Counts pro 360°** (am Motor). Mechanisch oft einfacher, aber das Getriebe kann beim Richtungswechsel Spiel haben.

**Auswirkung in der Firmware:** Homing‑Rampen, erwartete Counts pro Umdrehung sowie einige Schutz‑ und Auswertefunktionen verwenden diese feste Counts‑pro‑Umdrehung‑Annahme. Daher ändert sich das Verhalten spürbar, wenn die Encoder‑Variante gewechselt wird.

**Getriebespiel‑Kompensation beim Motor‑Encoder:** Wenn der Encoder am Motor sitzt, wird das Getriebespiel beim Homing durch die Firmware kompensiert. Dazu wird die Referenz immer mit einer definierten Anfahr‑Richtung und einer definierten Überfahr‑/Rückzugslogik angefahren, damit der Nullpunkt trotz Spiel reproduzierbar bleibt.

## 3. RS485‑Befehle – Tabelle {#cmd-table}

Spalte 1 zeigt ein Beispiel vom Master zum Slave. Spalte 2 zeigt die typische Antwort vom Slave. Spalte 3 ist eine Kurzbeschreibung. **Antennenversatz** (`SETANTOFF1..3`/`GET…`) und **Öffnungswinkel** (`SETANGLE1..3`/`GET…`) werden im Controller dauerhaft gespeichert (NVS) – dieselben Befehle stehen im Bridge‑Befehlsfenster zur Verfügung.

| Master → Slave | Slave → Master | Kurzbeschreibung |
| --- | --- | --- |
| `#0:20:TEST:...:CS$` | `#20:0:ACK_TEST:...:<CS>$` oder: `#20:0:NAK_TEST:REASON:CS$` | **[TEST](#cmd-TEST)** Verbindungstest (Ping). |
| `#0:20:STOP:...:CS$` | `#20:0:ACK_STOP:...:<CS>$` oder: `#20:0:NAK_STOP:REASON:CS$` | **[STOP](#cmd-STOP)** Weicher Stop (normales Abbremsen). |
| `#0:20:NSTOP:...:CS$` | `#20:0:ACK_NSTOP:...:<CS>$` oder: `#20:0:NAK_NSTOP:REASON:CS$` | **[NSTOP](#cmd-NSTOP)** Not-Stop über RS485. |
| `#0:20:GETREF:...:CS$` | `#20:0:ACK_GETREF:...:<CS>$` oder: `#20:0:NAK_GETREF:REASON:CS$` | **[GETREF](#cmd-GETREF)** Abfragen, ob Referenz (Homing) vorhanden ist. |
| `#0:20:GETPOSDG:...:CS$` | `#20:0:ACK_GETPOSDG:...:<CS>$` oder: `#20:0:NAK_GETPOSDG:REASON:CS$` | **[GETPOSDG](#cmd-GETPOSDG)** Aktuelle Position in Grad (0,01°). |
| `#0:20:GETIS:...:CS$` | `#20:0:ACK_GETIS:...:<CS>$` oder: `#20:0:NAK_GETIS:REASON:CS$` | **[GETIS](#cmd-GETIS)** Aktueller Strommesswert (mV) nach Offset-Abzug. |
| `#0:20:GETTEMPA:...:CS$` | `#20:0:ACK_GETTEMPA:...:<CS>$` oder: `#20:0:NAK_GETTEMPA:REASON:CS$` | **[GETTEMPA](#cmd-GETTEMPA)** Umgebungstemperatur in °C. |
| `#0:20:GETTEMPM:...:CS$` | `#20:0:ACK_GETTEMPM:...:<CS>$` oder: `#20:0:NAK_GETTEMPM:REASON:CS$` | **[GETTEMPM](#cmd-GETTEMPM)** Motortemperatur in °C. |
| `#0:20:GETANEMO:...:CS$` | `#20:0:ACK_GETANEMO:...:<CS>$` oder: `#20:0:NAK_GETANEMO:REASON:CS$` | **[GETANEMO](#cmd-GETANEMO)** Windgeschwindigkeit vom Windmesser in km/h. |
| `#0:20:GETBEAUFORT:...:CS$` | `#20:0:ACK_GETBEAUFORT:...:<CS>$` oder: `#20:0:NAK_GETBEAUFORT:REASON:CS$` | **[GETBEAUFORT](#cmd-GETBEAUFORT)** Windstärke des RS485-Sensors als Beaufortwert 0 bis 12. |
| `#0:20:GETANEMOOF:...:CS$` | `#20:0:ACK_GETANEMOOF:...:<CS>$` oder: `#20:0:NAK_GETANEMOOF:REASON:CS$` | **[GETANEMOOF](#cmd-GETANEMOOF)** Offset der Windgeschwindigkeit (km/h) lesen. |
| `#0:20:SETANEMOOF:...:CS$` | `#20:0:ACK_SETANEMOOF:...:<CS>$` oder: `#20:0:NAK_SETANEMOOF:REASON:CS$` | **[SETANEMOOF](#cmd-SETANEMOOF)** Offset der Windgeschwindigkeit (km/h) setzen. |
| `#0:20:GETWINDDIR:...:CS$` | `#20:0:ACK_WINDDIR:...:<CS>$` oder: `#20:0:NAK_WINDDIR:REASON:CS$` | **[GETWINDDIR](#cmd-GETWINDDIR)** Windrichtung des RS485-Sensors in Grad lesen. |
| `#0:20:GETWINDDIROF:...:CS$` | `#20:0:ACK_GETWINDDIROF:...:<CS>$` oder: `#20:0:NAK_GETWINDDIROF:REASON:CS$` | **[GETWINDDIROF](#cmd-GETWINDDIROF)** Offset der Windrichtung (Grad) lesen. |
| `#0:20:SETWINDDIROF:...:CS$` | `#20:0:ACK_SETWINDDIROF:...:<CS>$` oder: `#20:0:NAK_SETWINDDIROF:REASON:CS$` | **[SETWINDDIROF](#cmd-SETWINDDIROF)** Offset der Windrichtung (Grad) setzen. |
| `#0:20:GETWINDENABLE:...:CS$` | `#20:0:ACK_GETWINDENABLE:...:<CS>$` oder: `#20:0:NAK_GETWINDENABLE:REASON:CS$` | **[GETWINDENABLE](#cmd-GETWINDENABLE)** Abfragen, ob der RS485-Windsensor aktiv ist. |
| `#0:20:SETWINDENABLE:...:CS$` | `#20:0:ACK_SETWINDENABLE:...:<CS>$` oder: `#20:0:NAK_SETWINDENABLE:REASON:CS$` | **[SETWINDENABLE](#cmd-SETWINDENABLE)** RS485-Windsensor aktivieren oder deaktivieren. |
| `#0:20:GETANGLE1:1:20,01$` | `#20:0:ACK_GETANGLE1:<WERT>:<CS>$` oder: `#20:0:NAK_GETANGLE1:REASON:CS$` | **[GETANGLE1](#cmd-GETANGLE1)** Öffnungswinkel der Antenne 1 lesen. |
| `#0:20:SETANGLE1:120,0:21,20$` | `#20:0:ACK_SETANGLE1:1:<CS>$` oder: `#20:0:NAK_SETANGLE1:REASON:CS$` | **[SETANGLE1](#cmd-SETANGLE1)** Öffnungswinkel der Antenne 1 speichern. |
| `#0:20:GETANGLE2:1:20,01$` | `#20:0:ACK_GETANGLE2:<WERT>:<CS>$` oder: `#20:0:NAK_GETANGLE2:REASON:CS$` | **[GETANGLE2](#cmd-GETANGLE2)** Öffnungswinkel der Antenne 2 lesen. |
| `#0:20:SETANGLE2:120,0:21,20$` | `#20:0:ACK_SETANGLE2:1:<CS>$` oder: `#20:0:NAK_SETANGLE2:REASON:CS$` | **[SETANGLE2](#cmd-SETANGLE2)** Öffnungswinkel der Antenne 2 speichern. |
| `#0:20:GETANGLE3:1:20,01$` | `#20:0:ACK_GETANGLE3:<WERT>:<CS>$` oder: `#20:0:NAK_GETANGLE3:REASON:CS$` | **[GETANGLE3](#cmd-GETANGLE3)** Öffnungswinkel der Antenne 3 lesen. |
| `#0:20:SETANGLE3:120,0:21,20$` | `#20:0:ACK_SETANGLE3:1:<CS>$` oder: `#20:0:NAK_SETANGLE3:REASON:CS$` | **[SETANGLE3](#cmd-SETANGLE3)** Öffnungswinkel der Antenne 3 speichern. |
| `#0:20:GETANTOFF1:1:20,01$` | `#20:0:ACK_GETANTOFF1:<WERT>:<CS>$` oder: `#20:0:NAK_GETANTOFF1:REASON:CS$` | **[GETANTOFF1](#cmd-GETANTOFF1)** Winkelversatz der Antenne 1 lesen. |
| `#0:20:SETANTOFF1:45,0:20,45$` | `#20:0:ACK_SETANTOFF1:1:<CS>$` oder: `#20:0:NAK_SETANTOFF1:REASON:CS$` | **[SETANTOFF1](#cmd-SETANTOFF1)** Winkelversatz der Antenne 1 speichern. |
| `#0:20:GETANTOFF2:1:20,01$` | `#20:0:ACK_GETANTOFF2:<WERT>:<CS>$` oder: `#20:0:NAK_GETANTOFF2:REASON:CS$` | **[GETANTOFF2](#cmd-GETANTOFF2)** Winkelversatz der Antenne 2 lesen. |
| `#0:20:SETANTOFF2:45,0:20,45$` | `#20:0:ACK_SETANTOFF2:1:<CS>$` oder: `#20:0:NAK_SETANTOFF2:REASON:CS$` | **[SETANTOFF2](#cmd-SETANTOFF2)** Winkelversatz der Antenne 2 speichern. |
| `#0:20:GETANTOFF3:1:20,01$` | `#20:0:ACK_GETANTOFF3:<WERT>:<CS>$` oder: `#20:0:NAK_GETANTOFF3:REASON:CS$` | **[GETANTOFF3](#cmd-GETANTOFF3)** Winkelversatz der Antenne 3 lesen. |
| `#0:20:SETANTOFF3:45,0:20,45$` | `#20:0:ACK_SETANTOFF3:1:<CS>$` oder: `#20:0:NAK_SETANTOFF3:REASON:CS$` | **[SETANTOFF3](#cmd-SETANTOFF3)** Winkelversatz der Antenne 3 speichern. |
| `#0:20:GETTEMPAW:...:CS$` | `#20:0:ACK_GETTEMPAW:...:<CS>$` oder: `#20:0:NAK_GETTEMPAW:REASON:CS$` | **[GETTEMPAW](#cmd-GETTEMPAW)** Warnschwelle Umgebungstemperatur lesen. |
| `#0:20:GETTEMPMW:...:CS$` | `#20:0:ACK_GETTEMPMW:...:<CS>$` oder: `#20:0:NAK_GETTEMPMW:REASON:CS$` | **[GETTEMPMW](#cmd-GETTEMPMW)** Warnschwelle Motortemperatur lesen. |
| `#0:20:SETTEMPA:...:CS$` | `#20:0:ACK_SETTEMPA:...:<CS>$` oder: `#20:0:NAK_SETTEMPA:REASON:CS$` | **[SETTEMPA](#cmd-SETTEMPA)** Warnschwelle Umgebungstemperatur setzen. |
| `#0:20:SETTEMPM:...:CS$` | `#20:0:ACK_SETTEMPM:...:<CS>$` oder: `#20:0:NAK_SETTEMPM:REASON:CS$` | **[SETTEMPM](#cmd-SETTEMPM)** Warnschwelle Motortemperatur setzen. |
| `#0:20:GETSWAPTMP:...:CS$` | `#20:0:ACK_GETSWAPTMP:...:<CS>$` oder: `#20:0:NAK_GETSWAPTMP:REASON:CS$` | **[GETSWAPTMP](#cmd-GETSWAPTMP)** Zuordnung Motor-/Umgebungstemperatur lesen (0=normal, 1=vertauscht). |
| `#0:20:SETSWAPTEMP:...:CS$` | `#20:0:ACK_SETSWAPTEMP:...:<CS>$` oder: `#20:0:NAK_SETSWAPTEMP:REASON:CS$` | **[SETSWAPTEMP](#cmd-SETSWAPTEMP)** Zuordnung Motor-/Umgebungstemperatur setzen (0/1, NVS). |
| `#0:20:GETCOLDT:...:CS$` | `#20:0:ACK_GETCOLDT:...:<CS>$` oder: `#20:0:NAK_GETCOLDT:REASON:CS$` | **[GETCOLDT](#cmd-GETCOLDT)** Kälte-Schwelle (°C) lesen. |
| `#0:20:SETCOLDT:...:CS$` | `#20:0:ACK_SETCOLDT:...:<CS>$` oder: `#20:0:NAK_SETCOLDT:REASON:CS$` | **[SETCOLDT](#cmd-SETCOLDT)** Kälte-Schwelle (°C) setzen. |
| `#0:20:GETCOLDP:...:CS$` | `#20:0:ACK_GETCOLDP:...:<CS>$` oder: `#20:0:NAK_GETCOLDP:REASON:CS$` | **[GETCOLDP](#cmd-GETCOLDP)** Extra-Reibung bei Kälte (%) lesen. |
| `#0:20:SETCOLDP:...:CS$` | `#20:0:ACK_SETCOLDP:...:<CS>$` oder: `#20:0:NAK_SETCOLDP:REASON:CS$` | **[SETCOLDP](#cmd-SETCOLDP)** Extra-Reibung bei Kälte (%) setzen. |
| `#0:20:GETCALIGNDG:...:CS$` | `#20:0:ACK_GETCALIGNDG:...:<CS>$` oder: `#20:0:NAK_GETCALIGNDG:REASON:CS$` | **[GETCALIGNDG](#cmd-GETCALIGNDG)** Kalibrierfahrt: Rampenbereich, der ignoriert wird (Grad). |
| `#0:20:SETCALIGNDG:...:CS$` | `#20:0:ACK_SETCALIGNDG:...:<CS>$` oder: `#20:0:NAK_SETCALIGNDG:REASON:CS$` | **[SETCALIGNDG](#cmd-SETCALIGNDG)** Kalibrierfahrt: Rampenbereich (Grad) setzen. |
| `#0:20:GETSTATMINDG:...:CS$` | `#20:0:ACK_GETSTATMINDG:...:<CS>$` oder: `#20:0:NAK_GETSTATMINDG:REASON:CS$` | **[GETSTATMINDG](#cmd-GETSTATMINDG)** Mindestbewegung in Grad für Statistik. |
| `#0:20:SETSTATMINDG:...:CS$` | `#20:0:ACK_SETSTATMINDG:...:<CS>$` oder: `#20:0:NAK_SETSTATMINDG:REASON:CS$` | **[SETSTATMINDG](#cmd-SETSTATMINDG)** Mindestbewegung in Grad setzen. |
| `#0:20:GETDRAG:...:CS$` | `#20:0:ACK_GETDRAG:...:<CS>$` oder: `#20:0:NAK_GETDRAG:REASON:CS$` | **[GETDRAG](#cmd-GETDRAG)** Schwellwert „mehr Reibung“ (%) lesen. |
| `#0:20:SETDRAG:...:CS$` | `#20:0:ACK_SETDRAG:...:<CS>$` oder: `#20:0:NAK_SETDRAG:REASON:CS$` | **[SETDRAG](#cmd-SETDRAG)** Schwellwert „mehr Reibung“ (%) setzen. |
| `#0:20:GETDRAGBINS:...:CS$` | `#20:0:ACK_GETDRAGBINS:...:<CS>$` oder: `#20:0:NAK_GETDRAGBINS:REASON:CS$` | **[GETDRAGBINS](#cmd-GETDRAGBINS)** Wie viele Winkel-Bins müssen drüber sein (%) lesen. |
| `#0:20:SETDRAGBINS:...:CS$` | `#20:0:ACK_SETDRAGBINS:...:<CS>$` oder: `#20:0:NAK_SETDRAGBINS:REASON:CS$` | **[SETDRAGBINS](#cmd-SETDRAGBINS)** Drag-Bins-Prozent setzen. |
| `#0:20:GETDRAGPERSIST:...:CS$` | `#20:0:ACK_GETDRAGPERSIST:...:<CS>$` oder: `#20:0:NAK_GETDRAGPERSIST:REASON:CS$` | **[GETDRAGPERSIST](#cmd-GETDRAGPERSIST)** Wie viele große Fahrten hintereinander benötigt werden. |
| `#0:20:SETDRAGPERSIST:...:CS$` | `#20:0:ACK_SETDRAGPERSIST:...:<CS>$` oder: `#20:0:NAK_SETDRAGPERSIST:REASON:CS$` | **[SETDRAGPERSIST](#cmd-SETDRAGPERSIST)** Persistenz (Anzahl Fahrten) setzen. |
| `#0:20:GETWINDPEAK:...:CS$` | `#20:0:ACK_GETWINDPEAK:...:<CS>$` oder: `#20:0:NAK_GETWINDPEAK:REASON:CS$` | **[GETWINDPEAK](#cmd-GETWINDPEAK)** Wind: Peak-Schwellwert (%) lesen. |
| `#0:20:SETWINDPEAK:...:CS$` | `#20:0:ACK_SETWINDPEAK:...:<CS>$` oder: `#20:0:NAK_SETWINDPEAK:REASON:CS$` | **[SETWINDPEAK](#cmd-SETWINDPEAK)** Wind: Peak-Schwellwert (%) setzen. |
| `#0:20:GETWINDCOH:...:CS$` | `#20:0:ACK_GETWINDCOH:...:<CS>$` oder: `#20:0:NAK_GETWINDCOH:REASON:CS$` | **[GETWINDCOH](#cmd-GETWINDCOH)** Wind: Mindest-Kohärenz (%) lesen. |
| `#0:20:SETWINDCOH:...:CS$` | `#20:0:ACK_SETWINDCOH:...:<CS>$` oder: `#20:0:NAK_SETWINDCOH:REASON:CS$` | **[SETWINDCOH](#cmd-SETWINDCOH)** Wind: Mindest-Kohärenz (%) setzen. |
| `#0:20:GETCALVALID:...:CS$` | `#20:0:ACK_GETCALVALID:...:<CS>$` oder: `#20:0:NAK_GETCALVALID:REASON:CS$` | **[GETCALVALID](#cmd-GETCALVALID)** Abfragen, ob eine gültige Kalibrierung gespeichert ist. |
| `#0:20:SETCAL:...:CS$` | `#20:0:ACK_SETCAL:...:<CS>$` oder: `#20:0:NAK_SETCAL:REASON:CS$` | **[SETCAL](#cmd-SETCAL)** Kalibrierfahrt starten (0→360→0) und Stromprofil lernen. |
| `#0:20:ABORTCAL:...:CS$` | `#20:0:ACK_ABORTCAL:...:<CS>$` oder: `#20:0:NAK_ABORTCAL:REASON:CS$` | **[ABORTCAL](#cmd-ABORTCAL)** Kalibrierfahrt abbrechen. |
| `#0:20:DELCAL:...:CS$` | `#20:0:ACK_DELCAL:...:<CS>$` oder: `#20:0:NAK_DELCAL:REASON:CS$` | **[DELCAL](#cmd-DELCAL)** Gespeicherte Kalibrierung löschen. |
| `#0:20:CLRSTAT:...:CS$` | `#20:0:ACK_CLRSTAT:...:<CS>$` oder: `#20:0:NAK_CLRSTAT:REASON:CS$` | **[CLRSTAT](#cmd-CLRSTAT)** Live-Statistik (Betriebsdaten) löschen. |
| `#0:20:GETCALSTATE:...:CS$` | `#20:0:ACK_GETCALSTATE:...:<CS>$` oder: `#20:0:NAK_GETCALSTATE:REASON:CS$` | **[GETCALSTATE](#cmd-GETCALSTATE)** Status der Kalibrierfahrt abfragen. |
| `#0:20:GETLOADSTAT:...:CS$` | `#20:0:ACK_GETLOADSTAT:...:<CS>$` oder: `#20:0:NAK_GETLOADSTAT:REASON:CS$` | **[GETLOADSTAT](#cmd-GETLOADSTAT)** Zusammenfassung der Last-Statistik. |
| `#0:20:GETWIND:...:CS$` | `#20:0:ACK_GETWIND:...:<CS>$` oder: `#20:0:NAK_GETWIND:REASON:CS$` | **[GETWIND](#cmd-GETWIND)** Wind-Schätzung aus Motorlastprofil. |
| `#0:20:GETCALBINS:...:CS$` | `#20:0:ACK_GETCALBINS:...:<CS>$` oder: `#20:0:NAK_GETCALBINS:REASON:CS$` | **[GETCALBINS](#cmd-GETCALBINS)** Kalibrier-Bins lesen (72 Bins). |
| `#0:20:GETLIVEBINS:...:CS$` | `#20:0:ACK_GETLIVEBINS:...:<CS>$` oder: `#20:0:NAK_GETLIVEBINS:REASON:CS$` | **[GETLIVEBINS](#cmd-GETLIVEBINS)** Live-Bins lesen (72 Bins). |
| `#0:20:GETACCBINS:1;0;12:20,12$` | `#20:0:ACK_GETACCBINS:DIR;START;COUNT;V1;V2;...:<CS>$` oder: `#20:0:NAK_GETACCBINS:REASON:CS$` | **[GETACCBINS](#cmd-GETACCBINS)** Schnelle aktuelle Last-Bins lesen (72 Bins, auch ohne Kalibrierung). |
| `#0:20:SETACCBINSRST:1:20,01$` | `#20:0:ACK_SETACCBINSRST:1:<CS>$` oder: `#20:0:NAK_SETACCBINSRST:REASON:CS$` | **[SETACCBINSRST](#cmd-SETACCBINSRST)** Nur die schnelle ACC-Bin-Statistik löschen. |
| `#0:20:GETDELTABINS:...:CS$` | `#20:0:ACK_GETDELTABINS:...:<CS>$` oder: `#20:0:NAK_GETDELTABINS:REASON:CS$` | **[GETDELTABINS](#cmd-GETDELTABINS)** Delta-Bins (Live minus Cal) in % lesen. |
| `#0:20:GETWARN:...:CS$` | `#20:0:ACK_WARN:0:<CS>$` oder: `#20:0:NAK_GETWARN:REASON:CS$` | **[GETWARN](#cmd-GETWARN)** Warnungen abfragen (können mehrere sein). |
| `#0:20:DELWARN:...:CS$` | `#20:0:ACK_DELWARN:...:<CS>$` oder: `#20:0:NAK_DELWARN:REASON:CS$` | **[DELWARN](#cmd-DELWARN)** Warnungen löschen. |
| `#0:20:GETERR:...:CS$` optional, nicht mehr für zyklisches Polling empfohlen | `#20:0:ACK_ERR:0:<CS>$` oder: `#20:0:NAK_GETERR:REASON:CS$` | **[GETERR](#cmd-GETERR)** Aktuellen Fehlercode manuell abfragen (Fehlerfluss läuft primär über asynchrones `ERR`). |
| `#0:20:SETREF:...:CS$` | `#20:0:ACK_SETREF:...:<CS>$` oder: `#20:0:NAK_SETREF:REASON:CS$` | **[SETREF](#cmd-SETREF)** Homing starten und Fehler quittieren. |
| `#0:20:JOG:...:CS$` | `#20:0:ACK_JOG:...:<CS>$` oder: `#20:0:NAK_JOG:REASON:CS$` | **[JOG](#cmd-JOG)** Manuelles Verfahren (aktuell deaktiviert). |
| `#0:20:SETPOSDG:...:CS$` | `#20:0:ACK_SETPOSDG:...:<CS>$` oder: `#20:0:NAK_SETPOSDG:REASON:CS$` | **[SETPOSDG](#cmd-SETPOSDG)** Positionsfahrt in Grad starten. |
| `#2:1:SETPOSCC:<deg>;<rotor_id>:CS$` | (kein Slave-Pflicht-ACK; optional NAK je nach Gerät) | **[SETPOSCC](#cmd-SETPOSCC)** Encoder-Vorschau-Soll (Display-Controller): Payload ist `<deg>;<rotor_id>`, kein Fahrauftrag. Auf USB mitgespiegelt — PC kann den Sollzeiger des richtigen Rotors vor SETPOSDG nachführen. |
| `#0:20:GETID:...:CS$` | `#20:0:ACK_GETID:...:<CS>$` oder: `#20:0:NAK_GETID:REASON:CS$` | **[GETID](#cmd-GETID)** Slave-ID lesen. |
| `#0:20:SETID:...:CS$` | `#20:0:ACK_SETID:...:<CS>$` oder: `#20:0:NAK_SETID:REASON:CS$` | **[SETID](#cmd-SETID)** Slave-ID setzen und speichern. |
| `#0:20:GETBEGINDG:...:CS$` | `#20:0:ACK_GETBEGINDG:...:<CS>$` oder: `#20:0:NAK_GETBEGINDG:REASON:CS$` | **[GETBEGINDG](#cmd-GETBEGINDG)** Min-Winkel (Achsenanfang) lesen. |
| `#0:20:SETBEGINDG:...:CS$` | `#20:0:ACK_SETBEGINDG:...:<CS>$` oder: `#20:0:NAK_SETBEGINDG:REASON:CS$` | **[SETBEGINDG](#cmd-SETBEGINDG)** Min-Winkel setzen. |
| `#0:20:GETMAXDG:...:CS$` | `#20:0:ACK_GETMAXDG:...:<CS>$` oder: `#20:0:NAK_GETMAXDG:REASON:CS$` | **[GETMAXDG](#cmd-GETMAXDG)** Max-Winkel lesen. |
| `#0:20:SETMAXDG:...:CS$` | `#20:0:ACK_SETMAXDG:...:<CS>$` oder: `#20:0:NAK_SETMAXDG:REASON:CS$` | **[SETMAXDG](#cmd-SETMAXDG)** Max-Winkel setzen. |
| `#0:20:GETDGOFFSET:...:CS$` | `#20:0:ACK_GETDGOFFSET:...:<CS>$` oder: `#20:0:NAK_GETDGOFFSET:REASON:CS$` | **[GETDGOFFSET](#cmd-GETDGOFFSET)** Offset (Grad) lesen. |
| `#0:20:SETDGOFFSET:...:CS$` | `#20:0:ACK_SETDGOFFSET:...:<CS>$` oder: `#20:0:NAK_SETDGOFFSET:REASON:CS$` | **[SETDGOFFSET](#cmd-SETDGOFFSET)** Offset setzen (Grad). |
| `#0:20:GETHOMEPWM:...:CS$` | `#20:0:ACK_GETHOMEPWM:...:<CS>$` oder: `#20:0:NAK_GETHOMEPWM:REASON:CS$` | **[GETHOMEPWM](#cmd-GETHOMEPWM)** Homing-Max-PWM (%) lesen. |
| `#0:20:SETHOMEPWM:...:CS$` | `#20:0:ACK_SETHOMEPWM:...:<CS>$` oder: `#20:0:NAK_SETHOMEPWM:REASON:CS$` | **[SETHOMEPWM](#cmd-SETHOMEPWM)** Homing-Max-PWM (%) setzen. |
| `#0:20:GETHOMEBACKOFF:...:CS$` | `#20:0:ACK_GETHOMEBACKOFF:...:<CS>$` oder: `#20:0:NAK_GETHOMEBACKOFF:REASON:CS$` | **[GETHOMEBACKOFF](#cmd-GETHOMEBACKOFF)** Homing Rückzug (Grad) lesen. |
| `#0:20:SETHOMEBACKOFF:...:CS$` | `#20:0:ACK_SETHOMEBACKOFF:...:<CS>$` oder: `#20:0:NAK_SETHOMEBACKOFF:REASON:CS$` | **[SETHOMEBACKOFF](#cmd-SETHOMEBACKOFF)** Homing Rückzug (Grad) setzen. |
| `#0:20:GETHOMEBLSCALE:...:CS$` | `#20:0:ACK_GETHOMEBLSCALE:...:<CS>$` oder: `#20:0:NAK_GETHOMEBLSCALE:REASON:CS$` | **[GETHOMEBLSCALE](#cmd-GETHOMEBLSCALE)** Homing: Backlash-Skalierung (0–1) lesen. |
| `#0:20:SETHOMEBLSCALE:...:CS$` | `#20:0:ACK_SETHOMEBLSCALE:...:<CS>$` oder: `#20:0:NAK_SETHOMEBLSCALE:REASON:CS$` | **[SETHOMEBLSCALE](#cmd-SETHOMEBLSCALE)** Homing: Faktor für die Backlash-Berechnung (0–1) setzen. |
| `#0:20:GETHOMRETURN:...:CS$` | `#20:0:ACK_GETHOMRETURN:...:<CS>$` oder: `#20:0:NAK_GETHOMRETURN:REASON:CS$` | **[GETHOMRETURN](#cmd-GETHOMRETURN)** Rückfahrt auf 0 nach Homing an/aus lesen. |
| `#0:20:SETHOMERETURN:...:CS$` | `#20:0:ACK_SETHOMERETURN:...:<CS>$` oder: `#20:0:NAK_SETHOMERETURN:REASON:CS$` | **[SETHOMERETURN](#cmd-SETHOMERETURN)** Rückfahrt auf 0 nach Homing setzen. |
| `#0:20:GETHOMETIMEOUT:...:CS$` | `#20:0:ACK_GETHOMETIMEOUT:...:<CS>$` oder: `#20:0:NAK_GETHOMETIMEOUT:REASON:CS$` | **[GETHOMETIMEOUT](#cmd-GETHOMETIMEOUT)** Homing Timeout (ms) lesen. |
| `#0:20:SETHOMETIMEOUT:...:CS$` | `#20:0:ACK_SETHOMETIMEOUT:...:<CS>$` oder: `#20:0:NAK_SETHOMETIMEOUT:REASON:CS$` | **[SETHOMETIMEOUT](#cmd-SETHOMETIMEOUT)** Homing Timeout (ms) setzen. |
| `#0:20:GETPOSTIMEOUT:...:CS$` | `#20:0:ACK_GETPOSTIMEOUT:...:<CS>$` oder: `#20:0:NAK_GETPOSTIMEOUT:REASON:CS$` | **[GETPOSTIMEOUT](#cmd-GETPOSTIMEOUT)** Positions-Timeout (ms) lesen. |
| `#0:20:SETPOSTIMEOUT:...:CS$` | `#20:0:ACK_SETPOSTIMEOUT:...:<CS>$` oder: `#20:0:NAK_SETPOSTIMEOUT:REASON:CS$` | **[SETPOSTIMEOUT](#cmd-SETPOSTIMEOUT)** Positions-Timeout (ms) setzen. |
| `#0:20:GETHANDSPEED:...:CS$` | `#20:0:ACK_GETHANDSPEED:...:<CS>$` oder: `#20:0:NAK_GETHANDSPEED:REASON:CS$` | **[GETHANDSPEED](#cmd-GETHANDSPEED)** Handspeed (%) lesen. |
| `#0:20:SETHANDSPEED:...:CS$` | `#20:0:ACK_SETHANDSPEED:...:<CS>$` oder: `#20:0:NAK_SETHANDSPEED:REASON:CS$` | **[SETHANDSPEED](#cmd-SETHANDSPEED)** Handspeed (%) setzen. |
| `#0:20:GETDEADMAN:...:CS$` | `#20:0:ACK_GETDEADMAN:...:<CS>$` oder: `#20:0:NAK_GETDEADMAN:REASON:CS$` | **[GETDEADMAN](#cmd-GETDEADMAN)** Deadman/Keepalive Timeout (ms) lesen. |
| `#0:20:SETDEADMAN:...:CS$` | `#20:0:ACK_SETDEADMAN:...:<CS>$` oder: `#20:0:NAK_SETDEADMAN:REASON:CS$` | **[SETDEADMAN](#cmd-SETDEADMAN)** Deadman Timeout (ms) setzen. |
| `#0:20:GETIWARN:...:CS$` | `#20:0:ACK_GETIWARN:...:<CS>$` oder: `#20:0:NAK_GETIWARN:REASON:CS$` | **[GETIWARN](#cmd-GETIWARN)** Strom-Warnschwelle (mV) lesen. |
| `#0:20:SETIWARN:...:CS$` | `#20:0:ACK_SETIWARN:...:<CS>$` oder: `#20:0:NAK_SETIWARN:REASON:CS$` | **[SETIWARN](#cmd-SETIWARN)** Strom-Warnschwelle (mV) setzen. |
| `#0:20:GETIMAX:...:CS$` | `#20:0:ACK_GETIMAX:...:<CS>$` oder: `#20:0:NAK_GETIMAX:REASON:CS$` | **[GETIMAX](#cmd-GETIMAX)** Strom-Abschaltschwelle (mV) lesen. |
| `#0:20:SETIMAX:...:CS$` | `#20:0:ACK_SETIMAX:...:<CS>$` oder: `#20:0:NAK_SETIMAX:REASON:CS$` | **[SETIMAX](#cmd-SETIMAX)** Strom-Abschaltschwelle (mV) setzen. |
| `#0:20:GETARRTOL:...:CS$` | `#20:0:ACK_GETARRTOL:...:<CS>$` oder: `#20:0:NAK_GETARRTOL:REASON:CS$` | **[GETARRTOL](#cmd-GETARRTOL)** Ankunftstoleranz (Grad) lesen. |
| `#0:20:SETARRTOL:...:CS$` | `#20:0:ACK_SETARRTOL:...:<CS>$` oder: `#20:0:NAK_SETARRTOL:REASON:CS$` | **[SETARRTOL](#cmd-SETARRTOL)** Ankunftstoleranz (Grad) setzen. |
| `#0:20:GETRAMP:...:CS$` | `#20:0:ACK_GETRAMP:...:<CS>$` oder: `#20:0:NAK_GETRAMP:REASON:CS$` | **[GETRAMP](#cmd-GETRAMP)** Rampenlänge (Grad) lesen. |
| `#0:20:SETRAMP:...:CS$` | `#20:0:ACK_SETRAMP:...:<CS>$` oder: `#20:0:NAK_SETRAMP:REASON:CS$` | **[SETRAMP](#cmd-SETRAMP)** Rampenlänge (Grad) setzen. |
| `#0:20:GETMINPWM:...:CS$` | `#20:0:ACK_GETMINPWM:...:<CS>$` oder: `#20:0:NAK_GETMINPWM:REASON:CS$` | **[GETMINPWM](#cmd-GETMINPWM)** Min-PWM (%) lesen. |
| `#0:20:SETMINPWM:...:CS$` | `#20:0:ACK_SETMINPWM:...:<CS>$` oder: `#20:0:NAK_SETMINPWM:REASON:CS$` | **[SETMINPWM](#cmd-SETMINPWM)** Min-PWM (%) setzen. |
| `#0:20:GETSTALLTIMEOUT:...:CS$` | `#20:0:ACK_GETSTALLTIMEOUT:...:<CS>$` oder: `#20:0:NAK_GETSTALLTIMEOUT:REASON:CS$` | **[GETSTALLTIMEOUT](#cmd-GETSTALLTIMEOUT)** Stall-Timeout (ms) lesen. |
| `#0:20:SETSTALLTIMEOUT:...:CS$` | `#20:0:ACK_SETSTALLTIMEOUT:...:<CS>$` oder: `#20:0:NAK_SETSTALLTIMEOUT:REASON:CS$` | **[SETSTALLTIMEOUT](#cmd-SETSTALLTIMEOUT)** Stall-Timeout (ms) setzen. |
| `#0:20:GETMAXPWM:...:CS$` | `#20:0:ACK_GETMAXPWM:...:<CS>$` oder: `#20:0:NAK_GETMAXPWM:REASON:CS$` | **[GETMAXPWM](#cmd-GETMAXPWM)** Max-PWM (%) lesen. |
| `#0:20:SETMAXPWM:...:CS$` | `#20:0:ACK_SETMAXPWM:...:<CS>$` oder: `#20:0:NAK_SETMAXPWM:REASON:CS$` | **[SETMAXPWM](#cmd-SETMAXPWM)** Max-PWM (%) setzen. |
| `#0:20:GETPWM:...:CS$` | `#20:0:ACK_GETPWM:...:<CS>$` oder: `#20:0:NAK_GETPWM:REASON:CS$` | **[GETPWM](#cmd-GETPWM)** Aktuell freigegebenes PWM-Limit (%) lesen. |
| `#0:20:SETPWM:...:CS$` | `#20:0:ACK_SETPWM:...:<CS>$` oder: `#20:0:NAK_SETPWM:REASON:CS$` | **[SETPWM](#cmd-SETPWM)** PWM-Limit (%) zur Laufzeit setzen (ohne Speichern). |
| `#0:20:RESET:1:21$` | `#20:0:ACK_RESET:1:<CS>$` oder: `#20:0:NAK_RESET:REASON:CS$` | **[RESET](#cmd-RESET)** Factory-Reset (Auslieferungszustand) und Neustart – **alle** Einstellungen verworfen. |
| `#0:20:GETSTALLEN:...:CS$` | `#20:0:ACK_GETSTALLEN:...:<CS>$` oder: `#20:0:NAK_GETSTALLEN:REASON:CS$` | **[GETSTALLEN](#cmd-GETSTALLEN)** Stall-Erkennung ein/aus lesen (0/1). |
| `#0:20:SETSTALLEN:...:CS$` | `#20:0:ACK_SETSTALLEN:...:<CS>$` oder: `#20:0:NAK_SETSTALLEN:REASON:CS$` | **[SETSTALLEN](#cmd-SETSTALLEN)** Stall-Erkennung aktivieren/deaktivieren. |
| `#0:20:GETMINSTALLPWM:...:CS$` | `#20:0:ACK_GETMINSTALLPWM:...:<CS>$` oder: `#20:0:NAK_GETMINSTALLPWM:REASON:CS$` | **[GETMINSTALLPWM](#cmd-GETMINSTALLPWM)** Mindest-PWM für Stall-Erkennung lesen (%). |
| `#0:20:SETMINSTALLPWM:...:CS$` | `#20:0:ACK_SETMINSTALLPWM:...:<CS>$` oder: `#20:0:NAK_SETMINSTALLPWM:REASON:CS$` | **[SETMINSTALLPWM](#cmd-SETMINSTALLPWM)** Mindest-PWM für Stall-Erkennung setzen. |
| `#0:20:GETSTALLMINCOUNTS:...:CS$` | `#20:0:ACK_GETSTALLMINCOUNTS:...:<CS>$` oder: `#20:0:NAK_GETSTALLMINCOUNTS:REASON:CS$` | **[GETSTALLMINCOUNTS](#cmd-GETSTALLMINCOUNTS)** Mindest-Encoder-Counts im Stall-Zeitfenster lesen. |
| `#0:20:SETSTALLMINCOUNTS:...:CS$` | `#20:0:ACK_SETSTALLMINCOUNTS:...:<CS>$` oder: `#20:0:NAK_SETSTALLMINCOUNTS:REASON:CS$` | **[SETSTALLMINCOUNTS](#cmd-SETSTALLMINCOUNTS)** Mindest-Counts im Stall-Fenster setzen. |
| `#0:20:GETFILTERLEN:...:CS$` | `#20:0:ACK_GETFILTERLEN:...:<CS>$` oder: `#20:0:NAK_GETFILTERLEN:REASON:CS$` | **[GETFILTERLEN](#cmd-GETFILTERLEN)** Länge des Strom-Mittelwertfilters lesen (gehört zu `SETISFILTERLEN`). |
| `#0:20:SETISFILTERLEN:...:CS$` | `#20:0:ACK_SETISFILTERLEN:...:<CS>$` oder: `#20:0:NAK_SETISFILTERLEN:REASON:CS$` | **[SETISFILTERLEN](#cmd-SETISFILTERLEN)** Strom-Mittelwertfilter-Länge setzen (1…32). |
| `#0:20:GETISHOLDMS:...:CS$` | `#20:0:ACK_GETISHOLDMS:...:<CS>$` oder: `#20:0:NAK_GETISHOLDMS:REASON:CS$` | **[GETISHOLDMS](#cmd-GETISHOLDMS)** Hold-Zeit Strom-Hardlimit (ms) lesen. |
| `#0:20:SETISHOLDMS:...:CS$` | `#20:0:ACK_SETISHOLDMS:...:<CS>$` oder: `#20:0:NAK_SETISHOLDMS:REASON:CS$` | **[SETISHOLDMS](#cmd-SETISHOLDMS)** Hold-Zeit für Strom-Hardlimit setzen (ms). |
| `#0:20:GETGRACEMS:...:CS$` | `#20:0:ACK_GETGRACEMS:...:<CS>$` oder: `#20:0:NAK_GETGRACEMS:REASON:CS$` | **[GETGRACEMS](#cmd-GETGRACEMS)** Grace-Zeit Stromschutz (ms) lesen. |
| `#0:20:SETISGRACEMS:...:CS$` | `#20:0:ACK_SETISGRACEMS:...:<CS>$` oder: `#20:0:NAK_SETISGRACEMS:REASON:CS$` | **[SETISGRACEMS](#cmd-SETISGRACEMS)** Grace-Zeit nach Start/Umkehr setzen (ms). |
| `#0:20:GETHOMESEEKPPWM:...:CS$` | `#20:0:ACK_GETHOMESEEKPPWM:...:<CS>$` oder: `#20:0:NAK_GETHOMESEEKPPWM:REASON:CS$` | **[GETHOMESEEKPPWM](#cmd-GETHOMESEEKPPWM)** Zusätzliche Such-PWM in der Homing-Suchphase lesen (%). |
| `#0:20:SETHOMESEEKPPWM:...:CS$` | `#20:0:ACK_SETHOMESEEKPPWM:...:<CS>$` oder: `#20:0:NAK_SETHOMESEEKPPWM:REASON:CS$` | **[SETHOMESEEKPPWM](#cmd-SETHOMESEEKPPWM)** Zusätzliche Such-PWM für Homing-Suchphase setzen. |
| `#0:20:GETENCCRI:...:CS$` | `#20:0:ACK_GETENCCRI:...:<CS>$` oder: `#20:0:NAK_GETENCCRI:REASON:CS$` | **[GETENCCRI](#cmd-GETENCCRI)** Counts pro 360° **Ring**-Encoder lesen. |
| `#0:20:SETENCCRI:...:CS$` | `#20:0:ACK_SETENCCRI:...:<CS>$` oder: `#20:0:NAK_SETENCCRI:REASON:CS$` | **[SETENCCRI](#cmd-SETENCCRI)** Counts pro 360° Ring-Encoder setzen (typ. ~160000). |
| `#0:20:GETENCCAX:...:CS$` | `#20:0:ACK_GETENCCAX:...:<CS>$` oder: `#20:0:NAK_GETENCCAX:REASON:CS$` | **[GETENCCAX](#cmd-GETENCCAX)** Counts pro 360° **Motor-/Achsen**-Encoder lesen. |
| `#0:20:SETENCCAX:...:CS$` | `#20:0:ACK_SETENCCAX:...:<CS>$` oder: `#20:0:NAK_SETENCCAX:REASON:CS$` | **[SETENCCAX](#cmd-SETENCCAX)** Counts pro 360° Motor-/Achsen-Encoder setzen (typ. ~28600…50000). |
| `#0:20:GETENCTYPE:...:CS$` | `#20:0:ACK_GETENCTYPE:...:<CS>$` oder: `#20:0:NAK_GETENCTYPE:REASON:CS$` | **[GETENCTYPE](#cmd-GETENCTYPE)** Encoder-Variante lesen: 1 = Achse/Motor, 2 = Ring. |
| `#0:20:SETENCTYPE:...:CS$` | `#20:0:ACK_SETENCTYPE:...:<CS>$` oder: `#20:0:NAK_SETENCTYPE:REASON:CS$` | **[SETENCTYPE](#cmd-SETENCTYPE)** Encoder-Variante setzen (1 oder 2). Nach Wechsel Homing/Counts prüfen. |

[↑ Inhaltsverzeichnis](#toc)

## Hardwarecontroller Configuration {#hw-controller-config}

**Falsche Adresse = Antwort vom Rotor, nicht vom Controller:** Wenn Sie `DST = rotor_id` verwenden (z. B. `20`), geht das Telegramm an den **Antriebs‑Slave**. Der Rotor kennt `GETCONTID`, `GETCONANTNAME*` usw. nicht und antwortet mit `#<RotorID>:<SRC>:NAK_…:NOTIMPL:…$` — das ist **kein** Checksummenfehler auf dem Controller. Richtig ist `DST = master_id` aus der `config.json` des Display‑Controllers (typ. `2`), **nicht** die Rotor‑Slave‑ID.

Diese Befehle richten sich **nicht** an den Rotor‑Slave (Antrieb), sondern an den **Display‑/Hardware‑Controller** (ESP32 mit UI). Zieladresse `DST` ist die im Gerät konfigurierte **Master‑ID** des Controllers (entspricht `master_id` in `config.json` auf der FFat‑Partition). `SRC` ist die ID des sendenden Clients (z. B. PC‑Software).

**Beispiel** (PC `SRC = 1`, Controller `master_id = 2`, GET‑Parameter `0`): Anfrage `#1:2:GETCONTID:0:3$` — Checksumme `3 = 1 + 2 + 0`. Antwort vom Controller (nicht vom Rotor): `#2:1:ACK_GETCONTID:<Wert>:…$`. Falsch wäre `#1:20:GETCONTID:0:21$` — das trifft den Rotor mit ID 20.

Die gleichen Telegramme können über **USB‑Seriell** und über **RS485** eingehen; Antworten (`ACK_*`/`NAK_*`) gehen über `hw_send` ebenfalls auf den Bus **und** zum USB‑Monitor (Spiegelung). Checksumme `CS` wie gewohnt: `SRC + DST + letzter Zahlenwert in PARAMS` (siehe Abschnitt 1).

Schreibbefehle speichern in `config.json` (Slow/Fast‑PWM, IDs, Antennen‑Labels, Touch‑Pieps, Anemometer, Encoder‑Schritt, Antennenwechsel‑Verhalten `concha` u. a.); die UI wird danach aktualisiert (u. a. Labels, RS485‑IDs, Slow/Fast‑`SETPWM` zum Rotor).

| Befehl | Antwort |
| --- | --- |
| `GETCONRID` / `GETTCONRID` (Alias) | `ACK_GETCONRID` (Parameter = aktuelle `rotor_id`) |
| `SETCONRID` | `ACK_SETCONRID` / `NAK_SETCONRID` (Rotor‑Slave‑ID 1…254) |
| `GETCONTID` | `ACK_GETCONTID` (Parameter = `master_id` des Controllers) |
| `SETCONTID` | `ACK_SETCONTID` / `NAK_SETCONTID` (1…254) — Ziel `DST = master_id` (unicast). |
| `SETCONIDF` oder `SETCONTID` mit `DST = 255` (Broadcast) | `ACK_SETCONIDF` bzw. `ACK_SETCONTID` / `NAK_SETCONTID` — setzt die **neue** Controller‑`master_id` in `config.json`, wenn die bisherige ID unbekannt ist. Checksumme: `CS = SRC + 255 + <neue ID>` (z. B. `#1:255:SETCONIDF:5:261$` mit `1+255+5=261`). |
| `GETCONANTNAME1` … `GETCONANTNAME3` | `ACK_GETCONANTNAME1` … / `NAK_GETCONANTNAME*` — Antworttext enthält den Namen, für eine stabile CS wird `;0` angehängt (letzter Zahlenwert 0). |
| `SETCONANTNAME1` … `SETCONANTNAME3` | `ACK_SETCONANTNAME1` … / `NAK_SETCONANTNAME*` — ein `:` im Namen ist unzulässig (`NAK` mit Code 1). |
| `GETCONSPWM` / `SETCONSPWM` | `ACK_GETCONSPWM` / `ACK_SETCONSPWM` (bzw. `NAK_SETCONSPWM`) — Slow‑PWM in % (0…100), entspricht `slow_pwm` in der JSON. |
| `GETCONFPWM` / `SETCONFPWM` | `ACK_GETCONFPWM` / `ACK_SETCONFPWM` (bzw. `NAK_SETCONFPWM`) — Fast‑PWM in % (0…100), entspricht `fast_pwm`. |
| `GETCONFRQ` / `SETCONFRQ` | `ACK_GETCONFRQ` / `ACK_SETCONFRQ` — Touch‑Pieps‑Frequenz in Hz (200…4000), in `config.json` als `confrq`. |
| `GETLSL` / `SETLSL` | `ACK_GETLSL` / `ACK_SETLSL` — Touch‑Pieps‑Lautstärke 0…50 (wie `Signals::tone`), in `config.json` als `lsl`. |
| `GETCONANO` / `SETCONANO` | `ACK_GETCONANO` / `ACK_SETCONANO` (bzw. `NAK_SETCONANO`) — Anemometer/Wetter‑Tab: `1` = Wind, Außentemperatur und Windrichtung im Wetter‑Tab; `0` = Wetter‑Tab aus, `GETTEMPA` für Außentemp (Rotor\_Info) bleibt. JSON `anemometer`. |
| `GETCONDELTA` / `SETCONDELTA` | `ACK_GETCONDELTA` / `ACK_SETCONDELTA` (bzw. `NAK_SETCONDELTA`) — Encoder‑Schritt pro Raste: `1` oder `10` Zehntelgrad (0,1° bzw. 1° pro Klick). JSON `encoder_delta`. |
| `GETCONCHA` / `SETCONCHA` | `ACK_GETCONCHA` / `ACK_SETCONCHA` (bzw. `NAK_SETCONCHA`) — Verhalten beim Antennenwechsel: `1` = Anzeige‑Soll (`taget`) beibehalten, `SETPOSDG` mit neuer Antennen‑Geometrie; `0` = `taget` auf die aktuelle Ist‑Anzeige (Kompass) setzen, kein zusätzliches SETPOS. JSON `concha`. |

**Pflege (Firmware):** Neue Konfig‑Befehle für den Display‑Controller bitte in `src/rotor_rs485.cpp` (`handle_local_config_command`), in `include/pwm_config.h` / `src/pwm_config.cpp` / `data/config.json` und **in dieser Tabelle** parallel ergänzen.

**NAK‑Codes (typisch):** `1` = ungültiger Wertebereich oder verbotenes Zeichen im Namen; `2` = Checksumme/Format passt nicht.

**GET‑Anfragen** verwenden wie üblich z. B. `PARAMS = 0` (letzter Zahlenwert für die CS‑Bildung).

[↑ Inhaltsverzeichnis](#toc)

## 4. RS485‑Befehle – Erklärung & Empfehlungen {#cmd-details}

Hier ist jeder Befehl in einem eigenen Absatz beschrieben: Was er macht, wie man ihn benutzt, und welche Werte in der Praxis oft gut funktionieren.

### Grundbefehle

#### `TEST` {#cmd-TEST}

**Was es macht:** Verbindungstest (Ping).

**Details:** Keine Parameter. Antwort ist ein kurzes OK.

---

#### `GETID` {#cmd-GETID}

**Was es macht:** Slave-ID lesen.

**Details:** Rückgabe 0..255.

---

#### `SETID` {#cmd-SETID}

**Was es macht:** Slave-ID setzen und speichern.

**Details:** Achtung: danach neue Adresse verwenden.

---

#### `GETREF` {#cmd-GETREF}

**Was es macht:** Abfragen, ob Referenz (Homing) vorhanden ist.

**Details:** 0 = nicht referenziert, 1 = referenziert.

---

#### `SETREF` {#cmd-SETREF}

**Was es macht:** Homing starten und Fehler quittieren.

**Details:** SETREF:0 = nur Fehler quittieren. SETREF:1 = Homing starten (Standard).

**Hinweis:** Nach einem Fehler (ERR) muss `SETREF` gesendet werden, damit der Fehler quittiert wird. Warnungen werden nicht dadurch gelöscht (nur `DELWARN`).

---

#### `GETPOSDG` {#cmd-GETPOSDG}

**Was es macht:** Aktuelle Position in Grad (0,01°).

**Details:** Rückgabe ist immer die Offset-korrigierte Position.

---

#### `SETPOSDG` {#cmd-SETPOSDG}

**Was es macht:** Positionsfahrt in Grad starten.

**Details:** Parameter: Zielwinkel (z.B. 160,00).

**Gute Werte:** Zielwinkel in Grad, z.B. `160,00`. Bei sehr kleinen Schritten (0,01°) kann es sinnvoll sein, die Ankunftstoleranz (`ARRTOL`) etwas grösser zu wählen, damit die Regelung nicht „zittert“.

---

#### `SETPOSCC` {#cmd-SETPOSCC}

**Was es macht:** Nur Anzeige-/Vorschau-Soll melden (Encoder am Display-Controller) — **keine** Positionsfahrt am Rotor.

**Details:** Payload ist jetzt zweiteilig: `<deg>;<rotor_id>` (z. B. `151,30;20`). Wird bei jedem Encoder-Schritt gesendet, sobald `taget` auf dem Display neu steht; das eigentliche `SETPOSDG` folgt erst nach Encoder-Ruhepause. Gleichzeitig auf RS485 und USB (Monitor) ausgegeben, damit PC-Software den Sollzeiger früh in die richtige Richtung drehen kann.

**Neue Paketbeispiele:** `#2:1:SETPOSCC:151,30;20:23$`, `#2:1:SETPOSCC:87,00;21:24$`.

**PC-Software Verarbeitung (empfohlen):** Bei `SETPOSCC` den Parameterstring an `;` splitten. Teil 1 = Vorschauwinkel in Grad (Komma->Punkt, dann Float), Teil 2 = Rotor-ID (int). UI-Update nur auf den Rotor-Kontext anwenden, dessen ID mit Teil 2 übereinstimmt. Frames ohne Rotor-ID (Altformat) optional als Fallback auf den aktuell selektierten Rotor mappen oder ignorieren.

**Checksumme:** Unverändert nach Protokollregel (SRC + DST + letzte Zahl im Payload). Da die letzte Zahl nun die Rotor-ID ist, basiert die CS bei `SETPOSCC` auf dieser ID.

**Hinweis:** Der Rotor-Slave muss den Befehl nicht verstehen; unkritisch, wenn er ignoriert wird.

---

#### `STOP` {#cmd-STOP}

**Was es macht:** Weicher Stop (normales Abbremsen).

**Details:** Beendet eine Positionsfahrt. Fehler werden NICHT gelöscht.

---

#### `NSTOP` {#cmd-NSTOP}

**Was es macht:** Not-Stop über RS485.

**Details:** Setzt sofort einen Fehler SE\_NSTOP\_CMD und stoppt.

---

#### `GETERR` {#cmd-GETERR}

**Was es macht:** Aktuellen Fehlercode abfragen.

**Details:** Antwort `ACK_ERR` mit einem Code oder 0.

**Aktueller Betrieb:** In der aktuellen Controller-Firmware wird `GETERR` nicht mehr zyklisch gepollt. Fehler kommen asynchron als `ERR` (typisch `#<rotor_id>:255:ERR:<code>:<cs>$`) und werden direkt übernommen. `GETERR` ist damit primär für manuelle Diagnose/Service gedacht.

---

#### `GETWARN` {#cmd-GETWARN}

**Was es macht:** Warnungen abfragen (können mehrere sein).

**Details:** Antwort ist ACK\_WARN mit Liste: "0" oder "id;id;...".

---

#### `DELWARN` {#cmd-DELWARN}

**Was es macht:** Warnungen löschen.

**Details:** Antwort ACK\_DELWARN:1

---

### Strom / Temperatur / Windmesser

#### `GETIS` {#cmd-GETIS}

**Was es macht:** Aktueller Strommesswert (mV) nach Offset-Abzug.

**Details:** Wird automatisch aus IS1/IS2 passend zur Bewegungsrichtung gewählt.

---

#### `GETTEMPA` {#cmd-GETTEMPA}

**Was es macht:** Umgebungstemperatur in °C.

**Details:** DS18B20. Rückgabe mit 2 Nachkommastellen.

---

#### `GETTEMPM` {#cmd-GETTEMPM}

**Was es macht:** Motortemperatur in °C.

**Details:** Wenn Motor-Sensor deaktiviert ist: immer 0.

---

#### `GETTEMPAW` {#cmd-GETTEMPAW}

**Was es macht:** Warnschwelle Umgebungstemperatur lesen.

**Details:** <=0 deaktiviert die Warnung.

---

#### `GETTEMPMW` {#cmd-GETTEMPMW}

**Was es macht:** Warnschwelle Motortemperatur lesen.

**Details:** <=0 deaktiviert die Warnung.

---

#### `SETTEMPA` {#cmd-SETTEMPA}

**Was es macht:** Warnschwelle Umgebungstemperatur setzen.

**Details:** Wert in °C (Float).

---

#### `SETTEMPM` {#cmd-SETTEMPM}

**Was es macht:** Warnschwelle Motortemperatur setzen.

**Details:** Wert in °C (Float).

---

#### `GETSWAPTMP` {#cmd-GETSWAPTMP}

**Was es macht:** Liest, ob die beiden Temperaturfühler (Motor vs. Umgebung) in der Firmware als vertauscht behandelt werden.

**Telegramm:** `#0:20:GETSWAPTMP:0:20,01$` (Parameter wie bei anderen GET-Befehlen mit Literal `0`)

**Antwort:** `#20:0:ACK_GETSWAPTMP:<0|1>:<CS>$`

**Details:** `0` = normale Zuordnung, `1` = vertauscht. Abhängig von der Hardware-Erkennung können die gemessenen Kanäle getauscht werden müssen; siehe `SETSWAPTEMP`.

---

#### `SETSWAPTEMP` {#cmd-SETSWAPTEMP}

**Was es macht:** Legt fest, ob Motor- und Umgebungstemperatur in der Anzeige/Logik vertauscht werden – wird im NVS gespeichert.

**Telegramm:** `#0:20:SETSWAPTEMP:0:20,20$` bzw. `#0:20:SETSWAPTEMP:1:20,21$`

**Antwort:** `#20:0:ACK_SETSWAPTEMP:1:<CS>$`

**Details:** Nur `0` oder `1` zulässig. Nach Neustart bleibt die Einstellung erhalten.

**Praxis:** Wenn `GETTEMPA` und `GETTEMPM` „falsch herum“ wirken, zuerst prüfen und ggf. auf `1` setzen.

---

#### `GETANEMO` {#cmd-GETANEMO}

**Was es macht:** Liest die Windgeschwindigkeit des RS485-Wind-/Richtungssensors in km/h.

**Details:** Die Firmware pollt den Sensor zyklisch über **RS485/Modbus** und liefert den zwischengespeicherten Wert aus dem HalBoard-Cache. Rückgabe mit 1 Nachkommastelle im Kommaformat. Wenn der Windsensor per `SETWINDENABLE` deaktiviert ist, liefert `GETANEMO` immer `0,0`. Wenn gerade kein gültiger Sensorwert vorliegt, kommt ein `NAK_GETANEMO:NOWIND`.

**Praxis:** Das ist der echte Messwert des RS485-Sensors. Ein km/h-Offset existiert zwar noch, ist bei einem sauber kalibrierten digitalen Sensor aber normalerweise nicht nötig.

---

#### `GETBEAUFORT` {#cmd-GETBEAUFORT}

**Was es macht:** Liest die aktuelle Windstärke des RS485-Wind-/Richtungssensors als ganzzahligen Beaufortwert.

**Telegramm:** `#0:20:GETBEAUFORT:1:20,01$`

**Antwort:** `#20:0:ACK_GETBEAUFORT:<BFT>:<CS>$`

**Details:** Rückgabe als ganzzahliger Wert von `0` bis `12`. Der Wert wird wie `GETANEMO` und `GETWINDDIR` zyklisch vom RS485-Sensor eingelesen und im HalBoard zwischengespeichert. Wenn der Windsensor per `SETWINDENABLE` deaktiviert ist, liefert `GETBEAUFORT` `0`. Wenn kein gültiger Sensorwert vorliegt, kommt `NAK_GETBEAUFORT:NOWIND`. Wenn keine passende Hardware vorhanden ist, kommt `NAK_GETBEAUFORT:NOHW`.

**Praxis:** Dieser Befehl ist ideal, wenn der Master die Windstärke nicht als exakten km/h-Wert, sondern als robuste Beaufortstufe anzeigen oder für einfache Schaltschwellen verwenden soll. Typisch wäre zum Beispiel eine Anzeige „ruhig“, „frisch“ oder „stark“, ohne selbst die Umrechnung aus km/h machen zu müssen.

**Interpretation:** `0` bedeutet Windstille oder deaktivierten Sensor. Die Stufen `1` bis `12` entsprechen der vom Sensor gelieferten Beaufortskala. Für Warnungen oder Sperren sollte der Master trotzdem zusätzlich die direkte Geschwindigkeit aus `GETANEMO` berücksichtigen, wenn exakte Grenzwerte nötig sind.

---

#### `GETANEMOOF` {#cmd-GETANEMOOF}

**Was es macht:** Liest den eingestellten Offset für die Windgeschwindigkeit in km/h.

**Details:** Der Wert wird im NVS unter dem Key `ano` gespeichert und bei jedem Polling auf den Sensordatenstrom angewendet.

---

#### `SETANEMOOF` {#cmd-SETANEMOOF}

**Was es macht:** Setzt einen zusätzlichen Offset für die Windgeschwindigkeit in km/h.

**Details:** Wert mit 1 Nachkommastelle. Der Offset wird persistent gespeichert und sofort an den RS485-Sensorpfad übernommen. Zulässiger Bereich im Code: `-200,0` bis `+200,0` km/h.

**Praxis:** Dieser Befehl ist nur für Sonderfälle gedacht. Normalerweise sollte der RS485-Windsensor bereits die richtigen Werte liefern, sodass kein zusätzlicher Geschwindigkeits-Offset nötig ist.

---

#### `GETWINDDIR` {#cmd-GETWINDDIR}

**Was es macht:** Liest die Windrichtung des RS485-Wind-/Richtungssensors in Grad.

**Details:** Rückgabe mit 1 Nachkommastelle im Bereich `0,0` bis kleiner `360,0`. Im Code ist die Antwort historisch als `ACK_WINDDIR` umgesetzt, obwohl der Befehl `GETWINDDIR` heißt. Wenn der Sensor deaktiviert ist, wird `0,0` geliefert. Wenn kein gültiger Sensorwert vorliegt, kommt `NAK_WINDDIR:NOWIND`.

**Praxis:** Damit kann der Master direkt die aktuelle Windrichtung anzeigen. Das ist die echte gemessene Richtung des RS485-Sensors und nicht die aus dem Lastprofil geschätzte Windrichtung von `GETWIND`.

---

#### `GETWINDDIROF` {#cmd-GETWINDDIROF}

**Was es macht:** Liest den eingestellten Richtungs-Offset des Windsensors in Grad.

**Details:** Der Offset wird im NVS unter dem Key `wdo` gespeichert.

---

#### `SETWINDDIROF` {#cmd-SETWINDDIROF}

**Was es macht:** Setzt einen zusätzlichen Richtungs-Offset in Grad für den RS485-Windsensor.

**Details:** Wert mit 1 Nachkommastelle, sofort wirksam und persistent gespeichert. Zulässiger Bereich im Code: `-360,0` bis `+360,0` Grad.

**Praxis:** Sinnvoll, wenn der montierte Sensor mechanisch verdreht eingebaut ist und die Richtung im Master auf den echten Antennen-/Mastbezug korrigiert werden soll.

---

#### `GETWINDENABLE` {#cmd-GETWINDENABLE}

**Was es macht:** Zeigt an, ob der RS485-Wind-/Richtungssensor aktiv ist.

**Details:** Rückgabe `1` = aktiv, `0` = deaktiviert.

---

#### `SETWINDENABLE` {#cmd-SETWINDENABLE}

**Was es macht:** Aktiviert oder deaktiviert den RS485-Wind-/Richtungssensor.

**Details:** Bei `0` werden keine Sensorabfragen durchgeführt; `GETANEMO` und `GETWINDDIR` liefern dann `0`. Der Zustand wird persistent im NVS unter dem Key `wen` gespeichert.

**Praxis:** Nützlich für Tests oder wenn der Sensor zeitweise nicht vorhanden ist. Für den normalen Betrieb sollte der Wert auf `1` stehen.

---

### Kalibrierung & Statistik

#### `GETCALVALID` {#cmd-GETCALVALID}

**Was es macht:** Abfragen, ob eine gültige Kalibrierung gespeichert ist.

**Details:** 0 = nein, 1 = ja.

---

#### `GETANGLE1` {#cmd-GETANGLE1}

**Was es macht:** Liest den gespeicherten Öffnungswinkel der Antenne 1.

**Telegramm:** `#0:20:GETANGLE1:1:20,01$`

**Antwort:** `#20:0:ACK_GETANGLE1:<WERT>:<CS>$`

**Details:** Der Wert wird nur im Rotor gespeichert und dem Master über RS485 bereitgestellt. Er wird nicht für die Motorregelung verwendet. Standardwert ist `0,0`. Zulässiger Bereich ist `0,0` bis `360,0` Grad mit einer Nachkommastelle.

**Praxis:** Dieser Wert ist für den Öffnungswinkel der ersten möglichen Antenne gedacht. Der Master kann damit Antennendaten zentral im Rotor hinterlegen und später wieder abrufen.

---

#### `SETANGLE1` {#cmd-SETANGLE1}

**Was es macht:** Speichert den Öffnungswinkel der Antenne 1 im NVS.

**Telegramm:** `#0:20:SETANGLE1:120,0:21,20$`

**Antwort:** `#20:0:ACK_SETANGLE1:1:<CS>$`

**Details:** Der Wert wird persistent gespeichert und steht nach einem Neustart weiter zur Verfügung. Zulässig sind Werte von `0,0` bis `360,0` Grad. Außerhalb des Bereichs wird der Befehl mit NAK abgelehnt.

---

#### `GETANGLE2` {#cmd-GETANGLE2}

**Was es macht:** Liest den gespeicherten Öffnungswinkel der Antenne 2.

**Telegramm:** `#0:20:GETANGLE2:1:20,01$`

**Antwort:** `#20:0:ACK_GETANGLE2:<WERT>:<CS>$`

**Details:** Verhalten und Bereich wie bei `GETANGLE1`. Standardwert ist `0,0`.

---

#### `SETANGLE2` {#cmd-SETANGLE2}

**Was es macht:** Speichert den Öffnungswinkel der Antenne 2 im NVS.

**Telegramm:** `#0:20:SETANGLE2:120,0:21,20$`

**Antwort:** `#20:0:ACK_SETANGLE2:1:<CS>$`

**Details:** Persistent gespeichert, Bereich `0,0` bis `360,0` Grad, Standardwert `0,0`.

---

#### `GETANGLE3` {#cmd-GETANGLE3}

**Was es macht:** Liest den gespeicherten Öffnungswinkel der Antenne 3.

**Telegramm:** `#0:20:GETANGLE3:1:20,01$`

**Antwort:** `#20:0:ACK_GETANGLE3:<WERT>:<CS>$`

**Details:** Verhalten und Bereich wie bei `GETANGLE1`. Standardwert ist `0,0`.

---

#### `SETANGLE3` {#cmd-SETANGLE3}

**Was es macht:** Speichert den Öffnungswinkel der Antenne 3 im NVS.

**Telegramm:** `#0:20:SETANGLE3:120,0:21,20$`

**Antwort:** `#20:0:ACK_SETANGLE3:1:<CS>$`

**Details:** Persistent gespeichert, Bereich `0,0` bis `360,0` Grad, Standardwert `0,0`.

---

#### `GETANTOFF1` {#cmd-GETANTOFF1}

**Was es macht:** Liest den gespeicherten Winkelversatz der Antenne 1.

**Telegramm:** `#0:20:GETANTOFF1:1:20,01$`

**Antwort:** `#20:0:ACK_GETANTOFF1:<WERT>:<CS>$`

**Details:** Der Winkelversatz wird nur gespeichert und dem Master bereitgestellt. Der Rotor nutzt ihn nicht direkt in der Regelung. Standardwert ist `0,0`. Bereich `0,0` bis `360,0` Grad.

**Praxis:** Hier kann der Master den mechanischen oder elektrischen Versatz der Antenne 1 zum Rotor-Nullpunkt hinterlegen.

---

#### `SETANTOFF1` {#cmd-SETANTOFF1}

**Was es macht:** Speichert den Winkelversatz der Antenne 1 im NVS.

**Telegramm:** `#0:20:SETANTOFF1:45,0:20,45$`

**Antwort:** `#20:0:ACK_SETANTOFF1:1:<CS>$`

**Details:** Persistent gespeichert, Bereich `0,0` bis `360,0` Grad, Standardwert `0,0`.

---

#### `GETANTOFF2` {#cmd-GETANTOFF2}

**Was es macht:** Liest den gespeicherten Winkelversatz der Antenne 2.

**Telegramm:** `#0:20:GETANTOFF2:1:20,01$`

**Antwort:** `#20:0:ACK_GETANTOFF2:<WERT>:<CS>$`

**Details:** Verhalten und Bereich wie bei `GETANTOFF1`. Standardwert ist `0,0`.

---

#### `SETANTOFF2` {#cmd-SETANTOFF2}

**Was es macht:** Speichert den Winkelversatz der Antenne 2 im NVS.

**Telegramm:** `#0:20:SETANTOFF2:45,0:20,45$`

**Antwort:** `#20:0:ACK_SETANTOFF2:1:<CS>$`

**Details:** Persistent gespeichert, Bereich `0,0` bis `360,0` Grad, Standardwert `0,0`.

---

#### `GETANTOFF3` {#cmd-GETANTOFF3}

**Was es macht:** Liest den gespeicherten Winkelversatz der Antenne 3.

**Telegramm:** `#0:20:GETANTOFF3:1:20,01$`

**Antwort:** `#20:0:ACK_GETANTOFF3:<WERT>:<CS>$`

**Details:** Verhalten und Bereich wie bei `GETANTOFF1`. Standardwert ist `0,0`.

---

#### `SETANTOFF3` {#cmd-SETANTOFF3}

**Was es macht:** Speichert den Winkelversatz der Antenne 3 im NVS.

**Telegramm:** `#0:20:SETANTOFF3:45,0:20,45$`

**Antwort:** `#20:0:ACK_SETANTOFF3:1:<CS>$`

**Details:** Persistent gespeichert, Bereich `0,0` bis `360,0` Grad, Standardwert `0,0`.

---

#### `SETCAL` {#cmd-SETCAL}

**Was es macht:** Kalibrierfahrt starten (0→360→0) und Stromprofil lernen.

**Details:** Nur wenn REFF gemacht wurde.

---

#### `GETCALSTATE` {#cmd-GETCALSTATE}

**Was es macht:** Status der Kalibrierfahrt abfragen.

**Details:** Antwort: <state>;<progress> (progress 0..100).

**Interpretation:** `state` ist der Zustand der Kalibrierfahrt (0=IDLE, 1=RUNNING, 2=DONE, 3=ABORT/ERROR). `progress` läuft von 0 bis 100.

---

#### `ABORTCAL` {#cmd-ABORTCAL}

**Was es macht:** Kalibrierfahrt abbrechen.

**Details:** Stoppt die Kalibrierung, Daten bleiben wie vorher.

---

#### `DELCAL` {#cmd-DELCAL}

**Was es macht:** Gespeicherte Kalibrierung löschen.

**Details:** Danach ist GETCALVALID=0.

---

#### `CLRSTAT` {#cmd-CLRSTAT}

**Was es macht:** Live-Statistik (Betriebsdaten) löschen.

**Details:** Gut vor Tests, damit nur aktuelle Fahrten zählen.

---

#### `GETLOADSTAT` {#cmd-GETLOADSTAT}

**Was es macht:** Zusammenfassung der Last-Statistik.

**Details:** Antwort: mean%;peak%;coh%;moves. Werte sind Delta zur Kalibrierung.

**Hinweis:** `GETLOADSTAT` basiert auf der normalen Last-Statistik und nicht auf `GETACCBINS`. Für schnelle aktuelle Änderungen pro Winkel ist [`GETACCBINS`](#cmd-GETACCBINS) gedacht.

**Interpretation:** `mean` ist die durchschnittliche Abweichung in %, `peak` ein Spitzenwert, `coh` die „Gerichtetheit“ (hoch = Wind aus einer Richtung), `moves` wie viele große Fahrten in der Statistik sind.

---

#### `GETWIND` {#cmd-GETWIND}

**Was es macht:** Wind-Schätzung aus Motorlastprofil.

**Details:** Antwort: peak%;coh%;dirDeg;peakDeg.

**Interpretation:** `dirDeg` ist die geschätzte Windrichtung (wo es schwer geht). `peakDeg` ist der Winkel mit der stärksten Spitze. Bei Windstille sind peak/coh oft nahe 0.

---

#### `GETCALBINS` {#cmd-GETCALBINS}

**Was es macht:** Kalibrier-Bins lesen (72 Bins).

**Details:** Params: dir;start;count. Rückgabe: dir;start;count;v1;v2;...

---

#### `GETLIVEBINS` {#cmd-GETLIVEBINS}

**Was es macht:** Live-Bins lesen (72 Bins).

**Details:** Wie GETCALBINS, aber aktuelle Betriebsdaten.

**Verhalten:** Diese Statistik ist träge und geglättet. Sie ist für eine robuste Langzeitbetrachtung gedacht und wird nur während geeigneter Fahrfenster aufgebaut. Bereiche am Anfang und Ende einer Fahrt werden ignoriert. Die Werte bauen sich über mehrere größere Fahrten auf.

**Verwendung:** Gut geeignet, wenn der Master eine ruhige Langzeit-Heatmap oder den Vergleich zur Kalibrierung zeigen soll. Wenn eine Veränderung schnell sichtbar werden soll, ist [`GETACCBINS`](#cmd-GETACCBINS) besser geeignet.

---

#### `GETACCBINS` {#cmd-GETACCBINS}

**Was es macht:** Liest eine schnelle aktuelle Last-Statistik als 72 Winkel-Bins.

**Telegramm:** `#0:20:GETACCBINS:1;0;12:20,12$`

**Details:** Aufbau wie `GETCALBINS` und `GETLIVEBINS`: Parameter sind `DIR;START;COUNT`. Antwort ist `DIR;START;COUNT;V1;V2;...`. Es gibt insgesamt 72 Bins mit 5° pro Bin, pro Telegramm können 1 bis 12 Bins gelesen werden.

**Richtungen:** `DIR=1` für positive Richtung / CW / IS1, `DIR=2` für negative Richtung / CCW / IS2.

**Verhalten:** Diese Statistik ist ausdrücklich für schnelle aktuelle Änderungen gedacht. Neue Messwerte laufen mit hohem Gewicht ein, dadurch reagiert sie deutlich schneller als `GETLIVEBINS`. Sie funktioniert auch ohne vorhandene Kalibrierung und zeigt damit direkt die aktuelle Stromverteilung über den Winkel.

**Ignorierte Bereiche:** Wie bei der Lastaufzeichnung werden am Anfang und Ende einer Fahrt Randbereiche ignoriert. Für `GETACCBINS` wird dafür ein eigener Rampen-/Ignore-Wert `RAPDG` verwendet, der vom Master eingestellt werden kann. So lässt sich festlegen, wie viel Start- und Bremsbereich nicht in die ACC-Statistik eingehen soll.

**Verwendung im Master:** `GETACCBINS` eignet sich für eine schnelle Heatmap, wenn akut sichtbar werden soll, an welcher Stelle die Stromaufnahme gerade ansteigt. Typischer Ablauf: Statistik mit [`SETACCBINSRST`](#cmd-SETACCBINSRST) löschen, eine oder mehrere gezielte Fahrten über den kritischen Bereich machen und danach die 72 Bins in 6 Abfragen lesen.

**Beispiel:** Für die ersten 12 Bins in positiver Richtung: `#0:20:GETACCBINS:1;0;12:20,12$`

**Interpretation:** Höhere Werte bedeuten höhere aktuelle Stromaufnahme in diesem Winkelbereich. Im Gegensatz zu `GETDELTABINS` wird hier kein Vergleich zur Kalibrierung gemacht. Der Master kann die Werte daher direkt als Live-Heatmap anzeigen oder selbst mit älteren Kurven vergleichen.

---

#### `SETACCBINSRST` {#cmd-SETACCBINSRST}

**Was es macht:** Löscht nur die schnelle ACC-Bin-Statistik.

**Telegramm:** `#0:20:SETACCBINSRST:1:20,01$`

**Details:** Der Parameter ist ein einfacher Auslösewert. Mit dem Befehl wird die neue ACC-Statistik zurückgesetzt, ohne die normale `GETLIVEBINS`-Statistik oder die Kalibrierdaten zu verändern.

**Verwendung:** Vor einem gezielten Test oder vor einer neuen Messreihe senden, damit nur die danach aufgenommenen Werte in `GETACCBINS` sichtbar sind. Das ist sinnvoll, wenn der Master eine kurzfristige Veränderung oder eine akute Schwergängigkeit erkennen soll.

**Abgrenzung zu CLRSTAT:** `CLRSTAT` betrifft die normale Live-Statistik. `SETACCBINSRST` ist nur für die schnelle ACC-Statistik gedacht.

---

#### `GETDELTABINS` {#cmd-GETDELTABINS}

**Was es macht:** Delta-Bins (Live minus Cal) in % lesen.

**Details:** Wie GETCALBINS, Werte können negativ sein.

---

### Bewegungsgrenzen & Offset

#### `GETBEGINDG` {#cmd-GETBEGINDG}

**Was es macht:** Min-Winkel (Achsenanfang) lesen.

**Details:** In 0,01°.

---

#### `SETBEGINDG` {#cmd-SETBEGINDG}

**Was es macht:** Min-Winkel setzen.

**Details:** Typisch 0,00.

---

#### `GETMAXDG` {#cmd-GETMAXDG}

**Was es macht:** Max-Winkel lesen.

**Details:** In 0,01°.

---

#### `SETMAXDG` {#cmd-SETMAXDG}

**Was es macht:** Max-Winkel setzen.

**Details:** Typisch 360,00.

---

#### `GETDGOFFSET` {#cmd-GETDGOFFSET}

**Was es macht:** Offset (Grad) lesen.

**Details:** Offset wird bei Positionsausgabe/Regelung abgezogen.

---

#### `SETDGOFFSET` {#cmd-SETDGOFFSET}

**Was es macht:** Offset setzen (Grad).

**Details:** Typisch 2,25 oder 2,50. Wird gespeichert.

---

### Homing Einstellungen

#### `GETHOMEPWM` {#cmd-GETHOMEPWM}

**Was es macht:** Homing-Max-PWM (%) lesen.

**Details:** Das ist die schnelle Homing-PWM.

---

#### `SETHOMEPWM` {#cmd-SETHOMEPWM}

**Was es macht:** Homing-Max-PWM (%) setzen.

**Details:** Typisch 60..100%.

---

#### `GETHOMEBACKOFF` {#cmd-GETHOMEBACKOFF}

**Was es macht:** Homing Rückzug (Grad) lesen.

**Details:** Wie weit nach Endschalter wieder wegfahren.

---

#### `SETHOMEBACKOFF` {#cmd-SETHOMEBACKOFF}

**Was es macht:** Homing Rückzug (Grad) setzen.

**Details:** Typisch 10..60°.

---

#### `GETHOMEBLSCALE` {#cmd-GETHOMEBLSCALE}

**Was es macht:** Homing: Skalierung der Backlash-Berechnung (0–1) lesen.

**Details:** Typischer Default `0,50`. Wert wird im Rotor gespeichert (NV-Key `hbl`, falls in der Firmware so benannt).

---

#### `SETHOMEBLSCALE` {#cmd-SETHOMEBLSCALE}

**Was es macht:** Homing: Faktor für die Backlash-Berechnung (0–1) setzen.

**Details:** Standard `0,50`. Wenn beim Umkehren zu viel Weg „weg“ wirkt: Wert vergrößern (max. 1,0).

---

#### `GETHOMRETURN` {#cmd-GETHOMRETURN}

**Was es macht:** Rückfahrt auf 0 nach Homing an/aus lesen.

**Details:** 1=ja, 0=nein.

---

#### `SETHOMERETURN` {#cmd-SETHOMERETURN}

**Was es macht:** Rückfahrt auf 0 nach Homing setzen.

**Details:** Meist 1.

---

#### `GETHOMETIMEOUT` {#cmd-GETHOMETIMEOUT}

**Was es macht:** Homing Timeout (ms) lesen.

**Details:** Typisch 60000..120000.

---

#### `SETHOMETIMEOUT` {#cmd-SETHOMETIMEOUT}

**Was es macht:** Homing Timeout (ms) setzen.

**Details:** Wenn Homing abbricht: erhöhen.

---

### Positionsfahrt Einstellungen

#### `GETPOSTIMEOUT` {#cmd-GETPOSTIMEOUT}

**Was es macht:** Positions-Timeout (ms) lesen.

**Details:** Max. Zeit für eine Positionsfahrt.

---

#### `SETPOSTIMEOUT` {#cmd-SETPOSTIMEOUT}

**Was es macht:** Positions-Timeout (ms) setzen.

**Details:** Typisch 30000..90000.

---

#### `GETARRTOL` {#cmd-GETARRTOL}

**Was es macht:** Ankunftstoleranz (Grad) lesen.

**Details:** Wie nah er ans Ziel muss.

---

#### `SETARRTOL` {#cmd-SETARRTOL}

**Was es macht:** Ankunftstoleranz (Grad) setzen.

**Details:** Typisch 0,02..0,20°.

---

#### `GETRAMP` {#cmd-GETRAMP}

**Was es macht:** Rampenlänge (Grad) lesen.

**Details:** Beschleunigen/Abbremsen über diesen Winkel.

---

#### `SETRAMP` {#cmd-SETRAMP}

**Was es macht:** Rampenlänge (Grad) setzen.

**Details:** Typisch 10..40°.

---

#### `GETMINPWM` {#cmd-GETMINPWM}

**Was es macht:** Min-PWM (%) lesen.

**Details:** Untergrenze für Bewegung.

---

#### `SETMINPWM` {#cmd-SETMINPWM}

**Was es macht:** Min-PWM (%) setzen.

**Details:** Typisch 20..35% je nach Haftreibung.

**Gute Werte:** Typisch `20..35`%. Wenn er beim Anfahren häufig stehen bleibt: MINPWM etwas hoch. Wenn er zu ruppig ist: MINPWM runter, aber Stall/Timeout im Blick behalten.

---

#### `GETMAXPWM` {#cmd-GETMAXPWM}

**Was es macht:** Max-PWM (%) lesen.

**Details:** Globales Limit für Positionsfahrt.

---

#### `SETMAXPWM` {#cmd-SETMAXPWM}

**Was es macht:** Max-PWM (%) setzen.

**Details:** Typisch 60..100%.

---

#### `GETPWM` {#cmd-GETPWM}

**Was es macht:** Aktuell freigegebenes PWM-Limit (%) lesen.

**Details:** Kann durch SETMAXPWM/SETPWM beeinflusst sein.

---

#### `SETPWM` {#cmd-SETPWM}

**Was es macht:** PWM-Limit (%) zur Laufzeit setzen (ohne Speichern).

**Details:** Gut für Tests/Debug.

---

### Factory-Reset

#### `RESET` {#cmd-RESET}

**Was es macht:** Alles auf den Auslieferungszustand zurücksetzen und den Controller neu starten.

**Parameter:** laut Firmware/Katalog typisch `1` als Bestätigung (siehe Bridge-Befehlsfenster).

**Achtung:** Alle Einstellungen inkl. NVS gehen verloren (Antennenversatz, Öffnungswinkel, Kalibrierung, IDs, …). Nur in der Werkstatt oder mit absicherter Konfiguration verwenden.

---

### Stall-Erkennung (zusätzliche Parameter)

Die Stall-**Timeout**-Zeit (`GETSTALLTIMEOUT`/`SETSTALLTIMEOUT`) steht weiter unten bei den Motorparametern. Ergänzend gibt es:

#### `GETSTALLEN` {#cmd-GETSTALLEN}

**Was es macht:** Stall-Erkennung lesen. `0` = aus, `1` = ein.

---

#### `SETSTALLEN` {#cmd-SETSTALLEN}

**Was es macht:** Stall-Erkennung aktivieren (`1`) oder deaktivieren (`0`). Wird gespeichert.

---

#### `GETMINSTALLPWM` {#cmd-GETMINSTALLPWM}

**Was es macht:** Mindest-PWM für die Stall-Erkennung lesen (%). Unterhalb dieser PWM wird keine Stall-Auswertung erwartet (verhindert Fehltrigger bei sehr kleinem Antrieb).

---

#### `SETMINSTALLPWM` {#cmd-SETMINSTALLPWM}

**Was es macht:** Mindest-PWM für Stall-Erkennung setzen (0…100 %).

**Empfehlung:** So wählen, dass der Motor zuverlässig „Anzug“ hat, bevor Stall überwacht wird (typisch im mittleren Bereich, siehe Katalog-Default).

---

#### `GETSTALLMINCOUNTS` {#cmd-GETSTALLMINCOUNTS}

**Was es macht:** Mindestanzahl Encoder-Counts im Stall-Zeitfenster lesen (Plausibilität: PWM an, kaum Bewegung).

---

#### `SETSTALLMINCOUNTS` {#cmd-SETSTALLMINCOUNTS}

**Was es macht:** Mindest-Counts im Stall-Zeitfenster setzen (Integer ≥ 1).

---

### Strommessung: Filter & Zeiten

#### `GETFILTERLEN` {#cmd-GETFILTERLEN}

**Was es macht:** Länge des Strom-Mittelwertfilters lesen. Gehört zu `SETISFILTERLEN`.

---

#### `SETISFILTERLEN` {#cmd-SETISFILTERLEN}

**Was es macht:** Strom-Mittelwertfilter-Länge setzen (1…32). Größer = ruhiger, langsamer.

---

#### `GETISHOLDMS` {#cmd-GETISHOLDMS}

**Was es macht:** Hold-Zeit für das Strom-Hardlimit lesen (ms). Der Hard-Stop muss so lange überschritten sein.

---

#### `SETISHOLDMS` {#cmd-SETISHOLDMS}

**Was es macht:** Hold-Zeit Strom-Hardlimit setzen (ms).

---

#### `GETGRACEMS` {#cmd-GETGRACEMS}

**Was es macht:** Grace-Zeit des Stromschutzes lesen (ms). Gehört zu `SETISGRACEMS`.

---

#### `SETISGRACEMS` {#cmd-SETISGRACEMS}

**Was es macht:** Grace-Zeit nach Start/Umkehr setzen (ms). In dieser Schonzeit kann das Hardlimit ignoriert werden, damit Anfahren nicht sofort triggert.

---

### Homing: Such-PWM

#### `GETHOMESEEKPPWM` {#cmd-GETHOMESEEKPPWM}

**Was es macht:** Zusätzliche Such-PWM für die Homing-**Suchphase** lesen (%).

---

#### `SETHOMESEEKPPWM` {#cmd-SETHOMESEEKPPWM}

**Was es macht:** Zusätzliche Such-PWM für die Homing-Suchphase setzen (0…100 %). Ergänzt die Homing-PWM in der Phase, in der der Endschalter gesucht wird.

---

### Encoder: Counts & Typ (RS485)

Ergänzung zu Abschnitt [„Encoder-Varianten“](#encoders): Ring vs. Motor können hier fein eingestellt und der Typ umgeschaltet werden.

#### `GETENCCRI` {#cmd-GETENCCRI}

**Was es macht:** Counts pro 360° für den **Ring**-Encoder lesen (Integer, typisch ca. 160000).

---

#### `SETENCCRI` {#cmd-SETENCCRI}

**Was es macht:** Counts pro 360° Ring-Encoder setzen. Wird gespeichert.

---

#### `GETENCCAX` {#cmd-GETENCCAX}

**Was es macht:** Counts pro 360° für den **Achsen-/Motor**-Encoder lesen (Integer, typisch im Zehntausender-Bereich).

---

#### `SETENCCAX` {#cmd-SETENCCAX}

**Was es macht:** Counts pro 360° Achsen-/Motor-Encoder setzen. Wird gespeichert.

---

#### `GETENCTYPE` {#cmd-GETENCTYPE}

**Was es macht:** Encoder-Variante lesen: `1` = Axis/Motor, `2` = Ring.

---

#### `SETENCTYPE` {#cmd-SETENCTYPE}

**Was es macht:** Encoder-Variante setzen (`1` oder `2`). Nach Wechsel Homing und Counts prüfen.

---

#### `GETSTALLTIMEOUT` {#cmd-GETSTALLTIMEOUT}

**Was es macht:** Stall-Timeout (ms) lesen.

**Details:** Zeit, bis SE\_STALL auslöst.

---

#### `SETSTALLTIMEOUT` {#cmd-SETSTALLTIMEOUT}

**Was es macht:** Stall-Timeout (ms) setzen.

**Details:** Typisch 1500..4000ms.

**Gute Werte:** Start oft bei `2000` ms. Wenn der Motor bei kleinen PWM erst spät loskommt (zähes Getriebe), eher `3000..5000` ms. Wenn Blockaden sehr schnell erkannt werden sollen, eher kleiner – dann braucht es aber genug „Kick“.

---

### Lastprofil / Warnschwellen

#### `GETCOLDT` {#cmd-GETCOLDT}

**Was es macht:** Kälte-Schwelle (°C) lesen.

**Details:** Unterhalb dieser Temp wird Extra-Reibung erlaubt.

---

#### `SETCOLDT` {#cmd-SETCOLDT}

**Was es macht:** Kälte-Schwelle (°C) setzen.

**Details:** Typisch 0..10°C.

---

#### `GETCOLDP` {#cmd-GETCOLDP}

**Was es macht:** Extra-Reibung bei Kälte (%) lesen.

**Details:** Erlaubter Zuschlag in %.

---

#### `SETCOLDP` {#cmd-SETCOLDP}

**Was es macht:** Extra-Reibung bei Kälte (%) setzen.

**Details:** Typisch 10..25%.

---

#### `GETCALIGNDG` {#cmd-GETCALIGNDG}

**Was es macht:** Kalibrierfahrt: Rampenbereich, der ignoriert wird (Grad).

**Details:** Damit Stromwerte nicht durch Beschleunigen/Abbremsen verfälscht werden.

---

#### `SETCALIGNDG` {#cmd-SETCALIGNDG}

**Was es macht:** Kalibrierfahrt: Rampenbereich (Grad) setzen.

**Details:** Typisch 10° (Test) oder 30° (robust).

---

#### `GETSTATMINDG` {#cmd-GETSTATMINDG}

**Was es macht:** Mindestbewegung in Grad für Statistik.

**Details:** Nur Bewegungen grösser als dieser Wert fliessen in Load-Statistik.

---

#### `SETSTATMINDG` {#cmd-SETSTATMINDG}

**Was es macht:** Mindestbewegung in Grad setzen.

**Details:** Typisch 30..90°.

---

#### `GETDRAG` {#cmd-GETDRAG}

**Was es macht:** Schwellwert „mehr Reibung“ (%) lesen.

**Details:** Mittelwert-Schwellwert.

---

#### `SETDRAG` {#cmd-SETDRAG}

**Was es macht:** Schwellwert „mehr Reibung“ (%) setzen.

**Details:** Typisch 15..30%.

---

#### `GETDRAGBINS` {#cmd-GETDRAGBINS}

**Was es macht:** Wie viele Winkel-Bins müssen drüber sein (%) lesen.

**Details:** Damit einzelne Ausreisser nicht warnen.

---

#### `SETDRAGBINS` {#cmd-SETDRAGBINS}

**Was es macht:** Drag-Bins-Prozent setzen.

**Details:** Typisch 30..70%.

---

#### `GETDRAGPERSIST` {#cmd-GETDRAGPERSIST}

**Was es macht:** Wie viele große Fahrten hintereinander benötigt werden.

**Details:** Damit kurze Störungen nicht warnen.

---

#### `SETDRAGPERSIST` {#cmd-SETDRAGPERSIST}

**Was es macht:** Persistenz (Anzahl Fahrten) setzen.

**Details:** Typisch 3..5.

---

#### `GETWINDPEAK` {#cmd-GETWINDPEAK}

**Was es macht:** Wind: Peak-Schwellwert (%) lesen.

**Details:** Für Böen/kurzzeitig hohe Last.

---

#### `SETWINDPEAK` {#cmd-SETWINDPEAK}

**Was es macht:** Wind: Peak-Schwellwert (%) setzen.

**Details:** Typisch 25..60%.

---

#### `GETWINDCOH` {#cmd-GETWINDCOH}

**Was es macht:** Wind: Mindest-Kohärenz (%) lesen.

**Details:** Wie „gerichtet“ die Last sein muss.

---

#### `SETWINDCOH` {#cmd-SETWINDCOH}

**Was es macht:** Wind: Mindest-Kohärenz (%) setzen.

**Details:** Typisch 50..70%.

---

### Sonstiges

#### `JOG` {#cmd-JOG}

**Was es macht:** Manuelles Verfahren (aktuell deaktiviert).

**Details:** Gibt NAK\_JOG:DISABLED.

---

### Weitere

#### `GETHANDSPEED` {#cmd-GETHANDSPEED}

**Was es macht:** Handspeed (%) lesen.

**Details:** Sicher langsames Verfahren (lokal/Service).

---

#### `SETHANDSPEED` {#cmd-SETHANDSPEED}

**Was es macht:** Handspeed (%) setzen.

**Details:** Typisch 10..40%.

---

#### `GETDEADMAN` {#cmd-GETDEADMAN}

**Was es macht:** Deadman/Keepalive Timeout (ms) lesen.

**Details:** 0 = aus.

---

#### `SETDEADMAN` {#cmd-SETDEADMAN}

**Was es macht:** Deadman Timeout (ms) setzen.

**Details:** Typisch 0 (aus) oder 2000..5000ms.

**Gute Werte:** Wenn der Master sicher regelmäßig Befehle sendet (z.B. alle 100–500 ms), dann ist `2000..5000` ms ein guter Start. Wenn das nicht benötigt wird: `0` (aus).

---

#### `GETIWARN` {#cmd-GETIWARN}

**Was es macht:** Strom-Warnschwelle (mV) lesen.

**Details:** Nur Warnung, kein Stop.

---

#### `SETIWARN` {#cmd-SETIWARN}

**Was es macht:** Strom-Warnschwelle (mV) setzen.

**Details:** Typisch 200..350mV (abh. Sensor).

---

#### `GETIMAX` {#cmd-GETIMAX}

**Was es macht:** Strom-Abschaltschwelle (mV) lesen.

**Details:** Führt zu Stop + Error.

---

#### `SETIMAX` {#cmd-SETIMAX}

**Was es macht:** Strom-Abschaltschwelle (mV) setzen.

**Details:** Typisch 300..600mV (abh. Sensor).

---

[↑ Inhaltsverzeichnis](#toc)

## 5. INO‑Konfig‑Variablen – Tabelle {#vars-table}

Diese Tabelle listet die wichtigsten Einstellungen aus der `.ino`. „Kurzname“ ist der Preferences‑Key (Variante 1), also der kurze Speichername im EEPROM/NVS.

| Variablenname | Kurzname | EEPROM | RS485 | Kurzbeschreibung |
| --- | --- | --- | --- | --- |
| `g_slaveId` | `id` | ja | SETID/GETID | Slave-Adresse (RS485) |
| `g_axisMinDeg01` | `amin` | ja | SETBEGINDG/GETBEGINDG | Min-Winkel (0,01°) |
| `g_axisMaxDeg01` | `amax` | ja | SETMAXDG/GETMAXDG | Max-Winkel (0,01°) |
| `g_dgOffsetDeg01` | `dgo` | ja | SETDGOFFSET/GETDGOFFSET | Offset in Grad (0,01°) |
| `g_homeFastPwmPercent` | `hfp` | ja | SETHOMEPWM/GETHOMEPWM | Homing: Max-PWM (%) |
| `g_homeSeekMinPwmPercent` | `-` | nein | - | Homing Phase A: Ziel-PWM (%) zum linken Endschalter |
| `g_homeBackoff` | `hbo` | ja | SETHOMEBACKOFF/GETHOMEBACKOFF | Homing: Rückzug vom Endschalter (Grad) |
| `g_homeBacklashScale` | `hbl` | ja | SETHOMEBLSCALE/GETHOMEBLSCALE | Homing: Backlash-Berechnung (0–1) |
| `g_homeReturnToZero` | `hrz` | ja | SETHOMERETURN/GETHOMRETURN | Homing: danach auf 0 fahren (1/0) |
| `g_homeTimeoutMs` | `hto` | ja | SETHOMETIMEOUT/GETHOMETIMEOUT | Homing: Timeout (ms) |
| `g_posTimeoutMs` | `pto` | ja | SETPOSTIMEOUT/GETPOSTIMEOUT | Positionsfahrt: Timeout (ms) |
| `g_handSpeedPercent` | `hsp` | ja | SETHANDSPEED/GETHANDSPEED | Handspeed (%) |
| `g_cmdTimeoutMs` | `dman` | ja | SETDEADMAN/GETDEADMAN | Deadman/Keepalive (ms, 0=aus) |
| `g_arriveTolDeg01` | `atol` | ja | SETARRTOL/GETARRTOL | Ankunftstoleranz (Grad) |
| `g_rampDistDeg` | `ramp` | ja | SETRAMP/GETRAMP | Rampenlänge (Grad) |
| `g_minPwm` | `minp` | ja | SETMINPWM/GETMINPWM | Min-PWM (%) |
| `g_pwmMaxAbsNv` | `maxp` | ja | SETMAXPWM/GETMAXPWM | Max-PWM (%) (dauerhaft) |
| `g_pwmMaxAbsCmd` | `-` | nein | SETPWM/GETPWM | Max-PWM (%) (nur Laufzeit) |
| `g_isSoftWarnMv` | `iw` | ja | SETIWARN/GETIWARN | Strom: Warnschwelle (mV) |
| `g_isHardStopMv` | `imax` | ja | SETIMAX/GETIMAX | Strom: Abschaltschwelle (mV) |
| `g_isGraceMs` | `-` | nein | - | Strom: Schonzeit nach Start/Umkehr (ms) |
| `g_isHardHoldMs` | `-` | nein | - | Strom: Hardlimit muss so lange anliegen (ms) |
| `g_isFilterLen` | `-` | nein | - | Strom: Filterlänge (Samples) |
| `g_isSampleIntervalMs` | `-` | nein | - | Strom: Abtastintervall (ms) |
| `g_stallMonitorEnabled` | `-` | nein | - | Stall-Erkennung an/aus |
| `g_minStallPwm` | `-` | nein | - | Stall: Mindest-PWM für Arming (%) |
| `g_stallTimeoutMs` | `stto` | ja | SETSTALLTIMEOUT/GETSTALLTIMEOUT | Stall: Timeout (ms) |
| `g_stallMinCounts` | `-` | nein | - | Stall: Mindest-Counts im Timeout |
| `g_tempWarnAmbientC` | `twa` | ja | SETTEMPA/GETTEMPAW | Temp: Warnschwelle Umgebung (°C) |
| `g_tempWarnMotorC` | `twm` | ja | SETTEMPM/GETTEMPMW | Temp: Warnschwelle Motor (°C) |
| `g_coldTempDegC` | `cth` | ja | SETCOLDT/GETCOLDT | Kälte-Schwelle (°C) |
| `g_coldExtraDragPct` | `cpx` | ja | SETCOLDP/GETCOLDP | Extra-Reibung bei Kälte (%) |
| `g_calIgnoreRampDeg` | `cig` | ja | SETCALIGNDG/GETCALIGNDG | Kalibrierung: Rampenbereich ignorieren (°) |
| `g_statMinMoveDeg` | `smm` | ja | SETSTATMINDG/GETSTATMINDG | Statistik: Mindestbewegung (°) |
| `g_dragWarnPct` | `drw` | ja | SETDRAG/GETDRAG | Warnung: Reibung grösser (Mittelwert %) |
| `g_dragWarnBinsPct` | `drb` | ja | SETDRAGBINS/GETDRAGBINS | Warnung: Anteil Bins über Schwellwert (%) |
| `g_dragPersistMoves` | `drn` | ja | SETDRAGPERSIST/GETDRAGPERSIST | Warnung: Persistenz (Fahrten) |
| `g_windPeakPct` | `wpk` | ja | SETWINDPEAK/GETWINDPEAK | Warnung: Windböe Peak (%) |
| `g_windCoherenceMin` | `wco` | ja | SETWINDCOH/GETWINDCOH | Wind-Erkennung: Kohärenz Minimum (%) |
| `g_anemoOffsetKmh` | `ano` | ja | SETANEMOOF/GETANEMOOF | RS485-Windsensor: Geschwindigkeits-Offset (km/h) |
| `g_windDirOffsetDeg` | `wdo` | ja | SETWINDDIROF/GETWINDDIROF | RS485-Windsensor: Richtungs-Offset (Grad) |
| `g_windEnable` | `wen` | ja | SETWINDENABLE/GETWINDENABLE | RS485-Windsensor aktiv/inaktiv |
| `g_windDirOffsetDeg` | `wdo` | ja | SETWINDDIROF/GETWINDDIROF | RS485-Windsensor: Richtungs-Offset (Grad) |
| `g_windEnable` | `wen` | ja | SETWINDENABLE/GETWINDENABLE | RS485-Windsensor aktiv/inaktiv |

[↑ Inhaltsverzeichnis](#toc)

## 6. INO‑Variablen – Erklärung & gute Einstellungen {#vars-details}

Hier sind die Variablen in einfachen Worten erklärt. Wenn „gut“ genannt wird, ist das ein Startwert – am Ende entscheidet deine Mechanik (Haftreibung, Getriebe, Windlast, Temperatur).

### `g_slaveId`

**Kurz:** Slave-Adresse (RS485)

**Default:** `static uint8_t g_slaveId = 20;`

**Speicherung:** Ja (Key `id`). Ändern über RS485: **SETID/GETID**.

---

### `g_axisMinDeg01`

**Kurz:** Min-Winkel (0,01°)

**Default:** `static int32_t g_axisMinDeg01 = 0;`

**Speicherung:** Ja (Key `amin`). Ändern über RS485: **SETBEGINDG/GETBEGINDG**.

---

### `g_axisMaxDeg01`

**Kurz:** Max-Winkel (0,01°)

**Default:** `static int32_t g_axisMaxDeg01 = 36000;`

**Speicherung:** Ja (Key `amax`). Ändern über RS485: **SETMAXDG/GETMAXDG**.

---

### `g_dgOffsetDeg01`

**Kurz:** Offset in Grad (0,01°)

**Default:** `static int32_t g_dgOffsetDeg01 = 250;`

**Speicherung:** Ja (Key `dgo`). Ändern über RS485: **SETDGOFFSET/GETDGOFFSET**.

---

### `g_homeFastPwmPercent`

**Kurz:** Homing: Max-PWM (%)

**Default:** `static float g_homeFastPwmPercent = 100.0f;`

**Speicherung:** Ja (Key `hfp`). Ändern über RS485: **SETHOMEPWM/GETHOMEPWM**.

---

### `g_homeSeekMinPwmPercent`

**Kurz:** Homing Phase A: Ziel-PWM (%) zum linken Endschalter

**Default:** `static float g_homeSeekMinPwmPercent = 60.0f;`

**Speicherung:** Nein (nur Firmware/INO). Falls das später per RS485 änderbar sein soll, kann dafür ein GET/SET‑Befehl ergänzt werden.

---

### `g_homeBackoff`

**Kurz:** Homing: Rückzug vom Endschalter (Grad)

**Default:** `static float g_homeBackoff = 30.0f;`

**Speicherung:** Ja (Key `hbo`). Ändern über RS485: **SETHOMEBACKOFF/GETHOMEBACKOFF**.

---

### `g_homeBacklashScale`

**Kurz:** Homing: Skalierung der Backlash-Berechnung (0–1)

**Default:** `static float g_homeBacklashScale = 0.5f;` (Bezeichnung in der Firmware kann abweichen.)

**Speicherung:** Ja (Key `hbl`, sofern in der Firmware vorgesehen). Ändern über RS485: **SETHOMEBLSCALE/GETHOMEBLSCALE**.

---

### `g_homeReturnToZero`

**Kurz:** Homing: danach auf 0 fahren (1/0)

**Default:** `static bool g_homeReturnToZero = true;`

**Speicherung:** Ja (Key `hrz`). Ändern über RS485: **SETHOMERETURN/GETHOMRETURN**.

---

### `g_homeTimeoutMs`

**Kurz:** Homing: Timeout (ms)

**Default:** `static uint32_t g_homeTimeoutMs = 120000;`

**Speicherung:** Ja (Key `hto`). Ändern über RS485: **SETHOMETIMEOUT/GETHOMETIMEOUT**.

---

### `g_posTimeoutMs`

**Kurz:** Positionsfahrt: Timeout (ms)

**Default:** `static uint32_t g_posTimeoutMs = 60000;`

**Speicherung:** Ja (Key `pto`). Ändern über RS485: **SETPOSTIMEOUT/GETPOSTIMEOUT**.

---

### `g_handSpeedPercent`

**Kurz:** Handspeed (%)

**Default:** `static float g_handSpeedPercent = 25.0f;`

**Speicherung:** Ja (Key `hsp`). Ändern über RS485: **SETHANDSPEED/GETHANDSPEED**.

---

### `g_cmdTimeoutMs`

**Kurz:** Deadman/Keepalive (ms, 0=aus)

**Default:** `static uint32_t g_cmdTimeoutMs = 0;`

**Speicherung:** Ja (Key `dman`). Ändern über RS485: **SETDEADMAN/GETDEADMAN**.

**Praxis:** Diese Zeit ist nur sinnvoll, wenn der Master während Bewegung regelmäßig „lebt“. Im Stillstand wird der Deadman nicht benötigt.

---

### `g_arriveTolDeg01`

**Kurz:** Ankunftstoleranz (Grad)

**Default:** `static int32_t g_arriveTolDeg01 = 2; // 0,02deg`

**Speicherung:** Ja (Key `atol`). Ändern über RS485: **SETARRTOL/GETARRTOL**.

---

### `g_rampDistDeg`

**Kurz:** Rampenlänge (Grad)

**Default:** `static float g_rampDistDeg = 30.0f;`

**Speicherung:** Ja (Key `ramp`). Ändern über RS485: **SETRAMP/GETRAMP**.

---

### `g_minPwm`

**Kurz:** Min-PWM (%)

**Default:** `static float g_minPwm = 20.0f;`

**Speicherung:** Ja (Key `minp`). Ändern über RS485: **SETMINPWM/GETMINPWM**.

**Praxis:** Wenn der Motor manchmal nicht losfährt, ist MINPWM oft zu klein. Wenn er zu ruppig anfährt, ist MINPWM zu groß oder die Rampe zu kurz.

---

### `g_pwmMaxAbsNv`

**Kurz:** Max-PWM (%) (dauerhaft)

**Default:** `static float g_pwmMaxAbsNv = 100.0f;`

**Speicherung:** Ja (Key `maxp`). Ändern über RS485: **SETMAXPWM/GETMAXPWM**.

---

### `g_pwmMaxAbsCmd`

**Kurz:** Max-PWM (%) (nur Laufzeit)

**Default:** `static float g_pwmMaxAbsCmd = 100.0f;`

**Speicherung:** Nein (nur Firmware/INO). Falls das später per RS485 änderbar sein soll, kann dafür ein GET/SET‑Befehl ergänzt werden.

---

### `g_isSoftWarnMv`

**Kurz:** Strom: Warnschwelle (mV)

**Default:** `static uint32_t g_isSoftWarnMv = 250; // Warnschwelle (mV) -> nur Warnung/Log, KEIN Stop`

**Speicherung:** Ja (Key `iw`). Ändern über RS485: **SETIWARN/GETIWARN**.

---

### `g_isHardStopMv`

**Kurz:** Strom: Abschaltschwelle (mV)

**Default:** `static uint32_t g_isHardStopMv = 350; // Abschaltschwelle (mV) -> Fault + Motor aus`

**Speicherung:** Ja (Key `imax`). Ändern über RS485: **SETIMAX/GETIMAX**.

---

### `g_isGraceMs`

**Kurz:** Strom: Schonzeit nach Start/Umkehr (ms)

**Default:** `static uint32_t g_isGraceMs = 200; // Schonzeit nach Start/Umkehr (ms): HardStop wird ignoriert`

**Speicherung:** Nein (nur Firmware/INO). Falls das später per RS485 änderbar sein soll, kann dafür ein GET/SET‑Befehl ergänzt werden.

---

### `g_isHardHoldMs`

**Kurz:** Strom: Hardlimit muss so lange anliegen (ms)

**Default:** `static uint32_t g_isHardHoldMs = 60; // HardStop muss so lange überschritten sein (ms)`

**Speicherung:** Nein (nur Firmware/INO). Falls das später per RS485 änderbar sein soll, kann dafür ein GET/SET‑Befehl ergänzt werden.

---

### `g_isFilterLen`

**Kurz:** Strom: Filterlänge (Samples)

**Default:** `static uint8_t g_isFilterLen = 16; // Mittelwert-Länge (1..32). Grösser=ruhiger, aber langsamer`

**Speicherung:** Nein (nur Firmware/INO). Falls das später per RS485 änderbar sein soll, kann dafür ein GET/SET‑Befehl ergänzt werden.

---

### `g_isSampleIntervalMs`

**Kurz:** Strom: Abtastintervall (ms)

**Default:** `static uint32_t g_isSampleIntervalMs = 5; // Abtastintervall (ms). Kleiner=schneller, kann mehr rauschen`

**Speicherung:** Nein (nur Firmware/INO). Falls das später per RS485 änderbar sein soll, kann dafür ein GET/SET‑Befehl ergänzt werden.

---

### `g_stallMonitorEnabled`

**Kurz:** Stall-Erkennung an/aus

**Default:** `static bool g_stallMonitorEnabled = true;`

**Speicherung:** Nein (nur Firmware/INO). Falls das später per RS485 änderbar sein soll, kann dafür ein GET/SET‑Befehl ergänzt werden.

---

### `g_minStallPwm`

**Kurz:** Stall: Mindest-PWM für Arming (%)

**Default:** `static float g_minStallPwm = 20.0f;`

**Speicherung:** Nein (nur Firmware/INO). Falls das später per RS485 änderbar sein soll, kann dafür ein GET/SET‑Befehl ergänzt werden.

---

### `g_stallTimeoutMs`

**Kurz:** Stall: Timeout (ms)

**Default:** `static uint32_t g_stallTimeoutMs = 2000;`

**Speicherung:** Ja (Key `stto`). Ändern über RS485: **SETSTALLTIMEOUT/GETSTALLTIMEOUT**.

**Praxis:** Wenn häufig ERR16 beim Anfahren auftritt, obwohl nichts klemmt, ist das Timeout oft zu kurz (oder der Kick zu schwach). Ein höherer Wert hilft häufig sofort.

---

### `g_stallMinCounts`

**Kurz:** Stall: Mindest-Counts im Timeout

**Default:** `static uint32_t g_stallMinCounts = 10;`

**Speicherung:** Nein (nur Firmware/INO). Falls das später per RS485 änderbar sein soll, kann dafür ein GET/SET‑Befehl ergänzt werden.

---

### `g_tempWarnAmbientC`

**Kurz:** Temp: Warnschwelle Umgebung (°C)

**Default:** `static float g_tempWarnAmbientC = 0.0f;`

**Speicherung:** Ja (Key `twa`). Ändern über RS485: **SETTEMPA/GETTEMPAW**.

**Praxis:** Wenn nur angezeigt werden soll und keine Warnung gebraucht wird, den Wert auf `0` oder kleiner setzen (Warnung aus).

---

### `g_tempWarnMotorC`

**Kurz:** Temp: Warnschwelle Motor (°C)

**Default:** `static float g_tempWarnMotorC = 0.0f;`

**Speicherung:** Ja (Key `twm`). Ändern über RS485: **SETTEMPM/GETTEMPMW**.

**Praxis:** Wenn nur angezeigt werden soll und keine Warnung gebraucht wird, den Wert auf `0` oder kleiner setzen (Warnung aus).

---

### `g_coldTempDegC`

**Kurz:** Kälte-Schwelle (°C)

**Default:** `static float g_coldTempDegC = 5.0f;`

**Speicherung:** Ja (Key `cth`). Ändern über RS485: **SETCOLDT/GETCOLDT**.

**Praxis:** Diese Werte gehören zur „Lastprofil‑Auswertung“ (Wind/Getriebe/Kälte). Details dazu sind im Tuning‑Kapitel beschrieben.

---

### `g_coldExtraDragPct`

**Kurz:** Extra-Reibung bei Kälte (%)

**Default:** `static float g_coldExtraDragPct = 10.0f;`

**Speicherung:** Ja (Key `cpx`). Ändern über RS485: **SETCOLDP/GETCOLDP**.

**Praxis:** Diese Werte gehören zur „Lastprofil‑Auswertung“ (Wind/Getriebe/Kälte). Details dazu sind im Tuning‑Kapitel beschrieben.

---

### `g_calIgnoreRampDeg`

**Kurz:** Kalibrierung: Rampenbereich ignorieren (°)

**Default:** `static float g_calIgnoreRampDeg = 10.0f;`

**Speicherung:** Ja (Key `cig`). Ändern über RS485: **SETCALIGNDG/GETCALIGNDG**.

**Praxis:** Diese Werte gehören zur „Lastprofil‑Auswertung“ (Wind/Getriebe/Kälte). Details dazu sind im Tuning‑Kapitel beschrieben.

---

### `g_statMinMoveDeg`

**Kurz:** Statistik: Mindestbewegung (°)

**Default:** `static float g_statMinMoveDeg = 60.0f;`

**Speicherung:** Ja (Key `smm`). Ändern über RS485: **SETSTATMINDG/GETSTATMINDG**.

**Praxis:** Diese Werte gehören zur „Lastprofil‑Auswertung“ (Wind/Getriebe/Kälte). Details dazu sind im Tuning‑Kapitel beschrieben.

---

### `g_dragWarnPct`

**Kurz:** Warnung: Reibung grösser (Mittelwert %)

**Default:** `static float g_dragWarnPct = 25.0f; // Mittelwert-Schwellwert (%)`

**Speicherung:** Ja (Key `drw`). Ändern über RS485: **SETDRAG/GETDRAG**.

**Praxis:** Diese Werte gehören zur „Lastprofil‑Auswertung“ (Wind/Getriebe/Kälte). Details dazu sind im Tuning‑Kapitel beschrieben.

---

### `g_dragWarnBinsPct`

**Kurz:** Warnung: Anteil Bins über Schwellwert (%)

**Default:** `static float g_dragWarnBinsPct = 30.0f; // wie viele Bins müssen drüber sein (%)`

**Speicherung:** Ja (Key `drb`). Ändern über RS485: **SETDRAGBINS/GETDRAGBINS**.

**Praxis:** Diese Werte gehören zur „Lastprofil‑Auswertung“ (Wind/Getriebe/Kälte). Details dazu sind im Tuning‑Kapitel beschrieben.

---

### `g_dragPersistMoves`

**Kurz:** Warnung: Persistenz (Fahrten)

**Default:** `static uint8_t g_dragPersistMoves = 3; // wie viele große Fahrten hintereinander`

**Speicherung:** Ja (Key `drn`). Ändern über RS485: **SETDRAGPERSIST/GETDRAGPERSIST**.

**Praxis:** Diese Werte gehören zur „Lastprofil‑Auswertung“ (Wind/Getriebe/Kälte). Details dazu sind im Tuning‑Kapitel beschrieben.

---

### `g_windPeakPct`

**Kurz:** Warnung: Windböe Peak (%)

**Default:** `static float g_windPeakPct = 60.0f; // Peak-Schwellwert (%)`

**Speicherung:** Ja (Key `wpk`). Ändern über RS485: **SETWINDPEAK/GETWINDPEAK**.

**Praxis:** Diese Werte gehören zur „Lastprofil‑Auswertung“ (Wind/Getriebe/Kälte). Details dazu sind im Tuning‑Kapitel beschrieben.

---

### `g_windCoherenceMin`

**Kurz:** Wind-Erkennung: Kohärenz Minimum (%)

**Default:** `static float g_windCoherenceMin = 55.0f; // Kohärenz-Minimum (%)`

**Speicherung:** Ja (Key `wco`). Ändern über RS485: **SETWINDCOH/GETWINDCOH**.

**Praxis:** Diese Werte gehören zur „Lastprofil‑Auswertung“ (Wind/Getriebe/Kälte). Details dazu sind im Tuning‑Kapitel beschrieben.

---

### `g_anemoOffsetKmh`

**Kurz:** Anemometer: Offset (km/h)

**Default:** `static float g_anemoOffsetKmh = 0.0f;`

**Speicherung:** Ja (Key `ano`). Ändern über RS485: **SETANEMOOF/GETANEMOOF**.

**Praxis:** GETANEMO liefert die gemessene Windgeschwindigkeit des RS485-Sensors. Der Offset ist nur für Sonderfälle gedacht; normalerweise sollte er auf `0,0` bleiben.

---

### `g_windDirOffsetDeg`

**Kurz:** RS485-Windsensor: Richtungs-Offset (Grad)

**Default:** `static float g_windDirOffsetDeg = 0.0f;`

**Speicherung:** Ja (Key `wdo`). Ändern über RS485: **SETWINDDIROF/GETWINDDIROF**.

**Praxis:** Sinnvoll bei verdrehter Montage des Sensors. Damit kann die angezeigte Windrichtung auf den mechanischen Bezug der Anlage korrigiert werden.

---

### `g_windEnable`

**Kurz:** RS485-Windsensor aktiv (1/0)

**Default:** `static bool g_windEnable = true;`

**Speicherung:** Ja (Key `wen`). Ändern über RS485: **SETWINDENABLE/GETWINDENABLE**.

**Praxis:** Bei `0` werden keine Winddaten gepollt; `GETANEMO` und `GETWINDDIR` liefern dann `0`. Für den normalen Betrieb sollte der Sensor aktiviert bleiben.

---

[↑ Inhaltsverzeichnis](#toc)

## 7. Kalibrierung/Lastprofil/Temperatur – Tuning‑Anleitung {#tuning}

### 6.1 Ziel der Kalibrierung

Bei der Kalibrierfahrt (`SETCAL`) wird ein „Normalprofil“ gelernt: Wie hoch ist die Stromaufnahme in jedem Winkelbereich, wenn **kein Wind** da ist und die Mechanik ok ist. Später werden Bewegungen damit verglichen.

### 6.2 Wie viele Datenpunkte?

Das Profil ist in **72 Winkel‑Bins** aufgeteilt. Ein Bin entspricht also **5°**. Das reicht gut, um Richtung und typische Lastbereiche zu erkennen, ohne zu viel Speicher zu brauchen.

### 6.3 Wichtige Einstellwerte

- `g_calIgnoreRampDeg`: Bereich am Anfang/Ende der Fahrt, der ignoriert wird. Empfehlung: 10° (Test), 30° (sehr robust).
- `g_statMinMoveDeg`: Nur große Fahrten zählen. Empfehlung: 30–90°.
- `g_dragWarnPct`: Ab wann „gleichmäßig schwerer“ (Getriebe/Kälte). Empfehlung: 20–30%.
- `g_dragWarnBinsPct`: Wie viel vom Kreis muss betroffen sein. Empfehlung: 30–70%.
- `g_dragPersistMoves`: Wie oft hintereinander, bevor gewarnt wird. Empfehlung: 3–5.
- `g_windPeakPct`: Ab wann „Böe“. Empfehlung: 25–60%.
- `g_windCoherenceMin`: Wie klar die Richtung sein muss. Empfehlung: 50–70%.
- `g_coldTempDegC` und `g_coldExtraDragPct`: Bei Kälte darf es schwerer sein, ohne gleich zu warnen. Empfehlung: 5°C und 10–25%.

### 6.4 Einfache Schritt‑für‑Schritt‑Methode

1. **Homing**: `SETREF:1` und warten bis referenziert.
2. **Kalibrieren** bei Windstille: `SETCAL`, dann `GETCALVALID` muss `1` sein.
3. **Statistik leeren**: `CLRSTAT` für die normale Live-Statistik, `SETACCBINSRST` für die schnelle ACC-Statistik.
4. **2–3 große Fahrten** fahren (z.B. 0→160→0→300). Danach `GETLOADSTAT` und `GETWIND`- **Windtag**: Das gleiche bei Wind wiederholen. Jetzt sollten `peak` und `coh`dirDeg- **Kaltes Wetter**: Unterhalb `g_coldTempDegC` testen. Wenn ohne Wind schon Warnungen kommen: `g_coldExtraDragPct` etwas hoch oder `g_dragWarnPct`

### 6.5 Fehlerbild → typische Anpassung

| Beobachtung | Typische Ursache | Was anpassen? |
| --- | --- | --- |
| Viele kurze Peaks, aber `coh` niedrig | Böe/zufällige Störung | `g_windPeakPct` höher oder `g_dragPersistMoves` |
| `mean` steigt langsam über Tage, viele Bins betroffen | Getriebe wird zäher / Schmierung / Temperatur | `g_dragWarnPct`g\_coldExtraDragPct |
| Warnung „leichter“ (DRAG\_DECREASE) | Last fehlt oder Profil passt nicht mehr | Mechanik prüfen, ggf. neu kalibrieren (`SETCAL`) |
| Bei **Windstille** trotzdem Wind‑Warnung | Schwellwerte zu niedrig oder Profil verrauscht | `g_windPeakPct`g\_windCoherenceMin |
| Bei Kälte dauernd Drag‑Warnung | Kälte sorgt für normale Mehrreibung | `g_coldExtraDragPct`g\_coldTempDegC |

[↑ Inhaltsverzeichnis](#toc)

## 8. Warnungen {#warns}

Warnungen stoppen den Motor **nicht**. Sie werden gesammelt (mehrere möglich) und müssen bewusst gelöscht werden.

**Abfragen:** `GETWARN` → Antwort `ACK_WARN` mit `0` oder `id;id;...`

**Löschen:** `DELWARN` (Antwort `ACK_DELWARN:1`)

| ID | Name | Bedeutung | Was tun? |
| --- | --- | --- | --- |
| 0 | `SW_NONE` | Keine Warnung | - |
| 1 | `SW_IS_SOFT` | Strom-Warnung (Soft-Limit erreicht) | Limits prüfen, ggf. IWARN höher oder Mechanik prüfen. |
| 2 | `SW_WIND_GUST` | Windböe / kurzfristig stark erhöhte Last | Bei Wind normal. Wenn dauernd: Schwellwerte oder Mechanik checken. |
| 3 | `SW_DRAG_INCREASE` | Gleichmäßig mehr Reibung | Kälte/Schmierstoff/Getriebe. coldExtraDragPct/dragWarnPct anpassen. |
| 4 | `SW_DRAG_DECREASE` | Gleichmäßig weniger Reibung | Kann auf „Last fehlt“ hinweisen (z.B. Antenne weg). |
| 5 | `SW_TEMP_AMBIENT_HIGH` | Umgebung zu warm | Warnschwelle setzen, ggf. Schutz/Belüftung. |
| 6 | `SW_TEMP_MOTOR_HIGH` | Motor zu warm | Motorsensor aktivieren, Warnschwelle setzen, Pausen/Last reduzieren. |

[↑ Inhaltsverzeichnis](#toc)

## 9. Fehler {#errors}

Fehler stoppen den Motor und bleiben aktiv („gelatched“), bis sie quittiert werden (siehe SETREF).

**Abfragen:** Primär asynchron über `ERR` vom Rotor (typisch an `DST=255`). `GETERR` bleibt als manuelle Service-Abfrage verfügbar.

**Quittieren:** `SETREF:0` oder `SETREF:1` (quittiert immer den Fehler; `SETREF:1` startet zusätzlich Homing)

| ID | Name | Ursache (typisch) | Was tun? | Wie löschen? |
| --- | --- | --- | --- | --- |
| 0 | `SE_NONE` | Kein Fehler | - | - |
| 10 | `SE_TIMEOUT` | Deadman/Keepalive Timeout | Master sendet zu lange keine Befehle während Bewegung | SETREF (quittiert) / Deadman anpassen |
| 11 | `SE_ENDSTOP` | Endschalter blockiert Fahrtrichtung | Es soll in Richtung eines aktiven Endschalters gefahren werden | Richtung/Endschalter/Offset prüfen, dann SETREF |
| 12 | `SE_NSTOP_CMD` | NSTOP (Not-Aus) per RS485 | Not-Stop Kommando empfangen | Ursache im Master, dann SETREF |
| 15 | `SE_IS_HARD` | Strom-Hardlimit | Zu hoher Motorstrom länger als Hold | Mechanik prüfen, IMAX/Grace/Hold prüfen, dann SETREF |
| 16 | `SE_STALL` | Stall: Encoder bewegt sich nicht | PWM an, aber zu wenige Encoder-Counts im Timeout | Mechanik/Haftreibung, MINPWM/KICK/STALLTIMEOUT anpassen, dann SETREF |
| 17 | `SE_HOME_FAIL` | Homing fehlgeschlagen | Timeout in Homing-Phase oder Endschalterproblem | Endschalter, Counts-Parameter, HOMETIMEOUT prüfen, dann SETREF |
| 18 | `SE_POS_TIMEOUT` | Positionsfahrt: Ziel nicht innerhalb der eingestellten Zeit erreicht | Die Fahrt zur Zielposition dauert länger als das konfigurierte Positions‑Timeout | `SETPOSTIMEOUT` hochsetzen, Mechanik prüfen (Haftreibung, Blockade, Antrieb), dann SETREF |

[↑ Inhaltsverzeichnis](#toc)

## 10. Hinweise {#notes}

### 10.1 OneWire / DS18B20 Compiler‑Warnungen

Wenn beim Bauen Warnungen aus `OneWire.cpp` erscheinen (z.B. „extra tokens at end of #undef“), kommen die aus der Library/ESP32‑Core Kombination. Das ist meist nur ein Warnhinweis und kein Laufzeitfehler. Wenn das stört, hilft in der Regel ein Update der OneWire‑Library oder des ESP32‑Cores.

### 10.2 RS485-Wind-/Richtungssensor

Der Windmesser wird in der aktuellen Firmware nicht mehr analog über eine Spannung eingelesen, sondern als **RS485-Wind-/Richtungssensor** über `Serial2` mit `9600 Baud` abgefragt. Dabei werden **Windgeschwindigkeit**, **Windrichtung** und die **Windstärke in Beaufort** zyklisch gelesen und im HalBoard zwischengespeichert.

Für den Master sind besonders diese Befehle relevant:

- `GETANEMO` = aktuelle Windgeschwindigkeit in km/h
- `GETBEAUFORT` = aktuelle Windstärke als Beaufortwert 0 bis 12
- `GETWINDDIR` = aktuelle Windrichtung in Grad
- `GETWINDENABLE`/`SETWINDENABLE` = Sensor aktivieren oder deaktivieren
- `GETANEMOOF`/`SETANEMOOF` = optionaler Geschwindigkeits-Offset
- `GETWINDDIROF`/`SETWINDDIROF` = optionaler Richtungs-Offset

Wichtig: `GETWIND` ist davon getrennt. Dieser Befehl liefert eine **Wind-Schätzung aus dem Motorlastprofil** und nicht die direkt gemessenen Werte des RS485-Sensors.

[↑ Inhaltsverzeichnis](#toc)