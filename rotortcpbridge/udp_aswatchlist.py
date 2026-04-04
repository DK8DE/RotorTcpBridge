"""UDP-Listener für AirScout/KST ASWATCHLIST- und ASSETPATH-Broadcasts.

Empfängt z. B. auf Port 9872 Nachrichten der Form::

    ASWATCHLIST: \"…\" \"…\" 1440000,OWNCALL,JO62RM,OK1ABC,JO70AA,...
    (Die beiden ersten Anführungszeichen-Blöcke sind z. B. PY/AS und können variieren;
    sie werden nicht ausgewertet, nur das CSV dahinter.)
    ASSETPATH:   \"…\" \"…\" 1440000,OWNCALL,JO62RM,OK1ABC,JO70AA
    (Ebenfalls beliebige Präfix-Strings in Anführungszeichen vor dem CSV — werden ignoriert.)

**Zusammenführung (Karte):**

- **ASWATCHLIST** = Gesamtliste: definiert, *welche* Rufzeichen angezeigt werden.
  Wer nicht mehr in der Liste steht, wird von der Karte entfernt.
- **ASSETPATH** = Einzelabfrage: fügt/aktualisiert genau ein Gegen-Rufzeichen
  (Locator). Erscheint nur auf der Karte, bis die nächste **ASWATCHLIST**
  dieses Rufzeichen nicht mehr enthält – dann entfällt es mit der Gesamtliste.

Eigene Station (CSV-Feld 2) wird nicht als Marker angezeigt.

Das **Band / QRG** (CSV-Feld 0, z. B. ``1440000``) wird nur aus **ASSETPATH**-
Einzelpaketen ausgewertet und als Tooltip am User-Symbol angezeigt – nicht aus
der **ASWATCHLIST**-Gesamtabfrage.

**ASNEAREST** liefert pro Gegenstation mögliche Flugzeuge (Distanz km, Potenzial %,
Restzeit min). Position auf der Karte ist eine **Heuristik** entlang der Großkreislinie
eigenes QTH → Ziel; Rohdaten werden optional nach ``asnearest.jsonl`` protokolliert.

Bind mit SO_REUSEADDR und (falls vorhanden) SO_REUSEPORT, damit der Port
nach Möglichkeit nicht exklusiv blockiert wird (abhängig vom Betriebssystem).
"""

from __future__ import annotations

import csv
import json
import socket
import threading
import time
from collections import defaultdict
from io import StringIO
from typing import Any, Callable

from .geo_utils import (
    destination_point,
    effective_station_lat_lon,
    haversine_km,
    maidenhead_to_lat_lon,
    point_along_path_km,
    reflection_path_fraction_and_midpoint_factor,
)
from .logutil import appdata_dir
from .net_utils import normalize_udp_bind_host


def format_qrg_display(raw: str | None) -> str | None:
    """
    CSV-Feld „Band“ (z. B. ``1440000``) → lesbare Frequenz für Tooltip / Anzeige.

    Heuristik (AirScout/KST-typisch):

    - Werte **≥ 10 000 000** (z. B. ``14000000``) → als Frequenz in **Hz** (HF).
    - Werte **1 000 000 … 9 999 999** (z. B. ``1440000``) → oft **100-Hz-Schritte**,
      d. h. ``× 100`` → Hz (VHF/UHF).
    - Kleinere positive Ganzzahlen → als **MHz** (z. B. ``144`` → 144 MHz).
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    try:
        n = int(s.replace(",", ""))
    except ValueError:
        return s
    if n <= 0:
        return None
    hz: float
    if n >= 10_000_000:
        hz = float(n)
    elif n >= 1_000_000:
        hz = float(n) * 100.0
    else:
        hz = float(n) * 1e6
    mhz = hz / 1e6
    if mhz >= 1000.0:
        return f"{mhz / 1000:.6f} GHz"
    return f"{mhz:.3f} MHz"


def _strip_leading_quoted_tokens(s: str) -> str:
    """
    Entfernt führende ``\"…\"``-Blöcke (AirScout-Präfixe, z. B. PY/AS).

    Inhalt und Anzahl können variieren; für die Karte zählt nur das CSV danach.
    """
    s = s.strip()
    while s.startswith('"'):
        end = s.find('"', 1)
        if end == -1:
            break
        s = s[end + 1 :].strip()
    return s


def _split_udp_csv_payload(nachricht: str, prefix: str) -> list[str] | None:
    """CSV-Felder nach allen führenden ``\"…\"``-Blöcken; ``None`` wenn nichts Nützliches übrig bleibt."""
    if not nachricht.startswith(prefix):
        return None
    inhalt = nachricht[len(prefix) :].strip()
    datenblock = _strip_leading_quoted_tokens(inhalt)
    if not datenblock:
        return None
    return [e.strip() for e in datenblock.split(",") if e.strip()]


def parse_aswatchlist(nachricht: str) -> list[tuple[str, str]]:
    """Zerlegt eine ASWATCHLIST-Nachricht; Liste (Rufzeichen, Locator). QRG wird ignoriert."""
    daten = _split_udp_csv_payload(nachricht, "ASWATCHLIST:")
    if not daten or len(daten) < 3:
        return []

    gegenstationen = daten[3:]

    ergebnis: list[tuple[str, str]] = []
    for i in range(0, len(gegenstationen) - 1, 2):
        rufzeichen = gegenstationen[i]
        locator = gegenstationen[i + 1]
        ergebnis.append((rufzeichen, locator))

    return ergebnis


def parse_assetpath(nachricht: str) -> tuple[str, str, str] | None:
    """
    Zerlegt ASSETPATH (ein Ziel): ``(Rufzeichen, Locator, rohes Bandfeld)`` oder ``None``.

    Format wie AirScout (Präfix-Strings in Anführungszeichen können variieren)::
        ASSETPATH: \"…\" \"…\" 1440000,OWNCALL,OWNLOC,TARGET,TARGETLOC

    Eigene Station (Felder 1–2) wird nicht zurückgegeben (kein Marker für self).
    """
    daten = _split_udp_csv_payload(nachricht, "ASSETPATH:")
    if not daten or len(daten) < 5:
        return None
    qrg_raw = daten[0]
    own_call = daten[1]
    target_call = daten[3]
    target_loc = daten[4]
    if not target_call or not target_loc:
        return None
    if own_call.strip().upper() == target_call.strip().upper():
        return None
    return (target_call, target_loc, qrg_raw)


def parse_asnearest(nachricht: str) -> dict[str, Any] | None:
    """
    Zerlegt **ASNEAREST** (AirScout, vgl. kst4contest ``ReadUDPbyAirScoutMessageThread``).

    Dritter ``\"…\"``-Block: CSV mit Zeitstempel, eigenem Rufzeichen/Locator, Ziel-Rufzeichen/Locator,
    Anzahl Flugzeuge, danach je Flugzeug 5 Felder: Kennung, Kategorie (Größe, z. B. H/M/S — **keine** Flughöhe),
    Distanz km, Potenzial 0–100, Dauer Minuten bis Ankunft.

    **Hinweis:** Endet die CSV mit ``,...,0`` (nur sechs Felder), ist die Anzahl **0** — es gibt
    keine Flugzeug-Daten in diesem Paket (AirScout sendet dann oft viele solcher Meldungen pro Ziel).
    """
    s = nachricht.replace("\x00", "").strip()
    if not s.upper().startswith("ASNEAREST:"):
        return None
    parts = s.split('"')
    if len(parts) < 6:
        return None
    inner = parts[5].strip()
    try:
        fields = next(csv.reader(StringIO(inner)))
    except Exception:
        return None
    if len(fields) < 6:
        return None
    try:
        count = int(str(fields[5]).strip())
    except ValueError:
        return None
    planes: list[dict[str, Any]] = []
    for i in range(count):
        base = 6 + i * 5
        if base + 5 > len(fields):
            break
        try:
            dist = int(str(fields[base + 2]).strip())
            pot = int(str(fields[base + 3]).strip())
            dur = int(str(fields[base + 4]).strip())
        except ValueError:
            continue
        planes.append(
            {
                "flight": str(fields[base]).strip(),
                "category": str(fields[base + 1]).strip(),
                "distance_km": dist,
                "potential": pot,
                "duration_min": dur,
            }
        )
    return {
        "timestamp": str(fields[0]).strip(),
        "sender_call": str(fields[1]).strip(),
        "sender_loc": str(fields[2]).strip(),
        "dest_call": str(fields[3]).strip(),
        "dest_loc": str(fields[4]).strip(),
        "count": count,
        "planes": planes,
    }


def composite_asnearest_score(potential: int, duration_min: int) -> int:
    """
    Kombinierter Score 0–100 (analog kst4contest PriorityCalculator, Abschnitt AIRSCOUT BOOST;
    Quelle: github.com/praktimarc/kst4contest PriorityCalculator.java):
    AirScout-Potenzial (0–100 %) plus Zuschläge für sehr kurze Ankunftszeit
    (vergleichbar +120/+60/+30 für 0/1/2 min dort, hier auf 0–15 Punkte skaliert).
    Bei sehr langer Restzeit (>90 min) leichte Abschwächung.
    """
    p = max(0, min(100, int(potential)))
    d = int(duration_min)
    bonus = 0
    if d <= 0:
        bonus = 15
    elif d == 1:
        bonus = 10
    elif d == 2:
        bonus = 5
    raw = float(p) + float(bonus)
    if d > 90:
        raw *= max(0.75, 1.0 - (d - 90) / 180.0)
    return int(round(max(0.0, min(100.0, raw))))


def category_altitude_proxy(cat: str) -> float:
    """
    AirScout-UDP enthält **keine** Flughöhe; das zweite Flugzeugfeld ist eine Größen-/Kategoriekennung
    (vgl. KST ``AirPlane.setApSizeCategory`` — typ. H/M/S). Größere Muster korrelieren grob mit
    höherer Reiseflughöhe → besserer Aircraft-Scatter auf langen Pfaden.
    """
    u = (cat or "").strip().upper()
    if not u:
        return 0.82
    c0 = u[0]
    if c0 == "H":
        return 1.0
    if c0 == "M":
        return 0.88
    if c0 == "S":
        return 0.62
    if c0 == "N":
        return 0.76
    return 0.78


def path_length_category_factor(d_tot_km: float, cat: str) -> float:
    """
    Auf **kurzen** Verbindungen wenig einschränken; auf **langen** DX-Strecken den Faktor Richtung
    ``category_altitude_proxy`` ziehen (niedrige Proxy-Höhe = schlechter bei großer Bodenentfernung).
    """
    cat_f = category_altitude_proxy(cat)
    need = min(1.0, max(0.0, (float(d_tot_km) - 150.0) / 450.0))
    return 1.0 - need * (1.0 - cat_f)


def asnearest_score_with_geometry(
    own_ll: tuple[float, float],
    dest_ll: tuple[float, float],
    dist_from_own_km: float,
    potential: int,
    duration_min: int,
    category: str = "",
    *,
    use_category_path: bool = True,
) -> int:
    """AirScout-Basis × Mitte-Faktor g × optional Streckenlänge/Kategorie (Höhe nicht in UDP)."""
    _t, g = reflection_path_fraction_and_midpoint_factor(
        dist_from_own_km, own_ll[0], own_ll[1], dest_ll[0], dest_ll[1]
    )
    base = composite_asnearest_score(potential, duration_min)
    d_tot = haversine_km(own_ll[0], own_ll[1], dest_ll[0], dest_ll[1])
    p_cat = path_length_category_factor(d_tot, category) if use_category_path else 1.0
    return int(round(float(base) * g * p_cat))


def marker_asnearest_score(m: dict[str, Any]) -> int:
    """Liest ``score`` aus dem Marker (inkl. Geometrie) oder fällt auf AirScout-Basis zurück."""
    s = m.get("score")
    if s is not None:
        try:
            return int(s)
        except (TypeError, ValueError):
            pass
    return composite_asnearest_score(int(m.get("potential", 0)), int(m.get("duration_min", 9999)))


def pick_best_asnearest_plane(
    planes: list[dict[str, Any]],
    own_ll: tuple[float, float],
    dest_ll: tuple[float, float],
    use_category_path: bool = True,
) -> dict[str, Any] | None:
    """Ein Kandidat pro Ziel: höchster Score inkl. Mitte-Geometrie, dann Restzeit, dann Potenzial."""
    if not planes:
        return None

    def _sort_key(pl: dict[str, Any]) -> tuple:
        try:
            dk = float(pl["distance_km"])
        except (KeyError, TypeError, ValueError):
            dk = 0.0
        pot = int(pl.get("potential", 0))
        dur = int(pl.get("duration_min", 9999))
        cat = str(pl.get("category", ""))
        sc = asnearest_score_with_geometry(
            own_ll, dest_ll, dk, pot, dur, cat, use_category_path=use_category_path
        )
        return (-sc, dur, -pot)

    return min(planes, key=_sort_key)


def _build_markers_for_map(
    entries: list[tuple[str, str, str | None]],
    spacing_km: float = 0.012,
) -> list[dict[str, Any]]:
    """
    Wandelt (Rufzeichen, Locator, optionale QRG-Anzeige) in Karten-Marker um.
    Mehrere Stationen im gleichen Locator werden nebeneinander (Ost-West) versetzt.
    """
    by_loc: dict[str, list[tuple[str, str | None]]] = defaultdict(list)
    for call, loc, qrg in entries:
        loc_u = loc.strip().upper()
        c = call.strip()
        if not loc_u or not c:
            continue
        by_loc[loc_u].append((c, qrg))

    out: list[dict[str, Any]] = []
    for loc_u, calls in by_loc.items():
        ll = maidenhead_to_lat_lon(loc_u)
        if ll is None:
            continue
        lat, lon = ll
        calls_sorted = sorted(set(calls), key=lambda t: t[0])
        n = len(calls_sorted)
        for i, (call, qrg) in enumerate(calls_sorted):
            off_km = (i - (n - 1) / 2.0) * spacing_km
            lat2, lon2 = destination_point(lat, lon, 90.0, off_km)
            dk = f"{call.strip().upper()}|{loc_u}"
            item: dict[str, Any] = {"call": call, "lat": lat2, "lon": lon2, "dest_key": dk}
            if qrg:
                item["qrg"] = qrg
            out.append(item)
    return out


EmitFn = Callable[[list[dict[str, Any]]], None]
EmitAirFn = Callable[[list[dict[str, Any]]], None]
EmitSummaryFn = Callable[[list[dict[str, Any]]], None]


class UdpAswatchlistListener:
    """Hört auf UDP, wertet ASWATCHLIST + ASSETPATH + ASNEAREST aus und ruft Callbacks auf."""

    def __init__(
        self,
        log,
        cfg: dict | None,
        emit_fn: EmitFn,
        emit_air_fn: EmitAirFn | None = None,
        emit_summary_fn: EmitSummaryFn | None = None,
    ):
        self.log = log
        self.cfg = cfg or {}
        self._emit_fn = emit_fn
        self._emit_air_fn = emit_air_fn
        self._emit_summary_fn = emit_summary_fn
        self._enabled = False
        self._port = 9872
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self.packet_received_flag = False
        # Zusammengeführter Zustand: Rufzeichen (UPPER) -> Locator; Label; QRG-Anzeige
        self._call_to_loc: dict[str, str] = {}
        self._call_label: dict[str, str] = {}
        self._call_to_qrg: dict[str, str] = {}
        # ASNEAREST: Ziel (Rufzeichen|Locator) → Flugzeug-Marker für die Karte
        self._planes_by_dest: dict[str, list[dict[str, Any]]] = {}
        # Letztes geparstes ASNEAREST-Paket pro Ziel (für Liste ohne vollen Marker-Bau; Klick → Linie)
        self._asnearest_parsed_by_dest: dict[str, dict[str, Any]] = {}
        # Bei „Nur auf Klick“: gewähltes Ziel (dest_key) oder None
        self._asnearest_selected_dest_key: str | None = None
        # Pro Ziel: zuletzt gewähltes Flugzeug (Kennung) — gleiche ETA-Zeile über mehrere UDP-Pakete
        self._asnearest_sticky_flight: dict[str, str] = {}
        self._asnearest_zero_hint_logged = False
        self._asnearest_planes_hint_logged = False

    @staticmethod
    def _norm_call(call: str) -> str:
        return call.strip().upper()

    def _aircraft_enabled(self) -> bool:
        """True: ASNEAREST verarbeiten und Flugzeuge/Linien auf der Karte."""
        return bool(self.cfg.get("ui", {}).get("aswatch_aircraft_enabled", True))

    @staticmethod
    def _dest_key(dest_call: str, dest_loc: str) -> str:
        return f"{dest_call.strip().upper()}|{dest_loc.strip().upper()}"

    def _valid_dest_keys_for_map(self) -> set[str]:
        """Alle ``Rufzeichen|Locator``-Schlüssel, die aktuell zur Karte gehören."""
        return {self._dest_key(k, loc_u) for k, loc_u in self._call_to_loc.items()}

    def _prune_asnearest_not_on_map(self) -> None:
        """Entfernt ASNEAREST-Daten für Ziele, die nicht mehr in ``_call_to_loc`` sind."""
        valid = self._valid_dest_keys_for_map()
        for key in list(self._planes_by_dest.keys()):
            if key not in valid:
                self._planes_by_dest.pop(key, None)
                self._asnearest_sticky_flight.pop(key, None)
        for key in list(self._asnearest_parsed_by_dest.keys()):
            if key not in valid:
                self._asnearest_parsed_by_dest.pop(key, None)
                self._asnearest_sticky_flight.pop(key, None)
        if self._asnearest_selected_dest_key and self._asnearest_selected_dest_key not in valid:
            self._asnearest_selected_dest_key = None

    def _asnearest_dest_on_map(self, parsed: dict[str, Any]) -> bool:
        """True, wenn das ASNEAREST-Ziel aktuell als Karten-Marker (Call+Locator) geführt wird."""
        k = self._norm_call(str(parsed.get("dest_call", "")))
        if not k or k not in self._call_to_loc:
            return False
        loc_map = self._call_to_loc[k].strip().upper()
        loc_p = str(parsed.get("dest_loc", "")).strip().upper()
        return loc_map == loc_p

    def _resolve_own_lat_lon(self, sender_loc: str) -> tuple[float, float] | None:
        ll = maidenhead_to_lat_lon(sender_loc)
        if ll is not None:
            return ll
        ui = self.cfg.get("ui", {}) or {}
        try:
            return effective_station_lat_lon(ui)
        except (TypeError, ValueError):
            return None

    def _pick_plane_for_dest(
        self,
        planes_in: list[dict[str, Any]],
        own_ll: tuple[float, float],
        dest_ll: tuple[float, float],
        dest_key: str,
        use_cat_path: bool,
    ) -> dict[str, Any] | None:
        """Wählt ein Flugzeug: bei ``asnearest_sticky_flight`` dasselbe wie zuvor, falls noch in der Liste."""
        ui = self.cfg.get("ui", {})
        if not bool(ui.get("asnearest_sticky_flight", True)):
            return (
                pick_best_asnearest_plane(planes_in, own_ll, dest_ll, use_category_path=use_cat_path)
                if planes_in
                else None
            )
        if not planes_in:
            self._asnearest_sticky_flight.pop(dest_key, None)
            return None
        sticky = self._asnearest_sticky_flight.get(dest_key)
        if sticky:
            su = sticky.strip().upper()
            for pl in planes_in:
                fn = str(pl.get("flight", "")).strip().upper()
                if fn and fn == su:
                    return pl
        best = pick_best_asnearest_plane(planes_in, own_ll, dest_ll, use_category_path=use_cat_path)
        if best is not None:
            fn = str(best.get("flight", "")).strip()
            if fn:
                self._asnearest_sticky_flight[dest_key] = fn
        return best

    def _append_asnearest_jsonl(self, raw: str, parsed: dict[str, Any] | None) -> None:
        if not self._aircraft_enabled():
            return
        if not self.cfg.get("ui", {}).get("asnearest_jsonl_log", True):
            return
        try:
            path = appdata_dir() / "asnearest.jsonl"
            rec = {"ts": time.time(), "raw": raw, "parsed": parsed}
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def _build_aircraft_markers_for_packet(self, p: dict[str, Any]) -> list[dict[str, Any]]:
        """Pro Gegenstation genau **ein** Reflexionspunkt: bester Kandidat (Score, dann Restzeit).

        AirScout liefert oft viele Flugzeuge pro Ziel; mehrere Linien pro DX würden die Karte
        unlesbar machen — analog „beste“ Auswahl wie in der Infotabelle.

        **Sticky-Flug** (``asnearest_sticky_flight``): Sobald ein Flugzeug gewählt ist, bleibt
        dieselbe Kennung aktiv, bis sie nicht mehr in der Liste steht — sonst würde bei jedem
        UDP-Paket ein anderes „bestes“ Flugzeug die **Restzeit** springen lassen.
        """
        own_ll = self._resolve_own_lat_lon(p["sender_loc"])
        if own_ll is None:
            return []
        dest_ll = maidenhead_to_lat_lon(p["dest_loc"].strip())
        if dest_ll is None:
            return []
        ui = self.cfg.get("ui", {})
        try:
            pot_min = int(ui.get("asnearest_line_potential_min", 50))
        except (TypeError, ValueError):
            pot_min = 50
        try:
            dur_max = int(ui.get("asnearest_line_duration_max_min", 120))
        except (TypeError, ValueError):
            dur_max = 120
        try:
            geom_min = float(ui.get("asnearest_geom_factor_min", 0.20))
        except (TypeError, ValueError):
            geom_min = 0.20
        geom_min = max(0.0, min(1.0, geom_min))
        use_cat_path = bool(ui.get("asnearest_use_category_path", True))
        planes_in = list(p.get("planes") or [])
        dest_key = self._dest_key(str(p.get("dest_call", "")), str(p.get("dest_loc", "")))
        pl = self._pick_plane_for_dest(planes_in, own_ll, dest_ll, dest_key, use_cat_path)
        if pl is None:
            return []
        try:
            dist_km = float(pl["distance_km"])
        except (KeyError, TypeError, ValueError):
            return []
        lat, lon = point_along_path_km(own_ll[0], own_ll[1], dest_ll[0], dest_ll[1], dist_km)
        path_t, geom_g = reflection_path_fraction_and_midpoint_factor(
            dist_km, own_ll[0], own_ll[1], dest_ll[0], dest_ll[1]
        )
        pot = int(pl.get("potential", 0))
        dur = int(pl.get("duration_min", 9999))
        cat = str(pl.get("category", ""))
        d_tot_path = haversine_km(own_ll[0], own_ll[1], dest_ll[0], dest_ll[1])
        p_cat = path_length_category_factor(d_tot_path, cat) if use_cat_path else 1.0
        link_ok = pot >= pot_min and dur <= dur_max and geom_g >= geom_min
        score = asnearest_score_with_geometry(
            own_ll, dest_ll, dist_km, pot, dur, cat, use_category_path=use_cat_path
        )
        return [
            {
                "lat": lat,
                "lon": lon,
                "dest_key": dest_key,
                "flight": pl.get("flight", ""),
                "partner": p["dest_call"],
                "dest_loc": str(p.get("dest_loc", "")).strip().upper(),
                "partner_lat": dest_ll[0],
                "partner_lon": dest_ll[1],
                "distance_km": int(pl.get("distance_km", 0)),
                "potential": pot,
                "duration_min": dur,
                "score": score,
                "path_fraction": round(path_t, 5),
                "geom_factor": round(geom_g, 5),
                "alt_path_factor": round(p_cat, 5),
                "category": cat,
                "link_ok": link_ok,
                "timestamp": p.get("timestamp", ""),
            }
        ]

    def _own_lat_lon_cfg(self) -> tuple[float, float]:
        ui = self.cfg.get("ui", {}) or {}
        return effective_station_lat_lon(ui)

    def _summary_max_rows(self) -> int:
        try:
            return max(0, min(500, int(self.cfg.get("ui", {}).get("asnearest_list_max_rows", 20))))
        except (TypeError, ValueError):
            return 20

    def _summary_row_from_parsed(self, parsed: dict[str, Any]) -> dict[str, Any] | None:
        """Eine Tabellenzeile ohne vollständigen Flugzeug-Marker (Reflexionspunkt/Linie erst bei Auswahl)."""
        ui = self.cfg.get("ui", {})
        try:
            min_score = int(ui.get("asnearest_min_score", 45))
        except (TypeError, ValueError):
            min_score = 45
        min_score = max(0, min(100, min_score))
        try:
            list_max_min = int(ui.get("asnearest_list_max_minutes", 0))
        except (TypeError, ValueError):
            list_max_min = 0
        list_max_min = max(0, list_max_min)
        own_ll = self._resolve_own_lat_lon(str(parsed.get("sender_loc", "")))
        if own_ll is None:
            return None
        dest_ll = maidenhead_to_lat_lon(str(parsed.get("dest_loc", "")).strip())
        if dest_ll is None:
            return None
        dest_key = self._dest_key(str(parsed.get("dest_call", "")), str(parsed.get("dest_loc", "")))
        use_cat_path = bool(ui.get("asnearest_use_category_path", True))
        planes_in = list(parsed.get("planes") or [])
        pl = self._pick_plane_for_dest(planes_in, own_ll, dest_ll, dest_key, use_cat_path)
        if pl is None:
            return None
        pot = int(pl.get("potential", 0))
        dur = int(pl.get("duration_min", 9999))
        try:
            dist_km_plane = float(pl["distance_km"])
        except (KeyError, TypeError, ValueError):
            return None
        cat = str(pl.get("category", ""))
        score = int(
            round(
                asnearest_score_with_geometry(
                    own_ll, dest_ll, dist_km_plane, pot, dur, cat, use_category_path=use_cat_path
                )
            )
        )
        if score < min_score:
            return None
        if list_max_min > 0 and dur > list_max_min:
            return None
        own = self._own_lat_lon_cfg()
        plat = float(dest_ll[0])
        plon = float(dest_ll[1])
        try:
            d_km = haversine_km(own[0], own[1], plat, plon)
        except Exception:
            d_km = 0.0
        return {
            "call": str(parsed.get("dest_call", "")).strip(),
            "lat": plat,
            "lon": plon,
            "distance_km": int(round(d_km)),
            "duration_min": dur,
            "potential": pot,
            "score": score,
            "dest_key": dest_key,
        }

    def _build_asnearest_summary_rows(self, max_rows: int | None = None) -> list[dict[str, Any]]:
        """Pro Gegenstation eine Zeile: kürzeste Restzeit zuerst; nur Score ≥ min; max. Zeilen aus cfg."""
        ui = self.cfg.get("ui", {})
        try:
            min_score = int(ui.get("asnearest_min_score", 45))
        except (TypeError, ValueError):
            min_score = 45
        min_score = max(0, min(100, min_score))
        aircraft_on = self._aircraft_enabled()
        rows: list[dict[str, Any]] = []
        for _key, parsed in sorted(self._asnearest_parsed_by_dest.items(), key=lambda x: x[0]):
            row = self._summary_row_from_parsed(parsed)
            if row is None:
                continue
            if aircraft_on:
                markers = self._build_aircraft_markers_for_packet(parsed)
                for m in markers:
                    if m.get("link_ok") and marker_asnearest_score(m) >= min_score:
                        row["hover_plane_lat"] = m["lat"]
                        row["hover_plane_lon"] = m["lon"]
                        row["hover_partner_lat"] = m["partner_lat"]
                        row["hover_partner_lon"] = m["partner_lon"]
                        row["hover_flight"] = str(m.get("flight", "") or "")
                        break
            rows.append(row)
        rows.sort(key=lambda r: (int(r.get("duration_min", 9999)), str(r.get("call", ""))))
        mr = self._summary_max_rows() if max_rows is None else max(0, min(500, int(max_rows)))
        return rows[:mr]

    def _emit_aircraft_merged(self) -> None:
        if not self._aircraft_enabled():
            try:
                if self._emit_air_fn:
                    self._emit_air_fn([])
                if self._emit_summary_fn:
                    self._emit_summary_fn([])
            except Exception as e:
                try:
                    self.log.write("WARN", f"UDP ASNEAREST emit: {e}")
                except Exception:
                    pass
            return
        ui = self.cfg.get("ui", {})
        try:
            min_score = int(ui.get("asnearest_min_score", 45))
        except (TypeError, ValueError):
            min_score = 45
        min_score = max(0, min(100, min_score))
        flat_map: list[dict[str, Any]] = []
        sk = self._asnearest_selected_dest_key
        if sk and sk in self._asnearest_parsed_by_dest:
            parsed_sel = self._asnearest_parsed_by_dest[sk]
            if self._asnearest_dest_on_map(parsed_sel):
                markers = self._build_aircraft_markers_for_packet(parsed_sel)
                flat_map = [
                    m for m in markers if m.get("link_ok") and marker_asnearest_score(m) >= min_score
                ]
        # Ohne Klick auf ein Rufzeichen in der Liste: keine Flugzeug-Marker (kein „alle auf einmal“)
        summary = self._build_asnearest_summary_rows()
        self.packet_received_flag = True
        try:
            if self._emit_air_fn:
                self._emit_air_fn(flat_map)
        except Exception as e:
            try:
                self.log.write("WARN", f"UDP ASNEAREST emit: {e}")
            except Exception:
                pass
        try:
            if self._emit_summary_fn:
                self._emit_summary_fn(summary)
        except Exception as e:
            try:
                self.log.write("WARN", f"UDP ASNEAREST summary emit: {e}")
            except Exception:
                pass

    def set_asnearest_selected(self, dest_key: str | None) -> None:
        """Gewähltes ASNEAREST-Ziel (dest_key wie CALL|LOC): nur dieses Flugzeug auf der Karte; None = Filter aus."""
        if not self._aircraft_enabled():
            return
        if dest_key and str(dest_key).strip():
            dk = str(dest_key).strip()
            self._asnearest_selected_dest_key = dk if dk in self._asnearest_parsed_by_dest else None
        else:
            self._asnearest_selected_dest_key = None
        self._emit_aircraft_merged()

    def refresh_aircraft_emit(self) -> None:
        """Flugzeug-Marker erneut ausgeben (z. B. nach Karten-HTML neu geladen)."""
        self._emit_aircraft_merged()

    def _emit_merged(self) -> None:
        """Marker aus internem Zustand erzeugen und ausgeben (auch leere Liste)."""
        pairs: list[tuple[str, str, str | None]] = []
        for k in sorted(self._call_to_loc.keys()):
            loc = self._call_to_loc[k]
            label = self._call_label.get(k) or k
            qrg = self._call_to_qrg.get(k)
            pairs.append((label, loc, qrg))
        markers = _build_markers_for_map(pairs)
        self.packet_received_flag = True
        try:
            self._emit_fn(markers)
        except Exception as e:
            try:
                self.log.write("WARN", f"UDP ASWATCH/ASSETPATH emit: {e}")
            except Exception:
                pass

    def _apply_watchlist(self, pairs: list[tuple[str, str]]) -> None:
        """Gesamtliste: Mitgliedschaft = exakt diese Menge; Rest entfernen. QRG bleibt aus ASSETPATH."""
        wl: dict[str, tuple[str, str]] = {}
        for call_raw, loc_raw in pairs:
            k = self._norm_call(call_raw)
            if not k:
                continue
            loc_u = loc_raw.strip().upper()
            if not loc_u:
                continue
            wl[k] = (call_raw.strip(), loc_u)
        for key in list(self._call_to_loc.keys()):
            if key not in wl:
                del self._call_to_loc[key]
                self._call_label.pop(key, None)
                self._call_to_qrg.pop(key, None)
        for k, (label, loc_u) in wl.items():
            self._call_to_loc[k] = loc_u
            self._call_label[k] = label

    def _apply_assetpath(self, call_raw: str, loc_raw: str, qrg_raw: str | None) -> None:
        """Einzelabfrage: Rufzeichen ergänzen oder Locator aktualisieren."""
        k = self._norm_call(call_raw)
        if not k:
            return
        loc_u = loc_raw.strip().upper()
        if not loc_u:
            return
        self._call_to_loc[k] = loc_u
        self._call_label[k] = call_raw.strip()
        qfmt = format_qrg_display(qrg_raw) if qrg_raw else None
        if qfmt:
            self._call_to_qrg[k] = qfmt

    @property
    def is_active(self) -> bool:
        """True wenn Listener gebunden ist und Empfangsthread läuft."""
        return bool(self._enabled and self._running and self._sock is not None)

    def start(
        self,
        enabled: bool,
        port: int = 9872,
        listen_host: str | None = None,
    ) -> None:
        """Listener starten oder mit neuer Konfiguration neu starten."""
        self.stop()
        self._enabled = bool(enabled)
        self._port = max(1, min(65535, int(port)))
        bind_host = normalize_udp_bind_host(listen_host, "0.0.0.0")
        if not self._enabled:
            return
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if hasattr(socket, "SO_REUSEPORT"):
                try:
                    self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
                except OSError:
                    pass
            self._sock.bind((bind_host, self._port))
            self._sock.settimeout(0.5)
            self._running = True
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()
            self._call_to_loc.clear()
            self._call_label.clear()
            self._call_to_qrg.clear()
            self._planes_by_dest.clear()
            self._asnearest_parsed_by_dest.clear()
            self._asnearest_selected_dest_key = None
            self._asnearest_sticky_flight.clear()
            if self._emit_air_fn:
                try:
                    self._emit_air_fn([])
                except Exception:
                    pass
            if self._emit_summary_fn:
                try:
                    self._emit_summary_fn([])
                except Exception:
                    pass
            self.log.write(
                "INFO",
                f"UDP AirScout/KST: lausche auf {bind_host}:{self._port} (ASWATCHLIST/ASSETPATH/ASNEAREST → Karte)",
            )
        except OSError as e:
            self.log.write("ERROR", f"UDP ASWATCHLIST bind fehlgeschlagen: {e}")

    def stop(self) -> None:
        self._running = False
        try:
            if self._sock:
                self._sock.close()
        except Exception:
            pass
        self._sock = None
        if self._thread:
            self._thread.join(timeout=1.0)
        self._thread = None
        if self._enabled:
            self.log.write("INFO", "UDP ASWATCHLIST/ASSETPATH/ASNEAREST gestoppt")
        self._planes_by_dest.clear()
        self._asnearest_parsed_by_dest.clear()
        self._asnearest_selected_dest_key = None
        self._asnearest_sticky_flight.clear()
        if self._emit_air_fn:
            try:
                self._emit_air_fn([])
            except Exception:
                pass
        if self._emit_summary_fn:
            try:
                self._emit_summary_fn([])
            except Exception:
                pass

    def _loop(self) -> None:
        while self._running and self._sock:
            try:
                data, addr = self._sock.recvfrom(65535)
            except socket.timeout:
                continue
            except Exception:
                break
            if not data:
                continue
            try:
                nachricht = data.decode("utf-8", errors="replace").replace("\x00", "").strip()
            except Exception:
                continue
            if nachricht.startswith("ASWATCHLIST:"):
                pairs = parse_aswatchlist(nachricht)
                self._apply_watchlist(pairs)
                self._prune_asnearest_not_on_map()
                self._emit_merged()
                self._emit_aircraft_merged()
            elif nachricht.startswith("ASSETPATH:"):
                one = parse_assetpath(nachricht)
                if not one:
                    continue
                self._apply_assetpath(one[0], one[1], one[2])
                self._prune_asnearest_not_on_map()
                self._emit_merged()
                self._emit_aircraft_merged()
            elif nachricht.upper().startswith("ASNEAREST:"):
                if not self._aircraft_enabled():
                    self._planes_by_dest.clear()
                    self._asnearest_parsed_by_dest.clear()
                    self._asnearest_selected_dest_key = None
                    self._asnearest_sticky_flight.clear()
                    self._emit_aircraft_merged()
                    continue
                parsed = parse_asnearest(nachricht)
                self._append_asnearest_jsonl(nachricht, parsed)
                if parsed is not None:
                    try:
                        c = int(parsed.get("count") or 0)
                    except (TypeError, ValueError):
                        c = 0
                    if c == 0 and not self._asnearest_zero_hint_logged:
                        self._asnearest_zero_hint_logged = True
                        self.log.write(
                            "INFO",
                            "UDP ASNEAREST: Das letzte CSV-Feld ist die Anzahl Flugzeuge für diese "
                            "Gegenstation — bei 0 gibt es keine Marker (AirScout sendet oft viele solcher Kurzpakete).",
                        )
                    elif c > 0 and not self._asnearest_planes_hint_logged:
                        self._asnearest_planes_hint_logged = True
                        self.log.write(
                            "INFO",
                            "UDP ASNEAREST: Paket mit Flugzeug-Kandidaten (count>0) — Karte kann Symbole zeigen.",
                        )
                    key = self._dest_key(parsed["dest_call"], parsed["dest_loc"])
                    if not self._asnearest_dest_on_map(parsed):
                        self._planes_by_dest.pop(key, None)
                        self._asnearest_parsed_by_dest.pop(key, None)
                        self._asnearest_sticky_flight.pop(key, None)
                        if self._asnearest_selected_dest_key == key:
                            self._asnearest_selected_dest_key = None
                    else:
                        self._asnearest_parsed_by_dest[key] = parsed
                        # Volle Marker nur bei Tabellenklick/Karte; Liste nutzt _summary_row_from_parsed
                        self._planes_by_dest[key] = []
                self._emit_aircraft_merged()
