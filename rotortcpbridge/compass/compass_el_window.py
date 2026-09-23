from __future__ import annotations

"""Kompass-Fenster (EL) für RotorTcpBridge.

Dieses Fenster ist das Gegenstück zu ``compass_az_window.py``.

Unterschiede:
- Darstellung als Viertelkreis (0..90°) oder Halbkreis (0..180°) je nach GETROTORTYPE
- Nur EL-Rotor wird angesprochen (dst = ctrl.slave_el)

Bedienung:
- Klick auf den Außenring setzt SOLL und sendet sofort ``SETPOSDG``.
"""

import math
from typing import Optional, List

from PySide6.QtCore import Qt, Signal, QPointF, QRectF
from PySide6.QtGui import (
    QPainter,
    QPen,
    QColor,
    QPalette,
    QFontMetrics,
    QPolygonF,
    QLinearGradient,
)
from PySide6.QtWidgets import QLabel, QWidget
from ..angle_utils import clamp_el
from ..i18n import t
from ..ui.led_widget import Led
from ..ui.ui_utils import px_to_dip
from .statistic_compass_widget import HeatmapScale, paint_bins_heatmap_ring

# „Soll:“ + Eingabe (rechts oben) zusätzlich nach oben (kleineres oy)
_SOLL_OVERLAY_Y_SHIFT_PX = 60
_ARROW_SHAFT_WIDTH = 7.0


class ElevationCompassWidget(QWidget):
    """Elevations-Kompass: 0..90° (Viertelkreis) oder 0..180° (Halbkreis)."""

    targetPicked = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_deg: Optional[float] = None
        self._target_deg: Optional[float] = None
        self._angle_decimals: int = 1
        self._max_deg: float = 90.0
        self._bins_cw: Optional[List[int]] = None
        self._bins_ccw: Optional[List[int]] = None
        self._heatmap_visible: bool = False
        self._heatmap_offset_deg: float = 0.0
        self._heatmap_scale: Optional[HeatmapScale] = None
        self._heatmap_auto_smooth: List[float] = []
        self._top_center_widget: Optional[QWidget] = None
        self._soll_overlay: Optional[QWidget] = None
        self._overlay_ist: str = ""
        self._overlay_soll: str = ""

        self._led_d = px_to_dip(self, 13)
        lbl_style = "font-size: 16px; font-weight: bold;"

        self._moving_led = Led(self._led_d, self)
        self._moving_lbl = QLabel(t("axis.moving_label"), self)
        self._moving_lbl.setStyleSheet(lbl_style)

        self._online_led = Led(self._led_d, self)
        self._online_lbl = QLabel(t("axis.online_label"), self)
        self._online_lbl.setStyleSheet(lbl_style)

        self._ref_led = Led(self._led_d, self)
        self._ref_lbl = QLabel(t("compass.ref_led_label_el"), self)
        self._ref_lbl.setStyleSheet(lbl_style)

        self._text_overlay_visible: bool = True

        for w in (
            self._moving_led,
            self._moving_lbl,
            self._online_led,
            self._online_lbl,
            self._ref_led,
            self._ref_lbl,
        ):
            w.setVisible(False)

        self.setMinimumSize(280, 280)

    def max_deg(self) -> float:
        return float(self._max_deg)

    def set_max_deg(self, max_deg: float) -> None:
        """90 = Viertelkreis, 180 = Halbkreis (GETROTORTYPE 2 bzw. 3)."""
        try:
            mx = float(max_deg)
        except Exception:
            mx = 90.0
        mx = 180.0 if mx >= 180.0 else 90.0
        if abs(mx - self._max_deg) < 0.1:
            return
        self._max_deg = mx
        if self._current_deg is not None:
            self._current_deg = clamp_el(self._current_deg, mx)
        if self._target_deg is not None:
            self._target_deg = clamp_el(self._target_deg, mx)
        # 36 Firmware-Bins bleiben; nur die Darstellung (90°/180°) ändert sich
        self.update()

    def set_top_center_widget(self, widget: Optional[QWidget]) -> None:
        """Widget oben mittig über dem Kompass (z.B. Antennen-Dropdown)."""
        self._top_center_widget = widget
        if widget is not None:
            widget.setParent(self)
            widget.raise_()

    def set_soll_overlay_widget(self, widget: Optional[QWidget]) -> None:
        """Soll-Label + Eingabe oben rechts (statt nur Textzeichnung)."""
        old = self._soll_overlay
        self._soll_overlay = widget
        if old is not None and old is not widget:
            old.setParent(None)
            old.hide()
        if widget is not None:
            widget.setParent(self)
            widget.show()
            widget.raise_()
        self._layout_corner_controls()
        self.update()

    def set_ref_led_state(self, on: bool) -> None:
        self._ref_led.set_state(bool(on))

    def set_ref_led_homing(self, active: bool) -> None:
        self._ref_led.set_blinking_red_green(bool(active))

    def set_moving_led_state(self, on: bool) -> None:
        self._moving_led.set_state(bool(on))

    def set_online_led_state(self, on: bool) -> None:
        self._online_led.set_state(bool(on))

    def set_led_overlay_visible(self, visible: bool) -> None:
        """Interne LED-Zeilen ein-/ausblenden (wenn externe Statusanzeige verwendet wird)."""
        for w in (
            self._moving_led,
            self._moving_lbl,
            self._online_led,
            self._online_lbl,
            self._ref_led,
            self._ref_lbl,
        ):
            w.setVisible(bool(visible))

    def set_text_overlay_visible(self, visible: bool) -> None:
        self._text_overlay_visible = bool(visible)
        self.update()

    def apply_label_text_color(self, color: QColor) -> None:
        """Textfarbe aus Palette setzen (palette() in Stylesheet funktioniert unzuverlässig)."""
        style = f"font-size: 16px; font-weight: bold; color: {color.name()};"
        self._ref_lbl.setStyleSheet(style)
        self._moving_lbl.setStyleSheet(style)
        self._online_lbl.setStyleSheet(style)

    def set_overlay_ist_soll(self, ist: str, soll: str) -> None:
        """Ist/Soll oben links/rechts im EL-Kompass."""
        self._overlay_ist = str(ist or "")
        self._overlay_soll = str(soll or "")
        self.update()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._layout_corner_controls()

    def _layout_corner_controls(self) -> None:
        """LEDs rechts; eine Textzeile Ist/Soll oben, dann Abstand zu den LEDs."""
        margin = 7
        text_top = 13
        line_gap = 22
        led_extra_down = 5  # war 25 → LEDs 20px nach oben
        line_first_led = text_top + line_gap + led_extra_down
        if self._top_center_widget is not None:
            w = (
                self._top_center_widget.sizeHint().width()
                if self._top_center_widget.sizeHint().isValid()
                else 140
            )
            h = (
                self._top_center_widget.sizeHint().height()
                if self._top_center_widget.sizeHint().isValid()
                else 24
            )
            x = (self.width() - w) // 2
            y = 0
            self._top_center_widget.setGeometry(x, y, max(w, 120), max(h, 22))
            self._top_center_widget.raise_()
        led_d = self._led_d
        row_h = 22
        line2_y = line_first_led
        line3_y = line2_y + row_h
        line4_y = line3_y + row_h
        lbl_w = 80
        led_x = self.width() - margin - lbl_w - 4 - led_d
        lbl_x = led_x + led_d + 4

        self._moving_led.setGeometry(led_x, line2_y + 5, led_d, led_d)
        self._moving_lbl.setGeometry(lbl_x, line2_y, lbl_w, row_h)
        self._moving_led.raise_()
        self._moving_lbl.raise_()

        self._online_led.setGeometry(led_x, line3_y + 5, led_d, led_d)
        self._online_lbl.setGeometry(lbl_x, line3_y, lbl_w, row_h)
        self._online_led.raise_()
        self._online_lbl.raise_()

        self._ref_led.setGeometry(led_x, line4_y + 5, led_d, led_d)
        self._ref_lbl.setGeometry(lbl_x, line4_y, lbl_w, row_h)
        self._ref_led.raise_()
        self._ref_lbl.raise_()

        if self._soll_overlay is not None:
            margin = 7
            text_top = 13
            sh = self._soll_overlay.sizeHint()
            ow = int(sh.width()) if sh.width() > 0 else 140
            oh = max(int(sh.height()) if sh.height() > 0 else 24, 22)
            ox = int(self.width() - margin - ow)
            oy = max(0, int(text_top) - _SOLL_OVERLAY_Y_SHIFT_PX)
            self._soll_overlay.setGeometry(ox, oy, ow, oh)
            self._soll_overlay.raise_()

    def set_current_deg(self, deg: Optional[float]) -> None:
        self._current_deg = None if deg is None else clamp_el(deg, self._max_deg)
        self.update()

    def set_target_deg(self, deg: Optional[float]) -> None:
        self._target_deg = None if deg is None else clamp_el(deg, self._max_deg)
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
        """ACCBINS für 5px Heatmap-Ring (EL immer 36 Bins, auf 90°/180° gestreckt)."""
        need = 36
        self._bins_cw = list(cw) if cw is not None and len(cw) >= need else None
        self._bins_ccw = list(ccw) if ccw is not None and len(ccw) >= need else None
        if self._bins_cw is None and self._bins_ccw is None:
            self._heatmap_auto_smooth.clear()
        self.update()

    def set_heatmap_visible(self, on: bool) -> None:
        """Ob der ACCBINS-Heatmap-Ring angezeigt wird."""
        self._heatmap_visible = bool(on)
        self.update()

    def set_heatmap_offset_deg(self, offset: float) -> None:
        """Heatmap um Antennenversatz drehen (0° = Horizont)."""
        self._heatmap_offset_deg = float(offset)
        self.update()

    def set_heatmap_scale(self, scale: Optional[HeatmapScale]) -> None:
        """Optionale Last-Skala; None = automatische Min/Max-Skala."""
        self._heatmap_scale = scale
        if scale is not None:
            self._heatmap_auto_smooth.clear()
        self.update()

    def _geom(self) -> tuple[float, float, float]:
        """Hilfsgeometrie: (cx, cy, r)

        90°: Viertelkreis, Anker unten rechts (Bogen nach links/oben).
        180°: Halbkreis, Anker unten mittig.
        Skala: 0° links, 90° oben, 180° rechts.
        """
        w = max(1, int(self.width()))
        h = max(1, int(self.height()))

        margin = int(max(48, min(w, h) * 0.10))
        inner_w = float(w - 2 * margin)
        inner_h = float(h - 2 * margin)

        label_pad = 0.10
        if self._max_deg >= 180.0:
            # Halbkreis: Breite 2r, Höhe r
            r = min(inner_w / 2.0, inner_h) * 0.82
            label_pad_px = r * label_pad
            bbox_w = 2.0 * r + label_pad_px
            bbox_h = r + label_pad_px
            cx = w / 2.0
            cy = (h + bbox_h) / 2.0
        else:
            base = min(inner_w, inner_h)
            r = base * 0.82
            label_pad_px = r * label_pad
            bbox_w = r + label_pad_px
            bbox_h = r + label_pad_px
            # Bogen nach links/oben → Mittelpunkt unten rechts
            cx = (w + bbox_w) / 2.0
            cy = (h + bbox_h) / 2.0
        return cx, cy, r

    @staticmethod
    def _el_xy(cx: float, cy: float, radius: float, deg: float) -> tuple[float, float]:
        """EL-Winkel → Bildschirm: 0° links, 90° oben, 180° rechts."""
        rad = math.radians(float(deg))
        return cx - math.cos(rad) * radius, cy - math.sin(rad) * radius

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

        # Math: 0°=rechts → EL: 0°=links, 90°=oben, 180°=rechts
        rad = math.atan2(dy, dx)
        math_deg = math.degrees(rad)
        if math_deg < 0.0:
            # Unter dem Horizont → auf nächsten Endpunkt abbilden
            deg = 0.0 if dx < 0.0 else float(self._max_deg)
        else:
            deg = 180.0 - math_deg
        deg = clamp_el(deg, self._max_deg)

        deg = round(float(deg), int(self._angle_decimals))
        deg = clamp_el(deg, self._max_deg)

        self.set_target_deg(deg)
        self.targetPicked.emit(deg)

    def pick_target(self, deg: float) -> None:
        """Ziel programmatisch setzen (wie Klick) – z.B. aus Soll-Eingabefeld."""
        deg = clamp_el(float(deg), self._max_deg)
        self.set_target_deg(deg)
        self.targetPicked.emit(deg)

    def paintEvent(self, _event):
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

            cx, cy, r = self._geom()
            arc_span = int(self._max_deg)

            # Rahmen: Arc + Radien (Qt: 0°=rechts CCW; EL 0°=links → Start 180°, Span CW negativ)
            painter.setPen(QPen(self.palette().color(QPalette.ColorRole.WindowText), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)

            arc_rect = QRectF(cx - r, cy - r, 2 * r, 2 * r)
            painter.drawArc(arc_rect, 180 * 16, -arc_span * 16)
            x0, y0 = self._el_xy(cx, cy, r, 0.0)
            painter.drawLine(QPointF(cx, cy), QPointF(x0, y0))  # 0° links
            if arc_span >= 180:
                x180, y180 = self._el_xy(cx, cy, r, 180.0)
                painter.drawLine(QPointF(cx, cy), QPointF(x180, y180))  # 180° rechts
            else:
                x90, y90 = self._el_xy(cx, cy, r, 90.0)
                painter.drawLine(QPointF(cx, cy), QPointF(x90, y90))  # 90° oben

            # Teilstriche
            tick_pen = QPen(self.palette().color(QPalette.ColorRole.WindowText), 1)
            painter.setPen(tick_pen)
            for a in range(0, arc_span + 1, 5):
                x1, y1 = self._el_xy(cx, cy, r * 0.90, a)
                if a % 15 == 0:
                    x2, y2 = self._el_xy(cx, cy, r * 1.00, a)
                else:
                    x2, y2 = self._el_xy(cx, cy, r * 0.96, a)
                painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))

            # Grad-Beschriftung
            painter.save()
            deg_font = painter.font()
            deg_font.setBold(False)
            deg_font.setPointSize(max(7, min(14, int(r * 0.045))))
            painter.setFont(deg_font)
            fm_deg = QFontMetrics(deg_font)
            painter.setPen(QPen(self.palette().color(QPalette.ColorRole.WindowText), 1))

            label_r = r * 1.06
            label_step = 15 if arc_span >= 180 else 10
            for a in range(0, arc_span + 1, label_step):
                txt = f"{a}°"
                tx, ty = self._el_xy(cx, cy, label_r, a)
                w = fm_deg.horizontalAdvance(txt)
                h = fm_deg.height()
                painter.drawText(QPointF(tx - w / 2.0, ty + h / 3.0), txt)
            painter.restore()

            # ACCBINS-Heatmap-Ring
            if self._heatmap_visible and (self._bins_cw or self._bins_ccw):
                paint_bins_heatmap_ring(
                    painter,
                    cx,
                    cy,
                    r,
                    self._bins_cw,
                    self._bins_ccw,
                    elevation=True,
                    ring_width=5.0,
                    offset_deg=self._heatmap_offset_deg,
                    scale=self._heatmap_scale,
                    auto_smooth_state=self._heatmap_auto_smooth
                    if self._heatmap_scale is None
                    else None,
                    el_arc_deg=float(self._max_deg),
                )

            # SOLL (gleiche 3D-Pfeiloptik wie AZ)
            if self._target_deg is not None:
                self._draw_arrow_3d(
                    painter,
                    cx,
                    cy,
                    r * 0.85,
                    self._target_deg,
                    QColor(160, 0, 0),
                    _ARROW_SHAFT_WIDTH,
                )

            # IST
            if self._current_deg is not None:
                self._draw_arrow_3d(
                    painter,
                    cx,
                    cy,
                    r * 0.92,
                    self._current_deg,
                    QColor(0, 120, 0),
                    _ARROW_SHAFT_WIDTH,
                )

            if self._text_overlay_visible:
                margin_txt = 7
                text_top = 13
                txt_font = painter.font()
                txt_font.setBold(True)
                txt_font.setPixelSize(16)
                painter.setFont(txt_font)
                painter.setPen(QPen(self.palette().color(QPalette.ColorRole.WindowText), 1))
                ist_s = self._overlay_ist
                painter.drawText(QPointF(float(margin_txt), float(text_top)), ist_s)

            # Mittelpunkt
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(self.palette().color(QPalette.ColorRole.WindowText))
            painter.drawEllipse(QRectF(cx - 5.0, cy - 5.0, 10.0, 10.0))

    @staticmethod
    def _arrow_gradient(
        cx: float, cy: float, rx: float, ry: float, color: QColor
    ) -> QLinearGradient:
        """Leichter 3D-Verlauf quer zur Pfeilrichtung (hell → mittel → dunkel)."""
        grad = QLinearGradient(cx - rx, cy - ry, cx + rx, cy + ry)
        grad.setColorAt(0.0, color.lighter(135))
        grad.setColorAt(0.5, color)
        grad.setColorAt(1.0, color.darker(135))
        return grad

    @classmethod
    def _draw_arrow_3d(
        cls,
        painter: QPainter,
        cx: float,
        cy: float,
        length: float,
        deg: float,
        color: QColor,
        width: float,
    ) -> None:
        """Pfeil wie AZ: 3D-Verlauf und gefüllte Dreiecksspitze (EL: 0°=links)."""
        rad = math.radians(float(deg))
        # EL: 0° Horizont links, 90° Zenit, 180° rechts
        fx, fy = -math.cos(rad), -math.sin(rad)
        rx, ry = -math.sin(rad), math.cos(rad)
        half_w = max(2.0, float(width) / 2.0)
        head_len = max(11.0, float(length) * 0.13)
        shaft_len = max(half_w * 1.2, float(length) - head_len)

        tip_x = cx + fx * length
        tip_y = cy + fy * length
        base_x = cx + fx * shaft_len
        base_y = cy + fy * shaft_len

        head_half = half_w * 1.4
        tip = QPointF(tip_x, tip_y)
        head_l = QPointF(base_x - rx * head_half, base_y - ry * head_half)
        head_r = QPointF(base_x + rx * head_half, base_y + ry * head_half)
        head_poly = QPolygonF([tip, head_l, head_r])

        tail_half = half_w * 0.22
        shaft_poly = QPolygonF(
            [
                QPointF(cx - rx * tail_half, cy - ry * tail_half),
                QPointF(cx + rx * tail_half, cy + ry * tail_half),
                head_r,
                head_l,
            ]
        )

        mid_x = (cx + base_x) / 2.0
        mid_y = (cy + base_y) / 2.0
        lo = color.darker(140)

        painter.save()
        painter.translate(0.9, 1.1)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 55))
        painter.drawPolygon(shaft_poly)
        painter.drawPolygon(head_poly)
        painter.restore()

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(cls._arrow_gradient(mid_x, mid_y, rx, ry, color))
        painter.drawPolygon(shaft_poly)

        painter.setPen(QPen(lo, 1.0))
        painter.setBrush(cls._arrow_gradient(tip_x, tip_y, rx, ry, color))
        painter.drawPolygon(head_poly)
