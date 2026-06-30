"""Tests für Tastenkürzel-/Jog-Hilfsfunktionen."""

from __future__ import annotations

import pytest

from rotortcpbridge.shortcut_actions import effective_antenna_target_deg, set_antenna_azimuth_deg


class _DummyAz:
    def __init__(self) -> None:
        self.antdp1 = 1
        self.target_d10 = 700
        self.pos_d10 = 400
        self.last_set_sent_target_d10 = 700
        self.compass_target_d10 = None
        self.moving = False
        self.last_set_sent_ts = 0.0


class _DummyCtrl:
    def __init__(self) -> None:
        self.enable_az = True
        self.az = _DummyAz()
        self.az_dipole_display_bearing: float | None = 350.0
        self._compass_manual_az_ts = 0.0

    def set_az_deg(self, deg: float, force: bool = True) -> None:
        d10 = int(round(float(deg) * 10.0))
        self.az.target_d10 = d10
        self.az.last_set_sent_target_d10 = d10


def test_effective_antenna_target_prefers_dipole_display_bearing() -> None:
    cfg = {"ui": {"compass_antenna": 0, "antenna_dipoles_az": [True, False, False]}}
    ctrl = _DummyCtrl()
    assert effective_antenna_target_deg(cfg, ctrl) == pytest.approx(350.0)


def test_set_antenna_azimuth_deg_stores_dipole_display_bearing() -> None:
    cfg = {
        "ui": {
            "compass_antenna": 0,
            "antenna_dipoles_az": [True, False, False],
            "antenna_offsets_az": [90.0, 0.0, 0.0],
        }
    }
    ctrl = _DummyCtrl()
    set_antenna_azimuth_deg(cfg, ctrl, 10.0)
    assert ctrl.az_dipole_display_bearing == pytest.approx(10.0)
