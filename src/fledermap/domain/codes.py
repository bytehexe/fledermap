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
    # superseded like the other EMT-derived claims (task-11 fix round 1,
    # priority 4). Uses the same Wildlife Acoustics code vocabulary as the
    # EMT's auto-ID sources.
    EMT_MANUAL = "emt.manual"
    BATDETECT2 = "batdetect2"
    BATTYBIRDNET = "battybirdnet"
    KALEIDOSCOPE = "kaleidoscope"
    # A future UI-entered identification, never re-derived from the scanned
    # file — so it must never be auto-superseded by a rescan. Deliberately
    # excluded from `_EMT_SOURCES` in services/ingest.py.
    MANUAL = "manual"


class SessionKind(StrEnum):
    """Whether a session was stationary monitoring or a walked transect.

    User-set (parent spec section 9); every session derived without a UI to set
    it defaults to STATIONARY. A closed, two-member vocabulary — CHECK-enforced,
    like `Verdict`.
    """

    STATIONARY = "stationary"
    TRANSECT = "transect"


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
