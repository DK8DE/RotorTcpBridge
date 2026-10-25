"""Tests für Rotor-Profile (Migration, CRUD, Switch, Persistenz)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture()
def profile_env(tmp_path, monkeypatch):
    """AppData-Verzeichnis auf tmp umlenken."""
    import rotortcpbridge.app_config as app_config
    import rotortcpbridge.profile_store as ps

    monkeypatch.setattr(app_config, "appdata_dir", lambda: tmp_path)
    # Modul-Caches / Pfade neu nutzen
    return tmp_path, app_config, ps


def test_migrate_legacy_config_to_default(profile_env):
    tmp_path, app_config, ps = profile_env
    legacy = tmp_path / "config.json"
    legacy.write_text(
        json.dumps({"ui": {"language": "de"}, "rotor_bus": {"slave_az": 7}}),
        encoding="utf-8",
    )
    cfg = ps.load_active_config()
    assert (tmp_path / "profiles" / "profiles.json").exists()
    assert (tmp_path / "profiles" / "default.json").exists()
    assert ps.get_active_profile_id() == "default"
    assert cfg.get("rotor_bus", {}).get("slave_az") == 7
    assert any(p.get("is_default") for p in ps.list_profiles())


def test_new_profile_is_virgin_not_clone(profile_env):
    _tmp, _ac, ps = profile_env
    ps.ensure_profiles_migrated()
    cfg_a = ps.load_active_config()
    cfg_a.setdefault("rotor_bus", {})["slave_az"] = 99
    cfg_a.setdefault("ui", {})["force_dark_mode"] = False
    ps.save_active_config(cfg_a)

    pid = ps.create_profile("Frisch", clone_active=False)
    raw = ps.load_profile_config(pid)
    # Nicht die geänderten Default-Werte übernehmen
    assert raw.get("rotor_bus", {}).get("slave_az") != 99
    from rotortcpbridge.app_config import DEFAULT_CONFIG

    assert raw.get("rotor_bus", {}).get("slave_az") == DEFAULT_CONFIG["rotor_bus"]["slave_az"]


def test_seed_commissioning_runtime_link_keeps_bus_virgin(profile_env):
    _tmp, _ac, ps = profile_env
    ps.ensure_profiles_migrated()
    src = ps.load_active_config()
    src.setdefault("hardware_link", {})["mode"] = "tcp"
    src.setdefault("hardware_link", {})["tcp_ip"] = "10.0.0.42"
    src.setdefault("controller_hw", {})["usb_remote"] = True
    src.setdefault("controller_hw", {})["link_mode"] = 1
    src.setdefault("controller_link", {})["com_port"] = "COM7"
    src.setdefault("rotor_bus", {})["slave_az"] = 55
    src.setdefault("ui", {})["setup_axis_endpoints"] = {
        "az": {"host": "10.0.0.42", "port": 8886},
        "el": {"host": "10.0.0.43", "port": 8886},
    }
    ps.save_active_config(src)

    pid = ps.create_profile("R und S", clone_active=False)
    ps.seed_commissioning_runtime_link(pid, src)
    raw = ps.load_profile_config(pid)
    assert raw["hardware_link"]["mode"] == "tcp"
    assert raw["hardware_link"]["tcp_ip"] == "10.0.0.42"
    assert raw["controller_hw"]["usb_remote"] is True
    assert raw["controller_hw"]["link_mode"] == 1
    assert raw["controller_link"]["com_port"] == "COM7"
    assert raw["ui"]["setup_axis_endpoints"]["el"]["host"] == "10.0.0.43"
    # Bus bleibt jungfräulich
    from rotortcpbridge.app_config import DEFAULT_CONFIG

    assert raw["rotor_bus"]["slave_az"] == DEFAULT_CONFIG["rotor_bus"]["slave_az"]
    assert raw["rotor_bus"]["slave_az"] != 55


def test_copy_profile_from_selected(profile_env):
    _tmp, _ac, ps = profile_env
    ps.ensure_profiles_migrated()
    cfg_a = ps.load_active_config()
    cfg_a.setdefault("rotor_bus", {})["slave_az"] = 77
    ps.save_active_config(cfg_a)
    pid = ps.copy_profile("default", "Default Kopie")
    raw = ps.load_profile_config(pid)
    assert raw.get("rotor_bus", {}).get("slave_az") == 77
    assert pid != "default"
    assert any(p["id"] == pid and p["name"] == "Default Kopie" for p in ps.list_profiles())


def test_default_not_deletable_and_crud(profile_env):
    _tmp, _ac, ps = profile_env
    ps.ensure_profiles_migrated()
    with pytest.raises(ValueError):
        ps.delete_profile("default")
    pid = ps.create_profile("Station B")
    assert pid != "default"
    names = {p["name"] for p in ps.list_profiles()}
    assert "Station B" in names
    ps.rename_profile(pid, "Station C")
    assert any(p["id"] == pid and p["name"] == "Station C" for p in ps.list_profiles())
    ps.delete_profile(pid)
    assert all(p["id"] != pid for p in ps.list_profiles())


def test_switch_preserves_per_profile_changes(profile_env):
    _tmp, _ac, ps = profile_env
    ps.ensure_profiles_migrated()
    cfg_a = ps.load_active_config()
    cfg_a.setdefault("rotor_bus", {})["slave_az"] = 11
    ps.save_active_config(cfg_a)

    pid_b = ps.create_profile("B")
    cfg_b = ps.switch_profile(pid_b)
    cfg_b.setdefault("rotor_bus", {})["slave_az"] = 22
    ps.save_active_config(cfg_b)

    cfg_back = ps.switch_profile("default")
    assert cfg_back.get("rotor_bus", {}).get("slave_az") == 11
    cfg_b2 = ps.switch_profile(pid_b)
    assert cfg_b2.get("rotor_bus", {}).get("slave_az") == 22


def test_save_config_writes_active_profile(profile_env):
    tmp_path, app_config, ps = profile_env
    ps.ensure_profiles_migrated()
    pid = ps.create_profile("X")
    ps.switch_profile(pid)
    cfg = ps.load_active_config()
    cfg.setdefault("ui", {})["force_dark_mode"] = False
    app_config.save_config(cfg)
    raw = json.loads((tmp_path / "profiles" / f"{pid}.json").read_text(encoding="utf-8"))
    assert raw.get("ui", {}).get("force_dark_mode") is False


def test_export_import_profile(profile_env):
    tmp_path, _ac, ps = profile_env
    ps.ensure_profiles_migrated()
    cfg = ps.load_active_config()
    cfg.setdefault("rotor_bus", {})["slave_az"] = 42
    ps.save_active_config(cfg)
    out = tmp_path / "export" / "station.json"
    ps.export_profile("default", out)
    raw = json.loads(out.read_text(encoding="utf-8"))
    assert raw.get("format") == ps.PROFILE_EXPORT_FORMAT
    assert raw.get("config", {}).get("rotor_bus", {}).get("slave_az") == 42

    new_id = ps.import_profile(out)
    assert new_id != "default"
    imported = ps.load_profile_config(new_id)
    assert imported.get("rotor_bus", {}).get("slave_az") == 42
    assert any(p["id"] == new_id for p in ps.list_profiles())

    # Reine Config-JSON ebenfalls importierbar
    plain = tmp_path / "plain.json"
    plain.write_text(json.dumps({"rotor_bus": {"slave_az": 55}, "ui": {}}), encoding="utf-8")
    pid2 = ps.import_profile(plain, name="Plain Import")
    assert ps.load_profile_config(pid2).get("rotor_bus", {}).get("slave_az") == 55
    assert any(p["id"] == pid2 and p["name"] == "Plain Import" for p in ps.list_profiles())


def test_create_profile_single_list_entry(profile_env):
    _tmp, _ac, ps = profile_env
    ps.ensure_profiles_migrated()
    pid = ps.create_profile("Mein Rotor")
    rows = [p for p in ps.list_profiles() if p["id"] == pid]
    assert len(rows) == 1
    assert rows[0]["name"] == "Mein Rotor"
    ids = [p["id"] for p in ps.list_profiles()]
    assert len(ids) == len(set(ids))


def test_normalize_index_dedupes_duplicate_ids():
    from rotortcpbridge.profile_store import _normalize_index

    idx = _normalize_index(
        {
            "active_id": "p_abc",
            "profiles": [
                {"id": "default", "name": "Default", "file": "default.json", "is_default": True},
                {"id": "p_abc", "name": "p_abc", "file": "p_abc.json"},
                {"id": "p_abc", "name": "Station West", "file": "p_abc.json"},
            ],
        }
    )
    rows = [p for p in idx["profiles"] if p["id"] == "p_abc"]
    assert len(rows) == 1
    assert rows[0]["name"] == "Station West"


def test_load_index_does_not_rewrite_on_every_read(profile_env, monkeypatch):
    _tmp, _ac, ps = profile_env
    ps.ensure_profiles_migrated()
    writes = {"n": 0}
    real = ps._save_index_unlocked

    def counted(index):
        writes["n"] += 1
        return real(index)

    monkeypatch.setattr(ps, "_save_index_unlocked", counted)
    before = writes["n"]
    ps.list_profiles()
    ps.get_active_profile_id()
    ps.get_active_profile_meta()
    assert writes["n"] == before


def test_recover_orphan_profile_files_after_wiped_index(profile_env):
    tmp_path, _ac, ps = profile_env
    ps.ensure_profiles_migrated()
    pid = ps.create_profile("Station Lost")
    cfg = ps.load_profile_config(pid)
    cfg.setdefault("rotor_bus", {})["slave_az"] = 33
    ps.save_profile_config(pid, cfg)
    # Index auf Default zurücksetzen, Datei bleibt liegen (wie nach Race).
    ps.save_index(ps._empty_index())
    ps._INDEX_CACHE = None
    ps._INDEX_CACHE_PATH = None
    names = {p["id"]: p for p in ps.list_profiles()}
    assert pid in names
    assert ps.load_profile_config(pid).get("rotor_bus", {}).get("slave_az") == 33
