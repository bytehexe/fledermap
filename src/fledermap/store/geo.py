"""Shared geometry decoding, used by both `services/ingest.py` and
`services/derive.py`.

Uses `geoalchemy2`'s own `to_shape` (needs shapely) rather than hand-parsing WKB.
Phase 1's `services/ingest.py` avoided this specifically because shapely was not
otherwise a dependency; Phase 2 makes it one regardless (`LocalProjection`,
`GeoCluster`), so that reason no longer applies.
"""

from __future__ import annotations

from geoalchemy2.elements import WKBElement, WKTElement
from geoalchemy2.shape import to_shape
from shapely.geometry import Point


def decode_point(elem: object | None) -> tuple[float, float] | None:
    """Decode a stored geography Point into (lon, lat), or None if absent.

    Accepts `WKTElement` as well as `WKBElement`: every value that has round-
    tripped through the database comes back as `WKBElement`, but a `Recording`
    built directly in Python (e.g. a unit test that never touches a session)
    still carries the `WKTElement` it was constructed with. `to_shape` already
    handles both natively (found via `derive/sessions.py`'s `classify_kind`
    unit tests, which call it on freshly-constructed, unpersisted recordings).
    """
    if not isinstance(elem, (WKBElement, WKTElement)):
        return None
    point = to_shape(elem)
    if not isinstance(point, Point):
        # Not an `assert`: `python -O` strips those, and this guards real data
        # integrity, not just mypy narrowing — a non-Point geometry must fail
        # loudly rather than silently yield the wrong coordinates.
        msg = f"expected a Point geometry, got {type(point).__name__}"
        raise TypeError(msg)
    return (point.x, point.y)
