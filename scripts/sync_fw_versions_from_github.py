#!/usr/bin/env python3
"""Empfohlene Firmware-Versionen aus GitHub-Releases in version.py schreiben.

Holt die neuesten Release-Tags der Firmware-Repos (siehe ``FW_GITHUB_REPOS`` in
``rotortcpbridge/version.py``) und aktualisiert die Konstanten, wenn die Remote-
Version neuer ist.

Beispiele::

    python scripts/sync_fw_versions_from_github.py
    python scripts/sync_fw_versions_from_github.py --check   # Exit 1 wenn veraltet
    python scripts/sync_fw_versions_from_github.py --force   # Remote auch wenn älter

Vor einem Push (optional Pre-Push-Hook)::

    python scripts/sync_fw_versions_from_github.py
    git add rotortcpbridge/version.py && git commit -m "chore: sync firmware pins from GitHub"
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rotortcpbridge.app_update_check import fetch_latest_github_release_tag  # noqa: E402
from rotortcpbridge.firmware_compat_check import (  # noqa: E402
    normalize_version,
    version_tuple,
)
from rotortcpbridge.version import (  # noqa: E402
    APP_GITHUB_ORG,
    FW_GITHUB_REPOS,
)

VERSION_PY = ROOT / "rotortcpbridge" / "version.py"

_CONST_RE = re.compile(
    r"^(?P<name>ROTOR_FW_VERSION|CONTROLLER_FW_VERSION|INCLINOMETER_FW_VERSION|"
    r"WLAN_MODULE_FW_VERSION)\s*=\s*\"(?P<ver>[^\"]*)\"\s*$",
    re.MULTILINE,
)


def _read_local_versions(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for m in _CONST_RE.finditer(text):
        out[m.group("name")] = m.group("ver")
    return out


def _remote_newer(remote: str, local: str) -> bool:
    tr, tl = version_tuple(remote), version_tuple(local)
    if tr is None or tl is None:
        return False
    return tr > tl


def _apply_versions(text: str, updates: dict[str, str]) -> str:
    def repl(m: re.Match[str]) -> str:
        name = m.group("name")
        if name in updates:
            return f'{name} = "{updates[name]}"'
        return m.group(0)

    return _CONST_RE.sub(repl, text)


def sync(*, check_only: bool, force: bool, timeout_s: float) -> int:
    if not VERSION_PY.is_file():
        print(f"FEHLER: {VERSION_PY} nicht gefunden", file=sys.stderr)
        return 2

    text = VERSION_PY.read_text(encoding="utf-8")
    local = _read_local_versions(text)
    missing = [k for k in FW_GITHUB_REPOS if k not in local]
    if missing:
        print(f"FEHLER: Konstanten fehlen in version.py: {missing}", file=sys.stderr)
        return 2

    updates: dict[str, str] = {}
    stale: list[str] = []
    print(f"Org: {APP_GITHUB_ORG}")
    for const, repo in FW_GITHUB_REPOS.items():
        loc = local[const]
        tag = fetch_latest_github_release_tag(
            org=APP_GITHUB_ORG, repo=repo, timeout_s=timeout_s
        )
        if not tag:
            print(f"  {const}: {loc}  ← {repo}: kein Release (offline/Fehler)")
            continue
        rem = normalize_version(tag)
        if not rem:
            print(f"  {const}: {loc}  ← {repo}: Tag unlesbar ({tag!r})")
            continue
        if rem == loc:
            print(f"  {const}: {loc}  = {repo}@{tag} (aktuell)")
            continue
        if force or _remote_newer(rem, loc):
            print(f"  {const}: {loc}  → {rem}  ({repo}@{tag})")
            updates[const] = rem
            if _remote_newer(rem, loc):
                stale.append(const)
        else:
            print(
                f"  {const}: {loc}  > {rem}  ({repo}@{tag}) — lokal neuer, unverändert"
            )

    if check_only:
        if stale:
            print(
                f"\nCheck fehlgeschlagen: {len(stale)} Firmware-Pin(s) veraltet: "
                + ", ".join(stale)
            )
            return 1
        print("\nCheck OK — keine neueren GitHub-Releases.")
        return 0

    if not updates:
        print("\nKeine Änderungen an version.py.")
        return 0

    new_text = _apply_versions(text, updates)
    VERSION_PY.write_text(new_text, encoding="utf-8", newline="\n")
    print(f"\nAktualisiert: {VERSION_PY.relative_to(ROOT)}")
    print("Bitte committen, z. B.:")
    print('  git add rotortcpbridge/version.py')
    print('  git commit -m "chore: sync firmware pins from GitHub"')
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--check",
        action="store_true",
        help="Nur prüfen: Exit 1 wenn GitHub neuer als version.py",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Remote-Version auch übernehmen, wenn sie älter ist",
    )
    p.add_argument("--timeout", type=float, default=12.0, help="HTTP-Timeout (s)")
    args = p.parse_args(argv)
    return sync(check_only=bool(args.check), force=bool(args.force), timeout_s=float(args.timeout))


if __name__ == "__main__":
    raise SystemExit(main())
