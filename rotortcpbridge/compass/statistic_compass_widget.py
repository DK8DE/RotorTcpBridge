"""Kleiner Kompass ohne Zeiger für Statistik-Ringe (Last-Heatmap)."""

from __future__ import annotations

import math
from typing import Optional, List

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
) -> None:
    """5px Ring mit ACCBINS-Heatmap um einen Kompass.
    AZ: 62 nutzbare Bins (5–66) auf 360°, EL: 8 nutzbare Bins (5–12) auf 90°.
    Erste und letzte 5 Bins werden ausgeschlossen (nicht aussagekräftig).
    Farb-Skala: min/max aus den gelesenen Bin-Werten (blau=min, rot=max).
    offset_deg: Drehung der Heatmap um Antennenversatz (0° = oben/Nord)."""
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

    def val_to_color(v: int) -> QColor:
        if no_data:
            return QColor(120, 130, 170)
        if v_max <= v_min:
            return QColor(0, 180, 120)
        # Skala v_min…v_max: t=0 → blau, t=1 → rot (min/max aus Bins)
        span = max(v_max - v_min, 1)
        t = max(0.0, min(1.0, (v - v_min) / span))
        if t < 0.25:
            r, g, b = 0, int(100 + 155 * (t / 0.25)), 255
        elif t < 0.5:
            r, g, b = 0, 255, int(255 - 255 * ((t - 0.25) / 0.25))
        elif t < 0.75:
            r, g, b = int(255 * ((t - 0.5) / 0.25)), 255, 0
        else:
            r, g, b = 255, int(255 - 255 * ((t - 0.75) / 0.25)), 0
        return QColor(max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b)))

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


class StatisticCompassWidget(QWidget):
    """Kompass ohne Zeiger, mit einem 5px Heatmap-Ring. AZ=Vollkreis, EL=Viertelkreis."""

    def __init__(self, parent=None, elevation: bool = False):
        super().__init__(parent)
        self._bins_cw: Optional[List[int]] = None
        self._bins_ccw: Optional[List[int]] = None
        self._elevation: bool = bool(elevation)
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
        self._bins_cw = list(cw) if cw and len(cw) >= need else None
        self._bins_ccw = list(ccw) if ccw and len(ccw) >= need else None
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
        painter = QPainter(self)
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
        paint_bins_heatmap_ring(painter, cx, cy, r, self._bins_cw, self._bins_ccw, self._elevation)

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
