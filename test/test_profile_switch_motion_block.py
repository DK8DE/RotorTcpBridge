"""Profilwechsel ist gesperrt, solange eine aktive Achse fährt."""

from __future__ import annotations

from types import SimpleNamespace

from rotortcpbridge.rotor_controller import RotorController


def _fake(az_moving=False, el_moving=False, enable_el=False, ref=False):
    return SimpleNamespace(
        az=SimpleNamespace(moving=az_moving, ref_poll_active=ref),
        el=SimpleNamespace(moving=el_moving, ref_poll_active=False),
        enable_az=True,
        enable_el=enable_el,
    )


def test_idle_not_blocked() -> None:
    assert RotorController.is_any_axis_moving(_fake()) is False


def test_az_moving_blocks() -> None:
    assert RotorController.is_any_axis_moving(_fake(az_moving=True)) is True


def test_referencing_blocks() -> None:
    assert RotorController.is_any_axis_moving(_fake(ref=True)) is True


def test_disabled_el_ignored_enabled_el_counts() -> None:
    assert RotorController.is_any_axis_moving(_fake(el_moving=True, enable_el=False)) is False
    assert RotorController.is_any_axis_moving(_fake(el_moving=True, enable_el=True)) is True
