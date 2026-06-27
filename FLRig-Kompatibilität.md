# FLRig-Kompatibilität (RotorTcpBridge Rig-Bridge)

Die Rig-Bridge spricht mit Programmen wie **WSJT-X**, **fldigi**, **UcxLog** oder **QLog** über dieselbe **XML-RPC-Schnittstelle** wie [FLRig](https://www.w1hkj.org/). Ziel ist **breite Kompatibilität** mit der Methodenliste aus der [FLRig XML-RPC-Hilfe](https://www.w1hkj.org/flrig-help/xmlrpc_server.html): alle dort genannten üblichen `rig.*`- und `main.*`-Aufrufe werden **beantwortet** (kein `unknown method` für die dokumentierten Namen).

**Quellcode:** `rotortcpbridge/rig_bridge/protocol_flrig.py` (`FlrigBridgeServer._dispatch_xmlrpc`, Textmodus `_handle_cmd`).

---

## Offizielle Referenz (echtes FLRig)

- **FLRig XML-RPC / Server:** [Configure XmlRpc Server (flrig-help)](https://www.w1hkj.org/flrig-help/xmlrpc_server.html)  
- **Fldigi XML-RPC** (ähnliches Schema): [Xmlrpc Control – Fldigi](https://www.w1hkj.org/FldigiHelp/xmlrpc_control_page.html)  
- In **FLRig** oft: **Hilfe → XML-Help** (lokale Methodenliste).

Signaturen und Namen dort sind die **Referenz** für Clients.

---

## Verhalten: CAT vs. State vs. No-Op

| Kategorie | Beispiele | Hinweis |
|-----------|-----------|---------|
| **Echtes CAT** (Frequenz/Modus/PTT) | `rig.set_vfo*`, `main.set_frequency`, `rig.set_frequency`, `rig.set_verify_frequency`, `rig.set_mode*`, `rig.set_ptt*`, `rig.mod_vfoA/B` | Befehl an die CAT-Queue (`SETFREQ`, `SETMODE`, `SETPTT`); State wird optimistisch vorbelegt. |
| **Echtes CAT** (Pegel / Betriebsart) | `rig.set_power*`, `rig.set_volume*`, `rig.set_rfgain*`, `rig.set_micgain*`, `rig.mod_pwr/vol/rfg` | `PC`, `AG0`, `RG0`, `MG` jeweils mit 3-stelligem Wert; Wert landet im State-Cache. |
| **Echtes CAT** (VFO / Split) | `rig.set_AB*`, `rig.set_split*`, `rig.swap`, `rig.vfoA2B`, `rig.freqA2B` | `VS0/VS1`, `SP0/SP1`, `SV`, `AB`; State wird mitgeführt. |
| **Async CAT-Lesebefehl** (Meter) | `rig.get_smeter`, `rig.get_DBM`, `rig.get_Sunits`, `rig.get_pwrmeter` | Fordert `SM0;` bzw. `RM1;` am TRX an (fire-and-forget); Antwort landet im State und wird beim nächsten Poll zurückgeliefert. |
| **State-Getter** (kein CAT-Lesebefehl) | `rig.get_power`, `rig.get_volume`, `rig.get_rfgain`, `rig.get_micgain` | Liefert den zuletzt per SET gesetzten Wert aus dem Cache. |
| **No-Op (void, erfolgreich)** | Bandbreite (`rig.set_bw*`), PBT (`rig.set_pbt*`), Notch (`rig.set_notch`), `rig.mod_bw`, `rig.modeA2B`, `rig.tune`, `rig.cmd`, `rig.shutdown`, **CWIO/FSKIO** | Verhindert Client-Fehler; keine Geräteaktion (zu gerätespezifisch oder nicht anwendbar). |
| **Getter-Stub** | `rig.get_info`, `rig.get_sideband`, `rig.get_notch`, `rig.get_pwrmax`, `rig.get_update`, `rig.get_pbt`, `rig.get_pbt_inner/outer`, `rig.get_agc`, `rig.get_bw*` | Sinnvolle Platzhalter; kein echtes CAT-Lesen. |
| **`rig.cat_string` / `rig.cat_priority`** | Leerer String | Kein Roh-CAT-Passthrough. |

### Nicht unterstützt (Fault)

Methoden außerhalb `rig.*` / `main.*` oder **neue/unbekannte** Namen → **XML-RPC Fault** `unknown method <name>`.

---

## CAT-Befehlsübersicht (alle SET-Methoden)

### Frequenz / Modus / PTT

| FLRig-Methode | CAT-Befehl | State |
|---|---|---|
| `rig.set_vfoA/B/vfo`, `main.set_frequency`, `rig.set_frequency`, `rig.set_verify_frequency` | `FA<hz>;` (Yaesu/Kenwood) | `frequency_hz` |
| `rig.mod_vfoA`, `rig.mod_vfoB` | `FA<hz+delta>;` | `frequency_hz` |
| `rig.set_mode*`, `rig.set_verify_mode*` | `MD0<n>;` (Yaesu) / `MD<n>;` (Kenwood) | `mode` |
| `rig.set_ptt`, `rig.set_ptt_fast`, `rig.set_verify_ptt` | `TX1;`/`TX0;` (Yaesu) / `TX;`/`RX;` (Kenwood) | `ptt` |

### TX-Leistung / Pegel

| FLRig-Methode | CAT-Befehl | Wertebereich | State |
|---|---|---|---|
| `rig.set_power`, `rig.set_verify_power` | `PC<nnn>;` | 0–100 | `power` |
| `rig.mod_pwr` | `PC<aktuell+delta>;` | 0–100 | `power` |
| `rig.set_volume`, `rig.set_verify_volume` | `AG0<nnn>;` | 0–255 | `volume` |
| `rig.mod_vol` | `AG0<aktuell+delta>;` | 0–255 | `volume` |
| `rig.set_rfgain`, `rig.set_verify_rfgain` | `RG0<nnn>;` | 0–255 | `rfgain` |
| `rig.mod_rfg` | `RG0<aktuell+delta>;` | 0–255 | `rfgain` |
| `rig.set_micgain`, `rig.set_verify_micgain` | `MG<nnn>;` | 0–100 | `micgain` |

### VFO / Split

| FLRig-Methode | CAT-Befehl Yaesu | CAT-Befehl Kenwood/Elecraft | State |
|---|---|---|---|
| `rig.set_AB`, `rig.set_verify_AB` | `VS0;`/`VS1;` | `VFA;`/`VFB;` | `vfo` |
| `rig.set_split`, `rig.set_verify_split` | `SP0;`/`SP1;` | `SP0;`/`SP1;` | `split` |
| `rig.swap` | `SV;` | `SV;` | `vfo` (gespiegelt) |
| `rig.vfoA2B`, `rig.freqA2B` | `AB;` | `AB;` | — |

### Meter-Abfragen (async, read-only)

| FLRig-Methode | CAT-Abfrage | Antwort-Prefix | Skalierung | State |
|---|---|---|---|---|
| `rig.get_smeter`, `rig.get_DBM`, `rig.get_Sunits`, `rig.get_swrmeter`, `rig.get_SWR` | `SM0;` | `SM0` | × 8,5 → 0–255 (FLRig-Skala) | `smeter` |
| `rig.get_pwrmeter` | `RM1;` | `RM1` | 0–100 | `pwrmeter` |

---

## XML-RPC: Getter-Übersicht

| FLRig-Methode | Rückgabetyp | Quelle |
|---|---|---|
| `rig.get_vfoA/B/vfo`, `main.get_frequency/freq` | `s:n` (Hz als String) | State `frequency_hz` (async READFREQ) |
| `rig.get_modeA/B`, `rig.get_mode` | `s:n` | State `mode` |
| `rig.get_AB` | `s:n` (`"A"` / `"B"`) | State `vfo` |
| `rig.get_split` | `i:n` (0/1) | State `split` |
| `rig.get_ptt` | `i:n` (0/1) | State `ptt` |
| `rig.get_power` | `i:n` (0–100) | State `power` |
| `rig.get_volume` | `i:n` (0–255) | State `volume` |
| `rig.get_rfgain` | `i:n` (0–255) | State `rfgain` |
| `rig.get_micgain` | `i:n` (0–100) | State `micgain` |
| `rig.get_smeter` / `rig.get_DBM` / `rig.get_Sunits` | `s:n` (0–255) | State `smeter` (async SM0;) |
| `rig.get_pwrmeter` | `s:n` (0–100) | State `pwrmeter` (async RM1;) |
| `rig.get_modes` | `A:n` (Array) | Fest: alle üblichen Modi |
| `rig.get_bwA/B/bw` | `A:n` (Array) | Stub `["3000"]` |
| `rig.get_bws` | `A:n` (Array) | Feste Tabelle 1800–4200 Hz |
| `rig.get_xcvr` | `s:n` | `"RotorTcpBridge"` |
| `rig.get_pwrmeter_scale` | `s:n` | `"100"` |
| `rig.get_pwrmax` | `s:n` | `"100"` |
| `rig.get_notch` | `i:n` | `0` |
| `rig.get_pbt` | `A:n` | `[0, 0]` |
| `rig.get_pbt_inner/outer` | `i:n` | `0` |
| `rig.get_agc` | `i:n` | `0` |
| `rig.get_sideband` | `s:n` | `"U"` |
| `rig.get_info` | `s:n` | `""` |
| `rig.get_update` | `s:n` | `""` |
| `rig.cwio_get_wpm`, `rig.fskio_get_wpm` | `i:n` | `20` |
| `main.get_version` | `s:n` | `"1.4.2"` |

---

## Textzeilen-Protokoll (Legacy)

Unverändert: `GET FREQ`, `SET FREQ <hz>`, `GET MODE`, `SET MODE <name>`, `GET PTT`, `SET PTT <0/1>`, `GET VFO`; sonst `ERR`.

---

## Einschränkungen

- **Icom CI-V**: Neue Pegel- und VFO-Befehle werden nur im State-Cache gespeichert — CI-V-Binärkodierung ist für diese Parameter nicht implementiert.
- **CWIO/FSKIO** antworten erfolgreich, **steuern aber kein Morse** über die Bridge.
- **Bandbreite, PBT, Notch, AGC** sind No-Op; die gerätespezifische Kodierung variiert zu stark.
- **Parallele Clients** teilen sich **eine** COM-Session; Reihenfolge und Priorisierung siehe `radio_backend.py`.

---

## Änderungen an der Kompatibilität

Neue FLRig-Methoden: in `_dispatch_xmlrpc` ergänzen und diese Datei aktualisieren.
