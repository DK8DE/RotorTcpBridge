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


_DEFAULT_FLRIG: dict[str, Any] = {
    "enabled": False,
    "host": "127.0.0.1",
    "port": 12345,
    "autostart": False,
    "log_tcp_traffic": True,
}
_DEFAULT_HAMLIB: dict[str, Any] = {
    "enabled": False,
    "host": "127.0.0.1",
    "listeners": [{"port": 4532, "name": ""}],
    "autostart": False,
    "debug_traffic": False,
    "log_tcp_traffic": False,
}


def _normalize_rb_dict(
    cfg_dict: dict | None,
) -> tuple[list[dict], str, bool, dict, dict]:
    """Akzeptiert neue UND alte ``rig_bridge``-Struktur und liefert
    ``(profiles, active_id, global_enabled, flrig, hamlib)``.

    Flrig und Hamlib sind globale TCP-Server-Einstellungen und leben
    daher auf der obersten ``rig_bridge``-Ebene (nicht mehr pro Rig-Profil).

    - Neue Form: ``{"enabled": bool, "active_rig_id": str,
      "flrig": {..}, "hamlib": {..}, "rigs": [..]}``.
    - Alte Form (flach): alles direkt unter ``rig_bridge`` → als ein Profil
      mit ``id="default"`` verpackt; ``flrig``/``hamlib`` werden als global
      uebernommen (falls vorhanden).
    - Aeltere Profilstruktur mit flrig/hamlib pro Profil: hochheben auf
      Top-Level (aus aktivem oder erstem Profil), Profile werden bereinigt.
    """
    src = dict(cfg_dict or {})
    rigs = src.get("rigs")
    flrig_src: dict = dict(src.get("flrig") or {})
    hamlib_src: dict = dict(src.get("hamlib") or {})
    if isinstance(rigs, list) and rigs:
        profiles: list[dict] = []
        for pr in rigs:
            if not isinstance(pr, dict):
                continue
            p = dict(pr)
            p.setdefault("id", f"rig_{len(profiles)}")
            p.setdefault("name", str(p.get("selected_rig", "") or p["id"]))
            p.setdefault("enabled", True)
            profiles.append(p)
        if not profiles:
            profiles = [{"id": "default", "name": "Rig 1", "enabled": True}]
        active_id = str(src.get("active_rig_id", "") or profiles[0]["id"])
        if not any(p["id"] == active_id for p in profiles):
            active_id = str(profiles[0]["id"])
        # Uebergangsbetrieb: falls flrig/hamlib noch in einem Profil stecken,
        # einmalig nach oben ziehen (aktives oder erstes Profil). Anschliessend
        # entfernen, damit sie tatsaechlich global wirken.
        if not flrig_src or not hamlib_src:
            donor = next(
                (p for p in profiles if p["id"] == active_id),
                profiles[0],
            )
            if not flrig_src and isinstance(donor.get("flrig"), dict):
                flrig_src = dict(donor["flrig"])
            if not hamlib_src and isinstance(donor.get("hamlib"), dict):
                hamlib_src = dict(donor["hamlib"])
        for p in profiles:
            p.pop("flrig", None)
            p.pop("hamlib", None)
            p.pop("cat_tcp", None)
        flrig = dict(_DEFAULT_FLRIG)
        flrig.update(flrig_src)
        hamlib = dict(_DEFAULT_HAMLIB)
        hamlib.update(hamlib_src)
        return profiles, active_id, bool(src.get("enabled", False)), flrig, hamlib

    # Flache Altstruktur
    flat = {
        k: v
        for k, v in src.items()
        if k not in ("rigs", "active_rig_id", "flrig", "hamlib", "cat_tcp")
    }
    selected = str(flat.get("selected_rig", "") or "").strip() or "Rig 1"
    profile = dict(flat)
    profile.setdefault("id", "default")
    profile.setdefault("name", selected)
    profile.setdefault("enabled", True)
    # Die TCP-Server-Konfiguration wurde in der alten flachen Form ggf.
    # bereits unter flrig/hamlib mitgefuehrt — uebernehmen, ansonsten Defaults.
    flrig = dict(_DEFAULT_FLRIG)
    flrig.update(flrig_src)
    hamlib = dict(_DEFAULT_HAMLIB)
    hamlib.update(hamlib_src)
    return (
        [profile],
        str(profile["id"]),
        bool(flat.get("enabled", False)),
        flrig,
        hamlib,
    )


def _profile_as_radio_cfg(
    profile: dict,
    global_enabled: bool,
    flrig: dict,
    hamlib: dict,
) -> RigBridgeConfig:
    """Ein Profil-Dict + globale TCP-Settings in eine ``RigBridgeConfig``
    fuer den ``RadioConnectionManager`` umwandeln.

    Das globale ``enabled`` und das Profil-``enabled`` werden zu einem
    einzigen ``enabled`` fuer die alte Struktur verknuepft (beide muessen
    True sein, damit die Bruecke aktiv ist). ``flrig``/``hamlib``
    stammen aus der obersten ``rig_bridge``-Ebene und sind profilunabhaengig.
    """
    flat = dict(profile)
    flat.pop("flrig", None)
    flat.pop("hamlib", None)
    flat.pop("cat_tcp", None)
    flat["enabled"] = bool(global_enabled) and bool(profile.get("enabled", True))
    flat["flrig"] = dict(flrig or {})
    flat["hamlib"] = dict(hamlib or {})
    return RigBridgeConfig.from_dict(flat)


class RigBridgeManager:
    """Verwaltet Funkgeräteverbindung und alle Protokolle.

    Arbeitet intern mit einer Liste von Rig-Profilen; genau eines ist
    aktiv und hat Verbindung zur realen seriellen CAT-Leitung. Alle
    TCP-Protokolle (FLRig/Hamlib) und CAT-Simulation auf virtuellen COMs
    beziehen sich stets auf das aktive Profil.
    """

    def __init__(self, cfg_dict: dict, log_write: Callable[[str, str], None]):
        self._log_write = log_write
        profiles, active_id, global_enabled, flrig, hamlib = _normalize_rb_dict(cfg_dict)
        self._profiles: list[dict] = profiles
        self._active_id: str = active_id
        self._global_enabled: bool = global_enabled
        self._flrig_cfg: dict = dict(flrig)
        self._hamlib_cfg: dict = dict(hamlib)
        active_profile = self._get_profile_dict(active_id) or profiles[0]
        self._cfg = _profile_as_radio_cfg(
            active_profile, global_enabled, self._flrig_cfg, self._hamlib_cfg
        )
        self._state = RadioStateCache()
        self._state.update(
            selected_rig=self._cfg.selected_rig,
            com_port=self._cfg.com_port,
            active_rig_id=str(active_profile.get("id", "")),
            active_rig_name=str(active_profile.get("name", "")),
        )
        self._lock = threading.RLock()
        #: ``refresh_rig_frequency_from_cat`` legt nur ein READFREQ in die
        #: Worker-Queue (blockiert nicht auf die CAT-Antwort). Trotzdem darf
        #: der Aufruf **nicht direkt** aus Hamlib-/Flrig-Socket-Threads erfolgen:
        #: unter Last kann die Interaktion mit der Queue kurz warten und dann
        #: der TCP-Client (z. B. QLog) das gesamte Programm „einfrieren“ wirken
        #: lassen. Stattdessen ``request_cat_refresh_async`` (Event + Hintergrund-
        #: Thread) nutzen.
        self._cat_refresh_lock = threading.Lock()
        self._last_cat_refresh_mono: float = 0.0
        self._cat_refresh_min_interval_s: float = 0.30
        #: Letzte Log-Zeit, wenn UI-READFREQ wegen Flrig/Hamlib ausbleibt (sonst „Ruhe“ im Log).
        self._ui_poll_skip_log_mono: float | None = None
        self._ui_poll_skip_log_cooldown_s: float = 75.0
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
        self._cat_refresh_stop = threading.Event()
        self._cat_refresh_wakeup = threading.Event()
        self._cat_refresh_thread = threading.Thread(
            target=self._cat_refresh_bridge_loop,
            name="rig-bridge-cat-refresh",
            daemon=True,
        )
        self._cat_refresh_thread.start()
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
            log_write=self._flrig_protocol_log,
            log_client_traffic=bool(
                self._cfg.log_serial_traffic or self._cfg.flrig.get("log_tcp_traffic", True)
            ),
            on_state_patch=self._hamlib_state_patch,
            on_tcp_activity=self._pulse_rig_flrig_activity,
            refresh_frequency_before_read=self.flrig_refresh_frequency_before_read,
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

    def _hamlib_protocol_log(self, level: str, msg: str) -> None:
        """Nur Hamlib rigctld: Aufruf nur bei aktivem TCP-/Voll-Diagnose-Logging.

        Immer ins Hauptlog (``_log_write``) und in die Rig-Diagnosezeilen —
        unabhaengig von ``log_serial_traffic``, damit Hamlib-Analyse ohne
        vollstaendiges COM-Protokoll moeglich ist.
        """
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        stamped = f"[{ts}] {msg}"
        line = f"[{level}] {stamped}"
        with self._lock:
            self._diag_lines.append(line)
            if len(self._diag_lines) > 500:
                self._diag_lines = self._diag_lines[-500:]
        self._log_write(level, stamped)

    def _flrig_protocol_log(self, level: str, msg: str) -> None:
        """Flrig TCP/XML-RPC: wie Hamlib — immer sichtbar, wenn der Flrig-Server loggt.

        Unabhaengig von ``log_serial_traffic``, damit Port-Traffic analysierbar ist.
        """
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        stamped = f"[{ts}] {msg}"
        line = f"[{level}] {stamped}"
        with self._lock:
            self._diag_lines.append(line)
            if len(self._diag_lines) > 500:
                self._diag_lines = self._diag_lines[-500:]
        self._log_write(level, stamped)

    def diagnostics_text(self) -> str:
        with self._lock:
            return "\n".join(self._diag_lines[-200:])

    def take_rig_activity_flags(self) -> tuple[bool, bool, set[int]]:
        """UI-Takt: COM-, Flrig- und Hamlib-Aktivität abholen (einmalige Pulse).

        Dritter Wert: Hamlib-Listener-Ports mit TCP seit letztem Abruf.
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
        """Unerwarteter COM-Verlust (USB): TCP-Brücken stoppen, Auto-Reconnect erlauben.

        Ohne Stopp bleiben Flrig/Hamlib-Server offen; nach Wiederverbindung meldet ``start()``
        ggf. still (gleicher Host/Port), während Clients noch alten Zustand erwarten.
        """
        try:
            self.stop_protocol("flrig")
        except Exception:
            pass
        try:
            self.stop_protocol("hamlib")
        except Exception:
            pass
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
        """Konfiguration aktualisieren — neue Struktur (``rigs`` + ``active_rig_id``)
        oder alte flache Struktur. Beim Wechsel des aktiven Profils wird die
        bestehende COM-Verbindung sauber getrennt, bevor die neue Konfiguration
        uebernommen wird (sonst kollidiert der serielle Worker mit dem neuen
        Port).
        """
        profiles, active_id, global_enabled, flrig, hamlib = _normalize_rb_dict(cfg_dict)
        prev_active = self._active_id
        prev_global = bool(self._global_enabled)
        active_profile = next((p for p in profiles if p["id"] == active_id), profiles[0])
        profile_switched = bool(prev_active and prev_active != active_id)

        # Wechsel des aktiven Profils → alte Verbindung trennen.
        if profile_switched:
            try:
                self.stop_protocol("flrig")
            except Exception:
                pass
            try:
                self.stop_protocol("hamlib")
            except Exception:
                pass
            try:
                self.disconnect_radio()
            except Exception:
                pass

        with self._lock:
            self._profiles = profiles
            self._active_id = str(active_id)
            self._global_enabled = bool(global_enabled)
            self._flrig_cfg = dict(flrig)
            self._hamlib_cfg = dict(hamlib)
            prev_traffic = bool(self._cfg.log_serial_traffic)
            self._cfg = _profile_as_radio_cfg(
                active_profile,
                global_enabled,
                self._flrig_cfg,
                self._hamlib_cfg,
            )
            if prev_traffic and not bool(self._cfg.log_serial_traffic):
                self._diag_lines.clear()
            self._radio.update_config(self._cfg)
            for srv in self._hamlib_servers.values():
                srv.set_debug_traffic(bool(self._cfg.hamlib.get("debug_traffic", False)))
                srv.set_log_serial_traffic(bool(self._cfg.log_serial_traffic))
                srv.set_log_tcp_traffic(bool(self._cfg.hamlib.get("log_tcp_traffic", False)))
            self._flrig.set_log_client_traffic(
                bool(self._cfg.log_serial_traffic or self._cfg.flrig.get("log_tcp_traffic", True))
            )
            self._state.update(
                selected_rig=self._cfg.selected_rig,
                com_port=self._cfg.com_port,
                active_rig_id=str(active_profile.get("id", "")),
                active_rig_name=str(active_profile.get("name", "")),
            )
            now_enabled = bool(self._cfg.enabled)
            auto_conn = bool(self._cfg.auto_connect)
            # Nach Profilwechsel ODER Aktivierung der Rig-Bridge muss der
            # Auto-Reconnect-Pfad wieder greifen. ``disconnect_radio()``
            # hatte ``_allow_auto_reconnect`` auf False gesetzt.
            if now_enabled and (profile_switched or (not prev_global and now_enabled)):
                self._allow_auto_reconnect = True

        # Ausserhalb des Locks: bei enabled + auto_connect direkt verbinden,
        # wenn noch nicht verbunden. Andernfalls uebernimmt der
        # Auto-Reconnect-Loop innerhalb weniger Sekunden.
        try:
            if now_enabled and auto_conn and not self._radio.is_serial_connected():
                self.connect_radio_and_autostart_protocols()
        except Exception:
            pass

    def get_config_dict(self) -> dict:
        """Komplettes ``rig_bridge``-Dict (neue Struktur) mit aktivem Zustand."""
        return {
            "enabled": bool(self._global_enabled),
            "active_rig_id": str(self._active_id),
            "flrig": dict(self._flrig_cfg),
            "hamlib": dict(self._hamlib_cfg),
            "rigs": [dict(p) for p in self._profiles],
        }

    # ------------------------------------------------------------ Profiles
    def _get_profile_dict(self, rig_id: str) -> dict | None:
        rig_id = str(rig_id or "")
        for p in self._profiles:
            if str(p.get("id", "")) == rig_id:
                return p
        return None

    def list_profiles(self) -> list[dict]:
        """Profile-Uebersicht fuer UI / Listener-Manager.

        Liefert Kopien mit den fuer Anzeige wichtigen Feldern
        (``id``, ``name``, ``rig_brand``, ``rig_model``, ``enabled``,
        ``com_port``). Reihenfolge entspricht Config.
        """
        out: list[dict] = []
        for p in self._profiles:
            out.append(
                {
                    "id": str(p.get("id", "")),
                    "name": str(p.get("name", "") or p.get("selected_rig", "") or p.get("id", "")),
                    "rig_brand": str(p.get("rig_brand", "") or ""),
                    "rig_model": str(p.get("rig_model", "") or ""),
                    "enabled": bool(p.get("enabled", True)),
                    "com_port": str(p.get("com_port", "") or ""),
                    "hamlib_rig_id": int(p.get("hamlib_rig_id", 0) or 0),
                }
            )
        return out

    def active_rig_id(self) -> str:
        with self._lock:
            return str(self._active_id)

    def get_profile(self, rig_id: str) -> dict | None:
        """Rohes Profil-Dict (Kopie) zum angegebenen Rig-Profil."""
        p = self._get_profile_dict(rig_id)
        return dict(p) if p is not None else None

    def set_active_profile(self, rig_id: str) -> tuple[bool, str]:
        """Aktives Rig-Profil umschalten.

        Schritte: Protokolle stoppen → COM trennen → Config uebernehmen
        → bei ``auto_connect`` neu verbinden. Gibt ``(ok, msg)`` zurueck
        (ok=False wenn Profil unbekannt).
        """
        rig_id = str(rig_id or "")
        if rig_id == self._active_id:
            return True, "bereits aktiv"
        if self._get_profile_dict(rig_id) is None:
            return False, "unbekanntes Profil"
        # Neue Config bauen, aber mit identischer Profiltabelle — nur
        # ``active_rig_id`` aendert sich.
        new_cfg = {
            "enabled": bool(self._global_enabled),
            "active_rig_id": rig_id,
            "flrig": dict(self._flrig_cfg),
            "hamlib": dict(self._hamlib_cfg),
            "rigs": [dict(p) for p in self._profiles],
        }
        # ``update_config`` uebernimmt Disconnect alt + Reconnect neu
        # (sofern ``enabled`` + ``auto_connect``).
        self.update_config(new_cfg)
        return True, "umgeschaltet"

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
            now = time.monotonic()
            last = self._ui_poll_skip_log_mono
            cd = float(self._ui_poll_skip_log_cooldown_s)
            if last is None or (now - last) >= cd:
                self._ui_poll_skip_log_mono = now
                bits: list[str] = []
                if n_fl > 0:
                    bits.append(f"Flrig {n_fl} TCP-Client(s)")
                if n_hm > 0:
                    bits.append(f"Hamlib rigctl {n_hm} TCP-Client(s)")
                self._log_write(
                    "INFO",
                    "Rig-Bridge: UI-READFREQ (Hauptfenster-Poll) ist aus — "
                    + ", ".join(bits)
                    + ". CAT-Frequenz kommt von dort (async); COM-Details wie gewohnt "
                    "über „Rig-Befehle loggen“ / Hamlib-Diagnose. "
                    f"Diese Meldung höchstens alle {int(cd)} s.",
                )
            return
        self._ui_poll_skip_log_mono = None
        self._radio.write_command(
            "READFREQ",
            log_ctx="Hauptfenster-Poll → TRX lesen",
        )

    def refresh_rig_frequency_from_cat(self, timeout_s: float = 0.65) -> bool:
        """Asynchron ``READFREQ`` anfordern — der Aufrufer blockiert **nicht**.

        Frueher war das ein synchroner Aufruf (``read_frequency_sync``), der
        den Listener-Thread bis zu 650 ms schlafen legte. Ein externes Programm
        wie Ham Radio Deluxe pollt ``FA;`` aber mit 5–20 Hz — das hat:

        * den Listener-Thread an der Byte-Aufnahme vom com0com-Port gehindert
          (OS-Puffer lief voll, SET-Befehle kamen verspaetet),
        * die Worker-Queue mit READFREQs geflutet, sodass SETFREQ dahinter
          auflief, und
        * den echten Funkgeraet mit zu dicht aneinander liegenden Befehlen
          ueberlastet (Yaesu newcat: ``?;``-Antworten, verschluckte Bytes).

        Neues Verhalten:

        * Sofort ``True`` zurueckgeben — der Aufrufer nutzt den aktuellen
          Cache-Wert (``_state.frequency_hz``).
        * Wenn seit dem letzten Enqueue mindestens
          ``_cat_refresh_min_interval_s`` vergangen ist, wird ein einzelnes
          ``READFREQ`` auf die Worker-Queue gelegt (fire-and-forget) — der
          Cache wird dann in ~30 ms bis zum naechsten Poll aktualisiert.
        * Der ``timeout_s``-Parameter bleibt aus Kompatibilitaet erhalten,
          wird aber nicht mehr zum Blockieren verwendet.
        """
        try:
            if not self._radio.is_serial_connected():
                return False
            now = time.monotonic()
            with self._cat_refresh_lock:
                if (now - self._last_cat_refresh_mono) < self._cat_refresh_min_interval_s:
                    return True
                self._last_cat_refresh_mono = now
            self._radio.write_command(
                "READFREQ",
                log_ctx="CAT/Flrig/Hamlib → TRX lesen (async)",
            )
            return True
        except Exception:
            return False

    def _cat_refresh_bridge_loop(self) -> None:
        """Nur in ``_cat_refresh_thread``: READFREQ-Anforderungen von Hamlib/Flrig bündeln.

        Hamlib ``f`` / Flrig ``rig.get_vfo*`` feuern sehr schnell — mehrere
        ``set()`` auf dem Event werden zu einem Durchlauf von
        ``refresh_rig_frequency_from_cat`` zusammengefasst (zusätzliche
        Entlastung durch ``_cat_refresh_min_interval_s`` dort).
        """
        while not self._cat_refresh_stop.is_set():
            if not self._cat_refresh_wakeup.wait(timeout=0.4):
                continue
            self._cat_refresh_wakeup.clear()
            if self._cat_refresh_stop.is_set():
                break
            try:
                self.refresh_rig_frequency_from_cat()
            except Exception:
                pass

    def request_cat_refresh_async(self) -> bool:
        """READFREQ vom TRX anfordern — **ohne** den aufrufenden Socket-/HTTP-Thread zu belasten.

        Sofort ``True``, wenn die Anforderung übernommen wurde (oder Stop
        aktiv ist: ``False``). Die eigentliche Queue-Operation läuft im
        Hintergrund-Thread ``_cat_refresh_bridge_loop``.
        """
        if self._cat_refresh_stop.is_set():
            return False
        try:
            self._cat_refresh_wakeup.set()
            return True
        except Exception:
            return False

    def flrig_refresh_frequency_before_read(self) -> bool:
        """Vor Flrig ``rig.get_vfo*`` / ``main.get_freq*``: bei leerem Frequenz-Cache kurz synchron ``FA``.

        Strikte Logger (z. B. QLog) zeigen sonst eine leere VFO-Zeile, weil die erste
        XML-RPC-Antwort noch ``0`` Hz liefert, während ``READFREQ`` erst asynchron
        nachzieht. Einmaliges kurzes Blocken nur bei ``frequency_hz==0``; danach
        wie gewohnt asynchron über ``request_cat_refresh_async``.
        """
        try:
            if not self._radio.is_serial_connected():
                return self.request_cat_refresh_async()
            hz = int(self._state.snapshot().get("frequency_hz", 0) or 0)
            if hz <= 0:
                return self._radio.read_frequency_sync(
                    0.38,
                    log_ctx="Flrig rig.get_vfo* (sync, Cache 0 Hz)",
                )
        except Exception:
            pass
        return self.request_cat_refresh_async()

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
        """COM verbinden; bei Erfolg Flrig/Hamlib starten, sofern Autostart an ist."""
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
        prev = int(self._hamlib_client_counts.get(port, 0))
        self._hamlib_client_counts[port] = int(n)
        total = sum(self._hamlib_client_counts.values())
        self._state.set_protocol_clients("hamlib", total)
        if int(n) > prev:
            self._log_write(
                "INFO",
                "Hamlib rigctl: TCP-Client verbunden auf Port "
                f"{port} (je Port: {dict(self._hamlib_client_counts)}, Summe={total})",
            )
        elif int(n) < prev:
            self._log_write(
                "INFO",
                "Hamlib rigctl: TCP-Client getrennt von Port "
                f"{port} (je Port: {dict(self._hamlib_client_counts)}, Summe={total})",
            )

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

    def _snapshot_active_profile(self) -> dict:
        with self._lock:
            p = self._get_profile_dict(self._active_id)
            return dict(p) if p is not None else {}

    def _hamlib_reserved_ports(self) -> set[int]:
        out: set[int] = set()
        raw = self._hamlib_cfg.get("listeners")
        if not isinstance(raw, list):
            return out
        for it in raw:
            if not isinstance(it, dict) or it.get("port") in (None, ""):
                continue
            try:
                out.add(max(1, min(65535, int(it["port"]))))
            except (TypeError, ValueError):
                continue
        return out

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
            log_write=self._hamlib_protocol_log,
            on_state_patch=self._hamlib_state_patch,
            debug_traffic=bool(self._cfg.hamlib.get("debug_traffic", False)),
            log_serial_traffic=bool(self._cfg.log_serial_traffic),
            log_tcp_traffic=bool(self._cfg.hamlib.get("log_tcp_traffic", False)),
            log_label=log_label,
            on_tcp_activity=lambda pp=port: self._pulse_rig_hamlib_activity(pp),
            refresh_frequency_for_read=self.request_cat_refresh_async,
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
        self._cat_refresh_stop.set()
        self._cat_refresh_wakeup.set()
        t_cat = getattr(self, "_cat_refresh_thread", None)
        if t_cat is not None and t_cat.is_alive():
            t_cat.join(timeout=1.5)
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
