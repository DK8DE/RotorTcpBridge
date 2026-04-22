"""In-Memory-Schalter fuer das erweiterte CAT-/Serial-Diagnoselog.

Nur fuer Fehlersuche bei der seriellen Anbindung (COM-zu-COM via com0com):
Wenn aktiv, loggt die Bruecke **zusaetzlich** zum Standardlog:

* jeden Byte-Chunk, den ein externes Steuerprogramm (HRD, Logger32, JTDX,
  WSJT-X, Ham Radio Deluxe …) in den virtuellen COM-Port schreibt,
* jede Responder-Antwort ASCII-dekodiert inkl. ausgelesener Hz-Zahl bei
  ``FA;``/``IF;``,
* jede State-Cache-Aenderung der Frequenz mit Quelle *und* Vorher/Nachher,
* jeden Grund, aus dem der COM-Worker einen ``READFREQ`` verwirft oder
  einen Reply als „veraltet" ignoriert.

Die Sichtbarkeit der Diagnose wird ausschliesslich in-memory gesteuert.
Der Wert wird bewusst **nicht** in der Konfiguration persistiert, damit
eine versehentlich aktivierte Vollprotokollierung beim naechsten Start
verschwindet (vermeidet riesige Logdateien im Normalbetrieb)."""

from __future__ import annotations

import threading

_lock = threading.Lock()
_enabled: bool = False


def is_enabled() -> bool:
    """True, wenn das erweiterte CAT-Log eingeschaltet ist."""
    with _lock:
        return _enabled


def set_enabled(flag: bool) -> None:
    """Flag umschalten. Threadsicher; sofort wirksam in allen lesenden Stellen."""
    global _enabled
    with _lock:
        _enabled = bool(flag)


def format_ascii_preview(data: bytes, limit: int = 64) -> str:
    """Byte-Sequenz fuer das Log in druckbarer ASCII-Form anzeigen.

    Nicht druckbare Bytes werden als ``.`` ersetzt; lange Buffer werden
    auf ``limit`` Zeichen gekuerzt (mit ``…``-Marker).
    """
    if not data:
        return ""
    out: list[str] = []
    for b in data[:limit]:
        if 32 <= b < 127:
            out.append(chr(b))
        else:
            out.append(".")
    s = "".join(out)
    if len(data) > limit:
        s = s + "…"
    return s
