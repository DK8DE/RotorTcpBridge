"""Tests für rotor_parse_utils."""

from __future__ import annotations

import pytest

from rotortcpbridge.rotor_parse_utils import parse_float, parse_float_any, parse_int


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
