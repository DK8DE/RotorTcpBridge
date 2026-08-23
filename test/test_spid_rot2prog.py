"""Tests für SPID/ROT2PROG-Paketdekodierung."""
from __future__ import annotations

from rotortcpbridge.spid_rot2prog import CMD_SET, parse_command_packet


def _pkt(h: str, ph: int, v: str, pv: int, cmd: int = CMD_SET) -> bytes:
    assert len(h) == 4 and len(v) == 4
    return bytes([0x57]) + h.encode("ascii") + bytes([ph]) + v.encode("ascii") + bytes([pv, cmd, 0x20])


def test_decode_ph10_tenth_degree() -> None:
    # 10° → H = 10*(10+360) = 3700
    cmd = parse_command_packet(_pkt("3700", 10, "3600", 10))
    assert cmd is not None
    assert cmd.az_d10 == 100
    # 370° Overlap
    cmd = parse_command_packet(_pkt("7300", 10, "3600", 10))
    assert cmd is not None
    assert cmd.az_d10 == 3700


def test_decode_ph0_pst_720_branches() -> None:
    """PstRotator (Autor): PH=0, ASCII-H; 720-Zweige az+360 / az+720 / tmp=az."""
    # tmp = az+360.5 → 10°
    cmd = parse_command_packet(_pkt("3700", 0, "3600", 0))
    assert cmd is not None
    assert cmd.az_d10 == 100
    # tmp = az+720.5 → Overlap 370°
    cmd = parse_command_packet(_pkt("7300", 0, "3600", 0))
    assert cmd is not None
    assert cmd.az_d10 == 3700
    # tmp = az (ohne +360) → 350° (nicht −10°)
    cmd = parse_command_packet(_pkt("3500", 0, "3600", 0))
    assert cmd is not None
    assert cmd.az_d10 == 3500
    # 0°
    cmd = parse_command_packet(_pkt("3600", 0, "3600", 0))
    assert cmd is not None
    assert cmd.az_d10 == 0


def test_decode_ph2_half_degree() -> None:
    # 120° → H = 2*(120+360) = 960
    cmd = parse_command_packet(_pkt("0960", 2, "0720", 2))
    assert cmd is not None
    assert cmd.az_d10 == 1200
    # 240° → H = 2*(240+360) = 1200
    cmd = parse_command_packet(_pkt("1200", 2, "0720", 2))
    assert cmd is not None
    assert cmd.az_d10 == 2400


def test_decode_ph1_heuristic() -> None:
    # 1°-Modus: H = 10+360 = 370
    cmd = parse_command_packet(_pkt("0370", 1, "0360", 1))
    assert cmd is not None
    assert cmd.az_d10 == 100
