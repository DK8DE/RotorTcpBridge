"""Tests für App-Update-Check (GitHub, ohne Download)."""

from rotortcpbridge.app_update_check import is_remote_newer, download_page_url
from rotortcpbridge.version import DOWNLOAD_PAGE_URL


def test_download_page_url():
    assert download_page_url() == DOWNLOAD_PAGE_URL
    assert "dk8.de" in download_page_url()


def test_is_remote_newer():
    assert is_remote_newer("2.6", "2.5") is True
    assert is_remote_newer("v2.6.0", "2.5") is True
    assert is_remote_newer("2.5", "2.5") is False
    assert is_remote_newer("2.4", "2.5") is False
    assert is_remote_newer("", "2.5") is False
