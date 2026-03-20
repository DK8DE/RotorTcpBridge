"""Smoke-Tests für geo_utils (Peilung)."""
from __future__ import annotations

import pytest

from rotortcpbridge.geo_utils import bearing_deg


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
