"""Log-Fenster mit Filter und Pause."""
from __future__ import annotations

from PySide6.QtCore import Qt
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
from .ui_utils import px_to_dip


class LogWindow(QDialog):
    """Dialog-Fenster zur Anzeige des Logs (mit Filter, Pause, Auto-Scroll)."""

    def __init__(self, logbuf, parent=None):
        super().__init__(parent)
        self.logbuf = logbuf
        self.setWindowTitle(t("log.title"))
        self.setWindowFlag(Qt.WindowType.WindowMinimizeButtonHint, True)
        self.setWindowIcon(get_app_icon())
        self.setFixedSize(px_to_dip(self, 650), px_to_dip(self, 700))
        self._paused = False
        self._autoscroll = True
        self._max_display_lines = 1000
        self._last_rendered_last_line: str | None = None
        self._last_refresh_ts: float = 0.0
        self._filter_text: str = ""

        root = QVBoxLayout(self)
        self.txt_log = QPlainTextEdit()
        self.txt_log.setReadOnly(True)
        try:
            self.txt_log.document().setMaximumBlockCount(int(self._max_display_lines))
        except Exception:
            pass
        root.addWidget(self.txt_log, 1)

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel(t("log.filter_label")))
        self.ed_filter = QLineEdit()
        self.ed_filter.setPlaceholderText(t("log.filter_placeholder"))
        self.ed_filter.textChanged.connect(self._on_filter_changed)
        filter_row.addWidget(self.ed_filter, 1)
        root.addLayout(filter_row)

        btn_row = QHBoxLayout()
        self.btn_toggle = QPushButton(t("log.btn_pause"))
        self.btn_toggle.clicked.connect(self._toggle_pause)
        btn_row.addWidget(self.btn_toggle, 0)
        self.btn_scroll = QPushButton(t("log.btn_scroll_pause"))
        self.btn_scroll.clicked.connect(self._toggle_scroll)
        btn_row.addWidget(self.btn_scroll, 0)
        btn_row.addStretch(1)
        btn_close = QPushButton(t("log.btn_close"))
        btn_close.clicked.connect(self.close)
        btn_row.addWidget(btn_close, 0)
        root.addLayout(btn_row)

    def refresh(self):
        if self._paused:
            return

        try:
            import time as _time
            now = float(_time.time())
            if (now - float(self._last_refresh_ts or 0.0)) < 0.25:
                return
            self._last_refresh_ts = now
        except Exception:
            pass

        try:
            lines = list(self.logbuf.lines() or [])
        except Exception:
            lines = []
        try:
            maxn = int(self._max_display_lines)
        except Exception:
            maxn = 1000
        if maxn > 0 and len(lines) > maxn:
            lines = lines[-maxn:]

        try:
            sb = self.txt_log.verticalScrollBar()
            old_val = int(sb.value())
        except Exception:
            sb = None
            old_val = 0

        try:
            needle = str(self._filter_text or "").strip().lower()
            if needle:
                lines = [ln for ln in lines if needle in str(ln).lower()]
            self.txt_log.setPlainText("\n".join(lines) if lines else "")
            self._last_rendered_last_line = (lines[-1] if lines else None)
        except Exception:
            pass

        try:
            if sb is not None:
                if self._autoscroll:
                    sb.setValue(sb.maximum())
                else:
                    sb.setValue(min(old_val, sb.maximum()))
        except Exception:
            pass

    def _toggle_pause(self):
        self._paused = not bool(self._paused)
        self.btn_toggle.setText(t("log.btn_resume") if self._paused else t("log.btn_pause"))
        if not self._paused:
            try:
                sb = self.txt_log.verticalScrollBar()
                sb.setValue(sb.maximum())
            except Exception:
                pass
            self.refresh()

    def _toggle_scroll(self):
        self._autoscroll = not bool(self._autoscroll)
        self.btn_scroll.setText(t("log.btn_scroll_resume") if (not self._autoscroll) else t("log.btn_scroll_pause"))
        if self._autoscroll:
            try:
                sb = self.txt_log.verticalScrollBar()
                sb.setValue(sb.maximum())
            except Exception:
                pass

    def _on_filter_changed(self, text: str) -> None:
        self._filter_text = str(text or "")
        self._last_rendered_last_line = None
        self.refresh()
