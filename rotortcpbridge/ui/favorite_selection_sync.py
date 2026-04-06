"""Gemeinsame gespeicherte Favoriten-Zeile (Kompass- und Karten-Dropdown)."""

from __future__ import annotations

from typing import Any

CFG_KEY = "compass_favorite_selected"


def _fav_eq(a: dict, b: dict) -> bool:
    try:
        return (
            str(a.get("name", "")) == str(b.get("name", ""))
            and abs(float(a.get("az", 0)) - float(b.get("az", 0))) < 0.02
            and abs(float(a.get("el", 0)) - float(b.get("el", 0))) < 0.02
        )
    except (TypeError, ValueError):
        return False


def persist_favorite_selection(cfg: dict, fav: dict) -> None:
    """Nach Auswahl eines gespeicherten Ziels: für Karte/Kompass synchron halten."""
    ui = cfg.setdefault("ui", {})
    ui[CFG_KEY] = {
        "name": str(fav.get("name", ""))[:15],
        "az": float(fav.get("az", 0.0)),
        "el": float(fav.get("el", 0.0)),
    }


def clear_selection_if_favorite_removed(cfg: dict, removed: dict) -> None:
    """Nach Löschen eines Favoriten: Auswahl nur entfernen wenn genau dieser Eintrag aktiv war."""
    ui = cfg.get("ui") or {}
    sel = ui.get(CFG_KEY)
    if isinstance(sel, dict) and _fav_eq(sel, removed):
        ui.pop(CFG_KEY, None)


def apply_saved_selection_to_favorites_combo(cb: Any, cfg: dict) -> None:
    """Nach refill des Favoriten-Combos: Index aus cfg setzen (blockSignals sollte aktiv sein)."""
    ui = cfg.get("ui") or {}
    sel = ui.get(CFG_KEY)
    if not isinstance(sel, dict):
        return
    for i in range(cb.count()):
        data = cb.itemData(i)
        if isinstance(data, dict) and _fav_eq(data, sel):
            cb.setCurrentIndex(i)
            return
