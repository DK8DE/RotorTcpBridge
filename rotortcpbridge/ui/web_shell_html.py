"""Gemeinsame Web-Seite: Karte, Kompass und Rotoren als umschaltbare Ansichten.

Die Web-Serial-Verbindung zum Display-Controller lebt in dieser Hülle. Die drei Ansichten
laufen als iframes (``?embed=1``) und bleiben beim Umschalten erhalten.
"""

from __future__ import annotations

import json

from ..i18n import t
from .web_serial_js import web_serial_js


def web_shell_labels() -> dict:
    return {
        "map": t("map.web_tab_map"),
        "compass": t("map.web_open_compass"),
        "overview": t("map.web_open_overview"),
        "sites": t("overview.menu_sites"),
        "reload": t("overview.menu_reload"),
        "active_antenna": t("overview.chk_active_antenna"),
        "active_antenna_tip": t("overview.chk_active_antenna_tooltip"),
        "connect": t("map.web_serial_connect"),
        "disconnect": t("map.web_serial_disconnect"),
        "error_title": t("map.web_serial_error_title"),
        "unsupported": t("map.web_serial_unsupported"),
        "open_https": t("map.web_serial_open_https"),
    }


_TEMPLATE = r"""<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
  <title>RotorTcpBridge</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    html, body { width:100%; height:100%; overflow:hidden; background:__BG__; color:__FG__;
      font:13px/1.35 sans-serif; -webkit-text-size-adjust:100%; }
    body { display:flex; flex-direction:column; }
    #bar { flex:0 0 auto; display:flex; align-items:center; gap:6px; padding:4px 6px;
      background:__PANEL__; border-bottom:1px solid __BORDER__; height:38px; min-height:38px; }
    #bar .navTabs, #bar #ovActions {
      display:flex; align-items:center; gap:6px; flex:0 0 auto;
    }
    #bar #ovActions { display:none; }
    body.view-overview #bar #ovActions { display:flex; }
    /* Tabs + Aktionen + USB: gleiche Größe; Hover = blau / weiße Schrift */
    #bar button.navTab, #bar button.ovAction, #bar #btnUsb {
      box-sizing: border-box;
      font:700 13px/1.2 sans-serif;
      height:30px; min-height:30px; max-height:30px;
      padding:0 12px; border-radius:6px; cursor:pointer;
      border:1px solid __BORDER__; background:__BTN__; color:__FG__;
      touch-action:manipulation; display:inline-flex; align-items:center; justify-content:center;
      -webkit-appearance:none; appearance:none; white-space:nowrap;
    }
    #bar button.navTab { min-width:96px; }
    #bar button.navTab.active { background:#2563eb; border-color:#2563eb; color:#fff; }
    #bar button.navTab:hover, #bar button.ovAction:hover, #bar #btnUsb:hover {
      background:#2563eb; border-color:#2563eb; color:#fff;
    }
    #bar #ovChkActiveWrap {
      display:flex; align-items:center; gap:6px; font-size:12px; cursor:pointer;
      user-select:none; white-space:nowrap; color:__FG__; padding:0 4px;
    }
    #bar #ovChkActiveWrap input { margin:0; }
    #spacer { flex:1 1 auto; }
    #usbDot { width:10px; height:10px; border-radius:50%; background:#6b7280; display:none; }
    #usbDot.on { display:inline-block; background:#22c55e; }
    #usbDot.wait { display:inline-block; background:#f59e0b; }
    #usbMsg { font-size:11px; color:#fca5a5; max-width:46vw; overflow:hidden; text-overflow:ellipsis;
      white-space:nowrap; display:none; }
    #usbHttps { font-size:11px; font-weight:700; color:#7dd3fc; display:none; text-decoration:none; }
    #views { flex:1 1 auto; min-height:0; position:relative; }
    #views iframe { position:absolute; inset:0; width:100%; height:100%; border:0; display:none;
      background:__BG__; }
    #views iframe.show { display:block; }
  </style>
</head>
<body>
  <div id="bar">
    <div class="navTabs">
      <button type="button" class="navTab" id="tabMap" data-view="map">Karte</button>
      <button type="button" class="navTab" id="tabCompass" data-view="compass">Kompass</button>
      <button type="button" class="navTab" id="tabOverview" data-view="overview">Rotoren</button>
    </div>
    <div id="ovActions">
      <button type="button" class="ovAction" id="ovBtnSettings">Standorte…</button>
      <button type="button" class="ovAction" id="ovBtnReload">Verbindungen neu starten</button>
      <label id="ovChkActiveWrap" title="">
        <input type="checkbox" id="ovChkActiveAnt"/>
        <span id="ovChkActiveLabel">Nur aktive Antenne</span>
      </label>
    </div>
    <span id="spacer"></span>
    <span id="usbMsg"></span>
    <a id="usbHttps" href="#"></a>
    <span id="usbDot"></span>
    <button type="button" id="btnUsb">USB verbinden</button>
  </div>
  <div id="views">
    <iframe id="fMap" title="map"></iframe>
    <iframe id="fCompass" title="compass"></iframe>
    <iframe id="fOverview" title="overview"></iframe>
  </div>
<script>
__WEB_SERIAL_JS__
(function() {
  const L = __LABELS__;
  const VIEWS = {
    map: '/map?embed=1',
    compass: '/compass?embed=1',
    overview: '/rotoren?embed=1'
  };
  const frames = {
    map: document.getElementById('fMap'),
    compass: document.getElementById('fCompass'),
    overview: document.getElementById('fOverview')
  };
  const tabs = {
    map: document.getElementById('tabMap'),
    compass: document.getElementById('tabCompass'),
    overview: document.getElementById('tabOverview')
  };
  const btnUsb = document.getElementById('btnUsb');
  const dot = document.getElementById('usbDot');
  const msg = document.getElementById('usbMsg');
  const httpsLink = document.getElementById('usbHttps');
  const WS = window.WebSerial;
  let busIds = null;

  tabs.map.textContent = L.map || 'Karte';
  tabs.compass.textContent = L.compass || 'Kompass';
  tabs.overview.textContent = L.overview || 'Rotoren';
  btnUsb.textContent = L.connect || 'USB verbinden';
  const ovBtnSettings = document.getElementById('ovBtnSettings');
  const ovBtnReload = document.getElementById('ovBtnReload');
  const ovChkActive = document.getElementById('ovChkActiveAnt');
  const ovChkLabel = document.getElementById('ovChkActiveLabel');
  const ovChkWrap = document.getElementById('ovChkActiveWrap');
  if (ovBtnSettings) ovBtnSettings.textContent = L.sites || 'Standorte…';
  if (ovBtnReload) ovBtnReload.textContent = L.reload || 'Verbindungen neu starten';
  if (ovChkLabel) ovChkLabel.textContent = L.active_antenna || 'Nur aktive Antenne';
  if (ovChkWrap) ovChkWrap.title = L.active_antenna_tip || '';

  let pendingOvMsg = null;
  function overviewReady() {
    try {
      const f = frames.overview;
      return !!(f && f.getAttribute('src')
        && f.contentDocument && f.contentDocument.readyState === 'complete');
    } catch (e) { return false; }
  }
  function postToOverview(msg) {
    pendingOvMsg = msg;
    if (!overviewReady()) return;
    try {
      frames.overview.contentWindow.postMessage(msg, window.location.origin);
      pendingOvMsg = null;
    } catch (e) {}
  }
  frames.overview.addEventListener('load', function() {
    if (!pendingOvMsg) return;
    const m = pendingOvMsg;
    pendingOvMsg = null;
    try { frames.overview.contentWindow.postMessage(m, window.location.origin); } catch (e) {}
  });

  function showView(v) {
    if (!VIEWS[v]) v = 'map';
    document.body.classList.toggle('view-overview', v === 'overview');
    Object.keys(frames).forEach(function(k) {
      const f = frames[k];
      if (k === v) {
        if (!f.getAttribute('src')) f.setAttribute('src', VIEWS[k]);
        f.classList.add('show');
      } else {
        f.classList.remove('show');
      }
      tabs[k].classList.toggle('active', k === v);
    });
    try { localStorage.setItem('rotorWebView', v); } catch (e) {}
    try { if (location.hash !== '#' + v) history.replaceState(null, '', '#' + v); } catch (e) {}
    try {
      const w = frames[v].contentWindow;
      if (w) setTimeout(function() { w.dispatchEvent(new Event('resize')); }, 60);
    } catch (e) {}
  }
  Object.keys(tabs).forEach(function(k) {
    tabs[k].addEventListener('click', function() { showView(k); });
  });
  if (ovBtnSettings) {
    ovBtnSettings.addEventListener('click', function() {
      postToOverview({ rotorOverviewAction: 'settings' });
    });
  }
  if (ovBtnReload) {
    ovBtnReload.addEventListener('click', function() {
      postToOverview({ rotorOverviewAction: 'reload' });
    });
  }
  if (ovChkActive) {
    ovChkActive.addEventListener('change', function() {
      postToOverview({
        rotorOverviewAction: 'set_active_antenna_only',
        value: !!ovChkActive.checked
      });
    });
  }
  window.addEventListener('message', function(ev) {
    if (ev.origin !== window.location.origin) return;
    const d = ev.data;
    if (!d || typeof d !== 'object') return;
    if (d.rotorView) showView(String(d.rotorView));
    if (d.rotorOverviewState && typeof d.rotorOverviewState.active_antenna_only === 'boolean') {
      if (ovChkActive) ovChkActive.checked = !!d.rotorOverviewState.active_antenna_only;
    }
  });
  let first = (location.hash || '').replace('#', '');
  if (!VIEWS[first]) {
    try { first = localStorage.getItem('rotorWebView') || 'map'; } catch (e) { first = 'map'; }
  }
  showView(first);

  function loadIds() {
    return fetch('/api/compass', { cache: 'no-store' })
      .then(function(r) { return r.json(); })
      .then(function(j) {
        const d = (j && j.data) || {};
        busIds = d.bus || null;
        if (busIds) {
          WS.setIds({
            pc_id: busIds.pc_id, master_id: busIds.master_id, cont_id: busIds.cont_id,
            az_id: busIds.az_id, el_id: busIds.el_id,
            enable_az: d.enable_az !== false, enable_el: !!d.enable_el
          });
        }
      }).catch(function() {});
  }
  loadIds();
  Object.keys(frames).forEach(function(k) {
    frames[k].addEventListener('load', function() { loadIds(); });
  });
  setInterval(function() { loadIds(); }, 4000);

  function setMsg(text, title) {
    msg.textContent = text || '';
    msg.title = title || text || '';
    msg.style.display = text ? 'inline' : 'none';
  }
  function updateUsb(st) {
    st = st || {};
    btnUsb.textContent = st.connected ? (L.disconnect || 'USB trennen') : (L.connect || 'USB verbinden');
    dot.className = '';
    if (st.connected) dot.className = st.busReady ? 'on' : 'wait';
    if (st.connected && st.lastError) setMsg(st.lastError);
    else if (!st.connected && st.supported === false) setMsg(L.unsupported || 'Web Serial nur in Chrome/Edge');
    else if (!st.connected && st.secure === false) {
      setMsg('HTTPS nötig für USB');
    } else setMsg('');
    if (st.secure === false) {
      try {
        const u = new URL(window.location.href);
        u.protocol = 'https:';
        u.hash = '';
        httpsLink.href = u.toString();
        httpsLink.textContent = L.open_https || 'HTTPS öffnen';
        httpsLink.style.display = 'inline';
      } catch (e) {}
    } else {
      httpsLink.style.display = 'none';
    }
  }
  WS.setCallbacks({ onStatus: updateUsb });
  updateUsb({ connected: false, supported: WS.supported(), secure: WS.secureOk() });

  btnUsb.addEventListener('click', function() {
    if (WS.isLocal()) {
      try { sessionStorage.removeItem('rotorWebUsb'); } catch (e) {}
      WS.disconnect();
      return;
    }
    let portPromise;
    try {
      portPromise = WS.requestPortFromUser();
    } catch (err) {
      setMsg(String((err && err.message) || err).split('\n')[0], String((err && err.message) || err));
      return;
    }
    loadIds();
    portPromise.then(function(port) {
      if (!port) return;
      return WS.attachPort(port).then(function() {
        try { sessionStorage.setItem('rotorWebUsb', '1'); } catch (e) {}
      });
    }).catch(function(err) {
      if (err && (err.name === 'NotFoundError' || err.name === 'AbortError')) return;
      const m = String((err && err.message) || err);
      setMsg(m.split('\n')[0], m);
    });
  });

  try {
    if (WS.supported() && WS.secureOk() && sessionStorage.getItem('rotorWebUsb') === '1') {
      navigator.serial.getPorts().then(function(ports) {
        if (ports && ports.length && !WS.isLocal()) {
          loadIds().then(function() { return WS.attachPort(ports[0]); }).catch(function() {});
        }
      }).catch(function() {});
    }
  } catch (e) {}
})();
</script>
</body>
</html>
"""


def build_web_shell_html(dark: bool = True, labels: dict | None = None) -> str:
    bg = "#1c1c1c" if dark else "#f0f0f0"
    fg = "#eaeaea" if dark else "#1a1a1a"
    panel = "rgba(28,28,30,0.96)" if dark else "rgba(255,255,255,0.96)"
    border = "rgba(180,180,190,0.3)" if dark else "rgba(128,128,128,0.4)"
    btn = "rgba(42,42,46,0.9)" if dark else "rgba(255,255,255,0.9)"
    lab = labels if isinstance(labels, dict) else web_shell_labels()
    html = _TEMPLATE
    html = html.replace("__BG__", bg).replace("__FG__", fg)
    html = html.replace("__PANEL__", panel).replace("__BORDER__", border).replace("__BTN__", btn)
    html = html.replace("__LABELS__", json.dumps(lab, ensure_ascii=False))
    html = html.replace("__WEB_SERIAL_JS__", web_serial_js())
    return html


__all__ = ["build_web_shell_html", "web_shell_labels"]
