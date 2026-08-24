"""Per-site summariser.

Ported from mkmapdiary (`lib/geoCluster.py`), MIT-relicensed for this project
(parent spec section 16). Unchanged except the `LocalProjection` import. Does NOT
cluster despite its name — DBSCAN (`derive/sites.py`) partitions; this describes
one already-formed point set (parent spec section 7).
"""

from __future__ import annotations

import copy

import numpy as np
from scipy import stats
from scipy.spatial import ConvexHull
from shapely.geometry import MultiPoint

from fledermap.util.projection import LocalProjection


class GeoCluster:
    def __init__(
        self,
        locations: list[tuple[float, float]],
        *,
        remove_outliers: bool = True,
    ) -> None:
        # Interface expects locations as (lon, lat) tuples for consistency with
        # GeoJSON.
        self.__locations = locations
        if remove_outliers:
            self.__remove_outliers()

        self.__degrees, self.__distance, self.__midpoint = (
            self.__longest_greatcircle_separation()
        )

    EARTH_RADIUS_M = 6371008.8  # mean Earth radius in meters

    def __remove_outliers(self) -> None:
        if len(self.__locations) < 4:
            return  # Not enough points to determine outliers

        proj = LocalProjection(self.shape)
        local_locations = proj.to_local_np(np.array(self.__locations))

        # A degenerate spread (all points identical, or only 2 distinct values
        # per axis) makes every point sit exactly at the z-score threshold, or
        # produces nan from a zero-variance stddev. Z-score outlier removal is
        # meaningless on such input, and applying it anyway empties the point
        # set entirely: 4 identical points -> no point passes `z < 1` ->
        # `mass_point` is `(None, None)` -> `derive_sites` builds a WKTElement
        # with literal "None" text -> Postgres fails to parse the geometry on
        # write. `np.ptp` (peak-to-peak, max-min per axis) catches it before
        # `zscore` runs, which also avoids scipy's "precision loss ...
        # catastrophic cancellation" RuntimeWarning. Skip removal rather than
        # discard every point when nothing distinguishes an outlier from the
        # rest.
        if np.ptp(local_locations, axis=0).min() == 0:
            return

        threshold = 1
        z_scores = np.abs(stats.zscore(local_locations))
        filtered_data = local_locations[(z_scores < threshold).all(axis=1)]
        if filtered_data.shape[0] == 0:
            # Defensive backstop for any other degenerate shape: every point
            # looked like "an outlier", so keep them all.
            return
        self.__locations = proj.to_wgs_np(filtered_data).tolist()

    @property
    def locations(self) -> list[tuple[float, float]]:
        return copy.deepcopy(self.__locations)

    @property
    def separation_degrees(self) -> float:
        return self.__degrees

    @property
    def separation_meters(self) -> float:
        return self.__distance

    @property
    def midpoint(self) -> tuple[float | None, float | None]:
        return copy.deepcopy(self.__midpoint)

    @property
    def shape(self) -> MultiPoint:
        return MultiPoint(self.__locations)

    @property
    def mass_point(self) -> tuple[float | None, float | None]:
        if len(self.__locations) == 0:
            return (None, None)

        pts = np.array(self.__locations)
        lon = np.radians(pts[:, 0])
        lat = np.radians(pts[:, 1])

        x = np.cos(lat) * np.cos(lon)
        y = np.cos(lat) * np.sin(lon)
        z = np.sin(lat)

        x_mean = np.mean(x)
        y_mean = np.mean(y)
        z_mean = np.mean(z)

        lon_mean = np.arctan2(y_mean, x_mean)
        hyp = np.sqrt(x_mean * x_mean + y_mean * y_mean)
        lat_mean = np.arctan2(z_mean, hyp)

        return (
            (np.degrees(lon_mean) + 540) % 360 - 180,
            np.degrees(lat_mean),
        )

    @property
    def radius(self) -> float:
        return self.__distance / 2

    @property
    def zoom_level(self) -> int:
        if len(self.__locations) == 0:
            return 18
        if self.__degrees == 0:
            return 18

        adjustment_factor = 2
        level = int(round(np.log2(360.0 / self.__degrees * adjustment_factor)))
        return max(min(level, 18), 3)

    @staticmethod
    def _greatcircle_angle(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        return np.arccos(
            np.clip(
                np.sin(lat1) * np.sin(lat2)
                + np.cos(lat1) * np.cos(lat2) * np.cos(lon1 - lon2),
                -1,
                1,
            ),
        )

    @staticmethod
    def _greatcircle_midpoint(
        lat1: float, lon1: float, lat2: float, lon2: float
    ) -> tuple[float, float]:
        dlon = lon2 - lon1
        bx = np.cos(lat2) * np.cos(dlon)
        by = np.cos(lat2) * np.sin(dlon)
        lat3 = np.arctan2(
            np.sin(lat1) + np.sin(lat2),
            np.sqrt((np.cos(lat1) + bx) ** 2 + by**2),
        )
        lon3 = lon1 + np.arctan2(by, np.cos(lat1) + bx)
        return lat3, lon3

    def __longest_greatcircle_separation(
        self,
    ) -> tuple[float, float, tuple[float | None, float | None]]:
        pts = np.array(self.__locations)
        n = len(pts)

        if n < 2:
            return 0.0, 0.0, (None, None)

        if n == 2:
            lon1, lat1 = np.radians(pts[0])
            lon2, lat2 = np.radians(pts[1])
            ang = self._greatcircle_angle(lat1, lon1, lat2, lon2)
            mid_lat, mid_lon = self._greatcircle_midpoint(lat1, lon1, lat2, lon2)
            separation_m = ang * self.EARTH_RADIUS_M
            return (
                np.degrees(ang),
                separation_m,
                ((np.degrees(mid_lon) + 540) % 360 - 180, np.degrees(mid_lat)),
            )

        try:
            hull = ConvexHull(pts)
            hull_pts = pts[hull.vertices]
        except Exception:  # noqa: BLE001 — degenerate hull (collinear points) falls
            # back to comparing every point, matching the ported original.
            hull_pts = pts

        lon = np.radians(hull_pts[:, 0])
        lat = np.radians(hull_pts[:, 1])

        sin_lat = np.sin(lat)
        cos_lat = np.cos(lat)
        dlon = lon[:, None] - lon[None, :]
        central_angle = np.arccos(
            np.clip(
                sin_lat[:, None] * sin_lat[None, :]
                + cos_lat[:, None] * cos_lat[None, :] * np.cos(dlon),
                -1,
                1,
            ),
        )

        i, j = np.unravel_index(np.argmax(central_angle), central_angle.shape)
        ang = central_angle[i, j]
        separation_m = ang * self.EARTH_RADIUS_M

        mid_lat, mid_lon = self._greatcircle_midpoint(lat[i], lon[i], lat[j], lon[j])
        mid_lat_deg = np.degrees(mid_lat)
        mid_lon_deg = (np.degrees(mid_lon) + 540) % 360 - 180

        return np.degrees(ang), separation_m, (mid_lon_deg, mid_lat_deg)
