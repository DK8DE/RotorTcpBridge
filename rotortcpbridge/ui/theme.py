"""Theme- und Dark-Mode-Logik."""

from __future__ import annotations

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QPalette, QColor


def build_dark_palette() -> QPalette:
    """Erzeugt eine dunkle Qt-Palette für den Fusion-Style."""
    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window, QColor(28, 28, 28))
    pal.setColor(QPalette.ColorRole.WindowText, QColor(225, 225, 225))
    pal.setColor(QPalette.ColorRole.Base, QColor("#2d2d2d"))
    pal.setColor(QPalette.ColorRole.AlternateBase, QColor(32, 32, 32))
    pal.setColor(QPalette.ColorRole.ToolTipBase, QColor(36, 36, 36))
    pal.setColor(QPalette.ColorRole.ToolTipText, QColor(225, 225, 225))
    pal.setColor(QPalette.ColorRole.Text, QColor(225, 225, 225))
    pal.setColor(QPalette.ColorRole.Button, QColor(38, 38, 38))
    pal.setColor(QPalette.ColorRole.ButtonText, QColor(225, 225, 225))
    pal.setColor(QPalette.ColorRole.BrightText, QColor(255, 80, 80))
    pal.setColor(QPalette.ColorRole.Link, QColor(68, 150, 235))
    pal.setColor(QPalette.ColorRole.Highlight, QColor(68, 150, 235))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor(18, 18, 18))
    return pal


def apply_theme_mode(cfg: dict) -> None:
    """Wendet Dark-Mode an, falls force_dark_mode aktiv ist."""
    app_obj = QApplication.instance()
    if not isinstance(app_obj, QApplication):
        return
    app = app_obj
    ui_cfg = cfg.setdefault("ui", {})
    force_dark = bool(ui_cfg.get("force_dark_mode", True))
    if not hasattr(app, "_rtb_default_palette"):
        setattr(app, "_rtb_default_palette", QPalette(app.palette()))
    if force_dark:
        app.setStyle("Fusion")
        app.setPalette(build_dark_palette())
    else:
        base_pal = getattr(app, "_rtb_default_palette", None)
        if isinstance(base_pal, QPalette):
            app.setPalette(base_pal)
