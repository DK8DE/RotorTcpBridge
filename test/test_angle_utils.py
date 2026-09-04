"""Tests für angle_utils (Winkel-Hilfsfunktionen)."""

from __future__ import annotations

import pytest

from rotortcpbridge.angle_utils import (
    az_pos_deg_from_d10,
    clamp_el,
    deg_str_to_d10,
    deg_to_d10,
    dipole_rotor_move_cost,
    fmt_deg,
    fmt_deg_d10,
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
    assert fmt_deg(95.97) == "95.9°"
    assert fmt_deg(96.15) == "96.1°"
    assert fmt_deg_d10(959) == "95.9°"
    assert fmt_deg_d10(961) == "96.1°"


def test_deg_str_to_d10_truncates_second_decimal() -> None:
    assert deg_str_to_d10("96,15") == 961
    assert deg_str_to_d10("95,97") == 959
    assert deg_str_to_d10("96,00") == 960
    assert deg_to_d10(95.97) == 959


def test_parse_getposdg_ist_d10() -> None:
    from rotortcpbridge.rotor_parse_utils import parse_getposdg_ist_d10, parse_getposdg_ist_deg

    assert parse_getposdg_ist_d10("96,15") == 961
    assert parse_getposdg_ist_deg("95,97") == pytest.approx(95.9)


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
    # Nahe Nord mit Nullübertritt: Regler fährt lang → Gegenkeule.
    assert rotor_az_for_display_bearing(10, 0, 350, dipole=True) == pytest.approx(190)
    # Gegenkeule zeigt schon aufs Ziel → nicht drehen.
    assert rotor_az_for_display_bearing(120, 0, 300, dipole=True) == pytest.approx(300)


def test_dipole_rotor_move_cost_long_ccw() -> None:
    assert dipole_rotor_move_cost(300, 10) == pytest.approx(290)
    assert dipole_rotor_move_cost(300, 190) == pytest.approx(110)
    assert dipole_rotor_move_cost(350, 10) == pytest.approx(340)
    assert dipole_rotor_move_cost(300, 350) == pytest.approx(50)


def test_az_pos_deg_from_d10_full_circle() -> None:
    assert az_pos_deg_from_d10(3600) == pytest.approx(360.0)
    assert az_pos_deg_from_d10(3600, 0.0) == pytest.approx(360.0)
    # 359,9° bleibt 359,9 — nicht auf Homing-Marke 360,0 aufrunden
    assert az_pos_deg_from_d10(3599) == pytest.approx(359.9)
    assert is_az_pos_at_full_circle_d10(3599) is False
    assert is_az_pos_at_full_circle_d10(3600) is True
    assert az_pos_deg_from_d10(0) == pytest.approx(0.0)
    assert az_pos_deg_from_d10(900, 905.0) == pytest.approx(90.5)
    assert is_az_pos_at_full_circle_d10(3601) is False
    assert is_az_pos_at_full_circle_d10(4200) is False


def test_fmt_deg_extended_beyond_360() -> None:
    assert fmt_deg(430.0) == "430.0°"
    assert fmt_deg_d10(4300) == "430.0°"
    assert fmt_deg(420.0) == "420.0°"


def test_az_pos_deg_from_d10_extended_range() -> None:
    from rotortcpbridge.angle_utils import antenna_bearing_from_rotor_and_offset

    assert az_pos_deg_from_d10(4200, max_d10=4300) == pytest.approx(420.0)
    assert az_pos_deg_from_d10(4300, max_d10=4300) == pytest.approx(430.0)
    # Klassisch wrappt weiter
    assert az_pos_deg_from_d10(4200, max_d10=3600) == pytest.approx(60.0)
    assert antenna_bearing_from_rotor_and_offset(420.0, 10.0, max_d10=4300) == pytest.approx(430.0)
    assert antenna_bearing_from_rotor_and_offset(420.0, 10.0, max_d10=3600) == pytest.approx(70.0)
    # Versatz darf Summe über MAXDG heben (sonst klebt Anzeige bei großem Offset)
    assert antenna_bearing_from_rotor_and_offset(350.0, 180.0, max_d10=4200) == pytest.approx(530.0)
    assert antenna_bearing_from_rotor_and_offset(240.0, 180.0, max_d10=4200) == pytest.approx(420.0)
    assert antenna_bearing_from_rotor_and_offset(241.0, 180.0, max_d10=4200) == pytest.approx(421.0)


def test_pick_nearest_and_shortest_target_extended() -> None:
    from rotortcpbridge.angle_utils import pick_nearest_az_deg, resolve_external_az_d10

    # Ist 420°, Wunsch 70° bei MAXDG 430° → 430° (10°), nicht 70° (350°)
    assert pick_nearest_az_deg(70.0, 420.0, 430.0) == pytest.approx(430.0)
    assert rotor_az_for_display_bearing(70.0, 0.0, 420.0, max_deg=430.0) == pytest.approx(430.0)
    # MAXDG 360°: unverändert 70°
    assert rotor_az_for_display_bearing(70.0, 0.0, 350.0, max_deg=360.0) == pytest.approx(70.0)
    assert pick_nearest_az_deg(70.0, 350.0, 360.0) == pytest.approx(70.0)

    # Extern exact: Kompassrichtung 0…360 (auch wenn Client Overlap 370 sendet)
    assert resolve_external_az_d10(
        700, current_d10=4200, max_d10=4300, shortest_path=False
    ) == 700
    assert resolve_external_az_d10(
        3700, current_d10=3550, max_d10=4300, shortest_path=False
    ) == 100
    # Extern: kürzerer Weg (10° bzw. 370°-Overlap → 370/430 je nach Ist)
    assert resolve_external_az_d10(
        700, current_d10=4200, max_d10=4300, shortest_path=True
    ) == 4300
    assert resolve_external_az_d10(
        100, current_d10=3550, max_d10=4300, shortest_path=True
    ) == 3700
    assert resolve_external_az_d10(
        3700, current_d10=3550, max_d10=4300, shortest_path=True
    ) == 3700
    # Expliziter Overlap-Winkel bleibt erhalten (nicht auf 10° zurück)
    assert resolve_external_az_d10(
        3700, current_d10=500, max_d10=4300, shortest_path=True
    ) == 3700
    # Genau 360° und 720° (Pst/Big RAS) beibehalten
    assert resolve_external_az_d10(
        3600, current_d10=100, max_d10=7200, shortest_path=True
    ) == 3600
    assert resolve_external_az_d10(
        7200, current_d10=7000, max_d10=7200, shortest_path=True
    ) == 7200
    # Über MAXDG → klemmen
    assert resolve_external_az_d10(
        8000, current_d10=100, max_d10=7200, shortest_path=True
    ) == 7200
    # Klassischer Bereich: Wrap
    assert resolve_external_az_d10(
        3700, current_d10=0, max_d10=3600, shortest_path=False
    ) == 100
    # Homing-Marke 360,0° darf nicht auf 0 gewickelt werden (SETHOMERETURN=0)
    assert resolve_external_az_d10(
        3600, current_d10=3600, max_d10=3600, shortest_path=False
    ) == 3600
    assert resolve_external_az_d10(
        3600, current_d10=100, max_d10=3600, shortest_path=False
    ) == 3600

    from rotortcpbridge.angle_utils import (
        az_d10_equivalent_position,
        az_d10_for_external_report,
    )

    assert az_d10_equivalent_position(3600, 0) is True
    assert az_d10_equivalent_position(0, 3600) is True
    assert az_d10_equivalent_position(3600, 3600) is True
    assert az_d10_equivalent_position(3600, 10) is False
    assert az_d10_equivalent_position(3600, 0, max_d10=7200) is False

    # Report: kürzerer Weg ohne 0…360 → Rohwert (SPID-Clamp ≤639,9°)
    assert az_d10_for_external_report(
        3700, shortest_path=True, report_mod360=False
    ) == 3700
    assert az_d10_for_external_report(
        7200, shortest_path=True, report_mod360=False
    ) == 6399
    # Report: kürzerer Weg mit 0…360 → wrap
    assert az_d10_for_external_report(
        3700, shortest_path=True, report_mod360=True
    ) == 100
    # Report: Exact → immer wrap — außer Homing-Marke 360,0°
    assert az_d10_for_external_report(
        3700, shortest_path=False, report_mod360=False
    ) == 100
    assert az_d10_for_external_report(
        3600, shortest_path=False, report_mod360=False
    ) == 3600


def test_shortest_delta_az_rotor_deg_homing() -> None:
    assert shortest_delta_az_rotor_deg(0.0, 360.0) == pytest.approx(360.0)
    assert shortest_delta_az_rotor_deg(350.0, 360.0) == pytest.approx(10.0)
    assert shortest_delta_az_rotor_deg(360.0, 10.0) == pytest.approx(10.0)
    assert shortest_delta_az_rotor_deg(360.0, 0.0) == pytest.approx(0.0)


def test_dipole_rotor_move_cost_from_full_circle() -> None:
    assert dipole_rotor_move_cost(360, 10) == pytest.approx(350)
    assert rotor_az_for_display_bearing(10, 0, 360, dipole=True) == pytest.approx(190)


def test_dipole_near_east_picks_back_lobe_for_small_bearing_change() -> None:
    """83°→99° mit 90° Versatz nahe Ost-Anschlag: Gegenkeule statt Volldrehung über Null."""
    assert dipole_rotor_move_cost(353, 9) == pytest.approx(344)
    assert dipole_rotor_move_cost(360, 9) == pytest.approx(351)
    assert rotor_az_for_display_bearing(99, 90, 353, dipole=True) == pytest.approx(189)
    assert rotor_az_for_display_bearing(99, 90, 360, dipole=True) == pytest.approx(189)


def test_dipole_routing_ignores_stuck_smooth_at_zero() -> None:
    """Glättung bei 0° / Roh-Ist 353°: ohne Raw-Pos würde fälschlich Hauptkeule (Volldrehung) gewählt."""
    from rotortcpbridge.angle_utils import current_rotor_az_deg, raw_rotor_az_deg_from_axis

    class _Az:
        pos_d10 = 3530

        def get_smoothed_pos_d10f(self, now: float) -> float:
            return 0.0

    az = _Az()
    assert raw_rotor_az_deg_from_axis(az) == pytest.approx(353.0)
    assert current_rotor_az_deg(az) == pytest.approx(0.0)
    assert rotor_az_for_display_bearing(99, 90, 0.0, dipole=True) == pytest.approx(9.0)
    assert rotor_az_for_display_bearing(99, 90, 353.0, dipole=True) == pytest.approx(189.0)


def test_dipole_continuity_prefers_last_lobe_on_tie() -> None:
    """Kleine Peilungsänderung: auf bereits aktiver Gegenkeule bleiben."""
    assert (
        rotor_az_for_display_bearing(99, 90, 353, dipole=True, last_rotor_az=189.0)
        == pytest.approx(189.0)
    )


def test_dipole_west_from_east_stop_uses_back_lobe() -> None:
    """99°→80° nahe Ost-Anschlag (Rotor ~9°): Gegenkeule ~170°, nicht Volldrehung über 350°."""
    assert dipole_rotor_move_cost(9, 350) == pytest.approx(341)
    assert dipole_rotor_move_cost(9, 170) == pytest.approx(161)
    assert rotor_az_for_display_bearing(80, 90, 9, dipole=True) == pytest.approx(170)
