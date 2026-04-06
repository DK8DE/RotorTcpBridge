"""align_az_bearing_after_antenna_switch nur bei controller_hw.antenna_realign_on_switch."""

from __future__ import annotations

from rotortcpbridge.rotor_controller import RotorController


class _Log:
    def write(self, *args, **kwargs) -> None:
        pass


class _Hw:
    def send_request(self, *args, **kwargs) -> None:
        pass


def test_align_snaps_soll_to_ist_when_flag_off() -> None:
    """Ohne Nachführen: Soll = Rotor-Ist (neue Antenne zeigt Soll = Ist)."""
    c = RotorController(_Hw(), master_id=1, slave_az=20, slave_el=21, log=_Log())
    c.az.antoff1 = 0.0
    c.az.antoff2 = 90.0
    c.az.pos_d10 = 900
    c.az.smooth_pos_d10f = 900.0
    c.az._smooth_from_ts = 0.0
    c.az._smooth_to_ts = 0.0
    c.az.target_d10 = 1234
    c.az.compass_target_d10 = 500
    cfg = {"controller_hw": {"antenna_realign_on_switch": False}}
    c.align_az_bearing_after_antenna_switch(0, 1, cfg)
    assert c.az.target_d10 == 900
    assert c.az.compass_target_d10 is None
    assert c.az.moving is False


def test_align_sets_target_when_flag_on() -> None:
    c = RotorController(_Hw(), master_id=1, slave_az=20, slave_el=21, log=_Log())
    c.az.antoff1 = 0.0
    c.az.antoff2 = 90.0
    c.az.pos_d10 = 900
    c.az.smooth_pos_d10f = 900.0
    c.az._smooth_to_ts = 0.0
    cfg = {
        "controller_hw": {"antenna_realign_on_switch": True},
        "ui": {"antenna_offsets_az": [0.0, 90.0, 0.0]},
    }
    c.align_az_bearing_after_antenna_switch(0, 1, cfg)
    # Rotor 90° + off_old 0 → D=90°; rotor_soll = 90 - 90 = 0°
    assert c.az.target_d10 == 0
