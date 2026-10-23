"""Gemeinsames Standort-Panel (Locator + Lat/Lon) für Settings und Inbetriebnahme."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..geo_utils import maidenhead_to_lat_lon
from ..i18n import t, tt
from .ui_utils import px_to_dip


class LocationPanel(QWidget):
    """Bearbeitet ``ui.location_lat`` / ``location_lon`` / ``location_locator``."""

    def __init__(self, cfg: dict, parent=None):
        super().__init__(parent)
        self._cfg = cfg if isinstance(cfg, dict) else {}
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        self.gb_location = QGroupBox(t("settings.group_location"))
        fl = QFormLayout(self.gb_location)

        self.ed_location_lat = QDoubleSpinBox()
        self.ed_location_lat.setRange(-90.0, 90.0)
        self.ed_location_lat.setDecimals(6)
        self.ed_location_lat.setSuffix("°")
        self.ed_location_lat.setToolTip(tt("settings.location_lat_tooltip"))
        fl.addRow(t("settings.location_lat"), self.ed_location_lat)

        self.ed_location_lon = QDoubleSpinBox()
        self.ed_location_lon.setRange(-180.0, 180.0)
        self.ed_location_lon.setDecimals(6)
        self.ed_location_lon.setSuffix("°")
        self.ed_location_lon.setToolTip(tt("settings.location_lon_tooltip"))
        fl.addRow(t("settings.location_lon"), self.ed_location_lon)

        _row_locator = QWidget()
        _lay_locator = QHBoxLayout(_row_locator)
        _lay_locator.setContentsMargins(0, 0, 0, 0)
        _lay_locator.setSpacing(px_to_dip(self, 8))
        self.ed_location_locator = QLineEdit()
        self.ed_location_locator.setMaxLength(10)
        self.ed_location_locator.setPlaceholderText("JO31jg")
        self.ed_location_locator.setToolTip(tt("settings.location_locator_tooltip"))
        _lay_locator.addWidget(self.ed_location_locator, 1)
        self.btn_locator_apply_coords = QPushButton(t("settings.btn_locator_apply_coords"))
        self.btn_locator_apply_coords.setAutoDefault(False)
        self.btn_locator_apply_coords.setDefault(False)
        self.btn_locator_apply_coords.setToolTip(tt("settings.btn_locator_apply_coords_tooltip"))
        self.btn_locator_apply_coords.clicked.connect(self.apply_locator_to_coords)
        _lay_locator.addWidget(self.btn_locator_apply_coords, 0)
        fl.addRow(t("settings.location_locator"), _row_locator)

        root.addWidget(self.gb_location)
        self.load_from_cfg()

    def set_cfg(self, cfg: dict) -> None:
        self._cfg = cfg if isinstance(cfg, dict) else {}

    def load_from_cfg(self) -> None:
        ui = self._cfg.get("ui") if isinstance(self._cfg.get("ui"), dict) else {}
        try:
            self.ed_location_lat.setValue(float(ui.get("location_lat", 49.502651)))
        except (TypeError, ValueError):
            self.ed_location_lat.setValue(49.502651)
        try:
            self.ed_location_lon.setValue(float(ui.get("location_lon", 8.375019)))
        except (TypeError, ValueError):
            self.ed_location_lon.setValue(8.375019)
        self.ed_location_locator.setText(str(ui.get("location_locator", "") or ""))

    def apply_to_cfg(self, cfg: dict) -> None:
        ui = cfg.setdefault("ui", {})
        ui["location_lat"] = float(self.ed_location_lat.value())
        ui["location_lon"] = float(self.ed_location_lon.value())
        ui["location_locator"] = str(self.ed_location_locator.text() or "").strip()

    def apply_locator_to_coords(self) -> bool:
        """Maidenhead → Zellenmitte in Lat/Lon. True bei Erfolg."""
        s = "".join(str(self.ed_location_locator.text() or "").strip().upper().split())
        if not s:
            QMessageBox.information(
                self,
                t("settings.title"),
                t("settings.locator_empty"),
            )
            return False
        ll = maidenhead_to_lat_lon(s)
        if ll is None:
            QMessageBox.warning(
                self,
                t("settings.title"),
                t("settings.locator_invalid"),
            )
            return False
        self.ed_location_locator.setText(s)
        self.ed_location_lat.setValue(ll[0])
        self.ed_location_lon.setValue(ll[1])
        return True

    def install_aliases_on(self, host) -> None:
        for name in (
            "gb_location",
            "ed_location_lat",
            "ed_location_lon",
            "ed_location_locator",
            "btn_locator_apply_coords",
        ):
            try:
                setattr(host, name, getattr(self, name))
            except Exception:
                pass
