"""Einstellungsfenster für Verbindung und UI-Optionen."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QEventLoop, Qt, QTimer
from PySide6.QtGui import QColor, QCloseEvent, QFont, QKeyEvent, QPalette, QShowEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..compass.statistic_compass_widget import compute_bin_min_max
from ..app_icon import get_app_icon
from ..command_catalog import command_specs, format_cmd_tooltip
from ..ports import list_serial_ports
from ..i18n import t, load_lang
from ..net_utils import ipv4_subnet_broadcast_default
from .ui_utils import px_to_dip


def _settings_tooltip_html(text: str, max_width_px: int = 360) -> str:
    """HTML für Tooltips mit begrenzter Breite und Umbruch (Qt zeigt HTML in Tooltips)."""
    e = str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return f"<p style='white-space: pre-wrap; max-width: {max_width_px}px;'>{e}</p>"


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
        # Etwas breiter: linke Navigationsliste + Inhalt rechts
        # +120 px zur Breite des rechten Inhaltsbereichs (Sidebar-Breite unverändert)
        self.setFixedSize(px_to_dip(self, 600), px_to_dip(self, 620))

        main = QVBoxLayout(self)

        gb_conn = QWidget()
        form_conn = QFormLayout(gb_conn)
        gb_ui = QWidget()
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
        self.chk_pst_enabled.setChecked(bool(cfg["pst_server"].get("enabled", False)))
        self.chk_pst_enabled.setToolTip(t("settings.chk_pst_enabled_tooltip"))
        self.ed_listen_host.setToolTip(t("settings.pst_listen_host_tooltip"))
        self.sp_listen_port_az.setToolTip(t("settings.pst_port_az_tooltip"))
        self.sp_listen_port_el.setToolTip(t("settings.pst_port_el_tooltip"))
        _conn_ip_w = px_to_dip(self, 152)  # typ. IPv4-Feld +20 px ggü. 132
        self.ed_listen_host.setMinimumWidth(_conn_ip_w)
        form_conn.addRow(self.chk_pst_enabled)
        form_conn.addRow(t("settings.pst_listen_host"), self.ed_listen_host)
        form_conn.addRow(t("settings.pst_port_az"), self.sp_listen_port_az)
        form_conn.addRow(t("settings.pst_port_el"), self.sp_listen_port_el)
        form_conn.addRow(_hsep())

        self.cb_hw_mode = QComboBox()
        self.cb_hw_mode.addItems(["tcp", "com"])
        self.cb_hw_mode.setCurrentText(cfg["hardware_link"]["mode"])
        self.ed_hw_ip = QLineEdit(cfg["hardware_link"]["tcp_ip"])
        self.ed_hw_ip.setMinimumWidth(_conn_ip_w)
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
        self.chk_force_dark_mode.setChecked(bool(cfg.get("ui", {}).get("force_dark_mode", True)))
        self.chk_force_dark_mode.setToolTip(t("settings.chk_dark_mode_tooltip"))
        form_ui.addRow(self.chk_force_dark_mode)

        _ui0 = cfg.get("ui", {})

        _udp_ip_field_w = px_to_dip(self, 102)  # 82 + 20 px
        _udp_port_field_w = px_to_dip(self, 76 - 15)  # 61 px
        _udp_target_field_w = px_to_dip(self, 102)  # Ziel-IP wie Listen-IP (+20 px)
        # Checkbox-Spalte: Basisbreite minus gewünschten Linksschub (kein neg. margin – der clippt Beschriftung)
        _udp_chk_col_w = max(0, px_to_dip(self, 218) - px_to_dip(self, 130))

        def _asnearest_lbl_row(label_text: str) -> QLabel:
            w = QLabel(label_text)
            w.setWordWrap(True)
            w.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            w.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            return w

        self.chk_udp_ucxlog = QCheckBox(t("settings.chk_udp_ucxlog"))
        self.chk_udp_ucxlog.setToolTip(t("settings.chk_udp_ucxlog_tooltip"))
        self.chk_udp_ucxlog.setChecked(bool(_ui0.get("udp_ucxlog_enabled", True)))
        self.ed_udp_ucxlog_listen = QLineEdit()
        self.ed_udp_ucxlog_listen.setText(str(_ui0.get("udp_ucxlog_listen_host", "0.0.0.0")))
        self.ed_udp_ucxlog_listen.setFixedWidth(_udp_ip_field_w)
        self.ed_udp_ucxlog_listen.setToolTip(t("settings.ucxlog_udp_listen_tooltip"))
        self.sp_udp_ucxlog_port = QSpinBox()
        self.sp_udp_ucxlog_port.setRange(1, 65535)
        self.sp_udp_ucxlog_port.setValue(int(_ui0.get("udp_ucxlog_port", 12040)))
        self.sp_udp_ucxlog_port.setFixedWidth(_udp_port_field_w)
        self.sp_udp_ucxlog_port.setToolTip(t("settings.ucxlog_udp_port_tooltip"))

        self.chk_aswatch_udp = QCheckBox(t("settings.chk_aswatch_udp"))
        self.chk_aswatch_udp.setToolTip(t("settings.chk_aswatch_udp_tooltip"))
        self.chk_aswatch_udp.setChecked(bool(_ui0.get("aswatch_udp_enabled", True)))
        self.ed_aswatch_udp_listen = QLineEdit()
        self.ed_aswatch_udp_listen.setText(str(_ui0.get("aswatch_udp_listen_host", "0.0.0.0")))
        self.ed_aswatch_udp_listen.setFixedWidth(_udp_ip_field_w)
        self.ed_aswatch_udp_listen.setToolTip(t("settings.aswatch_udp_listen_tooltip"))
        self.sp_aswatch_udp_port = QSpinBox()
        self.sp_aswatch_udp_port.setRange(1, 65535)
        self.sp_aswatch_udp_port.setValue(int(_ui0.get("aswatch_udp_port", 9872)))
        self.sp_aswatch_udp_port.setFixedWidth(_udp_port_field_w)
        self.sp_aswatch_udp_port.setToolTip(t("settings.aswatch_udp_port_tooltip"))

        self.chk_aswatch_aircraft = QCheckBox(t("settings.chk_aswatch_aircraft"))
        self.chk_aswatch_aircraft.setToolTip(_settings_tooltip_html(t("settings.chk_aswatch_aircraft_tooltip")))
        self.chk_aswatch_aircraft.setChecked(bool(_ui0.get("aswatch_aircraft_enabled", True)))
        self.lbl_asnearest_min_score = _asnearest_lbl_row(t("settings.asnearest_min_score_label"))
        self.sp_asnearest_min_score = QSpinBox()
        self.sp_asnearest_min_score.setRange(0, 100)
        self.sp_asnearest_min_score.setValue(int(_ui0.get("asnearest_min_score", 45)))
        self.sp_asnearest_min_score.setToolTip(_settings_tooltip_html(t("settings.asnearest_min_score_tooltip")))
        self.lbl_asnearest_min_score.setToolTip(self.sp_asnearest_min_score.toolTip())
        self.sp_asnearest_min_score.setFixedWidth(_udp_port_field_w)
        self.lbl_asnearest_geom_min = _asnearest_lbl_row(t("settings.asnearest_geom_min_label"))
        self.sp_asnearest_geom_min = QSpinBox()
        self.sp_asnearest_geom_min.setRange(0, 100)
        self.sp_asnearest_geom_min.setValue(
            int(round(float(_ui0.get("asnearest_geom_factor_min", 0.20)) * 100.0))
        )
        self.sp_asnearest_geom_min.setToolTip(_settings_tooltip_html(t("settings.asnearest_geom_min_tooltip")))
        self.lbl_asnearest_geom_min.setToolTip(self.sp_asnearest_geom_min.toolTip())
        self.sp_asnearest_geom_min.setFixedWidth(_udp_port_field_w)
        self.lbl_asnearest_list_max_min = _asnearest_lbl_row(t("settings.asnearest_list_max_minutes_label"))
        self.sp_asnearest_list_max_min = QSpinBox()
        self.sp_asnearest_list_max_min.setRange(0, 999)
        self.sp_asnearest_list_max_min.setValue(
            max(0, int(_ui0.get("asnearest_list_max_minutes", 0)))
        )
        self.sp_asnearest_list_max_min.setToolTip(_settings_tooltip_html(t("settings.asnearest_list_max_minutes_tooltip")))
        self.lbl_asnearest_list_max_min.setToolTip(self.sp_asnearest_list_max_min.toolTip())
        self.sp_asnearest_list_max_min.setFixedWidth(_udp_port_field_w)
        self.lbl_asnearest_list_max_rows = _asnearest_lbl_row(t("settings.asnearest_list_max_rows_label"))
        self.sp_asnearest_list_max_rows = QSpinBox()
        self.sp_asnearest_list_max_rows.setRange(1, 500)
        self.sp_asnearest_list_max_rows.setValue(int(_ui0.get("asnearest_list_max_rows", 20)))
        self.sp_asnearest_list_max_rows.setToolTip(_settings_tooltip_html(t("settings.asnearest_list_max_rows_tooltip")))
        self.lbl_asnearest_list_max_rows.setToolTip(self.sp_asnearest_list_max_rows.toolTip())
        self.sp_asnearest_list_max_rows.setFixedWidth(_udp_port_field_w)

        self.chk_udp_pst = QCheckBox(t("settings.chk_udp_pst"))
        self.chk_udp_pst.setToolTip(t("settings.chk_udp_pst_tooltip"))
        self.chk_udp_pst.setChecked(bool(_ui0.get("udp_pst_enabled", True)))
        self.ed_udp_pst_listen = QLineEdit()
        self.ed_udp_pst_listen.setText(str(_ui0.get("udp_pst_listen_host", "0.0.0.0")))
        self.ed_udp_pst_listen.setFixedWidth(_udp_ip_field_w)
        self.ed_udp_pst_listen.setToolTip(t("settings.udp_pst_listen_tooltip"))
        self.sp_udp_pst_port = QSpinBox()
        self.sp_udp_pst_port.setRange(1, 65534)
        self.sp_udp_pst_port.setValue(int(_ui0.get("udp_pst_port", 12000)))
        self.sp_udp_pst_port.setFixedWidth(_udp_port_field_w)
        self.sp_udp_pst_port.setToolTip(t("settings.udp_pst_port_tooltip"))
        self.ed_udp_pst_send_host = QLineEdit()
        _pst_auto_host = ipv4_subnet_broadcast_default()
        _pst_saved = str(_ui0.get("udp_pst_send_host", "")).strip()
        self.ed_udp_pst_send_host.setText(_pst_saved if _pst_saved else _pst_auto_host)
        self.ed_udp_pst_send_host.setPlaceholderText(_pst_auto_host)
        self.ed_udp_pst_send_host.setFixedWidth(_udp_target_field_w)
        self.ed_udp_pst_send_host.setToolTip(t("settings.udp_pst_send_host_tooltip"))

        _lbl_ip = t("settings.udp_listen_ip_label")
        _lbl_port = t("settings.udp_listen_port_label")
        _lbl_tgt = t("settings.udp_pst_target_short_label")

        grid_udp = QGridLayout()
        grid_udp.setContentsMargins(0, 0, 0, 0)
        grid_udp.setHorizontalSpacing(8)
        grid_udp.setVerticalSpacing(6)
        grid_udp.setColumnMinimumWidth(0, _udp_chk_col_w)

        grid_udp.addWidget(self.chk_udp_ucxlog, 0, 0, alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        grid_udp.addWidget(QLabel(_lbl_ip), 0, 1, alignment=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        grid_udp.addWidget(self.ed_udp_ucxlog_listen, 0, 2, alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        grid_udp.addWidget(QLabel(_lbl_port), 0, 3, alignment=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        grid_udp.addWidget(self.sp_udp_ucxlog_port, 0, 4, alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        grid_udp.addWidget(self.chk_aswatch_udp, 1, 0, alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        grid_udp.addWidget(QLabel(_lbl_ip), 1, 1, alignment=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        grid_udp.addWidget(self.ed_aswatch_udp_listen, 1, 2, alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        grid_udp.addWidget(QLabel(_lbl_port), 1, 3, alignment=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        grid_udp.addWidget(self.sp_aswatch_udp_port, 1, 4, alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        grid_udp.addWidget(
            self.chk_aswatch_aircraft,
            2,
            0,
            1,
            5,
            alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        )

        def _asn_full_row(lbl: QLabel, sp: QSpinBox) -> QWidget:
            """Eine Zeile: Beschriftung links (wie Checkbox-Zeilen), Wert rechts."""
            row = QWidget()
            lay = QHBoxLayout(row)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(12)
            lay.addWidget(lbl, 1)
            lay.addWidget(sp, 0, Qt.AlignmentFlag.AlignRight)
            return row

        grid_udp.addWidget(_asn_full_row(self.lbl_asnearest_min_score, self.sp_asnearest_min_score), 3, 0, 1, 5)
        grid_udp.addWidget(_asn_full_row(self.lbl_asnearest_geom_min, self.sp_asnearest_geom_min), 4, 0, 1, 5)
        grid_udp.addWidget(_asn_full_row(self.lbl_asnearest_list_max_min, self.sp_asnearest_list_max_min), 5, 0, 1, 5)
        grid_udp.addWidget(_asn_full_row(self.lbl_asnearest_list_max_rows, self.sp_asnearest_list_max_rows), 6, 0, 1, 5)

        # PST: Checkbox über zwei Zeilen; Ziel-IP eine Zeile unter Listen-IP (unter „IP:“)
        grid_udp.addWidget(self.chk_udp_pst, 7, 0, 2, 1, alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        grid_udp.addWidget(QLabel(_lbl_ip), 7, 1, alignment=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        grid_udp.addWidget(self.ed_udp_pst_listen, 7, 2, alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        grid_udp.addWidget(QLabel(_lbl_port), 7, 3, alignment=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        grid_udp.addWidget(self.sp_udp_pst_port, 7, 4, alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        grid_udp.addWidget(QLabel(_lbl_tgt), 8, 1, alignment=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        grid_udp.addWidget(self.ed_udp_pst_send_host, 8, 2, alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        udp_block_w = QWidget()
        udp_block_w.setLayout(grid_udp)
        form_ui.addRow(udp_block_w)

        # SPID BIG-RAS (TCP) und UDP PST-Rotator schließen sich aus; beide aus ist erlaubt.
        if self.chk_pst_enabled.isChecked() and self.chk_udp_pst.isChecked():
            self.chk_udp_pst.setChecked(False)
        self.chk_pst_enabled.stateChanged.connect(self._on_spid_vs_udp_pst_exclusive)
        self.chk_udp_pst.stateChanged.connect(self._on_udp_pst_vs_spid_exclusive)

        def _sync_aswatch_aircraft_row():
            en = self.chk_aswatch_udp.isChecked()
            self.chk_aswatch_aircraft.setEnabled(en)
            self.lbl_asnearest_min_score.setEnabled(en and self.chk_aswatch_aircraft.isChecked())
            self.sp_asnearest_min_score.setEnabled(en and self.chk_aswatch_aircraft.isChecked())
            self.lbl_asnearest_geom_min.setEnabled(en and self.chk_aswatch_aircraft.isChecked())
            self.sp_asnearest_geom_min.setEnabled(en and self.chk_aswatch_aircraft.isChecked())
            self.lbl_asnearest_list_max_min.setEnabled(en and self.chk_aswatch_aircraft.isChecked())
            self.sp_asnearest_list_max_min.setEnabled(en and self.chk_aswatch_aircraft.isChecked())
            self.lbl_asnearest_list_max_rows.setEnabled(en and self.chk_aswatch_aircraft.isChecked())
            self.sp_asnearest_list_max_rows.setEnabled(en and self.chk_aswatch_aircraft.isChecked())

        self.chk_aswatch_udp.stateChanged.connect(lambda _=None: _sync_aswatch_aircraft_row())
        self.chk_aswatch_aircraft.stateChanged.connect(lambda _=None: _sync_aswatch_aircraft_row())
        _sync_aswatch_aircraft_row()

        _all_cmd_spec = {s.name.upper(): s for s in command_specs()}
        _spec_setcal = _all_cmd_spec.get("SETCAL")
        _spec_clrstat = _all_cmd_spec.get("CLRSTAT")

        self.btn_cal_start = QPushButton(t("cmd.btn_start_cal"))
        self.btn_cal_start.setAutoDefault(False)
        self.btn_cal_start.setDefault(False)
        self.btn_cal_reset = QPushButton(t("cmd.btn_reset_cal"))
        self.btn_cal_reset.setAutoDefault(False)
        self.btn_cal_reset.setDefault(False)
        if _spec_setcal:
            self.btn_cal_start.setToolTip(format_cmd_tooltip(_spec_setcal))
        if _spec_clrstat:
            self.btn_cal_reset.setToolTip(format_cmd_tooltip(_spec_clrstat))

        _ui_hm = cfg.get("ui", {})

        pg_stats = QWidget()
        vl_st = QVBoxLayout(pg_stats)
        _st_pad = px_to_dip(self, 5)
        vl_st.setContentsMargins(_st_pad, _st_pad, _st_pad, _st_pad)
        vl_st.setSpacing(10)
        gb_strom_cal = QGroupBox(t("settings.cal_label"))
        # Gleiches Layout wie Last-Heatmap-Gruppen (Standard-QGroupBox, kein Sonder-Stylesheet).
        fl_strom_cal = QFormLayout(gb_strom_cal)
        self.btn_cal_help = QPushButton(t("settings.stats_help_btn"))
        self.btn_cal_help.setAutoDefault(False)
        self.btn_cal_help.setDefault(False)
        self.btn_cal_help.setToolTip(t("settings.stats_help_title"))
        self.btn_cal_help.clicked.connect(self._show_stats_calibration_help)
        cal_btns = QHBoxLayout()
        cal_btns.setContentsMargins(0, 0, 0, 0)
        cal_btns.addWidget(self.btn_cal_start)
        cal_btns.addWidget(self.btn_cal_reset)
        cal_btns.addWidget(self.btn_cal_help)
        cal_btns.addStretch(1)
        _w_cal_btns = QWidget()
        _w_cal_btns.setLayout(cal_btns)
        fl_strom_cal.addRow(_w_cal_btns)
        vl_st.addWidget(gb_strom_cal)

        _stats_field_w = px_to_dip(self, 100)

        def _hm_spin(val: int) -> QSpinBox:
            sp = QSpinBox()
            sp.setRange(0, 65535)
            sp.setValue(int(val))
            sp.setMaximumWidth(_stats_field_w)
            return sp

        gb_hm_az = QGroupBox(t("settings.stats_heatmap_group_az"))
        fl_hm_az = QFormLayout(gb_hm_az)
        self.chk_heatmap_custom_az = QCheckBox(t("settings.stats_heatmap_custom"))
        self.chk_heatmap_custom_az.setChecked(bool(_ui_hm.get("heatmap_custom_az", False)))
        fl_hm_az.addRow(self.chk_heatmap_custom_az)
        self.sp_thr_blue_az = _hm_spin(int(_ui_hm.get("heatmap_thr_blue_az", 0)))
        self.sp_norm_min_az = _hm_spin(int(_ui_hm.get("heatmap_norm_min_az", 0)))
        self.sp_norm_max_az = _hm_spin(int(_ui_hm.get("heatmap_norm_max_az", 0)))
        self.sp_thr_red_az = _hm_spin(int(_ui_hm.get("heatmap_thr_red_az", 0)))
        self.sp_thr_blue_az.setToolTip(t("settings.stats_heatmap_thr_blue_tooltip"))
        self.sp_norm_min_az.setToolTip(t("settings.stats_heatmap_norm_tooltip"))
        self.sp_norm_max_az.setToolTip(t("settings.stats_heatmap_norm_tooltip"))
        self.sp_thr_red_az.setToolTip(t("settings.stats_heatmap_thr_red_tooltip"))
        fl_hm_az.addRow(t("settings.stats_heatmap_thr_blue"), self.sp_thr_blue_az)
        fl_hm_az.addRow(t("settings.stats_heatmap_norm_min"), self.sp_norm_min_az)
        fl_hm_az.addRow(t("settings.stats_heatmap_norm_max"), self.sp_norm_max_az)
        fl_hm_az.addRow(t("settings.stats_heatmap_thr_red"), self.sp_thr_red_az)
        self.btn_apply_cal_az = QPushButton(t("settings.stats_apply_from_cal"))
        self.btn_apply_cal_az.setToolTip(t("settings.stats_apply_from_cal_tooltip"))
        fl_hm_az.addRow(self.btn_apply_cal_az)
        vl_st.addWidget(gb_hm_az)

        gb_hm_el = QGroupBox(t("settings.stats_heatmap_group_el"))
        fl_hm_el = QFormLayout(gb_hm_el)
        self.chk_heatmap_custom_el = QCheckBox(t("settings.stats_heatmap_custom"))
        self.chk_heatmap_custom_el.setChecked(bool(_ui_hm.get("heatmap_custom_el", False)))
        fl_hm_el.addRow(self.chk_heatmap_custom_el)
        self.sp_thr_blue_el = _hm_spin(int(_ui_hm.get("heatmap_thr_blue_el", 0)))
        self.sp_norm_min_el = _hm_spin(int(_ui_hm.get("heatmap_norm_min_el", 0)))
        self.sp_norm_max_el = _hm_spin(int(_ui_hm.get("heatmap_norm_max_el", 0)))
        self.sp_thr_red_el = _hm_spin(int(_ui_hm.get("heatmap_thr_red_el", 0)))
        self.sp_thr_blue_el.setToolTip(t("settings.stats_heatmap_thr_blue_tooltip"))
        self.sp_norm_min_el.setToolTip(t("settings.stats_heatmap_norm_tooltip"))
        self.sp_norm_max_el.setToolTip(t("settings.stats_heatmap_norm_tooltip"))
        self.sp_thr_red_el.setToolTip(t("settings.stats_heatmap_thr_red_tooltip"))
        fl_hm_el.addRow(t("settings.stats_heatmap_thr_blue"), self.sp_thr_blue_el)
        fl_hm_el.addRow(t("settings.stats_heatmap_norm_min"), self.sp_norm_min_el)
        fl_hm_el.addRow(t("settings.stats_heatmap_norm_max"), self.sp_norm_max_el)
        fl_hm_el.addRow(t("settings.stats_heatmap_thr_red"), self.sp_thr_red_el)
        self.btn_apply_cal_el = QPushButton(t("settings.stats_apply_from_cal"))
        self.btn_apply_cal_el.setToolTip(t("settings.stats_apply_from_cal_tooltip"))
        fl_hm_el.addRow(self.btn_apply_cal_el)
        vl_st.addWidget(gb_hm_el)
        vl_st.addStretch(1)

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
            title: str,
            name_text: str,
            sp_off: QSpinBox,
            sp_angle: QSpinBox,
            sp_range: QSpinBox,
        ) -> tuple[QWidget, QLineEdit]:
            """Titel über dem Namen; dann Name; darunter Versatz/Öffnung/Reichweite."""
            lbl_title = QLabel(title)
            _f = QFont(lbl_title.font())
            _f.setBold(True)
            lbl_title.setFont(_f)
            name_ed = QLineEdit(name_text)
            name_ed.setMinimumWidth(120)
            sp_off.setRange(0, 360)
            sp_off.setValue(0)
            sp_off.setMinimumWidth(50)
            sp_off.setMaximumWidth(64)
            sp_angle.setRange(0, 360)
            sp_angle.setValue(0)
            sp_angle.setMinimumWidth(50)
            sp_angle.setMaximumWidth(64)
            sp_range.setRange(1, 4000)
            sp_range.setValue(100)
            sp_range.setSuffix(" km")
            # Bis 4 Stellen + Suffix — war bei 60px abgeschnitten
            sp_range.setMinimumWidth(px_to_dip(self, 82))
            lbl_rng = QLabel(t("settings.antenna_range_label"))
            lbl_rng.setToolTip(t("settings.tooltip_antenna_range"))
            row_vals = QHBoxLayout()
            row_vals.setSpacing(8)
            row_vals.setContentsMargins(0, 0, 0, 0)
            row_vals.addWidget(QLabel(t("settings.antenna_offset_unit")))
            row_vals.addWidget(sp_off)
            row_vals.addWidget(QLabel(t("settings.antenna_angle_unit")))
            row_vals.addWidget(sp_angle)
            row_vals.addWidget(lbl_rng)
            row_vals.addWidget(sp_range)
            row_vals.addStretch(1)
            row_name = QHBoxLayout()
            row_name.setContentsMargins(0, 0, 0, 0)
            row_name.addWidget(name_ed, 1)
            outer = QVBoxLayout()
            outer.setContentsMargins(0, 0, 0, 0)
            outer.setSpacing(6)
            outer.addWidget(lbl_title)
            outer.addLayout(row_name)
            outer.addLayout(row_vals)
            w = QWidget()
            w.setLayout(outer)
            return w, name_ed

        self.gb_antenna_az = QWidget()
        form_az = QFormLayout(self.gb_antenna_az)
        form_az.setHorizontalSpacing(10)
        form_az.setVerticalSpacing(8)
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
            t("settings.antenna_1"),
            antenna_names[0],
            self.sp_az_antoff_1,
            self.sp_az_angle_1,
            self.sp_az_range_1,
        )
        w2, self.ed_antenna_name_2 = _antenna_row(
            t("settings.antenna_2"),
            antenna_names[1],
            self.sp_az_antoff_2,
            self.sp_az_angle_2,
            self.sp_az_range_2,
        )
        w3, self.ed_antenna_name_3 = _antenna_row(
            t("settings.antenna_3"),
            antenna_names[2],
            self.sp_az_antoff_3,
            self.sp_az_angle_3,
            self.sp_az_range_3,
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
        form_az.addRow(w1)
        form_az.addRow(w2)
        form_az.addRow(w3)
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

        def _scroll_page(inner: QWidget) -> QScrollArea:
            sc = QScrollArea()
            sc.setWidgetResizable(True)
            sc.setFrameShape(QFrame.Shape.NoFrame)
            sc.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            sc.setWidget(inner)
            return sc

        pg_conn = QWidget()
        vl_conn = QVBoxLayout(pg_conn)
        vl_conn.setContentsMargins(0, 0, 0, 0)
        vl_conn.addWidget(gb_conn)
        vl_conn.addStretch(1)

        pg_ui = QWidget()
        vl_ui = QVBoxLayout(pg_ui)
        vl_ui.setContentsMargins(0, 0, 0, 0)
        vl_ui.addWidget(gb_ui)
        vl_ui.addStretch(1)

        pg_ant = QWidget()
        vl_ant = QVBoxLayout(pg_ant)
        vl_ant.setContentsMargins(0, 0, 0, 0)
        vl_ant.addWidget(self.gb_antenna_az)
        vl_ant.addStretch(1)

        _om_sectors = int(cfg.get("ui", {}).get("compass_om_radar_sectors", 60))
        _om_sectors = max(10, min(100, _om_sectors))
        _dwell_sec = int(cfg.get("ui", {}).get("compass_dwell_sectors", 60))
        _dwell_sec = max(10, min(100, _dwell_sec))
        try:
            _dwell_min = float(cfg.get("ui", {}).get("compass_dwell_full_minutes", 5.0))
        except (TypeError, ValueError):
            _dwell_min = 5.0
        _dwell_min = max(0.05, min(240.0, _dwell_min))
        pg_compass = QWidget()
        vl_compass = QVBoxLayout(pg_compass)
        _cp_pad = px_to_dip(self, 5)
        vl_compass.setContentsMargins(_cp_pad, _cp_pad, _cp_pad, _cp_pad)
        vl_compass.setSpacing(10)
        gb_compass_om = QGroupBox(t("settings.compass_om_radar_group"))
        fl_compass_om = QFormLayout(gb_compass_om)
        self.sp_compass_om_sectors = QSpinBox()
        self.sp_compass_om_sectors.setRange(10, 100)
        self.sp_compass_om_sectors.setValue(_om_sectors)
        self.sp_compass_om_sectors.setToolTip(t("settings.compass_om_radar_sectors_tooltip"))
        fl_compass_om.addRow(t("settings.compass_om_radar_sectors"), self.sp_compass_om_sectors)
        vl_compass.addWidget(gb_compass_om)
        gb_compass_dwell = QGroupBox(t("settings.compass_dwell_group"))
        fl_compass_dwell = QFormLayout(gb_compass_dwell)
        self.sp_compass_dwell_sectors = QSpinBox()
        self.sp_compass_dwell_sectors.setRange(10, 100)
        self.sp_compass_dwell_sectors.setValue(_dwell_sec)
        self.sp_compass_dwell_sectors.setToolTip(t("settings.compass_dwell_sectors_tooltip"))
        fl_compass_dwell.addRow(t("settings.compass_dwell_sectors"), self.sp_compass_dwell_sectors)
        self.sp_compass_dwell_minutes = QDoubleSpinBox()
        self.sp_compass_dwell_minutes.setRange(0.1, 240.0)
        self.sp_compass_dwell_minutes.setDecimals(1)
        self.sp_compass_dwell_minutes.setSingleStep(0.5)
        self.sp_compass_dwell_minutes.setValue(_dwell_min)
        self.sp_compass_dwell_minutes.setToolTip(t("settings.compass_dwell_minutes_tooltip"))
        fl_compass_dwell.addRow(t("settings.compass_dwell_minutes"), self.sp_compass_dwell_minutes)
        vl_compass.addWidget(gb_compass_dwell)
        vl_compass.addStretch(1)

        # Navigation: vertikale Liste links (scrollbar bei vielen Einträgen), Inhalt rechts
        # Breite ca. 2/3 der vorherigen Sidebar (ein Drittel schmaler)
        _nav_w_min = px_to_dip(self, 88)
        _nav_w_max = px_to_dip(self, 133)
        self._settings_nav = QListWidget()
        self._settings_nav.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._settings_nav.setWordWrap(True)
        self._settings_nav.setSpacing(0)
        self._settings_nav.setMinimumWidth(_nav_w_min)
        self._settings_nav.setMaximumWidth(_nav_w_max)
        self._settings_nav.setSizePolicy(
            QSizePolicy.Policy.Fixed,
            QSizePolicy.Policy.Expanding,
        )
        self._settings_nav.setUniformItemSizes(True)
        self._settings_stack = QStackedWidget()
        self._settings_stack.addWidget(_scroll_page(pg_ui))
        self._settings_stack.addWidget(_scroll_page(pg_conn))
        self._settings_stack.addWidget(_scroll_page(pg_ant))
        self._settings_stack.addWidget(_scroll_page(pg_compass))
        self._settings_stack.addWidget(_scroll_page(pg_stats))
        self._tab_antenna_index = 2
        for _lbl in (
            t("settings.group_ui"),
            t("settings.group_connection"),
            t("settings.tab_antenna"),
            t("settings.tab_compass"),
            t("settings.tab_statistics"),
        ):
            self._settings_nav.addItem(_lbl)
        self._settings_nav.currentRowChanged.connect(self._on_settings_nav_changed)
        self._settings_nav.setCurrentRow(0)

        self._settings_nav_wrap = QWidget()
        _nav_lay = QVBoxLayout(self._settings_nav_wrap)
        _nav_lay.setContentsMargins(0, 0, 0, 0)
        _nav_lay.addWidget(self._settings_nav)
        self._apply_settings_nav_style()

        _tabs_body = QWidget()
        _tabs_h = QHBoxLayout(_tabs_body)
        _tabs_h.setContentsMargins(0, 0, 0, 0)
        _tabs_h.setSpacing(px_to_dip(self, 8))
        _tabs_h.addWidget(self._settings_nav_wrap, 0)
        _tabs_h.addWidget(self._settings_stack, 1)
        main.addWidget(_tabs_body, 1)

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
        self.btn_apply_cal_az.clicked.connect(self._on_apply_cal_heatmap_az)
        self.btn_apply_cal_el.clicked.connect(self._on_apply_cal_heatmap_el)
        btnrow = QHBoxLayout()
        btnrow.addWidget(self.lbl_status, 1)
        btn_save_close = QPushButton(t("settings.btn_save_close"))
        btn_save_close.clicked.connect(self._save_and_close)
        btnrow.addWidget(btn_save_close)
        main.addLayout(btnrow)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        if hasattr(self.ctrl, "set_settings_window_open"):
            self.ctrl.set_settings_window_open(True)
        if hasattr(self.ctrl, "request_immediate_stats"):
            self.ctrl.request_immediate_stats()
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
        if hasattr(self.ctrl, "set_settings_window_open"):
            self.ctrl.set_settings_window_open(False)
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

    def _stats_calibration_help_text(self) -> str:
        """Hilfetext: Stromkalibrierung + Last-Heatmap (wie früher im Tab-Text)."""
        return (
            t("settings.cal_label")
            + "\n\n"
            + t("settings.cal_description")
            + "\n\n"
            + t("settings.stats_help_header_heatmap")
            + "\n\n"
            + t("settings.stats_help_mid")
            + "\n\n"
            + t("settings.stats_heatmap_info")
        )

    def _show_stats_calibration_help(self) -> None:
        """Dialog mit Erklärung zu Stromkalibrierung und Farb-/Schwellenwerten."""
        dlg = QDialog(self)
        dlg.setWindowTitle(t("settings.stats_help_title"))
        dlg.setModal(True)
        dlg.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        root = QVBoxLayout(dlg)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)
        te = QTextEdit()
        te.setReadOnly(True)
        te.setPlainText(self._stats_calibration_help_text())
        te.setMinimumWidth(px_to_dip(self, 480))
        te.setMinimumHeight(px_to_dip(self, 320))
        root.addWidget(te)
        btn_ok = QPushButton(t("about.btn_close"))
        btn_ok.setFixedWidth(100)
        btn_ok.clicked.connect(dlg.accept)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(btn_ok)
        root.addLayout(row)
        dlg.exec()

    def _on_apply_cal_heatmap_az(self) -> None:
        """CAL-Bins (AZ): Min/Max in Normfelder übernehmen, Schwellen mit Rand."""
        az = self.ctrl.az
        if getattr(az, "cal_state", 0) != 2:
            self.lbl_status.setText(t("settings.stats_cal_no_data"))
            return
        mn, mx = compute_bin_min_max(
            getattr(az, "cal_bins_cw", None),
            getattr(az, "cal_bins_ccw", None),
            False,
        )
        if mn is None or mx is None:
            self.lbl_status.setText(t("settings.stats_cal_no_data"))
            return
        margin = 50
        self.sp_norm_min_az.setValue(int(mn))
        self.sp_norm_max_az.setValue(int(mx))
        self.sp_thr_blue_az.setValue(max(0, int(mn) - margin))
        self.sp_thr_red_az.setValue(min(65535, int(mx) + margin))
        self.chk_heatmap_custom_az.setChecked(True)
        self.lbl_status.setText(t("settings.stats_apply_ok"))

    def _on_apply_cal_heatmap_el(self) -> None:
        """CAL-Bins (EL): Min/Max übernehmen."""
        el = getattr(self.ctrl, "el", None)
        if el is None or getattr(el, "cal_state", 0) != 2:
            self.lbl_status.setText(t("settings.stats_cal_no_data"))
            return
        mn, mx = compute_bin_min_max(
            getattr(el, "cal_bins_cw", None),
            getattr(el, "cal_bins_ccw", None),
            True,
        )
        if mn is None or mx is None:
            self.lbl_status.setText(t("settings.stats_cal_no_data"))
            return
        margin = 50
        self.sp_norm_min_el.setValue(int(mn))
        self.sp_norm_max_el.setValue(int(mx))
        self.sp_thr_blue_el.setValue(max(0, int(mn) - margin))
        self.sp_thr_red_el.setValue(min(65535, int(mx) + margin))
        self.chk_heatmap_custom_el.setChecked(True)
        self.lbl_status.setText(t("settings.stats_apply_ok"))

    def _heatmap_scale_valid(self) -> bool:
        """Prüft thr_blue ≤ norm_min ≤ norm_max ≤ thr_red wenn Custom aktiv."""
        for prefix in ("az", "el"):
            chk = getattr(self, f"chk_heatmap_custom_{prefix}", None)
            if chk is None or not chk.isChecked():
                continue
            tb = getattr(self, f"sp_thr_blue_{prefix}").value()
            nm = getattr(self, f"sp_norm_min_{prefix}").value()
            nx = getattr(self, f"sp_norm_max_{prefix}").value()
            tr = getattr(self, f"sp_thr_red_{prefix}").value()
            if not (tb <= nm <= nx <= tr):
                self.lbl_status.setText(t("settings.stats_heatmap_invalid"))
                return False
        return True

    def _on_spid_vs_udp_pst_exclusive(self, _state: int) -> None:
        """Nur eines aktiv: SPID BIG-RAS vs. UDP PST-Rotator."""
        if self.chk_pst_enabled.isChecked():
            self.chk_udp_pst.blockSignals(True)
            self.chk_udp_pst.setChecked(False)
            self.chk_udp_pst.blockSignals(False)

    def _on_udp_pst_vs_spid_exclusive(self, _state: int) -> None:
        """Nur eines aktiv: UDP PST-Rotator vs. SPID BIG-RAS."""
        if self.chk_udp_pst.isChecked():
            self.chk_pst_enabled.blockSignals(True)
            self.chk_pst_enabled.setChecked(False)
            self.chk_pst_enabled.blockSignals(False)

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

    def _on_settings_nav_changed(self, row: int) -> None:
        """Linke Liste → rechten Stacked-Inhalt umschalten."""
        if row < 0 or row >= self._settings_stack.count():
            return
        self._settings_stack.setCurrentIndex(row)

    def _apply_settings_nav_style(self) -> None:
        """Sidebar wie große Kacheln; Farben aus der System-/App-Palette (Highlight, Base, …)."""
        p = self.palette()

        def _hex(c: QColor) -> str:
            return c.name(QColor.NameFormat.HexRgb)

        nav_bg = _hex(p.color(QPalette.ColorRole.Window))
        item_bg = _hex(p.color(QPalette.ColorRole.Base))
        sel_bg = _hex(p.color(QPalette.ColorRole.Highlight))
        sel_fg = _hex(p.color(QPalette.ColorRole.HighlightedText))
        fg = _hex(p.color(QPalette.ColorRole.WindowText))
        sep = "#787878"

        # Feste Zeilenhöhe 45 px (DIP); Hover dunkelgrau mit heller Schrift (nicht Weiß auf Weiß)
        row_h = px_to_dip(self, 45)
        pad_x = px_to_dip(self, 8)
        gap = px_to_dip(self, 2)
        rad = px_to_dip(self, 3)
        hover_bg = "#4f4f4f"
        hover_fg = "#eaeaea"

        self._settings_nav_wrap.setStyleSheet(f"background-color: {nav_bg};")
        self._settings_nav.setStyleSheet(
            f"""
            QListWidget {{
                background-color: {nav_bg};
                border: none;
                border-right: 1px solid {sep};
                outline: none;
            }}
            QListWidget::item {{
                background-color: {item_bg};
                color: {fg};
                padding: 0 {pad_x}px;
                margin: {gap}px 4px;
                border-radius: {rad}px;
                min-height: {row_h}px;
                max-height: {row_h}px;
            }}
            QListWidget::item:selected {{
                background-color: {sel_bg};
                color: {sel_fg};
            }}
            QListWidget::item:hover:!selected {{
                background-color: {hover_bg};
                color: {hover_fg};
            }}
            """
        )

    def _update_antenna_visibility(self) -> None:
        show = self.chk_enable_az.isChecked()
        self.gb_antenna_az.setVisible(show)
        try:
            item = self._settings_nav.item(self._tab_antenna_index)
            if item is not None:
                item.setHidden(not show)
            if not show and self._settings_nav.currentRow() == self._tab_antenna_index:
                self._settings_nav.setCurrentRow(0)
        except Exception:
            pass
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

    def _save_clicked(self) -> bool:
        self.lbl_status.setText(t("settings.status_saving"))
        QApplication.processEvents()
        if not self._heatmap_scale_valid():
            return False

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
        self.cfg.setdefault("ui", {})["udp_ucxlog_port"] = int(self.sp_udp_ucxlog_port.value())
        self.cfg.setdefault("ui", {})["udp_ucxlog_listen_host"] = self.ed_udp_ucxlog_listen.text().strip()
        self.cfg.setdefault("ui", {})["aswatch_udp_enabled"] = bool(self.chk_aswatch_udp.isChecked())
        self.cfg.setdefault("ui", {})["aswatch_udp_port"] = int(self.sp_aswatch_udp_port.value())
        self.cfg.setdefault("ui", {})["aswatch_udp_listen_host"] = self.ed_aswatch_udp_listen.text().strip()
        self.cfg.setdefault("ui", {})["aswatch_aircraft_enabled"] = bool(self.chk_aswatch_aircraft.isChecked())
        self.cfg.setdefault("ui", {})["asnearest_min_score"] = int(self.sp_asnearest_min_score.value())
        self.cfg.setdefault("ui", {})["asnearest_geom_factor_min"] = max(
            0.0, min(1.0, int(self.sp_asnearest_geom_min.value()) / 100.0)
        )
        self.cfg.setdefault("ui", {})["asnearest_list_max_minutes"] = max(
            0, int(self.sp_asnearest_list_max_min.value())
        )
        self.cfg.setdefault("ui", {})["asnearest_list_max_rows"] = max(
            1, min(500, int(self.sp_asnearest_list_max_rows.value()))
        )
        self.cfg.setdefault("ui", {})["udp_pst_enabled"] = bool(self.chk_udp_pst.isChecked())
        self.cfg.setdefault("ui", {})["udp_pst_port"] = int(self.sp_udp_pst_port.value())
        self.cfg.setdefault("ui", {})["udp_pst_listen_host"] = self.ed_udp_pst_listen.text().strip()
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
        uih = self.cfg.setdefault("ui", {})
        uih["heatmap_custom_az"] = bool(self.chk_heatmap_custom_az.isChecked())
        uih["heatmap_thr_blue_az"] = int(self.sp_thr_blue_az.value())
        uih["heatmap_norm_min_az"] = int(self.sp_norm_min_az.value())
        uih["heatmap_norm_max_az"] = int(self.sp_norm_max_az.value())
        uih["heatmap_thr_red_az"] = int(self.sp_thr_red_az.value())
        uih["heatmap_custom_el"] = bool(self.chk_heatmap_custom_el.isChecked())
        uih["heatmap_thr_blue_el"] = int(self.sp_thr_blue_el.value())
        uih["heatmap_norm_min_el"] = int(self.sp_norm_min_el.value())
        uih["heatmap_norm_max_el"] = int(self.sp_norm_max_el.value())
        uih["heatmap_thr_red_el"] = int(self.sp_thr_red_el.value())
        uih["compass_om_radar_sectors"] = int(self.sp_compass_om_sectors.value())
        uih["compass_dwell_sectors"] = int(self.sp_compass_dwell_sectors.value())
        uih["compass_dwell_full_minutes"] = float(self.sp_compass_dwell_minutes.value())

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
        return True

    def _save_and_close(self):
        """Speichern (inkl. Antennen-Versätze), Status anzeigen, dann Fenster schließen."""
        if not self._save_clicked():
            return
        self.lbl_status.setText(t("settings.status_closing"))
        QApplication.processEvents()
        QTimer.singleShot(600, self.close)
