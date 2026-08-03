"""Eigenständiger RS485-/Serial-Bus-Monitor (nicht Teil der Haupt-App).

Zeigt Telegramme mit Zeilenbruch nach ``$`` und Zeitstempel in Millisekunden.
Start: ``python run_bus_monitor.py`` oder direkt diese Datei.
"""

from __future__ import annotations

import sys
import time
from datetime import datetime
from typing import Optional

from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

try:
    import serial
    from serial.tools import list_ports
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "pyserial fehlt. Bitte im Projekt-venv installieren: pip install pyserial"
    ) from exc


BAUDRATES = (
    "9600",
    "19200",
    "38400",
    "57600",
    "115200",
    "230400",
    "460800",
    "921600",
)
MAX_LINES = 5000
DEFAULT_BAUD = "115200"


def _now_ms() -> str:
    """Wanduhr mit Millisekunden, z. B. 18:32:01.123."""
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def list_serial_ports() -> list[tuple[str, str]]:
    """[(device, label), ...] sortiert."""
    rows: list[tuple[str, str]] = []
    try:
        for p in list_ports.comports():
            device = str(p.device or "").strip()
            if not device:
                continue
            desc = str(p.description or "").strip()
            label = f"{device} — {desc}" if desc else device
            rows.append((device, label))
    except Exception:
        pass
    rows.sort(key=lambda x: x[0].lower())
    return rows


class SerialReader(QObject):
    """Liest den Port in einem Worker-Thread und splittet an ``$``."""

    line_ready = Signal(str)  # fertige Anzeigezeile inkl. Zeitstempel
    status = Signal(str)
    error = Signal(str)
    finished = Signal()

    def __init__(self, port: str, baudrate: int) -> None:
        super().__init__()
        self._port = str(port)
        self._baudrate = int(baudrate)
        self._stop = False
        self._ser: Optional[serial.Serial] = None

    def stop(self) -> None:
        self._stop = True
        try:
            if self._ser is not None and self._ser.is_open:
                self._ser.close()
        except Exception:
            pass

    @Slot()
    def run(self) -> None:
        buf = bytearray()
        try:
            self._ser = serial.Serial(
                port=self._port,
                baudrate=self._baudrate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=0.05,
                write_timeout=0.2,
            )
            # Reines Mithören — nichts senden
            try:
                self._ser.reset_input_buffer()
            except Exception:
                pass
            self.status.emit(f"Verbunden: {self._port} @ {self._baudrate}")
        except Exception as e:
            self.error.emit(f"Port öffnen fehlgeschlagen: {e}")
            self.finished.emit()
            return

        try:
            while not self._stop:
                try:
                    n = int(getattr(self._ser, "in_waiting", 0) or 0)
                    chunk = self._ser.read(n if n > 0 else 1)
                except Exception as e:
                    if self._stop:
                        break
                    self.error.emit(f"Lesefehler: {e}")
                    break
                if not chunk:
                    continue
                buf.extend(chunk)
                while True:
                    idx = buf.find(b"$")
                    if idx < 0:
                        # Schutz vor endlosen Müll-Puffern ohne $
                        if len(buf) > 8192:
                            garbage = bytes(buf)
                            del buf[:]
                            text = garbage.decode("latin-1", errors="replace")
                            self.line_ready.emit(f"{_now_ms()}  {text}")
                        break
                    frame = bytes(buf[: idx + 1])
                    del buf[: idx + 1]
                    text = frame.decode("latin-1", errors="replace")
                    text = text.replace("\r", "").replace("\n", "")
                    if text:
                        self.line_ready.emit(f"{_now_ms()}  {text}")
        finally:
            try:
                if self._ser is not None and self._ser.is_open:
                    self._ser.close()
            except Exception:
                pass
            self._ser = None
            self.status.emit("Getrennt")
            self.finished.emit()


class BusMonitorWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("RS485 Bus-Monitor (Test)")
        self.resize(900, 700)

        self._paused = False
        self._autoscroll = True
        self._filter = ""
        self._lines: list[str] = []
        self._thread: Optional[QThread] = None
        self._reader: Optional[SerialReader] = None
        self._last_ui_ts = 0.0
        self._dirty = False

        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)

        # ── Port / Baud ────────────────────────────────────────────────────
        top = QHBoxLayout()
        top.addWidget(QLabel("Port:"))
        self.cb_port = QComboBox()
        self.cb_port.setMinimumWidth(280)
        self.cb_port.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        top.addWidget(self.cb_port, 1)

        self.btn_refresh = QPushButton("Aktualisieren")
        self.btn_refresh.clicked.connect(self._refresh_ports)
        top.addWidget(self.btn_refresh)

        top.addWidget(QLabel("Baud:"))
        self.cb_baud = QComboBox()
        self.cb_baud.setEditable(True)
        self.cb_baud.addItems(BAUDRATES)
        self.cb_baud.setCurrentText(DEFAULT_BAUD)
        self.cb_baud.setMinimumWidth(100)
        top.addWidget(self.cb_baud)

        self.btn_connect = QPushButton("Verbinden")
        self.btn_connect.clicked.connect(self._toggle_connect)
        top.addWidget(self.btn_connect)

        layout.addLayout(top)

        # ── Log ────────────────────────────────────────────────────────────
        self.txt = QPlainTextEdit()
        self.txt.setReadOnly(True)
        mono = QFont("Consolas")
        if not mono.exactMatch():
            mono = QFont("Courier New")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        mono.setPointSize(10)
        self.txt.setFont(mono)
        try:
            self.txt.document().setMaximumBlockCount(MAX_LINES)
        except Exception:
            pass
        layout.addWidget(self.txt, 1)

        # ── Filter ─────────────────────────────────────────────────────────
        filt = QHBoxLayout()
        filt.addWidget(QLabel("Filter:"))
        self.ed_filter = QLineEdit()
        self.ed_filter.setPlaceholderText("Teiltext in Zeile")
        self.ed_filter.setClearButtonEnabled(True)
        self.ed_filter.textChanged.connect(self._on_filter_changed)
        filt.addWidget(self.ed_filter, 1)
        layout.addLayout(filt)

        # ── Buttons ────────────────────────────────────────────────────────
        btns = QHBoxLayout()
        self.btn_pause = QPushButton("Log anhalten")
        self.btn_pause.clicked.connect(self._toggle_pause)
        btns.addWidget(self.btn_pause)

        self.btn_scroll = QPushButton("Scroll anhalten")
        self.btn_scroll.clicked.connect(self._toggle_scroll)
        btns.addWidget(self.btn_scroll)

        self.btn_clear = QPushButton("Leeren")
        self.btn_clear.clicked.connect(self._clear)
        btns.addWidget(self.btn_clear)

        btns.addStretch(1)
        layout.addLayout(btns)

        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status.showMessage("Bereit — Port wählen und verbinden")

        self._refresh_ports()

        self._ui_timer = QTimer(self)
        self._ui_timer.setInterval(80)
        self._ui_timer.timeout.connect(self._flush_ui)
        self._ui_timer.start()

    # ── Portliste ──────────────────────────────────────────────────────────
    def _refresh_ports(self) -> None:
        current = self.cb_port.currentData()
        self.cb_port.blockSignals(True)
        self.cb_port.clear()
        ports = list_serial_ports()
        for device, label in ports:
            self.cb_port.addItem(label, device)
        self.cb_port.blockSignals(False)
        if not ports:
            self.status.showMessage("Keine COM-Ports gefunden")
            return
        if current:
            idx = self.cb_port.findData(current)
            if idx >= 0:
                self.cb_port.setCurrentIndex(idx)
        self.status.showMessage(f"{len(ports)} Port(s) gefunden")

    def _selected_port(self) -> str:
        data = self.cb_port.currentData()
        if data:
            return str(data)
        return str(self.cb_port.currentText() or "").split("—")[0].strip()

    def _selected_baud(self) -> int:
        raw = str(self.cb_baud.currentText() or DEFAULT_BAUD).strip()
        try:
            return max(300, min(3_000_000, int(raw)))
        except ValueError:
            return int(DEFAULT_BAUD)

    # ── Connect ────────────────────────────────────────────────────────────
    def _toggle_connect(self) -> None:
        if self._thread is not None:
            self._disconnect()
        else:
            self._connect()

    def _connect(self) -> None:
        port = self._selected_port()
        if not port:
            QMessageBox.warning(self, "Port", "Bitte einen COM-Port wählen.")
            return
        baud = self._selected_baud()

        self._thread = QThread(self)
        self._reader = SerialReader(port, baud)
        self._reader.moveToThread(self._thread)
        self._thread.started.connect(self._reader.run)
        self._reader.line_ready.connect(self._on_line)
        self._reader.status.connect(self._on_status)
        self._reader.error.connect(self._on_error)
        self._reader.finished.connect(self._on_reader_finished)

        self.btn_connect.setText("Trennen")
        self.cb_port.setEnabled(False)
        self.cb_baud.setEnabled(False)
        self.btn_refresh.setEnabled(False)
        self._thread.start()

    def _disconnect(self) -> None:
        reader = self._reader
        thread = self._thread
        if reader is not None:
            reader.stop()
        if thread is not None:
            thread.quit()
            if not thread.wait(1500):
                thread.terminate()
                thread.wait(500)
        self._reader = None
        self._thread = None
        self.btn_connect.setText("Verbinden")
        self.cb_port.setEnabled(True)
        self.cb_baud.setEnabled(True)
        self.btn_refresh.setEnabled(True)

    @Slot()
    def _on_reader_finished(self) -> None:
        # Thread endete von selbst (Fehler / close)
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(500)
        self._reader = None
        self._thread = None
        self.btn_connect.setText("Verbinden")
        self.cb_port.setEnabled(True)
        self.cb_baud.setEnabled(True)
        self.btn_refresh.setEnabled(True)

    @Slot(str)
    def _on_status(self, msg: str) -> None:
        self.status.showMessage(msg)

    @Slot(str)
    def _on_error(self, msg: str) -> None:
        self.status.showMessage(msg)
        QMessageBox.warning(self, "Serial", msg)

    # ── Log-Anzeige ────────────────────────────────────────────────────────
    @Slot(str)
    def _on_line(self, line: str) -> None:
        self._lines.append(line)
        if len(self._lines) > MAX_LINES:
            overflow = len(self._lines) - MAX_LINES
            del self._lines[:overflow]
        if self._paused:
            return
        self._dirty = True
        now = time.monotonic()
        if (now - self._last_ui_ts) >= 0.05:
            self._flush_ui()

    def _flush_ui(self) -> None:
        if not self._dirty or self._paused:
            return
        self._dirty = False
        self._last_ui_ts = time.monotonic()
        self._render()

    def _visible_lines(self) -> list[str]:
        needle = self._filter.strip().lower()
        if not needle:
            return list(self._lines)
        return [ln for ln in self._lines if needle in ln.lower()]

    def _render(self) -> None:
        lines = self._visible_lines()
        sb = self.txt.verticalScrollBar()
        old = int(sb.value())
        self.txt.setPlainText("\n".join(lines))
        if self._autoscroll:
            sb.setValue(sb.maximum())
            self.txt.moveCursor(QTextCursor.MoveOperation.End)
        else:
            sb.setValue(min(old, sb.maximum()))

    def _on_filter_changed(self, text: str) -> None:
        self._filter = str(text or "")
        self._render()

    def _toggle_pause(self) -> None:
        self._paused = not self._paused
        self.btn_pause.setText("Log starten" if self._paused else "Log anhalten")
        if not self._paused:
            self._dirty = True
            self._flush_ui()

    def _toggle_scroll(self) -> None:
        self._autoscroll = not self._autoscroll
        self.btn_scroll.setText(
            "Scroll starten" if not self._autoscroll else "Scroll anhalten"
        )
        if self._autoscroll and not self._paused:
            sb = self.txt.verticalScrollBar()
            sb.setValue(sb.maximum())

    def _clear(self) -> None:
        self._lines.clear()
        self.txt.clear()
        self._dirty = False

    def closeEvent(self, event) -> None:  # noqa: N802
        self._disconnect()
        super().closeEvent(event)


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("RS485 Bus-Monitor")
    win = BusMonitorWindow()
    win.show()
    return int(app.exec())


if __name__ == "__main__":
    raise SystemExit(main())
