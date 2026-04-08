"""Internationalisierung (i18n) für RotorTcpBridge.

Verwendung:
    from .i18n import t, tt, load_lang
    load_lang("de")          # einmalig beim Start
    label = t("main.btn_compass")
    widget.setToolTip(tt("some.key_tooltip"))  # Zeilenumbruch max. TOOLTIP_WRAP_CHARS
"""

from __future__ import annotations

import json
import re
import textwrap
from html import escape as html_escape
from pathlib import Path

# Einheitliche Tooltip-Breite (Zeichen pro Zeile, nach Wörtern / bei Bedarf Worttrennung)
TOOLTIP_WRAP_CHARS = 35

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


def format_tooltip(text: str, width: int | None = None) -> str:
    """Text für Qt-setToolTip: manuelle Zeilenumbrüche aus Übersetzungen zusammenführen, dann umbrechen.

    Ergebnis ist Plaintext mit \\n zwischen den umgebrochenen Zeilen (Qt zeigt mehrzeilige Tooltips).
    """
    w = TOOLTIP_WRAP_CHARS if width is None else width
    if w <= 0:
        return str(text)
    s = str(text).replace("\r\n", "\n").replace("\r", "\n")
    s = " ".join(p.strip() for p in s.split("\n") if p.strip())
    s = re.sub(r"\s+", " ", s).strip()
    if not s:
        return ""
    lines = textwrap.wrap(s, width=w, break_long_words=True, break_on_hyphens=True)
    return "\n".join(lines)


def format_tooltip_html(
    text: str, width: int | None = None, max_width_px: int = 360
) -> str:
    """HTML-Tooltip (Qt); gleiche Logik wie format_tooltip, dann <br/> und Escaping."""
    plain = format_tooltip(text, width)
    if not plain:
        return ""
    e = "<br/>".join(html_escape(ln) for ln in plain.split("\n"))
    return f"<p style='max-width: {max_width_px}px;'>{e}</p>"


def tt(key: str, fallback: str | None = None, **kwargs: object) -> str:
    """Wie t(), aber für Widget-Tooltips mit globalem Zeilenumbruch (TOOLTIP_WRAP_CHARS)."""
    return format_tooltip(t(key, fallback, **kwargs))
