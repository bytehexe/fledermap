"""Ported from mkmapdiary/tests/test_geo_cluster_math.py (great-circle math,
unchanged) plus new tests for the properties that math feeds into."""

from __future__ import annotations

import math
import warnings

import pytest

from fledermap.derive.geo_cluster import GeoCluster


class TestGeoClusterMathematicalFunctions:
    def test_greatcircle_angle_same_point(self) -> None:
        lat1 = lon1 = lat2 = lon2 = math.radians(45.0)
        angle = GeoCluster._greatcircle_angle(lat1, lon1, lat2, lon2)
        assert abs(angle) < 1e-10

    def test_greatcircle_angle_antipodal_points(self) -> None:
        lat1, lon1 = math.radians(0.0), math.radians(0.0)
        lat2, lon2 = math.radians(0.0), math.radians(180.0)
        angle = GeoCluster._greatcircle_angle(lat1, lon1, lat2, lon2)
        assert abs(angle - math.pi) < 1e-10

    def test_greatcircle_angle_quarter_circle(self) -> None:
        lat1, lon1 = math.radians(90.0), math.radians(0.0)
        lat2, lon2 = math.radians(0.0), math.radians(0.0)
        angle = GeoCluster._greatcircle_angle(lat1, lon1, lat2, lon2)
        assert abs(angle - math.pi / 2) < 1e-10

    def test_greatcircle_angle_known_cities(self) -> None:
        nyc_lat, nyc_lon = math.radians(40.7128), math.radians(-74.0060)
        london_lat, london_lon = math.radians(51.5074), math.radians(-0.1278)
        angle = GeoCluster._greatcircle_angle(nyc_lat, nyc_lon, london_lat, london_lon)
        assert 49.0 < math.degrees(angle) < 51.0

    def test_greatcircle_angle_symmetry(self) -> None:
        lat1, lon1 = math.radians(40.0), math.radians(-74.0)
        lat2, lon2 = math.radians(51.0), math.radians(0.0)
        angle1 = GeoCluster._greatcircle_angle(lat1, lon1, lat2, lon2)
        angle2 = GeoCluster._greatcircle_angle(lat2, lon2, lat1, lon1)
        assert abs(angle1 - angle2) < 1e-10

    def test_greatcircle_midpoint_equator_points(self) -> None:
        lat1, lon1 = math.radians(0.0), math.radians(0.0)
        lat2, lon2 = math.radians(0.0), math.radians(90.0)
        mid_lat, mid_lon = GeoCluster._greatcircle_midpoint(lat1, lon1, lat2, lon2)
        assert abs(mid_lat) < 1e-10
        assert abs(mid_lon - math.radians(45.0)) < 1e-10

    def test_greatcircle_midpoint_symmetry(self) -> None:
        lat1, lon1 = math.radians(40.0), math.radians(-74.0)
        lat2, lon2 = math.radians(51.0), math.radians(0.0)
        mid1 = GeoCluster._greatcircle_midpoint(lat1, lon1, lat2, lon2)
        mid2 = GeoCluster._greatcircle_midpoint(lat2, lon2, lat1, lon1)
        assert mid1 == pytest.approx(mid2, abs=1e-10)

    def test_mathematical_consistency(self) -> None:
        lat1, lon1 = math.radians(40.0), math.radians(-74.0)
        lat2, lon2 = math.radians(51.0), math.radians(0.0)
        full_angle = GeoCluster._greatcircle_angle(lat1, lon1, lat2, lon2)
        mid_lat, mid_lon = GeoCluster._greatcircle_midpoint(lat1, lon1, lat2, lon2)
        half_angle = GeoCluster._greatcircle_angle(lat1, lon1, mid_lat, mid_lon)
        assert abs(2 * half_angle - full_angle) < 1e-8


class TestGeoClusterProperties:
    def test_mass_point_of_two_points_is_between_them(self) -> None:
        cluster = GeoCluster([(13.0, 52.0), (13.02, 52.0)])
        lon, lat = cluster.mass_point
        assert lon == pytest.approx(13.01, abs=1e-6)
        assert lat == pytest.approx(52.0, abs=1e-6)

    def test_radius_is_half_separation_meters(self) -> None:
        cluster = GeoCluster([(13.0, 52.0), (13.02, 52.0)])
        assert cluster.radius == pytest.approx(cluster.separation_meters / 2)

    def test_zoom_level_is_max_for_empty_locations(self) -> None:
        cluster = GeoCluster([])
        assert cluster.zoom_level == 18

    def test_mass_point_is_none_for_empty_locations(self) -> None:
        cluster = GeoCluster([])
        assert cluster.mass_point == (None, None)

    def test_outlier_is_removed_with_four_or_more_points(self) -> None:
        tight = [(13.0, 52.0), (13.0001, 52.0), (13.0002, 52.0001), (13.0001, 52.0002)]
        far_outlier = (20.0, 52.0)  # far east, same latitude
        cluster = GeoCluster([*tight, far_outlier])
        assert far_outlier not in cluster.locations
        assert len(cluster.locations) == len(tight)

    def test_four_identical_points_keep_a_real_mass_point(self) -> None:
        """Regression: a stationary detector reporting the same rounded GPS fix
        repeatedly gives a zero-variance spread. `stats.zscore` then returns
        nan, no point passes `z < 1`, and the whole set was discarded —
        `mass_point` became `(None, None)`, which `derive_sites` interpolated
        into `POINT(None None)` and Postgres refused to parse. The zero-spread
        case is now skipped before `zscore` runs, which also silences scipy's
        catastrophic-cancellation RuntimeWarning."""
        points = [(13.4, 52.5)] * 4
        with warnings.catch_warnings():
            # This project treats a warning as a defect; make one fail here.
            warnings.simplefilter("error")
            cluster = GeoCluster(points)

        assert len(cluster.locations) == len(points)
        lon, lat = cluster.mass_point
        assert lon == pytest.approx(13.4, abs=1e-9)
        assert lat == pytest.approx(52.5, abs=1e-9)

    def test_two_distinct_values_per_axis_keep_every_point(self) -> None:
        """A rectangle of four fixes: every point sits at exactly `z == 1`, so
        `z < 1` keeps none of them. Same degenerate-spread guard."""
        points = [(13.4, 52.5), (13.4, 52.5001), (13.4001, 52.5), (13.4001, 52.5001)]
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            cluster = GeoCluster(points)

        assert len(cluster.locations) == len(points)
        assert cluster.mass_point != (None, None)

    def test_remove_outliers_false_keeps_every_point(self) -> None:
        """`derive_sites` passes `remove_outliers=False`: DBSCAN already decided
        membership, and trimming at `z < 1` would understate `radius_m` against
        a `recording_count` that still counts every member."""
        tight = [(13.0, 52.0), (13.0001, 52.0), (13.0002, 52.0001), (13.0001, 52.0002)]
        far_outlier = (20.0, 52.0)
        points = [*tight, far_outlier]

        cluster = GeoCluster(points, remove_outliers=False)

        assert len(cluster.locations) == len(points)
        assert far_outlier in cluster.locations
        # ... and the default is unchanged: see
        # `test_outlier_is_removed_with_four_or_more_points` above.

    def test_no_outlier_removal_below_four_points(self) -> None:
        # Below 4 points, `GeoCluster.__remove_outliers` deliberately leaves the
        # set untouched — not enough points to determine an outlier.
        points = [(13.0, 52.0), (20.0, 52.0), (13.0001, 52.0)]
        cluster = GeoCluster(points)
        assert len(cluster.locations) == len(points)
