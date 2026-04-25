"""Kleiner Kompass ohne Zeiger für Statistik-Ringe (Last-Heatmap)."""

from __future__ import annotations

import math
from typing import Optional, List, Tuple

from PySide6.QtCore import Qt, QPointF, QRectF, QSize
from PySide6.QtGui import QPainter, QPen, QColor, QPalette, QFontMetrics, QPainterPath
from PySide6.QtWidgets import QWidget


# EL: Viertelkreis (90°), 18 Segmente à 5°, 2/3 größer als Basis
EL_ARC_DEG = 90.0
EL_N_SEG = 18
EL_SCALE = 1.0 + 2.0 / 3.0  # 2 Drittel größer

# Erste und letzte 5 Bins sind nicht aussagekräftig (Rand-Effekte), werden nicht genutzt
BINS_SKIP_EDGE = 5
AZ_N_TOTAL = 72
AZ_N_USED = AZ_N_TOTAL - 2 * BINS_SKIP_EDGE  # 62
EL_N_USED = EL_N_SEG - 2 * BINS_SKIP_EDGE  # 8

# OM-Radar (AirScout/KST): Standard-Sektoren (konfigurierbar 10–100 in den Einstellungen)
OM_RADAR_N_DEFAULT = 20
OM_RADAR_N = OM_RADAR_N_DEFAULT  # Abwärtskompatibel für Importe

# (thr_blue, norm_min, norm_max, thr_red) — thr_blue ≤ norm_min ≤ norm_max ≤ thr_red
HeatmapScale = Tuple[int, int, int, int]


def compute_bin_min_max(
    cw: Optional[List[int]],
    ccw: Optional[List[int]],
    elevation: bool = False,
) -> tuple[Optional[int], Optional[int]]:
    """Min/Max der nutzbaren Bin-Werte (max je Segment aus CW/CCW), Rand-Bins ausgeschlossen."""
    if not cw and not ccw:
        return None, None
    if elevation:
        start_idx = BINS_SKIP_EDGE
        end_idx = EL_N_SEG - BINS_SKIP_EDGE
    else:
        start_idx = BINS_SKIP_EDGE
        end_idx = AZ_N_TOTAL - BINS_SKIP_EDGE
    vals: list[int] = []
    for i in range(start_idx, end_idx):
        v_cw = int(cw[i]) if cw and i < len(cw) else 0
        v_ccw = int(ccw[i]) if ccw and i < len(ccw) else 0
        vals.append(max(v_cw, v_ccw))
    if not vals:
        return None, None
    return min(vals), max(vals)


def parse_heatmap_scale(ui: dict, axis: str) -> Optional[HeatmapScale]:
    """Liest ui.heatmap_custom_{axis} und Schwellen; None = automatische Min/Max-Skala pro Datenframe."""
    ax = str(axis).strip().lower()
    if ax not in ("az", "el"):
        return None
    if not bool(ui.get(f"heatmap_custom_{ax}", False)):
        return None
    try:
        tb = int(ui.get(f"heatmap_thr_blue_{ax}", -1))
        nm = int(ui.get(f"heatmap_norm_min_{ax}", -1))
        nx = int(ui.get(f"heatmap_norm_max_{ax}", -1))
        tr = int(ui.get(f"heatmap_thr_red_{ax}", -1))
    except (TypeError, ValueError):
        return None
    if tb < 0 or nm < 0 or nx < 0 or tr < 0:
        return None
    if not (tb <= nm <= nx <= tr):
        return None
    return (tb, nm, nx, tr)


def _t_to_heatmap_color(t: float) -> QColor:
    """t ∈ [0,1]: 0 → blau, 1 → rot (wie bisher)."""
    t = max(0.0, min(1.0, float(t)))
    if t < 0.25:
        r, g, b = 0, int(100 + 155 * (t / 0.25)), 255
    elif t < 0.5:
        r, g, b = 0, 255, int(255 - 255 * ((t - 0.25) / 0.25))
    elif t < 0.75:
        r, g, b = int(255 * ((t - 0.5) / 0.25)), 255, 0
    else:
        r, g, b = 255, int(255 - 255 * ((t - 0.75) / 0.25)), 0
    return QColor(max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b)))


# Auto-Skala: Mindestbreite, damit kleine Mess-/Poll-Schwankungen nicht ständig die Farben umsortieren
_AUTO_HEATMAP_MIN_SPAN = 32
# EMA auf (v_lo, v_hi) der Auto-Skala — gleiche Rohdaten, aber Min/Max springt leicht → ohne Glättung flackert der Ring
_AUTO_HEATMAP_SMOOTH_ALPHA = 0.28


def _expanded_auto_range_int(v_min: int, v_max: int, min_span: int = _AUTO_HEATMAP_MIN_SPAN) -> tuple[float, float]:
    """Symmetrisch um die Mitte auf mindestens ``min_span`` aufweiten (nur wenn v_max > v_min)."""
    mid = 0.5 * float(v_min + v_max)
    raw = float(v_max - v_min)
    half = max(0.5 * raw, 0.5 * float(min_span))
    return mid - half, mid + half


def _v_to_t_scaled(v: int, scale: HeatmapScale) -> float:
    """Wert → 0…1 für Farbverlauf: unterhalb norm_min bläulich, Normbereich grünlich, oberhalb rötlich."""
    tb, nm, nx, tr = scale
    if v <= tb:
        return 0.0
    if v >= tr:
        return 1.0
    if v < nm:
        if nm <= tb:
            return 0.25
        return 0.25 * float(v - tb) / float(nm - tb)
    if v > nx:
        if tr <= nx:
            return 0.85
        return 0.75 + 0.25 * float(v - nx) / float(tr - nx)
    # nm <= v <= nx
    if nx <= nm:
        return 0.5
    return 0.25 + 0.5 * float(v - nm) / float(nx - nm)


def paint_bins_heatmap_ring(
    painter: QPainter,
    cx: float,
    cy: float,
    inner_r: float,
    cw: Optional[List[int]],
    ccw: Optional[List[int]],
    elevation: bool = False,
    ring_width: float = 5.0,
    offset_deg: float = 0.0,
    scale: Optional[HeatmapScale] = None,
    auto_smooth_state: Optional[List[float]] = None,
) -> None:
    """5px Ring mit ACCBINS-Heatmap um einen Kompass.
    AZ: 62 nutzbare Bins (5–66) auf 360°, EL: 8 nutzbare Bins (5–12) auf 90°.
    Erste und letzte 5 Bins werden ausgeschlossen (nicht aussagekräftig).
    Ohne ``scale``: min/max aus den gelesenen Bin-Werten (blau=min, rot=max).
    Mit ``scale``=(thr_blue, norm_min, norm_max, thr_red): Normbereich grün, darunter blau, darüber rot.
    offset_deg: Drehung der Heatmap um Antennenversatz (0° = oben/Nord).
    auto_smooth_state: bei automatischer Skala optional eine Liste [v_lo, v_hi] — wird mit EMA geglättet (Widget hält Referenz)."""
    if not cw and not ccw:
        return
    outer_r = inner_r + ring_width
    if outer_r <= inner_r:
        return

    if elevation:
        n_used = EL_N_USED
        step = EL_ARC_DEG / n_used
        start_idx = BINS_SKIP_EDGE
        end_idx = EL_N_SEG - BINS_SKIP_EDGE  # 13, range 5..12
    else:
        n_used = AZ_N_USED
        step = 360.0 / n_used
        start_idx = BINS_SKIP_EDGE
        end_idx = AZ_N_TOTAL - BINS_SKIP_EDGE  # 67, range 5..66

    offset_bin = int(round(offset_deg / step)) % n_used if n_used > 0 else 0
    vals = []
    for i in range(start_idx, end_idx):
        v_cw = int(cw[i]) if cw and i < len(cw) else 0
        v_ccw = int(ccw[i]) if ccw and i < len(ccw) else 0
        vals.append(max(v_cw, v_ccw))

    v_min = min(vals) if vals else 0
    v_max = max(vals) if vals else 0
    no_data = not vals or all(v <= 0 for v in vals)

    v_lo_auto: Optional[float] = None
    v_hi_auto: Optional[float] = None
    if scale is not None:
        if auto_smooth_state is not None:
            auto_smooth_state.clear()
    elif not no_data and v_max > v_min:
        v_lo_new, v_hi_new = _expanded_auto_range_int(v_min, v_max)
        if auto_smooth_state is not None:
            if len(auto_smooth_state) < 2:
                auto_smooth_state.clear()
                auto_smooth_state.extend((v_lo_new, v_hi_new))
            else:
                a = _AUTO_HEATMAP_SMOOTH_ALPHA
                auto_smooth_state[0] += a * (v_lo_new - auto_smooth_state[0])
                auto_smooth_state[1] += a * (v_hi_new - auto_smooth_state[1])
            v_lo_auto, v_hi_auto = auto_smooth_state[0], auto_smooth_state[1]
        else:
            v_lo_auto, v_hi_auto = v_lo_new, v_hi_new
    elif auto_smooth_state is not None:
        auto_smooth_state.clear()

    def val_to_color(v: int) -> QColor:
        if no_data:
            return QColor(120, 130, 170)
        if scale is not None:
            return _t_to_heatmap_color(_v_to_t_scaled(int(v), scale))
        if v_max <= v_min:
            return QColor(0, 180, 120)
        # Auto: geglättete oder sofortige (v_lo, v_hi)
        assert v_lo_auto is not None and v_hi_auto is not None
        span = v_hi_auto - v_lo_auto
        if span <= 0:
            return QColor(0, 180, 120)
        t = max(0.0, min(1.0, (float(v) - v_lo_auto) / span))
        return _t_to_heatmap_color(t)

    painter.setPen(Qt.PenStyle.NoPen)
    for i in range(n_used):
        comp_start = i * step
        comp_span = step
        src_i = (i - offset_bin) % n_used
        seg_color = val_to_color(vals[src_i])
        if elevation:
            start_rad = math.radians(comp_start)
            end_rad = math.radians(comp_start + comp_span)
            path = QPainterPath()
            path.moveTo(cx + inner_r * math.cos(start_rad), cy - inner_r * math.sin(start_rad))
            path.arcTo(cx - outer_r, cy - outer_r, 2 * outer_r, 2 * outer_r, comp_start, comp_span)
            path.lineTo(cx + inner_r * math.cos(end_rad), cy - inner_r * math.sin(end_rad))
            path.arcTo(
                cx - inner_r,
                cy - inner_r,
                2 * inner_r,
                2 * inner_r,
                comp_start + comp_span,
                -comp_span,
            )
            path.closeSubpath()
        else:
            start_rad = math.radians(90.0 - comp_start)
            end_rad = math.radians(90.0 - (comp_start + comp_span))
            path = QPainterPath()
            path.moveTo(cx + inner_r * math.cos(start_rad), cy - inner_r * math.sin(start_rad))
            path.arcTo(
                cx - outer_r, cy - outer_r, 2 * outer_r, 2 * outer_r, 90.0 - comp_start, -comp_span
            )
            path.lineTo(cx + inner_r * math.cos(end_rad), cy - inner_r * math.sin(end_rad))
            path.arcTo(
                cx - inner_r,
                cy - inner_r,
                2 * inner_r,
                2 * inner_r,
                90.0 - (comp_start + comp_span),
                comp_span,
            )
            path.closeSubpath()
        painter.setBrush(seg_color)
        painter.drawPath(path)


def paint_om_radar_ring(
    painter: QPainter,
    cx: float,
    cy: float,
    inner_r: float,
    counts: Optional[List[float]],
    ring_width: float = 5.0,
    offset_deg: float = 0.0,
    n_sectors: int = OM_RADAR_N_DEFAULT,
) -> None:
    """5px-Ring: n Sektoren (360°/n); Werte = erwartete OM-Dichte (Öffnungswinkel-Verteilung); blau = wenig, rot = viel.

    ``offset_deg``: nur für rotorbezogene Daten; OM-Radar nutzt geografische Peilung → Aufruf mit 0.
    """
    outer_r = inner_r + ring_width
    if outer_r <= inner_r:
        return

    n = max(10, min(100, int(n_sectors)))
    vals = [0.0] * n
    if counts:
        for i in range(min(n, len(counts))):
            try:
                vals[i] = max(0.0, float(counts[i]))
            except (TypeError, ValueError):
                vals[i] = 0.0

    v_min = min(vals) if vals else 0.0
    v_max = max(vals) if vals else 0.0
    no_data = not vals or all(v <= 0.0 for v in vals)

    def val_to_color(v: float) -> QColor:
        if no_data:
            return QColor(120, 130, 170)
        if v_max <= v_min:
            return QColor(0, 180, 120)
        span = max(v_max - v_min, 1e-9)
        t = max(0.0, min(1.0, (float(v) - float(v_min)) / float(span)))
        return _t_to_heatmap_color(t)

    step = 360.0 / float(n)
    offset_bin = int(round(float(offset_deg) / step)) % n if n > 0 else 0

    painter.setPen(Qt.PenStyle.NoPen)
    for i in range(n):
        comp_start = i * step
        comp_span = step
        src_i = (i - offset_bin) % n
        seg_color = val_to_color(vals[src_i])
        start_rad = math.radians(90.0 - comp_start)
        end_rad = math.radians(90.0 - (comp_start + comp_span))
        path = QPainterPath()
        path.moveTo(cx + inner_r * math.cos(start_rad), cy - inner_r * math.sin(start_rad))
        path.arcTo(
            cx - outer_r, cy - outer_r, 2 * outer_r, 2 * outer_r, 90.0 - comp_start, -comp_span
        )
        path.lineTo(cx + inner_r * math.cos(end_rad), cy - inner_r * math.sin(end_rad))
        path.arcTo(
            cx - inner_r,
            cy - inner_r,
            2 * inner_r,
            2 * inner_r,
            90.0 - (comp_start + comp_span),
            comp_span,
        )
        path.closeSubpath()
        painter.setBrush(seg_color)
        painter.drawPath(path)


def paint_dwell_ring(
    painter: QPainter,
    cx: float,
    cy: float,
    inner_r: float,
    seconds: Optional[List[float]],
    full_seconds: float,
    ring_width: float = 5.0,
    offset_deg: float = 0.0,
    n_sectors: int = OM_RADAR_N_DEFAULT,
) -> None:
    """5px-Ring: kumulative Stillstandszeit je Sektor; 0s→blau, full_seconds→rot (linear).

    ``seconds[i]`` = Sektor i in **Rotor-/Mechanik-Koordinaten** (0° = wie Rotor-Null);
    ``offset_deg`` = Antennenversatz: gleiche Drehlogik wie OM-Radar/Heatmap (Anzeige = Rotor + Offset).
    """
    outer_r = inner_r + ring_width
    if outer_r <= inner_r:
        return

    n = max(10, min(100, int(n_sectors)))
    try:
        fs = max(0.001, float(full_seconds))
    except (TypeError, ValueError):
        fs = 300.0

    vals = [0.0] * n
    if seconds:
        for i in range(min(n, len(seconds))):
            try:
                vals[i] = max(0.0, float(seconds[i]))
            except (TypeError, ValueError):
                vals[i] = 0.0

    step = 360.0 / float(n)
    offset_bin = int(round(float(offset_deg) / step)) % n if n > 0 else 0

    def sec_to_color(sec: float) -> QColor:
        t = max(0.0, min(1.0, float(sec) / fs))
        return _t_to_heatmap_color(t)

    painter.setPen(Qt.PenStyle.NoPen)
    for i in range(n):
        comp_start = i * step
        comp_span = step
        src_i = (i - offset_bin) % n
        seg_color = sec_to_color(vals[src_i])
        start_rad = math.radians(90.0 - comp_start)
        end_rad = math.radians(90.0 - (comp_start + comp_span))
        path = QPainterPath()
        path.moveTo(cx + inner_r * math.cos(start_rad), cy - inner_r * math.sin(start_rad))
        path.arcTo(
            cx - outer_r, cy - outer_r, 2 * outer_r, 2 * outer_r, 90.0 - comp_start, -comp_span
        )
        path.lineTo(cx + inner_r * math.cos(end_rad), cy - inner_r * math.sin(end_rad))
        path.arcTo(
            cx - inner_r,
            cy - inner_r,
            2 * inner_r,
            2 * inner_r,
            90.0 - (comp_start + comp_span),
            comp_span,
        )
        path.closeSubpath()
        painter.setBrush(seg_color)
        painter.drawPath(path)


def paint_az_ring_gap_black(
    painter: QPainter,
    cx: float,
    cy: float,
    inner_r: float,
    gap_width: float = 1.0,
) -> None:
    """Voller 360°-Ring zwischen zwei AZ-Farbringern (schwarz, z. B. 1 px)."""
    outer_r = inner_r + gap_width
    if outer_r <= inner_r:
        return
    path = QPainterPath()
    # Loch in der Mitte: FillRule am Pfad (QPainter hat in Qt6 kein setFillRule)
    path.setFillRule(Qt.FillRule.OddEvenFill)
    path.addEllipse(QRectF(cx - outer_r, cy - outer_r, 2 * outer_r, 2 * outer_r))
    path.addEllipse(QRectF(cx - inner_r, cy - inner_r, 2 * inner_r, 2 * inner_r))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(0, 0, 0))
    painter.drawPath(path)


class StatisticCompassWidget(QWidget):
    """Kompass ohne Zeiger, mit einem 5px Heatmap-Ring. AZ=Vollkreis, EL=Viertelkreis."""

    def __init__(self, parent=None, elevation: bool = False):
        super().__init__(parent)
        self._bins_cw: Optional[List[int]] = None
        self._bins_ccw: Optional[List[int]] = None
        self._elevation: bool = bool(elevation)
        self._heatmap_scale: Optional[HeatmapScale] = None
        self.setMinimumSize(120, 120)  # Skalierbar, vernünftige Mindestgröße

    def sizeHint(self) -> QSize:
        return QSize(200, 200)  # Sinnvolle Standardgröße für Layout

    def set_elevation(self, on: bool) -> None:
        """Viertelkreis-Modus (EL) ein/aus."""
        if self._elevation != on:
            self._elevation = bool(on)
            self.update()

    def set_bins(self, cw: Optional[List[int]], ccw: Optional[List[int]]) -> None:
        """Bins setzen: AZ=72 Werte/Richtung, EL=18 Werte/Richtung (mV)."""
        need = EL_N_SEG if self._elevation else 72
        self._bins_cw = list(cw) if cw is not None and len(cw) >= need else None
        self._bins_ccw = list(ccw) if ccw is not None and len(ccw) >= need else None
        self.update()

    def set_heatmap_scale(self, scale: Optional[HeatmapScale]) -> None:
        """Optionale Skala (thr_blue, norm_min, norm_max, thr_red); None = auto min/max."""
        self._heatmap_scale = scale
        self.update()

    def _geom(self) -> tuple[float, float, float]:
        """(cx, cy, r) – 15px Rand. Bei EL: Mittelpunkt unten links, 2/3 größer."""
        rect = self.rect().adjusted(15, 15, -15, -15)
        r_base = float(min(rect.width(), rect.height())) / 2.0
        if self._elevation:
            cx = float(rect.left())
            cy = float(rect.bottom())
            r = r_base * EL_SCALE
        else:
            cx = float(rect.center().x())
            cy = float(rect.center().y())
            r = r_base
        return cx, cy, r

    def paintEvent(self, _event):
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

            cx, cy, r = self._geom()

            if self._elevation:
                # Viertelkreis (EL): 0°=rechts, 90°=oben
                arc_rect = QRectF(cx - r, cy - r, 2 * r, 2 * r)
                painter.setPen(QPen(self.palette().color(QPalette.ColorRole.WindowText), 1))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawArc(arc_rect, int(0 * 16), int(EL_ARC_DEG * 16))
                painter.drawLine(QPointF(cx, cy), QPointF(cx + r, cy))
                end_x = cx + math.cos(math.radians(EL_ARC_DEG)) * r
                end_y = cy - math.sin(math.radians(EL_ARC_DEG)) * r
                painter.drawLine(QPointF(cx, cy), QPointF(end_x, end_y))
                tick_pen = QPen(self.palette().color(QPalette.ColorRole.WindowText), 1)
                painter.setPen(tick_pen)
                for a in range(0, int(EL_ARC_DEG) + 1, 15):
                    rad = math.radians(a)
                    x1 = cx + math.cos(rad) * (r * 0.85)
                    y1 = cy - math.sin(rad) * (r * 0.85)
                    x2 = cx + math.cos(rad) * r
                    y2 = cy - math.sin(rad) * r
                    painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))
            else:
                # Vollkreis (AZ)
                painter.setPen(QPen(self.palette().color(QPalette.ColorRole.WindowText), 1))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawEllipse(QRectF(cx - r, cy - r, 2 * r, 2 * r))
                tick_pen = QPen(self.palette().color(QPalette.ColorRole.WindowText), 1)
                painter.setPen(tick_pen)
                for a in range(0, 360, 30):
                    rad = math.radians(a)
                    x1 = cx + math.sin(rad) * (r * 0.85)
                    y1 = cy - math.cos(rad) * (r * 0.85)
                    x2 = cx + math.sin(rad) * r
                    y2 = cy - math.cos(rad) * r
                    painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))
                font = painter.font()
                font.setBold(True)
                font.setPointSize(max(6, int(r * 0.12)))
                painter.setFont(font)
                fm = QFontMetrics(font)
                label_r = r * 0.65
                for text, angle in [("N", 0), ("O", 90), ("S", 180), ("W", 270)]:
                    rad = math.radians(angle)
                    tx = cx + math.sin(rad) * label_r
                    ty = cy - math.cos(rad) * label_r
                    w = fm.horizontalAdvance(text)
                    h = fm.height()
                    painter.drawText(QPointF(tx - w / 2.0, ty + h / 4.0), text)

            # 5px Heatmap-Ring
            paint_bins_heatmap_ring(
                painter,
                cx,
                cy,
                r,
                self._bins_cw,
                self._bins_ccw,
                self._elevation,
                scale=self._heatmap_scale,
            )

            # Kontur erneut darüber
            painter.setPen(QPen(self.palette().color(QPalette.ColorRole.WindowText), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            if self._elevation:
                painter.drawArc(QRectF(cx - r, cy - r, 2 * r, 2 * r), int(0 * 16), int(EL_ARC_DEG * 16))
                painter.drawLine(QPointF(cx, cy), QPointF(cx + r, cy))
                end_x = cx + math.cos(math.radians(EL_ARC_DEG)) * r
                end_y = cy - math.sin(math.radians(EL_ARC_DEG)) * r
                painter.drawLine(QPointF(cx, cy), QPointF(end_x, end_y))
            else:
                painter.drawEllipse(QRectF(cx - r, cy - r, 2 * r, 2 * r))
