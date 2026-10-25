"""Inbetriebnahme-Wizard (Aktionen → Inbetriebnahme)."""

from __future__ import annotations

import copy
from typing import Any, Callable, Optional

from PySide6.QtCore import QObject, Qt, QTimer, Signal, Slot
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
    QWizard,
    QWizardPage,
)

from ..app_icon import get_app_icon
from ..i18n import t, tt
from ..ports import list_serial_port_entries
from ..rotor_controller import SYNC_UI_NAK_PREFIX
from ..profile_store import (
    DEFAULT_PROFILE_ID,
    create_profile,
    delete_profile,
    get_active_profile_id,
    get_active_profile_meta,
    list_profiles,
    load_profile_config,
    save_profile_config,
)
from .antenna_names_panel import AntennaNamesPanel, sanitize_antenna_name
from .location_panel import LocationPanel
from .protocols_services_panel import ProtocolsServicesPanel
from .rotor_bus_ids_panel import RotorBusIdsPanel
from .rotor_link_panel import RotorLinkPanel
from .settings_controller_tab import ControllerHwTab
from .settings_network_tab import NetworkModulesTab
from .settings_weather_tab import WeatherThresholdsTab
from ..axis_link import axis_endpoint_from_cfg, ensure_axis_tcp_link
from ..rs485_protocol import BROADCAST_DST
from ..controller_hw_io import (
    apply_settings_to_controller_tab,
    ensure_controller_link,
    profile_rotor_ids,
    read_controller_settings,
    write_controller_rotor_ids,
)
from ..rotor_setup_steps import (
    DRIVE_GET_FIELDS,
    DRIVE_SET_SEQUENCE,
    ENCODER_TYPE_CHOICES,
    STEP_ALERTS,
    STEP_ANTENNA,
    STEP_CAL,
    STEP_DONE,
    STEP_DRIVE,
    STEP_ENCODER,
    STEP_ENDSTOPS,
    STEP_SERVICES,
    STEP_TEMP,
    STEP_VERIFY,
    STEP_WEATHER,
    STEP_ZERO_REF,
    cpr_for_range,
    drive_defaults,
    endstc_choices,
    endstc_recommendation,
    homing_setref_allowed,
    inclinometer_cal_allowed,
    jog_allowed,
    manual_setref_allowed,
    max_dg_for_rotor_type,
    parse_encoder_type,
    parse_endstc,
    setenczero_primary,
    weather_allowed,
    wind_alerts_allowed,
)


def _scroll(inner: QWidget) -> QScrollArea:
    sc = QScrollArea()
    sc.setWidgetResizable(True)
    sc.setFrameShape(QScrollArea.Shape.NoFrame)
    sc.setWidget(inner)
    return sc


class _GuiBridge(QObject):
    """Führt Callbacks aus dem Reader-Thread im GUI-Thread aus (Qt-Timer/Widgets nur dort)."""

    _sig = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self._sig.connect(self._run)

    @Slot(object)
    def _run(self, fn: object) -> None:
        try:
            fn()  # type: ignore[misc]
        except Exception:
            pass

    def post(self, fn: Callable[[], None]) -> None:
        self._sig.emit(fn)


_gui_bridge: Optional[_GuiBridge] = None


def _bridge() -> _GuiBridge:
    """Lazy im GUI-Thread anlegen (erster Aufruf kommt aus einer Wizard-Seite)."""
    global _gui_bridge
    if _gui_bridge is None:
        _gui_bridge = _GuiBridge()
    return _gui_bridge


class _CmdMixin:
    """Gemeinsame send_ui_command-Hilfen für Wizard-Seiten (nutzt ``self.wiz``)."""

    wiz: "CommissioningWizard"

    def _dst(self) -> int:
        return int(self.wiz.slave_dst())

    def _send(
        self,
        cmd: str,
        params: str = "0",
        *,
        on_done: Optional[Callable[[Any, Any], None]] = None,
        timeout_s: float = 1.2,
    ) -> None:
        ctrl = self.wiz.ctrl
        if ctrl is None:
            if on_done:
                on_done(None, "no ctrl")
            return
        gui_done = on_done
        if on_done is not None:
            br = _bridge()

            def gui_done(tel, err, _cb=on_done, _br=br):  # noqa: F811
                _br.post(lambda: _cb(tel, err))

        try:
            ctrl.send_ui_command(
                self._dst(),
                cmd,
                params,
                expect_prefix=f"ACK_{cmd}",
                timeout_s=timeout_s,
                priority=0,
                on_done=gui_done,
            )
        except Exception as e:
            if on_done:
                on_done(None, str(e))

    def _get_sync_ui(self, cmd: str, label: QLabel) -> None:
        def done(tel, err):
            if err or tel is None:
                label.setText(t("setup.val_err", err=str(err or "—")))
            else:
                label.setText(str(getattr(tel, "params", "") or "—"))

        self._send(cmd, "0", on_done=done)


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


class ProfilePage(QWizardPage):
    def __init__(self, wiz: "CommissioningWizard") -> None:
        super().__init__()
        self.wiz = wiz
        self.setTitle(t("setup.page_profile_title"))
        self.setSubTitle(t("setup.page_profile_sub"))
        root = QVBoxLayout(self)
        self.lbl_active = QLabel("")
        root.addWidget(self.lbl_active)
        self.rb_default = QRadioButton(t("setup.profile_use_default"))
        self.rb_edit = QRadioButton(t("setup.profile_edit_existing"))
        self.rb_new = QRadioButton(t("setup.profile_create_new"))
        self.rb_default.setChecked(True)
        root.addWidget(self.rb_default)
        root.addWidget(self.rb_edit)
        self.cb_existing = QComboBox()
        self.cb_existing.setEnabled(False)
        root.addWidget(self.cb_existing)
        root.addWidget(self.rb_new)
        self.ed_name = QLineEdit()
        self.ed_name.setPlaceholderText(t("setup.profile_name_ph"))
        self.ed_name.setEnabled(False)
        root.addWidget(self.ed_name)
        tip = QLabel(t("setup.profile_hint"))
        tip.setWordWrap(True)
        tip.setStyleSheet("color: gray;")
        root.addWidget(tip)
        tip2 = QLabel(t("setup.profile_temp_hint"))
        tip2.setWordWrap(True)
        tip2.setStyleSheet("color: gray;")
        root.addWidget(tip2)
        root.addStretch(1)
        self.rb_default.toggled.connect(self._sync_mode_ui)
        self.rb_edit.toggled.connect(self._sync_mode_ui)
        self.rb_new.toggled.connect(self._sync_mode_ui)

    def initializePage(self) -> None:
        meta = get_active_profile_meta()
        self.lbl_active.setText(
            t("setup.profile_active", name=str(meta.get("name") or "Default"))
        )
        self._fill_existing()
        self._sync_mode_ui()
        # Cache im GUI-Thread vorwärmen (nicht im Worker — unter Windows hängt das).
        QTimer.singleShot(0, lambda: list_serial_port_entries(force=False))

    def _fill_existing(self) -> None:
        prefer = str(get_active_profile_id() or "").strip()
        prev = str(self.cb_existing.currentData() or "").strip()
        self.cb_existing.clear()
        for p in list_profiles():
            pid = str(p.get("id") or "")
            if not pid:
                continue
            name = str(p.get("name") or pid)
            self.cb_existing.addItem(name, pid)
        # Aktives Profil vorwählen; sonst vorherige Auswahl behalten.
        for want in (prefer, prev):
            if not want:
                continue
            for i in range(self.cb_existing.count()):
                if str(self.cb_existing.itemData(i) or "") == want:
                    self.cb_existing.setCurrentIndex(i)
                    return

    def _sync_mode_ui(self) -> None:
        edit_on = self.rb_edit.isChecked()
        self.cb_existing.setEnabled(edit_on)
        self.ed_name.setEnabled(self.rb_new.isChecked())
        if edit_on:
            # Beim Umschalten auf „vorhandenes bearbeiten“ aktives Profil vorwählen.
            active = str(get_active_profile_id() or "").strip()
            if active:
                for i in range(self.cb_existing.count()):
                    if str(self.cb_existing.itemData(i) or "") == active:
                        self.cb_existing.setCurrentIndex(i)
                        break

    def validatePage(self) -> bool:
        if self.rb_default.isChecked():
            mode, pid, name = "default", DEFAULT_PROFILE_ID, "Default"
        elif self.rb_edit.isChecked():
            pid = str(self.cb_existing.currentData() or "").strip()
            if not pid:
                QMessageBox.warning(self, t("setup.title"), t("setup.err_profile_select"))
                return False
            mode, name = "edit", str(self.cb_existing.currentText() or pid)
        else:
            name = self.ed_name.text().strip()
            if not name:
                QMessageBox.warning(self, t("setup.title"), t("setup.err_profile_name"))
                return False
            if name.casefold() in ("default", "defaults"):
                QMessageBox.warning(
                    self, t("setup.title"), t("setup.err_profile_default_name")
                )
                return False
            mode, pid = "new", ""

        try:
            ok = self.wiz.begin_profile_session(mode, profile_id=pid, new_name=name)
        except Exception as e:
            QMessageBox.warning(self, t("setup.title"), str(e))
            return False
        if not ok:
            return False
        self.wiz.cfg = self.wiz._cfg_ref()
        return True


class LocationPage(QWizardPage):
    def __init__(self, wiz: "CommissioningWizard") -> None:
        super().__init__()
        self.wiz = wiz
        self.setTitle(t("setup.page_location_title"))
        self.setSubTitle(t("setup.page_location_sub"))
        root = QVBoxLayout(self)
        hint = QLabel(t("setup.location_hint"))
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray;")
        root.addWidget(hint)
        self._panel = LocationPanel(self.wiz.cfg, self)
        root.addWidget(self._panel)
        root.addStretch(1)

    def initializePage(self) -> None:
        self._panel.set_cfg(self.wiz.cfg)
        self._panel.load_from_cfg()

    def validatePage(self) -> bool:
        self._panel.apply_to_cfg(self.wiz.cfg)
        if callable(self.wiz.save_cfg_cb):
            self.wiz.save_cfg_cb(self.wiz.cfg)
        return True


class ConnectionPage(QWizardPage):
    def __init__(self, wiz: "CommissioningWizard") -> None:
        super().__init__()
        self.wiz = wiz
        self.setTitle(t("setup.page_conn_title"))
        self.setSubTitle(t("setup.page_conn_sub"))
        inner = QWidget()
        root = QVBoxLayout(inner)

        intro = QLabel(t("setup.conn_hint"))
        intro.setWordWrap(True)
        intro.setStyleSheet("color: gray;")
        root.addWidget(intro)

        ids_hint = QLabel(t("setup.conn_ids_hint"))
        ids_hint.setWordWrap(True)
        ids_hint.setStyleSheet("color: gray;")
        root.addWidget(ids_hint)
        self._bus_ids = RotorBusIdsPanel(self.wiz.cfg, self)
        self._bus_ids.axes_changed.connect(self._sync_axis_apply_buttons)
        root.addWidget(self._bus_ids)

        self._link = RotorLinkPanel(self.wiz.cfg, self)
        root.addWidget(self._link)

        row = QHBoxLayout()
        self.btn_apply = QPushButton(t("setup.btn_apply_conn"))
        self.btn_apply.clicked.connect(self._apply_conn)
        row.addWidget(self.btn_apply)
        row.addStretch(1)
        root.addLayout(row)
        self.lbl_status = QLabel("")
        self.lbl_status.setWordWrap(True)
        root.addWidget(self.lbl_status)

        self.gb_network = QGroupBox(t("settings.tab_network"))
        net_l = QVBoxLayout(self.gb_network)
        self.lbl_network_howto = QLabel(t("setup.conn_network_howto"))
        self.lbl_network_howto.setWordWrap(True)
        self.lbl_network_howto.setStyleSheet("color: gray;")
        net_l.addWidget(self.lbl_network_howto)
        self._network_tab = NetworkModulesTab(self.wiz.cfg, self)
        self._network_tab._quiet_on_show = True
        self._network_tab.save_requested.connect(self._on_network_save)
        self._network_tab.apply_to_link_requested.connect(self._on_apply_module_link)
        self._network_tab.apply_axis_link_requested.connect(self._on_apply_axis_link)
        net_l.addWidget(self._network_tab)
        root.addWidget(self.gb_network)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(_scroll(inner))
        self._link.cb_hw_mode.currentIndexChanged.connect(lambda _i: self._mode_ui())
        self._load_from_cfg()
        self._mode_ui()

    def initializePage(self) -> None:
        self._sync_network_cfg()
        self._load_from_cfg()
        self._mode_ui()

    def cleanupPage(self) -> None:
        try:
            self._bus_ids.apply_to_cfg(self.wiz.cfg)
        except Exception:
            pass
        try:
            self._network_tab.apply_to_cfg(self.wiz.cfg)
        except Exception:
            pass
        try:
            self._network_tab.stop_background_activity()
        except Exception:
            pass

    def _sync_network_cfg(self) -> None:
        self._network_tab._cfg = self.wiz.cfg
        try:
            self._network_tab.load_from_cfg()
        except Exception:
            pass

    def _sync_axis_apply_buttons(self) -> None:
        try:
            self._network_tab.set_dual_axis_apply(
                enable_az=self._bus_ids.enable_az(),
                enable_el=self._bus_ids.enable_el(),
            )
        except Exception:
            pass
        try:
            self._link.set_el_endpoint_visible(bool(self._bus_ids.enable_el()))
        except Exception:
            pass

    def _on_network_save(self) -> None:
        try:
            self._network_tab.apply_to_cfg(self.wiz.cfg)
            if callable(self.wiz.save_cfg_cb):
                self.wiz.save_cfg_cb(self.wiz.cfg)
        except Exception:
            pass

    def _store_axis_endpoint(self, axis: str, host: str, port: int) -> None:
        ui = self.wiz.cfg.setdefault("ui", {})
        eps = ui.setdefault("setup_axis_endpoints", {})
        if not isinstance(eps, dict):
            eps = {}
            ui["setup_axis_endpoints"] = eps
        eps[str(axis)] = {"host": str(host or "").strip(), "port": int(port)}

    def _on_apply_module_link(self, host: str, port: int) -> None:
        self._link.set_tcp_endpoint(host, port)
        self._mode_ui()
        try:
            port_i = int(port)
        except Exception:
            port_i = 8886
        self.lbl_status.setText(
            t("settings.network_apply_link_done", host=str(host or "").strip(), port=port_i)
        )

    def _on_apply_axis_link(self, host: str, port: int, axis: str) -> None:
        axis = str(axis or "az").strip().lower()
        if axis not in ("az", "el"):
            axis = "az"
        self._store_axis_endpoint(axis, host, port)
        self._link.set_tcp_endpoint(host, port, axis=axis)
        self._mode_ui()
        try:
            port_i = int(port)
        except Exception:
            port_i = 8886
        self.lbl_status.setText(
            t(
                "settings.network_apply_axis_done",
                axis=axis.upper(),
                host=str(host or "").strip(),
                port=port_i,
            )
        )
        if callable(self.wiz.save_cfg_cb):
            self._bus_ids.apply_to_cfg(self.wiz.cfg)
            self._link.apply_to_cfg(self.wiz.cfg)
            self.wiz.save_cfg_cb(self.wiz.cfg)

    def _load_from_cfg(self) -> None:
        self._bus_ids.set_cfg(self.wiz.cfg)
        self._bus_ids.load_from_cfg()
        self._link.set_cfg(self.wiz.cfg)
        self._link.load_from_cfg()
        self._sync_axis_apply_buttons()

    def _mode_ui(self) -> None:
        mode = str(self._link.cb_hw_mode.currentData() or "com")
        show_tcp = mode in ("tcp", "udp")
        self.gb_network.setVisible(show_tcp)
        if show_tcp:
            self._sync_network_cfg()
            self._sync_axis_apply_buttons()
            try:
                self._network_tab.on_tab_shown(quiet=True)
            except Exception:
                pass
        else:
            try:
                self._network_tab.stop_background_activity()
            except Exception:
                pass

    def _apply_conn(self) -> None:
        err = self._bus_ids.validation_error()
        if err:
            self.lbl_status.setText(err)
            QMessageBox.warning(self, t("setup.title"), err)
            return
        self._bus_ids.apply_to_cfg(self.wiz.cfg)
        self._link.apply_to_cfg(self.wiz.cfg)
        hl = self.wiz.cfg.get("hardware_link") or {}
        mode = str(hl.get("mode", "com"))
        if mode in ("tcp", "udp"):
            try:
                self._network_tab.apply_to_cfg(self.wiz.cfg)
            except Exception:
                pass
        if callable(self.wiz.save_cfg_cb):
            self.wiz.save_cfg_cb(self.wiz.cfg)
        # Nur Master/Enable live übernehmen — gewünschte Slave-IDs erst nach
        # erfolgreichem SETID/SETROTORID auf der nächsten Seite (sonst pollt die
        # App bereits die neue ID, während der Rotor noch die alte hat).
        ctrl = self.wiz.ctrl
        if ctrl is not None:
            rb = self.wiz.cfg.get("rotor_bus") or {}
            try:
                ctrl.master_id = int(rb.get("master_id", 0) or 0)
                ctrl.enable_az = bool(rb.get("enable_az", True))
                ctrl.enable_el = bool(rb.get("enable_el", False))
            except Exception:
                pass
        hw = getattr(ctrl, "hw", None) if ctrl else None
        if hw is not None and hasattr(hw, "update_cfg"):
            try:
                hw.update_cfg(hl)
            except Exception as e:
                self.lbl_status.setText(t("setup.conn_apply_err", err=e))
                return
        QTimer.singleShot(400, self._check_conn)

    def _check_conn(self) -> None:
        hw = getattr(self.wiz.ctrl, "hw", None) if self.wiz.ctrl else None
        ok = False
        try:
            ok = bool(hw and hw.is_connected())
        except Exception:
            ok = False
        self.lbl_status.setText(
            t("setup.conn_ok") if ok else t("setup.conn_offline")
        )

    def validatePage(self) -> bool:
        err = self._bus_ids.validation_error()
        if err:
            QMessageBox.warning(self, t("setup.title"), err)
            return False
        self._apply_conn()
        return self._bus_ids.validation_error() is None


class RotorIdsAssignPage(QWizardPage):
    """Broadcast SETROTORID für AZ/EL; Verifikation per GETREF an die neue ID."""

    # on_done von send_ui_command läuft im Reader-Thread → UI nur über Signal
    _sig_run_ui = Signal(object)

    def __init__(self, wiz: "CommissioningWizard") -> None:
        super().__init__()
        self.wiz = wiz
        self.setTitle(t("setup.page_rotor_ids_title"))
        self.setSubTitle(t("setup.page_rotor_ids_sub"))
        self._sig_run_ui.connect(self._run_ui)
        root = QVBoxLayout(self)
        howto = QLabel(t("setup.rotor_ids_howto"))
        howto.setWordWrap(True)
        howto.setStyleSheet("color: gray;")
        root.addWidget(howto)
        self.lbl_az_ep = QLabel("")
        self.lbl_el_ep = QLabel("")
        self.lbl_warn = QLabel("")
        self.lbl_warn.setWordWrap(True)
        self.lbl_warn.setStyleSheet("color: #b71c1c;")
        root.addWidget(self.lbl_az_ep)
        root.addWidget(self.lbl_el_ep)
        root.addWidget(self.lbl_warn)
        row = QHBoxLayout()
        self.btn_az = QPushButton("")
        self.btn_az.clicked.connect(lambda: self._set_axis_id("az"))
        self.btn_el = QPushButton("")
        self.btn_el.clicked.connect(lambda: self._set_axis_id("el"))
        row.addWidget(self.btn_az)
        row.addWidget(self.btn_el)
        row.addStretch(1)
        root.addLayout(row)
        self.lbl_status = QLabel("")
        self.lbl_status.setWordWrap(True)
        root.addWidget(self.lbl_status)
        root.addStretch(1)
        self._busy = False

    @Slot(object)
    def _run_ui(self, fn: object) -> None:
        if callable(fn):
            try:
                fn()
            except Exception:
                pass

    def _ui(self, fn: Callable[[], None]) -> None:
        """Callable im GUI-Thread ausführen (thread-sicher aus Reader-Callbacks)."""
        self._sig_run_ui.emit(fn)

    def initializePage(self) -> None:
        self._refresh_labels()
        self.lbl_status.setText("")

    def _rb(self) -> dict:
        return self.wiz.cfg.get("rotor_bus") if isinstance(self.wiz.cfg.get("rotor_bus"), dict) else {}

    def _endpoint(self, axis: str) -> tuple[str, int]:
        return axis_endpoint_from_cfg(self.wiz.cfg, axis)

    def _refresh_labels(self) -> None:
        rb = self._rb()
        en_az = bool(rb.get("enable_az", True))
        en_el = bool(rb.get("enable_el", False))
        az_id = int(rb.get("slave_az", 20) or 20)
        el_id = int(rb.get("slave_el", 21) or 21)
        az_h, az_p = self._endpoint("az")
        el_h, el_p = self._endpoint("el")
        mode = str((self.wiz.cfg.get("hardware_link") or {}).get("mode", "com")).lower()
        self.lbl_az_ep.setText(
            t("setup.rotor_ids_az_ep", host=az_h or "—", port=az_p) if en_az else ""
        )
        self.lbl_el_ep.setText(
            t("setup.rotor_ids_el_ep", host=el_h or "—", port=el_p) if en_el else ""
        )
        self.btn_az.setVisible(en_az)
        self.btn_el.setVisible(en_el)
        self.btn_az.setText(t("setup.rotor_ids_btn_az", id=az_id))
        self.btn_el.setText(t("setup.rotor_ids_btn_el", id=el_id))
        warn = ""
        if mode == "com" and en_az and en_el:
            warn = t("setup.rotor_ids_com_warn")
        elif en_az and en_el and az_h and el_h and az_h == el_h and az_p == el_p:
            warn = t("setup.rotor_ids_same_ip_warn")
        self.lbl_warn.setText(warn)

    def _confirm_broadcast(self) -> bool:
        mb = QMessageBox(self)
        mb.setIcon(QMessageBox.Icon.Warning)
        mb.setWindowTitle(t("cmd.msgbox_setrotorid_title"))
        mb.setText(t("cmd.msgbox_setrotorid_text"))
        mb.setStandardButtons(
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel
        )
        mb.setDefaultButton(QMessageBox.StandardButton.Cancel)
        ok_btn = mb.button(QMessageBox.StandardButton.Ok)
        cancel_btn = mb.button(QMessageBox.StandardButton.Cancel)
        if ok_btn is not None:
            ok_btn.setText(t("cmd.msgbox_setrotorid_ok"))
        if cancel_btn is not None:
            cancel_btn.setText(t("cmd.msgbox_setrotorid_cancel"))
        return mb.exec() == QMessageBox.StandardButton.Ok

    def _ensure_link_for_axis(self, axis: str) -> bool:
        """Immer die für die Achse übernommene IP nutzen — Broadcast geht nur dort an."""
        host, port = self._endpoint(axis)
        if host:
            self.lbl_status.setText(
                t("setup.rotor_ids_connecting", host=host, port=int(port))
            )
        ok, detail = ensure_axis_tcp_link(
            self.wiz.ctrl,
            self.wiz.cfg,
            axis,
            save_cfg_cb=self.wiz.save_cfg_cb,
            timeout_s=5.0,
        )
        if ok:
            return True
        if detail.startswith("no link"):
            self.lbl_status.setText(
                t("setup.rotor_ids_connect_fail", host=host, port=int(port))
            )
        elif detail == "offline":
            self.lbl_status.setText(t("setup.ctrl_test_offline"))
        elif detail == "no axis endpoint":
            self.lbl_status.setText(t("setup.rotor_ids_need_ep", axis=axis.upper()))
        else:
            self.lbl_status.setText(t("setup.conn_apply_err", err=detail))
        return False

    def _probe_live_id(self, ctrl: Any, axis: str, new_id: int) -> Optional[int]:
        """Aktuelle Slave-ID am Bus finden (GETREF), bevor wir umprogrammieren."""
        attr = "slave_el" if axis == "el" else "slave_az"
        try:
            runtime = int(getattr(ctrl, attr, 0) or 0)
        except Exception:
            runtime = 0
        candidates: list[int] = []
        for c in (runtime, int(new_id), 20, 21, 22):
            try:
                ci = int(c)
            except Exception:
                continue
            if 1 <= ci <= 254 and ci not in candidates:
                candidates.append(ci)
        if not hasattr(ctrl, "sync_ui_command_response"):
            return runtime if 1 <= runtime <= 254 else None
        for cid in candidates:
            try:
                r = ctrl.sync_ui_command_response(
                    int(cid), "GETREF", "0", "ACK_GETREF", timeout_s=0.9
                )
            except Exception:
                continue
            if r is None or str(r).startswith(SYNC_UI_NAK_PREFIX):
                continue
            return int(cid)
        return None

    def _set_axis_id(self, axis: str) -> None:
        if self._busy:
            return
        axis = str(axis or "az").lower()
        rb = self._rb()
        if axis == "az" and not bool(rb.get("enable_az", True)):
            return
        if axis == "el" and not bool(rb.get("enable_el", False)):
            return
        new_id = int(rb.get("slave_az" if axis == "az" else "slave_el", 20) or 20)
        if not self._confirm_broadcast():
            return
        self.lbl_status.setText(t("setup.rotor_ids_working", axis=axis.upper()))
        if not self._ensure_link_for_axis(axis):
            return
        ctrl = self.wiz.ctrl
        if ctrl is None or not hasattr(ctrl, "send_ui_command"):
            self.lbl_status.setText(t("setup.rotor_ids_set_fail", axis=axis.upper(), err="no ctrl"))
            return
        hw = getattr(ctrl, "hw", None)
        if hw is not None and not bool(getattr(hw, "is_connected", lambda: False)()):
            self.lbl_status.setText(t("setup.ctrl_test_offline"))
            return

        # Live-ID ermitteln — ohne das pollt die App schon die Ziel-ID und SET geht ins Leere
        self.lbl_status.setText(t("setup.rotor_ids_probe"))
        live_id = self._probe_live_id(ctrl, axis, new_id)
        if live_id is not None and int(live_id) == int(new_id):
            self.lbl_status.setText(
                t("setup.rotor_ids_already", axis=axis.upper(), id=new_id)
            )
            try:
                if axis == "az":
                    ctrl.slave_az = int(new_id)
                else:
                    ctrl.slave_el = int(new_id)
            except Exception:
                pass
            return
        if live_id is None:
            self.lbl_status.setText(t("setup.rotor_ids_probe_fail"))
            # trotzdem Broadcast versuchen
            live_id = 0

        self._busy = True
        self.btn_az.setEnabled(False)
        self.btn_el.setEnabled(False)
        host, port = self._endpoint(axis)
        ep_note = f"{host}:{port}" if host else "COM"

        def _finish_fail(msg: str) -> None:
            def apply():
                self._busy = False
                self.btn_az.setEnabled(True)
                self.btn_el.setEnabled(True)
                self.lbl_status.setText(msg)

            self._ui(apply)

        def _apply_runtime_id(ax: str, nid: int) -> None:
            try:
                if ax == "az":
                    ctrl.slave_az = int(nid)
                else:
                    ctrl.slave_el = int(nid)
            except Exception:
                pass
            rb2 = self.wiz.cfg.setdefault("rotor_bus", {})
            if isinstance(rb2, dict):
                if ax == "az":
                    rb2["slave_az"] = int(nid)
                else:
                    rb2["slave_el"] = int(nid)
                # Abgeschaltete Achse nicht auf dieselbe Bus-ID wie die aktive legen
                try:
                    from ..app_config import dedupe_disabled_axis_id

                    if dedupe_disabled_axis_id(rb2):
                        ctrl.slave_az = int(rb2["slave_az"])
                        ctrl.slave_el = int(rb2["slave_el"])
                except Exception:
                    pass
            if callable(self.wiz.save_cfg_cb):
                try:
                    self.wiz.save_cfg_cb(self.wiz.cfg)
                except Exception:
                    pass

        def _verify_getref(ax: str, nid: int, ep: str) -> None:
            self.lbl_status.setText(
                t("setup.rotor_ids_verify", axis=ax.upper(), id=nid)
            )

            def on_ref(tel, err, a=ax, n=nid, e=ep):
                def apply():
                    self._busy = False
                    self.btn_az.setEnabled(True)
                    self.btn_el.setEnabled(True)
                    if err or tel is None:
                        self.lbl_status.setText(
                            t(
                                "setup.rotor_ids_verify_fail",
                                axis=a.upper(),
                                id=n,
                                ep=e,
                            )
                        )
                        return
                    _apply_runtime_id(a, n)
                    params = str(getattr(tel, "params", "") or "").strip()
                    self.lbl_status.setText(
                        t(
                            "setup.rotor_ids_verify_ok",
                            axis=a.upper(),
                            id=n,
                            ref=params or "—",
                            ep=e,
                        )
                    )

                self._ui(apply)

            try:
                ctrl.send_ui_command(
                    int(nid),
                    "GETREF",
                    "0",
                    expect_prefix="ACK_GETREF",
                    timeout_s=3.0,
                    priority=0,
                    on_done=on_ref,
                    apply_local_state=False,
                )
            except Exception as e:
                _finish_fail(
                    t("setup.rotor_ids_set_fail", axis=ax.upper(), err=f"{e} @ {ep}")
                )

        def _send_setrotorid(ax: str, nid: int, ep: str) -> None:
            """Fallback: Broadcast, wenn keine Live-ID bekannt."""

            def done(tel, err, a=ax, n=nid, e=ep):
                if err and str(err) != "timeout":
                    _finish_fail(
                        t("setup.rotor_ids_set_fail", axis=a.upper(), err=f"{err} @ {e}")
                    )
                    return

                def after_set():
                    self.lbl_status.setText(
                        t("setup.rotor_ids_set_ok", axis=a.upper(), id=n) + f" ({e})"
                    )
                    QTimer.singleShot(400, lambda: _verify_getref(a, n, e))

                self._ui(after_set)

            try:
                ctrl.send_ui_command(
                    int(BROADCAST_DST),
                    "SETROTORID",
                    str(int(nid)),
                    expect_prefix="ACK_SETROTORID",
                    timeout_s=3.0,
                    priority=0,
                    on_done=done,
                    apply_local_state=False,
                )
            except Exception as e:
                _finish_fail(
                    t("setup.rotor_ids_set_fail", axis=ax.upper(), err=f"{e} @ {ep}")
                )

        def _send_setid(ax: str, old_id: int, nid: int, ep: str) -> None:
            """Zuverlässig: SETID gezielt an die aktuelle Slave-ID."""

            def done(tel, err, a=ax, o=old_id, n=nid, e=ep):
                if err and str(err) != "timeout":
                    # Fallback Broadcast
                    def fb():
                        self.lbl_status.setText(
                            t("setup.rotor_ids_setid_fail_broadcast", old=o, new=n)
                        )
                        _send_setrotorid(a, n, e)

                    self._ui(fb)
                    return

                def after_set():
                    self.lbl_status.setText(
                        t("setup.rotor_ids_setid_ok", old=o, new=n) + f" ({e})"
                    )
                    QTimer.singleShot(400, lambda: _verify_getref(a, n, e))

                self._ui(after_set)

            try:
                # z. B. #0:20:SETID:21:41$
                ctrl.send_ui_command(
                    int(old_id),
                    "SETID",
                    str(int(nid)),
                    expect_prefix="ACK_SETID",
                    timeout_s=3.0,
                    priority=0,
                    on_done=done,
                    apply_local_state=False,
                )
            except Exception as e:
                _finish_fail(
                    t("setup.rotor_ids_set_fail", axis=ax.upper(), err=f"{e} @ {ep}")
                )

        if live_id and int(live_id) != int(new_id):
            self.lbl_status.setText(
                t(
                    "setup.rotor_ids_setid_working",
                    axis=axis.upper(),
                    old=int(live_id),
                    new=int(new_id),
                )
            )
            _send_setid(axis, int(live_id), int(new_id), ep_note)
        else:
            self.lbl_status.setText(
                t("setup.rotor_ids_working", axis=axis.upper()) + f" ({ep_note})"
            )
            _send_setrotorid(axis, int(new_id), ep_note)

    def validatePage(self) -> bool:
        return True


class ControllerPage(QWizardPage):
    def __init__(self, wiz: "CommissioningWizard") -> None:
        super().__init__()
        self.wiz = wiz
        self.setTitle(t("setup.page_ctrl_title"))
        self.setSubTitle(t("setup.page_ctrl_sub"))
        inner = QWidget()
        root = QVBoxLayout(inner)
        info = QLabel(t("setup.ctrl_info"))
        info.setWordWrap(True)
        root.addWidget(info)
        howto = QLabel(t("setup.ctrl_mode_howto"))
        howto.setWordWrap(True)
        howto.setStyleSheet("color: gray;")
        root.addWidget(howto)

        self._tab = ControllerHwTab(self.wiz.cfg, self)
        self._tab.setconidf_requested.connect(self._on_setconidf)
        root.addWidget(self._tab)

        row = QHBoxLayout()
        self.btn_test = QPushButton(t("setup.ctrl_btn_test"))
        self.btn_test.clicked.connect(self._test_controller)
        self.btn_push_ids = QPushButton(t("setup.ctrl_btn_push_rotor_ids"))
        self.btn_push_ids.setEnabled(False)
        self.btn_push_ids.clicked.connect(self._push_rotor_ids)
        row.addWidget(self.btn_test)
        row.addWidget(self.btn_push_ids)
        row.addStretch(1)
        root.addLayout(row)
        self.lbl_test = QLabel("")
        self.lbl_test.setWordWrap(True)
        root.addWidget(self.lbl_test)

        tip = QLabel(t("setup.ctrl_hint_device"))
        tip.setWordWrap(True)
        tip.setStyleSheet("color: gray;")
        root.addWidget(tip)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(_scroll(inner))
        self._link_tested_ok = False

    def initializePage(self) -> None:
        self._tab.set_cfg(self.wiz.cfg)
        self._tab.load_from_cfg()
        # Gewünschte Profil-IDs als Ziel vorbelegen (nach dem Lesen werden Ist-Werte gezeigt)
        self._apply_profile_ids_to_tab()
        self.lbl_test.setText("")
        self._link_tested_ok = False
        self.btn_push_ids.setEnabled(False)

    def validatePage(self) -> bool:
        self._tab.apply_to_cfg(self.wiz.cfg)
        if callable(self.wiz.save_cfg_cb):
            self.wiz.save_cfg_cb(self.wiz.cfg)
        return True

    def _persist(self) -> None:
        self._tab.apply_to_cfg(self.wiz.cfg)
        if callable(self.wiz.save_cfg_cb):
            self.wiz.save_cfg_cb(self.wiz.cfg)

    def _apply_profile_ids_to_tab(self) -> None:
        """Profil-Slave-IDs als Soll in die AZ/EL-Felder (nach Read überschreibbar)."""
        try:
            _cid, az, el = profile_rotor_ids(self.wiz.cfg)
            self._tab.sp_cont_az_rotor_id.setValue(max(0, min(254, az)))
            self._tab.sp_cont_el_rotor_id.setValue(max(0, min(254, el)))
        except Exception:
            pass

    def _link_error_text(self, code: str) -> str:
        if code == "usb_offline":
            return t("setup.ctrl_test_usb_fail")
        if code == "web_offline":
            return t("setup.ctrl_test_web_offline")
        if code == "bus_offline":
            return t("setup.ctrl_test_offline")
        return t("setup.ctrl_test_fail")

    def _open_link(self) -> tuple[bool, str]:
        """Je nach UI-Modus Link öffnen (RS485 / PC-USB / WEB)."""
        self._persist()
        mode = int(self._tab.link_mode_from_ui())
        return ensure_controller_link(
            self.wiz.ctrl,
            mode=mode,
            cfg=self.wiz.cfg,
            proxy=getattr(self.wiz, "controller_remote_proxy", None),
        )

    def _on_setconidf(self) -> None:
        if not self._tab.hw_enabled():
            self.lbl_test.setText(t("setup.ctrl_test_disabled"))
            return
        ctrl = self.wiz.ctrl
        if ctrl is None or not hasattr(ctrl, "broadcast_setconidf"):
            self.lbl_test.setText(t("settings.controller_setconidf_offline"))
            return
        ok, err = self._open_link()
        if not ok:
            self.lbl_test.setText(self._link_error_text(err))
            return
        try:
            ctrl.broadcast_setconidf(int(self._tab.sp_controller_id.value()))
            self.lbl_test.setText(t("settings.controller_setconidf_sent"))
        except Exception as e:
            self.lbl_test.setText(str(e))

    def _write_profile_ids(self) -> bool:
        """Profil-AZ/EL-IDs per SETCONTAZID/EL an den (bereits offenen) Link schreiben."""
        self._apply_profile_ids_to_tab()
        self._persist()
        ctrl = self.wiz.ctrl
        if ctrl is None:
            return False
        cid, az, el = profile_rotor_ids(self.wiz.cfg)
        chw = self.wiz.cfg.setdefault("controller_hw", {})
        if isinstance(chw, dict):
            chw["cont_id"] = int(cid)
            chw["az_rotor_id"] = int(az)
            chw["el_rotor_id"] = int(el)
        dst = int(self._tab.sp_controller_id.value() or cid)
        return bool(write_controller_rotor_ids(ctrl, dst, az, el))

    def _push_rotor_ids(self) -> None:
        if not self._tab.hw_enabled():
            self.lbl_test.setText(t("setup.ctrl_test_disabled"))
            return
        if not self._link_tested_ok:
            self.lbl_test.setText(t("setup.ctrl_push_need_test"))
            return
        self.lbl_test.setText(t("setup.ctrl_push_working"))
        self.btn_test.setEnabled(False)
        self.btn_push_ids.setEnabled(False)
        try:
            ok_link, err = self._open_link()
            if not ok_link:
                self.lbl_test.setText(self._link_error_text(err))
                return
            ok = self._write_profile_ids()
        finally:
            self.btn_test.setEnabled(True)
            self.btn_push_ids.setEnabled(True)
        self.lbl_test.setText(
            t("setup.rotor_ids_push_ok") if ok else t("setup.rotor_ids_push_fail")
        )

    def _after_test_write_ids(self) -> None:
        if not self._link_tested_ok:
            return
        self.lbl_test.setText(t("setup.ctrl_test_ok") + " " + t("setup.ctrl_push_working"))
        # Link sollte noch stehen — bei Bedarf kurz nachziehen
        ok_link, _err = self._open_link()
        ok = bool(ok_link) and self._write_profile_ids()
        # Profil-Soll wieder in die Felder (nach Read standen Ist-Werte)
        self._apply_profile_ids_to_tab()
        if ok:
            self.lbl_test.setText(
                t("setup.ctrl_test_ok") + " " + t("setup.rotor_ids_push_ok")
            )
        else:
            self.lbl_test.setText(
                t("setup.ctrl_test_ok") + " " + t("setup.rotor_ids_push_fail")
            )

    def _test_controller(self) -> None:
        """Modus wählen → Link → Einstellungen lesen → IDs setzen können."""
        self._persist()
        self._link_tested_ok = False
        self.btn_push_ids.setEnabled(False)
        self._tab.set_led_ok(False)
        if not self._tab.hw_enabled():
            self.lbl_test.setText(t("setup.ctrl_test_disabled"))
            return
        ctrl = self.wiz.ctrl
        if ctrl is None:
            self.lbl_test.setText(t("setup.ctrl_test_fail"))
            return

        mode = int(self._tab.link_mode_from_ui())
        self.lbl_test.setText(t("settings.controller_status_reading"))
        self._tab.set_wait_visible(True)
        self.btn_test.setEnabled(False)
        ok = False
        try:
            ok_link, err = self._open_link()
            if not ok_link:
                self.lbl_test.setText(self._link_error_text(err))
                return
            dst = int(self._tab.sp_controller_id.value())
            read_ok, values = read_controller_settings(ctrl, dst, timeout_s=1.2)
            if not read_ok:
                # WEB/USB: manchmal nur Link, GET noch nicht — einmal kurz warten
                if mode == 2:
                    self.lbl_test.setText(t("setup.ctrl_test_web_offline"))
                elif mode == 1:
                    self.lbl_test.setText(t("setup.ctrl_test_usb_fail"))
                else:
                    self.lbl_test.setText(t("setup.ctrl_test_fail"))
                return
            apply_settings_to_controller_tab(self._tab, values)
            self._persist()
            ok = True
            self.lbl_test.setText(t("setup.ctrl_test_read_ok"))
        finally:
            self._tab.set_wait_visible(False)
            self.btn_test.setEnabled(True)
        self._tab.set_led_ok(ok)
        if not ok:
            return
        self._link_tested_ok = True
        self.btn_push_ids.setEnabled(True)
        # Profil-IDs schreiben, Felder danach auf Soll setzen
        QTimer.singleShot(100, self._after_test_write_ids)


class AxisPickPage(QWizardPage):
    def __init__(self, wiz: "CommissioningWizard") -> None:
        super().__init__()
        self.wiz = wiz
        self.setTitle(t("setup.page_axis_title"))
        self.setSubTitle(t("setup.page_axis_sub"))
        root = QVBoxLayout(self)
        self.cb = QComboBox()
        root.addWidget(self.cb)
        root.addStretch(1)

    def initializePage(self) -> None:
        self.cb.clear()
        rb = self.wiz.cfg.get("rotor_bus") or {}
        if bool(rb.get("enable_az", True)):
            self.cb.addItem(
                t("setup.axis_az", id=int(rb.get("slave_az", 20) or 20)),
                ("az", int(rb.get("slave_az", 20) or 20)),
            )
        if bool(rb.get("enable_el", False)):
            self.cb.addItem(
                t("setup.axis_el", id=int(rb.get("slave_el", 21) or 21)),
                ("el", int(rb.get("slave_el", 21) or 21)),
            )
        if self.cb.count() == 0:
            self.cb.addItem(t("setup.axis_az", id=20), ("az", 20))

    def validatePage(self) -> bool:
        data = self.cb.currentData()
        if not data:
            return False
        self.wiz.axis_role = str(data[0])
        self.wiz.axis_dst = int(data[1])
        return True


class EncoderPage(QWizardPage, _CmdMixin):
    def __init__(self, wiz: "CommissioningWizard") -> None:
        super().__init__()
        self.wiz = wiz
        self.setTitle(t("setup.page_encoder_title"))
        self.setSubTitle(t("setup.page_encoder_sub"))
        root = QVBoxLayout(self)
        self.cb = QComboBox()
        for val, key in ENCODER_TYPE_CHOICES:
            self.cb.addItem(t(key), val)
        root.addWidget(self.cb)
        tip = QLabel(t("setup.enc_reboot_hint"))
        tip.setWordWrap(True)
        tip.setStyleSheet("color: gray;")
        root.addWidget(tip)
        self.lbl = QLabel("")
        root.addWidget(self.lbl)
        btn = QPushButton(t("setup.btn_set_and_verify"))
        btn.clicked.connect(self._apply)
        root.addWidget(btn)
        root.addStretch(1)
        self._ok = False

    def initializePage(self) -> None:
        self._ok = False
        self._busy = False
        if self.wiz.enc_type:
            self._select_enc(int(self.wiz.enc_type))
        # Aktuellen Typ vom Rotor vorwählen (früher: Seite „Hardware prüfen“).
        self._send("GETENCTYPE", "0", on_done=self._on_get_enctype)

    def _select_enc(self, et: int) -> None:
        for i in range(self.cb.count()):
            if int(self.cb.itemData(i)) == int(et):
                self.cb.setCurrentIndex(i)
                break

    def _on_get_enctype(self, tel: Any, err: Any) -> None:
        if err or tel is None:
            return
        et = parse_encoder_type(getattr(tel, "params", ""))
        if et is None:
            return
        self.wiz.enc_type = et
        self._select_enc(et)
        self.lbl.setText(t("setup.verify_ok", val=et))

    _VERIFY_TRIES = 6

    def _apply(self) -> None:
        if getattr(self, "_busy", False):
            return
        val = int(self.cb.currentData())
        self._busy = True
        self._ok = False
        self.lbl.setText(t("setup.working"))

        def finish(ok: bool, text: str) -> None:
            self._busy = False
            self._ok = ok
            self.lbl.setText(text)

        def verify(attempt: int) -> None:
            def after_get(tel2, err2):
                if err2 or tel2 is None:
                    # Rotor startet nach Typ-Wechsel oft neu → erneut versuchen
                    if attempt + 1 < self._VERIFY_TRIES:
                        QTimer.singleShot(1500, lambda: verify(attempt + 1))
                        return
                    finish(False, t("setup.verify_fail", err=str(err2 or "timeout")))
                    return
                got = parse_encoder_type(getattr(tel2, "params", ""))
                if got == val:
                    self.wiz.enc_type = val
                    finish(True, t("setup.verify_ok", val=got))
                elif attempt + 1 < self._VERIFY_TRIES:
                    QTimer.singleShot(1500, lambda: verify(attempt + 1))
                else:
                    finish(False, t("setup.verify_mismatch", want=val, got=got))

            self._send("GETENCTYPE", "0", on_done=after_get, timeout_s=1.5)

        def after_set(tel, err):
            # Rotor kann den Neustart schon einleiten, bevor das ACK ankommt → nicht abbrechen,
            # sondern per GETENCTYPE prüfen (mit Wiederholungen).
            if err and str(err) != "timeout":
                finish(False, t("setup.set_fail", err=str(err)))
                return
            self.lbl.setText(t("setup.enc_wait_reboot"))
            QTimer.singleShot(800, lambda: verify(0))

        self._send("SETENCTYPE", str(val), on_done=after_set, timeout_s=2.0)

    def validatePage(self) -> bool:
        if self._ok:
            return True
        val = int(self.cb.currentData())
        # Typ ist laut GETENCTYPE (Startseite) schon gesetzt → ohne erneutes Schreiben weiter
        if self.wiz.enc_type and int(self.wiz.enc_type) == val:
            self._ok = True
            return True
        if getattr(self, "_busy", False):
            return False
        self._apply()
        QMessageBox.information(self, t("setup.title"), t("setup.enc_confirm_next"))
        return False


class DrivePage(QWizardPage, _CmdMixin):
    def __init__(self, wiz: "CommissioningWizard") -> None:
        super().__init__()
        self.wiz = wiz
        self.setTitle(t("setup.page_drive_title"))
        self.setSubTitle(t("setup.page_drive_sub"))
        w = QWidget()
        form = QFormLayout(w)
        self.cb_rtype = QComboBox()
        self.cb_rtype.addItem(t("setup.rtype_360"), 1)
        self.cb_rtype.addItem(t("setup.rtype_90"), 2)
        self.cb_rtype.addItem(t("setup.rtype_180"), 3)
        form.addRow(t("setup.drive_rotor_type"), self.cb_rtype)
        self.sp_begin = QDoubleSpinBox()
        self.sp_begin.setRange(-720, 720)
        self.sp_begin.setDecimals(2)
        form.addRow(t("setup.drive_begin"), self.sp_begin)
        self.sp_max = QDoubleSpinBox()
        self.sp_max.setRange(0, 720)
        self.sp_max.setDecimals(2)
        form.addRow(t("setup.drive_max"), self.sp_max)
        self.sp_minpwm = QDoubleSpinBox()
        self.sp_minpwm.setRange(15, 100)
        self.sp_minpwm.setDecimals(0)
        self.sp_minpwm.setSuffix(" %")
        form.addRow(t("setup.drive_minpwm"), self.sp_minpwm)
        self.sp_iwarn = QSpinBox()
        self.sp_iwarn.setRange(100, 10000)
        self.sp_iwarn.setSuffix(" mA")
        form.addRow(t("setup.drive_iwarn"), self.sp_iwarn)
        self.sp_imax = QSpinBox()
        self.sp_imax.setRange(100, 10000)
        self.sp_imax.setSuffix(" mA")
        form.addRow(t("setup.drive_imax"), self.sp_imax)
        self.sp_homepwm = QDoubleSpinBox()
        self.sp_homepwm.setRange(0, 100)
        self.sp_homepwm.setDecimals(0)
        self.sp_homepwm.setSuffix(" %")
        form.addRow(t("setup.drive_homepwm"), self.sp_homepwm)
        self.sp_seek = QDoubleSpinBox()
        self.sp_seek.setRange(0, 100)
        self.sp_seek.setDecimals(0)
        self.sp_seek.setSuffix(" %")
        form.addRow(t("setup.drive_seekpwm"), self.sp_seek)
        self.sp_ramp = QDoubleSpinBox()
        self.sp_ramp.setRange(0, 60)
        self.sp_ramp.setDecimals(1)
        form.addRow(t("setup.drive_ramp"), self.sp_ramp)
        self.sp_arrtol = QDoubleSpinBox()
        self.sp_arrtol.setRange(0, 5)
        self.sp_arrtol.setDecimals(2)
        self.sp_arrtol.setSuffix(" °")
        form.addRow(t("setup.drive_arrtol"), self.sp_arrtol)
        tip = QLabel(t("setup.drive_hint"))
        tip.setWordWrap(True)
        tip.setStyleSheet("color: gray;")
        form.addRow(tip)
        self.lbl = QLabel("")
        form.addRow(self.lbl)
        btn = QPushButton(t("setup.btn_set_and_verify"))
        btn.clicked.connect(self._apply)
        form.addRow(btn)
        btn_read = QPushButton(t("setup.btn_read"))
        btn_read.clicked.connect(self._read_from_rotor)
        form.addRow(btn_read)
        lay = QVBoxLayout(self)
        lay.addWidget(_scroll(w))
        self._ok = False
        self._loading = False
        self._rtype_prev_idx = 0
        self.cb_rtype.currentIndexChanged.connect(self._on_rtype_changed)

    def initializePage(self) -> None:
        self._apply_defaults_to_ui()
        self._ok = False
        self._read_from_rotor()

    def _apply_defaults_to_ui(self) -> None:
        d = drive_defaults(self.wiz.enc_type, self.wiz.axis_role)
        self._loading = True
        try:
            for i in range(self.cb_rtype.count()):
                if int(self.cb_rtype.itemData(i)) == int(d["rotor_type"]):
                    self.cb_rtype.setCurrentIndex(i)
                    break
            self.sp_begin.setValue(float(d["begin_dg"]))
            self.sp_max.setValue(float(d["max_dg"]))
            self.sp_minpwm.setValue(float(d["min_pwm"]))
            self.sp_homepwm.setValue(float(d["home_pwm"]))
            self.sp_seek.setValue(float(d["home_seek_pwm"]))
            self.sp_arrtol.setValue(float(d["arrtol"]))
            ramp = d.get("ramp")
            self.sp_ramp.setValue(5.0 if ramp is None else float(ramp))
            self.sp_iwarn.setValue(int(d.get("iwarn", 3000) or 3000))
            self.sp_imax.setValue(int(d.get("imax", 5000) or 5000))
        finally:
            self._loading = False
            self._rtype_prev_idx = int(self.cb_rtype.currentIndex())

    def _apply_values_to_ui(self, vals: dict) -> None:
        self._loading = True
        try:
            rt = vals.get("rotor_type")
            if rt is not None:
                for i in range(self.cb_rtype.count()):
                    if int(self.cb_rtype.itemData(i)) == int(rt):
                        self.cb_rtype.setCurrentIndex(i)
                        break
            if vals.get("begin_dg") is not None:
                self.sp_begin.setValue(float(vals["begin_dg"]))
            if vals.get("max_dg") is not None:
                self.sp_max.setValue(float(vals["max_dg"]))
            if vals.get("min_pwm") is not None:
                self.sp_minpwm.setValue(float(vals["min_pwm"]))
            if vals.get("home_pwm") is not None:
                self.sp_homepwm.setValue(float(vals["home_pwm"]))
            if vals.get("home_seek_pwm") is not None:
                self.sp_seek.setValue(float(vals["home_seek_pwm"]))
            if vals.get("arrtol") is not None:
                self.sp_arrtol.setValue(float(vals["arrtol"]))
            if vals.get("ramp") is not None:
                self.sp_ramp.setValue(float(vals["ramp"]))
            if vals.get("iwarn") is not None:
                self.sp_iwarn.setValue(int(vals["iwarn"]))
            if vals.get("imax") is not None:
                self.sp_imax.setValue(int(vals["imax"]))
        finally:
            self._loading = False
            self._rtype_prev_idx = int(self.cb_rtype.currentIndex())

    def _read_from_rotor(self) -> None:
        self.lbl.setText(t("setup.drive_reading"))
        fields = list(DRIVE_GET_FIELDS)
        vals: dict = {}
        n_ok = 0

        def step(i: int) -> None:
            nonlocal n_ok
            if i >= len(fields):
                if n_ok > 0:
                    self._apply_values_to_ui(vals)
                if n_ok == len(fields):
                    self.lbl.setText(t("setup.drive_read_ok"))
                elif n_ok > 0:
                    self.lbl.setText(t("setup.drive_read_partial", n=n_ok, total=len(fields)))
                else:
                    self.lbl.setText(t("setup.drive_read_fail"))
                return
            cmd, key, kind = fields[i]

            def got(tel, err) -> None:
                nonlocal n_ok
                if not err and tel is not None:
                    raw = str(getattr(tel, "params", "") or "")
                    cmd_u = str(getattr(tel, "cmd", "") or "").upper()
                    if not cmd_u.startswith("NAK_"):
                        try:
                            num = float(raw.strip().split(";")[0].replace(",", "."))
                            vals[key] = int(num) if kind == "int" else float(num)
                            n_ok += 1
                        except (TypeError, ValueError):
                            pass
                QTimer.singleShot(40, lambda: step(i + 1))

            self._send(cmd, "0", on_done=got, timeout_s=1.5)

        step(0)

    def _ask_360_or_720(self) -> Optional[int]:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle(t("setup.rtype_ask_title"))
        box.setText(t("setup.rtype_ask_text"))
        b360 = box.addButton(t("setup.rtype_ask_360"), QMessageBox.ButtonRole.AcceptRole)
        b720 = box.addButton(t("setup.rtype_ask_720"), QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        clicked = box.clickedButton()
        if clicked is b360:
            return 360
        if clicked is b720:
            return 720
        return None

    def _on_rtype_changed(self, _idx: int) -> None:
        if self._loading:
            return
        try:
            rtype = int(self.cb_rtype.currentData())
        except (TypeError, ValueError):
            return
        if rtype in (2, 3):
            mx = max_dg_for_rotor_type(rtype)
            if mx is not None:
                self.sp_max.setValue(float(mx))
            self._rtype_prev_idx = int(self.cb_rtype.currentIndex())
            return
        if rtype == 1:
            choice = self._ask_360_or_720()
            if choice is None:
                self._loading = True
                try:
                    self.cb_rtype.setCurrentIndex(int(self._rtype_prev_idx))
                finally:
                    self._loading = False
                return
            self.sp_max.setValue(float(choice))
            self._rtype_prev_idx = int(self.cb_rtype.currentIndex())

    def _fmt(self, v: float) -> str:
        s = f"{v:.2f}".rstrip("0").rstrip(".")
        return s.replace(".", ",")

    def _apply(self) -> None:
        values = {
            "SETROTORTYPE": str(int(self.cb_rtype.currentData())),
            "SETBEGINDG": self._fmt(self.sp_begin.value()),
            "SETMAXDG": self._fmt(self.sp_max.value()),
            "SETMINPWM": str(int(self.sp_minpwm.value())),
            "SETIWARN": str(int(self.sp_iwarn.value())),
            "SETIMAX": str(int(self.sp_imax.value())),
            "SETHOMEPWM": str(int(self.sp_homepwm.value())),
            "SETHOMESEEKPPWM": str(int(self.sp_seek.value())),
            "SETRAMP": self._fmt(self.sp_ramp.value()),
            "SETARRTOL": self._fmt(self.sp_arrtol.value()),
        }
        pairs = [(cmd, values[cmd]) for cmd in DRIVE_SET_SEQUENCE if cmd in values]
        self.lbl.setText(t("setup.working"))
        self._queue = list(pairs)
        self._ok = False
        self._run_next()

    def _run_next(self) -> None:
        if not self._queue:
            self.lbl.setText(t("setup.drive_done"))
            self._ok = True
            self.wiz.drive_snapshot = {
                "rotor_type": int(self.cb_rtype.currentData()),
                "begin_dg": float(self.sp_begin.value()),
                "max_dg": float(self.sp_max.value()),
                "min_pwm": float(self.sp_minpwm.value()),
                "iwarn": int(self.sp_iwarn.value()),
                "imax": int(self.sp_imax.value()),
                "home_pwm": float(self.sp_homepwm.value()),
                "home_seek_pwm": float(self.sp_seek.value()),
                "ramp": float(self.sp_ramp.value()),
                "arrtol": float(self.sp_arrtol.value()),
            }
            return
        cmd, params = self._queue.pop(0)

        def done(tel, err):
            if err or tel is None:
                self.lbl.setText(t("setup.set_fail", err=f"{cmd}: {err}"))
                self._ok = False
                return
            QTimer.singleShot(80, self._run_next)

        self._send(cmd, params, on_done=done)

    def validatePage(self) -> bool:
        if not self._ok:
            self._apply()
        return True


class EndstopsPage(QWizardPage, _CmdMixin):
    def __init__(self, wiz: "CommissioningWizard") -> None:
        super().__init__()
        self.wiz = wiz
        self.setTitle(t("setup.page_endst_title"))
        self.setSubTitle(t("setup.page_endst_sub"))
        root = QVBoxLayout(self)
        self.cb = QComboBox()
        root.addWidget(self.cb)
        self.lbl_rec = QLabel("")
        self.lbl_rec.setWordWrap(True)
        root.addWidget(self.lbl_rec)
        self.lbl = QLabel("")
        root.addWidget(self.lbl)
        btn = QPushButton(t("setup.btn_set_and_verify"))
        btn.clicked.connect(self._apply)
        root.addWidget(btn)
        root.addStretch(1)
        self._ok = False

    def initializePage(self) -> None:
        self.cb.clear()
        for v in endstc_choices(self.wiz.enc_type):
            self.cb.addItem(t(f"setup.endst_{v}"), v)
        rec = endstc_recommendation(self.wiz.enc_type)
        self.lbl_rec.setText(t("setup.endst_rec", v=rec))
        for i in range(self.cb.count()):
            if int(self.cb.itemData(i)) == rec:
                self.cb.setCurrentIndex(i)
                break
        self._ok = False

    def _apply(self) -> None:
        val = int(self.cb.currentData())

        def after(tel, err):
            if err or tel is None:
                self.lbl.setText(t("setup.set_fail", err=str(err or "")))
                self._ok = False
                return

            def gdone(tel2, err2):
                got = parse_endstc(getattr(tel2, "params", "") if tel2 else None)
                if got == val:
                    self.wiz.endstc = val
                    self.lbl.setText(t("setup.verify_ok", val=got))
                    self._ok = True
                else:
                    self.lbl.setText(t("setup.verify_mismatch", want=val, got=got))
                    self._ok = False

            self._send("GETENDSTC", "0", on_done=gdone)

        self._send("SETENDSTC", str(val), on_done=after)

    def validatePage(self) -> bool:
        self.wiz.endstc = int(self.cb.currentData())
        if not self._ok:
            self._apply()
        return True


class WeatherPage(QWizardPage, _CmdMixin):
    def __init__(self, wiz: "CommissioningWizard") -> None:
        super().__init__()
        self.wiz = wiz
        self.setTitle(t("setup.page_weather_title"))
        self.setSubTitle(t("setup.page_weather_sub"))
        root = QVBoxLayout(self)
        self.chk = QCheckBox(t("setup.weather_enable"))
        root.addWidget(self.chk)
        self.sp_off = QDoubleSpinBox()
        self.sp_off.setRange(-360, 360)
        self.sp_off.setDecimals(1)
        form = QFormLayout()
        form.addRow(t("setup.weather_dirof"), self.sp_off)
        root.addLayout(form)
        self.lbl_anemo = QLabel("—")
        self.lbl_dir = QLabel("—")
        root.addWidget(QLabel("GETANEMO:"))
        root.addWidget(self.lbl_anemo)
        root.addWidget(QLabel("GETWINDDIR:"))
        root.addWidget(self.lbl_dir)
        btn = QPushButton(t("setup.btn_set_and_verify"))
        btn.clicked.connect(self._apply)
        root.addWidget(btn)
        btn2 = QPushButton(t("setup.btn_read"))
        btn2.clicked.connect(self._read_sensors)
        root.addWidget(btn2)
        root.addStretch(1)
        self._known_val: Optional[bool] = None  # Windmesser-Zustand laut Rotor (GETWINDENABLE)
        self._touched = False
        self.chk.toggled.connect(self._on_chk_toggled)

    def _on_chk_toggled(self, _checked: bool) -> None:
        self._touched = True

    def initializePage(self) -> None:
        """Windmesser-Checkbox vom Rotor übernehmen (nicht ungefragt aktivieren/deaktivieren)."""
        self._known_val = None
        self._touched = False
        self.wiz.wind_choice = None
        self.chk.blockSignals(True)
        self.chk.setChecked(False)
        self.chk.blockSignals(False)

        def got(tel, err):
            if err or tel is None:
                return
            cmd_u = str(getattr(tel, "cmd", "") or "").upper()
            if cmd_u.startswith("NAK_"):
                return
            try:
                v = int(float(str(getattr(tel, "params", "") or "").split(";")[0].replace(",", ".")))
            except Exception:
                return
            if self._touched:
                return
            self._known_val = bool(v != 0)
            self.chk.blockSignals(True)
            self.chk.setChecked(self._known_val)
            self.chk.blockSignals(False)

        self._send("GETWINDENABLE", "0", on_done=got)

    def validatePage(self) -> bool:
        cur = bool(self.chk.isChecked())
        if self._known_val is not None:
            if cur != self._known_val:
                self._apply()
            self.wiz.wind_choice = cur
        elif self._touched:
            self.wiz.wind_choice = cur
        else:
            self.wiz.wind_choice = None  # unbekannt → Rotor-Zustand (ctrl) maßgeblich
        return True

    def isComplete(self) -> bool:
        return True

    def _apply(self) -> None:
        en = "1" if self.chk.isChecked() else "0"
        self._known_val = bool(self.chk.isChecked())
        self._touched = True

        def d1(tel, err):
            if err:
                return
            off = f"{self.sp_off.value():.1f}".replace(".", ",")
            self._send("SETWINDDIROF", off)
            self._read_sensors()

        self._send("SETWINDENABLE", en, on_done=d1)

    def _read_sensors(self) -> None:
        self._get_sync_ui("GETANEMO", self.lbl_anemo)
        self._get_sync_ui("GETWINDDIR", self.lbl_dir)


class TempPage(QWizardPage, _CmdMixin):
    def __init__(self, wiz: "CommissioningWizard") -> None:
        super().__init__()
        self.wiz = wiz
        self.setTitle(t("setup.page_temp_title"))
        self.setSubTitle(t("setup.page_temp_sub"))
        root = QVBoxLayout(self)
        info = QLabel(t("setup.temp_howto"))
        info.setWordWrap(True)
        root.addWidget(info)
        self.lbl_a = QLabel("—")
        self.lbl_m = QLabel("—")
        self.lbl_swap = QLabel("—")
        form = QFormLayout()
        form.addRow(t("setup.lbl_gettempa"), self.lbl_a)
        form.addRow(t("setup.lbl_gettempm"), self.lbl_m)
        form.addRow(t("setup.lbl_getswaptmp"), self.lbl_swap)
        root.addLayout(form)
        row = QHBoxLayout()
        b1 = QPushButton(t("setup.btn_read"))
        b1.clicked.connect(self._poll)
        b2 = QPushButton(t("setup.temp_swap"))
        b2.clicked.connect(self._swap)
        row.addWidget(b1)
        row.addWidget(b2)
        root.addLayout(row)
        self.chk_skip = QCheckBox(t("setup.temp_skip"))
        root.addWidget(self.chk_skip)
        root.addStretch(1)
        self._timer = QTimer(self)
        self._timer.setInterval(2000)
        self._timer.timeout.connect(self._poll)

    def initializePage(self) -> None:
        self._timer.start()
        self._poll()

    def cleanupPage(self) -> None:
        self._timer.stop()

    def _poll(self) -> None:
        self._get_sync_ui("GETTEMPA", self.lbl_a)
        self._get_sync_ui("GETTEMPM", self.lbl_m)
        self._get_sync_ui("GETSWAPTMP", self.lbl_swap)

    def _swap(self) -> None:
        def got(tel, err):
            cur = 0
            if tel is not None:
                try:
                    cur = int(float(str(tel.params).replace(",", ".").split(";")[0]))
                except Exception:
                    cur = 0
            nxt = 0 if cur else 1
            self._send("SETSWAPTEMP", str(nxt), on_done=lambda *_: self._poll())

        self._send("GETSWAPTMP", "0", on_done=got)


def _pf(value: Any) -> Optional[float]:
    """Zahl aus Telegramm-Parameter (Komma/Semikolon tolerant)."""
    try:
        return float(str(value or "").strip().split(";")[0].replace(",", "."))
    except Exception:
        return None


def _wire_deg(v: float) -> str:
    # Immer zwei Nachkommastellen (wie im Protokoll: 0,00 / 360,00)
    txt = f"{float(v):.2f}"
    if txt in ("-0.00", "0.00"):
        txt = "0.00"
    return txt.replace(".", ",")


def _fmt_deg(v: float) -> str:
    return f"{float(v):g}".replace(".", ",")


class CalPage(QWizardPage, _CmdMixin):
    """Encoder-Kalibrierung.

    Typ 1/2 ohne Endschalter: geführtes Einmessen im Setup-Modus
    (SETSETUP → JOG → SETPOSHERE → GETENCCOUNT → SETENCCAX/CRI → SETMAXDG → Null erneut → SETSETUP:0).
    """

    _DEFAULT_CPR = {1: 28600, 2: 160000}
    # Mechanische Null = 0,00° (SETBEGINDG:0,00 / SETPOSHERE:0,00) — kein freier Korrekturwinkel.
    _MIN_DEG = 0.0

    def __init__(self, wiz: "CommissioningWizard") -> None:
        super().__init__()
        self.wiz = wiz
        self.setTitle(t("setup.page_cal_title"))
        self.setSubTitle(t("setup.page_cal_sub"))
        root = QVBoxLayout(self)
        self.lbl_mode = QLabel("")
        self.lbl_mode.setWordWrap(True)
        root.addWidget(self.lbl_mode)

        # --- Einmessen ohne Endschalter (Typ 1/2) ----------------------------
        self.box_flow = QGroupBox(t("setup.cal_flow_group"))
        fv = QVBoxLayout(self.box_flow)
        # Immer sichtbarer Bereich: Warnung, Live-Werte und Fahrtasten
        self.top_flow = QWidget()
        tv = QVBoxLayout(self.top_flow)
        tv.setContentsMargins(0, 0, 0, 0)
        warn = QLabel(t("setup.cal_flow_warn"))
        warn.setWordWrap(True)
        warn.setStyleSheet("color: #b71c1c;")
        tv.addWidget(warn)
        live = QFormLayout()
        self.lbl_setup = QLabel("—")
        self.lbl_setup.setStyleSheet("font-weight: bold;")
        self.lbl_count = QLabel("—")
        self.lbl_raw = QLabel("—")
        live.addRow(t("setup.cal_live_setup"), self.lbl_setup)
        live.addRow(t("setup.cal_live_count"), self.lbl_count)
        live.addRow(t("setup.cal_live_raw"), self.lbl_raw)
        tv.addLayout(live)
        root.addWidget(self.top_flow)

        # Schritt 1
        g1 = QGroupBox(t("setup.cal_s1_title"))
        l1 = QVBoxLayout(g1)
        self.btn_s1 = QPushButton(t("setup.cal_s1_btn"))
        self.btn_s1.clicked.connect(self._start_setup)
        l1.addWidget(self.btn_s1)
        self.lbl_s1 = QLabel("")
        self.lbl_s1.setWordWrap(True)
        l1.addWidget(self.lbl_s1)
        fv.addWidget(g1)

        # Rotor von Hand fahren (gilt für Schritt 2, 3 und 5)
        self.g_jog = QGroupBox(t("setup.cal_jog_group"))
        jl = QHBoxLayout(self.g_jog)
        self.btn_ccw = QPushButton(t("setup.cal_jog_to_zero"))
        self.btn_cw = QPushButton(t("setup.cal_jog_to_max"))
        self.btn_ccw.setMinimumHeight(40)
        self.btn_cw.setMinimumHeight(40)
        self.btn_ccw.pressed.connect(lambda: self._jog(1))
        self.btn_ccw.released.connect(lambda: self._jog(0))
        self.btn_cw.pressed.connect(lambda: self._jog(2))
        self.btn_cw.released.connect(lambda: self._jog(0))
        jl.addWidget(self.btn_ccw)
        jl.addWidget(self.btn_cw)
        tv.addWidget(self.g_jog)

        # Schritt 2
        self.g2 = QGroupBox(t("setup.cal_s2_title"))
        l2 = QVBoxLayout(self.g2)
        h2 = QLabel(t("setup.cal_s2_howto"))
        h2.setWordWrap(True)
        l2.addWidget(h2)
        self.btn_s2 = QPushButton(t("setup.cal_s2_btn"))
        self.btn_s2.clicked.connect(self._set_zero)
        l2.addWidget(self.btn_s2)
        self.lbl_s2 = QLabel("")
        self.lbl_s2.setWordWrap(True)
        l2.addWidget(self.lbl_s2)
        fv.addWidget(self.g2)

        # Schritt 3
        self.g3 = QGroupBox(t("setup.cal_s3_title"))
        l3 = QVBoxLayout(self.g3)
        h3 = QLabel(t("setup.cal_s3_howto"))
        h3.setWordWrap(True)
        l3.addWidget(h3)
        f3 = QFormLayout()
        row_b = QHBoxLayout()
        self.sp_max = QDoubleSpinBox()
        self.sp_max.setRange(-720, 720)
        self.sp_max.setDecimals(2)
        self.sp_max.setValue(180)
        self.sp_max.setSuffix(" °")
        row_b.addWidget(self.sp_max)
        for v in (90, 180, 270, 360):
            b = QPushButton(f"{v}°")
            b.setMaximumWidth(56)
            b.clicked.connect(lambda _=False, vv=v: self.sp_max.setValue(float(vv)))
            row_b.addWidget(b)
        f3.addRow(t("setup.cal_s3_angle"), row_b)
        l3.addLayout(f3)
        self.btn_s3 = QPushButton(t("setup.cal_s3_btn"))
        self.btn_s3.clicked.connect(self._measure)
        l3.addWidget(self.btn_s3)
        self.lbl_s3 = QLabel("")
        self.lbl_s3.setWordWrap(True)
        l3.addWidget(self.lbl_s3)
        fv.addWidget(self.g3)

        # Schritt 4
        self.g4 = QGroupBox(t("setup.cal_s4_title"))
        l4 = QVBoxLayout(self.g4)
        self.btn_s4 = QPushButton(t("setup.cal_s4_btn"))
        self.btn_s4.clicked.connect(self._apply_all)
        l4.addWidget(self.btn_s4)
        self.lbl_s4 = QLabel("")
        self.lbl_s4.setWordWrap(True)
        l4.addWidget(self.lbl_s4)
        fv.addWidget(self.g4)

        # Schritt 5
        self.g5 = QGroupBox(t("setup.cal_s5_title"))
        l5 = QVBoxLayout(self.g5)
        h5 = QLabel(t("setup.cal_s5_howto"))
        h5.setWordWrap(True)
        l5.addWidget(h5)
        self.btn_s5 = QPushButton(t("setup.cal_s5_btn"))
        self.btn_s5.clicked.connect(self._finish)
        l5.addWidget(self.btn_s5)
        self.lbl_s5 = QLabel("")
        self.lbl_s5.setWordWrap(True)
        l5.addWidget(self.lbl_s5)
        fv.addWidget(self.g5)
        self.scroll_flow = _scroll(self.box_flow)
        root.addWidget(self.scroll_flow, 1)

        # --- Inklinometer ----------------------------------------------------
        self.box_inc = QGroupBox(t("setup.cal_inc_group"))
        il = QFormLayout(self.box_inc)
        self.chk_swap = QCheckBox(t("setup.inc_swapxy"))
        self.sp_cal = QDoubleSpinBox()
        self.sp_cal.setRange(-180, 180)
        self.sp_cal.setDecimals(2)
        il.addRow(self.chk_swap)
        il.addRow(t("setup.inc_cal"), self.sp_cal)
        binc = QPushButton(t("setup.btn_set_and_verify"))
        binc.clicked.connect(self._apply_inc)
        il.addRow(binc)
        btn_win = QPushButton(t("setup.inc_open_full"))
        btn_win.clicked.connect(self._open_inc_win)
        il.addRow(btn_win)
        root.addWidget(self.box_inc)
        # --- SSI -------------------------------------------------------------
        self.box_ssi = QGroupBox(t("setup.cal_ssi_group"))
        sl = QVBoxLayout(self.box_ssi)
        sl.addWidget(QLabel(t("setup.cal_ssi_hint")))
        root.addWidget(self.box_ssi)
        root.addStretch(1)

        self._setup_on = False
        self._zero_done = False
        self._zero_count = 0
        self._cpr_applied = False
        self._new_cpr: Optional[int] = None
        self._new_max: Optional[float] = None
        self._new_min: float = 0.0
        self._busy = False
        self._poll_inflight = False
        self._jog_dir = 0
        self._jog_inflight = False
        self._jog_timer = QTimer(self)
        self._jog_timer.setInterval(150)
        self._jog_timer.timeout.connect(self._jog_tick)
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(1000)
        self._poll_timer.timeout.connect(self._poll_live)

    # --- Seite ---------------------------------------------------------------

    def _flow_active(self) -> bool:
        return self.wiz.enc_type in (1, 2) and self.wiz.endstc == 0

    def initializePage(self) -> None:
        et = self.wiz.enc_type
        flow = self._flow_active()
        show_inc = inclinometer_cal_allowed(et)
        show_ssi = setenczero_primary(et)
        self.scroll_flow.setVisible(flow)
        self.top_flow.setVisible(flow)
        self.box_inc.setVisible(show_inc)
        self.box_ssi.setVisible(show_ssi)
        if flow:
            self.lbl_mode.setText(t("setup.cal_mode_flow"))
        elif show_inc:
            self.lbl_mode.setText(t("setup.cal_mode_inc"))
        elif show_ssi:
            self.lbl_mode.setText(t("setup.cal_mode_ssi"))
        else:
            self.lbl_mode.setText(t("setup.cal_mode_homing"))
        d = drive_defaults(et, self.wiz.axis_role)
        self.sp_max.setValue(float(d.get("max_dg", 180.0) or 180.0))
        self._zero_done = False
        self._new_cpr = None
        self._new_max = None
        self._cpr_applied = False
        for lb in (self.lbl_s1, self.lbl_s2, self.lbl_s3, self.lbl_s4, self.lbl_s5):
            lb.setText("")
        self._refresh_enabled()
        if flow:
            self._read_setup_state()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if self._flow_active():
            self._poll_timer.start()

    def hideEvent(self, event) -> None:  # noqa: N802
        self._poll_timer.stop()
        self._jog(0)
        super().hideEvent(event)

    def cleanupPage(self) -> None:
        self._poll_timer.stop()
        self._jog(0)
        self._setup_off_silent()

    def validatePage(self) -> bool:
        self._jog(0)
        if self._setup_on and self._flow_active():
            # Versehentliches "Weiter" abfangen: Setup läuft noch, Einmessen nicht abgeschlossen.
            reply = QMessageBox.question(
                self,
                t("setup.title"),
                t("setup.cal_leave_confirm"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return False
        # Setup-Modus nie "offen lassen": Achsgrenzen und Stall müssen wieder aktiv sein.
        self._setup_off_silent()
        return True

    def _setup_off_silent(self) -> None:
        if self._setup_on or getattr(self.wiz, "_setup_dst", None) is not None:
            self.wiz.force_setup_off()
        self._setup_on = False
        self._refresh_enabled()

    # --- Zustand -------------------------------------------------------------

    def _refresh_enabled(self) -> None:
        on = bool(self._setup_on)
        busy = bool(self._busy)
        self.btn_s1.setEnabled(not busy and not on)
        self.g_jog.setEnabled(on)
        self.g2.setEnabled(on)
        self.btn_s2.setEnabled(on and not busy)
        self.g3.setEnabled(on and self._zero_done)
        self.btn_s3.setEnabled(on and self._zero_done and not busy)
        self.g4.setEnabled(self._new_cpr is not None)
        self.btn_s4.setEnabled(self._new_cpr is not None and not busy)
        self.g5.setEnabled(on and self._cpr_applied)
        self.btn_s5.setEnabled(on and self._cpr_applied and not busy)
        self.lbl_setup.setText(
            t("setup.cal_on") if on else t("setup.cal_off")
        )

    def _show_step(self, w: QWidget) -> None:
        """Nächsten Schritt in den sichtbaren Bereich scrollen."""
        QTimer.singleShot(0, lambda: self.scroll_flow.ensureWidgetVisible(w, 10, 10))

    def _set_busy(self, busy: bool) -> None:
        self._busy = bool(busy)
        self._refresh_enabled()

    # --- Helfer --------------------------------------------------------------

    @staticmethod
    def _ack_params(tel: Any, err: Any, ack: str) -> tuple[Optional[str], str]:
        """(params, fehlertext). params=None bei Fehler/NAK."""
        if err:
            return None, str(err)
        if tel is None:
            return None, "keine Antwort"
        cmd_u = str(getattr(tel, "cmd", "") or "").strip().upper()
        par = str(getattr(tel, "params", "") or "").strip()
        if cmd_u.startswith("NAK_"):
            return None, f"{cmd_u} {par}".strip()
        a = ack.upper()
        alts = {a, a.replace("GET", "", 1), a.replace("SET", "", 1)}
        if any(cmd_u.startswith(x) for x in alts if x):
            return par, ""
        return None, f"unerwartet: {cmd_u}"

    def _cmd(
        self,
        cmd: str,
        params: str,
        cb: Callable[[Optional[str], str], None],
        timeout: float = 1.5,
    ) -> None:
        def done(tel, err):
            p, e = self._ack_params(tel, err, "ACK_" + cmd)
            cb(p, e)

        self._send(cmd, params, on_done=done, timeout_s=timeout)

    def _cpr_cmds(self) -> tuple[str, str]:
        """(GET, SET) für Counts pro 360° passend zum Encoder-Typ."""
        if int(self.wiz.enc_type or 1) == 2:
            return "GETENCCRI", "SETENCCRI"
        return "GETENCCAX", "SETENCCAX"

    def _wait_alive(self, cb: Callable[[bool], None], attempt: int = 0, first_delay: int = 2500) -> None:
        """Nach Neustart warten, bis der Rotor wieder antwortet."""

        def probe() -> None:
            def got(p, e):
                if p is not None:
                    cb(True)
                elif attempt + 1 < 10:
                    self._wait_alive(cb, attempt + 1, first_delay=1500)
                else:
                    cb(False)

            self._cmd("GETSETUP", "0", got, timeout=1.5)

        QTimer.singleShot(first_delay if attempt == 0 else 1500, probe)

    # --- Live ----------------------------------------------------------------

    def _read_setup_state(self) -> None:
        def got(p, e):
            v = _pf(p)
            self._setup_on = bool(v is not None and int(v) == 1)
            if self._setup_on:
                self.wiz._setup_dst = int(self.wiz.slave_dst())
            self._refresh_enabled()

        self._cmd("GETSETUP", "0", got)

    def _poll_live(self) -> None:
        if (
            self._busy
            or self._poll_inflight
            or not self._flow_active()
            or not self.scroll_flow.isVisibleTo(self)
        ):
            return
        self._poll_inflight = True

        def got_raw(p, e):
            self._poll_inflight = False
            v = _pf(p)
            self.lbl_raw.setText("—" if v is None else f"{v:.2f} °".replace(".", ","))

        def got_count(p, e):
            v = _pf(p)
            self.lbl_count.setText("—" if v is None else str(int(v)))
            self._cmd("GETPOSDG", "0", got_raw, timeout=0.8)

        self._cmd("GETENCCOUNT", "0", got_count, timeout=0.8)

    # --- JOG (roh, ohne App-Halte-Logik) ---------------------------------------

    def _jog(self, direction: int) -> None:
        d = int(direction)
        self._jog_dir = d
        if d:
            if not self._jog_timer.isActive():
                self._jog_timer.start()
            self._jog_tick()
        else:
            was = self._jog_timer.isActive()
            self._jog_timer.stop()
            if was:
                self._jog_inflight = False
                self._send_jog(0)

    def _jog_tick(self) -> None:
        if not self._jog_dir or self._jog_inflight:
            return
        self._jog_inflight = True
        self._send_jog(self._jog_dir)

    def _send_jog(self, d: int) -> None:
        # Bewusst direkt JOG:n senden: ctrl.jog_*() würde beim Loslassen ein SETPOSDG zum
        # "Halten" schicken — außerhalb der Achsgrenzen (Setup) würde der Rotor dann zurückfahren.
        def done(tel, err):
            self._jog_inflight = False
            p, e = self._ack_params(tel, err, "ACK_JOG")
            if p is None and "NAK" in e.upper():
                self._jog_timer.stop()
                self._jog_dir = 0
                self.lbl_s2.setText(t("setup.cal_jog_nak", err=e))

        self._send("JOG", str(int(d)), on_done=done, timeout_s=0.5)

    # --- Schritt 1: Setup-Modus ------------------------------------------------

    def _start_setup(self) -> None:
        if self._busy:
            return
        reply = QMessageBox.question(
            self, t("setup.title"), t("setup.cal_s1_confirm")
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._set_busy(True)
        self.lbl_s1.setText(t("setup.working"))
        get_cmd, set_cmd = self._cpr_cmds()
        self._s1_note = ""

        def fail(msg: str) -> None:
            self._set_busy(False)
            self.lbl_s1.setText(t("setup.cal_s1_fail", err=msg))

        def done_ok(*_a) -> None:
            self._setup_on = True
            self.wiz._setup_dst = int(self.wiz.slave_dst())
            self._zero_done = False
            self._cpr_applied = False
            self._new_cpr = None
            self._set_busy(False)
            self.lbl_s1.setText(self._s1_note + t("setup.cal_s1_ok"))
            self._show_step(self.g2)

        def after_maxdg(p, e):
            # Nicht kritisch: das endgültige Maximum wird in Schritt 4 gesetzt.
            done_ok()

        def got_setup(p, e):
            v = _pf(p)
            if v is not None and int(v) == 1:
                self.wiz._setup_dst = int(self.wiz.slave_dst())
                # Vorläufiges Maximum großzügig (≥ 360°), damit die Null-/Max-Fahrt nicht anstößt
                vmax = max(360.0, float(self.sp_max.value()))
                self._cmd("SETMAXDG", _wire_deg(vmax), after_maxdg)
            else:
                fail(e or t("setup.cal_s1_not_on"))

        def after_setup(p, e):
            if p is None:
                fail(e)
                return
            self.wiz._setup_dst = int(self.wiz.slave_dst())
            self._cmd("GETSETUP", "0", got_setup)

        def enable_setup() -> None:
            self._cmd("SETSETUP", "1", after_setup)

        def after_cpr_set(p, e):
            if p is None and "NAK" in e.upper():
                fail(e)
                return
            # Rotor kann nach Änderung neu starten → warten, bis er wieder antwortet
            self._wait_alive(
                lambda alive: enable_setup()
                if alive
                else fail(t("setup.cal_no_answer"))
            )

        def got_cpr(p, e):
            v = _pf(p)
            if v is not None and v > 0:
                enable_setup()
                return
            default = int(self._DEFAULT_CPR.get(int(self.wiz.enc_type or 1), 28600))
            self._s1_note = t("setup.cal_s1_cpr_default", cpr=default) + "\n"
            self.lbl_s1.setText(t("setup.working"))
            self._cmd(set_cmd, str(default), after_cpr_set, timeout=2.0)

        # Winkel-Setzen braucht eine gesetzte CPR (sonst NAK BADCPR) → vorher prüfen
        self._cmd(get_cmd, "0", got_cpr)

    # --- Aktuelle Position als Winkel setzen ----------------------------------------

    def _set_here(self, angle: float, cb: Callable[[Optional[str], str], None]) -> None:
        """SETPOSHERE:<winkel>; bei NAK Ersatz SETENCCOUNT:0 (nur für 0°)."""

        def after(p, e):
            if p is not None:
                cb(p, "")
                return
            if "NAK" in e.upper() and abs(angle) < 1e-9:
                self._cmd("SETENCCOUNT", "0", lambda p2, e2: cb(p2, e2 if p2 is None else ""))
                return
            cb(None, e)

        self._cmd("SETPOSHERE", _wire_deg(angle), after, timeout=3.0)

    # --- Schritt 2: Nullpunkt --------------------------------------------------

    def _set_zero(self) -> None:
        if self._busy or not self._setup_on:
            return
        self._jog(0)
        self._set_busy(True)
        self.lbl_s2.setText(t("setup.working"))
        angle = self._MIN_DEG  # mechanische Null ist immer 0,00° (Amin = 0)

        def fail(msg: str) -> None:
            self._set_busy(False)
            if "BADCPR" in msg.upper():
                self.lbl_s2.setText(t("setup.cal_s2_badcpr"))
            else:
                self.lbl_s2.setText(t("setup.cal_s2_fail", err=msg))

        def got_count(p, e):
            v = _pf(p)
            if v is None:
                fail(e or "?")
                return
            self._zero_count = int(v)
            self._zero_done = True
            self._cpr_applied = False
            self._new_cpr = None
            self._set_busy(False)
            self.lbl_s2.setText(t("setup.cal_s2_ok", count=int(v)))
            self._show_step(self.g3)

        def after_here(p, e):
            if p is None:
                fail(e)
                return
            self._cmd("GETENCCOUNT", "0", got_count)

        self._set_here(angle, after_here)

    # --- Schritt 3: einmessen --------------------------------------------------

    def _measure(self) -> None:
        if self._busy or not self._zero_done:
            self.lbl_s3.setText(t("setup.cal_s3_need_zero"))
            return
        self._jog(0)
        vmax = float(self.sp_max.value())
        vmin = self._MIN_DEG
        span = vmax - vmin
        self._new_cpr = None
        self._cpr_applied = False
        self._set_busy(True)
        self.lbl_s3.setText(t("setup.working"))
        get_cmd, _set_cmd = self._cpr_cmds()
        zero = int(self._zero_count)

        def fail(msg: str) -> None:
            self._set_busy(False)
            self.lbl_s3.setText(t("setup.cal_s3_fail", err=msg))

        def got_cpr(p, e, counts: int):
            old = _pf(p)
            cpr = cpr_for_range(zero, int(counts))
            self._set_busy(False)
            if cpr is None:
                self.lbl_s3.setText(t("setup.cal_s3_small", count=counts))
                return
            self._new_cpr = int(cpr)
            self._new_max = vmax
            self._new_min = vmin
            dev = ""
            if old is not None and old > 0:
                dev = f"{(int(cpr) - int(old)) * 100.0 / float(old):+.1f}".replace(".", ",")
            self.lbl_s3.setText(
                t(
                    "setup.cal_s3_result",
                    count=counts,
                    angle=_fmt_deg(vmax),
                    new=int(cpr),
                    old="?" if old is None else int(old),
                    dev=dev or "?",
                )
            )
            self._refresh_enabled()
            self._show_step(self.g4)

        def got_count(p, e):
            v = _pf(p)
            if v is None:
                fail(e or "?")
                return
            counts = int(v)
            # Richtung: Max-Stellung muss "über" dem Nullpunkt liegen (span > 0)
            if (span > 0 and counts <= zero) or (span < 0 and counts >= zero):
                self._set_busy(False)
                self.lbl_s3.setText(t("setup.cal_s3_neg", count=counts, zero=zero))
                return
            self._cmd(get_cmd, "0", lambda pp, ee, c=counts: got_cpr(pp, ee, c))

        self._cmd("GETENCCOUNT", "0", got_count)

    # --- Schritt 4: CPR/Grenzen übernehmen ---------------------------------------

    def _apply_all(self) -> None:
        if self._busy or self._new_cpr is None or self._new_max is None:
            return
        cpr = int(self._new_cpr)
        vmax = float(self._new_max)
        vmin = float(self._new_min)
        reply = QMessageBox.question(
            self,
            t("setup.title"),
            t("setup.cal_s4_confirm", cpr=cpr, max=_fmt_deg(vmax), min=_fmt_deg(vmin)),
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._jog(0)
        self._set_busy(True)
        self._cpr_applied = False
        self.lbl_s4.setText(t("setup.working"))
        get_cmd, set_cmd = self._cpr_cmds()

        def fail(step: str, msg: str) -> None:
            self._set_busy(False)
            self.lbl_s4.setText(t("setup.cal_s4_fail", step=step, err=msg))

        def got_setup(p, e):
            v = _pf(p)
            if v is None or int(v) != 1:
                fail("SETSETUP", e or t("setup.cal_s1_not_on"))
                return
            self.wiz._setup_dst = int(self.wiz.slave_dst())
            self._setup_on = True
            self._cpr_applied = True
            self._zero_done = False  # Nullpunkt muss nach CPR-Änderung neu gesetzt werden
            self._set_busy(False)
            self.lbl_s4.setText(t("setup.cal_s4_ok", cpr=cpr, max=_fmt_deg(vmax), min=_fmt_deg(vmin)))
            self._show_step(self.g5)

        def after_setup(p, e):
            if p is None:
                fail("SETSETUP", e)
                return
            self._cmd("GETSETUP", "0", got_setup)

        def after_min(p, e):
            if p is None:
                fail("SETBEGINDG", e)
                return
            # Nach CPR-Änderung (Neustart) ist der Setup-Modus aus → für Schritt 5 wieder an
            self._cmd("SETSETUP", "1", after_setup)

        def after_max(p, e):
            if p is None:
                fail("SETMAXDG", e)
                return
            self._cmd("SETBEGINDG", _wire_deg(vmin), after_min)

        def write_limits() -> None:
            self._cmd("SETMAXDG", _wire_deg(vmax), after_max)

        def verify_cpr(attempt: int) -> None:
            def got(p, e):
                v = _pf(p)
                if v is not None and int(v) == cpr:
                    write_limits()
                elif attempt + 1 < 8:
                    QTimer.singleShot(1500, lambda: verify_cpr(attempt + 1))
                else:
                    fail(set_cmd, e or f"{v}")

            self._cmd(get_cmd, "0", got, timeout=1.5)

        def after_cpr(p, e):
            if p is None and "NAK" in e.upper():
                fail(set_cmd, e)
                return
            # Bei geändertem Wert startet der Rotor neu → kurz warten, dann prüfen
            QTimer.singleShot(2500, lambda: verify_cpr(0))

        self._cmd(set_cmd, str(cpr), after_cpr, timeout=2.0)

    # --- Schritt 5: Null kontrollieren, Setup beenden -----------------------------

    def _finish(self) -> None:
        if self._busy or not self._setup_on or not self._cpr_applied:
            return
        self._jog(0)
        self._set_busy(True)
        self.lbl_s5.setText(t("setup.working"))
        vmin = self._MIN_DEG
        get_cmd, _set_cmd = self._cpr_cmds()
        notes: list[str] = []

        def fail(msg: str) -> None:
            self._set_busy(False)
            self.lbl_s5.setText(t("setup.cal_s5_fail", err=msg))

        def summary(cb: Callable[[str], None]) -> None:
            """Echte Rotorwerte zurücklesen (Counts, CPR, Amin/Amax, Korrekturwinkel)."""
            keys = (
                ("GETENCCOUNT", "counts"),
                (get_cmd, "cpr"),
                ("GETBEGINDG", "amin"),
                ("GETMAXDG", "amax"),
                ("GETDGCAL", "dgcal"),
            )
            vals: dict[str, str] = {}

            def step(i: int) -> None:
                if i >= len(keys):
                    cb(
                        t(
                            "setup.cal_s5_summary",
                            counts=vals.get("counts", "?"),
                            cpr=vals.get("cpr", "?"),
                            amin=vals.get("amin", "?"),
                            amax=vals.get("amax", "?"),
                            dgcal=vals.get("dgcal", "?"),
                        )
                    )
                    return
                cmd, key = keys[i]

                def got(p, e):
                    vals[key] = "?" if p is None else str(p).split(";")[0].strip()
                    step(i + 1)

                self._cmd(cmd, "0", got, timeout=1.5)

            step(0)

        def final(p_ref, _e, pos: Optional[float]) -> None:
            ref_ok = _pf(p_ref) == 1
            self._setup_on = False
            self.wiz._setup_dst = None
            self._cpr_applied = False
            near = pos is not None and abs(pos - vmin) <= 1.0
            if ref_ok and near:
                key = "setup.cal_s5_ok"
            elif ref_ok:
                key = "setup.cal_s5_off"
            else:
                key = "setup.cal_s5_noref"
            head = t(key, pos="?" if pos is None else f"{pos:.2f}".replace(".", ","), min=_fmt_deg(vmin))

            def show(summ: str) -> None:
                self._set_busy(False)
                self.lbl_s5.setText("\n".join([head, *notes, summ]))

            summary(show)

        def after_ref(p, e):
            ref_p = p

            def got_final_pos(pp, ee):
                final(ref_p, e, _pf(pp))

            self._cmd("GETPOSDG", "0", got_final_pos)

        def after_setup_off(p, e):
            self._cmd("GETREF", "0", after_ref)

        def leave_setup() -> None:
            self._cmd("SETSETUP", "0", after_setup_off)

        def got_dgcal(p, e):
            v = _pf(p)
            if v is not None and abs(v) > 1e-6:
                # Kein Korrekturwinkel nötig (und störend): Counts/CPR bestimmen den Winkel allein.
                def cleared(pp, ee, old=v):
                    if pp is not None:
                        notes.append(t("setup.cal_s5_dgcal_cleared", old=f"{old:.1f}".replace(".", ",")))
                    leave_setup()

                self._cmd("SETDGCAL", "0,0", cleared)
            else:
                leave_setup()

        def got_pos(p, e):
            self._cmd("GETDGCAL", "0", got_dgcal)

        def after_here(p, e):
            if p is None:
                fail(e)
                return
            self._cmd("GETPOSDG", "0", got_pos)

        self._set_here(vmin, after_here)

    def _apply_inc(self) -> None:
        self._send("SETINCSWAPXY", "1" if self.chk_swap.isChecked() else "0")
        cal = f"{self.sp_cal.value():.2f}".replace(".", ",")
        self._send("SETINCCAL", cal)

    def _open_inc_win(self) -> None:
        from .inclinometer_config_window import InclinometerConfigWindow

        win = InclinometerConfigWindow(
            self, ctrl=self.wiz.ctrl, get_dst=self.wiz.slave_dst
        )
        win.show()


class ZeroRefPage(QWizardPage, _CmdMixin):
    def __init__(self, wiz: "CommissioningWizard") -> None:
        super().__init__()
        self.wiz = wiz
        self.setTitle(t("setup.page_zero_title"))
        self.setSubTitle(t("setup.page_zero_sub"))
        root = QVBoxLayout(self)
        self.lbl = QLabel("")
        self.lbl.setWordWrap(True)
        root.addWidget(self.lbl)
        self.lbl_ref = QLabel("—")
        self.lbl_move = QLabel("—")
        self.lbl_pos = QLabel("—")
        form = QFormLayout()
        form.addRow(t("setup.lbl_getref"), self.lbl_ref)
        form.addRow(t("setup.lbl_getmove"), self.lbl_move)
        form.addRow(t("setup.lbl_getposdg"), self.lbl_pos)
        root.addLayout(form)
        self.btn_homing = QPushButton(t("setup.zero_homing"))
        self.btn_homing.clicked.connect(lambda: self._setref(True))
        self.btn_manual = QPushButton(t("setup.zero_manual"))
        self.btn_manual.clicked.connect(lambda: self._setref(True))
        self.btn_ssi = QPushButton(t("setup.zero_ssi"))
        self.btn_ssi.clicked.connect(self._ssi_zero)
        self.btn_poll = QPushButton(t("setup.btn_read"))
        self.btn_poll.clicked.connect(self._poll)
        # JOG
        row = QHBoxLayout()
        self.btn_ccw = QPushButton(t("setup.jog_minus"))
        self.btn_cw = QPushButton(t("setup.jog_plus"))
        self.btn_ccw.pressed.connect(lambda: self._jog(1))
        self.btn_ccw.released.connect(lambda: self._jog(0))
        self.btn_cw.pressed.connect(lambda: self._jog(2))
        self.btn_cw.released.connect(lambda: self._jog(0))
        row.addWidget(self.btn_ccw)
        row.addWidget(self.btn_cw)
        root.addLayout(row)
        root.addWidget(self.btn_homing)
        root.addWidget(self.btn_manual)
        root.addWidget(self.btn_ssi)
        root.addWidget(self.btn_poll)
        root.addStretch(1)
        self._jog_timer = QTimer(self)
        self._jog_timer.setInterval(80)
        self._jog_dir = 0
        self._jog_timer.timeout.connect(self._jog_tick)
        self._wait = QTimer(self)
        self._wait.setInterval(500)
        self._wait.timeout.connect(self._poll)

    def initializePage(self) -> None:
        et, es = self.wiz.enc_type, self.wiz.endstc
        self.btn_homing.setVisible(homing_setref_allowed(et, es))
        self.btn_manual.setVisible(manual_setref_allowed(et, es))
        self.btn_ssi.setVisible(setenczero_primary(et))
        j = jog_allowed(et, es)
        self.btn_ccw.setVisible(j)
        self.btn_cw.setVisible(j)
        if inclinometer_cal_allowed(et):
            self.lbl.setText(t("setup.zero_hint_inc"))
        elif setenczero_primary(et):
            self.lbl.setText(t("setup.zero_hint_ssi"))
        elif homing_setref_allowed(et, es):
            self.lbl.setText(t("setup.zero_hint_homing"))
        else:
            self.lbl.setText(t("setup.zero_hint_manual"))
        self._poll()
        self._wait.start()

    def cleanupPage(self) -> None:
        self._wait.stop()
        self._jog(0)

    def _jog(self, d: int) -> None:
        self._jog_dir = d
        if d:
            self._jog_tick()
            self._jog_timer.start()
        else:
            self._jog_timer.stop()
            self._jog_tick()

    def _jog_tick(self) -> None:
        ctrl = self.wiz.ctrl
        if not ctrl:
            return
        try:
            if self.wiz.axis_role == "el":
                ctrl.jog_el(self._jog_dir)
            else:
                ctrl.jog_az(self._jog_dir)
        except Exception:
            pass

    def _setref(self, start: bool) -> None:
        self._send("SETREF", "1" if start else "0")

    def _ssi_zero(self) -> None:
        self._send("SETENCZERO", "1", on_done=lambda *_: self._poll())

    def _poll(self) -> None:
        self._get_sync_ui("GETREF", self.lbl_ref)
        self._get_sync_ui("GETMOVE", self.lbl_move)
        self._get_sync_ui("GETPOSDG", self.lbl_pos)


class VerifyPage(QWizardPage, _CmdMixin):
    def __init__(self, wiz: "CommissioningWizard") -> None:
        super().__init__()
        self.wiz = wiz
        self.setTitle(t("setup.page_verify_title"))
        self.setSubTitle(t("setup.page_verify_sub"))
        root = QVBoxLayout(self)
        form = QFormLayout()
        self.sp_target = QDoubleSpinBox()
        self.sp_target.setRange(0, 720)
        self.sp_target.setDecimals(2)
        self.sp_target.setValue(45)
        form.addRow(t("setup.verify_target"), self.sp_target)
        self.lbl_ref = QLabel("—")
        self.lbl_err = QLabel("—")
        self.lbl_move = QLabel("—")
        self.lbl_pos = QLabel("—")
        form.addRow(t("setup.lbl_getref"), self.lbl_ref)
        form.addRow(t("setup.lbl_geterr"), self.lbl_err)
        form.addRow(t("setup.lbl_getmove"), self.lbl_move)
        form.addRow(t("setup.lbl_getposdg"), self.lbl_pos)
        root.addLayout(form)
        row = QHBoxLayout()
        b1 = QPushButton(t("setup.verify_go"))
        b1.clicked.connect(self._go)
        b2 = QPushButton(t("setup.verify_stop"))
        b2.clicked.connect(lambda: self._send("STOP", "0"))
        b3 = QPushButton(t("setup.btn_read"))
        b3.clicked.connect(self._poll)
        row.addWidget(b1)
        row.addWidget(b2)
        row.addWidget(b3)
        root.addLayout(row)
        root.addStretch(1)
        self._timer = QTimer(self)
        self._timer.setInterval(400)
        self._timer.timeout.connect(self._poll)

    def initializePage(self) -> None:
        self._poll()

    def cleanupPage(self) -> None:
        self._timer.stop()

    def _go(self) -> None:
        tgt = f"{self.sp_target.value():.2f}".replace(".", ",")
        self._send("SETPOSDG", tgt)
        self._timer.start()

    def _poll(self) -> None:
        self._get_sync_ui("GETREF", self.lbl_ref)
        self._get_sync_ui("GETERR", self.lbl_err)
        self._get_sync_ui("GETMOVE", self.lbl_move)
        self._get_sync_ui("GETPOSDG", self.lbl_pos)


class AntennaPage(QWizardPage, _CmdMixin):
    def __init__(self, wiz: "CommissioningWizard") -> None:
        super().__init__()
        self.wiz = wiz
        self.setTitle(t("setup.page_ant_title"))
        self.setSubTitle(t("setup.page_ant_sub"))
        root = QVBoxLayout(self)
        tip = QLabel(t("setup.ant_hint"))
        tip.setWordWrap(True)
        tip.setStyleSheet("color: gray;")
        root.addWidget(tip)
        self._names = AntennaNamesPanel(self, use_setup_labels=True)
        root.addWidget(_scroll(self._names))

        # Masthöhe: GETMASTHEIGHT / SETMASTHEIGHT am AZ-Rotor (NVS); Cache auch in ui.antenna_height_m
        mast_row = QHBoxLayout()
        mast_row.setContentsMargins(0, 8, 0, 0)
        mast_row.setSpacing(8)
        self._lbl_mast = QLabel(t("settings.antenna_height"))
        self.sp_antenna_height = QDoubleSpinBox()
        self.sp_antenna_height.setRange(0.0, 9999.0)
        self.sp_antenna_height.setDecimals(1)
        self.sp_antenna_height.setSingleStep(0.5)
        self.sp_antenna_height.setSuffix(" m")
        self.sp_antenna_height.setMinimumWidth(110)
        self.sp_antenna_height.setToolTip(tt("settings.antenna_height_tooltip"))
        self._lbl_mast.setToolTip(tt("settings.antenna_height_tooltip"))
        mast_hint = QLabel(t("setup.ant_mast_rotor_hint"))
        mast_hint.setWordWrap(True)
        mast_hint.setStyleSheet("color: gray;")
        mast_row.addWidget(self._lbl_mast)
        mast_row.addWidget(self.sp_antenna_height)
        mast_row.addStretch(1)
        root.addLayout(mast_row)
        root.addWidget(mast_hint)

        btn_row = QHBoxLayout()
        btn_read = QPushButton(t("setup.btn_read"))
        btn_read.clicked.connect(self._read_from_rotor)
        btn = QPushButton(t("setup.btn_set_and_verify"))
        btn.clicked.connect(self._apply)
        btn_row.addWidget(btn_read)
        btn_row.addWidget(btn)
        root.addLayout(btn_row)
        self.lbl = QLabel("")
        root.addWidget(self.lbl)
        self._read_gen = 0

    def _load_mast_height(self) -> None:
        ui = self.wiz.cfg.get("ui") if isinstance(self.wiz.cfg.get("ui"), dict) else {}
        try:
            h = float(ui.get("antenna_height_m", 0.0) or 0.0)
        except (TypeError, ValueError):
            h = 0.0
        self.sp_antenna_height.blockSignals(True)
        self.sp_antenna_height.setValue(max(0.0, min(9999.0, h)))
        self.sp_antenna_height.blockSignals(False)

    def _save_mast_height(self) -> None:
        ui = self.wiz.cfg.setdefault("ui", {})
        if not isinstance(ui, dict):
            self.wiz.cfg["ui"] = {}
            ui = self.wiz.cfg["ui"]
        ui["antenna_height_m"] = float(self.sp_antenna_height.value())

    def initializePage(self) -> None:
        self._names.load_from_cfg(self.wiz.cfg)
        self._load_mast_height()
        self._apply_from_ctrl_cache()
        # Sofort + einmal nachziehen (erster Besuch oft vor fertigem Bus-Cache).
        self._read_gen += 1
        gen = self._read_gen
        QTimer.singleShot(0, lambda g=gen: self._read_from_rotor(gen=g))
        QTimer.singleShot(700, lambda g=gen: self._read_from_rotor(gen=g))

    def _az_dst(self) -> int:
        rb = self.wiz.cfg.get("rotor_bus") or {}
        try:
            return int(rb.get("slave_az", 20) or 20)
        except (TypeError, ValueError):
            return 20

    def _dst(self) -> int:
        # Antennen liegen am AZ-Rotor — nicht an der ggf. gewählten EL-Achse.
        return self._az_dst()

    def _apply_from_ctrl_cache(self) -> None:
        ctrl = self.wiz.ctrl
        az = getattr(ctrl, "az", None) if ctrl is not None else None
        if az is None:
            return
        names: list[str] = []
        offs: list[float] = []
        dips: list[bool] = []
        for i in range(1, 4):
            nm = getattr(az, f"antname{i}", None)
            if nm is not None:
                names.append(sanitize_antenna_name(str(nm)))
            off = getattr(az, f"antoff{i}", None)
            if off is not None:
                try:
                    offs.append(float(off))
                except (TypeError, ValueError):
                    pass
            dp = getattr(az, f"antdp{i}", None)
            if dp is not None:
                dips.append(bool(dp))
        if len(names) == 3:
            self._names.set_names(names)
        if len(offs) == 3:
            self._names.set_offsets(offs)
        if len(dips) == 3:
            self._names.set_dipoles(dips)
        mh = getattr(az, "mast_height_m", None)
        if mh is not None:
            try:
                self.sp_antenna_height.blockSignals(True)
                self.sp_antenna_height.setValue(max(0.0, min(9999.0, float(mh))))
                self.sp_antenna_height.blockSignals(False)
                self._save_mast_height()
            except (TypeError, ValueError):
                pass

    def _request_antenna_poll(self) -> None:
        ctrl = self.wiz.ctrl
        if ctrl is None:
            return
        for fn_name in (
            "request_antenna_offsets",
            "request_antenna_dipoles",
            "request_antenna_names",
            "request_mast_height",
        ):
            fn = getattr(ctrl, fn_name, None)
            if callable(fn):
                try:
                    fn(priority=0)
                except TypeError:
                    try:
                        fn()
                    except Exception:
                        pass
                except Exception:
                    pass

    def _read_from_rotor(self, *, gen: Optional[int] = None) -> None:
        if gen is not None and gen != self._read_gen:
            return
        self._request_antenna_poll()
        self.lbl.setText(t("setup.ant_reading"))
        fields: list[tuple[str, str, int]] = []
        for i in range(1, 4):
            fields.append((f"GETANTNAME{i}", "name", i - 1))
            fields.append((f"GETANTOFF{i}", "off", i - 1))
            fields.append((f"GETANTDP{i}", "dp", i - 1))
        fields.append(("GETMASTHEIGHT", "mast", 0))
        names = list(self._names.names())
        offs = list(self._names.offsets())
        dips = list(self._names.dipoles())
        mast_h = float(self.sp_antenna_height.value())
        n_ok = 0

        def step(idx: int) -> None:
            nonlocal n_ok, mast_h
            if gen is not None and gen != self._read_gen:
                return
            if idx >= len(fields):
                self._names.set_names(names)
                self._names.set_offsets(offs)
                self._names.set_dipoles(dips)
                self.sp_antenna_height.blockSignals(True)
                self.sp_antenna_height.setValue(mast_h)
                self.sp_antenna_height.blockSignals(False)
                if n_ok > 0:
                    self._names.apply_to_cfg(self.wiz.cfg)
                    self._save_mast_height()
                    self.lbl.setText(t("setup.ant_read_ok", n=n_ok))
                else:
                    self.lbl.setText(t("setup.ant_read_fail"))
                return
            cmd, kind, slot = fields[idx]

            def got(tel, err) -> None:
                nonlocal n_ok, mast_h
                if not err and tel is not None:
                    raw = str(getattr(tel, "params", "") or "")
                    cmd_u = str(getattr(tel, "cmd", "") or "").upper()
                    if not cmd_u.startswith("NAK_"):
                        try:
                            if kind == "name":
                                names[slot] = sanitize_antenna_name(raw)
                                n_ok += 1
                            elif kind == "off":
                                offs[slot] = max(
                                    0.0,
                                    min(
                                        360.0,
                                        float(raw.strip().split(";")[0].replace(",", ".")),
                                    ),
                                )
                                n_ok += 1
                            elif kind == "mast":
                                mast_h = max(
                                    0.0,
                                    min(
                                        9999.0,
                                        float(raw.strip().split(";")[0].replace(",", ".")),
                                    ),
                                )
                                n_ok += 1
                            else:
                                dips[slot] = bool(
                                    int(
                                        float(
                                            raw.strip().split(";")[0].replace(",", ".")
                                        )
                                    )
                                )
                                n_ok += 1
                        except (TypeError, ValueError):
                            pass
                QTimer.singleShot(40, lambda: step(idx + 1))

            self._send(cmd, "0", on_done=got, timeout_s=1.5)

        step(0)

    def _apply(self) -> None:
        self._names.apply_to_cfg(self.wiz.cfg)
        self._save_mast_height()
        if callable(self.wiz.save_cfg_cb):
            self.wiz.save_cfg_cb(self.wiz.cfg)
        names = self._names.names()
        offs = self._names.offsets()
        dips = self._names.dipoles()
        mast_param = f"{float(self.sp_antenna_height.value()):.1f}".replace(".", ",")
        queue: list[tuple[str, str]] = []
        for i in range(1, 4):
            queue.append((f"SETANTNAME{i}", names[i - 1] or " "))
            queue.append(
                (f"SETANTOFF{i}", f"{float(offs[i - 1]):.1f}".replace(".", ","))
            )
            queue.append((f"SETANTDP{i}", "1" if dips[i - 1] else "0"))
        queue.append(("SETMASTHEIGHT", mast_param))
        self.lbl.setText(t("setup.working"))
        self._queue = list(queue)

        def run_next() -> None:
            if not self._queue:
                self.lbl.setText(t("setup.ant_saved"))
                return
            cmd, params = self._queue.pop(0)

            def done(tel, err):
                if err or tel is None:
                    self.lbl.setText(t("setup.set_fail", err=f"{cmd}: {err}"))
                    return
                QTimer.singleShot(60, run_next)

            self._send(cmd, params, on_done=done)

        run_next()

    def validatePage(self) -> bool:
        self._names.apply_to_cfg(self.wiz.cfg)
        self._save_mast_height()
        if callable(self.wiz.save_cfg_cb):
            self.wiz.save_cfg_cb(self.wiz.cfg)
        return True


class ServicesPage(QWizardPage):
    def __init__(self, wiz: "CommissioningWizard") -> None:
        super().__init__()
        self.wiz = wiz
        self.setTitle(t("setup.page_svc_title"))
        self.setSubTitle(t("setup.page_svc_sub"))
        self._panel = ProtocolsServicesPanel(
            self.wiz.cfg,
            self,
            open_com0com_cb=self._open_com0com,
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(_scroll(self._panel))

    def _open_com0com(self) -> None:
        if callable(self.wiz.open_settings_tab_cb):
            self.wiz.open_settings_tab_cb("protocols")

    def initializePage(self) -> None:
        self._panel.set_cfg(self.wiz.cfg)
        self._panel.load_from_cfg()

    def validatePage(self) -> bool:
        self._panel.apply_to_cfg(self.wiz.cfg)
        if callable(self.wiz.save_cfg_cb):
            self.wiz.save_cfg_cb(self.wiz.cfg)
        if callable(self.wiz.apply_services_cb):
            try:
                self.wiz.apply_services_cb()
            except Exception:
                pass
        return True


class AlertsPage(QWizardPage):
    """Optional: gleiche Warnschwellen-UI wie Einstellungen → Wetter.

    Keine „Überspringen“-Checkbox: die sperrte versehentlich die Temperaturfelder
    (Weiter ohne Änderung reicht als Optionalität).
    """

    def __init__(self, wiz: "CommissioningWizard") -> None:
        super().__init__()
        self.wiz = wiz
        self.setTitle(t("setup.page_alerts_title"))
        self.setSubTitle(t("setup.page_alerts_sub"))
        inner = QWidget()
        root = QVBoxLayout(inner)
        hint = QLabel(t("setup.alerts_hint"))
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray;")
        root.addWidget(hint)
        self._tab = WeatherThresholdsTab(self.wiz.cfg, self)
        root.addWidget(self._tab)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(_scroll(inner))

    def initializePage(self) -> None:
        self._tab._cfg = self.wiz.cfg
        self._tab.load_from_cfg()
        wind_ok = wind_alerts_allowed(self.wiz.enc_type)
        wind_on = wind_ok and self._wind_active()
        self._tab.set_wind_enable_available(wind_ok)
        # Aktiviert wird der Windmesser im Schritt „Wetter“; hier nur Schwellen anzeigen,
        # und zwar nur wenn er aktiv ist.
        self._tab.set_wind_enable_checked(wind_on)
        self._tab.set_wind_activation_visible(False)
        self._tab.setEnabled(True)

    def _wind_active(self) -> bool:
        """Windmesser aktiv? Entscheidung im Wetter-Schritt, sonst bekannter Rotor-Zustand."""
        choice = getattr(self.wiz, "wind_choice", None)
        if choice is not None:
            return bool(choice)
        ctrl = self.wiz.ctrl
        if ctrl is not None and bool(getattr(ctrl, "wind_enabled_known", False)):
            return bool(getattr(ctrl, "wind_enabled", False))
        return False

    def validatePage(self) -> bool:
        self._tab.apply_to_cfg(self.wiz.cfg)
        if not (wind_alerts_allowed(self.wiz.enc_type) and self._wind_active()):
            wa = self.wiz.cfg.setdefault("ui", {}).setdefault(
                "weather_alert_thresholds", {}
            )
            wa["wind_alert_enabled"] = False
        if callable(self.wiz.save_cfg_cb):
            self.wiz.save_cfg_cb(self.wiz.cfg)
        return True


class DonePage(QWizardPage):
    def __init__(self, wiz: "CommissioningWizard") -> None:
        super().__init__()
        self.wiz = wiz
        self.setTitle(t("setup.page_done_title"))
        self.setSubTitle(t("setup.page_done_sub"))
        root = QVBoxLayout(self)
        self._body = QVBoxLayout()
        wrap = QWidget()
        wrap.setLayout(self._body)
        root.addWidget(_scroll(wrap))
        foot = QLabel(t("setup.done_footer"))
        foot.setWordWrap(True)
        foot.setStyleSheet("color: gray;")
        root.addWidget(foot)

    def _clear_body(self) -> None:
        while self._body.count():
            item = self._body.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    @staticmethod
    def _row(form: QFormLayout, label: str, value: object) -> None:
        form.addRow(label, QLabel(str(value if value not in (None, "") else "—")))

    def _section(self, title: str, rows: list[tuple[str, object]]) -> None:
        if not rows:
            return
        g = QGroupBox(title)
        form = QFormLayout(g)
        for lab, val in rows:
            self._row(form, lab, val)
        self._body.addWidget(g)

    def initializePage(self) -> None:
        self._clear_body()
        wiz = self.wiz
        cfg = wiz.cfg if isinstance(wiz.cfg, dict) else {}
        meta = get_active_profile_meta()
        ui = cfg.get("ui") if isinstance(cfg.get("ui"), dict) else {}
        rb = cfg.get("rotor_bus") if isinstance(cfg.get("rotor_bus"), dict) else {}
        hl = cfg.get("hardware_link") if isinstance(cfg.get("hardware_link"), dict) else {}
        chw = cfg.get("controller_hw") if isinstance(cfg.get("controller_hw"), dict) else {}
        pst = cfg.get("pst_server") if isinstance(cfg.get("pst_server"), dict) else {}
        rot = cfg.get("rotctld_server") if isinstance(cfg.get("rotctld_server"), dict) else {}
        web = cfg.get("map_webserver") if isinstance(cfg.get("map_webserver"), dict) else {}

        mode = str(getattr(wiz, "_sess_mode", "") or "")
        mode_txt = {
            "default": t("setup.done_mode_default"),
            "edit": t("setup.done_mode_edit"),
            "new": t("setup.done_mode_new"),
        }.get(mode, mode or "—")

        enc = wiz.enc_type
        enc_txt = t(f"setup.enc_{enc}") if enc in (1, 2, 3, 4) else "—"
        endst = wiz.endstc
        endst_txt = t(f"setup.endst_{endst}") if endst in (0, 1, 2) else "—"
        axis = str(wiz.axis_role or "").upper() or "—"
        axis_name = t("setup.axis_az", id=wiz.axis_dst) if axis == "AZ" else (
            t("setup.axis_el", id=wiz.axis_dst) if axis == "EL" else axis
        )

        self._section(
            t("setup.done_sec_profile"),
            [
                (t("setup.done_profile"), meta.get("name") or "—"),
                (t("setup.done_mode"), mode_txt),
                (
                    t("setup.done_location"),
                    f"{ui.get('location_lat', '—')} / {ui.get('location_lon', '—')}"
                    + (
                        f" ({ui.get('location_locator')})"
                        if ui.get("location_locator")
                        else ""
                    ),
                ),
            ],
        )

        mode_hl = str(hl.get("mode") or "com").lower()
        if mode_hl == "com":
            link_txt = f"COM {hl.get('com_port') or '—'} @ {hl.get('baudrate') or '—'}"
        elif mode_hl in ("tcp", "udp"):
            try:
                from ..axis_link import axis_endpoint_from_cfg, dual_axis_tcp_active

                az_h, az_p = axis_endpoint_from_cfg(cfg, "az")
                if dual_axis_tcp_active(cfg):
                    el_h, el_p = axis_endpoint_from_cfg(cfg, "el")
                    link_txt = (
                        f"{mode_hl.upper()} AZ {az_h or '—'}:{az_p} · "
                        f"EL {el_h or '—'}:{el_p}"
                    )
                else:
                    link_txt = f"{mode_hl.upper()} {az_h or hl.get('tcp_ip') or '—'}:{az_p or hl.get('tcp_port') or '—'}"
            except Exception:
                link_txt = f"{mode_hl.upper()} {hl.get('tcp_ip') or '—'}:{hl.get('tcp_port') or '—'}"
        else:
            link_txt = f"{mode_hl.upper()} {hl.get('tcp_ip') or '—'}:{hl.get('tcp_port') or '—'}"
        self._section(
            t("setup.done_sec_bus"),
            [
                (t("setup.done_rotor_link"), link_txt),
                (t("setup.done_master_id"), rb.get("master_id", "—")),
                (t("setup.done_slave_az"), rb.get("slave_az", "—")),
                (t("setup.done_slave_el"), rb.get("slave_el", "—")),
                (
                    t("setup.done_axes_on"),
                    f"AZ={'✓' if rb.get('enable_az', True) else '—'}  "
                    f"EL={'✓' if rb.get('enable_el', False) else '—'}",
                ),
            ],
        )

        lm = int(chw.get("link_mode", 0) or 0) if chw else 0
        lm_txt = {0: "RS485", 1: "PC-USB", 2: "WEB"}.get(lm, str(lm))
        self._section(
            t("setup.done_sec_controller"),
            [
                (
                    t("setup.done_ctrl_enabled"),
                    t("setup.done_yes") if chw.get("enabled", True) else t("setup.done_no"),
                ),
                (t("setup.done_ctrl_id"), chw.get("cont_id", "—")),
                (t("setup.done_ctrl_link"), lm_txt),
            ],
        )

        self._section(
            t("setup.done_sec_axis"),
            [
                (t("setup.done_axis"), axis_name),
                (t("setup.done_encoder"), enc_txt),
                (t("setup.done_endst"), endst_txt),
            ],
        )

        snap = getattr(wiz, "drive_snapshot", None)
        if isinstance(snap, dict) and snap:
            rt = int(snap.get("rotor_type", 0) or 0)
            rt_txt = {
                1: t("setup.rtype_360"),
                2: t("setup.rtype_90"),
                3: t("setup.rtype_180"),
            }.get(rt, str(rt))
            self._section(
                t("setup.done_sec_drive"),
                [
                    (t("setup.drive_rotor_type"), rt_txt),
                    (t("setup.drive_begin"), f"{snap.get('begin_dg', '—')}°"),
                    (t("setup.drive_max"), f"{snap.get('max_dg', '—')}°"),
                    (t("setup.drive_minpwm"), f"{snap.get('min_pwm', '—')} %"),
                    (t("setup.drive_iwarn"), f"{snap.get('iwarn', '—')} mA"),
                    (t("setup.drive_imax"), f"{snap.get('imax', '—')} mA"),
                    (t("setup.drive_homepwm"), f"{snap.get('home_pwm', '—')} %"),
                    (t("setup.drive_seekpwm"), f"{snap.get('home_seek_pwm', '—')} %"),
                    (t("setup.drive_ramp"), snap.get("ramp", "—")),
                    (t("setup.drive_arrtol"), f"{snap.get('arrtol', '—')}°"),
                ],
            )

        if wiz.wind_choice is not None:
            wind_txt = t("setup.done_yes") if wiz.wind_choice else t("setup.done_no")
        else:
            wind_txt = "—"
        self._section(
            t("setup.done_sec_weather"),
            [(t("setup.done_wind"), wind_txt)],
        )

        names = list(ui.get("antenna_names") or [])
        ant_rows: list[tuple[str, object]] = []
        if names:
            ant_rows.extend(
                (t("setup.done_ant_n", n=i + 1), str(names[i]))
                for i in range(min(3, len(names)))
            )
        try:
            mast_h = float(ui.get("antenna_height_m", 0.0) or 0.0)
        except (TypeError, ValueError):
            mast_h = 0.0
        ant_rows.append(
            (
                t("settings.antenna_height"),
                f"{mast_h:.1f} m" if mast_h > 0 else "—",
            )
        )
        if ant_rows:
            self._section(t("setup.done_sec_antenna"), ant_rows)

        def _onoff(en: object) -> str:
            return t("setup.done_yes") if bool(en) else t("setup.done_no")

        self._section(
            t("setup.done_sec_services"),
            [
                (
                    t("setup.done_pst"),
                    f"{_onoff(pst.get('enabled'))} · "
                    f"{pst.get('listen_host', '—')}:"
                    f"{pst.get('listen_port_az', '—')}/{pst.get('listen_port_el', '—')}",
                ),
                (
                    t("setup.done_rotctld"),
                    f"{_onoff(rot.get('enabled'))} · "
                    f"{rot.get('listen_host', '—')}:{rot.get('listen_port', '—')}",
                ),
                (
                    t("setup.done_web"),
                    f"{_onoff(web.get('enabled'))} · Port {web.get('port', '—')}",
                ),
            ],
        )
        self._body.addStretch(1)


# ---------------------------------------------------------------------------
# Wizard
# ---------------------------------------------------------------------------


class CommissioningWizard(QWizard):
    """Geführte Inbetriebnahme unter Aktionen."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        ctrl: Any = None,
        cfg: dict | None = None,
        save_cfg_cb: Optional[Callable[[dict], None]] = None,
        switch_profile_cb: Optional[Callable[[str], None]] = None,
        open_settings_tab_cb: Optional[Callable[[str], None]] = None,
        apply_services_cb: Optional[Callable[[], None]] = None,
        get_cfg: Optional[Callable[[], dict]] = None,
        controller_remote_proxy: Any = None,
    ) -> None:
        super().__init__(parent)
        self.ctrl = ctrl
        self._cfg = cfg if isinstance(cfg, dict) else {}
        self._get_cfg = get_cfg
        self.save_cfg_cb = save_cfg_cb
        self.switch_profile_cb = switch_profile_cb
        # Nach „Fertig“: aktives Profil komplett neu anwenden (Link, IDs, Server, Controller)
        self.reload_profile_cb: Optional[Callable[[], None]] = None
        self.open_settings_tab_cb = open_settings_tab_cb
        self.apply_services_cb = apply_services_cb
        self.controller_remote_proxy = controller_remote_proxy
        self.axis_role = "az"
        self.axis_dst = 20
        self.enc_type: Optional[int] = None
        self.endstc: Optional[int] = None
        self._pending_cpr: Optional[int] = None
        # Ziel-Bus-ID, solange der Rotor im Setup-Modus (SETSETUP:1) ist; sonst None
        self._setup_dst: Optional[int] = None
        # Windmesser-Entscheidung aus dem Wetter-Schritt (None = unbekannt)
        self.wind_choice: Optional[bool] = None
        # Letzte gesetzte Fahrparameter (Drive-Seite) für Fertig-Übersicht
        self.drive_snapshot: Optional[dict] = None
        # Profil-Session: Temp bei Neu, Snapshot bei Default/Bearbeiten
        self._sess_active = False
        self._sess_mode = ""  # default|edit|new
        self._sess_previous_id = DEFAULT_PROFILE_ID
        self._sess_working_id = ""
        self._sess_snapshot: Optional[dict] = None
        self._sess_temp = False
        self._sess_committed = False
        self._sess_new_name = ""

        self.setWindowTitle(t("setup.title"))
        self.setWindowIcon(get_app_icon())
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        self.setOption(QWizard.WizardOption.NoBackButtonOnStartPage, True)
        self._apply_wizard_button_texts()
        self.setMinimumWidth(780)
        self.resize(780, 720)

        self._id_profile = self.addPage(ProfilePage(self))
        self._id_location = self.addPage(LocationPage(self))
        self._id_conn = self.addPage(ConnectionPage(self))
        self._id_rotor_ids = self.addPage(RotorIdsAssignPage(self))
        self._id_ctrl = self.addPage(ControllerPage(self))
        self._id_axis = self.addPage(AxisPickPage(self))
        self._id_enc = self.addPage(EncoderPage(self))
        self._id_drive = self.addPage(DrivePage(self))
        self._id_endst = self.addPage(EndstopsPage(self))
        self._id_weather = self.addPage(WeatherPage(self))
        self._id_temp = self.addPage(TempPage(self))
        self._id_cal = self.addPage(CalPage(self))
        self._id_zero = self.addPage(ZeroRefPage(self))
        self._id_verify = self.addPage(VerifyPage(self))
        self._id_ant = self.addPage(AntennaPage(self))
        self._id_svc = self.addPage(ServicesPage(self))
        self._id_alerts = self.addPage(AlertsPage(self))
        self._id_done = self.addPage(DonePage(self))

        # map step → page id for nextId
        self._step_page = {
            STEP_ENCODER: self._id_enc,
            STEP_DRIVE: self._id_drive,
            STEP_ENDSTOPS: self._id_endst,
            STEP_WEATHER: self._id_weather,
            STEP_TEMP: self._id_temp,
            STEP_CAL: self._id_cal,
            STEP_ZERO_REF: self._id_zero,
            STEP_VERIFY: self._id_verify,
            STEP_ANTENNA: self._id_ant,
            STEP_SERVICES: self._id_svc,
            STEP_ALERTS: self._id_alerts,
            STEP_DONE: self._id_done,
        }
        self.finished.connect(self._on_finished)

    @property
    def cfg(self) -> dict:
        if callable(self._get_cfg):
            try:
                c = self._get_cfg()
                if isinstance(c, dict):
                    self._cfg = c
            except Exception:
                pass
        return self._cfg

    @cfg.setter
    def cfg(self, value: dict) -> None:
        self._cfg = value if isinstance(value, dict) else {}

    def _cfg_ref(self) -> dict:
        return self.cfg

    def slave_dst(self) -> int:
        return int(self.axis_dst)

    def begin_profile_session(
        self, mode: str, *, profile_id: str = "", new_name: str = ""
    ) -> bool:
        """Profil für den Wizard aktivieren (Temp bei Neu, Snapshot bei Edit/Default)."""
        mode = str(mode or "").strip().lower()
        want_id = str(profile_id or "").strip()
        want_name = str(new_name or "").strip()
        if (
            self._sess_active
            and not self._sess_committed
            and self._sess_mode == mode
            and (
                (mode == "new" and self._sess_temp and self._sess_new_name == want_name)
                or (mode != "new" and self._sess_working_id == want_id)
            )
        ):
            # Gleiche Session (Zurück → Weiter): nichts erneut anlegen.
            self.cfg = self._cfg_ref()
            return True

        # Vorherige unfertige Session verwerfen
        self.rollback_profile_session()

        previous = get_active_profile_id()
        self._sess_previous_id = previous
        self._sess_mode = mode
        self._sess_committed = False
        self._sess_temp = False
        self._sess_snapshot = None
        self._sess_new_name = ""

        if mode == "new":
            # Temporäres Profil: bei Abbruch löschen, bei Fertig behalten.
            new_id = create_profile(want_name or "Profil", clone_active=False)
            # PC-Link vom laufenden Profil übernehmen — sonst reißt DEFAULT
            # (COM1 / Link-Mode 0) TCP+USB Remote ab und die App kann abstürzen.
            try:
                from ..profile_store import load_profile_config, seed_commissioning_runtime_link

                live = self.cfg if isinstance(self.cfg, dict) else {}
                if not live and previous:
                    live = load_profile_config(previous)
                seed_commissioning_runtime_link(new_id, live)
            except Exception:
                pass
            self._sess_working_id = new_id
            self._sess_temp = True
            self._sess_new_name = want_name or "Profil"
            if callable(self.switch_profile_cb):
                self.switch_profile_cb(new_id)
            self.cfg = self._cfg_ref()
            self._sess_active = True
            return True

        pid = DEFAULT_PROFILE_ID if mode == "default" else want_id
        if not pid:
            return False
        if callable(self.switch_profile_cb) and get_active_profile_id() != pid:
            self.switch_profile_cb(pid)
        self.cfg = self._cfg_ref()
        # Snapshot nach Aktivierung — Abbruch stellt genau diesen Stand wieder her.
        try:
            self._sess_snapshot = copy.deepcopy(load_profile_config(pid))
        except Exception:
            self._sess_snapshot = copy.deepcopy(self.cfg)
        self._sess_working_id = pid
        self._sess_active = True
        return True

    def commit_profile_session(self) -> None:
        """Wizard erfolgreich beendet: Temp-Profil behalten, Snapshot verwerfen."""
        if not self._sess_active:
            return
        # Dual-IP: AZ-Primärlink wiederherstellen, EL-Client anlegen (vor Reload).
        try:
            from ..axis_link import ensure_setup_axis_endpoints, sync_hardware_link_from_az

            ensure_setup_axis_endpoints(self.cfg)
            sync_hardware_link_from_az(self.cfg)
        except Exception:
            pass
        try:
            if callable(self.save_cfg_cb):
                self.save_cfg_cb(self.cfg)
        except Exception:
            pass
        self._sess_temp = False
        self._sess_snapshot = None
        self._sess_committed = True
        self._sess_active = False

    def rollback_profile_session(self) -> None:
        """Abbruch: Temp löschen; Edit/Default auf Snapshot zurück."""
        if not self._sess_active or self._sess_committed:
            self._reset_session_flags()
            return
        working = str(self._sess_working_id or "")
        previous = str(self._sess_previous_id or DEFAULT_PROFILE_ID)
        was_temp = bool(self._sess_temp)
        snapshot = self._sess_snapshot
        self._reset_session_flags()

        if was_temp and working:
            try:
                delete_profile(working)
            except Exception:
                pass
            if callable(self.switch_profile_cb) and previous:
                try:
                    if get_active_profile_id() != previous:
                        self.switch_profile_cb(previous)
                except Exception:
                    pass
            self.cfg = self._cfg_ref()
            return

        # Bestehendes Profil: Änderungen verwerfen
        if working and isinstance(snapshot, dict):
            try:
                save_profile_config(working, snapshot)
            except Exception:
                pass
            # Config im Speicher zurücksetzen (auch wenn gleiches Profil aktiv bleibt)
            try:
                restored = copy.deepcopy(snapshot)
                self.cfg.clear()
                self.cfg.update(restored)
                if callable(self.save_cfg_cb):
                    # nicht erneut speichern — Datei ist schon Snapshot
                    pass
                hw = getattr(self.ctrl, "hw", None) if self.ctrl else None
                if hw is not None and hasattr(hw, "update_cfg"):
                    hw.update_cfg(self.cfg.get("hardware_link") or {})
                try:
                    from ..axis_link import (
                        dual_axis_tcp_active,
                        ensure_setup_axis_endpoints,
                        link_cfg_for_axis,
                    )
                    from ..hardware_client import HardwareClient

                    ensure_setup_axis_endpoints(self.cfg)
                    if dual_axis_tcp_active(self.cfg) and self.ctrl is not None:
                        el_cfg = link_cfg_for_axis(self.cfg, "el")
                        el_hw = getattr(self.ctrl, "el_hw", None)
                        if el_hw is None:
                            log = getattr(self.ctrl, "log", None)
                            el_hw = HardwareClient(el_cfg, log)
                            el_hw.start()
                        else:
                            el_hw.update_cfg(el_cfg)
                        self.ctrl.set_el_link(el_hw)
                    elif self.ctrl is not None:
                        el_hw = getattr(self.ctrl, "el_hw", None)
                        if el_hw is not None:
                            try:
                                el_hw.stop()
                            except Exception:
                                pass
                        self.ctrl.set_el_link(None)
                except Exception:
                    pass
            except Exception:
                pass
        if callable(self.switch_profile_cb) and previous and previous != working:
            try:
                self.switch_profile_cb(previous)
            except Exception:
                pass
        elif callable(self.switch_profile_cb) and working:
            # gleiches Profil: Switch-CB lädt ggf. nicht neu — Datei+RAM schon restored
            pass
        self.cfg = self._cfg_ref()
        if working and isinstance(snapshot, dict) and get_active_profile_id() == working:
            # Nach get_cfg ggf. wieder Dateistand erzwingen
            try:
                self.cfg.clear()
                self.cfg.update(copy.deepcopy(load_profile_config(working)))
            except Exception:
                pass

    def _reset_session_flags(self) -> None:
        self._sess_active = False
        self._sess_mode = ""
        self._sess_working_id = ""
        self._sess_snapshot = None
        self._sess_temp = False
        self._sess_committed = False
        self._sess_new_name = ""

    def force_setup_off(self) -> None:
        """Setup-Modus am Rotor sicher beenden (JOG-Stopp + SETSETUP:0, Achsgrenzen wieder aktiv)."""
        dst = self._setup_dst
        self._setup_dst = None
        if dst is None or self.ctrl is None:
            return
        for cmd, par in (("JOG", "0"), ("SETSETUP", "0")):
            try:
                self.ctrl.send_ui_command(
                    int(dst),
                    cmd,
                    par,
                    expect_prefix=f"ACK_{cmd}",
                    timeout_s=1.5,
                    priority=0,
                    on_done=None,
                )
            except Exception:
                pass

    def _on_finished(self, result: int) -> None:
        self.force_setup_off()
        # LAN-Busy-Dialoge vor Rollback/Teardown schließen
        try:
            conn = self.page(self._id_conn)
            nt = getattr(conn, "_network_tab", None)
            if nt is not None and hasattr(nt, "stop_background_activity"):
                nt.stop_background_activity()
        except Exception:
            pass
        if int(result) == int(QDialog.DialogCode.Accepted):
            self.commit_profile_session()
            cb = self.reload_profile_cb
            if callable(cb):
                try:
                    cb()
                except Exception:
                    pass
        else:
            self.rollback_profile_session()

    def _apply_wizard_button_texts(self) -> None:
        """Weiter/Zurück/Fertig/Abbrechen in der aktiven UI-Sprache (nicht Qt-Englisch)."""
        self.setButtonText(QWizard.WizardButton.BackButton, t("setup.btn_back"))
        self.setButtonText(QWizard.WizardButton.NextButton, t("setup.btn_next"))
        self.setButtonText(QWizard.WizardButton.FinishButton, t("setup.btn_finish"))
        self.setButtonText(QWizard.WizardButton.CancelButton, t("setup.btn_cancel"))

    def restart(self) -> None:
        """Wizard von vorne starten (Profilseite)."""
        self.force_setup_off()
        self.rollback_profile_session()
        self.axis_role = "az"
        self.axis_dst = 20
        self.enc_type = None
        self.endstc = None
        self._pending_cpr = None
        self.wind_choice = None
        self.drive_snapshot = None
        self.setWindowTitle(t("setup.title"))
        self._apply_wizard_button_texts()
        QWizard.restart(self)

    def nextId(self) -> int:
        cur = self.currentId()
        order = [
            self._id_profile,
            self._id_location,
            self._id_conn,
            self._id_rotor_ids,
            self._id_ctrl,
            self._id_axis,
            self._id_enc,
            self._id_drive,
            self._id_endst,
            self._id_weather,
            self._id_temp,
            self._id_cal,
            self._id_zero,
            self._id_verify,
            self._id_ant,
            self._id_svc,
            self._id_alerts,
            self._id_done,
        ]
        try:
            idx = order.index(cur)
        except ValueError:
            return -1
        for nid in order[idx + 1 :]:
            if nid == self._id_weather and not weather_allowed(self.enc_type):
                continue
            if nid == self._id_ant:
                rb = self.cfg.get("rotor_bus") or {}
                if not bool(rb.get("enable_az", True)):
                    continue
            return nid
        return -1
