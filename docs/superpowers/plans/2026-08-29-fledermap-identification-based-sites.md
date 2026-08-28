# Fledermap Identification-Based Site Derivation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `Site` derivation depend on identified bat activity (`Verdict.SPECIES`), not on which session — or session kind — a recording belongs to; remove `SessionKind` entirely, since site derivation was its only consumer.

**Architecture:** `services/derive.py`'s `derive_sites` drops its `Session` join and `Session.kind == STATIONARY` filter, replacing it with a Python-side `current_best_identification(...).verdict == Verdict.SPECIES` filter — the same split `services/map_query.py` already uses for the same reason. With that filter gone, `SessionKind` (`classify_kind`/`reclassify_session` in `derive/sessions.py`, `Session.kind`/`kind_locked` in the schema, the "Kind" field in the session detail UI, `transect_distance_m` end to end) has no remaining consumer and is removed in two steps: first the classification *logic* and its config knob (Task 2), then the schema/enum itself once nothing references it (Task 3).

**Tech Stack:** SQLAlchemy + Alembic (existing), Flask + Jinja2 (existing) — no new dependencies.

**Spec:** `docs/superpowers/specs/2026-08-29-fledermap-identification-based-sites-design.md`

## Global Constraints

- **SB-1 (spec):** Site membership = `Recording.geom IS NOT NULL` AND `current_best_identification(recording).verdict == Verdict.SPECIES`. No session or session-kind involvement at all.
- **SB-2 (spec):** `SessionKind` is removed entirely, not kept as decoupled metadata — confirmed by grep before this plan was written that site derivation was its only consumer.
- **SB-3 (spec):** No new recompute trigger for identification changes — `run_ingest_cycle` already calls `derive_sites()` unconditionally every cycle, and `Identification` rows are only ever written inside that same cycle (`services/ingest.py`'s `commit_scan`). Nothing in this plan adds a trigger.
- **SB-4 (spec):** Session partitioning (grouping by detector + time gap, `partition_sessions`) is untouched — only the kind-classification layer built on top of it goes.
- **`hatch fmt --check` and `hatch run types:check` must be clean after every task**, including `tests/` (mypy covers tests too).
- **Test output must be pristine** — no warnings.
- **Run every `git commit`, the `alembic revision` step (Task 3), and every `hatch test` invocation that touches a `pytest.mark.db`-marked file with the sandbox disabled** (`dangerouslyDisableSandbox: true` on the Bash tool call — not shell syntax). `git commit` needs it because this repo GPG-signs commits; `db`-marked tests need it because they spin up real Postgres/PostGIS via `testcontainers` + Docker, which the sandbox blocks (`docker.errors.DockerException` / `PermissionError(1, 'Operation not permitted')` — reads like a network fault if you don't already know to expect it); `alembic revision` needs it because it writes into `alembic/versions/`.
- **The one exception:** `tests/test_config.py` runs are plain (`Config.from_env` needs no database) and don't require the sandbox disabled.

---

## Task 1: `derive_sites` — identification-based site query

**Files:**
- Modify: `src/fledermap/services/derive.py` (whole file)
- Modify: `tests/test_derive_sites.py` (whole file)

**Interfaces:**
- Consumes: `current_best_identification(recording: Recording) -> Identification | None` (`fledermap.services.current_best`, unchanged, existing).
- Produces: `derive_sites(db_session, *, eps_m: float, min_points: int) -> SiteDeriveReport` — same signature as today; only its internal recording-selection query changes. `SiteDeriveReport(site_count: int, unclustered: int)` — unchanged.

- [ ] **Step 1: Rewrite `services/derive.py`**

Replace the entire file with:

```python
"""Site derivation use-case layer. See spec section 7."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from geoalchemy2.elements import WKTElement
from sqlalchemy import delete, select
from sqlalchemy.orm import Session as OrmSession

from fledermap.derive.geo_cluster import GeoCluster
from fledermap.derive.sites import cluster_points
from fledermap.domain.codes import Verdict
from fledermap.services.current_best import current_best_identification
from fledermap.store.geo import decode_point
from fledermap.store.models import Recording, Site


@dataclass
class SiteDeriveReport:
    site_count: int = 0
    unclustered: int = 0


def derive_sites(
    db_session: OrmSession,
    *,
    eps_m: float,
    min_points: int,
) -> SiteDeriveReport:
    """Wholesale rebuild of `site` from GPS-bearing recordings with an
    identified-species current-best identification.

    A site is a place where we find bats -- not a session-scoped concept
    (design spec 2026-08-29-fledermap-identification-based-sites-design.md,
    decision SB-1): every GPS-bearing recording is eligible regardless of
    which session (or session kind) it belongs to, filtered to
    `Verdict.SPECIES` via `current_best_identification` -- the same rule
    `map_query._passes_verdict_filter` already applies by default to hide
    noise on the map. A recording with no non-superseded identification at
    all is excluded, matching that same rule's treatment of "no best" as
    equivalent to `NO_ID`.

    Idempotent — safe to re-run at any time (spec section 7: "tuning is
    free"). A recording left with `site_id = NULL` is a one-off spot or
    unidentified, not an error. An identification change is picked up
    automatically the next time this runs, with no separate invalidation
    needed (decision SB-3): `Identification` rows are only ever written by
    `services/ingest.py`'s `commit_scan`, which only ever runs inside
    `run_ingest_cycle` -- and that already calls this function
    unconditionally every cycle.

    `DELETE FROM site`, never `TRUNCATE`: Postgres `TRUNCATE` does not fire
    `ON DELETE SET NULL` the way `DELETE` does — it would either error on the
    referencing `recording.site_id` FK or, with CASCADE, truncate `recording`
    too.
    """
    candidates = db_session.scalars(
        select(Recording).where(Recording.geom.is_not(None)),
    )
    recordings = [
        r
        for r in candidates
        if (best := current_best_identification(r)) is not None
        and best.verdict == Verdict.SPECIES
    ]

    db_session.execute(delete(Site))
    db_session.flush()

    report = SiteDeriveReport()
    if not recordings:
        return report

    points = np.array(
        [decode_point(r.geom) for r in recordings],
    )
    labels = cluster_points(points, eps_m=eps_m, min_points=min_points)

    by_label: dict[int, list[Recording]] = {}
    for recording, label in zip(recordings, labels, strict=True):
        if label == -1:
            report.unclustered += 1
            continue
        by_label.setdefault(int(label), []).append(recording)

    for members in by_label.values():
        locations: list[tuple[float, float]] = []
        for r in members:
            point = decode_point(r.geom)
            assert point is not None, "excluded by the geom IS NOT NULL query above"
            locations.append(point)
        # No z-score outlier removal here: DBSCAN's eps/min_points already
        # decided membership (noise is labelled -1 and excluded above), so
        # re-trimming is redundant at best. At worst it is wrong — `z < 1`
        # keeps only ~68% of a normal spread per axis, so `radius_m` would
        # describe roughly half the points while `recording_count` counts them
        # all. `GeoCluster`'s removal was built for mkmapdiary's different
        # problem: trimming GPS spikes out of a continuous track.
        cluster = GeoCluster(locations, remove_outliers=False)
        lon, lat = cluster.mass_point

        site = Site(
            centroid=WKTElement(f"POINT({lon} {lat})", srid=4326),
            # `cluster.radius` is `np.float64`: psycopg2 has no adapter for it,
            # so bound as a query parameter it renders as the literal text
            # `np.float64(...)` and Postgres reads `np` as a schema name.
            # Plain `float()` avoids the numpy scalar entirely.
            radius_m=float(cluster.radius),
            recording_count=len(members),
            first_at=min(r.recorded_at for r in members),
            last_at=max(r.recorded_at for r in members),
        )
        db_session.add(site)
        db_session.flush()
        for recording in members:
            recording.site_id = site.id
        report.site_count += 1

    return report
```

(The `Session`/`SessionKind` join is gone entirely, and with it the `raiseload(Recording.identifications)` guard that existed only because identification was never touched — the query now needs `current_best_identification`'s data, so it falls back to `Recording.identifications`'s model default, `lazy="selectin"`, the same as `map_query.filtered_recordings` relies on with no special loader option at all.)

- [ ] **Step 2: Rewrite `tests/test_derive_sites.py`**

Replace the entire file with:

```python
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from geoalchemy2.elements import WKTElement
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import IdSource, SessionKind, Verdict
from fledermap.services.derive import derive_sites
from fledermap.store.geo import decode_point
from fledermap.store.models import Identification, Recording, Session, Site

pytestmark = pytest.mark.db


def _recording(
    hash_suffix: str,
    db_session: OrmSession,
    lon: float,
    lat: float,
    *,
    verdict: Verdict | None = Verdict.SPECIES,
    session_id: int | None = None,
) -> Recording:
    r = Recording(
        audio_hash=hash_suffix.rjust(64, "0"),
        path=f"{hash_suffix}.wav",
        recorded_at=datetime(2026, 8, 21, 21, tzinfo=UTC),
        session_id=session_id,
        geom=WKTElement(f"POINT({lon} {lat})", srid=4326),
    )
    db_session.add(r)
    db_session.flush()
    if verdict is not None:
        db_session.add(
            Identification(
                recording_id=r.id,
                source=IdSource.EMT_GUANO,
                verdict=verdict,
                first_seen_at=r.recorded_at,
            ),
        )
        db_session.flush()
    return r


def test_a_cluster_of_species_identified_recordings_becomes_one_site(
    engine: Engine,
) -> None:
    with OrmSession(engine) as session:
        _recording("a", session, 13.4000, 52.5000)
        _recording("b", session, 13.4001, 52.5000)
        session.commit()

        report = derive_sites(session, eps_m=75.0, min_points=2)
        session.commit()

        assert report.site_count == 1
        assert report.unclustered == 0
        sites = session.scalars(select(Site)).all()
        assert len(sites) == 1
        assert sites[0].recording_count == 2
        recordings = session.scalars(select(Recording)).all()
        assert all(r.site_id == sites[0].id for r in recordings)


def test_an_isolated_recording_stays_unclustered(engine: Engine) -> None:
    with OrmSession(engine) as session:
        _recording("a", session, 13.4000, 52.5000)
        session.commit()

        report = derive_sites(session, eps_m=75.0, min_points=2)
        session.commit()

        assert report.site_count == 0
        assert report.unclustered == 1
        recording = session.scalars(select(Recording)).one()
        assert recording.site_id is None


def test_a_transect_sessions_identified_recordings_now_form_a_site(
    engine: Engine,
) -> None:
    """Regression test for the bug that motivated this design: a walked
    transect that passes through a real hotspot used to be entirely invisible
    to site derivation, because `derive_sites` only ever looked at
    STATIONARY-classified sessions. Site membership no longer cares what
    session -- or session kind -- a recording belongs to."""
    with OrmSession(engine) as session:
        transect = Session(
            started_at=datetime(2026, 8, 21, 21, tzinfo=UTC),
            ended_at=datetime(2026, 8, 21, 23, tzinfo=UTC),
            kind=SessionKind.TRANSECT,
            detector_key="EMT\x1f1",
        )
        session.add(transect)
        session.flush()
        _recording("a", session, 13.4000, 52.5000, session_id=transect.id)
        _recording("b", session, 13.4001, 52.5000, session_id=transect.id)
        session.commit()

        report = derive_sites(session, eps_m=75.0, min_points=2)
        session.commit()

        assert report.site_count == 1
        assert report.unclustered == 0


@pytest.mark.parametrize("verdict", [Verdict.NO_ID, Verdict.NOISE])
def test_no_id_and_noise_verdicts_are_excluded(
    engine: Engine,
    verdict: Verdict,
) -> None:
    with OrmSession(engine) as session:
        _recording("a", session, 13.4000, 52.5000, verdict=verdict)
        _recording("b", session, 13.4001, 52.5000, verdict=verdict)
        session.commit()

        report = derive_sites(session, eps_m=75.0, min_points=2)
        session.commit()

        assert report.site_count == 0
        assert report.unclustered == 0
        recordings = session.scalars(select(Recording)).all()
        assert all(r.site_id is None for r in recordings)


def test_recordings_with_no_identification_at_all_are_excluded(
    engine: Engine,
) -> None:
    with OrmSession(engine) as session:
        _recording("a", session, 13.4000, 52.5000, verdict=None)
        _recording("b", session, 13.4001, 52.5000, verdict=None)
        session.commit()

        report = derive_sites(session, eps_m=75.0, min_points=2)
        session.commit()

        assert report.site_count == 0
        assert report.unclustered == 0


def test_mixed_verdict_cluster_counts_only_species_members(engine: Engine) -> None:
    """The verdict filter runs before clustering, not just for display -- a
    NO_ID recording at the exact same spot as two SPECIES ones must not
    inflate `recording_count`."""
    with OrmSession(engine) as session:
        _recording("a", session, 13.4000, 52.5000, verdict=Verdict.SPECIES)
        _recording("b", session, 13.4000, 52.5000, verdict=Verdict.SPECIES)
        _recording("c", session, 13.4000, 52.5000, verdict=Verdict.NO_ID)
        session.commit()

        report = derive_sites(session, eps_m=75.0, min_points=2)
        session.commit()

        assert report.site_count == 1
        site = session.scalars(select(Site)).one()
        assert site.recording_count == 2
        excluded = session.scalars(
            select(Recording).where(Recording.path == "c.wav"),
        ).one()
        assert excluded.site_id is None


def test_recordings_without_gps_are_excluded(engine: Engine) -> None:
    with OrmSession(engine) as session:
        r = Recording(
            audio_hash="c" * 64,
            path="c.wav",
            recorded_at=datetime(2026, 8, 21, 21, tzinfo=UTC),
            geom=None,
        )
        session.add(r)
        session.flush()
        session.add(
            Identification(
                recording_id=r.id,
                source=IdSource.EMT_GUANO,
                verdict=Verdict.SPECIES,
                first_seen_at=r.recorded_at,
            ),
        )
        session.commit()

        report = derive_sites(session, eps_m=75.0, min_points=2)
        session.commit()

        assert report.site_count == 0
        assert report.unclustered == 0


@pytest.mark.parametrize(
    ("label", "lon", "lat"),
    [
        ("berlin", 13.4000, 52.5000),  # high northern latitude
        ("quito", -78.4600, -0.1800),  # near-equatorial, southern hemisphere
    ],
)
def test_clustering_regression_at_both_latitudes(
    engine: Engine,
    label: str,
    lon: float,
    lat: float,
) -> None:
    """Phase 2's exit criterion (parent spec section 15): clustering must be
    correct near a pole-adjacent latitude AND near the equator. A wrong UTM
    zone pick, or an eps accidentally in degrees instead of metres, could pass
    every other test in this plan (all of which sit near Berlin) while still
    being broken here."""
    with OrmSession(engine) as session:
        # Two points ~15m apart (well inside a 75m eps); one far outlier that
        # must stay unclustered regardless of latitude.
        _recording(f"{label}-a", session, lon, lat)
        _recording(f"{label}-b", session, lon + 0.0002, lat)
        _recording(f"{label}-far", session, lon + 5.0, lat)
        session.commit()

        report = derive_sites(session, eps_m=75.0, min_points=2)
        session.commit()

        assert report.site_count == 1
        assert report.unclustered == 1
        recordings = {r.path: r for r in session.scalars(select(Recording)).all()}
        assert recordings[f"{label}-a.wav"].site_id is not None
        assert (
            recordings[f"{label}-a.wav"].site_id == recordings[f"{label}-b.wav"].site_id
        )
        assert recordings[f"{label}-far.wav"].site_id is None


def test_recordings_at_one_identical_fix_still_produce_a_site(engine: Engine) -> None:
    """Regression: a stationary detector reporting the same rounded GPS fix for
    every recording gives a zero-variance spread. `GeoCluster`'s z-score filter
    then discarded EVERY point, `mass_point` returned `(None, None)`, and the
    `POINT(None None)` that produced failed to parse in Postgres — so the whole
    `derive` run died on write. This project's own two bundled samples already
    share one identical fix; a third and fourth would have triggered it."""
    with OrmSession(engine) as session:
        for suffix in ("a", "b", "c", "d"):
            _recording(suffix, session, 13.4000, 52.5000)
        session.commit()

        report = derive_sites(session, eps_m=75.0, min_points=2)
        session.commit()

        assert report.site_count == 1
        assert report.unclustered == 0
        site = session.scalars(select(Site)).one()
        assert site.recording_count == 4
        centroid = decode_point(site.centroid)
        assert centroid is not None
        lon, lat = centroid
        assert lon == pytest.approx(13.4000, abs=1e-6)
        assert lat == pytest.approx(52.5000, abs=1e-6)
        assert site.radius_m == pytest.approx(0.0, abs=1e-6)


def test_rebuild_is_wholesale_and_idempotent(engine: Engine) -> None:
    """Re-running with the same data doesn't duplicate sites; a recording that
    drops out of the archive between runs loses its site cleanly."""
    with OrmSession(engine) as session:
        _recording("a", session, 13.4000, 52.5000)
        _recording("b", session, 13.4001, 52.5000)
        session.commit()

        derive_sites(session, eps_m=75.0, min_points=2)
        session.commit()
        first_site_id = session.scalars(select(Site)).one().id

        # Re-run with identical input.
        report = derive_sites(session, eps_m=75.0, min_points=2)
        session.commit()

        assert report.site_count == 1
        sites = session.scalars(select(Site)).all()
        assert len(sites) == 1
        # A fresh row (wholesale rebuild) — not necessarily the same id.
        recordings = session.scalars(select(Recording)).all()
        assert all(r.site_id == sites[0].id for r in recordings)
        assert first_site_id is not None  # sanity: the fixture actually ran once
```

(`SessionKind`/`Session` are still imported here for the one transect regression test — `Session.kind` still exists in the schema at this point in the plan; Task 3's final sweep removes this file's `SessionKind`/`Session` usage once the enum itself goes away.)

- [ ] **Step 3: Run the tests**

Run (sandbox disabled — `db`-marked): `hatch test tests/test_derive_sites.py -v`
Expected: PASS, 11 tests.

- [ ] **Step 4: Lint and type-check**

Run: `hatch fmt --check && hatch run types:check`
Expected: clean.

- [ ] **Step 5: Run the full suite**

Run (sandbox disabled): `hatch test`
Expected: PASS, no other test broken by this change (nothing outside `test_derive_sites.py` calls `derive_sites` in a way that depends on session kind).

- [ ] **Step 6: Commit**

```bash
git add src/fledermap/services/derive.py tests/test_derive_sites.py
git commit -m "feat: derive sites from identified activity, not session kind"
```

---

## Task 2: Remove session-kind classification — logic, config, and the web UI

**Files:**
- Modify: `src/fledermap/derive/sessions.py`
- Modify: `src/fledermap/services/sessions.py`
- Modify: `src/fledermap/web/views/sessions.py`
- Modify: `src/fledermap/web/templates/session_detail.html`
- Modify: `src/fledermap/web/app.py`
- Modify: `src/fledermap/config.py`
- Modify: `src/fledermap/cli/main.py`
- Modify: `src/fledermap/jobs/tasks.py`
- Modify: `docs/setup.md`
- Test: `tests/test_partition_sessions.py`
- Test: `tests/test_resolve_merge_proposal.py`
- Test: `tests/test_sessions_view.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `partition_sessions(db_session, *, session_gap: timedelta) -> SessionPartitionReport` (drops `transect_distance_m`). `resolve_merge_proposal(db_session, proposal_id, *, action, note, weather) -> int` (drops `transect_distance_m`). `create_app(engine, static_root, media_root) -> flask.Flask` (drops `transect_distance_m`). `Config` loses `transect_distance_m`. `Session.kind`/`Session.kind_locked` columns still exist in the schema after this task (Task 3 removes them) but nothing sets or reads them non-default from here on.

- [ ] **Step 1: `derive/sessions.py` — remove `classify_kind`/`reclassify_session`**

Remove these two imports (lines 11-13 today):

```python
import numpy as np
from scipy.spatial.distance import pdist
from shapely.geometry import MultiPoint
```

and these two (lines 18-19 and 21 today):

```python
from fledermap.domain.codes import SessionKind
from fledermap.store.geo import decode_point
```
```python
from fledermap.util.projection import LocalProjection
```

(Grepped: all four are used only inside `classify_kind`, removed in this step — `raiseload`, used by `partition_sessions`'s own `unsessioned` query, is unrelated and stays.)

Delete `classify_kind` and `reclassify_session` in their entirety (today's lines 52-111 — from `def classify_kind(` through the end of `reclassify_session`'s body, up to but not including `def partition_sessions(`).

In `partition_sessions`'s signature, drop `transect_distance_m`:

```python
def partition_sessions(
    db_session: OrmSession,
    *,
    session_gap: timedelta,
) -> SessionPartitionReport:
```

In the new-session branch (today's lines 233-239), drop the `kind=SessionKind.STATIONARY,` line — the model's own `default=SessionKind.STATIONARY` (still declared in `store/models.py` until Task 3) fills it in:

```python
                new_session = Session(
                    started_at=recording.recorded_at,
                    ended_at=recording.recorded_at,
                    detector_key=key,
                )
```

Delete the final reclassification loop entirely (today's lines 250-255):

```python
    for touched_session in touched_sessions:
        reclassify_session(
            db_session,
            touched_session,
            transect_distance_m=transect_distance_m,
        )
```

`touched_sessions` becomes unused now that nothing reads it after the main loop — remove its declaration too (today's lines 140-146, the `touched_sessions: set[Session] = set()` line and its preceding comment) and every `touched_sessions.add(...)` call inside the loop — four call sites, each immediately preceding one of the four `report.extended += 1` / `report.created += 1` lines: `touched_sessions.add(prev_session)` in the "extends `prev_session`" branch, `touched_sessions.add(prev_session)` again in the "joins_prev" branch, `touched_sessions.add(next_session)` in the "joins_next" branch, and `touched_sessions.add(new_session)` in the new-session branch.

- [ ] **Step 2: `services/sessions.py` — `resolve_merge_proposal` drops `transect_distance_m`**

Remove the import (today's line 17):

```python
from fledermap.derive.sessions import reclassify_session
```

In `resolve_merge_proposal`'s signature, drop the parameter:

```python
def resolve_merge_proposal(
    db_session: OrmSession,
    proposal_id: int,
    *,
    action: str,
    note: str | None,
    weather: str | None,
) -> int:
```

Delete the reclassification call (today's line 302):

```python
    reclassify_session(db_session, session_a, transect_distance_m=transect_distance_m)
```

- [ ] **Step 3: `web/views/sessions.py` — drop the "Kind" form field and its handling**

In the top import block, drop `SessionKind` (today's line 11):

```python
from fledermap.domain.codes import VisualSighting
```

Rewrite `save_session` (today's lines 96-122):

```python
@sessions_bp.post("/sessions/<int:session_id>")
def save_session(session_id: int) -> flask.Response:
    seen_visually_raw = flask.request.form.get("seen_visually", "")
    try:
        seen_visually = VisualSighting(seen_visually_raw)
    except ValueError:
        return flask.make_response(
            (f"Invalid seen_visually: {seen_visually_raw!r}", 400)
        )
    note = flask.request.form.get("note") or None
    weather = flask.request.form.get("weather") or None

    engine = flask.current_app.config["ENGINE"]
    with OrmSession(engine) as session:
        session_obj = session.get(AnnotationSession, session_id)
        if session_obj is None:
            return flask.make_response(("Session not found.", 404))
        session_obj.note = note
        session_obj.weather = weather
        session_obj.seen_visually = seen_visually
        session.commit()

    return flask.make_response(flask.redirect(f"/sessions/{session_id}"))
```

In `resolve_proposal`, drop the `transect_distance_m` lookup (today's line 135) and its threading into `resolve_merge_proposal` (today's line 144):

```python
    engine = flask.current_app.config["ENGINE"]
    with OrmSession(engine) as session:
        try:
            surviving_id = resolve_merge_proposal(
                session,
                proposal_id,
                action=action,
                note=note,
                weather=weather,
            )
```

- [ ] **Step 4: `session_detail.html` — drop the "Kind" field**

Delete this block (today's lines 25-30) from the edit form, immediately before the "Seen visually?" `<label>`:

```html
          <label>Kind
            <select name="kind">
              <option value="stationary" {% if detail.session.kind.value == 'stationary' %}selected{% endif %}>Stationary</option>
              <option value="transect" {% if detail.session.kind.value == 'transect' %}selected{% endif %}>Transect</option>
            </select>
          </label>
```

- [ ] **Step 5: `web/app.py` — drop `transect_distance_m`**

```python
def create_app(
    engine: Engine,
    static_root: Path,
    media_root: Path,
) -> flask.Flask:
    """`static_root` is `Config.static_root` -- where
    `services/vendor_assets.py`'s `ensure_vendor_assets` fetches Leaflet/HTMX/Alpine.
    Served from a dedicated `vendor` Blueprint (its own `static_folder`),
    kept separate from the app's own default static folder (which serves
    this package's own committed `app.js`/`app.css` -- Task 7) so the two
    genuinely different kinds of static content (fetched-at-setup-time vs.
    committed-with-the-code) never share one directory or one config knob.

    `media_root` is `Config.media_root` -- where `jobs/tasks.py` writes
    derived spectrograms and previews, served by the `media` Blueprint (see
    `web/views/media.py`).
    """
    app = flask.Flask(__name__)
    app.config["ENGINE"] = engine
    app.config["MEDIA_ROOT"] = media_root
    app.jinja_env.filters["detector_label"] = detector_label
```

(Only the signature, docstring, and the `app.config["TRANSECT_DISTANCE_M"] = transect_distance_m` line change — everything else in the function is untouched.)

- [ ] **Step 6: `config.py` — drop `transect_distance_m` end to end**

Remove the env var constant (today's line 36):

```python
ENV_TRANSECT_DISTANCE_M = "FLEDERMAP_TRANSECT_DISTANCE_M"
```

Remove `"transect_distance_m",` from `_KNOWN_FILE_KEYS` (today's line 58).

Remove the field and its comment from the `Config` dataclass (today's lines 260-265):

```python
    # Design spec 2026-08-27-fledermap-phase5b-sessions-design.md section 6:
    # the GPS-spread threshold `derive/sessions.py`'s `classify_kind` uses to
    # suggest TRANSECT over STATIONARY. Real derivation logic (unlike a UI
    # hint), so it gets the same operational-tuning treatment as
    # `site_eps_m`/`session_gap_hours` rather than a code constant.
    transect_distance_m: float = 150.0
```

`site_naming_radius_m`'s own comment two fields down references `transect_distance_m`'s default by analogy — reword it to stand alone:

```python
    # How far (metres) to search for a nearby named POI before falling back
    # to the administrative hierarchy string. Picked by analogy to
    # site_eps_m's default, not from parent-spec guidance -- this task owns
    # the default the same way P2-5 owned site_min_points's.
    site_naming_radius_m: float = 300.0
```

Remove the whole parsing block from `from_env` (today's lines 440-466):

```python
        transect_distance_raw = _lookup(
            ENV_TRANSECT_DISTANCE_M,
            "transect_distance_m",
            file_values,
        )
        if transect_distance_raw is None:
            transect_distance_m = 150.0
        else:
            label = _source_label(
                ENV_TRANSECT_DISTANCE_M,
                "transect_distance_m",
                config_path,
            )
            if isinstance(transect_distance_raw, bool):  # see session_gap_hours above
                msg = f"{label}={transect_distance_raw!r} is not a number of metres."
                raise ConfigError(msg)
            try:
                transect_distance_m = float(transect_distance_raw)
            except (TypeError, ValueError) as exc:
                msg = f"{label}={transect_distance_raw!r} is not a number of metres."
                raise ConfigError(msg) from exc
            if not transect_distance_m > 0:  # also rejects nan; see site_eps_m above
                msg = (
                    f"{label}={transect_distance_raw!r} is not a positive "
                    "number of metres."
                )
                raise ConfigError(msg)
```

Remove `transect_distance_m=transect_distance_m,` from the final `return cls(...)` call (today's line 545).

- [ ] **Step 7: `cli/main.py` — drop `transect_distance_m` from both call sites**

In the `derive` command's `partition_sessions(...)` call:

```python
        session_report = partition_sessions(
            session,
            session_gap=timedelta(hours=config.session_gap_hours),
        )
```

In the `serve` command's `create_app(...)` call:

```python
    app = create_app(
        engine,
        config.static_root,
        config.media_root,
    )
```

- [ ] **Step 8: `jobs/tasks.py` — drop `transect_distance_m` from `run_ingest_cycle`**

```python
        session_report = partition_sessions(
            session,
            session_gap=timedelta(hours=config.session_gap_hours),
        )
```

- [ ] **Step 9: `docs/setup.md` — drop the `transect_distance_m` row and example**

Delete this row from the settings table:

```
| Session-kind GPS-spread threshold (metres) | `FLEDERMAP_TRANSECT_DISTANCE_M` | `transect_distance_m` | no | `150.0` |
```

Delete this line from the commented config-file example:

```
# transect_distance_m = 150.0
```

- [ ] **Step 10: `tests/test_partition_sessions.py` — drop the classify/reclassify test block, strip `transect_distance_m`**

Delete everything from `def test_classify_kind_stationary_below_threshold() -> None:` (today's line 388) through the end of the file (today's line 604) — this removes `test_classify_kind_stationary_below_threshold`, `test_classify_kind_transect_above_threshold`, `test_classify_kind_no_gps_stays_stationary`, `test_classify_kind_one_gps_point_stays_stationary`, `test_extending_a_session_across_runs_reclassifies_it`, `test_session_built_up_across_several_recordings_in_one_run_gets_correct_kind`, and `test_locked_kind_survives_a_reclassifying_run` — all of them test `classify_kind`/`reclassify_session`/`kind_locked` behavior that no longer exists. The file must end with a single trailing newline after the last surviving test (today's `test_recording_close_to_only_one_neighbor_does_not_raise_a_proposal`, ending at line 385).

Change the import (today's line 10):

```python
from fledermap.derive.sessions import partition_sessions
```

Remove the `transect_distance_m=150.0,` line from every remaining `partition_sessions(...)` call in the surviving portion of the file — 12 occurrences, at (pre-edit) lines 38, 61, 82, 101, 117, 145, 176, 212, 248, 288, 340, 380.

(`from fledermap.domain.codes import SessionKind` stays for now — the surviving tests still construct fixture `Session(kind=SessionKind.STATIONARY, ...)` rows. Task 3's final sweep removes it.)

- [ ] **Step 11: `tests/test_resolve_merge_proposal.py` — drop the reclassification tests, strip `transect_distance_m`**

Delete `test_merge_reclassifies_session_a_when_unlocked` and `test_merge_does_not_reclassify_a_locked_session_a` in their entirety (today's lines 130-227, i.e. from `def test_merge_reclassifies_session_a_when_unlocked(engine: Engine) -> None:` up to but not including the blank line before `def test_merge_with_omitted_note_and_weather_falls_back_to_session_b`).

Remove the `transect_distance_m=150.0,` line from every remaining `resolve_merge_proposal(...)` call in the surviving portion of the file — 9 occurrences, at (pre-edit) lines 87, 109, 273, 340, 358, 376, 393, 435, 493.

(`SessionKind` stays imported and used by the `_session` helper's `kind=SessionKind.STATIONARY` default — Task 3's final sweep removes it.)

- [ ] **Step 12: `tests/test_sessions_view.py` — drop kind form handling from its tests**

In `test_session_detail_shows_edit_form_with_current_values`: remove `kind=SessionKind.TRANSECT,` from the `AnnotationSession(...)` fixture, and remove the assertion `assert 'value="transect" selected' in html`.

Rename `test_save_session_updates_kind_note_weather_and_locks` to `test_save_session_updates_note_weather_and_seen_visually`, and rewrite it:

```python
def test_save_session_updates_note_weather_and_seen_visually(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        s = AnnotationSession(
            started_at=datetime(2026, 8, 21, tzinfo=UTC),
            ended_at=datetime(2026, 8, 21, tzinfo=UTC),
            kind=SessionKind.STATIONARY,
            detector_key="EMT\x1f1",
        )
        session.add(s)
        session.commit()
        session_id = s.id

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.post(
        f"/sessions/{session_id}",
        data={
            "note": "new note",
            "weather": "clear",
            "seen_visually": "yes",
        },
    )

    assert response.status_code == 302
    assert response.location == f"/sessions/{session_id}"
    with OrmSession(engine) as session:
        refreshed = session.get(AnnotationSession, session_id)
        assert refreshed is not None
        assert refreshed.note == "new note"
        assert refreshed.weather == "clear"
        assert refreshed.seen_visually == VisualSighting.YES
```

In `test_save_session_invalid_seen_visually_returns_400`: remove `"kind": "stationary",` from the posted `data` dict (leave `data={"seen_visually": "bogus"}`).

Delete `test_save_session_invalid_kind_returns_400` in its entirety — the `kind` field, and its 400-on-invalid-value path, no longer exist.

In `test_save_session_not_found_returns_404`: remove `"kind": "stationary",` from the posted `data` dict.

(Every other `kind=SessionKind.STATIONARY,` fixture-construction line in this file — used only to build an otherwise-unrelated `AnnotationSession` fixture — stays for now; Task 3's final sweep removes it along with the `SessionKind` import.)

- [ ] **Step 13: `tests/test_config.py` — drop the `transect_distance_m` tests**

Delete these five tests in their entirety: `test_default_transect_distance`, `test_transect_distance_is_configurable`, `test_zero_transect_distance_raises_config_error`, `test_negative_transect_distance_raises_config_error`, `test_invalid_transect_distance_raises_config_error`.

Remove `ENV_TRANSECT_DISTANCE_M` from the import block at the top of the file.

- [ ] **Step 14: Run the affected tests**

Run (sandbox disabled — `db`-marked; `test_config.py` is the one exception that doesn't need it):
```
hatch test tests/test_partition_sessions.py tests/test_resolve_merge_proposal.py tests/test_sessions_view.py -v
hatch test tests/test_config.py -v
```
Expected: PASS.

- [ ] **Step 15: Lint, type-check, docs check, full suite**

Run: `hatch fmt --check && hatch run types:check`
Run (sandbox disabled): `hatch test`
Expected: all clean. `hatch test` includes `tests/test_setup_docs.py`, which fails loudly if `docs/setup.md` and `Config`'s known keys disagree — this is the check that `transect_distance_m` was removed from both sides together.

- [ ] **Step 16: Commit**

```bash
git add src/fledermap/derive/sessions.py src/fledermap/services/sessions.py \
  src/fledermap/web/views/sessions.py src/fledermap/web/templates/session_detail.html \
  src/fledermap/web/app.py src/fledermap/config.py src/fledermap/cli/main.py \
  src/fledermap/jobs/tasks.py docs/setup.md tests/test_partition_sessions.py \
  tests/test_resolve_merge_proposal.py tests/test_sessions_view.py tests/test_config.py
git commit -m "feat: remove session-kind classification, transect_distance_m config"
```

---

## Task 3: Remove the `SessionKind` schema and enum

**Files:**
- Modify: `src/fledermap/domain/codes.py`
- Modify: `src/fledermap/store/models.py`
- Create: `alembic/versions/<generated>_drop_session_kind.py`
- Modify: `tests/test_migrations.py`
- Modify: `tests/test_models.py`
- Modify: `tests/test_map_view.py`
- Modify: `tests/test_store_geo.py`
- Modify: `tests/test_partition_sessions.py`
- Modify: `tests/test_resolve_merge_proposal.py`
- Modify: `tests/test_sessions_view.py`
- Modify: `tests/test_derive_sites.py`

**Interfaces:**
- Consumes: nothing (Task 2 already removed every non-schema Python reference).
- Produces: nothing — this is a pure deletion. `Session` no longer has `kind`/`kind_locked` attributes at all.

- [ ] **Step 1: `domain/codes.py` — delete `SessionKind`**

Delete the `SessionKind` class in its entirety:

```python
class SessionKind(StrEnum):
    """Whether a session was stationary monitoring or a walked transect.

    User-set (parent spec section 9); every session derived without a UI to set
    it defaults to STATIONARY. A closed, two-member vocabulary — CHECK-enforced,
    like `Verdict`.
    """

    STATIONARY = "stationary"
    TRANSECT = "transect"
```

`VisualSighting`'s docstring, immediately below where `SessionKind` was, references it twice as a "same pattern as" comparison — reword both:

```python
class VisualSighting(StrEnum):
    """Whether a human observer saw a bat visually during a session --
    independent of acoustic detection/species ID.

    Purely user-set: nothing in this project auto-classifies it, so it has
    no `_locked` companion field to freeze against.
    `UNCLEAR` means "not recorded" as much as "genuinely ambiguous" -- every
    session defaults to it, and a merge falls back to it when neither half
    reports anything more definite (services/sessions.py's
    `resolve_merge_proposal`: YES beats UNCLEAR beats NO, since an unset
    "we don't know" carries no evidence a definite NO could outweigh).
    A closed, three-member vocabulary -- CHECK-enforced, like `Verdict`.
    """
```

- [ ] **Step 2: `store/models.py` — delete `kind`/`kind_locked`**

Delete the `kind` column declaration and its preceding comment (today's lines 197-208):

```python
    # Closed, two-value vocabulary — CHECK-enforced (matches `Verdict`'s pattern;
    # phase-2 fix, task 5 — `kind` shipped in phase 1 without a constraint).
    kind: Mapped[SessionKind] = mapped_column(
        SAEnum(
            SessionKind,
            native_enum=False,
            length=16,
            create_constraint=True,
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
        ),
        default=SessionKind.STATIONARY,
    )
```

Delete the `kind_locked` column declaration and its preceding comment (today's lines 212-216):

```python
    # True once a human has saved `kind` through the session detail form
    # (design spec 2026-08-27-fledermap-phase5b-sessions-design.md section 6)
    # -- freezes it against `derive/sessions.py`'s automatic reclassification
    # from then on, regardless of whether the saved value actually changed.
    kind_locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
```

Immediately below where `kind_locked` was, `seen_visually`'s own preceding comment (today's lines 217-221) references `kind`/`kind_locked` by name as a contrast — reword it to stand alone:

```python
    # Whether a human observer saw a bat visually during the session --
    # independent of acoustic detection/species ID (2026-08-28). Purely
    # user-set -- nothing here ever auto-classifies it. Defaults to UNCLEAR
    # ("we don't know") for both new and pre-existing sessions.
    seen_visually: Mapped[VisualSighting] = mapped_column(
```

(Only the comment changes — the `seen_visually` column declaration itself, starting at `mapped_column(`, is untouched.)

Remove `SessionKind` from the `fledermap.domain.codes` import block at the top of the file.

Remove `Boolean,` from the `from sqlalchemy import (...)` tuple at the top of the file (grepped: `kind_locked` was its only use in this file):

```python
from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
```

- [ ] **Step 3: Generate and fill in the migration**

Run (sandbox disabled):
```bash
hatch run alembic revision -m "drop session kind"
```

This creates `alembic/versions/<hash>_drop_session_kind.py` with `down_revision = "51e72cf104a2"` (current head) already filled in. Fill in `upgrade()`/`downgrade()`:

```python
def upgrade() -> None:
    """Upgrade schema."""
    op.drop_constraint("sessionkind", "session", type_="check")
    op.drop_column("session", "kind")
    op.drop_column("session", "kind_locked")


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column(
        "session",
        sa.Column(
            "kind_locked",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "session",
        sa.Column(
            "kind",
            sa.String(length=16),
            nullable=False,
            server_default="stationary",
        ),
    )
    op.create_check_constraint(
        "sessionkind", "session", "kind IN ('stationary', 'transect')"
    )
```

(`"sessionkind"` is the exact CHECK constraint name `e9a0c0f92971_phase_2_derivation_schema.py` gave it — confirmed by reading that migration before writing this plan. `server_default` on both `downgrade()` columns is required the same way `4d15c22c4f33_phase_5b_session_schema.py`'s `kind_locked` add already established: an existing populated `session` table needs a value for the `NOT NULL` `ADD COLUMN` to succeed, and the ORM's Python-side `default=` only applies to new INSERTs, not rows that already exist when the migration runs.)

- [ ] **Step 4: Run the migration drift test**

Run (sandbox disabled): `hatch test tests/test_migrations.py -v`
Expected: will still FAIL at this point — `test_migrated_kind_check_is_enforced`/`test_migrated_kind_check_accepts_every_kind` reference a constraint that no longer exists, and several other tests in the file still pass `kind` in raw SQL `INSERT`s against a column that's now gone. Fixed in the next step.

- [ ] **Step 5: `tests/test_migrations.py` — drop kind-specific tests, strip `kind` from raw SQL**

Delete `test_migrated_kind_check_is_enforced` and `test_migrated_kind_check_accepts_every_kind` in their entirety.

Remove `SessionKind` from the import block at the top of the file.

In every remaining raw-SQL `INSERT INTO session` in this file, drop the now-nonexistent `kind` column and its value. There are 7 such statements (pre-edit line numbers: 203, 216, 232, 245, 251, 281, 287) split across `test_migrated_seen_visually_check_is_enforced`, `test_migrated_seen_visually_check_accepts_every_sighting`, `test_migrated_seen_visually_defaults_to_unclear`, and `test_migrated_resolution_check_is_enforced`/`test_migrated_resolution_check_accepts_every_resolution`. For example, this pattern:

```python
                "INSERT INTO session (started_at, ended_at, kind, seen_visually)"
                " VALUES (now(), now(), 'stationary', 'not_a_sighting')"
```

becomes:

```python
                "INSERT INTO session (started_at, ended_at, seen_visually)"
                " VALUES (now(), now(), 'not_a_sighting')"
```

and this pattern:

```python
                "INSERT INTO session (started_at, ended_at, kind)"
                " VALUES (now(), now(), 'stationary')"
```

becomes:

```python
                "INSERT INTO session (started_at, ended_at)"
                " VALUES (now(), now())"
```

Apply the same transformation at each of the 7 sites — the column list loses `kind`, and the values list loses the matching `'stationary'` literal.

- [ ] **Step 6: `tests/test_models.py` — delete the kind round-trip test**

Delete `test_session_kind_round_trips_to_python_type` in its entirety (it exists solely to prove `Session.kind` round-trips as the enum type, not `str` — the column is gone).

Remove `SessionKind` from the import block at the top of the file.

- [ ] **Step 7: `tests/test_map_view.py` — drop the incidental `kind=` fixture argument**

Remove `kind=SessionKind.STATIONARY,` from both `AnnotationSession(...)` constructions in this file (in `test_site_panel_links_sessions_to_session_detail` and `test_recording_panel_links_session_to_session_detail`).

Remove `SessionKind` from the import block at the top of the file.

- [ ] **Step 8: `tests/test_store_geo.py` — reword the stale `classify_kind` reference**

`test_decode_point_decodes_an_unpersisted_wkt_element`'s docstring references `derive/sessions.py`'s `classify_kind` unit tests as the reason this behavior matters (that function is gone as of Task 2). Reword to describe the general case instead:

```python
def test_decode_point_decodes_an_unpersisted_wkt_element() -> None:
    """A `Recording` built directly in Python (never added to a session) still
    carries the `WKTElement` it was constructed with, not the `WKBElement` a
    database round-trip produces."""
```

- [ ] **Step 9: Final sweep — drop the remaining incidental `kind=`/`SessionKind` references**

In `tests/test_partition_sessions.py`: remove `kind=SessionKind.STATIONARY,` from every remaining `Session(...)` construction, then remove `SessionKind` from the import block.

In `tests/test_resolve_merge_proposal.py`: remove `kind=SessionKind.STATIONARY,` from the `_session` helper's `Session(...)` construction, then remove `SessionKind` from the import block.

In `tests/test_sessions_view.py`: remove every remaining `kind=SessionKind.STATIONARY,` line from `AnnotationSession(...)` constructions, then remove `SessionKind` from the import block.

In `tests/test_derive_sites.py`: rewrite `test_a_transect_sessions_identified_recordings_now_form_a_site` to construct its `Session` without `kind=SessionKind.TRANSECT` (the model no longer has the attribute):

```python
def test_a_transect_sessions_identified_recordings_now_form_a_site(
    engine: Engine,
) -> None:
    """Regression test for the bug that motivated this design: a walked
    transect that passes through a real hotspot used to be entirely invisible
    to site derivation. Site membership no longer cares what session a
    recording belongs to -- this session used to be the kind of thing
    `derive_sites` structurally excluded (design spec
    2026-08-29-fledermap-identification-based-sites-design.md)."""
    with OrmSession(engine) as session:
        walked = Session(
            started_at=datetime(2026, 8, 21, 21, tzinfo=UTC),
            ended_at=datetime(2026, 8, 21, 23, tzinfo=UTC),
            detector_key="EMT\x1f1",
        )
        session.add(walked)
        session.flush()
        _recording("a", session, 13.4000, 52.5000, session_id=walked.id)
        _recording("b", session, 13.4001, 52.5000, session_id=walked.id)
        session.commit()

        report = derive_sites(session, eps_m=75.0, min_points=2)
        session.commit()

        assert report.site_count == 1
        assert report.unclustered == 0
```

Remove `SessionKind` from this file's import block (keep `IdSource, Verdict`).

Run this exact check and confirm it prints nothing:
```bash
grep -rn "SessionKind\|\.kind_locked\|kind=SessionKind" src/ tests/
```

- [ ] **Step 10: Run the migration test and full suite**

Run (sandbox disabled): `hatch test tests/test_migrations.py -v`
Expected: PASS, no drift.

Run (sandbox disabled): `hatch test`
Expected: PASS, all tests, no warnings.

- [ ] **Step 11: Lint and type-check**

Run: `hatch fmt --check && hatch run types:check`
Expected: clean.

- [ ] **Step 12: Commit**

```bash
git add src/fledermap/domain/codes.py src/fledermap/store/models.py alembic/versions/ \
  tests/test_migrations.py tests/test_models.py tests/test_map_view.py \
  tests/test_store_geo.py tests/test_partition_sessions.py \
  tests/test_resolve_merge_proposal.py tests/test_sessions_view.py tests/test_derive_sites.py
git commit -m "feat: drop SessionKind schema and enum entirely"
```

---

## Task 4: Supersede the two design specs this reverses

**Files:**
- Modify: `docs/superpowers/specs/2026-08-24-fledermap-phase2-derivation-design.md`
- Modify: `docs/superpowers/specs/2026-08-27-fledermap-phase5b-sessions-design.md`

**Interfaces:**
- Consumes: nothing.
- Produces: nothing — documentation only.

- [ ] **Step 1: Add a superseded note to the Phase 2 derivation spec**

Immediately after the header block (today's line 7, the `---` separator after the "Parent spec" line), insert:

```markdown
> **Superseded 2026-08-29** by
> `docs/superpowers/specs/2026-08-29-fledermap-identification-based-sites-design.md`: section 12's
> STATIONARY-only site derivation (`derive_sites` joining `Session.kind`) no longer holds. Site
> membership is now identification-based (`Verdict.SPECIES`, via `current_best_identification`),
> independent of session or session kind. Left as-is below for historical record of the original
> decision and its reasoning.
```

- [ ] **Step 2: Add a superseded note to the Phase 5b sessions spec**

Immediately after this spec's own header (before its "## 1. Scope" section), insert:

```markdown
> **Superseded 2026-08-29** by
> `docs/superpowers/specs/2026-08-29-fledermap-identification-based-sites-design.md`: the
> GPS-spread `classify_kind` heuristic, `Session.kind`/`kind_locked`, and the session detail
> page's "Kind" field described below were removed entirely — site derivation (their only
> consumer) no longer depends on session kind. `/sessions` and `/sessions/{id}` themselves, and
> everything else this spec describes (merge proposals, notes/weather, the nav sidebar), are
> unaffected. Left as-is below for historical record of the original decision and its reasoning.
```

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/specs/2026-08-24-fledermap-phase2-derivation-design.md \
  docs/superpowers/specs/2026-08-27-fledermap-phase5b-sessions-design.md
git commit -m "docs: mark phase2/phase5b specs superseded by identification-based sites"
```
