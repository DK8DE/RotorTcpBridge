"""Tests DCU-1 Protokolllogik."""
from __future__ import annotations

from rotortcpbridge.dcu1_protocol import process_dcu1_frame, process_dcu1_line


class _Axis:
    def __init__(self, pos_d10: int = 0) -> None:
        self.pos_d10 = pos_d10


class _Ctrl:
    def __init__(self, az_d10: int = 0, enable_az=True) -> None:
        self.az = _Axis(az_d10)
        self.el = _Axis(0)
        self.enable_az = enable_az
        self.enable_el = False
        self.calls: list[tuple] = []

    def set_az_from_spid(self, d10: int, *, shortest_path: bool = False) -> None:
        self.calls.append(("set_az", int(d10), bool(shortest_path)))

    def hold_az_at_current_pos(self) -> None:
        self.calls.append(("hold_az",))


def test_ap1_sets_az() -> None:
    ctrl = _Ctrl()
    resp, _ = process_dcu1_frame("AP1180", ctrl)
    assert resp is None
    assert ("set_az", 1800, False) in ctrl.calls


def test_ap_short_form() -> None:
    ctrl = _Ctrl()
    process_dcu1_frame("AP090", ctrl)
    assert ("set_az", 900, False) in ctrl.calls


def test_ai1_returns_echo_bearing() -> None:
    ctrl = _Ctrl(az_d10=1230)
    resp, _ = process_dcu1_frame("AI1", ctrl)
    assert resp == "AI1123;"
    assert "123" in resp


def test_ai_returns_compact() -> None:
    ctrl = _Ctrl(az_d10=450)
    resp, _ = process_dcu1_frame("AI", ctrl)
    assert resp == "045;"


def test_bi1_query() -> None:
    ctrl = _Ctrl(az_d10=900)
    resp, _ = process_dcu1_frame("BI1", ctrl)
    assert resp == "BI1090;"


def test_ap_with_space() -> None:
    ctrl = _Ctrl()
    process_dcu1_frame("AP1 180", ctrl)
    assert ("set_az", 1800, False) in ctrl.calls


def test_line_with_semicolon() -> None:
    ctrl = _Ctrl()
    process_dcu1_line("AP1180;", ctrl)
    assert ("set_az", 1800, False) in ctrl.calls


def test_am_uses_pending() -> None:
    ctrl = _Ctrl()
    pending: list = [None]
    process_dcu1_frame("AP1045", ctrl, pending_target=pending)
    assert pending[0] == 45.0
    # AM erneut setzen (harmlos)
    process_dcu1_frame("AM1", ctrl, pending_target=pending)
    assert ("set_az", 450, False) in ctrl.calls


def test_stop_holds() -> None:
    ctrl = _Ctrl()
    process_dcu1_frame("STOP", ctrl)
    assert ("hold_az",) in ctrl.calls
