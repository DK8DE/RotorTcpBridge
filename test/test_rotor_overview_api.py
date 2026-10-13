from __future__ import annotations

import json
import threading
from types import SimpleNamespace

from rotortcpbridge.rotor_overview_config import (
    LOCAL_SITE_ID,
    default_site,
    normalize_overview_config,
    normalize_site,
)
from rotortcpbridge.rotor_overview_monitor import SiteLiveState, _SiteWorker
from rotortcpbridge.rotor_overview_node import (
    apply_node_snapshot_to_state,
    build_overview_node_snapshot,
)


def test_overview_node_payload_from_ctrl_cfg(monkeypatch) -> None:
    monkeypatch.setattr(
        "rotortcpbridge.profile_store.get_active_profile_meta",
        lambda: {"id": "prof-1", "name": "Station West"},
    )
    az = SimpleNamespace(
        online=True,
        referenced=True,
        moving=False,
        pos_d10=900,
        smooth_pos_d10f=900.0,
        pos_max_d10=3600,
        antoff1=12.0,
        angle1=40.0,
        antdis1=150,
        antoff2=90.0,
        angle2=30.0,
        antdis2=80,
        antoff3=0.0,
        angle3=30.0,
        antdis3=100,
        telemetry=SimpleNamespace(
            temp_motor_c=41.2,
            temp_ambient_c=7.5,
            wind_kmh=18.4,
            wind_dir_deg=240.0,
        ),
    )
    ctrl = SimpleNamespace(az=az, slave_az=21, wind_enabled=True, wind_enabled_known=True)
    cfg = {
        "ui": {
            "location_lat": 51.25,
            "location_lon": 6.8,
            "location_locator": "JO31",
            "compass_antenna": 1,
            "antenna_names": ["Yagi", "Quad", ""],
            "antenna_offsets_az": [12.0, 90.0, 0.0],
            "antenna_angles_az": [40.0, 30.0, 30.0],
            "antenna_ranges_az": [150, 80, 100],
        },
        "rotor_bus": {"enable_az": True, "slave_az": 21},
    }
    snap = build_overview_node_snapshot(ctrl, cfg)
    assert snap["profile_id"] == "prof-1"
    assert snap["profile_name"] == "Station West"
    assert snap["locator"] == "JO31"
    assert snap["lat"] == 51.25
    assert snap["lon"] == 6.8
    assert snap["online"] is True
    assert snap["az_deg"] == 90.0
    assert snap["aselect"] == 2
    assert snap["antennas"][0]["name"] == "Yagi"
    assert snap["antennas"][0]["offset_deg"] == 12.0
    assert snap["slave_az"] == 21
    assert snap["temp_motor_c"] == 41.2
    assert snap["temp_ambient_c"] == 7.5
    assert snap["wind_kmh"] == 18.4
    assert snap["wind_dir_deg"] == 240.0
    assert snap["wind_visible"] is True


def test_local_site_always_present_after_normalize() -> None:
    cfg = normalize_overview_config({"sites": []})
    sites = cfg["sites"]
    assert sites
    assert sites[0]["id"] == LOCAL_SITE_ID
    assert sites[0]["kind"] == "local"


def test_old_tcp_sites_dropped_on_migration() -> None:
    cfg = normalize_overview_config(
        {
            "sites": [
                {
                    "id": "old",
                    "name": "Gateway",
                    "mode": "tcp",
                    "host": "192.168.1.10",
                    "port": 8886,
                    "slave_az": 20,
                    "poll_pos_ms": 500,
                }
            ]
        }
    )
    kinds = {s["kind"] for s in cfg["sites"]}
    ids = {s["id"] for s in cfg["sites"]}
    assert kinds == {"local"}
    assert "old" not in ids


def test_add_site_persists_only_host_port_password() -> None:
    raw = default_site(port=8080)
    raw["host"] = "10.0.0.5"
    raw["password"] = "secret"
    raw["poll_pos_ms"] = 200
    raw["slave_az"] = 99
    site = normalize_site(raw)
    assert site is not None
    assert site["kind"] == "app"
    assert site["host"] == "10.0.0.5"
    assert site["port"] == 8080
    assert site["password"] == "secret"
    assert "poll_pos_ms" not in site
    assert "slave_az" not in site
    assert "master_id" not in site


def test_http_worker_applies_node_json_without_rs485(monkeypatch) -> None:
    payload = {
        "ok": True,
        "data": {
            "profile_name": "Remote A",
            "locator": "JN39",
            "lat": 50.1,
            "lon": 8.2,
            "online": True,
            "az_deg": 45.0,
            "referenced": True,
            "aselect": 1,
            "temp_motor_c": 33.0,
            "temp_ambient_c": 4.0,
            "wind_visible": True,
            "wind_kmh": 12.0,
            "wind_dir_deg": 90.0,
            "antennas": [
                {
                    "enabled": True,
                    "name": "Beam",
                    "offset_deg": 0,
                    "opening_deg": 30,
                    "range_km": 100,
                    "dipole": False,
                    "color": "#5BA3D0",
                }
            ],
        },
    }

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def read(self):
            return json.dumps(payload).encode("utf-8")

    monkeypatch.setattr(
        "rotortcpbridge.rotor_overview_monitor.urllib.request.urlopen",
        lambda *_a, **_k: _Resp(),
    )
    w = _SiteWorker(
        {"id": "r1", "kind": "app", "host": "127.0.0.1", "port": 80, "password": ""},
        on_update=lambda *_a, **_k: None,
        stop_event=threading.Event(),
    )
    w._poll_http()
    assert w.state.online is True
    assert w.state.az_deg == 45.0
    assert w.state.profile_name == "Remote A"
    assert w.state.locator == "JN39"
    assert w.state.aselect == 1
    assert w.state.antennas and w.state.antennas[0]["name"] == "Beam"
    assert w.state.temp_motor_c == 33.0
    assert w.state.wind_visible is True
    assert w.state.wind_kmh == 12.0

    payload["data"]["profile_name"] = "Remote B"
    payload["data"]["az_deg"] = 200.0
    w._poll_http()
    assert w.state.profile_name == "Remote B"
    assert w.state.az_deg == 200.0


def test_merge_keeps_site_antenna_enabled() -> None:
    from rotortcpbridge.rotor_overview_config import merge_site_antennas
    from rotortcpbridge.rotor_overview_service import RotorOverviewService

    cfg_ants = [
        {"enabled": True, "name": "A", "offset_deg": 0, "opening_deg": 30, "range_km": 100},
        {"enabled": False, "name": "B", "offset_deg": 0, "opening_deg": 30, "range_km": 100},
        {"enabled": True, "name": "C", "offset_deg": 0, "opening_deg": 30, "range_km": 100},
    ]
    live_ants = [
        {"enabled": True, "name": "Yagi", "offset_deg": 12, "opening_deg": 40, "range_km": 150},
        {"enabled": True, "name": "Quad", "offset_deg": 90, "opening_deg": 30, "range_km": 80},
        {"enabled": True, "name": "Vert", "offset_deg": 180, "opening_deg": 20, "range_km": 60},
    ]
    merged = merge_site_antennas(cfg_ants, live_ants)
    assert merged[0]["enabled"] is True
    assert merged[1]["enabled"] is False
    assert merged[2]["enabled"] is True
    assert merged[0]["name"] == "Yagi"
    assert merged[0]["offset_deg"] == 12.0

    svc = RotorOverviewService()
    svc._cfg = {
        "sites": [
            {
                "id": "local",
                "kind": "local",
                "enabled": True,
                "name": "",
                "host": "",
                "port": 80,
                "password": "",
                "lat": 50.0,
                "lon": 10.0,
                "antennas": cfg_ants,
            }
        ],
        "map_center_lat": 50.0,
        "map_center_lon": 10.0,
        "map_zoom": 5,
        "show_active_antenna_only": False,
    }
    st = SiteLiveState(site_id="local", online=True, az_deg=45.0, az_smooth_deg=45.0)
    st.antennas = live_ants
    st.lat = 51.0
    st.lon = 7.0
    svc._live["local"] = st
    rendered = svc.render_sites()
    assert len(rendered) == 1
    names = [b["name"] for b in rendered[0]["beams"]]
    assert "Yagi" in names
    assert "Quad" not in names
    assert "Vert" in names


def test_apply_node_updates_live_state() -> None:
    st = SiteLiveState(site_id="x")
    apply_node_snapshot_to_state(
        st,
        {"online": False, "az_deg": 10.0, "profile_name": "P", "lat": 1.0, "lon": 2.0},
    )
    assert st.online is False
    assert st.az_deg == 10.0
    assert st.profile_name == "P"
    assert st.lat == 1.0
    assert st.wind_visible is False
    assert st.wind_kmh is None


def test_wind_hidden_when_sensor_disabled(monkeypatch) -> None:
    monkeypatch.setattr(
        "rotortcpbridge.profile_store.get_active_profile_meta",
        lambda: {"id": "p", "name": "P"},
    )
    az = SimpleNamespace(
        online=True,
        referenced=True,
        moving=False,
        pos_d10=0,
        smooth_pos_d10f=0.0,
        pos_max_d10=3600,
        telemetry=SimpleNamespace(wind_kmh=5.0, wind_dir_deg=90.0),
    )
    ctrl = SimpleNamespace(az=az, slave_az=20, wind_enabled=False, wind_enabled_known=True)
    snap = build_overview_node_snapshot(ctrl, {"ui": {}, "rotor_bus": {}})
    assert snap["wind_visible"] is False
    assert snap["wind_kmh"] is None
    assert snap["wind_dir_deg"] is None

    st = SiteLiveState(site_id="x")
    apply_node_snapshot_to_state(
        st,
        {"wind_visible": False, "wind_kmh": 9.0, "wind_dir_deg": 10.0},
    )
    assert st.wind_visible is False
    assert st.wind_kmh is None
    assert st.wind_dir_deg is None
