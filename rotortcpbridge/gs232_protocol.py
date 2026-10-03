"""Yaesu GS-232B Textprotokoll (Kernbefehle)."""

from __future__ import annotations

import re
from typing import Optional

from .angle_utils import az_d10_for_external_report

_RE_M = re.compile(r"^M\s*(\d{1,3})\s*$", re.IGNORECASE)
_RE_W = re.compile(r"^W\s*(\d{1,3})\s+(\d{1,3})\s*$", re.IGNORECASE)

_MAX_PENDING = 64
_SINGLE = frozenset("CBASELRUDOFZH")


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


def _fmt3(deg: float) -> str:
    v = int(round(max(0.0, min(450.0, float(deg)))))
    return f"{v:03d}"


def _set_az(ctrl, az_deg: float, *, shortest: bool) -> None:
    if not getattr(ctrl, "enable_az", True):
        return
    ctrl.set_az_from_spid(int(round(float(az_deg) * 10.0)), shortest_path=bool(shortest))


def _set_el(ctrl, el_deg: float) -> None:
    if not getattr(ctrl, "enable_el", False):
        return
    ctrl.set_el_from_spid(int(round(float(el_deg) * 10.0)))


def process_gs232_line(
    line: str,
    ctrl,
    *,
    shortest_path: bool = False,
    report_mod360: bool = False,
) -> tuple[Optional[str], bool]:
    """Eine GS-232B-Zeile verarbeiten. Rückgabe ``(antwort|None, close)``."""
    cmd = (line or "").strip()
    if not cmd:
        return (None, False)

    up = cmd.upper()

    if up == "C":
        return (f"AZ={_fmt3(_az_deg(ctrl, shortest=shortest_path, report_mod360=report_mod360))}\r", False)
    if up == "B":
        return (f"EL={_fmt3(_el_deg(ctrl))}\r", False)
    if up == "C2":
        az = _fmt3(_az_deg(ctrl, shortest=shortest_path, report_mod360=report_mod360))
        el = _fmt3(_el_deg(ctrl))
        return (f"AZ={az} EL={el}\r", False)

    if up == "S":
        try:
            ctrl.hold_all_at_current_pos()
        except Exception:
            pass
        return (None, False)
    if up == "A":
        try:
            if getattr(ctrl, "enable_az", True):
                ctrl.hold_az_at_current_pos()
        except Exception:
            pass
        return (None, False)
    if up == "E":
        try:
            if getattr(ctrl, "enable_el", False):
                ctrl.hold_el_at_current_pos()
        except Exception:
            pass
        return (None, False)

    if up in ("L", "R", "U", "D"):
        try:
            if up in ("L", "R") and getattr(ctrl, "enable_az", True):
                cur = float(int(getattr(ctrl.az, "pos_d10", 0) or 0)) / 10.0
                delta = -3.0 if up == "L" else 3.0
                _set_az(ctrl, cur + delta, shortest=shortest_path)
            elif up in ("U", "D") and getattr(ctrl, "enable_el", False):
                cur = float(int(getattr(ctrl.el, "pos_d10", 0) or 0)) / 10.0
                delta = 3.0 if up == "U" else -3.0
                _set_el(ctrl, max(0.0, min(180.0, cur + delta)))
        except Exception:
            pass
        return (None, False)

    m = _RE_M.match(cmd)
    if m:
        try:
            _set_az(ctrl, float(m.group(1)), shortest=shortest_path)
        except Exception:
            pass
        return (None, False)

    w = _RE_W.match(cmd)
    if w:
        try:
            _set_az(ctrl, float(w.group(1)), shortest=shortest_path)
            _set_el(ctrl, float(w.group(2)))
        except Exception:
            pass
        return (None, False)

    # Kalibrierung / Speed / Modus: still akzeptieren
    if up in ("O", "F", "O2", "F2", "P36", "P45", "Z", "H") or up.startswith("X"):
        return (None, False)

    return (None, False)


def _take_ws(text: str, i: int) -> int:
    n = len(text)
    while i < n and text[i] in " \t\r\n":
        i += 1
    return i


def _take_digits(text: str, i: int, maxlen: int = 3) -> tuple[str, int]:
    n = len(text)
    start = i
    while i < n and text[i].isdigit() and (i - start) < maxlen:
        i += 1
    return text[start:i], i


def extract_gs232_commands(buf: bytes) -> tuple[list[str], bytes]:
    """GS-232B-Befehle aus einem Byte-Puffer ziehen.

    Erkennt CR/LF-Zeilen **und** Befehle ohne Terminator (typisch bei
    seriellen GS-232-Clients). Unvollständige Reste bleiben im Puffer.
    """
    if not buf:
        return ([], b"")
    text = buf.decode("ascii", errors="ignore")
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        i = _take_ws(text, i)
        if i >= n:
            break
        ch = text[i].upper()
        # Mehrbuchstabenbefehle vor den Einzelbuchstaben prüfen.
        if ch == "C" and i + 1 < n and text[i + 1] == "2":
            out.append("C2")
            i += 2
            continue
        if ch == "O" and i + 1 < n and text[i + 1] == "2":
            out.append("O2")
            i += 2
            continue
        if ch == "F" and i + 1 < n and text[i + 1] == "2":
            out.append("F2")
            i += 2
            continue
        if ch == "P":
            if i + 2 < n and text[i + 1 : i + 3] in ("36", "45"):
                out.append("P" + text[i + 1 : i + 3])
                i += 3
                continue
            if i + 1 >= n:
                break  # „P“ allein — auf Rest warten
            i += 1
            continue
        if ch == "X":
            j = i + 1
            while j < n and text[j].isdigit():
                j += 1
            out.append(text[i:j])
            i = j
            continue
        if ch == "M":
            j = _take_ws(text, i + 1)
            digits, j2 = _take_digits(text, j)
            if not digits:
                if j2 >= n:
                    break
                i += 1
                continue
            # Am Pufferende keine Ziffern-Abschneidung: „M1“ kann „M180“ werden.
            if j2 >= n and len(digits) < 3:
                break
            out.append("M" + digits)
            i = j2
            continue
        if ch == "W":
            j = _take_ws(text, i + 1)
            d1, j = _take_digits(text, j)
            if not d1:
                if j >= n:
                    break
                i += 1
                continue
            if j >= n:
                break
            j = _take_ws(text, j)
            if j >= n:
                break
            d2, j2 = _take_digits(text, j)
            if not d2:
                if j2 >= n:
                    break
                i += 1
                continue
            if j2 >= n and len(d2) < 3:
                break
            out.append(f"W{d1} {d2}")
            i = j2
            continue
        if ch in _SINGLE:
            # Am Pufferende „C“/„O“/„F“ zurückhalten: nächstes Byte kann „2“ sein.
            if ch in ("C", "O", "F") and i + 1 >= n:
                break
            out.append(ch)
            i += 1
            continue
        i += 1
    leftover = text[i:].encode("ascii", errors="ignore")
    if len(leftover) > _MAX_PENDING:
        leftover = leftover[-_MAX_PENDING:]
    return (out, leftover)
