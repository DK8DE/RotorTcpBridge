"""Gemeinsame Winkel-Hilfsfunktionen für Kompass und Windrose."""
from __future__ import annotations


def wrap_deg(v: float) -> float:
    """Winkel in den Bereich 0..360° bringen."""
    v = float(v) % 360.0
    if v < 0.0:
        v += 360.0
    return v


def clamp_el(deg: float) -> float:
    """EL-Winkel auf 0..90° begrenzen."""
    try:
        v = float(deg)
    except Exception:
        v = 0.0
    if v < 0.0:
        v = 0.0
    if v > 90.0:
        v = 90.0
    return v


def shortest_delta_deg(current: float, target: float) -> float:
    """Kleinste Winkeldifferenz target-current im Bereich [-180, 180]."""
    return (float(target) - float(current) + 180.0) % 360.0 - 180.0


def fmt_deg(v: float) -> str:
    """Winkel als String mit 1 Nachkommastelle und °-Symbol."""
    try:
        return f"{float(v):.1f}°"
    except Exception:
        return f"{v}°"
