"""Konfiguration für Rig-Bridge laden/validieren."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .cat_commands import normalize_com_port

# Maximale Länge für Rig-Profil-Anzeigenamen (Funkgerät-Profile in der UI/Config).
RIG_PROFILE_NAME_MAX_LEN = 20


def clamp_rig_profile_display_name(
    name: str | None, *, max_len: int = RIG_PROFILE_NAME_MAX_LEN
) -> str:
    """Profilname trimmen und auf ``max_len`` Zeichen kürzen."""
    s = str(name or "").strip()
    if len(s) <= max_len:
        return s
    return s[:max_len]


def _normalize_hamlib_listeners_dict(h: dict[str, Any]) -> None:
    """listeners-Liste vereinheitlichen; Legacy ``port`` → eine Zeile nur wenn ``listeners`` fehlt/leer."""
    raw = h.get("listeners")
    listeners = raw if isinstance(raw, list) else []
    if len(listeners) == 0:
        if "port" in h:
            try:
                op = int(h.get("port", 4532))
            except (TypeError, ValueError):
                op = 4532
            op = max(1, min(65535, op))
            h["listeners"] = [{"port": op, "name": ""}]
        else:
            h["listeners"] = []
    else:
        normalized: list[dict[str, Any]] = []
        for it in listeners:
            if not isinstance(it, dict):
                continue
            name = str(it.get("name", "") or "")
            pt = it.get("port", None)
            if pt in (None, ""):
                normalized.append({"name": name})
                continue
            try:
                p = int(pt)
            except (TypeError, ValueError):
                continue
            p = max(1, min(65535, p))
            normalized.append({"port": p, "name": name})
        h["listeners"] = normalized
    h.pop("port", None)


@dataclass
class RigBridgeConfig:
    """Typisierte Rig-Bridge-Konfiguration."""

    enabled: bool = False
    selected_rig: str = "Generic CAT"
    rig_brand: str = "Generisch"
    rig_model: str = "CAT (generisch)"
    hamlib_rig_id: int = 0
    com_port: str = "COM1"
    baudrate: int = 9600
    databits: int = 8
    stopbits: int = 1
    parity: str = "N"
    timeout_s: float = 0.2
    polling_interval_ms: int = 30
    auto_connect: bool = False
    auto_reconnect: bool = True
    #: COM-Bytes und TCP-Protokollzeilen (Flrig/Hamlib) ins Diagnose-Log mit Zeitstempel
    log_serial_traffic: bool = True
    #: Nach CAT-Schreibbefehl (FA/TX/MD): max. Wartezeit auf Echo/`;` — kurz halten, sonst blockiert der Worker.
    cat_post_write_drain_ms: int = 50
    #: Pause nach jedem ``SETFREQ`` auf der Seriellen (0 = aus); gibt dem TRX Zeit, bevor der nächste ``FA`` kommt.
    setfreq_gap_ms: int = 10
    flrig: dict[str, Any] = field(
        default_factory=lambda: {
            "enabled": False,
            "host": "127.0.0.1",
            "port": 12345,
            "autostart": False,
            "log_tcp_traffic": True,
        }
    )
    hamlib: dict[str, Any] = field(
        default_factory=lambda: {
            "enabled": False,
            "host": "127.0.0.1",
            "listeners": [{"port": 4532, "name": ""}],
            "autostart": False,
            "debug_traffic": False,
            "log_tcp_traffic": False,
        }
    )

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "RigBridgeConfig":
        """Aus Dict erzeugen und fehlende Felder ergänzen."""
        src = dict(data or {})
        cfg = cls()
        cfg.enabled = bool(src.get("enabled", cfg.enabled))
        cfg.selected_rig = str(src.get("selected_rig", cfg.selected_rig))
        cfg.rig_brand = str(src.get("rig_brand", cfg.rig_brand))
        cfg.rig_model = str(src.get("rig_model", cfg.rig_model))
        cfg.hamlib_rig_id = int(src.get("hamlib_rig_id", cfg.hamlib_rig_id) or 0)
        cfg.com_port = str(src.get("com_port", cfg.com_port))
        cfg.baudrate = int(src.get("baudrate", cfg.baudrate))
        cfg.databits = int(src.get("databits", cfg.databits))
        cfg.stopbits = int(src.get("stopbits", cfg.stopbits))
        cfg.parity = str(src.get("parity", cfg.parity)).upper()
        cfg.timeout_s = float(src.get("timeout_s", cfg.timeout_s))
        cfg.polling_interval_ms = int(src.get("polling_interval_ms", cfg.polling_interval_ms))
        cfg.auto_connect = bool(src.get("auto_connect", cfg.auto_connect))
        cfg.auto_reconnect = bool(src.get("auto_reconnect", cfg.auto_reconnect))
        cfg.log_serial_traffic = bool(src.get("log_serial_traffic", cfg.log_serial_traffic))
        cfg.cat_post_write_drain_ms = int(
            src.get("cat_post_write_drain_ms", cfg.cat_post_write_drain_ms)
        )
        cfg.setfreq_gap_ms = int(src.get("setfreq_gap_ms", cfg.setfreq_gap_ms))
        cfg.flrig.update(dict(src.get("flrig", {})))
        cfg.hamlib.update(dict(src.get("hamlib", {})))
        cfg.validate()
        return cfg

    def to_dict(self) -> dict[str, Any]:
        """Als serialisierbares Dict liefern."""
        return {
            "enabled": bool(self.enabled),
            "selected_rig": str(self.selected_rig),
            "rig_brand": str(self.rig_brand),
            "rig_model": str(self.rig_model),
            "hamlib_rig_id": int(self.hamlib_rig_id),
            "com_port": str(self.com_port),
            "baudrate": int(self.baudrate),
            "databits": int(self.databits),
            "stopbits": int(self.stopbits),
            "parity": str(self.parity),
            "timeout_s": float(self.timeout_s),
            "polling_interval_ms": int(self.polling_interval_ms),
            "auto_connect": bool(self.auto_connect),
            "auto_reconnect": bool(self.auto_reconnect),
            "log_serial_traffic": bool(self.log_serial_traffic),
            "cat_post_write_drain_ms": int(self.cat_post_write_drain_ms),
            "setfreq_gap_ms": int(self.setfreq_gap_ms),
            "flrig": dict(self.flrig),
            "hamlib": dict(self.hamlib),
        }

    def validate(self) -> None:
        """Grundlegende Konfigurationsvalidierung."""
        self.com_port = normalize_com_port(str(self.com_port or ""))
        if self.databits not in (5, 6, 7, 8):
            self.databits = 8
        if self.stopbits not in (1, 2):
            self.stopbits = 1
        if self.parity not in ("N", "E", "O", "M", "S"):
            self.parity = "N"
        self.baudrate = max(300, min(921600, int(self.baudrate)))
        self.timeout_s = max(0.05, min(10.0, float(self.timeout_s)))
        self.polling_interval_ms = max(30, min(5000, int(self.polling_interval_ms)))
        self.flrig["port"] = max(1, min(65535, int(self.flrig.get("port", 12345))))
        self.flrig["log_tcp_traffic"] = bool(self.flrig.get("log_tcp_traffic", True))
        _normalize_hamlib_listeners_dict(self.hamlib)
        self.hamlib["debug_traffic"] = bool(self.hamlib.get("debug_traffic", False))
        self.hamlib["log_tcp_traffic"] = bool(self.hamlib.get("log_tcp_traffic", False))
        self.log_serial_traffic = bool(self.log_serial_traffic)
        self.cat_post_write_drain_ms = max(20, min(500, int(self.cat_post_write_drain_ms)))
        self.setfreq_gap_ms = max(0, min(200, int(self.setfreq_gap_ms)))
