"""Live-Versionscheck gegen gepinnte Firmware aus version.py."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, List, Optional

from .version import (
    CONTROLLER_FW_VERSION,
    INCLINOMETER_FW_VERSION,
    ROTOR_FW_VERSION,
    WLAN_MODULE_FW_VERSION,
)

_VER_RE = re.compile(r"(\d+)\.(\d+)(?:\.(\d+))?")


def normalize_version(raw: Optional[str]) -> str:
    """Erste SemVer-ähnliche Zahl aus Antwort extrahieren."""
    s = str(raw or "").strip()
    if not s:
        return ""
    # erster Token vor ; oder Leerzeichen
    token = s.split(";")[0].strip().split()[0] if s else ""
    m = _VER_RE.search(token) or _VER_RE.search(s)
    if not m:
        return token.lstrip("vV")
    major, minor, patch = m.group(1), m.group(2), m.group(3) or "0"
    return f"{int(major)}.{int(minor)}.{int(patch)}"


def version_tuple(raw: Optional[str]) -> Optional[tuple[int, int, int]]:
    """SemVer als (major, minor, patch) oder None."""
    n = normalize_version(raw)
    if not n:
        return None
    m = _VER_RE.match(n)
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0))


def versions_equal(a: Optional[str], b: Optional[str]) -> bool:
    ta, tb = version_tuple(a), version_tuple(b)
    if ta is None or tb is None:
        return False
    return ta == tb


def version_at_least(found: Optional[str], minimum: Optional[str]) -> bool:
    """True wenn found >= minimum (neuere Firmware ist OK)."""
    tf, tm = version_tuple(found), version_tuple(minimum)
    if tf is None or tm is None:
        return False
    return tf >= tm


@dataclass
class AxisVersionResult:
    axis: str
    expected: str
    found: Optional[str]
    ok: bool
    offline: bool = False
    query_failed: bool = False


@dataclass
class CompatCheckResult:
    rotor_ok: bool = True
    rotor_axes: List[AxisVersionResult] = field(default_factory=list)
    controller_found: Optional[str] = None
    controller_expected: str = CONTROLLER_FW_VERSION
    controller_ok: Optional[bool] = None  # None = nicht geprüft
    inclinometer_found: Optional[str] = None
    inclinometer_expected: str = INCLINOMETER_FW_VERSION
    inclinometer_ok: Optional[bool] = None
    wlan_note: Optional[str] = None  # nur Info, oft keine Bus-Version
    info_lines: List[str] = field(default_factory=list)


def _sync_get(ctrl: Any, dst: int, cmd: str, expect: str, *, timeout_s: float = 1.5) -> Optional[str]:
    if ctrl is None or not hasattr(ctrl, "sync_ui_command_response"):
        return None
    try:
        r = ctrl.sync_ui_command_response(
            int(dst), str(cmd), "0", str(expect), timeout_s=float(timeout_s)
        )
    except Exception:
        return None
    if r is None:
        return None
    s = str(r).strip()
    if not s or s.upper().startswith("NAK"):
        return None
    return s.split(";")[0].strip() or None


def _bus_connected(ctrl: Any) -> bool:
    try:
        hw = getattr(ctrl, "hw", None)
        if hw is not None and hasattr(hw, "is_connected"):
            return bool(hw.is_connected())
    except Exception:
        pass
    return False


def _has_dk8de_wlan_module(cfg: Optional[dict]) -> bool:
    cfg = cfg if isinstance(cfg, dict) else {}
    mods = cfg.get("network_modules")
    if not isinstance(mods, list):
        return False
    for m in mods:
        if not isinstance(m, dict):
            continue
        vendor = str(m.get("vendor") or "").strip().lower()
        if vendor in ("dk8de_wlan", "dk8de", "wlan_to_rs485"):
            return True
        name = str(m.get("name") or "").lower()
        if "wlan" in name and "rs485" in name:
            return True
    return False


def _inclinometer_configured(ctrl: Any, cfg: Optional[dict]) -> bool:
    """Encoder-Typ 4 = Inklinometer."""
    try:
        et = getattr(ctrl, "encoder_type", None)
        if et is not None and int(et) == 4:
            return True
    except Exception:
        pass
    cfg = cfg if isinstance(cfg, dict) else {}
    ui = cfg.get("ui") if isinstance(cfg.get("ui"), dict) else {}
    try:
        if int(ui.get("encoder_type", 0) or 0) == 4:
            return True
    except Exception:
        pass
    return False


def run_compat_check(ctrl: Any, cfg: Optional[dict] = None) -> CompatCheckResult:
    """Synchron am Bus abfragen (UI-Thread: kurz halten / nach Connect verzögern)."""
    out = CompatCheckResult()
    cfg = cfg if isinstance(cfg, dict) else {}
    if ctrl is None or not _bus_connected(ctrl):
        out.rotor_ok = False
        out.info_lines.append("bus_offline")
        return out

    rb = cfg.get("rotor_bus") if isinstance(cfg.get("rotor_bus"), dict) else {}
    enable_az = bool(getattr(ctrl, "enable_az", rb.get("enable_az", True)))
    enable_el = bool(getattr(ctrl, "enable_el", rb.get("enable_el", False)))

    rotor_results: List[AxisVersionResult] = []

    def _check_axis(axis: str, enable: bool, slave_attr: str, cfg_key: str, default_id: int) -> None:
        if not enable:
            return
        st = getattr(ctrl, axis.lower(), None)
        online = bool(getattr(st, "online", False)) if st is not None else False
        try:
            dst = int(getattr(ctrl, slave_attr, rb.get(cfg_key, default_id)) or default_id)
        except Exception:
            dst = default_id
        if dst <= 0:
            return
        if not online:
            # Offline: kein Hinweis, nicht in die Pflicht-Prüfung aufnehmen.
            rotor_results.append(
                AxisVersionResult(
                    axis=axis,
                    expected=ROTOR_FW_VERSION,
                    found=None,
                    ok=True,
                    offline=True,
                )
            )
            return
        raw = _sync_get(ctrl, dst, "GETVERSION", "ACK_GETVERSION")
        found = normalize_version(raw) if raw else None
        # Neuere Rotor-FW als Pin ist OK — Hinweis nur wenn älter.
        # Online aber keine lesbare Version → wie „passt nicht“ behandeln.
        ok = bool(found) and version_at_least(found, ROTOR_FW_VERSION)
        rotor_results.append(
            AxisVersionResult(
                axis=axis,
                expected=ROTOR_FW_VERSION,
                found=found,
                ok=ok,
                query_failed=raw is None,
            )
        )

    _check_axis("AZ", enable_az, "slave_az", "slave_az", 20)
    _check_axis("EL", enable_el, "slave_el", "slave_el", 21)
    out.rotor_axes = rotor_results
    # Pflicht-Hinweis nur für online geprüfte Achsen (offline ignorieren).
    checked = [r for r in rotor_results if not r.offline]
    if checked:
        out.rotor_ok = all(r.ok for r in checked)
    else:
        out.rotor_ok = True  # keine online Achse — kein Dialog

    # Controller (optional Info)
    chw = cfg.get("controller_hw") if isinstance(cfg.get("controller_hw"), dict) else {}
    if bool(chw.get("enabled", True)):
        try:
            cont_id = int(chw.get("cont_id", 2) or 2)
        except Exception:
            cont_id = 2
        if cont_id > 0:
            raw = _sync_get(ctrl, cont_id, "GETCOVERSION", "ACK_GETCOVERSION")
            if raw:
                found = normalize_version(raw)
                out.controller_found = found
                out.controller_ok = versions_equal(found, CONTROLLER_FW_VERSION)
                if out.controller_ok is False:
                    out.info_lines.append(
                        f"controller:{found}:{CONTROLLER_FW_VERSION}"
                    )

    # Inklinometer nur wenn Typ 4 — GETVERSION am AZ (Sensor-FW oft über Rotor)
    if _inclinometer_configured(ctrl, cfg):
        # Kein separates GETINCVERSION in Bridge — Info-Hinweis mit Pin
        out.inclinometer_ok = None
        out.info_lines.append(f"inclinometer_pin:{INCLINOMETER_FW_VERSION}")

    if _has_dk8de_wlan_module(cfg):
        out.wlan_note = WLAN_MODULE_FW_VERSION
        out.info_lines.append(f"wlan_pin:{WLAN_MODULE_FW_VERSION}")

    return out
