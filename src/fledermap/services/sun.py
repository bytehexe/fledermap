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
from astral.sun import elevation, sun


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
    observer = Observer(latitude=lat, longitude=lon, elevation=0)
    now_local = now.astimezone(tz)
    today = now_local.date()

    try:
        times = sun(observer, date=today, tzinfo=tz)
    except ValueError:
        extreme = _always_day_or_night(observer, now_local)
        return RtcSnapshot(
            state=None,
            next_transition=None,
            moon=_moon_info(today) if extreme == "always_night" else None,
            extreme_latitude=extreme,
        )

    dawn, sunrise, sunset, dusk = (
        times["dawn"],
        times["sunrise"],
        times["sunset"],
        times["dusk"],
    )

    if now_local < dawn:
        state = RtcSunState.NIGHT
        next_transition = NextTransition("Dawn", dawn)
    elif now_local < sunrise:
        state = RtcSunState.DAWN
        next_transition = NextTransition("Sunrise", sunrise)
    elif now_local < sunset:
        state = RtcSunState.DAY
        next_transition = NextTransition("Sunset", sunset)
    elif now_local < dusk:
        state = RtcSunState.DUSK
        next_transition = NextTransition("Night from", dusk)
    else:
        # Past tonight's dusk: the *next* dawn is tomorrow's, not today's (already happened).
        state = RtcSunState.NIGHT
        tomorrow = today + timedelta(days=1)
        try:
            tomorrow_times = sun(observer, date=tomorrow, tzinfo=tz)
            next_transition = NextTransition("Dawn", tomorrow_times["dawn"])
        except ValueError:
            # Right at a polar transition boundary -- tomorrow has no dawn at all. Degrade
            # gracefully (no line 2) rather than propagate.
            next_transition = None

    moon = _moon_info(today)
    return RtcSnapshot(
        state=state,
        next_transition=next_transition,
        moon=moon,
        extreme_latitude=None,
    )
