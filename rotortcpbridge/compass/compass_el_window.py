from __future__ import annotations

"""Kompass-Fenster (EL) für RotorTcpBridge.

Dieses Fenster ist das Gegenstück zu ``compass_az_window.py``.

Unterschiede:
- Darstellung als Viertelkreis (0..90°)
- Nur EL-Rotor wird angesprochen (dst = ctrl.slave_el)

Bedienung:
- Klick auf den Außenring setzt SOLL und sendet sofort ``SETPOSDG``.
"""

import math
from typing import Optional, List

from PySide6.QtCore import Qt, Signal, QPointF, QRectF
from PySide6.QtGui import QPainter, QPen, QColor, QPalette, QFontMetrics
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from ..angle_utils import clamp_el
from ..i18n import t
from ..ui.led_widget import Led
from ..ui.ui_utils import px_to_dip
from .statistic_compass_widget import paint_bins_heatmap_ring


class ElevationCompassWidget(QWidget):
    """Viertelkreis-Kompass für 0..90° (Elevation)."""

    targetPicked = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_deg: Optional[float] = None
        self._target_deg: Optional[float] = None
        self._angle_decimals: int = 1
        self._bins_cw: Optional[List[int]] = None
        self._bins_ccw: Optional[List[int]] = None
        self._heatmap_visible: bool = False
        self._heatmap_offset_deg: float = 0.0
        self._top_center_widget: Optional[QWidget] = None

        # Ref/Fährt: Wort zuerst, dann LED (LED 3px unter Schriftbasis)
        led_d = px_to_dip(self, 13)
        self._ref_led = Led(led_d, self)
        ref_led_wrap = QWidget(self)
        ref_led_layout = QVBoxLayout(ref_led_wrap)
        ref_led_layout.setContentsMargins(0, 5, 0, 0)
        ref_led_layout.addWidget(self._ref_led)
        self._ref_lbl = QLabel(t("axis.ref_label") + ":")
        self._ref_lbl.setStyleSheet("font-size: 16px; font-weight: bold;")
        self._ref_lbl.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        self._ref_row = QWidget(self)
        ref_h = QHBoxLayout(self._ref_row)
        ref_h.setContentsMargins(0, 0, 0, 0)
        ref_h.setSpacing(4)
        ref_h.addWidget(self._ref_lbl, 0)
        ref_h.addWidget(ref_led_wrap, 0)
        self._moving_led = Led(led_d, self)
        mov_led_wrap = QWidget(self)
        mov_led_layout = QVBoxLayout(mov_led_wrap)
        mov_led_layout.setContentsMargins(0, 5, 0, 0)
        mov_led_layout.addWidget(self._moving_led)
        self._moving_lbl = QLabel(t("axis.moving_label"))
        self._moving_lbl.setStyleSheet("font-size: 16px; font-weight: bold;")
        self._moving_lbl.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        self._moving_row = QWidget(self)
        mov_h = QHBoxLayout(self._moving_row)
        mov_h.setContentsMargins(0, 0, 0, 0)
        mov_h.setSpacing(4)
        mov_h.addWidget(self._moving_lbl, 0)
        mov_h.addWidget(mov_led_wrap, 0)
        self._online_led = Led(led_d, self)
        on_led_wrap = QWidget(self)
        on_led_layout = QVBoxLayout(on_led_wrap)
        on_led_layout.setContentsMargins(0, 5, 0, 0)
        on_led_layout.addWidget(self._online_led)
        self._online_lbl = QLabel(t("axis.online_label") + ":")
        self._online_lbl.setStyleSheet("font-size: 16px; font-weight: bold;")
        self._online_lbl.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        self._online_row = QWidget(self)
        on_h = QHBoxLayout(self._online_row)
        on_h.setContentsMargins(0, 0, 0, 0)
        on_h.setSpacing(4)
        on_h.addWidget(self._online_lbl, 0)
        on_h.addWidget(on_led_wrap, 0)

        self.setMinimumSize(280, 280)

    def set_top_center_widget(self, widget: Optional[QWidget]) -> None:
        """Widget oben mittig über dem Kompass (z.B. Antennen-Dropdown)."""
        self._top_center_widget = widget
        if widget is not None:
            widget.setParent(self)
            widget.raise_()

    def set_ref_led_state(self, on: bool) -> None:
        self._ref_led.set_state(bool(on))

    def set_moving_led_state(self, on: bool) -> None:
        self._moving_led.set_state(bool(on))

    def set_online_led_state(self, on: bool) -> None:
        self._online_led.set_state(bool(on))

    def apply_label_text_color(self, color: QColor) -> None:
        """Textfarbe aus Palette setzen (palette() in Stylesheet funktioniert unzuverlässig)."""
        style = f"font-size: 16px; font-weight: bold; color: {color.name()};"
        self._ref_lbl.setStyleSheet(style)
        self._moving_lbl.setStyleSheet(style)
        self._online_lbl.setStyleSheet(style)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        margin = 7
        top_y = 13
        if self._top_center_widget is not None:
            w = self._top_center_widget.sizeHint().width() if self._top_center_widget.sizeHint().isValid() else 140
            h = self._top_center_widget.sizeHint().height() if self._top_center_widget.sizeHint().isValid() else 24
            x = (self.width() - w) // 2
            y = 0
            self._top_center_widget.setGeometry(x, y, max(w, 120), max(h, 22))
            self._top_center_widget.raise_()
        # Ref links oben, Fährt rechts oben, Online unter Fährt
        self._ref_row.adjustSize()
        self._ref_row.setGeometry(margin, top_y, self._ref_row.width(), self._ref_row.height())
        self._ref_row.raise_()
        self._moving_row.adjustSize()
        mov_w = self._moving_row.width()
        self._moving_row.setGeometry(self.width() - margin - mov_w, top_y, mov_w, self._moving_row.height())
        self._moving_row.raise_()
        line2_y = int(top_y + 22)
        self._online_row.adjustSize()
        on_w = self._online_row.width()
        self._online_row.setGeometry(self.width() - margin - on_w, line2_y, on_w, self._online_row.height())
        self._online_row.raise_()

    def set_current_deg(self, deg: Optional[float]) -> None:
        self._current_deg = None if deg is None else clamp_el(deg)
        self.update()

    def set_target_deg(self, deg: Optional[float]) -> None:
        self._target_deg = None if deg is None else clamp_el(deg)
        self.update()

    def set_angle_decimals(self, decimals: int) -> None:
        try:
            d = int(decimals)
        except Exception:
            d = 1
        if d not in (1, 2):
            d = 1
        self._angle_decimals = d

    def set_bins(self, cw: Optional[List[int]], ccw: Optional[List[int]]) -> None:
        """ACCBINS für 5px Heatmap-Ring. 18 Werte je Richtung (EL)."""
        self._bins_cw = list(cw) if cw and len(cw) >= 18 else None
        self._bins_ccw = list(ccw) if ccw and len(ccw) >= 18 else None
        self.update()

    def set_heatmap_visible(self, on: bool) -> None:
        """Ob der ACCBINS-Heatmap-Ring angezeigt wird."""
        self._heatmap_visible = bool(on)
        self.update()

    def set_heatmap_offset_deg(self, offset: float) -> None:
        """Heatmap um Antennenversatz drehen (0° = Horizont)."""
        self._heatmap_offset_deg = float(offset)
        self.update()

    def _geom(self) -> tuple[float, float, float]:
        """Hilfsgeometrie: (cx, cy, r)

        Für EL wählen wir den Kreismittelpunkt unten links, damit ein Viertelkreis entsteht.
        """
        # Damit die Grad-Zahlen *außerhalb* des Viertelkreises liegen können (und nicht
        # von den 0°/90°-Linien zerschnitten werden), brauchen wir rundherum genügend
        # Rand. Zusätzlich verkleinern wir den Radius leicht, damit noch Platz für die
        # Beschriftung bleibt.
        w = max(1, int(self.width()))
        h = max(1, int(self.height()))

        # Dynamischer Rand: mindestens 48px, bei großen Widgets etwas mehr.
        margin = int(max(48, min(w, h) * 0.10))

        rect = self.rect().adjusted(margin, margin, -margin, -margin)

        # Mittelpunkt unten links innerhalb des Randes
        cx = float(rect.left())
        cy = float(rect.bottom())

        # Radius bewusst etwas kleiner wählen, damit die Beschriftung außerhalb (label_r > r)
        # noch vollständig im Widget bleibt.
        base = float(min(rect.width(), rect.height()))
        r = base * 0.92
        return cx, cy, r

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)

        cx, cy, r = self._geom()
        pos = event.position()
        dx = float(pos.x() - cx)
        dy = float(cy - pos.y())  # nach oben positiv
        dist = math.hypot(dx, dy)

        # Nur Klicks auf dem Außenring akzeptieren
        inner = r * 0.78
        outer = r * 1.04
        if dist < inner or dist > outer:
            return super().mousePressEvent(event)

        # Winkel: 0° = rechts (Horizont), 90° = nach oben (Zenit)
        rad = math.atan2(dy, dx)
        deg = math.degrees(rad)
        if deg < 0.0:
            deg = 0.0
        if deg > 90.0:
            deg = 90.0

        deg = round(float(deg), int(self._angle_decimals))
        deg = clamp_el(deg)

        self.set_target_deg(deg)
        self.targetPicked.emit(deg)

    def pick_target(self, deg: float) -> None:
        """Ziel programmatisch setzen (wie Klick) – z.B. aus Soll-Eingabefeld."""
        deg = clamp_el(float(deg))
        self.set_target_deg(deg)
        self.targetPicked.emit(deg)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        cx, cy, r = self._geom()

        # Rahmen (Viertelkreis): Arc + zwei Radien
        painter.setPen(QPen(self.palette().color(QPalette.ColorRole.WindowText), 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)

        # Qt: drawArc benötigt ein Rechteck um den Vollkreis.
        # Unser Viertelkreis ist Teil eines Kreises mit Mittelpunkt (cx,cy) und Radius r.
        arc_rect = QRectF(cx - r, cy - r, 2 * r, 2 * r)
        # Startwinkel 0° (3 Uhr), Spannweite 90° CCW
        painter.drawArc(arc_rect, 0 * 16, 90 * 16)
        # Radialkanten
        painter.drawLine(QPointF(cx, cy), QPointF(cx + r, cy))  # 0°
        painter.drawLine(QPointF(cx, cy), QPointF(cx, cy - r))  # 90°

        # Teilstriche
        tick_pen = QPen(self.palette().color(QPalette.ColorRole.WindowText), 1)
        painter.setPen(tick_pen)
        for a in range(0, 91, 5):
            rad = math.radians(a)
            x1 = cx + math.cos(rad) * (r * 0.90)
            y1 = cy - math.sin(rad) * (r * 0.90)
            if a % 15 == 0:
                x2 = cx + math.cos(rad) * (r * 1.00)
                y2 = cy - math.sin(rad) * (r * 1.00)
            else:
                x2 = cx + math.cos(rad) * (r * 0.96)
                y2 = cy - math.sin(rad) * (r * 0.96)
            painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))

        # Grad-Beschriftung (alle 10°)
        painter.save()
        deg_font = painter.font()
        deg_font.setBold(False)
        # Beim AZ-Kompass wirken die Zahlen kleiner; beim Viertelkreis ist der Radius oft
        # größer, daher skalieren wir etwas konservativer und begrenzen nach oben.
        deg_font.setPointSize(max(7, min(14, int(r * 0.045))))
        painter.setFont(deg_font)
        fm_deg = QFontMetrics(deg_font)
        painter.setPen(QPen(self.palette().color(QPalette.ColorRole.WindowText), 1))

        # Beschriftung absichtlich *außerhalb* des Viertelkreises: so werden die Zahlen
        # an 0° und 90° nicht von den Radiallinien geschnitten.
        label_r = r * 1.06
        for a in range(0, 91, 10):
            txt = f"{a}°"
            rad = math.radians(a)
            tx = cx + math.cos(rad) * label_r
            ty = cy - math.sin(rad) * label_r
            w = fm_deg.horizontalAdvance(txt)
            h = fm_deg.height()
            painter.drawText(QPointF(tx - w / 2.0, ty + h / 3.0), txt)
        painter.restore()

        # ACCBINS-Heatmap-Ring (5px) um den Viertelkreis
        if self._heatmap_visible and (self._bins_cw or self._bins_ccw):
            paint_bins_heatmap_ring(painter, cx, cy, r, self._bins_cw, self._bins_ccw, elevation=True, ring_width=5.0, offset_deg=self._heatmap_offset_deg)

        # SOLL (gestrichelt)
        if self._target_deg is not None:
            painter.setPen(QPen(QColor(160, 0, 0), 3, Qt.PenStyle.DashLine))
            self._draw_arrow(painter, cx, cy, r * 0.85, self._target_deg)

        # IST (durchgezogen)
        if self._current_deg is not None:
            painter.setPen(QPen(QColor(0, 120, 0), 4, Qt.PenStyle.SolidLine))
            self._draw_arrow(painter, cx, cy, r * 0.92, self._current_deg)

        # Mittelpunkt
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self.palette().color(QPalette.ColorRole.WindowText))
        painter.drawEllipse(QRectF(cx - 5.0, cy - 5.0, 10.0, 10.0))

    @staticmethod
    def _draw_arrow(painter: QPainter, cx: float, cy: float, length: float, deg: float) -> None:
        rad = math.radians(float(deg))
        x2 = cx + math.cos(rad) * length
        y2 = cy - math.sin(rad) * length
        painter.drawLine(QPointF(cx, cy), QPointF(x2, y2))

        # Pfeilspitze
        head_len = max(10.0, length * 0.08)
        left = math.radians(float(deg) + 150)
        right = math.radians(float(deg) - 150)
        xl = x2 + math.cos(left) * head_len
        yl = y2 - math.sin(left) * head_len
        xr = x2 + math.cos(right) * head_len
        yr = y2 - math.sin(right) * head_len
        painter.drawLine(QPointF(x2, y2), QPointF(xl, yl))
        painter.drawLine(QPointF(x2, y2), QPointF(xr, yr))
