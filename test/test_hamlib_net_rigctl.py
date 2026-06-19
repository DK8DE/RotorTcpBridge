"""Hamlib NET rigctld: SETFREQ-Parsing und Befehlserkennung."""

from __future__ import annotations

from rotortcpbridge.rig_bridge.protocol_hamlib_net_rigctl import (
    HamlibNetRigctlServer,
    _hz_from_pending_freq_line,
    _looks_like_set_freq_awaiting_value,
    _parse_set_freq_hz,
    _strip_erp_cmd_prefix,
)


def test_strip_erp_prefix() -> None:
    assert _strip_erp_cmd_prefix("+F 14074000") == "F 14074000"
    assert _strip_erp_cmd_prefix("\\set_freq 14074000") == "\\set_freq 14074000"


def test_parse_set_freq_variants() -> None:
    assert _parse_set_freq_hz("F 28829600") == 28829600
    assert _parse_set_freq_hz("+F 28829600") == 28829600
    assert _parse_set_freq_hz("\\set_freq 28829600") == 28829600
    assert _parse_set_freq_hz("F28829600") == 28829600
    assert _parse_set_freq_hz("F VFOA 28829600") == 28829600


def test_pending_mhz_line() -> None:
    assert _looks_like_set_freq_awaiting_value("F")
    assert _hz_from_pending_freq_line("28.829600") == 28829600


def test_handle_set_freq_and_level() -> None:
    writes: list[str] = []
    patches: list[dict] = []

    srv = HamlibNetRigctlServer(
        get_state=lambda: {"frequency_hz": 0, "mode": "USB", "vfo": "A", "ptt": False},
        enqueue_write=lambda cmd, _ctx: writes.append(cmd),
        on_clients_changed=lambda _n: None,
        log_write=lambda _lvl, _msg: None,
        on_state_patch=lambda p: patches.append(p),
    )
    srv._running = True

    assert srv._handle_cmd("l KEYSPD") == "0"
    assert srv._handle_cmd("+F 22300000") == "RPRT 0"
    assert writes[-1] == "SETFREQ 22300000"
    assert patches[-1]["frequency_hz"] == 22300000

    writes.clear()
    assert srv._handle_cmd("I 22400000") == "RPRT 0"
    assert writes[-1] == "SETFREQ 22400000"
