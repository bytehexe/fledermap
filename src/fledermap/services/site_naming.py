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


def _cache_key(lon: float, lat: float) -> str:
    """Rounded-coordinate cache key. `SiteNameCache.geohash`'s own docstring
    (and the parent design spec) says "keyed on rounded coordinates" -- not
    the standard geohash algorithm, despite the column's name. 3 decimal
    degrees is roughly 111m at the equator, the same ballpark as
    site_eps_m's default clustering radius: coarse enough that a site's
    recomputed centroid landing a few metres away on a later derive_sites
    rebuild still hits the same cache entry, fine enough not to conflate two
    genuinely different nearby sites. Fits SiteNameCache.geohash's
    String(16) column: "52.520,13.405" is 13 characters."""
    return f"{lat:.3f},{lon:.3f}"


def name_site(
    db_session: OrmSession,
    lon: float,
    lat: float,
    *,
    radius_m: float,
) -> tuple[str, str | None] | None:
    """Resolve (name, admin_path) for a coordinate, cache-first through
    SiteNameCache. Returns None if poiidx could not resolve anything at all
    (no nearby POI AND no administrative hierarchy) -- the caller leaves
    Site.name as NULL so the existing coordinate fallback still applies.
    Deliberately NOT cached in that case, unlike a real resolution, so a
    later run can retry once poiidx's underlying region data improves.

    Caller must have already called `ensure_connected` this process --
    this function never calls poiidx.init() itself."""
    key = _cache_key(lon, lat)
    cached = db_session.scalar(
        select(SiteNameCache).where(SiteNameCache.geohash == key),
    )
    if cached is not None:
        return cached.name, cached.admin_path

    point = Point(lon, lat)
    pois = poiidx.get_nearest_pois(point, max_distance=radius_m, limit=_CANDIDATE_LIMIT)
    admin_path = poiidx.get_administrative_hierarchy_string(point) or None

    name: str | None
    if pois:
        # Highest rank wins, not merely nearest (poiidx: lower rank = bigger)
        # This way the pois get more specific: Otherwise everything is just the big
        # city's name, no details
        best = max(pois, key=lambda poi: poi["rank"])
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
        key = _cache_key(lon, lat)
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
