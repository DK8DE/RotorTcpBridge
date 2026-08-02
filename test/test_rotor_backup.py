"""Tests für Rotor-Backup / Restore (XML + lokale Config)."""

from __future__ import annotations

from pathlib import Path

import pytest

from rotortcpbridge.app_config import DEFAULT_CONFIG
from rotortcpbridge.rotor_backup import (
    apply_gui_config_from_backup,
    backup_hw_destinations,
    build_backup_work,
    controller_backupable_pairs,
    extract_gui_config_for_backup,
    is_controller_set_cmd,
    load_rotor_config_xml,
    rotor_backupable_pairs,
    save_rotor_config_xml,
)


def test_gui_backup_includes_all_default_top_level_keys() -> None:
    cfg = {
        "pst_server": {"enabled": True},
        "rotctld_server": {"listen_port": 4533},
        "pst_serial": {"enabled": True, "listeners": [{"port": "COM9"}]},
        "network_modules": [{"name": "gw", "host": "1.2.3.4"}],
        "network_scan": {"enabled": False},
        "rig_bridge": {"enabled": True, "active_rig_id": "r1"},
        "rotor_bus": {"slave_az": 20},
        "hardware_link": {"mode": "com"},
        "ui": {"language": "de"},
        "polling_ms": {"pos_fast": 200},
        "spid": {"ph": 10},
        "pwm": {"value_pct": 80.0},
        "behavior": {"auto_reference_on_connect": True},
        "controller_hw": {"enabled": True, "cont_id": 2},
    }
    gui = extract_gui_config_for_backup(cfg)
    for key in DEFAULT_CONFIG:
        assert key in gui, f"missing key in backup: {key}"
    assert gui["network_modules"][0]["host"] == "1.2.3.4"
    assert gui["pst_serial"]["listeners"][0]["port"] == "COM9"
    assert gui["rotctld_server"]["listen_port"] == 4533


def test_apply_gui_config_replaces_sections() -> None:
    cfg = {
        "ui": {"language": "en", "extra": 1},
        "network_modules": [{"name": "old"}],
        "rotctld_server": {"enabled": False},
    }
    apply_gui_config_from_backup(
        cfg,
        {
            "ui": {"language": "de"},
            "network_modules": [{"name": "new", "host": "9.9.9.9"}],
            "rotctld_server": {"enabled": True, "listen_port": 99},
        },
    )
    assert cfg["ui"] == {"language": "de"}
    assert cfg["network_modules"] == [{"name": "new", "host": "9.9.9.9"}]
    assert cfg["rotctld_server"]["listen_port"] == 99


def test_controller_vs_rotor_pairs_partition() -> None:
    assert is_controller_set_cmd("SETCONANTNAME1")
    assert is_controller_set_cmd("SETLSL")
    assert not is_controller_set_cmd("SETMAXDG")
    assert not is_controller_set_cmd("SETANTOFF1")
    ctrl = {s for s, _ in controller_backupable_pairs()}
    rotor = {s for s, _ in rotor_backupable_pairs()}
    assert "SETCONANTNAME1" in ctrl
    assert "SETLSL" in ctrl
    assert "SETMAXDG" in rotor
    assert "SETANTOFF1" in rotor
    assert ctrl.isdisjoint(rotor)


def test_backup_hw_destinations_include_controller() -> None:
    cfg = {
        "rotor_bus": {
            "master_id": 0,
            "slave_az": 20,
            "slave_el": 21,
            "enable_az": True,
            "enable_el": True,
        },
        "controller_hw": {"enabled": True, "cont_id": 2},
    }
    dests = backup_hw_destinations(cfg)
    assert (20, "rotor") in dests
    assert (21, "rotor") in dests
    assert (2, "controller") in dests


def test_build_backup_work_routes_con_to_controller() -> None:
    cfg = {
        "rotor_bus": {
            "master_id": 0,
            "slave_az": 20,
            "slave_el": 21,
            "enable_az": True,
            "enable_el": False,
        },
        "controller_hw": {"enabled": True, "cont_id": 2},
    }
    work = build_backup_work(cfg)
    assert any(dst == 20 and cmd == "SETMAXDG" for dst, cmd, _ in work)
    assert not any(dst == 21 for dst, _, _ in work)
    assert any(dst == 2 and cmd == "SETCONANTNAME1" for dst, cmd, _ in work)
    assert not any(dst == 20 and cmd.startswith("SETCON") for dst, cmd, _ in work)


def test_backup_skips_controller_when_hw_disabled() -> None:
    cfg = {
        "rotor_bus": {
            "master_id": 0,
            "slave_az": 20,
            "enable_az": True,
            "enable_el": False,
        },
        "controller_hw": {"enabled": False, "cont_id": 2},
    }
    from rotortcpbridge.rotor_backup import controller_hw_enabled, filter_restore_entries_for_encoder

    assert controller_hw_enabled(cfg) is False
    work = build_backup_work(cfg)
    assert not any(cmd.startswith("SETCON") or cmd == "SETLSL" for _, cmd, _ in work)
    assert any(cmd == "SETMAXDG" for _, cmd, _ in work)

    entries = [
        {"dst": 20, "cmd": "SETMAXDG", "params": "420,00"},
        {"dst": 2, "cmd": "SETLSL", "params": "10"},
        {"dst": 2, "cmd": "SETCONLEDP", "params": "50"},
    ]
    filtered = filter_restore_entries_for_encoder(
        entries, encoder_type=1, include_controller=False
    )
    assert [e["cmd"] for e in filtered] == ["SETMAXDG"]


def test_type3_skips_wind_commands_in_backup_and_restore() -> None:
    from rotortcpbridge.rotor_backup import (
        filter_restore_entries_for_encoder,
        is_type3_unsupported_set_cmd,
        should_skip_set_for_encoder,
    )

    assert is_type3_unsupported_set_cmd("SETWINDENABLE")
    assert should_skip_set_for_encoder("SETWINDENABLE", 3)
    assert not should_skip_set_for_encoder("SETWINDENABLE", 1)
    assert not should_skip_set_for_encoder("SETMAXDG", 3)

    cfg = {
        "rotor_bus": {
            "master_id": 0,
            "slave_az": 20,
            "enable_az": True,
            "enable_el": False,
        },
        "controller_hw": {"enabled": True, "cont_id": 2},
    }
    work3 = build_backup_work(cfg, encoder_type=3)
    assert not any(cmd == "SETWINDENABLE" for _, cmd, _ in work3)
    assert not any(cmd == "SETWINDDIROF" for _, cmd, _ in work3)
    assert not any(cmd == "SETCONANO" for _, cmd, _ in work3)
    assert any(cmd == "SETMAXDG" for _, cmd, _ in work3)

    work1 = build_backup_work(cfg, encoder_type=1)
    assert any(cmd == "SETWINDENABLE" for _, cmd, _ in work1)

    entries = [
        {"dst": 20, "cmd": "SETMAXDG", "params": "420,00"},
        {"dst": 20, "cmd": "SETWINDENABLE", "params": "1"},
        {"dst": 2, "cmd": "SETCONANO", "params": "1"},
    ]
    filtered = filter_restore_entries_for_encoder(entries, encoder_type=3)
    assert [e["cmd"] for e in filtered] == ["SETMAXDG"]


def test_xml_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "bak.xml"
    entries = [
        {"dst": 20, "cmd": "SETMAXDG", "params": "420,00"},
        {"dst": 2, "cmd": "SETCONANTNAME1", "params": "Yagi"},
    ]
    gui = extract_gui_config_for_backup(
        {
            "ui": {"antenna_names": ["A", "B", "C"]},
            "rotctld_server": {"enabled": True},
            "network_modules": [],
            "pst_server": {},
            "pst_serial": {"listeners": []},
            "rotor_bus": {"slave_az": 20},
            "hardware_link": {},
            "network_scan": {},
            "polling_ms": {},
            "spid": {},
            "pwm": {},
            "behavior": {},
            "controller_hw": {"cont_id": 2},
            "rig_bridge": {},
        }
    )
    save_rotor_config_xml(path, entries, gui_config=gui)
    loaded_entries, loaded_gui = load_rotor_config_xml(path)
    assert loaded_entries == entries
    assert loaded_gui is not None
    assert loaded_gui["ui"]["antenna_names"] == ["A", "B", "C"]
    assert loaded_gui["rotctld_server"]["enabled"] is True
