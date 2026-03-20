
from __future__ import annotations
import time
import zipfile
from pathlib import Path
from .app_config import appdata_dir, config_path
from .logutil import log_path

def backups_dir()->Path:
    p = appdata_dir() / "backups"
    p.mkdir(parents=True, exist_ok=True)
    return p

def make_runtime_backup():
    """Sichert Config + Log in eine ZIP. (leichtgewichtig, alle 3 Minuten)"""
    out = backups_dir() / f"runtime-backup-{int(time.time())}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        cp = config_path()
        lp = log_path()
        if cp.exists():
            z.write(cp, cp.name)
        if lp.exists():
            z.write(lp, lp.name)
    return out

def make_source_backup(project_root:Path):
    """Sichert den Quellcode (Projektordner) in eine ZIP."""
    out = backups_dir() / f"source-backup-{int(time.time())}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for p in project_root.rglob("*"):
            if "backups" in p.parts:
                continue
            if p.is_file():
                z.write(p, p.relative_to(project_root))
    return out
