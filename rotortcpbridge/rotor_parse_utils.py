"""Parser für Telegram-Parameter (RS485) – ausgelagert für Tests und schlankeren RotorController."""

from __future__ import annotations

import re
from typing import Optional


def parse_float(s: str) -> Optional[float]:
    try:
        return float(s.strip().replace(",", "."))
    except Exception:
        return None


def parse_int(s: str) -> Optional[int]:
    try:
        return int(float(s.strip().replace(",", ".")))
    except Exception:
        return None


def parse_float_any(s: str) -> Optional[float]:
    """Extrahiert den ersten Float aus beliebigem PARAMS-Text.

    Hintergrund: Manche ACKs liefern nicht nur einen nackten Zahlenwert,
    sondern zusätzliche Teile (z.B. mit ';'). Für die Windanzeige soll
    trotzdem robust der Messwert übernommen werden.
    """
    try:
        txt = str(s or "").strip()
    except Exception:
        return None
    if not txt:
        return None
    m = re.search(r"[-+]?\d+(?:[.,]\d+)?", txt)
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", "."))
    except Exception:
        return None


def parse_setposcc_params(params: str) -> tuple[Optional[float], Optional[int]]:
    """SETPOSCC-Payload: Vorschauwinkel [, optional ``;Rotor-Bus-ID``].

    - ``151,30`` → ``(151.3, None)`` (altes Format, Achse aus Telegramm-Ziel)
    - ``151,30;20`` → ``(151.3, 20)`` wenn das letzte ``;``-Feld eine Ganzzahl 1–254 ist

    Checksumme bleibt unverändert (letzte Zahl im gesamten PARAMS-String).
    """
    raw = str(params or "").strip()
    if not raw:
        return None, None
    parts = [p.strip() for p in raw.split(";") if p.strip() != ""]
    if len(parts) >= 2:
        tail = parts[-1].replace(" ", "")
        if tail.isdigit():
            rid = int(tail)
            if 1 <= rid <= 254:
                angle_s = ";".join(parts[:-1])
                return parse_float(angle_s), rid
    return parse_float(raw), None


def parse_getposdg_ist_deg(params: str) -> Optional[float]:
    """Ist-Position aus ACK_GETPOSDG-Parametern (Grad, Komma als Dezimaltrenner).

    Im RS485-ASCII-Rahmen ist die letzte Zahl nach ``:`` bereits die Checksumme (siehe
    :mod:`rs485_protocol`), sodass ``params`` für jede Achse **genau einen** Positionswert
    enthält. ``Ist;Soll`` (semikolongetrennt) wird weiter unterstützt — hier ist der erste
    Wert der Ist.
    """
    try:
        p = str(params or "").strip()
    except Exception:
        return None
    if not p:
        return None
    if ";" in p:
        return parse_float(p.split(";", 1)[0].strip())
    if ":" in p:
        return parse_float(p.split(":", 1)[0].strip())
    return parse_float(p)


def parse_getposdg_axis_deg(params: str, *, is_az: bool) -> Optional[float]:
    """Alias für :func:`parse_getposdg_ist_deg` (AZ und EL haben getrennte Slaves/ACKs)."""
    _ = is_az
    return parse_getposdg_ist_deg(params)
