"""Persistierte Konfiguration der Rotorübersicht (lokale Instanz + fremde Apps per HTTP)."""

from __future__ import annotations

import copy
import json
import os
import uuid
from typing import Any, Dict, List, Optional

from .app_config import appdata_dir
from .geo_utils import ANTENNA_BEAM_COLORS

OVERVIEW_CONFIG_FILENAME = "rotor_overview.json"
LOCAL_SITE_ID = "local"
DEFAULT_APP_PORT = 80

_DEFAULT_ANT_RANGE_KM = 100.0
_DEFAULT_OPENING_DEG = 30.0


def overview_config_path() -> str:
    return os.path.join(str(appdata_dir()), OVERVIEW_CONFIG_FILENAME)


def default_antenna(slot: int) -> Dict[str, Any]:
    i = max(0, min(2, int(slot) - 1))
    color = ANTENNA_BEAM_COLORS[i][0]
    return {
        "enabled": True,
        "name": f"Antenne {slot}",
        "offset_deg": 0.0,
        "opening_deg": _DEFAULT_OPENING_DEG,
        "range_km": _DEFAULT_ANT_RANGE_KM,
        "color": color,
        "dipole": False,
    }


def normalize_antenna(raw: Any, slot: int) -> Dict[str, Any]:
    d = default_antenna(slot)
    if not isinstance(raw, dict):
        return d
    d["enabled"] = bool(raw.get("enabled", d["enabled"]))
    d["name"] = str(raw.get("name") or d["name"]).strip() or d["name"]
    try:
        d["offset_deg"] = float(raw.get("offset_deg", d["offset_deg"]))
    except (TypeError, ValueError):
        pass
    try:
        d["opening_deg"] = max(1.0, min(180.0, float(raw.get("opening_deg", d["opening_deg"]))))
    except (TypeError, ValueError):
        pass
    try:
        d["range_km"] = max(1.0, min(20000.0, float(raw.get("range_km", d["range_km"]))))
    except (TypeError, ValueError):
        pass
    c = str(raw.get("color") or "").strip()
    if c.startswith("#") and len(c) in (4, 7):
        d["color"] = c
    d["dipole"] = bool(raw.get("dipole", False))
    return d


def merge_site_antennas(cfg_ants: Any, live_ants: Any) -> List[Dict[str, Any]]:
    """Live-Geometrie/Namen übernehmen, Anzeige-Häkchen (enabled) aus der Standort-Config behalten."""
    cfg_list = cfg_ants if isinstance(cfg_ants, list) else []
    live_list = live_ants if isinstance(live_ants, list) else []
    out: List[Dict[str, Any]] = []
    for i in range(3):
        slot = i + 1
        base = normalize_antenna(cfg_list[i] if i < len(cfg_list) else None, slot)
        if i < len(live_list) and isinstance(live_list[i], dict):
            merged = normalize_antenna(live_list[i], slot)
            merged["enabled"] = bool(base.get("enabled", True))
            out.append(merged)
        else:
            out.append(base)
    return out


def default_local_site() -> Dict[str, Any]:
    return {
        "id": LOCAL_SITE_ID,
        "kind": "local",
        "enabled": True,
        "name": "",
        "host": "",
        "port": DEFAULT_APP_PORT,
        "password": "",
        "lat": 50.0,
        "lon": 10.0,
        "antennas": [default_antenna(i) for i in range(1, 4)],
    }


def default_site(*, name: str = "", port: Optional[int] = None) -> Dict[str, Any]:
    """Neue Remote-Instanz: nur Host/Port/Passwort; Labels kommen von der Node-API."""
    p = int(port) if port else DEFAULT_APP_PORT
    if p < 1 or p > 65535:
        p = DEFAULT_APP_PORT
    return {
        "id": str(uuid.uuid4()),
        "kind": "app",
        "enabled": True,
        "name": str(name or "").strip(),
        "host": "",
        "port": p,
        "password": "",
        "lat": 50.0,
        "lon": 10.0,
        "antennas": [default_antenna(i) for i in range(1, 4)],
    }


def deep_copy_config(cfg: Any) -> Dict[str, Any]:
    return copy.deepcopy(normalize_overview_config(cfg))


def _as_int(v: Any, default: int) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return int(default)


def _as_float(v: Any, default: float) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return float(default)


def normalize_site(raw: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    kind = str(raw.get("kind") or "").strip().lower()
    if kind not in ("local", "app"):
        return None
    sid = str(raw.get("id") or "").strip()
    if kind == "local":
        sid = LOCAL_SITE_ID
    elif not sid:
        sid = str(uuid.uuid4())
    port = _as_int(raw.get("port"), DEFAULT_APP_PORT)
    if port < 1 or port > 65535:
        port = DEFAULT_APP_PORT
    ants = raw.get("antennas")
    if not isinstance(ants, list):
        ants = []
    antennas = [normalize_antenna(ants[i] if i < len(ants) else None, i + 1) for i in range(3)]
    return {
        "id": sid,
        "kind": kind,
        "enabled": bool(raw.get("enabled", True)),
        "name": str(raw.get("name") or "").strip(),
        "host": str(raw.get("host") or "").strip(),
        "port": port,
        "password": str(raw.get("password") or ""),
        "lat": _as_float(raw.get("lat"), 50.0),
        "lon": _as_float(raw.get("lon"), 10.0),
        "antennas": antennas,
    }


def default_overview_config() -> Dict[str, Any]:
    return {
        "sites": [default_local_site()],
        "map_center_lat": 50.0,
        "map_center_lon": 10.0,
        "map_zoom": 5,
        "show_active_antenna_only": False,
        "antenna_select_v1": True,
    }


def _ensure_local_site(sites: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    local: Optional[Dict[str, Any]] = None
    others: List[Dict[str, Any]] = []
    for s in sites:
        if s.get("kind") == "local" or str(s.get("id") or "") == LOCAL_SITE_ID:
            if local is None:
                s["id"] = LOCAL_SITE_ID
                s["kind"] = "local"
                local = s
            continue
        others.append(s)
    if local is None:
        local = default_local_site()
    return [local] + others


def normalize_overview_config(raw: Any) -> Dict[str, Any]:
    cfg = default_overview_config()
    if not isinstance(raw, dict):
        return cfg
    sites: List[Dict[str, Any]] = []
    for item in raw.get("sites") or []:
        s = normalize_site(item)
        if s is not None:
            sites.append(s)
    cfg["sites"] = _ensure_local_site(sites)
    cfg["map_center_lat"] = _as_float(raw.get("map_center_lat"), 50.0)
    cfg["map_center_lon"] = _as_float(raw.get("map_center_lon"), 10.0)
    try:
        cfg["map_zoom"] = max(2, min(18, int(raw.get("map_zoom", 5))))
    except (TypeError, ValueError):
        cfg["map_zoom"] = 5
    cfg["show_active_antenna_only"] = bool(raw.get("show_active_antenna_only", False))
    # Einmalig: alte Configs hatten oft nur Ant.1 enabled (ohne UI); Live-Flags
    # bestimmten die Anzeige. Ab v1 gelten die Standort-Häkchen — Standard: alle an.
    if not bool(raw.get("antenna_select_v1", False)):
        for site in cfg["sites"]:
            for ant in site.get("antennas") or []:
                if isinstance(ant, dict):
                    ant["enabled"] = True
        cfg["antenna_select_v1"] = True
    else:
        cfg["antenna_select_v1"] = True
    return cfg


def load_overview_config() -> Dict[str, Any]:
    path = overview_config_path()
    if not os.path.isfile(path):
        cfg = default_overview_config()
        try:
            save_overview_config(cfg)
        except Exception:
            pass
        return cfg
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        return default_overview_config()
    had_local = False
    if isinstance(raw, dict):
        for item in raw.get("sites") or []:
            if isinstance(item, dict) and (
                str(item.get("kind") or "").lower() == "local"
                or str(item.get("id") or "") == LOCAL_SITE_ID
            ):
                had_local = True
                break
    cfg = normalize_overview_config(raw)
    if not had_local:
        try:
            save_overview_config(cfg)
        except Exception:
            pass
    return cfg


def save_overview_config(cfg: Dict[str, Any]) -> None:
    path = overview_config_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    data = normalize_overview_config(cfg)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)
