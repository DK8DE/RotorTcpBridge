"""Tests für rotor_parse_utils."""

from __future__ import annotations

import pytest

from rotortcpbridge.rotor_parse_utils import (
    parse_float,
    parse_float_any,
    parse_int,
    parse_setposcc_params,
)


def test_parse_float_basic() -> None:
    assert parse_float(" 12.5 ") == 12.5
    assert parse_float("3,14") == pytest.approx(3.14)
    assert parse_float("x") is None


def test_parse_int_basic() -> None:
    assert parse_int("42") == 42
    assert parse_int("3.9") == 3


def test_parse_float_any_embedded() -> None:
    assert parse_float_any("foo;12.5;bar") == pytest.approx(12.5)
    assert parse_float_any("") is None


def test_parse_setposcc_params_plain_angle() -> None:
    v, rid = parse_setposcc_params("151,30")
    assert v == pytest.approx(151.3)
    assert rid is None


def test_parse_setposcc_params_angle_and_rotor_id() -> None:
    v, rid = parse_setposcc_params("151,30;20")
    assert v == pytest.approx(151.3)
    assert rid == 20


def test_parse_setposcc_params_tail_not_plain_int_falls_back() -> None:
    """Letztes Feld nicht reine Ziffern → kein Rotor-ID-Suffix, ganzer String als Winkel."""
    v, rid = parse_setposcc_params("90,5;foo")
    assert rid is None
    assert v is None
    v2, rid2 = parse_setposcc_params("90,5")
    assert v2 == pytest.approx(90.5)
    assert rid2 is None
