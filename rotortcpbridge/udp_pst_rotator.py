"""UDP-Schnittstelle kompatibel zum PstRotatorAz-Protokoll (AZ) plus EL-Steuerung.

Protokoll-Übersicht:
  Empfang  (auf listen_port, default 12000):
    <PST><AZIMUTH>85</AZIMUTH></PST>       → AZ auf 85° fahren (wenn AZ aktiv)
    <PST><ELEVATION>25</ELEVATION></PST>   → EL auf 25° fahren (wenn EL aktiv)
    <PST><STOP>1</STOP></PST>              → Rotor stoppen (AZ+EL)
    <PST><PARK>1</PARK></PST>              → Rotor stoppen (AZ+EL)
    <PST><QRA>JO31jg</QRA></PST>           → Peilung zum Locator, AZ fahren
    <PST><ANT>1</ANT></PST>                → Antenne 1–3 wählen
    <PST><TRACK>0|1</TRACK></PST>          → OK-Antwort (kein Tracking)
    <PST><ON>0|1</ON></PST>                → externe Kommandos aus/an + OK
    <PST>AZ?</PST>                         → aktuelle AZ-Position zurückschicken
    <PST>TGA?</PST>                        → Ziel-Azimut zurückschicken
    <PST>EL?</PST>                         → aktuelle EL-Position (wenn EL aktiv)
    <PST>TGE?</PST>                        → Ziel-Elevation (wenn EL aktiv)
    <PST>MODE?</PST>                       → immer MODE:0 (Manual)
    (andere bekannte Felder werden geparst, aber ignoriert bzw. geloggt)

  Senden  (Ziel konfigurierbar; leer = Subnetz-Broadcast x.y.z.255 : listen_port + 1):
    AZ:xxx<CR>   bei Positionsänderung und auf Anfrage AZ?
    TGA:xxx<CR>  auf Anfrage TGA?
    EL:xxx<CR>   auf Anfrage EL? (nur bei aktivem EL)
    TGE:xxx<CR>  auf Anfrage TGE? (nur bei aktivem EL)
    MODE:0<CR>   auf Anfrage MODE?
    OK:…<CR>     Bestätigung TRACK/ON/ANT/QRA
"""

from __future__ import annotations

import re
import socket
import threading
import time
from typing import Callable

from .angle_utils import (
    az_deg_for_external_report,
    az_d10_for_external_report,
    az_max_d10_from_axis,
    raw_rotor_az_deg_from_axis,
    resolve_external_az_d10,
    rotor_az_for_display_bearing,
    wrap_deg,
)
from .geo_utils import bearing_deg, effective_station_lat_lon, maidenhead_to_lat_lon
from .net_utils import ipv4_subnet_broadcast_default, normalize_udp_bind_host
from .pst_notify_logic import pst_notify_position_decision

# Bekannte PST-Tags ohne Rotor-Wirkung (PstRotator-UI / Zubehör)
_KNOWN_SILENT = {
    "OFFSET1",
    "OFFSET2",
    "STF",
    "STR",
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
    """Emuliert die UDP-Schnittstelle von PstRotatorAz (AZ) und nimmt EL-Befehle an.

    Hört auf ``listen_port`` (Standard: 12000) und sendet Positionsmeldungen
    an ``send_host : listen_port + 1`` (nicht konfiguriert: Subnetz-Broadcast x.y.z.255).
    ``<ELEVATION>`` sowie ``EL?``/``TGE?`` nur, wenn der Controller EL aktiv hat.
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
        # Letztes eingehendes Steuerpaket (für Traffic-LED in Einstellungen / GUI)
        self._last_rx_ts: float = 0.0
        # Fehlermeldung wenn Port beim Start belegt war (None = kein Fehler)
        self.bind_error_msg: str | None = None
        # <ON>1</ON> = Kommandos annehmen; 0 = nur Abfragen/OK
        self._commands_on: bool = True
        # Optional: Antennenwechsel Index 0–2 → UI/Bus (QueuedConnection empfohlen)
        self.on_antenna_selected: Callable[[int], None] | None = None

    # ------------------------------------------------------------------
    # Öffentliche API
    # ------------------------------------------------------------------

    @property
    def is_active(self) -> bool:
        return bool(self._enabled and self._running and self._sock_rx is not None)

    @property
    def last_rx_ts(self) -> float:
        try:
            return float(self._last_rx_ts or 0.0)
        except Exception:
            return 0.0

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
            from .net_bind_error import format_bind_error

            self.bind_error_msg = format_bind_error(
                bind_listen, self._port, e, proto_name="UDP PST-Rotator"
            )
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

    def _udp_az_flags(self) -> tuple[bool, bool]:
        try:
            ui = (self.cfg or {}).get("ui", {}) or {}
            return (
                bool(ui.get("udp_pst_az_shortest_path", False)),
                bool(ui.get("udp_pst_az_report_mod360", False)),
            )
        except Exception:
            return (False, False)

    def notify_position(self, az_d10: int) -> None:
        """Vom Haupt-Tick aufgerufen wenn sich AZ-Position geändert hat.

        Sendet AZ:xxx<CR> an listen_port+1, wenn der Wert sich um mindestens
        1 d10-Schritt (= 0,1°) geändert hat.

        Einzelne pos_d10==0-Samples nach einer anderen Position werden ignoriert
        (typische Leseglitch), bis 0° mehrere Ticks stabil ist.
        """
        if not self._enabled or self._sock_tx is None:
            return
        shortest, report_mod360 = self._udp_az_flags()
        try:
            report_d10 = az_d10_for_external_report(
                int(az_d10), shortest_path=shortest, report_mod360=report_mod360
            )
        except Exception:
            report_d10 = int(az_d10)
        send, self._zero_confirm = pst_notify_position_decision(
            report_d10,
            self._last_sent_d10,
            self._zero_confirm,
            zero_confirm_ticks=_ZERO_CONFIRM_TICKS,
        )
        if not send:
            return
        self._last_sent_d10 = report_d10
        az_deg = report_d10 / 10.0
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
                shortest, report_mod360 = self._udp_az_flags()
                return az_deg_for_external_report(
                    float(d10) / 10.0,
                    shortest_path=shortest,
                    report_mod360=report_mod360,
                )
        except Exception as e:
            self.log.write("WARN", f"UDP PST-Rotator _current_az_deg: {e}")
        return 0.0

    def _target_az_deg(self) -> float:
        """Gibt das aktuelle AZ-Ziel in Grad zurück."""
        try:
            d10 = getattr(self.ctrl.az, "target_d10", None)
            if d10 is not None:
                shortest, report_mod360 = self._udp_az_flags()
                return az_deg_for_external_report(
                    float(d10) / 10.0,
                    shortest_path=shortest,
                    report_mod360=report_mod360,
                )
        except Exception as e:
            self.log.write("WARN", f"UDP PST-Rotator _target_az_deg: {e}")
        return self._current_az_deg()

    def _current_el_deg(self) -> float:
        """Aktuelle EL-Position in Grad (0 wenn EL aus oder unbekannt)."""
        try:
            if not bool(getattr(self.ctrl, "enable_el", False)):
                return 0.0
            d10 = getattr(self.ctrl.el, "pos_d10", None)
            if d10 is not None:
                return float(int(d10)) / 10.0
        except Exception as e:
            self.log.write("WARN", f"UDP PST-Rotator _current_el_deg: {e}")
        return 0.0

    def _target_el_deg(self) -> float:
        """Aktuelles EL-Ziel in Grad."""
        try:
            if not bool(getattr(self.ctrl, "enable_el", False)):
                return 0.0
            d10 = getattr(self.ctrl.el, "target_d10", None)
            if d10 is not None:
                return float(int(d10)) / 10.0
        except Exception as e:
            self.log.write("WARN", f"UDP PST-Rotator _target_el_deg: {e}")
        return self._current_el_deg()

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

    def _pst_query_name(self, text: str) -> str | None:
        """Erkennt Positions-/Status-Abfragen; Rückgabe z. B. ``AZ?``, sonst None."""
        s = (text or "").strip()
        if not s:
            return None
        # <PST>AZ?</PST> oder <PST><AZ?></AZ?></PST> (Groß/Klein egal)
        m = re.fullmatch(
            r"<PST>\s*(?:<)?(AZ\?|TGA\?|EL\?|TGE\?|MODE\?)\s*(?:/>|></\1>)?\s*</PST>",
            s,
            flags=re.IGNORECASE,
        )
        if m:
            return m.group(1).upper()
        # Nur Inneres nach Strip des Wrappers
        if s.upper().startswith("<PST>") and s.upper().endswith("</PST>"):
            inner = s[5:-6].strip().upper()
            if inner in ("AZ?", "TGA?", "EL?", "TGE?", "MODE?"):
                return inner
            m2 = re.fullmatch(r"<(AZ\?|TGA\?|EL\?|TGE\?|MODE\?)>\s*</\1>", inner)
            if m2:
                return m2.group(1).upper()
        return None

    def _handle_packet(self, data: bytes, addr: tuple) -> None:
        """Verarbeitet ein eingehendes UDP-Paket."""
        try:
            text = data.decode("utf-8", errors="replace").strip()
        except Exception:
            return

        sender = f"{addr[0]}:{addr[1]}" if addr else "?"
        self._last_rx_ts = time.time()
        self.packet_received_flag = True

        # Alle Anfragen (Position, Mode, …) explizit ins Log – auch wenn unbekannt.
        q = self._pst_query_name(text)
        if q is not None:
            if q == "AZ?":
                az = self._current_az_deg()
                reply = f"AZ:{az:.1f}\r"
                self._send_reply(reply)
                self.log.write("UDP", f"PST Anfrage {q} von {sender} → {reply.strip()}")
                return
            if q == "TGA?":
                tga = self._target_az_deg()
                reply = f"TGA:{tga:.1f}\r"
                self._send_reply(reply)
                self.log.write("UDP", f"PST Anfrage {q} von {sender} → {reply.strip()}")
                return
            if q == "EL?":
                if not bool(getattr(self.ctrl, "enable_el", False)):
                    self.log.write(
                        "UDP", f"PST Anfrage {q} von {sender} → ignoriert (EL aus)"
                    )
                    return
                el = self._current_el_deg()
                reply = f"EL:{el:.1f}\r"
                self._send_reply(reply)
                self.log.write("UDP", f"PST Anfrage {q} von {sender} → {reply.strip()}")
                return
            if q == "TGE?":
                if not bool(getattr(self.ctrl, "enable_el", False)):
                    self.log.write(
                        "UDP", f"PST Anfrage {q} von {sender} → ignoriert (EL aus)"
                    )
                    return
                tge = self._target_el_deg()
                reply = f"TGE:{tge:.1f}\r"
                self._send_reply(reply)
                self.log.write("UDP", f"PST Anfrage {q} von {sender} → {reply.strip()}")
                return
            if q == "MODE?":
                # PstRotator: MODE:1 = Tracking, MODE:0 = Manual.
                # RotorTcpBridge hat keinen Satelliten-/Logger-Tracking-Modus → immer Manual.
                reply = "MODE:0\r"
                self._send_reply(reply)
                self.log.write("UDP", f"PST Anfrage {q} von {sender} → {reply.strip()}")
                return
            self.log.write(
                "UDP",
                f"PST Anfrage {q} von {sender} → nicht implementiert (ignoriert)",
            )
            return

        # Normales PST-XML: muss mit <PST> anfangen und mit </PST> enden
        if not (text.upper().startswith("<PST>") and text.upper().endswith("</PST>")):
            self.log.write(
                "WARN",
                f"UDP PST-Rotator: ungültiges Paket von {sender}: {text[:80]}",
            )
            return

        # Groß/Klein am Wrapper egal; Inhalt abschneiden über Länge von <PST>/</PST>
        inner = text[5:-6].strip()

        tags = list(_RE_TAG.finditer(inner))
        if not tags:
            self.log.write(
                "UDP",
                f"PST Anfrage von {sender} (kein Steuer-Tag): {text[:100]}",
            )
            return

        # Mehrere Tags in einem Paket möglich
        for m in tags:
            tag = m.group(1).strip().upper()
            val = m.group(2).strip()
            self._handle_tag(tag, val, sender)

    def _ack(self, tag: str, value: str) -> None:
        """PstRotator-übliche Bestätigung auf Port+1."""
        self._send_reply(f"OK:{tag}:{value}\r")

    def _commands_allowed(self, sender: str, tag: str) -> bool:
        if self._commands_on:
            return True
        self.log.write("UDP", f"PST {tag} von {sender} ignoriert (ON=0)")
        return False

    def _selected_antenna_idx(self) -> int:
        try:
            return max(0, min(2, int((self.cfg or {}).get("ui", {}).get("compass_antenna", 0))))
        except Exception:
            return 0

    def _antenna_offset_az(self, ant_idx: int | None = None) -> float:
        idx = self._selected_antenna_idx() if ant_idx is None else max(0, min(2, int(ant_idx)))
        slot = idx + 1
        try:
            v = getattr(self.ctrl.az, f"antoff{slot}", None)
            if v is not None:
                return float(v)
        except Exception:
            pass
        try:
            offs = (self.cfg or {}).get("ui", {}).get("antenna_offsets_az", [0.0, 0.0, 0.0])
            return float(offs[idx])
        except Exception:
            return 0.0

    def _antenna_dipole_enabled(self, ant_idx: int) -> bool:
        idx = max(0, min(2, int(ant_idx)))
        slot = idx + 1
        try:
            v = getattr(self.ctrl.az, f"antdp{slot}", None)
            if v is not None:
                return bool(int(v))
        except Exception:
            pass
        try:
            dips = (self.cfg or {}).get("ui", {}).get("antenna_dipole_az", [False, False, False])
            return bool(dips[idx])
        except Exception:
            return False

    def _set_az_to_display_bearing(self, bearing: float) -> float:
        """Anzeige-Peilung (Nord) → Rotor-Soll inkl. Versatz/Dipol; setzt den Rotor."""
        ant_idx = self._selected_antenna_idx()
        off = self._antenna_offset_az(ant_idx)
        cur_rotor = raw_rotor_az_deg_from_axis(getattr(self.ctrl, "az", None))
        dipole = self._antenna_dipole_enabled(ant_idx)
        rotor_deg = rotor_az_for_display_bearing(
            float(bearing),
            off,
            cur_rotor,
            dipole=dipole,
            last_rotor_az=getattr(self.ctrl, "az_dipole_last_rotor_az", None) if dipole else None,
            max_deg=float(az_max_d10_from_axis(getattr(self.ctrl, "az", None))) / 10.0,
        )
        self.ctrl.set_az_deg(rotor_deg, force=True)
        if dipole:
            try:
                self.ctrl.az_dipole_display_bearing = wrap_deg(float(bearing))
                self.ctrl.az_dipole_last_rotor_az = rotor_deg
            except Exception:
                pass
        return rotor_deg

    def _handle_tag(self, tag: str, val: str, sender: str) -> None:
        """Verarbeitet einen einzelnen PST-Tag."""
        if tag == "TRACK":
            # Nur Protokoll-ACK; kein Satelliten-Tracking. MODE? bleibt MODE:0.
            v = "1" if str(val).strip() in ("1", "true", "TRUE", "on", "ON") else "0"
            self._ack("TRACK", v)
            self.log.write("UDP", f"PST TRACK={v} von {sender} → OK (kein Tracking)")
            return

        if tag == "ON":
            on = str(val).strip() in ("1", "true", "TRUE", "on", "ON")
            self._commands_on = on
            self._ack("ON", "1" if on else "0")
            self.log.write(
                "UDP",
                f"PST ON={'1' if on else '0'} von {sender} → "
                f"{'Kommandos an' if on else 'Kommandos aus'}",
            )
            return

        if tag == "ANT":
            if not self._commands_allowed(sender, tag):
                return
            try:
                ant_num = int(float(str(val).strip()))
            except ValueError:
                self.log.write(
                    "WARN", f"UDP PST-Rotator: ungültiger ANT-Wert '{val}' von {sender}"
                )
                return
            if ant_num < 1 or ant_num > 3:
                self.log.write(
                    "WARN", f"UDP PST-Rotator: ANT={ant_num} von {sender} ungültig (1–3)"
                )
                return
            idx = ant_num - 1
            ui = (self.cfg or {}).setdefault("ui", {})
            try:
                old = max(0, min(2, int(ui.get("compass_antenna", 0))))
            except Exception:
                old = 0
            if old != idx and hasattr(self.ctrl, "align_az_bearing_after_antenna_switch"):
                try:
                    self.ctrl.align_az_bearing_after_antenna_switch(old, idx, self.cfg or {})
                except Exception as e:
                    self.log.write("WARN", f"UDP PST-Rotator ANT align: {e}")
            ui["compass_antenna"] = idx
            cb = self.on_antenna_selected
            if cb is not None:
                try:
                    cb(idx)
                except Exception as e:
                    self.log.write("WARN", f"UDP PST-Rotator ANT UI-Callback: {e}")
            self._ack("ANT", str(ant_num))
            self.log.write("UDP", f"PST ANT={ant_num} von {sender} → Antenne {ant_num}")
            return

        if tag == "QRA":
            if not self._commands_allowed(sender, tag):
                return
            loc = "".join(str(val or "").strip().upper().split())
            ll = maidenhead_to_lat_lon(loc) if loc else None
            if ll is None:
                self.log.write(
                    "WARN", f"UDP PST-Rotator: ungültiger QRA-Locator '{val}' von {sender}"
                )
                return
            if not getattr(self.ctrl, "enable_az", True):
                self.log.write("UDP", f"PST QRA von {sender} ignoriert (AZ aus)")
                return
            try:
                ui = (self.cfg or {}).get("ui", {}) or {}
                lat0, lon0 = effective_station_lat_lon(ui)
                bearing = wrap_deg(bearing_deg(lat0, lon0, float(ll[0]), float(ll[1])))
                rotor_deg = self._set_az_to_display_bearing(bearing)
            except Exception as e:
                self.log.write("WARN", f"UDP PST-Rotator QRA: {e}")
                return
            self._ack("QRA", loc)
            self.log.write(
                "UDP",
                f"PST QRA={loc} von {sender} → Peilung {bearing:.1f}° "
                f"(Rotor {rotor_deg:.1f}°)",
            )
            return

        if tag == "AZIMUTH":
            if not self._commands_allowed(sender, tag):
                return
            try:
                az_deg = float(val)
            except ValueError:
                self.log.write(
                    "WARN", f"UDP PST-Rotator: ungültiger AZIMUTH-Wert '{val}' von {sender}"
                )
                return
            try:
                shortest = bool(
                    (self.cfg or {}).get("ui", {}).get("udp_pst_az_shortest_path", False)
                )
            except Exception:
                shortest = False
            try:
                max_d10 = az_max_d10_from_axis(self.ctrl.az)
                cur_d10 = int(getattr(self.ctrl.az, "pos_d10", 0) or 0)
                az_d10 = resolve_external_az_d10(
                    int(round(float(az_deg) * 10.0)),
                    current_d10=cur_d10,
                    max_d10=max_d10,
                    shortest_path=shortest,
                )
                az_deg = float(az_d10) / 10.0
            except Exception as e:
                self.log.write("WARN", f"UDP PST-Rotator AZ-Auflösung: {e}")
                return
            self.log.write("UDP", f"PST AZIMUTH={az_deg:.1f}° von {sender} → setze Rotor")
            try:
                if getattr(self.ctrl, "enable_az", True):
                    self.ctrl.set_az_deg(az_deg, force=True)
                else:
                    self.log.write("UDP", f"PST AZIMUTH von {sender} ignoriert (AZ aus)")
            except Exception as e:
                self.log.write("WARN", f"UDP PST-Rotator set_az_deg: {e}")

        elif tag == "ELEVATION":
            if not self._commands_allowed(sender, tag):
                return
            try:
                el_deg = float(val)
            except ValueError:
                self.log.write(
                    "WARN", f"UDP PST-Rotator: ungültiger ELEVATION-Wert '{val}' von {sender}"
                )
                return
            if not bool(getattr(self.ctrl, "enable_el", False)):
                self.log.write("UDP", f"PST ELEVATION von {sender} ignoriert (EL aus)")
                return
            self.log.write("UDP", f"PST ELEVATION={el_deg:.1f}° von {sender} → setze Rotor")
            try:
                self.ctrl.set_el_deg(el_deg, force=True)
            except Exception as e:
                self.log.write("WARN", f"UDP PST-Rotator set_el_deg: {e}")

        elif tag in ("STOP", "PARK"):
            if not self._commands_allowed(sender, tag):
                return
            self.log.write("UDP", f"PST {tag} von {sender} → SETPOS auf Ist-Position (statt STOP)")
            try:
                self.ctrl.hold_all_at_current_pos()
                self._ack(tag, "1")
            except Exception as e:
                self.log.write("WARN", f"UDP PST-Rotator hold_all_at_current_pos: {e}")

        elif tag in _KNOWN_SILENT:
            self.log.write("UDP", f"PST {tag}={val} von {sender} (nicht implementiert, ignoriert)")

        else:
            self.log.write("UDP", f"PST unbekannter Tag {tag}={val} von {sender} (ignoriert)")
