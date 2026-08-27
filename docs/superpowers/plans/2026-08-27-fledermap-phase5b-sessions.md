# Fledermap Phase 5b (Sessions List + Detail) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `/sessions` (list) and `/sessions/{id}` (detail: edit `kind`/`note`/`weather`, resolve merge proposals, a per-session mini-map), plus the app's first global navigation, per the approved design.

**Architecture:** New `services/sessions.py` (query + mutation logic, mirroring `services/map_query.py`'s existing shape) sits between new Flask routes in `web/views/sessions.py` and the store. `kind` classification is real derivation logic living in `derive/sessions.py` (reopening Phase 2's `partition_sessions`), gated by a new `Session.kind_locked` column so a human's saved choice is never silently overwritten by a later `derive` run. The `effort` column is dropped. A collapsible left sidebar (`_nav.html`) becomes the app's first persistent navigation, included on all three HTML pages.

**Tech Stack:** Flask + Jinja2 (existing), SQLAlchemy + Alembic (existing), Leaflet (existing, vendored), Alpine.js (existing, vendored), scipy/shapely/numpy (existing deps) for the GPS-spread classifier.

**Spec:** `docs/superpowers/specs/2026-08-27-fledermap-phase5b-sessions-design.md`

## Global Constraints

- **D16 (read-only archive):** untouched by this plan — nothing here reads or writes archive files.
- **Postgres NULL semantics:** N/A this plan (no new `UniqueConstraint` on a nullable column).
- **`np.float64` has no psycopg2 adapter:** if any new query parameter is a numpy scalar (none currently planned, but watch for it in the GPS-spread classifier's distance value before it reaches any DB write), cast to plain `float()` first.
- **SQLAlchemy's `Enum` persists the member name, not `.value`:** `values_callable=lambda enum_cls: [e.value for e in enum_cls]` on every new/changed `SAEnum` column — none are added by this plan (`kind_locked` is a plain boolean), but the existing `kind`/`resolution` columns this plan reads and writes must not be redeclared without it.
- **`hatch fmt --check` and `hatch run types:check` must be clean** after every task, including `tests/` (mypy covers tests too).
- **Test output must be pristine** — no warnings.
- **Run `git`/`alembic` unsandboxed** (`dangerouslyDisableSandbox: true`) — sandboxed git config writes leave a stale `.git/config.lock`, and generating a migration touches `alembic/versions/`.
- **`db`-marked tests need `dangerouslyDisableSandbox: true`** (testcontainers + PostGIS; Docker is blocked by the sandbox and the failure looks like a network fault, not a permissions one).

---

## Task 1: Schema — drop `effort`, add `Session.kind_locked`

**Files:**
- Modify: `src/fledermap/store/models.py:194` (the `Session` class)
- Create: `alembic/versions/<generated>_phase_5b_session_schema.py`
- Test: `tests/test_migrations.py` (existing drift test — no new test needed; this task's job is to keep it green)

**Interfaces:**
- Produces: `Session.kind_locked: bool` (default `False`) — read/written by `derive/sessions.py` (Task 3) and `web/views/sessions.py` (Task 8).

- [ ] **Step 1: Update the model**

In `src/fledermap/store/models.py`, in the `Session` class, remove the `effort` line and add `kind_locked` after `weather`:

```python
    detector_key: Mapped[str | None] = mapped_column(String(160), index=True)
    note: Mapped[str | None] = mapped_column(Text)
    weather: Mapped[str | None] = mapped_column(Text)
    # True once a human has saved `kind` through the session detail form
    # (design spec 2026-08-27-fledermap-phase5b-sessions-design.md section 6)
    # -- freezes it against `derive/sessions.py`'s automatic reclassification
    # from then on, regardless of whether the saved value actually changed.
    kind_locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
```

(`effort` is gone entirely -- grepped every reference in the repo before removing it: the only code that touched it was this column declaration. No domain definition exists anywhere, including `docs/references.md`. See the design spec section 6 for the full reasoning.)

`Boolean` is not currently imported in this file (verified: the existing `from sqlalchemy import (...)` block at the top has `DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint`, no `Boolean`). Add it to that tuple:

```python
from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
```

- [ ] **Step 2: Generate the migration skeleton**

Run (no live database needed — `alembic revision` without `--autogenerate` only reads the versions directory to find head):

```bash
cd /home/janna/projekte/bats && dangerouslyDisableSandbox=true alembic revision -m "phase 5b session schema"
```

This creates `alembic/versions/<hash>_phase_5b_session_schema.py` with `down_revision = "e9a0c0f92971"` (current head) already filled in, and empty `upgrade()`/`downgrade()` stubs.

- [ ] **Step 3: Fill in `upgrade()`/`downgrade()`**

Edit the generated file's body:

```python
def upgrade() -> None:
    """Upgrade schema."""
    op.drop_column("session", "effort")
    op.add_column(
        "session",
        sa.Column(
            "kind_locked",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("session", "kind_locked")
    op.add_column("session", sa.Column("effort", sa.Text(), nullable=True))
```

(Plain column add/drop — no enum/CHECK involved, so `tests/test_migrations.py`'s `_comparable` exclusion needs no changes; `compare_metadata` sees this drift directly. `server_default=sa.false()` is required on the `ADD COLUMN ... NOT NULL` step so existing rows in a populated `session` table get a value — mirrors the model's own `default=False`, which only applies to new INSERTs via the ORM, not to rows that already exist when this migration runs.)

- [ ] **Step 4: Run the migration drift test**

Run: `dangerouslyDisableSandbox=true hatch test tests/test_migrations.py -v`
Expected: PASS, no drift detected.

- [ ] **Step 5: Lint and type-check**

Run: `hatch fmt --check && hatch run types:check`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
cd /home/janna/projekte/bats && dangerouslyDisableSandbox=true git add src/fledermap/store/models.py alembic/versions/
dangerouslyDisableSandbox=true git commit -m "feat: drop Session.effort, add Session.kind_locked"
```

---

## Task 2: `Config.transect_distance_m`

**Files:**
- Modify: `src/fledermap/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `Config.transect_distance_m: float` (default `150.0`), `ENV_TRANSECT_DISTANCE_M = "FLEDERMAP_TRANSECT_DISTANCE_M"` — consumed by `derive/sessions.py`'s `classify_kind` (Task 3) via the CLI, and by `web/app.py`'s `create_app` (Task 9) for the web-triggered reclassification path.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_config.py`, near the `site_eps_m` tests (mirror their exact shape):

```python
def test_default_transect_distance(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_MEDIA_ROOT, str(tmp_path / "media"))
    monkeypatch.delenv(ENV_TRANSECT_DISTANCE_M, raising=False)
    config = Config.from_env(tmp_path)
    assert config.transect_distance_m == 150.0


def test_transect_distance_is_configurable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_MEDIA_ROOT, str(tmp_path / "media"))
    monkeypatch.setenv(ENV_TRANSECT_DISTANCE_M, "300")
    config = Config.from_env(tmp_path)
    assert config.transect_distance_m == 300.0


def test_zero_transect_distance_raises_config_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_TRANSECT_DISTANCE_M, "0")
    with pytest.raises(ConfigError, match=ENV_TRANSECT_DISTANCE_M):
        Config.from_env(tmp_path)


def test_negative_transect_distance_raises_config_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_TRANSECT_DISTANCE_M, "-1")
    with pytest.raises(ConfigError, match=ENV_TRANSECT_DISTANCE_M):
        Config.from_env(tmp_path)


def test_invalid_transect_distance_raises_config_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_TRANSECT_DISTANCE_M, "not-a-number")
    with pytest.raises(ConfigError, match="not-a-number"):
        Config.from_env(tmp_path)
```

Add `ENV_TRANSECT_DISTANCE_M` to the existing import line from `fledermap.config` at the top of `tests/test_config.py`.

- [ ] **Step 2: Run to verify failure**

Run: `hatch test tests/test_config.py -k transect_distance -v`
Expected: FAIL (`ImportError` / `AttributeError` — `ENV_TRANSECT_DISTANCE_M`/`transect_distance_m` don't exist yet).

- [ ] **Step 3: Implement**

In `src/fledermap/config.py`:

Add the env var constant next to `ENV_SITE_MIN_POINTS`:
```python
ENV_TRANSECT_DISTANCE_M = "FLEDERMAP_TRANSECT_DISTANCE_M"
```

Add `"transect_distance_m"` to `_KNOWN_FILE_KEYS` (alongside `"site_min_points"`).

Add the field to the `Config` dataclass, next to `site_min_points`:
```python
    # Design spec 2026-08-27-fledermap-phase5b-sessions-design.md section 6:
    # the GPS-spread threshold `derive/sessions.py`'s `classify_kind` uses to
    # suggest TRANSECT over STATIONARY. Real derivation logic (unlike a UI
    # hint), so it gets the same operational-tuning treatment as
    # `site_eps_m`/`session_gap_hours` rather than a code constant.
    transect_distance_m: float = 150.0
```

In `Config.from_env`, add parsing right after the `site_min_points_raw` block, mirroring `site_eps_raw`'s exact pattern:
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

Add `transect_distance_m=transect_distance_m` to the final `return cls(...)` call (this is the step this project's own CLAUDE.md flags as easy to miss — a parsed-and-validated local variable silently dropped on the floor if it never reaches `cls(...)`; the tests in Step 1 assert the constructed `Config`'s attribute, not just that parsing didn't raise, precisely to catch that).

- [ ] **Step 4: Run to verify pass**

Run: `hatch test tests/test_config.py -k transect_distance -v`
Expected: PASS.

- [ ] **Step 5: Lint and type-check**

Run: `hatch fmt --check && hatch run types:check`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
dangerouslyDisableSandbox=true git add src/fledermap/config.py tests/test_config.py
dangerouslyDisableSandbox=true git commit -m "feat: Config.transect_distance_m (FLEDERMAP_TRANSECT_DISTANCE_M, optional)"
```

---

## Task 3: Persisted `kind` classification in `derive/sessions.py`

**Files:**
- Modify: `src/fledermap/derive/sessions.py`
- Modify: `src/fledermap/cli/main.py` (the `derive` command's `partition_sessions` call)
- Test: `tests/test_partition_sessions.py`

**Interfaces:**
- Consumes: `Config.transect_distance_m: float` (Task 2), `fledermap.store.geo.decode_point(elem) -> tuple[float, float] | None`, `fledermap.util.projection.LocalProjection` (existing).
- Produces: `classify_kind(recordings: Sequence[Recording], *, transect_distance_m: float) -> SessionKind`, `reclassify_session(db_session: OrmSession, session_obj: Session, *, transect_distance_m: float) -> None` — both consumed by `services/sessions.py`'s `resolve_merge_proposal` (Task 6). `partition_sessions` gains a required keyword-only `transect_distance_m: float` parameter.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_partition_sessions.py` (mirroring `_recording`'s existing helper; `geom` needs `WKTElement`, same as `test_derive_sites.py`):

```python
from geoalchemy2.elements import WKTElement

from fledermap.derive.sessions import classify_kind, partition_sessions


def test_classify_kind_stationary_below_threshold() -> None:
    recordings = [
        Recording(
            audio_hash="a".rjust(64, "0"),
            path="a.wav",
            recorded_at=datetime(2026, 8, 21, 21, tzinfo=UTC),
            geom=WKTElement("POINT(10.0 51.0)", srid=4326),
        ),
        Recording(
            audio_hash="b".rjust(64, "0"),
            path="b.wav",
            recorded_at=datetime(2026, 8, 21, 22, tzinfo=UTC),
            geom=WKTElement("POINT(10.0002 51.0)", srid=4326),  # ~14m east
        ),
    ]
    assert (
        classify_kind(recordings, transect_distance_m=150.0) == SessionKind.STATIONARY
    )


def test_classify_kind_transect_above_threshold() -> None:
    recordings = [
        Recording(
            audio_hash="a".rjust(64, "0"),
            path="a.wav",
            recorded_at=datetime(2026, 8, 21, 21, tzinfo=UTC),
            geom=WKTElement("POINT(10.0 51.0)", srid=4326),
        ),
        Recording(
            audio_hash="b".rjust(64, "0"),
            path="b.wav",
            recorded_at=datetime(2026, 8, 21, 22, tzinfo=UTC),
            geom=WKTElement("POINT(10.01 51.0)", srid=4326),  # ~700m east
        ),
    ]
    assert (
        classify_kind(recordings, transect_distance_m=150.0) == SessionKind.TRANSECT
    )


def test_classify_kind_no_gps_stays_stationary() -> None:
    recordings = [
        Recording(
            audio_hash="a".rjust(64, "0"),
            path="a.wav",
            recorded_at=datetime(2026, 8, 21, 21, tzinfo=UTC),
            geom=None,
        ),
    ]
    assert (
        classify_kind(recordings, transect_distance_m=150.0) == SessionKind.STATIONARY
    )


def test_classify_kind_one_gps_point_stays_stationary() -> None:
    recordings = [
        Recording(
            audio_hash="a".rjust(64, "0"),
            path="a.wav",
            recorded_at=datetime(2026, 8, 21, 21, tzinfo=UTC),
            geom=WKTElement("POINT(10.0 51.0)", srid=4326),
        ),
    ]
    assert (
        classify_kind(recordings, transect_distance_m=150.0) == SessionKind.STATIONARY
    )


def test_extending_a_session_across_runs_reclassifies_it(engine: Engine) -> None:
    """The realistic trickle-ingestion case (design spec section 6): a session
    created by one `derive` run with a single GPS point stays STATIONARY (no
    spread yet); a second run adding a distant point must reclassify it to
    TRANSECT."""
    with OrmSession(engine) as session:
        base = datetime(2026, 8, 21, 21, tzinfo=UTC)
        session.add(
            _recording(
                "a", base, make="EMT", serial="1",
                geom=WKTElement("POINT(10.0 51.0)", srid=4326),
            ),
        )
        session.commit()
        partition_sessions(
            session, session_gap=timedelta(hours=6), transect_distance_m=150.0,
        )
        session.commit()
        created = session.scalars(select(Session)).one()
        assert created.kind == SessionKind.STATIONARY

        session.add(
            _recording(
                "b", base + timedelta(hours=1), make="EMT", serial="1",
                geom=WKTElement("POINT(10.01 51.0)", srid=4326),  # ~700m away
            ),
        )
        session.commit()
        partition_sessions(
            session, session_gap=timedelta(hours=6), transect_distance_m=150.0,
        )
        session.commit()
        extended = session.scalars(select(Session)).one()
        assert extended.kind == SessionKind.TRANSECT


def test_locked_kind_survives_a_reclassifying_run(engine: Engine) -> None:
    with OrmSession(engine) as session:
        base = datetime(2026, 8, 21, 21, tzinfo=UTC)
        existing = Session(
            started_at=base,
            ended_at=base,
            kind=SessionKind.STATIONARY,
            kind_locked=True,
            detector_key="EMT\x1f1",
        )
        session.add(existing)
        session.flush()
        session.add(
            _recording(
                "a", base + timedelta(hours=1), make="EMT", serial="1",
                geom=WKTElement("POINT(10.01 51.0)", srid=4326),  # would flip it
            ),
        )
        session.commit()

        partition_sessions(
            session, session_gap=timedelta(hours=6), transect_distance_m=150.0,
        )
        session.commit()

        unchanged = session.scalars(select(Session)).one()
        assert unchanged.kind == SessionKind.STATIONARY
        assert unchanged.kind_locked is True
```

Update every other existing `partition_sessions(session, session_gap=timedelta(hours=6))` call already in this file to add `, transect_distance_m=150.0` (the new parameter is required, so every existing call site breaks otherwise — grep the file for `partition_sessions(` to find them all).

- [ ] **Step 2: Run to verify failure**

Run: `dangerouslyDisableSandbox=true hatch test tests/test_partition_sessions.py -v`
Expected: FAIL (`classify_kind` doesn't exist; `partition_sessions` doesn't accept `transect_distance_m`).

- [ ] **Step 3: Implement `classify_kind` and `reclassify_session`**

In `src/fledermap/derive/sessions.py`, add imports and the two functions:

```python
import numpy as np
from scipy.spatial.distance import pdist
from shapely.geometry import MultiPoint

from fledermap.store.geo import decode_point
from fledermap.util.projection import LocalProjection
```

```python
def classify_kind(
    recordings: Sequence[Recording],
    *,
    transect_distance_m: float,
) -> SessionKind:
    """GPS-spread heuristic (design spec 2026-08-27-fledermap-phase5b-sessions-design.md
    section 6): the maximum pairwise distance among `recordings`' GPS-bearing
    positions, projected via the same `LocalProjection` site clustering
    already uses (`derive/sites.py`) so the threshold is in metres, not
    degrees. Two or more points spread beyond `transect_distance_m` suggest a
    walked transect; fewer than two GPS-bearing recordings (not enough
    signal) stays `STATIONARY`, matching this project's existing default.
    """
    points = [decode_point(r.geom) for r in recordings]
    gps_points = [p for p in points if p is not None]
    if len(gps_points) < 2:
        return SessionKind.STATIONARY
    lonlat = np.array(gps_points)
    projection = LocalProjection(MultiPoint(lonlat.tolist()))
    local = projection.to_local_np(lonlat)
    max_distance = float(pdist(local).max())
    return (
        SessionKind.TRANSECT
        if max_distance > transect_distance_m
        else SessionKind.STATIONARY
    )


def reclassify_session(
    db_session: OrmSession,
    session_obj: Session,
    *,
    transect_distance_m: float,
) -> None:
    """Recompute `session_obj.kind` from its complete current set of
    GPS-bearing recordings, unless a human has already locked it in via the
    session detail form (`Session.kind_locked`). Queries `Recording` by
    `session_id` fresh rather than trusting a caller's partial in-memory
    batch: a session's membership can be built up across several separate
    `derive` runs, or by `services/sessions.py`'s `resolve_merge_proposal`,
    and this must see all of it -- SQLAlchemy's session-level autoflush
    means any pending `recording.session_id` change from earlier in the same
    call flushes before this SELECT runs, so no manual flush is needed here.
    """
    if session_obj.kind_locked:
        return
    recordings = db_session.scalars(
        select(Recording).where(Recording.session_id == session_obj.id),
    ).all()
    session_obj.kind = classify_kind(
        recordings, transect_distance_m=transect_distance_m,
    )
```

- [ ] **Step 4: Wire `reclassify_session` into all four `partition_sessions` paths**

In `partition_sessions`, add the `transect_distance_m: float` parameter to the signature:

```python
def partition_sessions(
    db_session: OrmSession,
    *,
    session_gap: timedelta,
    transect_distance_m: float,
) -> SessionPartitionReport:
```

Then call `reclassify_session` after each of the four places a recording gets assigned a `session_id`:

1. After the overlap-join (`recording.session_id = prev_session.id` inside the `if (prev_session is not None and prev_session.ended_at >= recording.recorded_at):` block):
```python
            if (
                prev_session is not None
                and prev_session.ended_at >= recording.recorded_at
            ):
                recording.session_id = prev_session.id
                reclassify_session(
                    db_session, prev_session, transect_distance_m=transect_distance_m,
                )
                report.extended += 1
                continue
```

2. After the `joins_prev` extend (`recording.session_id = prev_session.id` in `if joins_prev:`):
```python
            if joins_prev:
                assert prev_session is not None  # joins_prev implies this
                prev_session.ended_at = recording.recorded_at
                recording.session_id = prev_session.id
                reclassify_session(
                    db_session, prev_session, transect_distance_m=transect_distance_m,
                )
                report.extended += 1
```
(leave the rest of the `if joins_prev:` block, including the merge-proposal logic, unchanged)

3. After the `joins_next` extend (`recording.session_id = next_session.id` in `elif joins_next:`):
```python
            elif joins_next:
                assert next_session is not None  # joins_next implies this
                next_session.started_at = recording.recorded_at
                starts[idx] = next_session.started_at
                recording.session_id = next_session.id
                reclassify_session(
                    db_session, next_session, transect_distance_m=transect_distance_m,
                )
                report.extended += 1
```

4. After creation (`recording.session_id = new_session.id` in the `else:` branch) -- note `kind=SessionKind.STATIONARY` in the `Session(...)` constructor stays as the initial value (a session with exactly one recording always classifies to STATIONARY anyway per `classify_kind`'s "fewer than two GPS points" rule, so this is just the sensible starting value before the call below can run):
```python
            else:
                new_session = Session(
                    started_at=recording.recorded_at,
                    ended_at=recording.recorded_at,
                    kind=SessionKind.STATIONARY,
                    detector_key=key,
                )
                db_session.add(new_session)
                db_session.flush()
                recording.session_id = new_session.id
                reclassify_session(
                    db_session, new_session, transect_distance_m=transect_distance_m,
                )
                report.created += 1

                insert_at = bisect.bisect_right(starts, new_session.started_at)
                existing.insert(insert_at, new_session)
                starts.insert(insert_at, new_session.started_at)
```

- [ ] **Step 5: Update the CLI call site**

In `src/fledermap/cli/main.py`, the `derive` command's call to `partition_sessions` (around line 212):

```python
        session_report = partition_sessions(
            session,
            session_gap=timedelta(hours=config.session_gap_hours),
            transect_distance_m=config.transect_distance_m,
        )
```

- [ ] **Step 6: Run to verify pass**

Run: `dangerouslyDisableSandbox=true hatch test tests/test_partition_sessions.py -v`
Expected: PASS.

- [ ] **Step 7: Full suite, lint, type-check**

Run: `dangerouslyDisableSandbox=true hatch test && hatch fmt --check && hatch run types:check`
Expected: clean (this touches `cli/main.py`, so `tests/test_cli.py` must still pass unchanged).

- [ ] **Step 8: Commit**

```bash
dangerouslyDisableSandbox=true git add src/fledermap/derive/sessions.py src/fledermap/cli/main.py tests/test_partition_sessions.py
dangerouslyDisableSandbox=true git commit -m "feat: persist kind classification in derive/sessions.py"
```

---

## Task 4: `services/sessions.py` — `filtered_sessions`

**Files:**
- Create: `src/fledermap/services/sessions.py`
- Test: `tests/test_sessions_query.py`

**Interfaces:**
- Produces: `SessionListRow` (dataclass: `session: AnnotationSession`, `recording_count: int`), `filtered_sessions(db_session, *, detector=None, date_from=None, date_to=None, open_proposals_only=False) -> Sequence[SessionListRow]`, `open_proposal_session_ids(db_session) -> set[int]` — consumed by `web/views/sessions.py` (Task 7).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_sessions_query.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import MergeResolution, SessionKind
from fledermap.services.sessions import (
    filtered_sessions,
    open_proposal_session_ids,
)
from fledermap.store.models import Recording, Session, SessionMergeProposal

pytestmark = pytest.mark.db


def _session(detector_key: str, started: datetime, ended: datetime) -> Session:
    return Session(
        started_at=started, ended_at=ended, kind=SessionKind.STATIONARY,
        detector_key=detector_key,
    )


def test_filtered_sessions_orders_newest_first(engine: Engine) -> None:
    with OrmSession(engine) as session:
        base = datetime(2026, 8, 20, tzinfo=UTC)
        session.add(_session("EMT\x1f1", base, base))
        session.add(_session("EMT\x1f1", base.replace(day=25), base.replace(day=25)))
        session.commit()

        rows = filtered_sessions(session)
        assert [row.session.started_at.day for row in rows] == [25, 20]


def test_filtered_sessions_by_detector_substring(engine: Engine) -> None:
    with OrmSession(engine) as session:
        base = datetime(2026, 8, 20, tzinfo=UTC)
        session.add(_session("EMT\x1f1", base, base))
        session.add(_session("Kaleidoscope\x1f2", base, base))
        session.commit()

        rows = filtered_sessions(session, detector="EMT")
        assert len(rows) == 1
        assert rows[0].session.detector_key == "EMT\x1f1"


def test_filtered_sessions_by_date_range(engine: Engine) -> None:
    with OrmSession(engine) as session:
        base = datetime(2026, 8, 20, tzinfo=UTC)
        session.add(_session("EMT\x1f1", base, base))
        session.add(_session("EMT\x1f1", base.replace(day=1), base.replace(day=1)))
        session.commit()

        rows = filtered_sessions(session, date_from=base.replace(day=10))
        assert len(rows) == 1
        assert rows[0].session.started_at.day == 20


def test_filtered_sessions_reports_recording_count(engine: Engine) -> None:
    with OrmSession(engine) as session:
        base = datetime(2026, 8, 20, tzinfo=UTC)
        s = _session("EMT\x1f1", base, base)
        session.add(s)
        session.flush()
        session.add(
            Recording(
                audio_hash="a".rjust(64, "0"), path="a.wav", recorded_at=base,
                session_id=s.id,
            ),
        )
        session.add(
            Recording(
                audio_hash="b".rjust(64, "0"), path="b.wav", recorded_at=base,
                session_id=s.id,
            ),
        )
        session.commit()

        rows = filtered_sessions(session)
        assert rows[0].recording_count == 2


def test_filtered_sessions_recording_count_zero_when_none(engine: Engine) -> None:
    with OrmSession(engine) as session:
        base = datetime(2026, 8, 20, tzinfo=UTC)
        session.add(_session("EMT\x1f1", base, base))
        session.commit()

        rows = filtered_sessions(session)
        assert rows[0].recording_count == 0


def test_open_proposals_only_filters_to_sessions_with_an_open_proposal(
    engine: Engine,
) -> None:
    with OrmSession(engine) as session:
        base = datetime(2026, 8, 20, tzinfo=UTC)
        a = _session("EMT\x1f1", base, base)
        b = _session("EMT\x1f1", base.replace(day=21), base.replace(day=21))
        untouched = _session("EMT\x1f2", base, base)
        session.add_all([a, b, untouched])
        session.flush()
        session.add(
            Recording(
                audio_hash="x".rjust(64, "0"), path="x.wav", recorded_at=base,
                session_id=a.id,
            ),
        )
        session.flush()
        bridging = session.query(Recording).one()
        session.add(
            SessionMergeProposal(
                session_a_id=a.id, session_b_id=b.id,
                bridging_recording_id=bridging.id, detected_at=base,
            ),
        )
        session.commit()

        rows = filtered_sessions(session, open_proposals_only=True)
        assert {row.session.id for row in rows} == {a.id, b.id}


def test_resolved_proposal_does_not_count_as_open(engine: Engine) -> None:
    with OrmSession(engine) as session:
        base = datetime(2026, 8, 20, tzinfo=UTC)
        a = _session("EMT\x1f1", base, base)
        b = _session("EMT\x1f1", base.replace(day=21), base.replace(day=21))
        session.add_all([a, b])
        session.flush()
        session.add(
            Recording(
                audio_hash="x".rjust(64, "0"), path="x.wav", recorded_at=base,
                session_id=a.id,
            ),
        )
        session.flush()
        bridging = session.query(Recording).one()
        session.add(
            SessionMergeProposal(
                session_a_id=a.id, session_b_id=b.id,
                bridging_recording_id=bridging.id, detected_at=base,
                resolution=MergeResolution.REJECTED, resolved_at=base,
            ),
        )
        session.commit()

        assert open_proposal_session_ids(session) == set()


def test_filtered_sessions_runs_with_multiple_rows(engine: Engine) -> None:
    """Not a test of the MAX_SESSIONS=200 cap itself -- exercising that
    boundary would mean seeding 200+ rows for a check the design spec
    explicitly deprioritized ("revisit if a real dataset ever approaches the
    cap"). This just confirms the unfiltered query returns every matching
    row, as a sanity check that .limit() didn't get applied too early."""
    with OrmSession(engine) as session:
        base = datetime(2026, 8, 20, tzinfo=UTC)
        for day in range(1, 3):
            session.add(_session("EMT\x1f1", base.replace(day=day), base.replace(day=day)))
        session.commit()

        rows = filtered_sessions(session)
        assert len(rows) == 2
```

- [ ] **Step 2: Run to verify failure**

Run: `dangerouslyDisableSandbox=true hatch test tests/test_sessions_query.py -v`
Expected: FAIL (`fledermap.services.sessions` doesn't exist).

- [ ] **Step 3: Implement**

Create `src/fledermap/services/sessions.py`:

```python
"""Sessions list/detail query and mutation logic (design spec
2026-08-27-fledermap-phase5b-sessions-design.md), mirroring
`services/map_query.py`'s existing shape: filtering runs in SQL where the
field lives directly on `Session`, assembled results are dataclasses so
templates never make N+1 lookups."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session as OrmSession

from fledermap.store.models import Recording
from fledermap.store.models import Session as AnnotationSession
from fledermap.store.models import SessionMergeProposal

# Design spec section 3: a capped LIMIT stands in for real pagination at this
# project's established real-deployment scale (a handful of detectors).
MAX_SESSIONS = 200


@dataclass(frozen=True)
class SessionListRow:
    """One row of the `/sessions` list -- `Session` plus its recording count,
    assembled here rather than via a `relationship()` (none exists; adding
    one purely to satisfy a list-page count would let template code drive an
    N+1 query per row)."""

    session: AnnotationSession
    recording_count: int


def open_proposal_session_ids(db_session: OrmSession) -> set[int]:
    """Every session id currently part of at least one unresolved
    `SessionMergeProposal`, either side."""
    a_ids = db_session.scalars(
        select(SessionMergeProposal.session_a_id).where(
            SessionMergeProposal.resolution.is_(None),
        ),
    )
    b_ids = db_session.scalars(
        select(SessionMergeProposal.session_b_id).where(
            SessionMergeProposal.resolution.is_(None),
        ),
    )
    return set(a_ids) | set(b_ids)


def filtered_sessions(
    db_session: OrmSession,
    *,
    detector: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    open_proposals_only: bool = False,
) -> Sequence[SessionListRow]:
    stmt = select(AnnotationSession).order_by(AnnotationSession.started_at.desc())
    if detector:
        stmt = stmt.where(AnnotationSession.detector_key.ilike(f"%{detector}%"))
    if date_from is not None:
        stmt = stmt.where(AnnotationSession.started_at >= date_from)
    if date_to is not None:
        stmt = stmt.where(AnnotationSession.started_at <= date_to)
    if open_proposals_only:
        open_ids = open_proposal_session_ids(db_session)
        if not open_ids:
            return []
        stmt = stmt.where(AnnotationSession.id.in_(open_ids))
    stmt = stmt.limit(MAX_SESSIONS)

    sessions = list(db_session.scalars(stmt).all())
    if not sessions:
        return []

    session_ids = [s.id for s in sessions]
    counts = dict(
        db_session.execute(
            select(Recording.session_id, func.count(Recording.id))
            .where(Recording.session_id.in_(session_ids))
            .group_by(Recording.session_id),
        ).all(),
    )
    return [
        SessionListRow(session=s, recording_count=counts.get(s.id, 0))
        for s in sessions
    ]
```

- [ ] **Step 4: Run to verify pass**

Run: `dangerouslyDisableSandbox=true hatch test tests/test_sessions_query.py -v`
Expected: PASS.

- [ ] **Step 5: Lint and type-check**

Run: `hatch fmt --check && hatch run types:check`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
dangerouslyDisableSandbox=true git add src/fledermap/services/sessions.py tests/test_sessions_query.py
dangerouslyDisableSandbox=true git commit -m "feat: filtered_sessions -- sessions list query"
```

---

## Task 5: `services/sessions.py` — `session_detail`

**Files:**
- Modify: `src/fledermap/services/sessions.py`
- Test: `tests/test_sessions_query.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `OpenProposal` (dataclass: `proposal: SessionMergeProposal`, `counterpart: AnnotationSession`), `SessionDetail` (dataclass: `session: AnnotationSession`, `recordings: Sequence[Recording]`, `open_proposals: Sequence[OpenProposal]`), `session_detail(db_session, session_id: int) -> SessionDetail | None` — consumed by `web/views/sessions.py` (Task 8).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_sessions_query.py`:

```python
from fledermap.services.sessions import session_detail


def test_session_detail_none_when_not_found(engine: Engine) -> None:
    with OrmSession(engine) as session:
        assert session_detail(session, 999) is None


def test_session_detail_lists_recordings_oldest_first(engine: Engine) -> None:
    with OrmSession(engine) as session:
        base = datetime(2026, 8, 20, tzinfo=UTC)
        s = _session("EMT\x1f1", base, base)
        session.add(s)
        session.flush()
        session.add(
            Recording(
                audio_hash="b".rjust(64, "0"), path="b.wav",
                recorded_at=base.replace(hour=23), session_id=s.id,
            ),
        )
        session.add(
            Recording(
                audio_hash="a".rjust(64, "0"), path="a.wav",
                recorded_at=base.replace(hour=20), session_id=s.id,
            ),
        )
        session.commit()

        detail = session_detail(session, s.id)
        assert detail is not None
        assert [r.audio_hash[-1] for r in detail.recordings] == ["a", "b"]


def test_session_detail_no_open_proposals(engine: Engine) -> None:
    with OrmSession(engine) as session:
        base = datetime(2026, 8, 20, tzinfo=UTC)
        s = _session("EMT\x1f1", base, base)
        session.add(s)
        session.commit()

        detail = session_detail(session, s.id)
        assert detail is not None
        assert detail.open_proposals == []


def test_session_detail_shows_open_proposal_from_either_side(engine: Engine) -> None:
    with OrmSession(engine) as session:
        base = datetime(2026, 8, 20, tzinfo=UTC)
        a = _session("EMT\x1f1", base, base)
        b = _session("EMT\x1f1", base.replace(day=21), base.replace(day=21))
        session.add_all([a, b])
        session.flush()
        session.add(
            Recording(
                audio_hash="x".rjust(64, "0"), path="x.wav", recorded_at=base,
                session_id=a.id,
            ),
        )
        session.flush()
        bridging = session.query(Recording).one()
        session.add(
            SessionMergeProposal(
                session_a_id=a.id, session_b_id=b.id,
                bridging_recording_id=bridging.id, detected_at=base,
            ),
        )
        session.commit()

        detail_a = session_detail(session, a.id)
        assert detail_a is not None
        assert len(detail_a.open_proposals) == 1
        assert detail_a.open_proposals[0].counterpart.id == b.id

        detail_b = session_detail(session, b.id)
        assert detail_b is not None
        assert len(detail_b.open_proposals) == 1
        assert detail_b.open_proposals[0].counterpart.id == a.id
```

- [ ] **Step 2: Run to verify failure**

Run: `dangerouslyDisableSandbox=true hatch test tests/test_sessions_query.py -k session_detail -v`
Expected: FAIL (`session_detail` doesn't exist).

- [ ] **Step 3: Implement**

Add to `src/fledermap/services/sessions.py`:

```python
@dataclass(frozen=True)
class OpenProposal:
    """One unresolved `SessionMergeProposal` touching the session being
    viewed, paired with the *other* session it names -- the detail page
    banner never needs to re-derive which side is which."""

    proposal: SessionMergeProposal
    counterpart: AnnotationSession


@dataclass(frozen=True)
class SessionDetail:
    session: AnnotationSession
    recordings: Sequence[Recording]
    open_proposals: Sequence[OpenProposal]


def session_detail(db_session: OrmSession, session_id: int) -> SessionDetail | None:
    """Assemble session detail for `/sessions/{id}`: the session, every
    recording currently assigned to it (oldest first), and every unresolved
    merge proposal naming it from either side (design spec section 5's
    chained-proposal case: a session can appear in more than one)."""
    session_obj = db_session.get(AnnotationSession, session_id)
    if session_obj is None:
        return None

    recordings = db_session.scalars(
        select(Recording)
        .where(Recording.session_id == session_id)
        .order_by(Recording.recorded_at),
    ).all()

    proposals = db_session.scalars(
        select(SessionMergeProposal).where(
            SessionMergeProposal.resolution.is_(None),
            (SessionMergeProposal.session_a_id == session_id)
            | (SessionMergeProposal.session_b_id == session_id),
        ),
    ).all()
    open_proposals = []
    for proposal in proposals:
        counterpart_id = (
            proposal.session_b_id
            if proposal.session_a_id == session_id
            else proposal.session_a_id
        )
        counterpart = db_session.get(AnnotationSession, counterpart_id)
        assert counterpart is not None, "FK guarantees the counterpart session exists"
        open_proposals.append(OpenProposal(proposal=proposal, counterpart=counterpart))

    return SessionDetail(
        session=session_obj, recordings=recordings, open_proposals=open_proposals,
    )
```

- [ ] **Step 4: Run to verify pass**

Run: `dangerouslyDisableSandbox=true hatch test tests/test_sessions_query.py -v`
Expected: PASS (all tests in the file, including Task 4's).

- [ ] **Step 5: Lint and type-check**

Run: `hatch fmt --check && hatch run types:check`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
dangerouslyDisableSandbox=true git add src/fledermap/services/sessions.py tests/test_sessions_query.py
dangerouslyDisableSandbox=true git commit -m "feat: session_detail -- recordings + open merge proposals for one session"
```

---

## Task 6: `services/sessions.py` — `resolve_merge_proposal`

**Files:**
- Create: `src/fledermap/services/sessions.py` (append)
- Test: `tests/test_resolve_merge_proposal.py`

**Interfaces:**
- Consumes: `reclassify_session` (Task 3, `derive/sessions.py`).
- Produces: `ProposalNotFoundError`, `AlreadyResolvedError`, `MergeConflictError` (exceptions), `resolve_merge_proposal(db_session, proposal_id: int, *, action: str, note: str | None, weather: str | None, transect_distance_m: float) -> int` (returns `session_a_id`, always safe to redirect to) — consumed by `web/views/sessions.py` (Task 9).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_resolve_merge_proposal.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import MergeResolution, SessionKind
from fledermap.services.sessions import (
    AlreadyResolvedError,
    MergeConflictError,
    ProposalNotFoundError,
    resolve_merge_proposal,
)
from fledermap.store.models import Recording, Session, SessionMergeProposal

pytestmark = pytest.mark.db

BASE = datetime(2026, 8, 20, tzinfo=UTC)


def _session(started: datetime, ended: datetime, **kwargs: object) -> Session:
    return Session(
        started_at=started, ended_at=ended, kind=SessionKind.STATIONARY,
        detector_key="EMT\x1f1", **kwargs,
    )


def _make_proposal(
    db_session: OrmSession,
    *,
    a_recordings: int = 1,
    b_recordings: int = 1,
) -> tuple[Session, Session, SessionMergeProposal]:
    a = _session(BASE, BASE.replace(hour=21))
    b = _session(BASE.replace(day=21), BASE.replace(day=21, hour=1))
    db_session.add_all([a, b])
    db_session.flush()
    for i in range(a_recordings):
        db_session.add(
            Recording(
                audio_hash=f"a{i}".rjust(64, "0"), path=f"a{i}.wav",
                recorded_at=BASE, session_id=a.id,
            ),
        )
    for i in range(b_recordings):
        db_session.add(
            Recording(
                audio_hash=f"b{i}".rjust(64, "0"), path=f"b{i}.wav",
                recorded_at=BASE.replace(day=21), session_id=b.id,
            ),
        )
    db_session.flush()
    bridging = db_session.query(Recording).filter_by(session_id=a.id).first()
    assert bridging is not None
    proposal = SessionMergeProposal(
        session_a_id=a.id, session_b_id=b.id,
        bridging_recording_id=bridging.id, detected_at=BASE,
    )
    db_session.add(proposal)
    db_session.commit()
    return a, b, proposal


def test_reject_only_sets_resolution(engine: Engine) -> None:
    with OrmSession(engine) as session:
        a, b, proposal = _make_proposal(session)

        result = resolve_merge_proposal(
            session, proposal.id, action="reject", note=None, weather=None,
            transect_distance_m=150.0,
        )
        session.commit()

        assert result == a.id
        refreshed = session.get(SessionMergeProposal, proposal.id)
        assert refreshed is not None
        assert refreshed.resolution == MergeResolution.REJECTED
        assert refreshed.resolved_at is not None
        assert session.get(Session, b.id) is not None  # untouched


def test_merge_reassigns_recordings_and_deletes_session_b(engine: Engine) -> None:
    with OrmSession(engine) as session:
        a, b, proposal = _make_proposal(session, a_recordings=1, b_recordings=2)

        resolve_merge_proposal(
            session, proposal.id, action="merge", note="combined note",
            weather="combined weather", transect_distance_m=150.0,
        )
        session.commit()

        assert session.get(Session, b.id) is None
        remaining = session.scalars(select(Recording)).all()
        assert len(remaining) == 3
        assert all(r.session_id == a.id for r in remaining)
        merged_a = session.get(Session, a.id)
        assert merged_a is not None
        assert merged_a.note == "combined note"
        assert merged_a.weather == "combined weather"
        assert merged_a.started_at == BASE
        assert merged_a.ended_at == BASE.replace(day=21, hour=1)

        refreshed = session.get(SessionMergeProposal, proposal.id)
        assert refreshed is not None
        assert refreshed.resolution == MergeResolution.MERGED
        assert refreshed.resolved_at is not None


def test_merge_reclassifies_session_a_when_unlocked(engine: Engine) -> None:
    with OrmSession(engine) as session:
        a = _session(BASE, BASE)
        b = _session(BASE.replace(day=21), BASE.replace(day=21))
        session.add_all([a, b])
        session.flush()
        session.add(
            Recording(
                audio_hash="a".rjust(64, "0"), path="a.wav", recorded_at=BASE,
                session_id=a.id, geom=None,
            ),
        )
        session.add(
            Recording(
                audio_hash="b".rjust(64, "0"), path="b.wav",
                recorded_at=BASE.replace(day=21), session_id=b.id,
                geom=None,
            ),
        )
        session.flush()
        bridging = session.query(Recording).filter_by(session_id=a.id).one()
        proposal = SessionMergeProposal(
            session_a_id=a.id, session_b_id=b.id,
            bridging_recording_id=bridging.id, detected_at=BASE,
        )
        session.add(proposal)
        session.commit()

        resolve_merge_proposal(
            session, proposal.id, action="merge", note=None, weather=None,
            transect_distance_m=150.0,
        )
        session.commit()

        # no GPS on either side's recordings -- reclassification runs but
        # has nothing to work with, stays STATIONARY. Proves the call
        # happened without erroring, not a positive TRANSECT case (that's
        # covered directly in test_partition_sessions.py).
        merged_a = session.get(Session, a.id)
        assert merged_a is not None
        assert merged_a.kind == SessionKind.STATIONARY


def test_merge_does_not_reclassify_a_locked_session_a(engine: Engine) -> None:
    with OrmSession(engine) as session:
        a = _session(BASE, BASE, kind_locked=True)
        b = _session(BASE.replace(day=21), BASE.replace(day=21))
        session.add_all([a, b])
        session.flush()
        session.add(
            Recording(
                audio_hash="a".rjust(64, "0"), path="a.wav", recorded_at=BASE,
                session_id=a.id,
            ),
        )
        session.flush()
        bridging = session.query(Recording).filter_by(session_id=a.id).one()
        proposal = SessionMergeProposal(
            session_a_id=a.id, session_b_id=b.id,
            bridging_recording_id=bridging.id, detected_at=BASE,
        )
        session.add(proposal)
        session.commit()

        resolve_merge_proposal(
            session, proposal.id, action="merge", note=None, weather=None,
            transect_distance_m=150.0,
        )
        session.commit()

        merged_a = session.get(Session, a.id)
        assert merged_a is not None
        assert merged_a.kind == SessionKind.STATIONARY  # unchanged, still locked
        assert merged_a.kind_locked is True


def test_unknown_proposal_id_raises(engine: Engine) -> None:
    with OrmSession(engine) as session:
        with pytest.raises(ProposalNotFoundError):
            resolve_merge_proposal(
                session, 999, action="reject", note=None, weather=None,
                transect_distance_m=150.0,
            )


def test_already_resolved_raises_without_reapplying(engine: Engine) -> None:
    with OrmSession(engine) as session:
        a, b, proposal = _make_proposal(session)
        proposal.resolution = MergeResolution.REJECTED
        proposal.resolved_at = BASE
        session.commit()

        with pytest.raises(AlreadyResolvedError):
            resolve_merge_proposal(
                session, proposal.id, action="merge", note=None, weather=None,
                transect_distance_m=150.0,
            )
        session.commit()

        assert session.get(Session, b.id) is not None  # not merged after all


def test_invalid_action_raises_value_error(engine: Engine) -> None:
    with OrmSession(engine) as session:
        _a, _b, proposal = _make_proposal(session)
        with pytest.raises(ValueError, match="bogus"):
            resolve_merge_proposal(
                session, proposal.id, action="bogus", note=None, weather=None,
                transect_distance_m=150.0,
            )


def test_chained_proposal_raises_merge_conflict(engine: Engine) -> None:
    """Design spec section 5: session_b is also session_a of a second, still
    -open proposal. The FK on session_merge_proposal has no ON DELETE clause,
    so deleting session_b must be caught and turned into a clean error, not
    a raw IntegrityError."""
    with OrmSession(engine) as session:
        a, b, first_proposal = _make_proposal(session)
        c = _session(BASE.replace(day=22), BASE.replace(day=22))
        session.add(c)
        session.flush()
        session.add(
            Recording(
                audio_hash="c".rjust(64, "0"), path="c.wav",
                recorded_at=BASE.replace(day=22), session_id=c.id,
            ),
        )
        session.flush()
        bridging_bc = session.query(Recording).filter_by(session_id=c.id).one()
        second_proposal = SessionMergeProposal(
            session_a_id=b.id, session_b_id=c.id,
            bridging_recording_id=bridging_bc.id, detected_at=BASE,
        )
        session.add(second_proposal)
        session.commit()

        with pytest.raises(MergeConflictError):
            resolve_merge_proposal(
                session, first_proposal.id, action="merge", note=None,
                weather=None, transect_distance_m=150.0,
            )
        session.commit()

        # nothing partially applied
        refreshed = session.get(SessionMergeProposal, first_proposal.id)
        assert refreshed is not None
        assert refreshed.resolution is None
        assert session.get(Session, b.id) is not None
```

- [ ] **Step 2: Run to verify failure**

Run: `dangerouslyDisableSandbox=true hatch test tests/test_resolve_merge_proposal.py -v`
Expected: FAIL (`resolve_merge_proposal` and the exception classes don't exist).

- [ ] **Step 3: Implement**

Add to `src/fledermap/services/sessions.py` (new imports: `from datetime import UTC, datetime` already partially imported -- add `UTC`; `from sqlalchemy import update`; `from sqlalchemy.exc import IntegrityError`; `from fledermap.derive.sessions import reclassify_session`; `from fledermap.domain.codes import MergeResolution`):

```python
class ProposalNotFoundError(Exception):
    """No `SessionMergeProposal` exists with the given id."""


class AlreadyResolvedError(Exception):
    """The proposal was already resolved (merged or rejected), by this
    request or a concurrent one -- design spec section 10's "already
    resolved by someone else" case. Checked before any mutation, so this
    never partially re-applies a merge."""


class MergeConflictError(Exception):
    """`session_b` is still referenced by another open merge proposal, so it
    can't be deleted until that one is resolved first (design spec section
    5's chained-proposal edge case) -- `session_merge_proposal`'s FK columns
    have no `ON DELETE`, so Postgres raises `IntegrityError` rather than
    silently orphaning the other proposal; this turns that into a clean,
    named error instead of a raw 500."""


def resolve_merge_proposal(
    db_session: OrmSession,
    proposal_id: int,
    *,
    action: str,
    note: str | None,
    weather: str | None,
    transect_distance_m: float,
) -> int:
    """Accept ("merge") or reject a `SessionMergeProposal`. Returns
    `session_a_id` -- always safe to redirect to afterward, since
    `session_a` is never deleted (only `session_b` is, and only on a
    successful merge). Does not commit; the caller controls the transaction
    boundary, matching every other `services/` function in this project."""
    if action not in ("merge", "reject"):
        msg = f"{action!r} is not a valid action (expected 'merge' or 'reject')."
        raise ValueError(msg)

    proposal = db_session.get(SessionMergeProposal, proposal_id)
    if proposal is None:
        raise ProposalNotFoundError(proposal_id)
    if proposal.resolution is not None:
        msg = "This merge proposal was already resolved."
        raise AlreadyResolvedError(msg)

    if action == "reject":
        proposal.resolution = MergeResolution.REJECTED
        proposal.resolved_at = datetime.now(UTC)
        return proposal.session_a_id

    session_a = db_session.get(AnnotationSession, proposal.session_a_id)
    session_b = db_session.get(AnnotationSession, proposal.session_b_id)
    assert session_a is not None, "FK guarantees this row exists"
    assert session_b is not None, "FK guarantees this row exists"

    db_session.execute(
        update(Recording)
        .where(Recording.session_id == session_b.id)
        .values(session_id=session_a.id),
    )
    session_a.started_at = min(session_a.started_at, session_b.started_at)
    session_a.ended_at = max(session_a.ended_at, session_b.ended_at)
    if note is not None:
        session_a.note = note
    if weather is not None:
        session_a.weather = weather
    reclassify_session(db_session, session_a, transect_distance_m=transect_distance_m)

    db_session.delete(session_b)
    try:
        db_session.flush()
    except IntegrityError as exc:
        db_session.rollback()
        msg = (
            "Can't complete this merge: the other session is still part of "
            "another pending merge proposal. Resolve that one first."
        )
        raise MergeConflictError(msg) from exc

    proposal.resolution = MergeResolution.MERGED
    proposal.resolved_at = datetime.now(UTC)
    return session_a.id
```

- [ ] **Step 4: Run to verify pass**

Run: `dangerouslyDisableSandbox=true hatch test tests/test_resolve_merge_proposal.py -v`
Expected: PASS.

- [ ] **Step 5: Full suite, lint, type-check**

Run: `dangerouslyDisableSandbox=true hatch test && hatch fmt --check && hatch run types:check`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
dangerouslyDisableSandbox=true git add src/fledermap/services/sessions.py tests/test_resolve_merge_proposal.py
dangerouslyDisableSandbox=true git commit -m "feat: resolve_merge_proposal -- accept/reject with real session merge"
```

---

## Task 7: `GET /sessions` route + `sessions_list.html`

**Files:**
- Create: `src/fledermap/web/views/sessions.py`
- Create: `src/fledermap/web/templates/sessions_list.html`
- Modify: `src/fledermap/web/app.py` (register the new blueprint)
- Test: `tests/test_sessions_view.py`

**Interfaces:**
- Consumes: `filtered_sessions`, `open_proposal_session_ids` (Task 4); `fledermap.web.params.parse_datetime` (existing).
- Produces: `sessions_bp` (Flask blueprint) — extended by Tasks 8/9.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_sessions_view.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import SessionKind
from fledermap.store.models import Session as AnnotationSession
from fledermap.web.app import create_app

pytestmark = pytest.mark.db


def test_sessions_list_renders_a_session_row(engine: Engine, tmp_path: Path) -> None:
    with OrmSession(engine) as session:
        session.add(
            AnnotationSession(
                started_at=datetime(2026, 8, 21, 21, tzinfo=UTC),
                ended_at=datetime(2026, 8, 21, 23, tzinfo=UTC),
                kind=SessionKind.STATIONARY,
                detector_key="EMT\x1f1",
            ),
        )
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.get("/sessions")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "EMT" in html
    assert "stationary" in html


def test_sessions_list_filters_by_detector(engine: Engine, tmp_path: Path) -> None:
    with OrmSession(engine) as session:
        session.add(
            AnnotationSession(
                started_at=datetime(2026, 8, 21, tzinfo=UTC),
                ended_at=datetime(2026, 8, 21, tzinfo=UTC),
                kind=SessionKind.STATIONARY, detector_key="EMT\x1f1",
            ),
        )
        session.add(
            AnnotationSession(
                started_at=datetime(2026, 8, 21, tzinfo=UTC),
                ended_at=datetime(2026, 8, 21, tzinfo=UTC),
                kind=SessionKind.STATIONARY, detector_key="Kaleidoscope\x1f2",
            ),
        )
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.get("/sessions?detector=EMT")

    html = response.get_data(as_text=True)
    assert "EMT" in html
    assert "Kaleidoscope" not in html


def test_sessions_list_empty_state(engine: Engine, tmp_path: Path) -> None:
    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.get("/sessions")

    assert response.status_code == 200
    assert "No sessions match" in response.get_data(as_text=True)


def test_sessions_list_bad_date_returns_400(engine: Engine, tmp_path: Path) -> None:
    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.get("/sessions?from=not-a-date")

    assert response.status_code == 400
```

- [ ] **Step 2: Run to verify failure**

Run: `dangerouslyDisableSandbox=true hatch test tests/test_sessions_view.py -v`
Expected: FAIL (`fledermap.web.views.sessions` doesn't exist; `/sessions` 404s).

- [ ] **Step 3: Implement the view**

Create `src/fledermap/web/views/sessions.py`:

```python
"""Sessions list + detail pages (design spec
2026-08-27-fledermap-phase5b-sessions-design.md) -- full standalone pages,
not HTMX drawer fragments, matching the parent spec treating `/sessions` as
a first-class view distinct from the map's drawer."""

from __future__ import annotations

import flask
from sqlalchemy.orm import Session as OrmSession

from fledermap.services.sessions import filtered_sessions, open_proposal_session_ids
from fledermap.web.params import parse_datetime

sessions_bp = flask.Blueprint(
    "sessions", __name__, template_folder="../templates",
)


@sessions_bp.get("/sessions")
def sessions_list_page() -> flask.Response:
    detector = flask.request.args.get("detector") or None
    from_raw = flask.request.args.get("from", "")
    to_raw = flask.request.args.get("to", "")
    try:
        date_from = parse_datetime(from_raw)
        date_to = parse_datetime(to_raw, end_of_day=True)
    except ValueError as exc:
        return flask.make_response((str(exc), 400))
    open_only = flask.request.args.get("open_proposals") == "1"

    engine = flask.current_app.config["ENGINE"]
    with OrmSession(engine) as session:
        rows = filtered_sessions(
            session, detector=detector, date_from=date_from, date_to=date_to,
            open_proposals_only=open_only,
        )
        open_ids = open_proposal_session_ids(session)
        html = flask.render_template(
            "sessions_list.html",
            rows=rows,
            open_ids=open_ids,
            detector=detector or "",
            date_from=from_raw,
            date_to=to_raw,
            open_only=open_only,
        )
    return flask.make_response(html)
```

- [ ] **Step 4: Register the blueprint**

In `src/fledermap/web/app.py`, add the import and registration:

```python
from fledermap.web.views.sessions import sessions_bp
```
```python
    app.register_blueprint(sessions_bp)
```
(next to the existing `app.register_blueprint(views_bp)` line)

- [ ] **Step 5: Write the template**

Create `src/fledermap/web/templates/sessions_list.html`:

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Fledermap — Sessions</title>
  <link rel="stylesheet" href="{{ url_for('static', filename='app.css') }}">
</head>
<body>
  {% include "_nav.html" %}
  <main class="main-content">
    <h1>Sessions</h1>
    <form id="session-filters" method="get">
      <label>Detector <input type="text" name="detector" value="{{ detector }}"></label>
      <label>From <input type="date" name="from" value="{{ date_from }}"></label>
      <label>To <input type="date" name="to" value="{{ date_to }}"></label>
      <label>
        <input type="checkbox" name="open_proposals" value="1" {% if open_only %}checked{% endif %}>
        Open merge proposals only
      </label>
      <button type="submit">Filter</button>
    </form>
    <table id="sessions-table">
      <thead>
        <tr>
          <th>Date range</th><th>Detector</th><th>Kind</th><th>Recordings</th><th></th>
        </tr>
      </thead>
      <tbody>
        {% for row in rows %}
        <tr>
          <td>
            <a href="/sessions/{{ row.session.id }}">
              {{ row.session.started_at.strftime('%Y-%m-%d %H:%M') }}–{{ row.session.ended_at.strftime('%H:%M') }}
            </a>
          </td>
          <td>{{ row.session.detector_key or 'unknown detector' }}</td>
          <td>{{ row.session.kind.value }}</td>
          <td>{{ row.recording_count }}</td>
          <td>{% if row.session.id in open_ids %}<span class="merge-badge">⚠ merge proposal</span>{% endif %}</td>
        </tr>
        {% else %}
        <tr><td colspan="5">No sessions match these filters.</td></tr>
        {% endfor %}
      </tbody>
    </table>
  </main>
  <script src="{{ url_for('vendor.static', filename='alpine.min.js') }}" defer></script>
</body>
</html>
```

(`_nav.html` doesn't exist until Task 11 -- this template will 500 on `{% include %}` until then. That's expected and resolved within this same plan; Task 7's own tests below don't yet exercise a page that needs the sidebar to render correctly, but do exercise the full page render, so **Task 11 must land before this task's tests are considered done for real** -- for now, create a temporary placeholder `_nav.html` in this task with just `<nav id="sidebar"></nav>` so the include resolves and this task's tests pass in isolation; Task 11 replaces its contents wholesale.)

- [ ] **Step 5b: Temporary `_nav.html` placeholder**

Create `src/fledermap/web/templates/_nav.html`:

```html
{# src/fledermap/web/templates/_nav.html -- placeholder, replaced by Task 11 #}
<nav id="sidebar"></nav>
```

- [ ] **Step 6: Run to verify pass**

Run: `dangerouslyDisableSandbox=true hatch test tests/test_sessions_view.py -v`
Expected: PASS.

- [ ] **Step 7: Full suite, lint, type-check**

Run: `dangerouslyDisableSandbox=true hatch test && hatch fmt --check && hatch run types:check`
Expected: clean.

- [ ] **Step 8: Commit**

```bash
dangerouslyDisableSandbox=true git add src/fledermap/web/views/sessions.py src/fledermap/web/templates/sessions_list.html src/fledermap/web/templates/_nav.html src/fledermap/web/app.py tests/test_sessions_view.py
dangerouslyDisableSandbox=true git commit -m "feat: GET /sessions -- sessions list page"
```

---

## Task 8: `GET`/`POST /sessions/{id}` + `session_detail.html`

**Files:**
- Modify: `src/fledermap/web/views/sessions.py`
- Create: `src/fledermap/web/templates/session_detail.html`
- Test: `tests/test_sessions_view.py`

**Interfaces:**
- Consumes: `session_detail` (Task 5); `fledermap.services.current_best.current_best_identification` (existing); `fledermap.domain.codes.SessionKind` (existing).
- Produces: nothing new consumed elsewhere (this is a leaf page), but the `session-mini-map` div id and `data-session-id` attribute below are consumed by Task 10's `session_map.js`, and the merge-banner form's `action`/`note`/`weather` field names are consumed by Task 9's resolve route.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_sessions_view.py`:

```python
from fledermap.store.models import Recording


def test_session_detail_not_found_returns_404(engine: Engine, tmp_path: Path) -> None:
    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.get("/sessions/999")

    assert response.status_code == 404


def test_session_detail_shows_edit_form_with_current_values(
    engine: Engine, tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        s = AnnotationSession(
            started_at=datetime(2026, 8, 21, 21, tzinfo=UTC),
            ended_at=datetime(2026, 8, 21, 23, tzinfo=UTC),
            kind=SessionKind.TRANSECT, detector_key="EMT\x1f1",
            note="existing note", weather="rainy",
        )
        session.add(s)
        session.commit()
        session_id = s.id

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.get(f"/sessions/{session_id}")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "existing note" in html
    assert "rainy" in html
    assert 'value="transect" selected' in html
    assert "effort" not in html.lower()  # P5b-10: field is gone


def test_session_detail_lists_recordings(engine: Engine, tmp_path: Path) -> None:
    with OrmSession(engine) as session:
        s = AnnotationSession(
            started_at=datetime(2026, 8, 21, tzinfo=UTC),
            ended_at=datetime(2026, 8, 21, tzinfo=UTC),
            kind=SessionKind.STATIONARY, detector_key="EMT\x1f1",
        )
        session.add(s)
        session.flush()
        session.add(
            Recording(
                audio_hash="a".rjust(64, "0"), path="a.wav",
                recorded_at=datetime(2026, 8, 21, 21, tzinfo=UTC), session_id=s.id,
            ),
        )
        session.commit()
        session_id = s.id

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.get(f"/sessions/{session_id}")

    html = response.get_data(as_text=True)
    assert "Recordings in this session (1)" in html
    assert "unidentified" in html  # no Identification rows -- current_best is None


def test_session_detail_no_merge_banner_when_no_open_proposal(
    engine: Engine, tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        s = AnnotationSession(
            started_at=datetime(2026, 8, 21, tzinfo=UTC),
            ended_at=datetime(2026, 8, 21, tzinfo=UTC),
            kind=SessionKind.STATIONARY, detector_key="EMT\x1f1",
        )
        session.add(s)
        session.commit()
        session_id = s.id

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.get(f"/sessions/{session_id}")

    assert "merge-banner" not in response.get_data(as_text=True)


def test_save_session_updates_kind_note_weather_and_locks(
    engine: Engine, tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        s = AnnotationSession(
            started_at=datetime(2026, 8, 21, tzinfo=UTC),
            ended_at=datetime(2026, 8, 21, tzinfo=UTC),
            kind=SessionKind.STATIONARY, detector_key="EMT\x1f1",
        )
        session.add(s)
        session.commit()
        session_id = s.id

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.post(
        f"/sessions/{session_id}",
        data={"kind": "transect", "note": "new note", "weather": "clear"},
    )

    assert response.status_code == 302
    assert response.location == f"/sessions/{session_id}"
    with OrmSession(engine) as session:
        refreshed = session.get(AnnotationSession, session_id)
        assert refreshed is not None
        assert refreshed.kind == SessionKind.TRANSECT
        assert refreshed.note == "new note"
        assert refreshed.weather == "clear"
        assert refreshed.kind_locked is True


def test_save_session_invalid_kind_returns_400(engine: Engine, tmp_path: Path) -> None:
    with OrmSession(engine) as session:
        s = AnnotationSession(
            started_at=datetime(2026, 8, 21, tzinfo=UTC),
            ended_at=datetime(2026, 8, 21, tzinfo=UTC),
            kind=SessionKind.STATIONARY, detector_key="EMT\x1f1",
        )
        session.add(s)
        session.commit()
        session_id = s.id

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.post(f"/sessions/{session_id}", data={"kind": "bogus"})

    assert response.status_code == 400


def test_save_session_not_found_returns_404(engine: Engine, tmp_path: Path) -> None:
    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.post(
        "/sessions/999", data={"kind": "stationary", "note": "", "weather": ""},
    )

    assert response.status_code == 404
```

- [ ] **Step 2: Run to verify failure**

Run: `dangerouslyDisableSandbox=true hatch test tests/test_sessions_view.py -v`
Expected: FAIL (routes don't exist).

- [ ] **Step 3: Implement the routes**

Add to `src/fledermap/web/views/sessions.py` (new imports: `from fledermap.domain.codes import SessionKind`; `from fledermap.services.current_best import current_best_identification`; `from fledermap.services.sessions import session_detail` alongside the existing `filtered_sessions`/`open_proposal_session_ids` import; `from fledermap.store.models import Session as AnnotationSession`; `from fledermap.store.models import Taxon`):

```python
@sessions_bp.get("/sessions/<int:session_id>")
def session_detail_page(session_id: int) -> flask.Response:
    engine = flask.current_app.config["ENGINE"]
    with OrmSession(engine) as session:
        detail = session_detail(session, session_id)
        if detail is None:
            return flask.make_response(("Session not found.", 404))

        recordings_with_id = []
        for recording in detail.recordings:
            best = current_best_identification(recording)
            taxon = None
            if best is not None and best.taxon_id is not None:
                taxon = session.get(Taxon, best.taxon_id)
            recordings_with_id.append((recording, best, taxon))

        html = flask.render_template(
            "session_detail.html",
            detail=detail,
            recordings_with_id=recordings_with_id,
        )
    return flask.make_response(html)


@sessions_bp.post("/sessions/<int:session_id>")
def save_session(session_id: int) -> flask.Response:
    kind_raw = flask.request.form.get("kind", "")
    try:
        kind = SessionKind(kind_raw)
    except ValueError:
        return flask.make_response((f"Invalid kind: {kind_raw!r}", 400))
    note = flask.request.form.get("note") or None
    weather = flask.request.form.get("weather") or None

    engine = flask.current_app.config["ENGINE"]
    with OrmSession(engine) as session:
        session_obj = session.get(AnnotationSession, session_id)
        if session_obj is None:
            return flask.make_response(("Session not found.", 404))
        session_obj.kind = kind
        session_obj.note = note
        session_obj.weather = weather
        session_obj.kind_locked = True
        session.commit()

    return flask.redirect(f"/sessions/{session_id}")
```

- [ ] **Step 4: Write the template**

Create `src/fledermap/web/templates/session_detail.html`:

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Fledermap — Session {{ detail.session.id }}</title>
  <link rel="stylesheet" href="{{ url_for('vendor.static', filename='leaflet.css') }}">
  <link rel="stylesheet" href="{{ url_for('static', filename='app.css') }}">
</head>
<body>
  {% include "_nav.html" %}
  <main class="main-content">
    <a href="/sessions">← Back to sessions</a>
    <h1>
      Session: {{ detail.session.started_at.strftime('%Y-%m-%d %H:%M') }} – {{ detail.session.ended_at.strftime('%H:%M') }}
    </h1>
    <p>Detector: {{ detail.session.detector_key or 'unknown detector' }}</p>

    <div class="session-detail-columns">
      <div class="col session-map-col">
        <div id="session-mini-map" data-session-id="{{ detail.session.id }}"></div>
        <a href="/?session={{ detail.session.id }}">View on full map</a>
      </div>
      <div class="col session-edit-col">
        <form method="post" action="/sessions/{{ detail.session.id }}">
          <label>Kind
            <select name="kind">
              <option value="stationary" {% if detail.session.kind.value == 'stationary' %}selected{% endif %}>Stationary</option>
              <option value="transect" {% if detail.session.kind.value == 'transect' %}selected{% endif %}>Transect</option>
            </select>
          </label>
          <label>Note
            <textarea name="note">{{ detail.session.note or '' }}</textarea>
          </label>
          <label>Weather
            <textarea name="weather">{{ detail.session.weather or '' }}</textarea>
          </label>
          <button type="submit">Save</button>
        </form>
      </div>
    </div>

    {% for op in detail.open_proposals %}
    <div class="merge-banner">
      <p>
        ⚠ This session may merge with session
        <a href="/sessions/{{ op.counterpart.id }}">#{{ op.counterpart.id }}</a>
        ({{ op.counterpart.started_at.strftime('%Y-%m-%d %H:%M') }}–{{ op.counterpart.ended_at.strftime('%H:%M') }}).
      </p>
      <form method="post" action="/sessions/merge-proposals/{{ op.proposal.id }}/resolve">
        <label>Combined note
          <textarea name="note">{{ detail.session.note or '' }}
---
{{ op.counterpart.note or '' }}</textarea>
        </label>
        <label>Combined weather
          <textarea name="weather">{{ detail.session.weather or '' }}
---
{{ op.counterpart.weather or '' }}</textarea>
        </label>
        <button type="submit" name="action" value="merge">Accept merge</button>
        <button type="submit" name="action" value="reject">Reject</button>
      </form>
    </div>
    {% endfor %}

    <h2>Recordings in this session ({{ recordings_with_id|length }})</h2>
    <ul id="session-recordings">
      {% for recording, best, taxon in recordings_with_id %}
      <li>
        {{ recording.recorded_at.strftime('%Y-%m-%d %H:%M') }} —
        {{ taxon.scientific_name if taxon else (best.verdict.value if best else "unidentified") }}
      </li>
      {% endfor %}
    </ul>
  </main>

  <script src="{{ url_for('vendor.static', filename='leaflet.js') }}"></script>
  <script src="{{ url_for('vendor.static', filename='alpine.min.js') }}" defer></script>
  <script src="{{ url_for('static', filename='session_map.js') }}"></script>
</body>
</html>
```

(`session_map.js` doesn't exist until Task 10 -- a 404 on that one script tag doesn't break page rendering or this task's tests, which don't exercise the mini-map's JS behavior.)

- [ ] **Step 5: Run to verify pass**

Run: `dangerouslyDisableSandbox=true hatch test tests/test_sessions_view.py -v`
Expected: PASS.

- [ ] **Step 6: Full suite, lint, type-check**

Run: `dangerouslyDisableSandbox=true hatch test && hatch fmt --check && hatch run types:check`
Expected: clean.

- [ ] **Step 7: Commit**

```bash
dangerouslyDisableSandbox=true git add src/fledermap/web/views/sessions.py src/fledermap/web/templates/session_detail.html tests/test_sessions_view.py
dangerouslyDisableSandbox=true git commit -m "feat: GET/POST /sessions/{id} -- session detail and edit form"
```

---

## Task 9: `POST /sessions/merge-proposals/{id}/resolve`

**Files:**
- Modify: `src/fledermap/web/views/sessions.py`
- Modify: `src/fledermap/web/app.py` (`create_app` gains `transect_distance_m`)
- Modify: `src/fledermap/cli/main.py` (`serve` command's `create_app` call)
- Test: `tests/test_sessions_view.py`, `tests/test_web_app.py`

**Interfaces:**
- Consumes: `resolve_merge_proposal`, `ProposalNotFoundError`, `AlreadyResolvedError`, `MergeConflictError` (Task 6).
- Produces: `app.config["TRANSECT_DISTANCE_M"]` — this task's own route is the only current consumer.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_web_app.py`:

```python
def test_create_app_stores_transect_distance_on_config(
    tmp_path: Path, engine: Engine,
) -> None:
    app = create_app(
        engine, tmp_path / "static", tmp_path / "media", transect_distance_m=200.0,
    )

    assert app.config["TRANSECT_DISTANCE_M"] == 200.0


def test_create_app_transect_distance_defaults_to_150(
    tmp_path: Path, engine: Engine,
) -> None:
    app = create_app(engine, tmp_path / "static", tmp_path / "media")

    assert app.config["TRANSECT_DISTANCE_M"] == 150.0
```

Add to `tests/test_sessions_view.py` (new imports: `from fledermap.domain.codes import MergeResolution`; `from fledermap.store.models import SessionMergeProposal`):

```python
def _make_open_proposal(
    session: OrmSession,
) -> tuple[AnnotationSession, AnnotationSession, SessionMergeProposal]:
    a = AnnotationSession(
        started_at=datetime(2026, 8, 21, tzinfo=UTC),
        ended_at=datetime(2026, 8, 21, 21, tzinfo=UTC),
        kind=SessionKind.STATIONARY, detector_key="EMT\x1f1",
    )
    b = AnnotationSession(
        started_at=datetime(2026, 8, 22, tzinfo=UTC),
        ended_at=datetime(2026, 8, 22, tzinfo=UTC),
        kind=SessionKind.STATIONARY, detector_key="EMT\x1f1",
    )
    session.add_all([a, b])
    session.flush()
    session.add(
        Recording(
            audio_hash="a".rjust(64, "0"), path="a.wav",
            recorded_at=datetime(2026, 8, 21, tzinfo=UTC), session_id=a.id,
        ),
    )
    session.flush()
    bridging = session.query(Recording).filter_by(session_id=a.id).one()
    proposal = SessionMergeProposal(
        session_a_id=a.id, session_b_id=b.id,
        bridging_recording_id=bridging.id,
        detected_at=datetime(2026, 8, 21, tzinfo=UTC),
    )
    session.add(proposal)
    session.commit()
    return a, b, proposal


def test_resolve_proposal_merge_redirects_to_session_a(
    engine: Engine, tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        a, _b, proposal = _make_open_proposal(session)
        a_id, proposal_id = a.id, proposal.id

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.post(
        f"/sessions/merge-proposals/{proposal_id}/resolve",
        data={"action": "merge", "note": "combined", "weather": "combined"},
    )

    assert response.status_code == 302
    assert response.location == f"/sessions/{a_id}"
    with OrmSession(engine) as session:
        refreshed = session.get(SessionMergeProposal, proposal_id)
        assert refreshed is not None
        assert refreshed.resolution == MergeResolution.MERGED


def test_resolve_proposal_reject_redirects_to_session_a(
    engine: Engine, tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        a, _b, proposal = _make_open_proposal(session)
        a_id, proposal_id = a.id, proposal.id

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.post(
        f"/sessions/merge-proposals/{proposal_id}/resolve",
        data={"action": "reject"},
    )

    assert response.status_code == 302
    assert response.location == f"/sessions/{a_id}"


def test_resolve_proposal_not_found_returns_404(engine: Engine, tmp_path: Path) -> None:
    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.post(
        "/sessions/merge-proposals/999/resolve", data={"action": "reject"},
    )

    assert response.status_code == 404


def test_resolve_proposal_already_resolved_returns_409(
    engine: Engine, tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        _a, _b, proposal = _make_open_proposal(session)
        proposal.resolution = MergeResolution.REJECTED
        proposal.resolved_at = datetime(2026, 8, 21, tzinfo=UTC)
        session.commit()
        proposal_id = proposal.id

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.post(
        f"/sessions/merge-proposals/{proposal_id}/resolve",
        data={"action": "merge"},
    )

    assert response.status_code == 409


def test_resolve_proposal_invalid_action_returns_400(
    engine: Engine, tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        _a, _b, proposal = _make_open_proposal(session)
        proposal_id = proposal.id

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.post(
        f"/sessions/merge-proposals/{proposal_id}/resolve",
        data={"action": "bogus"},
    )

    assert response.status_code == 400
```

- [ ] **Step 2: Run to verify failure**

Run: `dangerouslyDisableSandbox=true hatch test tests/test_sessions_view.py tests/test_web_app.py -v`
Expected: FAIL (route and `TRANSECT_DISTANCE_M` config don't exist).

- [ ] **Step 3: Thread `transect_distance_m` through `create_app`**

In `src/fledermap/web/app.py`:

```python
def create_app(
    engine: Engine,
    static_root: Path,
    media_root: Path,
    transect_distance_m: float = 150.0,
) -> flask.Flask:
```

(Keyword-with-default rather than a new required positional -- every existing test file (`test_geojson_api.py`, `test_map_view.py`, `test_media_view.py`, `test_web_app.py`) already calls `create_app` positionally without it; a required 4th argument would break all of them for a value only the new merge-resolve route consumes. `150.0` matches `Config.transect_distance_m`'s own default (Task 2), so an un-configured `serve` still classifies sensibly.)

Add to the docstring, and store it on `app.config`:

```python
    app.config["ENGINE"] = engine
    app.config["MEDIA_ROOT"] = media_root
    app.config["TRANSECT_DISTANCE_M"] = transect_distance_m
```

- [ ] **Step 4: Update the `serve` command**

In `src/fledermap/cli/main.py`, the `serve` command's `create_app` call:

```python
    app = create_app(
        engine, config.static_root, config.media_root,
        transect_distance_m=config.transect_distance_m,
    )
```

- [ ] **Step 5: Implement the route**

Add to `src/fledermap/web/views/sessions.py` (new import: `from fledermap.services.sessions import (AlreadyResolvedError, MergeConflictError, ProposalNotFoundError, resolve_merge_proposal)` alongside the existing service imports):

```python
@sessions_bp.post("/sessions/merge-proposals/<int:proposal_id>/resolve")
def resolve_proposal(proposal_id: int) -> flask.Response:
    action = flask.request.form.get("action", "")
    note = flask.request.form.get("note") or None
    weather = flask.request.form.get("weather") or None

    engine = flask.current_app.config["ENGINE"]
    transect_distance_m = flask.current_app.config["TRANSECT_DISTANCE_M"]
    with OrmSession(engine) as session:
        try:
            surviving_id = resolve_merge_proposal(
                session, proposal_id, action=action, note=note, weather=weather,
                transect_distance_m=transect_distance_m,
            )
        except ProposalNotFoundError:
            return flask.make_response(("Merge proposal not found.", 404))
        except AlreadyResolvedError as exc:
            return flask.make_response((str(exc), 409))
        except MergeConflictError as exc:
            return flask.make_response((str(exc), 409))
        except ValueError as exc:
            return flask.make_response((str(exc), 400))
        session.commit()

    return flask.redirect(f"/sessions/{surviving_id}")
```

- [ ] **Step 6: Run to verify pass**

Run: `dangerouslyDisableSandbox=true hatch test tests/test_sessions_view.py tests/test_web_app.py -v`
Expected: PASS.

- [ ] **Step 7: Full suite, lint, type-check**

Run: `dangerouslyDisableSandbox=true hatch test && hatch fmt --check && hatch run types:check`
Expected: clean (confirms `cli/main.py`'s `serve` change didn't break `tests/test_cli.py`).

- [ ] **Step 8: Commit**

```bash
dangerouslyDisableSandbox=true git add src/fledermap/web/views/sessions.py src/fledermap/web/app.py src/fledermap/cli/main.py tests/test_sessions_view.py tests/test_web_app.py
dangerouslyDisableSandbox=true git commit -m "feat: POST /sessions/merge-proposals/{id}/resolve"
```

---

## Task 10: Session mini-map

**Files:**
- Create: `src/fledermap/web/static/session_map.js`
- Modify: `src/fledermap/web/static/app.css`
- Test: manual (design spec section 11: no JS test framework; the mini map's presence/attributes are covered by Task 8's `test_session_detail_shows_edit_form_with_current_values`-style tests already asserting on `session-mini-map`'s HTML)

**Interfaces:**
- Consumes: `GET /api/recordings.geojson?session_id=` (existing, Phase 4).
- Produces: nothing consumed by a later task.

- [ ] **Step 1: Write the script**

Create `src/fledermap/web/static/session_map.js`:

```javascript
// Session detail page's mini-map (design spec section 7): plain recording
// markers for one session -- no clustering, no polyline, no site circle,
// spatial context only. Guarded on the container's presence since this
// script is loaded only on session_detail.html.
document.addEventListener("DOMContentLoaded", () => {
  const container = document.getElementById("session-mini-map");
  if (!container) return;

  const sessionId = container.dataset.sessionId;
  const map = L.map(container).setView([51.0, 10.0], 6);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "© OpenStreetMap contributors",
  }).addTo(map);

  fetch(`/api/recordings.geojson?session_id=${sessionId}`)
    .then((response) => response.json())
    .then((data) => {
      const layer = L.geoJSON(data, {
        pointToLayer: (feature, latlng) =>
          L.circleMarker(latlng, { color: "#333333" }),
      }).addTo(map);
      // No markers (a session with no GPS-bearing recordings) is not an
      // error -- design spec section 10, same "degrade in place" convention
      // as Phase 5a's missing-media placeholder. getBounds() on an empty
      // layer is invalid, so this just leaves the map at its default view.
      if (layer.getBounds().isValid()) {
        map.fitBounds(layer.getBounds(), { maxZoom: 15 });
      }
    })
    .catch((err) => console.error("session mini-map fetch failed", err));
});
```

- [ ] **Step 2: Add CSS for the mini-map and detail-page columns**

Add to `src/fledermap/web/static/app.css`:

```css
.session-detail-columns { display: flex; gap: 1rem; margin: 1rem 0; flex-wrap: wrap; }
.session-detail-columns .col { flex: 1 1 260px; }
#session-mini-map {
  width: 100%;
  height: 220px;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  margin-bottom: 0.4rem;
}
.merge-banner {
  margin: 1rem 0;
  padding: 0.75rem 1rem;
  background: var(--color-bg-subtle);
  border: 1px solid var(--color-accent);
  border-radius: 6px;
}
.merge-badge { color: #b7791f; font-size: 0.8rem; }
#sessions-table { width: 100%; border-collapse: collapse; margin-top: 1rem; }
#sessions-table th, #sessions-table td {
  text-align: left;
  padding: 0.4rem 0.6rem;
  border-bottom: 1px solid var(--color-border);
  font-size: 0.9rem;
}
```

- [ ] **Step 3: Lint and type-check**

Run: `hatch fmt --check && hatch run types:check`
Expected: clean (no Python touched, but this confirms nothing else broke).

- [ ] **Step 4: Manual verification via the `run` skill**

Follow the `run` skill to start `fledermap serve` against real data. In the browser:
- Open a session detail page for a session with GPS-bearing recordings → the mini-map shows markers and centers on them.
- Click "View on full map" → the main map loads filtered to that session (`?session=<id>` in the URL, confirmed via the Network tab that `/api/recordings.geojson` and `/api/sites.geojson` both carry `session=<id>`).
- Open a session detail page for a session with no GPS-bearing recordings → the mini-map renders with the default view, no error in the console.

Fix anything broken before moving on.

- [ ] **Step 5: Commit**

```bash
dangerouslyDisableSandbox=true git add src/fledermap/web/static/session_map.js src/fledermap/web/static/app.css
dangerouslyDisableSandbox=true git commit -m "feat: session detail mini-map"
```

---

## Task 11: Global navigation — collapsible left sidebar

**Files:**
- Modify: `src/fledermap/web/templates/_nav.html` (replace Task 7's placeholder)
- Modify: `src/fledermap/web/templates/map.html` (body restructuring)
- Modify: `src/fledermap/web/static/app.css`
- Test: `tests/test_map_view.py`, `tests/test_sessions_view.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `#sidebar` (nav element, `x-data`-driven collapse), `.main-content` (CSS class every page's content wrapper needs) — consumed structurally by `map.html`, `sessions_list.html`, `session_detail.html` (all already `{% include "_nav.html" %}`, from Tasks 7/8/this task).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_map_view.py`:

```python
def test_map_page_includes_the_sidebar(engine: Engine, tmp_path: Path) -> None:
    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.get("/")

    html = response.get_data(as_text=True)
    assert 'id="sidebar"' in html
    assert 'href="/sessions"' in html
```

Add to `tests/test_sessions_view.py`:

```python
def test_sessions_list_includes_the_sidebar(engine: Engine, tmp_path: Path) -> None:
    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.get("/sessions")

    html = response.get_data(as_text=True)
    assert 'id="sidebar"' in html
    assert 'href="/"' in html  # link back to the map
```

- [ ] **Step 2: Run to verify failure**

Run: `dangerouslyDisableSandbox=true hatch test tests/test_map_view.py tests/test_sessions_view.py -k sidebar -v`
Expected: FAIL (Task 7's placeholder `_nav.html` has an empty `#sidebar` with no links).

- [ ] **Step 3: Write the real `_nav.html`**

Replace the contents of `src/fledermap/web/templates/_nav.html`:

```html
{# src/fledermap/web/templates/_nav.html -- included by map.html,
   sessions_list.html, session_detail.html. Design spec section 8:
   expanded by default at normal widths (this is the app's only standing
   nav, so hiding it by default would just make /sessions harder to find);
   auto-collapses below a responsive breakpoint, matching the "columns
   stack on narrow screens" convention Phase 5a's drawer panels already
   use, plus a manual toggle at any width. #}
<nav id="sidebar"
     x-data="{ collapsed: window.matchMedia('(max-width: 800px)').matches }"
     :class="{ collapsed: collapsed }">
  <button type="button" id="sidebar-toggle" @click="collapsed = !collapsed" aria-label="Toggle navigation">☰</button>
  <a class="sidebar-link" href="/"><span class="label">🦇 Map</span></a>
  <a class="sidebar-link" href="/sessions"><span class="label">Sessions</span></a>
</nav>
```

- [ ] **Step 4: Restructure `map.html`'s body to make room for the sidebar**

In `src/fledermap/web/templates/map.html`, wrap the existing body content (everything currently between `<body x-data="filterForm()">` and the vendor `<script>` tags) in a new `<div class="main-content" id="page-content">`, and include `_nav.html` as the sidebar before it:

```html
<body x-data="filterForm()">
  {% include "_nav.html" %}
  <div class="main-content" id="page-content">
    <form id="filters">
      <label>From <input type="date" name="from" x-model="from"></label>
      <label>To <input type="date" name="to" x-model="to"></label>
      <label>Taxon
        <select name="taxon" x-model="taxon">
          <option value="">Any</option>
          {% for taxon in taxa %}
          <option value="{{ taxon.id }}">{{ taxon.scientific_name }}{% if taxon.common_name_en %} — {{ taxon.common_name_en }}{% endif %}</option>
          {% endfor %}
        </select>
      </label>
      <label>Session
        <select name="session" x-model="session">
          <option value="">Any</option>
          {% for item in sessions %}
          <option value="{{ item.id }}">{{ item.started_at.strftime('%Y-%m-%d %H:%M') }}–{{ item.ended_at.strftime('%H:%M') }} ({{ item.detector_key or 'unknown detector' }})</option>
          {% endfor %}
        </select>
      </label>
      <label>Source
        <select name="source" x-model="source">
          <option value="">Any</option>
          <option value="emt.guano">EMT GUANO</option>
          <option value="emt.wamd">EMT WAMD</option>
          <option value="emt.filename">EMT filename</option>
          <option value="emt.manual">EMT manual</option>
        </select>
      </label>
      <label>Verdict
        <select name="verdict" x-model="verdict">
          <option value="">Species only (default)</option>
          <option value="noise">Noise</option>
          <option value="no_id">No ID</option>
          <option value="all">All</option>
        </select>
      </label>
      <input type="hidden" name="site" x-model="site">
    </form>
    <span id="site-filter-chip" x-show="site" x-cloak>
      Site filter active
      <button type="button" @click="fledermapFilterBySite('')">×</button>
    </span>

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
  </div>

  <script src="{{ url_for('vendor.static', filename='leaflet.js') }}"></script>
  <script src="{{ url_for('vendor.static', filename='leaflet.markercluster.js') }}"></script>
  <script src="{{ url_for('vendor.static', filename='htmx.min.js') }}"></script>
  <script src="{{ url_for('vendor.static', filename='alpine.min.js') }}" defer></script>
  <script src="{{ url_for('static', filename='app.js') }}"></script>
</body>
```

(Nothing inside `#filters`/`#map`/`#drawer` changed -- only the new wrapping `<div>` and the `{% include %}` line were added. `#drawer`'s `position: fixed; left: 0; right: 0` still spans the full viewport width regardless of the sidebar -- deliberately left as-is: the drawer's `z-index: 1000` already puts it above everything, so it briefly covers the sidebar while open, the same way it already covers the map underneath it. Not a regression to fix here; revisit only if it turns out to bother real use.)

- [ ] **Step 5: Update `app.css`**

Replace the existing `body { display: flex; flex-direction: column; }` rule with:

```css
body { display: flex; flex-direction: row; }
```

Add the sidebar and main-content rules (near the top, after the `:root`/`html, body` block):

```css
#sidebar {
  flex: 0 0 auto;
  width: 200px;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  padding: 0.75rem 0.5rem;
  background: var(--color-bg-subtle);
  border-right: 1px solid var(--color-border);
}
#sidebar.collapsed { width: 48px; }
#sidebar.collapsed .sidebar-link .label { display: none; }
#sidebar-toggle { align-self: flex-end; margin-bottom: 0.5rem; }
.sidebar-link {
  display: block;
  padding: 0.4rem 0.5rem;
  border-radius: 4px;
  color: var(--color-text);
  text-decoration: none;
  font-size: 0.9rem;
  white-space: nowrap;
  overflow: hidden;
}
.sidebar-link:hover { background: var(--color-bg); }

.main-content { flex: 1 1 auto; min-width: 0; display: flex; flex-direction: column; }
main.main-content { padding: 0 1.5rem 1.5rem; overflow-y: auto; }
main.main-content h1 { margin-top: 1rem; }
```

(`.main-content` alone -- no padding -- is what `map.html`'s wrapping `<div>` gets, keeping `#map` edge-to-edge exactly as before. The `main.main-content` element-qualified rule only matches `sessions_list.html`/`session_detail.html`'s `<main>` wrapper, giving those two pages readable margins without touching the map page's layout at all.)

- [ ] **Step 6: Run to verify pass**

Run: `dangerouslyDisableSandbox=true hatch test tests/test_map_view.py tests/test_sessions_view.py -v`
Expected: PASS (every test in both files, not just the new sidebar ones — this step touched `map.html`'s structure).

- [ ] **Step 7: Full suite, lint, type-check**

Run: `dangerouslyDisableSandbox=true hatch test && hatch fmt --check && hatch run types:check`
Expected: clean.

- [ ] **Step 8: Manual verification via the `run` skill**

Follow the `run` skill. In the browser:
- On the map page at normal desktop width, the sidebar is expanded (shows "Map"/"Sessions" labels) and `#map` still fills the remaining width correctly.
- Narrow the browser window below ~800px → the sidebar auto-collapses to icons-only on the next page load.
- Click the sidebar toggle at any width → it flips collapsed/expanded immediately.
- Click "Sessions" from the map page, and "Map" from the sessions list page → both navigate correctly and the sidebar's expanded/collapsed state is consistent (each page re-evaluates the same breakpoint check independently, so this is "consistent," not "persisted" — no shared state across page loads is expected here).

Fix anything broken before moving on.

- [ ] **Step 9: Commit**

```bash
dangerouslyDisableSandbox=true git add src/fledermap/web/templates/_nav.html src/fledermap/web/templates/map.html src/fledermap/web/static/app.css tests/test_map_view.py tests/test_sessions_view.py
dangerouslyDisableSandbox=true git commit -m "feat: collapsible left sidebar navigation"
```

---

## Task 12: Cross-links from the drawer panels to session detail

**Files:**
- Modify: `src/fledermap/web/templates/_site_panel.html`
- Modify: `src/fledermap/web/templates/_recording_panel.html`
- Test: `tests/test_map_view.py`

**Interfaces:** none — leaf change.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_map_view.py` (find the existing test setup for the site panel and recording panel — reuse their fixture shape; if none conveniently reusable, use this self-contained form):

```python
def test_site_panel_links_sessions_to_session_detail(
    engine: Engine, tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        annotation_session = AnnotationSession(
            started_at=datetime(2026, 8, 21, 21, tzinfo=UTC),
            ended_at=datetime(2026, 8, 21, 23, tzinfo=UTC),
            kind=SessionKind.STATIONARY, detector_key="EMT\x1f1",
        )
        session.add(annotation_session)
        session.flush()
        site = Site(
            centroid=WKTElement("POINT(10.0 51.0)", srid=4326),
            radius_m=10.0, recording_count=1,
            first_at=datetime(2026, 8, 21, tzinfo=UTC),
            last_at=datetime(2026, 8, 21, tzinfo=UTC),
        )
        session.add(site)
        session.flush()
        session.add(
            Recording(
                audio_hash="a".rjust(64, "0"), path="a.wav",
                recorded_at=datetime(2026, 8, 21, 21, tzinfo=UTC),
                session_id=annotation_session.id, site_id=site.id,
            ),
        )
        session.commit()
        site_id, session_id = site.id, annotation_session.id

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.get(f"/sites/{site_id}/panel")

    html = response.get_data(as_text=True)
    assert f'href="/sessions/{session_id}"' in html


def test_recording_panel_links_session_to_session_detail(
    engine: Engine, tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        annotation_session = AnnotationSession(
            started_at=datetime(2026, 8, 21, 21, tzinfo=UTC),
            ended_at=datetime(2026, 8, 21, 23, tzinfo=UTC),
            kind=SessionKind.STATIONARY, detector_key="EMT\x1f1",
        )
        session.add(annotation_session)
        session.flush()
        session.add(
            Recording(
                audio_hash="a".rjust(64, "0"), path="a.wav",
                recorded_at=datetime(2026, 8, 21, 21, tzinfo=UTC),
                session_id=annotation_session.id,
            ),
        )
        session.commit()
        session_id = annotation_session.id

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.get(f"/recordings/{'a'.rjust(64, '0')}/panel")

    html = response.get_data(as_text=True)
    assert f'href="/sessions/{session_id}"' in html
```

(If `SessionKind`/`WKTElement`/`Site` aren't already imported at the top of `tests/test_map_view.py`, add them: `from fledermap.domain.codes import SessionKind` alongside the existing `IdSource, Verdict` import; `from geoalchemy2.elements import WKTElement`; `Site` is likely already imported given `test_map_view.py`'s existing site-panel tests.)

- [ ] **Step 2: Run to verify failure**

Run: `dangerouslyDisableSandbox=true hatch test tests/test_map_view.py -k session_detail -v`
Expected: FAIL (no such link exists yet in either template).

- [ ] **Step 3: Update `_site_panel.html`**

In `src/fledermap/web/templates/_site_panel.html`, change:

```html
      {% for s in detail.sessions %}
      <li>{{ s.started_at.strftime('%Y-%m-%d %H:%M') }}–{{ s.ended_at.strftime('%H:%M') }}</li>
      {% endfor %}
```
to:
```html
      {% for s in detail.sessions %}
      <li><a href="/sessions/{{ s.id }}">{{ s.started_at.strftime('%Y-%m-%d %H:%M') }}–{{ s.ended_at.strftime('%H:%M') }}</a></li>
      {% endfor %}
```

- [ ] **Step 4: Update `_recording_panel.html`**

In `src/fledermap/web/templates/_recording_panel.html`, change:

```html
    {% if recording_session %}
    <p>Session: {{ recording_session.started_at.strftime('%Y-%m-%d %H:%M') }}–{{ recording_session.ended_at.strftime('%H:%M') }} ({{ recording_session.detector_key or 'unknown detector' }})</p>
    {% endif %}
```
to:
```html
    {% if recording_session %}
    <p>Session: <a href="/sessions/{{ recording_session.id }}">{{ recording_session.started_at.strftime('%Y-%m-%d %H:%M') }}–{{ recording_session.ended_at.strftime('%H:%M') }} ({{ recording_session.detector_key or 'unknown detector' }})</a></p>
    {% endif %}
```

- [ ] **Step 5: Run to verify pass**

Run: `dangerouslyDisableSandbox=true hatch test tests/test_map_view.py -v`
Expected: PASS (full file — confirms nothing else in this heavily-shared template broke).

- [ ] **Step 6: Full suite, lint, type-check**

Run: `dangerouslyDisableSandbox=true hatch test && hatch fmt --check && hatch run types:check`
Expected: clean.

- [ ] **Step 7: Commit**

```bash
dangerouslyDisableSandbox=true git add src/fledermap/web/templates/_site_panel.html src/fledermap/web/templates/_recording_panel.html tests/test_map_view.py
dangerouslyDisableSandbox=true git commit -m "feat: link session mentions in the drawer panels to session detail"
```

---

## Task 13: Whole-slice verification

**Files:** none (verification only).

- [ ] **Step 1: Full suite, lint, type-check, one more time**

Run: `dangerouslyDisableSandbox=true hatch test && hatch fmt --check && hatch run types:check`
Expected: clean, zero warnings.

- [ ] **Step 2: Manual verification via the `run` skill**

Follow the `run` skill to start `fledermap serve` against real data (the two bundled sample recordings, or the real field data already ingested earlier in this project's history). In the browser, walk the whole slice end to end:

- From the map page, click "Sessions" in the sidebar → the sessions list loads, showing real sessions.
- Filter by detector, by date range, and by "open merge proposals only" — each narrows the table correctly (check the Network tab: each filter change re-requests `/sessions` with the right query string).
- Click a session row → the detail page loads: mini-map with markers (or the empty-state view if no GPS), edit form pre-filled with the session's actual current `kind`/`note`/`weather`, recordings list, and (if applicable) a merge banner.
- Change only the note, leave `kind` untouched, click Save → redirects back to the same page; the displayed `kind` is unchanged (not silently flipped by any background reclassification).
- If a real merge proposal exists in the data (or contrive one by running `fledermap derive` against out-of-order test data): open the affected session, edit the combined note/weather text, click "Accept merge" → redirects to the surviving session; the other session no longer appears in `/sessions`; its recordings now show under the surviving session.
- From a site or recording drawer panel (opened from the map), click the session link → lands on that session's detail page.

Fix anything broken before moving on — there's no automated safety net for the JS/CSS pieces, so this manual pass is the actual verification for those, not optional polish.

- [ ] **Step 3: Confirm no leftover placeholder content**

Run: `grep -rn "placeholder, replaced by Task 11" src/fledermap/web/templates/_nav.html`
Expected: no output (Task 11 already replaced the Task 7 placeholder comment; this is a final sanity check that nothing reverted it).

- [ ] **Step 4: Final commit (if Step 2 required fixes)**

```bash
dangerouslyDisableSandbox=true git add -u
dangerouslyDisableSandbox=true git commit -m "fix: address phase 5b manual verification findings"
```

(Skip this step entirely if Step 2 found nothing to fix.)
