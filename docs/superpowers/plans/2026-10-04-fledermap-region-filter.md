# Region Filter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a poiidx-backed **Region** dropdown to the map's filter bar that filters recordings by the Geofabrik regions containing them, shown only when poiidx is configured.

**Architecture:** A worker job asks poiidx (`list_regions(Point)`) which regions contain each recording's rounded location, caches the answer per location, and fans it out into a `recording_region` join table; it then recomputes a `region.is_distinct` flag from one `list_regions(MultiPoint(all locations))` call. The web process reads only `bats_db`: the dropdown lists `region WHERE is_distinct`, and `filtered_recordings(region_id=...)` is an `EXISTS` on `recording_region`.

**Tech Stack:** Python 3.12, SQLAlchemy 2 + Alembic, PostGIS, Procrastinate, poiidx 0.1.0, Flask + Jinja + Alpine.js, pytest (`db` marker), hatch.

**Spec:** `docs/superpowers/specs/2026-10-04-fledermap-region-filter-design.md`

## Global Constraints

- All Python runs through `hatch` (`hatch test`, `hatch fmt`, `hatch run types:check`, `hatch run <cmd>`); never bare `python`/`pip`, never `PYTHONPATH`. `hatch run` brace-substitutes its arguments: put throwaway code in a file.
- `hatch` needs network for the poiidx bump (Task 1): run that command with `dangerouslyDisableSandbox: true`. Run `git` unsandboxed too (stale `.git/config.lock`). Stage explicit paths, never `git add -A`/`.`.
- `db`-marked tests need Docker: run them with `dangerouslyDisableSandbox: true`, from the controlling session, never inside a dispatched subagent (CLAUDE.md "Environment gotchas"). Pre-commit's `tests` hook runs only `hatch test -m "not db"`.
- Commit messages follow Conventional Commits and end with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
- poiidx pin: `poiidx>=0.1.0,<0.2` (was `>=0.0.9,<0.1`).
- Back up the database with `scripts/db-backup.sh` before applying the migration to any real database.
- `region.is_distinct` is recomputed on every ingest cycle with poiidx configured, even with nothing pending. `recording_region.region_id` is a real foreign key to `region.id`.
- Region control is **omitted** (not disabled) when poiidx is not configured or no region is stored. It sits directly after the Taxon group in `#filters`, as a plain `<label>Region <select name="region" x-model="region">` with an `Any` first option. Map page only: no change to `/sessions` or `/search`.
- The web process never imports or connects to poiidx.
- `location_region.location_key` format: `f"{lat:.3f},{lon:.3f}"` (3 decimal degrees, same rounding as `SiteNameCache`).
- Test output must be pristine (no warnings), mypy over `src`, `tests` and `scripts` must pass (no `# type: ignore`; bind `X | None`, assert, dereference).
- Comments explain why, never cite plan tasks, review rounds or this plan.

## Review Focus

Most likely first. Each has a test in the task named.

1. A recording in **no region** (outside Geofabrik coverage): resolves to an empty array, gets `region_resolved_at`, is not re-asked next cycle, and never breaks the dropdown. (Task 3)
2. **Nesting:** selecting Germany returns a recording that also sits in Bremen; selecting Bremen does not return a Germany-only recording. (Task 5)
3. A **stale or unknown region id** in the URL (`?region=nonexistent`) yields an empty result, not a 400/500. (Task 5)
4. A recording going **missing** changes `is_distinct` on the next refresh without any new recordings arriving. (Task 3)
5. poiidx **turned off after being on**: stored rows remain but the dropdown is not rendered. (Task 6)

Known gap, not covered by a test and not in the spec: a recording whose `geom` changes after it was resolved keeps its old regions until `fledermap backfill-regions --force`.

---

### Task 1: Bump poiidx to 0.1.0

**Files:**
- Modify: `pyproject.toml:50-53` (the comment above the pin and the pin itself)

**Interfaces:**
- Produces: the hatch envs resolve `poiidx` 0.1.0, so `poiidx.list_regions(shape, buffer=None)` exists and returns `list[dict]` with keys `id`, `name`, `url`, `used`, `distinct`, `downloaded`, `imported`.

- [ ] **Step 1: Read the pin and its comment**

Run: `sed -n 44,56p pyproject.toml`

- [ ] **Step 2: Change the pin**

Replace `"poiidx>=0.0.9,<0.1",` with `"poiidx>=0.1.0,<0.2",`. Keep the existing comment, but if it names `0.0.9` or `<0.1`, update the version numbers in it to match.

- [ ] **Step 3: Install and verify the new version resolves**

Needs network, run with `dangerouslyDisableSandbox: true`:

Run: `hatch run python -c "import importlib.metadata as m, poiidx; print(m.version('poiidx'), hasattr(poiidx, 'list_regions'))"`
Expected: `0.1.0 True`. If hatch cannot resolve 0.1.0 (not on PyPI), STOP and report to Janna; do not work around it.

- [ ] **Step 4: Confirm nothing regressed**

Run: `hatch test -m "not db"` (unsandboxed, same reason)
Expected: all pass, no warnings.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml
git commit -m "build: bump poiidx to 0.1.0 for region listing

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Schema — region tables and migration

**Files:**
- Modify: `src/fledermap/store/models.py` (imports; `Recording` gains one column; three new models after `SiteNameCache`)
- Create: `src/fledermap/alembic/versions/a1d4c7e9b2f6_add_region_tables.py`
- Test: `tests/test_migrations.py` (existing drift test must pass), `tests/test_region_models.py` (new)

**Interfaces:**
- Produces (models.py):
  - `Region(id: str PK, name: str, is_distinct: bool)`
  - `LocationRegion(id: int PK, location_key: str unique+indexed, region_ids: list[str])`
  - `RecordingRegion(recording_id: int PK/FK→recording.id ON DELETE CASCADE, region_id: str PK/FK→region.id, indexed)`
  - `Recording.region_resolved_at: datetime | None`

- [ ] **Step 1: Write the failing model test**

`tests/test_region_models.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as OrmSession

from fledermap.store.models import (
    LocationRegion,
    Recording,
    RecordingRegion,
    Region,
)

pytestmark = pytest.mark.db


def _recording(session: OrmSession) -> Recording:
    r = Recording(
        audio_hash="a" * 64,
        path="a.wav",
        recorded_at=datetime(2026, 10, 4, tzinfo=UTC),
    )
    session.add(r)
    session.flush()
    return r


def test_region_rows_round_trip(engine: Engine) -> None:
    with OrmSession(engine) as session:
        session.add(Region(id="germany", name="Germany", is_distinct=True))
        session.add(LocationRegion(location_key="52.520,13.405", region_ids=["germany"]))
        session.commit()
        assert session.scalars(select(Region.name)).all() == ["Germany"]
        row = session.scalars(select(LocationRegion)).one()
        assert row.region_ids == ["germany"]


def test_recording_region_rejects_an_unknown_region(engine: Engine) -> None:
    with OrmSession(engine) as session:
        r = _recording(session)
        session.add(RecordingRegion(recording_id=r.id, region_id="nowhere"))
        with pytest.raises(IntegrityError):
            session.flush()


def test_deleting_a_recording_cascades_to_recording_region(engine: Engine) -> None:
    with OrmSession(engine) as session:
        session.add(Region(id="germany", name="Germany", is_distinct=False))
        r = _recording(session)
        session.add(RecordingRegion(recording_id=r.id, region_id="germany"))
        session.commit()
        session.delete(r)
        session.commit()
        assert session.scalars(select(RecordingRegion)).all() == []
```

- [ ] **Step 2: Run it to verify it fails**

Run (unsandboxed): `hatch test tests/test_region_models.py`
Expected: FAIL with `ImportError: cannot import name 'LocationRegion'`.

- [ ] **Step 3: Add the models**

In `models.py`: add `ARRAY` to the `sqlalchemy.dialects.postgresql` import (`from sqlalchemy.dialects.postgresql import ARRAY, JSONB`). `Boolean`, `ForeignKey`, `Text`, `String`, `Integer`, `DateTime` are already imported. In `Recording`, directly after `note`:

```python
    # Set once this recording's containing regions are in `recording_region`,
    # including when there are none. Without it a recording outside every
    # region has no recording_region rows and would look unresolved forever.
    region_resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
```

After `SiteNameCache`:

```python
class Region(Base):
    """A poiidx/Geofabrik region that contains at least one recorded location.

    `is_distinct` is derived data (poiidx's rule over the current set of
    recorded locations), recomputed in place every ingest cycle by
    `services.regions.refresh_distinct_flags`; it changes as recordings are
    added or go missing. Rows are kept even when not distinct so
    `recording_region.region_id` can be a real foreign key and a region that
    becomes distinct later needs no re-resolution.
    """

    __tablename__ = "region"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    is_distinct: Mapped[bool] = mapped_column(Boolean, default=False)


class LocationRegion(Base):
    """poiidx's answer for one rounded coordinate (`services.regions.location_key`),
    so many recordings at the same spot cost one poiidx call. An empty array
    means "resolved, in no region" and is never re-asked."""

    __tablename__ = "location_region"

    id: Mapped[int] = mapped_column(primary_key=True)
    location_key: Mapped[str] = mapped_column(String(24), unique=True, index=True)
    region_ids: Mapped[list[str]] = mapped_column(ARRAY(Text))


class RecordingRegion(Base):
    """Every region whose area contains the recording's point. Regions nest
    (Bremen, Germany, DACH), so a recording has several rows and filtering by
    a larger region matches its sub-regions' recordings with no extra logic."""

    __tablename__ = "recording_region"

    recording_id: Mapped[int] = mapped_column(
        ForeignKey("recording.id", ondelete="CASCADE"),
        primary_key=True,
    )
    region_id: Mapped[str] = mapped_column(
        ForeignKey("region.id"),
        primary_key=True,
        index=True,
    )
```

- [ ] **Step 4: Write the migration**

`src/fledermap/alembic/versions/a1d4c7e9b2f6_add_region_tables.py` (current head is `7c1a8e2b6f43`):

```python
"""add region tables

Revision ID: a1d4c7e9b2f6
Revises: 7c1a8e2b6f43
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a1d4c7e9b2f6"
down_revision: str | Sequence[str] | None = "7c1a8e2b6f43"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "recording",
        sa.Column("region_resolved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "region",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("is_distinct", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "location_region",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("location_key", sa.String(length=24), nullable=False),
        sa.Column("region_ids", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_location_region_location_key"),
        "location_region",
        ["location_key"],
        unique=True,
    )
    op.create_table(
        "recording_region",
        sa.Column("recording_id", sa.Integer(), nullable=False),
        sa.Column("region_id", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["recording_id"], ["recording.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["region_id"], ["region.id"]),
        sa.PrimaryKeyConstraint("recording_id", "region_id"),
    )
    op.create_index(
        op.f("ix_recording_region_region_id"),
        "recording_region",
        ["region_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_recording_region_region_id"), table_name="recording_region")
    op.drop_table("recording_region")
    op.drop_index(op.f("ix_location_region_location_key"), table_name="location_region")
    op.drop_table("location_region")
    op.drop_table("region")
    op.drop_column("recording", "region_resolved_at")
```

- [ ] **Step 5: Run the new tests and the drift test**

Run (unsandboxed): `hatch test tests/test_region_models.py tests/test_migrations.py`
Expected: PASS. If `test_migration_matches_the_models` reports drift, fix the migration to match the models (the models are the intent), not the test.

- [ ] **Step 6: Mutation-test the drift check**

Temporarily rename `region_ids` to `region_idz` in the migration only, run `hatch test tests/test_migrations.py`, confirm it FAILS, then revert. (CLAUDE.md: a drift assertion that cannot fail is worse than none.)

- [ ] **Step 7: Lint, types, commit**

Run: `hatch fmt` then `hatch run types:check`
Expected: clean.

```bash
git add src/fledermap/store/models.py src/fledermap/alembic/versions/a1d4c7e9b2f6_add_region_tables.py tests/test_region_models.py
git commit -m "feat: add region, location_region and recording_region tables

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Region resolution service

**Files:**
- Create: `src/fledermap/services/regions.py`
- Test: `tests/test_regions_service.py`

**Interfaces:**
- Consumes (Task 2): `Region`, `LocationRegion`, `RecordingRegion`, `Recording.region_resolved_at`. Consumes `poiidx.list_regions(shape) -> list[dict]` with keys `id`, `name`, `distinct`. Consumes `fledermap.store.geo.decode_point(geom) -> tuple[float, float] | None` (lon, lat).
- Produces:
  - `location_key(lon: float, lat: float) -> str`
  - `resolve_pending(session: OrmSession) -> int` (number of recordings resolved)
  - `refresh_distinct_flags(session: OrmSession) -> None`
  - `resolve_regions(session: OrmSession) -> int` (both steps, returns `resolve_pending`'s count)
  - `reset_region_data(session: OrmSession) -> None` (deletes `recording_region`, `location_region`, `region`; nulls every `region_resolved_at`)
  - `list_distinct_regions(session: OrmSession) -> Sequence[Region]` (name-ordered)
  - The enqueue function is added in Task 4.

- [ ] **Step 1: Write the failing tests**

`tests/test_regions_service.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from geoalchemy2.elements import WKTElement
from shapely.geometry.base import BaseGeometry
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session as OrmSession

from fledermap.services import regions
from fledermap.store.models import LocationRegion, Recording, RecordingRegion, Region

pytestmark = pytest.mark.db

BREMEN = {"id": "bremen", "name": "Bremen"}
GERMANY = {"id": "germany", "name": "Germany"}
AUSTRIA = {"id": "austria", "name": "Austria"}


def _recording(
    session: OrmSession,
    audio_hash: str,
    lon: float | None,
    lat: float | None,
    *,
    missing: bool = False,
) -> Recording:
    r = Recording(
        audio_hash=audio_hash,
        path=f"{audio_hash}.wav",
        recorded_at=datetime(2026, 10, 4, tzinfo=UTC),
        geom=None if lon is None else WKTElement(f"POINT({lon} {lat})", srid=4326),
        missing_since=datetime(2026, 10, 4, tzinfo=UTC) if missing else None,
    )
    session.add(r)
    session.flush()
    return r


def _fake_poiidx(
    monkeypatch: pytest.MonkeyPatch,
    by_point: dict[tuple[float, float], list[dict[str, Any]]],
    distinct_ids: set[str],
) -> list[BaseGeometry]:
    """Fake `poiidx.list_regions`: a Point returns its candidates, a
    MultiPoint returns every candidate flagged by `distinct_ids`. Returns the
    list of shapes it was called with."""
    calls: list[BaseGeometry] = []

    def fake(shape: BaseGeometry, buffer: float | None = None) -> list[dict[str, Any]]:
        calls.append(shape)
        if shape.geom_type == "Point":
            return [
                {**r, "distinct": None} for r in by_point[(shape.x, shape.y)]
            ]
        seen: dict[str, dict[str, Any]] = {}
        for candidates in by_point.values():
            for r in candidates:
                seen[r["id"]] = r
        return [
            {**r, "distinct": r["id"] in distinct_ids} for r in seen.values()
        ]

    monkeypatch.setattr(regions.poiidx, "list_regions", fake)
    return calls


def test_location_key_rounds_to_three_decimals() -> None:
    assert regions.location_key(13.40549, 52.52049) == "52.520,13.405"


def test_resolve_pending_stores_every_containing_region(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_poiidx(monkeypatch, {(8.8, 53.1): [BREMEN, GERMANY]}, {"bremen"})
    with OrmSession(engine) as session:
        r = _recording(session, "a" * 64, 8.8, 53.1)
        assert regions.resolve_pending(session) == 1
        session.commit()
        rows = session.scalars(
            select(RecordingRegion.region_id).where(RecordingRegion.recording_id == r.id)
        ).all()
        assert sorted(rows) == ["bremen", "germany"]
        refreshed = session.get(Recording, r.id)
        assert refreshed is not None
        assert refreshed.region_resolved_at is not None


def test_a_recording_in_no_region_is_resolved_and_not_asked_again(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _fake_poiidx(monkeypatch, {(0.0, 0.0): []}, set())
    with OrmSession(engine) as session:
        r = _recording(session, "b" * 64, 0.0, 0.0)
        regions.resolve_pending(session)
        session.commit()
        refreshed = session.get(Recording, r.id)
        assert refreshed is not None
        assert refreshed.region_resolved_at is not None
        assert session.scalars(select(RecordingRegion)).all() == []
        assert session.scalars(select(LocationRegion.region_ids)).one() == []
        assert regions.resolve_pending(session) == 0
    assert len(calls) == 1


def test_recordings_at_one_location_cost_one_poiidx_call(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _fake_poiidx(monkeypatch, {(8.8, 53.1): [BREMEN]}, {"bremen"})
    with OrmSession(engine) as session:
        _recording(session, "c" * 64, 8.8, 53.1)
        _recording(session, "d" * 64, 8.8, 53.1)
        assert regions.resolve_pending(session) == 2
    assert len(calls) == 1


def test_recordings_without_a_point_are_left_unresolved(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_poiidx(monkeypatch, {}, set())
    with OrmSession(engine) as session:
        r = _recording(session, "e" * 64, None, None)
        assert regions.resolve_pending(session) == 0
        assert r.region_resolved_at is None


def test_refresh_distinct_flags_follows_poiidx(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_poiidx(
        monkeypatch,
        {(8.8, 53.1): [BREMEN, GERMANY], (13.4, 52.5): [GERMANY]},
        {"bremen", "germany"},
    )
    with OrmSession(engine) as session:
        _recording(session, "f" * 64, 8.8, 53.1)
        _recording(session, "1" * 64, 13.4, 52.5)
        regions.resolve_regions(session)
        session.commit()
        flags = {r.id: r.is_distinct for r in session.scalars(select(Region))}
        assert flags == {"bremen": True, "germany": True}
        assert [r.id for r in regions.list_distinct_regions(session)] == [
            "bremen",
            "germany",
        ]


def test_a_recording_going_missing_flips_the_flag_without_new_recordings(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    by_point = {(8.8, 53.1): [BREMEN, GERMANY], (16.4, 48.2): [AUSTRIA]}
    _fake_poiidx(monkeypatch, by_point, {"bremen", "austria"})
    with OrmSession(engine) as session:
        _recording(session, "2" * 64, 8.8, 53.1)
        vienna = _recording(session, "3" * 64, 16.4, 48.2)
        regions.resolve_regions(session)
        session.commit()
        assert {r.id for r in regions.list_distinct_regions(session)} == {
            "bremen",
            "austria",
        }

        vienna.missing_since = datetime(2026, 10, 5, tzinfo=UTC)
        # With only the Bremen point left, poiidx would flag Bremen alone.
        _fake_poiidx(monkeypatch, {(8.8, 53.1): [BREMEN, GERMANY]}, {"bremen"})
        regions.refresh_distinct_flags(session)
        session.commit()
        assert [r.id for r in regions.list_distinct_regions(session)] == ["bremen"]


def test_refresh_with_no_live_points_clears_every_flag(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _fake_poiidx(monkeypatch, {(8.8, 53.1): [BREMEN]}, {"bremen"})
    with OrmSession(engine) as session:
        r = _recording(session, "4" * 64, 8.8, 53.1)
        regions.resolve_regions(session)
        r.missing_since = datetime(2026, 10, 5, tzinfo=UTC)
        before = len(calls)
        regions.refresh_distinct_flags(session)
        assert len(calls) == before  # nothing to ask poiidx about
        assert regions.list_distinct_regions(session) == []


def test_reset_region_data_clears_everything_and_unresolves(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_poiidx(monkeypatch, {(8.8, 53.1): [BREMEN]}, {"bremen"})
    with OrmSession(engine) as session:
        r = _recording(session, "5" * 64, 8.8, 53.1)
        regions.resolve_regions(session)
        session.commit()
        regions.reset_region_data(session)
        session.commit()
        assert session.scalars(select(Region)).all() == []
        assert session.scalars(select(LocationRegion)).all() == []
        assert session.scalars(select(RecordingRegion)).all() == []
        session.refresh(r)
        assert r.region_resolved_at is None
```

- [ ] **Step 2: Run to verify failure**

Run (unsandboxed): `hatch test tests/test_regions_service.py`
Expected: FAIL with `ImportError` / `ModuleNotFoundError` for `fledermap.services.regions`.

- [ ] **Step 3: Implement the service**

`src/fledermap/services/regions.py`:

```python
"""poiidx region membership for the map's Region filter (design spec
2026-10-04-fledermap-region-filter-design.md).

Everything here runs in the worker, off the request path. poiidx's
`list_regions` only reads its region index (no download/import), so no
serialization lock like site naming's is needed.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import poiidx
from shapely.geometry import MultiPoint, Point
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session as OrmSession

from fledermap.store.geo import decode_point
from fledermap.store.models import LocationRegion, Recording, RecordingRegion, Region


def location_key(lon: float, lat: float) -> str:
    """3 decimal degrees (~111 m): coarse enough that many recordings at one
    spot share one poiidx call, fine against region borders, which are
    kilometres wide in practice. Same rounding as `SiteNameCache`'s key."""
    return f"{lat:.3f},{lon:.3f}"


def _upsert_region(session: OrmSession, region_id: str, name: str) -> None:
    existing = session.get(Region, region_id)
    if existing is None:
        session.add(Region(id=region_id, name=name, is_distinct=False))
    elif existing.name != name:
        existing.name = name


def resolve_pending(session: OrmSession) -> int:
    """Resolve every live, located recording not yet resolved. Returns how
    many recordings were resolved."""
    pending = session.scalars(
        select(Recording).where(
            Recording.region_resolved_at.is_(None),
            Recording.missing_since.is_(None),
            Recording.geom.is_not(None),
        ),
    ).all()

    by_key: dict[str, list[Recording]] = {}
    points: dict[str, tuple[float, float]] = {}
    for recording in pending:
        point = decode_point(recording.geom)
        if point is None:
            continue
        key = location_key(*point)
        by_key.setdefault(key, []).append(recording)
        points.setdefault(key, point)

    cached = {
        row.location_key: row
        for row in session.scalars(
            select(LocationRegion).where(LocationRegion.location_key.in_(by_key)),
        )
    }

    now = datetime.now(UTC)
    resolved = 0
    for key, recordings in by_key.items():
        row = cached.get(key)
        if row is None:
            lon, lat = points[key]
            found: list[dict[str, Any]] = poiidx.list_regions(Point(lon, lat))
            for info in found:
                _upsert_region(session, info["id"], info["name"])
            row = LocationRegion(
                location_key=key,
                region_ids=[info["id"] for info in found],
            )
            session.add(row)
            # Regions must exist before recording_region rows reference them.
            session.flush()
        for recording in recordings:
            for region_id in row.region_ids:
                session.add(
                    RecordingRegion(recording_id=recording.id, region_id=region_id),
                )
            recording.region_resolved_at = now
            resolved += 1
    session.flush()
    return resolved


def refresh_distinct_flags(session: OrmSession) -> None:
    """Recompute `Region.is_distinct` from poiidx's own rule over the current
    set of live recorded locations. Runs every ingest cycle, not only when
    something was resolved: recordings going missing change the answer too."""
    points: dict[str, tuple[float, float]] = {}
    for geom in session.scalars(
        select(Recording.geom).where(
            Recording.missing_since.is_(None),
            Recording.geom.is_not(None),
        ),
    ):
        point = decode_point(geom)
        if point is not None:
            points.setdefault(location_key(*point), point)

    distinct_ids: set[str] = set()
    if points:
        found: list[dict[str, Any]] = poiidx.list_regions(
            MultiPoint(list(points.values())),
        )
        distinct_ids = {info["id"] for info in found if info["distinct"]}

    session.execute(update(Region).values(is_distinct=Region.id.in_(distinct_ids)))
    session.flush()


def resolve_regions(session: OrmSession) -> int:
    resolved = resolve_pending(session)
    refresh_distinct_flags(session)
    return resolved


def reset_region_data(session: OrmSession) -> None:
    """Forget everything region-related so the next run re-asks poiidx. Order
    matters: recording_region references region."""
    session.execute(delete(RecordingRegion))
    session.execute(delete(LocationRegion))
    session.execute(delete(Region))
    session.execute(update(Recording).values(region_resolved_at=None))
    session.flush()


def list_distinct_regions(session: OrmSession) -> Sequence[Region]:
    return session.scalars(
        select(Region).where(Region.is_distinct.is_(True)).order_by(Region.name),
    ).all()
```

- [ ] **Step 4: Run to verify pass**

Run (unsandboxed): `hatch test tests/test_regions_service.py`
Expected: all PASS. If `update(Region).values(is_distinct=Region.id.in_(...))` is rejected by SQLAlchemy/Postgres, use `case((Region.id.in_(distinct_ids), True), else_=False)` instead and keep the test.

- [ ] **Step 5: Lint, types, commit**

Run: `hatch fmt` then `hatch run types:check`

```bash
git add src/fledermap/services/regions.py tests/test_regions_service.py
git commit -m "feat: resolve recording regions via poiidx and flag distinct ones

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Job, ingest-cycle hook and `backfill-regions` CLI

**Files:**
- Modify: `src/fledermap/jobs/tasks.py` (new lock constant + task near `name_site_task`; hook in `run_ingest_cycle` after the `enqueue_site_naming` commit, around line 712-720)
- Modify: `src/fledermap/services/regions.py` (add `enqueue_region_resolution`)
- Modify: `src/fledermap/cli/main.py` (import + new command after `backfill_site_names_command`)
- Test: `tests/test_regions_service.py` (enqueue tests), `tests/test_cli.py` (CLI tests)

**Interfaces:**
- Consumes (Task 3): `regions.resolve_regions(session)`, `regions.reset_region_data(session)`.
- Produces:
  - `jobs.tasks._RESOLVE_REGIONS_LOCK: str`, `jobs.tasks.resolve_regions_task` (`queue="geo"`, no arguments)
  - `regions.enqueue_region_resolution(engine: Engine, *, poiidx_database_url: str | None) -> bool` (True if a job was deferred)
  - CLI `fledermap backfill-regions [--force]`, prints `enqueued 0` or `enqueued 1`

- [ ] **Step 1: Write the failing enqueue tests**

Append to `tests/test_regions_service.py`. First read how `tests/test_site_naming.py` tests `enqueue_site_naming` (grep `ensure_schema(jobs_app`) and copy its setup (it calls `ensure_schema(jobs_app, engine)` before enqueueing). Then:

```python
from fledermap.jobs.app import ensure_schema
from fledermap.jobs.tasks import app as jobs_app


def test_enqueue_is_a_no_op_without_poiidx(engine: Engine) -> None:
    assert regions.enqueue_region_resolution(engine, poiidx_database_url=None) is False


def test_enqueue_defers_one_job_and_dedupes_the_second(engine: Engine) -> None:
    ensure_schema(jobs_app, engine)
    url = "postgresql://u:p@localhost/poiidx_bats_db"
    assert regions.enqueue_region_resolution(engine, poiidx_database_url=url) is True
    assert regions.enqueue_region_resolution(engine, poiidx_database_url=url) is False
```

- [ ] **Step 2: Write the failing CLI tests**

Append to `tests/test_cli.py` (mirrors the `backfill-site-names` tests; uses the same `clean_database_url`, imports already present there):

```python
def test_backfill_regions_without_poiidx_reports_zero_and_touches_nothing(
    clean_database_url: str,
    tmp_path: Path,
) -> None:
    env = {
        "FLEDERMAP_DATABASE_URL": clean_database_url,
        "FLEDERMAP_ARCHIVE_ROOTS": str(tmp_path / "archive"),
    }
    result = CliRunner().invoke(cli, ["backfill-regions", "--force"], env=env)

    assert result.exit_code == 0, result.output
    assert "enqueued 0" in result.output


def test_backfill_regions_force_resets_then_enqueues(
    clean_database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_reset(session: object) -> None:
        calls.append("reset")

    def fake_enqueue(engine: object, *, poiidx_database_url: str | None) -> bool:
        calls.append("enqueue")
        return True

    monkeypatch.setattr("fledermap.cli.main.reset_region_data", fake_reset)
    monkeypatch.setattr("fledermap.cli.main.enqueue_region_resolution", fake_enqueue)
    env = {
        "FLEDERMAP_DATABASE_URL": clean_database_url,
        "FLEDERMAP_ARCHIVE_ROOTS": str(tmp_path / "archive"),
        "FLEDERMAP_POIIDX_DATABASE_URL": "postgresql://u:p@localhost/poiidx_bats_db",
    }
    result = CliRunner().invoke(cli, ["backfill-regions", "--force"], env=env)

    assert result.exit_code == 0, result.output
    assert calls == ["reset", "enqueue"]
    assert "enqueued 1" in result.output


def test_backfill_regions_without_force_does_not_reset(
    clean_database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        "fledermap.cli.main.reset_region_data", lambda session: calls.append("reset")
    )
    monkeypatch.setattr(
        "fledermap.cli.main.enqueue_region_resolution",
        lambda engine, *, poiidx_database_url: False,
    )
    env = {
        "FLEDERMAP_DATABASE_URL": clean_database_url,
        "FLEDERMAP_ARCHIVE_ROOTS": str(tmp_path / "archive"),
        "FLEDERMAP_POIIDX_DATABASE_URL": "postgresql://u:p@localhost/poiidx_bats_db",
    }
    result = CliRunner().invoke(cli, ["backfill-regions"], env=env)

    assert result.exit_code == 0, result.output
    assert calls == []
    assert "enqueued 0" in result.output
```

- [ ] **Step 3: Run to verify failure**

Run (unsandboxed): `hatch test tests/test_regions_service.py tests/test_cli.py -k "enqueue or backfill_regions"`
Expected: FAIL (`enqueue_region_resolution` missing; no such CLI command).

- [ ] **Step 4: Add the task and lock to `jobs/tasks.py`**

Directly after `name_site_task`:

```python
_RESOLVE_REGIONS_LOCK = "poiidx-resolve-regions"


@app.task(queue="geo", pass_context=True, retry=_RETRY)
def resolve_regions_task(context: procrastinate.JobContext) -> None:
    """Resolve region membership for every pending recording, then refresh
    which regions are distinct (design spec
    2026-10-04-fledermap-region-filter-design.md). One job per cycle, not one
    per recording: `list_regions` only reads poiidx's region index, so
    batching is cheap and keeps the queue small."""
    # Local import for the same circularity reason as `name_site_task`'s.
    from fledermap.services import regions, site_naming

    config: Config = context.additional_context["config"]
    engine = context.additional_context["engine"]
    if config.poiidx_database_url is None:
        # Config changed after this job was deferred -- nothing to do.
        return
    site_naming.ensure_connected(config.poiidx_database_url)

    with OrmSession(engine) as session:
        regions.resolve_regions(session)
        session.commit()
```

- [ ] **Step 5: Add `enqueue_region_resolution` to `services/regions.py`**

Add imports at the top: `import procrastinate`, `from sqlalchemy.engine import Engine`, and (mirroring `site_naming.py`) `from fledermap.jobs.tasks import _RESOLVE_REGIONS_LOCK, resolve_regions_task` and `from fledermap.jobs.tasks import app as jobs_app`. Append:

```python
def enqueue_region_resolution(
    engine: Engine,
    *,
    poiidx_database_url: str | None,
) -> bool:
    """Defer one resolution job. Called every ingest cycle and from
    `backfill-regions`: the distinct-flag refresh inside the job must run
    even with nothing pending (recordings going missing change it), so there
    is deliberately no "anything pending?" shortcut. A true no-op when poiidx
    isn't configured. The queueing lock drops a duplicate while one is
    already waiting."""
    if not poiidx_database_url:
        return False
    try:
        jobs_app.open(engine)
    except NotImplementedError:
        pass  # already open inside a running worker -- see enqueue_media's docstring
    try:
        resolve_regions_task.configure(
            lock=_RESOLVE_REGIONS_LOCK,
            queueing_lock=_RESOLVE_REGIONS_LOCK,
        ).defer()
    except procrastinate.exceptions.AlreadyEnqueued:
        return False
    return True
```

- [ ] **Step 6: Hook into the ingest cycle**

In `run_ingest_cycle`, right after the existing `enqueue_site_naming(...)` call and its `session.commit()` (the job must see committed data):

```python
        from fledermap.services.regions import enqueue_region_resolution

        enqueue_region_resolution(
            engine,
            poiidx_database_url=config.poiidx_database_url,
        )
```

- [ ] **Step 7: Add the CLI command**

In `cli/main.py` add `from fledermap.services.regions import enqueue_region_resolution, reset_region_data` beside the `enqueue_site_naming` import, and after `backfill_site_names_command`:

```python
@cli.command(name="backfill-regions")
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help=(
        "Forget every stored region and re-resolve all recordings from "
        "poiidx -- for a poiidx region-index change whose effect the "
        "per-location cache would otherwise hide forever."
    ),
)
def backfill_regions_command(force: bool) -> None:
    """Resolve poiidx regions for recordings that don't have them yet. Turning
    poiidx on does this automatically at the worker's startup cycle; this is
    the explicit, on-demand version. A no-op (reports "enqueued 0") if
    FLEDERMAP_POIIDX_DATABASE_URL isn't configured -- and --force then leaves
    stored data alone, since nothing could re-resolve it."""
    try:
        config = Config.from_env()
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc

    engine = make_engine(config.database_url)
    _run_migrations(config.database_url)
    ensure_schema(jobs_app, engine)

    if not config.poiidx_database_url:
        click.echo("enqueued 0")
        return

    if force:
        with OrmSession(engine) as session:
            reset_region_data(session)
            session.commit()

    enqueued = enqueue_region_resolution(
        engine,
        poiidx_database_url=config.poiidx_database_url,
    )
    click.echo(f"enqueued {int(enqueued)}")
```

- [ ] **Step 7b: Confirm the hook ran in the ingest-cycle tests**

Run (unsandboxed): `hatch test tests/test_jobs_tasks.py`
Expected: PASS (no poiidx URL in those tests, so the new call is a no-op).

- [ ] **Step 8: Run all new tests**

Run (unsandboxed): `hatch test tests/test_regions_service.py tests/test_cli.py tests/test_jobs_tasks.py`
Expected: PASS.

- [ ] **Step 9: Lint, types, commit**

Run: `hatch fmt` then `hatch run types:check`

```bash
git add src/fledermap/jobs/tasks.py src/fledermap/services/regions.py src/fledermap/cli/main.py tests/test_regions_service.py tests/test_cli.py
git commit -m "feat: resolve regions in the ingest cycle and add backfill-regions

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Region filter in the query layer and routes

**Files:**
- Modify: `src/fledermap/web/params.py` (new `parse_region_filter` after `parse_taxon_filter`)
- Modify: `src/fledermap/services/map_query.py` (`filtered_recordings` signature and `stmt` filters; import `RecordingRegion`)
- Modify: `src/fledermap/web/api/geojson.py` (`recordings_geojson`)
- Modify: `src/fledermap/web/views/map.py` (`_render_recording_panel`)
- Test: `tests/test_map_query.py`, `tests/test_web_params.py` (find the existing params test file with `grep -ln parse_taxon_filter tests/`)

**Interfaces:**
- Consumes (Task 2): `RecordingRegion`, `Region`.
- Produces:
  - `parse_region_filter(raw: str | None) -> str | None` (absent/empty → None; otherwise the opaque id unchanged)
  - `filtered_recordings(..., region_id: str | None = None)` — keyword argument

- [ ] **Step 1: Write the failing tests**

In the params test file:

```python
from fledermap.web.params import parse_region_filter


def test_parse_region_filter_treats_absent_and_empty_as_none() -> None:
    assert parse_region_filter(None) is None
    assert parse_region_filter("") is None


def test_parse_region_filter_passes_the_id_through_unchanged() -> None:
    assert parse_region_filter("europe/germany/bremen") == "europe/germany/bremen"
```

In `tests/test_map_query.py` (uses its existing `_recording` helper and `engine` fixture; add `RecordingRegion, Region` to the models import):

```python
def _region(session: OrmSession, region_id: str, name: str) -> None:
    session.add(Region(id=region_id, name=name, is_distinct=True))
    session.flush()


def test_region_filter_includes_sub_region_recordings(engine: Engine) -> None:
    with OrmSession(engine) as session:
        _region(session, "bremen", "Bremen")
        _region(session, "germany", "Germany")
        in_bremen = _recording(session, audio_hash="a" * 64)
        in_germany_only = _recording(session, audio_hash="b" * 64)
        session.add_all(
            [
                RecordingRegion(recording_id=in_bremen.id, region_id="bremen"),
                RecordingRegion(recording_id=in_bremen.id, region_id="germany"),
                RecordingRegion(recording_id=in_germany_only.id, region_id="germany"),
            ]
        )
        session.flush()

        germany = filtered_recordings(session, region_id="germany")
        bremen = filtered_recordings(session, region_id="bremen")

        assert {r.audio_hash for r in germany} == {"a" * 64, "b" * 64}
        assert {r.audio_hash for r in bremen} == {"a" * 64}


def test_region_filter_with_an_unknown_region_matches_nothing(engine: Engine) -> None:
    with OrmSession(engine) as session:
        _recording(session, audio_hash="c" * 64)
        assert filtered_recordings(session, region_id="nonexistent") == []


def test_no_region_filter_returns_recordings_with_no_region(engine: Engine) -> None:
    with OrmSession(engine) as session:
        _recording(session, audio_hash="d" * 64)
        assert len(filtered_recordings(session)) == 1
```

Also add one route-level test for the unknown-region case in whichever test file covers `/api/recordings.geojson` (`grep -ln "recordings.geojson" tests/`): `client.get("/api/recordings.geojson?region=nonexistent")` returns 200 with `features == []`. Follow that file's existing app/client setup.

- [ ] **Step 2: Run to verify failure**

Run (unsandboxed): `hatch test tests/test_map_query.py -k region` and the params test.
Expected: FAIL (`parse_region_filter` missing; `filtered_recordings` has no `region_id`).

- [ ] **Step 3: Implement**

`web/params.py`, after `parse_taxon_filter`:

```python
def parse_region_filter(raw: str | None) -> str | None:
    """The `region` query param: a poiidx region id, opaque to us. Same
    absent/empty-is-None convention as `parse_int`. Never raises -- an id
    nothing is stored under simply matches no recordings."""
    return raw or None
```

`services/map_query.py`: import `RecordingRegion` from `fledermap.store.models`; add `region_id: str | None = None,` to the keyword list (after `site_id`); and after the `site_id` clause:

```python
    if region_id is not None:
        stmt = stmt.where(
            Recording.id.in_(
                select(RecordingRegion.recording_id).where(
                    RecordingRegion.region_id == region_id,
                ),
            ),
        )
```

`web/api/geojson.py`: import `parse_region_filter`; in `recordings_geojson` add `region_id = parse_region_filter(flask.request.args.get("region"))` after `site_id`, and `region_id=region_id,` in the `filtered_recordings(...)` call.

`web/views/map.py`: same two edits in `_render_recording_panel` (the panel's prev/next neighbours must come from the same filtered set as the map, so a region filter has to apply here too).

- [ ] **Step 4: Run to verify pass**

Run (unsandboxed): `hatch test tests/test_map_query.py tests/test_web_params.py` (use the actual params test filename) plus the geojson test file.
Expected: PASS.

- [ ] **Step 5: Lint, types, commit**

Run: `hatch fmt` then `hatch run types:check`

```bash
git add src/fledermap/web/params.py src/fledermap/services/map_query.py src/fledermap/web/api/geojson.py src/fledermap/web/views/map.py tests/
git commit -m "feat: filter recordings by region on the map's query layer

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

(`git add tests/` is acceptable here only because Step 1 touched exactly the test files named above; run `git status --short` first and stage explicit paths instead if anything else is modified.)

---

### Task 6: Region dropdown in the map UI

**Files:**
- Modify: `src/fledermap/web/app.py` (new `regions_enabled` parameter and `app.config["REGIONS_ENABLED"]`)
- Modify: `src/fledermap/cli/main.py` (`create_app(...)` call in `serve`, around line 482)
- Modify: `src/fledermap/web/views/map.py` (`map_page` context)
- Modify: `src/fledermap/web/templates/map.html` (after the `.taxon-group` span, before `<input type="hidden" name="session"`)
- Modify: `src/fledermap/web/static/app.js` (`filterForm()`)
- Test: `tests/test_map_view_regions.py` (new)

**Interfaces:**
- Consumes (Task 3): `regions.list_distinct_regions(session) -> Sequence[Region]`.
- Produces: `create_app(..., regions_enabled: bool = False)` (new LAST keyword parameter, after `longitude`); template variable `regions` (empty when disabled).

- [ ] **Step 1: Write the failing view tests**

`tests/test_map_view_regions.py` (copy the `_vendor_icons` usage and client setup from `tests/test_entities_view.py`):

```python
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session as OrmSession

from fledermap.store.models import Region
from fledermap.web.app import create_app

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("_vendor_icons")]


def _seed(engine: Engine, *, distinct: bool = True) -> None:
    with OrmSession(engine) as session:
        session.add(Region(id="germany", name="Germany", is_distinct=distinct))
        session.add(Region(id="bremen", name="Bremen", is_distinct=False))
        session.commit()


def _page(engine: Engine, tmp_path: Path, *, enabled: bool) -> str:
    app = create_app(
        engine, tmp_path / "static", tmp_path / "media", regions_enabled=enabled
    )
    return app.test_client().get("/").get_data(as_text=True)


def test_region_dropdown_lists_only_distinct_regions(
    engine: Engine, tmp_path: Path
) -> None:
    _seed(engine)
    html = _page(engine, tmp_path, enabled=True)
    assert 'name="region"' in html
    assert '<option value="germany">Germany</option>' in html
    assert "Bremen" not in html


def test_region_dropdown_sits_after_the_taxon_group_and_before_source(
    engine: Engine, tmp_path: Path
) -> None:
    _seed(engine)
    html = _page(engine, tmp_path, enabled=True)
    assert html.index('id="taxon-exclude"') < html.index('name="region"')
    assert html.index('name="region"') < html.index('name="source"')


def test_region_dropdown_is_omitted_when_poiidx_is_not_configured(
    engine: Engine, tmp_path: Path
) -> None:
    _seed(engine)
    assert 'name="region"' not in _page(engine, tmp_path, enabled=False)


def test_region_dropdown_is_omitted_when_no_region_is_stored(
    engine: Engine, tmp_path: Path
) -> None:
    assert 'name="region"' not in _page(engine, tmp_path, enabled=True)


def test_region_dropdown_is_omitted_when_no_region_is_distinct(
    engine: Engine, tmp_path: Path
) -> None:
    _seed(engine, distinct=False)
    assert 'name="region"' not in _page(engine, tmp_path, enabled=True)
```

- [ ] **Step 2: Run to verify failure**

Run (unsandboxed): `hatch test tests/test_map_view_regions.py`
Expected: FAIL with `TypeError: create_app() got an unexpected keyword argument 'regions_enabled'`.

- [ ] **Step 3: Wire `regions_enabled` through the app**

`web/app.py`: add `regions_enabled: bool = False,` as the last parameter of `create_app` (after `longitude`), add to its docstring: "`regions_enabled` is `Config.poiidx_database_url is not None` -- whether the map shows the Region filter; the web process never talks to poiidx itself, only reads the regions the worker stored.", and set `app.config["REGIONS_ENABLED"] = regions_enabled` beside `COMBINE_EMT_DISPLAY`.

`cli/main.py`, `serve`'s `create_app(...)` call: append `config.poiidx_database_url is not None,` after `config.longitude,`.

`web/views/map.py`: import `from fledermap.services.regions import list_distinct_regions`, and in `map_page` add to the `render_template` call:

```python
            regions=(
                list_distinct_regions(session)
                if flask.current_app.config["REGIONS_ENABLED"]
                else []
            ),
```

- [ ] **Step 4: Template and JS**

`map.html`, immediately after the closing `</span>` of `.taxon-group` and before `<input type="hidden" name="session" ...>`:

```jinja
      {% if regions %}
      <label>Region
        <select name="region" x-model="region">
          <option value="">Any</option>
          {% for region in regions %}
          <option value="{{ region.id }}">{{ region.name }}</option>
          {% endfor %}
        </select>
      </label>
      {% endif %}
```

`app.js`, in `filterForm()`'s returned object, after `taxon_exclude`:

```javascript
    region: params.get("region") || "",
```

(`query()` already serialises every named form field via `FormData`, so no other JS change is needed.)

- [ ] **Step 5: Run to verify pass**

Run (unsandboxed): `hatch test tests/test_map_view_regions.py`
Expected: PASS. Then `hatch test -m "not db"` and `node --test tests/js/` to confirm nothing else moved.

- [ ] **Step 6: Live verification in headless Chrome (mandatory: JS changed)**

Read the `reference-headless-chrome-live-verification-technique` memory and follow it against a running instance with at least two seeded regions and recordings: (a) the Region select renders between the Taxon group and Source, (b) choosing a region updates the URL with `region=<id>` and the marker count matches a direct DB count, (c) choosing `Any` clears it, (d) reloading with `?region=<id>` preselects the option. Report what was run and observed. If no instance with real region data exists yet, say so rather than claiming this step done.

- [ ] **Step 7: Lint, types, commit**

Run: `hatch fmt` then `hatch run types:check`

```bash
git add src/fledermap/web/app.py src/fledermap/cli/main.py src/fledermap/web/views/map.py src/fledermap/web/templates/map.html src/fledermap/web/static/app.js tests/test_map_view_regions.py
git commit -m "feat: add the Region dropdown to the map's filter bar

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Docs, full verification and live check

**Files:**
- Modify: `CLAUDE.md` (Architecture: `services/` list; Database: one bullet)
- Modify: `docs/reference/configuration.md` (the `FLEDERMAP_POIIDX_DATABASE_URL` entry)
- Modify: `docs/superpowers/specs/2026-10-04-fledermap-region-filter-design.md` (`**Status:** draft` → `implemented`)

- [ ] **Step 1: Update docs**

- `CLAUDE.md` Architecture, in the `services/` bullet, add: "`regions.py` resolves which poiidx regions contain each recording (worker-side, cached per rounded location in `location_region`, fanned out to `recording_region`) and recomputes `region.is_distinct`; the web process only reads those tables."
- `CLAUDE.md` Database section, add a bullet: "**`region.is_distinct` is derived, recomputed every ingest cycle** (`services/regions.py`'s `refresh_distinct_flags`), because poiidx's `distinct` is defined over the whole set of recorded locations and changes as recordings are added or go missing. Never treat it as a stored fact. `fledermap backfill-regions --force` clears all region tables and re-resolves."
- `docs/reference/configuration.md`: under `FLEDERMAP_POIIDX_DATABASE_URL`, add that it also enables the map's Region filter (worker resolves regions; the dropdown appears once at least one distinct region is stored).
- Spec status line → `implemented`.

- [ ] **Step 2: Full suite (controlling session only)**

Run (unsandboxed, background or accept ~120 s): `hatch test`
Expected: all pass including `db`-marked, no warnings. Also: `hatch run types:check`, `hatch fmt`, `node --test tests/js/`.

- [ ] **Step 3: Live check against real data (needs Janna)**

Ask Janna before touching the real `bats_db`: run `scripts/db-backup.sh` first, then start/restart the worker with `FLEDERMAP_POIIDX_DATABASE_URL` set, confirm in the logs that poiidx's schema hash did not trigger a rebuild of `poiidx_bats_db` (the spec predicts it won't), then confirm `region` is populated, the dropdown shows plausible regions, and a selected region's marker count matches a manual count.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md docs/reference/configuration.md docs/superpowers/specs/2026-10-04-fledermap-region-filter-design.md
git commit -m "docs: document the region filter

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```
