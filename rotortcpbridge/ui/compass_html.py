"""HTML/Canvas-Kompass für den LAN-Webserver (Parität zum Desktop-Kompass)."""

from __future__ import annotations

import base64
import json
from pathlib import Path


def _data_url_png(path: Path) -> str:
    try:
        if path.is_file():
            return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode("ascii")
    except OSError:
        pass
    return ""


def build_compass_html(params: dict) -> str:
    """Vollständige Kompass-Seite für den Browser."""
    dark = bool(params.get("dark_mode", True))
    init = params.get("compass_init") or {}
    chrome = params.get("web_chrome") or {}
    pkg = Path(__file__).resolve().parent.parent
    windrose = ""
    for name in ("Windrose.png", "windrose.png"):
        windrose = _data_url_png(pkg / name)
        if windrose:
            break
    wind_arrow = ""
    for name in ("windPfeil.png", "WindPfeil.png", "windpfeil.png"):
        wind_arrow = _data_url_png(pkg / name)
        if wind_arrow:
            break
    body_bg = "#1c1c1c" if dark else "#f0f0f0"
    fg = "#eaeaea" if dark else "#1a1a1a"
    panel_bg = "rgba(28,28,30,0.92)" if dark else "rgba(255,255,255,0.92)"
    border = "rgba(180,180,190,0.3)" if dark else "rgba(128,128,128,0.4)"
    init_json = json.dumps(init, ensure_ascii=False)
    chrome_json = json.dumps(chrome, ensure_ascii=False)
    windrose_js = json.dumps(windrose)
    wind_arrow_js = json.dumps(wind_arrow)

    return f"""<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
  <title>Kompass</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    html, body {{ width:100%; height:100%; overflow:hidden; background:{body_bg}; color:{fg};
      font:13px/1.35 sans-serif; -webkit-text-size-adjust:100%; }}
    #app {{ display:flex; flex-direction:column; height:100%; gap:6px; padding:6px; }}
    #main {{ flex:1 1 auto; min-height:0; display:flex; flex-direction:row; gap:6px; }}
    .block {{ display:flex; flex-direction:row; gap:6px; min-width:0; min-height:0; }}
    #azBlock {{ flex:1.25 1 0; }}
    #elBlock {{ flex:1 1 0; display:none; }}
    body.el-on #elBlock {{ display:flex; }}
    #azLeftCol {{
      flex:0 0 158px; width:158px; min-width:140px; max-width:180px;
      display:flex; flex-direction:column; gap:6px; overflow:auto; min-height:0;
    }}
    .side {{
      display:flex; flex-direction:column; gap:6px; min-width:0; min-height:0;
    }}
    #azLeftTop, #azLeftBottom {{ flex:0 0 auto; }}
    #azRight, #elRight {{
      flex:0 0 168px; width:168px; min-width:140px; max-width:180px; overflow:auto;
    }}
    .canvasCol {{ flex:1 1 auto; min-width:0; min-height:0; display:flex; flex-direction:column; }}
    .canvasWrap {{ flex:1 1 auto; min-height:160px; position:relative;
      border:1px solid {border}; border-radius:10px; background:{panel_bg}; overflow:hidden; }}
    canvas {{ display:block; width:100%; height:100%; touch-action:none; }}
    .card {{
      display:flex; flex-direction:column; gap:5px; padding:8px;
      border:1px solid {border}; border-radius:8px; background:{panel_bg}; flex:0 0 auto;
    }}
    .cardHdr {{
      font-size:11px; color:#aaa; font-weight:700; letter-spacing:1px;
      text-align:center; text-transform:uppercase;
    }}
    .val {{
      font-size:26px; font-weight:700; text-align:center; line-height:1.15;
      background:rgba(30,30,30,0.55); border-radius:5px; padding:6px 4px;
    }}
    .val.soll {{ color:#ff6b6b; }}
    .val.ist {{ color:#5ee07a; }}
    .val.rev {{ color:#da9a5c; }}
    .val.wind {{ color:#5eb8ff; }}
    .row {{ display:flex; gap:4px; align-items:center; }}
    .row > * {{ flex:1 1 0; min-width:0; }}
    .statusRow {{
      display:flex; align-items:center; gap:6px; padding:4px 6px;
      background:rgba(30,30,30,0.55); border-radius:5px;
    }}
    .statusRow span:last-child {{ font-weight:700; }}
    .windRow {{
      display:flex; flex-direction:column; align-items:center; justify-content:center; gap:6px;
    }}
    .windSpeed {{ display:flex; align-items:baseline; justify-content:center; gap:4px; width:100%; }}
    .windSpeed .val {{ flex:1; }}
    .unit {{ font-size:13px; color:#aaa; }}
    #azLeftCol a, #azLeftCol button, #azLeftCol select, #azLeftCol input,
    .side a, .side button, .side select, .side input {{
      font:12px/1.2 sans-serif; padding:6px 8px; border-radius:6px; border:1px solid {border};
      background:rgba(40,40,42,0.9); color:{fg}; width:100%;
    }}
    #azLeftCol a, .side a {{ text-decoration:none; display:block; text-align:center; font-weight:600; }}
    #azLeftCol button, .side button {{ cursor:pointer; }}
    #azLeftCol label.chk, .side label.chk {{
      display:flex; align-items:center; gap:6px; white-space:nowrap; font-size:12px;
    }}
    #azLeftCol label.chk input, .side label.chk input {{ width:auto; }}
    #azLeftCol form, .side form {{ margin:0; display:flex; }}
    #azLeftCol form input, .side form input {{ width:100%; }}
    .led {{ width:12px; height:12px; border-radius:50%; display:inline-block; background:#888; flex:0 0 auto; }}
    .led.on {{ background:#2ecc40; }}
    .led.off {{ background:#888; }}
    .led.blink {{ animation: blink 0.7s infinite; }}
    @keyframes blink {{ 0%,49% {{ background:#2ecc40; }} 50%,100% {{ background:#e74c3c; }} }}
    #azLeftCol input[type=number], #azLeftCol input[type=text], #azLeftCol input[type=search],
    .side input[type=number], .side input[type=text], .side input[type=search] {{
      -webkit-appearance:none; appearance:none; }}
    #geoPick {{
      position:fixed; z-index:1200; left:50%; top:50%; transform:translate(-50%,-50%);
      width:min(420px, calc(100vw - 24px)); max-height:min(70vh, 480px);
      display:none; flex-direction:column; gap:8px; padding:12px 14px; border-radius:10px;
      border:1px solid {border}; background:{panel_bg}; box-shadow:0 4px 18px rgba(0,0,0,0.35); }}
    #geoPickList {{ overflow:auto; max-height:min(50vh, 340px); display:flex; flex-direction:column; gap:4px; }}
    #geoPickList button {{ text-align:left; width:100%; padding:8px 10px; }}
    @media (max-width: 1100px) {{
      #main {{ flex-direction:column; overflow:auto; }}
      #azBlock {{
        display:flex; flex-direction:column; flex-wrap:nowrap; overflow:visible;
      }}
      #azLeftCol {{
        display:contents; width:auto; max-width:none; flex:none; overflow:visible;
      }}
      #azLeftTop {{
        order:1; flex:0 0 auto; width:100%; max-width:none;
        flex-direction:row; flex-wrap:wrap; overflow:visible;
      }}
      #azBlock > .canvasCol {{
        order:2; flex:0 0 auto; width:100%; min-height:42vh;
      }}
      #azLeftBottom {{
        order:3; flex:0 0 auto; width:100%; max-width:none;
        flex-direction:row; flex-wrap:wrap; overflow:visible;
      }}
      #azRight {{
        order:4; flex:0 0 auto; width:100%; max-width:none;
        flex-direction:row; flex-wrap:wrap; overflow:visible;
      }}
      #azLeftTop .card, #azLeftBottom .card, #azRight .card {{
        flex:1 1 150px; min-width:140px;
      }}
      .canvasWrap {{ min-height:42vh; }}
      #elBlock {{ flex-direction:column; }}
      #elBlock .canvasCol {{ flex:0 0 auto; width:100%; min-height:32vh; }}
      #elBlock .canvasWrap {{ min-height:32vh; }}
      #elRight {{
        flex:0 0 auto; width:100%; max-width:none;
        flex-direction:row; flex-wrap:wrap; overflow:visible;
      }}
      #elRight .card {{ flex:1 1 150px; min-width:140px; }}
    }}
  </style>
</head>
<body class="{'map-dark' if dark else ''}">
  <div id="app">
    <div id="main">
      <div class="block" id="azBlock">
        <div id="azLeftCol">
          <aside class="side" id="azLeftTop">
            <div class="card">
              <div class="cardHdr" id="lblSoll">Soll</div>
              <div class="val soll" id="azSollDisp">–</div>
              <div class="row">
                <input id="edAzSoll" type="number" step="0.1" />
                <button type="button" id="btnAzSoll">OK</button>
              </div>
            </div>
            <div class="card">
              <div class="cardHdr" id="lblIst">Ist</div>
              <div class="val ist" id="azIst">–</div>
              <div id="azIstRevWrap" style="display:none;">
                <div class="cardHdr" id="lblIstRev">Ist Reverse</div>
                <div class="val rev" id="azIstRev">–</div>
              </div>
            </div>
            <div class="card">
              <div class="cardHdr" id="lblLoc">Loc</div>
              <form id="locForm" action="#">
                <input id="edLoc" type="search" enterkeyhint="go" maxlength="10" autocomplete="off" placeholder="JN49…" />
              </form>
              <div class="cardHdr" id="lblOrt">Ort</div>
              <form id="placeForm" action="#">
                <input id="edPlace" type="search" enterkeyhint="search" autocomplete="off" placeholder="Ort…" />
              </form>
            </div>
            <div class="card">
              <div class="cardHdr" id="lblAnt">Antenne</div>
              <select id="selAnt"></select>
            </div>
          </aside>
          <aside class="side" id="azLeftBottom">
            <div class="card" id="windBox" style="display:none;">
              <div class="cardHdr" id="lblWind">Wind</div>
              <div class="windRow">
                <div class="windSpeed">
                  <span class="val wind" id="windKmh" style="padding:4px 6px;font-size:22px;">—</span>
                  <span class="unit">km/h</span>
                </div>
                <img id="windArr" width="36" height="36" alt="" style="display:none;" />
              </div>
            </div>
            <div class="card">
              <div class="cardHdr" id="lblDgcal">Korrekturwinkel</div>
              <div class="val" id="dgcalCur">–</div>
              <input id="edDgcal" type="number" step="0.1" />
              <div class="row">
                <button type="button" id="btnDgcalSave">Speichern</button>
                <button type="button" id="btnDgcalClear">Löschen</button>
              </div>
            </div>
          </aside>
        </div>
        <div class="canvasCol">
          <div class="canvasWrap"><canvas id="azCanvas"></canvas></div>
        </div>
        <aside class="side" id="azRight">
          <div class="card">
            <div class="cardHdr" id="lblConn">Verbindung</div>
            <div class="statusRow"><span class="led off" id="ledMoving"></span><span id="lblMoving">Moving</span></div>
            <div class="statusRow" id="refWrap"><span class="led off" id="ledRef"></span><span id="lblRef">Home</span></div>
            <div class="statusRow"><span class="led off" id="ledOnline"></span><span id="lblOnline">Online</span></div>
          </div>
          <div class="card">
            <div class="cardHdr" id="lblCtrl">Steuerung</div>
            <button type="button" id="btnStopAz">STOP AZ</button>
            <button type="button" id="btnRefAz">AZ Homing</button>
            <div class="cardHdr" id="lblHeat">Ringe</div>
            <label class="chk"><input type="checkbox" id="chkHeatStrom" /> <span id="lblHeatStrom">Strom</span></label>
            <label class="chk"><input type="checkbox" id="chkHeatOm" /> <span id="lblHeatOm">OM</span></label>
            <label class="chk"><input type="checkbox" id="chkHeatDwell" /> <span id="lblHeatDwell">Standzeit</span></label>
            <button type="button" id="btnDwellReset">Standzeit zurücksetzen</button>
            <a href="/map" id="btnBack">Zur Karte</a>
          </div>
          <div class="card">
            <div class="cardHdr" id="lblFav">Favoriten</div>
            <select id="selFav"></select>
            <input id="edFavName" type="text" maxlength="15" placeholder="Name" />
            <div class="row">
              <button type="button" id="btnFavSave">Save</button>
              <button type="button" id="btnFavDel">Delete</button>
            </div>
          </div>
          <div class="card">
            <div class="cardHdr" id="lblScan">SCAN</div>
            <input id="edScanA" type="number" step="0.1" placeholder="A" />
            <input id="edScanB" type="number" step="0.1" placeholder="B" />
            <button type="button" id="btnScan">Start</button>
          </div>
        </aside>
      </div>
      <div class="block" id="elBlock">
        <div class="canvasCol">
          <div class="canvasWrap"><canvas id="elCanvas"></canvas></div>
        </div>
        <aside class="side" id="elRight">
          <div class="card">
            <div class="cardHdr" id="lblElConn">Verbindung</div>
            <div class="statusRow"><span class="led off" id="ledElMoving"></span><span id="lblElMoving">Moving</span></div>
            <div class="statusRow" id="elRefWrap"><span class="led off" id="ledElRef"></span><span id="lblElRef">Home</span></div>
            <div class="statusRow"><span class="led off" id="ledElOnline"></span><span id="lblElOnline">Online</span></div>
          </div>
          <div class="card">
            <div class="cardHdr" id="lblElSoll">Soll</div>
            <div class="val soll" id="elSollDisp">–</div>
            <div class="row">
              <input id="edElSoll" type="number" step="0.1" />
              <button type="button" id="btnElSoll">OK</button>
            </div>
          </div>
          <div class="card">
            <div class="cardHdr" id="lblElIst">Ist</div>
            <div class="val ist" id="elIst">–</div>
          </div>
          <div class="card">
            <div class="cardHdr" id="lblElDgcal">Korrekturwinkel</div>
            <input id="edElDgcal" type="number" step="0.1" />
            <div class="row">
              <button type="button" id="btnElDgcalSave">Speichern</button>
              <button type="button" id="btnElDgcalClear">Löschen</button>
            </div>
          </div>
          <div class="card">
            <div class="cardHdr" id="lblElCtrl">Steuerung</div>
            <button type="button" id="btnStopEl">STOP EL</button>
            <button type="button" id="btnRefEl">EL Homing</button>
            <label class="chk"><input type="checkbox" id="chkHeatElStrom" /> <span id="lblHeatElStrom">Strom</span></label>
          </div>
        </aside>
      </div>
    </div>
  </div>
  <div id="geoPick">
    <div id="geoPickTitle">Ort auswählen</div>
    <div id="geoPickBody"></div>
    <div id="geoPickList"></div>
    <div style="display:flex;justify-content:flex-end;"><button type="button" id="geoPickCancel">Abbrechen</button></div>
  </div>
  <script>
    const INIT = {init_json};
    const CHROME0 = {chrome_json};
    const WINDROSE_URL = {windrose_js};
    const WIND_ARROW_URL = {wind_arrow_js};
    let state = Object.assign({{}}, INIT || {{}});
    let labels = (CHROME0 && CHROME0.labels) || {{}};
    let windroseImg = null;
    let windArrImg = null;
    if (WINDROSE_URL) {{
      windroseImg = new Image();
      windroseImg.src = WINDROSE_URL;
    }}
    if (WIND_ARROW_URL) {{
      windArrImg = new Image();
      windArrImg.src = WIND_ARROW_URL;
      const el = document.getElementById('windArr');
      if (el) {{ el.src = WIND_ARROW_URL; el.style.display = 'inline-block'; }}
    }}

    function api(action, extra) {{
      const body = Object.assign({{ action: action }}, extra || {{}});
      return fetch('/api/ui', {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify(body)
      }}).catch(function() {{}});
    }}
    function setLed(el, on, blink) {{
      if (!el) return;
      el.classList.remove('on','off','blink');
      if (blink) el.classList.add('blink');
      else el.classList.add(on ? 'on' : 'off');
    }}
    function wrapDeg(d) {{
      let x = Number(d) % 360;
      if (x < 0) x += 360;
      return x;
    }}
    function shortestDeltaDeg(a, b) {{
      let d = Number(b) - Number(a);
      while (d > 180) d -= 360;
      while (d < -180) d += 360;
      return d;
    }}
    let _needleAzIst = null;
    let _needleElIst = null;
    let _needleAnimStarted = false;
    function azIstForDraw(az) {{
      if (_needleAzIst != null) return _needleAzIst;
      return az.ist;
    }}
    function elIstForDraw(el) {{
      if (_needleElIst != null) return _needleElIst;
      return el.ist;
    }}
    function ensureNeedleAnimLoop() {{
      if (_needleAnimStarted) return;
      _needleAnimStarted = true;
      function frame() {{
        const az = (state && state.az) || {{}};
        const el = (state && state.el) || {{}};
        let dirty = false;
        if (az.ist != null) {{
          if (_needleAzIst == null) _needleAzIst = Number(az.ist);
          const d = shortestDeltaDeg(_needleAzIst, az.ist);
          if (Math.abs(d) > 0.04) {{
            _needleAzIst = wrapDeg(_needleAzIst + d * 0.42);
            dirty = true;
          }} else if (Math.abs(d) > 1e-6) {{
            _needleAzIst = Number(az.ist);
            dirty = true;
          }}
        }}
        if (state.enable_el && el.ist != null) {{
          if (_needleElIst == null) _needleElIst = Number(el.ist);
          const de = Number(el.ist) - _needleElIst;
          if (Math.abs(de) > 0.04) {{
            _needleElIst = _needleElIst + de * 0.42;
            dirty = true;
          }} else if (Math.abs(de) > 1e-6) {{
            _needleElIst = Number(el.ist);
            dirty = true;
          }}
        }}
        if (dirty) {{
          drawAz();
          drawEl();
        }}
        requestAnimationFrame(frame);
      }}
      requestAnimationFrame(frame);
    }}
    function heatColor(t) {{
      t = Math.max(0, Math.min(1, t));
      let r,g,b;
      if (t < 0.25) {{ r=0; g=Math.round(100+155*(t/0.25)); b=255; }}
      else if (t < 0.5) {{ r=0; g=255; b=Math.round(255-255*((t-0.25)/0.25)); }}
      else if (t < 0.75) {{ r=Math.round(255*((t-0.5)/0.25)); g=255; b=0; }}
      else {{ r=255; g=Math.round(255-255*((t-0.75)/0.25)); b=0; }}
      return 'rgb('+r+','+g+','+b+')';
    }}
    function drawArrow(ctx, cx, cy, len, deg, color, width) {{
      const rad = deg * Math.PI / 180;
      const fx = Math.sin(rad), fy = -Math.cos(rad);
      const rx = Math.cos(rad), ry = Math.sin(rad);
      const half = Math.max(2, width/2);
      const head = Math.max(11, len * 0.13);
      const shaft = Math.max(0, len - head);
      ctx.save();
      ctx.fillStyle = color;
      ctx.strokeStyle = color;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(cx - rx*half, cy - ry*half);
      ctx.lineTo(cx + fx*shaft - rx*half, cy + fy*shaft - ry*half);
      ctx.lineTo(cx + fx*shaft - rx*half*1.7, cy + fy*shaft - ry*half*1.7);
      ctx.lineTo(cx + fx*len, cy + fy*len);
      ctx.lineTo(cx + fx*shaft + rx*half*1.7, cy + fy*shaft + ry*half*1.7);
      ctx.lineTo(cx + fx*shaft + rx*half, cy + fy*shaft + ry*half);
      ctx.lineTo(cx + rx*half, cy + ry*half);
      ctx.closePath();
      ctx.fill();
      ctx.restore();
    }}
    function drawDashedArrow(ctx, cx, cy, len, deg, color, width) {{
      const rad = deg * Math.PI / 180;
      const fx = Math.sin(rad), fy = -Math.cos(rad);
      ctx.save();
      ctx.strokeStyle = color;
      ctx.lineWidth = width;
      ctx.setLineDash([6, 5]);
      ctx.beginPath();
      ctx.moveTo(cx, cy);
      ctx.lineTo(cx + fx*len*0.85, cy + fy*len*0.85);
      ctx.stroke();
      ctx.restore();
    }}
    function ringRadii(r, modes) {{
      const n = Math.max(1, (modes||[]).length);
      const out = [];
      for (let i=0;i<n;i++) {{
        const outer = r * (0.92 - i * 0.08);
        const inner = outer - r * 0.07;
        out.push({{ outer: outer, inner: Math.max(r*0.35, inner), mode: modes[i] }});
      }}
      return out;
    }}
    function drawHeatRing(ctx, cx, cy, inner, outer, binsCw, binsCcw, offsetDeg, elevation, maxDeg) {{
      const skip = 5;
      const nTotal = elevation ? 36 : 72;
      const used = nTotal - 2*skip;
      if (!binsCw && !binsCcw) return;
      let vmin = 1e9, vmax = -1e9;
      for (let i=skip; i<nTotal-skip; i++) {{
        const v = Math.max(binsCw && binsCw[i] || 0, binsCcw && binsCcw[i] || 0);
        if (v < vmin) vmin = v;
        if (v > vmax) vmax = v;
      }}
      if (vmax <= vmin) {{ vmin = 0; vmax = 1; }}
      const span = elevation ? (maxDeg || 90) : 360;
      const seg = span / used;
      for (let k=0; k<used; k++) {{
        const i = skip + k;
        const v = Math.max(binsCw && binsCw[i] || 0, binsCcw && binsCcw[i] || 0);
        const t = (v - vmin) / (vmax - vmin);
        if (elevation) {{
          // EL 0°=links (π), 90°=oben (3π/2): Canvas-Winkel nehmen zu im Uhrzeigersinn
          const sa = Math.PI + (k * seg) * Math.PI / 180;
          const ea = Math.PI + ((k + 1) * seg) * Math.PI / 180;
          ctx.beginPath();
          ctx.arc(cx, cy, outer, sa, ea, false);
          ctx.arc(cx, cy, inner, ea, sa, true);
          ctx.closePath();
        }} else {{
          const deg0 = wrapDeg(offsetDeg + k * seg);
          const deg1 = wrapDeg(offsetDeg + (k+1) * seg);
          // 0° oben, CW
          const sa = (deg0 - 90) * Math.PI / 180;
          const ea = (deg1 - 90) * Math.PI / 180;
          ctx.beginPath();
          ctx.arc(cx, cy, outer, sa, ea, false);
          ctx.arc(cx, cy, inner, ea, sa, true);
          ctx.closePath();
        }}
        ctx.fillStyle = heatColor(t);
        ctx.globalAlpha = 0.85;
        ctx.fill();
        ctx.globalAlpha = 1;
      }}
    }}
    function drawOmOrDwell(ctx, cx, cy, inner, outer, counts, full, offsetDeg) {{
      const n = (counts && counts.length) || 0;
      if (!n) return;
      let mx = 0;
      for (let i=0;i<n;i++) mx = Math.max(mx, counts[i] || 0);
      const denom = full > 0 ? full : (mx > 0 ? mx : 1);
      const seg = 360 / n;
      for (let i=0;i<n;i++) {{
        const t = Math.max(0, Math.min(1, (counts[i]||0) / denom));
        const deg0 = wrapDeg(offsetDeg + i * seg);
        const deg1 = wrapDeg(offsetDeg + (i+1) * seg);
        const sa = (deg0 - 90) * Math.PI / 180;
        const ea = (deg1 - 90) * Math.PI / 180;
        ctx.beginPath();
        ctx.arc(cx, cy, outer, sa, ea, false);
        ctx.arc(cx, cy, inner, ea, sa, true);
        ctx.closePath();
        ctx.fillStyle = heatColor(t);
        ctx.globalAlpha = 0.75;
        ctx.fill();
        ctx.globalAlpha = 1;
      }}
    }}
    function drawAz() {{
      const canvas = document.getElementById('azCanvas');
      if (!canvas) return;
      const parent = canvas.parentElement;
      const dpr = window.devicePixelRatio || 1;
      const w = Math.max(100, parent.clientWidth);
      const h = Math.max(100, parent.clientHeight);
      canvas.width = Math.floor(w * dpr);
      canvas.height = Math.floor(h * dpr);
      const ctx = canvas.getContext('2d');
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, w, h);
      const cx = w/2, cy = h/2;
      const r = Math.min(w, h) * 0.42;
      const az = state.az || {{}};
      const modes = az.heatmap_modes || [];
      if (windroseImg && windroseImg.complete) {{
        ctx.save();
        ctx.globalAlpha = 0.15;
        ctx.drawImage(windroseImg, cx-r, cy-r, r*2, r*2);
        ctx.restore();
      }}
      ctx.strokeStyle = '{fg}';
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.arc(cx, cy, r, 0, Math.PI*2);
      ctx.stroke();
      for (let d=0; d<360; d+=10) {{
        const rad = d * Math.PI / 180;
        const outer = r;
        const inner = r - ((d % 30 === 0) ? 12 : 7);
        ctx.beginPath();
        ctx.moveTo(cx + Math.sin(rad)*inner, cy - Math.cos(rad)*inner);
        ctx.lineTo(cx + Math.sin(rad)*outer, cy - Math.cos(rad)*outer);
        ctx.strokeStyle = '{fg}';
        ctx.lineWidth = (d % 30 === 0) ? 2 : 1;
        ctx.stroke();
        if (d % 20 === 0) {{
          ctx.fillStyle = '{fg}';
          ctx.font = '11px sans-serif';
          ctx.textAlign = 'center';
          ctx.textBaseline = 'middle';
          const lx = cx + Math.sin(rad)*(r-22);
          const ly = cy - Math.cos(rad)*(r-22);
          ctx.fillText(String(d), lx, ly);
        }}
      }}
      [['N',0],['O',90],['S',180],['W',270]].forEach(function(p) {{
        const rad = p[1]*Math.PI/180;
        ctx.fillStyle = '{fg}';
        ctx.font = 'bold 14px sans-serif';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(p[0], cx + Math.sin(rad)*(r+16), cy - Math.cos(rad)*(r+16));
      }});
      // Beam overlay
      if (az.beam_overlay && az.ist != null) {{
        const open = Number(az.opening_deg || 30);
        const half = open/2;
        const c = azIstForDraw(az);
        const colors = az.beam_color || 'rgba(80,160,255,0.25)';
        function wedge(center) {{
          const a0 = (center - half - 90) * Math.PI/180;
          const a1 = (center + half - 90) * Math.PI/180;
          ctx.beginPath();
          ctx.moveTo(cx, cy);
          ctx.arc(cx, cy, r*0.88, a0, a1, false);
          ctx.closePath();
          ctx.fillStyle = colors;
          ctx.fill();
        }}
        wedge(c);
        if (az.dipole) wedge(wrapDeg(c+180));
      }}
      const rings = ringRadii(r, modes);
      const off = Number(az.offset_deg || 0);
      rings.forEach(function(ring) {{
        if (ring.mode === 'strom') {{
          drawHeatRing(ctx, cx, cy, ring.inner, ring.outer, az.bins_cw, az.bins_ccw, off, false, 360);
        }} else if (ring.mode === 'om_radar') {{
          drawOmOrDwell(ctx, cx, cy, ring.inner, ring.outer, az.om_counts, 0, off);
        }} else if (ring.mode === 'dwell') {{
          drawOmOrDwell(ctx, cx, cy, ring.inner, ring.outer, az.dwell_seconds, az.dwell_full || 300, off);
        }}
      }});
      // Anschlag
      {{
        const deg = off;
        const spread = 4;
        const tip_r = r*0.96, base_r = r;
        const rad = deg*Math.PI/180;
        ctx.beginPath();
        ctx.moveTo(cx + Math.sin(rad)*tip_r, cy - Math.cos(rad)*tip_r);
        ctx.lineTo(cx + Math.sin((deg-spread)*Math.PI/180)*base_r, cy - Math.cos((deg-spread)*Math.PI/180)*base_r);
        ctx.lineTo(cx + Math.sin((deg+spread)*Math.PI/180)*base_r, cy - Math.cos((deg+spread)*Math.PI/180)*base_r);
        ctx.closePath();
        ctx.fillStyle = '#dc0000';
        ctx.fill();
      }}
      if (az.soll != null) {{
        drawArrow(ctx, cx, cy, r*0.82, az.soll, '#e74c3c', 7);
        if (az.dipole) drawDashedArrow(ctx, cx, cy, r*0.78, wrapDeg(az.soll+180), '#e74c3c', 3);
      }}
      if (az.ist != null) {{
        const istDraw = azIstForDraw(az);
        drawArrow(ctx, cx, cy, r*0.72, istDraw, '#2ecc40', 7);
        if (az.dipole) drawDashedArrow(ctx, cx, cy, r*0.68, wrapDeg(istDraw+180), '#2ecc40', 3);
      }}
      if (az.wind_visible && az.wind_dir != null) {{
        let wd = Number(az.wind_dir);
        if ((az.wind_mode||'to') === 'to') wd = wrapDeg(wd + 180);
        drawArrow(ctx, cx, cy, r*0.45, wd, '#3498db', 5);
      }}
    }}
    function elGeom(w, h, maxDeg) {{
      // Wie Desktop ElevationWidget._geom: 90° Viertelkreis (Anker unten rechts), 180° Halbkreis
      const margin = Math.max(48, Math.min(w, h) * 0.10);
      const innerW = w - 2 * margin;
      const innerH = h - 2 * margin;
      const labelPad = 0.10;
      let cx, cy, r;
      if (maxDeg >= 180) {{
        r = Math.min(innerW / 2, innerH) * 0.82;
        const pad = r * labelPad;
        const bboxH = r + pad;
        cx = w / 2;
        cy = (h + bboxH) / 2;
      }} else {{
        const base = Math.min(innerW, innerH);
        r = Math.max(40, base * 0.82);
        const pad = r * labelPad;
        const bboxW = r + pad;
        const bboxH = r + pad;
        // Bogen nach links/oben → Mittelpunkt unten rechts, BBox zentriert
        cx = (w + bboxW) / 2;
        cy = (h + bboxH) / 2;
      }}
      return {{ cx: cx, cy: cy, r: r }};
    }}
    function elXY(cx, cy, radius, deg) {{
      // 0° links, 90° oben, 180° rechts (Bildschirm-Y nach unten)
      const rad = Number(deg) * Math.PI / 180;
      return {{ x: cx - Math.cos(rad) * radius, y: cy - Math.sin(rad) * radius }};
    }}
    function elCanvasAngle(deg) {{
      // Canvas: 0=rechts, π/2=unten, π=links, 3π/2=oben → EL 0°=π, 90°=3π/2
      return Math.PI + Number(deg) * Math.PI / 180;
    }}
    let _elGeomCache = null;
    function drawEl() {{
      const canvas = document.getElementById('elCanvas');
      if (!canvas || !state.enable_el) return;
      const parent = canvas.parentElement;
      const dpr = window.devicePixelRatio || 1;
      const w = Math.max(100, parent.clientWidth);
      const h = Math.max(100, parent.clientHeight);
      canvas.width = Math.floor(w * dpr);
      canvas.height = Math.floor(h * dpr);
      const ctx = canvas.getContext('2d');
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, w, h);
      const el = state.el || {{}};
      const maxDeg = Number(el.max_deg || 90);
      const g = elGeom(w, h, maxDeg);
      _elGeomCache = {{ cx: g.cx, cy: g.cy, r: g.r, maxDeg: maxDeg, w: w, h: h }};
      const cx = g.cx, cy = g.cy, r = g.r;
      ctx.strokeStyle = '{fg}';
      ctx.lineWidth = 2;
      // π→3π/2 mit false (Winkel zunehmend = Uhrzeigersinn) = Viertelkreis links/oben
      ctx.beginPath();
      ctx.arc(cx, cy, r, elCanvasAngle(0), elCanvasAngle(maxDeg), false);
      ctx.stroke();
      function spoke(deg) {{
        const p = elXY(cx, cy, r, deg);
        ctx.beginPath();
        ctx.moveTo(cx, cy);
        ctx.lineTo(p.x, p.y);
        ctx.stroke();
      }}
      spoke(0);
      if (maxDeg >= 180) spoke(180);
      else spoke(Math.min(90, maxDeg));
      for (let d = 0; d <= maxDeg + 0.01; d += 10) {{
        const p0 = elXY(cx, cy, r * ((d % 30 === 0) ? 0.90 : 0.96), d);
        const p1 = elXY(cx, cy, r, d);
        ctx.beginPath();
        ctx.moveTo(p0.x, p0.y);
        ctx.lineTo(p1.x, p1.y);
        ctx.lineWidth = (d % 30 === 0) ? 2 : 1;
        ctx.stroke();
        if (d % 30 === 0) {{
          const lp = elXY(cx, cy, r * 1.12, d);
          ctx.fillStyle = '{fg}';
          ctx.font = '12px sans-serif';
          ctx.textAlign = 'center';
          ctx.textBaseline = 'middle';
          ctx.fillText(String(Math.round(d)), lp.x, lp.y);
        }}
      }}
      ctx.lineWidth = 2;
      if ((el.heatmap_mode || '') === 'strom') {{
        const inner = r * 0.78, outer = r * 0.92;
        drawHeatRing(ctx, cx, cy, inner, outer, el.bins_cw, el.bins_ccw, 0, true, maxDeg);
      }}
      function elArrow(deg, color) {{
        if (deg == null || isNaN(deg)) return;
        const tip = elXY(cx, cy, r * 0.78, deg);
        ctx.strokeStyle = color;
        ctx.fillStyle = color;
        ctx.lineWidth = 4;
        ctx.beginPath();
        ctx.moveTo(cx, cy);
        ctx.lineTo(tip.x, tip.y);
        ctx.stroke();
        const rad = Number(deg) * Math.PI / 180;
        const fx = -Math.cos(rad), fy = -Math.sin(rad);
        const px = -fy, py = fx;
        const hl = Math.max(10, r * 0.06);
        ctx.beginPath();
        ctx.moveTo(tip.x, tip.y);
        ctx.lineTo(tip.x - fx * hl + px * hl * 0.45, tip.y - fy * hl + py * hl * 0.45);
        ctx.lineTo(tip.x - fx * hl - px * hl * 0.45, tip.y - fy * hl - py * hl * 0.45);
        ctx.closePath();
        ctx.fill();
      }}
      if (el.soll != null) elArrow(el.soll, '#e74c3c');
      if (el.ist != null) elArrow(elIstForDraw(el), '#2ecc40');
      ctx.fillStyle = '{fg}';
      ctx.beginPath();
      ctx.arc(cx, cy, 5, 0, Math.PI * 2);
      ctx.fill();
    }}
    function fmt(v) {{
      if (v == null || isNaN(v)) return '–';
      return (Math.round(Number(v)*10)/10).toFixed(1);
    }}
    function applyLabels() {{
      const L = labels || {{}};
      const set = function(id, v) {{ const el=document.getElementById(id); if (el && v) el.textContent = v; }};
      const setPh = function(id, v) {{ const el=document.getElementById(id); if (el && v) el.placeholder = v; }};
      set('btnBack', L.back_to_map || 'Zur Karte');
      set('lblSoll', L.soll || 'Soll');
      set('lblIst', L.ist || 'Ist');
      set('lblIstRev', L.ist_reverse || 'Ist Reverse');
      set('lblWind', L.wind || 'Wind');
      set('lblConn', L.connection || 'Verbindung');
      set('lblCtrl', L.control || 'Steuerung');
      set('lblFav', L.fav_header || 'Favoriten');
      set('lblAnt', L.antenna || 'Antenne');
      set('lblHeat', L.heatmap_rings || 'Ringe');
      set('btnStopAz', L.stop_az || 'STOP AZ');
      set('btnRefAz', L.ref_az || 'AZ Homing');
      set('lblMoving', L.moving || 'Moving');
      set('lblOnline', L.online || 'Online');
      set('lblRef', L.ref || 'Home');
      set('lblHeatStrom', L.heatmap_strom || 'Strom');
      set('lblHeatOm', L.heatmap_om || 'OM');
      set('lblHeatDwell', L.heatmap_dwell || 'Standzeit');
      set('btnDwellReset', L.dwell_reset || 'Standzeit zurücksetzen');
      set('btnFavSave', L.fav_save || 'Save');
      set('btnFavDel', L.fav_delete || 'Delete');
      set('lblOrt', L.ort || L.place || 'Ort');
      set('lblLoc', L.locator || 'Loc');
      set('lblScan', L.scan || 'SCAN');
      set('btnScan', L.scan_start || 'Start');
      set('lblDgcal', L.dgcal || 'Korrekturwinkel');
      set('btnDgcalSave', L.dgcal_save || 'Speichern');
      set('btnDgcalClear', L.dgcal_clear || 'Löschen');
      set('lblElConn', L.connection || 'Verbindung');
      set('lblElCtrl', L.control || 'Steuerung');
      set('lblElSoll', L.soll || 'Soll');
      set('lblElIst', L.ist || 'Ist');
      set('lblElMoving', L.moving || 'Moving');
      set('lblElOnline', L.online || 'Online');
      set('lblElRef', L.ref || 'Home');
      set('btnStopEl', L.stop_el || 'STOP EL');
      set('btnRefEl', L.ref_el || 'EL Homing');
      set('lblHeatElStrom', L.heatmap_strom || 'Strom');
      set('lblElDgcal', L.dgcal || 'Korrekturwinkel');
      set('btnElDgcalSave', L.dgcal_save || 'Speichern');
      set('btnElDgcalClear', L.dgcal_clear || 'Löschen');
      set('geoPickCancel', L.search_cancel || 'Abbrechen');
      setPh('edFavName', L.fav_name_ph || 'Name');
      setPh('edLoc', L.locator_ph || 'JN49…');
      setPh('edPlace', L.search_ph || 'Ort…');
      setPh('edScanA', L.scan_a || 'A');
      setPh('edScanB', L.scan_b || 'B');
    }}
    function applyState(s) {{
      if (!s) return;
      state = s;
      labels = (s.labels) || labels;
      applyLabels();
      document.body.classList.toggle('el-on', !!s.enable_el);
      const az = s.az || {{}};
      const el = s.el || {{}};
      const istEl = document.getElementById('azIst');
      if (istEl) istEl.textContent = fmt(az.ist);
      const sollDisp = document.getElementById('azSollDisp');
      if (sollDisp) sollDisp.textContent = fmt(az.soll);
      const revWrap = document.getElementById('azIstRevWrap');
      if (revWrap) {{
        revWrap.style.display = az.dipole ? '' : 'none';
        const rev = document.getElementById('azIstRev');
        if (rev && az.ist != null) rev.textContent = fmt(wrapDeg(az.ist+180));
      }}
      const ed = document.getElementById('edAzSoll');
      if (ed && document.activeElement !== ed) ed.value = (az.soll != null) ? fmt(az.soll) : '';
      setLed(document.getElementById('ledMoving'), !!az.moving, false);
      setLed(document.getElementById('ledOnline'), !!az.online, false);
      setLed(document.getElementById('ledRef'), !!az.referenced, !!az.ref_blink);
      const refWrap = document.getElementById('refWrap');
      if (refWrap) refWrap.style.display = s.ref_visible === false ? 'none' : '';
      const btnRef = document.getElementById('btnRefAz');
      if (btnRef) btnRef.style.display = s.ref_visible === false ? 'none' : '';
      // antenna
      const ant = document.getElementById('selAnt');
      if (ant && Array.isArray(s.antennas)) {{
        const cur = ant.value;
        if (ant.options.length !== s.antennas.length) {{
          ant.innerHTML = '';
          s.antennas.forEach(function(a, i) {{
            const o = document.createElement('option');
            o.value = String(i);
            o.textContent = a;
            ant.appendChild(o);
          }});
        }}
        if (document.activeElement !== ant) ant.value = String(s.antenna_idx || 0);
      }}
      // favs
      const fav = document.getElementById('selFav');
      if (fav && Array.isArray(s.favorites)) {{
        const want = s.favorites.map(function(f) {{ return f.name + '|' + f.az; }}).join(';');
        if (fav.getAttribute('data-sig') !== want) {{
          fav.setAttribute('data-sig', want);
          fav.innerHTML = '';
          const ph = document.createElement('option');
          ph.value = '-1'; ph.textContent = '—';
          fav.appendChild(ph);
          s.favorites.forEach(function(f, i) {{
            const o = document.createElement('option');
            o.value = String(i);
            o.textContent = f.name + ' (' + fmt(f.az) + '°)';
            fav.appendChild(o);
          }});
        }}
      }}
      // heatmap checks
      const modes = az.heatmap_modes || [];
      const hs = document.getElementById('chkHeatStrom');
      const ho = document.getElementById('chkHeatOm');
      const hd = document.getElementById('chkHeatDwell');
      if (hs && document.activeElement !== hs) hs.checked = modes.indexOf('strom') >= 0;
      if (ho && document.activeElement !== ho) ho.checked = modes.indexOf('om_radar') >= 0;
      if (hd && document.activeElement !== hd) hd.checked = modes.indexOf('dwell') >= 0;
      if (ho) ho.parentElement.style.display = s.om_available ? '' : 'none';
      // wind
      const wb = document.getElementById('windBox');
      if (wb) {{
        if (az.wind_visible) {{
          wb.style.display = '';
          const wk = document.getElementById('windKmh');
          if (wk) wk.textContent = (az.wind_kmh != null) ? String(Math.round(az.wind_kmh*10)/10) : '—';
          const wa = document.getElementById('windArr');
          if (wa && az.wind_dir != null) {{
            let deg = Number(az.wind_dir);
            if ((az.wind_mode||'to') === 'to') deg = wrapDeg(deg+180);
            wa.style.transform = 'rotate('+deg+'deg)';
          }}
        }} else wb.style.display = 'none';
      }}
      const dg = document.getElementById('dgcalCur');
      if (dg) dg.textContent = (az.dgcal != null) ? fmt(az.dgcal) : '–';
      // EL
      if (s.enable_el) {{
        const ei = document.getElementById('elIst');
        if (ei) ei.textContent = fmt(el.ist);
        const esd = document.getElementById('elSollDisp');
        if (esd) esd.textContent = fmt(el.soll);
        const ee = document.getElementById('edElSoll');
        if (ee && document.activeElement !== ee) ee.value = (el.soll != null) ? fmt(el.soll) : '';
        setLed(document.getElementById('ledElMoving'), !!el.moving, false);
        setLed(document.getElementById('ledElOnline'), !!el.online, false);
        setLed(document.getElementById('ledElRef'), !!el.referenced, !!el.ref_blink);
        const elRefWrap = document.getElementById('elRefWrap');
        if (elRefWrap) elRefWrap.style.display = s.ref_visible === false ? 'none' : '';
        const er = document.getElementById('btnRefEl');
        if (er) er.style.display = s.ref_visible === false ? 'none' : '';
        const he = document.getElementById('chkHeatElStrom');
        if (he && document.activeElement !== he) he.checked = (el.heatmap_mode || '') === 'strom';
      }}
      drawAz();
      drawEl();
      ensureNeedleAnimLoop();
    }}

    function azClick(ev) {{
      const canvas = document.getElementById('azCanvas');
      const rect = canvas.getBoundingClientRect();
      const x = (ev.clientX != null ? ev.clientX : ev.touches[0].clientX) - rect.left;
      const y = (ev.clientY != null ? ev.clientY : ev.touches[0].clientY) - rect.top;
      const cx = rect.width/2, cy = rect.height/2;
      const dx = x - cx, dy = y - cy;
      const dist = Math.sqrt(dx*dx+dy*dy);
      const r = Math.min(rect.width, rect.height) * 0.42;
      if (dist < r * 0.55 || dist > r * 1.15) return;
      const rad = Math.atan2(dx, -dy);
      let deg = rad * 180 / Math.PI;
      if (deg < 0) deg += 360;
      api('set_az', {{ deg: deg }});
    }}
    function elClick(ev) {{
      if (!state.enable_el) return;
      const canvas = document.getElementById('elCanvas');
      const rect = canvas.getBoundingClientRect();
      const x = (ev.clientX != null ? ev.clientX : ev.touches[0].clientX) - rect.left;
      const y = (ev.clientY != null ? ev.clientY : ev.touches[0].clientY) - rect.top;
      // Geometrie in CSS-Pixeln wie beim Zeichnen
      const maxDeg = Number((state.el || {{}}).max_deg || 90);
      const g = elGeom(rect.width, rect.height, maxDeg);
      const dx = x - g.cx;
      const dy = g.cy - y; // nach oben positiv
      const dist = Math.sqrt(dx * dx + dy * dy);
      if (dist < g.r * 0.78 || dist > g.r * 1.04) return;
      const rad = Math.atan2(dy, dx);
      let mathDeg = rad * 180 / Math.PI;
      let deg;
      if (mathDeg < 0) {{
        deg = (dx < 0) ? 0 : maxDeg;
      }} else {{
        deg = 180 - mathDeg;
      }}
      if (deg < 0) deg = 0;
      if (deg > maxDeg) deg = maxDeg;
      api('set_el', {{ deg: deg }});
    }}

    function heatmapModesFromUi() {{
      const modes = [];
      if (document.getElementById('chkHeatStrom').checked) modes.push('strom');
      if (document.getElementById('chkHeatOm').checked) modes.push('om_radar');
      if (document.getElementById('chkHeatDwell').checked) modes.push('dwell');
      return modes.slice(0, 2);
    }}
    function bind() {{
      document.getElementById('azCanvas').addEventListener('click', azClick);
      document.getElementById('elCanvas').addEventListener('click', elClick);
      document.getElementById('btnAzSoll').addEventListener('click', function() {{
        api('set_az', {{ deg: parseFloat(document.getElementById('edAzSoll').value), exact: true }});
      }});
      document.getElementById('btnStopAz').addEventListener('click', function() {{ api('stop_az'); }});
      document.getElementById('btnRefAz').addEventListener('click', function() {{ api('ref_az'); }});
      document.getElementById('selAnt').addEventListener('change', function() {{
        api('antenna', {{ index: parseInt(this.value,10)||0 }});
      }});
      ['chkHeatStrom','chkHeatOm','chkHeatDwell'].forEach(function(id) {{
        document.getElementById(id).addEventListener('change', function() {{
          api('heatmap_az_modes', {{ modes: heatmapModesFromUi() }});
        }});
      }});
      document.getElementById('btnDwellReset').addEventListener('click', function() {{ api('dwell_reset'); }});
      document.getElementById('selFav').addEventListener('change', function() {{
        const idx = parseInt(this.value,10);
        if (idx >= 0) api('fav_select', {{ index: idx }});
      }});
      document.getElementById('btnFavSave').addEventListener('click', function() {{
        api('fav_save', {{ name: document.getElementById('edFavName').value || '' }});
      }});
      document.getElementById('btnFavDel').addEventListener('click', function() {{
        api('fav_delete', {{ index: parseInt(document.getElementById('selFav').value,10) }});
      }});
      document.getElementById('placeForm').addEventListener('submit', function(ev) {{
        ev.preventDefault();
        placeSearch(document.getElementById('edPlace').value || '');
      }});
      document.getElementById('locForm').addEventListener('submit', function(ev) {{
        ev.preventDefault();
        api('locator', {{ locator: document.getElementById('edLoc').value || '' }});
      }});
      document.getElementById('btnScan').addEventListener('click', function() {{
        api('az_scan', {{
          a: parseFloat(document.getElementById('edScanA').value),
          b: parseFloat(document.getElementById('edScanB').value)
        }});
      }});
      document.getElementById('btnDgcalSave').addEventListener('click', function() {{
        api('dgcal_az', {{ value: parseFloat(document.getElementById('edDgcal').value) }});
      }});
      document.getElementById('btnDgcalClear').addEventListener('click', function() {{
        api('dgcal_az', {{ clear: true }});
      }});
      document.getElementById('btnElSoll').addEventListener('click', function() {{
        api('set_el', {{ deg: parseFloat(document.getElementById('edElSoll').value) }});
      }});
      document.getElementById('btnStopEl').addEventListener('click', function() {{ api('stop_el'); }});
      document.getElementById('btnRefEl').addEventListener('click', function() {{ api('ref_el'); }});
      document.getElementById('chkHeatElStrom').addEventListener('change', function() {{
        api('heatmap_el', {{ mode: this.checked ? 'strom' : 'off' }});
      }});
      document.getElementById('btnElDgcalSave').addEventListener('click', function() {{
        api('dgcal_el', {{ value: parseFloat(document.getElementById('edElDgcal').value) }});
      }});
      document.getElementById('btnElDgcalClear').addEventListener('click', function() {{
        api('dgcal_el', {{ clear: true }});
      }});
      document.getElementById('geoPickCancel').addEventListener('click', function() {{
        document.getElementById('geoPick').style.display = 'none';
      }});
      window.addEventListener('resize', function() {{ drawAz(); drawEl(); }});
      api('compass_open');
      window.addEventListener('pagehide', function() {{ api('compass_close'); }});
      window.addEventListener('beforeunload', function() {{ api('compass_close'); }});
    }}

    function placeSearch(q) {{
      q = String(q||'').trim();
      if (!q) return;
      const box = document.getElementById('geoPick');
      const title = document.getElementById('geoPickTitle');
      const body = document.getElementById('geoPickBody');
      const list = document.getElementById('geoPickList');
      title.textContent = labels.search_searching || 'Suche…';
      body.textContent = q;
      list.innerHTML = '';
      box.style.display = 'flex';
      fetch('/api/geocode', {{
        method:'POST', headers:{{'Content-Type':'application/json'}},
        body: JSON.stringify({{ query: q }})
      }}).then(function(r){{return r.json();}}).then(function(data) {{
        if (!data || !data.ok) {{
          title.textContent = labels.search_error || 'Fehler';
          body.textContent = (data && data.error) || '';
          return;
        }}
        const results = data.results || [];
        if (!results.length) {{
          title.textContent = labels.search_not_found || 'Nicht gefunden';
          return;
        }}
        if (results.length === 1) {{
          box.style.display = 'none';
          api('place_pick', {{ lat: results[0].lat, lon: results[0].lon }});
          return;
        }}
        title.textContent = labels.search_pick_title || 'Ort auswählen';
        body.textContent = labels.search_pick_body || '';
        list.innerHTML = '';
        results.forEach(function(r) {{
          const b = document.createElement('button');
          b.type = 'button';
          b.textContent = r.display_name || (r.lat+', '+r.lon);
          b.addEventListener('click', function() {{
            box.style.display = 'none';
            api('place_pick', {{ lat: r.lat, lon: r.lon }});
          }});
          list.appendChild(b);
        }});
      }}).catch(function(err) {{
        title.textContent = labels.search_error || 'Fehler';
        body.textContent = String(err||'');
      }});
    }}

    bind();
    applyState(state);
    if (typeof EventSource !== 'undefined') {{
      try {{
        const es = new EventSource('/api/events');
        es.addEventListener('compass', function(ev) {{
          try {{ applyState(JSON.parse(ev.data)); }} catch (e) {{}}
        }});
      }} catch (e) {{}}
    }}
  </script>
</body>
</html>"""
