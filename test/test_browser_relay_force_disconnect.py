"""Profilwechsel WEB→PC-USB: Relay fordert Browser zum Trennen auf."""

from __future__ import annotations

from types import SimpleNamespace

from rotortcpbridge.browser_bus_relay import BrowserBusRelay


def test_force_disconnect_flag_in_handle_response():
    log = SimpleNamespace(write=lambda *_a, **_k: None)
    ctrl = SimpleNamespace()
    rotor_hw = SimpleNamespace(
        is_connected=lambda: False,
        on_rx_line=None,
        on_tx_line=None,
    )
    relay = BrowserBusRelay(
        rotor_hw,
        ctrl,
        log,
        cfg_getter=lambda: {"controller_hw": {"cont_id": 2, "link_mode": 2}},
        desktop_proxy_getter=lambda: None,
    )
    # Attach ohne Desktop-Konflikt
    relay._attach()
    assert relay.is_active()
    relay.request_force_disconnect()
    out = relay.handle({"tx": [], "since": 0})
    assert out.get("force_disconnect") is True
    assert out.get("active") is False
    assert not relay.is_active()
