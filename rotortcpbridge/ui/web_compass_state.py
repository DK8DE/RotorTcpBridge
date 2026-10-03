"""Web-Kompass Live-Payload und Steueraktionen (von MapWindow genutzt)."""

from __future__ import annotations

import math
import time
from typing import Any

from PySide6.QtGui import QColor

from ..angle_utils import (
    az_max_d10_from_axis,
    az_pos_deg_from_d10,
    clamp_el,
    el_max_deg_from_rotor_type,
    om_beam_contributions_per_sector,
    raw_rotor_az_deg_from_axis,
    rotor_az_for_display_bearing,
    shortest_delta_az_rotor_deg,
    wrap_deg,
)
from ..compass.statistic_compass_widget import parse_heatmap_scale
from ..geo_utils import ANTENNA_BEAM_COLORS, bearing_deg, effective_station_lat_lon, haversine_km
from ..i18n import t
from .compass_html import build_compass_html

# Letztes SETPOSCC behalten, wenn compass_target kurz gelöscht wird (SPID/Echo)
# — sonst springt der Web-Sollzeiger zwischen neuem CC und altem target_d10.
_WEB_CC_LATCH_KEEP_S = 2.0
# SCAN: erst umschalten, wenn Ist nahe am Soll (moving kann früher False werden)
_WEB_SCAN_ARRIVE_DEG = 0.8
_WEB_SCAN_CMD_GUARD_S = 0.5


def web_scan_goto(mw: Any, display_deg: float) -> None:
    """Ein SCAN-Bein anfahren und Ziel/Zeitstempel merken."""
    web_apply_set_az_display(mw, float(display_deg), exact=False)
    mw._web_scan_cmd_ts = time.time()
    mw._web_scan_saw_moving = False
    try:
        mw._web_scan_rotor_tgt = float(getattr(mw.ctrl.az, "target_d10", 0)) / 10.0
    except Exception:
        try:
            mw._web_scan_rotor_tgt = raw_rotor_az_deg_from_axis(getattr(mw.ctrl, "az", None))
        except Exception:
            mw._web_scan_rotor_tgt = None


def web_tick_scan(mw: Any) -> None:
    """SCAN-Bein wechseln erst nach Erreichen des Ist-Ziels (nicht nur moving=False)."""
    if not getattr(mw, "_web_scan_active", False):
        return
    a = getattr(mw, "_web_scan_a", None)
    b = getattr(mw, "_web_scan_b", None)
    if a is None or b is None:
        return
    now = time.time()
    if (now - float(getattr(mw, "_web_scan_cmd_ts", 0.0) or 0.0)) < _WEB_SCAN_CMD_GUARD_S:
        return
    moving = bool(getattr(mw.ctrl.az, "moving", False))
    if moving:
        mw._web_scan_saw_moving = True
        return
    cur = raw_rotor_az_deg_from_axis(getattr(mw.ctrl, "az", None))
    tgt = getattr(mw, "_web_scan_rotor_tgt", None)
    if cur is None or tgt is None:
        return
    try:
        err = abs(shortest_delta_az_rotor_deg(float(cur), float(tgt)))
    except Exception:
        return
    if err > _WEB_SCAN_ARRIVE_DEG:
        return
    if getattr(mw, "_web_scan_next_is_b", True):
        nxt = float(b)
        mw._web_scan_next_is_b = False
    else:
        nxt = float(a)
        mw._web_scan_next_is_b = True
    web_scan_goto(mw, nxt)


def web_effective_az_target_d10(mw: Any) -> int:
    """Effektives AZ-Soll für Web (SETPOSCC bevorzugt, mit Latch gegen Flackern)."""
    axis = getattr(getattr(mw, "ctrl", None), "az", None)
    if axis is None:
        return 0
    now = time.time()
    try:
        cc = getattr(axis, "compass_target_d10", None)
    except Exception:
        cc = None
    try:
        moving = bool(getattr(axis, "moving", False))
    except Exception:
        moving = False
    if cc is not None:
        mw._web_cc_latch_az_d10 = int(cc)
        mw._web_cc_latch_az_ts = now
        return int(cc)
    latch = getattr(mw, "_web_cc_latch_az_d10", None)
    if latch is not None:
        try:
            td = int(getattr(axis, "target_d10", 0))
        except Exception:
            td = 0
        try:
            latch_ts = float(getattr(mw, "_web_cc_latch_az_ts", 0.0) or 0.0)
        except Exception:
            latch_ts = 0.0
        hold = False
        try:
            hold = bool(mw.ctrl.cc_poll_hold_active(now))
        except Exception:
            hold = bool(getattr(mw.ctrl, "_setposcc_poll_hold", False))
        recent = (now - latch_ts) < _WEB_CC_LATCH_KEEP_S
        caught_up = abs(td - int(latch)) <= 2
        if moving or hold or (recent and not caught_up):
            return int(latch)
        mw._web_cc_latch_az_d10 = None
        mw._web_cc_latch_az_ts = 0.0
    try:
        return int(getattr(axis, "target_d10", 0))
    except Exception:
        return 0


def web_apply_set_az_display(mw: Any, deg: float, *, exact: bool) -> None:
    off = mw._get_antenna_offset_az()
    ui = mw.cfg.get("ui", {}) or {}
    antenna_idx = max(0, min(2, int(ui.get("compass_antenna", 0))))
    dipole = mw._antenna_dipole_enabled(antenna_idx)
    cur_rotor = raw_rotor_az_deg_from_axis(getattr(mw.ctrl, "az", None))
    if exact:
        display = float(deg)
        rotor_deg = wrap_deg(display - float(off))
    else:
        display = wrap_deg(deg)
        rotor_deg = rotor_az_for_display_bearing(
            display,
            off,
            cur_rotor,
            dipole=dipole,
            last_rotor_az=getattr(mw.ctrl, "az_dipole_last_rotor_az", None) if dipole else None,
            max_deg=float(az_max_d10_from_axis(getattr(mw.ctrl, "az", None))) / 10.0,
        )
    try:
        if dipole:
            mw.ctrl.az_dipole_display_bearing = wrap_deg(display)
            mw.ctrl.az_dipole_last_rotor_az = rotor_deg
        else:
            mw.ctrl.az_dipole_display_bearing = None
            mw.ctrl.az_dipole_last_rotor_az = None
    except Exception:
        pass
    mw._web_cc_latch_az_d10 = None
    mw._web_cc_latch_az_ts = 0.0
    mw.ctrl.set_az_deg(rotor_deg, force=True)


def web_aswatch_enabled(mw: Any) -> bool:
    """Wie Desktop-Kompass: OM-Radar nur bei aktivem AirScout/KST (aswatch)."""
    return bool((mw.cfg.get("ui", {}) or {}).get("aswatch_udp_enabled", False))


def web_heatmap_az_modes(mw: Any) -> list[str]:
    ui = mw.cfg.get("ui", {}) or {}
    raw = ui.get("compass_heatmap_az_modes")
    modes: list[str] = []
    if isinstance(raw, list):
        for m in raw:
            s = str(m).lower()
            if s in ("strom", "om_radar", "dwell") and s not in modes:
                modes.append(s)
    else:
        old = str(ui.get("compass_heatmap_az", "off")).lower()
        if old in ("strom", "om_radar", "dwell"):
            modes = [old]
    if not web_aswatch_enabled(mw):
        modes = [m for m in modes if m != "om_radar"]
    return modes[:2]


def web_om_radar_counts(mw: Any, opening: float, range_km: float) -> list[float]:
    ui = mw.cfg.get("ui", {}) or {}
    try:
        n = int(ui.get("compass_om_radar_sectors", 20))
    except (TypeError, ValueError):
        n = 20
    n = max(10, min(100, n))
    counts = [0.0] * n
    lat0, lon0 = effective_station_lat_lon(ui)
    op = max(1.0, float(opening or 30.0))
    R = max(1.0, min(4000.0, float(range_km or 100.0)))
    for m in getattr(mw, "_aswatch_last", None) or []:
        if not isinstance(m, dict):
            continue
        try:
            lat = float(m.get("lat"))
            lon = float(m.get("lon"))
        except (TypeError, ValueError):
            continue
        if haversine_km(lat0, lon0, lat, lon) > R:
            continue
        b = bearing_deg(lat0, lon0, lat, lon)
        frac = om_beam_contributions_per_sector(b, op, n)
        for j in range(n):
            counts[j] += float(frac[j])
    return counts


def _web_pwm_fields(axis_state: Any, *, enabled_axis: bool) -> dict:
    """Motorspeed wie Main-GUI: Min aus Telemetrie, aktueller PWM, nur online+Min bedienbar."""
    if not enabled_axis or axis_state is None:
        return {"pwm": 0, "pwm_min": 0, "pwm_enabled": False}
    online = bool(getattr(axis_state, "online", False))
    tel = getattr(axis_state, "telemetry", None)
    pwm_min = None
    pwm = None
    if tel is not None:
        try:
            if getattr(tel, "pwm_min_pct", None) is not None:
                pwm_min = max(0, min(100, int(math.ceil(float(tel.pwm_min_pct)))))
        except (TypeError, ValueError):
            pwm_min = None
        try:
            if getattr(tel, "pwm_max_pct", None) is not None:
                v = float(tel.pwm_max_pct)
                pwm = 100 if v >= 99.5 else int(round(v))
        except (TypeError, ValueError):
            pwm = None
    if not online:
        return {"pwm": 0, "pwm_min": pwm_min if pwm_min is not None else 0, "pwm_enabled": False}
    return {
        "pwm": pwm,
        "pwm_min": pwm_min if pwm_min is not None else 0,
        "pwm_enabled": pwm_min is not None,
    }


def web_tick_dwell(mw: Any, rotor_deg: float | None, moving: bool, ant_idx: int) -> None:
    ui = mw.cfg.get("ui", {}) or {}
    try:
        n = int(ui.get("compass_dwell_sectors", 20))
    except (TypeError, ValueError):
        n = 20
    n = max(10, min(100, n))
    arrs = mw._web_dwell_az_seconds_per_ant
    for i in range(3):
        if len(arrs[i]) != n:
            arrs[i] = [0.0] * n
    mono = time.monotonic()
    prev = getattr(mw, "_web_dwell_prev_mono", None)
    if prev is None:
        mw._web_dwell_prev_mono = mono
        return
    dt = max(0.0, float(mono - prev))
    mw._web_dwell_prev_mono = mono
    if moving or rotor_deg is None or not getattr(mw, "_web_compass_open", False):
        return
    idx = int((wrap_deg(rotor_deg) / 360.0) * n) % n
    ant_i = max(0, min(2, ant_idx))
    arrs[ant_i][idx] += dt
    if mw._antenna_dipole_enabled(ant_i):
        opp = int((wrap_deg(rotor_deg + 180.0) / 360.0) * n) % n
        arrs[ant_i][opp] += dt


def get_web_compass_payload(mw: Any) -> dict:
    ui = mw.cfg.get("ui", {}) or {}
    params = mw._get_params()
    ant_idx = max(0, min(2, int(ui.get("compass_antenna", 0))))
    off = mw._get_antenna_offset_az()
    dipole = mw._antenna_dipole_enabled(ant_idx)
    max_d10 = az_max_d10_from_axis(getattr(mw.ctrl, "az", None))
    now = time.time()
    ist = None
    rotor_raw = None
    try:
        pos_d10 = int(mw.ctrl.az.pos_d10)
        rotor_raw = az_pos_deg_from_d10(pos_d10, max_d10=max_d10)
        smooth = az_pos_deg_from_d10(
            pos_d10,
            float(mw.ctrl.az.get_smoothed_pos_d10f(now)),
            max_d10=max_d10,
        )
        ist = wrap_deg(float(smooth) + float(off))
    except Exception:
        pass
    soll = None
    try:
        td = web_effective_az_target_d10(mw)
        rotor_t = az_pos_deg_from_d10(td, max_d10=max_d10)
        soll = wrap_deg(float(rotor_t) + float(off))
        if (
            not bool(getattr(mw.ctrl.az, "referenced", False))
            and getattr(mw.ctrl.az, "compass_target_d10", None) is None
            and getattr(mw, "_web_cc_latch_az_d10", None) is None
            and getattr(mw.ctrl.az, "last_set_sent_target_d10", None) is None
            and td == 0
            and not bool(getattr(mw.ctrl.az, "moving", False))
        ):
            soll = ist
    except Exception:
        pass

    moving = bool(getattr(mw.ctrl.az, "moving", False))
    web_tick_dwell(mw, rotor_raw, moving, ant_idx)
    web_tick_scan(mw)

    wind_on = (
        bool(getattr(mw.ctrl, "wind_enabled", False))
        if getattr(mw.ctrl, "wind_enabled_known", False)
        else False
    )
    wdir = None
    wkmh = None
    try:
        tel = getattr(mw.ctrl.az, "telemetry", None)
        if tel is not None:
            wdir = getattr(tel, "wind_dir_deg", None)
            wkmh = getattr(tel, "wind_kmh", None)
            if not wind_on and (wdir is not None or wkmh is not None):
                wind_on = True
    except Exception:
        pass
    mode = str(ui.get("wind_dir_display", "to") or "to").strip().lower()
    if mode not in ("from", "to"):
        mode = "to"

    modes = web_heatmap_az_modes(mw)
    opening = float(params.get("opening", 30.0) or 30.0)
    range_km = float(params.get("range_km", 100.0) or 100.0)
    try:
        dwell_full = float(ui.get("compass_dwell_full_minutes", 5.0)) * 60.0
    except (TypeError, ValueError):
        dwell_full = 300.0

    bins_cw = list(getattr(mw.ctrl.az, "acc_bins_cw", None) or []) or None
    bins_ccw = list(getattr(mw.ctrl.az, "acc_bins_ccw", None) or []) or None
    az_scale = parse_heatmap_scale(ui, "az")
    om_counts = web_om_radar_counts(mw, opening, range_km) if "om_radar" in modes else []
    dwell = list(mw._web_dwell_az_seconds_per_ant[ant_idx]) if "dwell" in modes else []

    stroke = ANTENNA_BEAM_COLORS[ant_idx][0]
    try:
        beam_rgba = QColor(stroke)
        beam_color = f"rgba({beam_rgba.red()},{beam_rgba.green()},{beam_rgba.blue()},0.28)"
    except Exception:
        beam_color = "rgba(80,160,255,0.28)"

    hide_homing = bool(getattr(mw.ctrl, "abs_encoder_no_homing", lambda: False)())
    enable_el = bool(getattr(mw.ctrl, "enable_el", False))
    try:
        el_max = float(mw._el_max_deg())
    except Exception:
        el_max = float(el_max_deg_from_rotor_type(getattr(mw.ctrl, "el_rotor_type", None)))

    el_ist = None
    el_soll = None
    if enable_el:
        try:
            el_ist = clamp_el(float(getattr(mw.ctrl.el, "pos_d10", 0)) / 10.0, el_max)
        except Exception:
            el_ist = None
        try:
            el_soll = clamp_el(float(getattr(mw.ctrl.el, "target_d10", 0)) / 10.0, el_max)
        except Exception:
            el_soll = None

    el_heat = str(ui.get("compass_heatmap_el", "off") or "off").lower()
    el_bins_cw = (
        list(getattr(mw.ctrl.el, "acc_bins_cw", None) or []) or None if enable_el else None
    )
    el_bins_ccw = (
        list(getattr(mw.ctrl.el, "acc_bins_ccw", None) or []) or None if enable_el else None
    )
    el_scale = parse_heatmap_scale(ui, "el") if enable_el else None

    dgcal_az = None
    try:
        v = getattr(mw.ctrl.az, "dgcal_d10", None)
        if v is not None:
            dgcal_az = float(v) / 10.0
    except Exception:
        pass

    favs = mw._get_favorites()
    favs_sorted = sorted(
        favs,
        key=lambda f: (
            0 if f["name"] and f["name"][0].isdigit() else 1,
            f["name"].lower(),
        ),
    )
    antennas = mw._get_antenna_dropdown_items()

    chrome: dict = {}
    try:
        chrome = mw.get_web_chrome_state()
    except Exception:
        chrome = {}
    labels = dict((chrome or {}).get("labels") or {})
    labels.update(
        {
            "back_to_map": t("map.web_back_to_map"),
            "open_compass": t("map.web_open_compass"),
            "ist": t("compass.ist_label"),
            "ist_reverse": t("compass.ist_reverse_label"),
            "soll": t("compass.soll_label"),
            "wind": t("compass.wind_label"),
            "connection": t("compass.status_verbindung"),
            "control": t("compass.steuerung_label"),
            "fav_header": t("compass.fav_header"),
            "antenna": t("compass.antenna_header"),
            "stop_az": t("compass.btn_stop_az"),
            "ref_az": t("compass.btn_ref_az"),
            "stop_el": t("compass.btn_stop_el"),
            "ref_el": t("compass.btn_ref_el"),
            "heatmap_strom": t("compass.heatmap_strom"),
            "heatmap_om": t("compass.heatmap_om_radar"),
            "heatmap_dwell": t("compass.heatmap_dwell"),
            "motorspeed": t("axis.motorspeed_label"),
            "dwell_reset": t("compass.btn_reset_dwell"),
            "scan": t("compass.scan_header"),
            "scan_start": t("compass.scan_btn_start"),
            "scan_a": t("compass.scan_a_placeholder"),
            "scan_b": t("compass.scan_b_placeholder"),
            "dgcal": t("compass.dgcal_header"),
            "dgcal_save": t("compass.dgcal_btn_save"),
            "dgcal_clear": t("compass.dgcal_btn_clear"),
            "ort": t("map.ort_label"),
            "locator": t("map.locator_label"),
            "locator_ph": t("compass.locator_placeholder"),
            "locator_invalid_title": t("compass.locator_invalid_title"),
            "locator_invalid_body": t("compass.locator_invalid_body"),
            "dialog_ok": "OK",
            "search_ph": t("map.search_placeholder"),
            "search_pick_title": t("map.search_pick_title"),
            "search_pick_body": t("map.search_pick_body"),
            "search_cancel": t("map.search_cancel"),
            "search_searching": t("map.search_searching"),
            "search_not_found": t("map.search_not_found_body"),
            "search_error": t("map.search_error_title"),
        }
    )

    return {
        "enable_el": enable_el,
        "ref_visible": not hide_homing,
        "antenna_idx": ant_idx,
        "antennas": antennas,
        "favorites": [
            {"name": f.get("name", ""), "az": float(f.get("az", 0) or 0)} for f in favs_sorted
        ],
        "om_available": web_aswatch_enabled(mw),
        "labels": labels,
        "az": {
            "ist": ist,
            "soll": soll,
            "moving": moving,
            "online": bool(getattr(mw.ctrl.az, "online", False)),
            "referenced": bool(getattr(mw.ctrl.az, "referenced", False)),
            "ref_blink": bool(getattr(mw.ctrl.az, "ref_poll_active", False)),
            "offset_deg": float(off),
            "dipole": bool(dipole),
            "opening_deg": opening,
            "beam_overlay": bool(ui.get("compass_antenna_overlay", True)),
            "beam_color": beam_color,
            "heatmap_modes": modes,
            "bins_cw": bins_cw,
            "bins_ccw": bins_ccw,
            "heatmap_scale": list(az_scale) if az_scale is not None else None,
            "om_counts": om_counts,
            "dwell_seconds": dwell,
            "dwell_full": dwell_full,
            "wind_visible": bool(wind_on),
            "wind_dir": wdir,
            "wind_kmh": wkmh,
            "wind_mode": mode,
            "dgcal": dgcal_az,
            **_web_pwm_fields(getattr(mw.ctrl, "az", None), enabled_axis=True),
        },
        "el": {
            "ist": el_ist,
            "soll": el_soll,
            "max_deg": el_max,
            "moving": bool(getattr(mw.ctrl.el, "moving", False)) if enable_el else False,
            "online": bool(getattr(mw.ctrl.el, "online", False)) if enable_el else False,
            "referenced": bool(getattr(mw.ctrl.el, "referenced", False)) if enable_el else False,
            "ref_blink": bool(getattr(mw.ctrl.el, "ref_poll_active", False)) if enable_el else False,
            "heatmap_mode": el_heat if el_heat in ("strom", "off") else "off",
            "bins_cw": el_bins_cw,
            "bins_ccw": el_bins_ccw,
            "heatmap_scale": list(el_scale) if el_scale is not None else None,
            **_web_pwm_fields(getattr(mw.ctrl, "el", None), enabled_axis=enable_el),
        },
    }


def get_web_compass_html(mw: Any) -> str:
    dark = bool(mw.cfg.get("ui", {}).get("force_dark_mode", True))
    try:
        init = get_web_compass_payload(mw)
    except Exception:
        init = {}
    try:
        chrome = mw.get_web_chrome_state()
    except Exception:
        chrome = {}
    return build_compass_html({"dark_mode": dark, "compass_init": init, "web_chrome": chrome})


def handle_web_compass_action(mw: Any, act: str, data: dict) -> bool:
    """Kompass-spezifische UI-Aktionen. True wenn behandelt."""
    if act == "compass_open":
        mw._web_compass_open = True
        if hasattr(mw.ctrl, "set_compass_window_open"):
            mw.ctrl.set_compass_window_open(True, source="web")
        modes = web_heatmap_az_modes(mw)
        el_mode = str((mw.cfg.get("ui", {}) or {}).get("compass_heatmap_el", "off")).lower()
        if hasattr(mw.ctrl, "set_compass_strom_heatmap_active"):
            mw.ctrl.set_compass_strom_heatmap_active(
                "strom" in modes, el_mode == "strom"
            )
        if hasattr(mw.ctrl, "request_immediate_pos"):
            try:
                mw.ctrl.request_immediate_pos()
            except Exception:
                pass
        # Stromring-Bins (GETACCBINS) auch ohne Desktop-Kompassfenster anfordern.
        if "strom" in modes or el_mode == "strom":
            if hasattr(mw.ctrl, "request_immediate_stats"):
                try:
                    mw.ctrl.request_immediate_stats()
                except Exception:
                    pass
        return True
    if act == "compass_close":
        mw._web_compass_open = False
        mw._web_scan_active = False
        if hasattr(mw.ctrl, "set_compass_window_open"):
            mw.ctrl.set_compass_window_open(False, source="web")
        # Desktop-Kompass kann noch offen sein → Flags aus dessen UI/cfg.
        if bool(getattr(mw.ctrl, "_compass_window_open", False)):
            modes = web_heatmap_az_modes(mw)
            el_mode = str((mw.cfg.get("ui", {}) or {}).get("compass_heatmap_el", "off")).lower()
            if hasattr(mw.ctrl, "set_compass_strom_heatmap_active"):
                try:
                    mw.ctrl.set_compass_strom_heatmap_active(
                        "strom" in modes, el_mode == "strom"
                    )
                except Exception:
                    pass
        return True
    if act == "set_az":
        mw._web_scan_active = False
        deg = float(data.get("deg"))
        web_apply_set_az_display(mw, deg, exact=bool(data.get("exact")))
        return True
    if act == "set_el":
        if not bool(getattr(mw.ctrl, "enable_el", False)):
            return True
        deg = float(data.get("deg"))
        try:
            max_deg = float(mw._el_max_deg())
        except Exception:
            max_deg = 90.0
        mw.ctrl.set_el_deg(clamp_el(deg, max_deg), force=True)
        return True
    if act == "set_pwm":
        axis = str(data.get("axis") or "az").strip().lower()
        if axis not in ("az", "el"):
            return True
        if axis == "el" and not bool(getattr(mw.ctrl, "enable_el", False)):
            return True
        try:
            pct = float(data.get("pct"))
        except (TypeError, ValueError):
            return True
        pct = max(0.0, min(100.0, pct))
        axis_state = getattr(mw.ctrl, axis, None)
        tel = getattr(axis_state, "telemetry", None) if axis_state is not None else None
        try:
            mn = getattr(tel, "pwm_min_pct", None) if tel is not None else None
            if mn is not None:
                pct = max(float(mn), pct)
        except (TypeError, ValueError):
            pass
        try:
            if axis == "az":
                mw.ctrl.set_pwm_az(pct)
            else:
                mw.ctrl.set_pwm_el(pct)
        except Exception:
            pass
        return True
    if act == "stop_az":
        mw._web_scan_active = False
        try:
            pos = float(az_pos_deg_from_d10(int(mw.ctrl.az.pos_d10)))
            mw.ctrl.set_az_deg(pos, force=True)
        except Exception:
            pass
        return True
    if act == "stop_el":
        if bool(getattr(mw.ctrl, "enable_el", False)):
            try:
                pos = float(getattr(mw.ctrl.el, "pos_d10", 0)) / 10.0
                mw.ctrl.set_el_deg(pos, force=True)
            except Exception:
                pass
        return True
    if act == "ref_az":
        try:
            mw.ctrl.reference_az(True)
        except Exception:
            pass
        return True
    if act == "ref_el":
        try:
            mw.ctrl.reference_el(True)
        except Exception:
            pass
        return True
    if act == "heatmap_az_modes":
        modes = [str(m).lower() for m in (data.get("modes") or []) if str(m).lower() in ("strom", "om_radar", "dwell")]
        if not web_aswatch_enabled(mw):
            modes = [m for m in modes if m != "om_radar"]
        modes = modes[:2]
        ui = mw.cfg.setdefault("ui", {})
        ui["compass_heatmap_az_modes"] = modes
        ui["compass_heatmap_az"] = modes[0] if modes else "off"
        el_mode = str(ui.get("compass_heatmap_el", "off")).lower()
        if hasattr(mw.ctrl, "set_compass_strom_heatmap_active"):
            mw.ctrl.set_compass_strom_heatmap_active("strom" in modes, el_mode == "strom")
        if "strom" in modes and hasattr(mw.ctrl, "request_immediate_stats"):
            try:
                mw.ctrl.request_immediate_stats()
            except Exception:
                pass
        if mw.save_cfg_cb:
            try:
                mw.save_cfg_cb(mw.cfg)
            except Exception:
                pass
        return True
    if act == "heatmap_el":
        mode = str(data.get("mode") or "off").lower()
        if mode not in ("strom", "off"):
            mode = "off"
        ui = mw.cfg.setdefault("ui", {})
        ui["compass_heatmap_el"] = mode
        modes = web_heatmap_az_modes(mw)
        if hasattr(mw.ctrl, "set_compass_strom_heatmap_active"):
            mw.ctrl.set_compass_strom_heatmap_active("strom" in modes, mode == "strom")
        if mode == "strom" and hasattr(mw.ctrl, "request_immediate_stats"):
            try:
                mw.ctrl.request_immediate_stats()
            except Exception:
                pass
        if mw.save_cfg_cb:
            try:
                mw.save_cfg_cb(mw.cfg)
            except Exception:
                pass
        return True
    if act == "dwell_reset":
        ui = mw.cfg.get("ui", {}) or {}
        try:
            n = int(ui.get("compass_dwell_sectors", 20))
        except (TypeError, ValueError):
            n = 20
        n = max(10, min(100, n))
        mw._web_dwell_az_seconds_per_ant = [[0.0] * n, [0.0] * n, [0.0] * n]
        return True
    if act == "az_scan":
        try:
            a = float(data.get("a"))
            b = float(data.get("b"))
        except (TypeError, ValueError):
            return True
        if abs(a - b) < 0.05:
            return True
        mw._web_scan_a = a
        mw._web_scan_b = b
        mw._web_scan_next_is_b = True
        mw._web_scan_active = True
        web_scan_goto(mw, a)
        return True
    if act == "dgcal_az":
        # Vereinfacht: Peilwinkel → SETDGCAL über Controller falls vorhanden
        if data.get("clear"):
            try:
                if hasattr(mw.ctrl, "set_dgcal_az"):
                    mw.ctrl.set_dgcal_az(0.0)
                elif hasattr(mw.ctrl, "send_dgcal"):
                    mw.ctrl.send_dgcal(0)
            except Exception:
                pass
            return True
        try:
            true_deg = float(data.get("value"))
            ist = None
            try:
                off = mw._get_antenna_offset_az()
                pos = az_pos_deg_from_d10(int(mw.ctrl.az.pos_d10))
                ist = wrap_deg(float(pos) + float(off))
            except Exception:
                pass
            if ist is None:
                return True
            delta = true_deg - ist
            cur = 0.0
            try:
                v = getattr(mw.ctrl.az, "dgcal_d10", None)
                if v is not None:
                    cur = float(v) / 10.0
            except Exception:
                pass
            new_v = cur + delta
            if hasattr(mw.ctrl, "set_dgcal_az"):
                mw.ctrl.set_dgcal_az(new_v)
            elif hasattr(mw.ctrl, "send_setdgcal"):
                mw.ctrl.send_setdgcal(int(round(new_v * 10)))
        except Exception:
            pass
        return True
    if act == "dgcal_el":
        if data.get("clear"):
            try:
                if hasattr(mw.ctrl, "set_dgcal_el"):
                    mw.ctrl.set_dgcal_el(0.0)
            except Exception:
                pass
            return True
        try:
            true_deg = float(data.get("value"))
            ist = float(getattr(mw.ctrl.el, "pos_d10", 0)) / 10.0
            cur = 0.0
            v = getattr(mw.ctrl.el, "dgcal_d10", None)
            if v is not None:
                cur = float(v) / 10.0
            new_v = cur + (true_deg - ist)
            if hasattr(mw.ctrl, "set_dgcal_el"):
                mw.ctrl.set_dgcal_el(new_v)
        except Exception:
            pass
        return True
    if act == "place_pick":
        try:
            lat = float(data.get("lat"))
            lon = float(data.get("lon"))
            mw._on_map_click(lat, lon, asnearest_dest=None)
        except Exception:
            pass
        return True
    return False
