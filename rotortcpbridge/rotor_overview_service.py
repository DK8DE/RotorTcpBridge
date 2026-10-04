"""Gemeinsamer Runtime-Service für Rotorübersicht (Desktop-Fenster + Web ``/rotoren``)."""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, List, Optional

from .angle_utils import antenna_bearing_from_rotor_and_offset, fmt_deg, wrap_deg
from .geo_utils import beam_polygon_points, maidenhead_to_lat_lon
from .i18n import t
from .rotor_overview_config import (
    deep_copy_config,
    default_site,
    load_overview_config,
    normalize_overview_config,
    save_overview_config,
    site_from_profile,
)
from .rotor_overview_monitor import RotorOverviewMonitor, SiteLiveState


def fill_from_stroke(stroke: str) -> str:
    """Hellere Füllfarbe aus Stroke-Hex (ohne Qt)."""
    s = str(stroke or "").strip()
    if s.startswith("#") and len(s) == 7:
        try:
            r = int(s[1:3], 16)
            g = int(s[3:5], 16)
            b = int(s[5:7], 16)
            r = min(255, int(r + (255 - r) * 0.45))
            g = min(255, int(g + (255 - g) * 0.45))
            b = min(255, int(b + (255 - b) * 0.45))
            return f"#{r:02x}{g:02x}{b:02x}"
        except Exception:
            pass
    return "#87CEEB"


def _ui_dark_mode() -> bool:
    try:
        from .app_config import load_config

        ui = (load_config() or {}).get("ui") or {}
        return bool(ui.get("force_dark_mode", True))
    except Exception:
        return True


class RotorOverviewService:
    """Config + Monitor + Render für Desktop und Web gemeinsam."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._cfg = load_overview_config()
        self._live: Dict[str, SiteLiveState] = {}
        self._reasons: set[str] = set()
        self._revision = 0
        self._monitor = RotorOverviewMonitor(on_update=self._on_live)
        self._cfg_listeners: List[Callable[[], None]] = []
        self._live_cache: Dict[str, Any] = {}
        self._live_cache_ts = 0.0
        self._html_cache = ""
        self._html_cache_ts = 0.0
        self._html_cache_rev = -1

    @property
    def revision(self) -> int:
        with self._lock:
            return int(self._revision)

    def add_cfg_listener(self, cb: Callable[[], None]) -> None:
        if callable(cb):
            self._cfg_listeners.append(cb)

    def remove_cfg_listener(self, cb: Callable[[], None]) -> None:
        try:
            self._cfg_listeners.remove(cb)
        except ValueError:
            pass

    def _notify_cfg(self) -> None:
        for cb in list(self._cfg_listeners):
            try:
                cb()
            except Exception:
                pass

    def _on_live(self, st: SiteLiveState) -> None:
        with self._lock:
            self._live[st.site_id] = st

    def acquire(self, reason: str) -> None:
        key = str(reason or "").strip() or "anon"
        with self._lock:
            was = bool(self._reasons)
            already = key in self._reasons
            self._reasons.add(key)
            sites = list(self._cfg.get("sites") or [])
        if not was or not already:
            self._monitor.start(sites)

    def release(self, reason: str) -> None:
        key = str(reason or "").strip() or "anon"
        with self._lock:
            self._reasons.discard(key)
            stop = not self._reasons
        if stop:
            self._monitor.stop()

    def is_active(self) -> bool:
        with self._lock:
            return bool(self._reasons)

    def get_config(self) -> Dict[str, Any]:
        with self._lock:
            return deep_copy_config(self._cfg)

    def get_live_snapshot(self) -> Dict[str, SiteLiveState]:
        with self._lock:
            return dict(self._live)

    def reload_from_disk(self) -> None:
        with self._lock:
            self._cfg = load_overview_config()
            self._revision += 1
            sites = list(self._cfg.get("sites") or [])
            active = bool(self._reasons)
        if active:
            self._monitor.start(sites)
        self._notify_cfg()

    def replace_config(self, cfg: Any, *, persist: bool = True) -> Dict[str, Any]:
        data = normalize_overview_config(cfg)
        with self._lock:
            self._cfg = data
            self._revision += 1
            self._html_cache = ""
            self._live_cache = {}
            sites = list(self._cfg.get("sites") or [])
            active = bool(self._reasons)
        if persist:
            save_overview_config(data)
        if active:
            self._monitor.start(sites)
        self._notify_cfg()
        return deep_copy_config(data)

    def save_view(self, lat: float, lon: float, zoom: int) -> None:
        with self._lock:
            self._cfg["map_center_lat"] = max(-90.0, min(90.0, float(lat)))
            self._cfg["map_center_lon"] = max(-180.0, min(180.0, float(lon)))
            self._cfg["map_zoom"] = max(2, min(18, int(zoom)))
            data = deep_copy_config(self._cfg)
            self._revision += 1
        save_overview_config(data)

    def set_show_active_antenna_only(self, on: bool) -> Dict[str, Any]:
        """Overlay: aktive Rotor-Antenne statt Menü-Häkchen."""
        with self._lock:
            self._cfg["show_active_antenna_only"] = bool(on)
            data = deep_copy_config(self._cfg)
            self._revision += 1
            self._live_cache = {}
            self._html_cache = ""
        save_overview_config(data)
        self._notify_cfg()
        return data

    def restart_monitor(self) -> None:
        with self._lock:
            sites = list(self._cfg.get("sites") or [])
            active = bool(self._reasons)
        if active:
            self._monitor.start(sites)

    def _selected_antennas(
        self, site: dict, *, aselect: Optional[int] = None
    ) -> List[dict]:
        """Antennen für Overlay: Menü-Häkchen oder aktive Rotor-Antenne."""
        ants = list(site.get("antennas") or [])
        with self._lock:
            show_active = bool(self._cfg.get("show_active_antenna_only", False))
        if not show_active:
            return [a for a in ants if a.get("enabled")]
        slot = int(aselect) if aselect in (1, 2, 3) else None
        if slot is not None and 1 <= slot <= len(ants):
            return [ants[slot - 1]]
        for a in ants:
            if a.get("enabled"):
                return [a]
        return [ants[0]] if ants else []

    def site_beams(
        self,
        site: dict,
        az_deg: Optional[float],
        *,
        aselect: Optional[int] = None,
    ) -> List[dict]:
        if az_deg is None:
            return []
        lat = float(site["lat"])
        lon = float(site["lon"])
        beams: List[dict] = []
        for ant in self._selected_antennas(site, aselect=aselect):
            bearing = antenna_bearing_from_rotor_and_offset(
                float(az_deg), float(ant.get("offset_deg") or 0.0)
            )
            opening = float(ant.get("opening_deg") or 30.0)
            range_km = float(ant.get("range_km") or 100.0)
            stroke = str(ant.get("color") or "#5BA3D0")
            fill = fill_from_stroke(stroke)
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
                        "fill": fill,
                        "name": name,
                    }
                )
        return beams

    def render_sites(self) -> List[dict]:
        with self._lock:
            sites = list(self._cfg.get("sites") or [])
            live = dict(self._live)
        out: List[dict] = []
        for site in sites:
            if not site.get("enabled"):
                continue
            sid = str(site.get("id") or "")
            st = live.get(sid)
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
            color = "#2e7d32"
            selected = self._selected_antennas(site, aselect=aselect)
            if selected:
                color = str(selected[0].get("color") or color)
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
                    "beams": self.site_beams(
                        site, float(az) if az is not None else None, aselect=aselect
                    ),
                }
            )
        return out

    def status_counts(self) -> tuple[int, int]:
        with self._lock:
            n = len([s for s in (self._cfg.get("sites") or []) if s.get("enabled")])
            online = sum(1 for st in self._live.values() if st.online)
        return n, online

    def live_payload(self, *, min_interval_s: float = 0.25) -> Dict[str, Any]:
        now = time.time()
        with self._lock:
            if self._live_cache and (now - self._live_cache_ts) < min_interval_s:
                return dict(self._live_cache)
            cfg = self._cfg
            rev = self._revision
        sites = self.render_sites()
        n, online = self.status_counts()
        payload = {
            "revision": rev,
            "dark_mode": _ui_dark_mode(),
            "center_lat": float(cfg.get("map_center_lat", 50.0)),
            "center_lon": float(cfg.get("map_center_lon", 10.0)),
            "zoom": int(cfg.get("map_zoom", 5)),
            "show_active_antenna_only": bool(cfg.get("show_active_antenna_only", False)),
            "sites": sites,
            "status_sites": n,
            "status_online": online,
            "status_text": t("overview.status_line", sites=n, online=online),
            # Damit Desktop→Web die Standorte-Einstellungen mitzieht
            "config": deep_copy_config(cfg),
        }
        with self._lock:
            self._live_cache = payload
            self._live_cache_ts = now
        return dict(payload)

    def profiles_meta(self) -> List[dict]:
        out: List[dict] = []
        try:
            from .profile_store import get_active_profile_id, list_profiles

            active = get_active_profile_id()
            for p in list_profiles():
                pid = str(p.get("id") or "")
                if not pid:
                    continue
                out.append(
                    {
                        "id": pid,
                        "name": str(p.get("name") or pid),
                        "active": pid == active,
                    }
                )
        except Exception:
            pass
        return out

    def web_labels(self) -> Dict[str, str]:
        keys = (
            "overview.title",
            "overview.menu_settings",
            "overview.menu_sites",
            "overview.menu_reload",
            "overview.settings_title",
            "overview.site_edit_title",
            "overview.import_profile",
            "overview.profile_none",
            "overview.profile_active_suffix",
            "overview.btn_import_profile",
            "overview.field_name",
            "overview.field_enabled",
            "overview.field_lat",
            "overview.field_lon",
            "overview.field_locator",
            "overview.btn_locator_apply",
            "overview.locator_invalid",
            "overview.field_mode",
            "overview.field_host",
            "overview.field_port",
            "overview.field_udp_bind",
            "overview.field_master",
            "overview.field_slave_az",
            "overview.field_poll_pos",
            "overview.field_poll_ref",
            "overview.field_smooth",
            "overview.antennas_group",
            "overview.ant_offset",
            "overview.ant_opening",
            "overview.ant_range",
            "overview.ant_dipole",
            "overview.ant_dipole_tooltip",
            "overview.pick_color",
            "overview.btn_add",
            "overview.btn_edit",
            "overview.btn_delete",
            "overview.new_site",
            "overview.enabled_yes",
            "overview.enabled_no",
            "overview.btn_save",
            "overview.btn_cancel",
            "overview.btn_close",
            "overview.saved_ok",
            "overview.save_fail",
            "overview.delete_confirm",
            "overview.web_hint",
            "overview.chk_active_antenna",
            "overview.chk_active_antenna_tooltip",
        )
        labels = {k.split(".", 1)[1]: t(k) for k in keys}
        labels["antenna_1"] = t("overview.antenna_n", n=1)
        labels["antenna_2"] = t("overview.antenna_n", n=2)
        labels["antenna_3"] = t("overview.antenna_n", n=3)
        return labels

    def build_web_html(self, *, force: bool = False) -> str:
        from .ui.rotor_overview_html import build_overview_html

        now = time.time()
        with self._lock:
            if (
                not force
                and self._html_cache
                and self._html_cache_rev == self._revision
                and (now - self._html_cache_ts) < 2.0
            ):
                return self._html_cache
            cfg = deep_copy_config(self._cfg)
            rev = self._revision
        payload = self.live_payload(min_interval_s=0.0)
        html = build_overview_html(
            {
                "center_lat": payload["center_lat"],
                "center_lon": payload["center_lon"],
                "zoom": payload["zoom"],
                "dark_mode": payload["dark_mode"],
                "sites": payload["sites"],
                "web_mode": True,
                "labels": self.web_labels(),
                "profiles": self.profiles_meta(),
                "config": cfg,
                "status_text": payload["status_text"],
                "show_active_antenna_only": bool(
                    payload.get("show_active_antenna_only", False)
                ),
            }
        )
        with self._lock:
            self._html_cache = html
            self._html_cache_ts = now
            self._html_cache_rev = rev
        return html

    def handle_action(self, action: str, payload: Optional[dict] = None) -> Dict[str, Any]:
        act = str(action or "").strip().lower()
        data = payload if isinstance(payload, dict) else {}
        if act == "reload":
            self.restart_monitor()
            return {"ok": True, "data": self.live_payload(min_interval_s=0.0)}
        if act == "save_view":
            try:
                self.save_view(
                    float(data.get("lat")),
                    float(data.get("lon")),
                    int(data.get("zoom")),
                )
            except Exception as exc:
                return {"ok": False, "error": str(exc)}
            return {"ok": True}
        if act == "set_active_antenna_only":
            try:
                on = bool(data.get("value"))
                self.set_show_active_antenna_only(on)
                return {
                    "ok": True,
                    "show_active_antenna_only": on,
                    "data": self.live_payload(min_interval_s=0.0),
                }
            except Exception as exc:
                return {"ok": False, "error": str(exc)}
        if act == "locator":
            loc = str(data.get("locator") or "").strip()
            try:
                res = maidenhead_to_lat_lon(loc)
                if not res:
                    raise ValueError("invalid")
                lat, lon = res
                return {"ok": True, "lat": float(lat), "lon": float(lon)}
            except Exception:
                return {"ok": False, "error": "locator_invalid"}
        if act == "import_profile":
            pid = str(data.get("profile_id") or "").strip()
            if not pid:
                return {"ok": False, "error": "profile_required"}
            try:
                from .profile_store import get_profile_meta, load_profile_config

                meta = get_profile_meta(pid) or {}
                cfg = load_profile_config(pid)
                site = site_from_profile(pid, str(meta.get("name") or pid), cfg)
                return {"ok": True, "site": site}
            except Exception as exc:
                return {"ok": False, "error": str(exc)}
        if act == "default_site":
            return {"ok": True, "site": default_site(name=t("overview.new_site"))}
        if act == "save_config":
            try:
                cfg = data.get("config") if "config" in data else data
                saved = self.replace_config(cfg, persist=True)
                return {
                    "ok": True,
                    "config": saved,
                    "data": self.live_payload(min_interval_s=0.0),
                }
            except Exception as exc:
                return {"ok": False, "error": str(exc)}
        return {"ok": False, "error": "unknown_action"}
