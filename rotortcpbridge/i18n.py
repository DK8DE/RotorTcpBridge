"""Internationalisierung (i18n) für RotorTcpBridge.

Verwendung:
    from .i18n import t, load_lang
    load_lang("de")          # einmalig beim Start
    label = t("main.btn_compass")
"""
from __future__ import annotations

import json
from pathlib import Path

_strings: dict[str, str] = {}
LANG_CODE: str = "de"

_LOCALES_DIR = Path(__file__).parent / "locales"


def load_lang(code: str) -> None:
    """Sprachstrings aus JSON-Datei laden. Fehlende Datei → Fallback auf 'de'."""
    global _strings, LANG_CODE
    code = str(code or "de").strip().lower()
    path = _LOCALES_DIR / f"{code}.json"
    if not path.exists():
        code = "de"
        path = _LOCALES_DIR / "de.json"
    try:
        with open(path, encoding="utf-8") as f:
            _strings = json.load(f)
        LANG_CODE = code
    except Exception:
        _strings = {}
        LANG_CODE = code


def t(key: str, fallback: str | None = None, **kwargs: object) -> str:
    """Übersetzten String zurückgeben. Fallback: fallback-Argument oder Key selbst. Platzhalter via kwargs."""
    default = fallback if fallback is not None else key
    text = _strings.get(key, default)
    if kwargs:
        try:
            text = text.format(**kwargs)
        except (KeyError, ValueError):
            pass
    return text
