"""Gleiche FLRig/Hamlib-Modus-Aliase wie im FT-991-Audiomanager (rig_bridge/cat_commands)."""

from __future__ import annotations

from rotortcpbridge.rig_bridge.cat_commands import (
    _normalize_hamlib_mode_name,
    _yaesu_newcat_mode_char,
)


def test_usb_d1_is_data_usb_md_code() -> None:
    assert _normalize_hamlib_mode_name("USB-D1") == "PKTUSB"
    assert _yaesu_newcat_mode_char("USB-D1") == "C"


def test_lsb_d1_is_data_lsb_md_code() -> None:
    assert _normalize_hamlib_mode_name("LSB-D1") == "PKTLSB"
    assert _yaesu_newcat_mode_char("LSB-D1") == "8"


def test_flrig_style_tokens() -> None:
    assert _normalize_hamlib_mode_name("PKT-U") == "PKTUSB"
    assert _normalize_hamlib_mode_name("RTTY-L") == "RTTY"
    assert _normalize_hamlib_mode_name("CWU") == "CW"
    assert _normalize_hamlib_mode_name("NFM") == "FMN"
