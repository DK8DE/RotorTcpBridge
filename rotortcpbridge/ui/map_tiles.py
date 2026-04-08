"""Offline-Kacheln, rotortiles:-Scheme und lokaler HTTP-Tile-Server für die Karte."""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtWebEngineCore import (
    QWebEngineProfile,
    QWebEngineUrlRequestJob,
    QWebEngineUrlSchemeHandler,
)

# Custom URL-Scheme für Offline-Tiles (funktioniert ohne Netzwerkadapter)
ROTORTILES_SCHEME = "rotortiles"

_DEBUG_TILES = False


def _static_lib_path() -> Path:
    """Pfad zu rotortcpbridge/static (Leaflet, Maidenhead, MarkerCluster).

    PyInstaller entpackt nach sys._MEIPASS/rotortcpbridge/static; Entwicklung: Paketpfad.
    """
    dev = Path(__file__).resolve().parent.parent / "static"
    candidates: list[Path] = []
    try:
        meip = getattr(sys, "_MEIPASS", None)
        if meip:
            candidates.append(Path(meip) / "rotortcpbridge" / "static")
    except Exception:
        pass
    candidates.append(dev)
    for p in candidates:
        try:
            if p.is_dir() and (p / "leaflet.min.js").is_file():
                return p
        except Exception:
            continue
    return dev


def set_pending_map_html(html: str) -> None:
    """Setzt das HTML für rotortiles:map/ (Offline-Modus ohne HTTP-Tiles)."""
    global _pending_map_html
    _pending_map_html = html


class _TilesUrlSchemeHandler(QWebEngineUrlSchemeHandler):
    """Liefert Offline-Tiles und Bibliotheken (Leaflet) via rotortiles:-URLs (ohne Netzwerk)."""

    def requestStarted(self, job: QWebEngineUrlRequestJob) -> None:
        url = job.requestUrl()
        url_str = url.toString()
        path = url.path().lstrip("/")
        parts = path.split("/") if path else []
        # Map-Seite: rotortiles:map/ oder rotortiles:map
        if parts and parts[0] == "map":
            global _pending_map_html
            html = _pending_map_html
            if not html:
                if _DEBUG_TILES:
                    print("[rotortiles] FAIL (map): kein HTML gesetzt")
                job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
                return
            data = html.encode("utf-8")
            buffer = QBuffer(job)
            buffer.setData(QByteArray(data))
            buffer.open(QIODevice.OpenModeFlag.ReadOnly)
            job.reply(QByteArray(b"text/html; charset=utf-8"), buffer)
            if _DEBUG_TILES:
                print(f"[rotortiles] OK map ({len(data)} bytes)")
            return
        # Lib: rotortiles:lib/leaflet.min.js, leaflet.css, maidenhead.js, images/...
        if len(parts) >= 2 and parts[0] == "lib":
            rel = "/".join(parts[1:])
            base_lib = _static_lib_path()
            lib_path = (base_lib / rel).resolve()
            if not str(lib_path).startswith(str(base_lib.resolve())):
                job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
                return
            mime = "application/javascript"
            if rel.endswith(".css"):
                mime = "text/css"
            elif rel.endswith(".js"):
                mime = "application/javascript"
            elif rel.endswith(".png"):
                mime = "image/png"
            if not lib_path.is_file():
                job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
                return
            try:
                data = lib_path.read_bytes()
            except OSError:
                job.fail(QWebEngineUrlRequestJob.Error.RequestFailed)
                return
            buffer = QBuffer(job)
            buffer.setData(QByteArray(data))
            buffer.open(QIODevice.OpenModeFlag.ReadOnly)
            job.reply(QByteArray(mime.encode()), buffer)
            if _DEBUG_TILES:
                print(f"[rotortiles] OK lib: {rel}")
            return
        if len(parts) < 4 or parts[0] not in ("light", "dark"):
            if _DEBUG_TILES:
                print(f"[rotortiles] FAIL (kein Tile): {url_str} -> path={path!r} parts={parts}")
            job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
            return
        theme, z_s, x_s, y_part = parts[0], parts[1], parts[2], parts[3]
        if not y_part.endswith(".png"):
            job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
            return
        try:
            z, x, y = int(z_s), int(x_s), int(y_part[:-4])
        except ValueError:
            job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
            return
        base = _offline_tiles_base_path(dark=(theme == "dark"))
        tile_path = base / str(z) / str(x) / f"{y}.png"
        if not tile_path.is_file():
            if _DEBUG_TILES:
                print(f"[rotortiles] FAIL (Datei fehlt): {tile_path}")
            job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
            return
        try:
            data = tile_path.read_bytes()
        except OSError as e:
            if _DEBUG_TILES:
                print(f"[rotortiles] FAIL (Lesefehler): {tile_path} -> {e}")
            job.fail(QWebEngineUrlRequestJob.Error.RequestFailed)
            return
        buffer = QBuffer(job)
        buffer.setData(QByteArray(data))
        buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        job.reply(QByteArray(b"image/png"), buffer)
        if _DEBUG_TILES:
            print(f"[rotortiles] OK tile: {z}/{x}/{y}.png ({len(data)} bytes)")


_tiles_handler_installed = False
_pending_map_html: str = ""  # HTML für rotortiles:map/ (nur bei load() im Offline-Modus)

# Ein einzelner HTTP-Server für beide Kartensets (Light + Dark) via URL-Pfad
_http_tile_server: Optional["_TilesHTTPServer"] = None
_http_server_lock = threading.Lock()


class _TilesHTTPServer:
    """Lokaler HTTP-Server für Light- und Dark-Tiles über URL-Pfade.
    /light/z/x/y.png → KartenLight, /dark/z/x/y.png → KartenDark."""

    def __init__(self):
        self._server = None
        self._thread = None
        self._port = 0
        self._light_path = _offline_tiles_base_path(False)
        self._dark_path = _offline_tiles_base_path(True)

    def start(self) -> int:
        if self._server is not None:
            return self._port
        light_dir = str(self._light_path)
        dark_dir = str(self._dark_path)
        for port in range(37540, 37580):
            try:
                from http.server import HTTPServer, BaseHTTPRequestHandler

                class _Handler(BaseHTTPRequestHandler):
                    def log_message(self, format, *args):
                        pass

                    def do_GET(self):
                        path = self.path.lstrip("/")
                        if path.startswith("light/"):
                            rel = path[len("light/") :]
                            base = light_dir
                        elif path.startswith("dark/"):
                            rel = path[len("dark/") :]
                            base = dark_dir
                        else:
                            self.send_error(404)
                            return
                        file_path = os.path.join(base, rel.replace("/", os.sep))
                        if not os.path.isfile(file_path):
                            self.send_error(404)
                            return
                        try:
                            with open(file_path, "rb") as f:
                                data = f.read()
                            self.send_response(200)
                            self.send_header("Content-Type", "image/png")
                            self.send_header("Content-Length", str(len(data)))
                            self.send_header("Cache-Control", "no-store")
                            self.end_headers()
                            self.wfile.write(data)
                        except (
                            OSError,
                            ConnectionAbortedError,
                            BrokenPipeError,
                            ConnectionResetError,
                        ):
                            pass

                self._server = HTTPServer(("127.0.0.1", port), _Handler)
                self._port = port
                self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
                self._thread.start()
                if _DEBUG_TILES:
                    print(
                        f"[TileHTTP] Server gestartet port={port} light={light_dir} dark={dark_dir}"
                    )
                return port
            except OSError:
                continue
        return 0


def _offline_tile_url_http(dark: bool = False) -> str:
    """HTTP-Server starten, Tile-URL liefern. Leer wenn Server nicht startet."""
    global _http_tile_server
    with _http_server_lock:
        if _http_tile_server is None:
            _http_tile_server = _TilesHTTPServer()
        port = _http_tile_server.start()
    theme = "dark" if dark else "light"
    url = f"http://127.0.0.1:{port}/{theme}/{{z}}/{{x}}/{{y}}.png" if port else ""
    if _DEBUG_TILES:
        print(f"[TileHTTP] dark={dark} port={port} url={url[:70]}")
    return url


def install_rotortiles_handler() -> None:
    """Nach QApplication aufrufen."""
    global _tiles_handler_installed
    if _tiles_handler_installed:
        return
    profile = QWebEngineProfile.defaultProfile()
    profile.installUrlSchemeHandler(ROTORTILES_SCHEME.encode(), _TilesUrlSchemeHandler())
    _tiles_handler_installed = True


def _offline_tiles_base_path(dark: bool = False) -> Path:
    """Pfad zum Karten-Ordner (Standard: z/x/y.png). dark=True -> KartenDark, sonst KartenLight."""
    subdir = "KartenDark" if dark else "KartenLight"
    for base in [
        Path(__file__).resolve().parents[1] / subdir,
        Path(__file__).resolve().parents[2] / subdir,
        Path.cwd() / subdir,
        Path(os.environ.get("APPDATA", "")) / "RotorTcpBridge" / subdir,
    ]:
        if base.exists() and base.is_dir():
            return base
    return Path(__file__).resolve().parents[1] / subdir


def _offline_zoom_range(dark: bool = False) -> tuple[int, int]:
    """Ermittelt min/max Zoom aus vorhandenen Tiles (z/x/y-Struktur)."""
    for use_dark in (dark, not dark):
        base = _offline_tiles_base_path(use_dark)
        zooms = []
        try:
            for name in os.listdir(base):
                if name.isdigit():
                    z_dir = base / name
                    if z_dir.is_dir() and any(
                        (z_dir / n).is_dir() for n in os.listdir(z_dir) if n.isdigit()
                    ):
                        zooms.append(int(name))
            if zooms:
                return (min(zooms), max(zooms))
        except OSError:
            pass
    return (0, 4)


def _offline_tile_url(dark: bool = False) -> str:
    """Tile-URL: HTTP-Server (bei Netzwerk), sonst rotortiles:-Scheme."""
    url = _offline_tile_url_http(dark)
    if url:
        return url
    theme = "dark" if dark else "light"
    return f"{ROTORTILES_SCHEME}:{theme}/{{z}}/{{x}}/{{y}}.png"
