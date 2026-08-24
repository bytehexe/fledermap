from __future__ import annotations

import numpy as np
import pytest
from shapely.geometry import Point

from fledermap.util.projection import LocalProjection


def test_picks_northern_utm_zone_for_berlin() -> None:
    proj = LocalProjection(Point(13.4, 52.5))
    assert proj.crs.to_epsg() == 32633  # WGS84 / UTM zone 33N


def test_picks_southern_utm_zone_for_southern_hemisphere() -> None:
    # Santiago, Chile
    proj = LocalProjection(Point(-70.6, -33.4))
    assert proj.crs.to_epsg() == 32719  # WGS84 / UTM zone 19S


def test_picks_ups_north_above_84_degrees() -> None:
    proj = LocalProjection(Point(0.0, 85.0))
    assert proj.crs.to_epsg() == 32661  # WGS84 / UPS North


def test_picks_ups_south_below_minus_80_degrees() -> None:
    proj = LocalProjection(Point(0.0, -85.0))
    assert proj.crs.to_epsg() == 32761  # WGS84 / UPS South


def test_round_trip_recovers_the_original_coordinates() -> None:
    proj = LocalProjection(Point(13.4, 52.5))
    original = np.array([[13.4, 52.5], [13.41, 52.51]])

    local = proj.to_local_np(original)
    recovered = proj.to_wgs_np(local)

    assert recovered == pytest.approx(original, abs=1e-9)


def test_eps_in_local_metres_is_not_eps_in_degrees() -> None:
    """The pitfall parent spec section 7 pins: raw EPSG:4326 coordinates are
    degrees, not metres. Two points ~75m apart at Berlin's latitude must be
    within 75 units of each other in the LOCAL projection but nowhere near 75
    units apart in raw lon/lat (where 75 would be ~8000km)."""
    proj = LocalProjection(Point(13.4, 52.5))
    points = np.array([[13.4, 52.5], [13.401, 52.5]])  # ~68m apart at this latitude

    local = proj.to_local_np(points)
    local_distance = np.linalg.norm(local[0] - local[1])
    raw_distance = np.linalg.norm(points[0] - points[1])

    assert local_distance == pytest.approx(68.0, abs=5.0)
    assert raw_distance < 0.01  # far under 1 in raw degree units


def test_ups_north_boundary_is_inclusive_at_84_degrees() -> None:
    proj = LocalProjection(Point(0.0, 84.0))
    assert proj.crs.to_epsg() == 32661  # UPS North


def test_just_below_84_degrees_is_regular_utm() -> None:
    proj = LocalProjection(Point(0.0, 83.999999))
    assert proj.crs.to_epsg() == 32631  # UTM zone 31N, not UPS


def test_ups_south_boundary_is_inclusive_at_minus_80_degrees() -> None:
    proj = LocalProjection(Point(0.0, -80.0))
    assert proj.crs.to_epsg() == 32761  # UPS South


def test_just_above_minus_80_degrees_is_regular_utm() -> None:
    proj = LocalProjection(Point(0.0, -79.999999))
    assert proj.crs.to_epsg() == 32731  # UTM zone 31S, not UPS
