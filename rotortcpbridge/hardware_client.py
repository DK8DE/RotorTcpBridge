from __future__ import annotations
import threading
import queue
import time
import socket
from dataclasses import dataclass
from typing import Optional, Callable
from .rs485_protocol import parse, Telegram
from .logutil import LogBuffer

try:
    import serial
except Exception:
    serial = None


@dataclass
class HwRequest:
    line: str
    expect_prefix: Optional[str] = None  # z.B. "ACK_GETPOSDG"
    timeout_s: float = 0.8
    on_done: Optional[Callable[[Optional[Telegram], Optional[str]], None]] = None
    sent_ts: float = 0.0
    priority: int = 5  # 0 = höchste Priorität (UI), 5 = normal (Polling)
    dont_disconnect_on_timeout: bool = False  # True: bei Timeout nicht trennen (z.B. für Retry)


class HardwareClient:
    """Spricht mit dem Hardware-Serial-Server (TCP oder COM).

    Fixes:
    - RX wird nicht mehr zeilenbasiert geparst, sondern anhand der Telegramm-Klammern:
      Start '#' und Ende '$'. Viele Serial-Server senden KEINE \\n-Zeilenenden!
    - Prioritäten: UI-Befehle (SETREF/STOP/SETPOSDG) laufen vor Polling, damit Buttons sofort wirken.
    """

    def __init__(self, cfg: dict, log: LogBuffer):
        # Eigene Kopie halten (nicht die externe Dict-Referenz),
        # damit spätere In-Place-Änderungen von außen erkannt werden.
        self.cfg = dict(cfg or {})
        self._applied_cfg = dict(self.cfg)
        self.log = log

        # PriorityQueue: kleinste priority zuerst
        # Wichtig: PriorityQueue muss immer eindeutig vergleichbare Keys haben.
        # Bei Burst-Sendungen können (priority, time.time()) identisch sein -> dann würde
        # Python versuchen, HwRequest zu vergleichen (TypeError). Daher nutzen wir
        # eine monoton steigende Sequence-ID als Tie-Breaker.
        self._txq: "queue.PriorityQueue[tuple[int,int,HwRequest]]" = queue.PriorityQueue()
        self._tx_lock = threading.Lock()
        self._tx_seq: int = 0

        self._running = False
        self._sock: Optional[socket.socket] = None
        self._ser = None
        self._reader_thread = None
        self._worker_thread = None

        self._rxbuf = b""
        self._pending: Optional[HwRequest] = None
        self._lock = threading.Lock()

        self.on_async_telegram: Optional[Callable[[Telegram], None]] = None
        # Antworten auf unsere Requests haben DST = eigene Master-ID; andere Master nicht als Pending matchen
        self._pending_reply_dst: int = 0
        self._last_rx_any_ts: float = 0.0
        self._last_tx_any_ts: float = 0.0
        self._connected_since_ts: float = 0.0
        # Bei COM-Modus ohne RS485-Bus kommen keine RX-Daten, auch wenn COM offen ist.
        # Daher für COM einen deutlich großzügigeren no-rx-Timeout nutzen,
        # damit nicht ständig dis/reconnect getriggert wird.
        self._no_rx_timeout_s: float = 5.0
        self._update_no_rx_timeout()

    def start(self):
        self._running = True
        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._worker_thread.start()
        self._reader_thread.start()

    def stop(self):
        self._running = False
        try:
            if self._sock:
                self._sock.close()
        except Exception:
            pass
        try:
            if self._ser:
                self._ser.close()
        except Exception:
            pass
        self._sock = None
        self._ser = None

    def is_connected(self) -> bool:
        return self._sock is not None or self._ser is not None

    def _update_no_rx_timeout(self) -> None:
        """no-rx-Timeout abhängig vom Verbindungstyp setzen.

        TCP: 5s (Serial-Server antwortet bei jeder Anfrage).
        COM: 30s (RS485-Bus kann still sein, wenn kein Rotor angeschlossen).
        """
        mode = str(self.cfg.get("mode", "tcp") or "tcp").strip().lower()
        self._no_rx_timeout_s = 30.0 if mode == "com" else 5.0

    def set_expected_response_dst(self, master_id: int) -> None:
        """Nur Telegramme mit ``dst == master_id`` dürfen ein ausstehendes TX-Match sein."""
        try:
            self._pending_reply_dst = int(master_id)
        except Exception:
            self._pending_reply_dst = 0

    def update_cfg(self, cfg: dict):
        old = dict(self._applied_cfg or {})
        new = dict(cfg or {})
        self.cfg = new
        self._applied_cfg = dict(new)
        self._update_no_rx_timeout()

        # Bei relevanter Änderung (Mode/Endpoint/Baud) bestehende Verbindung
        # aktiv trennen, damit der Worker sofort mit den neuen Werten reconnectet.
        relevant_keys = ("mode", "tcp_ip", "tcp_port", "com_port", "baudrate")
        changed = any(old.get(k) != new.get(k) for k in relevant_keys)
        if changed and self.is_connected():
            self._disconnect("cfg_changed")

    def send_request(self, req: HwRequest):
        # Wenn keine Verbindung steht, Polling-Requests nicht aufstauen.
        # Sie würden beim Reconnect sonst in einem Burst gesendet und den Serial-Server
        # überfluten; außerdem sind alte Polls wertlos, da sofort neue erzeugt werden.
        try:
            if (not self.is_connected()) and int(getattr(req, "priority", 5)) >= 5:
                return
        except Exception:
            pass

        # PriorityQueue braucht (priority, seq, item)
        with self._tx_lock:
            self._tx_seq += 1
            self._txq.put((int(req.priority), int(self._tx_seq), req))

    # ------------------ Connection helpers ------------------
    def _connect(self):
        mode = self.cfg.get("mode", "tcp")
        if mode == "tcp":
            ip = self.cfg.get("tcp_ip", "127.0.0.1")
            port = int(self.cfg.get("tcp_port", 23))
            try:
                s = socket.create_connection((ip, port), timeout=1.0)
                # TCP Keepalive hilft, harte Netzabbrüche zu erkennen (best effort).
                try:
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                    # Windows: Keepalive-Intervalle aggressiv setzen (ms),
                    # damit ein wegfallender Serial-Server schnell erkannt wird.
                    try:
                        if hasattr(socket, "SIO_KEEPALIVE_VALS"):
                            s.ioctl(socket.SIO_KEEPALIVE_VALS, (1, 1000, 1000))
                    except Exception:
                        pass
                except Exception:
                    pass
                s.settimeout(0.2)
                self._sock = s
                self._last_rx_any_ts = time.time()
                self._last_tx_any_ts = 0.0
                self._connected_since_ts = time.time()
                self.log.write("INFO", f"Hardware TCP verbunden {ip}:{port}")
            except Exception:
                self._sock = None
        else:
            if serial is None:
                return
            com = self.cfg.get("com_port", "COM1")
            baud = int(self.cfg.get("baudrate", 115200))
            try:
                self._ser = serial.Serial(com, baud, timeout=0.2)
                self._last_rx_any_ts = time.time()
                self._last_tx_any_ts = 0.0
                self._connected_since_ts = time.time()
                self.log.write("INFO", f"Hardware COM verbunden {com} @ {baud}")
            except Exception:
                self._ser = None

    def _write(self, data: bytes):
        if self._sock:
            self._sock.sendall(data)
            self._last_tx_any_ts = time.time()
        elif self._ser:
            self._ser.write(data)
            self._last_tx_any_ts = time.time()

    def _read_some(self) -> bytes:
        if self._sock:
            try:
                data = self._sock.recv(4096)
                # TCP: 0 Bytes bedeutet "Gegenstelle hat sauber geschlossen"
                # -> als Disconnect behandeln, damit Hardware-LED korrekt reagiert.
                if data == b"":
                    raise ConnectionResetError("tcp socket closed")
                if data:
                    self._last_rx_any_ts = time.time()
                return data
            except socket.timeout:
                return b""
        elif self._ser:
            try:
                data = self._ser.read(4096)
                if data:
                    self._last_rx_any_ts = time.time()
                return data
            except Exception:
                # USB-Adapter abgezogen etc. -> als Disconnect behandeln
                raise
        return b""

    def _disconnect(self, reason: str = "disconnected"):
        """Verbindung hart schließen + pending freigeben."""
        try:
            if self._sock:
                self._sock.close()
        except Exception:
            pass
        try:
            if self._ser:
                self._ser.close()
        except Exception:
            pass
        self._sock = None
        self._ser = None
        self._connected_since_ts = 0.0
        # TX-Queue entschärfen: nur UI-Requests behalten (prio 0/1), Polling verwerfen
        try:
            kept: list[tuple[int, int, HwRequest]] = []
            with self._tx_lock:
                while True:
                    try:
                        pr, seq, req = self._txq.get_nowait()
                    except queue.Empty:
                        break
                    if int(pr) <= 1:
                        kept.append((int(pr), int(seq), req))
                self._txq = queue.PriorityQueue()
                for item in kept:
                    self._txq.put(item)
        except Exception:
            pass
        with self._lock:
            if self._pending and self._pending.on_done:
                self._pending.on_done(None, reason)
            self._pending = None

    # ------------------ RX loop: parse '#...$' ------------------
    def _reader_loop(self):
        def _matches_pending(tel: Telegram, pending: HwRequest) -> bool:
            """Prüft, ob ein Telegramm zum pending Request passt.

            Hintergrund:
            - Viele Firmware-Implementierungen antworten bei GETxxx entweder mit
              `ACK_GETxxx` oder verkürzt `ACK_xxx`.
            - Bei Fehlern kommt oft `NAK_xxx` statt `ACK_xxx`.
            - Wenn wir NAK/verkürzte ACKs nicht matchen, bleibt `_pending` bis zum
              Timeout stehen und blockiert dadurch nachfolgende Requests (ruckelnde UI).
            """
            exp = (pending.expect_prefix or "").strip()
            if not exp:
                return False

            cmd = (tel.cmd or "").strip()
            if not cmd:
                return False

            # Gleiches ACK von einem anderen Master (anderes DST) nicht als unsere Antwort werten
            try:
                if int(tel.dst) != int(self._pending_reply_dst):
                    return False
            except Exception:
                return False

            prefixes = {exp}

            # ACK_GETFOO -> auch ACK_FOO akzeptieren
            if exp.startswith("ACK_GET"):
                prefixes.add("ACK_" + exp[len("ACK_GET") :])

            # ACK_SETFOO -> auch ACK_FOO akzeptieren (manche Firmware nutzt verkürzte ACKs)
            if exp.startswith("ACK_SET"):
                prefixes.add("ACK_" + exp[len("ACK_SET") :])

            # ACK_xxx -> auch NAK_xxx akzeptieren
            if exp.startswith("ACK_"):
                rest = exp[len("ACK_") :]
                prefixes.add("NAK_" + rest)

                # ACK_GETxxx -> zusätzlich NAK_xxx akzeptieren
                if rest.startswith("GET"):
                    prefixes.add("NAK_" + rest[len("GET") :])

                # ACK_SETxxx -> zusätzlich NAK_xxx akzeptieren
                if rest.startswith("SET"):
                    prefixes.add("NAK_" + rest[len("SET") :])

            return any(cmd.startswith(p) for p in prefixes)

        while self._running:
            if not self.is_connected():
                time.sleep(0.2)
                continue
            try:
                chunk = self._read_some()
                if chunk:
                    self._rxbuf += chunk

                    # Wir extrahieren Telegramme: beginnend mit '#', endend mit '$'
                    while True:
                        start = self._rxbuf.find(b"#")
                        if start == -1:
                            # kein Start -> buffer klein halten
                            if len(self._rxbuf) > 4096:
                                self._rxbuf = self._rxbuf[-1024:]
                            break
                        end = self._rxbuf.find(b"$", start)
                        if end == -1:
                            # noch unvollständig
                            if start > 0:
                                self._rxbuf = self._rxbuf[start:]
                            break
                        raw_bytes = self._rxbuf[start : end + 1]
                        self._rxbuf = self._rxbuf[end + 1 :]

                        raw = raw_bytes.decode("ascii", errors="ignore").strip()
                        if not raw:
                            continue
                        self.log.write("RX", raw)
                        tel = parse(raw)
                        if tel is None:
                            continue

                        # Pending request match?
                        with self._lock:
                            pending = self._pending
                        if pending and _matches_pending(tel, pending):
                            with self._lock:
                                self._pending = None
                            if pending.on_done:
                                try:
                                    pending.on_done(tel, None)
                                except Exception as _e:
                                    pass
                        else:
                            if self.on_async_telegram:
                                try:
                                    self.on_async_telegram(tel)
                                except Exception:
                                    pass

            except Exception:
                # Verbindung verloren
                self._disconnect("disconnected")
                time.sleep(0.5)

    # ------------------ TX loop with pending/timeout ------------------
    def _worker_loop(self):
        """Sendeloop.

        WICHTIG:
        - Solange keine Verbindung steht, werden Requests NICHT verworfen.
          Sie bleiben in der Queue und werden gesendet, sobald die Verbindung da ist.
        - Pending-Requests haben weiterhin Timeouts.
        """
        last_connect_try = 0.0
        connect_retry_s = 1.0
        while self._running:
            # Verbindung aufbauen (periodisch)
            if not self.is_connected():
                now = time.time()
                if (now - last_connect_try) >= connect_retry_s:
                    last_connect_try = now
                    self._connect()
                time.sleep(0.05)
                continue

            # Health-Check (robust, mit Grace-Period):
            # Wenn die Verbindung "steht", aber über längere Zeit keinerlei RX kommt,
            # ist der TCP-Serial-Server oft weg/aufgehängt. Dann aktiv trennen, damit
            # der Reconnect-Loop wieder greift.
            try:
                now = time.time()
                since = float(self._connected_since_ts or 0.0)
                last_rx = float(self._last_rx_any_ts or 0.0)
                if (
                    since > 0.0
                    and (now - since) > 3.0
                    and last_rx > 0.0
                    and (now - last_rx) > float(self._no_rx_timeout_s)
                ):
                    self._disconnect("no_rx")
                    time.sleep(0.1)
                    continue
            except Exception:
                pass

            # Timeout für pending request
            with self._lock:
                pending = self._pending
            if pending:
                if time.time() - pending.sent_ts > pending.timeout_s:
                    with self._lock:
                        self._pending = None
                    if pending.on_done:
                        try:
                            pending.on_done(None, "timeout")
                        except Exception:
                            pass
                    # Bei COM ohne RS485-Bus kommen keine Antworten -> kein Disconnect.
                    # Bei TCP deutet Timeout auf hängende Verbindung -> disconnect/reconnect.
                    # dont_disconnect_on_timeout: Retry-Logik soll Verbindung behalten (z.B. SETPOSDG)
                    mode = str(self.cfg.get("mode", "tcp") or "tcp").strip().lower()
                    if mode != "com" and not getattr(pending, "dont_disconnect_on_timeout", False):
                        self._disconnect("timeout")
                time.sleep(0.01)
                continue

            try:
                _, _, req = self._txq.get(timeout=0.1)
            except queue.Empty:
                continue

            # Falls Verbindung zwischenzeitlich weg ist, Request zurückstellen und neu verbinden
            if not self.is_connected():
                self.send_request(req)
                time.sleep(0.05)
                continue

            try:
                # Viele Serial-Server akzeptieren \\n oder \\r\\n; wir schicken \\r\\n für maximale Kompatibilität
                data = (req.line + "\r\n").encode("ascii")
                self.log.write("TX", req.line)
                self._write(data)
                req.sent_ts = time.time()
                if req.expect_prefix:
                    with self._lock:
                        self._pending = req
                else:
                    if req.on_done:
                        req.on_done(None, None)
            except Exception:
                try:
                    if self._sock:
                        self._sock.close()
                except Exception:
                    pass
                try:
                    if self._ser:
                        self._ser.close()
                except Exception:
                    pass
                self._sock = None
                self._ser = None
                if req.on_done:
                    req.on_done(None, "send_error")
