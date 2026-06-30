"""Tests für angle_utils (Winkel-Hilfsfunktionen)."""

from __future__ import annotations

import pytest

from rotortcpbridge.angle_utils import (
    az_pos_deg_from_d10,
    clamp_el,
    dipole_rotor_move_cost,
    fmt_deg,
    is_az_pos_at_full_circle_d10,
    rotor_az_for_display_bearing,
    rotor_travel_deg,
    shortest_delta_az_rotor_deg,
    shortest_delta_deg,
    wrap_deg,
)


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


def test_rotor_travel_deg_shortest() -> None:
    assert rotor_travel_deg(300, 10) == pytest.approx(70)
    assert rotor_travel_deg(300, 190) == pytest.approx(110)


def test_rotor_az_for_display_bearing_dipole_picks_shorter_rotor_travel() -> None:
    # Mit Versatz: Peilungsnähe würde Hauptkeule wählen, Drehweg die Gegenkeule.
    assert rotor_az_for_display_bearing(350, 100, 40, dipole=True) == pytest.approx(70)
    # 300° → 10°: langer CCW-Bogen zur Hauptkeule → Gegenkeule (Rotor 190°).
    assert rotor_az_for_display_bearing(10, 0, 300, dipole=True) == pytest.approx(190)
    assert rotor_az_for_display_bearing(10, 0, 306, dipole=True) == pytest.approx(190)
    # 300° → 170°: Gegenkeule (Rotor 350°) ist näher als Hauptkeule (170°).
    assert rotor_az_for_display_bearing(170, 0, 300, dipole=True) == pytest.approx(350)
    # Nahe Nord: kurzer CW-Weg bleibt bei Hauptkeule.
    assert rotor_az_for_display_bearing(10, 0, 350, dipole=True) == pytest.approx(10)
    # Gegenkeule zeigt schon aufs Ziel → nicht drehen.
    assert rotor_az_for_display_bearing(120, 0, 300, dipole=True) == pytest.approx(300)


def test_dipole_rotor_move_cost_long_ccw() -> None:
    assert dipole_rotor_move_cost(300, 10) == pytest.approx(290)
    assert dipole_rotor_move_cost(300, 190) == pytest.approx(110)
    assert dipole_rotor_move_cost(350, 10) == pytest.approx(20)
    assert dipole_rotor_move_cost(300, 350) == pytest.approx(50)


def test_az_pos_deg_from_d10_full_circle() -> None:
    assert az_pos_deg_from_d10(3600) == pytest.approx(360.0)
    assert az_pos_deg_from_d10(3600, 0.0) == pytest.approx(360.0)
    assert az_pos_deg_from_d10(3599) == pytest.approx(360.0)
    assert az_pos_deg_from_d10(0) == pytest.approx(0.0)
    assert az_pos_deg_from_d10(900, 905.0) == pytest.approx(90.5)


def test_shortest_delta_az_rotor_deg_homing() -> None:
    assert shortest_delta_az_rotor_deg(0.0, 360.0) == pytest.approx(360.0)
    assert shortest_delta_az_rotor_deg(350.0, 360.0) == pytest.approx(10.0)
    assert shortest_delta_az_rotor_deg(360.0, 10.0) == pytest.approx(10.0)
    assert shortest_delta_az_rotor_deg(360.0, 0.0) == pytest.approx(0.0)


def test_dipole_rotor_move_cost_from_full_circle() -> None:
    assert dipole_rotor_move_cost(360, 10) == pytest.approx(10)
    assert rotor_az_for_display_bearing(10, 0, 360, dipole=True) == pytest.approx(10)
