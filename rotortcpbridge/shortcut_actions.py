"""Rotor-/Antennen-Aktionen für globale Tastenkürzel (ohne Plattform-Code)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .angle_utils import (
    antenna_bearing_from_rotor_and_offset,
    antenna_dipole_enabled,
    az_max_d10_from_axis,
    az_pos_deg_from_d10,
    clamp_el,
    raw_rotor_az_deg_from_axis,
    rotor_az_for_display_bearing,
    wrap_deg,
)

if TYPE_CHECKING:
    from .rotor_controller import RotorController


def antenna_offset_for_compass_slot(cfg: dict, ctrl: "RotorController | None" = None) -> float:
    """Versatz der gewählten Antenne (Kompass-Slot) in Grad — Hardware vor Config."""
    ui = cfg.get("ui") or {}
    slot = max(0, min(2, int(ui.get("compass_antenna", 0))))
    if ctrl is not None:
        az_axis = getattr(ctrl, "az", None)
        hw_v = getattr(az_axis, f"antoff{slot + 1}", None) if az_axis else None
        if hw_v is not None:
            return float(hw_v)
    offs = ui.get("antenna_offsets_az", [0.0, 0.0, 0.0])
    try:
        return float(offs[slot]) if slot < len(offs) else 0.0
    except (TypeError, ValueError):
        return 0.0


def antenna_dipole_for_compass_slot(cfg: dict, ctrl: "RotorController") -> bool:
    """Dipol-Flag der gewählten Antenne: Controller-Zustand bevorzugt, sonst Config."""
    ui = cfg.get("ui") or {}
    slot = max(0, min(2, int(ui.get("compass_antenna", 0))))
    return antenna_dipole_enabled(getattr(ctrl, "az", None), cfg, slot)


def set_antenna_azimuth_deg(cfg: dict, ctrl: "RotorController", antenna_deg: float) -> None:
    """Antennen-Richtung (wie Kompass-Anzeige) fahren; Dipol: kürzester Rotor-Drehweg."""
    if not getattr(ctrl, "enable_az", True):
        return
    bearing = wrap_deg(float(antenna_deg))
    off = antenna_offset_for_compass_slot(cfg, ctrl)
    dipole = antenna_dipole_for_compass_slot(cfg, ctrl)
    max_deg = float(az_max_d10_from_axis(getattr(ctrl, "az", None))) / 10.0
    rotor = rotor_az_for_display_bearing(
        bearing,
        off,
        raw_rotor_az_deg_from_axis(getattr(ctrl, "az", None)),
        dipole=dipole,
        last_rotor_az=getattr(ctrl, "az_dipole_last_rotor_az", None) if dipole else None,
        max_deg=max_deg,
    )
    ctrl.set_az_deg(rotor, force=True)
    if dipole:
        ctrl.az_dipole_display_bearing = bearing
        ctrl.az_dipole_last_rotor_az = rotor
    else:
        ctrl.az_dipole_display_bearing = None
        ctrl.az_dipole_last_rotor_az = None


def _az_rotor_deg_for_relative_steps(ctrl: "RotorController") -> float:
    """Rotor-Istwinkel (°) als Bezug für Jog/Hotkey-Schritte.

    Direkt nach Programmstart ist ``target_d10`` oft noch 0, während ``pos_d10`` schon
    von GETPOSDG kommt — dann soll Jog von der aktuellen Peilung aus zählen, nicht von 0°.
    Sobald ein Motor-Soll gesendet wurde (``last_set_sent_target_d10``), gilt weiter das Soll.
    """
    mx = az_max_d10_from_axis(getattr(ctrl, "az", None))
    try:
        if getattr(ctrl.az, "last_set_sent_target_d10", None) is not None:
            return az_pos_deg_from_d10(int(getattr(ctrl.az, "target_d10", 0)), max_d10=mx)
    except Exception:
        pass
    try:
        raw = raw_rotor_az_deg_from_axis(getattr(ctrl, "az", None))
        if raw is not None:
            return raw
        return az_pos_deg_from_d10(int(getattr(ctrl.az, "pos_d10", 0)), max_d10=mx)
    except Exception:
        return 0.0


def effective_antenna_target_deg(cfg: dict, ctrl: "RotorController") -> float:
    """Aktuelle AZ-Bezugspeilung (Antenne, °) für relative Schritte: Rotor + Versatz.

    Nur Rotor-Ist bzw. Motor-``target_d10`` (kein ``compass_target_d10`` / SETPOSCC), damit
    schnelle Hotkey-Ketten nicht am Encoder-Schnipsel hängen.
    Bei Dipol: gespeicherte Anzeige-Peilung (Gegenkeule ≠ Rotor+Versatz).
    """
    if antenna_dipole_for_compass_slot(cfg, ctrl):
        ext = getattr(ctrl, "az_dipole_display_bearing", None)
        if ext is not None:
            return wrap_deg(float(ext))
    rotor_tgt = _az_rotor_deg_for_relative_steps(ctrl)
    off = antenna_offset_for_compass_slot(cfg, ctrl)
    mx = az_max_d10_from_axis(getattr(ctrl, "az", None))
    return antenna_bearing_from_rotor_and_offset(rotor_tgt, off, max_d10=mx)


def bump_antenna_target_deg(cfg: dict, ctrl: "RotorController", delta_deg: float) -> None:
    """Antennen-Ziel um delta Grad drehen (0…360°)."""
    cur = effective_antenna_target_deg(cfg, ctrl)
    new_ant = wrap_deg(cur + float(delta_deg))
    set_antenna_azimuth_deg(cfg, ctrl, new_ant)


def _el_deg_for_relative_steps(ctrl: "RotorController") -> float:
    """EL-Bezug (°) für Jog: Motor-Soll wenn schon gesendet, sonst Ist-Position."""
    if not getattr(ctrl, "enable_el", False):
        return 0.0
    try:
        if getattr(ctrl.el, "last_set_sent_target_d10", None) is not None:
            return clamp_el(int(getattr(ctrl.el, "target_d10", 0)) / 10.0)
    except Exception:
        pass
    try:
        return clamp_el(int(getattr(ctrl.el, "pos_d10", 0)) / 10.0)
    except Exception:
        return 0.0


def effective_el_target_deg(ctrl: "RotorController") -> float:
    """Aktuelles EL in Grad (0…90°) als Bezug für relative Schritte, analog AZ."""
    return _el_deg_for_relative_steps(ctrl)


def bump_el_target_deg(ctrl: "RotorController", delta_deg: float) -> None:
    """EL-Ziel um delta Grad (0…90°)."""
    if not getattr(ctrl, "enable_el", False):
        return
    cur = effective_el_target_deg(ctrl)
    new_el = clamp_el(cur + float(delta_deg))
    ctrl.set_el_deg(new_el, force=True)
