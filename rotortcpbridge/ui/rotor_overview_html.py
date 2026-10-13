"""Leaflet-HTML für Rotorübersicht (Desktop-WebView + Browser ``/rotoren``)."""

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
    """params: center_lat, center_lon, zoom, dark_mode, sites; optional web_mode/labels/config."""
    center_lat = float(params.get("center_lat", 50.0))
    center_lon = float(params.get("center_lon", 10.0))
    zoom = int(params.get("zoom", 5))
    dark = bool(params.get("dark_mode", False))
    web_mode = bool(params.get("web_mode", False))
    sites_json = json.dumps(params.get("sites") or [])
    labels_json = json.dumps(params.get("labels") or {}, ensure_ascii=False)
    profiles_json = json.dumps(params.get("profiles") or [], ensure_ascii=False)
    config_json = json.dumps(params.get("config") or {}, ensure_ascii=False)
    status_text = json.dumps(str(params.get("status_text") or ""), ensure_ascii=False)
    show_active = bool(params.get("show_active_antenna_only", False))
    if not show_active and isinstance(params.get("config"), dict):
        show_active = bool(params["config"].get("show_active_antenna_only", False))
    leaflet_css = _read_static("leaflet.css")
    leaflet_js = _read_static("leaflet.min.js")
    tile_url = ONLINE_TILE_URL_LIGHT
    attrib = ONLINE_TILE_ATTRIBUTION
    body_cls = "map-dark" if dark else ""
    body_bg = "#1c1c1c" if dark else "inherit"
    web_css = _WEB_CSS if web_mode else ""
    web_chrome = _web_chrome_html() if web_mode else ""
    web_js = _web_js_block(status_text) if web_mode else ""

    return f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Rotorübersicht</title>
<style>
{leaflet_css}
html, body {{ margin:0; padding:0; height:100%; width:100%; }}
#mapWrap {{ margin:0; padding:0; height:100%; width:100%; position:relative; }}
#map {{ margin:0; padding:0; height:100%; width:100%; }}
body {{ background: {body_bg}; font-family: system-ui, Segoe UI, sans-serif; }}
body.map-dark .leaflet-tile-pane {{
  filter: invert(1) hue-rotate(180deg) brightness(0.95) contrast(0.9) saturate(0.65);
}}
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
.ov-beam-tip {{
  background: rgba(255,255,255,0.94);
  border: 1px solid #888;
  border-radius: 4px;
  color: #222;
  font: 12px/1.25 system-ui, Segoe UI, sans-serif;
  box-shadow: 0 1px 4px rgba(0,0,0,0.18);
  padding: 4px 8px;
}}
.ov-beam-tip::before {{ display: none; }}
body.map-dark .ov-beam-tip {{
  background: rgba(40,40,40,0.94);
  border-color: #666;
  color: #eaeaea;
}}
#ovHud {{
  position:absolute; top:10px; left:54px; right:10px; z-index:1100;
  display:flex; flex-wrap:wrap; gap:8px; align-items:flex-start;
  pointer-events:none;
}}
.ovHudCard {{
  min-width:148px; max-width:220px; padding:8px 12px; border-radius:8px;
  border:1px solid rgba(128,128,128,0.35);
  background:rgba(255,255,255,0.22);
  backdrop-filter:blur(6px); -webkit-backdrop-filter:blur(6px);
  box-shadow:0 1px 3px rgba(0,0,0,0.08); color:#1a1a1a;
  font:12px/1.25 system-ui, Segoe UI, sans-serif;
}}
body.map-dark .ovHudCard {{
  background:rgba(28,28,30,0.45); border-color:rgba(180,180,190,0.25);
  box-shadow:0 1px 4px rgba(0,0,0,0.35); color:#eaeaea;
}}
.ovHudCard.offline {{ opacity:0.62; }}
.ovHudAz {{
  font-size:28px; font-weight:700; line-height:1.05;
  font-variant-numeric:tabular-nums; letter-spacing:-0.02em;
}}
.ovHudName {{ font-size:13px; font-weight:600; margin-top:4px; word-break:break-word; }}
.ovHudMeta, .ovHudWind {{ font-size:12px; margin-top:3px; opacity:0.92; }}
{web_css}
</style>
</head><body class="{body_cls}">
{web_chrome}
<div id="mapWrap">
<div id="map"></div>
<div id="ovHud"></div>
</div>
<script>
{leaflet_js}
const TILE_URL = {json.dumps(tile_url)};
const ATTRIB = {json.dumps(attrib)};
const WEB_MODE = {str(web_mode).lower()};
const LABELS = {labels_json};
const PROFILES = {profiles_json};
let OV_CONFIG = {config_json};
const SHOW_ACTIVE_ANTENNA_ONLY = {str(show_active).lower()};
let _mapDark = {str(dark).lower()};
const map = L.map('map', {{ zoomControl: true }}).setView([{center_lat}, {center_lon}], {zoom});
L.tileLayer(TILE_URL, {{ maxZoom: 19, minZoom: 2, attribution: ATTRIB }}).addTo(map);
setTimeout(function() {{ try {{ map.invalidateSize(); }} catch (e) {{}} }}, 0);
const siteLayers = {{}};

function clearSite(id) {{
  const g = siteLayers[id];
  if (!g) return;
  g.forEach(l => map.removeLayer(l));
  delete siteLayers[id];
}}

function beamTooltipHtml(s, b) {{
  let html = '<b>' + (s.name || '') + '</b>';
  if (b && b.name) html += '<br/>' + b.name;
  if (s.az_text) html += '<br/>AZ: ' + s.az_text;
  return html;
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
  (s.beams || []).forEach(b => {{
    if (!b.polygon || b.polygon.length < 3) return;
    const poly = L.polygon(b.polygon, {{
      color: b.stroke || '#5BA3D0',
      fillColor: b.fill || b.stroke || '#87CEEB',
      weight: 2,
      fillOpacity: offline ? 0.15 : 0.28,
      opacity: offline ? 0.45 : 0.85
    }}).addTo(map);
    poly.bindTooltip(beamTooltipHtml(s, b), {{
      sticky: true,
      direction: 'top',
      opacity: 1,
      className: 'ov-beam-tip'
    }});
    layers.push(poly);
  }});
  siteLayers[s.id] = layers;
}}

function escHud(s) {{
  return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({{
    '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
  }})[c]);
}}
function hudLbl(k, fb) {{
  const LBL = (typeof LABELS === 'object' && LABELS) ? LABELS : {{}};
  return LBL[k] || fb || k;
}}
function fmtHudTemp(v) {{
  if (v == null || v === '') return '–';
  const n = Number(v);
  return isFinite(n) ? (n.toFixed(1) + ' °C') : '–';
}}
function fmtHudWind(s) {{
  const parts = [];
  if (s.wind_kmh != null && isFinite(Number(s.wind_kmh))) {{
    parts.push(Number(s.wind_kmh).toFixed(1) + ' km/h');
  }}
  if (s.wind_dir_deg != null && isFinite(Number(s.wind_dir_deg))) {{
    parts.push(Math.round(Number(s.wind_dir_deg)) + '°');
  }}
  return parts.join(' · ');
}}
function renderOverviewHud(sites) {{
  const el = document.getElementById('ovHud');
  if (!el) return;
  const motor = hudLbl('hud_temp_motor', 'Motor');
  const amb = hudLbl('hud_temp_ambient', 'Außen');
  const wind = hudLbl('hud_wind', 'Wind');
  el.innerHTML = (sites || []).map(s => {{
    const name = escHud(s.profile_name || s.name || '');
    const az = escHud(s.az_text || '–');
    const off = s.online ? '' : ' offline';
    let windHtml = '';
    if (s.wind_visible) {{
      const w = fmtHudWind(s);
      if (w) windHtml = '<div class="ovHudWind">' + escHud(wind) + ': ' + escHud(w) + '</div>';
    }}
    return '<div class="ovHudCard' + off + '">'
      + '<div class="ovHudAz">' + az + '</div>'
      + '<div class="ovHudName">' + name + '</div>'
      + '<div class="ovHudMeta">' + escHud(motor) + ': ' + escHud(fmtHudTemp(s.temp_motor_c))
      + ' · ' + escHud(amb) + ': ' + escHud(fmtHudTemp(s.temp_ambient_c)) + '</div>'
      + windHtml
      + '</div>';
  }}).join('');
}}

window.updateOverviewSites = function(sites) {{
  const ids = {{}};
  (sites || []).forEach(s => {{ ids[s.id] = true; drawSite(s); }});
  Object.keys(siteLayers).forEach(id => {{ if (!ids[id]) clearSite(id); }});
  renderOverviewHud(sites);
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
{web_js}
</script>
</body></html>
"""


_WEB_CSS = """
body.web-ov { display:flex; flex-direction:column; }
body.web-ov #mapWrap { flex:1 1 auto; min-height:0; height:auto; }
body.web-ov #map { height:100%; }
#ovTop {
  flex:0 0 auto; z-index:1000; display:flex; align-items:center; gap:10px;
  padding:8px 12px; border-bottom:1px solid #8884;
  background: rgba(250,250,250,0.94);
}
body.map-dark #ovTop { background: rgba(36,36,36,0.94); color:#eaeaea; border-color:#555; }
#ovTop h1 { font-size:15px; margin:0; font-weight:600; }
#ovStatus { margin-left:auto; font-size:12px; opacity:0.9; }
#ovChkActiveWrap {
  display:flex; align-items:center; gap:6px; font-size:12px; cursor:pointer;
  user-select:none; white-space:nowrap;
}
#ovChkActiveWrap input { margin:0; }
#ovTop button {
  border:1px solid #8888; border-radius:6px; padding:5px 10px; cursor:pointer;
  background:#fff; color:inherit; font-size:12px;
}
body.map-dark #ovTop button { background:#333; color:#eaeaea; border-color:#666; }
#ovTop button:hover { filter:brightness(1.05); }
#ovPanel {
  display:none; position:fixed; top:0; right:0; width:min(460px,100%); height:100%;
  z-index:2000; overflow:auto; box-shadow:-4px 0 18px rgba(0,0,0,0.25);
  background:#f7f7f7; color:#222; padding:12px 14px 28px;
}
body.map-dark #ovPanel { background:#242424; color:#eaeaea; }
#ovPanel.open { display:block; }
#ovPanel h2 { margin:0 0 10px; font-size:16px; }
#ovPanel h3 { margin:14px 0 8px; font-size:13px; }
#ovSiteList { list-style:none; margin:0; padding:0; }
#ovSiteList li {
  display:flex; gap:6px; align-items:center; padding:6px 0;
  border-bottom:1px solid #8883;
}
#ovSiteList li span { flex:1; font-size:13px; }
#ovPanel input, #ovPanel select, #ovPanel button {
  font-size:12px; border-radius:5px; border:1px solid #8888; padding:4px 6px;
  background:#fff; color:inherit;
}
body.map-dark #ovPanel input, body.map-dark #ovPanel select, body.map-dark #ovPanel button {
  background:#333; color:#eaeaea; border-color:#666;
}
#ovPanel .row { display:flex; gap:6px; align-items:center; margin:6px 0; flex-wrap:wrap; }
#ovPanel .row label { min-width:120px; font-size:12px; }
#ovPanel .row > input, #ovPanel .row > select { flex:1; min-width:120px; }
#ovEditor { display:none; margin-top:10px; padding-top:8px; border-top:1px solid #8884; }
#ovEditor.open { display:block; }
#ovEditor .ant-row { display:grid; grid-template-columns:auto 1fr auto 70px 70px 70px 42px; gap:4px; align-items:center; margin:4px 0; }
#ovEditor .ant-row label.dip { font-size:12px; white-space:nowrap; }
#ovMsg { font-size:12px; margin:8px 0; min-height:1.2em; opacity:0.9; }
#ovBackdrop {
  display:none; position:fixed; inset:0; background:rgba(0,0,0,0.35); z-index:1500;
}
#ovBackdrop.open { display:block; }
"""


def _web_chrome_html() -> str:
    return """
<div id="ovTop">
  <h1 id="ovTitle"></h1>
  <button type="button" id="ovBtnSettings"></button>
  <button type="button" id="ovBtnReload"></button>
  <label id="ovChkActiveWrap" title="">
    <input type="checkbox" id="ovChkActiveAnt"/>
    <span id="ovChkActiveLabel"></span>
  </label>
  <div id="ovStatus"></div>
</div>
<div id="ovBackdrop"></div>
<aside id="ovPanel">
  <h2 id="ovPanelTitle"></h2>
  <p id="ovWebHint" style="font-size:12px;opacity:.85;margin:0 0 10px;"></p>
  <div class="row">
    <button type="button" id="ovBtnAdd"></button>
    <button type="button" id="ovBtnClosePanel"></button>
  </div>
  <ul id="ovSiteList"></ul>
  <div id="ovEditor">
    <h3 id="ovEditTitle"></h3>
    <p id="ovEditHint" style="font-size:12px;opacity:.85;margin:0 0 8px;"></p>
    <div class="row"><label><input id="ovEnabled" type="checkbox"/> <span id="ovLblEnabled"></span></label></div>
    <div class="row ov-app-only"><label id="ovLblHost"></label><input id="ovHost" type="text"/></div>
    <div class="row ov-app-only"><label id="ovLblPort"></label><input id="ovPort" type="number" min="1" max="65535"/></div>
    <div class="row ov-app-only"><label id="ovLblPassword"></label><input id="ovPassword" type="password" autocomplete="off"/></div>
    <p id="ovMapHint" class="ov-app-only" style="font-size:12px;opacity:.85;margin:8px 0;"></p>
    <h3 id="ovAntTitle" style="margin:12px 0 4px;"></h3>
    <p id="ovAntHint" style="font-size:12px;opacity:.85;margin:0 0 6px;"></p>
    <div id="ovAntChecks" class="row" style="flex-direction:column;align-items:flex-start;gap:4px;"></div>
    <div class="row" style="margin-top:12px;">
      <button type="button" id="ovBtnSaveSite"></button>
      <button type="button" id="ovBtnCancelEdit"></button>
    </div>
  </div>
  <div id="ovMsg"></div>
</aside>
"""


def _web_js_block(status_text_json: str) -> str:
    js = r"""
document.body.classList.add('web-ov');
const statusEl = document.getElementById('ovStatus');
(function() {
  const LBL = LABELS || {};
  function L(k, fb) { return LBL[k] || fb || k; }
  document.getElementById('ovTitle').textContent = L('title', 'Rotorübersicht');
  document.getElementById('ovBtnSettings').textContent = L('menu_sites', 'Standorte…');
  document.getElementById('ovBtnReload').textContent = L('menu_reload', 'Reload');
  document.getElementById('ovPanelTitle').textContent = L('settings_title', 'Standorte');
  document.getElementById('ovWebHint').textContent = L('web_hint', '');
  document.getElementById('ovBtnAdd').textContent = L('btn_add', 'Add');
  document.getElementById('ovBtnClosePanel').textContent = L('btn_close', 'Close');
  document.getElementById('ovEditTitle').textContent = L('site_edit_title', 'Edit');
  document.getElementById('ovLblEnabled').textContent = L('field_enabled', 'Enabled');
  document.getElementById('ovLblHost').textContent = L('field_host', 'Host');
  document.getElementById('ovLblPort').textContent = L('field_port', 'Port');
  document.getElementById('ovLblPassword').textContent = L('field_password', 'Password');
  document.getElementById('ovPassword').placeholder = L('password_hint', 'empty = rotor');
  document.getElementById('ovMapHint').textContent = L('map_server_hint', '');
  document.getElementById('ovAntTitle').textContent = L('antennas_group', 'Antennas');
  document.getElementById('ovAntHint').textContent = L('antennas_select_hint', '');
  document.getElementById('ovBtnSaveSite').textContent = L('btn_save', 'Save');
  document.getElementById('ovBtnCancelEdit').textContent = L('btn_cancel', 'Cancel');
  const chkActive = document.getElementById('ovChkActiveAnt');
  const chkActiveLabel = document.getElementById('ovChkActiveLabel');
  const chkActiveWrap = document.getElementById('ovChkActiveWrap');
  if (chkActiveLabel) chkActiveLabel.textContent = L('chk_active_antenna', 'Active antenna only');
  if (chkActiveWrap) chkActiveWrap.title = L('chk_active_antenna_tooltip', '');
  if (chkActive) {
    const initial = (typeof SHOW_ACTIVE_ANTENNA_ONLY === 'boolean')
      ? SHOW_ACTIVE_ANTENNA_ONLY
      : !!(OV_CONFIG && OV_CONFIG.show_active_antenna_only);
    chkActive.checked = !!initial;
  }
  if (statusEl) statusEl.textContent = __STATUS_TEXT__;

  let editIndex = -1;
  let editSite = null;
  const panel = document.getElementById('ovPanel');
  const backdrop = document.getElementById('ovBackdrop');
  const editor = document.getElementById('ovEditor');
  const msg = document.getElementById('ovMsg');

  function setMsg(t, isErr) {
    msg.textContent = t || '';
    msg.style.color = isErr ? '#c62828' : '';
  }

  async function api(path, opts) {
    const r = await fetch(path, Object.assign({
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin'
    }, opts || {}));
    return await r.json();
  }

  async function postAction(action, payload) {
    return api('/api/overview/action', {
      method: 'POST',
      body: JSON.stringify(Object.assign({ action: action }, payload || {}))
    });
  }

  function openPanel() {
    renderSiteList();
    panel.classList.add('open');
    backdrop.classList.add('open');
  }
  function closePanel() {
    panel.classList.remove('open');
    backdrop.classList.remove('open');
    editor.classList.remove('open');
    editIndex = -1;
  }

  function isLocal(s) {
    return !!(s && (s.kind === 'local' || s.id === 'local'));
  }

  function fillProfiles() {}

  function renderSiteList() {
    const ul = document.getElementById('ovSiteList');
    ul.innerHTML = '';
    (OV_CONFIG.sites || []).forEach((s, i) => {
      const li = document.createElement('li');
      const span = document.createElement('span');
      const en = s.enabled ? L('enabled_yes', 'on') : L('enabled_no', 'off');
      if (isLocal(s)) {
        span.textContent = L('local_site', 'This instance') + ' · ' + en;
      } else {
        span.textContent = (s.host || '') + ':' + (s.port || '') + ' · ' + en;
      }
      const bEdit = document.createElement('button');
      bEdit.textContent = L('btn_edit', 'Edit');
      bEdit.onclick = () => openEditor(i);
      li.appendChild(span);
      li.appendChild(bEdit);
      if (!isLocal(s)) {
        const bDel = document.createElement('button');
        bDel.textContent = L('btn_delete', 'Delete');
        bDel.onclick = () => deleteSite(i);
        li.appendChild(bDel);
      }
      ul.appendChild(li);
    });
  }

  function fillAntennaChecks(site) {
    const box = document.getElementById('ovAntChecks');
    if (!box) return;
    box.innerHTML = '';
    const ants = Array.isArray(site.antennas) ? site.antennas : [];
    for (let i = 0; i < 3; i++) {
      const ant = ants[i] || {};
      const label = document.createElement('label');
      label.style.display = 'flex';
      label.style.alignItems = 'center';
      label.style.gap = '6px';
      label.style.fontSize = '12px';
      const chk = document.createElement('input');
      chk.type = 'checkbox';
      chk.className = 'ovAntChk';
      chk.dataset.slot = String(i + 1);
      chk.checked = ant.enabled !== false;
      const span = document.createElement('span');
      const base = L('antenna_' + (i + 1), 'Ant. ' + (i + 1));
      const nm = String(ant.name || '').trim();
      span.textContent = (nm && !/^antenne\s/i.test(nm)) ? (base + ' · ' + nm) : base;
      label.appendChild(chk);
      label.appendChild(span);
      box.appendChild(label);
    }
  }

  function openEditor(idx) {
    editIndex = idx;
    editSite = JSON.parse(JSON.stringify((OV_CONFIG.sites || [])[idx] || {}));
    const local = isLocal(editSite);
    document.getElementById('ovEditHint').textContent = local
      ? L('local_hint', '')
      : L('add_hint', '');
    document.getElementById('ovEnabled').checked = !!editSite.enabled;
    document.getElementById('ovHost').value = editSite.host || '';
    document.getElementById('ovPort').value = editSite.port || 80;
    document.getElementById('ovPassword').value = editSite.password || '';
    document.querySelectorAll('.ov-app-only').forEach(el => {
      el.style.display = local ? 'none' : '';
    });
    fillAntennaChecks(editSite);
    editor.classList.add('open');
  }

  function readEditorSite() {
    const site = Object.assign({}, editSite || {});
    site.enabled = document.getElementById('ovEnabled').checked;
    const ants = Array.isArray(site.antennas) ? site.antennas.slice() : [];
    while (ants.length < 3) ants.push({ enabled: true, name: '' });
    document.querySelectorAll('#ovAntChecks .ovAntChk').forEach((chk) => {
      const slot = parseInt(chk.dataset.slot || '0', 10);
      if (slot < 1 || slot > 3) return;
      const ant = Object.assign({}, ants[slot - 1] || {});
      ant.enabled = !!chk.checked;
      ants[slot - 1] = ant;
    });
    site.antennas = ants;
    if (isLocal(site)) {
      site.id = 'local';
      site.kind = 'local';
    } else {
      site.kind = 'app';
      site.host = document.getElementById('ovHost').value.trim();
      site.port = parseInt(document.getElementById('ovPort').value, 10) || 80;
      site.password = document.getElementById('ovPassword').value;
    }
    return site;
  }

  async function persistConfig() {
    const res = await postAction('save_config', { config: OV_CONFIG });
    if (!res || !res.ok) {
      setMsg((res && res.error) || L('save_fail', 'Save failed'), true);
      return false;
    }
    if (res.config) OV_CONFIG = res.config;
    if (res.data) applyLive(res.data);
    setMsg(L('saved_ok', 'Saved'), false);
    renderSiteList();
    return true;
  }

  async function deleteSite(i) {
    const s = (OV_CONFIG.sites || [])[i];
    if (isLocal(s)) {
      setMsg(L('cannot_delete_local', 'Cannot delete'), true);
      return;
    }
    if (!confirm(L('delete_confirm', 'Delete?'))) return;
    (OV_CONFIG.sites || []).splice(i, 1);
    editor.classList.remove('open');
    await persistConfig();
  }

  document.getElementById('ovBtnSettings').onclick = openPanel;
  document.getElementById('ovBtnClosePanel').onclick = closePanel;
  backdrop.onclick = closePanel;
  document.getElementById('ovBtnCancelEdit').onclick = () => {
    editor.classList.remove('open');
    editIndex = -1;
  };
  document.getElementById('ovBtnAdd').onclick = async () => {
    const res = await postAction('default_site', { port: 80 });
    const site = (res && res.site) || { kind: 'app', enabled: true, host: '', port: 80, password: '' };
    if (!OV_CONFIG.sites) OV_CONFIG.sites = [];
    OV_CONFIG.sites.push(site);
    openEditor(OV_CONFIG.sites.length - 1);
  };
  document.getElementById('ovBtnSaveSite').onclick = async () => {
    const site = readEditorSite();
    if (editIndex < 0) return;
    OV_CONFIG.sites[editIndex] = site;
    if (await persistConfig()) editor.classList.remove('open');
  };
  document.getElementById('ovBtnReload').onclick = async () => {
    await postAction('reload', {});
  };
  if (chkActive) {
    chkActive.onchange = async () => {
      const on = !!chkActive.checked;
      const res = await postAction('set_active_antenna_only', { value: on });
      if (res && res.ok && res.data) applyLive(res.data);
      else if (res && !res.ok) chkActive.checked = !on;
    };
  }

  let lastConfigRev = -1;
  function applyLive(data) {
    if (!data) return;
    if (typeof data.dark_mode === 'boolean') window.setOverviewDarkMode(data.dark_mode);
    if (data.sites) window.updateOverviewSites(data.sites);
    if (statusEl && data.status_text) statusEl.textContent = data.status_text;
    let activeOnly = null;
    if (typeof data.show_active_antenna_only === 'boolean') {
      activeOnly = data.show_active_antenna_only;
    } else if (data.config && typeof data.config.show_active_antenna_only === 'boolean') {
      activeOnly = data.config.show_active_antenna_only;
    }
    if (chkActive && activeOnly !== null && chkActive.checked !== activeOnly) {
      chkActive.checked = activeOnly;
    }
    if (data.config && typeof data.revision === 'number' && data.revision !== lastConfigRev) {
      lastConfigRev = data.revision;
      OV_CONFIG = data.config;
      if (panel.classList.contains('open') && !editor.classList.contains('open')) {
        renderSiteList();
      }
    }
  }

  async function pollOnce() {
    try {
      const res = await api('/api/overview');
      if (res && res.ok) applyLive(res.data || {});
    } catch (e) {}
  }

  if (typeof EventSource !== 'undefined') {
    try {
      const es = new EventSource('/api/events');
      es.addEventListener('overview', (ev) => {
        try { applyLive(JSON.parse(ev.data)); } catch (e) {}
      });
      es.onerror = () => {};
    } catch (e) {
      setInterval(pollOnce, 1000);
    }
  } else {
    setInterval(pollOnce, 1000);
  }
  setInterval(pollOnce, 2000);

  setInterval(() => {
    try {
      const v = window.getOverviewView();
      if (v) postAction('save_view', v);
    } catch (e) {}
  }, 15000);
})();
"""
    return js.replace("__STATUS_TEXT__", status_text_json)
