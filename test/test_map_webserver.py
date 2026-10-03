"""Tests für den Antennenkarten-HTTP-Webserver (Start / Port-Konflikt / Auth)."""
from __future__ import annotations

import base64
import json
import socket
import time
import urllib.error
import urllib.request

import pytest

from rotortcpbridge.map_webserver import (
    DEFAULT_MAP_WEBSERVER_PASSWORD,
    MapWebServer,
    resolve_map_webserver_password,
)


class _Log:
    def __init__(self) -> None:
        self.msgs: list[tuple[str, str]] = []

    def write(self, level: str, msg: str) -> None:
        self.msgs.append((level, msg))


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])
    finally:
        s.close()


def _auth_header(password: str, user: str = "rotor") -> str:
    token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
    return f"Basic {token}"


def _urlopen(url: str, *, password: str = "rotor", data: bytes | None = None, headers: dict | None = None, method: str | None = None):
    hdrs = dict(headers or {})
    hdrs["Authorization"] = _auth_header(password)
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    return urllib.request.urlopen(req, timeout=2)


def test_resolve_map_webserver_password_default() -> None:
    assert resolve_map_webserver_password("") == DEFAULT_MAP_WEBSERVER_PASSWORD
    assert resolve_map_webserver_password(None) == "rotor"
    assert resolve_map_webserver_password("  ") == "rotor"
    assert resolve_map_webserver_password("geheim") == "geheim"


def test_map_webserver_serves_html_and_setaz() -> None:
    port = _free_port()
    calls: list[tuple] = []
    live = {"azimuth": 42.5, "beams": []}

    srv = MapWebServer(
        "127.0.0.1",
        port,
        _Log(),
        password="rotor",
        html_provider=lambda: "<html><body>map-ok</body></html>",
        live_provider=lambda: dict(live),
        setaz_handler=lambda lat, lon, dest=None: calls.append((lat, lon, dest)),
        aswatch_provider=lambda: ([], 0),
        aircraft_provider=lambda: [],
        asnearest_provider=lambda: [],
    )
    ok, err = srv.start()
    assert ok, err
    try:
        with _urlopen(f"http://127.0.0.1:{port}/") as resp:
            body = resp.read().decode("utf-8")
            assert "map-ok" in body
        with _urlopen(f"http://127.0.0.1:{port}/api/state") as resp:
            data = json.loads(resp.read().decode("utf-8"))
            assert data["ok"] is True
            assert data["data"]["azimuth"] == 42.5
        with _urlopen(
            f"http://127.0.0.1:{port}/api/setaz",
            data=json.dumps(
                {"lat": 51.0, "lon": 7.0, "asnearest_dest": "JN39"}
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        ) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            assert result["ok"] is True
        # Signal/Handler darf asynchron sein – kurz warten
        deadline = time.time() + 1.0
        while not calls and time.time() < deadline:
            time.sleep(0.05)
        assert calls
        assert calls[0][0] == pytest.approx(51.0)
        assert calls[0][1] == pytest.approx(7.0)
        assert calls[0][2] == "JN39"
    finally:
        srv.stop()


def test_map_webserver_serves_compass_and_ui_set_az() -> None:
    port = _free_port()
    actions: list[tuple] = []
    compass = {"enable_el": False, "az": {"ist": 10.0, "soll": 20.0}}

    srv = MapWebServer(
        "127.0.0.1",
        port,
        _Log(),
        password="secret",
        html_provider=lambda: "<html><body>map-ok</body></html>",
        compass_html_provider=lambda: "<html><body>compass-ok</body></html>",
        live_provider=lambda: {"beams": []},
        compass_provider=lambda: dict(compass),
        ui_action_handler=lambda action, payload: actions.append((action, payload)),
    )
    ok, err = srv.start()
    assert ok, err
    try:
        with _urlopen(f"http://127.0.0.1:{port}/compass", password="secret") as resp:
            body = resp.read().decode("utf-8")
            assert "compass-ok" in body
        with _urlopen(f"http://127.0.0.1:{port}/api/compass", password="secret") as resp:
            data = json.loads(resp.read().decode("utf-8"))
            assert data["ok"] is True
            assert data["data"]["az"]["ist"] == 10.0
        with _urlopen(
            f"http://127.0.0.1:{port}/api/ui",
            password="secret",
            data=json.dumps({"action": "set_az", "deg": 123.4}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        ) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            assert result["ok"] is True
        deadline = time.time() + 1.0
        while not actions and time.time() < deadline:
            time.sleep(0.05)
        assert actions
        assert actions[0][0] == "set_az"
        assert float(actions[0][1].get("deg")) == pytest.approx(123.4)
    finally:
        srv.stop()


def test_map_webserver_requires_password() -> None:
    port = _free_port()
    srv = MapWebServer(
        "127.0.0.1",
        port,
        _Log(),
        password="",  # → Default rotor
        html_provider=lambda: "<html><body>map-ok</body></html>",
    )
    ok, err = srv.start()
    assert ok, err
    try:
        assert srv.password == "rotor"
        with pytest.raises(urllib.error.HTTPError) as ei:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2)
        assert ei.value.code == 401
        with pytest.raises(urllib.error.HTTPError) as ei2:
            _urlopen(f"http://127.0.0.1:{port}/", password="wrong")
        assert ei2.value.code == 401
        with _urlopen(f"http://127.0.0.1:{port}/", password="rotor") as resp:
            assert "map-ok" in resp.read().decode("utf-8")
        srv.set_password("neu")
        with _urlopen(f"http://127.0.0.1:{port}/", password="neu") as resp:
            assert "map-ok" in resp.read().decode("utf-8")
    finally:
        srv.stop()


def test_build_compass_html_contains_canvas() -> None:
    from rotortcpbridge.ui.compass_html import build_compass_html

    html = build_compass_html(
        {
            "dark_mode": True,
            "compass_init": {"enable_el": True, "az": {}, "el": {"max_deg": 90}},
            "web_chrome": {"labels": {}},
        }
    )
    assert "azCanvas" in html
    assert "elCanvas" in html
    assert "compass_open" in html


def test_map_webserver_port_busy() -> None:
    port = _free_port()
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
        blocker.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    blocker.bind(("127.0.0.1", port))
    blocker.listen(1)
    try:
        srv = MapWebServer("127.0.0.1", port, _Log())
        ok, err = srv.start()
        assert ok is False
        assert err
        assert "belegt" in err.lower() or "bind" in err.lower() or str(port) in err
        assert not srv.running
    finally:
        blocker.close()
        try:
            srv.stop()
        except Exception:
            pass
