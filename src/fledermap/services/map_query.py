# src/fledermap/services/map_query.py
"""Filtered Recording/Site queries shared by both GeoJSON endpoints (design
spec section 8) -- one definition of what the active filters mean, not
duplicated per endpoint.

Filters on fields stored directly on `Recording`/`Site` (date range, session,
missing-file status, `source`) run in SQL. `bbox` and taxon/verdict filtering
run in Python after that SQL prefilter: `bbox` because comparing against a
decoded `(lon, lat)` is simpler than a PostGIS bbox operator at this project's
established scale (`services/ingest.py`'s `sweep_missing` docstring: "fine at
journal scale, tens to low thousands"); taxon/verdict because they must be
evaluated against each recording's CURRENT-BEST identification (design spec
P4-2), not "any non-superseded identification" -- computing that per
candidate is exactly what `current_best_identification` does, and pushing
that logic into SQL would duplicate it.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import IdSource, Verdict
from fledermap.services.current_best import current_best_identification
from fledermap.store.geo import decode_point
from fledermap.store.models import Identification, Recording, Site

# This project's own established "tens to low thousands" scale assumption
# (see module docstring) makes true server-side, zoom-aware clustering
# unnecessary -- Leaflet.markercluster already declutters client-side (design
# spec section 6/P4-7). Over the cap, callers report `truncated: True` rather
# than a partial-and-silent result.
MAX_FEATURES = 2000

BBox = tuple[float, float, float, float]  # (min_lon, min_lat, max_lon, max_lat)


def _within_bbox(point: tuple[float, float] | None, bbox: BBox) -> bool:
    if point is None:
        return False
    lon, lat = point
    min_lon, min_lat, max_lon, max_lat = bbox
    return min_lon <= lon <= max_lon and min_lat <= lat <= max_lat


def _passes_verdict_filter(
    best: Identification | None,
    verdict: Verdict | Literal["all"] | None,
) -> bool:
    """A recording with no non-superseded identification at all (`best is
    None`) is treated as equivalent to `Verdict.NO_ID` for this purpose --
    both mean "we don't know what this is," which is exactly what "hide noise
    by default" is protecting the map from (decision P4-9)."""
    if verdict == "all":
        return True
    effective = best.verdict if best is not None else Verdict.NO_ID
    if verdict is None:
        return effective == Verdict.SPECIES
    return effective == verdict


def filtered_recordings(
    session: OrmSession,
    *,
    bbox: BBox | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    taxon_id: int | None = None,
    verdict: Verdict | Literal["all"] | None = None,
    session_id: int | None = None,
    source: IdSource | None = None,
) -> Sequence[Recording]:
    stmt = select(Recording).where(Recording.missing_since.is_(None))
    if date_from is not None:
        stmt = stmt.where(Recording.recorded_at >= date_from)
    if date_to is not None:
        stmt = stmt.where(Recording.recorded_at <= date_to)
    if session_id is not None:
        stmt = stmt.where(Recording.session_id == session_id)
    if source is not None:
        stmt = stmt.where(
            Recording.identifications.any(
                (Identification.source == source)
                & (Identification.superseded_at.is_(None)),
            ),
        )

    recordings = list(session.scalars(stmt).all())

    if bbox is not None:
        recordings = [r for r in recordings if _within_bbox(decode_point(r.geom), bbox)]

    results = []
    for r in recordings:
        best = current_best_identification(r)
        if not _passes_verdict_filter(best, verdict):
            continue
        if taxon_id is not None and (best is None or best.taxon_id != taxon_id):
            continue
        results.append(r)
    return results


def filtered_sites(
    session: OrmSession,
    *,
    bbox: BBox | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> Sequence[Site]:
    stmt = select(Site)
    if date_from is not None:
        stmt = stmt.where(Site.last_at >= date_from)
    if date_to is not None:
        stmt = stmt.where(Site.first_at <= date_to)

    sites = list(session.scalars(stmt).all())

    if bbox is not None:
        sites = [s for s in sites if _within_bbox(decode_point(s.centroid), bbox)]
    return sites
