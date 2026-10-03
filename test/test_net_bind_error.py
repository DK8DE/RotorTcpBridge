"""Tests für einheitliche Bind-Fehlermeldungen."""
from __future__ import annotations

from rotortcpbridge.net_bind_error import format_bind_error


class _E(Exception):
    def __init__(self, errno: int, msg: str = "x") -> None:
        super().__init__(msg)
        self.errno = errno


def test_eaddrinuse_windows() -> None:
    msg = format_bind_error("127.0.0.1", 4003, _E(10048), proto_name="GS-232B")
    assert "GS-232B" in msg
    assert "4003" in msg
    assert "belegt" in msg


def test_eaddrinuse_n1mm_hint() -> None:
    msg = format_bind_error("127.0.0.1", 12040, _E(10048), proto_name="N1MM")
    assert "12040" in msg
    assert "UcxLog" in msg


def test_eacces() -> None:
    msg = format_bind_error("0.0.0.0", 80, _E(10013), proto_name="Web")
    assert "Berechtigung" in msg or "80" in msg


def test_generic() -> None:
    msg = format_bind_error("1.2.3.4", 9, _E(22, "boom"), proto_name="X")
    assert "1.2.3.4" in msg
    assert "boom" in msg
