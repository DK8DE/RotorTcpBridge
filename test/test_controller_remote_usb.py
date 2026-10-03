"""Tests für Controller Remote USB Proxy und Link-Routing."""

from __future__ import annotations

from typing import Any, Callable, List, Optional

from rotortcpbridge.controller_remote_usb import ControllerRemoteUsbProxy
from rotortcpbridge.hardware_client import HwRequest
from rotortcpbridge.rs485_protocol import BROADCAST_DST, build, parse
from rotortcpbridge.rotor_controller import RotorController


class _FakeLog:
    def write(self, *_a: Any, **_k: Any) -> None:
        pass


class _FakeHw:
    def __init__(self) -> None:
        self.sent_requests: List[HwRequest] = []
        self.ff_lines: List[str] = []
        self.on_rx_line: Optional[Callable[[str], None]] = None
        self.on_tx_line: Optional[Callable[[str, int], None]] = None
        self._running = False
        self._connected = True
        self.cfg: dict = {}
        self._expected_dst = 0

    def start(self) -> None:
        self._running = True

    def stop(self) -> None:
        self._running = False

    def is_connected(self) -> bool:
        return bool(self._connected)

    def update_cfg(self, cfg: dict) -> None:
        self.cfg = dict(cfg or {})

    def set_expected_response_dst(self, dst: int) -> None:
        self._expected_dst = int(dst)

    def send_request(self, req: HwRequest) -> None:
        self.sent_requests.append(req)
        if self.on_tx_line is not None:
            try:
                self.on_tx_line(str(req.line).strip(), len(self.sent_requests))
            except Exception:
                pass
        # Sync-Probe (GETCONTID / SETCONREMOTE): sofort ACK, sonst hängen activate()-Tests.
        cb = getattr(req, "on_done", None)
        if callable(cb):
            exp = str(getattr(req, "expect_prefix", "") or "").strip().upper()
            if exp:
                tel = parse(build(2, 1, exp if exp.startswith("ACK_") else f"ACK_{exp}", "1"))
                try:
                    cb(tel, None)
                except Exception:
                    pass

    def send_line_fire_and_forget(self, line: str) -> None:
        self.ff_lines.append(str(line).strip())

    def clear_pending(self, *_a: Any, **_k: Any) -> None:
        return None


class _FakeCtrl:
    def __init__(self) -> None:
        self.ctrl_hw = None
        self.usb_remote = False
        self._controller_cont_id = 2
        self.master_id = 0
        self.slave_az = 20
        self.slave_el = 21
        self.enable_az = True
        self.enable_el = True
        self.link_telegrams: List[Any] = []
        self.sync_calls: List[tuple] = []

    def set_controller_link(self, hw: Any, enabled: bool) -> None:
        self.ctrl_hw = hw
        self.usb_remote = bool(enabled) and hw is not None

    def set_controller_cont_id(self, cont_id: int) -> None:
        self._controller_cont_id = int(cont_id)

    def resolve_panel_axis_dst(
        self, *, dst: int, params: str, cmd: str = "SETPOSCC", prefer_last_az: bool = True
    ) -> int | None:
        d = int(dst)
        if d in (self.slave_az, self.slave_el):
            return d
        from rotortcpbridge.rotor_parse_utils import parse_setposcc_params, parse_setposdg_params

        rid = None
        if str(cmd).upper() == "SETPOSDG":
            _, rid = parse_setposdg_params(params)
        else:
            _, rid = parse_setposcc_params(params)
        if rid in (self.slave_az, self.slave_el):
            return int(rid)
        if d == self.master_id or d == self._controller_cont_id:
            return self.slave_az if self.enable_az else self.slave_el
        return None

    def on_controller_link_telegram(self, tel: Any) -> None:
        self.link_telegrams.append(tel)

    def sync_ui_command_response(
        self, dst: int, cmd: str, params: str, ack: str, timeout_s: float = 1.5
    ) -> str:
        self.sync_calls.append((int(dst), str(cmd), str(params), str(ack), float(timeout_s)))
        return "1"


def _active_cfg() -> dict:
    return {
        "controller_hw": {"enabled": True, "usb_remote": True, "cont_id": 2},
        "controller_link": {"mode": "com", "com_port": "COM9", "baudrate": 115200},
    }


def _ready_proxy() -> tuple[ControllerRemoteUsbProxy, _FakeHw, _FakeHw, _FakeCtrl]:
    """Config + Proxy-Start (update_cfg allein schaltet den Mode nicht mehr)."""
    rotor = _FakeHw()
    ctrl_hw = _FakeHw()
    ctrl = _FakeCtrl()
    proxy = ControllerRemoteUsbProxy(rotor, ctrl_hw, ctrl, _FakeLog())  # type: ignore[arg-type]
    proxy.update_cfg(_active_cfg())
    proxy.start()
    assert proxy.is_active()
    assert ctrl.usb_remote is True
    return proxy, rotor, ctrl_hw, ctrl


def test_proxy_forwards_display_to_rotor_and_mirrors_rotor() -> None:
    proxy, rotor, ctrl_hw, ctrl = _ready_proxy()

    line = build(2, 20, "SETPOSDG", "90,00")
    proxy.on_ctrl_rx_line(line)
    assert len(rotor.sent_requests) == 1
    assert rotor.sent_requests[0].line == line
    assert rotor.sent_requests[0].priority == 0
    assert float(rotor.sent_requests[0].timeout_s) >= 1.5
    assert len(ctrl.link_telegrams) == 1

    # Routine-GET der Software wird nicht gespiegelt
    soft_get = build(0, 20, "GETANEMO", "0")
    proxy.on_rotor_tx_line(soft_get, 1)
    assert soft_get not in ctrl_hw.ff_lines

    soft_tx = build(0, 20, "SETPOSDG", "90,00")
    proxy.on_rotor_tx_line(soft_tx, 2)
    assert soft_tx in ctrl_hw.ff_lines

    ack = build(20, 0, "ACK_GETPOSDG", "12,34")
    proxy.on_rotor_rx_line(ack)
    # DST=Software-Master → Cont-ID
    mirrored = build(20, 2, "ACK_GETPOSDG", "12,34")
    assert mirrored in ctrl_hw.ff_lines

    # ACK an fremden Bus-Master (App 7) bleibt unverändert
    ctrl_hw.ff_lines.clear()
    ack7 = build(20, 7, "ACK_GETPOSDG", "12,34")
    proxy.on_rotor_rx_line(ack7)
    assert ack7 in ctrl_hw.ff_lines
    assert build(20, 2, "ACK_GETPOSDG", "12,34") not in ctrl_hw.ff_lines


def test_proxy_blocks_controller_poll_during_foreign_yield() -> None:
    proxy, rotor, _ctrl_hw, ctrl = _ready_proxy()
    ctrl.foreign_master_yield_active = lambda *_a, **_k: True  # type: ignore[method-assign]
    proxy.on_ctrl_rx_line(build(2, 20, "SETPWM", "100"))
    assert rotor.sent_requests == []
    assert rotor.ff_lines == []
    proxy.on_ctrl_rx_line(build(2, 20, "SETPOSDG", "90,00"))
    assert len(rotor.sent_requests) == 1


def test_proxy_setposcc_fire_and_forget() -> None:
    proxy, rotor, _ctrl_hw, ctrl = _ready_proxy()
    line = build(2, 20, "SETPOSCC", "166,70;20")
    proxy.on_ctrl_rx_line(line)
    assert line in rotor.ff_lines
    assert rotor.sent_requests == []
    assert len(ctrl.link_telegrams) == 1


def test_proxy_rewrites_bridge_dst_setposdg_to_slave() -> None:
    """#2:0:SETPOSDG…;20 → Rotor-Slave 20 (sonst keine Fahrt im USB-Remote)."""
    proxy, rotor, _ctrl_hw, ctrl = _ready_proxy()
    proxy.on_ctrl_rx_line(build(2, 0, "SETPOSDG", "151,30;20"))
    assert len(rotor.sent_requests) == 1
    out = parse(rotor.sent_requests[0].line)
    assert out is not None
    assert int(out.dst) == 20
    assert int(out.src) == 2
    assert str(out.cmd).upper() == "SETPOSDG"
    assert "151" in str(out.params)
    assert ";" not in str(out.params)
    assert len(ctrl.link_telegrams) == 1
    assert int(ctrl.link_telegrams[0].dst) == 20


def test_proxy_rewrites_bridge_dst_setposcc_to_slave() -> None:
    proxy, rotor, _ctrl_hw, ctrl = _ready_proxy()
    proxy.on_ctrl_rx_line(build(2, 0, "SETPOSCC", "90,00;20"))
    assert len(rotor.ff_lines) == 1
    out = parse(rotor.ff_lines[0])
    assert out is not None
    assert int(out.dst) == 20
    assert str(out.cmd).upper() == "SETPOSCC"


def test_proxy_dedup_no_echo_of_controller_tx() -> None:
    proxy, _rotor, ctrl_hw, _ctrl = _ready_proxy()
    line = build(2, 20, "GETREF", "0")
    proxy.on_ctrl_rx_line(line)
    before = list(ctrl_hw.ff_lines)
    proxy.on_rotor_tx_line(line, 99)
    assert ctrl_hw.ff_lines == before


def test_proxy_ignores_usb_echo_of_mirrored_rotor_tx() -> None:
    """USB-Echo einer gespiegelten Software-TX darf nicht zurück auf den Rotor."""
    proxy, rotor, ctrl_hw, _ctrl = _ready_proxy()

    soft_tx = build(0, 20, "SETPOSDG", "10,00")
    proxy.on_rotor_tx_line(soft_tx, 1)
    assert soft_tx in ctrl_hw.ff_lines
    n_before = len(rotor.sent_requests) + len(rotor.ff_lines)
    proxy.on_ctrl_rx_line(soft_tx)
    assert len(rotor.sent_requests) + len(rotor.ff_lines) == n_before


def test_proxy_drops_ack_and_garbage_from_controller() -> None:
    proxy, rotor, _ctrl_hw, _ctrl = _ready_proxy()

    proxy.on_ctrl_rx_line(build(20, 0, "ACK_GETPOSDG", "1,00"))
    proxy.on_ctrl_rx_line("#0zWo#garbage$")
    proxy.on_ctrl_rx_line(build(0, 20, "GETREF", "0"))  # falsche SRC
    assert rotor.sent_requests == []


def test_hw_for_dst_routes_controller_cmds() -> None:
    rotor = _FakeHw()
    ctrl_hw = _FakeHw()
    log = _FakeLog()
    rc = RotorController(rotor, 0, 20, 21, log, setposcc_controller_src_id=2)  # type: ignore[arg-type]
    rc.set_controller_link(ctrl_hw, True)  # type: ignore[arg-type]
    assert rc._hw_for_dst(2) is ctrl_hw
    assert rc._hw_for_dst(BROADCAST_DST) is ctrl_hw
    assert rc._hw_for_dst(20) is rotor

    rc.set_controller_link(ctrl_hw, False)  # type: ignore[arg-type]
    assert rc._hw_for_dst(2) is rotor


def test_send_ui_command_uses_controller_link_when_remote() -> None:
    rotor = _FakeHw()
    ctrl_hw = _FakeHw()
    log = _FakeLog()
    rc = RotorController(rotor, 0, 20, 21, log, setposcc_controller_src_id=2)  # type: ignore[arg-type]
    rc.set_controller_link(ctrl_hw, True)  # type: ignore[arg-type]
    rc.send_ui_command(2, "SETCONTAZID", "20", expect_prefix="ACK_SETCONTAZID")
    assert len(ctrl_hw.sent_requests) == 1
    assert "SETCONTAZID" in ctrl_hw.sent_requests[0].line
    assert len(rotor.sent_requests) == 0

    rc.set_controller_link(ctrl_hw, False)  # type: ignore[arg-type]
    rc.send_ui_command(2, "SETCONTAZID", "21", expect_prefix="ACK_SETCONTAZID")
    assert len(rotor.sent_requests) == 1
    assert "SETCONTAZID" in rotor.sent_requests[0].line


def test_activate_sends_bus_setconremote_before_usb() -> None:
    """RS485→USB: SETCONREMOTE:1 zuerst über Rotor-Bus, dann USB-Proxy."""
    rotor = _FakeHw()
    ctrl_hw = _FakeHw()
    ctrl = _FakeCtrl()
    proxy = ControllerRemoteUsbProxy(rotor, ctrl_hw, ctrl, _FakeLog())  # type: ignore[arg-type]
    proxy._cfg = _active_cfg()
    rotor.sent_requests.clear()
    ctrl_hw.sent_requests.clear()
    assert proxy.activate() is True
    assert proxy.is_active()
    assert ctrl.usb_remote is True
    bus_remote = [
        r.line for r in rotor.sent_requests if "SETCONREMOTE:1" in str(r.line)
    ]
    usb_remote = [
        r.line for r in ctrl_hw.sent_requests if "SETCONREMOTE:1" in str(r.line)
    ]
    assert bus_remote, rotor.sent_requests
    assert usb_remote, ctrl_hw.sent_requests


def test_proxy_sends_setconremote_0_before_stop() -> None:
    """USB-Remote aus: SETCONREMOTE:0 noch über USB."""
    proxy, _rotor, _ctrl_hw, ctrl = _ready_proxy()
    assert proxy.is_active()
    ctrl.sync_calls.clear()
    assert proxy.apply_mode(False, timeout_s=2.0) is True
    assert not proxy.is_active()
    assert ctrl.usb_remote is False
