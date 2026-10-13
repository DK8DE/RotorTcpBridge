from __future__ import annotations

import base64
import socket
import urllib.request

from rotortcpbridge.map_webserver import MapWebServer
from rotortcpbridge.ui.web_shell_html import build_web_shell_html


class _Log:
    def write(self, level: str, msg: str) -> None:
        pass


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])
    finally:
        s.close()


def _get(url: str) -> str:
    tok = base64.b64encode(b"rotor:rotor").decode("ascii")
    req = urllib.request.Request(url, headers={"Authorization": f"Basic {tok}"})
    return urllib.request.urlopen(req, timeout=3).read().decode("utf-8")


def test_shell_html_contains_views_and_serial():
    html = build_web_shell_html(dark=True, labels={"map": "Karte", "compass": "Kompass"})
    assert "/map?embed=1" in html
    assert "/compass?embed=1" in html
    assert "window.WebSerial" in html
    assert "rotorView" in html
    assert "__BG__" not in html and "__LABELS__" not in html and "__WEB_SERIAL_JS__" not in html


def test_root_serves_shell_and_map_stays_on_map():
    port = _free_port()
    srv = MapWebServer(
        "127.0.0.1",
        port,
        _Log(),
        password="rotor",
        html_provider=lambda: "<html>MAPPAGE</html>",
        shell_html_provider=lambda: "<html>SHELLPAGE</html>",
    )
    ok, err = srv.start()
    assert ok, err
    try:
        assert "SHELLPAGE" in _get(f"http://127.0.0.1:{port}/")
        assert "MAPPAGE" in _get(f"http://127.0.0.1:{port}/map?embed=1")
    finally:
        srv.stop()
