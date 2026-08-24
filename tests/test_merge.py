from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from fledermap.domain.codes import IdSource, Verdict
from fledermap.ingest.filename import parse_emt_filename
from fledermap.ingest.merge import NoTimestampError, merge_metadata
from fledermap.ingest.wamd import parse_wamd
from tests.fixtures import wamd_payload

BERLIN = timezone(timedelta(hours=2))


def test_filename_wins_by_default() -> None:
    """The provisional default. Metadata says 09:54, filename says 21:54.

    The chosen `recorded_at` is the filename reading, but it must not invent
    an offset: the metadata's +02:00 is the only offset evidence in the file,
    and `recorded_at` (and `filename_at`, stored in its own aware column) must
    carry it rather than a fabricated UTC (task-11 fix round 1, priority 1:
    `filename_at`/`metadata_at` are aware, not naive, matching their
    `DateTime(timezone=True)` columns).
    """
    result = merge_metadata(
        guano=None,
        wamd=parse_wamd(wamd_payload()),
        filename=parse_emt_filename("EPTSER_20150610_215446.wav"),
    )

    assert result.recorded_at.hour == 21
    assert result.recorded_at.utcoffset() == timedelta(hours=2)
    assert result.filename_at == datetime(2015, 6, 10, 21, 54, 46, tzinfo=BERLIN)
    assert result.metadata_at == datetime(2015, 6, 10, 9, 54, 54, tzinfo=BERLIN)


def test_metadata_source_can_be_selected() -> None:
    """Flipping the config must not require re-ingesting (spec D17)."""
    result = merge_metadata(
        guano=None,
        wamd=parse_wamd(wamd_payload()),
        filename=parse_emt_filename("EPTSER_20150610_215446.wav"),
        timestamp_source="metadata",
    )

    assert result.recorded_at.hour == 9


def test_disagreement_is_measured() -> None:
    result = merge_metadata(
        guano=None,
        wamd=parse_wamd(wamd_payload()),
        filename=parse_emt_filename("EPTSER_20150610_215446.wav"),
    )

    assert result.timestamp_disagreement_s is not None
    assert result.timestamp_disagreement_s > 3600


def test_agreeing_timestamps_report_no_disagreement() -> None:
    result = merge_metadata(
        guano=None,
        wamd=parse_wamd(wamd_payload(timestamp="2015-06-10 21:54:46+0200")),
        filename=parse_emt_filename("EPTSER_20150610_215446.wav"),
    )

    assert result.timestamp_disagreement_s == 0


def test_missing_timestamp_entirely_raises() -> None:
    with pytest.raises(NoTimestampError):
        merge_metadata(
            guano=None,
            wamd=parse_wamd(wamd_payload(timestamp=None)),
            filename=None,
        )


def test_falls_back_when_preferred_source_absent() -> None:
    """No parseable filename: use metadata rather than failing."""
    result = merge_metadata(
        guano=None,
        wamd=parse_wamd(wamd_payload()),
        filename=None,
    )

    assert result.recorded_at.hour == 9


def test_produces_one_identification_per_source() -> None:
    result = merge_metadata(
        guano=None,
        wamd=parse_wamd(wamd_payload(auto_id="MYODAU", manual_id="MYODAU")),
        filename=parse_emt_filename("MYODAU_20150623_213547.wav"),
    )

    sources = {i.source for i in result.identifications}

    assert IdSource.EMT_WAMD in sources
    assert IdSource.EMT_MANUAL in sources
    assert IdSource.EMT_FILENAME in sources


def test_noise_filename_yields_noise_verdict() -> None:
    result = merge_metadata(
        guano=None,
        wamd=parse_wamd(wamd_payload(auto_id=None, timestamp=None)),
        filename=parse_emt_filename("NOISE_20260821_220117.wav"),
    )

    verdicts = {i.verdict for i in result.identifications}

    assert Verdict.NOISE in verdicts


def test_position_prefers_guano_over_wamd() -> None:
    from fledermap.ingest.guano_read import GuanoMetadata

    guano = GuanoMetadata(latitude=52.5, longitude=13.4)
    result = merge_metadata(
        guano=guano,
        wamd=parse_wamd(wamd_payload()),
        filename=parse_emt_filename("EPTSER_20150610_215446.wav"),
    )

    assert result.latitude == 52.5


def test_naive_preferred_borrows_aware_fallback_offset() -> None:
    """Real sample shape: filename is naive, metadata carries +02:00.

    `recorded_at` must carry the metadata's offset rather than a fabricated
    UTC — it is the only offset evidence present in the file.
    """
    result = merge_metadata(
        guano=None,
        wamd=parse_wamd(wamd_payload(timestamp="2015-06-10 09:54:54+0200")),
        filename=parse_emt_filename("EPTSER_20150610_215446.wav"),
    )

    assert result.metadata_at is not None
    assert result.recorded_at.utcoffset() == result.metadata_at.utcoffset()
    assert result.recorded_at.utcoffset() == timedelta(hours=2)


def test_no_offset_evidence_anywhere_uses_default_timezone() -> None:
    """Neither source carries an offset: default_timezone applies (UTC default)."""
    result = merge_metadata(
        guano=None,
        wamd=parse_wamd(wamd_payload(timestamp="2015-06-10 09:54:54")),
        filename=parse_emt_filename("EPTSER_20150610_215446.wav"),
    )

    assert result.recorded_at.utcoffset() == timedelta(0)
    # filename_at/metadata_at get the same fabricated default, not just
    # recorded_at (task-11 fix round 1, priority 1).
    assert result.filename_at is not None
    assert result.filename_at.utcoffset() == timedelta(0)
    assert result.metadata_at is not None
    assert result.metadata_at.utcoffset() == timedelta(0)


def test_filename_at_and_metadata_at_are_always_aware() -> None:
    """Both columns are `DateTime(timezone=True)` (models.py); a naive value
    read back from Postgres becomes aware, so comparing it against a naive
    Python value in `_apply_metadata`'s change-guard is always unequal — the
    idempotency-breaking defect this test guards against (task-11 fix round
    1, priority 1).

    Covers two of the three offset-evidence shapes: metadata has the only
    offset, and neither has one. The third — filename has the only offset —
    is structurally impossible: `FilenameParse.timestamp` is naive by
    contract (`ingest/filename.py`), so `filename_at` alone can never carry
    offset evidence.
    """
    metadata_has_offset = merge_metadata(
        guano=None,
        wamd=parse_wamd(wamd_payload()),  # +02:00
        filename=parse_emt_filename("EPTSER_20150610_215446.wav"),  # naive
    )
    assert metadata_has_offset.filename_at is not None
    assert metadata_has_offset.filename_at.tzinfo is not None
    assert metadata_has_offset.metadata_at is not None
    assert metadata_has_offset.metadata_at.tzinfo is not None

    neither_has_offset = merge_metadata(
        guano=None,
        wamd=parse_wamd(wamd_payload(timestamp="2015-06-10 09:54:54")),  # naive
        filename=parse_emt_filename("EPTSER_20150610_215446.wav"),  # naive
    )
    assert neither_has_offset.filename_at is not None
    assert neither_has_offset.filename_at.tzinfo is not None
    assert neither_has_offset.metadata_at is not None
    assert neither_has_offset.metadata_at.tzinfo is not None


def test_explicit_default_timezone_is_honoured_when_no_evidence() -> None:
    eastern = timezone(timedelta(hours=-5))
    result = merge_metadata(
        guano=None,
        wamd=parse_wamd(wamd_payload(timestamp="2015-06-10 09:54:54")),
        filename=parse_emt_filename("EPTSER_20150610_215446.wav"),
        default_timezone=eastern,
    )

    assert result.recorded_at.utcoffset() == timedelta(hours=-5)
