"""TLS-Hilfen für Map-Webserver (Self-Signed)."""

from __future__ import annotations

from pathlib import Path

from rotortcpbridge.map_webserver import ensure_map_webserver_tls_files, map_webserver_tls_dir


def test_packaged_certs_exist():
    pkg = Path(__file__).resolve().parents[1] / "rotortcpbridge" / "certs"
    assert (pkg / "map_webserver.crt").is_file()
    assert (pkg / "map_webserver.key").is_file()


def test_ensure_tls_files_copies_to_appdata(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    crt, key = ensure_map_webserver_tls_files()
    assert crt.is_file() and key.is_file()
    assert crt.parent == map_webserver_tls_dir()
    # Zweiter Aufruf nutzt vorhandene Dateien
    crt2, key2 = ensure_map_webserver_tls_files()
    assert crt2 == crt and key2 == key
