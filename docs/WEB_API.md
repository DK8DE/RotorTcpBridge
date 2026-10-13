# RotorTcpBridge – Web-API (HTTP/JSON, SSE)

Diese Dokumentation beschreibt die HTTP-Schnittstelle des in RotorTcpBridge eingebauten
Webservers (Karte, Kompass, Rotorübersicht). Dieselbe Schnittstelle benutzt die
Browser-Oberfläche – sie kann daher auch von eigenen Programmen (Python, C#, Node-RED,
Home-Assistant, Logbuch-Software, Skripte …) verwendet werden, um den Rotor **auszulesen**
und **zu steuern**.

Quelle im Code: `rotortcpbridge/map_webserver.py` (Routing, Auth, SSE),
`rotortcpbridge/ui/map_window.py` und `rotortcpbridge/ui/web_compass_state.py`
(Aktionen und Zustands-Payload), `rotortcpbridge/rotor_overview_service.py`
(Rotorübersicht), `rotortcpbridge/browser_bus_relay.py` (Web-Serial-Relay).

---

## Inhaltsverzeichnis

1. [Server aktivieren und erreichen](#1-server-aktivieren-und-erreichen)
2. [Authentifizierung](#2-authentifizierung)
3. [Allgemeine Konventionen](#3-allgemeine-konventionen)
4. [Endpunkt-Übersicht](#4-endpunkt-übersicht)
5. [Lesen: GET-Endpunkte](#5-lesen-get-endpunkte)
6. [Echtzeit: Server-Sent Events `/api/events`](#6-echtzeit-server-sent-events-apievents)
7. [Steuern: `POST /api/setaz` (Kartenziel)](#7-steuern-post-apisetaz-kartenziel)
8. [Steuern: `POST /api/ui` (Aktionen)](#8-steuern-post-apiui-aktionen)
9. [Profile wechseln: `/api/profiles`, `POST /api/profile`](#9-profile-wechseln)
10. [Geocoding: `POST /api/geocode`](#10-geocoding-post-apigeocode)
11. [Rotorübersicht: `/api/overview*`](#11-rotorübersicht-apioverview)
12. [Browser-Relay `POST /api/bus` (Web Serial)](#12-browser-relay-post-apibus-web-serial)
13. [Beispiele (curl, PowerShell, Python, JavaScript)](#13-beispiele)
14. [Hinweise, Grenzen, Fehlersuche](#14-hinweise-grenzen-fehlersuche)
15. [Alternative Schnittstellen (TCP-Protokolle)](#15-alternative-schnittstellen)

---

## 1. Server aktivieren und erreichen

Einstellungen → Tab **Karten-Webserver** (Konfigurationsabschnitt `map_webserver`):

| Schlüssel      | Standard   | Bedeutung                                                              |
|----------------|------------|------------------------------------------------------------------------|
| `enabled`      | `false`    | Webserver ein/aus                                                      |
| `listen_host`  | `0.0.0.0`  | Bind-Adresse (`0.0.0.0` = ganzes LAN, `127.0.0.1` = nur lokal)         |
| `listen_port`  | `80`       | TCP-Port                                                               |
| `password`     | `rotor`    | Passwort für HTTP Basic Auth (leer ⇒ Fallback `rotor`)                 |
| `ssl_enabled`  | `false`    | HTTPS mit selbstsigniertem Zertifikat (nötig für Web Serial im LAN)    |

Basis-URL: `http(s)://<host>:<port>/`, z. B. `http://192.168.1.50/` (Port 80 darf entfallen).

- Bei HTTPS wird ein **selbstsigniertes Zertifikat** verwendet → in Clients Zertifikatsprüfung
  abschalten (`curl -k`, `requests.get(..., verify=False)`) oder das Zertifikat
  (`%APPDATA%…/map_webserver_tls/map_webserver.crt`) als vertrauenswürdig hinterlegen.
- Der Server ist ein `ThreadingHTTPServer` (HTTP/1.1, Keep-Alive); jeder Client bekommt
  einen eigenen Thread.

## 2. Authentifizierung

**Alle** Endpunkte (außer `OPTIONS`) verlangen **HTTP Basic Auth**:

- Benutzername: **beliebig** (z. B. `rotor`)
- Passwort: das konfigurierte Passwort (Standard `rotor`)

Header: `Authorization: Basic base64("<user>:<passwort>")`.

Bei fehlender/falscher Anmeldung:

```
HTTP/1.1 401 Unauthorized
WWW-Authenticate: Basic realm="RotorTcpBridge"
{"ok":false,"error":"unauthorized"}
```

> Die Verbindung ist ohne `ssl_enabled` **unverschlüsselt**. Nicht ins Internet exponieren;
> nur im vertrauenswürdigen LAN oder über VPN/Reverse-Proxy mit TLS verwenden.

## 3. Allgemeine Konventionen

- **Antwortformat**: JSON, UTF-8, `Content-Type: application/json; charset=utf-8`.
- **Erfolg/Fehler**: Feld `"ok": true|false`. Bei Fehlern zusätzlich `"error": "<code>"`.
- **POST-Body**: JSON-Objekt mit `Content-Type: application/json`
  (maximal 64 KB; bei `/api/overview/action` 512 KB).
- **Winkel**: Grad, Dezimalpunkt. Azimut 0° = Nord, im Uhrzeigersinn. Elevation 0° = Horizont.
- **Koordinaten**: WGS84, `lat` (−90…90), `lon` (−180…180).
- **CORS**: `Access-Control-Allow-Origin: *`; `OPTIONS` liefert `204` (Preflight ohne Auth).
- **Kein Caching**: `Cache-Control: no-store`.
- **Zeitverhalten**: Steuerbefehle werden in den GUI-Thread der App übergeben und
  **asynchron** ausgeführt. `{"ok":true}` heißt „angenommen“, nicht „Rotor ist angekommen“.
  Das Ergebnis siehst du im Zustand (`/api/compass`, SSE).
- **Aktualisierungsrate**: Der Zustands-Cache der GET-Endpunkte wird alle 33 ms (ca. 30 Hz)
  im GUI-Thread neu aufgebaut; der SSE-Stream sendet mit ca. 30 Hz.
  Die Rotordaten selbst kommen so schnell, wie die App den Rotor abfragt.

Standardfehler:

| HTTP | `error`                 | Bedeutung                                   |
|------|-------------------------|---------------------------------------------|
| 400  | `invalid_json`          | Body kein gültiges JSON                     |
| 400  | `action_required`       | `action` fehlt (`/api/ui`, `/api/overview/action`) |
| 400  | `lat_lon_required` / `lat_lon_range` | Ungültige Koordinaten (`/api/setaz`) |
| 400  | `profile_id_required`   | Profil-ID fehlt                             |
| 401  | `unauthorized`          | Auth fehlt/falsch                           |
| 404  | `not_found`             | Unbekannter Pfad                            |
| 500  | Text der Ausnahme       | Interner Fehler                             |
| 503  | `unavailable` / `bus_unavailable` | Funktion in der App nicht verdrahtet |

## 4. Endpunkt-Übersicht

| Methode | Pfad                    | Zweck                                                        |
|---------|-------------------------|--------------------------------------------------------------|
| GET     | `/`, `/index.html`      | Web-Oberfläche (Karte + Kompass in einer Seite)              |
| GET     | `/map`                  | Nur Karten-Seite                                             |
| GET     | `/compass`              | Nur Kompass-Seite                                            |
| GET     | `/rotoren`              | Rotorübersicht (mehrere Standorte)                           |
| GET     | `/api/state`            | Karten-/Beam-Zustand + Statusleiste (JSON)                   |
| GET     | `/api/compass`          | Kompass-Zustand: Ist/Soll, LEDs, Wind, Temperaturen … (JSON) |
| GET     | `/api/events`           | **SSE-Stream** mit allen Zuständen (Echtzeit)                |
| GET     | `/api/profiles`         | Profilliste                                                  |
| GET     | `/api/overview`         | Zustand der Rotorübersicht (alle Standorte)                  |
| GET     | `/api/overview/node`    | Kompakter Status **dieser** Instanz (für andere Instanzen)   |
| POST    | `/api/setaz`            | Rotor auf Kartenziel (lat/lon) ausrichten                    |
| POST    | `/api/ui`               | Steueraktionen (Azimut, Elevation, Jog, Stop, Referenz …)    |
| POST    | `/api/profile`          | Profil wechseln                                              |
| POST    | `/api/geocode`          | Ortssuche (Nominatim) → Koordinaten                          |
| POST    | `/api/overview/action`  | Aktionen der Rotorübersicht                                  |
| POST    | `/api/bus`              | Web-Serial-Relay (Display-Controller per Browser-USB)        |

---

## 5. Lesen: GET-Endpunkte

Alle liefern `{"ok": true, "data": { … }}`.

### 5.1 `GET /api/compass` – Rotorzustand (wichtigster Endpunkt)

Beispielantwort (gekürzt; `labels` sind lokalisierte Texte der Oberfläche und für eigene
Programme meist uninteressant):

```json
{
  "ok": true,
  "data": {
    "enable_el": true,
    "rig": {"show": true, "text": "14.205 MHz", "oob": false},
    "ref_visible": true,
    "drive_sync": false,
    "profile_locked": false,
    "bus": {"pc_id": 1, "master_id": 1, "cont_id": 2, "az_id": 20, "el_id": 21},
    "antenna_idx": 0,
    "antennas": ["Antenne 1 (0.0°)", "Antenne 2 (0.0°)", "Antenne 3 (0.0°)"],
    "favorites": [{"name": "DL0XYZ", "az": 123.4}],
    "om_available": false,
    "az": {
      "ist": 123.4, "ist_needle": 123.4,
      "soll": 150.0, "soll_needle": 150.0,
      "moving": true, "online": true, "referenced": true, "ref_blink": false,
      "offset_deg": 0.0, "dipole": false, "opening_deg": 30.0,
      "wind_visible": true, "wind_dir": 270.0, "wind_kmh": 12.3, "wind_mode": "to",
      "temp_ambient": 18.5, "temp_motor": 31.0,
      "dgcal": 0.0,
      "pwm": 60, "pwm_min": 20, "pwm_enabled": true
    },
    "el": {
      "ist": 5.0, "soll": 10.0, "max_deg": 90.0,
      "moving": false, "online": true, "referenced": true,
      "dgcal": 0.0
    }
  }
}
```

Felder (Auszug):

| Feld                              | Typ              | Bedeutung                                                                 |
|-----------------------------------|------------------|---------------------------------------------------------------------------|
| `enable_el`                       | bool             | Elevation aktiv (sonst `el.*` leer/`null`)                                |
| `az.ist` / `az.soll`              | float \| null    | **Antennen-Peilung** in °: Rotorwinkel **+ Antennen-Offset**, geglättet   |
| `az.ist_needle` / `soll_needle`   | float \| null    | wie oben, aber ungeglättet für Zeiger                                     |
| `az.offset_deg`                   | float            | Offset der gewählten Antenne (Peilung = Rotorwinkel + Offset)             |
| `az.dipole`                       | bool             | Dipol-Betrieb (Gegenrichtung +180° gleichwertig)                          |
| `az.moving` / `online` / `referenced` | bool         | Bewegt sich / Rotor antwortet / Referenzfahrt erfolgt                     |
| `az.wind_visible`                 | bool             | Windsensor aktiv (nur dann sind Wind-Werte gesetzt)                       |
| `az.wind_dir` / `wind_kmh`        | float \| null    | Windrichtung (°) und -geschwindigkeit (km/h); `wind_mode`: `to` \| `from`  |
| `az.temp_ambient` / `temp_motor`  | float \| null    | Außen- und Motortemperatur in °C                                          |
| `az.dgcal`, `el.dgcal`            | float \| null    | Korrekturwinkel in ° (`0.0` = keine Korrektur; `null` = noch unbekannt)   |
| `az.pwm` / `pwm_min` / `pwm_enabled` (auch `el.*`) | int / int / bool | Motorgeschwindigkeit in %, Minimum, ob einstellbar (Rotor online + Minimum bekannt) |
| `el.ist` / `el.soll` / `max_deg`  | float \| null    | Elevation in °, maximaler Winkel                                          |
| `antenna_idx`                     | int 0–2          | Aktuell gewählte Antenne                                                  |
| `antennas`                        | string[3]        | Antennennamen mit Offset                                                  |
| `favorites`                       | Objekt[]         | Favoriten (Name, Azimut) – **Index = Position in dieser Liste**           |
| `profile_locked`                  | bool             | Profilwechsel gesperrt (Rotor bewegt sich / Laufsynchronisation)          |
| `bus`                             | Objekt           | Bus-IDs (nur für Web-Serial-Relay relevant)                               |
| `rig`                             | Objekt           | Frequenzanzeige des Funkgeräts, falls konfiguriert                        |

Weitere Felder (Heatmaps `bins_cw`/`bins_ccw`, `om_counts`, `dwell_seconds`, `labels` …)
existieren für die Darstellung der Web-Oberfläche.

### 5.2 `GET /api/state` – Karten-/Beam-Zustand

```json
{
  "ok": true,
  "data": {
    "lat": 51.0, "lon": 10.0,
    "azimuth": 123.4,
    "opening": 30.0, "range_km": 100.0,
    "beams": [ { "polygon": [[lat,lon],…], "centerLine": [[lat,lon],…], "stroke": "#…", "fill": "rgba(…)" } ],
    "target_bearing_line": [[lat,lon],[lat,lon]],
    "location_str": "JO50xx", "rig_freq_text": "—",
    "chrome": {
      "status": {"ist": "123.4°", "soll": "150.0°", "moving": true, "online": true,
                 "referenced": true, "temp_motor": "31.0 °C", "temp_ambient": "18.5 °C"},
      "wind": {"visible": true, "dir_deg": 270.0, "kmh": 12.3, "mode": "to"},
      "antennas": {"index": 0, "items": ["…"], "colors": ["#…"], "visible": true},
      "favorites": [{"name":"…","az":1.0,"el":0.0,"label":"…"}],
      "flags": {"offline": false, "hover": true, "satellite": false, "locator": false},
      "drive_sync": false
    }
  }
}
```

- `azimuth` = aktuelle Antennen-Peilung (geglättet).
- `status.*` enthält **bereits formatierte Texte** (`"123.4°"`, `"–"` wenn unbekannt) – für
  Programme besser `/api/compass` mit Zahlenwerten verwenden.
- `beams[].polygon/centerLine` sind Geometrien (Lat/Lon-Paare) für Kartendarstellung.

### 5.3 `GET /api/profiles`

```json
{"ok": true, "active_id": "default", "applied_id": "default",
 "profiles": [{"id": "default", "name": "Default", "active": true},
              {"id": "rotor2",  "name": "Rotor 2", "active": false}]}
```

### 5.4 `GET /api/overview/node` – Status dieser Instanz

Kompakter Status ohne Bus-Last. Wird auch von anderen RotorTcpBridge-Instanzen abgefragt
(Rotorübersicht), eignet sich aber ebenso für externe Programme:

```json
{"ok": true, "data": {
  "profile_id": "default", "profile_name": "Default",
  "lat": 51.0, "lon": 10.0, "locator": "JO50xx",
  "online": true, "moving": false, "referenced": true,
  "az_deg": 123.4,
  "aselect": 1,
  "antennas": [{"enabled": true, "name": "Antenne 1", "offset_deg": 0.0,
                "opening_deg": 30.0, "range_km": 100.0, "color": "#…", "dipole": false}],
  "slave_az": 20,
  "temp_motor_c": 31.0, "temp_ambient_c": 18.5,
  "wind_visible": true, "wind_kmh": 12.3, "wind_dir_deg": 270.0
}}
```

> `az_deg` ist hier der **Rotorwinkel** (ohne Antennen-Offset). `aselect` = gewählte
> Antenne 1–3.

### 5.5 `GET /api/overview`

Zustand der Rotorübersicht (Karte mit mehreren Standorten) – siehe
[Abschnitt 11](#11-rotorübersicht-apioverview).

---

## 6. Echtzeit: Server-Sent Events `/api/events`

`GET /api/events` öffnet einen dauerhaften Stream (`Content-Type: text/event-stream`).
Etwa **30× pro Sekunde** werden nacheinander folgende Events gesendet (Auth wie üblich):

| Event       | Inhalt (`data:` = JSON)                                                    |
|-------------|----------------------------------------------------------------------------|
| `beam`      | wie `data` von `/api/state` (inkl. `chrome`)                               |
| `compass`   | wie `data` von `/api/compass`                                              |
| `overview`  | wie `data` von `/api/overview`                                             |
| `aswatch`   | `{"items":[…], "total": n}` – AirScout/KST-Daten (falls aktiv)             |
| `aircraft`  | `{"items":[…]}` – Flugzeugdaten (falls aktiv)                              |
| `asnearest` | `{"items":[…]}` – nächste Flugzeuge (falls aktiv)                          |

Format eines Events:

```
event: compass
data: {"enable_el":true,"az":{"ist":123.4, …}, …}

```

Hinweise:

- Der Browser-`EventSource` kann keine eigenen Header setzen. Bei Basic Auth entweder
  `http://user:pass@host/api/events` (Browser-abhängig) oder – besser – ein
  `fetch()`-Stream bzw. ein Nicht-Browser-Client verwenden (siehe Beispiele).
- Der SSE-Stream belegt einen Server-Thread je Client und ist daten-intensiv
  (~30 Events/s). Für einfache Auswertungen reicht **Polling von `/api/compass`** mit
  1–5 Hz.
- Verbindung bricht ab → einfach neu verbinden.

---

## 7. Steuern: `POST /api/setaz` (Kartenziel)

Richtet den Rotor auf einen **geografischen Punkt** aus (wie ein Klick in die Karte).
Die Peilung wird aus dem Stationsstandort der App berechnet.

Request:

```json
{"lat": 52.52, "lon": 13.405, "asnearest_dest": null}
```

| Feld             | Pflicht | Bedeutung                                                     |
|------------------|---------|---------------------------------------------------------------|
| `lat`, `lon`     | ja      | Zielkoordinate (WGS84)                                        |
| `asnearest_dest` | nein    | Optionaler Ziel-Bezeichner (AirScout-„nearest“-Kontext)       |

Antwort: `{"ok": true}` (angenommen). Fehler: `400 lat_lon_required` / `lat_lon_range`.

---

## 8. Steuern: `POST /api/ui` (Aktionen)

Allgemeine Form:

```json
{"action": "<name>", ...parameter}
```

Antwort: `{"ok": true}` – die Aktion wird im GUI-Thread ausgeführt. Unbekannte oder
wirkungslose Aktionen werden **nicht** als Fehler gemeldet (immer `ok:true`); Ausnahme
ist `locator` (siehe unten). Aktionsnamen sind **Kleinbuchstaben**.

### 8.1 Rotor steuern

| `action`   | Parameter                                  | Wirkung                                                                                   |
|------------|--------------------------------------------|-------------------------------------------------------------------------------------------|
| `set_az`   | `deg` (float), `exact` (bool, optional)    | Azimut-Ziel. `deg` ist die **Antennen-Peilung** (Offset und ggf. Dipol werden eingerechnet). `exact:true` nimmt `deg` ohne Wrap (z. B. Winkel > 360° bei Rotoren mit großem Bereich). |
| `set_el`   | `deg` (float)                              | Elevation-Ziel (auf `max_deg` begrenzt; ohne aktive EL ignoriert)                          |
| `stop_az`  | –                                          | AZ sofort anhalten, aktuelle Position halten                                              |
| `stop_el`  | –                                          | EL anhalten                                                                               |
| `jog_az`   | `dir`: `0` Stopp, `1` Richtung 0° (links/CCW), `2` Richtung Achsmaximum (rechts/CW) | Manuelles Fahren. **Taste-halten-Prinzip:** alle ~80 ms erneut senden; bleibt das Wiederholen aus, stoppt die Firmware nach ca. 500 ms selbst. |
| `jog_el`   | `dir`: `0`, `1` (abwärts), `2` (aufwärts)  | wie `jog_az`                                                                              |
| `set_pwm`  | `axis`: `"az"`\|`"el"`, `pct` (0–100)      | Motorgeschwindigkeit in % (nach unten durch `pwm_min` der Achse begrenzt)                 |
| `ref_az`   | –                                          | Referenzfahrt (Homing) Azimut                                                             |
| `ref_el`   | –                                          | Referenzfahrt Elevation                                                                   |
| `az_scan`  | `a`, `b` (float)                           | Fährt zwischen zwei Azimutwinkeln `a` und `b` hin und her (Scan). Beenden mit `stop_az`, `set_az` oder `jog_az`. |
| `place_pick` | `lat`, `lon`                             | wie `/api/setaz`                                                                          |

### 8.2 Korrekturwinkel (Kalibrierung, „DGCAL“)

| `action`   | Parameter                          | Wirkung                                                                              |
|------------|------------------------------------|--------------------------------------------------------------------------------------|
| `dgcal_az` | `value` (float, wahre Peilung)     | Berechnet aus aktuellem Ist und gegebener **wahrer Peilung** den neuen Korrekturwinkel und speichert ihn im Rotor (`SETDGCAL`). |
| `dgcal_az` | `clear: true`                      | Korrekturwinkel auf `0.0` setzen                                                     |
| `dgcal_el` | `value` bzw. `clear: true`         | wie oben für Elevation                                                               |

Den gespeicherten Wert liefert `/api/compass` in `az.dgcal` / `el.dgcal`.

### 8.3 Antennen, Favoriten, Ort

| `action`      | Parameter                  | Wirkung                                                                              |
|---------------|----------------------------|--------------------------------------------------------------------------------------|
| `antenna`     | `index` (0–2)              | Antenne wählen                                                                       |
| `fav_select`  | `index` (int)              | Favorit anfahren. `index` = Position in `favorites` aus `/api/compass` bzw. `chrome.favorites` (alphabetisch, Zahlen zuerst) |
| `fav_save`    | `name` (string, max. 15)   | Aktuelle Position als Favorit speichern                                              |
| `fav_delete`  | `index` (int)              | Favorit löschen                                                                      |
| `locator`     | `locator` (Maidenhead)     | Rotor auf Locator ausrichten. Bei ungültigem Locator **`400`**: `{"ok":false,"error":"locator_invalid","title":…,"message":…}` |
| `place_search`| `query` (string)           | Ort suchen und ersten Treffer als Ziel setzen (nur wenn „Offline“ aus ist)           |

### 8.4 Anzeige-Optionen (wirken auf die gemeinsame Konfiguration/Karte)

| `action`           | Parameter                                       | Wirkung                                        |
|--------------------|-------------------------------------------------|------------------------------------------------|
| `toggle_offline`   | `value` (bool)                                  | Offline-Karte                                  |
| `toggle_hover`     | `value` (bool)                                  | Hover-Vorschau                                 |
| `toggle_satellite` | `value` (bool)                                  | Satellitenansicht                              |
| `toggle_locator`   | `value` (bool)                                  | Locator-Gitter                                 |
| `heatmap_az_modes` | `modes`: Liste aus `strom`, `om_radar`, `dwell` (max. 2) | Heatmap-Modi Azimut             |
| `heatmap_el`       | `mode`: `strom` \| `off`                        | Heatmap Elevation                              |
| `dwell_reset`      | –                                               | Verweilzeit-Heatmap zurücksetzen               |
| `compass_open` / `compass_close` | –                                 | Meldet der App, dass ein Kompass-Client aktiv ist → Rotor wird häufiger abgefragt (empfohlen, wenn ein externes Programm Ist-Werte mit höherer Rate braucht). |
| `elevation`        | –                                               | Öffnet das Höhenprofil **am Desktop** (nur GUI) |

---

## 9. Profile wechseln

- `GET /api/profiles` – Liste (siehe 5.3). `active_id` ist das aktive Profil,
  `applied_id` das tatsächlich fertig angewendete.
- `POST /api/profile` mit `{"id": "<profil-id>"}` (alternativ `profile_id` oder `profile`).

Antworten:

```json
{"ok": true, "active_id": "rotor2"}                         // Wechsel gestartet
{"ok": true, "active_id": "default", "unchanged": true}      // bereits aktiv
{"ok": false, "error": "unknown_profile"}                    // 400
{"ok": false, "error": "rotor_moving", "message": "…"}       // 400: Rotor fährt
```

**Wichtig:** Solange ein Rotor fährt oder die Laufsynchronisation aktiv ist
(`profile_locked: true` in `/api/compass`), werden Profilwechsel mit `rotor_moving`
abgelehnt (sonst würde der bewegte Rotor nicht mehr abgefragt). Der Wechsel selbst
läuft asynchron – nach dem Request `/api/profiles` pollen, bis `applied_id == id`.

---

## 10. Geocoding: `POST /api/geocode`

```json
{"query": "Berlin Brandenburger Tor"}
```

Antwort (bis zu 8 Treffer, benötigt Internet auf dem PC der Bridge):

```json
{"ok": true, "results": [{"lat": 52.5163, "lon": 13.3777, "display_name": "Brandenburger Tor, …"}]}
```

Fehler: `400 query_required`, `500 <Text>`.

---

## 11. Rotorübersicht (`/api/overview*`)

Die Rotorübersicht zeigt mehrere RotorTcpBridge-Instanzen (Standorte) auf einer Karte;
jeder Standort wird über dessen `GET /api/overview/node` abgefragt (Basic Auth,
Benutzer `rotor`, Standort-Passwort).

### `GET /api/overview`

```json
{"ok": true, "data": {
  "revision": 3, "dark_mode": true,
  "center_lat": 50.0, "center_lon": 10.0, "zoom": 5,
  "show_active_antenna_only": false,
  "status_sites": 2, "status_online": 1, "status_text": "…",
  "sites": [{
    "id": "…", "name": "…", "profile_name": "…",
    "lat": 51.0, "lon": 10.0,
    "online": true, "az_text": "123.4°", "ref_text": "…",
    "marker_color": "#2e7d32", "aselect": 1,
    "temp_motor_c": 31.0, "temp_ambient_c": 18.5,
    "wind_visible": true, "wind_kmh": 12.3, "wind_dir_deg": 270.0,
    "beams": [ … ]
  }],
  "config": { … }
}}
```

### `POST /api/overview/action`

Body `{"action": "<name>", …}`. Ergebnis `{"ok":true,…}` bzw. `{"ok":false,"error":…}` (400).

| `action`                  | Parameter                       | Antwort / Wirkung                                           |
|---------------------------|---------------------------------|-------------------------------------------------------------|
| `reload`                  | –                               | Monitor neu starten; liefert `data` (wie `/api/overview`)   |
| `save_view`               | `lat`, `lon`, `zoom`            | Kartenausschnitt speichern                                  |
| `set_active_antenna_only` | `value` (bool)                  | Nur aktive Antenne zeichnen; liefert `data`                 |
| `locator`                 | `locator`                       | `{"ok":true,"lat":…,"lon":…}` oder `locator_invalid`        |
| `default_site`            | `port` (optional)               | Liefert eine Standard-Standortvorlage `site`                |
| `save_config`             | `config` (Standort-Konfiguration)| Speichert die Standortliste; liefert `config` und `data`   |

Unbekannte Aktion: `{"ok": false, "error": "unknown_action"}`.

---

## 12. Browser-Relay `POST /api/bus` (Web Serial)

Intern verwendet die Web-Oberfläche diesen Endpunkt, um einen **Display-Controller per
USB am Browser-PC** (Web Serial API) mit dem Rotor-Bus der Bridge zu verbinden
(Link-Modus „WEB“). Für eigene Programme normalerweise **nicht relevant**.

Request (zyklisch, z. B. alle 50–100 ms):

```json
{"tx": ["<RS485-Textzeile vom Display>", "…"], "since": 123}
```

oder zum Trennen `{"disconnect": true}`.

Antwort:

```json
{"ok": true, "active": true, "seq": 130, "rx": [{"seq": 124, "line": "<Zeile für den Display>"}]}
```

- `tx`: bis 64 vom Display gelesene Zeilen pro Request.
- `since`: höchste bereits verarbeitete `seq`; `rx` enthält alle neueren Zeilen, die in
  den Display-USB geschrieben werden müssen.
- Ohne Aktivität (~12 s) trennt der Server das Relay automatisch.
- Fehler: `{"ok":false,"error":"desktop_remote_usb_active"}` – der Display hängt bereits
  per USB am Desktop-PC der Bridge (Link-Modus „PC-USB“).

---

## 13. Beispiele

Im Folgenden: Host `192.168.1.50`, Port `80`, Passwort `rotor`.

### 13.1 curl

```bash
# Zustand lesen
curl -u x:rotor http://192.168.1.50/api/compass

# Azimut auf 270° setzen
curl -u x:rotor -H "Content-Type: application/json" \
     -d '{"action":"set_az","deg":270}' http://192.168.1.50/api/ui

# Stopp
curl -u x:rotor -H "Content-Type: application/json" \
     -d '{"action":"stop_az"}' http://192.168.1.50/api/ui

# Auf Koordinate ausrichten
curl -u x:rotor -H "Content-Type: application/json" \
     -d '{"lat":52.52,"lon":13.405}' http://192.168.1.50/api/setaz

# Profil wechseln
curl -u x:rotor -H "Content-Type: application/json" \
     -d '{"id":"rotor2"}' http://192.168.1.50/api/profile

# HTTPS (selbstsigniert)
curl -k -u x:rotor https://192.168.1.50/api/compass
```

### 13.2 PowerShell

```powershell
$cred = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes("x:rotor"))
$h = @{ Authorization = "Basic $cred" }

# Lesen
$s = Invoke-RestMethod -Uri http://192.168.1.50/api/compass -Headers $h
"{0:N1}° (Soll {1:N1}°)" -f $s.data.az.ist, $s.data.az.soll

# Steuern
Invoke-RestMethod -Uri http://192.168.1.50/api/ui -Method Post -Headers $h `
  -ContentType "application/json" -Body '{"action":"set_az","deg":90}'
```

### 13.3 Python (`requests`)

```python
import time
import requests

BASE = "http://192.168.1.50"
AUTH = ("x", "rotor")           # Benutzername beliebig, Passwort aus den Einstellungen
S = requests.Session()
S.auth = AUTH
# S.verify = False             # nur bei HTTPS mit selbstsigniertem Zertifikat


def state() -> dict:
    r = S.get(f"{BASE}/api/compass", timeout=3)
    r.raise_for_status()
    return r.json()["data"]


def ui(action: str, **params) -> dict:
    r = S.post(f"{BASE}/api/ui", json={"action": action, **params}, timeout=3)
    return r.json()


def goto_az(deg: float, tol: float = 1.0, timeout: float = 120.0) -> bool:
    ui("set_az", deg=deg)
    t0 = time.time()
    while time.time() - t0 < timeout:
        az = state()["az"]
        if az["ist"] is not None and abs(((az["ist"] - deg + 180) % 360) - 180) <= tol \
                and not az["moving"]:
            return True
        time.sleep(0.5)
    return False


if __name__ == "__main__":
    print("Ist:", state()["az"]["ist"])
    print("Angekommen:", goto_az(180.0))
```

### 13.4 Python – SSE-Stream lesen

```python
import json
import requests

with requests.get("http://192.168.1.50/api/events", auth=("x", "rotor"),
                  stream=True, timeout=(5, None)) as r:
    event = None
    for raw in r.iter_lines(decode_unicode=True):
        if raw is None:
            continue
        if raw.startswith("event:"):
            event = raw[6:].strip()
        elif raw.startswith("data:") and event == "compass":
            d = json.loads(raw[5:])
            print("AZ:", d["az"]["ist"], "moving:", d["az"]["moving"])
```

### 13.5 JavaScript (Browser/Node 18+, `fetch`)

```javascript
const BASE = "http://192.168.1.50";
const AUTH = "Basic " + btoa("x:rotor");       // Node: Buffer.from("x:rotor").toString("base64")

async function ui(action, params = {}) {
  const r = await fetch(`${BASE}/api/ui`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: AUTH },
    body: JSON.stringify({ action, ...params }),
  });
  return r.json();
}

async function compass() {
  const r = await fetch(`${BASE}/api/compass`, { headers: { Authorization: AUTH } });
  return (await r.json()).data;
}

await ui("set_az", { deg: 45 });
console.log((await compass()).az.ist);
```

### 13.6 Jog („Taste halten“)

```python
import time, requests
S = requests.Session(); S.auth = ("x", "rotor")
URL = "http://192.168.1.50/api/ui"

t_end = time.time() + 3.0                      # 3 s nach rechts fahren
while time.time() < t_end:
    S.post(URL, json={"action": "jog_az", "dir": 2})
    time.sleep(0.08)                           # alle ~80 ms wiederholen
S.post(URL, json={"action": "jog_az", "dir": 0})   # Stopp
```

---

## 14. Hinweise, Grenzen, Fehlersuche

- **Server läuft nicht?** Einstellungen → Karten-Webserver aktivieren. Bei belegtem Port
  (z. B. 80 durch IIS/Skype) einen anderen Port wählen, z. B. `8080`. Die App meldet den
  Bind-Fehler und deaktiviert den Server wieder.
- **Windows-Firewall:** Für Zugriff aus dem LAN muss der Port freigegeben sein.
- **Leere HTML-Seiten** („Map unavailable“) direkt nach dem Start: Die HTML-Caches
  werden vom GUI-Timer gefüllt – kurz warten. JSON-Endpunkte sind davon nicht betroffen.
- **`null`-Werte:** Solange der Rotor noch nicht geantwortet hat (`online:false`),
  sind Ist-/Soll-Werte `null` bzw. `"–"`.
- **Referenz:** Ohne Referenzfahrt (`referenced:false`) sind Soll-Werte teils nicht
  aussagekräftig. Rotoren mit Absolutgeber blenden Homing aus (`ref_visible:false`).
- **Asynchron:** Nach `set_az` ist `ok:true` nur die Annahme. Für „angekommen“ auf
  `az.moving == false` und `|ist − soll|` prüfen.
- **Parallele Clients:** Mehrere Clients dürfen gleichzeitig lesen und steuern; es gibt
  keine Sperre. Letzter Befehl gewinnt.
- **Peilung vs. Rotorwinkel:** `/api/compass` und `set_az` arbeiten mit der
  **Antennen-Peilung** (Rotorwinkel + Offset der gewählten Antenne);
  `/api/overview/node` (`az_deg`) liefert den **Rotorwinkel**.
- **Profilwechsel:** Während der Rotor fährt gesperrt (`rotor_moving`).
- **Sicherheit:** Basic Auth ohne TLS ist abhörbar. Standardpasswort `rotor` unbedingt
  ändern, nicht ohne VPN/TLS ins Internet stellen. Die API erlaubt, den Rotor physisch zu
  bewegen.
- **Last:** Für Dauerabfragen reichen 1–5 Hz (`/api/compass`). SSE nur, wenn wirklich
  Echtzeit nötig ist.

---

## 15. Alternative Schnittstellen

Neben der Web-API kann RotorTcpBridge klassische Rotor-Protokolle als TCP-Server anbieten
(jeweils unter Einstellungen aktivieren; Standard: aus, nur `127.0.0.1`):

| Protokoll                   | Standardport | Typische Software                                    |
|-----------------------------|--------------|------------------------------------------------------|
| Hamlib **rotctld**          | 4533         | gpredict, SatNOGS, WaveLogGate, Hamlib-Clients       |
| **GS-232B**                 | 4003         | Logger/Tracker mit Yaesu-GS-232-Unterstützung        |
| **EasyComm II**             | 4535         | Satelliten-Tracker                                   |
| **Hy-Gain DCU-1**           | 4004         | Software mit DCU-1-Unterstützung                     |

Für reine Winkel-Ansteuerung durch Logger/Tracker sind diese Protokolle meist die
einfachere Wahl; die Web-API bietet zusätzlich Zustandsdaten (Wind, Temperaturen, LEDs,
Profile, Favoriten, Antennen, Jog, Referenzfahrt, Kalibrierung).
