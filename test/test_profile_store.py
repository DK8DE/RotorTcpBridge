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
