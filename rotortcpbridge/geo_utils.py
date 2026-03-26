"""Geografische Hilfsfunktionen für Karten und Antennen-Beam."""

from __future__ import annotations

import math
from datetime import datetime, timezone

_EARTH_RADIUS_KM = 6371.0


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


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Berechnet die Peilung von Punkt 1 zu Punkt 2 in Grad (0=Nord, 90=Ost, 180=Süd, 270=West)."""
    lat1_r = math.radians(lat1)
    lat2_r = math.radians(lat2)
    dlon = math.radians(lon2 - lon1)
    x = math.sin(dlon) * math.cos(lat2_r)
    y = math.cos(lat1_r) * math.sin(lat2_r) - math.sin(lat1_r) * math.cos(lat2_r) * math.cos(dlon)
    b = math.degrees(math.atan2(x, y))
    return (b + 360.0) % 360.0


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
