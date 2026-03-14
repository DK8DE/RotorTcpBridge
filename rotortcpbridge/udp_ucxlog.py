"""UDP-Listener für UcxLog-Positionsdaten.

UcxLog sendet bei Klick auf 'Setze Rotor' eine UDP-Nachricht an 127.0.0.1:12040:
<?xml version="1.0" encoding="utf-8"?>
<Rotor>
  <app>UcxLog</app>
  <Azimut>306</Azimut>
</Rotor>
"""
from __future__ import annotations

import socket
import threading
import xml.etree.ElementTree as ET

from .angle_utils import wrap_deg


class UdpUcxLogListener:
    """Hört auf UDP-Port 12040 und setzt den Rotor gemäß Azimut aus UcxLog-XML."""

    def __init__(self, controller, log):
        self.ctrl = controller
        self.log = log
        self._enabled = False
        self._port = 12040
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._running = False

    def start(self, enabled: bool, port: int = 12040) -> None:
        """Listener starten oder mit neuer Konfiguration neu starten."""
        self.stop()
        self._enabled = bool(enabled)
        self._port = max(1, min(65535, int(port)))
        if not self._enabled:
            return
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._sock.bind(("127.0.0.1", self._port))
            self._sock.settimeout(0.5)
            self._running = True
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()
            self.log.write("INFO", f"UDP UcxLog Listener gestartet auf 127.0.0.1:{self._port}")
        except OSError as e:
            self.log.write("ERROR", f"UDP UcxLog bind fehlgeschlagen: {e}")

    def stop(self) -> None:
        """Listener anhalten."""
        self._running = False
        try:
            if self._sock:
                self._sock.close()
        except Exception:
            pass
        self._sock = None
        if self._thread:
            self._thread.join(timeout=1.0)
        self._thread = None
        if self._enabled:
            self.log.write("INFO", "UDP UcxLog Listener gestoppt")

    def _parse_azimut(self, data: bytes) -> float | None:
        """Azimut aus UcxLog-XML extrahieren. None wenn fehlt oder ungültig."""
        try:
            root = ET.fromstring(data)
        except ET.ParseError:
            return None
        az_el = root.find("Azimut")
        if az_el is None or az_el.text is None or not str(az_el.text).strip():
            return None
        try:
            return float(str(az_el.text).strip())
        except ValueError:
            return None

    def _loop(self) -> None:
        while self._running and self._sock:
            try:
                data, _ = self._sock.recvfrom(4096)
            except socket.timeout:
                continue
            except Exception:
                break
            if not data:
                continue
            az = self._parse_azimut(data)
            if az is None:
                continue
            az_deg = wrap_deg(az)
            self.log.write("UDP", f"UcxLog Azimut={az_deg:.1f}° → setze Rotor")
            try:
                if getattr(self.ctrl, "enable_az", True):
                    self.ctrl.set_az_deg(az_deg, force=True)
            except Exception as e:
                self.log.write("WARN", f"UDP UcxLog set_az_deg: {e}")
