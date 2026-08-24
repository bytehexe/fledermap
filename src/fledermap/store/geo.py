"""Shared geometry decoding, used by both `services/ingest.py` and
`services/derive.py`.

Uses `geoalchemy2`'s own `to_shape` (needs shapely) rather than hand-parsing WKB.
Phase 1's `services/ingest.py` avoided this specifically because shapely was not
otherwise a dependency; Phase 2 makes it one regardless (`LocalProjection`,
`GeoCluster`), so that reason no longer applies.
"""

from __future__ import annotations

from geoalchemy2.elements import WKBElement
from geoalchemy2.shape import to_shape
from shapely.geometry import Point


def decode_point(elem: object | None) -> tuple[float, float] | None:
    """Decode a stored geography Point into (lon, lat), or None if absent."""
    if not isinstance(elem, WKBElement):
        return None
    point = to_shape(elem)
    assert isinstance(point, Point)
    return (point.x, point.y)
