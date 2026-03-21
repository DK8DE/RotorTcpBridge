"""Fenster: alle Rotor-Warnungen und -Fehler (live), inkl. Warnungen löschen."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCloseEvent, QShowEvent, QHideEvent
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from ..app_icon import get_app_icon
from ..i18n import t
from ..rotor_model import error_info, warning_info
from .ui_utils import px_to_dip


class WarningsErrorsWindow(QDialog):
    """Zeigt aktuelle Warnungen und Fehler für AZ/EL; Button wie Menü „Warnungen löschen“."""

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self._ctrl = controller
        self._timer = QTimer(self)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self._refresh)

        self.setWindowIcon(get_app_icon())
        self.setWindowFlag(Qt.WindowType.WindowMinimizeButtonHint, True)

        root = QVBoxLayout(self)
        try:
            m = px_to_dip(self, 4)
            root.setContentsMargins(m, m, m, m)
            root.setSpacing(px_to_dip(self, 2))
        except Exception:
            pass
        self._lbl_errors = QLabel(t("warn_err.lbl_errors"))
        root.addWidget(self._lbl_errors)
        self._txt_errors = QLineEdit()
        self._txt_errors.setReadOnly(True)
        self._txt_errors.setFrame(True)

        self._lbl_warnings = QLabel(t("warn_err.lbl_warnings"))
        self._txt_warnings = QPlainTextEdit()
        self._txt_warnings.setReadOnly(True)
        try:
            fm = self._txt_warnings.fontMetrics()
            # Höhe für ca. 2 Textzeilen (sehr kompaktes Fenster)
            _lh = max(1, fm.lineSpacing())
            self._txt_warnings.setFixedHeight(int(_lh * 2 + px_to_dip(self, 6)))
        except Exception:
            self._txt_warnings.setFixedHeight(px_to_dip(self, 40))

        root.addWidget(self._txt_errors)
        root.addWidget(self._lbl_warnings)
        root.addWidget(self._txt_warnings)

        btn_row = QHBoxLayout()
        self._btn_clear = QPushButton(t("main.menu_delwarn"))
        self._btn_clear.clicked.connect(self._on_clear_warnings)
        btn_row.addWidget(self._btn_clear, 0)
        btn_row.addStretch(1)
        self._btn_close = QPushButton(t("warn_err.btn_close"))
        self._btn_close.clicked.connect(self.close)
        btn_row.addWidget(self._btn_close, 0)
        root.addLayout(btn_row)

        self._apply_fixed_size()
        self.retranslate_ui()
        self._refresh()

    def _apply_fixed_size(self) -> None:
        # Breite: zwei Drittel der bisherigen 480 dip
        # Höhe: fest 120 dip (nicht „120 weniger“, sondern Zielhöhe 120)
        try:
            w = int(px_to_dip(self, 480) * 2 / 3)
            h = px_to_dip(self, 150)
            self.setFixedSize(w, h)
        except Exception:
            self.setFixedSize(320, 10)

    def retranslate_ui(self) -> None:
        self.setWindowTitle(t("warn_err.title"))
        self._lbl_errors.setText(t("warn_err.lbl_errors"))
        self._lbl_warnings.setText(t("warn_err.lbl_warnings"))
        self._btn_clear.setText(t("main.menu_delwarn"))
        self._btn_close.setText(t("warn_err.btn_close"))
        self._refresh()

    def _on_clear_warnings(self) -> None:
        try:
            self._ctrl.clear_warnings_all()
        except Exception:
            pass
        self._refresh()

    def _format_errors(self) -> str:
        """Eine Zeile: bei fehlerfreien Achsen „Keine aktiven Fehler“, sonst nur betroffene Achsen."""
        c = self._ctrl
        has_az = getattr(c, "enable_az", False)
        has_el = getattr(c, "enable_el", False)
        if not has_az and not has_el:
            return t("warn_err.no_axes")

        codes: list[tuple[str, int]] = []
        if has_az:
            try:
                code = int(getattr(c.az, "error_code", 0) or 0)
            except Exception:
                code = 0
            codes.append(("AZ", code))
        if has_el:
            try:
                code = int(getattr(c.el, "error_code", 0) or 0)
            except Exception:
                code = 0
            codes.append(("EL", code))

        if all(code == 0 for _axis, code in codes):
            return t("warn_err.none_errors")

        parts: list[str] = []
        for axis, code in codes:
            if code != 0:
                name, _txt = error_info(code)
                parts.append(f"{axis}: {name} ({code:04d})")
        return "  ·  ".join(parts)

    def _format_warnings_axis(self, axis_label: str, axis_state) -> str:
        try:
            ws = set(getattr(axis_state, "warnings", set()) or set())
        except Exception:
            ws = set()
        ws.discard(0)
        if not ws:
            return ""
        blocks = []
        for wid in sorted(ws):
            name, meaning, todo = warning_info(wid)
            blocks.append(
                f"{wid}: {name}\n{t('popup.warn_meaning', meaning=meaning)}\n"
                f"{t('popup.warn_tip', todo=todo)}"
            )
        return f"{axis_label}:\n" + "\n\n".join(blocks)

    def _format_warnings(self) -> str:
        c = self._ctrl
        parts: list[str] = []
        if getattr(c, "enable_az", False):
            s = self._format_warnings_axis("AZ", c.az)
            if s:
                parts.append(s)
        if getattr(c, "enable_el", False):
            s = self._format_warnings_axis("EL", c.el)
            if s:
                parts.append(s)
        if not parts:
            return t("warn_err.none_warnings")
        return "\n\n".join(parts)

    def _refresh(self) -> None:
        try:
            self._txt_errors.setText(self._format_errors())
            self._txt_warnings.setPlainText(self._format_warnings())
        except Exception:
            pass

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self._refresh()
        self._timer.start()

    def hideEvent(self, event: QHideEvent) -> None:
        self._timer.stop()
        super().hideEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:
        self._timer.stop()
        super().closeEvent(event)
