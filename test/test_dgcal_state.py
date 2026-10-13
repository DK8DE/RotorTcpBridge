"""Korrekturwinkel im Controller-State (Web-Ansicht zeigt 0.0° statt „–“)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast

from rotortcpbridge.hardware_client import HardwareClient
from rotortcpbridge.rotor_controller import RotorController


class _Log:
    def write(self, *a, **k) -> None:
        pass


def _ctrl(reply_params: str = "0") -> tuple[RotorController, list]:
    c = RotorController(
        cast(HardwareClient, SimpleNamespace()), master_id=0, slave_az=20, slave_el=21, log=_Log()
    )
    sent: list = []

    def fake_send(dst, cmd, params, *, expect_prefix, timeout_s, priority, on_done, apply_local_state):
        sent.append((dst, cmd, params))
        if cmd == "GETDGCAL":
            tel = SimpleNamespace(cmd="ACK_GETDGCAL", params=reply_params)
        else:
            tel = SimpleNamespace(cmd="ACK_SETDGCAL", params=params)
        on_done(tel, None)

    c.send_ui_command = fake_send  # type: ignore[method-assign]
    c.request_immediate_pos = lambda *a, **k: None  # type: ignore[method-assign]
    return c, sent


def test_request_dgcal_zero_is_known_value() -> None:
    c, sent = _ctrl("0")
    assert c.az.dgcal_d10 is None
    c.request_dgcal("az")
    assert c.az.dgcal_d10 == 0  # 0 ist ein gültiger Wert (nicht „unbekannt“)
    assert sent == [(20, "GETDGCAL", "0")]


def test_request_dgcal_throttled_and_comma() -> None:
    c, sent = _ctrl("1,4")
    c.request_dgcal("az")
    c.request_dgcal("az")  # gedrosselt
    assert c.az.dgcal_d10 == 14
    assert len(sent) == 1


def test_set_dgcal_sends_and_rereads() -> None:
    c, sent = _ctrl("2,5")
    c.set_dgcal("az", 2.5)
    assert sent[0] == (20, "SETDGCAL", "2,5")
    assert sent[1][1] == "GETDGCAL"
    assert c.az.dgcal_d10 == 25


def test_el_disabled_does_nothing() -> None:
    c, sent = _ctrl()
    c.enable_el = False
    c.request_dgcal("el")
    assert sent == []
