"""Tests N1MM Rotor UDP Parser / Broadcast-Format."""
from __future__ import annotations

from rotortcpbridge.n1mm_rotor_udp import format_n1mm_broadcast, parse_n1mm_rotor_xml


def test_parse_goazi() -> None:
    xml = b"""<?xml version="1.0" encoding="utf-8"?>
<N1MMRotor>
  <rotor>0</rotor>
  <goazi>123.4</goazi>
  <stop>False</stop>
</N1MMRotor>
"""
    goazi, stop, ok = parse_n1mm_rotor_xml(xml)
    assert ok is True
    assert stop is False
    assert goazi == 123.4


def test_parse_stop() -> None:
    xml = b"""<N1MMRotor><stop>True</stop></N1MMRotor>"""
    goazi, stop, ok = parse_n1mm_rotor_xml(xml)
    assert ok is True
    assert stop is True
    assert goazi is None


def test_rotor_name_filter_rejects() -> None:
    xml = b"""<N1MMRotor><rotor>Other</rotor><goazi>10</goazi></N1MMRotor>"""
    _, _, ok = parse_n1mm_rotor_xml(xml, rotor_name="Mine")
    assert ok is False


def test_rotor_name_filter_accepts() -> None:
    xml = b"""<N1MMRotor><rotor>Mine</rotor><goazi>10</goazi></N1MMRotor>"""
    goazi, _, ok = parse_n1mm_rotor_xml(xml, rotor_name="Mine")
    assert ok is True
    assert goazi == 10.0


def test_empty_rotor_name_accepts_all() -> None:
    xml = b"""<N1MMRotor><rotor>X</rotor><goazi>5</goazi></N1MMRotor>"""
    goazi, _, ok = parse_n1mm_rotor_xml(xml, rotor_name="")
    assert ok is True
    assert goazi == 5.0


def test_broadcast_format() -> None:
    assert format_n1mm_broadcast("ROTOR", 123.4) == b"ROTOR @ 1234"
    assert format_n1mm_broadcast("", 10.0) == b"ROTOR @ 100"
