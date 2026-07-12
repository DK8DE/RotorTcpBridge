"""Gemeinsame Winkel-Hilfsfunktionen für Kompass und Windrose."""

from __future__ import annotations

import math
import re

_ANGLE_D10_RE = re.compile(r"^(-?)(\d+)(?:[.,](\d*))?$")


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


def shortest_delta_az_rotor_deg(current: float, target: float) -> float:
    """Winkeldifferenz für Rotor-Ist; 360° nach Homing ist nicht dasselbe wie 0°."""
    c = float(current)
    t = float(target)
    if t >= 359.95 and c < 359.95:
        return t - c
    if c >= 359.95 and t < 359.95:
        return shortest_delta_deg(c, t)
    return shortest_delta_deg(c, t)


def deg_str_to_d10(s: str) -> int | None:
    """RS485-Winkelstring → 0,1°-Einheiten; nur erste Nachkommastelle, Rest abschneiden.

    ``96,15`` → 961 (Anzeige 96,1°), nicht 962 durch Float-/Format-Rundung.
    """
    raw = str(s or "").strip().replace(" ", "")
    if not raw:
        return None
    if ":" in raw:
        raw = raw.split(":", 1)[0].strip()
    if ";" in raw:
        raw = raw.split(";", 1)[0].strip()
    raw = raw.replace(",", ".")
    m = _ANGLE_D10_RE.match(raw)
    if not m:
        try:
            return deg_to_d10(float(raw))
        except Exception:
            return None
    neg = m.group(1) == "-"
    whole = int(m.group(2))
    frac_s = m.group(3) or ""
    frac1 = int(frac_s[0]) if frac_s else 0
    d10 = whole * 10 + frac1
    return -d10 if neg else d10


def deg_to_d10(deg: float) -> int:
    """Grad → 0,1°-Einheiten; zweite Nachkommastelle abschneiden (Rotor-Auflösung 0,1°)."""
    v = float(deg)
    if v >= 0.0:
        return int(math.floor(v * 10.0 + 1e-6))
    return int(math.ceil(v * 10.0 - 1e-6))


def d10_to_deg(d10: int) -> float:
    """0,1°-Einheiten → Grad ohne Float-Anzeige-Rundung."""
    if is_az_pos_at_full_circle_d10(int(d10)):
        return 360.0
    return int(d10) / 10.0


def fmt_deg_d10(d10: int) -> str:
    """Winkel aus 0,1°-Einheiten als String mit genau einer Nachkommastelle."""
    if is_az_pos_at_full_circle_d10(int(d10)):
        return "360.0°"
    d = int(d10)
    if d < 0:
        d = abs(d)
        return f"-{d // 10}.{d % 10}°"
    return f"{d // 10}.{d % 10}°"


def is_az_pos_at_full_circle_d10(pos_d10: int) -> bool:
    """True wenn GETPOSDG nahe 360,0° meldet (Homing-Ende ohne Rückfahrt)."""
    return int(pos_d10) >= 3599


def az_pos_deg_from_d10(pos_d10: int, smooth_d10f: float | None = None) -> float:
    """Rotor-Azimut in Grad aus GETPOSDG (0,1°-Einheiten).

    Nach Homing mit SETHOMERETURN=0 meldet die Hardware oft 360,00° — das ist nicht
    dasselbe wie 0° für Anzeige/Glättung (volle Umdrehung vs. Nullstellung).
    """
    p = int(pos_d10)
    if is_az_pos_at_full_circle_d10(p):
        return 360.0
    if smooth_d10f is not None:
        return wrap_deg(float(smooth_d10f) / 10.0)
    return wrap_deg(d10_to_deg(p))


def antenna_bearing_from_rotor_and_offset(rotor_deg: float, offset_deg: float) -> float:
    """Antennen-Peilung für Kompass-Anzeige (Rotor-Ist + Versatz)."""
    return wrap_deg(float(rotor_deg) + float(offset_deg))


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

    Modelliert typisches Regler-Verhalten beim Überfahren des Nullpunkts:
    - Ist > Ziel (z. B. 353°→9°): oft langer CCW-Bogen statt kurz CW
    - Ist < Ziel (z. B. 9°→350°): oft langer CW-Bogen statt kurz CCW
    """
    c = float(cur)
    t = wrap_deg(tgt)
    if c >= 359.95:
        c = 360.0
    else:
        c = wrap_deg(c)
    cw = _rotor_cw_travel_deg(c, t)
    ccw = _rotor_ccw_travel_deg(c, t)
    if abs(cw - ccw) < 1e-9:
        return cw
    # Ziel kleiner als Ist (Nullübertritt von oben): langer CCW-Bogen.
    if t < c and cw < ccw and ccw > 180.0:
        return ccw
    # Ziel größer als Ist (Nullübertritt von unten, z. B. Ost-Anschlag): langer CW-Bogen.
    if t > c and cw > ccw and cw > 180.0:
        return cw
    return min(cw, ccw)


def _normalize_rotor_az_for_routing(cur: float) -> float:
    c = float(cur)
    if c >= 359.95:
        return 360.0
    return wrap_deg(c)


def raw_rotor_az_deg_from_axis(az_axis) -> float | None:
    """Rotor-Ist aus GETPOSDG-Rohwert — für Dipol-Routing (ohne Anzeige-Glättung).

    Die UI-Glättung kann kurzzeitig bei 0° hängen, obwohl der Rotor z. B. bei 353°
    steht; dann würde die Keulenwahl fälschlich die Volldrehung wählen.
    """
    if az_axis is None:
        return None
    try:
        pos_d10 = getattr(az_axis, "pos_d10", None)
        if pos_d10 is not None:
            return az_pos_deg_from_d10(int(pos_d10))
    except Exception:
        pass
    return None


def current_rotor_az_deg(az_axis, *, now: float | None = None) -> float | None:
    """Aktueller Rotor-Azimut (°) — geglättete Ist-Position bevorzugt."""
    if az_axis is None:
        return None
    if now is None:
        import time

        now = time.time()
    try:
        pos_d10 = int(getattr(az_axis, "pos_d10", 0))
        if hasattr(az_axis, "get_smoothed_pos_d10f"):
            return az_pos_deg_from_d10(pos_d10, float(az_axis.get_smoothed_pos_d10f(now)))
    except Exception:
        pass
    try:
        pos_d10 = getattr(az_axis, "pos_d10", None)
        if pos_d10 is not None:
            return az_pos_deg_from_d10(int(pos_d10))
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
    last_rotor_az: float | None = None,
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
    cur = _normalize_rotor_az_for_routing(float(current_rotor_az))
    cost_p = dipole_rotor_move_cost(cur, primary)
    cost_a = dipole_rotor_move_cost(cur, alternate)

    if last_rotor_az is not None:
        last = _normalize_rotor_az_for_routing(float(last_rotor_az))
        on_primary = dipole_rotor_move_cost(last, primary) <= 25.0
        on_alternate = dipole_rotor_move_cost(last, alternate) <= 25.0
        if on_alternate and not on_primary:
            cost_p += 30.0
        elif on_primary and not on_alternate:
            cost_a += 30.0
        elif abs(cost_a - cost_p) <= 30.0:
            if dipole_rotor_move_cost(last, alternate) < dipole_rotor_move_cost(last, primary):
                cost_p += 10.0
            elif dipole_rotor_move_cost(last, primary) < dipole_rotor_move_cost(last, alternate):
                cost_a += 10.0

    if cost_a < cost_p:
        return alternate
    return primary


def fmt_deg(v: float) -> str:
    """Winkel als String mit 1 Nachkommastelle und °-Symbol (über d10, ohne .1f-Rundung)."""
    try:
        return fmt_deg_d10(deg_to_d10(float(v)))
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
