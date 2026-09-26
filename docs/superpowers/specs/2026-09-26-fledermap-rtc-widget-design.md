# Fledermap RTC (sun/moon planning widget) — Design

**Status:** draft
**Date:** 2026-09-26

## Problem

The Obsidian backlog's "RTC" ("real time clock") item asks for a sidebar app showing the current
time relative to sunset/sunrise, with a sun/moon visual. Talking it through with Janna narrowed
the actual value: not ambient decoration (she can look out the window to judge "right now"), but
**planning** — today's sunset/dusk/dawn/sunrise window tells her when it's worth heading out to
record tonight, before the fact. Moon brightness is included on the same planning basis, per
Janna's own call, without this spec taking any position on how much it actually affects bat
activity. It's only worth computing *because* the widget already shows a sun/moon icon for
current state; if it didn't, moon phase would be out of scope entirely.

## Goals

- A small widget in the sidebar (`_nav.html`, so on every page) showing: current state (day / dawn
  / dusk / night), the next relevant transition time, and — only during the night state — the
  current moon phase and illumination.
- Location comes from server config (preferred, no permission prompt) or the browser's own
  geolocation (fallback, only when server config is absent and the feature is enabled).
- Server-side computation (`services/sun.py`, via the `astral` library), consistent with how this
  project already separates query/computation logic (`services/`) from rendering.
- Explicitly disableable via config, with no computation and no geolocation prompt when disabled.

## Non-goals

- A "best time to record tonight" recommendation/score. This shows the raw times; Janna makes the
  call herself.
- Historical or multi-day moon/sun data. Today only.
- Any per-recording use of this data (e.g. flagging a recording as "made during a full moon"). A
  future feature, not this one — see the Obsidian backlog's own scoping.
- Timezone-per-location derivation (e.g. via `timezonefinder`). Times display in the app's
  existing `DISPLAY_TIMEZONE` (see "Timezone" below), not a zone derived from the lat/lon.

## Config

Three new `Config` fields/env vars, following the existing `_lookup`/`_KNOWN_FILE_KEYS`/
`ConfigError` pattern in `config.py`:

- `FLEDERMAP_RTC_ENABLED` (bool, default `true`). `false` disables the feature outright: the
  widget never renders, `/api/rtc` isn't reachable (see below), and — critically — the frontend
  never calls `navigator.geolocation`, so no browser permission prompt appears even if latitude/
  longitude are also unset.
- `FLEDERMAP_LATITUDE` / `FLEDERMAP_LONGITUDE` (floats). Both-or-neither: setting exactly one is a
  `ConfigError` at startup (same shape as `battybirdnet_enabled`/`battybirdnet_url`). When both are
  set, they always win over browser geolocation and no geolocation is ever attempted — an operator
  who's configured a location shouldn't see a permission prompt either. When neither is set (and
  the feature is enabled), the frontend falls back to the browser's geolocation API.

## Timezone

Reuses `app.config["DISPLAY_TIMEZONE"]` (`web/app.py`, discovered once at startup from Postgres's
own configured timezone) — the same timezone already used for `local_date`/`local_time` and the
"Hour of day, {{ display_timezone_name }} time" captions on the stats pages. **Not**
`Config.default_timezone`, which is a distinct, narrower thing: an ingest-time fallback used only
when no source file on a recording carries its own UTC-offset evidence. Reusing the wrong one
would make the widget disagree with every other timestamp displayed in the app.

## Computation (`services/sun.py`)

Given `(lat: float, lon: float, today: date, tz: tzinfo)`:

- `astral.sun.sun(observer, date=today, tzinfo=tz)` → a dict with `dawn`/`sunrise`/`noon`/`sunset`/
  `dusk` (civil twilight, astral's default solar depression), already localized to `tz`.
- `astral.moon.phase(date=today)` → a float in `[0, 27.99]` (0/28 = new moon, ~14 = full).
  Converted to:
  - an **illumination fraction**: `(1 - cos(2π · phase / 28)) / 2` — a standard cosine
    approximation, not astronomically exact to the arcsecond, but more than sufficient for a UI
    cue.
  - a **phase bucket** (one of 8, see "Moon phase icons" below), by dividing `[0, 28)` into 8 equal
    ranges of 3.5, centred so 0 falls in the middle of "new moon" and 14 in the middle of "full
    moon".
- **Current state bucket**: comparing "now" (in `tz`) against today's `dawn`/`sunrise`/`sunset`/
  `dusk` gives one of `night` / `dawn` / `day` / `dusk`. "Now" before `dawn` or after `dusk` is
  `night`; between `dawn` and `sunrise` is `dawn`; between `sunrise` and `sunset` is `day`; between
  `sunset` and `dusk` is `dusk`.
- **Extreme latitude**: `astral.sun.sun()` raises when the sun doesn't rise or set that day (polar
  latitudes — unlikely given this project's EU/NA species scope, but not impossible). Caught and
  surfaced as a distinct state (see "Widget content" below) rather than propagated as a 500.

## API (`web/api/rtc.py`)

`GET /api/rtc?lat=<float>&lon=<float>` (both query params optional):

- If `FLEDERMAP_RTC_ENABLED` is false: `404` (the route doesn't meaningfully exist; the frontend
  never calls it in this case regardless — see "Frontend" below).
- If `Config.latitude`/`longitude` are set: compute using those, **ignoring** any `lat`/`lon` query
  params (server config always wins).
- Else if `lat`/`lon` query params are both present and valid: compute using those.
- Else (`Config` unset, no query params): `204 No Content` — tells the frontend "no location yet,
  try browser geolocation."
- On success: `200` with JSON `{state, next_transition: {label, time_iso} | null, moon: {phase_name,
  illumination_pct} | null, extreme_latitude: "always_day" | "always_night" | null}`.
  `next_transition`/`moon` are `null` together with a non-null `extreme_latitude`, since there's no
  "next" transition and no meaningful state that day.

## Frontend widget

Lives in a new `_rtc.html` partial, included from `_nav.html` **only in the expanded sidebar
state** (hidden entirely when collapsed — no icon-only fallback). htmx-driven:
`hx-get="/api/rtc" hx-trigger="load, every 5m"`.

If `FLEDERMAP_RTC_ENABLED` is false, `_nav.html` doesn't include `_rtc.html` at all (a
Jinja-level `{% if %}`, not a runtime fetch that then no-ops) — this is what actually guarantees no
geolocation prompt, rather than relying on the endpoint's 404.

On the initial load (no `lat`/`lon` params), a `204` response means "try geolocation": a small
inline script calls `navigator.geolocation.getCurrentPosition`, and on success re-issues the htmx
request with `lat`/`lon` appended. On denial/unavailability/timeout, no further request is made and
the widget area stays empty (matches "hidden, not an error").

**Content, one compact block, single icon + up to two lines of text:**

| State | Icon | Line 1 | Line 2 |
|---|---|---|---|
| `day` | Tabler `sun` (outline) | "Daylight" | "Sunset `HH:MM`" |
| `dawn` | Tabler `sunrise` (outline) | "Dawn" | "Sunrise `HH:MM`" |
| `dusk` | Tabler `sunset` (outline) | "Dusk" | "Night from `HH:MM`" (= today's `dusk` time) |
| `night` | first-party moon-phase icon (see below) | "Night" | "`Phase name` · `NN`% lit · Dawn `HH:MM`" |
| `extreme_latitude: always_day` | Tabler `sun` (outline) | "Sun doesn't set today" | *(none)* |
| `extreme_latitude: always_night` | first-party moon-phase icon | "Polar night" | "`Phase name` · `NN`% lit" |

Moon phase/illumination only ever appears during `night` or `always_night` — exactly the state
where it's decision-relevant (whether tonight's moon will suppress activity), matching the
"only because we're already showing a sun/moon icon" scope decision above.

## Moon phase icons

Tabler's icon set (`icon()`, `web/icons.py`) has no distinct moon-*phase* icons (`moon`, `moon-2`,
`moon-off`, `moon-stars` exist; no waxing/waning/quarter/gibbous variants) — confirmed against the
vendored set's file listing. Rather than fall back to emoji (`docs/style-guide.md`'s "No emoji or
Unicode dingbats in the UI" rule forbids this outright) or a filled/solid moon shape that would
look stylistically foreign next to Tabler's pure-line-art icons, 8 phase icons are hand-authored to
match Tabler's own outline style exactly: `viewBox="0 0 24 24"`, `stroke="currentColor"`,
`stroke-width="2"`, `stroke-linecap="round"`, `stroke-linejoin="round"`, `fill="none"` — a circle
outline for the disc plus one terminator-curve path, the same technique Tabler's own `moon.svg`
uses for its single crescent.

The 8: new, waxing crescent, first quarter, waxing gibbous, full, waning gibbous, last quarter,
waning crescent.

These **cannot** live in `vendor_assets.py`'s fetched/hash-verified directory — that directory's
entire invariant is "these are genuine files fetched from Tabler's CDN," and mixing in hand-drawn
ones would break any future re-verification of it. They ship instead as first-party static assets
(`web/static/icons/moon-phase-<name>.svg`, checked into the repo, not fetched at runtime), looked up
by a small dedicated helper alongside — but separate from — `icons.py`'s `icon()` global.

## Testing

- `tests/test_sun.py`: ground-truth tests against a real date/location with published reference
  sunrise/sunset/moon-phase values (e.g. from the `astral` project's own test suite or
  timeanddate.com), asserting `services/sun.py`'s output matches within a small tolerance — same
  style as `test_spectrogram.py`'s known-tone tests. Includes one case exercising the
  extreme-latitude path (a real polar-day/polar-night date/location) to confirm it's caught and
  surfaced as `extreme_latitude`, not propagated as an exception.
- `tests/test_config.py`: the both-or-neither `latitude`/`longitude` validation, and
  `rtc_enabled`'s default/parsing.
- The phase-bucket-to-icon-name mapping is a small pure function — covered by `node:test` if it
  ends up in JS (state → icon name is presentational), or a plain Python unit test if it stays
  server-side as part of the `/api/rtc` response shape (`phase_name` computed in `services/sun.py`,
  icon file chosen client-side by name — no DOM/Leaflet involvement, so no live-Chrome
  verification is needed for this feature at all).
