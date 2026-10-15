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
    """SoftAP am Rotor einschalten und Webflasher-Hinweis anzeigen."""

    softap_toggled = Signal(bool)
    refresh_clicked = Signal()
    open_webflasher_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._block_toggle = False
        root = QVBoxLayout(self)
        root.setSpacing(px_to_dip(self, 10))

        self._lbl_intro = QLabel(t("settings.update_intro"))
        self._lbl_intro.setWordWrap(True)
        root.addWidget(self._lbl_intro)

        self._gb_softap = QGroupBox(t("settings.update_group_softap"))
        vl = QVBoxLayout(self._gb_softap)
        vl.setSpacing(px_to_dip(self, 8))

        row = QHBoxLayout()
        row.setSpacing(px_to_dip(self, 8))
        led_d = max(8, px_to_dip(self, 12))
        self.led_softap = Led(led_d, self)
        self.led_softap.setToolTip(tt("settings.update_softap_led_tooltip"))
        self.led_softap.set_state(False)
        row.addWidget(self.led_softap, 0, Qt.AlignmentFlag.AlignVCenter)
        self.chk_softap = QCheckBox(t("settings.update_softap_enable"))
        self.chk_softap.setToolTip(tt("settings.update_softap_enable_tooltip"))
        self.chk_softap.toggled.connect(self._on_chk_toggled)
        row.addWidget(self.chk_softap, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addStretch(1)
        self.btn_refresh = QPushButton(t("settings.update_btn_refresh"))
        self.btn_refresh.setToolTip(tt("settings.update_btn_refresh_tooltip"))
        self.btn_refresh.setAutoDefault(False)
        self.btn_refresh.clicked.connect(self.refresh_clicked.emit)
        row.addWidget(self.btn_refresh, 0)
        vl.addLayout(row)

        self._lbl_details = QLabel(t("settings.update_softap_details"))
        self._lbl_details.setWordWrap(True)
        vl.addWidget(self._lbl_details)

        self._lbl_reboot_hint = QLabel(t("settings.update_reboot_hint"))
        self._lbl_reboot_hint.setWordWrap(True)
        self._lbl_reboot_hint.setStyleSheet("font-weight: bold;")
        vl.addWidget(self._lbl_reboot_hint)

        self.btn_open = QPushButton(t("settings.update_btn_open_webflasher"))
        self.btn_open.setToolTip(tt("settings.update_btn_open_webflasher_tooltip"))
        self.btn_open.setAutoDefault(False)
        self.btn_open.clicked.connect(self.open_webflasher_clicked.emit)
        vl.addWidget(self.btn_open, 0, Qt.AlignmentFlag.AlignLeft)

        self.lbl_status = QLabel("")
        self.lbl_status.setWordWrap(True)
        vl.addWidget(self.lbl_status)

        root.addWidget(self._gb_softap)
        root.addStretch(1)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)

    def _on_chk_toggled(self, checked: bool) -> None:
        if self._block_toggle:
            return
        self.softap_toggled.emit(bool(checked))

    def set_softap_active(self, active: bool | None) -> None:
        """LED + Checkbox an SoftAP-Status anpassen (ohne Signal)."""
        self._block_toggle = True
        try:
            on = bool(active) if active is not None else False
            self.chk_softap.setChecked(on)
            self.led_softap.set_state(on)
        finally:
            self._block_toggle = False

    def set_status(self, text: str) -> None:
        self.lbl_status.setText(str(text or ""))

    def retranslate(self) -> None:
        self._lbl_intro.setText(t("settings.update_intro"))
        self._gb_softap.setTitle(t("settings.update_group_softap"))
        self.chk_softap.setText(t("settings.update_softap_enable"))
        self.chk_softap.setToolTip(tt("settings.update_softap_enable_tooltip"))
        self.led_softap.setToolTip(tt("settings.update_softap_led_tooltip"))
        self.btn_refresh.setText(t("settings.update_btn_refresh"))
        self.btn_refresh.setToolTip(tt("settings.update_btn_refresh_tooltip"))
        self._lbl_details.setText(t("settings.update_softap_details"))
        self._lbl_reboot_hint.setText(t("settings.update_reboot_hint"))
        self.btn_open.setText(t("settings.update_btn_open_webflasher"))
        self.btn_open.setToolTip(tt("settings.update_btn_open_webflasher_tooltip"))
