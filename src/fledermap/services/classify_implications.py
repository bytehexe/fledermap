"""Mines the Classify box's "likely suggestions" implication rules from historical
MANUAL-vs-automatic-classifier patterns, part (b) of the classify-suggestions feature
(docs/superpowers/specs/2026-09-25-fledermap-classify-suggestions-part-b-design.md).

`mine_classify_implications` (offline, full rebuild every run -- spec §4/§6) and
`lookup_mined_suggestions` (suggestion-time, cheap indexed reads -- spec §5) share
`antecedent_key`/`subset_keys`: the canonical form a size-1/size-2 taxon-id subset maps to,
and the enumeration of every such subset from a recording's automatic-species atom set.
Antecedent size is capped at 2 (spec §1) -- neither function ever produces or looks up a
larger subset, regardless of how many atoms the recording actually has."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from itertools import combinations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import IdSource, Verdict, is_manual
from fledermap.services.classify_suggestions import ClassifySuggestion, SuggestedTaxon
from fledermap.store.models import (
    ClassifyImplicationRule,
    Identification,
    Recording,
    Taxon,
)


def antecedent_key(taxon_ids: Iterable[int]) -> str:
    """Canonical form of a taxon-id subset: sorted, comma-joined. Shared by mining
    (storage key) and lookup (query key) -- they must agree exactly, or a mined rule
    becomes unreachable at suggestion time."""
    return ",".join(str(i) for i in sorted(set(taxon_ids)))


def subset_keys(taxon_ids: Iterable[int]) -> list[str]:
    """Every size-1 and size-2 subset of `taxon_ids`, as canonical keys -- never larger,
    regardless of how many distinct atoms `taxon_ids` holds (spec §1's antecedent-size
    cap). Order is not meaningful; callers that need determinism sort or set() this."""
    atoms = sorted(set(taxon_ids))
    keys: list[str] = []
    for size in (1, 2):
        for combo in combinations(atoms, size):
            keys.append(antecedent_key(combo))
    return keys


def _automatic_atoms(recording: Recording) -> frozenset[int]:
    """Every distinct taxon_id among this recording's automatic (non-`is_manual`) SPECIES
    claims (spec §2) -- an antecedent atom, source-agnostic (Global Constraints: never
    "which classifier claimed X")."""
    return frozenset(
        ident.taxon_id
        for ident in recording.identifications
        if not is_manual(ident.source)
        and ident.verdict == Verdict.SPECIES
        and ident.taxon_id is not None
    )


def _manual_conclusions(recording: Recording) -> list[tuple[Verdict, int | None]]:
    """Every MANUAL/EMT_MANUAL claim on this recording, as a (verdict, taxon_id) conclusion
    -- a genuine multi-species MANUAL call yields one entry per taxon, decomposed into
    independent training rows (spec §2), never a single multi-conclusion rule. A SPECIES
    claim with no resolved taxon_id is excluded -- there's nothing to conclude."""
    return [
        (ident.verdict, ident.taxon_id)
        for ident in recording.identifications
        if is_manual(ident.source)
        and (ident.verdict != Verdict.SPECIES or ident.taxon_id is not None)
    ]


@dataclass(frozen=True)
class _Counters:
    co_occurrence: dict[tuple[str, Verdict, int | None], int]
    antecedent_total: dict[str, int]
    conclusion_total: dict[tuple[Verdict, int | None], int]
    example_total: int


def _count_training_examples(recordings: Sequence[Recording]) -> _Counters:
    co_occurrence: dict[tuple[str, Verdict, int | None], int] = defaultdict(int)
    antecedent_total: dict[str, int] = defaultdict(int)
    conclusion_total: dict[tuple[Verdict, int | None], int] = defaultdict(int)
    example_total = 0

    for recording in recordings:
        atoms = _automatic_atoms(recording)
        keys = subset_keys(atoms)
        for verdict, taxon_id in _manual_conclusions(recording):
            example_total += 1
            conclusion_total[(verdict, taxon_id)] += 1
            for key in keys:
                co_occurrence[(key, verdict, taxon_id)] += 1
                antecedent_total[key] += 1

    return _Counters(
        co_occurrence=dict(co_occurrence),
        antecedent_total=dict(antecedent_total),
        conclusion_total=dict(conclusion_total),
        example_total=example_total,
    )


def mine_classify_implications(
    session: OrmSession,
    *,
    min_support: int,
    min_confidence: float,
    min_lift: float,
) -> int:
    """Full rebuild of `classify_implication_rule` (spec §4/§6: no incremental updates).
    Returns the number of rules stored."""
    manual_sources = (IdSource.MANUAL, IdSource.EMT_MANUAL)
    recordings = (
        session.scalars(
            select(Recording)
            .join(Identification, Identification.recording_id == Recording.id)
            .where(Identification.source.in_(manual_sources))
            .distinct(),
        )
        .unique()
        .all()
    )

    counters = _count_training_examples(recordings)

    session.execute(delete(ClassifyImplicationRule))

    stored = 0
    for (key, verdict, taxon_id), support in counters.co_occurrence.items():
        if support < min_support:
            continue
        confidence = support / counters.antecedent_total[key]
        if confidence < min_confidence:
            continue
        base_rate = (
            counters.conclusion_total[(verdict, taxon_id)] / counters.example_total
        )
        lift = confidence / base_rate
        if lift < min_lift:
            continue
        session.add(
            ClassifyImplicationRule(
                antecedent_key=key,
                conclusion_verdict=verdict,
                conclusion_taxon_id=taxon_id,
                support=support,
                confidence=confidence,
                lift=lift,
            ),
        )
        stored += 1

    return stored


_MINED_PRIORITY_SCALE = 0.5


def lookup_mined_suggestions(
    session: OrmSession,
    recording: Recording,
) -> list[ClassifySuggestion]:
    """Cheap indexed reads against already-mined rules (spec §5) -- the expensive
    computation happened at mining time (`mine_classify_implications`), not here. Ranked by
    confidence descending; every stored row already cleared `min_lift` at mining time, so no
    further lift filtering happens here."""
    atoms = _automatic_atoms(recording)
    keys = subset_keys(atoms)
    if not keys:
        return []

    rules = session.scalars(
        select(ClassifyImplicationRule)
        .where(ClassifyImplicationRule.antecedent_key.in_(keys))
        .order_by(
            ClassifyImplicationRule.confidence.desc(),
            ClassifyImplicationRule.support.desc(),
        ),
    ).all()

    suggestions: list[ClassifySuggestion] = []
    for rule in rules:
        priority = rule.confidence * _MINED_PRIORITY_SCALE
        if (
            rule.conclusion_verdict != Verdict.SPECIES
            or rule.conclusion_taxon_id is None
        ):
            suggestions.append(
                ClassifySuggestion(
                    taxa=(), is_noise=True, label="Noise", priority=priority
                ),
            )
            continue
        taxon = session.get(Taxon, rule.conclusion_taxon_id)
        if taxon is None:
            continue
        suggestions.append(
            ClassifySuggestion(
                taxa=(
                    SuggestedTaxon(id=taxon.id, scientific_name=taxon.scientific_name),
                ),
                is_noise=False,
                label=taxon.scientific_name,
                priority=priority,
            ),
        )
    return suggestions
