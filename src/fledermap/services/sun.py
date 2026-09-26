"""Sun/moon computation for the RTC sidebar widget (design spec
2026-09-26-fledermap-rtc-widget-design.md). Pure computation, no I/O -- the view calling this
owns the DB session and Flask request; this module only takes plain values and returns plain
dataclasses."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta, tzinfo
from enum import Enum
from typing import Literal

from astral import Observer
from astral.moon import phase as moon_phase
from astral.sun import dawn, dusk, elevation, sun, sunrise, sunset


class RtcSunState(Enum):
    DAY = "day"
    DAWN = "dawn"
    DUSK = "dusk"
    NIGHT = "night"


class MoonPhaseBucket(Enum):
    """Icon file suffix doubles as the enum value -- `moon-phase-<value>.svg`."""

    NEW = "new"
    WAXING_CRESCENT = "waxing-crescent"
    FIRST_QUARTER = "first-quarter"
    WAXING_GIBBOUS = "waxing-gibbous"
    FULL = "full"
    WANING_GIBBOUS = "waning-gibbous"
    LAST_QUARTER = "last-quarter"
    WANING_CRESCENT = "waning-crescent"


_MOON_BUCKETS = list(
    MoonPhaseBucket
)  # ordered: new, waxing-crescent, ..., waning-crescent


@dataclass(frozen=True)
class MoonInfo:
    phase: MoonPhaseBucket
    illumination_pct: int


@dataclass(frozen=True)
class NextTransition:
    label: str
    at: datetime


@dataclass(frozen=True)
class RtcSnapshot:
    state: RtcSunState | None
    next_transition: NextTransition | None
    moon: MoonInfo | None
    extreme_latitude: Literal["always_day", "always_night"] | None


def _moon_info(on: date) -> MoonInfo:
    """astral's moon.phase() returns a float in [0, 27.99]: 0/28 = new moon, ~14 = full.
    Illumination is the standard cosine approximation, not arcsecond-exact -- sufficient for a
    UI cue, not a claim of astronomical precision. Bucket index via a phase-shifted modulo so 0
    falls in the middle of the "new" bucket rather than on a boundary."""
    p = moon_phase(date=on)
    illumination_pct = round((1 - math.cos(2 * math.pi * p / 28)) / 2 * 100)
    bucket_index = int(((p + 1.75) % 28) // 3.5) % 8
    return MoonInfo(
        phase=_MOON_BUCKETS[bucket_index], illumination_pct=illumination_pct
    )


def _always_day_or_night(
    observer: Observer, now_local: datetime
) -> Literal["always_day", "always_night"]:
    """Called only after sun() has raised ValueError for this date/location -- disambiguates
    polar day from polar night via the sun's elevation at local solar noon, not by matching the
    ValueError's message text (which is not a stable API across astral versions)."""
    noon_local = now_local.replace(hour=12, minute=0, second=0, microsecond=0)
    return "always_day" if elevation(observer, noon_local) > 0 else "always_night"


def compute_rtc(lat: float, lon: float, now: datetime, tz: tzinfo) -> RtcSnapshot:
    """Compute sun/moon state and next transition. Handles arbitrary latitude/timezone
    combinations by building an independent timeline of sun events (dawn/sunrise/sunset/dusk)
    across yesterday/today/tomorrow relative to the observer's actual local date (derived from
    longitude + now's UTC time, not from tz if tz differs from the observer's timezone). Each
    event can raise independently at extreme latitudes; all four existing together is never
    assumed. White-night cases (sunrise/sunset exist, dawn/dusk don't) resolve to DAY/NIGHT
    using sunrise/sunset boundaries."""
    observer = Observer(latitude=lat, longitude=lon, elevation=0)
    now_local = now.astimezone(tz)

    # Estimate observer's local date via longitude (rough but adequate for daily events).
    # This fixes Important #2: when tz != observer's actual timezone, use observer's true
    # local date for looking up sun times, not tz's calendar date.
    observer_local_date = (now + timedelta(hours=lon / 15)).date()

    # First, try sun() for today's date. If it raises, check if it's a true extreme-latitude
    # case (no sunrise/sunset at all) or a white-night case (sunrise/sunset exist but not civil
    # twilight). If sunrise and sunset both exist, proceed with the timeline approach.
    sun_raises = False
    try:
        sun(observer, date=observer_local_date, tzinfo=tz)
    except ValueError:
        sun_raises = True

    if sun_raises:
        # sun() raised. Check if sunrise/sunset exist (white night) or not (polar day/night).
        has_sunrise = False
        has_sunset = False
        try:
            sunrise(observer, date=observer_local_date, tzinfo=tz)
            has_sunrise = True
        except ValueError:
            pass
        try:
            sunset(observer, date=observer_local_date, tzinfo=tz)
            has_sunset = True
        except ValueError:
            pass

        if not (has_sunrise and has_sunset):
            # True extreme latitude: no sunrise/sunset at all.
            extreme = _always_day_or_night(observer, now_local)
            return RtcSnapshot(
                state=None,
                next_transition=None,
                moon=_moon_info(observer_local_date)
                if extreme == "always_night"
                else None,
                extreme_latitude=extreme,
            )
        # Otherwise, fall through to the timeline approach below (white-night case).

    # Candidate dates to try: yesterday, today, tomorrow relative to observer's local date.
    candidate_dates = [
        observer_local_date - timedelta(days=1),
        observer_local_date,
        observer_local_date + timedelta(days=1),
    ]

    # Build a timeline of all events that succeed. Each event (dawn/sunrise/sunset/dusk)
    # can raise independently, and we merge all that exist into one sorted sequence.
    # Each tuple is (datetime, state_entered_after_this_event, label_for_this_transition).
    timeline: list[tuple[datetime, RtcSunState, str]] = []

    for candidate_date in candidate_dates:
        # Try each event independently; any can raise.
        try:
            d = dawn(observer, date=candidate_date, tzinfo=tz)
            timeline.append((d, RtcSunState.DAWN, "Dawn"))
        except ValueError:
            pass

        try:
            sr = sunrise(observer, date=candidate_date, tzinfo=tz)
            timeline.append((sr, RtcSunState.DAY, "Sunrise"))
        except ValueError:
            pass

        try:
            ss = sunset(observer, date=candidate_date, tzinfo=tz)
            timeline.append((ss, RtcSunState.DUSK, "Sunset"))
        except ValueError:
            pass

        try:
            du = dusk(observer, date=candidate_date, tzinfo=tz)
            timeline.append((du, RtcSunState.NIGHT, "Night from"))
        except ValueError:
            pass

    if not timeline:
        # No sun events at all (should be rare given sun() succeeded earlier). Fall back.
        extreme = _always_day_or_night(observer, now_local)
        return RtcSnapshot(
            state=None,
            next_transition=None,
            moon=_moon_info(observer_local_date) if extreme == "always_night" else None,
            extreme_latitude=extreme,
        )

    # Sort timeline by time.
    timeline.sort(key=lambda x: x[0])

    # Find current state (last event <= now_local) and next transition (first event > now_local).
    state: RtcSunState | None = None
    next_transition: NextTransition | None = None

    for event_time, event_state, event_label in timeline:
        if event_time <= now_local:
            state = event_state
        else:
            # First event after now: this is the next transition.
            if next_transition is None:
                next_transition = NextTransition(event_label, event_time)

    moon = _moon_info(observer_local_date)
    return RtcSnapshot(
        state=state,
        next_transition=next_transition,
        moon=moon,
        extreme_latitude=None,
    )
