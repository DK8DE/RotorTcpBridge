"""Log-Zähler (TX-Nummer, RX-Paketnummer) dürfen im Dauerbetrieb nicht endlos wachsen."""

from __future__ import annotations

from rotortcpbridge.hardware_client import _LOG_SEQ_WRAP


def _next_seq(cur: int) -> int:
    """Gleiche Formel wie in _emit_wire_tx / _reader_loop."""
    return (int(cur) % _LOG_SEQ_WRAP) + 1


def test_seq_starts_at_one() -> None:
    assert _next_seq(0) == 1


def test_seq_counts_up() -> None:
    assert _next_seq(1) == 2
    assert _next_seq(41) == 42


def test_seq_wraps_back_to_one() -> None:
    assert _next_seq(_LOG_SEQ_WRAP - 1) == _LOG_SEQ_WRAP
    assert _next_seq(_LOG_SEQ_WRAP) == 1


def test_seq_stays_in_range_over_full_cycle() -> None:
    seq = 0
    for _ in range(_LOG_SEQ_WRAP + 5):
        seq = _next_seq(seq)
        assert 1 <= seq <= _LOG_SEQ_WRAP
