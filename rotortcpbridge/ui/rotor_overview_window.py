"""Fenster „Rotorübersicht“: globale Multi-Standort-Karte (nur Anzeige)."""

from __future__ import annotations

import json
from typing import Dict, List, Optional

from PySide6.QtCore import QTimer, QUrl, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMenuBar,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtWebEngineWidgets import QWebEngineView

from ..app_icon import get_app_icon
from ..i18n import t
from ..rotor_overview_config import (
    DEFAULT_APP_PORT,
    load_overview_config,
    save_overview_config,
)
from ..rotor_overview_monitor import RotorOverviewMonitor, SiteLiveState
from ..rotor_overview_service import RotorOverviewService, fill_from_stroke
from .map_widgets import MapWebPage
from .rotor_overview_dialogs import RotorOverviewSettingsDialog
from .rotor_overview_html import build_overview_html
from .ui_utils import px_to_dip


def _fill_from_stroke(stroke: str) -> str:
    return fill_from_stroke(stroke)


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
        port = DEFAULT_APP_PORT
        try:
            parent = self.parent()
            cfg = getattr(parent, "cfg", None) if parent is not None else None
            if isinstance(cfg, dict):
                port = int((cfg.get("map_webserver") or {}).get("listen_port") or port)
        except Exception:
            port = DEFAULT_APP_PORT
        dlg = RotorOverviewSettingsDialog(
            self._cfg, self, default_port=port, live=self._live
        )
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
        from ..rotor_overview_config import merge_site_antennas

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
            overlay = dict(site)
            if st is not None:
                overlay["antennas"] = merge_site_antennas(
                    site.get("antennas"), getattr(st, "antennas", None)
                )
                if st.lat is not None:
                    overlay["lat"] = float(st.lat)
                if st.lon is not None:
                    overlay["lon"] = float(st.lon)
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
            selected = _selected_ants(overlay, aselect)
            color = "#2e7d32"
            if selected:
                color = str(selected[0].get("color") or color)
            beams = []
            if az is not None:
                lat = float(overlay["lat"])
                lon = float(overlay["lon"])
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
                    "lat": float(overlay["lat"]),
                    "lon": float(overlay["lon"]),
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
            "labels": self._service.web_labels() if self._service is not None else {},
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
