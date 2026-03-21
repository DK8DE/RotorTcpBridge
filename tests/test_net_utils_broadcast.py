"""Tests für ipv4_subnet_broadcast_default (Format / gültige IPv4)."""
from __future__ import annotations

import socket

import pytest

from rotortcpbridge.net_utils import ipv4_subnet_broadcast_default


def test_ipv4_subnet_broadcast_default_is_valid_ipv4() -> None:
    ip = ipv4_subnet_broadcast_default()
    parts = ip.split(".")
    assert len(parts) == 4
    assert all(p.isdigit() and 0 <= int(p) <= 255 for p in parts)
    socket.inet_pton(socket.AF_INET, ip)


def test_ipv4_subnet_broadcast_default_last_octet_255_or_localhost() -> None:
    ip = ipv4_subnet_broadcast_default()
    # 127.0.0.1 = kein routbares Netz / nur Loopback
    # sonst typisch …255 für /24
    assert ip == "127.0.0.1" or ip.split(".")[-1] == "255"
