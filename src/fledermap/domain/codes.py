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


class TimestampSource(StrEnum):
    """Which candidate `merge_metadata` prefers for `recorded_at` (spec D17).

    Lives here, not in `ingest/merge.py`: spec section 4 places shared
    vocabulary in `domain/`, not in a leaf ingest module — `config.py`
    imports it from here rather than reaching into `ingest/merge.py`.
    """

    FILENAME = "filename"
    METADATA = "metadata"
