"""Einstellungen-Tab: Wetter-Schwellen (Außentemperatur, Wind)."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..i18n import t, tt


class WeatherThresholdsTab(QWidget):
    """Bearbeitet ``ui.weather_alert_thresholds``."""

    def __init__(self, cfg: dict, parent=None):
        super().__init__(parent)
        self._cfg = cfg
        root = QVBoxLayout(self)
        self._lbl_intro = QLabel(t("settings.weather_intro"))
        self._lbl_intro.setWordWrap(True)
        root.addWidget(self._lbl_intro)

        self._gb_temp = QGroupBox(t("settings.weather_group_temp"))
        ft = QFormLayout(self._gb_temp)
        self.chk_temp = QCheckBox(t("settings.weather_temp_enable"))
        self.chk_temp.setToolTip(tt("settings.weather_temp_enable_tooltip"))
        ft.addRow(self.chk_temp)
        self.sp_temp_min = QDoubleSpinBox()
        self.sp_temp_min.setRange(-80.0, 80.0)
        self.sp_temp_min.setDecimals(1)
        self.sp_temp_min.setSuffix(" °C")
        self.sp_temp_min.setToolTip(tt("settings.weather_temp_min_tooltip"))
        self._lbl_temp_min = QLabel(t("settings.weather_temp_min"))
        ft.addRow(self._lbl_temp_min, self.sp_temp_min)
        self.sp_temp_max = QDoubleSpinBox()
        self.sp_temp_max.setRange(-80.0, 80.0)
        self.sp_temp_max.setDecimals(1)
        self.sp_temp_max.setSuffix(" °C")
        self.sp_temp_max.setToolTip(tt("settings.weather_temp_max_tooltip"))
        self._lbl_temp_max = QLabel(t("settings.weather_temp_max"))
        ft.addRow(self._lbl_temp_max, self.sp_temp_max)
        root.addWidget(self._gb_temp)

        self._gb_wind = QGroupBox(t("settings.weather_group_wind"))
        fw = QFormLayout(self._gb_wind)
        self.chk_wind = QCheckBox(t("settings.weather_wind_enable"))
        self.chk_wind.setToolTip(tt("settings.weather_wind_enable_tooltip"))
        fw.addRow(self.chk_wind)
        self.sp_wind_max = QDoubleSpinBox()
        self.sp_wind_max.setRange(0.0, 300.0)
        self.sp_wind_max.setDecimals(1)
        self.sp_wind_max.setSuffix(" km/h")
        self.sp_wind_max.setToolTip(tt("settings.weather_wind_max_tooltip"))
        self._lbl_wind_max = QLabel(t("settings.weather_wind_max"))
        fw.addRow(self._lbl_wind_max, self.sp_wind_max)
        root.addWidget(self._gb_wind)

        root.addStretch(1)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.load_from_cfg()

    def load_from_cfg(self) -> None:
        wa = (self._cfg.get("ui") or {}).get("weather_alert_thresholds") or {}
        if not isinstance(wa, dict):
            wa = {}
        self.chk_temp.setChecked(bool(wa.get("temp_alert_enabled", False)))
        self.chk_wind.setChecked(bool(wa.get("wind_alert_enabled", False)))
        try:
            self.sp_temp_min.setValue(float(wa.get("temp_min_c", -15.0)))
        except (TypeError, ValueError):
            self.sp_temp_min.setValue(-15.0)
        try:
            self.sp_temp_max.setValue(float(wa.get("temp_max_c", 40.0)))
        except (TypeError, ValueError):
            self.sp_temp_max.setValue(40.0)
        try:
            self.sp_wind_max.setValue(float(wa.get("wind_max_kmh", 72.0)))
        except (TypeError, ValueError):
            self.sp_wind_max.setValue(72.0)

    def apply_to_cfg(self, cfg: dict) -> None:
        wa = cfg.setdefault("ui", {}).setdefault("weather_alert_thresholds", {})
        wa["temp_alert_enabled"] = bool(self.chk_temp.isChecked())
        wa["wind_alert_enabled"] = bool(self.chk_wind.isChecked())
        tmin = float(self.sp_temp_min.value())
        tmax = float(self.sp_temp_max.value())
        if tmin > tmax:
            tmin, tmax = tmax, tmin
        wa["temp_min_c"] = tmin
        wa["temp_max_c"] = tmax
        wa["wind_max_kmh"] = max(0.0, float(self.sp_wind_max.value()))

    def retranslate(self) -> None:
        self._lbl_intro.setText(t("settings.weather_intro"))
        self._gb_temp.setTitle(t("settings.weather_group_temp"))
        self._gb_wind.setTitle(t("settings.weather_group_wind"))
        self._lbl_temp_min.setText(t("settings.weather_temp_min"))
        self._lbl_temp_max.setText(t("settings.weather_temp_max"))
        self._lbl_wind_max.setText(t("settings.weather_wind_max"))
        self.chk_temp.setText(t("settings.weather_temp_enable"))
        self.chk_temp.setToolTip(tt("settings.weather_temp_enable_tooltip"))
        self.sp_temp_min.setToolTip(tt("settings.weather_temp_min_tooltip"))
        self.sp_temp_max.setToolTip(tt("settings.weather_temp_max_tooltip"))
        self.chk_wind.setText(t("settings.weather_wind_enable"))
        self.chk_wind.setToolTip(tt("settings.weather_wind_enable_tooltip"))
        self.sp_wind_max.setToolTip(tt("settings.weather_wind_max_tooltip"))
