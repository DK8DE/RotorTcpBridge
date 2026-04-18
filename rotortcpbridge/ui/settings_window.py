"""Einstellungsfenster für Verbindung und UI-Optionen."""

from __future__ import annotations

import time

from PySide6.QtCore import (
    QEvent,
    QEventLoop,
    QMetaObject,
    QRegularExpression,
    QSize,
    Qt,
    QTimer,
)
from PySide6.QtGui import (
    QColor,
    QCloseEvent,
    QFont,
    QHideEvent,
    QKeyEvent,
    QPalette,
    QRegularExpressionValidator,
    QShowEvent,
)
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
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
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
from ..i18n import format_tooltip_html, load_lang, t, tt
from ..geo_utils import maidenhead_to_lat_lon
from ..net_utils import ipv4_subnet_broadcast_default
from ..rotor_controller import SYNC_UI_NAK_PREFIX
from ..rig_bridge.manager import RigBridgeManager
from .settings_rig_bridge_tab import RigBridgeTab
from .settings_shortcuts_tab import ShortcutsTab
from .led_widget import Led
from .ui_utils import px_to_dip


def _sync_got_ack_value(r: str | None) -> bool:
    """True nur bei gültigem ACK-Parameter (nicht Timeout None, nicht NAK-Präfix)."""
    if r is None:
        return False
    return not str(r).startswith(SYNC_UI_NAK_PREFIX)


def _sync_nak_notimpl(r: str | None) -> bool:
    """NAK mit NOTIMPL (optionale Befehle nicht in Firmware) — für LED als Bus-OK zählen."""
    if r is None or not str(r).startswith(SYNC_UI_NAK_PREFIX):
        return False
    return "NOTIMPL" in str(r).upper()


class _SettingsScrollArea(QScrollArea):
    """Scroll-Bereich ohne riesige minimumSizeHint vom Formular-Inhalt (Dialog-Höhe bleibt steuerbar)."""

    def minimumSizeHint(self) -> QSize:
        sh = super().minimumSizeHint()
        cap_h = px_to_dip(self, 120)
        return QSize(sh.width(), min(sh.height(), cap_h))

    def sizeHint(self) -> QSize:
        sh = super().sizeHint()
        cap_h = px_to_dip(self, 560)
        return QSize(sh.width(), min(sh.height(), cap_h))


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
        rig_bridge_manager: RigBridgeManager | None = None,
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
        self.rig_bridge_manager = rig_bridge_manager
        self.rebuild_ui_cb = rebuild_ui_cb
        self._map_window = map_window
        self._antenna_giveup_done = False
        # Nur ein Controller-Bus-Load gleichzeitig (sonst verschachteln sich QEventLoops → doppelte TX)
        self._controller_load_busy = False
        self._controller_load_queued = False

        self.setWindowTitle(t("settings.title"))
        self.setWindowFlag(Qt.WindowType.WindowMinimizeButtonHint, True)
        self.setWindowIcon(get_app_icon())
        # Breite fix; Höhe frei skalierbar (niedrige Mindesthöhe). Start-Höhe beim Öffnen: _settings_open_height_dip.
        self._settings_base_width_dip = 730
        self._settings_min_height_dip = 320
        self._settings_open_height_dip = 710
        self.setFixedWidth(px_to_dip(self, self._settings_base_width_dip))
        self.setMinimumHeight(px_to_dip(self, self._settings_min_height_dip))

        main = QVBoxLayout(self)

        gb_master_rotor = QGroupBox(t("settings.group_master_rotor_ids"))
        form_master_rotor = QFormLayout(gb_master_rotor)
        gb_bus_connection = QGroupBox(t("settings.group_bus_connection"))
        form_bus_connection = QFormLayout(gb_bus_connection)
        gb_axes = QGroupBox(t("settings.group_axes"))
        form_axes = QFormLayout(gb_axes)
        gb_ui = QWidget()
        form_ui = QFormLayout(gb_ui)

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
        self.sp_master.setToolTip(tt("settings.tooltip_master_id"))
        self.sp_slave_az.setToolTip(tt("settings.tooltip_slave_az"))
        self.sp_slave_el.setToolTip(tt("settings.tooltip_slave_el"))
        form_master_rotor.addRow(t("settings.master_id"), self.sp_master)
        form_master_rotor.addRow(t("settings.slave_id_az"), self.sp_slave_az)
        form_master_rotor.addRow(t("settings.slave_id_el"), self.sp_slave_el)

        self.chk_pst_enabled = QCheckBox(t("settings.chk_pst_enabled"))
        self.chk_pst_enabled.setChecked(bool(cfg["pst_server"].get("enabled", False)))
        self.chk_pst_enabled.setToolTip(tt("settings.chk_pst_enabled_tooltip"))
        self.ed_listen_host.setToolTip(tt("settings.pst_listen_host_tooltip"))
        self.sp_listen_port_az.setToolTip(tt("settings.pst_port_az_tooltip"))
        self.sp_listen_port_el.setToolTip(tt("settings.pst_port_el_tooltip"))
        _conn_ip_w = px_to_dip(self, 152)  # typ. IPv4-Feld +20 px ggü. 132
        self.ed_listen_host.setMinimumWidth(_conn_ip_w)
        w_spid_tcp_pst = QWidget()
        fl_spid_tcp_pst = QFormLayout(w_spid_tcp_pst)
        fl_spid_tcp_pst.setContentsMargins(0, 0, 0, 0)
        fl_spid_tcp_pst.addRow(self.chk_pst_enabled)
        fl_spid_tcp_pst.addRow(t("settings.pst_listen_host"), self.ed_listen_host)
        fl_spid_tcp_pst.addRow(t("settings.pst_port_az"), self.sp_listen_port_az)
        fl_spid_tcp_pst.addRow(t("settings.pst_port_el"), self.sp_listen_port_el)

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

        self.cb_hw_mode.setToolTip(tt("settings.hw_mode_tooltip"))
        self.ed_hw_ip.setToolTip(tt("settings.hw_ip_tooltip"))
        self.sp_hw_port.setToolTip(tt("settings.hw_port_tooltip"))
        self.cb_hw_com.setToolTip(tt("settings.hw_com_tooltip"))
        self.btn_com_refresh.setToolTip(tt("settings.btn_com_refresh_tooltip"))

        form_bus_connection.addRow(t("settings.hw_mode"), self.cb_hw_mode)
        form_bus_connection.addRow(t("settings.hw_ip"), self.ed_hw_ip)
        form_bus_connection.addRow(t("settings.hw_port"), self.sp_hw_port)
        form_bus_connection.addRow(t("settings.hw_com"), com_row_widget)

        self.chk_enable_az = QCheckBox(t("settings.chk_enable_az"))
        self.chk_enable_el = QCheckBox(t("settings.chk_enable_el"))
        self.chk_enable_az.setChecked(bool(cfg["rotor_bus"].get("enable_az", True)))
        self.chk_enable_el.setChecked(bool(cfg["rotor_bus"].get("enable_el", True)))
        if not self.chk_enable_az.isChecked() and not self.chk_enable_el.isChecked():
            self.chk_enable_az.setChecked(True)
        self.chk_enable_az.setToolTip(tt("settings.chk_enable_az_tooltip"))
        self.chk_enable_el.setToolTip(tt("settings.chk_enable_el_tooltip"))
        form_axes.addRow(self.chk_enable_az)
        form_axes.addRow(self.chk_enable_el)

        self.chk_force_dark_mode = QCheckBox(t("settings.chk_dark_mode"))
        self.chk_force_dark_mode.setChecked(bool(cfg.get("ui", {}).get("force_dark_mode", True)))
        self.chk_force_dark_mode.setToolTip(tt("settings.chk_dark_mode_tooltip"))
        gb_display = QGroupBox(t("settings.group_display"))
        fl_display = QFormLayout(gb_display)
        fl_display.addRow(self.chk_force_dark_mode)

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
        self.chk_udp_ucxlog.setToolTip(tt("settings.chk_udp_ucxlog_tooltip"))
        self.chk_udp_ucxlog.setChecked(bool(_ui0.get("udp_ucxlog_enabled", False)))
        self.ed_udp_ucxlog_listen = QLineEdit()
        self.ed_udp_ucxlog_listen.setText(str(_ui0.get("udp_ucxlog_listen_host", "127.0.0.1")))
        self.ed_udp_ucxlog_listen.setFixedWidth(_udp_ip_field_w)
        self.ed_udp_ucxlog_listen.setToolTip(tt("settings.ucxlog_udp_listen_tooltip"))
        self.sp_udp_ucxlog_port = QSpinBox()
        self.sp_udp_ucxlog_port.setRange(1, 65535)
        self.sp_udp_ucxlog_port.setValue(int(_ui0.get("udp_ucxlog_port", 12040)))
        self.sp_udp_ucxlog_port.setFixedWidth(_udp_port_field_w)
        self.sp_udp_ucxlog_port.setToolTip(tt("settings.ucxlog_udp_port_tooltip"))

        self.chk_aswatch_udp = QCheckBox(t("settings.chk_aswatch_udp"))
        self.chk_aswatch_udp.setToolTip(tt("settings.chk_aswatch_udp_tooltip"))
        self.chk_aswatch_udp.setChecked(bool(_ui0.get("aswatch_udp_enabled", False)))
        self.ed_aswatch_udp_listen = QLineEdit()
        self.ed_aswatch_udp_listen.setText(str(_ui0.get("aswatch_udp_listen_host", "127.0.0.1")))
        self.ed_aswatch_udp_listen.setFixedWidth(_udp_ip_field_w)
        self.ed_aswatch_udp_listen.setToolTip(tt("settings.aswatch_udp_listen_tooltip"))
        self.sp_aswatch_udp_port = QSpinBox()
        self.sp_aswatch_udp_port.setRange(1, 65535)
        self.sp_aswatch_udp_port.setValue(int(_ui0.get("aswatch_udp_port", 9872)))
        self.sp_aswatch_udp_port.setFixedWidth(_udp_port_field_w)
        self.sp_aswatch_udp_port.setToolTip(tt("settings.aswatch_udp_port_tooltip"))

        self.chk_aswatch_aircraft = QCheckBox(t("settings.chk_aswatch_aircraft"))
        self.chk_aswatch_aircraft.setToolTip(format_tooltip_html(t("settings.chk_aswatch_aircraft_tooltip")))
        self.chk_aswatch_aircraft.setChecked(bool(_ui0.get("aswatch_aircraft_enabled", True)))
        self.lbl_asnearest_min_score = _asnearest_lbl_row(t("settings.asnearest_min_score_label"))
        self.sp_asnearest_min_score = QSpinBox()
        self.sp_asnearest_min_score.setRange(0, 100)
        self.sp_asnearest_min_score.setValue(int(_ui0.get("asnearest_min_score", 45)))
        self.sp_asnearest_min_score.setToolTip(format_tooltip_html(t("settings.asnearest_min_score_tooltip")))
        self.lbl_asnearest_min_score.setToolTip(self.sp_asnearest_min_score.toolTip())
        self.sp_asnearest_min_score.setFixedWidth(_udp_port_field_w)
        self.lbl_asnearest_geom_min = _asnearest_lbl_row(t("settings.asnearest_geom_min_label"))
        self.sp_asnearest_geom_min = QSpinBox()
        self.sp_asnearest_geom_min.setRange(0, 100)
        self.sp_asnearest_geom_min.setValue(
            int(round(float(_ui0.get("asnearest_geom_factor_min", 0.20)) * 100.0))
        )
        self.sp_asnearest_geom_min.setToolTip(format_tooltip_html(t("settings.asnearest_geom_min_tooltip")))
        self.lbl_asnearest_geom_min.setToolTip(self.sp_asnearest_geom_min.toolTip())
        self.sp_asnearest_geom_min.setFixedWidth(_udp_port_field_w)
        self.lbl_asnearest_list_max_min = _asnearest_lbl_row(t("settings.asnearest_list_max_minutes_label"))
        self.sp_asnearest_list_max_min = QSpinBox()
        self.sp_asnearest_list_max_min.setRange(0, 999)
        self.sp_asnearest_list_max_min.setValue(
            max(0, int(_ui0.get("asnearest_list_max_minutes", 0)))
        )
        self.sp_asnearest_list_max_min.setToolTip(format_tooltip_html(t("settings.asnearest_list_max_minutes_tooltip")))
        self.lbl_asnearest_list_max_min.setToolTip(self.sp_asnearest_list_max_min.toolTip())
        self.sp_asnearest_list_max_min.setFixedWidth(_udp_port_field_w)
        self.lbl_asnearest_list_max_rows = _asnearest_lbl_row(t("settings.asnearest_list_max_rows_label"))
        self.sp_asnearest_list_max_rows = QSpinBox()
        self.sp_asnearest_list_max_rows.setRange(1, 500)
        self.sp_asnearest_list_max_rows.setValue(int(_ui0.get("asnearest_list_max_rows", 20)))
        self.sp_asnearest_list_max_rows.setToolTip(format_tooltip_html(t("settings.asnearest_list_max_rows_tooltip")))
        self.lbl_asnearest_list_max_rows.setToolTip(self.sp_asnearest_list_max_rows.toolTip())
        self.sp_asnearest_list_max_rows.setFixedWidth(_udp_port_field_w)

        self.chk_map_aswatch_only_asnearest_list = QCheckBox(
            t("settings.map_aswatch_only_asnearest_list")
        )
        self.chk_map_aswatch_only_asnearest_list.setChecked(
            bool(_ui0.get("map_aswatch_only_asnearest_list", False))
        )
        self.chk_map_aswatch_only_asnearest_list.setToolTip(
            format_tooltip_html(t("settings.map_aswatch_only_asnearest_list_tooltip"))
        )
        self.chk_map_aswatch_cluster = QCheckBox(t("settings.map_aswatch_cluster_enabled"))
        self.chk_map_aswatch_cluster.setChecked(bool(_ui0.get("map_aswatch_cluster_enabled", True)))
        self.chk_map_aswatch_cluster.setToolTip(
            format_tooltip_html(t("settings.map_aswatch_cluster_tooltip"))
        )

        self.chk_udp_pst = QCheckBox(t("settings.chk_udp_pst"))
        self.chk_udp_pst.setToolTip(tt("settings.chk_udp_pst_tooltip"))
        self.chk_udp_pst.setChecked(bool(_ui0.get("udp_pst_enabled", True)))
        self.ed_udp_pst_listen = QLineEdit()
        self.ed_udp_pst_listen.setText(str(_ui0.get("udp_pst_listen_host", "0.0.0.0")))
        self.ed_udp_pst_listen.setFixedWidth(_udp_ip_field_w)
        self.ed_udp_pst_listen.setToolTip(tt("settings.udp_pst_listen_tooltip"))
        self.sp_udp_pst_port = QSpinBox()
        self.sp_udp_pst_port.setRange(1, 65534)
        self.sp_udp_pst_port.setValue(int(_ui0.get("udp_pst_port", 12000)))
        self.sp_udp_pst_port.setFixedWidth(_udp_port_field_w)
        self.sp_udp_pst_port.setToolTip(tt("settings.udp_pst_port_tooltip"))
        self.ed_udp_pst_send_host = QLineEdit()
        _pst_auto_host = ipv4_subnet_broadcast_default()
        _pst_saved = str(_ui0.get("udp_pst_send_host", "")).strip()
        self.ed_udp_pst_send_host.setText(_pst_saved if _pst_saved else _pst_auto_host)
        self.ed_udp_pst_send_host.setPlaceholderText(_pst_auto_host)
        self.ed_udp_pst_send_host.setFixedWidth(_udp_target_field_w)
        self.ed_udp_pst_send_host.setToolTip(tt("settings.udp_pst_send_host_tooltip"))

        _lbl_ip = t("settings.udp_listen_ip_label")
        _lbl_port = t("settings.udp_listen_port_label")
        _lbl_tgt = t("settings.udp_pst_target_short_label")

        def _asn_full_row(lbl: QLabel, sp: QSpinBox) -> QWidget:
            """Eine Zeile: Beschriftung links (wie Checkbox-Zeilen), Wert rechts."""
            row = QWidget()
            lay = QHBoxLayout(row)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(12)
            lay.addWidget(lbl, 1)
            lay.addWidget(sp, 0, Qt.AlignmentFlag.AlignRight)
            return row

        grid_ext_udp = QGridLayout()
        grid_ext_udp.setContentsMargins(0, 0, 0, 0)
        grid_ext_udp.setHorizontalSpacing(8)
        grid_ext_udp.setVerticalSpacing(6)
        grid_ext_udp.setColumnMinimumWidth(0, _udp_chk_col_w)

        grid_ext_udp.addWidget(self.chk_udp_ucxlog, 0, 0, alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        grid_ext_udp.addWidget(QLabel(_lbl_ip), 0, 1, alignment=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        grid_ext_udp.addWidget(self.ed_udp_ucxlog_listen, 0, 2, alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        grid_ext_udp.addWidget(QLabel(_lbl_port), 0, 3, alignment=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        grid_ext_udp.addWidget(self.sp_udp_ucxlog_port, 0, 4, alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        grid_ext_udp.addWidget(self.chk_aswatch_udp, 1, 0, alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        grid_ext_udp.addWidget(QLabel(_lbl_ip), 1, 1, alignment=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        grid_ext_udp.addWidget(self.ed_aswatch_udp_listen, 1, 2, alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        grid_ext_udp.addWidget(QLabel(_lbl_port), 1, 3, alignment=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        grid_ext_udp.addWidget(self.sp_aswatch_udp_port, 1, 4, alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        udp_ext_block_w = QWidget()
        udp_ext_block_w.setLayout(grid_ext_udp)

        grid_map_airscout = QGridLayout()
        grid_map_airscout.setContentsMargins(0, 0, 0, 0)
        grid_map_airscout.setHorizontalSpacing(8)
        grid_map_airscout.setVerticalSpacing(6)
        grid_map_airscout.setColumnMinimumWidth(0, _udp_chk_col_w)

        grid_map_airscout.addWidget(
            self.chk_aswatch_aircraft,
            0,
            0,
            1,
            5,
            alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        )

        grid_map_airscout.addWidget(_asn_full_row(self.lbl_asnearest_min_score, self.sp_asnearest_min_score), 1, 0, 1, 5)
        grid_map_airscout.addWidget(_asn_full_row(self.lbl_asnearest_geom_min, self.sp_asnearest_geom_min), 2, 0, 1, 5)
        grid_map_airscout.addWidget(_asn_full_row(self.lbl_asnearest_list_max_min, self.sp_asnearest_list_max_min), 3, 0, 1, 5)
        grid_map_airscout.addWidget(_asn_full_row(self.lbl_asnearest_list_max_rows, self.sp_asnearest_list_max_rows), 4, 0, 1, 5)
        grid_map_airscout.addWidget(
            self.chk_map_aswatch_only_asnearest_list,
            5,
            0,
            1,
            5,
            alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        )
        grid_map_airscout.addWidget(
            self.chk_map_aswatch_cluster,
            6,
            0,
            1,
            5,
            alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        )

        map_airscout_block_w = QWidget()
        map_airscout_block_w.setLayout(grid_map_airscout)

        gb_external_programs = QGroupBox(t("settings.group_external_programs"))
        _vl_ext_box = QVBoxLayout(gb_external_programs)
        _vl_ext_box.addWidget(udp_ext_block_w)

        gb_map_airscout = QGroupBox(t("settings.group_map_airscout"))
        _vl_map_as = QVBoxLayout(gb_map_airscout)
        _vl_map_as.addWidget(map_airscout_block_w)

        pg_external_programs = QWidget()
        vl_external_programs = QVBoxLayout(pg_external_programs)
        vl_external_programs.setContentsMargins(0, 0, 0, 0)
        vl_external_programs.setSpacing(10)
        vl_external_programs.addWidget(gb_external_programs)
        vl_external_programs.addWidget(gb_map_airscout)
        vl_external_programs.addStretch(1)

        grid_pst = QGridLayout()
        grid_pst.setContentsMargins(0, 0, 0, 0)
        grid_pst.setHorizontalSpacing(8)
        grid_pst.setVerticalSpacing(6)
        grid_pst.setColumnMinimumWidth(0, _udp_chk_col_w)
        # PST: Checkbox über zwei Zeilen; Ziel-IP eine Zeile unter Listen-IP (unter „IP:“)
        grid_pst.addWidget(self.chk_udp_pst, 0, 0, 2, 1, alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        grid_pst.addWidget(QLabel(_lbl_ip), 0, 1, alignment=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        grid_pst.addWidget(self.ed_udp_pst_listen, 0, 2, alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        grid_pst.addWidget(QLabel(_lbl_port), 0, 3, alignment=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        grid_pst.addWidget(self.sp_udp_pst_port, 0, 4, alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        grid_pst.addWidget(QLabel(_lbl_tgt), 1, 1, alignment=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        grid_pst.addWidget(self.ed_udp_pst_send_host, 1, 2, alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        udp_pst_block_w = QWidget()
        udp_pst_block_w.setLayout(grid_pst)

        gb_udp_pst_connection = QGroupBox(t("settings.group_udp_pst_connection"))
        _vl_pst_box = QVBoxLayout(gb_udp_pst_connection)
        _vl_pst_box.addWidget(w_spid_tcp_pst)
        _vl_pst_box.addWidget(udp_pst_block_w)

        pg_links = QWidget()
        vl_links = QVBoxLayout(pg_links)
        vl_links.setContentsMargins(0, 0, 0, 0)
        vl_links.setSpacing(10)
        vl_links.addWidget(gb_master_rotor)
        vl_links.addWidget(gb_udp_pst_connection)
        vl_links.addWidget(gb_bus_connection)
        vl_links.addWidget(gb_axes)
        vl_links.addStretch(1)

        # SPID BIG-RAS (TCP) und UDP PST-Rotator schließen sich aus; beide aus ist erlaubt.
        if self.chk_pst_enabled.isChecked() and self.chk_udp_pst.isChecked():
            self.chk_udp_pst.setChecked(False)
        self.chk_pst_enabled.stateChanged.connect(self._on_spid_vs_udp_pst_exclusive)
        self.chk_udp_pst.stateChanged.connect(self._on_udp_pst_vs_spid_exclusive)

        def _sync_aswatch_aircraft_row():
            en = self.chk_aswatch_udp.isChecked()
            self.chk_map_aswatch_only_asnearest_list.setEnabled(en)
            self.chk_map_aswatch_cluster.setEnabled(en)
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
        _spec_delcal = _all_cmd_spec.get("DELCAL")
        _spec_getcalvalid = _all_cmd_spec.get("GETCALVALID")
        _spec_getcalstate = _all_cmd_spec.get("GETCALSTATE")

        self.btn_cal_start = QPushButton(t("cmd.btn_start_cal"))
        self.btn_cal_start.setAutoDefault(False)
        self.btn_cal_start.setDefault(False)
        self.btn_cal_del = QPushButton(t("settings.cal_btn_reset_ref"))
        self.btn_cal_del.setAutoDefault(False)
        self.btn_cal_del.setDefault(False)
        self.btn_cal_reset = QPushButton(t("settings.cal_btn_reset_log"))
        self.btn_cal_reset.setAutoDefault(False)
        self.btn_cal_reset.setDefault(False)
        if _spec_setcal:
            self.btn_cal_start.setToolTip(format_cmd_tooltip(_spec_setcal))
        if _spec_delcal:
            self.btn_cal_del.setToolTip(format_cmd_tooltip(_spec_delcal))
        if _spec_clrstat:
            self.btn_cal_reset.setToolTip(format_cmd_tooltip(_spec_clrstat))

        _ui_hm = cfg.get("ui", {})

        pg_stats = QWidget()
        vl_st = QVBoxLayout(pg_stats)
        _st_pad = px_to_dip(self, 5)
        vl_st.setContentsMargins(_st_pad, _st_pad, _st_pad, _st_pad)
        vl_st.setSpacing(10)
        self.gb_strom_cal = QGroupBox(t("settings.cal_label"))
        # Gleiches Layout wie Last-Heatmap-Gruppen (Standard-QGroupBox, kein Sonder-Stylesheet).
        fl_strom_cal = QFormLayout(self.gb_strom_cal)
        self.btn_cal_help = QPushButton(t("settings.stats_help_btn"))
        self.btn_cal_help.setAutoDefault(False)
        self.btn_cal_help.setDefault(False)
        self.btn_cal_help.setToolTip(tt("settings.stats_help_title"))
        self.btn_cal_help.clicked.connect(self._show_stats_calibration_help)
        # Sichtbarer Kreis wie Controller-LED (12×12 dip, radius 6): Led malt mit 1px-Inset kleiner.
        _led_cal_d = px_to_dip(self, 12) + px_to_dip(self, 2)
        self._led_cal_valid = Led(diameter=max(8, _led_cal_d))
        if _spec_getcalvalid:
            self._led_cal_valid.setToolTip(format_cmd_tooltip(_spec_getcalvalid))
        cal_btns = QHBoxLayout()
        cal_btns.setContentsMargins(0, 0, 0, 0)
        cal_btns.addWidget(self.btn_cal_start)
        cal_btns.addWidget(self.btn_cal_del)
        cal_btns.addWidget(self.btn_cal_reset)
        cal_btns.addWidget(self.btn_cal_help)
        cal_btns.addStretch(1)
        cal_btns.addWidget(self._led_cal_valid, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        _w_cal_btns = QWidget()
        _w_cal_btns.setLayout(cal_btns)
        fl_strom_cal.addRow(_w_cal_btns)
        self._pb_cal = QProgressBar()
        self._pb_cal.setRange(0, 100)
        self._pb_cal.setValue(0)
        self._pb_cal.setVisible(False)
        self._pb_cal.setTextVisible(True)
        self._pb_cal.setFormat("%p%")
        self._pb_cal.setMinimumHeight(px_to_dip(self, 14))
        if _spec_getcalstate:
            self._pb_cal.setToolTip(format_cmd_tooltip(_spec_getcalstate))
        fl_strom_cal.addRow(self._pb_cal)
        vl_st.addWidget(self.gb_strom_cal)

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
        self.sp_thr_blue_az.setToolTip(tt("settings.stats_heatmap_thr_blue_tooltip"))
        self.sp_norm_min_az.setToolTip(tt("settings.stats_heatmap_norm_tooltip"))
        self.sp_norm_max_az.setToolTip(tt("settings.stats_heatmap_norm_tooltip"))
        self.sp_thr_red_az.setToolTip(tt("settings.stats_heatmap_thr_red_tooltip"))
        fl_hm_az.addRow(t("settings.stats_heatmap_thr_blue"), self.sp_thr_blue_az)
        fl_hm_az.addRow(t("settings.stats_heatmap_norm_min"), self.sp_norm_min_az)
        fl_hm_az.addRow(t("settings.stats_heatmap_norm_max"), self.sp_norm_max_az)
        fl_hm_az.addRow(t("settings.stats_heatmap_thr_red"), self.sp_thr_red_az)
        self.btn_apply_cal_az = QPushButton(t("settings.stats_apply_from_cal"))
        self.btn_apply_cal_az.setToolTip(tt("settings.stats_apply_from_cal_tooltip"))
        fl_hm_az.addRow(self.btn_apply_cal_az)
        vl_st.addWidget(gb_hm_az)

        self.gb_strom_cal_el = QGroupBox(t("settings.cal_label_el"))
        fl_strom_cal_el = QFormLayout(self.gb_strom_cal_el)
        self.btn_cal_start_el = QPushButton(t("cmd.btn_start_cal"))
        self.btn_cal_start_el.setAutoDefault(False)
        self.btn_cal_start_el.setDefault(False)
        self.btn_cal_del_el = QPushButton(t("settings.cal_btn_reset_ref"))
        self.btn_cal_del_el.setAutoDefault(False)
        self.btn_cal_del_el.setDefault(False)
        self.btn_cal_reset_el = QPushButton(t("settings.cal_btn_reset_log"))
        self.btn_cal_reset_el.setAutoDefault(False)
        self.btn_cal_reset_el.setDefault(False)
        if _spec_setcal:
            self.btn_cal_start_el.setToolTip(format_cmd_tooltip(_spec_setcal))
        if _spec_delcal:
            self.btn_cal_del_el.setToolTip(format_cmd_tooltip(_spec_delcal))
        if _spec_clrstat:
            self.btn_cal_reset_el.setToolTip(format_cmd_tooltip(_spec_clrstat))
        self.btn_cal_help_el = QPushButton(t("settings.stats_help_btn"))
        self.btn_cal_help_el.setAutoDefault(False)
        self.btn_cal_help_el.setDefault(False)
        self.btn_cal_help_el.setToolTip(tt("settings.stats_help_title"))
        self.btn_cal_help_el.clicked.connect(self._show_stats_calibration_help)
        self._led_cal_valid_el = Led(diameter=max(8, _led_cal_d))
        if _spec_getcalvalid:
            self._led_cal_valid_el.setToolTip(format_cmd_tooltip(_spec_getcalvalid))
        cal_btns_el = QHBoxLayout()
        cal_btns_el.setContentsMargins(0, 0, 0, 0)
        cal_btns_el.addWidget(self.btn_cal_start_el)
        cal_btns_el.addWidget(self.btn_cal_del_el)
        cal_btns_el.addWidget(self.btn_cal_reset_el)
        cal_btns_el.addWidget(self.btn_cal_help_el)
        cal_btns_el.addStretch(1)
        cal_btns_el.addWidget(
            self._led_cal_valid_el, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        _w_cal_btns_el = QWidget()
        _w_cal_btns_el.setLayout(cal_btns_el)
        fl_strom_cal_el.addRow(_w_cal_btns_el)
        self._pb_cal_el = QProgressBar()
        self._pb_cal_el.setRange(0, 100)
        self._pb_cal_el.setValue(0)
        self._pb_cal_el.setVisible(False)
        self._pb_cal_el.setTextVisible(True)
        self._pb_cal_el.setFormat("%p%")
        self._pb_cal_el.setMinimumHeight(px_to_dip(self, 14))
        if _spec_getcalstate:
            self._pb_cal_el.setToolTip(format_cmd_tooltip(_spec_getcalstate))
        fl_strom_cal_el.addRow(self._pb_cal_el)
        vl_st.addWidget(self.gb_strom_cal_el)

        self.gb_hm_el = QGroupBox(t("settings.stats_heatmap_group_el"))
        fl_hm_el = QFormLayout(self.gb_hm_el)
        self.chk_heatmap_custom_el = QCheckBox(t("settings.stats_heatmap_custom"))
        self.chk_heatmap_custom_el.setChecked(bool(_ui_hm.get("heatmap_custom_el", False)))
        fl_hm_el.addRow(self.chk_heatmap_custom_el)
        self.sp_thr_blue_el = _hm_spin(int(_ui_hm.get("heatmap_thr_blue_el", 0)))
        self.sp_norm_min_el = _hm_spin(int(_ui_hm.get("heatmap_norm_min_el", 0)))
        self.sp_norm_max_el = _hm_spin(int(_ui_hm.get("heatmap_norm_max_el", 0)))
        self.sp_thr_red_el = _hm_spin(int(_ui_hm.get("heatmap_thr_red_el", 0)))
        self.sp_thr_blue_el.setToolTip(tt("settings.stats_heatmap_thr_blue_tooltip"))
        self.sp_norm_min_el.setToolTip(tt("settings.stats_heatmap_norm_tooltip"))
        self.sp_norm_max_el.setToolTip(tt("settings.stats_heatmap_norm_tooltip"))
        self.sp_thr_red_el.setToolTip(tt("settings.stats_heatmap_thr_red_tooltip"))
        fl_hm_el.addRow(t("settings.stats_heatmap_thr_blue"), self.sp_thr_blue_el)
        fl_hm_el.addRow(t("settings.stats_heatmap_norm_min"), self.sp_norm_min_el)
        fl_hm_el.addRow(t("settings.stats_heatmap_norm_max"), self.sp_norm_max_el)
        fl_hm_el.addRow(t("settings.stats_heatmap_thr_red"), self.sp_thr_red_el)
        self.btn_apply_cal_el = QPushButton(t("settings.stats_apply_from_cal"))
        self.btn_apply_cal_el.setToolTip(tt("settings.stats_apply_from_cal_tooltip"))
        fl_hm_el.addRow(self.btn_apply_cal_el)
        vl_st.addWidget(self.gb_hm_el)
        vl_st.addStretch(1)

        _chw = cfg.setdefault("controller_hw", {})
        _ui_names = cfg.setdefault("ui", {})
        _antenna_names_cfg = list(
            _ui_names.get(
                "antenna_names",
                [t("settings.antenna_1"), t("settings.antenna_2"), t("settings.antenna_3")],
            )
        )
        while len(_antenna_names_cfg) < 3:
            _antenna_names_cfg.append(f"Antenne {len(_antenna_names_cfg) + 1}")

        pg_controller = QWidget()
        vl_ctrl = QVBoxLayout(pg_controller)
        _ctrl_pad = px_to_dip(self, 5)
        vl_ctrl.setContentsMargins(_ctrl_pad, _ctrl_pad, _ctrl_pad, _ctrl_pad)
        vl_ctrl.setSpacing(10)
        self.chk_hw_controller_enabled = QCheckBox(t("settings.controller_hw_enable"))
        self.chk_hw_controller_enabled.setChecked(bool(_chw.get("enabled", True)))
        self.chk_hw_controller_enabled.setToolTip(tt("settings.controller_hw_enable_tooltip"))
        self.chk_hw_controller_enabled.toggled.connect(self._on_hw_controller_toggled)
        vl_ctrl.addWidget(self.chk_hw_controller_enabled)
        self.gb_controller = QGroupBox(t("settings.controller_group"))
        fl_ctrl = QFormLayout(self.gb_controller)
        self.sp_controller_id = QSpinBox()
        self.sp_controller_id.setRange(0, 245)
        try:
            self.sp_controller_id.setValue(int(_chw.get("cont_id", 2)))
        except (TypeError, ValueError):
            self.sp_controller_id.setValue(2)
        self.sp_controller_id.setToolTip(tt("settings.controller_id_tooltip"))
        _row_cont_id = QWidget()
        _lay_cont_id = QHBoxLayout(_row_cont_id)
        _lay_cont_id.setContentsMargins(0, 0, 0, 0)
        _lay_cont_id.setSpacing(px_to_dip(self, 8))
        _lay_cont_id.addWidget(self.sp_controller_id, 0)
        self.btn_setconidf = QPushButton(t("settings.controller_btn_setconidf"))
        self.btn_setconidf.setAutoDefault(False)
        self.btn_setconidf.setDefault(False)
        self.btn_setconidf.setToolTip(tt("settings.controller_setconidf_tooltip"))
        self.btn_setconidf.clicked.connect(self._on_broadcast_setconidf)
        _lay_cont_id.addWidget(self.btn_setconidf, 0)
        self._lbl_controller_led = QLabel()
        self._lbl_controller_led.setObjectName("controllerReadLed")
        self._lbl_controller_led.setToolTip(tt("settings.controller_led_tooltip"))
        _led = px_to_dip(self, 12)
        self._lbl_controller_led.setFixedSize(_led, _led)
        self._set_controller_led_ok(False)
        _lay_cont_id.addWidget(self._lbl_controller_led, 0, Qt.AlignmentFlag.AlignVCenter)
        self._lbl_controller_wait = QLabel(t("settings.controller_wait"))
        self._lbl_controller_wait.setVisible(False)
        self._lbl_controller_wait.setToolTip(tt("settings.controller_wait_tooltip"))
        _lay_cont_id.addWidget(self._lbl_controller_wait, 0, Qt.AlignmentFlag.AlignVCenter)
        _lay_cont_id.addStretch(1)
        fl_ctrl.addRow(t("settings.controller_id"), _row_cont_id)
        self._controller_name_dirty = [False, False, False]
        self._controller_pwm_dirty = [False, False]
        self.sp_cont_pwm_slow = QSpinBox()
        self.sp_cont_pwm_slow.setRange(0, 100)
        try:
            self.sp_cont_pwm_slow.setValue(int(_chw.get("slow_pwm", 30)))
        except (TypeError, ValueError):
            self.sp_cont_pwm_slow.setValue(30)
        self.sp_cont_pwm_slow.setToolTip(tt("settings.controller_pwm_tooltip"))
        self.sp_cont_pwm_fast = QSpinBox()
        self.sp_cont_pwm_fast.setRange(0, 100)
        try:
            self.sp_cont_pwm_fast.setValue(int(_chw.get("fast_pwm", 80)))
        except (TypeError, ValueError):
            self.sp_cont_pwm_fast.setValue(80)
        self.sp_cont_pwm_fast.setToolTip(tt("settings.controller_pwm_tooltip"))
        self.sp_cont_pwm_slow.valueChanged.connect(lambda: self._mark_pwm_dirty(0))
        self.sp_cont_pwm_fast.valueChanged.connect(lambda: self._mark_pwm_dirty(1))
        fl_ctrl.addRow(t("settings.controller_pwm_slow"), self.sp_cont_pwm_slow)
        fl_ctrl.addRow(t("settings.controller_pwm_fast"), self.sp_cont_pwm_fast)
        self.chk_cont_wind_anemo = QCheckBox(t("settings.controller_wind_anemo"))
        self.chk_cont_wind_anemo.setChecked(bool(_chw.get("wind_anemometer", False)))
        self.chk_cont_wind_anemo.setToolTip(tt("settings.controller_wind_anemo_tooltip"))
        self.chk_cont_wind_anemo.toggled.connect(self._mark_anemo_dirty)
        self.chk_cont_wind_anemo.toggled.connect(self._update_wind_dir_display_row_visibility)
        fl_ctrl.addRow(self.chk_cont_wind_anemo)
        self.cb_cont_encoder_delta = QComboBox()
        self.cb_cont_encoder_delta.addItem(t("settings.controller_encoder_delta_0_1"), 1)
        self.cb_cont_encoder_delta.addItem(t("settings.controller_encoder_delta_1"), 10)
        try:
            _ed = int(_chw.get("encoder_delta", 10))
        except (TypeError, ValueError):
            _ed = 10
        if _ed not in (1, 10):
            _ed = 10
        self.cb_cont_encoder_delta.setCurrentIndex(0 if _ed == 1 else 1)
        self.cb_cont_encoder_delta.setToolTip(tt("settings.controller_encoder_delta_tooltip"))
        self.cb_cont_encoder_delta.currentIndexChanged.connect(self._mark_delta_dirty)
        fl_ctrl.addRow(t("settings.controller_encoder_delta"), self.cb_cont_encoder_delta)
        self.sp_cont_beep_freq = QSpinBox()
        self.sp_cont_beep_freq.setRange(500, 4000)
        try:
            self.sp_cont_beep_freq.setValue(
                max(500, min(4000, int(_chw.get("speaker_freq_hz", 1000))))
            )
        except (TypeError, ValueError):
            self.sp_cont_beep_freq.setValue(1000)
        self.sp_cont_beep_freq.setToolTip(tt("settings.controller_beep_freq_tooltip"))
        self.sp_cont_beep_vol = QSpinBox()
        self.sp_cont_beep_vol.setRange(0, 50)
        try:
            self.sp_cont_beep_vol.setValue(max(0, min(50, int(_chw.get("speaker_volume", 50)))))
        except (TypeError, ValueError):
            self.sp_cont_beep_vol.setValue(50)
        self.sp_cont_beep_vol.setToolTip(tt("settings.controller_beep_volume_tooltip"))
        self._controller_beep_dirty = [False, False]
        self._controller_anemo_dirty = False
        self._controller_delta_dirty = False
        self._controller_cha_dirty = False
        self.sp_cont_beep_freq.valueChanged.connect(lambda: self._mark_beep_dirty(0))
        self.sp_cont_beep_vol.valueChanged.connect(lambda: self._mark_beep_dirty(1))
        fl_ctrl.addRow(t("settings.controller_beep_freq"), self.sp_cont_beep_freq)
        fl_ctrl.addRow(t("settings.controller_beep_volume"), self.sp_cont_beep_vol)
        _w_conled = QWidget()
        _hl_conled = QHBoxLayout(_w_conled)
        _hl_conled.setContentsMargins(0, 0, 0, 0)
        self.sl_cont_display_brightness = QSlider(Qt.Orientation.Horizontal)
        self.sl_cont_display_brightness.setRange(0, 100)
        try:
            _db = int(_chw.get("display_brightness_pct", 100))
        except (TypeError, ValueError):
            _db = 100
        self.sl_cont_display_brightness.setValue(max(0, min(100, _db)))
        self.sl_cont_display_brightness.setToolTip(tt("settings.controller_display_brightness_tooltip"))
        self.lbl_cont_display_brightness = QLabel()
        self.lbl_cont_display_brightness.setMinimumWidth(40)
        self._update_conled_brightness_label()
        self.sl_cont_display_brightness.valueChanged.connect(self._on_conled_brightness_value_changed)
        self.sl_cont_display_brightness.sliderReleased.connect(self._on_conled_brightness_released)
        _hl_conled.addWidget(self.sl_cont_display_brightness, 1)
        _hl_conled.addWidget(self.lbl_cont_display_brightness)
        fl_ctrl.addRow(t("settings.controller_display_brightness"), _w_conled)
        vl_ctrl.addWidget(self.gb_controller)
        vl_ctrl.addStretch(1)
        self._apply_controller_enabled_ui()

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
        self._lbl_wind_dir_display = QLabel(t("settings.wind_dir_display"))
        self._lbl_wind_dir_display.setToolTip(tt("settings.wind_dir_display_tooltip"))
        self.cb_wind_dir_display.setToolTip(tt("settings.wind_dir_display_tooltip"))
        self._gb_wind_dir_display = QGroupBox(t("settings.group_wind_dir_display"))
        _fl_wind_dir = QFormLayout(self._gb_wind_dir_display)
        _fl_wind_dir.addRow(self._lbl_wind_dir_display, self.cb_wind_dir_display)
        self._update_wind_dir_display_row_visibility()

        self.cb_language = QComboBox()
        self.cb_language.addItem("Deutsch", "de")
        self.cb_language.addItem("English", "en")
        cur_lang = str(cfg.get("ui", {}).get("language", "de") or "de").strip().lower()
        lang_idx = self.cb_language.findData(cur_lang)
        if lang_idx >= 0:
            self.cb_language.setCurrentIndex(lang_idx)
        self.cb_language.setToolTip(tt("settings.language_label_tooltip"))
        fl_display.addRow(t("settings.language_label"), self.cb_language)
        form_ui.addRow(gb_display)

        gb_location = QGroupBox(t("settings.group_location"))
        fl_location = QFormLayout(gb_location)
        self.ed_location_lat = QDoubleSpinBox()
        self.ed_location_lat.setRange(-90.0, 90.0)
        self.ed_location_lat.setDecimals(6)
        self.ed_location_lat.setValue(float(cfg.get("ui", {}).get("location_lat", 49.502651)))
        self.ed_location_lat.setSuffix("°")
        self.ed_location_lat.setToolTip(tt("settings.location_lat_tooltip"))
        fl_location.addRow(t("settings.location_lat"), self.ed_location_lat)
        self.ed_location_lon = QDoubleSpinBox()
        self.ed_location_lon.setRange(-180.0, 180.0)
        self.ed_location_lon.setDecimals(6)
        self.ed_location_lon.setValue(float(cfg.get("ui", {}).get("location_lon", 8.375019)))
        self.ed_location_lon.setSuffix("°")
        self.ed_location_lon.setToolTip(tt("settings.location_lon_tooltip"))
        fl_location.addRow(t("settings.location_lon"), self.ed_location_lon)
        _row_locator = QWidget()
        _lay_locator = QHBoxLayout(_row_locator)
        _lay_locator.setContentsMargins(0, 0, 0, 0)
        _lay_locator.setSpacing(px_to_dip(self, 8))
        self.ed_location_locator = QLineEdit(str(cfg.get("ui", {}).get("location_locator", "") or ""))
        self.ed_location_locator.setMaxLength(10)
        self.ed_location_locator.setPlaceholderText("JO31jg")
        self.ed_location_locator.setToolTip(tt("settings.location_locator_tooltip"))
        _lay_locator.addWidget(self.ed_location_locator, 1)
        self.btn_locator_apply_coords = QPushButton(t("settings.btn_locator_apply_coords"))
        self.btn_locator_apply_coords.setAutoDefault(False)
        self.btn_locator_apply_coords.setDefault(False)
        self.btn_locator_apply_coords.setToolTip(tt("settings.btn_locator_apply_coords_tooltip"))
        self.btn_locator_apply_coords.clicked.connect(self._on_locator_apply_coords)
        _lay_locator.addWidget(self.btn_locator_apply_coords, 0)
        fl_location.addRow(t("settings.location_locator"), _row_locator)
        form_ui.addRow(gb_location)
        # --- Linke Spalte: Verbindung ---
        antenna_names = _antenna_names_cfg

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
            name_ed = QLineEdit(self._sanitize_controller_name(name_text))
            name_ed.setMaxLength(9)
            name_ed.setValidator(
                QRegularExpressionValidator(QRegularExpression(r"[^#:$]*"))
            )
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
            lbl_rng.setToolTip(tt("settings.tooltip_antenna_range"))
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

        self.gb_antenna_az = QGroupBox(t("settings.group_antenna_config"))
        form_az = QFormLayout(self.gb_antenna_az)
        form_az.setHorizontalSpacing(10)
        form_az.setVerticalSpacing(8)
        _row_ant_bus = QWidget()
        _lay_ant_bus = QHBoxLayout(_row_ant_bus)
        _lay_ant_bus.setContentsMargins(0, 0, 0, 0)
        _lay_ant_bus.setSpacing(px_to_dip(self, 8))
        self._lbl_antenna_names_led = QLabel()
        self._lbl_antenna_names_led.setObjectName("antennaNamesReadLed")
        self._lbl_antenna_names_led.setToolTip(tt("settings.antenna_names_led_tooltip"))
        _led_ant = px_to_dip(self, 12)
        self._lbl_antenna_names_led.setFixedSize(_led_ant, _led_ant)
        self._set_antenna_names_led_ok(False)
        _lay_ant_bus.addWidget(self._lbl_antenna_names_led, 0, Qt.AlignmentFlag.AlignVCenter)
        self._lbl_antenna_names_wait = QLabel(t("settings.controller_wait"))
        self._lbl_antenna_names_wait.setVisible(False)
        self._lbl_antenna_names_wait.setToolTip(tt("settings.controller_wait_tooltip"))
        _lay_ant_bus.addWidget(self._lbl_antenna_names_wait, 0, Qt.AlignmentFlag.AlignVCenter)
        _lay_ant_bus.addStretch(1)
        form_az.addRow(t("settings.antenna_names_bus_row"), _row_ant_bus)
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
        _tt_an = tt("settings.tooltip_antenna_name")
        _tt_off = tt("settings.tooltip_antenna_offset")
        _tt_ang = tt("settings.tooltip_antenna_angle")
        _tt_rng = tt("settings.tooltip_antenna_range")
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
        self.gb_antenna_misc = QGroupBox(t("settings.group_antenna_misc"))
        form_az_misc = QFormLayout(self.gb_antenna_misc)
        form_az_misc.setHorizontalSpacing(10)
        form_az_misc.setVerticalSpacing(8)
        self.sp_antenna_height = QDoubleSpinBox()
        self.sp_antenna_height.setRange(0.0, 500.0)
        self.sp_antenna_height.setDecimals(1)
        self.sp_antenna_height.setSingleStep(0.5)
        self.sp_antenna_height.setValue(float(cfg.get("ui", {}).get("antenna_height_m", 0.0)))
        self.sp_antenna_height.setSuffix(" m")
        self.sp_antenna_height.setToolTip(tt("settings.antenna_height_tooltip"))
        form_az_misc.addRow(t("settings.antenna_height"), self.sp_antenna_height)
        self.chk_cont_antenna_realign = QCheckBox(t("settings.antenna_realign_on_switch"))
        self.chk_cont_antenna_realign.setChecked(bool(_chw.get("antenna_realign_on_switch", False)))
        self.chk_cont_antenna_realign.setToolTip(tt("settings.antenna_realign_on_switch_tooltip"))
        self.chk_cont_antenna_realign.toggled.connect(self._mark_cha_dirty)
        form_az_misc.addRow(self.chk_cont_antenna_realign)
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
        self._wire_antenna_name_sync()

        def _scroll_page(inner: QWidget) -> QScrollArea:
            inner.setSizePolicy(
                QSizePolicy.Policy.Expanding,
                QSizePolicy.Policy.Preferred,
            )
            sc = _SettingsScrollArea()
            sc.setWidgetResizable(True)
            sc.setFrameShape(QFrame.Shape.NoFrame)
            sc.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            sc.setWidget(inner)
            sc.setSizePolicy(
                QSizePolicy.Policy.Expanding,
                QSizePolicy.Policy.Preferred,
            )
            return sc

        pg_ui = QWidget()
        vl_ui = QVBoxLayout(pg_ui)
        vl_ui.setContentsMargins(0, 0, 0, 0)
        vl_ui.addWidget(gb_ui)
        vl_ui.addStretch(1)

        pg_ant = QWidget()
        vl_ant = QVBoxLayout(pg_ant)
        vl_ant.setContentsMargins(0, 0, 0, 0)
        vl_ant.setSpacing(10)
        vl_ant.addWidget(self.gb_antenna_az)
        vl_ant.addWidget(self.gb_antenna_misc)
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
        self.sp_compass_om_sectors.setToolTip(tt("settings.compass_om_radar_sectors_tooltip"))
        fl_compass_om.addRow(t("settings.compass_om_radar_sectors"), self.sp_compass_om_sectors)
        vl_compass.addWidget(gb_compass_om)
        gb_compass_dwell = QGroupBox(t("settings.compass_dwell_group"))
        fl_compass_dwell = QFormLayout(gb_compass_dwell)
        self.sp_compass_dwell_sectors = QSpinBox()
        self.sp_compass_dwell_sectors.setRange(10, 100)
        self.sp_compass_dwell_sectors.setValue(_dwell_sec)
        self.sp_compass_dwell_sectors.setToolTip(tt("settings.compass_dwell_sectors_tooltip"))
        fl_compass_dwell.addRow(t("settings.compass_dwell_sectors"), self.sp_compass_dwell_sectors)
        self.sp_compass_dwell_minutes = QDoubleSpinBox()
        self.sp_compass_dwell_minutes.setRange(0.1, 240.0)
        self.sp_compass_dwell_minutes.setDecimals(1)
        self.sp_compass_dwell_minutes.setSingleStep(0.5)
        self.sp_compass_dwell_minutes.setValue(_dwell_min)
        self.sp_compass_dwell_minutes.setToolTip(tt("settings.compass_dwell_minutes_tooltip"))
        fl_compass_dwell.addRow(t("settings.compass_dwell_minutes"), self.sp_compass_dwell_minutes)
        vl_compass.addWidget(gb_compass_dwell)
        vl_compass.addWidget(self._gb_wind_dir_display)
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
            QSizePolicy.Policy.Preferred,
        )
        self._settings_nav.setUniformItemSizes(True)
        self._settings_stack = QStackedWidget()
        self._settings_stack.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        self._rig_bridge_tab = RigBridgeTab(
            self.cfg,
            self.rig_bridge_manager if self.rig_bridge_manager is not None else RigBridgeManager(self.cfg.get("rig_bridge", {}), self.logbuf.write),
            self.save_cfg_cb,
            self,
        )
        self._settings_stack.addWidget(_scroll_page(pg_ui))
        self._settings_stack.addWidget(_scroll_page(pg_links))
        self._settings_stack.addWidget(_scroll_page(pg_external_programs))
        self._settings_stack.addWidget(_scroll_page(pg_ant))
        self._settings_stack.addWidget(_scroll_page(pg_compass))
        self._settings_stack.addWidget(_scroll_page(pg_stats))
        self._settings_stack.addWidget(_scroll_page(pg_controller))
        self._settings_stack.addWidget(_scroll_page(self._rig_bridge_tab))
        self._shortcuts_tab = ShortcutsTab(self.cfg, self)
        self._settings_stack.addWidget(_scroll_page(self._shortcuts_tab))
        self._tab_antenna_index = 3
        self._tab_statistics_index = 5
        self._tab_controller_index = 6
        self._tab_rig_bridge_index = 7
        self._tab_shortcuts_index = 8
        self._calvalid_timer = QTimer(self)
        self._calvalid_timer.setInterval(5000)
        self._calvalid_timer.timeout.connect(self._poll_getcalvalid_once)
        self._cal_progress_timer = QTimer(self)
        self._cal_progress_timer.setInterval(1000)
        self._cal_progress_timer.timeout.connect(self._tick_cal_progress_poll)
        self._prev_cal_state_for_led_az: int | None = None
        self._prev_cal_state_for_led_el: int | None = None
        self._cal_poll_deadline_ts_az: float = 0.0
        self._cal_poll_deadline_ts_el: float = 0.0
        self._prev_cal_prog_st_az: int = -1
        self._prev_cal_prog_st_el: int = -1
        self._cal_led_want_blink_az: bool = False
        self._cal_led_want_blink_el: bool = False
        self._prev_cal_led_blink_az: bool = False
        self._prev_cal_led_blink_el: bool = False
        for _lbl in (
            t("settings.group_ui"),
            t("settings.tab_connections"),
            t("settings.tab_external_programs"),
            t("settings.tab_antenna"),
            t("settings.tab_compass"),
            t("settings.tab_statistics"),
            t("settings.tab_controller"),
            "Rig-Bridge",
            t("settings.tab_shortcuts"),
        ):
            self._settings_nav.addItem(_lbl)
        self._settings_nav.currentRowChanged.connect(self._on_settings_nav_changed)
        self._settings_nav.setCurrentRow(0)

        self._settings_nav_wrap = QWidget()
        _nav_lay = QVBoxLayout(self._settings_nav_wrap)
        _nav_lay.setContentsMargins(0, 0, 0, 0)
        _nav_lay.addWidget(self._settings_nav)
        self._apply_settings_nav_style()

        self._tabs_body = QWidget()
        self._tabs_body.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        _tabs_h = QHBoxLayout(self._tabs_body)
        _tabs_h.setContentsMargins(0, 0, 0, 0)
        _tabs_h.setSpacing(px_to_dip(self, 10))
        _tabs_h.setAlignment(Qt.AlignmentFlag.AlignTop)
        _tabs_h.addWidget(self._settings_nav_wrap, 0)
        _tabs_h.addWidget(self._settings_stack, 1)
        self._settings_stack.setMinimumSize(0, 0)
        self._tabs_body.setMinimumSize(0, 0)
        main.addWidget(self._tabs_body, 1)

        self.chk_enable_az.installEventFilter(self)
        self.chk_enable_el.installEventFilter(self)
        self.chk_enable_az.stateChanged.connect(self._update_antenna_visibility)
        self.chk_enable_el.stateChanged.connect(self._update_antenna_visibility)
        self.chk_enable_el.stateChanged.connect(self._shortcuts_tab.refresh_el_visibility)
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
        # Hardware-Verbindung: Start CAL / Reset CAL / Reset log CAL; Antennennamen (HW-Controller)
        self._hw_link_timer = QTimer(self)
        self._hw_link_timer.setInterval(500)
        self._hw_link_timer.timeout.connect(self._tick_hw_link_state)

        self.btn_com_refresh.clicked.connect(self._refresh_com_ports)
        self._refresh_com_ports(select=cfg["hardware_link"].get("com_port", ""))

        self.lbl_status = QLabel("")
        self.lbl_status.setStyleSheet("color: gray; font-style: italic;")
        self.lbl_status.setWordWrap(True)
        self.btn_cal_start.clicked.connect(self._on_settings_cal_start_az)
        self.btn_cal_reset.clicked.connect(self._on_settings_cal_reset_az)
        self.btn_cal_del.clicked.connect(self._on_settings_cal_delcal_az)
        self.btn_cal_start_el.clicked.connect(self._on_settings_cal_start_el)
        self.btn_cal_reset_el.clicked.connect(self._on_settings_cal_reset_el)
        self.btn_cal_del_el.clicked.connect(self._on_settings_cal_delcal_el)
        self.btn_apply_cal_az.clicked.connect(self._on_apply_cal_heatmap_az)
        self.btn_apply_cal_el.clicked.connect(self._on_apply_cal_heatmap_el)
        QTimer.singleShot(0, self._update_strom_cal_buttons_enabled)
        btnrow = QHBoxLayout()
        btnrow.addWidget(self.lbl_status, 1)
        btn_save_close = QPushButton(t("settings.btn_save_close"))
        btn_save_close.clicked.connect(self._save_and_close)
        btnrow.addWidget(btn_save_close)
        main.addLayout(btnrow)

    def _apply_settings_window_open_size(self) -> None:
        """Beim Öffnen: Zielhöhe 710 Referenzpixel (skaliert), Breite unverändert."""
        w = self.width()
        if w <= 0:
            w = px_to_dip(self, self._settings_base_width_dip)
        h = px_to_dip(self, self._settings_open_height_dip)
        self.resize(w, h)

    def minimumSizeHint(self) -> QSize:
        w = px_to_dip(self, self._settings_base_width_dip)
        h = px_to_dip(self, self._settings_min_height_dip)
        return QSize(w, h)

    def sizeHint(self) -> QSize:
        w = px_to_dip(self, self._settings_base_width_dip)
        h = px_to_dip(self, self._settings_open_height_dip)
        return QSize(w, h)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        if hasattr(self.ctrl, "set_settings_window_open"):
            self.ctrl.set_settings_window_open(True)
        if hasattr(self.ctrl, "request_immediate_stats"):
            self.ctrl.request_immediate_stats()
        self._antenna_giveup_done = False
        self._update_antenna_visibility()
        self._shortcuts_tab.refresh_el_visibility()
        self._update_antenna_offset_enabled()
        self._update_status_on_open()
        self._request_antenna_offsets_if_needed()
        self._antenna_refresh_timer.start()
        self._antenna_request_timer.start()
        self._antenna_giveup_timer.start()
        self._hw_link_timer.start()
        self._update_strom_cal_buttons_enabled()
        if self._settings_nav.currentRow() == getattr(self, "_tab_statistics_index", -1):
            self._start_calvalid_timer()
        # Snapshots: nur geänderte Werte gehen auf den Bus (SETANTOFF / SETCON* …)
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
        # Vergleichsbasis für SETCON* beim Speichern (sonst snap=None → kein Schreiben)
        self._snapshot_controller = self._controller_snapshot_from_ui()
        # Antennennamen vom Hardware-Controller (wie Tab „Controller“)
        QTimer.singleShot(0, self._load_controller_antenna_names_from_bus)
        self._apply_settings_window_open_size()
        QTimer.singleShot(0, self._apply_settings_window_open_size)

    def hideEvent(self, event: QHideEvent) -> None:
        """Minimieren: Polling-Flag zurück (closeEvent kommt bei Minimize nicht)."""
        self._antenna_refresh_timer.stop()
        self._antenna_request_timer.stop()
        self._antenna_giveup_timer.stop()
        self._hw_link_timer.stop()
        self._stop_calvalid_timer()
        if hasattr(self.ctrl, "set_settings_window_open"):
            self.ctrl.set_settings_window_open(False)
        super().hideEvent(event)

    def changeEvent(self, event: QEvent) -> None:
        """Minimize ohne hideEvent: Timer stoppen wie bei hideEvent."""
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange and self.isMinimized():
            self._antenna_refresh_timer.stop()
            self._antenna_request_timer.stop()
            self._antenna_giveup_timer.stop()
            self._hw_link_timer.stop()
            self._stop_calvalid_timer()
            if hasattr(self.ctrl, "set_settings_window_open"):
                self.ctrl.set_settings_window_open(False)

    def closeEvent(self, event: QCloseEvent) -> None:
        self._antenna_refresh_timer.stop()
        self._antenna_request_timer.stop()
        self._antenna_giveup_timer.stop()
        self._hw_link_timer.stop()
        self._stop_calvalid_timer()
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

    def _calvalid_tab_active(self) -> bool:
        return self._settings_nav.currentRow() == getattr(self, "_tab_statistics_index", -1)

    def _start_calvalid_timer(self) -> None:
        self._calvalid_timer.start()
        self._cal_progress_timer.start()
        self._poll_getcalvalid_once()
        self._tick_cal_progress_poll()

    def _stop_calvalid_timer(self) -> None:
        self._calvalid_timer.stop()
        self._cal_progress_timer.stop()
        self._prev_cal_state_for_led_az = None
        self._prev_cal_state_for_led_el = None
        self._cal_poll_deadline_ts_az = 0.0
        self._cal_poll_deadline_ts_el = 0.0
        self._prev_cal_prog_st_az = -1
        self._prev_cal_prog_st_el = -1
        self._cal_led_want_blink_az = False
        self._cal_led_want_blink_el = False
        self._prev_cal_led_blink_az = False
        self._prev_cal_led_blink_el = False
        self._led_cal_valid.set_blinking_green(False)
        self._led_cal_valid_el.set_blinking_green(False)
        self._apply_cal_progress_bar_ui(self._pb_cal, 0, 0)
        self._apply_cal_progress_bar_ui(self._pb_cal_el, 0, 0)

    def _update_strom_cal_sections_visibility(self) -> None:
        """Stromkalibrierung und EL-Heatmap nur bei jeweils aktivierter Achse (Verbindung)."""
        self.gb_strom_cal.setVisible(self.chk_enable_az.isChecked())
        self.gb_strom_cal_el.setVisible(self.chk_enable_el.isChecked())
        self.gb_hm_el.setVisible(self.chk_enable_el.isChecked())
        self._update_strom_cal_buttons_enabled()

    def _update_strom_cal_buttons_enabled(self) -> None:
        """Start CAL / Reset CAL / Reset log CAL nur bei Hardware-Verbindung und aktiver Achse."""
        try:
            hw_on = bool(self.hw and self.hw.is_connected())
        except Exception:
            hw_on = False
        az_en = bool(self.chk_enable_az.isChecked())
        el_en = bool(self.chk_enable_el.isChecked())
        en_az = hw_on and az_en
        en_el = hw_on and el_en
        self.btn_cal_start.setEnabled(en_az)
        self.btn_cal_del.setEnabled(en_az)
        self.btn_cal_reset.setEnabled(en_az)
        self.btn_cal_start_el.setEnabled(en_el)
        self.btn_cal_del_el.setEnabled(en_el)
        self.btn_cal_reset_el.setEnabled(en_el)

    def _apply_cal_progress_bar_ui(self, pb: QProgressBar, st: int, prog: int) -> None:
        """Fortschrittsbalken: sichtbar bei Kalibrierfahrt (GETCALSTATE state/progress)."""
        prog = max(0, min(100, int(prog)))
        if st == 1:
            pb.setVisible(True)
            pb.setRange(0, 100)
            pb.setValue(prog)
            pb.setFormat("%p%")
        elif st == 2:
            pb.setVisible(True)
            pb.setRange(0, 100)
            pb.setValue(100)
            pb.setFormat(t("settings.cal_progress_done_fmt"))
        elif st == 3:
            pb.setVisible(True)
            pb.setRange(0, 100)
            pb.setValue(0)
            pb.setFormat(t("settings.cal_progress_abort_fmt"))
        else:
            pb.setVisible(False)
            pb.setValue(0)
            pb.setFormat("%p%")

    def _tick_cal_progress_poll(self) -> None:
        if not self.isVisible() or not self._calvalid_tab_active():
            return
        self._tick_cal_progress_one_axis("az")
        self._tick_cal_progress_one_axis("el")

    def _tick_cal_progress_one_axis(self, which: str) -> None:
        if which == "az":
            if not self.chk_enable_az.isChecked():
                self._apply_cal_progress_bar_ui(self._pb_cal, 0, 0)
                self._cal_led_want_blink_az = False
                self._prev_cal_led_blink_az = False
                self._led_cal_valid.set_blinking_green(False)
                return
            dst = int(self.sp_slave_az.value())
            ax = self.ctrl.az
            pb = self._pb_cal
            dl_attr = "_cal_poll_deadline_ts_az"
            prev_attr = "_prev_cal_prog_st_az"
        else:
            if not self.chk_enable_el.isChecked():
                self._apply_cal_progress_bar_ui(self._pb_cal_el, 0, 0)
                self._cal_led_want_blink_el = False
                self._prev_cal_led_blink_el = False
                self._led_cal_valid_el.set_blinking_green(False)
                return
            dst = int(self.sp_slave_el.value())
            ax = self.ctrl.el
            pb = self._pb_cal_el
            dl_attr = "_cal_poll_deadline_ts_el"
            prev_attr = "_prev_cal_prog_st_el"
        st = int(getattr(ax, "cal_state", 0))
        prog = int(getattr(ax, "cal_progress", 0))
        self._apply_cal_progress_bar_ui(pb, st, prog)
        now = time.time()
        dl = float(getattr(self, dl_attr) or 0.0)
        prev = int(getattr(self, prev_attr))
        if which == "az":
            want_b_attr = "_cal_led_want_blink_az"
            led = self._led_cal_valid
            prev_blink_attr = "_prev_cal_led_blink_az"
        else:
            want_b_attr = "_cal_led_want_blink_el"
            led = self._led_cal_valid_el
            prev_blink_attr = "_prev_cal_led_blink_el"
        want_b = bool(getattr(self, want_b_attr))
        if st in (2, 3):
            setattr(self, want_b_attr, False)
            want_b = False
        elif want_b and st == 0 and dl > 0.0 and now >= dl:
            setattr(self, want_b_attr, False)
            want_b = False
        prev_blink = bool(getattr(self, prev_blink_attr, False))
        do_blink = want_b and (st == 1 or (st == 0 and now < dl))
        led.set_blinking_green(do_blink)
        if prev_blink and not do_blink:
            QTimer.singleShot(0, self._poll_getcalvalid_once)
        setattr(self, prev_blink_attr, do_blink)
        want_poll = st == 1 or now < dl
        if prev == 1 and st == 2:
            QTimer.singleShot(300, self._poll_getcalvalid_once)
            setattr(self, dl_attr, min(dl, now + 25.0) if dl > now else 0.0)
        elif prev == 1 and st == 3:
            setattr(self, dl_attr, min(dl, now + 15.0) if dl > now else 0.0)
        setattr(self, prev_attr, st)
        if want_poll:
            try:
                self.ctrl.send_ui_command(
                    int(dst),
                    "GETCALSTATE",
                    "0",
                    expect_prefix=None,
                    priority=0,
                    apply_local_state=False,
                )
            except Exception:
                pass

    def _apply_calvalid_led(self, led: Led, r: str | None) -> None:
        if r is None or str(r).startswith(SYNC_UI_NAK_PREFIX):
            led.set_state(False)
            return
        s = str(r).strip().split(";")[0].strip()
        led.set_state(s == "1")

    def _poll_getcalvalid_axis(
        self, sync, which: str, led: Led, prev_attr: str
    ) -> None:
        if which == "az" and getattr(self, "_cal_led_want_blink_az", False):
            return
        if which == "el" and getattr(self, "_cal_led_want_blink_el", False):
            return
        ax = self.ctrl.az if which == "az" else self.ctrl.el
        dst = int(self.sp_slave_az.value() if which == "az" else self.sp_slave_el.value())
        try:
            r = sync(
                dst,
                "GETCALVALID",
                "0",
                expect_prefix="ACK_GETCALVALID",
                timeout_s=1.2,
            )
        except Exception:
            r = None
        self._apply_calvalid_led(led, r)
        try:
            st = int(getattr(ax, "cal_state", 0))
            prev = getattr(self, prev_attr)
            if prev is not None and prev == 1 and st == 2:
                QTimer.singleShot(400, self._poll_getcalvalid_once)
            setattr(self, prev_attr, st)
        except Exception:
            setattr(self, prev_attr, None)

    def _poll_getcalvalid_once(self) -> None:
        """GETCALVALID je Achse nur wenn AZ/EL unter Verbindung aktiv."""
        if not self.isVisible() or not self._calvalid_tab_active():
            return
        sync = getattr(self.ctrl, "sync_ui_command_response", None)
        if sync is None:
            return
        if self.chk_enable_az.isChecked():
            self._poll_getcalvalid_axis(
                sync, "az", self._led_cal_valid, "_prev_cal_state_for_led_az"
            )
        if self.chk_enable_el.isChecked():
            self._poll_getcalvalid_axis(
                sync, "el", self._led_cal_valid_el, "_prev_cal_state_for_led_el"
            )

    def _can_start_cal(self, which: str) -> bool:
        """Verbindung und Referenz vor SETCAL prüfen; sonst Meldung und kein Senden."""
        hw = getattr(self.ctrl, "hw", None)
        if hw is None or not hw.is_connected():
            msg = t("settings.cal_start_not_connected")
            QMessageBox.warning(self, t("settings.cal_start_title"), msg)
            self.lbl_status.setText(msg)
            return False
        ax = self.ctrl.az if which == "az" else self.ctrl.el
        if not bool(getattr(ax, "online", False)):
            msg = (
                t("settings.cal_start_axis_offline_az")
                if which == "az"
                else t("settings.cal_start_axis_offline_el")
            )
            QMessageBox.warning(self, t("settings.cal_start_title"), msg)
            self.lbl_status.setText(msg)
            return False
        if not bool(getattr(ax, "referenced", False)):
            msg = t("settings.cal_start_need_ref")
            QMessageBox.warning(self, t("settings.cal_start_title"), msg)
            self.lbl_status.setText(msg)
            return False
        return True

    def _on_settings_cal_start_az(self) -> None:
        """SETCAL nur AZ-Slave."""
        if not self.chk_enable_az.isChecked():
            return
        if not self._can_start_cal("az"):
            return
        self._cal_led_want_blink_az = True
        self._led_cal_valid.set_blinking_green(True)
        self._cal_poll_deadline_ts_az = time.time() + 300.0
        self._prev_cal_prog_st_az = -1
        dst = int(self.sp_slave_az.value())
        try:
            self.ctrl.send_ui_command(dst, "SETCAL", "0", expect_prefix=None, priority=0)
        except Exception:
            pass
        self.lbl_status.setText(t("cmd.hint_cal_start"))
        if self._calvalid_tab_active():
            QTimer.singleShot(0, self._tick_cal_progress_poll)
            for ms in (800, 2500, 8000):
                QTimer.singleShot(ms, self._poll_getcalvalid_once)

    def _on_settings_cal_start_el(self) -> None:
        """SETCAL nur EL-Slave."""
        if not self.chk_enable_el.isChecked():
            return
        if not self._can_start_cal("el"):
            return
        self._cal_led_want_blink_el = True
        self._led_cal_valid_el.set_blinking_green(True)
        self._cal_poll_deadline_ts_el = time.time() + 300.0
        self._prev_cal_prog_st_el = -1
        dst = int(self.sp_slave_el.value())
        try:
            self.ctrl.send_ui_command(dst, "SETCAL", "0", expect_prefix=None, priority=0)
        except Exception:
            pass
        self.lbl_status.setText(t("cmd.hint_cal_start"))
        if self._calvalid_tab_active():
            QTimer.singleShot(0, self._tick_cal_progress_poll)
            for ms in (800, 2500, 8000):
                QTimer.singleShot(ms, self._poll_getcalvalid_once)

    def _on_settings_cal_reset_az(self) -> None:
        """CLRSTAT nur AZ."""
        if not self.chk_enable_az.isChecked():
            return
        dst = int(self.sp_slave_az.value())
        try:
            self.ctrl.send_ui_command(dst, "CLRSTAT", "0", expect_prefix=None, priority=0)
        except Exception:
            pass
        self.lbl_status.setText(t("cmd.hint_cal_reset"))
        if self._calvalid_tab_active():
            QTimer.singleShot(300, self._poll_getcalvalid_once)

    def _on_settings_cal_reset_el(self) -> None:
        """CLRSTAT nur EL."""
        if not self.chk_enable_el.isChecked():
            return
        dst = int(self.sp_slave_el.value())
        try:
            self.ctrl.send_ui_command(dst, "CLRSTAT", "0", expect_prefix=None, priority=0)
        except Exception:
            pass
        self.lbl_status.setText(t("cmd.hint_cal_reset"))
        if self._calvalid_tab_active():
            QTimer.singleShot(300, self._poll_getcalvalid_once)

    def _on_settings_cal_delcal_az(self) -> None:
        """DELCAL nur AZ."""
        if not self.chk_enable_az.isChecked():
            return
        dst = int(self.sp_slave_az.value())
        try:
            self.ctrl.send_ui_command(dst, "DELCAL", "0", expect_prefix=None, priority=0)
        except Exception:
            pass
        self.lbl_status.setText(t("cmd.hint_cal_delcal"))
        if self._calvalid_tab_active():
            QTimer.singleShot(300, self._poll_getcalvalid_once)

    def _on_settings_cal_delcal_el(self) -> None:
        """DELCAL nur EL."""
        if not self.chk_enable_el.isChecked():
            return
        dst = int(self.sp_slave_el.value())
        try:
            self.ctrl.send_ui_command(dst, "DELCAL", "0", expect_prefix=None, priority=0)
        except Exception:
            pass
        self.lbl_status.setText(t("cmd.hint_cal_delcal"))
        if self._calvalid_tab_active():
            QTimer.singleShot(300, self._poll_getcalvalid_once)

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

        event_loop = QEventLoop(self)

        def on_done(ok: bool):
            result[0] = ok
            QMetaObject.invokeMethod(event_loop, b"quit", Qt.ConnectionType.QueuedConnection)

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

        event_loop = QEventLoop(self)

        def on_done(ok: bool):
            result[0] = ok
            QMetaObject.invokeMethod(event_loop, b"quit", Qt.ConnectionType.QueuedConnection)

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
        if row == getattr(self, "_tab_statistics_index", -1):
            self._start_calvalid_timer()
        else:
            self._stop_calvalid_timer()
        if row == getattr(self, "_tab_antenna_index", -1):
            QTimer.singleShot(0, self._load_controller_antenna_names_from_bus)
        if row == getattr(self, "_tab_controller_index", -1):
            QTimer.singleShot(0, self._load_controller_from_bus)
        if row == getattr(self, "_tab_shortcuts_index", -1):
            self._shortcuts_tab.refresh_el_visibility()
            self.maybe_refresh_antenna_names_for_shortcuts_tab()
            QTimer.singleShot(0, self._shortcuts_tab.refresh_antenna_shortcut_row_labels)
            QTimer.singleShot(400, self._shortcuts_tab.refresh_antenna_shortcut_row_labels)

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
        pad_right_line = px_to_dip(self, 10)
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
                padding-right: {pad_right_line}px;
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
        self._update_strom_cal_sections_visibility()
        show = self.chk_enable_az.isChecked()
        self.gb_antenna_az.setVisible(show)
        self.gb_antenna_misc.setVisible(show)
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
        if not hasattr(self, "_antenna_offset_spinboxes_az"):
            return
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
        if self._controller_hw_enabled():
            names_en = (
                self.chk_enable_az.isChecked()
                and bool(self.hw and self.hw.is_connected())
                and self._controller_bus_read_enabled()
                and bool(getattr(self, "_antenna_names_bus_read_ok", False))
            )
        else:
            names_en = az_enabled
        for ed in self._antenna_name_edits_az:
            ed.setEnabled(names_en)

    def _tick_hw_link_state(self) -> None:
        """Periodisch: Verbindungsabhängige Buttons + Antennennamen (HW-Controller)."""
        self._update_strom_cal_buttons_enabled()
        self._update_antenna_offset_enabled()

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

    def _push_antenna_names_to_config(self) -> None:
        """Antennennamen (Kompass/Karte) — gleiche Quelle wie Hardware-Controller."""
        try:
            self.cfg.setdefault("ui", {})["antenna_names"] = [
                self.ed_antenna_name_1.text().strip() or t("settings.antenna_1"),
                self.ed_antenna_name_2.text().strip() or t("settings.antenna_2"),
                self.ed_antenna_name_3.text().strip() or t("settings.antenna_3"),
            ]
        except Exception:
            pass

    def _antenna_display_name(self, idx: int) -> str:
        """Anzeige-/Controller-Name (max. 9 Zeichen, ohne # : $)."""
        fb = (t("settings.antenna_1"), t("settings.antenna_2"), t("settings.antenna_3"))
        return self._sanitize_controller_name(
            self._antenna_name_edits_az[idx].text().strip() or fb[idx],
        )

    def _wire_antenna_name_sync(self) -> None:
        """Namen nur unter Tab „Antennen“; Config + Controller-Dirty bei Änderung."""
        for i in range(3):
            self._antenna_name_edits_az[i].textChanged.connect(
                lambda _txt="", idx=i: self._on_antenna_name_text_changed(idx),
            )

    def _on_antenna_name_text_changed(self, idx: int) -> None:
        self._push_antenna_names_to_config()
        self._mark_controller_name_dirty(idx)

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

    def _on_locator_apply_coords(self) -> None:
        """Maidenhead-Locator → Zellenmitte in die Koordinatenfelder übernehmen (offline)."""
        s = "".join(str(self.ed_location_locator.text() or "").strip().upper().split())
        if not s:
            QMessageBox.information(
                self,
                t("settings.title"),
                t("settings.locator_empty"),
            )
            return
        ll = maidenhead_to_lat_lon(s)
        if ll is None:
            QMessageBox.warning(
                self,
                t("settings.title"),
                t("settings.locator_invalid"),
            )
            return
        self.ed_location_locator.setText(s)
        self.ed_location_lat.setValue(ll[0])
        self.ed_location_lon.setValue(ll[1])

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

        mid = int(self.sp_master.value())
        cid = int(self.sp_controller_id.value())
        if self._controller_hw_enabled() and cid == mid:
            QMessageBox.warning(
                self,
                t("settings.title"),
                t("settings.controller_err_same_as_master", mid=mid),
            )
            self.lbl_status.setText("")
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
        self.cfg.setdefault("ui", {})["map_aswatch_only_asnearest_list"] = bool(
            self.chk_map_aswatch_only_asnearest_list.isChecked()
        )
        self.cfg.setdefault("ui", {})["map_aswatch_cluster_enabled"] = bool(
            self.chk_map_aswatch_cluster.isChecked()
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
        self.cfg.setdefault("ui", {})["location_locator"] = str(
            self.ed_location_locator.text() or ""
        ).strip()
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
        try:
            self.cfg["rig_bridge"] = self._rig_bridge_tab.to_config()
            if self.rig_bridge_manager is not None:
                self.rig_bridge_manager.update_config(self.cfg["rig_bridge"])
        except Exception as exc:
            self.logbuf.write("WARN", f"Rig-Bridge Konfigurationsübernahme fehlgeschlagen: {exc}")

        try:
            self._shortcuts_tab.apply_to_cfg(self.cfg)
        except Exception as exc:
            self.logbuf.write("WARN", f"Shortcuts-Konfiguration: {exc}")

        chw = self.cfg.setdefault("controller_hw", {})
        chw["enabled"] = bool(self.chk_hw_controller_enabled.isChecked())
        chw["cont_id"] = int(self.sp_controller_id.value())
        chw["cont_id_configured"] = True
        chw["ant_name_1"] = self._antenna_display_name(0)
        chw["ant_name_2"] = self._antenna_display_name(1)
        chw["ant_name_3"] = self._antenna_display_name(2)
        chw["slow_pwm"] = int(self.sp_cont_pwm_slow.value())
        chw["fast_pwm"] = int(self.sp_cont_pwm_fast.value())
        chw["speaker_freq_hz"] = int(self.sp_cont_beep_freq.value())
        chw["speaker_volume"] = int(self.sp_cont_beep_vol.value())
        chw["display_brightness_pct"] = int(self.sl_cont_display_brightness.value())
        chw["wind_anemometer"] = bool(self.chk_cont_wind_anemo.isChecked())
        chw["encoder_delta"] = int(self.cb_cont_encoder_delta.currentData())
        chw["antenna_realign_on_switch"] = bool(self.chk_cont_antenna_realign.isChecked())

        # AZ-Versatz und Öffnungswinkel in den Rotor schreiben (SETANTOFF1–3, SETANGLE1–3)
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
                QMessageBox.warning(
                    self,
                    t("settings.msgbox_az_title"),
                    t("settings.msgbox_az_error"),
                )
        else:
            self.lbl_status.setText(t("settings.status_saved"))
            QApplication.processEvents()

        if not self._save_controller_hw_if_changed():
            self.lbl_status.setText(t("settings.controller_status_write_fail"))
            QApplication.processEvents()
            QMessageBox.warning(
                self,
                t("settings.title"),
                t("settings.controller_status_write_fail"),
            )

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

    @staticmethod
    def _parse_hw_int(s: str | None) -> int | None:
        if s is None:
            return None
        try:
            return int(float(str(s).strip().split(";")[0].replace(",", ".")))
        except Exception:
            return None

    @staticmethod
    def _sanitize_controller_name(s: str) -> str:
        bad = "#:$"
        t = (s or "").strip()
        return "".join(c for c in t[:9] if c not in bad)[:9]

    def _controller_hw_enabled(self) -> bool:
        """True: Hardware-Controller ist eingeschaltet (Checkbox / Config)."""
        if hasattr(self, "chk_hw_controller_enabled"):
            return self.chk_hw_controller_enabled.isChecked()
        ch = self.cfg.get("controller_hw") or {}
        return bool(ch.get("enabled", True))

    def _on_hw_controller_toggled(self, _checked: bool) -> None:
        self._apply_controller_enabled_ui()

    def _apply_controller_enabled_ui(self) -> None:
        """Gruppe „Hardware-Controller“ ein-/ausgrauen; Checkbox bleibt bedienbar."""
        if not hasattr(self, "gb_controller"):
            return
        self.gb_controller.setEnabled(self._controller_hw_enabled())
        self._update_antenna_offset_enabled()
        self._update_wind_dir_display_row_visibility()

    def _update_wind_dir_display_row_visibility(self) -> None:
        """Wind-Richtung (UI): nur sinnvoll mit Windmesser (Controller)."""
        if not hasattr(self, "_gb_wind_dir_display"):
            return
        self._gb_wind_dir_display.setVisible(bool(self.chk_cont_wind_anemo.isChecked()))

    def _controller_bus_dst(self) -> int:
        """RS485-Zieladresse für den Hardware-Controller (Einstellungsfeld Controller-ID)."""
        return int(self.sp_controller_id.value())

    def _controller_bus_read_enabled(self) -> bool:
        """GET* nur, wenn die Controller-ID mindestens einmal gespeichert wurde (bekannt)."""
        if not self._controller_hw_enabled():
            return False
        ch = self.cfg.get("controller_hw") or {}
        if not ch.get("cont_id_configured") and "cont_id" not in ch:
            return False
        if self._controller_bus_dst() == int(self.sp_master.value()):
            return False
        return True

    def _controller_encoder_delta_value(self) -> int:
        v = self.cb_cont_encoder_delta.currentData()
        try:
            i = int(v)
        except (TypeError, ValueError):
            i = 10
        return 1 if i == 1 else 10

    def _update_conled_brightness_label(self) -> None:
        if hasattr(self, "lbl_cont_display_brightness"):
            self.lbl_cont_display_brightness.setText(
                f"{int(self.sl_cont_display_brightness.value())} %"
            )

    def _on_conled_brightness_value_changed(self, _v: int) -> None:
        self._update_conled_brightness_label()

    def _on_conled_brightness_released(self) -> None:
        self._update_conled_brightness_label()
        if not self._controller_hw_enabled():
            return
        if not self.hw.is_connected():
            return
        if not hasattr(self.ctrl, "sync_ui_command_response"):
            return
        dst = self._controller_bus_dst()
        val = int(self.sl_cont_display_brightness.value())
        r = self.ctrl.sync_ui_command_response(
            dst, "SETCONLEDP", str(val), "ACK_SETCONLEDP"
        )
        if _sync_got_ack_value(r):
            self._snapshot_controller = self._controller_snapshot_from_ui()
        else:
            self.lbl_status.setText(t("settings.controller_status_write_fail"))

    def _controller_snapshot_from_ui(
        self,
    ) -> tuple[int, str, str, str, int, int, int, int, int, int, int, int]:
        return (
            int(self.sp_controller_id.value()),
            self._antenna_display_name(0),
            self._antenna_display_name(1),
            self._antenna_display_name(2),
            int(self.sp_cont_pwm_slow.value()),
            int(self.sp_cont_pwm_fast.value()),
            int(self.sp_cont_beep_freq.value()),
            int(self.sp_cont_beep_vol.value()),
            int(self.sl_cont_display_brightness.value()),
            1 if self.chk_cont_wind_anemo.isChecked() else 0,
            self._controller_encoder_delta_value(),
            1 if self.chk_cont_antenna_realign.isChecked() else 0,
        )

    def _mark_controller_name_dirty(self, idx: int) -> None:
        if getattr(self, "_controller_suppress_dirty", False):
            return
        try:
            self._controller_name_dirty[idx] = True
        except Exception:
            pass

    def _mark_pwm_dirty(self, idx: int) -> None:
        if getattr(self, "_controller_suppress_dirty", False):
            return
        try:
            self._controller_pwm_dirty[idx] = True
        except Exception:
            pass

    def _mark_beep_dirty(self, idx: int) -> None:
        if getattr(self, "_controller_suppress_dirty", False):
            return
        try:
            self._controller_beep_dirty[idx] = True
        except Exception:
            pass

    def _mark_anemo_dirty(self, _checked: bool = False) -> None:
        if getattr(self, "_controller_suppress_dirty", False):
            return
        self._controller_anemo_dirty = True

    def _mark_delta_dirty(self, _idx: int = -1) -> None:
        if getattr(self, "_controller_suppress_dirty", False):
            return
        self._controller_delta_dirty = True

    def _mark_cha_dirty(self, _checked: bool = False) -> None:
        if getattr(self, "_controller_suppress_dirty", False):
            return
        self._controller_cha_dirty = True

    def _clear_controller_field_dirty(self) -> None:
        self._controller_name_dirty = [False, False, False]
        self._controller_pwm_dirty = [False, False]
        self._controller_beep_dirty = [False, False]
        self._controller_anemo_dirty = False
        self._controller_delta_dirty = False
        self._controller_cha_dirty = False

    def _apply_controller_from_cfg_only(self) -> None:
        self._controller_suppress_dirty = True
        try:
            ch = self.cfg.get("controller_hw") or {}
            try:
                self.sp_controller_id.setValue(max(0, min(245, int(ch.get("cont_id", 2)))))
            except (TypeError, ValueError):
                self.sp_controller_id.setValue(2)
            _ui_n = self.cfg.get("ui") or {}
            _names = list(_ui_n.get("antenna_names", []))
            while len(_names) < 3:
                _names.append("")
            for _i, _ed in enumerate(self._antenna_name_edits_az):
                _ed.setText(self._sanitize_controller_name(str(_names[_i] or "")))
            self._set_antenna_names_led_ok(False)
            try:
                self.sp_cont_pwm_slow.setValue(max(0, min(100, int(ch.get("slow_pwm", 30)))))
            except (TypeError, ValueError):
                self.sp_cont_pwm_slow.setValue(30)
            try:
                self.sp_cont_pwm_fast.setValue(max(0, min(100, int(ch.get("fast_pwm", 80)))))
            except (TypeError, ValueError):
                self.sp_cont_pwm_fast.setValue(80)
            try:
                self.sp_cont_beep_freq.setValue(
                    max(100, min(4000, int(ch.get("speaker_freq_hz", 1000))))
                )
            except (TypeError, ValueError):
                self.sp_cont_beep_freq.setValue(1000)
            try:
                self.sp_cont_beep_vol.setValue(max(0, min(50, int(ch.get("speaker_volume", 50)))))
            except (TypeError, ValueError):
                self.sp_cont_beep_vol.setValue(50)
            try:
                self.sl_cont_display_brightness.setValue(
                    max(0, min(100, int(ch.get("display_brightness_pct", 100))))
                )
            except (TypeError, ValueError):
                self.sl_cont_display_brightness.setValue(100)
            self._update_conled_brightness_label()
            self.chk_cont_wind_anemo.setChecked(bool(ch.get("wind_anemometer", False)))
            try:
                _ed = int(ch.get("encoder_delta", 10))
            except (TypeError, ValueError):
                _ed = 10
            if _ed not in (1, 10):
                _ed = 10
            self.cb_cont_encoder_delta.setCurrentIndex(0 if _ed == 1 else 1)
            self.chk_cont_antenna_realign.setChecked(bool(ch.get("antenna_realign_on_switch", False)))
            self._snapshot_controller = self._controller_snapshot_from_ui()
            self._set_controller_led_ok(False)
            self._clear_controller_field_dirty()
            self._update_wind_dir_display_row_visibility()
        finally:
            self._controller_suppress_dirty = False

    def _set_controller_wait_visible(self, visible: bool) -> None:
        """„Warten“ neben der LED nur während laufender Bus-Abfrage."""
        if not hasattr(self, "_lbl_controller_wait"):
            return
        self._lbl_controller_wait.setVisible(bool(visible))

    def _set_controller_led_ok(self, ok: bool) -> None:
        """LED neben „ID setzen“: grün = alle Controller-Werte vom Bus gelesen, sonst rot."""
        if not hasattr(self, "_lbl_controller_led"):
            return
        d = px_to_dip(self, 6)
        s = px_to_dip(self, 12)
        if ok:
            bg, br = "#2e7d32", "#1b5e20"
        else:
            bg, br = "#c62828", "#8e0000"
        self._lbl_controller_led.setStyleSheet(
            f"QLabel#controllerReadLed {{ background-color: {bg}; border: 1px solid {br}; "
            f"border-radius: {d}px; min-width: {s}px; max-width: {s}px; "
            f"min-height: {s}px; max-height: {s}px; }}"
        )

    def _set_antenna_names_wait_visible(self, visible: bool) -> None:
        if not hasattr(self, "_lbl_antenna_names_wait"):
            return
        self._lbl_antenna_names_wait.setVisible(bool(visible))

    def _set_antenna_names_led_ok(self, ok: bool) -> None:
        """LED Tab Antennen: grün = GETCONANTNAME1–3 alle OK."""
        self._antenna_names_bus_read_ok = bool(ok)
        if hasattr(self, "_lbl_antenna_names_led"):
            d = px_to_dip(self, 6)
            s = px_to_dip(self, 12)
            if ok:
                bg, br = "#2e7d32", "#1b5e20"
            else:
                bg, br = "#c62828", "#8e0000"
            self._lbl_antenna_names_led.setStyleSheet(
                f"QLabel#antennaNamesReadLed {{ background-color: {bg}; border: 1px solid {br}; "
                f"border-radius: {d}px; min-width: {s}px; max-width: {s}px; "
                f"min-height: {s}px; max-height: {s}px; }}"
            )
        try:
            self._update_antenna_offset_enabled()
        except Exception:
            pass

    def _load_controller_from_bus(self) -> None:
        """Controller-Werte vom Bus lesen. Seriell: kein zweiter paralleler Lauf (QEventLoop-Reentranz)."""
        if getattr(self, "_controller_load_busy", False):
            self._controller_load_queued = True
            return
        self._controller_load_busy = True
        self._controller_load_queued = False
        try:
            self._load_controller_from_bus_impl()
        finally:
            self._controller_load_busy = False
            if self._controller_load_queued:
                QTimer.singleShot(0, self._load_controller_from_bus)

    def maybe_refresh_antenna_names_for_shortcuts_tab(self) -> None:
        """Controller-Antennennamen lesen, falls noch kein erfolgreicher GETCONANTNAME-Lauf."""
        if bool(getattr(self, "_antenna_names_bus_read_ok", False)):
            return
        QTimer.singleShot(0, self._load_controller_antenna_names_from_bus)

    def _load_controller_antenna_names_from_bus(self) -> None:
        """Nur GETCONANTNAME1–3 → Felder unter Tab „Antennen“ (+ LED)."""
        if getattr(self, "_controller_load_busy", False):
            QTimer.singleShot(150, self._load_controller_antenna_names_from_bus)
            return
        self._controller_load_busy = True
        try:
            self._load_controller_antenna_names_from_bus_impl()
        finally:
            self._controller_load_busy = False
            if self._controller_load_queued:
                QTimer.singleShot(0, self._load_controller_from_bus)

    def _read_controller_conant_names_into_ui(self, dst: int) -> list[bool]:
        """GETCONANTNAME1–3 → Namensfelder unter Tab „Antennen“."""
        c = self.ctrl
        acks: list[bool] = []
        eds = self._antenna_name_edits_az
        for i, (cmd, exp) in enumerate(
            (
                ("GETCONANTNAME1", "ACK_GETCONANTNAME1"),
                ("GETCONANTNAME2", "ACK_GETCONANTNAME2"),
                ("GETCONANTNAME3", "ACK_GETCONANTNAME3"),
            ),
            start=1,
        ):
            rp = c.sync_ui_command_response(dst, cmd, "0", exp)
            acks.append(_sync_got_ack_value(rp))
            if rp is not None and _sync_got_ack_value(rp):
                eds[i - 1].setText(self._sanitize_controller_name(rp.split(";")[0]))
        return acks

    def _merge_snapshot_controller_antenna_names(self) -> None:
        """Nach Namens-Lesezugriff: Snapshot der drei Namen an UI anpassen (Speichern ohne Phantom-SET)."""
        cur = self._controller_snapshot_from_ui()
        snap = getattr(self, "_snapshot_controller", None)
        if snap is None or len(snap) < len(cur):
            self._snapshot_controller = cur
            return
        s = list(snap)
        s[1], s[2], s[3] = cur[1], cur[2], cur[3]
        self._snapshot_controller = tuple(s)

    def _load_controller_antenna_names_from_bus_impl(self) -> None:
        try:
            if not self._controller_hw_enabled():
                self._set_antenna_names_led_ok(False)
                return
            if not hasattr(self.ctrl, "sync_ui_command_response"):
                self._set_antenna_names_led_ok(False)
                return
            if not self.hw.is_connected():
                self._set_antenna_names_led_ok(False)
                return
            if not self._controller_bus_read_enabled():
                self._set_antenna_names_led_ok(False)
                return
            dst = self._controller_bus_dst()
            self._set_antenna_names_wait_visible(True)
            QApplication.processEvents()
            self._controller_suppress_dirty = True
            try:
                name_acks = self._read_controller_conant_names_into_ui(dst)
                self._set_antenna_names_led_ok(len(name_acks) == 3 and all(name_acks))
                self._merge_snapshot_controller_antenna_names()
                for i in range(3):
                    self._controller_name_dirty[i] = False
            finally:
                self._controller_suppress_dirty = False
                self._set_antenna_names_wait_visible(False)
        finally:
            try:
                st = getattr(self, "_shortcuts_tab", None)
                if st is not None:
                    st.refresh_antenna_shortcut_row_labels()
            except Exception:
                pass

    def _load_controller_from_bus_impl(self) -> None:
        if not self._controller_hw_enabled():
            self._apply_controller_from_cfg_only()
            return
        if not hasattr(self.ctrl, "sync_ui_command_response"):
            self._apply_controller_from_cfg_only()
            return
        if not self.hw.is_connected():
            self._apply_controller_from_cfg_only()
            return
        if not self._controller_bus_read_enabled():
            self._apply_controller_from_cfg_only()
            return
        dst = self._controller_bus_dst()
        self.lbl_status.setText(t("settings.controller_status_reading"))
        self._set_controller_led_ok(False)
        self._set_controller_wait_visible(True)
        QApplication.processEvents()
        c = self.ctrl
        self._controller_suppress_dirty = True
        try:
            acks: list[bool] = []
            p = c.sync_ui_command_response(dst, "GETCONTID", "0", "ACK_GETCONTID")
            acks.append(_sync_got_ack_value(p))
            if p is not None and _sync_got_ack_value(p):
                v = self._parse_hw_int(p)
                if v is None:
                    v = self._parse_hw_int(str(p).split(";")[0].strip())
                if v is not None:
                    self.sp_controller_id.setValue(max(0, min(245, v)))
            name_acks = self._read_controller_conant_names_into_ui(dst)
            acks.extend(name_acks)
            self._set_antenna_names_led_ok(len(name_acks) == 3 and all(name_acks))
            for sp, cmd, exp, lo, hi in (
                (self.sp_cont_pwm_slow, "GETCONSPWM", "ACK_GETCONSPWM", 0, 100),
                (self.sp_cont_pwm_fast, "GETCONFPWM", "ACK_GETCONFPWM", 0, 100),
                (self.sp_cont_beep_freq, "GETCONFRQ", "ACK_GETCONFRQ", 100, 4000),
                (self.sp_cont_beep_vol, "GETLSL", "ACK_GETLSL", 0, 50),
                (self.sl_cont_display_brightness, "GETCONLEDP", "ACK_GETCONLEDP", 0, 100),
            ):
                rp = c.sync_ui_command_response(dst, cmd, "0", exp)
                if cmd in ("GETCONFRQ", "GETLSL", "GETCONLEDP"):
                    if _sync_nak_notimpl(rp):
                        acks.append(True)
                        continue
                    if rp is not None and str(rp).startswith(SYNC_UI_NAK_PREFIX):
                        acks.append(False)
                        continue
                acks.append(_sync_got_ack_value(rp))
                if rp is None or not _sync_got_ack_value(rp):
                    continue
                w = self._parse_hw_int(rp)
                if w is None:
                    w = self._parse_hw_int(str(rp).split(";")[0].strip())
                if w is not None:
                    sp.setValue(max(lo, min(hi, w)))
            rp_ano = c.sync_ui_command_response(dst, "GETCONANO", "0", "ACK_GETCONANO")
            if _sync_nak_notimpl(rp_ano):
                acks.append(True)
            elif rp_ano is not None and str(rp_ano).startswith(SYNC_UI_NAK_PREFIX):
                acks.append(False)
            else:
                acks.append(_sync_got_ack_value(rp_ano))
                if rp_ano is not None and _sync_got_ack_value(rp_ano):
                    w = self._parse_hw_int(rp_ano)
                    if w is None:
                        w = self._parse_hw_int(str(rp_ano).split(";")[0].strip())
                    if w is not None:
                        self.chk_cont_wind_anemo.setChecked(bool(int(w)))
            rp_del = c.sync_ui_command_response(dst, "GETCONDELTA", "0", "ACK_GETCONDELTA")
            if _sync_nak_notimpl(rp_del):
                acks.append(True)
            elif rp_del is not None and str(rp_del).startswith(SYNC_UI_NAK_PREFIX):
                acks.append(False)
            else:
                acks.append(_sync_got_ack_value(rp_del))
                if rp_del is not None and _sync_got_ack_value(rp_del):
                    w = self._parse_hw_int(rp_del)
                    if w is None:
                        w = self._parse_hw_int(str(rp_del).split(";")[0].strip())
                    if w is not None:
                        wi = int(w)
                        if wi == 1:
                            self.cb_cont_encoder_delta.setCurrentIndex(0)
                        elif wi == 10:
                            self.cb_cont_encoder_delta.setCurrentIndex(1)
            rp_cha = c.sync_ui_command_response(dst, "GETCONCHA", "0", "ACK_GETCONCHA")
            if _sync_nak_notimpl(rp_cha):
                acks.append(True)
            elif rp_cha is not None and str(rp_cha).startswith(SYNC_UI_NAK_PREFIX):
                acks.append(False)
            else:
                acks.append(_sync_got_ack_value(rp_cha))
                if rp_cha is not None and _sync_got_ack_value(rp_cha):
                    w = self._parse_hw_int(rp_cha)
                    if w is None:
                        w = self._parse_hw_int(str(rp_cha).split(";")[0].strip())
                    if w is not None:
                        self.chk_cont_antenna_realign.setChecked(bool(int(w)))
            # LED: alle Kern-Abfragen mit ACK; Piep/LED-Ring (GETCONFRQ/GETLSL/GETCONLEDP): NAK NOTIMPL zählt als Bus-OK.
            # Anzahl = 1 + 3 Namen + 5 PWM/Beep/LED-Ring + GETCONANO + GETCONDELTA + GETCONCHA
            _n_ctrl_reads = 1 + 3 + 5 + 1 + 1 + 1
            all_ok = len(acks) == _n_ctrl_reads and all(acks)
            self.lbl_status.setText(t("settings.controller_status_saved"))
            self._set_controller_led_ok(all_ok)
            self._snapshot_controller = self._controller_snapshot_from_ui()
            self._clear_controller_field_dirty()
        finally:
            self._controller_suppress_dirty = False
            self._set_controller_wait_visible(False)
            self._update_wind_dir_display_row_visibility()

    def _save_controller_hw_if_changed(self) -> bool:
        if not self._controller_hw_enabled():
            self._snapshot_controller = self._controller_snapshot_from_ui()
            self._clear_controller_field_dirty()
            return True
        if not hasattr(self.ctrl, "sync_ui_command_response"):
            self._snapshot_controller = self._controller_snapshot_from_ui()
            return True
        snap = getattr(self, "_snapshot_controller", None)
        cur = self._controller_snapshot_from_ui()
        if snap is not None and len(snap) < len(cur):
            snap = tuple(snap) + tuple(cur[i] for i in range(len(snap), len(cur)))
        if snap is None:
            snap = cur
        _ctrl_hw_dirty = (
            any(self._controller_name_dirty)
            or any(self._controller_pwm_dirty)
            or any(self._controller_beep_dirty)
            or self._controller_anemo_dirty
            or self._controller_delta_dirty
            or self._controller_cha_dirty
        )
        if snap == cur and not _ctrl_hw_dirty:
            return True
        if not self.hw.is_connected():
            self._snapshot_controller = cur
            return True
        c = self.ctrl
        all_ok = True
        dst = int(cur[0])
        if snap[0] != cur[0]:
            # Ziel: bisherige Adresse (Adresswechsel), außer Snapshot war noch 0 (erstes Speichern):
            # dann an die eingetragene neue ID senden (Gerät erwartet dort).
            id_dst = int(cur[0]) if int(snap[0]) == 0 else int(snap[0])
            r = c.sync_ui_command_response(id_dst, "SETCONTID", str(int(cur[0])), "ACK_SETCONTID")
            if not _sync_got_ack_value(r):
                all_ok = False
        for i in range(3):
            if snap[1 + i] != cur[1 + i] or self._controller_name_dirty[i]:
                cmd = f"SETCONANTNAME{i + 1}"
                r = c.sync_ui_command_response(dst, cmd, cur[1 + i], f"ACK_{cmd}")
                if not _sync_got_ack_value(r):
                    all_ok = False
        if snap[4] != cur[4] or self._controller_pwm_dirty[0]:
            r = c.sync_ui_command_response(dst, "SETCONSPWM", str(int(cur[4])), "ACK_SETCONSPWM")
            if not _sync_got_ack_value(r):
                all_ok = False
        if snap[5] != cur[5] or self._controller_pwm_dirty[1]:
            r = c.sync_ui_command_response(dst, "SETCONFPWM", str(int(cur[5])), "ACK_SETCONFPWM")
            if not _sync_got_ack_value(r):
                all_ok = False
        if snap[6] != cur[6] or self._controller_beep_dirty[0]:
            r = c.sync_ui_command_response(dst, "SETCONFRQ", str(int(cur[6])), "ACK_SETCONFRQ")
            if not _sync_got_ack_value(r):
                all_ok = False
        if snap[7] != cur[7] or self._controller_beep_dirty[1]:
            r = c.sync_ui_command_response(dst, "SETLSL", str(int(cur[7])), "ACK_SETLSL")
            if not _sync_got_ack_value(r):
                all_ok = False
        if snap[8] != cur[8]:
            r = c.sync_ui_command_response(dst, "SETCONLEDP", str(int(cur[8])), "ACK_SETCONLEDP")
            if not _sync_got_ack_value(r):
                all_ok = False
        if snap[9] != cur[9] or self._controller_anemo_dirty:
            r = c.sync_ui_command_response(dst, "SETCONANO", str(int(cur[9])), "ACK_SETCONANO")
            if not _sync_got_ack_value(r):
                all_ok = False
        if snap[10] != cur[10] or self._controller_delta_dirty:
            r = c.sync_ui_command_response(dst, "SETCONDELTA", str(int(cur[10])), "ACK_SETCONDELTA")
            if not _sync_got_ack_value(r):
                all_ok = False
        if snap[11] != cur[11] or self._controller_cha_dirty:
            r = c.sync_ui_command_response(dst, "SETCONCHA", str(int(cur[11])), "ACK_SETCONCHA")
            if not _sync_got_ack_value(r):
                all_ok = False
        if all_ok:
            self._snapshot_controller = cur
            self._clear_controller_field_dirty()
        return all_ok

    def _on_broadcast_setconidf(self) -> None:
        """SETCONIDF an Broadcast 255: neue Controller-ID (Wert aus dem Feld)."""
        if not self._controller_hw_enabled():
            QMessageBox.information(
                self,
                t("settings.title"),
                t("settings.controller_broadcast_disabled"),
            )
            return
        if not self.hw.is_connected():
            QMessageBox.information(
                self,
                t("settings.title"),
                t("settings.controller_setconidf_offline"),
            )
            return
        mid = int(self.sp_master.value())
        cid = int(self.sp_controller_id.value())
        if cid == mid:
            QMessageBox.warning(
                self,
                t("settings.title"),
                t("settings.controller_err_same_as_master", mid=mid),
            )
            return
        if not hasattr(self.ctrl, "broadcast_setconidf"):
            return
        self.ctrl.broadcast_setconidf(cid)
        ch = self.cfg.setdefault("controller_hw", {})
        ch["cont_id_configured"] = True
        ch["cont_id"] = int(cid)
        self.lbl_status.setText(t("settings.controller_setconidf_sent"))
        QApplication.processEvents()
        # Kurze Pause, dann alle Controller-Werte wie beim Öffnen der Seite vom Bus lesen
        QTimer.singleShot(250, self._load_controller_from_bus)

    def _save_and_close(self):
        """Speichern (inkl. Antennen-Versätze), Status anzeigen, dann Fenster schließen."""
        if not self._save_clicked():
            return
        self.lbl_status.setText(t("settings.status_closing"))
        QApplication.processEvents()
        QTimer.singleShot(600, self.close)
