# Consistent Timezone Display Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every timestamp rendered in the Fledermap web UI is explicitly and correctly labeled
with the timezone it's shown in — one consistent zone across the whole app (the Postgres
session's own configured `TimeZone`) — replacing today's unlabeled, silently-assumed display and
the statistics page's actively wrong "server time (UTC)" caption.

**Architecture:** `create_app()` discovers the display timezone once at startup by asking Postgres
directly (`SELECT current_setting('TimeZone')`), caching both the zone name and a `ZoneInfo`
object on `app.config`. Two new Jinja filters (`local_datetime`, `local_date`) close over that
`ZoneInfo` and replace every bare `.strftime()`/`.isoformat()` call across the templates. The
statistics view functions pass the zone name into their templates so the hour-of-day chart's
caption states it correctly (and dynamically); the month chart's now-pointless timezone caveat is
deleted outright.

**Tech Stack:** Flask, Jinja2, SQLAlchemy, `zoneinfo` (stdlib).

**Spec:** `docs/superpowers/specs/2026-09-14-fledermap-timezone-display-design.md`

## Global Constraints

- No schema change, no migration, no new `FLEDERMAP_*` config setting (spec Non-goals, D1, D2).
- The display timezone is discovered from Postgres's `current_setting('TimeZone')`, cached once
  at app startup — not re-queried per request (spec D5).
- `local_datetime` output format: `YYYY-MM-DD HH:MM ZZZ` (e.g. `2026-09-06 20:29 CEST`).
  `local_date` output format: `YYYY-MM-DD`, no zone suffix (spec §2).
- A start–end range renders each end independently through `local_datetime` — never a shared,
  zone-shown-once macro (spec §3).
- The month chart's timezone-caveat sentence and `<details>` are deleted entirely, not reworded
  (spec D4). The hour chart's caption keeps its `<details>` pointer to the backlogged
  sunset-relative feature, only its zone-naming text changes.
- `None` input to either filter renders `"—"` (spec §2).

---

### Task 1: `local_datetime`/`local_date` filters

**Files:**
- Create: `src/fledermap/web/timefmt.py`
- Test: `tests/test_timefmt.py`

**Interfaces:**
- Consumes: nothing from other tasks (pure functions over stdlib `datetime`/`zoneinfo`).
- Produces: `local_datetime(dt: datetime | None, tz: zoneinfo.ZoneInfo) -> str` and
  `local_date(dt: datetime | None, tz: zoneinfo.ZoneInfo) -> str`, both in
  `fledermap.web.timefmt`. Task 2 imports both by name.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_timefmt.py
from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from fledermap.web.timefmt import local_date, local_datetime

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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `hatch test tests/test_timefmt.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'fledermap.web.timefmt'`

- [ ] **Step 3: Write the implementation**

```python
# src/fledermap/web/timefmt.py
"""Timezone-aware timestamp rendering for Jinja templates (design spec
docs/superpowers/specs/2026-09-14-fledermap-timezone-display-design.md).

Every `DateTime(timezone=True)` column comes back from SQLAlchemy already
converted to whatever the Postgres session's `TimeZone` setting is (see the
spec's Problem section for why that's Europe/Berlin on this machine, and why
the *original* per-recording device offset does not survive that round
trip). These functions re-attach a real IANA `ZoneInfo` -- via `astimezone()`,
which changes no instant, only the tzinfo object -- so `%Z` reports the
correct DST-aware abbreviation (CEST vs. CET) for that specific date, rather
than a value cached once and never revisited.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo


def local_datetime(dt: datetime | None, tz: ZoneInfo) -> str:
    """'2026-09-06 20:29 CEST'. `None` (an optional column with no value,
    e.g. a session's `weather`-adjacent nullable timestamps) renders as an
    em dash rather than raising or printing 'None'."""
    if dt is None:
        return "—"
    local = dt.astimezone(tz)
    return f"{local:%Y-%m-%d %H:%M %Z}"


def local_date(dt: datetime | None, tz: ZoneInfo) -> str:
    """'2026-09-06' -- no zone suffix. Used only for `sites_list.html`'s
    date-only column, where a bare calendar date reads fine without one and
    there's no room for it in that table."""
    if dt is None:
        return "—"
    return f"{dt.astimezone(tz):%Y-%m-%d}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `hatch test tests/test_timefmt.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Run mypy**

Run: `hatch run types:check`
Expected: no errors

- [ ] **Step 6: Commit**

```bash
git add src/fledermap/web/timefmt.py tests/test_timefmt.py
git commit -m "feat: add local_datetime/local_date timestamp formatters

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_013mSmeH275QjgBU2FGHt1GZ"
```

---

### Task 2: Discover and register the display timezone in `create_app`

**Files:**
- Modify: `src/fledermap/web/app.py`
- Test: `tests/test_web_app.py`

**Interfaces:**
- Consumes: `local_datetime`, `local_date` from `fledermap.web.timefmt` (Task 1).
- Produces: `app.config["DISPLAY_TIMEZONE_NAME"]` (`str`, e.g. `"Europe/Berlin"`) and
  `app.config["DISPLAY_TIMEZONE"]` (`zoneinfo.ZoneInfo`), plus the `local_datetime`/`local_date`
  Jinja filters, all set inside `create_app`. Task 3 (statistics views) reads
  `app.config["DISPLAY_TIMEZONE_NAME"]`; Task 4 (templates) uses the two filters.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_web_app.py` (already `pytestmark = pytest.mark.db`, already has an `engine`
fixture backed by a real Postgres testcontainer):

```python
import zoneinfo


def test_create_app_discovers_the_display_timezone_from_postgres(
    tmp_path: Path,
    engine: Engine,
) -> None:
    app = create_app(engine, tmp_path / "static", tmp_path / "media")

    # The postgis testcontainer image defaults its cluster TimeZone to UTC
    # (no override is configured anywhere in this test suite) -- asserting
    # the *type* and that it round-trips through ZoneInfo is what matters
    # here, not a hardcoded zone name that would only hold on this one image.
    assert isinstance(app.config["DISPLAY_TIMEZONE_NAME"], str)
    assert app.config["DISPLAY_TIMEZONE"] == zoneinfo.ZoneInfo(
        app.config["DISPLAY_TIMEZONE_NAME"]
    )


def test_create_app_registers_local_datetime_and_local_date_filters(
    tmp_path: Path,
    engine: Engine,
) -> None:
    app = create_app(engine, tmp_path / "static", tmp_path / "media")

    assert "local_datetime" in app.jinja_env.filters
    assert "local_date" in app.jinja_env.filters
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `hatch test tests/test_web_app.py -v -m db` (Docker required —
`dangerouslyDisableSandbox: true`; ~120s for the testcontainer to spin up, per CLAUDE.md's
Environment gotchas — the controlling session runs this itself rather than a dispatched
subagent)
Expected: FAIL with `KeyError: 'DISPLAY_TIMEZONE_NAME'`

- [ ] **Step 3: Implement**

In `src/fledermap/web/app.py`, add imports:

```python
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.orm import Session as OrmSession

from fledermap.web.timefmt import local_date, local_datetime
```

Inside `create_app`, after `app.config["ARCHIVE_ROOTS"] = archive_roots` and before the
`app.jinja_env.filters[...]`/`globals[...]` block, add:

```python
    with OrmSession(engine) as session:
        display_timezone_name = session.execute(
            text("SELECT current_setting('TimeZone')")
        ).scalar_one()
    display_timezone = ZoneInfo(display_timezone_name)
    app.config["DISPLAY_TIMEZONE_NAME"] = display_timezone_name
    app.config["DISPLAY_TIMEZONE"] = display_timezone
```

And add to the existing filters block:

```python
    app.jinja_env.filters["local_datetime"] = lambda dt: local_datetime(
        dt, display_timezone
    )
    app.jinja_env.filters["local_date"] = lambda dt: local_date(dt, display_timezone)
```

Update the module docstring's opening comment to mention the new startup step (one sentence is
enough — the existing docstring style is terse):

```python
"""Flask app factory (design spec section 3/4). `web/app` and `web/views`
both call `services/`, never `store/` directly -- the SPA-migration escape
hatch the parent spec's section 4 documents depends on that boundary holding.

Also discovers the display timezone once at startup from Postgres's own
`current_setting('TimeZone')` (docs/superpowers/specs/
2026-09-14-fledermap-timezone-display-design.md) -- every `DateTime(timezone=True)`
column is already read back in that zone, so this names the existing
behavior rather than introducing a new one.
"""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `hatch test tests/test_web_app.py -v -m db`
Expected: PASS (all tests in the file, including the two new ones)

- [ ] **Step 5: Run mypy**

Run: `hatch run types:check`
Expected: no errors

- [ ] **Step 6: Run the fast suite to make sure nothing else broke**

Run: `hatch test -m "not db"`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/fledermap/web/app.py tests/test_web_app.py
git commit -m "feat: discover display timezone from Postgres at app startup

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_013mSmeH275QjgBU2FGHt1GZ"
```

---

### Task 3: Statistics captions

**Files:**
- Modify: `src/fledermap/web/views/statistics.py`
- Modify: `src/fledermap/web/templates/statistics_global.html`
- Modify: `src/fledermap/web/templates/statistics_site.html`
- Modify: `src/fledermap/web/templates/statistics_species.html`
- Test: `tests/test_statistics_view.py`

**Interfaces:**
- Consumes: `app.config["DISPLAY_TIMEZONE_NAME"]` (Task 2).
- Produces: nothing consumed by later tasks.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_statistics_view.py`, which already imports `Site`, `WKTElement`, `datetime`,
`UTC`, `OrmSession`, `create_app` and has an `engine` fixture (real Postgres testcontainer):

```python
def test_global_statistics_hour_caption_names_the_real_timezone(
    engine: Engine,
    tmp_path: Path,
) -> None:
    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    html = client.get("/statistics").get_data(as_text=True)

    tz_name = app.config["DISPLAY_TIMEZONE_NAME"]
    assert f"Hour of day, {tz_name} time." in html
    assert "server time (UTC)" not in html


def test_global_statistics_month_caption_has_no_timezone_caveat(
    engine: Engine,
    tmp_path: Path,
) -> None:
    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    html = client.get("/statistics").get_data(as_text=True)

    assert "Bucketed by calendar month." in html
    assert "Not adjusted to any local timezone" not in html
    assert "server time (UTC)" not in html


def test_site_statistics_hour_and_month_captions_are_fixed(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        site = Site(
            centroid=WKTElement("POINT(10 50)", srid=4326),
            radius_m=50.0,
            recording_count=1,
            first_at=datetime(2026, 8, 25, tzinfo=UTC),
            last_at=datetime(2026, 8, 25, tzinfo=UTC),
            name="Old Barn",
        )
        session.add(site)
        session.commit()
        site_id = site.id

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    html = client.get(f"/statistics/sites/{site_id}").get_data(as_text=True)

    tz_name = app.config["DISPLAY_TIMEZONE_NAME"]
    assert f"Hour of day, {tz_name} time." in html
    assert "Bucketed by calendar month." in html
    assert "server time (UTC)" not in html
    assert "Not adjusted to any local timezone" not in html


def test_species_statistics_hour_and_month_captions_are_fixed(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        taxon = Taxon(rank="species", scientific_name="Eptesicus serotinus")
        session.add(taxon)
        session.commit()
        taxon_id = taxon.id

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    html = client.get(f"/statistics/species/{taxon_id}").get_data(as_text=True)

    tz_name = app.config["DISPLAY_TIMEZONE_NAME"]
    assert f"Hour of day, {tz_name} time." in html
    assert "Bucketed by calendar month." in html
    assert "server time (UTC)" not in html
    assert "Not adjusted to any local timezone" not in html
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `hatch test tests/test_statistics_view.py -v -m db`
Expected: FAIL — captions still say "server time (UTC)"

- [ ] **Step 3: Implement — view functions**

In `src/fledermap/web/views/statistics.py`, each of the three route functions
(`global_statistics_page`, `species_statistics_page`, `site_statistics_page`) gets one new
template argument. E.g. for `global_statistics_page`:

```python
        html = flask.render_template(
            "statistics_global.html",
            totals=global_totals,
            donut=_breakdown_json(donut),
            rarest=rarest,
            rarest_codes=rarest_codes,
            richest_sites=richest_sites.entries,
            diverse_sites=diverse_sites.entries,
            highest_estimated_richness_sites=highest_estimated_richness_sites.entries,
            least_sampled_sites=least_sampled_sites.entries,
            month=_series_json(month_series),
            hour=_series_json(hour_series),
            display_timezone_name=flask.current_app.config["DISPLAY_TIMEZONE_NAME"],
        )
```

Add the same `display_timezone_name=flask.current_app.config["DISPLAY_TIMEZONE_NAME"]` keyword
argument to the `flask.render_template(...)` calls in `species_statistics_page` and
`site_statistics_page`.

- [ ] **Step 4: Implement — templates**

In each of `statistics_global.html`, `statistics_site.html`, `statistics_species.html`, change:

```html
          <div class="stats-caption">Bucketed by calendar month, server time (UTC). <details><summary>{{ icon("info-circle") }}</summary>Not adjusted to any local timezone.</details></div>
```
to:
```html
          <div class="stats-caption">Bucketed by calendar month.</div>
```

and change:

```html
          <div class="stats-caption">Hour of day, server time (UTC). <details><summary>{{ icon("info-circle") }}</summary>Not sunset-relative (see the backlogged "H:MM before/after sunset" feature).</details></div>
```
to:
```html
          <div class="stats-caption">Hour of day, {{ display_timezone_name }} time. <details><summary>{{ icon("info-circle") }}</summary>Not sunset-relative (see the backlogged "H:MM before/after sunset" feature).</details></div>
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `hatch test tests/test_statistics_view.py -v -m db`
Expected: PASS

- [ ] **Step 6: Run mypy and the fast suite**

Run: `hatch run types:check && hatch test -m "not db"`
Expected: no errors, PASS

- [ ] **Step 7: Commit**

```bash
git add src/fledermap/web/views/statistics.py src/fledermap/web/templates/statistics_global.html src/fledermap/web/templates/statistics_site.html src/fledermap/web/templates/statistics_species.html tests/test_statistics_view.py
git commit -m "fix: statistics hour chart names the real bucketing timezone

The caption claimed UTC; recording_counts_by_hour has always bucketed
by whatever the DB session's TimeZone actually is. The month chart's
timezone caveat is dropped -- a zone shift can't meaningfully move a
recording into a different calendar month at this chart's resolution.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_013mSmeH275QjgBU2FGHt1GZ"
```

---

### Task 4: Template sweep — every remaining bare timestamp display

**Files:**
- Modify: `src/fledermap/web/templates/recording_details.html`
- Modify: `src/fledermap/web/templates/_recording_panel.html`
- Modify: `src/fledermap/web/templates/site_detail.html`
- Modify: `src/fledermap/web/templates/_site_panel.html`
- Modify: `src/fledermap/web/templates/species_detail.html`
- Modify: `src/fledermap/web/templates/map.html`
- Modify: `src/fledermap/web/templates/reviews.html`
- Modify: `src/fledermap/web/templates/sessions_list.html`
- Modify: `src/fledermap/web/templates/session_detail.html`
- Modify: `src/fledermap/web/templates/sites_list.html`
- Test: `tests/test_map_view.py`

**Interfaces:**
- Consumes: `local_datetime`/`local_date` Jinja filters (Task 2). No new Python interfaces.

- [ ] **Step 1: Write the failing tests**

`test_map_view.py` has two assertions on the exact rendered session-dropdown string. Add
`from fledermap.web.timefmt import local_datetime` to the file's imports, then update both tests
to use the filter directly so the expected string doesn't hardcode a zone name that could differ
by environment (the postgis testcontainer's own configured zone, whatever it is).

The existing test `test_session_filter_is_a_dropdown_labelled_by_date_range_and_detector` reads:

```python
def test_session_filter_is_a_dropdown_labelled_by_date_range_and_detector(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        annotation_session = AnnotationSession(
            started_at=datetime(2026, 8, 1, 22, 0, tzinfo=UTC),
            ended_at=datetime(2026, 8, 1, 23, 15, tzinfo=UTC),
            detector_key="ABC123",
        )
        session.add(annotation_session)
        session.commit()
        session_id = annotation_session.id

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    html = client.get("/").get_data(as_text=True)
    assert '<select name="session"' in html
    assert (
        f'<option value="{session_id}">2026-08-01 22:00–23:15 (ABC123)</option>' in html
    )
```

`annotation_session.started_at`/`.ended_at` cannot be read after the `with` block exits —
`session.commit()` expires the instance's attributes, and the session that could re-fetch them is
already closed by then, so reading them afterward raises `DetachedInstanceError`. Capture them as
plain local variables *inside* the `with` block instead (the same reason `session_id` is already
captured there rather than read from `annotation_session.id` after the block):

```python
def test_session_filter_is_a_dropdown_labelled_by_date_range_and_detector(
    engine: Engine,
    tmp_path: Path,
) -> None:
    started_at = datetime(2026, 8, 1, 22, 0, tzinfo=UTC)
    ended_at = datetime(2026, 8, 1, 23, 15, tzinfo=UTC)
    with OrmSession(engine) as session:
        annotation_session = AnnotationSession(
            started_at=started_at,
            ended_at=ended_at,
            detector_key="ABC123",
        )
        session.add(annotation_session)
        session.commit()
        session_id = annotation_session.id

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    html = client.get("/").get_data(as_text=True)
    display_tz = app.config["DISPLAY_TIMEZONE"]
    expected_start = local_datetime(started_at, display_tz)
    expected_end = local_datetime(ended_at, display_tz)
    assert '<select name="session"' in html
    assert (
        f'<option value="{session_id}">{expected_start}–{expected_end} (ABC123)</option>'
        in html
    )
```

The existing `test_session_option_falls_back_when_detector_key_is_missing` (shown in full in this
task's context above) keeps its setup unchanged; only its final assertion changes:

```python
    html = client.get("/").get_data(as_text=True)
    display_tz = app.config["DISPLAY_TIMEZONE"]
    started_at = datetime(2026, 8, 1, 22, 0, tzinfo=UTC)
    ended_at = datetime(2026, 8, 1, 23, 15, tzinfo=UTC)
    expected = (
        f"{local_datetime(started_at, display_tz)}–{local_datetime(ended_at, display_tz)} "
        "(unknown detector)"
    )
    assert expected in html
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `hatch test tests/test_map_view.py -v -m db -k "session_filter_is_a_dropdown or session_option_falls_back"`
Expected: FAIL — templates still render the old `HH:MM–HH:MM` shape with no zone suffix

- [ ] **Step 3: Implement — template edits**

`recording_details.html`, line with `{{ recording.recorded_at.isoformat() }} — {{ recording.make }} {{ recording.model }}`:
```html
      {{ recording.recorded_at | local_datetime }} — {{ recording.make }} {{ recording.model }}
```

`_recording_panel.html`, line 126 `<p>{{ recording.recorded_at.isoformat() }}</p>`:
```html
    <p>{{ recording.recorded_at | local_datetime }}</p>
```

`_recording_panel.html`, line 132:
```html
    <p>Session: <a href="/sessions/{{ recording_session.id }}">{{ recording_session.started_at | local_datetime }}–{{ recording_session.ended_at | local_datetime }} ({{ recording_session.detector_key | detector_label }})</a></p>
```

`site_detail.html`, line 57:
```html
          <td><a href="/sessions/{{ s.id }}">{{ s.started_at | local_datetime }}–{{ s.ended_at | local_datetime }}</a></td>
```

`_site_panel.html`, line 37:
```html
      <li><a href="/sessions/{{ s.id }}">{{ s.started_at | local_datetime }}–{{ s.ended_at | local_datetime }}</a></li>
```

`species_detail.html`, line 53:
```html
          <td><a href="/recordings/{{ recording.audio_hash }}?return_to=/species/{{ detail.taxon.id }}">{{ recording.recorded_at | local_datetime }}</a></td>
```

`map.html`, line 34:
```html
          <option value="{{ item.id }}">{{ item.started_at | local_datetime }}–{{ item.ended_at | local_datetime }} ({{ item.detector_key | detector_label }})</option>
```

`reviews.html`, line 22:
```html
          <td>{{ row.recorded_at | local_datetime }}</td>
```

`sessions_list.html`, line 53:
```html
                {{ row.session.started_at | local_datetime }}–{{ row.session.ended_at | local_datetime }}
```

`session_detail.html`, line 10:
```html
      Session: {{ detail.session.started_at | local_datetime }} – {{ detail.session.ended_at | local_datetime }}
```

`session_detail.html`, line 53:
```html
        ({{ op.counterpart.started_at | local_datetime }}–{{ op.counterpart.ended_at | local_datetime }}).
```

`session_detail.html`, line 84:
```html
          <td><a href="/recordings/{{ recording.audio_hash }}?return_to=/sessions/{{ detail.session.id }}">{{ recording.recorded_at | local_datetime }}</a></td>
```

`sites_list.html`, line 21:
```html
          <td>{{ site.last_at | local_date }}</td>
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `hatch test tests/test_map_view.py -v -m db -k "session_filter_is_a_dropdown or session_option_falls_back"`
Expected: PASS

- [ ] **Step 5: Run the full `db`-marked suite (the controlling session runs this itself — do not
  dispatch a subagent to wait on it, per CLAUDE.md's Environment gotchas)**

Run: `hatch test -m db` (Docker required, `dangerouslyDisableSandbox: true`, ~120s)
Expected: PASS. If any other test fails on a rendered timestamp string this sweep didn't
anticipate, that's a real gap in this task, not a pre-existing issue to note and move past —
find it (grep the failing test's assertion) and fix the assertion the same way as Step 1 above.

- [ ] **Step 6: Run the fast suite and mypy**

Run: `hatch test -m "not db" && hatch run types:check`
Expected: PASS, no errors

- [ ] **Step 7: Run `hatch fmt`**

Run: `hatch fmt`
Expected: no changes needed, or only whitespace/import-order fixes from Step 3's Python edits in
the test file

- [ ] **Step 8: Commit**

```bash
git add src/fledermap/web/templates/recording_details.html src/fledermap/web/templates/_recording_panel.html src/fledermap/web/templates/site_detail.html src/fledermap/web/templates/_site_panel.html src/fledermap/web/templates/species_detail.html src/fledermap/web/templates/map.html src/fledermap/web/templates/reviews.html src/fledermap/web/templates/sessions_list.html src/fledermap/web/templates/session_detail.html src/fledermap/web/templates/sites_list.html tests/test_map_view.py
git commit -m "fix: label every displayed timestamp with its actual timezone

Replaces every bare .strftime()/.isoformat() call across the app's
templates with the local_datetime/local_date filters, so what was
previously an unlabeled, silently-assumed zone is now explicit and
consistent everywhere.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_013mSmeH275QjgBU2FGHt1GZ"
```

---

### Task 5: Full verification pass

**Files:** none (verification only)

**Interfaces:** none.

- [ ] **Step 1: Run the complete test suite**

Run: `hatch test` (includes `-m db`, `dangerouslyDisableSandbox: true`, ~120s for the
testcontainer)
Expected: all tests PASS, zero warnings (a warning is a defect per CLAUDE.md's Tooling section —
investigate and fix the cause rather than adding a `filterwarnings` ignore)

- [ ] **Step 2: Run mypy over src, tests, and scripts**

Run: `hatch run types:check`
Expected: no errors

- [ ] **Step 3: Run hatch fmt**

Run: `hatch fmt`
Expected: no changes (ruff check --fix + format clean)

- [ ] **Step 4: Manually verify against the real dev instance**

Deploy per `project-fledermap-systemd-install-on-this-machine` (pipx install --force, then
restart the systemd service) and load `/statistics`, a recording details page, and a session
details page in a browser. Confirm every timestamp shows a trailing zone abbreviation
(`CEST`/`CET` on this machine, since its Postgres is configured `Europe/Berlin`) and the hour
chart's caption names that same zone. This is a UI change — per CLAUDE.md's "UI consistency
process," this is exactly the kind of change that should be checked live, not just through
`hatch test`.

- [ ] **Step 5: Confirm no leftover raw `.strftime()`/`.isoformat()` calls on a stored timestamp**

Run: `grep -rn "recorded_at\.\(strftime\|isoformat\)\|started_at\.\(strftime\|isoformat\)\|ended_at\.\(strftime\|isoformat\)\|last_at\.\(strftime\|isoformat\)\|first_seen_at\.\(strftime\|isoformat\)" src/fledermap/web/templates/`
Expected: no matches (everything now goes through `local_datetime`/`local_date`)
