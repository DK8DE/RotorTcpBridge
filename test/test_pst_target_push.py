"""Tests für den ausgehenden PST-Ziel-Push (Soll-Zeiger an PstRotator)."""
from __future__ import annotations

from rotortcpbridge.pst_target_push import PstTargetPush, target_changed


class _Log:
    def __init__(self) -> None:
        self.msgs: list[tuple[str, str]] = []

    def write(self, level: str, msg: str) -> None:
        self.msgs.append((level, msg))


def _make_push() -> tuple[PstTargetPush, list[str]]:
    """Push im aktiven Zustand, aber ohne echten Netzverkehr (._send abgefangen)."""
    push = PstTargetPush(controller=None, log=_Log())
    push.start(enabled=True, host="127.0.0.1", port=12000)
    sent: list[str] = []
    push._send = lambda msg: sent.append(msg)  # type: ignore[method-assign]
    return push, sent


def test_target_changed_basics() -> None:
    assert target_changed(None, None) is False
    assert target_changed(1730, None) is True  # noch nie gesendet
    assert target_changed(1730, 1730) is False  # unverändert
    assert target_changed(1731, 1730) is True  # 0,1° Änderung
    assert target_changed(1730, 1725, min_delta_d10=10) is False  # unter Schwelle


def test_first_target_is_sent_az_and_el() -> None:
    push, sent = _make_push()
    push.notify_target(1730, 250, now=100.0)
    assert sent == ["<PST><AZIMUTH>173.0</AZIMUTH></PST>", "<PST><ELEVATION>25.0</ELEVATION></PST>"]


def test_unchanged_target_not_resent() -> None:
    push, sent = _make_push()
    push.notify_target(1730, 250, now=100.0)
    sent.clear()
    push.notify_target(1730, 250, now=101.0)
    assert sent == []


def test_change_is_sent_after_interval() -> None:
    push, sent = _make_push()
    push.notify_target(1730, 250, now=100.0)
    sent.clear()
    push.notify_target(2000, 250, now=101.0)
    assert sent == ["<PST><AZIMUTH>200.0</AZIMUTH></PST>"]


def test_throttle_blocks_rapid_bursts() -> None:
    push, sent = _make_push()
    push.notify_target(1730, 250, now=100.0)
    sent.clear()
    # Sofortige zweite Änderung innerhalb des Mindestintervalls → unterdrückt
    push.notify_target(1800, 250, now=100.02)
    assert sent == []
    # Nach Ablauf des Intervalls → wird gesendet
    push.notify_target(1800, 250, now=100.2)
    assert sent == ["<PST><AZIMUTH>180.0</AZIMUTH></PST>"]


def test_disabled_axis_not_sent() -> None:
    push, sent = _make_push()
    push.notify_target(1730, 250, now=100.0, el_enabled=False)
    assert sent == ["<PST><AZIMUTH>173.0</AZIMUTH></PST>"]


def test_inactive_push_sends_nothing() -> None:
    push = PstTargetPush(controller=None, log=_Log())
    push.start(enabled=False)
    sent: list[str] = []
    push._send = lambda msg: sent.append(msg)  # type: ignore[method-assign]
    push.notify_target(1730, 250, now=100.0)
    assert sent == []


def test_full_circle_target_pushed_as_360_not_0() -> None:
    """Nach Homing (SETHOMERETURN=0) Ist/Soll=360° — Push darf nicht 0.0 melden."""
    push, sent = _make_push()
    push.notify_target(3600, None, now=100.0, el_enabled=False)
    assert sent == ["<PST><AZIMUTH>360.0</AZIMUTH></PST>"]
