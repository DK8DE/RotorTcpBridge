"""Abgeschaltete Achse darf nicht dieselbe Bus-ID wie die aktive Achse bekommen."""

from rotortcpbridge.app_config import dedupe_disabled_axis_id, migrate_and_merge_config


def test_disabled_az_same_id_as_el_is_moved() -> None:
    rb = {"slave_az": 20, "slave_el": 20, "enable_az": False, "enable_el": True}
    assert dedupe_disabled_axis_id(rb) is True
    assert rb["slave_el"] == 20
    assert rb["slave_az"] == 21


def test_disabled_el_same_id_as_az_is_moved() -> None:
    rb = {"slave_az": 21, "slave_el": 21, "enable_az": True, "enable_el": False}
    assert dedupe_disabled_axis_id(rb) is True
    assert rb["slave_az"] == 21
    assert rb["slave_el"] == 20


def test_no_change_when_ids_differ_or_both_enabled() -> None:
    rb = {"slave_az": 21, "slave_el": 20, "enable_az": False, "enable_el": True}
    assert dedupe_disabled_axis_id(rb) is False
    rb2 = {"slave_az": 20, "slave_el": 20, "enable_az": True, "enable_el": True}
    assert dedupe_disabled_axis_id(rb2) is False


def test_migrate_config_fixes_profile() -> None:
    cfg = migrate_and_merge_config(
        {"rotor_bus": {"slave_az": 20, "slave_el": 20, "enable_az": False, "enable_el": True}}
    )
    rb = cfg["rotor_bus"]
    assert rb["slave_el"] == 20
    assert rb["slave_az"] != 20
