"""GETMOVE: Firmware-Bewegungsflag steuert Fährt-LED."""

from __future__ import annotations

from typing import cast

from rotortcpbridge.command_catalog import command_specs
from rotortcpbridge.hardware_client import HardwareClient
from rotortcpbridge.rs485_protocol import Telegram
from rotortcpbridge.rotor_controller import RotorController


class _Log:
    def write(self, *args, **kwargs) -> None:
        pass


class _Hw:
    def is_connected(self) -> bool:
        return True


def _ctrl() -> RotorController:
    return RotorController(
        cast(HardwareClient, _Hw()),
        master_id=0,
        slave_az=20,
        slave_el=21,
        log=_Log(),
    )


def test_catalog_has_getmove() -> None:
    names = {s.name for s in command_specs()}
    assert "GETMOVE" in names


def test_ack_getmove_sets_moving_and_online() -> None:
    c = _ctrl()
    assert c.az.getmove_supported is None
    c._on_async_tel(Telegram(src=20, dst=0, cmd="ACK_GETMOVE", params="1", cs=0.0, ok=True))
    assert c.az.getmove_supported is True
    assert c.az.moving is True
    assert c.az.online is True
    c._on_async_tel(Telegram(src=20, dst=0, cmd="ACK_GETMOVE", params="0", cs=0.0, ok=True))
    assert c.az.moving is False


def test_nak_getmove_disables_further_polls() -> None:
    c = _ctrl()
    c._on_async_tel(
        Telegram(src=20, dst=0, cmd="NAK_GETMOVE", params="NOSUP", cs=0.0, ok=True)
    )
    assert c.az.getmove_supported is False
    assert c.az.online is True


def test_getposdg_heuristic_skipped_when_getmove_supported() -> None:
    c = _ctrl()
    c.az.getmove_supported = True
    c.az.moving = False
    c.az.referenced = True
    c.az.pos_d10 = 100
    c.az._last_sample_ts = 1.0
    c.az.last_set_sent_target_d10 = 500
    c.az.target_d10 = 500
    # Großer Sprung würde ohne GETMOVE moving=True setzen
    c._on_async_tel(
        Telegram(src=20, dst=0, cmd="ACK_GETPOSDG", params="50,0", cs=0.0, ok=True)
    )
    assert c.az.moving is False
    assert c.az.pos_d10 == 500  # 50,0° → 500 d10
