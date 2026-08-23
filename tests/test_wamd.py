from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fledermap.ingest.wamd import parse_wamd
from tests.fixtures import wamd_payload


def test_parses_the_observed_sample_layout() -> None:
    """Byte-for-byte the shape of EPTSER_20150610_215446.wav's wamd chunk."""
    meta = parse_wamd(wamd_payload())

    assert meta.model == "Echo Meter Touch"
    assert meta.app_version == "App 3.1.10"
    assert meta.device == "iPhone Simulator"
    assert meta.timestamp == datetime(
        2015,
        6,
        10,
        9,
        54,
        54,
        tzinfo=timezone(timedelta(hours=2)),
    )
    assert meta.latitude == 42.346973
    assert meta.longitude == -76.48760
    assert meta.auto_id == "EPTSER"


def test_null_elevation_becomes_none() -> None:
    """The EMT writes the literal string '(null)', not an empty field."""
    assert parse_wamd(wamd_payload()).elevation_m is None


def test_real_elevation_is_parsed() -> None:
    meta = parse_wamd(wamd_payload(position="WGS84,52.5194,13.4012,34.5"))

    assert meta.elevation_m == 34.5


def test_auto_and_manual_id_are_separate() -> None:
    """MYODAU_20150623_213547.wav carries both; they must not collapse."""
    meta = parse_wamd(wamd_payload(auto_id="MYODAU", manual_id="MYODAU"))

    assert meta.auto_id == "MYODAU"
    assert meta.manual_id == "MYODAU"


def test_absent_fields_are_none() -> None:
    meta = parse_wamd(wamd_payload(position=None, auto_id=None))

    assert meta.latitude is None
    assert meta.longitude is None
    assert meta.auto_id is None
    assert meta.manual_id is None


def test_unknown_entry_types_are_skipped() -> None:
    """Forward compatibility: a firmware update adding a field must not break ingest."""
    from tests.fixtures import wamd_entry

    payload = wamd_payload() + wamd_entry(0x7F, "something new")

    assert parse_wamd(payload).model == "Echo Meter Touch"


def test_truncated_payload_does_not_raise() -> None:
    truncated = wamd_payload()[:20]

    parse_wamd(truncated)  # must not raise


def test_malformed_position_is_ignored() -> None:
    meta = parse_wamd(wamd_payload(position="garbage"))

    assert meta.latitude is None
