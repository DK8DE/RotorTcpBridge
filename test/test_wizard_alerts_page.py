"""Warnschwellen-Seite im Inbetriebnahme-Assistenten: Temperatur immer, Wind nur mit Sensor."""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication  # noqa: E402

from rotortcpbridge.ui import rotor_setup_wizard as rsw  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _page(enc_type, wind_choice):
    saved = {}
    wiz = SimpleNamespace(
        cfg={"ui": {}},
        ctrl=None,
        enc_type=enc_type,
        wind_choice=wind_choice,
        save_cfg_cb=lambda c: saved.update(c),
    )
    page = rsw.AlertsPage(wiz)
    page.initializePage()
    return page, wiz


@pytest.mark.parametrize("enc_type,wind_choice", [(1, False), (1, None), (3, None), (1, True)])
def test_temperature_always_settable(qapp, enc_type, wind_choice):
    page, wiz = _page(enc_type, wind_choice)
    tab = page._tab
    assert tab.isEnabled()
    assert tab.chk_temp.isEnabled() and tab.sp_temp_min.isEnabled() and tab.sp_temp_max.isEnabled()
    tab.chk_temp.setChecked(True)
    tab.sp_temp_max.setValue(33.0)
    assert page.validatePage()
    wa = wiz.cfg["ui"]["weather_alert_thresholds"]
    assert wa["temp_alert_enabled"] is True
    assert wa["temp_max_c"] == 33.0
    assert not hasattr(page, "chk_skip")


def test_wind_max_not_settable_without_sensor(qapp):
    page, wiz = _page(1, False)
    tab = page._tab
    assert not tab.sp_wind_max.isEnabled()
    assert not tab._gb_wind.isEnabled()
    tab.chk_wind.setChecked(True)
    assert page.validatePage()
    assert wiz.cfg["ui"]["weather_alert_thresholds"]["wind_alert_enabled"] is False


def test_wind_max_settable_with_sensor(qapp):
    page, _wiz = _page(1, True)
    assert page._tab._gb_wind.isEnabled()
    assert page._tab.sp_wind_max.isEnabled()
