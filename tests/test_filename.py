from __future__ import annotations

from datetime import datetime

import pytest

from fledermap.domain.codes import Verdict
from fledermap.ingest.filename import parse_emt_filename


def test_parses_species_filename() -> None:
    """Taken verbatim from the real sample files."""
    parsed = parse_emt_filename("EPTSER_20150610_215446.wav")

    assert parsed is not None
    assert parsed.code == "EPTSER"
    assert parsed.verdict is Verdict.SPECIES
    assert parsed.timestamp == datetime(2015, 6, 10, 21, 54, 46)


def test_parses_noid() -> None:
    parsed = parse_emt_filename("NoID_20260821_214532.wav")

    assert parsed is not None
    assert parsed.verdict is Verdict.NO_ID
    assert parsed.code is None


def test_parses_noise() -> None:
    parsed = parse_emt_filename("NOISE_20260821_220117.WAV")

    assert parsed is not None
    assert parsed.verdict is Verdict.NOISE
    assert parsed.code is None


def test_uppercase_extension_is_accepted() -> None:
    assert parse_emt_filename("MYODAU_20150623_213547.WAV") is not None


@pytest.mark.parametrize(
    "name",
    [
        "random.wav",
        "EPTSER_20150610.wav",
        "EPTSER_notadate_215446.wav",
        "EPTSER_20150632_215446.wav",
        "EPTSER_20150610_996146.wav",
        "",
    ],
)
def test_unparseable_names_return_none(name: str) -> None:
    assert parse_emt_filename(name) is None


def test_timestamp_is_naive() -> None:
    """The filename carries no timezone; merging decides what to do about that."""
    parsed = parse_emt_filename("EPTSER_20150610_215446.wav")

    assert parsed is not None
    assert parsed.timestamp.tzinfo is None


def test_extension_is_deliberately_not_validated() -> None:
    """Content probing is the caller's gate; see parse_emt_filename's docstring."""
    parsed = parse_emt_filename("EPTSER_20150610_215446.mp3")

    assert parsed is not None
    assert parsed.code == "EPTSER"
