"""RS485-Rotor-Controller: Kommandos, UI, Referenz; Polling/Async in Mixins."""

from __future__ import annotations

import time
from typing import Callable, Optional

from .angle_utils import shortest_delta_deg, wrap_deg
from .rotor_parse_utils import parse_setposcc_params
from .rs485_protocol import BROADCAST_DST, build, Telegram
from .hardware_client import HardwareClient, HwRequest
from .rotor_model import AxisState
from .rotor_controller_async import RotorControllerAsyncMixin
from .rotor_controller_polling import RotorControllerPollingMixin

# sync_ui_command_response: Firmware-NAK (Checksumme ok) — nicht mit Timeout (None) verwechseln
SYNC_UI_NAK_PREFIX = "__NAK__:"

# Nach SETPOSDG: SETPOSCC vom Encoder nicht sofort anwenden (Reihenfolge auf dem Bus).
_SETPOSCC_SUPPRESS_S = 0.6

# SETPOSCC vom Bus: Idle-Polling (inkl. GETPOSDG) aussetzen — nach jedem CC mindestens diese Zeit.
_CC_IDLE_DEFER_S = 1.0

# Nach SETPOSDG (Bridge oder Mitschnitt fremder Master): kurz nur Positions-/Fehler-Polling,
# damit kein GETANEMO/GETWARN/… den Bus mit dem Rotor kreuzt. Deckt auch schnelle Hotkey-Bursts
# (mehrere SETPOSDG hintereinander) ab; Idle-Zusatzabfragen werden solange wie bei Fahrt pausiert.
_SETPOSDG_POLL_GRACE_S = 1.5


class RotorController(RotorControllerPollingMixin, RotorControllerAsyncMixin):
    """Fachlogik: übersetzt SPID-Kommandos in RS485-Befehle + Polling + Status.

    Fixes:
    - optionales Deaktivieren der zweiten Achse (z.B. nur Slave 20 vorhanden)
    - UI-Kommandos bekommen hohe Priorität, damit Buttons sofort senden
    """

    def __init__(
        self,
        hw: HardwareClient,
        master_id: int,
        slave_az: int,
        slave_el: int,
        log,
        enable_az: bool = True,
        enable_el: bool = True,
        setposcc_ignore_src_master_ids: Optional[list[int]] = None,
        # controller_hw.cont_id: nur SETPOSCC mit dieser Bus-SRC (z. B. 2→1 an Bridge) ins UI
        setposcc_controller_src_id: int = 0,
    ):
        self.hw = hw
        self.log = log
        self.master_id = master_id
        # SETPOSCC vom Bus: diese Master-IDs nicht ins UI übernehmen (z. B. [2] bei Stör-Telegrammen)
        self.setposcc_ignore_src_master_ids = self._normalize_master_id_list(
            setposcc_ignore_src_master_ids
        )
        try:
            self.setposcc_controller_src_id = max(
                0, min(254, int(setposcc_controller_src_id))
            )
        except Exception:
            self.setposcc_controller_src_id = 0
        self.slave_az = slave_az
        self.slave_el = slave_el
        self.enable_az = bool(enable_az)
        self.enable_el = bool(enable_el)

        self.az = AxisState(position_wrap_360=True)
        self.el = AxisState(position_wrap_360=False)
        self._idle_poll_defer_until: float = 0.0
        self._setposdg_poll_grace_until_ts: float = 0.0

        self._last_poll = 0.0
        self._last_warn = 0.0
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
        # ACC-Bins (GETACCBINS): schnelle aktuelle Last; Abruf siehe tick_polling / request_immediate_stats
        self._last_acc_bins_az: float = 0.0
        self._acc_bins_inflight_az: bool = False
        self._acc_bins_temp_cw: Optional[list] = None
        self._acc_bins_temp_ccw: Optional[list] = None
        self._acc_bins_block_mask_az: Optional[list[bool]] = None
        self._acc_bins_last_tx_ts_az: float = 0.0
        self._acc_bins_finalize_pending_az: bool = False
        self._acc_bins_finalize_until_az: float = 0.0
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
        self._acc_bins_block_mask_el: Optional[list[bool]] = None
        self._acc_bins_last_tx_ts_el: float = 0.0
        self._acc_bins_finalize_pending_el: bool = False
        self._acc_bins_finalize_until_el: float = 0.0
        # Statistik-Fenster offen: nur dann CAL/LIVE/ACC pollen (Bus entlasten)
        self._statistics_window_open: bool = False
        # Einstellungen offen: CAL/LIVE wie Statistik pollen (Tab Statistik / „aus Kalibrierung“)
        self._settings_window_open: bool = False
        # Kompass-Fenster offen (Anzeige); Strom-Bins nur wenn Heatmap „strom“ aktiv (siehe set_compass_strom_heatmap_active)
        self._compass_window_open: bool = False
        self._compass_strom_heatmap_az: bool = False
        self._compass_strom_heatmap_el: bool = False
        # Kurzer Cooldown nach SETPOSDG: laufende GETACCBINS-Kette abbrechen, Bus kurz frei.
        self._stats_cooldown_until: float = 0.0
        # GETACCBINS: Kompass-Stromring — einmal vollständig, danach nur nach Bewegung (SETPOSDG).
        self._acc_bins_compass_initial_az: bool = False
        self._acc_bins_compass_initial_el: bool = False
        self._acc_bins_compass_arm_after_move_az: bool = False
        self._acc_bins_compass_arm_after_move_el: bool = False
        # Statistik/Einstellungen: einmal vollständig, danach nur nach SETPOSDG erneut.
        self._acc_bins_stats_initial_az: bool = False
        self._acc_bins_stats_initial_el: bool = False
        self._acc_bins_stats_arm_after_setpos_az: bool = False
        self._acc_bins_stats_arm_after_setpos_el: bool = False
        # Kompass-Manual-Eingabe: PST-SET für 10s ignorieren, damit nicht überschrieben wird
        self._compass_manual_az_ts: float = 0.0
        self._compass_manual_el_ts: float = 0.0
        # Callback: wird nach jedem erfolgreichen SETANTOFF-ACK aufgerufen (z.B. Kompassfenster-Refresh)
        self.on_antenna_offsets_changed: Optional[Callable[[], None]] = None
        self.on_antenna_angles_changed: Optional[Callable[[], None]] = None
        # RS485-Broadcast SETASELECT (DST 255): arg = Antenne 1–3 (Hintergrund-Thread → UI per QTimer marshallen)
        self.on_setaselect_from_bus: Optional[Callable[[int], None]] = None
        # Callback: wird aufgerufen, wenn SETREF kein ACK erhält (Timeout/NAK). arg=Achsname "AZ"/"EL".
        # Wichtig: wird aus einem Hintergrund-Thread aufgerufen → UI muss QTimer.singleShot nutzen.
        self.on_ref_start_failed: Optional[Callable[[str], None]] = None

        # Windsensor-Feature-Flag aus GETWINDENABLE (0/1). Off bis Antwort vorliegt.
        self.wind_enabled: bool = False
        self.wind_enabled_known: bool = False
        self._wind_enable_inflight: bool = False
        self._wind_enable_sent_ts: float = 0.0
        self._hw_prev_connected: bool = False
        self._startup_burst_until: float = 0.0
        self._last_wind_enable_poll: float = 0.0
        # Polling-Intervalle (ms):
        # Fahrt  : nur GETPOSDG (Fehler kommen per Broadcast ERR / ACK_ERR, kein GETERR-Poll)
        # Idle   : alle weiteren Abfragen mit unterschiedlichem Takt
        self._cfg_poll = {
            "pos_fast": 200,  # 5 Hz  (Fahrt; weniger Buslast, oft stabiler)
            "pos_slow": 10000,  # 10 s   (Idle)
            "warn": 10000,  # 10 s   (nur Idle)
            "pwm": 10000,  # 10 s   (nur Idle)
            "minpwm": 10000,  # 10 s   (nur Idle, ändert sich kaum)
            "telemetry": 10000,  # 10 s   (nur Idle: GETTEMPA/GETTEMPM)
            "ref": 300,  # 300 ms (Referenzfahrt: schnell)
            "ref_idle": 5000,  # 5 s    (Idle: Referenzstatus)
            "windenable": 10000,  # 10 s   (Idle: Sensor angesteckt/abgesteckt)
            "offline_timeout": 2000,
        }

        # Async telegram handler
        self.hw.on_async_telegram = self._on_async_tel
        try:
            self.hw.set_expected_response_dst(int(self.master_id))
        except Exception:
            pass

    @staticmethod
    def _normalize_master_id_list(ids: Optional[list]) -> list[int]:
        if not ids:
            return []
        out: list[int] = []
        for x in ids:
            try:
                out.append(int(x))
            except Exception:
                pass
        return out

    def update_ids(
        self,
        master_id: int,
        slave_az: int,
        slave_el: int,
        enable_az: bool = True,
        enable_el: bool = True,
    ):
        self.master_id = master_id
        self.slave_az = slave_az
        self.slave_el = slave_el
        self.enable_az = bool(enable_az)
        self.enable_el = bool(enable_el)
        try:
            self.hw.set_expected_response_dst(int(self.master_id))
        except Exception:
            pass

    def set_statistics_window_open(self, open: bool) -> None:
        """Statistik-Fenster offen/geschlossen. Nur wenn offen: CAL/LIVE/ACC pollen."""
        self._statistics_window_open = bool(open)
        if bool(open):
            self._acc_bins_stats_initial_az = False
            self._acc_bins_stats_initial_el = False
            self._acc_bins_stats_arm_after_setpos_az = False
            self._acc_bins_stats_arm_after_setpos_el = False

    def set_settings_window_open(self, open: bool) -> None:
        """Einstellungen offen/geschlossen. Wenn offen: CAL/LIVE wie beim Statistik-Fenster pollen."""
        self._settings_window_open = bool(open)
        if bool(open):
            self._acc_bins_stats_initial_az = False
            self._acc_bins_stats_initial_el = False
            self._acc_bins_stats_arm_after_setpos_az = False
            self._acc_bins_stats_arm_after_setpos_el = False

    def set_compass_strom_heatmap_active(self, az: bool, el: bool) -> None:
        """Ob im Kompass die Strom-Heatmap (AZ/EL) eingeschaltet ist — steuert GETACCBINS-Polling."""
        pa = bool(self._compass_strom_heatmap_az)
        pe = bool(self._compass_strom_heatmap_el)
        self._compass_strom_heatmap_az = bool(az)
        self._compass_strom_heatmap_el = bool(el)
        if bool(az) and not pa:
            self._acc_bins_compass_initial_az = False
        if bool(el) and not pe:
            self._acc_bins_compass_initial_el = False

    def set_compass_window_open(self, open: bool) -> None:
        """Kompass-Fenster offen/geschlossen."""
        self._compass_window_open = bool(open)
        # Strom-Flags immer leeren: beim Öffnen kamen sonst stale Werte aus CompassWindow-Init
        # (cfg) bis zur nächsten Heatmap-Änderung — dann lief GETACCBINS trotz abgewählter Strom-Heatmap.
        self._compass_strom_heatmap_az = False
        self._compass_strom_heatmap_el = False
        if bool(open):
            self._acc_bins_compass_initial_az = False
            self._acc_bins_compass_initial_el = False

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

    def request_immediate_error_poll(self) -> None:
        """Früher: GETERR nach Start. Fehler kommen nur noch per Firmware-Broadcast ERR; No-Op."""
        return

    def request_immediate_pos(self) -> None:
        """Positionsabfrage sofort mit höchster Priorität (beim Öffnen des Kompassfensters)."""
        if self.hw.is_connected():
            try:
                prio = 0
                if self.enable_az:
                    line = build(self.master_id, self.slave_az, "GETPOSDG", "0")
                    self.hw.send_request(
                        HwRequest(
                            line=line,
                            expect_prefix=None,
                            timeout_s=0.8,
                            on_done=None,
                            priority=prio,
                        )
                    )
                if self.enable_el:
                    line = build(self.master_id, self.slave_el, "GETPOSDG", "0")
                    self.hw.send_request(
                        HwRequest(
                            line=line,
                            expect_prefix=None,
                            timeout_s=0.8,
                            on_done=None,
                            priority=prio,
                        )
                    )
            except Exception:
                pass

    def request_antenna_offsets(self) -> None:
        """AZ-Antennenversätze vom Rotor lesen (GETANTOFF1–3). EL-Versatz entfällt."""
        if self.enable_az:
            for cmd in ("GETANTOFF1", "GETANTOFF2", "GETANTOFF3"):
                self.hw.send_request(
                    HwRequest(
                        line=build(self.master_id, self.slave_az, cmd, "0"),
                        expect_prefix=None,
                        timeout_s=0.5,
                        on_done=None,
                        priority=4,
                    )
                )

    def request_antenna_angles(self) -> None:
        """AZ-Antennen-Öffnungswinkel vom Rotor lesen (GETANGLE1–3)."""
        if self.enable_az:
            for cmd in ("GETANGLE1", "GETANGLE2", "GETANGLE3"):
                self.hw.send_request(
                    HwRequest(
                        line=build(self.master_id, self.slave_az, cmd, "0"),
                        expect_prefix=None,
                        timeout_s=0.5,
                        on_done=None,
                        priority=4,
                    )
                )

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
                try:
                    if callable(self.on_antenna_offsets_changed):
                        self.on_antenna_offsets_changed()
                except Exception:
                    pass
            else:
                if tel:
                    self.log.write("WARN", f"{cmd} -> NAK oder ungültige Antwort: {tel.cmd}")
                else:
                    self.log.write("WARN", f"{cmd} -> keine gültige ACK-Antwort")
            if on_done:
                on_done(ok)

        self.hw.send_request(
            HwRequest(
                line=line,
                expect_prefix=expect,
                timeout_s=1.2,
                on_done=done,
                priority=2,
            )
        )

    def set_antenna_angle(
        self,
        axis: str,
        slot: int,
        value_deg: float,
        on_done: Optional[Callable[[bool], None]] = None,
    ) -> None:
        """Antennen-Öffnungswinkel schreiben (SETANGLE1–3). Ruft on_done(success) nach ACK/NAK/Timeout.
        success=True nur bei ACK_SETANGLEx."""
        if slot not in (1, 2, 3):
            if on_done:
                on_done(False)
            return
        cmd = f"SETANGLE{slot}"
        v = str(round(max(0.0, min(360.0, value_deg)), 1))
        expect = f"ACK_SETANGLE{slot}"
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
            elif tel and tel.cmd.startswith("ACK_SETANGLE"):
                ok = True
                try:
                    setattr(axis_state, f"angle{slot}", float(v.replace(",", ".")))
                except Exception:
                    pass
                try:
                    if callable(self.on_antenna_angles_changed):
                        self.on_antenna_angles_changed()
                except Exception:
                    pass
            else:
                if tel:
                    self.log.write("WARN", f"{cmd} -> NAK oder ungültige Antwort: {tel.cmd}")
                else:
                    self.log.write("WARN", f"{cmd} -> keine gültige ACK-Antwort")
            if on_done:
                on_done(ok)

        self.hw.send_request(
            HwRequest(
                line=line,
                expect_prefix=expect,
                timeout_s=1.2,
                on_done=done,
                priority=2,
            )
        )

    def request_immediate_stats(self) -> None:
        """Statistik-Abfrage sofort auslösen (beim Öffnen des Statistik-Fensters). Priorität 0 = vor allem anderen.

        GETLIVEBINS hier nicht parallel starten: sonst blockiert das Pending die CAL-Bin-ACKs,
        und der Async-Pfad verwirft sie fälschlich. LIVE startet nach GETCALBINS (Kettenende) oder per tick_polling.
        """
        self._last_cal_state_az = 0.0
        self._last_cal_state_el = 0.0
        now = time.time()
        self._last_live_bins_az = now
        self._last_live_bins_el = now
        stats_ui = bool(self._statistics_window_open or self._settings_window_open)
        comp_az = bool(self._compass_window_open and self._compass_strom_heatmap_az)
        comp_el = bool(self._compass_window_open and self._compass_strom_heatmap_el)
        want_acc_az = False
        want_acc_el = False
        if self.enable_az:
            if stats_ui and (
                (not self._acc_bins_stats_initial_az) or self._acc_bins_stats_arm_after_setpos_az
            ):
                want_acc_az = True
            if comp_az and (
                (not self._acc_bins_compass_initial_az)
                or self._acc_bins_compass_arm_after_move_az
            ):
                want_acc_az = True
        if self.enable_el:
            if stats_ui and (
                (not self._acc_bins_stats_initial_el) or self._acc_bins_stats_arm_after_setpos_el
            ):
                want_acc_el = True
            if comp_el and (
                (not self._acc_bins_compass_initial_el)
                or self._acc_bins_compass_arm_after_move_el
            ):
                want_acc_el = True
        if want_acc_az or want_acc_el:
            self._last_acc_bins_az = 0.0
            self._last_acc_bins_el = 0.0
        if self.hw.is_connected():
            try:
                prio = 0  # Höchste Priorität, vor GETPOSDG
                if self.enable_az and not self._cal_bins_inflight_az:
                    self._poll_cal_state(self.slave_az, self.az, priority=prio)
                if self.enable_el and not self._cal_bins_inflight_el:
                    self._poll_cal_state(self.slave_el, self.el, priority=prio)
                if time.time() >= float(self._stats_cooldown_until or 0.0):
                    if want_acc_az and (not self._acc_bins_inflight_az):
                        self._fetch_acc_bins(self.slave_az, self.az, "AZ", priority=prio)
                    if want_acc_el and (not self._acc_bins_inflight_el):
                        self._fetch_acc_bins_el(self.slave_el, self.el, "EL", priority=prio)
            except Exception:
                pass

    def update_polling(self, polling_ms: dict):
        self._cfg_poll.update(polling_ms or {})
        # Positionsabfrage in ms (Intervall): größer = seltener. Sinnvolle Grenzen für RS485.
        try:
            pf = int(self._cfg_poll.get("pos_fast", 200))
            self._cfg_poll["pos_fast"] = int(max(50, min(pf, 2000)))
        except Exception:
            self._cfg_poll["pos_fast"] = 200
        # Keine harte Obergrenze mehr erzwingen: je nach Setup sollen Warn/Err/Telemetrie
        # bewusst nur alle ~2s abgefragt werden (und während Fahrt teils gar nicht).
        for k in ("warn", "err", "telemetry", "pwm", "ref_idle", "offline_timeout"):
            try:
                self._cfg_poll[k] = int(self._cfg_poll.get(k, 2000))
            except Exception:
                pass

    # -------------------- UI-Helfer (Direktkommandos) --------------------
    def build_line(self, dst: int, cmd: str, params: str) -> str:
        """RS485-Telegramm mit aktueller Master-ID erzeugen."""
        return build(int(self.master_id), int(dst), str(cmd).strip(), str(params))

    def broadcast_set_aselect(self, antenna_id_1_to_3: int) -> None:
        """Broadcast (DST 255): gewählte Antenne 1–3 melden (SETASELECT). Keine Antwort erwartet."""
        try:
            n = int(antenna_id_1_to_3)
            if n < 1 or n > 3:
                return
        except Exception:
            return
        line = build(int(self.master_id), int(BROADCAST_DST), "SETASELECT", str(n))
        # Direkt senden: Worker blockiert die Queue bei ausstehendem Poll-ACK
        self.hw.send_line_fire_and_forget(line)

    def note_setposcc_bus_activity(self) -> None:
        """SETPOSCC auf dem Bus (Mitschnitt): Idle-Polling bis Zeitstempel pausieren (je CC neu verlängert)."""
        self._idle_poll_defer_until = time.time() + _CC_IDLE_DEFER_S

    def note_setposdg_poll_restrict(self) -> None:
        """SETPOSDG an Rotor-Slave (gesendet oder mitgeschnitten): Zusatz-Polling wie bei Fahrt begrenzen.

        Setzt zusätzlich ``_idle_poll_defer_until`` auf dasselbe Fenster, damit während schneller
        Hotkey-Bursts (mehrere SETPOSDG kurz hintereinander) **keine** Idle-Zusatzabfragen
        (``GETWARN``, ``GETTEMPA/M``, ``GETWIND*``, ``GETPWM``, ``GETREF``, ``GETACCBINS`` …)
        zwischen die SETPOSDG/ACK rutschen und den Bus mit dem Rotor kreuzen. Ohne diesen Schutz
        verschwanden z. B. Windanzeigen im Kompass oder der Rotor verlor sein Ziel.
        """
        now = time.time()
        self._setposdg_poll_grace_until_ts = now + _SETPOSDG_POLL_GRACE_S
        try:
            prev = float(getattr(self, "_idle_poll_defer_until", 0.0) or 0.0)
        except Exception:
            prev = 0.0
        self._idle_poll_defer_until = max(prev, now + _SETPOSDG_POLL_GRACE_S)

    def _az_antenna_offset_deg(self, idx_0_to_2: int, cfg: Optional[dict] = None) -> float:
        """Versatz der Antenne idx (0..2) aus Achsen-State, sonst cfg-Fallback wie Kompass."""
        try:
            slot = int(idx_0_to_2) + 1
            if 1 <= slot <= 3:
                v = getattr(self.az, f"antoff{slot}", None)
                if v is not None:
                    return float(v)
        except Exception:
            pass
        if cfg is not None:
            try:
                offs = (cfg.get("ui") or {}).get("antenna_offsets_az", [0.0, 0.0, 0.0])
                i = max(0, min(2, int(idx_0_to_2)))
                return float(offs[i])
            except (IndexError, TypeError, ValueError):
                pass
        return 0.0

    def snap_az_soll_to_ist_for_antenna_switch(self) -> None:
        """Ohne Nachführen: lokales AZ-Soll = Ist (Rotor), Kompass-SETPOSCC löschen — kein Bus-Befehl."""
        if not self.enable_az:
            return
        try:
            now = time.time()
            cur = float(self.az.get_smoothed_pos_d10f(now)) / 10.0
        except Exception:
            try:
                cur = float(self.az.pos_d10) / 10.0
            except Exception:
                return
        d10 = int(round(cur * 10.0))
        self.az.compass_target_d10 = None
        self.az.target_d10 = d10
        self.az.last_set_sent_target_d10 = d10
        self.az.last_set_sent_ts = time.time()
        self.az.moving = False

    def align_az_bearing_after_antenna_switch(
        self, old_idx: int, new_idx: int, cfg: Optional[dict] = None
    ) -> None:
        """Mit ``controller_hw.antenna_realign_on_switch``: SETPOSDG, gleiche Luftpeilung wie zuvor.

        Ohne Nachführen: Soll = aktuelle Rotor-Istposition (Anzeige folgt der neu gewählten Antenne).

        Anzeige = Rotor + Versatz → rotor_soll = (rotor_ist + versatz_alt) − versatz_neu (mod 360).
        """
        if not cfg or not bool((cfg.get("controller_hw") or {}).get("antenna_realign_on_switch", False)):
            self.snap_az_soll_to_ist_for_antenna_switch()
            return
        try:
            oi = max(0, min(2, int(old_idx)))
            ni = max(0, min(2, int(new_idx)))
        except Exception:
            return
        if oi == ni:
            return
        if not self.enable_az:
            return
        O_old = self._az_antenna_offset_deg(oi, cfg)
        O_new = self._az_antenna_offset_deg(ni, cfg)
        if abs(O_old - O_new) < 1e-6:
            return
        try:
            now = time.time()
            cur = float(self.az.get_smoothed_pos_d10f(now)) / 10.0
        except Exception:
            try:
                cur = float(self.az.pos_d10) / 10.0
            except Exception:
                return
        D = wrap_deg(cur + O_old)
        rotor_target = wrap_deg(D - O_new)
        if abs(shortest_delta_deg(cur, rotor_target)) < 0.05:
            return
        self.set_az_deg(rotor_target, force=True)

    def broadcast_setconidf(self, new_controller_id: int) -> None:
        """Broadcast (DST 255): Controller-ID per SETCONIDF setzen. Keine Antwort erwartet."""
        try:
            n = int(new_controller_id)
            if n < 0 or n > 245:
                return
        except Exception:
            return
        line = build(int(self.master_id), int(BROADCAST_DST), "SETCONIDF", str(n))
        self.hw.send_line_fire_and_forget(line)

    def send_ui_command(
        self,
        dst: int,
        cmd: str,
        params: str,
        expect_prefix: Optional[str] = None,
        timeout_s: float = 0.8,
        priority: int = 0,
        on_done=None,
        apply_local_state: bool = True,
    ) -> None:
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
        # Daher: Für relevante Kommandos (SETPOSDG/SETPOSCC/STOP/SETREF) aktualisieren
        # wir den lokalen State SOFORT.
        if apply_local_state:
            self._apply_local_state_for_ui_command(int(dst), str(cmd).strip().upper(), str(params))

        cmd_u0 = str(cmd).strip().upper()
        if cmd_u0 == "SETPOSDG":
            try:
                d = int(dst)
                if (self.enable_az and d == int(self.slave_az)) or (
                    self.enable_el and d == int(self.slave_el)
                ):
                    self.note_setposdg_poll_restrict()
            except Exception:
                pass

        line = self.build_line(dst, cmd, params)
        # SETPOSCC (Kompass-Soll): Firmware sendet kein ACK — kein Pending, sonst Timeout und
        # eingehende ACKs (z. B. ACK_GET…) können vorübergehend fälschlich dem Request zugeordnet werden.
        if str(cmd).strip().upper() == "SETPOSCC":
            expect_prefix = None
        self.hw.send_request(
            HwRequest(
                line=line,
                expect_prefix=expect_prefix,
                timeout_s=float(timeout_s),
                on_done=on_done,
                priority=int(priority),
            )
        )

    @staticmethod
    def _ack_cmd_matches_expect(cmd: str, expect_prefix: str) -> bool:
        c = str(cmd or "").strip().upper()
        e = str(expect_prefix or "").strip().upper()
        if not e:
            return False
        if c.startswith(e):
            return True
        # z. B. ACK_CONTID statt ACK_GETCONTID
        if "GET" in e and e.startswith("ACK_"):
            alt = e.replace("GET", "", 1)
            if c.startswith(alt):
                return True
        if "SET" in e and e.startswith("ACK_"):
            alt = e.replace("SET", "", 1)
            if c.startswith(alt):
                return True
        return False

    def sync_ui_command_response(
        self,
        dst: int,
        cmd: str,
        params: str,
        expect_prefix: str,
        timeout_s: float = 5.0,
    ) -> Optional[str]:
        """Ein RS485-Kommando mit pending; blockiert den Qt-Thread (nur für UI).

        Rückgabe: tel.params bei passendem ACK, sonst None.

        Kein verschachteltes QEventLoop.exec(): on_done läuft im Reader-Thread; ein zweites
        QEventLoop.quit() über Threads hinweg ist in Qt/PySide fehleranfällig. Stattdessen
        pollt der GUI-Thread mit processEvents(), bis on_done das Ergebnis gesetzt hat.

        timeout_s ist mit HwRequest.timeout_s identisch: RS485/TCP kann >1 s pro Telegramm
        brauchen; bei 1,2 s kommt die RX-Zeile oft noch im Log, aber on_done war schon „timeout“.
        """
        from PySide6.QtCore import QEventLoop, QElapsedTimer
        from PySide6.QtWidgets import QApplication

        result: list[Optional[str]] = [None]
        done = [False]
        to = float(max(0.35, timeout_s))

        def on_done(tel: Optional[Telegram], err: Optional[str]) -> None:
            if err:
                pass
            elif str(cmd).strip().upper() == "SETPOSCC" and tel is None:
                # send_ui_command erzwingt kein ACK — sofortiger on_done(None, None) = Erfolg
                result[0] = ""
            elif tel and not err:
                cmd_u = str(tel.cmd or "").strip().upper()
                if cmd_u.startswith("NAK_"):
                    result[0] = SYNC_UI_NAK_PREFIX + str(tel.params)
                elif self._ack_cmd_matches_expect(cmd_u, expect_prefix):
                    result[0] = str(tel.params)
            done[0] = True

        self.send_ui_command(
            int(dst),
            cmd,
            params,
            expect_prefix=expect_prefix,
            timeout_s=to,
            priority=0,
            on_done=on_done,
            apply_local_state=False,
        )
        app = QApplication.instance()
        timer = QElapsedTimer()
        timer.start()
        timeout_ms = int(to * 1000.0)
        while not done[0]:
            if timer.elapsed() > timeout_ms:
                break
            time.sleep(0)  # GIL freigeben, damit der Reader-Thread on_done ausführen kann
            if app is not None:
                app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)
            else:
                time.sleep(0.005)
        return result[0]

    def _apply_local_state_for_ui_command(
        self, dst: int, cmd: str, params: str, *, from_bus_sniff: bool = False
    ) -> None:
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

            # -------------------- Kompass-Soll (Bus / Encoder-Panel, kein Motor-SET) --------------------
            if cmd == "SETPOSCC":
                # Kurz nach SETPOSDG: erstes SETPOSCC ignorieren (Bus-Reihenfolge / Echo).
                # Während Fahrt: SETPOSCC immer anwenden — Sollzeiger = Encoder (#2:…), Motorziel bleibt target_d10.
                # (Die Ignore-Zeit sonst: SETPOSDG-Mitschnitte verlängern sie oft → CC würde nie durchkommen.)
                try:
                    if not bool(getattr(axis, "moving", False)):
                        if time.time() < float(
                            getattr(axis, "setposcc_ignore_until_ts", 0.0) or 0.0
                        ):
                            return
                except Exception:
                    pass
                try:
                    v, _rid = parse_setposcc_params(str(params or ""))
                    if v is None:
                        d10 = None
                    else:
                        d10 = int(round(float(v) * 10.0))
                except Exception:
                    d10 = None
                if d10 is not None:
                    axis.compass_target_d10 = d10
                return

            # -------------------- Fahrziel setzen --------------------
            if cmd == "SETPOSDG":
                # params ist i.d.R. Grad als Float mit "," oder ".".
                try:
                    p = str(params).strip()
                    if ";" in p:
                        p = p.split(";")[-1]
                    p = p.replace(" ", "")
                    v = float(p.replace(",", "."))
                    d10 = int(round(v * 10.0))
                except Exception:
                    d10 = None

                # Bus-Mitschnitt kann SETPOSDG zum aktuellen Motorziel wiederholen (Echo/andere Master).
                # Dann compass_target_d10 (Encoder-Soll) nicht löschen — sonst springt der Sollzeiger zurück.
                # Gilt auch während Fahrt: Wiederhol-Echo trifft ein, während SETPOSCC noch ein neues Soll zeigt —
                # ohne diese Abfrage würde moving==True den Schutz umgehen und der Kompass aufs alte Ziel springen.
                if d10 is not None and from_bus_sniff:
                    try:
                        cc = getattr(axis, "compass_target_d10", None)
                        if (
                            int(axis.target_d10) == int(d10)
                            and cc is not None
                            and int(cc) != int(d10)
                        ):
                            return
                    except Exception:
                        pass

                self._abort_stats_fetch_and_cooldown()
                if d10 is not None:
                    axis.target_d10 = d10
                    axis.compass_target_d10 = None
                    axis.last_set_sent_target_d10 = d10
                    axis.last_set_sent_ts = time.time()
                    try:
                        axis.setposcc_ignore_until_ts = time.time() + _SETPOSCC_SUPPRESS_S
                    except Exception:
                        pass
                axis.moving = True
                return

        except Exception:
            # Keine harte Fehlerbehandlung: UI darf nicht wegen Status-Update abbrechen.
            return

    def set_az_deg(self, deg: float, force: bool = True) -> None:
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
            self.az.compass_target_d10 = None
            self.az.target_d10 = d10
            self._compass_manual_az_ts = time.time()
            self._send_setpos(self.slave_az, d10, axis="AZ")
            self.az.last_set_sent_target_d10 = d10
            self.az.last_set_sent_ts = time.time()
            self.az.moving = True
            try:
                self.az.setposcc_ignore_until_ts = time.time() + _SETPOSCC_SUPPRESS_S
            except Exception:
                pass
            return
        self.set_az_from_spid(d10)

    def set_el_deg(self, deg: float, force: bool = True) -> None:
        """EL-Zielwinkel in Grad setzen.

        Analog zu :meth:`set_az_deg`.

        - Wenn force=True, wird der Entprell-Mechanismus ("gleiches Ziel") umgangen.
        - Referenz-Check wird beibehalten.
        """
        d10 = int(round(float(deg) * 10.0))
        if force:
            if not self.enable_el:
                return
            self.el.compass_target_d10 = None
            self.el.target_d10 = d10
            self._compass_manual_el_ts = time.time()
            self._send_setpos(self.slave_el, d10, axis="EL")
            self.el.last_set_sent_target_d10 = d10
            self.el.last_set_sent_ts = time.time()
            self.el.moving = True
            try:
                self.el.setposcc_ignore_until_ts = time.time() + _SETPOSCC_SUPPRESS_S
            except Exception:
                pass
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
    def set_pos_from_spid(self, az_d10: int, el_d10: int):
        self.set_az_from_spid(az_d10)
        self.set_el_from_spid(el_d10)

    def set_az_from_spid(self, az_d10: int):
        """Zielposition von PstRotator (0,1°) für AZ.

        PstRotator sendet bei manchen Einstellungen SET ständig erneut.
        Wir senden SETPOSDG nur dann, wenn sich das Ziel wirklich geändert hat
        (und wenn wir nicht bereits am Ziel sind).
        Kompass-Manual-Eingabe hat 10s Vorrang (PST-SET wird ignoriert).
        """
        if (time.time() - self._compass_manual_az_ts) < 10.0:
            return
        self.az.compass_target_d10 = None
        self.az.target_d10 = az_d10

        if not self.enable_az:
            return

        # Wenn wir bereits am Ziel sind (Toleranz 0,1°) und nicht fahren -> nichts senden
        if (not self.az.moving) and (abs(self.az.pos_d10 - az_d10) <= 1):
            return

        # Gleiches Ziel wie zuletzt gesendet? -> nicht erneut senden
        if (
            self.az.last_set_sent_target_d10 is not None
            and az_d10 == self.az.last_set_sent_target_d10
        ):
            return

        if not self.az.referenced:
            self.log.write("WARN", "AZ SETPOSDG ignoriert: AZ nicht referenziert (Ziel gemerkt)")
            return

        self._send_setpos(self.slave_az, az_d10, axis="AZ")
        self.az.last_set_sent_target_d10 = az_d10
        self.az.last_set_sent_ts = time.time()
        self.az.moving = True
        try:
            self.az.setposcc_ignore_until_ts = time.time() + _SETPOSCC_SUPPRESS_S
        except Exception:
            pass

    def set_el_from_spid(self, el_d10: int):
        """Zielposition von PstRotator (0,1°) für EL.

        Siehe set_az_from_spid(): wir entprellen doppelte SET-Kommandos.
        Kompass-Manual-Eingabe hat 10s Vorrang.
        """
        if (time.time() - self._compass_manual_el_ts) < 10.0:
            return
        self.el.compass_target_d10 = None
        self.el.target_d10 = el_d10

        if not self.enable_el:
            return

        if (not self.el.moving) and (abs(self.el.pos_d10 - el_d10) <= 1):
            return

        if (
            self.el.last_set_sent_target_d10 is not None
            and el_d10 == self.el.last_set_sent_target_d10
        ):
            return

        if not self.el.referenced:
            self.log.write("WARN", "EL SETPOSDG ignoriert: EL nicht referenziert (Ziel gemerkt)")
            return

        self._send_setpos(self.slave_el, el_d10, axis="EL")
        self.el.last_set_sent_target_d10 = el_d10
        self.el.last_set_sent_ts = time.time()
        self.el.moving = True
        try:
            self.el.setposcc_ignore_until_ts = time.time() + _SETPOSCC_SUPPRESS_S
        except Exception:
            pass

    def stop_all(self):
        self.stop_az()
        self.stop_el()

    def hold_az_at_current_pos(self) -> None:
        """Statt STOP: SETPOSDG auf aktuelle Ist-Position (z. B. PST/HW-Stop), damit Ziel = Ist für Clients."""
        if not self.enable_az:
            return
        self._abort_stats_fetch_and_cooldown()
        try:
            d10 = int(self.az.pos_d10)
        except Exception:
            return
        self.az.compass_target_d10 = None
        self.az.target_d10 = d10
        self.az.last_set_sent_target_d10 = d10
        self.az.last_set_sent_ts = time.time()
        self.az.moving = False
        if not self.az.referenced:
            return
        self._send_setpos(self.slave_az, d10, axis="AZ")

    def hold_el_at_current_pos(self) -> None:
        """Wie hold_az_at_current_pos für EL."""
        if not self.enable_el:
            return
        self._abort_stats_fetch_and_cooldown()
        try:
            d10 = int(self.el.pos_d10)
        except Exception:
            return
        self.el.compass_target_d10 = None
        self.el.target_d10 = d10
        self.el.last_set_sent_target_d10 = d10
        self.el.last_set_sent_ts = time.time()
        self.el.moving = False
        if not self.el.referenced:
            return
        self._send_setpos(self.slave_el, d10, axis="EL")

    def hold_all_at_current_pos(self) -> None:
        """Beide Achsen: aktuelle Position als SETPOSDG (für PST-Stop von HW/Remote)."""
        self.hold_az_at_current_pos()
        self.hold_el_at_current_pos()

    def stop_az(self):
        if not self.enable_az:
            return
        self._abort_stats_fetch_and_cooldown()
        self._send_simple(self.slave_az, "STOP", "0", expect="ACK_STOP", prio=0)
        self.az.moving = False
        self.az.compass_target_d10 = None

    def stop_el(self):
        if not self.enable_el:
            return
        self._abort_stats_fetch_and_cooldown()
        self._send_simple(self.slave_el, "STOP", "0", expect="ACK_STOP", prio=0)
        self.el.moving = False
        self.el.compass_target_d10 = None

    def reference_all(self, start_homing: bool = True) -> None:
        """Referenziert alle aktiven Rotoren (AZ und/oder EL laut Config)."""
        if self.enable_az:
            self.reference_az(start_homing)
        if self.enable_el:
            self.reference_el(start_homing)

    def reference_az(self, start_homing: bool = True) -> None:
        """Referenziert nur AZ.

        moving und ref_poll_active werden erst nach erfolgreichem ACK_SETREF gesetzt.
        Bei Timeout oder NAK wird on_ref_start_failed aufgerufen (aus Hintergrundthread!).
        """
        if not self.enable_az:
            return
        v = "1" if start_homing else "0"
        try:
            self.az.compass_target_d10 = None
            self.az.target_d10 = 0
            self.az.last_set_sent_target_d10 = None
            self.az.last_set_sent_ts = 0.0
        except Exception:
            pass
        self.az.referenced = False
        # ref_poll_active und moving erst nach ACK setzen (nicht sofort)

        _start_homing = start_homing
        _axis_state = self.az
        _ctrl = self
        line = build(self.master_id, self.slave_az, "SETREF", v)

        def _on_done_az(tel: Optional[Telegram], err: Optional[str]) -> None:
            if err:
                _ctrl.log.write("WARN", f"AZ SETREF -> keine Antwort ({err})")
                _axis_state.ref_poll_active = False
                _axis_state.moving = False
                if callable(_ctrl.on_ref_start_failed):
                    try:
                        _ctrl.on_ref_start_failed("AZ")
                    except Exception:
                        pass
                return
            if tel and (tel.cmd.startswith("ACK_SETREF") or tel.cmd.startswith("ACK_REF")):
                _axis_state.ref_poll_active = True
                if _start_homing:
                    _axis_state.moving = True
            else:
                _ctrl.log.write(
                    "WARN", f"AZ SETREF -> NAK/unbekannte Antwort: {tel.cmd if tel else 'None'}"
                )
                _axis_state.ref_poll_active = False
                _axis_state.moving = False
                if callable(_ctrl.on_ref_start_failed):
                    try:
                        _ctrl.on_ref_start_failed("AZ")
                    except Exception:
                        pass

        self.hw.send_request(
            HwRequest(
                line=line,
                expect_prefix="ACK_SETREF",
                timeout_s=1.0,
                on_done=_on_done_az,
                priority=0,
            )
        )

    def reference_el(self, start_homing: bool = True) -> None:
        """Referenziert nur EL.

        moving und ref_poll_active werden erst nach erfolgreichem ACK_SETREF gesetzt.
        Bei Timeout oder NAK wird on_ref_start_failed aufgerufen (aus Hintergrundthread!).
        """
        if not self.enable_el:
            return
        v = "1" if start_homing else "0"
        try:
            self.el.compass_target_d10 = None
            self.el.target_d10 = 0
            self.el.last_set_sent_target_d10 = None
            self.el.last_set_sent_ts = 0.0
        except Exception:
            pass
        self.el.referenced = False
        # ref_poll_active und moving erst nach ACK setzen (nicht sofort)

        _start_homing = start_homing
        _axis_state = self.el
        _ctrl = self
        line = build(self.master_id, self.slave_el, "SETREF", v)

        def _on_done_el(tel: Optional[Telegram], err: Optional[str]) -> None:
            if err:
                _ctrl.log.write("WARN", f"EL SETREF -> keine Antwort ({err})")
                _axis_state.ref_poll_active = False
                _axis_state.moving = False
                if callable(_ctrl.on_ref_start_failed):
                    try:
                        _ctrl.on_ref_start_failed("EL")
                    except Exception:
                        pass
                return
            if tel and (tel.cmd.startswith("ACK_SETREF") or tel.cmd.startswith("ACK_REF")):
                _axis_state.ref_poll_active = True
                if _start_homing:
                    _axis_state.moving = True
            else:
                _ctrl.log.write(
                    "WARN", f"EL SETREF -> NAK/unbekannte Antwort: {tel.cmd if tel else 'None'}"
                )
                _axis_state.ref_poll_active = False
                _axis_state.moving = False
                if callable(_ctrl.on_ref_start_failed):
                    try:
                        _ctrl.on_ref_start_failed("EL")
                    except Exception:
                        pass

        self.hw.send_request(
            HwRequest(
                line=line,
                expect_prefix="ACK_SETREF",
                timeout_s=1.0,
                on_done=_on_done_el,
                priority=0,
            )
        )

    def clear_warnings_all(self):
        if self.enable_az:
            self._send_simple(self.slave_az, "DELWARN", "1", expect="ACK_DELWARN", prio=0)
        if self.enable_el:
            self._send_simple(self.slave_el, "DELWARN", "1", expect="ACK_DELWARN", prio=0)

    def set_pwm_all(self, pwm_pct: float):
        self.set_pwm_az(pwm_pct)
        self.set_pwm_el(pwm_pct)

    def _set_pwm(self, dst: int, axis_state: AxisState, pwm_pct: float, axis_label: str):
        pwm_pct = max(0.0, min(100.0, float(pwm_pct)))
        v = f"{pwm_pct:.1f}".replace(".", ",")
        self._send_simple(dst, "SETPWM", v, expect="ACK_SETPWM", prio=0)
        # Optimistisches UI-Update: GETPWM kommt nur alle ~2s.
        try:
            axis_state.telemetry.pwm_max_pct = float(pwm_pct)
        except Exception:
            pass

    def set_pwm_az(self, pwm_pct: float):
        if not self.enable_az:
            return
        self._set_pwm(self.slave_az, self.az, pwm_pct, "AZ")

    def set_pwm_el(self, pwm_pct: float):
        if not self.enable_el:
            return
        self._set_pwm(self.slave_el, self.el, pwm_pct, "EL")

