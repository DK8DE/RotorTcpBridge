"""Fenster „Rotorübersicht“: globale Multi-Standort-Karte (nur Anzeige)."""

from __future__ import annotations

import json
from typing import Dict, List, Optional

from PySide6.QtCore import QTimer, QUrl, Qt, Signal, Slot
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenuBar,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtWebEngineWidgets import QWebEngineView

from ..app_icon import get_app_icon
from ..geo_utils import maidenhead_to_lat_lon
from ..i18n import t
from ..rotor_overview_config import (
    deep_copy_config,
    default_site,
    load_overview_config,
    next_distinct_color,
    normalize_site,
    save_overview_config,
    site_from_profile,
)
from ..rotor_overview_monitor import RotorOverviewMonitor, SiteLiveState
from ..rotor_overview_service import RotorOverviewService, fill_from_stroke
from .map_widgets import MapWebPage
from .rotor_overview_html import build_overview_html
from .ui_utils import px_to_dip


def _fill_from_stroke(stroke: str) -> str:
    return fill_from_stroke(stroke)


class RotorOverviewSiteDialog(QDialog):
    """Standort anlegen/bearbeiten inkl. Profil-Import und Antennen."""

    def __init__(self, site: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle(t("overview.site_edit_title"))
        self.setWindowIcon(get_app_icon())
        self._site = normalize_site(site)
        root = QVBoxLayout(self)

        # Profil-Import
        row_imp = QHBoxLayout()
        self.cb_profile = QComboBox()
        self.cb_profile.addItem(t("overview.profile_none"), "")
        try:
            from ..profile_store import get_active_profile_id, list_profiles

            active = get_active_profile_id()
            for p in list_profiles():
                pid = str(p.get("id") or "")
                name = str(p.get("name") or pid)
                mark = t("overview.profile_active_suffix") if pid == active else ""
                self.cb_profile.addItem(f"{name}{mark}", pid)
        except Exception:
            pass
        self.btn_import = QPushButton(t("overview.btn_import_profile"))
        self.btn_import.clicked.connect(self._on_import_profile)
        self.btn_import.setEnabled(self.cb_profile.count() > 1)
        row_imp.addWidget(QLabel(t("overview.import_profile")))
        row_imp.addWidget(self.cb_profile, 1)
        row_imp.addWidget(self.btn_import)
        root.addLayout(row_imp)

        form = QFormLayout()
        self.ed_name = QLineEdit(self._site["name"])
        form.addRow(t("overview.field_name"), self.ed_name)
        self.chk_enabled = QCheckBox(t("overview.field_enabled"))
        self.chk_enabled.setChecked(bool(self._site["enabled"]))
        form.addRow(self.chk_enabled)

        self.sp_lat = QDoubleSpinBox()
        self.sp_lat.setRange(-90.0, 90.0)
        self.sp_lat.setDecimals(6)
        self.sp_lat.setValue(float(self._site["lat"]))
        self.sp_lon = QDoubleSpinBox()
        self.sp_lon.setRange(-180.0, 180.0)
        self.sp_lon.setDecimals(6)
        self.sp_lon.setValue(float(self._site["lon"]))
        self.ed_locator = QLineEdit(str(self._site.get("locator") or ""))
        self.btn_loc = QPushButton(t("overview.btn_locator_apply"))
        self.btn_loc.clicked.connect(self._on_locator_apply)
        loc_row = QHBoxLayout()
        loc_row.addWidget(self.ed_locator, 1)
        loc_row.addWidget(self.btn_loc)
        form.addRow(t("overview.field_lat"), self.sp_lat)
        form.addRow(t("overview.field_lon"), self.sp_lon)
        form.addRow(t("overview.field_locator"), loc_row)

        self.cb_mode = QComboBox()
        self.cb_mode.addItem("TCP", "tcp")
        self.cb_mode.addItem("UDP", "udp")
        idx = self.cb_mode.findData(str(self._site.get("mode") or "tcp"))
        if idx >= 0:
            self.cb_mode.setCurrentIndex(idx)
        self.ed_host = QLineEdit(str(self._site.get("host") or ""))
        self.sp_port = QSpinBox()
        self.sp_port.setRange(1, 65535)
        self.sp_port.setValue(int(self._site.get("port") or 8886))
        self.sp_udp_bind = QSpinBox()
        self.sp_udp_bind.setRange(0, 65535)
        self.sp_udp_bind.setValue(int(self._site.get("udp_bind_port") or 0))
        self.sp_master = QSpinBox()
        self.sp_master.setRange(0, 254)
        self.sp_master.setValue(int(self._site.get("master_id") or 0))
        self.sp_slave = QSpinBox()
        self.sp_slave.setRange(1, 254)
        self.sp_slave.setValue(int(self._site.get("slave_az") or 20))
        self.sp_poll_pos = QSpinBox()
        self.sp_poll_pos.setRange(200, 60000)
        self.sp_poll_pos.setSuffix(" ms")
        self.sp_poll_pos.setValue(int(self._site.get("poll_pos_ms") or 1000))
        self.sp_poll_ref = QSpinBox()
        self.sp_poll_ref.setRange(1000, 120000)
        self.sp_poll_ref.setSuffix(" ms")
        self.sp_poll_ref.setValue(int(self._site.get("poll_ref_ms") or 5000))
        self.sp_smooth = QDoubleSpinBox()
        self.sp_smooth.setRange(0.05, 1.0)
        self.sp_smooth.setSingleStep(0.05)
        self.sp_smooth.setValue(float(self._site.get("smooth_alpha") or 0.35))
        form.addRow(t("overview.field_mode"), self.cb_mode)
        form.addRow(t("overview.field_host"), self.ed_host)
        form.addRow(t("overview.field_port"), self.sp_port)
        form.addRow(t("overview.field_udp_bind"), self.sp_udp_bind)
        form.addRow(t("overview.field_master"), self.sp_master)
        form.addRow(t("overview.field_slave_az"), self.sp_slave)
        form.addRow(t("overview.field_poll_pos"), self.sp_poll_pos)
        form.addRow(t("overview.field_poll_ref"), self.sp_poll_ref)
        form.addRow(t("overview.field_smooth"), self.sp_smooth)
        root.addLayout(form)

        gb = QGroupBox(t("overview.antennas_group"))
        vl_a = QVBoxLayout(gb)
        self._ant_widgets = []
        for i, ant in enumerate(self._site.get("antennas") or []):
            row = QHBoxLayout()
            chk = QCheckBox(t("overview.antenna_n", n=i + 1))
            chk.setChecked(bool(ant.get("enabled")))
            ed_n = QLineEdit(str(ant.get("name") or ""))
            chk_dip = QCheckBox(t("overview.ant_dipole"))
            chk_dip.setChecked(bool(ant.get("dipole")))
            chk_dip.setToolTip(t("overview.ant_dipole_tooltip"))
            sp_off = QDoubleSpinBox()
            sp_off.setRange(-3600, 3600)
            sp_off.setDecimals(1)
            sp_off.setValue(float(ant.get("offset_deg") or 0))
            sp_op = QDoubleSpinBox()
            sp_op.setRange(1, 360)
            sp_op.setDecimals(1)
            sp_op.setValue(float(ant.get("opening_deg") or 30))
            sp_rg = QDoubleSpinBox()
            sp_rg.setRange(1, 4000)
            sp_rg.setDecimals(1)
            sp_rg.setValue(float(ant.get("range_km") or 100))
            btn_col = QPushButton()
            color = str(ant.get("color") or "#5BA3D0")
            btn_col.setStyleSheet(f"background:{color}; min-width:36px;")
            btn_col.setProperty("color_hex", color)
            btn_col.clicked.connect(lambda _=False, b=btn_col: self._pick_color(b))
            row.addWidget(chk)
            row.addWidget(ed_n, 1)
            row.addWidget(chk_dip)
            row.addWidget(QLabel(t("overview.ant_offset")))
            row.addWidget(sp_off)
            row.addWidget(QLabel(t("overview.ant_opening")))
            row.addWidget(sp_op)
            row.addWidget(QLabel(t("overview.ant_range")))
            row.addWidget(sp_rg)
            row.addWidget(btn_col)
            vl_a.addLayout(row)
            self._ant_widgets.append((chk, ed_n, chk_dip, sp_off, sp_op, sp_rg, btn_col))
        root.addWidget(gb)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self.resize(px_to_dip(self, 720), px_to_dip(self, 560))

    def _pick_color(self, btn: QPushButton) -> None:
        cur = QColor(str(btn.property("color_hex") or "#5BA3D0"))
        col = QColorDialog.getColor(cur, self, t("overview.pick_color"))
        if col.isValid():
            hx = col.name()
            btn.setProperty("color_hex", hx)
            btn.setStyleSheet(f"background:{hx}; min-width:36px;")

    def _on_locator_apply(self) -> None:
        loc = self.ed_locator.text().strip()
        if not loc:
            return
        try:
            res = maidenhead_to_lat_lon(loc)
            if not res:
                raise ValueError("invalid")
            lat, lon = res
            self.sp_lat.setValue(float(lat))
            self.sp_lon.setValue(float(lon))
        except Exception:
            QMessageBox.warning(self, t("overview.title"), t("overview.locator_invalid"))

    def _on_import_profile(self) -> None:
        pid = str(self.cb_profile.currentData() or "").strip()
        if not pid:
            return
        try:
            from ..profile_store import get_profile_meta, load_profile_config

            meta = get_profile_meta(pid) or {}
            cfg = load_profile_config(pid)
            site = site_from_profile(pid, str(meta.get("name") or pid), cfg)
        except Exception as exc:
            QMessageBox.warning(
                self, t("overview.title"), t("overview.import_fail", err=str(exc))
            )
            return
        self._site = site
        self.ed_name.setText(site["name"])
        self.chk_enabled.setChecked(bool(site["enabled"]))
        self.sp_lat.setValue(float(site["lat"]))
        self.sp_lon.setValue(float(site["lon"]))
        self.ed_locator.setText(str(site.get("locator") or ""))
        idx = self.cb_mode.findData(str(site.get("mode") or "tcp"))
        if idx >= 0:
            self.cb_mode.setCurrentIndex(idx)
        self.ed_host.setText(str(site.get("host") or ""))
        self.sp_port.setValue(int(site.get("port") or 8886))
        self.sp_udp_bind.setValue(int(site.get("udp_bind_port") or 0))
        self.sp_master.setValue(int(site.get("master_id") or 0))
        self.sp_slave.setValue(int(site.get("slave_az") or 20))
        for i, (chk, ed_n, chk_dip, sp_off, sp_op, sp_rg, btn_col) in enumerate(
            self._ant_widgets
        ):
            ant = site["antennas"][i]
            chk.setChecked(bool(ant.get("enabled")))
            ed_n.setText(str(ant.get("name") or ""))
            chk_dip.setChecked(bool(ant.get("dipole")))
            sp_off.setValue(float(ant.get("offset_deg") or 0))
            sp_op.setValue(float(ant.get("opening_deg") or 30))
            sp_rg.setValue(float(ant.get("range_km") or 100))
            color = str(ant.get("color") or "#5BA3D0")
            btn_col.setProperty("color_hex", color)
            btn_col.setStyleSheet(f"background:{color}; min-width:36px;")

    def result_site(self) -> dict:
        ants = []
        for chk, ed_n, chk_dip, sp_off, sp_op, sp_rg, btn_col in self._ant_widgets:
            ants.append(
                {
                    "enabled": bool(chk.isChecked()),
                    "name": ed_n.text().strip() or "Antenne",
                    "dipole": bool(chk_dip.isChecked()),
                    "offset_deg": float(sp_off.value()),
                    "opening_deg": float(sp_op.value()),
                    "range_km": float(sp_rg.value()),
                    "color": str(btn_col.property("color_hex") or "#5BA3D0"),
                }
            )
        out = dict(self._site)
        out.update(
            {
                "name": self.ed_name.text().strip() or "Standort",
                "enabled": bool(self.chk_enabled.isChecked()),
                "lat": float(self.sp_lat.value()),
                "lon": float(self.sp_lon.value()),
                "locator": self.ed_locator.text().strip(),
                "mode": str(self.cb_mode.currentData() or "tcp"),
                "host": self.ed_host.text().strip(),
                "port": int(self.sp_port.value()),
                "udp_bind_port": int(self.sp_udp_bind.value()),
                "master_id": int(self.sp_master.value()),
                "slave_az": int(self.sp_slave.value()),
                "poll_pos_ms": int(self.sp_poll_pos.value()),
                "poll_ref_ms": int(self.sp_poll_ref.value()),
                "smooth_alpha": float(self.sp_smooth.value()),
                "antennas": ants,
            }
        )
        return normalize_site(out)


class RotorOverviewSettingsDialog(QDialog):
    """Liste der Standorte verwalten."""

    def __init__(self, cfg: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle(t("overview.settings_title"))
        self.setWindowIcon(get_app_icon())
        self._cfg = deep_copy_config(cfg)
        root = QVBoxLayout(self)
        self.lst = QListWidget()
        root.addWidget(self.lst)
        row = QHBoxLayout()
        self.btn_add = QPushButton(t("overview.btn_add"))
        self.btn_edit = QPushButton(t("overview.btn_edit"))
        self.btn_del = QPushButton(t("overview.btn_delete"))
        self.btn_add.clicked.connect(self._on_add)
        self.btn_edit.clicked.connect(self._on_edit)
        self.btn_del.clicked.connect(self._on_delete)
        row.addWidget(self.btn_add)
        row.addWidget(self.btn_edit)
        row.addWidget(self.btn_del)
        row.addStretch(1)
        root.addLayout(row)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self._reload_list()
        self.resize(px_to_dip(self, 480), px_to_dip(self, 360))

    def _reload_list(self) -> None:
        self.lst.clear()
        for s in self._cfg.get("sites") or []:
            en = t("overview.enabled_yes") if s.get("enabled") else t("overview.enabled_no")
            text = f"{s.get('name')}  [{s.get('host')}:{s.get('port')}]  ({en})"
            it = QListWidgetItem(text)
            it.setData(Qt.ItemDataRole.UserRole, str(s.get("id")))
            self.lst.addItem(it)

    def _selected_index(self) -> int:
        row = self.lst.currentRow()
        return row

    def _on_add(self) -> None:
        used = []
        for s in self._cfg.get("sites") or []:
            for a in s.get("antennas") or []:
                used.append(str(a.get("color") or ""))
        site = default_site(name=t("overview.new_site"))
        if site["antennas"]:
            site["antennas"][0]["color"] = next_distinct_color(used)
        dlg = RotorOverviewSiteDialog(site, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        self._cfg.setdefault("sites", []).append(dlg.result_site())
        self._reload_list()

    def _on_edit(self) -> None:
        i = self._selected_index()
        sites = self._cfg.get("sites") or []
        if i < 0 or i >= len(sites):
            return
        dlg = RotorOverviewSiteDialog(sites[i], self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        sites[i] = dlg.result_site()
        self._reload_list()

    def _on_delete(self) -> None:
        i = self._selected_index()
        sites = self._cfg.get("sites") or []
        if i < 0 or i >= len(sites):
            return
        del sites[i]
        self._reload_list()

    def result_cfg(self) -> dict:
        return deep_copy_config(self._cfg)


class RotorOverviewWindow(QDialog):
    """Globale Übersichtskarte — keine Rotor-Steuerung."""

    site_state_changed = Signal(object)

    def __init__(self, parent=None, service: RotorOverviewService | None = None):
        super().__init__(parent)
        self.setWindowTitle(t("overview.title"))
        self.setWindowIcon(get_app_icon())
        # Wie MapWindow: Fenster-Flags setzen, nicht Dialog-Modalität erzwingen
        self.setWindowFlag(Qt.WindowType.WindowMinimizeButtonHint, True)
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self._service = service
        if self._service is not None:
            self._cfg = self._service.get_config()
            self._live = self._service.get_live_snapshot()
            self._monitor = None
            self._service.add_cfg_listener(self._on_service_cfg_changed)
        else:
            self._cfg = load_overview_config()
            self._live = {}
            self._monitor = RotorOverviewMonitor(on_update=self._emit_state)
        self._page_ready = False
        self._cfg_rev_seen = self._service.revision if self._service is not None else 0

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QWidget(self)
        header.setObjectName("overviewHeader")
        header_l = QHBoxLayout(header)
        header_l.setContentsMargins(4, 0, 8, 0)
        header_l.setSpacing(8)

        mb = QMenuBar(header)
        mb.setNativeMenuBar(False)
        mb.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Preferred)
        self._menu_settings = mb.addMenu(t("overview.menu_settings"))
        self._act_sites = self._menu_settings.addAction(t("overview.menu_sites"))
        self._act_sites.triggered.connect(self._open_settings)
        self._act_reload = self._menu_settings.addAction(t("overview.menu_reload"))
        self._act_reload.triggered.connect(self._reload_monitor)

        self._chk_active_ant = QCheckBox(t("overview.chk_active_antenna"))
        self._chk_active_ant.setToolTip(t("overview.chk_active_antenna_tooltip"))
        self._chk_active_ant.setChecked(
            bool(self._cfg.get("show_active_antenna_only", False))
        )
        self._chk_active_ant.toggled.connect(self._on_active_antenna_toggled)

        self._status = QLabel("")
        self._status.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self._status.setSizePolicy(
            QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred
        )

        # Kompakter Header: Menü links, Status rechts in einer Zeile
        hdr_h = max(22, px_to_dip(self, 24))
        header.setFixedHeight(hdr_h)
        for w in (mb, self._chk_active_ant, self._status):
            f = w.font()
            f.setPointSize(max(8, f.pointSize() - 1))
            w.setFont(f)
        mb.setStyleSheet(
            "QMenuBar { padding: 0px; margin: 0px; background: transparent; }"
            "QMenuBar::item { padding: 2px 6px; margin: 0px; }"
        )
        self._status.setStyleSheet("QLabel { padding: 0px; margin: 0px; }")
        self._chk_active_ant.setStyleSheet(
            "QCheckBox { padding: 0px 4px; margin: 0px; }"
        )

        header_l.addWidget(mb, 0)
        header_l.addWidget(self._chk_active_ant, 0)
        header_l.addStretch(1)
        header_l.addWidget(self._status, 0)
        root.addWidget(header)

        self._view = QWebEngineView(self)
        self._page = MapWebPage(
            on_click_cb=lambda *_a, **_k: None,
            parent=self._view,
        )
        self._view.setPage(self._page)
        root.addWidget(self._view, 1)

        self.site_state_changed.connect(self._on_site_state)
        self._ui_timer = QTimer(self)
        self._ui_timer.setInterval(500)
        self._ui_timer.timeout.connect(self._push_sites_js)

        self.resize(px_to_dip(self, 1000), px_to_dip(self, 700))
        self._load_map()
        self._reload_monitor()

    def _emit_state(self, st: SiteLiveState) -> None:
        self.site_state_changed.emit(st)

    @Slot(object)
    def _on_site_state(self, st: object) -> None:
        if isinstance(st, SiteLiveState):
            self._live[st.site_id] = st

    def _on_service_cfg_changed(self) -> None:
        if self._service is None:
            return
        self._cfg = self._service.get_config()
        self._cfg_rev_seen = self._service.revision
        self._sync_active_antenna_chk()
        self._push_sites_js()

    def _sync_active_antenna_chk(self) -> None:
        wanted = bool(self._cfg.get("show_active_antenna_only", False))
        if self._chk_active_ant.isChecked() == wanted:
            return
        self._chk_active_ant.blockSignals(True)
        self._chk_active_ant.setChecked(wanted)
        self._chk_active_ant.blockSignals(False)

    def _on_active_antenna_toggled(self, on: bool) -> None:
        if self._service is not None:
            self._service.set_show_active_antenna_only(bool(on))
            self._cfg = self._service.get_config()
            self._cfg_rev_seen = self._service.revision
        else:
            self._cfg["show_active_antenna_only"] = bool(on)
            save_overview_config(self._cfg)
        self._push_sites_js()

    def retranslate(self) -> None:
        self.setWindowTitle(t("overview.title"))
        self._menu_settings.setTitle(t("overview.menu_settings"))
        self._act_sites.setText(t("overview.menu_sites"))
        self._act_reload.setText(t("overview.menu_reload"))
        self._chk_active_ant.setText(t("overview.chk_active_antenna"))
        self._chk_active_ant.setToolTip(t("overview.chk_active_antenna_tooltip"))
        self._update_status()

    def _open_settings(self) -> None:
        dlg = RotorOverviewSettingsDialog(self._cfg, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        self._cfg = dlg.result_cfg()
        if self._service is not None:
            self._service.replace_config(self._cfg, persist=True)
            self._cfg = self._service.get_config()
        else:
            save_overview_config(self._cfg)
            self._reload_monitor()
        self._push_sites_js()

    def _reload_monitor(self) -> None:
        if self._service is not None:
            self._service.restart_monitor()
        elif self._monitor is not None:
            self._monitor.start(list(self._cfg.get("sites") or []))
        self._update_status()

    def _update_status(self) -> None:
        if self._service is not None:
            n, online = self._service.status_counts()
        else:
            n = len([s for s in (self._cfg.get("sites") or []) if s.get("enabled")])
            online = sum(1 for st in self._live.values() if st.online)
        self._status.setText(t("overview.status_line", sites=n, online=online))

    def _render_sites(self) -> List[dict]:
        if self._service is not None:
            return self._service.render_sites()
        # Fallback ohne Service (Tests / Standalone)
        from ..angle_utils import antenna_bearing_from_rotor_and_offset, fmt_deg, wrap_deg
        from ..geo_utils import beam_polygon_points

        show_active = bool(self._cfg.get("show_active_antenna_only", False))

        def _selected_ants(site: dict, aselect: Optional[int]) -> List[dict]:
            ants = list(site.get("antennas") or [])
            if not show_active:
                return [a for a in ants if a.get("enabled")]
            slot = int(aselect) if aselect in (1, 2, 3) else None
            if slot is not None and 1 <= slot <= len(ants):
                return [ants[slot - 1]]
            for a in ants:
                if a.get("enabled"):
                    return [a]
            return [ants[0]] if ants else []

        out: List[dict] = []
        for site in self._cfg.get("sites") or []:
            if not site.get("enabled"):
                continue
            sid = str(site.get("id") or "")
            st = self._live.get(sid)
            az = None
            aselect = None
            if st is not None:
                az = st.az_smooth_deg if st.az_smooth_deg is not None else st.az_deg
                aselect = st.aselect
            online = bool(st.online) if st else False
            ref = st.referenced if st else None
            az_text = fmt_deg(float(az)).rstrip("°") + "°" if az is not None else "—"
            if ref is True:
                ref_text = t("overview.ref_yes")
            elif ref is False:
                ref_text = t("overview.ref_no")
            else:
                ref_text = t("overview.ref_unknown")
            selected = _selected_ants(site, aselect)
            color = "#2e7d32"
            if selected:
                color = str(selected[0].get("color") or color)
            beams = []
            if az is not None:
                lat = float(site["lat"])
                lon = float(site["lon"])
                for ant in selected:
                    bearing = antenna_bearing_from_rotor_and_offset(
                        float(az), float(ant.get("offset_deg") or 0.0)
                    )
                    stroke = str(ant.get("color") or "#5BA3D0")
                    opening = float(ant.get("opening_deg") or 30.0)
                    range_km = float(ant.get("range_km") or 100.0)
                    name = ant.get("name") or ""
                    bearings = [bearing]
                    if ant.get("dipole"):
                        bearings.append(wrap_deg(bearing + 180.0))
                    for brg in bearings:
                        poly = beam_polygon_points(lat, lon, brg, opening, range_km)
                        beams.append(
                            {
                                "polygon": [[p[0], p[1]] for p in poly],
                                "stroke": stroke,
                                "fill": _fill_from_stroke(stroke),
                                "name": name,
                            }
                        )
            out.append(
                {
                    "id": sid,
                    "name": site.get("name") or "",
                    "lat": float(site["lat"]),
                    "lon": float(site["lon"]),
                    "online": online,
                    "az_text": az_text,
                    "ref_text": ref_text,
                    "marker_color": color,
                    "aselect": aselect,
                    "beams": beams,
                }
            )
        return out

    def _ui_dark_mode(self) -> bool:
        try:
            from ..app_config import load_config

            ui = (load_config() or {}).get("ui") or {}
            return bool(ui.get("force_dark_mode", True))
        except Exception:
            return True

    def _load_map(self) -> None:
        dark = self._ui_dark_mode()
        self._map_dark_mode = dark
        params = {
            "center_lat": float(self._cfg.get("map_center_lat", 50.0)),
            "center_lon": float(self._cfg.get("map_center_lon", 10.0)),
            "zoom": int(self._cfg.get("map_zoom", 5)),
            "dark_mode": dark,
            "sites": self._render_sites(),
        }
        html = build_overview_html(params)
        self._page_ready = False
        # Online-Tiles + Leaflet inline → about:blank (wie MapWindow online)
        self._view.setHtml(html, QUrl("about:blank"))
        QTimer.singleShot(400, self._mark_page_ready)

    def _apply_dark_mode_js(self) -> None:
        dark = self._ui_dark_mode()
        if getattr(self, "_map_dark_mode", None) == dark and self._page_ready:
            return
        self._map_dark_mode = dark
        if not self._page_ready:
            return
        js = (
            "if (typeof window.setOverviewDarkMode === 'function') "
            f"window.setOverviewDarkMode({str(bool(dark)).lower()});"
        )
        try:
            self._view.page().runJavaScript(js)
        except Exception:
            pass

    def _mark_page_ready(self) -> None:
        self._page_ready = True
        self._apply_dark_mode_js()
        self._push_sites_js()

    def _push_sites_js(self) -> None:
        if self._service is not None:
            self._live = self._service.get_live_snapshot()
            if self._service.revision != self._cfg_rev_seen:
                self._cfg = self._service.get_config()
                self._cfg_rev_seen = self._service.revision
                self._sync_active_antenna_chk()
        self._update_status()
        if not self._page_ready:
            return
        sites = self._render_sites()
        js = (
            "if (typeof window.updateOverviewSites === 'function') "
            f"window.updateOverviewSites({json.dumps(sites)});"
        )
        try:
            self._view.page().runJavaScript(js)
        except Exception:
            pass

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._ui_timer.isActive():
            self._ui_timer.start()
        if self._service is not None:
            self._service.acquire("window")
            self._cfg = self._service.get_config()
            self._sync_active_antenna_chk()
        else:
            self._reload_monitor()
        self._apply_dark_mode_js()

    def hideEvent(self, event) -> None:
        self._ui_timer.stop()
        if self._service is not None:
            self._service.release("window")
        elif self._monitor is not None:
            self._monitor.stop()
        # Kartenausschnitt speichern
        try:
            self._view.page().runJavaScript(
                "window.getOverviewView ? window.getOverviewView() : null;",
                self._save_view_cb,
            )
        except Exception:
            pass
        super().hideEvent(event)

    def _save_view_cb(self, result) -> None:
        if not isinstance(result, dict):
            return
        try:
            lat = float(result.get("lat", self._cfg.get("map_center_lat")))
            lon = float(result.get("lon", self._cfg.get("map_center_lon")))
            zoom = int(result.get("zoom", self._cfg.get("map_zoom")))
            if self._service is not None:
                self._service.save_view(lat, lon, zoom)
                self._cfg = self._service.get_config()
            else:
                self._cfg["map_center_lat"] = lat
                self._cfg["map_center_lon"] = lon
                self._cfg["map_zoom"] = zoom
                save_overview_config(self._cfg)
        except Exception:
            pass

    def closeEvent(self, event) -> None:
        self._ui_timer.stop()
        if self._service is not None:
            try:
                self._service.remove_cfg_listener(self._on_service_cfg_changed)
            except Exception:
                pass
            self._service.release("window")
            save_overview_config(self._service.get_config())
        else:
            if self._monitor is not None:
                self._monitor.stop()
            save_overview_config(self._cfg)
        super().closeEvent(event)
