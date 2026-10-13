"""Snapshot einer RotorTcpBridge-Instanz für die Rotorübersicht (kein Extra-Bus)."""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from .angle_utils import antenna_dipole_enabled, az_pos_deg_from_d10
from .geo_utils import ANTENNA_BEAM_COLORS, effective_station_lat_lon
from .rotor_overview_config import normalize_antenna


def _cfg_list_float(raw: Any, idx: int, default: float) -> float:
    if not isinstance(raw, list) or idx < 0 or idx >= len(raw):
        return float(default)
    try:
        return float(raw[idx])
    except (TypeError, ValueError):
        return float(default)


def _axis_ant_float(axis: Any, attr: str, fallback: float) -> float:
    if axis is None:
        return float(fallback)
    v = getattr(axis, attr, None)
    if v is None:
        return float(fallback)
    try:
        return float(v)
    except (TypeError, ValueError):
        return float(fallback)


def _axis_ant_int(axis: Any, attr: str, fallback: int) -> int:
    if axis is None:
        return int(fallback)
    v = getattr(axis, attr, None)
    if v is None:
        return int(fallback)
    try:
        return int(v)
    except (TypeError, ValueError):
        return int(fallback)


def _opt_float(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def build_overview_antennas(ctrl: Any, cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    ui = cfg.get("ui") if isinstance(cfg.get("ui"), dict) else {}
    rb = cfg.get("rotor_bus") if isinstance(cfg.get("rotor_bus"), dict) else {}
    az_on = bool(rb.get("enable_az", True))
    az = getattr(ctrl, "az", None) if ctrl is not None else None
    names = ui.get("antenna_names") or []
    offs = ui.get("antenna_offsets_az") or []
    angles = ui.get("antenna_angles_az") or []
    ranges = ui.get("antenna_ranges_az") or []
    ants: List[Dict[str, Any]] = []
    for i in range(3):
        slot = i + 1
        name = ""
        if isinstance(names, list) and i < len(names):
            name = str(names[i] or "").strip()
        offset = _axis_ant_float(az, f"antoff{slot}", _cfg_list_float(offs, i, 0.0))
        opening = _axis_ant_float(az, f"angle{slot}", _cfg_list_float(angles, i, 30.0))
        if opening < 1.0:
            opening = 30.0
        range_km = float(_axis_ant_int(az, f"antdis{slot}", int(_cfg_list_float(ranges, i, 100.0))))
        if range_km < 1.0:
            range_km = 100.0
        color = ANTENNA_BEAM_COLORS[i % len(ANTENNA_BEAM_COLORS)][0]
        enabled = bool(az_on)
        if az_on and i > 0:
            enabled = bool(
                name
                or abs(offset) > 0.01
                or (isinstance(angles, list) and i < len(angles) and float(angles[i] or 0) > 0)
            )
        raw = {
            "enabled": enabled,
            "name": name or f"Antenne {slot}",
            "offset_deg": offset,
            "opening_deg": opening,
            "range_km": range_km,
            "color": color,
            "dipole": antenna_dipole_enabled(az, cfg, i),
        }
        ants.append(normalize_antenna(raw, slot))
        ants[-1]["enabled"] = bool(enabled)
    return ants


def build_overview_node_snapshot(ctrl: Any, cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Live-Status dieser Instanz: Profil, Standort, Ist, Antennen — ohne Bus-Poll."""
    cfg = cfg if isinstance(cfg, dict) else {}
    ui = cfg.get("ui") if isinstance(cfg.get("ui"), dict) else {}
    rb = cfg.get("rotor_bus") if isinstance(cfg.get("rotor_bus"), dict) else {}
    lat, lon = effective_station_lat_lon(ui)
    locator = str(ui.get("location_locator") or "").strip()
    profile_id = ""
    profile_name = ""
    try:
        from .profile_store import get_active_profile_meta

        meta = get_active_profile_meta() or {}
        profile_id = str(meta.get("id") or "")
        profile_name = str(meta.get("name") or profile_id or "")
    except Exception:
        pass
    az_deg: Optional[float] = None
    referenced: Optional[bool] = None
    moving = False
    online = False
    az = getattr(ctrl, "az", None) if ctrl is not None else None
    if az is not None:
        try:
            online = bool(getattr(az, "online", False))
        except Exception:
            online = False
        try:
            referenced = bool(getattr(az, "referenced", False))
        except Exception:
            referenced = None
        try:
            moving = bool(getattr(az, "moving", False))
        except Exception:
            moving = False
        try:
            max_d10 = int(getattr(az, "pos_max_d10", 3600) or 3600)
            az_deg = az_pos_deg_from_d10(
                int(getattr(az, "pos_d10", 0) or 0),
                float(getattr(az, "smooth_pos_d10f", 0.0) or 0.0),
                max_d10,
            )
        except Exception:
            az_deg = None
    aselect = None
    try:
        aselect = max(1, min(3, int(ui.get("compass_antenna", 0)) + 1))
    except (TypeError, ValueError):
        aselect = 1
    try:
        slave_az = int(rb.get("slave_az", getattr(ctrl, "slave_az", 20) or 20))
    except (TypeError, ValueError):
        slave_az = 20
    if ctrl is not None:
        try:
            slave_az = int(getattr(ctrl, "slave_az", slave_az) or slave_az)
        except (TypeError, ValueError):
            pass
    tel = getattr(az, "telemetry", None) if az is not None else None
    temp_motor = _opt_float(getattr(tel, "temp_motor_c", None) if tel is not None else None)
    temp_ambient = _opt_float(getattr(tel, "temp_ambient_c", None) if tel is not None else None)
    # Nur GETWINDENABLE: Telemetrie-Werte ohne Sensor flackern (kommen/gehen).
    wind_on = False
    if ctrl is not None:
        known = bool(getattr(ctrl, "wind_enabled_known", False))
        wind_on = bool(getattr(ctrl, "wind_enabled", False)) if known else False
    wind_kmh = None
    wind_dir = None
    if wind_on:
        wind_kmh = _opt_float(getattr(tel, "wind_kmh", None) if tel is not None else None)
        wind_dir = _opt_float(getattr(tel, "wind_dir_deg", None) if tel is not None else None)
    return {
        "profile_id": profile_id,
        "profile_name": profile_name,
        "lat": float(lat),
        "lon": float(lon),
        "locator": locator,
        "online": bool(online),
        "az_deg": az_deg,
        "moving": bool(moving),
        "referenced": referenced,
        "aselect": aselect,
        "antennas": build_overview_antennas(ctrl, cfg),
        "slave_az": slave_az,
        "temp_motor_c": temp_motor,
        "temp_ambient_c": temp_ambient,
        "wind_kmh": wind_kmh,
        "wind_dir_deg": wind_dir,
        "wind_visible": bool(wind_on),
    }


def apply_node_snapshot_to_state(state: Any, data: Optional[Dict[str, Any]]) -> None:
    """Node-JSON in SiteLiveState schreiben."""
    if not isinstance(data, dict):
        return
    try:
        az = data.get("az_deg")
        if az is not None:
            state.az_deg = float(az)
            state.az_smooth_deg = float(az)
    except (TypeError, ValueError):
        pass
    try:
        sel = data.get("aselect")
        state.aselect = int(sel) if sel in (1, 2, 3) else None
    except (TypeError, ValueError):
        state.aselect = None
    try:
        ref = data.get("referenced")
        state.referenced = None if ref is None else bool(ref)
    except Exception:
        pass
    try:
        state.moving = bool(data.get("moving", False))
    except Exception:
        state.moving = False
    try:
        state.online = bool(data.get("online", False))
    except Exception:
        state.online = False
    state.profile_name = str(data.get("profile_name") or "").strip()
    state.locator = str(data.get("locator") or "").strip()
    try:
        state.lat = float(data.get("lat"))
    except (TypeError, ValueError):
        state.lat = None
    try:
        state.lon = float(data.get("lon"))
    except (TypeError, ValueError):
        state.lon = None
    ants = data.get("antennas")
    if isinstance(ants, list):
        state.antennas = [normalize_antenna(a, i + 1) for i, a in enumerate(ants[:3])]
        while len(state.antennas) < 3:
            state.antennas.append(normalize_antenna(None, len(state.antennas) + 1))
    state.temp_motor_c = _opt_float(data.get("temp_motor_c"))
    state.temp_ambient_c = _opt_float(data.get("temp_ambient_c"))
    state.wind_visible = bool(data.get("wind_visible"))
    if state.wind_visible:
        state.wind_kmh = _opt_float(data.get("wind_kmh"))
        state.wind_dir_deg = _opt_float(data.get("wind_dir_deg"))
    else:
        state.wind_kmh = None
        state.wind_dir_deg = None
    state.last_rx_ts = time.time()
    state.last_error = ""
