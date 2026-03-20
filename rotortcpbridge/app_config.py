import json
import os
from pathlib import Path
from typing import Any, Dict

APP_NAME = "RotorTcpBridge"

def appdata_dir()->Path:
    base = os.getenv("APPDATA") or str(Path.home() / ".config")
    p = Path(base) / APP_NAME
    p.mkdir(parents=True, exist_ok=True)
    return p

def config_path()->Path:
    return appdata_dir() / "config.json"

DEFAULT_CONFIG: Dict[str, Any] = {
  "pst_server": {"enabled":True,"listen_host":"127.0.0.1","listen_port_az":4001,"listen_port_el":4002},
  "hardware_link": {
      "mode":"tcp",
      "tcp_ip":"192.168.1.50",
      "tcp_port":8886,
      "com_port":"COM1",
      "baudrate":115200
  },
  "rotor_bus": {
      "master_id":0,
      "slave_az":20,
      "slave_el":21,
      "enable_az": True,
      "enable_el": False
  },
  "spid": {"ph":10,"pv":10},
  "polling_ms": {
      "pos_fast":100,
      "pos_slow":300,
      # User-Anforderung: diese Werte sollen laufend (~1s) aktualisiert werden
      "err":1000,
      "warn":1000,
      "telemetry":1000,
      "ref":300
  },
  "pwm": {"set_on_connect": False, "value_pct": 100.0},
  "behavior": {"auto_reference_on_connect": False},
  "ui": {
      # Frei belegbare Schnell-Buttons (werden in der GUI im Fenster "Befehle" verwaltet)
      # Format: Liste mit 15 Einträgen.
      # - None: Button ist leer
      # - Dict: {"dst": <int>, "cmd": <str>, "params": <str>}
      "quick_buttons": [None] * 15,
      # Anzeige der Windrichtung im Kompass:
      # - "from": woher der Wind kommt (meteorologisch)
      # - "to": wohin der Wind weht
      "wind_dir_display": "to",
      # Wenn True: Darkmode immer erzwingen (unabhängig von Windows).
      # Wenn False: System-/Windows-Theme verwenden.
      "force_dark_mode": False,
      # Wenn True: ACCBINS-Heatmap (Strom/Last) als 5px-Ring um den Kompass anzeigen.
      "compass_strom_az": False,
      "compass_strom_el": False,
      # Gewählte Antenne im Kompass (0/1/2 = Antenne 1/2/3), wird beim Start geladen.
      "compass_antenna": 0,
      # AZ-Antennenversätze (Fallback wenn Rotor noch nicht geantwortet hat): [Ant1, Ant2, Ant3] in Grad
      "antenna_offsets_az": [0.0, 0.0, 0.0],
      "antenna_angles_az": [0.0, 0.0, 0.0],
      "antenna_ranges_az": [100.0, 100.0, 100.0],
      # Lokal gespeicherte Antennen-Namen (3 Stück) für Einstellungen.
      "antenna_names": ["Antenne 1", "Antenne 2", "Antenne 3"],
      # Sprache der Benutzeroberfläche: "de" oder "en"
      "language": "de",
      # Standortkoordinaten (Breite/Länge in Grad dezimal)
      "location_lat": 49.502651,
      "location_lon": 8.375019,
      # Offline-Karte: True = lokale Tiles aus KartenLight-/KartenDark-Ordner verwenden
      "map_offline": False,
      # Amateurfunk-Locator: True = Maidenhead-Grid als Overlay einblenden
      "map_locator_overlay": False,
      # UDP UcxLog: Bei aktiviertem Häkchen lauscht die App auf 127.0.0.1:12040
      # und nimmt XML-Positionsdaten von UcxLog entgegen (<Rotor><Azimut>…</Azimut></Rotor>).
      "udp_ucxlog_enabled": False,
      "udp_ucxlog_port": 12040,
      # UDP PST-Rotator-Emulation: Emuliert das UDP-Protokoll von PstRotatorAz.
      # Hört auf udp_pst_port, sendet Positionsmeldungen an udp_pst_port + 1.
      "udp_pst_enabled": False,
      "udp_pst_port": 12000
  }
}

def _merge(dst:Dict[str,Any], src:Dict[str,Any]):
    for k,v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _merge(dst[k], v)
        else:
            dst[k] = v

def load_config()->Dict[str,Any]:
    p = config_path()
    if not p.exists():
        save_config(DEFAULT_CONFIG)
        return json.loads(json.dumps(DEFAULT_CONFIG))
    with open(p,"r",encoding="utf-8") as f:
        cfg = json.load(f)

    # Migration: alte Konfiguration hatte evtl. nur "listen_port"
    if "pst_server" in cfg and isinstance(cfg["pst_server"], dict):
        ps = cfg["pst_server"]
        if "listen_port" in ps and ("listen_port_az" not in ps or "listen_port_el" not in ps):
            try:
                base_port = int(ps.get("listen_port"))
            except Exception:
                base_port = 4001
            ps.setdefault("listen_port_az", base_port)
            ps.setdefault("listen_port_el", base_port + 1)
            ps.pop("listen_port", None)

    # Migration: enable flags defaults
    if "rotor_bus" in cfg and isinstance(cfg["rotor_bus"], dict):
        rb = cfg["rotor_bus"]
        rb.setdefault("enable_az", True)
        rb.setdefault("enable_el", True)

    # Merge defaults (für neue Felder bei Updates)
    merged = json.loads(json.dumps(DEFAULT_CONFIG))
    _merge(merged, cfg)
    return merged

def save_config(cfg:Dict[str,Any]):
    p = config_path()
    with open(p,"w",encoding="utf-8") as f:
        json.dump(cfg,f,indent=2,ensure_ascii=False)
