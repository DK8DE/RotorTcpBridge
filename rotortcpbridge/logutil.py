# Einfaches Log-System: schreibt in Datei + hält letzten N Zeilen für GUI
# Rotation: bei 20MB wird in rotortcpbridge_YYYY-MM-DD_HHMMSS.zip gepackt, max 10 Zips
from __future__ import annotations
import os
import zipfile
from pathlib import Path
from datetime import datetime
from collections import deque
from typing import Deque, Optional

APP_NAME = "RotorTcpBridge"
LOG_MAX_BYTES = 20 * 1024 * 1024  # 20 MB
LOG_ZIP_MAX_COUNT = 10


def appdata_dir() -> Path:
    base = os.getenv("APPDATA") or str(Path.home() / ".config")
    p = Path(base) / APP_NAME
    p.mkdir(parents=True, exist_ok=True)
    return p


def log_path() -> Path:
    return appdata_dir() / "rotortcpbridge.log"


def _zip_log_and_clear(log_p: Path) -> None:
    """Aktuelle Log-Datei zippen, danach leeren. Alte Zips löschen (max 10 behalten)."""
    if not log_p.exists() or log_p.stat().st_size == 0:
        return
    base = log_p.parent
    ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    zip_name = base / f"rotortcpbridge_{ts}.zip"
    try:
        with zipfile.ZipFile(zip_name, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(log_p, log_p.name)
        log_p.write_text("", encoding="utf-8")
    except Exception:
        return
    # Alte Zips löschen, nur die 10 neuesten behalten
    zips = sorted(base.glob("rotortcpbridge_*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in zips[LOG_ZIP_MAX_COUNT:]:
        try:
            old.unlink()
        except Exception:
            pass


class LogBuffer:
    def __init__(self, max_lines: int = 5000):
        self._buf: Deque[str] = deque(maxlen=max_lines)
        self._log_path = log_path()
        self._file = open(self._log_path, "a", encoding="utf-8", buffering=1)

    def close(self):
        try:
            self._file.close()
        except Exception:
            pass

    def _maybe_rotate(self) -> None:
        """Wenn Log >= 20MB: zippen, leeren, alte Zips entfernen."""
        try:
            size = self._file.tell()
            if size >= LOG_MAX_BYTES:
                self._file.close()
                _zip_log_and_clear(self._log_path)
                self._file = open(self._log_path, "a", encoding="utf-8", buffering=1)
        except Exception:
            pass

    def write(self, level: str, msg: str):
        self._maybe_rotate()
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"{ts} [{level}] {msg}"
        self._buf.append(line)
        try:
            self._file.write(line + "\n")
        except Exception:
            pass

    def lines(self):
        return list(self._buf)
