"""GETROTORTYPE → EL-GUI Maxwinkel (90/180/360)."""

from rotortcpbridge.angle_utils import (
    clamp_el,
    el_max_deg_from_rotor_type,
    el_pos_max_d10_from_rotor_type,
)


def test_el_max_from_rotor_type():
    assert el_max_deg_from_rotor_type(None) == 90.0
    assert el_max_deg_from_rotor_type(1) == 360.0
    assert el_max_deg_from_rotor_type(2) == 90.0
    assert el_max_deg_from_rotor_type(3) == 180.0


def test_el_pos_max_d10():
    assert el_pos_max_d10_from_rotor_type(1) == 3600
    assert el_pos_max_d10_from_rotor_type(2) == 900
    assert el_pos_max_d10_from_rotor_type(3) == 1800


def test_clamp_el_allows_360():
    assert clamp_el(270.0, 360.0) == 270.0
    assert clamp_el(400.0, 360.0) == 360.0
    assert clamp_el(120.0, 90.0) == 90.0
