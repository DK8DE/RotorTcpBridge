"""Hintergrund-Listener: DK8DE-Module melden sich alle ~12 s per Broadcast auf UDP 8889.

Firmware (LAN/WLAN): DISCOVER_RESPONSE (AA 55 …) an 255.255.255.255:8889.
So kann die Bridge neue Module erkennen, ohne aktiv zu suchen — auch wenn die
Modul-IP in einem anderen Segment liegt.
"""

from __future__ import annotations

import socket
import time
from typing import Optional, Set

from PySide6.QtCore import QThread, Signal

from .dk8de_wlan_module import (
    DK8DE_DISCOVERY_CLIENT_PORT,
    Dk8deDevice,
    parse_dk8de_announce_datagram,
)


def announce_enabled(cfg: dict | None) -> bool:
    block = (cfg or {}).get("network_announce") if isinstance(cfg, dict) else None
    if not isinstance(block, dict):
        return True
    return bool(block.get("enabled", True))


def dismissed_announce_uids(cfg: dict | None) -> Set[str]:
    block = (cfg or {}).get("network_announce") if isinstance(cfg, dict) else None
    if not isinstance(block, dict):
        return set()
    raw = block.get("dismissed_uids") or []
    if not isinstance(raw, list):
        return set()
    return {str(x or "").strip().upper() for x in raw if str(x or "").strip()}


def dismiss_announce_uid(cfg: dict, uid: str) -> bool:
    """UID dauerhaft ausblenden. True wenn neu hinzugefügt."""
    key = str(uid or "").strip().upper()
    if not key or not isinstance(cfg, dict):
        return False
    block = cfg.setdefault("network_announce", {})
    if not isinstance(block, dict):
        block = {}
        cfg["network_announce"] = block
    lst = block.setdefault("dismissed_uids", [])
    if not isinstance(lst, list):
        lst = []
        block["dismissed_uids"] = lst
    if key in {str(x or "").strip().upper() for x in lst}:
        return False
    lst.append(key)
    block["enabled"] = bool(block.get("enabled", True))
    return True


def clear_dismissed_announce_uid(cfg: dict, uid: str) -> None:
    key = str(uid or "").strip().upper()
    if not key or not isinstance(cfg, dict):
        return
    block = cfg.get("network_announce")
    if not isinstance(block, dict):
        return
    lst = block.get("dismissed_uids")
    if not isinstance(lst, list):
        return
    block["dismissed_uids"] = [
        x for x in lst if str(x or "").strip().upper() != key
    ]


class ModuleAnnounceWatcher(QThread):
    """Lauscht dauerhaft auf UDP 8889 und meldet gefundene DK8DE-Geräte."""

    module_announced = Signal(object)  # Dk8deDevice
    status = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stop = False
        # UID → letzte Emission (Debounce, Modul sendet alle 12 s)
        self._last_emit_mono: dict[str, float] = {}
        self._emit_cooldown_s = 45.0

    def stop(self) -> None:
        self._stop = True

    def forget_uid(self, uid: str) -> None:
        """Cooldownounce für eine UID zurücksetzen (z. B. nach Ablehnen)."""
        key = str(uid or "").strip().upper()
        if key:
            self._last_emit_mono.pop(key, None)

    def run(self) -> None:
        while not self._stop:
            sock: Optional[socket.socket] = None
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                except OSError:
                    pass
                sock.bind(("0.0.0.0", DK8DE_DISCOVERY_CLIENT_PORT))
                sock.settimeout(0.5)
                self.status.emit(f"announce listen :{DK8DE_DISCOVERY_CLIENT_PORT}")
            except OSError as exc:
                self.status.emit(f"announce bind failed: {exc}")
                if sock is not None:
                    try:
                        sock.close()
                    except OSError:
                        pass
                # Port oft durch aktive AT/Discovery belegt — später erneut versuchen
                for _ in range(20):
                    if self._stop:
                        return
                    time.sleep(0.1)
                continue

            try:
                while not self._stop:
                    try:
                        data, addr = sock.recvfrom(2048)
                    except socket.timeout:
                        continue
                    except OSError:
                        break
                    if not data:
                        continue
                    reply_ip = str(addr[0] if addr else "")
                    try:
                        devices = parse_dk8de_announce_datagram(data, reply_ip)
                    except Exception:
                        continue
                    now = time.monotonic()
                    for dev in devices:
                        if not isinstance(dev, Dk8deDevice):
                            continue
                        uid = dev.uid_norm
                        if not uid:
                            continue
                        last = self._last_emit_mono.get(uid, 0.0)
                        if (now - last) < self._emit_cooldown_s:
                            continue
                        self._last_emit_mono[uid] = now
                        self.module_announced.emit(dev)
            finally:
                try:
                    sock.close()
                except OSError:
                    pass
                sock = None
