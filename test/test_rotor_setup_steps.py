"""Unit-Tests: Inbetriebnahme Step-Visibility und ACK-Parsing."""

from __future__ import annotations

from rotortcpbridge.rotor_setup_steps import (
    DRIVE_SET_SEQUENCE,
    ENCODER_TYPE_CHOICES,
    STEP_ALERTS,
    STEP_ANTENNA,
    STEP_CAL,
    STEP_DRIVE,
    STEP_WEATHER,
    ack_value_matches,
    compute_cpr,
    cpr_for_range,
    cpr_measure_allowed,
    drive_defaults,
    endstc_choices,
    endstc_recommendation,
    homing_setref_allowed,
    inclinometer_cal_allowed,
    jog_allowed,
    manual_setref_allowed,
    parse_encoder_type,
    parse_endstc,
    setenczero_primary,
    step_visible,
    visible_rotor_steps,
    weather_allowed,
    wind_alerts_allowed,
)
from rotortcpbridge.ui.antenna_names_panel import sanitize_antenna_name


def test_parse_encoder_type() -> None:
    assert parse_encoder_type("3") == 3
    assert parse_encoder_type("4,0") == 4
    assert parse_encoder_type("9") is None
    assert parse_encoder_type("") is None


def test_encoder_type_choices_catalog() -> None:
    vals = [v for v, _k in ENCODER_TYPE_CHOICES]
    assert vals == [1, 2, 3, 4]
    assert "SETARRTOL" in DRIVE_SET_SEQUENCE
    assert DRIVE_SET_SEQUENCE[0] == "SETROTORTYPE"


def test_sanitize_antenna_name() -> None:
    assert sanitize_antenna_name("AB#C:D$E") == "ABCDE"
    assert len(sanitize_antenna_name("1234567890")) == 9


def test_parse_endstc() -> None:
    assert parse_endstc("0") == 0
    assert parse_endstc("2") == 2
    assert parse_endstc("3") is None


def test_weather_only_type_1_2() -> None:
    assert weather_allowed(1) and weather_allowed(2)
    assert not weather_allowed(3) and not weather_allowed(4)
    assert not step_visible(STEP_WEATHER, enc_type=3)
    assert step_visible(STEP_WEATHER, enc_type=1)
    assert wind_alerts_allowed(1) and wind_alerts_allowed(2)
    assert not wind_alerts_allowed(3) and not wind_alerts_allowed(4)
    assert step_visible(STEP_ALERTS, enc_type=4)
    assert STEP_ALERTS in visible_rotor_steps(enc_type=3, enable_az=True)


def test_jog_and_cpr_only_type12_endstc0() -> None:
    assert jog_allowed(1, 0) and cpr_measure_allowed(2, 0)
    assert not jog_allowed(1, 1)
    assert not jog_allowed(3, 0)
    assert not jog_allowed(4, 0)


def test_homing_vs_manual_ref() -> None:
    assert homing_setref_allowed(1, 2)
    assert not homing_setref_allowed(1, 0)
    assert manual_setref_allowed(2, 0)
    assert not manual_setref_allowed(2, 1)


def test_ssi_and_inclinometer() -> None:
    assert setenczero_primary(3)
    assert not setenczero_primary(1)
    assert inclinometer_cal_allowed(4)
    assert not inclinometer_cal_allowed(2)


def test_endstc_matrix() -> None:
    assert endstc_choices(4) == (0, 2)
    assert endstc_choices(3) == (0,)
    assert endstc_choices(1) == (0, 1, 2)
    assert endstc_recommendation(1) == 2
    assert endstc_recommendation(4) == 0


def test_visible_steps_skip_weather_and_antenna() -> None:
    steps_t3 = visible_rotor_steps(enc_type=3, endstc=0, enable_az=True)
    assert STEP_WEATHER not in steps_t3
    assert STEP_DRIVE in steps_t3
    assert STEP_CAL in steps_t3
    steps_no_az = visible_rotor_steps(enc_type=1, endstc=0, enable_az=False)
    assert STEP_ANTENNA not in steps_no_az
    assert STEP_ANTENNA in visible_rotor_steps(enc_type=1, enable_az=True)


def test_drive_defaults_arrtol_and_axis() -> None:
    az = drive_defaults(1, "az")
    assert az["max_dg"] == 360.0
    assert az["min_pwm"] == 40.0
    assert abs(az["arrtol"] - 0.25) < 1e-9
    el4 = drive_defaults(4, "el")
    assert el4["max_dg"] == 90.0
    assert abs(el4["arrtol"] - 1.0) < 1e-9


def test_max_dg_for_rotor_type() -> None:
    from rotortcpbridge.rotor_setup_steps import DRIVE_GET_FIELDS, max_dg_for_rotor_type

    assert max_dg_for_rotor_type(2) == 90.0
    assert max_dg_for_rotor_type(3) == 180.0
    assert max_dg_for_rotor_type(1) is None
    assert max_dg_for_rotor_type(1, choice_360_720=360) == 360.0
    assert max_dg_for_rotor_type(1, choice_360_720=720) == 720.0
    assert len(DRIVE_GET_FIELDS) >= 10


def test_compute_cpr() -> None:
    assert compute_cpr(0, 1000, 90.0) == 4000
    assert compute_cpr(100, 100, 90.0) is None
    assert compute_cpr(0, 10, 0.0) is None


def test_ack_value_matches() -> None:
    assert ack_value_matches("1", "1")
    assert ack_value_matches("1,00", "1.0")
    assert ack_value_matches("40", "40;extra", kind="int")
    assert not ack_value_matches("2", "1", kind="int")
    assert not ack_value_matches("", "1")


def test_scale_cpr_basic():
    from rotortcpbridge.rotor_setup_steps import scale_cpr

    # Anzeige 85� statt 90� -> Rotor "dreht zu wenig an": CPR kleiner
    assert scale_cpr(28600, 85.0, 90.0) == round(28600 * 85 / 90)
    assert scale_cpr(28600, 90.0, 90.0) == 28600
    assert scale_cpr(28600, 180.0, 90.0) == 57200


def test_scale_cpr_rejects_bad_input():
    from rotortcpbridge.rotor_setup_steps import scale_cpr

    assert scale_cpr(28600, 3.0, 2.0) is None  # Strecke zu klein
    assert scale_cpr(28600, -50.0, 90.0) is None  # gegenl�ufig
    assert scale_cpr(0, 90.0, 90.0) is None


def test_cpr_for_range_is_not_scaled() -> None:
    assert cpr_for_range(0, 15000) == 15000
    assert cpr_for_range(-4000, 11000) == 15000
    assert cpr_for_range(0, 50) is None
