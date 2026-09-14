"""Computed "needs review" criteria for a recording's assigned species
(docs/superpowers/specs/2026-09-09-fledermap-flagged-for-review-design.md).
Nothing here is stored -- every reason is recomputed live and cleared only
by fixing the underlying data (mapping a species code, adding a manual
classification), the same philosophy the unmapped-species review queue
already uses. See services/manual_classification.py's current_manual_state
and services/current_best.py's current_best_identification, which this
module builds on rather than duplicates."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import IdSource, Verdict
from fledermap.services.current_best import current_best_identification
from fledermap.services.manual_classification import current_manual_state
from fledermap.store.models import Recording

# Fixed, explainable thresholds (spec: "fixed low count dataset-wide" over a
# percentile -- doesn't shift as more data comes in, and stays simple to
# reason about at this project's self-hosted scale).
SITE_RARITY_MAX = 2
DATASET_RARITY_MAX = 5


def _taxon_counts(
    recordings: Sequence[Recording],
) -> tuple[dict[int, int], dict[tuple[int, int], int]]:
    """(dataset-wide taxon_id -> count, (site_id, taxon_id) -> count), tallied
    from every non-missing recording's CURRENT-BEST taxon set -- same "walk
    every recording, recompute current-best in Python" style as
    services/statistics.py's totals/richness functions, since there's no SQL
    equivalent of current_best_identification's precedence walk. A
    multi-species MANUAL result contributes to every one of its taxa, not
    just one.

    Takes the caller's already-fetched non-missing recordings rather than
    querying its own -- `ReviewContext.build` fetches once and shares it with
    `_misattribution_rates` (see that function's own docstring)."""
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
) -> str | None:
    """Either threshold is enough to flag -- a species can be locally rare at
    one site while common dataset-wide (or vice versa for a site with very
    little coverage), and both are independently interesting to a
    reviewer.

    Deliberately says "Rare species", not the species' own name: every
    caller already shows that name right next to this reason (the
    recording-detail page's own title, the Reviews table's Species column),
    so repeating it here read as redundant."""
    if site_id is not None:
        site_count = site_counts.get((site_id, taxon_id), 0)
        if site_count <= SITE_RARITY_MAX:
            plural = "" if site_count == 1 else "s"
            return f"Rare species ({site_count} recording{plural} at this site)"
    dataset_count = dataset_counts.get(taxon_id, 0)
    if dataset_count <= DATASET_RARITY_MAX:
        plural = "" if dataset_count == 1 else "s"
        return f"Rare species ({dataset_count} recording{plural} dataset-wide)"
    return None


# Minimum disagreement count before a source/taxon pair is ever flagged --
# avoids treating one correction as proof of a pattern.
MISATTRIBUTION_MIN_DISAGREEMENTS = 3


def _misattribution_rates(
    recordings: Sequence[Recording],
) -> dict[tuple[IdSource, int], tuple[int, int]]:
    """(source, taxon_id) -> (misattribution_count, total_counted) across
    every non-missing recording that has both a claim of that taxon from
    that source and SOME standing MANUAL verdict. Takes the caller's
    already-fetched non-missing recordings -- see `_taxon_counts`'s
    docstring for why.

    Per-recording accounting (spec's Goals section has the full table):
    - MANUAL SPECIES whose taxon set contains the classifier's taxon:
      correct (not counted as a disagreement) -- a classifier only ever
      names one species, so a broader human multi-species call still
      confirms it.
    - MANUAL SPECIES whose taxon set does NOT contain it, or MANUAL NOISE:
      counted as a disagreement.
    - MANUAL NO_ID: excluded entirely, from both numerator and denominator
      -- a human declining to call it isn't evidence the classifier was
      wrong.

    Deliberately no taxonomic-hierarchy awareness: a manual genus/group
    claim (e.g. Myotis/MYSP) and an automatic species-level claim underneath
    it (e.g. Myotis daubentonii) are compared by plain taxon_id equality,
    same as any other pair -- see the design spec's Non-goals section for
    why a Taxon.parent_id walk isn't worth it here.
    """
    totals: dict[tuple[IdSource, int], int] = {}
    disagreements: dict[tuple[IdSource, int], int] = {}
    for r in recordings:
        manual_claims = [i for i in r.identifications if i.source == IdSource.MANUAL]
        if not manual_claims:
            continue
        manual_verdict = manual_claims[0].verdict
        if manual_verdict == Verdict.NO_ID:
            continue
        manual_taxon_ids = frozenset(
            i.taxon_id for i in manual_claims if i.taxon_id is not None
        )
        for ident in r.identifications:
            if ident.source == IdSource.MANUAL or ident.taxon_id is None:
                continue
            key = (ident.source, ident.taxon_id)
            totals[key] = totals.get(key, 0) + 1
            is_correct = (
                manual_verdict == Verdict.SPECIES and ident.taxon_id in manual_taxon_ids
            )
            if not is_correct:
                disagreements[key] = disagreements.get(key, 0) + 1
    return {key: (disagreements.get(key, 0), total) for key, total in totals.items()}


def _misattribution_reason(
    rates: dict[tuple[IdSource, int], tuple[int, int]],
    source: IdSource,
    taxon_id: int,
) -> str | None:
    """Same "don't repeat the species name" call as `_rarity_reason` above --
    the species is already shown right next to this reason wherever it's
    rendered."""
    disagreements, total = rates.get((source, taxon_id), (0, 0))
    if disagreements >= MISATTRIBUTION_MIN_DISAGREEMENTS and disagreements * 2 > total:
        return f"{source.value} is often wrong about this species"
    return None


@dataclass(frozen=True)
class ReviewContext:
    """Aggregates shared across every `review_reasons` call in one request --
    computing these per-recording would be an N+1 query pattern across a
    whole filtered set (the Reviews page, the map's `needs_review` filter).
    Build once per request, pass into every `review_reasons` call."""

    dataset_counts: dict[int, int]
    site_counts: dict[tuple[int, int], int]
    misattribution_rates: dict[tuple[IdSource, int], tuple[int, int]]

    @classmethod
    def build(cls, session: OrmSession) -> ReviewContext:
        # Fetched once and shared with both helpers below -- they used to
        # each run their own identical `select(Recording)...` query, doubling
        # this request's DB round-trips for no reason.
        recordings = session.scalars(
            select(Recording).where(Recording.missing_since.is_(None)),
        ).all()
        dataset_counts, site_counts = _taxon_counts(recordings)
        return cls(
            dataset_counts=dataset_counts,
            site_counts=site_counts,
            misattribution_rates=_misattribution_rates(recordings),
        )


def review_reasons(recording: Recording, context: ReviewContext) -> list[str]:
    """Every computed reason this recording's assigned species should be
    reviewed -- empty if none apply. Scoped to a SPECIES verdict with no
    standing MANUAL claim (a human classifying it already counts as
    reviewed); see the module docstring and the design spec's Goals section
    for why nothing here is stored or dismissible."""
    best = current_best_identification(recording)
    if best is None or best.verdict != Verdict.SPECIES:
        return []
    manual_verdict, _manual_taxon_ids = current_manual_state(recording)
    if manual_verdict is not None:
        return []

    reasons: list[str] = []
    for claim in best.claims:
        if claim.taxon_id is None:
            continue
        rarity = _rarity_reason(
            context.dataset_counts,
            context.site_counts,
            recording.site_id,
            claim.taxon_id,
        )
        if rarity is not None:
            reasons.append(rarity)
        misattribution = _misattribution_reason(
            context.misattribution_rates,
            claim.source,
            claim.taxon_id,
        )
        if misattribution is not None:
            reasons.append(misattribution)
    return reasons


# Matches MAX_FEATURES's existing precedent elsewhere: degrade visibly (a
# "showing the first 500" note) rather than risk a request-line size some
# intermediary silently rejects. At this project's expected review-queue
# scale (tens, not thousands) this is a backstop, not a normal case.
MAX_REVIEW_SNAPSHOT = 500


def build_review_snapshot(recordings: Sequence[Recording]) -> list[int]:
    """Ordered Recording.id list for a review session's URL -- id, not
    audio_hash, specifically to keep the query string compact (a 64-char
    hash per entry would risk common ~8KB request-line limits well before
    MAX_REVIEW_SNAPSHOT is even reached)."""
    return [r.id for r in recordings[:MAX_REVIEW_SNAPSHOT]]


def parse_review_snapshot(raw: str | None) -> list[int]:
    """Parse a `review=<id>,<id>,...` query param into an ordered id list.
    A malformed entry is skipped, not raised -- a hand-edited or stale URL
    should degrade to "not a review session" (see resolve_review_snapshot
    and recording_detail.py), never a 500."""
    if not raw:
        return []
    ids: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.append(int(part))
        except ValueError:
            continue
    return ids


def resolve_review_snapshot(
    session: OrmSession,
    ids: Sequence[int],
) -> list[Recording]:
    """Resolve a snapshot's id list back to Recording rows, IN THE
    SNAPSHOT'S OWN ORDER -- not insertion or query order. Any id that no
    longer resolves (e.g. a deleted recording) is silently dropped, per the
    design spec's Non-goals section."""
    if not ids:
        return []
    rows = session.scalars(select(Recording).where(Recording.id.in_(ids))).all()
    by_id = {r.id: r for r in rows}
    return [by_id[i] for i in ids if i in by_id]
