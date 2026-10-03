"""Wiederverwendbarer zeilenbasierter TCP-/Serial-Server für Rotor-Emulationen."""

from __future__ import annotations

import socket
import threading
import time
from typing import Any, Callable, Optional

from .logutil import LogBuffer
from .net_bind_error import format_bind_error

ProcessLineFn = Callable[[str], tuple[Optional[str], bool]]

try:
    import serial  # pyserial
except Exception:  # pragma: no cover
    serial = None  # type: ignore[assignment]


class _ClientQuit(Exception):
    pass


def find_line_end(buf: bytes, terminators: bytes = b"\r\n") -> int:
    """Index des ersten Terminators in ``terminators`` oder -1."""
    best = -1
    for t in terminators or b"\r\n":
        i = buf.find(bytes([t]))
        if i >= 0 and (best < 0 or i < best):
            best = i
    return best


def _normalize_port(name: str) -> str:
    s = str(name or "").strip()
    if not s:
        return s
    if s.startswith("\\\\.\\"):
        return s
    up = s.upper()
    if up.startswith("COM") or up.startswith("CNCA") or up.startswith("CNCB"):
        return "\\\\.\\" + s
    return s


class LineProtocolTcpServer:
    """TCP-Server: eine Zeile → ``process_line(line) -> (antwort|None, close)``."""

    def __init__(
        self,
        host: str,
        port: int,
        log: LogBuffer,
        *,
        name: str,
        process_line: ProcessLineFn,
        cfg_section: str = "",
        terminators: bytes = b"\r\n",
        extract_commands: Optional[Callable[[bytes], tuple[list[str], bytes]]] = None,
    ):
        self.host = str(host or "127.0.0.1")
        self.port = int(port)
        self.log = log
        self.name = name
        self._process_line = process_line
        self.cfg_section = cfg_section
        self._terminators = terminators or b"\r\n"
        self._extract_commands = extract_commands
        self.running = False
        self._thread: Optional[threading.Thread] = None
        self._listen_sock: Optional[socket.socket] = None
        self._clients: list[socket.socket] = []
        self._clients_lock = threading.Lock()
        self.last_rx_ts: float = 0.0
        self.bind_error_msg: str | None = None

    def start(self) -> None:
        if self.running:
            return
        self.bind_error_msg = None
        if not self._open_listen_socket():
            return
        self.running = True
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name=f"{self.name}-tcp"
        )
        self._thread.start()
        self.log.write("INFO", f"{self.name} gestartet auf {self.host}:{self.port}")

    def stop(self) -> None:
        was = self.running
        self.running = False
        try:
            if self._listen_sock:
                self._listen_sock.close()
        except Exception:
            pass
        self._listen_sock = None
        with self._clients_lock:
            clients = list(self._clients)
            self._clients.clear()
        for c in clients:
            try:
                c.close()
            except Exception:
                pass
        if was:
            self.log.write("INFO", f"{self.name} gestoppt")

    def restart(self, host: str, port: int) -> None:
        self.stop()
        time.sleep(0.15)
        self.host = str(host or "127.0.0.1")
        self.port = int(port)
        self.start()

    def _open_listen_socket(self) -> bool:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.settimeout(0.5)
            s.bind((self.host, self.port))
            s.listen(5)
        except Exception as e:
            self.bind_error_msg = format_bind_error(
                self.host, self.port, e, proto_name=self.name
            )
            self.log.write("ERROR", f"{self.name} bind/listen fehlgeschlagen: {e}")
            try:
                s.close()
            except Exception:
                pass
            self._listen_sock = None
            return False
        self._listen_sock = s
        return True

    def _loop(self) -> None:
        s = self._listen_sock
        if s is None:
            self.running = False
            return
        while self.running:
            try:
                c, addr = s.accept()
            except socket.timeout:
                continue
            except Exception:
                break
            threading.Thread(
                target=self._client_loop, args=(c, addr), daemon=True
            ).start()

        try:
            s.close()
        except Exception:
            pass
        self._listen_sock = None
        self.running = False

    def _client_loop(self, conn: socket.socket, addr) -> None:
        self.log.write("INFO", f"{self.name} verbunden: {addr}")
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
                commands: list[str] = []
                if self._extract_commands is not None:
                    commands, buf = self._extract_commands(buf)
                    if commands:
                        try:
                            self.last_rx_ts = time.time()
                        except Exception:
                            pass
                    for line in commands:
                        resp, close = self._process_line(line)
                        if resp:
                            try:
                                conn.sendall(resp.encode("ascii", errors="ignore"))
                            except Exception:
                                close = True
                        if close:
                            raise _ClientQuit()
                    continue
                while True:
                    idx = find_line_end(buf, self._terminators)
                    if idx < 0:
                        break
                    line_bytes = buf[:idx]
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
                    resp, close = self._process_line(line)
                    if resp:
                        try:
                            conn.sendall(resp.encode("ascii", errors="ignore"))
                        except Exception:
                            close = True
                    if close:
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
            self.log.write("INFO", f"{self.name} getrennt: {addr}")


class LineProtocolSerialPort:
    """Serieller Zeilen-Listener (com0com) mit demselben Parser wie der TCP-Server."""

    def __init__(
        self,
        port: str,
        baudrate: int,
        log: LogBuffer,
        *,
        name: str,
        process_line: ProcessLineFn,
        terminators: bytes = b"\r\n",
        target: str = "",
        extract_commands: Optional[Callable[[bytes], tuple[list[str], bytes]]] = None,
    ) -> None:
        self.port = str(port or "").strip()
        self.baudrate = int(baudrate or 9600)
        self.log = log
        self.name = name
        self.target = target
        self._process_line = process_line
        self._terminators = terminators or b"\r\n"
        self._extract_commands = extract_commands
        self.running = False
        self._thread: Optional[threading.Thread] = None
        self._ser: Optional[Any] = None
        self.last_rx_ts: float = 0.0
        self.last_error: str = ""

    def start(self) -> None:
        if self.running:
            return
        if serial is None:
            self.log.write("ERROR", f"{self.name} {self.port}: pyserial fehlt")
            return
        self.running = True
        self._thread = threading.Thread(
            target=self._loop, name=f"{self.name}-{self.port}", daemon=True
        )
        self._thread.start()
        self.log.write(
            "INFO", f"{self.name} gestartet auf {self.port} @ {self.baudrate}"
        )

    def stop(self) -> None:
        self.running = False
        try:
            if self._ser is not None:
                self._ser.close()
        except Exception:
            pass
        self._ser = None

    def _safe_close(self) -> None:
        try:
            if self._ser is not None:
                self._ser.close()
        except Exception:
            pass
        self._ser = None

    def _open_port(self) -> Any:
        assert serial is not None
        return serial.Serial(
            _normalize_port(self.port),
            baudrate=self.baudrate,
            bytesize=8,
            parity="N",
            stopbits=1,
            timeout=0.05,
            write_timeout=0.5,
        )

    def _loop(self) -> None:
        buf = b""
        idle_since: float | None = None
        while self.running:
            while self.running and self._ser is None:
                try:
                    self._ser = self._open_port()
                    self.last_error = ""
                    self.log.write("INFO", f"{self.name} {self.port} offen")
                except Exception as extra:
                    self.last_error = str(extra)
                    self.log.write(
                        "WARN", f"{self.name} {self.port} open fehlgeschlagen: {extra}"
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
                chunk = ser.read(64)
            except Exception as extra:
                self.last_error = str(extra)
                self.log.write("WARN", f"{self.name} {self.port} read-Fehler: {extra}")
                self._safe_close()
                buf = b""
                idle_since = None
                continue

            if chunk:
                buf += chunk
                idle_since = None
            elif buf and self._extract_commands is not None:
                now = time.time()
                if idle_since is None:
                    idle_since = now
                if now - idle_since < 0.08:
                    continue
                if buf[-1:] not in b"\r\n":
                    buf += b"\r"
                idle_since = None
            else:
                continue

            commands: list[str] = []
            if self._extract_commands is not None:
                commands, buf = self._extract_commands(buf)
            else:
                while True:
                    idx = find_line_end(buf, self._terminators)
                    if idx < 0:
                        break
                    line_bytes = buf[:idx]
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
                        commands.append(line_bytes.decode("ascii", errors="ignore"))
                    except Exception:
                        commands.append("")
            if not commands:
                continue
            try:
                self.last_rx_ts = time.time()
            except Exception:
                pass
            for line in commands:
                resp, _close = self._process_line(line)
                if not resp:
                    continue
                try:
                    ser.write(resp.encode("ascii", errors="ignore"))
                except Exception as extra:
                    self.last_error = str(extra)
                    self.log.write(
                        "WARN", f"{self.name} {self.port} write-Fehler: {extra}"
                    )
                    self._safe_close()
                    buf = b""
                    idle_since = None
                    break
