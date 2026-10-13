"""Polling: während Soll≠Ist nur GETPOSDG, keine Idle-Abfragen."""

from __future__ import annotations

from typing import cast

from rotortcpbridge.hardware_client import HardwareClient
from rotortcpbridge.rotor_controller import RotorController


class _Log:
    def write(self, *args, **kwargs) -> None:
        pass


class _Hw:
    pass


def _ctrl() -> RotorController:
    return RotorController(
        cast(HardwareClient, _Hw()),
        master_id=0,
        slave_az=20,
        slave_el=21,
        log=_Log(),
    )


def test_axis_motion_by_target_gap_while_goal_pending() -> None:
    c = _ctrl()
    c.az.referenced = True
    c.az.pos_d10 = 968
    c.az.target_d10 = 862
    c.az.last_set_sent_target_d10 = 862
    c.az.last_set_sent_ts = 1.0
    assert c._axis_motion_by_target_gap(c.az, 100.0) is True


def test_axis_motion_by_target_gap_no_goal_after_reconnect() -> None:
    c = _ctrl()
    c.az.referenced = True
    c.az.pos_d10 = 100
    c.az.target_d10 = 500
    c.az.last_set_sent_target_d10 = None
    assert c._axis_motion_by_target_gap(c.az, 100.0) is False


def test_axis_motion_by_target_gap_not_after_arrival() -> None:
    c = _ctrl()
    c.az.referenced = True
    c.az.pos_d10 = 862
    c.az.target_d10 = 862
    c.az.last_set_sent_target_d10 = 862
    assert c._axis_motion_by_target_gap(c.az, 100.0) is False


def test_axis_target_pending_prefers_last_set_sent() -> None:
    """target_d10 kann auf Ist springen — last_set hält die Fahrt-Sperre."""
    c = _ctrl()
    c.az.referenced = True
    c.az.pos_d10 = 3100
    c.az.target_d10 = 3100
    c.az.last_set_sent_target_d10 = 2485
    assert c._axis_target_pending(c.az) is True
    assert c._axis_motion_by_target_gap(c.az, 100.0) is True


def test_motion_poll_restrict_long_after_setpos() -> None:
    """Kein 3s-Limit mehr: auch nach langer Fahrt nur GETPOSDG."""
    c = _ctrl()
    c.az.referenced = True
    c.az.moving = False
    c.az.pos_d10 = 500
    c.az.target_d10 = 100
    c.az.last_set_sent_target_d10 = 100
    c.az.last_set_sent_ts = 0.0
    c.az.last_motion_ts = 0.0
    assert c._motion_poll_restrict_active(120.0, 0.2) is True


def test_setposcc_hold_blocks_poll_restrict_until_setposdg() -> None:
    c = _ctrl()
    assert c._setposcc_poll_hold is False
    c.note_setposcc_bus_activity()
    assert c._setposcc_poll_hold is True
    assert c._motion_poll_restrict_active(100.0, 0.2) is True
    c.note_setposdg_poll_restrict()
    assert c._setposcc_poll_hold is False


def test_usb_remote_yields_to_bus_master_but_not_controller() -> None:
    """Remote-USB: App-ID auf dem Bus → Yield; Controller-ID (USB) → kein Yield."""
    c = _ctrl()
    c.usb_remote = True
    c._controller_cont_id = 2
    c.note_foreign_master_activity(c.az, set_target=True, foreign_master_id=2)
    assert c.foreign_master_yield_active() is False
    c.note_foreign_master_activity(c.az, set_target=True, foreign_master_id=7)
    assert c.foreign_master_yield_active() is True
    assert bool(getattr(c.az, "foreign_follow_active", False)) is True


def test_setposcc_hold_expires_without_setposdg() -> None:
    """Sicherheitsnetz: ohne SETPOSDG löst sich der CC-Hold nach dem Timeout."""
    import time as _t

    c = _ctrl()
    c.note_setposcc_bus_activity()
    now = _t.time()
    assert c.cc_poll_hold_active(now) is True
    assert c.cc_poll_hold_active(now + 5.0) is False
    assert c._setposcc_poll_hold is False


def test_drive_sync_hold_blocks_setpos_until_data() -> None:
    c = _ctrl()
    c.hw.is_connected = lambda: True  # type: ignore[attr-defined]
    c.enable_el = False
    c.reset_bus_discovery_state()
    assert c.drive_ready() is False
    sent: list[str] = []
    c.hw.send_request = lambda req: sent.append(getattr(req, "line", "") or "")  # type: ignore[method-assign]
    c.set_az_deg(123.0, force=True)
    assert sent == []
    now = 1000.0
    c.encoder_type = 1
    c.encoder_type_known = True
    c._max_deg_known = True
    c._aselect_known = True
    c.az.antoff1 = 0.0
    c.az.antoff2 = 60.0
    c.az.antoff3 = 120.0
    c.az.ref_status_known = True
    c.az.referenced = True
    c.az._last_sample_ts = now
    c._drive_sync_since_ts = now - 1.0
    assert c.drive_ready() is True
    c.set_az_deg(123.0, force=True)
    assert any("SETPOSDG" in line for line in sent)


def test_drive_sync_hold_timeout_releases() -> None:
    import time as _t

    c = _ctrl()
    c.reset_bus_discovery_state()
    c._drive_sync_since_ts = _t.time() - 21.0
    assert c.drive_ready() is True
    assert c._drive_sync_hold is False
