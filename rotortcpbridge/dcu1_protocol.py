"""Hy-Gain DCU-1 / Rotor-EZ / Green-Heron Dialekt (Semikolon-terminiert, nur AZ)."""

from __future__ import annotations

import re
from typing import Optional

from .angle_utils import az_d10_for_external_report

# AP1nnn / API1nnn / APnnn — Ziel setzen (nnn 1–3 Ziffern, optional Whitespace)
_RE_AP = re.compile(r"^AP(?:I\s*1|I1|1)?\s*(\d{1,3})$", re.IGNORECASE)
_RE_AM = re.compile(r"^AM\s*1?$", re.IGNORECASE)
# AI / AI1 / BI1 — Positionsabfrage
_RE_AI = re.compile(r"^(AI|BI)\s*1?$", re.IGNORECASE)


def _az_deg(ctrl, *, shortest: bool, report_mod360: bool) -> float:
    try:
        raw = int(getattr(ctrl.az, "pos_d10", 0) or 0) if getattr(ctrl, "enable_az", True) else 0
        d10 = az_d10_for_external_report(raw, shortest_path=shortest, report_mod360=report_mod360)
        return float(d10) / 10.0
    except Exception:
        return 0.0


def process_dcu1_frame(
    frame: str,
    ctrl,
    *,
    shortest_path: bool = False,
    report_mod360: bool = False,
    pending_target: Optional[list] = None,
) -> tuple[Optional[str], bool]:
    """Einen DCU-Befehl (ohne ``;``) verarbeiten.

    ``pending_target`` ist optional eine 1-Element-Liste für AP→AM-Sequenzen.
    Rückgabe ``(antwort|None, close)``.
    """
    cmd = re.sub(r"\s+", " ", (frame or "").strip())
    if not cmd:
        return (None, False)

    m_ap = _RE_AP.match(cmd.replace(" ", ""))
    if m_ap is None:
        # Whitespace-Variante: "AP1 180"
        m_ap = _RE_AP.match(cmd)
    if m_ap:
        try:
            az = int(m_ap.group(1)) % 360
            if pending_target is not None:
                pending_target[:] = [float(az)]
            if getattr(ctrl, "enable_az", True):
                # Viele Clients senden nur AP (ohne AM) — sofort fahren.
                ctrl.set_az_from_spid(int(az) * 10, shortest_path=bool(shortest_path))
        except Exception:
            pass
        return (None, False)

    if _RE_AM.match(cmd.replace(" ", "")) or _RE_AM.match(cmd):
        try:
            if pending_target and pending_target[0] is not None:
                az = float(pending_target[0])
                if getattr(ctrl, "enable_az", True):
                    ctrl.set_az_from_spid(
                        int(round(az * 10.0)), shortest_path=bool(shortest_path)
                    )
        except Exception:
            pass
        return (None, False)

    m_ai = _RE_AI.match(cmd.replace(" ", "")) or _RE_AI.match(cmd)
    if m_ai:
        az = int(round(_az_deg(ctrl, shortest=shortest_path, report_mod360=report_mod360))) % 360
        prefix = m_ai.group(1).upper()
        compact = f"{az:03d};"
        # AI1/BI1: Echo-Form (enthält weiterhin 3 Ziffern für Hamlib/OpsLog)
        raw_up = cmd.replace(" ", "").upper()
        if raw_up in ("AI1", "BI1") or (prefix == "BI"):
            return (f"{prefix}1{az:03d};", False)
        return (compact, False)

    # Stop-Emulation: aktuelle Position erneut anfahren / halten
    up = cmd.upper().replace(" ", "")
    if up in ("S", "STOP", ";"):
        try:
            if getattr(ctrl, "enable_az", True):
                ctrl.hold_az_at_current_pos()
        except Exception:
            pass
        return (None, False)

    return (None, False)


def process_dcu1_line(
    line: str,
    ctrl,
    *,
    shortest_path: bool = False,
    report_mod360: bool = False,
    pending_target: Optional[list] = None,
) -> tuple[Optional[str], bool]:
    """Zeile/Puffer mit einem oder mehreren ``;``-Frames."""
    text = (line or "").strip()
    if not text:
        return (None, False)
    # Falls der TCP-Helper bereits bis zum Terminator geschnitten hat, fehlt ``;``.
    frames = [p for p in text.replace("\r", ";").replace("\n", ";").split(";") if p.strip()]
    if not frames and text:
        frames = [text]
    replies: list[str] = []
    for fr in frames:
        resp, _ = process_dcu1_frame(
            fr,
            ctrl,
            shortest_path=shortest_path,
            report_mod360=report_mod360,
            pending_target=pending_target,
        )
        if resp:
            replies.append(resp)
    if not replies:
        return (None, False)
    return ("".join(replies), False)
