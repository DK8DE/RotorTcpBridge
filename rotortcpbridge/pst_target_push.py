"""Ausgehender UDP-Ziel-Push an PstRotator (Soll-Zeiger setzen).

Anwendungsfall:
    PstRotator ist als Master ueber SPID/BIG-RAS (TCP) mit dieser Bruecke als
    "Rotor" verbunden. Im SPID-Protokoll meldet der Rotor nur die *Ist*-Position
    zurueck; PstRotator zeichnet seinen Soll-Zeiger ausschliesslich aus dem Ziel,
    das es selbst kommandiert hat. Wird das Ziel dagegen in dieser Software (bzw.
    durch einen externen Controller am RS485-Bus) gesetzt, erfaehrt PstRotator es
    ueber die Rotor-Leitung nicht.

    Loesung: Sobald sich das Soll (``target_d10``) hier aendert, schicken wir es
    zusaetzlich an PstRotators UDP-Control-Eingang (Standard-Port 12000):

        <PST><AZIMUTH>173.0</AZIMUTH></PST>
        <PST><ELEVATION>25.0</ELEVATION></PST>

    PstRotator uebernimmt das als Ziel und setzt den Soll-Zeiger. Dieser Melder
    ist rein ausgehend und laeuft damit *parallel* zum SPID-TCP-Server (anders als
    der ``UdpPstRotator``-Emulator, der sich mit dem SPID-TCP-Server ausschliesst).

Hinweis zur Rueckkopplung:
    Empfaengt PstRotator das Ziel, kommandiert es (bei aktivem ON-Button) den Rotor
    per SPID auf denselben Wert zurueck. Da wir nur bei *Aenderung* des Ziels senden
    und der zurueckkommende Wert identisch ist, konvergiert das nach einem Schritt –
    es entsteht keine Endlosschleife.
"""

from __future__ import annotations

import socket
import time
from typing import Optional


def target_changed(new_d10: Optional[int], last_d10: Optional[int], min_delta_d10: int = 1) -> bool:
    """True, wenn ein neuer Ziel-Wert gemeldet werden soll.

    Args:
        new_d10: Aktuelles Ziel in Zehntelgrad (None = unbekannt → nicht senden).
        last_d10: Zuletzt gesendetes Ziel (None = noch nie gesendet → senden).
        min_delta_d10: Mindestaenderung in Zehntelgrad, ab der neu gesendet wird.
    """
    if new_d10 is None:
        return False
    if last_d10 is None:
        return True
    return abs(int(new_d10) - int(last_d10)) >= int(min_delta_d10)


class PstTargetPush:
    """Sendet Soll-Werte per UDP an PstRotators Control-Port (nur ausgehend)."""

    # Mindestabstand zwischen zwei Sende-Bursts, um kurze Ziel-Zappler zu buendeln.
    _MIN_SEND_INTERVAL_S = 0.1
    # Mindestaenderung (Zehntelgrad), ab der ein neues Ziel gemeldet wird.
    _MIN_DELTA_D10 = 1

    def __init__(self, controller, log, cfg: dict | None = None):
        self.ctrl = controller
        self.log = log
        self.cfg = cfg
        self._enabled = False
        self._host = "127.0.0.1"
        self._port = 12000
        self._sock: socket.socket | None = None
        self._last_az_d10: Optional[int] = None
        self._last_el_d10: Optional[int] = None
        self._last_send_ts: float = 0.0

    @property
    def is_active(self) -> bool:
        return bool(self._enabled and self._sock is not None)

    def start(self, enabled: bool, host: str | None = None, port: int = 12000) -> None:
        """Sender (neu) konfigurieren. Oeffnet einen UDP-TX-Socket, wenn aktiv."""
        self.stop()
        self._enabled = bool(enabled)
        self._host = (str(host or "").strip()) or "127.0.0.1"
        self._port = max(1, min(65535, int(port or 12000)))
        self._last_az_d10 = None
        self._last_el_d10 = None
        self._last_send_ts = 0.0
        if not self._enabled:
            return
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            except OSError:
                pass
            self.log.write(
                "INFO",
                f"PST-Ziel-Push aktiv → sende Soll an {self._host}:{self._port}",
            )
        except OSError as e:
            self._sock = None
            self.log.write("ERROR", f"PST-Ziel-Push: Socket-Fehler: {e}")

    def stop(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except Exception:
                pass
        self._sock = None
        if self._enabled:
            self.log.write("INFO", "PST-Ziel-Push gestoppt")
        self._enabled = False

    def notify_target(
        self,
        az_d10: Optional[int],
        el_d10: Optional[int],
        *,
        az_enabled: bool = True,
        el_enabled: bool = True,
        now: float | None = None,
    ) -> None:
        """Bei Ziel-Aenderung <PST><AZIMUTH>/<ELEVATION> an PstRotator senden."""
        if not self._enabled or self._sock is None:
            return
        now = time.time() if now is None else float(now)
        send_az = az_enabled and target_changed(az_d10, self._last_az_d10, self._MIN_DELTA_D10)
        send_el = el_enabled and target_changed(el_d10, self._last_el_d10, self._MIN_DELTA_D10)
        if not (send_az or send_el):
            return
        if (now - self._last_send_ts) < self._MIN_SEND_INTERVAL_S:
            return
        if send_az and az_d10 is not None:
            self._last_az_d10 = int(az_d10)
            self._send(f"<PST><AZIMUTH>{int(az_d10) / 10.0:.1f}</AZIMUTH></PST>")
        if send_el and el_d10 is not None:
            self._last_el_d10 = int(el_d10)
            self._send(f"<PST><ELEVATION>{int(el_d10) / 10.0:.1f}</ELEVATION></PST>")
        self._last_send_ts = now

    def _send(self, msg: str) -> None:
        if self._sock is None:
            return
        try:
            self._sock.sendto(msg.encode("ascii"), (self._host, self._port))
            self.log.write("UDP", f"PST-Ziel-Push → {self._host}:{self._port} {msg}")
        except Exception as e:
            self.log.write("WARN", f"PST-Ziel-Push Senden fehlgeschlagen: {e}")
