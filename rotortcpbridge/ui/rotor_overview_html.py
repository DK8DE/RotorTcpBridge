"""Leaflet-HTML für Rotorübersicht (mehrere Standorte, nur Anzeige)."""

from __future__ import annotations

import json

from .map_tiles import (
    ONLINE_TILE_ATTRIBUTION,
    ONLINE_TILE_URL_LIGHT,
    _static_lib_path,
)


def _read_static(name: str) -> str:
    p = _static_lib_path() / name
    if not p.is_file():
        return ""
    txt = p.read_text(encoding="utf-8", errors="replace")
    if name.endswith(".js"):
        txt = txt.replace("</script>", "<\\/script>")
    return txt


def build_overview_html(params: dict) -> str:
    """params: center_lat, center_lon, zoom, dark_mode, sites (list of site render dicts)."""
    center_lat = float(params.get("center_lat", 50.0))
    center_lon = float(params.get("center_lon", 10.0))
    zoom = int(params.get("zoom", 5))
    dark = bool(params.get("dark_mode", False))
    sites_json = json.dumps(params.get("sites") or [])
    leaflet_css = _read_static("leaflet.css")
    leaflet_js = _read_static("leaflet.min.js")
    tile_url = ONLINE_TILE_URL_LIGHT
    attrib = ONLINE_TILE_ATTRIBUTION
    body_cls = "map-dark" if dark else ""
    body_bg = "#1c1c1c" if dark else "inherit"

    return f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8"/>
<style>
{leaflet_css}
html, body, #map {{ margin:0; padding:0; height:100%; width:100%; }}
body {{ background: {body_bg}; }}
/* Wie MapWindow: nur Kacheln abdunkeln, Marker/Labels/Beams bleiben farbtreu */
body.map-dark .leaflet-tile-pane {{
  filter: invert(1) hue-rotate(180deg) brightness(0.95) contrast(0.9) saturate(0.65);
}}
.ov-label {{
  background: rgba(255,255,255,0.85);
  border: 1px solid #888;
  border-radius: 3px;
  padding: 2px 6px;
  font: 12px/1.2 sans-serif;
  white-space: nowrap;
  color: #222;
}}
body.map-dark .ov-label {{
  background: rgba(45,45,45,0.9);
  border-color: #666;
  color: #eaeaea;
}}
.ov-offline {{ opacity: 0.55; }}
body.map-dark .leaflet-control-attribution {{
  background: rgba(45,45,45,0.85) !important;
  color: #c8c8c8;
}}
body.map-dark .leaflet-control-attribution a {{ color: #9ec8ff; }}
body.map-dark .leaflet-control-zoom a {{
  background: #2d2d2d !important;
  color: #eaeaea !important;
  border-color: #555 !important;
}}
</style>
</head><body class="{body_cls}">
<div id="map"></div>
<script>
{leaflet_js}
const TILE_URL = {json.dumps(tile_url)};
const ATTRIB = {json.dumps(attrib)};
let _mapDark = {str(dark).lower()};
const map = L.map('map', {{ zoomControl: true }}).setView([{center_lat}, {center_lon}], {zoom});
L.tileLayer(TILE_URL, {{ maxZoom: 19, minZoom: 2, attribution: ATTRIB }}).addTo(map);
const siteLayers = {{}};

function clearSite(id) {{
  const g = siteLayers[id];
  if (!g) return;
  g.forEach(l => map.removeLayer(l));
  delete siteLayers[id];
}}

function drawSite(s) {{
  if (!s || !s.id) return;
  clearSite(s.id);
  const layers = [];
  const offline = !s.online;
  const marker = L.circleMarker([s.lat, s.lon], {{
    radius: 7,
    color: s.marker_color || '#333',
    fillColor: s.online ? (s.marker_color || '#2e7d32') : '#888',
    fillOpacity: 0.9,
    weight: 2
  }}).addTo(map);
  layers.push(marker);
  let popup = '<b>' + (s.name || '') + '</b><br/>';
  if (s.az_text) popup += 'AZ: ' + s.az_text + '<br/>';
  if (s.ref_text) popup += s.ref_text + '<br/>';
  popup += s.online ? 'Online' : 'Offline';
  marker.bindPopup(popup);
  const label = L.marker([s.lat, s.lon], {{
    icon: L.divIcon({{
      className: offline ? 'ov-label ov-offline' : 'ov-label',
      html: (s.name || '') + (s.az_text ? (' · ' + s.az_text) : ''),
      iconSize: null,
      iconAnchor: [-8, 20]
    }}),
    interactive: false
  }}).addTo(map);
  layers.push(label);
  (s.beams || []).forEach(b => {{
    if (!b.polygon || b.polygon.length < 3) return;
    const poly = L.polygon(b.polygon, {{
      color: b.stroke || '#5BA3D0',
      fillColor: b.fill || b.stroke || '#87CEEB',
      weight: 2,
      fillOpacity: offline ? 0.15 : 0.28,
      opacity: offline ? 0.45 : 0.85
    }}).addTo(map);
    layers.push(poly);
  }});
  siteLayers[s.id] = layers;
}}

window.updateOverviewSites = function(sites) {{
  const ids = {{}};
  (sites || []).forEach(s => {{ ids[s.id] = true; drawSite(s); }});
  Object.keys(siteLayers).forEach(id => {{ if (!ids[id]) clearSite(id); }});
}};

window.setOverviewView = function(lat, lon, zoom) {{
  if (lat == null || lon == null) return;
  map.setView([lat, lon], zoom != null ? zoom : map.getZoom());
}};

window.getOverviewView = function() {{
  const c = map.getCenter();
  return {{ lat: c.lat, lon: c.lng, zoom: map.getZoom() }};
}};

window.setOverviewDarkMode = function(dark) {{
  _mapDark = !!dark;
  document.body.classList.toggle('map-dark', _mapDark);
  document.body.style.background = _mapDark ? '#1c1c1c' : 'inherit';
}};

window.updateOverviewSites({sites_json});
</script>
</body></html>
"""
