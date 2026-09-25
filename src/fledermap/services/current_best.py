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

from fledermap.domain.codes import (
    IdSource,
    Verdict,
    is_emt_automatic,
    is_manual,
    is_multi_support,
)
from fledermap.store.models import Identification, Recording, Taxon

_PRECEDENCE: tuple[IdSource, ...] = (
    IdSource.MANUAL,
    IdSource.EMT_MANUAL,
    IdSource.EMT_GUANO,
    IdSource.EMT_WAMD,
    IdSource.EMT_FILENAME,
    IdSource.BATDETECT2,
    IdSource.BATTYBIRDNET,
    IdSource.FLEDERMAP_NOISE,
)

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


@dataclass(frozen=True)
class CurrentIdentification:
    """One or more non-superseded claims from the single winning source (§2/§3 of the design
    spec, extended by docs/superpowers/specs/2026-09-17-fledermap-classifier-interaction-design.md).
    `claims` has more than one entry only when the winning source is `is_multi_support`
    (MANUAL/BATDETECT2/BATTYBIRDNET) with multiple SPECIES/group claims -- every plain source is
    resolved to exactly one claim."""

    claims: tuple[Identification, ...]

    @property
    def is_multi(self) -> bool:
        return len(self.claims) > 1

    @property
    def primary(self) -> Identification:
        """The single representative claim for headline/marker-color purposes --
        first-added (lowest first_seen_at). This tie-break is only reachable for a genuine
        multi-species `is_multi_support` result: every plain source has at most one live row
        per recording by construction (services/identifications.py's replace_claims), so there
        is nothing to tie-break there any more."""
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


def _live_claims(recording: Recording, source: IdSource) -> list[Identification]:
    """A source's claims for this recording, or an empty list when its claims are ALL
    automatic NO_ID ("weak NoID" -- falls through to the next source). `is_manual` sources
    never fall through ("strong NoID": a human's own NO_ID is a considered answer, not an
    admission of uncertainty) -- see domain/codes.py's `is_manual` docstring."""
    matches = [i for i in recording.identifications if i.source == source]
    if not matches:
        return []
    if not is_manual(source) and all(m.verdict == Verdict.NO_ID for m in matches):
        return []
    return matches


def _is_effective_multi_support_override_target(matches: list[Identification]) -> bool:
    """Whether a `multi_support` source's live claim set is eligible to be an override target
    in `_multi_support_containment_override` below (spec §3.2 steps 2-3 / §5). Two independent
    gates, both required:

    - **Homogeneous SPECIES** (spec §5): the source's live claims must all be verdict `SPECIES`.
      A set mixing a `SPECIES` claim with a `NOISE` claim (legitimately possible per
      `services/classifier_detections.py`'s `materialize_claims`, which dedups by
      `effective_taxon_id` alone) means the source is contradicting itself, not offering a
      coherent multi-species answer to defer to -- treating it as an override target would
      produce a `CurrentIdentification` that itself mixes a SPECIES claim with a NOISE claim.
    - **More than one claim**: a singleton claim set gains nothing by overriding the winner --
      it names the same information the winner already does, so preferring it would only flip
      which source's row displays as "current" for no informational gain."""
    return len(matches) > 1 and all(m.verdict == Verdict.SPECIES for m in matches)


def _multi_support_containment_override(
    recording: Recording,
    winning_taxon_id: int | None,
    remaining_sources: Sequence[IdSource],
) -> list[Identification] | None:
    """Looks past a plain (non-multi_support, non-manual) SPECIES winner for the NEAREST
    multi_support source below it with live claims -- skipping any weak-NoID source along the
    way via `_live_claims`, same as the main walk. That nearest source must also be
    "effectively multi-support" (`_is_effective_multi_support_override_target` above) to be
    considered at all; if it isn't, the peek gives up right there -- per spec §3.2 step 2, an
    ineligible nearest source is never skipped in favor of a further multi_support source down
    the precedence order. Returns that source's full claim set when it contains
    `winning_taxon_id` (no real disagreement, so show the richer result instead of discarding
    it); returns None otherwise (either no eligible multi_support source was found, or it
    doesn't contain the taxon -- a real disagreement, so the original winner stands and nothing
    further below is consulted, per spec §3.2: "the highest precedence source that actually
    disagrees is the one that should win")."""
    for source in remaining_sources:
        if not is_multi_support(source):
            continue
        matches = _live_claims(recording, source)
        if not matches:
            continue
        if not _is_effective_multi_support_override_target(matches):
            return None
        taxon_ids = {m.taxon_id for m in matches if m.taxon_id is not None}
        if winning_taxon_id in taxon_ids:
            return matches
        return None
    return None


def _best_identification(
    recording: Recording,
    precedence: Sequence[IdSource],
) -> CurrentIdentification | None:
    for index, source in enumerate(precedence):
        matches = _live_claims(recording, source)
        if not matches:
            continue
        if (
            not is_manual(source)
            and not is_multi_support(source)
            and matches[0].verdict == Verdict.SPECIES
        ):
            override = _multi_support_containment_override(
                recording,
                matches[0].taxon_id,
                precedence[index + 1 :],
            )
            if override is not None:
                return CurrentIdentification.from_matches(override)
        return CurrentIdentification.from_matches(matches)
    return None


_AUTOMATIC_PRECEDENCE: tuple[IdSource, ...] = tuple(
    source for source in _PRECEDENCE if not is_manual(source)
)


def current_best_identification(recording: Recording) -> CurrentIdentification | None:
    """Walk sources in precedence order. A source's claims are skipped (fall through to the
    next source) per `_live_claims` above -- any SPECIES, NOISE, or is_manual claim of any
    verdict wins outright and stops the walk, EXCEPT that a plain (non-`is_manual`,
    non-`is_multi_support`) SPECIES winner is first checked against the nearest
    `is_multi_support` source below it (`_multi_support_containment_override`): if that
    source's claim set contains the winner's taxon, its full (possibly multi-species) result
    replaces the plain winner instead of discarding the extra species information. A `NOISE`
    winner never triggers this check -- a `multi_support` source's set is always species-only or
    `{NO_ID}` (spec §5), so containment against `NOISE` can never apply. Nor does an `is_manual`
    winner -- a human's call, or a device-side manual correction, is never second-guessed by an
    automatic classifier below it.

    `is_manual` and `is_multi_support` sources may surface more than one claim (a genuine
    multi-species file, or the containment override above) -- every plain source has at most one
    live row per recording by construction (services/identifications.py's replace_claims keeps
    it that way)."""
    return _best_identification(recording, _PRECEDENCE)


def automatic_best_identification(recording: Recording) -> CurrentIdentification | None:
    """Like `current_best_identification`, but skips every `is_manual` source entirely
    (MANUAL, EMT_MANUAL) rather than merely falling through their NO_ID -- "what would the
    automatic classifiers alone resolve to, ignoring any human judgment already recorded."
    Used by `services/classify_suggestions.py`'s "Agree with classifiers"/"Noise" heuristics
    (design spec 2026-09-25-fledermap-classify-suggestions-design.md) to suggest a likely
    classification independent of whatever manual state is currently staged in the Classify
    box, including the "revert to what the classifiers said" case where a standing MANUAL
    claim already exists but disagrees.

    `automatic_best.verdict` can only ever be SPECIES, NOISE, or this function returns None --
    never a live NO_ID: `_live_claims`'s "all-NO_ID-from-a-non-manual-source is absent" rule
    means a pure automatic NO_ID (including `fledermap.noise`'s own) always folds to absent,
    never survives as a winning claim."""
    return _best_identification(recording, _AUTOMATIC_PRECEDENCE)


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
    """One identification row's own label -- the matrix's row label
    (`identification_matrix` above; `_identification_matrix.html`).

    - A resolved taxon: just the scientific name -- the raw code (if any) is never shown
      alongside it. The matrix already has a column per classifier to say who made the
      claim, so repeating the code there would add no information, only noise.
    - A code with no resolved taxon (an unmapped automatic code): the raw code alone.
    - Neither: the verdict's own value (`species`/`no_id`/`noise`), same fallback
      `recording_headline` uses.
    """
    if taxon is not None:
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
    if not is_manual(ident.source) and ident.verdict == Verdict.NO_ID:
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
    entries for classifiers not yet implemented, e.g. BATTYBIRDNET --
    CLAUDE.md's "further classifiers are coming" note) sorts last rather than
    raising.
    Stable sort: multiple claims from the same source (a multi-species MANUAL
    result) keep their relative order."""

    def key(ident: Identification) -> int:
        try:
            return _PRECEDENCE.index(ident.source)
        except ValueError:
            return len(_PRECEDENCE)

    return sorted(identifications, key=key)


@dataclass(frozen=True)
class IdentificationRow:
    """One rendered row of the "Identifications" breakdown box
    (`_recording_panel.html`, `_identifications_box.html`) -- `source_label` is what the
    template shows, which may differ from `ident.source.value` when `combine_emt` folded
    the row into the group label `"emt"` (see `display_identifications` below)."""

    source_label: str
    ident: Identification
    status: IdentificationStatus


def display_identifications(
    identifications: Sequence[Identification],
    best: CurrentIdentification | None,
    *,
    combine_emt: bool,
) -> list[IdentificationRow]:
    """Precedence-ordered rows for the "Identifications" breakdown box, optionally folding
    the three EMT auto-ID materializations (`domain.codes.is_emt_automatic` --
    emt.guano/emt.wamd/emt.filename) into a single `"emt"` row (Config.combine_emt_display,
    an operator-facing display choice: the three sources are different materializations of
    the same underlying detector result, not independent evidence). `emt.manual` is never
    folded in -- it's a human's own on-device correction, not another auto-ID
    materialization.

    Sorting by precedence (`sort_identifications_by_precedence`) already puts
    emt.guano/emt.wamd/emt.filename consecutively when present. Within that run, the combined
    row represents whichever claim is `"current"` (the one `current_best_identification`
    actually picked) if any -- NOT simply the highest-precedence row in the group, since a
    higher-precedence source's own automatic NO_ID falls through to the next EMT source
    (`_live_claims` above) without becoming that group's winner. Absent a `"current"` claim in
    the group (the whole group lost to a higher-precedence non-EMT source, or is a pure NO_ID
    fallthrough with nothing else live), the highest-precedence row stands in as before. The
    database still keeps all three `Identification` rows; this only changes what's displayed."""
    ordered = [
        (ident, identification_status(ident, best))
        for ident in sort_identifications_by_precedence(identifications)
    ]
    if not combine_emt:
        return [
            IdentificationRow(ident.source.value, ident, status)
            for ident, status in ordered
        ]

    rows: list[IdentificationRow] = []
    index = 0
    while index < len(ordered):
        ident, status = ordered[index]
        if not is_emt_automatic(ident.source):
            rows.append(IdentificationRow(ident.source.value, ident, status))
            index += 1
            continue
        group = []
        while index < len(ordered) and is_emt_automatic(ordered[index][0].source):
            group.append(ordered[index])
            index += 1
        rep_ident, rep_status = next(
            (row for row in group if row[1] == "current"),
            group[0],
        )
        rows.append(IdentificationRow("emt", rep_ident, rep_status))
    return rows


ClaimKey = tuple[Verdict] | tuple[Verdict, int | str]


def claim_key(ident: Identification) -> ClaimKey:
    """One classifier's "answer", comparable across sources regardless of which raw text or
    taxon each source's row carries -- shared by `identification_matrix` below (grouping rows)
    and `services/review_flags.py` (disagreement voting), which used to keep its own private
    copy of exactly this (`docs/superpowers/specs/2026-09-17-fledermap-classifier-interaction-
    design.md`'s own §6 flagged this duplication as a known limitation). A resolved species
    compares by taxon_id (raw_label may differ trivially between sources naming the same
    species); an unmapped one falls back to raw_label, so two different unmapped codes aren't
    silently treated as agreeing just because neither resolved; every other verdict (NO_ID,
    NOISE) compares by verdict alone, so e.g. every NO_ID claim groups together regardless of
    source."""
    if ident.verdict != Verdict.SPECIES:
        return (ident.verdict,)
    if ident.taxon_id is not None:
        return (ident.verdict, ident.taxon_id)
    return (ident.verdict, ident.raw_label or "")


@dataclass(frozen=True)
class MatrixColumn:
    """One classifier column of the "Identifications" matrix -- `key` matches an
    `IdentificationRow.source_label` from `display_identifications`, `label` is the
    abbreviated column header actually shown."""

    key: str
    label: str


_COLUMN_LABELS: dict[str, str] = {
    "batdetect2": "bd2",
    "battybirdnet": "bbn",
    "fledermap.noise": "fm.n",
    "emt.manual": "emt.m",
    "emt.guano": "emt.g",
    "emt.wamd": "emt.w",
    "emt.filename": "emt.f",
}


@dataclass(frozen=True)
class MatrixCell:
    """One (row, column) intersection. `ident`/`status` are `None` when this column's
    classifier made no claim matching this row's `claim_key` -- the template renders that as
    a blank cell rather than a checkmark."""

    ident: Identification | None
    status: IdentificationStatus | None


@dataclass(frozen=True)
class MatrixRow:
    """One species/verdict row of the matrix. `label`/`taxon` come from whichever claim in
    this row happens to be encountered first when building the row (any of them carries the
    same `claim_key`, so the same display text) -- not necessarily the current winner.
    `is_current` is true when any cell in the row is the actual "current" claim, driving the
    row label's bold styling; a cell's own `status` (not this flag) decides whether that
    individual cell's checkmark is bold -- a row can be the current winner while still holding
    a "shadowed" cell for a source that merely agrees without being the one that won."""

    label: str
    taxon: Taxon | None
    is_current: bool
    cells: dict[str, MatrixCell]


@dataclass(frozen=True)
class IdentificationMatrix:
    columns: list[MatrixColumn]
    rows: list[MatrixRow]


def identification_matrix(
    identifications: Sequence[Identification],
    best: CurrentIdentification | None,
    *,
    combine_emt: bool,
) -> IdentificationMatrix:
    """Species-by-classifier pivot of the "Identifications" breakdown (Obsidian backlog:
    "Classifier list vs table") -- built directly on `display_identifications`'s already-
    collapsed rows, so `Config.combine_emt_display` folds guano/wamd/filename into one `emt`
    column here exactly the same way it folds them into one list row there; EMT-group
    disagreement already resolves to the winning claim per that same function, so a combined
    `emt` column never needs its own disagreement handling. Rows group claims by `claim_key`
    (same species/verdict, whichever source said it) and are ordered by first appearance
    across the precedence-sorted columns."""
    display_rows = display_identifications(
        identifications, best, combine_emt=combine_emt
    )

    columns: list[MatrixColumn] = []
    seen_columns: set[str] = set()
    for row in display_rows:
        if row.source_label not in seen_columns:
            seen_columns.add(row.source_label)
            columns.append(
                MatrixColumn(
                    row.source_label,
                    _COLUMN_LABELS.get(row.source_label, row.source_label),
                ),
            )

    row_order: list[ClaimKey] = []
    by_key: dict[ClaimKey, dict[str, IdentificationRow]] = {}
    for row in display_rows:
        key = claim_key(row.ident)
        if key not in by_key:
            row_order.append(key)
            by_key[key] = {}
        by_key[key][row.source_label] = row

    rows: list[MatrixRow] = []
    for key in row_order:
        by_column = by_key[key]
        representative = next(iter(by_column.values())).ident
        rows.append(
            MatrixRow(
                label=identification_label(representative, representative.taxon),
                taxon=representative.taxon,
                is_current=any(row.status == "current" for row in by_column.values()),
                cells={
                    column.key: MatrixCell(
                        by_column[column.key].ident,
                        by_column[column.key].status,
                    )
                    if column.key in by_column
                    else MatrixCell(None, None)
                    for column in columns
                },
            ),
        )
    return IdentificationMatrix(columns=columns, rows=rows)
