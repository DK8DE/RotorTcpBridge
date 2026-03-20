"""Netzwerk-Utilities für RotorTcpBridge."""

from __future__ import annotations

import ctypes
import platform
import socket
import urllib.error
import urllib.request


def check_internet(timeout: float = 2.0) -> bool:
    """Prüft ob echte Internetverbindung besteht.

    Windows: InternetGetConnectedState als schneller Vor-Check.
    Gibt sofort False zurück wenn kein Netzwerkadapter aktiv ist.

    HTTP-Prüfung: Jede HTTP-Antwort (auch Fehler wie 404) bedeutet online.
    Nur Verbindungsfehler/Timeout bedeuten offline.
    """
    if platform.system().lower() == "windows":
        try:
            flags = ctypes.c_ulong(0)
            connected = ctypes.windll.wininet.InternetGetConnectedState(ctypes.byref(flags), 0)
            if not connected:
                return False  # Kein Netzwerkadapter aktiv → sofort False
        except Exception:
            pass

    # HTTP-Check: Jede Antwort vom Server = online (auch 404, 403 etc.)
    for url in (
        "http://www.msftconnecttest.com/connecttest.txt",
        "http://connectivitycheck.gstatic.com/generate_204",
    ):
        try:
            urllib.request.urlopen(url, timeout=timeout)
            return True
        except urllib.error.HTTPError:
            return True  # HTTP-Fehlerantwort erhalten = trotzdem online
        except Exception:
            pass

    # TCP-Fallback port 443 (HTTPS) – fast nie durch Firewalls geblockt
    for host in ("8.8.8.8", "1.1.1.1"):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(timeout)
            s.connect((host, 443))
            s.close()
            return True
        except OSError:
            pass

    return False
