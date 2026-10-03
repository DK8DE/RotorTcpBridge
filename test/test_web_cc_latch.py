"""Web-Sollzeiger: SETPOSCC-Latch gegen Flackern mit altem target_d10."""

from __future__ import annotations

import time
from types import SimpleNamespace

from rotortcpbridge.ui.web_compass_state import web_effective_az_target_d10


def _mw(*, cc=None, target=900, moving=False, hold=False):
    az = SimpleNamespace(
        compass_target_d10=cc,
        target_d10=target,
        moving=moving,
    )
    ctrl = SimpleNamespace(
        az=az,
        _setposcc_poll_hold=hold,
        _setposcc_hold_until=time.time() + 5.0 if hold else 0.0,
    )

    def cc_poll_hold_active(now: float) -> bool:
        if not ctrl._setposcc_poll_hold:
            return False
        return now < float(ctrl._setposcc_hold_until or 0.0)

    ctrl.cc_poll_hold_active = cc_poll_hold_active  # type: ignore[attr-defined]
    return SimpleNamespace(ctrl=ctrl, _web_cc_latch_az_d10=None, _web_cc_latch_az_ts=0.0)


def test_web_cc_uses_compass_target() -> None:
    mw = _mw(cc=1800, target=900)
    assert web_effective_az_target_d10(mw) == 1800
    assert mw._web_cc_latch_az_d10 == 1800


def test_web_cc_latch_survives_cleared_compass_target() -> None:
    mw = _mw(cc=1800, target=900)
    assert web_effective_az_target_d10(mw) == 1800
    mw.ctrl.az.compass_target_d10 = None  # SPID/Echo löscht CC kurz
    # Latch + recent → weiter neues CC-Soll, nicht altes target_d10
    assert web_effective_az_target_d10(mw) == 1800


def test_web_cc_latch_uses_hold_when_not_recent_enough() -> None:
    mw = _mw(cc=1800, target=900, hold=True)
    assert web_effective_az_target_d10(mw) == 1800
    mw.ctrl.az.compass_target_d10 = None
    mw._web_cc_latch_az_ts = time.time() - 10.0  # Latch „alt“, aber Hold aktiv
    assert web_effective_az_target_d10(mw) == 1800


def test_web_cc_falls_back_when_caught_up() -> None:
    mw = _mw(cc=1800, target=1800)
    assert web_effective_az_target_d10(mw) == 1800
    mw.ctrl.az.compass_target_d10 = None
    mw._web_cc_latch_az_ts = time.time() - 10.0
    # Motorziel = Latch, kein Hold → Fallback target
    assert web_effective_az_target_d10(mw) == 1800
    assert mw._web_cc_latch_az_d10 is None
