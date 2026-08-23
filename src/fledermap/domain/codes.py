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
    BATDETECT2 = "batdetect2"
    BATTYBIRDNET = "battybirdnet"
    KALEIDOSCOPE = "kaleidoscope"
    MANUAL = "manual"
