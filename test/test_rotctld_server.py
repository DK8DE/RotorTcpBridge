"""Tests für die Hamlib-rotctld-Protokolllogik (Zeilenprotokoll)."""
from __future__ import annotations

from rotortcpbridge.rotctld_server import (
    build_dump_state,
    process_rotctld_line,
)


class _Log:
    def __init__(self) -> None:
        self.msgs: list[tuple[str, str]] = []

    def write(self, level: str, msg: str) -> None:
        self.msgs.append((level, msg))


class _Axis:
    def __init__(self, pos_d10: int = 0) -> None:
        self.pos_d10 = pos_d10


class _Ctrl:
    """Minimaler Controller-Stub, der die von rotctld genutzten Methoden mitschreibt."""

    def __init__(self, az_d10: int = 0, el_d10: int = 0, enable_az=True, enable_el=True) -> None:
        self.az = _Axis(az_d10)
        self.el = _Axis(el_d10)
        self.enable_az = enable_az
        self.enable_el = enable_el
        self.calls: list[tuple] = []

    def set_az_from_spid(self, d10: int) -> None:
        self.calls.append(("set_az", int(d10)))

    def set_el_from_spid(self, d10: int) -> None:
        self.calls.append(("set_el", int(d10)))

    def hold_az_at_current_pos(self) -> None:
        self.calls.append(("hold_az",))

    def hold_el_at_current_pos(self) -> None:
        self.calls.append(("hold_el",))


def test_get_pos_returns_two_float_lines() -> None:
    ctrl = _Ctrl(az_d10=1234, el_d10=456)
    resp, close = process_rotctld_line("p", ctrl)
    assert close is False
    assert resp == "123.400000\n45.600000\n"


def test_get_pos_long_form() -> None:
    ctrl = _Ctrl(az_d10=0, el_d10=900)
    resp, _ = process_rotctld_line("\\get_pos", ctrl)
    assert resp == "0.000000\n90.000000\n"


def test_get_pos_disabled_axis_reports_zero() -> None:
    ctrl = _Ctrl(az_d10=1800, el_d10=450, enable_el=False)
    resp, _ = process_rotctld_line("p", ctrl)
    assert resp == "180.000000\n0.000000\n"


def test_set_pos_moves_both_axes_and_acks() -> None:
    ctrl = _Ctrl()
    resp, close = process_rotctld_line("P 123.4 45.6", ctrl)
    assert resp == "RPRT 0\n"
    assert close is False
    assert ("set_az", 1234) in ctrl.calls
    assert ("set_el", 456) in ctrl.calls


def test_set_pos_respects_disabled_axis() -> None:
    ctrl = _Ctrl(enable_el=False)
    process_rotctld_line("P 10 20", ctrl)
    assert ("set_az", 100) in ctrl.calls
    assert all(c[0] != "set_el" for c in ctrl.calls)


def test_set_pos_invalid_params_returns_rprt_neg8() -> None:
    ctrl = _Ctrl()
    resp, _ = process_rotctld_line("P abc def", ctrl)
    assert resp == "RPRT -8\n"
    assert ctrl.calls == []


def test_set_pos_missing_arg_returns_rprt_neg8() -> None:
    ctrl = _Ctrl()
    resp, _ = process_rotctld_line("P 100", ctrl)
    assert resp == "RPRT -8\n"


def test_stop_holds_current_pos_and_acks() -> None:
    ctrl = _Ctrl()
    resp, _ = process_rotctld_line("S", ctrl)
    assert resp == "RPRT 0\n"
    assert ("hold_az",) in ctrl.calls
    assert ("hold_el",) in ctrl.calls


def test_park_holds_current_pos() -> None:
    ctrl = _Ctrl()
    resp, _ = process_rotctld_line("K", ctrl)
    assert resp == "RPRT 0\n"
    assert ("hold_az",) in ctrl.calls


def test_get_info_returns_model_line() -> None:
    ctrl = _Ctrl()
    resp, _ = process_rotctld_line("_", ctrl)
    assert resp == "RotorTcpBridge\n"


def test_quit_closes_connection() -> None:
    ctrl = _Ctrl()
    resp, close = process_rotctld_line("q", ctrl)
    assert resp is None
    assert close is True


def test_unknown_command_returns_rprt_neg11() -> None:
    ctrl = _Ctrl()
    resp, _ = process_rotctld_line("XYZ", ctrl)
    assert resp == "RPRT -11\n"


def test_empty_line_is_ignored() -> None:
    ctrl = _Ctrl()
    resp, close = process_rotctld_line("   ", ctrl)
    assert resp is None
    assert close is False


def test_extended_prefix_is_stripped() -> None:
    ctrl = _Ctrl(az_d10=100, el_d10=200)
    resp, _ = process_rotctld_line("+p", ctrl)
    assert resp == "10.000000\n20.000000\n"


def test_dump_state_matches_netrotctl_format() -> None:
    block = build_dump_state(az_enabled=True, el_enabled=True)
    lines = block.split("\n")
    # Zeile 1 = Protokollversion (int), Zeile 2 = rot_model (vom Client verworfen)
    assert lines[0] == "1"
    assert lines[1] == "0"
    assert "min_az=0.000000" in lines
    assert "max_az=360.000000" in lines
    assert "min_el=0.000000" in lines
    assert "max_el=90.000000" in lines
    assert "rot_type=AzEl" in lines
    assert "done" in lines
    # tag=value-Zeilen sind vom Hamlib-Parser lesbar (setting=value)
    kv = dict(
        ln.split("=", 1) for ln in lines if "=" in ln
    )
    assert kv["max_el"] == "90.000000"


def test_dump_state_azimuth_only_when_el_disabled() -> None:
    block = build_dump_state(az_enabled=True, el_enabled=False)
    assert "rot_type=Az" in block.split("\n")


def test_dump_state_via_process_line() -> None:
    ctrl = _Ctrl()
    resp, close = process_rotctld_line("\\dump_state", ctrl)
    assert close is False
    assert resp is not None and resp.endswith("done\n")
    assert resp.startswith("1\n")
