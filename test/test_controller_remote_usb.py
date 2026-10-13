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

    def send_line_fire_and_forget(self, line: str) -> None:
        self.ff_lines.append(str(line).strip())


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
        self.cleared_follow = False
        self.link_telegrams: List[Any] = []
        self.sync_calls: List[tuple] = []
        self._drive_ready = True

    def drive_ready(self) -> bool:
        return bool(self._drive_ready)

    def set_controller_link(self, hw: Any, enabled: bool) -> None:
        self.ctrl_hw = hw
        self.usb_remote = bool(enabled) and hw is not None

    def set_controller_cont_id(self, cont_id: int) -> None:
        self._controller_cont_id = int(cont_id)

    def clear_foreign_master_follow(self, axis: Any = None) -> None:
        self.cleared_follow = True

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


def test_proxy_forwards_display_to_rotor_and_mirrors_rotor() -> None:
    rotor = _FakeHw()
    ctrl_hw = _FakeHw()
    ctrl = _FakeCtrl()
    proxy = ControllerRemoteUsbProxy(rotor, ctrl_hw, ctrl, _FakeLog())  # type: ignore[arg-type]
    proxy.update_cfg(_active_cfg())
    proxy._start_proxy()
    assert proxy.is_active()
    assert ctrl.usb_remote is True

    line = build(2, 20, "SETPOSDG", "90,00")
    proxy.on_ctrl_rx_line(line)
    takeover = build(0, 20, "SETPOSDG", "90,00")
    assert takeover in rotor.ff_lines
    assert rotor.sent_requests == []
    assert len(ctrl.link_telegrams) == 1
    assert ctrl.cleared_follow is True

    # Routine-GET der Software wird nicht gespiegelt
    soft_get = build(0, 20, "GETANEMO", "0")
    proxy.on_rotor_tx_line(soft_get, 1)
    assert soft_get not in ctrl_hw.ff_lines

    soft_tx = build(0, 20, "SETPOSDG", "12,00")
    proxy.on_rotor_tx_line(soft_tx, 2)
    assert soft_tx in ctrl_hw.ff_lines

    ant_tx = build(0, 20, "SETANTOFF1", "45.0")
    proxy.on_rotor_tx_line(ant_tx, 3)
    assert ant_tx in ctrl_hw.ff_lines

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
    rotor = _FakeHw()
    ctrl_hw = _FakeHw()
    ctrl = _FakeCtrl()
    ctrl.foreign_master_yield_active = lambda *_a, **_k: True  # type: ignore[method-assign]
    proxy = ControllerRemoteUsbProxy(rotor, ctrl_hw, ctrl, _FakeLog())  # type: ignore[arg-type]
    proxy.update_cfg(_active_cfg())
    proxy._start_proxy()
    proxy.on_ctrl_rx_line(build(2, 20, "SETPWM", "100"))
    assert rotor.sent_requests == []
    assert rotor.ff_lines == []
    proxy.on_ctrl_rx_line(build(2, 20, "SETPOSDG", "90,00"))
    assert build(0, 20, "SETPOSDG", "90,00") in rotor.ff_lines
    assert rotor.sent_requests == []
    assert ctrl.cleared_follow is True


def test_proxy_setposcc_fire_and_forget() -> None:
    rotor = _FakeHw()
    ctrl_hw = _FakeHw()
    ctrl = _FakeCtrl()
    proxy = ControllerRemoteUsbProxy(rotor, ctrl_hw, ctrl, _FakeLog())  # type: ignore[arg-type]
    proxy.update_cfg(_active_cfg())
    proxy._start_proxy()
    line = build(2, 20, "SETPOSCC", "166,70;20")
    proxy.on_ctrl_rx_line(line)
    assert line in rotor.ff_lines
    assert rotor.sent_requests == []
    assert len(ctrl.link_telegrams) == 1


def test_proxy_setposdg_rewrites_dst_from_master_param() -> None:
    """Display sendet SETPOSDG an die Bridge; Bus braucht den Rotor-Slave."""
    rotor = _FakeHw()
    ctrl_hw = _FakeHw()
    ctrl = _FakeCtrl()
    proxy = ControllerRemoteUsbProxy(rotor, ctrl_hw, ctrl, _FakeLog())  # type: ignore[arg-type]
    proxy.update_cfg(_active_cfg())
    proxy._start_proxy()
    proxy.on_ctrl_rx_line(build(2, 0, "SETPOSDG", "151,30;20"))
    assert build(0, 20, "SETPOSDG", "151,30;20") in rotor.ff_lines
    assert rotor.sent_requests == []


def test_proxy_dedup_no_echo_of_controller_tx() -> None:
    rotor = _FakeHw()
    ctrl_hw = _FakeHw()
    ctrl = _FakeCtrl()
    proxy = ControllerRemoteUsbProxy(rotor, ctrl_hw, ctrl, _FakeLog())  # type: ignore[arg-type]
    proxy.update_cfg(_active_cfg())
    proxy._start_proxy()
    line = build(2, 20, "GETREF", "0")
    proxy.on_ctrl_rx_line(line)
    assert line in rotor.ff_lines
    assert rotor.sent_requests == []
    before = list(ctrl_hw.ff_lines)
    proxy.on_rotor_tx_line(line, 99)
    assert ctrl_hw.ff_lines == before


def test_proxy_getposdg_fire_and_forget_no_bus_block() -> None:
    """Display-GETPOSDG darf den Rotor-Bus nicht bis Timeout blockieren."""
    rotor = _FakeHw()
    ctrl_hw = _FakeHw()
    ctrl = _FakeCtrl()
    ctrl.slave_az = 20
    proxy = ControllerRemoteUsbProxy(rotor, ctrl_hw, ctrl, _FakeLog())  # type: ignore[arg-type]
    proxy.update_cfg(_active_cfg())
    proxy._start_proxy()
    line = build(2, 22, "GETPOSDG", "0")
    proxy.on_ctrl_rx_line(line)
    assert line in rotor.ff_lines
    assert rotor.sent_requests == []


def test_proxy_drops_foreign_getposdg_during_local_motion() -> None:
    """Während Fahrt: alle Display-GETs verwerfen (auch eigener Slave)."""
    rotor = _FakeHw()
    ctrl_hw = _FakeHw()
    ctrl = _FakeCtrl()
    ctrl.slave_az = 20
    ctrl._motion_poll_restrict_active = lambda *_a, **_k: True  # type: ignore[attr-defined]
    proxy = ControllerRemoteUsbProxy(rotor, ctrl_hw, ctrl, _FakeLog())  # type: ignore[arg-type]
    proxy.update_cfg(_active_cfg())
    proxy._start_proxy()
    proxy.on_ctrl_rx_line(build(2, 22, "GETPOSDG", "0"))
    assert rotor.ff_lines == []
    assert rotor.sent_requests == []
    own = build(2, 20, "GETPOSDG", "0")
    proxy.on_ctrl_rx_line(own)
    assert rotor.ff_lines == []
    proxy.on_ctrl_rx_line(build(2, 20, "GETTEMPA", "0"))
    assert rotor.ff_lines == []
    assert rotor.sent_requests == []


def test_proxy_forwards_boot_config_gets_during_local_motion() -> None:
    """Einmalige Konfig-GETs des Display-Starts dürfen auch während Fahrt durch."""
    rotor = _FakeHw()
    ctrl_hw = _FakeHw()
    ctrl = _FakeCtrl()
    ctrl.slave_az = 20
    ctrl._motion_poll_restrict_active = lambda *_a, **_k: True  # type: ignore[attr-defined]
    proxy = ControllerRemoteUsbProxy(rotor, ctrl_hw, ctrl, _FakeLog())  # type: ignore[arg-type]
    proxy.update_cfg(_active_cfg())
    proxy._start_proxy()
    line = build(2, 20, "GETANTOFF1", "0")
    proxy.on_ctrl_rx_line(line)
    assert line in rotor.ff_lines


def test_proxy_forwards_gets_when_idle() -> None:
    """Ohne Fahrt: Display-GETs gehen fire-and-forget auf den Rotor."""
    rotor = _FakeHw()
    ctrl_hw = _FakeHw()
    ctrl = _FakeCtrl()
    ctrl.slave_az = 20
    ctrl._motion_poll_restrict_active = lambda *_a, **_k: False  # type: ignore[attr-defined]
    proxy = ControllerRemoteUsbProxy(rotor, ctrl_hw, ctrl, _FakeLog())  # type: ignore[arg-type]
    proxy.update_cfg(_active_cfg())
    proxy._start_proxy()
    line = build(2, 20, "GETTEMPA", "0")
    proxy.on_ctrl_rx_line(line)
    assert line in rotor.ff_lines


def test_proxy_drops_setposdg_during_profile_sync() -> None:
    """Während Profil-Sync: Display-SETPOSDG nicht auf den Rotor."""
    rotor = _FakeHw()
    ctrl_hw = _FakeHw()
    ctrl = _FakeCtrl()
    ctrl._drive_ready = False
    proxy = ControllerRemoteUsbProxy(rotor, ctrl_hw, ctrl, _FakeLog())  # type: ignore[arg-type]
    proxy.update_cfg(_active_cfg())
    proxy._start_proxy()
    proxy.on_ctrl_rx_line(build(2, 20, "SETPOSDG", "90,00"))
    assert rotor.ff_lines == []
    assert rotor.sent_requests == []


def test_proxy_ignores_usb_echo_of_mirrored_rotor_tx() -> None:
    """USB-Echo einer gespiegelten Software-TX darf nicht zurück auf den Rotor."""
    rotor = _FakeHw()
    ctrl_hw = _FakeHw()
    ctrl = _FakeCtrl()
    proxy = ControllerRemoteUsbProxy(rotor, ctrl_hw, ctrl, _FakeLog())  # type: ignore[arg-type]
    proxy.update_cfg(_active_cfg())
    proxy._start_proxy()

    soft_tx = build(0, 20, "SETPOSDG", "10,00")
    proxy.on_rotor_tx_line(soft_tx, 1)
    assert soft_tx in ctrl_hw.ff_lines
    n_before = len(rotor.sent_requests) + len(rotor.ff_lines)
    proxy.on_ctrl_rx_line(soft_tx)
    assert len(rotor.sent_requests) + len(rotor.ff_lines) == n_before


def test_proxy_drops_ack_and_garbage_from_controller() -> None:
    rotor = _FakeHw()
    ctrl_hw = _FakeHw()
    ctrl = _FakeCtrl()
    proxy = ControllerRemoteUsbProxy(rotor, ctrl_hw, ctrl, _FakeLog())  # type: ignore[arg-type]
    proxy.update_cfg(_active_cfg())
    proxy._start_proxy()

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
    ctrl.sync_calls.clear()
    assert proxy.activate() is True
    assert proxy.is_active()
    assert ctrl.usb_remote is True
    remote_ons = [c for c in ctrl.sync_calls if c[1] == "SETCONREMOTE" and c[2] == "1"]
    assert remote_ons, ctrl.sync_calls
    assert remote_ons[0][0] == 2


def test_proxy_sends_setconremote_0_before_stop() -> None:
    """USB-Remote aus: SETCONREMOTE:0 noch über USB."""
    rotor = _FakeHw()
    ctrl_hw = _FakeHw()
    ctrl = _FakeCtrl()
    proxy = ControllerRemoteUsbProxy(rotor, ctrl_hw, ctrl, _FakeLog())  # type: ignore[arg-type]
    proxy.update_cfg(_active_cfg())
    assert proxy.is_active()
    ctrl.sync_calls.clear()
    off = {
        "controller_hw": {"enabled": True, "usb_remote": False, "cont_id": 2},
        "controller_link": {"mode": "com", "com_port": "COM9", "baudrate": 115200},
    }
    proxy.update_cfg(off)
    assert not proxy.is_active()
    assert ctrl.usb_remote is False
    assert any(c[1] == "SETCONREMOTE" and c[2] == "0" for c in ctrl.sync_calls)
