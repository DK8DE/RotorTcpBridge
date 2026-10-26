"""Prüft beim Start, ob auf GitHub eine neuere App-Version vorliegt (kein Download)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Optional, Tuple

from .firmware_compat_check import normalize_version, version_tuple
from .version import (
    APP_GITHUB_ORG,
    APP_GITHUB_REPO,
    APP_VERSION,
    DOWNLOAD_PAGE_URL,
)


def download_page_url() -> str:
    return str(DOWNLOAD_PAGE_URL).strip() or "https://dk8.de/de/download/"


def fetch_latest_github_release_tag(
    *,
    org: Optional[str] = None,
    repo: Optional[str] = None,
    timeout_s: float = 8.0,
) -> Optional[str]:
    """Neueste Release-Tag von GitHub (z. B. ``v2.6``) oder None bei Offline/Fehler."""
    owner = str(org or APP_GITHUB_ORG).strip() or APP_GITHUB_ORG
    name = str(repo or APP_GITHUB_REPO).strip() or APP_GITHUB_REPO
    url = f"https://api.github.com/repos/{owner}/{name}/releases/latest"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": f"RotorTcpBridge/{APP_VERSION}",
            "Accept": "application/vnd.github+json",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        data = json.loads(raw)
        if not isinstance(data, dict):
            return None
        tag = str(data.get("tag_name") or "").strip()
        return tag or None
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ValueError):
        return None
    except Exception:
        return None


def is_remote_newer(remote: Optional[str], local: Optional[str] = None) -> bool:
    """True wenn remote-Version strikt neuer als local (APP_VERSION)."""
    loc = local if local is not None else APP_VERSION
    tr, tl = version_tuple(remote), version_tuple(loc)
    if tr is None or tl is None:
        return False
    return tr > tl


def check_app_update_available(
    *,
    timeout_s: float = 8.0,
) -> Tuple[bool, Optional[str]]:
    """
    Returns ``(update_available, remote_version_normalized)``.
    Bei fehlender Verbindung: ``(False, None)`` — still schweigen.
    """
    tag = fetch_latest_github_release_tag(timeout_s=timeout_s)
    if not tag:
        return False, None
    remote = normalize_version(tag)
    if not remote:
        return False, None
    if is_remote_newer(remote, APP_VERSION):
        return True, remote
    return False, remote
