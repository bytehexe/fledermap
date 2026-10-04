"""IUCN Red List range plausibility check (design spec
2026-10-04-fledermap-iucn-range-check-design.md).

Fetching and cache-writing run in the worker; the web process only calls `range_status` /
`attach_ranges`, which read the cache. The polygons are IUCN data under non-commercial terms
that forbid redistribution (T&C v3.1 §4), so nothing here hands a polygon to a caller outside
this module: `range_status` reduces them to a verdict and a distance.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import requests
from shapely.geometry import MultiPolygon, Polygon, shape
from sqlalchemy import delete, func, or_, select, text
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.ranges import (
    FETCH_FOUND,
    FETCH_NOT_FOUND,
    IUCN_RELEASE,
    KEPT_PRESENCE,
    RANGE_CODE_SOURCE,
    VAGRANT_ORIGIN,
    NoDataReason,
    RangeKind,
    RangeStatus,
)
from fledermap.store.models import (
    ClassifierDetection,
    Identification,
    Recording,
    SpeciesRange,
    SpeciesRangeFetch,
    Taxon,
    TaxonCode,
)

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


# Repair, then simplify, then force a MULTIPOLYGON in PostGIS rather than in shapely: the
# service's own simplification can leave a self-intersecting ring, and `ST_MakeValid` is the
# one place that repairs it without a second geometry library. The geography cast must come
# last -- the geometry functions above do not exist for it.
_INSERT_POLYGON = text(
    """
    INSERT INTO species_range (taxon_id, presence, origin, geom)
    SELECT :taxon_id, :presence, :origin,
           ST_Multi(
               ST_CollectionExtract(
                   ST_SimplifyPreserveTopology(
                       ST_MakeValid(ST_GeomFromText(:wkt, 4326)), :tolerance
                   ),
                   3
               )
           )::geography
    """,
)


def pending_taxa(session: OrmSession) -> list[tuple[int, str]]:
    """(taxon_id, IUCN name) for every species that occurs in this database -- as an
    identification or a raw classifier detection -- has an `iucn2022` code, and has not been
    fetched under that code. A changed code makes the taxon pending again; a `not_found` fetch
    does not, so an unknown species is asked about exactly once."""
    used = or_(
        TaxonCode.taxon_id.in_(
            select(Identification.taxon_id).where(Identification.taxon_id.is_not(None)),
        ),
        TaxonCode.taxon_id.in_(
            select(ClassifierDetection.taxon_id).where(
                ClassifierDetection.taxon_id.is_not(None),
            ),
        ),
    )
    rows = session.execute(
        select(TaxonCode.taxon_id, TaxonCode.code)
        .join(Taxon, Taxon.id == TaxonCode.taxon_id)
        .outerjoin(
            SpeciesRangeFetch,
            SpeciesRangeFetch.taxon_id == TaxonCode.taxon_id,
        )
        .where(
            TaxonCode.source == RANGE_CODE_SOURCE,
            Taxon.rank == "species",
            used,
            or_(
                SpeciesRangeFetch.taxon_id.is_(None),
                SpeciesRangeFetch.query_name != TaxonCode.code,
            ),
        )
        .order_by(TaxonCode.taxon_id),
    ).all()
    return [(row.taxon_id, row.code) for row in rows]


def store_range(
    session: OrmSession,
    taxon_id: int,
    query_name: str,
    polygons: Sequence[RangePolygon],
    *,
    now: datetime | None = None,
) -> None:
    """Replace a taxon's cached polygons with `polygons` and record the fetch. An empty list
    is recorded as `not_found`, so the species is not asked about again."""
    session.execute(delete(SpeciesRange).where(SpeciesRange.taxon_id == taxon_id))
    for polygon in polygons:
        session.execute(
            _INSERT_POLYGON,
            {
                "taxon_id": taxon_id,
                "presence": polygon.presence,
                "origin": polygon.origin,
                "wkt": polygon.wkt,
                "tolerance": _SIMPLIFY_DEGREES,
            },
        )
    session.merge(
        SpeciesRangeFetch(
            taxon_id=taxon_id,
            query_name=query_name,
            status=FETCH_FOUND if polygons else FETCH_NOT_FOUND,
            source_version=IUCN_RELEASE,
            fetched_at=now or datetime.now(UTC),
        ),
    )
    session.flush()


def fetch_pending(
    session: OrmSession,
    *,
    fetch: Callable[[str], list[RangePolygon]] | None = None,
) -> int:
    """Fetch and store every pending species, one at a time. Commits after each so a failure
    on a later species (the job then retries) keeps the earlier ones. Returns how many were
    stored."""
    # Looked up here, not bound as a default argument: a default would freeze the original
    # function object at definition time and make `fetch_range` impossible to replace for the
    # job-level tests, which cannot pass an argument through the task.
    fetcher = fetch if fetch is not None else fetch_range
    stored = 0
    for taxon_id, query_name in pending_taxa(session):
        store_range(session, taxon_id, query_name, fetcher(query_name))
        session.commit()
        stored += 1
    return stored


def range_status(
    session: OrmSession,
    taxon_id: int,
    recording: Recording,
    *,
    edge_km: float,
) -> RangeStatus:
    """Where `recording` lies relative to the taxon's cached IUCN range. Reads the cache only;
    returns a verdict and a distance, never a polygon (T&C §4). `recording` must be persisted."""
    if recording.geom is None:
        return RangeStatus(RangeKind.NO_DATA, reason=NoDataReason.NO_LOCATION)
    code = session.scalar(
        select(TaxonCode.code).where(
            TaxonCode.taxon_id == taxon_id,
            TaxonCode.source == RANGE_CODE_SOURCE,
        ),
    )
    if code is None:
        return RangeStatus(RangeKind.NO_DATA, reason=NoDataReason.NO_CODE)
    fetched = session.get(SpeciesRangeFetch, taxon_id)
    if fetched is None or fetched.query_name != code:
        return RangeStatus(RangeKind.NO_DATA, reason=NoDataReason.NOT_FETCHED)
    if fetched.status != FETCH_FOUND:
        return RangeStatus(RangeKind.NO_DATA, reason=NoDataReason.NOT_FOUND)

    distance = func.ST_Distance(SpeciesRange.geom, Recording.geom)
    native_m, any_m, in_vagrant = session.execute(
        select(
            func.min(distance).filter(SpeciesRange.origin != VAGRANT_ORIGIN),
            func.min(distance),
            func.bool_or(func.ST_Intersects(SpeciesRange.geom, Recording.geom)).filter(
                SpeciesRange.origin == VAGRANT_ORIGIN,
            ),
        )
        .select_from(SpeciesRange)
        # An explicit JOIN, not a second FROM element: SQLAlchemy's cartesian-product linter
        # warns about two FROMs the WHERE clause does not connect, and a warning is a defect.
        .join(Recording, Recording.id == recording.id)
        .where(SpeciesRange.taxon_id == taxon_id),
    ).one()

    if any_m is None:
        return RangeStatus(RangeKind.NO_DATA, reason=NoDataReason.NOT_FOUND)
    if native_m is not None and native_m == 0:
        return RangeStatus(RangeKind.INSIDE, distance_km=0.0)
    # Distance is measured to the regular (non-vagrant) range when there is one: a vagrant
    # polygon next to the point must not make a far-from-range record look close.
    distance_km = float(native_m if native_m is not None else any_m) / 1000.0
    if in_vagrant:
        return RangeStatus(RangeKind.NEAR, distance_km=distance_km, vagrant=True)
    kind = RangeKind.NEAR if distance_km <= edge_km else RangeKind.FAR
    return RangeStatus(kind, distance_km=distance_km)
