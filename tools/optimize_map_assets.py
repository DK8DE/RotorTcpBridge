#!/usr/bin/env python3
"""PNG-Assets verkleinern (Karten-Icons, optional Installer/airplane).

Benötigt: pip install Pillow

Karten-Icons (Antenne, User, User_ACC): max. Kantenlänge 96 px — reicht für ~30 px Anzeige inkl. Retina.
Installer-Bilder: nur neu komprimieren (Abmessungen für Inno Setup unverändert).

Ausführen aus dem Projektroot: python tools/optimize_map_assets.py
"""

from __future__ import annotations

import sys
from pathlib import Path

try:
    from PIL import Image
except ImportError as e:
    print("Pillow fehlt: pip install Pillow", file=sys.stderr)
    raise SystemExit(1) from e

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "rotortcpbridge"

MAP_ICON_MAX = 96
AIRPLANE_MAX = 128


def _shrink_rgba(path: Path, max_edge: int) -> tuple[int, int]:
    before = path.read_bytes()
    im = Image.open(path).convert("RGBA")
    w, h = im.size
    if max(w, h) > max_edge:
        r = max_edge / max(w, h)
        im = im.resize((int(w * r), int(h * r)), Image.Resampling.LANCZOS)
    im.save(path, format="PNG", optimize=True, compress_level=9)
    after = path.stat().st_size
    return len(before), after


def _recompress_only(path: Path) -> tuple[int, int]:
    before = path.read_bytes()
    im = Image.open(path).convert("RGBA")
    im.save(path, format="PNG", optimize=True, compress_level=9)
    after = path.stat().st_size
    return len(before), after


def main() -> None:
    print("Karten-Icons (max %d px):" % MAP_ICON_MAX)
    for name in ("Antenne.png", "Antenne_T.png", "User.PNG", "User_ACC.png"):
        p = PKG / name
        if not p.is_file():
            print(f"  überspringe (fehlt): {name}")
            continue
        b, a = _shrink_rgba(p, MAP_ICON_MAX)
        print(f"  {name}: {b // 1024} KB -> {a // 1024} KB")

    ap = PKG / "airplane.PNG"
    if ap.is_file():
        b, a = _shrink_rgba(ap, AIRPLANE_MAX)
        print(f"airplane.PNG (max {AIRPLANE_MAX} px): {b // 1024} KB -> {a // 1024} KB")

    print("Installer (nur Kompression, Groesse gleich):")
    for name in ("Installer.png", "InstallerSmall.png"):
        p = ROOT / name
        if not p.is_file():
            continue
        b, a = _recompress_only(p)
        print(f"  {name}: {b // 1024} KB -> {a // 1024} KB")


if __name__ == "__main__":
    main()
