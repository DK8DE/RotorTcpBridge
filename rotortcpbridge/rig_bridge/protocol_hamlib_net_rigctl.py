"""Hamlib NET rigctl kompatibler TCP-Server (rigctld-Zeilenprotokoll).

Ziel ist **libhamlib-kompatible** Textbefehle wie ``rigctld``/``netrigctl`` —
jede Anwendung mit Modell „NET rigctl“ (WSJT-X, fldigi, eigene Skripte, …)
spricht dasselbe Protokoll; es gibt **keine** WSJT-X-spezifischen Sonderpfade.
"""

from __future__ import annotations

import socket
import threading
import time
from typing import Any, Callable

from .rigctld_dump_state import build_rigctld_dump_state_block
from .utils import bind_tcp_listen_socket


def _try_extract_rigctld_line(buf: bytes) -> tuple[bytes, bytes, bool] | None:
    """Erstes vollständiges Telegramm.

    Rückgabe ``(line_bytes, rest_buf, used_semicolon_framing)``.

    - rigctld/Hamlib: Zeilenende ``\\r\\n``, ``\\n`` oder ``\\r`` (dritter Wert
      ``False``).
    - Manche Programme schicken stattdessen **nur CAT mit ``;``** (z. B.
      ``PS;``) ohne Zeilenumbruch auf den rigctld-Port — dann ``True``, damit
      wir einmalig darauf hinweisen können (QLog muss **NET rigctl** nutzen).
    """
    if not buf:
        return None
    crlf = buf.find(b"\r\n")
    lf = buf.find(b"\n")
    cr = buf.find(b"\r")
    candidates: list[tuple[int, int]] = []
    if crlf >= 0:
        candidates.append((crlf, 2))
    if lf >= 0:
        candidates.append((lf, 1))
    if cr >= 0:
        candidates.append((cr, 1))
    if candidates:
        cut, sep_len = min(candidates, key=lambda t: t[0])
        line = buf[:cut]
        rest = buf[cut + sep_len :]
        return (line, rest, False)
    # Kein Newline: Yaesu-/CAT-artig bis zum ersten ';' (max. 128 Zeichen)
    semi = buf.find(b";")
    if semi <= 0:
        return None
    body = buf[:semi]
    if len(body) + 1 > 128:
        return None
    if any(b < 32 or b > 126 for b in body):
        return None
    return (body, buf[semi + 1 :], True)


def _rigctld_vfo_name_to_internal(name: str) -> str:
    """Rigctld-Namen (VFOA, Main, …) auf internes Kurzfeld (A/B)."""
    t = (name or "").strip().upper()
    if t in ("VFOA", "A", "MAINA", "MAIN"):
        return "A"
    if t in ("VFOB", "B", "MAINB", "SUB", "SUBA", "SUBB"):
        return "B"
    return "A"


def _internal_vfo_to_rigctld(short: str) -> str:
    """Intern A/B → Antwort für ``v`` / ``rig_parse_vfo``."""
    s = (short or "A").strip().upper()
    if s == "B":
        return "VFOB"
    return "VFOA"


def _mode_pb_width_hz(mode: str) -> int:
    """Passbandbreite (Hz) für zweite Zeile bei ``m`` (Hamlib liest zwei Zeilen)."""
    m = (mode or "USB").strip().upper()
    if m in ("CW", "CWR", "CWN"):
        return 500
    if m in ("FM", "PKTFM"):
        return 15000
    if m in ("WFM",):
        return 120000
    if m in ("USB", "LSB", "AM", "DIG", "PKTUSB", "PKTLSB", "RTTY", "RTTYR"):
        return 2400
    return 0


def _parse_frequency_token_to_hz(token: str) -> int | None:
    """Ein Token als Frequenz in Hz (Hamlib nutzt oft ``double``, z. B. ``144300055.000000``)."""
    s = (token or "").strip().replace(",", ".")
    if not s or s == ".":
        return None
    try:
        val = float(s)
    except ValueError:
        return None
    if val <= 0 or val > 1e12:
        return None
    hz = int(round(val))
    return hz if hz > 0 else None


def _parse_set_freq_hz(cmd: str) -> int | None:
    """``F …`` / ``\\set_freq …`` → Hz (Hamlib: letztes numerisches Token, Fließkomma erlaubt)."""
    parts = (cmd or "").strip().split()
    if len(parts) < 2:
        return None
    key = parts[0]
    if key == "F":
        toks = parts[1:]
    elif key.lower() == "\\set_freq":
        toks = parts[1:]
    else:
        return None
    if not toks:
        return None
    for tok in reversed(toks):
        hz = _parse_frequency_token_to_hz(tok)
        if hz is not None:
            return hz
    return None


def _looks_like_rigctld_vfo_token(tok: str) -> bool:
    """True, wenn ``tok`` wie ein optionales VFO-Argument vor dem Modus aussieht."""
    t = (tok or "").strip().upper()
    if not t:
        return False
    if t.startswith("VFO") or t in ("MAIN", "SUB", "A", "B", "CURR"):
        return True
    if t.startswith("MAIN") or t.startswith("SUB"):
        return True
    return False


def _parse_set_mode_token(cmd: str) -> str | None:
    """``M [VFO] MODE WIDTH`` → MODE-String."""
    parts = (cmd or "").strip().split()
    if len(parts) < 2 or parts[0] != "M":
        return None
    rest = parts[1:]
    if len(rest) >= 3:
        # M VFOA USB 2400
        return rest[1]
    if len(rest) >= 2:
        # M USB 2400  oder  M VFOA USB
        if _looks_like_rigctld_vfo_token(rest[0]):
            return rest[1]
        return rest[0]
    return None


def _parse_set_ptt_int(cmd: str) -> int | None:
    """``T [VFO] 0|1`` → PTT-Wert."""
    parts = (cmd or "").strip().split()
    if len(parts) < 2 or parts[0].upper() != "T":
        return None
    try:
        return int(parts[-1])
    except ValueError:
        return None


def _strip_cmd_vfo_prefix(cmd: str, letter: str) -> bool:
    """True, wenn ``letter`` oder ``letter <vfo>`` (z. B. ``f VFOA``)."""
    c = (cmd or "").strip()
    if c == letter:
        return True
    return c.startswith(f"{letter} ")


class HamlibNetRigctlServer:
    """Minimaler rigctl-Server über TCP."""

    def __init__(
        self,
        get_state: Callable[[], dict],
        enqueue_write: Callable[..., None],
        on_clients_changed: Callable[[int], None],
        log_write: Callable[[str, str], None],
        on_state_patch: Callable[[dict[str, Any]], None] | None = None,
        debug_traffic: bool = False,
        log_serial_traffic: bool = True,
        log_tcp_traffic: bool = False,
        log_label: str = "",
        on_tcp_activity: Callable[[], None] | None = None,
        refresh_frequency_for_read: Callable[[], bool] | None = None,
    ):
        self._get_state = get_state
        self._enqueue_write = enqueue_write
        self._on_clients_changed = on_clients_changed
        self._log_write = log_write
        self._on_state_patch = on_state_patch
        self._on_tcp_activity = on_tcp_activity or (lambda: None)
        self._refresh_frequency_for_read = refresh_frequency_for_read
        self._debug_traffic = bool(debug_traffic)
        self._log_serial_traffic = bool(log_serial_traffic)
        self._log_tcp_traffic = bool(log_tcp_traffic)
        self._log_label = str(log_label or "").strip()
        self._sock = None
        self._running = False
        self._clients: set[socket.socket] = set()
        self._listen_host: str = ""
        self._listen_port: int = 0
        self._accept_thread: threading.Thread | None = None
        self._lifecycle_lock = threading.Lock()

    def _log_pfx(self) -> str:
        if self._log_label:
            return f"{self._log_label}: "
        return ""

    def set_debug_traffic(self, enabled: bool) -> None:
        """Laufzeit: Diagnose für TCP rigctld (Einstellung „Hamlib … Diagnose-Log“)."""
        self._debug_traffic = bool(enabled)

    def set_log_serial_traffic(self, enabled: bool) -> None:
        """TCP rigctld-Zeilen ins Rig-Diagnose-Log (Einstellung „Rig-Befehle loggen“)."""
        self._log_serial_traffic = bool(enabled)

    def set_log_tcp_traffic(self, enabled: bool) -> None:
        """Nur Hamlib-TCP (rigctl-Zeilen) ins Hauptlog, ohne volles COM-Protokoll."""
        self._log_tcp_traffic = bool(enabled)

    def _net_log_enabled(self) -> bool:
        return bool(
            self._debug_traffic or self._log_serial_traffic or self._log_tcp_traffic
        )

    def start(self, host: str, port: int) -> None:
        """Server starten; bei geändertem Host/Port vorher sauber neu binden."""
        with self._lifecycle_lock:
            host = str(host or "127.0.0.1").strip() or "127.0.0.1"
            port = int(port)
            if self._running and self._listen_host == host and self._listen_port == port:
                return
            if self._running:
                self._stop_unlocked()
            s = bind_tcp_listen_socket(host, port)
            s.listen(8)
            self._sock = s
            self._listen_host = host
            self._listen_port = port
            self._running = True
            self._accept_thread = threading.Thread(target=self._accept_loop, daemon=True)
            self._accept_thread.start()
            if self._net_log_enabled():
                self._log_write(
                    "INFO",
                    f"{self._log_pfx()}Hamlib rigctl (rigctld-kompatibel) lauscht auf {host}:{port} "
                    f"(IPv4+IPv6 über ::, falls vom System unterstützt)",
                )

    def stop(self) -> None:
        with self._lifecycle_lock:
            self._stop_unlocked()

    def _stop_unlocked(self) -> None:
        """Listen-Socket und Clients zuverlässig schließen; Accept-Thread beenden (ohne Lock)."""
        self._running = False
        for c in list(self._clients):
            try:
                c.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            try:
                c.close()
            except Exception:
                pass
        self._clients.clear()
        ls = self._sock
        self._sock = None
        self._listen_host = ""
        self._listen_port = 0
        if ls is not None:
            try:
                ls.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            try:
                ls.close()
            except Exception:
                pass
        self._on_clients_changed(0)
        t = self._accept_thread
        self._accept_thread = None
        if t is not None and t.is_alive():
            t.join(timeout=4.0)

    def _accept_loop(self) -> None:
        while self._running and self._sock is not None:
            try:
                c, _ = self._sock.accept()
            except OSError:
                break
            except Exception:
                if not self._running:
                    break
                continue
            if not self._running:
                try:
                    c.close()
                except Exception:
                    pass
                break
            self._clients.add(c)
            self._on_clients_changed(len(self._clients))
            threading.Thread(target=self._client_loop, args=(c,), daemon=True).start()

    def _client_loop(self, client: socket.socket) -> None:
        peer = "?"
        try:
            a = client.getpeername()
            peer = f"{a[0]}:{a[1]}"
        except Exception:
            pass
        try:
            with client:
                if self._net_log_enabled():
                    self._log_write(
                        "INFO",
                        f"{self._log_pfx()}Hamlib NET Sitzung von {peer} (rigctld)",
                    )
                try:
                    client.settimeout(180.0)
                except Exception:
                    pass
                dbg_last_rx = time.monotonic()
                buf = b""
                last_partial_log_mono = 0.0
                warned_semicolon_framing = False
                abort_session = False
                while True:
                    if not self._running:
                        break
                    try:
                        chunk = client.recv(1024)
                    except socket.timeout:
                        if self._net_log_enabled():
                            self._log_write(
                                "WARN",
                                f"{self._log_pfx()}Hamlib NET: recv Timeout (180 s) {peer}, "
                                f"Zwischenpuffer {len(buf)} Byte — Verbindung wird getrennt",
                            )
                        break
                    if not chunk:
                        break
                    buf += chunk
                    while True:
                        if not self._running:
                            buf = b""
                            abort_session = True
                            break
                        extracted = _try_extract_rigctld_line(buf)
                        if extracted is None:
                            break
                        line, buf, used_semicolon_framing = extracted
                        cmd = line.decode("ascii", errors="ignore").strip()
                        if (
                            used_semicolon_framing
                            and self._net_log_enabled()
                            and not warned_semicolon_framing
                        ):
                            warned_semicolon_framing = True
                            self._log_write(
                                "WARN",
                                f"{self._log_pfx()}Hamlib NET: Befehl nur mit Semikolon begrenzt "
                                f"({cmd!r}), ohne rigctld-Zeilenumbruch (\\n). "
                                f"In QLog Hamlib NET rigctl / rigctld nutzen — nicht "
                                f"serielles Yaesu-CAT (…;) auf denselben TCP-Port.",
                            )
                        try:
                            self._on_tcp_activity()
                        except Exception:
                            pass
                        if self._net_log_enabled():
                            now = time.monotonic()
                            dt_ms = (now - dbg_last_rx) * 1000.0
                            dbg_last_rx = now
                            if "dump_state" in cmd.lower():
                                self._log_write(
                                    "INFO",
                                    f"{self._log_pfx()}Hamlib NET RX (+{dt_ms:.1f} ms): dump_state",
                                )
                            elif self._debug_traffic:
                                self._log_write(
                                    "INFO",
                                    f"{self._log_pfx()}Hamlib NET RX (+{dt_ms:.1f} ms): {cmd!r}",
                                )
                            else:
                                self._log_write(
                                    "INFO",
                                    f"{self._log_pfx()}Hamlib NET RX: {cmd!r}",
                                )
                        if not self._running:
                            break
                        out = self._handle_cmd(cmd, quiet_unknown=used_semicolon_framing)
                        if out and self._running:
                            if self._net_log_enabled():
                                if len(out) > 400 or "dump_state" in cmd.lower():
                                    self._log_write(
                                        "INFO",
                                        f"{self._log_pfx()}Hamlib NET TX: {len(out)} Zeichen Antwort",
                                    )
                                else:
                                    pv = out.replace("\r", "").replace("\n", "\\n")
                                    if len(pv) > 200:
                                        pv = pv[:197] + "..."
                                    self._log_write("INFO", f"{self._log_pfx()}Hamlib NET TX: {pv!r}")
                            payload = out.encode("ascii", errors="ignore")
                            if not payload.endswith(b"\n"):
                                payload += b"\n"
                            try:
                                client.sendall(payload)
                            except socket.timeout:
                                if self._net_log_enabled():
                                    self._log_write(
                                        "WARN",
                                        f"{self._log_pfx()}Hamlib NET: send Timeout (180 s) {peer}",
                                    )
                                abort_session = True
                                break
                            except Exception:
                                abort_session = True
                                break
                    if abort_session:
                        break
                    if len(buf) > 8192:
                        if self._net_log_enabled():
                            hx = buf[:64].hex()
                            self._log_write(
                                "WARN",
                                f"{self._log_pfx()}Hamlib NET: Puffer >8192 B ohne gültiges "
                                f"Zeilenende — verworfen (hex_start={hx}…)",
                            )
                        buf = b""
                    elif buf and self._net_log_enabled():
                        now_mono = time.monotonic()
                        if now_mono - last_partial_log_mono >= 15.0:
                            last_partial_log_mono = now_mono
                            hx = buf[:64].hex()
                            self._log_write(
                                "INFO",
                                f"{self._log_pfx()}Hamlib NET: warte auf Zeilenende "
                                f"({len(buf)} B empfangen, hex_start={hx}…)",
                            )
        except Exception as exc:
            if self._net_log_enabled():
                try:
                    self._log_write(
                        "WARN",
                        f"{self._log_pfx()}Hamlib NET Sitzung {peer}: {exc!r}",
                    )
                except Exception:
                    pass
        finally:
            if self._net_log_enabled():
                try:
                    self._log_write(
                        "INFO",
                        f"{self._log_pfx()}Hamlib NET Sitzung beendet ({peer})",
                    )
                except Exception:
                    pass
            self._clients.discard(client)
            self._on_clients_changed(len(self._clients))

    def _freq_hz_line(self, st: dict) -> str:
        """Eine Zeile für rigctld ``f``: zuletzt bekannte Hz (Software), kein CAT-Lesevorgang."""
        hz = int(st.get("frequency_hz", 0) or 0)
        return str(max(0, hz))

    def _handle_cmd(self, cmd: str, *, quiet_unknown: bool = False) -> str:
        """rigctld-Zeilenprotokoll; unbekannte Befehle mit RPRT -11."""
        if not self._running:
            return ""
        st = self._get_state()
        cmd = (cmd or "").strip()
        if not cmd or cmd.startswith("#"):
            return ""

        parts0 = cmd.split()

        # --- VFO set (WSJT-X: ``V VFOA``) ---
        if cmd.startswith("V ") or cmd.startswith("\\set_vfo "):
            if cmd.startswith("V "):
                name = cmd[2:].strip()
            else:
                tail = cmd.split(None, 1)
                name = tail[1].strip() if len(tail) > 1 else ""
            short = _rigctld_vfo_name_to_internal(name)
            if self._on_state_patch is not None:
                self._on_state_patch({"vfo": short})
            return "RPRT 0"

        # --- get/set freq (optional VFO-Suffix wie ``f VFOA``, ``F VFOA 14074000``) ---
        # ``f``: Anzeigefrequenz — nach Möglichkeit zuerst CAT-READ (VFO am TRX), sonst RAM-State.
        if cmd in ("f", "\\get_freq") or _strip_cmd_vfo_prefix(cmd, "f"):
            if self._refresh_frequency_for_read is not None:
                try:
                    self._refresh_frequency_for_read()
                except Exception:
                    pass
                st = self._get_state()
            return self._freq_hz_line(st)
        if cmd.startswith("F ") or (parts0 and parts0[0].lower() == "\\set_freq"):
            hz = _parse_set_freq_hz(cmd)
            if hz is None:
                return "RPRT -8"
            # Sofort gleiche Ziel-Frequenz für ``f``-Abfragen (Hamlib/UI), COM folgt asynchron.
            if self._on_state_patch is not None:
                self._on_state_patch({"frequency_hz": hz})
            self._enqueue_write(f"SETFREQ {hz}", "Hamlib NET rigctld → TRX")
            return "RPRT 0"

        # --- get_mode: zwei Zeilen (Modus, dann Passband) ---
        if cmd in ("m", "\\get_mode") or _strip_cmd_vfo_prefix(cmd, "m"):
            mode = str(st.get("mode", "USB"))
            w = _mode_pb_width_hz(mode)
            return f"{mode}\n{w}"

        if cmd.startswith("M ") or cmd.startswith("\\set_mode "):
            if cmd.startswith("M "):
                mode_tok = _parse_set_mode_token(cmd)
            else:
                mode_tok = _parse_set_mode_token(cmd.replace("\\set_mode ", "M ", 1))
            if not mode_tok:
                return "RPRT -8"
            self._enqueue_write(f"SETMODE {mode_tok}", "Hamlib NET rigctld → TRX")
            if self._on_state_patch is not None:
                self._on_state_patch({"mode": mode_tok})
            return "RPRT 0"

        if cmd in ("t", "\\get_ptt") or _strip_cmd_vfo_prefix(cmd, "t"):
            return "1" if st.get("ptt", False) else "0"

        if cmd.upper().startswith("T ") or cmd.startswith("\\set_ptt "):
            if cmd.startswith("\\set_ptt "):
                raw = "T " + cmd.split(None, 1)[1].strip()
            else:
                raw = cmd
            ptt = _parse_set_ptt_int(raw)
            if ptt is None:
                return "RPRT -8"
            self._enqueue_write(f"SETPTT {ptt}", "Hamlib NET rigctld → TRX")
            if self._on_state_patch is not None:
                self._on_state_patch({"ptt": bool(ptt)})
            return "RPRT 0"

        if cmd in ("v", "\\get_vfo"):
            return _internal_vfo_to_rigctld(str(st.get("vfo", "A")))

        if cmd in ("q", "\\quit"):
            return "RPRT 0"

        if cmd in ("s", "\\get_split_vfo"):
            return "0"
        if cmd in ("i", "\\get_split_freq"):
            return self._freq_hz_line(st)
        if cmd in ("n", "\\get_ts"):
            return "0"
        if cmd in ("\\chk_vfo",):
            return "0"
        if cmd in ("\\dump_state", "dump_state"):
            return build_rigctld_dump_state_block()
        if cmd in ("\\get_powerstat",):
            return "1"
        if cmd.startswith("\\set_conf "):
            return "RPRT 0"
        if self._net_log_enabled() and not quiet_unknown:
            self._log_write(
                "WARN",
                f"{self._log_pfx()}Hamlib NET: unbekannter Befehl {cmd!r} → RPRT -11",
            )
        return "RPRT -11"
