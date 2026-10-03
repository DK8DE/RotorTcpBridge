"""Leaflet-HTML für die Antennenkarte (Offline/Online, Beams, Maidenhead-Overlay)."""

from __future__ import annotations

import base64
import html
import json
from pathlib import Path
from urllib.parse import quote

from .map_tiles import (
    ONLINE_TILE_ATTRIBUTION,
    ONLINE_TILE_URL_DARK,
    ONLINE_TILE_URL_LIGHT,
    _DEBUG_TILES,
    _offline_tile_url,
    _offline_zoom_range,
    _static_lib_path,
)


def build_map_html(params: dict, dark: bool | None = None) -> str:
    """Erstellt die vollständige HTML-Seite mit Leaflet.
    Enthält window.updateBeam(data) zum Aktualisieren ohne Zoom/Zentrum zu ändern.
    dark: expliziter Wert (hat Vorrang vor params['dark_mode']).
    params['web_mode']=True: Browser-Client (fetch/SSE statt rotorapp://).
    """
    web_mode = bool(params.get("web_mode", False))
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
    rig_freq_show = bool(params.get("rig_freq_show", False))
    rig_freq_out_of_band = bool(params.get("rig_freq_out_of_band", False))
    info_frequenz = params.get("info_frequenz", "Frequenz")
    rig_freq_text = params.get("rig_freq_text", "—")
    rig_freq_block = ""
    if rig_freq_show:
        _rf_esc = html.escape(str(rig_freq_text), quote=True)
        _if_esc = html.escape(str(info_frequenz), quote=True)
        _rf_st = "color:#dc2626;font-weight:600;" if rig_freq_out_of_band else ""
        rig_freq_block = (
            f"<div><strong>{_if_esc}:</strong> "
            f'<span style="{_rf_st}">{_rf_esc}</span></div>'
        )
    asnearest_title = params.get("asnearest_title", "Nächste Verbindungen")
    aswatch_users_online = params.get("aswatch_users_online", "User online: {count}")
    asnearest_col_call = params.get("asnearest_col_call", "Rufzeichen")
    asnearest_col_dist = params.get("asnearest_col_dist", "Entfernung")
    asnearest_col_eta = params.get("asnearest_col_eta", "Zeit (min)")
    asnearest_col_score = params.get("asnearest_col_score", "Score")
    asnearest_tooltip_path = params.get("asnearest_tooltip_path", "Strecke QTH→DX")
    asnearest_tooltip_catpath = params.get("asnearest_tooltip_catpath", "Strecke/Kategorie")
    offline = bool(params.get("offline", False))
    locator_overlay = bool(params.get("map_locator_overlay", False))
    hover_preview = bool(params.get("map_hover_preview", True))
    aswatch_use_cluster = bool(params.get("aswatch_use_cluster", True))
    web_chrome_json = json.dumps(params.get("web_chrome") or {}, ensure_ascii=False)
    offline_min_z, offline_max_z = _offline_zoom_range(dark)
    if offline:
        tile_url = _offline_tile_url(dark) or (
            ONLINE_TILE_URL_DARK if dark else ONLINE_TILE_URL_LIGHT
        )
        tile_url_light = _offline_tile_url(False) or ONLINE_TILE_URL_LIGHT
        tile_url_dark = _offline_tile_url(True) or ONLINE_TILE_URL_DARK
        if _DEBUG_TILES:
            print(
                f"[BuildHTML] dark={dark} tile={tile_url[:60]} light={tile_url_light[:60]} dark={tile_url_dark[:60]}"
            )
    else:
        # Online immer detaillierte Street-Map; Dark = CSS-Filter auf Tile-Pane.
        tile_url = ONLINE_TILE_URL_LIGHT
        tile_url_light = tile_url_dark = tile_url
    body_bg = "#1c1c1c" if dark else "inherit"
    _body_cls = []
    if dark:
        _body_cls.append("map-dark")
    if offline:
        _body_cls.append("map-offline")
    body_map_dark_class = " ".join(_body_cls)

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
    # Ein gemeinsames background-image für alle User-Marker (ein Dekodieren/GPU-Cache statt vieler <img src=data:…>).
    aswatch_userimg_css = ""
    if user_watch_data_url:
        _u = json.dumps(user_watch_data_url)
        aswatch_userimg_css = f"""
    .rotor-aswatch-userimg-24 {{
      width: 24px; height: 24px; margin-top: 2px; flex-shrink: 0;
      background-image: url({_u}); background-size: contain; background-repeat: no-repeat; background-position: center;
      filter: drop-shadow(0 1px 2px rgba(0,0,0,0.45)); pointer-events: auto;
    }}
    .rotor-aswatch-userimg-28 {{
      width: 28px; height: 28px; flex-shrink: 0;
      background-image: url({_u}); background-size: contain; background-repeat: no-repeat; background-position: center;
      filter: drop-shadow(0 1px 2px rgba(0,0,0,0.45)); pointer-events: auto;
    }}"""
    # Kein großes PNG per Base64 im Inline-Skript (Qt WebEngine: sehr große Seiten → weiße Karte).
    # Kompaktes SVG; optional kann später rotortiles:assets/ genutzt werden, wenn die Seite nicht about:blank ist.
    _airplane_svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="#e65100">'
        '<path d="M21 16v-2l-8-5V3.5c0-.83-.67-1.5-1.5-1.5S10 2.67 10 3.5V9l-8 5v2l8-2.5V19l-2 1.5V22l3.5-1 3.5 1v-1.5L13 19v-5.5l8 2.5z"/></svg>'
    )
    airplane_icon_url = "data:image/svg+xml;charset=utf-8," + quote(_airplane_svg, safe="")

    wind_arrow_data_url = ""
    for _wname in ("windPfeil.png", "WindPfeil.png", "windpfeil.png"):
        try:
            _wp = _pkg_root / _wname
            if _wp.is_file():
                wind_arrow_data_url = (
                    "data:image/png;base64," + base64.b64encode(_wp.read_bytes()).decode("ascii")
                )
                break
        except OSError:
            continue

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

    web_chrome_css = ""
    web_wind_html = ""
    web_chrome_html = ""
    web_info_az_html = ""
    if web_mode:
        web_chrome_css = """
    html, body {
      width: 100%; height: 100%; overflow: hidden;
      overscroll-behavior: none;
      -webkit-text-size-adjust: 100%;
    }
    #map, .leaflet-container {
      width: 100%; height: 100%;
      touch-action: none;
      -ms-touch-action: none;
      -webkit-user-select: none;
      user-select: none;
      -webkit-tap-highlight-color: transparent;
    }
    /* CSS-Filter auf Tile-Pane bricht Touch/Drag auf Mobile (Safari/Chrome) */
    body.map-touch.map-dark:not(.map-offline):not(.map-satellite) .leaflet-tile-pane {
      filter: none !important;
    }
    #webWind { position:absolute; top:12px; right:12px; z-index:1100; width:90px; height:100px;
      border-radius:8px; border:1px solid rgba(128,128,128,0.35);
      background:rgba(255,255,255,0.22); backdrop-filter:blur(6px); -webkit-backdrop-filter:blur(6px);
      box-shadow:0 1px 3px rgba(0,0,0,0.08); color:#1a1a1a; display:none;
      flex-direction:column; align-items:center; justify-content:flex-start; padding:6px 4px 4px; pointer-events:none; }
    body.map-dark #webWind { background:rgba(28,28,30,0.45); border-color:rgba(180,180,190,0.25);
      box-shadow:0 1px 4px rgba(0,0,0,0.35); color:#eaeaea; }
    #webWindArrow { width:48px; height:48px; margin-top:4px; object-fit:contain;
      transition:transform 0.12s linear; display:block; }
    #webWindKmh { font:700 12px/1.2 sans-serif; margin-top:10px; text-align:center; width:100%; }
    #webPanels {
      position:absolute; left:8px; right:8px; bottom:8px; z-index:1100;
      display:flex; flex-direction:row; justify-content:space-between; align-items:flex-end; gap:8px;
      pointer-events:none; }
    body.web-panels-hidden #webPanels { display:none; }
    .webPanel {
      position:relative; left:auto; right:auto; bottom:auto;
      max-width:min(480px, calc(50vw - 16px));
      padding:8px 10px; border-radius:8px;
      border:1px solid rgba(128,128,128,0.35);
      background:rgba(255,255,255,0.22); backdrop-filter:blur(6px); -webkit-backdrop-filter:blur(6px);
      box-shadow:0 1px 3px rgba(0,0,0,0.08); color:#1a1a1a;
      font:12px/1.25 sans-serif; display:flex; flex-wrap:wrap; align-items:center; gap:6px;
      pointer-events:auto; touch-action:manipulation; }
    body.map-dark .webPanel {
      background:rgba(28,28,30,0.45); border-color:rgba(180,180,190,0.25);
      box-shadow:0 1px 4px rgba(0,0,0,0.35); color:#eaeaea; }
    #webPanelRight { justify-content:flex-end; margin-left:auto; }
    @media (max-width: 760px) {
      #webPanels { flex-direction:column-reverse; align-items:stretch; }
      .webPanel { max-width:none; width:100%; }
      #webPanelRight { margin-left:0; justify-content:flex-start; }
      #info {
        left:10px; right:auto; width:fit-content; max-width:min(420px, calc(100vw - 20px));
        pointer-events:none; }
      #infoMain, #asnearestBlock {
        pointer-events:auto; display:block; width:fit-content;
        max-width:min(420px, calc(100vw - 20px)); box-sizing:border-box; }
      #webAzBtns button { width:100%; }
    }
    .webPanel select, .webPanel input[type=text], .webPanel input[type=search],
    .webPanel button, #webTogglePanels, #webOpenCompass {
      font:12px/1.2 sans-serif; padding:4px 6px; border-radius:4px;
      border:1px solid rgba(128,128,128,0.45); background:rgba(255,255,255,0.75); color:#111; }
    body.map-dark .webPanel select, body.map-dark .webPanel input[type=text],
    body.map-dark .webPanel input[type=search],
    body.map-dark .webPanel button, body.map-dark #webTogglePanels, body.map-dark #webOpenCompass {
      background:rgba(42,42,46,0.85); color:#eee; border-color:rgba(180,180,190,0.35); }
    .webPanel input[type=search] {-webkit-appearance:none; appearance:none; }
    .webPanel button, #webTogglePanels, #webOpenCompass { cursor:pointer; touch-action:manipulation; }
    .webPanel label.chk { display:inline-flex; align-items:center; gap:4px; white-space:nowrap; }
    .webPanel .bold { font-weight:700; }
    .webPanel .led { width:12px; height:12px; border-radius:50%; display:inline-block;
      background:#888; border:1px solid rgba(0,0,0,0.25); flex:0 0 auto; }
    .webPanel .led.on { background:#2ecc40; }
    .webPanel .led.off { background:#888; }
    .webPanel .led.blink { animation: webLedBlink 0.7s infinite; }
    @keyframes webLedBlink { 0%,49% { background:#2ecc40; } 50%,100% { background:#e74c3c; } }
    #webAzLines {
      margin-top:4px; padding-top:4px; border-top:1px solid rgba(128,128,128,0.25);
      display:flex; flex-direction:column; align-items:stretch; gap:4px;
    }
    body.map-dark #webAzLines { border-top-color:rgba(180,180,190,0.2); }
    #webAzVals {
      display:flex; flex-direction:row; flex-wrap:wrap; align-items:baseline; gap:8px 14px;
    }
    #webAzBtns {
      display:flex; flex-direction:column; align-items:stretch; gap:4px; margin-top:2px;
    }
    #webAzBtns button, #webTogglePanels, #webOpenCompass {
      width:100%; max-width:100%; font-weight:600;
      display:block; box-sizing:border-box; text-align:center;
    }
    #webGeoPick {
      position:fixed; z-index:1200; left:50%; top:50%; transform:translate(-50%,-50%);
      width:min(420px, calc(100vw - 24px)); max-height:min(70vh, 480px);
      display:none; flex-direction:column; gap:8px; padding:12px 14px; border-radius:10px;
      border:1px solid rgba(128,128,128,0.4);
      background:rgba(255,255,255,0.92); backdrop-filter:blur(8px); -webkit-backdrop-filter:blur(8px);
      box-shadow:0 4px 18px rgba(0,0,0,0.25); color:#1a1a1a; font:13px/1.35 sans-serif;
      pointer-events:auto; touch-action:manipulation;
      box-sizing:border-box; overflow:hidden; }
    body.map-dark #webGeoPick {
      background:rgba(28,28,30,0.94); border-color:rgba(180,180,190,0.3); color:#eaeaea;
      box-shadow:0 4px 18px rgba(0,0,0,0.5); }
    #webGeoPickTitle { font-weight:700; font-size:14px; flex:0 0 auto; }
    #webGeoPickBody { font-size:12px; opacity:0.9; flex:0 0 auto; }
    #webGeoPickList {
      overflow:auto; -webkit-overflow-scrolling:touch; flex:1 1 auto; min-height:0;
      max-height:min(50vh, 340px); display:flex; flex-direction:column; gap:4px; }
    #webGeoPickList button {
      text-align:left; width:100%; padding:8px 10px; border-radius:6px; cursor:pointer;
      border:1px solid rgba(128,128,128,0.35); background:rgba(255,255,255,0.7); color:inherit;
      font:12px/1.3 sans-serif; }
    body.map-dark #webGeoPickList button {
      background:rgba(42,42,46,0.9); border-color:rgba(180,180,190,0.3); }
    #webGeoPickActions { display:flex; justify-content:flex-end; gap:8px; flex:0 0 auto; }
    #webUiMsgOverlay {
      display:none; position:fixed; inset:0; z-index:1400;
      align-items:center; justify-content:center;
      background:rgba(0,0,0,0.45); padding:16px;
      pointer-events:auto; touch-action:manipulation;
    }
    #webUiMsgOverlay.show { display:flex; }
    #webUiMsgBox {
      width:min(420px, calc(100vw - 32px)); max-width:100%;
      display:flex; flex-direction:column; gap:10px; padding:16px 18px;
      border-radius:10px; border:1px solid rgba(128,128,128,0.4);
      background:rgba(255,255,255,0.96); color:#1a1a1a;
      box-shadow:0 8px 28px rgba(0,0,0,0.35); font:13px/1.35 sans-serif;
    }
    body.map-dark #webUiMsgBox {
      background:rgba(28,28,30,0.96); border-color:rgba(180,180,190,0.3); color:#eaeaea;
      box-shadow:0 8px 28px rgba(0,0,0,0.55);
    }
    #webUiMsgTitle { font-weight:700; font-size:15px; text-align:center; }
    #webUiMsgBody {
      font-size:13px; line-height:1.4; text-align:center; white-space:pre-wrap; opacity:0.95;
    }
    #webUiMsgActions { display:flex; justify-content:center; margin-top:4px; }
    #webUiMsgOk {
      min-width:96px; cursor:pointer; font-weight:700; padding:8px 18px; border-radius:6px;
      border:1px solid rgba(128,128,128,0.4); background:rgba(255,255,255,0.85); color:inherit;
    }
    body.map-dark #webUiMsgOk {
      background:rgba(42,42,46,0.95); border-color:rgba(180,180,190,0.3);
    }
"""
        if wind_arrow_data_url:
            web_wind_html = (
                "<div id='webWind'>"
                f"<img id='webWindArrow' src='{wind_arrow_data_url}' alt='' width='48' height='48'/>"
                "<div id='webWindKmh'>—</div></div>"
            )
        else:
            web_wind_html = (
                "<div id='webWind'>"
                "<svg id='webWindArrow' viewBox='0 0 24 24' xmlns='http://www.w3.org/2000/svg'>"
                "<path fill='currentColor' d='M12 2l4 9h-3v11h-2V11H8l4-9z'/></svg>"
                "<div id='webWindKmh'>—</div></div>"
            )
        web_info_az_html = """
      <div id="webAzLines">
        <div id="webAzVals">
          <div><strong id="webIstLbl">Ist</strong>: <span id="webIst">–</span></div>
          <div><strong id="webSollLbl">Soll</strong>: <span id="webSoll">–</span></div>
        </div>
        <div id="webAzBtns">
          <button type="button" id="webTogglePanels">Bedienung</button>
          <button type="button" id="webOpenCompass">Kompass</button>
        </div>
      </div>"""
        web_chrome_html = """
  <div id="webPanels">
    <div class="webPanel" id="webPanelLeft">
      <select id="webAnt" title="Antenna"></select>
      <select id="webFav"></select>
      <input id="webFavName" type="text" maxlength="15" style="width:96px;" />
      <button type="button" id="webFavSave">Save</button>
      <button type="button" id="webFavDel">Delete</button>
      <span class="bold" id="webOrtLbl">Ort</span>
      <form id="webPlaceForm" action="#" method="get" style="display:inline;margin:0;padding:0;">
        <input id="webPlace" type="search" enterkeyhint="search" inputmode="search"
               autocomplete="off" autocorrect="off" autocapitalize="off" spellcheck="false"
               style="min-width:140px;width:160px;" />
      </form>
      <span class="bold" id="webLocLbl">Loc</span>
      <form id="webLocatorForm" action="#" method="get" style="display:inline;margin:0;padding:0;">
        <input id="webLocator" type="search" enterkeyhint="go" inputmode="text"
               autocomplete="off" autocorrect="off" autocapitalize="characters" spellcheck="false"
               maxlength="10" style="width:88px;" />
      </form>
    </div>
    <div class="webPanel" id="webPanelRight">
      <span class="led off" id="webLedMoving"></span><span class="bold" id="webMovingLbl">Moving</span>
      <span class="led off" id="webLedOnline"></span><span class="bold" id="webOnlineLbl">Online</span>
      <span id="webRefWrap"><span class="led off" id="webLedRef"></span><span class="bold" id="webRefLbl">Ref</span></span>
      <span class="bold" id="webTempMotor">–</span>
      <span class="bold" id="webTempAmbient">–</span>
      <label class="chk"><input type="checkbox" id="webChkHover" /> <span id="webHoverLbl">Hover</span></label>
      <button type="button" id="webBtnSat">Satellite</button>
      <label class="chk"><input type="checkbox" id="webChkLocator" /> <span id="webLocatorChkLbl">Locator</span></label>
    </div>
  </div>
  <div id="webGeoPick">
    <div id="webGeoPickTitle">Ort auswählen</div>
    <div id="webGeoPickBody"></div>
    <div id="webGeoPickList"></div>
    <div id="webGeoPickActions">
      <button type="button" id="webGeoPickCancel">Abbrechen</button>
    </div>
  </div>
  <div id="webUiMsgOverlay" role="dialog" aria-modal="true" aria-labelledby="webUiMsgTitle">
    <div id="webUiMsgBox">
      <div id="webUiMsgTitle"></div>
      <div id="webUiMsgBody"></div>
      <div id="webUiMsgActions"><button type="button" id="webUiMsgOk">OK</button></div>
    </div>
  </div>"""

    return f"""<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
  <title>Antennenkarte</title>
  <style>{leaflet_css}</style>
  <style>{markercluster_css}</style>
  <style>
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    html, body {{ width: 100%; height: 100%; overflow: hidden; background: {body_bg}; }}
    #map {{ width: 100%; height: 100%; }}
    /* Online-Dark: helle Street-Map behalten, nur Kacheln abdunkeln (Marker/Overlays unverändert). */
    body.map-dark:not(.map-offline):not(.map-satellite) .leaflet-tile-pane {{
      filter: invert(1) hue-rotate(180deg) brightness(0.95) contrast(0.9) saturate(0.65);
    }}
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
    #asnearestUserOnline {{ font-size: 11px; line-height: 1.25; color: #333; margin-bottom: 6px; opacity: 0.92; }}
    body.map-dark #asnearestUserOnline {{ color: #c8c8c8; }}
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
    {aswatch_userimg_css}
    {_cluster_extra_css}
    {web_chrome_css}
  </style>
</head>
<body class="{body_map_dark_class}">
  <div id="info">
    <div id="infoMain">
      <div id="infoMainCore">
      <div><strong>{info_standort}:</strong> {loc_str}</div>
      <div><strong>{info_offnung}:</strong> {opening:.1f}°</div>
      <div><strong>{info_reichweite}:</strong> {range_km:.1f} km</div>
      {rig_freq_block}
      </div>
      {web_info_az_html}
    </div>
    <div id="asnearestBlock" style="display:none;">
      <div id="asnearestTitle" style="font-weight:600;margin-bottom:4px;"></div>
      <div id="asnearestUserOnline"></div>
      <div id="asnearestList"></div>
    </div>
  </div>
  {web_wind_html}
  <div id="map"></div>
  {web_chrome_html}
  <script>{leaflet_js}</script>
  <script>{markercluster_js}</script>
  <script>{maidenhead_js}</script>
  <script>
    const lat = {lat};
    const lon = {lon};
    let stationLat = lat;
    let stationLon = lon;
    let rangeKm = {range_km};
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
    const ASWATCH_USERS_ONLINE_TMPL = {json.dumps(aswatch_users_online)};
    const ASNEAREST_COL_CALL = {json.dumps(asnearest_col_call)};
    const ASNEAREST_COL_DIST = {json.dumps(asnearest_col_dist)};
    const ASNEAREST_COL_ETA = {json.dumps(asnearest_col_eta)};
    const ASNEAREST_COL_SCORE = {json.dumps(asnearest_col_score)};
    const ASNEAREST_TOOLTIP_PATH = {json.dumps(asnearest_tooltip_path)};
    const ASNEAREST_TOOLTIP_CATPATH = {json.dumps(asnearest_tooltip_catpath)};

    const TILE_URL_LIGHT = {json.dumps(ONLINE_TILE_URL_LIGHT)};
    const TILE_URL_SATELLITE = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{{z}}/{{y}}/{{x}}";
    const OFFLINE_ATTRIBUTION = "© OpenStreetMap-Mitwirkende";
    const ONLINE_ATTRIBUTION = {json.dumps(ONLINE_TILE_ATTRIBUTION)};
    const SATELLITE_ATTRIBUTION = 'Tiles &copy; <a href="https://www.esri.com/">Esri</a>';
    const isOffline = {str(offline).lower()};
    let _currentOffline = isOffline;
    let _mapDark = {str(dark).lower()};
    let _mapSatellite = false;
    function _syncMapBodyTileClasses() {{
      document.body.classList.toggle('map-dark', !!_mapDark);
      document.body.classList.toggle('map-offline', !!_currentOffline);
      document.body.classList.toggle('map-satellite', !!_mapSatellite && !_currentOffline);
    }}
    _syncMapBodyTileClasses();
    const offlineMinZ = {offline_min_z};
    const offlineMaxZ = {offline_max_z};
    const tileOpts = isOffline ? {{ maxZoom: offlineMaxZ, minZoom: offlineMaxZ, attribution: OFFLINE_ATTRIBUTION,
      fadeAnimation: false, keepBuffer: 2, updateWhenIdle: true, updateWhenZooming: false }}
      : {{ maxZoom: 19, attribution: ONLINE_ATTRIBUTION,
      fadeAnimation: false, keepBuffer: 2, updateWhenIdle: true, updateWhenZooming: false }};

    console.log('[Map] Init isOffline=' + isOffline + ' tileUrl=' + {json.dumps(tile_url)} + ' origin=' + (document.location && document.location.origin ? document.location.origin : '?'));
    const WEB_MODE = {str(web_mode).lower()};
    const WEB_CHROME_INIT = {web_chrome_json};
    function _webApi(action, extra) {{
      if (!WEB_MODE) return Promise.resolve({{ ok: true }});
      const body = Object.assign({{ action: action }}, extra || {{}});
      return fetch('/api/ui', {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify(body)
      }}).then(function(r) {{
        return r.text().then(function(txt) {{
          let data = {{}};
          try {{ data = txt ? JSON.parse(txt) : {{}}; }} catch (e) {{ data = {{}}; }}
          if (typeof data.ok !== 'boolean') data.ok = !!r.ok;
          return data;
        }});
      }}).catch(function() {{ return {{ ok: false }}; }});
    }}
    function _webChromeLabels() {{
      return (WEB_CHROME_INIT && WEB_CHROME_INIT.labels) || {{}};
    }}
    function _webHideUiError() {{
      const ov = document.getElementById('webUiMsgOverlay');
      if (ov) ov.classList.remove('show');
    }}
    function _webShowUiError(data) {{
      if (!data || data.ok) {{ _webHideUiError(); return; }}
      const Lbl = _webChromeLabels();
      const title = data.title || Lbl.locator_invalid_title || 'Fehler';
      const msg = data.message || data.error || Lbl.locator_invalid_body || '';
      const tEl = document.getElementById('webUiMsgTitle');
      const bEl = document.getElementById('webUiMsgBody');
      const okBtn = document.getElementById('webUiMsgOk');
      const ov = document.getElementById('webUiMsgOverlay');
      if (tEl) tEl.textContent = title;
      if (bEl) bEl.textContent = msg;
      if (okBtn) okBtn.textContent = Lbl.dialog_ok || 'OK';
      if (ov) ov.classList.add('show');
    }}
    function _webHideGeoPick() {{
      const box = document.getElementById('webGeoPick');
      if (box) box.style.display = 'none';
      _webGeoPickVisible = false;
    }}
    let _webGeoPickVisible = false;
    let _webGeoPickVvBound = false;
    function _webPositionGeoPick() {{
      const box = document.getElementById('webGeoPick');
      if (!box || !_webGeoPickVisible) return;
      const vv = window.visualViewport;
      const vw = (vv && vv.width) ? vv.width : (window.innerWidth || document.documentElement.clientWidth || 360);
      const vh = (vv && vv.height) ? vv.height : (window.innerHeight || document.documentElement.clientHeight || 640);
      const ox = (vv && typeof vv.offsetLeft === 'number') ? vv.offsetLeft : 0;
      const oy = (vv && typeof vv.offsetTop === 'number') ? vv.offsetTop : 0;
      const pad = 12;
      const maxH = Math.max(140, Math.min(vh - pad * 2, 480));
      const width = Math.min(420, Math.max(200, vw - pad * 2));
      box.style.position = 'fixed';
      box.style.left = (ox + vw / 2) + 'px';
      box.style.top = (oy + vh / 2) + 'px';
      box.style.transform = 'translate(-50%, -50%)';
      box.style.width = width + 'px';
      box.style.maxHeight = maxH + 'px';
      const list = document.getElementById('webGeoPickList');
      if (list) {{
        list.style.maxHeight = Math.max(60, maxH - 120) + 'px';
      }}
    }}
    function _webBindGeoPickViewport() {{
      if (_webGeoPickVvBound) return;
      _webGeoPickVvBound = true;
      const reposition = function() {{ _webPositionGeoPick(); }};
      window.addEventListener('resize', reposition);
      if (window.visualViewport) {{
        window.visualViewport.addEventListener('resize', reposition);
        window.visualViewport.addEventListener('scroll', reposition);
      }}
    }}
    function _webOpenGeoPick() {{
      const box = document.getElementById('webGeoPick');
      if (!box) return;
      // Tastatur: Fokus vom Eingabefeld nehmen, Overlay in sichtbare Mitte legen
      try {{
        const ae = document.activeElement;
        if (ae && typeof ae.blur === 'function') ae.blur();
      }} catch (e) {{}}
      try {{
        const place = document.getElementById('webPlace');
        if (place && typeof place.blur === 'function') place.blur();
      }} catch (e) {{}}
      _webGeoPickVisible = true;
      box.style.display = 'flex';
      _webBindGeoPickViewport();
      _webPositionGeoPick();
      // Nach Tastatur-Animation nochmal zentrieren
      setTimeout(_webPositionGeoPick, 50);
      setTimeout(_webPositionGeoPick, 250);
      setTimeout(_webPositionGeoPick, 450);
    }}
    function _webShowGeoMessage(title, body) {{
      const box = document.getElementById('webGeoPick');
      const tEl = document.getElementById('webGeoPickTitle');
      const bEl = document.getElementById('webGeoPickBody');
      const list = document.getElementById('webGeoPickList');
      if (!box) {{ alert(body || title); return; }}
      if (tEl) tEl.textContent = title || '';
      if (bEl) bEl.textContent = body || '';
      if (list) list.innerHTML = '';
      _webOpenGeoPick();
    }}
    function _webApplyGeoResult(r) {{
      if (!r) return;
      const lat2 = Number(r.lat);
      const lon2 = Number(r.lon);
      if (isNaN(lat2) || isNaN(lon2)) return;
      _webHideGeoPick();
      if (typeof window.setClickMarker === 'function') window.setClickMarker(lat2, lon2);
      if (typeof window._rotorSetAz === 'function') window._rotorSetAz(lat2, lon2, null);
      try {{
        map.setView([lat2, lon2], Math.max(map.getZoom(), 10));
      }} catch (e) {{}}
    }}
    function _webShowGeoPick(results) {{
      const Lbl = _webChromeLabels();
      const box = document.getElementById('webGeoPick');
      const tEl = document.getElementById('webGeoPickTitle');
      const bEl = document.getElementById('webGeoPickBody');
      const list = document.getElementById('webGeoPickList');
      const cancel = document.getElementById('webGeoPickCancel');
      if (!box || !list) return;
      if (tEl) tEl.textContent = Lbl.search_pick_title || 'Ort auswählen';
      if (bEl) bEl.textContent = Lbl.search_pick_body || '';
      if (cancel) cancel.textContent = Lbl.search_cancel || 'Abbrechen';
      list.innerHTML = '';
      (results || []).forEach(function(r) {{
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.textContent = r.display_name || (r.lat + ', ' + r.lon);
        btn.addEventListener('click', function() {{ _webApplyGeoResult(r); }});
        list.appendChild(btn);
      }});
      _webOpenGeoPick();
    }}
    function _webPlaceSearch(query) {{
      const q = String(query || '').trim();
      if (!q) return;
      const Lbl = _webChromeLabels();
      const place = document.getElementById('webPlace');
      if (place) place.disabled = true;
      _webShowGeoMessage(Lbl.search_searching || 'Suche…', q);
      fetch('/api/geocode', {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify({{ query: q }})
      }}).then(function(resp) {{ return resp.json(); }}).then(function(data) {{
        if (place) place.disabled = false;
        if (!data || !data.ok) {{
          _webShowGeoMessage(Lbl.search_error || 'Fehler', (data && data.error) || '');
          return;
        }}
        const results = data.results || [];
        if (!results.length) {{
          _webShowGeoMessage(Lbl.search_not_found || 'Nicht gefunden', q);
          return;
        }}
        if (results.length === 1) {{
          _webApplyGeoResult(results[0]);
          return;
        }}
        _webShowGeoPick(results);
      }}).catch(function(err) {{
        if (place) place.disabled = false;
        _webShowGeoMessage(Lbl.search_error || 'Fehler', String(err || ''));
      }});
    }}
    function _webSetLed(el, on, blink) {{
      if (!el) return;
      el.classList.remove('on', 'off', 'blink');
      if (blink) el.classList.add('blink');
      else el.classList.add(on ? 'on' : 'off');
    }}
    function _webApplyChrome(ch) {{
      if (!WEB_MODE || !ch) return;
      const Lbl = ch.labels || {{}};
      const ant = ch.antennas || {{}};
      const favs = ch.favorites || [];
      const flags = ch.flags || {{}};
      const st = ch.status || {{}};
      const wind = ch.wind || {{}};
      const setTxt = function(id, v) {{ const e = document.getElementById(id); if (e) e.textContent = v; }};
      setTxt('webOrtLbl', Lbl.ort || 'Ort');
      setTxt('webLocLbl', Lbl.locator || 'Loc');
      setTxt('webIstLbl', String(Lbl.ist_prefix || 'Ist').trim() || 'Ist');
      setTxt('webSollLbl', Lbl.soll || 'Soll');
      setTxt('webMovingLbl', Lbl.moving || '');
      setTxt('webOnlineLbl', Lbl.online || '');
      setTxt('webRefLbl', Lbl.ref || '');
      setTxt('webHoverLbl', Lbl.hover || '');
      setTxt('webLocatorChkLbl', Lbl.locator_chk || '');
      const tog = document.getElementById('webTogglePanels');
      if (tog) {{
        const hidden = document.body.classList.contains('web-panels-hidden');
        tog.textContent = hidden
          ? (Lbl.toggle_controls_show || Lbl.toggle_controls || 'Show')
          : (Lbl.toggle_controls_hide || Lbl.toggle_controls || 'Hide');
        tog.title = tog.textContent;
      }}
      const openComp = document.getElementById('webOpenCompass');
      if (openComp) openComp.textContent = Lbl.open_compass || 'Kompass';
      const favSave = document.getElementById('webFavSave');
      const favDel = document.getElementById('webFavDel');
      const btnSat = document.getElementById('webBtnSat');
      if (favSave) favSave.textContent = Lbl.fav_save || 'Save';
      if (favDel) favDel.textContent = Lbl.fav_delete || 'Delete';
      if (btnSat) btnSat.textContent = Lbl.satellite || 'Satellite';
      const geoCancel = document.getElementById('webGeoPickCancel');
      if (geoCancel) geoCancel.textContent = Lbl.search_cancel || 'Abbrechen';
      const favName = document.getElementById('webFavName');
      const place = document.getElementById('webPlace');
      const locIn = document.getElementById('webLocator');
      if (favName && document.activeElement !== favName) favName.placeholder = Lbl.fav_name_ph || '';
      if (place && document.activeElement !== place) place.placeholder = Lbl.search_ph || '';
      if (locIn && document.activeElement !== locIn) locIn.placeholder = Lbl.locator_ph || '';
      const antSel = document.getElementById('webAnt');
      if (antSel && document.activeElement !== antSel) {{
        const items = ant.items || [];
        const colors = ant.colors || [];
        let html = '';
        for (let i = 0; i < items.length; i++) {{
          html += '<option value="' + i + '" style="color:' + (colors[i] || '#333') + ';">' + items[i] + '</option>';
        }}
        antSel.innerHTML = html;
        antSel.value = String(ant.index != null ? ant.index : 0);
        antSel.style.display = ant.visible === false ? 'none' : '';
      }}
      const favSel = document.getElementById('webFav');
      if (favSel && document.activeElement !== favSel) {{
        let html = '';
        if (!favs.length) {{
          html = '<option value="-1">' + (Lbl.fav_placeholder || '—') + '</option>';
        }} else {{
          for (let i = 0; i < favs.length; i++) {{
            html += '<option value="' + i + '">' + favs[i].label + '</option>';
          }}
        }}
        const prev = favSel.value;
        favSel.innerHTML = html;
        if (prev && favSel.querySelector('option[value="' + prev + '"]')) favSel.value = prev;
      }}
      setTxt('webIst', st.ist || '–');
      setTxt('webSoll', st.soll || '–');
      if (ch.clear_map_target && typeof window.clearClickMarker === 'function') {{
        try {{ window.clearClickMarker(); }} catch (e) {{}}
      }}
      _webSetLed(document.getElementById('webLedMoving'), !!st.moving, false);
      _webSetLed(document.getElementById('webLedOnline'), !!st.online, false);
      _webSetLed(document.getElementById('webLedRef'), !!st.referenced, !!st.ref_blink);
      const refWrap = document.getElementById('webRefWrap');
      if (refWrap) refWrap.style.display = flags.ref_visible === false ? 'none' : '';
      setTxt('webTempMotor', (Lbl.temp_motor || 'Motor') + ': ' + (st.temp_motor || '–'));
      setTxt('webTempAmbient', (Lbl.temp_ambient || 'Air') + ': ' + (st.temp_ambient || '–'));
      const chkHover = document.getElementById('webChkHover');
      const chkLoc = document.getElementById('webChkLocator');
      if (chkHover && document.activeElement !== chkHover) chkHover.checked = !!flags.hover;
      if (chkLoc && document.activeElement !== chkLoc) chkLoc.checked = !!flags.locator;
      if (_isTouchUi) {{
        hoverPreviewEnabled = false;
        if (chkHover) chkHover.checked = false;
      }} else {{
        hoverPreviewEnabled = flags.hover !== false;
      }}
      if (typeof window.setMapSatelliteMode === 'function' && !!flags.satellite !== !!_mapSatellite) {{
        window.setMapSatelliteMode(!!flags.satellite);
      }}
      if (typeof window.setMapLocatorOverlay === 'function') {{
        const wantLoc = !!flags.locator;
        if (wantLoc !== !!locatorVisible) {{
          window.setMapLocatorOverlay(wantLoc, _mapDark);
        }}
      }}
      const wEl = document.getElementById('webWind');
      const wArr = document.getElementById('webWindArrow');
      const wKmh = document.getElementById('webWindKmh');
      if (wEl) {{
        if (wind.visible) {{
          wEl.style.display = 'flex';
          let deg = (wind.dir_deg != null) ? Number(wind.dir_deg) : 0;
          if ((wind.mode || 'to') === 'to') deg = (deg + 180) % 360;
          if (wArr) wArr.style.transform = 'rotate(' + deg + 'deg)';
          if (wKmh) {{
            wKmh.textContent = (wind.kmh != null && !isNaN(wind.kmh))
              ? (Math.round(Number(wind.kmh) * 10) / 10) + ' km/h' : '—';
          }}
        }} else {{
          wEl.style.display = 'none';
        }}
      }}
    }}
    function _webBindChrome() {{
      if (!WEB_MODE) return;
      const antSel = document.getElementById('webAnt');
      if (antSel) antSel.addEventListener('change', function() {{
        _webApi('antenna', {{ index: parseInt(antSel.value, 10) || 0 }});
      }});
      const favSel = document.getElementById('webFav');
      if (favSel) favSel.addEventListener('change', function() {{
        const idx = parseInt(favSel.value, 10);
        if (idx >= 0) _webApi('fav_select', {{ index: idx }});
      }});
      const favSave = document.getElementById('webFavSave');
      if (favSave) favSave.addEventListener('click', function() {{
        const name = (document.getElementById('webFavName') || {{}}).value || '';
        _webApi('fav_save', {{ name: name }});
      }});
      const favDel = document.getElementById('webFavDel');
      if (favDel) favDel.addEventListener('click', function() {{
        const idx = parseInt((document.getElementById('webFav') || {{}}).value, 10);
        if (idx >= 0) _webApi('fav_delete', {{ index: idx }});
      }});
      const place = document.getElementById('webPlace');
      const placeForm = document.getElementById('webPlaceForm');
      function _webRunPlaceSearch(ev) {{
        if (ev) {{
          try {{ ev.preventDefault(); }} catch (e) {{}}
          try {{ ev.stopPropagation(); }} catch (e) {{}}
        }}
        if (!place) return;
        const q = place.value || '';
        try {{ place.blur(); }} catch (e) {{}}
        _webPlaceSearch(q);
      }}
      if (placeForm) {{
        placeForm.addEventListener('submit', function(ev) {{ _webRunPlaceSearch(ev); }});
      }}
      if (place) {{
        place.addEventListener('keydown', function(ev) {{
          if (ev.key === 'Enter' || ev.keyCode === 13) _webRunPlaceSearch(ev);
        }});
        place.addEventListener('search', function(ev) {{ _webRunPlaceSearch(ev); }});
      }}
      const locIn = document.getElementById('webLocator');
      const locForm = document.getElementById('webLocatorForm');
      function _webRunLocator(ev) {{
        if (ev) {{
          try {{ ev.preventDefault(); }} catch (e) {{}}
          try {{ ev.stopPropagation(); }} catch (e) {{}}
        }}
        if (!locIn) return;
        _webApi('locator', {{ locator: locIn.value || '' }}).then(function(data) {{
          _webShowUiError(data);
          if (data && !data.ok) {{
            try {{ locIn.focus(); locIn.select(); }} catch (e) {{}}
          }} else {{
            try {{ locIn.blur(); }} catch (e) {{}}
          }}
        }});
      }}
      if (locForm) {{
        locForm.addEventListener('submit', function(ev) {{ _webRunLocator(ev); }});
      }}
      if (locIn) {{
        locIn.addEventListener('keydown', function(ev) {{
          if (ev.key === 'Enter' || ev.keyCode === 13) _webRunLocator(ev);
        }});
        locIn.addEventListener('search', function(ev) {{ _webRunLocator(ev); }});
      }}
      const uiMsgOk = document.getElementById('webUiMsgOk');
      if (uiMsgOk) uiMsgOk.addEventListener('click', function() {{ _webHideUiError(); }});
      const uiMsgOv = document.getElementById('webUiMsgOverlay');
      if (uiMsgOv) {{
        uiMsgOv.addEventListener('click', function(ev) {{
          if (ev.target === uiMsgOv) _webHideUiError();
        }});
        if (typeof L !== 'undefined' && L.DomEvent) {{
          try {{ L.DomEvent.disableClickPropagation(uiMsgOv); }} catch (e) {{}}
        }}
      }}
      document.addEventListener('keydown', function(ev) {{
        if (ev.key === 'Escape') _webHideUiError();
      }});
      const geoCancel = document.getElementById('webGeoPickCancel');
      if (geoCancel) geoCancel.addEventListener('click', function() {{ _webHideGeoPick(); }});
      const geoPick = document.getElementById('webGeoPick');
      if (geoPick && typeof L !== 'undefined' && L.DomEvent) {{
        try {{
          L.DomEvent.disableClickPropagation(geoPick);
          L.DomEvent.disableScrollPropagation(geoPick);
        }} catch (e) {{}}
      }}
      const chkHover = document.getElementById('webChkHover');
      if (chkHover) chkHover.addEventListener('change', function() {{
        hoverPreviewEnabled = !!chkHover.checked;
        _webApi('toggle_hover', {{ value: !!chkHover.checked }});
      }});
      const chkLoc = document.getElementById('webChkLocator');
      if (chkLoc) chkLoc.addEventListener('change', function() {{
        if (typeof window.setMapLocatorOverlay === 'function') {{
          window.setMapLocatorOverlay(!!chkLoc.checked, _mapDark);
        }}
        _webApi('toggle_locator', {{ value: !!chkLoc.checked }});
      }});
      const btnSat = document.getElementById('webBtnSat');
      if (btnSat) btnSat.addEventListener('click', function() {{
        const next = !_mapSatellite;
        if (typeof window.setMapSatelliteMode === 'function') window.setMapSatelliteMode(next);
        _webApi('toggle_satellite', {{ value: next }});
      }});
      const panels = document.getElementById('webPanels');
      if (panels && typeof L !== 'undefined' && L.DomEvent) {{
        try {{
          L.DomEvent.disableClickPropagation(panels);
          L.DomEvent.disableScrollPropagation(panels);
        }} catch (e) {{}}
      }}
      const infoEl = document.getElementById('info');
      if (infoEl && typeof L !== 'undefined' && L.DomEvent) {{
        try {{
          L.DomEvent.disableClickPropagation(infoEl);
          L.DomEvent.disableScrollPropagation(infoEl);
        }} catch (e) {{}}
      }}
      const tog = document.getElementById('webTogglePanels');
      if (tog) {{
        try {{
          if (localStorage.getItem('rotorWebPanelsHidden') === '1') {{
            document.body.classList.add('web-panels-hidden');
          }}
        }} catch (e) {{}}
        tog.addEventListener('click', function() {{
          document.body.classList.toggle('web-panels-hidden');
          const hidden = document.body.classList.contains('web-panels-hidden');
          try {{ localStorage.setItem('rotorWebPanelsHidden', hidden ? '1' : '0'); }} catch (e) {{}}
          const Lbl = (WEB_CHROME_INIT && WEB_CHROME_INIT.labels) || {{}};
          tog.textContent = hidden
            ? (Lbl.toggle_controls_show || 'Show')
            : (Lbl.toggle_controls_hide || 'Hide');
          setTimeout(function() {{ try {{ map.invalidateSize(); }} catch (e) {{}} }}, 50);
        }});
      }}
      const openComp = document.getElementById('webOpenCompass');
      if (openComp) {{
        openComp.addEventListener('click', function() {{
          try {{ window.location.href = '/compass'; }} catch (e) {{}}
        }});
      }}
      window.addEventListener('orientationchange', function() {{
        setTimeout(function() {{
          try {{
            if (typeof window._rotorSafeInvalidateSize === 'function') window._rotorSafeInvalidateSize();
            else map.invalidateSize({{ pan: false }});
          }} catch (e) {{}}
        }}, 200);
      }});
      // Resize debouncen: auf Mobile feuert das während Pinch/Adressleisten-Einblenden
      // und kämpft sonst gegen Zoom/Pan.
      let _webResizeTimer = null;
      window.addEventListener('resize', function() {{
        if (_webResizeTimer) clearTimeout(_webResizeTimer);
        _webResizeTimer = setTimeout(function() {{
          _webResizeTimer = null;
          try {{
            if (typeof window._rotorSafeInvalidateSize === 'function') window._rotorSafeInvalidateSize();
            else map.invalidateSize({{ pan: false }});
          }} catch (e) {{}}
        }}, 300);
      }});
      _webApplyChrome(WEB_CHROME_INIT);
    }}
    window._rotorSetAz = function(lat2, lon2, destKey) {{
      // Nach Tippen/Suche: ausstehende Beam-Updates sofort anzeigen
      try {{
        _mapPinchUntil = 0;
        _flushPendingMapUpdates(true);
      }} catch (e) {{}}
      if (WEB_MODE) {{
        const body = {{ lat: Number(lat2), lon: Number(lon2) }};
        if (destKey) body.asnearest_dest = String(destKey);
        fetch('/api/setaz', {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify(body)
        }}).catch(function() {{}});
        // Ziel-Linie kommt per SSE — nach kurzer Latenz nochmal flushen
        setTimeout(function() {{ try {{ _flushPendingMapUpdates(true); }} catch (e) {{}} }}, 200);
        setTimeout(function() {{ try {{ _flushPendingMapUpdates(true); }} catch (e) {{}} }}, 600);
        return;
      }}
      let href = 'rotorapp://setaz?lat=' + lat2 + '&lon=' + lon2;
      if (destKey) href += '&asnearest_dest=' + encodeURIComponent(destKey);
      window.location = href;
    }};
    const initZoom = isOffline ? {offline_max_z} : 10;
    const _isTouchUi = (typeof window !== 'undefined') && (
      ('ontouchstart' in window)
      || (navigator.maxTouchPoints && navigator.maxTouchPoints > 0)
      || (window.matchMedia && window.matchMedia('(pointer: coarse)').matches)
    );
    if (_isTouchUi) {{
      try {{ document.body.classList.add('map-touch'); }} catch (e) {{}}
    }}
    // iOS/Safari: Browser-Pinch unterdrücken, damit Leaflet den Zoom bekommt.
    // preventDefault nur außerhalb der Karte — auf #map muss Leaflet die Touches behalten.
    if (_isTouchUi) {{
      ['gesturestart', 'gesturechange', 'gestureend'].forEach(function(type) {{
        document.addEventListener(type, function(ev) {{
          try {{ ev.preventDefault(); }} catch (e) {{}}
        }}, {{ passive: false }});
      }});
      document.addEventListener('touchmove', function(ev) {{
        if (!(ev.touches && ev.touches.length > 1)) return;
        const t = ev.target;
        if (t && t.closest && t.closest('.leaflet-container')) return;
        try {{ ev.preventDefault(); }} catch (e) {{}}
      }}, {{ passive: false }});
    }}
    const map = L.map('map', {{
      maxZoom: isOffline ? {offline_max_z} : 19,
      minZoom: isOffline ? {offline_max_z} : 3,
      zoomControl: true,
      dragging: true,
      touchZoom: true,
      doubleClickZoom: true,
      scrollWheelZoom: !_isTouchUi,
      boxZoom: !_isTouchUi,
      keyboard: true,
      bounceAtZoomLimits: false,
      inertia: !_isTouchUi,
      worldCopyJump: false,
      // Verhindert, dass kurze Fingerbewegung als Klick statt Drag zählt
      tapTolerance: 25,
      // Auf Touch: Zoom-Animation aus — sonst bleiben Kacheln nach abgebrochenem Pinch skaliert stehen
      zoomAnimation: !_isTouchUi,
      fadeAnimation: !_isTouchUi,
      markerZoomAnimation: !_isTouchUi
    }}).setView([lat, lon], initZoom);
    // Pinch-Schutz: SSE-Beam nur während echtem Zwei-Finger-Zoom zurückhalten.
    // Pan/Tippen darf Beams nicht blockieren — sonst bleibt der Overlay nach setaz stehen.
    let _mapPinchUntil = 0;
    let _pendingBeamData = null;
    let _pendingAswatch = null;
    let _pendingAircraft = null;
    function _markMapPinch(ms) {{
      _mapPinchUntil = Math.max(_mapPinchUntil, Date.now() + (ms || 500));
    }}
    function _isMapPinching() {{
      if (Date.now() < _mapPinchUntil) return true;
      try {{
        if (map.touchZoom && map.touchZoom._zooming) return true;
        if (map._animatingZoom) return true;
      }} catch (e) {{}}
      return false;
    }}
    // Alias für Locator-Refresh-Code
    function _isMapGestureBusy() {{ return _isMapPinching(); }}
    function _flushPendingMapUpdates(force) {{
      if (!force && _isMapPinching()) return;
      if (_pendingBeamData) {{
        const d = _pendingBeamData;
        _pendingBeamData = null;
        try {{ if (typeof window.updateBeam === 'function') window.updateBeam(d); }} catch (e) {{}}
      }}
      if (_pendingAswatch) {{
        const a = _pendingAswatch;
        _pendingAswatch = null;
        try {{
          if (typeof window.setAswatchMarkers === 'function') window.setAswatchMarkers(a.items || [], a.total);
        }} catch (e) {{}}
      }}
      if (_pendingAircraft) {{
        const p = _pendingAircraft;
        _pendingAircraft = null;
        try {{
          if (typeof window.setAirplaneMarkers === 'function') window.setAirplaneMarkers(p.items || []);
        }} catch (e) {{}}
      }}
    }}
    function _endMapPinch() {{
      _mapPinchUntil = 0;
      setTimeout(function() {{
        try {{
          if (tileLayer && typeof tileLayer.redraw === 'function') tileLayer.redraw();
        }} catch (e) {{}}
        try {{ if (typeof _resumeLocatorAfterGesture === 'function') _resumeLocatorAfterGesture(); }} catch (e) {{}}
        _flushPendingMapUpdates(true);
      }}, 60);
    }}
    window._rotorSafeInvalidateSize = function() {{
      if (_isMapPinching()) return;
      try {{ map.invalidateSize({{ pan: false }}); }} catch (e) {{}}
    }};
    try {{
      const mc = map.getContainer();
      if (mc) {{
        mc.addEventListener('touchstart', function(ev) {{
          if (ev.touches && ev.touches.length >= 2) {{
            _markMapPinch(800);
            try {{ if (typeof _suspendLocatorForGesture === 'function') _suspendLocatorForGesture(); }} catch (e) {{}}
          }}
        }}, {{ passive: true }});
        mc.addEventListener('touchmove', function(ev) {{
          if (ev.touches && ev.touches.length >= 2) {{
            _markMapPinch(800);
            try {{ if (typeof _suspendLocatorForGesture === 'function') _suspendLocatorForGesture(); }} catch (e) {{}}
          }}
        }}, {{ passive: true }});
        mc.addEventListener('touchend', function(ev) {{
          if (ev.touches && ev.touches.length >= 1) return;
          setTimeout(function() {{
            try {{
              if (map.touchZoom && map.touchZoom._zooming) return;
              if (map._animatingZoom) return;
            }} catch (e) {{}}
            if (_mapPinchUntil > Date.now() || (typeof _locatorSuspended !== 'undefined' && _locatorSuspended)) {{
              _endMapPinch();
            }} else {{
              _flushPendingMapUpdates(true);
            }}
          }}, 80);
        }}, {{ passive: true }});
        mc.addEventListener('touchcancel', function() {{ _endMapPinch(); }}, {{ passive: true }});
      }}
      map.on('zoomstart', function() {{ _markMapPinch(800); }});
      map.on('zoomend', function() {{ _endMapPinch(); }});
      map.on('moveend', function() {{
        setTimeout(function() {{ _flushPendingMapUpdates(false); }}, 40);
      }});
    }} catch (e) {{}}
    try {{
      if (map.dragging) {{
        map.dragging.enable();
        if (map.dragging._draggable) {{
          // etwas höhere Schwelle gegen Klick/Drag-Konflikt auf Touch
          try {{ map.dragging._draggable._clickTolerance = 8; }} catch (e) {{}}
        }}
      }}
      if (map.touchZoom) map.touchZoom.enable();
      const c = map.getContainer();
      if (c) {{
        c.style.touchAction = 'none';
        c.style.msTouchAction = 'none';
        c.style.webkitUserSelect = 'none';
        c.style.userSelect = 'none';
      }}
    }} catch (e) {{}}
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
    /* Offline: eine Zoomstufe → immer volle Marker. Online + Cluster: bis Zoom ≤17 nur Cluster; ab 18 Einzelmarker mit vollem Symbol (kein Zwischen-Punkt). Ohne Cluster: niedriger Zoom schlanke Labels, ab 19 voll. */
    const aswatchDisableClusterZoom = isOffline ? offlineMaxZ : 18;
    const aswatchFullIconZoom = isOffline ? offlineMaxZ : 19;
    let aswatchLayer;
    const _aswatchUseClusterLayer = {str(aswatch_use_cluster).lower()} && typeof L.markerClusterGroup === 'function';
    if (_aswatchUseClusterLayer) {{
      aswatchLayer = L.markerClusterGroup({{
        spiderfyOnMaxZoom: true,
        showCoverageOnHover: true,
        zoomToBoundsOnClick: true,
        maxClusterRadius: 96,
        disableClusteringAtZoom: aswatchDisableClusterZoom,
        removeOutsideVisibleBounds: true,
        animate: false,
        chunkedLoading: true,
        chunkInterval: 100,
        chunkDelay: 40
      }}).addTo(map);
    }} else {{
      aswatchLayer = L.layerGroup().addTo(map);
    }}
    function _aswatchWantFullDetailByZoom() {{
      if (isOffline) return true;
      if (_lastAswatch.length <= 1) return true;
      if (_aswatchUseClusterLayer) return map.getZoom() >= aswatchDisableClusterZoom;
      return map.getZoom() >= aswatchFullIconZoom;
    }}
    let _lastAswatch = [];
    let _aswatchTotalOnline = 0;
    let _aswatchMarkerRefs = [];
    let _mapDarkAswatch = {str(dark).lower()};
    function _updateAswatchUsersOnlineLine() {{
      const el = document.getElementById('asnearestUserOnline');
      if (!el) return;
      const block = document.getElementById('asnearestBlock');
      if (!block || block.style.display === 'none') {{
        el.textContent = '';
        return;
      }}
      el.textContent = ASWATCH_USERS_ONLINE_TMPL.split('{{count}}').join(String(_aswatchTotalOnline));
    }}
    function _escapeHtmlAswatch(s) {{
      return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;');
    }}
    function _escapeAttrAswatch(s) {{
      return String(s).replace(/&/g,'&amp;').replace(/\"/g,'&quot;');
    }}
    function _buildAswatchDivIcon(m, fullDetail) {{
      const bubbleBg = _mapDarkAswatch ? 'rgba(40,40,40,0.95)' : 'rgba(255,255,255,0.95)';
      const bubbleFg = _mapDarkAswatch ? '#f0f0f0' : '#111';
      const bubbleBr = _mapDarkAswatch ? '#888' : '#333';
      const call = _escapeHtmlAswatch(m.call || '');
      if (!fullDetail) {{
        let symS = '';
        if (userWatchIconUrl) {{
          symS = '<div class="rotor-aswatch-userimg-24" role="img" aria-hidden="true"></div>';
        }} else {{
          symS = '<div style="width:10px;height:10px;margin-top:2px;border-radius:50%;background:#5B9BD5;border:2px solid #2e6da4;box-shadow:0 1px 2px rgba(0,0,0,0.35);"></div>';
        }}
        const html = '<div style="display:flex;flex-direction:column;align-items:center;pointer-events:auto;">'
          + '<div style="background:' + bubbleBg + ';border:1px solid ' + bubbleBr + ';border-radius:5px;padding:1px 5px;font-size:10px;font-weight:700;line-height:1.2;color:' + bubbleFg + ';white-space:nowrap;max-width:118px;overflow:hidden;text-overflow:ellipsis;">' + call + '</div>'
          + symS + '</div>';
        return L.divIcon({{ html: html, iconSize: [126, 52], iconAnchor: [63, 52], className: 'rotor-aswatch-marker rotor-aswatch-marker-simple' }});
      }}
      const qrgRaw = (m.qrg != null && String(m.qrg).trim()) ? String(m.qrg).trim() : '';
      const qrgHtml = qrgRaw
        ? ('<div style="font-size:10px;font-weight:600;line-height:1.15;margin-top:2px;white-space:nowrap;text-align:center;color:' + bubbleFg + ';">' + _escapeHtmlAswatch(qrgRaw) + '</div>')
        : '';
      let symbol = '';
      if (userWatchIconUrl) {{
        symbol = '<div class="rotor-aswatch-userimg-28" role="img" aria-hidden="true"></div>';
      }} else {{
        symbol = '<div style="width:28px;height:28px;border-radius:50%;background:#5B9BD5;border:2px solid #2e6da4;box-shadow:0 1px 3px rgba(0,0,0,0.4);pointer-events:auto;"></div>';
      }}
      const bubbleInner = '<div style="white-space:nowrap;text-align:center;line-height:1.15;">' + call + '</div>' + qrgHtml;
      const html = '<div style="display:flex;flex-direction:column;align-items:center;">'
        + '<div style="background:' + bubbleBg + ';border:1px solid ' + bubbleBr + ';border-radius:7px;padding:2px 6px;font-size:10px;font-weight:600;line-height:1.15;margin-bottom:3px;color:' + bubbleFg + ';">' + bubbleInner + '</div>'
        + symbol + '</div>';
      const hasQrg = !!qrgRaw;
      const iconH = hasQrg ? 72 : 56;
      return L.divIcon({{ html: html, iconSize: [120, iconH], iconAnchor: [60, iconH], className: 'rotor-aswatch-marker' }});
    }}
    function _syncAswatchMarkerIconsByZoom() {{
      if (!_aswatchMarkerRefs.length) return;
      const wantFull = _aswatchWantFullDetailByZoom();
      let changed = false;
      _aswatchMarkerRefs.forEach(function(mk) {{
        const mm = mk._rotorAswatchData;
        if (!mm) return;
        if (mk._rotorAswatchFullDetail === wantFull) return;
        mk._rotorAswatchFullDetail = wantFull;
        mk.setIcon(_buildAswatchDivIcon(mm, wantFull));
        changed = true;
      }});
      if (changed && aswatchLayer && typeof aswatchLayer.refreshClusters === 'function') {{
        try {{
          requestAnimationFrame(function() {{
            try {{ aswatchLayer.refreshClusters(); }} catch (e2) {{}}
          }});
        }} catch (e) {{
          try {{ aswatchLayer.refreshClusters(); }} catch (e3) {{}}
        }}
      }}
    }}
    let _aswatchSyncZoomTimer = null;
    function _scheduleSyncAswatchMarkerIconsByZoom() {{
      if (_aswatchSyncZoomTimer !== null) clearTimeout(_aswatchSyncZoomTimer);
      _aswatchSyncZoomTimer = setTimeout(function() {{
        _aswatchSyncZoomTimer = null;
        _syncAswatchMarkerIconsByZoom();
      }}, 100);
    }}
    let _lastAsnearestSummary = [];
    function _findAswatchMarkerByDestKey(destKey) {{
      if (!destKey) return null;
      var ri;
      for (ri = 0; ri < _aswatchMarkerRefs.length; ri++) {{
        var ref = _aswatchMarkerRefs[ri];
        if (ref && ref._rotorDestKey === destKey) return ref;
      }}
      var found = null;
      if (aswatchLayer && aswatchLayer.eachLayer) {{
        try {{
          aswatchLayer.eachLayer(function(layer) {{
            if (found) return;
            if (layer instanceof L.Marker && layer._rotorDestKey === destKey) found = layer;
          }});
        }} catch (e) {{}}
      }}
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
        const uoHide = document.getElementById('asnearestUserOnline');
        if (uoHide) uoHide.textContent = '';
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
        const call = _escapeHtmlAswatch(r.call || '');
        const dkAttr = r.dest_key ? _escapeAttrAswatch(r.dest_key) : '';
        const dkJs = r.dest_key ? JSON.stringify(String(r.dest_key)) : 'null';
        html += '<tr class="asnearest-row"' + (dkAttr ? ' data-dest-key="' + dkAttr + '"' : '') + ' style="cursor:pointer;"><td><a href="#" onclick="window.setClickMarker(' + lat + ',' + lon + ');window._rotorSetAz(' + lat + ',' + lon + ',' + dkJs + ');return false;" style="color:inherit;text-decoration:underline;">' + call + '</a></td>';
        html += '<td align="right">' + (r.distance_km != null ? r.distance_km + ' km' : '–') + '</td>';
        html += '<td align="right">' + (r.duration_min != null ? r.duration_min + ' min' : '–') + '</td>';
        html += '<td align="right">' + (r.score != null ? r.score : '–') + '</td></tr>';
      }});
      html += '</tbody></table>';
      listEl.innerHTML = html;
      _bindAsnearestRowHover(listEl);
      _updateAswatchUsersOnlineLine();
    }}
    window.setAsnearestSummary = function(rows) {{
      _lastAsnearestSummary = (rows && rows.length) ? rows.slice() : [];
      _redrawAsnearestPanel();
    }};
    window.setAswatchMarkers = function(arr, totalOnline) {{
      if (_aswatchSyncZoomTimer !== null) {{
        clearTimeout(_aswatchSyncZoomTimer);
        _aswatchSyncZoomTimer = null;
      }}
      _lastAswatch = (arr && arr.length) ? arr.slice() : [];
      if (typeof totalOnline === 'number' && !isNaN(totalOnline) && totalOnline >= 0) {{
        _aswatchTotalOnline = Math.floor(totalOnline);
      }} else {{
        _aswatchTotalOnline = _lastAswatch.length;
      }}
      _aswatchMarkerRefs = [];
      _hiddenAswatchForHover = null;
      if (asnearestHoverFlightLayer) asnearestHoverFlightLayer.clearLayers();
      asnearestHoverLayer.clearLayers();
      aswatchLayer.clearLayers();
      if (!_lastAswatch.length) {{
        if (_hoverDestKey) _clearAsnearestHover();
        _updateAswatchUsersOnlineLine();
        return;
      }}
      const wantFullInit = _aswatchWantFullDetailByZoom();
      _lastAswatch.forEach(function(m) {{
        const icon = _buildAswatchDivIcon(m, wantFullInit);
        const mk = L.marker([m.lat, m.lon], {{ icon: icon, interactive: true }});
        mk._rotorDestKey = (m.dest_key != null && String(m.dest_key).trim()) ? String(m.dest_key).trim() : '';
        mk._rotorAswatchData = m;
        mk._rotorAswatchFullDetail = wantFullInit;
        _aswatchMarkerRefs.push(mk);
        mk.on('click', function(ev) {{
          if (ev && ev.originalEvent) {{ L.DomEvent.stopPropagation(ev.originalEvent); }}
          const lat2 = Number(m.lat);
          const lon2 = Number(m.lon);
          if (isNaN(lat2) || isNaN(lon2)) return;
          window.setClickMarker(lat2, lon2);
          window._rotorSetAz(lat2, lon2, mk._rotorDestKey || null);
        }});
        mk.addTo(aswatchLayer);
      }});
      if (_hoverDestKey) {{
        _showAsnearestListHover(_hoverDestKey);
      }}
      _syncAswatchMarkerIconsByZoom();
      _updateAswatchUsersOnlineLine();
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
    let previewBearingLineLayer = null;
    let hoverPreviewEnabled = {str(hover_preview).lower()};
    function clearPreviewBearingLine() {{
      if (previewBearingLineLayer) {{
        map.removeLayer(previewBearingLineLayer);
        previewBearingLineLayer = null;
      }}
    }}
    function bearingDegSpherical(lat1, lon1, lat2, lon2) {{
      const toRad = Math.PI / 180;
      const phi1 = lat1 * toRad;
      const phi2 = lat2 * toRad;
      const dLon = (lon2 - lon1) * toRad;
      const y = Math.sin(dLon) * Math.cos(phi2);
      const x = Math.cos(phi1) * Math.sin(phi2) - Math.sin(phi1) * Math.cos(phi2) * Math.cos(dLon);
      return (Math.atan2(y, x) / toRad + 360) % 360;
    }}
    function destinationPoint(latDeg, lonDeg, bearingDeg, distKm) {{
      const R = 6371.0;
      const delta = distKm / R;
      const theta = bearingDeg * Math.PI / 180;
      const phi1 = latDeg * Math.PI / 180;
      const lam1 = lonDeg * Math.PI / 180;
      const sinPhi1 = Math.sin(phi1);
      const cosPhi1 = Math.cos(phi1);
      const sinDelta = Math.sin(delta);
      const cosDelta = Math.cos(delta);
      const phi2 = Math.asin(sinPhi1 * cosDelta + cosPhi1 * sinDelta * Math.cos(theta));
      const lam2 = lam1 + Math.atan2(Math.sin(theta) * sinDelta * cosPhi1, cosDelta - sinPhi1 * Math.sin(phi2));
      return [phi2 * 180 / Math.PI, ((lam2 * 180 / Math.PI) + 540) % 360 - 180];
    }}
    function drawPreviewBearingLine(lat2, lon2) {{
      const bearing = bearingDegSpherical(stationLat, stationLon, lat2, lon2);
      const end = destinationPoint(stationLat, stationLon, bearing, rangeKm);
      const coords = [[stationLat, stationLon], end];
      if (previewBearingLineLayer) {{
        previewBearingLineLayer.setLatLngs(coords);
      }} else {{
        previewBearingLineLayer = L.polyline(coords, {{
          color: '#ff6b6b', weight: 2, dashArray: '6, 5', opacity: 0.9, interactive: false
        }}).addTo(map);
      }}
    }}
    window.clearPreviewBearingLine = clearPreviewBearingLine;
    window.setHoverPreviewEnabled = function(on) {{
      hoverPreviewEnabled = !!on;
      if (!hoverPreviewEnabled) {{ clearPreviewBearingLine(); console.log('ROTOR_HOVERAZ:'); }}
    }};
    drawBeamLayers(beamsInitial);
    drawTargetBearingLine(targetBearingLineInitial, targetBearingColorInitial);

    // Web/Mobile: nicht auf den ganzen Beam/Horizont zoomen — sonst wirkt die Karte
    // „festgenagelt“ und Pinch/Pan fühlen sich kaputt an. Desktop-App behält fitBounds.
    if (WEB_MODE) {{
      map.setView([lat, lon], initZoom, {{ animate: false }});
    }} else {{
      const allLayerItems = [marker].concat(beamPolys, beamDashLines);
      if (targetBearingLineLayer) allLayerItems.push(targetBearingLineLayer);
      if (horizonCircle) allLayerItems.push(horizonCircle);
      const allLayer = L.featureGroup(allLayerItems);
      map.fitBounds(allLayer.getBounds().pad(0.1));
    }}
    if (isOffline) {{
      map.setMinZoom(offlineMaxZ);
      map.setMaxZoom(offlineMaxZ);
      map.setZoom(offlineMaxZ);
    }}
    // Nach Layout/Overlays Größe neu berechnen (sonst kaputte Touch-Hitbox)
    setTimeout(function() {{
      try {{
        map.invalidateSize({{ pan: false }});
        if (map.dragging) map.dragging.enable();
        if (map.touchZoom) map.touchZoom.enable();
      }} catch (e) {{}}
    }}, 100);
    setTimeout(function() {{
      try {{ map.invalidateSize({{ pan: false }}); }} catch (e) {{}}
    }}, 500);

    let clickMarker = null;

    window.setClickMarker = function(lat2, lon2) {{
      if (clickMarker) map.removeLayer(clickMarker);
      clickMarker = L.marker([lat2, lon2], antennaTargetIcon ? {{ icon: antennaTargetIcon }} : {{}}).addTo(map);
      clickMarker.bindPopup(popupTarget);
    }};

    window.clearClickMarker = function() {{
      if (clickMarker) {{ map.removeLayer(clickMarker); clickMarker = null; }}
    }};

    let _lastHoverNotify = 0;
    // Touch: Hover-Vorschau aus — mousemove nach Touch stört sonst Drag/Zoom
    if (_isTouchUi) {{
      hoverPreviewEnabled = false;
      try {{
        const chkHover = document.getElementById('webChkHover');
        if (chkHover) chkHover.checked = false;
      }} catch (e) {{}}
    }}
    map.on('mousemove', function(e) {{
      if (!hoverPreviewEnabled || _isTouchUi) return;
      if (map.dragging && map.dragging.moving()) return;
      const lat2 = e.latlng.lat;
      const lon2 = e.latlng.lng;
      drawPreviewBearingLine(lat2, lon2);
      const now = Date.now();
      if (now - _lastHoverNotify < 40) return;
      _lastHoverNotify = now;
      console.log('ROTOR_HOVERAZ:' + lat2 + ',' + lon2);
    }});
    map.getContainer().addEventListener('mouseleave', function() {{
      clearPreviewBearingLine();
      console.log('ROTOR_HOVERAZ:');
    }});

    map.on('click', function(e) {{
      clearPreviewBearingLine();
      const lat2 = e.latlng.lat;
      const lon2 = e.latlng.lng;
      window.setClickMarker(lat2, lon2);
      window._rotorSetAz(lat2, lon2, null);
    }});

    window.updateBeam = function(data) {{
      if (!data) return;
      stationLat = data.lat;
      stationLon = data.lon;
      rangeKm = data.range_km;
      const posChanged = !marker
        || Math.abs(marker.getLatLng().lat - data.lat) > 1e-8
        || Math.abs(marker.getLatLng().lng - data.lon) > 1e-8;
      if (posChanged) {{
        if (marker) map.removeLayer(marker);
        marker = L.marker([data.lat, data.lon], antennaIcon ? {{ icon: antennaIcon }} : {{}}).addTo(map);
      }}
      clearBeamLayers();
      if (horizonCircle) {{ map.removeLayer(horizonCircle); horizonCircle = null; }}
      drawBeamLayers(data.beams || []);
      drawTargetBearingLine(data.target_bearing_line, data.target_bearing_color);
      const hKm = data.horizon_dist_km || 0;
      if (hKm > 0.5) {{
        horizonCircle = L.circle([data.lat, data.lon], {{ radius: hKm * 1000, color: horizonColor, weight: 2, dashArray: '8, 8', fill: false, fillOpacity: 0, interactive: false }}).addTo(map);
      }}
      const infoMain = document.getElementById('infoMainCore') || document.getElementById('infoMain');
      if (infoMain) {{
        let html = '<div><strong>' + (data.info_standort || 'Standort') + ':</strong> ' + data.location_str + '</div>' +
          '<div><strong>' + (data.info_offnung || 'Öffnungswinkel') + ':</strong> ' + data.opening.toFixed(1) + '°</div>' +
          '<div><strong>' + (data.info_reichweite || 'Reichweite') + ':</strong> ' + data.range_km.toFixed(1) + ' km</div>';
        if (data.rig_freq_show) {{
          const rf = (data.rig_freq_text != null) ? String(data.rig_freq_text) : '—';
          const rfAlert = data.rig_freq_out_of_band ? 'color:#dc2626;font-weight:600;' : '';
          html += '<div><strong>' + (data.info_frequenz || 'Frequenz') + ':</strong> '
            + '<span style=\"' + rfAlert + '\">' + _escapeHtmlAswatch(rf) + '</span></div>';
        }}
        infoMain.innerHTML = html;
      }}
    }};

    const OFFLINE_TILE_URL_LIGHT = {json.dumps(tile_url_light)};
    const OFFLINE_TILE_URL_DARK = {json.dumps(tile_url_dark)};
    let _currentTileUrl = {json.dumps(tile_url)};
    function _applyMapZoomLimits() {{
      if (_currentOffline) {{
        const z = offlineMaxZ;
        map.setMinZoom(z);
        map.setMaxZoom(z);
        if (map.getZoom() !== z) {{
          map.setView(map.getCenter(), z, {{ animate: false }});
        }}
      }} else {{
        map.setMinZoom(3);
        map.setMaxZoom(19);
        // Nach minZoom===maxZoom (Offline) können Zoom-Handler „tot“ wirken — neu aktivieren.
        try {{
          if (map.scrollWheelZoom) map.scrollWheelZoom.enable();
          if (map.touchZoom) map.touchZoom.enable();
          if (map.doubleClickZoom) map.doubleClickZoom.enable();
          if (map.boxZoom) map.boxZoom.enable();
          if (map.keyboard) map.keyboard.enable();
        }} catch (e) {{}}
        try {{
          const zc = map.zoomControl;
          if (zc && typeof zc._updateDisabled === 'function') zc._updateDisabled();
        }} catch (e) {{}}
      }}
    }}
    function _replaceBaseTileLayer() {{
      let url, opts;
      if (_mapSatellite && !_currentOffline) {{
        url = TILE_URL_SATELLITE;
        opts = {{ maxZoom: 19, minZoom: 3, attribution: SATELLITE_ATTRIBUTION,
          fadeAnimation: false, keepBuffer: 2, updateWhenIdle: true, updateWhenZooming: false }};
      }} else if (_currentOffline) {{
        url = _mapDark ? OFFLINE_TILE_URL_DARK : OFFLINE_TILE_URL_LIGHT;
        opts = {{ maxZoom: offlineMaxZ, minZoom: offlineMaxZ, attribution: OFFLINE_ATTRIBUTION,
          fadeAnimation: false, keepBuffer: 2, updateWhenIdle: true, updateWhenZooming: false }};
      }} else {{
        // Online: immer Street-Map; Dark-Look per CSS-Filter auf .leaflet-tile-pane.
        url = TILE_URL_LIGHT;
        opts = {{ maxZoom: 19, minZoom: 3, attribution: ONLINE_ATTRIBUTION,
          fadeAnimation: false, keepBuffer: 2, updateWhenIdle: true, updateWhenZooming: false }};
      }}
      if (tileLayer && _currentTileUrl === url) {{
        // Gleiche URL (z. B. Offline-Fallback = OSM): trotzdem Zoom-Limits anpassen!
        _applyMapZoomLimits();
        _syncMapBodyTileClasses();
        return;
      }}
      _currentTileUrl = url;
      if (tileLayer) map.removeLayer(tileLayer);
      tileLayer = L.tileLayer(url, opts).addTo(map);
      if (!_currentOffline && !_mapSatellite) {{
        tileLayer.on('tileerror', function(e) {{
          if (!_currentOffline) console.error('ROTOR_TILEERROR');
        }});
      }}
      _applyMapZoomLimits();
      _syncMapBodyTileClasses();
    }}
    window.setMapSatelliteMode = function(on) {{
      if (_currentOffline && on) {{
        _mapSatellite = false;
        _syncMapBodyTileClasses();
        return false;
      }}
      const want = !!on;
      if (_mapSatellite === want) return _mapSatellite;
      _mapSatellite = want;
      _replaceBaseTileLayer();
      return _mapSatellite;
    }};
    window.setMapOfflineMode = function(offline, tileUrl) {{
      const wasOffline = _currentOffline;
      const hadSatellite = _mapSatellite;
      _currentOffline = !!offline;
      if (_currentOffline && _mapSatellite) {{
        _mapSatellite = false;
      }}
      // Layer-URL-Cache leeren, damit Zoom-Limits und Tile-Opts immer neu gesetzt werden
      // (wichtig wenn Offline-Fallback dieselbe OSM-URL wie Online nutzt).
      if (wasOffline !== _currentOffline || hadSatellite !== _mapSatellite) {{
        _currentTileUrl = null;
        _replaceBaseTileLayer();
      }} else {{
        _applyMapZoomLimits();
        _syncMapBodyTileClasses();
      }}
    }};

    window.setMapDarkMode = function(dark) {{
      const wasDark = _mapDark;
      _mapDark = !!dark;
      if (wasDark !== _mapDark) {{
        // Online: gleiche Street-Tiles, nur Filter wechseln — Offline: Dark/Light-Tiles tauschen.
        if (_currentOffline) {{
          _replaceBaseTileLayer();
        }} else {{
          _syncMapBodyTileClasses();
        }}
      }} else {{
        _syncMapBodyTileClasses();
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
      _mapDarkAswatch = dark;
      if (typeof window.setAswatchMarkers === 'function') window.setAswatchMarkers(_lastAswatch, _aswatchTotalOnline);
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
    let _locatorSuspended = false;
    let _locPrec = 2, _locDispLen = 2;
    function _suspendLocatorForGesture() {{
      if (!locatorVisible || _locatorSuspended) return;
      _locatorSuspended = true;
      try {{
        if (locatorLayer && map.hasLayer(locatorLayer)) map.removeLayer(locatorLayer);
        if (locatorLabelLayer && map.hasLayer(locatorLabelLayer)) map.removeLayer(locatorLabelLayer);
      }} catch (e) {{}}
    }}
    function _resumeLocatorAfterGesture() {{
      if (!_locatorSuspended) return;
      _locatorSuspended = false;
      if (!locatorVisible) return;
      _currentLocatorKey = '';
      if (typeof _scheduleLocatorRefresh === 'function') _scheduleLocatorRefresh();
      else {{
        try {{ _updateLocatorPrecision(); }} catch (e) {{}}
      }}
    }}
    // Präzision je nach Zoom (2/4/6/8 Zeichen Maidenhead).
    // Ab maxZoom−3 (eine Stufe früher als „drittletzte“) … 8: Raster inkl. Ziffernpaar
    // nach den Subquadrat-Buchstaben (z. B. …km18), vgl. maidenhead.js precision 8.
    function _locatorFineZoomFrom() {{
      return Math.max(3, map.getMaxZoom() - 3);
    }}
    function _precisionForZoom(z) {{
      const zFine = _locatorFineZoomFrom();
      if (z < 7) return 2;
      if (z < 12) return 4;
      if (z < zFine) return 6;
      return 8;
    }}
    function _displayLengthForZoom(z) {{
      const zFine = _locatorFineZoomFrom();
      if (z < 3) return 1;
      if (z < 7) return 2;
      if (z < 12) return 4;
      if (z < zFine) return 6;
      return 8;
    }}
    // Zellgröße in Grad für die jeweilige Präzision
    // Werte aus maidenhead.js: latDelta, lngDelta = latDelta * 2
    function _cellSize(prec) {{
      if (prec === 2) return {{lat: 10,       lng: 20}};
      if (prec === 4) return {{lat: 1,        lng: 2}};
      if (prec === 6) return {{lat: 2.5/60,   lng: 5/60}};
      return             {{lat: 0.25/60, lng: 0.5/60}};
    }}
    // Nur Gitternetz – Beschriftung übernehmen wir selbst
    function _createLocatorGridLayer(isDark, precision) {{
      const layer = L.maidenhead({{
        precision: precision,
        // interactive:false — sonst fangen die Locator-Polygone Pinch/Pan ab
        polygonStyle: {{
          color: isDark ? '#b0b0b0' : '#333',
          weight: 0.5,
          fill: false,
          fillOpacity: 0,
          interactive: false,
          bubblingMouseEvents: false
        }},
        // Keine Dummy-Marker pro Zelle (Labels kommen aus locatorLabelLayer)
        spawnMarker: function() {{ return null; }}
      }});
      return layer;
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
      if (!locatorVisible || !map || _locatorSuspended) return;
      const z       = map.getZoom();
      const prec    = _precisionForZoom(z);
      const dispLen = _displayLengthForZoom(z);
      const key     = map.getMaxZoom() + '-' + prec + '-' + dispLen;
      if (key === _currentLocatorKey && locatorLayer) {{
        if (locatorLayer && !map.hasLayer(locatorLayer)) map.addLayer(locatorLayer);
        if (locatorLabelLayer && !map.hasLayer(locatorLabelLayer)) map.addLayer(locatorLabelLayer);
        return;
      }}
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
    let _locatorRefreshTimer = null;
    function _scheduleLocatorRefresh() {{
      if (!locatorVisible) return;
      if (_locatorRefreshTimer) clearTimeout(_locatorRefreshTimer);
      _locatorRefreshTimer = setTimeout(function() {{
        _locatorRefreshTimer = null;
        if (!locatorVisible) return;
        if (typeof _isMapGestureBusy === 'function' && _isMapGestureBusy()) {{
          _scheduleLocatorRefresh();
          return;
        }}
        _updateLocatorPrecision();
        _updateLocatorLabels();
      }}, _isTouchUi ? 180 : 40);
    }}
    // Kein Rebuild während Pinch/Pan — nur nach abgeschlossener Geste
    map.on('zoomend', function() {{
      _scheduleLocatorRefresh();
      _scheduleSyncAswatchMarkerIconsByZoom();
    }});
    map.on('moveend', function() {{
      _scheduleLocatorRefresh();
    }});
    window.setMapLocatorOverlay = function(show, darkMode) {{
      const want = !!show;
      const darkChanged = (darkMode !== undefined) && (!!darkMode !== !!_mapDark);
      if (darkMode !== undefined) _mapDark = darkMode;
      // Idempotent: SSE ruft Chrome oft auf — Overlay nicht jedes Mal neu aufbauen.
      if (want === locatorVisible && !(want && darkChanged)) return;
      locatorVisible = want;
      if (want) {{
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

    if (WEB_MODE && typeof EventSource !== 'undefined') {{
      try {{
        const es = new EventSource('/api/events');
        es.addEventListener('beam', function(ev) {{
          try {{
            const data = JSON.parse(ev.data);
            if (typeof _isMapGestureBusy === 'function' && _isMapGestureBusy()) {{
              _pendingBeamData = data;
            }} else if (typeof window.updateBeam === 'function') {{
              window.updateBeam(data);
            }}
            if (data && data.chrome) _webApplyChrome(data.chrome);
          }} catch (e) {{}}
        }});
        es.addEventListener('aswatch', function(ev) {{
          try {{
            const msg = JSON.parse(ev.data);
            if (typeof _isMapGestureBusy === 'function' && _isMapGestureBusy()) {{
              _pendingAswatch = msg;
            }} else if (typeof window.setAswatchMarkers === 'function') {{
              window.setAswatchMarkers(msg.items || [], msg.total);
            }}
          }} catch (e) {{}}
        }});
        es.addEventListener('aircraft', function(ev) {{
          try {{
            const msg = JSON.parse(ev.data);
            if (typeof _isMapGestureBusy === 'function' && _isMapGestureBusy()) {{
              _pendingAircraft = msg;
            }} else if (typeof window.setAirplaneMarkers === 'function') {{
              window.setAirplaneMarkers(msg.items || []);
            }}
          }} catch (e) {{}}
        }});
        es.addEventListener('asnearest', function(ev) {{
          try {{
            const msg = JSON.parse(ev.data);
            if (typeof window.setAsnearestSummary === 'function') {{
              window.setAsnearestSummary(msg.items || []);
            }}
          }} catch (e) {{}}
        }});
      }} catch (e) {{}}
    }}
    if (WEB_MODE) {{
      try {{ _webBindChrome(); }} catch (e) {{}}
    }}

  </script>
</body>
</html>"""
