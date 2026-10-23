"""Gemeinsames Panel: Master-/AZ-/EL-Bus-IDs (Settings + Inbetriebnahme)."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QGroupBox,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..i18n import t, tt


def clamp_slave_id(v: object, default: int = 1) -> int:
    try:
        x = int(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        x = int(default)
    return max(1, min(254, x))


class RotorBusIdsPanel(QWidget):
    """Bearbeitet ``rotor_bus`` Master/Slave und Achsen-Aktivierung."""

    axes_changed = Signal()

    def __init__(self, cfg: dict, parent=None):
        super().__init__(parent)
        self._cfg = cfg if isinstance(cfg, dict) else {}
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        gb = QGroupBox(t("settings.group_master_rotor_ids"))
        fl = QFormLayout(gb)

        self.sp_master = QSpinBox()
        self.sp_master.setRange(0, 255)
        self.sp_master.setToolTip(tt("settings.tooltip_master_id"))
        fl.addRow(t("settings.master_id"), self.sp_master)

        self.chk_enable_az = QCheckBox(t("settings.chk_enable_az"))
        self.chk_enable_az.setToolTip(tt("settings.chk_enable_az_tooltip"))
        self.sp_slave_az = QSpinBox()
        self.sp_slave_az.setRange(1, 254)
        self.sp_slave_az.setToolTip(tt("settings.tooltip_slave_az"))
        fl.addRow(self.chk_enable_az, self.sp_slave_az)

        self.chk_enable_el = QCheckBox(t("settings.chk_enable_el"))
        self.chk_enable_el.setToolTip(tt("settings.chk_enable_el_tooltip"))
        self.sp_slave_el = QSpinBox()
        self.sp_slave_el.setRange(1, 254)
        self.sp_slave_el.setToolTip(tt("settings.tooltip_slave_el"))
        fl.addRow(self.chk_enable_el, self.sp_slave_el)

        root.addWidget(gb)
        self.chk_enable_az.toggled.connect(self._on_axes_toggled)
        self.chk_enable_el.toggled.connect(self._on_axes_toggled)
        self.load_from_cfg()

    def set_cfg(self, cfg: dict) -> None:
        self._cfg = cfg if isinstance(cfg, dict) else {}

    def _on_axes_toggled(self, *_args) -> None:
        if not self.chk_enable_az.isChecked() and not self.chk_enable_el.isChecked():
            self.chk_enable_az.blockSignals(True)
            try:
                self.chk_enable_az.setChecked(True)
            finally:
                self.chk_enable_az.blockSignals(False)
        self.sp_slave_az.setEnabled(self.chk_enable_az.isChecked())
        self.sp_slave_el.setEnabled(self.chk_enable_el.isChecked())
        self.axes_changed.emit()

    def load_from_cfg(self) -> None:
        rb = self._cfg.get("rotor_bus") if isinstance(self._cfg.get("rotor_bus"), dict) else {}
        try:
            self.sp_master.setValue(int(rb.get("master_id", 0) or 0))
        except Exception:
            self.sp_master.setValue(0)
        self.chk_enable_az.setChecked(bool(rb.get("enable_az", True)))
        self.chk_enable_el.setChecked(bool(rb.get("enable_el", False)))
        if not self.chk_enable_az.isChecked() and not self.chk_enable_el.isChecked():
            self.chk_enable_az.setChecked(True)
        self.sp_slave_az.setValue(clamp_slave_id(rb.get("slave_az", 20), 20))
        self.sp_slave_el.setValue(clamp_slave_id(rb.get("slave_el", 21), 21))
        self.sp_slave_az.setEnabled(self.chk_enable_az.isChecked())
        self.sp_slave_el.setEnabled(self.chk_enable_el.isChecked())

    def apply_to_cfg(self, cfg: dict) -> None:
        rb = cfg.setdefault("rotor_bus", {})
        rb["master_id"] = int(self.sp_master.value())
        rb["enable_az"] = bool(self.chk_enable_az.isChecked())
        rb["enable_el"] = bool(self.chk_enable_el.isChecked())
        rb["slave_az"] = int(self.sp_slave_az.value())
        rb["slave_el"] = int(self.sp_slave_el.value())
        # Controller-IDs am Profil spiegeln (wie bei Profilwechsel)
        chw = cfg.setdefault("controller_hw", {})
        chw["az_rotor_id"] = int(rb["slave_az"]) if rb["enable_az"] else 0
        chw["el_rotor_id"] = int(rb["slave_el"]) if rb["enable_el"] else 0

    def validation_error(self) -> str | None:
        """None wenn ok, sonst lokalisierter Fehlertext."""
        en_az = bool(self.chk_enable_az.isChecked())
        en_el = bool(self.chk_enable_el.isChecked())
        if not en_az and not en_el:
            return t("setup.conn_ids_err_no_axis")
        mid = int(self.sp_master.value())
        az = int(self.sp_slave_az.value())
        el = int(self.sp_slave_el.value())
        if en_az and az == mid:
            return t("setup.conn_ids_err_az_eq_master")
        if en_el and el == mid:
            return t("setup.conn_ids_err_el_eq_master")
        if en_az and en_el and az == el:
            return t("setup.conn_ids_err_az_eq_el")
        return None

    def enable_az(self) -> bool:
        return bool(self.chk_enable_az.isChecked())

    def enable_el(self) -> bool:
        return bool(self.chk_enable_el.isChecked())
