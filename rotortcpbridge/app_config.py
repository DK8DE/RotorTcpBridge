import json
import os
from pathlib import Path
from typing import Any, Dict

from .net_utils import ipv4_subnet_broadcast_default

APP_NAME = "RotorTcpBridge"


def appdata_dir() -> Path:
    base = os.getenv("APPDATA") or str(Path.home() / ".config")
    p = Path(base) / APP_NAME
    p.mkdir(parents=True, exist_ok=True)
    return p


def config_path() -> Path:
    return appdata_dir() / "config.json"


DEFAULT_CONFIG: Dict[str, Any] = {
    "pst_server": {
        # SPID BIG-RAS (TCP): Standard aus; UDP-PST-Emulator ist separat (ui.udp_pst_enabled).
        "enabled": False,
        "listen_host": "127.0.0.1",
        "listen_port_az": 4001,
        "listen_port_el": 4002,
    },
    "hardware_link": {
        "mode": "com",
        "tcp_ip": "192.168.1.50",
        "tcp_port": 8886,
        "com_port": "COM1",
        "baudrate": 115200,
    },
    "rotor_bus": {
        "master_id": 0,
        "slave_az": 20,
        "slave_el": 21,
        "enable_az": True,
        "enable_el": False,
    },
    "spid": {"ph": 10, "pv": 10},
    "polling_ms": {
        "pos_fast": 100,
        "pos_slow": 300,
        # User-Anforderung: diese Werte sollen laufend (~1s) aktualisiert werden
        "err": 1000,
        "warn": 1000,
        "telemetry": 1000,
        "ref": 300,
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
        "force_dark_mode": True,
        # Wenn True: ACCBINS-Heatmap (Strom/Last) als 5px-Ring um den Kompass anzeigen.
        "compass_strom_az": False,
        "compass_strom_el": False,
        # Kompass-Ring: "off" | "strom" | "om_radar" (AZ); EL nur "off" | "strom".
        "compass_heatmap_az": "off",
        "compass_heatmap_el": "off",
        # OM-Radar: Anzahl Richtungs-Sektoren (10 = grob, 100 = fein; Standard 60).
        "compass_om_radar_sectors": 60,
        # Standzeit-Ring (AZ): Sektoren 10–100; volle Skala (rot) nach X Minuten Gesamtstillstand im Sektor.
        "compass_dwell_sectors": 60,
        "compass_dwell_full_minutes": 5.0,
        # AZ-Kompass: bis zu zwei Ringe gleichzeitig: "strom" / "om_radar" / "dwell"
        "compass_heatmap_az_modes": [],
        # Last-Heatmap: optional feste Skala (Kompass + Statistik-Fenster, Langzeit=Aktuell gleich).
        # thr_blue ≤ norm_min ≤ norm_max ≤ thr_red (mV); nur wenn heatmap_custom_* True.
        "heatmap_custom_az": False,
        "heatmap_thr_blue_az": 0,
        "heatmap_norm_min_az": 0,
        "heatmap_norm_max_az": 0,
        "heatmap_thr_red_az": 0,
        "heatmap_custom_el": False,
        "heatmap_thr_blue_el": 0,
        "heatmap_norm_min_el": 0,
        "heatmap_norm_max_el": 0,
        "heatmap_thr_red_el": 0,
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
        "location_locator": "",
        # Offline-Karte: True = lokale Tiles aus KartenLight-/KartenDark-Ordner verwenden
        "map_offline": False,
        # Amateurfunk-Locator: True = Maidenhead-Grid als Overlay einblenden
        "map_locator_overlay": False,
        # UDP UcxLog: Lauscht auf udp_ucxlog_listen_host:udp_ucxlog_port (Standard 0.0.0.0:12040).
        # XML von UcxLog (<Rotor><Azimut>…</Azimut></Rotor>).
        "udp_ucxlog_enabled": True,
        "udp_ucxlog_port": 12040,
        "udp_ucxlog_listen_host": "0.0.0.0",
        # UDP PST-Rotator-Emulation: Emuliert das UDP-Protokoll von PstRotatorAz.
        # Hört auf udp_pst_port, sendet Positionsmeldungen an udp_pst_port + 1.
        # Ziel für AZ:/TGA:-Antworten. Leer = automatisch Subnetz-Broadcast (x.y.z.255);
        # 127.0.0.1 = nur dieser PC; 255.255.255.255 = globaler Broadcast; sonst konkrete IPv4.
        "udp_pst_enabled": True,
        "udp_pst_port": 12000,
        "udp_pst_listen_host": "0.0.0.0",
        # Neuinstallation: in load_config beim ersten Speichern als Subnetz-Broadcast (x.y.z.255) gesetzt.
        # Leer = zur Laufzeit automatisch ipv4_subnet_broadcast_default()
        "udp_pst_send_host": "",
        # AirScout/KST: ASWATCHLIST/ASSETPATH auf aswatch_udp_listen_host:aswatch_udp_port
        "aswatch_udp_enabled": True,
        "aswatch_udp_port": 9872,
        "aswatch_udp_listen_host": "0.0.0.0",
        # ASNEAREST (Flugzeuge): wenn False, keine Verarbeitung/Anzeige (Karte ohne Flugzeuge/Linien)
        "aswatch_aircraft_enabled": True,
        # ASNEAREST (Flugzeuge): Rohdaten nach %APPDATA%/RotorTcpBridge/asnearest.jsonl
        "asnearest_jsonl_log": True,
        # Linie Flugzeug → Gegenstation wenn Potenzial ≥ und Restzeit ≤ (Minuten)
        "asnearest_line_potential_min": 50,
        "asnearest_line_duration_max_min": 120,
        # ASNEAREST: kombinierter Score 0–100 (Potenzial + Zeit, vgl. KST); Anzeige nur ab diesem Wert
        "asnearest_min_score": 45,
        # Karten-Infotabelle ASNEAREST: nur Einträge mit Restzeit (min) ≤ diesem Wert; 0 = kein Limit
        "asnearest_list_max_minutes": 0,
        # Max. Zeilen in der ASNEAREST-Tabelle auf der Karte
        "asnearest_list_max_rows": 20,
        # Mitte-Faktor g = 4*t*(1-t); Linie nur wenn g ≥ (0 = aus, typ. 0.12–0.25)
        "asnearest_geom_factor_min": 0.20,
        # Keine Höhe in UDP: H/M/S-Faktor stärker bei langer QTH↔DX-Strecke (siehe udp_aswatchlist)
        "asnearest_use_category_path": True,
        # Gleiches Flugzeug pro DX beibehalten (sonst springt Restzeit beim Wechsel des „Besten“)
        "asnearest_sticky_flight": True,
    },
}


def _merge(dst: Dict[str, Any], src: Dict[str, Any]):
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _merge(dst[k], v)
        else:
            dst[k] = v


def load_config() -> Dict[str, Any]:
    p = config_path()
    if not p.exists():
        initial = json.loads(json.dumps(DEFAULT_CONFIG))
        ui = initial.setdefault("ui", {})
        # Erste Installation: alle UDP-Listen-IPs 0.0.0.0 (DEFAULT), PST-Ziel = Subnetz-Broadcast (…255)
        ui["udp_ucxlog_listen_host"] = "0.0.0.0"
        ui["udp_pst_listen_host"] = "0.0.0.0"
        ui["aswatch_udp_listen_host"] = "0.0.0.0"
        ui["udp_pst_send_host"] = ipv4_subnet_broadcast_default()
        save_config(initial)
        return json.loads(json.dumps(initial))
    with open(p, "r", encoding="utf-8") as f:
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


def save_config(cfg: Dict[str, Any]):
    p = config_path()
    with open(p, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
