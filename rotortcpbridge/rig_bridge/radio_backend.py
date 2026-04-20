"""Zentrale physische Funkgeräte-Verbindung mit serialisiertem Schreibzugriff."""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

from .cat_commands import (
    build_ptt_payload,
    build_read_vfo_frequency_query,
    build_set_frequency_payload,
    build_set_mode_payload,
    normalize_com_port,
    parse_fa_style_frequency_hz,
)
from .config import RigBridgeConfig
from .exceptions import RigConnectionError
from .state import RadioStateCache

try:
    import serial
except Exception:  # pragma: no cover - optional bei fehlendem pyserial
    serial = None

# PySerial: USB abziehen / Handle ungültig → typ. OSError oder SerialException
def _serial_fatal_types() -> tuple[type, ...]:
    t: list[type] = [OSError, IOError]
    if serial is not None:
        se = getattr(serial, "SerialException", None)
        if se is not None:
            t.append(se)
    return tuple(t)


@dataclass
class _WriteCommand:
    command: str
    callback: Optional[Callable[[str], None]] = None
    #: Wenn gesetzt: nach Bearbeitung (Erfolg oder Fehler) setzen — z. B. für synchrones READFREQ.
    done: Optional[threading.Event] = None
    #: Kurztext fürs Diagnose-Log (z. B. Flrig rig.set_frequency vs. rig.get_vfo).
    log_ctx: str = ""


class RadioConnectionManager:
    """Verwaltet die einzige physische Verbindung zum Funkgerät."""

    def __init__(
        self,
        state: RadioStateCache,
        log_write: Callable[[str, str], None],
        on_serial_activity: Optional[Callable[[], None]] = None,
        on_link_lost: Optional[Callable[[], None]] = None,
    ):
        self._state = state
        self._log_write = log_write
        self._on_serial_activity = on_serial_activity or (lambda: None)
        self._on_link_lost = on_link_lost
        self._cfg = RigBridgeConfig()
        self._ser = None
        self._running = False
        self._connecting = False
        self._write_q: "queue.Queue[_WriteCommand]" = queue.Queue()
        self._worker: threading.Thread | None = None
        self._poller: threading.Thread | None = None
        self._lock = threading.RLock()
        # Alle Zugriffe auf dieselbe serielle Schnittstelle (Worker + Verbindungstest)
        self._io_lock = threading.Lock()
        self._debug_traffic = False
        self._log_serial = True
        self._cat_post_write_drain_s = 0.08
        self._setfreq_gap_s = 0.0
        #: Nach SETFREQ kein FA-Lesen: vermeidet „Echo“/Ziffernrücksprung, wenn der TRX noch nachzieht.
        #: 300 ms (vorher 110 ms) geben dem Geraet genug Zeit, um den VFO wirklich
        #: zu setzen — sonst bekommt die Gegenstelle beim naechsten Poll die alte
        #: Frequenz zurueck ("Frequenz springt zurueck") und der TRX fuehlt sich
        #: von schnell aufeinanderfolgenden FA;/FA<n>; Befehlen ueberfordert
        #: (bei Yaesu newcat sichtbar an ``?;``-Antworten).
        self._readfreq_suppress_until_mono = 0.0
        self._post_setfreq_read_suppress_s = 0.30
        #: Mehrere FLRig/Hamlib-Clients pollen ``get_vfo``/``f`` — COM entlasten, State bleibt zuletzt gesetzt.
        self._readfreq_min_interval_s = 0.048
        self._last_readfreq_cat_mono = 0.0
        #: Zeitstempel des zuletzt in die Queue gelegten ``SETFREQ``. Dient als
        #: Race-Schutz: wenn ein READFREQ bereits am TRX fliegt und *waehrenddessen*
        #: ein externes Programm eine neue Frequenz per ``FA<neu>;`` setzt, liefert
        #: das Geraet fuer den laufenden READFREQ noch den *alten* Wert zurueck.
        #: Diesen Reply duerfen wir nicht mehr in den Cache uebernehmen — sonst
        #: ueberschreibt er den optimistischen Patch und die naechsten Polls
        #: sehen kurzfristig wieder den alten Wert („Frequenz springt zurueck").
        self._last_setfreq_enqueue_mono = 0.0
        #: Tuning-Modus: solange der letzte SETFREQ-Enqueue weniger als X s
        #: zurueckliegt, werden READFREQs komplett uebersprungen. Grund: beim
        #: Drehen am externen Abstimmknopf (HRD, Logger32 …) feuert das Programm
        #: pro Knopf-Tick sowohl ``FA<n>;`` (SET) als auch im naechsten Poll-Zyklus
        #: ``FA;`` (READ). Wir brauchen waehrend einer aktiven Tuning-Phase den
        #: TRX nicht mit READs zu befragen — die **optimistische State-Cache-
        #: Aktualisierung** liefert dem externen Programm sofort den "richtigen"
        #: Wert zurueck und der TRX kann die SETs ungestoert durchgehen lassen.
        #: Ohne diese Sperre kloppen sich SETs und READs um Rig-Bandbreite und
        #: der TRX verschluckt Frequenzaenderungen (null-Byte-Antworten auf SET).
        self._tuning_active_window_s = 1.5

    @staticmethod
    def _is_fatal_link_error(exc: BaseException) -> bool:
        """True bei echtem Verbindungsverlust (USB weg, Handle tot); nicht bei Logik-Fehlern."""
        if isinstance(exc, RigConnectionError):
            return False
        return isinstance(exc, _serial_fatal_types())

    def _join_worker_threads(self) -> None:
        """Vor neuem connect(): alte Worker beenden (wie Hardware-Client nach COM-Verlust)."""
        tw: threading.Thread | None
        tp: threading.Thread | None
        with self._lock:
            tw, tp = self._worker, self._poller
            self._worker = None
            self._poller = None
        for t in (tw, tp):
            if t is not None and t.is_alive():
                t.join(timeout=4.0)

    def _drain_write_queue(self) -> None:
        while True:
            try:
                self._write_q.get_nowait()
            except queue.Empty:
                break

    def _drop_com_link(self, exc: BaseException | None = None) -> None:
        """COM-Session abwerfen (Kabel raus, I/O-Fehler). Idempotent; Zustand wie nach „Trennen“.

        Schließen nur unter ``_io_lock``, damit kein paralleler Worker-``read``/``write`` auf dem
        gleichen Handle läuft (Windows: sonst oft ClearCommError / PermissionError).
        """
        with self._lock:
            if self._ser is None:
                return
            self._running = False
        ser_obj = None
        with self._io_lock:
            with self._lock:
                if self._ser is None:
                    ser_obj = None
                else:
                    ser_obj = self._ser
                    self._ser = None
        if ser_obj is not None:
            try:
                ser_obj.close()
            except Exception:
                pass
        self._drain_write_queue()
        self._readfreq_suppress_until_mono = 0.0
        self._last_readfreq_cat_mono = 0.0
        self._state.update(connected=False)
        if exc is not None:
            self._state.set_error(str(exc))
        else:
            self._state.set_error("COM getrennt")
        if exc is not None:
            self._log_write("WARN", f"Rig-Bridge: COM-Verbindung verloren: {exc}")
        else:
            self._log_write("WARN", "Rig-Bridge: COM-Verbindung verloren")
        cb = self._on_link_lost
        if cb is not None:
            try:
                cb()
            except Exception:
                pass

    @staticmethod
    def _is_setfreq_cmd(cmd: str) -> bool:
        return str(cmd).strip().upper().startswith("SETFREQ ")

    @staticmethod
    def _setfreq_hz_from_command(cmd: str) -> int:
        arg = str(cmd).strip().split(None, 1)[1].strip()
        return int(round(float(arg.replace(",", "."))))

    @staticmethod
    def _format_serial_payload(payload: bytes) -> str:
        """Kurzbeschreibung serieller Rohdaten für das Diagnose-Log."""
        data = bytes(payload or b"")
        n = len(data)
        if n == 0:
            return "(0 Byte)"
        hx = data.hex()
        if len(hx) > 120:
            hx = hx[:120] + "…"
        asc = data.decode("ascii", errors="replace")
        vis = "".join(ch if 32 <= ord(ch) < 127 else "." for ch in asc)
        return f"{n} Byte hex={hx} ascii={vis!r}"

    def _log_serial_io(self, direction: str, payload: bytes, note: str = "") -> None:
        if not self._log_serial:
            return
        tail = f" {note}" if note else ""
        self._log_write(
            "INFO",
            f"Rig-Bridge: COM {direction} {self._format_serial_payload(payload)}{tail}",
        )

    @staticmethod
    def _read_cat_quick(ser, *, is_icom: bool, max_wait_s: float) -> bytes:
        """Kurzes Abtropfen nach Schreib-CAT (Echo/`;`).

        Viele TRX antworten auf ``FA…``/``TX…`` nicht. Wichtig: ``ser.read`` blockiert bis
        ``ser.timeout`` — daher Timeout hier kurz setzen, sonst folgen 300–500 ms pro Leerlauf.
        """
        deadline = time.monotonic() + max(0.02, float(max_wait_s))
        buf = b""
        old_tmo = getattr(ser, "timeout", 0.25)
        try:
            ser.timeout = 0.02
            while time.monotonic() < deadline:
                chunk = ser.read(128)
                if chunk:
                    buf += chunk
                    if is_icom and b"\xFD" in buf:
                        break
                    if not is_icom and b";" in buf:
                        break
                else:
                    time.sleep(0.002)
        finally:
            try:
                ser.timeout = old_tmo
            except Exception:
                pass
        return buf

    def _read_cat_response_logged(
        self,
        ser,
        *,
        is_icom: bool,
        min_deadline_s: float = 0.0,
        note: str = "",
        quick_drain: bool = False,
    ) -> bytes:
        if quick_drain:
            rx = self._read_cat_quick(
                ser, is_icom=is_icom, max_wait_s=self._cat_post_write_drain_s
            )
        else:
            rx = self._read_cat_response_ephemeral(
                ser, is_icom=is_icom, min_deadline_s=min_deadline_s
            )
        self._log_serial_io("RX", rx, note)
        return rx

    @property
    def connecting(self) -> bool:
        return bool(self._connecting)

    def is_serial_connected(self) -> bool:
        """True, wenn die Dauer-Verbindung zum COM-Port aktiv ist."""
        with self._lock:
            return self._ser is not None

    def update_config(self, cfg: RigBridgeConfig) -> None:
        """Neue Konfiguration übernehmen."""
        with self._lock:
            self._cfg = cfg
            self._debug_traffic = bool(cfg.hamlib.get("debug_traffic", False))
            self._log_serial = bool(getattr(cfg, "log_serial_traffic", True))
            ms = int(getattr(cfg, "cat_post_write_drain_ms", 80))
            self._cat_post_write_drain_s = max(0.02, min(0.5, ms / 1000.0))
            gap = int(getattr(cfg, "setfreq_gap_ms", 0))
            self._setfreq_gap_s = max(0.0, min(0.2, gap / 1000.0))
            self._state.update(selected_rig=cfg.selected_rig, com_port=cfg.com_port)

    def connect(self) -> None:
        """Verbindung aufbauen und Worker starten."""
        with self._lock:
            if self._ser is not None:
                return
            if serial is None:
                raise RigConnectionError("pyserial ist nicht verfügbar")
        # Ohne Lock: Join kann auf Worker warten, die gerade _drop_com_link (_lock) verlassen
        self._join_worker_threads()
        with self._lock:
            if self._ser is not None:
                return
            self._connecting = True
        try:
            stopbits = serial.STOPBITS_ONE if self._cfg.stopbits == 1 else serial.STOPBITS_TWO
            parity_map = {
                "N": serial.PARITY_NONE,
                "E": serial.PARITY_EVEN,
                "O": serial.PARITY_ODD,
                "M": serial.PARITY_MARK,
                "S": serial.PARITY_SPACE,
            }
            com = normalize_com_port(self._cfg.com_port)
            self._ser = serial.Serial(
                port=com,
                baudrate=self._cfg.baudrate,
                bytesize=int(self._cfg.databits),
                stopbits=stopbits,
                parity=parity_map.get(self._cfg.parity, serial.PARITY_NONE),
                timeout=float(self._cfg.timeout_s),
            )
            self._running = True
            self._readfreq_suppress_until_mono = 0.0
            self._last_readfreq_cat_mono = 0.0
            self._worker = threading.Thread(target=self._write_loop, daemon=True)
            self._poller = threading.Thread(target=self._poll_loop, daemon=True)
            self._worker.start()
            self._poller.start()
            self._state.update(connected=True)
            self._state.mark_success()
            self._log_write("INFO", f"Rig-Bridge: COM verbunden {com}")
        except Exception as exc:
            self._state.update(connected=False)
            self._state.set_error(str(exc))
            raise RigConnectionError(str(exc)) from exc
        finally:
            self._connecting = False

    def disconnect(self) -> None:
        """Verbindung sauber schließen."""
        with self._lock:
            self._running = False
        ser = None
        with self._io_lock:
            with self._lock:
                ser = self._ser
                self._ser = None
        if ser is not None:
            try:
                ser.close()
            except Exception:
                pass
        self._drain_write_queue()
        self._join_worker_threads()
        self._readfreq_suppress_until_mono = 0.0
        self._last_readfreq_cat_mono = 0.0
        self._state.update(connected=False)
        self._state.set_error("")
        self._log_write("INFO", "Rig-Bridge: COM getrennt")

    def write_command(
        self,
        cmd: str,
        callback: Optional[Callable[[str], None]] = None,
        *,
        log_ctx: str = "",
    ) -> None:
        """Schreibzugriff serialisiert über Queue.

        Fuer ``SETFREQ`` gilt zusaetzlich: **Coalescing**. Beim schnellen
        Drehen am externen Abstimmknopf (Ham Radio Deluxe, Logger32 …)
        feuert das Programm fuer jedes Knopf-Tick einen ``FA<neu>;`` ab —
        bis zu 10–15 pro Sekunde. Das physische Yaesu/Kenwood-TRX kommt
        damit nicht mit (Anzeichen: ``?;``-Antworten, null-Byte-Replies,
        korrupter Binaermuell auf der CAT-Leitung, „verschluckte" SETs).
        Die gedrehten Zwischenfrequenzen hat aber niemand interessiert —
        nur der *letzte* Wert zaehlt. Wir entfernen daher beim Enqueue
        eines neuen ``SETFREQ`` **alle** noch in der Queue wartenden
        ``SETFREQ``-Items (die noch nicht am TRX waren) und ersetzen sie
        durch den neuen. Ergebnis: max. ~12 Hz tatsaechliche CAT-SETs am
        Rig, selbst wenn extern mit 100 Hz getrommelt wird.
        """
        c = str(cmd)
        is_setfreq = self._is_setfreq_cmd(c)
        if is_setfreq:
            # Race-Guard: jetzt laufende READFREQ-Replies sind nicht mehr
            # vertrauenswuerdig (TRX hat noch den alten Wert). Siehe Kommentar
            # zu ``_last_setfreq_enqueue_mono`` im Konstruktor.
            self._last_setfreq_enqueue_mono = time.monotonic()
            self._drop_pending_setfreqs()
        self._write_q.put(
            _WriteCommand(command=c, callback=callback, log_ctx=(log_ctx or "").strip())
        )

    def _drop_pending_setfreqs(self) -> int:
        """Alle noch nicht bearbeiteten ``SETFREQ`` aus der Queue entfernen.

        Wird vor dem Enqueue eines neueren ``SETFREQ`` aufgerufen — der
        neue Wert ist aus Sicht des Anwenders immer der "richtige", die
        Zwischenwerte waehrend einer schnellen Knopfdrehung sind
        ueberfluessig und ueberlasten den TRX.
        """
        dropped: list[str] = []
        # Direktzugriff auf das interne Deque von ``queue.Queue`` — benoetigt
        # den Mutex der Queue. ``task_done``/``unfinished_tasks`` lassen wir
        # bewusst unveraendert (niemand ruft ``join()`` auf).
        with self._write_q.mutex:
            q = self._write_q.queue
            i = 0
            while i < len(q):
                item = q[i]
                if self._is_setfreq_cmd(item.command):
                    dropped.append(item.command)
                    # Falls jemand synchron wartet (derzeit nur bei READFREQ,
                    # aber zur Sicherheit): Event setzen statt leise weggehen.
                    if item.done is not None:
                        try:
                            item.done.set()
                        except Exception:
                            pass
                    del q[i]
                else:
                    i += 1
        if dropped and self._log_serial:
            self._log_write(
                "INFO",
                f"Rig-Bridge: {len(dropped)} aelteres/-e SETFREQ durch neueren Wert "
                f"ersetzt (Coalescing bei schnellem Tunen)",
            )
        return len(dropped)

    def read_frequency_sync(self, timeout_s: float = 0.75, *, log_ctx: str = "") -> bool:
        """``READFREQ`` über den COM-Worker; blockiert bis Bearbeitung oder Timeout.

        Genutzt von Flrig/Hamlib bei Abfragen der Anzeigefrequenz, damit VFO-Drehs am Gerät
        sichtbar werden (nicht nur der zuletzt per Software gesetzte Wert).
        """
        if not self.is_serial_connected():
            return False
        with self._lock:
            running = bool(self._running)
        if not running:
            return False
        done = threading.Event()
        ctx = (log_ctx or "").strip()
        self._write_q.put(_WriteCommand(command="READFREQ", done=done, log_ctx=ctx))
        return done.wait(float(timeout_s))

    def _pop_next_command(self, timeout: float) -> Optional[_WriteCommand]:
        """Naechstes Kommando aus der Queue – SETFREQ haben Vorrang.

        Hintergrund: bei aktivem Tunen am externen Drehknopf stehen haeufig
        READFREQs (von Hamlib/Flrig-Refresh bzw. Hauptfenster-Poll) **vor**
        eigentlichen SETFREQs in der FIFO-Queue. Jeder dieser READs blockiert
        den Worker ~30 ms und frisst Rig-Bandbreite, waehrend der User
        darauf wartet, dass seine neue Frequenz ankommt — mit der Folge,
        dass sich SETFREQs aufstauen und manche vom TRX gar nicht mehr
        sauber verarbeitet werden (null-Byte-Reply, ``?;``). Wir ziehen
        deshalb **alle wartenden SETFREQs vor** jegliche READFREQs.

        Die Reihenfolge *innerhalb* der SETFREQ-Gruppe bleibt FIFO; dito
        fuer READs.
        """
        try:
            first = self._write_q.get(timeout=timeout)
        except queue.Empty:
            return None
        if self._is_setfreq_cmd(first.command):
            return first
        # Der erste Eintrag ist *kein* SETFREQ – gibt es weiter hinten einen?
        with self._write_q.mutex:
            q = self._write_q.queue
            promoted_idx = -1
            for idx, cand in enumerate(q):
                if self._is_setfreq_cmd(cand.command):
                    promoted_idx = idx
                    break
            if promoted_idx < 0:
                # Kein SETFREQ in der Queue -> first (READ o. ae.) wird behandelt.
                return first
            promoted = q[promoted_idx]
            del q[promoted_idx]
            # "first" zurueck an den Kopf legen, damit er als Naechster dran ist,
            # sobald alle SETFREQs abgearbeitet sind.
            q.appendleft(first)
            # unfinished_tasks: wir haben first nicht verarbeitet, sondern
            # re-queued, dafuer aber promoted "aus dem Nichts" entnommen.
            # Netto-Aenderung 0, also nichts anpassen.
        if self._log_serial:
            self._log_write(
                "INFO",
                f"Rig-Bridge: COM Worker SETFREQ vorgezogen vor "
                f"{first.command!r} (Tuning-Prio)",
            )
        return promoted

    def _write_loop(self) -> None:
        while self._running:
            item = self._pop_next_command(timeout=0.2)
            if item is None:
                continue
            done_ev = item.done
            try:
                if self._log_serial:
                    ctx = f" {item.log_ctx}" if item.log_ctx else ""
                    self._log_write(
                        "INFO",
                        f"Rig-Bridge: COM Worker dequeue {item.command!r}{ctx}",
                    )
                t0 = time.monotonic()
                with self._io_lock:
                    response = self._send_and_read_unlocked(item)
                if self._debug_traffic and self._is_setfreq_cmd(item.command):
                    dt_ms = (time.monotonic() - t0) * 1000.0
                    self._log_write(
                        "INFO",
                        f"Rig-Bridge: COM SETFREQ in {dt_ms:.1f} ms ({item.command!r})",
                    )
                if self._is_setfreq_cmd(item.command) and self._setfreq_gap_s > 0:
                    time.sleep(self._setfreq_gap_s)
                if self._is_setfreq_cmd(item.command):
                    self._readfreq_suppress_until_mono = (
                        time.monotonic() + float(self._post_setfreq_read_suppress_s)
                    )
                if item.callback is not None:
                    item.callback(response)
                self._state.mark_success()
                try:
                    self._on_serial_activity()
                except Exception:
                    pass
            except Exception as exc:
                if self._is_fatal_link_error(exc):
                    self._drop_com_link(exc)
                    break
                self._state.set_error(str(exc))
                self._log_write("WARN", f"Rig-Bridge TX Fehler: {exc}")
            finally:
                if done_ev is not None:
                    try:
                        done_ev.set()
                    except Exception:
                        pass

    def _poll_loop(self) -> None:
        """Leichtgewichtig: kein CAT-Frequenz-Poll — aber regelmäßiger COM-„Lebenszeichen“-Check.

        Ohne Traffic merkt pyserial ein abgezogenes USB-Kabel oft erst beim nächsten Zugriff.
        ``in_waiting`` ist dafür geeignet (blockiert nicht wie ``read``). Bei Fehler: Session wie
        beim Hardware-Client beenden, LED/State werden frei.
        """
        probe_s = 2.0
        next_probe = time.monotonic() + probe_s
        while self._running:
            try:
                now = time.monotonic()
                if now >= next_probe:
                    next_probe = now + probe_s
                    probe_exc: BaseException | None = None
                    with self._io_lock:
                        ser = self._ser
                        if ser is not None:
                            try:
                                _ = ser.in_waiting
                                self._state.mark_success()
                            except Exception as e:
                                probe_exc = e
                    if probe_exc is not None and self._is_fatal_link_error(probe_exc):
                        self._drop_com_link(probe_exc)
                        break
                time.sleep(max(0.03, float(self._cfg.polling_interval_ms) / 1000.0))
            except Exception as exc:
                if self._is_fatal_link_error(exc):
                    self._drop_com_link(exc)
                    break
                self._state.set_error(str(exc))
                time.sleep(0.3)

    def _write_ptt_cat_unlocked(self, on: bool) -> None:
        """SETPTT: PTT per CAT (Yaesu ``TX0;``/``TX1;``), State danach."""
        ser = self._ser
        if ser is None:
            raise RigConnectionError("Keine aktive Funkgeräteverbindung")
        payload, desc = build_ptt_payload(
            self._cfg.rig_brand, on, self._cfg.rig_model, self._cfg.hamlib_rig_id
        )
        if not payload:
            self._state.update(ptt=on)
            self._log_write("WARN", f"Rig-Bridge: SETPTT ohne CAT: {desc}")
            return
        is_icom = "icom" in (self._cfg.rig_brand or "").lower()
        try:
            ser.reset_input_buffer()
        except Exception:
            pass
        self._log_serial_io("TX", payload, f"({desc})")
        ser.write(payload)
        ser.flush()
        if not is_icom:
            self._read_cat_response_logged(
                ser, is_icom=False, note="(SETPTT)", quick_drain=True
            )
        self._state.update(ptt=on)
        if self._debug_traffic or self._log_serial:
            self._log_write("INFO", f"Rig-Bridge: SETPTT erledigt: {desc}")

    def _write_setfreq_cat_unlocked(self, hz: int, log_ctx: str = "") -> None:
        """SETFREQ: nur CAT-Set (schnell); kein ``FA;``-Poll — sonst stufenweise Hamlib-Nutzung extrem langsam.

        Der Anzeige-State kommt bereits optimistisch vom Hamlib-Server; hier geht es nur noch ums Gerät.
        """
        ser = self._ser
        if ser is None:
            raise RigConnectionError("Keine aktive Funkgeräteverbindung")
        payload, _ = build_set_frequency_payload(
            self._cfg.rig_brand, hz, self._cfg.rig_model, self._cfg.hamlib_rig_id
        )
        is_icom = "icom" in (self._cfg.rig_brand or "").lower()
        try:
            ser.reset_input_buffer()
        except Exception:
            pass
        note = f"(SETFREQ {hz} Hz)"
        if log_ctx:
            note = f"{note} [{log_ctx}]"
        self._log_serial_io("TX", payload, note)
        ser.write(payload)
        ser.flush()
        if not is_icom:
            rx_note = "(SETFREQ)"
            if log_ctx:
                rx_note = f"{rx_note} [{log_ctx}]"
            self._read_cat_response_logged(
                ser, is_icom=False, note=rx_note, quick_drain=True
            )
        self._state.update(frequency_hz=int(hz))

    def _write_mode_cat_unlocked(self, mode: str) -> None:
        """SETMODE: Modus per CAT (Yaesu ``MD0…;``, Kenwood ``MD…;``), State danach."""
        ser = self._ser
        if ser is None:
            raise RigConnectionError("Keine aktive Funkgeräteverbindung")
        payload, desc = build_set_mode_payload(
            self._cfg.rig_brand, mode, self._cfg.rig_model, self._cfg.hamlib_rig_id
        )
        label = (mode or "").strip() or "USB"
        if not payload:
            self._state.update(mode=label)
            self._log_write("WARN", f"Rig-Bridge: SETMODE ohne CAT: {desc}")
            return
        is_icom = "icom" in (self._cfg.rig_brand or "").lower()
        try:
            ser.reset_input_buffer()
        except Exception:
            pass
        self._log_serial_io("TX", payload, f"({desc})")
        ser.write(payload)
        ser.flush()
        if not is_icom:
            self._read_cat_response_logged(
                ser, is_icom=False, note="(SETMODE)", quick_drain=True
            )
        self._state.update(mode=label)
        if self._debug_traffic or self._log_serial:
            self._log_write("INFO", f"Rig-Bridge: SETMODE erledigt: {desc}")

    def _send_and_read_unlocked(self, item: _WriteCommand) -> str:
        """Nur mit ``self._io_lock`` aufrufen."""
        ser = self._ser
        if ser is None:
            raise RigConnectionError("Keine aktive Funkgeräteverbindung")
        c = str(item.command).strip()
        up = c.upper()
        ctx = (item.log_ctx or "").strip()
        if up.startswith("SETFREQ "):
            try:
                hz = self._setfreq_hz_from_command(c)
            except (IndexError, ValueError) as exc:
                raise RigConnectionError("SETFREQ: ungueltiger Wert") from exc
            self._write_setfreq_cat_unlocked(hz, ctx)
            return ""
        if up.startswith("SETMODE "):
            tail = c.split(None, 1)[1].strip() if " " in c else ""
            mode = (tail.split()[0] if tail else "USB").strip() or "USB"
            self._write_mode_cat_unlocked(mode)
            return ""
        if up.startswith("SETPTT "):
            tail = (c.split(None, 1)[1].strip() if " " in c else "").lower()
            if tail in ("0", "off", "false", "rx", "no"):
                on = False
            elif tail in ("1", "on", "tx", "true", "yes"):
                on = True
            else:
                try:
                    on = int(float(tail.replace(",", "."))) != 0
                except ValueError:
                    on = False
            self._write_ptt_cat_unlocked(on)
            return ""
        if up == "READFREQ":
            return self._read_vfo_frequency_unlocked(ctx)

        payload = (str(c).strip() + "\n").encode("ascii", errors="ignore")
        self._log_serial_io("TX", payload, "(Rohbefehl)")
        ser.write(payload)
        raw = ser.readline()
        self._log_serial_io("RX", raw, "(Roh readline)")
        try:
            return raw.decode("ascii", errors="ignore").strip()
        except Exception:
            return ""

    def _read_vfo_frequency_unlocked(self, log_ctx: str = "") -> str:
        """``FA;`` senden und ``frequency_hz`` aus der Antwort parsen (VFO am Gerät)."""
        ser = self._ser
        if ser is None:
            raise RigConnectionError("Keine aktive Funkgeräte-Verbindung")
        now = time.monotonic()
        if now < self._readfreq_suppress_until_mono:
            if self._debug_traffic:
                self._log_write(
                    "INFO",
                    "Rig-Bridge: READFREQ übersprungen (kurz nach SETFREQ; kein Echo auf TRX)",
                )
            return ""
        # Aktive Tuning-Phase: letzter SETFREQ < _tuning_active_window_s her.
        # READFREQ bringt hier nichts — der optimistische State-Patch im
        # CatResponder liefert dem externen Programm bereits den richtigen Wert,
        # und der TRX braucht die CAT-Leitung gerade zum Durchschalten der SETs.
        tuning_win = float(self._tuning_active_window_s)
        if tuning_win > 0 and self._last_setfreq_enqueue_mono > 0:
            since_set = now - self._last_setfreq_enqueue_mono
            if 0.0 <= since_set < tuning_win:
                if self._debug_traffic or self._log_serial:
                    tail = f" [{log_ctx}]" if log_ctx else ""
                    self._log_write(
                        "INFO",
                        f"Rig-Bridge: READFREQ übersprungen (Tuning aktiv, "
                        f"letzter SETFREQ vor {since_set*1000:.0f} ms){tail}",
                    )
                return ""
        gap = float(self._readfreq_min_interval_s)
        if gap > 0 and (now - self._last_readfreq_cat_mono) < gap:
            if self._debug_traffic:
                self._log_write(
                    "INFO",
                    "Rig-Bridge: READFREQ übersprungen (Entprellung bei mehreren CAT-Abfragen)",
                )
            return ""
        self._last_readfreq_cat_mono = now
        read_start_mono = now
        cfg = self._cfg
        read_payload, _desc = build_read_vfo_frequency_query(cfg.rig_brand)
        if not read_payload:
            return ""
        try:
            ser.reset_input_buffer()
        except Exception:
            pass
        note = "(READFREQ)"
        if log_ctx:
            note = f"{note} [{log_ctx}]"
        self._log_serial_io("TX", read_payload, note)
        ser.write(read_payload)
        ser.flush()
        # FA…; endet mit Semikolon (Kenwood/Elecraft u. ä.); nicht CI-V \xFD.
        rx_read = self._read_cat_quick(ser, is_icom=False, max_wait_s=1.0)
        rx_read = rx_read.lstrip(b"\x00")
        self._log_serial_io("RX", rx_read, note)
        parsed = parse_fa_style_frequency_hz(rx_read)
        if parsed is not None:
            # Race-Guard: wenn waehrend des laufenden READFREQ ein neues
            # SETFREQ in die Queue gelegt wurde, ist der gerade vom TRX
            # gemeldete Wert "veraltet" (der TRX hat das neue SET noch nicht
            # ausgefuehrt). State nicht ueberschreiben.
            if self._last_setfreq_enqueue_mono > read_start_mono:
                if self._debug_traffic or self._log_serial:
                    tail = f" [{log_ctx}]" if log_ctx else ""
                    self._log_write(
                        "INFO",
                        f"Rig-Bridge: READFREQ-Reply {parsed / 1e6:.6f} MHz "
                        f"verworfen (SETFREQ in Flight, Race-Schutz){tail}",
                    )
            else:
                self._state.update(frequency_hz=int(parsed))
                if self._log_serial:
                    tail = f" [{log_ctx}]" if log_ctx else ""
                    self._log_write(
                        "INFO",
                        f"Rig-Bridge: VFO-Frequenz aus TRX (CAT) übernommen: "
                        f"{parsed / 1e6:.6f} MHz ({parsed} Hz){tail}",
                    )
        return ""

    def _frequency_test_set_then_read(
        self,
        ser,
        cfg: RigBridgeConfig,
        payload: bytes,
        is_icom: bool,
        log: Callable[[str, str], None],
        *,
        log_serial: bool = True,
    ) -> int | None:
        """SET-Frequenz senden, danach ``FA;`` lesen und Hz parsen (None bei Icom / ohne Lese-CAT)."""
        try:
            ser.reset_input_buffer()
        except Exception:
            pass
        if log_serial:
            log(
                "INFO",
                f"Rig-Bridge: COM TX {self._format_serial_payload(payload)} (Verbindungstest SET)",
            )
        ser.write(payload)
        ser.flush()
        # SET-Echo wie im Betrieb: kurz abtropfen (kein 1,5-s-Ephemeral-Warten).
        drain_s = max(0.05, float(cfg.cat_post_write_drain_ms) / 1000.0)
        rx_set = self._read_cat_quick(ser, is_icom=is_icom, max_wait_s=drain_s)
        if log_serial:
            log(
                "INFO",
                f"Rig-Bridge: COM RX {self._format_serial_payload(rx_set)} (Verbindungstest SET)",
            )
        self._log_rx_lines(log, rx_set, is_icom, role="SET")

        read_payload, read_desc = build_read_vfo_frequency_query(cfg.rig_brand)
        if not read_payload:
            log("INFO", read_desc)
            return None

        log("INFO", read_desc)
        time.sleep(0.05)
        try:
            ser.reset_input_buffer()
        except Exception:
            pass
        if log_serial:
            log(
                "INFO",
                f"Rig-Bridge: COM TX {self._format_serial_payload(read_payload)} (Verbindungstest READ)",
            )
        ser.write(read_payload)
        ser.flush()
        # Auf ``FA…;``-Antwort warten, aber mit kurzem Read-Timeout (kein Multi-Sekunden-Block).
        rx_read = self._read_cat_quick(ser, is_icom=is_icom, max_wait_s=1.5 if not is_icom else 0.8)
        if log_serial:
            log(
                "INFO",
                f"Rig-Bridge: COM RX {self._format_serial_payload(rx_read)} (Verbindungstest READ)",
            )
        self._log_rx_lines(log, rx_read, is_icom, role="READ")

        if is_icom or not rx_read:
            return None
        parsed = parse_fa_style_frequency_hz(rx_read)
        if parsed is not None:
            log(
                "INFO",
                f"Rig-Bridge Test: gelesene VFO-A-Frequenz {parsed / 1e6:.6f} MHz ({parsed} Hz)",
            )
        else:
            log(
                "WARN",
                "Rig-Bridge Test: READ-Antwort konnte nicht als FA-Frequenz gedeutet werden.",
            )
        return parsed

    def run_frequency_test_ephemeral(
        self,
        cfg: RigBridgeConfig,
        freq_hz: int,
        log: Callable[[str, str], None],
    ) -> tuple[bool, str]:
        """Set-Frequenz-CAT testen: über bestehende COM-Verbindung **oder** kurz separat öffnen.

        Wenn bereits „Verbinden“ aktiv ist, wird derselbe Port genutzt (mit Lock gegen den Worker).
        """
        if serial is None:
            return False, "pyserial ist nicht verfügbar"

        payload, desc = build_set_frequency_payload(
            cfg.rig_brand, freq_hz, cfg.rig_model, cfg.hamlib_rig_id
        )
        is_icom = "icom" in (cfg.rig_brand or "").lower()
        com = normalize_com_port(cfg.com_port)
        log("INFO", "Rig-Bridge Test: CAT Payload vorbereitet")
        log("INFO", desc)

        with self._lock:
            use_existing = self._ser is not None

        if use_existing:
            log("INFO", "Rig-Bridge Test: nutze bestehende COM-Verbindung (Worker pausiert kurz)")
            with self._io_lock:
                ser = self._ser
                if ser is None:
                    return False, "COM-Verbindung unerwartet getrennt"
                parsed = self._frequency_test_set_then_read(
                    ser, cfg, payload, is_icom, log, log_serial=cfg.log_serial_traffic
                )
            hz_state = parsed if parsed is not None else int(freq_hz)
            self._state.update(frequency_hz=hz_state)
            if parsed is not None:
                return (
                    True,
                    f"Test OK (Ziel {freq_hz / 1e6:.3f} MHz, gelesen {parsed / 1e6:.6f} MHz), siehe Log",
                )
            return True, f"Test OK ({freq_hz / 1e6:.3f} MHz), siehe Log"

        log("INFO", f"Rig-Bridge Test: oeffne {com} @ {cfg.baudrate} (kurz)")

        stopbits = serial.STOPBITS_ONE if cfg.stopbits == 1 else serial.STOPBITS_TWO
        parity_map = {
            "N": serial.PARITY_NONE,
            "E": serial.PARITY_EVEN,
            "O": serial.PARITY_ODD,
            "M": serial.PARITY_MARK,
            "S": serial.PARITY_SPACE,
        }
        ser = None
        try:
            ser = serial.Serial(
                port=com,
                baudrate=int(cfg.baudrate),
                bytesize=int(cfg.databits),
                stopbits=stopbits,
                parity=parity_map.get(cfg.parity, serial.PARITY_NONE),
                timeout=float(cfg.timeout_s),
            )
            parsed = self._frequency_test_set_then_read(
                ser, cfg, payload, is_icom, log, log_serial=cfg.log_serial_traffic
            )
            hz_state = parsed if parsed is not None else int(freq_hz)
            self._state.update(frequency_hz=hz_state)
            if parsed is not None:
                return (
                    True,
                    f"Test OK (Ziel {freq_hz / 1e6:.3f} MHz, gelesen {parsed / 1e6:.6f} MHz), siehe Log",
                )
            return True, f"Test OK ({freq_hz / 1e6:.3f} MHz), siehe Log"
        except Exception as exc:
            log("WARN", f"Rig-Bridge Test fehlgeschlagen: {exc}")
            return False, str(exc)
        finally:
            if ser is not None:
                try:
                    ser.close()
                except Exception:
                    pass
                log("INFO", "Rig-Bridge Test: COM geschlossen (nur Ephemeral-Pfad)")

    @staticmethod
    def _log_rx_lines(
        log: Callable[[str, str], None],
        rx: bytes,
        is_icom: bool,
        *,
        role: str,
    ) -> None:
        log("INFO", f"Rig-Bridge Test: {role} RX {len(rx)} Byte")
        if rx:
            log("INFO", f"Rig-Bridge Test: {role} RX hex: {rx.hex()}")
            if not is_icom:
                log(
                    "INFO",
                    f"Rig-Bridge Test: {role} RX ASCII: {rx.decode('ascii', errors='replace')!r}",
                )
        elif role.upper().startswith("SET"):
            log(
                "INFO",
                "Rig-Bridge Test: SET ohne sichtbare Antwort (Timeout) — bei vielen Yaesu-Geraeten normal; Lesebefehl prueft die Verbindung.",
            )
        else:
            log(
                "WARN",
                "Rig-Bridge Test: keine Antwort (Timeout). CAT-Menue am Geraet pruefen (38400, USB-CAT).",
            )

    @staticmethod
    def _read_cat_response_ephemeral(
        ser,
        *,
        is_icom: bool,
        min_deadline_s: float = 0.0,
    ) -> bytes:
        """Kurz auf Antwort warten (ASCII bis ';' bzw. Icom bis 0xFD)."""
        tmo = float(ser.timeout or 0.25)
        base = max(0.6, tmo * 3.0)
        if min_deadline_s > 0:
            base = max(base, float(min_deadline_s))
        deadline = time.monotonic() + base
        buf = b""
        while time.monotonic() < deadline:
            chunk = ser.read(128)
            if chunk:
                buf += chunk
                if is_icom and b"\xFD" in buf:
                    break
                if not is_icom and b";" in buf:
                    break
            else:
                time.sleep(0.02)
        return buf
