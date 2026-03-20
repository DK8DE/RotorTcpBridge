"""Reine Logik für PST-UDP Positions-Push (ohne Socket) – testbar ohne Qt/Hardware."""
from __future__ import annotations


def pst_notify_position_decision(
    az_d10: int,
    last_sent_d10: int | None,
    zero_confirm: int,
    *,
    zero_confirm_ticks: int = 3,
) -> tuple[bool, int]:
    """Entscheidet, ob eine neue ``AZ:…``-Meldung gesendet werden soll.

    Args:
        az_d10: Aktuelle Position in Zehntelgrad.
        last_sent_d10: Zuletzt per UDP gemeldete Position (None = noch nie gesendet).
        zero_confirm: Laufender Zähler für aufeinanderfolgende 0-Samples (Debounce).
        zero_confirm_ticks: Mindestanzahl stabiler 0-Samples nach Nicht-Null-Position.

    Returns:
        ``(should_send, new_zero_confirm)``. Bei ``should_send`` ist der Aufrufer
        verpflichtet, ``last_sent_d10 = az_d10`` zu setzen; ``new_zero_confirm``
        ist immer der Zählerstand für den nächsten Aufruf.
    """
    zc = zero_confirm
    if az_d10 == 0 and last_sent_d10 is not None and last_sent_d10 != 0:
        zc = zero_confirm + 1
        if zc < zero_confirm_ticks:
            return (False, zc)
    else:
        zc = 0
    if last_sent_d10 is not None and abs(az_d10 - last_sent_d10) < 1:
        return (False, zc)
    return (True, zc)
