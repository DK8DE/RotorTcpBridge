"""Tests EasyComm II Protokolllogik."""
from __future__ import annotations

from rotortcpbridge.easycomm_protocol import process_easycomm_line


class _Axis:
    def __init__(self, pos_d10: int = 0, moving: bool = False) -> None:
        self.pos_d10 = pos_d10
        self.moving = moving


class _Ctrl:
    def __init__(
        self,
        az_d10: int = 0,
        el_d10: int = 0,
        enable_az=True,
        enable_el=True,
        moving: bool = False,
    ) -> None:
        self.az = _Axis(az_d10, moving=moving)
        self.el = _Axis(el_d10)
        self.enable_az = enable_az
        self.enable_el = enable_el
        self.calls: list[tuple] = []

    def set_az_from_spid(self, d10: int, *, shortest_path: bool = False) -> None:
        self.calls.append(("set_az", int(d10), bool(shortest_path)))

    def set_el_from_spid(self, d10: int) -> None:
        self.calls.append(("set_el", int(d10)))

    def hold_all_at_current_pos(self) -> None:
        self.calls.append(("hold_all",))

    def hold_az_at_current_pos(self) -> None:
        self.calls.append(("hold_az",))

    def hold_el_at_current_pos(self) -> None:
        self.calls.append(("hold_el",))


def test_az_query() -> None:
    ctrl = _Ctrl(az_d10=1234)
    resp, close = process_easycomm_line("AZ", ctrl)
    assert close is False
    assert resp == "AZ123.4\n"


def test_el_query() -> None:
    ctrl = _Ctrl(el_d10=456)
    resp, _ = process_easycomm_line("EL", ctrl)
    assert resp == "EL45.6\n"


def test_az_set() -> None:
    ctrl = _Ctrl()
    resp, _ = process_easycomm_line("AZ180.0", ctrl)
    assert resp is None
    assert ("set_az", 1800, False) in ctrl.calls


def test_el_set() -> None:
    ctrl = _Ctrl()
    process_easycomm_line("EL45", ctrl)
    assert ("set_el", 450) in ctrl.calls


def test_stop_and_park() -> None:
    ctrl = _Ctrl()
    process_easycomm_line("S", ctrl)
    process_easycomm_line("PARK", ctrl)
    assert ctrl.calls.count(("hold_all",)) == 2


def test_sa_se() -> None:
    ctrl = _Ctrl()
    process_easycomm_line("SA", ctrl)
    process_easycomm_line("SE", ctrl)
    assert ("hold_az",) in ctrl.calls
    assert ("hold_el",) in ctrl.calls


def test_version() -> None:
    ctrl = _Ctrl()
    resp, _ = process_easycomm_line("VE", ctrl)
    assert resp == "VERotorTcpBridge\n"


def test_radio_tags_silent() -> None:
    ctrl = _Ctrl()
    resp, _ = process_easycomm_line("UP DN AO LO", ctrl)
    assert resp is None
    assert ctrl.calls == []


def test_az_set_with_space() -> None:
    ctrl = _Ctrl()
    process_easycomm_line("AZ 90.5", ctrl)
    assert ("set_az", 905, False) in ctrl.calls


def test_combined_query() -> None:
    ctrl = _Ctrl(az_d10=100, el_d10=200)
    resp, _ = process_easycomm_line("AZ EL", ctrl)
    assert resp == "AZ10.0\nEL20.0\n"


def test_gs_ge_registers() -> None:
    idle = _Ctrl()
    resp, _ = process_easycomm_line("GS", idle)
    assert resp == "GS1\n"
    moving = _Ctrl(moving=True)
    resp, _ = process_easycomm_line("GS GE", moving)
    assert resp == "GS2\nGE0\n"


def test_velocity_jogs() -> None:
    ctrl = _Ctrl(az_d10=1000)
    process_easycomm_line("VR1000", ctrl)
    assert any(c[0] == "set_az" for c in ctrl.calls)


def test_reset_holds() -> None:
    ctrl = _Ctrl()
    process_easycomm_line("RESET", ctrl)
    assert ("hold_all",) in ctrl.calls


def test_cr_dummy() -> None:
    ctrl = _Ctrl()
    resp, _ = process_easycomm_line("CR1", ctrl)
    assert resp == "CR1,0\n"
