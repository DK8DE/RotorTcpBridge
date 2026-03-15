#!/usr/bin/env python3
"""
Lädt OSM-Kartenkacheln für die Offline-Karte der RotorTcpBridge.
Speichert im Standard-Format z/x/y.png in rotortcpbridge/KartenLight.
"""
import os
import time
import urllib.request
import urllib.error
from pathlib import Path

ZIEL_ORDNER = Path(__file__).parent / "rotortcpbridge" / "KartenLight"
ZOOMSTUFE = 4
PAUSE_ZWISCHEN_DOWNLOADS = 0.5
USER_AGENT = "RotorTcpBridge/1.0 (Offline-Karte)"


def baue_tile_url(z, x, y):
    return f"https://tile.openstreetmap.org/{z}/{x}/{y}.png"


def baue_dateipfad(basisordner, z, x, y):
    return basisordner / str(z) / str(x) / f"{y}.png"


def lade_datei_herunter(url, dateipfad, user_agent):
    request = urllib.request.Request(url, headers={"User-Agent": user_agent})
    try:
        with urllib.request.urlopen(request, timeout=30) as antwort:
            dateipfad.parent.mkdir(parents=True, exist_ok=True)
            dateipfad.write_bytes(antwort.read())
        return True
    except (urllib.error.HTTPError, urllib.error.URLError) as e:
        print(f"Fehler bei {url}: {e}")
        return False


def schreibe_attribution(basisordner):
    (basisordner / "ATTRIBUTION.txt").write_text(
        "© OpenStreetMap-Mitwirkende\n"
        "https://tile.openstreetmap.org/{z}/{x}/{y}.png\n",
        encoding="utf-8",
    )


def lade_weltkarte():
    basis = Path(ZIEL_ORDNER)
    n = 2 ** ZOOMSTUFE
    gesamt = n * n
    zaehler = erfolge = fehlschlaege = 0

    print("Download Offline-Karte für RotorTcpBridge")
    print(f"Zoomstufe: {ZOOMSTUFE}, Tiles: {gesamt}")
    print(f"Ziel: {basis.resolve()}\n")

    for x in range(n):
        for y in range(n):
            zaehler += 1
            pfad = baue_dateipfad(basis, ZOOMSTUFE, x, y)
            url = baue_tile_url(ZOOMSTUFE, x, y)
            print(f"[{zaehler:03d}/{gesamt}] z={ZOOMSTUFE} x={x} y={y}", end=" ")
            if pfad.exists():
                print("(bereits vorhanden)")
                erfolge += 1
            elif lade_datei_herunter(url, pfad, USER_AGENT):
                print("OK")
                erfolge += 1
            else:
                print("FEHLER")
                fehlschlaege += 1
            time.sleep(PAUSE_ZWISCHEN_DOWNLOADS)

    schreibe_attribution(basis)
    print(f"\nFertig: {erfolge} OK, {fehlschlaege} Fehler")


if __name__ == "__main__":
    lade_weltkarte()
