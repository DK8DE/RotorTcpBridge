"""Controller Remote USB: Mode-Switch + transparenter Proxy Display↔Rotor.

Ein einziger Einstieg für USB↔RS485: ``apply_mode(want_remote)``.
``update_cfg`` ändert nur Config/COM — kein Auto-Umschalten (sonst Race mit Save/Load).
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any, Deque, FrozenSet, Optional, Tuple

from .hardware_client import HardwareClient, HwRequest
from .rotor_parse_utils import format_deg_param, parse_setposcc_params, parse_setposdg_params
from .rs485_protocol import build, parse

# Software-TX die das Display sehen muss (Soll/Steuerung). Routine-GETs nicht spiegeln.
_MIRROR_TX_CMDS: FrozenSet[str] = frozenset(
    {
        "SETPOSDG",
        "SETPOSCC",
        "STOP",
        "NSTOP",
        "SETREF",
        "SETASELECT",
        "SETCONREMOTE",
        "SETCONTAZID",
        "SETCONTELID",
        "SETCONTID",
    }
)

_FIRE_AND_FORGET_CMDS: FrozenSet[str] = frozenset(
    {
        "SETPOSCC",
        "SETASELECT",
    }
)


class ControllerRemoteUsbProxy:
    """Leitet Telegramme Display↔Rotor weiter und besitzt den Transport-Mode."""

    def __init__(
        self,
        rotor_hw: HardwareClient,
        ctrl_hw: HardwareClient,
        ctrl: Any,
        log: Any,
    ) -> None:
        self.rotor_hw = rotor_hw
        self.ctrl_hw = ctrl_hw
        self.ctrl = ctrl
        self.log = log
        self._active = False
        self._lock = threading.Lock()
        self._switch_lock = threading.Lock()
        self._dedup: Deque[Tuple[str, float]] = deque(maxlen=96)
        self._dedup_s = 1.5
        self._cfg: dict = {}
        # Letzte SETPOSCC-Achse (Slave-ID), falls SETPOSDG ohne ;rotor_id an Bridge-DST kommt.
        self._last_cc_axis_dst: Optional[int] = None

    def is_active(self) -> bool:
        return bool(self._active)

    def _cont_id(self) -> int:
        chw = self._cfg.get("controller_hw") if isinstance(self._cfg.get("controller_hw"), dict) else {}
        try:
            return max(0, min(254, int(chw.get("cont_id", 2) or 2)))
        except Exception:
            return 2

    @staticmethod
    def want_remote(cfg: dict) -> bool:
        chw = cfg.get("controller_hw") if isinstance(cfg.get("controller_hw"), dict) else {}
        return bool(chw.get("enabled", True)) and bool(chw.get("usb_remote", False))

    def update_cfg(self, cfg: dict) -> None:
        """Nur Config + COM-Link — kein Mode-Wechsel."""
        self._cfg = dict(cfg or {})
        cl = self._cfg.get("controller_link") if isinstance(self._cfg.get("controller_link"), dict) else {}
        if isinstance(cl, dict) and "no_rx_timeout_s" not in cl:
            cl = dict(cl)
            cl["no_rx_timeout_s"] = 0
            self._cfg["controller_link"] = cl
        try:
            self.ctrl_hw.update_cfg(cl or {})
        except Exception as exc:
            try:
                self.log.write("WARN", f"Controller-Link update_cfg: {exc}")
            except Exception:
                pass
        if self._active:
            try:
                chw = self._cfg.get("controller_hw") or {}
                self.ctrl.set_controller_link(self.ctrl_hw, True)
                cont_id = int(chw.get("cont_id", 2) or 2)
                setter = getattr(self.ctrl, "set_controller_cont_id", None)
                if callable(setter):
                    setter(cont_id)
            except Exception:
                pass

    def apply_mode(self, want_remote: bool, timeout_s: float = 8.0) -> bool:
        """Idempotent USB↔RS485 umschalten und auf Ziel-Link warten."""
        with self._switch_lock:
            if want_remote:
                return self._enter_remote(timeout_s)
            return self._leave_remote(timeout_s)

    # --- Kompatibilität für bestehende Aufrufer ---------------------------------

    def activate(self) -> bool:
        return self.apply_mode(True, timeout_s=8.0)

    def stop(self, send_remote_off: bool = False) -> None:
        """USB-Proxy beenden.

        send_remote_off=False (App-Ende): Gerät bleibt im Remote-USB-Modus → „Warten auf PC“.
        send_remote_off=True (Haken aus): SETCONREMOTE:0 und zurück auf RS485.
        """
        if send_remote_off:
            self.apply_mode(False, timeout_s=8.0)
        else:
            self._teardown_proxy(close_usb=True)

    def wait_config_ready(self, timeout_s: float = 5.0) -> bool:
        return self.wait_ready(remote=True, timeout_s=timeout_s)

    def send_remote_on(self) -> bool:
        return self._setconremote(self.rotor_hw, 1, via="Bus")

    def send_remote_off(self) -> bool:
        """SETCONREMOTE:0 über USB (öffnet Link kurz, falls nötig)."""
        with self._switch_lock:
            opened = self._ensure_usb_open(timeout_s=4.0)
            if not opened:
                return False
            try:
                self.ctrl.set_controller_link(self.ctrl_hw, True)
            except Exception:
                pass
            ok = self._setconremote(self.ctrl_hw, 0, via="USB")
            if not self._active:
                self._teardown_proxy(close_usb=True)
            return ok

    # --- Mode-Switch -----------------------------------------------------------

    def _enter_remote(self, timeout_s: float) -> bool:
        if self._active and self._probe(self.ctrl_hw, timeout_s=0.6):
            return True

        self._start_proxy()
        if not self._wait_hw_connected(self.ctrl_hw, min(4.0, timeout_s)):
            try:
                self.log.write("WARN", "Remote USB: Controller-COM nicht bereit")
            except Exception:
                pass

        try:
            self.ctrl_hw.clear_pending("enter_remote")
        except Exception:
            pass

        bus_ok = False
        try:
            if bool(self.rotor_hw.is_connected()):
                bus_ok = self._setconremote(self.rotor_hw, 1, via="Bus")
        except Exception:
            bus_ok = False

        try:
            self.ctrl.set_controller_link(self.ctrl_hw, True)
        except Exception:
            pass

        usb_ok = self._setconremote(self.ctrl_hw, 1, via="USB")
        if not bus_ok and not usb_ok:
            time.sleep(0.25)
            usb_ok = self._setconremote(self.ctrl_hw, 1, via="USB")

        # Controller speichert + legt RS485-TX hochohmig — kurz warten, dann USB-Probe.
        time.sleep(0.35)
        ready = self.wait_ready(remote=True, timeout_s=max(2.0, float(timeout_s) - 1.0))
        try:
            self.log.write(
                "INFO" if ready else "WARN",
                f"Remote USB enter: bus={bus_ok} usb={usb_ok} ready={ready}",
            )
        except Exception:
            pass
        if not ready:
            # Proxy bleibt aktiv (USB lauscht), damit Relink nachziehen kann —
            # aber Aufrufer sieht False und schreibt keine SETs.
            pass
        return bool(ready)

    def _leave_remote(self, timeout_s: float) -> bool:
        # Schon Bus und Proxy aus: nur kurz Bus prüfen.
        if (not self._active) and self._probe(self.rotor_hw, timeout_s=0.6):
            try:
                self.ctrl.set_controller_link(self.ctrl_hw, False)
            except Exception:
                pass
            return True

        sent = False
        if self._active or getattr(self.ctrl_hw, "_running", False):
            if self._ensure_usb_open(timeout_s=min(4.0, timeout_s)):
                try:
                    self.ctrl.set_controller_link(self.ctrl_hw, True)
                except Exception:
                    pass
                try:
                    self.ctrl_hw.clear_pending("leave_remote")
                except Exception:
                    pass
                sent = self._setconremote(self.ctrl_hw, 0, via="USB")
                # UART-Reinit auf dem Gerät (serial_bridge::set_remote_usb(false))
                time.sleep(0.45)

        if not sent:
            # USB schon tot / kein ACK: Gerät empfängt im Remote-Mode oft noch Bus-RX.
            try:
                if bool(self.rotor_hw.is_connected()):
                    sent = self._setconremote(self.rotor_hw, 0, via="Bus")
                    if sent:
                        time.sleep(0.45)
            except Exception:
                pass

        self._teardown_proxy(close_usb=True)

        try:
            self.ctrl.set_controller_link(self.ctrl_hw, False)
        except Exception:
            pass

        # Bus muss wieder antworten — sonst Speichern/Lesen → rot + Write-Fail.
        ready = self.wait_ready(remote=False, timeout_s=max(2.0, float(timeout_s) - 1.0))
        try:
            self.log.write(
                "INFO" if ready else "WARN",
                f"Remote USB leave: set0={sent} bus_ready={ready}",
            )
        except Exception:
            pass
        return bool(ready)

    def wait_ready(self, *, remote: bool, timeout_s: float = 5.0) -> bool:
        """Ziel-Link antwortet auf GETCONTID (ein Befehl, kurze Timeouts)."""
        hw = self.ctrl_hw if remote else self.rotor_hw
        if remote:
            try:
                self.ctrl.set_controller_link(self.ctrl_hw, True)
            except Exception:
                pass
        else:
            try:
                self.ctrl.set_controller_link(self.ctrl_hw, False)
            except Exception:
                pass
        deadline = time.time() + max(0.8, float(timeout_s))
        while time.time() < deadline:
            if not self._wait_hw_connected(hw, 0.6):
                time.sleep(0.12)
                continue
            if self._probe(hw, timeout_s=0.7):
                return True
            time.sleep(0.18)
        return False

    def _probe(self, hw: HardwareClient, timeout_s: float = 0.7) -> bool:
        """Ein schneller GETCONTID — kein Pending-Flush, keine Doppel-GETs."""
        r = self._sync_cmd(
            hw,
            self._cont_id(),
            "GETCONTID",
            "0",
            "ACK_GETCONTID",
            float(timeout_s),
            clear_pending=False,
        )
        return r is not None and not str(r).startswith("NAK")

    def _setconremote(self, hw: HardwareClient, value: int, *, via: str) -> bool:
        r = self._sync_cmd(
            hw,
            self._cont_id(),
            "SETCONREMOTE",
            str(int(value)),
            "ACK_SETCONREMOTE",
            1.5,
            clear_pending=False,
        )
        ok = r is not None and not str(r).startswith("NAK")
        try:
            self.log.write(
                "INFO" if ok else "WARN",
                f"SETCONREMOTE:{int(value)} ({via}) → {'OK' if ok else repr(r)}",
            )
        except Exception:
            pass
        return bool(ok)

    def _sync_cmd(
        self,
        hw: HardwareClient,
        dst: int,
        cmd: str,
        params: str,
        expect_prefix: str,
        timeout_s: float = 1.5,
        *,
        clear_pending: bool = False,
    ) -> Optional[str]:
        """Blockierendes Sync ohne Qt (Worker-Threads)."""
        try:
            mid = int(getattr(self.ctrl, "master_id", 0) or 0)
        except Exception:
            mid = 0
        if mid <= 0:
            mid = 2
        line = build(int(mid), int(dst), str(cmd), str(params or "0"))
        done = threading.Event()
        box: list = [None]

        def _on_done(tel: Any, err: Any) -> None:
            try:
                if err:
                    box[0] = None
                elif tel is not None:
                    cmd_u = str(getattr(tel, "cmd", "") or "").strip().upper()
                    exp = str(expect_prefix or "").strip().upper()
                    if cmd_u.startswith("NAK_"):
                        box[0] = f"NAK:{getattr(tel, 'params', '')}"
                    elif exp and cmd_u.startswith(exp):
                        box[0] = str(getattr(tel, "params", "") or "")
                    elif not exp:
                        box[0] = str(getattr(tel, "params", "") or "")
            finally:
                done.set()

        if clear_pending:
            try:
                hw.clear_pending("before_proxy_sync")
            except Exception:
                pass
        try:
            hw.send_request(
                HwRequest(
                    line=line,
                    expect_prefix=str(expect_prefix),
                    timeout_s=float(timeout_s),
                    on_done=_on_done,
                    priority=0,
                    wait_for_reply=True,
                    dont_disconnect_on_timeout=True,
                )
            )
        except Exception:
            return None
        if not done.wait(float(timeout_s) + 0.5):
            try:
                hw.clear_pending("proxy_sync_timeout")
            except Exception:
                pass
            return None
        return box[0]

    def _wait_hw_connected(self, hw: HardwareClient, timeout_s: float) -> bool:
        deadline = time.time() + max(0.2, float(timeout_s))
        while time.time() < deadline:
            try:
                if bool(hw.is_connected()) and not bool(
                    getattr(hw, "_in_safe_reconnect_mode", lambda: False)()
                ):
                    return True
            except Exception:
                pass
            time.sleep(0.08)
        try:
            return bool(hw.is_connected())
        except Exception:
            return False

    def _ensure_usb_open(self, timeout_s: float = 4.0) -> bool:
        cl = self._cfg.get("controller_link") if isinstance(self._cfg.get("controller_link"), dict) else {}
        if cl:
            try:
                self.ctrl_hw.update_cfg(cl)
            except Exception:
                pass
        try:
            if not getattr(self.ctrl_hw, "_running", False):
                self.ctrl_hw.start()
        except Exception:
            return False
        return self._wait_hw_connected(self.ctrl_hw, timeout_s)

    def _start_proxy(self) -> None:
        if not self._active:
            try:
                if not getattr(self.ctrl_hw, "_running", False):
                    self.ctrl_hw.start()
            except Exception as exc:
                try:
                    self.log.write("WARN", f"Controller-Link start: {exc}")
                except Exception:
                    pass
            self.ctrl_hw.on_rx_line = self.on_ctrl_rx_line
            self.ctrl_hw.on_tx_line = None
            self.rotor_hw.on_rx_line = self.on_rotor_rx_line
            self.rotor_hw.on_tx_line = self.on_rotor_tx_line
            self._active = True
            try:
                self.log.write("INFO", "Controller Remote USB aktiv")
            except Exception:
                pass
        try:
            chw = self._cfg.get("controller_hw") or {}
            self.ctrl.set_controller_link(self.ctrl_hw, True)
            cont_id = int(chw.get("cont_id", 2) or 2)
            setter = getattr(self.ctrl, "set_controller_cont_id", None)
            if callable(setter):
                setter(cont_id)
        except Exception:
            pass

    def _teardown_proxy(self, *, close_usb: bool) -> None:
        was = self._active
        self._active = False
        if self.ctrl_hw.on_rx_line is self.on_ctrl_rx_line:
            self.ctrl_hw.on_rx_line = None
        if self.rotor_hw.on_rx_line is self.on_rotor_rx_line:
            self.rotor_hw.on_rx_line = None
        if self.rotor_hw.on_tx_line is self.on_rotor_tx_line:
            self.rotor_hw.on_tx_line = None
        try:
            self.ctrl.set_controller_link(self.ctrl_hw, False)
        except Exception:
            pass
        if close_usb:
            try:
                self.ctrl_hw.stop()
            except Exception:
                pass
        with self._lock:
            self._dedup.clear()
        if was:
            try:
                self.log.write("INFO", "Controller Remote USB aus")
            except Exception:
                pass

    def start(self) -> None:
        """Proxy-Hooks ohne SETCONREMOTE (nur intern / Kompatibilität)."""
        self._start_proxy()

    # --- Forwarding ------------------------------------------------------------

    def _note_dedup(self, line: str) -> None:
        s = str(line or "").strip()
        if not s:
            return
        now = time.time()
        with self._lock:
            self._dedup.append((s, now))
            while self._dedup and (now - self._dedup[0][1]) > self._dedup_s:
                self._dedup.popleft()

    def _is_dedup(self, line: str) -> bool:
        s = str(line or "").strip()
        if not s:
            return False
        now = time.time()
        with self._lock:
            while self._dedup and (now - self._dedup[0][1]) > self._dedup_s:
                self._dedup.popleft()
            for item, _ts in self._dedup:
                if item == s:
                    return True
        return False

    def _mirror_to_display(self, line: str) -> None:
        s = str(line or "").strip()
        if not s:
            return
        try:
            tel = parse(s)
            cont_id = self._cont_id()
            try:
                mid = int(getattr(self.ctrl, "master_id", 0) or 0)
            except Exception:
                mid = 0
            if (
                tel is not None
                and bool(getattr(tel, "ok", False))
                and cont_id > 0
                and int(tel.dst) == mid
                and mid != cont_id
            ):
                cmd_u = str(tel.cmd or "").strip().upper()
                if cmd_u.startswith("ACK_") or cmd_u.startswith("NAK_") or cmd_u == "ERR":
                    s = build(int(tel.src), cont_id, str(tel.cmd), str(tel.params or ""))
        except Exception:
            pass
        if self._is_dedup(s):
            return
        self._note_dedup(s)
        try:
            self.ctrl_hw.send_line_fire_and_forget(s)
        except Exception:
            pass

    def _should_forward_ctrl_to_rotor(self, line: str) -> bool:
        if self._is_dedup(line):
            return False
        tel = parse(line)
        if tel is None or not bool(getattr(tel, "ok", False)):
            return False
        cmd_u = str(tel.cmd or "").strip().upper()
        if not cmd_u or cmd_u.startswith("ACK_") or cmd_u.startswith("NAK_"):
            return False
        try:
            src = int(tel.src)
        except Exception:
            return False
        cont_id = self._cont_id()
        if cont_id > 0 and src != cont_id:
            return False
        try:
            if bool(self.ctrl.foreign_master_yield_active()):
                if cmd_u.startswith("GET") or cmd_u in ("SETPWM", "TEST"):
                    return False
        except Exception:
            pass
        return True

    def _rewrite_ctrl_line_for_rotor(self, line: str) -> Tuple[str, Any]:
        """Panel-Telegramme an Bridge-DST → Slave-DST umschreiben (Remote-USB).

        Display sendet oft ``#2:1:SETPOSCC:151,30;20`` / ``SETPOSDG`` an die Software-ID.
        Unverändert auf RS485 weitergeleitet ignoriert der Rotor das — keine Fahrt.
        """
        tel = parse(line)
        if tel is None or not bool(getattr(tel, "ok", False)):
            return line, tel
        cmd_u = str(tel.cmd or "").strip().upper()
        if cmd_u not in ("SETPOSDG", "SETPOSCC", "STOP", "NSTOP", "SETREF"):
            return line, tel
        try:
            saz = int(getattr(self.ctrl, "slave_az", 0) or 0)
            sel = int(getattr(self.ctrl, "slave_el", 0) or 0)
            mid = int(getattr(self.ctrl, "master_id", 0) or 0)
            dst = int(tel.dst)
            src = int(tel.src)
        except Exception:
            return line, tel
        if dst in (saz, sel) and saz > 0:
            if cmd_u == "SETPOSCC":
                self._last_cc_axis_dst = dst
            return line, tel

        new_dst: Optional[int] = None
        new_params = str(tel.params or "")
        resolver = getattr(self.ctrl, "resolve_panel_axis_dst", None)
        if callable(resolver):
            try:
                new_dst = resolver(dst=dst, params=new_params, cmd=cmd_u)
            except Exception:
                new_dst = None
        if new_dst is None:
            rid = None
            try:
                if cmd_u == "SETPOSDG":
                    _, rid = parse_setposdg_params(new_params)
                elif cmd_u == "SETPOSCC":
                    _, rid = parse_setposcc_params(new_params)
            except Exception:
                rid = None
            if rid in (saz, sel):
                new_dst = int(rid)
            elif dst == mid or dst == self._cont_id():
                if cmd_u == "SETPOSDG" and self._last_cc_axis_dst in (saz, sel):
                    new_dst = int(self._last_cc_axis_dst)
                elif bool(getattr(self.ctrl, "enable_az", True)) and saz > 0:
                    new_dst = saz
                elif bool(getattr(self.ctrl, "enable_el", False)) and sel > 0:
                    new_dst = sel
        if new_dst is None or int(new_dst) == dst:
            return line, tel

        if cmd_u == "SETPOSCC":
            self._last_cc_axis_dst = int(new_dst)
        if cmd_u == "SETPOSDG":
            try:
                ang, _rid = parse_setposdg_params(new_params)
                if ang is not None:
                    new_params = format_deg_param(ang)
            except Exception:
                pass
        try:
            out = build(src, int(new_dst), cmd_u, new_params)
            out_tel = parse(out)
            return out, out_tel if out_tel is not None else tel
        except Exception:
            return line, tel

    def _forward_to_rotor(self, line: str, cmd_u: str) -> None:
        self._note_dedup(line)
        try:
            if cmd_u in _FIRE_AND_FORGET_CMDS:
                self.rotor_hw.send_line_fire_and_forget(line)
            else:
                prio = 0 if cmd_u in ("SETPOSDG", "STOP", "NSTOP", "SETREF") else 1
                timeout = 1.5 if cmd_u == "SETPOSDG" else 0.8
                self.rotor_hw.send_request(
                    HwRequest(
                        line=line,
                        expect_prefix=None,
                        wait_for_reply=True,
                        timeout_s=timeout,
                        priority=prio,
                        dont_disconnect_on_timeout=True,
                    )
                )
        except Exception:
            pass

    def on_ctrl_rx_line(self, raw: str) -> None:
        if not self._active:
            return
        line = str(raw or "").strip()
        if not line or not self._should_forward_ctrl_to_rotor(line):
            return
        line, tel = self._rewrite_ctrl_line_for_rotor(line)
        cmd_u = str(tel.cmd or "").strip().upper() if tel is not None else ""
        if not cmd_u:
            tel2 = parse(line)
            cmd_u = str(tel2.cmd or "").strip().upper() if tel2 is not None else ""
            if tel is None:
                tel = tel2
        self._forward_to_rotor(line, cmd_u)
        try:
            if tel is not None and hasattr(self.ctrl, "on_controller_link_telegram"):
                self.ctrl.on_controller_link_telegram(tel)
        except Exception:
            pass

    def on_rotor_rx_line(self, raw: str) -> None:
        if not self._active:
            return
        self._mirror_to_display(raw)

    def on_rotor_tx_line(self, raw: str, seq: int = 0) -> None:
        if not self._active:
            return
        line = str(raw or "").strip()
        if not line:
            return
        tel = parse(line)
        if tel is None:
            return
        cmd_u = str(tel.cmd or "").strip().upper()
        if cmd_u not in _MIRROR_TX_CMDS:
            return
        self._mirror_to_display(line)
