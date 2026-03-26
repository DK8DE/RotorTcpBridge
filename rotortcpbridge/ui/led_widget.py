"""LED-Anzeige-Widget (Status-Indikator)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QColor
from PySide6.QtWidgets import QWidget


class Led(QWidget):
    """Kleine LED-Anzeige als farbiger Punkt (rot/grün)."""

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
        self.setMinimumSize(self._d, self._d)
        self.setMaximumSize(self._d, self._d)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)

    def set_state(self, on: bool):
        self._on = bool(on)
        self.update()

    def paintEvent(self, _event):
        with QPainter(self) as p:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

            color = QColor(46, 204, 113) if self._on else QColor(231, 76, 60)
            border = QColor(30, 30, 30)

            rect = self.rect().adjusted(1, 1, -1, -1)

            p.setPen(border)
            p.setBrush(color)
            p.drawEllipse(rect)
