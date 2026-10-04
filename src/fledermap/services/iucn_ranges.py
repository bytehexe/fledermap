"""IUCN Red List range plausibility check (design spec
2026-10-04-fledermap-iucn-range-check-design.md).

Fetching and cache-writing run in the worker; the web process only calls `range_status` /
`attach_ranges`, which read the cache. The polygons are IUCN data under non-commercial terms
that forbid redistribution (T&C v3.1 §4), so nothing here hands a polygon to a caller outside
this module: `range_status` reduces them to a verdict and a distance.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import requests
from shapely.geometry import MultiPolygon, Polygon, shape

from fledermap.domain.ranges import KEPT_PRESENCE

LAYER_QUERY_URL = "https://data-gis.unep-wcmc.org/server/rest/services/Hosted/IUCN_Red_List/MapServer/0/query"
# Server-side simplification tolerance in degrees (~2 km): far below the "near" threshold, so
# it cannot change a verdict, and it keeps the download and the stored rows small.
_SIMPLIFY_DEGREES = 0.02
_REQUEST_TIMEOUT_S = 60
_USER_AGENT = "fledermap-iucn-range-check (non-commercial hobby project)"


class IucnFetchError(Exception):
    """The service answered, but not with a usable range -- distinct from "no such species"
    (an empty feature list), which is a legitimate answer that gets cached."""


@dataclass(frozen=True)
class RangePolygon:
    presence: int
    origin: int
    wkt: str  # MULTIPOLYGON, EPSG:4326


def _int_property(properties: dict[str, Any], key: str, query_name: str) -> int:
    try:
        return int(properties[key])
    except (KeyError, TypeError, ValueError) as exc:
        msg = f"IUCN feature for {query_name!r} has no usable {key!r}"
        raise IucnFetchError(msg) from exc


def _parse(payload: Any, query_name: str) -> list[RangePolygon]:
    # ArcGIS reports a rejected query as HTTP 200 with an "error" object. Reading that as "no
    # features" would cache a transient failure as `not_found` and never retry it.
    if not isinstance(payload, dict) or "error" in payload:
        msg = (
            f"IUCN service rejected the query for {query_name!r}: {str(payload)[:200]}"
        )
        raise IucnFetchError(msg)
    features = payload.get("features")
    if not isinstance(features, list):
        msg = f"IUCN reply for {query_name!r} has no 'features' list"
        raise IucnFetchError(msg)
    nested = payload.get("properties")
    truncated = payload.get("exceededTransferLimit") or (
        isinstance(nested, dict) and nested.get("exceededTransferLimit")
    )
    if truncated:
        # A partial range would make real records look implausible; we do not paginate because
        # one species never approaches the 2,000-record limit.
        msg = f"IUCN reply for {query_name!r} was truncated"
        raise IucnFetchError(msg)

    polygons: list[RangePolygon] = []
    for feature in features:
        geometry = feature.get("geometry")
        if geometry is None:
            continue
        properties = feature.get("properties") or {}
        presence = _int_property(properties, "presence", query_name)
        origin = _int_property(properties, "origin", query_name)
        geom = shape(geometry)
        if geom.is_empty:
            continue
        if isinstance(geom, Polygon):
            multi = MultiPolygon([geom])
        elif isinstance(geom, MultiPolygon):
            multi = geom
        else:
            msg = f"IUCN reply for {query_name!r} has unexpected geometry {geom.geom_type!r}"
            raise IucnFetchError(msg)
        polygons.append(RangePolygon(presence, origin, multi.wkt))
    return polygons


def fetch_range(
    query_name: str,
    *,
    get: Callable[..., Any] = requests.get,
) -> list[RangePolygon]:
    """The kept range polygons for one IUCN species name; `[]` when the service has none.
    `get` is injectable so tests never touch the network."""
    quoted = query_name.replace("'", "''")
    presence = ",".join(str(p) for p in KEPT_PRESENCE)
    response = get(
        LAYER_QUERY_URL,
        params={
            "where": f"sci_name = '{quoted}' AND presence IN ({presence})",
            "outFields": "presence,origin",
            "returnGeometry": "true",
            "outSR": "4326",
            "maxAllowableOffset": str(_SIMPLIFY_DEGREES),
            "geometryPrecision": "3",
            "f": "geojson",
        },
        headers={"User-Agent": _USER_AGENT},
        timeout=_REQUEST_TIMEOUT_S,
    )
    response.raise_for_status()
    return _parse(response.json(), query_name)
