"""Gemeinsame Ortssuche (Nominatim) für Karte und Kompass."""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QInputDialog, QMessageBox, QWidget

from ..geocode import geocode_places
from ..i18n import t
from ..net_utils import check_internet


class GeocodeThread(QThread):
    results_ready = Signal(list)
    error_occurred = Signal(str)

    def __init__(self, query: str) -> None:
        super().__init__()
        self._query = query

    def run(self) -> None:
        try:
            results = geocode_places(self._query)
            payload = [
                {"lat": r.lat, "lon": r.lon, "display_name": r.display_name} for r in results
            ]
            self.results_ready.emit(payload)
        except Exception as exc:  # noqa: BLE001
            self.error_occurred.emit(str(exc))


def confirm_internet_for_place_search(parent: QWidget) -> bool:
    if check_internet():
        return True
    QMessageBox.warning(
        parent,
        t("map.search_no_internet_title"),
        t("map.search_no_internet_body"),
    )
    return False


def pick_geocode_result(parent: QWidget, results: list) -> dict | None:
    """Einzel- oder Mehrfachtreffer — gleiche Dialoge wie in der Karte."""
    if not results:
        QMessageBox.information(
            parent,
            t("map.search_not_found_title"),
            t("map.search_not_found_body"),
        )
        return None
    if len(results) == 1:
        return results[0]
    labels = [str(r.get("display_name") or "") for r in results]
    choice, ok = QInputDialog.getItem(
        parent,
        t("map.search_pick_title"),
        t("map.search_pick_body"),
        labels,
        0,
        False,
    )
    if not ok or not choice:
        return None
    try:
        idx = labels.index(choice)
    except ValueError:
        return None
    return results[idx]
