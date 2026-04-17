"""Zentraler, thread-sicherer State-Cache für Rig-Bridge."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

from .utils import now_ts


@dataclass
class RadioStateCache:
    """Zentrale Zustandsdaten des Funkgeräts."""

    connected: bool = False
    selected_rig: str = ""
    com_port: str = ""
    #: 0 = noch keine Frequenz aus Software/CAT bekannt (kein erzwungenes 2-m-Band).
    frequency_hz: int = 0
    mode: str = "USB"
    ptt: bool = False
    vfo: str = "A"
    split: bool = False
    last_error: str = ""
    last_success_ts: float = 0.0
    protocol_active: dict[str, bool] = field(
        default_factory=lambda: {"flrig": False, "hamlib": False}
    )
    protocol_clients: dict[str, int] = field(
        default_factory=lambda: {"flrig": 0, "hamlib": 0}
    )

    def __post_init__(self) -> None:
        self._lock = threading.RLock()

    def snapshot(self) -> dict[str, Any]:
        """Thread-sicheren Snapshot liefern."""
        with self._lock:
            return {
                "connected": bool(self.connected),
                "selected_rig": str(self.selected_rig),
                "com_port": str(self.com_port),
                "frequency_hz": int(self.frequency_hz),
                "mode": str(self.mode),
                "ptt": bool(self.ptt),
                "vfo": str(self.vfo),
                "split": bool(self.split),
                "last_error": str(self.last_error),
                "last_success_ts": float(self.last_success_ts),
                "protocol_active": dict(self.protocol_active),
                "protocol_clients": dict(self.protocol_clients),
            }

    def update(self, **kwargs: Any) -> None:
        """Mehrere Felder atomar aktualisieren."""
        with self._lock:
            for key, value in kwargs.items():
                if hasattr(self, key):
                    setattr(self, key, value)

    def set_error(self, msg: str) -> None:
        """Letzten Fehler setzen."""
        with self._lock:
            self.last_error = str(msg or "")

    def mark_success(self) -> None:
        """Kommunikationserfolg markieren."""
        with self._lock:
            self.last_success_ts = now_ts()
            self.last_error = ""

    def set_protocol_active(self, protocol: str, active: bool) -> None:
        """Aktivstatus eines Protokolls setzen."""
        with self._lock:
            self.protocol_active[str(protocol)] = bool(active)

    def set_protocol_clients(self, protocol: str, clients: int) -> None:
        """Client-Anzahl eines Protokolls setzen."""
        with self._lock:
            self.protocol_clients[str(protocol)] = max(0, int(clients))
