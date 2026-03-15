
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Set, Optional

WARNINGS = {
    0: ("SW_NONE", "Keine Warnung", "-"),
    1: ("SW_IS_SOFT", "Strom-Warnung (Soft-Limit erreicht)", "Limits prüfen / IWARN / Mechanik prüfen"),
    2: ("SW_WIND_GUST", "Windböe / kurzfristig stark erhöhte Last", "Bei Dauer: Schwellwerte oder Mechanik prüfen"),
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
    1: ("ERR_HOME_TIMEOUT", "Homing‑Phase überschreitet HOMETIMEOUT.", "Endschalter/Counts/HOMETIMEOUT prüfen und ggf. Mechanik prüfen.", "SETREF (quittiert)"),
    2: ("ERR_MOVE_TIMEOUT", "Positionsfahrt überschreitet MOVETIMEOUT.", "Mechanik/Last prüfen, MOVETIMEOUT anpassen.", "SETREF (quittiert)"),
    3: ("ERR_NOT_HOMED", "Positionsfahrt angefordert, aber Homing nicht durchgeführt.", "Erst referenzieren (Homing starten).", "SETREF"),
    12: ("ERR_OVERCURRENT", "Überstrom erkannt (IMAX überschritten unter Berücksichtigung von OCHOLD/OCIGN).", "Mechanik prüfen, IMAX/Grace/Hold/OCHOLD/OCIGN prüfen.", "SETREF (quittiert)"),
    13: ("ERR_JAM_DETECTED", "Blockade: zu geringe Bewegung innerhalb JAM‑Fenster.", "Mechanik/Haftreibung prüfen, JAM‑Parameter prüfen/anpassen.", "SETREF (quittiert)"),
    14: ("ERR_DEADMAN_TIMEOUT", "Deadman‑Timeout: keine gültige RS485‑Anfrage innerhalb des Zeitfensters.", "Master/Bridge prüfen: Keepalive/Deadman‑Intervall anpassen.", "SETREF (quittiert) / Deadman anpassen"),
    17: ("ERR_MOTOR_BEGIN_FAILED", "PWM/Driver‑(Re)Initialisierung fehlgeschlagen (z. B. nach SETPWMF/SETINVERT).", "PWM/Driver-Config prüfen, ggf. neu starten/Spannung prüfen.", "SETREF (quittiert)"),
}

ERROR_DETAILS_LEGACY: dict[int, tuple[str, str, str, str]] = {
    0: ("SE_NONE", "Kein Fehler", "-", "-"),
    10: ("SE_TIMEOUT", "Deadman/Keepalive Timeout", "Master sendet zu lange keine Befehle während Bewegung.", "SETREF (quittiert) / Deadman anpassen"),
    11: ("SE_ENDSTOP", "Endschalter blockiert Fahrtrichtung", "Es soll in Richtung eines aktiven Endschalters gefahren werden.", "Richtung/Endschalter/Offset prüfen, dann SETREF"),
    12: ("SE_NSTOP_CMD", "NSTOP (Not-Aus) per RS485", "Not-Stop Kommando empfangen.", "Ursache im Master, dann SETREF"),
    15: ("SE_IS_HARD", "Strom-Hardlimit", "Zu hoher Motorstrom länger als Hold.", "Mechanik prüfen, IMAX/Grace/Hold prüfen, dann SETREF"),
    16: ("SE_STALL", "Stall: Encoder bewegt sich nicht", "PWM an, aber zu wenige Encoder-Counts im Timeout.", "Mechanik/Haftreibung, MINPWM/KICK/STALLTIMEOUT anpassen, dann SETREF"),
    17: ("SE_HOME_FAIL", "Homing fehlgeschlagen", "Timeout in Homing-Phase oder Endschalterproblem.", "Endschalter, Counts-Parameter, HOMETIMEOUT prüfen, dann SETREF"),
    18: ("SE_POS_TIMEOUT", "Positionsfahrt Timeout", "Ziel nicht innerhalb posTimeoutMs erreicht (z.B. zu wenig PWM, Rampen zu weich, Mechanik blockiert).", "SETPOSTIMEOUT erhöhen"),
}

# Backwards compatibility: bisherige Imports erwarten ERRORS.
ERRORS = {k: (v[0], v[1]) for k, v in ERROR_DETAILS_LEGACY.items()}

def error_info(code: int) -> tuple[str, str]:
    """Fehlercode -> (Name, Text) für UI (Popup + Statusfeld)."""
    try:
        c = int(code)
    except Exception:
        c = 0

    doc = ERROR_DETAILS_DOC.get(c)
    legacy = ERROR_DETAILS_LEGACY.get(c)

    # Doku bevorzugen, aber Legacy als Zusatz (wenn unterschiedlich) anzeigen
    if doc and legacy and doc[0] != legacy[0]:
        name = f"{doc[0]} (alt: {legacy[0]})"
        urs, todo, clear = doc[1], doc[2], doc[3]
        extra = f"\n\nAlt/Legacy:\nUrsache: {legacy[1]}\nWas tun: {legacy[2]}\nWie löschen: {legacy[3]}"
        text = f"Ursache: {urs}\nWas tun: {todo}\nWie löschen: {clear}{extra}"
        return name, text

    if doc:
        name, urs, todo, clear = doc
        return name, f"Ursache: {urs}\nWas tun: {todo}\nWie löschen: {clear}"
    if legacy:
        name, urs, todo, clear = legacy
        return name, f"Ursache: {urs}\nWas tun: {todo}\nWie löschen: {clear}"

    return ("SE_UNKNOWN", "Ursache: Unbekannt\nWas tun: -\nWie löschen: -")

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
    # Glatt dargestellte Position (für GUI): wird zwischen Polling-Samples interpoliert.
    smooth_pos_d10: int = 0
    # Gleiche Information wie smooth_pos_d10, aber als Float (für wirklich weiche Anzeige).
    smooth_pos_d10f: float = 0.0
    # Interpolations-Parameter (private): linear von _smooth_from -> _smooth_to.
    _smooth_from_d10: float = 0.0
    _smooth_to_d10: float = 0.0
    _smooth_from_ts: float = 0.0
    _smooth_to_ts: float = 0.0
    _last_sample_ts: float = 0.0
    target_d10: int = 0
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
    # Interne Entprellung für "steht am Ziel": erst nach mehreren stabilen Samples
    # wird moving=False gesetzt (sonst stockt die Anzeige beim Überschleifen).
    stop_confirm_samples: int = 0

    # Polling-Flow-Control: verhindert, dass mehrere GETPOSDG gleichzeitig "in flight" sind.
    # Ohne diese Sperre können sich Requests aufstauen und Antworten gebündelt ankommen,
    # was die Interpolation ruckelig macht.
    pos_poll_inflight: bool = False
    pos_poll_sent_ts: float = 0.0
    pos_poll_expected_period_s: float = 0.2

    # Kalibrier-Bins (nur wenn GETCALSTATE=2 DONE): 72 Stromwerte in mV pro Richtung
    cal_state: int = 0  # 0=IDLE, 1=RUNNING, 2=DONE, 3=ABORT
    cal_bins_cw: Optional[list] = None   # DIR=1, 72 Werte
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

    def update_position_sample(self, new_pos_d10: int, sample_ts: Optional[float] = None,
                               expected_period_s: float = 0.2) -> None:
        """Neuen Positions-Sample übernehmen und Interpolation vorbereiten.

        Idee:
        - Während der Rotor fährt, pollen wir GETPOSDG alle ~200ms.
        - Dazwischen soll die Anzeige weich laufen.

        Vorgehen:
        - Beim Eintreffen eines neuen Samples merken wir den aktuellen (bereits geglätteten)
          Wert als Startpunkt und interpolieren dann bis zum neuen Sample.
        - Die Interpolationsdauer orientiert sich an der Zeit seit dem letzten Sample,
          wird aber auf sinnvolle Grenzen geklemmt.
        """
        try:
            ts = float(sample_ts) if sample_ts is not None else 0.0
        except Exception:
            ts = 0.0
        if ts <= 0.0:
            # Fallback auf "jetzt"
            import time as _time
            ts = _time.time()

        # Intervall seit letztem Sample bestimmen
        dt = expected_period_s
        if self._last_sample_ts > 0.0:
            # Wenn Polling langsamer ist (oder jittert), soll die Anzeige trotzdem
            # bis zum nächsten Sample "weiterlaufen" statt nach 0,5s zu stoppen.
            # Daher erlauben wir größere dt (bis ca. 1,5s).
            dt = max(0.05, min(1.5, ts - self._last_sample_ts))
        self._last_sample_ts = ts

        # Aktuellen geglätteten Wert als Start nehmen
        cur_f = float(self.get_smoothed_pos_d10f(ts))

        self.pos_d10 = int(new_pos_d10)
        self._smooth_from_d10 = float(cur_f)
        self._smooth_to_d10 = float(int(new_pos_d10))
        self._smooth_from_ts = ts
        self._smooth_to_ts = ts + float(dt)

        # Direkt auch den sichtbaren Wert aktualisieren (damit UI ohne Timer nicht "hängt")
        self.smooth_pos_d10f = float(cur_f)
        self.smooth_pos_d10 = int(round(cur_f))

    def get_smoothed_pos_d10(self, now_ts: Optional[float] = None) -> int:
        """Geglättete Position (0,1°) als int (abwärtskompatibel)."""
        v = float(self.get_smoothed_pos_d10f(now_ts))
        vi = int(round(v))
        self.smooth_pos_d10 = vi
        return vi

    def get_smoothed_pos_d10f(self, now_ts: Optional[float] = None) -> float:
        """Geglättete Position (0,1°) als float (für wirklich weiche UI)."""
        import time as _time
        t = float(now_ts) if now_ts is not None else _time.time()

        # Wenn keine Interpolation aktiv ist, direkt Istwert
        if self._smooth_to_ts <= self._smooth_from_ts:
            v = float(self.pos_d10)
            self.smooth_pos_d10f = v
            return v

        # Nach Interpolation: am Ziel
        if t >= self._smooth_to_ts:
            v = float(self._smooth_to_d10)
            self.smooth_pos_d10f = v
            return v

        # Lineare Interpolation (ohne Rundung)
        f = (t - self._smooth_from_ts) / (self._smooth_to_ts - self._smooth_from_ts)
        if f < 0.0:
            f = 0.0
        elif f > 1.0:
            f = 1.0
        v = float(self._smooth_from_d10 + (self._smooth_to_d10 - self._smooth_from_d10) * f)
        self.smooth_pos_d10f = v
        return v
