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


def ipv4_subnet_broadcast_default() -> str:
    """Best-effort-Broadcast-Adresse für das lokale IPv4-Subnetz (meist /24: x.y.z.255).

    Ermittelt die primäre lokale IPv4 über das ausgehende UDP-Interface (connect zu
    öffentlicher IP ohne Datenversand). Ohne nutzbares Netzwerk oder bei Loopback
    nur 127.x → ``127.0.0.1`` (nur dieser Rechner).

    Hinweis: Streng genommen hängt die echte Broadcast-Adresse vom Präfix (/24, /23, …)
    ab; für typische Heimnetze ist x.y.z.255 üblich.
    """
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.35)
        try:
            s.connect(("8.8.8.8", 80))
        except OSError:
            try:
                s.connect(("1.1.1.1", 80))
            except OSError:
                try:
                    s.close()
                except OSError:
                    pass
                return "127.0.0.1"
        local_ip = s.getsockname()[0]
        try:
            s.close()
        except OSError:
            pass
    except OSError:
        return "127.0.0.1"

    if local_ip.startswith("127."):
        return "127.0.0.1"

    parts = local_ip.split(".")
    if len(parts) != 4:
        return "127.0.0.1"
    try:
        socket.inet_pton(socket.AF_INET, local_ip)
    except OSError:
        return "127.0.0.1"

    # Typisches Class-C-/24-Heimnetz: Hostanteil → 255
    return f"{parts[0]}.{parts[1]}.{parts[2]}.255"


def normalize_udp_bind_host(raw: str | None, default: str) -> str:
    """IPv4-Adresse für ``socket.bind``; leer oder ungültig → ``default``."""
    s = (raw or "").strip()
    if not s:
        return default
    try:
        socket.inet_pton(socket.AF_INET, s)
        return s
    except OSError:
        return default
