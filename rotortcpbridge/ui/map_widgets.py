"""Qt-Widgets für die Kartenansicht (Container, Wind-Overlay, WebEngine-Seite)."""

from __future__ import annotations

import math
import time
from typing import Optional
from urllib.parse import parse_qs, unquote, urlparse

from PySide6.QtCore import QRect, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QPainter, QPalette, QPen
from PySide6.QtWidgets import QFrame, QWidget
from PySide6.QtWebEngineCore import QWebEnginePage

from ..angle_utils import shortest_delta_deg, wrap_deg
from ..ui.weather_window import WindRoseWidget


class _MapContainer(QWidget):
    """Container für Karte + Wind-Overlay; positioniert Overlay rechts oben."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._wind_overlay: Optional[MapWindOverlay] = None

    def resizeEvent(self, event):
        super().resizeEvent(event)
        ov = getattr(self, "_wind_overlay", None)
        if ov is not None:
            margin = 12
            w, h = ov.width(), ov.height()
            ov.setGeometry(self.width() - w - margin, margin, w, h)
            ov.raise_()


class MapWindOverlay(QFrame):
    """Kompaktes Wind-Overlay mit PNG-Pfeil wie im Wetterfenster."""

    # Pfeil oben, km/h unten — extra Höhe + Abstand, damit der Richtungspfeil nicht an den Text stößt
    _OVERLAY_W = 90
    _OVERLAY_H = 100
    _MARGIN = 4
    _TEXT_STRIP_H = 24
    _ARROW_TEXT_GAP = 10

    def __init__(self, parent=None):
        super().__init__(parent)
        self._wind_dir_deg: Optional[float] = None
        self._wind_dir_draw_deg: Optional[float] = None
        self._wind_kmh: Optional[float] = None
        self._wind_dir_mode: str = "to"
        self._arrow_pixmap = WindRoseWidget._load_arrow_pixmap()
        self._wind_anim_speed_dps = 280.0
        self._wind_anim_last_ts = time.monotonic()
        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(16)
        self._anim_timer.timeout.connect(self._animate_wind_dir)
        self.setFixedSize(self._OVERLAY_W, self._OVERLAY_H)
        self.setObjectName("mapWindOverlay")
        self._dark_mode = False
        self._apply_theme()
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def set_wind_dir_deg(self, deg: Optional[float]) -> None:
        self._wind_dir_deg = None if deg is None else wrap_deg(deg)
        if self._wind_dir_deg is None:
            self._wind_dir_draw_deg = None
            self._anim_timer.stop()
        else:
            if self._wind_dir_draw_deg is None:
                self._wind_dir_draw_deg = float(self._wind_dir_deg)
            self._wind_anim_last_ts = time.monotonic()
            if not self._anim_timer.isActive():
                self._anim_timer.start()
        self.update()

    def set_wind_kmh(self, kmh: Optional[float]) -> None:
        self._wind_kmh = None if kmh is None else float(kmh)
        self.update()

    def set_wind_dir_mode(self, mode: str) -> None:
        m = str(mode or "").strip().lower()
        self._wind_dir_mode = m if m in ("from", "to") else "to"
        self.update()

    def set_dark_mode(self, dark: bool) -> None:
        if self._dark_mode != dark:
            self._dark_mode = bool(dark)
            self._apply_theme()

    def set_visible(self, on: bool) -> None:
        """Overlay ein-/ausblenden, wenn Windsensor offline."""
        self.setVisible(bool(on))

    def _apply_theme(self) -> None:
        # Wie Infopanel/ASNEAREST auf der Karte: halbtransparent + Rand (Karte scheint leicht durch)
        if self._dark_mode:
            self.setStyleSheet(
                "#mapWindOverlay { background-color: rgba(28, 28, 30, 0.45); color: #eaeaea; "
                "border-radius: 8px; border: 1px solid rgba(180, 180, 190, 0.25); "
                "}"
            )
        else:
            self.setStyleSheet(
                "#mapWindOverlay { background-color: rgba(255, 255, 255, 0.22); color: #1a1a1a; "
                "border-radius: 8px; border: 1px solid rgba(128, 128, 128, 0.35); "
                "}"
            )

    def _animate_wind_dir(self) -> None:
        if self._wind_dir_deg is None or self._wind_dir_draw_deg is None:
            self._anim_timer.stop()
            return
        now = time.monotonic()
        dt = max(0.0, min(0.2, now - self._wind_anim_last_ts))
        self._wind_anim_last_ts = now
        delta = shortest_delta_deg(self._wind_dir_draw_deg, self._wind_dir_deg)
        max_step = float(self._wind_anim_speed_dps) * dt
        if abs(delta) <= max(0.4, max_step):
            self._wind_dir_draw_deg = float(self._wind_dir_deg)
            self._anim_timer.stop()
        else:
            step = max(-max_step, min(max_step, delta * 0.30))
            self._wind_dir_draw_deg = wrap_deg(float(self._wind_dir_draw_deg) + step)
        self.update()

    def paintEvent(self, _event) -> None:
        with QPainter(self) as p:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            m = self._MARGIN
            full = self.rect().adjusted(m, m, -m, -m)
            # Unteren Streifen für km/h; Pfeil nur im Bereich darüber (mehr Platz für die Richtung)
            text_rect = QRect(
                full.left(),
                full.bottom() - self._TEXT_STRIP_H + 1,
                full.width(),
                self._TEXT_STRIP_H,
            )
            arrow_rect_h = max(32, full.height() - self._TEXT_STRIP_H - self._ARROW_TEXT_GAP)
            arrow_rect = QRect(
                full.left(),
                full.top(),
                full.width(),
                arrow_rect_h,
            )
            cx = float(arrow_rect.center().x())
            cy = float(arrow_rect.center().y())
            r = float(min(arrow_rect.width(), arrow_rect.height())) / 2.0 * 0.85
            if self._wind_dir_draw_deg is not None:
                wd = (
                    wrap_deg(float(self._wind_dir_draw_deg) + 180.0)
                    if self._wind_dir_mode == "to"
                    else float(self._wind_dir_draw_deg)
                )
                if not self._arrow_pixmap.isNull():
                    side = int(max(24.0, min(r * 1.6, 56.0)))
                    scaled = self._arrow_pixmap.scaled(
                        side,
                        side,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                    w, h = float(scaled.width()), float(scaled.height())
                    p.save()
                    p.translate(cx, cy)
                    p.rotate(float(wd))
                    p.drawPixmap(int(-w / 2.0), int(-h / 2.0), scaled)
                    p.restore()
                else:
                    p.setPen(QPen(self.palette().color(QPalette.ColorRole.WindowText), 2))
                    rad = math.radians(wd)
                    x2 = cx + math.sin(rad) * r
                    y2 = cy - math.cos(rad) * r
                    p.drawLine(int(cx), int(cy), int(x2), int(y2))
            p.setPen(QPen(QColor(234, 234, 234) if self._dark_mode else QColor(26, 26, 26)))
            f = self.font()
            f.setPointSize(f.pointSize() + 1)
            p.setFont(f)
            txt = "--.- km/h"
            if self._wind_kmh is not None:
                txt = f"{self._wind_kmh:.1f} km/h"
            p.drawText(text_rect, Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter, txt)


class MapWebPage(QWebEnginePage):
    """WebEnginePage die rotorapp://-Navigations abfängt."""

    def __init__(self, on_click_cb, on_tile_error_cb=None, parent=None):
        super().__init__(parent)
        self._on_click_cb = on_click_cb
        self._on_tile_error_cb = on_tile_error_cb

    def javaScriptConsoleMessage(self, level, message, line_number, source_id):
        if message == "ROTOR_TILEERROR" and self._on_tile_error_cb:
            self._on_tile_error_cb()

    def acceptNavigationRequest(self, url: QUrl, nav_type, is_main_frame: bool) -> bool:
        u = url.toString()
        if u.startswith("rotorapp://setaz?"):
            try:
                parsed = urlparse(u)
                qs = parse_qs(parsed.query or "")
                lat = float(qs.get("lat", [0])[0])
                lon = float(qs.get("lon", [0])[0])
                raw_dest = qs.get("asnearest_dest", [None])[0]
                asnearest_dest: str | None = None
                if raw_dest is not None and str(raw_dest).strip():
                    asnearest_dest = unquote(str(raw_dest).strip())
                if self._on_click_cb:
                    self._on_click_cb(lat, lon, asnearest_dest=asnearest_dest)
            except Exception:
                pass
            return False
        return super().acceptNavigationRequest(url, nav_type, is_main_frame)
