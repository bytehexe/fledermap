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
    """The Review Focus case: after tonight's dusk, "Dawn" must be tomorrow's, not today's
    (which already happened)."""
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
