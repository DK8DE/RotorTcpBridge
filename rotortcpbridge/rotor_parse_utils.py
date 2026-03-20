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
