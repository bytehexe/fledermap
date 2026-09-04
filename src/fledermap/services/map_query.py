# src/fledermap/services/map_query.py
"""Filtered Recording/Site queries shared by both GeoJSON endpoints (design
spec section 8) -- one definition of what the active filters mean, not
duplicated per endpoint.

Filters on fields stored directly on `Recording`/`Site` (date range, session,
missing-file status, `source`, `favourite`) run in SQL. `bbox` and taxon/verdict filtering
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
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import IdSource, Verdict
from fledermap.services.current_best import current_best_identification
from fledermap.store.geo import decode_point
from fledermap.store.models import Identification, Recording, Site, Taxon
from fledermap.store.models import Session as AnnotationSession

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
    taxon_id: int | Literal["unmapped"] | None = None,
    taxon_exclude: bool = False,
    verdict: Verdict | Literal["all"] | None = None,
    session_id: int | None = None,
    site_id: int | None = None,
    source: IdSource | None = None,
    favourite_only: bool = False,
) -> Sequence[Recording]:
    stmt = select(Recording).where(Recording.missing_since.is_(None))
    if date_from is not None:
        stmt = stmt.where(Recording.recorded_at >= date_from)
    if date_to is not None:
        stmt = stmt.where(Recording.recorded_at <= date_to)
    if session_id is not None:
        stmt = stmt.where(Recording.session_id == session_id)
    if site_id is not None:
        stmt = stmt.where(Recording.site_id == site_id)
    if favourite_only:
        stmt = stmt.where(Recording.favourite.is_(True))
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
        if taxon_id is not None:
            # `taxon_exclude` inverts the match rather than negating the
            # whole filter -- a recording with no taxon at all (no
            # identification, NoID, Noise, or an unmapped species) still
            # isn't taxon_id, so it's included under "not X" the same way
            # it's excluded under plain "X".
            if taxon_id == "unmapped":
                # A single bucket for every SPECIES verdict whose code never
                # mapped to a Taxon (see recording_headline in
                # services/current_best.py), rather than filtering by the
                # specific raw code -- see map_query.py's has_unmapped_species
                # docstring for why.
                matches = (
                    best is not None
                    and best.taxon_id is None
                    and best.verdict == Verdict.SPECIES
                )
            else:
                matches = best is not None and best.taxon_id == taxon_id
            if matches == taxon_exclude:
                continue
        results.append(r)
    return results


def neighbor_recordings(
    recordings: Sequence[Recording],
    audio_hash: str,
) -> tuple[Recording | None, Recording | None] | None:
    """The (previous, next) recording relative to `audio_hash` within
    `recordings`, ordered by `recorded_at` -- `recordings` must already be
    the filtered set the drawer is showing (design spec P5a-9: same filters
    as the map, minus bbox). Either side is `None` at a boundary (no
    wrap-around, P5a-10). Returns `None` entirely if `audio_hash` isn't in
    `recordings` at all -- e.g. the filters changed while the drawer was
    open and this recording no longer matches; the caller treats that the
    same as 'not found'."""
    ordered = sorted(recordings, key=lambda r: r.recorded_at)
    index = next(
        (i for i, r in enumerate(ordered) if r.audio_hash == audio_hash),
        None,
    )
    if index is None:
        return None
    previous = ordered[index - 1] if index > 0 else None
    following = ordered[index + 1] if index < len(ordered) - 1 else None
    return previous, following


def filtered_sites(
    session: OrmSession,
    *,
    bbox: BBox | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    site_id: int | None = None,
) -> Sequence[Site]:
    stmt = select(Site)
    if date_from is not None:
        stmt = stmt.where(Site.last_at >= date_from)
    if date_to is not None:
        stmt = stmt.where(Site.first_at <= date_to)
    if site_id is not None:
        stmt = stmt.where(Site.id == site_id)

    sites = list(session.scalars(stmt).all())

    if bbox is not None:
        sites = [s for s in sites if _within_bbox(decode_point(s.centroid), bbox)]
    return sites


def list_taxa(session: OrmSession) -> Sequence[Taxon]:
    """Taxa actually found in this archive, for the map's taxon filter dropdown
    (a numeric ID input is not something a person can use -- feedback on the
    first UI pass). Restricted to taxa referenced by at least one
    non-superseded Identification -- taxa_eu.yaml/the species list carry many
    entries with no matching detection yet (CLAUDE.md's "Species codes"
    section), and an option that can never match anything just clutters the
    dropdown. Ordered by scientific_name so the dropdown reads alphabetically
    regardless of insertion order. Not scoped to any rank: a group- or
    genus-level taxon is as valid an identification, and so as valid a
    filter target, as a species (docs/references.md)."""
    stmt = (
        select(Taxon)
        .where(
            Taxon.id.in_(
                select(Identification.taxon_id).where(
                    Identification.taxon_id.is_not(None),
                    Identification.superseded_at.is_(None),
                ),
            ),
        )
        .order_by(Taxon.scientific_name)
    )
    return list(session.scalars(stmt).all())


def has_unmapped_species(session: OrmSession) -> bool:
    """Whether the map's taxon filter dropdown needs its "Unmapped
    species" option -- a single bucket for every SPECIES verdict whose raw
    code never mapped to a Taxon (`recording_headline` in
    `services/current_best.py` shows these as "<CODE> (unmapped
    species)"). One bucket, not a per-code filter: the actual use case is
    "show me everything the classifier saw that we don't recognise yet," not
    usually one specific unmapped code (2026-09-04 design discussion) --
    the per-code filter remains a separate, deferred feature."""
    stmt = select(Identification.id).where(
        Identification.taxon_id.is_(None),
        Identification.verdict == Verdict.SPECIES,
        Identification.superseded_at.is_(None),
    )
    return session.scalars(stmt.limit(1)).first() is not None


def list_sessions(session: OrmSession) -> Sequence[AnnotationSession]:
    """Every session, for the map's session filter dropdown. Unlike Taxon,
    a session has no natural name -- the dropdown falls back to labelling by
    date range and detector (design spec section 7's `detector_key`) -- and
    no cap on how many can accumulate over time, unlike Taxon's small, fixed
    set. Most recent first: filtering by session is almost always about
    recent fieldwork, not the oldest deployment on record. Revisit with a
    search/pagination UI if this list grows large enough that "most recent
    first" stops being enough on its own."""
    stmt = select(AnnotationSession).order_by(AnnotationSession.started_at.desc())
    return list(session.scalars(stmt).all())


@dataclass(frozen=True)
class SiteDetail:
    """Everything the site drawer panel needs, assembled in one query pass
    rather than the template making N+1 lookups."""

    site: Site
    species_counts: list[tuple[Taxon, int]]
    sessions: Sequence[AnnotationSession]


def site_detail(session: OrmSession, site_id: int) -> SiteDetail | None:
    """Assemble site detail for the drawer panel: the site, its species
    breakdown (sorted by count descending), and the sessions that touched it."""
    site = session.get(Site, site_id)
    if site is None:
        return None

    recordings = session.scalars(
        select(Recording).where(
            Recording.site_id == site_id,
            Recording.missing_since.is_(None),
        ),
    ).all()

    counts: dict[int, int] = {}
    for recording in recordings:
        best = current_best_identification(recording)
        if best is not None and best.taxon_id is not None:
            counts[best.taxon_id] = counts.get(best.taxon_id, 0) + 1

    taxa_by_id = {}
    if counts:
        taxa_by_id = {
            t.id: t
            for t in session.scalars(
                select(Taxon).where(Taxon.id.in_(counts)),
            ).all()
        }
    species_counts = sorted(
        ((taxa_by_id[tid], n) for tid, n in counts.items()),
        key=lambda pair: -pair[1],
    )

    session_ids = {r.session_id for r in recordings if r.session_id is not None}
    sessions: Sequence[AnnotationSession] = []
    if session_ids:
        sessions = session.scalars(
            select(AnnotationSession)
            .where(AnnotationSession.id.in_(session_ids))
            .order_by(AnnotationSession.started_at.desc()),
        ).all()

    return SiteDetail(site=site, species_counts=species_counts, sessions=sessions)
