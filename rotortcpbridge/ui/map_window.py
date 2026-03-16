"""Karten-Fenster mit Leaflet und Antennen-Beam."""
from __future__ import annotations

import base64
import json
import math
import os
import socket
import threading
import time
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QPainter, QPalette, QPen
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtWebEngineCore import (
    QWebEnginePage,
    QWebEngineProfile,
    QWebEngineUrlRequestJob,
    QWebEngineUrlSchemeHandler,
)
from PySide6.QtWebEngineWidgets import QWebEngineView

from ..angle_utils import clamp_el, fmt_deg, shortest_delta_deg, wrap_deg
from ..ui.led_widget import Led
from ..ui.ui_utils import px_to_dip
from ..ui.weather_window import WindRoseWidget
from ..app_icon import get_app_icon
from ..geo_utils import beam_center_line_points, beam_polygon_points, bearing_deg
from ..i18n import t
from .elevation_window import ElevationProfileWindow

# Custom URL-Scheme für Offline-Tiles (funktioniert ohne Netzwerkadapter)
ROTORTILES_SCHEME = "rotortiles"


def _static_lib_path() -> Path:
    """Pfad zu rotortcpbridge/static (Leaflet, Maidenhead)."""
    return Path(__file__).resolve().parent.parent / "static"


_DEBUG_TILES = False


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
                    print(f"[rotortiles] FAIL (map): kein HTML gesetzt")
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
                            rel = path[len("light/"):]
                            base = light_dir
                        elif path.startswith("dark/"):
                            rel = path[len("dark/"):]
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
                        except (OSError, ConnectionAbortedError, BrokenPipeError, ConnectionResetError):
                            pass

                self._server = HTTPServer(("127.0.0.1", port), _Handler)
                self._port = port
                self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
                self._thread.start()
                if _DEBUG_TILES:
                    print(f"[TileHTTP] Server gestartet port={port} light={light_dir} dark={dark_dir}")
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
                    if z_dir.is_dir() and any((z_dir / n).is_dir() for n in os.listdir(z_dir) if n.isdigit()):
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


def _build_map_html(params: dict, dark: bool | None = None) -> str:
    """Erstellt die vollständige HTML-Seite mit Leaflet.
    Enthält window.updateBeam(data) zum Aktualisieren ohne Zoom/Zentrum zu ändern.
    dark: expliziter Wert (hat Vorrang vor params['dark_mode'])."""
    lat = params["lat"]
    lon = params["lon"]
    azimuth = params["azimuth"]
    opening = params["opening"]
    range_km = params["range_km"]
    poly_json = json.dumps(params["polygon"])
    center_line = params["center_line"]
    loc_str = params["location_str"]
    info_standort = params.get("info_standort", "Standort")
    info_offnung = params.get("info_offnung", "Öffnungswinkel")
    info_reichweite = params.get("info_reichweite", "Reichweite")
    if dark is None:
        dark = bool(params.get("dark_mode", False))
    else:
        dark = bool(dark)
    offline = bool(params.get("offline", False))
    locator_overlay = bool(params.get("map_locator_overlay", False))
    offline_min_z, offline_max_z = _offline_zoom_range(dark)
    if offline:
        tile_url = _offline_tile_url(dark) or ("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png" if dark else "https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png")
        tile_url_light = _offline_tile_url(False) or "https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png"
        tile_url_dark = _offline_tile_url(True) or "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"
        if _DEBUG_TILES:
            print(f"[BuildHTML] dark={dark} tile={tile_url[:60]} light={tile_url_light[:60]} dark={tile_url_dark[:60]}")
    elif dark:
        tile_url = "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"
        tile_url_light = tile_url_dark = tile_url
    else:
        tile_url = "https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png"
        tile_url_light = tile_url_dark = tile_url
    info_bg = "#2d2d2d" if dark else "white"
    info_color = "#e1e1e1" if dark else "inherit"
    body_bg = "#1c1c1c" if dark else "inherit"

    antenna_path = Path(__file__).resolve().parent.parent / "Antenne.png"
    antenna_data_url = ""
    antenna_target_data_url = ""
    try:
        data = antenna_path.read_bytes()
        antenna_data_url = "data:image/png;base64," + base64.b64encode(data).decode("ascii")
    except OSError:
        pass
    antenna_target_path = Path(__file__).resolve().parent.parent / "Antenne_T.png"
    try:
        data = antenna_target_path.read_bytes()
        antenna_target_data_url = "data:image/png;base64," + base64.b64encode(data).decode("ascii")
    except OSError:
        pass

    # Leaflet und Maidenhead inline einbetten (rotortiles:-URLs werden bei about:blank blockiert)
    def _read_static(name: str) -> str:
        p = _static_lib_path() / name
        if not p.is_file():
            return ""
        txt = p.read_text(encoding="utf-8", errors="replace")
        if name.endswith(".js"):
            txt = txt.replace("</script>", "<\\/script>")
        return txt

    leaflet_css = _read_static("leaflet.css")
    leaflet_js = _read_static("leaflet.min.js")
    maidenhead_js = _read_static("maidenhead.js")

    return f"""<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Antennenkarte</title>
  <style>{leaflet_css}</style>
  <style>
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    html, body {{ width: 100%; height: 100%; overflow: hidden; background: {body_bg}; }}
    #map {{ width: 100%; height: 100%; }}
    #info {{ position: absolute; top: 12px; left: 62px; z-index: 1000;
      background: {info_bg}; color: {info_color}; padding: 10px 14px; border-radius: 8px;
      font: 13px/1.4 sans-serif; }}
    #info div {{ margin: 2px 0; }}
  </style>
</head>
<body>
  <div id="info">
    <div><strong>{info_standort}:</strong> {loc_str}</div>
    <div><strong>{info_offnung}:</strong> {opening:.1f}°</div>
    <div><strong>{info_reichweite}:</strong> {range_km:.1f} km</div>
  </div>
  <div id="map"></div>
  <script>{leaflet_js}</script>
  <script>{maidenhead_js}</script>
  <script>
    const lat = {lat};
    const lon = {lon};
    const polyCoords = {poly_json};
    const centerLine = {json.dumps(center_line)};

    const TILE_URL_DARK = "https://{{s}}.basemaps.cartocdn.com/dark_all/{{z}}/{{x}}/{{y}}{{r}}.png";
    const TILE_URL_LIGHT = "https://{{s}}.basemaps.cartocdn.com/rastertiles/voyager/{{z}}/{{x}}/{{y}}{{r}}.png";
    const OFFLINE_ATTRIBUTION = "© OpenStreetMap-Mitwirkende";
    const ONLINE_ATTRIBUTION = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>';
    const isOffline = {str(offline).lower()};
    const offlineMinZ = {offline_min_z};
    const offlineMaxZ = {offline_max_z};
    const tileOpts = isOffline ? {{ maxZoom: offlineMaxZ, minZoom: offlineMaxZ, attribution: OFFLINE_ATTRIBUTION }}
      : {{ subdomains: 'abcd', maxZoom: 19, attribution: ONLINE_ATTRIBUTION }};

    console.log('[Map] Init isOffline=' + isOffline + ' tileUrl=' + {json.dumps(tile_url)} + ' origin=' + (document.location && document.location.origin ? document.location.origin : '?'));
    const initZoom = isOffline ? {offline_max_z} : 10;
    const map = L.map('map', {{ maxZoom: isOffline ? {offline_max_z} : 19, minZoom: isOffline ? {offline_max_z} : 3 }}).setView([lat, lon], initZoom);
    let tileLayer = L.tileLayer({json.dumps(tile_url)}, tileOpts).addTo(map);
    tileLayer.on('tileerror', function(e) {{ console.error('[Map] Tile-Fehler:', e.tile?.src || e); }});
    tileLayer.on('load', function() {{ console.log('[Map] Tiles geladen'); }});

    const antennaIcon = {json.dumps(antenna_data_url)} ? L.icon({{
      iconUrl: {json.dumps(antenna_data_url)},
      iconSize: [30, 30],
      iconAnchor: [15, 30]
    }}) : null;
    const antennaTargetIcon = {json.dumps(antenna_target_data_url)} ? L.icon({{
      iconUrl: {json.dumps(antenna_target_data_url)},
      iconSize: [30, 30],
      iconAnchor: [15, 30]
    }}) : antennaIcon;
    let marker = L.marker([lat, lon], antennaIcon ? {{ icon: antennaIcon }} : {{}}).addTo(map);
    marker.bindPopup("Antennenstandort").openPopup();

    let poly = L.polygon(polyCoords, {{
      color: '#5BA3D0', fillColor: '#87CEEB', fillOpacity: 0.35, weight: 2,
      interactive: false
    }}).addTo(map);

    let dashLine = L.polyline(centerLine, {{
      color: 'blue', weight: 2, dashArray: '8, 8',
      interactive: false
    }}).addTo(map);

    const allLayer = L.featureGroup([marker, poly, dashLine]);
    map.fitBounds(allLayer.getBounds().pad(0.1));
    if (isOffline) {{
      map.setMinZoom(offlineMaxZ);
      map.setMaxZoom(offlineMaxZ);
      map.setZoom(offlineMaxZ);
    }}

    let clickMarker = null;
    map.on('click', function(e) {{
      const lat2 = e.latlng.lat;
      const lon2 = e.latlng.lng;
      if (clickMarker) map.removeLayer(clickMarker);
      clickMarker = L.marker([lat2, lon2], antennaTargetIcon ? {{ icon: antennaTargetIcon }} : {{}}).addTo(map);
      clickMarker.bindPopup("Ziel");
      window.location = 'rotorapp://setaz?lat=' + lat2 + '&lon=' + lon2;
    }});

    window.updateBeam = function(data) {{
      if (!data) return;
      map.removeLayer(marker);
      map.removeLayer(poly);
      map.removeLayer(dashLine);
      marker = L.marker([data.lat, data.lon], antennaIcon ? {{ icon: antennaIcon }} : {{}}).addTo(map);
      marker.bindPopup("Antennenstandort");
      poly = L.polygon(data.polygon, {{ color: '#5BA3D0', fillColor: '#87CEEB', fillOpacity: 0.35, weight: 2, interactive: false }}).addTo(map);
      dashLine = L.polyline(data.centerLine, {{ color: 'blue', weight: 2, dashArray: '8, 8', interactive: false }}).addTo(map);
      document.getElementById('info').innerHTML = '<div><strong>' + (data.info_standort || 'Standort') + ':</strong> ' + data.location_str + '</div>' +
        '<div><strong>' + (data.info_offnung || 'Öffnungswinkel') + ':</strong> ' + data.opening.toFixed(1) + '°</div>' +
        '<div><strong>' + (data.info_reichweite || 'Reichweite') + ':</strong> ' + data.range_km.toFixed(1) + ' km</div>';
    }};

    const OFFLINE_TILE_URL_LIGHT = {json.dumps(tile_url_light)};
    const OFFLINE_TILE_URL_DARK = {json.dumps(tile_url_dark)};
    let _currentOffline = isOffline;
    window.setMapOfflineMode = function(offline, tileUrl) {{
      _currentOffline = offline;
      if (tileLayer) map.removeLayer(tileLayer);
      const opts = offline ? {{ maxZoom: offlineMaxZ, minZoom: offlineMaxZ, attribution: OFFLINE_ATTRIBUTION }}
        : {{ subdomains: 'abcd', maxZoom: 19, attribution: ONLINE_ATTRIBUTION }};
      tileLayer = L.tileLayer(tileUrl, opts).addTo(map);
      if (offline) {{
        map.setMinZoom(offlineMaxZ);
        map.setMaxZoom(offlineMaxZ);
        map.setView(map.getCenter(), offlineMaxZ, {{ animate: false }});
      }} else {{
        map.setMinZoom(3);
        map.setMaxZoom(19);
      }}
    }};

    window.setMapDarkMode = function(dark) {{
      const url = _currentOffline ? (dark ? OFFLINE_TILE_URL_DARK : OFFLINE_TILE_URL_LIGHT) : (dark ? TILE_URL_DARK : TILE_URL_LIGHT);
      const opts = _currentOffline ? {{ maxZoom: offlineMaxZ, minZoom: offlineMaxZ, attribution: OFFLINE_ATTRIBUTION }}
        : {{ subdomains: 'abcd', maxZoom: 19, attribution: ONLINE_ATTRIBUTION }};
      if (tileLayer) map.removeLayer(tileLayer);
      tileLayer = L.tileLayer(url, opts).addTo(map);
      if (_currentOffline) {{
        map.setMinZoom(offlineMaxZ);
        map.setMaxZoom(offlineMaxZ);
        map.setView(map.getCenter(), offlineMaxZ, {{ animate: false }});
      }}
      document.body.style.background = dark ? '#1c1c1c' : 'inherit';
      const info = document.getElementById('info');
      if (info) {{
        info.style.background = dark ? '#2d2d2d' : 'white';
        info.style.color = dark ? '#e1e1e1' : 'inherit';
      }}
      if (locatorVisible) {{
        _mapDark = dark;
        _currentLocatorKey = '';
        _updateLocatorPrecision();
      }}
    }};

    let locatorLayer = null;      // Maidenhead-Gitternetz (Polygone)
    let locatorLabelLayer = null; // Beschriftungen – dynamisch positioniert
    let locatorVisible = false;
    let _mapDark = {str(dark).lower()};
    let _locPrec = 2, _locDispLen = 2;
    // Präzision je nach Zoom
    function _precisionForZoom(z) {{
      if (z < 7) return 2;
      if (z < 12) return 4;
      return 6;
    }}
    function _displayLengthForZoom(z) {{
      if (z < 3) return 1;
      if (z < 7) return 2;
      if (z < 12) return 4;
      return 6;
    }}
    // Zellgröße in Grad für die jeweilige Präzision
    // Werte aus maidenhead.js: latDelta, lngDelta = latDelta * 2
    function _cellSize(prec) {{
      if (prec === 2) return {{lat: 10,       lng: 20}};
      if (prec === 4) return {{lat: 1,        lng: 2}};
      return             {{lat: 2.5/60,   lng: 5/60}};
    }}
    // Nur Gitternetz – Beschriftung übernehmen wir selbst
    function _createLocatorGridLayer(isDark, precision) {{
      return L.maidenhead({{
        precision: precision,
        polygonStyle: {{ color: isDark ? '#b0b0b0' : '#333', weight: 0.5, fill: true, fillColor: 'transparent', fillOpacity: 0 }},
        spawnMarker: function(latlng, prec) {{
          // unsichtbarer Dummy-Marker – Beschriftung wird separat gesetzt
          return L.marker(latlng, {{ opacity: 0, interactive: false, keyboard: false }});
        }}
      }});
    }}
    // Beschriftungen neu berechnen.
    // Zwei Phasen:
    //   Phase 1 – alle sichtbaren Zellmittelpunkte: Label genau am Mittelpunkt
    //   Phase 2 – Zelle des Viewport-Zentrums: Label immer am Viewport-Zentrum,
    //             falls Phase 1 für diese Zelle kein Label erzeugt hat.
    //             Damit ist bei jeder Zoomstufe und jeder Panposition
    //             immer mindestens ein Label sichtbar.
    function _updateLocatorLabels() {{
      if (!locatorLabelLayer || !locatorVisible) return;
      locatorLabelLayer.clearLayers();
      const cs  = _cellSize(_locPrec);
      const b   = map.getBounds();
      const ne  = b.getNorthEast();
      const sw  = b.getSouthWest();
      const mc  = map.getCenter();
      const fg     = _mapDark ? '#e0e0e0' : '#333';
      const shadow = _mapDark ? '0 0 2px #000, 0 0 4px #000, 1px 1px 1px #000'
                              : '0 0 2px #fff, 0 0 4px #fff, 1px 1px 1px #fff';
      const style = "display:inline-block; white-space:nowrap; background:transparent; color:" + fg +
                    "; text-shadow:" + shadow +
                    "; padding:1px 4px; font:bold 20px monospace; line-height:1.2;" +
                    " transform:translate(-50%,-50%); pointer-events:none;";
      function addLabel(lat, lng, cellCenterLat, cellCenterLng) {{
        if (cellCenterLat < -90 || cellCenterLat > 90) return;
        const text = L.Maidenhead.latLngToIndex(cellCenterLat, cellCenterLng, _locPrec)
                       .substring(0, _locDispLen).toUpperCase();
        locatorLabelLayer.addLayer(L.marker([lat, lng], {{
          icon: L.divIcon({{ html: "<div style='" + style + "'>" + text + "</div>",
                             iconSize: [0, 0], iconAnchor: [0, 0] }}),
          interactive: false, keyboard: false
        }}));
      }}
      // Zell-Index des Viewport-Zentrums (für Phase-2-Prüfung)
      const ctrRowIdx = Math.floor(mc.lat / cs.lat);
      const ctrColIdx = Math.floor(mc.lng / cs.lng);
      let centerCellLabeled = false;
      // Phase 1: Labels für alle Zellen, deren Mittelpunkt im Viewport sichtbar ist
      const lat0 = Math.floor(sw.lat / cs.lat) * cs.lat;
      const lng0 = Math.floor(sw.lng / cs.lng) * cs.lng;
      for (let r = lat0; r <= ne.lat + cs.lat; r += cs.lat) {{
        for (let c = lng0; c <= ne.lng + cs.lng; c += cs.lng) {{
          const clat = r + cs.lat / 2;
          const clng = c + cs.lng / 2;
          if (clat <= sw.lat || clat >= ne.lat) continue;
          if (clng <= sw.lng || clng >= ne.lng) continue;
          addLabel(clat, clng, clat, clng);
          const rowIdx = Math.round(r / cs.lat);
          const colIdx = Math.round(c / cs.lng);
          if (rowIdx === ctrRowIdx && colIdx === ctrColIdx) centerCellLabeled = true;
        }}
      }}
      // Phase 2: Viewport-Zentrum-Zelle noch nicht beschriftet?
      // → Label am Viewport-Mittelpunkt (immer sichtbar)
      if (!centerCellLabeled) {{
        const cellS   = ctrRowIdx * cs.lat;
        const cellCLat = cellS + cs.lat / 2;
        const cellCLng = ctrColIdx * cs.lng + cs.lng / 2;
        addLabel(mc.lat, mc.lng, cellCLat, cellCLng);
      }}
    }}
    let _currentLocatorKey = '';
    function _updateLocatorPrecision() {{
      if (!locatorVisible || !map) return;
      const z       = map.getZoom();
      const prec    = _precisionForZoom(z);
      const dispLen = _displayLengthForZoom(z);
      const key     = prec + '-' + dispLen;
      if (key === _currentLocatorKey && locatorLayer) return;
      _currentLocatorKey = key;
      _locPrec    = prec;
      _locDispLen = dispLen;
      if (locatorLayer)      map.removeLayer(locatorLayer);
      if (locatorLabelLayer) map.removeLayer(locatorLabelLayer);
      locatorLayer      = _createLocatorGridLayer(_mapDark, prec);
      locatorLabelLayer = L.layerGroup();
      map.addLayer(locatorLayer);
      map.addLayer(locatorLabelLayer);
      _updateLocatorLabels();
    }}
    let _lastLocatorCheck = 0;
    map.on('zoom', function() {{
      if (!locatorVisible) return;
      const now = Date.now();
      if (now - _lastLocatorCheck < 80) return;
      _lastLocatorCheck = now;
      _updateLocatorPrecision();
    }});
    map.on('zoomend', function() {{
      if (locatorVisible) _updateLocatorPrecision();
    }});
    // Beim Verschieben der Karte nur die Labels neu positionieren (Gitternetz bleibt)
    map.on('moveend', function() {{
      if (locatorVisible) _updateLocatorLabels();
    }});
    window.setMapLocatorOverlay = function(show, darkMode) {{
      if (darkMode !== undefined) _mapDark = darkMode;
      locatorVisible = !!show;
      if (show) {{
        _updateLocatorPrecision();
      }} else {{
        if (locatorLayer)      {{ map.removeLayer(locatorLayer);      locatorLayer      = null; }}
        if (locatorLabelLayer) {{ map.removeLayer(locatorLabelLayer); locatorLabelLayer = null; }}
        _currentLocatorKey = '';
      }}
    }};
    if ({str(locator_overlay).lower()}) {{
      setTimeout(function() {{ if (typeof window.setMapLocatorOverlay === 'function') window.setMapLocatorOverlay(true, {str(dark).lower()}); }}, 100);
    }}

  </script>
</body>
</html>"""


class _MapContainer(QWidget):
    """Container für Karte + Wind-Overlay; positioniert Overlay rechts oben."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._wind_overlay: Optional[MapWindOverlay] = None

    def resizeEvent(self, event):
        super().resizeEvent(event)
        ov = getattr(self, "_wind_overlay", None)
        if ov is not None:
            margin = 12
            w, h = ov.width(), ov.height()
            ov.setGeometry(self.width() - w - margin, margin, w, h)
            ov.raise_()


class MapWindOverlay(QFrame):
    """Kompaktes Wind-Overlay mit PNG-Pfeil wie im Wetterfenster."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._wind_dir_deg: Optional[float] = None
        self._wind_dir_draw_deg: Optional[float] = None
        self._wind_kmh: Optional[float] = None
        self._wind_dir_mode: str = "to"
        self._arrow_pixmap = WindRoseWidget._load_arrow_pixmap()
        self._wind_anim_speed_dps = 280.0
        self._wind_anim_last_ts = time.monotonic()
        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(16)
        self._anim_timer.timeout.connect(self._animate_wind_dir)
        self.setFixedSize(90, 90)
        self._dark_mode = False
        self._apply_theme()
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def set_wind_dir_deg(self, deg: Optional[float]) -> None:
        self._wind_dir_deg = None if deg is None else wrap_deg(deg)
        if self._wind_dir_deg is None:
            self._wind_dir_draw_deg = None
            self._anim_timer.stop()
        else:
            if self._wind_dir_draw_deg is None:
                self._wind_dir_draw_deg = float(self._wind_dir_deg)
            self._wind_anim_last_ts = time.monotonic()
            if not self._anim_timer.isActive():
                self._anim_timer.start()
        self.update()

    def set_wind_kmh(self, kmh: Optional[float]) -> None:
        self._wind_kmh = None if kmh is None else float(kmh)
        self.update()

    def set_wind_dir_mode(self, mode: str) -> None:
        m = str(mode or "").strip().lower()
        self._wind_dir_mode = m if m in ("from", "to") else "to"
        self.update()

    def set_dark_mode(self, dark: bool) -> None:
        if self._dark_mode != dark:
            self._dark_mode = bool(dark)
            self._apply_theme()

    def set_visible(self, on: bool) -> None:
        """Overlay ein-/ausblenden, wenn Windsensor offline."""
        self.setVisible(bool(on))

    def _apply_theme(self) -> None:
        if self._dark_mode:
            self.setStyleSheet("background: #2d2d2d; color: #e1e1e1; border-radius: 8px;")
        else:
            self.setStyleSheet("background: white; border-radius: 8px;")

    def _animate_wind_dir(self) -> None:
        if self._wind_dir_deg is None or self._wind_dir_draw_deg is None:
            self._anim_timer.stop()
            return
        now = time.monotonic()
        dt = max(0.0, min(0.2, now - self._wind_anim_last_ts))
        self._wind_anim_last_ts = now
        delta = shortest_delta_deg(self._wind_dir_draw_deg, self._wind_dir_deg)
        max_step = float(self._wind_anim_speed_dps) * dt
        if abs(delta) <= max(0.4, max_step):
            self._wind_dir_draw_deg = float(self._wind_dir_deg)
            self._anim_timer.stop()
        else:
            step = max(-max_step, min(max_step, delta * 0.30))
            self._wind_dir_draw_deg = wrap_deg(float(self._wind_dir_draw_deg) + step)
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        rect = self.rect().adjusted(4, 4, -4, -4)
        cx = float(rect.center().x())
        cy = float(rect.center().y())
        r = float(min(rect.width(), rect.height())) / 2.0 * 0.85
        if self._wind_dir_draw_deg is not None:
            wd = wrap_deg(float(self._wind_dir_draw_deg) + 180.0) if self._wind_dir_mode == "to" else float(self._wind_dir_draw_deg)
            if not self._arrow_pixmap.isNull():
                side = int(max(24.0, min(r * 1.6, 56.0)))
                scaled = self._arrow_pixmap.scaled(side, side, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                w, h = float(scaled.width()), float(scaled.height())
                p.save()
                p.translate(cx, cy)
                p.rotate(float(wd))
                p.drawPixmap(int(-w / 2.0), int(-h / 2.0), scaled)
                p.restore()
            else:
                p.setPen(QPen(self.palette().color(QPalette.ColorRole.WindowText), 2))
                rad = math.radians(wd)
                x2 = cx + math.sin(rad) * r
                y2 = cy - math.cos(rad) * r
                p.drawLine(int(cx), int(cy), int(x2), int(y2))
        p.setPen(QPen(QColor(225, 225, 225) if self._dark_mode else Qt.GlobalColor.black))
        f = self.font()
        f.setPointSize(f.pointSize() + 1)
        p.setFont(f)
        txt = "--.- km/h"
        if self._wind_kmh is not None:
            txt = f"{self._wind_kmh:.1f} km/h"
        p.drawText(rect, Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter, txt)


class MapWebPage(QWebEnginePage):
    """WebEnginePage die rotorapp://-Navigations abfängt."""

    def __init__(self, on_click_cb, parent=None):
        super().__init__(parent)
        self._on_click_cb = on_click_cb

    def acceptNavigationRequest(self, url: QUrl, nav_type, is_main_frame: bool) -> bool:
        u = url.toString()
        if u.startswith("rotorapp://setaz?"):
            try:
                parsed = urlparse(u)
                qs = parse_qs(parsed.query or "")
                lat = float(qs.get("lat", [0])[0])
                lon = float(qs.get("lon", [0])[0])
                if self._on_click_cb:
                    self._on_click_cb(lat, lon)
            except Exception:
                pass
            return False
        return super().acceptNavigationRequest(url, nav_type, is_main_frame)


class MapWindow(QDialog):
    """Fenster mit Leaflet-Karte, Antennen-Beam und Klick-zu-Rotor."""

    def __init__(self, cfg: dict, controller, save_cfg_cb=None, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.ctrl = controller
        self.save_cfg_cb = save_cfg_cb
        self.setWindowTitle(t("map.title"))
        self.setWindowFlag(Qt.WindowType.WindowMinimizeButtonHint, True)
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        self.setWindowIcon(get_app_icon())
        self.resize(900, 700)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)

        # Toolbar: Antenne, Favoriten, Name, Speichern, Löschen (wie Kompassfenster)
        toolbar = QHBoxLayout()
        antenna_idx = max(0, min(2, int(self.cfg.get("ui", {}).get("compass_antenna", 0))))
        self._cb_antenna = QComboBox()
        self._cb_antenna.addItems(self._get_antenna_dropdown_items())
        self._cb_antenna.setMinimumWidth(160)
        self._cb_antenna.setCurrentIndex(antenna_idx)
        self._cb_antenna.currentIndexChanged.connect(self._on_antenna_changed)

        self._cb_fav = QComboBox()
        self._cb_fav.setMinimumWidth(180)
        self._cb_fav.setEditable(False)
        self._ed_fav_name = QLineEdit()
        self._ed_fav_name.setPlaceholderText(t("compass.fav_name_placeholder"))
        self._ed_fav_name.setMaxLength(15)
        self._ed_fav_name.setFixedWidth(110)
        self._btn_fav_save = QPushButton(t("compass.fav_btn_save"))
        self._btn_fav_save.setAutoDefault(False)
        self._btn_fav_save.setDefault(False)
        self._btn_fav_delete = QPushButton(t("compass.fav_btn_delete"))
        self._btn_fav_delete.setAutoDefault(False)
        self._btn_fav_delete.setDefault(False)
        self._btn_elevation = QPushButton("Höhenprofil")
        self._btn_elevation.setAutoDefault(False)
        self._btn_elevation.setDefault(False)
        self._btn_elevation.setEnabled(False)
        self._btn_elevation.setToolTip(
            "Höhenprofil zwischen Antenne und Ziel anzeigen\n"
            "(Ziel durch Klick auf die Karte festlegen)"
        )

        toolbar.addWidget(self._cb_antenna)
        toolbar.addWidget(self._cb_fav)
        toolbar.addWidget(self._ed_fav_name)
        toolbar.addWidget(self._btn_fav_save)
        toolbar.addWidget(self._btn_fav_delete)
        toolbar.addWidget(self._btn_elevation)
        layout.addLayout(toolbar)

        self._cb_fav.activated.connect(self._on_fav_activated)
        self._btn_fav_save.clicked.connect(self._on_fav_save)
        self._btn_fav_delete.clicked.connect(self._on_fav_delete)
        self._btn_elevation.clicked.connect(self._on_elevation_profile)
        self._refresh_favorites_dropdown()

        map_container = _MapContainer(self)
        map_layout = QVBoxLayout(map_container)
        map_layout.setContentsMargins(0, 0, 0, 0)
        self._view = QWebEngineView(map_container)
        self._page = MapWebPage(self._on_map_click, self._view)
        self._view.setPage(self._page)
        map_layout.addWidget(self._view, 1)
        self._wind_overlay = MapWindOverlay(map_container)
        map_container._wind_overlay = self._wind_overlay
        layout.addWidget(map_container, 1)

        # Statusleiste unter der Karte: Ist, Soll, LEDs (Fährt, Online, Ref), Ref AZ
        status_bar = QHBoxLayout()
        status_bar.setContentsMargins(0, 6, 0, 0)
        led_d = px_to_dip(self, 13)
        lbl_style = "font-size: 12pt; font-weight: bold;"
        self._lbl_ist = QLabel(t("compass.ist_prefix") + "–")
        self._lbl_ist.setMinimumWidth(95)
        self._lbl_ist.setStyleSheet(lbl_style)
        self._lbl_soll = QLabel(t("compass.soll_label"))
        self._lbl_soll.setStyleSheet(lbl_style)
        self._lbl_soll_value = QLabel("–")
        self._lbl_soll_value.setStyleSheet(lbl_style)
        self._lbl_soll_value.setMinimumWidth(55)
        self._led_moving = Led(led_d, self)
        self._lbl_moving = QLabel(t("axis.moving_label"))
        self._lbl_moving.setStyleSheet(lbl_style)
        self._led_online = Led(led_d, self)
        self._lbl_online = QLabel(t("axis.online_label"))
        self._lbl_online.setStyleSheet(lbl_style)
        self._led_ref = Led(led_d, self)
        self._lbl_ref = QLabel(t("axis.ref_label"))
        self._lbl_ref.setStyleSheet(lbl_style)
        self._lbl_temp_motor = QLabel("–")
        self._lbl_temp_motor.setStyleSheet(lbl_style)
        self._lbl_temp_ambient = QLabel("–")
        self._lbl_temp_ambient.setStyleSheet(lbl_style)
        self._chk_offline = QCheckBox(t("map.chk_offline"))
        self._chk_offline.setChecked(bool(self.cfg.get("ui", {}).get("map_offline", False)))
        self._chk_offline.stateChanged.connect(self._on_offline_changed)
        self._chk_locator = QCheckBox(t("map.chk_locator"))
        self._chk_locator.setChecked(bool(self.cfg.get("ui", {}).get("map_locator_overlay", False)))
        self._chk_locator.stateChanged.connect(self._on_locator_changed)
        self._btn_ref_az = QPushButton(t("compass.btn_ref_az"))
        self._btn_ref_az.setAutoDefault(False)
        self._btn_ref_az.setDefault(False)
        self._btn_ref_az.clicked.connect(self._on_ref_az)

        status_bar.addWidget(self._lbl_ist)
        status_bar.addWidget(self._lbl_soll)
        status_bar.addWidget(self._lbl_soll_value)
        status_bar.addStretch(1)
        status_bar.addWidget(self._led_moving)
        status_bar.addWidget(self._lbl_moving)
        status_bar.addWidget(self._led_online)
        status_bar.addWidget(self._lbl_online)
        status_bar.addWidget(self._led_ref)
        status_bar.addWidget(self._lbl_ref)
        status_bar.addSpacing(px_to_dip(self, 20))
        status_bar.addWidget(self._lbl_temp_motor)
        status_bar.addWidget(self._lbl_temp_ambient)
        status_bar.addStretch(1)
        status_bar.addWidget(self._chk_offline)
        status_bar.addWidget(self._chk_locator)
        status_bar.addWidget(self._btn_ref_az)
        layout.addLayout(status_bar)

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(50)
        self._refresh_timer.timeout.connect(self._refresh_map)
        self._map_loaded = False
        self._map_dark_mode: Optional[bool] = None
        self._map_offline: Optional[bool] = None
        self._map_locator_overlay: Optional[bool] = None
        self._smooth_azimuth: Optional[float] = None
        self._SMOOTH_FACTOR = 0.25
        # Zuletzt per Kartenklick gewähltes Ziel (für Höhenprofil)
        self._target_lat: Optional[float] = None
        self._target_lon: Optional[float] = None
        # Referenz auf offenes Höhenprofil-Fenster (None = nicht offen)
        self._elevation_win: Optional[ElevationProfileWindow] = None

    def _get_params(self) -> dict:
        """Aktuelle Parameter für die Karte."""
        ui = self.cfg.get("ui", {})
        lat = float(ui.get("location_lat", 49.502651))
        lon = float(ui.get("location_lon", 8.375019))
        antenna_idx = max(0, min(2, int(ui.get("compass_antenna", 0))))
        slot = antenna_idx + 1
        offs = ui.get("antenna_offsets_az", [0.0, 0.0, 0.0])
        angles = ui.get("antenna_angles_az", [0.0, 0.0, 0.0])
        offset = float(offs[slot - 1]) if slot <= len(offs) else 0.0
        opening = float(angles[slot - 1]) if slot <= len(angles) else 0.0
        if opening <= 0:
            opening = 30.0
        ranges = ui.get("antenna_ranges_az", [100.0, 100.0, 100.0])
        range_km = float(ranges[slot - 1]) if slot <= len(ranges) else 100.0
        if range_km <= 0:
            range_km = 100.0
        range_km = min(4000.0, range_km)

        az_axis = getattr(self.ctrl, "az", None)
        az_pos = getattr(az_axis, "pos_d10", None) if az_axis else None
        if az_pos is not None:
            rotor_az = az_pos / 10.0
            azimuth = wrap_deg(rotor_az + offset)
        else:
            azimuth = 0.0

        polygon = beam_polygon_points(lat, lon, azimuth, opening, range_km)
        center_line = beam_center_line_points(lat, lon, azimuth, range_km)

        return {
            "lat": lat,
            "lon": lon,
            "azimuth": azimuth,
            "opening": opening,
            "range_km": range_km,
            "polygon": [[p[0], p[1]] for p in polygon],
            "center_line": [[p[0], p[1]] for p in center_line],
            "location_str": f"{lat:.5f}, {lon:.5f}",
            "info_standort": t("map.info_standort"),
            "info_offnung": t("map.info_offnung"),
            "info_reichweite": t("map.info_reichweite"),
            "dark_mode": bool(self.cfg.get("ui", {}).get("force_dark_mode", False)),
            "offline": bool(self.cfg.get("ui", {}).get("map_offline", False)),
            "map_locator_overlay": bool(self.cfg.get("ui", {}).get("map_locator_overlay", False)),
        }

    def _get_antenna_offset_az(self) -> float:
        """Versatz der gewählten Antenne (wie Compass)."""
        ui = self.cfg.get("ui", {})
        antenna_idx = max(0, min(2, int(ui.get("compass_antenna", 0))))
        slot = antenna_idx + 1
        az_axis = getattr(self.ctrl, "az", None)
        v = getattr(az_axis, f"antoff{slot}", None) if az_axis else None
        if v is not None:
            return float(v)
        offs = ui.get("antenna_offsets_az", [0.0, 0.0, 0.0])
        try:
            return float(offs[slot - 1])
        except (IndexError, TypeError, ValueError):
            return 0.0

    def _get_antenna_dropdown_items(self) -> list[str]:
        """Antennen-Namen mit Versatz in Klammern (wie Kompass)."""
        names = list(self.cfg.get("ui", {}).get("antenna_names", ["Antenne 1", "Antenne 2", "Antenne 3"]))
        while len(names) < 3:
            names.append(f"Antenne {len(names)+1}")
        az_axis = getattr(self.ctrl, "az", None)
        offsets: list[float] = []
        for slot in (1, 2, 3):
            v = getattr(az_axis, f"antoff{slot}", None) if az_axis else None
            if v is not None:
                offsets.append(float(v))
            else:
                offs = self.cfg.get("ui", {}).get("antenna_offsets_az", [0.0, 0.0, 0.0])
                try:
                    offsets.append(float(offs[slot - 1]))
                except (IndexError, TypeError, ValueError):
                    offsets.append(0.0)
        return [f"{names[i]} ({offsets[i]:.1f}°)" for i in range(3)]

    def _get_favorites(self) -> list[dict]:
        """Gespeicherte Favoriten aus Config (wie Kompass)."""
        items = self.cfg.get("ui", {}).get("compass_favorites", [])
        if not isinstance(items, list):
            return []
        out: list[dict] = []
        for it in items:
            if isinstance(it, dict) and "name" in it:
                try:
                    out.append({
                        "name": str(it["name"])[:15],
                        "az": float(it.get("az", 0.0)),
                        "el": clamp_el(float(it.get("el", 0.0))),
                    })
                except (TypeError, ValueError):
                    pass
        return out

    def _refresh_favorites_dropdown(self) -> None:
        """Favoriten-Dropdown füllen (wie Kompass)."""
        favs = self._get_favorites()
        favs = sorted(favs, key=lambda f: (
            0 if f["name"] and f["name"][0].isdigit() else 1,
            f["name"].lower(),
        ))
        self._cb_fav.blockSignals(True)
        self._cb_fav.clear()
        if not favs:
            self._cb_fav.addItem(t("compass.fav_dropdown_placeholder"), None)
        else:
            for f in favs:
                self._cb_fav.addItem(f"{f['name']} ({f['az']:.1f}°, {f['el']:.1f}°)", f)
        self._cb_fav.blockSignals(False)

    def _refresh_antenna_dropdown(self) -> None:
        """Antennen-Dropdown mit aktuellen Werten aktualisieren."""
        idx = max(0, min(2, self._cb_antenna.currentIndex()))
        self._cb_antenna.blockSignals(True)
        self._cb_antenna.clear()
        self._cb_antenna.addItems(self._get_antenna_dropdown_items())
        self._cb_antenna.setCurrentIndex(idx)
        self._cb_antenna.blockSignals(False)

    def _on_antenna_changed(self) -> None:
        """Antenne gewechselt → Config speichern, Karte aktualisiert sich über cfg."""
        idx = max(0, min(2, self._cb_antenna.currentIndex()))
        if "ui" not in self.cfg:
            self.cfg["ui"] = {}
        self.cfg["ui"]["compass_antenna"] = idx
        try:
            if self.save_cfg_cb:
                self.save_cfg_cb(self.cfg)
        except Exception:
            pass

    def _on_fav_activated(self, idx: int) -> None:
        """Favorit ausgewählt → dorthin fahren."""
        if idx < 0:
            return
        data = self._cb_fav.itemData(idx)
        if not isinstance(data, dict) or "az" not in data or "el" not in data:
            return
        rotor_az = wrap_deg(float(data["az"]))
        rotor_el = clamp_el(float(data["el"]))
        try:
            self.ctrl.set_az_deg(rotor_az, force=True)
            if hasattr(self.ctrl, "set_el_deg"):
                self.ctrl.set_el_deg(rotor_el, force=True)
        except Exception:
            pass
        self._refresh_map()

    def _on_fav_save(self) -> None:
        """Aktuelle Position unter dem eingegebenen Namen speichern."""
        name = str(self._ed_fav_name.text()).strip()
        if not name:
            return
        name = name[:15]
        try:
            az_d10 = getattr(self.ctrl.az, "pos_d10", None)
        except Exception:
            return
        if az_d10 is None:
            return
        az_deg = float(az_d10) / 10.0
        try:
            el_d10 = getattr(self.ctrl.el, "pos_d10", None) if hasattr(self.ctrl, "el") else None
        except Exception:
            el_d10 = None
        el_deg = clamp_el(float(el_d10 or 0) / 10.0)
        fav = {"name": name[:15], "az": wrap_deg(az_deg), "el": el_deg}
        if "ui" not in self.cfg:
            self.cfg["ui"] = {}
        favs = self._get_favorites()
        favs.append(fav)
        self.cfg["ui"]["compass_favorites"] = favs
        try:
            if self.save_cfg_cb:
                self.save_cfg_cb(self.cfg)
        except Exception:
            pass
        self._refresh_favorites_dropdown()
        self._ed_fav_name.clear()

    def _on_fav_delete(self) -> None:
        """Ausgewählten Favoriten löschen."""
        idx = self._cb_fav.currentIndex()
        if idx < 0:
            return
        data = self._cb_fav.itemData(idx)
        if data is None:
            return
        sel_name = data.get("name")
        sel_az = data.get("az")
        sel_el = data.get("el")
        favs = self._get_favorites()
        favs = [f for f in favs if not (
            f.get("name") == sel_name
            and abs(float(f.get("az", 0) or 0) - float(sel_az or 0)) < 0.01
            and abs(float(f.get("el", 0) or 0) - float(sel_el or 0)) < 0.01
        )]
        if "ui" not in self.cfg:
            self.cfg["ui"] = {}
        self.cfg["ui"]["compass_favorites"] = favs
        try:
            if self.save_cfg_cb:
                self.save_cfg_cb(self.cfg)
        except Exception:
            pass
        self._refresh_favorites_dropdown()

    def _on_offline_changed(self) -> None:
        """Offline-Karte ein/aus → Config speichern, Karte aktualisiert sich über cfg."""
        on = bool(self._chk_offline.isChecked())
        if "ui" not in self.cfg:
            self.cfg["ui"] = {}
        self.cfg["ui"]["map_offline"] = on
        try:
            if self.save_cfg_cb:
                self.save_cfg_cb(self.cfg)
        except Exception:
            pass
        self._refresh_map()

    def _on_locator_changed(self) -> None:
        """Locator-Overlay ein/aus → Config speichern, Karte aktualisieren."""
        on = bool(self._chk_locator.isChecked())
        if "ui" not in self.cfg:
            self.cfg["ui"] = {}
        self.cfg["ui"]["map_locator_overlay"] = on
        try:
            if self.save_cfg_cb:
                self.save_cfg_cb(self.cfg)
        except Exception:
            pass
        self._refresh_map()

    def _on_ref_az(self) -> None:
        """Referenz AZ auslösen."""
        try:
            self.ctrl.reference_az(True)
        except Exception:
            pass

    def _update_wind_overlay(self) -> None:
        """Wind-Overlay aus Telemetrie aktualisieren."""
        dark = bool(self.cfg.get("ui", {}).get("force_dark_mode", False))
        self._wind_overlay.set_dark_mode(dark)
        wind_on = bool(getattr(self.ctrl, "wind_enabled", False)) if getattr(self.ctrl, "wind_enabled_known", False) else False
        if not wind_on and hasattr(self.ctrl, "az"):
            tel = getattr(self.ctrl.az, "telemetry", None)
            if tel and (getattr(tel, "wind_kmh", None) is not None or getattr(tel, "wind_dir_deg", None) is not None):
                wind_on = True
        mode = str(self.cfg.get("ui", {}).get("wind_dir_display", "to") or "to").strip().lower()
        if mode not in ("from", "to"):
            mode = "to"
        self._wind_overlay.set_wind_dir_mode(mode)
        self._wind_overlay.set_visible(wind_on)
        if not wind_on:
            self._wind_overlay.set_wind_dir_deg(0.0)
            self._wind_overlay.set_wind_kmh(None)
            return
        try:
            tel = getattr(self.ctrl.az, "telemetry", None)
            wdir = getattr(tel, "wind_dir_deg", None) if tel else None
            wkmh = getattr(tel, "wind_kmh", None) if tel else None
            self._wind_overlay.set_wind_dir_deg(wdir)
            self._wind_overlay.set_wind_kmh(wkmh)
        except Exception:
            self._wind_overlay.set_wind_dir_deg(0.0)
            self._wind_overlay.set_wind_kmh(None)

    def _update_status_bar(self) -> None:
        """IST, SOLL und LEDs aus Controller aktualisieren."""
        off = self._get_antenna_offset_az()
        az_axis = getattr(self.ctrl, "az", None)
        if az_axis is None:
            self._lbl_ist.setText(t("compass.ist_prefix") + "–")
            self._lbl_soll_value.setText("–")
            self._led_moving.set_state(False)
            self._led_online.set_state(False)
            self._led_ref.set_state(False)
            self._lbl_temp_motor.setText(f"{t('weather.temp_motor_label')}: –")
            self._lbl_temp_ambient.setText(f"{t('weather.temp_ambient_label')}: –")
            return
        try:
            pos_d10 = getattr(az_axis, "pos_d10", None)
            cur = wrap_deg(float(pos_d10) / 10.0 + off) if pos_d10 is not None else None
        except Exception:
            cur = None
        try:
            tgt_d10 = getattr(az_axis, "target_d10", None)
            tgt = wrap_deg(float(tgt_d10) / 10.0 + off) if tgt_d10 is not None else None
        except Exception:
            tgt = None
        # Bei unbekanntem Ziel (z.B. erste Öffnung): Soll = Ist
        unknown_target = (
            (tgt_d10 is None)
            or (
                int(tgt_d10 or 0) == 0
                and float(getattr(az_axis, "last_set_sent_ts", 0.0) or 0.0) <= 0.0
                and getattr(az_axis, "last_set_sent_target_d10", None) is None
            )
        )
        if cur is not None and unknown_target:
            tgt = cur
        self._lbl_ist.setText(t("compass.ist_prefix") + (fmt_deg(cur) if cur is not None else "–"))
        self._lbl_soll_value.setText(fmt_deg(tgt) if tgt is not None else "–")
        self._led_moving.set_state(bool(getattr(az_axis, "moving", False)))
        self._led_online.set_state(bool(getattr(az_axis, "online", False)))
        self._led_ref.set_state(bool(getattr(az_axis, "referenced", False)))
        try:
            tel = getattr(az_axis, "telemetry", None)
            ta = getattr(tel, "temp_ambient_c", None) if tel else None
            tm = getattr(tel, "temp_motor_c", None) if tel else None
            self._lbl_temp_motor.setText(f"{t('weather.temp_motor_label')}: {float(tm):.1f} °C" if tm is not None else f"{t('weather.temp_motor_label')}: –")
            self._lbl_temp_ambient.setText(f"{t('weather.temp_ambient_label')}: {float(ta):.1f} °C" if ta is not None else f"{t('weather.temp_ambient_label')}: –")
        except Exception:
            self._lbl_temp_motor.setText(f"{t('weather.temp_motor_label')}: –")
            self._lbl_temp_ambient.setText(f"{t('weather.temp_ambient_label')}: –")

    def _on_map_click(self, lat: float, lon: float) -> None:
        """Klick auf Karte: Rotor auf Peilung zu diesem Punkt drehen."""
        self._target_lat = lat
        self._target_lon = lon
        self._btn_elevation.setEnabled(True)

        # Offenes Höhenprofil-Fenster sofort aktualisieren
        if self._elevation_win is not None and self._elevation_win.isVisible():
            ui = self.cfg.get("ui", {})
            home_lat = float(ui.get("location_lat", 49.502651))
            home_lon = float(ui.get("location_lon", 8.375019))
            self._elevation_win.update_target(home_lat, home_lon, lat, lon)

        ui = self.cfg.get("ui", {})
        lat0 = float(ui.get("location_lat", 49.502651))
        lon0 = float(ui.get("location_lon", 8.375019))
        bearing = bearing_deg(lat0, lon0, lat, lon)
        off = self._get_antenna_offset_az()
        rotor_deg = wrap_deg(bearing - off)
        try:
            self.ctrl.set_az_deg(rotor_deg, force=True)
        except Exception:
            pass
        self._refresh_map()

    def _on_elevation_profile(self) -> None:
        """Höhenprofil-Fenster zwischen Heimstation und gewähltem Ziel öffnen."""
        if self._target_lat is None or self._target_lon is None:
            return

        # Bereits offenes Fenster in den Vordergrund holen
        if self._elevation_win is not None and self._elevation_win.isVisible():
            self._elevation_win.raise_()
            self._elevation_win.activateWindow()
            return

        ui = self.cfg.get("ui", {})
        home_lat       = float(ui.get("location_lat", 49.502651))
        home_lon       = float(ui.get("location_lon", 8.375019))
        dark           = bool(ui.get("force_dark_mode", False))
        antenna_height = float(ui.get("antenna_height_m", 0.0))
        freq_mhz       = float(ui.get("rf_freq_mhz", 145.0))

        # parent=None → unabhängiges Top-Level-Fenster
        self._elevation_win = ElevationProfileWindow(
            home_lat=home_lat,
            home_lon=home_lon,
            target_lat=self._target_lat,
            target_lon=self._target_lon,
            home_name="Antenne",
            target_name="Ziel",
            antenna_height_m=antenna_height,
            freq_mhz=freq_mhz,
            dark=dark,
            parent=None,
        )
        self._elevation_win.show()

    def _refresh_map(self) -> None:
        """Beam-Daten aktualisieren ohne Karten-Zoom/Zentrum zu ändern.
        Azimuth wird geglättet für flüssige Bewegung ohne Ruckeln."""
        params = self._get_params()
        # dark_mode immer direkt aus Config (force_dark_mode) – auch für Offline-Tiles
        dark = bool(self.cfg.get("ui", {}).get("force_dark_mode", False))
        params["dark_mode"] = dark
        target_az = params["azimuth"]
        if self._smooth_azimuth is None:
            self._smooth_azimuth = target_az
        else:
            delta = shortest_delta_deg(self._smooth_azimuth, target_az)
            self._smooth_azimuth = wrap_deg(self._smooth_azimuth + delta * self._SMOOTH_FACTOR)
        params["azimuth"] = self._smooth_azimuth
        lat = params["lat"]
        lon = params["lon"]
        opening = params["opening"]
        range_km = params["range_km"]
        az = float(self._smooth_azimuth or 0.0)
        polygon = beam_polygon_points(lat, lon, az, opening, range_km)
        center_line = beam_center_line_points(lat, lon, az, range_km)
        params["polygon"] = [[p[0], p[1]] for p in polygon]
        params["center_line"] = [[p[0], p[1]] for p in center_line]
        if self._map_loaded:
            data = {
                "lat": params["lat"],
                "lon": params["lon"],
                "azimuth": params["azimuth"],
                "opening": params["opening"],
                "range_km": params["range_km"],
                "polygon": params["polygon"],
                "centerLine": params["center_line"],
                "location_str": params["location_str"],
                "info_standort": params["info_standort"],
                "info_offnung": params["info_offnung"],
                "info_reichweite": params["info_reichweite"],
            }
            js = f"if (typeof window.updateBeam === 'function') window.updateBeam({json.dumps(data)});"
            self._view.page().runJavaScript(js)
        else:
            html = _build_map_html(params, dark=dark)
        offline = params.get("offline", False)
        if self._map_loaded:
            if self._map_offline is not None and self._map_offline != offline:
                if offline:
                    tile_url_off = _offline_tile_url(dark) or ("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png" if dark else "https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png")
                    js_off = f"if (typeof window.setMapOfflineMode === 'function') window.setMapOfflineMode(true, {json.dumps(tile_url_off)});"
                else:
                    tile_url_on = "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png" if dark else "https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png"
                    js_off = f"if (typeof window.setMapOfflineMode === 'function') window.setMapOfflineMode(false, {json.dumps(tile_url_on)});"
                self._view.page().runJavaScript(js_off)
            elif self._map_dark_mode is not None and self._map_dark_mode != dark:
                js_dark = f"if (typeof window.setMapDarkMode === 'function') window.setMapDarkMode({json.dumps(dark)});"
                self._view.page().runJavaScript(js_dark)
            if self._map_locator_overlay is not None and self._map_locator_overlay != params.get("map_locator_overlay", False):
                loc_on = bool(params.get("map_locator_overlay", False))
                js_loc = f"if (typeof window.setMapLocatorOverlay === 'function') window.setMapLocatorOverlay({json.dumps(loc_on)}, {json.dumps(dark)});"
                self._view.page().runJavaScript(js_loc)
        self._map_dark_mode = dark
        self._map_offline = offline
        self._map_locator_overlay = params.get("map_locator_overlay", False)
        self._update_status_bar()
        self._update_wind_overlay()
        if not self._map_loaded:
            tile_url = _offline_tile_url(params.get("dark_mode", False)) if offline else ""
            if offline and tile_url.startswith("http://"):
                # HTTP-Server funktioniert: setHtml mit about:blank (Tiles von 127.0.0.1)
                if _DEBUG_TILES:
                    print(f"[Map] setHtml (HTTP-Tiles) offline=True tileUrl={tile_url[:50]}...")
                self._view.setHtml(html, QUrl("about:blank"))
            elif offline and tile_url.startswith("rotortiles:"):
                # Kein HTTP: load von rotortiles:map/
                global _pending_map_html
                _pending_map_html = html
                if _DEBUG_TILES:
                    print(f"[Map] load rotortiles:map/ tileUrl={tile_url}")
                self._view.load(QUrl("rotortiles:map/"))
            else:
                self._view.setHtml(html, QUrl("about:blank"))
            self._map_loaded = True

    def showEvent(self, event):
        super().showEvent(event)
        self._refresh_antenna_dropdown()
        self._refresh_favorites_dropdown()
        self._map_loaded = False
        self._map_dark_mode = None
        self._map_offline = None
        self._map_locator_overlay = None
        self._smooth_azimuth = None
        self._refresh_map()
        self._refresh_timer.start()
        QTimer.singleShot(100, self._reposition_wind_overlay)

    def _reposition_wind_overlay(self) -> None:
        """Wind-Overlay nach Layout-Berechnung positionieren."""
        cont = self._wind_overlay.parent()
        if isinstance(cont, QWidget) and hasattr(cont, "_wind_overlay"):
            ov = getattr(cont, "_wind_overlay", None)
            if ov is not None:
                margin = 12
                w, h = ov.width(), ov.height()
                ov.setGeometry(cont.width() - w - margin, margin, w, h)
                ov.raise_()

    def reload_for_settings_change(self) -> None:
        """Karte vollständig neu laden (z.B. nach Änderung Dark Mode/Offline in Einstellungen).
        Erzwingt neues HTML mit aktuellem cfg – setMapDarkMode per JS reicht in Offline nicht zuverlässig."""
        if not self._view or not self._view.page():
            return
        self._map_loaded = False
        self._map_dark_mode = None
        self._map_offline = None
        self._map_locator_overlay = None
        self._refresh_map()
        QApplication.processEvents()

    def hideEvent(self, event):
        self._refresh_timer.stop()
        super().hideEvent(event)
