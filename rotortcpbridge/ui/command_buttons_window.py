"""Fenster 'Befehle' für RotorTcpBridge."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QTimer, Signal, Slot
from PySide6.QtGui import QFontDatabase, QShowEvent
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..rs485_protocol import build
from ..command_catalog import command_specs, format_cmd_tooltip
from ..app_icon import get_app_icon
from ..i18n import t
from ..rotor_backup import (
    backupable_pairs,
    get_params_for_get,
    save_rotor_config_xml,
    load_rotor_config_xml,
    extract_gui_config_for_backup,
    apply_gui_config_from_backup,
)
from ..backup import backups_dir


_CMD_RE = re.compile(r"^[A-Z0-9_]+$")
_SET_TO_GET_SPECIAL_MAP = {
    "SETSWAPTEMP": "GETSWAPTMP",
    "SETHOMERETURN": "GETHOMRETURN",
    "SETISFILTERLEN": "GETFILTERLEN",
    "SETISGRACEMS": "GETGRACEMS",
    "SETTEMPA": "GETTEMPAW",
    "SETTEMPM": "GETTEMPMW",
}

# Block 1: (label_key, SET-CMD, GET-CMD) – GETTEMPMW für SETTEMPM
_BLOCK1_DEFS = [
    ("cmd.label_rotor_id", "SETID", "GETID"),
    ("cmd.label_wind_sensor", "SETWINDENABLE", "GETWINDENABLE"),
    ("cmd.label_wind_offset", "SETWINDDIROF", "GETWINDDIROF"),
    ("cmd.label_max_motor_temp", "SETTEMPM", "GETTEMPMW"),
    ("cmd.label_swap_temp", "SETSWAPTEMP", "GETSWAPTMP"),
    ("cmd.label_ramp", "SETRAMP", "GETRAMP"),
    ("cmd.label_pos_timeout", "SETPOSTIMEOUT", "GETPOSTIMEOUT"),
    ("cmd.label_homing_timeout", "SETHOMETIMEOUT", "GETHOMETIMEOUT"),
]

# Block 2
_BLOCK2_DEFS = [
    ("cmd.label_home_return", "SETHOMERETURN", "GETHOMRETURN"),
    ("cmd.label_homing_pwm", "SETHOMEPWM", "GETHOMEPWM"),
    ("cmd.label_homing_seek_pwm", "SETHOMESEEKPPWM", "GETHOMESEEKPPWM"),
    ("cmd.label_min_pwm", "SETMINPWM", "GETMINPWM"),
    ("cmd.label_max_angle", "SETMAXDG", "GETMAXDG"),
    ("cmd.label_current_warn", "SETIWARN", "GETIWARN"),
    ("cmd.label_current_max", "SETIMAX", "GETIMAX"),
]


def _BLOCK1():
    return [(t(key), sc, gc) for key, sc, gc in _BLOCK1_DEFS]


def _BLOCK2():
    return [(t(key), sc, gc) for key, sc, gc in _BLOCK2_DEFS]

# Tupel: (min, max, unit, is_current_mA, is_timeout_s)
# is_current_mA : Eingabe mA, Senden/Empfangen mV  (0–1300 mV = 0–10 A)
# is_timeout_s  : Eingabe Sekunden, Senden/Empfangen ms
_PARAM_SPEC = {
    "SETID":           (1,   254,   "ID",  False, False),
    "SETWINDENABLE":   (0,   1,     "0/1", False, False),
    "SETWINDDIROF":    (0,   360,   "°",   False, False),
    "SETTEMPM":        (0,   90,    "°C",  False, False),
    "SETSWAPTEMP":     (0,   1,     "0/1", False, False),
    "SETRAMP":         (0,   60,    "°",   False, False),
    "SETPOSTIMEOUT":   (1,   600,   "s",   False, True),
    "SETHOMETIMEOUT":  (1,   600,   "s",   False, True),
    "SETHOMERETURN":   (0,   1,     "0/1", False, False),
    "SETHOMEPWM":      (0,   100,   "%",   False, False),
    "SETHOMESEEKPPWM": (0,   100,   "%",   False, False),
    "SETMINPWM":       (15,  100,   "%",   False, False),
    "SETMAXDG":        (0,   360,   "°",   False, False),
    "SETIWARN":        (100, 10000, "mA",  True,  False),
    "SETIMAX":         (100, 10000, "mA",  True,  False),
}

_MV_PER_A = 130.0  # 1300 mV = 10 A
_PARAM_EDIT_WIDTH = 90
_PARAM_BTN_WIDTH = 55
_PARAM_UNIT_WIDTH = 28
_PARAM_EDIT_LEFT_MARGIN = 5
_PARAM_LABEL_MIN_WIDTH = 170


class CommandButtonsWindow(QDialog):
    """Dialog mit Rotor-Parametern (Lesen/Schreiben) sowie Backup/Restore."""
    sig_get_result = Signal(str, str, str)
    sig_send_result = Signal(str, str, str, str)
    sig_backup_step_done = Signal(bool, str, str, int, str, str)
    sig_restore_step_done = Signal(bool, str, int, str, str)

    def __init__(self, cfg: dict, controller, save_cfg_cb, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.ctrl = controller
        self.save_cfg_cb = save_cfg_cb

        self.setWindowTitle(t("cmd.title"))
        self.setWindowFlag(Qt.WindowType.WindowMinimizeButtonHint, True)
        self.setWindowIcon(get_app_icon())
        self.setFixedSize(730, 610)

        self._backup_state: Optional[dict] = None
        self._restore_state: Optional[dict] = None
        self._BACKUP_DELAY_MS = 500
        self._RESTORE_DELAY_MS = 500
        self._block_auto_send = False
        self._auto_query_inflight = False
        self._auto_query_pending_cmd: str = ""
        self._auto_query_timer = QTimer(self)
        self._auto_query_timer.setSingleShot(True)
        self._auto_query_timer.timeout.connect(self._run_auto_query)

        all_cmd_specs = command_specs()
        self._all_spec_by_name = {c.name: c for c in all_cmd_specs}
        set_cmds = {c.name for c in all_cmd_specs if c.name.startswith("SET")}
        get_cmds_hidden = set()
        for set_cmd in set_cmds:
            candidate = f"GET{set_cmd[3:]}"
            if candidate in self._all_spec_by_name:
                get_cmds_hidden.add(candidate)
            mapped = _SET_TO_GET_SPECIAL_MAP.get(set_cmd)
            if mapped and mapped in self._all_spec_by_name:
                get_cmds_hidden.add(mapped)
        self._cmd_specs = [c for c in all_cmd_specs if not (c.name.startswith("GET") and c.name in get_cmds_hidden)]
        self._spec_by_name = {c.name: c for c in self._cmd_specs}
        self._param_rows: dict[str, tuple[QLineEdit, QPushButton]] = {}
        self._send_set_inflight: set[tuple[int, str]] = set()

        root = QVBoxLayout(self)

        # Befehl zusammenstellen (oben)
        gb = QGroupBox(t("cmd.group_build"))
        gb.setFixedHeight(255)
        root.addWidget(gb)
        gl = QGridLayout(gb)

        self.cb_dst = self._make_dst_combo()
        self.cb_dst.currentIndexChanged.connect(self._on_dst_changed)
        self.cb_dst.currentIndexChanged.connect(self._update_frame)
        self.cb_cmd = QComboBox()
        self.cb_cmd.setEditable(True)
        self.cb_cmd.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        for spec in self._cmd_specs:
            self.cb_cmd.addItem(spec.name)
            idx = self.cb_cmd.count() - 1
            self.cb_cmd.setItemData(idx, format_cmd_tooltip(spec), Qt.ItemDataRole.ToolTipRole)
        last_cmd = str(self.cfg.get("ui", {}).get("last_cmd", "SETPOSDG") or "SETPOSDG").strip().upper()
        if last_cmd and last_cmd in self._spec_by_name:
            self.cb_cmd.setCurrentText(last_cmd)
        elif self.cb_cmd.count() > 0:
            self.cb_cmd.setCurrentIndex(0)

        self.lbl_cmd_info = QLabel("")
        self.lbl_cmd_info.setWordWrap(True)
        self.lbl_cmd_info.setMinimumHeight(70)
        self.lbl_cmd_info.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.lbl_cmd_info.setStyleSheet(
            "color: #111; background: #f2f2f2; border: 1px solid #c8c8c8; border-radius: 4px; padding: 6px;"
        )

        self.ed_params = QLineEdit("0")
        self.ed_params.setPlaceholderText(t("cmd.params_placeholder"))

        self.ed_frame = QLineEdit()
        self.ed_frame.setReadOnly(True)
        self.ed_frame.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))

        btn_box = QVBoxLayout()
        self.btn_send = QPushButton(t("cmd.btn_send"))
        self.btn_send.setAutoDefault(False)
        self.btn_send.setDefault(False)
        self.btn_backup = QPushButton(t("cmd.btn_backup"))
        self.btn_backup.setAutoDefault(False)
        self.btn_restore = QPushButton(t("cmd.btn_restore"))
        self.btn_restore.setAutoDefault(False)
        btn_box.addWidget(self.btn_send)
        btn_box.addWidget(self.btn_backup)
        btn_box.addWidget(self.btn_restore)

        gl.addWidget(QLabel(t("cmd.dst_label")), 0, 0)
        gl.addWidget(self.cb_dst, 0, 1)
        gl.addWidget(QLabel(t("cmd.cmd_label")), 0, 2)
        gl.addWidget(self.cb_cmd, 0, 3)
        gl.addWidget(QLabel(t("cmd.params_label")), 1, 0)
        gl.addWidget(self.ed_params, 1, 1, 1, 3)
        gl.addWidget(self.lbl_cmd_info, 3, 0, 1, 5)
        gl.addWidget(QLabel(t("cmd.frame_label")), 2, 0)
        gl.addWidget(self.ed_frame, 2, 1, 1, 3)
        gl.addLayout(btn_box, 0, 4, 3, 1)

        self.cb_cmd.currentIndexChanged.connect(self._update_frame)
        self.cb_cmd.currentIndexChanged.connect(self._on_cmd_index_changed)
        self.cb_cmd.editTextChanged.connect(self._update_frame)
        self.ed_params.textChanged.connect(self._update_frame)
        self.btn_send.clicked.connect(self._send_current)
        self.ed_params.returnPressed.connect(self._send_current)

        # Zwei Blöcke nebeneinander – Grid: Label | Eingabe | Einheit | Button
        blocks_h = QHBoxLayout()
        blocks_h.setSpacing(16)

        gb1 = QGroupBox(t("cmd.group_params"))
        gl1 = QGridLayout(gb1)
        gl1.setHorizontalSpacing(4)
        gl1.setVerticalSpacing(4)
        gl1.setColumnMinimumWidth(0, _PARAM_LABEL_MIN_WIDTH)
        gl1.setColumnMinimumWidth(1, _PARAM_EDIT_LEFT_MARGIN + _PARAM_EDIT_WIDTH)
        for row, (label, set_cmd, get_cmd) in enumerate(_BLOCK1()):
            ed, btn = self._add_param_row_to_grid(gl1, row, label, set_cmd, get_cmd)
            self._param_rows[get_cmd] = (ed, btn)
        blocks_h.addWidget(gb1, 1)

        gb2 = QGroupBox(t("cmd.group_homing"))
        gb2_layout = QVBoxLayout(gb2)
        gb2_layout.setContentsMargins(4, 4, 4, 4)
        gb2_layout.setSpacing(4)

        gl2 = QGridLayout()
        gl2.setHorizontalSpacing(4)
        gl2.setVerticalSpacing(4)
        gl2.setColumnMinimumWidth(0, _PARAM_LABEL_MIN_WIDTH)
        gl2.setColumnMinimumWidth(1, _PARAM_EDIT_LEFT_MARGIN + _PARAM_EDIT_WIDTH)
        for row, (label, set_cmd, get_cmd) in enumerate(_BLOCK2()):
            ed, btn = self._add_param_row_to_grid(gl2, row, label, set_cmd, get_cmd)
            self._param_rows[get_cmd] = (ed, btn)
        gb2_layout.addLayout(gl2)

        # Strom-Kalibrierung
        cal_sep = QLabel(t("cmd.cal_label") + ":")
        cal_sep.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.btn_cal_start = QPushButton(t("cmd.btn_start_cal"))
        self.btn_cal_start.setAutoDefault(False)
        self.btn_cal_start.setDefault(False)
        _spec_setcal = self._all_spec_by_name.get("SETCAL")
        if _spec_setcal:
            self.btn_cal_start.setToolTip(format_cmd_tooltip(_spec_setcal))
        self.btn_cal_reset = QPushButton(t("cmd.btn_reset_cal"))
        self.btn_cal_reset.setAutoDefault(False)
        self.btn_cal_reset.setDefault(False)
        _spec_clrstat = self._all_spec_by_name.get("CLRSTAT")
        if _spec_clrstat:
            self.btn_cal_reset.setToolTip(format_cmd_tooltip(_spec_clrstat))
        cal_row = QHBoxLayout()
        cal_row.setContentsMargins(0, 0, 0, 0)
        cal_row.addWidget(cal_sep, 1)
        cal_row.addWidget(self.btn_cal_start)
        cal_row.addWidget(self.btn_cal_reset)
        gb2_layout.addLayout(cal_row)

        self.btn_cal_start.clicked.connect(self._on_cal_start_clicked)
        self.btn_cal_reset.clicked.connect(self._on_cal_reset_clicked)

        blocks_h.addWidget(gb2, 1)

        root.addLayout(blocks_h, 1)

        self.btn_backup.clicked.connect(self._on_backup_clicked)
        self.btn_restore.clicked.connect(self._on_restore_clicked)

        self.lbl_hint = QLabel(t("cmd.hint_default"))
        self.lbl_hint.setStyleSheet("color: gray; font-style: italic;")
        root.addWidget(self.lbl_hint)

        self.sig_get_result.connect(self._apply_get_result)
        self.sig_send_result.connect(self._apply_send_result)
        self.sig_backup_step_done.connect(self._on_backup_step_done)
        self.sig_restore_step_done.connect(self._on_restore_step_done)

    def _make_dst_combo(self) -> QComboBox:
        cb = QComboBox()
        rb = self.cfg.get("rotor_bus", {})
        ids = []
        for key in ("slave_az", "slave_el"):
            try:
                v = int(rb.get(key))
                if v not in ids:
                    ids.append(v)
            except Exception:
                pass
        if not ids:
            ids = [0]
        for v in ids:
            cb.addItem(f"ID {v}", v)
        return cb

    def _on_dst_changed(self) -> None:
        self._read_all_params()

    def _current_cmd(self) -> str:
        return str(self.cb_cmd.currentText() or "").strip().upper()

    def _current_params(self) -> str:
        return str(self.ed_params.text() or "").strip()

    def _get_query_cmd_for_selection(self, selected_cmd: str) -> str:
        cmd = str(selected_cmd or "").strip().upper()
        if not cmd:
            return ""
        if cmd.startswith("GET"):
            return cmd if cmd in self._all_spec_by_name else ""
        if not cmd.startswith("SET"):
            return ""
        candidate = f"GET{cmd[3:]}"
        if candidate in self._all_spec_by_name:
            return candidate
        mapped = _SET_TO_GET_SPECIAL_MAP.get(cmd)
        return mapped if mapped and mapped in self._all_spec_by_name else ""

    def _on_cmd_index_changed(self) -> None:
        if self._block_auto_send:
            return
        cmd_sel = self._current_cmd()
        spec = self._spec_by_name.get(str(cmd_sel or "").strip().upper())
        query_cmd = self._get_query_cmd_for_selection(cmd_sel)
        if query_cmd:
            self._auto_query_pending_cmd = query_cmd
            self._auto_query_timer.start(180)
        elif spec is not None and spec.params_literal is not None:
            self._block_auto_send = True
            try:
                self.ed_params.setText(str(spec.params_literal).strip())
            finally:
                self._block_auto_send = False
            self._update_frame()

    def _run_auto_query(self) -> None:
        cmd = str(self._auto_query_pending_cmd or "").strip().upper()
        if not cmd:
            return
        spec = self._all_spec_by_name.get(cmd)
        if spec is None:
            return
        if self._auto_query_inflight:
            self._auto_query_timer.start(120)
            return
        params_to_send = str(spec.params_literal) if spec.kind == "none" and spec.params_literal is not None else "0"
        dst = self._current_dst()
        self._block_auto_send = True
        try:
            self.ed_params.setText("…")
        finally:
            self._block_auto_send = False
        self._auto_query_inflight = True

        def done(tel, err):
            self._auto_query_inflight = False
            if err or tel is None:
                self.sig_get_result.emit(cmd, "", str(err or "keine Antwort"))
            else:
                self.sig_get_result.emit(cmd, str(tel.params).strip(), "")

        try:
            self.ctrl.send_ui_command(
                dst, cmd, params_to_send,
                expect_prefix=f"ACK_{cmd}",
                priority=0,
                on_done=done,
            )
        except Exception as e:
            self._auto_query_inflight = False
            self.lbl_hint.setText(f"{cmd}: Senden fehlgeschlagen ({e})")

    def _update_frame(self) -> None:
        cmd = self._current_cmd()
        params = self._current_params()
        dst = self._current_dst()
        src = self._master_id()
        spec = self._all_spec_by_name.get(cmd)
        if spec is not None:
            info = format_cmd_tooltip(spec)
            if cmd.startswith("GET") and params and params != "…":
                lit = str(spec.params_literal).strip() if spec.kind == "none" and spec.params_literal is not None else None
                if lit is None or params != lit:
                    info = f"{info}\n{t('catalog.response')}: {params}"
            self.lbl_cmd_info.setText(info)
        else:
            self.lbl_cmd_info.setText("")
        try:
            self.cfg.setdefault("ui", {})["last_cmd"] = cmd
        except Exception:
            pass
        if cmd and not _CMD_RE.match(cmd):
            self.ed_frame.setText("CMD ungültig (nur A-Z/0-9/_)")
            return
        params_for_preview = params
        if spec is not None and cmd.startswith("GET") and spec.kind == "none" and spec.params_literal is not None:
            params_for_preview = str(spec.params_literal)
        try:
            self.ed_frame.setText(build(src, dst, cmd, params_for_preview))
        except Exception:
            self.ed_frame.setText("–")

    @Slot()
    def _send_current(self) -> None:
        dst = self._current_dst()
        cmd = self._current_cmd()
        params = self._current_params()
        if not cmd:
            QMessageBox.warning(self, "Befehl", "CMD ist leer.")
            return
        if not _CMD_RE.match(cmd):
            QMessageBox.warning(self, "Befehl", "CMD enthält ungültige Zeichen (nur A-Z/0-9/_).")
            return
        spec = self._all_spec_by_name.get(cmd)
        if spec is not None and spec.kind == "none" and spec.params_literal is not None:
            if cmd.startswith("GET") or self._get_query_cmd_for_selection(cmd) == "":
                params = str(spec.params_literal)
        try:
            def done(tel, err):
                if err or tel is None:
                    self.sig_send_result.emit(cmd, "", "", str(err or "keine Antwort"))
                else:
                    self.sig_send_result.emit(
                        cmd, str(getattr(tel, "cmd", "") or ""),
                        str(getattr(tel, "params", "") or ""), ""
                    )
            self.ctrl.send_ui_command(
                dst, cmd, params,
                expect_prefix=f"ACK_{cmd}",
                priority=0,
                on_done=done,
            )
        except Exception as e:
            QMessageBox.warning(self, "Befehl", f"Senden fehlgeschlagen: {e}")

    def _build_timeout_s_tooltip(self, set_cmd: str, min_s: float, max_s: float) -> str:
        """Tooltip für Timeout-Eingabefelder (Eingabe Sekunden, Hardware ms)."""
        key_map = {
            "SETPOSTIMEOUT":  "cmd.tooltip_setpostimeout_s",
            "SETHOMETIMEOUT": "cmd.tooltip_sethometimeout_s",
        }
        help_line  = t(key_map.get(set_cmd, "cmd.tooltip_setpostimeout_s"))
        note_line  = t("cmd.tooltip_timeout_s_note")
        range_line = f"{t('catalog.range')}: {int(min_s)} .. {int(max_s)} s"
        return "\n".join([set_cmd, help_line, note_line, range_line])

    def _build_current_ma_tooltip(self, set_cmd: str, min_ma: float, max_ma: float) -> str:
        """Tooltip für mA-Eingabefelder (SETIWARN / SETIMAX)."""
        key_map = {
            "SETIWARN": "cmd.tooltip_setiwarn_ma",
            "SETIMAX":  "cmd.tooltip_setimax_ma",
        }
        help_line = t(key_map.get(set_cmd, "cmd.tooltip_setiwarn_ma"))
        note_line  = t("cmd.tooltip_current_ma_note")
        range_line = f"{t('catalog.range')}: {int(min_ma)} .. {int(max_ma)} mA"
        return "\n".join([set_cmd, help_line, note_line, range_line])

    def _add_param_row_to_grid(self, grid: QGridLayout, row: int, label: str, set_cmd: str, get_cmd: str):
        """Grid-Spalten: 0=Label (links), 1=Eingabe (100px), 2=Einheit (28px), 3=Button (55px)."""
        param_spec = _PARAM_SPEC.get(set_cmd, (None, None, "", False, False))
        min_v, max_v, unit, is_current_mA, is_timeout_s = param_spec
        lab = QLabel(label + ":")
        lab.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        cmd_spec = self._all_spec_by_name.get(set_cmd) or self._all_spec_by_name.get(get_cmd)
        if is_current_mA and min_v is not None and max_v is not None:
            tooltip = self._build_current_ma_tooltip(set_cmd, min_v, max_v)
        elif is_timeout_s and min_v is not None and max_v is not None:
            tooltip = self._build_timeout_s_tooltip(set_cmd, min_v, max_v)
        else:
            tooltip = format_cmd_tooltip(cmd_spec) if cmd_spec is not None else ""
        if tooltip:
            lab.setToolTip(tooltip)
        ed = QLineEdit()
        ed.setPlaceholderText("–")
        ed.setFixedWidth(_PARAM_EDIT_WIDTH)
        if tooltip:
            ed.setToolTip(tooltip)
        ed_wrap = QWidget()
        ed_layout = QHBoxLayout(ed_wrap)
        ed_layout.setContentsMargins(_PARAM_EDIT_LEFT_MARGIN, 0, 0, 0)
        ed_layout.setSpacing(0)
        ed_layout.addWidget(ed)
        btn = QPushButton("Set")
        btn.setFixedWidth(_PARAM_BTN_WIDTH)
        btn.setAutoDefault(False)
        btn.setDefault(False)
        grid.addWidget(lab, row, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        grid.addWidget(ed_wrap, row, 1, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        unit_lbl = QLabel(unit if unit else "")
        unit_lbl.setFixedWidth(_PARAM_UNIT_WIDTH)
        unit_lbl.setStyleSheet("color: gray;")
        unit_lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        grid.addWidget(unit_lbl, row, 2, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        grid.addWidget(btn, row, 3, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        def _on_set():
            self._send_set(set_cmd, ed.text().strip(), ed)

        btn.clicked.connect(_on_set)
        ed.returnPressed.connect(_on_set)
        return ed, btn

    def _current_dst(self) -> int:
        v = self.cb_dst.currentData()
        try:
            return int(v)
        except Exception:
            return 0

    def _master_id(self) -> int:
        try:
            return int(self.cfg.get("rotor_bus", {}).get("master_id", 0))
        except Exception:
            return 0

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self._refresh_dst_dropdown()
        self._update_frame()
        self._read_all_params()
        try:
            qcmd = self._get_query_cmd_for_selection(self._current_cmd())
            if qcmd:
                self._auto_query_pending_cmd = qcmd
                self._auto_query_timer.start(350)
        except Exception:
            pass

    def _refresh_dst_dropdown(self) -> None:
        self.cb_dst.blockSignals(True)
        self.cb_dst.clear()
        rb = self.cfg.get("rotor_bus", {})
        ids = []
        for key in ("slave_az", "slave_el"):
            try:
                v = int(rb.get(key))
                if v not in ids:
                    ids.append(v)
            except Exception:
                pass
        if not ids:
            ids = [0]
        for v in ids:
            self.cb_dst.addItem(f"ID {v}", v)
        self.cb_dst.blockSignals(False)

    def _read_all_params(self) -> None:
        """GET-Befehle senden, Ergebnisse in Eingabefelder schreiben."""
        dst = self._current_dst()
        for label, set_cmd, get_cmd in _BLOCK1() + _BLOCK2():
            ed, btn = self._param_rows.get(get_cmd, (None, None))
            if ed is None:
                continue
            spec = self._all_spec_by_name.get(get_cmd)
            params = get_params_for_get(spec) if spec else "0"

            def done(tel, err, _get_cmd=get_cmd, _ed=ed):
                if err or tel is None:
                    self.sig_get_result.emit(_get_cmd, "", str(err or "keine Antwort"))
                else:
                    val = str(getattr(tel, "params", "") or "").strip()
                    self.sig_get_result.emit(_get_cmd, val, "")

            try:
                self.ctrl.send_ui_command(
                    dst, get_cmd, params,
                    expect_prefix=f"ACK_{get_cmd}",
                    timeout_s=0.8,
                    priority=0,
                    on_done=done,
                )
            except Exception:
                ed.setPlaceholderText(t("cmd.err_placeholder"))

    def _display_value_for_get(self, get_cmd: str, params: str) -> str:
        """Konvertiert Hardware-Werte für Anzeige (mV→mA bei Strom, ms→s bei Timeout)."""
        set_cmd = None
        for _, sc, gc in _BLOCK1() + _BLOCK2():
            if gc == get_cmd:
                set_cmd = sc
                break
        if set_cmd in ("SETIWARN", "SETIMAX"):
            try:
                mv = int(float(str(params).strip()))
                ma = round(mv * 10000.0 / 1300.0)
                return str(ma)
            except (ValueError, TypeError):
                pass
        if set_cmd in ("SETPOSTIMEOUT", "SETHOMETIMEOUT"):
            try:
                ms = int(float(str(params).strip()))
                return str(ms // 1000)
            except (ValueError, TypeError):
                pass
        return params.strip()

    def _cmd_matches_result(self, cmd: str) -> bool:
        """True wenn das aktuell gewählte Kommando zu diesem GET-Ergebnis gehört.

        Deckt beide Fälle ab:
        - GET direkt ausgewählt (cmd == current)
        - SET ausgewählt, dessen korrespondierendes GET cmd ist (auto-query)
        """
        current = self._current_cmd()
        if current == cmd:
            return True
        return self._get_query_cmd_for_selection(current) == cmd

    @Slot(str, str, str)
    def _apply_get_result(self, cmd: str, params: str, err: str) -> None:
        if err:
            if cmd in self._param_rows:
                ed, _ = self._param_rows[cmd]
                ed.setText("")
                ed.setPlaceholderText("–")
            if self._cmd_matches_result(cmd):
                self._block_auto_send = True
                try:
                    self.ed_params.setText("—")
                finally:
                    self._block_auto_send = False
                self.lbl_hint.setText(t("cmd.hint_no_response", cmd=cmd, err=err))
            self._update_frame()
            return
        display_val = self._display_value_for_get(cmd, params)
        self._block_auto_send = True
        try:
            if cmd in self._param_rows:
                ed, _ = self._param_rows[cmd]
                ed.setText(display_val)
                ed.setPlaceholderText("")
            if self._cmd_matches_result(cmd):
                self.ed_params.setText(display_val)
                self.lbl_hint.setText(t("cmd.hint_enter"))
        finally:
            self._block_auto_send = False
        self._update_frame()

    def _validate_and_convert_param(self, cmd: str, raw: str) -> tuple[str | None, str | None]:
        """Prüft den Wert; konvertiert mA→mV (Strom) bzw. s→ms (Timeout).
        Returns (params_to_send, error_msg); bei Ok ist error_msg None."""
        spec = _PARAM_SPEC.get(cmd)
        if not spec:
            return (raw, None)
        min_v, max_v, unit, is_current_mA, is_timeout_s = spec
        try:
            val = int(float(str(raw).strip().replace(",", ".")))
        except (ValueError, TypeError):
            return (None, f"Ungültige Zahl: {raw}")
        if is_current_mA:
            if val < min_v or val > max_v:
                return (None, f"Strom {val} mA außerhalb {min_v}–{max_v} mA")
            mv = round(val * _MV_PER_A / 1000.0)
            return (str(mv), None)
        if is_timeout_s:
            if val < min_v or val > max_v:
                return (None, f"Timeout {val} s außerhalb {min_v}–{max_v} s")
            return (str(val * 1000), None)
        if min_v is not None and val < min_v:
            return (None, f"Wert {val} unter Minimum {min_v}")
        if max_v is not None and val > max_v:
            return (None, f"Wert {val} über Maximum {max_v}")
        return (str(val), None)

    def _cal_active_dsts(self) -> list[int]:
        """Alle aktiven Slave-IDs aus der Config zurückgeben."""
        rb = self.cfg.get("rotor_bus", {})
        dsts: list[int] = []
        if bool(rb.get("enable_az", True)):
            try:
                v = int(rb.get("slave_az", 0))
                if v not in dsts:
                    dsts.append(v)
            except Exception:
                pass
        if bool(rb.get("enable_el", False)):
            try:
                v = int(rb.get("slave_el", 0))
                if v not in dsts:
                    dsts.append(v)
            except Exception:
                pass
        return dsts or [0]

    def _on_cal_start_clicked(self) -> None:
        """SETCAL an alle aktiven Achsen senden."""
        for dst in self._cal_active_dsts():
            try:
                self.ctrl.send_ui_command(dst, "SETCAL", "0", expect_prefix=None, priority=0)
            except Exception:
                pass
        self.lbl_hint.setText(t("cmd.hint_cal_start"))

    def _on_cal_reset_clicked(self) -> None:
        """CLRSTAT an alle aktiven Achsen senden."""
        for dst in self._cal_active_dsts():
            try:
                self.ctrl.send_ui_command(dst, "CLRSTAT", "0", expect_prefix=None, priority=0)
            except Exception:
                pass
        self.lbl_hint.setText(t("cmd.hint_cal_reset"))

    def _send_set(self, cmd: str, params: str, ed: QLineEdit) -> None:
        if not params:
            QMessageBox.warning(self, t("cmd.btn_send"), t("cmd.msgbox_set_empty"))
            return
        converted, err = self._validate_and_convert_param(cmd, params)
        if err or converted is None:
            QMessageBox.warning(self, t("cmd.btn_send"), err or t("cmd.msgbox_set_empty"))
            return
        params = converted
        dst = self._current_dst()
        key = (dst, cmd)
        if key in self._send_set_inflight:
            return
        self._send_set_inflight.add(key)

        def done(tel, err):
            self._send_set_inflight.discard(key)
            if err or tel is None:
                self.sig_send_result.emit(cmd, "", "", str(err or "keine Antwort"))
            else:
                if cmd == "SETWINDENABLE" and hasattr(self.ctrl, "set_wind_enabled_from_value"):
                    val = getattr(tel, "params", "") or params
                    self.ctrl.set_wind_enabled_from_value(val)
                self.sig_send_result.emit(
                    cmd, str(getattr(tel, "cmd", "") or ""),
                    str(getattr(tel, "params", "") or ""), ""
                )

        try:
            self.ctrl.send_ui_command(
                dst, cmd, params,
                expect_prefix=f"ACK_{cmd}",
                timeout_s=1.0,
                priority=0,
                on_done=done,
            )
        except Exception as e:
            self._send_set_inflight.discard(key)
            QMessageBox.warning(self, t("cmd.btn_send"), t("cmd.msgbox_send_failed", err=e))

    @Slot(str, str, str, str)
    def _apply_send_result(self, cmd: str, ack_cmd: str, params: str, err: str) -> None:
        if err:
            self.lbl_hint.setText(t("cmd.hint_no_response", cmd=cmd, err=err))
        else:
            if params:
                self.lbl_hint.setText(t("cmd.hint_sent_params", cmd=cmd, params=params))
            else:
                self.lbl_hint.setText(t("cmd.hint_sent", cmd=cmd))

    def _on_backup_clicked(self) -> None:
        if self._backup_state:
            QMessageBox.information(self, t("cmd.btn_backup"), t("cmd.msgbox_backup_running"))
            return
        rb = self.cfg.get("rotor_bus", {})
        dsts = []
        for key in ("slave_az", "slave_el"):
            try:
                v = int(rb.get(key))
                if v not in dsts:
                    dsts.append(v)
            except Exception:
                pass
        if not dsts:
            dsts = [0]
        path, _ = QFileDialog.getSaveFileName(
            self, t("cmd.backup_save_title"), str(backups_dir()),
            t("cmd.file_filter_xml")
        )
        if not path:
            return
        path = Path(path)
        if path.suffix.lower() != ".xml":
            path = path.with_suffix(".xml")
        pairs = backupable_pairs()
        work = [(dst, sc, gc) for dst in dsts for sc, gc in pairs]
        self._backup_state = {"work": work, "index": 0, "data": [], "path": path}
        self.btn_backup.setEnabled(False)
        self.btn_restore.setEnabled(False)
        self.lbl_hint.setText(t("cmd.hint_backup_start"))
        QTimer.singleShot(100, self._run_backup_step)

    def _run_backup_step(self) -> None:
        if not self._backup_state:
            return
        s = self._backup_state
        work, idx = s["work"], s["index"]
        if idx >= len(work):
            self._finish_backup()
            return
        dst, set_cmd, get_cmd = work[idx]
        spec = self._all_spec_by_name.get(get_cmd)
        params_to_send = get_params_for_get(spec) if spec else "0"

        def done(tel, err):
            ok, params_val, err_s = False, "", ""
            if err or tel is None:
                err_s = str(err or "keine Antwort")
            else:
                cmd_str = str(getattr(tel, "cmd", "") or "")
                if cmd_str.upper().startswith("ACK_"):
                    ok = True
                    params_val = str(getattr(tel, "params", "") or "").strip()
            try:
                self.sig_backup_step_done.emit(ok, params_val, err_s, dst, set_cmd, get_cmd)
            except RuntimeError:
                pass

        try:
            self.ctrl.send_ui_command(
                dst, get_cmd, params_to_send,
                expect_prefix=f"ACK_{get_cmd}",
                timeout_s=1.2,
                priority=0,
                on_done=done,
            )
        except Exception as e:
            self.sig_backup_step_done.emit(False, "", str(e), dst, set_cmd, get_cmd)

    @Slot(bool, str, str, int, str, str)
    def _on_backup_step_done(self, ok: bool, params_val: str, err: str, dst: int, set_cmd: str, get_cmd: str) -> None:
        if not self._backup_state:
            return
        s = self._backup_state
        if ok and params_val is not None:
            s["data"].append({"dst": dst, "cmd": set_cmd, "params": params_val})
        idx = s["index"] + 1
        s["index"] = idx
        total = len(s["work"])
        self.lbl_hint.setText(t("cmd.hint_backup_progress", idx=idx, total=total, cmd=set_cmd))
        if idx >= total:
            QTimer.singleShot(self._BACKUP_DELAY_MS, self._finish_backup)
        else:
            QTimer.singleShot(self._BACKUP_DELAY_MS, self._run_backup_step)

    def _finish_backup(self) -> None:
        if not self._backup_state:
            return
        s = self._backup_state
        path, data = s["path"], s["data"]
        self._backup_state = None
        self.btn_backup.setEnabled(True)
        self.btn_restore.setEnabled(True)
        try:
            gui_cfg = extract_gui_config_for_backup(self.cfg)
            save_rotor_config_xml(path, data, gui_config=gui_cfg)
            self.lbl_hint.setText(t("cmd.hint_backup_done", count=len(data), name=path.name))
        except Exception as e:
            QMessageBox.warning(self, t("cmd.btn_backup"), t("cmd.msgbox_backup_save_error", err=e))

    def _on_restore_clicked(self) -> None:
        if self._restore_state:
            QMessageBox.information(self, t("cmd.btn_restore"), t("cmd.msgbox_restore_running"))
            return
        path, _ = QFileDialog.getOpenFileName(
            self, t("cmd.backup_load_title"), str(backups_dir()),
            t("cmd.file_filter_xml")
        )
        if not path:
            return
        try:
            entries, gui_config = load_rotor_config_xml(Path(path))
        except Exception as e:
            QMessageBox.warning(self, t("cmd.btn_restore"), t("cmd.msgbox_restore_read_error", err=e))
            return
        if not entries and not gui_config:
            QMessageBox.information(self, t("cmd.btn_restore"), t("cmd.msgbox_restore_empty"))
            return
        self._restore_state = {"entries": entries or [], "gui_config": gui_config, "index": 0}
        self.btn_backup.setEnabled(False)
        self.btn_restore.setEnabled(False)
        self.lbl_hint.setText(t("cmd.hint_restore_start"))
        QTimer.singleShot(100, self._run_restore_step)

    def _run_restore_step(self) -> None:
        if not self._restore_state:
            return
        s = self._restore_state
        entries, idx = s["entries"], s["index"]
        if idx >= len(entries):
            self._finish_restore()
            return
        e = entries[idx]
        dst = int(e["dst"])
        cmd = str(e["cmd"])
        params = str(e.get("params", "")).strip()

        def done(tel, err):
            ok = err is None and tel is not None and str(getattr(tel, "cmd", "") or "").startswith("ACK_")
            err_s = "" if ok else (str(err or "keine Antwort") if err else "NAK")
            self.sig_restore_step_done.emit(ok, err_s, dst, cmd, params)

        try:
            self.ctrl.send_ui_command(
                dst, cmd, params,
                expect_prefix=f"ACK_{cmd}",
                timeout_s=1.0,
                priority=0,
                on_done=done,
            )
        except Exception as ex:
            self.sig_restore_step_done.emit(False, str(ex), dst, cmd, params)

    @Slot(bool, str, int, str, str)
    def _on_restore_step_done(self, ok: bool, err: str, dst: int, cmd: str, params: str) -> None:
        if not self._restore_state:
            return
        s = self._restore_state
        idx = s["index"] + 1
        s["index"] = idx
        total = len(s["entries"])
        self.lbl_hint.setText(t("cmd.hint_restore_progress", idx=idx, total=total, cmd=cmd))
        if idx >= total:
            QTimer.singleShot(self._RESTORE_DELAY_MS, self._finish_restore)
        else:
            QTimer.singleShot(self._RESTORE_DELAY_MS, self._run_restore_step)

    def _finish_restore(self) -> None:
        if not self._restore_state:
            return
        s = self._restore_state
        total = len(s["entries"])
        gui_config = s.get("gui_config")
        self._restore_state = None
        self.btn_backup.setEnabled(True)
        self.btn_restore.setEnabled(True)
        if gui_config:
            try:
                apply_gui_config_from_backup(self.cfg, gui_config)
                self.save_cfg_cb(self.cfg)
                self._refresh_dst_dropdown()
                self._read_all_params()
            except Exception as e:
                QMessageBox.warning(self, t("cmd.btn_restore"), t("cmd.msgbox_restore_gui_error", err=e))
        self.lbl_hint.setText(t("cmd.hint_restore_done", total=total))
        QMessageBox.information(self, t("cmd.btn_restore"), t("cmd.msgbox_restore_done", total=total))
