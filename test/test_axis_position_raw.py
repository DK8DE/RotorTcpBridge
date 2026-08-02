"""Positionsanzeige: Rohwert ``pos_d10`` vs. geglättete UI-Position (SmoothDamp)."""

from __future__ import annotations

import pytest

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


def test_az_smoothing_reaches_full_circle_after_homing() -> None:
    """Homing-Ende 360,0°: Glättung darf nicht bei smooth≈0 stehen bleiben."""
    az = AxisState(position_wrap_360=True)
    az.update_position_sample(0, sample_ts=1000.0)
    az.get_smoothed_pos_d10f(1000.0)
    az.update_position_sample(3600, sample_ts=1001.0)
    t = 1001.0
    v = 0.0
    for _ in range(480):
        t += 1.0 / 60.0
        v = az.get_smoothed_pos_d10f(t)
    assert v >= 3599.0


def test_getposdg_jump_reject_coherent_series_triggers_resync() -> None:
    """Veraltetes pos_d10 (~180°) + kohärente Hardware ~12° → nach 2. Sample Resync."""
    az = AxisState(position_wrap_360=True)
    az.referenced = True
    az.update_position_sample(1797, sample_ts=1000.0)
    assert az.note_getposdg_jump_reject(118) is False
    assert az.pos_reject_streak == 1
    assert az.note_getposdg_jump_reject(121) is True
    assert az.pos_reject_streak == 2
    az.clear_getposdg_jump_reject()
    assert az.pos_reject_streak == 0


def test_apply_position_resync_after_rehoming() -> None:
    """Nach erneutem Homing: großer Positions-Sprung muss sofort in Anzeige/Soll landen."""
    az = AxisState(position_wrap_360=True)
    az.update_position_sample(1800, sample_ts=1000.0)
    az.get_smoothed_pos_d10f(1000.0)
    az.pos_resync_pending = True
    az.apply_position_resync(3600, sample_ts=1002.0)
    assert az.pos_d10 == 3600
    assert az.smooth_pos_d10f == pytest.approx(3600.0)
    assert az.target_d10 == 3600
    assert az.pos_resync_pending is False


def test_smoothing_lerps_between_getposdg_without_overshoot() -> None:
    """Zwischen GETPOSDG: monoton zum neuen Sample, nie darüber hinaus (kein Vor/Zurück)."""
    az = AxisState(position_wrap_360=True)
    az.moving = True
    az.update_position_sample(1000, sample_ts=1000.0, expected_period_s=0.25)
    az.get_smoothed_pos_d10f(1000.0)
    az.update_position_sample(1030, sample_ts=1000.25, expected_period_s=0.25)
    vals = [az.get_smoothed_pos_d10f(1000.25 + i * (1.0 / 60.0)) for i in range(20)]
    assert vals[0] <= vals[-1] <= 1030.0 + 1e-6
    assert vals[-1] == pytest.approx(1030.0, abs=0.5)
    # Kein Zurückrudern innerhalb des Segments
    for a, b in zip(vals, vals[1:]):
        assert b + 1e-6 >= a
