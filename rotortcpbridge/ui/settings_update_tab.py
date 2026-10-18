"""Einstellungen-Tab: Rotor SoftAP / Web-OTA (Update)."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..i18n import t, tt
from .led_widget import Led
from .ui_utils import px_to_dip


class UpdateTab(QWidget):
    """SoftAP pro aktiver Achse (AZ/EL) und Webflasher-Hinweis."""

    softap_toggled = Signal(str, bool)  # axis "az"|"el", want_on
    refresh_clicked = Signal()
    open_webflasher_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._block_toggle: dict[str, bool] = {"az": False, "el": False}
        self._axis_visible = {"az": True, "el": False}

        root = QVBoxLayout(self)
        root.setSpacing(px_to_dip(self, 10))

        self._lbl_intro = QLabel(t("settings.update_intro"))
        self._lbl_intro.setWordWrap(True)
        root.addWidget(self._lbl_intro)

        self._gb_softap = QGroupBox(t("settings.update_group_softap"))
        vl = QVBoxLayout(self._gb_softap)
        vl.setSpacing(px_to_dip(self, 8))

        led_d = max(8, px_to_dip(self, 12))
        self._axis_rows: dict[str, QWidget] = {}
        self._leds: dict[str, Led] = {}
        self._chks: dict[str, QCheckBox] = {}
        self._status_lbls: dict[str, QLabel] = {}

        for axis, enable_key, tip_key in (
            ("az", "settings.update_softap_enable_az", "settings.update_softap_enable_az_tooltip"),
            ("el", "settings.update_softap_enable_el", "settings.update_softap_enable_el_tooltip"),
        ):
            row_w = QWidget(self)
            row = QHBoxLayout(row_w)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(px_to_dip(self, 8))
            led = Led(led_d, self)
            led.setToolTip(tt("settings.update_softap_led_tooltip"))
            led.set_state(False)
            row.addWidget(led, 0, Qt.AlignmentFlag.AlignVCenter)
            chk = QCheckBox(t(enable_key))
            chk.setToolTip(tt(tip_key))
            chk.toggled.connect(lambda checked, a=axis: self._on_chk_toggled(a, checked))
            row.addWidget(chk, 0, Qt.AlignmentFlag.AlignVCenter)
            row.addStretch(1)
            status = QLabel("")
            status.setWordWrap(True)
            row.addWidget(status, 1)
            vl.addWidget(row_w)
            self._axis_rows[axis] = row_w
            self._leds[axis] = led
            self._chks[axis] = chk
            self._status_lbls[axis] = status

        btn_row = QHBoxLayout()
        btn_row.setSpacing(px_to_dip(self, 8))
        self.btn_refresh = QPushButton(t("settings.update_btn_refresh"))
        self.btn_refresh.setToolTip(tt("settings.update_btn_refresh_tooltip"))
        self.btn_refresh.setAutoDefault(False)
        self.btn_refresh.clicked.connect(self.refresh_clicked.emit)
        btn_row.addWidget(self.btn_refresh, 0)
        btn_row.addStretch(1)
        vl.addLayout(btn_row)

        self._lbl_details = QLabel(t("settings.update_softap_details"))
        self._lbl_details.setWordWrap(True)
        vl.addWidget(self._lbl_details)

        self._lbl_both_hint = QLabel(t("settings.update_softap_both_hint"))
        self._lbl_both_hint.setWordWrap(True)
        vl.addWidget(self._lbl_both_hint)

        self._lbl_reboot_hint = QLabel(t("settings.update_reboot_hint"))
        self._lbl_reboot_hint.setWordWrap(True)
        self._lbl_reboot_hint.setStyleSheet("font-weight: bold;")
        vl.addWidget(self._lbl_reboot_hint)

        self.btn_open = QPushButton(t("settings.update_btn_open_webflasher"))
        self.btn_open.setToolTip(tt("settings.update_btn_open_webflasher_tooltip"))
        self.btn_open.setAutoDefault(False)
        self.btn_open.clicked.connect(self.open_webflasher_clicked.emit)
        vl.addWidget(self.btn_open, 0, Qt.AlignmentFlag.AlignLeft)

        root.addWidget(self._gb_softap)
        root.addStretch(1)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.set_axes_visible(az_on=True, el_on=False)

    def _on_chk_toggled(self, axis: str, checked: bool) -> None:
        if self._block_toggle.get(axis):
            return
        self.softap_toggled.emit(str(axis), bool(checked))

    def set_axes_visible(self, *, az_on: bool, el_on: bool) -> None:
        """Zeilen nur für aktivierte Achsen anzeigen."""
        self._axis_visible["az"] = bool(az_on)
        self._axis_visible["el"] = bool(el_on)
        for axis, row in self._axis_rows.items():
            row.setVisible(bool(self._axis_visible.get(axis)))
        both = bool(az_on) and bool(el_on)
        self._lbl_both_hint.setVisible(both)

    def set_softap_active(self, axis: str, active: bool | None) -> None:
        """LED + Checkbox einer Achse an SoftAP-Status anpassen (ohne Signal)."""
        ax = str(axis or "").strip().lower()
        if ax not in self._chks:
            return
        self._block_toggle[ax] = True
        try:
            on = bool(active) if active is not None else False
            self._chks[ax].setChecked(on)
            self._leds[ax].set_state(on)
        finally:
            self._block_toggle[ax] = False

    def set_status(self, axis: str, text: str) -> None:
        ax = str(axis or "").strip().lower()
        lbl = self._status_lbls.get(ax)
        if lbl is not None:
            lbl.setText(str(text or ""))

    def retranslate(self) -> None:
        self._lbl_intro.setText(t("settings.update_intro"))
        self._gb_softap.setTitle(t("settings.update_group_softap"))
        self._chks["az"].setText(t("settings.update_softap_enable_az"))
        self._chks["az"].setToolTip(tt("settings.update_softap_enable_az_tooltip"))
        self._chks["el"].setText(t("settings.update_softap_enable_el"))
        self._chks["el"].setToolTip(tt("settings.update_softap_enable_el_tooltip"))
        for led in self._leds.values():
            led.setToolTip(tt("settings.update_softap_led_tooltip"))
        self.btn_refresh.setText(t("settings.update_btn_refresh"))
        self.btn_refresh.setToolTip(tt("settings.update_btn_refresh_tooltip"))
        self._lbl_details.setText(t("settings.update_softap_details"))
        self._lbl_both_hint.setText(t("settings.update_softap_both_hint"))
        self._lbl_reboot_hint.setText(t("settings.update_reboot_hint"))
        self.btn_open.setText(t("settings.update_btn_open_webflasher"))
        self.btn_open.setToolTip(tt("settings.update_btn_open_webflasher_tooltip"))
