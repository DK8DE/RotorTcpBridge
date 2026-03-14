"""Gemeinsame UI-Hilfsfunktionen (DPI-Skalierung etc.)."""
from __future__ import annotations

from PySide6.QtWidgets import QWidget
from PySide6.QtGui import QGuiApplication


def scale_factor_for(widget: QWidget) -> float:
    """Ermittelt Windows/Qt Scale-Faktor (DPI-Skalierung)."""
    screen = None
    try:
        wh = widget.windowHandle()
        if wh:
            screen = wh.screen()
    except Exception:
        screen = None

    if screen is None:
        try:
            screen = QGuiApplication.primaryScreen()
        except Exception:
            screen = None

    if screen is None:
        return 1.0

    try:
        dpi = float(screen.logicalDotsPerInch())
    except Exception:
        dpi = 96.0

    if dpi <= 1.0:
        return 1.0
    return dpi / 96.0


def px_to_dip(widget: QWidget, px: int) -> int:
    """Konvertiert px (Bildschirmpixel) in DIP für das Widget."""
    sf = scale_factor_for(widget)
    return int(round(float(px) / sf))
