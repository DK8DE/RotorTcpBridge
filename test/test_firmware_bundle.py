"""Tests: Firmware-Bundle-Pfade und Versionsvergleich (ohne Netz)."""

from __future__ import annotations

from pathlib import Path

import pytest


def test_normalize_and_equal():
    from rotortcpbridge.firmware_compat_check import normalize_version, versions_equal

    assert normalize_version("1.10.8") == "1.10.8"
    assert normalize_version("v1.10.8;extra") == "1.10.8"
    assert normalize_version("1.5") == "1.5.0"
    assert versions_equal("1.10.8", "v1.10.8")
    assert not versions_equal("1.10.7", "1.10.8")
    assert not versions_equal("", "1.0.0")


def test_pinned_components_and_paths(tmp_path, monkeypatch):
    import rotortcpbridge.firmware_bundle as fb
    import rotortcpbridge.version as ver

    monkeypatch.setattr(fb, "_user_downloads_dir", lambda: tmp_path)
    monkeypatch.setattr(ver, "APP_VERSION", "9.9.9")
    monkeypatch.setattr(fb, "APP_VERSION", "9.9.9")

    comps = fb.pinned_components()
    assert len(comps) == 4
    keys = {c.key for c in comps}
    assert keys == {"rotor", "controller", "inclinometer", "wlan"}

    root = fb.bundle_root_for_app("9.9.9")
    assert root == tmp_path / ver.FW_DOWNLOAD_ROOT_NAME / "9.9.9"
    rotor = next(c for c in comps if c.key == "rotor")
    d = fb.component_dir(rotor, app_version="9.9.9")
    assert d.name == f"{rotor.repo}-{rotor.version}"
    assert not fb.component_assets_complete(rotor, app_version="9.9.9")

    d.mkdir(parents=True)
    for a in rotor.assets:
        (d / a).write_bytes(b"x")
    assert fb.component_assets_complete(rotor, app_version="9.9.9")


def test_asset_url_shape():
    from rotortcpbridge.firmware_bundle import asset_url, pinned_components
    from rotortcpbridge.version import FW_GITHUB_ORG

    rotor = next(c for c in pinned_components() if c.key == "rotor")
    url = asset_url(rotor, rotor.assets[0])
    assert url.startswith(f"https://github.com/{FW_GITHUB_ORG}/{rotor.repo}/releases/download/v")
    assert rotor.assets[0] in url
