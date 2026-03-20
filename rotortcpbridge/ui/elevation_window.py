"""Höhenprofil-Fenster zwischen zwei Koordinaten (Antenne ↔ Ziel).

Benötigt Internetverbindung – Höhendaten von opentopodata.org (SRTM 90 m).
Beugungsanalyse nach ITU-R P.526 (Knife-Edge, einzelne Kante).
"""
from __future__ import annotations

import json
import math
from typing import Dict, List, Optional, Tuple
from urllib.request import Request, urlopen

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
)
from PySide6.QtWebEngineWidgets import QWebEngineView

from ..i18n import t

# Stützpunkte entlang der Strecke (max. 100 für opentopodata free tier)
_N_POINTS = 80


# ── Geometrie ──────────────────────────────────────────────────────────────

def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return R * 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))


def _great_circle_sample(
    lat1: float, lon1: float,
    lat2: float, lon2: float,
    n: int,
) -> List[Tuple[float, float]]:
    """n gleichmäßig verteilte Punkte auf dem Großkreis via SLERP."""
    def to_ecef(lat_d: float, lon_d: float) -> Tuple[float, float, float]:
        lat, lon = math.radians(lat_d), math.radians(lon_d)
        return (math.cos(lat) * math.cos(lon),
                math.cos(lat) * math.sin(lon),
                math.sin(lat))

    def from_ecef(x: float, y: float, z: float) -> Tuple[float, float]:
        return (math.degrees(math.atan2(z, math.sqrt(x * x + y * y))),
                math.degrees(math.atan2(y, x)))

    p1 = to_ecef(lat1, lon1)
    p2 = to_ecef(lat2, lon2)
    dot = max(-1.0, min(1.0, sum(a * b for a, b in zip(p1, p2))))
    omega = math.acos(dot)
    if omega < 1e-10:
        return [(lat1, lon1)] * n
    sin_omega = math.sin(omega)
    pts: List[Tuple[float, float]] = []
    for i in range(n):
        t = i / (n - 1)
        s1 = math.sin((1.0 - t) * omega) / sin_omega
        s2 = math.sin(t * omega) / sin_omega
        x = s1 * p1[0] + s2 * p2[0]
        y = s1 * p1[1] + s2 * p2[1]
        z = s1 * p1[2] + s2 * p2[2]
        pts.append(from_ecef(x, y, z))
    return pts


# ── ITU-R P.526  Knife-Edge Beugung ───────────────────────────────────────

def _knife_edge_analysis(
    elevations: List[float],
    los: List[float],
    dists: List[float],
    freq_mhz: float,
) -> Optional[Dict]:
    """
    Findet das dominante Hindernis (maximales h über Sichtlinie) und berechnet
    den Knife-Edge-Beugungsverlust nach ITU-R P.526.

    Rückgabe: dict mit Analyseergebnissen oder None wenn nicht berechenbar.
    """
    n = len(elevations)
    if n < 3 or freq_mhz <= 0 or dists[-1] <= 0:
        return None

    # Maximales Überschusshindernis h = Gelände − Sichtlinie (ohne Endpunkte)
    max_h   = -1e9
    max_idx = 1
    for i in range(1, n - 1):
        h = elevations[i] - los[i]
        if h > max_h:
            max_h   = h
            max_idx = i

    d1_km = dists[max_idx]
    d2_km = dists[-1] - dists[max_idx]
    if d1_km <= 0 or d2_km <= 0:
        return None

    lam_m = 3e8 / (freq_mhz * 1e6)   # Wellenlänge in Metern
    d1_m  = d1_km * 1000.0
    d2_m  = d2_km * 1000.0

    # Beugungsparameter ν (nu), Formel aus ITU-R P.526
    nu = max_h * math.sqrt(2.0 * (d1_m + d2_m) / (lam_m * d1_m * d2_m))

    # Beugungsverlust J(ν) in dB – ITU-R P.526 Näherung
    if nu <= -0.78:
        j_db = 0.0
    else:
        arg  = math.sqrt((nu - 0.1) ** 2 + 1.0) + nu - 0.1
        j_db = max(0.0, 6.9 + 20.0 * math.log10(max(arg, 1e-12)))

    # Leistungsanteil in %  (10^(−J/10) × 100)
    power_pct = 10.0 ** (-j_db / 10.0) * 100.0

    # Qualitative Bewertung
    # max_h <= 0: Terrain geometrisch UNTER der Sichtlinie → freie LOS, keine Beugung.
    # Fresnel-Zonen-Effekte werden ignoriert wenn die Antenne niedriger als das Ziel
    # ist und die Keule nach oben zeigt – die physikalisch relevante Aussage ist dann
    # "freie Sichtlinie", auch wenn das Terrain knapp in die erste Fresnel-Zone ragt.
    if max_h <= 0 or nu <= -0.78 or j_db < 1.0:
        quality_key   = "elevation.quality_none"
        quality_color = "#5cb85c"
        quality_grade = 5
    elif j_db < 6.0:
        quality_key   = "elevation.quality_light"
        quality_color = "#8bc34a"
        quality_grade = 4
    elif j_db < 10.0:
        quality_key   = "elevation.quality_moderate"
        quality_color = "#f0ad4e"
        quality_grade = 3
    elif j_db < 16.0:
        quality_key   = "elevation.quality_strong"
        quality_color = "#e07050"
        quality_grade = 2
    else:
        quality_key   = "elevation.quality_very_strong"
        quality_color = "#c0392b"
        quality_grade = 1

    return {
        "h_m":           max_h,
        "nu":            nu,
        "j_db":          j_db,
        "power_pct":     power_pct,
        "obstacle_idx":  max_idx,
        "obstacle_dist": dists[max_idx],
        "quality_key":   quality_key,
        "quality_color": quality_color,
        "quality_grade": quality_grade,
        "d1_km":         d1_km,
        "d2_km":         d2_km,
        "lam_m":         lam_m,
    }


# ── Ionosphärische Ausbreitungsanalyse (KW < 30 MHz) ─────────────────────

def _sky_wave_analysis(
    dist_km: float,
    freq_mhz: float,
    fo_f2: Optional[float] = None,
    fo_e: Optional[float] = None,
) -> Optional[Dict]:
    """
    Ionosphärische Ausbreitungsanalyse für KW-Frequenzen (< 30 MHz).

    fo_f2, fo_e: optional aus NOAA SWPC Live-Daten (F10.7 → foF2).
    Ohne Angabe: Tagesmittelwerte (Sonnenflecken-Mittelwert, Mitteleuropa).
    """
    if freq_mhz >= 30.0:
        return None

    RE = 6371.0       # Erdradius km
    H_E   = 110.0     # E-Schicht Höhe km
    H_F2  = 350.0     # F2-Schicht Höhe km
    FO_E  = float(fo_e) if fo_e is not None else 4.0
    FO_F2 = float(fo_f2) if fo_f2 is not None else 7.5

    # Maximale Einfachsprung-Entfernung (flache Erde, horizontale Abstrahlung)
    e_max_km  = 2.0 * math.sqrt(2.0 * RE * H_E)    # ≈ 2370 km
    f2_max_km = 2.0 * math.sqrt(2.0 * RE * H_F2)   # ≈ 4220 km

    def _min_skip(h_km: float, fo: float) -> float:
        """Minimale Skip-Entfernung: freq ≤ fo → NVIS (0 km), sonst geometrisch."""
        if freq_mhz <= fo:
            return 0.0
        sin_t = fo / freq_mhz
        theta = math.asin(min(sin_t, 1.0))
        tan_t = math.tan(theta)
        return 2.0 * h_km / tan_t if tan_t > 0.01 else 0.0

    e_min_km  = _min_skip(H_E,  FO_E)
    f2_min_km = _min_skip(H_F2, FO_F2)

    # Bodenwellen-Reichweite (empirisch, mittlerer Boden)
    gw_km = max(30.0, 450.0 * (3.5 / max(freq_mhz, 0.5)) ** 0.75)

    # Anzahl Sprünge
    hops_e  = max(1, math.ceil(dist_km / e_max_km))  if dist_km > 10 else 1
    hops_f2 = max(1, math.ceil(dist_km / f2_max_km)) if dist_km > 10 else 1

    # MUF-Abschätzung für diesen Pfad (F2, Sprungs-Geometrie)
    if dist_km > 50:
        d_hop = dist_km / hops_f2
        sin_t = (2.0 * H_F2) / math.sqrt(d_hop ** 2 + (2.0 * H_F2) ** 2)
        muf = FO_F2 / max(sin_t, 0.05)
    else:
        muf = FO_F2 * 1.05   # Quasi-vertikal → MUF ≈ foF2

    # NVIS: Frequenz ≤ foF2 → senkrechter Einfall möglich
    nvis_ok = freq_mhz <= FO_F2

    # ── Verfügbare Ausbreitungsmodi ───────────────────────────────────────
    modes: List[Dict] = []

    if dist_km <= gw_km:
        modes.append({
            "name_key": "elevation.groundwave",
            "sub_key": "elevation.groundwave_range",
            "sub_params": {"km": gw_km},
            "ok": True,
        })

    if nvis_ok and dist_km <= 500:
        modes.append({
            "name_key": "elevation.nvis",
            "sub_key": "elevation.nvis_sub",
            "sub_params": {},
            "ok": True,
        })

    if dist_km >= e_min_km and dist_km <= e_max_km * hops_e:
        modes.append({
            "name_key": "elevation.e_layer",
            "name_params": {"n": hops_e},
            "sub_key": "elevation.e_skip",
            "sub_params": {"min": e_min_km, "max": int(e_max_km)},
            "ok": hops_e <= 2,
        })

    f2_reachable = (nvis_ok and dist_km <= 500) or (dist_km >= f2_min_km)
    if f2_reachable:
        if freq_mhz <= muf:
            modes.append({
                "name_key": "elevation.f2_layer",
                "name_params": {"n": hops_f2},
                "sub_key": "elevation.f2_muf",
                "sub_params": {"muf": muf},
                "ok": hops_f2 <= 2 and freq_mhz <= muf,
            })
        else:
            modes.append({
                "name_key": "elevation.f2_layer",
                "name_params": {"n": hops_f2},
                "sub_key": "elevation.f2_over_muf",
                "sub_params": {"muf": muf},
                "ok": False,
            })

    # Skip-Zone liegt vor, wenn weder Bodenwelle noch Sky-Wave reicht
    skip_zone = (
        dist_km > gw_km
        and not nvis_ok
        and (e_min_km == 0 or dist_km < e_min_km)
        and (f2_min_km == 0 or dist_km < f2_min_km)
    )
    if skip_zone and not any(m["ok"] for m in modes):
        modes = []   # alle nicht-OK Einträge entfernen für saubere Darstellung

    # Bandname
    _bands = [
        (2.0, "160 m"), (4.5, "80 m"), (7.5, "40 m"), (10.5, "30 m"),
        (14.5, "20 m"), (18.5, "17 m"), (21.5, "15 m"), (25.0, "12 m"),
    ]
    band_name = "10 m"
    for thr, bname in _bands:
        if freq_mhz <= thr:
            band_name = bname
            break

    return {
        "band_name":  band_name,
        "gw_km":      round(gw_km),
        "nvis_ok":    nvis_ok,
        "e_min_km":   round(e_min_km),
        "e_max_km":   round(e_max_km),
        "f2_min_km":  round(f2_min_km),
        "f2_max_km":  round(f2_max_km),
        "hops_e":     hops_e,
        "hops_f2":    hops_f2,
        "muf":        round(muf, 1),
        "skip_zone":  skip_zone,
        "fo_f2":      FO_F2,
        "modes":      modes,
    }


# ── Optimales Band / Frequenzempfehlung ───────────────────────────────────

# Amateurfunkbänder: (center MHz, Bandname, untere Grenze MHz, obere Grenze MHz)
_AMATEUR_BANDS: List[Tuple[float, str, float, float]] = [
    (1.85,   "160 m",  1.810,  2.000),
    (3.65,   "80 m",   3.500,  3.800),
    (7.10,   "40 m",   7.000,  7.300),
    (10.12,  "30 m",  10.100, 10.150),
    (14.20,  "20 m",  14.000, 14.350),
    (18.12,  "17 m",  18.068, 18.168),
    (21.20,  "15 m",  21.000, 21.450),
    (24.93,  "12 m",  24.890, 24.990),
    (28.50,  "10 m",  28.000, 29.700),
    (50.20,   "6 m",  50.000, 54.000),
    (144.30,  "2 m",  144.000, 146.000),
    (432.20, "70 cm", 430.000, 440.000),
]
# Nur HF + 6 m für Skywave-Empfehlung (2 m / 70 cm F2-Ausbreitung praktisch nicht)
_AMATEUR_BANDS_SKYWAVE: List[Tuple[float, str, float, float]] = [
    b for b in _AMATEUR_BANDS if b[0] <= 54.0
]


def _best_freq_recommendation(
    dist_km: float,
    los_blocked: bool,
    fo_f2: Optional[float] = None,
) -> Dict:
    """
    Empfiehlt das optimale Amateurfunkband und die Frequenz für diese Entfernung.

    fo_f2: optional aus NOAA SWPC Live-Daten. Ohne: Tagesmittelwerte.
    Empfohlene Betriebsfrequenz = 85 % der MUF (±1 Bandstufe Puffer).
    """
    RE    = 6371.0
    H_F2  = 350.0
    FO_F2 = float(fo_f2) if fo_f2 is not None else 7.5

    f2_max = 2.0 * math.sqrt(2.0 * RE * H_F2)   # ≈ 4220 km
    hops_f2 = max(1, math.ceil(dist_km / f2_max)) if dist_km > 0 else 1

    # MUF für diesen Pfad
    if dist_km > 50:
        d_hop = dist_km / hops_f2
        sin_t = (2.0 * H_F2) / math.sqrt(d_hop ** 2 + (2.0 * H_F2) ** 2)
        muf   = FO_F2 / max(sin_t, 0.05)
    else:
        muf = FO_F2 * 1.05

    # ── Kurze Entfernungen: VHF bevorzugt ────────────────────────────────
    if dist_km < 80 and not los_blocked:
        if dist_km < 30:
            return dict(band="70 cm", freq_str="430–440 MHz",
                        mode="Sichtverbindung", muf=None, color="#5cb85c")
        return dict(band="2 m", freq_str="144–146 MHz",
                    mode="VHF Sichtverbindung", muf=None, color="#5cb85c")

    # ── NVIS-Zone: kurze bis mittlere KW-Strecken ────────────────────────
    if dist_km <= 200:
        return dict(band="80 m", freq_str="3.5–3.8 MHz",
                    mode="NVIS (F2-Schicht)", muf=round(muf, 1), color="#5cb85c")
    if dist_km <= 500:
        return dict(band="40 m", freq_str="7.0–7.3 MHz",
                    mode="NVIS / F2-Schicht", muf=round(muf, 1), color="#5cb85c")

    # ── Längere Strecken: MUF-basierte Empfehlung (nur HF + 6 m) ───────────
    opt = muf * 0.85   # optimale Betriebsfrequenz ≈ 85 % MUF

    best: Optional[Tuple[float, str, float, float]] = None
    for band in _AMATEUR_BANDS_SKYWAVE:
        if band[0] <= opt:
            best = band

    if best is None:
        best = _AMATEUR_BANDS_SKYWAVE[0]   # Fallback: 160 m

    cf, name, lo, hi = best
    if hi - lo > 1.0:
        freq_str = f"{lo:.0f}–{hi:.0f} MHz"
    else:
        freq_str = f"{lo:.3f}–{hi:.3f} MHz"

    hop_str = f"F2 {hops_f2}× Sprung"
    color   = "#5cb85c" if muf >= 10 else "#f0ad4e"

    return dict(band=name, freq_str=freq_str, mode=hop_str,
                muf=round(muf, 1), color=color)


# ── NOAA SWPC Live-Daten (F10.7 → foF2) ───────────────────────────────────

_SWPC_F107_URL = "https://services.swpc.noaa.gov/json/f107_cm_flux.json"


def _f107_to_fo_f2(flux: float) -> float:
    """F10.7 Solar Flux (sfu) → foF2 (MHz) für mittlere Breiten, vereinfacht."""
    f = max(65.0, min(300.0, float(flux)))
    return round(2.8 + 0.035 * f, 1)


class _SwpcFetchThread(QThread):
    """Holt F10.7 von NOAA SWPC und berechnet foF2/foE für Live-Prognose."""
    data_ready = Signal(dict)
    error_occurred = Signal(str)

    def run(self) -> None:
        try:
            req = Request(_SWPC_F107_URL, headers={"User-Agent": "RotorTcpBridge/1.0"})
            with urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
            if not isinstance(data, list) or not data:
                self.error_occurred.emit("Keine F10.7-Daten erhalten.")
                return
            entry = data[0]
            flux = float(entry.get("flux", 0) or 0)
            if flux <= 0:
                self.error_occurred.emit("Ungültiger F10.7-Wert.")
                return
            fo_f2 = _f107_to_fo_f2(flux)
            fo_e = round(fo_f2 * 0.5, 1)
            self.data_ready.emit({"flux": flux, "fo_f2": fo_f2, "fo_e": fo_e})
        except Exception as exc:
            self.error_occurred.emit(str(exc))


# ── Hintergrund-Thread ────────────────────────────────────────────────────

class _FetchThread(QThread):
    data_ready     = Signal(list)
    error_occurred = Signal(str)

    def __init__(self, points: List[Tuple[float, float]]) -> None:
        super().__init__()
        self._points = points

    def run(self) -> None:
        try:
            locations = "|".join(f"{lat:.6f},{lon:.6f}" for lat, lon in self._points)
            url = f"https://api.opentopodata.org/v1/srtm90m?locations={locations}"
            req = Request(url, headers={"User-Agent": "RotorTcpBridge/1.0"})
            with urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode())
            results = data.get("results", [])
            if not results:
                self.error_occurred.emit("Keine Höhendaten erhalten.")
                return
            self.data_ready.emit([float(r.get("elevation") or 0.0) for r in results])
        except Exception as exc:  # noqa: BLE001
            self.error_occurred.emit(str(exc))


# ── Höhenprofil-Dialog ────────────────────────────────────────────────────

class ElevationProfileWindow(QDialog):
    """Zeigt Höhenprofil und Knife-Edge-Beugungsanalyse (ITU-R P.526)."""

    def __init__(
        self,
        home_lat: float,
        home_lon: float,
        target_lat: float,
        target_lon: float,
        home_name: str = "Antenne",
        target_name: str = "Ziel",
        antenna_height_m: float = 0.0,
        freq_mhz: float = 145.0,
        cfg: Optional[Dict] = None,
        save_cfg_cb=None,
        dark: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._home_lat       = home_lat
        self._home_lon       = home_lon
        self._target_lat     = target_lat
        self._target_lon     = target_lon
        self._home_name      = home_name
        self._target_name    = target_name
        self._antenna_height = antenna_height_m
        self._freq_mhz       = freq_mhz
        self._cfg            = cfg
        self._save_cfg_cb    = save_cfg_cb
        self._dark           = dark
        self._dist_km        = _haversine_km(home_lat, home_lon, target_lat, target_lon)
        self._thread: Optional[_FetchThread] = None
        self._swpc_data: Optional[Dict] = None
        self._swpc_thread: Optional[_SwpcFetchThread] = None
        self._live_preferred_when_online = bool((cfg or {}).get("ui", {}).get("elevation_live_swpc", False))

        # Zuletzt geladene Daten (für Neuberechnung bei Frequenzänderung)
        self._last_elev: Optional[List[float]] = None
        self._last_dists: Optional[List[float]] = None
        self._last_los: Optional[List[float]]  = None

        self.setWindowTitle(t("elevation.title", home=home_name, target=target_name))
        self.resize(960, 680)
        self.setWindowFlags(
            Qt.WindowType.Window | Qt.WindowType.WindowCloseButtonHint
            | Qt.WindowType.WindowMinimizeButtonHint | Qt.WindowType.WindowMaximizeButtonHint
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(5)

        # ── Zeile: Frequenz | Status | Beugungszusammenfassung ───────────
        row = QHBoxLayout()
        row.addWidget(QLabel(t("elevation.freq_label")))
        self._sp_freq = QDoubleSpinBox()
        self._sp_freq.setRange(0.1, 3000.0)
        self._sp_freq.setDecimals(3)
        self._sp_freq.setSingleStep(1.0)
        self._sp_freq.setValue(freq_mhz)
        self._sp_freq.setSuffix(" MHz")
        self._sp_freq.setFixedWidth(130)
        self._sp_freq.setToolTip(t("elevation.freq_tooltip"))
        self._sp_freq.valueChanged.connect(self._on_freq_changed)
        row.addWidget(self._sp_freq)
        row.addSpacing(8)
        self._chk_live = QCheckBox(t("elevation.chk_live"))
        self._chk_live.setToolTip(t("elevation.chk_live_tooltip"))
        live_on = bool((self._cfg or {}).get("ui", {}).get("elevation_live_swpc", False))
        self._chk_live.setChecked(live_on)
        self._chk_live.stateChanged.connect(self._on_live_changed)
        row.addWidget(self._chk_live)
        row.addSpacing(12)
        # Ionosphärische Ausbreitung – kompakte Info neben der Frequenzeingabe
        self._lbl_skywave = QLabel()
        self._lbl_skywave.setTextFormat(Qt.TextFormat.RichText)
        self._lbl_skywave.setStyleSheet("font-size: 11px;")
        row.addWidget(self._lbl_skywave)
        row.addSpacing(8)
        self._lbl_status = QLabel(t("elevation.status_loading"))
        self._lbl_status.setStyleSheet("color: gray; font-style: italic;")
        row.addWidget(self._lbl_status)
        if live_on:
            self._fetch_swpc()
        row.addStretch()
        root.addLayout(row)

        # ── Chart ─────────────────────────────────────────────────────────
        self._view = QWebEngineView()
        self._view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._view.setHtml(self._loading_html())
        root.addWidget(self._view)

        self._fetch()

    # ── Ziel aktualisieren (Live-Update bei Kartenklick) ─────────────────

    def update_target(
        self,
        home_lat: float, home_lon: float,
        target_lat: float, target_lon: float,
    ) -> None:
        """Neues Ziel setzen und Profil neu laden (ohne Fenster schließen)."""
        self._home_lat   = home_lat
        self._home_lon   = home_lon
        self._target_lat = target_lat
        self._target_lon = target_lon
        self._dist_km    = _haversine_km(home_lat, home_lon, target_lat, target_lon)

        # Alte Daten verwerfen, neu laden
        self._last_elev  = None
        self._last_dists = None
        self._last_los   = None
        self._view.setHtml(self._loading_html())
        self._fetch()

    # ── Theme-Wechsel (Dark ↔ Light) ─────────────────────────────────────

    def apply_theme(self, dark: bool) -> None:
        """Dark/Light-Mode zur Laufzeit umschalten und Chart neu rendern."""
        if dark == self._dark:
            return
        self._dark = dark
        if self._last_elev is not None:
            self._rebuild_chart()
        else:
            self._view.setHtml(self._loading_html())

    # ── Live SWPC ─────────────────────────────────────────────────────────

    def apply_internet_status(self, online: bool) -> None:
        """Live-Checkbox je nach Internetstatus automatisch umschalten."""
        self._chk_live.blockSignals(True)
        if online:
            self._chk_live.setChecked(self._live_preferred_when_online)
            if self._cfg is not None:
                self._cfg.setdefault("ui", {})["elevation_live_swpc"] = self._live_preferred_when_online
                if self._save_cfg_cb:
                    self._save_cfg_cb(self._cfg)
            if self._chk_live.isChecked():
                self._fetch_swpc()
        else:
            self._live_preferred_when_online = self._chk_live.isChecked()
            self._chk_live.setChecked(False)
            self._swpc_data = None
            if self._cfg is not None:
                self._cfg.setdefault("ui", {})["elevation_live_swpc"] = False
                if self._save_cfg_cb:
                    self._save_cfg_cb(self._cfg)
            if self._last_elev is not None:
                self._rebuild_chart()
        self._chk_live.blockSignals(False)

    def _on_live_changed(self, _state: int) -> None:
        on = self._chk_live.isChecked()
        self._live_preferred_when_online = on
        if self._cfg is not None:
            self._cfg.setdefault("ui", {})["elevation_live_swpc"] = on
            if self._save_cfg_cb:
                self._save_cfg_cb(self._cfg)
        if on:
            self._fetch_swpc()
        else:
            self._swpc_data = None
            if self._last_elev is not None:
                self._rebuild_chart()

    def _fetch_swpc(self) -> None:
        if self._swpc_thread and self._swpc_thread.isRunning():
            return
        self._lbl_status.setText(t("elevation.status_swpc"))
        self._lbl_status.setStyleSheet("color: gray; font-style: italic;")
        self._swpc_thread = _SwpcFetchThread()
        self._swpc_thread.data_ready.connect(self._on_swpc_data)
        self._swpc_thread.error_occurred.connect(self._on_swpc_error)
        self._swpc_thread.start()

    def _on_swpc_data(self, data: Dict) -> None:
        self._swpc_data = data
        self._lbl_status.setText("")
        if self._last_elev is not None:
            self._rebuild_chart()

    def _on_swpc_error(self, msg: str) -> None:
        self._swpc_data = None
        self._lbl_status.setText(t("elevation.status_swpc_error", msg=msg))
        self._lbl_status.setStyleSheet("color: #f0ad4e;")
        if self._last_elev is not None:
            self._rebuild_chart()

    # ── Frequenzänderung ──────────────────────────────────────────────────

    def _on_freq_changed(self, value: float) -> None:
        self._freq_mhz = value
        # Frequenz persistent speichern
        if self._cfg is not None:
            self._cfg.setdefault("ui", {})["rf_freq_mhz"] = value
            if self._save_cfg_cb:
                self._save_cfg_cb(self._cfg)
        if self._last_elev is not None:
            self._rebuild_chart()

    # ── Datenabruf ────────────────────────────────────────────────────────

    def _fetch(self) -> None:
        if self._thread and self._thread.isRunning():
            self._thread.terminate()
            self._thread.wait()
        self._lbl_status.setText(t("elevation.status_opentopo"))
        self._lbl_status.setStyleSheet("color: gray; font-style: italic;")

        points = _great_circle_sample(
            self._home_lat, self._home_lon,
            self._target_lat, self._target_lon,
            _N_POINTS,
        )
        self._thread = _FetchThread(points)
        self._thread.data_ready.connect(self._on_data)
        self._thread.error_occurred.connect(self._on_error)
        self._thread.start()

    def _on_data(self, elevations: List[float]) -> None:
        self._lbl_status.setText("")
        n  = len(elevations)
        h0 = elevations[0]
        hn = elevations[-1]
        los_start = h0 + self._antenna_height
        dists = [round(self._dist_km * i / max(n - 1, 1), 3) for i in range(n)]
        los   = [round(los_start + (hn - los_start) * i / max(n - 1, 1), 1)
                 for i in range(n)]
        self._last_elev  = elevations
        self._last_dists = dists
        self._last_los   = los
        self._rebuild_chart()

    def _on_error(self, msg: str) -> None:
        self._lbl_status.setText("")
        self._lbl_status.setStyleSheet("")
        # Offline: Band-Empfehlung und Ausbreitungsmodi mit Standardwerten anzeigen
        self._view.setHtml(self._build_offline_html())
        # Skywave-Label mit Standardwerten aktualisieren
        fo_f2 = None
        fo_e = None
        sky = _sky_wave_analysis(self._dist_km, self._freq_mhz, fo_f2=fo_f2, fo_e=fo_e)
        best = _best_freq_recommendation(self._dist_km, los_blocked=False, fo_f2=fo_f2)
        bc = best["color"]
        parts = []
        if sky:
            nvis = " · NVIS" if sky["nvis_ok"] else ""
            fo = f"foF₂≈{sky['fo_f2']:.0f} MHz"
            parts.append(f"<span style='opacity:0.55'>Ionosph. · {sky['band_name']}{nvis} · {fo}</span>")
        muf_str = f" · MUF≈{best['muf']} MHz" if best.get("muf") else ""
        parts.append(
            f"&nbsp;&nbsp;<b style='color:{bc}'>{best['band']}</b>"
            f"&nbsp;<span style='opacity:0.7'>{best['freq_str']} · {best['mode']}{muf_str}</span>"
        )
        self._lbl_skywave.setText("&nbsp;&nbsp;".join(parts))

    def _rebuild_chart(self) -> None:
        """Berechnet Beugungsanalyse neu und aktualisiert Chart + Qt-Infoleiste."""
        if (self._last_elev is None or self._last_dists is None or self._last_los is None):
            return
        elevations = self._last_elev
        dists = self._last_dists
        los = self._last_los
        h0 = elevations[0]
        hn = elevations[-1]

        diff = _knife_edge_analysis(elevations, los, dists, self._freq_mhz)

        # ── Qt-Label: Ionosphärische Info neben Frequenzeingabe ──────────
        swpc = self._swpc_data
        fo_f2 = float(swpc["fo_f2"]) if swpc else None
        fo_e = float(swpc["fo_e"]) if swpc else None
        sky  = _sky_wave_analysis(self._dist_km, self._freq_mhz, fo_f2=fo_f2, fo_e=fo_e)
        los_blocked = any(e > l for e, l in list(zip(elevations, los))[1:-1])
        best = _best_freq_recommendation(self._dist_km, los_blocked, fo_f2=fo_f2)
        bc   = best["color"]

        parts = []
        if sky:
            nvis = " · NVIS" if sky["nvis_ok"] else ""
            live_tag = " · Live" if swpc else ""
            fo   = f"foF₂≈{sky['fo_f2']:.0f} MHz{live_tag}"
            parts.append(
                f"<span style='opacity:0.55'>"
                f"Ionosph. · {sky['band_name']}{nvis} · {fo}</span>"
            )
        muf_str = f" · MUF≈{best['muf']} MHz" if best.get("muf") else ""
        parts.append(
            f"&nbsp;&nbsp;<b style='color:{bc}'>{best['band']}</b>"
            f"&nbsp;<span style='opacity:0.7'>{best['freq_str']} · {best['mode']}{muf_str}</span>"
        )
        self._lbl_skywave.setText("&nbsp;&nbsp;".join(parts))

        self._view.setHtml(
            self._build_html(dists, elevations, los, h0, hn, diff)
        )

    # ── HTML-Generierung ─────────────────────────────────────────────────

    def _css_vars(self) -> dict:
        if self._dark:
            return dict(
                bg="#1c1c1c", fg="#e1e1e1",
                card_bg="#2d2d2d", card_border="#3a3a3a",
                grid="#333333",
                elev_color="#4496eb", elev_fill="rgba(68,150,235,0.18)",
                los_color="#e07050",
                horizon_color="#7eb87e",
            )
        return dict(
            bg="#f0f0f0", fg="#1a1a1a",
            card_bg="#ffffff", card_border="#d0d0d0",
            grid="#e0e0e0",
            elev_color="#0078d4", elev_fill="rgba(0,120,212,0.12)",
            los_color="#c0392b",
            horizon_color="#2e7d32",
        )

    def _loading_html(self) -> str:
        v = self._css_vars()
        return (
            f"<!DOCTYPE html><html><body style='margin:0;background:{v['bg']};"
            f"color:{v['fg']};font-family:sans-serif;display:flex;"
            f"align-items:center;justify-content:center;height:100vh;'>"
            f"<div style='font-size:18px;opacity:0.6'>{t('elevation.status_loading')}</div>"
            f"</body></html>"
        )

    def _error_html(self, msg: str, show_internet_hint: bool = True) -> str:
        v = self._css_vars()
        ec = "#e07050" if self._dark else "#c0392b"
        hint = f"<div style='font-size:12px;opacity:0.6'>{t('elevation.internet_check')}</div>" if show_internet_hint else ""
        return (
            f"<!DOCTYPE html><html><body style='margin:0;background:{v['bg']};"
            f"color:{ec};font-family:sans-serif;display:flex;flex-direction:column;"
            f"align-items:center;justify-content:center;height:100vh;gap:12px;'>"
            f"<div style='font-size:48px'>⚠</div>"
            f"<div style='font-size:15px;max-width:500px;text-align:center'>{msg}</div>"
            f"{hint}"
            f"</body></html>"
        )

    def _build_offline_html(self) -> str:
        """Offline-Ansicht: Band-Empfehlung und Ausbreitungsmodi mit Standardwerten (ohne Höhenprofil)."""
        _tr = t
        v = self._css_vars()
        fo_f2 = None
        fo_e = None
        sky = _sky_wave_analysis(self._dist_km, self._freq_mhz, fo_f2=fo_f2, fo_e=fo_e)
        best = _best_freq_recommendation(self._dist_km, los_blocked=False, fo_f2=fo_f2)
        bc = best["color"]
        muf_note = f"&nbsp;·&nbsp;MUF ≈ {best['muf']} MHz" if best.get("muf") else ""
        opt_label = _tr("elevation.opt_band_mean")
        opt_card = (
            f'<div class="card" style="border-color:{bc};border-width:2px">'
            f'<div class="card-label" style="color:{bc}">{opt_label}</div>'
            f'<div class="card-value" style="font-size:18px">{best["band"]}</div>'
            f'<div style="font-size:10px;opacity:0.75;margin-top:2px">'
            f'{best["freq_str"]}&nbsp;·&nbsp;{best["mode"]}{muf_note}</div>'
            f'</div>'
        )
        skywave_section = ""
        if sky:
            mode_cards_html = ""
            for m in sky["modes"]:
                ok_col = "#5cb85c" if m["ok"] else "#f0ad4e"
                name = _tr(m["name_key"], **m.get("name_params", {}))
                sub = _tr(m["sub_key"], **m.get("sub_params", {}))
                mode_cards_html += (
                    f'<div class="card">'
                    f'<div class="card-label" style="color:{ok_col}">{name}</div>'
                    f'<div class="card-value">{sub}</div>'
                    f'</div>'
                )
            if sky["skip_zone"] and not sky["modes"]:
                skip_col = "#e07050" if self._dark else "#c0392b"
                mode_cards_html += (
                    f'<div class="card" style="border-color:{skip_col}55">'
                    f'<div class="card-label" style="color:{skip_col}">{_tr("elevation.skip_zone")}</div>'
                    f'<div class="card-value" style="color:{skip_col}">'
                    f'{_tr("elevation.skip_zone_desc")}</div></div>'
                )
            skywave_section = (
                f'<div class="divider"></div>'
                f'<div class="cards">{mode_cards_html}</div>'
            )
        skywave_section = (
            skywave_section[:-len('</div>')] + opt_card + '</div>'
            if skywave_section
            else f'<div class="divider"></div><div class="cards">{opt_card}</div>'
        )
        offline_msg = _tr("elevation.offline_no_profile").replace("\n", "<br>")
        return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
html, body {{ height: 100%; background: {v['bg']}; color: {v['fg']};
  font-family: 'Segoe UI', Arial, sans-serif; font-size: 13px;
  display: flex; flex-direction: column; padding: 8px; gap: 8px; }}
.cards {{ display: flex; gap: 6px; flex-wrap: wrap; flex-shrink: 0; }}
.card {{ background: {v['card_bg']}; border: 1px solid {v['card_border']};
  border-radius: 7px; padding: 6px 12px; flex: 1; min-width: 100px; }}
.card-label {{ font-size: 10px; opacity: 0.6; text-transform: uppercase; letter-spacing: 0.5px; }}
.card-value {{ font-size: 16px; font-weight: bold; margin-top: 2px; }}
.divider {{ width: 100%; height: 1px; background: {v['card_border']}; flex-shrink: 0; }}
.chart-placeholder {{ flex: 1; display: flex; align-items: center; justify-content: center;
  background: {v['card_bg']}; border: 1px dashed {v['card_border']}; border-radius: 8px;
  color: {v['fg']}; opacity: 0.7; font-size: 14px; text-align: center; padding: 24px; }}
</style>
</head>
<body>
<div class="cards">
  <div class="card">
    <div class="card-label">{self._home_name}</div>
    <div class="card-value">{self._home_lat:.5f}, {self._home_lon:.5f}</div>
  </div>
  <div class="card">
    <div class="card-label">{self._target_name}</div>
    <div class="card-value">{self._target_lat:.5f}, {self._target_lon:.5f}</div>
  </div>
  <div class="card">
    <div class="card-label">{_tr('elevation.card_distance')}</div>
    <div class="card-value">{self._dist_km:.1f} km</div>
  </div>
</div>
{skywave_section}
<div class="chart-placeholder">{offline_msg}</div>
</body>
</html>"""

    def _build_html(
        self,
        dists: List[float],
        elevations: List[float],
        los: List[float],
        h0: float,
        hn: float,
        diff: Optional[Dict],
    ) -> str:
        _tr = t
        v    = self._css_vars()
        hmax = max(elevations)
        hmin = min(elevations)

        h0_antenna  = h0 + self._antenna_height
        height_note = f" (+{self._antenna_height:.0f} m Mast)" if self._antenna_height > 0 else ""

        # Horizontdistanz: d_h = sqrt(2*R*h), R=6371 km
        R_km = 6371.0
        horizon_dist_km = math.sqrt(2.0 * R_km * (h0_antenna / 1000.0))
        horizon_idx = min(
            range(len(dists)),
            key=lambda i: abs(dists[i] - horizon_dist_km),
            default=0,
        )
        if horizon_dist_km > self._dist_km * 1.05:
            horizon_idx = None  # Horizont außerhalb des Profils

        # ── Infokarten ────────────────────────────────────────────────────
        diff_cards = ""
        annotation_js = "{}"     # Chart.js annotation – wird bei Hindernis befüllt

        if diff:
            qcol  = diff["quality_color"]
            j_str = f"{diff['j_db']:.1f}"
            p_str = f"{diff['power_pct']:.1f}"
            nu_str = f"{diff['nu']:.2f}"
            obs_dist = f"{diff['obstacle_dist']:.1f}"
            h_str = f"{diff['h_m']:.0f}"

            diff_cards = f"""
  <div class="card card-diff" style="border-color:{qcol}33;">
    <div class="card-label">{_tr('elevation.diff_j')}</div>
    <div class="card-value">{j_str} dB</div>
  </div>
  <div class="card card-diff" style="border-color:{qcol}33;">
    <div class="card-label">{_tr('elevation.diff_nu', dist=obs_dist)}</div>
    <div class="card-value">{nu_str}</div>
  </div>
  <div class="card card-diff" style="border-color:{qcol}33;">
    <div class="card-label">{_tr('elevation.diff_power')}</div>
    <div class="card-value">{p_str} %</div>
  </div>
  <div class="card card-diff" style="border-left: 3px solid {qcol}; border-color:{qcol};">
    <div class="card-label">{_tr('elevation.diff_rating')}</div>
    <div class="card-value" style="font-size:13px;padding-top:3px;color:{qcol}">{_tr(diff['quality_key'])}</div>
  </div>"""

            # Chart.js Annotation für Haupthindernis
            obs_idx   = diff["obstacle_idx"]
            ann_color = diff["quality_color"]
            annotation_js = f"""{{
              obstacle: {{
                type: 'line',
                scaleID: 'x',
                value: {obs_idx},
                borderColor: '{ann_color}',
                borderWidth: 1.5,
                borderDash: [5, 4],
                label: {{
                  display: true,
                  content: {json.dumps(_tr("elevation.obstacle_label", h=h_str))},
                  position: 'start',
                  yAdjust: 6,
                  color: '{ann_color}',
                  backgroundColor: '{v['card_bg']}cc',
                  font: {{ size: 11 }},
                  padding: 4,
                  borderRadius: 4
                }}
              }},
              horizon: {{
                type: 'line',
                scaleID: 'x',
                value: {horizon_idx if horizon_idx is not None else -1},
                borderColor: '{v['horizon_color']}',
                borderWidth: 1,
                borderDash: [3, 3],
                display: {str(horizon_idx is not None).lower()},
                label: {{
                  display: true,
                  content: {json.dumps(_tr("elevation.horizon_label", km=f"{horizon_dist_km:.1f}"))},
                  position: 'start',
                  yAdjust: -6,
                  color: '{v['horizon_color']}',
                  backgroundColor: '{v['card_bg']}cc',
                  font: {{ size: 10 }},
                  padding: 3,
                  borderRadius: 3
                }}
              }}
            }}"""
        elif horizon_idx is not None:
            annotation_js = f"""{{
              horizon: {{
                type: 'line',
                scaleID: 'x',
                value: {horizon_idx},
                borderColor: '{v['horizon_color']}',
                borderWidth: 1,
                borderDash: [3, 3],
                label: {{
                  display: true,
                  content: {json.dumps(_tr("elevation.horizon_label", km=f"{horizon_dist_km:.1f}"))},
                  position: 'start',
                  yAdjust: -6,
                  color: '{v['horizon_color']}',
                  backgroundColor: '{v['card_bg']}cc',
                  font: {{ size: 10 }},
                  padding: 3,
                  borderRadius: 3
                }}
              }}
            }}"""

        # Sichtlinie-Status (einfach, für Karten ohne diff)
        blocked = any(e > l for e, l in list(zip(elevations, los))[1:-1])
        los_txt = (
            f'<span style="color:#e07050">{_tr("elevation.los_blocked")}</span>'
            if blocked else
            f'<span style="color:#5cb85c">{_tr("elevation.los_free")}</span>'
        )

        # ── Ionosphärische Ausbreitung (nur KW < 30 MHz) ─────────────────
        swpc = self._swpc_data
        fo_f2 = float(swpc["fo_f2"]) if swpc else None
        fo_e = float(swpc["fo_e"]) if swpc else None
        sky = _sky_wave_analysis(self._dist_km, self._freq_mhz, fo_f2=fo_f2, fo_e=fo_e)
        skywave_section = ""
        if sky:
            mode_cards_html = ""
            for m in sky["modes"]:
                ok_col = "#5cb85c" if m["ok"] else "#f0ad4e"
                name = _tr(m["name_key"], **m.get("name_params", {}))
                sub = _tr(m["sub_key"], **m.get("sub_params", {}))
                mode_cards_html += (
                    f'<div class="card">'
                    f'<div class="card-label" style="color:{ok_col}">{name}</div>'
                    f'<div class="card-value">{sub}</div>'
                    f'</div>'
                )
            if sky["skip_zone"] and not sky["modes"]:
                skip_col = "#e07050" if self._dark else "#c0392b"
                mode_cards_html += (
                    f'<div class="card" style="border-color:{skip_col}55">'
                    f'<div class="card-label" style="color:{skip_col}">{_tr("elevation.skip_zone")}</div>'
                    f'<div class="card-value" style="color:{skip_col}">'
                    f'{_tr("elevation.skip_zone_desc")}</div></div>'
                )
            skywave_section = (
                f'<div class="divider"></div>'
                f'<div class="cards">{mode_cards_html}</div>'
            )

        # ── Optimales Band (immer anzeigen, unabhängig von akt. Frequenz) ─
        los_blocked = any(e > l for e, l in list(zip(elevations, los))[1:-1])
        best = _best_freq_recommendation(self._dist_km, los_blocked, fo_f2=fo_f2)
        bc   = best["color"]
        muf_note = f"&nbsp;·&nbsp;MUF ≈ {best['muf']} MHz" if best.get("muf") else ""
        opt_label = _tr("elevation.opt_band_live") if fo_f2 is not None else _tr("elevation.opt_band_mean")
        opt_card = (
            f'<div class="card" style="border-color:{bc};border-width:2px">'
            f'<div class="card-label" style="color:{bc}">{opt_label}</div>'
            f'<div class="card-value" style="font-size:18px">{best["band"]}</div>'
            f'<div style="font-size:10px;opacity:0.75;margin-top:2px">'
            f'{best["freq_str"]}&nbsp;·&nbsp;{best["mode"]}{muf_note}</div>'
            f'</div>'
        )
        # Optimal-Karte in den sky-wave-Abschnitt anhängen oder als eigenen Block
        if skywave_section:
            # An bestehende Karten-Zeile anhängen
            skywave_section = skywave_section[:-len('</div>')] + opt_card + '</div>'
        else:
            # Eigener Abschnitt wenn Frequenz ≥ 30 MHz
            skywave_section = (
                f'<div class="divider"></div>'
                f'<div class="cards">{opt_card}</div>'
            )

        dists_json = json.dumps(dists)
        elev_json  = json.dumps(elevations)
        los_json   = json.dumps(los)

        # ── Hover-Analyse: Punkt i als hypothetisches Ziel ───────────────
        # Für jeden Hover-Punkt i: schlechtestes Hindernis auf dem Teilpfad
        # von der Antenne bis zu diesem Punkt, als wäre i das Ziel.
        # Zusätzlich: reale Clearance dieses Punktes zur echten Sichtlinie.
        lam_m_pp   = 3e8 / (self._freq_mhz * 1e6)
        los_start_pp = elevations[0] + self._antenna_height
        per_point: list = []

        for i in range(len(elevations)):
            if i < 2 or dists[i] <= 0:
                per_point.append(None)
                continue

            dist_i   = dists[i]
            los_end_pp = elevations[i]   # Ziel-Höhe = Gelände an Punkt i

            # Reale Clearance dieses Punktes zur echten Sichtlinie (zum tatsächlichen Ziel)
            real_clearance = round(los[i] - elevations[i], 1)  # positiv = unter LOS

            # Schlechtestes Hindernis auf dem Teilpfad 0 → i (virtuelle LOS zum Hover-Punkt)
            max_h = -1e9
            max_j = 1
            for j in range(1, i):
                t_frac = dists[j] / dist_i
                los_j = los_start_pp + (los_end_pp - los_start_pp) * t_frac
                h_j   = elevations[j] - los_j
                if h_j > max_h:
                    max_h = h_j
                    max_j = j

            d1_km = dists[max_j]
            d2_km = dists[i] - dists[max_j]
            if d1_km <= 0 or d2_km <= 0:
                per_point.append(None)
                continue

            d1_m  = d1_km * 1000.0
            d2_m  = d2_km * 1000.0
            nu_pp = max_h * math.sqrt(2.0 * (d1_m + d2_m) / (lam_m_pp * d1_m * d2_m))
            if nu_pp <= -0.78 or max_h <= 0:
                j_pp = 0.0
            else:
                arg_pp = math.sqrt((nu_pp - 0.1) ** 2 + 1.0) + nu_pp - 0.1
                j_pp   = max(0.0, 6.9 + 20.0 * math.log10(max(arg_pp, 1e-12)))
            pct_pp = 10.0 ** (-j_pp / 10.0) * 100.0

            per_point.append({
                "h":            round(max_h, 1),
                "nu":           round(nu_pp, 3),
                "jdb":          round(j_pp, 1),
                "pct":          round(pct_pp, 1),
                "obsDist":      round(dists[max_j], 1),
                "dist":         round(dist_i, 1),
                "realClear":    real_clearance,   # Freistand zur echten Sichtlinie
                "realLos":      round(los[i], 1), # Echte LOS-Höhe an diesem Punkt
            })
        per_point_json = json.dumps(per_point)

        return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
html, body {{
  height: 100%;
  background: {v['bg']};
  color: {v['fg']};
  font-family: 'Segoe UI', Arial, sans-serif;
  font-size: 13px;
  display: flex;
  flex-direction: column;
  padding: 8px;
  gap: 8px;
}}
.cards {{
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  flex-shrink: 0;
}}
.card {{
  background: {v['card_bg']};
  border: 1px solid {v['card_border']};
  border-radius: 7px;
  padding: 6px 12px;
  flex: 1;
  min-width: 100px;
}}
.card-label {{ font-size: 10px; opacity: 0.6; text-transform: uppercase; letter-spacing: 0.5px; }}
.card-value {{ font-size: 16px; font-weight: bold; margin-top: 2px; }}
.section-title {{
  font-size: 10px;
  opacity: 0.5;
  text-transform: uppercase;
  letter-spacing: 0.7px;
  flex-shrink: 0;
}}
.divider {{ width: 100%; height: 1px; background: {v['card_border']}; flex-shrink: 0; }}
.chart-wrap {{ flex: 1; position: relative; min-height: 0; }}
canvas {{ position: absolute; top: 0; left: 0; width: 100% !important; height: 100% !important; }}
</style>
</head>
<body>
<div class="cards">
  <div class="card">
    <div class="card-label">{self._home_name}{height_note}</div>
    <div class="card-value">{h0_antenna:.0f} m</div>
  </div>
  <div class="card">
    <div class="card-label">{self._target_name}</div>
    <div class="card-value">{hn:.0f} m</div>
  </div>
  <div class="card">
    <div class="card-label">{_tr('elevation.card_max_terrain')}</div>
    <div class="card-value">{hmax:.0f} m</div>
  </div>
  <div class="card">
    <div class="card-label">{_tr('elevation.card_distance')}</div>
    <div class="card-value">{self._dist_km:.1f} km</div>
  </div>
  <div class="card">
    <div class="card-label">{_tr('elevation.card_los')}</div>
    <div class="card-value" style="font-size:13px;padding-top:3px;">{los_txt}</div>
  </div>
</div>
<div class="divider"></div>
<div class="cards">{diff_cards}</div>
{skywave_section}
<div class="chart-wrap">
  <canvas id="c"></canvas>
</div>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-annotation@3.0.1/dist/chartjs-plugin-annotation.min.js"></script>
<script>
const dists    = {dists_json};
const elev     = {elev_json};
const los      = {los_json};
const perPoint = {per_point_json};

const qNone = {json.dumps(_tr("elevation.quality_none"))};
const qLight = {json.dumps(_tr("elevation.quality_light"))};
const qMod = {json.dumps(_tr("elevation.quality_moderate"))};
const qStrong = {json.dumps(_tr("elevation.quality_strong"))};
const qVery = {json.dumps(_tr("elevation.quality_very_strong"))};
function qualityLabel(jdb) {{
  if (jdb < 1.0)  return qNone;
  if (jdb < 6.0)  return qLight;
  if (jdb < 10.0) return qMod;
  if (jdb < 16.0) return qStrong;
  return qVery;
}}
function qualityColor(pp) {{
  if (!pp) return '{v['fg']}';
  if (pp.jdb < 1.0)  return '#5cb85c';
  if (pp.jdb < 6.0)  return '#8bc34a';
  if (pp.jdb < 10.0) return '#f0ad4e';
  if (pp.jdb < 16.0) return '#e07050';
  return '#c0392b';
}}

// Plugin registriert sich via CDN automatisch – kein Chart.register() nötig.
new Chart(document.getElementById('c'), {{
  type: 'line',
  data: {{
    labels: dists,
    datasets: [
      {{
        label: {json.dumps(_tr("elevation.chart_terrain"))},
        data: elev,
        borderColor: '{v['elev_color']}',
        backgroundColor: '{v['elev_fill']}',
        fill: true,
        pointRadius: 0,
        borderWidth: 2,
        tension: 0.3,
        order: 2
      }},
      {{
        label: {json.dumps(_tr("elevation.chart_los"))},
        data: los,
        borderColor: '{v['los_color']}',
        borderDash: [7, 4],
        backgroundColor: 'transparent',
        fill: false,
        pointRadius: 0,
        borderWidth: 1.5,
        order: 1
      }}
    ]
  }},
  options: {{
    responsive: true,
    maintainAspectRatio: false,
    animation: {{ duration: 300 }},
    interaction: {{ mode: 'index', intersect: false }},
    plugins: {{
      legend: {{
        labels: {{
          color: '{v['fg']}',
          font: {{ size: 12 }},
          boxWidth: 26,
          padding: 12
        }}
      }},
      tooltip: {{
        backgroundColor: '{v['card_bg']}',
        borderColor: '{v['card_border']}',
        borderWidth: 1,
        titleColor: '{v['fg']}',
        bodyColor: '{v['fg']}',
        footerColor: '{v['fg']}',
        padding: 9,
        callbacks: {{
          title: items => ' ' + parseFloat(items[0].label).toFixed(1) + ' km',
          label: ctx => '  ' + ctx.dataset.label + ':  ' + Math.round(ctx.raw) + ' m',
          footer: items => {{
            const pp = perPoint[items[0].dataIndex];
            if (!pp) return [];
            const dot = ['🟢','🟡','🟠','🔴','🔴'];
            const grade = (pp.h <= 0 || pp.jdb < 1) ? 0 : pp.jdb < 6 ? 1 : pp.jdb < 10 ? 2 : pp.jdb < 16 ? 3 : 4;
            const tooltipObstacle = {json.dumps(_tr("elevation.tooltip_obstacle", dist="__DIST__", h="__H__"))};
            const tooltipNoObs = {json.dumps("  " + _tr("elevation.tooltip_no_obstacle"))};
            const tooltipIfTarget = {json.dumps(_tr("elevation.tooltip_if_target", dist="__D__"))};
            const lines = [''];
            // ── Reale Clearance zur echten Ziel-Sichtlinie (primäre Information) ──
            if (pp.realClear !== undefined) {{
              const clr = pp.realClear;
              if (clr >= 0) {{
                lines.push('  ↓ Freistand zur Ziel-LOS:  +' + clr.toFixed(0) + ' m frei');
              }} else {{
                lines.push('  ↑ Hindernis für Ziel:  +' + Math.abs(clr).toFixed(0) + ' m über Ziel-LOS  ⚠');
              }}
            }}
            // ── Hypothetische Analyse: virtuelles Ziel an diesem Punkt ──
            const obsNote = pp.h > 0
              ? tooltipObstacle.replace('__DIST__', pp.obsDist).replace('__H__', pp.h.toFixed(0))
              : tooltipNoObs;
            lines.push('');
            lines.push(tooltipIfTarget.replace('__D__', pp.dist));
            lines.push(obsNote);
            lines.push(
              '  ν = ' + pp.nu.toFixed(2) +
              '    J(ν) = ' + pp.jdb.toFixed(1) + ' dB' +
              '    Signal = ' + pp.pct.toFixed(1) + ' %'
            );
            lines.push('  ' + dot[grade] + '  ' + qualityLabel(pp.jdb));
            return lines;
          }}
        }}
      }},
      annotation: {{
        annotations: {annotation_js}
      }}
    }},
    scales: {{
      x: {{
        ticks: {{
          color: '{v['fg']}',
          maxTicksLimit: 10,
          callback: (_, i) => parseFloat(dists[i]).toFixed(1) + ' km'
        }},
        grid: {{ color: '{v['grid']}' }}
      }},
      y: {{
        ticks: {{
          color: '{v['fg']}',
          callback: v => v + ' m'
        }},
        grid: {{ color: '{v['grid']}' }}
      }}
    }}
  }}
}});
</script>
</body>
</html>"""
