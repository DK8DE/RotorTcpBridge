"""Hilfsfunktionen für Rig-Bridge."""

from __future__ import annotations

import socket
import time
from datetime import datetime


def now_ts() -> float:
    """Aktuellen Unix-Zeitstempel liefern."""
    return time.time()


def fmt_ts(ts: float | None) -> str:
    """Zeitstempel benutzerfreundlich formatieren."""
    if not ts:
        return "-"
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return "-"


def bind_tcp_listen_socket(host: str, port: int) -> socket.socket:
    """TCP-Server-Socket anlegen und binden.

    Wichtig unter Windows: Viele Clients (z. B. WSJT-X/libhamlib) verbinden zu
    ``localhost`` zuerst per IPv6 (::1). Bindet man nur auf 127.0.0.1, schlägt
    die Verbindung mit „Verbindung verweigert“ fehl. Deshalb: für Loopback-
    und Default-Host-Fälle IPv6-Dual-Stack auf ``::`` mit IPV6_V6ONLY=0 nutzen.
    """
    port_i = int(port)
    h = (host or "").strip().lower()
    use_dual_stack = h in ("", "localhost", "127.0.0.1", "::1", "0.0.0.0")

    if use_dual_stack and hasattr(socket, "AF_INET6"):
        try:
            s = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
            s.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("::", port_i))
            return s
        except OSError:
            try:
                s.close()
            except Exception:
                pass

    bind_host = (host or "").strip() or "127.0.0.1"
    if bind_host.lower() in ("localhost", "::1"):
        bind_host = "127.0.0.1"
    if bind_host.lower() == "0.0.0.0":
        bind_host = "0.0.0.0"

    s4 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s4.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s4.bind((bind_host, port_i))
    return s4
