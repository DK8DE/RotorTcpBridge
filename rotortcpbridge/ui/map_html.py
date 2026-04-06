"""Leaflet-HTML für die Antennenkarte (Offline/Online, Beams, Maidenhead-Overlay)."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from urllib.parse import quote

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
    target_bearing_line_json = json.dumps(params.get("target_bearing_line"))
    target_bearing_color_json = json.dumps(params.get("target_bearing_color") or "")
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
    asnearest_title = params.get("asnearest_title", "Nächste Verbindungen")
    asnearest_col_call = params.get("asnearest_col_call", "Rufzeichen")
    asnearest_col_dist = params.get("asnearest_col_dist", "Entfernung")
    asnearest_col_eta = params.get("asnearest_col_eta", "Zeit (min)")
    asnearest_col_score = params.get("asnearest_col_score", "Score")
    asnearest_tooltip_path = params.get("asnearest_tooltip_path", "Strecke QTH→DX")
    asnearest_tooltip_catpath = params.get("asnearest_tooltip_catpath", "Strecke/Kategorie")
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
    body_bg = "#1c1c1c" if dark else "inherit"
    body_map_dark_class = "map-dark" if dark else ""

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
    user_watch_data_url = ""
    for _uname in ("User.PNG", "User.png"):
        try:
            _up = _pkg_root / _uname
            user_watch_data_url = "data:image/png;base64," + base64.b64encode(_up.read_bytes()).decode(
                "ascii"
            )
            break
        except OSError:
            continue
    # User_ACC nur einbetten, wenn klein genug — große PNGs (z. B. > ~120 KB) sprengen die Seite → weiße Karte (WebEngine).
    _max_embed_asset_bytes = 120_000
    user_watch_acc_data_url = ""
    for _uname in ("User_ACC.png", "User_acc.png"):
        try:
            _up = _pkg_root / _uname
            if not _up.is_file():
                continue
            _raw = _up.read_bytes()
            if len(_raw) <= _max_embed_asset_bytes:
                user_watch_acc_data_url = "data:image/png;base64," + base64.b64encode(_raw).decode("ascii")
            break
        except OSError:
            continue
    # Kein großes PNG per Base64 im Inline-Skript (Qt WebEngine: sehr große Seiten → weiße Karte).
    # Kompaktes SVG; optional kann später rotortiles:assets/ genutzt werden, wenn die Seite nicht about:blank ist.
    _airplane_svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="#e65100">'
        '<path d="M21 16v-2l-8-5V3.5c0-.83-.67-1.5-1.5-1.5S10 2.67 10 3.5V9l-8 5v2l8-2.5V19l-2 1.5V22l3.5-1 3.5 1v-1.5L13 19v-5.5l8 2.5z"/></svg>'
    )
    airplane_icon_url = "data:image/svg+xml;charset=utf-8," + quote(_airplane_svg, safe="")

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
    _mc_css_a = _read_static("MarkerCluster.css")
    _mc_css_b = _read_static("MarkerCluster.Default.css")
    markercluster_css = (_mc_css_a + "\n" + _mc_css_b).strip()
    markercluster_js = _read_static("leaflet.markercluster.js")
    _cluster_extra_css = ""
    if dark and markercluster_css:
        # Lesbare Cluster-Farben auf dunkler Basemap
        _cluster_extra_css = """
    .marker-cluster-small { background-color: rgba(70, 130, 200, 0.45); }
    .marker-cluster-small div { background-color: rgba(45, 100, 170, 0.88); }
    .marker-cluster-medium { background-color: rgba(255, 193, 7, 0.45); }
    .marker-cluster-medium div { background-color: rgba(200, 150, 0, 0.88); }
    .marker-cluster-large { background-color: rgba(255, 120, 80, 0.5); }
    .marker-cluster-large div { background-color: rgba(200, 80, 40, 0.9); }
    """

    return f"""<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Antennenkarte</title>
  <style>{leaflet_css}</style>
  <style>{markercluster_css}</style>
  <style>
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    html, body {{ width: 100%; height: 100%; overflow: hidden; background: {body_bg}; }}
    #map {{ width: 100%; height: 100%; }}
    #info {{ position: absolute; top: 12px; left: 62px; z-index: 1000;
      background: transparent; padding: 0; max-width: min(420px, calc(100vw - 80px));
      font: 13px/1.4 sans-serif; }}
    #infoMain {{ padding: 10px 14px; border-radius: 8px;
      border: 1px solid rgba(128,128,128,0.35);
      background: rgba(255, 255, 255, 0.22);
      backdrop-filter: blur(6px);
      -webkit-backdrop-filter: blur(6px);
      box-shadow: 0 1px 3px rgba(0,0,0,0.08);
      color: #1a1a1a;
    }}
    body.map-dark #infoMain {{
      background: rgba(28, 28, 30, 0.45);
      border-color: rgba(180,180,190,0.25);
      box-shadow: 0 1px 4px rgba(0,0,0,0.35);
      color: #eaeaea;
    }}
    #infoMain div {{ margin: 2px 0; }}
    #asnearestBlock {{ margin-top: 8px; padding: 8px 10px; border-radius: 8px;
      border: 1px solid rgba(128,128,128,0.35);
      background: rgba(255, 255, 255, 0.22);
      backdrop-filter: blur(6px);
      -webkit-backdrop-filter: blur(6px);
      box-shadow: 0 1px 3px rgba(0,0,0,0.08);
    }}
    body.map-dark #asnearestBlock {{
      background: rgba(28, 28, 30, 0.45);
      border-color: rgba(180,180,190,0.25);
      box-shadow: 0 1px 4px rgba(0,0,0,0.35);
    }}
    #asnearestTitle {{ color: #1a1a1a; }}
    body.map-dark #asnearestTitle {{ color: #eaeaea; }}
    #asnearestList table {{ width: 100%; border-collapse: collapse; font-size: 11px;
      background: rgba(255, 255, 255, 0.15); border-radius: 4px; color: #1f1f1f; }}
    body.map-dark #asnearestList table {{
      background: rgba(0, 0, 0, 0.12);
      color: #e8e8e8;
    }}
    #asnearestList th, #asnearestList td {{ padding: 2px 4px; vertical-align: top; }}
    #asnearestList tbody tr.asnearest-row-hover {{
      background: rgba(0, 0, 0, 0.07);
    }}
    body.map-dark #asnearestList tbody tr.asnearest-row-hover {{
      background: rgba(255, 255, 255, 0.08);
    }}
    #asnearestList a {{ color: inherit; }}
    .leaflet-div-icon.rotor-aswatch-marker {{ border: none; background: transparent; }}
    /* Sicherstellen, dass Hover/Tooltip am User-Marker ankommen (Leaflet setzt sonst oft pointer-events:none) */
    .leaflet-marker-icon.rotor-aswatch-marker {{
      pointer-events: auto !important;
    }}
    .leaflet-div-icon.rotor-airplane-marker {{ border: none; background: transparent; }}
    .leaflet-marker-icon.rotor-airplane-marker {{
      pointer-events: auto !important;
    }}
    .leaflet-marker-icon.rotor-asnearest-hover-marker {{
      pointer-events: none !important;
    }}
    img.rotor-asnearest-hover-fallback-img {{
      filter: drop-shadow(0 0 5px rgba(76, 175, 80, 0.9)) drop-shadow(0 1px 2px rgba(0,0,0,0.45));
    }}
    {_cluster_extra_css}
  </style>
</head>
<body class="{body_map_dark_class}">
  <div id="info">
    <div id="infoMain">
      <div><strong>{info_standort}:</strong> {loc_str}</div>
      <div><strong>{info_offnung}:</strong> {opening:.1f}°</div>
      <div><strong>{info_reichweite}:</strong> {range_km:.1f} km</div>
    </div>
    <div id="asnearestBlock" style="display:none;">
      <div id="asnearestTitle" style="font-weight:600;margin-bottom:4px;"></div>
      <div id="asnearestList"></div>
    </div>
  </div>
  <div id="map"></div>
  <script>{leaflet_js}</script>
  <script>{markercluster_js}</script>
  <script>{maidenhead_js}</script>
  <script>
    const lat = {lat};
    const lon = {lon};
    const beamsInitial = {beams_json};
    const targetBearingLineInitial = {target_bearing_line_json};
    const targetBearingColorInitial = {target_bearing_color_json};
    const graylineCoords = {grayline_json};
    const graylineColor = {json.dumps(grayline_color)};
    const horizonDistKm = {params.get("horizon_dist_km", 0.0)};
    const horizonColor = {json.dumps(horizon_color)};
    const popupAntenna = {json.dumps(popup_antenna)};
    const popupTarget = {json.dumps(popup_target)};
    const ASNEAREST_TITLE = {json.dumps(asnearest_title)};
    const ASNEAREST_COL_CALL = {json.dumps(asnearest_col_call)};
    const ASNEAREST_COL_DIST = {json.dumps(asnearest_col_dist)};
    const ASNEAREST_COL_ETA = {json.dumps(asnearest_col_eta)};
    const ASNEAREST_COL_SCORE = {json.dumps(asnearest_col_score)};
    const ASNEAREST_TOOLTIP_PATH = {json.dumps(asnearest_tooltip_path)};
    const ASNEAREST_TOOLTIP_CATPATH = {json.dumps(asnearest_tooltip_catpath)};

    const TILE_URL_DARK = "https://{{s}}.basemaps.cartocdn.com/dark_all/{{z}}/{{x}}/{{y}}{{r}}.png";
    const TILE_URL_LIGHT = "https://{{s}}.basemaps.cartocdn.com/rastertiles/voyager/{{z}}/{{x}}/{{y}}{{r}}.png";
    const OFFLINE_ATTRIBUTION = "© OpenStreetMap-Mitwirkende";
    const ONLINE_ATTRIBUTION = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>';
    const isOffline = {str(offline).lower()};
    let _currentOffline = isOffline;
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
    const userWatchIconUrl = {json.dumps(user_watch_data_url)};
    const userWatchAccIconUrl = {json.dumps(user_watch_acc_data_url)};
    if (typeof map.createPane === 'function') {{
      map.createPane('rotorAsnearestHover');
      map.getPane('rotorAsnearestHover').style.zIndex = 650;
      map.getPane('rotorAsnearestHover').style.pointerEvents = 'none';
    }}
    let asnearestHoverLayer = L.layerGroup().addTo(map);
    let asnearestHoverFlightLayer = null;
    let _hoverDestKey = null;
    let _hiddenAswatchForHover = null;
    /* Offline: max. Zoom = Tile-Limit → Einzelmarker ab diesem Level; Online: ab 16 */
    const aswatchDisableClusterZoom = isOffline ? offlineMaxZ : 16;
    let aswatchLayer;
    if (typeof L.markerClusterGroup === 'function') {{
      aswatchLayer = L.markerClusterGroup({{
        spiderfyOnMaxZoom: true,
        showCoverageOnHover: false,
        zoomToBoundsOnClick: true,
        maxClusterRadius: 72,
        disableClusteringAtZoom: aswatchDisableClusterZoom,
        removeOutsideVisibleBounds: true,
        chunkedLoading: true
      }}).addTo(map);
    }} else {{
      aswatchLayer = L.layerGroup().addTo(map);
    }}
    let _lastAswatch = [];
    let _mapDarkAswatch = {str(dark).lower()};
    function _escapeHtmlAswatch(s) {{
      return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;');
    }}
    function _escapeAttrAswatch(s) {{
      return String(s).replace(/&/g,'&amp;').replace(/\"/g,'&quot;');
    }}
    let _lastAsnearestSummary = [];
    function _findAswatchMarkerByDestKey(destKey) {{
      if (!destKey || !aswatchLayer || !aswatchLayer.eachLayer) return null;
      var found = null;
      try {{
        aswatchLayer.eachLayer(function(layer) {{
          if (found) return;
          if (layer instanceof L.Marker && layer._rotorDestKey === destKey) found = layer;
        }});
      }} catch (e) {{}}
      return found;
    }}
    function _clearAsnearestHover() {{
      if (_hiddenAswatchForHover) {{
        try {{ _hiddenAswatchForHover.setOpacity(1); }} catch (e) {{}}
        _hiddenAswatchForHover = null;
      }}
      if (asnearestHoverFlightLayer) asnearestHoverFlightLayer.clearLayers();
      asnearestHoverLayer.clearLayers();
      _hoverDestKey = null;
    }}
    function _showAsnearestListHover(destKey) {{
      if (_hiddenAswatchForHover) {{
        try {{ _hiddenAswatchForHover.setOpacity(1); }} catch (e) {{}}
        _hiddenAswatchForHover = null;
      }}
      asnearestHoverLayer.clearLayers();
      if (asnearestHoverFlightLayer) asnearestHoverFlightLayer.clearLayers();
      if (!destKey) {{ _hoverDestKey = null; return; }}
      _hoverDestKey = destKey;
      var row = null;
      for (var ri = 0; ri < _lastAsnearestSummary.length; ri++) {{
        if (_lastAsnearestSummary[ri] && _lastAsnearestSummary[ri].dest_key === destKey) {{
          row = _lastAsnearestSummary[ri];
          break;
        }}
      }}
      var lat = null, lon = null;
      var mk = _findAswatchMarkerByDestKey(destKey);
      if (mk) {{
        var ll = mk.getLatLng();
        lat = ll.lat; lon = ll.lng;
      }} else if (row) {{
        var la = Number(row.lat), lo = Number(row.lon);
        if (!isNaN(la) && !isNaN(lo)) {{ lat = la; lon = lo; }}
      }}
      if (lat == null || lon == null) {{ _hoverDestKey = null; return; }}
      var useAcc = !!userWatchAccIconUrl;
      if (useAcc && mk) {{
        try {{ mk.setOpacity(0); _hiddenAswatchForHover = mk; }} catch (e) {{}}
      }}
      var bubbleBg = _mapDarkAswatch ? 'rgba(40,40,40,0.95)' : 'rgba(255,255,255,0.95)';
      var bubbleFg = _mapDarkAswatch ? '#f0f0f0' : '#111';
      var bubbleBr = _mapDarkAswatch ? '#888' : '#333';
      var sym = '';
      if (userWatchAccIconUrl) {{
        sym = '<img src="' + userWatchAccIconUrl + '" width="28" height="28" alt="" style="display:block;filter:drop-shadow(0 1px 2px rgba(0,0,0,0.45));"/>';
      }} else if (userWatchIconUrl) {{
        sym = '<img src="' + userWatchIconUrl + '" width="28" height="28" alt="" class="rotor-asnearest-hover-fallback-img" style="display:block;"/>';
      }} else {{
        sym = '<div style="width:28px;height:28px;border-radius:50%;background:#81c784;border:2px solid #2e7d32;box-shadow:0 1px 3px rgba(0,0,0,0.4);"></div>';
      }}
      var callTxt = row ? _escapeHtmlAswatch(row.call || '') : '';
      var html = '<div style="display:flex;flex-direction:column;align-items:center;">'
        + '<div style="background:' + bubbleBg + ';border:1px solid ' + bubbleBr + ';border-radius:7px;padding:2px 6px;font-size:10px;font-weight:600;line-height:1.15;margin-bottom:3px;color:' + bubbleFg + ';text-align:center;white-space:nowrap;">' + callTxt + '</div>'
        + sym + '</div>';
      var icon = L.divIcon({{ html: html, iconSize: [120, 56], iconAnchor: [60, 56], className: 'rotor-aswatch-marker rotor-asnearest-hover-marker' }});
      var paneOpt = (typeof map.getPane === 'function' && map.getPane('rotorAsnearestHover')) ? {{ pane: 'rotorAsnearestHover' }} : {{}};
      var hm = L.marker([lat, lon], L.extend({{ icon: icon, interactive: false, keyboard: false }}, paneOpt));
      hm.addTo(asnearestHoverLayer);
      if (row && asnearestHoverFlightLayer && row.hover_plane_lat != null && row.hover_plane_lon != null
          && row.hover_partner_lat != null && row.hover_partner_lon != null) {{
        var skipFlight = false;
        if (_lastAirplanes && _lastAirplanes.length && _lastAirplanes[0].dest_key === destKey) skipFlight = true;
        if (!skipFlight) {{
          var plat = Number(row.hover_plane_lat), plon = Number(row.hover_plane_lon);
          var ptlat = Number(row.hover_partner_lat), ptlon = Number(row.hover_partner_lon);
          if (!isNaN(plat) && !isNaN(plon) && !isNaN(ptlat) && !isNaN(ptlon)) {{
            var lineColor = _mapDarkAswatch ? '#ffb74d' : '#e65100';
            var fp = (typeof map.getPane === 'function' && map.getPane('rotorAsnearestHover')) ? 'rotorAsnearestHover' : undefined;
            var lineOpts = {{ color: lineColor, weight: 2, dashArray: '7,6', opacity: 0.92, interactive: false }};
            if (fp) lineOpts.pane = fp;
            L.polyline([[plat, plon], [ptlat, ptlon]], lineOpts).addTo(asnearestHoverFlightLayer);
            var flightLbl = '';
            if (row.hover_flight != null && String(row.hover_flight).trim()) flightLbl = _escapeHtmlAswatch(String(row.hover_flight).trim());
            var symP = '';
            if (airplaneIconUrl) {{
              symP = '<img src="' + airplaneIconUrl + '" width="32" height="32" alt="" style="display:block;filter:drop-shadow(0 1px 2px rgba(0,0,0,0.45));"/>';
            }} else {{
              symP = '<div style="width:32px;height:32px;background:#ff9800;border-radius:4px;border:2px solid #e65100;"></div>';
            }}
            var htmlP = '<div style="display:flex;flex-direction:column;align-items:center;">'
              + '<div style="background:' + bubbleBg + ';border:1px solid ' + bubbleBr + ';border-radius:7px;padding:2px 6px;font-size:10px;font-weight:600;line-height:1.2;margin-bottom:3px;color:' + bubbleFg + ';text-align:center;max-width:140px;">' + flightLbl + '</div>'
              + symP + '</div>';
            var iconP = L.divIcon({{ html: htmlP, iconSize: [140, 70], iconAnchor: [70, 70], className: 'rotor-airplane-marker rotor-asnearest-hover-marker' }});
            var mkOpts = L.extend({{ icon: iconP, interactive: false, keyboard: false }}, fp ? {{ pane: fp }} : {{}});
            L.marker([plat, plon], mkOpts).addTo(asnearestHoverFlightLayer);
          }}
        }}
      }}
    }}
    function _bindAsnearestRowHover(listEl) {{
      if (!listEl) return;
      listEl.querySelectorAll('tbody tr.asnearest-row').forEach(function(tr) {{
        tr.addEventListener('mouseenter', function() {{
          listEl.querySelectorAll('tbody tr.asnearest-row-hover').forEach(function(x) {{ x.classList.remove('asnearest-row-hover'); }});
          tr.classList.add('asnearest-row-hover');
          var dk = tr.getAttribute('data-dest-key');
          if (dk) _showAsnearestListHover(dk);
        }});
        tr.addEventListener('mouseleave', function() {{
          tr.classList.remove('asnearest-row-hover');
          _clearAsnearestHover();
        }});
      }});
    }}
    function _redrawAsnearestPanel() {{
      const block = document.getElementById('asnearestBlock');
      const listEl = document.getElementById('asnearestList');
      const titleEl = document.getElementById('asnearestTitle');
      if (!block || !listEl) return;
      _clearAsnearestHover();
      const rows = _lastAsnearestSummary;
      if (!rows || !rows.length) {{
        block.style.display = 'none';
        listEl.innerHTML = '';
        if (titleEl) titleEl.textContent = '';
        return;
      }}
      block.style.display = 'block';
      if (titleEl) titleEl.textContent = ASNEAREST_TITLE;
      let html = '<table><thead><tr>'
        + '<th align="left">' + ASNEAREST_COL_CALL + '</th>'
        + '<th align="right">' + ASNEAREST_COL_DIST + '</th>'
        + '<th align="right">' + ASNEAREST_COL_ETA + '</th>'
        + '<th align="right">' + ASNEAREST_COL_SCORE + '</th></tr></thead><tbody>';
      rows.forEach(function(r) {{
        const lat = Number(r.lat), lon = Number(r.lon);
        if (isNaN(lat) || isNaN(lon)) return;
        let href = 'rotorapp://setaz?lat=' + lat + '&lon=' + lon;
        if (r.dest_key) {{
          href += '&asnearest_dest=' + encodeURIComponent(r.dest_key);
        }}
        const call = _escapeHtmlAswatch(r.call || '');
        const dkAttr = r.dest_key ? _escapeAttrAswatch(r.dest_key) : '';
        html += '<tr class="asnearest-row"' + (dkAttr ? ' data-dest-key="' + dkAttr + '"' : '') + ' style="cursor:pointer;"><td><a href="' + href + '" style="color:inherit;text-decoration:underline;">' + call + '</a></td>';
        html += '<td align="right">' + (r.distance_km != null ? r.distance_km + ' km' : '–') + '</td>';
        html += '<td align="right">' + (r.duration_min != null ? r.duration_min + ' min' : '–') + '</td>';
        html += '<td align="right">' + (r.score != null ? r.score : '–') + '</td></tr>';
      }});
      html += '</tbody></table>';
      listEl.innerHTML = html;
      _bindAsnearestRowHover(listEl);
    }}
    window.setAsnearestSummary = function(rows) {{
      _lastAsnearestSummary = (rows && rows.length) ? rows.slice() : [];
      _redrawAsnearestPanel();
    }};
    window.setAswatchMarkers = function(arr) {{
      _lastAswatch = (arr && arr.length) ? arr.slice() : [];
      _hiddenAswatchForHover = null;
      if (asnearestHoverFlightLayer) asnearestHoverFlightLayer.clearLayers();
      asnearestHoverLayer.clearLayers();
      aswatchLayer.clearLayers();
      if (!_lastAswatch.length) {{
        if (_hoverDestKey) _clearAsnearestHover();
        return;
      }}
      const bubbleBg = _mapDarkAswatch ? 'rgba(40,40,40,0.95)' : 'rgba(255,255,255,0.95)';
      const bubbleFg = _mapDarkAswatch ? '#f0f0f0' : '#111';
      const bubbleBr = _mapDarkAswatch ? '#888' : '#333';
      _lastAswatch.forEach(function(m) {{
        const call = _escapeHtmlAswatch(m.call || '');
        const qrgRaw = (m.qrg != null && String(m.qrg).trim()) ? String(m.qrg).trim() : '';
        const qrgHtml = qrgRaw
          ? ('<div style="font-size:10px;font-weight:600;line-height:1.15;margin-top:2px;white-space:nowrap;text-align:center;color:' + bubbleFg + ';">' + _escapeHtmlAswatch(qrgRaw) + '</div>')
          : '';
        let symbol = '';
        if (userWatchIconUrl) {{
          symbol = '<img src="' + userWatchIconUrl + '" width="28" height="28" alt="" style="display:block;filter:drop-shadow(0 1px 2px rgba(0,0,0,0.45));pointer-events:auto;"/>';
        }} else {{
          symbol = '<div style="width:28px;height:28px;border-radius:50%;background:#5B9BD5;border:2px solid #2e6da4;box-shadow:0 1px 3px rgba(0,0,0,0.4);pointer-events:auto;"></div>';
        }}
        const bubbleInner = '<div style="white-space:nowrap;text-align:center;line-height:1.15;">' + call + '</div>' + qrgHtml;
        const html = '<div style="display:flex;flex-direction:column;align-items:center;">'
          + '<div style="background:' + bubbleBg + ';border:1px solid ' + bubbleBr + ';border-radius:7px;padding:2px 6px;font-size:10px;font-weight:600;line-height:1.15;margin-bottom:3px;color:' + bubbleFg + ';">' + bubbleInner + '</div>'
          + symbol + '</div>';
        const hasQrg = !!qrgRaw;
        const iconH = hasQrg ? 72 : 56;
        const icon = L.divIcon({{ html: html, iconSize: [120, iconH], iconAnchor: [60, iconH], className: 'rotor-aswatch-marker' }});
        const mk = L.marker([m.lat, m.lon], {{ icon: icon, interactive: true }});
        mk._rotorDestKey = (m.dest_key != null && String(m.dest_key).trim()) ? String(m.dest_key).trim() : '';
        /* MarkerClusterGroup fängt Klicks ab: map.on('click') feuert auf User-Icon nicht → Rotor wie bei Kartenklick */
        mk.on('click', function(ev) {{
          if (ev && ev.originalEvent) {{ L.DomEvent.stopPropagation(ev.originalEvent); }}
          const lat2 = Number(m.lat);
          const lon2 = Number(m.lon);
          if (isNaN(lat2) || isNaN(lon2)) return;
          window.setClickMarker(lat2, lon2);
          window.location = 'rotorapp://setaz?lat=' + lat2 + '&lon=' + lon2;
        }});
        mk.addTo(aswatchLayer);
      }});
      if (_hoverDestKey) {{
        _showAsnearestListHover(_hoverDestKey);
      }}
      // Kein fitBounds bei ASWATCH-Updates: sonst zoomt die Karte bei jedem UDP-
      // Tick heraus, sobald ein Marker außerhalb des Viewports liegt. Zoom und
      // Pan bleiben in der Hand des Nutzers.
    }};
    const airplaneIconUrl = {json.dumps(airplane_icon_url)};
    let airplaneLayer = L.layerGroup().addTo(map);
    asnearestHoverFlightLayer = L.layerGroup().addTo(map);
    let _lastAirplanes = [];
    function _airplanePopupKey(m) {{
      return String(m.flight || '') + '\\u0001' + String(m.partner || '') + '\\u0001' + String(m.dest_loc || '');
    }}
    window.setAirplaneMarkers = function(arr) {{
      var reopenKey = null;
      try {{
        if (map._popup && map._popup.isOpen() && map._popup._source && map._popup._source._rotorAirplaneKey) {{
          reopenKey = map._popup._source._rotorAirplaneKey;
        }}
      }} catch (e) {{}}
      _lastAirplanes = (arr && arr.length) ? arr.slice() : [];
      airplaneLayer.clearLayers();
      if (!_lastAirplanes.length) {{
        if (_hoverDestKey) _showAsnearestListHover(_hoverDestKey);
        return;
      }}
      const lineColor = _mapDarkAswatch ? '#ffb74d' : '#e65100';
      _lastAirplanes.forEach(function(m) {{
        if (m.link_ok && m.partner_lat != null && m.partner_lon != null
            && !isNaN(Number(m.partner_lat)) && !isNaN(Number(m.partner_lon))) {{
          L.polyline([[m.lat, m.lon], [Number(m.partner_lat), Number(m.partner_lon)]], {{
            color: lineColor, weight: 2, dashArray: '7,6', opacity: 0.92, interactive: false
          }}).addTo(airplaneLayer);
        }}
        const flight = _escapeHtmlAswatch(m.flight || '');
        const partner = _escapeHtmlAswatch(m.partner || '');
        let tip = '<b>' + flight + '</b> → ' + partner;
        if (m.distance_km != null) tip += '<br/>' + m.distance_km + ' km';
        tip += '<br/>Potenzial: ' + (m.potential != null ? m.potential : '-') + ' %';
        if (m.path_fraction != null) tip += '<br/>' + ASNEAREST_TOOLTIP_PATH + ': ' + Math.round(Number(m.path_fraction) * 1000) / 10 + ' %';
        if (m.alt_path_factor != null) tip += '<br/>' + ASNEAREST_TOOLTIP_CATPATH + ': ' + Math.round(Number(m.alt_path_factor) * 1000) / 10 + ' %';
        if (m.score != null) tip += '<br/>Score: ' + m.score + ' /100';
        if (m.duration_min != null) tip += '<br/>ca. ' + m.duration_min + ' min';
        if (m.category) tip += '<br/>' + _escapeHtmlAswatch(m.category);
        let sym = '';
        if (airplaneIconUrl) {{
          sym = '<img src="' + airplaneIconUrl + '" width="32" height="32" alt="" style="display:block;filter:drop-shadow(0 1px 2px rgba(0,0,0,0.45));"/>';
        }} else {{
          sym = '<div style="width:32px;height:32px;background:#ff9800;border-radius:4px;border:2px solid #e65100;"></div>';
        }}
        const bubbleBg = _mapDarkAswatch ? 'rgba(40,40,40,0.95)' : 'rgba(255,255,255,0.95)';
        const bubbleFg = _mapDarkAswatch ? '#f0f0f0' : '#111';
        const bubbleBr = _mapDarkAswatch ? '#888' : '#333';
        const html = '<div style="display:flex;flex-direction:column;align-items:center;">'
          + '<div style="background:' + bubbleBg + ';border:1px solid ' + bubbleBr + ';border-radius:7px;padding:2px 6px;font-size:10px;font-weight:600;line-height:1.2;margin-bottom:3px;color:' + bubbleFg + ';text-align:center;max-width:140px;">' + flight + '</div>'
          + sym + '</div>';
        const icon = L.divIcon({{ html: html, iconSize: [140, 70], iconAnchor: [70, 70], className: 'rotor-airplane-marker' }});
        const mk = L.marker([m.lat, m.lon], {{ icon: icon, interactive: true }}).addTo(airplaneLayer);
        mk._rotorAirplaneKey = _airplanePopupKey(m);
        mk.bindPopup(tip);
      }});
      if (reopenKey) {{
        airplaneLayer.eachLayer(function(layer) {{
          if (layer instanceof L.Marker && layer._rotorAirplaneKey === reopenKey) {{
            layer.openPopup();
          }}
        }});
      }}
      if (_hoverDestKey) {{
        _showAsnearestListHover(_hoverDestKey);
      }}
    }};

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
    let targetBearingLineLayer = null;
    function clearTargetBearingLine() {{
      if (targetBearingLineLayer) {{
        map.removeLayer(targetBearingLineLayer);
        targetBearingLineLayer = null;
      }}
    }}
    function drawTargetBearingLine(coords, color) {{
      clearTargetBearingLine();
      if (!coords || coords.length < 2 || !color) return;
      targetBearingLineLayer = L.polyline(coords, {{
        color: color, weight: 2, dashArray: '8, 8', interactive: false
      }}).addTo(map);
    }}
    drawBeamLayers(beamsInitial);
    drawTargetBearingLine(targetBearingLineInitial, targetBearingColorInitial);

    const allLayerItems = [marker].concat(beamPolys, beamDashLines);
    if (targetBearingLineLayer) allLayerItems.push(targetBearingLineLayer);
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
      drawTargetBearingLine(data.target_bearing_line, data.target_bearing_color);
      const hKm = data.horizon_dist_km || 0;
      if (hKm > 0.5) {{
        horizonCircle = L.circle([data.lat, data.lon], {{ radius: hKm * 1000, color: horizonColor, weight: 2, dashArray: '8, 8', fill: false, fillOpacity: 0, interactive: false }}).addTo(map);
      }}
      const infoMain = document.getElementById('infoMain');
      if (infoMain) infoMain.innerHTML = '<div><strong>' + (data.info_standort || 'Standort') + ':</strong> ' + data.location_str + '</div>' +
        '<div><strong>' + (data.info_offnung || 'Öffnungswinkel') + ':</strong> ' + data.opening.toFixed(1) + '°</div>' +
        '<div><strong>' + (data.info_reichweite || 'Reichweite') + ':</strong> ' + data.range_km.toFixed(1) + ' km</div>';
    }};

    const OFFLINE_TILE_URL_LIGHT = {json.dumps(tile_url_light)};
    const OFFLINE_TILE_URL_DARK = {json.dumps(tile_url_dark)};
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
      document.body.classList.toggle('map-dark', !!dark);
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
      _mapDarkAswatch = dark;
      if (typeof window.setAswatchMarkers === 'function') window.setAswatchMarkers(_lastAswatch);
      if (typeof window.setAirplaneMarkers === 'function') window.setAirplaneMarkers(_lastAirplanes);
      if (typeof _redrawAsnearestPanel === 'function') _redrawAsnearestPanel();
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
