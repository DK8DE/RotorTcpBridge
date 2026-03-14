from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtGui import QIcon

_CACHED_ICON: Optional[QIcon] = None


def get_app_icon() -> QIcon:
    """Liefert das Programm-Icon (`rotor.ico`) als QIcon.

    Wird für alle Fenster verwendet (MainWindow + Dialoge).
    """
    global _CACHED_ICON
    if _CACHED_ICON is not None:
        return _CACHED_ICON

    ico_path = Path(__file__).resolve().parent / "rotor.ico"
    if ico_path.exists():
        _CACHED_ICON = QIcon(str(ico_path))
    else:
        _CACHED_ICON = QIcon()
    return _CACHED_ICON

