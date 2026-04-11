"""Lokalisierte Rotor-Fehlertexte für Popups (de/en).

Die Texte beschreiben Ursache und Prüfschritte am Rotor; Homing/SETREF wird nicht
mehr als zuverlässige Quittierung während eines angezeigten Fehlers dargestellt.
"""

from __future__ import annotations

from .i18n import t
from .rotor_model import ERROR_DETAILS_DOC, ERROR_DETAILS_LEGACY


def _assemble(name: str, cause: str, check: str, footer_key: str) -> tuple[str, str]:
    body = (
        f"{t('popup.error_label_cause')}: {cause}\n"
        f"{t('popup.error_label_check')}: {check}\n\n"
        f"{t(footer_key)}"
    )
    return name, body


def error_popup_text(code: int) -> tuple[str, str]:
    """Fehlercode -> (Name, Fließtext) für QMessageBox und Status."""
    try:
        c = int(code)
    except Exception:
        c = 0

    doc = ERROR_DETAILS_DOC.get(c)
    legacy = ERROR_DETAILS_LEGACY.get(c)
    # Explizit (nicht über bool(...)): sonst narrowt Pyright doc/legacy im Block nicht.
    if doc is not None and legacy is not None and doc[0] != legacy[0]:
        name = t(f"popup.err.m{c}.name", fallback=doc[0])
        body = t(f"popup.err.m{c}.body", fallback="")
        if not body.strip():
            name = f"{doc[0]} (alt: {legacy[0]})"
            body = (
                f"{t('popup.error_label_cause')}: {doc[1]}\n"
                f"{t('popup.error_label_check')}: {doc[2]}\n\n"
                f"{t('popup.error_label_legacy')}: {legacy[1]}\n"
                f"{t('popup.error_label_check')}: {legacy[2]}\n\n"
                f"{t('popup.error_footer_restart')}"
            )
        return name, body

    if doc:
        name = t(f"popup.err.d{c}.name", fallback=doc[0])
        cause = t(f"popup.err.d{c}.cause", fallback=doc[1])
        check = t(f"popup.err.d{c}.check", fallback=doc[2])
        if c == 3:
            footer_key = "popup.error_footer_not_homed"
        elif c == 14:
            footer_key = "popup.error_footer_deadman"
        else:
            footer_key = "popup.error_footer_restart"
        return _assemble(name, cause, check, footer_key)

    if legacy:
        name = t(f"popup.err.l{c}.name", fallback=legacy[0])
        cause = t(f"popup.err.l{c}.cause", fallback=legacy[1])
        check = t(f"popup.err.l{c}.check", fallback=legacy[2])
        return _assemble(name, cause, check, "popup.error_footer_restart")

    name = t("popup.err.unknown.name", fallback="SE_UNKNOWN")
    cause = t("popup.err.unknown.cause", fallback="Unbekannt")
    check = t("popup.err.unknown.check", fallback="-")
    return _assemble(name, cause, check, "popup.error_footer_restart")
