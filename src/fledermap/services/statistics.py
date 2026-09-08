# src/fledermap/services/statistics.py
"""Live-SQL-aggregation query layer for the /statistics dashboard pages
(docs/superpowers/specs/2026-09-05-fledermap-statistics-design.md). No cache,
no precomputation -- every function here re-reads the database on every call,
a deliberate choice for this project's self-hosted single-user/small-group
scale (see the spec's "Data layer" section). Sibling to map_query.py and
entities.py, kept separate: this module answers "what does the whole archive
look like," not "what does the map's active filter set mean" or "browse
everything.\""""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session as OrmSession

from fledermap.services.current_best import current_best_identification
from fledermap.store.models import Recording, Site

DEFAULT_TOP_N = 8


def _scoped_recordings(
    session: OrmSession,
    *,
    site_id: int | None = None,
) -> list[Recording]:
    """Every non-missing recording, optionally scoped to one site. Shared by
    every query function below -- same "recompute current-best in Python per
    recording" style entities.py's site_detail/species_detail already use,
    not a SQL-side aggregate (current_best_identification's precedence logic
    has no SQL equivalent)."""
    stmt = select(Recording).where(Recording.missing_since.is_(None))
    if site_id is not None:
        stmt = stmt.where(Recording.site_id == site_id)
    return list(session.scalars(stmt).all())


@dataclass(frozen=True)
class Totals:
    """Global fills every field; site-scoped fills only total_recordings;
    species-scoped fills total_recordings + total_sites. See `totals`'s
    docstring for why each scope leaves the others unset."""

    total_recordings: int
    total_species: int | None = None
    total_sites: int | None = None


def totals(
    session: OrmSession,
    *,
    site_id: int | None = None,
    taxon_id: int | None = None,
) -> Totals:
    """Stat-tile numbers. `total_recordings` always counts EVERY non-missing
    recording in scope, species or not -- the donut chart deliberately does
    NOT sum to this (spec's "Species-breakdown inclusion rules": noise/no_id/
    unidentified recordings have no species-breakdown slice, but they still
    count here). `taxon_id` scope uses the same membership rule as
    `recording_counts_by_site` (a multi-species recording counts if the
    filtered taxon is ANY of its current-best taxa, not the sole one)."""
    if taxon_id is not None:
        recordings = _scoped_recordings(session)
        matching = []
        site_ids: set[int] = set()
        for r in recordings:
            best = current_best_identification(r)
            if best is None or taxon_id not in best.taxon_ids:
                continue
            matching.append(r)
            if r.site_id is not None:
                site_ids.add(r.site_id)
        return Totals(total_recordings=len(matching), total_sites=len(site_ids))

    if site_id is not None:
        return Totals(
            total_recordings=len(_scoped_recordings(session, site_id=site_id))
        )

    recordings = _scoped_recordings(session)
    species_ids: set[int] = set()
    for r in recordings:
        best = current_best_identification(r)
        if best is not None:
            species_ids |= best.taxon_ids
    total_sites = session.scalar(select(func.count()).select_from(Site)) or 0
    return Totals(
        total_recordings=len(recordings),
        total_species=len(species_ids),
        total_sites=total_sites,
    )
