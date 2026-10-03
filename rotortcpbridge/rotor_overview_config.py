"""Eigene Config für das Fenster „Rotorübersicht“ (getrennt von Rotor-Profilen)."""

from __future__ import annotations

import json
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional

from .geo_utils import ANTENNA_BEAM_COLORS

_FILE_NAME = "rotor_overview.json"

_DEFAULT_COLORS = [c[0] for c in ANTENNA_BEAM_COLORS] + [
    "#E67E22",
    "#E74C3C",
    "#1ABC9C",
    "#9B59B6",
    "#3498DB",
]


def overview_config_path() -> Path:
    from .app_config import appdata_dir

    return appdata_dir() / _FILE_NAME


def _default_antenna(slot: int, *, enabled: bool = False) -> Dict[str, Any]:
    i = max(0, min(2, int(slot) - 1))
    stroke = _DEFAULT_COLORS[i % len(_DEFAULT_COLORS)]
    return {
        "enabled": bool(enabled),
        "name": f"Antenne {slot}",
        "offset_deg": 0.0,
        "opening_deg": 30.0,
        "range_km": 100.0,
        "color": stroke,
    }


def default_site(*, name: str = "Standort") -> Dict[str, Any]:
    return {
        "id": str(uuid.uuid4()),
        "name": str(name or "Standort"),
        "enabled": True,
        "lat": 49.5,
        "lon": 8.4,
        "locator": "",
        "mode": "tcp",
        "host": "192.168.0.1",
        "port": 8886,
        "udp_bind_port": 0,
        "master_id": 0,
        "slave_az": 20,
        "poll_pos_ms": 1000,
        "poll_ref_ms": 5000,
        "smooth_alpha": 0.35,
        "antennas": [
            _default_antenna(1, enabled=True),
            _default_antenna(2, enabled=False),
            _default_antenna(3, enabled=False),
        ],
    }


def default_overview_config() -> Dict[str, Any]:
    return {
        "version": 1,
        "map_center_lat": 50.0,
        "map_center_lon": 10.0,
        "map_zoom": 5,
        "sites": [],
    }


def _clamp_float(v: Any, lo: float, hi: float, default: float) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return float(default)
    return max(lo, min(hi, x))


def _clamp_int(v: Any, lo: int, hi: int, default: int) -> int:
    try:
        x = int(v)
    except (TypeError, ValueError):
        return int(default)
    return max(lo, min(hi, x))


def normalize_antenna(raw: Any, slot: int) -> Dict[str, Any]:
    base = _default_antenna(slot, enabled=False)
    if not isinstance(raw, dict):
        return base
    out = dict(base)
    out["enabled"] = bool(raw.get("enabled", False))
    name = str(raw.get("name") or "").strip()
    out["name"] = name or base["name"]
    out["offset_deg"] = _clamp_float(raw.get("offset_deg"), -3600.0, 3600.0, 0.0)
    out["opening_deg"] = _clamp_float(raw.get("opening_deg"), 1.0, 360.0, 30.0)
    out["range_km"] = _clamp_float(raw.get("range_km"), 1.0, 4000.0, 100.0)
    color = str(raw.get("color") or "").strip()
    if color.startswith("#") and len(color) in (4, 7):
        out["color"] = color
    return out


def normalize_site(raw: Any) -> Dict[str, Any]:
    base = default_site()
    if not isinstance(raw, dict):
        return base
    out = dict(base)
    sid = str(raw.get("id") or "").strip()
    out["id"] = sid or str(uuid.uuid4())
    name = str(raw.get("name") or "").strip()
    out["name"] = name or "Standort"
    out["enabled"] = bool(raw.get("enabled", True))
    out["lat"] = _clamp_float(raw.get("lat"), -90.0, 90.0, 49.5)
    out["lon"] = _clamp_float(raw.get("lon"), -180.0, 180.0, 8.4)
    out["locator"] = str(raw.get("locator") or "").strip()
    mode = str(raw.get("mode") or "tcp").strip().lower()
    out["mode"] = mode if mode in ("tcp", "udp") else "tcp"
    out["host"] = str(raw.get("host") or "192.168.0.1").strip() or "192.168.0.1"
    out["port"] = _clamp_int(raw.get("port"), 1, 65535, 8886)
    out["udp_bind_port"] = _clamp_int(raw.get("udp_bind_port"), 0, 65535, 0)
    out["master_id"] = _clamp_int(raw.get("master_id"), 0, 254, 0)
    out["slave_az"] = _clamp_int(raw.get("slave_az"), 1, 254, 20)
    out["poll_pos_ms"] = _clamp_int(raw.get("poll_pos_ms"), 200, 60000, 1000)
    out["poll_ref_ms"] = _clamp_int(raw.get("poll_ref_ms"), 1000, 120000, 5000)
    out["smooth_alpha"] = _clamp_float(raw.get("smooth_alpha"), 0.05, 1.0, 0.35)
    ants_in = raw.get("antennas")
    ants: List[Dict[str, Any]] = []
    if isinstance(ants_in, list):
        for i in range(3):
            src = ants_in[i] if i < len(ants_in) else None
            ants.append(normalize_antenna(src, i + 1))
    else:
        ants = [
            _default_antenna(1, enabled=True),
            _default_antenna(2, enabled=False),
            _default_antenna(3, enabled=False),
        ]
    out["antennas"] = ants
    return out


def normalize_overview_config(raw: Any) -> Dict[str, Any]:
    base = default_overview_config()
    if not isinstance(raw, dict):
        return base
    out = dict(base)
    out["version"] = 1
    out["map_center_lat"] = _clamp_float(raw.get("map_center_lat"), -90.0, 90.0, 50.0)
    out["map_center_lon"] = _clamp_float(raw.get("map_center_lon"), -180.0, 180.0, 10.0)
    out["map_zoom"] = _clamp_int(raw.get("map_zoom"), 2, 18, 5)
    sites_in = raw.get("sites")
    sites: List[Dict[str, Any]] = []
    if isinstance(sites_in, list):
        for item in sites_in:
            sites.append(normalize_site(item))
    out["sites"] = sites
    return out


def load_overview_config() -> Dict[str, Any]:
    path = overview_config_path()
    if not path.is_file():
        return default_overview_config()
    try:
        with path.open("r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        return default_overview_config()
    return normalize_overview_config(raw)


def save_overview_config(cfg: Dict[str, Any]) -> None:
    path = overview_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = normalize_overview_config(cfg)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


def site_from_profile(profile_id: str, profile_name: str, cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Rotor-Profil → Overview-Standort (Kopie, danach unabhängig)."""
    site = default_site(name=str(profile_name or "Standort"))
    hl = cfg.get("hardware_link") if isinstance(cfg.get("hardware_link"), dict) else {}
    rb = cfg.get("rotor_bus") if isinstance(cfg.get("rotor_bus"), dict) else {}
    ui = cfg.get("ui") if isinstance(cfg.get("ui"), dict) else {}

    mode = str(hl.get("mode") or "tcp").strip().lower()
    site["mode"] = mode if mode in ("tcp", "udp") else "tcp"
    site["host"] = str(hl.get("tcp_ip") or site["host"]).strip() or site["host"]
    try:
        site["port"] = int(hl.get("tcp_port", site["port"]))
    except (TypeError, ValueError):
        pass
    try:
        site["udp_bind_port"] = int(hl.get("udp_bind_port", 0) or 0)
    except (TypeError, ValueError):
        site["udp_bind_port"] = 0
    try:
        site["master_id"] = int(rb.get("master_id", 0) or 0)
    except (TypeError, ValueError):
        site["master_id"] = 0
    try:
        site["slave_az"] = int(rb.get("slave_az", 20) or 20)
    except (TypeError, ValueError):
        site["slave_az"] = 20

    try:
        site["lat"] = float(ui.get("location_lat", site["lat"]))
    except (TypeError, ValueError):
        pass
    try:
        site["lon"] = float(ui.get("location_lon", site["lon"]))
    except (TypeError, ValueError):
        pass
    site["locator"] = str(ui.get("location_locator") or "").strip()

    names = ui.get("antenna_names") or []
    offs = ui.get("antenna_offsets_az") or []
    angles = ui.get("antenna_angles_az") or []
    ranges = ui.get("antenna_ranges_az") or []
    az_on = bool(rb.get("enable_az", True))
    ants: List[Dict[str, Any]] = []
    for i in range(3):
        ant = _default_antenna(i + 1, enabled=(az_on and i == 0))
        if isinstance(names, list) and i < len(names) and str(names[i] or "").strip():
            ant["name"] = str(names[i]).strip()
        try:
            if isinstance(offs, list) and i < len(offs):
                ant["offset_deg"] = float(offs[i])
        except (TypeError, ValueError):
            pass
        try:
            if isinstance(angles, list) and i < len(angles):
                ant["opening_deg"] = max(1.0, float(angles[i]) or 30.0)
        except (TypeError, ValueError):
            pass
        try:
            if isinstance(ranges, list) and i < len(ranges):
                ant["range_km"] = max(1.0, float(ranges[i]) or 100.0)
        except (TypeError, ValueError):
            pass
        # Weitere Antennen aktiv, wenn Öffnung/Name gesetzt und AZ an
        if az_on and i > 0:
            has_meta = ant["opening_deg"] > 0 and (
                (isinstance(names, list) and i < len(names) and str(names[i] or "").strip())
                or (isinstance(offs, list) and i < len(offs) and float(offs[i] or 0) != 0)
            )
            ant["enabled"] = bool(has_meta)
        ants.append(ant)
    site["antennas"] = ants
    site["id"] = str(uuid.uuid4())
    # Merker nur informativ (kein Sync)
    site["_imported_profile_id"] = str(profile_id or "")
    return normalize_site(site)


def next_distinct_color(used: List[str]) -> str:
    used_l = {str(c).lower() for c in used}
    for c in _DEFAULT_COLORS:
        if c.lower() not in used_l:
            return c
    return _DEFAULT_COLORS[len(used) % len(_DEFAULT_COLORS)]


def deep_copy_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    return deepcopy(normalize_overview_config(cfg))
