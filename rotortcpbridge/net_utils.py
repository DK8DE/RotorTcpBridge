"""Netzwerk-Utilities für RotorTcpBridge."""

from __future__ import annotations

import ctypes
import platform
import socket
import threading
import time
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


# -----------------------------------------------------------------------------
# Lokale IPv4-Erkennung für UDP-Quelladressfilter
# -----------------------------------------------------------------------------

# Cache, damit wir nicht bei jedem UDP-Paket DNS/Adapter abfragen.
# Windows-Adapter können sich im Betrieb ändern (WLAN wechselt, VPN verbindet),
# deshalb ist die TTL bewusst kurz, aber hoch genug, dass ein dichter UDP-Strom
# keinen Overhead verursacht.
_LOCAL_IPS_LOCK = threading.Lock()
_LOCAL_IPS_CACHE: set[str] = set()
_LOCAL_IPS_CACHE_MONO: float = 0.0
_LOCAL_IPS_TTL_S: float = 10.0


def _enumerate_local_ipv4() -> set[str]:
    """Ermittelt alle IPv4-Adressen der lokalen Netzwerkadapter (ohne Cache).

    Kombiniert mehrere Quellen, damit auch Hosts mit mehreren Adaptern (WLAN +
    LAN + VPN + Hyper-V …) zuverlässig alle ihre Adressen sehen:

    - ``getaddrinfo(hostname)`` — meist die primäre Adresse.
    - ``gethostbyname_ex(hostname)`` — liefert auf Windows typ. alle Adapter.
    - Ein UDP-``connect`` zu einer öffentlichen IP, um die „ausgehende" IP zu
      ermitteln (nützlich wenn der Hostname nicht alle Adapter nennt).

    Loopback (``127.0.0.1``) ist immer enthalten.
    """
    ips: set[str] = {"127.0.0.1"}

    try:
        hostname = socket.gethostname()
    except OSError:
        hostname = ""

    if hostname:
        try:
            for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
                addr = info[4][0]
                if addr:
                    ips.add(addr)
        except socket.gaierror:
            pass
        try:
            _name, _aliases, addr_list = socket.gethostbyname_ex(hostname)
            for a in addr_list:
                if a:
                    ips.add(a)
        except OSError:
            pass

    for probe in (("8.8.8.8", 80), ("1.1.1.1", 80)):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(0.25)
            try:
                s.connect(probe)
                ips.add(s.getsockname()[0])
            finally:
                try:
                    s.close()
                except OSError:
                    pass
        except OSError:
            pass

    return ips


def get_local_ipv4_addresses(force_refresh: bool = False) -> set[str]:
    """Gibt alle IPv4-Adressen zurück, die zu diesem Rechner gehören.

    Ergebnis ist kurz gecacht (``_LOCAL_IPS_TTL_S``). Loopback ist immer dabei.
    """
    global _LOCAL_IPS_CACHE, _LOCAL_IPS_CACHE_MONO
    now = time.monotonic()
    with _LOCAL_IPS_LOCK:
        if (
            not force_refresh
            and _LOCAL_IPS_CACHE
            and (now - _LOCAL_IPS_CACHE_MONO) < _LOCAL_IPS_TTL_S
        ):
            return set(_LOCAL_IPS_CACHE)
    fresh = _enumerate_local_ipv4()
    with _LOCAL_IPS_LOCK:
        _LOCAL_IPS_CACHE = fresh
        _LOCAL_IPS_CACHE_MONO = now
        return set(fresh)


def is_local_ipv4(ip: str | None) -> bool:
    """True, wenn ``ip`` zu diesem Rechner gehört (Loopback oder lokaler Adapter).

    - Alles aus ``127.0.0.0/8`` (Loopback) → True.
    - Jede Adresse, die ein lokaler Netzwerkadapter aktuell trägt → True.
    - Unbekannt / ungültig / Fremdadresse → False.

    Die Liste der Adapter-IPs wird gecacht; wenn die Antwort beim ersten Check
    False ist, wird ein Refresh erzwungen, damit wir bei Adapterwechseln
    (z. B. frisch verbundenes VPN, neuer WLAN-Lease) nicht bis zum nächsten
    TTL-Ablauf Pakete verwerfen, die doch lokal sind.
    """
    if not ip:
        return False
    s = ip.strip()
    if not s:
        return False
    try:
        socket.inet_pton(socket.AF_INET, s)
    except OSError:
        return False
    if s.startswith("127."):
        return True
    if s in get_local_ipv4_addresses():
        return True
    return s in get_local_ipv4_addresses(force_refresh=True)
