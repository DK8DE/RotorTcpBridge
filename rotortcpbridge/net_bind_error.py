"""Einheitliche Meldungen bei Socket-Bind-Fehlern (TCP/UDP)."""

from __future__ import annotations


def format_bind_error(
    host: str,
    port: int,
    exc: BaseException,
    *,
    proto_name: str = "",
) -> str:
    """Menschenlesbare Bind-Fehlermeldung (EADDRINUSE / EACCES / sonst)."""
    errno = getattr(exc, "errno", None)
    h = str(host or "?").strip() or "?"
    try:
        p = int(port)
    except Exception:
        p = port
    prefix = f"{proto_name}: " if proto_name else ""
    if errno in (98, 10048, 48):  # EADDRINUSE Linux / Windows / macOS
        msg = (
            f"{prefix}Port {p} auf {h} ist bereits belegt. "
            f"Anderen Port wählen oder den blockierenden Dienst beenden."
        )
        if p == 12040:
            msg += (
                "\n\nHinweis: N1MM Rotor UDP und UcxLog nutzen beide Port 12040 "
                "— nur einer darf aktiv sein."
            )
        return msg
    if errno in (13, 10013, 1):  # EACCES / permission
        return (
            f"{prefix}Keine Berechtigung für {h}:{p} "
            f"(unter Windows benötigt Port 80 oft Administratorrechte)."
        )
    return f"{prefix}Konnte {h}:{p} nicht binden: {exc}"
