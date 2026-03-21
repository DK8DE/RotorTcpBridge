"""Einstellungsfenster für Verbindung und UI-Optionen."""

from __future__ import annotations

from PySide6.QtCore import Qt, QEvent, QEventLoop, QTimer
from PySide6.QtGui import QCloseEvent, QFont, QKeyEvent, QPalette, QShowEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..app_icon import get_app_icon
from ..command_catalog import command_specs, format_cmd_tooltip
from ..ports import list_serial_ports
from ..i18n import t, load_lang
from ..net_utils import ipv4_subnet_broadcast_default
from .ui_utils import px_to_dip


class SettingsWindow(QDialog):
    """Einstellungen + Server/Hardware Buttons."""

    def __init__(
        self,
        cfg: dict,
        controller,
        pst_server,
        hw_client,
        save_cfg_cb,
        logbuf,
        after_apply_cb,
        rebuild_ui_cb=None,
        map_window=None,
        parent=None,
    ):
        super().__init__(parent)
        self.cfg = cfg
        self.ctrl = controller
        self.pst = pst_server
        self.hw = hw_client
        self.save_cfg_cb = save_cfg_cb
        self.logbuf = logbuf
        self.after_apply_cb = after_apply_cb
        self.rebuild_ui_cb = rebuild_ui_cb
        self._map_window = map_window
        self._antenna_giveup_done = False

        self.setWindowTitle(t("settings.title"))
        self.setWindowFlag(Qt.WindowType.WindowMinimizeButtonHint, True)
        self.setWindowIcon(get_app_icon())
        self.setFixedSize(px_to_dip(self, 820), px_to_dip(self, 610))

        main = QVBoxLayout(self)
        cols = QHBoxLayout()
        left_col = QVBoxLayout()
        right_col = QVBoxLayout()

        # --- Linke Spalte: Verbindung + Einstellungen ---
        gb_conn = QGroupBox(t("settings.group_connection"))
        form_conn = QFormLayout(gb_conn)
        gb_ui = QGroupBox(t("settings.group_ui"))
        form_ui = QFormLayout(gb_ui)

        def _hsep() -> QFrame:
            s = QFrame()
            s.setFrameShape(QFrame.Shape.HLine)
            s.setFrameShadow(QFrame.Shadow.Sunken)
            return s

        self.ed_listen_host = QLineEdit(cfg["pst_server"]["listen_host"])
        self.sp_listen_port_az = QSpinBox()
        self.sp_listen_port_az.setRange(1, 65535)
        self.sp_listen_port_az.setValue(int(cfg["pst_server"]["listen_port_az"]))
        self.sp_listen_port_el = QSpinBox()
        self.sp_listen_port_el.setRange(1, 65535)
        self.sp_listen_port_el.setValue(int(cfg["pst_server"]["listen_port_el"]))
        self.sp_master = QSpinBox()
        self.sp_master.setRange(0, 255)
        self.sp_master.setValue(int(cfg["rotor_bus"]["master_id"]))

        def _clamp_slave_id(v) -> int:
            try:
                x = int(v)
            except (TypeError, ValueError):
                x = 1
            return max(1, min(254, x))

        self.sp_slave_az = QSpinBox()
        self.sp_slave_az.setRange(1, 254)
        self.sp_slave_az.setValue(_clamp_slave_id(cfg["rotor_bus"]["slave_az"]))
        self.sp_slave_el = QSpinBox()
        self.sp_slave_el.setRange(1, 254)
        self.sp_slave_el.setValue(_clamp_slave_id(cfg["rotor_bus"]["slave_el"]))
        self.sp_master.setToolTip(t("settings.tooltip_master_id"))
        self.sp_slave_az.setToolTip(t("settings.tooltip_slave_az"))
        self.sp_slave_el.setToolTip(t("settings.tooltip_slave_el"))
        form_conn.addRow(t("settings.master_id"), self.sp_master)
        form_conn.addRow(t("settings.slave_id_az"), self.sp_slave_az)
        form_conn.addRow(t("settings.slave_id_el"), self.sp_slave_el)
        form_conn.addRow(_hsep())

        self.chk_pst_enabled = QCheckBox(t("settings.chk_pst_enabled"))
        self.chk_pst_enabled.setChecked(bool(cfg["pst_server"].get("enabled", True)))
        self.chk_pst_enabled.setToolTip(t("settings.chk_pst_enabled_tooltip"))
        self.ed_listen_host.setToolTip(t("settings.pst_listen_host_tooltip"))
        self.sp_listen_port_az.setToolTip(t("settings.pst_port_az_tooltip"))
        self.sp_listen_port_el.setToolTip(t("settings.pst_port_el_tooltip"))
        form_conn.addRow(self.chk_pst_enabled)
        form_conn.addRow(t("settings.pst_listen_host"), self.ed_listen_host)
        form_conn.addRow(t("settings.pst_port_az"), self.sp_listen_port_az)
        form_conn.addRow(t("settings.pst_port_el"), self.sp_listen_port_el)
        form_conn.addRow(_hsep())

        self.cb_hw_mode = QComboBox()
        self.cb_hw_mode.addItems(["tcp", "com"])
        self.cb_hw_mode.setCurrentText(cfg["hardware_link"]["mode"])
        self.ed_hw_ip = QLineEdit(cfg["hardware_link"]["tcp_ip"])
        self.sp_hw_port = QSpinBox()
        self.sp_hw_port.setRange(1, 65535)
        self.sp_hw_port.setValue(int(cfg["hardware_link"]["tcp_port"]))

        self.cb_hw_com = QComboBox()
        self.btn_com_refresh = QPushButton("↻")
        self.btn_com_refresh.setFixedWidth(30)
        com_row = QHBoxLayout()
        com_row.addWidget(self.cb_hw_com, 1)
        com_row.addWidget(self.btn_com_refresh)
        com_row_widget = QWidget()
        com_row_widget.setLayout(com_row)

        self.lbl_baud = QLabel(str(cfg["hardware_link"]["baudrate"]))
        self.cb_hw_mode.setToolTip(t("settings.hw_mode_tooltip"))
        self.ed_hw_ip.setToolTip(t("settings.hw_ip_tooltip"))
        self.sp_hw_port.setToolTip(t("settings.hw_port_tooltip"))
        self.cb_hw_com.setToolTip(t("settings.hw_com_tooltip"))
        self.btn_com_refresh.setToolTip(t("settings.btn_com_refresh_tooltip"))
        self.lbl_baud.setToolTip(t("settings.baudrate_tooltip"))

        form_conn.addRow(t("settings.hw_mode"), self.cb_hw_mode)
        form_conn.addRow(t("settings.hw_ip"), self.ed_hw_ip)
        form_conn.addRow(t("settings.hw_port"), self.sp_hw_port)
        form_conn.addRow(t("settings.hw_com"), com_row_widget)
        form_conn.addRow(t("settings.baudrate"), self.lbl_baud)
        form_conn.addRow(_hsep())

        self.chk_enable_az = QCheckBox(t("settings.chk_enable_az"))
        self.chk_enable_el = QCheckBox(t("settings.chk_enable_el"))
        self.chk_enable_az.setChecked(bool(cfg["rotor_bus"].get("enable_az", True)))
        self.chk_enable_el.setChecked(bool(cfg["rotor_bus"].get("enable_el", True)))
        if not self.chk_enable_az.isChecked() and not self.chk_enable_el.isChecked():
            self.chk_enable_az.setChecked(True)
        self.chk_enable_az.setToolTip(t("settings.chk_enable_az_tooltip"))
        self.chk_enable_el.setToolTip(t("settings.chk_enable_el_tooltip"))
        form_conn.addRow(self.chk_enable_az)
        form_conn.addRow(self.chk_enable_el)

        self.chk_force_dark_mode = QCheckBox(t("settings.chk_dark_mode"))
        self.chk_force_dark_mode.setChecked(bool(cfg.get("ui", {}).get("force_dark_mode", False)))
        self.chk_force_dark_mode.setToolTip(t("settings.chk_dark_mode_tooltip"))
        form_ui.addRow(self.chk_force_dark_mode)

        self.chk_udp_ucxlog = QCheckBox(t("settings.chk_udp_ucxlog"))
        self.chk_udp_ucxlog.setToolTip(t("settings.chk_udp_ucxlog_tooltip"))
        self.chk_udp_ucxlog.setChecked(bool(cfg.get("ui", {}).get("udp_ucxlog_enabled", False)))
        form_ui.addRow(self.chk_udp_ucxlog)

        self.chk_udp_pst = QCheckBox(t("settings.chk_udp_pst"))
        self.chk_udp_pst.setToolTip(t("settings.chk_udp_pst_tooltip"))
        self.chk_udp_pst.setChecked(bool(cfg.get("ui", {}).get("udp_pst_enabled", False)))
        self.sp_udp_pst_port = QSpinBox()
        self.sp_udp_pst_port.setRange(1, 65534)
        self.sp_udp_pst_port.setValue(int(cfg.get("ui", {}).get("udp_pst_port", 12000)))
        self.sp_udp_pst_port.setToolTip(t("settings.udp_pst_port_tooltip"))
        pst_row = QHBoxLayout()
        pst_row.setContentsMargins(0, 0, 0, 0)
        pst_row.setAlignment(Qt.AlignmentFlag.AlignLeft)
        pst_row.addWidget(self.chk_udp_pst)
        pst_row.addSpacing(12)
        pst_row.addWidget(QLabel(t("settings.udp_pst_port_label")))
        pst_row.addWidget(self.sp_udp_pst_port)
        pst_row.addStretch(1)
        pst_row_w = QWidget()
        pst_row_w.setLayout(pst_row)
        form_ui.addRow(pst_row_w)

        self.ed_udp_pst_send_host = QLineEdit()
        _pst_auto_host = ipv4_subnet_broadcast_default()
        _pst_saved = str(cfg.get("ui", {}).get("udp_pst_send_host", "")).strip()
        self.ed_udp_pst_send_host.setText(_pst_saved if _pst_saved else _pst_auto_host)
        self.ed_udp_pst_send_host.setPlaceholderText(_pst_auto_host)
        self.ed_udp_pst_send_host.setToolTip(t("settings.udp_pst_send_host_tooltip"))
        self.ed_udp_pst_send_host.setMaximumWidth(200)
        pst_send_row = QHBoxLayout()
        pst_send_row.setContentsMargins(0, 0, 0, 0)
        pst_send_row.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self._lbl_udp_pst_send_host = QLabel(t("settings.udp_pst_send_host_label"))
        pst_send_row.addWidget(self._lbl_udp_pst_send_host)
        pst_send_row.addWidget(self.ed_udp_pst_send_host)
        pst_send_row.addStretch(1)
        pst_send_row_w = QWidget()
        pst_send_row_w.setLayout(pst_send_row)
        form_ui.addRow(pst_send_row_w)

        self.btn_cal_start = QPushButton(t("cmd.btn_start_cal"))
        self.btn_cal_start.setAutoDefault(False)
        self.btn_cal_start.setDefault(False)
        self.btn_cal_reset = QPushButton(t("cmd.btn_reset_cal"))
        self.btn_cal_reset.setAutoDefault(False)
        self.btn_cal_reset.setDefault(False)
        _all_cmd_spec = {s.name.upper(): s for s in command_specs()}
        _spec_setcal = _all_cmd_spec.get("SETCAL")
        if _spec_setcal:
            self.btn_cal_start.setToolTip(format_cmd_tooltip(_spec_setcal))
        _spec_clrstat = _all_cmd_spec.get("CLRSTAT")
        if _spec_clrstat:
            self.btn_cal_reset.setToolTip(format_cmd_tooltip(_spec_clrstat))
        lbl_cal_title = QLabel(t("settings.cal_label"))
        _f_cal = QFont(lbl_cal_title.font())
        _f_cal.setBold(True)
        lbl_cal_title.setFont(_f_cal)
        lbl_cal_desc = QLabel(t("settings.cal_description"))
        lbl_cal_desc.setWordWrap(True)
        lbl_cal_desc.setForegroundRole(QPalette.ColorRole.WindowText)
        cal_btns = QHBoxLayout()
        cal_btns.setContentsMargins(0, 4, 0, 0)
        cal_btns.setAlignment(Qt.AlignmentFlag.AlignLeft)
        cal_btns.addWidget(self.btn_cal_start)
        cal_btns.addWidget(self.btn_cal_reset)
        cal_btns.addStretch(1)
        cal_outer = QVBoxLayout()
        cal_outer.setContentsMargins(0, 0, 0, 0)
        cal_outer.setSpacing(4)
        cal_outer.addWidget(lbl_cal_title)
        cal_outer.addWidget(lbl_cal_desc)
        cal_outer.addLayout(cal_btns)
        cal_row_w = QWidget()
        cal_row_w.setLayout(cal_outer)
        form_ui.addRow(cal_row_w)

        self.cb_wind_dir_display = QComboBox()
        self.cb_wind_dir_display.addItem(t("settings.wind_dir_from"), "from")
        self.cb_wind_dir_display.addItem(t("settings.wind_dir_to"), "to")
        wd_mode = str(cfg.get("ui", {}).get("wind_dir_display", "to") or "to").strip().lower()
        if wd_mode not in ("from", "to"):
            wd_mode = "to"
        idx = self.cb_wind_dir_display.findData(wd_mode)
        if idx < 0:
            idx = 1
        self.cb_wind_dir_display.setCurrentIndex(idx)
        self.cb_wind_dir_display.setToolTip(t("settings.wind_dir_display_tooltip"))
        form_ui.addRow(t("settings.wind_dir_display"), self.cb_wind_dir_display)

        self.cb_language = QComboBox()
        self.cb_language.addItem("Deutsch", "de")
        self.cb_language.addItem("English", "en")
        cur_lang = str(cfg.get("ui", {}).get("language", "de") or "de").strip().lower()
        lang_idx = self.cb_language.findData(cur_lang)
        if lang_idx >= 0:
            self.cb_language.setCurrentIndex(lang_idx)
        self.cb_language.setToolTip(t("settings.language_label_tooltip"))
        form_ui.addRow(t("settings.language_label"), self.cb_language)

        self.ed_location_lat = QDoubleSpinBox()
        self.ed_location_lat.setRange(-90.0, 90.0)
        self.ed_location_lat.setDecimals(6)
        self.ed_location_lat.setValue(float(cfg.get("ui", {}).get("location_lat", 49.502651)))
        self.ed_location_lat.setSuffix("°")
        self.ed_location_lat.setToolTip(t("settings.location_lat_tooltip"))
        form_ui.addRow(t("settings.location_lat"), self.ed_location_lat)
        self.ed_location_lon = QDoubleSpinBox()
        self.ed_location_lon.setRange(-180.0, 180.0)
        self.ed_location_lon.setDecimals(6)
        self.ed_location_lon.setValue(float(cfg.get("ui", {}).get("location_lon", 8.375019)))
        self.ed_location_lon.setSuffix("°")
        self.ed_location_lon.setToolTip(t("settings.location_lon_tooltip"))
        form_ui.addRow(t("settings.location_lon"), self.ed_location_lon)
        self.sp_antenna_height = QDoubleSpinBox()
        self.sp_antenna_height.setRange(0.0, 500.0)
        self.sp_antenna_height.setDecimals(1)
        self.sp_antenna_height.setSingleStep(0.5)
        self.sp_antenna_height.setValue(float(cfg.get("ui", {}).get("antenna_height_m", 0.0)))
        self.sp_antenna_height.setSuffix(" m")
        self.sp_antenna_height.setToolTip(t("settings.antenna_height_tooltip"))
        form_ui.addRow(t("settings.antenna_height"), self.sp_antenna_height)
        # --- Linke Spalte: Verbindung ---
        antenna_names = list(
            cfg.get("ui", {}).get(
                "antenna_names",
                [t("settings.antenna_1"), t("settings.antenna_2"), t("settings.antenna_3")],
            )
        )
        while len(antenna_names) < 3:
            antenna_names.append(f"Antenne {len(antenna_names) + 1}")

        def _antenna_row(
            name_text: str, sp_off: QSpinBox, sp_angle: QSpinBox, sp_range: QSpinBox
        ) -> tuple[QWidget, QLineEdit]:
            name_ed = QLineEdit(name_text)
            name_ed.setMinimumWidth(90)
            sp_off.setRange(0, 360)
            sp_off.setValue(0)
            sp_off.setFixedWidth(55)
            sp_angle.setRange(0, 360)
            sp_angle.setValue(0)
            sp_angle.setFixedWidth(55)
            sp_range.setRange(1, 4000)
            sp_range.setValue(100)
            sp_range.setFixedWidth(60)
            sp_range.setSuffix(" km")
            w = QWidget()
            h = QHBoxLayout(w)
            h.setContentsMargins(0, 0, 0, 0)
            h.addWidget(name_ed)
            h.addWidget(QLabel(t("settings.antenna_offset_unit")))
            h.addWidget(sp_off)
            h.addWidget(QLabel(t("settings.antenna_angle_unit")))
            h.addWidget(sp_angle)
            h.addWidget(sp_range)
            h.addStretch(1)
            return w, name_ed

        self.gb_antenna_az = QGroupBox(t("settings.group_antenna_az"))
        form_az = QFormLayout(self.gb_antenna_az)
        self.sp_az_antoff_1 = QSpinBox()
        self.sp_az_antoff_2 = QSpinBox()
        self.sp_az_antoff_3 = QSpinBox()
        self.sp_az_angle_1 = QSpinBox()
        self.sp_az_angle_2 = QSpinBox()
        self.sp_az_angle_3 = QSpinBox()
        self.sp_az_range_1 = QSpinBox()
        self.sp_az_range_2 = QSpinBox()
        self.sp_az_range_3 = QSpinBox()
        w1, self.ed_antenna_name_1 = _antenna_row(
            antenna_names[0], self.sp_az_antoff_1, self.sp_az_angle_1, self.sp_az_range_1
        )
        w2, self.ed_antenna_name_2 = _antenna_row(
            antenna_names[1], self.sp_az_antoff_2, self.sp_az_angle_2, self.sp_az_range_2
        )
        w3, self.ed_antenna_name_3 = _antenna_row(
            antenna_names[2], self.sp_az_antoff_3, self.sp_az_angle_3, self.sp_az_range_3
        )
        _tt_an = t("settings.tooltip_antenna_name")
        _tt_off = t("settings.tooltip_antenna_offset")
        _tt_ang = t("settings.tooltip_antenna_angle")
        _tt_rng = t("settings.tooltip_antenna_range")
        for ed in (self.ed_antenna_name_1, self.ed_antenna_name_2, self.ed_antenna_name_3):
            ed.setToolTip(_tt_an)
        for sp in (self.sp_az_antoff_1, self.sp_az_antoff_2, self.sp_az_antoff_3):
            sp.setToolTip(_tt_off)
        for sp in (self.sp_az_angle_1, self.sp_az_angle_2, self.sp_az_angle_3):
            sp.setToolTip(_tt_ang)
        for sp in (self.sp_az_range_1, self.sp_az_range_2, self.sp_az_range_3):
            sp.setToolTip(_tt_rng)
        form_az.addRow(t("settings.antenna_1"), w1)
        form_az.addRow(t("settings.antenna_2"), w2)
        form_az.addRow(t("settings.antenna_3"), w3)
        # Initial aus Config (Fallback wenn Rotor noch nicht geantwortet)
        for i, sp in enumerate([self.sp_az_antoff_1, self.sp_az_antoff_2, self.sp_az_antoff_3]):
            try:
                offs = cfg.get("ui", {}).get("antenna_offsets_az", [0, 0, 0])
                if i < len(offs):
                    sp.setValue(int(round(float(offs[i]))))
            except Exception:
                pass
        for i, sp in enumerate([self.sp_az_angle_1, self.sp_az_angle_2, self.sp_az_angle_3]):
            try:
                angles = cfg.get("ui", {}).get("antenna_angles_az", [0, 0, 0])
                if i < len(angles):
                    sp.setValue(int(round(float(angles[i]))))
            except Exception:
                pass
        for i, sp in enumerate([self.sp_az_range_1, self.sp_az_range_2, self.sp_az_range_3]):
            try:
                ranges = cfg.get("ui", {}).get("antenna_ranges_az", [100, 100, 100])
                if i < len(ranges):
                    sp.setValue(int(round(float(ranges[i]))))
            except Exception:
                pass

        self._antenna_offset_spinboxes_az = [
            self.sp_az_antoff_1,
            self.sp_az_antoff_2,
            self.sp_az_antoff_3,
        ]
        self._antenna_angle_spinboxes_az = [
            self.sp_az_angle_1,
            self.sp_az_angle_2,
            self.sp_az_angle_3,
        ]
        self._antenna_range_spinboxes_az = [
            self.sp_az_range_1,
            self.sp_az_range_2,
            self.sp_az_range_3,
        ]
        self._antenna_name_edits_az = [
            self.ed_antenna_name_1,
            self.ed_antenna_name_2,
            self.ed_antenna_name_3,
        ]
        left_col.addWidget(gb_conn, 1)

        # --- Rechte Spalte: Einstellungen, AZ/EL Antennen ---
        right_col.addWidget(gb_ui, 0)
        right_col.addWidget(self.gb_antenna_az, 0)
        right_col.addStretch(1)

        cols.addLayout(left_col)
        cols.addLayout(right_col)
        main.addLayout(cols)

        self.chk_enable_az.installEventFilter(self)
        self.chk_enable_el.installEventFilter(self)
        self.chk_enable_az.stateChanged.connect(self._update_antenna_visibility)
        self.chk_enable_el.stateChanged.connect(self._update_antenna_visibility)
        self._update_antenna_visibility()

        # Versatz- und Öffnungswinkel-Änderungen sofort in Config schreiben (Kompass liest daraus)
        for sp in [self.sp_az_antoff_1, self.sp_az_antoff_2, self.sp_az_antoff_3]:
            sp.valueChanged.connect(self._push_antenna_offsets_to_config)
        for sp in [self.sp_az_angle_1, self.sp_az_angle_2, self.sp_az_angle_3]:
            sp.valueChanged.connect(self._push_antenna_angles_to_config)
        for sp in [self.sp_az_range_1, self.sp_az_range_2, self.sp_az_range_3]:
            sp.valueChanged.connect(self._push_antenna_ranges_to_config)

        # Versatz-Felder live aktualisieren, solange Fenster offen ist
        self._antenna_refresh_timer = QTimer(self)
        self._antenna_refresh_timer.setInterval(200)
        self._antenna_refresh_timer.timeout.connect(self._refresh_antenna_data_once)
        # Periodisch GETANTOFF anfragen (falls Fenster vor HW-Verbindung geöffnet wurde)
        self._antenna_request_timer = QTimer(self)
        self._antenna_request_timer.setInterval(2000)
        self._antenna_request_timer.timeout.connect(self._request_antenna_offsets_if_needed)
        # Nach kurzem Timeout Felder freigeben falls Rotor offline – Nutzer kann manuell eintragen
        self._antenna_giveup_timer = QTimer(self)
        self._antenna_giveup_timer.setSingleShot(True)
        self._antenna_giveup_timer.setInterval(1200)  # 1,2 s – schneller als offline_timeout (2 s)
        self._antenna_giveup_timer.timeout.connect(self._on_antenna_giveup)

        self.btn_com_refresh.clicked.connect(self._refresh_com_ports)
        self._refresh_com_ports(select=cfg["hardware_link"].get("com_port", ""))

        self.lbl_status = QLabel("")
        self.lbl_status.setStyleSheet("color: gray; font-style: italic;")
        self.lbl_status.setWordWrap(True)
        self.btn_cal_start.clicked.connect(self._on_settings_cal_start)
        self.btn_cal_reset.clicked.connect(self._on_settings_cal_reset)
        btnrow = QHBoxLayout()
        btnrow.addWidget(self.lbl_status, 1)
        btn_save_close = QPushButton(t("settings.btn_save_close"))
        btn_save_close.clicked.connect(self._save_and_close)
        btnrow.addWidget(btn_save_close)
        main.addLayout(btnrow)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self._antenna_giveup_done = False
        self._update_antenna_visibility()
        self._update_antenna_offset_enabled()
        self._update_status_on_open()
        self._request_antenna_offsets_if_needed()
        self._antenna_refresh_timer.start()
        self._antenna_request_timer.start()
        self._antenna_giveup_timer.start()
        # Snapshot der aktuellen Spinbox-Werte – nur wenn User etwas ändert, wird ins EEPROM geschrieben
        self._snapshot_antoff = [
            self.sp_az_antoff_1.value(),
            self.sp_az_antoff_2.value(),
            self.sp_az_antoff_3.value(),
        ]
        self._snapshot_angle = [
            self.sp_az_angle_1.value(),
            self.sp_az_angle_2.value(),
            self.sp_az_angle_3.value(),
        ]

    def closeEvent(self, event: QCloseEvent) -> None:
        self._antenna_refresh_timer.stop()
        self._antenna_request_timer.stop()
        self._antenna_giveup_timer.stop()
        super().closeEvent(event)

    def _on_antenna_giveup(self) -> None:
        """Rotor nicht erreichbar – Felder nach kurzem Timeout trotzdem freigeben."""
        self._antenna_giveup_done = True
        self._update_antenna_offset_enabled()

    def _cal_active_dsts_from_ui(self) -> list[int]:
        """Slave-IDs der aktuell in der Maske aktivierten Achsen (wie Rotor-Konfiguration)."""
        dsts: list[int] = []
        if self.chk_enable_az.isChecked():
            v = int(self.sp_slave_az.value())
            if v not in dsts:
                dsts.append(v)
        if self.chk_enable_el.isChecked():
            v = int(self.sp_slave_el.value())
            if v not in dsts:
                dsts.append(v)
        return dsts or [0]

    def _on_settings_cal_start(self) -> None:
        """SETCAL an alle aktiven Achsen senden (wie im Rotor-Konfigurationsfenster)."""
        for dst in self._cal_active_dsts_from_ui():
            try:
                self.ctrl.send_ui_command(dst, "SETCAL", "0", expect_prefix=None, priority=0)
            except Exception:
                pass
        self.lbl_status.setText(t("cmd.hint_cal_start"))

    def _on_settings_cal_reset(self) -> None:
        """CLRSTAT an alle aktiven Achsen senden (wie im Rotor-Konfigurationsfenster)."""
        for dst in self._cal_active_dsts_from_ui():
            try:
                self.ctrl.send_ui_command(dst, "CLRSTAT", "0", expect_prefix=None, priority=0)
            except Exception:
                pass
        self.lbl_status.setText(t("cmd.hint_cal_reset"))

    def _update_status_on_open(self) -> None:
        """Statuszeile beim Öffnen: welche Achsen aktiv und online sind."""
        parts = []
        if self.chk_enable_az.isChecked():
            az_online = bool(getattr(self.ctrl.az, "online", False))
            parts.append(
                t("settings.status_az_active")
                + (t("settings.status_online") if az_online else t("settings.status_offline"))
            )
        if self.chk_enable_el.isChecked():
            el_online = bool(getattr(self.ctrl.el, "online", False))
            parts.append(
                t("settings.status_el_active")
                + (t("settings.status_online") if el_online else t("settings.status_offline"))
            )
        self.lbl_status.setText(" – ".join(parts) if parts else "")

    def _push_antenna_offsets_to_config(self) -> None:
        """Versatz-Spinboxen sofort in Config schreiben, damit Kompass sie nutzt."""
        try:
            self.cfg.setdefault("ui", {})["antenna_offsets_az"] = [
                float(self.sp_az_antoff_1.value()),
                float(self.sp_az_antoff_2.value()),
                float(self.sp_az_antoff_3.value()),
            ]
        except Exception:
            pass

    def _request_antenna_offsets_if_needed(self) -> None:
        """GETANTOFF/GETANGLE erneut anfordern (wichtig wenn Fenster vor HW-Verbindung geöffnet wurde)."""
        if self.hw.is_connected():
            if hasattr(self.ctrl, "request_antenna_offsets"):
                self.ctrl.request_antenna_offsets()
            if hasattr(self.ctrl, "request_antenna_angles"):
                self.ctrl.request_antenna_angles()

    def _set_antenna_offset_and_wait(self, axis: str, slot: int, value_deg: float) -> bool:
        """SETANTOFF senden und auf ACK warten. Gibt True nur bei gültigem ACK zurück."""
        result: list[bool | None] = [None]

        def on_done(ok: bool):
            result[0] = ok
            QTimer.singleShot(0, event_loop.quit)

        event_loop = QEventLoop(self)
        safety_timer = QTimer(self)
        safety_timer.setSingleShot(True)
        safety_timer.timeout.connect(event_loop.quit)
        safety_timer.start(2500)  # Max. 2,5 s warten, sonst abbrechen
        self.ctrl.set_antenna_offset(axis, slot, value_deg, on_done=on_done)
        event_loop.exec()
        safety_timer.stop()
        return result[0] is True

    def _set_antenna_angle_and_wait(self, axis: str, slot: int, value_deg: float) -> bool:
        """SETANGLE senden und auf ACK warten. Gibt True nur bei gültigem ACK zurück."""
        result: list[bool | None] = [None]

        def on_done(ok: bool):
            result[0] = ok
            QTimer.singleShot(0, event_loop.quit)

        event_loop = QEventLoop(self)
        safety_timer = QTimer(self)
        safety_timer.setSingleShot(True)
        safety_timer.timeout.connect(event_loop.quit)
        safety_timer.start(2500)
        self.ctrl.set_antenna_angle(axis, slot, value_deg, on_done=on_done)
        event_loop.exec()
        safety_timer.stop()
        return result[0] is True

    def eventFilter(self, obj, event) -> bool:
        """Verhindert Deaktivieren beider Achsen – mindestens AZ oder EL muss aktiv bleiben."""
        if obj not in (self.chk_enable_az, self.chk_enable_el):
            return super().eventFilter(obj, event)
        az = self.chk_enable_az.isChecked()
        el = self.chk_enable_el.isChecked()
        if event.type() == QEvent.Type.MouseButtonRelease:
            if obj == self.chk_enable_az and az and not el:
                return True
            if obj == self.chk_enable_el and el and not az:
                return True
        if event.type() == QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
            if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Select):
                if obj == self.chk_enable_az and az and not el:
                    return True
                if obj == self.chk_enable_el and el and not az:
                    return True
        return super().eventFilter(obj, event)

    def _update_antenna_visibility(self) -> None:
        self.gb_antenna_az.setVisible(self.chk_enable_az.isChecked())
        self._update_antenna_offset_enabled()

    def _update_antenna_offset_enabled(self) -> None:
        """Versatz-Felder aktivieren: online+Daten ODER Giveup (Rotor offline). Nur AZ."""
        az_ready = False
        az_online = False
        try:
            if self.chk_enable_az.isChecked():
                az = self.ctrl.az
                az_ready = all(
                    getattr(az, a, None) is not None
                    for a in ("antoff1", "antoff2", "antoff3", "angle1", "angle2", "angle3")
                )
                az_online = bool(getattr(az, "online", False))
        except Exception:
            pass
        az_enabled = (
            self.chk_enable_az.isChecked() and az_online and (az_ready or self._antenna_giveup_done)
        )
        for sp in self._antenna_offset_spinboxes_az:
            sp.setEnabled(az_enabled)
        for sp in self._antenna_angle_spinboxes_az:
            sp.setEnabled(az_enabled)
        for sp in self._antenna_range_spinboxes_az:
            sp.setEnabled(az_enabled)
        for ed in self._antenna_name_edits_az:
            ed.setEnabled(az_enabled)

    def _push_antenna_angles_to_config(self) -> None:
        """Öffnungswinkel-Spinboxen sofort in Config schreiben."""
        try:
            self.cfg.setdefault("ui", {})["antenna_angles_az"] = [
                float(self.sp_az_angle_1.value()),
                float(self.sp_az_angle_2.value()),
                float(self.sp_az_angle_3.value()),
            ]
        except Exception:
            pass

    def _push_antenna_ranges_to_config(self) -> None:
        """Reichweiten-Spinboxen sofort in Config schreiben."""
        try:
            self.cfg.setdefault("ui", {})["antenna_ranges_az"] = [
                float(self.sp_az_range_1.value()),
                float(self.sp_az_range_2.value()),
                float(self.sp_az_range_3.value()),
            ]
        except Exception:
            pass

    def _refresh_antenna_data_once(self) -> None:
        """Versatz- und Öffnungswinkel-SpinBoxen aus Controller-State übernehmen."""
        if any(
            s.hasFocus()
            for s in self._antenna_offset_spinboxes_az
            + self._antenna_angle_spinboxes_az
            + self._antenna_range_spinboxes_az
        ):
            return
        all_loaded = True
        try:
            if self.chk_enable_az.isChecked():
                az = self.ctrl.az
                for attr, sp in [
                    ("antoff1", self.sp_az_antoff_1),
                    ("antoff2", self.sp_az_antoff_2),
                    ("antoff3", self.sp_az_antoff_3),
                ]:
                    v = getattr(az, attr, None)
                    if v is None:
                        all_loaded = False
                    else:
                        sp.blockSignals(True)
                        sp.setValue(int(round(v)))
                        sp.blockSignals(False)
                for attr, sp in [
                    ("angle1", self.sp_az_angle_1),
                    ("angle2", self.sp_az_angle_2),
                    ("angle3", self.sp_az_angle_3),
                ]:
                    v = getattr(az, attr, None)
                    if v is None:
                        all_loaded = False
                    else:
                        sp.blockSignals(True)
                        sp.setValue(int(round(v)))
                        sp.blockSignals(False)
                self._push_antenna_offsets_to_config()
                self._push_antenna_angles_to_config()
                self._push_antenna_ranges_to_config()
            if all_loaded:
                self._antenna_refresh_timer.stop()
                self._antenna_request_timer.stop()
                # Snapshot nach erstem vollständigen Poll aus Gerät aktualisieren
                self._snapshot_antoff = [
                    self.sp_az_antoff_1.value(),
                    self.sp_az_antoff_2.value(),
                    self.sp_az_antoff_3.value(),
                ]
                self._snapshot_angle = [
                    self.sp_az_angle_1.value(),
                    self.sp_az_angle_2.value(),
                    self.sp_az_angle_3.value(),
                ]
            self._update_antenna_offset_enabled()
        except Exception:
            pass

    def _refresh_com_ports(self, select: str = ""):
        ports = list_serial_ports()
        current = select or self.cb_hw_com.currentText()
        self.cb_hw_com.clear()

        if not ports:
            if current:
                self.cb_hw_com.addItem(current)
            return

        for p in ports:
            self.cb_hw_com.addItem(p)

        if current:
            idx = self.cb_hw_com.findText(current)
            if idx >= 0:
                self.cb_hw_com.setCurrentIndex(idx)

    def _apply_ids_live(self):
        self.ctrl.update_ids(
            int(self.sp_master.value()),
            int(self.sp_slave_az.value()),
            int(self.sp_slave_el.value()),
            bool(self.chk_enable_az.isChecked()),
            bool(self.chk_enable_el.isChecked()),
        )

    def _save_clicked(self):
        self.lbl_status.setText(t("settings.status_saving"))
        QApplication.processEvents()

        self.cfg["pst_server"]["enabled"] = bool(self.chk_pst_enabled.isChecked())
        self.cfg["pst_server"]["listen_host"] = self.ed_listen_host.text().strip()
        self.cfg["pst_server"]["listen_port_az"] = int(self.sp_listen_port_az.value())
        self.cfg["pst_server"]["listen_port_el"] = int(self.sp_listen_port_el.value())

        self.cfg["rotor_bus"]["master_id"] = int(self.sp_master.value())
        self.cfg["rotor_bus"]["slave_az"] = int(self.sp_slave_az.value())
        self.cfg["rotor_bus"]["slave_el"] = int(self.sp_slave_el.value())
        self.cfg["rotor_bus"]["enable_az"] = bool(self.chk_enable_az.isChecked())
        self.cfg["rotor_bus"]["enable_el"] = bool(self.chk_enable_el.isChecked())

        self.cfg["hardware_link"]["mode"] = self.cb_hw_mode.currentText()
        self.cfg["hardware_link"]["tcp_ip"] = self.ed_hw_ip.text().strip()
        self.cfg["hardware_link"]["tcp_port"] = int(self.sp_hw_port.value())
        self.cfg["hardware_link"]["com_port"] = self.cb_hw_com.currentText().strip()
        self.cfg.setdefault("ui", {})["wind_dir_display"] = str(
            self.cb_wind_dir_display.currentData() or "to"
        )
        self.cfg.setdefault("ui", {})["force_dark_mode"] = bool(
            self.chk_force_dark_mode.isChecked()
        )
        self.cfg.setdefault("ui", {})["udp_ucxlog_enabled"] = bool(self.chk_udp_ucxlog.isChecked())
        self.cfg.setdefault("ui", {})["udp_pst_enabled"] = bool(self.chk_udp_pst.isChecked())
        self.cfg.setdefault("ui", {})["udp_pst_port"] = int(self.sp_udp_pst_port.value())
        self.cfg.setdefault("ui", {})["udp_pst_send_host"] = self.ed_udp_pst_send_host.text().strip()
        new_lang = str(self.cb_language.currentData() or "de")
        lang_changed = self.cfg.get("ui", {}).get("language", "de") != new_lang
        self.cfg.setdefault("ui", {})["language"] = new_lang
        self.cfg.setdefault("ui", {})["location_lat"] = float(self.ed_location_lat.value())
        self.cfg.setdefault("ui", {})["location_lon"] = float(self.ed_location_lon.value())
        self.cfg.setdefault("ui", {})["antenna_height_m"] = float(self.sp_antenna_height.value())
        self.cfg.setdefault("ui", {})["antenna_names"] = [
            self.ed_antenna_name_1.text().strip() or t("settings.antenna_1"),
            self.ed_antenna_name_2.text().strip() or t("settings.antenna_2"),
            self.ed_antenna_name_3.text().strip() or t("settings.antenna_3"),
        ]
        self.cfg.setdefault("ui", {})["antenna_offsets_az"] = [
            float(self.sp_az_antoff_1.value()),
            float(self.sp_az_antoff_2.value()),
            float(self.sp_az_antoff_3.value()),
        ]
        self.cfg.setdefault("ui", {})["antenna_angles_az"] = [
            float(self.sp_az_angle_1.value()),
            float(self.sp_az_angle_2.value()),
            float(self.sp_az_angle_3.value()),
        ]
        self.cfg.setdefault("ui", {})["antenna_ranges_az"] = [
            float(self.sp_az_range_1.value()),
            float(self.sp_az_range_2.value()),
            float(self.sp_az_range_3.value()),
        ]

        # AZ-Antennenversatz und Öffnungswinkel in den Rotor schreiben (SETANTOFF1–3, SETANGLE1–3)
        # Nur übertragen wenn Wert sich gegenüber dem Snapshot beim Öffnen tatsächlich geändert hat
        snapshot_antoff = getattr(self, "_snapshot_antoff", [None, None, None])
        snapshot_angle = getattr(self, "_snapshot_angle", [None, None, None])
        if self.hw.is_connected() and hasattr(self.ctrl, "set_antenna_offset"):
            all_ok = True
            if self.chk_enable_az.isChecked():
                for slot, sp in [
                    (1, self.sp_az_antoff_1),
                    (2, self.sp_az_antoff_2),
                    (3, self.sp_az_antoff_3),
                ]:
                    new_val = int(sp.value())
                    old_val = snapshot_antoff[slot - 1]
                    if old_val is None or new_val != int(old_val):
                        self.lbl_status.setText(t("settings.status_az_saving", slot=slot))
                        QApplication.processEvents()
                        if not self._set_antenna_offset_and_wait("az", slot, float(new_val)):
                            all_ok = False
                        else:
                            snapshot_antoff[slot - 1] = new_val
                for slot, sp in [
                    (1, self.sp_az_angle_1),
                    (2, self.sp_az_angle_2),
                    (3, self.sp_az_angle_3),
                ]:
                    new_val = int(sp.value())
                    old_val = snapshot_angle[slot - 1]
                    if old_val is None or new_val != int(old_val):
                        self.lbl_status.setText(t("settings.status_angle_saving", slot=slot))
                        QApplication.processEvents()
                        if hasattr(
                            self.ctrl, "set_antenna_angle"
                        ) and not self._set_antenna_angle_and_wait("az", slot, float(new_val)):
                            all_ok = False
                        else:
                            snapshot_angle[slot - 1] = new_val
            self.lbl_status.setText(
                t("settings.status_az_saved") if all_ok else t("settings.status_az_error")
            )
            QApplication.processEvents()
            if not all_ok:
                from PySide6.QtWidgets import QMessageBox

                QMessageBox.warning(
                    self,
                    t("settings.msgbox_az_title"),
                    t("settings.msgbox_az_error"),
                )
        else:
            self.lbl_status.setText(t("settings.status_saved"))
            QApplication.processEvents()

        self.save_cfg_cb(self.cfg)
        self._apply_ids_live()
        self.pst.restart(
            self.cfg["pst_server"]["listen_host"],
            int(self.cfg["pst_server"]["listen_port_az"]),
            int(self.cfg["pst_server"]["listen_port_el"]),
        )

        if lang_changed:
            load_lang(new_lang)

        if self._map_window is not None:
            try:
                self._map_window.reload_for_settings_change()
            except Exception:
                pass

        if self.after_apply_cb:
            self.after_apply_cb()

        if lang_changed and self.rebuild_ui_cb:
            self.rebuild_ui_cb()

    def _save_and_close(self):
        """Speichern (inkl. Antennen-Versätze), Status anzeigen, dann Fenster schließen."""
        self._save_clicked()
        self.lbl_status.setText(t("settings.status_closing"))
        QApplication.processEvents()
        QTimer.singleShot(600, self.close)
