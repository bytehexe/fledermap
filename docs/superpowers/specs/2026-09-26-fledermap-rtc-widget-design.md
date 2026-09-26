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
  widget never renders, `/rtc-widget` isn't reachable (see below), and — critically — the frontend
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

## Backend route (`web/views/rtc.py`, not `web/api/`)

**Correction from an earlier draft of this spec**, caught while writing the implementation plan:
this originally specified a JSON-returning `/api/rtc` endpoint consumed by hand-rolled htmx-driven
JS. But every existing `/api/*` route in this codebase (`web/api/geojson.py`) serves GeoJSON to
Leaflet — a genuinely different consumer with its own parsing needs. Every htmx-consumed piece of
content elsewhere in the app is a server-rendered HTML fragment from `web/views/` (e.g.
`entities.py`'s `_site_panel.html`). A JSON API here would mean reinventing htmx's fragment-swap
model with bespoke client-side JS for no reason, against the grain of the rest of the app. Fixed:
this is a view route returning a rendered HTML fragment, and the state→icon/text mapping happens
in Python/Jinja, not client-side JS.

`GET /rtc-widget?lat=<float>&lon=<float>` (both query params optional; `rtc_bp`, its own blueprint,
no `url_prefix`, registered alongside `views_bp` etc. in `create_app`):

- If `app.config["RTC_ENABLED"]` is false: `404` (the route doesn't meaningfully exist; the
  frontend never calls it in this case regardless — see "Frontend" below).
- If `app.config["RTC_LOCATION"]` is set (from `Config.latitude`/`longitude`): compute using that,
  **ignoring** any `lat`/`lon` query params (server config always wins).
- Else if `lat`/`lon` query params are both present and parse as floats: compute using those.
- Else (no configured location, no query params): `204 No Content` — htmx no-ops on `204` by
  default (no swap, nothing rendered), so this doubles as both "no location yet, try browser
  geolocation" and "nothing to show," with no separate empty-fragment case needed.
- On success: `200` rendering `_rtc_content.html` (a small fragment, not a full page) with the
  computed state, next-transition label/time, and (only for `night`/`always_night`) which
  moon-phase icon file to use — all decided in `services/sun.py`/this view, not in the template
  itself beyond picking which icon/text row to render (see the content table below).

## Frontend widget

Lives in a new `_rtc.html` partial (the sidebar's htmx-driven container), included from
`_nav.html` unconditionally when `app.jinja_env.globals["rtc_enabled"]` is true (a Jinja `{% if
%}`, checked once per render, not a runtime fetch that then no-ops) — this is what actually
guarantees no geolocation prompt when the feature is disabled, rather than relying on the route's
`404`. Visually hidden entirely when the sidebar is collapsed (CSS, `#sidebar.collapsed
.rtc-widget { display: none; }` — the same mechanism `_nav.html`'s other collapsed-state rules
already use, since "expanded vs. collapsed" is Alpine client state, not something Jinja knows at
render time).

`_rtc.html` itself is just the container: `<div id="rtc-widget" hx-get="/rtc-widget"
hx-trigger="load, every 5m" hx-swap="innerHTML"></div>`. A successful response replaces its
contents with the rendered `_rtc_content.html` fragment (icon + text, per the table below); a
`204` leaves it empty.

On the initial load (no `lat`/`lon` params), an empty result (`204`) means "try geolocation": a
small inline script listens for htmx's `htmx:afterRequest` on `#rtc-widget`, and if the response
was `204` **and** this is the first attempt (a `data-geo-tried` flag, so a later 5-minute poll
with genuinely no location configured doesn't re-prompt every cycle), calls
`navigator.geolocation.getCurrentPosition`, then re-issues the htmx request with `lat`/`lon`
appended on success. On denial/unavailability/timeout, no further request is made and the widget
area stays empty (matches "hidden, not an error").

**Content, one compact block, single icon + up to two lines of text:**

| State | Icon | Line 1 | Line 2 |
|---|---|---|---|
| `day` | Tabler `sun` (outline) | "Daylight" | "Sunset `HH:MM`" |
| `dawn` | Tabler `sunrise` (outline) | "Dawn" | "Sunrise `HH:MM`" |
| `dusk` | Tabler `sunset` (outline) | "Dusk" | "Night from `HH:MM`" (= today's `dusk` time) |
| `night` | first-party moon-phase icon (see below) | "Night" | "Dawn `HH:MM`" |
| `extreme_latitude: always_day` | Tabler `sun` (outline) | "Polar day" | *(none)* |
| `extreme_latitude: always_night` | first-party moon-phase icon | "Polar night" | *(none)* |

No phase name or illumination percentage is spelled out in text — narrow sidebar real estate
doesn't comfortably fit it alongside the timing info, which is the widget's primary job. The
moon-phase icon itself (see below) is what conveys phase during `night`/`always_night`; the API
still returns `phase_name`/`illumination_pct` (see "API" below) for the frontend to pick the right
icon file, they're just not rendered as their own text.

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
- The phase-bucket-to-icon-name mapping is a small pure function in `services/sun.py`, covered by
  a plain Python unit test (not JS — the `GET /rtc-widget` view decides the icon file server-side,
  same as every other Jinja-rendered decision in this app; no DOM/Leaflet involvement, so no
  live-Chrome verification is needed for this feature at all).
- `tests/test_rtc_view.py` (`pytest.mark.db`, since `create_app` always needs a real DB connection
  to discover `DISPLAY_TIMEZONE` at startup — see "Timezone" above): the route's status-code
  branching (enabled/disabled, configured vs. query-param vs. neither location, extreme latitude),
  using Flask's test client, same style as `tests/test_geojson_api.py`.
