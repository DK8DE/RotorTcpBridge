"""Kompassfenster: Bereich „Profile“ mit Dropdown (links), gesperrt bei Fahrt."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from typing import cast

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

import rotortcpbridge.ui  # noqa: F401  (Zirkelimport vermeiden)
from rotortcpbridge.app_config import migrate_and_merge_config
from rotortcpbridge.compass.compass_window import CompassWindow
from rotortcpbridge.hardware_client import HardwareClient
from rotortcpbridge.rotor_controller import RotorController


class _Log:
    def write(self, *args, **kwargs) -> None:
        pass


class _Hw:
    pass


def test_compass_has_profile_card_and_locks_while_moving(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(
        "rotortcpbridge.profile_store.list_profiles",
        lambda: [{"id": "default", "name": "Default"}, {"id": "p2", "name": "Rotor 2"}],
    )
    monkeypatch.setattr("rotortcpbridge.profile_store.get_active_profile_id", lambda: "p2")
    cfg = migrate_and_merge_config({})
    ctrl = RotorController(
        cast(HardwareClient, _Hw()), master_id=0, slave_az=20, slave_el=21, log=_Log()
    )
    switched: list[str] = []
    win = CompassWindow(cfg, ctrl, lambda c: None, switch_profile_cb=switched.append)
    try:
        assert win.cb_profile.count() == 2
        assert win.cb_profile.currentData() == "p2"
        assert win.cb_profile.isEnabled()

        win.cb_profile.setCurrentIndex(0)  # Wechsel auf Default
        assert switched == ["default"]

        ctrl.az.moving = True
        win._refresh_profile_dropdown()
        assert not win.cb_profile.isEnabled()
        win.cb_profile.setCurrentIndex(1)
        assert switched == ["default"]  # gesperrt: kein weiterer Wechsel
    finally:
        win.close()
        app.processEvents()
