# Fledermap — Consistent Timezone Display — Design

**Status:** design, not yet implemented
**Date:** 2026-09-14

## Problem

Timestamps across the UI are displayed inconsistently, and in one place actively mislabeled.

`recorded_at`/`filename_at`/`metadata_at` (and every other `DateTime(timezone=True)` column —
`started_at`/`ended_at`, `last_at`, `first_seen_at`, etc.) are tz-aware. `ingest/merge.py` goes to
real effort to attach each recording's own device-reported UTC offset to `recorded_at` before it's
written (D17). None of that offset survives storage: Postgres's `TIMESTAMPTZ` type never stores an
offset — it converts to an absolute UTC instant on write, and on every read re-expresses that
instant in whatever the connection's session `TimeZone` setting is. `create_engine()`
(`store/db.py`) never sets one, so every read falls back to the server's configured default —
verified live: `/etc/postgresql/16/main/postgresql.conf` has `timezone = 'Europe/Berlin'`, which is
why `recording_details.html` currently shows something like `2026-09-06T20:29:53+02:00`. That
value is Berlin's civil offset for that date (CEST, +02:00 in summer, +01:00 in winter) — not the
originating device's own offset, and not labeled as Berlin's at all. It happens to look plausible
for a recording actually made in Germany during CEST season, and would be silently wrong (with no
indication anything is off) for a recording made anywhere else, or read in winter.

The three statistics pages (`statistics_global.html`/`statistics_site.html`/
`statistics_species.html`) compound this: their "Recordings per hour of day" caption explicitly
claims `"server time (UTC)"`, but `recording_counts_by_hour`'s `bucket_of=lambda r:
r.recorded_at.hour` (`services/statistics.py`) reads `recorded_at` the same way every other query
does — already converted to the session's configured zone (Berlin), not UTC. The caption is simply
false today, not just imprecise.

Every other individual-timestamp display (recording/session/site details, drawer panels, the
sessions list, the map's session dropdown, the reviews list) renders the same session-zone value
via bare `.strftime()`/`.isoformat()`, with no timezone indication of any kind — a reader has no
way to tell what zone they're looking at, or that every one of these values is actually already in
the same zone as every other.

## Goals

- Every rendered timestamp in the UI is explicitly and correctly labeled with the zone it's shown
  in — no bare, ambiguous date/time strings.
- That zone is a single, consistent one across the whole app: the Postgres session's own
  configured `TimeZone` (discovered at runtime, not hardcoded, not a new config setting), since
  that's what every stored value is already expressed in the moment SQLAlchemy reads it.
- The "Recordings per hour of day" caption states the zone it actually buckets by, correctly and
  dynamically — not a fixed, wrong "UTC" string.
- The "Recordings per month" caption's timezone-uncertainty sentence is removed — a timezone
  shift cannot meaningfully move a recording into a different calendar month for this project's
  purposes, so the caveat has no reader-relevant content.

## Non-goals

- **No new column, no ingest-path change, no migration.** The per-device offset `ingest/merge.py`
  computes and stores is real and stays (D17 still governs `recorded_at`'s precedence), but making
  it independently recoverable after the `TIMESTAMPTZ` round-trip is a different, larger project
  than this one. This spec's fix is display-only: label what's already there correctly, in one
  consistent zone.
- **No new `FLEDERMAP_*` config setting for the display timezone.** Reusing or shadowing
  `default_timezone`/`FLEDERMAP_DEFAULT_TIMEZONE` would conflate two unrelated concerns — that
  setting is D17's ingest-time fallback for a recording with no offset evidence at all, not a
  display preference. Introducing a second, independent display-timezone setting risks it silently
  disagreeing with the DB's actual session zone, which is strictly worse than today's implicit (if
  unlabeled) behavior. The display zone is discovered from Postgres itself.
- **No sunset-relative hour-of-day chart.** Deferred to the already-backlogged "H:MM
  before/after sunset" feature — real, but a separate design (needs per-site sunset times) from
  "the current caption is wrong."
- **No change to how `recorded_at`/`filename_at`/`metadata_at` are computed or stored.**

## Design

### 1. Discovering the display timezone

At `create_app()` time (`web/app.py`), query the engine once:

```python
with OrmSession(engine) as session:
    tz_name = session.execute(text("SELECT current_setting('TimeZone')")).scalar_one()
app.config["DISPLAY_TIMEZONE_NAME"] = tz_name
app.config["DISPLAY_TIMEZONE"] = zoneinfo.ZoneInfo(tz_name)
```

This is exactly the zone every `TIMESTAMPTZ` column is already being read back in — not a new
behavior, just naming the existing one. It self-adjusts if the server's configured zone ever
changes (a fresh deployment, a container defaulting to UTC) with no code change needed.

### 2. Rendering: two Jinja filters

New module `web/timefmt.py`, registered in `web/app.py` next to `detector_label`:

```python
def local_datetime(dt: datetime | None, tz: zoneinfo.ZoneInfo) -> str:
    """'2026-09-06 20:29 CEST'. `dt` is already tz-aware (whatever fixed
    offset the DB session attached); re-attaching a real IANA zone via
    astimezone() doesn't change the instant, it only lets tzname() report
    the correct DST-aware abbreviation for that date."""
    if dt is None:
        return "—"
    local = dt.astimezone(tz)
    return f"{local:%Y-%m-%d %H:%M} {local:%Z}"


def local_date(dt: datetime | None, tz: zoneinfo.ZoneInfo) -> str:
    """'2026-09-06' -- no zone suffix; a bare calendar date reads fine
    without one and sites_list.html's column has no room for it."""
    if dt is None:
        return "—"
    return f"{dt.astimezone(tz):%Y-%m-%d}"
```

Registered as Jinja filters that close over `app.config["DISPLAY_TIMEZONE"]`, the same pattern
`make_icon_global` already uses for `icon()`:

```python
app.jinja_env.filters["local_datetime"] = lambda dt: local_datetime(dt, display_tz)
app.jinja_env.filters["local_date"] = lambda dt: local_date(dt, display_tz)
```

### 3. Template sweep

Every bare `.strftime(...)`/`.isoformat()` call on a stored timestamp becomes `| local_datetime`
(or `| local_date` for `sites_list.html`'s date-only column):

| Template | Current | New |
|---|---|---|
| `recording_details.html` | `recording.recorded_at.isoformat()` | `recording.recorded_at \| local_datetime` |
| `_recording_panel.html` | `recording.recorded_at.isoformat()` | `recording.recorded_at \| local_datetime` |
| `_recording_panel.html` | `recording_session.started_at.strftime(...)`–`.ended_at.strftime('%H:%M')` | `recording_session.started_at \| local_datetime` – `recording_session.ended_at \| local_datetime` |
| `site_detail.html` | `s.started_at.strftime(...)`–`s.ended_at.strftime('%H:%M')` | same pattern as above |
| `_site_panel.html` | same shape | same pattern |
| `species_detail.html` | `recording.recorded_at.strftime(...)` | `recording.recorded_at \| local_datetime` |
| `map.html` | session dropdown `item.started_at.strftime(...)`–`.ended_at.strftime('%H:%M')` | same pattern |
| `reviews.html` | `row.recorded_at.strftime(...)` | `row.recorded_at \| local_datetime` |
| `sessions_list.html` | `row.session.started_at.strftime(...)`–`.ended_at.strftime('%H:%M')` | same pattern |
| `session_detail.html` | three occurrences, same started/ended shape | same pattern |
| `sites_list.html` | `site.last_at.strftime('%Y-%m-%d')` | `site.last_at \| local_date` |

A start–end range (e.g. `session_detail.html`) renders both ends through `local_datetime`
independently rather than trying to print the zone abbreviation only once — the two ends could in
principle straddle a DST transition, and repeating a two-argument `{% macro %}` for "start–end,
zone shown once, unless it changed mid-session" is more machinery than this problem needs. Each
end is unambiguous on its own; that's the bar, not the shortest possible string.

### 4. Statistics captions

`recording_counts_by_hour`'s `bucket_of=lambda r: r.recorded_at.hour` is unchanged — it was
already correctly bucketing by the DB session's local hour, only the caption calling it "UTC" was
wrong. The three statistics view functions (`web/views/statistics.py`) pass
`display_timezone_name=flask.current_app.config["DISPLAY_TIMEZONE_NAME"]` into each template
(`statistics_global.html`/`statistics_site.html`/`statistics_species.html`), which interpolate it:

```
Hour of day, {{ display_timezone_name }} time. <details>...Not sunset-relative
(see the backlogged "H:MM before/after sunset" feature).</details>
```

The month chart's caption drops its timezone sentence and `<details>` entirely, leaving just
`Bucketed by calendar month.`

### 5. Test impact

- New unit tests for `local_datetime`/`local_date` in `tests/test_timefmt.py`: a fixed-offset
  input converted through a real `ZoneInfo` (e.g. `Europe/Berlin`) renders `CEST` for a
  summer date and `CET` for a winter date from the *same* function — proving the DST-aware
  abbreviation actually depends on the date, not a static label. Also: `None` input.
- A test that `create_app` populates `app.config["DISPLAY_TIMEZONE_NAME"]` from
  `current_setting('TimeZone')` (`db`-marked, real Postgres).
- Every existing view/template test asserting on a specific rendered timestamp string
  (`test_statistics_view.py`, `test_sessions_view.py`, `test_entities_view.py`,
  `test_map_view.py`, `test_recording_detail_view.py`, `test_reviews_view.py`, ...) needs its
  expected string updated to the new `YYYY-MM-DD HH:MM ZZZ` shape — a grep for `.strftime(` /
  `isoformat()` expectations in `tests/` before implementation finds the full list, per the
  project's "grep for every reader" rule.

## Decisions

- **D1 — display timezone is discovered from Postgres (`current_setting('TimeZone')`), not a new
  `FLEDERMAP_*` setting.** Rationale: Non-goals above — avoids a second timezone concept that
  could disagree with what's actually stored, and self-adjusts across deployments.
- **D2 — no schema change, no offset column.** The per-device offset D17 computes at ingest time
  stays real but unrecoverable after storage; recovering it is a separate, larger project than
  "the UI's timezone labeling is wrong and inconsistent." Flagged explicitly rather than silently
  narrowed.
- **D3 — sunset-relative hour-of-day chart deferred**, not built now. YAGNI: it needs per-site
  sunset times, a separate design, and the caption-correctness fix here doesn't depend on it.
- **D4 — month chart's timezone caveat is deleted, not reworded.** A timezone shift changes which
  calendar month a recording falls into only in an edge-of-month, edge-of-day case irrelevant at
  this chart's resolution — the caveat has no actionable content for a reader.

## Open items

None — brainstorming converged; ready for `writing-plans`.
