"""Log-Fenster: Paketzähler ausblendbar."""

from __future__ import annotations

from rotortcpbridge.ui.log_window import strip_packet_counter


def test_strip_tx_counter() -> None:
    line = "2026-10-12 17:03:54.018 [TX] #155706 #0:20:GETANEMO:0:20$"
    assert strip_packet_counter(line) == "2026-10-12 17:03:54.018 [TX] #0:20:GETANEMO:0:20$"


def test_strip_rx_counter() -> None:
    line = "2026-10-12 17:03:54.137 [RX] pkt=155464 #20:0:ACK_GETANEMO:0,0:20$"
    assert strip_packet_counter(line) == "2026-10-12 17:03:54.137 [RX] #20:0:ACK_GETANEMO:0,0:20$"


def test_other_lines_unchanged() -> None:
    for line in (
        "2026-10-12 17:03:54.016 [INFO] AZ referenziert",
        "2026-10-12 17:03:54.016 [TX] #0:20:GETPOSDG:0:20$",  # ohne Zähler
        "",
    ):
        assert strip_packet_counter(line) == line
