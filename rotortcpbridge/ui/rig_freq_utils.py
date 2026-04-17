"""Gemeinsame Hilfen für Rig-Frequenz (MHz) in Hauptfenster und Kompass."""


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
