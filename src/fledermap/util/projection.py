"""Local-metre map projection.

Ported from mkmapdiary (`util/projection.py`), MIT-relicensed for this project
(parent spec section 16 — the owner holds copyright to both, so this is settled,
not re-decided here). One addition beyond the original: the `crs` property, so
callers (and tests) can observe which UTM/UPS zone got picked.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from pyproj import CRS, Transformer
from shapely.ops import transform


class LocalProjection:
    @staticmethod
    def __get_local_projection(lon: float, lat: float) -> CRS:
        # Interface uses (lon, lat) format for consistency with GeoJSON and web
        # standards.
        # UPS zones
        if lat >= 84:
            return CRS.from_epsg(32661)  # UPS North
        if lat <= -80:
            return CRS.from_epsg(32761)  # UPS South

        # UTM zones
        zone = int(math.floor((lon + 180) / 6) + 1)
        hemisphere = "north" if lat >= 0 else "south"
        epsg_code = 32600 + zone if hemisphere == "north" else 32700 + zone
        return CRS.from_epsg(epsg_code)

    def __init__(self: LocalProjection, shape: Any) -> None:
        centroid = shape.centroid

        # centroid.x is longitude, centroid.y is latitude (shapely uses (x=lon, y=lat)).
        self.__crs_proj = self.__get_local_projection(centroid.x, centroid.y)
        self.__crs_wgs = "EPSG:4326"

        self.__transformer_to_proj = Transformer.from_crs(
            self.__crs_wgs,
            self.__crs_proj,
            always_xy=True,
        )
        self.__transformer_to_wgs = Transformer.from_crs(
            self.__crs_proj,
            self.__crs_wgs,
            always_xy=True,
        )

    @property
    def crs(self: LocalProjection) -> CRS:
        return self.__crs_proj

    def to_local_np(self: LocalProjection, lonlat_array: np.ndarray) -> np.ndarray:
        x_array, y_array = self.__transformer_to_proj.transform(
            lonlat_array[:, 0],
            lonlat_array[:, 1],
        )
        return np.column_stack((x_array, y_array))

    def to_wgs_np(self: LocalProjection, lonlat_array: np.ndarray) -> np.ndarray:
        lon_array, lat_array = self.__transformer_to_wgs.transform(
            lonlat_array[:, 0],
            lonlat_array[:, 1],
        )
        return np.column_stack((lon_array, lat_array))

    def to_local(self: LocalProjection, shape: Any) -> Any:
        return transform(self.__transformer_to_proj.transform, shape)

    def to_wgs(self: LocalProjection, shape: Any) -> Any:
        return transform(self.__transformer_to_wgs.transform, shape)
