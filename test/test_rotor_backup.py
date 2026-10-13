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
    controller_hw_norm,
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
        "gs232_server": {"enabled": False, "listen_port": 4003},
        "easycomm_server": {"enabled": False, "listen_port": 4535},
        "dcu1_server": {"enabled": False, "listen_port": 4004},
        "n1mm_rotor": {"enabled": False, "listen_port": 12040},
        "map_webserver": {"enabled": False, "listen_port": 80},
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
        "controller_link": {"mode": "com", "com_port": ""},
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
    assert is_controller_set_cmd("SETCONLEDP")
    assert is_controller_set_cmd("SETLSL")
    assert not is_controller_set_cmd("SETMAXDG")
    assert not is_controller_set_cmd("SETANTOFF1")
    assert not is_controller_set_cmd("SETANTNAME1")
    ctrl = {s for s, _ in controller_backupable_pairs()}
    rotor = {s for s, _ in rotor_backupable_pairs()}
    assert "SETCONLEDP" in ctrl
    assert "SETLSL" in ctrl
    assert "SETMAXDG" in rotor
    assert "SETANTOFF1" in rotor
    assert "SETANTNAME1" in rotor
    assert ctrl.isdisjoint(rotor)


def test_controller_hw_norm_keeps_speaker_freq_above_254() -> None:
    n = controller_hw_norm({"speaker_freq_hz": 1100, "cont_id": 2})
    assert n["speaker_freq_hz"] == 1100
    n2 = controller_hw_norm({"speaker_freq_hz": 4000})
    assert n2["speaker_freq_hz"] == 4000
    n3 = controller_hw_norm({"speaker_freq_hz": 50})
    assert n3["speaker_freq_hz"] == 200
    n4 = controller_hw_norm({"speaker_volume": 37})
    assert n4["speaker_volume"] == 37


class _FakeCtrl:
    def __init__(self, stored: dict) -> None:
        self.stored = dict(stored)
        self.writes: list = []

    def sync_ui_command_response(self, dst, cmd, params, expect, timeout_s=1.5):
        if cmd.startswith("GET"):
            key = "SET" + cmd[3:]
            if key not in self.stored:
                return None
            return str(self.stored[key])
        self.writes.append((cmd, params))
        self.stored[cmd] = int(params)
        return params


def test_push_controller_hw_skips_equal_values() -> None:
    from rotortcpbridge.rotor_backup import push_controller_hw_diff

    chw = {
        "enabled": True,
        "cont_id": 2,
        "slow_pwm": 30,
        "fast_pwm": 80,
        "speaker_freq_hz": 1500,
        "speaker_volume": 37,
        "display_brightness_pct": 60,
        "encoder_delta": 10,
        "antenna_realign_on_switch": False,
        "az_rotor_id": 20,
        "el_rotor_id": 0,
        "wind_anemometer": False,
        "usb_remote": False,
    }
    ctrl = _FakeCtrl(
        {
            "SETCONSPWM": 30,
            "SETCONFPWM": 80,
            "SETCONFRQ": 1500,
            "SETLSL": 20,
            "SETCONLEDP": 60,
            "SETCONDELTA": 10,
            "SETCONCHA": 0,
            "SETCONTAZID": 20,
            "SETCONTELID": 0,
            "SETCONANO": 0,
            "SETCONREMOTE": 0,
        }
    )
    ok = push_controller_hw_diff(ctrl, old_chw=chw, new_chw=chw, connected=True, force=True)
    assert ok
    assert ctrl.writes == [("SETLSL", "37")]


def test_push_controller_rotor_ids_follows_profile_slave_ids() -> None:
    """Web-Display: Profilwechsel Default (22) → Rotor 2 (20) muss am Display ankommen."""
    from rotortcpbridge.rotor_backup import push_controller_rotor_ids

    cfg = {
        "rotor_bus": {"slave_az": 20, "slave_el": 21, "enable_az": True, "enable_el": False},
        "controller_hw": {"cont_id": 2, "enabled": False},
    }
    ctrl = _FakeCtrl({"SETCONTAZID": 22, "SETCONTELID": 0})
    assert push_controller_rotor_ids(ctrl, cfg)
    assert ctrl.writes == [("SETCONTAZID", "20")]
    # zweiter Aufruf: nichts mehr zu schreiben
    ctrl.writes.clear()
    assert push_controller_rotor_ids(ctrl, cfg)
    assert ctrl.writes == []


def test_app_config_link_mode_normalized() -> None:
    from rotortcpbridge.app_config import migrate_and_merge_config

    cfg = migrate_and_merge_config(
        {"controller_hw": {"enabled": True, "usb_remote": True, "link_mode": 2}}
    )
    assert cfg["controller_hw"]["link_mode"] == 2
    assert cfg["controller_hw"]["usb_remote"] is False
    cfg = migrate_and_merge_config({"controller_hw": {"enabled": True, "usb_remote": True}})
    assert cfg["controller_hw"]["link_mode"] == 1


def test_push_controller_hw_skip_remote_keeps_web_link() -> None:
    """Display per Web Serial: Profilwechsel darf SETCONREMOTE/Link nicht anfassen."""
    from rotortcpbridge.rotor_backup import push_controller_hw_diff

    chw = {"enabled": True, "cont_id": 2, "az_rotor_id": 20, "usb_remote": False}
    link = object()

    class _Ctrl(_FakeCtrl):
        def __init__(self) -> None:
            super().__init__({"SETCONREMOTE": 1, "SETCONTAZID": 11})
            self.ctrl_hw = link
            self.usb_remote = True

        def set_controller_link(self, hw, enabled) -> None:
            self.ctrl_hw = hw
            self.usb_remote = bool(enabled) and hw is not None

    ctrl = _Ctrl()
    ok = push_controller_hw_diff(
        ctrl, old_chw=chw, new_chw=chw, connected=True, force=True, skip_remote=True
    )
    assert ok
    assert ctrl.usb_remote is True and ctrl.ctrl_hw is link
    assert all(cmd != "SETCONREMOTE" for cmd, _ in ctrl.writes)
    assert ("SETCONTAZID", "20") in ctrl.writes

    ctrl2 = _Ctrl()
    push_controller_hw_diff(ctrl2, old_chw=chw, new_chw=chw, connected=True, force=True)
    assert ctrl2.usb_remote is False  # ohne skip_remote: bisheriges Verhalten


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
    assert any(dst == 2 and cmd == "SETCONLEDP" for dst, cmd, _ in work)
    assert any(dst == 20 and cmd == "SETANTNAME1" for dst, cmd, _ in work)
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
        {"dst": 20, "cmd": "SETANTNAME1", "params": "Yagi"},
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


class _FakeAz:
    antoff1 = 10.0
    antoff2 = 20.0
    antoff3 = 30.0
    antdp1 = False
    antdp2 = False
    antdp3 = False


class _FakeCtrlOffsets:
    def __init__(self) -> None:
        self.az = _FakeAz()
        self.enable_az = True
        self.slave_az = 20
        self.synced: list[tuple] = []
        self.dipole_calls: list[tuple] = []
        self._antenna_bootstrap_requested = False
        self.on_antenna_offsets_changed = None
        self.on_antenna_dipoles_changed = None

    def sync_ui_command_response(self, dst, cmd, params, expect_prefix, timeout_s=5.0):
        self.synced.append((dst, cmd, params, expect_prefix, timeout_s))
        return "1"

    def set_antenna_dipole(self, axis, slot, value_enabled, on_done=None):
        self.dipole_calls.append((axis, slot, bool(value_enabled)))
        if on_done:
            on_done(True)


def test_push_profile_antenna_offsets_writes_setantoff() -> None:
    from rotortcpbridge.rotor_backup import push_profile_antenna_offsets

    ctrl = _FakeCtrlOffsets()
    cfg = {
        "ui": {
            "antenna_offsets_az": [11.0, 22.0, 33.0],
            "antenna_dipoles_az": [False, True, False],
        }
    }
    assert push_profile_antenna_offsets(ctrl, cfg, connected=True) is True
    assert ctrl.az.antoff1 == 11.0
    assert ctrl.az.antoff2 == 22.0
    assert ctrl.az.antoff3 == 33.0
    assert ctrl.az.antdp2 is True
    cmds = [c[1] for c in ctrl.synced]
    assert cmds == [
        "SETANTOFF1",
        "SETANTOFF2",
        "SETANTOFF3",
        "SETANTDP1",
        "SETANTDP2",
        "SETANTDP3",
    ]
    assert ctrl.synced[4][2] == "1"
    assert ctrl._antenna_bootstrap_requested is True


def test_push_profile_antenna_offsets_seeds_when_offline() -> None:
    from rotortcpbridge.rotor_backup import push_profile_antenna_offsets

    ctrl = _FakeCtrlOffsets()
    cfg = {
        "ui": {
            "antenna_offsets_az": [5.0, 6.0, 7.0],
            "antenna_dipoles_az": [True, False, False],
        }
    }
    assert push_profile_antenna_offsets(ctrl, cfg, connected=False) is True
    assert ctrl.az.antoff1 == 5.0
    assert ctrl.az.antdp1 is True
    assert ctrl.synced == []
    assert ctrl._antenna_bootstrap_requested is False


def test_push_antenna_dipoles_async_sets_all_slots() -> None:
    from rotortcpbridge.rotor_backup import push_antenna_dipoles_async

    ctrl = _FakeCtrlOffsets()
    cfg = {"ui": {"antenna_dipoles_az": [True, False, True]}}
    push_antenna_dipoles_async(ctrl, cfg)
    assert ctrl.dipole_calls == [("az", 1, True), ("az", 2, False), ("az", 3, True)]
