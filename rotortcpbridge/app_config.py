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
    # SPID BIG-RAS / CAT über serielle Schnittstelle (z. B. com0com). Jeder
    # Listener bedient entweder den Rotor (ROT2PROG-13-Byte-Frames, AZ+EL in
    # einem Frame) oder ein Funkgeraet (CAT-Protokoll des aktiven Rig-Profils).
    # Das Ziel wird ueber ``target`` festgelegt:
    #   * ``"rotor"``             → SPID-BIG-RAS-Listener
    #   * ``"rig:<rig_id>"``     → CAT-Listener, simuliert das Profil ``rig_id``
    "pst_serial": {
        "enabled": False,
        # Einträge: {"port": "COM21", "baudrate": 115200, "enabled": true,
        #            "target": "rotor" | "rig:<id>"}
        "listeners": [],
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
        # SETPOSCC vom Bus: Master-IDs ignorieren (z. B. [2] wenn Stör-Telegramme den Soll verfälschen)
        "setposcc_ignore_src_master_ids": [],
    },
    # Hardware-Controller (RS485): Tab „Controller“ — Standard aktiv, Bus-ID 2
    "controller_hw": {
        "enabled": True,
        "cont_id": 2,
    },
    "spid": {"ph": 10, "pv": 10},
    "polling_ms": {
        "pos_fast": 200,
        "pos_slow": 300,
        # User-Anforderung: diese Werte sollen laufend (~1s) aktualisiert werden
        "err": 1000,
        "warn": 1000,
        "telemetry": 1000,
        "ref": 300,
    },
    "pwm": {"set_on_connect": False, "value_pct": 100.0},
    "behavior": {"auto_reference_on_connect": False},
    # Rig-Bridge: mehrere Profile, eines ist aktiv. Das aktive Profil steuert
    # die echte CAT-Verbindung und ist Ziel der Rig-CAT-Listener auf der
    # virtuellen seriellen Schnittstelle (com0com).
    "rig_bridge": {
        "enabled": False,
        "active_rig_id": "default",
        # Flrig/Hamlib-NET sind TCP-Protokolle, die *eine* aktive Funkgeraete-
        # verbindung nach aussen bereitstellen. Sie gelten deshalb global und
        # nicht pro Rig-Profil — beim Profilwechsel wechselt nur das dahinter
        # angesprochene Funkgeraet; Host/Port/Autostart bleiben gleich.
        "flrig": {
            "enabled": False,
            "host": "127.0.0.1",
            "port": 12345,
            "autostart": False,
        },
        "hamlib": {
            "enabled": False,
            "host": "127.0.0.1",
            "listeners": [{"port": 4532, "name": ""}],
            "autostart": False,
            "debug_traffic": False,
        },
        "rigs": [
            {
                "id": "default",
                "name": "Rig 1",
                "enabled": True,
                "selected_rig": "Generic CAT",
                "rig_brand": "Generisch",
                "rig_model": "CAT (generisch)",
                "hamlib_rig_id": 0,
                "com_port": "COM1",
                "baudrate": 9600,
                "databits": 8,
                "stopbits": 1,
                "parity": "N",
                "timeout_s": 0.2,
                "polling_interval_ms": 30,
                "auto_connect": False,
                "auto_reconnect": True,
                "log_serial_traffic": True,
                "cat_post_write_drain_ms": 50,
                "setfreq_gap_ms": 10,
            }
        ],
    },
    "ui": {
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
        # UDP UcxLog: Lauscht auf udp_ucxlog_listen_host:udp_ucxlog_port (Standard aus, 127.0.0.1:12040).
        # XML von UcxLog (<Rotor><Azimut>…</Azimut></Rotor>).
        "udp_ucxlog_enabled": False,
        "udp_ucxlog_port": 12040,
        "udp_ucxlog_listen_host": "127.0.0.1",
        # UDP PST-Rotator-Emulation: Emuliert das UDP-Protokoll von PstRotatorAz.
        # Hört auf udp_pst_port, sendet Positionsmeldungen an udp_pst_port + 1.
        # Ziel für AZ:/TGA:-Antworten. Leer = automatisch Subnetz-Broadcast (x.y.z.255);
        # 127.0.0.1 = nur dieser PC; 255.255.255.255 = globaler Broadcast; sonst konkrete IPv4.
        "udp_pst_enabled": True,
        "udp_pst_port": 12000,
        # Standardmaessig nur Loopback: Windows filtert Pakete per bind() schon nach
        # Ziel-IP; so koennen parallele Rotor-Setups im LAN uns nicht versehentlich
        # ansteuern. Wer von einem anderen Rechner im Netz steuern will, traegt hier
        # die eigene LAN-IP ein (nicht 0.0.0.0).
        "udp_pst_listen_host": "127.0.0.1",
        # Neuinstallation: in load_config beim ersten Speichern als Subnetz-Broadcast (x.y.z.255) gesetzt.
        # Leer = zur Laufzeit automatisch ipv4_subnet_broadcast_default()
        "udp_pst_send_host": "",
        # AirScout/KST: ASWATCHLIST/ASSETPATH auf aswatch_udp_listen_host:aswatch_udp_port (Standard aus, 127.0.0.1)
        "aswatch_udp_enabled": False,
        "aswatch_udp_port": 9872,
        "aswatch_udp_listen_host": "127.0.0.1",
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
        # Karte: nur Stationen mit dest_key aus der ASNEAREST-Tabelle „Nächste Verbindungen“
        "map_aswatch_only_asnearest_list": False,
        # Karte: Leaflet MarkerCluster für ASWATCH-User (aus = einzelne Marker)
        "map_aswatch_cluster_enabled": True,
        # Globale Tastenkürzel (Windows RegisterHotKey; siehe Einstellungen → Shortcuts)
        "global_shortcuts": {
            "enabled": True,
            "modifier_1": "control",
            "modifier_2": "shift",
            "antenna_deg_w": 0.0,
            "antenna_deg_d": 90.0,
            "antenna_deg_s": 180.0,
            "antenna_deg_a": 270.0,
            "key_win_alt_w": "UP",
            "key_win_alt_d": "RIGHT",
            "key_win_alt_s": "DOWN",
            "key_win_alt_a": "LEFT",
            "key_win_alt_compass": "K",
            "key_win_alt_map": "M",
            "key_win_alt_elevation": "H",
            "key_ctrl_alt_plus": "PRIOR",
            "key_ctrl_alt_minus": "NEXT",
            "target_step_deg": 3.0,
            "el_target_step_deg": 5.0,
            "key_el_target_plus": "R",
            "key_el_target_minus": "F",
            "key_antenna_1": "1",
            "key_antenna_2": "2",
            "key_antenna_3": "3",
        },
    },
}


def _merge(dst: Dict[str, Any], src: Dict[str, Any]):
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _merge(dst[k], v)
        else:
            dst[k] = v


def _apply_compass_strom_analysis_defaults(ui: Dict[str, Any]) -> None:
    """Fehlende Schlüssel mit „Stromanalyse aus“ füllen (Erststart / minimale Installer-config).

    Bestehende Nutzerwahl (z. B. Strom an) wird nicht überschrieben.
    """
    ui.setdefault("compass_strom_az", False)
    ui.setdefault("compass_strom_el", False)
    ui.setdefault("compass_heatmap_az", "off")
    ui.setdefault("compass_heatmap_el", "off")
    if not isinstance(ui.get("compass_heatmap_az_modes"), list):
        ui["compass_heatmap_az_modes"] = []


def load_config() -> Dict[str, Any]:
    p = config_path()
    if not p.exists():
        initial = json.loads(json.dumps(DEFAULT_CONFIG))
        ui = initial.setdefault("ui", {})
        # Erste Installation: UcxLog/AirScout/PST lauschen standardmaessig nur lokal
        # (Loopback). bind("127.0.0.1") laesst Windows alle Pakete verwerfen, die
        # nicht an diesen Rechner adressiert sind — schuetzt vor Fremd-Setups im LAN.
        # PST-Ziel bleibt Subnetz-Broadcast, damit PstRotator-Clients uns weiter finden.
        ui["udp_ucxlog_listen_host"] = "127.0.0.1"
        ui["udp_pst_listen_host"] = "127.0.0.1"
        ui["aswatch_udp_listen_host"] = "127.0.0.1"
        ui["udp_pst_send_host"] = ipv4_subnet_broadcast_default()
        _apply_compass_strom_analysis_defaults(ui)
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

    # Migration: rig_bridge flach → {rigs: [...], active_rig_id}.
    # Alte Konfigurationen hatten com_port/rig_brand/... direkt unterhalb
    # "rig_bridge". Neu lebt das pro Profil in ``rigs[]``; das aktive Profil
    # referenziert ``active_rig_id``.
    if "rig_bridge" in cfg and isinstance(cfg["rig_bridge"], dict):
        rb = cfg["rig_bridge"]
        if "rigs" not in rb or not isinstance(rb.get("rigs"), list) or not rb.get("rigs"):
            # Flach → ein Profil. flrig/hamlib bleiben aber GLOBAL — die
            # alten flachen Keys flrig/hamlib wandern daher nicht ins Profil
            # (vergleiche naechster Migrationsblock).
            flat = {
                k: v
                for k, v in rb.items()
                if k not in ("rigs", "active_rig_id", "flrig", "hamlib")
            }
            # Ein Profil aus der alten flachen Struktur bauen.
            selected_rig = str(flat.get("selected_rig", "") or "").strip() or "Rig 1"
            profile = dict(flat)
            profile.setdefault("id", "default")
            profile.setdefault("name", selected_rig)
            # Globales "enabled" war frueher identisch mit "Rig-Bridge an".
            # Wir speichern pro Profil zusaetzlich ein "enabled" (Standard True),
            # damit einzelne Profile deaktiviert werden koennen, ohne das
            # Dropdown leer zu machen.
            profile.setdefault("enabled", True)
            new_rb: Dict[str, Any] = {
                "enabled": bool(flat.get("enabled", False)),
                "active_rig_id": str(profile["id"]),
                "rigs": [profile],
            }
            # Eventuell vorhandenes flaches flrig/hamlib bleibt auf der
            # obersten Rig-Bridge-Ebene liegen (globale Protokoll-Einstellungen).
            if isinstance(rb.get("flrig"), dict):
                new_rb["flrig"] = dict(rb["flrig"])
            if isinstance(rb.get("hamlib"), dict):
                new_rb["hamlib"] = dict(rb["hamlib"])
            cfg["rig_bridge"] = new_rb
        else:
            # Neue Form — nur Defaults absichern.
            rb.setdefault("active_rig_id", "")
            rb.setdefault("enabled", False)
            for pr in rb["rigs"]:
                if isinstance(pr, dict):
                    pr.setdefault("enabled", True)
                    pr.setdefault("name", str(pr.get("selected_rig", "") or pr.get("id", "Rig")))
            if not rb["active_rig_id"] and rb["rigs"]:
                first = rb["rigs"][0]
                if isinstance(first, dict):
                    rb["active_rig_id"] = str(first.get("id", "default"))

    # Migration: Flrig/Hamlib sind globale Settings (ein TCP-Server fuer alle
    # Rigs). Aeltere Configs hielten sie pro Profil. Hebt sie aus dem aktiven
    # (oder ersten) Profil auf die oberste Rig-Bridge-Ebene und entfernt sie
    # anschliessend aus allen Profilen.
    if "rig_bridge" in cfg and isinstance(cfg["rig_bridge"], dict):
        rb = cfg["rig_bridge"]
        rigs_list = rb.get("rigs") if isinstance(rb.get("rigs"), list) else []
        if rigs_list:
            src_prof: Dict[str, Any] | None = None
            aid = str(rb.get("active_rig_id", "") or "")
            for pr in rigs_list:
                if isinstance(pr, dict) and str(pr.get("id", "")) == aid:
                    src_prof = pr
                    break
            if src_prof is None:
                for pr in rigs_list:
                    if isinstance(pr, dict):
                        src_prof = pr
                        break
            if isinstance(src_prof, dict):
                if "flrig" not in rb and isinstance(src_prof.get("flrig"), dict):
                    rb["flrig"] = dict(src_prof["flrig"])
                if "hamlib" not in rb and isinstance(src_prof.get("hamlib"), dict):
                    rb["hamlib"] = dict(src_prof["hamlib"])
            for pr in rigs_list:
                if isinstance(pr, dict):
                    pr.pop("flrig", None)
                    pr.pop("hamlib", None)

    # Rig-Profil-Anzeigenamen auf max. Länge begrenzen (ältere Configs).
    if "rig_bridge" in cfg and isinstance(cfg["rig_bridge"], dict):
        from .rig_bridge.config import clamp_rig_profile_display_name

        rb = cfg["rig_bridge"]
        rigs_list = rb.get("rigs")
        if isinstance(rigs_list, list):
            for pr in rigs_list:
                if isinstance(pr, dict):
                    pr["name"] = clamp_rig_profile_display_name(
                        pr.get("name", pr.get("id", ""))
                    )

    # Migration: udp_pst_send_host "0.0.0.0" → leer (Windows: sendto ungültig, WinError 10049).
    if "ui" in cfg and isinstance(cfg["ui"], dict):
        ui_m = cfg["ui"]
        if str(ui_m.get("udp_pst_send_host", "")).strip() == "0.0.0.0":
            ui_m["udp_pst_send_host"] = ""

    # Migration: pst_serial Listener um ``target`` erweitern.
    if "pst_serial" in cfg and isinstance(cfg["pst_serial"], dict):
        ps = cfg["pst_serial"]
        lst = ps.get("listeners")
        if isinstance(lst, list):
            for item in lst:
                if isinstance(item, dict):
                    item.setdefault("target", "rotor")

    # Merge defaults (für neue Felder bei Updates)
    merged = json.loads(json.dumps(DEFAULT_CONFIG))
    _merge(merged, cfg)
    ui = merged.setdefault("ui", {})
    _apply_compass_strom_analysis_defaults(ui)
    # Entfernt: Schnell-Buttons (GUI gibt es nicht mehr); alte Keys aus früheren Versionen verwerfen.
    ui.pop("quick_buttons", None)
    return merged


def save_config(cfg: Dict[str, Any]):
    p = config_path()
    with open(p, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
