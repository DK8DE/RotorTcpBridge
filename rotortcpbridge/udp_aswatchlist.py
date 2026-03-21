"""UDP-Listener für AirScout/KST ASWATCHLIST- und ASSETPATH-Broadcasts.

Empfängt z. B. auf Port 9872 Nachrichten der Form::

    ASWATCHLIST: \"PY\" \"AS\" 1440000,OWNCALL,JO62RM,OK1ABC,JO70AA,...
    ASSETPATH:   \"PY\" \"AS\" 1440000,OWNCALL,JO62RM,OK1ABC,JO70AA

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

Bind mit SO_REUSEADDR und (falls vorhanden) SO_REUSEPORT, damit der Port
nach Möglichkeit nicht exklusiv blockiert wird (abhängig vom Betriebssystem).
"""

from __future__ import annotations

import socket
import threading
from collections import defaultdict
from typing import Any, Callable

from .geo_utils import destination_point, maidenhead_to_lat_lon


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


def _split_udp_csv_payload(nachricht: str, prefix: str) -> list[str] | None:
    """CSV-Felder nach dem zweiten Anführungszeichenblock; ``None`` bei Parse-Fehler."""
    if not nachricht.startswith(prefix):
        return None
    inhalt = nachricht[len(prefix) :].strip()
    teile = inhalt.split('"')
    if len(teile) < 5:
        return None
    datenblock = teile[-1].strip()
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

    Format wie AirScout::
        ASSETPATH: \"PY\" \"AS\" 1440000,OWNCALL,OWNLOC,TARGET,TARGETLOC

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
            item: dict[str, Any] = {"call": call, "lat": lat2, "lon": lon2}
            if qrg:
                item["qrg"] = qrg
            out.append(item)
    return out


EmitFn = Callable[[list[dict[str, Any]]], None]


class UdpAswatchlistListener:
    """Hört auf UDP, wertet ASWATCHLIST + ASSETPATH aus und ruft emit_fn mit Marker-Listen auf."""

    def __init__(self, log, cfg: dict | None, emit_fn: EmitFn):
        self.log = log
        self.cfg = cfg or {}
        self._emit_fn = emit_fn
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

    @staticmethod
    def _norm_call(call: str) -> str:
        return call.strip().upper()

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

    def start(self, enabled: bool, port: int = 9872) -> None:
        """Listener starten oder mit neuer Konfiguration neu starten."""
        self.stop()
        self._enabled = bool(enabled)
        self._port = max(1, min(65535, int(port)))
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
            self._sock.bind(("", self._port))
            self._sock.settimeout(0.5)
            self._running = True
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()
            self._call_to_loc.clear()
            self._call_label.clear()
            self._call_to_qrg.clear()
            self.log.write(
                "INFO",
                f"UDP AirScout/KST: lausche auf 0.0.0.0:{self._port} (ASWATCHLIST + ASSETPATH → Karte)",
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
            self.log.write("INFO", "UDP ASWATCHLIST/ASSETPATH gestoppt")

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
                nachricht = data.decode("utf-8", errors="replace").strip()
            except Exception:
                continue
            if nachricht.startswith("ASWATCHLIST:"):
                pairs = parse_aswatchlist(nachricht)
                self._apply_watchlist(pairs)
                self._emit_merged()
            elif nachricht.startswith("ASSETPATH:"):
                one = parse_assetpath(nachricht)
                if not one:
                    continue
                self._apply_assetpath(one[0], one[1], one[2])
                self._emit_merged()
