"""Rotor-/Antennen-Aktionen für globale Tastenkürzel (ohne Plattform-Code)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .angle_utils import clamp_el, wrap_deg

if TYPE_CHECKING:
    from .rotor_controller import RotorController


def antenna_offset_for_compass_slot(cfg: dict) -> float:
    """Versatz der gewählten Antenne (Kompass-Slot) in Grad."""
    ui = cfg.get("ui") or {}
    slot = max(0, min(2, int(ui.get("compass_antenna", 0))))
    offs = ui.get("antenna_offsets_az", [0.0, 0.0, 0.0])
    try:
        return float(offs[slot]) if slot < len(offs) else 0.0
    except (TypeError, ValueError):
        return 0.0


def set_antenna_azimuth_deg(cfg: dict, ctrl: "RotorController", antenna_deg: float) -> None:
    """Antennen-Richtung (wie Kompass-Anzeige) fahren: Rotor = Antenne − Versatz."""
    if not getattr(ctrl, "enable_az", True):
        return
    off = antenna_offset_for_compass_slot(cfg)
    rotor = wrap_deg(float(antenna_deg) - off)
    ctrl.set_az_deg(rotor, force=True)


def effective_antenna_target_deg(cfg: dict, ctrl: "RotorController") -> float:
    """Aktuelles AZ-Soll als Antennenpeilung (°), aus Motor-/SETPOSCC-Soll + Versatz."""
    az = ctrl.az
    try:
        cc = getattr(az, "compass_target_d10", None)
        td10 = int(cc) if cc is not None else int(getattr(az, "target_d10", 0))
    except Exception:
        td10 = 0
    rotor_tgt = td10 / 10.0
    off = antenna_offset_for_compass_slot(cfg)
    return wrap_deg(rotor_tgt + off)


def bump_antenna_target_deg(cfg: dict, ctrl: "RotorController", delta_deg: float) -> None:
    """Antennen-Ziel um delta Grad drehen (0…360°)."""
    cur = effective_antenna_target_deg(cfg, ctrl)
    new_ant = wrap_deg(cur + float(delta_deg))
    set_antenna_azimuth_deg(cfg, ctrl, new_ant)


def effective_el_target_deg(ctrl: "RotorController") -> float:
    """Aktuelles EL-Soll in Grad (0…90°), aus Motor-/Kompass-Soll."""
    if not getattr(ctrl, "enable_el", False):
        return 0.0
    el = ctrl.el
    try:
        cc = getattr(el, "compass_target_d10", None)
        td10 = int(cc) if cc is not None else int(getattr(el, "target_d10", 0))
    except Exception:
        td10 = 0
    return clamp_el(td10 / 10.0)


def bump_el_target_deg(ctrl: "RotorController", delta_deg: float) -> None:
    """EL-Ziel um delta Grad (0…90°)."""
    if not getattr(ctrl, "enable_el", False):
        return
    cur = effective_el_target_deg(ctrl)
    new_el = clamp_el(cur + float(delta_deg))
    ctrl.set_el_deg(new_el, force=True)
