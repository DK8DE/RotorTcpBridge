"""UDP-Schnittstelle kompatibel zum PstRotatorAz-Protokoll.

Protokoll-Übersicht:
  Empfang  (auf listen_port, default 12000):
    <PST><AZIMUTH>85</AZIMUTH></PST>   → Rotor auf 85° fahren
    <PST><STOP>1</STOP></PST>          → Rotor stoppen
    <PST><PARK>1</PARK></PST>          → Rotor stoppen
    <PST>AZ?</PST>                     → aktuelle Position zurückschicken
    <PST>TGA?</PST>                    → Ziel-Azimut zurückschicken
    (alle anderen Felder werden geparst, aber ignoriert bzw. geloggt)

  Senden  (Ziel konfigurierbar; leer = Subnetz-Broadcast x.y.z.255 : listen_port + 1):
    AZ:xxx<CR>   bei Positionsänderung und auf Anfrage AZ?
    TGA:xxx<CR>  auf Anfrage TGA?
"""

from __future__ import annotations

import re
import socket
import threading
from .angle_utils import wrap_deg
from .net_utils import ipv4_subnet_broadcast_default, normalize_udp_bind_host
from .pst_notify_logic import pst_notify_position_decision

# Alle bekannten PST-Tags, die still ignoriert werden dürfen
_KNOWN_SILENT = {
    "TRACK",
    "ON",
    "ANT",
    "OFFSET1",
    "OFFSET2",
    "STF",
    "STR",
    "QRA",
    "MYQRA",
}

_RE_TAG = re.compile(r"<([^/][^>]*)>(.*?)</\1>", re.DOTALL)

# Mindestanzahl aufeinanderfolgender pos_d10==0-Samples, bevor AZ:0.0 gesendet wird,
# wenn zuvor eine andere Position gemeldet wurde (verhindert kurze Leseglitches).
_ZERO_CONFIRM_TICKS = 3


def _parse_ipv4_send_host(raw: str | None, log) -> str:
    """Gültige IPv4 für sendto; sonst 127.0.0.1 und Log.

    ``0.0.0.0`` ist unter Windows kein gültiges Ziel für ``sendto`` (WinError 10049),
    kommt aber vor, wenn es mit dem Lausch-Host verwechselt wurde.
    """
    s = (raw or "").strip() or "127.0.0.1"
    try:
        socket.inet_pton(socket.AF_INET, s)
    except OSError:
        log.write("WARN", f"UDP PST-Rotator: ungültige Ziel-IP {raw!r}, verwende 127.0.0.1")
        return "127.0.0.1"
    if s == "0.0.0.0":
        log.write(
            "WARN",
            "UDP PST-Rotator: Ziel-IP 0.0.0.0 ist kein gültiges sendto-Ziel (Windows) — "
            "verwende 127.0.0.1. Für Broadcast z. B. x.y.z.255 oder 255.255.255.255 eintragen.",
        )
        return "127.0.0.1"
    return s


class UdpPstRotator:
    """Emuliert die UDP-Schnittstelle von PstRotatorAz.

    Hört auf ``listen_port`` (Standard: 12000) und sendet Positionsmeldungen
    an ``send_host : listen_port + 1`` (nicht konfiguriert: Subnetz-Broadcast x.y.z.255).
    """

    def __init__(self, controller, log, cfg: dict | None = None):
        self.ctrl = controller
        self.log = log
        self.cfg = cfg
        self._enabled = False
        self._port = 12000
        self._send_host = "127.0.0.1"
        self._sock_rx: socket.socket | None = None
        self._sock_tx: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        # Zuletzt gesendete Position (in d10-Schritten), um Flooding zu vermeiden
        self._last_sent_d10: int | None = None
        # Aufeinanderfolgende Samples mit pos_d10==0 nach einer anderen Position (gegen Leseglitch)
        self._zero_confirm: int = 0
        # Wird auf True gesetzt wenn ein Steuerpaket eingeht → LED blinken
        self.packet_received_flag = False
        # Fehlermeldung wenn Port beim Start belegt war (None = kein Fehler)
        self.bind_error_msg: str | None = None

    # ------------------------------------------------------------------
    # Öffentliche API
    # ------------------------------------------------------------------

    @property
    def is_active(self) -> bool:
        return bool(self._enabled and self._running and self._sock_rx is not None)

    def start(
        self,
        enabled: bool,
        port: int = 12000,
        send_host: str | None = None,
        listen_host: str | None = None,
    ) -> None:
        """Listener starten oder mit neuer Konfiguration neu starten.

        listen_host: IPv4 für eingehende PST-UDP (Standard 0.0.0.0).

        send_host: IPv4 für ausgehende AZ:/TGA:-Datagramme (Port ist weiter port+1).
        Wenn None, wird ui.udp_pst_send_host aus cfg gelesen; leerer Wert →
        automatisch Subnetz-Broadcast (``ipv4_subnet_broadcast_default``).
        """
        self.stop()
        self.bind_error_msg = None
        self._enabled = bool(enabled)
        self._port = max(1, min(65534, int(port)))  # max 65534 weil port+1 noch frei sein muss
        bind_listen = normalize_udp_bind_host(listen_host, "0.0.0.0")
        if send_host is not None:
            raw = str(send_host).strip()
        else:
            raw = str((self.cfg or {}).get("ui", {}).get("udp_pst_send_host", "")).strip()
        if not raw:
            self._send_host = _parse_ipv4_send_host(ipv4_subnet_broadcast_default(), self.log)
        else:
            self._send_host = _parse_ipv4_send_host(raw, self.log)
        self._last_sent_d10 = None
        self._zero_confirm = 0
        if not self._enabled:
            return
        try:
            self._sock_rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock_rx.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._sock_rx.bind((bind_listen, self._port))
            self._sock_rx.settimeout(0.5)
            self._sock_tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            # Broadcast (255.255.255.255 oder Subnetz-x.x.x.255) erfordert explizit SO_BROADCAST
            try:
                self._sock_tx.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            except OSError:
                pass
            self._running = True
            self._thread = threading.Thread(target=self._loop, daemon=True, name="UdpPstRotator")
            self._thread.start()
            self.log.write(
                "INFO",
                f"UDP PST-Rotator Listener auf {bind_listen}:{self._port}, "
                f"Sende an {self._send_host}:{self._port + 1}",
            )
        except OSError as e:
            self._running = False
            self.bind_error_msg = f"UDP PST-Rotator: Port {self._port} ist bereits belegt.\n\n{e}"
            self.log.write(
                "ERROR", f"UDP PST-Rotator bind fehlgeschlagen auf Port {self._port}: {e}"
            )

    def stop(self) -> None:
        """Listener anhalten."""
        self._running = False
        for sock in (self._sock_rx, self._sock_tx):
            try:
                if sock:
                    sock.close()
            except Exception:
                pass
        self._sock_rx = None
        self._sock_tx = None
        if self._thread:
            self._thread.join(timeout=1.0)
        self._thread = None
        if self._enabled:
            self.log.write("INFO", "UDP PST-Rotator gestoppt")

    def notify_position(self, az_d10: int) -> None:
        """Vom Haupt-Tick aufgerufen wenn sich AZ-Position geändert hat.

        Sendet AZ:xxx<CR> an listen_port+1, wenn der Wert sich um mindestens
        1 d10-Schritt (= 0,1°) geändert hat.

        Einzelne pos_d10==0-Samples nach einer anderen Position werden ignoriert
        (typische Leseglitch), bis 0° mehrere Ticks stabil ist.
        """
        if not self._enabled or self._sock_tx is None:
            return
        send, self._zero_confirm = pst_notify_position_decision(
            az_d10,
            self._last_sent_d10,
            self._zero_confirm,
            zero_confirm_ticks=_ZERO_CONFIRM_TICKS,
        )
        if not send:
            return
        self._last_sent_d10 = az_d10
        az_deg = az_d10 / 10.0
        self._send_reply(f"AZ:{az_deg:.1f}\r")

    # ------------------------------------------------------------------
    # Internes
    # ------------------------------------------------------------------

    def _send_reply(self, msg: str) -> None:
        """Sendet eine Antwort-Nachricht an konfiguriertes Ziel : port+1."""
        if self._sock_tx is None:
            return
        try:
            self._sock_tx.sendto(msg.encode("ascii"), (self._send_host, self._port + 1))
        except Exception as e:
            self.log.write("WARN", f"UDP PST-Rotator Senden fehlgeschlagen: {e}")

    def _current_az_deg(self) -> float:
        """Gibt die aktuelle AZ-Position in Grad zurück.

        Kurzes pos_d10==0 nach einer anderen Position: wie notify_position nicht
        als 0° werten (AZ?-Antwort konsistent zur Positions-Push-Logik).
        """
        try:
            d10 = getattr(self.ctrl.az, "pos_d10", None)
            if d10 is not None:
                if (
                    d10 == 0
                    and self._last_sent_d10 is not None
                    and self._last_sent_d10 != 0
                    and self._zero_confirm < _ZERO_CONFIRM_TICKS
                ):
                    return self._last_sent_d10 / 10.0
                return d10 / 10.0
        except Exception as e:
            self.log.write("WARN", f"UDP PST-Rotator _current_az_deg: {e}")
        return 0.0

    def _target_az_deg(self) -> float:
        """Gibt das aktuelle AZ-Ziel in Grad zurück."""
        try:
            d10 = getattr(self.ctrl.az, "target_d10", None)
            if d10 is not None:
                return d10 / 10.0
        except Exception as e:
            self.log.write("WARN", f"UDP PST-Rotator _target_az_deg: {e}")
        return self._current_az_deg()

    def _loop(self) -> None:
        while self._running and self._sock_rx:
            try:
                data, addr = self._sock_rx.recvfrom(4096)
            except socket.timeout:
                continue
            except Exception:
                break
            if not data:
                continue
            try:
                self._handle_packet(data, addr)
            except Exception as e:
                self.log.write("WARN", f"UDP PST-Rotator Fehler beim Verarbeiten: {e}")

    def _handle_packet(self, data: bytes, addr: tuple) -> None:
        """Verarbeitet ein eingehendes UDP-Paket."""
        try:
            text = data.decode("utf-8", errors="replace").strip()
        except Exception:
            return

        sender = f"{addr[0]}:{addr[1]}" if addr else "?"
        self.packet_received_flag = True

        # Kurzabfragen ohne XML-Wrapper
        if text == "<PST>AZ?</PST>":
            az = self._current_az_deg()
            reply = f"AZ:{az:.1f}\r"
            self._send_reply(reply)
            self.log.write("UDP", f"PST AZ? von {sender} → {reply.strip()}")
            return

        if text == "<PST>TGA?</PST>":
            tga = self._target_az_deg()
            reply = f"TGA:{tga:.1f}\r"
            self._send_reply(reply)
            self.log.write("UDP", f"PST TGA? von {sender} → {reply.strip()}")
            return

        # Normales PST-XML: muss mit <PST> anfangen und mit </PST> enden
        if not (text.startswith("<PST>") and text.endswith("</PST>")):
            self.log.write("WARN", f"UDP PST-Rotator: ungültiges Paket von {sender}: {text[:80]}")
            return

        inner = text[5:-6].strip()  # <PST>…</PST> abschneiden

        # Mehrere Tags in einem Paket möglich
        for m in _RE_TAG.finditer(inner):
            tag = m.group(1).strip().upper()
            val = m.group(2).strip()
            self._handle_tag(tag, val, sender)

    def _handle_tag(self, tag: str, val: str, sender: str) -> None:
        """Verarbeitet einen einzelnen PST-Tag."""
        if tag == "AZIMUTH":
            try:
                az_deg = wrap_deg(float(val))
            except ValueError:
                self.log.write(
                    "WARN", f"UDP PST-Rotator: ungültiger AZIMUTH-Wert '{val}' von {sender}"
                )
                return
            self.log.write("UDP", f"PST AZIMUTH={az_deg:.1f}° von {sender} → setze Rotor")
            try:
                if getattr(self.ctrl, "enable_az", True):
                    self.ctrl.set_az_deg(az_deg, force=True)
            except Exception as e:
                self.log.write("WARN", f"UDP PST-Rotator set_az_deg: {e}")

        elif tag in ("STOP", "PARK"):
            self.log.write("UDP", f"PST {tag} von {sender} → SETPOS auf Ist-Position (statt STOP)")
            try:
                self.ctrl.hold_all_at_current_pos()
            except Exception as e:
                self.log.write("WARN", f"UDP PST-Rotator hold_all_at_current_pos: {e}")

        elif tag in _KNOWN_SILENT:
            self.log.write("UDP", f"PST {tag}={val} von {sender} (nicht implementiert, ignoriert)")

        else:
            self.log.write("UDP", f"PST unbekannter Tag {tag}={val} von {sender} (ignoriert)")
