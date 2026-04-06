"""Lokaler State für SETPOSCC (Kompass-Soll) vs. SETPOSDG (Motor-Ziel)."""

from __future__ import annotations

import time

from rotortcpbridge.rotor_controller import RotorController


class _Log:
    def write(self, *args, **kwargs) -> None:
        pass


class _Hw:
    pass


def test_setposcc_sets_compass_only_no_moving() -> None:
    c = RotorController(_Hw(), master_id=1, slave_az=20, slave_el=21, log=_Log())
    c.az.moving = False
    c._apply_local_state_for_ui_command(20, "SETPOSCC", "90,5")
    assert c.az.compass_target_d10 == 905
    assert c.az.moving is False


def test_setposdg_clears_compass_and_sets_moving() -> None:
    c = RotorController(_Hw(), master_id=1, slave_az=20, slave_el=21, log=_Log())
    c.az.compass_target_d10 = 100
    c.az.moving = False
    c._apply_local_state_for_ui_command(20, "SETPOSDG", "12,3")
    assert c.az.compass_target_d10 is None
    assert c.az.target_d10 == 123
    assert c.az.moving is True


def test_setposcc_applies_while_moving() -> None:
    """Encoder-Soll auch während Fahrt (Sollzeiger folgt SETPOSCC)."""
    c = RotorController(_Hw(), master_id=1, slave_az=20, slave_el=21, log=_Log())
    c.az.moving = True
    c.az.compass_target_d10 = None
    c._apply_local_state_for_ui_command(20, "SETPOSCC", "90,5")
    assert c.az.compass_target_d10 == 905


def test_setposcc_suppressed_shortly_after_setposdg_even_while_moving() -> None:
    c = RotorController(_Hw(), master_id=1, slave_az=20, slave_el=21, log=_Log())
    c.az.moving = True
    c.az.setposcc_ignore_until_ts = time.time() + 1.0
    c._apply_local_state_for_ui_command(20, "SETPOSCC", "90,5")
    assert c.az.compass_target_d10 is None


def test_setposcc_applies_even_if_far_from_motor_target() -> None:
    c = RotorController(_Hw(), master_id=1, slave_az=20, slave_el=21, log=_Log())
    c.az.moving = False
    c.az.target_d10 = 2951
    c.az.compass_target_d10 = None
    c._apply_local_state_for_ui_command(20, "SETPOSCC", "310,0")
    assert c.az.compass_target_d10 == 3100


def test_setposdg_duplicate_on_bus_preserves_compass() -> None:
    """Echo SETPOSDG = aktuelles Motorziel darf SETPOSCC (Encoder) nicht löschen."""
    c = RotorController(_Hw(), master_id=1, slave_az=20, slave_el=21, log=_Log())
    c.az.moving = False
    c.az.target_d10 = 2951
    c.az.compass_target_d10 = 3100
    c._apply_local_state_for_ui_command(
        20, "SETPOSDG", "295,1", from_bus_sniff=True
    )
    assert c.az.compass_target_d10 == 3100
    assert c.az.target_d10 == 2951
    assert c.az.moving is False


def test_setposdg_duplicate_while_moving_preserves_compass() -> None:
    """Während Fahrt: gleiches Echo wie Motorziel, aber Encoder-Soll (SETPOSCC) anders — nicht überschreiben."""
    c = RotorController(_Hw(), master_id=1, slave_az=20, slave_el=21, log=_Log())
    c.az.moving = True
    c.az.target_d10 = 2951
    c.az.compass_target_d10 = 3100
    c._apply_local_state_for_ui_command(
        20, "SETPOSDG", "295,1", from_bus_sniff=True
    )
    assert c.az.compass_target_d10 == 3100
    assert c.az.target_d10 == 2951
    assert c.az.moving is True


def test_setposdg_from_gui_clears_compass_even_if_same_angle() -> None:
    c = RotorController(_Hw(), master_id=1, slave_az=20, slave_el=21, log=_Log())
    c.az.moving = False
    c.az.target_d10 = 2951
    c.az.compass_target_d10 = 3100
    c._apply_local_state_for_ui_command(20, "SETPOSDG", "295,1")
    assert c.az.compass_target_d10 is None
    assert c.az.moving is True


def test_acc_bins_poll_not_from_cfg_when_compass_closed() -> None:
    """Strom-Flags (z. B. nach CompassWindow-Init aus cfg) ohne gezeigtes Kompassfenster → kein ACC."""
    c = RotorController(_Hw(), master_id=1, slave_az=20, slave_el=21, log=_Log())
    c.set_statistics_window_open(False)
    c.set_compass_window_open(False)
    c.set_compass_strom_heatmap_active(True, True)
    assert c._acc_bins_poll_enabled() is False


def test_acc_bins_poll_when_compass_open_and_strom_heatmap() -> None:
    c = RotorController(_Hw(), master_id=1, slave_az=20, slave_el=21, log=_Log())
    c.set_statistics_window_open(False)
    c.set_compass_window_open(True)
    c.set_compass_strom_heatmap_active(True, False)
    assert c._acc_bins_poll_enabled() is True


def test_compass_window_open_clears_strom_flags_for_fresh_sync() -> None:
    """Beim Anzeigen des Kompass: alte Strom-Flags weg, bis UI wieder notify setzt."""
    c = RotorController(_Hw(), master_id=1, slave_az=20, slave_el=21, log=_Log())
    c.set_statistics_window_open(False)
    c.set_compass_strom_heatmap_active(True, True)
    c.set_compass_window_open(True)
    assert c._compass_strom_heatmap_az is False
    assert c._compass_strom_heatmap_el is False
    assert c._acc_bins_poll_enabled() is True
    assert c._acc_bins_strom_live() is False
