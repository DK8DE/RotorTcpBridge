"""Globaler Schutz gegen versehentliches Verstellen von Dropdown-/Spin-Widgets.

Ueber ein Mausrad, das waehrend des Scrollens zufaellig ueber einem
``QComboBox`` oder ``QAbstractSpinBox`` landet, werden Werte in Qt standardmaessig
sofort geaendert — auch wenn das Widget nicht den Fokus hat und das Dropdown
nicht geoeffnet ist. Das ist im Formular-/Einstellungskontext unerwuenscht.

Die hier definierte :class:`WheelGuard` laesst sich als Application-Event-Filter
installieren. Mausrad-Events auf Combo- und Spinbox-Widgets (inkl. deren
inneren QLineEdit-/Button-Kinder) werden verworfen und stattdessen an einen
umgebenden ``QAbstractScrollArea`` weitergereicht, sodass der Scrollbereich
weiterhin scrollt.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractScrollArea,
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QWidget,
)


class WheelGuard(QObject):
    """Event-Filter, der Wheel-Events auf Dropdowns/Spinboxen blockt.

    Das Filter wird typischerweise einmal global auf die :class:`QApplication`
    installiert::

        guard = WheelGuard(app)
        app.installEventFilter(guard)

    Verhalten:
    - Landet ein Wheel-Event auf einem ``QComboBox`` oder ``QAbstractSpinBox``
      (oder auf einem inneren Kindwidget davon wie dem eingebetteten
      ``QLineEdit``), wird das Event unterbunden — der Wert des Widgets
      aendert sich nicht.
    - Das Event wird an den umgebenden ``QAbstractScrollArea``/dessen
      ``viewport()`` weitergereicht, damit der Nutzer den Formularbereich
      weiter scrollen kann.
    - Wenn kein Scrollbereich gefunden wird, wird das Event schlicht
      verworfen; das unterbindet sicher jede Wertaenderung.
    """

    _GUARDED_TYPES = (QComboBox, QAbstractSpinBox)

    @staticmethod
    def _find_guarded_ancestor(w: QWidget | None) -> QWidget | None:
        """Gibt den obersten Combo-/Spinbox-Vorfahren zurueck, falls vorhanden."""
        while w is not None:
            if isinstance(w, WheelGuard._GUARDED_TYPES):
                return w
            w = w.parentWidget()
        return None

    @staticmethod
    def _find_scroll_area(w: QWidget | None) -> QAbstractScrollArea | None:
        """Sucht den ersten umgebenden Scrollbereich oberhalb von ``w``."""
        while w is not None:
            if isinstance(w, QAbstractScrollArea):
                return w
            w = w.parentWidget()
        return None

    @staticmethod
    def _is_inside_item_view(w: QWidget | None) -> bool:
        """True, wenn ``w`` innerhalb einer aufgeklappten Dropdown-Liste liegt.

        Das geoeffnete ``QComboBox``-Popup ist intern ein ``QAbstractItemView``
        (z. B. ``QListView``), das als Vorfahr im Widget-Baum erscheint, bevor
        die eigentliche ``QComboBox`` als noch weiter entfernter Parent
        auftaucht. Findet sich die Item-View in der Kette zuerst, handelt es
        sich um das aufgeklappte Popup — dort soll das Mausrad normal
        funktionieren.
        """
        while w is not None:
            if isinstance(w, QAbstractItemView):
                return True
            w = w.parentWidget()
        return False

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() != QEvent.Type.Wheel:
            return False
        start = obj if isinstance(obj, QWidget) else None
        # Offenes Combo-Popup: Scrollen in der Liste zulassen.
        if self._is_inside_item_view(start):
            return False
        target = self._find_guarded_ancestor(start)
        if target is None:
            return False
        # Oberhalb der Combo-/Spinbox nach einem Scrollbereich suchen und
        # dessen Viewport das Event geben, damit das Scrollen weitergeht.
        scroll = self._find_scroll_area(target.parentWidget())
        if scroll is not None:
            viewport = scroll.viewport()
            if viewport is not None:
                QApplication.sendEvent(viewport, event)
        return True


def install_wheel_guard(app: QApplication) -> WheelGuard:
    """Richtet den :class:`WheelGuard` global auf ``app`` ein und gibt ihn zurueck."""
    guard = WheelGuard(app)
    app.installEventFilter(guard)
    return guard
