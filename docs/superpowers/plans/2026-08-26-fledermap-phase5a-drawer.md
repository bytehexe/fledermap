# Fledermap Phase 5a (Recording & Site Detail Drawer) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Phase 4's native Leaflet popup with a bottom drawer that shows a
full recording or site detail panel, fetched via HTMX, wired into the existing
Alpine/vanilla-JS/HTMX split.

**Architecture:** Two new HTMX fragment routes (`/recordings/{hash}/panel`,
`/sites/{id}/panel`) render Jinja partials from small new service-layer queries
(neighbor lookup, site detail). A drawer container added to `map.html` is driven by
Alpine for open/collapsed/closed state; `app.js` gains the marker-click → HTMX wiring
and a small event bridge so prev/next navigation can pan the Leaflet map without HTMX
touching Leaflet directly.

**Tech Stack:** Flask, Jinja2, SQLAlchemy, HTMX 2.0.3, Alpine.js, Leaflet — all
already in place from Phases 1–4. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-08-26-fledermap-phase5a-drawer-design.md`
(and the parent `docs/superpowers/specs/2026-08-23-fledermap-design.md` §9, and
`docs/superpowers/specs/2026-08-25-fledermap-phase4-map-design.md` for the
patterns this plan continues).

## Global Constraints

- `web/api` and `web/views` call `services/`, never `store/` directly (parent spec §4
  boundary, `web/app.py`'s own docstring).
- No JS test framework (Phase 4 decision, P4 design doc §9) — JS changes are verified
  manually via the `run` skill, not by an automated test.
- Every `Recording`/`Site` filter query goes through `services/map_query.py`'s
  existing `filtered_recordings`/`filtered_sites` — no second, parallel filtering
  implementation.
- `hatch test`, `hatch fmt --check`, and `hatch run types:check` must all be clean
  after every task (project's own quality bar, `CLAUDE.md` "Tooling").
- Prev/next excludes `bbox` from its filter set (design spec P5a-9) — every other
  active filter (`from`, `to`, `taxon`, `verdict`, `session`, `source`) applies.

**Note found while planning, not in the design spec:** the design doc assumes
spectrograms/previews are servable under `/media/` (parent spec §9 already names this
route), but no phase has ever built it — `web/app.py` has no media blueprint, and
`create_app` doesn't even receive a `media_root`. Task 3 below builds this; without
it the recording panel would have nothing to point an `<img>`/`<audio>` tag at.

---

## Task 1: Shared query-param parsing (`web/params.py`)

Both the new panel routes and the existing GeoJSON API parse the same filter
querystring shape. `web/api/geojson.py` currently defines that parsing as
module-private functions — extract them to a shared module so the panel routes
(Task 6/7) reuse them instead of duplicating.

**Files:**
- Create: `src/fledermap/web/params.py`
- Modify: `src/fledermap/web/api/geojson.py`

**Interfaces:**
- Produces: `parse_bbox(raw: str | None) -> BBox | None`, `parse_datetime(raw: str | None, *, end_of_day: bool = False) -> datetime | None`, `parse_verdict(raw: str | None) -> Verdict | Literal["all"] | None`, `parse_int(raw: str | None) -> int | None`, `fallback_site_label(point: tuple[float, float] | None) -> str` — all in `fledermap.web.params`.

- [ ] **Step 1: Create `web/params.py` with the extracted functions**

```python
# src/fledermap/web/params.py
"""Query-string parsing shared by the GeoJSON API and the drawer's panel
fragment routes -- both parse the same filter shape (design spec
2026-08-26-fledermap-phase5a-drawer-design.md), and duplicating it would let
the two silently drift on a param's exact meaning (e.g. `to`'s end-of-day
handling)."""

from __future__ import annotations

from datetime import UTC, datetime, time
from typing import Literal

from fledermap.domain.codes import Verdict

BBox = tuple[float, float, float, float]  # (min_lon, min_lat, max_lon, max_lat)


def parse_bbox(raw: str | None) -> BBox | None:
    if raw is None:
        return None
    parts = raw.split(",")
    msg = "bbox must be 4 comma-separated numbers: min_lon,min_lat,max_lon,max_lat"
    if len(parts) != 4:
        raise ValueError(msg)
    try:
        min_lon, min_lat, max_lon, max_lat = (float(p) for p in parts)
    except ValueError:
        raise ValueError(msg) from None
    return (min_lon, min_lat, max_lon, max_lat)


def parse_datetime(raw: str | None, *, end_of_day: bool = False) -> datetime | None:
    """Parse a `from`/`to` query-param value.

    Deliberate, minimal INTERIM policy for the `<input type="date">` case --
    NOT a resolution of the project's open timezone question (spec D17,
    risks R1-R3), just a decision not to make it silently worse here. A bare
    `YYYY-MM-DD` value (no time component) is anchored to UTC rather than
    left naive (which would make the boundary depend on the Postgres session
    timezone against `Recording.recorded_at`'s `DateTime(timezone=True)`
    column), and when it's the `to` bound it's treated as the END of that day
    (23:59:59.999999 UTC) rather than midnight -- otherwise `to=2026-08-25`
    would silently exclude every recording from the 25th itself. A value that
    already carries a time and/or offset is left exactly as authored.
    """
    if not raw:
        return None
    parsed = datetime.fromisoformat(raw)
    is_bare_date = len(raw) == 10 and "T" not in raw
    if is_bare_date:
        if end_of_day:
            parsed = datetime.combine(parsed.date(), time(23, 59, 59, 999999))
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def parse_verdict(raw: str | None) -> Verdict | Literal["all"] | None:
    if raw is None:
        return None
    if raw == "all":
        return "all"
    return Verdict(raw)


def parse_int(raw: str | None) -> int | None:
    return int(raw) if raw else None


def fallback_site_label(point: tuple[float, float] | None) -> str:
    """P4-1: Site.name is unpopulated until poiidx naming ships as its own
    task -- fall back to a rounded-coordinate label rather than block on
    that unrelated integration."""
    if point is None:
        return "Site"
    lon, lat = point
    return f"{lat:.4f}, {lon:.4f}"
```

- [ ] **Step 2: Point `geojson.py` at the shared module and delete the local copies**

Replace the `_parse_bbox`/`_parse_datetime`/`_parse_verdict`/`_parse_int`/
`_fallback_site_label` definitions (currently `geojson.py` lines 26–84) with:

```python
from fledermap.web.params import (
    parse_bbox,
    parse_datetime,
    parse_verdict,
    parse_int,
    fallback_site_label,
)
```

and update every call site in `geojson.py` to drop the leading underscore
(`_parse_bbox(...)` → `parse_bbox(...)`, etc., `_fallback_site_label` →
`fallback_site_label`).

- [ ] **Step 3: Run the existing GeoJSON API tests — this is a pure refactor, nothing should change**

Run: `hatch test tests/test_geojson_api.py -v`
Expected: all tests PASS, unchanged from before the refactor.

- [ ] **Step 4: Lint and type-check**

Run: `hatch fmt --check && hatch run types:check`
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add src/fledermap/web/params.py src/fledermap/web/api/geojson.py
git commit -m "refactor: extract shared query-param parsing to web/params.py"
```

---

## Task 2: `site_id` filter on `filtered_recordings`/`filtered_sites`

**Correction to the design spec, found while planning:** P5a-7 says "show only this
site" drives "the *existing* session/site filter state already wired up in Phase
4... no new filtering code path." Checked against the actual Phase 4 code while
writing this plan (`services/map_query.py`, `web/api/geojson.py`,
`web/templates/map.html`) — Phase 4 built a `session_id` filter, but **no `site_id`
filter exists anywhere**: not on `filtered_recordings`, not on `filtered_sites`, not
as a query param, not as a form field. The design's intent still holds (site
filtering should be *shaped* like every other filter, sharing the same form/query/
service-layer mechanism) — the spec was simply wrong that the mechanism already
existed. This task builds it, following the exact pattern `session_id` already
established rather than inventing a new one.

**Files:**
- Modify: `src/fledermap/services/map_query.py`
- Modify: `src/fledermap/web/api/geojson.py`
- Modify: `src/fledermap/web/templates/map.html`
- Modify: `src/fledermap/web/static/app.js`
- Test: `tests/test_map_query.py`, `tests/test_geojson_api.py`

**Interfaces:**
- Consumes: `Recording.site_id`, `Site.id` (`store/models.py`, both already exist).
- Produces: `filtered_recordings(..., site_id: int | None = None)`, `filtered_sites(..., site_id: int | None = None)` — new keyword-only params, default `None` (no behavior change when omitted).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_map_query.py -- add near the other filtered_recordings tests
def test_filtered_recordings_by_site(engine: Engine) -> None:
    with OrmSession(engine) as session:
        site = Site(
            centroid=WKTElement("POINT(10 50)", srid=4326),
            radius_m=50.0,
            recording_count=1,
            first_at=datetime(2026, 8, 25, tzinfo=UTC),
            last_at=datetime(2026, 8, 25, tzinfo=UTC),
        )
        session.add(site)
        session.flush()
        at_site = _recording(session, audio_hash="a" * 64)
        at_site.site_id = site.id
        elsewhere = _recording(session, audio_hash="b" * 64)
        session.add_all([at_site, elsewhere])
        session.commit()
        site_id = site.id

        results = filtered_recordings(session, site_id=site_id, verdict="all")

        assert {r.audio_hash for r in results} == {"a" * 64}


def test_filtered_sites_by_id(engine: Engine) -> None:
    with OrmSession(engine) as session:
        wanted = Site(
            centroid=WKTElement("POINT(10 50)", srid=4326),
            radius_m=50.0, recording_count=1,
            first_at=datetime(2026, 8, 25, tzinfo=UTC),
            last_at=datetime(2026, 8, 25, tzinfo=UTC),
        )
        other = Site(
            centroid=WKTElement("POINT(11 51)", srid=4326),
            radius_m=50.0, recording_count=1,
            first_at=datetime(2026, 8, 25, tzinfo=UTC),
            last_at=datetime(2026, 8, 25, tzinfo=UTC),
        )
        session.add_all([wanted, other])
        session.commit()
        wanted_id = wanted.id

        results = filtered_sites(session, site_id=wanted_id)

        assert [s.id for s in results] == [wanted_id]
```

(`_recording` is the existing helper already defined at the top of
`tests/test_map_query.py` — reuse it, don't redefine it. Both tests use
`pytestmark = pytest.mark.db`, already set at module level in that file.)

- [ ] **Step 2: Run to verify they fail**

Run: `hatch test tests/test_map_query.py -k "by_site or by_id" -v`
Expected: FAIL — `filtered_recordings()`/`filtered_sites()` got an unexpected keyword
argument `site_id`.

- [ ] **Step 3: Add the parameter to both functions**

In `services/map_query.py`, `filtered_recordings`:

```python
def filtered_recordings(
    session: OrmSession,
    *,
    bbox: BBox | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    taxon_id: int | None = None,
    verdict: Verdict | Literal["all"] | None = None,
    session_id: int | None = None,
    site_id: int | None = None,
    source: IdSource | None = None,
) -> Sequence[Recording]:
    stmt = select(Recording).where(Recording.missing_since.is_(None))
    if date_from is not None:
        stmt = stmt.where(Recording.recorded_at >= date_from)
    if date_to is not None:
        stmt = stmt.where(Recording.recorded_at <= date_to)
    if session_id is not None:
        stmt = stmt.where(Recording.session_id == session_id)
    if site_id is not None:
        stmt = stmt.where(Recording.site_id == site_id)
    if source is not None:
        ...  # unchanged
```

(Only the signature and the one new `if site_id is not None:` block change; every
other line of the function's body stays exactly as it is today.)

`filtered_sites`:

```python
def filtered_sites(
    session: OrmSession,
    *,
    bbox: BBox | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    site_id: int | None = None,
) -> Sequence[Site]:
    stmt = select(Site)
    if date_from is not None:
        stmt = stmt.where(Site.last_at >= date_from)
    if date_to is not None:
        stmt = stmt.where(Site.first_at <= date_to)
    if site_id is not None:
        stmt = stmt.where(Site.id == site_id)
    ...  # unchanged bbox filtering below
```

- [ ] **Step 4: Run to verify they pass**

Run: `hatch test tests/test_map_query.py -k "by_site or by_id" -v`
Expected: PASS.

- [ ] **Step 5: Wire `site` into the GeoJSON API's query params**

In `web/api/geojson.py`, both `recordings_geojson` and `sites_geojson` gain:

```python
        site_id = parse_int(flask.request.args.get("site"))
```

added to their existing `try:` blocks (alongside `taxon_id = parse_int(...)`), and
`site_id=site_id` added to their respective `filtered_recordings(...)` /
`filtered_sites(...)` calls.

- [ ] **Step 6: Write the API-level test**

```python
# tests/test_geojson_api.py
def test_recordings_geojson_filters_by_site(engine: Engine, tmp_path: Path) -> None:
    with OrmSession(engine) as session:
        site = Site(
            centroid=WKTElement("POINT(10 50)", srid=4326),
            radius_m=50.0, recording_count=1,
            first_at=datetime(2026, 8, 25, tzinfo=UTC),
            last_at=datetime(2026, 8, 25, tzinfo=UTC),
        )
        session.add(site)
        session.flush()
        at_site = Recording(
            audio_hash="a" * 64, path="a.wav",
            recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
            geom=WKTElement("POINT(10 50)", srid=4326),
            site_id=site.id,
        )
        elsewhere = Recording(
            audio_hash="b" * 64, path="b.wav",
            recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
            geom=WKTElement("POINT(11 51)", srid=4326),
        )
        session.add_all([at_site, elsewhere])
        session.commit()
        site_id = site.id

    client = _app_client(engine, tmp_path)
    response = client.get(f"/api/recordings.geojson?verdict=all&site={site_id}")

    hashes = {f["properties"]["audio_hash"] for f in response.get_json()["features"]}
    assert hashes == {"a" * 64}
```

- [ ] **Step 7: Run it**

Run: `hatch test tests/test_geojson_api.py -k filters_by_site -v`
Expected: PASS.

- [ ] **Step 8: Add the hidden `site` field to the filter form and its Alpine state**

`web/templates/map.html`, inside `<form id="filters">` (alongside the other filter
inputs, order doesn't matter since it's hidden):

```html
    <input type="hidden" name="site" x-model="site">
```

`web/static/app.js`, `filterForm()`:

```javascript
function filterForm() {
  return {
    from: "", to: "", taxon: "", session: "", source: "", verdict: "", site: "",
  };
}
```

No visible UI for this field yet — Task 9 adds the button that sets it. Adding it
now keeps this task's diff to "the filter dimension exists," separate from "a button
uses it."

- [ ] **Step 9: Lint, type-check, full suite**

Run: `hatch fmt --check && hatch run types:check && hatch test`
Expected: clean, all passing.

- [ ] **Step 10: Commit**

```bash
git add src/fledermap/services/map_query.py src/fledermap/web/api/geojson.py \
        src/fledermap/web/templates/map.html src/fledermap/web/static/app.js \
        tests/test_map_query.py tests/test_geojson_api.py
git commit -m "feat: filter recordings and sites by site_id"
```

---

## Task 3: Serve derived media (`/media/<hash>/spectrogram.webp`, `/media/<hash>/preview.opus`)

Not in the design spec's prose (it assumes this already exists, per the parent
spec's §9) — this is the missing plumbing the recording panel needs to point an
`<img>`/`<audio>` tag at anything.

**Files:**
- Create: `src/fledermap/web/views/media.py`
- Modify: `src/fledermap/web/app.py`
- Modify: `src/fledermap/cli/main.py`
- Modify: `tests/test_geojson_api.py`, `tests/test_map_view.py`, `tests/test_web_app.py` (every existing `create_app(...)` call site)
- Test: `tests/test_media_view.py` (new)

**Interfaces:**
- Consumes: `fledermap.media.paths.spectrogram_path`, `fledermap.media.paths.preview_path` (both already exist, `media/paths.py`).
- Produces: `create_app(engine: Engine, static_root: Path, media_root: Path) -> flask.Flask` (media_root is a new **required** third positional parameter — matches `static_root`'s existing required-positional treatment, not optional, since every caller must supply a real value); Flask endpoints `media.spectrogram`, `media.preview`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_media_view.py
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session as OrmSession

from fledermap.media.paths import preview_path, spectrogram_path
from fledermap.store.models import Recording
from fledermap.web.app import create_app

pytestmark = pytest.mark.db


def test_spectrogram_serves_existing_file(engine: Engine, tmp_path: Path) -> None:
    media_root = tmp_path / "media"
    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="a" * 64,
                path="a.wav",
                recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
            ),
        )
        session.commit()

    path = spectrogram_path(media_root, "a" * 64)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"fake-webp-bytes")

    app = create_app(engine, tmp_path / "static", media_root)
    client = app.test_client()
    response = client.get(f"/media/{'a' * 64}/spectrogram.webp")

    assert response.status_code == 200
    assert response.data == b"fake-webp-bytes"


def test_spectrogram_404s_when_not_yet_rendered(engine: Engine, tmp_path: Path) -> None:
    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="b" * 64,
                path="b.wav",
                recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
            ),
        )
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().get(f"/media/{'b' * 64}/spectrogram.webp")

    assert response.status_code == 404


def test_spectrogram_404s_for_unknown_hash(engine: Engine, tmp_path: Path) -> None:
    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().get(f"/media/{'c' * 64}/spectrogram.webp")

    assert response.status_code == 404


def test_preview_serves_existing_file(engine: Engine, tmp_path: Path) -> None:
    media_root = tmp_path / "media"
    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="d" * 64,
                path="d.wav",
                recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
            ),
        )
        session.commit()

    path = preview_path(media_root, "d" * 64)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"fake-opus-bytes")

    app = create_app(engine, tmp_path / "static", media_root)
    response = app.test_client().get(f"/media/{'d' * 64}/preview.opus")

    assert response.status_code == 200
    assert response.data == b"fake-opus-bytes"
```

- [ ] **Step 2: Run to verify failure**

Run: `hatch test tests/test_media_view.py -v`
Expected: FAIL — `create_app() takes 2 positional arguments but 3 were given`
(or a 404 from a route that doesn't exist yet, once the signature is patched enough
to import; either way, not a pass).

- [ ] **Step 3: Create the media blueprint**

```python
# src/fledermap/web/views/media.py
"""Serves derived media (spectrograms, audio previews) written under
`Config.media_root` by the jobs in `jobs/tasks.py` (design spec section 8;
parent spec section 9 names this route but no phase had built it yet).

Every route resolves `audio_hash` against the `Recording` table BEFORE
touching the filesystem -- not just to 404 for an unknown recording, but
because it's the path-traversal guard: `media/paths.py`'s helpers join
`audio_hash` directly into a filesystem path, and a hash that doesn't match
any real `Recording` row never reaches them.
"""

from __future__ import annotations

import flask
from flask.typing import ResponseReturnValue
from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from fledermap.media.paths import preview_path, spectrogram_path
from fledermap.store.models import Recording

media_bp = flask.Blueprint("media", __name__)


def _known_hash(session: OrmSession, audio_hash: str) -> bool:
    return (
        session.scalars(
            select(Recording.id).where(Recording.audio_hash == audio_hash),
        ).first()
        is not None
    )


@media_bp.get("/media/<audio_hash>/spectrogram.webp")
def spectrogram(audio_hash: str) -> ResponseReturnValue:
    engine = flask.current_app.config["ENGINE"]
    media_root = flask.current_app.config["MEDIA_ROOT"]
    with OrmSession(engine) as session:
        if not _known_hash(session, audio_hash):
            flask.abort(404)
    path = spectrogram_path(media_root, audio_hash)
    if not path.exists():
        flask.abort(404)
    return flask.send_file(path, mimetype="image/webp")


@media_bp.get("/media/<audio_hash>/preview.opus")
def preview(audio_hash: str) -> ResponseReturnValue:
    engine = flask.current_app.config["ENGINE"]
    media_root = flask.current_app.config["MEDIA_ROOT"]
    with OrmSession(engine) as session:
        if not _known_hash(session, audio_hash):
            flask.abort(404)
    path = preview_path(media_root, audio_hash)
    if not path.exists():
        flask.abort(404)
    return flask.send_file(path, mimetype="audio/opus")
```

- [ ] **Step 4: Wire it into `create_app`**

```python
# src/fledermap/web/app.py
from fledermap.web.views.media import media_bp

def create_app(engine: Engine, static_root: Path, media_root: Path) -> flask.Flask:
    app = flask.Flask(__name__)
    app.config["ENGINE"] = engine
    app.config["MEDIA_ROOT"] = media_root

    vendor_bp = flask.Blueprint(
        "vendor",
        __name__,
        static_folder=str(static_root / "vendor"),
        static_url_path="/static/vendor",
    )
    app.register_blueprint(vendor_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(views_bp)
    app.register_blueprint(media_bp)
    return app
```

(Update the docstring's parameter description to mention `media_root` alongside
`static_root`.)

- [ ] **Step 5: Update the real call site**

`src/fledermap/cli/main.py`, `serve` command:

```python
    app = create_app(engine, config.static_root, config.media_root)
```

- [ ] **Step 6: Update every other existing test call site**

`tests/test_geojson_api.py`'s `_app_client` helper:

```python
def _app_client(engine: Engine, tmp_path: Path) -> flask.testing.FlaskClient:
    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    return app.test_client()
```

`tests/test_map_view.py` and `tests/test_web_app.py`: every direct
`create_app(engine, tmp_path / "static")` call becomes
`create_app(engine, tmp_path / "static", tmp_path / "media")`. Grep for
`create_app(` in both files to find every call site — do not assume there is only
one per file.

- [ ] **Step 7: Run the new test and the full suite**

Run: `hatch test tests/test_media_view.py -v`
Expected: PASS, all four cases.

Run: `hatch test`
Expected: all passing — this step touches every `create_app` call site in the repo,
so the full suite is the real check, not just the new file.

- [ ] **Step 8: Lint and type-check**

Run: `hatch fmt --check && hatch run types:check`
Expected: clean.

- [ ] **Step 9: Commit**

```bash
git add src/fledermap/web/views/media.py src/fledermap/web/app.py \
        src/fledermap/cli/main.py tests/test_media_view.py \
        tests/test_geojson_api.py tests/test_map_view.py tests/test_web_app.py
git commit -m "feat: serve derived media under /media/<hash>/"
```

---

## Task 4: `neighbor_recordings` (prev/next lookup)

Pure function — no DB access itself, operates on an already-filtered,
already-fetched list (design spec P5a-9's ordering: date/taxon/verdict/session/
source applied, `bbox` excluded). Keeping it pure makes it trivial to unit-test
without a database.

**Files:**
- Modify: `src/fledermap/services/map_query.py`
- Test: `tests/test_map_query.py`

**Interfaces:**
- Consumes: `Sequence[Recording]` (anything `filtered_recordings` returns), `Recording.audio_hash`, `Recording.recorded_at`.
- Produces: `neighbor_recordings(recordings: Sequence[Recording], audio_hash: str) -> tuple[Recording | None, Recording | None] | None`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_map_query.py
from fledermap.services.map_query import neighbor_recordings  # add to existing import block


def _bare_recording(audio_hash: str, recorded_at: datetime) -> Recording:
    """A Recording that's never touched a session -- neighbor_recordings only
    reads audio_hash/recorded_at, so no DB round trip is needed to test it."""
    return Recording(audio_hash=audio_hash, path=f"{audio_hash}.wav", recorded_at=recorded_at)


def test_neighbor_recordings_finds_both_sides() -> None:
    early = _bare_recording("a" * 64, datetime(2026, 8, 25, 20, 0, tzinfo=UTC))
    middle = _bare_recording("b" * 64, datetime(2026, 8, 25, 21, 0, tzinfo=UTC))
    late = _bare_recording("c" * 64, datetime(2026, 8, 25, 22, 0, tzinfo=UTC))

    result = neighbor_recordings([late, early, middle], "b" * 64)

    assert result is not None
    previous, next_ = result
    assert previous is not None and previous.audio_hash == "a" * 64
    assert next_ is not None and next_.audio_hash == "c" * 64


def test_neighbor_recordings_stops_at_the_start() -> None:
    early = _bare_recording("a" * 64, datetime(2026, 8, 25, 20, 0, tzinfo=UTC))
    late = _bare_recording("b" * 64, datetime(2026, 8, 25, 21, 0, tzinfo=UTC))

    result = neighbor_recordings([early, late], "a" * 64)

    assert result == (None, late)


def test_neighbor_recordings_stops_at_the_end() -> None:
    early = _bare_recording("a" * 64, datetime(2026, 8, 25, 20, 0, tzinfo=UTC))
    late = _bare_recording("b" * 64, datetime(2026, 8, 25, 21, 0, tzinfo=UTC))

    result = neighbor_recordings([early, late], "b" * 64)

    assert result == (early, None)


def test_neighbor_recordings_none_when_hash_not_in_set() -> None:
    present = _bare_recording("a" * 64, datetime(2026, 8, 25, 20, 0, tzinfo=UTC))

    assert neighbor_recordings([present], "z" * 64) is None
```

- [ ] **Step 2: Run to verify failure**

Run: `hatch test tests/test_map_query.py -k neighbor_recordings -v`
Expected: FAIL — `ImportError: cannot import name 'neighbor_recordings'`.

- [ ] **Step 3: Implement it**

```python
# src/fledermap/services/map_query.py -- add below filtered_recordings
def neighbor_recordings(
    recordings: Sequence[Recording],
    audio_hash: str,
) -> tuple[Recording | None, Recording | None] | None:
    """The (previous, next) recording relative to `audio_hash` within
    `recordings`, ordered by `recorded_at` -- `recordings` must already be
    the filtered set the drawer is showing (design spec P5a-9: same filters
    as the map, minus bbox). Either side is `None` at a boundary (no
    wrap-around, P5a-10). Returns `None` entirely if `audio_hash` isn't in
    `recordings` at all -- e.g. the filters changed while the drawer was
    open and this recording no longer matches; the caller treats that the
    same as 'not found'."""
    ordered = sorted(recordings, key=lambda r: r.recorded_at)
    index = next(
        (i for i, r in enumerate(ordered) if r.audio_hash == audio_hash),
        None,
    )
    if index is None:
        return None
    previous = ordered[index - 1] if index > 0 else None
    following = ordered[index + 1] if index < len(ordered) - 1 else None
    return previous, following
```

- [ ] **Step 4: Run to verify they pass**

Run: `hatch test tests/test_map_query.py -k neighbor_recordings -v`
Expected: PASS, all four.

- [ ] **Step 5: Lint and type-check**

Run: `hatch fmt --check && hatch run types:check`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add src/fledermap/services/map_query.py tests/test_map_query.py
git commit -m "feat: add neighbor_recordings for drawer prev/next"
```

---

## Task 5: `site_detail` (species breakdown, stats, sessions for a site)

**Files:**
- Modify: `src/fledermap/services/map_query.py`
- Test: `tests/test_map_query.py`

**Interfaces:**
- Consumes: `Recording.site_id`, `Recording.session_id`, `current_best_identification` (`services/current_best.py`, already exists), `Session` model (imported as `AnnotationSession` per existing convention in this file).
- Produces: `SiteDetail` dataclass (`site: Site`, `species_counts: list[tuple[Taxon, int]]`, `sessions: Sequence[AnnotationSession]`), `site_detail(session: OrmSession, site_id: int) -> SiteDetail | None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_map_query.py
from fledermap.services.map_query import SiteDetail, site_detail  # add to import block


def test_site_detail_breaks_down_species_and_lists_sessions(engine: Engine) -> None:
    with OrmSession(engine) as session:
        site = Site(
            centroid=WKTElement("POINT(10 50)", srid=4326),
            radius_m=50.0, recording_count=2,
            first_at=datetime(2026, 8, 25, tzinfo=UTC),
            last_at=datetime(2026, 8, 25, tzinfo=UTC),
        )
        taxon = Taxon(rank="species", scientific_name="Eptesicus serotinus")
        annotation_session = AnnotationSession(
            started_at=datetime(2026, 8, 25, 20, 0, tzinfo=UTC),
            ended_at=datetime(2026, 8, 25, 23, 0, tzinfo=UTC),
        )
        session.add_all([site, taxon, annotation_session])
        session.flush()

        r1 = _recording(
            session, audio_hash="a" * 64, taxon_id=taxon.id, session_id=annotation_session.id,
        )
        r1.site_id = site.id
        r2 = _recording(
            session, audio_hash="b" * 64, taxon_id=taxon.id, session_id=annotation_session.id,
        )
        r2.site_id = site.id
        session.add_all([r1, r2])
        session.commit()
        site_id, taxon_id, session_id = site.id, taxon.id, annotation_session.id

        detail = site_detail(session, site_id)

        assert detail is not None
        assert detail.site.id == site_id
        assert detail.species_counts == [(session.get(Taxon, taxon_id), 2)]
        assert [s.id for s in detail.sessions] == [session_id]


def test_site_detail_returns_none_for_unknown_site(engine: Engine) -> None:
    with OrmSession(engine) as session:
        assert site_detail(session, 999999) is None
```

(Uses the existing `_recording` helper — it already accepts `taxon_id` and
`session_id` keyword arguments per its current signature at the top of this file;
`site_id` is set as a plain attribute afterward since `_recording` doesn't take it.)

- [ ] **Step 2: Run to verify failure**

Run: `hatch test tests/test_map_query.py -k site_detail -v`
Expected: FAIL — `ImportError: cannot import name 'site_detail'`.

- [ ] **Step 3: Implement it**

```python
# src/fledermap/services/map_query.py
from dataclasses import dataclass  # add to existing imports

from fledermap.services.current_best import current_best_identification  # add to existing imports


@dataclass(frozen=True)
class SiteDetail:
    """Everything the site drawer panel needs, assembled in one query pass
    rather than the template making N+1 lookups."""

    site: Site
    species_counts: list[tuple[Taxon, int]]
    sessions: Sequence[AnnotationSession]


def site_detail(session: OrmSession, site_id: int) -> SiteDetail | None:
    site = session.get(Site, site_id)
    if site is None:
        return None

    recordings = session.scalars(
        select(Recording).where(
            Recording.site_id == site_id,
            Recording.missing_since.is_(None),
        ),
    ).all()

    counts: dict[int, int] = {}
    for recording in recordings:
        best = current_best_identification(recording)
        if best is not None and best.taxon_id is not None:
            counts[best.taxon_id] = counts.get(best.taxon_id, 0) + 1

    taxa_by_id = {}
    if counts:
        taxa_by_id = {
            t.id: t
            for t in session.scalars(
                select(Taxon).where(Taxon.id.in_(counts)),
            ).all()
        }
    species_counts = sorted(
        ((taxa_by_id[tid], n) for tid, n in counts.items()),
        key=lambda pair: -pair[1],
    )

    session_ids = {r.session_id for r in recordings if r.session_id is not None}
    sessions: Sequence[AnnotationSession] = []
    if session_ids:
        sessions = session.scalars(
            select(AnnotationSession)
            .where(AnnotationSession.id.in_(session_ids))
            .order_by(AnnotationSession.started_at.desc()),
        ).all()

    return SiteDetail(site=site, species_counts=species_counts, sessions=sessions)
```

- [ ] **Step 4: Run to verify they pass**

Run: `hatch test tests/test_map_query.py -k site_detail -v`
Expected: PASS.

- [ ] **Step 5: Lint and type-check**

Run: `hatch fmt --check && hatch run types:check`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add src/fledermap/services/map_query.py tests/test_map_query.py
git commit -m "feat: add site_detail for the site drawer panel"
```

---

## Task 6: Recording panel route and template

**Files:**
- Modify: `src/fledermap/web/views/map.py`
- Create: `src/fledermap/web/templates/_recording_panel.html`
- Test: `tests/test_map_view.py`

**Interfaces:**
- Consumes: `filtered_recordings`, `neighbor_recordings` (Task 4), `current_best_identification`, `decode_point`, `parse_datetime`/`parse_verdict`/`parse_int` (Task 1), `spectrogram_path`/`preview_path` (`media/paths.py`).
- Produces: `GET /recordings/<audio_hash>/panel` — HTML fragment, 200 always (not-found is rendered content, per design spec §7), `HX-Trigger: {"recording-selected": {...}}` header when the recording has a location.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_map_view.py
import json

from fledermap.domain.codes import IdSource, Verdict
from fledermap.store.models import Identification, Recording, Taxon
from geoalchemy2.elements import WKTElement


def test_recording_panel_renders_identification_and_metadata(
    engine: Engine, tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        taxon = Taxon(rank="species", scientific_name="Eptesicus serotinus")
        session.add(taxon)
        session.flush()
        recording = Recording(
            audio_hash="a" * 64, path="a.wav",
            recorded_at=datetime(2026, 8, 25, 21, 0, tzinfo=UTC),
            geom=WKTElement("POINT(10 50)", srid=4326),
        )
        session.add(recording)
        session.flush()
        session.add(
            Identification(
                recording_id=recording.id, source=IdSource.EMT_GUANO,
                source_version=None, verdict=Verdict.SPECIES,
                taxon_id=taxon.id, raw_label="EPTSER",
                first_seen_at=datetime(2026, 8, 25, 21, 0, tzinfo=UTC),
            ),
        )
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().get(f"/recordings/{'a' * 64}/panel")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Eptesicus serotinus" in html
    trigger = json.loads(response.headers["HX-Trigger"])
    assert trigger["recording-selected"]["hash"] == "a" * 64


def test_recording_panel_not_found_renders_gracefully(
    engine: Engine, tmp_path: Path,
) -> None:
    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().get(f"/recordings/{'z' * 64}/panel")

    assert response.status_code == 200
    assert "not found" in response.get_data(as_text=True).lower()
    assert "HX-Trigger" not in response.headers


def test_recording_panel_degrades_when_media_not_rendered(
    engine: Engine, tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="b" * 64, path="b.wav",
                recorded_at=datetime(2026, 8, 25, 21, 0, tzinfo=UTC),
            ),
        )
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    html = app.test_client().get(f"/recordings/{'b' * 64}/panel").get_data(as_text=True)

    assert "not processed yet" in html.lower()


def test_recording_panel_shows_prev_next_within_filters(
    engine: Engine, tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        early = Recording(
            audio_hash="a" * 64, path="a.wav",
            recorded_at=datetime(2026, 8, 25, 20, 0, tzinfo=UTC),
        )
        middle = Recording(
            audio_hash="b" * 64, path="b.wav",
            recorded_at=datetime(2026, 8, 25, 21, 0, tzinfo=UTC),
        )
        late = Recording(
            audio_hash="c" * 64, path="c.wav",
            recorded_at=datetime(2026, 8, 25, 22, 0, tzinfo=UTC),
        )
        session.add_all([early, middle, late])
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    html = app.test_client().get(
        f"/recordings/{'b' * 64}/panel?verdict=all",
    ).get_data(as_text=True)

    assert f"/recordings/{'a' * 64}/panel" in html
    assert f"/recordings/{'c' * 64}/panel" in html
```

- [ ] **Step 2: Run to verify failure**

Run: `hatch test tests/test_map_view.py -k recording_panel -v`
Expected: FAIL — 404 (route doesn't exist yet).

- [ ] **Step 3: Create the template**

```html
{# src/fledermap/web/templates/_recording_panel.html #}
{% if not found %}
<p>Recording not found — it may have been removed, or no longer matches the active filters.</p>
{% else %}
<div class="panel-header">
  <h2>{{ taxon.scientific_name if taxon else (best.verdict.value if best else "unidentified") }}</h2>
</div>

{% if spectrogram_ready %}
<img class="spectrogram" src="{{ url_for('media.spectrogram', audio_hash=recording.audio_hash) }}" alt="Spectrogram">
{% else %}
<p class="media-placeholder">Spectrogram not processed yet.</p>
{% endif %}

<div class="audio-row">
  {% if preview_ready %}
  <audio controls src="{{ url_for('media.preview', audio_hash=recording.audio_hash) }}"></audio>
  {% else %}
  <p class="media-placeholder">Audio preview not processed yet.</p>
  {% endif %}
</div>

<div class="panel-columns">
  <div class="col identifications">
    <h3>Identifications</h3>
    <ul>
      {% for ident in recording.identifications %}
      <li{% if ident.superseded_at %} class="superseded"{% endif %}>
        {{ ident.source.value }}: {{ ident.raw_label or ident.verdict.value }}
      </li>
      {% endfor %}
    </ul>
  </div>
  <div class="col recording-meta">
    <h3>Recording</h3>
    <p>{{ recording.recorded_at.isoformat() }}</p>
    <p>{{ recording.make }} {{ recording.model }}</p>
  </div>
  <div class="col context">
    <h3>Context</h3>
    {% if previous %}
    <button type="button" hx-get="/recordings/{{ previous.audio_hash }}/panel?{{ filter_qs }}" hx-target="#drawer-body">← Previous</button>
    {% endif %}
    {% if next %}
    <button type="button" hx-get="/recordings/{{ next.audio_hash }}/panel?{{ filter_qs }}" hx-target="#drawer-body">Next →</button>
    {% endif %}
  </div>
</div>
{% endif %}
```

- [ ] **Step 4: Add the route**

Add these imports to `web/views/map.py`. It currently has only
`from fledermap.services.map_query import list_sessions, list_taxa` — replace that
one line with the merged version below rather than adding a second, duplicate
import of the same module:

```python
# src/fledermap/web/views/map.py
import json

from fledermap.domain.codes import IdSource
from fledermap.media.paths import preview_path, spectrogram_path
from fledermap.services.current_best import current_best_identification
from fledermap.services.map_query import (
    filtered_recordings,
    list_sessions,
    list_taxa,
    neighbor_recordings,
)
from fledermap.store.geo import decode_point
from fledermap.store.models import Taxon
from fledermap.web.params import parse_datetime, parse_int, parse_verdict


@views_bp.get("/recordings/<audio_hash>/panel")
def recording_panel(audio_hash: str) -> flask.Response:
    try:
        date_from = parse_datetime(flask.request.args.get("from"))
        date_to = parse_datetime(flask.request.args.get("to"), end_of_day=True)
        taxon_id = parse_int(flask.request.args.get("taxon"))
        verdict = parse_verdict(flask.request.args.get("verdict"))
        session_id = parse_int(flask.request.args.get("session"))
        site_id = parse_int(flask.request.args.get("site"))
        source_raw = flask.request.args.get("source")
        source = IdSource(source_raw) if source_raw else None
    except ValueError as exc:
        return flask.make_response((str(exc), 400))

    engine = flask.current_app.config["ENGINE"]
    media_root = flask.current_app.config["MEDIA_ROOT"]
    filter_qs = flask.request.query_string.decode()

    with OrmSession(engine) as session:
        recordings = filtered_recordings(
            session, date_from=date_from, date_to=date_to, taxon_id=taxon_id,
            verdict=verdict, session_id=session_id, site_id=site_id, source=source,
        )
        neighbors = neighbor_recordings(recordings, audio_hash)
        if neighbors is None:
            html = flask.render_template("_recording_panel.html", found=False)
            return flask.make_response(html)

        previous, following = neighbors
        recording = next(r for r in recordings if r.audio_hash == audio_hash)
        best = current_best_identification(recording)
        taxon = None
        if best is not None and best.taxon_id is not None:
            taxon = session.get(Taxon, best.taxon_id)
        point = decode_point(recording.geom)

        html = flask.render_template(
            "_recording_panel.html",
            found=True,
            recording=recording,
            best=best,
            taxon=taxon,
            previous=previous,
            next=following,
            filter_qs=filter_qs,
            spectrogram_ready=spectrogram_path(media_root, audio_hash).exists(),
            preview_ready=preview_path(media_root, audio_hash).exists(),
        )

    response = flask.make_response(html)
    if point is not None:
        response.headers["HX-Trigger"] = json.dumps(
            {
                "recording-selected": {
                    "hash": recording.audio_hash,
                    "latitude": point[1],
                    "longitude": point[0],
                },
            },
        )
    return response
```

(`next` is a Jinja variable name here, shadowing the Python builtin only inside the
template context dict — fine, Jinja templates don't execute as Python scope.)

- [ ] **Step 5: Run to verify they pass**

Run: `hatch test tests/test_map_view.py -k recording_panel -v`
Expected: PASS, all four.

- [ ] **Step 6: Lint and type-check**

Run: `hatch fmt --check && hatch run types:check`
Expected: clean. (`flask.Response` as the return type is correct here, unlike the
existing `map_page`'s bare `str` — this view sometimes needs to set a response
header, so it always builds a `flask.Response` via `make_response`.)

- [ ] **Step 7: Commit**

```bash
git add src/fledermap/web/views/map.py src/fledermap/web/templates/_recording_panel.html tests/test_map_view.py
git commit -m "feat: recording detail panel route and template"
```

---

## Task 7: Site panel route and template

**Files:**
- Modify: `src/fledermap/web/views/map.py`
- Create: `src/fledermap/web/templates/_site_panel.html`
- Test: `tests/test_map_view.py`

**Interfaces:**
- Consumes: `site_detail` (Task 5), `fallback_site_label` (Task 1), `decode_point`.
- Produces: `GET /sites/<int:site_id>/panel` — HTML fragment, 200 always.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_map_view.py
from fledermap.store.models import Site


def test_site_panel_renders_species_breakdown_and_sessions(
    engine: Engine, tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        site = Site(
            centroid=WKTElement("POINT(10 50)", srid=4326),
            radius_m=50.0, recording_count=1,
            first_at=datetime(2026, 8, 25, tzinfo=UTC),
            last_at=datetime(2026, 8, 25, tzinfo=UTC),
            name="Behind the barn",
        )
        taxon = Taxon(rank="species", scientific_name="Eptesicus serotinus")
        session.add_all([site, taxon])
        session.flush()
        recording = Recording(
            audio_hash="a" * 64, path="a.wav",
            recorded_at=datetime(2026, 8, 25, 21, 0, tzinfo=UTC),
            site_id=site.id,
        )
        session.add(recording)
        session.flush()
        session.add(
            Identification(
                recording_id=recording.id, source=IdSource.EMT_GUANO,
                source_version=None, verdict=Verdict.SPECIES,
                taxon_id=taxon.id, raw_label="EPTSER",
                first_seen_at=datetime(2026, 8, 25, 21, 0, tzinfo=UTC),
            ),
        )
        session.commit()
        site_id = site.id

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    html = app.test_client().get(f"/sites/{site_id}/panel").get_data(as_text=True)

    assert "Behind the barn" in html
    assert "Eptesicus serotinus" in html


def test_site_panel_not_found_renders_gracefully(engine: Engine, tmp_path: Path) -> None:
    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().get("/sites/999999/panel")

    assert response.status_code == 200
    assert "not found" in response.get_data(as_text=True).lower()


def test_site_panel_has_show_only_this_site_button(engine: Engine, tmp_path: Path) -> None:
    with OrmSession(engine) as session:
        site = Site(
            centroid=WKTElement("POINT(10 50)", srid=4326),
            radius_m=50.0, recording_count=0,
            first_at=datetime(2026, 8, 25, tzinfo=UTC),
            last_at=datetime(2026, 8, 25, tzinfo=UTC),
        )
        session.add(site)
        session.commit()
        site_id = site.id

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    html = app.test_client().get(f"/sites/{site_id}/panel").get_data(as_text=True)

    assert f"fledermapFilterBySite({site_id})" in html
```

- [ ] **Step 2: Run to verify failure**

Run: `hatch test tests/test_map_view.py -k site_panel -v`
Expected: FAIL — 404.

- [ ] **Step 3: Create the template**

```html
{# src/fledermap/web/templates/_site_panel.html #}
{% if not found %}
<p>Site not found.</p>
{% else %}
<div class="panel-header">
  <h2>{{ label }}</h2>
  <button type="button" onclick="fledermapFilterBySite({{ detail.site.id }})">Show only this site</button>
</div>

<div class="panel-columns">
  <div class="col species-breakdown">
    <h3>Species</h3>
    <ul>
      {% for taxon, count in detail.species_counts %}
      <li>{{ taxon.scientific_name }}: {{ count }}</li>
      {% endfor %}
    </ul>
  </div>
  <div class="col site-stats">
    <h3>Site</h3>
    <p>{{ detail.site.recording_count }} recordings</p>
    <p>{{ detail.site.admin_path or "" }}</p>
  </div>
  <div class="col sessions">
    <h3>Sessions</h3>
    <ul>
      {% for s in detail.sessions %}
      <li>{{ s.started_at.strftime('%Y-%m-%d %H:%M') }}–{{ s.ended_at.strftime('%H:%M') }}</li>
      {% endfor %}
    </ul>
  </div>
</div>
{% endif %}
```

- [ ] **Step 4: Add the route**

By this task, Task 6 has already turned `web/views/map.py`'s `map_query` import into
the multi-line form shown in Task 6 Step 4 — add `site_detail` into that same
parenthesized import (don't add a second `from fledermap.services.map_query import
...` line), and extend Task 6's `from fledermap.web.params import ...` line with
`fallback_site_label`:

```python
# src/fledermap/web/views/map.py
from fledermap.services.map_query import (
    filtered_recordings,
    list_sessions,
    list_taxa,
    neighbor_recordings,
    site_detail,
)
from fledermap.web.params import fallback_site_label, parse_datetime, parse_int, parse_verdict


@views_bp.get("/sites/<int:site_id>/panel")
def site_panel(site_id: int) -> str:
    engine = flask.current_app.config["ENGINE"]
    with OrmSession(engine) as session:
        detail = site_detail(session, site_id)
        if detail is None:
            return flask.render_template("_site_panel.html", found=False)
        point = decode_point(detail.site.centroid)
        label = detail.site.name if detail.site.name else fallback_site_label(point)
        return flask.render_template(
            "_site_panel.html", found=True, detail=detail, label=label,
        )
```

(No `HX-Trigger` here — unlike recording prev/next, nothing navigates *between*
sites, so there's no case where opening a site panel should move the map; the user
already clicked the site marker they wanted.)

- [ ] **Step 5: Run to verify they pass**

Run: `hatch test tests/test_map_view.py -k site_panel -v`
Expected: PASS, all three.

- [ ] **Step 6: Lint, type-check, full suite**

Run: `hatch fmt --check && hatch run types:check && hatch test`
Expected: clean, all passing.

- [ ] **Step 7: Commit**

```bash
git add src/fledermap/web/views/map.py src/fledermap/web/templates/_site_panel.html tests/test_map_view.py
git commit -m "feat: site detail panel route and template"
```

---

## Task 8: Drawer chrome in `map.html`

Server-rendered structure and Alpine state — testable via `test_map_view.py`
(asserting the elements exist), unlike Task 9's JS behavior.

**Files:**
- Modify: `src/fledermap/web/templates/map.html`
- Modify: `src/fledermap/web/static/app.css`
- Test: `tests/test_map_view.py`

**Interfaces:**
- Produces: `#drawer` container with `#drawer-body` as the HTMX swap target — Task 6/7's routes target this id; Task 9's JS opens/closes it via the same Alpine state defined here.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_map_view.py
def test_map_page_includes_the_drawer_shell(engine: Engine, tmp_path: Path) -> None:
    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    html = app.test_client().get("/").get_data(as_text=True)

    assert 'id="drawer"' in html
    assert 'id="drawer-body"' in html
    assert "$store.drawer.open" in html
    assert "$store.drawer.collapsed" in html
```

- [ ] **Step 2: Run to verify failure**

Run: `hatch test tests/test_map_view.py -k drawer_shell -v`
Expected: FAIL — none of those strings are in the page yet.

- [ ] **Step 3: Add the drawer markup**

Drawer state lives in an `Alpine.store()`, not `<body>`'s own `x-data`, because
Task 9's plain-JS marker-click handlers (outside any Alpine expression) need to
toggle it too — `Alpine.store()` is Alpine's documented mechanism for state read
and written from both directives and external script (unlike a component's private
`x-data`, which has no public, documented external-access API). The store itself is
registered in `app.js` (Task 9, Step 0) — this step only adds the markup that reads
it. Leave `<body x-data="filterForm()">` exactly as it is; add the drawer right
after `<div id="map"></div>`:

```html
  <div id="map"></div>

  <div id="drawer" x-show="$store.drawer.open" x-cloak
       :class="{ collapsed: $store.drawer.collapsed }">
    <div id="drawer-handle"></div>
    <div id="drawer-header">
      <button type="button" @click="$store.drawer.collapsed = !$store.drawer.collapsed" aria-label="Collapse">▾</button>
      <button type="button" @click="$store.drawer.open = false; $store.drawer.collapsed = false; document.getElementById('drawer-body').innerHTML = ''" aria-label="Close">×</button>
    </div>
    <div id="drawer-body"></div>
  </div>
```

(`x-cloak` needs its usual `[x-cloak] { display: none !important; }` rule — add it
to `app.css` alongside the drawer's own rules in the next step, if it isn't already
defined anywhere in the project; grep `app.css` for `x-cloak` first, since Phase 4
may already have it from the filter form.)

- [ ] **Step 4: Add drawer CSS**

```css
/* src/fledermap/web/static/app.css -- append */
[x-cloak] { display: none !important; }

#drawer {
  position: fixed;
  left: 0;
  right: 0;
  bottom: 0;
  height: 40vh;
  background: white;
  border-top: 1px solid #ccc;
  display: flex;
  flex-direction: column;
  z-index: 1000;
}
#drawer.collapsed { height: auto; }
#drawer.collapsed #drawer-body { display: none; }
#drawer-handle { height: 6px; cursor: ns-resize; background: #ddd; }
#drawer-header { display: flex; justify-content: flex-end; gap: 0.5rem; padding: 0.25rem 0.5rem; }
#drawer-body { flex: 1; overflow: auto; padding: 0 1rem 1rem; }
.panel-columns { display: flex; gap: 1rem; }
.panel-columns .identifications { flex: 3; }
.panel-columns .col { flex: 2; }
.superseded { text-decoration: line-through; opacity: 0.6; }
.spectrogram { width: 100%; display: block; }
.audio-row { padding: 0.5rem 0; }
```

(Drag-resize interaction itself — actually changing `#drawer`'s height on
`#drawer-handle` mousedown/mousemove — is JS behavior; Task 9 wires it. This step
only gives the handle a hoverable, styled target to attach that to.)

- [ ] **Step 5: Run to verify it passes**

Run: `hatch test tests/test_map_view.py -k drawer_shell -v`
Expected: PASS.

- [ ] **Step 6: Run the full suite, lint, type-check**

Run: `hatch test && hatch fmt --check && hatch run types:check`
Expected: clean, all passing — this touches a template every other `test_map_view.py`
test also renders, so a regression here would show up broadly, not just in the new
test.

- [ ] **Step 7: Commit**

```bash
git add src/fledermap/web/templates/map.html src/fledermap/web/static/app.css tests/test_map_view.py
git commit -m "feat: add drawer chrome (open/collapsed/closed) to map.html"
```

---

## Task 9: `app.js` wiring — marker click, pan-on-navigate, site filter bridge, drag-resize

No automated test (Phase 4's own established choice — no JS test framework). Verify
manually via the `run` skill after this task: ingest the two bundled sample
recordings, run `fledermap derive`, `fledermap worker --no-wait` (or the DB
already has media from earlier in this session), `fledermap serve`, and check in a
browser that clicking a recording marker opens the drawer, prev/next swaps content
and re-pans the map, collapse/close work, dragging the handle resizes the drawer,
and a site's "show only this site" button actually narrows the map.

**Files:**
- Modify: `src/fledermap/web/static/app.js`

**Interfaces:**
- Consumes: `#filters` form's `site` hidden input (Task 2), the `recording-selected` `HX-Trigger` event (Task 6), `htmx.ajax` and `Alpine.store`/`document.addEventListener("alpine:init", ...)` (both vendored, already loaded in `map.html`).
- Produces: the `drawer` Alpine store (`{ open, collapsed }`, read by Task 8's markup), `window.fledermapFilterBySite(siteId)` — called from `_site_panel.html`'s button (Task 7).

- [ ] **Step 0: Register the drawer's Alpine store**

Add at module scope in `app.js`, near the top (alongside `TAXON_PALETTE` and
`colorForFeature`, before the `DOMContentLoaded` listener) — registering the store
here, not inside `DOMContentLoaded`, matters: `app.js` loads without a `defer`
attribute (unlike `alpine.min.js`, which has one), so this line runs synchronously
while the page is still parsing, ahead of Alpine's own deferred script — exactly
when Alpine's documented `alpine:init` pattern expects a store registration to
happen, before Alpine starts scanning the DOM:

```javascript
document.addEventListener("alpine:init", () => {
  Alpine.store("drawer", { open: false, collapsed: false });
});
```

- [ ] **Step 1: Replace marker-click popups with drawer opens**

In the `refreshRecordings` function, replace the `.bindPopup(...)` call with an
`hx-get`-equivalent programmatic request plus opening the drawer:

```javascript
  const recordingLayersByHash = new Map();
  let highlightedRecordingLayer = null;

  async function refreshRecordings(params) {
    let response;
    try {
      response = await fetch(`/api/recordings.geojson?${params}`);
    } catch (err) {
      console.error("recordings.geojson fetch failed", err);
      return;
    }
    if (!response.ok) {
      console.error("recordings.geojson returned", response.status);
      return;
    }
    const recordingsData = await response.json();
    recordingsLayer.clearLayers();
    recordingLayersByHash.clear();
    highlightedRecordingLayer = null;
    L.geoJSON(recordingsData, {
      pointToLayer: (feature, latlng) => {
        const marker = L.circleMarker(latlng, { color: colorForFeature(feature.properties) })
          .on("click", () => openRecordingPanel(feature.properties.audio_hash, params));
        recordingLayersByHash.set(feature.properties.audio_hash, marker);
        return marker;
      },
    }).eachLayer((layer) => recordingsLayer.addLayer(layer));
  }

  function openRecordingPanel(audioHash, params) {
    htmx.ajax("GET", `/recordings/${audioHash}/panel?${params}`, {
      target: "#drawer-body",
      swap: "innerHTML",
    });
    Alpine.store("drawer").open = true;
    Alpine.store("drawer").collapsed = false;
  }

  // P5a-6: prev/next must pan AND highlight, not just pan -- otherwise the
  // drawer and the map can visibly disagree about which recording is current.
  function highlightRecording(audioHash) {
    if (highlightedRecordingLayer) {
      highlightedRecordingLayer.setStyle({ weight: 1 });
    }
    const marker = recordingLayersByHash.get(audioHash);
    if (marker) {
      marker.setStyle({ weight: 4 });
      highlightedRecordingLayer = marker;
    }
  }
```

Do the equivalent in `refreshSites` for site circles:

```javascript
    L.geoJSON(sitesData, {
      pointToLayer: (feature, latlng) =>
        L.circle(latlng, { radius: feature.properties.radius_m, color: "blue" })
          .on("click", () => openSitePanel(feature.properties.id)),
    }).eachLayer((layer) => sitesLayer.addLayer(layer));

  function openSitePanel(siteId) {
    htmx.ajax("GET", `/sites/${siteId}/panel`, {
      target: "#drawer-body",
      swap: "innerHTML",
    });
    Alpine.store("drawer").open = true;
    Alpine.store("drawer").collapsed = false;
  }
```

(`Alpine.store("drawer")` returns the same reactive object registered in Step 0 —
assigning to its properties from plain JS updates Task 8's `x-show`/`:class`
bindings immediately, the same as if an Alpine directive had made the change.)

- [ ] **Step 2: Listen for `recording-selected` to pan and highlight**

Add near the other `document.addEventListener` calls, inside the
`DOMContentLoaded` handler (it needs `map` and `highlightRecording` — Step 1,
same scope — in scope):

```javascript
  document.body.addEventListener("recording-selected", (event) => {
    const { latitude, longitude, hash } = event.detail;
    if (latitude != null && longitude != null) {
      map.panTo([latitude, longitude]);
    }
    highlightRecording(hash);
  });
```

(HTMX's `HX-Trigger` header fires the event on the element that made the request
and it bubbles — listening on `document.body` catches it regardless of which
marker's click handler issued the underlying `htmx.ajax` call. `highlightRecording`
is a no-op if `hash` isn't in `recordingLayersByHash` yet — e.g. the filters changed
between the map's last refresh and this navigation — rather than throwing.)

- [ ] **Step 3: Add the site-filter bridge function**

```javascript
  window.fledermapFilterBySite = function (siteId) {
    const input = document.querySelector('#filters [name="site"]');
    input.value = siteId;
    input.dispatchEvent(new Event("input", { bubbles: true }));
  };
```

Place this at module scope (outside `DOMContentLoaded`, alongside
`colorForFeature`), since `_site_panel.html`'s inline `onclick` needs it as a global.

- [ ] **Step 4: Add drag-resize on the handle**

```javascript
  const drawer = document.getElementById("drawer");
  const handle = document.getElementById("drawer-handle");
  let dragging = false;

  handle.addEventListener("mousedown", () => { dragging = true; });
  document.addEventListener("mouseup", () => { dragging = false; });
  document.addEventListener("mousemove", (event) => {
    if (!dragging) return;
    const newHeight = window.innerHeight - event.clientY;
    drawer.style.height = `${Math.max(120, Math.min(newHeight, window.innerHeight - 80))}px`;
  });
```

Add this inside the `DOMContentLoaded` handler, after the existing `refresh()` call
at the end of the file.

- [ ] **Step 5: Lint and type-check**

Run: `hatch fmt --check && hatch run types:check`
Expected: clean. (No JS linter is configured in this project — `hatch fmt`/`types:check`
only cover Python; this step is a reminder that the *Python* side must still be
clean, not a claim that it checks the JS.)

- [ ] **Step 6: Manual verification via the `run` skill**

Follow the `run` skill to start `fledermap serve` against real data (the two bundled
sample recordings, or the real field data already ingested earlier in this
project's history, are both fine). In the browser:
- Click a recording marker → drawer opens with spectrogram/player/columns.
- Click "Next →" in the Context column → drawer content swaps, map pans, and the
  new recording's marker visibly thickens (highlight) while the old one resets.
- Click the collapse chevron → drawer shrinks to a bar; re-expand shows the same
  recording, no re-fetch (check the Network tab: no new request on re-expand).
- Click close (×) → drawer disappears; opening a new marker afterward starts fresh.
- Click a site circle → drawer shows species breakdown/stats/sessions.
- Click "Show only this site" → both layers narrow to that site's recordings/site
  only (verify via the Network tab: `/api/recordings.geojson` and
  `/api/sites.geojson` both now carry `site=<id>`).
- Drag the handle → drawer height changes smoothly.

Fix anything broken before moving on — there's no automated safety net for this
task, so this manual pass is the actual verification, not optional polish.

- [ ] **Step 7: Full suite one more time**

Run: `hatch test && hatch fmt --check && hatch run types:check`
Expected: clean — confirms Tasks 1–8's automated coverage still holds after this
task's changes (which don't touch Python, but a stray edit anywhere in the diff
should still be caught here).

- [ ] **Step 8: Commit**

```bash
git add src/fledermap/web/static/app.js
git commit -m "feat: wire marker clicks, prev/next pan, and site filter to the drawer"
```
