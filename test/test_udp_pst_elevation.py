"""Tests für EL-Unterstützung in der UDP-PST-Emulation."""
from __future__ import annotations

from types import SimpleNamespace

from rotortcpbridge.udp_pst_rotator import UdpPstRotator


class _Log:
    def __init__(self) -> None:
        self.lines: list[tuple[str, str]] = []

    def write(self, level: str, msg: str) -> None:
        self.lines.append((level, msg))


class _Ctrl:
    def __init__(self, *, enable_az: bool = True, enable_el: bool = True) -> None:
        self.enable_az = enable_az
        self.enable_el = enable_el
        self.az = SimpleNamespace(pos_d10=1000, target_d10=1100, pos_max_d10=3600)
        self.el = SimpleNamespace(pos_d10=250, target_d10=300)
        self.calls: list[tuple] = []

    def set_az_deg(self, deg: float, force: bool = True) -> None:
        self.calls.append(("set_az", float(deg), bool(force)))

    def set_el_deg(self, deg: float, force: bool = True) -> None:
        self.calls.append(("set_el", float(deg), bool(force)))

    def hold_all_at_current_pos(self) -> None:
        self.calls.append(("hold_all",))


def _emu(ctrl: _Ctrl) -> UdpPstRotator:
    u = UdpPstRotator(ctrl, _Log(), cfg={})
    u._enabled = True
    u._send_reply = lambda msg: u.calls_reply.append(msg)  # type: ignore[method-assign]
    u.calls_reply = []  # type: ignore[attr-defined]
    return u


def test_elevation_sets_el_when_enabled() -> None:
    ctrl = _Ctrl(enable_el=True)
    u = _emu(ctrl)
    u._handle_packet(b"<PST><ELEVATION>25.5</ELEVATION></PST>", ("127.0.0.1", 1))
    assert ("set_el", 25.5, True) in ctrl.calls


def test_elevation_ignored_when_el_off() -> None:
    ctrl = _Ctrl(enable_el=False)
    u = _emu(ctrl)
    u._handle_packet(b"<PST><ELEVATION>25.5</ELEVATION></PST>", ("127.0.0.1", 1))
    assert ctrl.calls == []


def test_el_query_replies_when_enabled() -> None:
    ctrl = _Ctrl(enable_el=True)
    u = _emu(ctrl)
    u._handle_packet(b"<PST>EL?</PST>", ("127.0.0.1", 1))
    assert u.calls_reply == ["EL:25.0\r"]  # type: ignore[attr-defined]
    u._handle_packet(b"<PST>TGE?</PST>", ("127.0.0.1", 1))
    assert u.calls_reply[-1] == "TGE:30.0\r"  # type: ignore[attr-defined]


def test_el_query_ignored_when_el_off() -> None:
    ctrl = _Ctrl(enable_el=False)
    u = _emu(ctrl)
    u._handle_packet(b"<PST>EL?</PST>", ("127.0.0.1", 1))
    assert u.calls_reply == []  # type: ignore[attr-defined]


def test_azimuth_still_works() -> None:
    ctrl = _Ctrl()
    u = _emu(ctrl)
    u._handle_packet(b"<PST><AZIMUTH>90</AZIMUTH></PST>", ("127.0.0.1", 1))
    assert any(c[0] == "set_az" and c[1] == 90.0 for c in ctrl.calls)
