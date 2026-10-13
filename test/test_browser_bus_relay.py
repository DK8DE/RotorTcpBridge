"""Browser-Bus-Relay: Display-Frames (Web Serial) ↔ Rotor-Bus."""

from __future__ import annotations

from types import SimpleNamespace

from rotortcpbridge.browser_bus_relay import BrowserBusRelay
from rotortcpbridge.rs485_protocol import build


class _FakeHw:
    def __init__(self) -> None:
        self.on_rx_line = None
        self.on_tx_line = None
        self.fire = []
        self.requests = []

    def send_line_fire_and_forget(self, line: str) -> None:
        self.fire.append(line)

    def send_request(self, req) -> None:
        self.requests.append(req)


class _FakeCtrl:
    master_id = 1
    slave_az = 20
    slave_el = 21
    enable_az = True
    enable_el = False

    def __init__(self) -> None:
        self.link = None

    def set_controller_link(self, hw, enabled) -> None:
        self.link = hw if enabled else None

    def set_controller_cont_id(self, _cid) -> None:
        return

    def foreign_master_yield_active(self) -> bool:
        return False

    def on_controller_link_telegram(self, _tel) -> None:
        return

    def clear_foreign_master_follow(self) -> None:
        return


class _Log:
    def write(self, *_a, **_k) -> None:
        return


def _relay(desktop_active: bool = False):
    hw = _FakeHw()
    ctrl = _FakeCtrl()
    cfg = {"controller_hw": {"cont_id": 2, "enabled": True}}
    desktop = SimpleNamespace(is_active=lambda: desktop_active)
    r = BrowserBusRelay(
        hw,
        ctrl,
        _Log(),
        cfg_getter=lambda: cfg,
        desktop_proxy_getter=lambda: desktop,
    )
    return r, hw, ctrl


def test_attach_and_forward_display_poll_to_rotor():
    r, hw, ctrl = _relay()
    line = build(2, 20, "GETPOSDG", "0")
    res = r.handle({"tx": [line], "since": 0})
    assert res["ok"] and res["active"]
    assert ctrl.link is not None
    assert line in hw.fire  # GET → fire-and-forget auf Rotor-Bus
    r.detach()
    assert ctrl.link is None


def test_rotor_ack_is_mirrored_back_to_display():
    r, hw, _ctrl = _relay()
    r.handle({"tx": [], "since": 0})
    ack = build(20, 1, "ACK_GETPOSDG", "123,00")
    r.on_rotor_rx_line(ack)
    res = r.handle({"tx": [], "since": 0})
    lines = [x["line"] for x in res["rx"]]
    assert any(":ACK_GETPOSDG:" in ln and ln.startswith("#20:2:") for ln in lines)
    # since: nichts doppelt
    res2 = r.handle({"tx": [], "since": res["seq"]})
    assert res2["rx"] == []
    r.detach()


def test_desktop_remote_usb_blocks_relay():
    r, _hw, _ctrl = _relay(desktop_active=True)
    res = r.handle({"tx": [], "since": 0})
    assert not res["ok"]
    assert res["error"] == "desktop_remote_usb_active"
