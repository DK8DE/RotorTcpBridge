from __future__ import annotations

import socket
import threading
import time
from typing import Optional, Tuple

from .logutil import LogBuffer

# Hamlib rotctld Standardport (rigctld nutzt 4532 -> Rotor ungerade, z.B. 4533).
DEFAULT_ROTCTLD_PORT = 4533

# Protokollversion des dump_state-Blocks (netrotctl: 1 = tag=value Format).
_PROT_VER = 1
_MODEL_NAME = "RotorTcpBridge"

# AZ-Bereich (zirkulaer 0..360), EL-Bereich (0..90).
_MIN_AZ = 0.0
_MAX_AZ = 360.0
_MIN_EL = 0.0
_MAX_EL = 90.0


def _fmt_deg_from_d10(d10: Optional[int]) -> str:
    """0,1°-Rohwert als Hamlib-Grad-String (double, 6 Nachkommastellen)."""
    try:
        return f"{(int(d10 or 0) / 10.0):.6f}"
    except Exception:
        return "0.000000"


def build_dump_state(*, az_enabled: bool = True, el_enabled: bool = True) -> str:
    """dump_state-Block im netrotctl-Format (Protokollversion 1, tag=value).

    Aufbau (siehe Hamlib rigs/dummy/netrotctl.c ``netrotctl_open``):
      Zeile 1: Protokollversion
      Zeile 2: rot_model (wird vom Client verworfen)
      danach : ``key=value``-Zeilen, abgeschlossen mit ``done``
    """
    if el_enabled and az_enabled:
        rot_type = "AzEl"
    elif el_enabled and not az_enabled:
        rot_type = "El"
    else:
        rot_type = "Az"
    lines = [
        str(_PROT_VER),
        "0",  # rot_model (Platzhalter, Client ignoriert diese Zeile)
        f"min_az={_MIN_AZ:.6f}",
        f"max_az={_MAX_AZ:.6f}",
        f"min_el={_MIN_EL:.6f}",
        f"max_el={_MAX_EL:.6f}",
        f"rot_type={rot_type}",
        "south_zero=0",
        "done",
    ]
    return "\n".join(lines) + "\n"


def process_rotctld_line(
    line: str, ctrl, log: Optional[LogBuffer] = None
) -> Tuple[Optional[str], bool]:
    """Eine rotctld-Protokollzeile verarbeiten.

    Rueckgabe: ``(antwort, schliessen)``.
      - ``antwort`` ist der zu sendende Text (bereits mit ``\\n`` terminiert)
        oder ``None`` (nichts senden).
      - ``schliessen`` = True beendet die Verbindung (``q``/``Q``).

    Das Protokoll ist zeilenbasiert (siehe rotctld(1)); GET-Befehle liefern die
    Werte als Klartextzeilen, SET-Befehle antworten mit ``RPRT <code>`` (0 = OK).
    """
    raw = (line or "").strip()
    if raw == "":
        return (None, False)

    # Erweitertes Antwortformat (Praefix +/;/|/,) wird nicht separat unterstuetzt;
    # das Zeichen wird entfernt und im Standardformat geantwortet.
    if raw[0] in "+;|,":
        raw = raw[1:].strip()
        if raw == "":
            return (None, False)

    parts = raw.split()
    cmd = parts[0]
    args = parts[1:]

    az_enabled = bool(getattr(ctrl, "enable_az", True))
    el_enabled = bool(getattr(ctrl, "enable_el", True))

    # --- get_pos: p ---------------------------------------------------------
    if cmd in ("p", "\\get_pos", "get_pos"):
        try:
            az_d10 = int(getattr(ctrl.az, "pos_d10", 0) or 0) if az_enabled else 0
        except Exception:
            az_d10 = 0
        try:
            el_d10 = int(getattr(ctrl.el, "pos_d10", 0) or 0) if el_enabled else 0
        except Exception:
            el_d10 = 0
        return (f"{_fmt_deg_from_d10(az_d10)}\n{_fmt_deg_from_d10(el_d10)}\n", False)

    # --- set_pos: P <az> <el> ----------------------------------------------
    if cmd in ("P", "\\set_pos", "set_pos"):
        if len(args) < 2:
            return ("RPRT -8\n", False)
        try:
            az_deg = float(args[0])
            el_deg = float(args[1])
        except (ValueError, TypeError):
            return ("RPRT -8\n", False)
        try:
            if az_enabled:
                ctrl.set_az_from_spid(int(round(az_deg * 10.0)))
            if el_enabled:
                ctrl.set_el_from_spid(int(round(el_deg * 10.0)))
            if log is not None:
                log.write("ROTCTLD", f"set_pos AZ={az_deg:.2f} EL={el_deg:.2f}")
        except Exception as e:
            if log is not None:
                log.write("ERROR", f"rotctld set_pos fehlgeschlagen: {e}")
            return ("RPRT -1\n", False)
        return ("RPRT 0\n", False)

    # --- stop: S ------------------------------------------------------------
    if cmd in ("S", "\\stop", "stop"):
        try:
            if az_enabled:
                ctrl.hold_az_at_current_pos()
            if el_enabled:
                ctrl.hold_el_at_current_pos()
            if log is not None:
                log.write("ROTCTLD", "stop")
        except Exception as e:
            if log is not None:
                log.write("ERROR", f"rotctld stop fehlgeschlagen: {e}")
            return ("RPRT -1\n", False)
        return ("RPRT 0\n", False)

    # --- park: K (wie Stop: auf aktueller Position halten) ------------------
    if cmd in ("K", "\\park", "park"):
        try:
            if az_enabled:
                ctrl.hold_az_at_current_pos()
            if el_enabled:
                ctrl.hold_el_at_current_pos()
            if log is not None:
                log.write("ROTCTLD", "park")
        except Exception:
            return ("RPRT -1\n", False)
        return ("RPRT 0\n", False)

    # --- move / reset: als No-op bestaetigen -------------------------------
    if cmd in ("M", "\\move", "move", "R", "\\reset", "reset"):
        return ("RPRT 0\n", False)

    # --- get_info: _ --------------------------------------------------------
    if cmd in ("_", "\\get_info", "get_info"):
        return (f"{_MODEL_NAME}\n", False)

    # --- dump_state ---------------------------------------------------------
    if cmd in ("\\dump_state", "dump_state"):
        return (build_dump_state(az_enabled=az_enabled, el_enabled=el_enabled), False)

    # --- dump_caps: 1 (nur Bestaetigung) -----------------------------------
    if cmd in ("1", "\\dump_caps", "dump_caps"):
        return ("RPRT 0\n", False)

    # --- quit: q / Q --------------------------------------------------------
    if cmd in ("q", "Q", "\\quit", "quit"):
        return (None, True)

    # Unbekannter Befehl -> RPRT -11 (RIG_ENIMPL)
    return ("RPRT -11\n", False)


class RotctldServer:
    """Hamlib-kompatibler rotctld-TCP-Server (mehrere Clients gleichzeitig).

    Anwendungen (gpredict, SatNOGS, Linux-/Satelliten-Software ...) koennen die
    Rotorposition abfragen (``p``) und den Rotor steuern (``P <az> <el>``).
    """

    def __init__(self, host: str, port: int, controller, log: LogBuffer):
        self.host = host
        self.port = int(port)
        self.ctrl = controller
        self.log = log
        self.running = False
        self._thread: Optional[threading.Thread] = None
        self._listen_sock: Optional[socket.socket] = None
        self._clients: list[socket.socket] = []
        self._clients_lock = threading.Lock()
        # Zeitstempel der letzten gueltigen Client-Aktivitaet (fuer UI-LED).
        self.last_rx_ts: float = 0.0

    def start(self) -> None:
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        self.log.write(
            "INFO", f"Hamlib rotctld-Server gestartet auf {self.host}:{self.port}"
        )

    def stop(self) -> None:
        self.running = False
        try:
            if self._listen_sock:
                self._listen_sock.close()
        except Exception:
            pass
        self._listen_sock = None
        # Offene Client-Sockets schliessen, damit deren Threads enden.
        with self._clients_lock:
            clients = list(self._clients)
            self._clients.clear()
        for c in clients:
            try:
                c.close()
            except Exception:
                pass

    def restart(self, host: str, port: int) -> None:
        self.stop()
        time.sleep(0.2)
        self.host = host
        self.port = int(port)
        self.start()

    def _loop(self) -> None:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listen_sock = s
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.settimeout(0.5)  # damit stop() schnell wirkt
        try:
            s.bind((self.host, self.port))
            s.listen(5)
        except Exception as e:
            self.log.write("ERROR", f"rotctld bind/listen fehlgeschlagen: {e}")
            self.running = False
            try:
                s.close()
            except Exception:
                pass
            self._listen_sock = None
            return

        while self.running:
            try:
                c, addr = s.accept()
            except socket.timeout:
                continue
            except Exception:
                break
            t = threading.Thread(target=self._client_loop, args=(c, addr), daemon=True)
            t.start()

        try:
            s.close()
        except Exception:
            pass
        self._listen_sock = None
        self.running = False

    def _client_loop(self, conn: socket.socket, addr) -> None:
        self.log.write("INFO", f"rotctld verbunden: {addr}")
        with self._clients_lock:
            self._clients.append(conn)
        try:
            conn.settimeout(0.5)
            buf = b""
            while self.running:
                try:
                    chunk = conn.recv(4096)
                except socket.timeout:
                    continue
                except Exception:
                    break
                if not chunk:
                    break
                buf += chunk
                # Zeilenweise verarbeiten (Trenner \n, \r\n und \r zulassen).
                while True:
                    idx = _find_line_end(buf)
                    if idx < 0:
                        break
                    line_bytes = buf[:idx]
                    # folgendes \n nach einem \r ueberspringen
                    nxt = idx + 1
                    if (
                        idx < len(buf)
                        and buf[idx : idx + 1] == b"\r"
                        and nxt < len(buf)
                        and buf[nxt : nxt + 1] == b"\n"
                    ):
                        nxt += 1
                    buf = buf[nxt:]
                    try:
                        line = line_bytes.decode("ascii", errors="ignore")
                    except Exception:
                        line = ""
                    try:
                        self.last_rx_ts = time.time()
                    except Exception:
                        pass
                    resp, close = process_rotctld_line(line, self.ctrl, self.log)
                    if resp:
                        try:
                            conn.sendall(resp.encode("ascii", errors="ignore"))
                        except Exception:
                            close = True
                    if close:
                        buf = b""
                        raise _ClientQuit()
        except _ClientQuit:
            pass
        except Exception:
            pass
        finally:
            with self._clients_lock:
                try:
                    self._clients.remove(conn)
                except ValueError:
                    pass
            try:
                conn.close()
            except Exception:
                pass
            self.log.write("INFO", f"rotctld getrennt: {addr}")


class _ClientQuit(Exception):
    """Interner Marker: Client hat ``q`` gesendet."""


def _find_line_end(buf: bytes) -> int:
    """Index des ersten Zeilenende-Zeichens (\\n oder \\r) oder -1."""
    i_n = buf.find(b"\n")
    i_r = buf.find(b"\r")
    if i_n < 0:
        return i_r
    if i_r < 0:
        return i_n
    return min(i_n, i_r)
