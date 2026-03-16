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
    QDialog,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
)
from PySide6.QtWebEngineWidgets import QWebEngineView

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
    if nu <= -0.78 or j_db < 1.0:
        quality_text  = "Kein Beugungsverlust"
        quality_color = "#5cb85c"
        quality_grade = 5
    elif j_db < 6.0:
        quality_text  = "Leichte Beugung"
        quality_color = "#8bc34a"
        quality_grade = 4
    elif j_db < 10.0:
        quality_text  = "Merkliche Beugung"
        quality_color = "#f0ad4e"
        quality_grade = 3
    elif j_db < 16.0:
        quality_text  = "Starke Beugung"
        quality_color = "#e07050"
        quality_grade = 2
    else:
        quality_text  = "Sehr starke Beugung"
        quality_color = "#c0392b"
        quality_grade = 1

    return {
        "h_m":           max_h,
        "nu":            nu,
        "j_db":          j_db,
        "power_pct":     power_pct,
        "obstacle_idx":  max_idx,
        "obstacle_dist": dists[max_idx],
        "quality_text":  quality_text,
        "quality_color": quality_color,
        "quality_grade": quality_grade,
        "d1_km":         d1_km,
        "d2_km":         d2_km,
        "lam_m":         lam_m,
    }


# ── Ionosphärische Ausbreitungsanalyse (KW < 30 MHz) ─────────────────────

def _sky_wave_analysis(dist_km: float, freq_mhz: float) -> Optional[Dict]:
    """
    Ionosphärische Ausbreitungsanalyse für KW-Frequenzen (< 30 MHz).

    Modell: Flacherde-Näherung, typische Tagesmittelwerte
    (Sonnenflecken-Mittelwert, Mitteleuropa).
    Berücksichtigt Bodenwelle, NVIS, E-Schicht und F2-Schicht.
    """
    if freq_mhz >= 30.0:
        return None

    RE = 6371.0       # Erdradius km
    H_E   = 110.0     # E-Schicht Höhe km
    H_F2  = 350.0     # F2-Schicht Höhe km
    FO_E  = 4.0       # krit. Frequenz E-Schicht (Tagesmittel) MHz
    FO_F2 = 7.5       # krit. Frequenz F2-Schicht (Tagesmittel) MHz

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
            "name": "Bodenwelle",
            "sub":  f"Reichweite ≈ {gw_km:.0f} km",
            "ok":   True,
        })

    if nvis_ok and dist_km <= 500:
        modes.append({
            "name": "NVIS (F2-Schicht)",
            "sub":  "Senkrechter Einfall · 0–500 km",
            "ok":   True,
        })

    if dist_km >= e_min_km and dist_km <= e_max_km * hops_e:
        modes.append({
            "name": f"E-Schicht · {hops_e}× Sprung",
            "sub":  f"Skip {e_min_km:.0f}–{int(e_max_km)} km",
            "ok":   hops_e <= 2,
        })

    f2_reachable = (nvis_ok and dist_km <= 500) or (dist_km >= f2_min_km)
    if f2_reachable:
        if freq_mhz <= muf:
            modes.append({
                "name": f"F2-Schicht · {hops_f2}× Sprung",
                "sub":  f"MUF ≈ {muf:.0f} MHz",
                "ok":   hops_f2 <= 2 and freq_mhz <= muf,
            })
        else:
            modes.append({
                "name": f"F2-Schicht · {hops_f2}× Sprung",
                "sub":  f"Freq > MUF ({muf:.0f} MHz)",
                "ok":   False,
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
        self._dark           = dark
        self._dist_km        = _haversine_km(home_lat, home_lon, target_lat, target_lon)
        self._thread: Optional[_FetchThread] = None

        # Zuletzt geladene Daten (für Neuberechnung bei Frequenzänderung)
        self._last_elev: Optional[List[float]] = None
        self._last_dists: Optional[List[float]] = None
        self._last_los: Optional[List[float]]  = None

        self.setWindowTitle(f"Höhenprofil  –  {home_name}  →  {target_name}")
        self.resize(960, 680)
        self.setWindowFlags(
            Qt.Window | Qt.WindowCloseButtonHint | Qt.WindowMinMaxButtonsHint
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(5)

        # ── Zeile: Frequenz | Streckeninfo | Status | Beugung ────────────
        row = QHBoxLayout()
        row.addWidget(QLabel("Frequenz:"))
        self._sp_freq = QDoubleSpinBox()
        self._sp_freq.setRange(0.1, 3000.0)
        self._sp_freq.setDecimals(3)
        self._sp_freq.setSingleStep(1.0)
        self._sp_freq.setValue(freq_mhz)
        self._sp_freq.setSuffix(" MHz")
        self._sp_freq.setFixedWidth(130)
        self._sp_freq.setToolTip(
            "Sendefrequenz für Ausbreitungsanalyse.\n"
            "VHF/UHF (≥ 30 MHz): Knife-Edge-Beugung nach ITU-R P.526.\n"
            "KW (< 30 MHz): zusätzlich Bodenwelle, NVIS, E- und F2-Schicht,\n"
            "  Skip-Zone und MUF-Abschätzung.\n"
            "Häufige Bänder: 7 MHz (40 m), 14 MHz (20 m),\n"
            "  144 MHz (2 m), 432 MHz (70 cm)"
        )
        self._sp_freq.valueChanged.connect(self._on_freq_changed)
        row.addWidget(self._sp_freq)
        row.addSpacing(12)
        self._lbl_dist = QLabel()
        self._lbl_dist.setTextFormat(Qt.RichText)
        self._lbl_dist.setText(
            f"<b>{home_name}</b> → <b>{target_name}</b>"
            f"&nbsp;&nbsp;&nbsp;Entfernung: <b>{self._dist_km:.1f} km</b>"
        )
        row.addWidget(self._lbl_dist)
        row.addStretch()
        self._lbl_status = QLabel("Lade Höhendaten…")
        self._lbl_status.setStyleSheet("color: gray; font-style: italic;")
        row.addWidget(self._lbl_status)
        # Zusammenfassung der Beugungsanalyse – wird nach Datenladen befüllt
        self._lbl_diff = QLabel()
        self._lbl_diff.setTextFormat(Qt.RichText)
        row.addWidget(self._lbl_diff)
        root.addLayout(row)

        # ── Chart ─────────────────────────────────────────────────────────
        self._view = QWebEngineView()
        self._view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
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

        self._lbl_dist.setText(
            f"<b>{self._home_name}</b> → <b>{self._target_name}</b>"
            f"&nbsp;&nbsp;&nbsp;Entfernung: <b>{self._dist_km:.1f} km</b>"
        )
        # Alte Daten verwerfen, neu laden
        self._last_elev  = None
        self._last_dists = None
        self._last_los   = None
        self._lbl_diff.setText("")
        self._view.setHtml(self._loading_html())
        self._fetch()

    # ── Frequenzänderung ──────────────────────────────────────────────────

    def _on_freq_changed(self, value: float) -> None:
        self._freq_mhz = value
        if self._last_elev is not None:
            self._rebuild_chart()

    # ── Datenabruf ────────────────────────────────────────────────────────

    def _fetch(self) -> None:
        if self._thread and self._thread.isRunning():
            self._thread.terminate()
            self._thread.wait()
        self._lbl_status.setText("Lade Höhendaten von opentopodata.org…")
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
        self._lbl_status.setText(f"Fehler: {msg}")
        self._lbl_status.setStyleSheet("color: #c0392b;")
        self._lbl_diff.setText("")
        self._view.setHtml(self._error_html(msg))

    def _rebuild_chart(self) -> None:
        """Berechnet Beugungsanalyse neu und aktualisiert Chart + Zusammenfassungszeile."""
        if self._last_elev is None:
            return
        elevations = self._last_elev
        dists      = self._last_dists
        los        = self._last_los
        h0 = elevations[0]
        hn = elevations[-1]

        diff = _knife_edge_analysis(elevations, los, dists, self._freq_mhz)

        # Kompakte Zusammenfassung in Zeile 2
        if diff:
            nu_str  = f"{diff['nu']:.2f}"
            j_str   = f"{diff['j_db']:.1f}"
            pwr_str = f"{diff['power_pct']:.1f}"
            col     = diff["quality_color"]
            self._lbl_diff.setText(
                f"ν = <b>{nu_str}</b>"
                f"&nbsp;&nbsp;J(ν) = <b>{j_str} dB</b>"
                f"&nbsp;&nbsp;Signal = <b>{pwr_str} %</b>"
                f"&nbsp;&nbsp;<span style='color:{col};font-weight:bold'>"
                f"{diff['quality_text']}</span>"
            )
        else:
            self._lbl_diff.setText("")

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
            )
        return dict(
            bg="#f0f0f0", fg="#1a1a1a",
            card_bg="#ffffff", card_border="#d0d0d0",
            grid="#e0e0e0",
            elev_color="#0078d4", elev_fill="rgba(0,120,212,0.12)",
            los_color="#c0392b",
        )

    def _loading_html(self) -> str:
        v = self._css_vars()
        return (
            f"<!DOCTYPE html><html><body style='margin:0;background:{v['bg']};"
            f"color:{v['fg']};font-family:sans-serif;display:flex;"
            f"align-items:center;justify-content:center;height:100vh;'>"
            f"<div style='font-size:18px;opacity:0.6'>Höhendaten werden geladen…</div>"
            f"</body></html>"
        )

    def _error_html(self, msg: str) -> str:
        v = self._css_vars()
        ec = "#e07050" if self._dark else "#c0392b"
        return (
            f"<!DOCTYPE html><html><body style='margin:0;background:{v['bg']};"
            f"color:{ec};font-family:sans-serif;display:flex;flex-direction:column;"
            f"align-items:center;justify-content:center;height:100vh;gap:12px;'>"
            f"<div style='font-size:48px'>⚠</div>"
            f"<div style='font-size:15px;max-width:500px;text-align:center'>{msg}</div>"
            f"<div style='font-size:12px;opacity:0.6'>Internetverbindung prüfen.</div>"
            f"</body></html>"
        )

    def _build_html(
        self,
        dists: List[float],
        elevations: List[float],
        los: List[float],
        h0: float,
        hn: float,
        diff: Optional[Dict],
    ) -> str:
        v    = self._css_vars()
        hmax = max(elevations)
        hmin = min(elevations)

        h0_antenna  = h0 + self._antenna_height
        height_note = f" (+{self._antenna_height:.0f} m Mast)" if self._antenna_height > 0 else ""

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
    <div class="card-label">Beugungsverlust J(ν)</div>
    <div class="card-value">{j_str} dB</div>
  </div>
  <div class="card card-diff" style="border-color:{qcol}33;">
    <div class="card-label">ν  (bei {obs_dist} km)</div>
    <div class="card-value">{nu_str}</div>
  </div>
  <div class="card card-diff" style="border-color:{qcol}33;">
    <div class="card-label">Signal-Leistung</div>
    <div class="card-value">{p_str} %</div>
  </div>
  <div class="card card-diff" style="border-left: 3px solid {qcol}; border-color:{qcol};">
    <div class="card-label">Bewertung (ITU-R P.526)</div>
    <div class="card-value" style="font-size:13px;padding-top:3px;color:{qcol}">{diff['quality_text']}</div>
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
                  content: 'Hindernis  h={h_str} m',
                  position: 'start',
                  yAdjust: 6,
                  color: '{ann_color}',
                  backgroundColor: '{v['card_bg']}cc',
                  font: {{ size: 11 }},
                  padding: 4,
                  borderRadius: 4
                }}
              }}
            }}"""

        # Sichtlinie-Status (einfach, für Karten ohne diff)
        blocked = any(e > l for e, l in list(zip(elevations, los))[1:-1])
        los_txt = (
            f'<span style="color:#e07050">⚠ blockiert</span>'
            if blocked else
            f'<span style="color:#5cb85c">✔ frei</span>'
        )

        # ── Ionosphärische Ausbreitung (nur KW < 30 MHz) ─────────────────
        sky = _sky_wave_analysis(self._dist_km, self._freq_mhz)
        skywave_section = ""
        if sky:
            mode_cards_html = ""
            for m in sky["modes"]:
                ok_col = "#5cb85c" if m["ok"] else "#f0ad4e"
                mode_cards_html += (
                    f'<div class="card">'
                    f'<div class="card-label" style="color:{ok_col}">{m["name"]}</div>'
                    f'<div class="card-value" style="font-size:11px;padding-top:3px">{m["sub"]}</div>'
                    f'</div>'
                )
            if sky["skip_zone"] and not sky["modes"]:
                skip_col = "#e07050" if self._dark else "#c0392b"
                mode_cards_html += (
                    f'<div class="card" style="border-color:{skip_col}55">'
                    f'<div class="card-label" style="color:{skip_col}">Toter Bereich (Skip-Zone)</div>'
                    f'<div class="card-value" style="font-size:11px;padding-top:3px;color:{skip_col}">'
                    f'kein ionosphärischer Ausbreitungsweg</div></div>'
                )
            nvis_note = " · NVIS möglich" if sky["nvis_ok"] else ""
            fo_str    = f"foF₂ ≈ {sky['fo_f2']:.0f} MHz (Tagesmittel)"
            skywave_section = (
                f'<div class="divider"></div>'
                f'<div class="section-title">'
                f'Ionosphärische Ausbreitung · {sky["band_name"]}{nvis_note}'
                f'&nbsp;·&nbsp;{fo_str}</div>'
                f'<div class="cards">{mode_cards_html}</div>'
            )

        dists_json = json.dumps(dists)
        elev_json  = json.dumps(elevations)
        los_json   = json.dumps(los)

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
    <div class="card-label">Max. Gelände</div>
    <div class="card-value">{hmax:.0f} m</div>
  </div>
  <div class="card">
    <div class="card-label">Entfernung</div>
    <div class="card-value">{self._dist_km:.1f} km</div>
  </div>
  <div class="card">
    <div class="card-label">Sichtlinie</div>
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
const dists = {dists_json};
const elev  = {elev_json};
const los   = {los_json};

// Plugin registriert sich via CDN automatisch – kein Chart.register() nötig.
new Chart(document.getElementById('c'), {{
  type: 'line',
  data: {{
    labels: dists,
    datasets: [
      {{
        label: 'Gelände (m)',
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
        label: 'Sichtlinie',
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
        padding: 9,
        callbacks: {{
          title: items => ' ' + parseFloat(items[0].label).toFixed(1) + ' km',
          label: ctx  => '  ' + ctx.dataset.label + ':  ' + Math.round(ctx.raw) + ' m'
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
