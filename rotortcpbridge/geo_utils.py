"""Geografische Hilfsfunktionen für Karten und Antennen-Beam."""
from __future__ import annotations

import math

_EARTH_RADIUS_KM = 6371.0


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Berechnet die Peilung von Punkt 1 zu Punkt 2 in Grad (0=Nord, 90=Ost, 180=Süd, 270=West)."""
    lat1_r = math.radians(lat1)
    lat2_r = math.radians(lat2)
    dlon = math.radians(lon2 - lon1)
    x = math.sin(dlon) * math.cos(lat2_r)
    y = math.cos(lat1_r) * math.sin(lat2_r) - math.sin(lat1_r) * math.cos(lat2_r) * math.cos(dlon)
    b = math.degrees(math.atan2(x, y))
    return (b + 360.0) % 360.0


def destination_point(
    lat: float, lon: float, bearing_deg_val: float, dist_km: float
) -> tuple[float, float]:
    """Berechnet den Zielpunkt von (lat, lon) bei Peilung und Distanz. Gibt (lat, lon) zurück."""
    d = dist_km / _EARTH_RADIUS_KM
    br = math.radians(bearing_deg_val)
    lat_r = math.radians(lat)
    lon_r = math.radians(lon)
    lat2_r = math.asin(
        math.sin(lat_r) * math.cos(d)
        + math.cos(lat_r) * math.sin(d) * math.cos(br)
    )
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
            arc_pts[j - 1][0], arc_pts[j - 1][1],
            arc_pts[j][0], arc_pts[j][1],
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
    return _points_along_great_circle(lat, lon, end_pt[0], end_pt[1], max(2, min(n_seg, int(range_km / 300) + 1)))
