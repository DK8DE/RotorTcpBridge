"""Leichter Multi-Standort-Monitor für Rotorübersicht (nur Lesen, kein SET*)."""

from __future__ import annotations

import socket
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from .angle_utils import wrap_deg
from .rs485_protocol import build, parse


@dataclass
class SiteLiveState:
    site_id: str
    online: bool = False
    referenced: Optional[bool] = None
    az_deg: Optional[float] = None
    az_smooth_deg: Optional[float] = None
    last_rx_ts: float = 0.0
    last_error: str = ""


class _SiteWorker:
    def __init__(
        self,
        site: dict,
        on_update: Callable[[SiteLiveState], None],
        stop_event: threading.Event,
    ):
        self.site = dict(site)
        self.on_update = on_update
        self._stop = stop_event
        self.state = SiteLiveState(site_id=str(site.get("id") or ""))
        self._sock: Optional[socket.socket] = None
        self._udp: Optional[socket.socket] = None
        self._rxbuf = b""
        self._last_pos_poll = 0.0
        self._last_ref_poll = 0.0

    def _emit(self) -> None:
        try:
            self.on_update(self.state)
        except Exception:
            pass

    def _close(self) -> None:
        for s in (self._sock, self._udp):
            if s is not None:
                try:
                    s.close()
                except Exception:
                    pass
        self._sock = None
        self._udp = None
        self._rxbuf = b""

    def _connect_tcp(self) -> bool:
        host = str(self.site.get("host") or "").strip()
        port = int(self.site.get("port") or 0)
        if not host or port <= 0:
            self.state.last_error = "host/port"
            return False
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(2.0)
        try:
            s.connect((host, port))
            s.settimeout(0.25)
            self._sock = s
            self.state.online = True
            self.state.last_error = ""
            self._emit()
            return True
        except Exception as exc:
            try:
                s.close()
            except Exception:
                pass
            self.state.online = False
            self.state.last_error = str(exc)
            self._emit()
            return False

    def _bind_udp(self) -> bool:
        host = str(self.site.get("host") or "").strip() or "0.0.0.0"
        port = int(self.site.get("port") or 0)
        bind_port = int(self.site.get("udp_bind_port") or 0) or port
        if bind_port <= 0:
            self.state.last_error = "udp port"
            return False
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("0.0.0.0", bind_port))
            s.settimeout(0.25)
            self._udp = s
            # Ziel für optionale GET (nicht in v1 für UDP-Poll)
            self._udp_peer = (host, port)
            self.state.online = True
            self.state.last_error = ""
            self._emit()
            return True
        except Exception as exc:
            try:
                s.close()
            except Exception:
                pass
            self.state.online = False
            self.state.last_error = str(exc)
            self._emit()
            return False

    def _send_tcp(self, line: str) -> None:
        if self._sock is None:
            return
        data = (line if line.endswith("$") else line) + ("\n" if not line.endswith("\n") else "")
        # Protokoll endet mit $; viele Gateways brauchen kein \n — sende Rohtelegramm
        raw = line.encode("ascii", errors="ignore")
        if not raw.endswith(b"$"):
            raw += b"$"
        try:
            self._sock.sendall(raw)
        except Exception as exc:
            self.state.online = False
            self.state.last_error = str(exc)
            self._close()
            self._emit()

    def _poll_tcp(self, now: float) -> None:
        mid = int(self.site.get("master_id") or 0)
        dst = int(self.site.get("slave_az") or 0)
        if dst <= 0:
            return
        pos_ms = max(200, int(self.site.get("poll_pos_ms") or 1000))
        ref_ms = max(1000, int(self.site.get("poll_ref_ms") or 5000))
        if now - self._last_pos_poll >= pos_ms / 1000.0:
            self._last_pos_poll = now
            self._send_tcp(build(mid, dst, "GETPOSDG", "0"))
        if now - self._last_ref_poll >= ref_ms / 1000.0:
            self._last_ref_poll = now
            self._send_tcp(build(mid, dst, "GETREF", "0"))

    def _apply_az(self, deg: float) -> None:
        deg = float(wrap_deg(deg))
        self.state.az_deg = deg
        alpha = float(self.site.get("smooth_alpha") or 0.35)
        alpha = max(0.05, min(1.0, alpha))
        if self.state.az_smooth_deg is None:
            self.state.az_smooth_deg = deg
        else:
            # kürzester Weg glätten
            cur = float(self.state.az_smooth_deg)
            d = ((deg - cur + 540.0) % 360.0) - 180.0
            self.state.az_smooth_deg = wrap_deg(cur + alpha * d)
        self.state.last_rx_ts = time.time()
        self.state.online = True
        self._emit()

    def _handle_telegram(self, line: str) -> None:
        tel = parse(line)
        if tel is None or not tel.ok:
            return
        dst_az = int(self.site.get("slave_az") or 0)
        # Nur Telegramme vom/zum AZ-Slave
        if int(tel.src) != dst_az and int(tel.dst) != dst_az:
            return
        cmd = str(tel.cmd or "").upper()
        params = str(tel.params or "")
        if cmd.startswith("ACK_GETPOSDG") or cmd == "SETPOSDG" or cmd.startswith("ACK_SETPOSDG"):
            # erster Winkelteil
            part = params.split(";")[0].strip().replace(",", ".")
            try:
                # d10 oder Grad mit Komma — Firmware oft "123,4"
                if "." in part or "," in params.split(";")[0]:
                    # Grad
                    deg = float(part)
                    if deg > 720:  # eher d10 ohne Dezimal
                        deg = deg / 10.0
                else:
                    # könnte d10 ganzzahlig sein
                    v = float(part)
                    deg = v / 10.0 if v > 720 else v
            except (TypeError, ValueError):
                return
            self._apply_az(deg)
        elif cmd.startswith("ACK_GETREF"):
            try:
                v = int(float(str(params).split(";")[0].replace(",", ".")))
                self.state.referenced = bool(v != 0)
                self.state.last_rx_ts = time.time()
                self.state.online = True
                self._emit()
            except (TypeError, ValueError):
                pass

    def _feed_rx(self, data: bytes) -> None:
        if not data:
            return
        self._rxbuf += data
        while True:
            start = self._rxbuf.find(b"#")
            if start < 0:
                self._rxbuf = b""
                return
            if start > 0:
                self._rxbuf = self._rxbuf[start:]
            end = self._rxbuf.find(b"$")
            if end < 0:
                if len(self._rxbuf) > 4096:
                    self._rxbuf = self._rxbuf[-512:]
                return
            chunk = self._rxbuf[: end + 1]
            self._rxbuf = self._rxbuf[end + 1 :]
            try:
                line = chunk.decode("ascii", errors="ignore")
            except Exception:
                continue
            self._handle_telegram(line)

    def _recv_once(self) -> None:
        try:
            if self._sock is not None:
                data = self._sock.recv(4096)
                if not data:
                    self.state.online = False
                    self.state.last_error = "closed"
                    self._close()
                    self._emit()
                    return
                self._feed_rx(data)
            elif self._udp is not None:
                data, _addr = self._udp.recvfrom(4096)
                self._feed_rx(data)
        except socket.timeout:
            pass
        except Exception as exc:
            self.state.online = False
            self.state.last_error = str(exc)
            self._close()
            self._emit()

    def run(self) -> None:
        mode = str(self.site.get("mode") or "tcp").lower()
        while not self._stop.is_set():
            if mode == "udp":
                if self._udp is None and not self._bind_udp():
                    self._stop.wait(2.0)
                    continue
            else:
                if self._sock is None and not self._connect_tcp():
                    self._stop.wait(2.0)
                    continue
            now = time.time()
            if mode == "tcp" and self._sock is not None:
                self._poll_tcp(now)
            self._recv_once()
            # Offline wenn lange kein RX
            if self.state.last_rx_ts > 0 and (now - self.state.last_rx_ts) > 8.0:
                if self.state.online:
                    self.state.online = False
                    self._emit()
            self._stop.wait(0.05)
        self._close()


class RotorOverviewMonitor:
    """Verwaltet Worker-Threads für alle enabled Sites."""

    def __init__(self, on_update: Optional[Callable[[SiteLiveState], None]] = None):
        self._on_update = on_update
        self._lock = threading.Lock()
        self._states: Dict[str, SiteLiveState] = {}
        self._stop = threading.Event()
        self._threads: List[threading.Thread] = []
        self._workers: List[_SiteWorker] = []

    def _handle(self, st: SiteLiveState) -> None:
        with self._lock:
            self._states[st.site_id] = st
        if self._on_update:
            try:
                self._on_update(st)
            except Exception:
                pass

    def get_states(self) -> Dict[str, SiteLiveState]:
        with self._lock:
            return dict(self._states)

    def start(self, sites: List[dict]) -> None:
        self.stop()
        self._stop = threading.Event()
        self._threads = []
        self._workers = []
        for site in sites:
            if not bool(site.get("enabled", True)):
                continue
            w = _SiteWorker(site, self._handle, self._stop)
            self._workers.append(w)
            t = threading.Thread(target=w.run, name=f"overview-{site.get('id')}", daemon=True)
            self._threads.append(t)
            t.start()

    def stop(self) -> None:
        self._stop.set()
        for t in self._threads:
            try:
                t.join(timeout=1.0)
            except Exception:
                pass
        self._threads = []
        self._workers = []
