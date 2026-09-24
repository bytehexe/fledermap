"""Vocabulary shared across ingest, storage, and the eventual web layer."""

from __future__ import annotations

from enum import StrEnum


class Verdict(StrEnum):
    """What an identification actually asserts.

    `NoID` and `NOISE` are legitimate answers, not missing data, so they get a
    first-class representation rather than sentinel taxa (spec section 5).
    """

    SPECIES = "species"
    NO_ID = "no_id"
    NOISE = "noise"


_SENTINEL_VERDICTS: dict[str, Verdict] = {
    "noid": Verdict.NO_ID,
    "noise": Verdict.NOISE,
}


def sentinel_verdict(label: str) -> Verdict | None:
    """Classify one of the EMT's own non-species identification labels.

    The device emits two spellings of the same two sentinels depending on
    where they're read from: the filename convention writes `NoID`/`NOISE`
    (no space), while GUANO's and wamd's `Species Auto ID`/`Species Manual
    ID` fields write `No ID` (with a space) for the same "found nothing" case
    -- confirmed against real field recordings, 2026-08-26, where both chunks
    in the same file carried the literal string `No ID`. Comparing
    whitespace- and case-insensitively unifies both spellings. Returns
    `None` for anything else, i.e. a real species code -- callers keep that
    text as `raw_label` for `resolve_code`; a sentinel match must not, or it
    would show up as an unmapped label in the review queue for something
    that was never meant to be a species code."""
    normalized = "".join(label.split()).lower()
    return _SENTINEL_VERDICTS.get(normalized)


class IdSource(StrEnum):
    """Where an identification came from. Sources coexist; they never overwrite."""

    EMT_GUANO = "emt.guano"
    EMT_WAMD = "emt.wamd"
    EMT_FILENAME = "emt.filename"
    # The on-device manual correction (GUANO/wamd `manual_id`), re-derived from
    # the file on every scan — distinct from `MANUAL` below so it can be
    # superseded like the other EMT-derived claims. Uses the same Wildlife
    # Acoustics code vocabulary as the EMT's auto-ID sources.
    EMT_MANUAL = "emt.manual"
    BATDETECT2 = "batdetect2"
    BATTYBIRDNET = "battybirdnet"
    KALEIDOSCOPE = "kaleidoscope"
    # A future UI-entered identification, never re-derived from the scanned
    # file — so it must never be auto-superseded by a rescan. Deliberately
    # excluded from `_EMT_SOURCES` in services/ingest.py.
    MANUAL = "manual"
    # Only ever emits NOISE or NO_ID (services/noise.py's `classify_noise`),
    # never SPECIES -- lowest precedence in current_best.py's `_PRECEDENCE`,
    # so any real identification from another source always wins over it.
    FLEDERMAP_NOISE = "fledermap.noise"


class AnnotationSource(StrEnum):
    """Where an annotation came from -- deliberately separate from IdSource: `comment` has no
    meaning as an identification-claim source, and IdSource carries precedence/disagreement
    semantics (current_best.py, review_flags.py) that annotations don't participate in. See
    docs/superpowers/specs/2026-09-23-fledermap-annotations-design.md §2."""

    COMMENT = "comment"
    # Produced by services/classifier_detections.py's record_detections, which writes one
    # Annotation row per detected call geometry alongside its classifier_detection cache row.
    BATDETECT2 = "batdetect2"
    # Reserved for later specs -- no code produces battybirdnet call geometry yet.
    BATTYBIRDNET = "battybirdnet"


# Declarative classifier properties -- consumed by services/current_best.py's precedence walk
# and services/review_flags.py's disagreement check, so a future classifier participates
# correctly by declaring its properties here instead of needing new code in either function
# (docs/superpowers/specs/2026-09-17-fledermap-classifier-interaction-design.md §2).

_MANUAL_SOURCES: frozenset[IdSource] = frozenset({IdSource.MANUAL, IdSource.EMT_MANUAL})

_ONLY_NOISE_SOURCES: frozenset[IdSource] = frozenset({IdSource.FLEDERMAP_NOISE})

_MULTI_SUPPORT_SOURCES: frozenset[IdSource] = frozenset(
    {IdSource.MANUAL, IdSource.BATDETECT2, IdSource.BATTYBIRDNET},
)

# The three on-device auto-ID materializations of the same underlying detector result --
# distinct from EMT_MANUAL (a human's own on-device correction). Drives
# services/current_best.py's combined-EMT display grouping and services/map_query.py's
# combined-EMT source filter, both gated by Config.combine_emt_display.
EMT_AUTOMATIC_SOURCES: frozenset[IdSource] = frozenset(
    {IdSource.EMT_GUANO, IdSource.EMT_WAMD, IdSource.EMT_FILENAME},
)


def is_manual(source: IdSource) -> bool:
    """A human's deliberate judgment -- either in-app (`MANUAL`) or the on-device correction
    re-derived from the file (`EMT_MANUAL`). Its own `NO_ID` is a considered "I checked and
    there's nothing here," not an admission of uncertainty, so `current_best.py`'s precedence
    walk treats it as "strong NoID" (wins outright) rather than falling through like an
    automatic classifier's `NO_ID` does. Also excluded entirely from
    `review_flags.py`'s disagreement voting."""
    return source in _MANUAL_SOURCES


def is_only_noise(source: IdSource) -> bool:
    """A source whose only possible verdicts are `NOISE` and `NO_ID` -- it can never assert a
    species. Its `NO_ID` is excluded from `review_flags.py`'s disagreement voting (an admission
    from a coarse pre-filter that it can't tell either way is not informative alongside a real
    classifier's confident species call); its `NOISE` claim is not excluded."""
    return source in _ONLY_NOISE_SOURCES


def is_multi_support(source: IdSource) -> bool:
    """A source whose claims for one recording may legitimately name more than one taxon at
    once, rather than every extra claim past the first being either a competing alternative or
    an error. Drives both `current_best.py`'s multi-support containment override and
    `review_flags.py`'s equality/containment disagreement rule."""
    return source in _MULTI_SUPPORT_SOURCES


def is_emt_automatic(source: IdSource) -> bool:
    """One of the three EMT auto-ID materializations (GUANO/wamd/filename) -- see
    `EMT_AUTOMATIC_SOURCES` above."""
    return source in EMT_AUTOMATIC_SOURCES


class VisualSighting(StrEnum):
    """Whether a human observer saw a bat visually during a session --
    independent of acoustic detection/species ID.

    Purely user-set: nothing in this project auto-classifies it, so it has
    no `_locked` companion field to freeze against.
    `UNCLEAR` means "not recorded" as much as "genuinely ambiguous" -- every
    session defaults to it, and a merge falls back to it when neither half
    reports anything more definite (services/sessions.py's
    `resolve_merge_proposal`: YES beats UNCLEAR beats NO, since an unset
    "we don't know" carries no evidence a definite NO could outweigh).
    A closed, three-member vocabulary -- CHECK-enforced, like `Verdict`.
    """

    YES = "yes"
    NO = "no"
    UNCLEAR = "unclear"


class MergeResolution(StrEnum):
    """How a human resolved a `SessionMergeProposal`. NULL means still open."""

    MERGED = "merged"
    REJECTED = "rejected"


class TimestampSource(StrEnum):
    """Which candidate `merge_metadata` prefers for `recorded_at` (spec D17).

    Lives here, not in `ingest/merge.py`: spec section 4 places shared
    vocabulary in `domain/`, not in a leaf ingest module — `config.py`
    imports it from here rather than reaching into `ingest/merge.py`.
    """

    FILENAME = "filename"
    METADATA = "metadata"
