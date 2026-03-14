"""Wetter-Fenster mit Windrose und Temperatur-Anzeige."""
from __future__ import annotations

import math
import os
import sys
import time
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen, QPalette, QFontMetrics, QPixmap
from PySide6.QtWidgets import QDialog, QFormLayout, QLabel, QVBoxLayout, QHBoxLayout, QWidget

from ..app_icon import get_app_icon
from ..angle_utils import shortest_delta_deg, wrap_deg
from ..i18n import t


class WindRoseWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._wind_dir_deg: Optional[float] = 0.0
        self._wind_dir_draw_deg: Optional[float] = 0.0
        self._wind_dir_mode: str = "to"
        self._arrow_pixmap = self._load_arrow_pixmap()
        self._wind_anim_speed_dps: float = 280.0
        self._wind_anim_last_ts: float = time.monotonic()
        self._wind_anim_timer = QTimer(self)
        self._wind_anim_timer.setInterval(16)
        self._wind_anim_timer.timeout.connect(self._animate_wind_dir)
        self.setMinimumSize(140, 140)
        self.setMaximumSize(140, 140)

    @staticmethod
    def _load_arrow_pixmap() -> QPixmap:
        def _candidate_names(base: Path) -> list[Path]:
            return [
                base / "rotortcpbridge" / "windPfeil.png",
                base / "rotortcpbridge" / "WindPfeil.png",
                base / "rotortcpbridge" / "windpfeil.png",
                base / "windPfeil.png",
                base / "WindPfeil.png",
                base / "windpfeil.png",
                base / "windPfeil.PNG",
                base / "WindPfeil.PNG",
                base / "windpfeil.PNG",
            ]

        roots: list[Path] = []
        try:
            roots.append(Path(__file__).resolve().parent)  # ui/
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
        try:
            if str(sys.argv[0] or "").strip():
                roots.append(Path(sys.argv[0]).resolve().parent)
        except Exception:
            pass
        try:
            roots.append(Path(sys.executable).resolve().parent)
        except Exception:
            pass

        unique_roots: list[Path] = []
        seen: set[str] = set()
        for r in roots:
            k = os.path.normcase(str(r))
            if k not in seen:
                seen.add(k)
                unique_roots.append(r)

        for root in unique_roots:
            for p in _candidate_names(root):
                if p.exists():
                    pm = QPixmap(str(p))
                    if not pm.isNull():
                        return pm

        for root in unique_roots:
            try:
                for p in root.rglob("*"):
                    if ("wind" in p.name.lower()) and ("pfeil" in p.name.lower()) and p.name.lower().endswith(".png"):
                        pm = QPixmap(str(p))
                        if not pm.isNull():
                            return pm
            except Exception:
                continue
        return QPixmap()

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

    def set_wind_dir_mode(self, mode: str) -> None:
        m = str(mode or "").strip().lower()
        self._wind_dir_mode = m if m in ("from", "to") else "to"
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
            step = max(-max_step, min(max_step, delta * 0.30))
            self._wind_dir_draw_deg = wrap_deg(float(self._wind_dir_draw_deg) + step)
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        rect = self.rect().adjusted(8, 8, -8, -8)
        cx, cy = float(rect.center().x()), float(rect.center().y())
        r = float(min(rect.width(), rect.height())) / 2.0
        p.setPen(QPen(self.palette().color(QPalette.ColorRole.WindowText), 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(rect)
        p.setPen(QPen(self.palette().color(QPalette.ColorRole.WindowText), 1))
        for a in range(0, 360, 10):
            rad = math.radians(a)
            x1 = cx + math.sin(rad) * (r * (0.84 if a % 30 == 0 else 0.89))
            y1 = cy - math.cos(rad) * (r * (0.84 if a % 30 == 0 else 0.89))
            x2 = cx + math.sin(rad) * (r * 0.97)
            y2 = cy - math.cos(rad) * (r * 0.97)
            p.drawLine(int(x1), int(y1), int(x2), int(y2))
        f_main = p.font()
        f_main.setBold(True)
        f_main.setPointSize(max(8, int(r * 0.12)))
        f_diag = p.font()
        f_diag.setBold(True)
        f_diag.setPointSize(max(7, f_main.pointSize() - 1))

        def _draw_cardinal(text: str, deg: float, diagonal: bool = False) -> None:
            p.setFont(f_diag if diagonal else f_main)
            fm = QFontMetrics(f_diag if diagonal else f_main)
            rad = math.radians(deg)
            label_r = r * (0.60 if diagonal else 0.68)
            tx = cx + math.sin(rad) * label_r
            ty = cy - math.cos(rad) * label_r
            w, h = fm.horizontalAdvance(text), fm.height()
            p.drawText(int(tx - w / 2.0), int(ty + h / 4.0), text)

        for txt, deg, diag in [("N", 0, False), ("NO", 45, True), ("O", 90, False), ("SO", 135, True),
                               ("S", 180, False), ("SW", 225, True), ("W", 270, False), ("NW", 315, True)]:
            _draw_cardinal(txt, deg, diag)

        if self._wind_dir_draw_deg is not None:
            wd = wrap_deg(float(self._wind_dir_draw_deg) + 180.0) if self._wind_dir_mode == "to" else float(self._wind_dir_draw_deg)
            if not self._arrow_pixmap.isNull():
                self._draw_arrow_image(p, cx, cy, r * 0.72, wd)
            else:
                self._draw_arrow(p, cx, cy, r * 0.72, wd)

    @staticmethod
    def _draw_arrow(p: QPainter, cx: float, cy: float, length: float, deg: float) -> None:
        p.setPen(QPen(QColor(0, 90, 220), 3))
        rad = math.radians(deg)
        x2 = cx + math.sin(rad) * length
        y2 = cy - math.cos(rad) * length
        p.drawLine(int(cx), int(cy), int(x2), int(y2))
        head = max(8.0, length * 0.10)
        xl = x2 + math.sin(math.radians(deg + 150)) * head
        yl = y2 - math.cos(math.radians(deg + 150)) * head
        xr = x2 + math.sin(math.radians(deg - 150)) * head
        yr = y2 - math.cos(math.radians(deg - 150)) * head
        p.drawLine(int(x2), int(y2), int(xl), int(yl))
        p.drawLine(int(x2), int(y2), int(xr), int(yr))

    def _draw_arrow_image(self, p: QPainter, cx: float, cy: float, length: float, deg: float) -> None:
        if self._arrow_pixmap.isNull():
            return
        side = int(max(16.0, min(length * 1.35, 88.0) - 20.0))
        scaled = self._arrow_pixmap.scaled(side, side, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        w, h = float(scaled.width()), float(scaled.height())
        p.save()
        p.translate(cx, cy)
        p.rotate(float(deg))
        p.drawPixmap(int(-w / 2.0), int(-h / 2.0), scaled)
        p.restore()


class WeatherWindow(QDialog):
    def __init__(self, cfg: dict, controller, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.ctrl = controller
        self.setWindowTitle(t("weather.title"))
        self.setWindowFlags(
            Qt.WindowType.Window | Qt.WindowType.CustomizeWindowHint
            | Qt.WindowType.WindowTitleHint | Qt.WindowType.WindowCloseButtonHint
        )
        self.setWindowIcon(get_app_icon())
        self.setFixedSize(200, 290)

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        self.wind_rose = WindRoseWidget(self)
        rose_row = QHBoxLayout()
        rose_row.addStretch(1)
        rose_row.addWidget(self.wind_rose)
        rose_row.addStretch(1)
        root.addLayout(rose_row)

        form = QFormLayout()
        form.setFormAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        root.addLayout(form)

        self.ed_wind_dir = QLabel("--.-°")
        self.ed_wind_speed = QLabel("--.- km/h")
        self.ed_beaufort = QLabel("--")
        self.ed_temp_a = QLabel("--.- °C")
        self.ed_temp_m = QLabel("--.- °C")
        for v in (self.ed_wind_dir, self.ed_wind_speed, self.ed_beaufort, self.ed_temp_a, self.ed_temp_m):
            v.setStyleSheet("font-weight: 700;")
        form.addRow(t("weather.wind_dir_label"), self.ed_wind_dir)
        form.addRow(t("weather.wind_speed_label"), self.ed_wind_speed)
        form.addRow(t("weather.beaufort_label"), self.ed_beaufort)
        form.addRow(t("weather.temp_ambient_label"), self.ed_temp_a)
        form.addRow(t("weather.temp_motor_label"), self.ed_temp_m)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(250)
        self._tick()

    def _tick(self) -> None:
        tel = getattr(self.ctrl.az, "telemetry", None)
        wind_known = bool(getattr(self.ctrl, "wind_enabled_known", False))
        wind_on = bool(getattr(self.ctrl, "wind_enabled", False)) if wind_known else False
        if not wind_on and not wind_known and hasattr(self.ctrl, "az"):
            tel = getattr(self.ctrl.az, "telemetry", None)
            if tel is not None and (
                getattr(tel, "wind_kmh", None) is not None or getattr(tel, "wind_dir_deg", None) is not None
            ):
                wind_on = True
        mode = str(self.cfg.get("ui", {}).get("wind_dir_display", "to") or "to").strip().lower()
        if mode not in ("from", "to"):
            mode = "to"
        self.wind_rose.set_wind_dir_mode(mode)

        if tel is None:
            self.wind_rose.set_wind_dir_deg(0.0)
            self.ed_wind_dir.setText("--.-°")
            self.ed_wind_speed.setText("--.- km/h")
            self.ed_beaufort.setText("--")
            self.ed_temp_a.setText("--.- °C")
            self.ed_temp_m.setText("--.- °C")
            return

        wdir = getattr(tel, "wind_dir_deg", None) if wind_on else None
        wkmh = getattr(tel, "wind_kmh", None) if wind_on else None
        bft = getattr(tel, "wind_beaufort", None) if wind_on else None
        ta, tm = getattr(tel, "temp_ambient_c", None), getattr(tel, "temp_motor_c", None)

        self.wind_rose.set_wind_dir_deg(wdir)
        self.ed_wind_dir.setText(f"{float(wdir):.1f}°" if wdir is not None else "--.-°")
        self.ed_wind_speed.setText(f"{float(wkmh):.1f} km/h" if wkmh is not None else "--.- km/h")
        self.ed_beaufort.setText(f"{int(bft)}" if bft is not None else "--")
        self.ed_temp_a.setText(f"{float(ta):.1f} °C" if ta is not None else "--.- °C")
        self.ed_temp_m.setText(f"{float(tm):.1f} °C" if tm is not None else "--.- °C")
