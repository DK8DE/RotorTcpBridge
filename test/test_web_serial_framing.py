"""Checksum/Framing-Spiegel für Web-Serial-JS (wie rs485_protocol)."""

from __future__ import annotations

from rotortcpbridge.rs485_protocol import build, calc_checksum, parse
from rotortcpbridge.ui.web_serial_js import web_serial_js


def test_web_serial_js_contains_core_api():
    js = web_serial_js()
    assert "navigator.serial" in js
    assert "GETLINKMODE" in js
    assert "SETLINKMODE" in js
    assert "/api/bus" in js
    assert "relayTick" in js
    assert "canCommand" in js
    assert "txQueue" in js
    # Kein lokales Rotor-Polling mehr über USB (Display ist im WEB-Modus Remote-USB)
    assert "GETPOSDG" not in js
    assert "baudRate: 115200" in js or "BAUD = 115200" in js


def test_checksum_matches_known_frame():
    # SRC=1 DST=2 CMD=GETLINKMODE PARAMS=0 → CS = 1+2+0 = 3
    frame = build(1, 2, "GETLINKMODE", "0")
    assert frame.startswith("#1:2:GETLINKMODE:0:")
    assert frame.endswith("$")
    tel = parse(frame)
    assert tel is not None
    assert tel.ok
    assert tel.cmd == "GETLINKMODE"
    assert abs(calc_checksum(1, 2, "0") - 3.0) < 1e-6


def test_setposdg_checksum_with_comma():
    frame = build(1, 20, "SETPOSDG", "123,5")
    tel = parse(frame)
    assert tel is not None and tel.ok
    assert abs(tel.cs - (1 + 20 + 123.5)) < 0.02
