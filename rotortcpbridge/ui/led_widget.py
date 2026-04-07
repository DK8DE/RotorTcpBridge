"""LED-Anzeige-Widget (Status-Indikator)."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPainter, QColor
from PySide6.QtWidgets import QWidget


class Led(QWidget):
    """Kleine LED-Anzeige als farbiger Punkt (rot/grün), optional grün blinkend."""

    def __init__(self, diameter: int = 6, parent=None):
        super().__init__(parent)
        try:
            d = int(diameter)
        except Exception:
            d = 6
        if d < 4:
            d = 4
        self._d = d
        self._on = False
        self._blink_green = False
        self._blink_phase = True
        self._blink_timer = QTimer(self)
        self._blink_timer.setInterval(400)
        self._blink_timer.timeout.connect(self._on_blink_tick)
        self.setMinimumSize(self._d, self._d)
        self.setMaximumSize(self._d, self._d)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)

    def _on_blink_tick(self) -> None:
        self._blink_phase = not self._blink_phase
        self.update()

    def set_blinking_green(self, active: bool) -> None:
        """Grün blinkend (laufende Kalibrierung); schaltet Rot/Grün-Anzeige aus."""
        active = bool(active)
        if active == self._blink_green:
            return
        self._blink_green = active
        if active:
            self._blink_phase = True
            self._blink_timer.start()
        else:
            self._blink_timer.stop()
        self.update()

    def set_state(self, on: bool):
        self._blink_green = False
        self._blink_timer.stop()
        self._on = bool(on)
        self.update()

    def paintEvent(self, _event):
        with QPainter(self) as p:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

            if self._blink_green:
                color = (
                    QColor(46, 204, 113)
                    if self._blink_phase
                    else QColor(28, 110, 66)
                )
            else:
                color = QColor(46, 204, 113) if self._on else QColor(231, 76, 60)
            border = QColor(30, 30, 30)

            rect = self.rect().adjusted(1, 1, -1, -1)

            p.setPen(border)
            p.setBrush(color)
            p.drawEllipse(rect)
