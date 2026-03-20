"""Hauptfenster der RotorTcpBridge-Anwendung."""
from __future__ import annotations

import threading

from PySide6.QtWidgets import (
    QSizePolicy,
    QMainWindow,
    QMessageBox,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGroupBox,
    QLabel,
    QPushButton,
    QLineEdit,
    QFormLayout,
    QMenuBar,
    QMenu,
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
from .ui_utils import px_to_dip
from .theme import apply_theme_mode
from .popup_handlers import ErrorPopupHandler, WarningPopupHandler
from .axis_widget import _make_axis_panel, fill_axis_panel


class MainWindow(QMainWindow):
    def __init__(self, cfg: dict, controller, pst_server, hw_client, save_cfg_cb, logbuf, udp_ucxlog=None):
        super().__init__()
        self.cfg = cfg
        self.ctrl = controller
        self.pst = pst_server
        self.hw = hw_client
        self.save_cfg_cb = save_cfg_cb
        self.logbuf = logbuf
        self._udp_ucxlog = udp_ucxlog
        self._hw_off_since: float | None = None
        self._last_title: str = ""
        self._ucxlog_blink_phase = 0
        self._ucxlog_blink_active = False
        self._ucxlog_blink_sequence = (True, False, True, False, True, False, True, False, True)

        self._update_title_bar()
        self.setWindowIcon(get_app_icon())
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, False)

        menubar = self.menuBar()
        self._menu_setup = menubar.addMenu(t("main.menu_setup"))
        self._act_commands = QAction(t("main.btn_commands"), self)
        self._act_commands.triggered.connect(self._open_commands)
        self._menu_setup.addAction(self._act_commands)
        self._act_settings = QAction(t("main.btn_settings"), self)
        self._act_settings.triggered.connect(self._open_settings)
        self._menu_setup.addAction(self._act_settings)
        self._act_log = QAction(t("main.btn_log"), self)
        self._act_log.triggered.connect(self._toggle_log)
        self._menu_setup.addAction(self._act_log)

        self._menu_help = menubar.addMenu(t("main.menu_help"))
        self._act_version = QAction(t("main.menu_version"), self)
        self._act_version.triggered.connect(self._open_about)
        self._menu_help.addAction(self._act_version)

        root = QWidget()
        self.setCentralWidget(root)
        main = QVBoxLayout(root)

        top = QHBoxLayout()
        self.btn_open_compass = QPushButton(t("main.btn_compass"))
        self.btn_open_map = QPushButton(t("main.btn_map"))
        self.btn_statistics = QPushButton(t("main.btn_statistics"))
        self.btn_open_weather = QPushButton(t("main.btn_weather"))
        self.btn_open_weather.setVisible(False)
        top.addWidget(self.btn_open_compass, 1)
        top.addWidget(self.btn_open_map, 1)
        top.addWidget(self.btn_statistics, 1)
        top.addWidget(self.btn_open_weather, 1)
        main.addLayout(top)

        gb_srv = QGroupBox(t("main.group_server"))
        main.addWidget(gb_srv)
        srv_form = QFormLayout(gb_srv)

        led_d = px_to_dip(self, 12)
        self.led_pst = Led(led_d, self)
        self.led_pst_conn = Led(led_d, self)
        self.led_ucxlog = Led(led_d, self)
        self.led_hw = Led(led_d, self)

        def _led_wrap(led) -> QWidget:
            w = QWidget()
            l = QVBoxLayout(w)
            l.setContentsMargins(0, 2, 0, 0)
            l.addWidget(led)
            return w

        self.ed_pst = QLineEdit()
        self.ed_pst.setReadOnly(True)
        self.ed_hw = QLineEdit()
        self.ed_hw.setReadOnly(True)

        pst_row = QHBoxLayout()
        pst_row.setContentsMargins(0, 0, 0, 0)
        pst_row.setSpacing(px_to_dip(self, 6))
        pst_row.addWidget(_led_wrap(self.led_pst))
        pst_row.addWidget(self.ed_pst, 1)
        pst_row_w = QWidget()
        pst_row_w.setLayout(pst_row)
        srv_form.addRow(t("main.srv_pst_label"), pst_row_w)

        hw_row = QHBoxLayout()
        hw_row.setContentsMargins(0, 0, 0, 0)
        hw_row.setSpacing(px_to_dip(self, 6))
        hw_row.addWidget(_led_wrap(self.led_hw))
        hw_row.addWidget(self.ed_hw, 1)
        hw_row_w = QWidget()
        hw_row_w.setLayout(hw_row)
        srv_form.addRow(t("main.srv_hw_label"), hw_row_w)

        pst_conn_row = QHBoxLayout()
        pst_conn_row.setContentsMargins(0, 0, 0, 0)
        pst_conn_row.setSpacing(px_to_dip(self, 6))
        pst_conn_row.addWidget(_led_wrap(self.led_pst_conn))
        pst_conn_row.addWidget(QLabel(t("main.srv_pst_conn_text")))
        pst_conn_row.addStretch(1)
        pst_conn_row_w = QWidget()
        pst_conn_row_w.setLayout(pst_conn_row)
        srv_form.addRow(t("main.srv_pst_conn_label"), pst_conn_row_w)

        ucxlog_row = QHBoxLayout()
        ucxlog_row.setContentsMargins(0, 0, 0, 0)
        ucxlog_row.setSpacing(px_to_dip(self, 6))
        ucxlog_row.addWidget(_led_wrap(self.led_ucxlog))
        ucxlog_row.addWidget(QLabel(t("main.srv_ucxlog_suffix")))
        ucxlog_row.addStretch(1)
        ucxlog_row_w = QWidget()
        ucxlog_row_w.setLayout(ucxlog_row)
        srv_form.addRow(t("main.srv_ucxlog_prefix"), ucxlog_row_w)

        try:
            srv_form.setVerticalSpacing(px_to_dip(self, 4))
            srv_form.setContentsMargins(px_to_dip(self, 8), px_to_dip(self, 4), px_to_dip(self, 8), px_to_dip(self, 4))
        except Exception:
            pass

        slave_az = self.cfg.get("rotor_bus", {}).get("slave_az", "?")
        slave_el = self.cfg.get("rotor_bus", {}).get("slave_el", "?")
        self.gb_az = QGroupBox(f"AZ ID:{slave_az}")
        self.gb_el = QGroupBox(f"EL ID:{slave_el}")
        main.addWidget(self.gb_az)
        main.addWidget(self.gb_el)

        self.az_fields = _make_axis_panel(self.gb_az, "az", self.ctrl)
        self.el_fields = _make_axis_panel(self.gb_el, "el", self.ctrl)

        gb_act = QGroupBox(t("main.group_actions"))
        main.addWidget(gb_act)
        gb_act.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        gb_act.setMaximumHeight(px_to_dip(self, 140))
        actions = QVBoxLayout(gb_act)

        self.btn_ref = QPushButton(t("main.btn_ref"))
        self.btn_stop = QPushButton(t("main.btn_stop"))
        self.btn_delwarn = QPushButton(t("main.btn_delwarn"))
        act_btn_row = QHBoxLayout()
        act_btn_row.addWidget(self.btn_ref, 1)
        act_btn_row.addWidget(self.btn_stop, 1)
        act_btn_row.addWidget(self.btn_delwarn, 1)
        actions.addLayout(act_btn_row)

        self.btn_ref.clicked.connect(lambda: self.ctrl.reference_all(True))
        self.btn_stop.clicked.connect(self.ctrl.stop_all)
        self.btn_delwarn.clicked.connect(self.ctrl.clear_warnings_all)

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

        self._log_win = LogWindow(self.logbuf, parent=None)
        self._compass_win = CompassWindow(self.cfg, self.ctrl, self.save_cfg_cb, parent=None)
        self._map_win = MapWindow(self.cfg, self.ctrl, self.save_cfg_cb, parent=None)
        self._settings_win = SettingsWindow(
            self.cfg, self.ctrl, self.pst, self.hw, self.save_cfg_cb, self.logbuf,
            after_apply_cb=self._after_settings_applied,
            rebuild_ui_cb=self._rebuild_all_windows,
            map_window=self._map_win,
            parent=None,
        )
        self._statistics_win = StatisticsWindow(self.cfg, self.ctrl, parent=None)
        self._weather_win = WeatherWindow(self.cfg, self.ctrl, parent=None)
        self._commands_win = CommandButtonsWindow(self.cfg, self.ctrl, self.save_cfg_cb, parent=None)

        self.btn_open_compass.clicked.connect(self._open_compass)
        self.btn_open_map.clicked.connect(self._open_map)
        self.btn_statistics.clicked.connect(self._open_statistics)
        self.btn_open_weather.clicked.connect(self._open_weather)

        self._fixed_w = None
        self._fixed_h = None
        self._last_axis_vis: tuple[bool, bool] | None = None
        self._last_wind_vis: bool | None = None
        self._error_popup = ErrorPopupHandler()
        self._warning_popup = WarningPopupHandler()

        apply_theme_mode(self.cfg)
        self._update_axis_visibility()
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
        """Titelleiste dynamisch aktualisieren: Basis + aktuelle Position."""
        base = f"{t('app.title')} v{APP_VERSION}"
        try:
            hw_on = bool(self.hw.is_connected())
            if hw_on:
                parts: list[str] = []
                if bool(getattr(self.ctrl, "enable_az", True)):
                    az_d10 = getattr(self.ctrl.az, "pos_d10", None)
                    if az_d10 is not None:
                        parts.append(f"AZ: {az_d10 / 10:.1f}°")
                if bool(getattr(self.ctrl, "enable_el", True)):
                    el_d10 = getattr(self.ctrl.el, "pos_d10", None)
                    if el_d10 is not None:
                        parts.append(f"EL: {el_d10 / 10:.1f}°")
                if parts:
                    title = f"{base} \u2014 {' '.join(parts)}"
                else:
                    title = base
            else:
                title = base
        except Exception:
            title = base
        if title != self._last_title:
            self._last_title = title
            self.setWindowTitle(title)

    def _retranslate_ui(self):
        """Alle Texte des Hauptfensters auf die aktuelle Sprache aktualisieren."""
        self._last_title = ""  # Cache zurücksetzen damit Neuaufbau greift
        self._update_title_bar()
        self._menu_setup.setTitle(t("main.menu_setup"))
        self._act_commands.setText(t("main.btn_commands"))
        self._act_settings.setText(t("main.btn_settings"))
        self._act_log.setText(t("main.btn_log"))
        self._menu_help.setTitle(t("main.menu_help"))
        self._act_version.setText(t("main.menu_version"))
        self.btn_open_compass.setText(t("main.btn_compass"))
        self.btn_open_map.setText(t("main.btn_map"))
        self.btn_open_weather.setText(t("main.btn_weather"))
        self.btn_ref.setText(t("main.btn_ref"))
        self.btn_stop.setText(t("main.btn_stop"))
        self.btn_delwarn.setText(t("main.btn_delwarn"))
        self.btn_statistics.setText(t("main.btn_statistics"))

    def _rebuild_all_windows(self):
        """Alle Fenster schließen und neu erstellen (nach Sprachänderung)."""
        try:
            for attr in ("_log_win", "_compass_win", "_map_win", "_statistics_win", "_weather_win", "_commands_win"):
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
            self._log_win = LogWindow(self.logbuf, parent=None)
            self._compass_win = CompassWindow(self.cfg, self.ctrl, self.save_cfg_cb, parent=None)
            self._map_win = MapWindow(self.cfg, self.ctrl, self.save_cfg_cb, parent=None)
            self._statistics_win = StatisticsWindow(self.cfg, self.ctrl, parent=None)
            self._weather_win = WeatherWindow(self.cfg, self.ctrl, parent=None)
            self._commands_win = CommandButtonsWindow(self.cfg, self.ctrl, self.save_cfg_cb, parent=None)
            self._retranslate_ui()
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
                self.cfg, self.ctrl, self.pst, self.hw, self.save_cfg_cb, self.logbuf,
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
        self._apply_fixed_mainwindow_size()
        if hasattr(self, "_map_win") and self._map_win is not None:
            try:
                self._map_win.on_settings_applied()
            except Exception:
                pass
            if self._internet_online is not None:
                self._map_win.apply_internet_status(self._internet_online)
        if self._udp_ucxlog is not None:
            ui = self.cfg.get("ui", {})
            self._udp_ucxlog.start(
                enabled=bool(ui.get("udp_ucxlog_enabled", False)),
                port=int(ui.get("udp_ucxlog_port", 12040)),
            )
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
            except Exception:
                online = False
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
            w1 = self.az_fields.get("wind_pair_w")
            if w1 is not None:
                w1.setVisible(wind_on)
            w2 = self.az_fields.get("winddir_pair_w")
            if w2 is not None:
                w2.setVisible(wind_on)
            w3 = self.az_fields.get("wind_bft_pair_w")
            if w3 is not None:
                w3.setVisible(wind_on)
        except Exception:
            pass
        try:
            if hasattr(self, "btn_open_weather"):
                self.btn_open_weather.setVisible(wind_on)
            if (not wind_on) and hasattr(self, "_weather_win") and self._weather_win.isVisible():
                self._weather_win.hide()
        except Exception:
            pass
        return wind_on

    def _apply_fixed_mainwindow_size(self):
        width = px_to_dip(self, 460)
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
            for w in (getattr(self, "_log_win", None), getattr(self, "_settings_win", None),
                     getattr(self, "_compass_win", None), getattr(self, "_map_win", None),
                     getattr(self, "_statistics_win", None), getattr(self, "_weather_win", None),
                     getattr(self, "_commands_win", None)):
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

    def _tick(self):
        import time as _time
        self.ctrl.tick_polling()

        pst_on = bool(self.pst.running)
        hw_on = bool(self.hw.is_connected())
        self.led_pst.set_state(pst_on)
        try:
            last_rx = float(getattr(self.pst, "last_rx_ts", 0.0) or 0.0)
            pst_recent = pst_on and (last_rx > 0.0) and ((_time.time() - last_rx) <= 2.0)
        except Exception:
            pst_recent = False
        try:
            self.led_pst_conn.set_state(bool(pst_recent))
        except Exception:
            pass

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

        try:
            now = float(_time.time())
            if hw_on:
                self._hw_off_since = None
                self.led_hw.set_state(True)
            else:
                if self._hw_off_since is None:
                    self._hw_off_since = now
                self.led_hw.set_state((now - float(self._hw_off_since)) < 3.0)
        except Exception:
            self.led_hw.set_state(hw_on)

        self.ed_pst.setText(f"{t('main.pst_running') if pst_on else t('main.pst_stopped')}  AZ:{self.pst.port_az}  EL:{self.pst.port_el}  Host:{self.pst.host}")
        mode = self.cfg["hardware_link"].get("mode", "tcp")
        if mode == "tcp":
            ip = self.cfg["hardware_link"].get("tcp_ip", "")
            port = self.cfg["hardware_link"].get("tcp_port", "")
            self.ed_hw.setText(f"{t('main.hw_connected') if hw_on else t('main.hw_disconnected')}  TCP {ip}:{port}")
        else:
            com = self.cfg["hardware_link"].get("com_port", "")
            self.ed_hw.setText(f"{t('main.hw_connected') if hw_on else t('main.hw_disconnected')}  COM {com} @ 115200")

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

        self._update_title_bar()

        if self._log_win.isVisible():
            self._log_win.refresh()
