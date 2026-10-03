"""Tests für QRA / ANT / TRACK / ON in der UDP-PST-Emulation."""
from __future__ import annotations

from types import SimpleNamespace

from rotortcpbridge.udp_pst_rotator import UdpPstRotator


class _Log:
    def __init__(self) -> None:
        self.lines: list[tuple[str, str]] = []

    def write(self, level: str, msg: str) -> None:
        self.lines.append((level, msg))


class _Ctrl:
    def __init__(self) -> None:
        self.enable_az = True
        self.enable_el = False
        self.az = SimpleNamespace(
            pos_d10=0,
            target_d10=0,
            pos_max_d10=3600,
            antoff1=0.0,
            antoff2=0.0,
            antoff3=0.0,
            antdp1=0,
            antdp2=0,
            antdp3=0,
        )
        self.el = SimpleNamespace(pos_d10=0, target_d10=0)
        self.calls: list[tuple] = []
        self.align_calls: list[tuple[int, int]] = []

    def set_az_deg(self, deg: float, force: bool = True) -> None:
        self.calls.append(("set_az", float(deg), bool(force)))

    def hold_all_at_current_pos(self) -> None:
        self.calls.append(("hold_all",))

    def align_az_bearing_after_antenna_switch(self, old: int, new: int, cfg) -> None:
        self.align_calls.append((int(old), int(new)))


def _emu(ctrl: _Ctrl, cfg: dict | None = None) -> UdpPstRotator:
    u = UdpPstRotator(
        ctrl,
        _Log(),
        cfg=cfg
        or {
            "ui": {
                "location_lat": 49.0,
                "location_lon": 8.0,
                "location_locator": "",
                "compass_antenna": 0,
            }
        },
    )
    u._enabled = True
    u.calls_reply = []  # type: ignore[attr-defined]
    u._send_reply = lambda msg: u.calls_reply.append(msg)  # type: ignore[method-assign]
    return u


def test_mode_query_replies_manual() -> None:
    u = _emu(_Ctrl())
    u._handle_packet(b"<PST>MODE?</PST>", ("127.0.0.1", 1))
    assert u.calls_reply == ["MODE:0\r"]  # type: ignore[attr-defined]


def test_track_ack_only() -> None:
    ctrl = _Ctrl()
    u = _emu(ctrl)
    u._handle_packet(b"<PST><TRACK>1</TRACK></PST>", ("127.0.0.1", 1))
    assert ctrl.calls == []
    assert u.calls_reply == ["OK:TRACK:1\r"]  # type: ignore[attr-defined]
    u._handle_packet(b"<PST>MODE?</PST>", ("127.0.0.1", 1))
    assert u.calls_reply[-1] == "MODE:0\r"  # type: ignore[attr-defined]


def test_on_gates_azimuth() -> None:
    ctrl = _Ctrl()
    u = _emu(ctrl)
    u._handle_packet(b"<PST><ON>0</ON></PST>", ("127.0.0.1", 1))
    assert u.calls_reply[-1] == "OK:ON:0\r"  # type: ignore[attr-defined]
    u._handle_packet(b"<PST><AZIMUTH>90</AZIMUTH></PST>", ("127.0.0.1", 1))
    assert ctrl.calls == []
    u._handle_packet(b"<PST><ON>1</ON></PST>", ("127.0.0.1", 1))
    u._handle_packet(b"<PST><AZIMUTH>90</AZIMUTH></PST>", ("127.0.0.1", 1))
    assert any(c[0] == "set_az" and c[1] == 90.0 for c in ctrl.calls)


def test_ant_selects_and_callbacks() -> None:
    ctrl = _Ctrl()
    u = _emu(ctrl)
    seen: list[int] = []
    u.on_antenna_selected = seen.append
    u._handle_packet(b"<PST><ANT>2</ANT></PST>", ("127.0.0.1", 1))
    assert seen == [1]
    assert u.cfg["ui"]["compass_antenna"] == 1
    assert ctrl.align_calls == [(0, 1)]
    assert u.calls_reply[-1] == "OK:ANT:2\r"  # type: ignore[attr-defined]


def test_qra_sets_azimuth() -> None:
    ctrl = _Ctrl()
    # Standort und Ziel so wählen, dass Peilung ~90° (Ost) ist
    u = _emu(
        ctrl,
        cfg={
            "ui": {
                "location_lat": 50.0,
                "location_lon": 8.0,
                "location_locator": "",
                "compass_antenna": 0,
            }
        },
    )
    # JN49: grob östlich von 50N/8E
    u._handle_packet(b"<PST><QRA>JN49</QRA></PST>", ("127.0.0.1", 1))
    assert any(c[0] == "set_az" for c in ctrl.calls)
    assert any(r.startswith("OK:QRA:JN49") for r in u.calls_reply)  # type: ignore[attr-defined]


def test_qra_invalid() -> None:
    ctrl = _Ctrl()
    u = _emu(ctrl)
    u._handle_packet(b"<PST><QRA>XX</QRA></PST>", ("127.0.0.1", 1))
    assert ctrl.calls == []
