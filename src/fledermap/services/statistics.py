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

from fledermap.domain.codes import Verdict
from fledermap.services.current_best import current_best_identification
from fledermap.store.models import Identification, Recording, Site, Taxon

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


@dataclass(frozen=True)
class TaxonCount:
    taxon: Taxon
    count: int


@dataclass(frozen=True)
class TaxonBreakdown:
    """Species-composition donut data. `other_count`/`unmapped_count`/
    `multi_species_count` are each their own donut slice -- see the spec's
    "Species-breakdown inclusion rules" for why noise/no_id/unidentified
    recordings appear in none of them."""

    entries: list[TaxonCount]
    other_count: int
    unmapped_count: int
    multi_species_count: int


def recording_counts_by_taxon(
    session: OrmSession,
    *,
    site_id: int | None = None,
    top_n: int = DEFAULT_TOP_N,
) -> TaxonBreakdown:
    recordings = _scoped_recordings(session, site_id=site_id)
    counts: dict[int, int] = {}
    unmapped = 0
    multi = 0
    for r in recordings:
        best = current_best_identification(r)
        if best is None or best.verdict in (Verdict.NOISE, Verdict.NO_ID):
            continue
        if best.is_multi:
            multi += 1
            continue
        taxon_id = best.primary.taxon_id
        if taxon_id is None:
            unmapped += 1
            continue
        counts[taxon_id] = counts.get(taxon_id, 0) + 1

    ranked = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    top = ranked[:top_n]
    other_count = sum(c for _, c in ranked[top_n:])

    taxa_by_id: dict[int, Taxon] = {}
    if top:
        taxa_by_id = {
            t.id: t
            for t in session.scalars(
                select(Taxon).where(Taxon.id.in_([tid for tid, _ in top])),
            )
        }
    entries = [
        TaxonCount(taxon=taxa_by_id[tid], count=c)
        for tid, c in top
        if tid in taxa_by_id
    ]
    return TaxonBreakdown(
        entries=entries,
        other_count=other_count,
        unmapped_count=unmapped,
        multi_species_count=multi,
    )


def rarest_species(
    session: OrmSession, *, bottom_n: int = DEFAULT_TOP_N
) -> TaxonBreakdown:
    """Bottom-N current-best taxa by recording count, excluding taxa with
    zero recordings (a taxon never appears in `counts` unless something maps
    to it). Unlike `recording_counts_by_taxon`, a multi-species recording
    counts toward EVERY taxon it contains (see this function's own test for
    why), and unmapped-species results have no taxon to appear under at all
    -- both deliberate divergences documented in the spec."""
    recordings = _scoped_recordings(session)
    counts: dict[int, int] = {}
    for r in recordings:
        best = current_best_identification(r)
        if best is None or best.verdict in (Verdict.NOISE, Verdict.NO_ID):
            continue
        for taxon_id in best.taxon_ids:
            counts[taxon_id] = counts.get(taxon_id, 0) + 1

    ranked = sorted(counts.items(), key=lambda kv: kv[1])
    bottom = ranked[:bottom_n]
    taxa_by_id: dict[int, Taxon] = {}
    if bottom:
        taxa_by_id = {
            t.id: t
            for t in session.scalars(
                select(Taxon).where(Taxon.id.in_([tid for tid, _ in bottom])),
            )
        }
    entries = [
        TaxonCount(taxon=taxa_by_id[tid], count=c)
        for tid, c in bottom
        if tid in taxa_by_id
    ]
    return TaxonBreakdown(
        entries=entries,
        other_count=0,
        unmapped_count=0,
        multi_species_count=0,
    )


@dataclass(frozen=True)
class CodeCount:
    code: str
    count: int


@dataclass(frozen=True)
class CodeBreakdown:
    entries: list[CodeCount]


def rarest_unmapped_codes(
    session: OrmSession,
    *,
    bottom_n: int = DEFAULT_TOP_N,
) -> CodeBreakdown:
    """Bottom-N raw code strings among unmapped SPECIES-verdict claims,
    across every source's live claims (not just current-best) -- this is a
    review-queue-style surface ("which unmapped codes exist at all, and how
    rare are they"), not a per-recording current-best breakdown."""
    stmt = select(Identification.raw_label).where(
        Identification.taxon_id.is_(None),
        Identification.verdict == Verdict.SPECIES,
    )
    counts: dict[str, int] = {}
    for (raw_label,) in session.execute(stmt):
        label = raw_label or "(no code)"
        counts[label] = counts.get(label, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: kv[1])
    return CodeBreakdown(
        entries=[CodeCount(code=c, count=n) for c, n in ranked[:bottom_n]],
    )
