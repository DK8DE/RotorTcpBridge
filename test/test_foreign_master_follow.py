"""Zweiter Bus-Master: denselben Rotor mitsteuern — Soll/Ist mitübernehmen."""

from __future__ import annotations

import time
from typing import cast

from rotortcpbridge.hardware_client import HardwareClient
from rotortcpbridge.rs485_protocol import Telegram, parse
from rotortcpbridge.rotor_controller import RotorController


class _Log:
    def write(self, *args, **kwargs) -> None:
        pass


class _Hw:
    pass


def _hw_stub() -> HardwareClient:
    return cast(HardwareClient, _Hw())


def test_ack_setposdg_angle_from_foreign_master_adopts_target() -> None:
    """#20:7:ACK_SETPOSDG:323,28 — Ziel aus ACK, Master-ID egal (nur Rotor-SRC zählt)."""
    c = RotorController(
        _hw_stub(),
        master_id=0,
        slave_az=20,
        slave_el=21,
        log=_Log(),
        setposcc_controller_src_id=2,
    )
    c.az.pos_d10 = 3000
    c.az.moving = False
    tel = parse("#20:7:ACK_SETPOSDG:323,28:350,28$")
    assert tel is not None
    assert c._tel_dst_allowed(tel) is True
    c._on_async_tel(tel)
    assert c.az.target_d10 == 3233
    assert c.az.moving is True


def test_ack_setposdg_boolean_ok_only_marks_moving() -> None:
    """Klassisches ACK_SETPOSDG:1 — kein Winkel, nur moving."""
    c = RotorController(_hw_stub(), master_id=0, slave_az=20, slave_el=21, log=_Log())
    c.az.target_d10 = 1000
    c.az.moving = False
    tel = Telegram(src=20, dst=7, cmd="ACK_SETPOSDG", params="1", cs=0.0, ok=True)
    c._on_async_tel(tel)
    assert c.az.target_d10 == 1000
    assert c.az.moving is True


def test_foreign_ack_getposdg_updates_pos_after_own_ack() -> None:
    """Nach eigenem ACK weiterhin #20:7:ACK_GETPOSDG für Ist-Anzeige auswerten."""
    c = RotorController(_hw_stub(), master_id=0, slave_az=20, slave_el=21, log=_Log())
    c.az.pos_d10 = 3000
    c.az.referenced = True
    c.az._last_sample_ts = 1.0
    c.az.pos_poll_sent_ts = 100.0
    c.az.pos_poll_last_ack_ts = 100.1  # eigenes ACK schon da
    c.az.pos_poll_inflight = False

    foreign = parse("#20:7:ACK_GETPOSDG:332,63:359,63$")
    assert foreign is not None
    assert c._tel_dst_allowed(foreign) is True
    c._on_async_tel(foreign)
    assert c.az.pos_d10 == 3326


def test_foreign_ack_getposdg_does_not_steal_own_rtt_but_clears_inflight() -> None:
    """Fremdes ACK: Position ja; Poll-Yield setzt inflight zurück (wir pollen nicht weiter)."""
    c = RotorController(_hw_stub(), master_id=0, slave_az=20, slave_el=21, log=_Log())
    c.az.pos_d10 = 3000
    c.az.referenced = True
    c.az._last_sample_ts = 1.0
    c.az.pos_poll_sent_ts = 200.0
    c.az.pos_poll_last_ack_ts = 0.0
    c.az.pos_poll_inflight = True

    foreign = Telegram(
        src=20, dst=7, cmd="ACK_GETPOSDG", params="310,00", cs=0.0, ok=True
    )
    c._on_async_tel(foreign)
    assert c.az.pos_d10 == 3100
    assert c.az.pos_poll_last_ack_ts == 0.0  # eigenes RTT/Coalescing unberührt
    assert c.foreign_master_yield_active() is True


def test_setposdg_command_from_any_master_to_our_slave() -> None:
    """#7:20:SETPOSDG — Befehl an unsere Rotor-ID, SRC-Master beliegig."""
    c = RotorController(_hw_stub(), master_id=0, slave_az=20, slave_el=21, log=_Log())
    c.az.pos_d10 = 1000
    tel = Telegram(src=7, dst=20, cmd="SETPOSDG", params="180,00", cs=0.0, ok=True)
    assert c._tel_dst_allowed(tel) is True
    c._on_async_tel(tel)
    assert c.az.target_d10 == 1800
    assert c.az.moving is True
    assert c.az.foreign_follow_active is True
    assert c.foreign_master_yield_active() is True


def test_foreign_getposdg_ack_pauses_own_polling() -> None:
    """#20:7:ACK_GETPOSDG → Yield: kein eigenes Polling solange fremder Master aktiv."""
    c = RotorController(_hw_stub(), master_id=0, slave_az=20, slave_el=21, log=_Log())
    c.az.referenced = True
    c.az._last_sample_ts = 1.0
    c.az.pos_d10 = 270
    tel = parse("#20:7:ACK_GETPOSDG:27,58:54,58$")
    assert tel is not None
    c._on_async_tel(tel)
    assert c.az.pos_d10 == 275  # 27,58° → 275 (0,1°-Einheiten, Truncate)
    assert c.foreign_master_yield_active() is True


def test_foreign_follow_clears_when_at_target() -> None:
    c = RotorController(_hw_stub(), master_id=0, slave_az=20, slave_el=21, log=_Log())
    c.az.foreign_follow_active = True
    c.az.foreign_follow_last_rx_ts = 1e12  # frisch
    c.az.target_d10 = 1000
    c.az.last_set_sent_target_d10 = 1000
    c.az.pos_d10 = 1000
    c.az.moving = False
    c.az.referenced = True
    c._tick_foreign_follow_clear(time.time())
    assert c.az.foreign_follow_active is False


def test_own_setpos_clears_foreign_follow() -> None:
    c = RotorController(_hw_stub(), master_id=0, slave_az=20, slave_el=21, log=_Log())
    c.az.foreign_follow_active = True
    c._foreign_poll_seen_until = time.time() + 10.0
    c.clear_foreign_master_follow(c.az)
    assert c.az.foreign_follow_active is False


def _acc_ack_params(direction: int = 1, start: int = 0, base: int = 100) -> str:
    vals = ";".join(str(base + i) for i in range(12))
    return f"{direction};{start};12;{vals}"


def test_foreign_ack_getaccbins_sniffed_when_stromring_and_follow() -> None:
    """Mitlauf + Stromring: #20:7:ACK_GETACCBINS → acc_bins für Heatmap übernehmen."""
    c = RotorController(_hw_stub(), master_id=0, slave_az=20, slave_el=21, log=_Log())
    c.set_compass_window_open(True)
    c.set_compass_strom_heatmap_active(True, False)
    c.az.foreign_follow_active = True
    c._acc_bins_inflight_az = False
    tel = Telegram(
        src=20,
        dst=7,
        cmd="ACK_GETACCBINS",
        params=_acc_ack_params(1, 0, 200),
        cs=0.0,
        ok=True,
    )
    assert c._tel_dst_allowed(tel) is True
    c._on_async_tel(tel)
    assert c.az.acc_bins_cw is not None
    assert c.az.acc_bins_cw[0] == 200
    assert c.az.acc_bins_cw[11] == 211
    assert c.foreign_master_yield_active() is True


def test_foreign_ack_getaccbins_ignored_without_stromring() -> None:
    """Ohne Stromring/Statistik: fremdes ACK_GETACCBINS nicht in acc_bins schreiben."""
    c = RotorController(_hw_stub(), master_id=0, slave_az=20, slave_el=21, log=_Log())
    c.set_compass_window_open(True)
    c.set_compass_strom_heatmap_active(False, False)
    c.az.foreign_follow_active = True
    tel = Telegram(
        src=20,
        dst=7,
        cmd="ACK_GETACCBINS",
        params=_acc_ack_params(1, 0, 50),
        cs=0.0,
        ok=True,
    )
    c._on_async_tel(tel)
    assert c.az.acc_bins_cw is None
