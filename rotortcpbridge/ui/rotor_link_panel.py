"""Gemeinsames Panel: Rotorverbindung (hardware_link + AZ/EL-IPs) für Settings und Inbetriebnahme."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..axis_link import (
    axis_endpoint_from_cfg,
    ensure_setup_axis_endpoints,
    store_axis_endpoint,
    sync_hardware_link_from_az,
)
from ..i18n import t, tt
from ..ports import cached_serial_port_entries, list_serial_port_entries
from .ui_utils import px_to_dip


class RotorLinkPanel(QWidget):
    """Bearbeitet ``cfg['hardware_link']`` und ``ui.setup_axis_endpoints`` (AZ/EL getrennt)."""

    refresh_com_requested = Signal()

    def __init__(self, cfg: dict, parent=None, *, ip_min_width: int | None = None):
        super().__init__(parent)
        self._cfg = cfg if isinstance(cfg, dict) else {}
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        form = QFormLayout()
        self._form = form
        _ip_w = int(ip_min_width) if ip_min_width is not None else px_to_dip(self, 140)

        self.cb_hw_mode = QComboBox()
        self.cb_hw_mode.addItem("TCP", "tcp")
        self.cb_hw_mode.addItem("UDP", "udp")
        self.cb_hw_mode.addItem("COM", "com")
        self.cb_hw_mode.setToolTip(tt("settings.hw_mode_tooltip"))

        self.ed_hw_ip_az = QLineEdit()
        self.ed_hw_ip_az.setMinimumWidth(_ip_w)
        self.ed_hw_ip_az.setToolTip(tt("settings.hw_ip_az_tooltip"))
        self.sp_hw_port_az = QSpinBox()
        self.sp_hw_port_az.setRange(1, 65535)
        self.sp_hw_port_az.setToolTip(tt("settings.hw_port_az_tooltip"))

        self.ed_hw_ip_el = QLineEdit()
        self.ed_hw_ip_el.setMinimumWidth(_ip_w)
        self.ed_hw_ip_el.setToolTip(tt("settings.hw_ip_el_tooltip"))
        self.sp_hw_port_el = QSpinBox()
        self.sp_hw_port_el.setRange(1, 65535)
        self.sp_hw_port_el.setToolTip(tt("settings.hw_port_el_tooltip"))

        # Alias für bestehenden Settings-Code (ed_hw_ip / sp_hw_port = AZ)
        self.ed_hw_ip = self.ed_hw_ip_az
        self.sp_hw_port = self.sp_hw_port_az

        self.sp_hw_udp_bind_port = QSpinBox()
        self.sp_hw_udp_bind_port.setRange(0, 65535)
        self.sp_hw_udp_bind_port.setToolTip(tt("settings.hw_udp_bind_port_tooltip"))

        self.cb_hw_com = QComboBox()
        self.cb_hw_com.setToolTip(tt("settings.hw_com_tooltip"))
        self.btn_com_refresh = QPushButton("↻")
        self.btn_com_refresh.setFixedWidth(30)
        self.btn_com_refresh.setToolTip(tt("settings.btn_com_refresh_tooltip"))
        self.btn_com_refresh.clicked.connect(self._on_refresh_clicked)
        com_row = QHBoxLayout()
        com_row.setContentsMargins(0, 0, 0, 0)
        com_row.addWidget(self.cb_hw_com, 1)
        com_row.addWidget(self.btn_com_refresh)
        self.com_row_widget = QWidget()
        self.com_row_widget.setLayout(com_row)

        form.addRow(t("settings.hw_mode"), self.cb_hw_mode)
        form.addRow(t("settings.hw_ip_az"), self.ed_hw_ip_az)
        form.addRow(t("settings.hw_port_az"), self.sp_hw_port_az)
        form.addRow(t("settings.hw_ip_el"), self.ed_hw_ip_el)
        form.addRow(t("settings.hw_port_el"), self.sp_hw_port_el)
        form.addRow(t("settings.hw_udp_bind_port"), self.sp_hw_udp_bind_port)
        form.addRow(t("settings.hw_com"), self.com_row_widget)
        root.addLayout(form)

        self._el_fields_forced = True  # EL-IP immer anzeigen im TCP-Modus (auch wenn EL noch aus)
        self.cb_hw_mode.currentIndexChanged.connect(lambda _i: self.sync_mode_rows())
        self.load_from_cfg()
        self.sync_mode_rows()

    def set_cfg(self, cfg: dict) -> None:
        self._cfg = cfg if isinstance(cfg, dict) else {}

    def set_el_endpoint_visible(self, visible: bool) -> None:
        """Optional: EL-IP/Port ausblenden wenn EL-Achse deaktiviert."""
        self._el_fields_forced = bool(visible)
        self.sync_mode_rows()

    def load_from_cfg(self) -> None:
        ensure_setup_axis_endpoints(self._cfg)
        hl = (
            self._cfg.get("hardware_link")
            if isinstance(self._cfg.get("hardware_link"), dict)
            else {}
        )
        mode = str(hl.get("mode", "tcp") or "tcp").strip().lower()
        idx = self.cb_hw_mode.findData(mode)
        self.cb_hw_mode.setCurrentIndex(idx if idx >= 0 else 0)
        az_h, az_p = axis_endpoint_from_cfg(self._cfg, "az")
        el_h, el_p = axis_endpoint_from_cfg(self._cfg, "el")
        self.ed_hw_ip_az.setText(az_h or str(hl.get("tcp_ip", "") or ""))
        try:
            self.sp_hw_port_az.setValue(int(az_p or hl.get("tcp_port", 8886) or 8886))
        except Exception:
            self.sp_hw_port_az.setValue(8886)
        self.ed_hw_ip_el.setText(el_h or az_h or "")
        try:
            self.sp_hw_port_el.setValue(int(el_p or az_p or 8886))
        except Exception:
            self.sp_hw_port_el.setValue(8886)
        try:
            self.sp_hw_udp_bind_port.setValue(int(hl.get("udp_bind_port", 0) or 0))
        except Exception:
            self.sp_hw_udp_bind_port.setValue(0)
        self.refresh_com_ports(select=str(hl.get("com_port", "") or ""), force=False)
        self.sync_mode_rows()

    def apply_to_cfg(self, cfg: dict) -> None:
        hl = cfg.setdefault("hardware_link", {})
        if not isinstance(hl, dict):
            cfg["hardware_link"] = {}
            hl = cfg["hardware_link"]
        mode = str(self.cb_hw_mode.currentData() or "tcp").strip().lower()
        hl["mode"] = mode
        hl["udp_bind_port"] = int(self.sp_hw_udp_bind_port.value())
        hl["com_port"] = str(self.cb_hw_com.currentData() or self.cb_hw_com.currentText() or "")
        az_host = self.ed_hw_ip_az.text().strip()
        az_port = int(self.sp_hw_port_az.value())
        el_host = self.ed_hw_ip_el.text().strip() or az_host
        el_port = int(self.sp_hw_port_el.value()) if self.ed_hw_ip_el.text().strip() else az_port
        if mode in ("tcp", "udp"):
            store_axis_endpoint(cfg, "az", az_host, az_port)
            store_axis_endpoint(cfg, "el", el_host, el_port)
            sync_hardware_link_from_az(cfg)
        else:
            # COM: gemeinsame Seriell-Verbindung — Endpunkte leer lassen / aus HW ableiten
            hl["tcp_ip"] = az_host
            hl["tcp_port"] = az_port

    def sync_mode_rows(self) -> None:
        mode = str(self.cb_hw_mode.currentData() or "tcp").strip().lower()
        show_tcp = mode in ("tcp", "udp")
        show_com = mode == "com"
        show_udp = mode == "udp"
        show_el = show_tcp and bool(getattr(self, "_el_fields_forced", True))
        for field, show in (
            (self.ed_hw_ip_az, show_tcp),
            (self.sp_hw_port_az, show_tcp),
            (self.ed_hw_ip_el, show_el),
            (self.sp_hw_port_el, show_el),
            (self.sp_hw_udp_bind_port, show_udp),
            (self.com_row_widget, show_com),
        ):
            field.setVisible(show)
            lbl = self._form.labelForField(field)
            if lbl is not None:
                lbl.setVisible(show)

    def set_tcp_endpoint(self, host: str, port: int, *, axis: str = "az") -> None:
        """LAN-Modul → TCP-Felder für AZ oder EL."""
        host = str(host or "").strip()
        try:
            port_i = int(port)
        except Exception:
            port_i = 8886
        if not host:
            return
        idx = self.cb_hw_mode.findData("tcp")
        if idx >= 0:
            self.cb_hw_mode.setCurrentIndex(idx)
        axis = str(axis or "az").strip().lower()
        if axis == "el":
            self.ed_hw_ip_el.setText(host)
            self.sp_hw_port_el.setValue(max(1, min(65535, port_i)))
        else:
            self.ed_hw_ip_az.setText(host)
            self.sp_hw_port_az.setValue(max(1, min(65535, port_i)))
            # Wenn EL noch leer: mit AZ vorbelegen
            if not self.ed_hw_ip_el.text().strip():
                self.ed_hw_ip_el.setText(host)
                self.sp_hw_port_el.setValue(max(1, min(65535, port_i)))
        self.sync_mode_rows()

    def _on_refresh_clicked(self) -> None:
        self.refresh_com_ports(force=True)
        self.refresh_com_requested.emit()

    def refresh_com_ports(self, *, select: str = "", force: bool = False) -> None:
        sel = select or str(self.cb_hw_com.currentData() or "")
        if not force:
            cached = cached_serial_port_entries()
            entries = cached if cached is not None else list_serial_port_entries(force=False)
        else:
            entries = list_serial_port_entries(force=True)
        self.cb_hw_com.clear()
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
            self.cb_hw_com.addItem(label, str(dev))
            if tip:
                from PySide6.QtCore import Qt

                self.cb_hw_com.setItemData(
                    self.cb_hw_com.count() - 1,
                    f"{dev} — {tip}",
                    Qt.ItemDataRole.ToolTipRole,
                )
        if sel:
            for i in range(self.cb_hw_com.count()):
                if self.cb_hw_com.itemData(i) == sel:
                    self.cb_hw_com.setCurrentIndex(i)
                    return
            self.cb_hw_com.addItem(sel, sel)
            self.cb_hw_com.setCurrentIndex(self.cb_hw_com.count() - 1)
