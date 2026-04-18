"""UI-Tab für Rig-Bridge-Einstellungen."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QDoubleValidator, QIntValidator, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QLineEdit,
)

from ..i18n import format_tooltip, t
from ..ports import list_serial_ports
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

        gb_general = QGroupBox("Allgemein")
        fl_general = QFormLayout(gb_general)
        self.chk_enabled = QCheckBox("Rig-Bridge aktiv")
        self.cb_rig_brand = QComboBox()
        self.cb_rig_model = QComboBox()
        self.lbl_rig_info = QLabel("-")
        self._hamlib_models: list[dict[str, str | int]] = []
        btn_row_general = QWidget()
        hl_general = QHBoxLayout(btn_row_general)
        hl_general.setContentsMargins(0, 0, 0, 0)
        self.btn_test = QPushButton("Verbindung testen")
        self.btn_test.setToolTip(
            format_tooltip(
                "Sendet Set-Frequenz auf 144,300 MHz (CAT je Marke, z. B. Yaesu FA…;) und protokolliert RX.\n"
                "Mit aktiver „Verbinden“-Session: gleicher COM-Port, kurz serialisiert gegen den Worker.\n"
                "Ohne Verbindung: COM wird kurz separat geöffnet. Reine Ziffer im Port wird zu COMn (Windows)."
            )
        )
        self.btn_test.setMinimumWidth(170)
        hl_general.addWidget(self.btn_test)
        fl_general.addRow(self.chk_enabled)
        fl_general.addRow("Funkgeräte-Marke", self.cb_rig_brand)
        fl_general.addRow("Funkgeräte-Modell", self.cb_rig_model)
        fl_general.addRow("Hamlib-Modell-ID", self.lbl_rig_info)
        fl_general.addRow(btn_row_general)

        gb_status = QGroupBox("Statusanzeige")
        fl_status = QFormLayout(gb_status)
        self.led_radio = Led(14, self)
        self.lbl_status = QLabel("-")
        self.lbl_error = QLabel("-")
        self.lbl_last = QLabel("-")
        self.lbl_com = QLabel("-")
        led_wrap = QWidget()
        led_l = QHBoxLayout(led_wrap)
        led_l.setContentsMargins(0, 0, 0, 0)
        led_l.addWidget(self.led_radio, 0, Qt.AlignmentFlag.AlignLeft)
        led_l.addWidget(self.lbl_status, 1)
        fl_status.addRow("Verbindung", led_wrap)
        fl_status.addRow("Letzter Fehler", self.lbl_error)
        fl_status.addRow("Letzter Kontakt", self.lbl_last)
        fl_status.addRow("COM-Port", self.lbl_com)

        gb_serial = QGroupBox("Funkgerät / serielle Verbindung")
        outer_serial = QVBoxLayout(gb_serial)
        self.chk_auto_connect = QCheckBox("Bei Programmstart mit Funkgerät verbinden")
        self.chk_auto_connect.setToolTip(
            format_tooltip(
                "Wenn Rig-Bridge aktiv ist: COM beim Start der Anwendung öffnen. "
                "Danach starten Flrig und Hamlib (rigctld) automatisch, sofern dort „Autostart“ gesetzt ist."
            )
        )
        self.chk_auto_reconnect = QCheckBox("Automatisch wieder verbinden")
        self.chk_auto_reconnect.setToolTip(
            format_tooltip(
                "Vorgabe für künftige Wiederherstellung der COM-Session (Konfiguration); "
                "die Rig-Bridge nutzt sie, sobald die Logik dafür angebunden ist."
            )
        )
        outer_serial.addWidget(self.chk_auto_connect)
        outer_serial.addWidget(self.chk_auto_reconnect)
        grid_serial = QGridLayout()
        outer_serial.addLayout(grid_serial)
        self.cb_com = QComboBox()
        self.cb_com.setToolTip(
            format_tooltip(
                "Serieller COM-Port des Funkgeräts (CAT).\n"
                "Nach USB-Stecker ziehen/neu einstecken oder anderem Port: Liste mit „↻“ aktualisieren.\n"
                "Reine Ziffern werden unter Windows zu COMn aufgelöst."
            )
        )
        self.btn_refresh_com = QPushButton("\u21bb")
        self.btn_refresh_com.setToolTip(format_tooltip("COM-Ports aktualisieren"))
        self.btn_refresh_com.setFixedWidth(px_to_dip(self, 34))
        _nw = 88
        self.cb_baud = QComboBox()
        for br in _BAUD_RATES:
            self.cb_baud.addItem(str(br), br)
        self.cb_baud.setFixedWidth(px_to_dip(self, _nw))
        self.cb_baud.setToolTip(
            format_tooltip(
                "UART-Baudrate der CAT-Schnittstelle laut Funkgeräte-Handbuch "
                "(typisch 38400, 9600 oder 115200). Muss zum Gerät passen, sonst keine zuverlässige CAT-Kommunikation."
            )
        )
        self.lbl_serial_frame = QLabel(
            "Serieller Rahmen (fest): 8 Datenbits · 1 Stopbit · keine Parität (8N1)"
        )
        self.lbl_serial_frame.setWordWrap(True)
        self.lbl_serial_frame.setToolTip(
            format_tooltip(
                "Fest vorgegeben und nicht einstellbar: 8 Datenbits, 1 Stopbit, keine Parität (8N1).\n"
                "Entspricht der üblichen CAT-UART vieler Hersteller."
            )
        )
        self.ed_timeout = _make_float_line_edit(self, 0.05, 10.0, 2, _nw)
        self.ed_timeout.setToolTip(
            format_tooltip(
                "Maximale Wartezeit pro serieller CAT-Operation in Sekunden (Lesen/Schreiben am COM-Port).\n"
                "Zu klein: Timeouts oder abgebrochene Befehle bei langsamen Geräten oder langen Kabeln.\n"
                "Zu groß: längere Blockaden, falls das Gerät nicht antwortet."
            )
        )
        self.ed_poll = _make_int_line_edit(self, 30, 5000, _nw)
        self.ed_poll.setToolTip(
            format_tooltip(
                "Abstand in Millisekunden zwischen periodischen CAT-Abfragen (z. B. Frequenz), "
                "wenn Clients oder die Anzeige aktuelle Werte vom Funkgerät brauchen.\n"
                "Kleiner = schnellere Aktualisierung, mehr Last auf COM und Gerät; größer = weniger Traffic."
            )
        )
        self.ed_cat_drain = _make_int_line_edit(self, 20, 500, _nw)
        self.ed_cat_drain.setToolTip(
            format_tooltip(
                "Nach jedem CAT-Schreibbefehl (FA/TX/MD): maximal so lange auf Echo oder `;` warten "
                "(Millisekunden). Zu groß = langsame WSJT-X-Runden; zu klein = evtl. verpasstes Echo bei manchen Geräten."
            )
        )
        self.ed_setfreq_gap = _make_int_line_edit(self, 0, 200, _nw)
        self.ed_setfreq_gap.setToolTip(
            format_tooltip(
                "Kurze Pause nach jedem seriellen SETFREQ (FA…;) in Millisekunden. Hilft, wenn das Funkgerät bei schnellem "
                "Abstimmen mit ``?;`` antwortet oder Schritte auslässt. 0 = keine zusätzliche Pause."
            )
        )
        self.btn_connect = QPushButton("Verbinden")
        self.btn_connect.setToolTip(
            format_tooltip(
                "Öffnet die serielle CAT-Session zum gewählten COM-Port mit eingestellter Baudrate.\n"
                "Vorher Marke/Modell prüfen; danach können Flrig/Hamlib über dieselbe Session arbeiten."
            )
        )
        self.btn_disconnect = QPushButton("Trennen")
        self.btn_disconnect.setToolTip(
            format_tooltip(
                "Schließt die CAT-Verbindung zum Funkgerät.\n"
                "Laufende TCP-Dienste (Flrig/Hamlib) bitte separat mit „Stop“ beenden, falls aktiv."
            )
        )
        btn_serial_col = QWidget()
        hl_btn_serial = QHBoxLayout(btn_serial_col)
        hl_btn_serial.setContentsMargins(0, 0, 0, 0)
        hl_btn_serial.setSpacing(8)
        hl_btn_serial.addWidget(self.btn_connect, 0)
        hl_btn_serial.addWidget(self.btn_disconnect, 0)
        hl_btn_serial.addStretch(1)
        grid_serial.addWidget(QLabel("COM-Port"), 0, 0)
        grid_serial.addWidget(self.cb_com, 0, 1)
        grid_serial.addWidget(self.btn_refresh_com, 0, 2)
        grid_serial.addWidget(QLabel("Baudrate"), 1, 0)
        grid_serial.addWidget(self.cb_baud, 1, 1)
        grid_serial.addWidget(self.lbl_serial_frame, 2, 0, 1, 3)
        grid_serial.addWidget(QLabel("Timeout (s)"), 3, 0)
        grid_serial.addWidget(self.ed_timeout, 3, 1)
        grid_serial.addWidget(QLabel("Polling-Intervall (ms)"), 4, 0)
        grid_serial.addWidget(self.ed_poll, 4, 1)
        grid_serial.addWidget(QLabel("CAT-Drain nach Schreiben (max., ms)"), 5, 0)
        grid_serial.addWidget(self.ed_cat_drain, 5, 1)
        grid_serial.addWidget(QLabel("Pause nach SETFREQ (ms)"), 6, 0)
        grid_serial.addWidget(self.ed_setfreq_gap, 6, 1)
        grid_serial.addWidget(btn_serial_col, 7, 1, 1, 2)

        gb_protocols = QGroupBox("Protokolle")
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
            title = "Flrig"
            gb_single = QGroupBox(title)
            fl_single = QFormLayout(gb_single)
            chk = QCheckBox(f"{title} aktiv")
            host = QLineEdit()
            host.setFixedWidth(px_to_dip(self, 100))
            port = QLineEdit()
            port.setValidator(QIntValidator(1, 65535, self))
            port.setFixedWidth(px_to_dip(self, 56))
            chk_auto = QCheckBox("Start beim Programmstart")
            led = Led(12, self)
            self._lbl_flrig_bind_clients = QLabel("")
            self._lbl_flrig_bind_clients.setWordWrap(True)
            cli_led = Led(12, self)
            btn_start = QPushButton("Start")
            btn_stop = QPushButton("Stop")
            row_host_port = QWidget()
            hl_host_port = QHBoxLayout(row_host_port)
            hl_host_port.setContentsMargins(0, 0, 0, 0)
            hl_host_port.setSpacing(8)
            hl_host_port.addWidget(QLabel("Host"))
            hl_host_port.addWidget(host, 1)
            hl_host_port.addWidget(QLabel("Port"))
            hl_host_port.addWidget(port, 0)
            row_status = QWidget()
            hl_status = QHBoxLayout(row_status)
            hl_status.setContentsMargins(0, 0, 0, 0)
            hl_status.setSpacing(8)
            hl_status.addWidget(QLabel("Status"))
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
            fl_single.addRow(row_status)
            vl_protocols.addWidget(gb_single)

        gb_hamlib = QGroupBox("Hamlib NET rigctl")
        fl_hamlib = QFormLayout(gb_hamlib)
        self._protocol_enabled["hamlib"] = QCheckBox("Hamlib NET rigctl aktiv")
        self._hamlib_host = QLineEdit()
        self._hamlib_host.setFixedWidth(px_to_dip(self, 100))
        row_hamlib_host = QWidget()
        hl_hh = QHBoxLayout(row_hamlib_host)
        hl_hh.setContentsMargins(0, 0, 0, 0)
        hl_hh.setSpacing(8)
        hl_hh.addWidget(QLabel("Host"))
        hl_hh.addWidget(self._hamlib_host, 1)
        self._hamlib_rows_box = QWidget()
        self._hamlib_rows_layout = QVBoxLayout(self._hamlib_rows_box)
        self._hamlib_rows_layout.setContentsMargins(0, 0, 0, 0)
        self._hamlib_rows_layout.setSpacing(6)
        self._hamlib_rows: list[tuple[QLineEdit, QLineEdit, QLabel, QLabel, Led, QWidget]] = []
        self.btn_hamlib_add_row = QPushButton("Zeile hinzufügen")
        self.btn_hamlib_add_row.clicked.connect(lambda: self._hamlib_add_row("", ""))
        self._protocol_autostart["hamlib"] = QCheckBox("Start beim Programmstart")
        self.chk_hamlib_debug = QCheckBox("Hamlib-Diagnose (Zeitabstände + lange Antworten)")
        self.chk_hamlib_debug.setToolTip(
            format_tooltip(
                "Zusätzlich zu „Rig-Befehle loggen“: Zeitabstände (+ms) zwischen TCP-Befehlen "
                "und Dauer jedes COM-SETFREQ."
            )
        )
        self._protocol_leds["hamlib"] = Led(12, self)
        self._lbl_hamlib_bind_clients = QLabel("")
        self._lbl_hamlib_bind_clients.setWordWrap(False)
        self._protocol_start["hamlib"] = QPushButton("Start")
        self._protocol_stop["hamlib"] = QPushButton("Stop")
        row_hamlib_status = QWidget()
        hl_hs = QHBoxLayout(row_hamlib_status)
        hl_hs.setContentsMargins(0, 0, 0, 0)
        hl_hs.setSpacing(8)
        hl_hs.addWidget(QLabel("Status"))
        hl_hs.addWidget(self._protocol_leds["hamlib"], 0, Qt.AlignmentFlag.AlignLeft)
        hl_hs.addSpacing(10)
        hl_hs.addWidget(self._lbl_hamlib_bind_clients, 1)
        hl_hs.addWidget(self._protocol_start["hamlib"], 0)
        hl_hs.addWidget(self._protocol_stop["hamlib"], 0)
        fl_hamlib.addRow(self._protocol_enabled["hamlib"])
        fl_hamlib.addRow(row_hamlib_host)
        lbl_hamlib_ports = QLabel(
            "Ports / Programm (je Zeile ein Listener, gleicher Host oben):"
        )
        lbl_hamlib_ports.setWordWrap(True)
        lbl_hamlib_ports.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        fl_hamlib.addRow(lbl_hamlib_ports)
        fl_hamlib.addRow(self._hamlib_rows_box)
        fl_hamlib.addRow(self.btn_hamlib_add_row)
        fl_hamlib.addRow(row_hamlib_status)
        fl_hamlib.addRow(self._protocol_autostart["hamlib"])
        fl_hamlib.addRow(self.chk_hamlib_debug)
        gb_hamlib.setToolTip(
            format_tooltip(
                "WSJT-X: Netzwerk-Rig / Hamlib NET rigctl.\n"
                "• Ein Host für alle Ports (z. B. 127.0.0.1)\n"
                "• Pro Programm einen eigenen Port eintragen; Name nur zur Orientierung.\n"
                "• Funkgerät verbinden, dann „Start“."
            )
        )
        vl_protocols.addWidget(gb_hamlib)

        gb_diag = QGroupBox("Diagnose / Logging")
        fl_diag = QFormLayout(gb_diag)
        self.chk_serial_log = QCheckBox(
            "Rig-Befehle loggen (TCP-Zeilen + COM TX/RX mit Zeitstempel im Fenster unten)"
        )
        self.chk_serial_log.setToolTip(
            format_tooltip(
                "Wenn aktiv: dieselben Zeilen wie im Diagnosefenster unten zusätzlich in rotortcpbridge.log "
                "(Benutzer-AppData). Flrig-/Hamlib-TCP, COM-Worker und Rohbytes (hex + ASCII). "
                "Warnungen/Fehler werden immer geloggt. Bei sehr hoher SETFREQ-Rate ggf. deaktivieren."
            )
        )
        fl_diag.addRow(self.chk_serial_log)
        self.txt_diag = QTextEdit()
        self.txt_diag.setReadOnly(True)
        fl_diag.addRow(self.txt_diag)

        main.addWidget(gb_general)
        main.addWidget(gb_status)
        main.addWidget(gb_serial)
        main.addWidget(gb_protocols)
        main.addWidget(gb_diag, 1)

        self.btn_refresh_com.clicked.connect(self._refresh_com_ports)
        self.cb_com.currentIndexChanged.connect(self._on_com_selection_changed)
        self.btn_connect.clicked.connect(self._on_connect)
        self.btn_disconnect.clicked.connect(self._on_disconnect)
        self.btn_test.clicked.connect(self._on_test)
        self.cb_rig_brand.currentIndexChanged.connect(self._on_brand_changed)
        self.cb_rig_model.currentIndexChanged.connect(self._update_rig_info_label)
        self.chk_serial_log.stateChanged.connect(self.apply_to_manager)
        self.ed_cat_drain.editingFinished.connect(self.apply_to_manager)
        self.ed_setfreq_gap.editingFinished.connect(self.apply_to_manager)
        self.chk_auto_connect.stateChanged.connect(self.apply_to_manager)
        self.chk_auto_reconnect.stateChanged.connect(self.apply_to_manager)
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

    def _hamlib_add_row(self, port_text: str, name_text: str) -> None:
        row_w = QWidget()
        hl = QHBoxLayout(row_w)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(8)
        ed_port = QLineEdit()
        ed_port.setPlaceholderText("Port")
        ed_port.setFixedWidth(px_to_dip(self, 56))
        ed_port.setText(port_text)
        ed_name = QLineEdit()
        ed_name.setPlaceholderText("z. B. WSJT-X (freiwillig)")
        ed_name.setFixedWidth(px_to_dip(self, 150))
        ed_name.setText(name_text)
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
        btn_del = QPushButton("Löschen")
        btn_del.setToolTip(format_tooltip("Diese Port-Zeile entfernen"))
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

    def _load_from_config(self) -> None:
        self._rig_loading_cfg = True
        try:
            self._load_from_config_inner()
        finally:
            self._rig_loading_cfg = False
        self.refresh_status()

    def _load_from_config_inner(self) -> None:
        cfg = self.cfg.get("rig_bridge", {})
        self.chk_enabled.setChecked(bool(cfg.get("enabled", False)))
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
        self._protocol_enabled["flrig"].setChecked(bool(cfg.get("flrig", {}).get("enabled", False)))
        self._protocol_host["flrig"].setText(str(cfg.get("flrig", {}).get("host", "127.0.0.1")))
        self._protocol_port["flrig"].setText(str(int(cfg.get("flrig", {}).get("port", 12345))))
        self._protocol_autostart["flrig"].setChecked(bool(cfg.get("flrig", {}).get("autostart", False)))
        self._protocol_enabled["hamlib"].setChecked(bool(cfg.get("hamlib", {}).get("enabled", False)))
        self._hamlib_host.setText(str(cfg.get("hamlib", {}).get("host", "127.0.0.1")))
        self._hamlib_clear_rows()
        hlib = cfg.get("hamlib", {}) or {}
        listeners = hlib.get("listeners")
        if not listeners and "port" in hlib:
            try:
                p = int(hlib.get("port", 4532))
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
        self._protocol_autostart["hamlib"].setChecked(bool(cfg.get("hamlib", {}).get("autostart", False)))
        self.chk_hamlib_debug.setChecked(bool(cfg.get("hamlib", {}).get("debug_traffic", False)))
        self.chk_serial_log.blockSignals(True)
        self.chk_serial_log.setChecked(bool(cfg.get("log_serial_traffic", True)))
        self.chk_serial_log.blockSignals(False)
        self.chk_auto_connect.blockSignals(True)
        self.chk_auto_reconnect.blockSignals(True)
        self.chk_auto_connect.setChecked(bool(cfg.get("auto_connect", False)))
        self.chk_auto_reconnect.setChecked(bool(cfg.get("auto_reconnect", True)))
        self.chk_auto_connect.blockSignals(False)
        self.chk_auto_reconnect.blockSignals(False)
        self._rig_combo_apply_max_width()

    def to_config(self) -> dict:
        rig_id = int(self.cb_rig_model.currentData() or 0)
        rig_brand = self.cb_rig_brand.currentText().strip()
        rig_model = self.cb_rig_model.currentText().strip()
        selected_rig = f"{rig_brand} {rig_model}".strip()
        return {
            "enabled": bool(self.chk_enabled.isChecked()),
            "selected_rig": selected_rig,
            "rig_brand": rig_brand,
            "rig_model": rig_model,
            "hamlib_rig_id": rig_id,
            "com_port": normalize_com_port(self.cb_com.currentText().strip()),
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
            "log_serial_traffic": bool(self.chk_serial_log.isChecked()),
            "flrig": {
                "enabled": bool(self._protocol_enabled["flrig"].isChecked()),
                "host": self._protocol_host["flrig"].text().strip() or "127.0.0.1",
                "port": self._int_from_field(self._protocol_port["flrig"].text(), 12345, 1, 65535),
                "autostart": bool(self._protocol_autostart["flrig"].isChecked()),
            },
            "hamlib": {
                "enabled": bool(self._protocol_enabled["hamlib"].isChecked()),
                "host": self._hamlib_host.text().strip() or "127.0.0.1",
                "listeners": self._hamlib_listeners_to_config(),
                "autostart": bool(self._protocol_autostart["hamlib"].isChecked()),
                "debug_traffic": bool(self.chk_hamlib_debug.isChecked()),
            },
        }

    def apply_to_manager(self) -> None:
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
                new_port = normalize_com_port(self.cb_com.currentText().strip()).upper()
                active = normalize_com_port(str(st.com_port or "")).upper()
                if new_port != active:
                    self.manager.disconnect_radio()
        except Exception:
            pass
        self.apply_to_manager()

    def _refresh_com_ports(self, preferred_port: str | None = None) -> None:
        """Portliste neu füllen. ``preferred_port``: gespeicherter COM aus der Konfiguration
        (wird normalisiert und per exakter Auswahl gesetzt; fehlt der Port im System, als Eintrag ergänzen).
        Ohne Argument: vorher gewählter Eintrag bleibt erhalten, sofern noch vorhanden."""
        current = self.cb_com.currentText().strip()
        self.cb_com.blockSignals(True)
        self.cb_com.clear()
        ports = list_serial_ports()
        for p in ports:
            self.cb_com.addItem(p)
        want_raw = preferred_port if preferred_port is not None else current
        want = normalize_com_port(want_raw) if (want_raw or "").strip() else ""
        if want:
            want_u = want.upper()
            idx = -1
            for i in range(self.cb_com.count()):
                if self.cb_com.itemText(i).strip().upper() == want_u:
                    idx = i
                    break
            if idx >= 0:
                self.cb_com.setCurrentIndex(idx)
            else:
                self.cb_com.addItem(want)
                self.cb_com.setCurrentIndex(self.cb_com.count() - 1)
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
        self.txt_diag.setPlainText(self.manager.diagnostics_text())
        # Nach setPlainText ist die Ansicht oben; Scroll erst nach Layout-Berechnung ans Ende
        QTimer.singleShot(0, self._scroll_diag_to_bottom)

    def _scroll_diag_to_bottom(self) -> None:
        bar = self.txt_diag.verticalScrollBar()
        bar.setValue(bar.maximum())
        cur = self.txt_diag.textCursor()
        cur.movePosition(QTextCursor.MoveOperation.End)
        self.txt_diag.setTextCursor(cur)
