from __future__ import annotations

# Hilfsfunktionen zum Auslesen von seriellen Schnittstellen (COM-Ports).
# Unter Windows liefert pyserial die Ports über serial.tools.list_ports.

from typing import List, Tuple


def list_serial_ports() -> List[str]:
    """Gibt eine Liste verfügbarer COM-Ports zurück (z.B. ['COM3','COM7']).

    Wenn pyserial nicht verfügbar ist, wird eine leere Liste zurückgegeben.
    """
    return [dev for dev, _ in list_serial_port_entries()]


def _com_sort_key(device: str) -> int:
    s = (device or "").strip().upper()
    if s.startswith("COM"):
        s = s[3:]
    try:
        return int(s)
    except Exception:
        return 9999


def list_serial_port_entries() -> List[Tuple[str, str]]:
    """``(device, tooltip)`` für UI: ``device`` ist der zu öffnende Name (z. B. ``COM8``).

    ``tooltip`` ist die Treiber-/Gerätebeschreibung für einen Hover-Tooltip (leer,
    wenn sie dem Gerätenamen entspricht oder fehlt). Kurze Anzeige ``COMn``,
    Details nur im Tooltip.
    """
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
