from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Set, Optional

from .angle_utils import shortest_delta_deg

WARNINGS = {
    0: ("SW_NONE", "Keine Warnung", "-"),
    1: (
        "SW_IS_SOFT",
        "Strom-Warnung (Soft-Limit erreicht)",
        "Limits prüfen / IWARN / Mechanik prüfen",
    ),
    2: (
        "SW_WIND_GUST",
        "Windböe / kurzfristig stark erhöhte Last",
        "Bei Dauer: Schwellwerte oder Mechanik prüfen",
    ),
    3: ("SW_DRAG_INCREASE", "Gleichmäßig mehr Reibung", "Kälte/Schmierstoff/Getriebe prüfen"),
    4: ("SW_DRAG_DECREASE", "Gleichmäßig weniger Reibung", "Kann auf 'Last fehlt' hinweisen"),
    5: ("SW_TEMP_AMBIENT_HIGH", "Umgebung zu warm", "Warnschwelle / Schutz / Belüftung prüfen"),
    6: ("SW_TEMP_MOTOR_HIGH", "Motor zu warm", "Warnschwelle / Pausen / Last reduzieren"),
}


def warning_info(wid: int) -> tuple[str, str, str]:
    """Warnungs-ID -> (Name, Bedeutung, Was tun?)."""
    try:
        w = int(wid)
    except Exception:
        w = 0
    name, meaning, todo = WARNINGS.get(w, ("SW_UNKNOWN", "Unbekannt", "-"))
    return str(name), str(meaning), str(todo)


# Fehlercodes: es gibt in der Praxis (leider) mehrere Firmware-/Dokumentationsstände.
# Wir unterstützen daher zwei Tabellen:
# - ERROR_DETAILS_DOC: Codes wie vom User beschrieben (0001, 0002, ...).
# - ERROR_DETAILS_LEGACY: Codes wie im bisherigen Projekt (10/11/12/15/16/17/...).
#
# Format: (name, ursache_typisch, was_tun, wie_loeschen)
ERROR_DETAILS_DOC: dict[int, tuple[str, str, str, str]] = {
    0: ("SE_NONE", "-", "-", "-"),
    1: (
        "ERR_HOME_TIMEOUT",
        "Homing‑Phase überschreitet HOMETIMEOUT.",
        "Endschalter/Counts/HOMETIMEOUT prüfen und ggf. Mechanik prüfen.",
        "SETREF (quittiert)",
    ),
    2: (
        "ERR_MOVE_TIMEOUT",
        "Positionsfahrt überschreitet MOVETIMEOUT.",
        "Mechanik/Last prüfen, MOVETIMEOUT anpassen.",
        "SETREF (quittiert)",
    ),
    3: (
        "ERR_NOT_HOMED",
        "Positionsfahrt angefordert, aber Homing nicht durchgeführt.",
        "Erst referenzieren (Homing starten).",
        "SETREF",
    ),
    12: (
        "ERR_OVERCURRENT",
        "Überstrom erkannt (IMAX überschritten unter Berücksichtigung von OCHOLD/OCIGN).",
        "Mechanik prüfen, IMAX/Grace/Hold/OCHOLD/OCIGN prüfen.",
        "SETREF (quittiert)",
    ),
    13: (
        "ERR_JAM_DETECTED",
        "Blockade: zu geringe Bewegung innerhalb JAM‑Fenster.",
        "Mechanik/Haftreibung prüfen, JAM‑Parameter prüfen/anpassen.",
        "SETREF (quittiert)",
    ),
    14: (
        "ERR_DEADMAN_TIMEOUT",
        "Deadman‑Timeout: keine gültige RS485‑Anfrage innerhalb des Zeitfensters.",
        "Master/Bridge prüfen: Keepalive/Deadman‑Intervall anpassen.",
        "SETREF (quittiert) / Deadman anpassen",
    ),
    17: (
        "ERR_MOTOR_BEGIN_FAILED",
        "PWM/Driver‑(Re)Initialisierung fehlgeschlagen (z. B. nach SETPWMF/SETINVERT).",
        "PWM/Driver-Config prüfen, ggf. neu starten/Spannung prüfen.",
        "SETREF (quittiert)",
    ),
}

ERROR_DETAILS_LEGACY: dict[int, tuple[str, str, str, str]] = {
    0: ("SE_NONE", "Kein Fehler", "-", "-"),
    10: (
        "SE_TIMEOUT",
        "Deadman/Keepalive (Fehlercode 10 aus GETERR — kein App-Bus-Timeout)",
        "Firmware: zu lange keine gültigen Befehle während Bewegung; Log kann trotzdem Telegramme zeigen.",
        "SETREF (quittiert) / Deadman anpassen",
    ),
    11: (
        "SE_ENDSTOP",
        "Endschalter blockiert Fahrtrichtung",
        "Es soll in Richtung eines aktiven Endschalters gefahren werden.",
        "Richtung/Endschalter/Offset prüfen, dann SETREF",
    ),
    12: (
        "SE_NSTOP_CMD",
        "NSTOP (Not-Aus) per RS485",
        "Not-Stop Kommando empfangen.",
        "Ursache im Master, dann SETREF",
    ),
    15: (
        "SE_IS_HARD",
        "Strom-Hardlimit",
        "Zu hoher Motorstrom länger als Hold.",
        "Mechanik prüfen, IMAX/Grace/Hold prüfen, dann SETREF",
    ),
    16: (
        "SE_STALL",
        "Stall: Encoder bewegt sich nicht",
        "PWM an, aber zu wenige Encoder-Counts im Timeout.",
        "Mechanik/Haftreibung, MINPWM/KICK/STALLTIMEOUT anpassen, dann SETREF",
    ),
    17: (
        "SE_HOME_FAIL",
        "Homing fehlgeschlagen",
        "Timeout in Homing-Phase oder Endschalterproblem.",
        "Endschalter, Counts-Parameter, HOMETIMEOUT prüfen, dann SETREF",
    ),
    18: (
        "SE_POS_TIMEOUT",
        "Positionsfahrt Timeout",
        "Ziel nicht innerhalb posTimeoutMs erreicht (z.B. zu wenig PWM, Rampen zu weich, Mechanik blockiert).",
        "SETPOSTIMEOUT erhöhen",
    ),
}

# Backwards compatibility: bisherige Imports erwarten ERRORS.
ERRORS = {k: (v[0], v[1]) for k, v in ERROR_DETAILS_LEGACY.items()}


def error_info(code: int) -> tuple[str, str]:
    """Fehlercode -> (Name, Text) für UI (Popup + Statusfeld). Texte aus de/en-Locales."""
    from .error_popup_text import error_popup_text

    return error_popup_text(code)


# SmoothDamp smoothTime (s): größer = weicher / träger (Unity Mathf.SmoothDamp).
_SMOOTH_TIME_DYNAMIC_S = 0.20
_SMOOTH_TIME_IDLE_S = 0.42
# Max. Geschwindigkeit der Anzeige (0,1°/s); verhindert Ruckler bei großen dt.
_SMOOTH_MAX_SPEED_DYNAMIC_D10_S = 3200.0
_SMOOTH_MAX_SPEED_IDLE_D10_S = 900.0
# Sprung größer als dieses Δ (0,1°-Einheiten) → Anzeige + Geschwindigkeit sofort zurücksetzen.
_SMOOTH_SNAP_D10 = 500


def _smooth_delta_d10(wrap_360: bool, smooth_f: float, target_i: int) -> float:
    """Differenz smooth → target; AZ kürzester Kreisweg, EL linear."""
    if wrap_360:
        return shortest_delta_deg(smooth_f * 0.1, float(target_i) * 0.1) * 10.0
    return float(target_i) - float(smooth_f)


def _smooth_damp_scalar(
    current: float,
    target: float,
    current_velocity: float,
    smooth_time: float,
    max_speed: float,
    delta_time: float,
) -> tuple[float, float]:
    """Unity Mathf.SmoothDamp — kritisch gedämpft, ohne Überschwingen über ``target``."""
    st = max(0.0001, float(smooth_time))
    omega = 2.0 / st
    dt = float(delta_time)
    x = omega * dt
    exp = 1.0 / (1.0 + x + 0.48 * x * x + 0.235 * x * x * x)
    change = float(current) - float(target)
    original_to = float(target)
    max_change = float(max_speed) * st
    change = max(-max_change, min(max_change, change))
    target_adj = float(current) - change
    temp = (float(current_velocity) + omega * change) * dt
    new_vel = (float(current_velocity) - omega * temp) * exp
    output = target_adj + (change + temp) * exp
    if ((original_to - float(current)) > 0.0) == (output > original_to):
        output = original_to
        if abs(dt) > 1e-12:
            new_vel = (output - original_to) / dt
        else:
            new_vel = 0.0
    return output, new_vel


@dataclass
class AxisTelemetry:
    temp_ambient_c: Optional[float] = None
    temp_motor_c: Optional[float] = None
    wind_kmh: Optional[float] = None
    wind_dir_deg: Optional[float] = None
    wind_beaufort: Optional[int] = None  # 0–12, von GETBEAUFORT
    pwm_max_pct: Optional[float] = None
    pwm_min_pct: Optional[float] = None


@dataclass
class AxisState:
    pos_d10: int = 0
    # Geglättete Anzeige (Kompass/UI); Rohwert bleibt ``pos_d10``.
    smooth_pos_d10: int = 0
    smooth_pos_d10f: float = 0.0
    # Zeitstempel des letzten GETPOSDG-Samples (u. a. für moving/stop_confirm-Logik).
    _last_sample_ts: float = 0.0
    target_d10: int = 0
    # Kompass-Soll aus Bus (SETPOSCC, z. B. Encoder-Panel): nur Anzeige; SETPOSDG/PST/manuell setzen das zurück.
    compass_target_d10: Optional[int] = None
    referenced: bool = False
    moving: bool = False
    error_code: int = 0
    warnings: Set[int] = field(default_factory=set)
    ref_poll_active: bool = False
    online: bool = False
    last_rx_ts: float = 0.0
    telemetry: AxisTelemetry = field(default_factory=AxisTelemetry)
    # Letztes gesendetes Ziel (0,1°). Damit wir SETPOSDG nicht dauernd wiederholen.
    last_set_sent_target_d10: Optional[int] = None
    last_set_sent_ts: float = 0.0
    # SETPOSCC vom Bus kurz nach SETPOSDG ignorieren (Encoder-Spam vs. neues Motorziel).
    setposcc_ignore_until_ts: float = 0.0
    # Interne Entprellung für "steht am Ziel": erst nach mehreren stabilen Samples
    # wird moving=False gesetzt (sonst stockt die Anzeige beim Überschleifen).
    stop_confirm_samples: int = 0
    # True = AZ (0..360° kürzester Weg); False = EL (linear 0..90°).
    position_wrap_360: bool = True
    _last_smooth_render_ts: float = 0.0
    # Geschwindigkeit der Anzeige (0,1°/s) für SmoothDamp.
    _smooth_vel_f: float = 0.0

    # GETPOSDG: Coalescing in _poll_pos verhindert, dass sich GETPOSDG während
    # SETPOSDG-Bursts in der TX-Queue stauen (sonst friert der Ist-Zeiger ein
    # und alle aufgestauten ACKs kommen erst nach Burst-Ende als Batch). Flag
    # wird beim Enqueuen gesetzt und in _on_async_tel bei ACK_GETPOSDG wieder
    # freigegeben; Watchdog über pos_poll_sent_ts (0,9 s) verhindert Hänger.
    pos_poll_inflight: bool = False
    pos_poll_sent_ts: float = 0.0
    pos_poll_expected_period_s: float = 0.2

    # Kalibrier-Bins (nur wenn GETCALSTATE=2 DONE): 72 Stromwerte in mV pro Richtung
    cal_state: int = 0  # 0=IDLE, 1=RUNNING, 2=DONE, 3=ABORT
    cal_progress: int = 0  # GETCALSTATE Fortschritt 0..100 (nur sinnvoll bei state==1)
    cal_bins_cw: Optional[list] = None  # DIR=1, 72 Werte
    cal_bins_ccw: Optional[list] = None  # DIR=2, 72 Werte
    # Live-Bins (GETLIVEBINS): 72 aktuelle Stromwerte in mV pro Richtung
    live_bins_cw: Optional[list] = None
    live_bins_ccw: Optional[list] = None
    # ACC-Bins (GETACCBINS): 72 schnelle aktuelle Last-Bins (reagiert schneller als LIVE)
    acc_bins_cw: Optional[list] = None
    acc_bins_ccw: Optional[list] = None
    # Antennen-Versätze (GETANTOFF1–3): 0–360° pro Antenne
    antoff1: Optional[float] = None
    antoff2: Optional[float] = None
    antoff3: Optional[float] = None
    # Antennen-Öffnungswinkel (GETANGLE1–3): 0–360° pro Antenne
    angle1: Optional[float] = None
    angle2: Optional[float] = None
    angle3: Optional[float] = None

    def update_position_sample(
        self, new_pos_d10: int, sample_ts: Optional[float] = None, expected_period_s: float = 0.2
    ) -> None:
        """GETPOSDG: Rohposition setzen; große Sprünge ziehen die Anzeige sofort nach.

        ``expected_period_s`` bleibt in der Signatur (Aufrufer); optional für zukünftige Nutzung.
        """
        _ = expected_period_s
        prev = int(self.pos_d10)
        try:
            ts = float(sample_ts) if sample_ts is not None else 0.0
        except Exception:
            ts = 0.0
        if ts <= 0.0:
            ts = time.time()
        self._last_sample_ts = ts
        nxt = int(new_pos_d10)
        jump = abs(_smooth_delta_d10(self.position_wrap_360, float(prev), nxt))
        self.pos_d10 = nxt
        if jump >= float(_SMOOTH_SNAP_D10):
            self.smooth_pos_d10f = float(nxt)
            self.smooth_pos_d10 = int(nxt)
            self._last_smooth_render_ts = 0.0
            self._smooth_vel_f = 0.0

    def _in_dynamic_smoothing(self) -> bool:
        """Fahrt oder Referenz-Polling: schnelleres Nachführen wie bei Bewegung."""
        try:
            if bool(getattr(self, "moving", False)):
                return True
        except Exception:
            pass
        try:
            if bool(getattr(self, "ref_poll_active", False)):
                return True
        except Exception:
            pass
        return False

    def get_smoothed_pos_d10(self, now_ts: Optional[float] = None) -> int:
        """Geglätteter Istwert (0,1°), für UI/Kompass."""
        v = float(self.get_smoothed_pos_d10f(now_ts))
        vi = int(round(v))
        self.smooth_pos_d10 = vi
        return vi

    def get_smoothed_pos_d10f(self, now_ts: Optional[float] = None) -> float:
        """Kritisch gedämpftes Nachführen (SmoothDamp) zum Rohwert ``pos_d10``.

        Läuft pro UI-Schritt mit echtem ``dt`` — gleichmäßig auch bei unregelmäßigen GETPOSDG.
        AZ: Ziel entlang kürzestem Kreisbogen; EL: linear, Anzeige 0..90° begrenzt.
        """
        now = float(now_ts) if now_ts is not None else time.time()
        tgt_i = int(self.pos_d10)

        if self._last_smooth_render_ts <= 0.0:
            self._last_smooth_render_ts = now
            self.smooth_pos_d10f = float(tgt_i)
            self._smooth_vel_f = 0.0
            self.smooth_pos_d10 = int(round(self.smooth_pos_d10f))
            return self.smooth_pos_d10f

        dt_raw = now - self._last_smooth_render_ts
        if dt_raw < 0.0:
            self._last_smooth_render_ts = now
            dt = 0.0
        else:
            dt = min(dt_raw, 0.12)
            self._last_smooth_render_ts = now

        cur = float(self.smooth_pos_d10f)
        err = _smooth_delta_d10(self.position_wrap_360, cur, tgt_i)
        if abs(err) >= float(_SMOOTH_SNAP_D10):
            self.smooth_pos_d10f = float(tgt_i)
            self._smooth_vel_f = 0.0
        else:
            target_lin = cur + float(err)
            dyn = self._in_dynamic_smoothing()
            st = _SMOOTH_TIME_DYNAMIC_S if dyn else _SMOOTH_TIME_IDLE_S
            mx = _SMOOTH_MAX_SPEED_DYNAMIC_D10_S if dyn else _SMOOTH_MAX_SPEED_IDLE_D10_S
            new_x, new_v = _smooth_damp_scalar(cur, target_lin, float(self._smooth_vel_f), st, mx, dt)
            self.smooth_pos_d10f = new_x
            self._smooth_vel_f = float(new_v)

        if not self.position_wrap_360:
            self.smooth_pos_d10f = max(0.0, min(900.0, float(self.smooth_pos_d10f)))

        self.smooth_pos_d10 = int(round(self.smooth_pos_d10f))
        return self.smooth_pos_d10f
