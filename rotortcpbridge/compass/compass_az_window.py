from __future__ import annotations

"""Kompass-Fenster (AZ) für RotorTcpBridge.

Ziele:
- Der Kompass soll wie im Projekt "rotor_rs485_gui" funktionieren:
  * Anzeige von IST- und SOLL-Winkel
  * Ziel per Klick auf den Außenring auswählen
  * Klick sendet sofort ein SETPOSDG an den AZ-Rotor

Unterschiede zur RS485-GUI:
- Wir nutzen die bereits vorhandene Polling-Logik von RotorController.
  Die IST-Position folgt ctrl.az (get_smoothed_pos_d10f, SmoothDamp) und wird nicht separat gepollt.
- Es wird ausschließlich der AZ-Rotor angesprochen (dst = ctrl.slave_az).
- Last-Ringe (CAL/LIVE) werden im separaten Statistik-Fenster angezeigt.
"""

import math
import time
from typing import Optional, List

from PySide6.QtCore import Qt, QTimer, Signal, QPointF, QRectF
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPalette, QPen, QPolygonF
from PySide6.QtWidgets import QLabel, QWidget

from ..angle_utils import shortest_delta_deg, wrap_deg
from ..i18n import t
from ..ui.led_widget import Led
from ..ui.ui_utils import px_to_dip
from .statistic_compass_widget import (
    OM_RADAR_N_DEFAULT,
    HeatmapScale,
    paint_az_ring_gap_black,
    paint_bins_heatmap_ring,
    paint_dwell_ring,
    paint_om_radar_ring,
)

# Reihenfolge der Ringe von innen nach außen (wie die AZ-Liste im Kompass)
_AZ_RING_ORDER = {"strom": 0, "om_radar": 1, "dwell": 2}

# „Soll:“ + Eingabe (rechts oben) zusätzlich nach oben (kleineres oy)
_SOLL_OVERLAY_Y_SHIFT_PX = 60
# Kompass-Mitte vertikal (px nach oben); Platz für Ringe/Beschriftung, bei Bedarf anpassen
_COMPASS_CENTER_Y_SHIFT_PX = 0


class CompassWidget(QWidget):
    """Einfacher Kompass (QPainter) mit zwei Zeigern (IST/SOLL)."""

    # Signal: Zielwinkel wurde per Klick gewählt
    targetPicked = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_deg: Optional[float] = None
        self._target_deg: Optional[float] = None
        self._wind_dir_deg: Optional[float] = None
        self._wind_dir_draw_deg: Optional[float] = None
        self._wind_kmh: Optional[float] = None
        self._wind_visible: bool = True
        # "from" = woher der Wind kommt, "to" = wohin er weht
        self._wind_dir_mode: str = "to"
        # Klick-Rundung (RotorTcpBridge arbeitet intern in 0,1°)
        self._angle_decimals: int = 1
        self._wind_anim_speed_dps: float = 300.0
        self._wind_anim_last_ts: float = time.monotonic()
        self._wind_anim_timer = QTimer(self)
        self._wind_anim_timer.setInterval(16)
        self._wind_anim_timer.timeout.connect(self._animate_wind_dir)
        self._bins_cw: Optional[List[int]] = None
        self._bins_ccw: Optional[List[int]] = None
        self._heatmap_visible: bool = False
        # Bis zu zwei Einträge aus: "strom" | "om_radar" | "dwell" (innen→außen sortiert)
        self._heatmap_modes: List[str] = []
        self._om_radar_counts: Optional[List[float]] = None
        self._om_radar_n: int = OM_RADAR_N_DEFAULT
        self._dwell_seconds: Optional[List[float]] = None
        self._dwell_full_sec: float = 300.0
        self._dwell_n: int = OM_RADAR_N_DEFAULT
        self._heatmap_offset_deg: float = 0.0
        self._heatmap_scale: Optional[HeatmapScale] = None
        # EMA-Zustand für automatische Strom-Heatmap-Skala (verhindert Farbflattern bei leicht schwankendem Min/Max)
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
        self._ref_lbl = QLabel(t("compass.ref_led_label_az"), self)
        self._ref_lbl.setStyleSheet(lbl_style)

        for w in (
            self._moving_led,
            self._moving_lbl,
            self._online_led,
            self._online_lbl,
            self._ref_led,
            self._ref_lbl,
        ):
            w.setVisible(True)

        self.setMinimumSize(280, 280)

    def set_top_center_widget(self, widget: Optional[QWidget]) -> None:
        """Widget oben mittig über dem Kompass (z.B. Antennen-Dropdown). None = keine Überlagerung."""
        old = self._top_center_widget
        self._top_center_widget = widget
        if old is not None and old is not widget:
            old.setParent(None)
            old.hide()
        if widget is not None:
            widget.setParent(self)
            widget.show()
            widget.raise_()
        self._layout_corner_controls()
        self.update()

    def set_soll_overlay_widget(self, widget: Optional[QWidget]) -> None:
        """Soll-Label + Eingabe oben rechts (statt nur Textzeichnung). None = entfernen."""
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
        self._layout_corner_controls()

    def _layout_corner_controls(self) -> None:
        """LED-Zeilen unter Wind + Ist-Zeile; Soll/Target rechts auf Höhe der Ist-/Pos-Zeile."""
        margin = 7
        text_top = 13
        line_gap = 22
        led_extra_down = 5  # war 25 → LEDs 20px nach oben
        if self._wind_visible:
            line_first_led = text_top + 2 * line_gap + led_extra_down
        else:
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
        lbl_x = margin + led_d + 4
        row_h = 22
        line2_y = line_first_led
        line3_y = line2_y + row_h
        line4_y = line3_y + row_h

        self._moving_led.setGeometry(margin, line2_y + 5, led_d, led_d)
        self._moving_lbl.setGeometry(lbl_x, line2_y, 200, row_h)
        self._moving_led.raise_()
        self._moving_lbl.raise_()

        self._online_led.setGeometry(margin, line3_y + 5, led_d, led_d)
        self._online_lbl.setGeometry(lbl_x, line3_y, 200, row_h)
        self._online_led.raise_()
        self._online_lbl.raise_()

        self._ref_led.setGeometry(margin, line4_y + 5, led_d, led_d)
        self._ref_lbl.setGeometry(lbl_x, line4_y, 200, row_h)
        self._ref_led.raise_()
        self._ref_lbl.raise_()

        if self._soll_overlay is not None:
            # Mit Wind: gleiche Zeile wie die gemalte Ist-/Pos-Zeile (links unter „Wind:“); leicht nach oben für optische Ausrichtung zur Text-Baseline
            # Ohne Wind: gleiche Zeile wie Ist-/Pos (nur eine Textzeile oben)
            if self._wind_visible:
                row_y = float(text_top + line_gap - px_to_dip(self, 2))
            else:
                row_y = float(text_top)
            sh = self._soll_overlay.sizeHint()
            ow = int(sh.width()) if sh.width() > 0 else 140
            oh = max(int(sh.height()) if sh.height() > 0 else 24, 22)
            ox = int(self.width() - margin - ow)
            oy = max(0, int(row_y) - _SOLL_OVERLAY_Y_SHIFT_PX)
            self._soll_overlay.setGeometry(ox, oy, ow, oh)
            self._soll_overlay.raise_()

    def set_current_deg(self, deg: Optional[float]) -> None:
        self._current_deg = None if deg is None else wrap_deg(deg)
        self.update()

    def set_target_deg(self, deg: Optional[float]) -> None:
        self._target_deg = None if deg is None else wrap_deg(deg)
        self.update()

    def set_wind_dir_deg(self, deg: Optional[float]) -> None:
        self._wind_dir_deg = None if deg is None else wrap_deg(deg)
        if self._wind_dir_deg is None:
            self._wind_dir_draw_deg = None
            self._wind_anim_timer.stop()
        else:
            if self._wind_dir_draw_deg is None:
                self._wind_dir_draw_deg = float(self._wind_dir_deg)
            self._wind_anim_last_ts = time.monotonic()
            if not self._wind_anim_timer.isActive():
                self._wind_anim_timer.start()
        self.update()

    def set_wind_kmh(self, kmh: Optional[float]) -> None:
        try:
            self._wind_kmh = None if kmh is None else float(kmh)
        except Exception:
            self._wind_kmh = None
        self.update()

    def set_wind_dir_mode(self, mode: str) -> None:
        m = str(mode or "").strip().lower()
        if m not in ("from", "to"):
            m = "to"
        self._wind_dir_mode = m
        self.update()

    def set_wind_visible(self, on: bool) -> None:
        self._wind_visible = bool(on)
        self._layout_corner_controls()
        self.update()

    def set_overlay_ist_soll(self, ist: str, soll: str) -> None:
        """Ist/Soll als Textzeile(n) im oberen Bereich (unter der Wind-Zeile, falls sichtbar)."""
        self._overlay_ist = str(ist or "")
        self._overlay_soll = str(soll or "")
        self.update()

    def set_bins(self, cw: Optional[List[int]], ccw: Optional[List[int]]) -> None:
        """ACCBINS für 5px Heatmap-Ring. 72 Werte je Richtung."""
        self._bins_cw = list(cw) if cw is not None and len(cw) >= 72 else None
        self._bins_ccw = list(ccw) if ccw is not None and len(ccw) >= 72 else None
        if self._bins_cw is None and self._bins_ccw is None:
            self._heatmap_auto_smooth.clear()
        self.update()

    def set_heatmap_visible(self, on: bool) -> None:
        """Abwärtskompatibel: nur noch über set_heatmap_modes gesteuert."""
        if not on:
            self.set_heatmap_modes([])

    @staticmethod
    def _sort_ring_modes(modes: List[str]) -> List[str]:
        """Innen → außen: Strom, OM-Radar, Standzeit."""
        allowed = frozenset(("strom", "om_radar", "dwell"))
        seen: set[str] = set()
        out: List[str] = []
        for m in modes:
            s = str(m or "").strip().lower()
            if s in allowed and s not in seen:
                seen.add(s)
                out.append(s)
                if len(out) >= 2:
                    break
        out.sort(key=lambda x: _AZ_RING_ORDER.get(x, 99))
        return out

    def set_heatmap_modes(self, modes: List[str]) -> None:
        """0–2 Ringe: strom | om_radar | dwell (Anzeige-Reihenfolge innen nach außen fest)."""
        self._heatmap_modes = self._sort_ring_modes(list(modes or []))
        self._heatmap_visible = len(self._heatmap_modes) > 0
        self.update()

    def set_heatmap_mode(self, mode: str) -> None:
        """Einzelmodus (ältere API): 'off' oder ein Ring."""
        m = str(mode or "").strip().lower()
        if m in ("off", ""):
            self.set_heatmap_modes([])
        elif m in ("strom", "om_radar", "dwell"):
            self.set_heatmap_modes([m])
        else:
            self.set_heatmap_modes([])

    def set_dwell_ring_data(
        self,
        seconds: Optional[List[float]],
        full_seconds: float,
        n_sectors: int,
    ) -> None:
        """Standzeit-Ring: kumulative Sekunden je Sektor, Skala bis full_seconds → rot."""
        self._dwell_seconds = list(seconds) if seconds else None
        try:
            self._dwell_full_sec = max(0.001, float(full_seconds))
        except (TypeError, ValueError):
            self._dwell_full_sec = 300.0
        self._dwell_n = max(10, min(100, int(n_sectors)))
        self.update()

    def set_om_radar_sector_count(self, n: int) -> None:
        """Anzahl Sektoren für OM-Radar (10–100), Standard 60."""
        nn = max(10, min(100, int(n)))
        if nn != self._om_radar_n:
            self._om_radar_n = nn
            self.update()

    def set_om_radar_counts(self, counts: Optional[List[float]]) -> None:
        """Erwartete OM-Dichte je Sektor (Länge = Sektorenanzahl); Anteile aus Öffnungswinkel, Summe ≈ OM-Anzahl."""
        if not counts:
            self._om_radar_counts = None
            self.update()
            return
        v: List[float] = []
        for i in range(self._om_radar_n):
            if i < len(counts):
                try:
                    v.append(max(0.0, float(counts[i])))
                except (TypeError, ValueError):
                    v.append(0.0)
            else:
                v.append(0.0)
        self._om_radar_counts = v
        self.update()

    def set_heatmap_offset_deg(self, offset: float) -> None:
        """Heatmap um Antennenversatz drehen (0° = Nord)."""
        self._heatmap_offset_deg = float(offset)
        self.update()

    def set_heatmap_scale(self, scale: Optional[HeatmapScale]) -> None:
        """Optionale Last-Skala (blau/rot-Schwellen, Normbereich); None = auto."""
        self._heatmap_scale = scale
        if scale is not None:
            self._heatmap_auto_smooth.clear()
        self.update()

    def _animate_wind_dir(self) -> None:
        if self._wind_dir_deg is None or self._wind_dir_draw_deg is None:
            self._wind_anim_timer.stop()
            return
        now = time.monotonic()
        dt = max(0.0, min(0.2, now - self._wind_anim_last_ts))
        self._wind_anim_last_ts = now
        delta = shortest_delta_deg(self._wind_dir_draw_deg, self._wind_dir_deg)
        max_step = float(self._wind_anim_speed_dps) * dt
        if abs(delta) <= max(0.4, max_step):
            self._wind_dir_draw_deg = float(self._wind_dir_deg)
            self._wind_anim_timer.stop()
        else:
            # Sanfte Interpolation pro Tick + harte Obergrenze (keine Sprünge).
            desired_step = delta * 0.30
            step = max(-max_step, min(max_step, desired_step))
            self._wind_dir_draw_deg = wrap_deg(float(self._wind_dir_draw_deg) + step)
        self.update()

    def set_angle_decimals(self, decimals: int) -> None:
        """Anzahl der Dezimalstellen für Klick-Rundung setzen."""
        try:
            d = int(decimals)
        except Exception:
            d = 1
        if d not in (1, 2):
            d = 1
        self._angle_decimals = d

    def _geom(self) -> tuple[float, float, float]:
        """Hilfsgeometrie: (cx, cy, r). Rand oben/unten für Beschriftung + äußere Ringe."""
        rect = self.rect().adjusted(10, 26, -10, -14)
        cx = float(rect.center().x())
        cy = float(rect.center().y()) - float(_COMPASS_CENTER_Y_SHIFT_PX)
        r = float(min(rect.width(), rect.height())) / 2.0
        return cx, cy, r

    def mousePressEvent(self, event):
        """Klick auf den Außenring setzt SOLL."""
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)

        cx, cy, r = self._geom()
        pos = event.position()
        dx = float(pos.x() - cx)
        dy = float(pos.y() - cy)
        dist = math.hypot(dx, dy)

        # Nur Klicks auf dem Außenring akzeptieren
        inner = r * 0.78
        outer = r * 1.04
        if dist < inner or dist > outer:
            return super().mousePressEvent(event)

        # 0° = Norden, 90° = Osten, 180° = Süden, 270° = Westen
        rad = math.atan2(dx, -dy)
        deg = wrap_deg(math.degrees(rad))

        # Auf Encoder-Auflösung runden
        deg = round(deg, int(self._angle_decimals))

        self.set_target_deg(deg)
        self.targetPicked.emit(deg)

    def pick_target(self, deg: float) -> None:
        """Ziel programmatisch setzen (wie Klick) – z.B. aus Soll-Eingabefeld."""
        deg = wrap_deg(float(deg))
        self.set_target_deg(deg)
        self.targetPicked.emit(deg)

    def paintEvent(self, _event):
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

            cx, cy, r = self._geom()

            # Hauptkreis zuerst (darunter)
            painter.setPen(QPen(self.palette().color(QPalette.ColorRole.WindowText), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QRectF(cx - r, cy - r, 2 * r, 2 * r))

            # Teilstriche
            tick_pen = QPen(self.palette().color(QPalette.ColorRole.WindowText), 1)
            painter.setPen(tick_pen)
            for a in range(0, 360, 10):
                rad = math.radians(a)
                x1 = cx + math.sin(rad) * (r * 0.90)
                y1 = cy - math.cos(rad) * (r * 0.90)
                if a % 30 == 0:
                    x2 = cx + math.sin(rad) * (r * 1.00)
                    y2 = cy - math.cos(rad) * (r * 1.00)
                else:
                    x2 = cx + math.sin(rad) * (r * 0.96)
                    y2 = cy - math.cos(rad) * (r * 0.96)
                painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))

            # Grad-Beschriftung (alle 20°; Kardinalpunkte weglassen)
            painter.save()
            deg_font = painter.font()
            deg_font.setBold(False)
            deg_font.setPointSize(max(7, int(r * 0.06)))
            painter.setFont(deg_font)
            fm_deg = QFontMetrics(deg_font)
            painter.setPen(QPen(self.palette().color(QPalette.ColorRole.WindowText), 1))

            label_r = max(r * 0.60, (r * 0.88) - 30.0)
            for a in range(0, 360, 20):
                if a % 90 == 0:
                    continue
                txt = f"{a}°"
                rad = math.radians(a)
                tx = cx + math.sin(rad) * label_r
                ty = cy - math.cos(rad) * label_r
                w = fm_deg.horizontalAdvance(txt)
                h = fm_deg.height()
                painter.drawText(QPointF(tx - w / 2.0, ty + h / 3.0), txt)
            painter.restore()

            # Himmelsrichtungen
            font = painter.font()
            font.setBold(True)
            font.setPointSize(max(font.pointSize(), int(r * 0.12)))
            painter.setFont(font)
            fm = QFontMetrics(font)

            def draw_label(text: str, angle: float) -> None:
                rad = math.radians(angle)
                tx = cx + math.sin(rad) * (r * 0.70)
                ty = cy - math.cos(rad) * (r * 0.70)
                w = fm.horizontalAdvance(text)
                h = fm.height()
                painter.drawText(QPointF(tx - w / 2.0, ty + h / 4.0), text)

            draw_label("N", 0)
            draw_label("O", 90)
            draw_label("S", 180)
            draw_label("W", 270)

            # Heatmap-Ringe: Farbring 7px, dazwischen 1px schwarz; innen nach außen = Strom → OM-Radar → Standzeit
            ring_w = 7.0
            gap_w = 1.0
            step = ring_w + gap_w
            modes = self._heatmap_modes if self._heatmap_visible else []
            for i, mode in enumerate(modes):
                inner_r = r + float(i) * step
                if mode == "strom" and (self._bins_cw or self._bins_ccw):
                    paint_bins_heatmap_ring(
                        painter,
                        cx,
                        cy,
                        inner_r,
                        self._bins_cw,
                        self._bins_ccw,
                        elevation=False,
                        ring_width=ring_w,
                        offset_deg=self._heatmap_offset_deg,
                        scale=self._heatmap_scale,
                        auto_smooth_state=self._heatmap_auto_smooth
                        if self._heatmap_scale is None
                        else None,
                    )
                elif mode == "om_radar":
                    # OM-Zähler sind in geografischer Peilung (Nord=0°); kein Antennenversatz wie bei Strom/Standzeit (Rotor-Koordinaten).
                    paint_om_radar_ring(
                        painter,
                        cx,
                        cy,
                        inner_r,
                        self._om_radar_counts,
                        ring_width=ring_w,
                        offset_deg=0.0,
                        n_sectors=self._om_radar_n,
                    )
                elif mode == "dwell":
                    paint_dwell_ring(
                        painter,
                        cx,
                        cy,
                        inner_r,
                        self._dwell_seconds,
                        self._dwell_full_sec,
                        ring_width=ring_w,
                        offset_deg=self._heatmap_offset_deg,
                        n_sectors=self._dwell_n,
                    )
                if i < len(modes) - 1:
                    paint_az_ring_gap_black(painter, cx, cy, inner_r + ring_w, gap_w)

            # Rotes Dreieck: Anschlag der Antenne (auf Kreislinie, nach innen zeigend)
            self._draw_anschlag_triangle(painter, cx, cy, r)

            # Kreis-Kontur erneut
            painter.setPen(QPen(self.palette().color(QPalette.ColorRole.WindowText), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QRectF(cx - r, cy - r, 2 * r, 2 * r))

            # SOLL (gestrichelt)
            if self._target_deg is not None:
                painter.setPen(QPen(QColor(160, 0, 0), 3, Qt.PenStyle.DashLine))
                self._draw_arrow(painter, cx, cy, r * 0.85, self._target_deg)

            # IST (durchgezogen)
            if self._current_deg is not None:
                painter.setPen(QPen(QColor(0, 120, 0), 4, Qt.PenStyle.SolidLine))
                self._draw_arrow(painter, cx, cy, r * 0.92, self._current_deg)

            # WIND Richtung (blau, halb so lang wie der grüne IST-Pfeil)
            if self._wind_visible and self._wind_dir_draw_deg is not None:
                painter.setPen(QPen(QColor(0, 90, 220), 3, Qt.PenStyle.SolidLine))
                wd = float(self._wind_dir_draw_deg)
                if self._wind_dir_mode == "to":
                    wd = wrap_deg(wd + 180.0)
                self._draw_arrow(painter, cx, cy, r * 0.46, wd)

            # Wind (links) Zeile 1; Ist-Text Zeile 2. Ziel-Eingabe liegt als Widget oben rechts (Zeile 1).
            margin = 7
            top_y = 13
            line_gap = 22
            txt_font = painter.font()
            txt_font.setBold(True)
            txt_font.setPixelSize(16)
            painter.setFont(txt_font)
            painter.setPen(QPen(self.palette().color(QPalette.ColorRole.WindowText), 1))

            ist_txt = self._overlay_ist or ""

            if self._wind_visible:
                speed_txt = "Wind: --.- km/h"
                if self._wind_kmh is not None:
                    speed_txt = f"Wind: {self._wind_kmh:.1f} km/h"
                painter.drawText(QPointF(float(margin), float(top_y)), speed_txt)

                row2_y = float(top_y + line_gap)
                painter.drawText(QPointF(float(margin), row2_y), ist_txt)
            else:
                painter.drawText(QPointF(float(margin), float(top_y)), ist_txt)

            # Mittelpunkt
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(self.palette().color(QPalette.ColorRole.WindowText))
            painter.drawEllipse(QRectF(cx - 5.0, cy - 5.0, 10.0, 10.0))

    def _draw_anschlag_triangle(self, painter: QPainter, cx: float, cy: float, r: float) -> None:
        """Rotes Dreieck auf der Kreislinie am Antennenversatz, Spitze nach innen."""
        deg = float(self._heatmap_offset_deg)
        spread = 4.0  # halb so groß (urspr. 8)
        tip_r = r * 0.96
        base_r = r
        rad = math.radians(deg)
        rad_l = math.radians(deg - spread)
        rad_r = math.radians(deg + spread)
        tip_x = cx + math.sin(rad) * tip_r
        tip_y = cy - math.cos(rad) * tip_r
        base_l_x = cx + math.sin(rad_l) * base_r
        base_l_y = cy - math.cos(rad_l) * base_r
        base_r_x = cx + math.sin(rad_r) * base_r
        base_r_y = cy - math.cos(rad_r) * base_r
        poly = QPolygonF(
            [QPointF(tip_x, tip_y), QPointF(base_l_x, base_l_y), QPointF(base_r_x, base_r_y)]
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(220, 0, 0))
        painter.drawPolygon(poly)

    @staticmethod
    def _draw_arrow(painter: QPainter, cx: float, cy: float, length: float, deg: float) -> None:
        rad = math.radians(deg)
        x2 = cx + math.sin(rad) * length
        y2 = cy - math.cos(rad) * length
        painter.drawLine(QPointF(cx, cy), QPointF(x2, y2))

        # Pfeilspitze
        head_len = max(10.0, length * 0.08)
        left = math.radians(deg + 150)
        right = math.radians(deg - 150)
        xl = x2 + math.sin(left) * head_len
        yl = y2 - math.cos(left) * head_len
        xr = x2 + math.sin(right) * head_len
        yr = y2 - math.cos(right) * head_len
        painter.drawLine(QPointF(x2, y2), QPointF(xl, yl))
        painter.drawLine(QPointF(x2, y2), QPointF(xr, yr))
