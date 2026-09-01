"""poiidx query and job-enqueue logic for site naming (design spec
2026-08-28-fledermap-poiidx-site-naming-design.md).

WARNING: the connection built here must never point at `poiidx_db` (the
owner's own, separate, pre-existing POI index) or `bats_db` (Fledermap's own
real storage -- see the warning at the top of `store/db.py`). It must point
at `poiidx_bats_db`, a third, dedicated database this integration owns
exclusively. poiidx hashes its own schema and filter config on `init()` and
DROPS AND RECREATES ALL TABLES on any mismatch -- same hazard, different
database.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from importlib.resources import files
from typing import Any
from urllib.parse import urlsplit

import poiidx
import procrastinate
import yaml
from shapely.geometry import Point
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session as OrmSession

from fledermap.jobs.tasks import (
    _NAME_SITE_LOCK,
    name_site_queueing_lock,
    name_site_task,
)
from fledermap.jobs.tasks import app as jobs_app
from fledermap.store.geo import decode_point
from fledermap.store.models import Site, SiteNameCache
from fledermap.util.projection import LocalProjection

_FILTER_CONFIG_FILE = "poiidx_filter_config.yaml"


def _poiidx_connection_kwargs(database_url: str) -> dict[str, Any]:
    """Parse FLEDERMAP_POIIDX_DATABASE_URL into poiidx.init()'s discrete
    connect kwargs. poiidx.connect() forwards straight to peewee's
    PostgresqlDatabase.init(), which takes host/port/user/password/database
    separately -- confirmed against poiidx's own README and example.py, not
    a single connection-string argument.

    Error messages never include the parsed URL itself (final review,
    2026-08-28): it may carry a password, and this function's errors
    propagate into name_site_task, where Procrastinate logs the full
    traceback on every retry. Every other config error in this codebase
    reports the env-var label, not the value -- this matches that."""
    parsed = urlsplit(database_url)
    database = parsed.path.lstrip("/")
    missing = [
        label
        for label, value in (
            ("scheme", parsed.scheme in ("postgresql", "postgres")),
            ("host", parsed.hostname),
            ("user", parsed.username),
            ("password", parsed.password),
            ("database name", database),
        )
        if not value
    ]
    if missing:
        msg = (
            "FLEDERMAP_POIIDX_DATABASE_URL must be a "
            "postgresql://user:password@host:port/database URL "
            f"(missing: {', '.join(missing)})."
        )
        raise ValueError(msg)
    try:
        port = parsed.port or 5432
    except ValueError as exc:
        msg = "FLEDERMAP_POIIDX_DATABASE_URL's port must be a number."
        raise ValueError(msg) from exc
    return {
        "host": parsed.hostname,
        "port": port,
        "user": parsed.username,
        "password": parsed.password,
        "database": database,
    }


def _load_filter_config() -> list[dict[str, Any]]:
    """Fledermap's own toponymy-focused filter config (design spec §2), NOT
    poiidx's shipped default -- must stay byte-identical across every
    poiidx.init() call, or poiidx drops and recreates every table in
    poiidx_bats_db."""
    raw = (
        files("fledermap.services.data")
        .joinpath(_FILTER_CONFIG_FILE)
        .read_text(encoding="utf-8")
    )
    return yaml.safe_load(raw)


_connected = False


def ensure_connected(poiidx_database_url: str) -> None:
    """Idempotent within a process. poiidx.init() re-hashes the filter config
    against what's already stored on every call -- harmless to call more
    than once with the SAME config, but there's no reason to pay that
    comparison on every single job when a module-level flag can skip it
    after the first call in this process. Never reset except by tests."""
    global _connected
    if _connected:
        return
    poiidx.init(
        filter_config=_load_filter_config(),
        **_poiidx_connection_kwargs(poiidx_database_url),
    )
    _connected = True


_CANDIDATE_LIMIT = 5

# poiidx's own valid rank band (poiidx/osm.py's MIN_RANK/MAX_RANK) -- not
# imported, since osm.py isn't part of poiidx's public API (poiidx/__init__.py
# never re-exports it). Reimplemented here deliberately small.
_MIN_RANK = 13
_MAX_RANK = 23

# Above this rank, a candidate's own geometry is specific enough that
# "is it actually near the site" is a meaningful question -- below it
# (city/town/suburb-scale), the search radius already answered that.
_INTERSECTS_RANK_THRESHOLD = 19

# Padding on the site's own extent before testing intersects, to absorb
# GPS/OSM-digitization noise. Sized against real field data (2026-09-01): the
# closest genuine miss found was 54m, and an already-good match at 3m outside
# must NOT be demoted -- 15m clears the second without threatening the first.
_INTERSECTS_MARGIN_M = 15.0


def _target_rank(site_radius_m: float) -> int:
    """The rank a POI covering an area this size would get from poiidx's own
    scanner (poiidx/osm.py's calculate_rank(radius=...), mirrored rather than
    imported -- see _MIN_RANK above). Used backwards here: not to rank a POI,
    but to ask what specificity of name fits a site this size.

    Clipped to _MAX_RANK at the top, same as the source formula. Deliberately
    diverges from it at the bottom: poiidx's calculate_rank returns None
    below _MIN_RANK (its way of saying "too coarse to be a POI's rank at
    all"), but this function always needs a concrete target to compare
    candidates against, so it clips to _MIN_RANK instead (corrected wording
    2026-09-01 -- this function was never a literal mirror of the source
    formula's contract, only of its band).
    """
    if site_radius_m <= 0:
        return _MAX_RANK
    raw = math.ceil(20 - math.log2(site_radius_m / 1000))
    return max(_MIN_RANK, min(_MAX_RANK, raw))


def _select_best_poi(
    pois: list[dict[str, Any]],
    *,
    site_point: Point,
    site_radius_m: float,
) -> dict[str, Any]:
    """Closest-to-target-rank wins, not merely the most specific or the least
    specific candidate in range (generalises design spec SN-7, corrected
    2026-09-01 -- see that section for the two failure modes a plain min/max
    each produced). Ties keep poiidx's own nearest-first ordering, since
    Python's min() is stable.

    For any candidate specific enough that its own geometry means something
    (rank > _INTERSECTS_RANK_THRESHOLD), one proven to sit outside the site's
    own extent (padded by _INTERSECTS_MARGIN_M) is sorted behind every
    candidate that isn't -- demoted, not discarded, so a site surrounded only
    by imperfect matches still gets the best of them rather than nothing.
    """
    target = _target_rank(site_radius_m)
    projection = None
    site_circle = None
    if any(poi["rank"] > _INTERSECTS_RANK_THRESHOLD for poi in pois):
        projection = LocalProjection(site_point)
        site_circle = projection.to_local(site_point).buffer(
            site_radius_m + _INTERSECTS_MARGIN_M,
        )

    def sort_key(poi: dict[str, Any]) -> tuple[int, int]:
        outside_penalty = 0
        if poi["rank"] > _INTERSECTS_RANK_THRESHOLD:
            geometry = poi.get("coordinates")
            if geometry is None:
                # Can't verify a candidate specific enough to need verifying
                # -- treat it the same as one proven outside, rather than
                # crash the whole resolution on one malformed/edge-case
                # poiidx row. `Poi.coordinates` is schema-NOT-NULL in poiidx
                # today; nothing here should depend on that holding forever.
                outside_penalty = 1
            else:
                assert projection is not None and site_circle is not None
                local_geom = projection.to_local(geometry)
                outside_penalty = 0 if site_circle.intersects(local_geom) else 1
        return (outside_penalty, abs(poi["rank"] - target))

    return min(pois, key=sort_key)


# Bucket width for _cache_key's radius component. An earlier version of this
# (2026-09-01) bucketed by _target_rank(site_radius_m) instead -- wrong,
# caught by code review the same day: _target_rank clips to _MAX_RANK for
# essentially every radius under ~250m, so two real sites of very different
# scale (a 10m stationary site and a 200m one, say) collided into the exact
# same bucket despite _select_best_poi's intersects margin genuinely
# depending on the raw, un-bucketed radius. Bucketing site_radius_m directly
# tracks the continuous quantity that actually matters.
_CACHE_RADIUS_BUCKET_M = 10.0
# A multiple of the bucket width, so rounding can never push the result past
# 5 digits -- keeps the worst-case key length within SiteNameCache.geohash's
# String(24) at any realistic site size (bat survey areas, not continents).
_CACHE_RADIUS_BUCKET_MAX_M = 99_990.0


def _radius_bucket(site_radius_m: float) -> int:
    clamped = min(max(site_radius_m, 0.0), _CACHE_RADIUS_BUCKET_MAX_M)
    return int(round(clamped / _CACHE_RADIUS_BUCKET_M) * _CACHE_RADIUS_BUCKET_M)


def _cache_key(lon: float, lat: float, site_radius_m: float) -> str:
    """Rounded-coordinate cache key, plus a radius bucket.
    `SiteNameCache.geohash`'s own docstring (and the parent design spec) says
    "keyed on rounded coordinates" -- not the standard geohash algorithm,
    despite the column's name. 3 decimal degrees is roughly 111m at the
    equator, the same ballpark as site_eps_m's default clustering radius:
    coarse enough that a site's recomputed centroid landing a few metres away
    on a later derive_sites rebuild still hits the same cache entry, fine
    enough not to conflate two genuinely different nearby sites.

    The `_radius_bucket(site_radius_m)` suffix (added 2026-09-01, alongside
    SN-7's fix; bucketing scheme corrected same day, see _CACHE_RADIUS_BUCKET_M)
    exists because the resolved name now depends on the querying site's own
    radius, not just its coordinate: two Sites landing in the same
    rounded-coordinate bucket but at very different scales (a small
    stationary site and a large transect-derived one, say) must not silently
    share one cached resolution -- `SiteNameCache.geohash` is unique, so
    whichever resolved first would otherwise permanently win the slot for
    both. The bucket width (10m) is itself an approximation, same spirit as
    the coordinate rounding above -- it substantially narrows the window
    where two genuinely different-scale sites could still collide (a pair
    like 40m/60m no longer share a bucket the way they would have at a
    coarser width), but does NOT eliminate it: _select_best_poi's intersects
    margin is sensitive to the exact, un-bucketed site_radius_m, so no finite
    bucket width can rule out every collision without keying on the exact
    radius and defeating caching's whole point (reusing a resolution across
    ordinary derive_sites recompute noise). A worse-than-ideal cached name
    for a rare colliding pair is the accepted cost (code review finding,
    2026-09-01 -- corrected from an earlier version of this docstring that
    overclaimed the bucket eliminated conflation outright)."""
    return f"{lat:.3f},{lon:.3f},{_radius_bucket(site_radius_m)}"


def name_site(
    db_session: OrmSession,
    lon: float,
    lat: float,
    *,
    radius_m: float,
    site_radius_m: float,
) -> tuple[str, str | None] | None:
    """Resolve (name, admin_path) for a coordinate, cache-first through
    SiteNameCache. Returns None if poiidx could not resolve anything at all
    (no nearby POI AND no administrative hierarchy) -- the caller leaves
    Site.name as NULL so the existing coordinate fallback still applies.
    Deliberately NOT cached in that case, unlike a real resolution, so a
    later run can retry once poiidx's underlying region data improves.

    `radius_m` is the configured search-radius default; `site_radius_m` is
    the Site's own true extent (its DBSCAN radius). The wider of the two is
    searched, so a site bigger than the default is never searched too small
    for its own footprint -- see _select_best_poi for how site_radius_m also
    drives which candidate wins.

    Caller must have already called `ensure_connected` this process --
    this function never calls poiidx.init() itself."""
    key = _cache_key(lon, lat, site_radius_m)
    cached = db_session.scalar(
        select(SiteNameCache).where(SiteNameCache.geohash == key),
    )
    if cached is not None:
        return cached.name, cached.admin_path

    point = Point(lon, lat)
    search_radius_m = max(radius_m, site_radius_m)
    # `buffer=` is deliberately NEVER passed to either poiidx call below.
    # It should be (it drives poiidx's own region-loading via
    # init_regions_by_shape, a separate mechanism from max_distance's
    # candidate filtering -- without it a widened search near a poiidx
    # region boundary stays silently confined to the origin point's single
    # region), but the installed poiidx==0.0.9 crashes unconditionally
    # whenever buffer is not None: PoiIdx.init_regions_by_shape does
    # `local_shape.convex_hull().buffer(buffer)`, and shapely's convex_hull
    # is a property, not a method -- calling it with () invokes whatever
    # geometry it returns as if THAT were callable, raising
    # "TypeError: 'Point' object is not callable" on every call, for any
    # buffer value. Confirmed 2026-09-01 against the real package, not
    # mocked: an earlier version of this code passed buffer and broke every
    # name_site_task run in production the moment it merged. Tracked as a
    # poiidx bug in docs/references.md -- revisit once fixed upstream.
    pois = poiidx.get_nearest_pois(
        point,
        max_distance=search_radius_m,
        limit=_CANDIDATE_LIMIT,
    )
    admin_path = poiidx.get_administrative_hierarchy_string(point) or None

    name: str | None
    if pois:
        best = _select_best_poi(
            pois,
            site_point=point,
            site_radius_m=site_radius_m,
        )
        name = best["name"] or None
    else:
        name = admin_path

    if name is None:
        return None

    db_session.add(
        SiteNameCache(
            geohash=key,
            name=name,
            admin_path=admin_path,
            fetched_at=datetime.now(UTC),
        ),
    )
    return name, admin_path


def enqueue_site_naming(
    db_session: OrmSession,
    engine: Engine,
    *,
    poiidx_database_url: str | None,
) -> int:
    """Cache-first resolution for every Site still missing a name. Called
    right after derive_sites()+commit() -- its wholesale rebuild resets
    every site's name to NULL on every run (design spec §4) -- and from the
    `backfill-site-names` CLI command. Returns the number of name_site jobs
    actually deferred; a SiteNameCache hit is resolved directly onto the
    Site row instead and does not count.

    A true no-op when poiidx isn't configured at all -- the "optional
    integration, current behaviour preserved" goal (design spec Goals)."""
    if not poiidx_database_url:
        return 0

    try:
        jobs_app.open(engine)
    except NotImplementedError:
        pass  # already open inside a running worker -- see enqueue_media's docstring

    unnamed = db_session.scalars(select(Site).where(Site.name.is_(None))).all()

    enqueued = 0
    for site in unnamed:
        point = decode_point(site.centroid)
        if point is None:
            continue
        lon, lat = point
        key = _cache_key(lon, lat, site.radius_m)
        cached = db_session.scalar(
            select(SiteNameCache).where(SiteNameCache.geohash == key),
        )
        if cached is not None:
            site.name = cached.name
            site.admin_path = cached.admin_path
            continue
        try:
            name_site_task.configure(
                lock=_NAME_SITE_LOCK,
                queueing_lock=name_site_queueing_lock(key),
            ).defer(site_id=site.id)
        except procrastinate.exceptions.AlreadyEnqueued:
            continue
        enqueued += 1
    return enqueued
