"""Gemeinsame UI-Hilfsfunktionen (DPI-Skalierung, Flow-Layout etc.)."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QLayout, QLayoutItem, QWidget


def scale_factor_for(widget: QWidget) -> float:
    """Ermittelt Windows/Qt Scale-Faktor (DPI-Skalierung)."""
    screen = None
    try:
        wh = widget.windowHandle()
        if wh:
            screen = wh.screen()
    except Exception:
        screen = None

    if screen is None:
        try:
            screen = QGuiApplication.primaryScreen()
        except Exception:
            screen = None

    if screen is None:
        return 1.0

    try:
        dpi = float(screen.logicalDotsPerInch())
    except Exception:
        dpi = 96.0

    if dpi <= 1.0:
        return 1.0
    return dpi / 96.0


def px_to_dip(widget: QWidget, px: int) -> int:
    """Konvertiert px (Bildschirmpixel) in DIP für das Widget."""
    sf = scale_factor_for(widget)
    return int(round(float(px) / sf))


class FlowLayout(QLayout):
    """Zeilenumbrüche für Karten/Buttons — gleiche Zeilenhöhe, Breite ausfüllen."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        margin: int = 0,
        h_spacing: int = 6,
        v_spacing: int = 6,
        equalize_heights: bool = True,
        fill_width: bool = True,
        min_item_width: int = 140,
    ) -> None:
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self._h_space = int(h_spacing)
        self._v_space = int(v_spacing)
        self._equalize_heights = bool(equalize_heights)
        self._fill_width = bool(fill_width)
        self._min_item_width = max(40, int(min_item_width))
        self.setContentsMargins(margin, margin, margin, margin)

    def addItem(self, item: QLayoutItem) -> None:  # noqa: N802
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:  # noqa: N802
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int) -> QLayoutItem | None:  # noqa: N802
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self) -> Qt.Orientation:  # noqa: N802
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self) -> QSize:  # noqa: N802
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        size += QSize(m.left() + m.right(), m.top() + m.bottom())
        return size

    def _visible_items(self) -> list[QLayoutItem]:
        out: list[QLayoutItem] = []
        for item in self._items:
            wid = item.widget()
            if wid is not None and not wid.isVisible():
                continue
            out.append(item)
        return out

    def _do_layout(self, rect: QRect, *, test_only: bool) -> int:
        m = self.contentsMargins()
        effective = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        items = self._visible_items()
        if not items:
            return m.top() + m.bottom()

        space_x = self._h_space
        space_y = self._v_space
        avail_w = max(1, effective.width())

        # Spaltenzahl aus Mindestbreite → gleichmäßige Kachelbreite
        if self._fill_width:
            cols = max(1, avail_w // (self._min_item_width + space_x))
            # Nicht mehr Spalten als Items in der längsten sinnvollen Zeile
            cols = min(cols, max(1, len(items)))
            # Wenn Rest zu schmal für letzte Spalte: eine weniger
            while cols > 1 and (avail_w - (cols - 1) * space_x) // cols < self._min_item_width:
                cols -= 1
            cell_w = max(self._min_item_width, (avail_w - (cols - 1) * space_x) // cols)
        else:
            cols = 0
            cell_w = 0

        # Zeilen aufbauen
        rows: list[list[QLayoutItem]] = []
        row: list[QLayoutItem] = []
        x = effective.x()
        for item in items:
            hint_w = cell_w if self._fill_width else max(item.sizeHint().width(), item.minimumSize().width())
            next_x = x + hint_w + space_x
            if row and (
                (self._fill_width and len(row) >= cols)
                or (not self._fill_width and next_x - space_x > effective.right())
            ):
                rows.append(row)
                row = []
                x = effective.x()
                next_x = x + hint_w + space_x
            row.append(item)
            x = next_x
        if row:
            rows.append(row)

        y = effective.y()
        for row_items in rows:
            n = len(row_items)
            heights = [max(it.sizeHint().height(), it.minimumSize().height()) for it in row_items]
            line_h = max(heights) if self._equalize_heights else max(heights or [0])
            if self._fill_width:
                # Restbreite auf die Zeile verteilen (letzte Zeile mit weniger Spalten)
                total_gap = space_x * max(0, n - 1)
                base = max(self._min_item_width, (avail_w - total_gap) // max(1, n))
                widths = [base] * n
                rem = avail_w - total_gap - base * n
                for i in range(max(0, rem)):
                    widths[i % n] += 1
            else:
                widths = [max(it.sizeHint().width(), it.minimumSize().width()) for it in row_items]

            x = effective.x()
            for i, item in enumerate(row_items):
                w = widths[i]
                h = line_h if self._equalize_heights else heights[i]
                if not test_only:
                    item.setGeometry(QRect(QPoint(x, y), QSize(w, h)))
                x += w + space_x
            y += line_h + space_y

        return y - space_y - rect.y() + m.bottom()
