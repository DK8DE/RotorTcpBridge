"""Polling, Telemetrie/Wind und Bins-Fetch für RotorController."""

from __future__ import annotations

import time
from typing import Any, Callable, Optional

from .rs485_protocol import build, Telegram
from .hardware_client import HardwareClient, HwRequest
from .rotor_model import AxisState
from .rotor_parse_utils import parse_int


def bins_block_looks_complete(vals: list[int]) -> bool:
    """12 Strom-Werte je Block: bei Buslast liefert die Firmware oft Null-Padding (siehe Log).

    Ohne diese Prüfung landen Nullen in der Heatmap und die Auto-Farbskala wird unbrauchbar.
    """
    if len(vals) != 12:
        return False
    hi = max(vals)
    if hi == 0:
        return True
    leading = 0
    for v in vals:
        if v == 0:
            leading += 1
        else:
            break
    if leading >= 6 and hi > 50:
        return False
    trailing = 0
    for v in reversed(vals):
        if v == 0:
            trailing += 1
        else:
            break
    if trailing >= 3 and hi > 50:
        return False
    for i in range(1, 11):
        if vals[i] == 0 and vals[i - 1] > 50 and vals[i + 1] > 50:
            return False
    if sum(1 for v in vals if v == 0) >= 6 and hi > 50:
        return False
    return True


def merge_strom_bin_block(
    bins: list[int],
    parts: list[str],
    start_val: int,
    count_val: int,
) -> tuple[bool, bool]:
    """Schreibt Rohwerte in ``bins`` [0..71].

    Rückgabe ``(success, plausible)``: success=False nur bei Parse-Fehler.
    plausible=False = Null-Padding/Lücken (trotzdem geschrieben, UI soll Daten sehen).
    """
    if count_val < 1 or count_val > 12 or len(parts) < 3 + count_val:
        return False, False
    vals: list[int] = []
    for i in range(count_val):
        v = parse_int(parts[3 + i])
        if v is None:
            return False, False
        vals.append(int(v))
    plausible = bins_block_looks_complete(vals)
    for i, v in enumerate(vals):
        j = start_val + i
        if 0 <= j < 72:
            bins[j] = v
    return True, plausible


# Mindestabstand zwischen GETACCBINS-Rundenstarts (s), solange noch „komplett werden“ nötig ist.
_ACC_BINS_REFETCH_GAP_S = 0.12


class _RotorPollingHost:
    """Nur Typannotationen: Instanzattribute setzt ``RotorController.__init__`` (Mixin-Kombination)."""

    hw: HardwareClient
    az: AxisState
    el: AxisState
    master_id: int
    slave_az: int
    slave_el: int
    enable_az: bool
    enable_el: bool
    log: Any
    wind_enabled: bool
    wind_enabled_known: bool
    _cfg_poll: dict[str, int]
    _statistics_window_open: bool
    _settings_window_open: bool
    _compass_window_open: bool
    _compass_strom_heatmap_az: bool
    _compass_strom_heatmap_el: bool
    _stats_cooldown_until: float
    _acc_bins_compass_initial_az: bool
    _acc_bins_compass_initial_el: bool
    _acc_bins_compass_arm_after_move_az: bool
    _acc_bins_compass_arm_after_move_el: bool
    _acc_bins_stats_initial_az: bool
    _acc_bins_stats_initial_el: bool
    _acc_bins_stats_arm_after_setpos_az: bool
    _acc_bins_stats_arm_after_setpos_el: bool
    _hw_prev_connected: bool
    _startup_burst_until: float
    _last_poll: float
    _last_warn: float
    _last_tel: float
    _last_wind: float
    _last_wind_dir: float
    _last_wind_beaufort: float
    _wind_dir_due_ts: float
    _wind_beaufort_due_ts: float
    _wind_speed_inflight: bool
    _wind_speed_sent_ts: float
    _wind_dir_inflight: bool
    _wind_dir_sent_ts: float
    _wind_beaufort_inflight: bool
    _wind_beaufort_sent_ts: float
    _last_pwm: float
    _last_minpwm: float
    _last_ref_idle: float
    _last_ref_active_az: float
    _last_ref_active_el: float
    _last_cal_state_az: float
    _last_cal_state_el: float
    _cal_bins_inflight_az: bool
    _cal_bins_fetched_az: bool
    _cal_bins_received_az: int
    _last_live_bins_az: float
    _live_bins_inflight_az: bool
    _live_bins_received_az: int
    _cal_bins_temp_cw: list[Any] | None
    _cal_bins_temp_ccw: list[Any] | None
    _live_bins_temp_cw: list[Any] | None
    _live_bins_temp_ccw: list[Any] | None
    _last_acc_bins_az: float
    _acc_bins_inflight_az: bool
    _acc_bins_temp_cw: list[Any] | None
    _acc_bins_temp_ccw: list[Any] | None
    _cal_bins_inflight_el: bool
    _cal_bins_fetched_el: bool
    _cal_bins_temp_cw_el: list[Any] | None
    _cal_bins_temp_ccw_el: list[Any] | None
    _last_live_bins_el: float
    _live_bins_inflight_el: bool
    _live_bins_temp_cw_el: list[Any] | None
    _live_bins_temp_ccw_el: list[Any] | None
    _last_acc_bins_el: float
    _acc_bins_inflight_el: bool
    _acc_bins_temp_cw_el: list[Any] | None
    _acc_bins_temp_ccw_el: list[Any] | None
    _last_wind_enable_poll: float
    _wind_enable_inflight: bool
    _wind_enable_sent_ts: float
    _cal_bins_priority_az: int
    _cal_bins_priority_el: int
    _live_bins_priority_az: int
    _live_bins_priority_el: int
    _acc_bins_priority_az: int
    _acc_bins_priority_el: int
    _acc_bins_block_mask_az: list[bool] | None
    _acc_bins_block_mask_el: list[bool] | None
    _acc_bins_last_tx_ts_az: float
    _acc_bins_last_tx_ts_el: float
    _acc_bins_finalize_pending_az: bool
    _acc_bins_finalize_pending_el: bool
    _acc_bins_finalize_until_az: float
    _acc_bins_finalize_until_el: float
    request_antenna_offsets: Callable[[], None]
    request_antenna_angles: Callable[[], None]
    # Von ``RotorController`` / Polling-Mixin; für ``RotorControllerAsyncMixin`` (gleicher Host-Stub)
    _apply_local_state_for_ui_command: Callable[..., None]
    set_az_from_spid: Callable[[int], None]
    set_el_from_spid: Callable[[int], None]
    note_setposcc_bus_activity: Callable[[], None]
    note_setposdg_poll_restrict: Callable[[], None]
    _setposdg_poll_grace_until_ts: float
    _acc_bins_chain_in_progress: Callable[[], bool]
    _fetch_cal_bins: Callable[..., None]
    _fetch_live_bins: Callable[..., None]
    _fetch_cal_bins_el: Callable[..., None]
    _fetch_live_bins_el: Callable[..., None]
    _async_reconcile_cal_bins_ack_az: Callable[..., None]
    _async_reconcile_cal_bins_ack_el: Callable[..., None]
    _async_reconcile_live_bins_ack_az: Callable[..., None]
    _async_reconcile_live_bins_ack_el: Callable[..., None]
    _async_merge_acc_bins_ack_az: Callable[..., None]
    _async_merge_acc_bins_ack_el: Callable[..., None]


class RotorControllerPollingMixin(_RotorPollingHost):
    """Polling-Logik: ``tick_polling``, ``_poll_*``, sequentielle Bins-Abfragen."""

    def _acc_bins_chain_in_progress(self) -> bool:
        """True, solange eine GETACCBINS-Kette (12 Blöcke) für AZ und/oder EL läuft.

        Keine weiteren Polls einreihen: Die TX-Queue würde sonst voll, und z. B. GETREF
        (prio 2) kann vor dem nächsten ACC-Block (prio 6) senden — verspätete ACC-ACKs
        wirken wie Timeouts.

        Nur laufende Blöcke (inflight), nicht die kurze Abschluss-Wartezeit: sonst fehlen
        Wind-Telegramme zu lange und der Kompass blendet den Windpfeil aus.
        """
        try:
            return bool(
                getattr(self, "_acc_bins_inflight_az", False)
                or getattr(self, "_acc_bins_inflight_el", False)
            )
        except Exception:
            return False

    def _acc_bins_poll_enabled(self) -> bool:
        """True, wenn GETACCBINS grundsätzlich erlaubt ist (UI-Kontext).

        Der eigentliche Abrufrhythmus steuert ``tick_polling`` (einmal komplett, dann nur nach SETPOSDG).
        """
        if bool(getattr(self, "_statistics_window_open", False)):
            return True
        if bool(getattr(self, "_settings_window_open", False)):
            return True
        if bool(getattr(self, "_compass_window_open", False)) and (
            bool(getattr(self, "_compass_strom_heatmap_az", False))
            or bool(getattr(self, "_compass_strom_heatmap_el", False))
        ):
            return True
        return False

    def _acc_bins_strom_live(self) -> bool:
        """Kompass offen und Strom-Heatmap aktiv (Haken). Hinweis: GETACCBINS-Takt steuert ``tick_polling``."""
        return bool(getattr(self, "_compass_window_open", False)) and (
            bool(getattr(self, "_compass_strom_heatmap_az", False))
            or bool(getattr(self, "_compass_strom_heatmap_el", False))
        )

    # -------------------- Polling --------------------
    def tick_polling(self: RotorControllerPollingMixin) -> None:
        now = time.time()
        pos_fast_s = self._cfg_poll["pos_fast"] / 1000.0
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
            # Antennenwerte zurücksetzen → Kompassfenster-Timer erkennt fehlende Werte
            self.az.antoff1 = None
            self.az.antoff2 = None
            self.az.antoff3 = None
            self.az.angle1 = None
            self.az.angle2 = None
            self.az.angle3 = None

            # Sofortige Erstabfrage (damit UI direkt gefüllt wird):
            # Position + PWM + MINPWM + REF + Warn + Temp (Fehler: Broadcast ERR, kein GETERR)
            # GETWINDENABLE wird vom Idle-Polling-Block übernommen (Inflight-Guard verhindert Doppelabfrage)
            try:
                if self.enable_az:
                    self._poll_pos(self.slave_az, self.az, "AZ", now, expected_period_s=pos_fast_s)
                    self._poll_pwm(self.slave_az, self.az, "AZ")
                    self._poll_minpwm(self.slave_az, self.az, "AZ")
                    self._poll_ref(self.slave_az, self.az, "AZ")
                    self._poll_warn(self.slave_az, self.az, "AZ")
                    self._poll_idle_telemetry(self.slave_az, self.az, "AZ")
                    self._poll_idle_wind(self.slave_az, self.az, "AZ")
                if self.enable_el:
                    self._poll_pos(self.slave_el, self.el, "EL", now, expected_period_s=pos_fast_s)
                    self._poll_pwm(self.slave_el, self.el, "EL")
                    self._poll_minpwm(self.slave_el, self.el, "EL")
                    self._poll_ref(self.slave_el, self.el, "EL")
                    self._poll_warn(self.slave_el, self.el, "EL")
                    self._poll_idle_telemetry(self.slave_el, self.el, "EL")
                self.request_antenna_offsets()
                self.request_antenna_angles()
            except Exception:
                pass
        self._hw_prev_connected = hw_on

        pos_slow_s = self._cfg_poll["pos_slow"] / 1000.0
        warn_s = self._cfg_poll["warn"] / 1000.0
        pwm_s = self._cfg_poll["pwm"] / 1000.0
        minpwm_s = self._cfg_poll["minpwm"] / 1000.0
        tel_s = self._cfg_poll["telemetry"] / 1000.0
        ref_s = self._cfg_poll["ref"] / 1000.0
        ref_idle_s = self._cfg_poll["ref_idle"] / 1000.0
        windenable_s = self._cfg_poll["windenable"] / 1000.0
        offline_timeout_s = self._cfg_poll["offline_timeout"] / 1000.0

        # Dynamisches Polling:
        # - Fahrt  : nur GETPOSDG (pos_fast, Standard 5 Hz) → Bus frei für Position
        # - Idle   : GETPOSDG (10 s) + WARN/PWM/TEMP/MINPWM (10 s)
        #            + GETREF/GETWINDENABLE (5–10 s) + Wind (2 s)
        moving = bool(
            self.az.moving or self.el.moving or self.az.ref_poll_active or self.el.ref_poll_active
        )
        try:
            grace_u = float(getattr(self, "_setposdg_poll_grace_until_ts", 0.0) or 0.0)
        except Exception:
            grace_u = 0.0
        # Wie „Fahrt“: nur GETPOSDG, solange Achse fährt/referenziert oder kurz nach SETPOSDG-Mitschnitt.
        poll_restrict = bool(moving or (now < grace_u))

        # GETACCBINS: Abschlussprüfung auch ohne hw_on (sonst hängt Inflight bei Disconnect).
        self._tick_acc_bins_finalize_rounds(now)

        if hw_on:
            # Inflight-Sperren nach Request-Timeout freigeben (verhindert dauerhaftes Blockieren).
            if self._wind_enable_inflight and (
                (now - self._wind_enable_sent_ts) > 1.5
            ):  # now = time.time()
                self._wind_enable_inflight = False
            if self._wind_speed_inflight and ((now - float(self._wind_speed_sent_ts or 0.0)) > 0.9):
                self._wind_speed_inflight = False
            if self._wind_dir_inflight and ((now - float(self._wind_dir_sent_ts or 0.0)) > 0.9):
                self._wind_dir_inflight = False
            if self._wind_beaufort_inflight and (
                (now - float(self._wind_beaufort_sent_ts or 0.0)) > 0.9
            ):
                self._wind_beaufort_inflight = False

            pos_period = pos_fast_s if poll_restrict else pos_slow_s
            # In den ersten Sekunden nach Connect einmal schneller pollen, damit Werte "schnappen"
            if now < float(self._startup_burst_until or 0.0):
                pos_period = min(pos_period, pos_fast_s)
            # Im Stand: während SETPOSCC-Strom kein GETPOSDG (sonst Bus-Kollisionen mit Encoder).
            _defer_u = float(getattr(self, "_idle_poll_defer_until", 0.0) or 0.0)
            skip_pos_for_cc = (not moving) and (now < _defer_u)
            acc_chain = self._acc_bins_chain_in_progress()
            if (
                (not skip_pos_for_cc)
                and (now - self._last_poll >= pos_period)
                and (not acc_chain)
            ):
                sent_any = False
                if self.enable_az:
                    sent_any = (
                        self._poll_pos(
                            self.slave_az, self.az, "AZ", now, expected_period_s=pos_period
                        )
                        or sent_any
                    )
                if self.enable_el:
                    sent_any = (
                        self._poll_pos(
                            self.slave_el, self.el, "EL", now, expected_period_s=pos_period
                        )
                        or sent_any
                    )
                # Nur wenn wirklich gesendet wurde, Zeitstempel fortschreiben.
                if sent_any:
                    self._last_poll = now

            # Während Bewegung: NUR Position (schnell). Kein WARN, keine Telemetrie, kein Wind, kein PWM.
            # Idle-Zusatzabfragen bei SETPOSCC-Strom kurz aussetzen (Bus frei für Encoder/GETPOSDG).
            # Während GETACCBINS-Kette ebenfalls aussetzen (sonst stauen andere Telegramme und
            # überholen per Priorität den nächsten ACC-Block → falsche Timeouts).
            if (
                not poll_restrict
                and now >= float(getattr(self, "_idle_poll_defer_until", 0.0) or 0.0)
                and (not acc_chain)
            ):
                # Idle: alle Zusatzabfragen – während Fahrt / SETPOSDG-Grace nur GETPOSDG.

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
                    wind_unknown_retry = not self.wind_enabled_known and (
                        now - self._last_wind_enable_poll >= 3.0
                    )
                    wind_known_repoll = self.wind_enabled_known and (
                        now - self._last_wind_enable_poll >= windenable_s
                    )
                    if wind_unknown_retry or wind_known_repoll:
                        self._poll_wind_enable(self.slave_az, self.az, "AZ")

                # GETCALSTATE/LIVE wenn Statistik- oder Einstellungsfenster offen
                if self._statistics_window_open or self._settings_window_open:
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

                # GETACCBINS: einmal vollständig bei Statistik/Kompass-Strom, danach nur nach SETPOSDG.
                if now >= self._stats_cooldown_until:
                    gap = float(_ACC_BINS_REFETCH_GAP_S)
                    stats_ui = bool(self._statistics_window_open or self._settings_window_open)
                    comp_az = bool(self._compass_window_open and self._compass_strom_heatmap_az)
                    comp_el = bool(self._compass_window_open and self._compass_strom_heatmap_el)
                    if self.enable_az and (not self._acc_bins_inflight_az):
                        want_az = False
                        if stats_ui and (
                            (not self._acc_bins_stats_initial_az)
                            or self._acc_bins_stats_arm_after_setpos_az
                        ):
                            want_az = True
                        if comp_az and (
                            (not self._acc_bins_compass_initial_az)
                            or self._acc_bins_compass_arm_after_move_az
                        ):
                            want_az = True
                        if want_az and (now - self._last_acc_bins_az >= gap):
                            self._last_acc_bins_az = now
                            self._fetch_acc_bins(self.slave_az, self.az, "AZ")
                    if self.enable_el and (not self._acc_bins_inflight_el):
                        want_el = False
                        if stats_ui and (
                            (not self._acc_bins_stats_initial_el)
                            or self._acc_bins_stats_arm_after_setpos_el
                        ):
                            want_el = True
                        if comp_el and (
                            (not self._acc_bins_compass_initial_el)
                            or self._acc_bins_compass_arm_after_move_el
                        ):
                            want_el = True
                        if want_el and (now - self._last_acc_bins_el >= gap):
                            self._last_acc_bins_el = now
                            self._fetch_acc_bins_el(self.slave_el, self.el, "EL")

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

            # Wind auch während GETACCBINS: GETANEMO/GETWINDDIR/GETBEAUFORT haben prio 2, kein Pending —
            # blockiert ACC nicht, hält aber Telemetrie für den Kompass (Windpfeil) aktuell.
            if (not poll_restrict) and now >= float(
                getattr(self, "_idle_poll_defer_until", 0.0) or 0.0
            ):
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

            # Referenzfahrt: GETREF unabhängig von Positions-ACKs pollen
            if (
                self.enable_az
                and self.az.ref_poll_active
                and (now - self._last_ref_active_az) >= ref_s
            ):
                self._last_ref_active_az = now
                self._poll_ref(self.slave_az, self.az, "AZ")
            if (
                self.enable_el
                and self.el.ref_poll_active
                and (now - self._last_ref_active_el) >= ref_s
            ):
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
    def _send_simple(self, dst: int, cmd: str, params: str, expect: str | None, prio: int = 5):
        line = build(self.master_id, dst, cmd, params)

        def done(tel: Optional[Telegram], err: Optional[str]):
            if err:
                self.log.write("WARN", f"{cmd} -> keine Antwort ({err})")
                return
            if tel and not tel.ok:
                self.log.write("WARN", f"{cmd} -> CS falsch: {tel}")

        self.hw.send_request(
            HwRequest(line=line, expect_prefix=expect, timeout_s=0.8, on_done=done, priority=prio)
        )

    def _abort_stats_fetch_and_cooldown(self) -> None:
        """Laufende GETACCBINS-Kette abbrechen; kurzer Cooldown, damit der Worker die Queue leeren kann."""
        self._abort_acc_bins_fetch_only()
        self._stats_cooldown_until = time.time() + 0.25

    def _abort_acc_bins_fetch_only(self) -> None:
        """Laufende GETACCBINS-Kette abbrechen (ohne Cooldown): Haken aus, Kompass zu."""
        self._acc_bins_inflight_az = False
        self._acc_bins_inflight_el = False
        self._acc_bins_temp_cw = None
        self._acc_bins_temp_ccw = None
        self._acc_bins_temp_cw_el = None
        self._acc_bins_temp_ccw_el = None
        self._acc_bins_block_mask_az = None
        self._acc_bins_block_mask_el = None
        self._acc_bins_finalize_pending_az = False
        self._acc_bins_finalize_pending_el = False

    def _send_setpos(self, dst: int, d10: int, axis: str, retry_count: int = 0):
        """SETPOSDG senden. Bei fehlendem ACK nach ~250ms automatisch einmal erneut versuchen.

        Retry wegen möglicher RS485-Kollisionen; Verbindung bleibt bei Timeout erhalten.
        """
        try:
            self.note_setposdg_poll_restrict()
        except Exception:
            pass
        self._abort_stats_fetch_and_cooldown()
        axu = str(axis).upper()
        if axu == "AZ":
            self._acc_bins_compass_arm_after_move_az = True
            self._acc_bins_stats_arm_after_setpos_az = True
        elif axu == "EL":
            self._acc_bins_compass_arm_after_move_el = True
            self._acc_bins_stats_arm_after_setpos_el = True
        deg = d10 / 10.0
        params = f"{deg:.2f}".replace(".", ",")
        line = build(self.master_id, dst, "SETPOSDG", params)

        def done(tel: Optional[Telegram], err: Optional[str]):
            if err:
                if retry_count < 1:
                    self.log.write("INFO", f"{axis} SETPOSDG kein ACK ({err}), Retry...")
                    self._send_setpos(dst, d10, axis, retry_count=1)
                else:
                    self.log.write("WARN", f"{axis} SETPOSDG keine Antwort nach Retry ({err})")
                return
            if tel and tel.cmd.startswith("NAK_SETPOSDG"):
                self.log.write("WARN", f"{axis} SETPOSDG NAK: {tel.params}")

        self.hw.send_request(
            HwRequest(
                line=line,
                expect_prefix="ACK_SETPOSDG",
                timeout_s=0.25,
                on_done=done,
                priority=0,
                dont_disconnect_on_timeout=True,
            )
        )

    # -------------------- Poll helpers --------------------
    def _poll_pos(
        self, dst: int, axis_state: AxisState, axis: str, now_ts: float, expected_period_s: float
    ) -> bool:
        """GETPOSDG senden — ohne Warten auf ein voriges ACK.

        Der Hardware-Client setzt bei ``expect_prefix=None`` kein ``_pending``; Antworten
        laufen asynchron über ``on_async_telegram``. Früheres Inflight-Gating verzögerte
        bei verspäteten ACKs den nächsten Send und wirkte wie Ruckeln.
        """
        _ = now_ts
        try:
            axis_state.pos_poll_expected_period_s = float(max(0.05, expected_period_s))
        except Exception:
            axis_state.pos_poll_expected_period_s = 0.2

        line = build(self.master_id, dst, "GETPOSDG", "0")
        self.hw.send_request(
            HwRequest(line=line, expect_prefix=None, timeout_s=0.8, on_done=None, priority=5)
        )
        return True

    def _poll_warn(self, dst: int, axis_state: AxisState, axis: str):
        # WICHTIG: Warn-/Error-/Telemetrie-Polls dürfen die Positionsanzeige nicht ausbremsen.
        # Daher: ohne expect_prefix senden (kein "pending"), Antworten kommen asynchron rein
        # und werden in _on_async_tel verarbeitet.
        line = build(self.master_id, dst, "GETWARN", "0")
        self.hw.send_request(
            HwRequest(line=line, expect_prefix=None, timeout_s=0.8, on_done=None, priority=5)
        )

    def _poll_ref(self, dst: int, axis_state: AxisState, axis: str):
        # WICHTIG: GETREF darf die restlichen Polls (Pos/Err/Warn/Telemetrie) nicht blockieren.
        # Daher ohne pending senden; Antwort wird in _on_async_tel verarbeitet.
        line = build(self.master_id, dst, "GETREF", "0")
        self.hw.send_request(
            HwRequest(line=line, expect_prefix=None, timeout_s=0.8, on_done=None, priority=2)
        )

    def _poll_telemetry(self, dst: int, axis_state: AxisState, axis: str):
        # Telemetrie ist niedrige Priorität; außerdem ohne pending (siehe _poll_warn),
        # Verarbeitung erfolgt in _on_async_tel.
        self.hw.send_request(
            HwRequest(
                line=build(self.master_id, dst, "GETTEMPA", "0"),
                expect_prefix=None,
                timeout_s=0.8,
                on_done=None,
                priority=6,
            )
        )
        self.hw.send_request(
            HwRequest(
                line=build(self.master_id, dst, "GETTEMPM", "0"),
                expect_prefix=None,
                timeout_s=0.8,
                on_done=None,
                priority=6,
            )
        )
        self._poll_idle_wind(dst, axis_state, axis)
        # PWM wird separat über _poll_pwm() abgefragt (max. alle ~2s).

    def _poll_idle_telemetry(self, dst: int, axis_state: AxisState, axis: str):
        """Telemetrie, die im Idle abgefragt werden darf (Temp/Wind).

        Während der Fahrt bewusst NICHT pollen, um die Positionsanzeige nicht auszubremsen.
        """
        self.hw.send_request(
            HwRequest(
                line=build(self.master_id, dst, "GETTEMPA", "0"),
                expect_prefix=None,
                timeout_s=0.8,
                on_done=None,
                priority=6,
            )
        )
        self.hw.send_request(
            HwRequest(
                line=build(self.master_id, dst, "GETTEMPM", "0"),
                expect_prefix=None,
                timeout_s=0.8,
                on_done=None,
                priority=6,
            )
        )
        # Wind wird separat mit eigener Taktung/Prio gepollt (siehe _poll_idle_wind()).

    def _poll_idle_wind(self, dst: int, axis_state: AxisState, axis: str):
        """Windgeschwindigkeit im Idle pollen (AZ)."""
        # Winddaten kommen ausschließlich vom AZ-Rotor.
        if int(dst) != int(self.slave_az) or (not self.wind_enabled):
            return
        self._wind_speed_inflight = True
        self._wind_speed_sent_ts = time.time()
        self.hw.send_request(
            HwRequest(
                line=build(self.master_id, dst, "GETANEMO", "0"),
                expect_prefix=None,
                timeout_s=0.8,
                on_done=None,
                priority=2,
            )
        )

    def _poll_idle_wind_dir(self, dst: int, axis_state: AxisState, axis: str):
        """Windrichtung im Idle pollen (AZ), zeitversetzt zu GETANEMO."""
        if int(dst) != int(self.slave_az) or (not self.wind_enabled):
            return
        self._wind_dir_inflight = True
        self._wind_dir_sent_ts = time.time()
        self.hw.send_request(
            HwRequest(
                line=build(self.master_id, dst, "GETWINDDIR", "0"),
                expect_prefix=None,
                timeout_s=0.8,
                on_done=None,
                priority=2,
            )
        )

    def _poll_idle_wind_beaufort(self, dst: int, axis_state: AxisState, axis: str):
        """Windstärke in Beaufort (0–12) im Idle pollen (AZ)."""
        if int(dst) != int(self.slave_az) or (not self.wind_enabled):
            return
        self._wind_beaufort_inflight = True
        self._wind_beaufort_sent_ts = time.time()
        self.hw.send_request(
            HwRequest(
                line=build(self.master_id, dst, "GETBEAUFORT", "0"),
                expect_prefix=None,
                timeout_s=0.8,
                on_done=None,
                priority=2,
            )
        )

    def _poll_wind_enable(self, dst: int, axis_state: AxisState, axis: str):
        """Abfragen, ob Windsensor vorhanden ist (GETWINDENABLE). Inflight-Guard verhindert Doppelabfrage."""
        if int(dst) != int(self.slave_az):
            return
        if self._wind_enable_inflight:
            return
        self._wind_enable_inflight = True
        self._wind_enable_sent_ts = (
            time.time()
        )  # muss time.time() sein – tick nutzt time.time() als 'now'
        self._last_wind_enable_poll = (
            time.time()
        )  # muss time.time() sein – tick nutzt time.time() als 'now'
        self.hw.send_request(
            HwRequest(
                line=build(self.master_id, dst, "GETWINDENABLE", "0"),
                expect_prefix=None,
                timeout_s=0.8,
                on_done=None,
                priority=3,
            )
        )

    def _poll_pwm(self, dst: int, axis_state: AxisState, axis: str):
        """PWM-Status abfragen (immer nur alle ~2s)."""
        self.hw.send_request(
            HwRequest(
                line=build(self.master_id, dst, "GETPWM", "0"),
                expect_prefix=None,
                timeout_s=0.8,
                on_done=None,
                priority=6,
            )
        )

    def _poll_minpwm(self, dst: int, axis_state: AxisState, axis: str):
        """MINPWM abfragen (Untergrenze für PWM)."""
        self.hw.send_request(
            HwRequest(
                line=build(self.master_id, dst, "GETMINPWM", "0"),
                expect_prefix=None,
                timeout_s=0.8,
                on_done=None,
                priority=6,
            )
        )

    def _poll_cal_state(self, dst: int, axis_state: AxisState, priority: int = 5) -> None:
        """GETCALSTATE abfragen (state;progress). state: 0=IDLE,1=RUNNING,2=DONE,3=ABORT."""
        self.hw.send_request(
            HwRequest(
                line=build(self.master_id, dst, "GETCALSTATE", "0"),
                expect_prefix=None,
                timeout_s=0.8,
                on_done=None,
                priority=priority,
            )
        )

    def _bins_block_idx_from_tel(self, tel: Optional[Telegram]) -> Optional[int]:
        """DIR/START aus ACK-Params → Index in _CAL_LIVE_BLOCKS (CAL/LIVE/ACC gleiches Raster)."""
        if tel is None:
            return None
        parts = (tel.params or "").strip().split(";")
        if len(parts) < 2:
            return None
        dir_val = parse_int(parts[0])
        start_val = parse_int(parts[1])
        if dir_val is None or start_val is None:
            return None
        for i, (d, s) in enumerate(self._CAL_LIVE_BLOCKS):
            if d == dir_val and s == start_val:
                return i
        return None

    def _tick_acc_bins_finalize_rounds(self, now: float) -> None:
        """Nach letztem GETACCBINS-TX mindestens 1s warten, dann Vollständigkeit per Block-Maske prüfen."""
        if getattr(self, "_acc_bins_finalize_pending_az", False) and now >= float(
            getattr(self, "_acc_bins_finalize_until_az", 0.0) or 0.0
        ):
            self._acc_bins_finalize_pending_az = False
            self._finish_acc_bins_round_az()
        if getattr(self, "_acc_bins_finalize_pending_el", False) and now >= float(
            getattr(self, "_acc_bins_finalize_until_el", 0.0) or 0.0
        ):
            self._acc_bins_finalize_pending_el = False
            self._finish_acc_bins_round_el()

    def _arm_acc_bins_finalize_az(self) -> None:
        t = time.time()
        last_tx = float(getattr(self, "_acc_bins_last_tx_ts_az", 0.0) or 0.0)
        gap = float(getattr(self, "_ACC_BINS_FINALIZE_GAP_S", 1.0) or 1.0)
        self._acc_bins_finalize_until_az = max(t, last_tx + gap)
        self._acc_bins_finalize_pending_az = True

    def _arm_acc_bins_finalize_el(self) -> None:
        t = time.time()
        last_tx = float(getattr(self, "_acc_bins_last_tx_ts_el", 0.0) or 0.0)
        gap = float(getattr(self, "_ACC_BINS_FINALIZE_GAP_S", 1.0) or 1.0)
        self._acc_bins_finalize_until_el = max(t, last_tx + gap)
        self._acc_bins_finalize_pending_el = True

    def _acc_bins_blend_incomplete(
        self,
        axis_state: AxisState,
        temp_cw: list[Any],
        temp_ccw: list[Any],
        mask: Optional[list[bool]],
    ) -> tuple[list[int], list[int]]:
        """Fehlende GETACCBINS-Blöcke: Werte aus vorheriger Runde übernehmen, sonst 0 (Heatmap bleibt konsistent)."""
        prev_cw = getattr(axis_state, "acc_bins_cw", None)
        prev_ccw = getattr(axis_state, "acc_bins_ccw", None)
        out_cw: list[int] = (
            [int(x) for x in prev_cw[:72]] if (prev_cw is not None and len(prev_cw) >= 72) else [0] * 72
        )
        out_ccw: list[int] = (
            [int(x) for x in prev_ccw[:72]]
            if (prev_ccw is not None and len(prev_ccw) >= 72)
            else [0] * 72
        )
        nblk = len(self._CAL_LIVE_BLOCKS)
        m = (
            list(mask)
            if isinstance(mask, list) and len(mask) == nblk
            else [False] * nblk
        )
        for i, (d, s) in enumerate(self._CAL_LIVE_BLOCKS):
            if not m[i]:
                continue
            src = temp_cw if d == 1 else temp_ccw
            dst = out_cw if d == 1 else out_ccw
            for j in range(12):
                idx = s + j
                if 0 <= idx < 72:
                    dst[idx] = int(src[idx])
        return out_cw, out_ccw

    def _finish_acc_bins_round_az(self) -> None:
        if not getattr(self, "_acc_bins_inflight_az", False):
            return
        axis_state = self.az
        mask = getattr(self, "_acc_bins_block_mask_az", None)
        temp_cw, temp_ccw = self._acc_bins_temp_cw, self._acc_bins_temp_ccw
        complete = (
            isinstance(mask, list)
            and len(mask) == len(self._CAL_LIVE_BLOCKS)
            and all(mask)
            and temp_cw is not None
            and temp_ccw is not None
        )
        if complete:
            assert temp_cw is not None and temp_ccw is not None
            axis_state.acc_bins_cw = list(temp_cw)
            axis_state.acc_bins_ccw = list(temp_ccw)
            if bool(getattr(self, "_statistics_window_open", False)) or bool(
                getattr(self, "_settings_window_open", False)
            ):
                self._acc_bins_stats_initial_az = True
                self._acc_bins_stats_arm_after_setpos_az = False
            if bool(getattr(self, "_compass_window_open", False)) and bool(
                getattr(self, "_compass_strom_heatmap_az", False)
            ):
                self._acc_bins_compass_initial_az = True
                self._acc_bins_compass_arm_after_move_az = False
        elif temp_cw is not None and temp_ccw is not None:
            miss: list[int] = []
            if isinstance(mask, list) and len(mask) == len(self._CAL_LIVE_BLOCKS):
                miss = [i + 1 for i, ok in enumerate(mask) if not ok]
            bcw, bcc = self._acc_bins_blend_incomplete(axis_state, temp_cw, temp_ccw, mask)
            axis_state.acc_bins_cw = bcw
            axis_state.acc_bins_ccw = bcc
            if miss:
                self.log.write(
                    "INFO",
                    f"AZ GETACCBINS: unvollständig (fehlende Blöcke {', '.join(str(x) for x in miss)}) — Anzeige mit Alt/0 aufgefüllt",
                )
            else:
                self.log.write(
                    "INFO",
                    "AZ GETACCBINS: unvollständig — Anzeige mit Alt/0 aufgefüllt",
                )
        self._acc_bins_temp_cw = None
        self._acc_bins_temp_ccw = None
        self._acc_bins_block_mask_az = None
        self._acc_bins_inflight_az = False
        self._acc_bins_finalize_pending_az = False

    def _finish_acc_bins_round_el(self) -> None:
        if not getattr(self, "_acc_bins_inflight_el", False):
            return
        axis_state = self.el
        mask = getattr(self, "_acc_bins_block_mask_el", None)
        temp_cw, temp_ccw = self._acc_bins_temp_cw_el, self._acc_bins_temp_ccw_el
        complete = (
            isinstance(mask, list)
            and len(mask) == len(self._CAL_LIVE_BLOCKS)
            and all(mask)
            and temp_cw is not None
            and temp_ccw is not None
        )
        if complete:
            assert temp_cw is not None and temp_ccw is not None
            axis_state.acc_bins_cw = list(temp_cw)
            axis_state.acc_bins_ccw = list(temp_ccw)
            if bool(getattr(self, "_statistics_window_open", False)) or bool(
                getattr(self, "_settings_window_open", False)
            ):
                self._acc_bins_stats_initial_el = True
                self._acc_bins_stats_arm_after_setpos_el = False
            if bool(getattr(self, "_compass_window_open", False)) and bool(
                getattr(self, "_compass_strom_heatmap_el", False)
            ):
                self._acc_bins_compass_initial_el = True
                self._acc_bins_compass_arm_after_move_el = False
        elif temp_cw is not None and temp_ccw is not None:
            miss: list[int] = []
            if isinstance(mask, list) and len(mask) == len(self._CAL_LIVE_BLOCKS):
                miss = [i + 1 for i, ok in enumerate(mask) if not ok]
            bcw, bcc = self._acc_bins_blend_incomplete(axis_state, temp_cw, temp_ccw, mask)
            axis_state.acc_bins_cw = bcw
            axis_state.acc_bins_ccw = bcc
            if miss:
                self.log.write(
                    "INFO",
                    f"EL GETACCBINS: unvollständig (fehlende Blöcke {', '.join(str(x) for x in miss)}) — Anzeige mit Alt/0 aufgefüllt",
                )
            else:
                self.log.write(
                    "INFO",
                    "EL GETACCBINS: unvollständig — Anzeige mit Alt/0 aufgefüllt",
                )
        self._acc_bins_temp_cw_el = None
        self._acc_bins_temp_ccw_el = None
        self._acc_bins_block_mask_el = None
        self._acc_bins_inflight_el = False
        self._acc_bins_finalize_pending_el = False

    def _async_merge_acc_bins_ack_az(self, tel: Optional[Telegram], axis_state: AxisState) -> None:
        """Verspäteter ACK_GETACCBINS: nur mergen (Kette läuft über on_done / Abschluss-Maske)."""
        if not getattr(self, "_acc_bins_inflight_az", False):
            return
        idx = self._bins_block_idx_from_tel(tel)
        if idx is None:
            return
        if tel:
            axis_state.last_rx_ts = time.time()
            axis_state.online = True
        temp_cw, temp_ccw = self._acc_bins_temp_cw, self._acc_bins_temp_ccw
        mask = getattr(self, "_acc_bins_block_mask_az", None)
        if tel and tel.params and temp_cw is not None and temp_ccw is not None:
            parts = (tel.params or "").strip().split(";")
            if len(parts) >= 4:
                dir_val = parse_int(parts[0])
                start_val = parse_int(parts[1])
                count_val = parse_int(parts[2])
                if dir_val is not None and start_val is not None and count_val is not None:
                    bins = temp_cw if dir_val == 1 else temp_ccw
                    if bins and 0 <= start_val < 72 and 1 <= count_val <= 12:
                        ok_m, plausible = merge_strom_bin_block(bins, parts, start_val, count_val)
                        if ok_m:
                            if isinstance(mask, list) and 0 <= idx < len(mask):
                                mask[idx] = True
                        elif not plausible:
                            self.log.write(
                                "WARN",
                                f"AZ GETACCBINS Block {idx + 1} (async): verdächtige Nullen/Lücken, Rohwerte übernommen",
                            )

    def _async_merge_acc_bins_ack_el(self, tel: Optional[Telegram], axis_state: AxisState) -> None:
        if not getattr(self, "_acc_bins_inflight_el", False):
            return
        idx = self._bins_block_idx_from_tel(tel)
        if idx is None:
            return
        if tel:
            axis_state.last_rx_ts = time.time()
            axis_state.online = True
        temp_cw, temp_ccw = self._acc_bins_temp_cw_el, self._acc_bins_temp_ccw_el
        mask = getattr(self, "_acc_bins_block_mask_el", None)
        if tel and tel.params and temp_cw is not None and temp_ccw is not None:
            parts = (tel.params or "").strip().split(";")
            if len(parts) >= 4:
                dir_val = parse_int(parts[0])
                start_val = parse_int(parts[1])
                count_val = parse_int(parts[2])
                if dir_val is not None and start_val is not None and count_val is not None:
                    bins = temp_cw if dir_val == 1 else temp_ccw
                    if bins and 0 <= start_val < 72 and 1 <= count_val <= 12:
                        ok_m, plausible = merge_strom_bin_block(bins, parts, start_val, count_val)
                        if ok_m:
                            if isinstance(mask, list) and 0 <= idx < len(mask):
                                mask[idx] = True
                        elif not plausible:
                            self.log.write(
                                "WARN",
                                f"EL GETACCBINS Block {idx + 1} (async): verdächtige Nullen/Lücken, Rohwerte übernommen",
                            )

    def _async_reconcile_cal_bins_ack_az(
        self, tel: Optional[Telegram], axis_state: AxisState
    ) -> None:
        """CAL-Bin-ACK aus Async-Pfad, wenn HW-Pending auf ein anderes ACK wartet (z. B. GETLIVEBINS)."""
        dst = int(self.slave_az)
        idx = self._bins_block_idx_from_tel(tel)
        if idx is None:
            idx = int(getattr(self, "_cal_bins_received_az", 0) or 0)
        if tel:
            axis_state.last_rx_ts = time.time()
            axis_state.online = True
        temp_cw, temp_ccw = self._cal_bins_temp_cw, self._cal_bins_temp_ccw
        if tel and tel.params and temp_cw is not None and temp_ccw is not None:
            parts = (tel.params or "").strip().split(";")
            if len(parts) >= 4:
                dir_val = parse_int(parts[0])
                start_val = parse_int(parts[1])
                count_val = parse_int(parts[2])
                if dir_val is not None and start_val is not None and count_val is not None:
                    bins = temp_cw if dir_val == 1 else temp_ccw
                    if bins and 0 <= start_val < 72 and 1 <= count_val <= 12:
                        ok_m, plausible = merge_strom_bin_block(bins, parts, start_val, count_val)
                        if not ok_m:
                            pass
                        elif not plausible:
                            self.log.write(
                                "WARN",
                                f"AZ GETCALBINS Block {idx + 1} (async): verdächtige Nullen/Lücken, Rohwerte übernommen",
                            )
        self._cal_bins_received_az = idx + 1
        self._send_next_cal_block(dst, axis_state, idx + 1)

    def _async_reconcile_cal_bins_ack_el(
        self, tel: Optional[Telegram], axis_state: AxisState, dst: int
    ) -> None:
        idx = self._bins_block_idx_from_tel(tel)
        if idx is None:
            return
        if tel:
            axis_state.last_rx_ts = time.time()
            axis_state.online = True
        temp_cw, temp_ccw = self._cal_bins_temp_cw_el, self._cal_bins_temp_ccw_el
        if tel and tel.params and temp_cw is not None and temp_ccw is not None:
            parts = (tel.params or "").strip().split(";")
            if len(parts) >= 4:
                dir_val = parse_int(parts[0])
                start_val = parse_int(parts[1])
                count_val = parse_int(parts[2])
                if dir_val is not None and start_val is not None and count_val is not None:
                    bins = temp_cw if dir_val == 1 else temp_ccw
                    if bins and 0 <= start_val < 72 and 1 <= count_val <= 12:
                        ok_m, plausible = merge_strom_bin_block(bins, parts, start_val, count_val)
                        if not ok_m:
                            pass
                        elif not plausible:
                            self.log.write(
                                "WARN",
                                f"EL GETCALBINS Block {idx + 1} (async): verdächtige Nullen/Lücken, Rohwerte übernommen",
                            )
        self._send_next_cal_block_el(int(dst), axis_state, idx + 1)

    def _async_reconcile_live_bins_ack_az(
        self, tel: Optional[Telegram], axis_state: AxisState
    ) -> None:
        dst = int(self.slave_az)
        idx = self._bins_block_idx_from_tel(tel)
        if idx is None:
            return
        if tel:
            axis_state.last_rx_ts = time.time()
            axis_state.online = True
        temp_cw, temp_ccw = self._live_bins_temp_cw, self._live_bins_temp_ccw
        if tel and tel.params and temp_cw is not None and temp_ccw is not None:
            parts = (tel.params or "").strip().split(";")
            if len(parts) >= 4:
                dir_val = parse_int(parts[0])
                start_val = parse_int(parts[1])
                count_val = parse_int(parts[2])
                if dir_val is not None and start_val is not None and count_val is not None:
                    bins = temp_cw if dir_val == 1 else temp_ccw
                    if bins and 0 <= start_val < 72 and 1 <= count_val <= 12:
                        ok_m, plausible = merge_strom_bin_block(bins, parts, start_val, count_val)
                        if not ok_m:
                            pass
                        elif not plausible:
                            self.log.write(
                                "WARN",
                                f"AZ GETLIVEBINS Block {idx + 1} (async): verdächtige Nullen/Lücken, Rohwerte übernommen",
                            )
        self._send_next_live_block(dst, axis_state, idx + 1)

    def _async_reconcile_live_bins_ack_el(
        self, tel: Optional[Telegram], axis_state: AxisState, dst: int
    ) -> None:
        idx = self._bins_block_idx_from_tel(tel)
        if idx is None:
            return
        if tel:
            axis_state.last_rx_ts = time.time()
            axis_state.online = True
        temp_cw, temp_ccw = self._live_bins_temp_cw_el, self._live_bins_temp_ccw_el
        if tel and tel.params and temp_cw is not None and temp_ccw is not None:
            parts = (tel.params or "").strip().split(";")
            if len(parts) >= 4:
                dir_val = parse_int(parts[0])
                start_val = parse_int(parts[1])
                count_val = parse_int(parts[2])
                if dir_val is not None and start_val is not None and count_val is not None:
                    bins = temp_cw if dir_val == 1 else temp_ccw
                    if bins and 0 <= start_val < 72 and 1 <= count_val <= 12:
                        ok_m, plausible = merge_strom_bin_block(bins, parts, start_val, count_val)
                        if not ok_m:
                            pass
                        elif not plausible:
                            self.log.write(
                                "WARN",
                                f"EL GETLIVEBINS Block {idx + 1} (async): verdächtige Nullen/Lücken, Rohwerte übernommen",
                            )
        self._send_next_live_block_el(int(dst), axis_state, idx + 1)

    # GETLIVEBINS: Pause zwischen aufeinanderfolgenden Blöcken (Bus/Firmware entlasten).
    _LIVE_BINS_INTER_BLOCK_DELAY_S = 0.0075  # 7,5 ms (Zielbereich ca. 5–10 ms)
    # GETACCBINS: längere ACKs / RS485 → großzügigeres HwRequest-Timeout; kurze Pause zwischen Blöcken.
    _ACC_BINS_HW_TIMEOUT_S = 0.2  # kurz: Worker/Pending blockiert sonst die UI; verspätete ACKs → Async-Merge + Maske
    _ACC_BINS_INTER_BLOCK_DELAY_S = 0.012
    _ACC_BINS_FINALIZE_GAP_S = 1.0

    _CAL_LIVE_BLOCKS = [
        (1, 0),
        (1, 12),
        (1, 24),
        (1, 36),
        (1, 48),
        (1, 60),
        (2, 0),
        (2, 12),
        (2, 24),
        (2, 36),
        (2, 48),
        (2, 60),
    ]

    def _fetch_cal_bins(
        self, dst: int, axis_state: AxisState, axis_name: str, priority: int = 3
    ) -> None:
        """CAL-Bins sequentiell abfragen (1 Block → warten → nächster), um Bus nicht zu überlasten."""
        if self._cal_bins_inflight_az:
            return
        self._cal_bins_inflight_az = True
        self._cal_bins_received_az = 0
        self._cal_bins_temp_cw = [0] * 72
        self._cal_bins_temp_ccw = [0] * 72
        self._cal_bins_priority_az = priority
        self._send_next_cal_block(dst, axis_state, 0)

    def _send_next_cal_block(self, dst: int, axis_state: AxisState, idx: int) -> None:
        if idx >= len(self._CAL_LIVE_BLOCKS):
            # Alle 12 Blöcke empfangen: Temp in axis_state übernehmen (nur wenn noch DONE)
            if (
                self._cal_bins_temp_cw
                and self._cal_bins_temp_ccw
                and getattr(axis_state, "cal_state", 0) == 2
            ):
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

        def on_done(tel: Optional[Telegram], err: Optional[str]):
            if tel:
                axis_state.last_rx_ts = time.time()
                axis_state.online = True
            if err:
                ctrl.log.write("WARN", f"AZ GETCALBINS Block {idx + 1} fehlgeschlagen: {err}")
            if tel and tel.params and temp_cw is not None and temp_ccw is not None:
                parts = (tel.params or "").strip().split(";")
                if len(parts) >= 4:
                    dir_val = parse_int(parts[0])
                    start_val = parse_int(parts[1])
                    count_val = parse_int(parts[2])
                    if dir_val is not None and start_val is not None and count_val is not None:
                        bins = temp_cw if dir_val == 1 else temp_ccw
                        if bins and 0 <= start_val < 72 and 1 <= count_val <= 12:
                            ok_m, plausible = merge_strom_bin_block(bins, parts, start_val, count_val)
                            if not ok_m:
                                pass
                            elif not plausible:
                                ctrl.log.write(
                                    "WARN",
                                    f"AZ GETCALBINS Block {idx + 1}: verdächtige Nullen/Lücken, Rohwerte übernommen",
                                )
            ctrl._cal_bins_received_az = idx + 1
            ctrl._send_next_cal_block(dst, axis_state, idx + 1)

        prio = getattr(self, "_cal_bins_priority_az", 3)
        self.hw.send_request(
            HwRequest(
                line=line,
                expect_prefix="ACK_GETCALBINS",
                timeout_s=1.2,
                on_done=on_done,
                priority=prio,
                dont_disconnect_on_timeout=True,
            )
        )

    def _fetch_cal_bins_el(
        self, dst: int, axis_state: AxisState, axis_name: str, priority: int = 3
    ) -> None:
        if self._cal_bins_inflight_el:
            return
        self._cal_bins_inflight_el = True
        self._cal_bins_temp_cw_el = [0] * 72
        self._cal_bins_temp_ccw_el = [0] * 72
        self._cal_bins_priority_el = priority
        self._send_next_cal_block_el(dst, axis_state, 0)

    def _send_next_cal_block_el(self, dst: int, axis_state: AxisState, idx: int) -> None:
        if idx >= len(self._CAL_LIVE_BLOCKS):
            if (
                self._cal_bins_temp_cw_el
                and self._cal_bins_temp_ccw_el
                and getattr(axis_state, "cal_state", 0) == 2
            ):
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

        def on_done(tel: Optional[Telegram], err: Optional[str]):
            if tel:
                axis_state.last_rx_ts = time.time()
                axis_state.online = True
            if err:
                ctrl.log.write("WARN", f"EL GETCALBINS Block {idx + 1} fehlgeschlagen: {err}")
            if tel and tel.params and temp_cw is not None and temp_ccw is not None:
                parts = (tel.params or "").strip().split(";")
                if len(parts) >= 4:
                    dir_val = parse_int(parts[0])
                    start_val = parse_int(parts[1])
                    count_val = parse_int(parts[2])
                    if dir_val is not None and start_val is not None and count_val is not None:
                        bins = temp_cw if dir_val == 1 else temp_ccw
                        if bins and 0 <= start_val < 72 and 1 <= count_val <= 12:
                            ok_m, plausible = merge_strom_bin_block(bins, parts, start_val, count_val)
                            if not ok_m:
                                pass
                            elif not plausible:
                                ctrl.log.write(
                                    "WARN",
                                    f"EL GETCALBINS Block {idx + 1}: verdächtige Nullen/Lücken, Rohwerte übernommen",
                                )
            ctrl._send_next_cal_block_el(dst, axis_state, idx + 1)

        prio = getattr(self, "_cal_bins_priority_el", 3)
        self.hw.send_request(
            HwRequest(
                line=line,
                expect_prefix="ACK_GETCALBINS",
                timeout_s=1.2,
                on_done=on_done,
                priority=prio,
                dont_disconnect_on_timeout=True,
            )
        )

    def _fetch_live_bins(
        self, dst: int, axis_state: AxisState, axis_name: str, priority: int = 3
    ) -> None:
        """LIVE-Bins sequentiell abfragen (1 Block → warten → nächster)."""
        if self._live_bins_inflight_az:
            return
        self._live_bins_inflight_az = True
        self._live_bins_received_az = 0
        self._live_bins_temp_cw = [0] * 72
        self._live_bins_temp_ccw = [0] * 72
        self._live_bins_priority_az = priority
        self._send_next_live_block(dst, axis_state, 0)

    def _send_next_live_block(self, dst: int, axis_state: AxisState, idx: int) -> None:
        if idx >= len(self._CAL_LIVE_BLOCKS):
            # Alle 12 Blöcke empfangen: Temp in axis_state übernehmen (Langzeit / GETLIVEBINS)
            if self._live_bins_temp_cw and self._live_bins_temp_ccw:
                axis_state.live_bins_cw = list(self._live_bins_temp_cw)
                axis_state.live_bins_ccw = list(self._live_bins_temp_ccw)
            self._live_bins_temp_cw = None
            self._live_bins_temp_ccw = None
            self._live_bins_inflight_az = False
            return
        if idx > 0:
            time.sleep(self._LIVE_BINS_INTER_BLOCK_DELAY_S)
        direction, start = self._CAL_LIVE_BLOCKS[idx]
        params = f"{direction};{start};12"
        line = build(self.master_id, dst, "GETLIVEBINS", params)
        ctrl = self
        temp_cw, temp_ccw = self._live_bins_temp_cw, self._live_bins_temp_ccw

        def on_done(tel: Optional[Telegram], err: Optional[str]):
            if tel:
                axis_state.last_rx_ts = time.time()
                axis_state.online = True
            if err:
                ctrl.log.write("WARN", f"AZ GETLIVEBINS Block {idx + 1} fehlgeschlagen: {err}")
            if tel and tel.params and temp_cw is not None and temp_ccw is not None:
                parts = (tel.params or "").strip().split(";")
                if len(parts) >= 4:
                    dir_val = parse_int(parts[0])
                    start_val = parse_int(parts[1])
                    count_val = parse_int(parts[2])
                    if dir_val is not None and start_val is not None and count_val is not None:
                        bins = temp_cw if dir_val == 1 else temp_ccw
                        if bins and 0 <= start_val < 72 and 1 <= count_val <= 12:
                            ok_m, plausible = merge_strom_bin_block(bins, parts, start_val, count_val)
                            if not ok_m:
                                pass
                            elif not plausible:
                                ctrl.log.write(
                                    "WARN",
                                    f"AZ GETLIVEBINS Block {idx + 1}: verdächtige Nullen/Lücken, Rohwerte übernommen",
                                )
            ctrl._send_next_live_block(dst, axis_state, idx + 1)

        prio = getattr(self, "_live_bins_priority_az", 3)
        self.hw.send_request(
            HwRequest(
                line=line,
                expect_prefix="ACK_GETLIVEBINS",
                timeout_s=1.2,
                on_done=on_done,
                priority=prio,
                dont_disconnect_on_timeout=True,
            )
        )

    def _fetch_live_bins_el(
        self, dst: int, axis_state: AxisState, axis_name: str, priority: int = 3
    ) -> None:
        if self._live_bins_inflight_el:
            return
        self._live_bins_inflight_el = True
        self._live_bins_temp_cw_el = [0] * 72
        self._live_bins_temp_ccw_el = [0] * 72
        self._live_bins_priority_el = priority
        self._send_next_live_block_el(dst, axis_state, 0)

    def _send_next_live_block_el(self, dst: int, axis_state: AxisState, idx: int) -> None:
        if idx >= len(self._CAL_LIVE_BLOCKS):
            if self._live_bins_temp_cw_el and self._live_bins_temp_ccw_el:
                axis_state.live_bins_cw = list(self._live_bins_temp_cw_el)
                axis_state.live_bins_ccw = list(self._live_bins_temp_ccw_el)
            self._live_bins_temp_cw_el = None
            self._live_bins_temp_ccw_el = None
            self._live_bins_inflight_el = False
            return
        if idx > 0:
            time.sleep(self._LIVE_BINS_INTER_BLOCK_DELAY_S)
        direction, start = self._CAL_LIVE_BLOCKS[idx]
        params = f"{direction};{start};12"
        line = build(self.master_id, dst, "GETLIVEBINS", params)
        ctrl = self
        temp_cw, temp_ccw = self._live_bins_temp_cw_el, self._live_bins_temp_ccw_el

        def on_done(tel: Optional[Telegram], err: Optional[str]):
            if tel:
                axis_state.last_rx_ts = time.time()
                axis_state.online = True
            if err:
                ctrl.log.write("WARN", f"EL GETLIVEBINS Block {idx + 1} fehlgeschlagen: {err}")
            if tel and tel.params and temp_cw is not None and temp_ccw is not None:
                parts = (tel.params or "").strip().split(";")
                if len(parts) >= 4:
                    dir_val = parse_int(parts[0])
                    start_val = parse_int(parts[1])
                    count_val = parse_int(parts[2])
                    if dir_val is not None and start_val is not None and count_val is not None:
                        bins = temp_cw if dir_val == 1 else temp_ccw
                        if bins and 0 <= start_val < 72 and 1 <= count_val <= 12:
                            ok_m, plausible = merge_strom_bin_block(bins, parts, start_val, count_val)
                            if not ok_m:
                                pass
                            elif not plausible:
                                ctrl.log.write(
                                    "WARN",
                                    f"EL GETLIVEBINS Block {idx + 1}: verdächtige Nullen/Lücken, Rohwerte übernommen",
                                )
            ctrl._send_next_live_block_el(dst, axis_state, idx + 1)

        prio = getattr(self, "_live_bins_priority_el", 3)
        self.hw.send_request(
            HwRequest(
                line=line,
                expect_prefix="ACK_GETLIVEBINS",
                timeout_s=1.2,
                on_done=on_done,
                priority=prio,
                dont_disconnect_on_timeout=True,
            )
        )

    def _fetch_acc_bins(
        self, dst: int, axis_state: AxisState, axis_name: str, priority: int = 3
    ) -> None:
        """ACC-Bins sequentiell abfragen (wie LIVE, schnelle aktuelle Last)."""
        if self._acc_bins_inflight_az:
            return
        self._acc_bins_inflight_az = True
        self._acc_bins_temp_cw = [0] * 72
        self._acc_bins_temp_ccw = [0] * 72
        self._acc_bins_block_mask_az = [False] * len(self._CAL_LIVE_BLOCKS)
        self._acc_bins_finalize_pending_az = False
        self._acc_bins_priority_az = priority
        self._send_next_acc_block(dst, axis_state, 0)

    def _send_next_acc_block(self, dst: int, axis_state: AxisState, idx: int) -> None:
        if time.time() < self._stats_cooldown_until:
            self._acc_bins_inflight_az = False
            self._acc_bins_temp_cw = None
            self._acc_bins_temp_ccw = None
            self._acc_bins_block_mask_az = None
            self._acc_bins_finalize_pending_az = False
            return
        if idx >= len(self._CAL_LIVE_BLOCKS):
            self._arm_acc_bins_finalize_az()
            return
        if not self._acc_bins_inflight_az:
            return
        if idx > 0:
            time.sleep(float(getattr(self, "_ACC_BINS_INTER_BLOCK_DELAY_S", 0.012) or 0.0))
        direction, start = self._CAL_LIVE_BLOCKS[idx]
        params = f"{direction};{start};12"
        line = build(self.master_id, dst, "GETACCBINS", params)
        ctrl = self
        temp_cw = self._acc_bins_temp_cw
        temp_ccw = self._acc_bins_temp_ccw
        mask = self._acc_bins_block_mask_az
        to = float(getattr(self, "_ACC_BINS_HW_TIMEOUT_S", 0.2) or 0.2)

        def on_done(tel: Optional[Telegram], err: Optional[str]):
            if not ctrl._acc_bins_inflight_az:
                return
            if tel:
                axis_state.last_rx_ts = time.time()
                axis_state.online = True
            # Timeout bricht die Runde nicht ab; verspätete ACKs werden per Async gemerged.
            block_ok = False
            if tel and tel.params and temp_cw is not None and temp_ccw is not None:
                parts = (tel.params or "").strip().split(";")
                if len(parts) >= 4:
                    dir_val = parse_int(parts[0])
                    start_val = parse_int(parts[1])
                    count_val = parse_int(parts[2])
                    if dir_val is not None and start_val is not None and count_val is not None:
                        bins = temp_cw if dir_val == 1 else temp_ccw
                        if bins and 0 <= start_val < 72 and 1 <= count_val <= 12:
                            ok_m, plausible = merge_strom_bin_block(bins, parts, start_val, count_val)
                            if ok_m:
                                block_ok = True
                                bi = ctrl._bins_block_idx_from_tel(tel)
                                if bi is None:
                                    bi = idx
                                if isinstance(mask, list) and 0 <= bi < len(mask):
                                    mask[bi] = True
                            elif not plausible:
                                ctrl.log.write(
                                    "WARN",
                                    f"AZ GETACCBINS Block {idx + 1}: verdächtige Nullen/Lücken, Rohwerte übernommen",
                                )
            if not err and not block_ok:
                ctrl.log.write(
                    "WARN",
                    f"AZ GETACCBINS Block {idx + 1}: unerwartete/leere Antwort (ohne Timeout)",
                )
            ctrl._send_next_acc_block(dst, axis_state, idx + 1)

        # Prio 6: hinter GETREF(2) / viele Polls — weniger „Überholen“ in der TX-Queue während der Kette.
        prio = 6
        self._acc_bins_last_tx_ts_az = time.time()
        self.hw.send_request(
            HwRequest(
                line=line,
                expect_prefix="ACK_GETACCBINS",
                timeout_s=to,
                on_done=on_done,
                priority=prio,
                dont_disconnect_on_timeout=True,
            )
        )

    def _fetch_acc_bins_el(
        self, dst: int, axis_state: AxisState, axis_name: str, priority: int = 3
    ) -> None:
        if self._acc_bins_inflight_el:
            return
        self._acc_bins_inflight_el = True
        self._acc_bins_temp_cw_el = [0] * 72
        self._acc_bins_temp_ccw_el = [0] * 72
        self._acc_bins_block_mask_el = [False] * len(self._CAL_LIVE_BLOCKS)
        self._acc_bins_finalize_pending_el = False
        self._acc_bins_priority_el = priority
        self._send_next_acc_block_el(dst, axis_state, 0)

    def _send_next_acc_block_el(self, dst: int, axis_state: AxisState, idx: int) -> None:
        if time.time() < self._stats_cooldown_until:
            self._acc_bins_inflight_el = False
            self._acc_bins_temp_cw_el = None
            self._acc_bins_temp_ccw_el = None
            self._acc_bins_block_mask_el = None
            self._acc_bins_finalize_pending_el = False
            return
        if idx >= len(self._CAL_LIVE_BLOCKS):
            self._arm_acc_bins_finalize_el()
            return
        if not self._acc_bins_inflight_el:
            return
        if idx > 0:
            time.sleep(float(getattr(self, "_ACC_BINS_INTER_BLOCK_DELAY_S", 0.012) or 0.0))
        direction, start = self._CAL_LIVE_BLOCKS[idx]
        params = f"{direction};{start};12"
        line = build(self.master_id, dst, "GETACCBINS", params)
        ctrl = self
        temp_cw, temp_ccw = self._acc_bins_temp_cw_el, self._acc_bins_temp_ccw_el
        mask = self._acc_bins_block_mask_el
        to = float(getattr(self, "_ACC_BINS_HW_TIMEOUT_S", 0.2) or 0.2)

        def on_done(tel: Optional[Telegram], err: Optional[str]):
            if not ctrl._acc_bins_inflight_el:
                return
            if tel:
                axis_state.last_rx_ts = time.time()
                axis_state.online = True
            block_ok = False
            if tel and tel.params and temp_cw is not None and temp_ccw is not None:
                parts = (tel.params or "").strip().split(";")
                if len(parts) >= 4:
                    dir_val = parse_int(parts[0])
                    start_val = parse_int(parts[1])
                    count_val = parse_int(parts[2])
                    if dir_val is not None and start_val is not None and count_val is not None:
                        bins = temp_cw if dir_val == 1 else temp_ccw
                        if bins and 0 <= start_val < 72 and 1 <= count_val <= 12:
                            ok_m, plausible = merge_strom_bin_block(bins, parts, start_val, count_val)
                            if ok_m:
                                block_ok = True
                                bi = ctrl._bins_block_idx_from_tel(tel)
                                if bi is None:
                                    bi = idx
                                if isinstance(mask, list) and 0 <= bi < len(mask):
                                    mask[bi] = True
                            elif not plausible:
                                ctrl.log.write(
                                    "WARN",
                                    f"EL GETACCBINS Block {idx + 1}: verdächtige Nullen/Lücken, Rohwerte übernommen",
                                )
            if not err and not block_ok:
                ctrl.log.write(
                    "WARN",
                    f"EL GETACCBINS Block {idx + 1}: unerwartete/leere Antwort (ohne Timeout)",
                )
            ctrl._send_next_acc_block_el(dst, axis_state, idx + 1)

        prio = 6
        self._acc_bins_last_tx_ts_el = time.time()
        self.hw.send_request(
            HwRequest(
                line=line,
                expect_prefix="ACK_GETACCBINS",
                timeout_s=to,
                on_done=on_done,
                priority=prio,
                dont_disconnect_on_timeout=True,
            )
        )

