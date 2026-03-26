"""Synchronisation der Antennenauswahl (compass_antenna) zwischen Kompass- und Kartenfenster."""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal


class AntennaSelectionBridge(QObject):
    """Wird ausgelöst, wenn der Nutzer die Antenne in einem Fenster ändert (Index 0–2)."""

    selection_changed = Signal(int)
