"""Browser-Relay: Display-USB (Web Serial) ↔ Rotor-Bus über den Map-Webserver.

Der Display läuft im Modus WEB wie bei Controller Remote USB (RS485-TX stumm).
Der Browser liest Display-Frames per USB und schickt sie per ``POST /api/bus`` an die
Bridge; Rotor-Antworten/Soll-Spiegelung kommen in der HTTP-Antwort zurück und werden
vom Browser auf den Display-USB geschrieben.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any, Callable, Deque, List, Optional, Tuple

from .controller_remote_usb import ControllerRemoteUsbProxy
from .hardware_client import HwRequest
from .rs485_protocol import Telegram, parse


class _BrowserCtrlLink:
    """HardwareClient-Ersatz für den Display, der über den Browser erreichbar ist."""

    def __init__(self) -> None:
        self.relay: Optional["BrowserBusRelay"] = None
        self.on_rx_line: Optional[Callable[[str], None]] = None
        self.on_tx_line: Optional[Callable[..., None]] = None
        self._running = True
        self._lock = threading.Lock()
        self._pending: List[Tuple[HwRequest, float]] = []

    # --- HardwareClient-Teilmenge, die Proxy/Controller benötigen -------------

    def start(self) -> None:
        return

    def stop(self) -> None:
        return

    def update_cfg(self, _cfg: dict) -> None:
        return

    def set_expected_response_dst(self, _dst: int) -> None:
        return

    def _in_safe_reconnect_mode(self) -> bool:
        return False

    def is_connected(self) -> bool:
        r = self.relay
        return bool(r is not None and r.is_active())

    def clear_pending(self, reason: str = "clear") -> None:
        with self._lock:
            items = list(self._pending)
            self._pending.clear()
        for req, _dl in items:
            self._finish(req, None, reason)

    def send_line_fire_and_forget(self, line: str) -> None:
        r = self.relay
        if r is not None:
            r.push_to_display(line)

    def send_request(self, req: HwRequest) -> None:
        r = self.relay
        if r is None:
            self._finish(req, None, "no_relay")
            return
        deadline = time.time() + max(0.2, float(getattr(req, "timeout_s", 1.0) or 1.0))
        with self._lock:
            self._pending.append((req, deadline))
        r.push_to_display(str(req.line))

    # --- Antworten vom Display (über Browser) ----------------------------------

    def resolve_reply(self, tel: Telegram) -> None:
        cmd_u = str(tel.cmd or "").strip().upper()
        hit: Optional[HwRequest] = None
        with self._lock:
            for i, (req, _dl) in enumerate(self._pending):
                exp = str(getattr(req, "expect_prefix", "") or "").strip().upper()
                if cmd_u.startswith("NAK_") or (exp and cmd_u.startswith(exp)) or not exp:
                    hit = req
                    del self._pending[i]
                    break
        if hit is not None:
            self._finish(hit, tel, None)

    def sweep(self) -> None:
        now = time.time()
        expired: List[HwRequest] = []
        with self._lock:
            keep: List[Tuple[HwRequest, float]] = []
            for req, dl in self._pending:
                if dl <= now:
                    expired.append(req)
                else:
                    keep.append((req, dl))
            self._pending = keep
        for req in expired:
            self._finish(req, None, "timeout")

    @staticmethod
    def _finish(req: HwRequest, tel: Optional[Telegram], err: Optional[str]) -> None:
        cb = getattr(req, "on_done", None)
        if cb is None:
            return
        try:
            cb(tel, err)
        except Exception:
            pass


class BrowserBusRelay(ControllerRemoteUsbProxy):
    """Nutzt die Remote-USB-Proxy-Logik, aber der Display-Link ist ein Browser."""

    IDLE_DETACH_S = 12.0
    OUT_MAX = 600

    def __init__(
        self,
        rotor_hw: Any,
        ctrl: Any,
        log: Any,
        *,
        cfg_getter: Callable[[], dict],
        desktop_proxy_getter: Optional[Callable[[], Any]] = None,
    ) -> None:
        self._link = _BrowserCtrlLink()
        super().__init__(rotor_hw, self._link, ctrl, log)  # type: ignore[arg-type]
        self._link.relay = self
        self._cfg_getter = cfg_getter
        self._desktop_getter = desktop_proxy_getter
        self._io = threading.Lock()
        self._attach_lock = threading.Lock()
        self._out: Deque[Tuple[int, str]] = deque(maxlen=self.OUT_MAX)
        self._seq = 0
        self._last_seen = 0.0
        self._watch: Optional[threading.Thread] = None
        # Wird nach jedem (Neu-)Attach aufgerufen (Browser verbindet sich).
        self.on_attached: Optional[Callable[[], None]] = None

    # --- Display-Seite ---------------------------------------------------------

    def push_to_display(self, line: str) -> None:
        s = str(line or "").strip()
        if not s:
            return
        with self._io:
            self._seq += 1
            self._out.append((self._seq, s))

    def _from_display(self, line: str) -> None:
        s = str(line or "").strip()
        if not s:
            return
        tel = parse(s)
        if tel is None:
            return
        cmd_u = str(tel.cmd or "").strip().upper()
        if cmd_u.startswith("ACK_") or cmd_u.startswith("NAK_"):
            self._link.resolve_reply(tel)
            return
        self.on_ctrl_rx_line(s)

    # --- Lebenszyklus ----------------------------------------------------------

    def _attach(self) -> None:
        try:
            cfg = self._cfg_getter() or {}
            self.update_cfg(cfg)
        except Exception:
            pass
        with self._io:
            self._out.clear()
        self._start_proxy()
        self._last_seen = time.time()
        if self._watch is None or not self._watch.is_alive():
            self._watch = threading.Thread(
                target=self._watch_loop, name="browser-bus-relay", daemon=True
            )
            self._watch.start()
        try:
            self.log.write("INFO", "Browser-Bus-Relay aktiv (Display via Web Serial)")
        except Exception:
            pass
        cb = self.on_attached
        if callable(cb):
            try:
                cb()
            except Exception:
                pass

    def detach(self) -> None:
        was = self._active
        self._teardown_proxy(close_usb=False)
        self._link.clear_pending("detach")
        for hw_attr in ("on_rx_line", "on_tx_line"):
            cur = getattr(self.rotor_hw, hw_attr, None)
            if getattr(cur, "__self__", None) is self:
                try:
                    setattr(self.rotor_hw, hw_attr, None)
                except Exception:
                    pass
        with self._io:
            self._out.clear()
        if was:
            try:
                self.log.write("INFO", "Browser-Bus-Relay aus")
            except Exception:
                pass

    def _watch_loop(self) -> None:
        while self._active:
            time.sleep(0.3)
            try:
                self._link.sweep()
            except Exception:
                pass
            if (time.time() - self._last_seen) > self.IDLE_DETACH_S:
                self.detach()
                return

    # --- HTTP-Einstieg ---------------------------------------------------------

    def handle(self, payload: dict) -> dict:
        payload = payload if isinstance(payload, dict) else {}
        if payload.get("disconnect"):
            self.detach()
            return {"ok": True, "active": False, "rx": [], "seq": self._seq}

        desktop = self._desktop_getter() if callable(self._desktop_getter) else None
        try:
            if desktop is not None and bool(desktop.is_active()):
                return {
                    "ok": False,
                    "error": "desktop_remote_usb_active",
                    "active": False,
                    "rx": [],
                    "seq": self._seq,
                }
        except Exception:
            pass

        with self._attach_lock:
            if not self._active:
                self._attach()
        self._last_seen = time.time()

        tx = payload.get("tx")
        if isinstance(tx, list):
            for line in tx[:64]:
                try:
                    self._from_display(str(line))
                except Exception:
                    pass

        try:
            since = int(payload.get("since") or 0)
        except Exception:
            since = 0
        with self._io:
            if since > self._seq:
                since = 0
            rx = [{"seq": s, "line": ln} for s, ln in self._out if s > since]
            seq = self._seq
        return {"ok": True, "active": bool(self._active), "rx": rx, "seq": seq}


__all__ = ["BrowserBusRelay"]
