"""SPID/ROT2PROG und CAT-Simulation ueber eine serielle Schnittstelle
(z. B. com0com).

Analog zu ``PstAxisServer`` in ``pst_server.py``, aber:
- Transport ist pyserial statt TCP-Socket.
- Ein Rotor-Listener bedient AZ **und** EL gleichzeitig (wie der echte SPID
  MD-03 / ROT2PROG-Controller, der beide Achsen im selben 13-Byte-Frame
  transportiert).
- Ein Rig-Listener stellt einen vom aktiven Rig-Profil abhaengigen
  CAT-Kanal bereit (Yaesu newcat/legacy, Kenwood, Elecraft, Icom CI-V).
- Automatisches Reconnect, wenn die Gegenseite den virtuellen COM-Port
  schliesst.

Oeffentliche API:
- ``PstSerialPort(port, baudrate, ctrl, log)``: SPID-Rotor-Listener (Thread).
- ``RigSerialPort(port, baudrate, rig_bridge, profile_id, log)``: CAT-Listener
  fuer ein Rig-Profil (Thread).
- ``PstSerialManager(ctrl, log, rig_bridge=None)``: verwaltet eine Liste
  konfigurierter Ports anhand eines Dict-Abschnitts ``pst_serial`` aus der
  App-Konfiguration.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

try:
    import serial  # pyserial
    from serial import SerialException
except Exception:  # pragma: no cover - pyserial ist eine harte Abhängigkeit
    serial = None  # type: ignore[assignment]

    class SerialException(Exception):  # type: ignore[no-redef]
        pass

from . import verbose_cat_log
from .logutil import LogBuffer
from .rig_bridge.cat_responder import CatResponder, build_responder
from .spid_rot2prog import (
    CMD_SET,
    CMD_STATUS,
    CMD_STOP,
    encode_reply,
    parse_command_packet,
)


def _normalize_port(name: str) -> str:
    """COM-Name in eine für ``pyserial`` sichere Form bringen.

    ``COM10`` und höher (sowie ``CNCA0``/``CNCB0`` aus com0com) müssen auf
    Windows mit dem ``\\\\.\\``-Prefix geöffnet werden.
    """
    s = str(name or "").strip()
    if not s:
        return s
    if s.startswith("\\\\.\\"):
        return s
    up = s.upper()
    if up.startswith("COM") or up.startswith("CNCA") or up.startswith("CNCB"):
        return "\\\\.\\" + s
    return s


class PstSerialPort:
    """SPID/ROT2PROG-Listener auf einer seriellen Schnittstelle.

    Ein Frame (13 Byte) → Achsen setzen + Antwort (12 Byte) zurücksenden.
    Achsen, die per ``enable_az``/``enable_el`` deaktiviert sind, werden
    schreibend ignoriert und liefern in der Statusantwort 0°.
    """

    def __init__(self, port: str, baudrate: int, ctrl, log: LogBuffer):
        self.port = str(port or "").strip()
        self.baudrate = int(baudrate or 115200)
        self.ctrl = ctrl
        self.log = log
        self.running: bool = False
        self._thread: Optional[threading.Thread] = None
        self._ser: Optional[Any] = None
        self.last_rx_ts: float = 0.0
        self.last_error: str = ""

    # ------------------------------------------------------------------ API
    def start(self) -> None:
        if self.running:
            return
        if serial is None:
            self.log.write(
                "ERROR",
                f"PST-Serial {self.port}: pyserial ist nicht installiert",
            )
            return
        self.running = True
        self._thread = threading.Thread(
            target=self._loop, name=f"pst-serial-{self.port}", daemon=True
        )
        self._thread.start()
        self.log.write("INFO", f"PST-Serial gestartet auf {self.port} @ {self.baudrate}")

    def stop(self) -> None:
        self.running = False
        try:
            if self._ser is not None:
                self._ser.close()
        except Exception:
            pass
        self._ser = None

    # -------------------------------------------------------------- internals
    def _apply_set(self, cmd) -> None:
        """CMD_SET bedient beide Achsen gleichzeitig (SPID MD-03 / ROT2PROG)."""
        try:
            if cmd.az_d10 is not None and bool(getattr(self.ctrl, "enable_az", True)):
                self.ctrl.set_az_from_spid(cmd.az_d10)
        except Exception as exc:
            self.log.write("WARN", f"PST-Serial {self.port}: set AZ fehlgeschlagen: {exc}")
        try:
            if cmd.el_d10 is not None and bool(getattr(self.ctrl, "enable_el", True)):
                self.ctrl.set_el_from_spid(cmd.el_d10)
        except Exception as exc:
            self.log.write("WARN", f"PST-Serial {self.port}: set EL fehlgeschlagen: {exc}")

    def _apply_stop(self) -> None:
        """CMD_STOP: Soll-Wert auf aktuelle Position klemmen (beide Achsen)."""
        try:
            if bool(getattr(self.ctrl, "enable_az", True)):
                self.ctrl.hold_az_at_current_pos()
        except Exception:
            pass
        try:
            if bool(getattr(self.ctrl, "enable_el", True)):
                self.ctrl.hold_el_at_current_pos()
        except Exception:
            pass

    def _build_reply(self) -> bytes:
        az_d10 = (
            (self.ctrl.az.pos_d10 or 0)
            if bool(getattr(self.ctrl, "enable_az", True))
            else 0
        )
        el_d10 = (
            (self.ctrl.el.pos_d10 or 0)
            if bool(getattr(self.ctrl, "enable_el", True))
            else 0
        )
        return encode_reply(az_d10, el_d10, ph=10, pv=10)

    def _open_port(self) -> Any:
        assert serial is not None
        return serial.Serial(
            _normalize_port(self.port),
            baudrate=self.baudrate,
            bytesize=8,
            parity="N",
            stopbits=1,
            timeout=0.2,
            write_timeout=0.5,
        )

    def _loop(self) -> None:
        buf = b""
        while self.running:
            # Port öffnen (mit Retry-Schleife)
            while self.running and self._ser is None:
                try:
                    self._ser = self._open_port()
                    self.last_error = ""
                    self.log.write("INFO", f"PST-Serial {self.port} offen")
                except Exception as exc:
                    self.last_error = str(exc)
                    self.log.write(
                        "WARN", f"PST-Serial {self.port} open fehlgeschlagen: {exc}"
                    )
                    # Kurz warten; Stop-Flag zwischendurch prüfen
                    for _ in range(10):
                        if not self.running:
                            break
                        time.sleep(0.1)

            if not self.running:
                break

            ser = self._ser
            assert ser is not None

            try:
                chunk = ser.read(64)
            except SerialException as exc:
                self.last_error = str(exc)
                self.log.write(
                    "WARN", f"PST-Serial {self.port} read-Fehler: {exc}"
                )
                self._safe_close()
                buf = b""
                continue
            except Exception as exc:
                self.last_error = str(exc)
                self.log.write(
                    "ERROR", f"PST-Serial {self.port} unerwarteter Fehler: {exc}"
                )
                self._safe_close()
                buf = b""
                continue

            if not chunk:
                continue
            buf += chunk

            # ROT2PROG-Frames à 13 Byte abarbeiten
            while len(buf) >= 13:
                pkt = buf[:13]
                buf = buf[13:]
                cmd = parse_command_packet(pkt)
                if cmd is None:
                    self.log.write(
                        "PST",
                        f"SERIAL {self.port} RX <unbekannt> len=13 raw={pkt.hex()}",
                    )
                    continue

                try:
                    self.last_rx_ts = time.time()
                except Exception:
                    pass

                self.log.write(
                    "PST",
                    f"SERIAL {self.port} RX cmd={cmd.cmd} az_d10={cmd.az_d10} "
                    f"el_d10={cmd.el_d10} raw={pkt.hex()}",
                )

                if cmd.cmd == CMD_SET:
                    self._apply_set(cmd)
                elif cmd.cmd == CMD_STOP:
                    self._apply_stop()
                elif cmd.cmd == CMD_STATUS:
                    pass

                try:
                    reply = self._build_reply()
                    ser.write(reply)
                    try:
                        ser.flush()
                    except Exception:
                        pass
                    self.log.write(
                        "PST",
                        f"SERIAL {self.port} TX reply_len={len(reply)} hex={reply.hex()}",
                    )
                except SerialException as exc:
                    self.last_error = str(exc)
                    self.log.write(
                        "WARN", f"PST-Serial {self.port} write-Fehler: {exc}"
                    )
                    self._safe_close()
                    buf = b""
                    break

        self._safe_close()
        self.running = False
        self.log.write("INFO", f"PST-Serial {self.port} gestoppt")

    def _safe_close(self) -> None:
        try:
            if self._ser is not None:
                self._ser.close()
        except Exception:
            pass
        self._ser = None


class RigSerialPort:
    """CAT-Simulations-Listener fuer ein bestimmtes Rig-Profil.

    Oeffnet einen virtuellen COM und verhaelt sich gegenueber externen
    Programmen (WSJT-X, fldigi, JTDX, N1MM …) wie ein echtes Funkgeraet.
    Die konkreten CAT-Regeln liefert der ``CatResponder``, der aus dem
    gebundenen Rig-Profil (Marke/Modell/Hamlib-ID) gebaut wird.

    Verhalten bei *nicht*-aktivem Profil: Port bleibt geoeffnet und liefert
    Antworten aus dem Cache; Schreibwuensche werden aber nicht auf die
    echte CAT-Leitung gelegt, da das Profil gerade gar nicht mit dem
    realen TRX verbunden ist.
    """

    def __init__(
        self,
        port: str,
        baudrate: int,
        rig_bridge,
        profile_id: str,
        log: LogBuffer,
    ) -> None:
        self.port = str(port or "").strip()
        self.baudrate = int(baudrate or 38400)
        self._rb = rig_bridge
        self.profile_id = str(profile_id or "")
        self.log = log
        self.running: bool = False
        self._thread: Optional[threading.Thread] = None
        self._ser: Optional[Any] = None
        self.last_rx_ts: float = 0.0
        self.last_error: str = ""
        self._responder: Optional[CatResponder] = None
        self._responder_profile: dict = {}
        self._build_responder()

    # ------------------------------------------------------------------ API
    @property
    def is_profile_active(self) -> bool:
        if self._rb is None:
            return False
        try:
            return str(self._rb.active_rig_id()) == self.profile_id
        except Exception:
            return False

    def rebuild_responder(self) -> None:
        """Responder mit ggf. geaendertem Profil-Dict neu erzeugen."""
        self._build_responder()

    def _build_responder(self) -> None:
        prof: dict = {}
        if self._rb is not None:
            try:
                prof = self._rb.get_profile(self.profile_id) or {}
            except Exception:
                prof = {}
        self._responder_profile = dict(prof)
        try:
            self._responder = build_responder(
                prof,
                get_state=self._get_state,
                enqueue_write=self._enqueue_write_gated,
                refresh_frequency_for_read=self._refresh_for_read,
                on_state_patch=self._on_state_patch,
                log_label=f"CAT-Sim[{self.profile_id}]",
            )
        except Exception as exc:
            self.last_error = f"Responder-Init fehlgeschlagen: {exc}"
            self._responder = None

    def _get_state(self) -> dict:
        if self._rb is None:
            return {}
        try:
            st = self._rb._state.snapshot()  # noqa: SLF001 — gewollter Zugriff
            return dict(st)
        except Exception:
            return {}

    def _refresh_for_read(self, timeout_s: float) -> bool:
        """Vom Responder aus aufgerufen, wenn die Gegenstelle die VFO-Frequenz
        lesen will. ``refresh_rig_frequency_from_cat`` ist heute nicht mehr
        blockierend — wir triggern also nur noch einen Hintergrund-READFREQ
        und kehren sofort zurueck. Der Listener-Thread haelt so unter allen
        Umstaenden den Byte-Fluss vom externen Programm am Laufen, auch wenn
        der echte TRX gerade mal zickt."""
        if not self.is_profile_active or self._rb is None:
            return False
        try:
            return bool(self._rb.refresh_rig_frequency_from_cat(float(timeout_s)))
        except Exception:
            return False

    def _enqueue_write_gated(self, command: str, log_ctx: str) -> None:
        if self._rb is None:
            return
        if not self.is_profile_active:
            # Profil ist nicht verbunden — Schreibwunsch verwerfen, damit
            # WSJT-X/fldigi nicht plötzlich das aktive Rig verstellen.
            self.log.write(
                "PST",
                f"SERIAL {self.port} (rig:{self.profile_id}) — Schreibbefehl "
                f"ignoriert (Profil nicht aktiv): {command!r}",
            )
            return
        try:
            self._rb._enqueue_radio_write(command, log_ctx=log_ctx)  # noqa: SLF001
        except Exception as exc:
            self.log.write("WARN", f"SERIAL {self.port} enqueue_write Fehler: {exc}")

    def _on_state_patch(self, patch: dict) -> None:
        """Optimistischer State-Update fuer den zentralen RadioStateCache.

        Wird vom Responder sofort vor dem ``SETFREQ``/``SETMODE``/``SETPTT``-
        Enqueue aufgerufen. So sehen externe Programme *unmittelbar* nach
        ihrem eigenen Write den neuen Wert beim nachfolgenden Lese-Poll —
        das Polling-Verhalten z. B. von Ham Radio Deluxe wertet den alten
        Cache-Wert sonst als "Funkgeraet hat meinen SET nicht angenommen"
        und blockiert weitere Benutzereingaben (`hängt ständig`).

        Regel: nur patchen, wenn das Profil aktuell das aktive Rig ist —
        passive Profile duerfen den Haupt-State nicht veraendern.
        """
        if not patch or self._rb is None or not self.is_profile_active:
            return
        try:
            if verbose_cat_log.is_enabled():
                # Vor/Nach-Werte loggen, damit man im Fehlerbild sehen kann,
                # ob der optimistische Patch tatsaechlich angewendet wurde
                # und mit welcher Quelle er im Cache landet.
                try:
                    before = self._rb._state.snapshot()  # noqa: SLF001
                except Exception:
                    before = {}
                self.log.write(
                    "INFO",
                    f"CAT-VERB [rig:{self.profile_id}] optimistic state_patch "
                    f"patch={patch} before={{'frequency_hz': {before.get('frequency_hz')}, "
                    f"'mode': {before.get('mode')!r}, 'ptt': {before.get('ptt')}}}",
                )
            self._rb._state.update(**patch)  # noqa: SLF001
        except Exception as exc:
            self.log.write("WARN", f"SERIAL {self.port} state_patch Fehler: {exc}")

    def start(self) -> None:
        if self.running:
            return
        if serial is None:
            self.log.write(
                "ERROR",
                f"RIG-Serial {self.port}: pyserial ist nicht installiert",
            )
            return
        self.running = True
        self._thread = threading.Thread(
            target=self._loop, name=f"rig-serial-{self.port}", daemon=True
        )
        self._thread.start()
        self.log.write(
            "INFO",
            f"RIG-Serial gestartet auf {self.port} @ {self.baudrate} "
            f"(Profil {self.profile_id})",
        )

    def stop(self) -> None:
        self.running = False
        try:
            if self._ser is not None:
                self._ser.close()
        except Exception:
            pass
        self._ser = None

    # -------------------------------------------------------------- intern
    def _open_port(self) -> Any:
        assert serial is not None
        return serial.Serial(
            _normalize_port(self.port),
            baudrate=self.baudrate,
            bytesize=8,
            parity="N",
            stopbits=1,
            timeout=0.2,
            write_timeout=0.5,
        )

    def _loop(self) -> None:
        while self.running:
            while self.running and self._ser is None:
                try:
                    self._ser = self._open_port()
                    self.last_error = ""
                    self.log.write("INFO", f"RIG-Serial {self.port} offen")
                except Exception as exc:
                    self.last_error = str(exc)
                    self.log.write(
                        "WARN", f"RIG-Serial {self.port} open fehlgeschlagen: {exc}"
                    )
                    for _ in range(10):
                        if not self.running:
                            break
                        time.sleep(0.1)

            if not self.running:
                break

            ser = self._ser
            assert ser is not None

            try:
                chunk = ser.read(256)
            except SerialException as exc:
                self.last_error = str(exc)
                self.log.write("WARN", f"RIG-Serial {self.port} read-Fehler: {exc}")
                self._safe_close()
                continue
            except Exception as exc:
                self.last_error = str(exc)
                self.log.write(
                    "ERROR", f"RIG-Serial {self.port} unerwarteter Fehler: {exc}"
                )
                self._safe_close()
                continue

            if not chunk:
                continue

            try:
                self.last_rx_ts = time.time()
            except Exception:
                pass

            if verbose_cat_log.is_enabled():
                try:
                    ascii_preview = verbose_cat_log.format_ascii_preview(bytes(chunk))
                    self.log.write(
                        "PST",
                        f"SERIAL {self.port} RIG RX len={len(chunk)} "
                        f"hex={bytes(chunk).hex()} ascii={ascii_preview!r} "
                        f"(externes Programm → Bruecke)",
                    )
                except Exception:
                    pass

            if self._responder is None:
                continue
            try:
                replies = self._responder.feed(bytes(chunk))
            except Exception as exc:
                self.log.write(
                    "WARN", f"RIG-Serial {self.port} Responder-Fehler: {exc}"
                )
                replies = []

            for r in replies:
                if not r:
                    continue
                try:
                    ser.write(r)
                    try:
                        ser.flush()
                    except Exception:
                        pass
                    self.log.write(
                        "PST",
                        f"SERIAL {self.port} RIG TX len={len(r)} hex={r.hex()}",
                    )
                    if verbose_cat_log.is_enabled():
                        self._log_responder_reply(r)
                except SerialException as exc:
                    self.last_error = str(exc)
                    self.log.write(
                        "WARN", f"RIG-Serial {self.port} write-Fehler: {exc}"
                    )
                    self._safe_close()
                    break

        self._safe_close()
        self.running = False
        self.log.write("INFO", f"RIG-Serial {self.port} gestoppt")

    def _safe_close(self) -> None:
        try:
            if self._ser is not None:
                self._ser.close()
        except Exception:
            pass
        self._ser = None

    def _log_responder_reply(self, reply: bytes) -> None:
        """Nur im Verbose-Modus: Responder-Antwort ASCII-dekodieren und die
        eingebettete Frequenz (bei ``FA``/``FB``/``IF``) aufbereiten.

        Die rohe ``SERIAL … RIG TX len=N hex=…`` wird schon immer geloggt;
        mit eingeschaltetem erweiterten Log wollen wir im Klartext sehen,
        *welchen* VFO-Wert die Bruecke der externen Software gerade
        zurueckgegeben hat — genau das ist der Unterschied zwischen
        "optimistischer Patch wirkt" und "alter Cache-Wert wird echot".
        """
        try:
            txt = reply.decode("ascii", errors="replace").strip()
        except Exception:
            return
        hz: Optional[int] = None
        head = txt[:2].upper() if txt else ""
        # FA/FB<digits>; und IF...; (Yaesu newcat / legacy / Kenwood).
        if head in ("FA", "FB") and txt.endswith(";"):
            digits = "".join(ch for ch in txt[2:-1] if ch.isdigit())
            if digits:
                try:
                    hz = int(digits)
                except ValueError:
                    hz = None
        elif head == "IF" and txt.endswith(";"):
            # newcat: IF + 3 Mem + 9 Freq + 5 Clar + …  → Stellen 5..14
            # legacy/Kenwood: IF + 11 Freq + …          → Stellen 2..13
            body = txt[2:-1]
            for start, length in ((3, 9), (0, 11)):
                cand = body[start : start + length]
                if cand.isdigit():
                    try:
                        hz = int(cand)
                        break
                    except ValueError:
                        continue
        extra = ""
        if hz is not None:
            extra = f" (VFO={hz / 1e6:.6f} MHz, {hz} Hz)"
        self.log.write(
            "INFO",
            f"CAT-VERB [rig:{self.profile_id}] Reply an externes Programm: "
            f"{txt!r}{extra}",
        )


@dataclass
class _ListenerCfg:
    port: str
    baudrate: int
    enabled: bool
    target: str  # "rotor" oder "rig:<profile_id>"

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "_ListenerCfg":
        return _ListenerCfg(
            port=str(d.get("port", "") or "").strip(),
            baudrate=int(d.get("baudrate", 115200) or 115200),
            enabled=bool(d.get("enabled", True)),
            target=str(d.get("target", "rotor") or "rotor").strip() or "rotor",
        )


class PstSerialManager:
    """Verwaltet beliebig viele Serial-Listener (Rotor- und Rig-CAT).

    Die Config-Struktur ist::

        {
            "enabled": bool,
            "listeners": [
                {"port": "COM21", "baudrate": 115200, "enabled": true, "target": "rotor"},
                {"port": "COM23", "baudrate": 38400,  "enabled": true, "target": "rig:default"},
                ...
            ],
        }

    ``update_config`` übernimmt die Änderungen; ``start_all``/``stop_all``
    respektieren das globale ``enabled``-Flag sowie die Einzel-Flags.
    Der Manager laesst sich optional mit ``rig_bridge`` verdrahten — ohne
    werden Rig-Listener deaktiviert (sie koennen dann mangels Profilquelle
    nicht aufgebaut werden).
    """

    def __init__(self, ctrl, log: LogBuffer, rig_bridge=None):
        self._ctrl = ctrl
        self._log = log
        self._rb = rig_bridge
        # Port → Listener (PstSerialPort oder RigSerialPort)
        self._ports: Dict[str, Any] = {}
        self._cfg: Dict[str, Any] = {"enabled": False, "listeners": []}

    # ------------------------------------------------------------------ API
    @property
    def enabled(self) -> bool:
        return bool(self._cfg.get("enabled", False))

    @property
    def running(self) -> bool:
        return any(p.running for p in self._ports.values())

    @property
    def last_rx_ts(self) -> float:
        best = 0.0
        for p in self._ports.values():
            try:
                t = float(getattr(p, "last_rx_ts", 0.0) or 0.0)
            except Exception:
                t = 0.0
            if t > best:
                best = t
        return best

    def listeners(self) -> List[Any]:
        return list(self._ports.values())

    def get(self, port: str) -> Optional[Any]:
        return self._ports.get(str(port or "").strip())

    def _listener_target_type(self, listener: Any) -> str:
        return "rig" if isinstance(listener, RigSerialPort) else "rotor"

    def _make_listener(self, lc: _ListenerCfg) -> Any:
        """Anhand ``target`` den passenden Listener-Typ instanziieren."""
        tgt = (lc.target or "rotor").strip().lower()
        if tgt.startswith("rig:") and self._rb is not None:
            profile_id = tgt.split(":", 1)[1].strip()
            return RigSerialPort(
                lc.port, lc.baudrate, self._rb, profile_id, self._log
            )
        # Rotor-Listener (Fallback auch wenn rig_bridge fehlt oder target unbekannt).
        return PstSerialPort(lc.port, lc.baudrate, self._ctrl, self._log)

    def _needs_rebuild(self, cur: Any, lc: _ListenerCfg) -> bool:
        """Prueft, ob die Laufzeit-Instanz verworfen werden muss."""
        if int(cur.baudrate) != int(lc.baudrate):
            return True
        tgt = (lc.target or "rotor").strip().lower()
        wants_rig = tgt.startswith("rig:")
        has_rig = isinstance(cur, RigSerialPort)
        if wants_rig != has_rig:
            return True
        if has_rig:
            profile_id = tgt.split(":", 1)[1].strip()
            if cur.profile_id != profile_id:
                return True
        return False

    def update_config(self, cfg: Dict[str, Any]) -> None:
        """Neue Config übernehmen. Ports, die nicht mehr vorkommen, werden
        gestoppt; neue Ports werden angelegt (aber nicht automatisch
        gestartet — darum kümmert sich ``start_all``).
        """
        cfg = dict(cfg or {})
        self._cfg = {
            "enabled": bool(cfg.get("enabled", False)),
            "listeners": list(cfg.get("listeners") or []),
        }

        wanted: Dict[str, _ListenerCfg] = {}
        for item in self._cfg["listeners"]:
            if not isinstance(item, dict):
                continue
            lc = _ListenerCfg.from_dict(item)
            if not lc.port:
                continue
            wanted[lc.port] = lc

        # Entfernte Ports stoppen und verwerfen
        for port in list(self._ports.keys()):
            if port not in wanted:
                try:
                    self._ports[port].stop()
                except Exception:
                    pass
                del self._ports[port]

        # Bestehende aktualisieren, neue anlegen
        for port, lc in wanted.items():
            cur = self._ports.get(port)
            if cur is None:
                self._ports[port] = self._make_listener(lc)
                continue
            if self._needs_rebuild(cur, lc):
                was_running = cur.running
                try:
                    cur.stop()
                except Exception:
                    pass
                new = self._make_listener(lc)
                self._ports[port] = new
                if was_running and self.enabled and lc.enabled:
                    new.start()

    def refresh_rig_listeners(self) -> None:
        """Nach Profilwechsel: alle ``RigSerialPort``-Responder neu bauen,
        damit z. B. eine neue Marke/ein neues Modell sofort simuliert wird.
        Port und Thread bleiben erhalten; es wird nur der Responder
        ausgetauscht (Responder-Erzeugung ist vergleichsweise guenstig).
        """
        for listener in self._ports.values():
            if isinstance(listener, RigSerialPort):
                try:
                    listener.rebuild_responder()
                except Exception:
                    pass

    def _listener_enabled(self, port: str) -> bool:
        for item in self._cfg.get("listeners") or []:
            if isinstance(item, dict) and str(item.get("port", "")).strip() == port:
                return bool(item.get("enabled", True))
        return False

    def start_all(self) -> None:
        """Startet alle Listener, die global + einzeln aktiviert sind."""
        if not self.enabled:
            return
        for port, p in self._ports.items():
            if self._listener_enabled(port) and not p.running:
                p.start()

    def stop_all(self) -> None:
        for p in self._ports.values():
            try:
                p.stop()
            except Exception:
                pass

    def start_port(self, port: str) -> bool:
        p = self._ports.get(port)
        if p is None:
            return False
        if not p.running:
            p.start()
        return True

    def stop_port(self, port: str) -> bool:
        p = self._ports.get(port)
        if p is None:
            return False
        try:
            p.stop()
        except Exception:
            pass
        return True

    def ports(self) -> Iterable[str]:
        return list(self._ports.keys())
