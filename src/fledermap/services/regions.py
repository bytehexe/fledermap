"""poiidx region membership for the map's Region filter (design spec
2026-10-04-fledermap-region-filter-design.md).

Everything here runs in the worker, off the request path. poiidx's
`list_regions` only reads its region index (no download/import), so no
serialization lock like site naming's is needed.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import poiidx
import procrastinate
from shapely.geometry import MultiPoint, Point
from sqlalchemy import delete, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session as OrmSession

from fledermap.jobs.tasks import _RESOLVE_REGIONS_LOCK, resolve_regions_task
from fledermap.jobs.tasks import app as jobs_app
from fledermap.store.geo import decode_point
from fledermap.store.models import LocationRegion, Recording, RecordingRegion, Region


def location_key(lon: float, lat: float) -> str:
    """3 decimal degrees (~111 m): coarse enough that many recordings at one
    spot share one poiidx call, fine against region borders, which are
    kilometres wide in practice. Same rounding as `SiteNameCache`'s key."""
    return f"{lat:.3f},{lon:.3f}"


def _upsert_region(session: OrmSession, region_id: str, name: str) -> None:
    existing = session.get(Region, region_id)
    if existing is None:
        session.add(Region(id=region_id, name=name, is_distinct=False))
    elif existing.name != name:
        existing.name = name


def resolve_pending(session: OrmSession) -> int:
    """Resolve every live, located recording not yet resolved. Returns how
    many recordings were resolved."""
    pending = session.scalars(
        select(Recording).where(
            Recording.region_resolved_at.is_(None),
            Recording.missing_since.is_(None),
            Recording.geom.is_not(None),
        ),
    ).all()

    by_key: dict[str, list[Recording]] = {}
    points: dict[str, tuple[float, float]] = {}
    for recording in pending:
        point = decode_point(recording.geom)
        if point is None:
            continue
        key = location_key(*point)
        by_key.setdefault(key, []).append(recording)
        points.setdefault(key, point)

    cached = {
        row.location_key: row
        for row in session.scalars(
            select(LocationRegion).where(LocationRegion.location_key.in_(by_key)),
        )
    }

    now = datetime.now(UTC)
    resolved = 0
    for key, recordings in by_key.items():
        row = cached.get(key)
        if row is None:
            lon, lat = points[key]
            found: list[dict[str, Any]] = poiidx.list_regions(Point(lon, lat))
            for info in found:
                _upsert_region(session, info["id"], info["name"])
            row = LocationRegion(
                location_key=key,
                region_ids=[info["id"] for info in found],
            )
            session.add(row)
            # Regions must exist before recording_region rows reference them.
            session.flush()
        for recording in recordings:
            for region_id in row.region_ids:
                session.add(
                    RecordingRegion(recording_id=recording.id, region_id=region_id),
                )
            recording.region_resolved_at = now
            resolved += 1
    session.flush()
    return resolved


def refresh_distinct_flags(session: OrmSession) -> None:
    """Recompute `Region.is_distinct` from poiidx's own rule over the current
    set of live recorded locations. Runs every ingest cycle, not only when
    something was resolved: recordings going missing change the answer too."""
    points: dict[str, tuple[float, float]] = {}
    for geom in session.scalars(
        select(Recording.geom).where(
            Recording.missing_since.is_(None),
            Recording.geom.is_not(None),
        ),
    ):
        point = decode_point(geom)
        if point is not None:
            points.setdefault(location_key(*point), point)

    distinct_ids: set[str] = set()
    if points:
        found: list[dict[str, Any]] = poiidx.list_regions(
            MultiPoint(list(points.values())),
        )
        distinct_ids = {info["id"] for info in found if info["distinct"]}

    session.execute(update(Region).values(is_distinct=Region.id.in_(distinct_ids)))
    session.flush()


def resolve_regions(session: OrmSession) -> int:
    resolved = resolve_pending(session)
    refresh_distinct_flags(session)
    return resolved


def reset_region_data(session: OrmSession) -> None:
    """Forget everything region-related so the next run re-asks poiidx. Order
    matters: recording_region references region."""
    session.execute(delete(RecordingRegion))
    session.execute(delete(LocationRegion))
    session.execute(delete(Region))
    session.execute(update(Recording).values(region_resolved_at=None))
    session.flush()


def list_distinct_regions(session: OrmSession) -> Sequence[Region]:
    return session.scalars(
        select(Region).where(Region.is_distinct.is_(True)).order_by(Region.name),
    ).all()


def enqueue_region_resolution(
    engine: Engine,
    *,
    poiidx_database_url: str | None,
) -> bool:
    """Defer one resolution job. Called every ingest cycle and from
    `backfill-regions`: the distinct-flag refresh inside the job must run
    even with nothing pending (recordings going missing change it), so there
    is deliberately no "anything pending?" shortcut. A true no-op when poiidx
    isn't configured. The queueing lock drops a duplicate while one is
    already waiting."""
    if not poiidx_database_url:
        return False
    try:
        jobs_app.open(engine)
    except NotImplementedError:
        pass  # already open inside a running worker -- see enqueue_media's docstring
    try:
        resolve_regions_task.configure(
            lock=_RESOLVE_REGIONS_LOCK,
            queueing_lock=_RESOLVE_REGIONS_LOCK,
        ).defer()
    except procrastinate.exceptions.AlreadyEnqueued:
        return False
    return True
