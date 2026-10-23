"""Gemeinsamer HW-Controller-Tab für Settings und Inbetriebnahme."""

from __future__ import annotations

from typing import Any, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..i18n import t, tt
from ..ports import list_serial_port_entries
from .ui_utils import px_to_dip


class ControllerHwTab(QWidget):
    """Bearbeitet ``controller_hw`` / ``controller_link`` (gleiche Ansicht wie Einstellungen)."""

    setconidf_requested = Signal()
    brightness_released = Signal(int)
    usb_remote_toggled = Signal(bool)
    web_toggled = Signal(bool)
    enabled_toggled = Signal(bool)
    save_requested = Signal()

    # Attribute, die SettingsWindow per Alias erwartet
    ALIAS_ATTRS = (
        "chk_hw_controller_enabled",
        "chk_cont_usb_remote",
        "chk_cont_web",
        "_row_cont_usb_port",
        "cb_cont_usb_com",
        "btn_cont_usb_refresh",
        "gb_controller",
        "sp_controller_id",
        "btn_setconidf",
        "_lbl_controller_led",
        "_lbl_controller_wait",
        "sp_cont_az_rotor_id",
        "_lbl_cont_az_rotor_id",
        "sp_cont_el_rotor_id",
        "_lbl_cont_el_rotor_id",
        "sp_cont_pwm_slow",
        "sp_cont_pwm_fast",
        "chk_cont_wind_anemo",
        "cb_cont_encoder_delta",
        "sp_cont_beep_freq",
        "sp_cont_beep_vol",
        "sl_cont_display_brightness",
        "lbl_cont_display_brightness",
        "_lbl_controller_intro",
    )

    def __init__(self, cfg: dict, parent=None):
        super().__init__(parent)
        self._cfg = cfg if isinstance(cfg, dict) else {}
        self._suppress = False
        self._build_ui()
        self.load_from_cfg()
        self.apply_enabled_ui()
        self.apply_usb_remote_ui()

    def set_cfg(self, cfg: dict) -> None:
        self._cfg = cfg if isinstance(cfg, dict) else {}

    def _build_ui(self) -> None:
        vl = QVBoxLayout(self)
        _pad = px_to_dip(self, 5)
        vl.setContentsMargins(_pad, _pad, _pad, _pad)
        vl.setSpacing(10)

        self._lbl_controller_intro = QLabel(t("settings.controller_intro"))
        self._lbl_controller_intro.setWordWrap(True)
        vl.addWidget(self._lbl_controller_intro)

        self.chk_hw_controller_enabled = QCheckBox(t("settings.controller_hw_enable"))
        self.chk_hw_controller_enabled.setToolTip(tt("settings.controller_hw_enable_tooltip"))
        self.chk_hw_controller_enabled.toggled.connect(self._on_enabled_toggled)
        vl.addWidget(self.chk_hw_controller_enabled)

        self.chk_cont_usb_remote = QCheckBox(t("settings.controller_usb_remote"))
        self.chk_cont_usb_remote.setToolTip(tt("settings.controller_usb_remote_tooltip"))
        self.chk_cont_usb_remote.toggled.connect(self._on_usb_remote_toggled)
        vl.addWidget(self.chk_cont_usb_remote)

        self.chk_cont_web = QCheckBox(t("settings.controller_web"))
        self.chk_cont_web.setToolTip(tt("settings.controller_web_tooltip"))
        self.chk_cont_web.toggled.connect(self._on_web_toggled)
        vl.addWidget(self.chk_cont_web)

        self._row_cont_usb_port = QWidget()
        _lay_usb = QHBoxLayout(self._row_cont_usb_port)
        _lay_usb.setContentsMargins(0, 0, 0, 0)
        _lay_usb.setSpacing(px_to_dip(self, 8))
        self.cb_cont_usb_com = QComboBox()
        self.cb_cont_usb_com.setToolTip(tt("settings.controller_usb_port_tooltip"))
        _lay_usb.addWidget(self.cb_cont_usb_com, 1)
        self.btn_cont_usb_refresh = QPushButton("↻")
        self.btn_cont_usb_refresh.setFixedWidth(30)
        self.btn_cont_usb_refresh.setToolTip(tt("settings.btn_com_refresh_tooltip"))
        self.btn_cont_usb_refresh.clicked.connect(lambda: self.refresh_usb_com(force=True))
        _lay_usb.addWidget(self.btn_cont_usb_refresh, 0)
        vl.addWidget(self._row_cont_usb_port)

        self.gb_controller = QGroupBox(t("settings.controller_group"))
        fl = QFormLayout(self.gb_controller)

        self.sp_controller_id = QSpinBox()
        self.sp_controller_id.setRange(0, 245)
        self.sp_controller_id.setToolTip(tt("settings.controller_id_tooltip"))
        _row_id = QWidget()
        _lay_id = QHBoxLayout(_row_id)
        _lay_id.setContentsMargins(0, 0, 0, 0)
        _lay_id.setSpacing(px_to_dip(self, 8))
        _lay_id.addWidget(self.sp_controller_id, 0)
        self.btn_setconidf = QPushButton(t("settings.controller_btn_setconidf"))
        self.btn_setconidf.setAutoDefault(False)
        self.btn_setconidf.setDefault(False)
        self.btn_setconidf.setToolTip(tt("settings.controller_setconidf_tooltip"))
        self.btn_setconidf.clicked.connect(self.setconidf_requested.emit)
        _lay_id.addWidget(self.btn_setconidf, 0)
        self._lbl_controller_led = QLabel()
        self._lbl_controller_led.setObjectName("controllerReadLed")
        self._lbl_controller_led.setToolTip(tt("settings.controller_led_tooltip"))
        _led = px_to_dip(self, 12)
        self._lbl_controller_led.setFixedSize(_led, _led)
        self.set_led_ok(False)
        _lay_id.addWidget(self._lbl_controller_led, 0, Qt.AlignmentFlag.AlignVCenter)
        self._lbl_controller_wait = QLabel(t("settings.controller_wait"))
        self._lbl_controller_wait.setVisible(False)
        self._lbl_controller_wait.setToolTip(tt("settings.controller_wait_tooltip"))
        _lay_id.addWidget(self._lbl_controller_wait, 0, Qt.AlignmentFlag.AlignVCenter)
        _lay_id.addStretch(1)
        fl.addRow(t("settings.controller_id"), _row_id)

        self.sp_cont_az_rotor_id = QSpinBox()
        self.sp_cont_az_rotor_id.setRange(0, 254)
        self.sp_cont_az_rotor_id.setToolTip(tt("settings.controller_az_rotor_id_tooltip"))
        self._lbl_cont_az_rotor_id = QLabel(t("settings.controller_az_rotor_id"))
        fl.addRow(self._lbl_cont_az_rotor_id, self.sp_cont_az_rotor_id)

        self.sp_cont_el_rotor_id = QSpinBox()
        self.sp_cont_el_rotor_id.setRange(0, 254)
        self.sp_cont_el_rotor_id.setToolTip(tt("settings.controller_el_rotor_id_tooltip"))
        self._lbl_cont_el_rotor_id = QLabel(t("settings.controller_el_rotor_id"))
        fl.addRow(self._lbl_cont_el_rotor_id, self.sp_cont_el_rotor_id)

        self.sp_cont_pwm_slow = QSpinBox()
        self.sp_cont_pwm_slow.setRange(0, 100)
        self.sp_cont_pwm_slow.setToolTip(tt("settings.controller_pwm_tooltip"))
        self.sp_cont_pwm_fast = QSpinBox()
        self.sp_cont_pwm_fast.setRange(0, 100)
        self.sp_cont_pwm_fast.setToolTip(tt("settings.controller_pwm_tooltip"))
        fl.addRow(t("settings.controller_pwm_slow"), self.sp_cont_pwm_slow)
        fl.addRow(t("settings.controller_pwm_fast"), self.sp_cont_pwm_fast)

        self.chk_cont_wind_anemo = QCheckBox(t("settings.controller_wind_anemo"))
        self.chk_cont_wind_anemo.setToolTip(tt("settings.controller_wind_anemo_tooltip"))
        fl.addRow(self.chk_cont_wind_anemo)

        self.cb_cont_encoder_delta = QComboBox()
        self.cb_cont_encoder_delta.addItem(t("settings.controller_encoder_delta_0_1"), 1)
        self.cb_cont_encoder_delta.addItem(t("settings.controller_encoder_delta_1"), 10)
        self.cb_cont_encoder_delta.setToolTip(tt("settings.controller_encoder_delta_tooltip"))
        fl.addRow(t("settings.controller_encoder_delta"), self.cb_cont_encoder_delta)

        self.sp_cont_beep_freq = QSpinBox()
        self.sp_cont_beep_freq.setRange(200, 4000)
        self.sp_cont_beep_freq.setToolTip(tt("settings.controller_beep_freq_tooltip"))
        self.sp_cont_beep_vol = QSpinBox()
        self.sp_cont_beep_vol.setRange(0, 50)
        self.sp_cont_beep_vol.setToolTip(tt("settings.controller_beep_volume_tooltip"))
        fl.addRow(t("settings.controller_beep_freq"), self.sp_cont_beep_freq)
        fl.addRow(t("settings.controller_beep_volume"), self.sp_cont_beep_vol)

        _w_conled = QWidget()
        _hl = QHBoxLayout(_w_conled)
        _hl.setContentsMargins(0, 0, 0, 0)
        self.sl_cont_display_brightness = QSlider(Qt.Orientation.Horizontal)
        self.sl_cont_display_brightness.setRange(0, 100)
        self.sl_cont_display_brightness.setToolTip(
            tt("settings.controller_display_brightness_tooltip")
        )
        self.lbl_cont_display_brightness = QLabel()
        self.lbl_cont_display_brightness.setMinimumWidth(40)
        self.sl_cont_display_brightness.valueChanged.connect(self._update_brightness_label)
        self.sl_cont_display_brightness.sliderReleased.connect(self._on_brightness_released)
        _hl.addWidget(self.sl_cont_display_brightness, 1)
        _hl.addWidget(self.lbl_cont_display_brightness)
        fl.addRow(t("settings.controller_display_brightness"), _w_conled)

        vl.addWidget(self.gb_controller)
        vl.addStretch(1)

    def hw_enabled(self) -> bool:
        return bool(self.chk_hw_controller_enabled.isChecked())

    def link_mode_from_ui(self) -> int:
        if self.chk_cont_web.isChecked():
            return 2
        if self.chk_cont_usb_remote.isChecked():
            return 1
        return 0

    def load_from_cfg(self) -> None:
        chw = self._cfg.get("controller_hw") if isinstance(self._cfg.get("controller_hw"), dict) else {}
        cl = self._cfg.get("controller_link") if isinstance(self._cfg.get("controller_link"), dict) else {}
        self._suppress = True
        try:
            self.chk_hw_controller_enabled.setChecked(bool(chw.get("enabled", True)))
            try:
                web_on = int(chw.get("link_mode", 0)) == 2
            except Exception:
                web_on = False
            usb_on = bool(chw.get("usb_remote", False)) and not web_on
            self.chk_cont_usb_remote.setChecked(usb_on)
            self.chk_cont_web.setChecked(web_on)
            try:
                self.sp_controller_id.setValue(int(chw.get("cont_id", 2)))
            except Exception:
                self.sp_controller_id.setValue(2)
            try:
                self.sp_cont_az_rotor_id.setValue(max(0, min(254, int(chw.get("az_rotor_id", 20)))))
            except Exception:
                self.sp_cont_az_rotor_id.setValue(20)
            try:
                self.sp_cont_el_rotor_id.setValue(max(0, min(254, int(chw.get("el_rotor_id", 0)))))
            except Exception:
                self.sp_cont_el_rotor_id.setValue(0)
            try:
                self.sp_cont_pwm_slow.setValue(int(chw.get("slow_pwm", 30)))
            except Exception:
                self.sp_cont_pwm_slow.setValue(30)
            try:
                self.sp_cont_pwm_fast.setValue(int(chw.get("fast_pwm", 80)))
            except Exception:
                self.sp_cont_pwm_fast.setValue(80)
            self.chk_cont_wind_anemo.setChecked(bool(chw.get("wind_anemometer", False)))
            try:
                ed = int(chw.get("encoder_delta", 10))
            except Exception:
                ed = 10
            if ed not in (1, 10):
                ed = 10
            self.cb_cont_encoder_delta.setCurrentIndex(0 if ed == 1 else 1)
            try:
                self.sp_cont_beep_freq.setValue(
                    max(200, min(4000, int(chw.get("speaker_freq_hz", 1000))))
                )
            except Exception:
                self.sp_cont_beep_freq.setValue(1000)
            try:
                self.sp_cont_beep_vol.setValue(max(0, min(50, int(chw.get("speaker_volume", 50)))))
            except Exception:
                self.sp_cont_beep_vol.setValue(50)
            try:
                db = int(chw.get("display_brightness_pct", 100))
            except Exception:
                db = 100
            self.sl_cont_display_brightness.setValue(max(0, min(100, db)))
            self._update_brightness_label()
            self.refresh_usb_com(select=str(cl.get("com_port", "") or ""), force=False)
        finally:
            self._suppress = False
        self.apply_enabled_ui()
        self.apply_usb_remote_ui()

    def apply_to_cfg(self, cfg: dict) -> None:
        chw = cfg.setdefault("controller_hw", {})
        chw["enabled"] = bool(self.chk_hw_controller_enabled.isChecked())
        chw["usb_remote"] = bool(self.chk_cont_usb_remote.isChecked())
        chw["link_mode"] = self.link_mode_from_ui()
        chw["cont_id"] = int(self.sp_controller_id.value())
        chw["cont_id_configured"] = True
        chw["az_rotor_id"] = int(self.sp_cont_az_rotor_id.value())
        chw["el_rotor_id"] = int(self.sp_cont_el_rotor_id.value())
        chw["slow_pwm"] = int(self.sp_cont_pwm_slow.value())
        chw["fast_pwm"] = int(self.sp_cont_pwm_fast.value())
        chw["wind_anemometer"] = bool(self.chk_cont_wind_anemo.isChecked())
        chw["encoder_delta"] = int(self.cb_cont_encoder_delta.currentData() or 10)
        chw["speaker_freq_hz"] = int(self.sp_cont_beep_freq.value())
        chw["speaker_volume"] = int(self.sp_cont_beep_vol.value())
        chw["display_brightness_pct"] = int(self.sl_cont_display_brightness.value())
        if self.chk_cont_usb_remote.isChecked() or self.chk_cont_web.isChecked():
            cl = cfg.setdefault("controller_link", {})
            cl["mode"] = "com"
            cl["com_port"] = str(
                self.cb_cont_usb_com.currentData() or self.cb_cont_usb_com.currentText() or ""
            )
            cl.setdefault("baudrate", 115200)

    def apply_enabled_ui(self) -> None:
        on = self.hw_enabled()
        self.chk_cont_usb_remote.setEnabled(on)
        self.chk_cont_web.setEnabled(on)
        self.gb_controller.setEnabled(on)
        self.apply_usb_remote_ui()

    def apply_usb_remote_ui(self) -> None:
        on = self.hw_enabled() and bool(self.chk_cont_usb_remote.isChecked())
        self._row_cont_usb_port.setVisible(on)
        self.cb_cont_usb_com.setEnabled(on)
        self.btn_cont_usb_refresh.setEnabled(on)

    def refresh_usb_com(self, *, select: str = "", force: bool = False) -> None:
        sel = select or str(self.cb_cont_usb_com.currentData() or "")
        entries = list_serial_port_entries(force=force)
        self.cb_cont_usb_com.clear()
        for item in entries or []:
            try:
                dev, tip = item[0], item[1] if len(item) > 1 else ""
            except Exception:
                continue
            if not dev:
                continue
            label = f"{dev} — {tip}" if tip else str(dev)
            if len(label) > 50:
                label = label[:49].rstrip() + "…"
            self.cb_cont_usb_com.addItem(label, str(dev))
            if tip:
                self.cb_cont_usb_com.setItemData(
                    self.cb_cont_usb_com.count() - 1,
                    f"{dev} — {tip}",
                    Qt.ItemDataRole.ToolTipRole,
                )
        if sel:
            for i in range(self.cb_cont_usb_com.count()):
                if self.cb_cont_usb_com.itemData(i) == sel:
                    self.cb_cont_usb_com.setCurrentIndex(i)
                    return
            self.cb_cont_usb_com.addItem(sel, sel)
            self.cb_cont_usb_com.setCurrentIndex(self.cb_cont_usb_com.count() - 1)

    def set_led_ok(self, ok: bool) -> None:
        color = "#2ecc71" if ok else "#95a5a6"
        self._lbl_controller_led.setStyleSheet(
            f"background:{color};border-radius:{max(2, px_to_dip(self, 6))}px;"
        )

    def set_wait_visible(self, visible: bool) -> None:
        self._lbl_controller_wait.setVisible(bool(visible))

    def _update_brightness_label(self, *_args: Any) -> None:
        self.lbl_cont_display_brightness.setText(f"{int(self.sl_cont_display_brightness.value())} %")

    def _on_brightness_released(self) -> None:
        if self._suppress:
            return
        self.brightness_released.emit(int(self.sl_cont_display_brightness.value()))

    def _on_enabled_toggled(self, checked: bool) -> None:
        if self._suppress:
            return
        self.apply_enabled_ui()
        self.enabled_toggled.emit(bool(checked))

    def _on_usb_remote_toggled(self, checked: bool) -> None:
        if self._suppress:
            return
        if checked and self.chk_cont_web.isChecked():
            self._suppress = True
            try:
                self.chk_cont_web.setChecked(False)
            finally:
                self._suppress = False
        self.apply_usb_remote_ui()
        self.usb_remote_toggled.emit(bool(checked))

    def _on_web_toggled(self, checked: bool) -> None:
        if self._suppress:
            return
        if checked and self.chk_cont_usb_remote.isChecked():
            self._suppress = True
            try:
                self.chk_cont_usb_remote.setChecked(False)
            finally:
                self._suppress = False
        self.apply_usb_remote_ui()
        self.web_toggled.emit(bool(checked))

    def install_aliases_on(self, host: Any) -> None:
        """SettingsWindow: self.chk_… = tab.chk_… für bestehenden Code."""
        for name in self.ALIAS_ATTRS:
            try:
                setattr(host, name, getattr(self, name))
            except Exception:
                pass
