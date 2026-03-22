"""Karten-Fenster mit Leaflet und Antennen-Beam."""

from __future__ import annotations

import json
import math
import time
from typing import Optional

from PySide6.QtCore import QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtWebEngineWidgets import QWebEngineView

from ..angle_utils import clamp_el, fmt_deg, shortest_delta_deg, wrap_deg
from ..ui.led_widget import Led
from ..ui.ui_utils import px_to_dip
from ..app_icon import get_app_icon
from ..geo_utils import beam_center_line_points, beam_polygon_points, bearing_deg, grayline_points
from ..i18n import t
from .elevation_window import ElevationProfileWindow
from .map_html import build_map_html
from .map_tiles import (
    ROTORTILES_SCHEME,
    _DEBUG_TILES,
    _offline_tile_url,
    set_pending_map_html,
)
from .map_widgets import MapWindOverlay, MapWebPage, _MapContainer

# Strich / Füllung für die drei Antennen-Beams auf der Karte (Antenne 1–3)
_MAP_ANTENNA_BEAM_COLORS: tuple[tuple[str, str], ...] = (
    ("#5BA3D0", "#87CEEB"),  # 1: bisheriges Blau
    ("#66BB6A", "#C8E6C9"),  # 2: helles Grün
    ("#ae80d9", "#d8c4f0"),  # 3: Violett (Stroke #ae80d9)
)


def _map_antenna_swatch_icon(antenna_index: int, size_px: int = 14) -> QIcon:
    """Kleines Quadrat in Beam-Farbe (Füllung + Rand) für das Antennen-Dropdown.
    Farben wie auf der Karte, nur bewusst abgedunkelt (gleicher Farbton)."""
    i = max(0, min(2, antenna_index))
    stroke, fill = _MAP_ANTENNA_BEAM_COLORS[i]
    # Qt: factor > 100 → dunkler (typ. 150–180 für sichtbar kräftigere Swatches)
    fill_d = QColor(fill).darker(155)
    stroke_d = QColor(stroke).darker(150)
    pm = QPixmap(size_px, size_px)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(fill_d)
    p.setPen(QPen(stroke_d, 1))
    p.drawRect(1, 1, size_px - 3, size_px - 3)
    p.end()
    return QIcon(pm)


class MapWindow(QDialog):
    """Fenster mit Leaflet-Karte, Antennen-Beam und Klick-zu-Rotor."""

    def __init__(self, cfg: dict, controller, save_cfg_cb=None, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.ctrl = controller
        self.save_cfg_cb = save_cfg_cb
        self.setWindowTitle(t("map.title"))
        self.setWindowFlag(Qt.WindowType.WindowMinimizeButtonHint, True)
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        self.setWindowIcon(get_app_icon())
        self.resize(900, 700)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)

        # Toolbar: Antenne, Favoriten, Name, Speichern, Löschen (wie Kompassfenster)
        toolbar = QHBoxLayout()
        antenna_idx = max(0, min(2, int(self.cfg.get("ui", {}).get("compass_antenna", 0))))
        self._cb_antenna = QComboBox()
        sw = max(12, px_to_dip(self, 14))
        self._cb_antenna.setIconSize(QSize(sw, sw))
        self._populate_antenna_dropdown()
        self._cb_antenna.setMinimumWidth(160)
        self._cb_antenna.setCurrentIndex(antenna_idx)
        self._cb_antenna.currentIndexChanged.connect(self._on_antenna_changed)

        self._cb_fav = QComboBox()
        self._cb_fav.setMinimumWidth(180)
        self._cb_fav.setEditable(False)
        self._ed_fav_name = QLineEdit()
        self._ed_fav_name.setPlaceholderText(t("compass.fav_name_placeholder"))
        self._ed_fav_name.setMaxLength(15)
        self._ed_fav_name.setFixedWidth(110)
        self._btn_fav_save = QPushButton(t("compass.fav_btn_save"))
        self._btn_fav_save.setAutoDefault(False)
        self._btn_fav_save.setDefault(False)
        self._btn_fav_delete = QPushButton(t("compass.fav_btn_delete"))
        self._btn_fav_delete.setAutoDefault(False)
        self._btn_fav_delete.setDefault(False)
        self._btn_elevation = QPushButton(t("map.btn_elevation"))
        self._btn_elevation.setAutoDefault(False)
        self._btn_elevation.setDefault(False)

        toolbar.addWidget(self._cb_antenna)
        toolbar.addWidget(self._cb_fav)
        toolbar.addWidget(self._ed_fav_name)
        toolbar.addWidget(self._btn_fav_save)
        toolbar.addWidget(self._btn_fav_delete)
        toolbar.addWidget(self._btn_elevation)
        self._cb_antenna.setToolTip(t("map.tooltip_antenna"))
        self._cb_fav.setToolTip(t("map.tooltip_favorites"))
        self._ed_fav_name.setToolTip(t("map.tooltip_fav_name"))
        self._btn_fav_save.setToolTip(t("map.tooltip_fav_save"))
        self._btn_fav_delete.setToolTip(t("map.tooltip_fav_delete"))
        self._btn_elevation.setToolTip(t("map.tooltip_elevation"))
        layout.addLayout(toolbar)

        self._cb_fav.activated.connect(self._on_fav_activated)
        self._btn_fav_save.clicked.connect(self._on_fav_save)
        self._btn_fav_delete.clicked.connect(self._on_fav_delete)
        self._btn_elevation.clicked.connect(self._on_elevation_profile)
        self._refresh_favorites_dropdown()

        map_container = _MapContainer(self)
        map_layout = QVBoxLayout(map_container)
        map_layout.setContentsMargins(0, 0, 0, 0)
        self._view = QWebEngineView(map_container)
        self._page = MapWebPage(
            self._on_map_click, on_tile_error_cb=self._on_tile_error, parent=self._view
        )
        self._view.setPage(self._page)
        self._view.loadFinished.connect(self._on_map_load_finished)
        map_layout.addWidget(self._view, 1)
        self._wind_overlay = MapWindOverlay(map_container)
        map_container._wind_overlay = self._wind_overlay
        layout.addWidget(map_container, 1)

        # Statusleiste unter der Karte: Ist, Soll, LEDs (Fährt, Online, Ref), Ref AZ
        status_bar = QHBoxLayout()
        status_bar.setContentsMargins(0, 6, 0, 0)
        led_d = px_to_dip(self, 13)
        lbl_style = "font-size: 12pt; font-weight: bold;"
        self._lbl_ist = QLabel(t("compass.ist_prefix") + "–")
        self._lbl_ist.setMinimumWidth(95)
        self._lbl_ist.setStyleSheet(lbl_style)
        self._lbl_soll = QLabel(t("compass.soll_label"))
        self._lbl_soll.setStyleSheet(lbl_style)
        self._lbl_soll_value = QLabel("–")
        self._lbl_soll_value.setStyleSheet(lbl_style)
        self._lbl_soll_value.setMinimumWidth(55)
        self._led_moving = Led(led_d, self)
        self._lbl_moving = QLabel(t("axis.moving_label"))
        self._lbl_moving.setStyleSheet(lbl_style)
        self._led_online = Led(led_d, self)
        self._lbl_online = QLabel(t("axis.online_label"))
        self._lbl_online.setStyleSheet(lbl_style)
        self._led_ref = Led(led_d, self)
        self._lbl_ref = QLabel(t("compass.ref_led_label_az"))
        self._lbl_ref.setStyleSheet(lbl_style)
        self._lbl_temp_motor = QLabel("–")
        self._lbl_temp_motor.setStyleSheet(lbl_style)
        self._lbl_temp_ambient = QLabel("–")
        self._lbl_temp_ambient.setStyleSheet(lbl_style)
        self._chk_offline = QCheckBox(t("map.chk_offline"))
        self._chk_offline.setChecked(bool(self.cfg.get("ui", {}).get("map_offline", False)))
        self._chk_offline.stateChanged.connect(self._on_offline_changed)
        self._internet_online: Optional[bool] = None
        self._chk_locator = QCheckBox(t("map.chk_locator"))
        self._chk_locator.setChecked(bool(self.cfg.get("ui", {}).get("map_locator_overlay", False)))
        self._chk_locator.stateChanged.connect(self._on_locator_changed)
        self._btn_ref_az = QPushButton(t("compass.btn_ref_az"))
        self._btn_ref_az.setAutoDefault(False)
        self._btn_ref_az.setDefault(False)
        self._btn_ref_az.clicked.connect(self._on_ref_az)
        self._chk_offline.setToolTip(t("map.tooltip_offline"))
        self._chk_locator.setToolTip(t("map.tooltip_locator"))
        self._btn_ref_az.setToolTip(t("map.tooltip_ref_az"))

        status_bar.addWidget(self._lbl_ist)
        status_bar.addWidget(self._lbl_soll)
        status_bar.addWidget(self._lbl_soll_value)
        status_bar.addStretch(1)
        status_bar.addWidget(self._led_moving)
        status_bar.addWidget(self._lbl_moving)
        status_bar.addWidget(self._led_online)
        status_bar.addWidget(self._lbl_online)
        status_bar.addWidget(self._led_ref)
        status_bar.addWidget(self._lbl_ref)
        status_bar.addSpacing(px_to_dip(self, 20))
        status_bar.addWidget(self._lbl_temp_motor)
        status_bar.addWidget(self._lbl_temp_ambient)
        status_bar.addStretch(1)
        status_bar.addWidget(self._chk_offline)
        status_bar.addWidget(self._chk_locator)
        status_bar.addWidget(self._btn_ref_az)
        layout.addLayout(status_bar)

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(50)
        self._refresh_timer.timeout.connect(self._refresh_map)
        self._map_loaded = False
        self._map_dark_mode: Optional[bool] = None
        self._map_offline: Optional[bool] = None
        self._map_locator_overlay: Optional[bool] = None
        self._smooth_rotor_az: Optional[float] = None
        self._SMOOTH_FACTOR = 0.25
        # Zuletzt per Kartenklick gewähltes Ziel (aus cfg wiederherstellen)
        _saved_ui = self.cfg.get("ui", {})
        self._target_lat: Optional[float] = (
            float(_saved_ui["map_target_lat"]) if "map_target_lat" in _saved_ui else None
        )
        self._target_lon: Optional[float] = (
            float(_saved_ui["map_target_lon"]) if "map_target_lon" in _saved_ui else None
        )
        # Rotor-Azimut, der durch den letzten Kartenklick gesetzt wurde
        # (zum Erkennen von Fremd-Bewegungen)
        self._map_click_rotor_az: Optional[float] = None
        # Referenz auf offenes Höhenprofil-Fenster (None = nicht offen)
        self._elevation_win: Optional[ElevationProfileWindow] = None
        # ASWATCHLIST (AirScout/KST): andere Stationen auf der Karte
        self._aswatch_last: list = []
        # True erst nach loadFinished – sonst existiert setAswatchMarkers in JS noch nicht
        self._map_page_ready = False

    def update_aswatch_users(self, items: list) -> None:
        """Zeigt andere User-Marker (User.png + Rufzeichen) aus UDP ASWATCHLIST/ASSETPATH."""
        try:
            self._aswatch_last = list(items) if items else []
        except Exception:
            self._aswatch_last = []
        if not self._view or not getattr(self, "_map_page_ready", False):
            return
        try:
            js = (
                f"if (typeof window.setAswatchMarkers === 'function') "
                f"window.setAswatchMarkers({json.dumps(self._aswatch_last)});"
            )
            self._view.page().runJavaScript(js)
        except Exception:
            pass

    def _get_params(self) -> dict:
        """Aktuelle Parameter für die Karte."""
        ui = self.cfg.get("ui", {})
        lat = float(ui.get("location_lat", 49.502651))
        lon = float(ui.get("location_lon", 8.375019))
        antenna_idx = max(0, min(2, int(ui.get("compass_antenna", 0))))
        slot = antenna_idx + 1
        offs = ui.get("antenna_offsets_az", [0.0, 0.0, 0.0])
        angles = ui.get("antenna_angles_az", [0.0, 0.0, 0.0])
        offset = float(offs[slot - 1]) if slot <= len(offs) else 0.0
        opening = float(angles[slot - 1]) if slot <= len(angles) else 0.0
        if opening <= 0:
            opening = 30.0
        ranges = ui.get("antenna_ranges_az", [100.0, 100.0, 100.0])
        range_km = float(ranges[slot - 1]) if slot <= len(ranges) else 100.0
        if range_km <= 0:
            range_km = 100.0
        range_km = min(4000.0, range_km)

        az_axis = getattr(self.ctrl, "az", None)
        az_pos = getattr(az_axis, "pos_d10", None) if az_axis else None
        if az_pos is not None:
            rotor_az = az_pos / 10.0
            azimuth = wrap_deg(rotor_az + offset)
        else:
            rotor_az = 0.0
            azimuth = 0.0

        grayline = grayline_points()

        # Horizontdistanz: d_h = sqrt(2*R*h), R=6371 km, h in m
        antenna_height_m = float(ui.get("antenna_height_m", 0.0))
        R_km = 6371.0
        horizon_dist_km = (
            math.sqrt(2.0 * R_km * (antenna_height_m / 1000.0)) if antenna_height_m > 0 else 0.0
        )

        return {
            "lat": lat,
            "lon": lon,
            "azimuth": azimuth,
            "rotor_az_deg": float(rotor_az),
            "opening": opening,
            "range_km": range_km,
            "grayline": [[p[0], p[1]] for p in grayline],
            "location_str": f"{lat:.5f}, {lon:.5f}",
            "info_standort": t("map.info_standort"),
            "info_offnung": t("map.info_offnung"),
            "info_reichweite": t("map.info_reichweite"),
            "dark_mode": bool(self.cfg.get("ui", {}).get("force_dark_mode", True)),
            "offline": bool(self.cfg.get("ui", {}).get("map_offline", False)),
            "map_locator_overlay": bool(self.cfg.get("ui", {}).get("map_locator_overlay", False)),
            "horizon_dist_km": horizon_dist_km,
            "popup_antenna": t("map.popup_antenna"),
            "popup_target": t("map.popup_target"),
        }

    def _compute_beams(self, rotor_az_deg: float, antenna_idx: int) -> list[dict]:
        """Nur der Beam der im Dropdown gewählten Antenne; Farbe je nach Slot 1–3; Richtung = Rotor + Offset."""
        i = max(0, min(2, antenna_idx))
        ui = self.cfg.get("ui", {})
        lat = float(ui.get("location_lat", 49.502651))
        lon = float(ui.get("location_lon", 8.375019))
        offs = ui.get("antenna_offsets_az", [0.0, 0.0, 0.0])
        angles = ui.get("antenna_angles_az", [0.0, 0.0, 0.0])
        ranges = ui.get("antenna_ranges_az", [100.0, 100.0, 100.0])
        offset = float(offs[i]) if i < len(offs) else 0.0
        opening = float(angles[i]) if i < len(angles) else 30.0
        if opening <= 0:
            opening = 30.0
        range_km = float(ranges[i]) if i < len(ranges) else 100.0
        if range_km <= 0:
            range_km = 100.0
        range_km = min(4000.0, range_km)
        azimuth = wrap_deg(rotor_az_deg + offset)
        polygon = beam_polygon_points(lat, lon, azimuth, opening, range_km)
        center_line = beam_center_line_points(lat, lon, azimuth, range_km)
        stroke, fill = _MAP_ANTENNA_BEAM_COLORS[i]
        return [
            {
                "polygon": [[p[0], p[1]] for p in polygon],
                "centerLine": [[p[0], p[1]] for p in center_line],
                "stroke": stroke,
                "fill": fill,
            }
        ]

    def _get_antenna_offset_az(self) -> float:
        """Versatz der gewählten Antenne (wie Compass)."""
        ui = self.cfg.get("ui", {})
        antenna_idx = max(0, min(2, int(ui.get("compass_antenna", 0))))
        slot = antenna_idx + 1
        az_axis = getattr(self.ctrl, "az", None)
        v = getattr(az_axis, f"antoff{slot}", None) if az_axis else None
        if v is not None:
            return float(v)
        offs = ui.get("antenna_offsets_az", [0.0, 0.0, 0.0])
        try:
            return float(offs[slot - 1])
        except (IndexError, TypeError, ValueError):
            return 0.0

    def _get_antenna_dropdown_items(self) -> list[str]:
        """Antennen-Namen mit Versatz in Klammern (wie Kompass)."""
        names = list(
            self.cfg.get("ui", {}).get("antenna_names", ["Antenne 1", "Antenne 2", "Antenne 3"])
        )
        while len(names) < 3:
            names.append(f"Antenne {len(names) + 1}")
        az_axis = getattr(self.ctrl, "az", None)
        offsets: list[float] = []
        for slot in (1, 2, 3):
            v = getattr(az_axis, f"antoff{slot}", None) if az_axis else None
            if v is not None:
                offsets.append(float(v))
            else:
                offs = self.cfg.get("ui", {}).get("antenna_offsets_az", [0.0, 0.0, 0.0])
                try:
                    offsets.append(float(offs[slot - 1]))
                except (IndexError, TypeError, ValueError):
                    offsets.append(0.0)
        return [f"{names[i]} ({offsets[i]:.1f}°)" for i in range(3)]

    def _populate_antenna_dropdown(self) -> None:
        """Dropdown mit farbigem Quadrat (Beam-Farbe) vor dem Text."""
        self._cb_antenna.clear()
        labels = self._get_antenna_dropdown_items()
        sw = self._cb_antenna.iconSize().width()
        if sw <= 0:
            sw = 14
        for i in range(3):
            self._cb_antenna.addItem(_map_antenna_swatch_icon(i, sw), labels[i])

    def _get_favorites(self) -> list[dict]:
        """Gespeicherte Favoriten aus Config (wie Kompass)."""
        items = self.cfg.get("ui", {}).get("compass_favorites", [])
        if not isinstance(items, list):
            return []
        out: list[dict] = []
        for it in items:
            if isinstance(it, dict) and "name" in it:
                try:
                    out.append(
                        {
                            "name": str(it["name"])[:15],
                            "az": float(it.get("az", 0.0)),
                            "el": clamp_el(float(it.get("el", 0.0))),
                        }
                    )
                except (TypeError, ValueError):
                    pass
        return out

    def _refresh_favorites_dropdown(self) -> None:
        """Favoriten-Dropdown füllen (wie Kompass)."""
        favs = self._get_favorites()
        favs = sorted(
            favs,
            key=lambda f: (
                0 if f["name"] and f["name"][0].isdigit() else 1,
                f["name"].lower(),
            ),
        )
        self._cb_fav.blockSignals(True)
        self._cb_fav.clear()
        if not favs:
            self._cb_fav.addItem(t("compass.fav_dropdown_placeholder"), None)
        else:
            for f in favs:
                self._cb_fav.addItem(f"{f['name']} ({f['az']:.1f}°, {f['el']:.1f}°)", f)
        self._cb_fav.blockSignals(False)

    def _refresh_antenna_dropdown(self) -> None:
        """Antennen-Dropdown mit aktuellen Werten aktualisieren."""
        idx = max(0, min(2, self._cb_antenna.currentIndex()))
        self._cb_antenna.blockSignals(True)
        self._populate_antenna_dropdown()
        self._cb_antenna.setCurrentIndex(idx)
        self._cb_antenna.blockSignals(False)

    def _on_antenna_changed(self) -> None:
        """Antenne gewechselt → Config speichern, Karte aktualisiert sich über cfg."""
        idx = max(0, min(2, self._cb_antenna.currentIndex()))
        if "ui" not in self.cfg:
            self.cfg["ui"] = {}
        self.cfg["ui"]["compass_antenna"] = idx
        try:
            if self.save_cfg_cb:
                self.save_cfg_cb(self.cfg)
        except Exception:
            pass

    def _on_fav_activated(self, idx: int) -> None:
        """Favorit ausgewählt → dorthin fahren."""
        if idx < 0:
            return
        data = self._cb_fav.itemData(idx)
        if not isinstance(data, dict) or "az" not in data or "el" not in data:
            return
        rotor_az = wrap_deg(float(data["az"]))
        rotor_el = clamp_el(float(data["el"]))
        try:
            self.ctrl.set_az_deg(rotor_az, force=True)
            if hasattr(self.ctrl, "set_el_deg"):
                self.ctrl.set_el_deg(rotor_el, force=True)
        except Exception:
            pass
        self._refresh_map()

    def _on_fav_save(self) -> None:
        """Aktuelle Position unter dem eingegebenen Namen speichern."""
        name = str(self._ed_fav_name.text()).strip()
        if not name:
            return
        name = name[:15]
        try:
            az_d10 = getattr(self.ctrl.az, "pos_d10", None)
        except Exception:
            return
        if az_d10 is None:
            return
        az_deg = float(az_d10) / 10.0
        try:
            el_d10 = getattr(self.ctrl.el, "pos_d10", None) if hasattr(self.ctrl, "el") else None
        except Exception:
            el_d10 = None
        el_deg = clamp_el(float(el_d10 or 0) / 10.0)
        fav = {"name": name[:15], "az": wrap_deg(az_deg), "el": el_deg}
        if "ui" not in self.cfg:
            self.cfg["ui"] = {}
        favs = self._get_favorites()
        favs.append(fav)
        self.cfg["ui"]["compass_favorites"] = favs
        try:
            if self.save_cfg_cb:
                self.save_cfg_cb(self.cfg)
        except Exception:
            pass
        self._refresh_favorites_dropdown()
        self._ed_fav_name.clear()

    def _on_fav_delete(self) -> None:
        """Ausgewählten Favoriten löschen."""
        idx = self._cb_fav.currentIndex()
        if idx < 0:
            return
        data = self._cb_fav.itemData(idx)
        if data is None:
            return
        sel_name = data.get("name")
        sel_az = data.get("az")
        sel_el = data.get("el")
        favs = self._get_favorites()
        favs = [
            f
            for f in favs
            if not (
                f.get("name") == sel_name
                and abs(float(f.get("az", 0) or 0) - float(sel_az or 0)) < 0.01
                and abs(float(f.get("el", 0) or 0) - float(sel_el or 0)) < 0.01
            )
        ]
        if "ui" not in self.cfg:
            self.cfg["ui"] = {}
        self.cfg["ui"]["compass_favorites"] = favs
        try:
            if self.save_cfg_cb:
                self.save_cfg_cb(self.cfg)
        except Exception:
            pass
        self._refresh_favorites_dropdown()

    def _on_offline_changed(self) -> None:
        """Offline-Karte ein/aus → Config speichern, Karte aktualisiert sich über cfg."""
        on = bool(self._chk_offline.isChecked())
        if "ui" not in self.cfg:
            self.cfg["ui"] = {}
        self.cfg["ui"]["map_offline"] = on
        try:
            if self.save_cfg_cb:
                self.save_cfg_cb(self.cfg)
        except Exception:
            pass
        # Interne State-Sync: manueller Wechsel auf Online → Schutzperiode starten
        if not on:
            self._internet_online = True
            self._online_switch_ts = time.monotonic()
            self._last_tile_error_ts = 0.0  # Cooldown zurücksetzen
        else:
            self._internet_online = False
        self._refresh_map()

    def _on_tile_error(self) -> None:
        """Wird von MapWebPage aufgerufen wenn Leaflet einen Tile-Fehler meldet.
        Schutzperiode: Direkt nach Online-Wechsel Tile-Fehler ignorieren (DNS-Anlaufzeit).
        Timestamp nur setzen wenn noch online, damit Timer später auf Online schalten kann."""
        if self._internet_online is False:
            return  # Bereits offline – nichts zu tun
        # Schutzperiode nach Online-Wechsel: erste Tile-Fehler ignorieren
        online_since = getattr(self, "_online_switch_ts", 0.0)
        if time.monotonic() - online_since < 8.0:
            return  # Frisch online geschaltet – kurze Fehler ignorieren
        self._last_tile_error_ts = time.monotonic()
        self._apply_internet_status(False)

    def apply_internet_status(self, online: bool) -> None:
        """Öffentlich: Kartenmodus je nach Internetstatus (von MainWindow aufgerufen)."""
        if online:
            last_err = getattr(self, "_last_tile_error_ts", 0.0)
            if time.monotonic() - last_err < 6.0:
                return
        self._apply_internet_status(online)

    def _apply_internet_status(self, online: bool) -> None:
        """Kartenmodus und Checkbox je nach Internetstatus automatisch umschalten."""
        if self._internet_online == online:
            return
        self._internet_online = online
        if online:
            self._online_switch_ts = time.monotonic()  # Schutzperiode starten
        self._chk_offline.blockSignals(True)
        self._chk_offline.setChecked(not online)
        # Checkbox NICHT deaktivieren – Nutzer hat immer manuelle Kontrolle
        self._chk_offline.blockSignals(False)
        if "ui" not in self.cfg:
            self.cfg["ui"] = {}
        self.cfg["ui"]["map_offline"] = not online
        try:
            if self.save_cfg_cb:
                self.save_cfg_cb(self.cfg)
        except Exception:
            pass
        self._refresh_map()
        if self._elevation_win is not None:
            self._elevation_win.apply_internet_status(online)

    def _on_locator_changed(self) -> None:
        """Locator-Overlay ein/aus → Config speichern, Karte aktualisieren."""
        on = bool(self._chk_locator.isChecked())
        if "ui" not in self.cfg:
            self.cfg["ui"] = {}
        self.cfg["ui"]["map_locator_overlay"] = on
        try:
            if self.save_cfg_cb:
                self.save_cfg_cb(self.cfg)
        except Exception:
            pass
        self._refresh_map()

    def _on_ref_az(self) -> None:
        """Referenz AZ auslösen."""
        try:
            self.ctrl.reference_az(True)
        except Exception:
            pass

    def _update_wind_overlay(self) -> None:
        """Wind-Overlay aus Telemetrie aktualisieren."""
        dark = bool(self.cfg.get("ui", {}).get("force_dark_mode", True))
        self._wind_overlay.set_dark_mode(dark)
        wind_on = (
            bool(getattr(self.ctrl, "wind_enabled", False))
            if getattr(self.ctrl, "wind_enabled_known", False)
            else False
        )
        if not wind_on and hasattr(self.ctrl, "az"):
            tel = getattr(self.ctrl.az, "telemetry", None)
            if tel and (
                getattr(tel, "wind_kmh", None) is not None
                or getattr(tel, "wind_dir_deg", None) is not None
            ):
                wind_on = True
        mode = str(self.cfg.get("ui", {}).get("wind_dir_display", "to") or "to").strip().lower()
        if mode not in ("from", "to"):
            mode = "to"
        self._wind_overlay.set_wind_dir_mode(mode)
        self._wind_overlay.set_visible(wind_on)
        if not wind_on:
            self._wind_overlay.set_wind_dir_deg(0.0)
            self._wind_overlay.set_wind_kmh(None)
            return
        try:
            tel = getattr(self.ctrl.az, "telemetry", None)
            wdir = getattr(tel, "wind_dir_deg", None) if tel else None
            wkmh = getattr(tel, "wind_kmh", None) if tel else None
            self._wind_overlay.set_wind_dir_deg(wdir)
            self._wind_overlay.set_wind_kmh(wkmh)
        except Exception:
            self._wind_overlay.set_wind_dir_deg(0.0)
            self._wind_overlay.set_wind_kmh(None)

    def _update_status_bar(self) -> None:
        """IST, SOLL und LEDs aus Controller aktualisieren."""
        off = self._get_antenna_offset_az()
        az_axis = getattr(self.ctrl, "az", None)
        if az_axis is None:
            self._lbl_ist.setText(t("compass.ist_prefix") + "–")
            self._lbl_soll_value.setText("–")
            self._led_moving.set_state(False)
            self._led_online.set_state(False)
            self._led_ref.set_state(False)
            self._lbl_temp_motor.setText(f"{t('weather.temp_motor_label')}: –")
            self._lbl_temp_ambient.setText(f"{t('weather.temp_ambient_label')}: –")
            return
        try:
            pos_d10 = getattr(az_axis, "pos_d10", None)
            cur = wrap_deg(float(pos_d10) / 10.0 + off) if pos_d10 is not None else None
        except Exception:
            cur = None
        try:
            tgt_d10 = getattr(az_axis, "target_d10", None)
            tgt = wrap_deg(float(tgt_d10) / 10.0 + off) if tgt_d10 is not None else None
        except Exception:
            tgt = None
        # Bei unbekanntem Ziel (z.B. erste Öffnung): Soll = Ist
        unknown_target = (tgt_d10 is None) or (
            int(tgt_d10 or 0) == 0
            and float(getattr(az_axis, "last_set_sent_ts", 0.0) or 0.0) <= 0.0
            and getattr(az_axis, "last_set_sent_target_d10", None) is None
        )
        if cur is not None and unknown_target:
            tgt = cur
        self._lbl_ist.setText(t("compass.ist_prefix") + (fmt_deg(cur) if cur is not None else "–"))
        self._lbl_soll_value.setText(fmt_deg(tgt) if tgt is not None else "–")
        self._led_moving.set_state(bool(getattr(az_axis, "moving", False)))
        self._led_online.set_state(bool(getattr(az_axis, "online", False)))
        self._led_ref.set_state(bool(getattr(az_axis, "referenced", False)))
        try:
            tel = getattr(az_axis, "telemetry", None)
            ta = getattr(tel, "temp_ambient_c", None) if tel else None
            tm = getattr(tel, "temp_motor_c", None) if tel else None
            self._lbl_temp_motor.setText(
                f"{t('weather.temp_motor_label')}: {float(tm):.1f} °C"
                if tm is not None
                else f"{t('weather.temp_motor_label')}: –"
            )
            self._lbl_temp_ambient.setText(
                f"{t('weather.temp_ambient_label')}: {float(ta):.1f} °C"
                if ta is not None
                else f"{t('weather.temp_ambient_label')}: –"
            )
        except Exception:
            self._lbl_temp_motor.setText(f"{t('weather.temp_motor_label')}: –")
            self._lbl_temp_ambient.setText(f"{t('weather.temp_ambient_label')}: –")

    def _on_map_click(self, lat: float, lon: float) -> None:
        """Klick auf Karte: Rotor auf Peilung zu diesem Punkt drehen."""
        self._target_lat = lat
        self._target_lon = lon

        # Zielpunkt persistent speichern (bleibt nach Fensterschluss erhalten)
        self.cfg.setdefault("ui", {})["map_target_lat"] = lat
        self.cfg.setdefault("ui", {})["map_target_lon"] = lon
        if self.save_cfg_cb:
            self.save_cfg_cb(self.cfg)

        # Offenes Höhenprofil-Fenster sofort aktualisieren
        if self._elevation_win is not None and self._elevation_win.isVisible():
            ui = self.cfg.get("ui", {})
            home_lat = float(ui.get("location_lat", 49.502651))
            home_lon = float(ui.get("location_lon", 8.375019))
            self._elevation_win.update_target(home_lat, home_lon, lat, lon)

        ui = self.cfg.get("ui", {})
        lat0 = float(ui.get("location_lat", 49.502651))
        lon0 = float(ui.get("location_lon", 8.375019))
        bearing = bearing_deg(lat0, lon0, lat, lon)
        off = self._get_antenna_offset_az()
        rotor_deg = wrap_deg(bearing - off)
        self._map_click_rotor_az = rotor_deg  # Kartenklick-Azimut merken
        try:
            self.ctrl.set_az_deg(rotor_deg, force=True)
        except Exception:
            pass
        self._refresh_map()

    def _clear_map_target(self) -> None:
        """Zielmarker löschen: Zustand, cfg und Kartenanzeige bereinigen."""
        self._target_lat = None
        self._target_lon = None
        self._map_click_rotor_az = None
        ui = self.cfg.setdefault("ui", {})
        ui.pop("map_target_lat", None)
        ui.pop("map_target_lon", None)
        if self.save_cfg_cb:
            try:
                self.save_cfg_cb(self.cfg)
            except Exception:
                pass
        if self._map_loaded:
            self._view.page().runJavaScript(
                "if (typeof window.clearClickMarker === 'function') window.clearClickMarker();"
            )

    def _on_elevation_profile(self) -> None:
        """Höhenprofil-Fenster zwischen Heimstation und gewähltem Ziel öffnen."""
        # Bereits offenes Fenster in den Vordergrund holen
        if self._elevation_win is not None and self._elevation_win.isVisible():
            self._elevation_win.raise_()
            self._elevation_win.activateWindow()
            return

        ui = self.cfg.get("ui", {})
        home_lat = float(ui.get("location_lat", 49.502651))
        home_lon = float(ui.get("location_lon", 8.375019))
        dark = bool(ui.get("force_dark_mode", True))
        antenna_height = float(ui.get("antenna_height_m", 0.0))
        freq_mhz = float(ui.get("rf_freq_mhz", 145.0))

        # Kein Ziel gesetzt → Heimposition als Ziel (Entfernung = 0)
        target_lat = self._target_lat if self._target_lat is not None else home_lat
        target_lon = self._target_lon if self._target_lon is not None else home_lon

        # parent=None → unabhängiges Top-Level-Fenster
        self._elevation_win = ElevationProfileWindow(
            home_lat=home_lat,
            home_lon=home_lon,
            target_lat=target_lat,
            target_lon=target_lon,
            home_name="Antenne",
            target_name="Ziel",
            antenna_height_m=antenna_height,
            freq_mhz=freq_mhz,
            cfg=self.cfg,
            save_cfg_cb=self.save_cfg_cb,
            dark=dark,
            parent=None,
        )
        if self._internet_online is not None:
            self._elevation_win.apply_internet_status(self._internet_online)
        self._elevation_win.show()

    def _refresh_map(self) -> None:
        """Beam-Daten aktualisieren ohne Karten-Zoom/Zentrum zu ändern.
        Azimuth wird geglättet für flüssige Bewegung ohne Ruckeln."""
        # ── Fremd-Bewegungserkennung: Rotor extern bewegt? → Marker löschen ──
        if self._map_click_rotor_az is not None and self._target_lat is not None:
            az_axis = getattr(self.ctrl, "az", None)
            if az_axis is not None and bool(getattr(az_axis, "online", False)):
                tgt_d10 = getattr(az_axis, "target_d10", None)
                if tgt_d10 is not None:
                    extern_az = float(tgt_d10) / 10.0
                    if abs(shortest_delta_deg(extern_az, self._map_click_rotor_az)) > 2.0:
                        self._clear_map_target()

        params = self._get_params()
        # dark_mode immer direkt aus Config (force_dark_mode) – auch für Offline-Tiles
        dark = bool(self.cfg.get("ui", {}).get("force_dark_mode", True))
        params["dark_mode"] = dark
        rotor_target = float(params.get("rotor_az_deg", 0.0))
        if self._smooth_rotor_az is None:
            self._smooth_rotor_az = rotor_target
        else:
            delta = shortest_delta_deg(self._smooth_rotor_az, rotor_target)
            self._smooth_rotor_az = wrap_deg(self._smooth_rotor_az + delta * self._SMOOTH_FACTOR)
        ui = self.cfg.get("ui", {})
        antenna_idx = max(0, min(2, int(ui.get("compass_antenna", 0))))
        params["beams"] = self._compute_beams(self._smooth_rotor_az, antenna_idx)
        offs = ui.get("antenna_offsets_az", [0.0, 0.0, 0.0])
        offset_sel = float(offs[antenna_idx]) if antenna_idx < len(offs) else 0.0
        params["azimuth"] = wrap_deg(float(self._smooth_rotor_az or 0.0) + offset_sel)
        if self._map_loaded:
            data = {
                "lat": params["lat"],
                "lon": params["lon"],
                "azimuth": params["azimuth"],
                "opening": params["opening"],
                "range_km": params["range_km"],
                "beams": params["beams"],
                "location_str": params["location_str"],
                "info_standort": params["info_standort"],
                "info_offnung": params["info_offnung"],
                "info_reichweite": params["info_reichweite"],
                "horizon_dist_km": params.get("horizon_dist_km", 0.0),
                "popup_antenna": params.get("popup_antenna", "Antennenstandort"),
                "popup_target": params.get("popup_target", "Ziel"),
            }
            js = f"if (typeof window.updateBeam === 'function') window.updateBeam({json.dumps(data)});"
            self._view.page().runJavaScript(js)
        else:
            html = build_map_html(params, dark=dark)
        offline = params.get("offline", False)
        if self._map_loaded:
            if self._map_offline is not None and self._map_offline != offline:
                if offline:
                    tile_url_off = _offline_tile_url(dark) or (
                        "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"
                        if dark
                        else "https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png"
                    )
                    js_off = f"if (typeof window.setMapOfflineMode === 'function') window.setMapOfflineMode(true, {json.dumps(tile_url_off)});"
                else:
                    tile_url_on = (
                        "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"
                        if dark
                        else "https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png"
                    )
                    js_off = f"if (typeof window.setMapOfflineMode === 'function') window.setMapOfflineMode(false, {json.dumps(tile_url_on)});"
                self._view.page().runJavaScript(js_off)
            elif self._map_dark_mode is not None and self._map_dark_mode != dark:
                js_dark = f"if (typeof window.setMapDarkMode === 'function') window.setMapDarkMode({json.dumps(dark)});"
                self._view.page().runJavaScript(js_dark)
            if self._map_locator_overlay is not None and self._map_locator_overlay != params.get(
                "map_locator_overlay", False
            ):
                loc_on = bool(params.get("map_locator_overlay", False))
                js_loc = f"if (typeof window.setMapLocatorOverlay === 'function') window.setMapLocatorOverlay({json.dumps(loc_on)}, {json.dumps(dark)});"
                self._view.page().runJavaScript(js_loc)
        self._map_dark_mode = dark
        self._map_offline = offline
        self._map_locator_overlay = params.get("map_locator_overlay", False)
        self._update_status_bar()
        self._update_wind_overlay()
        if not self._map_loaded:
            tile_url = _offline_tile_url(params.get("dark_mode", False)) if offline else ""
            if offline and tile_url.startswith("http://"):
                # HTTP-Server funktioniert: setHtml mit about:blank (Tiles von 127.0.0.1)
                if _DEBUG_TILES:
                    print(f"[Map] setHtml (HTTP-Tiles) offline=True tileUrl={tile_url[:50]}...")
                self._view.setHtml(html, QUrl("about:blank"))
            elif offline and tile_url.startswith("rotortiles:"):
                # Kein HTTP: load von rotortiles:map/
                set_pending_map_html(html)
                if _DEBUG_TILES:
                    print(f"[Map] load rotortiles:map/ tileUrl={tile_url}")
                self._view.load(QUrl(f"{ROTORTILES_SCHEME}:map/"))
            else:
                self._view.setHtml(html, QUrl("about:blank"))
            self._map_loaded = True

    def _on_map_load_finished(self, ok: bool) -> None:
        """Wird aufgerufen sobald die Leaflet-Seite vollständig geladen ist."""
        if not ok or not self._map_loaded:
            return
        self._map_page_ready = True
        if self._target_lat is not None and self._target_lon is not None:
            js = (
                f"if (typeof window.setClickMarker === 'function') "
                f"window.setClickMarker({self._target_lat}, {self._target_lon});"
            )
            self._view.page().runJavaScript(js)
        if self._aswatch_last:
            self.update_aswatch_users(self._aswatch_last)
            # WebEngine: JS manchmal erst einen Tick später zuverlässig – erneut anwenden
            QTimer.singleShot(
                200,
                lambda: self.update_aswatch_users(self._aswatch_last)
                if getattr(self, "_aswatch_last", None)
                else None,
            )

    def on_settings_applied(self) -> None:
        """Wird von main_window nach dem Speichern der Einstellungen aufgerufen."""
        dark = bool(self.cfg.get("ui", {}).get("force_dark_mode", True))
        if self._elevation_win is not None and self._elevation_win.isVisible():
            self._elevation_win.apply_theme(dark)

    def closeEvent(self, event):
        if self._elevation_win is not None:
            try:
                self._elevation_win.close()
            except Exception:
                pass
            self._elevation_win = None
        super().closeEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        self._refresh_antenna_dropdown()
        self._refresh_favorites_dropdown()
        self._map_loaded = False
        self._map_page_ready = False
        self._map_dark_mode = None
        self._map_offline = None
        self._map_locator_overlay = None
        self._smooth_rotor_az = None
        self._refresh_map()
        self._refresh_timer.start()
        QTimer.singleShot(100, self._reposition_wind_overlay)

    def _reposition_wind_overlay(self) -> None:
        """Wind-Overlay nach Layout-Berechnung positionieren."""
        cont = self._wind_overlay.parent()
        if isinstance(cont, QWidget) and hasattr(cont, "_wind_overlay"):
            ov = getattr(cont, "_wind_overlay", None)
            if ov is not None:
                margin = 12
                w, h = ov.width(), ov.height()
                ov.setGeometry(cont.width() - w - margin, margin, w, h)
                ov.raise_()

    def reload_for_settings_change(self) -> None:
        """Karte vollständig neu laden (z.B. nach Änderung Dark Mode/Offline in Einstellungen).
        Erzwingt neues HTML mit aktuellem cfg – setMapDarkMode per JS reicht in Offline nicht zuverlässig."""
        if not self._view or not self._view.page():
            return
        self._map_loaded = False
        self._map_page_ready = False
        self._map_dark_mode = None
        self._map_offline = None
        self._map_locator_overlay = None
        self._refresh_map()
        QApplication.processEvents()

    def hideEvent(self, event):
        self._refresh_timer.stop()
        super().hideEvent(event)
