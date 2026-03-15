"""Lädt Leaflet und Maidenhead in rotortcpbridge/static (für Offline-Karte ohne Netzwerk)."""
import urllib.request
from pathlib import Path

STATIC = Path(__file__).parent / "rotortcpbridge" / "static"
URLS = [
    ("https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.min.js", "leaflet.min.js"),
    ("https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.css", "leaflet.css"),
    ("https://cdn.jsdelivr.net/npm/leaflet.maidenhead@1.1.0/src/maidenhead.js", "maidenhead.js"),
]

if __name__ == "__main__":
    STATIC.mkdir(parents=True, exist_ok=True)
    for url, name in URLS:
        path = STATIC / name
        try:
            urllib.request.urlretrieve(url, path)
            print(f"OK {name}")
        except Exception as e:
            print(f"FEHLER {name}: {e}")
