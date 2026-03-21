"""Tests für PST-UDP Positions-Push-Logik (Null-Spurios-Debounce)."""

from __future__ import annotations


from rotortcpbridge.pst_notify_logic import pst_notify_position_decision


def test_no_send_when_unchanged_within_one_d10() -> None:
    send, zc = pst_notify_position_decision(1309, 1309, 0, zero_confirm_ticks=3)
    assert send is False
    assert zc == 0


def test_send_when_move_at_least_one_d10() -> None:
    send, zc = pst_notify_position_decision(1310, 1309, 0, zero_confirm_ticks=3)
    assert send is True
    assert zc == 0


def test_glitch_single_zero_suppressed() -> None:
    last = 1309
    send, zc = pst_notify_position_decision(0, last, 0, zero_confirm_ticks=3)
    assert send is False
    assert zc == 1


def test_glitch_zero_needs_three_ticks() -> None:
    last = 1309
    s1, z1 = pst_notify_position_decision(0, last, 0, zero_confirm_ticks=3)
    assert s1 is False and z1 == 1
    s2, z2 = pst_notify_position_decision(0, last, z1, zero_confirm_ticks=3)
    assert s2 is False and z2 == 2
    s3, z3 = pst_notify_position_decision(0, last, z2, zero_confirm_ticks=3)
    assert s3 is True and z3 == 3


def test_recovery_after_glitch_without_send() -> None:
    last = 1309
    _, z1 = pst_notify_position_decision(0, last, 0, zero_confirm_ticks=3)
    assert z1 == 1
    send, z2 = pst_notify_position_decision(1309, last, z1, zero_confirm_ticks=3)
    assert send is False
    assert z2 == 0


def test_first_send_can_be_zero() -> None:
    send, zc = pst_notify_position_decision(0, None, 0, zero_confirm_ticks=3)
    assert send is True
    assert zc == 0
