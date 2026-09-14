from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from fledermap.web.timefmt import local_date, local_datetime, local_datetime_seconds

BERLIN = ZoneInfo("Europe/Berlin")


def test_local_datetime_renders_date_time_and_zone_abbreviation() -> None:
    dt = datetime(2026, 9, 6, 18, 29, 53, tzinfo=UTC)  # 20:29:53 CEST
    assert local_datetime(dt, BERLIN) == "2026-09-06 20:29 CEST"


def test_local_datetime_uses_winter_abbreviation_for_a_winter_date() -> None:
    dt = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)  # 13:00 CET
    assert local_datetime(dt, BERLIN) == "2026-01-15 13:00 CET"


def test_local_datetime_none_renders_em_dash() -> None:
    assert local_datetime(None, BERLIN) == "—"


def test_local_datetime_converts_a_non_utc_input_offset_too() -> None:
    # Simulates what SQLAlchemy actually hands back: a fixed-offset tzinfo
    # (whatever the DB session's TimeZone is), not necessarily UTC.
    from datetime import timedelta, timezone

    fixed_offset = timezone(timedelta(hours=2))
    dt = datetime(2026, 9, 6, 20, 29, 53, tzinfo=fixed_offset)
    assert local_datetime(dt, BERLIN) == "2026-09-06 20:29 CEST"


def test_local_date_renders_bare_date_no_zone_suffix() -> None:
    dt = datetime(2026, 9, 6, 23, 0, tzinfo=UTC)  # 01:00 CEST the next day
    assert local_date(dt, BERLIN) == "2026-09-07"


def test_local_date_none_renders_em_dash() -> None:
    assert local_date(None, BERLIN) == "—"


def test_local_datetime_seconds_renders_date_time_seconds_and_zone_abbreviation() -> (
    None
):
    dt = datetime(2026, 9, 6, 18, 29, 53, tzinfo=UTC)  # 20:29:53 CEST
    assert local_datetime_seconds(dt, BERLIN) == "2026-09-06 20:29:53 CEST"


def test_local_datetime_seconds_uses_winter_abbreviation_for_a_winter_date() -> None:
    dt = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)  # 13:00:00 CET
    assert local_datetime_seconds(dt, BERLIN) == "2026-01-15 13:00:00 CET"


def test_local_datetime_seconds_none_renders_em_dash() -> None:
    assert local_datetime_seconds(None, BERLIN) == "—"


def test_local_datetime_seconds_converts_a_non_utc_input_offset_too() -> None:
    from datetime import timedelta, timezone

    fixed_offset = timezone(timedelta(hours=2))
    dt = datetime(2026, 9, 6, 20, 29, 53, tzinfo=fixed_offset)
    assert local_datetime_seconds(dt, BERLIN) == "2026-09-06 20:29:53 CEST"
