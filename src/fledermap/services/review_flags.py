"""Computed "needs review" criteria for a recording's assigned species
(docs/superpowers/specs/2026-09-09-fledermap-flagged-for-review-design.md).
Nothing here is stored -- every reason is recomputed live and cleared only
by fixing the underlying data (mapping a species code, adding a manual
classification), the same philosophy the unmapped-species review queue
already uses. See services/current_best.py's current_best_identification,
which this module builds on rather than duplicates."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import (
    IdSource,
    Verdict,
    is_manual,
    is_multi_support,
    is_only_noise,
)
from fledermap.services.current_best import current_best_identification
from fledermap.store.models import Identification, Recording

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


_HUMAN_REVIEW_SOURCES = frozenset({IdSource.MANUAL, IdSource.EMT_MANUAL})


def _has_human_review(recording: Recording) -> bool:
    """Whether a human has already weighed in on this recording -- either
    in-app (`IdSource.MANUAL`) or on the detector itself
    (`IdSource.EMT_MANUAL`). Either one means "already reviewed", so it
    suppresses every criterion in `review_reasons` below, not just the
    disagreement check -- a single shared gate rather than each criterion
    re-deriving its own notion of "human reviewed"."""
    return any(i.source in _HUMAN_REVIEW_SOURCES for i in recording.identifications)


_ClaimKey = tuple[Verdict] | tuple[Verdict, int | str]


def _claim_key(ident: Identification) -> _ClaimKey:
    """One classifier's "answer", comparable across sources. A resolved
    species compares by taxon_id (raw_label may differ trivially between
    sources naming the same species); an unmapped one falls back to
    raw_label, so two different unmapped codes aren't silently treated as
    agreeing just because neither resolved."""
    if ident.verdict != Verdict.SPECIES:
        return (ident.verdict,)
    if ident.taxon_id is not None:
        return (ident.verdict, ident.taxon_id)
    return (ident.verdict, ident.raw_label or "")


@dataclass(frozen=True)
class _SourceVote:
    """One source's vote-set plus whether it counts as "effectively multi-support" for this
    recording (spec §4/§5) -- a source's `is_multi_support` property is a fixed fact about the
    source, but whether it behaves as multi-support here depends on its actual claims, so
    `_compatible` needs the per-recording answer, not the raw property."""

    claim_keys: frozenset[_ClaimKey]
    effectively_multi_support: bool


def _source_vote_sets(
    recording: Recording,
) -> dict[IdSource, _SourceVote]:
    """One vote per eligible source -- `is_manual` sources are excluded entirely (matching
    `_HUMAN_REVIEW_SOURCES`'s old role; called directly, not just via `review_reasons`, this
    function must not treat a human's claim as just another classifier's vote), and an
    `is_only_noise` source's `NO_ID` claim is excluded (a coarse pre-filter admitting it can't
    tell either way is not informative -- see domain/codes.py's `is_only_noise` docstring). A
    source with no remaining claims after that filtering contributes no vote at all.

    A source counts as "effectively multi-support" only when `is_multi_support(source)` is true
    AND every one of its (already-filtered) live claims has verdict `SPECIES` (spec §5) -- a set
    mixing a `SPECIES` claim with a `NOISE` claim is the source contradicting itself, not a
    coherent multi-species answer, so `_compatible` must not read it via the
    equality/containment rules meant for that case."""
    by_source: dict[IdSource, list[Identification]] = {}
    for ident in recording.identifications:
        if is_manual(ident.source):
            continue
        by_source.setdefault(ident.source, []).append(ident)

    votes: dict[IdSource, _SourceVote] = {}
    for source, claims in by_source.items():
        if is_only_noise(source):
            claims = [c for c in claims if c.verdict != Verdict.NO_ID]
        if not claims:
            continue
        effectively_multi_support = is_multi_support(source) and all(
            c.verdict == Verdict.SPECIES for c in claims
        )
        votes[source] = _SourceVote(
            claim_keys=frozenset(_claim_key(c) for c in claims),
            effectively_multi_support=effectively_multi_support,
        )
    return votes


def _compatible(
    set_a: frozenset[_ClaimKey],
    a_multi: bool,
    set_b: frozenset[_ClaimKey],
    b_multi: bool,
) -> bool:
    """Whether two sources' vote-sets agree, per spec §4. `a_multi`/`b_multi` are each source's
    "effectively multi-support" status for this recording (`_SourceVote.effectively_multi_support`)
    -- not the raw `is_multi_support(source)` property -- since a `multi_support` source whose
    claims mix `SPECIES` with `NOISE` (spec §5) is treated here as an ordinary plain source:

    - Both effectively multi-support: only exact equality agrees -- deliberately strict (see the
      module-level design spec's §4/§6): two multi-species sources must name exactly the same
      set, even a strict subset of the other's calls counts as disagreement.
    - Exactly one effectively multi-support: the plain source's single call must be a MEMBER of
      the multi-support source's set (containment, not equality) -- a source that can only ever
      name one species isn't expected to name every species a multi-species source might.
    - Neither effectively multi-support: both sets are singletons, so equality and containment
      coincide -- same comparison the original flat key-based check already made for two plain
      sources. One exception: a bare `{NOISE}` vs. a bare `{NO_ID}` is compatible even though the
      sets aren't equal -- a classifier confidently calling NOISE and another classifier simply
      not clearing its own threshold for anything carries no signal of actual contradiction,
      unlike SPECIES vs. NOISE (still flagged) or SPECIES vs. NO_ID (still flagged, per §4's gap
      3 in the classifier-interaction design spec)."""
    if a_multi and b_multi:
        return set_a == set_b
    if a_multi:
        return set_b <= set_a
    if b_multi:
        return set_a <= set_b
    noise_key, no_id_key = (Verdict.NOISE,), (Verdict.NO_ID,)
    if {set_a, set_b} == {frozenset({noise_key}), frozenset({no_id_key})}:
        return True
    return set_a == set_b


def _disagreement_reason(recording: Recording) -> str | None:
    """At least two classifiers disagreeing on verdict or species (Obsidian backlog: a new
    "flagged for review" criterion, built with future classifiers in mind, extended by
    docs/superpowers/specs/2026-09-17-fledermap-classifier-interaction-design.md §4).
    Deliberately names no `IdSource` explicitly beyond the `is_manual`/`is_only_noise`/
    `is_multi_support` properties -- a future classifier participates automatically by
    declaring those, with no code change here."""
    votes = list(_source_vote_sets(recording).items())
    for i in range(len(votes)):
        _, vote_a = votes[i]
        for _, vote_b in votes[i + 1 :]:
            if not _compatible(
                vote_a.claim_keys,
                vote_a.effectively_multi_support,
                vote_b.claim_keys,
                vote_b.effectively_multi_support,
            ):
                return "Classifiers disagree"
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
    """Every computed reason this recording should be reviewed -- empty if
    none apply. Gated on `_has_human_review`, not just a standing MANUAL
    claim (a human classifying it, in-app or on the detector, already
    counts as reviewed); see the module docstring and the design spec's
    Goals section for why nothing here is stored or dismissible.

    The disagreement check runs regardless of what verdict currently wins
    -- unlike rarity/misattribution below, which are only meaningful for a
    SPECIES result, a classifier disagreement is exactly as interesting
    when the CURRENT-BEST verdict is NOISE (or nothing at all) but a
    lower-precedence source called it a species."""
    if _has_human_review(recording):
        return []

    reasons: list[str] = []
    disagreement = _disagreement_reason(recording)
    if disagreement is not None:
        reasons.append(disagreement)

    best = current_best_identification(recording)
    if best is None or best.verdict != Verdict.SPECIES:
        return reasons
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
