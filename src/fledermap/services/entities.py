# src/fledermap/services/entities.py
"""Species/Site list + detail pages' query layer. Sibling to map_query.py but
deliberately separate from it: this module answers "browse everything," not
"what does the map's active filter set mean" -- and per CLAUDE.md's
Drawer/detail-page feature-parity note, `species_detail`/`site_detail` are
kept independently editable from their drawer-panel counterparts on purpose,
even where they render the same content today."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from fledermap.services.current_best import current_best_identification
from fledermap.store.models import Recording, Site, Taxon, TaxonCode

# How many of a species' most recent recordings to show on its detail page --
# a bounded preview, not a full paginated list (no such UI exists for
# recordings yet -- see the backlog's separate "Recordings list page" item).
RECENT_RECORDINGS_LIMIT = 20


def _current_best_counts_by_taxon(session: OrmSession) -> dict[int, int]:
    """Tally every non-missing recording's current-best taxon_ids, the same
    membership rule site_detail's own tally already uses (a multi-species
    recording increments every taxon it contains, not just one)."""
    recordings = session.scalars(
        select(Recording).where(Recording.missing_since.is_(None)),
    ).all()
    counts: dict[int, int] = {}
    for recording in recordings:
        best = current_best_identification(recording)
        if best is not None:
            for taxon_id in best.taxon_ids:
                counts[taxon_id] = counts.get(taxon_id, 0) + 1
    return counts


@dataclass(frozen=True)
class SpeciesListRow:
    """One row of the species list page."""

    taxon: Taxon
    code: str | None
    recording_count: int


def list_species(session: OrmSession) -> Sequence[SpeciesListRow]:
    """Every taxon with at least one current-best recording, ordered
    alphabetically -- same "must have been detected" restriction list_taxa
    already applies to the map's taxon filter dropdown, for the same reason:
    taxa_eu.yaml/taxa_na.yaml carry many entries with no matching detection
    yet, and listing all of them here would say nothing interesting."""
    counts = _current_best_counts_by_taxon(session)
    if not counts:
        return []

    taxa = session.scalars(
        select(Taxon).where(Taxon.id.in_(counts)).order_by(Taxon.scientific_name),
    ).all()

    codes_by_taxon: dict[int, str] = {}
    for code in session.scalars(
        select(TaxonCode).where(TaxonCode.taxon_id.in_(counts)),
    ):
        codes_by_taxon.setdefault(code.taxon_id, code.code)

    return [
        SpeciesListRow(
            taxon=taxon,
            code=codes_by_taxon.get(taxon.id),
            recording_count=counts[taxon.id],
        )
        for taxon in taxa
    ]


@dataclass(frozen=True)
class SpeciesDetail:
    """Everything the species detail page needs, assembled in one query pass."""

    taxon: Taxon
    codes: list[str]
    sites: list[tuple[Site, int]]
    recent_recordings: Sequence[Recording]


def species_detail(session: OrmSession, taxon_id: int) -> SpeciesDetail | None:
    """Assemble species detail: the taxon, its per-source codes, the sites
    it's been recorded at (by current-best membership, same rule as
    `list_species`'s counts and the statistics spec's
    `recording_counts_by_site`), and its most recent recordings."""
    taxon = session.get(Taxon, taxon_id)
    if taxon is None:
        return None

    codes = [
        c.code
        for c in session.scalars(
            select(TaxonCode).where(TaxonCode.taxon_id == taxon_id),
        )
    ]

    recordings = session.scalars(
        select(Recording).where(Recording.missing_since.is_(None)),
    ).all()

    matching: list[Recording] = []
    site_counts: dict[int, int] = {}
    for recording in recordings:
        best = current_best_identification(recording)
        if best is None or taxon_id not in best.taxon_ids:
            continue
        matching.append(recording)
        if recording.site_id is not None:
            site_counts[recording.site_id] = site_counts.get(recording.site_id, 0) + 1

    sites_by_id = {}
    if site_counts:
        sites_by_id = {
            s.id: s
            for s in session.scalars(
                select(Site).where(Site.id.in_(site_counts)),
            )
        }
    sites = sorted(
        (
            (sites_by_id[site_id], count)
            for site_id, count in site_counts.items()
            if site_id in sites_by_id
        ),
        key=lambda pair: pair[1],
        reverse=True,
    )

    matching.sort(key=lambda r: r.recorded_at, reverse=True)

    return SpeciesDetail(
        taxon=taxon,
        codes=codes,
        sites=sites,
        recent_recordings=matching[:RECENT_RECORDINGS_LIMIT],
    )


def list_sites(session: OrmSession) -> Sequence[Site]:
    """Every derived site, most recently active first -- same convention
    list_sessions already uses (filtering/browsing is almost always about
    recent fieldwork, not the oldest deployment on record)."""
    stmt = select(Site).order_by(Site.last_at.desc())
    return list(session.scalars(stmt).all())
