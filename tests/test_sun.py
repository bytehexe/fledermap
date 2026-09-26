from __future__ import annotations

import datetime
import zoneinfo

from fledermap.services.sun import (
    MoonInfo,
    MoonPhaseBucket,
    RtcSunState,
    compute_rtc,
)

LONDON_TZ = zoneinfo.ZoneInfo("Europe/London")
# Observer(latitude=51.5074, longitude=-0.1278, elevation=0), 2024-06-21 (summer solstice),
# computed directly from astral 3.2's sun(observer, date=..., tzinfo=LONDON_TZ) -- see the
# design spec / plan for how these were derived (a live run of the actual library, not a
# hand-guessed value). dawn 03:54:50, sunrise 04:43:33, sunset 21:21:17, dusk 22:09:59 (all
# +01:00, BST).


def test_state_is_day_between_sunrise_and_sunset() -> None:
    now = datetime.datetime(2024, 6, 21, 12, 0, tzinfo=LONDON_TZ)
    snapshot = compute_rtc(51.5074, -0.1278, now, LONDON_TZ)
    assert snapshot.state == RtcSunState.DAY
    assert snapshot.next_transition is not None
    assert snapshot.next_transition.label == "Sunset"
    assert snapshot.next_transition.at == datetime.datetime(
        2024, 6, 21, 21, 21, 17, 493304, tzinfo=LONDON_TZ
    )


def test_state_is_dawn_between_civil_dawn_and_sunrise() -> None:
    now = datetime.datetime(2024, 6, 21, 4, 0, tzinfo=LONDON_TZ)
    snapshot = compute_rtc(51.5074, -0.1278, now, LONDON_TZ)
    assert snapshot.state == RtcSunState.DAWN
    assert snapshot.next_transition is not None
    assert snapshot.next_transition.label == "Sunrise"


def test_state_is_dusk_between_sunset_and_civil_dusk() -> None:
    now = datetime.datetime(2024, 6, 21, 21, 45, tzinfo=LONDON_TZ)
    snapshot = compute_rtc(51.5074, -0.1278, now, LONDON_TZ)
    assert snapshot.state == RtcSunState.DUSK
    assert snapshot.next_transition is not None
    assert snapshot.next_transition.label == "Night from"


def test_state_is_night_before_dawn_and_next_transition_is_todays_dawn() -> None:
    now = datetime.datetime(2024, 6, 21, 2, 0, tzinfo=LONDON_TZ)
    snapshot = compute_rtc(51.5074, -0.1278, now, LONDON_TZ)
    assert snapshot.state == RtcSunState.NIGHT
    assert snapshot.next_transition is not None
    assert snapshot.next_transition.label == "Dawn"
    assert snapshot.next_transition.at == datetime.datetime(
        2024, 6, 21, 3, 54, 50, 817008, tzinfo=LONDON_TZ
    )


def test_state_is_night_after_dusk_and_next_transition_is_tomorrows_dawn() -> None:
    """After tonight's dusk, the next dawn is tomorrow's, not today's (which already happened).
    This matters for the midnight transition case: at 23:00 on a day with sunset, the next
    dawn must point forward in time, not backward."""
    now = datetime.datetime(2024, 6, 21, 23, 0, tzinfo=LONDON_TZ)
    snapshot = compute_rtc(51.5074, -0.1278, now, LONDON_TZ)
    assert snapshot.state == RtcSunState.NIGHT
    assert snapshot.next_transition is not None
    assert snapshot.next_transition.label == "Dawn"
    assert snapshot.next_transition.at.date() == datetime.date(2024, 6, 22)


def test_moon_phase_near_full_moon() -> None:
    # astral 3.2's moon.phase(date=2024-06-21) == 13.489 -- close to 14 (full).
    now = datetime.datetime(2024, 6, 21, 12, 0, tzinfo=LONDON_TZ)
    snapshot = compute_rtc(51.5074, -0.1278, now, LONDON_TZ)
    assert snapshot.moon == MoonInfo(phase=MoonPhaseBucket.FULL, illumination_pct=100)


def test_moon_phase_near_new_moon() -> None:
    # astral 3.2's moon.phase(date=2024-01-10) == 26.944... -- close to 28/0 (new).
    now = datetime.datetime(2024, 1, 10, 12, 0, tzinfo=LONDON_TZ)
    snapshot = compute_rtc(51.5074, -0.1278, now, LONDON_TZ)
    assert snapshot.moon == MoonInfo(phase=MoonPhaseBucket.NEW, illumination_pct=1)


def test_extreme_latitude_polar_day_in_summer() -> None:
    tz = zoneinfo.ZoneInfo("Europe/Oslo")
    now = datetime.datetime(2024, 6, 21, 12, 0, tzinfo=tz)
    snapshot = compute_rtc(69.6492, 18.9553, now, tz)  # Tromso, Norway
    assert snapshot.extreme_latitude == "always_day"
    assert snapshot.state is None
    assert snapshot.next_transition is None
    assert snapshot.moon is None


def test_extreme_latitude_polar_night_in_winter() -> None:
    tz = zoneinfo.ZoneInfo("Europe/Oslo")
    now = datetime.datetime(2024, 12, 21, 12, 0, tzinfo=tz)
    snapshot = compute_rtc(69.6492, 18.9553, now, tz)  # Tromso, Norway
    assert snapshot.extreme_latitude == "always_night"
    assert snapshot.state is None
    assert snapshot.next_transition is None
    # Moon phase is independent of the sun's behaviour -- still populated so the frontend
    # has an icon to show.
    assert snapshot.moon is not None


def test_northern_eu_dusk_wraps_midnight_helsinki_at_2330() -> None:
    """Important #1 Case A: at high latitude around midsummer, dusk wraps midnight but is
    stamped with the same calendar date as dawn, breaking time-order assumptions. At 23:30
    local (genuine civil twilight, elevation -3.21°), should show DUSK state with next
    transition to NIGHT (dusk ends at 00:45 next calendar day)."""
    tz = zoneinfo.ZoneInfo("Europe/Helsinki")
    now = datetime.datetime(2024, 6, 21, 23, 30, tzinfo=tz)
    snapshot = compute_rtc(60.17, 24.94, now, tz)
    assert snapshot.state == RtcSunState.DUSK
    assert snapshot.next_transition is not None
    assert snapshot.next_transition.label == "Night from"
    # Verify it's the correct dusk time (00:45:20 on next calendar day).
    assert snapshot.next_transition.at.hour == 0
    assert snapshot.next_transition.at.minute == 45
    assert snapshot.extreme_latitude is None


def test_white_night_reykjavik_sunrise_sunset_exist_no_civil_twilight() -> None:
    """Important #1 Case B: at high latitude on a white-night day, sun never reaches civil
    twilight (dawn/dusk raise ValueError), but sunrise and sunset do occur. Should show DAY
    state with actual sunrise/sunset times, not collapse to 'always_day' hiding the times."""
    tz = zoneinfo.ZoneInfo("Atlantic/Reykjavik")
    now = datetime.datetime(2024, 6, 21, 12, 0, tzinfo=tz)
    snapshot = compute_rtc(64.15, -21.94, now, tz)
    # At noon on a white-night day, we're in DAY state (between sunrise and sunset).
    assert snapshot.state == RtcSunState.DAY
    assert snapshot.next_transition is not None
    assert snapshot.next_transition.label == "Sunset"
    # Verify the sunset time is correct (00:02:50 next calendar day).
    assert snapshot.next_transition.at.hour == 0
    assert snapshot.next_transition.at.minute == 2
    assert snapshot.extreme_latitude is None


def test_display_timezone_differs_from_observer_timezone_la_utc() -> None:
    """Important #2: when DISPLAY_TIMEZONE differs from observer's timezone, use observer's
    actual local date for looking up sun times, not the display timezone's date. Here, UTC
    time 2024-06-21 03:00:00Z = 2024-06-20 20:00:00 PDT (LA is still in daylight, sunset at
    20:07). Should show DAY state, not NIGHT."""
    tz_utc = zoneinfo.ZoneInfo("UTC")
    now_utc = datetime.datetime(2024, 6, 21, 3, 0, tzinfo=tz_utc)
    # Pass DISPLAY_TIMEZONE (UTC) to compute_rtc, even though observer is in LA.
    # The function should use longitude to estimate observer's true local date.
    snapshot = compute_rtc(34.05, -118.24, now_utc, tz_utc)
    # At LA's actual local time (20:00), we're in DAY state (sunset is 20:07).
    assert snapshot.state == RtcSunState.DAY
    assert snapshot.next_transition is not None
    assert snapshot.next_transition.label == "Sunset"
    assert snapshot.extreme_latitude is None
