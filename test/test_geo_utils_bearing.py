"""Smoke-Tests für geo_utils (Peilung)."""

from __future__ import annotations

import pytest

from rotortcpbridge.geo_utils import bearing_deg, effective_station_lat_lon, maidenhead_to_lat_lon


def test_bearing_north() -> None:
    b = bearing_deg(0.0, 0.0, 1.0, 0.0)
    assert b == pytest.approx(0.0, abs=0.5)


def test_bearing_east() -> None:
    b = bearing_deg(0.0, 0.0, 0.0, 1.0)
    assert b == pytest.approx(90.0, abs=0.5)


def test_destination_point_north_short() -> None:
    from rotortcpbridge.geo_utils import destination_point

    lat2, lon2 = destination_point(49.0, 8.0, 0.0, 1.0)
    assert lat2 > 49.0
    assert abs(lon2 - 8.0) < 0.01


def test_effective_station_default_no_locator() -> None:
    ui = {"location_lat": 49.502651, "location_lon": 8.375019}
    lat, lon = effective_station_lat_lon(ui)
    assert lat == pytest.approx(49.502651)
    assert lon == pytest.approx(8.375019)


def test_effective_station_locator_uses_field_center() -> None:
    ui = {
        "location_lat": 49.502651,
        "location_lon": 8.375019,
        "location_locator": "JO31jg",
    }
    exp = maidenhead_to_lat_lon("JO31jg")
    assert exp is not None
    lat, lon = effective_station_lat_lon(ui)
    assert lat == pytest.approx(exp[0])
    assert lon == pytest.approx(exp[1])


def test_effective_station_explicit_coords_override_locator() -> None:
    ui = {
        "location_lat": 52.5,
        "location_lon": 10.0,
        "location_locator": "JO31jg",
    }
    lat, lon = effective_station_lat_lon(ui)
    assert lat == pytest.approx(52.5)
    assert lon == pytest.approx(10.0)
