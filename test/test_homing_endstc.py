"""Homing-UI: ohne Endanschläge (GETENDSTC=0) keine Homing-Taste."""

from __future__ import annotations

from types import SimpleNamespace

from rotortcpbridge.rotor_model import AxisState


def _ctrl_stub():
    from rotortcpbridge.rotor_controller import RotorController

    # Nur die Hilfsmethoden testen — ohne HW: Instanz-Attrs manuell setzen
    class _T:
        abs_encoder_no_homing = RotorController.abs_encoder_no_homing
        _axis_has_endstops = RotorController._axis_has_endstops
        homing_button_hidden_for_axis = RotorController.homing_button_hidden_for_axis

    c = _T()
    c.encoder_type = None
    c.encoder_type_known = False
    c.az = AxisState()
    c.el = AxisState()
    return c


def test_homing_visible_until_endstc_known():
    c = _ctrl_stub()
    assert c.homing_button_hidden_for_axis("az") is False
    assert c.homing_button_hidden_for_axis("el") is False


def test_homing_hidden_when_endstc_zero():
    c = _ctrl_stub()
    c.az.endstc = 0
    c.az.endstc_known = True
    c.el.endstc = 1
    c.el.endstc_known = True
    assert c.homing_button_hidden_for_axis("az") is True
    assert c.homing_button_hidden_for_axis("el") is False


def test_homing_hidden_for_abs_encoder():
    c = _ctrl_stub()
    c.encoder_type = 3
    c.encoder_type_known = True
    c.az.endstc = 2
    c.az.endstc_known = True
    assert c.homing_button_hidden_for_axis("az") is True
    assert c.homing_button_hidden_for_axis("el") is True


def test_homing_visible_with_endstops():
    c = _ctrl_stub()
    c.encoder_type = 1
    c.encoder_type_known = True
    c.az.endstc = 1
    c.az.endstc_known = True
    assert c.homing_button_hidden_for_axis("az") is False
