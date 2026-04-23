"""UI-Tab für Rig-Bridge-Einstellungen."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QDoubleValidator, QIntValidator
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
    QLineEdit,
)

from ..i18n import format_tooltip, t
from ..ports import list_serial_port_entries
from ..rig_bridge.cat_commands import normalize_com_port
from ..rig_bridge.manager import RigBridgeManager

# Defaults wenn rig_bridge in der JSON fehlt oder Felder fehlen (an RigBridgeConfig angeglichen)
_RIG_DEF_TIMEOUT_S = 0.2
_RIG_DEF_POLL_MS = 30
_RIG_DEF_CAT_DRAIN_MS = 50
_RIG_DEF_SETFREQ_GAP_MS = 10
from .led_widget import Led
from .ui_utils import px_to_dip

# Gängige UART-Baudraten (wie typische COM-Dialoge)
_BAUD_RATES = (
    300,
    600,
    1200,
    2400,
    4800,
    9600,
    14400,
    19200,
    28800,
    38400,
    56000,
    57600,
    115200,
    128000,
    256000,
)


def _make_int_line_edit(parent: QWidget, lo: int, hi: int, width_dip: int = 0) -> QLineEdit:
    w = QLineEdit(parent)
    w.setValidator(QIntValidator(lo, hi, parent))
    if width_dip > 0:
        w.setFixedWidth(px_to_dip(parent, width_dip))
    return w


def _make_float_line_edit(parent: QWidget, lo: float, hi: float, decimals: int, width_dip: int = 0) -> QLineEdit:
    w = QLineEdit(parent)
    v = QDoubleValidator(lo, hi, decimals, parent)
    v.setNotation(QDoubleValidator.Notation.StandardNotation)
    w.setValidator(v)
    if width_dip > 0:
        w.setFixedWidth(px_to_dip(parent, width_dip))
    return w


class RigBridgeTab(QWidget):
    """Kompletter Einstellungen-Tab für Rig-Bridge."""

    @staticmethod
    def _int_from_field(text: str, default: int, lo: int, hi: int) -> int:
        try:
            v = int(text.strip())
        except ValueError:
            return default
        return max(lo, min(hi, v))

    @staticmethod
    def _float_from_field(text: str, default: float, lo: float, hi: float) -> float:
        try:
            v = float(text.replace(",", ".").strip())
        except ValueError:
            return default
        return max(lo, min(hi, v))

    def _set_baud_combo(self, baud: int) -> None:
        """Baudrate in der Liste wählen; unübliche gespeicherte Werte als zusätzlichen Eintrag."""
        try:
            baud = int(baud)
        except (TypeError, ValueError):
            baud = 9600
        baud = max(300, min(921600, baud))
        for i in range(self.cb_baud.count()):
            if int(self.cb_baud.itemData(i)) == baud:
                self.cb_baud.setCurrentIndex(i)
                return
        self.cb_baud.addItem(str(baud), baud)
        self.cb_baud.setCurrentIndex(self.cb_baud.count() - 1)

    def __init__(self, cfg: dict, manager: RigBridgeManager, save_cfg_cb, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.manager = manager
        self.save_cfg_cb = save_cfg_cb
        # Liste aller Profil-Dicts (Reihenfolge = Anzeigereihenfolge).
        self._profiles: list[dict] = []
        # Profil-ID, das gerade im Formular editiert wird.
        self._selected_id: str = ""
        # Profil-ID, das als "aktiv" markiert ist (nutzt die COM-CAT-Strecke).
        self._active_id: str = ""
        # Guard gegen reentrante Loads, waehrend wir zwischen Profilen wechseln.
        self._switching_profile: bool = False
        self._build_ui()
        self._load_from_config()
        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self.refresh_status)
        self._timer.start()

    def _build_ui(self) -> None:
        main = QVBoxLayout(self)
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(10)

        # Profile-Verwaltung oben: Liste + Buttons. Das rechte Formular
        # arbeitet immer auf dem in der Liste ausgewaehlten Profil.
        gb_profiles = QGroupBox(t("rig_bridge.group_profiles"))
        hl_profiles = QHBoxLayout(gb_profiles)
        self.lst_profiles = QListWidget()
        self.lst_profiles.setMaximumHeight(px_to_dip(self, 110))
        self.lst_profiles.setMinimumWidth(px_to_dip(self, 180))
        self.lst_profiles.currentRowChanged.connect(self._on_profile_selected)
        hl_profiles.addWidget(self.lst_profiles, 1)
        btn_col = QWidget()
        vl_btn = QVBoxLayout(btn_col)
        vl_btn.setContentsMargins(0, 0, 0, 0)
        vl_btn.setSpacing(4)
        self.btn_profile_new = QPushButton(t("rig_bridge.btn_new"))
        self.btn_profile_rename = QPushButton(t("rig_bridge.btn_rename"))
        self.btn_profile_del = QPushButton(t("rig_bridge.btn_delete"))
        self.btn_profile_active = QPushButton(t("rig_bridge.btn_set_active"))
        for b in (self.btn_profile_new, self.btn_profile_rename, self.btn_profile_del, self.btn_profile_active):
            vl_btn.addWidget(b)
        vl_btn.addStretch(1)
        hl_profiles.addWidget(btn_col, 0)
        self.btn_profile_new.clicked.connect(self._on_profile_new)
        self.btn_profile_rename.clicked.connect(self._on_profile_rename)
        self.btn_profile_del.clicked.connect(self._on_profile_delete)
        self.btn_profile_active.clicked.connect(self._on_profile_set_active)
        main.addWidget(gb_profiles)

        gb_general = QGroupBox(t("rig.group_general"))
        fl_general = QFormLayout(gb_general)
        self.chk_enabled = QCheckBox(t("rig.enabled"))
        self.chk_enabled.setToolTip(format_tooltip(t("rig.enabled_tooltip")))
        self.cb_rig_brand = QComboBox()
        self.cb_rig_brand.setToolTip(format_tooltip(t("rig.radio_brand_tooltip")))
        self.cb_rig_model = QComboBox()
        self.cb_rig_model.setToolTip(format_tooltip(t("rig.radio_model_tooltip")))
        self.lbl_rig_info = QLabel("-")
        self.lbl_rig_info.setToolTip(format_tooltip(t("rig.hamlib_model_id_tooltip")))
        self._hamlib_models: list[dict[str, str | int]] = []
        btn_row_general = QWidget()
        hl_general = QHBoxLayout(btn_row_general)
        hl_general.setContentsMargins(0, 0, 0, 0)
        self.btn_test = QPushButton(t("rig.btn_test"))
        self.btn_test.setToolTip(format_tooltip(t("rig.btn_test_tooltip")))
        self.btn_test.setMinimumWidth(170)
        self.btn_test.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        hl_general.addWidget(self.btn_test)
        hl_general.addStretch(1)
        fl_general.addRow(self.chk_enabled)
        fl_general.addRow(t("rig.radio_brand"), self.cb_rig_brand)
        fl_general.addRow(t("rig.radio_model"), self.cb_rig_model)
        fl_general.addRow(t("rig.hamlib_model_id"), self.lbl_rig_info)
        fl_general.addRow(btn_row_general)

        self.led_radio = Led(14, self)
        self.lbl_status = QLabel("-")
        self.lbl_error = QLabel("-")
        self.lbl_last = QLabel("-")
        self.lbl_com = QLabel("-")

        gb_serial = QGroupBox(t("rig.group_serial"))
        outer_serial = QVBoxLayout(gb_serial)
        conn_row = QWidget()
        conn_l = QHBoxLayout(conn_row)
        conn_l.setContentsMargins(0, 0, 0, 0)
        conn_l.addWidget(QLabel(t("rig.status_connection")), 0, Qt.AlignmentFlag.AlignLeft)
        conn_l.addWidget(self.led_radio, 0, Qt.AlignmentFlag.AlignLeft)
        conn_l.addWidget(self.lbl_status, 1)
        outer_serial.addWidget(conn_row)
        self.chk_auto_connect = QCheckBox(t("rig.auto_connect"))
        self.chk_auto_connect.setToolTip(format_tooltip(t("rig.auto_connect_tooltip")))
        self.chk_auto_reconnect = QCheckBox(t("rig.auto_reconnect"))
        self.chk_auto_reconnect.setToolTip(format_tooltip(t("rig.auto_reconnect_tooltip")))
        outer_serial.addWidget(self.chk_auto_connect)
        outer_serial.addWidget(self.chk_auto_reconnect)
        grid_serial = QGridLayout()
        outer_serial.addLayout(grid_serial)
        self.cb_com = QComboBox()
        self.cb_com.setEditable(False)
        self.cb_com.view().setMouseTracking(True)
        self.cb_com.setToolTip(format_tooltip(t("rig.com_port_tooltip")))
        self.btn_refresh_com = QPushButton("\u21bb")
        self.btn_refresh_com.setToolTip(format_tooltip(t("rig.com_refresh_tooltip")))
        self.btn_refresh_com.setFixedWidth(px_to_dip(self, 34))
        _nw = 88
        self.cb_baud = QComboBox()
        for br in _BAUD_RATES:
            self.cb_baud.addItem(str(br), br)
        self.cb_baud.setFixedWidth(px_to_dip(self, _nw))
        self.cb_baud.setToolTip(format_tooltip(t("rig.baud_tooltip")))
        self.lbl_serial_frame = QLabel(t("rig.serial_frame"))
        self.lbl_serial_frame.setWordWrap(True)
        self.lbl_serial_frame.setToolTip(format_tooltip(t("rig.serial_frame_tooltip")))
        self.ed_timeout = _make_float_line_edit(self, 0.05, 10.0, 2, _nw)
        self.ed_timeout.setToolTip(format_tooltip(t("rig.timeout_tooltip")))
        self.ed_poll = _make_int_line_edit(self, 30, 5000, _nw)
        self.ed_poll.setToolTip(format_tooltip(t("rig.poll_tooltip")))
        self.ed_cat_drain = _make_int_line_edit(self, 20, 500, _nw)
        self.ed_cat_drain.setToolTip(format_tooltip(t("rig.cat_drain_tooltip")))
        self.ed_setfreq_gap = _make_int_line_edit(self, 0, 200, _nw)
        self.ed_setfreq_gap.setToolTip(format_tooltip(t("rig.setfreq_gap_tooltip")))
        self.btn_connect = QPushButton(t("rig.btn_connect"))
        self.btn_connect.setToolTip(format_tooltip(t("rig.btn_connect_tooltip")))
        self.btn_disconnect = QPushButton(t("rig.btn_disconnect"))
        self.btn_disconnect.setToolTip(format_tooltip(t("rig.btn_disconnect_tooltip")))
        btn_serial_col = QWidget()
        hl_btn_serial = QHBoxLayout(btn_serial_col)
        hl_btn_serial.setContentsMargins(0, 0, 0, 0)
        hl_btn_serial.setSpacing(8)
        hl_btn_serial.addWidget(self.btn_connect, 0)
        hl_btn_serial.addWidget(self.btn_disconnect, 0)
        hl_btn_serial.addStretch(1)
        grid_serial.addWidget(QLabel(t("rig.com_port_label")), 0, 0)
        grid_serial.addWidget(self.cb_com, 0, 1)
        grid_serial.addWidget(self.btn_refresh_com, 0, 2)
        grid_serial.addWidget(QLabel(t("rig.baud_label")), 1, 0)
        grid_serial.addWidget(self.cb_baud, 1, 1)
        grid_serial.addWidget(self.lbl_serial_frame, 2, 0, 1, 3)
        grid_serial.addWidget(QLabel(t("rig.timeout_label")), 3, 0)
        grid_serial.addWidget(self.ed_timeout, 3, 1)
        grid_serial.addWidget(QLabel(t("rig.poll_label")), 4, 0)
        grid_serial.addWidget(self.ed_poll, 4, 1)
        grid_serial.addWidget(QLabel(t("rig.cat_drain_label")), 5, 0)
        grid_serial.addWidget(self.ed_cat_drain, 5, 1)
        grid_serial.addWidget(QLabel(t("rig.setfreq_gap_label")), 6, 0)
        grid_serial.addWidget(self.ed_setfreq_gap, 6, 1)
        grid_serial.addWidget(btn_serial_col, 7, 1, 1, 2)

        gb_protocols = QGroupBox(t("rig.group_protocols"))
        vl_protocols = QVBoxLayout(gb_protocols)
        self._protocol_leds: dict[str, Led] = {}
        self._protocol_client_led: dict[str, Led] = {}
        self._protocol_enabled: dict[str, QCheckBox] = {}
        self._protocol_host: dict[str, QLineEdit] = {}
        self._protocol_port: dict[str, QLineEdit] = {}
        self._protocol_autostart: dict[str, QCheckBox] = {}
        self._protocol_start: dict[str, QPushButton] = {}
        self._protocol_stop: dict[str, QPushButton] = {}

        for name in ("flrig",):
            title = t("rig.flrig_title")
            gb_single = QGroupBox(title)
            fl_single = QFormLayout(gb_single)
            chk = QCheckBox(t("rig.flrig_active"))
            chk.setToolTip(format_tooltip(t("rig.flrig_active_tooltip")))
            host = QLineEdit()
            host.setFixedWidth(px_to_dip(self, 100))
            host.setToolTip(format_tooltip(t("rig.flrig_host_tooltip")))
            port = QLineEdit()
            port.setValidator(QIntValidator(1, 65535, self))
            port.setFixedWidth(px_to_dip(self, 56))
            port.setToolTip(format_tooltip(t("rig.flrig_port_tooltip")))
            chk_auto = QCheckBox(t("rig.flrig_autostart"))
            chk_auto.setToolTip(format_tooltip(t("rig.flrig_autostart_tooltip")))
            self.chk_flrig_log_tcp = QCheckBox(t("rig.flrig_log_tcp"))
            self.chk_flrig_log_tcp.setToolTip(format_tooltip(t("rig.flrig_log_tcp_tooltip")))
            led = Led(12, self)
            self._lbl_flrig_bind_clients = QLabel("")
            self._lbl_flrig_bind_clients.setWordWrap(True)
            cli_led = Led(12, self)
            btn_start = QPushButton(t("rig.flrig_btn_start"))
            btn_start.setToolTip(format_tooltip(t("rig.flrig_btn_start_tooltip")))
            btn_stop = QPushButton(t("rig.flrig_btn_stop"))
            btn_stop.setToolTip(format_tooltip(t("rig.flrig_btn_stop_tooltip")))
            row_host_port = QWidget()
            hl_host_port = QHBoxLayout(row_host_port)
            hl_host_port.setContentsMargins(0, 0, 0, 0)
            hl_host_port.setSpacing(8)
            hl_host_port.addWidget(QLabel(t("rig.lbl_host")))
            hl_host_port.addWidget(host, 1)
            hl_host_port.addWidget(QLabel(t("rig.lbl_port")))
            hl_host_port.addWidget(port, 0)
            row_status = QWidget()
            hl_status = QHBoxLayout(row_status)
            hl_status.setContentsMargins(0, 0, 0, 0)
            hl_status.setSpacing(8)
            hl_status.addWidget(QLabel(t("rig.lbl_status")))
            hl_status.addWidget(led, 0, Qt.AlignmentFlag.AlignLeft)
            hl_status.addSpacing(10)
            hl_status.addWidget(self._lbl_flrig_bind_clients, 1)
            hl_status.addWidget(cli_led, 0, Qt.AlignmentFlag.AlignLeft)
            hl_status.addStretch(1)
            hl_status.addWidget(btn_start, 0)
            hl_status.addWidget(btn_stop, 0)
            self._protocol_enabled[name] = chk
            self._protocol_host[name] = host
            self._protocol_port[name] = port
            self._protocol_autostart[name] = chk_auto
            self._protocol_leds[name] = led
            self._protocol_client_led[name] = cli_led
            self._protocol_start[name] = btn_start
            self._protocol_stop[name] = btn_stop
            fl_single.addRow(chk)
            fl_single.addRow(row_host_port)
            fl_single.addRow(chk_auto)
            fl_single.addRow(self.chk_flrig_log_tcp)
            fl_single.addRow(row_status)
            vl_protocols.addWidget(gb_single)

        gb_hamlib = QGroupBox(t("rig.group_hamlib"))
        fl_hamlib = QFormLayout(gb_hamlib)
        self._protocol_enabled["hamlib"] = QCheckBox(t("rig.hamlib_active"))
        self._protocol_enabled["hamlib"].setToolTip(format_tooltip(t("rig.hamlib_active_tooltip")))
        self._hamlib_host = QLineEdit()
        self._hamlib_host.setFixedWidth(px_to_dip(self, 100))
        self._hamlib_host.setToolTip(format_tooltip(t("rig.hamlib_host_tooltip")))
        row_hamlib_host = QWidget()
        hl_hh = QHBoxLayout(row_hamlib_host)
        hl_hh.setContentsMargins(0, 0, 0, 0)
        hl_hh.setSpacing(8)
        hl_hh.addWidget(QLabel(t("rig.lbl_host")))
        hl_hh.addWidget(self._hamlib_host, 1)
        self._hamlib_rows_box = QWidget()
        self._hamlib_rows_layout = QVBoxLayout(self._hamlib_rows_box)
        self._hamlib_rows_layout.setContentsMargins(0, 0, 0, 0)
        self._hamlib_rows_layout.setSpacing(6)
        self._hamlib_rows: list[tuple[QLineEdit, QLineEdit, QLabel, QLabel, Led, QWidget]] = []
        self.btn_hamlib_add_row = QPushButton(t("rig.hamlib_btn_add_row"))
        self.btn_hamlib_add_row.setToolTip(format_tooltip(t("rig.hamlib_btn_add_row_tooltip")))
        self.btn_hamlib_add_row.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.btn_hamlib_add_row.clicked.connect(lambda: self._hamlib_add_row("", ""))
        self._protocol_autostart["hamlib"] = QCheckBox(t("rig.hamlib_autostart"))
        self._protocol_autostart["hamlib"].setToolTip(format_tooltip(t("rig.hamlib_autostart_tooltip")))
        self.chk_hamlib_debug = QCheckBox(t("rig.hamlib_debug"))
        self.chk_hamlib_debug.setToolTip(format_tooltip(t("rig.hamlib_debug_tooltip")))
        self.chk_hamlib_log_tcp = QCheckBox(t("rig.hamlib_log_tcp"))
        self.chk_hamlib_log_tcp.setToolTip(format_tooltip(t("rig.hamlib_log_tcp_tooltip")))
        self._protocol_leds["hamlib"] = Led(12, self)
        self._lbl_hamlib_bind_clients = QLabel("")
        self._lbl_hamlib_bind_clients.setWordWrap(False)
        self._protocol_start["hamlib"] = QPushButton(t("rig.hamlib_btn_start"))
        self._protocol_start["hamlib"].setToolTip(format_tooltip(t("rig.hamlib_btn_start_tooltip")))
        self._protocol_stop["hamlib"] = QPushButton(t("rig.hamlib_btn_stop"))
        self._protocol_stop["hamlib"].setToolTip(format_tooltip(t("rig.hamlib_btn_stop_tooltip")))
        row_hamlib_status = QWidget()
        hl_hs = QHBoxLayout(row_hamlib_status)
        hl_hs.setContentsMargins(0, 0, 0, 0)
        hl_hs.setSpacing(8)
        hl_hs.addWidget(QLabel(t("rig.lbl_status")))
        hl_hs.addWidget(self._protocol_leds["hamlib"], 0, Qt.AlignmentFlag.AlignLeft)
        hl_hs.addSpacing(10)
        hl_hs.addWidget(self._lbl_hamlib_bind_clients, 1)
        hl_hs.addStretch(1)
        hl_hs.addWidget(self._protocol_start["hamlib"], 0)
        hl_hs.addWidget(self._protocol_stop["hamlib"], 0)
        fl_hamlib.addRow(self._protocol_enabled["hamlib"])
        fl_hamlib.addRow(row_hamlib_host)
        lbl_hamlib_ports = QLabel(t("rig.hamlib_ports_header"))
        lbl_hamlib_ports.setWordWrap(True)
        lbl_hamlib_ports.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        fl_hamlib.addRow(lbl_hamlib_ports)
        fl_hamlib.addRow(self._hamlib_rows_box)
        row_hamlib_add = QWidget()
        hl_hamlib_add = QHBoxLayout(row_hamlib_add)
        hl_hamlib_add.setContentsMargins(0, 0, 0, 0)
        hl_hamlib_add.addWidget(self.btn_hamlib_add_row)
        hl_hamlib_add.addStretch(1)
        fl_hamlib.addRow(row_hamlib_add)
        fl_hamlib.addRow(self._protocol_autostart["hamlib"])
        fl_hamlib.addRow(self.chk_hamlib_debug)
        fl_hamlib.addRow(self.chk_hamlib_log_tcp)
        fl_hamlib.addRow(row_hamlib_status)
        vl_protocols.addWidget(gb_hamlib)

        main.addWidget(gb_general)
        main.addWidget(gb_serial)
        main.addWidget(gb_protocols, 1)

        self.btn_refresh_com.clicked.connect(self._refresh_com_ports)
        self.cb_com.currentIndexChanged.connect(self._on_com_selection_changed)
        self.btn_connect.clicked.connect(self._on_connect)
        self.btn_disconnect.clicked.connect(self._on_disconnect)
        self.btn_test.clicked.connect(self._on_test)
        self.cb_rig_brand.currentIndexChanged.connect(self._on_brand_changed)
        self.cb_rig_model.currentIndexChanged.connect(self._update_rig_info_label)
        self.chk_enabled.stateChanged.connect(self.apply_to_manager)
        self.ed_cat_drain.editingFinished.connect(self.apply_to_manager)
        self.ed_setfreq_gap.editingFinished.connect(self.apply_to_manager)
        self.chk_auto_connect.stateChanged.connect(self.apply_to_manager)
        self.chk_auto_reconnect.stateChanged.connect(self.apply_to_manager)
        self.chk_hamlib_debug.stateChanged.connect(self.apply_to_manager)
        self.chk_hamlib_log_tcp.stateChanged.connect(self.apply_to_manager)
        self.chk_flrig_log_tcp.stateChanged.connect(self.apply_to_manager)
        self._wire_protocol_buttons()

    def _rig_combo_apply_max_width(self) -> None:
        """Marke/Modell-Combos etwas schmaler (ca. 100 px unter natürlicher Breite)."""
        for cb in (self.cb_rig_brand, self.cb_rig_model):
            try:
                w = int(cb.sizeHint().width())
            except Exception:
                w = 0
            if w > 160:
                cb.setMaximumWidth(max(160, w - 100))

    def _wire_protocol_buttons(self) -> None:
        self._protocol_start["flrig"].clicked.connect(lambda: self._start_protocol("flrig"))
        self._protocol_stop["flrig"].clicked.connect(lambda: self._stop_protocol("flrig"))
        self._protocol_start["hamlib"].clicked.connect(lambda: self._start_protocol("hamlib"))
        self._protocol_stop["hamlib"].clicked.connect(lambda: self._stop_protocol("hamlib"))

    def _hamlib_led_wrap(self, parent: QWidget, led: Led) -> QWidget:
        w = QWidget(parent)
        vl = QVBoxLayout(w)
        vl.setContentsMargins(0, 2, 0, 0)
        vl.addWidget(led)
        return w

    def _hamlib_clear_rows(self) -> None:
        for _ed_p, _ed_n, _lbl_hp, _lbl_n, _cli_led, row_w in self._hamlib_rows:
            self._hamlib_rows_layout.removeWidget(row_w)
            row_w.deleteLater()
        self._hamlib_rows.clear()

    def _hamlib_next_free_port(self, default: int = 4532) -> int:
        """Ermittelt den naechsten freien rigctl-Port basierend auf den bereits
        angelegten Zeilen. Erste Zeile -> ``default`` (4532), danach max+1."""
        used: list[int] = []
        for ed_p, _ed_n, _lhp, _ln, _cli, _w in self._hamlib_rows:
            try:
                p = int(ed_p.text().strip())
            except (TypeError, ValueError):
                continue
            if 1 <= p <= 65535:
                used.append(p)
        if not used:
            return default
        nxt = max(used) + 1
        return min(65535, max(1, nxt))

    def _hamlib_add_row(self, port_text: str, name_text: str) -> None:
        if not port_text:
            port_text = str(self._hamlib_next_free_port())
        row_w = QWidget()
        hl = QHBoxLayout(row_w)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(8)
        ed_port = QLineEdit()
        ed_port.setPlaceholderText(t("rig.hamlib_port_placeholder"))
        ed_port.setFixedWidth(px_to_dip(self, 56))
        ed_port.setText(port_text)
        ed_port.setToolTip(format_tooltip(t("rig.hamlib_port_tooltip")))
        ed_name = QLineEdit()
        ed_name.setPlaceholderText(t("rig.hamlib_name_placeholder"))
        ed_name.setFixedWidth(px_to_dip(self, 150))
        ed_name.setText(name_text)
        ed_name.setToolTip(format_tooltip(t("rig.hamlib_name_tooltip")))
        led_d = px_to_dip(self, 12)
        lbl_hp = QLabel("")
        lbl_hp.setWordWrap(False)
        lbl_hp.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        lbl_hp.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        lbl_n = QLabel("")
        lbl_n.setWordWrap(False)
        lbl_n.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        lbl_n.setMinimumWidth(px_to_dip(self, 72))
        lbl_n.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        cli_led = Led(led_d, self)
        btn_del = QPushButton(t("rig.hamlib_btn_del"))
        btn_del.setToolTip(format_tooltip(t("rig.hamlib_btn_del_tooltip")))
        hl.addWidget(ed_port, 0)
        hl.addWidget(ed_name, 0)
        hl.addWidget(lbl_hp, 0)
        hl.addWidget(lbl_n, 0)
        hl.addWidget(self._hamlib_led_wrap(row_w, cli_led), 0, Qt.AlignmentFlag.AlignLeft)
        hl.addWidget(btn_del, 0)

        def _remove() -> None:
            self._hamlib_remove_row(row_w)

        btn_del.clicked.connect(_remove)
        self._hamlib_rows_layout.addWidget(row_w)
        self._hamlib_rows.append((ed_port, ed_name, lbl_hp, lbl_n, cli_led, row_w))

    def _hamlib_remove_row(self, row_w: QWidget) -> None:
        for i, (_p, _n, _lhp, _ln, _cl, w) in enumerate(self._hamlib_rows):
            if w is row_w:
                self._hamlib_rows_layout.removeWidget(row_w)
                row_w.deleteLater()
                del self._hamlib_rows[i]
                return

    def _hamlib_listeners_to_config(self) -> list[dict]:
        out: list[dict] = []
        for ed_p, ed_n, _, _, _, _ in self._hamlib_rows:
            pt = ed_p.text().strip()
            nm = ed_n.text().strip()
            if not pt:
                out.append({"name": nm})
            else:
                try:
                    p = int(pt)
                except ValueError:
                    continue
                p = max(1, min(65535, p))
                out.append({"port": p, "name": nm})
        return out

    # --------------------------------------------------- Profile-Verwaltung
    def _refresh_profiles_list(self) -> None:
        """Listen-Widget synchron zu ``self._profiles`` + Aktiv-Marker.

        Der aktive Eintrag wird zusaetzlich beschriftet (Marker aus i18n).
        """
        self.lst_profiles.blockSignals(True)
        self.lst_profiles.clear()
        marker = t("rig_bridge.active_marker")
        for pr in self._profiles:
            name = str(pr.get("name", "") or pr.get("id", ""))
            if str(pr.get("id", "")) == self._active_id:
                label = f"{name} {marker}".strip()
            else:
                label = name
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, str(pr.get("id", "")))
            self.lst_profiles.addItem(item)
        # Auswahl auf _selected_id wiederherstellen, sonst erstes Element.
        idx = 0
        for i in range(self.lst_profiles.count()):
            if self.lst_profiles.item(i).data(Qt.ItemDataRole.UserRole) == self._selected_id:
                idx = i
                break
        self.lst_profiles.setCurrentRow(idx)
        self.lst_profiles.blockSignals(False)

    def _on_profile_selected(self, row: int) -> None:
        if self._switching_profile or row < 0 or row >= self.lst_profiles.count():
            return
        new_id = self.lst_profiles.item(row).data(Qt.ItemDataRole.UserRole)
        if not isinstance(new_id, str) or new_id == self._selected_id:
            return
        # Aktuelles Formular einfrieren und ins bisherige Profil speichern.
        self._capture_form_into_profile(self._selected_id)
        self._selected_id = new_id
        self._load_selected_profile_into_form()

    def _capture_form_into_profile(self, rig_id: str) -> None:
        """Form-Inhalt ins Profil-Dict ``rig_id`` zurueckschreiben."""
        if not rig_id:
            return
        prof = self._find_profile(rig_id)
        if prof is None:
            return
        form = self._form_to_profile_dict()
        form["id"] = prof.get("id", rig_id)
        form["name"] = prof.get("name", form.get("selected_rig", "") or rig_id)
        prof.update(form)

    def _load_selected_profile_into_form(self) -> None:
        prof = self._find_profile(self._selected_id)
        if prof is None:
            return
        self._switching_profile = True
        try:
            self._rig_loading_cfg = True
            try:
                self._load_profile_dict_into_form(prof)
            finally:
                self._rig_loading_cfg = False
        finally:
            self._switching_profile = False
        self.refresh_status()

    def _find_profile(self, rig_id: str) -> dict | None:
        for p in self._profiles:
            if str(p.get("id", "")) == str(rig_id):
                return p
        return None

    def _make_unique_id(self, base: str) -> str:
        existing = {str(p.get("id", "")) for p in self._profiles}
        base = (base or "rig").strip() or "rig"
        if base not in existing:
            return base
        i = 2
        while f"{base}_{i}" in existing:
            i += 1
        return f"{base}_{i}"

    def _on_profile_new(self) -> None:
        self._capture_form_into_profile(self._selected_id)
        new_id = self._make_unique_id("rig")
        new_name, ok = QInputDialog.getText(
            self, t("rig_bridge.btn_new"), t("rig_bridge.new_name_prompt"), text=f"Rig {len(self._profiles) + 1}"
        )
        if not ok:
            return
        new_name = (new_name or "").strip() or f"Rig {len(self._profiles) + 1}"
        prof = {
            "id": new_id,
            "name": new_name,
            "enabled": True,
            "selected_rig": "Generic CAT",
            "rig_brand": "Generisch",
            "rig_model": "CAT (generisch)",
            "hamlib_rig_id": 0,
            "com_port": "",
            "baudrate": 9600,
            "databits": 8,
            "stopbits": 1,
            "parity": "N",
            "timeout_s": 0.2,
            "polling_interval_ms": 30,
            "auto_connect": False,
            "auto_reconnect": True,
            "log_serial_traffic": True,
            "cat_post_write_drain_ms": 50,
            "setfreq_gap_ms": 10,
        }
        self._profiles.append(prof)
        self._selected_id = new_id
        self._refresh_profiles_list()
        self._load_selected_profile_into_form()
        self.apply_to_manager()

    def _on_profile_rename(self) -> None:
        self._capture_form_into_profile(self._selected_id)
        prof = self._find_profile(self._selected_id)
        if prof is None:
            return
        current = str(prof.get("name", "") or prof.get("id", ""))
        new_name, ok = QInputDialog.getText(
            self,
            t("rig_bridge.btn_rename"),
            t("rig_bridge.rename_prompt"),
            text=current,
        )
        if not ok:
            return
        new_name = (new_name or "").strip()
        if not new_name or new_name == current:
            return
        prof["name"] = new_name
        self._refresh_profiles_list()
        self.apply_to_manager()

    def _on_profile_delete(self) -> None:
        if len(self._profiles) <= 1:
            QMessageBox.information(
                self, t("rig_bridge.btn_delete"), t("rig_bridge.cannot_delete_last")
            )
            return
        prof = self._find_profile(self._selected_id)
        if prof is None:
            return
        if (
            QMessageBox.question(
                self,
                t("rig_bridge.btn_delete"),
                t("rig_bridge.confirm_delete").replace("{name}", str(prof.get("name", ""))),
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        idx = self._profiles.index(prof)
        del self._profiles[idx]
        # Aktives Profil wurde geloescht? → erstes nehmen.
        if self._active_id == prof.get("id", ""):
            self._active_id = str(self._profiles[0].get("id", ""))
        # Neue Auswahl.
        new_idx = max(0, min(idx, len(self._profiles) - 1))
        self._selected_id = str(self._profiles[new_idx].get("id", ""))
        self._refresh_profiles_list()
        self._load_selected_profile_into_form()
        self.apply_to_manager()

    def _on_profile_set_active(self) -> None:
        self._capture_form_into_profile(self._selected_id)
        if not self._selected_id:
            return
        self._active_id = self._selected_id
        self._refresh_profiles_list()
        self.apply_to_manager()

    def _load_from_config(self) -> None:
        self._rig_loading_cfg = True
        try:
            rb = dict(self.cfg.get("rig_bridge", {}) or {})
            rigs_raw = rb.get("rigs")
            profiles: list[dict] = []
            if isinstance(rigs_raw, list) and rigs_raw:
                for pr in rigs_raw:
                    if isinstance(pr, dict):
                        p = dict(pr)
                        p.setdefault("id", f"rig_{len(profiles)}")
                        p.setdefault("name", str(p.get("selected_rig", "") or p["id"]))
                        p.setdefault("enabled", True)
                        # flrig/hamlib sind global — aus Profilen entfernen.
                        p.pop("flrig", None)
                        p.pop("hamlib", None)
                        p.pop("cat_tcp", None)
                        profiles.append(p)
            if not profiles:
                # Altform: flache Struktur → ein Profil bauen.
                flat = {
                    k: v
                    for k, v in rb.items()
                    if k not in ("rigs", "active_rig_id", "flrig", "hamlib", "cat_tcp")
                }
                flat.setdefault("id", "default")
                flat.setdefault("name", str(flat.get("selected_rig", "") or "Rig 1"))
                flat.setdefault("enabled", True)
                profiles = [flat]
            self._profiles = profiles
            active_id = str(rb.get("active_rig_id", "") or "")
            if not any(p.get("id") == active_id for p in profiles):
                active_id = str(profiles[0].get("id", ""))
            self._active_id = active_id
            self._selected_id = active_id
            # Globales rig_bridge.enabled → chk_enabled soll Rig-Bridge gesamt anzeigen.
            self._global_enabled = bool(rb.get("enabled", False))

            # Globale Flrig/Hamlib-Settings einmalig ins Formular uebernehmen
            # (profilunabhaengig). Fallback: noch in einem Profil vorhanden?
            global_flrig = dict(rb.get("flrig") or {})
            global_hamlib = dict(rb.get("hamlib") or {})
            if not global_flrig or not global_hamlib:
                donor = next(
                    (p for p in profiles if p.get("id") == active_id),
                    profiles[0] if profiles else {},
                )
                if not global_flrig and isinstance(donor.get("flrig"), dict):
                    global_flrig = dict(donor["flrig"])
                if not global_hamlib and isinstance(donor.get("hamlib"), dict):
                    global_hamlib = dict(donor["hamlib"])
            self._load_global_flrig_hamlib_into_form(global_flrig, global_hamlib)

            self._refresh_profiles_list()
            cur = self._find_profile(self._selected_id)
            if cur is None and profiles:
                cur = profiles[0]
                self._selected_id = str(cur.get("id", ""))
            if cur is not None:
                self._load_profile_dict_into_form(cur)
        finally:
            self._rig_loading_cfg = False
        self.refresh_status()

    def _load_global_flrig_hamlib_into_form(self, flrig: dict, hamlib: dict) -> None:
        """Globale Flrig-/Hamlib-Settings ins Formular uebertragen.

        Diese Werte sind NICHT profilabhaengig und werden deshalb nur beim
        Einlesen der Konfiguration (und nach Profilaenderungen nicht erneut)
        gesetzt.
        """
        fl = dict(flrig or {})
        hl = dict(hamlib or {})
        self._protocol_enabled["flrig"].setChecked(bool(fl.get("enabled", False)))
        self._protocol_host["flrig"].setText(str(fl.get("host", "127.0.0.1")))
        self._protocol_port["flrig"].setText(str(int(fl.get("port", 12345))))
        self._protocol_autostart["flrig"].setChecked(bool(fl.get("autostart", False)))
        self.chk_flrig_log_tcp.setChecked(bool(fl.get("log_tcp_traffic", True)))
        self._protocol_enabled["hamlib"].setChecked(bool(hl.get("enabled", False)))
        self._hamlib_host.setText(str(hl.get("host", "127.0.0.1")))
        self._hamlib_clear_rows()
        listeners = hl.get("listeners")
        if not listeners and "port" in hl:
            try:
                p = int(hl.get("port", 4532))
            except (TypeError, ValueError):
                p = 4532
            self._hamlib_add_row(str(max(1, min(65535, p))), "")
        elif isinstance(listeners, list) and len(listeners) > 0:
            for it in listeners:
                if not isinstance(it, dict):
                    continue
                nm = str(it.get("name", "") or "")
                if "port" not in it or it.get("port") in (None, ""):
                    self._hamlib_add_row("", nm)
                else:
                    try:
                        p = int(it["port"])
                    except (TypeError, ValueError):
                        continue
                    self._hamlib_add_row(str(max(1, min(65535, p))), nm)
        else:
            self._hamlib_add_row("", "")
        self._protocol_autostart["hamlib"].setChecked(bool(hl.get("autostart", False)))
        self.chk_hamlib_debug.setChecked(bool(hl.get("debug_traffic", False)))
        self.chk_hamlib_log_tcp.setChecked(bool(hl.get("log_tcp_traffic", False)))

    def _load_profile_dict_into_form(self, cfg: dict) -> None:
        """Formular mit den Werten des gegebenen Profil-Dicts fuellen.

        ``chk_enabled`` ist die GLOBALE ``rig_bridge.enabled``-Flagge und
        spiegelt ``self._global_enabled`` — sie ist damit unabhaengig vom
        gerade selektierten Profil. Die per-Profil-Aktivschaltung
        passiert ueber den Profilwechsel (``Aktiv setzen``/Combobox im
        Hauptfenster); Profile haben kein eigenes Deaktivieren mehr.
        """
        self.chk_enabled.blockSignals(True)
        self.chk_enabled.setChecked(bool(getattr(self, "_global_enabled", False)))
        self.chk_enabled.blockSignals(False)
        self._load_hamlib_models()
        self._populate_brand_combo()
        saved_brand = str(cfg.get("rig_brand", "")).strip()
        saved_model = str(cfg.get("rig_model", "")).strip()
        saved_rig_id = int(cfg.get("hamlib_rig_id", 0) or 0)
        if saved_brand:
            idx_brand = self.cb_rig_brand.findText(saved_brand)
            if idx_brand >= 0:
                self.cb_rig_brand.setCurrentIndex(idx_brand)
        self._on_brand_changed()
        if saved_model:
            idx_model = self.cb_rig_model.findText(saved_model)
            if idx_model >= 0:
                self.cb_rig_model.setCurrentIndex(idx_model)
        if saved_rig_id > 0:
            self._select_model_by_rig_id(saved_rig_id)
        self._update_rig_info_label()
        saved_com = normalize_com_port(str(cfg.get("com_port", "COM1") or ""))
        self._refresh_com_ports(preferred_port=saved_com)
        self._set_baud_combo(int(cfg.get("baudrate", 9600)))
        self.ed_timeout.setText(str(float(cfg.get("timeout_s", _RIG_DEF_TIMEOUT_S))))
        self.ed_poll.setText(str(int(cfg.get("polling_interval_ms", _RIG_DEF_POLL_MS))))
        self.ed_cat_drain.blockSignals(True)
        self.ed_cat_drain.setText(str(int(cfg.get("cat_post_write_drain_ms", _RIG_DEF_CAT_DRAIN_MS))))
        self.ed_cat_drain.blockSignals(False)
        self.ed_setfreq_gap.blockSignals(True)
        self.ed_setfreq_gap.setText(str(int(cfg.get("setfreq_gap_ms", _RIG_DEF_SETFREQ_GAP_MS))))
        self.ed_setfreq_gap.blockSignals(False)
        # flrig/hamlib sind global und werden nur aus _load_from_config
        # uebernommen — beim Profilwechsel bleiben sie unveraendert.
        # ``log_serial_traffic`` ist immer aktiv, solange Rig-Bridge laeuft —
        # es gibt keinen UI-Schalter mehr und die Profile tragen den Default.
        self.chk_auto_connect.blockSignals(True)
        self.chk_auto_reconnect.blockSignals(True)
        self.chk_auto_connect.setChecked(bool(cfg.get("auto_connect", False)))
        self.chk_auto_reconnect.setChecked(bool(cfg.get("auto_reconnect", True)))
        self.chk_auto_connect.blockSignals(False)
        self.chk_auto_reconnect.blockSignals(False)
        self._rig_combo_apply_max_width()

    def _form_to_profile_dict(self) -> dict:
        """Liest die Formularfelder in ein einzelnes Profil-Dict (ohne
        ``id``/``name``). ``chk_enabled`` steuert nicht mehr das Profil,
        sondern die globale Rig-Bridge — deshalb bleibt ``enabled`` hier
        immer True, damit das Profil jederzeit aktivierbar ist.
        """
        rig_id = int(self.cb_rig_model.currentData() or 0)
        rig_brand = self.cb_rig_brand.currentText().strip()
        rig_model = self.cb_rig_model.currentText().strip()
        selected_rig = f"{rig_brand} {rig_model}".strip()
        return {
            "enabled": True,
            "selected_rig": selected_rig,
            "rig_brand": rig_brand,
            "rig_model": rig_model,
            "hamlib_rig_id": rig_id,
            "com_port": self._com_port_from_combo(),
            "baudrate": int(self.cb_baud.currentData()) if self.cb_baud.currentData() is not None else 9600,
            "databits": 8,
            "stopbits": 1,
            "parity": "N",
            "timeout_s": self._float_from_field(self.ed_timeout.text(), _RIG_DEF_TIMEOUT_S, 0.05, 10.0),
            "polling_interval_ms": self._int_from_field(self.ed_poll.text(), _RIG_DEF_POLL_MS, 30, 5000),
            "cat_post_write_drain_ms": self._int_from_field(
                self.ed_cat_drain.text(), _RIG_DEF_CAT_DRAIN_MS, 20, 500
            ),
            "setfreq_gap_ms": self._int_from_field(self.ed_setfreq_gap.text(), _RIG_DEF_SETFREQ_GAP_MS, 0, 200),
            "auto_connect": bool(self.chk_auto_connect.isChecked()),
            "auto_reconnect": bool(self.chk_auto_reconnect.isChecked()),
            # Logging laeuft jetzt immer mit, solange die Rig-Bridge aktiv ist.
            "log_serial_traffic": True,
        }

    def _form_to_global_flrig(self) -> dict:
        """Flrig-Formular in das GLOBALE Flrig-Settings-Dict umwandeln."""
        return {
            "enabled": bool(self._protocol_enabled["flrig"].isChecked()),
            "host": self._protocol_host["flrig"].text().strip() or "127.0.0.1",
            "port": self._int_from_field(self._protocol_port["flrig"].text(), 12345, 1, 65535),
            "autostart": bool(self._protocol_autostart["flrig"].isChecked()),
            "log_tcp_traffic": bool(self.chk_flrig_log_tcp.isChecked()),
        }

    def _form_to_global_hamlib(self) -> dict:
        """Hamlib-Formular in das GLOBALE Hamlib-Settings-Dict umwandeln."""
        return {
            "enabled": bool(self._protocol_enabled["hamlib"].isChecked()),
            "host": self._hamlib_host.text().strip() or "127.0.0.1",
            "listeners": self._hamlib_listeners_to_config(),
            "autostart": bool(self._protocol_autostart["hamlib"].isChecked()),
            "debug_traffic": bool(self.chk_hamlib_debug.isChecked()),
            "log_tcp_traffic": bool(self.chk_hamlib_log_tcp.isChecked()),
        }

    def to_config(self) -> dict:
        """Komplettes ``rig_bridge``-Dict im neuen Profilformat bauen.

        Vor dem Export wird das aktuell editierte Profil aus dem Formular
        uebernommen, damit keine Aenderungen verloren gehen.
        """
        # Während ``_load_from_config`` feuern setChecked-Handler (Hamlib-Log
        # usw.) — ohne Abbruch würde ``_capture_form_into_profile`` das Profil aus
        # noch leeren Widgets in die JSON schreiben.
        if getattr(self, "_rig_loading_cfg", False):
            return dict(self.cfg.get("rig_bridge") or {})
        # Aktuelles Formular ins zugehoerige Profil schreiben und das
        # Global-Flag aus der Checkbox in ``_global_enabled`` spiegeln.
        self._capture_form_into_profile(self._selected_id)
        self._global_enabled = bool(self.chk_enabled.isChecked())
        rigs_out: list[dict] = []
        for pr in self._profiles:
            entry = dict(pr)
            entry.setdefault("id", f"rig_{len(rigs_out)}")
            entry.setdefault("name", str(entry.get("selected_rig", "") or entry["id"]))
            # flrig/hamlib sind global — aus Profilen fernhalten.
            entry.pop("flrig", None)
            entry.pop("hamlib", None)
            entry.pop("cat_tcp", None)
            rigs_out.append(entry)
        active = self._active_id or (str(rigs_out[0].get("id", "")) if rigs_out else "")
        if not any(p.get("id") == active for p in rigs_out) and rigs_out:
            active = str(rigs_out[0].get("id", ""))
            self._active_id = active
        return {
            "enabled": bool(self._global_enabled),
            "active_rig_id": active,
            "flrig": self._form_to_global_flrig(),
            "hamlib": self._form_to_global_hamlib(),
            "rigs": rigs_out,
        }

    def apply_to_manager(self) -> None:
        if getattr(self, "_rig_loading_cfg", False):
            return
        self.cfg["rig_bridge"] = self.to_config()
        self.manager.update_config(self.cfg["rig_bridge"])
        self.save_cfg_cb(self.cfg)

    def _on_com_selection_changed(self) -> None:
        """Bei anderem COM während aktiver Session: zuerst trennen, dann Konfiguration übernehmen."""
        if getattr(self, "_rig_loading_cfg", False):
            return
        try:
            st = self.manager.status_model()
            if st.radio_connected:
                new_port = normalize_com_port(self._com_port_from_combo()).upper()
                active = normalize_com_port(str(st.com_port or "")).upper()
                if new_port != active:
                    self.manager.disconnect_radio()
        except Exception:
            pass
        self.apply_to_manager()

    def _com_port_from_combo(self) -> str:
        """Öffnen immer über den Gerätenamen (UserRole), nicht über den Anzeige-Text mit Beschreibung."""
        d = self.cb_com.currentData()
        if d is not None and str(d).strip():
            return normalize_com_port(str(d).strip())
        return normalize_com_port(self.cb_com.currentText().strip())

    def _refresh_com_ports(self, preferred_port: str | None = None) -> None:
        """Portliste neu füllen. ``preferred_port``: gespeicherter COM aus der Konfiguration
        (wird normalisiert und per exakter Auswahl gesetzt; fehlt der Port im System, als Eintrag ergänzen).
        Ohne Argument: vorher gewählter Eintrag bleibt erhalten, sofern noch vorhanden."""
        prev_data = self.cb_com.currentData()
        prev = (
            str(prev_data).strip()
            if prev_data is not None and str(prev_data).strip()
            else self.cb_com.currentText().strip()
        )
        self.cb_com.blockSignals(True)
        self.cb_com.clear()
        for device, tip in list_serial_port_entries():
            self.cb_com.addItem(device, userData=device)
            if tip:
                i = self.cb_com.count() - 1
                self.cb_com.setItemData(i, tip, Qt.ItemDataRole.ToolTipRole)
        want_raw = preferred_port if preferred_port is not None else prev
        want = normalize_com_port(want_raw) if (want_raw or "").strip() else ""
        if want:
            want_n = want.upper()
            idx = self.cb_com.findData(want, Qt.ItemDataRole.UserRole, Qt.MatchFlag.MatchExactly)
            if idx < 0:
                for i in range(self.cb_com.count()):
                    d = self.cb_com.itemData(i, Qt.ItemDataRole.UserRole)
                    if d is not None and str(d).strip().upper() == want_n:
                        idx = i
                        break
            if idx >= 0:
                self.cb_com.setCurrentIndex(idx)
            else:
                self.cb_com.addItem(want, userData=want)
                i = self.cb_com.count() - 1
                self.cb_com.setItemData(
                    i,
                    t("rig.com_saved_not_available"),
                    Qt.ItemDataRole.ToolTipRole,
                )
                self.cb_com.setCurrentIndex(i)
        elif self.cb_com.count() > 0:
            self.cb_com.setCurrentIndex(0)
        self.cb_com.blockSignals(False)


    def _select_model_by_rig_id(self, rig_id: int) -> None:
        """Modellauswahl anhand gespeicherter Hamlib-ID wiederherstellen."""
        if rig_id <= 0:
            return
        for m in self._hamlib_models:
            if int(m.get("id", 0) or 0) != rig_id:
                continue
            brand = str(m.get("brand", "") or "")
            model = str(m.get("model", "") or "")
            idx_brand = self.cb_rig_brand.findText(brand)
            if idx_brand >= 0:
                self.cb_rig_brand.setCurrentIndex(idx_brand)
                self._on_brand_changed()
            idx_model = self.cb_rig_model.findText(model)
            if idx_model >= 0:
                self.cb_rig_model.setCurrentIndex(idx_model)
            self._update_rig_info_label()
            return

    def _populate_brand_combo(self) -> None:
        """Markenliste aus verfügbaren Hamlib-Modellen ableiten."""
        self.cb_rig_brand.blockSignals(True)
        self.cb_rig_brand.clear()
        brands: list[str] = []
        for m in self._hamlib_models:
            b = str(m.get("brand", "") or "").strip()
            if b and b not in brands:
                brands.append(b)
        if not brands:
            brands = ["Generisch"]
        brands.sort(key=lambda s: s.casefold())
        for b in brands:
            self.cb_rig_brand.addItem(b)
        self.cb_rig_brand.blockSignals(False)

    def _on_brand_changed(self) -> None:
        """Bei Markenwechsel passende Modelle in die Modellliste laden."""
        brand = self.cb_rig_brand.currentText().strip()
        self.cb_rig_model.blockSignals(True)
        self.cb_rig_model.clear()
        model_rows: list[tuple[str, int]] = []
        for m in self._hamlib_models:
            if str(m.get("brand", "")) == brand:
                model_rows.append(
                    (str(m.get("model", "")), int(m.get("id", 0) or 0))
                )
        model_rows.sort(key=lambda t: t[0].casefold())
        for model, rid in model_rows:
            self.cb_rig_model.addItem(model, rid)
        if self.cb_rig_model.count() == 0:
            self.cb_rig_model.addItem("CAT (generisch)", 0)
        self.cb_rig_model.blockSignals(False)
        self._update_rig_info_label()
        self._rig_combo_apply_max_width()

    def _update_rig_info_label(self) -> None:
        """Aktuelle Hamlib-Modell-ID sichtbar machen."""
        rig_id = int(self.cb_rig_model.currentData() or 0)
        self.lbl_rig_info.setText(str(rig_id) if rig_id > 0 else "-")

    def _load_hamlib_models(self) -> None:
        """Modelle aus Hamlib (`rigctl -l`) laden; bei Fehler auf Fallback gehen."""
        loaded = self._read_hamlib_models_via_rigctl()
        if loaded:
            self._hamlib_models = loaded
            return
        loaded = self._read_hamlib_models_from_markdown()
        if loaded:
            self._hamlib_models = loaded
            return
        # Fallback, falls rigctl/hamlib nicht installiert oder nicht im PATH ist.
        self._hamlib_models = [
            {"id": 1, "brand": "Icom", "model": "IC-7300"},
            {"id": 2, "brand": "Icom", "model": "IC-7610"},
            {"id": 3, "brand": "Yaesu", "model": "FT-991A"},
            {"id": 4, "brand": "Yaesu", "model": "FT-891"},
            {"id": 5, "brand": "Kenwood", "model": "TS-590SG"},
            {"id": 6, "brand": "Elecraft", "model": "K3"},
            {"id": 7, "brand": "FlexRadio", "model": "6xxx"},
            {"id": 8, "brand": "Generisch", "model": "CAT (generisch)"},
        ]

    @staticmethod
    def _read_hamlib_models_via_rigctl() -> list[dict[str, str | int]]:
        """Hamlib-Modellliste aus `rigctl -l` parsen."""
        try:
            out = subprocess.check_output(
                ["rigctl", "-l"],
                text=True,
                encoding="utf-8",
                errors="ignore",
                timeout=5,
            )
        except Exception:
            return []
        return RigBridgeTab._parse_hamlib_table_text(out)

    @staticmethod
    def _read_hamlib_models_from_markdown() -> list[dict[str, str | int]]:
        """Hamlib-Modelle aus lokaler Supported-Radios-Markdown-Datei lesen."""
        candidates: list[Path] = []
        repo_root = Path(__file__).resolve().parents[2]
        candidates.append(repo_root / "Supported-Radios-0.md")
        candidates.append(repo_root / "uploads" / "Supported-Radios-0.md")
        candidates.append(repo_root / "rotortcpbridge" / "rig_bridge" / "Supported-Radios-0.md")
        # Cursor-Uploads (lokale Arbeitsumgebung des Nutzers)
        home = Path.home()
        candidates.append(
            home
            / ".cursor"
            / "projects"
            / "d-Rotor-RotorTcpBridge"
            / "uploads"
            / "Supported-Radios-0.md"
        )
        for path in candidates:
            try:
                if not path.exists():
                    continue
                text = path.read_text(encoding="utf-8", errors="ignore")
                rows = RigBridgeTab._parse_hamlib_table_text(text)
                if rows:
                    return rows
            except Exception:
                continue
        return []

    @staticmethod
    def _parse_hamlib_table_text(text: str) -> list[dict[str, str | int]]:
        """Parst Zeilen wie bei `rigctl -l`/Wiki-Tabelle robust nach Spalten."""
        rows: list[dict[str, str | int]] = []
        for raw in text.splitlines():
            line = raw.rstrip()
            # Nur Tabellenzeilen mit numerischer Rig-ID berücksichtigen.
            if not re.match(r"^\s*\d+\s+", line):
                continue
            # Spalten sind typischerweise mit >=2 Leerzeichen getrennt:
            # Rig# | Mfg | Model | Version | Status | Macro
            cols = re.split(r"\s{2,}", line.strip())
            if len(cols) < 3:
                continue
            try:
                rig_id = int(cols[0])
            except Exception:
                continue
            brand = str(cols[1]).strip()
            model = str(cols[2]).strip()
            if not brand or not model:
                continue
            rows.append({"id": rig_id, "brand": brand, "model": model})
        # Doppelte entfernen, Reihenfolge beibehalten.
        unique: list[dict[str, str | int]] = []
        seen: set[tuple[int, str, str]] = set()
        for row in rows:
            key = (int(row["id"]), str(row["brand"]), str(row["model"]))
            if key in seen:
                continue
            seen.add(key)
            unique.append(row)
        return unique

    def _on_connect(self) -> None:
        self.apply_to_manager()
        ok, msg = self.manager.connect_radio_and_autostart_protocols()
        self.lbl_status.setText(msg)
        if not ok:
            self.lbl_error.setText(msg)
        else:
            self.lbl_error.setText("-")
        self.refresh_status()

    def _on_disconnect(self) -> None:
        for name in ("flrig", "hamlib"):
            self.manager.stop_protocol(name)
        self.manager.disconnect_radio()
        self.refresh_status()

    def _on_test(self) -> None:
        self.apply_to_manager()
        ok, msg = self.manager.test_connection(144_300_000)
        self.lbl_status.setText(msg)
        if ok:
            self.lbl_error.setText("-")
        else:
            self.lbl_error.setText(msg)
        self.refresh_status()

    def _start_protocol(self, name: str) -> None:
        self.apply_to_manager()
        ok, msg = self.manager.start_protocol(name)
        if not ok:
            self.lbl_error.setText(msg)
        self.refresh_status()

    def _stop_protocol(self, name: str) -> None:
        self.manager.stop_protocol(name)
        self.refresh_status()

    def refresh_status(self) -> None:
        st = self.manager.status_model()
        color = st.led_color()
        if color == "green":
            self.led_radio.set_state(True)
        elif color == "yellow":
            self.led_radio.set_blinking_green(True)
        elif color == "gray":
            self.led_radio.set_state(False)
            self.led_radio.setStyleSheet("opacity: 0.55;")
        else:
            self.led_radio.set_state(False)
            self.led_radio.setStyleSheet("")
        self.lbl_status.setText(st.status_text())
        self.lbl_error.setText(st.last_error or "-")
        self.lbl_last.setText(st.last_contact_text())
        self.lbl_com.setText(st.com_port or "-")
        self._protocol_leds["flrig"].set_state(bool(st.protocol_active.get("flrig", False)))
        self._protocol_leds["hamlib"].set_state(bool(st.protocol_active.get("hamlib", False)))
        n_fl = int(st.protocol_clients.get("flrig", 0) or 0)
        self._protocol_client_led["flrig"].set_state(n_fl > 0)
        hm_on = bool(st.protocol_active.get("hamlib", False))
        fh = (self._protocol_host["flrig"].text() or "").strip() or "127.0.0.1"
        try:
            fport = int((self._protocol_port["flrig"].text() or "12345").strip())
        except ValueError:
            fport = 12345
        self._lbl_flrig_bind_clients.setText(t("main.rig_flrig_detail", host=fh, port=fport, n=n_fl))
        hh = (self._hamlib_host.text() or "").strip() or "127.0.0.1"
        hm_counts: dict[int, int] = {}
        try:
            hm_counts = self.manager.hamlib_listener_client_counts()
        except Exception:
            hm_counts = {}
        any_listen_port = False
        for ed_p, _ed_n, lbl_hp, lbl_n, cli_led, _ in self._hamlib_rows:
            pt = ed_p.text().strip()
            if not pt:
                lbl_hp.setText("")
                lbl_n.setText("")
                cli_led.set_state(False)
                continue
            try:
                p = int(pt)
            except ValueError:
                lbl_hp.setText("")
                lbl_n.setText("")
                cli_led.set_state(False)
                continue
            p = max(1, min(65535, p))
            any_listen_port = True
            c = int(hm_counts.get(p, 0))
            lbl_hp.setText(f"{hh}:{p}")
            lbl_n.setText(t("main.rig_n_clients", n=c) if hm_on else "")
            cli_led.set_state(hm_on and c > 0)
        if any_listen_port:
            self._lbl_hamlib_bind_clients.setText("")
        else:
            self._lbl_hamlib_bind_clients.setText(t("settings.rig_listen_none", host=hh))
