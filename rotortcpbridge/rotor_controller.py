from __future__ import annotations
import time
import re
from typing import Callable, Optional
from .rs485_protocol import build, Telegram
from .hardware_client import HardwareClient, HwRequest
from .rotor_model import AxisState

def _parse_float(s:str)->Optional[float]:
    try:
        return float(s.strip().replace(",", "."))
    except Exception:
        return None

def _parse_int(s:str)->Optional[int]:
    try:
        return int(float(s.strip().replace(",", ".")))
    except Exception:
        return None

def _parse_float_any(s:str)->Optional[float]:
    """Extrahiert den ersten Float aus beliebigem PARAMS-Text.

    Hintergrund: Manche ACKs liefern nicht nur einen nackten Zahlenwert,
    sondern zusätzliche Teile (z.B. mit ';'). Für die Windanzeige wollen wir
    trotzdem robust den Messwert übernehmen.
    """
    try:
        txt = str(s or "").strip()
    except Exception:
        return None
    if not txt:
        return None
    m = re.search(r"[-+]?\d+(?:[.,]\d+)?", txt)
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", "."))
    except Exception:
        return None

class RotorController:
    """Fachlogik: übersetzt SPID-Kommandos in RS485-Befehle + Polling + Status.

    Fixes:
    - optionales Deaktivieren der zweiten Achse (z.B. nur Slave 20 vorhanden)
    - UI-Kommandos bekommen hohe Priorität, damit Buttons sofort senden
    """

    def __init__(self, hw:HardwareClient, master_id:int, slave_az:int, slave_el:int, log,
                 enable_az:bool=True, enable_el:bool=True):
        self.hw = hw
        self.log = log
        self.master_id = master_id
        self.slave_az = slave_az
        self.slave_el = slave_el
        self.enable_az = bool(enable_az)
        self.enable_el = bool(enable_el)

        self.az = AxisState()
        self.el = AxisState()

        self._last_poll = 0.0
        self._last_warn = 0.0
        self._last_err = 0.0
        self._last_tel = 0.0
        self._last_wind = 0.0
        self._last_pwm = 0.0
        self._last_minpwm = 0.0
        self._last_ref_idle = 0.0
        self._last_ref_active_az = 0.0
        self._last_ref_active_el = 0.0
        self._last_wind_dir = 0.0
        self._last_wind_beaufort = 0.0
        self._wind_beaufort_due_ts = 0.0
        self._wind_speed_inflight = False
        self._wind_speed_sent_ts = 0.0
        self._wind_dir_inflight = False
        self._wind_dir_sent_ts = 0.0
        self._wind_beaufort_inflight = False
        self._wind_beaufort_sent_ts = 0.0
        # Kalibrier-Bins: GETCALSTATE -> wenn DONE, GETCALBINS fetchen
        self._last_cal_state_az: float = 0.0
        self._cal_bins_inflight_az: bool = False
        self._cal_bins_fetched_az: bool = False
        self._cal_bins_received_az: int = 0  # Zähler für 12 Blöcke
        # Live-Bins: GETLIVEBINS alle 30s im Idle (seltener = stabilere Anzeige)
        self._last_live_bins_az: float = 0.0
        self._live_bins_inflight_az: bool = False
        self._live_bins_received_az: int = 0
        # Temp-Buffer während Fetch (nicht in axis_state schreiben bis vollständig)
        self._cal_bins_temp_cw: Optional[list] = None
        self._cal_bins_temp_ccw: Optional[list] = None
        self._live_bins_temp_cw: Optional[list] = None
        self._live_bins_temp_ccw: Optional[list] = None
        # ACC-Bins (GETACCBINS): schnelle aktuelle Last, alle 10s
        self._last_acc_bins_az: float = 0.0
        self._acc_bins_inflight_az: bool = False
        self._acc_bins_temp_cw: Optional[list] = None
        self._acc_bins_temp_ccw: Optional[list] = None
        # EL: gleiche Statistik wie AZ
        self._last_cal_state_el: float = 0.0
        self._cal_bins_inflight_el: bool = False
        self._cal_bins_fetched_el: bool = False
        self._cal_bins_temp_cw_el: Optional[list] = None
        self._cal_bins_temp_ccw_el: Optional[list] = None
        self._last_live_bins_el: float = 0.0
        self._live_bins_inflight_el: bool = False
        self._live_bins_temp_cw_el: Optional[list] = None
        self._live_bins_temp_ccw_el: Optional[list] = None
        self._last_acc_bins_el: float = 0.0
        self._acc_bins_inflight_el: bool = False
        self._acc_bins_temp_cw_el: Optional[list] = None
        self._acc_bins_temp_ccw_el: Optional[list] = None
        # Statistik-Fenster offen: nur dann CAL/LIVE/ACC pollen (Bus entlasten)
        self._statistics_window_open: bool = False
        # Kompass-Fenster offen: ACCBINS pollen für Strom-Heatmap
        self._compass_window_open: bool = False
        # Nach Bewegung: ACCBINS sofort abbrechen, erst nach 10s Idle wieder starten (Dead-Man-Vermeidung)
        self._stats_cooldown_until: float = 0.0
        # Kompass-Manual-Eingabe: PST-SET für 10s ignorieren, damit nicht überschrieben wird
        self._compass_manual_az_ts: float = 0.0
        self._compass_manual_el_ts: float = 0.0
        # Windsensor-Feature-Flag aus GETWINDENABLE (0/1). Off bis Antwort vorliegt.
        self.wind_enabled: bool = False
        self.wind_enabled_known: bool = False
        self._wind_enable_inflight: bool = False
        self._wind_enable_sent_ts: float = 0.0
        self._hw_prev_connected: bool = False
        self._startup_burst_until: float = 0.0
        self._last_wind_enable_poll: float = 0.0
        # Polling-Intervalle (ms):
        # Fahrt  : nur GETPOSDG + GETERR
        # Idle   : alle weiteren Abfragen mit unterschiedlichem Takt
        self._cfg_poll = {
            "pos_fast": 100,    # 10 Hz  (Fahrt)
            "pos_slow": 10000,  # 10 s   (Idle)
            "err_moving": 5000,  # 5 s    (während Fahrt)
            "err_idle":  10000,  # 10 s   (Idle)
            "warn":     10000,  # 10 s   (nur Idle)
            "pwm":      10000,  # 10 s   (nur Idle)
            "minpwm":   10000,  # 10 s   (nur Idle, ändert sich kaum)
            "telemetry": 10000, # 10 s   (nur Idle: GETTEMPA/GETTEMPM)
            "ref":       300,   # 300 ms (Referenzfahrt: schnell)
            "ref_idle":  5000,  # 5 s    (Idle: Referenzstatus)
            "windenable": 10000,# 10 s   (Idle: Sensor angesteckt/abgesteckt)
            "offline_timeout": 2000,
        }

        # Async telegram handler
        self.hw.on_async_telegram = self._on_async_tel

    def update_ids(self, master_id:int, slave_az:int, slave_el:int, enable_az:bool=True, enable_el:bool=True):
        self.master_id = master_id
        self.slave_az = slave_az
        self.slave_el = slave_el
        self.enable_az = bool(enable_az)
        self.enable_el = bool(enable_el)

    def set_statistics_window_open(self, open: bool) -> None:
        """Statistik-Fenster offen/geschlossen. Nur wenn offen: CAL/LIVE/ACC pollen."""
        self._statistics_window_open = bool(open)

    def set_compass_window_open(self, open: bool) -> None:
        """Kompass-Fenster offen/geschlossen. Wenn offen: ACCBINS pollen für Strom-Heatmap."""
        self._compass_window_open = bool(open)

    def set_wind_enabled_from_value(self, value: int | str) -> None:
        """Windmesser-Status setzen (z.B. nach SETWINDENABLE im Befehlsfenster)."""
        try:
            v = int(value) if value is not None else 0
        except (TypeError, ValueError):
            v = 0
        self.wind_enabled = bool(v != 0)
        self.wind_enabled_known = True
        if not self.wind_enabled:
            self._wind_speed_inflight = False
            self._wind_dir_inflight = False
            self._wind_beaufort_inflight = False
            try:
                self.az.telemetry.wind_kmh = None
                self.az.telemetry.wind_dir_deg = None
                self.az.telemetry.wind_beaufort = None
                self.el.telemetry.wind_kmh = None
                self.el.telemetry.wind_dir_deg = None
                self.el.telemetry.wind_beaufort = None
            except Exception:
                pass

    def request_immediate_pos(self) -> None:
        """Positionsabfrage sofort mit höchster Priorität (beim Öffnen des Kompassfensters)."""
        if self.hw.is_connected():
            try:
                prio = 0
                if self.enable_az:
                    line = build(self.master_id, self.slave_az, "GETPOSDG", "0")
                    self.hw.send_request(HwRequest(line=line, expect_prefix=None, timeout_s=0.8, on_done=None, priority=prio))
                if self.enable_el:
                    line = build(self.master_id, self.slave_el, "GETPOSDG", "0")
                    self.hw.send_request(HwRequest(line=line, expect_prefix=None, timeout_s=0.8, on_done=None, priority=prio))
            except Exception:
                pass

    def request_antenna_offsets(self) -> None:
        """AZ-Antennenversätze vom Rotor lesen (GETANTOFF1–3). EL-Versatz entfällt."""
        if self.enable_az:
            for cmd in ("GETANTOFF1", "GETANTOFF2", "GETANTOFF3"):
                self.hw.send_request(HwRequest(
                    line=build(self.master_id, self.slave_az, cmd, "0"),
                    expect_prefix=None,
                    timeout_s=0.5,
                    on_done=None,
                    priority=4,
                ))

    def set_antenna_offset(
        self,
        axis: str,
        slot: int,
        value_deg: float,
        on_done: Optional[Callable[[bool], None]] = None,
    ) -> None:
        """Antennen-Versatz schreiben (SETANTOFF1–3). Ruft on_done(success) nach ACK/NAK/Timeout.
        success=True nur bei ACK_SETANTOFFx."""
        if slot not in (1, 2, 3):
            if on_done:
                on_done(False)
            return
        cmd = f"SETANTOFF{slot}"
        v = str(round(max(0.0, min(360.0, value_deg)), 1))
        expect = f"ACK_SETANTOFF{slot}"
        dst = None
        axis_state = None
        if axis.lower() == "az" and self.enable_az:
            dst = self.slave_az
            axis_state = self.az
        elif axis.lower() == "el" and self.enable_el:
            dst = self.slave_el
            axis_state = self.el
        if dst is None or axis_state is None:
            if on_done:
                on_done(False)
            return

        line = build(self.master_id, dst, cmd, v)

        def done(tel: Optional[Telegram], err: Optional[str]):
            ok = False
            if err:
                self.log.write("WARN", f"{cmd} -> keine Antwort ({err})")
            elif tel and tel.cmd.startswith("ACK_SETANTOFF"):
                ok = True
                try:
                    setattr(axis_state, f"antoff{slot}", float(v.replace(",", ".")))
                except Exception:
                    pass
            else:
                if tel:
                    self.log.write("WARN", f"{cmd} -> NAK oder ungültige Antwort: {tel.cmd}")
                else:
                    self.log.write("WARN", f"{cmd} -> keine gültige ACK-Antwort")
            if on_done:
                on_done(ok)

        self.hw.send_request(HwRequest(
            line=line,
            expect_prefix=expect,
            timeout_s=1.2,
            on_done=done,
            priority=2,
        ))

    def request_immediate_stats(self) -> None:
        """Statistik-Abfrage sofort auslösen (beim Öffnen des Statistik-Fensters). Priorität 0 = vor allem anderen.
        Wird während Cooldown (10s nach Bewegung) übersprungen."""
        if time.time() < self._stats_cooldown_until:
            return
        self._last_cal_state_az = 0.0
        self._last_cal_state_el = 0.0
        self._last_live_bins_az = 0.0
        self._last_live_bins_el = 0.0
        self._last_acc_bins_az = 0.0
        self._last_acc_bins_el = 0.0
        if self.hw.is_connected():
            try:
                prio = 0  # Höchste Priorität, vor GETPOSDG
                if self.enable_az and not self._cal_bins_inflight_az:
                    self._poll_cal_state(self.slave_az, self.az, priority=prio)
                if self.enable_el and not self._cal_bins_inflight_el:
                    self._poll_cal_state(self.slave_el, self.el, priority=prio)
                if self.enable_az and not self._live_bins_inflight_az:
                    self._fetch_live_bins(self.slave_az, self.az, "AZ", priority=prio)
                if self.enable_el and not self._live_bins_inflight_el:
                    self._fetch_live_bins_el(self.slave_el, self.el, "EL", priority=prio)
                if self.enable_az and not self._acc_bins_inflight_az:
                    self._fetch_acc_bins(self.slave_az, self.az, "AZ", priority=prio)
                if self.enable_el and not self._acc_bins_inflight_el:
                    self._fetch_acc_bins_el(self.slave_el, self.el, "EL", priority=prio)
            except Exception:
                pass

    def update_polling(self, polling_ms:dict):
        self._cfg_poll.update(polling_ms or {})
        # Positionsabfrage: mindestens 10x/s (User-Wunsch). Größerer Wert = langsamer.
        try:
            self._cfg_poll["pos_fast"] = int(min(int(self._cfg_poll.get("pos_fast", 100)), 100))
        except Exception:
            self._cfg_poll["pos_fast"] = 100
        # Keine harte Obergrenze mehr erzwingen: je nach Setup sollen Warn/Err/Telemetrie
        # bewusst nur alle ~2s abgefragt werden (und während Fahrt teils gar nicht).
        for k in ("warn", "err", "telemetry", "pwm", "ref_idle", "offline_timeout"):
            try:
                self._cfg_poll[k] = int(self._cfg_poll.get(k, 2000))
            except Exception:
                pass

    # -------------------- UI-Helfer (Direktkommandos) --------------------
    def build_line(self, dst:int, cmd:str, params:str) -> str:
        """RS485-Telegramm mit aktueller Master-ID erzeugen."""
        return build(int(self.master_id), int(dst), str(cmd).strip(), str(params))

    def send_ui_command(self, dst:int, cmd:str, params:str, expect_prefix:Optional[str]=None,
                        timeout_s:float=0.8, priority:int=0, on_done=None) -> None:
        """Beliebiges RS485-Kommando mit hoher Priorität senden.

        Wird von UI-Fenstern (Kompass/Befehle) genutzt.

        Hinweis:
        - HardwareClient kann immer nur EIN "pending" Request gleichzeitig haben.
          Deshalb ist "expect_prefix" standardmäßig None.
        """

        # ------------------------------------------------------------
        # WICHTIG (GUI-Feedback "Fährt")
        # ------------------------------------------------------------
        # Die "Fährt"-Anzeige im Hauptfenster basiert auf axis_state.moving.
        # Wenn die Bewegung per API/PST ausgelöst wird, setzt die Bridge den
        # Status bereits korrekt (set_az_from_spid()/set_el_from_spid()).
        #
        # Bei UI-Direktkommandos (Kompass / Befehlsfenster) wird jedoch
        # send_ui_command() genutzt. Ohne lokales Update würde "Fährt" erst
        # nach einem Status-/Positions-Polling umspringen oder gar nicht,
        # wenn der Befehl außerhalb der "spid"-Wege gesendet wird.
        #
        # Daher: Für relevante Kommandos (SETPOSDG/STOP/SETREF) aktualisieren
        # wir den lokalen State SOFORT.
        self._apply_local_state_for_ui_command(int(dst), str(cmd).strip().upper(), str(params))

        line = self.build_line(dst, cmd, params)
        self.hw.send_request(HwRequest(
            line=line,
            expect_prefix=expect_prefix,
            timeout_s=float(timeout_s),
            on_done=on_done,
            priority=int(priority),
        ))

    def _apply_local_state_for_ui_command(self, dst:int, cmd:str, params:str) -> None:
        """Setzt lokale Statusfelder für UI-Direktkommandos.

        Ziel:
        - "Fährt" im Hauptfenster soll sofort reagieren, wenn über die GUI
          ein Fahrbefehl abgesetzt wird (Kompass/Befehlsfenster).
        - Wir versuchen konservativ nur offensichtliche Fahr-Kommandos zu
          erkennen und den Zustand zu spiegeln.

        Hinweis:
        - Das ersetzt kein echtes Bewegungs-Feedback. Die Polling-Logik
          (GETPOSDG) setzt moving wieder auf False, sobald das Ziel erreicht ist.
        """
        try:
            # Achse anhand der konfigurierten IDs bestimmen
            axis = None
            if dst == int(self.slave_az):
                axis = self.az
            elif dst == int(self.slave_el):
                axis = self.el
            else:
                return

            # -------------------- STOP --------------------
            if cmd == "STOP":
                self._abort_stats_fetch_and_cooldown()
                axis.moving = False
                return

            # -------------------- Referenzfahrt --------------------
            # SETREF (mit start_homing=1) löst typischerweise eine Fahrt aus.
            # Wir markieren "moving" vorsichtig als True, wenn params != "0".
            if cmd == "SETREF":
                try:
                    v = str(params).strip()
                    if v and v != "0":
                        axis.moving = True
                except Exception:
                    pass
                return

            # -------------------- Fahrziel setzen --------------------
            if cmd == "SETPOSDG":
                self._abort_stats_fetch_and_cooldown()
                # params ist i.d.R. Grad als Float mit "," oder ".".
                try:
                    p = str(params).strip()
                    # Falls mehrere Teile (z.B. "X;Y"), nehmen wir den letzten Wert
                    if ";" in p:
                        p = p.split(";")[-1]
                    p = p.replace(" ", "")
                    v = float(p.replace(",", "."))
                    d10 = int(round(v * 10.0))
                except Exception:
                    d10 = None

                if d10 is not None:
                    axis.target_d10 = d10
                    axis.last_set_sent_target_d10 = d10
                    axis.last_set_sent_ts = time.time()
                axis.moving = True
                return

        except Exception:
            # Keine harte Fehlerbehandlung: UI darf nicht wegen Status-Update abbrechen.
            return

    def set_az_deg(self, deg:float, force:bool=True) -> None:
        """AZ-Zielwinkel in Grad setzen.

        - Wenn force=True, wird der Entprell-Mechanismus ("gleiches Ziel") umgangen.
        - Referenz-Check wird beibehalten (wie beim restlichen Bridge-Verhalten).
        """
        d10 = int(round(float(deg) * 10.0))
        # Manuelle Kompass-Steuerung (force=True) soll immer senden, auch wenn
        # der Referenzstatus nach Reconnect kurzzeitig noch unbekannt ist.
        if force:
            if not self.enable_az:
                return
            self.az.target_d10 = d10
            self._compass_manual_az_ts = time.time()
            self._send_setpos(self.slave_az, d10, axis="AZ")
            self.az.last_set_sent_target_d10 = d10
            self.az.last_set_sent_ts = time.time()
            self.az.moving = True
            return
        self.set_az_from_spid(d10)

    def set_el_deg(self, deg:float, force:bool=True) -> None:
        """EL-Zielwinkel in Grad setzen.

        Analog zu :meth:`set_az_deg`.

        - Wenn force=True, wird der Entprell-Mechanismus ("gleiches Ziel") umgangen.
        - Referenz-Check wird beibehalten.
        """
        d10 = int(round(float(deg) * 10.0))
        if force:
            if not self.enable_el:
                return
            self.el.target_d10 = d10
            self._compass_manual_el_ts = time.time()
            self._send_setpos(self.slave_el, d10, axis="EL")
            self.el.last_set_sent_target_d10 = d10
            self.el.last_set_sent_ts = time.time()
            self.el.moving = True
            return
        self.set_el_from_spid(d10)

    def check_ref_once(self):
        """Einmalig GETREF absetzen, um festzustellen, ob der Rotor bereits referenziert ist.

        Ziel:
        - Beim Programmstart (oder nach Verbinden) einmal prüfen.
        - Wenn ACK_GETREF:1 -> referenced=True
        - Wenn ACK_GETREF:0 -> referenced=False (User muss ggf. SETREF drücken)
        - Keine Dauerschleife (kein ref_poll_active), nur ein einmaliger Check.
        """

        if self.enable_az:
            # Ohne pending senden (darf Startup nicht blockieren)
            self._poll_ref(self.slave_az, self.az, "AZ")

        if self.enable_el:
            self._poll_ref(self.slave_el, self.el, "EL")



    # -------------------- Kommandos von PstRotator (SPID) --------------------
    def set_pos_from_spid(self, az_d10:int, el_d10:int):
        self.set_az_from_spid(az_d10)
        self.set_el_from_spid(el_d10)

    def set_az_from_spid(self, az_d10:int):
        """Zielposition von PstRotator (0,1°) für AZ.

        PstRotator sendet bei manchen Einstellungen SET ständig erneut.
        Wir senden SETPOSDG nur dann, wenn sich das Ziel wirklich geändert hat
        (und wenn wir nicht bereits am Ziel sind).
        Kompass-Manual-Eingabe hat 10s Vorrang (PST-SET wird ignoriert).
        """
        if (time.time() - self._compass_manual_az_ts) < 10.0:
            return
        self.az.target_d10 = az_d10

        if not self.enable_az:
            return

        # Wenn wir bereits am Ziel sind (Toleranz 0,1°) und nicht fahren -> nichts senden
        if (not self.az.moving) and (abs(self.az.pos_d10 - az_d10) <= 1):
            return

        # Gleiches Ziel wie zuletzt gesendet? -> nicht erneut senden
        if self.az.last_set_sent_target_d10 is not None and az_d10 == self.az.last_set_sent_target_d10:
            return

        if not self.az.referenced:
            self.log.write("WARN", "AZ SETPOSDG ignoriert: AZ nicht referenziert (Ziel gemerkt)")
            return

        self._send_setpos(self.slave_az, az_d10, axis="AZ")
        self.az.last_set_sent_target_d10 = az_d10
        self.az.last_set_sent_ts = time.time()
        self.az.moving = True



    def set_el_from_spid(self, el_d10:int):
        """Zielposition von PstRotator (0,1°) für EL.

        Siehe set_az_from_spid(): wir entprellen doppelte SET-Kommandos.
        Kompass-Manual-Eingabe hat 10s Vorrang.
        """
        if (time.time() - self._compass_manual_el_ts) < 10.0:
            return
        self.el.target_d10 = el_d10

        if not self.enable_el:
            return

        if (not self.el.moving) and (abs(self.el.pos_d10 - el_d10) <= 1):
            return

        if self.el.last_set_sent_target_d10 is not None and el_d10 == self.el.last_set_sent_target_d10:
            return

        if not self.el.referenced:
            self.log.write("WARN", "EL SETPOSDG ignoriert: EL nicht referenziert (Ziel gemerkt)")
            return

        self._send_setpos(self.slave_el, el_d10, axis="EL")
        self.el.last_set_sent_target_d10 = el_d10
        self.el.last_set_sent_ts = time.time()
        self.el.moving = True



    def stop_all(self):
        self.stop_az()
        self.stop_el()

    def stop_az(self):
        if not self.enable_az:
            return
        self._abort_stats_fetch_and_cooldown()
        self._send_simple(self.slave_az, "STOP", "0", expect="ACK_STOP", prio=0)
        self.az.moving = False

    def stop_el(self):
        if not self.enable_el:
            return
        self._abort_stats_fetch_and_cooldown()
        self._send_simple(self.slave_el, "STOP", "0", expect="ACK_STOP", prio=0)
        self.el.moving = False

    def reference_all(self, start_homing: bool = True) -> None:
        """Referenziert alle aktiven Rotoren (AZ und/oder EL laut Config)."""
        if self.enable_az:
            self.reference_az(start_homing)
        if self.enable_el:
            self.reference_el(start_homing)

    def reference_az(self, start_homing: bool = True) -> None:
        """Referenziert nur AZ."""
        if not self.enable_az:
            return
        v = "1" if start_homing else "0"
        try:
            self.az.target_d10 = 0
            self.az.last_set_sent_target_d10 = None
            self.az.last_set_sent_ts = 0.0
        except Exception:
            pass
        self._send_simple(self.slave_az, "SETREF", v, expect="ACK_SETREF", prio=0)
        self.az.ref_poll_active = True
        self.az.referenced = False
        if start_homing:
            self.az.moving = True

    def reference_el(self, start_homing: bool = True) -> None:
        """Referenziert nur EL."""
        if not self.enable_el:
            return
        v = "1" if start_homing else "0"
        try:
            self.el.target_d10 = 0
            self.el.last_set_sent_target_d10 = None
            self.el.last_set_sent_ts = 0.0
        except Exception:
            pass
        self._send_simple(self.slave_el, "SETREF", v, expect="ACK_SETREF", prio=0)
        self.el.ref_poll_active = True
        self.el.referenced = False
        if start_homing:
            self.el.moving = True

    def clear_warnings_all(self):
        if self.enable_az:
            self._send_simple(self.slave_az, "DELWARN", "1", expect="ACK_DELWARN", prio=0)
        if self.enable_el:
            self._send_simple(self.slave_el, "DELWARN", "1", expect="ACK_DELWARN", prio=0)

    def set_pwm_all(self, pwm_pct:float):
        self.set_pwm_az(pwm_pct)
        self.set_pwm_el(pwm_pct)

    def _set_pwm(self, dst:int, axis_state:AxisState, pwm_pct:float, axis_label:str):
        pwm_pct = max(0.0, min(100.0, float(pwm_pct)))
        v = f"{pwm_pct:.1f}".replace(".", ",")
        self._send_simple(dst, "SETPWM", v, expect="ACK_SETPWM", prio=0)
        # Optimistisches UI-Update: GETPWM kommt nur alle ~2s.
        try:
            axis_state.telemetry.pwm_max_pct = float(pwm_pct)
        except Exception:
            pass

    def set_pwm_az(self, pwm_pct:float):
        if not self.enable_az:
            return
        self._set_pwm(self.slave_az, self.az, pwm_pct, "AZ")

    def set_pwm_el(self, pwm_pct:float):
        if not self.enable_el:
            return
        self._set_pwm(self.slave_el, self.el, pwm_pct, "EL")

    # -------------------- Polling --------------------
    def tick_polling(self):
        now = time.time()
        hw_on = False
        try:
            hw_on = bool(self.hw.is_connected())
        except Exception:
            hw_on = False

        # Wenn die Hardware-Verbindung gerade (wieder) da ist: Timer zurücksetzen,
        # damit beim ersten Mal alle Werte sofort abgefragt werden (Startup/Recover).
        if hw_on and (not self._hw_prev_connected):
            self._startup_burst_until = float(now) + 3.0
            self._last_poll = 0.0
            self._last_warn = 0.0
            self._last_err = 0.0
            self._last_tel = 0.0
            self._last_wind = 0.0
            self._last_wind_dir = 0.0
            self._last_wind_beaufort = 0.0
            self._wind_dir_due_ts = 0.0
            self._wind_beaufort_due_ts = 0.0
            self._wind_speed_inflight = False
            self._wind_speed_sent_ts = 0.0
            self._wind_dir_inflight = False
            self._wind_dir_sent_ts = 0.0
            self._wind_beaufort_inflight = False
            self._wind_beaufort_sent_ts = 0.0
            self._last_pwm = 0.0
            self._last_minpwm = 0.0
            self._last_ref_idle = 0.0
            self._last_cal_state_az = 0.0
            self._last_live_bins_az = 0.0
            self._cal_bins_inflight_az = False
            self._live_bins_inflight_az = False
            self._last_wind_enable_poll = 0.0
            self.wind_enabled_known = False
            self._wind_enable_inflight = False
            self._wind_enable_sent_ts = 0.0

            # Sofortige Erstabfrage (damit UI direkt gefüllt wird):
            # Position + ERR + PWM + MINPWM + REF + Warn + Temp
            # GETWINDENABLE wird vom Idle-Polling-Block übernommen (Inflight-Guard verhindert Doppelabfrage)
            try:
                if self.enable_az:
                    self._poll_pos(self.slave_az, self.az, "AZ", now, expected_period_s=0.2)
                    self._poll_err(self.slave_az, self.az, "AZ")
                    self._poll_pwm(self.slave_az, self.az, "AZ")
                    self._poll_minpwm(self.slave_az, self.az, "AZ")
                    self._poll_ref(self.slave_az, self.az, "AZ")
                    self._poll_warn(self.slave_az, self.az, "AZ")
                    self._poll_idle_telemetry(self.slave_az, self.az, "AZ")
                    self._poll_idle_wind(self.slave_az, self.az, "AZ")
                if self.enable_el:
                    self._poll_pos(self.slave_el, self.el, "EL", now, expected_period_s=0.2)
                    self._poll_err(self.slave_el, self.el, "EL")
                    self._poll_pwm(self.slave_el, self.el, "EL")
                    self._poll_minpwm(self.slave_el, self.el, "EL")
                    self._poll_ref(self.slave_el, self.el, "EL")
                    self._poll_warn(self.slave_el, self.el, "EL")
                    self._poll_idle_telemetry(self.slave_el, self.el, "EL")
                self.request_antenna_offsets()
            except Exception:
                pass
        self._hw_prev_connected = hw_on

        pos_fast_s      = self._cfg_poll["pos_fast"]    / 1000.0
        pos_slow_s      = self._cfg_poll["pos_slow"]    / 1000.0
        err_moving_s    = self._cfg_poll["err_moving"]   / 1000.0
        err_idle_s      = self._cfg_poll["err_idle"]    / 1000.0
        warn_s          = self._cfg_poll["warn"]        / 1000.0
        pwm_s           = self._cfg_poll["pwm"]         / 1000.0
        minpwm_s        = self._cfg_poll["minpwm"]      / 1000.0
        tel_s           = self._cfg_poll["telemetry"]   / 1000.0
        ref_s           = self._cfg_poll["ref"]         / 1000.0
        ref_idle_s      = self._cfg_poll["ref_idle"]    / 1000.0
        windenable_s    = self._cfg_poll["windenable"]  / 1000.0
        offline_timeout_s = self._cfg_poll["offline_timeout"] / 1000.0

        # Dynamisches Polling:
        # - Fahrt  : nur GETPOSDG (10 Hz) + GETERR (5 s) → Bus frei für Position
        # - Idle   : GETPOSDG (10 s) + ERR/WARN/PWM/TEMP/MINPWM (10 s)
        #            + GETREF/GETWINDENABLE (5–10 s) + Wind (2 s)
        moving = bool(self.az.moving or self.el.moving or self.az.ref_poll_active or self.el.ref_poll_active)

        if hw_on:
            # Inflight-Sperren nach Request-Timeout freigeben (verhindert dauerhaftes Blockieren).
            if self._wind_enable_inflight and ((now - self._wind_enable_sent_ts) > 1.5):  # now = time.time()
                self._wind_enable_inflight = False
            if self._wind_speed_inflight and ((now - float(self._wind_speed_sent_ts or 0.0)) > 0.9):
                self._wind_speed_inflight = False
            if self._wind_dir_inflight and ((now - float(self._wind_dir_sent_ts or 0.0)) > 0.9):
                self._wind_dir_inflight = False
            if self._wind_beaufort_inflight and ((now - float(self._wind_beaufort_sent_ts or 0.0)) > 0.9):
                self._wind_beaufort_inflight = False

            pos_period = pos_fast_s if moving else pos_slow_s
            # In den ersten Sekunden nach Connect einmal schneller pollen, damit Werte "schnappen"
            if now < float(self._startup_burst_until or 0.0):
                pos_period = min(pos_period, pos_fast_s)
            if now - self._last_poll >= pos_period:
                sent_any = False
                if self.enable_az:
                    sent_any = self._poll_pos(self.slave_az, self.az, "AZ", now, expected_period_s=pos_period) or sent_any
                if self.enable_el:
                    sent_any = self._poll_pos(self.slave_el, self.el, "EL", now, expected_period_s=pos_period) or sent_any
                # Nur wenn wirklich gesendet wurde, Zeitstempel fortschreiben.
                # Sonst (inflight) würden wir unnötig lange warten, bis wir direkt nach dem ACK wieder senden.
                if sent_any:
                    self._last_poll = now

            # Während Bewegung: NUR Position (schnell) + ERR alle 5 s.
            # Kein WARN, keine Telemetrie, kein Wind, kein PWM – Bus-Priorität für Position.
            if moving:
                if now - self._last_err >= err_moving_s:
                    self._last_err = now
                    if self.enable_az:
                        self._poll_err(self.slave_az, self.az, "AZ")
                    if self.enable_el:
                        self._poll_err(self.slave_el, self.el, "EL")

            if not moving:
                # Idle: alle Zusatzabfragen – damit während der Fahrt GETPOSDG maximal priorisiert bleibt.

                # GETERR: 10 s im Idle
                if now - self._last_err >= err_idle_s:
                    self._last_err = now
                    if self.enable_az:
                        self._poll_err(self.slave_az, self.az, "AZ")
                    if self.enable_el:
                        self._poll_err(self.slave_el, self.el, "EL")

                # GETPWM: 2 s
                if now - self._last_pwm >= pwm_s:
                    self._last_pwm = now
                    if self.enable_az:
                        self._poll_pwm(self.slave_az, self.az, "AZ")
                    if self.enable_el:
                        self._poll_pwm(self.slave_el, self.el, "EL")

                # GETMINPWM: 10 s (ändert sich kaum)
                if now - self._last_minpwm >= minpwm_s:
                    self._last_minpwm = now
                    if self.enable_az:
                        self._poll_minpwm(self.slave_az, self.az, "AZ")
                    if self.enable_el:
                        self._poll_minpwm(self.slave_el, self.el, "EL")

                # GETWINDENABLE: Startphase alle 3 s, danach alle 10 s (Sensor an/ab)
                if self.enable_az:
                    wind_unknown_retry = (not self.wind_enabled_known
                                         and (now - self._last_wind_enable_poll >= 3.0))
                    wind_known_repoll  = (self.wind_enabled_known
                                         and (now - self._last_wind_enable_poll >= windenable_s))
                    if wind_unknown_retry or wind_known_repoll:
                        self._poll_wind_enable(self.slave_az, self.az, "AZ")

                # GETCALSTATE/LIVE nur wenn Statistik-Fenster offen
                if self._statistics_window_open:
                    if self.enable_az and (now - self._last_cal_state_az >= 10.0):
                        self._last_cal_state_az = now
                        self._poll_cal_state(self.slave_az, self.az)
                    if self.enable_el and (now - self._last_cal_state_el >= 10.0):
                        self._last_cal_state_el = now
                        self._poll_cal_state(self.slave_el, self.el)

                    live_interval = 2.0 if (self.az.live_bins_cw is None) else 30.0
                    if self.enable_az and (now - self._last_live_bins_az >= live_interval):
                        self._last_live_bins_az = now
                        self._fetch_live_bins(self.slave_az, self.az, "AZ")
                    live_interval_el = 2.0 if (self.el.live_bins_cw is None) else 30.0
                    if self.enable_el and (now - self._last_live_bins_el >= live_interval_el):
                        self._last_live_bins_el = now
                        self._fetch_live_bins_el(self.slave_el, self.el, "EL")

                # ACCBINS wenn Statistik- oder Kompass-Fenster offen (Strom-Heatmap)
                # Nach Bewegung 10s Cooldown (Dead-Man-Vermeidung)
                if (self._statistics_window_open or self._compass_window_open) and now >= self._stats_cooldown_until:
                    acc_interval = 2.0 if (self.az.acc_bins_cw is None) else 10.0
                    if self.enable_az and (now - self._last_acc_bins_az >= acc_interval):
                        self._last_acc_bins_az = now
                        self._fetch_acc_bins(self.slave_az, self.az, "AZ")
                    acc_interval_el = 2.0 if (self.el.acc_bins_cw is None) else 10.0
                    if self.enable_el and (now - self._last_acc_bins_el >= acc_interval_el):
                        self._last_acc_bins_el = now
                        self._fetch_acc_bins_el(self.slave_el, self.el, "EL")

                # Wind nur im Stand pollen (AZ-only), ebenfalls entkoppelt.
                if self.wind_enabled and (now - self._last_wind >= 2.0):
                    if self.enable_az and (not self._wind_speed_inflight):
                        self._last_wind = now
                        self._poll_idle_wind(self.slave_az, self.az, "AZ")
                        self._wind_dir_due_ts = float(now) + 0.25
                        self._wind_beaufort_due_ts = float(now) + 0.5
                if (
                    self.wind_enabled
                    and self.enable_az
                    and (not self._wind_dir_inflight)
                    and (now >= float(self._wind_dir_due_ts or 0.0))
                    and (now - self._last_wind_dir >= 2.0)
                ):
                    self._last_wind_dir = now
                    self._wind_dir_due_ts = 0.0
                    self._poll_idle_wind_dir(self.slave_az, self.az, "AZ")

                if (
                    self.wind_enabled
                    and self.enable_az
                    and (not self._wind_beaufort_inflight)
                    and (now >= float(self._wind_beaufort_due_ts or 0.0))
                    and (now - self._last_wind_beaufort >= 2.0)
                ):
                    self._last_wind_beaufort = now
                    self._wind_beaufort_due_ts = 0.0
                    self._poll_idle_wind_beaufort(self.slave_az, self.az, "AZ")

                # GETWARN: 2 s
                if now - self._last_warn >= warn_s:
                    self._last_warn = now
                    if self.enable_az:
                        self._poll_warn(self.slave_az, self.az, "AZ")
                    if self.enable_el:
                        self._poll_warn(self.slave_el, self.el, "EL")

                # GETTEMPA/GETTEMPM: 10 s
                if now - self._last_tel >= tel_s:
                    self._last_tel = now
                    if self.enable_az:
                        self._poll_idle_telemetry(self.slave_az, self.az, "AZ")
                    if self.enable_el:
                        self._poll_idle_telemetry(self.slave_el, self.el, "EL")

                # GETREF: 5 s (Rotor könnte zwischendurch neu gestartet worden sein)
                if now - self._last_ref_idle >= ref_idle_s:
                    self._last_ref_idle = now
                    if self.enable_az:
                        self._poll_ref(self.slave_az, self.az, "AZ")
                    if self.enable_el:
                        self._poll_ref(self.slave_el, self.el, "EL")
            # Referenzfahrt: GETREF unabhängig von Positions-ACKs pollen
            if self.enable_az and self.az.ref_poll_active and (now - self._last_ref_active_az) >= ref_s:
                self._last_ref_active_az = now
                self._poll_ref(self.slave_az, self.az, "AZ")
            if self.enable_el and self.el.ref_poll_active and (now - self._last_ref_active_el) >= ref_s:
                self._last_ref_active_el = now
                self._poll_ref(self.slave_el, self.el, "EL")

        # Online/Offline Bewertung: wenn länger als 2s kein ACK/NAK reinkommt -> offline
        def _update_online(axis_state):
            try:
                prev_online = bool(getattr(axis_state, "online", False))
            except Exception:
                prev_online = False
            try:
                last = float(getattr(axis_state, "last_rx_ts", 0.0) or 0.0)
            except Exception:
                last = 0.0
            if last <= 0.0:
                axis_state.online = False
                # User-Wunsch: Beim Übergang nach offline Ziel zurücksetzen
                if prev_online:
                    try:
                        axis_state.target_d10 = 0
                        axis_state.last_set_sent_target_d10 = None
                        axis_state.last_set_sent_ts = 0.0
                        axis_state.referenced = False
                        axis_state.moving = False
                        axis_state.error_code = 0
                        try:
                            axis_state.warnings.clear()
                        except Exception:
                            axis_state.warnings = set()
                        try:
                            axis_state.telemetry.temp_ambient_c = None
                            axis_state.telemetry.temp_motor_c = None
                            axis_state.telemetry.wind_kmh = None
                            axis_state.telemetry.wind_dir_deg = None
                            axis_state.telemetry.wind_beaufort = None
                            axis_state.telemetry.pwm_max_pct = None
                            axis_state.telemetry.pwm_min_pct = None
                        except Exception:
                            pass
                    except Exception:
                        pass
                return
            new_online = (now - last) <= float(max(2.0, offline_timeout_s))
            axis_state.online = bool(new_online)
            if prev_online and (not new_online):
                try:
                    axis_state.target_d10 = 0
                    axis_state.last_set_sent_target_d10 = None
                    axis_state.last_set_sent_ts = 0.0
                    axis_state.referenced = False
                    axis_state.moving = False
                    axis_state.error_code = 0
                    try:
                        axis_state.warnings.clear()
                    except Exception:
                        axis_state.warnings = set()
                    try:
                        axis_state.telemetry.temp_ambient_c = None
                        axis_state.telemetry.temp_motor_c = None
                        axis_state.telemetry.wind_kmh = None
                        axis_state.telemetry.wind_dir_deg = None
                        axis_state.telemetry.wind_beaufort = None
                        axis_state.telemetry.pwm_max_pct = None
                        axis_state.telemetry.pwm_min_pct = None
                    except Exception:
                        pass
                except Exception:
                    pass

        try:
            _update_online(self.az)
        except Exception:
            pass
        try:
            _update_online(self.el)
        except Exception:
            pass

    # -------------------- Send helpers --------------------
    def _send_simple(self, dst:int, cmd:str, params:str, expect:str|None, prio:int=5):
        line = build(self.master_id, dst, cmd, params)
        def done(tel:Optional[Telegram], err:Optional[str]):
            if err:
                self.log.write("WARN", f"{cmd} -> keine Antwort ({err})")
                return
            if tel and not tel.ok:
                self.log.write("WARN", f"{cmd} -> CS falsch: {tel}")
        self.hw.send_request(HwRequest(line=line, expect_prefix=expect, timeout_s=0.8, on_done=done, priority=prio))

    def _abort_stats_fetch_and_cooldown(self) -> None:
        """ACCBINS-Statistik abbrechen und 10s Cooldown setzen (Dead-Man-Vermeidung bei Bewegung)."""
        self._acc_bins_inflight_az = False
        self._acc_bins_inflight_el = False
        self._acc_bins_temp_cw = None
        self._acc_bins_temp_ccw = None
        self._acc_bins_temp_cw_el = None
        self._acc_bins_temp_ccw_el = None
        self._stats_cooldown_until = time.time() + 10.0

    def _send_setpos(self, dst:int, d10:int, axis:str, retry_count:int=0):
        """SETPOSDG senden. Bei fehlendem ACK nach ~250ms automatisch einmal erneut versuchen.

        Retry wegen möglicher RS485-Kollisionen; Verbindung bleibt bei Timeout erhalten.
        """
        self._abort_stats_fetch_and_cooldown()
        deg = (d10/10.0)
        params = f"{deg:.2f}".replace(".", ",")
        line = build(self.master_id, dst, "SETPOSDG", params)

        def done(tel:Optional[Telegram], err:Optional[str]):
            if err:
                if retry_count < 1:
                    self.log.write("INFO", f"{axis} SETPOSDG kein ACK ({err}), Retry...")
                    self._send_setpos(dst, d10, axis, retry_count=1)
                else:
                    self.log.write("WARN", f"{axis} SETPOSDG keine Antwort nach Retry ({err})")
                return
            if tel and tel.cmd.startswith("NAK_SETPOSDG"):
                self.log.write("WARN", f"{axis} SETPOSDG NAK: {tel.params}")

        self.hw.send_request(HwRequest(
            line=line,
            expect_prefix="ACK_SETPOSDG",
            timeout_s=0.25,
            on_done=done,
            priority=0,
            dont_disconnect_on_timeout=True,
        ))

    # -------------------- Poll helpers --------------------
    def _poll_pos(self, dst:int, axis_state:AxisState, axis:str, now_ts: float, expected_period_s: float) -> bool:
        """Positionsabfrage mit Flow-Control.

        Wichtig: Wir senden GETPOSDG weiterhin ohne pending (blockiert nichts),
        aber verhindern pro Achse mehrere gleichzeitige Inflight-Requests.
        """
        try:
            inflight = bool(getattr(axis_state, "pos_poll_inflight", False))
            sent_ts = float(getattr(axis_state, "pos_poll_sent_ts", 0.0) or 0.0)
        except Exception:
            inflight = False
            sent_ts = 0.0

        # Wenn schon ein Request läuft, nicht weiter aufstauen.
        # Wenn es "zu lange" dauert, lassen wir einen Retry zu.
        if inflight:
            if sent_ts > 0.0 and (now_ts - sent_ts) < max(0.6, float(expected_period_s) * 4.0):
                return False
            # Timeout/Retry: inflight freigeben
            axis_state.pos_poll_inflight = False

        axis_state.pos_poll_inflight = True
        axis_state.pos_poll_sent_ts = float(now_ts)
        axis_state.pos_poll_expected_period_s = float(max(0.05, expected_period_s))

        line = build(self.master_id, dst, "GETPOSDG", "0")
        self.hw.send_request(HwRequest(line=line, expect_prefix=None, timeout_s=0.8, on_done=None, priority=5))
        return True

    def _poll_warn(self, dst:int, axis_state:AxisState, axis:str):
        # WICHTIG: Warn-/Error-/Telemetrie-Polls dürfen die Positionsanzeige nicht ausbremsen.
        # Daher: ohne expect_prefix senden (kein "pending"), Antworten kommen asynchron rein
        # und werden in _on_async_tel verarbeitet.
        line = build(self.master_id, dst, "GETWARN", "0")
        self.hw.send_request(HwRequest(line=line, expect_prefix=None, timeout_s=0.8, on_done=None, priority=5))

    def _poll_err(self, dst:int, axis_state:AxisState, axis:str):
        line = build(self.master_id, dst, "GETERR", "0")
        self.hw.send_request(HwRequest(line=line, expect_prefix=None, timeout_s=0.8, on_done=None, priority=5))

    def _poll_ref(self, dst:int, axis_state:AxisState, axis:str):
        # WICHTIG: GETREF darf die restlichen Polls (Pos/Err/Warn/Telemetrie) nicht blockieren.
        # Daher ohne pending senden; Antwort wird in _on_async_tel verarbeitet.
        line = build(self.master_id, dst, "GETREF", "0")
        self.hw.send_request(HwRequest(line=line, expect_prefix=None, timeout_s=0.8, on_done=None, priority=2))

    def _poll_telemetry(self, dst:int, axis_state:AxisState, axis:str):
        # Telemetrie ist niedrige Priorität; außerdem ohne pending (siehe _poll_warn/_poll_err),
        # Verarbeitung erfolgt in _on_async_tel.
        self.hw.send_request(HwRequest(line=build(self.master_id, dst, "GETTEMPA", "0"), expect_prefix=None, timeout_s=0.8, on_done=None, priority=6))
        self.hw.send_request(HwRequest(line=build(self.master_id, dst, "GETTEMPM", "0"), expect_prefix=None, timeout_s=0.8, on_done=None, priority=6))
        self._poll_idle_wind(dst, axis_state, axis)
        # PWM wird separat über _poll_pwm() abgefragt (max. alle ~2s).

    def _poll_idle_telemetry(self, dst:int, axis_state:AxisState, axis:str):
        """Telemetrie, die im Idle abgefragt werden darf (Temp/Wind).

        Während der Fahrt bewusst NICHT pollen, um die Positionsanzeige nicht auszubremsen.
        """
        self.hw.send_request(HwRequest(line=build(self.master_id, dst, "GETTEMPA", "0"), expect_prefix=None, timeout_s=0.8, on_done=None, priority=6))
        self.hw.send_request(HwRequest(line=build(self.master_id, dst, "GETTEMPM", "0"), expect_prefix=None, timeout_s=0.8, on_done=None, priority=6))
        # Wind wird separat mit eigener Taktung/Prio gepollt (siehe _poll_idle_wind()).

    def _poll_idle_wind(self, dst:int, axis_state:AxisState, axis:str):
        """Windgeschwindigkeit im Idle pollen (AZ)."""
        # Winddaten kommen ausschließlich vom AZ-Rotor.
        if int(dst) != int(self.slave_az) or (not self.wind_enabled):
            return
        self._wind_speed_inflight = True
        self._wind_speed_sent_ts = time.time()
        self.hw.send_request(HwRequest(line=build(self.master_id, dst, "GETANEMO", "0"), expect_prefix=None, timeout_s=0.8, on_done=None, priority=2))

    def _poll_idle_wind_dir(self, dst:int, axis_state:AxisState, axis:str):
        """Windrichtung im Idle pollen (AZ), zeitversetzt zu GETANEMO."""
        if int(dst) != int(self.slave_az) or (not self.wind_enabled):
            return
        self._wind_dir_inflight = True
        self._wind_dir_sent_ts = time.time()
        self.hw.send_request(HwRequest(
            line=build(self.master_id, dst, "GETWINDDIR", "0"),
            expect_prefix=None,
            timeout_s=0.8,
            on_done=None,
            priority=2,
        ))

    def _poll_idle_wind_beaufort(self, dst:int, axis_state:AxisState, axis:str):
        """Windstärke in Beaufort (0–12) im Idle pollen (AZ)."""
        if int(dst) != int(self.slave_az) or (not self.wind_enabled):
            return
        self._wind_beaufort_inflight = True
        self._wind_beaufort_sent_ts = time.time()
        self.hw.send_request(HwRequest(
            line=build(self.master_id, dst, "GETBEAUFORT", "0"),
            expect_prefix=None,
            timeout_s=0.8,
            on_done=None,
            priority=2,
        ))

    def _poll_wind_enable(self, dst:int, axis_state:AxisState, axis:str):
        """Abfragen, ob Windsensor vorhanden ist (GETWINDENABLE). Inflight-Guard verhindert Doppelabfrage."""
        if int(dst) != int(self.slave_az):
            return
        if self._wind_enable_inflight:
            return
        self._wind_enable_inflight = True
        self._wind_enable_sent_ts = time.time()    # muss time.time() sein – tick nutzt time.time() als 'now'
        self._last_wind_enable_poll = time.time()  # muss time.time() sein – tick nutzt time.time() als 'now'
        self.hw.send_request(HwRequest(
            line=build(self.master_id, dst, "GETWINDENABLE", "0"),
            expect_prefix=None,
            timeout_s=0.8,
            on_done=None,
            priority=3,
        ))

    def _poll_pwm(self, dst:int, axis_state:AxisState, axis:str):
        """PWM-Status abfragen (immer nur alle ~2s)."""
        self.hw.send_request(HwRequest(line=build(self.master_id, dst, "GETPWM", "0"), expect_prefix=None, timeout_s=0.8, on_done=None, priority=6))

    def _poll_minpwm(self, dst:int, axis_state:AxisState, axis:str):
        """MINPWM abfragen (Untergrenze für PWM)."""
        self.hw.send_request(HwRequest(line=build(self.master_id, dst, "GETMINPWM", "0"), expect_prefix=None, timeout_s=0.8, on_done=None, priority=6))

    def _poll_cal_state(self, dst:int, axis_state:AxisState, priority: int = 5) -> None:
        """GETCALSTATE abfragen (state;progress). state: 0=IDLE,1=RUNNING,2=DONE,3=ABORT."""
        self.hw.send_request(HwRequest(
            line=build(self.master_id, dst, "GETCALSTATE", "0"),
            expect_prefix=None, timeout_s=0.8, on_done=None, priority=priority,
        ))

    _CAL_LIVE_BLOCKS = [
        (1, 0), (1, 12), (1, 24), (1, 36), (1, 48), (1, 60),
        (2, 0), (2, 12), (2, 24), (2, 36), (2, 48), (2, 60),
    ]

    def _fetch_cal_bins(self, dst:int, axis_state:AxisState, axis_name:str, priority: int = 3) -> None:
        """CAL-Bins sequentiell abfragen (1 Block → warten → nächster), um Bus nicht zu überlasten."""
        if self._cal_bins_inflight_az:
            return
        self._cal_bins_inflight_az = True
        self._cal_bins_received_az = 0
        self._cal_bins_temp_cw = [0] * 72
        self._cal_bins_temp_ccw = [0] * 72
        self._cal_bins_priority_az = priority
        self._send_next_cal_block(dst, axis_state, 0)

    def _send_next_cal_block(self, dst:int, axis_state:AxisState, idx:int) -> None:
        if idx >= len(self._CAL_LIVE_BLOCKS):
            # Alle 12 Blöcke empfangen: Temp in axis_state übernehmen (nur wenn noch DONE)
            if self._cal_bins_temp_cw and self._cal_bins_temp_ccw and getattr(axis_state, "cal_state", 0) == 2:
                axis_state.cal_bins_cw = list(self._cal_bins_temp_cw)
                axis_state.cal_bins_ccw = list(self._cal_bins_temp_ccw)
            self._cal_bins_temp_cw = None
            self._cal_bins_temp_ccw = None
            self._cal_bins_inflight_az = False
            self._cal_bins_fetched_az = True
            if not self._live_bins_inflight_az:
                self._fetch_live_bins(dst, axis_state, "AZ")
                self._last_live_bins_az = time.time()
            return
        direction, start = self._CAL_LIVE_BLOCKS[idx]
        params = f"{direction};{start};12"
        line = build(self.master_id, dst, "GETCALBINS", params)
        ctrl = self
        temp_cw, temp_ccw = self._cal_bins_temp_cw, self._cal_bins_temp_ccw

        def on_done(tel:Optional[Telegram], err:Optional[str]):
            if tel:
                axis_state.last_rx_ts = time.time()
                axis_state.online = True
            if err:
                ctrl.log.write("WARN", f"AZ GETCALBINS Block {idx+1} fehlgeschlagen: {err}")
            if tel and tel.params and temp_cw is not None and temp_ccw is not None:
                parts = (tel.params or "").strip().split(";")
                if len(parts) >= 4:
                    dir_val = _parse_int(parts[0])
                    start_val = _parse_int(parts[1])
                    count_val = _parse_int(parts[2])
                    if dir_val is not None and start_val is not None and count_val is not None:
                        bins = temp_cw if dir_val == 1 else temp_ccw
                        if bins and 0 <= start_val < 72 and 1 <= count_val <= 12:
                            for i in range(count_val):
                                v = _parse_int(parts[3 + i]) if (3 + i) < len(parts) else None
                                if v is not None and start_val + i < 72:
                                    bins[start_val + i] = int(v)
            ctrl._cal_bins_received_az = idx + 1
            ctrl._send_next_cal_block(dst, axis_state, idx + 1)
        prio = getattr(self, "_cal_bins_priority_az", 3)
        self.hw.send_request(HwRequest(
            line=line,
            expect_prefix="ACK_GETCALBINS",
            timeout_s=0.5,
            on_done=on_done,
            priority=prio,
            dont_disconnect_on_timeout=True,
        ))

    def _fetch_cal_bins_el(self, dst:int, axis_state:AxisState, axis_name:str, priority: int = 3) -> None:
        if self._cal_bins_inflight_el:
            return
        self._cal_bins_inflight_el = True
        self._cal_bins_temp_cw_el = [0] * 72
        self._cal_bins_temp_ccw_el = [0] * 72
        self._cal_bins_priority_el = priority
        self._send_next_cal_block_el(dst, axis_state, 0)

    def _send_next_cal_block_el(self, dst:int, axis_state:AxisState, idx:int) -> None:
        if idx >= len(self._CAL_LIVE_BLOCKS):
            if self._cal_bins_temp_cw_el and self._cal_bins_temp_ccw_el and getattr(axis_state, "cal_state", 0) == 2:
                axis_state.cal_bins_cw = list(self._cal_bins_temp_cw_el)
                axis_state.cal_bins_ccw = list(self._cal_bins_temp_ccw_el)
            self._cal_bins_temp_cw_el = None
            self._cal_bins_temp_ccw_el = None
            self._cal_bins_inflight_el = False
            self._cal_bins_fetched_el = True
            if not self._live_bins_inflight_el:
                self._fetch_live_bins_el(dst, axis_state, "EL")
                self._last_live_bins_el = time.time()
            return
        direction, start = self._CAL_LIVE_BLOCKS[idx]
        params = f"{direction};{start};12"
        line = build(self.master_id, dst, "GETCALBINS", params)
        ctrl = self
        temp_cw, temp_ccw = self._cal_bins_temp_cw_el, self._cal_bins_temp_ccw_el

        def on_done(tel:Optional[Telegram], err:Optional[str]):
            if tel:
                axis_state.last_rx_ts = time.time()
                axis_state.online = True
            if err:
                ctrl.log.write("WARN", f"EL GETCALBINS Block {idx+1} fehlgeschlagen: {err}")
            if tel and tel.params and temp_cw is not None and temp_ccw is not None:
                parts = (tel.params or "").strip().split(";")
                if len(parts) >= 4:
                    dir_val = _parse_int(parts[0])
                    start_val = _parse_int(parts[1])
                    count_val = _parse_int(parts[2])
                    if dir_val is not None and start_val is not None and count_val is not None:
                        bins = temp_cw if dir_val == 1 else temp_ccw
                        if bins and 0 <= start_val < 72 and 1 <= count_val <= 12:
                            for i in range(count_val):
                                v = _parse_int(parts[3 + i]) if (3 + i) < len(parts) else None
                                if v is not None and start_val + i < 72:
                                    bins[start_val + i] = int(v)
            ctrl._send_next_cal_block_el(dst, axis_state, idx + 1)
        prio = getattr(self, "_cal_bins_priority_el", 3)
        self.hw.send_request(HwRequest(
            line=line,
            expect_prefix="ACK_GETCALBINS",
            timeout_s=0.5,
            on_done=on_done,
            priority=prio,
            dont_disconnect_on_timeout=True,
        ))

    def _fetch_live_bins(self, dst:int, axis_state:AxisState, axis_name:str, priority: int = 3) -> None:
        """LIVE-Bins sequentiell abfragen (1 Block → warten → nächster)."""
        if self._live_bins_inflight_az:
            return
        self._live_bins_inflight_az = True
        self._live_bins_received_az = 0
        self._live_bins_temp_cw = [0] * 72
        self._live_bins_temp_ccw = [0] * 72
        self._live_bins_priority_az = priority
        self._send_next_live_block(dst, axis_state, 0)

    def _send_next_live_block(self, dst:int, axis_state:AxisState, idx:int) -> None:
        if idx >= len(self._CAL_LIVE_BLOCKS):
            # Alle 12 Blöcke empfangen: Temp in axis_state übernehmen
            if self._live_bins_temp_cw and self._live_bins_temp_ccw:
                axis_state.live_bins_cw = list(self._live_bins_temp_cw)
                axis_state.live_bins_ccw = list(self._live_bins_temp_ccw)
            self._live_bins_temp_cw = None
            self._live_bins_temp_ccw = None
            self._live_bins_inflight_az = False
            return
        direction, start = self._CAL_LIVE_BLOCKS[idx]
        params = f"{direction};{start};12"
        line = build(self.master_id, dst, "GETLIVEBINS", params)
        ctrl = self
        temp_cw, temp_ccw = self._live_bins_temp_cw, self._live_bins_temp_ccw

        def on_done(tel:Optional[Telegram], err:Optional[str]):
            if tel:
                axis_state.last_rx_ts = time.time()
                axis_state.online = True
            if err:
                ctrl.log.write("WARN", f"AZ GETLIVEBINS Block {idx+1} fehlgeschlagen: {err}")
            if tel and tel.params and temp_cw is not None and temp_ccw is not None:
                parts = (tel.params or "").strip().split(";")
                if len(parts) >= 4:
                    dir_val = _parse_int(parts[0])
                    start_val = _parse_int(parts[1])
                    count_val = _parse_int(parts[2])
                    if dir_val is not None and start_val is not None and count_val is not None:
                        bins = temp_cw if dir_val == 1 else temp_ccw
                        if bins and 0 <= start_val < 72 and 1 <= count_val <= 12:
                            for i in range(count_val):
                                v = _parse_int(parts[3 + i]) if (3 + i) < len(parts) else None
                                if v is not None and start_val + i < 72:
                                    bins[start_val + i] = int(v)
            ctrl._send_next_live_block(dst, axis_state, idx + 1)
        prio = getattr(self, "_live_bins_priority_az", 3)
        self.hw.send_request(HwRequest(
            line=line,
            expect_prefix="ACK_GETLIVEBINS",
            timeout_s=0.5,
            on_done=on_done,
            priority=prio,
            dont_disconnect_on_timeout=True,
        ))

    def _fetch_live_bins_el(self, dst:int, axis_state:AxisState, axis_name:str, priority: int = 3) -> None:
        if self._live_bins_inflight_el:
            return
        self._live_bins_inflight_el = True
        self._live_bins_temp_cw_el = [0] * 72
        self._live_bins_temp_ccw_el = [0] * 72
        self._live_bins_priority_el = priority
        self._send_next_live_block_el(dst, axis_state, 0)

    def _send_next_live_block_el(self, dst:int, axis_state:AxisState, idx:int) -> None:
        if idx >= len(self._CAL_LIVE_BLOCKS):
            if self._live_bins_temp_cw_el and self._live_bins_temp_ccw_el:
                axis_state.live_bins_cw = list(self._live_bins_temp_cw_el)
                axis_state.live_bins_ccw = list(self._live_bins_temp_ccw_el)
            self._live_bins_temp_cw_el = None
            self._live_bins_temp_ccw_el = None
            self._live_bins_inflight_el = False
            return
        direction, start = self._CAL_LIVE_BLOCKS[idx]
        params = f"{direction};{start};12"
        line = build(self.master_id, dst, "GETLIVEBINS", params)
        ctrl = self
        temp_cw, temp_ccw = self._live_bins_temp_cw_el, self._live_bins_temp_ccw_el

        def on_done(tel:Optional[Telegram], err:Optional[str]):
            if tel:
                axis_state.last_rx_ts = time.time()
                axis_state.online = True
            if err:
                ctrl.log.write("WARN", f"EL GETLIVEBINS Block {idx+1} fehlgeschlagen: {err}")
            if tel and tel.params and temp_cw is not None and temp_ccw is not None:
                parts = (tel.params or "").strip().split(";")
                if len(parts) >= 4:
                    dir_val = _parse_int(parts[0])
                    start_val = _parse_int(parts[1])
                    count_val = _parse_int(parts[2])
                    if dir_val is not None and start_val is not None and count_val is not None:
                        bins = temp_cw if dir_val == 1 else temp_ccw
                        if bins and 0 <= start_val < 72 and 1 <= count_val <= 12:
                            for i in range(count_val):
                                v = _parse_int(parts[3 + i]) if (3 + i) < len(parts) else None
                                if v is not None and start_val + i < 72:
                                    bins[start_val + i] = int(v)
            ctrl._send_next_live_block_el(dst, axis_state, idx + 1)
        prio = getattr(self, "_live_bins_priority_el", 3)
        self.hw.send_request(HwRequest(
            line=line,
            expect_prefix="ACK_GETLIVEBINS",
            timeout_s=0.5,
            on_done=on_done,
            priority=prio,
            dont_disconnect_on_timeout=True,
        ))

    def _fetch_acc_bins(self, dst:int, axis_state:AxisState, axis_name:str, priority: int = 3) -> None:
        """ACC-Bins sequentiell abfragen (wie LIVE, schnelle aktuelle Last)."""
        if self._acc_bins_inflight_az:
            return
        self._acc_bins_inflight_az = True
        self._acc_bins_temp_cw = [0] * 72
        self._acc_bins_temp_ccw = [0] * 72
        self._acc_bins_priority_az = priority
        self._send_next_acc_block(dst, axis_state, 0)

    def _send_next_acc_block(self, dst:int, axis_state:AxisState, idx:int) -> None:
        if time.time() < self._stats_cooldown_until:
            self._acc_bins_inflight_az = False
            self._acc_bins_temp_cw = None
            self._acc_bins_temp_ccw = None
            return
        if idx >= len(self._CAL_LIVE_BLOCKS):
            if self._acc_bins_temp_cw and self._acc_bins_temp_ccw:
                axis_state.acc_bins_cw = list(self._acc_bins_temp_cw)
                axis_state.acc_bins_ccw = list(self._acc_bins_temp_ccw)
            self._acc_bins_temp_cw = None
            self._acc_bins_temp_ccw = None
            self._acc_bins_inflight_az = False
            return
        direction, start = self._CAL_LIVE_BLOCKS[idx]
        params = f"{direction};{start};12"
        line = build(self.master_id, dst, "GETACCBINS", params)
        ctrl = self
        temp_cw = self._acc_bins_temp_cw
        temp_ccw = self._acc_bins_temp_ccw

        def on_done(tel:Optional[Telegram], err:Optional[str]):
            if tel:
                axis_state.last_rx_ts = time.time()
                axis_state.online = True
            if err:
                ctrl.log.write("WARN", f"AZ GETACCBINS Block {idx+1} fehlgeschlagen: {err}")
            if tel and tel.params and temp_cw is not None and temp_ccw is not None:
                parts = (tel.params or "").strip().split(";")
                if len(parts) >= 4:
                    dir_val = _parse_int(parts[0])
                    start_val = _parse_int(parts[1])
                    count_val = _parse_int(parts[2])
                    if dir_val is not None and start_val is not None and count_val is not None:
                        bins = temp_cw if dir_val == 1 else temp_ccw
                        if bins and 0 <= start_val < 72 and 1 <= count_val <= 12:
                            for i in range(count_val):
                                v = _parse_int(parts[3 + i]) if (3 + i) < len(parts) else None
                                if v is not None and start_val + i < 72:
                                    bins[start_val + i] = int(v)
            ctrl._send_next_acc_block(dst, axis_state, idx + 1)
        prio = getattr(self, "_acc_bins_priority_az", 3)
        self.hw.send_request(HwRequest(
            line=line,
            expect_prefix="ACK_GETACCBINS",
            timeout_s=0.5,
            on_done=on_done,
            priority=prio,
            dont_disconnect_on_timeout=True,
        ))

    def _fetch_acc_bins_el(self, dst:int, axis_state:AxisState, axis_name:str, priority: int = 3) -> None:
        if self._acc_bins_inflight_el:
            return
        self._acc_bins_inflight_el = True
        self._acc_bins_temp_cw_el = [0] * 72
        self._acc_bins_temp_ccw_el = [0] * 72
        self._acc_bins_priority_el = priority
        self._send_next_acc_block_el(dst, axis_state, 0)

    def _send_next_acc_block_el(self, dst:int, axis_state:AxisState, idx:int) -> None:
        if time.time() < self._stats_cooldown_until:
            self._acc_bins_inflight_el = False
            self._acc_bins_temp_cw_el = None
            self._acc_bins_temp_ccw_el = None
            return
        if idx >= len(self._CAL_LIVE_BLOCKS):
            if self._acc_bins_temp_cw_el and self._acc_bins_temp_ccw_el:
                axis_state.acc_bins_cw = list(self._acc_bins_temp_cw_el)
                axis_state.acc_bins_ccw = list(self._acc_bins_temp_ccw_el)
            self._acc_bins_temp_cw_el = None
            self._acc_bins_temp_ccw_el = None
            self._acc_bins_inflight_el = False
            return
        direction, start = self._CAL_LIVE_BLOCKS[idx]
        params = f"{direction};{start};12"
        line = build(self.master_id, dst, "GETACCBINS", params)
        ctrl = self
        temp_cw, temp_ccw = self._acc_bins_temp_cw_el, self._acc_bins_temp_ccw_el

        def on_done(tel:Optional[Telegram], err:Optional[str]):
            if tel:
                axis_state.last_rx_ts = time.time()
                axis_state.online = True
            if err:
                ctrl.log.write("WARN", f"EL GETACCBINS Block {idx+1} fehlgeschlagen: {err}")
            if tel and tel.params and temp_cw is not None and temp_ccw is not None:
                parts = (tel.params or "").strip().split(";")
                if len(parts) >= 4:
                    dir_val = _parse_int(parts[0])
                    start_val = _parse_int(parts[1])
                    count_val = _parse_int(parts[2])
                    if dir_val is not None and start_val is not None and count_val is not None:
                        bins = temp_cw if dir_val == 1 else temp_ccw
                        if bins and 0 <= start_val < 72 and 1 <= count_val <= 12:
                            for i in range(count_val):
                                v = _parse_int(parts[3 + i]) if (3 + i) < len(parts) else None
                                if v is not None and start_val + i < 72:
                                    bins[start_val + i] = int(v)
            ctrl._send_next_acc_block_el(dst, axis_state, idx + 1)
        prio = getattr(self, "_acc_bins_priority_el", 3)
        self.hw.send_request(HwRequest(
            line=line,
            expect_prefix="ACK_GETACCBINS",
            timeout_s=0.5,
            on_done=on_done,
            priority=prio,
            dont_disconnect_on_timeout=True,
        ))

    # -------------------- Async telegram handler --------------------
    def _on_async_tel(self, tel:Telegram):
        # Asynchrone ACK/NAK aus Polling (wenn Requests ohne pending gesendet werden).
        try:
            axis_state: AxisState | None = None
            axis_name = None
            if tel.src == self.slave_az:
                axis_state = self.az
                axis_name = "AZ"
            elif tel.src == self.slave_el:
                axis_state = self.el
                axis_name = "EL"

            if axis_state is not None:
                # Jede gültige Antwort vom Slave zählt als "online"
                axis_state.online = True
                axis_state.last_rx_ts = time.time()

                # Position (GETPOSDG ohne pending)
                if tel.cmd.startswith("ACK_GETPOSDG") or tel.cmd.startswith("ACK_POSDG"):
                    # Inflight freigeben (sonst stauen sich Requests)
                    try:
                        axis_state.pos_poll_inflight = False
                    except Exception:
                        pass
                    axis_state.online = True
                    axis_state.last_rx_ts = time.time()
                    v = _parse_float(tel.params.split(";")[-1])
                    if v is not None:
                        d10 = int(round(v * 10))
                        prev_pos = int(axis_state.pos_d10)
                        prev_moving = bool(axis_state.moving)
                        # Beim allerersten Sample nach Programmstart ist prev_pos nur Default (meist 0)
                        # und darf nicht als echte Bewegung interpretiert werden.
                        try:
                            had_prev_sample = float(getattr(axis_state, "_last_sample_ts", 0.0) or 0.0) > 0.0
                        except Exception:
                            had_prev_sample = False

                        try:
                            exp = float(getattr(axis_state, "pos_poll_expected_period_s", 0.2) or 0.2)
                        except Exception:
                            exp = 0.2
                        axis_state.update_position_sample(d10, sample_ts=time.time(), expected_period_s=exp)

                        # Während aktiver Referenzfahrt darf "moving" NICHT auf False fallen,
                        # auch wenn temporär keine sauberen Positions-Samples ankommen.
                        # Sonst blinkt die Anzeige "Fährt" zwischen ja/nein.
                        try:
                            if bool(getattr(axis_state, "ref_poll_active", False)):
                                axis_state.moving = True
                                axis_state.stop_confirm_samples = 0
                                return
                        except Exception:
                            pass

                        # Bewegung/Stillstand robust bestimmen:
                        # - Nicht sofort "moving=False" sobald wir nahe am Ziel sind, weil der Rotor
                        #   beim Überschleifen noch weiterläuft. Das führte zu langsamem Polling und
                        #   zu einem kurzen "Stocken" im grünen Zeiger.
                        # - Stattdessen: "steht" erst nach mehreren stabilen Samples.
                        #
                        # WICHTIG (Bugfix):
                        # - Beim Programmstart oder nach einem externen SET (z.B. PST) kann `moving=True`
                        #   gesetzt sein, obwohl der Rotor faktisch steht (Ziel == Position).
                        # - Außerdem kann die Zielabweichung >0,2° sein, obwohl der Motor steht.
                        #   Daher basiert "steht" primär auf *Positionsstabilität*.
                        dpos = abs(int(d10) - int(prev_pos)) if had_prev_sample else 0
                        stable = (not had_prev_sample) or (dpos <= 1)

                        if stable:
                            try:
                                axis_state.stop_confirm_samples = int(getattr(axis_state, "stop_confirm_samples", 0)) + 1
                            except Exception:
                                axis_state.stop_confirm_samples = 1
                        else:
                            axis_state.stop_confirm_samples = 0

                        if axis_state.stop_confirm_samples >= 4:
                            axis_state.moving = False
                        else:
                            # Wenn wir eine nennenswerte Positionsänderung sehen, sind wir sicher in Bewegung.
                            if dpos > 1:
                                axis_state.moving = True
                            else:
                                axis_state.moving = prev_moving
                    return
                if tel.cmd.startswith("NAK_GETPOSDG") or tel.cmd.startswith("NAK_POSDG"):
                    try:
                        axis_state.pos_poll_inflight = False
                    except Exception:
                        pass
                    axis_state.online = True
                    axis_state.last_rx_ts = time.time()
                    self.log.write("WARN", f"{axis_name} GETPOSDG NAK: {tel.params}")
                    return

                # Referenz-Status (GETREF Polling ohne pending)
                if tel.cmd.startswith("ACK_GETREF") or tel.cmd.startswith("ACK_REF"):
                    axis_state.last_rx_ts = time.time()
                    v = _parse_int(tel.params.strip())
                    if v == 1:
                        axis_state.referenced = True
                        axis_state.ref_poll_active = False
                        # Homing beendet (oder war bereits fertig)
                        axis_state.moving = False
                        self.log.write("INFO", f"{axis_name} referenziert")
                        # Falls Ziel bereits gesetzt ist, erneut anstoßen
                        if abs(axis_state.target_d10) > 0:
                            if axis_name == "AZ":
                                self.set_az_from_spid(axis_state.target_d10)
                            else:
                                self.set_el_from_spid(axis_state.target_d10)
                    else:
                        # Noch nicht referenziert -> Homing läuft weiter
                        axis_state.referenced = False
                        # Nur während aktiver Referenzfahrt "Fährt" setzen
                        if axis_state.ref_poll_active:
                            axis_state.moving = True
                    return
                if tel.cmd.startswith("NAK_GETREF") or tel.cmd.startswith("NAK_REF"):
                    axis_state.last_rx_ts = time.time()
                    self.log.write("WARN", f"{axis_name} GETREF NAK: {tel.params}")
                    return

                # Warnungen
                if tel.cmd.startswith("ACK_GETWARN") or tel.cmd.startswith("ACK_WARN"):
                    p = tel.params.strip()
                    axis_state.warnings.clear()
                    if p and p != "0":
                        for part in p.split(";"):
                            n = _parse_int(part)
                            if n is not None:
                                axis_state.warnings.add(n)
                    return
                if tel.cmd.startswith("NAK_GETWARN") or tel.cmd.startswith("NAK_WARN"):
                    self.log.write("WARN", f"{axis_name} GETWARN NAK: {tel.params}")
                    return

                # Fehlercode
                if tel.cmd.startswith("ACK_GETERR") or tel.cmd.startswith("ACK_ERR"):
                    code = _parse_int(tel.params.strip())
                    if code is not None:
                        axis_state.error_code = int(code)
                        if axis_state.error_code != 0:
                            axis_state.moving = False
                    return
                if tel.cmd.startswith("NAK_GETERR") or tel.cmd.startswith("NAK_ERR"):
                    self.log.write("WARN", f"{axis_name} GETERR NAK: {tel.params}")
                    return

                # Telemetrie
                if tel.cmd.startswith("ACK_GETTEMPA"):
                    v = _parse_float(tel.params.strip())
                    if v is not None:
                        axis_state.telemetry.temp_ambient_c = v
                    return
                if tel.cmd.startswith("ACK_GETTEMPM"):
                    v = _parse_float(tel.params.strip())
                    if v is not None:
                        axis_state.telemetry.temp_motor_c = v
                    return

                # Antennen-Versätze (GETANTOFF1–3)
                if tel.cmd.startswith("ACK_GETANTOFF1"):
                    v = _parse_float(tel.params.strip())
                    if v is not None:
                        axis_state.antoff1 = max(0.0, min(360.0, v))
                    return
                if tel.cmd.startswith("ACK_GETANTOFF2"):
                    v = _parse_float(tel.params.strip())
                    if v is not None:
                        axis_state.antoff2 = max(0.0, min(360.0, v))
                    return
                if tel.cmd.startswith("ACK_GETANTOFF3"):
                    v = _parse_float(tel.params.strip())
                    if v is not None:
                        axis_state.antoff3 = max(0.0, min(360.0, v))
                    return
                if tel.cmd.startswith("ACK_GETANEMO") or tel.cmd.startswith("ACK_ANEMO"):
                    v = _parse_float_any(tel.params)
                    self._wind_speed_inflight = False
                    if v is not None:
                        axis_state.telemetry.wind_kmh = v
                    return
                if tel.cmd.startswith("ACK_WINDDIR") or tel.cmd.startswith("ACK_GETWINDDIR"):
                    v = _parse_float_any(tel.params)
                    self._wind_dir_inflight = False
                    if v is not None:
                        axis_state.telemetry.wind_dir_deg = v
                    return
                if tel.cmd.startswith("ACK_GETBEAUFORT") or tel.cmd.startswith("ACK_BEAUFORT"):
                    parts = (tel.params or "").strip().split(";")[0].split(":")
                    v = _parse_int(parts[0].strip()) if parts else None
                    self._wind_beaufort_inflight = False
                    if v is not None and 0 <= v <= 12:
                        axis_state.telemetry.wind_beaufort = int(v)
                    return
                if (tel.cmd.startswith("ACK_GETWINDENABLE") or tel.cmd.startswith("ACK_WINDENABLE")
                        or tel.cmd.startswith("ACK_SETWINDENABLE")):
                    self._wind_enable_inflight = False
                    v = _parse_int(tel.params.strip())
                    if v is not None:
                        self.wind_enabled = bool(int(v) != 0)
                        self.wind_enabled_known = True
                        if not self.wind_enabled:
                            self._wind_speed_inflight = False
                            self._wind_dir_inflight = False
                            self._wind_beaufort_inflight = False
                            try:
                                self.az.telemetry.wind_kmh = None
                                self.az.telemetry.wind_dir_deg = None
                                self.az.telemetry.wind_beaufort = None
                                self.el.telemetry.wind_kmh = None
                                self.el.telemetry.wind_dir_deg = None
                                self.el.telemetry.wind_beaufort = None
                            except Exception:
                                pass
                    return
                if tel.cmd.startswith("ACK_GETPWM"):
                    v = _parse_float(tel.params.strip())
                    if v is not None:
                        axis_state.telemetry.pwm_max_pct = v
                    return
                if tel.cmd.startswith("ACK_GETMINPWM") or tel.cmd.startswith("ACK_MINPWM"):
                    v = _parse_float(tel.params.strip())
                    if v is not None:
                        axis_state.telemetry.pwm_min_pct = v
                    return

                # Kalibrier-Status (GETCALSTATE)
                if tel.cmd.startswith("ACK_GETCALSTATE") or tel.cmd.startswith("ACK_CALSTATE"):
                    parts = (tel.params or "").strip().split(";")
                    state = _parse_int(parts[0]) if parts else None
                    if state is not None:
                        axis_state.cal_state = int(state)
                        if state != 2 and axis_name == "AZ":
                            axis_state.cal_bins_cw = None
                            axis_state.cal_bins_ccw = None
                            self._cal_bins_fetched_az = False
                        elif state != 2 and axis_name == "EL":
                            axis_state.cal_bins_cw = None
                            axis_state.cal_bins_ccw = None
                            self._cal_bins_fetched_el = False
                        elif state == 2 and axis_name == "AZ" and self._statistics_window_open:
                            if not self._cal_bins_inflight_az and (
                                axis_state.cal_bins_cw is None or axis_state.cal_bins_ccw is None
                                or not self._cal_bins_fetched_az
                            ):
                                self._fetch_cal_bins(int(self.slave_az), axis_state, "AZ")
                            elif not self._live_bins_inflight_az and self._cal_bins_fetched_az:
                                self._fetch_live_bins(int(self.slave_az), axis_state, "AZ")
                                self._last_live_bins_az = time.time()
                        elif state == 2 and axis_name == "EL" and self._statistics_window_open:
                            dst_el = int(self.slave_el)
                            if not self._cal_bins_inflight_el and (
                                axis_state.cal_bins_cw is None or axis_state.cal_bins_ccw is None
                                or not self._cal_bins_fetched_el
                            ):
                                self._fetch_cal_bins_el(dst_el, axis_state, "EL")
                            elif not self._live_bins_inflight_el and self._cal_bins_fetched_el:
                                self._fetch_live_bins_el(dst_el, axis_state, "EL")
                                self._last_live_bins_el = time.time()
                    return

                # Kalibrier-Bins + Live-Bins (case-insensitiv für Firmware-Varianten)
                cmd_u = (tel.cmd or "").upper()
                if cmd_u.startswith("ACK_GETCALBINS") or cmd_u.startswith("ACK_CALBINS"):
                    parts = (tel.params or "").strip().split(";")
                    if len(parts) >= 4:
                        dir_val = _parse_int(parts[0])
                        start_val = _parse_int(parts[1])
                        count_val = _parse_int(parts[2])
                        if dir_val is not None and start_val is not None and count_val is not None:
                            bins = axis_state.cal_bins_cw if dir_val == 1 else axis_state.cal_bins_ccw
                            if bins is not None and 0 <= start_val < 72 and 1 <= count_val <= 12:
                                for i in range(count_val):
                                    v = _parse_int(parts[3 + i]) if (3 + i) < len(parts) else None
                                    if v is not None:
                                        idx = start_val + i
                                        if idx < 72:
                                            bins[idx] = int(v)
                            # Fertig wenn alle 12 Blöcke angekommen
                            if axis_name == "AZ":
                                self._cal_bins_received_az = int(getattr(self, "_cal_bins_received_az", 0)) + 1
                                if self._cal_bins_received_az >= 12:
                                    self._cal_bins_inflight_az = False
                                    self._cal_bins_fetched_az = True
                    return

                # Live-Bins (ACK_GETLIVEBINS: dir;start;count;v0;v1;...;vn)
                if cmd_u.startswith("ACK_GETLIVEBINS") or cmd_u.startswith("ACK_LIVEBINS"):
                    parts = (tel.params or "").strip().split(";")
                    if len(parts) >= 4:
                        dir_val = _parse_int(parts[0])
                        start_val = _parse_int(parts[1])
                        count_val = _parse_int(parts[2])
                        if dir_val is not None and start_val is not None and count_val is not None:
                            bins = axis_state.live_bins_cw if dir_val == 1 else axis_state.live_bins_ccw
                            if bins is not None and 0 <= start_val < 72 and 1 <= count_val <= 12:
                                for i in range(count_val):
                                    v = _parse_int(parts[3 + i]) if (3 + i) < len(parts) else None
                                    if v is not None:
                                        idx = start_val + i
                                        if idx < 72:
                                            bins[idx] = int(v)
                            if axis_name == "AZ":
                                self._live_bins_received_az = int(getattr(self, "_live_bins_received_az", 0)) + 1
                                if self._live_bins_received_az >= 12:
                                    self._live_bins_inflight_az = False
                    return
        except Exception:
            pass

        if tel.cmd == "ERR":
            code = _parse_int(tel.params.strip())
            if code is None:
                return
            if tel.src == self.slave_az:
                self.az.error_code = int(code); self.az.moving = False
            elif tel.src == self.slave_el:
                self.el.error_code = int(code); self.el.moving = False
            self.log.write("ERROR", f"ERR vom Slave {tel.src}: {code}")

