"""Zentrale Orchestrierung der Rig-Bridge."""

from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Any, Callable

from ..ports import list_serial_ports
from .cat_commands import normalize_com_port
from .config import RigBridgeConfig
from .protocol_flrig import FlrigBridgeServer
from .protocol_hamlib_net_rigctl import HamlibNetRigctlServer
from .radio_backend import RadioConnectionManager
from .state import RadioStateCache
from .status import RigBridgeStatusModel


class RigBridgeManager:
    """Verwaltet Funkgeräteverbindung und alle Protokolle."""

    def __init__(self, cfg_dict: dict, log_write: Callable[[str, str], None]):
        self._log_write = log_write
        self._cfg = RigBridgeConfig.from_dict(cfg_dict)
        self._state = RadioStateCache()
        self._state.update(selected_rig=self._cfg.selected_rig, com_port=self._cfg.com_port)
        self._lock = threading.RLock()
        self._diag_lines: list[str] = []
        self._rig_serial_activity_flag = False
        self._rig_flrig_activity_flag = False
        #: Ports, auf denen seit dem letzten UI-Takt TCP-Daten gingen (Hamlib mehrere Listener).
        self._rig_hamlib_activity_ports: set[int] = set()
        self._allow_auto_reconnect = True
        self._reconnect_stop = threading.Event()
        self._reconnect_thread = threading.Thread(
            target=self._auto_reconnect_loop,
            name="rig-bridge-auto-reconnect",
            daemon=True,
        )
        self._radio = RadioConnectionManager(
            self._state,
            log_write=self._log_and_diag,
            on_serial_activity=self._pulse_rig_serial_activity,
            on_link_lost=self._on_serial_link_lost,
        )
        self._radio.update_config(self._cfg)
        self._reconnect_thread.start()
        #: FLRig: HTTP-Clients schließen oft nach jeder XML-RPC-Anfrage — für die UI wird die
        #: Anzahl kurz gehalten (>0), damit LED und Zähler nicht zwischen 0/1 flackern.
        self._flrig_last_nonempty_ts: float = 0.0
        self._FLRIG_CLIENT_UI_HOLD_S = 2.5
        #: Kurzzeitig weniger offene Sockets als zuvor (z. B. 2→1 bei zwei Pollern) — Anzeige
        #: erst senken, wenn die niedrigere Zahl stabil bleibt (gegen 1↔2-Springen).
        self._flrig_peak_display: int = 0
        self._flrig_below_peak_since: float | None = None
        self._FLRIG_CLIENT_DOWN_HOLD_S = 1.4

        self._flrig = FlrigBridgeServer(
            get_state=self._state.snapshot,
            enqueue_write=self._enqueue_radio_write,
            on_clients_changed=self._on_flrig_clients_changed,
            log_write=self._log_and_diag,
            log_client_traffic=bool(self._cfg.log_serial_traffic),
            on_state_patch=self._hamlib_state_patch,
            on_tcp_activity=self._pulse_rig_flrig_activity,
            refresh_frequency_before_read=self.refresh_rig_frequency_from_cat,
        )
        #: Port → laufender rigctld-Server (mehrere Clients pro Port möglich)
        self._hamlib_servers: dict[int, HamlibNetRigctlServer] = {}
        self._hamlib_client_counts: dict[int, int] = {}

    def _on_flrig_clients_changed(self, n: int) -> None:
        n = max(0, int(n))
        with self._lock:
            if n > 0:
                self._flrig_last_nonempty_ts = time.monotonic()
        self._state.set_protocol_clients("flrig", n)

    def _log_and_diag(self, level: str, msg: str) -> None:
        """Diagnosefenster und ``rotortcpbridge.log``: gleicher Inhalt nur bei ``log_serial_traffic``.

        INFO/Diagnose: nur wenn „Rig-Befehle loggen“ aktiv. WARN/ERROR: immer in Datei + Fenster,
        damit Fehler nicht untergehen.
        """
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        stamped = f"[{ts}] {msg}"
        line = f"[{level}] {stamped}"
        with self._lock:
            traffic = bool(self._cfg.log_serial_traffic)
            important = str(level or "INFO").upper() in ("WARN", "ERROR", "CRITICAL")
            if not traffic and not important:
                return
            self._diag_lines.append(line)
            if len(self._diag_lines) > 500:
                self._diag_lines = self._diag_lines[-500:]
        self._log_write(level, stamped)

    def diagnostics_text(self) -> str:
        with self._lock:
            return "\n".join(self._diag_lines[-200:])

    def take_rig_activity_flags(self) -> tuple[bool, bool, set[int]]:
        """UI-Takt: COM-, Flrig-, Hamlib-TCP-Aktivität abholen (einmalige Pulse).

        Dritter Wert: Menge der Hamlib-Listener-Ports, auf denen seit dem letzten Abruf
        TCP-Verkehr war (für getrennte LED-Blinken pro Port).
        """
        with self._lock:
            s = self._rig_serial_activity_flag
            f = self._rig_flrig_activity_flag
            h_ports = set(self._rig_hamlib_activity_ports)
            self._rig_serial_activity_flag = False
            self._rig_flrig_activity_flag = False
            self._rig_hamlib_activity_ports.clear()
            return s, f, h_ports

    def _pulse_rig_serial_activity(self) -> None:
        with self._lock:
            self._rig_serial_activity_flag = True

    def _pulse_rig_flrig_activity(self) -> None:
        with self._lock:
            self._rig_flrig_activity_flag = True

    def _pulse_rig_hamlib_activity(self, port: int) -> None:
        with self._lock:
            self._rig_hamlib_activity_ports.add(int(port))

    def _on_serial_link_lost(self) -> None:
        """Unerwarteter COM-Verlust (USB) — Auto-Reconnect wieder erlauben."""
        with self._lock:
            self._allow_auto_reconnect = True

    def _auto_reconnect_loop(self) -> None:
        while not self._reconnect_stop.wait(timeout=2.5):
            try:
                self._try_auto_reconnect()
            except Exception:
                pass

    def _try_auto_reconnect(self) -> None:
        with self._lock:
            if not bool(self._cfg.enabled):
                return
            if not bool(self._cfg.auto_reconnect):
                return
            if not self._allow_auto_reconnect:
                return
            wanted = normalize_com_port(self._cfg.com_port).upper()
        if not wanted:
            return
        if self._radio.is_serial_connected():
            return
        ports_upper = {normalize_com_port(p).upper() for p in list_serial_ports()}
        if wanted not in ports_upper:
            return
        ok, _msg = self.connect_radio_and_autostart_protocols()
        if ok:
            self._log_and_diag(
                "INFO",
                "Rig-Bridge: automatisch wieder verbunden (COM wieder verfügbar).",
            )

    def update_config(self, cfg_dict: dict) -> None:
        """Konfiguration aktualisieren."""
        with self._lock:
            prev_traffic = bool(self._cfg.log_serial_traffic)
            self._cfg = RigBridgeConfig.from_dict(cfg_dict)
            if prev_traffic and not bool(self._cfg.log_serial_traffic):
                self._diag_lines.clear()
            self._radio.update_config(self._cfg)
            for srv in self._hamlib_servers.values():
                srv.set_debug_traffic(bool(self._cfg.hamlib.get("debug_traffic", False)))
                srv.set_log_serial_traffic(bool(self._cfg.log_serial_traffic))
            self._flrig.set_log_client_traffic(bool(self._cfg.log_serial_traffic))
            self._state.update(selected_rig=self._cfg.selected_rig, com_port=self._cfg.com_port)

    def get_config_dict(self) -> dict:
        return self._cfg.to_dict()

    def _enqueue_radio_write(self, command: str, log_ctx: str = "") -> None:
        self._radio.write_command(command, log_ctx=log_ctx)

    def enqueue_read_frequency(self) -> None:
        """VFO-Frequenz per CAT (``FA;``) vom Funkgerät lesen und in den State schreiben."""
        if not self._radio.is_serial_connected():
            return
        # Flrig (rig.get_vfo*) und Hamlib (``f``) lesen die Frequenz bereits pro Client — zusätzlicher
        # UI-Poll würde die COM-Schlange stauen (Doppel-READFREQ), verzögert Abstimmung und kann
        # sporadische ``.?;``/Parser-Stolperer am Yaesu-CAT begünstigen.
        n_fl = int(self._state.protocol_clients.get("flrig", 0) or 0)
        n_hm = int(self._state.protocol_clients.get("hamlib", 0) or 0)
        if n_fl > 0 or n_hm > 0:
            return
        self._radio.write_command(
            "READFREQ",
            log_ctx="Hauptfenster-Poll → TRX lesen",
        )

    def refresh_rig_frequency_from_cat(self, timeout_s: float = 0.65) -> bool:
        """Synchron ``READFREQ`` — für Flrig ``rig.get_vfo*`` / Hamlib ``f`` (frische VFO vom TRX)."""
        try:
            if not self._radio.is_serial_connected():
                return False
            return bool(
                self._radio.read_frequency_sync(
                    float(timeout_s),
                    log_ctx="FLRig/Hamlib-Abfrage → TRX lesen (z. B. rig.get_vfo)",
                )
            )
        except Exception:
            return False

    def enqueue_set_frequency_hz(self, hz: int) -> None:
        """Funkgerät auf ``hz`` abstimmen (serieller CAT-``SETFREQ``-Pfad wie Hamlib/Flrig)."""
        v = int(hz)
        if v <= 0:
            return
        self._radio.write_command(
            f"SETFREQ {v}",
            log_ctx="Rotor-UI (Kompass/Höhe/Frequenzfeld) → TRX",
        )

    def _hamlib_state_patch(self, patch: dict[str, Any]) -> None:
        """Vom Hamlib-Server (z. B. ``V VFOA``) kommende State-Änderungen."""
        if patch:
            self._state.update(**patch)

    def connect_radio(self) -> tuple[bool, str]:
        try:
            self._radio.connect()
            with self._lock:
                self._allow_auto_reconnect = True
            self._state.update(connected=True)
            return True, "Funkgerät verbunden"
        except Exception as exc:
            self._state.update(connected=False)
            self._state.set_error(str(exc))
            return False, str(exc)

    def connect_radio_and_autostart_protocols(self) -> tuple[bool, str]:
        """COM verbinden; bei Erfolg Flrig/Hamlib starten, sofern in der Konfiguration Autostart an ist."""
        ok, msg = self.connect_radio()
        if ok:
            self.start_enabled_protocols()
        return ok, msg

    def disconnect_radio(self) -> None:
        with self._lock:
            self._allow_auto_reconnect = False
        self._radio.disconnect()
        self._state.update(connected=False)

    def test_connection(self, freq_hz: int = 144_300_000) -> tuple[bool, str]:
        """Kurzer COM-Test ohne Dauer-Verbindung: Port öffnen, Set-Frequenz-CAT, Log, schließen.

        Wichtig: Wenn bereits „Verbinden“ aktiv ist, muss zuerst „Trennen“ gewählt werden,
        damit der Test exklusiv auf den COM-Port zugreifen kann.
        """
        self._log_and_diag("INFO", f"Rig-Bridge: Verbindungstest (CAT Set-Frequenz {freq_hz / 1e6:.6f} MHz) …")
        return self._radio.run_frequency_test_ephemeral(self._cfg, int(freq_hz), self._log_and_diag)

    def _hamlib_on_clients(self, port: int, n: int) -> None:
        self._hamlib_client_counts[port] = int(n)
        total = sum(self._hamlib_client_counts.values())
        self._state.set_protocol_clients("hamlib", total)

    def hamlib_listener_client_counts(self) -> dict[int, int]:
        """Port → Anzahl verbundener Hamlib-/rigctld-Clients (Hauptfenster: eine Zeile pro Port)."""
        with self._lock:
            return dict(self._hamlib_client_counts)

    def _stop_hamlib_servers(self) -> None:
        for srv in list(self._hamlib_servers.values()):
            srv.stop()
        self._hamlib_servers.clear()
        self._hamlib_client_counts.clear()
        self._state.set_protocol_clients("hamlib", 0)

    def _parse_hamlib_start_entries(self) -> tuple[list[tuple[int, str]] | None, str | None]:
        """Konfigurierte Listener: (Port, Anzeigename). Fehler → (None, Meldung)."""
        raw = self._cfg.hamlib.get("listeners")
        if not isinstance(raw, list):
            raw = []
        slots: list[tuple[int | None, str]] = []
        for it in raw:
            if not isinstance(it, dict):
                continue
            name = str(it.get("name", "") or "").strip()
            if "port" not in it or it.get("port") in (None, ""):
                slots.append((None, name))
                continue
            try:
                p = int(it["port"])
            except (TypeError, ValueError):
                return None, "Hamlib: ungültiger Port in der Konfiguration."
            p = max(1, min(65535, p))
            slots.append((p, name))
        if len(slots) == 1 and slots[0][0] is None:
            return None, "Hamlib: Port eingeben oder die leere Zeile entfernen."
        resolved = [(p, n) for p, n in slots if p is not None]
        if not resolved:
            return None, "Hamlib: mindestens einen gültigen Port eintragen."
        ports = [p for p, _ in resolved]
        if len(ports) != len(set(ports)):
            return None, "Hamlib: jeder Port darf nur einmal vorkommen."
        return resolved, None

    def _make_hamlib_server(self, port: int, log_label: str) -> HamlibNetRigctlServer:
        return HamlibNetRigctlServer(
            get_state=self._state.snapshot,
            enqueue_write=self._enqueue_radio_write,
            on_clients_changed=lambda n, pp=port: self._hamlib_on_clients(pp, n),
            log_write=self._log_and_diag,
            on_state_patch=self._hamlib_state_patch,
            debug_traffic=bool(self._cfg.hamlib.get("debug_traffic", False)),
            log_serial_traffic=bool(self._cfg.log_serial_traffic),
            log_label=log_label,
            on_tcp_activity=lambda pp=port: self._pulse_rig_hamlib_activity(pp),
            refresh_frequency_for_read=self.refresh_rig_frequency_from_cat,
        )

    def start_protocol(self, name: str) -> tuple[bool, str]:
        st = self._state.snapshot()
        if not st.get("connected", False):
            return False, "Funkgerät nicht verbunden"
        try:
            if name == "flrig":
                self._flrig.start(self._cfg.flrig.get("host", "127.0.0.1"), int(self._cfg.flrig.get("port", 12345)))
            elif name == "hamlib":
                entries, err = self._parse_hamlib_start_entries()
                if err:
                    return False, err
                assert entries is not None
                self._stop_hamlib_servers()
                host = str(self._cfg.hamlib.get("host", "127.0.0.1")).strip() or "127.0.0.1"
                try:
                    for port, label in entries:
                        srv = self._make_hamlib_server(port, label)
                        srv.start(host, port)
                        self._hamlib_servers[port] = srv
                except Exception:
                    self._stop_hamlib_servers()
                    raise
            else:
                return False, "Unbekanntes Protokoll"
            self._state.set_protocol_active(name, True)
            return True, f"{name} gestartet"
        except Exception as exc:
            self._state.set_protocol_active(name, False)
            self._state.set_error(str(exc))
            return False, str(exc)

    def stop_protocol(self, name: str) -> None:
        try:
            if name == "flrig":
                self._flrig.stop()
                with self._lock:
                    self._flrig_last_nonempty_ts = 0.0
                    self._flrig_peak_display = 0
                    self._flrig_below_peak_since = None
            elif name == "hamlib":
                self._stop_hamlib_servers()
        finally:
            self._state.set_protocol_active(name, False)
            self._state.set_protocol_clients(name, 0)

    def start_enabled_protocols(self) -> None:
        """TCP-Protokolle starten — nur sinnvoll, wenn ``connected`` (COM) bereits steht."""
        if bool(self._cfg.flrig.get("enabled", False)) and bool(self._cfg.flrig.get("autostart", False)):
            self.start_protocol("flrig")
        if bool(self._cfg.hamlib.get("enabled", False)) and bool(self._cfg.hamlib.get("autostart", False)):
            self.start_protocol("hamlib")

    def stop_all(self) -> None:
        self._reconnect_stop.set()
        for name in ("flrig", "hamlib"):
            self.stop_protocol(name)
        self.disconnect_radio()
        if self._reconnect_thread.is_alive():
            self._reconnect_thread.join(timeout=4.0)

    def status_model(self) -> RigBridgeStatusModel:
        st = self._state.snapshot()
        raw_pc = dict(st.get("protocol_clients", {}))
        raw_fl = int(raw_pc.get("flrig", 0) or 0)
        fl_active = bool(st.get("protocol_active", {}).get("flrig", False))
        now = time.monotonic()
        hold_empty = float(self._FLRIG_CLIENT_UI_HOLD_S)
        hold_down = float(self._FLRIG_CLIENT_DOWN_HOLD_S)
        with self._lock:
            if not fl_active:
                self._flrig_peak_display = 0
                self._flrig_below_peak_since = None
            else:
                if raw_fl > self._flrig_peak_display:
                    self._flrig_peak_display = raw_fl
                    self._flrig_below_peak_since = None
                elif raw_fl < self._flrig_peak_display:
                    if self._flrig_below_peak_since is None:
                        self._flrig_below_peak_since = now
                    elif (now - self._flrig_below_peak_since) >= hold_down:
                        self._flrig_peak_display = raw_fl
                        self._flrig_below_peak_since = None
                else:
                    self._flrig_below_peak_since = None
            peak_disp = int(self._flrig_peak_display)
            last_ts = float(self._flrig_last_nonempty_ts)
        disp_fl = peak_disp
        if fl_active and raw_fl == 0 and last_ts > 0.0 and (now - last_ts) < hold_empty:
            disp_fl = max(disp_fl, 1)
        raw_pc["flrig"] = int(disp_fl)
        return RigBridgeStatusModel(
            module_enabled=bool(self._cfg.enabled),
            connecting=bool(self._radio.connecting),
            radio_connected=bool(st.get("connected", False)),
            selected_rig=str(st.get("selected_rig", "")),
            rig_brand=str(getattr(self._cfg, "rig_brand", "") or ""),
            rig_model=str(getattr(self._cfg, "rig_model", "") or ""),
            com_port=str(st.get("com_port", "")),
            frequency_hz=int(st.get("frequency_hz", 0)),
            mode=str(st.get("mode", "USB")),
            ptt=bool(st.get("ptt", False)),
            vfo=str(st.get("vfo", "A")),
            last_error=str(st.get("last_error", "")),
            last_contact_ts=float(st.get("last_success_ts", 0.0)),
            protocol_active=dict(st.get("protocol_active", {})),
            protocol_clients=raw_pc,
        )
