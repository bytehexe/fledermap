# RTC (sun/moon planning widget) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a sidebar widget showing today's sun/twilight window (and, at night, moon phase) so Janna can plan when to go out recording, sourced from a server config location or browser geolocation.

**Architecture:** A pure computation module (`services/sun.py`, wrapping `astral`) feeds a small server-rendered HTML-fragment view (`GET /rtc-widget`, htmx-polled from the sidebar), with 8 hand-generated first-party moon-phase icons alongside 3 newly-vendored Tabler sun/dawn/dusk icons.

**Tech Stack:** Flask, Jinja, htmx (already vendored), `astral` (new dependency), plain JS (no build step).

**Spec:** `docs/superpowers/specs/2026-09-26-fledermap-rtc-widget-design.md`

## Global Constraints

- `FLEDERMAP_RTC_ENABLED` (bool, default `true`). `false` means: no computation, no `/rtc-widget` route reachable (404), and the sidebar never even renders the widget's container — this is what guarantees zero `navigator.geolocation` calls, not the route's status code.
- `FLEDERMAP_LATITUDE`/`FLEDERMAP_LONGITUDE` (floats): both-or-neither, `ConfigError` if only one is set. When both are set, they always win over any `lat`/`lon` query parameter — no geolocation is ever attempted.
- Display timezone is `app.config["DISPLAY_TIMEZONE"]` (Postgres-derived), **never** `Config.default_timezone` (an unrelated ingest-time fallback).
- Sun times use civil twilight (astral's own default `Depression.CIVIL`) — never override this.
- htmx poll interval is exactly `every 5m` (`hx-trigger="load, every 5m"`).
- No emoji or Unicode dingbats anywhere in the UI (`docs/style-guide.md`).
- The moon-phase icons deviate from Tabler's zero-fill convention by design: the illuminated region gets `fill="currentColor"`; everything else stays `stroke="currentColor"`/`fill="none"`, `viewBox="0 0 24 24"`, `stroke-width="2"`, round linecap/linejoin.
- `astral` is a plain (non-extras, non-NC-licensed) dependency — Apache-2.0, no opt-in ceremony needed.
- Widget content/copy is exactly the table in the spec's "Frontend widget" section — do not improvise different wording.
- `create_app` always opens a real DB connection (to discover `DISPLAY_TIMEZONE`) — every test that calls it is `pytest.mark.db` and needs a real Postgres testcontainer (`dangerouslyDisableSandbox: true`, ~120s spin-up). Run these yourself in the controlling session rather than dispatching them to a subagent to run to completion unassisted (CLAUDE.md's "Environment gotchas").

## Review Focus

- After tonight's `dusk` has passed, the `night` state's "Dawn `HH:MM`" must show **tomorrow's** dawn, not today's (which already happened) — a naive "just read today's `sun()` dict" implementation gets this wrong for roughly half of every night.
- Setting only one of `FLEDERMAP_LATITUDE`/`FLEDERMAP_LONGITUDE` must raise `ConfigError` at startup, not silently compute with a missing coordinate or silently ignore the one that's set.
- The two extreme-latitude cases (`astral.sun.sun()` raising for polar day vs. polar night) must be told apart correctly — both raise the same `ValueError` type, so a lazy implementation might collapse them into one state. Disambiguate via `astral.sun.elevation()` at local solar noon, not by matching the exception's message text (fragile across library versions).
- A configured server location must win over a `lat`/`lon` query parameter unconditionally, including when the query parameter is present, valid, and *different* from the configured location — not just when the query parameter is absent.
- `FLEDERMAP_RTC_ENABLED=false` must mean the rendered page never contains the widget's container element at all (checked at the HTML level), not merely that the route 404s while a dead container still sits in the DOM.

---

## Task 1: Config — `FLEDERMAP_RTC_ENABLED`/`FLEDERMAP_LATITUDE`/`FLEDERMAP_LONGITUDE`

**Files:**
- Modify: `pyproject.toml` (add `astral` dependency)
- Modify: `src/fledermap/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `Config.rtc_enabled: bool`, `Config.latitude: float | None`, `Config.longitude: float | None`. `ConfigError` raised at `Config.from_env()` time if exactly one of latitude/longitude is set.

- [ ] **Step 1: Add the `astral` dependency**

In `pyproject.toml`, in the `dependencies` list, right after `"flask",`:

```toml
  "flask",
  # Apache-2.0, no extras/opt-in ceremony needed (unlike batdetect2/battybirdnet) --
  # sunrise/sunset/moon-phase astronomical calculations for the RTC widget (design spec
  # 2026-09-26-fledermap-rtc-widget-design.md).
  "astral",
```

- [ ] **Step 2: Write the failing config tests**

Append to `tests/test_config.py` (add `ENV_LATITUDE`, `ENV_LONGITUDE`, `ENV_RTC_ENABLED` to the existing `from fledermap.config import (...)` block, alphabetically among the other `ENV_*` names):

```python
def test_rtc_enabled_defaults_to_true(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_MEDIA_ROOT, str(tmp_path / "media"))
    monkeypatch.delenv(ENV_RTC_ENABLED, raising=False)
    config = Config.from_env()
    assert config.rtc_enabled is True


def test_rtc_enabled_can_be_disabled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_MEDIA_ROOT, str(tmp_path / "media"))
    monkeypatch.setenv(ENV_RTC_ENABLED, "false")
    config = Config.from_env()
    assert config.rtc_enabled is False


def test_latitude_and_longitude_default_to_none(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_MEDIA_ROOT, str(tmp_path / "media"))
    monkeypatch.delenv(ENV_LATITUDE, raising=False)
    monkeypatch.delenv(ENV_LONGITUDE, raising=False)
    config = Config.from_env()
    assert config.latitude is None
    assert config.longitude is None


def test_latitude_and_longitude_are_configurable_together(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_MEDIA_ROOT, str(tmp_path / "media"))
    monkeypatch.setenv(ENV_LATITUDE, "51.5074")
    monkeypatch.setenv(ENV_LONGITUDE, "-0.1278")
    config = Config.from_env()
    assert config.latitude == 51.5074
    assert config.longitude == -0.1278


@pytest.mark.parametrize(
    "set_lat,set_lon",
    [(True, False), (False, True)],
)
def test_latitude_and_longitude_are_both_or_neither(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    set_lat: bool,
    set_lon: bool,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_MEDIA_ROOT, str(tmp_path / "media"))
    if set_lat:
        monkeypatch.setenv(ENV_LATITUDE, "51.5074")
    else:
        monkeypatch.delenv(ENV_LATITUDE, raising=False)
    if set_lon:
        monkeypatch.setenv(ENV_LONGITUDE, "-0.1278")
    else:
        monkeypatch.delenv(ENV_LONGITUDE, raising=False)
    with pytest.raises(ConfigError, match="latitude.*longitude|longitude.*latitude"):
        Config.from_env()
```

- [ ] **Step 2b: Run the tests to verify they fail**

Run: `hatch test tests/test_config.py -k "rtc_enabled or latitude or longitude" -v`
Expected: FAIL (`ImportError` — `ENV_RTC_ENABLED`/`ENV_LATITUDE`/`ENV_LONGITUDE` don't exist yet).

- [ ] **Step 3: Implement the config fields**

In `src/fledermap/config.py`, add the three env var names near the other `ENV_*` constants (after `ENV_CLASSIFY_IMPLICATION_MIN_LIFT`):

```python
ENV_RTC_ENABLED = "FLEDERMAP_RTC_ENABLED"
ENV_LATITUDE = "FLEDERMAP_LATITUDE"
ENV_LONGITUDE = "FLEDERMAP_LONGITUDE"
```

Add to `_KNOWN_FILE_KEYS`:

```python
        "rtc_enabled",
        "latitude",
        "longitude",
```

Add fields to the `Config` dataclass (after `host: str = "127.0.0.1"`):

```python
    # RTC sidebar widget (design spec 2026-09-26-fledermap-rtc-widget-design.md). On by
    # default -- unlike batdetect2/battybirdnet, there's no licensing/heavyweight-dependency
    # reason to default it off.
    rtc_enabled: bool = True
    # Both-or-neither -- validated below. Server config always wins over browser geolocation
    # when set, and no geolocation prompt is attempted in that case either.
    latitude: float | None = None
    longitude: float | None = None
```

Add parsing logic in `from_env`, after the `host` block and before `poiidx_database_url_raw = _lookup(...)`:

```python
        rtc_enabled_raw = _lookup(ENV_RTC_ENABLED, "rtc_enabled", file_values)
        if rtc_enabled_raw is None:
            rtc_enabled = True
        elif isinstance(rtc_enabled_raw, bool):
            rtc_enabled = rtc_enabled_raw
        else:
            label = _source_label(ENV_RTC_ENABLED, "rtc_enabled", config_path)
            text = _as_str(rtc_enabled_raw, label).strip().lower()
            if text in ("true", "1", "yes"):
                rtc_enabled = True
            elif text in ("false", "0", "no"):
                rtc_enabled = False
            else:
                msg = f"{label}={rtc_enabled_raw!r} is not a boolean (true/false)."
                raise ConfigError(msg)

        latitude_raw = _lookup(ENV_LATITUDE, "latitude", file_values)
        longitude_raw = _lookup(ENV_LONGITUDE, "longitude", file_values)
        if (latitude_raw is None) != (longitude_raw is None):
            msg = (
                f"{ENV_LATITUDE}/{ENV_LONGITUDE} (or 'latitude'/'longitude' in "
                f"{config_path}) must both be set or both be unset -- got only "
                f"{'latitude' if latitude_raw is not None else 'longitude'}."
            )
            raise ConfigError(msg)
        if latitude_raw is None:
            latitude = None
            longitude = None
        else:
            lat_label = _source_label(ENV_LATITUDE, "latitude", config_path)
            lon_label = _source_label(ENV_LONGITUDE, "longitude", config_path)
            if isinstance(latitude_raw, bool) or isinstance(longitude_raw, bool):
                msg = f"{lat_label}/{lon_label} must be numbers."
                raise ConfigError(msg)
            try:
                latitude = float(latitude_raw)
                longitude = float(longitude_raw)
            except (TypeError, ValueError) as exc:
                msg = f"{lat_label}={latitude_raw!r}/{lon_label}={longitude_raw!r} must be numbers."
                raise ConfigError(msg) from exc
            if not -90.0 <= latitude <= 90.0:
                msg = f"{lat_label}={latitude_raw!r} is not between -90 and 90."
                raise ConfigError(msg)
            if not -180.0 <= longitude <= 180.0:
                msg = f"{lon_label}={longitude_raw!r} is not between -180 and 180."
                raise ConfigError(msg)
```

Add `rtc_enabled=rtc_enabled, latitude=latitude, longitude=longitude,` to the final `return cls(...)` call.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `hatch test tests/test_config.py -v`
Expected: PASS (all, including the pre-existing ones — no regressions).

- [ ] **Step 5: Type-check and lint**

Run: `hatch run types:check && hatch fmt`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml src/fledermap/config.py tests/test_config.py
git commit -m "feat: add FLEDERMAP_RTC_ENABLED/LATITUDE/LONGITUDE config"
```

---

## Task 2: `services/sun.py` — sun/moon computation

**Files:**
- Create: `src/fledermap/services/sun.py`
- Test: `tests/test_sun.py`

**Interfaces:**
- Consumes: nothing from earlier tasks (pure `astral` wrapper).
- Produces: `RtcSunState` (Enum: `DAY`, `DAWN`, `DUSK`, `NIGHT`), `MoonPhaseBucket` (Enum, values `"new"`, `"waxing-crescent"`, `"first-quarter"`, `"waxing-gibbous"`, `"full"`, `"waning-gibbous"`, `"last-quarter"`, `"waning-crescent"`), `MoonInfo` (dataclass: `phase: MoonPhaseBucket`, `illumination_pct: int`), `NextTransition` (dataclass: `label: str`, `at: datetime`), `RtcSnapshot` (dataclass: `state: RtcSunState | None`, `next_transition: NextTransition | None`, `moon: MoonInfo | None`, `extreme_latitude: Literal["always_day", "always_night"] | None`), `compute_rtc(lat: float, lon: float, now: datetime, tz: tzinfo) -> RtcSnapshot`.

- [ ] **Step 1: Write the failing ground-truth tests**

Create `tests/test_sun.py`:

```python
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
    assert snapshot.moon == MoonInfo(phase=MoonPhaseBucket.FULL, illumination_pct=99)


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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `hatch test tests/test_sun.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'fledermap.services.sun'`).

- [ ] **Step 3: Implement `services/sun.py`**

```python
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


_MOON_BUCKETS = list(MoonPhaseBucket)  # ordered: new, waxing-crescent, ..., waning-crescent


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
    return MoonInfo(phase=_MOON_BUCKETS[bucket_index], illumination_pct=illumination_pct)


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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `hatch test tests/test_sun.py -v`
Expected: PASS. If the exact `next_transition.at` microsecond values don't match (astral's algorithm can shift slightly across versions), run this to get the actual values astral computes on this machine and update the test's literals to match:

```bash
hatch run python -c "
import datetime, zoneinfo
from astral import Observer
from astral.sun import sun
tz = zoneinfo.ZoneInfo('Europe/London')
o = Observer(latitude=51.5074, longitude=-0.1278, elevation=0)
print(sun(o, date=datetime.date(2024, 6, 21), tzinfo=tz))
"
```

- [ ] **Step 5: Type-check and lint**

Run: `hatch run types:check && hatch fmt`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/fledermap/services/sun.py tests/test_sun.py
git commit -m "feat: add services/sun.py for RTC sun/moon computation"
```

---

## Task 3: Load `htmx.min.js` globally via `_layout.html`

**Why this is its own task, and why it comes before the widget:** the RTC widget lives inside `_nav.html`, which every page includes via `_layout.html` — but `htmx.min.js` is currently only loaded per-page (`map.html`, `search.html`, `recording_details.html`, `sessions_list.html`), **not** globally. The statistics/species/sites/reviews pages currently load no htmx at all. Without this fix, the widget's `hx-get`/`hx-trigger` attributes would silently do nothing on 8 of the app's 12 pages — the exact same bug class `_layout.html`'s own docstring already documents for Alpine ("`_nav.html` is included by EVERY page via this template, but before this fix, only the 4 pages that happened to need [it] for their OWN content actually loaded it").

**Files:**
- Modify: `src/fledermap/web/templates/_layout.html`
- Modify: `src/fledermap/web/templates/map.html`
- Modify: `src/fledermap/web/templates/search.html`
- Modify: `src/fledermap/web/templates/recording_details.html`
- Modify: `src/fledermap/web/templates/sessions_list.html`
- Test: `tests/test_layout.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_layout.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import Engine

from fledermap.web.app import create_app

pytestmark = pytest.mark.db


def test_htmx_is_loaded_on_a_page_that_previously_had_no_htmx_of_its_own(
    engine: Engine,
    tmp_path: Path,
) -> None:
    """statistics.html never had its own htmx.min.js <script> tag -- this only passes once
    _layout.html loads it globally, the same way it already does for alpine.min.js."""
    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    html = client.get("/statistics").get_data(as_text=True)

    assert "vendor/htmx.min.js" in html


def test_map_page_loads_htmx_exactly_once(engine: Engine, tmp_path: Path) -> None:
    """Guards against the fix leaving a duplicate <script> tag on pages that used to load
    htmx themselves."""
    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    html = client.get("/").get_data(as_text=True)

    assert html.count("vendor/htmx.min.js") == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `hatch test tests/test_layout.py -v`
Expected: `test_htmx_is_loaded_on_a_page_that_previously_had_no_htmx_of_its_own` FAILs (statistics page has no htmx at all yet).

- [ ] **Step 3: Move `htmx.min.js` into `_layout.html`**

In `src/fledermap/web/templates/_layout.html`, add right after the existing `alpine.min.js` line:

```html
  <script src="{{ url_for('vendor.static', filename='alpine.min.js') }}" defer></script>
  <script src="{{ url_for('vendor.static', filename='htmx.min.js') }}" defer></script>
```

Extend the file's own top-of-file comment block (the one already explaining the Alpine move) with one sentence:

```
   htmx.min.js moved here too, 2026-09-26, for the identical reason and by the identical
   mechanism: the RTC sidebar widget (_nav.html) needs htmx on every page, and before this
   fix only map.html/search.html/recording_details.html/sessions_list.html loaded it
   themselves -- statistics/species/sites/reviews had none at all.
```

- [ ] **Step 4: Remove the now-duplicate per-page `<script>` tags**

In each of `map.html`, `search.html`, `recording_details.html`, `sessions_list.html`, delete the page's own `<script src="{{ url_for('vendor.static', filename='htmx.min.js') }}"></script>` line (grep each file for `htmx.min.js` to find its exact spot — the surrounding lines differ per file, so don't guess the line number).

- [ ] **Step 5: Run the tests to verify they pass**

Run: `hatch test tests/test_layout.py tests/test_map_view.py -v`
Expected: PASS (including the pre-existing `test_map_page` assertion checking for `vendor/htmx.min.js`, which still holds since it's now loaded via `_layout.html`).

- [ ] **Step 6: Run the full non-db suite to check for regressions**

Run: `hatch test -m "not db" -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/fledermap/web/templates/_layout.html src/fledermap/web/templates/map.html \
  src/fledermap/web/templates/search.html src/fledermap/web/templates/recording_details.html \
  src/fledermap/web/templates/sessions_list.html tests/test_layout.py
git commit -m "fix: load htmx.min.js globally via _layout.html, not per-page

The RTC widget (upcoming) lives in _nav.html, included on every page, but
htmx was only loaded on 4 of 12 pages -- the same bug class this file's own
comment already documents for Alpine."
```

---

## Task 4: Vendor the 3 new Tabler icons

**Files:**
- Modify: `src/fledermap/services/vendor_assets.py`

**Interfaces:**
- Produces: `icon("sun")`, `icon("sunrise")`, `icon("sunset")` (outline style) become available wherever `web/icons.py`'s `icon()` global is used, once fetched.

- [ ] **Step 1: Add the 3 new `VendorAsset` entries**

In `src/fledermap/services/vendor_assets.py`, add to the `ASSETS` tuple (SHA-256 values verified directly against `unpkg.com/@tabler/icons@3.46.0` — the same pinned version this file's other icon entries already use):

```python
    VendorAsset(
        url="https://unpkg.com/@tabler/icons@3.46.0/icons/outline/sun.svg",
        sha256="1753d34d4c09f4a0e32afbb417defeded055a88f8ac33963556570a53a76dfa9",
        relative_path="icons/outline/sun.svg",
    ),
    VendorAsset(
        url="https://unpkg.com/@tabler/icons@3.46.0/icons/outline/sunrise.svg",
        sha256="d8a1d0fe1234c36e36a3c495140afd18492a839594333a4d6bbdd6ec4a89a37e",
        relative_path="icons/outline/sunrise.svg",
    ),
    VendorAsset(
        url="https://unpkg.com/@tabler/icons@3.46.0/icons/outline/sunset.svg",
        sha256="25431fccffcb87a215fa244956f87ee3b372c61fe9223bb325dc6fd1e95f88a0",
        relative_path="icons/outline/sunset.svg",
    ),
```

No new test needed here: `tests/test_vendor_assets.py` already exercises the fetch/verify/write mechanism generically against fake assets, and these 3 entries are exactly the same shape as every existing one.

- [ ] **Step 2: Fetch them into the dev static root and sanity-check**

Run: `hatch run fledermap fetch-assets` (uses `Config.static_root`, needs `FLEDERMAP_DATABASE_URL`/`FLEDERMAP_ARCHIVE_ROOTS` set in your dev env the way they already are for other local testing)

Verify: the 3 files now exist under `<static_root>/vendor/icons/outline/{sun,sunrise,sunset}.svg` and each starts with `<svg`.

- [ ] **Step 3: Type-check and lint**

Run: `hatch run types:check && hatch fmt`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add src/fledermap/services/vendor_assets.py
git commit -m "feat: vendor Tabler sun/sunrise/sunset outline icons for the RTC widget"
```

---

## Task 5: Moon phase icons

**Files:**
- Create: `scripts/generate_moon_phase_icons.py`
- Create: `src/fledermap/web/static/icons/moon-phase-new.svg`
- Create: `src/fledermap/web/static/icons/moon-phase-waxing-crescent.svg`
- Create: `src/fledermap/web/static/icons/moon-phase-first-quarter.svg`
- Create: `src/fledermap/web/static/icons/moon-phase-waxing-gibbous.svg`
- Create: `src/fledermap/web/static/icons/moon-phase-full.svg`
- Create: `src/fledermap/web/static/icons/moon-phase-waning-gibbous.svg`
- Create: `src/fledermap/web/static/icons/moon-phase-last-quarter.svg`
- Create: `src/fledermap/web/static/icons/moon-phase-waning-crescent.svg`
- Modify: `src/fledermap/web/icons.py`
- Test: `tests/test_web_icons.py`

**Interfaces:**
- Consumes: `MoonPhaseBucket` (Task 2).
- Produces: `make_moon_phase_icon_global(icons_dir: Path) -> Callable[[MoonPhaseBucket], Markup]`, registered as the `moon_phase_icon` Jinja global in Task 6.

**Why a generator script, not freehand-drawn paths:** the boundary of each phase's illuminated region is the intersection of the moon's outer circle with a "terminator" ellipse (`rx = r * cos(theta)`, the standard flat-projection model) — a formula, not something to eyeball per icon. The script is the actual source of truth; the 8 `.svg` files are its checked-in output, kept in the repo (not regenerated at build/run time) the same way `favicon.png` is a checked-in asset, not something fetched or built on demand.

- [ ] **Step 1: Write the generator script**

Create `scripts/generate_moon_phase_icons.py`:

```python
"""Generates the 8 moon-phase SVG icons under src/fledermap/web/static/icons/. Dev-only
tooling (see CLAUDE.md's "scripts/ never ships in the built package") -- run by hand whenever
the phase geometry needs retuning, not at build or run time. The 8 output files are the actual
checked-in asset; this script is how they were produced and how to reproduce/adjust them.

Model: a moon disc of radius R centered at (12, 12) in a 24x24 viewBox, matching Tabler's own
icon conventions (viewBox, stroke-width, linecap/linejoin) with one deliberate deviation: the
illuminated region gets a currentColor FILL (see the design spec's "Moon phase icons" section
for why a pure zero-fill outline can't tell new moon from full moon apart at all). The
illuminated region's boundary is the outer circle's near limb plus a terminator ellipse of
horizontal radius `rx = R * cos(theta)` (theta: 0 = new, pi = full) -- the standard
orthographic-projection terminator model.
"""

from __future__ import annotations

import math
from pathlib import Path

CX = CY = 12
R = 9
OUT_DIR = Path(__file__).resolve().parent.parent / "src" / "fledermap" / "web" / "static" / "icons"

HEADER = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" '
    'fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" '
    'stroke-linejoin="round" class="icon icon-moon-phase icon-moon-phase-{name}">'
)
FOOTER = "</svg>"
DISC = f'<circle cx="{CX}" cy="{CY}" r="{R}"/>'


def _fmt(x: float) -> str:
    return f"{round(x, 4):g}"


def _illuminated_path(theta: float, waxing: bool) -> str:
    top = f"{CX},{CY - R}"
    bottom = f"{CX},{CY + R}"
    rx = R * math.cos(theta)
    if waxing:
        limb = f"M{top} A{R},{R} 0 0 1 {bottom}"
        sweep = 0 if rx >= 0 else 1
    else:
        limb = f"M{top} A{R},{R} 0 0 0 {bottom}"
        sweep = 1 if rx >= 0 else 0
    term = f"A{_fmt(abs(rx))},{R} 0 0 {sweep} {top}"
    return f'<path d="{limb} {term} Z" fill="currentColor" stroke="none"/>'


# (name, theta, waxing) -- theta/waxing is None for the two degenerate buckets (new/full),
# handled specially below.
BUCKETS: list[tuple[str, float | None, bool | None]] = [
    ("new", None, None),
    ("waxing-crescent", math.pi / 4, True),
    ("first-quarter", math.pi / 2, True),
    ("waxing-gibbous", 3 * math.pi / 4, True),
    ("full", math.pi, None),
    ("waning-gibbous", 3 * math.pi / 4, False),
    ("last-quarter", math.pi / 2, False),
    ("waning-crescent", math.pi / 4, False),
]


def render(name: str, theta: float | None, waxing: bool | None) -> str:
    header = HEADER.format(name=name)
    if theta is None:
        # new moon: disc outline only, no illuminated region.
        return f"{header}{DISC}{FOOTER}"
    if math.isclose(theta, math.pi):
        # full moon: the whole disc is illuminated -- fill the disc itself rather than
        # drawing a separate (degenerate, zero-width) path on top of it.
        return f'{header}<circle cx="{CX}" cy="{CY}" r="{R}" fill="currentColor"/>{FOOTER}'
    assert waxing is not None
    return f"{header}{DISC}{_illuminated_path(theta, waxing)}{FOOTER}"


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, theta, waxing in BUCKETS:
        svg = render(name, theta, waxing)
        path = OUT_DIR / f"moon-phase-{name}.svg"
        path.write_text(svg + "\n", encoding="utf-8")
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it**

Run: `hatch run python scripts/generate_moon_phase_icons.py`
Expected: prints 8 "wrote ..." lines, creating the 8 files listed above under `src/fledermap/web/static/icons/`.

- [ ] **Step 3: Visually sanity-check**

Open each of the 8 generated files in a browser (or reuse the visual-companion technique from brainstorming) and confirm: `new` is an empty circle outline, `full` is a filled circle, the 6 in between show a crescent/gibbous shape lit on the correct side (waxing = right, waning = left, per the Northern Hemisphere convention already confirmed live with Janna during brainstorming) with no stray artifacts. This was already visually verified once during brainstorming with these exact coordinates — this step is re-confirming the *generator's actual output file contents* match what was verified, not re-doing the design work.

- [ ] **Step 4: Write the failing icon-lookup test**

Add to `tests/test_web_icons.py` (add `MoonPhaseBucket` import from `fledermap.services.sun`):

```python
from fledermap.services.sun import MoonPhaseBucket
from fledermap.web.icons import make_moon_phase_icon_global


def test_moon_phase_icon_reads_the_file_for_the_given_bucket(tmp_path: Path) -> None:
    (tmp_path / "moon-phase-full.svg").write_text("<svg>full-moon</svg>")

    moon_phase_icon = make_moon_phase_icon_global(tmp_path)

    assert str(moon_phase_icon(MoonPhaseBucket.FULL)) == "<svg>full-moon</svg>"


def test_moon_phase_icon_is_marked_safe_for_jinja_autoescape(tmp_path: Path) -> None:
    from markupsafe import Markup

    (tmp_path / "moon-phase-new.svg").write_text("<svg>x</svg>")

    moon_phase_icon = make_moon_phase_icon_global(tmp_path)

    assert isinstance(moon_phase_icon(MoonPhaseBucket.NEW), Markup)


def test_moon_phase_icon_raises_loudly_when_the_file_is_missing(tmp_path: Path) -> None:
    moon_phase_icon = make_moon_phase_icon_global(tmp_path)

    with pytest.raises(IconNotFoundError, match="full"):
        moon_phase_icon(MoonPhaseBucket.FULL)
```

(Add `IconNotFoundError` to the existing `from fledermap.web.icons import ...` line at the top of the file if not already imported there.)

- [ ] **Step 5: Run the tests to verify they fail**

Run: `hatch test tests/test_web_icons.py -v`
Expected: FAIL (`ImportError: cannot import name 'make_moon_phase_icon_global'`).

- [ ] **Step 6: Implement `make_moon_phase_icon_global`**

In `src/fledermap/web/icons.py`, add (after the existing `make_icon_global` function; add `from fledermap.services.sun import MoonPhaseBucket` to the imports):

```python
def make_moon_phase_icon_global(
    icons_dir: Path,
) -> Callable[[MoonPhaseBucket], Markup]:
    """`icons_dir` is `src/fledermap/web/static/icons` -- checked-in first-party assets
    (Task 5's generator script), NOT `vendor_dir` like `make_icon_global` above. These can't
    live in the vendor-fetched directory: that directory's entire invariant is "these are
    genuine files fetched from Tabler's CDN," and mixing in hand-generated ones would break
    any future re-verification of it (design spec's "Moon phase icons" section)."""

    def moon_phase_icon(bucket: MoonPhaseBucket) -> Markup:
        path = icons_dir / f"moon-phase-{bucket.value}.svg"
        if not path.exists():
            msg = f"moon phase icon {bucket.value!r} not found at {path}"
            raise IconNotFoundError(msg)
        return Markup(path.read_text(encoding="utf-8"))

    return moon_phase_icon
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `hatch test tests/test_web_icons.py -v`
Expected: PASS.

- [ ] **Step 8: Type-check and lint**

Run: `hatch run types:check && hatch fmt`
Expected: no errors.

- [ ] **Step 9: Commit**

```bash
git add scripts/generate_moon_phase_icons.py src/fledermap/web/static/icons/moon-phase-*.svg \
  src/fledermap/web/icons.py tests/test_web_icons.py
git commit -m "feat: add 8 moon-phase icons and their Jinja lookup global"
```

---

## Task 6: `GET /rtc-widget` view

**Files:**
- Create: `src/fledermap/web/views/rtc.py`
- Create: `src/fledermap/web/templates/_rtc_content.html`
- Modify: `src/fledermap/web/app.py`
- Modify: `src/fledermap/cli/main.py`
- Test: `tests/test_rtc_view.py`

**Interfaces:**
- Consumes: `compute_rtc` (Task 2), `MoonPhaseBucket` (Task 2), `icon()`/`moon_phase_icon()` Jinja globals (Task 5, `web/icons.py`).
- Produces: `rtc_bp` (Flask blueprint), registered in `create_app`. `app.config["RTC_ENABLED"]: bool`, `app.config["RTC_LOCATION"]: tuple[float, float] | None`. `app.jinja_env.globals["rtc_enabled"]: bool` (Task 7 reads this in `_nav.html`).

- [ ] **Step 1: Write the failing view tests**

Create `tests/test_rtc_view.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import Engine

from fledermap.web.app import create_app

pytestmark = pytest.mark.db


def test_rtc_widget_returns_404_when_disabled(engine: Engine, tmp_path: Path) -> None:
    app = create_app(engine, tmp_path / "static", tmp_path / "media", rtc_enabled=False)
    client = app.test_client()

    response = client.get("/rtc-widget")

    assert response.status_code == 404


def test_rtc_widget_returns_204_when_no_location_available(
    engine: Engine, tmp_path: Path
) -> None:
    app = create_app(engine, tmp_path / "static", tmp_path / "media", rtc_enabled=True)
    client = app.test_client()

    response = client.get("/rtc-widget")

    assert response.status_code == 204


def test_rtc_widget_uses_query_params_when_no_server_location_configured(
    engine: Engine, tmp_path: Path
) -> None:
    app = create_app(engine, tmp_path / "static", tmp_path / "media", rtc_enabled=True)
    client = app.test_client()

    response = client.get("/rtc-widget?lat=51.5074&lon=-0.1278")

    assert response.status_code == 200
    assert "rtc-widget-content" in response.get_data(as_text=True)


def test_rtc_widget_ignores_query_params_when_server_location_is_configured(
    engine: Engine, tmp_path: Path
) -> None:
    """Review Focus case: server config always wins, even when a different, validly-formed
    lat/lon is passed in the query string."""
    app = create_app(
        engine,
        tmp_path / "static",
        tmp_path / "media",
        rtc_enabled=True,
        latitude=51.5074,
        longitude=-0.1278,
    )
    client = app.test_client()

    configured = client.get("/rtc-widget").get_data(as_text=True)
    with_bogus_query_params = client.get(
        "/rtc-widget?lat=-33.8688&lon=151.2093"  # Sydney -- very different result
    ).get_data(as_text=True)

    assert configured == with_bogus_query_params


def test_rtc_widget_extreme_latitude_polar_day(engine: Engine, tmp_path: Path) -> None:
    app = create_app(engine, tmp_path / "static", tmp_path / "media", rtc_enabled=True)
    client = app.test_client()

    # Tromso in summer -- polar day. The view's own "now" is real wall-clock time, so this
    # test only makes sense if run in a month where that's actually true; instead exercise it
    # through query params against a date astral will treat consistently: this endpoint always
    # computes for "today", so this test asserts the mechanism (a valid response, not a 500)
    # rather than pinning a specific season-dependent state.
    response = client.get("/rtc-widget?lat=69.6492&lon=18.9553")

    assert response.status_code == 200
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `hatch test tests/test_rtc_view.py -v`
Expected: FAIL (`TypeError: create_app() got an unexpected keyword argument 'rtc_enabled'`).

- [ ] **Step 3: Write `_rtc_content.html`**

Create `src/fledermap/web/templates/_rtc_content.html`:

```jinja
{# Rendered by GET /rtc-widget (web/views/rtc.py) into #rtc-widget's innerHTML. Context:
   `icon_name` (a Tabler outline icon name, or None when `moon_bucket` is set instead),
   `moon_bucket` (a MoonPhaseBucket, or None when `icon_name` is set instead -- exactly one of
   the two is ever set), `line1` (str), `line2` (str | None). #}
<div class="rtc-widget-content">
  <span class="rtc-icon">
    {% if moon_bucket %}{{ moon_phase_icon(moon_bucket) }}{% else %}{{ icon(icon_name) }}{% endif %}
  </span>
  <div class="rtc-lines">
    <div class="rtc-line1">{{ line1 }}</div>
    {% if line2 %}<div class="rtc-line2">{{ line2 }}</div>{% endif %}
  </div>
</div>
```

- [ ] **Step 4: Write `web/views/rtc.py`**

```python
"""GET /rtc-widget -- the RTC sidebar widget's htmx-polled content (design spec
2026-09-26-fledermap-rtc-widget-design.md). A view, not an /api/* route: every other
htmx-consumed piece of content in this app is a rendered HTML fragment from web/views/, and
/api/* is exclusively GeoJSON for Leaflet -- a genuinely different consumer. See the design
spec's "Backend route" section for the correction this reflects."""

from __future__ import annotations

from datetime import UTC, datetime

import flask

from fledermap.services.sun import RtcSnapshot, compute_rtc

rtc_bp = flask.Blueprint("rtc", __name__, template_folder="../templates")

_LINE1 = {
    "day": "Daylight",
    "dawn": "Dawn",
    "dusk": "Dusk",
    "night": "Night",
    "always_day": "Polar day",
    "always_night": "Polar night",
}

_ICON_NAME = {
    "day": "sun",
    "dawn": "sunrise",
    "dusk": "sunset",
    "always_day": "sun",
}


def _render(snapshot: RtcSnapshot) -> str:
    if snapshot.extreme_latitude is not None:
        line1 = _LINE1[snapshot.extreme_latitude]
        if snapshot.extreme_latitude == "always_night":
            assert snapshot.moon is not None
            return flask.render_template(
                "_rtc_content.html",
                icon_name=None,
                moon_bucket=snapshot.moon.phase,
                line1=line1,
                line2=None,
            )
        return flask.render_template(
            "_rtc_content.html",
            icon_name=_ICON_NAME["always_day"],
            moon_bucket=None,
            line1=line1,
            line2=None,
        )

    assert snapshot.state is not None
    state = snapshot.state.value
    line1 = _LINE1[state]
    line2 = None
    if snapshot.next_transition is not None:
        label = snapshot.next_transition.label
        time_str = snapshot.next_transition.at.strftime("%H:%M")
        line2 = f"{label} {time_str}"

    if state == "night":
        assert snapshot.moon is not None
        return flask.render_template(
            "_rtc_content.html",
            icon_name=None,
            moon_bucket=snapshot.moon.phase,
            line1=line1,
            line2=line2,
        )
    return flask.render_template(
        "_rtc_content.html",
        icon_name=_ICON_NAME[state],
        moon_bucket=None,
        line1=line1,
        line2=line2,
    )


@rtc_bp.get("/rtc-widget")
def rtc_widget() -> flask.Response:
    if not flask.current_app.config["RTC_ENABLED"]:
        return flask.make_response(("", 404))

    location = flask.current_app.config["RTC_LOCATION"]
    if location is not None:
        lat, lon = location
    else:
        lat = flask.request.args.get("lat", type=float)
        lon = flask.request.args.get("lon", type=float)
        if lat is None or lon is None:
            return flask.make_response(("", 204))

    tz = flask.current_app.config["DISPLAY_TIMEZONE"]
    snapshot = compute_rtc(lat, lon, datetime.now(tz=UTC), tz)
    html = _render(snapshot)
    return flask.make_response(html)
```

- [ ] **Step 5: Wire it into `create_app` and `cli/main.py`**

In `src/fledermap/web/app.py`:
- Replace the existing `from fledermap.web.icons import make_icon_global` line with
  `from fledermap.web.icons import make_icon_global, make_moon_phase_icon_global`, and add
  `from fledermap.web.views.rtc import rtc_bp` alongside the other `from fledermap.web.views...`
  imports.
- Add 3 new parameters to `create_app`'s signature, after `noise_snr_threshold_db: float = 2.0,`:
  ```python
      rtc_enabled: bool = True,
      latitude: float | None = None,
      longitude: float | None = None,
  ```
- After `app.config["NOISE_SNR_THRESHOLD_DB"] = noise_snr_threshold_db`, add:
  ```python
  app.config["RTC_ENABLED"] = rtc_enabled
  app.config["RTC_LOCATION"] = (
      (latitude, longitude) if latitude is not None and longitude is not None else None
  )
  ```
- After `app.jinja_env.globals["display_timezone_name"] = display_timezone_name`, add these two
  new lines (the `app.jinja_env.globals["icon"] = ...` line already exists a few lines above —
  leave it as-is, don't duplicate it):
  ```python
  app.jinja_env.globals["moon_phase_icon"] = make_moon_phase_icon_global(
      Path(__file__).parent / "static" / "icons"
  )
  app.jinja_env.globals["rtc_enabled"] = rtc_enabled
  ```
- Add `app.register_blueprint(rtc_bp)` alongside the other `app.register_blueprint(...)` calls.

In `src/fledermap/cli/main.py`'s `serve` command, add three more positional arguments to the existing `create_app(...)` call, after `config.noise_snr_threshold_db,`:

```python
        config.rtc_enabled,
        config.latitude,
        config.longitude,
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `hatch test tests/test_rtc_view.py -v`
Expected: PASS.

- [ ] **Step 7: Run the full non-db suite to check for regressions**

Run: `hatch test -m "not db" -v`
Expected: PASS.

- [ ] **Step 8: Run the full db suite (controlling session, not a subagent)**

Run: `hatch test -v` with `dangerouslyDisableSandbox: true` (~120s for the PostGIS testcontainer spin-up).
Expected: PASS.

- [ ] **Step 9: Type-check and lint**

Run: `hatch run types:check && hatch fmt`
Expected: no errors.

- [ ] **Step 10: Commit**

```bash
git add src/fledermap/web/views/rtc.py src/fledermap/web/templates/_rtc_content.html \
  src/fledermap/web/app.py src/fledermap/cli/main.py tests/test_rtc_view.py
git commit -m "feat: add GET /rtc-widget view rendering the RTC widget's content"
```

---

## Task 7: Sidebar integration — `_rtc.html`, `_nav.html`, CSS, geolocation JS

**Files:**
- Create: `src/fledermap/web/templates/_rtc.html`
- Create: `src/fledermap/web/static/rtc.js`
- Modify: `src/fledermap/web/templates/_nav.html`
- Modify: `src/fledermap/web/templates/_layout.html`
- Modify: `src/fledermap/web/static/app.css`
- Test: `tests/test_layout.py` (extend)

**Interfaces:**
- Consumes: `rtc_enabled` Jinja global (Task 6), `GET /rtc-widget` (Task 6).

- [ ] **Step 1: Write the failing template test**

Add to `tests/test_layout.py`:

```python
def test_rtc_widget_container_is_present_when_enabled(engine: Engine, tmp_path: Path) -> None:
    app = create_app(engine, tmp_path / "static", tmp_path / "media", rtc_enabled=True)
    client = app.test_client()

    html = client.get("/statistics").get_data(as_text=True)

    assert 'id="rtc-widget"' in html


def test_rtc_widget_container_is_absent_when_disabled(engine: Engine, tmp_path: Path) -> None:
    """Review Focus case: disabling must mean the container never renders at all -- not just
    that the route 404s while a dead container sits in the DOM (which would still mean the
    browser issues the initial hx-get, just to a 404 instead of a 204)."""
    app = create_app(engine, tmp_path / "static", tmp_path / "media", rtc_enabled=False)
    client = app.test_client()

    html = client.get("/statistics").get_data(as_text=True)

    assert 'id="rtc-widget"' not in html
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `hatch test tests/test_layout.py -v`
Expected: FAIL (`_rtc.html` doesn't exist / isn't included yet).

- [ ] **Step 3: Write `_rtc.html`**

Create `src/fledermap/web/templates/_rtc.html`:

```jinja
{# The RTC sidebar widget's htmx container (design spec 2026-09-26-fledermap-rtc-widget-design.md).
   Included from _nav.html only when rtc_enabled is true -- that Jinja-level omission, not the
   /rtc-widget route's 404, is what guarantees no navigator.geolocation call ever happens when
   the feature is disabled. Visually hidden when the sidebar is collapsed via CSS
   (#sidebar.collapsed .rtc-widget in app.css) -- collapsed/expanded is Alpine client state,
   not something Jinja knows about at render time. #}
<div id="rtc-widget"
     class="rtc-widget"
     hx-get="/rtc-widget"
     hx-trigger="load, every 5m"
     hx-swap="innerHTML">
</div>
```

- [ ] **Step 4: Include it from `_nav.html`**

In `src/fledermap/web/templates/_nav.html`, add right before the closing `</nav>` tag:

```jinja
  {% if rtc_enabled %}{% include "_rtc.html" %}{% endif %}
</nav>
```

- [ ] **Step 5: Load `rtc.js`**

In `src/fledermap/web/templates/_layout.html`, add a new script tag right after the `htmx.min.js` line added in Task 3 (non-deferred — it needs to register its `htmx:afterRequest` listener before htmx's own deferred init, though in practice `addEventListener` registration order relative to htmx's init doesn't matter here since the listener fires only on later request completions, not at load; keep it simple and consistent with this file's existing plain non-deferred script pattern used elsewhere in the app, e.g. `app.js`):

```html
  <script src="{{ url_for('static', filename='rtc.js') }}"></script>
```

- [ ] **Step 6: Write `rtc.js`**

Create `src/fledermap/web/static/rtc.js`:

```javascript
// Geolocation fallback for the RTC sidebar widget (design spec
// 2026-09-26-fledermap-rtc-widget-design.md). No pure logic to extract for node:test here --
// this is DOM/browser-API event wiring end to end, covered by live-Chrome verification instead
// (CLAUDE.md's JavaScript tooling section).
document.addEventListener("htmx:afterRequest", function (evt) {
  var widget = document.getElementById("rtc-widget");
  if (!widget || evt.target !== widget) return;
  if (evt.detail.xhr.status !== 204) return;
  if (widget.dataset.geoTried) return;
  widget.dataset.geoTried = "1";
  if (!("geolocation" in navigator)) return;

  navigator.geolocation.getCurrentPosition(
    function (position) {
      var lat = position.coords.latitude;
      var lon = position.coords.longitude;
      window.htmx.ajax("GET", "/rtc-widget?lat=" + lat + "&lon=" + lon, {
        target: "#rtc-widget",
        swap: "innerHTML",
      });
    },
    function () {
      // Denied, unavailable, or timed out: leave the widget empty. No retry -- the next
      // 5-minute poll will hit this same 204 branch, see geoTried already set, and stop here.
    },
  );
});
```

- [ ] **Step 7: Add CSS**

In `src/fledermap/web/static/app.css`, add near the other `#sidebar`/`.sidebar-*` rules:

```css
.rtc-widget {
  margin-top: auto; /* pushes it to the bottom of #sidebar's flex column */
  padding: 0.5rem;
  border-radius: 4px;
  background: var(--color-bg);
  border: 1px solid var(--color-border);
  font-size: 0.8rem;
}
#sidebar.collapsed .rtc-widget { display: none; }
.rtc-widget-content { display: flex; align-items: center; gap: 0.5rem; }
.rtc-icon { flex: 0 0 auto; width: 1.5rem; height: 1.5rem; color: var(--color-accent); }
.rtc-icon svg { width: 100%; height: 100%; }
.rtc-lines { display: flex; flex-direction: column; min-width: 0; }
.rtc-line1 { font-weight: 600; color: var(--color-text); }
.rtc-line2 { color: var(--color-muted); }
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `hatch test tests/test_layout.py -v`
Expected: PASS.

- [ ] **Step 9: Run the full non-db suite**

Run: `hatch test -m "not db" -v`
Expected: PASS.

- [ ] **Step 10: Live-Chrome verification (mandatory for this task — CLAUDE.md's JavaScript tooling section)**

Using the project's established headless-Chrome technique (see the `Headless Chrome live-verification technique` reference and `run` skill), start `fledermap serve` against a real dev DB and verify, at minimum:

1. With `FLEDERMAP_LATITUDE`/`FLEDERMAP_LONGITUDE` set: the widget shows content on load with **no** `navigator.geolocation` call (stub/spy it via `page.evaluateOnNewDocument` and assert it's never invoked).
2. With no location configured and geolocation **granted** (`page.setGeolocationOverride`, or the CDP equivalent Puppeteer wraps, before navigating): the widget starts empty, then a second request fires and content appears with real coordinates.
3. With no location configured and geolocation **denied**: the widget stays empty, and after forcing the 5-minute poll to fire again (e.g. by dispatching `htmx:load` manually, or waiting/advancing time in the harness you're using), geolocation is **not** requested a second time (`data-geo-tried` already set).
4. With `FLEDERMAP_RTC_ENABLED=false`: `document.getElementById("rtc-widget")` is `null`, and `navigator.geolocation.getCurrentPosition` is never called.
5. Resize/collapse the sidebar (click the toggle) and confirm the widget visually disappears.

Report each of the 5 as pass/fail before moving on — do not claim this task complete without having actually run these checks against a real browser, not just inferred from reading the code.

- [ ] **Step 11: Commit**

```bash
git add src/fledermap/web/templates/_rtc.html src/fledermap/web/static/rtc.js \
  src/fledermap/web/templates/_nav.html src/fledermap/web/templates/_layout.html \
  src/fledermap/web/static/app.css tests/test_layout.py
git commit -m "feat: add RTC widget to the sidebar (container, JS, CSS)"
```
