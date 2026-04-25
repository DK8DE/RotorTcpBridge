"""Gemeinsame Hilfen für Rig-Frequenz (MHz) in Hauptfenster und Kompass."""

from __future__ import annotations

# MHz inkl. Grenzen; an ``elevation_window._AMATEUR_BANDS`` angeglichen, plus übliche GHz-Bände (R1).
_AMATEUR_BAND_EDGES_MHZ: tuple[tuple[float, float], ...] = (
    (1.810, 2.000),
    (3.500, 3.800),
    (7.000, 7.300),
    (10.100, 10.150),
    (14.000, 14.350),
    (18.068, 18.168),
    (21.000, 21.450),
    (24.890, 24.990),
    (28.000, 29.700),
    (50.000, 54.000),
    (144.000, 146.000),
    (430.000, 440.000),
    (1240.0, 1300.0),
    (2300.0, 2450.0),
)

# QRG außerhalb dieser Bereiche → auffällige Anzeige (z. B. rot)
RIG_FREQ_OUT_OF_BAND_QSS = "color: #dc2626; font-weight: 600;"


def is_amateur_frequency_hz(hz: int) -> bool:
    """True wenn ``hz`` in einem der üblichen AFU-Bänder liegt (sonst False)."""
    if hz <= 0:
        return True
    m = hz / 1e6
    for lo, hi in _AMATEUR_BAND_EDGES_MHZ:
        if lo <= m <= hi:
            return True
    return False


def rig_freq_out_of_band_hz(hz: int) -> bool:
    """True wenn QRG gesetzt ist und **nicht** in einem AFU-Band liegt."""
    if hz <= 0:
        return False
    return not is_amateur_frequency_hz(hz)


def apply_rig_freq_band_alert_styles(
    ed: object | None,
    lbl: object | None,
    hz: int,
) -> None:
    """Färbt Frequenzfeld (und optional MHz-Label) rot, wenn QRG außerhalb der AFU-Bänder liegt."""
    if hz <= 0 or is_amateur_frequency_hz(hz):
        for w in (ed, lbl):
            if w is not None:
                try:
                    w.setStyleSheet("")
                except Exception:
                    pass
        return
    for w in (ed, lbl):
        if w is not None:
            try:
                w.setStyleSheet(RIG_FREQ_OUT_OF_BAND_QSS)
            except Exception:
                pass


def format_rig_freq_mhz(hz: int) -> str:
    if hz <= 0:
        return ""
    return f"{int(hz) / 1e6:.6f}"


def parse_rig_freq_mhz_text(text: str) -> int | None:
    raw = (text or "").strip().replace(" ", "")
    if not raw:
        return None
    raw_dot = raw.replace(",", ".")
    if raw_dot.isdigit():
        v = int(raw_dot)
        return v if v > 0 else None
    try:
        f = float(raw_dot)
    except ValueError:
        return None
    if f <= 0:
        return None
    if f < 1e5:
        return int(round(f * 1e6))
    return int(round(f))
