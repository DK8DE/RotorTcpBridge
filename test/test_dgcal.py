"""Tests für SETDGCAL / GETDGCAL Korrekturwinkel."""

from __future__ import annotations

import pytest

from rotortcpbridge.angle_utils import compute_dgcal_deg
from rotortcpbridge.command_catalog import command_specs
from rotortcpbridge.rotor_backup import backupable_pairs


def test_compute_dgcal_first_correction() -> None:
    assert compute_dgcal_deg(0.0, 60.0, 61.4) == pytest.approx(1.4)


def test_compute_dgcal_adds_existing() -> None:
    # Ist zeigt bereits korrigiert 61.4; aktuelle Kalibrierung 1.4; neue Peilung 62.0
    assert compute_dgcal_deg(1.4, 61.4, 62.0) == pytest.approx(2.0)


def test_compute_dgcal_clamps() -> None:
    assert compute_dgcal_deg(350.0, 0.0, 20.0) == pytest.approx(360.0)
    assert compute_dgcal_deg(-350.0, 20.0, 0.0) == pytest.approx(-360.0)


def test_dgcal_in_catalog_and_backup_pairs() -> None:
    names = {s.name for s in command_specs()}
    assert "SETDGCAL" in names
    assert "GETDGCAL" in names
    pairs = backupable_pairs()
    assert ("SETDGCAL", "GETDGCAL") in pairs
