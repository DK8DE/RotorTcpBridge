from __future__ import annotations

import time
from typing import Callable, Optional

from PySide6.QtCore import QEvent, QTimer, Qt, Slot
from PySide6.QtGui import QAction, QCloseEvent, QPalette, QShowEvent
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..app_icon import get_app_icon
from ..angle_utils import clamp_el, fmt_deg, om_beam_contributions_per_sector, wrap_deg
from ..geo_utils import bearing_deg, haversine_km
from ..i18n import t
from ..ui.ui_utils import px_to_dip
from .compass_az_window import CompassWidget
from .compass_el_window import ElevationCompassWidget
from .statistic_compass_widget import parse_heatmap_scale


class CompassWindow(QDialog):
    """Gemeinsames Kompass-Fenster für AZ/EL."""

    def __init__(self, cfg: dict, controller, save_cfg_cb, parent=None, antenna_bridge=None):
        super().__init__(parent)
        self.cfg = cfg
        self.ctrl = controller
        self.save_cfg_cb = save_cfg_cb
        self._antenna_bridge = antenna_bridge
        self.setWindowTitle(t("compass.title"))
        self.setWindowFlag(Qt.WindowType.WindowMinimizeButtonHint, True)
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        self.setWindowIcon(get_app_icon())
        self.resize(940, 640)

        self._target_az: Optional[float] = None
        self._target_el: Optional[float] = None
        self._last_axes_vis: tuple[bool, bool] | None = None
        self._aswatch_marker_fn: Optional[Callable[[], list]] = None
        # Nach STOP: Soll springt auf STOP-Position und bleibt fix; nach ~3s einmal nachziehen
        self._stop_az_ts: Optional[float] = None
        self._stop_el_ts: Optional[float] = None
        self._STOP_PULL_DELAY_S = 3.0
        self._last_label_color: Optional[str] = None
        # AZ Standzeit-Ring (nur Session): je Antenne (0–2) eigene Sektorliste, parallel geführt
        self._dwell_az_seconds_per_ant: list[list[float]] = [[], [], []]
        self._dwell_prev_mono: Optional[float] = None

        root = QVBoxLayout(self)

        row = QHBoxLayout()
        root.addLayout(row, 1)

        # ---------------- AZ ----------------
        slave_az = cfg.get("rotor_bus", {}).get("slave_az", "?")
        slave_el = cfg.get("rotor_bus", {}).get("slave_el", "?")
        self.gb_az = QGroupBox(f"AZ ID:{slave_az}")
        az_l = QVBoxLayout(self.gb_az)

        antenna_idx = max(0, min(2, int(self.cfg.get("ui", {}).get("compass_antenna", 0))))
        self.cb_antenna = QComboBox()
        self.cb_antenna.addItems(self._get_antenna_dropdown_items())
        self.cb_antenna.setMinimumWidth(160)
        self.cb_antenna.setCurrentIndex(antenna_idx)
        self.cb_antenna.currentIndexChanged.connect(self._on_antenna_changed)
        az_antenna_row = QHBoxLayout()
        az_antenna_row.addStretch(1)
        az_antenna_row.addWidget(self.cb_antenna)
        az_antenna_row.addStretch(1)
        az_l.addLayout(az_antenna_row)

        self.lbl_az_soll = QLabel(t("compass.soll_label"))
        self.ed_az_soll = QLineEdit()
        self.ed_az_soll.setPlaceholderText("–")
        self.ed_az_soll.setFixedWidth(70)
        self.ed_az_soll.setMaxLength(7)
        self._style_compass_info_label(self.lbl_az_soll)
        self.w_az_soll = QWidget()
        _h_az_soll = QHBoxLayout(self.w_az_soll)
        _h_az_soll.setContentsMargins(0, 0, 0, 0)
        _h_az_soll.setSpacing(4)
        _h_az_soll.addWidget(self.lbl_az_soll)
        _h_az_soll.addWidget(self.ed_az_soll)

        self.az_compass = CompassWidget(self.gb_az)
        self.az_compass.set_top_center_widget(None)
        self.az_compass.set_soll_overlay_widget(self.w_az_soll)
        az_l.addWidget(self.az_compass, 1)

        az_info = QHBoxLayout()
        az_info.setContentsMargins(7, 0, 7, 0)
        self.btn_stop_az = QPushButton(t("compass.btn_stop_az"))
        self.btn_stop_az.setAutoDefault(False)
        self.btn_stop_az.setDefault(False)
        self.btn_ref_az = QPushButton(t("compass.btn_ref_az"))
        self.btn_ref_az.setAutoDefault(False)
        self.btn_ref_az.setDefault(False)
        self.menu_heatmap_az = QMenu(self)
        self._act_heatmap_strom = QAction(t("compass.heatmap_strom"), self)
        self._act_heatmap_strom.setCheckable(True)
        self._act_heatmap_strom.setData("strom")
        self._act_heatmap_om = QAction(t("compass.heatmap_om_radar"), self)
        self._act_heatmap_om.setCheckable(True)
        self._act_heatmap_om.setToolTip(t("compass.heatmap_om_radar_tooltip"))
        self._act_heatmap_om.setData("om_radar")
        self._act_heatmap_dwell = QAction(t("compass.heatmap_dwell"), self)
        self._act_heatmap_dwell.setCheckable(True)
        self._act_heatmap_dwell.setData("dwell")
        self.menu_heatmap_az.addAction(self._act_heatmap_strom)
        self.menu_heatmap_az.addAction(self._act_heatmap_om)
        self.menu_heatmap_az.addAction(self._act_heatmap_dwell)
        for _a in (self._act_heatmap_strom, self._act_heatmap_om, self._act_heatmap_dwell):
            _a.toggled.connect(self._on_heatmap_az_action_toggled)
        self.btn_heatmap_az = QToolButton()
        self.btn_heatmap_az.setToolTip(t("compass.heatmap_az_rings_tooltip"))
        self.btn_heatmap_az.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.btn_heatmap_az.setMenu(self.menu_heatmap_az)
        self.btn_heatmap_az.setMinimumWidth(px_to_dip(self, 120))
        self._update_heatmap_az_button_text()
        self.btn_reset_dwell_az = QPushButton(t("compass.btn_reset_dwell"))
        self.btn_reset_dwell_az.setToolTip(t("compass.btn_reset_dwell_tooltip"))
        self.btn_reset_dwell_az.setAutoDefault(False)
        self.btn_reset_dwell_az.setDefault(False)
        self.btn_reset_dwell_az.clicked.connect(self._on_reset_dwell_az)
        az_info.addStretch(1)
        az_info.addWidget(self.btn_stop_az)
        az_info.addWidget(self.btn_ref_az)
        az_info.addWidget(self.btn_heatmap_az)
        az_info.addWidget(self.btn_reset_dwell_az)
        az_info.addStretch(1)
        self.ed_az_soll.returnPressed.connect(self._on_az_soll_entered)
        az_l.addLayout(az_info)

        row.addWidget(self.gb_az, 1)

        # ---------------- EL ----------------
        self.gb_el = QGroupBox(f"EL ID:{slave_el}")
        el_l = QVBoxLayout(self.gb_el)
        self.lbl_el_soll = QLabel(t("compass.soll_label"))
        self.ed_el_soll = QLineEdit()
        self.ed_el_soll.setPlaceholderText("–")
        self.ed_el_soll.setFixedWidth(70)
        self.ed_el_soll.setMaxLength(6)
        self._style_compass_info_label(self.lbl_el_soll)
        self.w_el_soll = QWidget()
        _h_el_soll = QHBoxLayout(self.w_el_soll)
        _h_el_soll.setContentsMargins(0, 0, 0, 0)
        _h_el_soll.setSpacing(4)
        _h_el_soll.addWidget(self.lbl_el_soll)
        _h_el_soll.addWidget(self.ed_el_soll)

        self.el_compass = ElevationCompassWidget(self.gb_el)
        self.el_compass.set_soll_overlay_widget(self.w_el_soll)
        el_l.addWidget(self.el_compass, 1)

        el_info = QHBoxLayout()
        el_info.setContentsMargins(7, 0, 7, 0)
        self.btn_stop_el = QPushButton(t("compass.btn_stop_el"))
        self.btn_stop_el.setAutoDefault(False)
        self.btn_stop_el.setDefault(False)
        self.btn_ref_el = QPushButton(t("compass.btn_ref_el"))
        self.btn_ref_el.setAutoDefault(False)
        self.btn_ref_el.setDefault(False)
        self.cb_heatmap_el = QComboBox()
        self.cb_heatmap_el.setMinimumWidth(140)
        el_info.addStretch(1)
        el_info.addWidget(self.btn_stop_el)
        el_info.addWidget(self.btn_ref_el)
        el_info.addWidget(self.cb_heatmap_el)
        el_info.addStretch(1)
        self.ed_el_soll.returnPressed.connect(self._on_el_soll_entered)
        el_l.addLayout(el_info)

        row.addWidget(self.gb_el, 1)

        # Favoriten-Zeile unter der POS-Zeile
        fav_row = QHBoxLayout()
        fav_row.setContentsMargins(7, 6, 7, 4)
        self.cb_fav = QComboBox()
        self.cb_fav.setMinimumWidth(180)
        self.cb_fav.setEditable(False)
        self.ed_fav_name = QLineEdit()
        self.ed_fav_name.setPlaceholderText(t("compass.fav_name_placeholder"))
        self.ed_fav_name.setMaxLength(15)
        self.ed_fav_name.setFixedWidth(110)
        self.btn_fav_save = QPushButton(t("compass.fav_btn_save"))
        self.btn_fav_save.setAutoDefault(False)
        self.btn_fav_save.setDefault(False)
        self.btn_fav_delete = QPushButton(t("compass.fav_btn_delete"))
        self.btn_fav_delete.setAutoDefault(False)
        self.btn_fav_delete.setDefault(False)
        fav_row.addWidget(self.cb_fav)
        fav_row.addWidget(self.ed_fav_name)
        fav_row.addWidget(self.btn_fav_save)
        fav_row.addWidget(self.btn_fav_delete)
        root.addLayout(fav_row, 0)

        self.btn_stop_az.clicked.connect(self._on_stop_az)
        self.btn_stop_el.clicked.connect(self._on_stop_el)
        self.btn_ref_az.clicked.connect(lambda: self.ctrl.reference_az(True))
        self.btn_ref_el.clicked.connect(lambda: self.ctrl.reference_el(True))
        self._migrate_heatmap_ui_keys()
        self._fill_heatmap_az_list()
        self._fill_heatmap_el_combo()
        self._apply_heatmap_combo_selection_to_widgets()
        self.cb_heatmap_el.currentIndexChanged.connect(self._on_heatmap_el_changed)
        self.az_compass.targetPicked.connect(self._on_target_picked_az)
        self.el_compass.targetPicked.connect(self._on_target_picked_el)

        self.cb_fav.activated.connect(self._on_fav_activated)
        self.btn_fav_save.clicked.connect(self._on_fav_save)
        self.btn_fav_delete.clicked.connect(self._on_fav_delete)
        self._refresh_favorites_dropdown()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(33)
        self._antenna_request_timer = QTimer(self)
        self._antenna_request_timer.setInterval(2000)
        self._antenna_request_timer.timeout.connect(self._request_antenna_offsets)
        # Callback: sofort Dropdown aktualisieren wenn Antennenwerte via Einstellungen geändert wurden
        if hasattr(self.ctrl, "on_antenna_offsets_changed"):
            self.ctrl.on_antenna_offsets_changed = self._on_antenna_offsets_changed
        self._tick()

    def _on_antenna_offsets_changed(self) -> None:
        """Wird nach erfolgreichem SETANTOFF-ACK vom Controller aufgerufen → Dropdown sofort aktualisieren."""
        self._refresh_antenna_dropdown()

    def _request_antenna_offsets(self) -> None:
        """Antennenwerte abfragen – nur wenn noch nicht alle drei bekannt sind.
        Timer stoppt sich selbst, sobald alle Werte vorhanden sind."""
        all_known = all(getattr(self.ctrl.az, f"antoff{i}", None) is not None for i in (1, 2, 3))
        if all_known:
            self._antenna_request_timer.stop()
        else:
            if hasattr(self.ctrl, "request_antenna_offsets"):
                self.ctrl.request_antenna_offsets()
        self._refresh_antenna_dropdown()

    def _refresh_antenna_dropdown(self) -> None:
        """Dropdown-Einträge mit aktuellen Versatzwerten aktualisieren; Index aus cfg (single source of truth)."""
        idx = max(0, min(2, int(self.cfg.get("ui", {}).get("compass_antenna", 0))))
        self.cb_antenna.blockSignals(True)
        self.cb_antenna.clear()
        self.cb_antenna.addItems(self._get_antenna_dropdown_items())
        self.cb_antenna.setCurrentIndex(idx)
        self.cb_antenna.blockSignals(False)

    def sync_antenna_from_external(self, idx: int) -> None:
        """Andere Fenster / Bridge / RS485: Index 0–2 in cfg schreiben und Dropdown aktualisieren."""
        idx = max(0, min(2, int(idx)))
        self.cfg.setdefault("ui", {})["compass_antenna"] = idx
        self._refresh_antenna_dropdown()
        self._refresh_after_antenna_changed()

    def _get_antenna_dropdown_items(self) -> list[str]:
        """Antennen-Namen mit Versatz in Klammern: 'Antenne 1 (0°)' etc."""
        names = list(
            self.cfg.get("ui", {}).get("antenna_names", ["Antenne 1", "Antenne 2", "Antenne 3"])
        )
        while len(names) < 3:
            names.append(f"Antenne {len(names) + 1}")
        offsets: list[float] = []
        for slot in (1, 2, 3):
            v = getattr(self.ctrl.az, f"antoff{slot}", None)
            if v is not None:
                offsets.append(float(v))
            else:
                offs = self.cfg.get("ui", {}).get("antenna_offsets_az", [0.0, 0.0, 0.0])
                try:
                    offsets.append(float(offs[slot - 1]))
                except (IndexError, TypeError, ValueError):
                    offsets.append(0.0)
        return [f"{names[i]} ({offsets[i]:.1f}°)" for i in range(3)]

    def _request_immediate_stats_delayed(self) -> None:
        """Heatmap/Stats verzögert laden, damit Zeiger zuerst aktualisiert werden."""
        if hasattr(self.ctrl, "request_immediate_stats"):
            self.ctrl.request_immediate_stats()

    def _refresh_after_antenna_changed(self) -> None:
        """Nach Antennenwechsel: Anzeige mit neuem Versatz aktualisieren."""
        self._tick()
        self.az_compass.update()
        self.el_compass.update()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self._apply_label_colors_from_palette()
        self._refresh_antenna_dropdown()
        if hasattr(self.ctrl, "set_compass_window_open"):
            self.ctrl.set_compass_window_open(True)
        self.sync_heatmap_controls_from_cfg()
        # Zeiger (Position, Antenne) sofort mit höchster Priorität; Heatmap/Stats danach
        if hasattr(self.ctrl, "request_immediate_pos"):
            self.ctrl.request_immediate_pos()
        all_known = all(getattr(self.ctrl.az, f"antoff{i}", None) is not None for i in (1, 2, 3))
        if not all_known:
            # Noch nicht alle Versätze bekannt → einmalig anfordern und Retry-Timer starten
            if hasattr(self.ctrl, "request_antenna_offsets"):
                self.ctrl.request_antenna_offsets()
            self._antenna_request_timer.start()
        # Wenn alle Versätze bereits bekannt: kein Request, kein Timer nötig
        QTimer.singleShot(300, self._request_immediate_stats_delayed)
        self._tick()

    def closeEvent(self, event: QCloseEvent) -> None:
        self._antenna_request_timer.stop()
        if hasattr(self.ctrl, "on_antenna_offsets_changed"):
            self.ctrl.on_antenna_offsets_changed = None
        if hasattr(self.ctrl, "set_compass_window_open"):
            self.ctrl.set_compass_window_open(False)
        super().closeEvent(event)

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.PaletteChange:
            self._apply_label_colors_from_palette()

    @Slot()
    def _on_antenna_changed(self) -> None:
        """Antenne gewechselt → Versatz für Zeiger, Heatmap und Dreieck aktualisieren."""
        idx = max(0, min(2, self.cb_antenna.currentIndex()))
        if "ui" not in self.cfg:
            self.cfg["ui"] = {}
        self.cfg["ui"]["compass_antenna"] = idx
        try:
            self.save_cfg_cb(self.cfg)
        except Exception:
            pass
        if self._antenna_bridge is not None:
            try:
                self._antenna_bridge.selection_changed.emit(idx)
            except Exception:
                pass
        self._refresh_after_antenna_changed()

    def _migrate_heatmap_ui_keys(self) -> None:
        """Alte bool-Flags → compass_heatmap_az/el."""
        ui = self.cfg.setdefault("ui", {})
        if "compass_heatmap_az" not in ui:
            if ui.get("compass_strom_az", ui.get("compass_strom", False)):
                ui["compass_heatmap_az"] = "strom"
            else:
                ui["compass_heatmap_az"] = "off"
        if "compass_heatmap_el" not in ui:
            if ui.get("compass_strom_el", ui.get("compass_strom", False)):
                ui["compass_heatmap_el"] = "strom"
            else:
                ui["compass_heatmap_el"] = "off"
        self._migrate_heatmap_az_modes_cfg()

    def _migrate_heatmap_az_modes_cfg(self) -> None:
        """Einzel-Modus-String → Liste compass_heatmap_az_modes (max. 2 Ringe)."""
        ui = self.cfg.setdefault("ui", {})
        if "compass_heatmap_az_modes" in ui and isinstance(ui.get("compass_heatmap_az_modes"), list):
            return
        old = str(ui.get("compass_heatmap_az", "off")).lower()
        if old in ("strom", "om_radar"):
            ui["compass_heatmap_az_modes"] = [old]
        else:
            ui["compass_heatmap_az_modes"] = []

    def _aswatch_enabled(self) -> bool:
        return bool(self.cfg.get("ui", {}).get("aswatch_udp_enabled", True))

    def _heatmap_az_actions(self) -> list[QAction]:
        return [self._act_heatmap_strom, self._act_heatmap_om, self._act_heatmap_dwell]

    def _fill_heatmap_az_list(self) -> None:
        """OM-Radar-Eintrag nur wenn AirScout/KST aktiv."""
        show_om = self._aswatch_enabled()
        self._act_heatmap_om.setVisible(show_om)
        if not show_om and self._act_heatmap_om.isChecked():
            self._act_heatmap_om.setChecked(False)

    def _get_heatmap_az_modes_from_list(self) -> list[str]:
        modes: list[str] = []
        for act in self._heatmap_az_actions():
            if not act.isVisible():
                continue
            if act.isChecked():
                m = str(act.data() or "")
                if m in ("strom", "om_radar", "dwell"):
                    modes.append(m)
        return CompassWidget._sort_ring_modes(modes)

    def _sync_heatmap_az_list_checks_from_cfg(self) -> None:
        """Haken aus compass_heatmap_az_modes; OM-Radar ggf. abwählen."""
        self._migrate_heatmap_az_modes_cfg()
        ui = self.cfg.setdefault("ui", {})
        raw = ui.get("compass_heatmap_az_modes", [])
        modes: list[str] = (
            [str(m) for m in raw if str(m) in ("strom", "om_radar", "dwell")]
            if isinstance(raw, list)
            else []
        )
        modes = CompassWidget._sort_ring_modes(modes)
        want = set(modes)
        if "om_radar" in want and not self._aswatch_enabled():
            want.discard("om_radar")
        if len(want) > 2:
            for drop in ("dwell", "om_radar", "strom"):
                if drop in want and len(want) > 2:
                    want.discard(drop)
        for act in self._heatmap_az_actions():
            act.blockSignals(True)
        try:
            for act in self._heatmap_az_actions():
                mid = str(act.data() or "")
                if not act.isVisible():
                    act.setChecked(False)
                    continue
                act.setChecked(mid in want)
        finally:
            for act in self._heatmap_az_actions():
                act.blockSignals(False)
        ui["compass_heatmap_az_modes"] = CompassWidget._sort_ring_modes(list(want))
        self._update_heatmap_az_button_text()

    def _update_heatmap_az_button_text(self) -> None:
        """Kurzer Button-Text je nach gewählten Ringen."""
        modes = self._get_heatmap_az_modes_from_list()
        labels = {
            "strom": t("compass.heatmap_strom"),
            "om_radar": t("compass.heatmap_om_radar"),
            "dwell": t("compass.heatmap_dwell"),
        }
        if not modes:
            self.btn_heatmap_az.setText(t("compass.heatmap_az_rings_none"))
        else:
            self.btn_heatmap_az.setText(" · ".join(labels[m] for m in modes))

    def _apply_heatmap_az_modes_to_widget(self) -> None:
        modes = self._get_heatmap_az_modes_from_list()
        self.az_compass.set_heatmap_modes(modes)
        self.az_compass.set_om_radar_sector_count(self._om_radar_sector_count())
        if "om_radar" in modes:
            self.az_compass.set_om_radar_counts(self._compute_om_radar_counts())
        else:
            self.az_compass.set_om_radar_counts(None)
        n_d = self._dwell_sector_count()
        self._ensure_dwell_arrays(n_d)
        self.az_compass.set_dwell_ring_data(
            self._dwell_az_seconds_per_ant[self._selected_antenna_idx()],
            self._dwell_full_seconds(),
            n_d,
        )

    def _fill_heatmap_el_combo(self) -> None:
        self.cb_heatmap_el.blockSignals(True)
        self.cb_heatmap_el.clear()
        self.cb_heatmap_el.addItem(t("compass.heatmap_off"), "off")
        self.cb_heatmap_el.addItem(t("compass.heatmap_strom"), "strom")
        self.cb_heatmap_el.blockSignals(False)

    def _set_heatmap_el_combo_to_mode(self, mode: str) -> None:
        m = str(mode or "off").lower()
        if m not in ("off", "strom"):
            m = "off"
        for i in range(self.cb_heatmap_el.count()):
            if self.cb_heatmap_el.itemData(i) == m:
                self.cb_heatmap_el.setCurrentIndex(i)
                return
        self.cb_heatmap_el.setCurrentIndex(0)

    def _apply_heatmap_combo_selection_to_widgets(self) -> None:
        ui = self.cfg.setdefault("ui", {})
        self._fill_heatmap_az_list()
        self._sync_heatmap_az_list_checks_from_cfg()
        self._apply_heatmap_az_modes_to_widget()
        modes = self._get_heatmap_az_modes_from_list()
        ui["compass_heatmap_az_modes"] = modes
        if len(modes) == 1:
            ui["compass_heatmap_az"] = modes[0]
        elif not modes:
            ui["compass_heatmap_az"] = "off"
        else:
            ui["compass_heatmap_az"] = modes[0]

        mode_el = str(ui.get("compass_heatmap_el", "off"))
        self._set_heatmap_el_combo_to_mode(mode_el)
        self.el_compass.set_heatmap_visible(str(self.cb_heatmap_el.currentData() or "off") == "strom")

    def set_aswatch_marker_provider(self, fn: Optional[Callable[[], list]]) -> None:
        """Liefert die letzte AirScout/KST-Markerliste [{lat, lon, ...}, ...]."""
        self._aswatch_marker_fn = fn

    def refresh_om_radar_from_aswatch(self) -> None:
        """Bei neuen ASWATCHLIST-Daten sofort neu zeichnen (User kommen/gehen ohne Timer-Verzögerung)."""
        if not self.isVisible():
            return
        try:
            if "om_radar" not in self._get_heatmap_az_modes_from_list():
                return
            self.az_compass.set_om_radar_sector_count(self._om_radar_sector_count())
            self.az_compass.set_om_radar_counts(self._compute_om_radar_counts())
        except Exception:
            pass

    def sync_heatmap_controls_from_cfg(self) -> None:
        """Nach Einstellungen (z. B. UDP AirScout an/aus): Liste neu und Modus validieren."""
        self._fill_heatmap_az_list()
        self._sync_heatmap_az_list_checks_from_cfg()
        self._apply_heatmap_az_modes_to_widget()

    def _om_radar_sector_count(self) -> int:
        try:
            n = int(self.cfg.get("ui", {}).get("compass_om_radar_sectors", 60))
        except (TypeError, ValueError):
            n = 60
        return max(10, min(100, n))

    def _om_opening_deg(self) -> float:
        """Öffnungswinkel der gewählten AZ-Antenne (Hardware, sonst Config), für OM-Radar-Verteilung."""
        idx = self._selected_antenna_idx()
        op: Optional[float] = None
        if hasattr(self.ctrl, "az"):
            axis = self.ctrl.az
            for i, attr in enumerate(("angle1", "angle2", "angle3")):
                if i == idx:
                    a = getattr(axis, attr, None)
                    if a is not None:
                        try:
                            op = float(a)
                        except (TypeError, ValueError):
                            op = None
                    break
        if op is None or op <= 0.0:
            ui = self.cfg.get("ui", {})
            angles = ui.get("antenna_angles_az")
            if isinstance(angles, list) and idx < len(angles):
                try:
                    op = float(angles[idx])
                except (TypeError, ValueError):
                    op = 30.0
            else:
                op = 30.0
        if op <= 0.0:
            op = 30.0
        return min(360.0, max(1.0, float(op)))

    def _om_range_km(self) -> float:
        """Reichweite der gewählten AZ-Antenne (km), wie auf der Karte (antenna_ranges_az)."""
        ui = self.cfg.get("ui", {})
        idx = self._selected_antenna_idx()
        ranges = ui.get("antenna_ranges_az", [100.0, 100.0, 100.0])
        try:
            r = float(ranges[idx]) if isinstance(ranges, list) and idx < len(ranges) else 100.0
        except (TypeError, ValueError):
            r = 100.0
        if r <= 0.0:
            r = 100.0
        r = min(4000.0, r)
        return max(1.0, r)

    def _compute_om_radar_counts(self) -> list[float]:
        """Erwartete OM-Dichte je Sektor: nur Stationen mit d ≤ Reichweite; Gewichtung über Öffnungswinkel."""
        n = self._om_radar_sector_count()
        counts = [0.0] * n
        ui = self.cfg.get("ui", {})
        try:
            lat0 = float(ui.get("location_lat", 49.502651))
            lon0 = float(ui.get("location_lon", 8.375019))
        except (TypeError, ValueError):
            return counts
        fn = self._aswatch_marker_fn
        markers = fn() if fn else []
        if not markers:
            return counts
        op = self._om_opening_deg()
        R = self._om_range_km()
        for m in markers:
            if not isinstance(m, dict):
                continue
            try:
                lat = float(m.get("lat"))
                lon = float(m.get("lon"))
            except (TypeError, ValueError):
                continue
            d_km = haversine_km(lat0, lon0, lat, lon)
            if d_km > R:
                continue
            b = bearing_deg(lat0, lon0, lat, lon)
            frac = om_beam_contributions_per_sector(b, op, n)
            for j in range(n):
                counts[j] += frac[j]
        return counts

    def _dwell_sector_count(self) -> int:
        try:
            n = int(self.cfg.get("ui", {}).get("compass_dwell_sectors", 60))
        except (TypeError, ValueError):
            n = 60
        return max(10, min(100, n))

    def _dwell_full_seconds(self) -> float:
        try:
            m = float(self.cfg.get("ui", {}).get("compass_dwell_full_minutes", 5.0))
        except (TypeError, ValueError):
            m = 5.0
        m = max(0.05, m)
        return m * 60.0

    def _selected_antenna_idx(self) -> int:
        return max(0, min(2, int(self.cfg.get("ui", {}).get("compass_antenna", 0))))

    def _ensure_dwell_arrays(self, n_d: int) -> None:
        """Drei parallele Listen à n_d Sektoren; bei Sektorzahl-Wechsel neu mit 0 füllen."""
        for i in range(3):
            if len(self._dwell_az_seconds_per_ant[i]) != n_d:
                self._dwell_az_seconds_per_ant[i] = [0.0] * n_d

    def _on_heatmap_az_action_toggled(self, _checked: bool) -> None:
        act = self.sender()
        if not isinstance(act, QAction):
            return
        checked_actions = [a for a in self._heatmap_az_actions() if a.isVisible() and a.isChecked()]
        if len(checked_actions) > 2:
            act.blockSignals(True)
            try:
                act.setChecked(False)
            finally:
                act.blockSignals(False)
        modes = self._get_heatmap_az_modes_from_list()
        ui = self.cfg.setdefault("ui", {})
        ui["compass_heatmap_az_modes"] = modes
        if len(modes) == 1:
            ui["compass_heatmap_az"] = modes[0]
        elif not modes:
            ui["compass_heatmap_az"] = "off"
        else:
            ui["compass_heatmap_az"] = modes[0]
        self._update_heatmap_az_button_text()
        try:
            self.save_cfg_cb(self.cfg)
        except Exception:
            pass
        self._apply_heatmap_az_modes_to_widget()

    def _on_reset_dwell_az(self) -> None:
        """Kumulative Standzeiten pro Sektor (Session) für alle drei Antennen auf 0 setzen."""
        n = self._dwell_sector_count()
        self._dwell_az_seconds_per_ant = [[0.0] * n, [0.0] * n, [0.0] * n]
        self._apply_heatmap_az_modes_to_widget()

    @Slot()
    def _on_heatmap_el_changed(self) -> None:
        mode = str(self.cb_heatmap_el.currentData() or "off")
        self.cfg.setdefault("ui", {})["compass_heatmap_el"] = mode
        try:
            self.save_cfg_cb(self.cfg)
        except Exception:
            pass
        self.el_compass.set_heatmap_visible(mode == "strom")

    def _get_favorites(self) -> list[dict]:
        """Liefert Liste der gespeicherten Favoriten aus der Config."""
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
        """Dropdown mit Favoriten füllen, sortiert: erst 0–9, dann a–z."""
        favs = self._get_favorites()
        favs = sorted(
            favs,
            key=lambda f: (
                0 if f["name"] and f["name"][0].isdigit() else 1,
                f["name"].lower(),
            ),
        )
        self.cb_fav.blockSignals(True)
        self.cb_fav.clear()
        if not favs:
            self.cb_fav.addItem(t("compass.fav_dropdown_placeholder"), None)
        else:
            for f in favs:
                self.cb_fav.addItem(f"{f['name']} ({f['az']:.1f}°, {f['el']:.1f}°)", f)
        self.cb_fav.blockSignals(False)

    @Slot(int)
    def _on_fav_activated(self, idx: int) -> None:
        """Favorit ausgewählt → dorthin fahren."""
        if idx < 0:
            return
        data = self.cb_fav.itemData(idx)
        if not isinstance(data, dict) or "az" not in data or "el" not in data:
            return
        rotor_az = wrap_deg(float(data["az"]))
        rotor_el = clamp_el(float(data["el"]))
        self._stop_az_ts = None
        self._stop_el_ts = None
        off_az = self._get_antenna_offset_az()
        az_display = wrap_deg(rotor_az + off_az)
        self._target_az = rotor_az
        self._target_el = rotor_el
        self.az_compass.set_target_deg(az_display)
        self.el_compass.set_target_deg(rotor_el)
        self.ed_az_soll.setText(f"{az_display:.1f}")
        self.ed_el_soll.setText(f"{rotor_el:.1f}")
        # Nur set_az_deg/set_el_deg aufrufen – sie setzen moving nur für aktivierte Achsen.
        # Vorher manuell moving=True zu setzen führte bei deaktivierter EL dazu, dass
        # el.moving nie zurückgesetzt wurde (kein EL-Polling) → GETPOSDG blieb im Schnelltakt.
        try:
            self.ctrl.set_az_deg(rotor_az, force=True)
            self.ctrl.set_el_deg(rotor_el, force=True)
        except Exception:
            pass

    @Slot()
    def _on_fav_save(self) -> None:
        """Aktuelle Position unter dem eingegebenen Namen speichern."""
        name = str(self.ed_fav_name.text()).strip()
        if not name:
            return
        name = name[:15]
        try:
            az_d10 = getattr(self.ctrl.az, "pos_d10", None)
            el_d10 = getattr(self.ctrl.el, "pos_d10", None)
        except Exception:
            return
        if az_d10 is None:
            return
        az_deg = float(az_d10) / 10.0
        el_deg = clamp_el(float(el_d10 or 0) / 10.0)  # EL 0 bei nur AZ
        fav = {"name": name[:15], "az": wrap_deg(az_deg), "el": el_deg}
        if "ui" not in self.cfg:
            self.cfg["ui"] = {}
        favs = self._get_favorites()
        favs.append(fav)
        self.cfg["ui"]["compass_favorites"] = favs
        try:
            self.save_cfg_cb(self.cfg)
        except Exception:
            pass
        self._refresh_favorites_dropdown()
        self.ed_fav_name.clear()

    @Slot()
    def _on_fav_delete(self) -> None:
        """Ausgewählten Favoriten löschen."""
        idx = self.cb_fav.currentIndex()
        if idx < 0:
            return
        data = self.cb_fav.itemData(idx)
        if data is None:  # Placeholder bei leerer Liste
            return
        favs = self._get_favorites()
        # Dropdown zeigt sortierte Liste – Eintrag per Daten (name/az/el) identifizieren
        sel_name = data.get("name")
        sel_az = data.get("az")
        sel_el = data.get("el")
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
            self.save_cfg_cb(self.cfg)
        except Exception:
            pass
        self._refresh_favorites_dropdown()

    _COMPASS_INFO_STYLE = "font-size: 12pt; font-weight: 700;"

    @staticmethod
    def _style_compass_info_label(lbl: QLabel) -> None:
        lbl.setStyleSheet(CompassWindow._COMPASS_INFO_STYLE)

    def _apply_label_colors_from_palette(self) -> None:
        """Textfarbe aus Palette des Kompass-Widgets setzen (gleiche Quelle wie Wind/Richtung)."""
        color = self.az_compass.palette().color(QPalette.ColorRole.WindowText)
        self._last_label_color = color.name()
        style = f"{self._COMPASS_INFO_STYLE} color: {self._last_label_color};"
        for lbl in (self.lbl_az_soll, self.lbl_el_soll):
            lbl.setStyleSheet(style)
        if hasattr(self.az_compass, "apply_label_text_color"):
            self.az_compass.apply_label_text_color(color)
        if hasattr(self.el_compass, "apply_label_text_color"):
            self.el_compass.apply_label_text_color(color)

    def _update_groupbox_titles(self) -> None:
        slave_az = self.cfg.get("rotor_bus", {}).get("slave_az", "?")
        slave_el = self.cfg.get("rotor_bus", {}).get("slave_el", "?")
        self.gb_az.setTitle(f"AZ ID:{slave_az}")
        self.gb_el.setTitle(f"EL ID:{slave_el}")

    def refresh_visibility(self) -> None:
        az_on = bool(getattr(self.ctrl, "enable_az", True))
        el_on = bool(getattr(self.ctrl, "enable_el", True))
        self.gb_az.setVisible(az_on)
        self.gb_el.setVisible(el_on)

        vis = (az_on, el_on)
        if vis == self._last_axes_vis:
            return
        self._last_axes_vis = vis

        if az_on and el_on:
            min_w, min_h = 835, 640
            open_w = 1450
        else:
            min_w, min_h = 555, 570
            open_w = min_w

        self.setMinimumSize(min_w, min_h)
        # Bei Sichtbarkeitswechsel Fenster auf Öffnungsgröße setzen.
        # Mindestgröße bleibt davon unabhängig kleiner, damit der User es einziehen kann.
        try:
            if self.isMaximized():
                self.showNormal()
            lay = self.layout()
            if lay is not None:
                lay.invalidate()
                lay.activate()
            self.adjustSize()
            self.resize(open_w, min_h)
        except Exception:
            pass

    @Slot()
    def _tick(self) -> None:
        self.refresh_visibility()
        # Bei Windows-Theme-Wechsel Palette prüfen (PaletteChange kann ausbleiben)
        if self.isVisible():
            try:
                color = self.az_compass.palette().color(QPalette.ColorRole.WindowText)
                cn = color.name()
                if self._last_label_color != cn:
                    self._last_label_color = cn
                    self._apply_label_colors_from_palette()
            except Exception:
                pass

        if bool(self.gb_az.isVisible()):
            self._tick_az()
        if bool(self.gb_el.isVisible()):
            self._tick_el()

    def _get_antenna_offset_az(self) -> float:
        """Versatz der gewählten Antenne für AZ (0–360°). Rotor-Werte vor Config-Fallback."""
        slot = self.cb_antenna.currentIndex() + 1
        v = getattr(self.ctrl.az, f"antoff{slot}", None)
        if v is not None:
            # Config für künftigen Offline-Fall mitschreiben (nur in-memory, kein Speichern)
            try:
                ui = self.cfg.setdefault("ui", {})
                offs = list(ui.get("antenna_offsets_az", [0.0, 0.0, 0.0]))
                while len(offs) < 3:
                    offs.append(0.0)
                offs[slot - 1] = float(v)
                ui["antenna_offsets_az"] = offs[:3]
            except Exception:
                pass
            return float(v)
        # Fallback: Config (wenn Rotor noch nicht geantwortet oder offline)
        offsets = self.cfg.get("ui", {}).get("antenna_offsets_az", [0.0, 0.0, 0.0])
        try:
            return float(offsets[slot - 1])
        except (IndexError, TypeError, ValueError):
            return 0.0

    def _tick_az(self) -> None:
        now = time.time()
        try:
            cur = float(self.ctrl.az.get_smoothed_pos_d10f(now)) / 10.0
        except Exception:
            cur = None
        off_az = self._get_antenna_offset_az()

        try:
            wind_kmh = self.ctrl.az.telemetry.wind_kmh
        except Exception:
            wind_kmh = None
        try:
            wind_dir = self.ctrl.az.telemetry.wind_dir_deg
        except Exception:
            wind_dir = None
        wind_known = bool(getattr(self.ctrl, "wind_enabled_known", False))
        wind_on = bool(getattr(self.ctrl, "wind_enabled", False)) if wind_known else False
        if not wind_on and not wind_known and hasattr(self.ctrl, "az"):
            tel = getattr(self.ctrl.az, "telemetry", None)
            if tel is not None and (
                getattr(tel, "wind_kmh", None) is not None
                or getattr(tel, "wind_dir_deg", None) is not None
            ):
                wind_on = True
        try:
            wd_mode = (
                str(self.cfg.get("ui", {}).get("wind_dir_display", "to") or "to").strip().lower()
            )
        except Exception:
            wd_mode = "to"
        if wd_mode not in ("from", "to"):
            wd_mode = "to"

        tgt: Optional[float] = None
        unknown_target = False

        # Nach STOP: Soll bleibt fix auf STOP-Position; Ist rollt aus und kommt zurück; danach nachziehen
        if self._stop_az_ts is not None:
            if (now - self._stop_az_ts) >= self._STOP_PULL_DELAY_S:
                # Abbremsen vorbei: Soll einmal an Ist angleichen
                if cur is not None:
                    self._target_az = wrap_deg(cur)
                self._stop_az_ts = None
                tgt = self._target_az  # diesen Tick noch die angeglichene Position zeigen
            elif self._target_az is not None:
                tgt = self._target_az  # Soll fix halten, nicht mit Ist mitziehen

        # Wenn manuelle Eingabe abgelaufen (>10s) und PST ein neues Ziel gesetzt hat → freigeben
        if self._target_az is not None and self._stop_az_ts is None:
            manual_ts = float(getattr(self.ctrl, "_compass_manual_az_ts", 0.0) or 0.0)
            if (now - manual_ts) >= 10.0:
                try:
                    pst_d10 = int(getattr(self.ctrl.az, "target_d10", 0))
                    manual_d10 = int(round(self._target_az * 10.0))
                    if pst_d10 != manual_d10:
                        self._target_az = None
                except Exception:
                    pass

        # _target_az hat Vorrang (Eingabefeld/Klick): verhindert Zurückspringen durch PST/anderes
        if self._target_az is not None:
            tgt = self._target_az
        elif tgt is None:
            try:
                axis = self.ctrl.az
                axis_target_d10 = int(getattr(axis, "target_d10"))
                axis_last_set_ts = float(getattr(axis, "last_set_sent_ts", 0.0) or 0.0)
                axis_last_set_target_d10 = getattr(axis, "last_set_sent_target_d10", None)
                tgt = float(axis_target_d10) / 10.0
                unknown_target = (
                    axis_target_d10 == 0
                    and axis_last_set_ts <= 0.0
                    and axis_last_set_target_d10 is None
                )
            except Exception:
                tgt = None
                unknown_target = True

        if tgt is None:
            tgt = self._target_az
        if cur is not None and unknown_target and self._target_az is None:
            tgt = cur
        # Bei falschem Offline-Reset: axis_target_d10=0, last_set_sent_target_d10=None,
        # aber User hatte zuvor Ziel gewählt -> _target_az beibehalten statt 0 zeigen
        if tgt == 0.0 and self._target_az is not None:
            if getattr(self.ctrl.az, "last_set_sent_target_d10", None) is None:
                tgt = self._target_az

        if cur is not None:
            cur_display = wrap_deg(cur + off_az)
            self.az_compass.set_current_deg(cur_display)

        try:
            acc_cw = getattr(self.ctrl.az, "acc_bins_cw", None)
            acc_ccw = getattr(self.ctrl.az, "acc_bins_ccw", None)
            self.az_compass.set_heatmap_offset_deg(off_az)
            self.az_compass.set_bins(acc_cw, acc_ccw)
            ui0 = self.cfg.get("ui", {})
            self.az_compass.set_heatmap_scale(parse_heatmap_scale(ui0, "az"))
        except Exception:
            pass

        # Standzeit je Sektor (nur wenn nicht fährt = LED „Fährt“ rot)
        mono = time.monotonic()
        if self._dwell_prev_mono is None:
            self._dwell_prev_mono = mono
        dt = max(0.0, float(mono - self._dwell_prev_mono))
        self._dwell_prev_mono = mono
        n_d = self._dwell_sector_count()
        self._ensure_dwell_arrays(n_d)
        moving = bool(getattr(self.ctrl.az, "moving", True))
        if not moving and cur is not None:
            # Rotor-Sektor (ohne Antennenversatz); die Drehung zur Anzeige übernimmt
            # paint_dwell_ring(..., offset_deg) wie bei OM-Radar/Heatmap.
            rotor_deg = wrap_deg(cur)
            step = 360.0 / float(n_d)
            idx = int(rotor_deg / step) % n_d
            ant_i = self._selected_antenna_idx()
            self._dwell_az_seconds_per_ant[ant_i][idx] += dt

        try:
            mode_az_list = self._get_heatmap_az_modes_from_list()
            self.az_compass.set_heatmap_modes(mode_az_list)
            self.az_compass.set_om_radar_sector_count(self._om_radar_sector_count())
            if "om_radar" in mode_az_list:
                self.az_compass.set_om_radar_counts(self._compute_om_radar_counts())
            else:
                self.az_compass.set_om_radar_counts(None)
            self.az_compass.set_dwell_ring_data(
                self._dwell_az_seconds_per_ant[self._selected_antenna_idx()],
                self._dwell_full_seconds(),
                n_d,
            )
        except Exception:
            pass

        if tgt is not None:
            tgt_display = wrap_deg(tgt + off_az)
            self.az_compass.set_target_deg(tgt_display)
            if not self.ed_az_soll.hasFocus():
                self.ed_az_soll.setText(f"{tgt_display:.1f}")
        else:
            if not self.ed_az_soll.hasFocus():
                self.ed_az_soll.clear()

        # Wind als letztes aktualisieren (nach Pfeile, Richtungen, Antenne)
        self.az_compass.set_wind_kmh(wind_kmh)
        self.az_compass.set_wind_dir_deg(wind_dir)
        self.az_compass.set_wind_dir_mode(wd_mode)
        self.az_compass.set_wind_visible(wind_on)
        try:
            self.az_compass.set_ref_led_state(bool(self.ctrl.az.referenced))
            self.az_compass.set_moving_led_state(bool(self.ctrl.az.moving))
            self.az_compass.set_online_led_state(bool(self.ctrl.az.online))
        except Exception:
            pass

        # Ist-Text im Kompass (Soll = Eingabe oben rechts)
        if cur is not None:
            cur_display_ov = wrap_deg(cur + off_az)
            ist_txt = t("compass.ist_prefix") + fmt_deg(cur_display_ov)
        else:
            ist_txt = t("compass.ist_prefix") + "–"
        self.az_compass.set_overlay_ist_soll(ist_txt, "")

    def _tick_el(self) -> None:
        now = time.time()
        try:
            cur = float(self.ctrl.el.get_smoothed_pos_d10f(now)) / 10.0
        except Exception:
            cur = None
        # EL: kein Antennenversatz

        tgt: Optional[float] = None
        unknown_target = False

        # Nach STOP: Soll bleibt fix auf STOP-Position; Ist rollt aus und kommt zurück; danach nachziehen
        if self._stop_el_ts is not None:
            if (now - self._stop_el_ts) >= self._STOP_PULL_DELAY_S:
                # Abbremsen vorbei: Soll einmal an Ist angleichen
                if cur is not None:
                    self._target_el = clamp_el(cur)
                self._stop_el_ts = None
                tgt = self._target_el  # diesen Tick noch die angeglichene Position zeigen
            elif self._target_el is not None:
                tgt = self._target_el  # Soll fix halten, nicht mit Ist mitziehen

        # Wenn manuelle Eingabe abgelaufen (>10s) und PST ein neues Ziel gesetzt hat → freigeben
        if self._target_el is not None and self._stop_el_ts is None:
            manual_ts = float(getattr(self.ctrl, "_compass_manual_el_ts", 0.0) or 0.0)
            if (now - manual_ts) >= 10.0:
                try:
                    pst_d10 = int(getattr(self.ctrl.el, "target_d10", 0))
                    manual_d10 = int(round(self._target_el * 10.0))
                    if pst_d10 != manual_d10:
                        self._target_el = None
                except Exception:
                    pass

        # _target_el hat Vorrang (Eingabefeld/Klick): verhindert Zurückspringen
        if self._target_el is not None:
            tgt = self._target_el
        elif tgt is None:
            try:
                axis = self.ctrl.el
                axis_target_d10 = int(getattr(axis, "target_d10"))
                axis_last_set_ts = float(getattr(axis, "last_set_sent_ts", 0.0) or 0.0)
                axis_last_set_target_d10 = getattr(axis, "last_set_sent_target_d10", None)
                tgt = float(axis_target_d10) / 10.0
                unknown_target = (
                    axis_target_d10 == 0
                    and axis_last_set_ts <= 0.0
                    and axis_last_set_target_d10 is None
                )
            except Exception:
                tgt = None
                unknown_target = True

        if tgt is None:
            tgt = self._target_el
        if cur is not None and unknown_target and self._target_el is None:
            tgt = cur
        # Bei falschem Offline-Reset: _target_el beibehalten statt 0 zeigen
        if tgt == 0.0 and self._target_el is not None:
            if getattr(self.ctrl.el, "last_set_sent_target_d10", None) is None:
                tgt = self._target_el

        if cur is not None:
            cur_clamped = clamp_el(cur)
            self.el_compass.set_current_deg(cur_clamped)

        try:
            acc_cw = getattr(self.ctrl.el, "acc_bins_cw", None)
            acc_ccw = getattr(self.ctrl.el, "acc_bins_ccw", None)
            # EL: 72 Bins möglich, für Viertelkreis nur erste 18 nutzen
            if acc_cw is not None and len(acc_cw) >= 18:
                acc_cw = acc_cw[:18]
            if acc_ccw is not None and len(acc_ccw) >= 18:
                acc_ccw = acc_ccw[:18]
            self.el_compass.set_heatmap_offset_deg(0.0)
            self.el_compass.set_bins(acc_cw, acc_ccw)
            ui0 = self.cfg.get("ui", {})
            self.el_compass.set_heatmap_scale(parse_heatmap_scale(ui0, "el"))
        except Exception:
            pass

        if tgt is not None:
            tgt_clamped = clamp_el(tgt)
            self.el_compass.set_target_deg(tgt_clamped)
            if not self.ed_el_soll.hasFocus():
                self.ed_el_soll.setText(f"{tgt_clamped:.1f}")
        else:
            if not self.ed_el_soll.hasFocus():
                self.ed_el_soll.clear()
        try:
            self.el_compass.set_ref_led_state(bool(self.ctrl.el.referenced))
            self.el_compass.set_moving_led_state(bool(self.ctrl.el.moving))
            self.el_compass.set_online_led_state(bool(self.ctrl.el.online))
        except Exception:
            pass

        if cur is not None:
            ist_txt = t("compass.ist_prefix") + fmt_deg(clamp_el(cur))
        else:
            ist_txt = t("compass.ist_prefix") + "–"
        self.el_compass.set_overlay_ist_soll(ist_txt, "")

    def _parse_deg_input(self, text: str) -> Optional[float]:
        """Eingabe parsen: Komma und Punkt als Dezimaltrennzeichen."""
        s = str(text).strip().replace(",", ".")
        if not s:
            return None
        try:
            return float(s)
        except ValueError:
            return None

    @Slot()
    def _on_az_soll_entered(self) -> None:
        """Soll-Wert aus Eingabefeld: Kompass pick_target (wie Klick) → targetPicked → Handler."""
        v = self._parse_deg_input(self.ed_az_soll.text())
        if v is None:
            return
        self.az_compass.pick_target(wrap_deg(v))

    @Slot()
    def _on_el_soll_entered(self) -> None:
        """Soll-Wert aus Eingabefeld: Kompass pick_target (wie Klick) → targetPicked → Handler."""
        v = self._parse_deg_input(self.ed_el_soll.text())
        if v is None:
            return
        self.el_compass.pick_target(clamp_el(v))

    @Slot(float)
    def _on_target_picked_az(self, deg: float) -> None:
        """deg = angezeigter Winkel (Antennenrichtung). Rotor-Ziel = deg - Versatz."""
        self._stop_az_ts = None
        off_az = self._get_antenna_offset_az()
        rotor_deg = wrap_deg(deg - off_az)
        self._target_az = rotor_deg
        self.az_compass.set_target_deg(deg)  # Anzeige bleibt Antennenrichtung
        self.ed_az_soll.setText(f"{deg:.1f}")
        self.ed_az_soll.setFocus()
        self.ed_az_soll.selectAll()
        try:
            d10 = int(round(float(rotor_deg) * 10.0))
            self.ctrl.az.target_d10 = d10
            self.ctrl.az.moving = True
            self.ctrl.az.last_set_sent_ts = time.time()
        except Exception:
            pass
        try:
            self.ctrl.set_az_deg(rotor_deg, force=True)
        except Exception:
            pass

    @Slot(float)
    def _on_target_picked_el(self, deg: float) -> None:
        """deg = angezeigter Winkel (EL: kein Versatz)."""
        self._stop_el_ts = None
        rotor_deg = clamp_el(deg)
        self._target_el = rotor_deg
        self.el_compass.set_target_deg(deg)  # Anzeige bleibt Antennenrichtung
        self.ed_el_soll.setText(f"{deg:.1f}")
        self.ed_el_soll.setFocus()
        self.ed_el_soll.selectAll()
        try:
            d10 = int(round(float(rotor_deg) * 10.0))
            self.ctrl.el.target_d10 = d10
            self.ctrl.el.moving = True
            self.ctrl.el.last_set_sent_ts = time.time()
        except Exception:
            pass
        try:
            self.ctrl.set_el_deg(rotor_deg, force=True)
        except Exception:
            pass

    @Slot()
    def _on_stop_az(self) -> None:
        now = time.time()
        self._stop_az_ts = now
        try:
            cur = float(self.ctrl.az.get_smoothed_pos_d10f(now)) / 10.0
            self._target_az = wrap_deg(cur)  # Soll springt auf Position bei STOP
            # Controller mitschreiben, damit keine andere Stelle alte Soll-Position zurückholt
            self.ctrl.az.target_d10 = int(round(self._target_az * 10.0))
        except Exception:
            pass
        try:
            self.ctrl.stop_az()
        except Exception:
            pass

    @Slot()
    def _on_stop_el(self) -> None:
        now = time.time()
        self._stop_el_ts = now
        try:
            cur = float(self.ctrl.el.get_smoothed_pos_d10f(now)) / 10.0
            self._target_el = clamp_el(cur)  # Soll springt auf Position bei STOP
            # Controller mitschreiben, damit keine andere Stelle alte Soll-Position zurückholt
            self.ctrl.el.target_d10 = int(round(self._target_el * 10.0))
        except Exception:
            pass
        try:
            self.ctrl.stop_el()
        except Exception:
            pass

    def keyPressEvent(self, event) -> None:
        # ESC soll dieses Fenster nicht schließen.
        if event.key() == Qt.Key.Key_Escape:
            event.ignore()
            return
        super().keyPressEvent(event)
