"""Inbetriebnahme-Wizard: Sichtbarkeit, Defaults und Hilfsfunktionen (ohne UI)."""

from __future__ import annotations

from typing import Optional


# Step-IDs in Ablauffolge (nach Profil/Verbindung/Controller/Achsenwahl)
STEP_ENCODER = "encoder"
STEP_DRIVE = "drive"
STEP_ENDSTOPS = "endstops"
STEP_WEATHER = "weather"
STEP_TEMP = "temp"
STEP_CAL = "cal"
STEP_ZERO_REF = "zero_ref"
STEP_VERIFY = "verify"
STEP_ANTENNA = "antenna"
STEP_SERVICES = "services"
STEP_ALERTS = "alerts"
STEP_DONE = "done"

ROTOR_STEPS = (
    STEP_ENCODER,
    STEP_DRIVE,
    STEP_ENDSTOPS,
    STEP_WEATHER,
    STEP_TEMP,
    STEP_CAL,
    STEP_ZERO_REF,
    STEP_VERIFY,
    STEP_ANTENNA,
    STEP_SERVICES,
    STEP_ALERTS,
    STEP_DONE,
)

# Einzige Encoder-Typ-Liste (UI-Label-Keys). Settings/Rotor-Config sollen dieselbe Quelle nutzen.
ENCODER_TYPE_CHOICES: tuple[tuple[int, str], ...] = (
    (1, "setup.enc_1"),
    (2, "setup.enc_2"),
    (3, "setup.enc_3"),
    (4, "setup.enc_4"),
)

# SET*-Reihenfolge der Drive-Seite (Werte kommen aus UI / drive_defaults).
DRIVE_SET_SEQUENCE: tuple[str, ...] = (
    "SETROTORTYPE",
    "SETBEGINDG",
    "SETMAXDG",
    "SETMINPWM",
    "SETIWARN",
    "SETIMAX",
    "SETHOMEPWM",
    "SETHOMESEEKPPWM",
    "SETRAMP",
    "SETARRTOL",
)

# GET*-Felder der Drive-Seite: (GET-Befehl, UI-Key, "int"|"float").
DRIVE_GET_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("GETROTORTYPE", "rotor_type", "int"),
    ("GETBEGINDG", "begin_dg", "float"),
    ("GETMAXDG", "max_dg", "float"),
    ("GETMINPWM", "min_pwm", "float"),
    ("GETIWARN", "iwarn", "int"),
    ("GETIMAX", "imax", "int"),
    ("GETHOMEPWM", "home_pwm", "float"),
    ("GETHOMESEEKPPWM", "home_seek_pwm", "float"),
    ("GETRAMP", "ramp", "float"),
    ("GETARRTOL", "arrtol", "float"),
)


def max_dg_for_rotor_type(rotor_type: int, *, choice_360_720: Optional[int] = None) -> Optional[float]:
    """Max-Winkel (° ) zum Rotortyp. Typ 1 braucht ``choice_360_720`` (360 oder 720)."""
    try:
        rt = int(rotor_type)
    except (TypeError, ValueError):
        return None
    if rt == 2:
        return 90.0
    if rt == 3:
        return 180.0
    if rt == 1:
        if choice_360_720 in (360, 720):
            return float(choice_360_720)
        return None
    return None


def parse_encoder_type(value: object) -> Optional[int]:
    try:
        v = int(float(str(value or "").strip().replace(",", ".").split(";")[0]))
    except Exception:
        return None
    return v if v in (1, 2, 3, 4) else None


def parse_endstc(value: object) -> Optional[int]:
    try:
        v = int(float(str(value or "").strip().replace(",", ".").split(";")[0]))
    except Exception:
        return None
    return v if v in (0, 1, 2) else None


def weather_allowed(enc_type: Optional[int]) -> bool:
    return enc_type in (1, 2)


def wind_alerts_allowed(enc_type: Optional[int]) -> bool:
    """Wind-Warnschwellen nur bei Encoder-Typ 1/2 (Wetterstation/Anemo)."""
    return weather_allowed(enc_type)


def jog_allowed(enc_type: Optional[int], endstc: Optional[int]) -> bool:
    return enc_type in (1, 2) and endstc == 0


def cpr_measure_allowed(enc_type: Optional[int], endstc: Optional[int]) -> bool:
    return jog_allowed(enc_type, endstc)


def homing_setref_allowed(enc_type: Optional[int], endstc: Optional[int]) -> bool:
    return enc_type in (1, 2) and endstc is not None and endstc > 0


def manual_setref_allowed(enc_type: Optional[int], endstc: Optional[int]) -> bool:
    return enc_type in (1, 2) and endstc == 0


def setenczero_primary(enc_type: Optional[int]) -> bool:
    return enc_type == 3


def inclinometer_cal_allowed(enc_type: Optional[int]) -> bool:
    return enc_type == 4


def antenna_names_allowed(*, enable_az: bool) -> bool:
    return bool(enable_az)


def endstc_recommendation(enc_type: Optional[int]) -> int:
    if enc_type in (3, 4):
        return 0
    if enc_type in (1, 2):
        return 2
    return 0


def endstc_choices(enc_type: Optional[int]) -> tuple[int, ...]:
    if enc_type == 4:
        return (0, 2)
    if enc_type == 3:
        return (0,)
    return (0, 1, 2)


def drive_defaults(enc_type: Optional[int], axis: str) -> dict:
    """UI-Vorschläge für die DRIVE-Seite."""
    ax = str(axis or "").strip().lower()
    arrtol = 1.0 if enc_type in (3, 4) else 0.25
    if ax == "el":
        if enc_type == 4:
            max_dg = 90.0
            rotor_type = 2
        else:
            max_dg = 180.0
            rotor_type = 3
    else:
        max_dg = 360.0
        rotor_type = 1
    return {
        "rotor_type": rotor_type,
        "begin_dg": 0.0,
        "max_dg": max_dg,
        "min_pwm": 40.0,
        "home_pwm": 60.0,
        "home_seek_pwm": 60.0,
        "arrtol": arrtol,
        "ramp": None,  # aus GET übernehmen
        "iwarn": 3000,
        "imax": 5000,
    }


def compute_cpr(count_a: int, count_b: int, degrees: float) -> Optional[int]:
    """CPR aus zwei Encoder-Counts und dem gefahrenen Winkel (Grad)."""
    try:
        deg = float(degrees)
        if abs(deg) < 1e-6:
            return None
        delta = abs(int(count_b) - int(count_a))
        cpr = int(round(delta * 360.0 / abs(deg)))
        return cpr if cpr > 0 else None
    except Exception:
        return None


def cpr_for_range(count_zero: int, count_max: int, *, min_counts: int = 100) -> Optional[int]:
    """CPR = gemessene Counts von der Null bis zur Max-Stellung.

    Die Counts (``SETENCCAX`` / ``SETENCCRI``) gelten für den Bereich ``0 … SETMAXDG``
    (bei ``SETMAXDG:180`` also für 180°, bei 360 für 360°) — es wird nichts hochgerechnet.
    """
    try:
        delta = abs(int(count_max) - int(count_zero))
    except Exception:
        return None
    return delta if delta >= int(min_counts) else None


def scale_cpr(
    cpr_old: int,
    measured_deg: float,
    true_deg: float,
    *,
    min_true_deg: float = 5.0,
    min_measured_deg: float = 1.0,
) -> Optional[int]:
    """Neue Counts pro 360° aus Anzeige-Strecke und tatsächlicher Strecke.

    Der Rotor rechnet Winkel = Counts * 360 / CPR. Zeigt er für eine Strecke
    ``measured_deg`` an, die tatsächlich ``true_deg`` lang ist, gilt
    ``CPR_neu = CPR_alt * measured / true``. Gegenläufige Vorzeichen oder zu kleine
    Strecken liefern None (unzuverlässig).
    """
    try:
        old = int(cpr_old)
        m = float(measured_deg)
        tr = float(true_deg)
    except Exception:
        return None
    if old <= 0 or abs(tr) < float(min_true_deg) or abs(m) < float(min_measured_deg):
        return None
    if (m > 0) != (tr > 0):
        return None
    new = int(round(old * abs(m) / abs(tr)))
    return new if new > 0 else None


def step_visible(
    step_id: str,
    *,
    enc_type: Optional[int] = None,
    endstc: Optional[int] = None,
    enable_az: bool = True,
) -> bool:
    """Ob ein Rotor-Schritt in der aktuellen Konfiguration gezeigt wird."""
    sid = str(step_id or "").strip()
    if sid == STEP_WEATHER:
        return weather_allowed(enc_type)
    if sid == STEP_ANTENNA:
        return antenna_names_allowed(enable_az=enable_az)
    if sid == STEP_ALERTS:
        # Optional, aber immer angeboten (Wind-UI nur bei Typ 1/2).
        return True
    if sid in ROTOR_STEPS:
        return True
    return False


def visible_rotor_steps(
    *,
    enc_type: Optional[int] = None,
    endstc: Optional[int] = None,
    enable_az: bool = True,
) -> list[str]:
    return [
        s
        for s in ROTOR_STEPS
        if step_visible(s, enc_type=enc_type, endstc=endstc, enable_az=enable_az)
    ]


def ack_value_matches(expected: str, got: str, *, kind: str = "float") -> bool:
    """Vergleicht SET-Soll mit GET-Antwort (tolerant)."""
    e = str(expected or "").strip().replace(",", ".")
    g = str(got or "").strip().replace(",", ".").split(";")[0]
    if not e or not g:
        return False
    if kind == "int":
        try:
            return int(float(e)) == int(float(g))
        except Exception:
            return False
    try:
        return abs(float(e) - float(g)) < 1e-3
    except Exception:
        return e.casefold() == g.casefold()
