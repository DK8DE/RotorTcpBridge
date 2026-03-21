"""Hauptfenster der RotorTcpBridge-Anwendung."""

from __future__ import annotations

import threading

from PySide6.QtWidgets import (
    QMainWindow,
    QMessageBox,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGroupBox,
    QLabel,
    QPushButton,
    QFormLayout,
)
from PySide6.QtGui import QAction
from PySide6.QtCore import Qt, QTimer

from ..app_icon import get_app_icon
from ..i18n import t
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
from .command_buttons_window import CommandButtonsWindow
from .warnings_errors_window import WarningsErrorsWindow
from .ui_utils import px_to_dip
from .theme import apply_theme_mode
from .popup_handlers import ErrorPopupHandler, WarningPopupHandler
from .axis_widget import _make_axis_panel, fill_axis_panel, retranslate_axis_panel


class MainWindow(QMainWindow):
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
        if aswatch_bridge is not None:
            try:
                aswatch_bridge.users.connect(
                    self._on_aswatch_users, Qt.ConnectionType.QueuedConnection
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

        self.gb_srv = QGroupBox(t("main.group_server"))
        main.addWidget(self.gb_srv)
        srv_form = QFormLayout(self.gb_srv)
        self._srv_form = srv_form

        led_d = px_to_dip(self, 12)
        self.led_pst = Led(led_d, self)
        self.led_pst_conn = Led(led_d, self)
        self.led_ucxlog = Led(led_d, self)
        self.led_pst_udp = Led(led_d, self)
        self.led_aswatch = Led(led_d, self)
        self.led_hw = Led(led_d, self)

        def _led_wrap(led) -> QWidget:
            w = QWidget()
            l = QVBoxLayout(w)
            l.setContentsMargins(0, 2, 0, 0)
            l.addWidget(led)
            return w

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
        hw_row.addWidget(_led_wrap(self.led_hw))
        hw_row.addWidget(self.lbl_hw, 1)
        hw_row_w = QWidget()
        hw_row_w.setLayout(hw_row)
        srv_form.addRow(t("main.srv_hw_label"), hw_row_w)
        self._srv_row_hw_w = hw_row_w

        pst_row = QHBoxLayout()
        pst_row.setContentsMargins(0, 0, 0, 0)
        pst_row.setSpacing(px_to_dip(self, 6))
        pst_row.addWidget(_led_wrap(self.led_pst))
        pst_row.addWidget(self.lbl_pst, 1)
        pst_row_w = QWidget()
        pst_row_w.setLayout(pst_row)
        srv_form.addRow(t("main.srv_pst_label"), pst_row_w)
        self._srv_row_pst_w = pst_row_w

        pst_conn_row = QHBoxLayout()
        pst_conn_row.setContentsMargins(0, 0, 0, 0)
        pst_conn_row.setSpacing(px_to_dip(self, 6))
        pst_conn_row.addWidget(_led_wrap(self.led_pst_conn))
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
        ucxlog_row.addWidget(_led_wrap(self.led_ucxlog))
        self._lbl_srv_ucxlog_suffix = QLabel(t("main.srv_ucxlog_suffix"))
        ucxlog_row.addWidget(self._lbl_srv_ucxlog_suffix)
        ucxlog_row.addStretch(1)
        ucxlog_row_w = QWidget()
        ucxlog_row_w.setLayout(ucxlog_row)
        srv_form.addRow(t("main.srv_ucxlog_prefix"), ucxlog_row_w)
        self._srv_row_ucxlog_w = ucxlog_row_w

        pst_udp_row = QHBoxLayout()
        pst_udp_row.setContentsMargins(0, 0, 0, 0)
        pst_udp_row.setSpacing(px_to_dip(self, 6))
        pst_udp_row.addWidget(_led_wrap(self.led_pst_udp))
        self._lbl_srv_pst_udp_suffix = QLabel(t("main.srv_pst_udp_suffix"))
        pst_udp_row.addWidget(self._lbl_srv_pst_udp_suffix)
        pst_udp_row.addStretch(1)
        pst_udp_row_w = QWidget()
        pst_udp_row_w.setLayout(pst_udp_row)
        srv_form.addRow(t("main.srv_pst_udp_prefix"), pst_udp_row_w)
        self._srv_row_pst_udp_w = pst_udp_row_w

        aswatch_row = QHBoxLayout()
        aswatch_row.setContentsMargins(0, 0, 0, 0)
        aswatch_row.setSpacing(px_to_dip(self, 6))
        aswatch_row.addWidget(_led_wrap(self.led_aswatch))
        self._lbl_srv_aswatch_suffix = QLabel("")
        self._lbl_srv_aswatch_suffix.setWordWrap(False)
        aswatch_row.addWidget(self._lbl_srv_aswatch_suffix)
        aswatch_row.addStretch(1)
        aswatch_row_w = QWidget()
        aswatch_row_w.setLayout(aswatch_row)
        srv_form.addRow(t("main.srv_aswatch_label"), aswatch_row_w)
        self._srv_row_aswatch_w = aswatch_row_w
        try:
            _ui0 = self.cfg.get("ui", {})
            _p0 = int(_ui0.get("aswatch_udp_port", 9872))
            self._lbl_srv_aswatch_suffix.setText(t("main.srv_aswatch_suffix", port=_p0))
        except Exception:
            pass

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
        self._compass_win = CompassWindow(self.cfg, self.ctrl, self.save_cfg_cb, parent=None)
        self._map_win = MapWindow(self.cfg, self.ctrl, self.save_cfg_cb, parent=None)
        self._settings_win = SettingsWindow(
            self.cfg,
            self.ctrl,
            self.pst,
            self.hw,
            self.save_cfg_cb,
            self.logbuf,
            after_apply_cb=self._after_settings_applied,
            rebuild_ui_cb=self._rebuild_all_windows,
            map_window=self._map_win,
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

        apply_theme_mode(self.cfg)
        self._update_axis_visibility()
        self._update_srv_rows_visibility()
        self._apply_fixed_mainwindow_size()

    def _open_about(self):
        dlg = AboutWindow(parent=self)
        dlg.exec()

    def _open_settings(self):
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
        """Titelleiste: App-Name und Version (ohne Live-AZ/EL)."""
        title = f"{t('app.title')} v{APP_VERSION}"
        if title != self._last_title:
            self._last_title = title
            self.setWindowTitle(title)

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
        self.btn_open_compass.setText(t("main.btn_compass"))
        self.btn_open_map.setText(t("main.btn_map"))
        self.btn_ref.setText(t("main.btn_ref"))
        try:
            self.gb_control.setTitle(t("main.group_control"))
        except Exception:
            pass
        # Server-GroupBox: Überschriften + Beschriftungen der Formularzeilen
        try:
            self.gb_srv.setTitle(t("main.group_server"))
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
            self._lbl_srv_pst_conn.setText(t("main.srv_pst_conn_text"))
            self._lbl_srv_ucxlog_suffix.setText(t("main.srv_ucxlog_suffix"))
            self._lbl_srv_pst_udp_suffix.setText(t("main.srv_pst_udp_suffix"))
            ui = self.cfg.get("ui", {})
            _p = int(ui.get("aswatch_udp_port", 9872))
            self._lbl_srv_aswatch_suffix.setText(t("main.srv_aswatch_suffix", port=_p))
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
            from .command_buttons_window import CommandButtonsWindow
            from .log_window import LogWindow
            from .warnings_errors_window import WarningsErrorsWindow

            self._log_win = LogWindow(self.logbuf, parent=None)
            self._compass_win = CompassWindow(self.cfg, self.ctrl, self.save_cfg_cb, parent=None)
            self._map_win = MapWindow(self.cfg, self.ctrl, self.save_cfg_cb, parent=None)
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
                rebuild_ui_cb=self._rebuild_all_windows,
                map_window=self._map_win,
                parent=None,
            )
        except Exception:
            pass

    def _after_settings_applied(self):
        apply_theme_mode(self.cfg)
        self._update_groupbox_titles()
        self._update_axis_visibility()
        self._update_srv_rows_visibility()
        self._apply_fixed_mainwindow_size()
        # PST-Server starten oder stoppen je nach Einstellung
        pst_enabled = bool(self.cfg.get("pst_server", {}).get("enabled", True))
        try:
            if pst_enabled and not self.pst.running:
                self.pst.start()
            elif not pst_enabled and self.pst.running:
                self.pst.stop()
        except Exception as e:
            self._log_exception("_after_settings_applied PST start/stop", e)
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
            )
        if self._udp_pst is not None:
            ui = self.cfg.get("ui", {})
            self._udp_pst.start(
                enabled=bool(ui.get("udp_pst_enabled", False)),
                port=int(ui.get("udp_pst_port", 12000)),
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
            )
        # Hauptfenster-Texte (Server, AZ/EL, Menü, …) nach load_lang / Einstellungen synchronisieren
        self._retranslate_ui()
        if hasattr(self, "_compass_win") and self._compass_win.isVisible():
            self._compass_win._update_groupbox_titles()
            if hasattr(self._compass_win, "_apply_label_colors_from_palette"):
                self._compass_win._apply_label_colors_from_palette()

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
            if hasattr(self, "_map_win") and self._map_win is not None:
                self._map_win.update_aswatch_users(items)
        except Exception:
            pass

    def _open_compass(self):
        try:
            if hasattr(self._compass_win, "_update_groupbox_titles"):
                self._compass_win._update_groupbox_titles()
            if hasattr(self._compass_win, "refresh_visibility"):
                self._compass_win.refresh_visibility()
            self._compass_win.show()
            self._compass_win.raise_()
            self._compass_win.activateWindow()
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
            self._map_win.show()
            self._map_win.raise_()
            self._map_win.activateWindow()
        except Exception:
            pass

    def _open_commands(self):
        try:
            if hasattr(self._commands_win, "_refresh_dst_dropdown"):
                self._commands_win._refresh_dst_dropdown()
            self._commands_win.show()
            self._commands_win.raise_()
            self._commands_win.activateWindow()
        except Exception:
            pass

    def _open_statistics(self):
        try:
            self._statistics_win.show()
            self._statistics_win.raise_()
            self._statistics_win.activateWindow()
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
        try:
            if hasattr(self, "_compass_win") and hasattr(self._compass_win, "refresh_visibility"):
                self._compass_win.refresh_visibility()
        except Exception:
            pass

    def _update_srv_rows_visibility(self) -> None:
        """Server-GroupBox-Zeilen je nach aktivierten Diensten ein-/ausblenden."""
        ui = self.cfg.get("ui", {})
        pst_on = bool(self.cfg.get("pst_server", {}).get("enabled", True))
        ucxlog_on = bool(ui.get("udp_ucxlog_enabled", False))
        pst_udp_on = bool(ui.get("udp_pst_enabled", False))
        aswatch_on = bool(ui.get("aswatch_udp_enabled", False))
        try:
            self._srv_form.setRowVisible(self._srv_row_pst_w, pst_on)
            self._srv_form.setRowVisible(self._srv_row_pst_conn_w, pst_on)
            self._srv_form.setRowVisible(self._srv_row_ucxlog_w, ucxlog_on)
            self._srv_form.setRowVisible(self._srv_row_pst_udp_w, pst_udp_on)
            self._srv_form.setRowVisible(self._srv_row_aswatch_w, aswatch_on)
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

    def _apply_fixed_mainwindow_size(self):
        # Feste Fensterbreite (DIP) — schmales Hauptfenster; Achsen-Layout nutzt Stretch in den Wert-Spalten
        width = px_to_dip(self, 345)
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
        self._update_axis_visibility()
        self._apply_fixed_mainwindow_size()

    def closeEvent(self, event):
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
        mode = self.cfg["hardware_link"].get("mode", "tcp")
        if mode == "tcp":
            ip = self.cfg["hardware_link"].get("tcp_ip", "")
            port = self.cfg["hardware_link"].get("tcp_port", "")
            detail = f"TCP {ip}:{port}"
        else:
            com = self.cfg["hardware_link"].get("com_port", "")
            detail = f"COM {com} @ 115200"
        if hw_on and pst_on:
            self.lbl_hw.setText(f"{t('main.hw_connected_via_pst')}  {detail}")
        elif hw_on:
            self.lbl_hw.setText(f"{t('main.hw_connected')}  {detail}")
        else:
            self.lbl_hw.setText(f"{t('main.hw_disconnected')}  {detail}")

        try:
            ui = self.cfg.get("ui", {})
            if bool(ui.get("aswatch_udp_enabled", False)):
                _ap = int(ui.get("aswatch_udp_port", 9872))
                self._lbl_srv_aswatch_suffix.setText(t("main.srv_aswatch_suffix", port=_ap))
        except Exception:
            pass

        self._update_axis_visibility()
        wind_on = self._update_wind_visibility()
        axis_vis = (bool(self.gb_az.isVisible()), bool(self.gb_el.isVisible()))
        size_changed = False
        if self._last_axis_vis != axis_vis:
            self._last_axis_vis = axis_vis
            size_changed = True
        if self._last_wind_vis != wind_on:
            self._last_wind_vis = wind_on
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
        self._update_title_bar()

        if self._log_win.isVisible():
            self._log_win.refresh()
