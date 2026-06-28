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
import os
import sys
import time
from pathlib import Path
from typing import Optional, List

from PySide6.QtCore import Qt, QTimer, Signal, QPointF, QRectF
from PySide6.QtGui import (
    QColor,
    QFontMetrics,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QPixmap,
    QPolygonF,
)
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
# Gleiches Blau wie Windgeschwindigkeit im Kompass-Panel (#5eb8ff)
_WIND_SPEED_LABEL_COLOR = QColor(0x5E, 0xB8, 0xFF)
_ARROW_SHAFT_WIDTH = 7.0
# Windrose-Hintergrund: 85 % transparent (= 15 % deckend)
_WINDROSE_BG_OPACITY = 0.15

_CACHED_WINDROSE: Optional[QPixmap] = None


def _load_windrose_pixmap() -> QPixmap:
    """Windrose-Hintergrundbild (Windrose.png) aus Paket oder Projektroot."""
    global _CACHED_WINDROSE
    if _CACHED_WINDROSE is not None:
        return _CACHED_WINDROSE

    names = ("Windrose.png", "windrose.png", "WINDROSE.png")
    roots: list[Path] = []
    try:
        roots.append(Path(sys._MEIPASS))  # type: ignore[attr-defined]
    except AttributeError:
        pass
    try:
        roots.append(Path(__file__).resolve().parent)  # compass/
    except Exception:
        pass
    try:
        roots.append(Path(__file__).resolve().parents[1])  # rotortcpbridge/
    except Exception:
        pass
    try:
        roots.append(Path(__file__).resolve().parents[2])  # project root
    except Exception:
        pass
    try:
        roots.append(Path.cwd())
    except Exception:
        pass

    seen: set[str] = set()
    for root in roots:
        key = os.path.normcase(str(root))
        if key in seen:
            continue
        seen.add(key)
        for name in names:
            p = root / name
            if p.is_file():
                pm = QPixmap(str(p))
                if not pm.isNull():
                    _CACHED_WINDROSE = pm
                    return pm
        pkg = root / "rotortcpbridge"
        for name in names:
            p = pkg / name
            if p.is_file():
                pm = QPixmap(str(p))
                if not pm.isNull():
                    _CACHED_WINDROSE = pm
                    return pm

    _CACHED_WINDROSE = QPixmap()
    return _CACHED_WINDROSE


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
        self._text_overlay_visible: bool = True
        self._dipole_active: bool = False
        self._windrose_pixmap = _load_windrose_pixmap()
        self._windrose_scaled_px: int = 0
        self._windrose_scaled_pm: Optional[QPixmap] = None

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
            w.setVisible(False)

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

    def set_text_overlay_visible(self, visible: bool) -> None:
        """Text-Overlay (Ist/Wind-Zeile auf dem Kompass) ein-/ausblenden."""
        self._text_overlay_visible = bool(visible)
        self.update()

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

    def set_ref_led_state(self, on: bool) -> None:
        self._ref_led.set_state(bool(on))

    def set_ref_led_homing(self, active: bool) -> None:
        self._ref_led.set_blinking_red_green(bool(active))

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
        self._windrose_scaled_px = 0
        self._windrose_scaled_pm = None
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

    def set_dipole_active(self, active: bool) -> None:
        """Dipol-Modus: zweiter, um 180° versetzter gestrichelter Pfeil für Ist/Soll."""
        on = bool(active)
        if self._dipole_active != on:
            self._dipole_active = on
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
        """Anzahl Sektoren für OM-Radar (10–100), Standard 20."""
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

    def _windrose_pixmap_scaled(self, side: float) -> QPixmap:
        """Windrose mit glatter Interpolation auf Zielgröße skalieren (Cache pro Größe)."""
        pm = self._windrose_pixmap
        if pm is None or pm.isNull():
            return QPixmap()
        px = max(1, int(round(side)))
        if self._windrose_scaled_px == px and self._windrose_scaled_pm is not None:
            return self._windrose_scaled_pm
        scaled = pm.scaled(
            px,
            px,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._windrose_scaled_px = px
        self._windrose_scaled_pm = scaled
        return scaled

    def _draw_windrose_background(
        self, painter: QPainter, cx: float, cy: float, r: float
    ) -> None:
        """Windrose als Hintergrund: Durchmesser = Außenkreis minus 100 px, skaliert mit."""
        inset = float(px_to_dip(self, 300))
        side = max(8.0, 2.0 * r - inset)
        scaled = self._windrose_pixmap_scaled(side)
        if scaled.isNull():
            return
        dest = QRectF(cx - side / 2.0, cy - side / 2.0, side, side)
        painter.save()
        painter.setOpacity(_WINDROSE_BG_OPACITY)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        clip = QPainterPath()
        clip.addEllipse(QRectF(cx - r, cy - r, 2.0 * r, 2.0 * r))
        painter.setClipPath(clip)
        painter.drawPixmap(dest, scaled, scaled.rect())
        painter.restore()

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
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

            cx, cy, r = self._geom()

            self._draw_windrose_background(painter, cx, cy, r)

            # Hauptkreis zuerst (darüber der Windrose)
            painter.setPen(QPen(self.palette().color(QPalette.ColorRole.WindowText), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QRectF(cx - r, cy - r, 2 * r, 2 * r))

            # Teilstriche
            tick_pen = QPen(self.palette().color(QPalette.ColorRole.WindowText), 2)
            tick_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
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

            # Himmelsrichtungen (Blau wie Windgeschwindigkeit; DE: N/O/S/W, EN: N/E/S/W)
            font = painter.font()
            font.setBold(True)
            font.setPointSize(max(font.pointSize(), int(r * 0.12)))
            painter.setFont(font)
            fm = QFontMetrics(font)
            painter.setPen(QPen(_WIND_SPEED_LABEL_COLOR, 1))

            def draw_label(text: str, angle: float) -> None:
                rad = math.radians(angle)
                tx = cx + math.sin(rad) * (r * 0.70)
                ty = cy - math.cos(rad) * (r * 0.70)
                w = fm.horizontalAdvance(text)
                h = fm.height()
                painter.drawText(QPointF(tx - w / 2.0, ty + h / 4.0), text)

            for label, angle in (
                (t("compass.cardinal_n"), 0),
                (t("compass.cardinal_e"), 90),
                (t("compass.cardinal_s"), 180),
                (t("compass.cardinal_w"), 270),
            ):
                draw_label(label, angle)

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

            # SOLL (durchgezogen)
            if self._target_deg is not None:
                target_color = QColor(160, 0, 0)
                self._draw_arrow_3d(
                    painter, cx, cy, r * 0.85, self._target_deg, target_color, _ARROW_SHAFT_WIDTH
                )
                if self._dipole_active:
                    self._draw_arrow_3d(
                        painter,
                        cx,
                        cy,
                        r * 0.85,
                        wrap_deg(self._target_deg + 180.0),
                        target_color,
                        _ARROW_SHAFT_WIDTH,
                        dashed=True,
                    )

            # IST (durchgezogen)
            if self._current_deg is not None:
                current_color = QColor(0, 120, 0)
                self._draw_arrow_3d(
                    painter, cx, cy, r * 0.92, self._current_deg, current_color, _ARROW_SHAFT_WIDTH
                )
                if self._dipole_active:
                    self._draw_arrow_3d(
                        painter,
                        cx,
                        cy,
                        r * 0.92,
                        wrap_deg(self._current_deg + 180.0),
                        current_color,
                        _ARROW_SHAFT_WIDTH,
                        dashed=True,
                    )

            # WIND Richtung (blau, halb so lang wie der grüne IST-Pfeil)
            if self._wind_visible and self._wind_dir_draw_deg is not None:
                wd = float(self._wind_dir_draw_deg)
                if self._wind_dir_mode == "to":
                    wd = wrap_deg(wd + 180.0)
                self._draw_arrow_3d(
                    painter, cx, cy, r * 0.46, wd, QColor(0, 90, 220), _ARROW_SHAFT_WIDTH
                )

            # Wind (links) Zeile 1; Ist-Text Zeile 2. Ziel-Eingabe liegt als Widget oben rechts (Zeile 1).
            margin = 7
            top_y = 13
            line_gap = 22
            txt_font = painter.font()
            txt_font.setBold(True)
            txt_font.setPixelSize(16)
            painter.setFont(txt_font)
            painter.setPen(QPen(self.palette().color(QPalette.ColorRole.WindowText), 1))

            if self._text_overlay_visible:
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
        *,
        dashed: bool = False,
    ) -> None:
        """Pfeil mit leichtem 3D-Verlauf und gefüllter Dreiecksspitze."""
        rad = math.radians(deg)
        fx, fy = math.sin(rad), -math.cos(rad)
        rx, ry = math.cos(rad), math.sin(rad)
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

        # Leichter Schatten für Tiefe
        painter.save()
        painter.translate(0.9, 1.1)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 55))
        if not dashed:
            painter.drawPolygon(shaft_poly)
        painter.drawPolygon(head_poly)
        painter.restore()

        if dashed:
            dash_pen = QPen(color, width, Qt.PenStyle.DashLine, Qt.PenCapStyle.RoundCap)
            dash_pen.setDashPattern([6.0, 4.0])
            painter.setPen(dash_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawLine(QPointF(cx, cy), QPointF(base_x, base_y))
        else:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(cls._arrow_gradient(mid_x, mid_y, rx, ry, color))
            painter.drawPolygon(shaft_poly)

        painter.setPen(QPen(lo, 1.0))
        painter.setBrush(cls._arrow_gradient(tip_x, tip_y, rx, ry, color))
        painter.drawPolygon(head_poly)
