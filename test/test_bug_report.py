"""Tests Bug-Report / GUI-Backup-Keys."""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

from rotortcpbridge.app_config import DEFAULT_CONFIG
from rotortcpbridge.bug_report import (
    REQUIRED_GUI_BACKUP_KEYS,
    create_bug_report_zip,
    gui_backup_has_required_keys,
    write_rotor_backup_xml,
)
from rotortcpbridge.rotor_backup import extract_gui_config_for_backup


def test_extract_includes_new_protocol_keys() -> None:
    gui = extract_gui_config_for_backup(DEFAULT_CONFIG)
    missing = gui_backup_has_required_keys(gui)
    assert missing == [], f"missing keys: {missing}"


def test_write_rotor_backup_xml_embeds_gui(tmp_path: Path) -> None:
    path = tmp_path / "rotor_backup.xml"
    gui = write_rotor_backup_xml(path, DEFAULT_CONFIG, hw_entries=[])
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "gs232_server" in text
    assert "n1mm_rotor" in text
    assert "easycomm_server" in gui
    assert "dcu1_server" in gui


def test_create_bug_report_zip(tmp_path: Path) -> None:
    zpath = tmp_path / "bug.zip"
    create_bug_report_zip(zpath, cfg=DEFAULT_CONFIG, hw_entries=[], notes="test")
    assert zpath.is_file()
    with zipfile.ZipFile(zpath, "r") as zf:
        names = set(zf.namelist())
        assert "version.txt" in names
        assert "README.txt" in names
        assert "rotor_backup.xml" in names
        assert "active_config.json" in names
        active = json.loads(zf.read("active_config.json").decode("utf-8"))
        for key in REQUIRED_GUI_BACKUP_KEYS:
            assert key in active
