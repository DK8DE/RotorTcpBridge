"""EasyComm II / III (Kern für Satelliten-Clients)."""

from __future__ import annotations

import re
from typing import Optional

from .angle_utils import az_d10_for_external_report

_RE_AZ = re.compile(r"^AZ\s*([+-]?\d+(?:\.\d*)?)?\s*$", re.IGNORECASE)
_RE_EL = re.compile(r"^EL\s*([+-]?\d+(?:\.\d*)?)?\s*$", re.IGNORECASE)
_RE_VEL = re.compile(r"^(VL|VR|VU|VD)\s*([+-]?\d+(?:\.\d*)?)?\s*$", re.IGNORECASE)
_RE_CR = re.compile(r"^CR\s*(\d+)\s*$", re.IGNORECASE)
_RE_CW = re.compile(r"^CW\s*(\d+)\s*,\s*([+-]?\d+(?:\.\d*)?)\s*$", re.IGNORECASE)

_VERSION = "VERotorTcpBridge"

# Frequenz-/Radio-Tags und AOS/LOS: still akzeptieren
_SILENT_TAGS = frozenset(
    {
        "UP",
        "DN",
        "UM",
        "DM",
        "UL",
        "DL",
        "VM",
        "AO",
        "LO",
        "OP",
        "IP",
        "AN",
    }
)


def _az_deg(ctrl, *, shortest: bool, report_mod360: bool) -> float:
    try:
        raw = int(getattr(ctrl.az, "pos_d10", 0) or 0) if getattr(ctrl, "enable_az", True) else 0
        d10 = az_d10_for_external_report(raw, shortest_path=shortest, report_mod360=report_mod360)
        return float(d10) / 10.0
    except Exception:
        return 0.0


def _el_deg(ctrl) -> float:
    try:
        if not getattr(ctrl, "enable_el", False):
            return 0.0
        return float(int(getattr(ctrl.el, "pos_d10", 0) or 0)) / 10.0
    except Exception:
        return 0.0


def _fmt_az(deg: float) -> str:
    return f"AZ{float(deg):.1f}"


def _fmt_el(deg: float) -> str:
    return f"EL{float(deg):.1f}"


def _is_moving(ctrl) -> bool:
    try:
        if getattr(ctrl, "enable_az", True) and bool(getattr(ctrl.az, "moving", False)):
            return True
        if getattr(ctrl, "enable_el", False) and bool(getattr(ctrl.el, "moving", False)):
            return True
    except Exception:
        pass
    return False


def _jog_az(ctrl, delta: float, *, shortest: bool) -> None:
    if not getattr(ctrl, "enable_az", True):
        return
    cur = float(int(getattr(ctrl.az, "pos_d10", 0) or 0)) / 10.0
    ctrl.set_az_from_spid(int(round((cur + delta) * 10.0)), shortest_path=bool(shortest))


def _jog_el(ctrl, delta: float) -> None:
    if not getattr(ctrl, "enable_el", False):
        return
    cur = float(int(getattr(ctrl.el, "pos_d10", 0) or 0)) / 10.0
    ctrl.set_el_from_spid(int(round(max(0.0, min(180.0, cur + delta)) * 10.0)))


def process_easycomm_line(
    line: str,
    ctrl,
    *,
    shortest_path: bool = False,
    report_mod360: bool = False,
) -> tuple[Optional[str], bool]:
    """Eine EasyComm-II/III-Zeile. Mehrere Tokens (Leerzeichen) möglich."""
    raw = (line or "").strip()
    if not raw:
        return (None, False)

    parts = raw.split()
    replies: list[str] = []
    i = 0
    while i < len(parts):
        token = parts[i]
        up = token.upper()
        nxt = parts[i + 1] if i + 1 < len(parts) else None

        # AZ / EL mit optionalem Folgewert: "AZ 123.4"
        if up == "AZ" and nxt is not None:
            try:
                float(nxt)
                token = f"AZ{nxt}"
                i += 1
            except ValueError:
                pass
        elif up == "EL" and nxt is not None:
            try:
                float(nxt)
                token = f"EL{nxt}"
                i += 1
            except ValueError:
                pass

        m_az = _RE_AZ.match(token)
        if m_az:
            val = m_az.group(1)
            if val is None or val == "":
                replies.append(
                    _fmt_az(_az_deg(ctrl, shortest=shortest_path, report_mod360=report_mod360))
                )
            else:
                try:
                    if getattr(ctrl, "enable_az", True):
                        ctrl.set_az_from_spid(
                            int(round(float(val) * 10.0)),
                            shortest_path=bool(shortest_path),
                        )
                except Exception:
                    pass
            i += 1
            continue

        m_el = _RE_EL.match(token)
        if m_el:
            val = m_el.group(1)
            if val is None or val == "":
                replies.append(_fmt_el(_el_deg(ctrl)))
            else:
                try:
                    if getattr(ctrl, "enable_el", False):
                        ctrl.set_el_from_spid(int(round(float(val) * 10.0)))
                except Exception:
                    pass
            i += 1
            continue

        if up in ("SA", "SE", "S"):
            try:
                if up == "SA" and getattr(ctrl, "enable_az", True):
                    ctrl.hold_az_at_current_pos()
                elif up == "SE" and getattr(ctrl, "enable_el", False):
                    ctrl.hold_el_at_current_pos()
                else:
                    ctrl.hold_all_at_current_pos()
            except Exception:
                pass
            i += 1
            continue

        if up in ("ML", "MR", "MU", "MD"):
            try:
                if up == "ML":
                    _jog_az(ctrl, -3.0, shortest=shortest_path)
                elif up == "MR":
                    _jog_az(ctrl, 3.0, shortest=shortest_path)
                elif up == "MU":
                    _jog_el(ctrl, 3.0)
                elif up == "MD":
                    _jog_el(ctrl, -3.0)
            except Exception:
                pass
            i += 1
            continue

        # EasyComm III Velocity → sanfter Jog
        m_vel = _RE_VEL.match(token)
        if m_vel:
            kind = m_vel.group(1).upper()
            try:
                speed = float(m_vel.group(2) or "1")
            except ValueError:
                speed = 1.0
            if abs(speed) > 1e-9:
                try:
                    if kind == "VL":
                        _jog_az(ctrl, -3.0, shortest=shortest_path)
                    elif kind == "VR":
                        _jog_az(ctrl, 3.0, shortest=shortest_path)
                    elif kind == "VU":
                        _jog_el(ctrl, 3.0)
                    elif kind == "VD":
                        _jog_el(ctrl, -3.0)
                except Exception:
                    pass
            i += 1
            continue

        if up in ("VL", "VR", "VU", "VD") and nxt is not None:
            # "VL 1000" als zwei Tokens
            try:
                float(nxt)
                token2 = f"{up}{nxt}"
                m_vel2 = _RE_VEL.match(token2)
                if m_vel2:
                    parts[i] = token2
                    # ohne i++ erneut verarbeiten
                    continue
            except ValueError:
                pass

        if up == "GS":
            # Bitmask: 1=Idle, 2=Moving
            replies.append("GS2" if _is_moving(ctrl) else "GS1")
            i += 1
            continue

        if up == "GE":
            replies.append("GE0")
            i += 1
            continue

        if up == "VE":
            replies.append(_VERSION)
            i += 1
            continue

        if up in ("PARK", "RESET"):
            try:
                ctrl.hold_all_at_current_pos()
            except Exception:
                pass
            i += 1
            continue

        if _RE_CR.match(token) or up.startswith("CR"):
            # Config-Read: Dummy-Antwort
            m = _RE_CR.match(token)
            reg = m.group(1) if m else "0"
            replies.append(f"CR{reg},0")
            i += 1
            continue

        if _RE_CW.match(token) or up.startswith("CW"):
            # Config-Write: still akzeptieren
            i += 1
            continue

        if up in _SILENT_TAGS or up[:2] in _SILENT_TAGS:
            i += 1
            continue

        i += 1

    if not replies:
        return (None, False)
    return ("\n".join(replies) + "\n", False)
