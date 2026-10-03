"""Tests GS-232B Protokolllogik."""
from __future__ import annotations

from rotortcpbridge.gs232_protocol import process_gs232_line


class _Axis:
    def __init__(self, pos_d10: int = 0) -> None:
        self.pos_d10 = pos_d10


class _Ctrl:
    def __init__(self, az_d10: int = 0, el_d10: int = 0, enable_az=True, enable_el=True) -> None:
        self.az = _Axis(az_d10)
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


def test_c_returns_az() -> None:
    ctrl = _Ctrl(az_d10=1230)
    resp, close = process_gs232_line("C", ctrl)
    assert close is False
    assert resp == "AZ=123\r"


def test_b_returns_el() -> None:
    ctrl = _Ctrl(el_d10=450)
    resp, _ = process_gs232_line("B", ctrl)
    assert resp == "EL=045\r"


def test_c2_returns_both() -> None:
    ctrl = _Ctrl(az_d10=100, el_d10=200)
    resp, _ = process_gs232_line("C2", ctrl)
    assert resp == "AZ=010 EL=020\r"


def test_m_sets_az() -> None:
    ctrl = _Ctrl()
    resp, _ = process_gs232_line("M180", ctrl)
    assert resp is None
    assert ("set_az", 1800, False) in ctrl.calls


def test_w_sets_az_el() -> None:
    ctrl = _Ctrl()
    process_gs232_line("W090 045", ctrl)
    assert ("set_az", 900, False) in ctrl.calls
    assert ("set_el", 450) in ctrl.calls


def test_s_holds() -> None:
    ctrl = _Ctrl()
    process_gs232_line("S", ctrl)
    assert ("hold_all",) in ctrl.calls


def test_shortest_path_flag() -> None:
    ctrl = _Ctrl()
    process_gs232_line("M070", ctrl, shortest_path=True)
    assert ("set_az", 700, True) in ctrl.calls


def test_report_mod360() -> None:
    ctrl = _Ctrl(az_d10=3700)
    resp, _ = process_gs232_line("C", ctrl, shortest_path=True, report_mod360=True)
    assert resp == "AZ=010\r"


def test_extract_bare_c_without_cr() -> None:
    from rotortcpbridge.gs232_protocol import extract_gs232_commands

    cmds, leftover = extract_gs232_commands(b"C")
    assert cmds == []
    assert leftover == b"C"
    cmds, leftover = extract_gs232_commands(b"C\r")
    assert cmds == ["C"]
    assert leftover == b""


def test_extract_c2_not_split_into_c() -> None:
    from rotortcpbridge.gs232_protocol import extract_gs232_commands

    cmds, leftover = extract_gs232_commands(b"C2")
    assert cmds == ["C2"]
    assert leftover == b""


def test_extract_w_without_cr() -> None:
    from rotortcpbridge.gs232_protocol import extract_gs232_commands

    cmds, leftover = extract_gs232_commands(b"W180 045")
    assert cmds == ["W180 045"]
    assert leftover == b""


def test_extract_partial_w_waits() -> None:
    from rotortcpbridge.gs232_protocol import extract_gs232_commands

    cmds, leftover = extract_gs232_commands(b"W18")
    assert cmds == []
    assert leftover == b"W18"


def test_extract_crlf_still_works() -> None:
    from rotortcpbridge.gs232_protocol import extract_gs232_commands

    cmds, leftover = extract_gs232_commands(b"C\r\nM090\r")
    assert cmds == ["C", "M090"]
    assert leftover == b""
