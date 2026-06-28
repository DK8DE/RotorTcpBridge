"""Geocoding für Ortsnamen (OpenStreetMap Nominatim)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.parse import quote
from urllib.request import Request, urlopen

from .version import APP_NAME, APP_VERSION

_NOMINATIM_SEARCH = "https://nominatim.openstreetmap.org/search"


@dataclass(frozen=True)
class GeocodeResult:
    lat: float
    lon: float
    display_name: str


def geocode_places(query: str, *, limit: int = 5, timeout: float = 12.0) -> list[GeocodeResult]:
    """Ortsname → Koordinaten (Nominatim). Leere Liste bei keinem Treffer."""
    q = (query or "").strip()
    if not q:
        return []
    url = (
        f"{_NOMINATIM_SEARCH}?q={quote(q)}&format=json&limit={max(1, min(limit, 10))}"
        "&addressdetails=0"
    )
    req = Request(url, headers={"User-Agent": f"{APP_NAME}/{APP_VERSION}"})
    with urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode())
    if not isinstance(data, list):
        return []
    out: list[GeocodeResult] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        try:
            lat = float(item["lat"])
            lon = float(item["lon"])
            name = str(item.get("display_name") or q).strip()
        except (KeyError, TypeError, ValueError):
            continue
        out.append(GeocodeResult(lat=lat, lon=lon, display_name=name))
    return out
