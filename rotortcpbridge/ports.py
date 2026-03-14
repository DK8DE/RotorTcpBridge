from __future__ import annotations

# Hilfsfunktionen zum Auslesen von seriellen Schnittstellen (COM-Ports).
# Unter Windows liefert pyserial die Ports über serial.tools.list_ports.

from typing import List

def list_serial_ports()->List[str]:
    """Gibt eine Liste verfügbarer COM-Ports zurück (z.B. ['COM3','COM7']).

    Wenn pyserial nicht verfügbar ist, wird eine leere Liste zurückgegeben.
    """
    try:
        import serial.tools.list_ports
        ports = []
        for p in serial.tools.list_ports.comports():
            # p.device ist z.B. 'COM3'
            if p.device:
                ports.append(str(p.device))
        # sortieren: COM1, COM2, COM10 ...
        def _key(name:str):
            try:
                return int(name.replace("COM",""))
            except Exception:
                return 9999
        ports.sort(key=_key)
        return ports
    except Exception:
        return []
