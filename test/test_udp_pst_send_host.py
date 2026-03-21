"""Tests für Ziel-IP-Auswahl bei UDP PST-Rotator."""
from __future__ import annotations

from rotortcpbridge.udp_pst_rotator import _parse_ipv4_send_host


class _Log:
    def __init__(self) -> None:
        self.warns: list[str] = []

    def write(self, level: str, msg: str) -> None:
        if level == "WARN":
            self.warns.append(msg)


def test_parse_ipv4_valid_unicast() -> None:
    log = _Log()
    assert _parse_ipv4_send_host("192.168.1.10", log) == "192.168.1.10"
    assert not log.warns


def test_parse_ipv4_broadcast() -> None:
    log = _Log()
    assert _parse_ipv4_send_host("255.255.255.255", log) == "255.255.255.255"
    assert not log.warns


def test_parse_ipv4_localhost() -> None:
    log = _Log()
    assert _parse_ipv4_send_host("127.0.0.1", log) == "127.0.0.1"
    assert not log.warns


def test_parse_ipv4_empty_defaults() -> None:
    log = _Log()
    assert _parse_ipv4_send_host("", log) == "127.0.0.1"
    assert _parse_ipv4_send_host(None, log) == "127.0.0.1"


def test_parse_ipv4_invalid_falls_back() -> None:
    log = _Log()
    assert _parse_ipv4_send_host("not-an-ip", log) == "127.0.0.1"
    assert len(log.warns) == 1


def test_parse_ipv4_strips_whitespace() -> None:
    log = _Log()
    assert _parse_ipv4_send_host("  10.0.0.1  ", log) == "10.0.0.1"
