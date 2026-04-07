"""Regression: Pending-Match muss Slave (tel.src) mit TX-Ziel (dst) abgleichen."""

from rotortcpbridge.rs485_protocol import Telegram, build, parse


def test_parse_tx_dst_for_slave_specific_pending() -> None:
    line_az = build(1, 20, "GETLIVEBINS", "1;0;12")
    meta = parse(line_az)
    assert meta is not None
    assert meta.dst == 20
    line_el = build(1, 21, "GETLIVEBINS", "1;0;12")
    meta_el = parse(line_el)
    assert meta_el is not None
    assert meta_el.dst == 21


def test_simulated_wrong_slave_would_mismatch() -> None:
    """Antwort von Slave 21 passt nicht zu Pending an Slave 20 (nur Konzept)."""
    line_az = build(1, 20, "GETLIVEBINS", "1;0;12")
    tx = parse(line_az)
    assert tx is not None
    wrong = Telegram(
        src=21,
        dst=1,
        cmd="ACK_GETLIVEBINS",
        params="1;0;12;0;0;0;0;0;0;0;0;0;0;0;0",
        cs=0.0,
        ok=True,
    )
    assert int(wrong.src) != int(tx.dst)
