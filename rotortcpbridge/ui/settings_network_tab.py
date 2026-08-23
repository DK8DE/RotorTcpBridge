"""Einstellungen-Tab: Netzwerk-Module (RS485-Konverter LAN/WLAN)."""

from __future__ import annotations

import ipaddress
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..i18n import t, tt
from ..network_modules import (
    DEFAULT_AT_PORTS,
    DEFAULT_CONFIG_PORTS,
    DEFAULT_WEB_PORTS,
    VENDOR_DK8DE,
    VENDOR_GENERIC,
    VENDOR_NA11X,
    VENDOR_NE2,
    VENDOR_USR,
    EbyteDevice,
    NetworkModule,
    ebyte_set_network,
    ebyte_udp_discover,
    modules_from_cfg,
    modules_to_cfg,
    probe_module,
    read_status,
    status_has_data,
    write_config,
)
from ..dk8de_wlan_module import (
    DK8DE_INFO_STAT_KEYS,
    DK8DE_STATUS_STAT_KEYS,
    DK8DE_STATUS_UI_SECTIONS,
    Dk8deDevice,
    dk8de_discover,
    dk8de_netmode_to_sock,
    parse_dk8de_stats_payload,
    read_dk8de_statistics,
    write_wan_dk8de,
)
from .led_widget import Led
from .ui_utils import px_to_dip


class _ProbeWorker(QThread):
    """Prueft Online-Status aller Module im Hintergrund."""

    # List[Tuple[NetworkModule, bool]] -- Modul-Objekt + Ergebnis, damit die
    # Zuordnung auch dann stimmt, wenn sich die Modulliste (loeschen/
    # hinzufuegen) waehrend der laufenden Pruefung veraendert.
    results = Signal(object)

    def __init__(self, modules: List[NetworkModule], parent=None):
        super().__init__(parent)
        self._modules = list(modules)

    def run(self) -> None:
        out: List[Tuple[NetworkModule, bool]] = []
        for m in self._modules:
            try:
                out.append((m, probe_module(m, timeout=0.6)))
            except Exception:
                out.append((m, False))
        self.results.emit(out)


class _ReadWorker(QThread):
    finished_ok = Signal(object)  # dict status
    finished_err = Signal(str)

    def __init__(self, module: NetworkModule, parent=None, *, at_host: str = ""):
        super().__init__(parent)
        self._module = module
        self._at_host = str(at_host or "").strip()

    def run(self) -> None:
        try:
            st = read_status(self._module, at_host=self._at_host)
            if not status_has_data(st):
                self.finished_err.emit(
                    str(st.get("error") or "Keine Konfigurationsdaten empfangen")
                )
            else:
                self.finished_ok.emit(st)
        except Exception as exc:
            self.finished_err.emit(str(exc))


class _WriteWorker(QThread):
    finished_ok = Signal(object)
    finished_err = Signal(str)

    def __init__(
        self,
        module: NetworkModule,
        wan: Dict[str, Any],
        sock: Dict[str, Any],
        parent=None,
        *,
        at_host: str = "",
    ):
        super().__init__(parent)
        self._module = module
        self._wan = wan
        self._sock = sock
        self._at_host = str(at_host or "").strip()

    def run(self) -> None:
        try:
            res = write_config(
                self._module,
                self._wan,
                self._sock,
                reboot=True,
                at_host=self._at_host,
            )
            if not res.get("ok"):
                self.finished_err.emit(str(res.get("error") or "write failed"))
            else:
                self.finished_ok.emit(res)
        except Exception as exc:
            self.finished_err.emit(str(exc))


class _ScanWorker(QThread):
    """Ebyte-UDP (1901/1902) + DK8DE-UDP (8880) Broadcast-Suche."""

    finished_ok = Signal(object)  # dict ebyte/dk8de
    finished_err = Signal(str)

    def __init__(self, timeout: float = 2.0, parent=None):
        super().__init__(parent)
        self._timeout = float(timeout)

    def run(self) -> None:
        try:
            ebyte = ebyte_udp_discover(timeout=self._timeout, read_pages=True)
            dk8de = dk8de_discover(timeout=self._timeout, http_fallback=True)
            self.finished_ok.emit({"ebyte": ebyte, "dk8de": dk8de})
        except Exception as exc:
            self.finished_err.emit(str(exc))


class _Dk8deStatsWorker(QThread):
    finished_ok = Signal(object)
    finished_err = Signal(str)

    def __init__(self, module: NetworkModule, parent=None):
        super().__init__(parent)
        self._module = module

    def run(self) -> None:
        try:
            st = read_dk8de_statistics(
                self._module.host,
                self._module.uid,
                config_port=int(self._module.config_port or 8880),
                web_port=int(self._module.web_port or 80),
                web_user=str(self._module.web_user or "admin"),
                web_password=str(self._module.web_password or "Rotorconfig"),
                timeout=3.0,
            )
            if st.get("error"):
                self.finished_err.emit(str(st["error"]))
            else:
                self.finished_ok.emit(st)
        except Exception as exc:
            self.finished_err.emit(str(exc))


class _SetIpWorker(QThread):
    finished_ok = Signal(object)
    finished_err = Signal(str)

    def __init__(
        self,
        device: EbyteDevice,
        ip: str,
        mask: str,
        gateway: str,
        dns: str,
        dns2: str,
        parent=None,
    ):
        super().__init__(parent)
        self._device = device
        self._ip = ip
        self._mask = mask
        self._gateway = gateway
        self._dns = dns
        self._dns2 = dns2

    def run(self) -> None:
        try:
            res = ebyte_set_network(
                self._device.mac,
                ip=self._ip,
                mask=self._mask,
                gateway=self._gateway,
                dns=self._dns,
                dns2=self._dns2,
                vendor=self._device.vendor,
                pages=self._device.pages or None,
            )
            if not res.get("ok"):
                self.finished_err.emit(str(res.get("error") or "UDP write failed"))
            else:
                self.finished_ok.emit(res)
        except Exception as exc:
            self.finished_err.emit(str(exc))


class _Dk8deSetIpWorker(QThread):
    finished_ok = Signal(object)
    finished_err = Signal(str)

    def __init__(
        self,
        device: Dk8deDevice,
        ip: str,
        mask: str,
        gateway: str,
        dns: str,
        parent=None,
    ):
        super().__init__(parent)
        self._device = device
        self._ip = ip
        self._mask = mask
        self._gateway = gateway
        self._dns = dns

    def run(self) -> None:
        host = str(
            self._device.info.get("CONTACT_IP") or self._device.ip or ""
        ).strip()
        if not host or host in ("0.0.0.0", "-"):
            host = "255.255.255.255"
        uid = self._device.uid_norm
        try:
            res = write_wan_dk8de(
                host,
                uid,
                ip=self._ip,
                mask=self._mask,
                gateway=self._gateway,
                dns=self._dns,
                dhcp=False,
                reboot=True,
                timeout=12.0,
            )
            if not res.get("ok"):
                self.finished_err.emit(str(res.get("error") or "AT write failed"))
            else:
                self.finished_ok.emit(res)
        except Exception as exc:
            self.finished_err.emit(str(exc))


def _ip_in_subnet(ip: str, mask: str, other: str) -> Optional[bool]:
    """True/False ob ``other`` im selben Subnetz wie ``ip``/``mask`` liegt; None falls ungueltig."""
    try:
        net = ipaddress.ip_network(f"{ip}/{mask}", strict=False)
        return ipaddress.ip_address(other) in net
    except ValueError:
        return None


class _EbyteSetIpDialog(QDialog):
    """IP/Maske/Gateway/DNS per Ebyte-UDP setzen."""

    def __init__(self, device: EbyteDevice, parent=None):
        super().__init__(parent)
        self.setWindowTitle(t("settings.network_discover_set_ip_title"))
        self._device = device
        lay = QVBoxLayout(self)
        lay.addWidget(
            QLabel(
                t(
                    "settings.network_discover_set_ip_intro",
                    model=device.model or "?",
                    mac=device.mac_str,
                )
            )
        )
        form = QFormLayout()
        self.ed_ip = QLineEdit(device.ip or "")
        self.ed_mask = QLineEdit(device.mask or "255.255.255.0")
        self.ed_gw = QLineEdit(device.gateway or "")
        self.ed_dns = QLineEdit(device.dns or "")
        self.ed_dns2 = QLineEdit(device.dns2 or "")
        form.addRow(t("settings.network_ip"), self.ed_ip)
        form.addRow(t("settings.network_mask"), self.ed_mask)
        form.addRow(t("settings.network_gateway"), self.ed_gw)
        form.addRow(t("settings.network_dns"), self.ed_dns)
        form.addRow(t("settings.network_dns2"), self.ed_dns2)
        lay.addLayout(form)
        hint = QLabel(t("settings.network_discover_set_ip_hint"))
        hint.setWordWrap(True)
        lay.addWidget(hint)
        self._lbl_gw_warn = QLabel("")
        self._lbl_gw_warn.setWordWrap(True)
        self._lbl_gw_warn.setStyleSheet("color: #d9822b; font-weight: bold;")
        lay.addWidget(self._lbl_gw_warn)
        self.ed_ip.textChanged.connect(self._check_gateway_subnet)
        self.ed_mask.textChanged.connect(self._check_gateway_subnet)
        self.ed_gw.textChanged.connect(self._check_gateway_subnet)
        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._btn_ok = bb.button(QDialogButtonBox.StandardButton.Ok)
        bb.accepted.connect(self._on_accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self._check_gateway_subnet()

    def _check_gateway_subnet(self) -> None:
        ip = self.ed_ip.text().strip()
        mask = self.ed_mask.text().strip()
        gw = self.ed_gw.text().strip()
        if not (ip and mask and gw):
            self._lbl_gw_warn.setText("")
            return
        ok = _ip_in_subnet(ip, mask, gw)
        if ok is False:
            self._lbl_gw_warn.setText(t("settings.network_discover_gw_subnet_warn"))
        else:
            self._lbl_gw_warn.setText("")

    def _on_accept(self) -> None:
        vals = self.values()
        if vals["ip"] and vals["mask"] and vals["gateway"]:
            if _ip_in_subnet(vals["ip"], vals["mask"], vals["gateway"]) is False:
                resp = QMessageBox.warning(
                    self,
                    t("settings.network_discover_title"),
                    t("settings.network_discover_gw_subnet_warn_confirm"),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if resp != QMessageBox.StandardButton.Yes:
                    return
        self.accept()

    def values(self) -> Dict[str, str]:
        return {
            "ip": self.ed_ip.text().strip(),
            "mask": self.ed_mask.text().strip(),
            "gateway": self.ed_gw.text().strip(),
            "dns": self.ed_dns.text().strip(),
            "dns2": self.ed_dns2.text().strip(),
        }


class _Dk8deSetIpDialog(QDialog):
    """IP/Maske/Gateway/DNS per DK8DE-AT (UDP 8880) setzen."""

    def __init__(self, device: Dk8deDevice, parent=None):
        super().__init__(parent)
        self.setWindowTitle(t("settings.network_discover_set_ip_title_dk8de"))
        self._device = device
        lay = QVBoxLayout(self)
        lay.addWidget(
            QLabel(
                t(
                    "settings.network_discover_set_ip_intro_dk8de",
                    name=(device.name or device.uid_norm or "?"),
                    uid=(device.uid_norm or "?"),
                    mac=(device.mac or "?"),
                )
            )
        )
        form = QFormLayout()
        self.ed_ip = QLineEdit(device.ip or "")
        reported = str(device.info.get("REPORTED_IP") or "").strip()
        if reported and reported not in ("", "-", "0.0.0.0") and reported != (device.ip or "").strip():
            self.ed_ip.setPlaceholderText(
                t("settings.network_discover_dk8de_ip_reported_hint", ip=reported)
            )
        self.ed_mask = QLineEdit("255.255.255.0")
        self.ed_gw = QLineEdit("")
        self.ed_dns = QLineEdit("")
        form.addRow(t("settings.network_ip"), self.ed_ip)
        form.addRow(t("settings.network_mask"), self.ed_mask)
        form.addRow(t("settings.network_gateway"), self.ed_gw)
        form.addRow(t("settings.network_dns"), self.ed_dns)
        lay.addLayout(form)
        hint = QLabel(t("settings.network_discover_set_ip_hint_dk8de"))
        hint.setWordWrap(True)
        lay.addWidget(hint)
        self._lbl_gw_warn = QLabel("")
        self._lbl_gw_warn.setWordWrap(True)
        self._lbl_gw_warn.setStyleSheet("color: #d9822b; font-weight: bold;")
        lay.addWidget(self._lbl_gw_warn)
        self.ed_ip.textChanged.connect(self._check_gateway_subnet)
        self.ed_mask.textChanged.connect(self._check_gateway_subnet)
        self.ed_gw.textChanged.connect(self._check_gateway_subnet)
        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        bb.accepted.connect(self._on_accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self._check_gateway_subnet()

    def _check_gateway_subnet(self) -> None:
        ip = self.ed_ip.text().strip()
        mask = self.ed_mask.text().strip()
        gw = self.ed_gw.text().strip()
        if not (ip and mask and gw):
            self._lbl_gw_warn.setText("")
            return
        ok = _ip_in_subnet(ip, mask, gw)
        if ok is False:
            self._lbl_gw_warn.setText(t("settings.network_discover_gw_subnet_warn"))
        else:
            self._lbl_gw_warn.setText("")

    def _on_accept(self) -> None:
        vals = self.values()
        if vals["ip"] and vals["mask"] and vals["gateway"]:
            if _ip_in_subnet(vals["ip"], vals["mask"], vals["gateway"]) is False:
                resp = QMessageBox.warning(
                    self,
                    t("settings.network_discover_title"),
                    t("settings.network_discover_gw_subnet_warn_confirm"),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if resp != QMessageBox.StandardButton.Yes:
                    return
        self.accept()

    def values(self) -> Dict[str, str]:
        return {
            "ip": self.ed_ip.text().strip(),
            "mask": self.ed_mask.text().strip(),
            "gateway": self.ed_gw.text().strip(),
            "dns": self.ed_dns.text().strip(),
        }


class _EbyteDiscoverDialog(QDialog):
    """Zeigt per UDP gefundene Ebyte-Module; Uebernehmen / IP setzen."""

    adopt_requested = Signal(object)  # EbyteDevice
    ip_set_done = Signal(object, str, str)  # device, old_ip, new_ip

    def __init__(self, devices: List[EbyteDevice], parent=None):
        super().__init__(parent)
        self.setWindowTitle(t("settings.network_discover_title"))
        self.resize(720, 420)
        self._devices = list(devices)
        self._set_ip_worker: Optional[QThread] = None
        self._scan_worker: Optional[_ScanWorker] = None

        lay = QVBoxLayout(self)
        intro = QLabel(t("settings.network_discover_intro"))
        intro.setWordWrap(True)
        lay.addWidget(intro)
        hint = QLabel(t("settings.network_discover_hint_incomplete"))
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #d9a441;")
        lay.addWidget(hint)

        self.tbl = QTableWidget(0, 5)
        self.tbl.setHorizontalHeaderLabels(
            [
                t("settings.network_discover_col_model"),
                t("settings.network_discover_col_mac"),
                t("settings.network_discover_col_ip"),
                t("settings.network_discover_col_fw"),
                t("settings.network_discover_col_vendor"),
            ]
        )
        self.tbl.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tbl.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tbl.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tbl.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.tbl.verticalHeader().setVisible(False)
        lay.addWidget(self.tbl, 1)

        for dev in self._devices:
            row = self.tbl.rowCount()
            self.tbl.insertRow(row)
            self.tbl.setItem(row, 0, QTableWidgetItem(dev.model or "—"))
            self.tbl.setItem(row, 1, QTableWidgetItem(dev.mac_str))
            self.tbl.setItem(row, 2, QTableWidgetItem(dev.ip or "—"))
            self.tbl.setItem(row, 3, QTableWidgetItem(dev.fw or "—"))
            self.tbl.setItem(row, 4, QTableWidgetItem(dev.vendor or "—"))
        if self._devices:
            self.tbl.selectRow(0)

        btn_row = QHBoxLayout()
        self._btn_adopt = QPushButton(t("settings.network_discover_btn_adopt"))
        self._btn_adopt.clicked.connect(self._on_adopt)
        self._btn_set_ip = QPushButton(t("settings.network_discover_btn_set_ip"))
        self._btn_set_ip.clicked.connect(self._on_set_ip)
        self._btn_refresh = QPushButton(t("settings.network_discover_btn_refresh"))
        self._btn_refresh.clicked.connect(self._on_refresh)
        btn_row.addWidget(self._btn_adopt)
        btn_row.addWidget(self._btn_set_ip)
        btn_row.addWidget(self._btn_refresh)
        btn_row.addStretch(1)
        lay.addLayout(btn_row)

        self._lbl = QLabel("")
        lay.addWidget(self._lbl)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self._btn_close = bb.button(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(self.reject)
        if self._btn_close is not None:
            self._btn_close.setText(t("about.btn_close"))
            self._btn_close.clicked.connect(self.reject)
        lay.addWidget(bb)

    def _ip_write_running(self) -> bool:
        w = self._set_ip_worker
        if w is None:
            return False
        try:
            return bool(w.isRunning())
        except RuntimeError:
            self._set_ip_worker = None
            return False

    def _scan_running(self) -> bool:
        w = self._scan_worker
        if w is None:
            return False
        try:
            return bool(w.isRunning())
        except RuntimeError:
            self._scan_worker = None
            return False

    def reject(self) -> None:  # type: ignore[override]
        if self._ip_write_running():
            QMessageBox.information(
                self,
                t("settings.network_discover_title"),
                t("settings.network_discover_wait_write"),
            )
            return
        if self._scan_running():
            QMessageBox.information(
                self,
                t("settings.network_discover_title"),
                t("settings.network_discover_wait_scan"),
            )
            return
        super().reject()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if self._ip_write_running():
            event.ignore()
            QMessageBox.information(
                self,
                t("settings.network_discover_title"),
                t("settings.network_discover_wait_write"),
            )
            return
        if self._scan_running():
            event.ignore()
            QMessageBox.information(
                self,
                t("settings.network_discover_title"),
                t("settings.network_discover_wait_scan"),
            )
            return
        w = self._set_ip_worker
        self._set_ip_worker = None
        if w is not None:
            try:
                if w.isRunning():
                    w.wait(1500)
            except RuntimeError:
                pass
        sw = self._scan_worker
        self._scan_worker = None
        if sw is not None:
            try:
                if sw.isRunning():
                    sw.wait(1500)
            except RuntimeError:
                pass
        super().closeEvent(event)

    def _selected(self) -> Optional[EbyteDevice]:
        rows = self.tbl.selectionModel().selectedRows()
        if not rows:
            return None
        idx = rows[0].row()
        if 0 <= idx < len(self._devices):
            return self._devices[idx]
        return None

    def _reload_table(self, devices: List[EbyteDevice]) -> None:
        prev_mac = None
        dev = self._selected()
        if dev is not None:
            prev_mac = dev.mac_str
        self._devices = list(devices)
        self.tbl.setRowCount(0)
        for d in self._devices:
            row = self.tbl.rowCount()
            self.tbl.insertRow(row)
            self.tbl.setItem(row, 0, QTableWidgetItem(d.model or "—"))
            self.tbl.setItem(row, 1, QTableWidgetItem(d.mac_str))
            self.tbl.setItem(row, 2, QTableWidgetItem(d.ip or "—"))
            self.tbl.setItem(row, 3, QTableWidgetItem(d.fw or "—"))
            self.tbl.setItem(row, 4, QTableWidgetItem(d.vendor or "—"))
        if self._devices:
            row_to_select = 0
            if prev_mac is not None:
                for i, d in enumerate(self._devices):
                    if d.mac_str == prev_mac:
                        row_to_select = i
                        break
            self.tbl.selectRow(row_to_select)

    def _on_refresh(self) -> None:
        if self._scan_running() or self._ip_write_running():
            return
        self._btn_adopt.setEnabled(False)
        self._btn_set_ip.setEnabled(False)
        self._btn_refresh.setEnabled(False)
        if self._btn_close is not None:
            self._btn_close.setEnabled(False)
        self._lbl.setText(t("settings.network_status_scanning"))
        self._scan_worker = _ScanWorker(timeout=5.0, parent=self)
        self._scan_worker.finished_ok.connect(self._on_refresh_ok)
        self._scan_worker.finished_err.connect(self._on_refresh_err)
        self._scan_worker.finished.connect(self._on_refresh_finished)
        self._scan_worker.start()

    def _on_refresh_finished(self) -> None:
        self._btn_adopt.setEnabled(True)
        self._btn_set_ip.setEnabled(True)
        self._btn_refresh.setEnabled(True)
        if self._btn_close is not None:
            self._btn_close.setEnabled(True)
        w = self._scan_worker
        self._scan_worker = None
        if w is not None:
            try:
                w.deleteLater()
            except RuntimeError:
                pass

    def _on_refresh_ok(self, found: object) -> None:
        if isinstance(found, dict):
            devices = [
                d
                for d in (found.get("ebyte") or [])
                if isinstance(d, EbyteDevice)
            ]
        else:
            devices = [d for d in (found if isinstance(found, list) else []) if isinstance(d, EbyteDevice)]
        if not devices:
            self._lbl.setText(t("settings.network_status_scan_empty"))
            return
        self._reload_table(devices)
        self._lbl.setText(t("settings.network_status_scan_done", found=len(devices)))

    def _on_refresh_err(self, msg: str) -> None:
        self._lbl.setText(t("settings.network_status_error", err=msg))

    def _on_adopt(self) -> None:
        if self._ip_write_running() or self._scan_running():
            return
        dev = self._selected()
        if dev is None:
            return
        self.adopt_requested.emit(dev)
        self._lbl.setText(
            t("settings.network_discover_adopted", model=dev.model or "?", ip=dev.ip or "?")
        )

    def _on_set_ip(self) -> None:
        if self._ip_write_running() or self._scan_running():
            return
        dev = self._selected()
        if dev is None:
            return
        if not dev.pages:
            QMessageBox.warning(
                self,
                t("settings.network_discover_title"),
                t("settings.network_discover_no_pages"),
            )
            return
        dlg = _EbyteSetIpDialog(dev, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        vals = dlg.values()
        if not vals["ip"] or not vals["mask"]:
            QMessageBox.warning(
                self,
                t("settings.network_discover_title"),
                t("settings.network_discover_ip_required"),
            )
            return
        self._btn_set_ip.setEnabled(False)
        self._btn_adopt.setEnabled(False)
        if self._btn_close is not None:
            self._btn_close.setEnabled(False)
        self._lbl.setText(t("settings.network_discover_setting_ip"))
        self._set_ip_worker = _SetIpWorker(
            dev,
            vals["ip"],
            vals["mask"],
            vals["gateway"],
            vals["dns"],
            vals["dns2"],
            self,
        )
        self._pending_ip = vals["ip"]
        self._pending_dev = dev
        self._set_ip_worker.finished_ok.connect(self._on_set_ip_ok)
        self._set_ip_worker.finished_err.connect(self._on_set_ip_err)
        self._set_ip_worker.finished.connect(self._on_set_ip_finished)
        self._set_ip_worker.start()

    def _on_set_ip_finished(self) -> None:
        self._btn_set_ip.setEnabled(True)
        self._btn_adopt.setEnabled(True)
        if self._btn_close is not None:
            self._btn_close.setEnabled(True)
        w = self._set_ip_worker
        self._set_ip_worker = None
        if w is not None:
            try:
                w.deleteLater()
            except RuntimeError:
                pass

    def _on_set_ip_ok(self, _res: object) -> None:
        dev = getattr(self, "_pending_dev", None)
        new_ip = str(getattr(self, "_pending_ip", "") or "")
        old_ip = ""
        if dev is not None:
            old_ip = str(dev.ip or "")
            dev.ip = new_ip
            row = self._devices.index(dev) if dev in self._devices else -1
            if row >= 0:
                self.tbl.setItem(row, 2, QTableWidgetItem(new_ip or "—"))
            self.ip_set_done.emit(dev, old_ip, new_ip)
        self._lbl.setText(t("settings.network_discover_set_ip_ok", ip=new_ip))
        vendor = str(getattr(dev, "vendor", "") or "").strip().lower()
        # Laut Mitschnitt des Original-Tools schickt es nach dem Schreiben
        # KEIN Neustart-Kommando an das NE2 - das Modul startet nicht selbst
        # neu, die neuen Netzdaten sind aber bereits geschrieben (ACK) und
        # werden nach einem manuellen/physischen Neustart aktiv.
        detail_key = (
            "settings.network_discover_set_ip_ok_detail_manual_reboot"
            if vendor == VENDOR_NE2
            else "settings.network_discover_set_ip_ok_detail"
        )
        QMessageBox.information(
            self,
            t("settings.network_discover_title"),
            t(detail_key, ip=new_ip),
        )

    def _on_set_ip_err(self, msg: str) -> None:
        self._lbl.setText(t("settings.network_status_error", err=msg))
        QMessageBox.warning(
            self,
            t("settings.network_discover_title"),
            t("settings.network_discover_set_ip_failed", err=msg),
        )


class _Dk8deStatsDialog(QDialog):
    """Zeigt AT+INFO? und AT+STATUS? fuer ein DK8DE-Modul (formatiert)."""

    _INFO_LABEL_KEYS = {
        "UID": "settings.network_dk8de_stat_uid",
        "NAME": "settings.network_dk8de_stat_name",
        "AP": "settings.network_dk8de_stat_ap",
        "MAC": "settings.network_dk8de_stat_mac",
        "BUS": "settings.network_dk8de_stat_bus",
        "FW": "settings.network_dk8de_stat_fw",
        "HW": "settings.network_dk8de_stat_hw",
        "IP": "settings.network_dk8de_stat_ip",
        "NETMODE": "settings.network_dk8de_stat_netmode",
        "WIFIMODE": "settings.network_dk8de_stat_wifimode",
        "LPORT": "settings.network_dk8de_stat_lport",
        "DISCOVERY_UDP": "settings.network_dk8de_stat_discovery_udp",
    }
    _STATUS_LABEL_KEYS = {
        "WIFI": "settings.network_dk8de_stat_wifi",
        "IP": "settings.network_dk8de_stat_ip",
        "RSSI": "settings.network_dk8de_stat_rssi",
        "LINK": "settings.network_dk8de_stat_link",
        "HEAP": "settings.network_dk8de_stat_heap",
        "PACKETTIME": "settings.network_dk8de_stat_packettime",
        "PACKETSIZE": "settings.network_dk8de_stat_packetsize",
        "RS485_RX": "settings.network_dk8de_stat_rs485_rx",
        "RS485_TX": "settings.network_dk8de_stat_rs485_tx",
        "NET_RX": "settings.network_dk8de_stat_net_rx",
        "NET_TX": "settings.network_dk8de_stat_net_tx",
        "NET_TX_DROPS": "settings.network_dk8de_stat_net_tx_drops",
        "NET_RX_DROPS": "settings.network_dk8de_stat_net_rx_drops",
        "RS485_TX_ALLOWED": "settings.network_dk8de_stat_rs485_tx_allowed",
        "RS485_RX_ALLOWED": "settings.network_dk8de_stat_rs485_rx_allowed",
        "BRIDGE": "settings.network_dk8de_stat_bridge",
    }

    def __init__(self, module: NetworkModule, parent=None):
        super().__init__(parent)
        self.setWindowTitle(t("settings.network_dk8de_stats_title"))
        self.resize(480, 640)
        self._module = module
        self._worker: Optional[_Dk8deStatsWorker] = None
        self._info_values: Dict[str, QLineEdit] = {}
        self._status_values: Dict[str, QLineEdit] = {}

        lay = QVBoxLayout(self)
        intro = QLabel(
            t(
                "settings.network_dk8de_stats_intro",
                host=module.host or "?",
                uid=(module.uid or "?"),
            )
        )
        intro.setWordWrap(True)
        lay.addWidget(intro)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        content = QWidget()
        content_l = QVBoxLayout(content)
        content_l.setContentsMargins(0, 0, 0, 0)
        content_l.setSpacing(10)

        self._gb_info = QGroupBox(t("settings.network_dk8de_stats_group_info"))
        form_info = QFormLayout(self._gb_info)
        form_info.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        form_info.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        form_info.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        form_info.setHorizontalSpacing(12)
        for key in DK8DE_INFO_STAT_KEYS:
            lbl = self._make_label(t(self._INFO_LABEL_KEYS.get(key, key)))
            val = self._make_value_field("—")
            form_info.addRow(lbl, val)
            self._info_values[key] = val
        content_l.addWidget(self._gb_info)

        for group_title_key, keys in DK8DE_STATUS_UI_SECTIONS:
            gb = QGroupBox(t(group_title_key))
            form = QFormLayout(gb)
            form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
            form.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            form.setHorizontalSpacing(12)
            for key in keys:
                lbl = self._make_label(t(self._STATUS_LABEL_KEYS.get(key, key)))
                val = self._make_value_field("—")
                form.addRow(lbl, val)
                self._status_values[key] = val
            content_l.addWidget(gb)
        content_l.addStretch(1)
        scroll.setWidget(content)
        lay.addWidget(scroll, 1)

        self._lbl = QLabel(t("settings.network_dk8de_stats_loading"))
        lay.addWidget(self._lbl)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(self.reject)
        btn_close = bb.button(QDialogButtonBox.StandardButton.Close)
        if btn_close is not None:
            btn_close.setText(t("about.btn_close"))
            btn_close.clicked.connect(self.reject)
        lay.addWidget(bb)

        self._start_load()

    @staticmethod
    def _make_label(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setWordWrap(True)
        lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        return lbl

    @staticmethod
    def _make_value_field(text: str) -> QLineEdit:
        ed = QLineEdit(text)
        ed.setReadOnly(True)
        ed.setMinimumWidth(220)
        return ed

    @classmethod
    def _format_value(cls, key: str, raw: str) -> str:
        k = key.upper()
        v = str(raw or "").strip()
        if not v or v in ("-", "?"):
            return "—"
        if k == "NETMODE":
            sock = dk8de_netmode_to_sock(v)
            label_key = {
                "TCPS": "settings.network_dk8de_stats_val_netmode_tcps",
                "TCPC": "settings.network_dk8de_stats_val_netmode_tcpc",
                "UDPS": "settings.network_dk8de_stats_val_netmode_udps",
                "UDPC": "settings.network_dk8de_stats_val_netmode_udpc",
                "DISABLE": "settings.network_dk8de_stats_val_netmode_disable",
            }.get(sock)
            return t(label_key) if label_key else v
        if k == "WIFIMODE":
            w = v.upper()
            if w.isdigit():
                w = {"0": "AP", "1": "STA", "2": "APSTA"}.get(w, w)
            label_key = {
                "AP": "settings.network_dk8de_stats_val_wifimode_ap",
                "STA": "settings.network_dk8de_stats_val_wifimode_sta",
                "APSTA": "settings.network_dk8de_stats_val_wifimode_apsta",
            }.get(w)
            return t(label_key) if label_key else v
        if k == "LINK":
            if v in ("1", "UP", "TRUE", "CONNECTED"):
                return t("settings.network_dk8de_stats_val_link_up")
            if v in ("0", "DOWN", "FALSE", "DISCONNECTED"):
                return t("settings.network_dk8de_stats_val_link_down")
            return v
        if k == "RSSI":
            try:
                return f"{int(v)} dBm"
            except (TypeError, ValueError):
                return v if v.endswith("dBm") else f"{v} dBm"
        if k == "HEAP":
            try:
                n = int(v)
                if n >= 1024:
                    return t("settings.network_dk8de_stats_val_heap_kib", kb=n / 1024.0)
                return t("settings.network_dk8de_stats_val_heap_b", bytes=n)
            except (TypeError, ValueError):
                return v
        if k in ("LPORT", "DISCOVERY_UDP", "BUS", "PACKETTIME", "PACKETSIZE"):
            try:
                n = int(v)
                if k == "PACKETTIME":
                    return t("settings.network_dk8de_stats_val_ms", ms=n)
                if k == "PACKETSIZE":
                    return t("settings.network_dk8de_stats_val_bytes", bytes=n)
                return str(n)
            except (TypeError, ValueError):
                return v
        if k in (
            "RS485_RX",
            "RS485_TX",
            "NET_RX",
            "NET_TX",
            "NET_TX_DROPS",
            "NET_RX_DROPS",
        ):
            try:
                return t("settings.network_dk8de_stats_val_counter", n=int(v))
            except (TypeError, ValueError):
                return v
        if k == "RS485_TX_ALLOWED":
            if v in ("1", "TRUE", "ON"):
                return t("settings.network_dk8de_stats_val_dir_ns_on")
            if v in ("0", "FALSE", "OFF"):
                return t("settings.network_dk8de_stats_val_dir_ns_off")
            return v
        if k == "RS485_RX_ALLOWED":
            if v in ("1", "TRUE", "ON"):
                return t("settings.network_dk8de_stats_val_dir_sn_on")
            if v in ("0", "FALSE", "OFF"):
                return t("settings.network_dk8de_stats_val_dir_sn_off")
            return v
        if k == "BRIDGE":
            if v in ("1", "TRUE", "ON"):
                return t("settings.network_dk8de_stats_val_on")
            if v in ("0", "FALSE", "OFF"):
                return t("settings.network_dk8de_stats_val_off")
            return v
        return v

    def _fill_section(
        self,
        keys: Tuple[str, ...],
        data: Dict[str, str],
        widgets: Dict[str, QLineEdit],
    ) -> None:
        for key in keys:
            w = widgets.get(key)
            if w is None:
                continue
            w.setText(self._format_value(key, str(data.get(key, "") or "")))

    def _start_load(self) -> None:
        if self._worker and self._worker.isRunning():
            return
        self._worker = _Dk8deStatsWorker(self._module, self)
        self._worker.finished_ok.connect(self._on_ok)
        self._worker.finished_err.connect(self._on_err)
        self._worker.finished.connect(self._on_finished)
        self._worker.start()

    def _on_finished(self) -> None:
        w = self._worker
        self._worker = None
        if w is not None:
            try:
                w.deleteLater()
            except RuntimeError:
                pass

    def _on_ok(self, st: object) -> None:
        data = st if isinstance(st, dict) else {}
        info_raw = str(data.get("info") or "")
        status_raw = str(data.get("status") or "")
        info, status = parse_dk8de_stats_payload(info_raw, status_raw)
        if not info and not status:
            self._lbl.setText(t("settings.network_dk8de_stats_empty"))
            return
        self._fill_section(DK8DE_INFO_STAT_KEYS, info, self._info_values)
        self._fill_section(DK8DE_STATUS_STAT_KEYS, status, self._status_values)
        self._lbl.setText(t("settings.network_dk8de_stats_ok"))

    def _on_err(self, msg: str) -> None:
        for w in list(self._info_values.values()) + list(self._status_values.values()):
            w.setText("—")
        self._lbl.setText(t("settings.network_status_error", err=msg))
        QMessageBox.warning(
            self,
            t("settings.network_dk8de_stats_title"),
            t("settings.network_dk8de_stats_failed", err=msg),
        )


class _NetworkDiscoverDialog(QDialog):
    """Gefundene Ebyte- und DK8DE-Module in einer Tabelle."""

    adopt_ebyte = Signal(object)
    adopt_dk8de = Signal(object)
    ip_set_done = Signal(object, str, str)

    def __init__(
        self,
        ebyte_devices: List[EbyteDevice],
        dk8de_devices: List[Dk8deDevice],
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle(t("settings.network_discover_title"))
        self.resize(860, 480)
        self._ebyte = list(ebyte_devices)
        self._dk8de = list(dk8de_devices)
        self._rows: List[Tuple[str, object]] = []
        self._set_ip_worker: Optional[QThread] = None
        self._scan_worker: Optional[_ScanWorker] = None

        lay = QVBoxLayout(self)
        intro = QLabel(t("settings.network_discover_intro"))
        intro.setWordWrap(True)
        lay.addWidget(intro)
        hint = QLabel(t("settings.network_discover_hint_combined"))
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #d9a441;")
        lay.addWidget(hint)

        self.tbl = QTableWidget(0, 7)
        self.tbl.setHorizontalHeaderLabels(
            [
                t("settings.network_discover_col_type"),
                t("settings.network_discover_col_name"),
                t("settings.network_discover_col_uid"),
                t("settings.network_discover_col_mac"),
                t("settings.network_discover_col_ip"),
                t("settings.network_discover_col_fw"),
                t("settings.network_discover_col_port"),
            ]
        )
        self.tbl.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tbl.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tbl.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tbl.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.selectionModel().selectionChanged.connect(self._update_action_buttons)
        lay.addWidget(self.tbl, 1)

        btn_row = QHBoxLayout()
        self._btn_adopt = QPushButton(t("settings.network_discover_btn_adopt"))
        self._btn_adopt.clicked.connect(self._on_adopt)
        self._btn_set_ip = QPushButton(t("settings.network_discover_btn_set_ip"))
        self._btn_set_ip.clicked.connect(self._on_set_ip)
        self._btn_refresh = QPushButton(t("settings.network_discover_btn_refresh"))
        self._btn_refresh.clicked.connect(self._on_refresh)
        btn_row.addWidget(self._btn_adopt)
        btn_row.addWidget(self._btn_set_ip)
        btn_row.addWidget(self._btn_refresh)
        btn_row.addStretch(1)
        lay.addLayout(btn_row)

        self._reload_table()

        self._lbl = QLabel("")
        lay.addWidget(self._lbl)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self._btn_close = bb.button(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(self.reject)
        if self._btn_close is not None:
            self._btn_close.setText(t("about.btn_close"))
            self._btn_close.clicked.connect(self.reject)
        lay.addWidget(bb)
        self._update_action_buttons()

    def _ip_write_running(self) -> bool:
        w = self._set_ip_worker
        if w is None:
            return False
        try:
            return bool(w.isRunning())
        except RuntimeError:
            self._set_ip_worker = None
            return False

    def _scan_running(self) -> bool:
        w = self._scan_worker
        if w is None:
            return False
        try:
            return bool(w.isRunning())
        except RuntimeError:
            self._scan_worker = None
            return False

    def reject(self) -> None:  # type: ignore[override]
        if self._ip_write_running():
            QMessageBox.information(
                self,
                t("settings.network_discover_title"),
                t("settings.network_discover_wait_write"),
            )
            return
        if self._scan_running():
            QMessageBox.information(
                self,
                t("settings.network_discover_title"),
                t("settings.network_discover_wait_scan"),
            )
            return
        super().reject()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if self._ip_write_running():
            event.ignore()
            QMessageBox.information(
                self,
                t("settings.network_discover_title"),
                t("settings.network_discover_wait_write"),
            )
            return
        if self._scan_running():
            event.ignore()
            QMessageBox.information(
                self,
                t("settings.network_discover_title"),
                t("settings.network_discover_wait_scan"),
            )
            return
        w = self._set_ip_worker
        self._set_ip_worker = None
        if w is not None:
            try:
                if w.isRunning():
                    w.wait(3000)
            except RuntimeError:
                pass
        sw = self._scan_worker
        self._scan_worker = None
        if sw is not None:
            try:
                if sw.isRunning():
                    sw.wait(1500)
            except RuntimeError:
                pass
        super().closeEvent(event)

    def _reload_table(self) -> None:
        self._rows = [("dk8de", dev) for dev in self._dk8de] + [
            ("ebyte", dev) for dev in self._ebyte
        ]
        self.tbl.setRowCount(0)
        for kind, dev in self._rows:
            row = self.tbl.rowCount()
            self.tbl.insertRow(row)
            if kind == "dk8de" and isinstance(dev, Dk8deDevice):
                self.tbl.setItem(row, 0, QTableWidgetItem(t("settings.network_discover_type_dk8de")))
                self.tbl.setItem(row, 1, QTableWidgetItem(dev.name or "—"))
                self.tbl.setItem(row, 2, QTableWidgetItem(dev.uid_norm or "—"))
                self.tbl.setItem(row, 3, QTableWidgetItem(dev.mac or "—"))
                self.tbl.setItem(row, 4, QTableWidgetItem(dev.ip or "—"))
                self.tbl.setItem(row, 5, QTableWidgetItem(dev.fw or "—"))
                self.tbl.setItem(row, 6, QTableWidgetItem(str(dev.lport or 8886)))
            elif kind == "ebyte" and isinstance(dev, EbyteDevice):
                self.tbl.setItem(row, 0, QTableWidgetItem(dev.vendor or dev.model or "Ebyte"))
                self.tbl.setItem(row, 1, QTableWidgetItem(dev.model or "—"))
                self.tbl.setItem(row, 2, QTableWidgetItem("—"))
                self.tbl.setItem(row, 3, QTableWidgetItem(dev.mac_str))
                self.tbl.setItem(row, 4, QTableWidgetItem(dev.ip or "—"))
                self.tbl.setItem(row, 5, QTableWidgetItem(dev.fw or "—"))
                self.tbl.setItem(row, 6, QTableWidgetItem("—"))
        if self._rows:
            self.tbl.selectRow(0)
        self._update_action_buttons()

    def _selected(self) -> Optional[Tuple[str, object]]:
        rows = self.tbl.selectionModel().selectedRows()
        if not rows:
            return None
        idx = rows[0].row()
        if 0 <= idx < len(self._rows):
            return self._rows[idx]
        return None

    def _row_index_for_dev(self, dev: object) -> int:
        for i, (_kind, d) in enumerate(self._rows):
            if d is dev:
                return i
        return -1

    def _update_action_buttons(self, *_args) -> None:
        if self._ip_write_running():
            self._btn_adopt.setEnabled(False)
            self._btn_set_ip.setEnabled(False)
            return
        sel = self._selected()
        if sel is None:
            self._btn_adopt.setEnabled(False)
            self._btn_set_ip.setEnabled(False)
            return
        kind, dev = sel
        self._btn_adopt.setEnabled(True)
        if kind == "ebyte" and isinstance(dev, EbyteDevice):
            self._btn_set_ip.setEnabled(bool(dev.pages))
        elif kind == "dk8de" and isinstance(dev, Dk8deDevice):
            host = str(dev.ip or "").strip()
            self._btn_set_ip.setEnabled(
                bool(dev.uid_norm) and bool(host) and host not in ("0.0.0.0", "-")
            )
        else:
            self._btn_set_ip.setEnabled(False)

    def _on_adopt(self) -> None:
        sel = self._selected()
        if sel is None:
            return
        kind, dev = sel
        if kind == "ebyte" and isinstance(dev, EbyteDevice):
            self.adopt_ebyte.emit(dev)
        elif kind == "dk8de" and isinstance(dev, Dk8deDevice):
            self.adopt_dk8de.emit(dev)

    def _on_set_ip(self) -> None:
        if self._ip_write_running():
            QMessageBox.information(
                self,
                t("settings.network_discover_title"),
                t("settings.network_discover_wait_write"),
            )
            return
        sel = self._selected()
        if sel is None:
            return
        kind, dev = sel
        if kind == "dk8de" and isinstance(dev, Dk8deDevice):
            self._start_dk8de_set_ip(dev)
        elif kind == "ebyte" and isinstance(dev, EbyteDevice):
            self._start_ebyte_set_ip(dev)

    def _start_dk8de_set_ip(self, dev: Dk8deDevice) -> None:
        host = str(dev.ip or "").strip()
        if not host or host in ("0.0.0.0", "-"):
            QMessageBox.warning(
                self,
                t("settings.network_discover_title"),
                t("settings.network_discover_dk8de_no_contact_ip"),
            )
            return
        if not dev.uid_norm:
            QMessageBox.warning(
                self,
                t("settings.network_discover_title"),
                t("settings.network_discover_dk8de_no_uid"),
            )
            return
        dlg = _Dk8deSetIpDialog(dev, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        vals = dlg.values()
        if not vals["ip"] or not vals["mask"]:
            QMessageBox.warning(
                self,
                t("settings.network_discover_title"),
                t("settings.network_discover_ip_required"),
            )
            return
        self._btn_adopt.setEnabled(False)
        self._btn_set_ip.setEnabled(False)
        if self._btn_close is not None:
            self._btn_close.setEnabled(False)
        self._lbl.setText(t("settings.network_discover_setting_ip_dk8de"))
        self._set_ip_worker = _Dk8deSetIpWorker(
            dev,
            vals["ip"],
            vals["mask"],
            vals["gateway"],
            vals["dns"],
            self,
        )
        self._pending_ip = vals["ip"]
        self._pending_dev = dev
        self._set_ip_worker.finished_ok.connect(self._on_set_ip_ok)
        self._set_ip_worker.finished_err.connect(self._on_set_ip_err)
        self._set_ip_worker.finished.connect(self._on_set_ip_finished)
        self._set_ip_worker.start()

    def _start_ebyte_set_ip(self, dev: EbyteDevice) -> None:
        if not dev.pages:
            QMessageBox.warning(
                self,
                t("settings.network_discover_title"),
                t("settings.network_discover_no_pages"),
            )
            return
        dlg = _EbyteSetIpDialog(dev, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        vals = dlg.values()
        if not vals["ip"] or not vals["mask"]:
            QMessageBox.warning(
                self,
                t("settings.network_discover_title"),
                t("settings.network_discover_ip_required"),
            )
            return
        self._btn_adopt.setEnabled(False)
        self._btn_set_ip.setEnabled(False)
        if self._btn_close is not None:
            self._btn_close.setEnabled(False)
        self._lbl.setText(t("settings.network_discover_setting_ip"))
        self._set_ip_worker = _SetIpWorker(
            dev,
            vals["ip"],
            vals["mask"],
            vals["gateway"],
            vals["dns"],
            vals["dns2"],
            self,
        )
        self._pending_ip = vals["ip"]
        self._pending_dev = dev
        self._set_ip_worker.finished_ok.connect(self._on_set_ip_ok)
        self._set_ip_worker.finished_err.connect(self._on_set_ip_err)
        self._set_ip_worker.finished.connect(self._on_set_ip_finished)
        self._set_ip_worker.start()

    def _on_set_ip_finished(self) -> None:
        if self._btn_close is not None:
            self._btn_close.setEnabled(True)
        w = self._set_ip_worker
        self._set_ip_worker = None
        if w is not None:
            try:
                if w.isRunning():
                    w.wait(3000)
            except RuntimeError:
                pass
        self._update_action_buttons()

    def _on_set_ip_ok(self, _res: object) -> None:
        dev = getattr(self, "_pending_dev", None)
        new_ip = str(getattr(self, "_pending_ip", "") or "")
        old_ip = ""
        if isinstance(dev, EbyteDevice):
            old_ip = str(dev.ip or "")
            dev.ip = new_ip
            row = self._row_index_for_dev(dev)
            if row >= 0:
                self.tbl.setItem(row, 4, QTableWidgetItem(new_ip or "—"))
            self.ip_set_done.emit(dev, old_ip, new_ip)
            self._lbl.setText(t("settings.network_discover_set_ip_ok", ip=new_ip))
        elif isinstance(dev, Dk8deDevice):
            old_ip = str(dev.ip or "")
            dev.ip = new_ip
            if dev.info.get("REPORTED_IP"):
                dev.info["REPORTED_IP"] = new_ip
            row = self._row_index_for_dev(dev)
            if row >= 0:
                self.tbl.setItem(row, 4, QTableWidgetItem(new_ip or "—"))
            self.ip_set_done.emit(dev, old_ip, new_ip)
            self._lbl.setText(t("settings.network_discover_set_ip_ok", ip=new_ip))
            QMessageBox.information(
                self,
                t("settings.network_discover_title"),
                t("settings.network_discover_set_ip_ok_detail_dk8de", ip=new_ip),
            )

    def _on_set_ip_err(self, msg: str) -> None:
        self._lbl.setText(t("settings.network_status_error", err=msg))
        QMessageBox.warning(
            self,
            t("settings.network_discover_title"),
            t("settings.network_discover_set_ip_failed", err=msg),
        )

    def _on_refresh(self) -> None:
        if self._scan_worker and self._scan_worker.isRunning():
            return
        self._btn_refresh.setEnabled(False)
        self._lbl.setText(t("settings.network_status_scanning"))
        self._scan_worker = _ScanWorker(timeout=5.0, parent=self)
        self._scan_worker.finished_ok.connect(self._on_refresh_ok)
        self._scan_worker.finished_err.connect(self._on_refresh_err)
        self._scan_worker.finished.connect(lambda: self._btn_refresh.setEnabled(True))
        self._scan_worker.start()

    def _on_refresh_ok(self, found: object) -> None:
        ebyte: List[EbyteDevice] = []
        dk8de: List[Dk8deDevice] = []
        if isinstance(found, dict):
            ebyte = [d for d in (found.get("ebyte") or []) if isinstance(d, EbyteDevice)]
            dk8de = [d for d in (found.get("dk8de") or []) if isinstance(d, Dk8deDevice)]
        self._ebyte = ebyte
        self._dk8de = dk8de
        self._reload_table()
        total = len(ebyte) + len(dk8de)
        if total == 0:
            self._lbl.setText(t("settings.network_status_scan_empty"))
        else:
            self._lbl.setText(t("settings.network_status_scan_done", found=total))

    def _on_refresh_err(self, msg: str) -> None:
        self._lbl.setText(t("settings.network_status_error", err=msg))


class NetworkModulesTab(QWidget):
    """Bearbeitet ``network_modules`` (+ optionale Legacy-``network_scan``-Keys)."""

    # Signalisiert dem umschliessenden Settings-Fenster, dass die Modulliste
    # sich veraendert hat und sofort persistiert werden soll (ohne dass der
    # Nutzer extra auf den globalen "Speichern"-Button klicken muss).
    save_requested = Signal()

    def __init__(self, cfg: dict, parent=None):
        super().__init__(parent)
        self._cfg = cfg
        self._modules: List[NetworkModule] = []
        self._online: List[bool] = []
        # Zaehlt aufeinanderfolgende fehlgeschlagene Status-Proben je Modul;
        # erst ab _OFFLINE_STREAK_LIMIT Fehlschlaegen in Folge wird die LED
        # wirklich auf "offline" gesetzt (schwache Embedded-Stacks liefern
        # gelegentlich einen einzelnen verlorenen/verzoegerten Connect).
        self._offline_streak: List[int] = []
        self._persisted_ids: set[int] = set()
        self._suppress = False
        self._form_enabled = False
        self._read_interactive = True
        # Wird beim Uebernehmen eines gefundenen Moduls gesetzt: nach dem
        # automatischen Auslesen (erfolgreich oder nicht) soll sofort
        # gespeichert werden, damit auch korrigierte Ports uebernommen werden.
        self._save_after_read = False
        self._probe_worker: Optional[_ProbeWorker] = None
        self._read_worker: Optional[_ReadWorker] = None
        self._write_worker: Optional[_WriteWorker] = None
        self._scan_worker: Optional[_ScanWorker] = None
        self._scan_progress: Optional[QProgressDialog] = None
        self._leds: List[Led] = []
        self._build_ui()
        self.load_from_cfg()
        self._status_timer = QTimer(self)
        self._status_timer.setInterval(4000)
        self._status_timer.timeout.connect(self._tick_probe)

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        self._lbl_intro = QLabel(t("settings.network_intro"))
        self._lbl_intro.setWordWrap(True)
        root.addWidget(self._lbl_intro)

        split = QSplitter(Qt.Orientation.Horizontal)
        # --- Liste ---
        left = QWidget()
        left_l = QVBoxLayout(left)
        left_l.setContentsMargins(0, 0, 0, 0)
        self._list = QListWidget()
        self._list.currentRowChanged.connect(self._on_sel_changed)
        left_l.addWidget(self._list, 1)
        btn_row = QHBoxLayout()
        self._btn_add = QPushButton(t("settings.network_btn_add"))
        self._btn_add.clicked.connect(self._on_add)
        self._btn_remove = QPushButton(t("settings.network_btn_remove"))
        self._btn_remove.clicked.connect(self._on_remove)
        btn_row.addWidget(self._btn_add)
        btn_row.addWidget(self._btn_remove)
        left_l.addLayout(btn_row)
        self._btn_scan = QPushButton(t("settings.network_btn_scan"))
        self._btn_scan.setToolTip(tt("settings.network_btn_scan_tooltip"))
        self._btn_scan.clicked.connect(self._on_scan)
        left_l.addWidget(self._btn_scan)
        split.addWidget(left)

        # --- Detail ---
        right = QWidget()
        right_l = QVBoxLayout(right)
        right_l.setContentsMargins(0, 0, 0, 0)

        self._gb_ident = QGroupBox(t("settings.network_group_ident"))
        fi = QFormLayout(self._gb_ident)
        self.ed_name = QLineEdit()
        self.ed_name.editingFinished.connect(self._apply_form_to_current)
        self.ed_name.setToolTip(tt("settings.network_name_tooltip"))
        self._lbl_name = QLabel(t("settings.network_name"))
        fi.addRow(self._lbl_name, self.ed_name)

        self.cb_vendor = QComboBox()
        self._fill_vendor_combo()
        self.cb_vendor.currentIndexChanged.connect(self._on_vendor_changed)
        self.cb_vendor.setToolTip(tt("settings.network_vendor_tooltip"))
        self._lbl_vendor = QLabel(t("settings.network_vendor"))
        fi.addRow(self._lbl_vendor, self.cb_vendor)

        self.ed_uid = QLineEdit()
        self.ed_uid.setMaxLength(16)
        self.ed_uid.editingFinished.connect(self._apply_form_to_current)
        self.ed_uid.setToolTip(tt("settings.network_uid_tooltip"))
        self._lbl_uid = QLabel(t("settings.network_uid"))
        fi.addRow(self._lbl_uid, self.ed_uid)

        self.sp_config_port = QSpinBox()
        self.sp_config_port.setRange(1, 65535)
        self.sp_config_port.setValue(8880)
        self.sp_config_port.valueChanged.connect(self._apply_form_to_current)
        self.sp_config_port.setToolTip(tt("settings.network_config_port_tooltip"))
        self._lbl_config_port = QLabel(t("settings.network_config_port"))
        fi.addRow(self._lbl_config_port, self.sp_config_port)

        self.sp_web_port = QSpinBox()
        self.sp_web_port.setRange(1, 65535)
        self.sp_web_port.valueChanged.connect(self._apply_form_to_current)
        self.sp_web_port.setToolTip(tt("settings.network_web_port_tooltip"))
        self._lbl_web_port = QLabel(t("settings.network_web_port"))
        fi.addRow(self._lbl_web_port, self.sp_web_port)

        self.ed_netat = QLineEdit("NETAT")
        self.ed_netat.editingFinished.connect(self._apply_form_to_current)
        self.ed_netat.setToolTip(tt("settings.network_netat_header_tooltip"))
        self._lbl_netat = QLabel(t("settings.network_netat_header"))
        fi.addRow(self._lbl_netat, self.ed_netat)

        self.ed_cmdpw = QLineEdit("USR")
        self.ed_cmdpw.editingFinished.connect(self._apply_form_to_current)
        self.ed_cmdpw.setToolTip(tt("settings.network_cmdpw_tooltip"))
        self._lbl_cmdpw = QLabel(t("settings.network_cmdpw"))
        fi.addRow(self._lbl_cmdpw, self.ed_cmdpw)

        self.ed_web_user = QLineEdit("admin")
        self.ed_web_user.editingFinished.connect(self._apply_form_to_current)
        self.ed_web_user.setToolTip(tt("settings.network_web_user_tooltip"))
        self._lbl_web_user = QLabel(t("settings.network_web_user"))
        fi.addRow(self._lbl_web_user, self.ed_web_user)

        self.ed_web_password = QLineEdit("admin")
        self.ed_web_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.ed_web_password.editingFinished.connect(self._apply_form_to_current)
        self.ed_web_password.setToolTip(tt("settings.network_web_password_tooltip"))
        self._lbl_web_password = QLabel(t("settings.network_web_password"))
        fi.addRow(self._lbl_web_password, self.ed_web_password)
        right_l.addWidget(self._gb_ident)

        self._gb_wan = QGroupBox(t("settings.network_group_wan"))
        fw = QFormLayout(self._gb_wan)
        self.cb_wan_mode = QComboBox()
        self.cb_wan_mode.addItem("STATIC", "STATIC")
        self.cb_wan_mode.addItem("DHCP", "DHCP")
        self.cb_wan_mode.setToolTip(tt("settings.network_wan_mode_tooltip"))
        self._lbl_wan_mode = QLabel(t("settings.network_wan_mode"))
        fw.addRow(self._lbl_wan_mode, self.cb_wan_mode)
        self.ed_ip = QLineEdit()
        self.ed_ip.editingFinished.connect(self._apply_form_to_current)
        # Diese eine IP dient sowohl als Verbindungsadresse (frueher "Host")
        # als auch als statische Netzwerk-IP des Moduls - beide waren immer
        # identisch, daher genuegt jetzt ein einziges Feld.
        self.ed_ip.setToolTip(tt("settings.network_host_tooltip"))
        self._lbl_ip = QLabel(t("settings.network_host"))
        fw.addRow(self._lbl_ip, self.ed_ip)
        self.ed_mask = QLineEdit()
        self.ed_mask.setToolTip(tt("settings.network_mask_tooltip"))
        self._lbl_mask = QLabel(t("settings.network_mask"))
        fw.addRow(self._lbl_mask, self.ed_mask)
        self.ed_gw = QLineEdit()
        self.ed_gw.setToolTip(tt("settings.network_gateway_tooltip"))
        self._lbl_gw = QLabel(t("settings.network_gateway"))
        fw.addRow(self._lbl_gw, self.ed_gw)
        self.ed_dns = QLineEdit()
        self.ed_dns.setToolTip(tt("settings.network_dns_tooltip"))
        self._lbl_dns = QLabel(t("settings.network_dns"))
        fw.addRow(self._lbl_dns, self.ed_dns)
        self.ed_dns2 = QLineEdit()
        self.ed_dns2.setToolTip(tt("settings.network_dns2_tooltip"))
        self._lbl_dns2 = QLabel(t("settings.network_dns2"))
        fw.addRow(self._lbl_dns2, self.ed_dns2)
        right_l.addWidget(self._gb_wan)

        self._gb_sock = QGroupBox(t("settings.network_group_sock"))
        fs = QFormLayout(self._gb_sock)
        self.cb_sock_mode = QComboBox()
        for m, label in (
            ("TCPS", "TCP Server"),
            ("TCPC", "TCP Client"),
            ("UDPS", "UDP Server"),
            ("UDPC", "UDP Client"),
        ):
            self.cb_sock_mode.addItem(label, m)
        self.cb_sock_mode.currentIndexChanged.connect(self._update_sock_fields_visibility)
        self.cb_sock_mode.setToolTip(tt("settings.network_sock_mode_tooltip"))
        self._lbl_sock_mode = QLabel(t("settings.network_sock_mode"))
        fs.addRow(self._lbl_sock_mode, self.cb_sock_mode)
        self.ed_remote_ip = QLineEdit()
        self.ed_remote_ip.setToolTip(tt("settings.network_remote_ip_tooltip"))
        self._lbl_remote_ip = QLabel(t("settings.network_remote_ip"))
        fs.addRow(self._lbl_remote_ip, self.ed_remote_ip)
        self.sp_remote_port = QSpinBox()
        self.sp_remote_port.setRange(1, 65535)
        self.sp_remote_port.setValue(8886)
        # AT-/Daten-Port und Socket-Port sind immer identisch - ein Feld genuegt.
        self.sp_remote_port.valueChanged.connect(self._apply_form_to_current)
        self.sp_remote_port.setToolTip(tt("settings.network_remote_port_tooltip"))
        self._lbl_remote_port = QLabel(t("settings.network_remote_port"))
        fs.addRow(self._lbl_remote_port, self.sp_remote_port)
        self.lbl_model = QLabel("-")
        self._lbl_model = QLabel(t("settings.network_model"))
        fs.addRow(self._lbl_model, self.lbl_model)
        right_l.addWidget(self._gb_sock)
        # Ziel-IP ist nur im Client-Modus sinnvoll (Server wartet auf
        # eingehende Verbindungen und braucht kein Ziel) - initial verstecken,
        # bis _load_form()/_update_sock_fields_visibility() den echten Modus setzt.
        self._update_sock_fields_visibility()

        act = QVBoxLayout()
        act_row1 = QHBoxLayout()
        self._btn_read = QPushButton(t("settings.network_btn_read"))
        self._btn_read.setToolTip(tt("settings.network_btn_read_tooltip"))
        self._btn_read.clicked.connect(self._on_read)
        self._btn_write = QPushButton(t("settings.network_btn_write"))
        self._btn_write.setToolTip(tt("settings.network_btn_write_tooltip"))
        self._btn_write.clicked.connect(self._on_write)
        self._btn_web = QPushButton(t("settings.network_btn_web"))
        self._btn_web.setToolTip(tt("settings.network_btn_web_tooltip"))
        self._btn_web.clicked.connect(self._on_web)
        act_row1.addWidget(self._btn_read)
        act_row1.addWidget(self._btn_write)
        act_row1.addWidget(self._btn_web)
        act_row1.addStretch(1)
        act.addLayout(act_row1)

        self._act_row2_wrap = QWidget()
        act_row2 = QHBoxLayout(self._act_row2_wrap)
        act_row2.setContentsMargins(0, 0, 0, 0)
        self._btn_stats = QPushButton(t("settings.network_btn_stats"))
        self._btn_stats.setToolTip(tt("settings.network_btn_stats_tooltip"))
        self._btn_stats.clicked.connect(self._on_stats)
        act_row2.addWidget(self._btn_stats)
        act_row2.addStretch(1)
        act.addWidget(self._act_row2_wrap)
        right_l.addLayout(act)

        self._lbl_status = QLabel("")
        self._lbl_status.setWordWrap(True)
        right_l.addWidget(self._lbl_status)
        right_l.addStretch(1)
        split.addWidget(right)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 2)
        root.addWidget(split, 1)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self._update_vendor_fields_visibility()
        # Ohne Auswahl sind Detailfelder wirkungslos (_apply_form_to_current
        # speichert nichts) - daher von Anfang an deaktiviert.
        self._set_detail_enabled(False)

    def _fill_vendor_combo(self) -> None:
        self.cb_vendor.blockSignals(True)
        self.cb_vendor.clear()
        self.cb_vendor.addItem(t("settings.network_vendor_ne2"), VENDOR_NE2)
        self.cb_vendor.addItem(t("settings.network_vendor_na11x"), VENDOR_NA11X)
        self.cb_vendor.addItem(t("settings.network_vendor_usr"), VENDOR_USR)
        self.cb_vendor.addItem(t("settings.network_vendor_dk8de"), VENDOR_DK8DE)
        self.cb_vendor.addItem(t("settings.network_vendor_generic"), VENDOR_GENERIC)
        self.cb_vendor.blockSignals(False)

    # ----------------------------------------------------------- show/hide
    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        if not self._status_timer.isActive():
            self._status_timer.start()
            QTimer.singleShot(0, self._tick_probe)

    def hideEvent(self, event) -> None:  # type: ignore[override]
        self._status_timer.stop()
        super().hideEvent(event)

    # ----------------------------------------------------------- cfg sync
    def load_from_cfg(self) -> None:
        self._modules = modules_from_cfg(self._cfg)
        self._online = [False] * len(self._modules)
        self._offline_streak = [0] * len(self._modules)
        self._persisted_ids = {id(m) for m in self._modules}
        self._rebuild_list()
        # Beim Oeffnen bewusst keine Vorauswahl treffen: der Nutzer soll aktiv
        # ein Modul anklicken, statt dass eines blau markiert ist, dessen
        # Infos rechts aber (noch) nicht angezeigt/ausgelesen sind.
        self._list.setCurrentRow(-1)
        self._clear_form()
        self._set_detail_enabled(False)

    def apply_to_cfg(self, cfg: dict) -> None:
        self._apply_form_to_current()
        cfg["network_modules"] = modules_to_cfg(self._modules)
        # Nach Speichern gelten alle aktuellen Module als persistiert
        self._persisted_ids = {id(m) for m in self._modules}
        # Legacy-Key beibehalten (nicht mehr fuer die Suche genutzt)
        scan = cfg.setdefault("network_scan", {})
        if not isinstance(scan, dict):
            scan = {}
            cfg["network_scan"] = scan
        scan.setdefault("ports", [8886, 8899, 80])
        scan.setdefault("enabled", True)

    # ----------------------------------------------------------- list
    def _rebuild_list(self) -> None:
        self._suppress = True
        row = self._list.currentRow()
        self._list.clear()
        self._leds = []
        for i, m in enumerate(self._modules):
            item = QListWidgetItem()
            wrap = QWidget()
            hl = QHBoxLayout(wrap)
            hl.setContentsMargins(4, 2, 4, 2)
            led = Led(max(8, px_to_dip(self, 10)), wrap)
            online = self._online[i] if i < len(self._online) else False
            led.set_state(online)
            self._leds.append(led)
            lbl = QLabel(self._module_label(m))
            hl.addWidget(led, 0)
            hl.addWidget(lbl, 1)
            item.setSizeHint(wrap.sizeHint())
            self._list.addItem(item)
            self._list.setItemWidget(item, wrap)
        self._suppress = False
        if 0 <= row < self._list.count():
            self._list.setCurrentRow(row)

    def _module_label(self, m: NetworkModule) -> str:
        name = (m.name or "").strip() or t("settings.network_unnamed")
        host = (m.host or "").strip() or "?"
        if m.vendor == VENDOR_DK8DE and (m.uid or "").strip():
            return f"{name}  ({host} · {m.uid.strip().upper()})"
        return f"{name}  ({host}:{m.at_port})"

    def _on_sel_changed(self, row: int) -> None:
        if self._suppress:
            return
        if row < 0 or row >= len(self._modules):
            self._clear_form()
            self._set_detail_enabled(False)
            return
        self._load_form(self._modules[row])
        self._set_detail_enabled(True)
        self._maybe_auto_read(row)

    def _module_is_persisted(self, m: NetworkModule) -> bool:
        return id(m) in self._persisted_ids

    def _maybe_auto_read(self, row: int) -> None:
        """Beim Wechsel: gespeicherte Module automatisch auslesen.

        Bewusst UNABHAENGIG vom aktuellen LED-Status: direkt nach dem
        Oeffnen der Einstellungen (oder nach erneutem Anzeigen des Fensters)
        ist ``_online`` erst einmal auf False zurueckgesetzt, bis die
        periodische Status-Probe (alle 4s) das erste Mal durchgelaufen ist.
        Wuerde man hier auf "bereits online" warten, bliebe die LED beim
        Auswaehlen eines tatsaechlich erreichbaren Moduls unnoetig lange rot,
        bis der naechste Probe-Tick zufaellig durchkommt.
        """
        if row < 0 or row >= len(self._modules):
            return
        m = self._modules[row]
        if not self._module_is_persisted(m):
            return
        if not (m.host or "").strip():
            return
        self._start_read(interactive=False)

    def _current(self) -> Optional[NetworkModule]:
        row = self._list.currentRow()
        if row < 0 or row >= len(self._modules):
            return None
        return self._modules[row]

    def _set_detail_enabled(self, enabled: bool) -> None:
        """Detailformular und Aktionsbuttons nur bei gewaehltem Modul aktiv.

        Ohne Auswahl wuerden Eingaben still verworfen (kein Ziel-Modul).
        Hinzufuegen/Suche bleiben immer aktiv.
        """
        self._form_enabled = bool(enabled)
        widgets = (
            self._gb_ident,
            self._gb_wan,
            self._gb_sock,
            self.ed_name,
            self.cb_vendor,
            self.sp_web_port,
            self.ed_netat,
            self.ed_cmdpw,
            self.ed_web_user,
            self.ed_web_password,
            self.ed_uid,
            self.sp_config_port,
            self.cb_wan_mode,
            self.ed_ip,
            self.ed_mask,
            self.ed_gw,
            self.ed_dns,
            self.ed_dns2,
            self.cb_sock_mode,
            self.ed_remote_ip,
            self.sp_remote_port,
            self._btn_read,
            self._btn_write,
            self._btn_web,
            self._btn_stats,
            self._btn_remove,
        )
        for w in widgets:
            w.setEnabled(enabled)
        # Ziel-IP zusaetzlich vom Socket-Modus abhaengig
        if enabled:
            self._update_sock_fields_visibility()
        else:
            self.ed_remote_ip.setEnabled(False)

    def _clear_form(self) -> None:
        self._suppress = True
        self.ed_name.clear()
        self.sp_web_port.setValue(80)
        self.ed_netat.setText("NETAT")
        self.ed_cmdpw.setText("USR")
        self.ed_web_user.setText("admin")
        self.ed_web_password.setText("admin")
        self.ed_uid.clear()
        self.sp_config_port.setValue(8880)
        self.cb_wan_mode.setCurrentIndex(0)
        self.ed_ip.clear()
        self.ed_mask.clear()
        self.ed_gw.clear()
        self.ed_dns.clear()
        self.ed_dns2.clear()
        self.cb_sock_mode.setCurrentIndex(0)
        self.ed_remote_ip.clear()
        self.sp_remote_port.setValue(8886)
        self.lbl_model.setText("-")
        self._update_sock_fields_visibility()
        self._suppress = False

    def _load_form(self, m: NetworkModule) -> None:
        self._suppress = True
        self.ed_name.setText(m.name)
        idx = self.cb_vendor.findData(m.vendor)
        self.cb_vendor.setCurrentIndex(idx if idx >= 0 else 0)
        self.sp_web_port.setValue(int(m.web_port))
        self.ed_netat.setText(m.netat_header or "NETAT")
        self.ed_cmdpw.setText(m.cmdpw or "USR")
        self.ed_web_user.setText(m.web_user or "admin")
        self.ed_web_password.setText(
            m.web_password if m.web_password is not None else "admin"
        )
        self.ed_uid.setText(m.uid or "")
        self.sp_config_port.setValue(int(m.config_port or DEFAULT_CONFIG_PORTS.get(m.vendor, 8880)))
        if m.vendor == VENDOR_DK8DE and not (m.contact_host or "").strip() and (m.host or "").strip():
            m.contact_host = m.host.strip()
        st = m.last_status or {}
        wan = st.get("wan") or {}
        sock = st.get("sock") or {}
        wm = str(wan.get("mode", "") or "").upper()
        widx = self.cb_wan_mode.findData(wm if wm in ("STATIC", "DHCP") else "STATIC")
        self.cb_wan_mode.setCurrentIndex(widx if widx >= 0 else 0)
        # Host (Verbindungsadresse) und die vom Modul gemeldete WAN-IP sind
        # in der Praxis immer identisch - bevorzugt m.host zeigen, da dieses
        # Feld auch ohne vorheriges "Lesen" bereits gesetzt ist.
        self.ed_ip.setText(m.host or str(wan.get("ip", "") or ""))
        self.ed_mask.setText(str(wan.get("mask", "") or ""))
        self.ed_gw.setText(str(wan.get("gateway", "") or ""))
        self.ed_dns.setText(str(wan.get("dns", "") or ""))
        self.ed_dns2.setText(str(wan.get("dns2", "") or ""))
        sm = str(sock.get("mode", "") or "TCPS").upper()
        sidx = self.cb_sock_mode.findData(sm)
        self.cb_sock_mode.setCurrentIndex(sidx if sidx >= 0 else 0)
        self.ed_remote_ip.setText(str(sock.get("remote_ip", "") or ""))
        # Socket-Port und AT-/Daten-Port sind immer gleich - ein Wert.
        try:
            port = int(sock.get("remote_port") or m.at_port or 8886)
        except (TypeError, ValueError):
            port = int(m.at_port) if m.at_port else 8886
        self.sp_remote_port.setValue(port if 1 <= port <= 65535 else 8886)
        self._update_sock_fields_visibility()
        model = str(st.get("model", "") or "")
        mac = str(st.get("mac", "") or "")
        self.lbl_model.setText(f"{model}  {mac}".strip() or "-")
        self._update_vendor_fields_visibility()
        self._suppress = False

    def _apply_form_to_current(self, *_args) -> None:
        if self._suppress:
            return
        m = self._current()
        if m is None:
            return
        m.name = self.ed_name.text().strip()
        m.vendor = str(self.cb_vendor.currentData() or VENDOR_GENERIC)
        m.host = self.ed_ip.text().strip()
        # AT-/Daten-Port = Socket Ziel-/Listen-Port (immer identisch).
        m.at_port = int(self.sp_remote_port.value())
        m.web_port = int(self.sp_web_port.value())
        m.netat_header = self.ed_netat.text().strip() or "NETAT"
        m.cmdpw = self.ed_cmdpw.text().strip() or "USR"
        m.web_user = self.ed_web_user.text().strip() or "admin"
        m.web_password = self.ed_web_password.text()
        if m.vendor == VENDOR_DK8DE:
            m.uid = self.ed_uid.text().strip().upper()
            m.config_port = int(self.sp_config_port.value())
        else:
            m.uid = ""
            m.config_port = DEFAULT_CONFIG_PORTS.get(VENDOR_DK8DE, 8880)
        # Liste-Label aktualisieren
        row = self._list.currentRow()
        if 0 <= row < self._list.count():
            item = self._list.item(row)
            w = self._list.itemWidget(item)
            if w is not None:
                for child in w.findChildren(QLabel):
                    child.setText(self._module_label(m))
                    break

    def _on_vendor_changed(self, *_args) -> None:
        if self._suppress:
            return
        vendor = str(self.cb_vendor.currentData() or VENDOR_GENERIC)
        self.sp_remote_port.blockSignals(True)
        self.sp_web_port.blockSignals(True)
        self.sp_config_port.blockSignals(True)
        self.sp_remote_port.setValue(DEFAULT_AT_PORTS.get(vendor, 8886))
        self.sp_web_port.setValue(DEFAULT_WEB_PORTS.get(vendor, 80))
        self.sp_config_port.setValue(DEFAULT_CONFIG_PORTS.get(vendor, 8880))
        if vendor == VENDOR_DK8DE:
            self.ed_web_password.setText("Rotorconfig")
        self.sp_remote_port.blockSignals(False)
        self.sp_web_port.blockSignals(False)
        self.sp_config_port.blockSignals(False)
        self._update_vendor_fields_visibility()
        self._apply_form_to_current()

    def _update_vendor_fields_visibility(self) -> None:
        vendor = str(self.cb_vendor.currentData() or VENDOR_GENERIC)
        is_usr = vendor == VENDOR_USR
        is_dk8de = vendor == VENDOR_DK8DE
        use_web = vendor in (VENDOR_NE2, VENDOR_NA11X, VENDOR_GENERIC, VENDOR_USR, VENDOR_DK8DE)
        self._lbl_cmdpw.setVisible(is_usr)
        self.ed_cmdpw.setVisible(is_usr)
        self._lbl_netat.setVisible(not is_usr and not is_dk8de)
        self.ed_netat.setVisible(not is_usr and not is_dk8de)
        self._lbl_uid.setVisible(is_dk8de)
        self.ed_uid.setVisible(is_dk8de)
        self._lbl_config_port.setVisible(is_dk8de)
        self.sp_config_port.setVisible(is_dk8de)
        self._btn_stats.setVisible(is_dk8de)
        self._act_row2_wrap.setVisible(is_dk8de)
        self._lbl_web_user.setVisible(use_web)
        self.ed_web_user.setVisible(use_web)
        self._lbl_web_password.setVisible(use_web)
        self.ed_web_password.setVisible(use_web)
        self._lbl_dns2.setVisible(vendor == VENDOR_NE2)
        self.ed_dns2.setVisible(vendor == VENDOR_NE2)

    def _update_sock_fields_visibility(self) -> None:
        """Ziel-IP nur im Client-Modus zeigen/aktivieren.

        Im Server-Modus (TCPS/UDPS) wartet das Modul auf eingehende
        Verbindungen - eine Ziel-Adresse ist dann ohne Bedeutung und wuerde
        nur verwirren. Der Port bleibt in beiden Faellen sichtbar, da er im
        Client-Modus als Ziel-Port und im Server-Modus als lokaler
        Listen-Port verwendet wird (siehe Tooltip).
        """
        mode = str(self.cb_sock_mode.currentData() or "TCPS").upper()
        is_client = mode in ("TCPC", "UDPC")
        self._lbl_remote_ip.setVisible(is_client)
        self.ed_remote_ip.setVisible(is_client)
        self.ed_remote_ip.setEnabled(bool(getattr(self, "_form_enabled", False)) and is_client)

    # ----------------------------------------------------------- actions
    def _on_add(self) -> None:
        self._apply_form_to_current()
        m = NetworkModule(
            name=t("settings.network_new_name"),
            vendor=VENDOR_NE2,
            host="",
            at_port=8886,
            web_port=80,
            role="bus_gateway",
        )
        self._modules.append(m)
        self._online.append(False)
        self._offline_streak.append(0)
        self._rebuild_list()
        self._list.setCurrentRow(len(self._modules) - 1)
        self.save_requested.emit()

    def _on_remove(self) -> None:
        row = self._list.currentRow()
        if row < 0 or row >= len(self._modules):
            return
        del self._modules[row]
        if row < len(self._online):
            del self._online[row]
        if row < len(self._offline_streak):
            del self._offline_streak[row]
        self._rebuild_list()
        if self._modules:
            self._list.setCurrentRow(min(row, len(self._modules) - 1))
        else:
            self._clear_form()
            self._set_detail_enabled(False)
        self.save_requested.emit()

    def _on_web(self) -> None:
        self._apply_form_to_current()
        m = self._current()
        if m is None or not m.host.strip():
            QMessageBox.information(self, t("settings.network_tab"), t("settings.network_need_host"))
            return
        url = f"http://{m.host.strip()}:{int(m.web_port)}/"
        QDesktopServices.openUrl(QUrl(url))

    def _on_read(self) -> None:
        self._start_read(interactive=True)

    def _start_read(self, *, interactive: bool = True) -> None:
        m = self._current()
        if m is None:
            if interactive:
                QMessageBox.information(self, t("settings.network_tab"), t("settings.network_need_host"))
            return
        at_host = str(m.contact_host or m.host or "").strip()
        self._apply_form_to_current()
        m = self._current()
        if m is None or not m.host.strip():
            if interactive:
                QMessageBox.information(self, t("settings.network_tab"), t("settings.network_need_host"))
            return
        if m.vendor == VENDOR_DK8DE and not (m.uid or "").strip():
            if interactive:
                QMessageBox.information(self, t("settings.network_tab"), t("settings.network_need_uid"))
            return
        if self._read_worker and self._read_worker.isRunning():
            return
        self._read_interactive = interactive
        self._lbl_status.setText(t("settings.network_status_reading"))
        self._btn_read.setEnabled(False)
        self._read_worker = _ReadWorker(m, self, at_host=at_host)
        self._read_worker.finished_ok.connect(self._on_read_ok)
        self._read_worker.finished_err.connect(self._on_read_err)
        self._read_worker.finished.connect(
            lambda: self._btn_read.setEnabled(self._form_enabled)
        )
        self._read_worker.start()

    def _on_read_ok(self, st: object) -> None:
        status = st if isinstance(st, dict) else {}
        m = self._current()
        if m is not None:
            m.last_status = status
            if m.vendor == VENDOR_DK8DE:
                uid = str(status.get("uid") or "").strip().upper()
                if uid:
                    m.uid = uid
                wan = status.get("wan") if isinstance(status.get("wan"), dict) else {}
                live_ip = str(wan.get("ip") or m.host or "").strip()
                if live_ip and live_ip not in ("0.0.0.0", "-"):
                    m.contact_host = live_ip
                    m.host = live_ip
            sock = status.get("sock") if isinstance(status.get("sock"), dict) else {}
            try:
                lp = int(sock.get("remote_port") or sock.get("local_port") or 0)
                if 1 <= lp <= 65535:
                    m.at_port = lp
            except (TypeError, ValueError):
                pass
            try:
                wp = int(sock.get("web_port") or 0)
                if 1 <= wp <= 65535:
                    m.web_port = wp
            except (TypeError, ValueError):
                pass
            self._load_form(m)
            # Ein erfolgreiches Auslesen beweist, dass das Modul online ist -
            # LED sofort grün setzen, statt auf den naechsten periodischen
            # Probe-Tick (bis zu 4s) zu warten.
            row = self._list.currentRow()
            if 0 <= row < len(self._online):
                self._online[row] = True
            if 0 <= row < len(self._offline_streak):
                self._offline_streak[row] = 0
            if 0 <= row < len(self._leds):
                self._leds[row].set_state(True)
        self._lbl_status.setText(t("settings.network_status_read_ok"))
        if self._save_after_read:
            self._save_after_read = False
            self.save_requested.emit()

    def _on_read_err(self, msg: str) -> None:
        self._lbl_status.setText(t("settings.network_status_error", err=msg))
        if self._read_interactive:
            QMessageBox.warning(self, t("settings.network_tab"), t("settings.network_read_failed", err=msg))
        if self._save_after_read:
            self._save_after_read = False
            self.save_requested.emit()

    def _on_write(self) -> None:
        m = self._current()
        if m is None or not m.host.strip():
            QMessageBox.information(self, t("settings.network_tab"), t("settings.network_need_host"))
            return
        if m.vendor == VENDOR_DK8DE and not (m.uid or "").strip():
            QMessageBox.information(self, t("settings.network_tab"), t("settings.network_need_uid"))
            return
        at_host = str(m.contact_host or m.host or "").strip()
        wan = {
            "mode": str(self.cb_wan_mode.currentData() or "STATIC"),
            "ip": self.ed_ip.text().strip(),
            "mask": self.ed_mask.text().strip(),
            "gateway": self.ed_gw.text().strip(),
            "dns": self.ed_dns.text().strip(),
            "dns2": self.ed_dns2.text().strip(),
        }
        sock = {
            "mode": str(self.cb_sock_mode.currentData() or "TCPS"),
            "remote_ip": self.ed_remote_ip.text().strip(),
            "remote_port": int(self.sp_remote_port.value()),
        }
        reply = QMessageBox.question(
            self,
            t("settings.network_tab"),
            t("settings.network_write_confirm"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._apply_form_to_current()
        m = self._current()
        if m is None:
            return
        if self._write_worker and self._write_worker.isRunning():
            return
        self._lbl_status.setText(t("settings.network_status_writing"))
        self._btn_write.setEnabled(False)
        self._write_worker = _WriteWorker(m, wan, sock, self, at_host=at_host)
        self._write_worker.finished_ok.connect(self._on_write_ok)
        self._write_worker.finished_err.connect(self._on_write_err)
        self._write_worker.finished.connect(self._on_write_finished)
        self._write_worker.start()

    def _on_write_finished(self) -> None:
        self._btn_write.setEnabled(self._form_enabled)
        self._write_worker = None

    def _on_write_ok(self, _res: object) -> None:
        # Nach Reboot: Host ggf. auf neue IP setzen
        new_ip = self.ed_ip.text().strip()
        m = self._current()
        if m is not None and new_ip and str(self.cb_wan_mode.currentData()) == "STATIC":
            m.host = new_ip
            if m.vendor == VENDOR_DK8DE:
                m.contact_host = new_ip
            self._rebuild_list()
        self._lbl_status.setText(t("settings.network_status_write_ok"))
        # NE2 startet laut Mitschnitt des Original-Tools nach dem Schreiben
        # NICHT automatisch neu, auch wenn hier "reboot=True" angefordert
        # wird - der Nutzer muss das Modul manuell/physisch neu starten,
        # damit z. B. eine geaenderte Gateway-Adresse aktiv wird.
        if m is not None and str(m.vendor or "").strip().lower() == VENDOR_NE2:
            QMessageBox.information(
                self,
                t("settings.network_tab"),
                t("settings.network_write_ok_manual_reboot_ne2"),
            )
        elif m is not None and m.vendor == VENDOR_DK8DE:
            QMessageBox.information(
                self,
                t("settings.network_tab"),
                t("settings.network_write_ok_dk8de_reboot"),
            )

    def _on_stats(self) -> None:
        self._apply_form_to_current()
        m = self._current()
        if m is None or m.vendor != VENDOR_DK8DE:
            return
        if not m.host.strip():
            QMessageBox.information(self, t("settings.network_tab"), t("settings.network_need_host"))
            return
        if not (m.uid or "").strip():
            QMessageBox.information(self, t("settings.network_tab"), t("settings.network_need_uid"))
            return
        dlg = _Dk8deStatsDialog(m, self)
        dlg.exec()

    def _on_write_err(self, msg: str) -> None:
        self._lbl_status.setText(t("settings.network_status_error", err=msg))
        QMessageBox.warning(self, t("settings.network_tab"), t("settings.network_write_failed", err=msg))

    def _on_scan(self) -> None:
        w = self._scan_worker
        if w is not None:
            try:
                if w.isRunning():
                    return
            except RuntimeError:
                self._scan_worker = None
        self._lbl_status.setText(t("settings.network_status_scanning"))
        self._btn_scan.setEnabled(False)
        parent_win = self.window()
        # Fortschritt an Hauptfenster haengen — in QScrollArea sonst oft sofort wieder weg.
        self._scan_progress = QProgressDialog(
            t("settings.network_status_scanning"), "", 0, 0, parent_win
        )
        self._scan_progress.setWindowTitle(t("settings.network_discover_scan_title"))
        self._scan_progress.setCancelButton(None)
        self._scan_progress.setWindowModality(Qt.WindowModality.ApplicationModal)
        self._scan_progress.setMinimumDuration(0)
        self._scan_progress.show()
        self._scan_worker = _ScanWorker(timeout=5.0, parent=self)
        self._scan_worker.finished_ok.connect(self._on_scan_ok)
        self._scan_worker.finished_err.connect(self._on_scan_err)
        self._scan_worker.finished.connect(self._on_scan_finished)
        self._scan_worker.start()

    def _on_scan_finished(self) -> None:
        self._btn_scan.setEnabled(True)
        self._scan_worker = None

    def _close_scan_progress(self) -> None:
        dlg = getattr(self, "_scan_progress", None)
        if dlg is not None:
            try:
                dlg.close()
            except RuntimeError:
                pass
            self._scan_progress = None

    def _on_scan_ok(self, found: object) -> None:
        self._close_scan_progress()
        ebyte: List[EbyteDevice] = []
        dk8de: List[Dk8deDevice] = []
        if isinstance(found, dict):
            ebyte = [d for d in (found.get("ebyte") or []) if isinstance(d, EbyteDevice)]
            dk8de = [d for d in (found.get("dk8de") or []) if isinstance(d, Dk8deDevice)]
        if not ebyte and not dk8de:
            self._lbl_status.setText(t("settings.network_status_scan_empty"))
            QMessageBox.information(
                self.window(),
                t("settings.network_discover_scan_title"),
                t("settings.network_status_scan_empty"),
            )
            return
        self._lbl_status.setText(
            t("settings.network_status_scan_done", found=len(ebyte) + len(dk8de))
        )
        try:
            self._discover_dlg = _NetworkDiscoverDialog(ebyte, dk8de, self.window())
            self._discover_dlg.adopt_ebyte.connect(self._adopt_ebyte_device)
            self._discover_dlg.adopt_dk8de.connect(self._adopt_dk8de_device)
            self._discover_dlg.ip_set_done.connect(self._on_discover_ip_set)
            self._discover_dlg.exec()
        except Exception as exc:
            QMessageBox.critical(
                self.window(),
                t("settings.network_discover_title"),
                t("settings.network_discover_set_ip_failed", err=str(exc)),
            )
        finally:
            self._discover_dlg = None

    def _adopt_ebyte_device(self, device: object) -> None:
        if not isinstance(device, EbyteDevice):
            return
        host = (device.ip or "").strip()
        if not host:
            QMessageBox.warning(
                self,
                t("settings.network_discover_title"),
                t("settings.network_discover_no_ip"),
            )
            return
        vendor = device.vendor if device.vendor in (VENDOR_NE2, VENDOR_NA11X) else VENDOR_NE2
        existing = {(m.host.strip(), int(m.at_port)) for m in self._modules}
        at_port = DEFAULT_AT_PORTS.get(vendor, 8886)
        if (host, at_port) in existing:
            self._lbl_status.setText(
                t("settings.network_discover_already", host=host)
            )
            return
        name = (device.model or vendor.upper()).strip() or t("settings.network_new_name")
        m = NetworkModule(
            name=f"{name} {host}",
            vendor=vendor,
            host=host,
            at_port=at_port,
            web_port=DEFAULT_WEB_PORTS.get(vendor, 80),
            role="bus_gateway",
        )
        self._modules.append(m)
        self._online.append(True)
        self._offline_streak.append(0)
        self._rebuild_list()
        self._list.setCurrentRow(len(self._modules) - 1)
        self._lbl_status.setText(
            t("settings.network_discover_adopted", model=name, ip=host)
        )
        # Direkt auslesen, damit alle Infos (WAN/Socket/Firmware) sofort
        # sichtbar sind, statt manuell auf "Lesen" klicken zu muessen. Das
        # eigentliche Speichern erfolgt erst, wenn das Auslesen abgeschlossen
        # ist (siehe _on_read_ok/_on_read_err), damit auch korrigierte Ports
        # aus dem Geraet mit gespeichert werden.
        self._save_after_read = True
        self._start_read(interactive=False)
        self.save_requested.emit()

    def _adopt_dk8de_device(self, device: object) -> None:
        if not isinstance(device, Dk8deDevice):
            return
        host = (device.ip or "").strip()
        uid = device.uid_norm
        if not host or host in ("0.0.0.0", "-"):
            QMessageBox.warning(
                self,
                t("settings.network_discover_title"),
                t("settings.network_discover_dk8de_no_ip"),
            )
            return
        if not uid:
            QMessageBox.warning(
                self,
                t("settings.network_discover_title"),
                t("settings.network_discover_dk8de_no_uid"),
            )
            return
        existing = {(m.host.strip(), m.uid.strip().upper()) for m in self._modules if m.vendor == VENDOR_DK8DE}
        if (host, uid) in existing:
            self._lbl_status.setText(t("settings.network_discover_already", host=host))
            return
        label = (device.name or f"DK8DE {uid}").strip()
        m = NetworkModule(
            name=f"{label} {host}",
            vendor=VENDOR_DK8DE,
            host=host,
            contact_host=host,
            uid=uid,
            at_port=int(device.lport or DEFAULT_AT_PORTS[VENDOR_DK8DE]),
            config_port=DEFAULT_CONFIG_PORTS[VENDOR_DK8DE],
            web_port=DEFAULT_WEB_PORTS[VENDOR_DK8DE],
            web_user="admin",
            web_password="Rotorconfig",
            role="bus_gateway",
        )
        self._modules.append(m)
        self._online.append(True)
        self._offline_streak.append(0)
        self._rebuild_list()
        self._list.setCurrentRow(len(self._modules) - 1)
        self._lbl_status.setText(
            t("settings.network_discover_adopted_dk8de", name=label, ip=host, uid=uid)
        )
        self._save_after_read = True
        self._start_read(interactive=False)
        self.save_requested.emit()

    def _on_discover_ip_set(self, device: object, old_ip: str, new_ip: str) -> None:
        ip = str(new_ip or "").strip()
        prev = str(old_ip or "").strip()
        if not ip:
            return
        if isinstance(device, EbyteDevice):
            for m in self._modules:
                if (prev and m.host == prev) or m.host == ip:
                    m.host = ip
            device.ip = ip
            self._rebuild_list()
        elif isinstance(device, Dk8deDevice):
            uid = device.uid_norm
            for m in self._modules:
                if m.vendor != VENDOR_DK8DE:
                    continue
                if uid and m.uid.strip().upper() == uid:
                    m.host = ip
                    m.contact_host = ip
                elif (prev and m.host == prev) or m.host == ip:
                    m.host = ip
                    m.contact_host = ip
            device.ip = ip
            self._rebuild_list()

    def _on_scan_err(self, msg: str) -> None:
        self._close_scan_progress()
        self._lbl_status.setText(t("settings.network_status_error", err=msg))
        QMessageBox.warning(
            self.window(),
            t("settings.network_discover_scan_title"),
            t("settings.network_discover_set_ip_failed", err=msg),
        )

    # ----------------------------------------------------------- probe
    def _tick_probe(self) -> None:
        if self._probe_worker and self._probe_worker.isRunning():
            return
        if not self._modules:
            return
        # Waehrend Lesen/Schreiben/Suchen pausieren: manche Module (schwacher
        # Embedded-TCP-Stack, begrenzte Anzahl gleichzeitiger Verbindungen)
        # geraten bei paralleler Status-Abfrage + Web-Login sonst in einen
        # Zustand, in dem der Web-Login (401 auf /login.json /login.js)
        # fehlschlaegt, obwohl das Modul online ist.
        for w in (self._read_worker, self._write_worker, self._scan_worker):
            if w is not None and w.isRunning():
                return
        # Formular zuerst in Module schreiben, damit Host/Port aktuell sind
        self._apply_form_to_current()
        self._probe_worker = _ProbeWorker(list(self._modules), self)
        self._probe_worker.results.connect(self._on_probe_results)
        self._probe_worker.start()

    # Anzahl aufeinanderfolgender Fehlschlaege, bevor die LED wirklich auf
    # "offline" wechselt (Debounce gegen einzelne verlorene/verzoegerte
    # Connects bei schwachen Embedded-TCP-Stacks).
    _OFFLINE_STREAK_LIMIT = 2

    def _on_probe_results(self, results: object) -> None:
        while len(self._offline_streak) < len(self._modules):
            self._offline_streak.append(0)
        while len(self._online) < len(self._modules):
            self._online.append(False)
        # Zuordnung ueber das Modul-Objekt selbst (nicht ueber die Position):
        # Wenn waehrend der laufenden Pruefung Module geloescht/hinzugefuegt
        # wurden, stimmen Listenpositionen nicht mehr mit der Momentaufnahme
        # des Workers ueberein. Ergebnisse fuer inzwischen entfernte Module
        # werden einfach verworfen, statt auf ein falsches (z. B. neu
        # hinzugefuegtes) Modul angewendet zu werden.
        index_by_id = {id(m): i for i, m in enumerate(self._modules)}
        pairs = results if isinstance(results, list) else []
        for entry in pairs:
            if not isinstance(entry, tuple) or len(entry) != 2:
                continue
            module, is_up = entry
            i = index_by_id.get(id(module))
            if i is None:
                continue
            if is_up:
                self._offline_streak[i] = 0
                self._online[i] = True
            else:
                self._offline_streak[i] += 1
                if self._offline_streak[i] >= self._OFFLINE_STREAK_LIMIT:
                    self._online[i] = False
        for i, led in enumerate(self._leds):
            if i < len(self._online):
                led.set_state(self._online[i])

    # ----------------------------------------------------------- i18n
    def retranslate(self) -> None:
        self._lbl_intro.setText(t("settings.network_intro"))
        self._gb_ident.setTitle(t("settings.network_group_ident"))
        self._gb_wan.setTitle(t("settings.network_group_wan"))
        self._gb_sock.setTitle(t("settings.network_group_sock"))
        self._btn_add.setText(t("settings.network_btn_add"))
        self._btn_remove.setText(t("settings.network_btn_remove"))
        self._btn_scan.setText(t("settings.network_btn_scan"))
        self._btn_scan.setToolTip(tt("settings.network_btn_scan_tooltip"))
        self._btn_read.setText(t("settings.network_btn_read"))
        self._btn_read.setToolTip(tt("settings.network_btn_read_tooltip"))
        self._btn_write.setText(t("settings.network_btn_write"))
        self._btn_write.setToolTip(tt("settings.network_btn_write_tooltip"))
        self._btn_web.setText(t("settings.network_btn_web"))
        self._btn_web.setToolTip(tt("settings.network_btn_web_tooltip"))
        self._btn_stats.setText(t("settings.network_btn_stats"))
        self._btn_stats.setToolTip(tt("settings.network_btn_stats_tooltip"))
        self._lbl_vendor.setText(t("settings.network_vendor"))
        self.cb_vendor.setToolTip(tt("settings.network_vendor_tooltip"))
        self._lbl_name.setText(t("settings.network_name"))
        self.ed_name.setToolTip(tt("settings.network_name_tooltip"))
        self._lbl_web_port.setText(t("settings.network_web_port"))
        self.sp_web_port.setToolTip(tt("settings.network_web_port_tooltip"))
        self._lbl_netat.setText(t("settings.network_netat_header"))
        self.ed_netat.setToolTip(tt("settings.network_netat_header_tooltip"))
        self._lbl_cmdpw.setText(t("settings.network_cmdpw"))
        self.ed_cmdpw.setToolTip(tt("settings.network_cmdpw_tooltip"))
        self._lbl_web_user.setText(t("settings.network_web_user"))
        self.ed_web_user.setToolTip(tt("settings.network_web_user_tooltip"))
        self._lbl_web_password.setText(t("settings.network_web_password"))
        self.ed_web_password.setToolTip(tt("settings.network_web_password_tooltip"))
        self._lbl_wan_mode.setText(t("settings.network_wan_mode"))
        self.cb_wan_mode.setToolTip(tt("settings.network_wan_mode_tooltip"))
        self._lbl_ip.setText(t("settings.network_host"))
        self.ed_ip.setToolTip(tt("settings.network_host_tooltip"))
        self._lbl_mask.setText(t("settings.network_mask"))
        self.ed_mask.setToolTip(tt("settings.network_mask_tooltip"))
        self._lbl_gw.setText(t("settings.network_gateway"))
        self.ed_gw.setToolTip(tt("settings.network_gateway_tooltip"))
        self._lbl_dns.setText(t("settings.network_dns"))
        self.ed_dns.setToolTip(tt("settings.network_dns_tooltip"))
        self._lbl_dns2.setText(t("settings.network_dns2"))
        self.ed_dns2.setToolTip(tt("settings.network_dns2_tooltip"))
        self._lbl_sock_mode.setText(t("settings.network_sock_mode"))
        self.cb_sock_mode.setToolTip(tt("settings.network_sock_mode_tooltip"))
        self._lbl_remote_ip.setText(t("settings.network_remote_ip"))
        self.ed_remote_ip.setToolTip(tt("settings.network_remote_ip_tooltip"))
        self._lbl_remote_port.setText(t("settings.network_remote_port"))
        self.sp_remote_port.setToolTip(tt("settings.network_remote_port_tooltip"))
        self._lbl_model.setText(t("settings.network_model"))
        cur_v = self.cb_vendor.currentData()
        self._fill_vendor_combo()
        vi = self.cb_vendor.findData(cur_v)
        if vi >= 0:
            self.cb_vendor.setCurrentIndex(vi)
        self._rebuild_list()
