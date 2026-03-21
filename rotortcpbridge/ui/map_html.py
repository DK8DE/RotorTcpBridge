"""Leaflet-HTML für die Antennenkarte (Offline/Online, Beams, Maidenhead-Overlay)."""

from __future__ import annotations

import base64
import json
from pathlib import Path

from .map_tiles import (
    _DEBUG_TILES,
    _offline_tile_url,
    _offline_zoom_range,
    _static_lib_path,
)


def build_map_html(params: dict, dark: bool | None = None) -> str:
    """Erstellt die vollständige HTML-Seite mit Leaflet.
    Enthält window.updateBeam(data) zum Aktualisieren ohne Zoom/Zentrum zu ändern.
    dark: expliziter Wert (hat Vorrang vor params['dark_mode'])."""
    lat = params["lat"]
    lon = params["lon"]
    opening = params["opening"]
    range_km = params["range_km"]
    beams_json = json.dumps(params.get("beams", []))
    grayline = params.get("grayline", [])
    # Am Antimeridian (±180°) aufteilen, damit Leaflet die Kurve korrekt zeichnet
    grayline_segments: list[list[list[float]]] = []
    if len(grayline) >= 2:
        seg: list[list[float]] = [[grayline[0][0], grayline[0][1]]]
        for i in range(1, len(grayline)):
            lon_prev, lon_curr = grayline[i - 1][1], grayline[i][1]
            if abs(lon_curr - lon_prev) > 180:
                if len(seg) >= 2:
                    grayline_segments.append(seg)
                seg = []
            seg.append([grayline[i][0], grayline[i][1]])
        if len(seg) >= 2:
            grayline_segments.append(seg)
    grayline_json = json.dumps(grayline_segments)
    if dark is None:
        dark = bool(params.get("dark_mode", False))
    else:
        dark = bool(dark)
    grayline_color = "#b8b8b8" if dark else "#505050"
    horizon_color = "#7eb87e" if dark else "#2e7d32"
    loc_str = params["location_str"]
    info_standort = params.get("info_standort", "Standort")
    popup_antenna = params.get("popup_antenna", "Antennenstandort")
    popup_target = params.get("popup_target", "Ziel")
    info_offnung = params.get("info_offnung", "Öffnungswinkel")
    info_reichweite = params.get("info_reichweite", "Reichweite")
    offline = bool(params.get("offline", False))
    locator_overlay = bool(params.get("map_locator_overlay", False))
    offline_min_z, offline_max_z = _offline_zoom_range(dark)
    if offline:
        tile_url = _offline_tile_url(dark) or (
            "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"
            if dark
            else "https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png"
        )
        tile_url_light = (
            _offline_tile_url(False)
            or "https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png"
        )
        tile_url_dark = (
            _offline_tile_url(True)
            or "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"
        )
        if _DEBUG_TILES:
            print(
                f"[BuildHTML] dark={dark} tile={tile_url[:60]} light={tile_url_light[:60]} dark={tile_url_dark[:60]}"
            )
    elif dark:
        tile_url = "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"
        tile_url_light = tile_url_dark = tile_url
    else:
        tile_url = "https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png"
        tile_url_light = tile_url_dark = tile_url
    info_bg = "#2d2d2d" if dark else "white"
    info_color = "#e1e1e1" if dark else "inherit"
    body_bg = "#1c1c1c" if dark else "inherit"

    _pkg_root = Path(__file__).resolve().parent.parent
    antenna_path = _pkg_root / "Antenne.png"
    antenna_data_url = ""
    antenna_target_data_url = ""
    try:
        data = antenna_path.read_bytes()
        antenna_data_url = "data:image/png;base64," + base64.b64encode(data).decode("ascii")
    except OSError:
        pass
    antenna_target_path = _pkg_root / "Antenne_T.png"
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
    const beamsInitial = {beams_json};
    const graylineCoords = {grayline_json};
    const graylineColor = {json.dumps(grayline_color)};
    const horizonDistKm = {params.get("horizon_dist_km", 0.0)};
    const horizonColor = {json.dumps(horizon_color)};
    const popupAntenna = {json.dumps(popup_antenna)};
    const popupTarget = {json.dumps(popup_target)};

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
    tileLayer.on('tileerror', function(e) {{
      if (!_currentOffline) console.error('ROTOR_TILEERROR');
    }});

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
    let graylineLayer = null;
    if (graylineCoords && graylineCoords.length > 0) {{
      const segments = graylineCoords.filter(function(s) {{ return s && s.length >= 2; }});
      if (segments.length > 0) {{
        graylineLayer = L.polyline(segments, {{
          color: graylineColor, weight: 2, dashArray: '10, 8',
          interactive: false
        }}).addTo(map);
      }}
    }}

    let marker = L.marker([lat, lon], antennaIcon ? {{ icon: antennaIcon }} : {{}}).addTo(map);
    marker.bindPopup(popupAntenna).openPopup();

    let horizonCircle = null;
    if (horizonDistKm > 0.5) {{
      horizonCircle = L.circle([lat, lon], {{
        radius: horizonDistKm * 1000,
        color: horizonColor,
        weight: 2,
        dashArray: '8, 8',
        fill: false,
        fillOpacity: 0,
        interactive: false
      }}).addTo(map);
    }}

    let beamPolys = [];
    let beamDashLines = [];
    function clearBeamLayers() {{
      beamPolys.forEach(function(p) {{ map.removeLayer(p); }});
      beamDashLines.forEach(function(d) {{ map.removeLayer(d); }});
      beamPolys = [];
      beamDashLines = [];
    }}
    function drawBeamLayers(beamList) {{
      clearBeamLayers();
      if (!beamList || !beamList.length) return;
      beamList.forEach(function(b) {{
        const poly = L.polygon(b.polygon, {{
          color: b.stroke, fillColor: b.fill, fillOpacity: 0.35, weight: 2, interactive: false
        }}).addTo(map);
        const dash = L.polyline(b.centerLine, {{
          color: b.stroke, weight: 2, dashArray: '8, 8', interactive: false
        }}).addTo(map);
        beamPolys.push(poly);
        beamDashLines.push(dash);
      }});
    }}
    drawBeamLayers(beamsInitial);

    const allLayerItems = [marker].concat(beamPolys, beamDashLines);
    if (horizonCircle) allLayerItems.push(horizonCircle);
    const allLayer = L.featureGroup(allLayerItems);
    map.fitBounds(allLayer.getBounds().pad(0.1));
    if (isOffline) {{
      map.setMinZoom(offlineMaxZ);
      map.setMaxZoom(offlineMaxZ);
      map.setZoom(offlineMaxZ);
    }}

    let clickMarker = null;

    window.setClickMarker = function(lat2, lon2) {{
      if (clickMarker) map.removeLayer(clickMarker);
      clickMarker = L.marker([lat2, lon2], antennaTargetIcon ? {{ icon: antennaTargetIcon }} : {{}}).addTo(map);
      clickMarker.bindPopup(popupTarget);
    }};

    window.clearClickMarker = function() {{
      if (clickMarker) {{ map.removeLayer(clickMarker); clickMarker = null; }}
    }};

    map.on('click', function(e) {{
      const lat2 = e.latlng.lat;
      const lon2 = e.latlng.lng;
      window.setClickMarker(lat2, lon2);
      window.location = 'rotorapp://setaz?lat=' + lat2 + '&lon=' + lon2;
    }});

    window.updateBeam = function(data) {{
      if (!data) return;
      map.removeLayer(marker);
      clearBeamLayers();
      if (horizonCircle) {{ map.removeLayer(horizonCircle); horizonCircle = null; }}
      marker = L.marker([data.lat, data.lon], antennaIcon ? {{ icon: antennaIcon }} : {{}}).addTo(map);
      marker.bindPopup(data.popup_antenna || popupAntenna);
      drawBeamLayers(data.beams || []);
      const hKm = data.horizon_dist_km || 0;
      if (hKm > 0.5) {{
        horizonCircle = L.circle([data.lat, data.lon], {{ radius: hKm * 1000, color: horizonColor, weight: 2, dashArray: '8, 8', fill: false, fillOpacity: 0, interactive: false }}).addTo(map);
      }}
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
      if (!offline) {{
        tileLayer.on('tileerror', function(e) {{ if (!_currentOffline) console.error('ROTOR_TILEERROR'); }});
      }}
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
      if (graylineLayer) {{
        graylineLayer.setStyle({{ color: dark ? '#b8b8b8' : '#505050' }});
      }}
      if (horizonCircle) {{
        horizonCircle.setStyle({{ color: dark ? '#7eb87e' : '#2e7d32' }});
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
