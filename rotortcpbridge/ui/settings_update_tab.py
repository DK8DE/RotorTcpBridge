"""Einstellungen-Tab: Rotor SoftAP / Web-OTA und Versionsvergleich."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..i18n import t, tt
from ..version import (
    APP_VERSION,
    CONTROLLER_FW_VERSION,
    INCLINOMETER_FW_VERSION,
    ROTOR_FW_VERSION,
)
from .led_widget import Led
from .ui_utils import px_to_dip


def _status_style(kind: str) -> str:
    """kind: ok | update | unknown | offline | na"""
    colors = {
        "ok": "#1b7a3d",
        "update": "#b35c00",
        "unknown": "#666666",
        "offline": "#666666",
        "na": "#666666",
    }
    c = colors.get(kind, "#666666")
    return f"color: {c}; font-weight: bold;"


class UpdateTab(QWidget):
    """SoftAP pro aktiver Achse (AZ/EL), Webflasher und Versionsvergleich."""

    softap_toggled = Signal(str, bool)  # axis "az"|"el", want_on
    refresh_clicked = Signal()
    open_webflasher_clicked = Signal()
    open_download_page_clicked = Signal()
    versions_refresh_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._block_toggle: dict[str, bool] = {"az": False, "el": False, "inc": False}
        self._axis_visible = {"az": True, "el": False, "inc": False}
        self._ver_value_lbls: dict[str, QLabel] = {}
        self._ver_online_lbls: dict[str, QLabel] = {}
        self._ver_status_lbls: dict[str, QLabel] = {}
        self._ver_row_widgets: dict[str, list] = {}

        root = QVBoxLayout(self)
        root.setSpacing(px_to_dip(self, 10))

        # --- Versionsvergleich (wie Hilfe → Version + Online-Soll) -------------
        self._gb_fw = QGroupBox(t("settings.update_fw_bundle_group"))
        vl_fw = QVBoxLayout(self._gb_fw)
        vl_fw.setSpacing(px_to_dip(self, 8))
        self._lbl_fw_hint = QLabel(t("settings.update_fw_bundle_hint"))
        self._lbl_fw_hint.setWordWrap(True)
        vl_fw.addWidget(self._lbl_fw_hint)

        grid_w = QWidget()
        self._ver_grid = QGridLayout(grid_w)
        self._ver_grid.setContentsMargins(0, 0, 0, 0)
        self._ver_grid.setHorizontalSpacing(px_to_dip(self, 12))
        self._ver_grid.setVerticalSpacing(px_to_dip(self, 4))

        self._hdr_comp = QLabel(t("settings.update_ver_col_component"))
        self._hdr_cur = QLabel(t("settings.update_ver_col_current"))
        self._hdr_online = QLabel(t("settings.update_ver_col_online"))
        self._hdr_status = QLabel(t("settings.update_ver_col_status"))
        for h in (self._hdr_comp, self._hdr_cur, self._hdr_online, self._hdr_status):
            h.setStyleSheet("font-weight: bold;")
        self._ver_grid.addWidget(self._hdr_comp, 0, 0)
        self._ver_grid.addWidget(self._hdr_cur, 0, 1)
        self._ver_grid.addWidget(self._hdr_online, 0, 2)
        self._ver_grid.addWidget(self._hdr_status, 0, 3)

        self._ver_row_keys = ("app", "controller", "az", "el", "inc")
        self._ver_label_keys = {
            "app": "settings.update_ver_app",
            "controller": "about.lbl_controller",
            "az": "about.lbl_rotor_az",
            "el": "about.lbl_rotor_el",
            "inc": "settings.update_ver_inclinometer",
        }
        for i, key in enumerate(self._ver_row_keys, start=1):
            name = QLabel(t(self._ver_label_keys[key]))
            cur = QLabel("—")
            cur.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            online = QLabel("—")
            online.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            status = QLabel("—")
            self._ver_grid.addWidget(name, i, 0)
            self._ver_grid.addWidget(cur, i, 1)
            self._ver_grid.addWidget(online, i, 2)
            self._ver_grid.addWidget(status, i, 3)
            self._ver_value_lbls[key] = cur
            self._ver_online_lbls[key] = online
            self._ver_status_lbls[key] = status
            self._ver_row_widgets[key] = [name, cur, online, status]

        vl_fw.addWidget(grid_w)

        self._lbl_fw_extra = QLabel("")
        self._lbl_fw_extra.setWordWrap(True)
        self._lbl_fw_extra.setVisible(False)
        vl_fw.addWidget(self._lbl_fw_extra)

        btn_fw_row = QHBoxLayout()
        btn_fw_row.setSpacing(px_to_dip(self, 8))
        self.btn_ver_refresh = QPushButton(t("settings.update_ver_refresh"))
        self.btn_ver_refresh.setToolTip(tt("settings.update_ver_refresh_tooltip"))
        self.btn_ver_refresh.setAutoDefault(False)
        self.btn_ver_refresh.clicked.connect(self.versions_refresh_clicked.emit)
        btn_fw_row.addWidget(self.btn_ver_refresh, 0)
        self.btn_fw_download = QPushButton(t("settings.update_fw_open_download"))
        self.btn_fw_download.setToolTip(tt("settings.update_fw_open_download_tooltip"))
        self.btn_fw_download.setAutoDefault(False)
        self.btn_fw_download.clicked.connect(self.open_download_page_clicked.emit)
        btn_fw_row.addWidget(self.btn_fw_download, 0)
        btn_fw_row.addStretch(1)
        vl_fw.addLayout(btn_fw_row)
        root.addWidget(self._gb_fw)

        # Startwerte (App sofort, Rest nach Prüfung)
        self.set_version_row(
            "app",
            current=f"v{APP_VERSION}",
            online=t("settings.update_ver_checking"),
            status_key="unknown",
            status_text=t("settings.update_ver_status_checking"),
        )
        self.set_version_row(
            "controller",
            current=t("about.ver_na"),
            online=CONTROLLER_FW_VERSION,
            status_key="unknown",
            status_text=t("settings.update_ver_status_checking"),
        )
        self.set_version_row(
            "az",
            current=t("about.ver_na"),
            online=ROTOR_FW_VERSION,
            status_key="unknown",
            status_text=t("settings.update_ver_status_checking"),
        )
        self.set_version_row(
            "el",
            current=t("about.ver_na"),
            online=ROTOR_FW_VERSION,
            status_key="na",
            status_text=t("about.ver_na"),
        )
        self.set_version_row(
            "inc",
            current=t("about.ver_na"),
            online=INCLINOMETER_FW_VERSION,
            status_key="na",
            status_text=t("about.ver_na"),
            visible=False,
        )

        # --- Webserver Rotor --------------------------------------------------
        self._gb_softap = QGroupBox(t("settings.update_group_softap"))
        vl = QVBoxLayout(self._gb_softap)
        vl.setSpacing(px_to_dip(self, 8))

        self._lbl_intro = QLabel(t("settings.update_intro"))
        self._lbl_intro.setWordWrap(True)
        vl.addWidget(self._lbl_intro)

        led_d = max(8, px_to_dip(self, 12))
        self._axis_rows: dict[str, QWidget] = {}
        self._leds: dict[str, Led] = {}
        self._chks: dict[str, QCheckBox] = {}
        self._status_lbls: dict[str, QLabel] = {}

        # Eine Grid-Tabelle: LED | Checkbox | Status — gleiche Spalten für AZ/EL/Inc
        softap_grid_w = QWidget(self)
        softap_grid = QGridLayout(softap_grid_w)
        softap_grid.setContentsMargins(0, 0, 0, 0)
        softap_grid.setHorizontalSpacing(px_to_dip(self, 8))
        softap_grid.setVerticalSpacing(px_to_dip(self, 6))
        softap_grid.setColumnStretch(1, 1)
        softap_grid.setColumnStretch(2, 0)

        for row_i, (axis, enable_key, tip_key) in enumerate(
            (
                ("az", "settings.update_softap_enable_az", "settings.update_softap_enable_az_tooltip"),
                ("el", "settings.update_softap_enable_el", "settings.update_softap_enable_el_tooltip"),
                ("inc", "settings.update_softap_enable_inc", "settings.update_softap_enable_inc_tooltip"),
            )
        ):
            led = Led(led_d, self)
            tip_led = (
                "settings.update_softap_led_inc_tooltip"
                if axis == "inc"
                else "settings.update_softap_led_tooltip"
            )
            led.setToolTip(tt(tip_led))
            led.set_state(False)
            chk = QCheckBox(t(enable_key))
            chk.setToolTip(tt(tip_key))
            chk.toggled.connect(lambda checked, a=axis: self._on_chk_toggled(a, checked))
            status = QLabel("")
            status.setAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            status.setMinimumWidth(px_to_dip(self, 140))
            softap_grid.addWidget(led, row_i, 0, Qt.AlignmentFlag.AlignVCenter)
            softap_grid.addWidget(chk, row_i, 1, Qt.AlignmentFlag.AlignVCenter)
            softap_grid.addWidget(status, row_i, 2, Qt.AlignmentFlag.AlignVCenter)
            # Sichtbarkeit: ganze Zeile über LED/Chk/Status (kein Row-Widget)
            self._axis_rows[axis] = (led, chk, status)
            self._leds[axis] = led
            self._chks[axis] = chk
            self._status_lbls[axis] = status
            if axis == "inc":
                for w in (led, chk, status):
                    w.setVisible(False)
        vl.addWidget(softap_grid_w)

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

        self._lbl_inc_details = QLabel(t("settings.update_softap_inc_details"))
        self._lbl_inc_details.setWordWrap(True)
        self._lbl_inc_details.setVisible(False)
        vl.addWidget(self._lbl_inc_details)

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
        self.set_axes_visible(az_on=True, el_on=False, inc_on=False)

    def set_version_row(
        self,
        key: str,
        *,
        current: str,
        online: str,
        status_key: str,
        status_text: str,
        visible: Optional[bool] = None,
    ) -> None:
        cur = self._ver_value_lbls.get(key)
        onl = self._ver_online_lbls.get(key)
        st = self._ver_status_lbls.get(key)
        if cur is not None:
            cur.setText(str(current or "—"))
        if onl is not None:
            onl.setText(str(online or "—"))
        if st is not None:
            st.setText(str(status_text or "—"))
            st.setStyleSheet(_status_style(status_key))
        if visible is not None:
            for w in self._ver_row_widgets.get(key, []):
                w.setVisible(bool(visible))

    def set_fw_extra_info(self, text: str) -> None:
        self._lbl_fw_extra.setText(str(text or ""))
        self._lbl_fw_extra.setVisible(bool(str(text or "").strip()))

    # Rückwärtskompatibilität für ältere Aufrufer
    def set_fw_rotor_status(self, text: str) -> None:
        if str(text or "").strip():
            self.set_fw_extra_info(text)

    def set_fw_info_status(self, text: str) -> None:
        self.set_fw_extra_info(text)

    def _on_chk_toggled(self, axis: str, checked: bool) -> None:
        if self._block_toggle.get(axis):
            return
        self.softap_toggled.emit(str(axis), bool(checked))

    def set_axes_visible(
        self, *, az_on: bool, el_on: bool, inc_on: bool | None = None
    ) -> None:
        """Zeilen nur für aktivierte Achsen / vorhandenes Inklinometer anzeigen."""
        self._axis_visible["az"] = bool(az_on)
        self._axis_visible["el"] = bool(el_on)
        if inc_on is not None:
            self._axis_visible["inc"] = bool(inc_on)
        for axis, cells in self._axis_rows.items():
            show = bool(self._axis_visible.get(axis))
            if isinstance(cells, (tuple, list)):
                for w in cells:
                    w.setVisible(show)
            else:
                cells.setVisible(show)
        both = bool(az_on) and bool(el_on)
        self._lbl_both_hint.setVisible(both)
        self._lbl_inc_details.setVisible(bool(self._axis_visible.get("inc")))
        for w in self._ver_row_widgets.get("az", []):
            w.setVisible(bool(az_on))
        for w in self._ver_row_widgets.get("el", []):
            w.setVisible(bool(el_on))

    def set_softap_active(self, axis: str, active: bool | None) -> None:
        """LED + Checkbox einer Achse/Inklinometer an SoftAP-Status anpassen (ohne Signal)."""
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
        self._chks["inc"].setText(t("settings.update_softap_enable_inc"))
        self._chks["inc"].setToolTip(tt("settings.update_softap_enable_inc_tooltip"))
        self._leds["az"].setToolTip(tt("settings.update_softap_led_tooltip"))
        self._leds["el"].setToolTip(tt("settings.update_softap_led_tooltip"))
        self._leds["inc"].setToolTip(tt("settings.update_softap_led_inc_tooltip"))
        self.btn_refresh.setText(t("settings.update_btn_refresh"))
        self.btn_refresh.setToolTip(tt("settings.update_btn_refresh_tooltip"))
        self._lbl_details.setText(t("settings.update_softap_details"))
        self._lbl_inc_details.setText(t("settings.update_softap_inc_details"))
        self._lbl_both_hint.setText(t("settings.update_softap_both_hint"))
        self._lbl_reboot_hint.setText(t("settings.update_reboot_hint"))
        self.btn_open.setText(t("settings.update_btn_open_webflasher"))
        self.btn_open.setToolTip(tt("settings.update_btn_open_webflasher_tooltip"))
        self._gb_fw.setTitle(t("settings.update_fw_bundle_group"))
        self._lbl_fw_hint.setText(t("settings.update_fw_bundle_hint"))
        self.btn_fw_download.setText(t("settings.update_fw_open_download"))
        self.btn_fw_download.setToolTip(tt("settings.update_fw_open_download_tooltip"))
        self.btn_ver_refresh.setText(t("settings.update_ver_refresh"))
        self.btn_ver_refresh.setToolTip(tt("settings.update_ver_refresh_tooltip"))
        self._hdr_comp.setText(t("settings.update_ver_col_component"))
        self._hdr_cur.setText(t("settings.update_ver_col_current"))
        self._hdr_online.setText(t("settings.update_ver_col_online"))
        self._hdr_status.setText(t("settings.update_ver_col_status"))
        for key, lbl_key in self._ver_label_keys.items():
            widgets = self._ver_row_widgets.get(key) or []
            if widgets:
                widgets[0].setText(t(lbl_key))
