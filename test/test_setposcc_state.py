"""Lokaler State für SETPOSCC (Kompass-Soll) vs. SETPOSDG (Motor-Ziel)."""

from __future__ import annotations

import time
from typing import cast

from rotortcpbridge.hardware_client import HardwareClient
from rotortcpbridge.rs485_protocol import Telegram
from rotortcpbridge.rotor_controller import RotorController


class _Log:
    def write(self, *args, **kwargs) -> None:
        pass


class _Hw:
    """Leerer Stub — diese Tests nutzen keinen echten HardwareClient."""

    pass


def _hw_stub() -> HardwareClient:
    return cast(HardwareClient, _Hw())


def test_setposcc_sets_compass_only_no_moving() -> None:
    c = RotorController(_hw_stub(), master_id=1, slave_az=20, slave_el=21, log=_Log())
    c.az.moving = False
    c._apply_local_state_for_ui_command(20, "SETPOSCC", "90,5")
    assert c.az.compass_target_d10 == 905
    assert c.az.moving is False


def test_setposdg_clears_compass_and_sets_moving() -> None:
    c = RotorController(_hw_stub(), master_id=1, slave_az=20, slave_el=21, log=_Log())
    c.az.compass_target_d10 = 100
    c.az.moving = False
    c._apply_local_state_for_ui_command(20, "SETPOSDG", "12,3")
    assert c.az.compass_target_d10 is None
    assert c.az.target_d10 == 123
    assert c.az.moving is True


def test_setposcc_applies_while_moving() -> None:
    """Encoder-Soll auch während Fahrt (Sollzeiger folgt SETPOSCC)."""
    c = RotorController(_hw_stub(), master_id=1, slave_az=20, slave_el=21, log=_Log())
    c.az.moving = True
    c.az.compass_target_d10 = None
    c._apply_local_state_for_ui_command(20, "SETPOSCC", "90,5")
    assert c.az.compass_target_d10 == 905


def test_setposcc_applies_while_moving_despite_ignore_window() -> None:
    """Während Fahrt: SETPOSCC vom Encoder (#2:…) trotz setposcc_ignore_until_ts (nach SETPOSDG)."""
    c = RotorController(_hw_stub(), master_id=1, slave_az=20, slave_el=21, log=_Log())
    c.az.moving = True
    c.az.setposcc_ignore_until_ts = time.time() + 1.0
    c._apply_local_state_for_ui_command(20, "SETPOSCC", "90,5")
    assert c.az.compass_target_d10 == 905


def test_setposcc_suppressed_after_setposdg_when_idle() -> None:
    c = RotorController(_hw_stub(), master_id=1, slave_az=20, slave_el=21, log=_Log())
    c.az.moving = False
    c.az.setposcc_ignore_until_ts = time.time() + 1.0
    c._apply_local_state_for_ui_command(20, "SETPOSCC", "90,5")
    assert c.az.compass_target_d10 is None


def test_setposcc_applies_even_if_far_from_motor_target() -> None:
    c = RotorController(_hw_stub(), master_id=1, slave_az=20, slave_el=21, log=_Log())
    c.az.moving = False
    c.az.target_d10 = 2951
    c.az.compass_target_d10 = None
    c._apply_local_state_for_ui_command(20, "SETPOSCC", "310,0")
    assert c.az.compass_target_d10 == 3100


def test_setposdg_duplicate_on_bus_preserves_compass() -> None:
    """Echo SETPOSDG = aktuelles Motorziel darf SETPOSCC (Encoder) nicht löschen."""
    c = RotorController(_hw_stub(), master_id=1, slave_az=20, slave_el=21, log=_Log())
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
    c = RotorController(_hw_stub(), master_id=1, slave_az=20, slave_el=21, log=_Log())
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
    c = RotorController(_hw_stub(), master_id=1, slave_az=20, slave_el=21, log=_Log())
    c.az.moving = False
    c.az.target_d10 = 2951
    c.az.compass_target_d10 = 3100
    c._apply_local_state_for_ui_command(20, "SETPOSDG", "295,1")
    assert c.az.compass_target_d10 is None
    assert c.az.moving is True


def test_acc_bins_poll_not_from_cfg_when_compass_closed() -> None:
    """Strom-Flags (z. B. nach CompassWindow-Init aus cfg) ohne gezeigtes Kompassfenster → kein ACC."""
    c = RotorController(_hw_stub(), master_id=1, slave_az=20, slave_el=21, log=_Log())
    c.set_statistics_window_open(False)
    c.set_compass_window_open(False)
    c.set_compass_strom_heatmap_active(True, True)
    assert c._acc_bins_poll_enabled() is False


def test_acc_bins_poll_when_compass_open_and_strom_heatmap() -> None:
    c = RotorController(_hw_stub(), master_id=1, slave_az=20, slave_el=21, log=_Log())
    c.set_statistics_window_open(False)
    c.set_compass_window_open(True)
    c.set_compass_strom_heatmap_active(True, False)
    assert c._acc_bins_poll_enabled() is True


def test_setposcc_controller_to_bridge_updates_compass() -> None:
    """#2:1:SETPOSCC = Controller SRC 2 → Bridge DST 1 (master_id); Kompass-Soll."""
    c = RotorController(
        _hw_stub(),
        master_id=1,
        slave_az=20,
        slave_el=21,
        log=_Log(),
        setposcc_controller_src_id=0,
    )
    c.az.moving = False
    tel = Telegram(src=2, dst=1, cmd="SETPOSCC", params="92,0", cs=0.0, ok=True)
    assert c._tel_dst_allowed(tel) is True
    c._on_async_tel(tel)
    assert c.az.compass_target_d10 == 920


def test_setposcc_controller_src_filter_requires_cont_id() -> None:
    """Mit gesetztem Controller (cont_id=2): nur SRC 2 zählt für SETPOSCC."""
    c = RotorController(
        _hw_stub(),
        master_id=1,
        slave_az=20,
        slave_el=21,
        log=_Log(),
        setposcc_controller_src_id=2,
    )
    c.az.moving = False
    ok_tel = Telegram(src=2, dst=1, cmd="SETPOSCC", params="90,0", cs=0.0, ok=True)
    c._on_async_tel(ok_tel)
    assert c.az.compass_target_d10 == 900
    c.az.compass_target_d10 = None
    wrong_src = Telegram(src=1, dst=1, cmd="SETPOSCC", params="45,0", cs=0.0, ok=True)
    c._on_async_tel(wrong_src)
    assert c.az.compass_target_d10 is None


def test_setposcc_payload_angle_semicolon_rotor_id() -> None:
    """Winkel;Rotor-ID: Winkel ist 151,30° (nicht fälschlich 20°)."""
    c = RotorController(_hw_stub(), master_id=1, slave_az=20, slave_el=21, log=_Log())
    c.az.moving = False
    c._apply_local_state_for_ui_command(20, "SETPOSCC", "151,30;20")
    assert c.az.compass_target_d10 == 1513


def test_setposcc_bridge_payload_rotor_id_selects_el() -> None:
    """#2:1:… mit ;21 → EL-Kompass, nicht AZ."""
    c = RotorController(
        _hw_stub(),
        master_id=1,
        slave_az=20,
        slave_el=21,
        log=_Log(),
        enable_az=True,
        enable_el=True,
    )
    c.el.moving = False
    tel = Telegram(src=2, dst=1, cmd="SETPOSCC", params="45,0;21", cs=0.0, ok=True)
    c._on_async_tel(tel)
    assert c.el.compass_target_d10 == 450
    assert c.az.compass_target_d10 is None


def test_setposcc_unknown_rotor_id_in_payload_dropped() -> None:
    c = RotorController(
        _hw_stub(),
        master_id=1,
        slave_az=20,
        slave_el=21,
        log=_Log(),
        enable_az=True,
        enable_el=True,
    )
    c.az.moving = False
    c.az.compass_target_d10 = 111
    tel = Telegram(src=2, dst=1, cmd="SETPOSCC", params="99,0;99", cs=0.0, ok=True)
    c._on_async_tel(tel)
    assert c.az.compass_target_d10 == 111


def test_setposcc_to_slave_from_controller_still_applies() -> None:
    """#2:20:SETPOSCC: direkt an Rotor, SRC = Controller — gleicher cont_id-Filter."""
    c = RotorController(
        _hw_stub(),
        master_id=1,
        slave_az=20,
        slave_el=21,
        log=_Log(),
        setposcc_controller_src_id=2,
    )
    c.az.moving = False
    tel = Telegram(src=2, dst=20, cmd="SETPOSCC", params="88,0", cs=0.0, ok=True)
    assert c._tel_dst_allowed(tel) is True
    c._on_async_tel(tel)
    assert c.az.compass_target_d10 == 880


def test_compass_window_open_clears_strom_flags_for_fresh_sync() -> None:
    """Beim Anzeigen des Kompass: alte Strom-Flags weg, bis UI wieder notify setzt."""
    c = RotorController(_hw_stub(), master_id=1, slave_az=20, slave_el=21, log=_Log())
    c.set_statistics_window_open(False)
    c.set_compass_strom_heatmap_active(True, True)
    c.set_compass_window_open(True)
    assert c._compass_strom_heatmap_az is False
    assert c._compass_strom_heatmap_el is False
    assert c._acc_bins_poll_enabled() is False
    assert c._acc_bins_strom_live() is False
