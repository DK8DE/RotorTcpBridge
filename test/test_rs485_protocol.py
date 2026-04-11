"""Tests für RS485-Telegramm (Bauen, Checksumme, Parsen)."""

from __future__ import annotations

import pytest

from rotortcpbridge.rotor_parse_utils import parse_getposdg_ist_deg
from rotortcpbridge.rs485_protocol import Telegram, build, calc_checksum, parse


def test_calc_checksum_no_number_in_params() -> None:
    assert calc_checksum(1, 20, "") == float(1 + 20) + 0.0


def test_calc_checksum_uses_last_number() -> None:
    # SRC+DST + letzte Zahl in PARAMS
    assert calc_checksum(1, 20, "x;12,5") == pytest.approx(21.0 + 12.5)


def test_build_parse_roundtrip() -> None:
    line = build(1, 20, "GETPOS", "")
    assert line.startswith("#") and line.endswith("$")
    t = parse(line)
    assert t is not None
    assert t.src == 1
    assert t.dst == 20
    assert t.cmd == "GETPOS"
    assert t.params == ""
    assert t.ok is True


def test_build_with_params_decimal_checksum() -> None:
    line = build(1, 20, "SETPOSDG", "123,4")
    t = parse(line)
    assert t is not None
    assert t.params == "123,4"
    assert t.ok is True


def test_build_setposcc_roundtrip() -> None:
    line = build(1, 20, "SETPOSCC", "45,00")
    t = parse(line)
    assert t is not None
    assert t.cmd == "SETPOSCC"
    assert t.params == "45,00"
    assert t.ok is True


def test_build_setposcc_with_rotor_id_checksum_last_number() -> None:
    """CS = SRC+DST+letzte Zahl im Payload (hier Rotor-ID 20)."""
    line = build(2, 1, "SETPOSCC", "151,30;20")
    assert line.endswith("$")
    t = parse(line)
    assert t is not None
    assert t.src == 2 and t.dst == 1
    assert t.params == "151,30;20"
    assert t.ok is True
    assert calc_checksum(2, 1, "151,30;20") == pytest.approx(23.0)


def test_parse_invalid_line() -> None:
    assert parse("") is None
    assert parse("no hash") is None
    assert parse("#1:2:CMD$") is None  # zu wenig Teile


def test_parse_checksum_mismatch() -> None:
    # Manipulierte Checksumme
    bad = "#1:20:GETPOS::999$"
    t = parse(bad)
    assert t is not None
    assert t.ok is False


def test_parse_float_cs_format() -> None:
    line = build(10, 11, "X", "0,5")
    t = parse(line)
    assert t is not None
    assert t.ok is True
    assert isinstance(t, Telegram)


def test_parse_getposdg_ist_deg_colon_pair() -> None:
    line = build(1, 20, "ACK_GETPOSDG", "177,53:198,53")
    t = parse(line)
    assert t is not None
    assert parse_getposdg_ist_deg(t.params) == pytest.approx(177.53)


def test_parse_getposdg_ist_deg_semicolon_pair() -> None:
    line = build(1, 20, "ACK_GETPOSDG", "177,53;198,53")
    t = parse(line)
    assert t is not None
    assert parse_getposdg_ist_deg(t.params) == pytest.approx(177.53)


def test_parse_getposdg_ist_deg_single() -> None:
    assert parse_getposdg_ist_deg("45,12") == pytest.approx(45.12)
    assert parse_getposdg_ist_deg("") is None
