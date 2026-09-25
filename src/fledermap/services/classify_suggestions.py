"""Derives the Classify box's "likely" suggestion chips (design spec
2026-09-25-fledermap-classify-suggestions-design.md) -- two independent, explainable
heuristics for part (a):

- "Agree with classifiers": the automatic-only precedence winner
  (`current_best.automatic_best_identification`), when it's a SPECIES verdict.
- "Noise": either the recording's own SNR is below the configured threshold (the same
  comparison `services/noise.py` makes for `fledermap.noise`'s own claim), or no automatic
  classifier found anything at all.

Both can fire on the same recording -- they are independent signals, not a single decision;
see the design spec's "Two independent heuristics" section for why. This module never looks
at MANUAL state: whether a suggestion still "adds something" given what's currently staged in
the Classify box is decided entirely client-side (`classifier_logic.js`), so the same embedded
candidate list works whether the box is empty, mid-edit, or already holds a different manual
pick.

The returned list is ordered by `priority` descending and capped to `pool_cap` -- deliberately
looser than the UI's own visible cap, so the client can re-filter this same embedded list as
chips change without a new request (see the spec's "Candidate list" section)."""

from __future__ import annotations

from dataclasses import dataclass

from fledermap.domain.codes import Verdict
from fledermap.services.current_best import automatic_best_identification
from fledermap.store.models import Recording

_PRIORITY_AGREE_WITH_CLASSIFIERS = 2.0
_PRIORITY_NOISE = 1.0


@dataclass(frozen=True)
class SuggestedTaxon:
    id: int
    scientific_name: str


@dataclass(frozen=True)
class ClassifySuggestion:
    taxa: tuple[SuggestedTaxon, ...]  # empty means the Noise sentinel
    is_noise: bool
    label: str
    priority: float


def classify_suggestions(
    recording: Recording,
    *,
    noise_snr_threshold_db: float,
    pool_cap: int = 8,
) -> list[ClassifySuggestion]:
    automatic_best = automatic_best_identification(recording)
    candidates: list[ClassifySuggestion] = []

    if automatic_best is not None and automatic_best.verdict == Verdict.SPECIES:
        taxa = sorted(
            (
                SuggestedTaxon(
                    id=claim.taxon_id, scientific_name=claim.taxon.scientific_name
                )
                for claim in automatic_best.claims
                if claim.taxon_id is not None and claim.taxon is not None
            ),
            key=lambda t: t.scientific_name,
        )
        if taxa:
            label = "Likely: " + ", ".join(t.scientific_name for t in taxa)
            candidates.append(
                ClassifySuggestion(
                    taxa=tuple(taxa),
                    is_noise=False,
                    label=label,
                    priority=_PRIORITY_AGREE_WITH_CLASSIFIERS,
                ),
            )

    low_snr = recording.snr_db is not None and recording.snr_db < noise_snr_threshold_db
    nothing_found = automatic_best is None or automatic_best.verdict == Verdict.NOISE
    if low_snr or nothing_found:
        candidates.append(
            ClassifySuggestion(
                taxa=(), is_noise=True, label="Likely: Noise", priority=_PRIORITY_NOISE
            ),
        )

    candidates.sort(key=lambda c: c.priority, reverse=True)
    return candidates[:pool_cap]
