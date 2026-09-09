"""'Current best' identification -- design spec P4-2, resolving the parent
spec's (section 5) explicit but never-implemented rule: "manual wins, else
highest-priority non-superseded source by configured order." Not a stored
column -- recomputed on every call, so the order below can change without a
migration.

Rewritten 2026-09-05 (docs/superpowers/specs/2026-09-05-fledermap-manual-
classification-design.md) to fix a real precedence bug and support
multi-species files: a source's claims are only skipped (treated as absent)
when they are PURELY automatic NO_ID -- an automatic classifier's admission
of uncertainty must not shadow a real answer further down the precedence
order. NOISE is always active, and MANUAL is always active including its own
NO_ID, since a human's judgment is deliberate rather than an admission of
uncertainty. A winning MANUAL source may carry more than one non-superseded
SPECIES/group claim (a genuine multi-species file); CurrentIdentification
represents that instead of forcing a single winner."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from fledermap.domain.codes import IdSource, Verdict
from fledermap.store.models import Identification, Recording, Taxon

_PRECEDENCE: tuple[IdSource, ...] = (
    IdSource.MANUAL,
    IdSource.EMT_MANUAL,
    IdSource.EMT_GUANO,
    IdSource.EMT_WAMD,
    IdSource.EMT_FILENAME,
)

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


@dataclass(frozen=True)
class CurrentIdentification:
    """One or more non-superseded claims from the single winning source
    (§2/§3 of the design spec). `claims` has more than one entry only when
    the winning source is MANUAL with multiple SPECIES/group claims -- every
    other source is resolved to exactly one claim, same as before this
    rewrite."""

    claims: tuple[Identification, ...]

    @property
    def is_multi(self) -> bool:
        return len(self.claims) > 1

    @property
    def primary(self) -> Identification:
        """The single representative claim for headline/marker-color
        purposes -- first-added (lowest first_seen_at). This tie-break is
        only reachable for a genuine multi-species MANUAL result: every
        other source has at most one live row per recording by construction
        (services/identifications.py's replace_claims), so there is nothing
        to tie-break there any more."""
        return min(self.claims, key=lambda i: i.first_seen_at or _EPOCH)

    @property
    def verdict(self) -> Verdict:
        return self.primary.verdict

    @property
    def taxon_ids(self) -> frozenset[int]:
        return frozenset(c.taxon_id for c in self.claims if c.taxon_id is not None)

    @classmethod
    def from_matches(cls, matches: Sequence[Identification]) -> CurrentIdentification:
        return cls(claims=tuple(matches))


def current_best_identification(recording: Recording) -> CurrentIdentification | None:
    """Walk sources in precedence order. A source's claims are skipped
    (fall through to the next source) only when they are ALL automatic
    NO_ID -- any SPECIES, NOISE, or MANUAL claim of any verdict wins
    outright and stops the walk.

    Only MANUAL may surface more than one claim (a genuine multi-species
    file) -- every other source has at most one live row per recording by
    construction (services/identifications.py's replace_claims keeps it that
    way), so there is nothing to dedupe here any more."""
    for source in _PRECEDENCE:
        matches = [i for i in recording.identifications if i.source == source]
        if not matches:
            continue
        if source != IdSource.MANUAL and all(
            m.verdict == Verdict.NO_ID for m in matches
        ):
            continue
        return CurrentIdentification.from_matches(matches)
    return None


def recording_headline(taxon: Taxon | None, best: CurrentIdentification | None) -> str:
    """The species/verdict label every recording headline renders
    (recording_details.html, _recording_panel.html, session_detail.html each
    had their own copy of the same ternary -- centralized here so a fix only
    has to happen once).

    - A resolved `taxon` always wins: its scientific name (the single-claim,
      single-taxon case -- callers resolve `taxon` from `best.primary.taxon_id`
      today, unaffected by multi-species results since a multi-species
      caller passes `taxon=None` and lets the branch below handle it).
    - `best.is_multi`: "Multiple Species" -- individual claims are only
      enumerated in the classifier box (spec §5), not the headline.
    - No identification at all: "unidentified".
    - `NO_ID`/`NOISE` verdicts show their own value (`best.verdict.value`).
    - A real `SPECIES` verdict whose code never mapped to a `Taxon`: shown as
      "<code> (unmapped species)" using `best.primary.raw_label`.
    """
    if taxon is not None:
        return taxon.scientific_name
    if best is None:
        return "unidentified"
    if best.is_multi:
        return "Multiple Species"
    if best.verdict == Verdict.SPECIES and best.primary.raw_label:
        return f"{best.primary.raw_label} (unmapped species)"
    return best.verdict.value


def identification_label(ident: Identification, taxon: Taxon | None) -> str:
    """One identification row's own label, for the per-source breakdown
    (`_identifications_box.html`, `_recording_panel.html` -- centralized
    here for the same reason as `recording_headline` above: both templates
    had their own copy of `ident.raw_label or ident.verdict.value`, which
    silently rendered a MANUAL claim as the bare word "species" -- MANUAL
    never sets `raw_label` (it's a structured taxon pick, not a detector's
    raw code string), so it fell straight through to the generic verdict
    fallback with no indication of which species was actually chosen.

    - Both a code and a resolved taxon (an automatic classifier whose code
      mapped): "<code> (<scientific name>)", e.g. "PIPPIP (Pipistrellus
      pipistrellus)".
    - A resolved taxon with no code (the MANUAL case this was added for):
      just the scientific name.
    - A code with no resolved taxon (an unmapped automatic code): the raw
      code alone.
    - Neither: the verdict's own value (`species`/`no_id`/`noise`), same
      fallback `recording_headline` uses.
    """
    if taxon is not None:
        if ident.raw_label:
            return f"{ident.raw_label} ({taxon.scientific_name})"
        return taxon.scientific_name
    if ident.raw_label:
        return ident.raw_label
    return ident.verdict.value


IdentificationStatus = Literal["current", "passive", "shadowed"]


def identification_status(
    ident: Identification,
    best: CurrentIdentification | None,
) -> IdentificationStatus:
    """Where one raw Identification row stands relative to the resolved
    precedence result -- for the "Identifications" breakdown box (spec §5a),
    so a human making a manual call can see *why* the page shows what it
    shows, not just the final headline in isolation.

    A row is in exactly one of these three states:
    - "current": one of `best.claims` -- actually driving the shown result.
    - "passive": an automatic-source NO_ID claim skipped per the precedence
      walk (services/current_best.py's `current_best_identification`) --
      true regardless of what ultimately won, since this describes the
      row's own status, not the overall outcome.
    - "shadowed": a real claim that lost only because a higher-precedence
      source's claim won outright.
    """
    if best is not None and ident in best.claims:
        return "current"
    if ident.source != IdSource.MANUAL and ident.verdict == Verdict.NO_ID:
        return "passive"
    return "shadowed"


def sort_identifications_by_precedence(
    identifications: Sequence[Identification],
) -> list[Identification]:
    """Display order for the "Identifications" breakdown box on both the
    recording-details page and the map drawer panel: highest-precedence
    source first, the same order `current_best_identification`'s own walk
    uses -- replaces the earlier per-row "shadowed by emt.guano" annotation,
    which needed the winning source spelled out in text; the row's position
    in this order plus its `identification_status` styling (bold/italic,
    `app.css`'s `.identification-current`/`-passive`/`-shadowed`) now say
    the same thing without it.

    A source not yet wired into `_PRECEDENCE` (`domain.codes.IdSource` has
    entries for classifiers not yet implemented, e.g. BATDETECT2 -- CLAUDE.md's
    "further classifiers are coming" note) sorts last rather than raising.
    Stable sort: multiple claims from the same source (a multi-species MANUAL
    result) keep their relative order."""

    def key(ident: Identification) -> int:
        try:
            return _PRECEDENCE.index(ident.source)
        except ValueError:
            return len(_PRECEDENCE)

    return sorted(identifications, key=key)
