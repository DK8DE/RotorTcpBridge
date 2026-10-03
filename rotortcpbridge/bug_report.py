"""Bug-Report: Logs, Profile und Rotor-Backup in ein ZIP packen."""

from __future__ import annotations

import json
import platform
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .logutil import appdata_dir, log_path
from .profile_store import profiles_dir
from .rotor_backup import extract_gui_config_for_backup, save_rotor_config_xml
from .version import APP_AUTHOR, APP_DATE, APP_NAME, APP_VERSION

# GUI-Keys, die der Bug-Report / Backup zwingend mitnehmen soll.
REQUIRED_GUI_BACKUP_KEYS = (
    "gs232_server",
    "easycomm_server",
    "dcu1_server",
    "n1mm_rotor",
    "map_webserver",
    "rotctld_server",
    "pst_server",
    "pst_serial",
)


def build_version_text() -> str:
    lines = [
        f"app={APP_NAME}",
        f"version={APP_VERSION}",
        f"date={APP_DATE}",
        f"author={APP_AUTHOR}",
        f"python={sys.version.replace(chr(10), ' ')}",
        f"platform={platform.platform()}",
        f"machine={platform.machine()}",
        f"created={datetime.now().isoformat(timespec='seconds')}",
    ]
    return "\n".join(lines) + "\n"


def iter_log_artifacts() -> List[Path]:
    """Aktuelle Logdatei + rotierte Log-ZIPs."""
    out: List[Path] = []
    lp = log_path()
    if lp.is_file():
        out.append(lp)
    base = appdata_dir()
    for z in sorted(base.glob("rotortcpbridge_*.zip"), key=lambda p: p.stat().st_mtime):
        out.append(z)
    return out


def iter_profile_artifacts() -> List[Path]:
    """profiles.json + alle Profil-JSON-Dateien."""
    out: List[Path] = []
    pdir = profiles_dir()
    if not pdir.is_dir():
        return out
    idx = pdir / "profiles.json"
    if idx.is_file():
        out.append(idx)
    for p in sorted(pdir.glob("*.json")):
        if p.name == "profiles.json":
            continue
        out.append(p)
    return out


def write_rotor_backup_xml(
    path: Path,
    cfg: Dict[str, Any],
    hw_entries: Optional[List[dict]] = None,
) -> Dict[str, Any]:
    """Rotor-Backup-XML mit GUI-Config (inkl. neuer Protokoll-Keys) schreiben."""
    gui = extract_gui_config_for_backup(cfg)
    save_rotor_config_xml(path, list(hw_entries or []), gui_config=gui)
    return gui


def gui_backup_has_required_keys(gui: Dict[str, Any]) -> List[str]:
    """Fehlende REQUIRED_GUI_BACKUP_KEYS zurückgeben (leer = alles da)."""
    return [k for k in REQUIRED_GUI_BACKUP_KEYS if k not in gui]


def create_bug_report_zip(
    zip_path: Path,
    *,
    cfg: Dict[str, Any],
    hw_entries: Optional[List[dict]] = None,
    notes: str = "",
) -> Path:
    """Bug-Report-ZIP erzeugen. ``zip_path`` Endziel."""
    zip_path = Path(zip_path)
    zip_path.parent.mkdir(parents=True, exist_ok=True)

    import tempfile

    with tempfile.TemporaryDirectory(prefix="rtb-bug-") as tmp:
        tmp_p = Path(tmp)
        (tmp_p / "version.txt").write_text(build_version_text(), encoding="utf-8")
        readme_parts = [
            "RotorTcpBridge Bug Report",
            "",
            "Inhalt:",
            "- version.txt",
            "- logs/ … aktuelle und rotierte Logs",
            "- profiles/ … Profilindex und Profil-JSONs",
            "- rotor_backup.xml … Hardware-Parameter + gui_config",
            "",
        ]
        if notes:
            readme_parts.extend(["Hinweise:", notes, ""])
        (tmp_p / "README.txt").write_text("\n".join(readme_parts), encoding="utf-8")

        logs_dir = tmp_p / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        for src in iter_log_artifacts():
            try:
                dest = logs_dir / src.name
                dest.write_bytes(src.read_bytes())
            except Exception:
                pass

        prof_dir = tmp_p / "profiles"
        prof_dir.mkdir(parents=True, exist_ok=True)
        for src in iter_profile_artifacts():
            try:
                dest = prof_dir / src.name
                dest.write_bytes(src.read_bytes())
            except Exception:
                pass

        backup_xml = tmp_p / "rotor_backup.xml"
        gui = write_rotor_backup_xml(backup_xml, cfg, hw_entries)
        missing = gui_backup_has_required_keys(gui)
        if missing:
            (tmp_p / "gui_keys_missing.txt").write_text(
                "Missing GUI keys in backup extract:\n" + "\n".join(missing) + "\n",
                encoding="utf-8",
            )

        # Aktive Config zusätzlich als JSON
        try:
            (tmp_p / "active_config.json").write_text(
                json.dumps(cfg, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in tmp_p.rglob("*"):
                if f.is_file():
                    zf.write(f, arcname=str(f.relative_to(tmp_p)).replace("\\", "/"))

    return zip_path
