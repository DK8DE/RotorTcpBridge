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


def rotor_travel_deg(cur: float, tgt: float) -> float:
    """Kürzester Rotor-Drehweg cur→tgt in Grad (0…180)."""
    c = wrap_deg(cur)
    t = wrap_deg(tgt)
    cw = (t - c) % 360.0
    ccw = (c - t) % 360.0
    return min(cw, ccw)


def _rotor_cw_travel_deg(cur: float, tgt: float) -> float:
    return (wrap_deg(tgt) - wrap_deg(cur)) % 360.0


def _rotor_ccw_travel_deg(cur: float, tgt: float) -> float:
    return (wrap_deg(cur) - wrap_deg(tgt)) % 360.0


def dipole_rotor_move_cost(cur: float, tgt: float) -> float:
    """Geschätzter Rotor-Fahrweg in Grad (Dipol-Keulenwahl).

    Viele Controller nehmen bei Ziel „über Null“ (z. B. 300°→10°) den langen
    CCW-Bogen (~290°), obwohl CW kürzer wäre (~70°). Dann lohnt die Gegenkeule.
    """
    c = wrap_deg(cur)
    t = wrap_deg(tgt)
    cw = _rotor_cw_travel_deg(c, t)
    ccw = _rotor_ccw_travel_deg(c, t)
    short = min(cw, ccw)
    if t < c and cw < ccw and ccw > 180.0 and cw >= 50.0:
        return ccw
    return short


def current_rotor_az_deg(az_axis, *, now: float | None = None) -> float | None:
    """Aktueller Rotor-Azimut (°) — geglättete Ist-Position bevorzugt."""
    if az_axis is None:
        return None
    if now is None:
        import time

        now = time.time()
    try:
        if hasattr(az_axis, "get_smoothed_pos_d10f"):
            return wrap_deg(float(az_axis.get_smoothed_pos_d10f(now)) / 10.0)
    except Exception:
        pass
    try:
        pos_d10 = getattr(az_axis, "pos_d10", None)
        if pos_d10 is not None:
            return wrap_deg(float(pos_d10) / 10.0)
    except Exception:
        pass
    return None


def antenna_dipole_enabled(az_axis, cfg: dict | None, ant_idx: int) -> bool:
    """Dipol-Flag für Antenne ant_idx (0–2): Rotor-Zustand, sonst Config."""
    ant_idx = max(0, min(2, int(ant_idx)))
    slot = ant_idx + 1
    if az_axis is not None:
        hw_v = getattr(az_axis, f"antdp{slot}", None)
        if hw_v is not None:
            return bool(hw_v)
    if cfg:
        dips = (cfg.get("ui") or {}).get("antenna_dipoles_az", [False, False, False])
        try:
            return bool(dips[ant_idx])
        except (IndexError, TypeError):
            pass
    return False


def rotor_az_for_display_bearing(
    display_bearing_deg: float,
    offset_az_deg: float,
    current_rotor_az: float | None = None,
    *,
    dipole: bool = False,
) -> float:
    """Rotor-Azimut für Ziel-Peilung (Anzeige-Azimut, 0°=Nord).

    Normale Antenne: Hauptkeule zeigt auf ``display_bearing_deg``.
    Dipol: Haupt- oder Gegenkeule (+180° Rotor) — welche Rotor-Position den
    kürzeren geschätzten Fahrweg von der aktuellen Ist-Position erfordert.
    """
    primary = wrap_deg(float(display_bearing_deg) - float(offset_az_deg))
    if not dipole:
        return primary
    alternate = wrap_deg(primary + 180.0)
    if current_rotor_az is None:
        return primary
    cur = wrap_deg(float(current_rotor_az))
    cost_p = dipole_rotor_move_cost(cur, primary)
    cost_a = dipole_rotor_move_cost(cur, alternate)
    if cost_a < cost_p:
        return alternate
    return primary


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
