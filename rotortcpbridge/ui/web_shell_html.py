"""Gemeinsame Web-Seite: Karte und Kompass als umschaltbare Ansichten.

Die Web-Serial-Verbindung zum Display-Controller lebt in dieser Hülle. Karte und Kompass
laufen als iframes (``?embed=1``) und bleiben beim Umschalten erhalten, daher muss der
Controller beim Wechsel nicht neu verbunden werden.
"""

from __future__ import annotations

import json

from ..i18n import t
from .web_serial_js import web_serial_js


def web_shell_labels() -> dict:
    return {
        "map": t("map.web_tab_map"),
        "compass": t("map.web_open_compass"),
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
      background:__PANEL__; border-bottom:1px solid __BORDER__; min-height:38px; }
    #bar button { font:13px/1.2 sans-serif; padding:6px 12px; border-radius:6px; cursor:pointer;
      border:1px solid __BORDER__; background:__BTN__; color:__FG__; touch-action:manipulation; }
    #bar button.active { background:#2563eb; border-color:#2563eb; color:#fff; font-weight:700; }
    #spacer { flex:1 1 auto; }
    #usbDot { width:10px; height:10px; border-radius:50%; background:#6b7280; display:none; }
    #usbDot.on { display:inline-block; background:#22c55e; }
    #usbDot.wait { display:inline-block; background:#f59e0b; }
    #usbMsg { font-size:11px; color:#fca5a5; max-width:46vw; overflow:hidden; text-overflow:ellipsis;
      white-space:nowrap; display:none; }
    #usbHttps { font-size:11px; font-weight:700; color:#7dd3fc; display:none; }
    #views { flex:1 1 auto; min-height:0; position:relative; }
    #views iframe { position:absolute; inset:0; width:100%; height:100%; border:0; display:none;
      background:__BG__; }
    #views iframe.show { display:block; }
  </style>
</head>
<body>
  <div id="bar">
    <button type="button" id="tabMap">Karte</button>
    <button type="button" id="tabCompass">Kompass</button>
    <span id="spacer"></span>
    <span id="usbMsg"></span>
    <a id="usbHttps" href="#"></a>
    <span id="usbDot"></span>
    <button type="button" id="btnUsb">USB verbinden</button>
  </div>
  <div id="views">
    <iframe id="fMap" title="map"></iframe>
    <iframe id="fCompass" title="compass"></iframe>
  </div>
<script>
__WEB_SERIAL_JS__
(function() {
  const L = __LABELS__;
  const VIEWS = { map: '/map?embed=1', compass: '/compass?embed=1' };
  const frames = { map: document.getElementById('fMap'), compass: document.getElementById('fCompass') };
  const tabs = { map: document.getElementById('tabMap'), compass: document.getElementById('tabCompass') };
  const btnUsb = document.getElementById('btnUsb');
  const dot = document.getElementById('usbDot');
  const msg = document.getElementById('usbMsg');
  const httpsLink = document.getElementById('usbHttps');
  const WS = window.WebSerial;
  let busIds = null;

  tabs.map.textContent = L.map || 'Karte';
  tabs.compass.textContent = L.compass || 'Kompass';
  btnUsb.textContent = L.connect || 'USB verbinden';

  function showView(v) {
    if (!VIEWS[v]) v = 'map';
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
    // Leaflet/Canvas brauchen nach dem Einblenden eine Größenanpassung
    try {
      const w = frames[v].contentWindow;
      if (w) setTimeout(function() { w.dispatchEvent(new Event('resize')); }, 60);
    } catch (e) {}
  }
  tabs.map.addEventListener('click', function() { showView('map'); });
  tabs.compass.addEventListener('click', function() { showView('compass'); });
  window.addEventListener('message', function(ev) {
    if (ev.origin !== window.location.origin) return;
    const d = ev.data;
    if (d && typeof d === 'object' && d.rotorView) showView(String(d.rotorView));
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
  /* Profilwechsel (neue cont_id/pc_id): IDs nach Iframe-Reload und periodisch nachziehen,
     sonst filtert Web Serial die Frames des Displays mit der neuen ID weg. */
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
    /* requestPort synchron im Klick (User-Gesture) */
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
      if (err && (err.name === 'NotFoundError' || err.name === 'AbortError')) return; // Dialog abgebrochen
      const m = String((err && err.message) || err);
      setMsg(m.split('\n')[0], m);
    });
  });

  /* Nach Reload (Strg+F5): bereits erlaubten Port ohne Dialog wieder öffnen */
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
