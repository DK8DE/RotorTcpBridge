from __future__ import annotations

# Hilfsfunktionen zum Auslesen von seriellen Schnittstellen (COM-Ports).
# Unter Windows liefert pyserial die Ports über serial.tools.list_ports.
# Wichtig: comports() nur im GUI-/Hauptthread aufrufen — Hintergrundthreads
# können unter Windows (WMI/COM) minutenlang hängen.

import time
from typing import List, Optional, Tuple

_cache_entries: Optional[List[Tuple[str, str]]] = None
_cache_ts: float = 0.0
_CACHE_TTL_S = 12.0


def list_serial_ports(*, force: bool = False) -> List[str]:
    """Gibt eine Liste verfügbarer COM-Ports zurück (z.B. ['COM3','COM7']).

    Wenn pyserial nicht verfügbar ist, wird eine leere Liste zurückgegeben.
    """
    return [dev for dev, _ in list_serial_port_entries(force=force)]


def _com_sort_key(device: str) -> int:
    s = (device or "").strip().upper()
    if s.startswith("COM"):
        s = s[3:]
    try:
        return int(s)
    except Exception:
        return 9999


def _scan_serial_port_entries() -> List[Tuple[str, str]]:
    try:
        import serial.tools.list_ports

        rows: list[tuple[str, str]] = []
        for p in serial.tools.list_ports.comports():
            dev = str(p.device or "").strip()
            if not dev:
                continue
            desc = str(p.description or "").strip()
            if desc and desc.upper().replace(" ", "") != dev.upper().replace(" ", ""):
                tip = desc
            else:
                tip = ""
            rows.append((dev, tip))
        rows.sort(key=lambda t: _com_sort_key(t[0]))
        return rows
    except Exception:
        return []


def list_serial_port_entries(*, force: bool = False) -> List[Tuple[str, str]]:
    """``(device, tooltip)`` für UI: ``device`` ist der zu öffnende Name (z. B. ``COM8``).

    ``tooltip`` ist die Treiber-/Gerätebeschreibung für einen Hover-Tooltip (leer,
    wenn sie dem Gerätenamen entspricht oder fehlt). Kurze Anzeige ``COMn``,
    Details nur im Tooltip.

    Kurz gecacht; ``force=True`` erzwingt einen frischen Scan (Refresh-Button).
    Nur im Hauptthread aufrufen.
    """
    global _cache_entries, _cache_ts
    now = time.monotonic()
    if (
        not force
        and _cache_entries is not None
        and (now - _cache_ts) < _CACHE_TTL_S
    ):
        return list(_cache_entries)

    rows = _scan_serial_port_entries()
    _cache_entries = list(rows)
    _cache_ts = time.monotonic()
    return list(_cache_entries)


def cached_serial_port_entries() -> Optional[List[Tuple[str, str]]]:
    """Aktueller Cache ohne Scan, oder ``None`` wenn noch nichts geladen."""
    if _cache_entries is None:
        return None
    return list(_cache_entries)
