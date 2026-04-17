"""Statusmodell für Rig-Bridge-UI."""

from __future__ import annotations

from dataclasses import dataclass, field

from .utils import fmt_ts


@dataclass
class RigBridgeStatusModel:
    """Statusobjekt für UI und Diagnose."""

    module_enabled: bool = False
    connecting: bool = False
    radio_connected: bool = False
    selected_rig: str = ""
    com_port: str = ""
    frequency_hz: int = 0
    mode: str = "USB"
    ptt: bool = False
    vfo: str = "A"
    last_error: str = ""
    last_contact_ts: float = 0.0
    protocol_active: dict[str, bool] = field(default_factory=dict)
    protocol_clients: dict[str, int] = field(default_factory=dict)

    def led_color(self) -> str:
        """Statusfarbe für die Haupt-LED."""
        if not self.module_enabled:
            return "gray"
        if self.connecting:
            return "yellow"
        if self.radio_connected:
            return "green"
        return "red"

    def status_text(self) -> str:
        """Lesbarer Statustext für UI."""
        if not self.module_enabled:
            return "Rig-Bridge deaktiviert"
        if self.connecting:
            return "Verbindung wird aufgebaut"
        if self.radio_connected:
            return "Funkgerät verbunden"
        return "Keine Funkgeräteverbindung"

    def last_contact_text(self) -> str:
        """Formatierten Zeitstempel liefern."""
        return fmt_ts(self.last_contact_ts)
