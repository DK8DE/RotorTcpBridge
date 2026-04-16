"""Geografische Hilfsfunktionen für Karten und Antennen-Beam."""

from __future__ import annotations

import math
from datetime import datetime, timezone

_EARTH_RADIUS_KM = 6371.0

# Standard-Standort in app_config (Einstellungen) — Abgleich für Locator-Logik
_DEFAULT_LOCATION_LAT = 49.502651
_DEFAULT_LOCATION_LON = 8.375019


def effective_station_lat_lon(ui: dict) -> tuple[float, float]:
    """Effektiven Standort für Karte/Beams ermitteln.

    - Sind **Breite/Länge** von den Installations-Defaults abweichend gesetzt, zählen die
      Koordinaten (expliziter Standort, ggf. nach „Koordinaten übernehmen“ verfeinert).
    - Sind die Koordinaten noch **Default** und ein **Maidenhead-Locator** ist gesetzt,
      wird die **Zellenmitte** des Locators verwendet (Karte: Antenne in Locator-Mitte).
    - Ohne gültigen Locator: gespeicherte Koordinaten.
    """
    try:
        lat = float(ui.get("location_lat", _DEFAULT_LOCATION_LAT))
    except (TypeError, ValueError):
        lat = _DEFAULT_LOCATION_LAT
    try:
        lon = float(ui.get("location_lon", _DEFAULT_LOCATION_LON))
    except (TypeError, ValueError):
        lon = _DEFAULT_LOCATION_LON
    loc = str(ui.get("location_locator", "") or "").strip()
    if not loc:
        return lat, lon
    ll = maidenhead_to_lat_lon(loc)
    if ll is None:
        return lat, lon
    clat, clon = ll
    if abs(lat - _DEFAULT_LOCATION_LAT) > 1e-5 or abs(lon - _DEFAULT_LOCATION_LON) > 1e-5:
        return lat, lon
    return clat, clon


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Großkreis-Distanz zwischen zwei Punkten in Kilometern."""
    lat1_r = math.radians(lat1)
    lat2_r = math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1_r) * math.cos(lat2_r) * math.sin(dlon / 2) ** 2
    c = 2 * math.asin(min(1.0, math.sqrt(max(0.0, a))))
    return _EARTH_RADIUS_KM * c


def great_circle_interpolate(
    lat1: float, lon1: float, lat2: float, lon2: float, t: float
) -> tuple[float, float]:
    """Großkreis zwischen zwei Punkten: ``t=0`` Start, ``t=1`` Ziel."""
    t = min(1.0, max(0.0, t))
    lat1_r = math.radians(lat1)
    lon1_r = math.radians(lon1)
    lat2_r = math.radians(lat2)
    lon2_r = math.radians(lon2)
    x1 = math.cos(lat1_r) * math.cos(lon1_r)
    y1 = math.cos(lat1_r) * math.sin(lon1_r)
    z1 = math.sin(lat1_r)
    x2 = math.cos(lat2_r) * math.cos(lon2_r)
    y2 = math.cos(lat2_r) * math.sin(lon2_r)
    z2 = math.sin(lat2_r)
    dot = x1 * x2 + y1 * y2 + z1 * z2
    dot = max(-1.0, min(1.0, dot))
    ang = math.acos(dot)
    if ang < 1e-9:
        return lat1, lon1
    a = math.sin((1.0 - t) * ang) / math.sin(ang)
    b = math.sin(t * ang) / math.sin(ang)
    x = a * x1 + b * x2
    y = a * y1 + b * y2
    z = a * z1 + b * z2
    lat_r = math.atan2(z, math.sqrt(x * x + y * y))
    lon_r = math.atan2(y, x)
    return (math.degrees(lat_r), math.degrees(lon_r))


def point_along_path_km(
    lat_own: float,
    lon_own: float,
    lat_dest: float,
    lon_dest: float,
    distance_from_own_km: float,
) -> tuple[float, float]:
    """Punkt auf der Großkreislinie eigenes QTH → Ziel, ``distance_from_own_km`` vom Start (geklappt)."""
    d_tot = haversine_km(lat_own, lon_own, lat_dest, lon_dest)
    if d_tot < 0.01:
        return lat_own, lon_own
    t = min(1.0, max(0.0, distance_from_own_km / d_tot))
    return great_circle_interpolate(lat_own, lon_own, lat_dest, lon_dest, t)


def reflection_path_fraction_and_midpoint_factor(
    dist_from_own_km: float,
    lat_own: float,
    lon_own: float,
    lat_dest: float,
    lon_dest: float,
) -> tuple[float, float]:
    """
    Aircraft-Scatter-Heuristik auf der Großkreislinie QTH → Gegenstation:

    - ``t`` = Anteil der Streckenlänge (0 = bei dir, 1 = beim Partner).
    - ``g`` = ``4 * t * (1 - t)`` in [0, 1], Maximum **1** bei **Mitte** (t = 0,5);
      nahe den Enden (Flug direkt über QTH oder über dem Ziel) → **g ≈ 0**.

    AirScouts ``distance_km`` wird als Entfernung vom eigenen Standort entlang des Pfads interpretiert
    (wie ``point_along_path_km``).
    """
    d_tot = haversine_km(lat_own, lon_own, lat_dest, lon_dest)
    if d_tot < 0.01:
        return (0.5, 1.0)
    t = min(1.0, max(0.0, float(dist_from_own_km) / d_tot))
    g = 4.0 * t * (1.0 - t)
    return (t, g)


def offset_perpendicular_toward_dest(
    lat: float, lon: float, lat_dest: float, lon_dest: float, offset_km: float
) -> tuple[float, float]:
    """Senkrecht zur Verbindung (lat,lon)→Ziel um ``offset_km`` verschieben (für mehrere Marker)."""
    brg = bearing_deg(lat, lon, lat_dest, lon_dest)
    return destination_point(lat, lon, (brg + 90.0) % 360.0, offset_km)


def grayline_points(n_points: int = 360) -> list[tuple[float, float]]:
    """Punkte des Solar-Terminators (Grayline) für die aktuelle UTC-Zeit.

    Die Grayline trennt Tag und Nacht. Parametrisierung entlang des Großkreises,
    damit die Kurve auf der Karte glatt erscheint (keine Ecken).
    """
    now = datetime.now(timezone.utc)
    day = now.timetuple().tm_yday
    utc_hours = now.hour + now.minute / 60.0 + now.second / 3600.0

    # Subsolar-Punkt: wo die Sonne senkrecht steht
    sun_lon = 180.0 - 15.0 * utc_hours
    sun_lon = ((sun_lon + 180.0) % 360.0) - 180.0
    sun_lat = 23.44 * math.sin(2.0 * math.pi * (day - 81) / 365.0)

    sun_lat_r = math.radians(sun_lat)
    sun_lon_r = math.radians(sun_lon)

    # Einheitsvektor zur Sonne
    sx = math.cos(sun_lat_r) * math.cos(sun_lon_r)
    sy = math.cos(sun_lat_r) * math.sin(sun_lon_r)
    sz = math.sin(sun_lat_r)

    # Zwei orthogonale Vektoren in der Terminator-Ebene (senkrecht zur Sonne)
    ux = -math.sin(sun_lon_r)
    uy = math.cos(sun_lon_r)
    uz = 0.0
    vx = sy * uz - sz * uy
    vy = sz * ux - sx * uz
    vz = sx * uy - sy * ux

    result: list[tuple[float, float]] = []
    for i in range(n_points + 1):
        t = 2.0 * math.pi * i / n_points
        # Punkt auf dem Großkreis (Einheitskugel)
        x = math.cos(t) * ux + math.sin(t) * vx
        y = math.cos(t) * uy + math.sin(t) * vy
        z = math.cos(t) * uz + math.sin(t) * vz
        lat = math.degrees(math.asin(max(-1.0, min(1.0, z))))
        lon = math.degrees(math.atan2(y, x))
        result.append((lat, lon))
    return result


def _bearing_deg_spherical(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Großkreis-Anfangs-Peilung auf der Kugel (R≈6371 km), identisch zur früheren ``bearing_deg``-Formel."""
    lat1_r = math.radians(lat1)
    lat2_r = math.radians(lat2)
    dlon = math.radians(lon2 - lon1)
    x = math.sin(dlon) * math.cos(lat2_r)
    y = math.cos(lat1_r) * math.sin(lat2_r) - math.sin(lat1_r) * math.cos(lat2_r) * math.cos(dlon)
    b = math.degrees(math.atan2(x, y))
    return (b + 360.0) % 360.0


def _vincenty_inverse_wgs84(
    lat1_deg: float, lon1_deg: float, lat2_deg: float, lon2_deg: float
) -> tuple[float, float, float] | None:
    """Vincenty-Inverse auf WGS-84 (T. Vincenty, Survey Review 1975).

    Liefert ``(s_m, α12_deg, α21_deg)`` mit Anfangs- bzw. End-Peilung, oder ``None`` bei
    fehlender Konvergenz (fast antipodal) — dann soll die Kugel-Näherung genutzt werden.
    """
    a = 6378137.0
    f = 1.0 / 298.257223563
    b = (1.0 - f) * a
    pi = math.pi

    phi1, lam1 = math.radians(lat1_deg), math.radians(lon1_deg)
    phi2, lam2 = math.radians(lat2_deg), math.radians(lon2_deg)

    if abs(phi1 - phi2) < 1e-15 and abs(lam1 - lam2) < 1e-15:
        return (0.0, 0.0, 0.0)

    L = lam2 - lam1
    tan_u1 = (1 - f) * math.tan(phi1)
    cos_u1 = 1 / math.sqrt(1 + tan_u1 * tan_u1)
    sin_u1 = tan_u1 * cos_u1
    tan_u2 = (1 - f) * math.tan(phi2)
    cos_u2 = 1 / math.sqrt(1 + tan_u2 * tan_u2)
    sin_u2 = tan_u2 * cos_u2

    antipodal = abs(L) > pi / 2 or abs(phi2 - phi1) > pi / 2
    lam = L
    sin_sq_sigma = 0.0
    sin_sigma = 0.0
    cos_sigma = -1.0 if antipodal else 1.0
    sigma = pi if antipodal else 0.0
    cos2_sigma_m = 1.0
    cos_sq_alpha = 1.0
    sin_lam = 0.0
    cos_lam = 0.0

    iterations = 0
    lam_prev: float | None = None
    while True:
        sin_lam = math.sin(lam)
        cos_lam = math.cos(lam)
        sin_sq_sigma = (cos_u2 * sin_lam) ** 2 + (cos_u1 * sin_u2 - sin_u1 * cos_u2 * cos_lam) ** 2
        if abs(sin_sq_sigma) < 1e-24:
            break
        sin_sigma = math.sqrt(sin_sq_sigma)
        cos_sigma = sin_u1 * sin_u2 + cos_u1 * cos_u2 * cos_lam
        sigma = math.atan2(sin_sigma, cos_sigma)
        sin_alpha = cos_u1 * cos_u2 * sin_lam / sin_sigma if sin_sigma > 0 else 0.0
        cos_sq_alpha = 1.0 - sin_alpha * sin_alpha
        cos2_sigma_m = (
            (cos_sigma - 2 * sin_u1 * sin_u2 / cos_sq_alpha) if abs(cos_sq_alpha) > 1e-15 else 0.0
        )
        C = (f / 16.0) * cos_sq_alpha * (4 + f * (4 - 3 * cos_sq_alpha))
        lam_prev = lam
        lam = L + (1 - C) * f * sin_alpha * (
            sigma + C * sin_sigma * (cos2_sigma_m + C * cos_sigma * (-1 + 2 * cos2_sigma_m * cos2_sigma_m))
        )
        iteration_check = abs(lam) - pi if antipodal else abs(lam)
        if iteration_check > pi:
            return None
        if abs(lam - lam_prev) <= 1e-12:
            break
        iterations += 1
        if iterations >= 1000:
            return None

    if abs(sin_sq_sigma) < 1e-24:
        return None

    u_sq = cos_sq_alpha * (a * a - b * b) / (b * b)
    A = 1 + u_sq / 16384 * (4096 + u_sq * (-768 + u_sq * (320 - 175 * u_sq)))
    B = u_sq / 1024 * (256 + u_sq * (-128 + u_sq * (74 - 47 * u_sq)))
    delta_sigma = B * sin_sigma * (
        cos2_sigma_m
        + B
        / 4
        * (
            cos_sigma * (-1 + 2 * cos2_sigma_m * cos2_sigma_m)
            - B
            / 6
            * cos2_sigma_m
            * (-3 + 4 * sin_sigma * sin_sigma)
            * (-3 + 4 * cos2_sigma_m * cos2_sigma_m)
        )
    )
    s = b * A * (sigma - delta_sigma)

    eps_js = 2.220446049250313e-16  # ~ Number.EPSILON in JS reference
    if abs(sin_sq_sigma) < eps_js:
        alpha1 = 0.0
        alpha2 = math.pi
    else:
        alpha1 = math.atan2(cos_u2 * sin_lam, cos_u1 * sin_u2 - sin_u1 * cos_u2 * cos_lam)
        alpha2 = math.atan2(cos_u1 * sin_lam, -sin_u1 * cos_u2 + cos_u1 * sin_u2 * cos_lam)

    a12 = (math.degrees(alpha1) + 360.0) % 360.0
    a21 = (math.degrees(alpha2) + 360.0) % 360.0
    return (s, a12, a21)


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Peilung von Punkt 1 zu Punkt 2 in Grad (0=Nord, 90=Ost, …).

    Standard: **WGS-84-Geodäte** (Vincenty-Inverse, Anfangs-Azimut). Fast antipodale Punkte:
    Fallback auf die frühere Kugel-Formel (identisch zur Großkreis-Anfangs-Peilung mit
    ``_EARTH_RADIUS_KM`` in ``destination_point`` / Beam-Polygonen).
    """
    inv = _vincenty_inverse_wgs84(lat1, lon1, lat2, lon2)
    if inv is not None:
        _s, a12, _a21 = inv
        if _s <= 1e-3:
            return _bearing_deg_spherical(lat1, lon1, lat2, lon2)
        return a12
    return _bearing_deg_spherical(lat1, lon1, lat2, lon2)


def maidenhead_to_lat_lon(grid: str) -> tuple[float, float] | None:
    """Maidenhead-Locator (2–10 Zeichen) → (Breite, Länge) Zentrum der Zelle in Grad.

    Dekodierung wie üblich (untere linke Ecke der Felder, dann Zentrum der Zelle).
    Vorheriger Fehler: Bei 4+ Zeichen wurde die Feldmittel (+10°/+5°) zur Quadrat-
    Position addiert, was die Position um ~ein halbes Feld verschoben hat.

    Gibt ``None`` zurück bei ungültigem Format.
    """
    s = "".join(grid.strip().upper().split())
    if len(s) < 2:
        return None
    if len(s) % 2 == 1:
        s = s[:-1]
    try:
        if not ("A" <= s[0] <= "R" and "A" <= s[1] <= "R"):
            return None
        # Südwest-Ecke des Feldes (2 Zeichen)
        lon = -180.0 + (ord(s[0]) - ord("A")) * 20
        lat = -90.0 + (ord(s[1]) - ord("A")) * 10
        n = len(s)
        if n >= 4:
            if not (s[2].isdigit() and s[3].isdigit()):
                return None
            lon += int(s[2]) * 2
            lat += int(s[3]) * 1
        if n >= 6:
            if not ("A" <= s[4] <= "X" and "A" <= s[5] <= "X"):
                return None
            lon += (ord(s[4]) - ord("A")) * (2.0 / 24)
            lat += (ord(s[5]) - ord("A")) * (1.0 / 24)
        if n >= 8:
            if not (s[6].isdigit() and s[7].isdigit()):
                return None
            lon += int(s[6]) * (2.0 / 240)
            lat += int(s[7]) * (1.0 / 240)
        if n >= 10:
            if not ("A" <= s[8] <= "X" and "A" <= s[9] <= "X"):
                return None
            lon += (ord(s[8]) - ord("A")) * (2.0 / 5760)
            lat += (ord(s[9]) - ord("A")) * (1.0 / 5760)

        # Zentrum der Zelle (je nach Präzision)
        if n == 2:
            lon += 10
            lat += 5
        elif n == 4:
            lon += 1
            lat += 0.5
        elif n == 6:
            lon += (2.0 / 24) / 2
            lat += (1.0 / 24) / 2
        elif n == 8:
            lon += (2.0 / 240) / 2
            lat += (1.0 / 240) / 2
        elif n == 10:
            lon += (2.0 / 5760) / 2
            lat += (1.0 / 5760) / 2
        return lat, lon
    except Exception:
        return None


def lat_lon_to_maidenhead(lat: float, lon: float, n_chars: int = 8) -> str:
    """Geografische Koordinaten (Grad WGS84) → Maidenhead-Locator.

    Auflösung wie bei ``maidenhead_to_lat_lon`` (2/4/6/8/10 Zeichen).
    Standard 8 Zeichen (z. B. ``JO31jg12``) für Kartenklicks; 6 = übliches ``JO31jg``.
    """
    try:
        n = int(n_chars)
    except (TypeError, ValueError):
        n = 8
    if n not in (2, 4, 6, 8, 10):
        n = 8
    try:
        lat_f = float(lat)
        lon_f = float(lon)
    except (TypeError, ValueError):
        return ""
    lon_f = max(-180.0, min(179.999999999, lon_f))
    lat_f = max(-90.0, min(89.999999999, lat_f))

    lon180 = lon_f + 180.0
    lat90 = lat_f + 90.0

    a = int(lon180 // 20.0)
    b = int(lat90 // 10.0)
    if not 0 <= a <= 17 or not 0 <= b <= 17:
        return ""
    s = chr(ord("A") + a) + chr(ord("A") + b)
    if n <= 2:
        return s

    lon_rem = lon180 - a * 20.0
    lat_rem = lat90 - b * 10.0
    d2 = int(lon_rem // 2.0)
    d3 = int(lat_rem // 1.0)
    d2 = max(0, min(9, d2))
    d3 = max(0, min(9, d3))
    s += str(d2) + str(d3)
    if n <= 4:
        return s

    lon_rem = lon_rem - d2 * 2.0
    lat_rem = lat_rem - d3 * 1.0
    step_lon = 2.0 / 24.0
    step_lat = 1.0 / 24.0
    c4 = int(lon_rem // step_lon)
    c5 = int(lat_rem // step_lat)
    c4 = max(0, min(23, c4))
    c5 = max(0, min(23, c5))
    s += chr(ord("A") + c4) + chr(ord("A") + c5)
    if n <= 6:
        return s

    lon_rem = lon_rem - c4 * step_lon
    lat_rem = lat_rem - c5 * step_lat
    step_lon = 2.0 / 240.0
    step_lat = 1.0 / 240.0
    d6 = int(lon_rem // step_lon)
    d7 = int(lat_rem // step_lat)
    d6 = max(0, min(9, d6))
    d7 = max(0, min(9, d7))
    s += str(d6) + str(d7)
    if n <= 8:
        return s

    lon_rem = lon_rem - d6 * step_lon
    lat_rem = lat_rem - d7 * step_lat
    step_lon = 2.0 / 5760.0
    step_lat = 1.0 / 5760.0
    c8 = int(lon_rem // step_lon)
    c9 = int(lat_rem // step_lat)
    c8 = max(0, min(23, c8))
    c9 = max(0, min(23, c9))
    s += chr(ord("A") + c8) + chr(ord("A") + c9)
    return s


def destination_point(
    lat: float, lon: float, bearing_deg_val: float, dist_km: float
) -> tuple[float, float]:
    """Berechnet den Zielpunkt von (lat, lon) bei Peilung und Distanz. Gibt (lat, lon) zurück."""
    d = dist_km / _EARTH_RADIUS_KM
    br = math.radians(bearing_deg_val)
    lat_r = math.radians(lat)
    lon_r = math.radians(lon)
    lat2_r = math.asin(math.sin(lat_r) * math.cos(d) + math.cos(lat_r) * math.sin(d) * math.cos(br))
    lon2_r = lon_r + math.atan2(
        math.sin(br) * math.sin(d) * math.cos(lat_r),
        math.cos(d) - math.sin(lat_r) * math.sin(lat2_r),
    )
    return (math.degrees(lat2_r), math.degrees(lon2_r))


def _points_along_great_circle(
    lat1: float, lon1: float, lat2: float, lon2: float, n_seg: int
) -> list[tuple[float, float]]:
    """Punkte entlang der Großkreislinie von (lat1,lon1) nach (lat2,lon2).
    n_seg = Anzahl Segmente → n_seg+1 Punkte (inkl. Start und Ende)."""
    if n_seg < 1:
        return [(lat1, lon1), (lat2, lon2)]
    lat1_r = math.radians(lat1)
    lon1_r = math.radians(lon1)
    lat2_r = math.radians(lat2)
    lon2_r = math.radians(lon2)
    # Slerp auf der Einheitskugel
    x1 = math.cos(lat1_r) * math.cos(lon1_r)
    y1 = math.cos(lat1_r) * math.sin(lon1_r)
    z1 = math.sin(lat1_r)
    x2 = math.cos(lat2_r) * math.cos(lon2_r)
    y2 = math.cos(lat2_r) * math.sin(lon2_r)
    z2 = math.sin(lat2_r)
    dot = x1 * x2 + y1 * y2 + z1 * z2
    dot = max(-1.0, min(1.0, dot))
    ang = math.acos(dot)
    if ang < 1e-9:
        return [(lat1, lon1), (lat2, lon2)]
    result: list[tuple[float, float]] = []
    for i in range(n_seg + 1):
        t = i / n_seg
        a = math.sin((1 - t) * ang) / math.sin(ang)
        b = math.sin(t * ang) / math.sin(ang)
        x = a * x1 + b * x2
        y = a * y1 + b * y2
        z = a * z1 + b * z2
        lat_r = math.atan2(z, math.sqrt(x * x + y * y))
        lon_r = math.atan2(y, x)
        result.append((math.degrees(lat_r), math.degrees(lon_r)))
    return result


def beam_polygon_points(
    lat: float, lon: float, azimuth_deg: float, opening_deg: float, range_km: float, steps: int = 24
) -> list[tuple[float, float]]:
    """Erzeugt Polygon-Punkte für den Antennen-Beam (Sektor) unter Berücksichtigung
    der Erdkrümmung. Die Kanten werden als Großkreisbögen approximiert.
    azimuth_deg: Mittelachse (0=Nord, 90=Ost).
    opening_deg: Gesamter Öffnungswinkel (halbe auf jeder Seite).
    Gibt [Antenne, P1, P2, ..., Antenne] zurück."""
    half = opening_deg / 2.0
    start_bearing = (azimuth_deg - half + 360.0) % 360.0
    end_bearing = (azimuth_deg + half + 360.0) % 360.0
    if end_bearing <= start_bearing:
        end_bearing += 360.0

    # Kantenlänge für Unterteilung: bei großen Reichweiten mehr Segmente
    radial_seg = max(3, min(20, int(range_km / 500) + 1))
    arc_seg = max(2, min(15, int(range_km / 1000) + 1))

    # Punkte auf dem Frontbogen (Kreis in Entfernung range_km)
    arc_pts: list[tuple[float, float]] = []
    for i in range(steps + 1):
        t_ = i / steps
        b = (start_bearing + t_ * (end_bearing - start_bearing)) % 360.0
        arc_pts.append(destination_point(lat, lon, b, range_km))

    points: list[tuple[float, float]] = [(lat, lon)]
    # Radial links: Antenne → erster Bogenpunkt (Großkreis)
    for pt in _points_along_great_circle(lat, lon, arc_pts[0][0], arc_pts[0][1], radial_seg)[1:]:
        points.append(pt)
    # Bogen: zwischen aufeinanderfolgenden Bogenpunkten (Großkreis)
    for j in range(1, len(arc_pts)):
        segs = _points_along_great_circle(
            arc_pts[j - 1][0],
            arc_pts[j - 1][1],
            arc_pts[j][0],
            arc_pts[j][1],
            arc_seg,
        )
        for pt in segs[1:]:
            points.append(pt)
    # Radial rechts: letzter Bogenpunkt → Antenne (Großkreis)
    for pt in _points_along_great_circle(arc_pts[-1][0], arc_pts[-1][1], lat, lon, radial_seg)[1:]:
        points.append(pt)
    return points


def beam_center_line_points(
    lat: float, lon: float, azimuth_deg: float, range_km: float, n_seg: int = 15
) -> list[tuple[float, float]]:
    """Punkte der Mittellinie (Großkreisbogen) für die gestrichelte Linie."""
    end_pt = destination_point(lat, lon, azimuth_deg, range_km)
    return _points_along_great_circle(
        lat, lon, end_pt[0], end_pt[1], max(2, min(n_seg, int(range_km / 300) + 1))
    )
