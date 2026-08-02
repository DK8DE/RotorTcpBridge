from __future__ import annotations
import sys
import threading
import queue
import time
import socket
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Deque, Optional, Callable

# Direktausführung dieser Datei (Play-Button): sys.path zeigt nur auf dieses Verzeichnis —
# Projektroot eintragen, damit `import rotortcpbridge…` wie bei `python run.py` funktioniert.
if __package__ is None:  # pragma: no cover
    _repo_root = Path(__file__).resolve().parents[1]
    if str(_repo_root) not in sys.path:
        sys.path.insert(0, str(_repo_root))

from rotortcpbridge.rs485_protocol import BROADCAST_DST, parse, Telegram
from rotortcpbridge.logutil import LogBuffer

try:
    import serial
except Exception:
    serial = None

# Nach erstem fehlerhaften RX höchstens so viele erneute Sends desselben Telegramms
_MAX_CHECKSUM_RETRIES = 2

# Reine Log-Zähler (TX-Nummer, RX-Paketnummer) laufen bei 1 wieder los, damit sie im
# Dauerbetrieb nicht endlos wachsen. Nur zur Diagnose — keine Reihenfolge-Bedeutung.
_LOG_SEQ_WRAP = 1_000_000


@dataclass
class HwRequest:
    line: str
    expect_prefix: Optional[str] = None  # z.B. "ACK_GETPOSDG"
    timeout_s: float = 0.8
    on_done: Optional[Callable[[Optional[Telegram], Optional[str]], None]] = None
    sent_ts: float = 0.0
    priority: int = 5  # 0 = höchste Priorität (UI), 5 = normal (Polling)
    dont_disconnect_on_timeout: bool = False  # True: bei Timeout nicht trennen (z.B. für Retry)
    checksum_retries_done: int = 0  # fehlerhafte CS → erneut senden (siehe Reader)
    # Half-Duplex RS485: naechstes TX erst nach RX (oder Timeout).
    # Default True — Polling-GETs erwarten ACK, auch ohne expect_prefix.
    # False nur fuer echte Fire-and-Forget-Broadcasts (SETASELECT o.ae.).
    wait_for_reply: bool = True


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
        self._udp_sock: Optional[socket.socket] = None
        self._ser = None
        self._reader_thread = None
        self._worker_thread = None

        self._rxbuf = b""
        self._pending: Optional[HwRequest] = None
        self._lock = threading.Lock()
        # Serielle Ausgabe: Worker und UI (Fire-and-Forget) dürfen nicht mischen
        self._serial_write_lock = threading.Lock()

        self.on_async_telegram: Optional[Callable[[Telegram], None]] = None
        # Antworten auf unsere Requests haben DST = eigene Master-ID; andere Master nicht als Pending matchen
        self._pending_reply_dst: int = 0
        self._last_rx_any_ts: float = 0.0
        self._last_tx_any_ts: float = 0.0
        # Monotone Wire-TX-Nummer: wird nur bei physischem Write erhoeht.
        # Doppel-Send auf dem Bus waere im Log als zwei aufeinanderfolgende TX# sichtbar.
        self._wire_tx_seq: int = 0
        # Kuerzlich gesendete Telegramme — zum Filtern von:
        # 1) USB-RS485 Lokal-Echo (typisch <20 ms)
        # 2) Befehlsreflexion zusammen mit dem ACK (~RTT, oft ~200 ms) — KEIN Zweit-Send
        self._recent_tx: Deque[tuple[str, float, int]] = deque(maxlen=32)
        self._tx_echo_window_s: float = 0.8
        # Nur so kurz = echtes Adapter-Loopback; spaeter = Slave/Gateway spiegelt den Befehl.
        self._tx_adapter_echo_max_s: float = 0.040
        # Laufende Nummer je physischem Lesevorgang (TCP/UDP-Paket bzw. COM-Read).
        # Gleiche Nummer bei Reflexion und ACK = beides kam in EINEM Paket, kann also
        # nicht die Antwort auf zwei getrennte Sendungen sein.
        self._rx_chunk_seq: int = 0
        # Half-Duplex: nach TX auf echte RX warten, bevor das naechste Telegramm
        # gesendet wird (USB-RS485 braucht Zeit fuer DE/RE-Umschaltung).
        self._reply_gate_active: bool = False
        self._reply_gate_until: float = 0.0
        self._connected_since_ts: float = 0.0
        self._safe_reconnect_until_ts: float = 0.0
        # TX-Pacing: Controller reagiert unzuverlässig auf eng gebündelte Telegramme.
        # UI-Befehle bleiben etwas schneller, Polling wird klar begrenzt.
        self._tx_min_gap_ui_s: float = 0.05
        # Mindestabstand Wire-zu-Wire; zusaetzlich serialisiert wait_for_reply den Bus.
        self._tx_min_gap_poll_s: float = 0.12
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
            if self._udp_sock:
                self._udp_sock.close()
        except Exception:
            pass
        try:
            if self._ser:
                self._ser.close()
        except Exception:
            pass
        self._sock = None
        self._udp_sock = None
        self._ser = None

    def is_connected(self) -> bool:
        return self._sock is not None or self._udp_sock is not None or self._ser is not None

    def _update_no_rx_timeout(self) -> None:
        """no-rx-Timeout abhängig vom Verbindungstyp setzen.

        TCP: 5s (Serial-Server antwortet bei jeder Anfrage).
        COM: 30s (RS485-Bus kann still sein, wenn kein Rotor angeschlossen).
        """
        mode = str(self.cfg.get("mode", "com") or "com").strip().lower()
        self._no_rx_timeout_s = 30.0 if mode == "com" else 5.0

    def set_expected_response_dst(self, master_id: int) -> None:
        """Nur Telegramme mit ``dst == master_id`` dürfen ein ausstehendes TX-Match sein."""
        try:
            self._pending_reply_dst = int(master_id)
        except Exception:
            self._pending_reply_dst = 0

    def _in_safe_reconnect_mode(self) -> bool:
        try:
            return time.time() < float(self._safe_reconnect_until_ts or 0.0)
        except Exception:
            return False

    def _activate_safe_reconnect_mode(self, holdoff_s: float = 0.9) -> None:
        """Kurzes Schutzfenster beim Transportwechsel (TCP<->COM).

        Währenddessen werden TX/RX nicht weiterverarbeitet, damit alte Pending-Requests
        nicht in den neuen Transport hineinlaufen.
        """
        try:
            until = time.time() + max(0.2, float(holdoff_s))
        except Exception:
            until = time.time() + 0.9
        self._safe_reconnect_until_ts = max(float(self._safe_reconnect_until_ts or 0.0), until)

    def update_cfg(self, cfg: dict):
        old = dict(self._applied_cfg or {})
        new = dict(cfg or {})
        self.cfg = new
        self._applied_cfg = dict(new)
        self._update_no_rx_timeout()

        # Bei relevanter Änderung (Mode/Endpoint/Baud) bestehende Verbindung
        # aktiv trennen, damit der Worker sofort mit den neuen Werten reconnectet.
        relevant_keys = ("mode", "tcp_ip", "tcp_port", "udp_bind_port", "com_port", "baudrate")
        changed = any(old.get(k) != new.get(k) for k in relevant_keys)
        if changed:
            self._activate_safe_reconnect_mode()
            self._disconnect("cfg_changed", keep_priority_le=-1)
            self.log.write("INFO", "Safe-Reconnect aktiv (Transportwechsel)")

    def send_request(self, req: HwRequest):
        if self._in_safe_reconnect_mode():
            return
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
        mode = self.cfg.get("mode", "com")
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
        elif mode == "udp":
            ip = str(self.cfg.get("tcp_ip", "127.0.0.1") or "127.0.0.1").strip() or "127.0.0.1"
            port = int(self.cfg.get("tcp_port", 23))
            try:
                bind_port = int(self.cfg.get("udp_bind_port", 0))
            except Exception:
                bind_port = 0
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.bind(("0.0.0.0", max(0, min(65535, bind_port))))
                s.settimeout(0.2)
                self._udp_sock = s
                self._last_rx_any_ts = time.time()
                self._last_tx_any_ts = 0.0
                self._connected_since_ts = time.time()
                local_port = 0
                try:
                    local_port = int(s.getsockname()[1])
                except Exception:
                    local_port = bind_port
                self.log.write("INFO", f"Hardware UDP aktiv {ip}:{port} (lokal:{local_port})")
            except Exception:
                self._udp_sock = None
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

    def _write_unlocked(self, data: bytes) -> None:
        if self._sock:
            self._sock.sendall(data)
            self._last_tx_any_ts = time.time()
        elif self._udp_sock:
            ip = str(self.cfg.get("tcp_ip", "127.0.0.1") or "127.0.0.1").strip() or "127.0.0.1"
            port = int(self.cfg.get("tcp_port", 23))
            self._udp_sock.sendto(data, (ip, port))
            self._last_tx_any_ts = time.time()
        elif self._ser:
            self._ser.write(data)
            self._last_tx_any_ts = time.time()

    def _emit_wire_tx(self, line: str, *, priority: int = 5) -> int:
        """Ein physisches TX auf den Bus: Log + Echo-Merkmal + Write.

        Rueckgabe: Wire-Sequenznummer. Jeder echte Send erhoeht sie genau einmal —
        ein Doppel-Send waere also als zwei TX# im Log erkennbar.
        """
        s = str(line or "").strip()
        if not s:
            return 0
        with self._lock:
            self._wire_tx_seq = (int(self._wire_tx_seq) % _LOG_SEQ_WRAP) + 1
            seq = int(self._wire_tx_seq)
        self.log.write("TX", f"#{seq} {s}")
        # Vor dem Write merken (sonst kann das Echo schneller da sein als der Eintrag);
        # danach auf die echte Schreibzeit korrigieren, da _write_with_pacing wartet.
        self._note_tx_line(s, seq)
        self._write_with_pacing(s.encode("ascii"), priority=int(priority))
        self._mark_tx_written(seq)
        return seq

    def _write(self, data: bytes):
        self._write_with_pacing(data, priority=5)

    def _tx_gap_for_priority(self, priority: int) -> float:
        try:
            p = int(priority)
        except Exception:
            p = 5
        if p <= 1:
            return float(self._tx_min_gap_ui_s)
        return float(self._tx_min_gap_poll_s)

    def _write_with_pacing(self, data: bytes, priority: int = 5) -> None:
        """Seriell schreiben mit Mindestabstand zwischen Telegrammen."""
        with self._serial_write_lock:
            now = time.time()
            last_tx = float(self._last_tx_any_ts or 0.0)
            gap_s = max(0.0, self._tx_gap_for_priority(priority))
            wait_s = (last_tx + gap_s) - now
            if wait_s > 0.0:
                time.sleep(wait_s)
            self._write_unlocked(data)

    def _note_tx_line(self, line: str, seq: int = 0) -> None:
        """Merkt gesendete Telegramme, um RS485-Adapter-Echos zu erkennen."""
        s = str(line or "").strip()
        if not s:
            return
        now = time.time()
        with self._lock:
            self._recent_tx.append((s, now, int(seq)))
            # Abgelaufene Eintraege entfernen
            win = float(self._tx_echo_window_s)
            while self._recent_tx and (now - self._recent_tx[0][1]) > win:
                self._recent_tx.popleft()

    def _mark_tx_written(self, seq: int) -> None:
        """Zeitstempel auf den tatsaechlichen Wire-Write setzen (nach der Pacing-Pause)."""
        if not seq:
            return
        now = time.time()
        with self._lock:
            for i, (line, _ts, s) in enumerate(self._recent_tx):
                if int(s) == int(seq):
                    self._recent_tx[i] = (line, now, int(s))
                    break

    def _consume_tx_dup_rx(self, raw: str) -> tuple[int, float, str] | None:
        """RX-Zeile identisch zu einem kuerzlichen TX: (seq, age_s, kind) oder None.

        kind:
          - ``adapter_echo``: sehr schnell nach TX (USB-Loopback)
          - ``cmd_reflect``: erst mit der Antwort (~RTT) — Slave/Gateway spiegelt den
            Befehl; das ist **kein** zweites Senden der Bridge
        """
        s = str(raw or "").strip()
        if not s:
            return None
        now = time.time()
        win = float(self._tx_echo_window_s)
        adapt_max = float(self._tx_adapter_echo_max_s)
        with self._lock:
            while self._recent_tx and (now - self._recent_tx[0][1]) > win:
                self._recent_tx.popleft()
            for i, (line, ts, seq) in enumerate(self._recent_tx):
                if line != s:
                    continue
                age = now - float(ts)
                if age > win:
                    continue
                del self._recent_tx[i]
                kind = "adapter_echo" if age <= adapt_max else "cmd_reflect"
                return int(seq), float(age), kind
        return None

    def _arm_reply_gate(self, timeout_s: float) -> None:
        """Naechstes TX blockieren, bis echte RX kommt oder Timeout."""
        to = float(max(0.12, min(1.5, timeout_s)))
        with self._lock:
            self._reply_gate_active = True
            self._reply_gate_until = time.time() + to

    def _clear_reply_gate(self) -> None:
        with self._lock:
            self._reply_gate_active = False
            self._reply_gate_until = 0.0

    def _reply_gate_blocks_tx(self) -> bool:
        """True solange auf Antwort gewartet wird (Half-Duplex-Pause)."""
        with self._lock:
            if not self._reply_gate_active:
                return False
            if time.time() >= float(self._reply_gate_until or 0.0):
                self._reply_gate_active = False
                self._reply_gate_until = 0.0
                return False
            return True

    def send_line_fire_and_forget(self, line: str) -> None:
        """Sofort senden, ohne Worker-Queue und ohne Pending-Wartezeit.

        Nötig z. B. für Broadcasts ohne Antwort: sonst blockiert die TX-Schleife
        bei ausstehendem Poll-ACK und das Telegramm bleibt in der Queue.
        """
        if self._in_safe_reconnect_mode() or (not self.is_connected()):
            return
        s = str(line).strip()
        if not s:
            return
        try:
            # Kein wait_for_reply: Broadcasts haben oft kein ACK. Kurze Pause nach
            # dem Write ueber UI-Pacing (DE/RE-Umschaltung).
            self._emit_wire_tx(s, priority=1)
        except Exception:
            pass

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
        elif self._udp_sock:
            try:
                data, _ = self._udp_sock.recvfrom(4096)
                if data:
                    self._last_rx_any_ts = time.time()
                return data
            except socket.timeout:
                return b""
        elif self._ser:
            try:
                # pyserial read(n) wartet, bis n Bytes da sind ODER der Timeout ablaeuft.
                # read(4096) hat deshalb IMMER die vollen 200 ms geblockt: Adapter-Echo und
                # ACK landeten zusammen in einem Chunk und jede Antwort kam 200 ms zu spaet.
                # Daher: nur abholen was anliegt, sonst kurz auf das erste Byte warten.
                n = 0
                try:
                    n = int(self._ser.in_waiting or 0)
                except Exception:
                    n = 0
                if n > 0:
                    data = self._ser.read(min(n, 4096))
                else:
                    data = self._ser.read(1)
                    if data:
                        try:
                            n2 = int(self._ser.in_waiting or 0)
                        except Exception:
                            n2 = 0
                        if n2 > 0:
                            data += self._ser.read(min(n2, 4095))
                if data:
                    self._last_rx_any_ts = time.time()
                return data
            except Exception:
                # USB-Adapter abgezogen etc. -> als Disconnect behandeln
                raise
        return b""

    def _disconnect(self, reason: str = "disconnected", keep_priority_le: int = 1):
        """Verbindung hart schließen + pending freigeben."""
        pending: Optional[HwRequest] = None
        try:
            if self._sock:
                self._sock.close()
        except Exception:
            pass
        try:
            if self._udp_sock:
                self._udp_sock.close()
        except Exception:
            pass
        try:
            if self._ser:
                self._ser.close()
        except Exception:
            pass
        self._sock = None
        self._udp_sock = None
        self._ser = None
        self._connected_since_ts = 0.0
        self._rxbuf = b""
        # TX-Queue entschärfen: nur UI-Requests behalten (prio 0/1), Polling verwerfen
        try:
            kept: list[tuple[int, int, HwRequest]] = []
            with self._tx_lock:
                while True:
                    try:
                        pr, seq, req = self._txq.get_nowait()
                    except queue.Empty:
                        break
                    if int(pr) <= int(keep_priority_le):
                        kept.append((int(pr), int(seq), req))
                self._txq = queue.PriorityQueue()
                for item in kept:
                    self._txq.put(item)
        except Exception:
            pass
        with self._lock:
            pending = self._pending
            self._pending = None
            self._reply_gate_active = False
            self._reply_gate_until = 0.0
        if pending and pending.on_done:
            try:
                pending.on_done(None, reason)
            except Exception:
                pass

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

            # Antwort muss vom angefragten Slave stammen (tel.src = Slave, TX-Ziel = dst im #M:S:…).
            # Sonst passen z. B. ACK_GETLIVEBINS/ACK_GETACCBINS von EL fälschlich zum AZ-Pending
            # (gleicher Präfix, anderer Slave) — Merge in falsche Temp-Buffer, Timeouts, leere UI.
            try:
                tx_meta = parse(str(pending.line).strip())
                if tx_meta is not None:
                    tx_dst = int(tx_meta.dst)
                    # Broadcast (DST 255): Slave antwortet mit SRC = eigener ID, nicht 255
                    if tx_dst != int(BROADCAST_DST):
                        if int(tel.src) != tx_dst:
                            return False
            except Exception:
                pass

            prefixes = {exp}

            # SETROTORID (Broadcast): manche Firmware antwortet wie bei SETID (ACK_SETID/NAK_SETID)
            try:
                tx_cmd = parse(str(pending.line).strip())
                if tx_cmd is not None and (tx_cmd.cmd or "").strip().upper() == "SETROTORID":
                    prefixes.add("ACK_SETID")
                    prefixes.add("NAK_SETID")
                    prefixes.add("ACK_ROTORID")
                    prefixes.add("NAK_ROTORID")
            except Exception:
                pass

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

            if not any(cmd.startswith(p) for p in prefixes):
                return False

            # Paging-Bins (GETACCBINS/GETLIVEBINS/…): gleicher ACK-Präfix für jeden Block —
            # ein verspäteter ACK vom vorherigen Block darf nicht dem nächsten Pending zugeordnet werden
            # (sonst falscher Merge / „Timeout“, obwohl die Antwort später noch kam).
            try:
                tx_meta = parse(str(pending.line).strip())
                if tx_meta is not None:
                    ucmd = (tx_meta.cmd or "").strip().upper()
                    if ucmd in (
                        "GETACCBINS",
                        "GETLIVEBINS",
                        "GETCALBINS",
                        "GETDELTABINS",
                    ):
                        cmd_u = (cmd or "").strip().upper()
                        if "NAK" not in cmd_u:
                            req_p = (tx_meta.params or "").strip()
                            if req_p:
                                ack_p = (tel.params or "").strip()
                                if not (ack_p == req_p or ack_p.startswith(req_p + ";")):
                                    return False
            except Exception:
                pass

            return True

        while self._running:
            if not self.is_connected():
                time.sleep(0.2)
                continue
            try:
                chunk = self._read_some()
                if chunk:
                    self._rx_chunk_seq = (int(self._rx_chunk_seq) % _LOG_SEQ_WRAP) + 1
                    chunk_id = int(self._rx_chunk_seq)
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
                        # Identische Zeile zu unserem TX: kein zweiter Bridge-Send
                        # (Wire-TX# steigt nur in _emit_wire_tx). Entweder schnelles
                        # USB-Loopback oder Befehlsreflexion kurz vor dem ACK.
                        dup = self._consume_tx_dup_rx(raw)
                        if dup is not None:
                            echo_seq, age_s, kind = dup
                            age_ms = int(round(age_s * 1000.0))
                            if kind == "adapter_echo":
                                self.log.write(
                                    "ECHO",
                                    f"adapter of TX#{echo_seq} age={age_ms}ms pkt={chunk_id} {raw}",
                                )
                            else:
                                self.log.write(
                                    "RX_CMD",
                                    f"reflect of TX#{echo_seq} age={age_ms}ms pkt={chunk_id} {raw}",
                                )
                            continue
                        self.log.write("RX", f"pkt={chunk_id} {raw}")
                        # Echte Antwort vom Bus → Half-Duplex-Gate oeffnen
                        self._clear_reply_gate()
                        tel = parse(raw)
                        if tel is None:
                            continue

                        # Pending request match?
                        with self._lock:
                            pending = self._pending
                        if pending and _matches_pending(tel, pending):
                            if tel.ok:
                                with self._lock:
                                    self._pending = None
                                if pending.on_done:
                                    try:
                                        pending.on_done(tel, None)
                                    except Exception as _e:
                                        pass
                            else:
                                # Checksumme passt nicht: gleiche Anfrage begrenzt erneut senden
                                pending.checksum_retries_done += 1
                                if pending.checksum_retries_done <= _MAX_CHECKSUM_RETRIES:
                                    self.log.write(
                                        "WARN",
                                        f"RX Checksumme ungültig, erneut senden ({pending.checksum_retries_done}/{_MAX_CHECKSUM_RETRIES})",
                                    )
                                    try:
                                        # Explizit als neues Wire-TX loggen (kein stiller Zweit-Send).
                                        self._emit_wire_tx(
                                            pending.line,
                                            priority=int(getattr(pending, "priority", 5)),
                                        )
                                        pending.sent_ts = time.time()
                                    except Exception:
                                        with self._lock:
                                            self._pending = None
                                        if pending.on_done:
                                            try:
                                                pending.on_done(None, "bad_checksum_resend_error")
                                            except Exception:
                                                pass
                                else:
                                    # Nach Retries trotzdem auswerten (wie vor CS-Retry), sonst blockiert
                                    # die Queue / Folgeabfragen (z. B. GETACCBINS) unnötig.
                                    with self._lock:
                                        self._pending = None
                                    self.log.write(
                                        "WARN",
                                        "RX Checksumme nach Retries noch ungültig — Telegramm wird trotzdem ausgewertet",
                                    )
                                    if pending.on_done:
                                        try:
                                            pending.on_done(tel, None)
                                        except Exception as _e:
                                            pass
                        else:
                            if self.on_async_telegram:
                                try:
                                    cmd_u = (tel.cmd or "").strip().upper()
                                    # SETASELECT / SETPOSCC (Mitschnitt): CS kann abweichen
                                    if tel.ok or "SETASELECT" in cmd_u or "SETPOSCC" in cmd_u:
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
            if self._in_safe_reconnect_mode():
                time.sleep(0.02)
                continue
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

            # Half-Duplex: auf Antwort des letzten Poll-TX warten (nicht nur Pacing-Luecke).
            if self._reply_gate_blocks_tx():
                time.sleep(0.01)
                continue

            # Timeout für pending request
            with self._lock:
                pending = self._pending
            if pending:
                if time.time() - pending.sent_ts > pending.timeout_s:
                    with self._lock:
                        self._pending = None
                    self._clear_reply_gate()
                    if pending.on_done:
                        try:
                            pending.on_done(None, "timeout")
                        except Exception:
                            pass
                    # Bei COM ohne RS485-Bus kommen keine Antworten -> kein Disconnect.
                    # Bei TCP deutet Timeout auf hängende Verbindung -> disconnect/reconnect.
                    # dont_disconnect_on_timeout: Retry-Logik soll Verbindung behalten (z.B. SETPOSDG)
                    mode = str(self.cfg.get("mode", "com") or "com").strip().lower()
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
                # Nur das Telegramm (#...$), ohne \\r\\n — Protokoll endet mit $
                req.checksum_retries_done = 0
                self._emit_wire_tx(req.line, priority=int(getattr(req, "priority", 5)))
                req.sent_ts = time.time()
                if req.expect_prefix:
                    with self._lock:
                        self._pending = req
                else:
                    # Polling ohne expect_prefix: trotzdem Half-Duplex serialisieren.
                    if bool(getattr(req, "wait_for_reply", True)):
                        self._arm_reply_gate(float(getattr(req, "timeout_s", 0.8) or 0.8))
                    if req.on_done:
                        try:
                            req.on_done(None, None)
                        except Exception:
                            pass
            except Exception:
                try:
                    if self._sock:
                        self._sock.close()
                except Exception:
                    pass
                try:
                    if self._udp_sock:
                        self._udp_sock.close()
                except Exception:
                    pass
                try:
                    if self._ser:
                        self._ser.close()
                except Exception:
                    pass
                self._sock = None
                self._udp_sock = None
                self._ser = None
                if req.on_done:
                    try:
                        req.on_done(None, "send_error")
                    except Exception:
                        pass
