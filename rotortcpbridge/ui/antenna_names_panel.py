"""Gemeinsame Antennen-Felder (Settings + Inbetriebnahme): Name, Versatz, Dipol."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QRegularExpression
from PySide6.QtGui import QFont, QRegularExpressionValidator
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
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
    """Drei Antennen: Name, AZ-Versatz °, Dipol — wie in den Einstellungen."""

    def __init__(self, parent=None, *, use_setup_labels: bool = False):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)
        self.edits: list[QLineEdit] = []
        self.offset_spins: list[QDoubleSpinBox] = []
        self.dipole_checks: list[QCheckBox] = []
        validator = QRegularExpressionValidator(
            QRegularExpression(ANTENNA_NAME_VALIDATOR_PATTERN)
        )
        tip_name = tt("settings.tooltip_antenna_name")
        tip_off = tt("settings.tooltip_antenna_offset")
        tip_dip = tt("settings.tooltip_antenna_dipole")
        for i in range(1, 4):
            title = (
                t("setup.ant_name", n=i)
                if use_setup_labels
                else t(f"settings.antenna_{i}")
            )
            lbl_title = QLabel(title)
            fnt = QFont(lbl_title.font())
            fnt.setBold(True)
            lbl_title.setFont(fnt)

            ed = QLineEdit()
            ed.setMaxLength(ANTENNA_NAME_MAX_LEN)
            ed.setValidator(validator)
            ed.setMinimumWidth(120)
            ed.setToolTip(tip_name)

            sp = QDoubleSpinBox()
            sp.setRange(0.0, 360.0)
            sp.setDecimals(1)
            sp.setSingleStep(1.0)
            sp.setSuffix(" °")
            sp.setMinimumWidth(90)
            sp.setToolTip(tip_off)

            chk = QCheckBox(t("settings.antenna_dipole_label"))
            chk.setToolTip(tip_dip)

            row_vals = QHBoxLayout()
            row_vals.setContentsMargins(0, 0, 0, 0)
            row_vals.setSpacing(8)
            row_vals.addWidget(QLabel(t("settings.antenna_offset_unit")))
            row_vals.addWidget(sp)
            row_vals.addWidget(chk)
            row_vals.addStretch(1)

            block = QVBoxLayout()
            block.setContentsMargins(0, 0, 0, 0)
            block.setSpacing(4)
            block.addWidget(lbl_title)
            block.addWidget(ed)
            block.addLayout(row_vals)
            w = QWidget()
            w.setLayout(block)
            root.addWidget(w)

            self.edits.append(ed)
            self.offset_spins.append(sp)
            self.dipole_checks.append(chk)

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
        for i, chk in enumerate(self.dipole_checks):
            chk.blockSignals(True)
            chk.setChecked(bool(seq[i]))
            chk.blockSignals(False)

    def dipoles(self) -> list[bool]:
        return [bool(chk.isChecked()) for chk in self.dipole_checks]

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
