"""Tests für angle_utils (Winkel-Hilfsfunktionen)."""
from __future__ import annotations

import pytest

from rotortcpbridge.angle_utils import clamp_el, fmt_deg, shortest_delta_deg, wrap_deg


@pytest.mark.parametrize(
    "a,b",
    [
        (0.0, 0.0),
        (360.0, 0.0),
        (-90.0, 270.0),
        (450.0, 90.0),
    ],
)
def test_wrap_deg(a: float, b: float) -> None:
    assert wrap_deg(a) == pytest.approx(b)


def test_shortest_delta_deg() -> None:
    assert shortest_delta_deg(0, 90) == pytest.approx(90)
    assert shortest_delta_deg(350, 10) == pytest.approx(20)
    assert shortest_delta_deg(10, 350) == pytest.approx(-20)


def test_clamp_el() -> None:
    assert clamp_el(-5) == 0.0
    assert clamp_el(100) == 90.0
    assert clamp_el(45) == pytest.approx(45.0)


def test_fmt_deg() -> None:
    assert "45.0" in fmt_deg(45.0)


def test_wrap_deg_large_negative() -> None:
    assert wrap_deg(-720.0) == pytest.approx(0.0)


def test_shortest_delta_deg_symmetry() -> None:
    assert shortest_delta_deg(100, 200) == pytest.approx(-shortest_delta_deg(200, 100))
