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
{web_css}
</style>
</head><body class="{body_cls}">
{web_chrome}
<div id="map"></div>
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
{web_js}
</script>
</body></html>
"""


_WEB_CSS = """
body.web-ov { display:flex; flex-direction:column; }
body.web-ov #map { flex:1 1 auto; min-height:0; }
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
    <div class="row"><label id="ovLblProfile"></label><select id="ovProfile"></select><button type="button" id="ovBtnImport"></button></div>
    <div class="row"><label id="ovLblName"></label><input id="ovName" type="text"/></div>
    <div class="row"><label><input id="ovEnabled" type="checkbox"/> <span id="ovLblEnabled"></span></label></div>
    <div class="row"><label id="ovLblLat"></label><input id="ovLat" type="number" step="0.000001"/></div>
    <div class="row"><label id="ovLblLon"></label><input id="ovLon" type="number" step="0.000001"/></div>
    <div class="row"><label id="ovLblLocator"></label><input id="ovLocator" type="text"/><button type="button" id="ovBtnLocator"></button></div>
    <div class="row"><label id="ovLblMode"></label><select id="ovMode"><option value="tcp">TCP</option><option value="udp">UDP</option></select></div>
    <div class="row"><label id="ovLblHost"></label><input id="ovHost" type="text"/></div>
    <div class="row"><label id="ovLblPort"></label><input id="ovPort" type="number" min="1" max="65535"/></div>
    <div class="row"><label id="ovLblUdp"></label><input id="ovUdpBind" type="number" min="0" max="65535"/></div>
    <div class="row"><label id="ovLblMaster"></label><input id="ovMaster" type="number" min="0" max="254"/></div>
    <div class="row"><label id="ovLblSlave"></label><input id="ovSlave" type="number" min="1" max="254"/></div>
    <div class="row"><label id="ovLblPollPos"></label><input id="ovPollPos" type="number" min="200" max="60000"/></div>
    <div class="row"><label id="ovLblPollRef"></label><input id="ovPollRef" type="number" min="1000" max="120000"/></div>
    <div class="row"><label id="ovLblSmooth"></label><input id="ovSmooth" type="number" min="0.05" max="1" step="0.05"/></div>
    <h3 id="ovAntennasTitle"></h3>
    <div id="ovAntennas"></div>
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
  document.getElementById('ovLblProfile').textContent = L('import_profile', 'Profile');
  document.getElementById('ovBtnImport').textContent = L('btn_import_profile', 'Import');
  document.getElementById('ovLblName').textContent = L('field_name', 'Name');
  document.getElementById('ovLblEnabled').textContent = L('field_enabled', 'Enabled');
  document.getElementById('ovLblLat').textContent = L('field_lat', 'Lat');
  document.getElementById('ovLblLon').textContent = L('field_lon', 'Lon');
  document.getElementById('ovLblLocator').textContent = L('field_locator', 'Locator');
  document.getElementById('ovBtnLocator').textContent = L('btn_locator_apply', 'Locator');
  document.getElementById('ovLblMode').textContent = L('field_mode', 'Mode');
  document.getElementById('ovLblHost').textContent = L('field_host', 'Host');
  document.getElementById('ovLblPort').textContent = L('field_port', 'Port');
  document.getElementById('ovLblUdp').textContent = L('field_udp_bind', 'UDP bind');
  document.getElementById('ovLblMaster').textContent = L('field_master', 'Master');
  document.getElementById('ovLblSlave').textContent = L('field_slave_az', 'Slave');
  document.getElementById('ovLblPollPos').textContent = L('field_poll_pos', 'Poll POS');
  document.getElementById('ovLblPollRef').textContent = L('field_poll_ref', 'Poll REF');
  document.getElementById('ovLblSmooth').textContent = L('field_smooth', 'Smooth');
  document.getElementById('ovAntennasTitle').textContent = L('antennas_group', 'Antennas');
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
    fillProfiles();
    panel.classList.add('open');
    backdrop.classList.add('open');
  }
  function closePanel() {
    panel.classList.remove('open');
    backdrop.classList.remove('open');
    editor.classList.remove('open');
    editIndex = -1;
  }

  function fillProfiles() {
    const sel = document.getElementById('ovProfile');
    sel.innerHTML = '';
    const o0 = document.createElement('option');
    o0.value = '';
    o0.textContent = L('profile_none', '—');
    sel.appendChild(o0);
    (PROFILES || []).forEach(p => {
      const o = document.createElement('option');
      o.value = p.id;
      o.textContent = p.name + (p.active ? L('profile_active_suffix', '') : '');
      sel.appendChild(o);
    });
  }

  function renderSiteList() {
    const ul = document.getElementById('ovSiteList');
    ul.innerHTML = '';
    (OV_CONFIG.sites || []).forEach((s, i) => {
      const li = document.createElement('li');
      const span = document.createElement('span');
      const en = s.enabled ? L('enabled_yes', 'on') : L('enabled_no', 'off');
      span.textContent = (s.name || '') + ' · ' + en + ' · ' + (s.host || '');
      const bEdit = document.createElement('button');
      bEdit.textContent = L('btn_edit', 'Edit');
      bEdit.onclick = () => openEditor(i);
      const bDel = document.createElement('button');
      bDel.textContent = L('btn_delete', 'Delete');
      bDel.onclick = () => deleteSite(i);
      li.appendChild(span);
      li.appendChild(bEdit);
      li.appendChild(bDel);
      ul.appendChild(li);
    });
  }

  function defaultAntennas() {
    return [1,2,3].map((n, i) => ({
      enabled: i === 0,
      name: L('antenna_' + n, 'Ant. ' + n),
      offset_deg: 0,
      opening_deg: 30,
      range_km: 100,
      dipole: false,
      color: ['#5BA3D0','#E67E22','#E74C3C'][i]
    }));
  }

  function openEditor(idx) {
    editIndex = idx;
    editSite = JSON.parse(JSON.stringify((OV_CONFIG.sites || [])[idx] || {}));
    if (!editSite.antennas) editSite.antennas = defaultAntennas();
    document.getElementById('ovName').value = editSite.name || '';
    document.getElementById('ovEnabled').checked = !!editSite.enabled;
    document.getElementById('ovLat').value = editSite.lat;
    document.getElementById('ovLon').value = editSite.lon;
    document.getElementById('ovLocator').value = editSite.locator || '';
    document.getElementById('ovMode').value = editSite.mode || 'tcp';
    document.getElementById('ovHost').value = editSite.host || '';
    document.getElementById('ovPort').value = editSite.port || 8886;
    document.getElementById('ovUdpBind').value = editSite.udp_bind_port || 0;
    document.getElementById('ovMaster').value = editSite.master_id || 0;
    document.getElementById('ovSlave').value = editSite.slave_az || 20;
    document.getElementById('ovPollPos').value = editSite.poll_pos_ms || 1000;
    document.getElementById('ovPollRef').value = editSite.poll_ref_ms || 5000;
    document.getElementById('ovSmooth').value = editSite.smooth_alpha || 0.35;
    const box = document.getElementById('ovAntennas');
    box.innerHTML = '';
    for (let i = 0; i < 3; i++) {
      const a = editSite.antennas[i] || defaultAntennas()[i];
      const row = document.createElement('div');
      row.className = 'ant-row';
      row.innerHTML =
        '<label><input type="checkbox" data-k="enabled"' + (a.enabled ? ' checked' : '') + '/> ' + L('antenna_' + (i+1), 'A'+(i+1)) + '</label>' +
        '<input type="text" data-k="name" value="' + String(a.name||'').replace(/"/g,'&quot;') + '"/>' +
        '<label class="dip" title="' + String(L('ant_dipole_tooltip','')).replace(/"/g,'&quot;') + '"><input type="checkbox" data-k="dipole"' + (a.dipole ? ' checked' : '') + '/> ' + L('ant_dipole','Dipol') + '</label>' +
        '<input type="number" data-k="offset_deg" step="0.1" title="' + L('ant_offset','') + '" value="' + (a.offset_deg||0) + '"/>' +
        '<input type="number" data-k="opening_deg" step="0.1" title="' + L('ant_opening','') + '" value="' + (a.opening_deg||30) + '"/>' +
        '<input type="number" data-k="range_km" step="0.1" title="' + L('ant_range','') + '" value="' + (a.range_km||100) + '"/>' +
        '<input type="color" data-k="color" value="' + (a.color || '#5BA3D0') + '" title="' + L('pick_color','') + '"/>';
      box.appendChild(row);
    }
    editor.classList.add('open');
  }

  function readEditorSite() {
    const ants = [];
    document.querySelectorAll('#ovAntennas .ant-row').forEach(row => {
      const get = (k) => row.querySelector('[data-k="'+k+'"]');
      ants.push({
        enabled: !!get('enabled').checked,
        name: get('name').value.trim() || 'Antenne',
        dipole: !!(get('dipole') && get('dipole').checked),
        offset_deg: parseFloat(get('offset_deg').value) || 0,
        opening_deg: parseFloat(get('opening_deg').value) || 30,
        range_km: parseFloat(get('range_km').value) || 100,
        color: get('color').value || '#5BA3D0'
      });
    });
    const site = Object.assign({}, editSite || {});
    site.name = document.getElementById('ovName').value.trim() || 'Standort';
    site.enabled = document.getElementById('ovEnabled').checked;
    site.lat = parseFloat(document.getElementById('ovLat').value);
    site.lon = parseFloat(document.getElementById('ovLon').value);
    site.locator = document.getElementById('ovLocator').value.trim();
    site.mode = document.getElementById('ovMode').value || 'tcp';
    site.host = document.getElementById('ovHost').value.trim();
    site.port = parseInt(document.getElementById('ovPort').value, 10) || 8886;
    site.udp_bind_port = parseInt(document.getElementById('ovUdpBind').value, 10) || 0;
    site.master_id = parseInt(document.getElementById('ovMaster').value, 10) || 0;
    site.slave_az = parseInt(document.getElementById('ovSlave').value, 10) || 20;
    site.poll_pos_ms = parseInt(document.getElementById('ovPollPos').value, 10) || 1000;
    site.poll_ref_ms = parseInt(document.getElementById('ovPollRef').value, 10) || 5000;
    site.smooth_alpha = parseFloat(document.getElementById('ovSmooth').value) || 0.35;
    site.antennas = ants;
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
    const res = await postAction('default_site', {});
    const site = (res && res.site) || { name: L('new_site', 'New'), enabled: true, antennas: defaultAntennas() };
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
  document.getElementById('ovBtnLocator').onclick = async () => {
    const res = await postAction('locator', { locator: document.getElementById('ovLocator').value });
    if (!res || !res.ok) {
      setMsg(L('locator_invalid', 'Invalid locator'), true);
      return;
    }
    document.getElementById('ovLat').value = res.lat;
    document.getElementById('ovLon').value = res.lon;
    setMsg('', false);
  };
  document.getElementById('ovBtnImport').onclick = async () => {
    const pid = document.getElementById('ovProfile').value;
    if (!pid) return;
    const res = await postAction('import_profile', { profile_id: pid });
    if (!res || !res.ok || !res.site) {
      setMsg((res && res.error) || 'import failed', true);
      return;
    }
    const keepId = editSite && editSite.id;
    editSite = res.site;
    if (keepId) editSite.id = keepId;
    openEditor(editIndex);
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
