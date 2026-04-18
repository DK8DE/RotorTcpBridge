# FLRig-Kompatibilität (RotorTcpBridge Rig-Bridge)

Die Rig-Bridge spricht mit Programmen wie **WSJT-X**, **fldigi** oder **UcxLog** über dieselbe **XML-RPC-Schnittstelle** wie [FLRig](https://www.w1hkj.org/). Implementiert ist ein **Teilmenge** der Methoden; nicht unterstützte Aufrufe liefern eine XML-RPC-**Fault** mit `unknown method …` (Hamlib/flrig-Clients erkennen das typischerweise als nicht verfügbar).

**Quellcode:** `rotortcpbridge/rig_bridge/protocol_flrig.py` (`FlrigBridgeServer._dispatch_xmlrpc`, Textmodus `_handle_cmd`).

---

## Offizielle Referenz (echtes FLRig)

- **FLRig XML-RPC / Server:** [Configure XmlRpc Server (flrig-help)](https://www.w1hkj.org/flrig-help/xmlrpc_server.html)  
- **Fldigi XML-RPC** (ähnliches Schema): [Xmlrpc Control – Fldigi](https://www.w1hkj.org/FldigiHelp/xmlrpc_control_page.html)  
- In **FLRig** oft: **Hilfe → XML-Help** (lokale Methodenliste).

Signaturen und Namen dort sind die **Referenz** für Clients. RotorTcpBridge muss nicht jede Methode implementieren; die Tabelle unten beschreibt den **aktuellen Stand** in diesem Projekt.

---

## XML-RPC: unterstützte Methoden

### Meta / Lesen (ohne CAT oder mit Stub)

| Methode | Antwort / Verhalten |
|--------|----------------------|
| `main.get_version` | Fester Versions-String (Kompatibilität mit flrig_open). |
| `rig.get_xcvr` | Kennung `RotorTcpBridge`. |
| `rig.get_pwrmeter_scale` | `"100"`. |
| `rig.get_mode`, `rig.get_modeA`, `rig.get_modeB` | Modus aus **internem State** (letzter `rig.set_mode*` / Startwert). |
| `rig.get_vfo`, `rig.get_vfoA`, `rig.get_vfoB`, `main.get_frequency`, `main.get_freq` | Vor der Antwort optional **CAT READFREQ** (`FA;` o. ä.), dann Frequenz in **Hz als String** (FLRig-konform). |
| `rig.get_AB` | VFO `A`/`B` aus State. |
| `rig.get_modes` | Pipe-getrennte Modusliste wie FLRig (siehe Konstante `_FLRIG_MODES_PIPE` im Code). |
| `rig.get_bw`, `rig.get_bwA`, `rig.get_bwB`, `rig.get_bws` | Platzhalter `"3000"`. |
| `rig.get_split` | Immer `0` (kein Split). |
| `rig.get_ptt` | Aus State. |
| `rig.get_DBM`, `rig.get_smeter`, `rig.get_swrmeter`, `rig.get_SWR`, `rig.get_Sunits`, `rig.get_pwrmeter` | Stub `"0"`. |
| `rig.get_volume`, `rig.get_rfgain`, `rig.get_micgain`, `rig.get_power`, `rig.get_agc` | Stub `0`. |

### Schreiben (State + ggf. CAT)

| Methode | State | CAT / Queue |
|--------|--------|----------------|
| `rig.set_vfo`, `rig.set_vfoA`, `rig.set_vfoB`, `rig.set_verify_vfoA`, `rig.set_verify_vfoB`, `rig.set_vfoA_fast`, `rig.set_vfoB_fast`, `main.set_frequency`, `rig.set_frequency` | `frequency_hz` | `SETFREQ` → serielles CAT (Marke/Modell siehe `cat_commands.py`). |
| `rig.set_mode`, `rig.set_modeA`, `rig.set_modeB`, `rig.set_verify_mode`, `rig.set_verify_modeA`, `rig.set_verify_modeB` | `mode` | `SETMODE` → CAT (Icom CI-V: aktuell nur State, kein Modus-CAT). |
| `rig.set_ptt`, `rig.set_ptt_fast`, `rig.set_verify_ptt` | `ptt` | `SETPTT` → CAT. |
| `rig.set_AB`, `rig.set_verify_AB` | `vfo` | Kein CAT. |
| `rig.set_split`, `rig.set_verify_split` | — | No-op, erfolgreiche Antwort. |
| `rig.set_bw*`, `rig.set_bandwidth` | — | No-op, erfolgreiche Antwort. |
| `rig.shutdown`, `rig.tune` | — | No-op, erfolgreiche Antwort. |
| `rig.cat_string` | — | Leerer String. |

### Nicht unterstützt

Alle anderen `main.*` / `rig.*` Methoden → **XML-RPC Fault** `unknown method <name>`.

---

## Textzeilen-Protokoll (Legacy)

Wenn die erste Zeile **nicht** wie HTTP aussieht, wird ein einfacher **zeilenweiser** Modus verwendet (`_handle_cmd`):

| Befehl | Verhalten |
|--------|-------------|
| `GET FREQ` | optional READFREQ, dann Hz als Text |
| `SET FREQ <hz>` | State + `SETFREQ` |
| `GET MODE` | Modus aus State |
| `SET MODE <name>` | State + `SETMODE` |
| `GET PTT` / `SET PTT <0\|1>` | State + `SETPTT` |
| `GET VFO` | VFO aus State |
| sonst | `ERR` |

---

## XML-RPC-Parameter (Parser)

Clients unterscheiden sich bei der Kodierung:

- **Namespaces** auf `methodCall` / Kind-Elementen  
- **`<value>FM</value>`** ohne `<string>` (z. B. Indy / Apache XML-RPC)  
- **`<value><string>FM</string></value>`**  

Der Parser in `protocol_flrig.py` (`_param_scalar_values`, Fallbacks `_body_first_frequency_hz`, `_body_mode_name_from_set_mode_xml`) soll diese Fälle abdecken. Tritt dennoch ein Problem auf, **Rig-Befehle loggen** aktivieren und den rohen Request prüfen.

---

## Grenzen / Erwartungen

- **Stub-Getter** (Pegel, Bandbreite, …) verhindern Client-Hänger; sie ersetzen **kein** echtes S-Meter-/Audio-CAT.
- **Frequenz/Modus/PTT** sind die Hauptpfade zum Funkgerät; tatsächliche CAT-Befehle hängen von **Marke/Modell** und `cat_commands.py` ab.
- Parallele Clients (FLRig + Hamlib) teilen sich **eine** COM-Session; Last und Reihenfolge siehe Entwicklerkommentare in `radio_backend.py` / `manager.py`.

---

## Änderungen an der Kompatibilität

Neue FLRig-Methoden: in `_dispatch_xmlrpc` ergänzen und in dieser Datei dokumentieren. Nach Möglichkeit mit Referenz zur W1HKJ-Doku und einem kurzen Satz zum CAT-Verhalten.
