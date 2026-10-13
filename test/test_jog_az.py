"""JOG: Taste halten = JOG:1/2 wiederholen, Loslassen = JOG:0 + Position halten."""

from __future__ import annotations

import time
from typing import Any, cast

from rotortcpbridge.hardware_client import HardwareClient
from rotortcpbridge.rotor_controller import RotorController
from rotortcpbridge.rs485_protocol import build, parse


class _Log:
    def __init__(self) -> None:
        self.msgs: list[tuple[str, str]] = []

    def write(self, level: str, msg: str) -> None:
        self.msgs.append((level, msg))


class _Hw:
    def __init__(self) -> None:
        self.requests: list[Any] = []

    def send_request(self, req: Any) -> None:
        self.requests.append(req)

    def send_line_fire_and_forget(self, line: str) -> None:
        pass

    def is_connected(self) -> bool:
        return True


def _ctrl() -> tuple[RotorController, _Hw, _Log]:
    hw = _Hw()
    log = _Log()
    c = RotorController(cast(HardwareClient, hw), master_id=0, slave_az=20, slave_el=21, log=log)
    c.az.referenced = True
    c.az.pos_d10 = 1800
    return c, hw, log


def _cmds(hw: _Hw, *, with_dst: bool = False) -> list[tuple[Any, ...]]:
    """Gesendete Befehle ohne die zusätzlichen GETPOSDG-Abfragen."""
    out: list[tuple[Any, ...]] = []
    for r in hw.requests:
        tel = parse(r.line)
        assert tel is not None and tel.ok
        if str(tel.cmd) == "GETPOSDG":
            continue
        item: tuple[Any, ...] = (str(tel.cmd), str(tel.params))
        out.append((str(tel.dst),) + item if with_dst else item)
    return out


def _getposdg_count(hw: _Hw) -> int:
    return sum(1 for r in hw.requests if str(parse(r.line).cmd) == "GETPOSDG")


def _jog_requests(hw: _Hw) -> list[Any]:
    return [r for r in hw.requests if str(parse(r.line).cmd) == "JOG"]


def test_jog_frame_has_valid_checksum_and_expects_ack() -> None:
    c, hw, _ = _ctrl()
    c.jog_az(1)
    assert _cmds(hw) == [("JOG", "1")]
    req = _jog_requests(hw)[0]
    assert req.line.startswith("#0:20:JOG:1:")
    assert req.expect_prefix == "ACK_JOG"


def test_jog_start_triggers_immediate_getposdg() -> None:
    c, hw, _ = _ctrl()
    c.jog_az(2)
    assert _getposdg_count(hw) == 1


def test_jog_release_with_fresh_position_holds_immediately() -> None:
    c, hw, _ = _ctrl()
    c.jog_az(2)
    c.az.pos_d10 = 1953  # zuletzt angezeigte Position beim Loslassen
    c.az.pos_poll_last_ack_ts = time.time()  # frisch
    c.jog_az(0)
    c.jog_az(0)  # zweites Loslassen sendet nichts mehr
    assert _cmds(hw) == [("JOG", "2"), ("JOG", "0"), ("SETPOSDG", "195,30")]
    assert c.az.target_d10 == 1953
    assert c.az.last_set_sent_target_d10 == 1953


def test_jog_release_with_stale_position_waits_for_fresh_sample() -> None:
    """Kurzer Klick: noch kein GETPOSDG seit dem Drücken -> erst frische Position, dann halten."""
    c, hw, _ = _ctrl()
    c.jog_az(1)
    c.az.pos_poll_last_ack_ts = time.time() - 5.0  # alter Wert
    c.jog_az(0)
    assert _cmds(hw) == [("JOG", "1"), ("JOG", "0")]  # noch kein SETPOSDG mit altem Wert
    c.az.pos_d10 = 1744  # Rotor hat sich bewegt, neue Antwort kommt
    c.az.pos_poll_last_ack_ts = time.time() + 0.01
    c._tick_jog_hold(time.time())
    assert _cmds(hw) == [("JOG", "1"), ("JOG", "0"), ("SETPOSDG", "174,40")]
    assert c.az.target_d10 == 1744
    # nur einmal
    c._tick_jog_hold(time.time())
    assert _cmds(hw)[-1] == ("SETPOSDG", "174,40")
    assert len(_cmds(hw)) == 3


def test_jog_release_stale_hold_falls_back_after_timeout() -> None:
    c, hw, _ = _ctrl()
    c.jog_az(1)
    c.az.pos_poll_last_ack_ts = time.time() - 5.0
    c.jog_az(0)
    c._tick_jog_hold(time.time() + 1.0)  # keine Antwort gekommen
    assert _cmds(hw)[-1][0] == "SETPOSDG"


def test_manual_target_cancels_pending_jog_hold() -> None:
    c, hw, _ = _ctrl()
    c.jog_az(1)
    c.az.pos_poll_last_ack_ts = time.time() - 5.0
    c.jog_az(0)
    c.set_az_deg(90.0, force=True)
    c._tick_jog_hold(time.time() + 1.0)
    setpos = [x for x in _cmds(hw) if x[0] == "SETPOSDG"]
    assert setpos == [("SETPOSDG", "90,00")]


def test_jog_repeat_is_throttled_while_request_in_flight() -> None:
    c, hw, _ = _ctrl()
    c.jog_az(1)
    c.jog_az(1)
    c.jog_az(1)
    assert len(_jog_requests(hw)) == 1  # Antwort steht noch aus
    _jog_requests(hw)[0].on_done(None, "timeout")
    c.jog_az(1)
    assert len(_jog_requests(hw)) == 2


def test_jog_resends_after_inflight_limit() -> None:
    c, hw, _ = _ctrl()
    c.jog_az(1)
    c._jog_az_inflight_ts = time.time() - 1.0  # Antwort blieb aus
    c.jog_az(1)
    assert len(_jog_requests(hw)) == 2


def test_jog_updates_axis_state_without_fixed_target() -> None:
    c, hw, _ = _ctrl()
    c.az.last_set_sent_target_d10 = 900
    c.az.compass_target_d10 = 900
    c.jog_az(1)
    assert c.az.moving is True
    assert c.az.last_set_sent_target_d10 is None
    assert c.az.compass_target_d10 is None
    assert c.az.target_d10 == 1800


def test_jog_invalid_direction_ignored() -> None:
    c, hw, _ = _ctrl()
    c.jog_az(3)
    c.jog_az(-1)
    assert hw.requests == []


def test_jog_nak_stops_and_logs_reason() -> None:
    c, hw, log = _ctrl()
    c.jog_az(1)
    tel = parse(build(20, 0, "NAK_JOG", "NOREF"))
    assert tel is not None
    _jog_requests(hw)[0].on_done(tel, None)
    assert c.az.moving is False
    assert any("abgelehnt" in m for _lvl, m in log.msgs)
    n = len(hw.requests)
    c.jog_az(0)  # nach NAK ist JOG nicht mehr aktiv -> kein weiterer Stopp/SETPOSDG nötig
    assert len(hw.requests) == n


def test_jog_disabled_axis_sends_nothing() -> None:
    c, hw, _ = _ctrl()
    c.enable_az = False
    c.jog_az(1)
    assert hw.requests == []


def test_jog_fast_poll_flag_active_and_tail() -> None:
    c, hw, _ = _ctrl()
    assert c._jog_fast_poll_axis(c.az) is False
    c.jog_az(1)
    assert c._jog_fast_poll_axis(c.az) is True
    assert c._jog_fast_poll_axis(c.el) is False
    c.az.pos_poll_last_ack_ts = time.time()
    c.jog_az(0)
    assert c._jog_fast_poll_axis(c.az) is True  # Nachlauf
    assert c._jog_fast_poll_axis(c.az, time.time() + 5.0) is False


def test_jog_fast_poll_shortens_min_interval() -> None:
    c, _hw, _ = _ctrl()
    slow = c._motion_pos_min_interval(c.az, 0.2, True)
    assert slow >= 0.25
    c.jog_az(1)
    fast = c._motion_pos_min_interval(c.az, 0.2, True)
    assert fast < 0.15


def test_jog_el_uses_el_slave_and_holds_position_on_release() -> None:
    c, hw, _ = _ctrl()
    c.el.referenced = True
    c.el.pos_d10 = 452
    c.jog_el(2)
    c.jog_el(2)  # gedrosselt
    c.el.pos_d10 = 471
    c.el.pos_poll_last_ack_ts = time.time()
    c.jog_el(0)
    c.jog_el(0)
    assert _cmds(hw, with_dst=True) == [
        ("21", "JOG", "2"),
        ("21", "JOG", "0"),
        ("21", "SETPOSDG", "47,10"),
    ]
    assert c.el.target_d10 == 471
    assert c.az.moving is False


def test_jog_el_and_az_are_independent() -> None:
    c, hw, _ = _ctrl()
    c.el.referenced = True
    c.jog_az(1)
    c.jog_el(1)
    assert [x[0] for x in _cmds(hw, with_dst=True)] == ["20", "21"]
    c.el.pos_poll_last_ack_ts = time.time()
    c.jog_el(0)
    assert c._jog_az_active is True
    assert c._jog_el_active is False


def test_jog_el_disabled_sends_nothing() -> None:
    c, hw, _ = _ctrl()
    c.enable_el = False
    c.jog_el(1)
    assert hw.requests == []
