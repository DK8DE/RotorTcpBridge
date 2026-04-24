"""Hauptfenster der RotorTcpBridge-Anwendung."""

from __future__ import annotations

import sys
import threading
from functools import partial

from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QMainWindow,
    QMessageBox,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QPushButton,
    QFormLayout,
)
from PySide6.QtGui import QAction, QFont, QGuiApplication
from PySide6.QtCore import QEvent, Qt, QTimer

from .antenna_sync import AntennaSelectionBridge

from ..app_icon import get_app_icon
from ..i18n import t
from ..shortcut_actions import (
    bump_antenna_target_deg,
    bump_el_target_deg,
    set_antenna_azimuth_deg,
)
from ..rig_bridge.cat_commands import normalize_com_port
from ..net_utils import check_internet
from ..version import APP_VERSION
from ..compass.compass_window import CompassWindow
from .statistics_window import StatisticsWindow
from .led_widget import Led
from .log_window import LogWindow
from .settings_window import SettingsWindow
from .weather_window import WeatherWindow
from .map_window import MapWindow
from .about_window import AboutWindow
from .rotor_configuration import CommandButtonsWindow
from .warnings_errors_window import WarningsErrorsWindow
from .rig_freq_utils import format_rig_freq_mhz, parse_rig_freq_mhz_text
from .ui_utils import px_to_dip
from .theme import apply_theme_mode
from .popup_handlers import ErrorPopupHandler, WarningPopupHandler
from .axis_widget import _make_axis_panel, fill_axis_panel, retranslate_axis_panel



class MainWindow(QMainWindow):
    @staticmethod
    def _hamlib_listener_ports_sorted(ham_cfg: dict) -> list[int]:
        ports: list[int] = []
        for it in ham_cfg.get("listeners") or []:
            if isinstance(it, dict) and it.get("port") not in (None, ""):
                try:
                    ports.append(int(it["port"]))
                except (TypeError, ValueError):
                    pass
        return sorted(set(ports))

    @staticmethod
    def _hamlib_listener_names_by_port(ham_cfg: dict) -> dict[int, str]:
        """Port → freier Anzeigename aus der Konfiguration (Rig-Bridge → Hamlib listeners)."""
        out: dict[int, str] = {}
        for it in ham_cfg.get("listeners") or []:
            if not isinstance(it, dict) or it.get("port") in (None, ""):
                continue
            try:
                p = int(it["port"])
            except (TypeError, ValueError):
                continue
            out[p] = str(it.get("name", "") or "").strip()
        return out

    def _active_rig_view(self) -> dict:
        """Liefert eine flache Sicht auf das aktive Rig-Profil.

        Die ``rig_bridge``-Konfig ist profilbasiert (``rigs`` + ``active_rig_id``).
        Viele UI-Stellen lesen aber weiterhin flache Felder wie ``com_port``
        oder die globalen TCP-Protokolle ``flrig``/``hamlib``. Diese
        Helper-Methode gibt ein Dict zurueck, das das aktive Profil mit
        dem globalen ``enabled``-Flag und den global gepflegten
        ``flrig``/``hamlib``-Settings kombiniert. Fuer alte flache Configs
        wird das Dict unveraendert zurueckgeliefert.
        """
        rb = dict(self.cfg.get("rig_bridge") or {})
        rigs = rb.get("rigs")
        global_flrig = dict(rb.get("flrig") or {}) if isinstance(rb.get("flrig"), dict) else {}
        global_hamlib = dict(rb.get("hamlib") or {}) if isinstance(rb.get("hamlib"), dict) else {}
        if not isinstance(rigs, list):
            # Altform: flache Struktur, direkte Ruecklieferung genuegt.
            return rb
        active_id = str(rb.get("active_rig_id", "") or "")
        active: dict | None = None
        for p in rigs:
            if isinstance(p, dict) and str(p.get("id", "")) == active_id:
                active = p
                break
        if active is None:
            for p in rigs:
                if isinstance(p, dict):
                    active = p
                    break
        if not isinstance(active, dict):
            return {
                "enabled": bool(rb.get("enabled", False)),
                "flrig": global_flrig,
                "hamlib": global_hamlib,
            }
        view = dict(active)
        # Globales enabled UND Profil-enabled muessen True sein, damit
        # die Bruecke als aktiv gilt — dieselbe Logik wie im Manager.
        view["enabled"] = bool(rb.get("enabled", False)) and bool(
            active.get("enabled", True)
        )
        view["active_rig_id"] = active_id
        # Flrig/Hamlib stets aus Top-Level uebernehmen (globale Settings,
        # nicht pro Profil). Fallback: sollte das Profil noch alte Felder
        # tragen, diese nur als Default verwenden.
        view["flrig"] = global_flrig or (
            dict(active.get("flrig") or {}) if isinstance(active.get("flrig"), dict) else {}
        )
        view["hamlib"] = global_hamlib or (
            dict(active.get("hamlib") or {}) if isinstance(active.get("hamlib"), dict) else {}
        )
        return view

    def _srv_led_wrap(self, led: Led) -> QWidget:
        w = QWidget()
        l = QVBoxLayout(w)
        l.setContentsMargins(0, 2, 0, 0)
        l.addWidget(led)
        return w

    def _ensure_rig_hamlib_extra_rows(self, n_extra: int) -> None:
        """Zusätzliche Hamlib-Zeilen (ab dem 2. konfigurierten Port)."""
        lay = getattr(self, "_lay_hamlib_stack", None)
        if lay is None:
            return
        while len(self._rig_hamlib_extra_rows) < n_extra:
            proto_led = Led(self._srv_led_d, self)
            lbl_hp = QLabel("")
            lbl_hp.setWordWrap(False)
            lbl_hp.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            w_r = QWidget(self)
            hl_r = QHBoxLayout(w_r)
            hl_r.setContentsMargins(0, 0, 0, 0)
            hl_r.setSpacing(px_to_dip(self, 6))
            hl_r.addStretch(1)
            lbl_n = QLabel("")
            lbl_n.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            lbl_n.setMinimumWidth(px_to_dip(self, 72))
            cli_led = Led(self._srv_led_d, self)
            hl_r.addWidget(lbl_n, 0)
            hl_r.addWidget(self._srv_led_wrap(cli_led), 0)
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(px_to_dip(self, 6))
            row.addWidget(self._srv_led_wrap(proto_led))
            row.addWidget(lbl_hp, 1)
            row.addWidget(w_r, 0)
            w = QWidget()
            w.setLayout(row)
            lay.addWidget(w)
            self._rig_hamlib_extra_rows.append((w, proto_led, lbl_hp, lbl_n, cli_led))
        for i, (w, *_rest) in enumerate(self._rig_hamlib_extra_rows):
            w.setVisible(i < n_extra)

    def __init__(
        self,
        cfg: dict,
        controller,
        pst_server,
        hw_client,
        save_cfg_cb,
        logbuf,
        udp_ucxlog=None,
        udp_pst=None,
        udp_aswatch=None,
        aswatch_bridge=None,
        rig_bridge_manager=None,
        pst_serial=None,
    ):
        super().__init__()
        self.cfg = cfg
        self.ctrl = controller
        self.pst = pst_server
        self.hw = hw_client
        self.save_cfg_cb = save_cfg_cb
        self.logbuf = logbuf
        self._udp_ucxlog = udp_ucxlog
        self._udp_pst = udp_pst
        self._udp_aswatch = udp_aswatch
        self._rig_bridge_manager = rig_bridge_manager
        self.pst_serial = pst_serial
        if aswatch_bridge is not None:
            try:
                aswatch_bridge.users.connect(
                    self._on_aswatch_users, Qt.ConnectionType.QueuedConnection
                )
                aswatch_bridge.airplanes.connect(
                    self._on_aswatch_aircraft, Qt.ConnectionType.QueuedConnection
                )
                aswatch_bridge.asnearest_summary.connect(
                    self._on_asnearest_summary, Qt.ConnectionType.QueuedConnection
                )
            except Exception:
                pass
        self._hw_off_since: float | None = None
        self._last_title: str = ""
        self._ucxlog_blink_phase = 0
        self._ucxlog_blink_active = False
        self._ucxlog_blink_sequence = (True, False, True, False, True, False, True, False, True)
        self._last_pst_az_d10: int | None = None
        self._pst_udp_blink_phase = 0
        self._pst_udp_blink_active = False
        self._pst_udp_blink_sequence = (True, False, True, False, True, False, True, False, True)
        self._aswatch_blink_phase = 0
        self._aswatch_blink_active = False
        self._aswatch_blink_sequence = (True, False, True, False, True, False, True, False, True)
        self._rig_serial_blink_phase = 0
        self._rig_serial_blink_active = False
        self._rig_flrig_blink_phase = 0
        self._rig_flrig_blink_active = False
        self._rig_hamlib_blink_phase: dict[int, int] = {}
        self._rig_hamlib_blink_active: dict[int, bool] = {}
        self._rig_blink_sequence = (True, False, True, False, True, False, True, False, True)
        self._last_rig_srv_vis: tuple | None = None
        self._rig_hamlib_extra_rows: list[tuple[QWidget, Led, QLabel, QLabel, Led]] = []
        self._aswatch_markers_last: list = []
        self._aswatch_aircraft_last: list = []
        self._asnearest_summary_last: list = []
        self._actions_locked_while_moving: bool | None = None

        self._antenna_bridge = AntennaSelectionBridge(self)

        self._update_title_bar()
        self.setWindowIcon(get_app_icon())
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, False)

        menubar = self.menuBar()
        self._menu_setup = menubar.addMenu(t("main.menu_setup"))
        self._act_settings = QAction(t("main.btn_settings"), self)
        self._act_settings.triggered.connect(self._open_settings)
        self._menu_setup.addAction(self._act_settings)
        self._act_commands = QAction(t("main.btn_commands"), self)
        self._act_commands.triggered.connect(self._open_commands)
        self._menu_setup.addAction(self._act_commands)
        self._act_statistics = QAction(t("main.menu_statistics"), self)
        self._act_statistics.triggered.connect(self._open_statistics)
        self._menu_setup.addAction(self._act_statistics)
        self._act_delwarn = QAction(t("main.menu_delwarn"), self)
        self._act_delwarn.triggered.connect(self.ctrl.clear_warnings_all)
        self._menu_setup.addAction(self._act_delwarn)

        self._menu_window = menubar.addMenu(t("main.menu_window"))
        self._act_win_compass = QAction(t("main.btn_compass"), self)
        self._act_win_compass.triggered.connect(self._open_compass)
        self._menu_window.addAction(self._act_win_compass)
        self._act_win_map = QAction(t("main.btn_map"), self)
        self._act_win_map.triggered.connect(self._open_map)
        self._menu_window.addAction(self._act_win_map)
        self._act_win_weather = QAction(t("main.btn_weather"), self)
        self._act_win_weather.triggered.connect(self._open_weather)
        self._menu_window.addAction(self._act_win_weather)
        self._act_win_weather.setVisible(False)
        self._act_win_warnings_errors = QAction(t("main.menu_win_warnings_errors"), self)
        self._act_win_warnings_errors.triggered.connect(self._open_warnings_errors)
        self._menu_window.addAction(self._act_win_warnings_errors)

        self._menu_help = menubar.addMenu(t("main.menu_help"))
        self._act_version = QAction(t("main.menu_version"), self)
        self._act_version.triggered.connect(self._open_about)
        self._menu_help.addAction(self._act_version)
        self._act_log = QAction(t("main.btn_log"), self)
        self._act_log.triggered.connect(self._toggle_log)
        self._menu_help.addAction(self._act_log)

        menubar.setStyleSheet("QMenuBar { font-weight: bold; }")
        _menu_bold = "QMenu { font-weight: bold; }"
        self._menu_setup.setStyleSheet(_menu_bold)
        self._menu_window.setStyleSheet(_menu_bold)
        self._menu_help.setStyleSheet(_menu_bold)

        root = QWidget()
        self.setCentralWidget(root)
        main = QVBoxLayout(root)

        self.gb_control = QGroupBox(t("main.group_control"))
        top = QHBoxLayout(self.gb_control)
        try:
            top.setContentsMargins(
                px_to_dip(self, 8), px_to_dip(self, 6), px_to_dip(self, 8), px_to_dip(self, 6)
            )
        except Exception:
            pass
        self.btn_open_compass = QPushButton(t("main.btn_compass"))
        self.btn_open_map = QPushButton(t("main.btn_map"))
        self.btn_ref = QPushButton(t("main.btn_ref"))
        top.addWidget(self.btn_open_compass, 1)
        top.addWidget(self.btn_open_map, 1)
        top.addWidget(self.btn_ref, 1)

        # Funkgeraet-Zeile: Aktives-Rig-Dropdown + Frequenzanzeige in einer
        # Zeile. Label und Combobox werden nur sichtbar, wenn mehrere Profile
        # vorhanden sind; der Funkgeraete-Name unterhalb der Frequenz entfaellt
        # bewusst, weil der Profilname im Dropdown denselben Kontext liefert.
        self._rig_freq_row = QGroupBox(t("main.group_radio"))
        self._rig_freq_row.setVisible(False)
        vl_rf = QVBoxLayout(self._rig_freq_row)
        try:
            vl_rf.setContentsMargins(
                px_to_dip(self, 8), px_to_dip(self, 4), px_to_dip(self, 8), px_to_dip(self, 4)
            )
        except Exception:
            pass
        vl_rf.setSpacing(px_to_dip(self, 4))
        hl_rf = QHBoxLayout()
        hl_rf.setSpacing(px_to_dip(self, 6))
        self._lbl_active_rig = QLabel(t("main.active_rig_label"))
        self._cb_active_rig = QComboBox()
        self._cb_active_rig.setMinimumWidth(px_to_dip(self, 140))
        self._cb_active_rig.currentIndexChanged.connect(self._on_active_rig_changed)
        self._lbl_active_rig.setVisible(False)
        self._cb_active_rig.setVisible(False)
        hl_rf.addWidget(self._lbl_active_rig, 0)
        hl_rf.addWidget(self._cb_active_rig, 0)
        self._ed_rig_freq = QLineEdit()
        self._ed_rig_freq.setPlaceholderText(t("main.rig_freq_placeholder"))
        _rf_font = QFont(self._ed_rig_freq.font())
        _rf_font.setPointSizeF(max(8.0, _rf_font.pointSizeF() * 2.0) * (2.0 / 3.0))
        self._ed_rig_freq.setFont(_rf_font)
        self._ed_rig_freq.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_rig_freq_unit = QLabel(t("main.rig_freq_suffix"))
        self._lbl_rig_freq_unit.setFont(_rf_font)
        hl_rf.addWidget(self._ed_rig_freq, 1)
        hl_rf.addWidget(self._lbl_rig_freq_unit, 0)
        vl_rf.addLayout(hl_rf)
        main.addWidget(self._rig_freq_row)

        self.gb_antenna = QGroupBox(t("main.group_antenna_select"))
        _lay_ant = QVBoxLayout(self.gb_antenna)
        try:
            _lay_ant.setContentsMargins(
                px_to_dip(self, 8), px_to_dip(self, 4), px_to_dip(self, 8), px_to_dip(self, 4)
            )
        except Exception:
            pass
        self._cb_main_antenna = QComboBox()
        self._cb_main_antenna.setMinimumWidth(px_to_dip(self, 160))
        self._cb_main_antenna.addItems(self._get_antenna_dropdown_labels())
        _ant_idx = max(0, min(2, int(self.cfg.get("ui", {}).get("compass_antenna", 0))))
        self._cb_main_antenna.setCurrentIndex(_ant_idx)
        self._cb_main_antenna.currentIndexChanged.connect(self._on_main_antenna_changed)
        _lay_ant.addWidget(self._cb_main_antenna)
        self._last_main_antenna_labels: tuple[str, ...] | None = None
        main.addWidget(self.gb_antenna)

        self._rig_freq_poll_timer = QTimer(self)
        self._rig_freq_poll_timer.setInterval(1000)
        self._rig_freq_poll_timer.timeout.connect(self._on_rig_freq_poll_timer)
        self._ed_rig_freq.editingFinished.connect(self._on_rig_freq_editing_finished)
        _app = QApplication.instance()
        if _app is not None:
            _app.installEventFilter(self)

        self.gb_srv = QGroupBox(t("main.group_server"))
        main.addWidget(self.gb_srv)
        srv_form = QFormLayout(self.gb_srv)
        self._srv_form = srv_form

        led_d = px_to_dip(self, 12)
        self._srv_led_d = led_d
        self.led_pst = Led(led_d, self)
        self.led_pst_conn = Led(led_d, self)
        self.led_ucxlog = Led(led_d, self)
        self.led_pst_udp = Led(led_d, self)
        self.led_aswatch = Led(led_d, self)
        self.led_rig_serial = Led(led_d, self)
        self.led_rig_flrig = Led(led_d, self)
        self.led_rig_hamlib = Led(led_d, self)
        self.led_hw = Led(led_d, self)

        self.lbl_pst = QLabel("")
        self.lbl_pst.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        self.lbl_pst.setWordWrap(False)
        self.lbl_hw = QLabel("")
        self.lbl_hw.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        self.lbl_hw.setWordWrap(False)

        hw_row = QHBoxLayout()
        hw_row.setContentsMargins(0, 0, 0, 0)
        hw_row.setSpacing(px_to_dip(self, 6))
        hw_row.addWidget(self._srv_led_wrap(self.led_hw))
        hw_row.addWidget(self.lbl_hw, 1)
        hw_row_w = QWidget()
        hw_row_w.setLayout(hw_row)
        srv_form.addRow(t("main.srv_hw_label"), hw_row_w)
        self._srv_row_hw_w = hw_row_w

        pst_row = QHBoxLayout()
        pst_row.setContentsMargins(0, 0, 0, 0)
        pst_row.setSpacing(px_to_dip(self, 6))
        pst_row.addWidget(self._srv_led_wrap(self.led_pst))
        pst_row.addWidget(self.lbl_pst, 1)
        pst_row_w = QWidget()
        pst_row_w.setLayout(pst_row)
        srv_form.addRow(t("main.srv_pst_label"), pst_row_w)
        self._srv_row_pst_w = pst_row_w

        pst_conn_row = QHBoxLayout()
        pst_conn_row.setContentsMargins(0, 0, 0, 0)
        pst_conn_row.setSpacing(px_to_dip(self, 6))
        pst_conn_row.addWidget(self._srv_led_wrap(self.led_pst_conn))
        self._lbl_srv_pst_conn = QLabel(t("main.srv_pst_conn_text"))
        pst_conn_row.addWidget(self._lbl_srv_pst_conn)
        pst_conn_row.addStretch(1)
        pst_conn_row_w = QWidget()
        pst_conn_row_w.setLayout(pst_conn_row)
        srv_form.addRow(t("main.srv_pst_conn_label"), pst_conn_row_w)
        self._srv_row_pst_conn_w = pst_conn_row_w

        ucxlog_row = QHBoxLayout()
        ucxlog_row.setContentsMargins(0, 0, 0, 0)
        ucxlog_row.setSpacing(px_to_dip(self, 6))
        ucxlog_row.addWidget(self._srv_led_wrap(self.led_ucxlog))
        self._lbl_srv_ucxlog_suffix = QLabel("")
        ucxlog_row.addWidget(self._lbl_srv_ucxlog_suffix)
        ucxlog_row.addStretch(1)
        ucxlog_row_w = QWidget()
        ucxlog_row_w.setLayout(ucxlog_row)
        srv_form.addRow(t("main.srv_ucxlog_prefix"), ucxlog_row_w)
        self._srv_row_ucxlog_w = ucxlog_row_w
        try:
            self._lbl_srv_ucxlog_suffix.setText(
                self._udp_bind_status_text(
                    "udp_ucxlog_listen_host", "udp_ucxlog_port", "127.0.0.1", 12040
                )
            )
        except Exception:
            pass

        pst_udp_row = QHBoxLayout()
        pst_udp_row.setContentsMargins(0, 0, 0, 0)
        pst_udp_row.setSpacing(px_to_dip(self, 6))
        pst_udp_row.addWidget(self._srv_led_wrap(self.led_pst_udp))
        self._lbl_srv_pst_udp_suffix = QLabel("")
        pst_udp_row.addWidget(self._lbl_srv_pst_udp_suffix)
        pst_udp_row.addStretch(1)
        pst_udp_row_w = QWidget()
        pst_udp_row_w.setLayout(pst_udp_row)
        srv_form.addRow(t("main.srv_pst_udp_prefix"), pst_udp_row_w)
        self._srv_row_pst_udp_w = pst_udp_row_w
        try:
            self._lbl_srv_pst_udp_suffix.setText(
                self._udp_bind_status_text("udp_pst_listen_host", "udp_pst_port", "127.0.0.1", 12000)
            )
        except Exception:
            pass

        aswatch_row = QHBoxLayout()
        aswatch_row.setContentsMargins(0, 0, 0, 0)
        aswatch_row.setSpacing(px_to_dip(self, 6))
        aswatch_row.addWidget(self._srv_led_wrap(self.led_aswatch))
        self._lbl_srv_aswatch_suffix = QLabel("")
        self._lbl_srv_aswatch_suffix.setWordWrap(False)
        aswatch_row.addWidget(self._lbl_srv_aswatch_suffix)
        aswatch_row.addStretch(1)
        aswatch_row_w = QWidget()
        aswatch_row_w.setLayout(aswatch_row)
        srv_form.addRow(t("main.srv_aswatch_label"), aswatch_row_w)
        self._srv_row_aswatch_w = aswatch_row_w
        try:
            self._lbl_srv_aswatch_suffix.setText(
                self._udp_bind_status_text(
                    "aswatch_udp_listen_host", "aswatch_udp_port", "127.0.0.1", 9872
                )
            )
        except Exception:
            pass

        rig_serial_row = QHBoxLayout()
        rig_serial_row.setContentsMargins(0, 0, 0, 0)
        rig_serial_row.setSpacing(px_to_dip(self, 6))
        rig_serial_row.addWidget(self._srv_led_wrap(self.led_rig_serial))
        self.lbl_rig_serial = QLabel("")
        self.lbl_rig_serial.setWordWrap(False)
        rig_serial_row.addWidget(self.lbl_rig_serial, 1)
        rig_serial_row_w = QWidget()
        rig_serial_row_w.setLayout(rig_serial_row)
        srv_form.addRow(t("main.srv_rig_com_label"), rig_serial_row_w)
        self._srv_row_rig_serial_w = rig_serial_row_w

        rig_flrig_row = QHBoxLayout()
        rig_flrig_row.setContentsMargins(0, 0, 0, 0)
        rig_flrig_row.setSpacing(px_to_dip(self, 6))
        rig_flrig_row.addWidget(self._srv_led_wrap(self.led_rig_flrig))
        self.lbl_rig_flrig = QLabel("")
        self.lbl_rig_flrig.setWordWrap(False)
        self.lbl_rig_flrig.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        rig_flrig_row.addWidget(self.lbl_rig_flrig, 1)
        self._w_rig_flrig_right = QWidget(self)
        hl_flr = QHBoxLayout(self._w_rig_flrig_right)
        hl_flr.setContentsMargins(0, 0, 0, 0)
        hl_flr.setSpacing(px_to_dip(self, 6))
        hl_flr.addStretch(1)
        self._lbl_rig_flrig_n = QLabel("")
        self._lbl_rig_flrig_n.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self._lbl_rig_flrig_n.setMinimumWidth(px_to_dip(self, 72))
        self._led_rig_flrig_conn = Led(self._srv_led_d, self)
        hl_flr.addWidget(self._lbl_rig_flrig_n, 0)
        hl_flr.addWidget(self._srv_led_wrap(self._led_rig_flrig_conn), 0)
        rig_flrig_row.addWidget(self._w_rig_flrig_right, 0)
        rig_flrig_row_w = QWidget()
        rig_flrig_row_w.setLayout(rig_flrig_row)
        srv_form.addRow(t("main.srv_rig_flrig_label"), rig_flrig_row_w)
        self._srv_row_rig_flrig_w = rig_flrig_row_w

        self._rig_hamlib_outer = QWidget()
        self._lay_hamlib_stack = QVBoxLayout(self._rig_hamlib_outer)
        self._lay_hamlib_stack.setContentsMargins(0, 0, 0, 0)
        self._lay_hamlib_stack.setSpacing(px_to_dip(self, 2))
        rig_hamlib_row = QHBoxLayout()
        rig_hamlib_row.setContentsMargins(0, 0, 0, 0)
        rig_hamlib_row.setSpacing(px_to_dip(self, 6))
        rig_hamlib_row.addWidget(self._srv_led_wrap(self.led_rig_hamlib))
        self.lbl_rig_hamlib = QLabel("")
        self.lbl_rig_hamlib.setWordWrap(False)
        self.lbl_rig_hamlib.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        rig_hamlib_row.addWidget(self.lbl_rig_hamlib, 1)
        self._w_rig_hamlib_right = QWidget(self)
        hl_hmr = QHBoxLayout(self._w_rig_hamlib_right)
        hl_hmr.setContentsMargins(0, 0, 0, 0)
        hl_hmr.setSpacing(px_to_dip(self, 6))
        hl_hmr.addStretch(1)
        self._lbl_rig_hamlib_n = QLabel("")
        self._lbl_rig_hamlib_n.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self._lbl_rig_hamlib_n.setMinimumWidth(px_to_dip(self, 72))
        self._led_rig_hamlib_conn = Led(self._srv_led_d, self)
        hl_hmr.addWidget(self._lbl_rig_hamlib_n, 0)
        hl_hmr.addWidget(self._srv_led_wrap(self._led_rig_hamlib_conn), 0)
        rig_hamlib_row.addWidget(self._w_rig_hamlib_right, 0)
        rig_hamlib_row_w = QWidget()
        rig_hamlib_row_w.setLayout(rig_hamlib_row)
        self._lay_hamlib_stack.addWidget(rig_hamlib_row_w)
        srv_form.addRow(t("main.srv_rig_hamlib_label"), self._rig_hamlib_outer)
        self._srv_row_rig_hamlib_w = self._rig_hamlib_outer

        try:
            srv_form.setVerticalSpacing(px_to_dip(self, 4))
            srv_form.setContentsMargins(
                px_to_dip(self, 8), px_to_dip(self, 4), px_to_dip(self, 8), px_to_dip(self, 4)
            )
        except Exception:
            pass

        slave_az = self.cfg.get("rotor_bus", {}).get("slave_az", "?")
        slave_el = self.cfg.get("rotor_bus", {}).get("slave_el", "?")
        self.gb_az = QGroupBox(f"AZ ID:{slave_az}")
        self.gb_el = QGroupBox(f"EL ID:{slave_el}")
        main.addWidget(self.gb_az)
        main.addWidget(self.gb_el)
        main.addWidget(self.gb_control)

        self.az_fields = _make_axis_panel(self.gb_az, "az", self.ctrl)
        self.el_fields = _make_axis_panel(self.gb_el, "el", self.ctrl)

        self.btn_ref.clicked.connect(lambda: self.ctrl.reference_all(True))

        # Referenzierungs-Fehler-Callback: Controller ruft dies aus Hintergrundthread auf
        self.ctrl.on_ref_start_failed = self._on_ref_start_failed

        self.t = QTimer(self)
        self.t.timeout.connect(self._tick)
        self.t.start(100)

        self._internet_online: bool | None = None
        self._internet_checking: bool = False  # Verhindert gleichzeitige Prüfungen
        self._internet_check_timer = QTimer(self)
        self._internet_check_timer.setInterval(3_000)
        self._internet_check_timer.timeout.connect(self._on_internet_check_timer)
        self._internet_check_timer.start()
        QTimer.singleShot(300, self._on_internet_check_timer)
        QTimer.singleShot(500, self._check_pst_udp_startup_error)

        self._log_win = LogWindow(self.logbuf, parent=None)
        self._compass_win = CompassWindow(
            self.cfg,
            self.ctrl,
            self.save_cfg_cb,
            parent=None,
            antenna_bridge=self._antenna_bridge,
            open_map_cb=self._open_map,
            rig_bridge_manager=self._rig_bridge_manager,
        )
        self._attach_compass_aswatch_provider()
        self._map_win = MapWindow(
            self.cfg,
            self.ctrl,
            self.save_cfg_cb,
            parent=None,
            antenna_bridge=self._antenna_bridge,
            on_asnearest_select_cb=self._on_map_asnearest_select,
            on_map_page_ready_cb=self._on_map_page_ready,
            rig_bridge_manager=self._rig_bridge_manager,
        )
        # Broadcast zuerst: Sync-Slots dürfen den TX nicht verhindern; Fire-and-Forget ist separat
        self._antenna_bridge.selection_changed.connect(self._on_antenna_broadcast_aselect)
        self._antenna_bridge.selection_changed.connect(self._sync_main_antenna_combo_from_bridge)
        self._antenna_bridge.selection_changed.connect(self._compass_win.sync_az_rotor_target_from_controller)
        self._antenna_bridge.selection_changed.connect(self._compass_win.sync_antenna_from_external)
        self._antenna_bridge.selection_changed.connect(self._map_win.sync_antenna_from_external)
        self._antenna_bridge.setaselect_from_bus.connect(self._apply_setaselect_from_bus_ui)
        self.ctrl.on_setaselect_from_bus = (
            lambda n: self._antenna_bridge.setaselect_from_bus.emit(int(n))
        )
        self._settings_win = SettingsWindow(
            self.cfg,
            self.ctrl,
            self.pst,
            self.hw,
            self.save_cfg_cb,
            self.logbuf,
            after_apply_cb=self._after_settings_applied,
            rig_bridge_manager=self._rig_bridge_manager,
            rebuild_ui_cb=self._rebuild_all_windows,
            map_window=self._map_win,
            pst_serial=self.pst_serial,
            parent=None,
        )
        self._statistics_win = StatisticsWindow(self.cfg, self.ctrl, parent=None)
        self._weather_win = WeatherWindow(self.cfg, self.ctrl, parent=None)
        self._warnings_errors_win = WarningsErrorsWindow(self.ctrl, parent=None)
        self._commands_win = CommandButtonsWindow(
            self.cfg, self.ctrl, self.save_cfg_cb, parent=None
        )

        self.btn_open_compass.clicked.connect(self._open_compass)
        self.btn_open_map.clicked.connect(self._open_map)

        self._fixed_w = None
        self._fixed_h = None
        self._last_axis_vis: tuple[bool, bool] | None = None
        self._last_wind_vis: bool | None = None
        self._error_popup = ErrorPopupHandler()
        self._warning_popup = WarningPopupHandler()
        self._startup_error_check_scheduled = False

        apply_theme_mode(self.cfg)
        QTimer.singleShot(0, self._repolish_menu_bar_for_os_theme)
        QTimer.singleShot(0, self._refresh_settings_nav_theme)
        self._install_system_theme_change_hooks()
        self._update_axis_visibility()
        self._update_srv_rows_visibility()
        self._apply_fixed_mainwindow_size()

        self._global_hotkey_controller = None
        if sys.platform == "win32":
            from ..global_hotkeys_win import GlobalHotkeyController

            self._global_hotkey_controller = GlobalHotkeyController(
                lambda: int(self.winId()),
                lambda a: QTimer.singleShot(0, partial(self._apply_global_shortcut_action, a)),
            )

    def _install_system_theme_change_hooks(self) -> None:
        """Bei OS-Theme-Wechsel native Menüleiste nachziehen (nur ohne force_dark)."""
        app = QApplication.instance()
        if not isinstance(app, QApplication) or getattr(app, "_rtb_system_theme_hooks", False):
            return
        setattr(app, "_rtb_system_theme_hooks", True)
        try:
            sh = QGuiApplication.styleHints()
            sh.colorSchemeChanged.connect(
                lambda *_: QTimer.singleShot(0, self._sync_system_theme_ui)
            )
        except Exception:
            pass

    def _sync_system_theme_ui(self) -> None:
        if bool((self.cfg.get("ui") or {}).get("force_dark_mode", True)):
            return
        try:
            # Kein erneutes apply_theme_mode: würde System-Palette unnötig anfassen.
            self._repolish_menu_bar_for_os_theme()
        except Exception:
            pass

    def _refresh_settings_nav_theme(self) -> None:
        """Einstellungs-Sidebar: wird vor erstem apply_theme_mode gebaut, daher nach Theme nachziehen."""
        sw = getattr(self, "_settings_win", None)
        if sw is None:
            return
        try:
            sw.refresh_nav_theme()
        except Exception:
            pass

    def _repolish_menu_bar_for_os_theme(self) -> None:
        mb = self.menuBar()
        if mb is None:
            return
        app = QApplication.instance()
        for w in (
            mb,
            getattr(self, "_menu_setup", None),
            getattr(self, "_menu_window", None),
            getattr(self, "_menu_help", None),
        ):
            if w is None:
                continue
            ss = w.styleSheet()
            w.setStyleSheet("")
            st = app.style() if isinstance(app, QApplication) else w.style()
            if st is not None:
                try:
                    st.unpolish(w)
                    st.polish(w)
                except RuntimeError:
                    pass
            w.setStyleSheet(ss)
        mb.update()

    def changeEvent(self, event: QEvent) -> None:
        if event.type() in (
            QEvent.Type.ThemeChange,
            QEvent.Type.ApplicationPaletteChange,
        ):
            if not bool((self.cfg.get("ui") or {}).get("force_dark_mode", True)):
                QTimer.singleShot(0, self._sync_system_theme_ui)
        super().changeEvent(event)

    def _refresh_global_hotkeys(self) -> None:
        hc = getattr(self, "_global_hotkey_controller", None)
        if hc is None:
            return
        try:
            hc.apply_config(self.cfg)
        except Exception:
            pass

    def _apply_global_shortcut_action(self, action: str) -> None:
        try:
            gs = (self.cfg.get("ui") or {}).get("global_shortcuts") or {}
            if not bool(gs.get("enabled", True)):
                return
            if action == "rot_w":
                set_antenna_azimuth_deg(
                    self.cfg, self.ctrl, float(gs.get("antenna_deg_w", 0.0))
                )
            elif action == "rot_d":
                set_antenna_azimuth_deg(
                    self.cfg, self.ctrl, float(gs.get("antenna_deg_d", 90.0))
                )
            elif action == "rot_s":
                set_antenna_azimuth_deg(
                    self.cfg, self.ctrl, float(gs.get("antenna_deg_s", 180.0))
                )
            elif action == "rot_a":
                set_antenna_azimuth_deg(
                    self.cfg, self.ctrl, float(gs.get("antenna_deg_a", 270.0))
                )
            elif action == "open_compass":
                self._open_compass()
            elif action == "open_map":
                self._open_map()
            elif action == "open_elevation":
                self._open_elevation_from_shortcut()
            elif action == "target_plus":
                bump_antenna_target_deg(
                    self.cfg, self.ctrl, float(gs.get("target_step_deg", 3.0))
                )
            elif action == "target_minus":
                bump_antenna_target_deg(
                    self.cfg, self.ctrl, -float(gs.get("target_step_deg", 3.0))
                )
            elif action == "el_target_plus":
                bump_el_target_deg(
                    self.ctrl, float(gs.get("el_target_step_deg", 5.0))
                )
            elif action == "el_target_minus":
                bump_el_target_deg(
                    self.ctrl, -float(gs.get("el_target_step_deg", 5.0))
                )
            elif action == "select_antenna_1":
                self._select_antenna_by_shortcut(0)
            elif action == "select_antenna_2":
                self._select_antenna_by_shortcut(1)
            elif action == "select_antenna_3":
                self._select_antenna_by_shortcut(2)
        except Exception:
            pass

    def _select_antenna_by_shortcut(self, idx: int) -> None:
        """Antenne 1–3 wie Hauptfenster-Dropdown (Config, SETASELECT-Broadcast, Bridge)."""
        cb = getattr(self, "_cb_main_antenna", None)
        if cb is None:
            return
        idx = max(0, min(2, int(idx)))
        if cb.currentIndex() == idx:
            return
        cb.blockSignals(True)
        cb.setCurrentIndex(idx)
        cb.blockSignals(False)
        self._on_main_antenna_changed()

    def _open_elevation_from_shortcut(self) -> None:
        try:
            mw = getattr(self, "_map_win", None)
            if mw is not None and hasattr(mw, "_on_elevation_profile"):
                mw._on_elevation_profile()
        except Exception:
            pass

    def nativeEvent(self, eventType, message):
        hc = getattr(self, "_global_hotkey_controller", None)
        if hc is not None:
            r = hc.process_native_event(eventType, message)
            if r is not None:
                return r
        return super().nativeEvent(eventType, message)

    def _open_about(self):
        dlg = AboutWindow(parent=self)
        dlg.exec()

    def _open_settings(self):
        if not bool(getattr(self._act_settings, "isEnabled", lambda: True)()):
            return
        self._settings_win.show()
        self._settings_win.raise_()
        self._settings_win.activateWindow()

    def _toggle_log(self):
        if self._log_win.isVisible():
            self._log_win.hide()
        else:
            self._log_win.show()
            self._log_win.raise_()
            self._log_win.activateWindow()
            self._log_win.refresh()

    def _update_title_bar(self) -> None:
        """Titelleiste: App-Name, Version und konfigurierte HW-Art (TCP/COM)."""
        hl = self.cfg.get("hardware_link", {})
        mode = str(hl.get("mode", "tcp")).strip().lower()
        link = "COM" if mode == "com" else "TCP"
        title = f"{t('app.title')} v{APP_VERSION} {link}"
        if title != self._last_title:
            self._last_title = title
            self.setWindowTitle(title)

    def _udp_bind_status_text(
        self,
        host_key: str,
        port_key: str,
        default_host: str,
        default_port: int,
    ) -> str:
        """Kurzinfo „host:port“ für UDP-Zeilen in der Server-Gruppe (Suffix rechts)."""
        ui = self.cfg.get("ui", {})
        h = str(ui.get(host_key, default_host) or default_host).strip() or default_host
        try:
            p = int(ui.get(port_key, default_port))
        except Exception:
            p = default_port
        return t("main.srv_udp_bind_suffix", host=h, port=p)

    def _refresh_menubar_top_level(self) -> None:
        """Top-Level-Menüs kurz von der Leiste lösen und wieder einhängen.

        Unter Windows aktualisiert die native Titelleisten-Menüleiste die sichtbaren
        Menünamen oft nicht, wenn nur QMenu.setTitle() aufgerufen wird — die
        QAction-Texte in den geöffneten Menüs sind dagegen korrekt.
        """
        mb = self.menuBar()
        menus = (self._menu_setup, self._menu_window, self._menu_help)
        for m in menus:
            act = m.menuAction()
            if act is not None:
                mb.removeAction(act)
        for m in menus:
            mb.addMenu(m)

    def _retranslate_ui(self):
        """Alle Texte des Hauptfensters auf die aktuelle Sprache aktualisieren."""
        self._last_title = ""  # Cache zurücksetzen damit Neuaufbau greift
        self._update_title_bar()
        self._menu_setup.setTitle(t("main.menu_setup"))
        self._act_settings.setText(t("main.btn_settings"))
        self._act_commands.setText(t("main.btn_commands"))
        self._act_statistics.setText(t("main.menu_statistics"))
        self._act_delwarn.setText(t("main.menu_delwarn"))
        self._menu_window.setTitle(t("main.menu_window"))
        self._act_win_compass.setText(t("main.btn_compass"))
        self._act_win_map.setText(t("main.btn_map"))
        self._act_win_weather.setText(t("main.btn_weather"))
        self._act_win_warnings_errors.setText(t("main.menu_win_warnings_errors"))
        self._menu_help.setTitle(t("main.menu_help"))
        self._act_version.setText(t("main.menu_version"))
        self._act_log.setText(t("main.btn_log"))
        self._refresh_menubar_top_level()
        self.btn_open_compass.setText(t("main.btn_compass"))
        self.btn_open_map.setText(t("main.btn_map"))
        self.btn_ref.setText(t("main.btn_ref"))
        try:
            self.gb_control.setTitle(t("main.group_control"))
        except Exception:
            pass
        try:
            if hasattr(self, "gb_antenna"):
                self.gb_antenna.setTitle(t("main.group_antenna_select"))
        except Exception:
            pass
        try:
            if hasattr(self, "_rig_freq_row"):
                self._rig_freq_row.setTitle(t("main.group_radio"))
        except Exception:
            pass
        # Server-GroupBox: Überschriften + Beschriftungen der Formularzeilen
        try:
            self.gb_srv.setTitle(t("main.group_server"))
            if hasattr(self, "_lbl_rig_freq_unit"):
                self._lbl_rig_freq_unit.setText(t("main.rig_freq_suffix"))
            if hasattr(self, "_ed_rig_freq"):
                self._ed_rig_freq.setPlaceholderText(t("main.rig_freq_placeholder"))
            sf = self._srv_form
            lab = sf.labelForField(self._srv_row_hw_w)
            if isinstance(lab, QLabel):
                lab.setText(t("main.srv_hw_label"))
            lab = sf.labelForField(self._srv_row_pst_w)
            if isinstance(lab, QLabel):
                lab.setText(t("main.srv_pst_label"))
            lab = sf.labelForField(self._srv_row_pst_conn_w)
            if isinstance(lab, QLabel):
                lab.setText(t("main.srv_pst_conn_label"))
            lab = sf.labelForField(self._srv_row_ucxlog_w)
            if isinstance(lab, QLabel):
                lab.setText(t("main.srv_ucxlog_prefix"))
            lab = sf.labelForField(self._srv_row_pst_udp_w)
            if isinstance(lab, QLabel):
                lab.setText(t("main.srv_pst_udp_prefix"))
            lab = sf.labelForField(self._srv_row_aswatch_w)
            if isinstance(lab, QLabel):
                lab.setText(t("main.srv_aswatch_label"))
            lab = sf.labelForField(self._srv_row_rig_serial_w)
            if isinstance(lab, QLabel):
                lab.setText(t("main.srv_rig_com_label"))
            lab = sf.labelForField(self._srv_row_rig_flrig_w)
            if isinstance(lab, QLabel):
                lab.setText(t("main.srv_rig_flrig_label"))
            lab = sf.labelForField(self._srv_row_rig_hamlib_w)
            if isinstance(lab, QLabel):
                lab.setText(t("main.srv_rig_hamlib_label"))
            self._lbl_srv_pst_conn.setText(t("main.srv_pst_conn_text"))
            self._lbl_srv_ucxlog_suffix.setText(
                self._udp_bind_status_text(
                    "udp_ucxlog_listen_host", "udp_ucxlog_port", "127.0.0.1", 12040
                )
            )
            self._lbl_srv_pst_udp_suffix.setText(
                self._udp_bind_status_text("udp_pst_listen_host", "udp_pst_port", "127.0.0.1", 12000)
            )
            self._lbl_srv_aswatch_suffix.setText(
                self._udp_bind_status_text(
                    "aswatch_udp_listen_host", "aswatch_udp_port", "127.0.0.1", 9872
                )
            )
        except Exception:
            pass
        # AZ/EL-Achsenfelder
        try:
            retranslate_axis_panel(self.az_fields)
            retranslate_axis_panel(self.el_fields)
        except Exception:
            pass
        try:
            if hasattr(self, "_warnings_errors_win") and self._warnings_errors_win is not None:
                self._warnings_errors_win.retranslate_ui()
        except Exception:
            pass
        try:
            if hasattr(self, "_compass_win") and hasattr(self._compass_win, "sync_heatmap_controls_from_cfg"):
                self._compass_win.sync_heatmap_controls_from_cfg()
        except Exception:
            pass
        try:
            if hasattr(self, "_compass_win") and hasattr(self._compass_win, "retranslate_ui"):
                self._compass_win.retranslate_ui()
        except Exception:
            pass
        try:
            sw = getattr(self, "_settings_win", None)
            if sw is not None and hasattr(sw, "_shortcuts_tab"):
                sw._shortcuts_tab.retranslate_hotkey_combo_texts()
                sw._shortcuts_tab.refresh_antenna_shortcut_row_labels()
        except Exception:
            pass

    def _rebuild_all_windows(self):
        """Alle Fenster schließen und neu erstellen (nach Sprachänderung)."""
        try:
            for attr in (
                "_log_win",
                "_compass_win",
                "_map_win",
                "_statistics_win",
                "_weather_win",
                "_warnings_errors_win",
                "_commands_win",
            ):
                w = getattr(self, attr, None)
                if w is not None:
                    try:
                        w.close()
                    except Exception:
                        pass
            from ..compass.compass_window import CompassWindow
            from .statistics_window import StatisticsWindow
            from .weather_window import WeatherWindow
            from .rotor_configuration import CommandButtonsWindow
            from .log_window import LogWindow
            from .warnings_errors_window import WarningsErrorsWindow

            self._log_win = LogWindow(self.logbuf, parent=None)
            self._compass_win = CompassWindow(
                self.cfg,
                self.ctrl,
                self.save_cfg_cb,
                parent=None,
                antenna_bridge=self._antenna_bridge,
                open_map_cb=self._open_map,
                rig_bridge_manager=self._rig_bridge_manager,
            )
            self._attach_compass_aswatch_provider()
            self._map_win = MapWindow(
                self.cfg,
                self.ctrl,
                self.save_cfg_cb,
                parent=None,
                antenna_bridge=self._antenna_bridge,
                on_asnearest_select_cb=self._on_map_asnearest_select,
                on_map_page_ready_cb=self._on_map_page_ready,
                rig_bridge_manager=self._rig_bridge_manager,
            )
            try:
                self._antenna_bridge.selection_changed.disconnect()
            except TypeError:
                pass
            self._antenna_bridge.selection_changed.connect(self._on_antenna_broadcast_aselect)
            self._antenna_bridge.selection_changed.connect(self._compass_win.sync_az_rotor_target_from_controller)
            self._antenna_bridge.selection_changed.connect(self._compass_win.sync_antenna_from_external)
            self._antenna_bridge.selection_changed.connect(self._map_win.sync_antenna_from_external)
            # setaselect_from_bus bleibt an derselben Bridge verbunden (nur Callback am Controller setzen)
            self.ctrl.on_setaselect_from_bus = (
                lambda n: self._antenna_bridge.setaselect_from_bus.emit(int(n))
            )
            self._statistics_win = StatisticsWindow(self.cfg, self.ctrl, parent=None)
            self._weather_win = WeatherWindow(self.cfg, self.ctrl, parent=None)
            self._warnings_errors_win = WarningsErrorsWindow(self.ctrl, parent=None)
            self._commands_win = CommandButtonsWindow(
                self.cfg, self.ctrl, self.save_cfg_cb, parent=None
            )
        except Exception:
            pass
        self._after_settings_applied()
        # Einstellungsfenster verzögert neu erstellen (wir befinden uns noch in seinem Aufrufstack)
        QTimer.singleShot(900, self._rebuild_settings_win)

    def _rebuild_settings_win(self):
        """Einstellungsfenster mit neuer Sprache neu erstellen."""
        try:
            old = getattr(self, "_settings_win", None)
            if old is not None:
                try:
                    old.close()
                except Exception:
                    pass
            self._settings_win = SettingsWindow(
                self.cfg,
                self.ctrl,
                self.pst,
                self.hw,
                self.save_cfg_cb,
                self.logbuf,
                after_apply_cb=self._after_settings_applied,
                rig_bridge_manager=self._rig_bridge_manager,
                rebuild_ui_cb=self._rebuild_all_windows,
                map_window=self._map_win,
                pst_serial=self.pst_serial,
                parent=None,
            )
        except Exception:
            pass

    def _after_settings_applied(self):
        apply_theme_mode(self.cfg)
        # Immer repolish: bei Forced-Dark sonst helle native Win-Menüleiste; bei Systemmodus OS-Farben.
        QTimer.singleShot(0, self._repolish_menu_bar_for_os_theme)
        QTimer.singleShot(0, self._refresh_settings_nav_theme)
        self._update_groupbox_titles()
        self._update_axis_visibility()
        self._update_srv_rows_visibility()
        self._apply_fixed_mainwindow_size()
        # PST-Server starten oder stoppen je nach Einstellung
        pst_enabled = bool(self.cfg.get("pst_server", {}).get("enabled", False))
        try:
            if pst_enabled and not self.pst.running:
                self.pst.start()
            elif not pst_enabled and self.pst.running:
                self.pst.stop()
        except Exception as e:
            self._log_exception("_after_settings_applied PST start/stop", e)
        # PST-Serial (com0com) Listener analog aktualisieren
        if self.pst_serial is not None:
            try:
                ps_cfg = self.cfg.get("pst_serial", {}) or {}
                self.pst_serial.update_config(ps_cfg)
                if bool(ps_cfg.get("enabled", False)):
                    self.pst_serial.start_all()
                else:
                    self.pst_serial.stop_all()
            except Exception as e:
                self._log_exception("_after_settings_applied PST-Serial start/stop", e)
        if hasattr(self, "_map_win") and self._map_win is not None:
            try:
                self._map_win.on_settings_applied()
            except Exception as e:
                self._log_exception("_after_settings_applied MapWindow", e)
            if self._internet_online is not None:
                self._map_win.apply_internet_status(self._internet_online)
        if self._udp_ucxlog is not None:
            ui = self.cfg.get("ui", {})
            self._udp_ucxlog.start(
                enabled=bool(ui.get("udp_ucxlog_enabled", False)),
                port=int(ui.get("udp_ucxlog_port", 12040)),
                listen_host=str(ui.get("udp_ucxlog_listen_host", "127.0.0.1")),
            )
        if self._udp_pst is not None:
            ui = self.cfg.get("ui", {})
            self._udp_pst.start(
                enabled=bool(ui.get("udp_pst_enabled", True)),
                port=int(ui.get("udp_pst_port", 12000)),
                listen_host=str(ui.get("udp_pst_listen_host", "127.0.0.1")),
            )
            if self._udp_pst.bind_error_msg:
                QMessageBox.warning(
                    self, t("main.pst_udp_error_title"), self._udp_pst.bind_error_msg
                )
        if self._udp_aswatch is not None:
            ui = self.cfg.get("ui", {})
            self._udp_aswatch.start(
                enabled=bool(ui.get("aswatch_udp_enabled", False)),
                port=int(ui.get("aswatch_udp_port", 9872)),
                listen_host=str(ui.get("aswatch_udp_listen_host", "127.0.0.1")),
            )
        # Hauptfenster-Texte (Server, AZ/EL, Menü, …) nach load_lang / Einstellungen synchronisieren
        self._retranslate_ui()
        if hasattr(self, "_compass_win") and self._compass_win.isVisible():
            self._compass_win._update_groupbox_titles()
            if hasattr(self._compass_win, "_apply_label_colors_from_palette"):
                self._compass_win._apply_label_colors_from_palette()
        if hasattr(self, "_compass_win") and hasattr(self._compass_win, "sync_heatmap_controls_from_cfg"):
            self._compass_win.sync_heatmap_controls_from_cfg()
        self._refresh_global_hotkeys()

    def _on_internet_check_timer(self) -> None:
        """Internet-Prüfung im Hintergrund, UI-Update auf Hauptthread.
        Nur ein Thread gleichzeitig – verhindert Race Condition durch veraltete Ergebnisse."""
        if self._internet_checking:
            return  # Vorherige Prüfung noch aktiv → überspringen
        self._internet_checking = True

        def do_check():
            try:
                online = check_internet()
            except Exception as e:
                exc = e

                def _apply_err() -> None:
                    self._log_exception("check_internet (Hintergrund)", exc)
                    self._handle_internet_result(False)

                QTimer.singleShot(0, _apply_err)
                return
            QTimer.singleShot(0, lambda o=online: self._handle_internet_result(o))

        threading.Thread(target=do_check, daemon=True).start()

    def _handle_internet_result(self, online: bool) -> None:
        """Ergebnis der Internet-Prüfung auf Hauptthread verarbeiten."""
        self._internet_checking = False  # Flag auf Main-Thread zurücksetzen (thread-safe)
        self._apply_internet_status(online)

    def _apply_internet_status(self, online: bool) -> None:
        """Kartenmodus je nach Internetstatus an MapWindow weiterleiten."""
        self._internet_online = online
        if hasattr(self, "_map_win") and self._map_win is not None:
            self._map_win.apply_internet_status(online)

    def _on_aswatch_users(self, items: list) -> None:
        """UDP ASWATCHLIST → Karten-Marker (Hauptthread)."""
        try:
            self._aswatch_markers_last = list(items) if items else []
        except Exception:
            self._aswatch_markers_last = []
        try:
            if hasattr(self, "_map_win") and self._map_win is not None:
                self._map_win.update_aswatch_users(items)
        except Exception:
            pass
        try:
            cw = getattr(self, "_compass_win", None)
            if cw is not None and hasattr(cw, "refresh_om_radar_from_aswatch"):
                cw.refresh_om_radar_from_aswatch()
        except Exception:
            pass

    def _on_aswatch_aircraft(self, items: list) -> None:
        """UDP ASNEAREST → Flugzeug-Marker und Reflexions-Linien (Hauptthread)."""
        try:
            self._aswatch_aircraft_last = list(items) if items else []
        except Exception:
            self._aswatch_aircraft_last = []
        try:
            if hasattr(self, "_map_win") and self._map_win is not None:
                self._map_win.update_aircraft_markers(items)
        except Exception:
            pass

    def _on_asnearest_summary(self, rows: list) -> None:
        """ASNEAREST: Liste Rufzeichen / Entfernung / ETA (Hauptthread)."""
        try:
            self._asnearest_summary_last = list(rows) if rows else []
        except Exception:
            self._asnearest_summary_last = []
        try:
            if hasattr(self, "_map_win") and self._map_win is not None:
                self._map_win.update_asnearest_summary(rows)
        except Exception:
            pass

    def _on_map_asnearest_select(self, dest_key: str | None) -> None:
        """Karten-HTML: Zeile in der ASNEAREST-Tabelle oder Kartenklick (Auswahl löschen)."""
        asw = getattr(self, "_udp_aswatch", None)
        if asw is None:
            return
        try:
            asw.set_asnearest_selected(dest_key)
        except Exception:
            pass

    def _on_map_page_ready(self) -> None:
        """Leaflet geladen: Flugzeug-Marker mit Listener-Zustand synchronisieren (keine veraltete Liste)."""
        asw = getattr(self, "_udp_aswatch", None)
        if asw is None:
            return
        try:
            asw.refresh_aircraft_emit()
        except Exception:
            pass

    def _attach_compass_aswatch_provider(self) -> None:
        """Kompass: OM-Radar aus letzter AirScout/KST-Markerliste."""
        if not hasattr(self, "_compass_win") or self._compass_win is None:
            return
        self._compass_win.set_aswatch_marker_provider(lambda: self._aswatch_markers_last)

    def _on_antenna_broadcast_aselect(self, idx: int) -> None:
        """Kompass/Karte: Antennenwechsel → RS485-Broadcast SETASELECT (Antenne 1–3)."""
        try:
            self.ctrl.broadcast_set_aselect(int(idx) + 1)
        except Exception:
            pass

    def _apply_setaselect_from_bus_ui(self, antenna_id_1_to_3: int) -> None:
        """Antenne 1–3 aus Bus übernehmen; cfg + Kompass/Karte, kein selection_changed (kein Echo)."""
        try:
            idx = max(0, min(2, int(antenna_id_1_to_3) - 1))
            ui = self.cfg.setdefault("ui", {})
            old = int(ui.get("compass_antenna", 0))
            if old == idx:
                return
            if hasattr(self.ctrl, "align_az_bearing_after_antenna_switch"):
                try:
                    self.ctrl.align_az_bearing_after_antenna_switch(old, idx, self.cfg)
                except Exception:
                    pass
            ui["compass_antenna"] = idx
            try:
                if self.save_cfg_cb:
                    self.save_cfg_cb(self.cfg)
            except Exception:
                pass
            cw = getattr(self, "_compass_win", None)
            if cw is not None:
                if hasattr(cw, "sync_az_rotor_target_from_controller"):
                    try:
                        cw.sync_az_rotor_target_from_controller()
                    except Exception:
                        pass
                cw.sync_antenna_from_external(idx)
            mw = getattr(self, "_map_win", None)
            if mw is not None:
                mw.sync_antenna_from_external(idx)
            cb = getattr(self, "_cb_main_antenna", None)
            if cb is not None:
                cb.blockSignals(True)
                cb.setCurrentIndex(idx)
                cb.blockSignals(False)
        except Exception:
            pass

    def _get_antenna_dropdown_labels(self) -> list[str]:
        """Wie Kompass: Namen mit AZ-Versatz in Klammern."""
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

    def _sync_main_antenna_combo_from_bridge(self, idx: int) -> None:
        """Kompass/Karte/anderes Fenster hat Antenne gewechselt → Hauptfenster-Dropdown."""
        cb = getattr(self, "_cb_main_antenna", None)
        if cb is None:
            return
        idx = max(0, min(2, int(idx)))
        if cb.currentIndex() == idx:
            return
        cb.blockSignals(True)
        cb.setCurrentIndex(idx)
        cb.blockSignals(False)

    def _on_main_antenna_changed(self) -> None:
        """Antennenwahl wie im Kompass: Config, Bus-Broadcast, Bridge."""
        cb = getattr(self, "_cb_main_antenna", None)
        if cb is None:
            return
        old = max(0, min(2, int(self.cfg.get("ui", {}).get("compass_antenna", 0))))
        idx = max(0, min(2, cb.currentIndex()))
        ui = self.cfg.setdefault("ui", {})
        if old != idx and hasattr(self.ctrl, "align_az_bearing_after_antenna_switch"):
            try:
                self.ctrl.align_az_bearing_after_antenna_switch(old, idx, self.cfg)
            except Exception:
                pass
            try:
                if hasattr(self._compass_win, "sync_az_rotor_target_from_controller"):
                    self._compass_win.sync_az_rotor_target_from_controller()
            except Exception:
                pass
        ui["compass_antenna"] = idx
        try:
            if self.save_cfg_cb:
                self.save_cfg_cb(self.cfg)
        except Exception:
            pass
        try:
            self._antenna_bridge.selection_changed.emit(idx)
        except Exception:
            pass

    def _refresh_main_antenna_dropdown_labels_if_needed(self) -> None:
        """Versatz-/Namen aus HW oder Config — gleiche Zeilen wie Kompass."""
        cb = getattr(self, "_cb_main_antenna", None)
        if cb is None or not cb.isVisible():
            return
        labels = tuple(self._get_antenna_dropdown_labels())
        if getattr(self, "_last_main_antenna_labels", None) == labels:
            return
        self._last_main_antenna_labels = labels
        idx = max(0, min(2, int(self.cfg.get("ui", {}).get("compass_antenna", 0))))
        cb.blockSignals(True)
        cb.clear()
        cb.addItems(list(labels))
        cb.setCurrentIndex(idx)
        cb.blockSignals(False)

    @staticmethod
    def _bring_tool_window_to_front(w: QWidget) -> None:
        """Hilfsfenster aus Taskleisten-Minimierung holen und in den Vordergrund legen."""
        if w.isMinimized():
            w.showNormal()
        w.show()
        w.raise_()
        w.activateWindow()

    def _open_compass(self):
        try:
            if hasattr(self._compass_win, "_update_groupbox_titles"):
                self._compass_win._update_groupbox_titles()
            if hasattr(self._compass_win, "refresh_visibility"):
                self._compass_win.refresh_visibility()
            self._bring_tool_window_to_front(self._compass_win)
        except Exception:
            pass

    def _open_weather(self):
        try:
            self._weather_win.show()
            self._weather_win.raise_()
            self._weather_win.activateWindow()
        except Exception:
            pass

    def _open_warnings_errors(self):
        try:
            self._warnings_errors_win.show()
            self._warnings_errors_win.raise_()
            self._warnings_errors_win.activateWindow()
        except Exception:
            pass

    def _open_map(self):
        try:
            self._bring_tool_window_to_front(self._map_win)
        except Exception:
            pass

    def _open_commands(self):
        if not bool(getattr(self._act_commands, "isEnabled", lambda: True)()):
            return
        try:
            if hasattr(self._commands_win, "_refresh_dst_dropdown"):
                self._commands_win._refresh_dst_dropdown()
            self._commands_win.show()
            self._commands_win.raise_()
            self._commands_win.activateWindow()
        except Exception:
            pass

    def _open_statistics(self):
        if not bool(getattr(self._act_statistics, "isEnabled", lambda: True)()):
            return
        try:
            self._statistics_win.show()
            self._statistics_win.raise_()
            self._statistics_win.activateWindow()
        except Exception:
            pass

    def _update_actions_locked_by_moving(self) -> None:
        """Sperrt bestimmte Menüaktionen, solange AZ oder EL fährt."""
        try:
            moving = bool(getattr(self.ctrl.az, "moving", False)) or bool(
                getattr(self.ctrl.el, "moving", False)
            )
        except Exception:
            moving = False
        if self._actions_locked_while_moving == moving:
            return
        self._actions_locked_while_moving = moving
        enabled = not moving
        for act_name in ("_act_settings", "_act_commands", "_act_statistics"):
            try:
                act = getattr(self, act_name, None)
                if act is not None:
                    act.setEnabled(enabled)
            except Exception:
                pass

    def _update_groupbox_titles(self):
        slave_az = self.cfg.get("rotor_bus", {}).get("slave_az", "?")
        slave_el = self.cfg.get("rotor_bus", {}).get("slave_el", "?")
        self.gb_az.setTitle(f"AZ ID:{slave_az}")
        self.gb_el.setTitle(f"EL ID:{slave_el}")

    def _update_axis_visibility(self):
        az_on = bool(getattr(self.ctrl, "enable_az", True))
        el_on = bool(getattr(self.ctrl, "enable_el", True))
        self.gb_az.setVisible(az_on)
        self.gb_el.setVisible(el_on)
        if hasattr(self, "gb_antenna"):
            self.gb_antenna.setVisible(az_on)
        try:
            if hasattr(self, "_compass_win") and hasattr(self._compass_win, "refresh_visibility"):
                self._compass_win.refresh_visibility()
        except Exception:
            pass

    def _update_srv_rows_visibility(self) -> None:
        """Server-GroupBox-Zeilen je nach aktivierten Diensten ein-/ausblenden."""
        ui = self.cfg.get("ui", {})
        pst_on = bool(self.cfg.get("pst_server", {}).get("enabled", False))
        ucxlog_on = bool(ui.get("udp_ucxlog_enabled", False))
        pst_udp_on = bool(ui.get("udp_pst_enabled", True))
        aswatch_on = bool(ui.get("aswatch_udp_enabled", False))
        rb = self._active_rig_view()
        rig_mod = bool(rb.get("enabled", False))
        rig_flrig = rig_mod and bool((rb.get("flrig") or {}).get("enabled", False))
        rig_ham = rig_mod and bool((rb.get("hamlib") or {}).get("enabled", False))
        try:
            self._srv_form.setRowVisible(self._srv_row_pst_w, pst_on)
            self._srv_form.setRowVisible(self._srv_row_pst_conn_w, pst_on)
            self._srv_form.setRowVisible(self._srv_row_ucxlog_w, ucxlog_on)
            self._srv_form.setRowVisible(self._srv_row_pst_udp_w, pst_udp_on)
            self._srv_form.setRowVisible(self._srv_row_aswatch_w, aswatch_on)
            self._srv_form.setRowVisible(self._srv_row_rig_serial_w, rig_mod)
            self._srv_form.setRowVisible(self._srv_row_rig_flrig_w, rig_flrig)
            self._srv_form.setRowVisible(self._srv_row_rig_hamlib_w, rig_ham)
        except Exception:
            pass

    def _update_wind_visibility(self) -> bool:
        """Wind-UI ein-/ausblenden. Gibt wind_on zurück."""
        wind_known = bool(getattr(self.ctrl, "wind_enabled_known", False))
        wind_on = bool(getattr(self.ctrl, "wind_enabled", False)) if wind_known else False
        # Fallback nur wenn GETWINDENABLE noch unbekannt (Rotor implementiert es nicht):
        # Wind anzeigen, wenn bereits Winddaten empfangen wurden.
        if not wind_on and not wind_known and hasattr(self.ctrl, "az"):
            tel = getattr(self.ctrl.az, "telemetry", None)
            if tel is not None:
                has_wind = (
                    getattr(tel, "wind_kmh", None) is not None
                    or getattr(tel, "wind_dir_deg", None) is not None
                )
                if has_wind:
                    wind_on = True
        try:
            w3 = self.az_fields.get("wind_bft_pair_w")
            if w3 is not None:
                w3.setVisible(wind_on)
        except Exception:
            pass
        try:
            if hasattr(self, "_act_win_weather"):
                self._act_win_weather.setVisible(wind_on)
            if (not wind_on) and hasattr(self, "_weather_win") and self._weather_win.isVisible():
                self._weather_win.hide()
        except Exception:
            pass
        return wind_on

    def _refresh_active_rig_combo(self) -> None:
        """Fuellt die QComboBox fuer den aktiven Rig-Profil-Wechsel aus den
        Profilen des RigBridgeManagers. Combobox und Label werden nur
        sichtbar, sobald mindestens zwei Profile existieren — sonst waere
        die Auswahl sinnlos und wuerde die Zeile im Funkgeraete-Kasten
        unnoetig breit machen."""
        cb = getattr(self, "_cb_active_rig", None)
        lbl = getattr(self, "_lbl_active_rig", None)
        if cb is None or lbl is None:
            return
        rbm = getattr(self, "_rig_bridge_manager", None)
        profiles: list[dict] = []
        active_id = ""
        if rbm is not None:
            try:
                profiles = list(rbm.list_profiles() or [])
                active_id = str(rbm.active_rig_id() or "")
            except Exception:
                profiles = []
                active_id = ""
        show = len(profiles) >= 2
        lbl.setVisible(show)
        cb.setVisible(show)
        # Vergleich ueber Signatur, um nur bei echter Aenderung neu zu bauen.
        sig = tuple((str(p.get("id", "")), str(p.get("name", ""))) for p in profiles)
        if getattr(self, "_active_rig_combo_sig", None) != sig:
            cb.blockSignals(True)
            cb.clear()
            for p in profiles:
                pid = str(p.get("id", ""))
                name = str(p.get("name", "") or pid)
                cb.addItem(name, pid)
            cb.blockSignals(False)
            self._active_rig_combo_sig = sig
        # Aktuelle Auswahl angleichen.
        if active_id:
            for i in range(cb.count()):
                if str(cb.itemData(i)) == active_id:
                    if cb.currentIndex() != i:
                        cb.blockSignals(True)
                        cb.setCurrentIndex(i)
                        cb.blockSignals(False)
                    break

    def _on_active_rig_changed(self, idx: int) -> None:
        """User hat ein anderes Profil gewaehlt → im Manager aktiv schalten
        und Rig-Listener zwingen, den neuen CatResponder einzusetzen."""
        if idx < 0:
            return
        cb = getattr(self, "_cb_active_rig", None)
        rbm = getattr(self, "_rig_bridge_manager", None)
        if cb is None or rbm is None:
            return
        new_id = str(cb.itemData(idx) or "")
        if not new_id:
            return
        try:
            cur = str(rbm.active_rig_id() or "")
        except Exception:
            cur = ""
        if new_id == cur:
            return
        try:
            ok, _ = rbm.set_active_profile(new_id)
        except Exception:
            ok = False
        if not ok:
            return
        # Konfig mitschreiben, damit der Wechsel persistent bleibt.
        try:
            rb_cfg = dict(self.cfg.get("rig_bridge", {}) or {})
            rb_cfg["active_rig_id"] = new_id
            self.cfg["rig_bridge"] = rb_cfg
            if callable(self.save_cfg_cb):
                self.save_cfg_cb(self.cfg)
        except Exception:
            pass
        # Rig-Listener (CAT-Sim) umbinden.
        try:
            pst = getattr(self, "pst_serial", None)
            if pst is not None and hasattr(pst, "refresh_rig_listeners"):
                pst.refresh_rig_listeners()
        except Exception:
            pass

    def _on_rig_freq_poll_timer(self) -> None:
        rbm = getattr(self, "_rig_bridge_manager", None)
        if rbm is None:
            return
        try:
            rb_cfg = self.cfg.get("rig_bridge") or {}
            if not bool(rb_cfg.get("enabled", False)):
                return
            st = rbm.status_model()
            if not st.radio_connected or st.connecting:
                return
            rbm.enqueue_read_frequency()
        except Exception:
            pass

    def _on_rig_freq_editing_finished(self) -> None:
        rbm = getattr(self, "_rig_bridge_manager", None)
        if rbm is None:
            return
        try:
            rb_cfg = self.cfg.get("rig_bridge") or {}
            if not bool(rb_cfg.get("enabled", False)):
                return
            st = rbm.status_model()
            if not st.radio_connected:
                return
            hz = parse_rig_freq_mhz_text(self._ed_rig_freq.text())
            if hz is None:
                return
            cur = int(st.frequency_hz or 0)
            if cur > 0 and abs(hz - cur) < 2:
                return
            rbm.enqueue_set_frequency_hz(hz)
        except Exception:
            pass

    def _apply_fixed_mainwindow_size(self):
        # Feste Fensterbreite (DIP) — schmales Hauptfenster; Achsen-Layout nutzt Stretch in den Wert-Spalten
        width = px_to_dip(self, 365)
        try:
            lay = self.centralWidget().layout()
            if lay:
                lay.invalidate()
                lay.activate()
        except Exception:
            pass
        height = int(self.sizeHint().height()) + px_to_dip(self, 10)
        if height < px_to_dip(self, 300):
            height = px_to_dip(self, 300)
        if self._fixed_w == width and self._fixed_h == height:
            return
        self._fixed_w = width
        self._fixed_h = height
        self.setFixedSize(width, height)

    def showEvent(self, event):
        super().showEvent(event)
        self._refresh_global_hotkeys()
        self._update_axis_visibility()
        self._apply_fixed_mainwindow_size()
        if not self._startup_error_check_scheduled:
            self._startup_error_check_scheduled = True
            QTimer.singleShot(400, self._startup_error_poll_and_show)
            QTimer.singleShot(2200, self._startup_error_poll_and_show)

    def _startup_error_poll_and_show(self) -> None:
        """Nach kurzer Wartezeit Fehler-Popup (Fehlercode kommt per Broadcast ERR, kein GETERR)."""

        def _show() -> None:
            try:
                self._update_axis_visibility()
                if self.gb_az.isVisible():
                    self._error_popup.maybe_show(
                        self, "AZ", getattr(self.ctrl.az, "error_code", 0)
                    )
                if self.gb_el.isVisible():
                    self._error_popup.maybe_show(
                        self, "EL", getattr(self.ctrl.el, "error_code", 0)
                    )
            except Exception:
                pass

        QTimer.singleShot(450, _show)

    def eventFilter(self, watched, event):  # noqa: N802
        """Klick außerhalb des Frequenzfelds: Fokus entfernen (QLabels nehmen oft keinen Fokus)."""
        try:
            if event.type() == QEvent.Type.MouseButtonPress:
                ed = getattr(self, "_ed_rig_freq", None)
                if ed is not None and ed.hasFocus() and ed.isVisible():
                    if isinstance(watched, QWidget):
                        if watched is not ed and not ed.isAncestorOf(watched):
                            ed.clearFocus()
        except Exception:
            pass
        return False

    def closeEvent(self, event):
        try:
            hc = getattr(self, "_global_hotkey_controller", None)
            if hc is not None:
                hc.unregister_all()
        except Exception:
            pass
        try:
            app = QApplication.instance()
            if app is not None:
                app.removeEventFilter(self)
        except Exception:
            pass
        try:
            for w in (
                getattr(self, "_log_win", None),
                getattr(self, "_settings_win", None),
                getattr(self, "_compass_win", None),
                getattr(self, "_map_win", None),
                getattr(self, "_statistics_win", None),
                getattr(self, "_weather_win", None),
                getattr(self, "_warnings_errors_win", None),
                getattr(self, "_commands_win", None),
            ):
                try:
                    if w is not None:
                        w.close()
                except Exception:
                    pass
        except Exception:
            pass
        super().closeEvent(event)

    def _on_ref_start_failed(self, axis: str) -> None:
        """Wird aus Hintergrundthread aufgerufen – auf UI-Thread weiterleiten."""
        from PySide6.QtCore import QTimer as _QTimer

        _QTimer.singleShot(0, lambda: self._show_ref_failed_popup(str(axis)))

    def _show_ref_failed_popup(self, axis: str) -> None:
        """Zeigt Hinweis, dass SETREF kein ACK erhalten hat."""
        try:
            QMessageBox.warning(
                self,
                t("main.ref_failed_title"),
                t("main.ref_failed_msg", axis=axis),
            )
        except Exception:
            pass

    def _check_pst_udp_startup_error(self) -> None:
        """Beim Programmstart einmalig prüfen ob PST-UDP-Port belegt war."""
        if self._udp_pst is not None and self._udp_pst.bind_error_msg:
            QMessageBox.warning(self, t("main.pst_udp_error_title"), self._udp_pst.bind_error_msg)

    def _log_exception(self, context: str, exc: BaseException) -> None:
        """Unerwartete Ausnahme ins Logbuch schreiben (statt still zu schlucken)."""
        try:
            self.logbuf.write("WARN", f"{context}: {type(exc).__name__}: {exc}")
        except Exception:
            pass

    def _notify_pst_position(self) -> None:
        """Sendet AZ-Position via PST-UDP wenn sie sich geändert hat."""
        if self._udp_pst is None or not self._udp_pst.is_active:
            return
        try:
            az_d10 = getattr(self.ctrl.az, "pos_d10", None)
            if az_d10 is None:
                return
            self._udp_pst.notify_position(int(az_d10))
        except Exception as e:
            self._log_exception("PST-UDP notify_position", e)

    def _tick(self):
        import time as _time

        self.ctrl.tick_polling()

        pst_on = bool(self.pst.running)
        hw_on = bool(self.hw.is_connected())
        self.led_pst.set_state(pst_on)
        try:
            last_rx = float(getattr(self.pst, "last_rx_ts", 0.0) or 0.0)
            pst_recent = pst_on and (last_rx > 0.0) and ((_time.time() - last_rx) <= 2.0)
        except Exception as e:
            self._log_exception("_tick pst_recent", e)
            pst_recent = False
        try:
            self.led_pst_conn.set_state(bool(pst_recent))
        except Exception as e:
            self._log_exception("_tick led_pst_conn", e)

        udp = getattr(self, "_udp_ucxlog", None)
        if udp is not None:
            if getattr(udp, "packet_received_flag", False):
                udp.packet_received_flag = False
                self._ucxlog_blink_phase = 0
                self._ucxlog_blink_active = True
            if self._ucxlog_blink_active:
                seq = self._ucxlog_blink_sequence
                if self._ucxlog_blink_phase < len(seq):
                    self.led_ucxlog.set_state(seq[self._ucxlog_blink_phase])
                    self._ucxlog_blink_phase += 1
                else:
                    self._ucxlog_blink_active = False
            if not self._ucxlog_blink_active:
                self.led_ucxlog.set_state(udp.is_active)
        else:
            self.led_ucxlog.set_state(False)

        pst_udp = getattr(self, "_udp_pst", None)
        if pst_udp is not None:
            if getattr(pst_udp, "packet_received_flag", False):
                pst_udp.packet_received_flag = False
                self._pst_udp_blink_phase = 0
                self._pst_udp_blink_active = True
            if self._pst_udp_blink_active:
                seq = self._pst_udp_blink_sequence
                if self._pst_udp_blink_phase < len(seq):
                    self.led_pst_udp.set_state(seq[self._pst_udp_blink_phase])
                    self._pst_udp_blink_phase += 1
                else:
                    self._pst_udp_blink_active = False
            if not self._pst_udp_blink_active:
                self.led_pst_udp.set_state(pst_udp.is_active)
        else:
            self.led_pst_udp.set_state(False)

        asw = getattr(self, "_udp_aswatch", None)
        if asw is not None:
            if getattr(asw, "packet_received_flag", False):
                asw.packet_received_flag = False
                self._aswatch_blink_phase = 0
                self._aswatch_blink_active = True
            if self._aswatch_blink_active:
                seq = self._aswatch_blink_sequence
                if self._aswatch_blink_phase < len(seq):
                    self.led_aswatch.set_state(seq[self._aswatch_blink_phase])
                    self._aswatch_blink_phase += 1
                else:
                    self._aswatch_blink_active = False
            if not self._aswatch_blink_active:
                self.led_aswatch.set_state(asw.is_active)
        else:
            self.led_aswatch.set_state(False)

        rbm = getattr(self, "_rig_bridge_manager", None)
        rb_cfg = self._active_rig_view()
        rig_mod = bool(rb_cfg.get("enabled", False))
        # Auswahl-Combobox mit aktuellen Profilen synchron halten.
        try:
            self._refresh_active_rig_combo()
        except Exception:
            pass
        s_act = False
        f_act = False
        h_ports: set[int] = set()
        if rbm is not None and rig_mod:
            raw = rbm.take_rig_activity_flags()
            if isinstance(raw, tuple) and len(raw) >= 3:
                s_act, f_act, h_ports = raw[0], raw[1], raw[2]
            else:
                s_act, f_act, h_ports = False, False, set()
            try:
                st = rbm.status_model()
                com_p = st.com_port or str(rb_cfg.get("com_port", "") or "").strip() or "—"
                if st.connecting:
                    self.led_rig_serial.set_blinking_green(True)
                    self._rig_serial_blink_active = False
                    self.lbl_rig_serial.setText(f"{t('main.rig_com_connecting')}  {com_p}")
                else:
                    self.led_rig_serial.set_blinking_green(False)
                    if s_act:
                        self._rig_serial_blink_phase = 0
                        self._rig_serial_blink_active = True
                    seq = self._rig_blink_sequence
                    if self._rig_serial_blink_active:
                        if self._rig_serial_blink_phase < len(seq):
                            self.led_rig_serial.set_state(seq[self._rig_serial_blink_phase])
                            self._rig_serial_blink_phase += 1
                        else:
                            self._rig_serial_blink_active = False
                    if not self._rig_serial_blink_active:
                        self.led_rig_serial.set_state(st.radio_connected)
                    if st.radio_connected:
                        self.lbl_rig_serial.setText(t("main.rig_com_ok", com=com_p))
                    else:
                        self.lbl_rig_serial.setText(t("main.rig_com_off", com=com_p))

                vis_freq = bool(
                    rig_mod and st.radio_connected and not st.connecting
                )
                frw = getattr(self, "_rig_freq_row", None)
                if frw is not None:
                    prev_vis = frw.isVisible()
                    frw.setVisible(vis_freq)
                    if vis_freq and not prev_vis:
                        self._rig_freq_poll_timer.start()
                        self._on_rig_freq_poll_timer()
                    if not vis_freq and prev_vis:
                        self._rig_freq_poll_timer.stop()
                    if vis_freq != prev_vis:
                        self._apply_fixed_mainwindow_size()
                    if vis_freq:
                        hz_disp = int(st.frequency_hz or 0)
                        ed = self._ed_rig_freq
                        if not ed.hasFocus():
                            txt = (
                                format_rig_freq_mhz(hz_disp) if hz_disp > 0 else ""
                            )
                            if ed.text() != txt:
                                ed.blockSignals(True)
                                ed.setText(txt)
                                ed.blockSignals(False)

                fl_en = bool((rb_cfg.get("flrig") or {}).get("enabled", False))
                if fl_en:
                    fl_on = bool(st.protocol_active.get("flrig", False))
                    n_fl = int(st.protocol_clients.get("flrig", 0) or 0)
                    fh = str((rb_cfg.get("flrig") or {}).get("host", "127.0.0.1") or "127.0.0.1")
                    fp = int((rb_cfg.get("flrig") or {}).get("port", 12345) or 12345)
                    if f_act:
                        self._rig_flrig_blink_phase = 0
                        self._rig_flrig_blink_active = True
                    seqf = self._rig_blink_sequence
                    if self._rig_flrig_blink_active:
                        if self._rig_flrig_blink_phase < len(seqf):
                            self.led_rig_flrig.set_state(seqf[self._rig_flrig_blink_phase])
                            self._rig_flrig_blink_phase += 1
                        else:
                            self._rig_flrig_blink_active = False
                    if not self._rig_flrig_blink_active:
                        self.led_rig_flrig.set_state(fl_on)
                    self.lbl_rig_flrig.setText(
                        t("main.rig_flrig_host_port", host=fh, port=fp)
                        if fl_on
                        else t("main.rig_proto_stopped")
                    )
                    self._lbl_rig_flrig_n.setText(
                        t("main.rig_n_clients", n=n_fl) if fl_on else ""
                    )
                    self._led_rig_flrig_conn.set_state(fl_on and n_fl > 0)
                else:
                    self._rig_flrig_blink_phase = 0
                    self._rig_flrig_blink_active = False
                    self.led_rig_flrig.set_state(False)
                    self.lbl_rig_flrig.setText("")
                    self._lbl_rig_flrig_n.setText("")
                    self._led_rig_flrig_conn.set_state(False)

                hm_en = bool((rb_cfg.get("hamlib") or {}).get("enabled", False))
                hm_cfg = rb_cfg.get("hamlib") or {}
                ports = MainWindow._hamlib_listener_ports_sorted(hm_cfg)
                hm_names = MainWindow._hamlib_listener_names_by_port(hm_cfg)
                multi_ham = len(ports) > 1
                n_extra = (len(ports) - 1) if multi_ham else 0
                self._ensure_rig_hamlib_extra_rows(n_extra)
                hm_counts: dict[int, int] = {}
                if rbm is not None:
                    try:
                        hm_counts = rbm.hamlib_listener_client_counts()
                    except Exception:
                        hm_counts = {}
                if hm_en:
                    hm_on = bool(st.protocol_active.get("hamlib", False))
                    n_hm = int(st.protocol_clients.get("hamlib", 0) or 0)
                    hh = str(hm_cfg.get("host", "127.0.0.1") or "127.0.0.1")
                    seqh = self._rig_blink_sequence
                    _blink_keys = set(ports) if multi_ham else ({ports[0]} if len(ports) == 1 else {-1})
                    for k in list(self._rig_hamlib_blink_phase):
                        if k not in _blink_keys:
                            self._rig_hamlib_blink_phase.pop(k, None)
                            self._rig_hamlib_blink_active.pop(k, None)
                    ham_pairs: list[tuple[Led, int]] = []
                    if multi_ham:
                        ham_pairs.append((self.led_rig_hamlib, ports[0]))
                        for i in range(1, len(ports)):
                            ham_pairs.append((self._rig_hamlib_extra_rows[i - 1][1], ports[i]))
                    else:
                        _pk = ports[0] if len(ports) == 1 else -1
                        ham_pairs.append((self.led_rig_hamlib, _pk))
                    for led, pkey in ham_pairs:
                        trig = (pkey != -1 and pkey in h_ports) or (
                            pkey == -1 and bool(h_ports)
                        )
                        if trig:
                            self._rig_hamlib_blink_active[pkey] = True
                            self._rig_hamlib_blink_phase[pkey] = 0
                        if self._rig_hamlib_blink_active.get(pkey):
                            ph = self._rig_hamlib_blink_phase.get(pkey, 0)
                            if ph < len(seqh):
                                led.set_state(seqh[ph])
                                self._rig_hamlib_blink_phase[pkey] = ph + 1
                            else:
                                self._rig_hamlib_blink_active[pkey] = False
                        if not self._rig_hamlib_blink_active.get(pkey):
                            led.set_state(hm_on)
                    if multi_ham:
                        for i, p in enumerate(ports):
                            n_c = int(hm_counts.get(p, 0))
                            txt_hp = (
                                t("main.rig_flrig_host_port", host=hh, port=p)
                                if hm_on
                                else t("main.rig_proto_stopped")
                            )
                            txt_n = t("main.rig_n_clients", n=n_c) if hm_on else ""
                            cli_on = hm_on and n_c > 0
                            tip = hm_names.get(p, "")
                            if i == 0:
                                self.lbl_rig_hamlib.setText(txt_hp)
                                self._lbl_rig_hamlib_n.setText(txt_n)
                                self.lbl_rig_hamlib.setToolTip(tip)
                                self._led_rig_hamlib_conn.set_state(cli_on)
                                row0 = self.lbl_rig_hamlib.parentWidget()
                                if row0 is not None:
                                    row0.setToolTip(tip)
                            else:
                                wrow, _pl, lbl, lbl_n, cli_led = self._rig_hamlib_extra_rows[i - 1]
                                lbl.setText(txt_hp)
                                lbl_n.setText(txt_n)
                                lbl.setToolTip(tip)
                                cli_led.set_state(cli_on)
                                wrow.setToolTip(tip)
                    else:
                        if ports:
                            ham_detail = " · ".join(f"{hh}:{p}" for p in ports)
                        else:
                            ham_detail = f"{hh}:—"
                        self.lbl_rig_hamlib.setText(
                            t("main.rig_hamlib_detail_hosts", detail=ham_detail)
                            if hm_on
                            else t("main.rig_proto_stopped")
                        )
                        self._lbl_rig_hamlib_n.setText(
                            t("main.rig_n_clients", n=n_hm) if hm_on else ""
                        )
                        self._led_rig_hamlib_conn.set_state(hm_on and n_hm > 0)
                        tip1 = hm_names.get(ports[0], "") if len(ports) == 1 else ""
                        self.lbl_rig_hamlib.setToolTip(tip1)
                        row0 = self.lbl_rig_hamlib.parentWidget()
                        if row0 is not None:
                            row0.setToolTip(tip1 if len(ports) == 1 else "")
                else:
                    self._rig_hamlib_blink_phase.clear()
                    self._rig_hamlib_blink_active.clear()
                    self.led_rig_hamlib.set_state(False)
                    self.lbl_rig_hamlib.setText("")
                    self._lbl_rig_hamlib_n.setText("")
                    self.lbl_rig_hamlib.setToolTip("")
                    self._led_rig_hamlib_conn.set_state(False)
                    row0 = self.lbl_rig_hamlib.parentWidget()
                    if row0 is not None:
                        row0.setToolTip("")
                    for wrow, led, lbl, lbl_n, cli_led in self._rig_hamlib_extra_rows:
                        led.set_state(False)
                        lbl.setText("")
                        lbl_n.setText("")
                        lbl.setToolTip("")
                        cli_led.set_state(False)
                        wrow.setToolTip("")

            except Exception as e:
                self._log_exception("_tick rig-bridge LEDs", e)
        else:
            if rbm is not None:
                try:
                    rbm.take_rig_activity_flags()
                except Exception:
                    pass
            self.led_rig_serial.set_blinking_green(False)
            self.led_rig_serial.set_state(False)
            self._rig_flrig_blink_phase = 0
            self._rig_flrig_blink_active = False
            self.led_rig_flrig.set_state(False)
            self._led_rig_flrig_conn.set_state(False)
            self._rig_hamlib_blink_phase.clear()
            self._rig_hamlib_blink_active.clear()
            self.led_rig_hamlib.set_state(False)
            self.lbl_rig_serial.setText("")
            self.lbl_rig_flrig.setText("")
            if hasattr(self, "_lbl_rig_flrig_n"):
                self._lbl_rig_flrig_n.setText("")
            self.lbl_rig_hamlib.setText("")
            if hasattr(self, "_lbl_rig_hamlib_n"):
                self._lbl_rig_hamlib_n.setText("")
            self.lbl_rig_hamlib.setToolTip("")
            self._led_rig_hamlib_conn.set_state(False)
            row0 = self.lbl_rig_hamlib.parentWidget()
            if row0 is not None:
                row0.setToolTip("")
            for wrow, led, lbl, lbl_n, cli_led in getattr(self, "_rig_hamlib_extra_rows", []):
                led.set_state(False)
                lbl.setText("")
                lbl_n.setText("")
                lbl.setToolTip("")
                cli_led.set_state(False)
                wrow.setToolTip("")
            frw = getattr(self, "_rig_freq_row", None)
            if frw is not None and frw.isVisible():
                frw.setVisible(False)
                self._rig_freq_poll_timer.stop()
                self._apply_fixed_mainwindow_size()

        try:
            now = float(_time.time())
            if hw_on:
                self._hw_off_since = None
                self.led_hw.set_state(True)
            else:
                if self._hw_off_since is None:
                    self._hw_off_since = now
                self.led_hw.set_state((now - float(self._hw_off_since)) < 3.0)
        except Exception as e:
            self._log_exception("_tick led_hw / HW-Timeout-Anzeige", e)
            self.led_hw.set_state(hw_on)

        self.lbl_pst.setText(
            f"{t('main.pst_running') if pst_on else t('main.pst_stopped')}  AZ:{self.pst.port_az}  EL:{self.pst.port_el}  Host:{self.pst.host}"
        )
        hl = self.cfg["hardware_link"]
        mode = hl.get("mode", "tcp")
        ip = str(hl.get("tcp_ip", "") or "")
        port = str(hl.get("tcp_port", "") or "")
        if mode == "tcp":
            detail = f"{ip}:{port}"
            com_disp = ""
        else:
            com_disp = normalize_com_port(str(hl.get("com_port", "") or "")).upper()
            detail = com_disp
        if hw_on and pst_on:
            if mode == "tcp":
                self.lbl_hw.setText(f"{ip}:{port}")
            else:
                self.lbl_hw.setText(com_disp)
        elif hw_on:
            self.lbl_hw.setText(detail)
        else:
            self.lbl_hw.setText(f"{t('main.hw_disconnected')}  {detail}")

        try:
            self._lbl_srv_ucxlog_suffix.setText(
                self._udp_bind_status_text(
                    "udp_ucxlog_listen_host", "udp_ucxlog_port", "127.0.0.1", 12040
                )
            )
            self._lbl_srv_pst_udp_suffix.setText(
                self._udp_bind_status_text("udp_pst_listen_host", "udp_pst_port", "127.0.0.1", 12000)
            )
            self._lbl_srv_aswatch_suffix.setText(
                self._udp_bind_status_text(
                    "aswatch_udp_listen_host", "aswatch_udp_port", "127.0.0.1", 9872
                )
            )
        except Exception:
            pass

        self._update_axis_visibility()
        self._refresh_main_antenna_dropdown_labels_if_needed()
        wind_on = self._update_wind_visibility()
        axis_vis = (bool(self.gb_az.isVisible()), bool(self.gb_el.isVisible()))
        size_changed = False
        if self._last_axis_vis != axis_vis:
            self._last_axis_vis = axis_vis
            size_changed = True
        if self._last_wind_vis != wind_on:
            self._last_wind_vis = wind_on
            size_changed = True
        _rb = self._active_rig_view()
        _rig_en = bool(_rb.get("enabled", False))
        rig_srv_vis = (
            _rig_en,
            _rig_en and bool((_rb.get("flrig") or {}).get("enabled", False)),
            _rig_en and bool((_rb.get("hamlib") or {}).get("enabled", False)),
        )
        if getattr(self, "_last_rig_srv_vis", None) != rig_srv_vis:
            self._last_rig_srv_vis = rig_srv_vis
            size_changed = True
        if size_changed:
            self._apply_fixed_mainwindow_size()
        if self.gb_az.isVisible():
            fill_axis_panel(self.az_fields, self.ctrl.az)
            self._error_popup.maybe_show(self, "AZ", getattr(self.ctrl.az, "error_code", 0))
            self._warning_popup.maybe_show(self, "AZ", self.ctrl.az)
        if self.gb_el.isVisible():
            fill_axis_panel(self.el_fields, self.ctrl.el)
            self._error_popup.maybe_show(self, "EL", getattr(self.ctrl.el, "error_code", 0))
            self._warning_popup.maybe_show(self, "EL", self.ctrl.el)

        self._notify_pst_position()
        self._update_actions_locked_by_moving()
        self._update_title_bar()

        if self._log_win.isVisible():
            self._log_win.refresh()
