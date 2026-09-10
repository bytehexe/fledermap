"""Computed "needs review" criteria for a recording's assigned species
(docs/superpowers/specs/2026-09-09-fledermap-flagged-for-review-design.md).
Nothing here is stored -- every reason is recomputed live and cleared only
by fixing the underlying data (mapping a species code, adding a manual
classification), the same philosophy the unmapped-species review queue
already uses. See services/manual_classification.py's current_manual_state
and services/current_best.py's current_best_identification, which this
module builds on rather than duplicates."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from fledermap.services.current_best import current_best_identification
from fledermap.store.models import Recording

# Fixed, explainable thresholds (spec: "fixed low count dataset-wide" over a
# percentile -- doesn't shift as more data comes in, and stays simple to
# reason about at this project's self-hosted scale).
SITE_RARITY_MAX = 2
DATASET_RARITY_MAX = 5


def _taxon_counts(
    session: OrmSession,
) -> tuple[dict[int, int], dict[tuple[int, int], int]]:
    """(dataset-wide taxon_id -> count, (site_id, taxon_id) -> count), tallied
    from every non-missing recording's CURRENT-BEST taxon set -- same "walk
    every recording, recompute current-best in Python" style as
    services/statistics.py's totals/richness functions, since there's no SQL
    equivalent of current_best_identification's precedence walk. A
    multi-species MANUAL result contributes to every one of its taxa, not
    just one."""
    recordings = session.scalars(
        select(Recording).where(Recording.missing_since.is_(None)),
    ).all()
    dataset_counts: dict[int, int] = {}
    site_counts: dict[tuple[int, int], int] = {}
    for r in recordings:
        best = current_best_identification(r)
        if best is None:
            continue
        for taxon_id in best.taxon_ids:
            dataset_counts[taxon_id] = dataset_counts.get(taxon_id, 0) + 1
            if r.site_id is not None:
                key = (r.site_id, taxon_id)
                site_counts[key] = site_counts.get(key, 0) + 1
    return dataset_counts, site_counts


def _rarity_reason(
    dataset_counts: dict[int, int],
    site_counts: dict[tuple[int, int], int],
    site_id: int | None,
    taxon_id: int,
    taxon_label: str,
) -> str | None:
    """Either threshold is enough to flag -- a species can be locally rare at
    one site while common dataset-wide (or vice versa for a site with very
    little coverage), and both are independently interesting to a
    reviewer."""
    if site_id is not None:
        site_count = site_counts.get((site_id, taxon_id), 0)
        if site_count <= SITE_RARITY_MAX:
            plural = "" if site_count == 1 else "s"
            return f"{taxon_label}: rare ({site_count} recording{plural} at this site)"
    dataset_count = dataset_counts.get(taxon_id, 0)
    if dataset_count <= DATASET_RARITY_MAX:
        plural = "" if dataset_count == 1 else "s"
        return f"{taxon_label}: rare ({dataset_count} recording{plural} dataset-wide)"
    return None
