"""N1MM Logger Rotor-UDP: Empfang Port 12040, Positions-Broadcast 13010."""

from __future__ import annotations

import socket
import threading
import time
import xml.etree.ElementTree as ET
from typing import Optional

from .angle_utils import az_d10_for_external_report
from .logutil import LogBuffer
from .net_utils import normalize_udp_bind_host

DEFAULT_N1MM_LISTEN_PORT = 12040
DEFAULT_N1MM_BROADCAST_PORT = 13010


def parse_n1mm_rotor_xml(
    data: bytes,
    *,
    rotor_name: str = "",
) -> tuple[Optional[float], bool, bool]:
    """N1MM-XML parsen.

    Rückgabe ``(goazi|None, stop, accepted)``.
    ``accepted=False`` wenn Rotorname-Filter greift.
    """
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return (None, False, False)

    tag = (root.tag or "").lower()
    if tag not in ("n1mmrotor", "rotor"):
        # Namespace / Großschreibung tolerieren
        if "n1mmrotor" not in tag and tag != "rotor":
            return (None, False, False)

    want = (rotor_name or "").strip()
    if want:
        rotor_el = root.find("rotor")
        if rotor_el is None:
            rotor_el = root.find("Rotor")
        got = (rotor_el.text or "").strip() if rotor_el is not None else ""
        if got and got.lower() != want.lower():
            return (None, False, False)

    stop = False
    stop_el = root.find("stop")
    if stop_el is None:
        stop_el = root.find("Stop")
    if stop_el is not None and stop_el.text is not None:
        stop = str(stop_el.text).strip().lower() in ("1", "true", "yes", "y")

    goazi: Optional[float] = None
    az_el = root.find("goazi")
    if az_el is None:
        az_el = root.find("goazi".upper()) if False else root.find("Goazi")
    if az_el is None:
        for child in list(root):
            if (child.tag or "").lower() == "goazi":
                az_el = child
                break
    if az_el is not None and az_el.text is not None and str(az_el.text).strip():
        try:
            goazi = float(str(az_el.text).strip())
        except ValueError:
            goazi = None

    return (goazi, stop, True)


def format_n1mm_broadcast(name: str, az_deg: float) -> bytes:
    """``NAME @ heading`` mit heading = Azimut×10 (N1MM-Konvention)."""
    nm = (name or "ROTOR").strip() or "ROTOR"
    heading = int(round(float(az_deg) * 10.0))
    return f"{nm} @ {heading}".encode("ascii", errors="ignore")


class N1mmRotorUdp:
    """UDP-Empfang (N1MM→Rotor) + optionaler Positions-Broadcast."""

    def __init__(self, controller, log: LogBuffer, cfg: Optional[dict] = None):
        self.ctrl = controller
        self.log = log
        self.cfg = cfg
        self._enabled = False
        self._listen_host = "127.0.0.1"
        self._listen_port = DEFAULT_N1MM_LISTEN_PORT
        self._broadcast_host = "127.0.0.1"
        self._broadcast_port = DEFAULT_N1MM_BROADCAST_PORT
        self._rotor_name = ""
        self._sock: Optional[socket.socket] = None
        self._bcast_sock: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._bcast_thread: Optional[threading.Thread] = None
        self._running = False
        self.last_rx_ts: float = 0.0
        self.bind_error_msg: str | None = None
        self.packet_received_flag = False

    @property
    def is_active(self) -> bool:
        return bool(self._enabled and self._running and self._sock is not None)

    @property
    def running(self) -> bool:
        return self.is_active

    def _section(self) -> dict:
        try:
            return (self.cfg or {}).get("n1mm_rotor", {}) or {}
        except Exception:
            return {}

    def _az_shortest_path(self) -> bool:
        try:
            return bool(self._section().get("az_shortest_path", False))
        except Exception:
            return False

    def _az_report_mod360(self) -> bool:
        try:
            return bool(self._section().get("az_report_mod360", False))
        except Exception:
            return False

    def start(
        self,
        *,
        enabled: bool | None = None,
        listen_host: str | None = None,
        listen_port: int | None = None,
        broadcast_host: str | None = None,
        broadcast_port: int | None = None,
        rotor_name: str | None = None,
    ) -> None:
        sec = self._section()
        if enabled is None:
            enabled = bool(sec.get("enabled", False))
        if listen_host is None:
            listen_host = str(sec.get("listen_host", "127.0.0.1"))
        if listen_port is None:
            listen_port = int(sec.get("listen_port", DEFAULT_N1MM_LISTEN_PORT))
        if broadcast_host is None:
            broadcast_host = str(sec.get("broadcast_host", "127.0.0.1"))
        if broadcast_port is None:
            broadcast_port = int(sec.get("broadcast_port", DEFAULT_N1MM_BROADCAST_PORT))
        if rotor_name is None:
            rotor_name = str(sec.get("rotor_name", "") or "")

        self.stop()
        self._enabled = bool(enabled)
        self._listen_host = normalize_udp_bind_host(listen_host, "127.0.0.1")
        self._listen_port = max(1, min(65535, int(listen_port)))
        self._broadcast_host = str(broadcast_host or "127.0.0.1").strip() or "127.0.0.1"
        self._broadcast_port = max(1, min(65535, int(broadcast_port)))
        self._rotor_name = str(rotor_name or "").strip()
        self.bind_error_msg = None
        if not self._enabled:
            return

        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._sock.bind((self._listen_host, self._listen_port))
            self._sock.settimeout(0.5)
            self._running = True
            self._thread = threading.Thread(target=self._loop, daemon=True, name="n1mm-rotor-udp")
            self._thread.start()
            self._bcast_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._bcast_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            self._bcast_thread = threading.Thread(
                target=self._broadcast_loop, daemon=True, name="n1mm-rotor-bcast"
            )
            self._bcast_thread.start()
            self.log.write(
                "INFO",
                f"N1MM Rotor UDP gestartet auf {self._listen_host}:{self._listen_port} "
                f"(Broadcast {self._broadcast_host}:{self._broadcast_port})",
            )
        except OSError as e:
            from .net_bind_error import format_bind_error

            self.bind_error_msg = format_bind_error(
                self._listen_host, self._listen_port, e, proto_name="N1MM Rotor UDP"
            )
            self.log.write("ERROR", f"N1MM Rotor UDP bind fehlgeschlagen: {e}")
            self._running = False
            try:
                if self._sock:
                    self._sock.close()
            except Exception:
                pass
            self._sock = None

    def stop(self) -> None:
        was = self._enabled and self._running
        self._running = False
        try:
            if self._sock:
                self._sock.close()
        except Exception:
            pass
        self._sock = None
        try:
            if self._bcast_sock:
                self._bcast_sock.close()
        except Exception:
            pass
        self._bcast_sock = None
        if self._thread:
            self._thread.join(timeout=1.0)
        self._thread = None
        if self._bcast_thread:
            self._bcast_thread.join(timeout=1.0)
        self._bcast_thread = None
        if was:
            self.log.write("INFO", "N1MM Rotor UDP gestoppt")

    def restart(self) -> None:
        self.start()

    def _apply_command(self, goazi: Optional[float], stop: bool) -> None:
        try:
            if stop:
                if getattr(self.ctrl, "enable_az", True):
                    self.ctrl.hold_az_at_current_pos()
                return
            if goazi is None:
                return
            if getattr(self.ctrl, "enable_az", True):
                self.ctrl.set_az_from_spid(
                    int(round(float(goazi) * 10.0)),
                    shortest_path=self._az_shortest_path(),
                )
        except Exception as exc:
            self.log.write("WARN", f"N1MM Rotor: Befehl fehlgeschlagen: {exc}")

    def _loop(self) -> None:
        while self._running and self._sock is not None:
            try:
                data, _addr = self._sock.recvfrom(4096)
            except socket.timeout:
                continue
            except Exception:
                break
            if not data:
                continue
            goazi, stop, accepted = parse_n1mm_rotor_xml(
                data, rotor_name=self._rotor_name
            )
            if not accepted:
                continue
            try:
                self.last_rx_ts = time.time()
                self.packet_received_flag = True
            except Exception:
                pass
            self._apply_command(goazi, stop)

    def _current_az_deg(self) -> float:
        try:
            raw = (
                int(getattr(self.ctrl.az, "pos_d10", 0) or 0)
                if getattr(self.ctrl, "enable_az", True)
                else 0
            )
            d10 = az_d10_for_external_report(
                raw,
                shortest_path=self._az_shortest_path(),
                report_mod360=self._az_report_mod360(),
            )
            return float(d10) / 10.0
        except Exception:
            return 0.0

    def _broadcast_loop(self) -> None:
        while self._running:
            try:
                if self._bcast_sock is not None:
                    name = self._rotor_name or "ROTOR"
                    payload = format_n1mm_broadcast(name, self._current_az_deg())
                    self._bcast_sock.sendto(
                        payload, (self._broadcast_host, self._broadcast_port)
                    )
            except Exception:
                pass
            for _ in range(10):
                if not self._running:
                    break
                time.sleep(0.1)
