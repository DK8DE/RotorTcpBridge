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

## Verhalten: CAT vs. Stub vs. No-Op

| Kategorie | Beispiele | Hinweis |
|-----------|-----------|---------|
| **Echtes CAT** (Queue zum Funkgerät) | `rig.set_vfo*`, `main.set_frequency`, `rig.set_frequency`, `rig.set_verify_frequency`, `rig.set_mode*`, `rig.set_ptt*`, `rig.mod_vfoA` / `rig.mod_vfoB` | Frequenz/Modus/PTT und VFO-Relativsprünge wie in FLRig üblich an die Bridge-Queue (`SETFREQ`, `SETMODE`, `SETPTT`). |
| **State ohne CAT** | `rig.set_AB`, `rig.set_split` | Nur interner Cache (`vfo`, `split`). |
| **No-Op, erfolgreich (void)** | Bandbreite, PBT, Notch, Leistung, Volume/RF/Mic, `rig.mod_vol` / `mod_pwr` / `mod_rfg` / `mod_bw`, `rig.swap`, `rig.vfoA2B`, `rig.freqA2B`, `rig.modeA2B`, `rig.tune`, `rig.cmd`, `rig.shutdown`, **CWIO/FSKIO** (`rig.cwio_send`, `rig.cwio_set_wpm`, `rig.cwio_text`, `rig.mod_cwio_wpm`, `rig.fskio_text`) | Verhindert Client-Fehler; **kein** paralleles CWIO-Keying über die Bridge (QLog bricht sonst mit `unknown method rig.cwio_send` ab). |
| **Getter-Stub** | `rig.get_info`, `rig.get_sideband`, `rig.get_notch`, `rig.get_pwrmax`, `rig.get_update`, `rig.get_pbt` (Array), `rig.get_pbt_inner` / `outer`, S-Meter, `rig.get_bw*` usw. | Sinnvolle Platzhalter; **kein** echtes S-Meter-/PBT-CAT, sofern nicht später ergänzt. |
| **`rig.cat_string` / `rig.cat_priority`** | Leerer String | Kein Roh-CAT-Passthrough. |

### Nicht unterstützt (Fault)

Methoden außerhalb `rig.*` / `main.*` oder **neue/unbekannte** Namen, die nicht in der FLRig-Doku stehen → **XML-RPC Fault** `unknown method <name>`.

---

## XML-RPC: zentrale Methoden (Auszug)

### Lesen (mit optional READFREQ vor Abfrage)

`rig.get_vfoA`, `rig.get_vfoB`, `rig.get_vfo`, `main.get_frequency`, `main.get_freq` — wie zuvor optional **CAT READFREQ**, Antwort **Hz als String** (FLRig-konform).

Weitere Getter siehe Code-Block „S-Meter / Pegel“ und „Zusätzliche Getter“ in `protocol_flrig.py`.

### Schreiben (State + ggf. CAT)

| Methode | State | CAT / Queue |
|--------|--------|-------------|
| `rig.set_vfo*`, `main.set_frequency`, `rig.set_frequency`, **`rig.set_verify_frequency`** | `frequency_hz` | `SETFREQ` |
| **`rig.mod_vfoA`**, **`rig.mod_vfoB`** | Relativ zu aktueller Anzeigefrequenz | `SETFREQ` |
| `rig.set_mode*` | `mode` | `SETMODE` |
| `rig.set_ptt*` | `ptt` | `SETPTT` |
| `rig.set_split*` | `split` | — |
| `rig.set_AB*` | `vfo` | — |

---

## Textzeilen-Protokoll (Legacy)

Unverändert: `GET FREQ`, `SET FREQ`, `GET MODE`, `SET MODE`, `GET PTT`, `SET PTT`, `GET VFO`, sonst `ERR`.

---

## Grenzen / Erwartungen

- **CWIO/FSKIO** antworten erfolgreich, **steuern aber kein Morse** über die Bridge — für echtes CW-Keying weiterhin Funkgerät/FLRig/CAT-Keyer nutzen.
- **Stub-Getter** ersetzen kein echtes Mess-CAT.
- Parallele Clients teilen sich **eine** COM-Session; Last und Reihenfolge siehe `radio_backend.py` / `manager.py`.

---

## Änderungen an der Kompatibilität

Neue FLRig-Methoden: in `_dispatch_xmlrpc` ergänzen und diese Datei anpassen.
