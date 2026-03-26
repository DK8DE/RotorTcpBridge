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


def arc_segments_deg(center: float, opening_deg: float) -> list[tuple[float, float]]:
    """Kreisbogen [center − op/2, center + op/2] als 1–2 Intervalle in [0, 360)°."""
    op = min(360.0, max(0.0, float(opening_deg)))
    if op <= 0.0:
        return []
    if op >= 360.0:
        return [(0.0, 360.0)]
    hw = op * 0.5
    c = wrap_deg(center)
    lo = c - hw
    hi = c + hw
    if lo >= 0.0 and hi <= 360.0:
        return [(lo, hi)]
    if lo < 0.0:
        return [(0.0, hi), (360.0 + lo, 360.0)]
    if hi > 360.0:
        return [(lo, 360.0), (0.0, hi - 360.0)]
    return [(lo, hi)]


def om_beam_contributions_per_sector(bearing_deg: float, opening_deg: float, n_sectors: int) -> list[float]:
    """Verteilt eine OM-Richtung gleichmäßig auf ``opening_deg``; Anteile je Sektor, Summe 1.

    ``n_sectors``: Kreisteilung (wie OM-Radar-Ring). Bei Öffnung 0° fällt alles in einen Sektor.
    """
    n = max(1, min(100, int(n_sectors)))
    step = 360.0 / float(n)
    out = [0.0] * n
    try:
        op = float(opening_deg)
    except (TypeError, ValueError):
        op = 30.0
    if op <= 1e-9:
        idx = int(wrap_deg(bearing_deg) / step) % n
        out[idx] = 1.0
        return out
    segs = arc_segments_deg(bearing_deg, op)
    total_beam = sum(e - s for s, e in segs)
    if total_beam <= 1e-12:
        idx = int(wrap_deg(bearing_deg) / step) % n
        out[idx] = 1.0
        return out
    for j in range(n):
        s0 = j * step
        s1 = s0 + step
        ol = 0.0
        for s, e in segs:
            ol += max(0.0, min(e, s1) - max(s, s0))
        out[j] = ol / total_beam
    return out
