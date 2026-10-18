"""Inklinometer-Konfiguration (SETINC*/GETINC*) für Encoder-Typ 4."""

from __future__ import annotations

from typing import Any, Callable, Optional

from PySide6.QtCore import Qt, QTimer, Signal, Slot
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
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

from ..app_icon import get_app_icon
from ..command_catalog import command_specs, format_cmd_tooltip
from ..i18n import format_tooltip, t
from ..rotor_backup import get_params_for_get


_LABEL_MIN_W = 160
_EDIT_W = 110
_BTN_W = 48
_UNIT_W = 36
_TIMEOUT_S = 1.5

# (label_key, set_cmd, get_cmd, kind)
# kind: "bool" | "mount" | "dec" | "fs1g" | "float"
_ROWS_MOUNT: list[tuple[str, str, str, str]] = [
    ("inc.label_swapxy", "SETINCSWAPXY", "GETINCSWAPXY", "bool"),
    ("inc.label_invref", "SETINCINVREF", "GETINCINVREF", "bool"),
    ("inc.label_invsen", "SETINCINVSEN", "GETINCINVSEN", "bool"),
    ("inc.label_invrot", "SETINCINVROT", "GETINCINVROT", "bool"),
    ("inc.label_mount", "SETINCMOUNT", "GETINCMOUNT", "mount"),
    ("inc.label_lim180", "SETINCLIM180", "GETINCLIM180", "bool"),
    ("inc.label_cal", "SETINCCAL", "GETINCCAL", "float"),
]

_ROWS_SENSOR: list[tuple[str, str, str, str]] = [
    ("inc.label_fs1g", "SETINCFS1G", "GETINCFS1G", "fs1g"),
    ("inc.label_dec", "SETINCDEC", "GETINCDEC", "dec"),
    ("inc.label_filt", "SETINCFILT", "GETINCFILT", "float"),
]

_ROWS_DIAG: list[tuple[str, str, str, str]] = [
    ("inc.label_wlan", "SETINCWLAN", "GETINCWLAN", "bool"),
    ("inc.label_dbg", "SETINCDBG", "GETINCDBG", "bool"),
]


class InclinometerConfigWindow(QDialog):
    """Konfigurationsfenster für Inklinometer-Parameter (ohne Ruck-Analyse)."""

    sig_get_result = Signal(str, str, str)  # get_cmd, params, err
    sig_send_result = Signal(str, str, str)  # set_cmd, params, err

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        ctrl: Any | None = None,
        get_dst: Optional[Callable[[], int]] = None,
    ) -> None:
        super().__init__(parent)
        self.ctrl = ctrl
        self._get_dst = get_dst or (lambda: 0)
        self._spec_by_name = {c.name: c for c in command_specs()}
        self._param_rows: dict[str, tuple[QWidget, QPushButton]] = {}
        self._send_inflight: set[tuple[int, str]] = set()
        self._get_batch_id = 0

        self.setWindowTitle(t("inc.title"))
        self.setWindowFlag(Qt.WindowType.WindowMinimizeButtonHint, True)
        self.setWindowIcon(get_app_icon())
        self.setMinimumWidth(420)

        root = QVBoxLayout(self)
        root.setSpacing(8)

        self.lbl_dst = QLabel("")
        self.lbl_dst.setStyleSheet("color: gray;")
        root.addWidget(self.lbl_dst)

        root.addWidget(self._build_group(t("inc.group_mount"), _ROWS_MOUNT))
        root.addWidget(self._build_group(t("inc.group_sensor"), _ROWS_SENSOR))
        root.addWidget(self._build_group(t("inc.group_diag"), _ROWS_DIAG))

        row_btn = QHBoxLayout()
        self.btn_refresh = QPushButton(t("inc.btn_refresh"))
        self.btn_refresh.setAutoDefault(False)
        self.btn_refresh.setDefault(False)
        self.btn_refresh.clicked.connect(self._read_all)
        row_btn.addWidget(self.btn_refresh)
        row_btn.addStretch(1)
        self.btn_close = QPushButton(t("inc.btn_close"))
        self.btn_close.setAutoDefault(False)
        self.btn_close.setDefault(False)
        self.btn_close.clicked.connect(self.close)
        row_btn.addWidget(self.btn_close)
        root.addLayout(row_btn)

        self.lbl_hint = QLabel(t("inc.hint_default"))
        self.lbl_hint.setStyleSheet("color: gray; font-style: italic;")
        self.lbl_hint.setWordWrap(True)
        root.addWidget(self.lbl_hint)

        self.sig_get_result.connect(self._apply_get_result)
        self.sig_send_result.connect(self._apply_send_result)

    def _build_group(self, title: str, rows: list[tuple[str, str, str, str]]) -> QGroupBox:
        gb = QGroupBox(title)
        grid = QGridLayout(gb)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(4)
        grid.setColumnMinimumWidth(0, _LABEL_MIN_W)
        for i, (label_key, set_cmd, get_cmd, kind) in enumerate(rows):
            ed, btn = self._add_row(grid, i, label_key, set_cmd, get_cmd, kind)
            self._param_rows[get_cmd] = (ed, btn)
        return gb

    def _add_row(
        self,
        grid: QGridLayout,
        row: int,
        label_key: str,
        set_cmd: str,
        get_cmd: str,
        kind: str,
    ) -> tuple[QWidget, QPushButton]:
        lab = QLabel(t(label_key) + ":")
        lab.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        spec = self._spec_by_name.get(set_cmd) or self._spec_by_name.get(get_cmd)
        tip = format_cmd_tooltip(spec) if spec is not None else set_cmd
        if tip:
            lab.setToolTip(format_tooltip(tip))

        unit_txt = ""
        if kind == "bool":
            ed: QWidget = QCheckBox(t("cmd.chk_enabled"))
            ed.setFixedWidth(_EDIT_W)
        elif kind == "mount":
            ed = QComboBox()
            ed.setFixedWidth(_EDIT_W)
            for v in (0, 90, 180, 270):
                ed.addItem(f"{v}°", int(v))
        elif kind == "dec":
            ed = QComboBox()
            ed.setFixedWidth(_EDIT_W)
            for v in (0, 1, 2):
                ed.addItem(str(v), int(v))
        elif kind == "fs1g":
            ed = QComboBox()
            ed.setFixedWidth(_EDIT_W)
            ed.addItem(t("inc.opt_fs_2g"), 0)
            ed.addItem(t("inc.opt_fs_1g"), 1)
        else:
            ed = QLineEdit()
            ed.setPlaceholderText("–")
            ed.setFixedWidth(_EDIT_W)
            if set_cmd == "SETINCCAL":
                unit_txt = "°"
            elif set_cmd == "SETINCFILT":
                unit_txt = "α"

        if tip:
            ed.setToolTip(format_tooltip(tip))

        btn = QPushButton("Set")
        btn.setFixedWidth(_BTN_W)
        btn.setAutoDefault(False)
        btn.setDefault(False)

        grid.addWidget(lab, row, 0)
        grid.addWidget(ed, row, 1)
        unit_lbl = QLabel(unit_txt)
        unit_lbl.setFixedWidth(_UNIT_W)
        unit_lbl.setStyleSheet("color: gray;")
        grid.addWidget(unit_lbl, row, 2)
        grid.addWidget(btn, row, 3)

        def _on_set() -> None:
            self._send_set(set_cmd, self._widget_raw(ed))

        btn.clicked.connect(_on_set)
        if isinstance(ed, QLineEdit):
            ed.returnPressed.connect(_on_set)
        return ed, btn

    @staticmethod
    def _widget_raw(widget: QWidget) -> str:
        if isinstance(widget, QComboBox):
            data = widget.currentData()
            try:
                return str(int(data))
            except (TypeError, ValueError):
                return ""
        if isinstance(widget, QCheckBox):
            return "1" if widget.isChecked() else "0"
        if isinstance(widget, QLineEdit):
            return widget.text().strip()
        return ""

    @staticmethod
    def _widget_set(widget: QWidget, value: str) -> None:
        raw = str(value or "").strip()
        if isinstance(widget, QComboBox):
            try:
                v = int(float(raw.replace(",", ".")))
            except (TypeError, ValueError):
                return
            for i in range(widget.count()):
                try:
                    if int(widget.itemData(i)) == v:
                        widget.blockSignals(True)
                        widget.setCurrentIndex(i)
                        widget.blockSignals(False)
                        return
                except (TypeError, ValueError):
                    continue
            return
        if isinstance(widget, QCheckBox):
            on = False
            try:
                on = int(float(raw.replace(",", "."))) != 0
            except (TypeError, ValueError):
                on = raw.lower() in ("1", "true", "ein", "on", "yes")
            widget.blockSignals(True)
            widget.setChecked(bool(on))
            widget.blockSignals(False)
            return
        if isinstance(widget, QLineEdit):
            try:
                x = float(raw.replace(",", "."))
                s = f"{x:.6f}".rstrip("0").rstrip(".").replace(".", ",")
                widget.setText(s if s else "0")
            except (TypeError, ValueError):
                widget.setText(raw)
            widget.setPlaceholderText("")

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self._update_dst_label()
        QTimer.singleShot(0, self._read_all)

    def _update_dst_label(self) -> None:
        try:
            dst = int(self._get_dst())
        except Exception:
            dst = 0
        self.lbl_dst.setText(t("inc.label_dst", dst=dst))

    def _all_rows(self) -> list[tuple[str, str, str, str]]:
        return _ROWS_MOUNT + _ROWS_SENSOR + _ROWS_DIAG

    def _read_all(self) -> None:
        if self.ctrl is None:
            self.lbl_hint.setText(t("inc.hint_no_ctrl"))
            return
        self._update_dst_label()
        try:
            dst = int(self._get_dst())
        except Exception:
            dst = 0
        self._get_batch_id += 1
        bid = self._get_batch_id
        self.lbl_hint.setText(t("inc.hint_reading"))

        for _lk, _sc, get_cmd, _kind in self._all_rows():
            spec = self._spec_by_name.get(get_cmd)
            params = get_params_for_get(spec)

            def make_done(gc: str = get_cmd):
                def done(tel, err):
                    if bid != self._get_batch_id:
                        return
                    if err or tel is None:
                        self.sig_get_result.emit(gc, "", str(err or "keine Antwort"))
                    else:
                        val = str(getattr(tel, "params", "") or "").strip()
                        self.sig_get_result.emit(gc, val, "")

                return done

            try:
                self.ctrl.send_ui_command(
                    dst,
                    get_cmd,
                    params,
                    expect_prefix=f"ACK_{get_cmd}",
                    timeout_s=_TIMEOUT_S,
                    priority=0,
                    on_done=make_done(),
                )
            except Exception as e:
                self.sig_get_result.emit(get_cmd, "", str(e))

    def _validate_param(self, set_cmd: str, raw: str) -> tuple[Optional[str], Optional[str]]:
        spec = self._spec_by_name.get(set_cmd)
        s = str(raw or "").strip().replace(",", ".")
        if not s:
            return None, t("cmd.msgbox_set_empty")
        if set_cmd == "SETINCMOUNT":
            try:
                v = int(float(s))
            except (TypeError, ValueError):
                return None, t("inc.err_bad_value")
            if v not in (0, 90, 180, 270):
                return None, t("inc.err_mount")
            return str(v), None
        if set_cmd in (
            "SETINCSWAPXY",
            "SETINCINVREF",
            "SETINCINVSEN",
            "SETINCINVROT",
            "SETINCLIM180",
            "SETINCFS1G",
            "SETINCWLAN",
            "SETINCDBG",
        ):
            try:
                v = int(float(s))
            except (TypeError, ValueError):
                return None, t("inc.err_bad_value")
            if v not in (0, 1):
                return None, t("inc.err_bool")
            return str(v), None
        if set_cmd == "SETINCDEC":
            try:
                v = int(float(s))
            except (TypeError, ValueError):
                return None, t("inc.err_bad_value")
            if v not in (0, 1, 2):
                return None, t("inc.err_dec")
            return str(v), None
        if set_cmd in ("SETINCCAL", "SETINCFILT"):
            try:
                x = float(s)
            except (TypeError, ValueError):
                return None, t("inc.err_bad_value")
            if spec is not None and spec.min_value is not None and x < float(spec.min_value):
                return None, t("inc.err_range")
            if spec is not None and spec.max_value is not None and x > float(spec.max_value):
                return None, t("inc.err_range")
            # Firmware akzeptiert Komma oder Punkt; Punkt ist unproblematisch.
            if set_cmd == "SETINCCAL":
                out = f"{x:.2f}".rstrip("0").rstrip(".")
            else:
                out = f"{x:.4f}".rstrip("0").rstrip(".")
            return out if out else "0", None
        return s, None

    def _send_set(self, set_cmd: str, raw: str) -> None:
        if self.ctrl is None:
            QMessageBox.warning(self, t("inc.title"), t("inc.hint_no_ctrl"))
            return
        converted, err = self._validate_param(set_cmd, raw)
        if err or converted is None:
            QMessageBox.warning(self, t("inc.title"), err or t("cmd.msgbox_set_empty"))
            return
        try:
            dst = int(self._get_dst())
        except Exception:
            dst = 0
        key = (dst, set_cmd)
        if key in self._send_inflight:
            return
        self._send_inflight.add(key)

        def done(tel, err2):
            self._send_inflight.discard(key)
            if err2 or tel is None:
                self.sig_send_result.emit(set_cmd, "", str(err2 or "keine Antwort"))
            else:
                self.sig_send_result.emit(
                    set_cmd,
                    str(getattr(tel, "params", "") or converted),
                    "",
                )

        try:
            self.ctrl.send_ui_command(
                dst,
                set_cmd,
                converted,
                expect_prefix=f"ACK_{set_cmd}",
                timeout_s=_TIMEOUT_S,
                priority=0,
                on_done=done,
            )
        except Exception as e:
            self._send_inflight.discard(key)
            QMessageBox.warning(self, t("inc.title"), t("cmd.msgbox_send_failed", err=e))

    @Slot(str, str, str)
    def _apply_get_result(self, get_cmd: str, params: str, err: str) -> None:
        ed, _ = self._param_rows.get(get_cmd, (None, None))
        if err:
            if isinstance(ed, QLineEdit) and not str(ed.text() or "").strip():
                ed.setPlaceholderText("–")
            self.lbl_hint.setText(t("cmd.hint_no_response", cmd=get_cmd, err=err))
            return
        if ed is not None:
            self._widget_set(ed, params)
        self.lbl_hint.setText(t("inc.hint_default"))

    @Slot(str, str, str)
    def _apply_send_result(self, set_cmd: str, params: str, err: str) -> None:
        if err:
            self.lbl_hint.setText(t("cmd.hint_no_response", cmd=set_cmd, err=err))
        elif params:
            self.lbl_hint.setText(t("cmd.hint_sent_params", cmd=set_cmd, params=params))
        else:
            self.lbl_hint.setText(t("cmd.hint_sent", cmd=set_cmd))
