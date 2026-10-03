"""Web-SCAN: nächstes Bein erst nach Erreichen des Ist-Ziels."""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import MagicMock

from rotortcpbridge.ui import web_compass_state as wcs


def _mw(*, pos_deg: float, target_deg: float, moving: bool = False):
    az = SimpleNamespace(
        pos_d10=int(round(pos_deg * 10)),
        target_d10=int(round(target_deg * 10)),
        moving=moving,
        position_wrap_360=True,
    )
    ctrl = SimpleNamespace(az=az, set_az_deg=MagicMock())
    mw = SimpleNamespace(
        ctrl=ctrl,
        cfg={"ui": {}},
        _web_scan_active=True,
        _web_scan_a=0.0,
        _web_scan_b=90.0,
        _web_scan_next_is_b=True,
        _web_scan_cmd_ts=time.time() - 1.0,
        _web_scan_saw_moving=True,
        _web_scan_rotor_tgt=target_deg,
        _get_antenna_offset_az=lambda: 0.0,
        _antenna_dipole_enabled=lambda _i: False,
    )
    return mw


def test_web_scan_waits_until_near_target(monkeypatch) -> None:
    mw = _mw(pos_deg=20.0, target_deg=90.0, moving=False)
    called = []

    def _goto(m, deg):
        called.append(deg)

    monkeypatch.setattr(wcs, "web_scan_goto", _goto)
    wcs.web_tick_scan(mw)
    assert called == []


def test_web_scan_switches_when_arrived(monkeypatch) -> None:
    # Am Bein A (0°) angekommen → nächstes Ziel B (90°)
    mw = _mw(pos_deg=0.2, target_deg=0.0, moving=False)
    called = []
    monkeypatch.setattr(wcs, "web_scan_goto", lambda m, d: called.append(d))
    wcs.web_tick_scan(mw)
    assert called == [90.0]
    assert mw._web_scan_next_is_b is False


def test_web_scan_no_switch_while_moving(monkeypatch) -> None:
    mw = _mw(pos_deg=89.7, target_deg=90.0, moving=True)
    called = []
    monkeypatch.setattr(wcs, "web_scan_goto", lambda m, d: called.append(d))
    wcs.web_tick_scan(mw)
    assert called == []
