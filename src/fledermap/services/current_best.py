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
        purposes -- first-added (lowest first_seen_at), matching how ties
        within one source already broke before this rewrite."""
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
    file). Every other source is deduped to its single most-recently
    first-seen claim before wrapping, same tie-break as before this
    rewrite -- a non-MANUAL source having two non-superseded claims at once
    is rare (normally a rescan supersedes the prior claim first) and never
    represents a real multi-species result."""
    candidates = [i for i in recording.identifications if i.superseded_at is None]
    for source in _PRECEDENCE:
        matches = [i for i in candidates if i.source == source]
        if not matches:
            continue
        if source != IdSource.MANUAL and all(
            m.verdict == Verdict.NO_ID for m in matches
        ):
            continue
        if source != IdSource.MANUAL:
            matches = [max(matches, key=lambda i: i.first_seen_at or _EPOCH)]
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


IdentificationStatus = Literal["current", "passive", "shadowed", "superseded"]


def identification_status(
    ident: Identification,
    best: CurrentIdentification | None,
) -> IdentificationStatus:
    """Where one raw Identification row stands relative to the resolved
    precedence result -- for the "Identifications" breakdown box (spec §5a),
    so a human making a manual call can see *why* the page shows what it
    shows, not just the final headline in isolation.

    A row is in exactly one of these four states:
    - "superseded": a past claim from the same source, replaced by a newer
      one -- orthogonal to the other three, since current_best_identification
      never even considers superseded rows.
    - "current": one of `best.claims` -- actually driving the shown result.
    - "passive": an automatic-source NO_ID claim skipped per the precedence
      walk (services/current_best.py's `current_best_identification`) --
      true regardless of what ultimately won, since this describes the
      row's own status, not the overall outcome.
    - "shadowed": a real claim that lost only because a higher-precedence
      source's claim won outright. Includes the edge case of a second,
      non-surviving claim from the SAME winning non-MANUAL source (that
      source's claims are deduped to one before wrapping in
      `current_best_identification` -- see that function's docstring) --
      still genuinely not part of `best.claims` and not an automatic NO_ID,
      so "shadowed" is the correct bucket even though nothing of higher
      precedence is actually responsible; callers rendering this state
      should not blindly attribute it to `best.primary.source`.
    """
    if ident.superseded_at is not None:
        return "superseded"
    if best is not None and ident in best.claims:
        return "current"
    if ident.source != IdSource.MANUAL and ident.verdict == Verdict.NO_ID:
        return "passive"
    return "shadowed"
