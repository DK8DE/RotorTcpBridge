"""Positionsanzeige: Rohwert ``pos_d10`` vs. geglättete UI-Position (SmoothDamp)."""

from __future__ import annotations

from rotortcpbridge.rotor_model import AxisState


def test_pos_d10_is_raw_hardware_value() -> None:
    a = AxisState()
    a.update_position_sample(1234, sample_ts=100.0)
    assert a.pos_d10 == 1234
    a.update_position_sample(2000, sample_ts=100.1)
    assert a.pos_d10 == 2000


def test_smoothed_converges_toward_target() -> None:
    a = AxisState()
    a.update_position_sample(1000, sample_ts=1000.0)
    assert a.get_smoothed_pos_d10f(1000.0) == 1000.0
    a.update_position_sample(1300, sample_ts=1001.0)
    t = 1001.0
    v = 1000.0
    for _ in range(240):
        t += 1.0 / 60.0
        v = a.get_smoothed_pos_d10f(t)
    assert abs(v - 1300.0) < 4.0


def test_large_jump_snaps_display() -> None:
    a = AxisState()
    a.update_position_sample(1000, sample_ts=0.0)
    a.get_smoothed_pos_d10f(0.0)
    a.update_position_sample(2000, sample_ts=1.0)
    assert a.smooth_pos_d10f == 2000.0
    assert a._smooth_vel_f == 0.0


def test_el_axis_clamped_and_converges() -> None:
    el = AxisState(position_wrap_360=False)
    el.update_position_sample(100, sample_ts=1000.0)
    el.get_smoothed_pos_d10f(1000.0)
    el.update_position_sample(400, sample_ts=1001.0)
    t = 1001.0
    v = 100.0
    for _ in range(240):
        t += 1.0 / 60.0
        v = el.get_smoothed_pos_d10f(t)
    assert abs(v - 400.0) < 4.0
    assert 0.0 <= v <= 900.0


def test_az_wrap_shortest_path_when_moving() -> None:
    """358° → 20°: kürzester Weg ist vorwärts (~22°), nicht 338° zurück."""
    az = AxisState(position_wrap_360=True)
    az.moving = True
    az.update_position_sample(3580, sample_ts=1000.0)
    az.get_smoothed_pos_d10f(1000.0)
    az.update_position_sample(200, sample_ts=1001.0)
    v = az.get_smoothed_pos_d10f(1001.05)
    assert v > 3580.0
