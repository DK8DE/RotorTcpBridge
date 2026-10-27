"""Gemeinsame Antennen-Felder (Settings + Inbetriebnahme): Name, Versatz, Dipol."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QRegularExpression
from PySide6.QtGui import QRegularExpressionValidator
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QGridLayout,
    QLabel,
    QLineEdit,
    QWidget,
)

from ..i18n import t, tt

ANTENNA_NAME_MAX_LEN = 9
ANTENNA_NAME_FORBIDDEN = "#:$"
ANTENNA_NAME_VALIDATOR_PATTERN = r"[^#:$]*"


def sanitize_antenna_name(s: str) -> str:
    """Gleiche Zeichenregeln wie Controller/Rotor SETANTNAME (max. 9, ohne # : $)."""
    text = (s or "").strip()
    return "".join(
        c for c in text[:ANTENNA_NAME_MAX_LEN] if c not in ANTENNA_NAME_FORBIDDEN
    )[:ANTENNA_NAME_MAX_LEN]


class AntennaNamesPanel(QWidget):
    """Drei Antennen je eine Zeile: Name | Versatzwinkel | Dipol."""

    def __init__(self, parent=None, *, use_setup_labels: bool = False):
        super().__init__(parent)
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(6)
        self.edits: list[QLineEdit] = []
        self.offset_spins: list[QDoubleSpinBox] = []
        self.dipole_checks: list[QComboBox] = []
        validator = QRegularExpressionValidator(
            QRegularExpression(ANTENNA_NAME_VALIDATOR_PATTERN)
        )
        tip_name = tt("settings.tooltip_antenna_name")
        tip_off = tt("settings.tooltip_antenna_offset")
        tip_dip = tt("settings.tooltip_antenna_dipole")

        hdr_ant = QLabel(t("setup.ant_col_antenna") if use_setup_labels else "")
        hdr_name = QLabel(t("setup.ant_col_name"))
        hdr_off = QLabel(t("setup.ant_col_offset"))
        hdr_dip = QLabel(t("setup.ant_col_dipole"))
        for hdr in (hdr_ant, hdr_name, hdr_off, hdr_dip):
            f = hdr.font()
            f.setBold(True)
            hdr.setFont(f)
        grid.addWidget(hdr_ant, 0, 0)
        grid.addWidget(hdr_name, 0, 1)
        grid.addWidget(hdr_off, 0, 2)
        grid.addWidget(hdr_dip, 0, 3)

        for i in range(1, 4):
            title = (
                t("setup.ant_name", n=i)
                if use_setup_labels
                else t(f"settings.antenna_{i}")
            )
            lbl = QLabel(title)

            ed = QLineEdit()
            ed.setMaxLength(ANTENNA_NAME_MAX_LEN)
            ed.setValidator(validator)
            ed.setMinimumWidth(100)
            ed.setToolTip(tip_name)

            sp = QDoubleSpinBox()
            sp.setRange(0.0, 360.0)
            sp.setDecimals(1)
            sp.setSingleStep(1.0)
            sp.setSuffix(" °")
            sp.setMinimumWidth(90)
            sp.setToolTip(tip_off)

            dip = QComboBox()
            dip.addItem(t("setup.ant_dipole_no"), False)
            dip.addItem(t("setup.ant_dipole_yes"), True)
            dip.setMinimumWidth(72)
            dip.setToolTip(tip_dip)

            r = i
            grid.addWidget(lbl, r, 0)
            grid.addWidget(ed, r, 1)
            grid.addWidget(sp, r, 2)
            grid.addWidget(dip, r, 3)

            self.edits.append(ed)
            self.offset_spins.append(sp)
            self.dipole_checks.append(dip)

        grid.setColumnStretch(1, 1)

    def set_names(self, names: list[str] | tuple[str, ...] | None) -> None:
        seq = list(names or [])
        while len(seq) < 3:
            seq.append("")
        for i, ed in enumerate(self.edits):
            ed.blockSignals(True)
            ed.setText(sanitize_antenna_name(str(seq[i])))
            ed.blockSignals(False)

    def names(self) -> list[str]:
        return [sanitize_antenna_name(ed.text()) for ed in self.edits]

    def set_offsets(self, offsets: list[float] | tuple[float, ...] | None) -> None:
        seq = list(offsets or [])
        while len(seq) < 3:
            seq.append(0.0)
        for i, sp in enumerate(self.offset_spins):
            try:
                v = max(0.0, min(360.0, float(seq[i])))
            except (TypeError, ValueError):
                v = 0.0
            sp.blockSignals(True)
            sp.setValue(v)
            sp.blockSignals(False)

    def offsets(self) -> list[float]:
        return [float(sp.value()) for sp in self.offset_spins]

    def set_dipoles(self, dipoles: list[bool] | tuple[bool, ...] | None) -> None:
        seq = list(dipoles or [])
        while len(seq) < 3:
            seq.append(False)
        for i, cb in enumerate(self.dipole_checks):
            want = bool(seq[i])
            idx = 1 if want else 0
            cb.blockSignals(True)
            cb.setCurrentIndex(idx)
            cb.blockSignals(False)

    def dipoles(self) -> list[bool]:
        out: list[bool] = []
        for cb in self.dipole_checks:
            data = cb.currentData()
            out.append(bool(data) if data is not None else cb.currentIndex() == 1)
        return out

    def load_from_cfg(self, cfg: dict, *, defaults: Optional[list[str]] = None) -> None:
        ui = cfg.get("ui") if isinstance(cfg.get("ui"), dict) else {}
        raw = list(ui.get("antenna_names") or [])
        while len(raw) < 3:
            if defaults and len(defaults) > len(raw):
                raw.append(defaults[len(raw)])
            else:
                raw.append(t(f"settings.antenna_{len(raw) + 1}"))
        self.set_names(raw)
        offs = list(ui.get("antenna_offsets_az") or [0.0, 0.0, 0.0])
        while len(offs) < 3:
            offs.append(0.0)
        self.set_offsets(offs)
        dips = list(ui.get("antenna_dipoles_az") or [False, False, False])
        while len(dips) < 3:
            dips.append(False)
        self.set_dipoles(dips)

    def apply_to_cfg(self, cfg: dict) -> None:
        ui = cfg.setdefault("ui", {})
        ui["antenna_names"] = self.names()
        ui["antenna_offsets_az"] = self.offsets()
        ui["antenna_dipoles_az"] = self.dipoles()
