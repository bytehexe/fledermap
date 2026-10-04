# IUCN Range Plausibility Check Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show, per species row of the Identifications matrix, whether the recording lies inside, near, or far outside that species' IUCN range (or that there is no data), in a new `iucn` column, behind an off-by-default flag.

**Architecture:** A worker job lazily fetches per-species range polygons from the UNEP-WCMC-hosted IUCN layer into two new PostGIS-backed tables (`species_range`, `species_range_fetch`). The web process never calls out: at render time one indexed distance query per species row yields a `RangeStatus`, which `attach_ranges` hangs onto the existing `IdentificationMatrix`, and the shared `_identification_matrix.html` renders as one extra column. Range geometry never leaves the server.

**Tech Stack:** Python 3.12, SQLAlchemy 2 + GeoAlchemy2 (PostGIS geography), Alembic, Procrastinate, `requests`, `shapely`, Flask/Jinja, `hatch`.

**Spec:** `docs/superpowers/specs/2026-10-04-fledermap-iucn-range-check-design.md` (read it first; it carries the licensing reasoning).

## Global Constraints

- Flag `iucn_ranges_enabled` / `FLEDERMAP_IUCN_RANGES_ENABLED` is **off by default**; `iucn_range_edge_km` / `FLEDERMAP_IUCN_RANGE_EDGE_KM` defaults to `100.0` and must be `> 0`.
- **Display-only.** Nothing in this plan may touch `current_best`, a verdict, `claim_key`, or any review queue.
- **Range geometry never reaches the browser**: no endpoint, template variable, JSON, or API response may carry a polygon. Only `RangeKind`, a rounded distance and a tooltip string.
- **No real IUCN data is ever committed** (IUCN T&C §4). Test fixtures are hand-written and synthetic. Never save a live service response into `tests/` or anywhere in the repo.
- Source key for names is the string `iucn2022` (`RANGE_CODE_SOURCE`); release label is `2022-2`; citation text is `IUCN Red List, release 2022-2 (non-commercial use only)`.
- Polygons kept at fetch: `presence IN (1, 2, 3, 6)`. Every `origin` counts, except a point lying only in an `origin = 4` (vagrant) polygon is **near**, never inside. `seasonal` is ignored entirely (not fetched, not stored).
- One edge threshold: distance `0` → inside, `0 < d <= edge_km` → near, `d > edge_km` → far.
- No fallback from a missing `iucn2022` code to `scientific_name`; absent code means "no data".
- Column header is lowercase `iucn`. Icons: `circle-check` (inside, `--color-success`), `alert-triangle` (near, `--color-warning`), `ban` (far, `--color-danger`), `help-circle` (no data, `--color-muted`). Blank cell = not applicable (non-species / unmapped / NoID rows). No check-mark icon in this column.
- All Python goes through `hatch` (`hatch test`, `hatch fmt`, `hatch run types:check`); never bare `python`/`pip`, never `PYTHONPATH`. `db`-marked tests need `dangerouslyDisableSandbox: true` and are run by the **controlling session**, never a subagent (~120 s container start).
- Run `git` unsandboxed. Stage explicit paths only, never `git add -A`/`git add .`. Commit messages end with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
- Comments explain *why* (rejected alternative, invariant, outside constraint). Never cite a plan task number, review round, or conversation quote in a code comment; cite the spec by path instead.
- Test output must be pristine (a warning is a defect; never add a `filterwarnings` ignore). Test code must type-check under `hatch run types:check`.
- Before running the new migration against the live `bats_db` (deployment, not part of this plan's tests): `scripts/db-backup.sh`.

## Review Focus

Inputs and conditions the spec implies that no happy-path test would hit, most likely first. Each has a pinning test in the task named.

1. **ArcGIS error returned as HTTP 200** (`{"error": {...}}`) must raise, never be read as "no features" — otherwise a transient failure is cached forever as `not_found`. (Task 4)
2. **Truncated response** (`exceededTransferLimit`) must raise rather than store a partial range. (Task 4)
3. **A species whose only polygons are vagrant** (`origin = 4`): a point inside is `NEAR` (vagrant), a point far away is `FAR`, never `INSIDE`/crash on a `None` native distance. (Task 6)
4. **Flag turned off after data was fetched**: the column disappears, no job is queued, cached rows stay. (Tasks 7, 9)
5. **Species the service doesn't know, and a changed `iucn2022` code**: `not_found` is never re-fetched; a changed code is fetched again. (Task 5)
6. **A new code source leaking into existing readers** (species list "code" column, species page "Codes:", Classify-box search index). (Task 3)

---

### Task 1: Config settings

**Files:**
- Modify: `src/fledermap/config.py` (ENV constants near line 62, `_KNOWN_FILE_KEYS` near line 97, `Config` fields near line 344, parsing before `rtc_enabled_raw`, `cls(...)` call near line 1020)
- Modify: `docs/reference/configuration.md` (settings table + notes)
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `Config.iucn_ranges_enabled: bool` (default `False`), `Config.iucn_range_edge_km: float` (default `100.0`, `> 0`); env constants `ENV_IUCN_RANGES_ENABLED`, `ENV_IUCN_RANGE_EDGE_KM`.

- [ ] **Step 1: Write the failing tests**

Add `ENV_IUCN_RANGES_ENABLED` and `ENV_IUCN_RANGE_EDGE_KM` to the existing `from fledermap.config import (...)` block in `tests/test_config.py`, then append:

```python
def _base_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_MEDIA_ROOT, str(tmp_path / "media"))
    monkeypatch.delenv(ENV_IUCN_RANGES_ENABLED, raising=False)
    monkeypatch.delenv(ENV_IUCN_RANGE_EDGE_KM, raising=False)


def test_iucn_ranges_default_to_disabled_with_a_100_km_edge(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _base_env(monkeypatch, tmp_path)
    config = Config.from_env()
    assert config.iucn_ranges_enabled is False
    assert config.iucn_range_edge_km == 100.0


def test_iucn_ranges_settings_are_configurable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _base_env(monkeypatch, tmp_path)
    monkeypatch.setenv(ENV_IUCN_RANGES_ENABLED, "true")
    monkeypatch.setenv(ENV_IUCN_RANGE_EDGE_KM, "250.5")
    config = Config.from_env()
    assert config.iucn_ranges_enabled is True
    assert config.iucn_range_edge_km == 250.5


def test_invalid_iucn_ranges_enabled_raises_config_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _base_env(monkeypatch, tmp_path)
    monkeypatch.setenv(ENV_IUCN_RANGES_ENABLED, "maybe")
    with pytest.raises(ConfigError, match="not a boolean"):
        Config.from_env()


@pytest.mark.parametrize("value", ["0", "-5", "abc", "inf", "nan"])
def test_invalid_iucn_range_edge_km_raises_config_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    value: str,
) -> None:
    _base_env(monkeypatch, tmp_path)
    monkeypatch.setenv(ENV_IUCN_RANGE_EDGE_KM, value)
    with pytest.raises(ConfigError, match="FLEDERMAP_IUCN_RANGE_EDGE_KM"):
        Config.from_env()
```

- [ ] **Step 2: Run to verify failure**

Run: `hatch test tests/test_config.py -k iucn`
Expected: FAIL (`ImportError: cannot import name 'ENV_IUCN_RANGES_ENABLED'`).

- [ ] **Step 3: Implement**

In `src/fledermap/config.py`:

(a) After `ENV_RTC_ENABLED = ...` add:
```python
ENV_IUCN_RANGES_ENABLED = "FLEDERMAP_IUCN_RANGES_ENABLED"
ENV_IUCN_RANGE_EDGE_KM = "FLEDERMAP_IUCN_RANGE_EDGE_KM"
```
(b) In `_KNOWN_FILE_KEYS` add `"iucn_ranges_enabled",` and `"iucn_range_edge_km",` (after `"combine_emt_display",`).
(c) In the `Config` dataclass, directly after the line `    combine_emt_display: bool = True`, add:
```python
    # Off by default: IUCN Red List data is licensed for non-commercial use only and may not be
    # redistributed (T&C v3.1 §§3-4), and enabling this makes the worker fetch range polygons
    # from a third-party server. See docs/superpowers/specs/2026-10-04-fledermap-iucn-range-check-
    # design.md "Licensing". Enabling it is the operator's own acceptance of those terms.
    iucn_ranges_enabled: bool = False
    # A recording farther than this from a species' IUCN range counts as "far" (the forbid
    # icon); between 0 and this it is "near". IUCN ranges are generalised and exclude vagrants
    # and recent range expansions, so a plain outside/inside split would mislabel real records.
    iucn_range_edge_km: float = 100.0
```
(d) Immediately before the line `        rtc_enabled_raw = _lookup(ENV_RTC_ENABLED, "rtc_enabled", file_values)` insert:
```python
        iucn_ranges_enabled_raw = _lookup(
            ENV_IUCN_RANGES_ENABLED,
            "iucn_ranges_enabled",
            file_values,
        )
        if iucn_ranges_enabled_raw is None:
            iucn_ranges_enabled = False
        elif isinstance(iucn_ranges_enabled_raw, bool):
            iucn_ranges_enabled = iucn_ranges_enabled_raw
        else:
            label = _source_label(
                ENV_IUCN_RANGES_ENABLED,
                "iucn_ranges_enabled",
                config_path,
            )
            text = _as_str(iucn_ranges_enabled_raw, label).strip().lower()
            if text in ("true", "1", "yes"):
                iucn_ranges_enabled = True
            elif text in ("false", "0", "no"):
                iucn_ranges_enabled = False
            else:
                msg = f"{label}={iucn_ranges_enabled_raw!r} is not a boolean (true/false)."
                raise ConfigError(msg)

        iucn_range_edge_km_raw = _lookup(
            ENV_IUCN_RANGE_EDGE_KM,
            "iucn_range_edge_km",
            file_values,
        )
        if iucn_range_edge_km_raw is None:
            iucn_range_edge_km = 100.0
        else:
            label = _source_label(
                ENV_IUCN_RANGE_EDGE_KM,
                "iucn_range_edge_km",
                config_path,
            )
            if isinstance(iucn_range_edge_km_raw, bool):  # see session_gap_hours above
                msg = f"{label}={iucn_range_edge_km_raw!r} is not a number."
                raise ConfigError(msg)
            try:
                iucn_range_edge_km = float(iucn_range_edge_km_raw)
            except (TypeError, ValueError) as exc:
                msg = f"{label}={iucn_range_edge_km_raw!r} is not a number."
                raise ConfigError(msg) from exc
            # Written as a chained comparison so NaN fails it too (NaN > 0 is False).
            if not 0.0 < iucn_range_edge_km < float("inf"):
                msg = f"{label}={iucn_range_edge_km_raw!r} must be a finite number above 0."
                raise ConfigError(msg)

```
(e) In the `return cls(` call, after the line `            combine_emt_display=combine_emt_display,` add:
```python
            iucn_ranges_enabled=iucn_ranges_enabled,
            iucn_range_edge_km=iucn_range_edge_km,
```

- [ ] **Step 4: Document the settings**

In `docs/reference/configuration.md`, after the `Combine EMT sources in the UI` table row add:
```
| IUCN range check enabled | `iucn_ranges_enabled` | `FLEDERMAP_IUCN_RANGES_ENABLED` | no | `false` |
| IUCN range "near" distance (km) | `iucn_range_edge_km` | `FLEDERMAP_IUCN_RANGE_EDGE_KM` | no | `100.0` |
```
and append to the "Notes on individual settings" list:
```
- **`iucn_ranges_enabled`** — adds an `iucn` column to the Identifications table showing
  whether the recording lies inside, near or far outside each species' IUCN Red List range.
  **Non-commercial use only**: the IUCN Red List data may not be used by or for a for-profit
  entity or to generate revenue, and may not be redistributed (IUCN Terms and Conditions of Use
  v3.1, §§3–4). Enabling it makes the worker fetch range polygons from the UNEP-WCMC-hosted IUCN
  layer (release 2022-2, which IUCN has since superseded, §7) and cache them in your own
  database; the polygons are never shown or sent to the browser. Enabling this is your
  acceptance of those terms. The rights are personal to you or your household (§1) — ask
  redlist@iucn.org before opening an instance to other people.
- **`iucn_range_edge_km`** — a recording this far or less outside a species' range is shown as
  "near" (warning icon); farther is "far" (forbid icon). IUCN ranges are generalised and omit
  vagrants and recent expansions, so "outside" alone is not the same as "implausible".
```

- [ ] **Step 5: Run tests**

Run: `hatch test tests/test_config.py tests/test_configuration_docs.py -m "not db"`
Expected: PASS (including `test_every_known_config_file_key_is_documented_in_configuration_md`).

- [ ] **Step 6: Commit**

```bash
git add src/fledermap/config.py docs/reference/configuration.md tests/test_config.py
git commit -m "feat: add IUCN range check config settings"
```

---

### Task 2: Domain vocabulary, models and migration

**Files:**
- Create: `src/fledermap/domain/ranges.py`
- Modify: `src/fledermap/store/models.py` (import `SmallInteger`; add two classes at the end of the region models)
- Create: `src/fledermap/alembic/versions/b7e2d94a1c35_add_species_range_tables.py`
- Test: `tests/test_ranges_domain.py` (no DB), `tests/test_species_range_models.py` (db), `tests/test_migrations.py` (append)

**Interfaces:**
- Produces (domain): `RANGE_CODE_SOURCE: str`, `IUCN_RELEASE: str`, `IUCN_CITATION: str`, `VAGRANT_ORIGIN: int`, `KEPT_PRESENCE: tuple[int, ...]`, `FETCH_FOUND: str`, `FETCH_NOT_FOUND: str`, `class RangeKind(StrEnum)` (`INSIDE`, `NEAR`, `FAR`, `NO_DATA`), `class NoDataReason(StrEnum)` (`NO_LOCATION`, `NO_CODE`, `NOT_FOUND`, `NOT_FETCHED`), `@dataclass(frozen=True) class RangeStatus(kind: RangeKind, distance_km: float | None = None, reason: NoDataReason | None = None, vagrant: bool = False)` with `.describe() -> str`.
- Produces (models): `SpeciesRange(id, taxon_id, presence, origin, geom)`, `SpeciesRangeFetch(taxon_id, query_name, status, source_version, fetched_at)`.

- [ ] **Step 1: Write the failing domain tests** — `tests/test_ranges_domain.py`

```python
from __future__ import annotations

from fledermap.domain.ranges import NoDataReason, RangeKind, RangeStatus


def test_inside_describes_itself() -> None:
    assert RangeStatus(RangeKind.INSIDE).describe() == "Inside the IUCN range"


def test_near_and_far_state_the_rounded_distance() -> None:
    assert (
        RangeStatus(RangeKind.NEAR, distance_km=87.4).describe()
        == "87 km outside the IUCN range"
    )
    assert (
        RangeStatus(RangeKind.FAR, distance_km=640.6).describe()
        == "641 km outside the IUCN range"
    )


def test_a_sub_kilometre_distance_is_not_shown_as_zero() -> None:
    assert (
        RangeStatus(RangeKind.NEAR, distance_km=0.3).describe()
        == "Less than 1 km outside the IUCN range"
    )


def test_vagrant_only_area_is_described_as_such() -> None:
    status = RangeStatus(RangeKind.NEAR, distance_km=0.0, vagrant=True)
    assert status.describe() == "Only inside an IUCN vagrant-records area"
    far_from_regular = RangeStatus(RangeKind.NEAR, distance_km=212.0, vagrant=True)
    assert far_from_regular.describe() == (
        "Only inside an IUCN vagrant-records area (212 km outside the regular range)"
    )


def test_every_no_data_reason_has_its_own_text() -> None:
    texts = {
        reason: RangeStatus(RangeKind.NO_DATA, reason=reason).describe()
        for reason in NoDataReason
    }
    assert len(set(texts.values())) == len(NoDataReason)
    assert "no location" in texts[NoDataReason.NO_LOCATION].lower()
    assert "not fetched" in texts[NoDataReason.NOT_FETCHED].lower()
```

- [ ] **Step 2: Run to verify failure**

Run: `hatch test tests/test_ranges_domain.py`
Expected: FAIL (`ModuleNotFoundError: fledermap.domain.ranges`).

- [ ] **Step 3: Implement the domain module** — `src/fledermap/domain/ranges.py`

```python
"""Vocabulary for the IUCN range plausibility check (design spec
2026-10-04-fledermap-iucn-range-check-design.md). No I/O, no SQLAlchemy, so
`services/current_best.py` can type a matrix row's range without importing the
service module (which imports the job queue)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

# The `taxon_code.source` under which a taxon's name in the IUCN 2022-2 layer is stored.
# Deliberately not an `IdSource`: nothing identifies a recording under it, and a CHECK/enum
# there would force every classifier-facing consumer to know about it.
RANGE_CODE_SOURCE = "iucn2022"
IUCN_RELEASE = "2022-2"
# T&C v3.1 §6 requires the citation to name the data version; §3 the non-commercial restriction.
IUCN_CITATION = f"IUCN Red List, release {IUCN_RELEASE} (non-commercial use only)"

# IUCN `presence` codes kept at fetch: 1 extant, 2 probably extant, 3 possibly extant,
# 6 presence uncertain. 4 (possibly extinct) and 5 (extinct) say nothing about where a living
# bat could be. Verified against the layer's own `legend` field.
KEPT_PRESENCE: tuple[int, ...] = (1, 2, 3, 6)
# IUCN `origin` 4: a polygon of vagrant records, not a regular range.
VAGRANT_ORIGIN = 4

FETCH_FOUND = "found"
FETCH_NOT_FOUND = "not_found"


class RangeKind(StrEnum):
    INSIDE = "inside"
    NEAR = "near"
    FAR = "far"
    NO_DATA = "no_data"


class NoDataReason(StrEnum):
    NO_LOCATION = "no_location"
    NO_CODE = "no_code"
    NOT_FOUND = "not_found"
    NOT_FETCHED = "not_fetched"


_NO_DATA_TEXT: dict[NoDataReason, str] = {
    NoDataReason.NO_LOCATION: "No range check: the recording has no location",
    NoDataReason.NO_CODE: "No range check: species not matched to an IUCN name",
    NoDataReason.NOT_FOUND: "IUCN has no range for this species",
    NoDataReason.NOT_FETCHED: "IUCN range not fetched yet",
}


def _km_text(distance_km: float) -> str:
    if distance_km < 1:
        return "less than 1 km"
    return f"{round(distance_km)} km"


@dataclass(frozen=True)
class RangeStatus:
    """The browser-safe reduction of a species' range polygons at one recording's location: a
    verdict, a distance, and a tooltip. Never carries geometry (IUCN T&C §4)."""

    kind: RangeKind
    distance_km: float | None = None
    reason: NoDataReason | None = None
    # True when the point lies only inside an `origin == 4` polygon.
    vagrant: bool = False

    def describe(self) -> str:
        if self.kind is RangeKind.INSIDE:
            return "Inside the IUCN range"
        if self.kind is RangeKind.NO_DATA:
            if self.reason is None:
                return "No IUCN range data"
            return _NO_DATA_TEXT[self.reason]
        if self.vagrant:
            base = "Only inside an IUCN vagrant-records area"
            if self.distance_km is not None and self.distance_km >= 1:
                return f"{base} ({_km_text(self.distance_km)} outside the regular range)"
            return base
        text = f"{_km_text(self.distance_km or 0.0)} outside the IUCN range"
        return text[0].upper() + text[1:]
```

- [ ] **Step 4: Run domain tests**

Run: `hatch test tests/test_ranges_domain.py`
Expected: PASS.

- [ ] **Step 5: Write the failing model tests** — `tests/test_species_range_models.py`

```python
from __future__ import annotations

import pytest
from geoalchemy2.elements import WKTElement
from sqlalchemy import Engine, delete, func, select
from sqlalchemy.orm import Session as OrmSession

from fledermap.store.models import SpeciesRange, SpeciesRangeFetch, Taxon

pytestmark = pytest.mark.db

SQUARE = "MULTIPOLYGON(((0 0, 0 1, 1 1, 1 0, 0 0)))"


def _taxon(session: OrmSession) -> Taxon:
    taxon = Taxon(rank="species", scientific_name="Pipistrellus pipistrellus")
    session.add(taxon)
    session.flush()
    return taxon


def test_species_range_round_trips(engine: Engine) -> None:
    with OrmSession(engine) as session:
        taxon = _taxon(session)
        session.add(
            SpeciesRange(
                taxon_id=taxon.id,
                presence=1,
                origin=1,
                geom=WKTElement(SQUARE, srid=4326),
            ),
        )
        session.commit()
        row = session.scalars(select(SpeciesRange)).one()
        assert (row.presence, row.origin) == (1, 1)
        area = session.scalar(select(func.ST_Area(SpeciesRange.geom)))
        assert area is not None
        assert area > 0


def test_deleting_a_taxon_cascades_to_its_ranges_and_fetch_row(
    engine: Engine,
) -> None:
    from datetime import UTC, datetime

    with OrmSession(engine) as session:
        taxon = _taxon(session)
        session.add(
            SpeciesRange(
                taxon_id=taxon.id,
                presence=1,
                origin=1,
                geom=WKTElement(SQUARE, srid=4326),
            ),
        )
        session.add(
            SpeciesRangeFetch(
                taxon_id=taxon.id,
                query_name="Pipistrellus pipistrellus",
                status="found",
                source_version="2022-2",
                fetched_at=datetime(2026, 10, 4, tzinfo=UTC),
            ),
        )
        session.commit()

        session.execute(delete(Taxon))
        session.commit()

        assert session.scalars(select(SpeciesRange)).all() == []
        assert session.scalars(select(SpeciesRangeFetch)).all() == []
```

- [ ] **Step 6: Run to verify failure**

Run (unsandboxed): `hatch test tests/test_species_range_models.py -m db`
Expected: FAIL (`ImportError: cannot import name 'SpeciesRange'`).

- [ ] **Step 7: Add the models** — in `src/fledermap/store/models.py`

Add `SmallInteger,` to the `from sqlalchemy import (...)` list (alphabetically, between `Integer,` and `String,`). After the `RecordingRegion` class append:

```python
class SpeciesRange(Base):
    """One simplified IUCN range polygon for a taxon (design spec 2026-10-04-fledermap-iucn-
    range-check-design.md). IUCN data under non-commercial, no-redistribution terms: rows are a
    private cache and are never serialised to a browser. `seasonal` is deliberately absent --
    IUCN files every bat in our species lists as resident, so there is nothing to compare a
    recording date against."""

    __tablename__ = "species_range"

    id: Mapped[int] = mapped_column(primary_key=True)
    taxon_id: Mapped[int] = mapped_column(
        ForeignKey("taxon.id", ondelete="CASCADE"),
        index=True,
    )
    presence: Mapped[int] = mapped_column(SmallInteger)
    origin: Mapped[int] = mapped_column(SmallInteger)
    geom: Mapped[object] = mapped_column(
        Geography(geometry_type="MULTIPOLYGON", srid=4326),
    )


class SpeciesRangeFetch(Base):
    """That the range of one taxon was asked for, and what came back. Without this a species
    the service does not know would be re-queried every cycle -- the same trap as the
    classifier NoID marker row. `query_name` records which `iucn2022` code was asked, so a
    changed code makes the taxon pending again."""

    __tablename__ = "species_range_fetch"

    taxon_id: Mapped[int] = mapped_column(
        ForeignKey("taxon.id", ondelete="CASCADE"),
        primary_key=True,
    )
    query_name: Mapped[str] = mapped_column(String(100))
    # "found" | "not_found" (domain/ranges.py FETCH_*). Plain string, not an Enum+CHECK: a
    # closed two-value vocabulary private to one service does not earn a drift-test blind spot.
    status: Mapped[str] = mapped_column(String(16))
    source_version: Mapped[str] = mapped_column(String(16))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
```

- [ ] **Step 8: Write the migration** — `src/fledermap/alembic/versions/b7e2d94a1c35_add_species_range_tables.py`

```python
"""add species range tables

Revision ID: b7e2d94a1c35
Revises: a1d4c7e9b2f6
"""

from __future__ import annotations

from collections.abc import Sequence

import geoalchemy2
import sqlalchemy as sa
from alembic import op

revision: str = "b7e2d94a1c35"
down_revision: str | Sequence[str] | None = "a1d4c7e9b2f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "species_range",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("taxon_id", sa.Integer(), nullable=False),
        sa.Column("presence", sa.SmallInteger(), nullable=False),
        sa.Column("origin", sa.SmallInteger(), nullable=False),
        sa.Column(
            "geom",
            geoalchemy2.types.Geography(
                geometry_type="MULTIPOLYGON",
                srid=4326,
                dimension=2,
                from_text="ST_GeogFromText",
                name="geography",
            ),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["taxon_id"], ["taxon.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    # No explicit spatial index: geoalchemy2 creates the GIST index itself on table creation
    # (see e9a0c0f92971), and an explicit one would collide with it.
    op.create_index(
        op.f("ix_species_range_taxon_id"),
        "species_range",
        ["taxon_id"],
        unique=False,
    )
    op.create_table(
        "species_range_fetch",
        sa.Column("taxon_id", sa.Integer(), nullable=False),
        sa.Column("query_name", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("source_version", sa.String(length=16), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["taxon_id"], ["taxon.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("taxon_id"),
    )


def downgrade() -> None:
    op.drop_table("species_range_fetch")
    op.drop_index(op.f("ix_species_range_taxon_id"), table_name="species_range")
    op.drop_table("species_range")
```

- [ ] **Step 9: Add the migration spatial-index test** — append to `tests/test_migrations.py`

```python
def test_migrated_species_range_has_a_spatial_index(migrated_engine: Engine) -> None:
    """`compare_metadata` does not see a missing GIST index on a geography column, and the
    distance query in `services/iucn_ranges.py` needs it once ranges are large."""
    with migrated_engine.connect() as conn:
        definitions = (
            conn.execute(
                text(
                    "SELECT indexdef FROM pg_indexes WHERE tablename = 'species_range'",
                ),
            )
            .scalars()
            .all()
        )
    assert any("gist" in d.lower() and "geom" in d for d in definitions), definitions
```

- [ ] **Step 10: Run the DB tests (controlling session, unsandboxed)**

Run: `hatch test tests/test_species_range_models.py tests/test_migrations.py -m db`
Expected: PASS, including `test_migration_matches_the_models` (no drift) and the new spatial-index test. If the spatial-index test fails because `op.create_table` did not create the GIST index, add `op.create_index("idx_species_range_geom", "species_range", ["geom"], postgresql_using="gist")` to `upgrade()` (and its drop to `downgrade()`) and re-run both this test and `test_migration_matches_the_models`.

- [ ] **Step 11: Format, type-check, commit**

Run: `hatch fmt` then `hatch run types:check`
Expected: clean.

```bash
git add src/fledermap/domain/ranges.py src/fledermap/store/models.py src/fledermap/alembic/versions/b7e2d94a1c35_add_species_range_tables.py tests/test_ranges_domain.py tests/test_species_range_models.py tests/test_migrations.py
git commit -m "feat: add species range tables and range vocabulary"
```

---

### Task 3: `iucn2022` codes in the taxa YAML, and the reader sweep

**Files:**
- Modify: `src/fledermap/store/data/taxa_eu.yaml`, `src/fledermap/store/data/taxa_na.yaml` (one `iucn2022` entry in every species' `codes` mapping; header comments)
- Modify: `src/fledermap/services/entities.py` (two readers), `src/fledermap/web/views/recording_detail.py` (`_taxon_search_index`)
- Create: `tests/test_iucn_codes.py`
- Test (append): `tests/test_entities_query.py`, `tests/test_recording_detail_view.py`

**Interfaces:**
- Consumes: `RANGE_CODE_SOURCE` from `fledermap.domain.ranges`.
- Produces: every species entry in `taxa_eu.yaml`/`taxa_na.yaml` has `codes.iucn2022`; no reader outside the range service sees that source.

**Reader sweep (done 2026-10-04, `grep -rn TaxonCode src`):** `services/entities.py` (`list_species` takes an arbitrary first code per species; `species_detail` lists every code on the species page), `web/views/recording_detail.py` (`_taxon_search_index` feeds every code to the Classify box search). `services/search.py`'s exact-match resolution and `store/seed.py` are intentionally unchanged (an `iucn2022` row names the same taxon, so it cannot change a search answer; seeding is the writer).

- [ ] **Step 1: Verify the three renamed species against the live service (manual, nothing saved to the repo)**

Run (needs `allowed_domains: ["data-gis.unep-wcmc.org"]` or an unsandboxed call):
```bash
for n in "Eptesicus serotinus" "Eptesicus isabellinus" "Eptesicus nilssonii"; do
  printf '%s: ' "$n"
  curl -sG 'https://data-gis.unep-wcmc.org/server/rest/services/Hosted/IUCN_Red_List/MapServer/0/query' \
    --data-urlencode "where=sci_name = '$n'" --data-urlencode 'returnCountOnly=true' \
    --data-urlencode 'f=json'; echo
done
```
Expected: each prints `{"count":N}` with `N >= 1`. **If any is 0, stop and report** which name IUCN 2022-2 uses for it (try `Cnephaeus ...`, `Eptesicus ...` synonyms with a `LIKE` query); do not guess. Do not save the output anywhere under the repo.

- [ ] **Step 2: Write the failing YAML tests** — `tests/test_iucn_codes.py`

```python
from __future__ import annotations

from importlib.resources import files
from typing import Any

import pytest
import yaml
from sqlalchemy import Engine
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.ranges import RANGE_CODE_SOURCE
from fledermap.store.seed import resolve_code, seed_taxonomy

# Where IUCN 2022-2's name differs from our `scientific_name` (current taxonomy). Explicit, so a
# typo in a code cannot hide behind a lenient "close enough" comparison.
RENAMED_IN_IUCN = {
    "Cnephaeus serotinus": "Eptesicus serotinus",
    "Cnephaeus isabellinus": "Eptesicus isabellinus",
    "Cnephaeus nilssonii": "Eptesicus nilssonii",
}


def _entries(name: str) -> list[dict[str, Any]]:
    raw = files("fledermap.store.data").joinpath(name).read_text(encoding="utf-8")
    entries: list[dict[str, Any]] = yaml.safe_load(raw)["taxa"]
    return entries


def _species() -> list[dict[str, Any]]:
    return [
        e
        for name in ("taxa_eu.yaml", "taxa_na.yaml")
        for e in _entries(name)
        if e["rank"] == "species"
    ]


def test_every_species_has_its_iucn2022_code() -> None:
    for entry in _species():
        name = entry["scientific_name"]
        expected = RENAMED_IN_IUCN.get(name, name)
        assert entry["codes"].get(RANGE_CODE_SOURCE) == expected, name


def test_iucn2022_codes_are_unique_across_species() -> None:
    codes = [e["codes"][RANGE_CODE_SOURCE] for e in _species()]
    assert len(codes) == len(set(codes))


def test_group_rank_taxa_carry_no_iucn2022_code() -> None:
    for entry in _entries("taxa_groups.yaml"):
        assert RANGE_CODE_SOURCE not in (entry.get("codes") or {}), entry[
            "scientific_name"
        ]


@pytest.mark.db
def test_seeding_stores_the_iucn2022_codes(engine: Engine) -> None:
    with OrmSession(engine) as session:
        seed_taxonomy(session)
        session.commit()

        taxon = resolve_code(session, RANGE_CODE_SOURCE, "Eptesicus serotinus")

    assert taxon is not None
    assert taxon.scientific_name == "Cnephaeus serotinus"
```

- [ ] **Step 3: Run to verify failure**

Run: `hatch test tests/test_iucn_codes.py -m "not db"`
Expected: FAIL (`KeyError`/assertion: no `iucn2022` codes yet).

- [ ] **Step 4: Add the codes to both YAML files**

Create the edit script in the scratchpad (not in the repo) and run it for both files:

```bash
S=/tmp/claude-1000/-home-janna-projekte-bats/60031245-5a3a-4dd1-ad7a-6005db4d0221/scratchpad
cat > $S/add_iucn.awk <<'AWK'
BEGIN {
  o["Cnephaeus serotinus"] = "Eptesicus serotinus"
  o["Cnephaeus isabellinus"] = "Eptesicus isabellinus"
  o["Cnephaeus nilssonii"] = "Eptesicus nilssonii"
}
/^  - scientific_name: / { name = $0; sub(/^  - scientific_name: /, "", name) }
/^    codes: \{/ && name != "" {
  q = (name in o) ? o[name] : name
  sub(/\}[ ]*$/, ", iucn2022: \"" q "\"}")
  name = ""
}
{ print }
AWK
cd src/fledermap/store/data
for f in taxa_eu.yaml taxa_na.yaml; do awk -f $S/add_iucn.awk $f > $S/$f.new && cp $S/$f.new $f; done
grep -c 'iucn2022' taxa_eu.yaml taxa_na.yaml
```
Expected counts sum to **71** (the species in both files). If not, inspect the lines the script missed (`grep -n -B1 'codes:' ... | grep -v iucn2022`) — a species with a multi-line or differently-formatted `codes:` needs a hand edit following the same `iucn2022: "<name>"` shape.

Then extend each file's header comment (before the `taxa:` line) with:
```yaml
#
# `iucn2022` codes are each species' name in the IUCN Red List 2022-2 range layer
# (https://data-gis.unep-wcmc.org/server/rest/services/Hosted/IUCN_Red_List/MapServer/0), used
# only by the optional range plausibility check. They equal `scientific_name` except where IUCN
# still files a species under its pre-2023 name (the three *Cnephaeus* species are *Eptesicus*
# there). Like every code here they are looked up by exact match, never derived from
# `scientific_name`; a species with no `iucn2022` code simply shows "no range data".
```

- [ ] **Step 5: Run the YAML tests**

Run: `hatch test tests/test_iucn_codes.py -m "not db"`
Expected: PASS.

- [ ] **Step 6: Write the failing reader tests**

Append to `tests/test_entities_query.py`:

```python
def test_list_species_code_column_ignores_the_iucn_range_name(engine: Engine) -> None:
    """The species list shows one arbitrary code per species; a range-lookup name is not
    something a user knows as a code."""
    with OrmSession(engine) as session:
        taxon = Taxon(rank="species", scientific_name="Cnephaeus serotinus")
        session.add(taxon)
        session.flush()
        # The IUCN row is inserted FIRST so a reader that does not filter would pick it.
        session.add(
            TaxonCode(source="iucn2022", code="Eptesicus serotinus", taxon_id=taxon.id),
        )
        session.add(TaxonCode(source="wa", code="EPTSER", taxon_id=taxon.id))
        _recording(session, audio_hash="b" * 64, taxon_id=taxon.id)
        session.commit()

        rows = list_species(session)

    assert rows[0].code == "EPTSER"


def test_species_detail_codes_exclude_the_iucn_range_name(engine: Engine) -> None:
    with OrmSession(engine) as session:
        taxon = Taxon(rank="species", scientific_name="Cnephaeus serotinus")
        session.add(taxon)
        session.flush()
        session.add(TaxonCode(source="wa", code="EPTSER", taxon_id=taxon.id))
        session.add(
            TaxonCode(source="iucn2022", code="Eptesicus serotinus", taxon_id=taxon.id),
        )
        session.commit()

        detail = species_detail(session, taxon.id)

    assert detail is not None
    assert detail.codes == ["EPTSER"]
```

Append to `tests/test_recording_detail_view.py` (the module already has `pytestmark = [db, _vendor_icons]`; add `TaxonCode` to its `from fledermap.store.models import ...` line and `from fledermap.web.views.recording_detail import _taxon_search_index` below the other imports):

```python
def test_classify_search_index_excludes_the_iucn_range_name(engine: Engine) -> None:
    with OrmSession(engine) as session:
        taxon = Taxon(rank="species", scientific_name="Cnephaeus serotinus")
        session.add(taxon)
        session.flush()
        session.add(TaxonCode(source="wa", code="EPTSER", taxon_id=taxon.id))
        session.add(
            TaxonCode(source="iucn2022", code="Eptesicus serotinus", taxon_id=taxon.id),
        )
        session.commit()

        index = _taxon_search_index(session)

    assert index[0]["codes"] == ["EPTSER"]
```

- [ ] **Step 7: Run to verify failure**

Run (unsandboxed): `hatch test tests/test_entities_query.py tests/test_recording_detail_view.py -m db -k "iucn"`
Expected: FAIL (the readers return the `iucn2022` code too).

- [ ] **Step 8: Filter the three readers**

In `src/fledermap/services/entities.py` add `from fledermap.domain.ranges import RANGE_CODE_SOURCE` to the imports, then:

In `list_species`, change
```python
        select(TaxonCode).where(TaxonCode.taxon_id.in_(counts)),
```
to
```python
        select(TaxonCode).where(
            TaxonCode.taxon_id.in_(counts),
            # A range-lookup name is not a code anyone knows the species by.
            TaxonCode.source != RANGE_CODE_SOURCE,
        ),
```
In `species_detail`, change
```python
            select(TaxonCode).where(TaxonCode.taxon_id == taxon_id),
```
to
```python
            select(TaxonCode).where(
                TaxonCode.taxon_id == taxon_id,
                TaxonCode.source != RANGE_CODE_SOURCE,
            ),
```
In `src/fledermap/web/views/recording_detail.py` add `from fledermap.domain.ranges import RANGE_CODE_SOURCE` and in `_taxon_search_index` change
```python
    for code in session.scalars(select(TaxonCode)).all():
```
to
```python
    for code in session.scalars(
        select(TaxonCode).where(TaxonCode.source != RANGE_CODE_SOURCE),
    ).all():
```

- [ ] **Step 9: Run the full affected suites**

Run (unsandboxed): `hatch test tests/test_iucn_codes.py tests/test_entities_query.py tests/test_entities_view.py tests/test_recording_detail_view.py tests/test_seed.py tests/test_search_service_db.py`
Expected: PASS.

- [ ] **Step 10: Format, type-check, commit**

Run: `hatch fmt` then `hatch run types:check`.

```bash
git add src/fledermap/store/data/taxa_eu.yaml src/fledermap/store/data/taxa_na.yaml src/fledermap/services/entities.py src/fledermap/web/views/recording_detail.py tests/test_iucn_codes.py tests/test_entities_query.py tests/test_recording_detail_view.py
git commit -m "feat: add iucn2022 range-lookup names to the taxa data"
```

---

### Task 4: Fetching and parsing a species' range

**Files:**
- Create: `src/fledermap/services/iucn_ranges.py` (first part: fetch + parse only)
- Test: `tests/test_iucn_fetch.py` (no DB, no network)

**Interfaces:**
- Consumes: `KEPT_PRESENCE` from `fledermap.domain.ranges`.
- Produces: `LAYER_QUERY_URL: str`, `class IucnFetchError(Exception)`, `@dataclass(frozen=True) class RangePolygon(presence: int, origin: int, wkt: str)`, `fetch_range(query_name: str, *, get: Callable[..., Any] = requests.get) -> list[RangePolygon]` (empty list = the service has no such species; any malformed/rejected/truncated reply raises `IucnFetchError`; network errors propagate as `requests` exceptions).

- [ ] **Step 1: Write the failing tests** — `tests/test_iucn_fetch.py`

```python
"""`fetch_range` against hand-written, synthetic responses. A real service reply must never be
committed here: IUCN T&C §4 forbids redistributing the data in whole or in part."""

from __future__ import annotations

from typing import Any

import pytest

from fledermap.services.iucn_ranges import IucnFetchError, fetch_range

SQUARE = [[[0.0, 0.0], [0.0, 1.0], [1.0, 1.0], [1.0, 0.0], [0.0, 0.0]]]


class _Response:
    def __init__(self, payload: Any) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Any:
        return self._payload


class _FakeGet:
    def __init__(self, payload: Any) -> None:
        self.payload = payload
        self.calls: list[dict[str, Any]] = []

    def __call__(self, url: str, **kwargs: Any) -> _Response:
        self.calls.append({"url": url, **kwargs})
        return _Response(self.payload)


def _feature(geometry: dict[str, Any] | None, presence: int = 1, origin: int = 1) -> Any:
    return {
        "type": "Feature",
        "geometry": geometry,
        "properties": {"presence": presence, "origin": origin},
    }


def test_a_polygon_becomes_a_multipolygon_with_its_codes() -> None:
    get = _FakeGet(
        {
            "type": "FeatureCollection",
            "features": [
                _feature({"type": "Polygon", "coordinates": SQUARE}, presence=2, origin=4),
            ],
        },
    )

    polygons = fetch_range("Pipistrellus pipistrellus", get=get)

    assert len(polygons) == 1
    assert (polygons[0].presence, polygons[0].origin) == (2, 4)
    assert polygons[0].wkt.startswith("MULTIPOLYGON")


def test_a_multipolygon_is_kept_as_one_polygon_row() -> None:
    get = _FakeGet(
        {
            "features": [
                _feature({"type": "MultiPolygon", "coordinates": [SQUARE, SQUARE]}),
            ],
        },
    )
    assert len(fetch_range("X y", get=get)) == 1


def test_no_features_means_the_service_does_not_know_the_species() -> None:
    assert fetch_range("Nonexistent species", get=_FakeGet({"features": []})) == []


def test_the_query_filters_presence_and_quotes_the_name() -> None:
    get = _FakeGet({"features": []})
    fetch_range("Pipistrellus o'brieni", get=get)

    call = get.calls[0]
    where = call["params"]["where"]
    assert "sci_name = 'Pipistrellus o''brieni'" in where
    assert "presence IN (1,2,3,6)" in where
    assert call["params"]["f"] == "geojson"
    assert call["timeout"] > 0
    assert "fledermap" in call["headers"]["User-Agent"].lower()


def test_an_error_reply_with_http_200_raises_instead_of_reading_as_not_found() -> None:
    """ArcGIS reports a rejected query as HTTP 200 + an `error` object. Reading that as "no
    features" would cache a transient failure as `not_found` forever."""
    get = _FakeGet({"error": {"code": 400, "message": "Unable to complete operation."}})
    with pytest.raises(IucnFetchError, match="rejected"):
        fetch_range("Pipistrellus pipistrellus", get=get)


@pytest.mark.parametrize(
    "payload",
    [
        {"features": [], "exceededTransferLimit": True},
        {"features": [], "properties": {"exceededTransferLimit": True}},
    ],
)
def test_a_truncated_reply_raises(payload: dict[str, Any]) -> None:
    with pytest.raises(IucnFetchError, match="truncated"):
        fetch_range("Pipistrellus pipistrellus", get=_FakeGet(payload))


def test_a_reply_without_a_features_list_raises() -> None:
    with pytest.raises(IucnFetchError, match="features"):
        fetch_range("Pipistrellus pipistrellus", get=_FakeGet({"unexpected": 1}))


def test_a_non_polygon_geometry_raises() -> None:
    get = _FakeGet(
        {"features": [_feature({"type": "Point", "coordinates": [0.0, 0.0]})]},
    )
    with pytest.raises(IucnFetchError, match="geometry"):
        fetch_range("Pipistrellus pipistrellus", get=get)


def test_a_feature_missing_its_presence_raises() -> None:
    feature = {
        "type": "Feature",
        "geometry": {"type": "Polygon", "coordinates": SQUARE},
        "properties": {"origin": 1},
    }
    with pytest.raises(IucnFetchError, match="presence"):
        fetch_range("Pipistrellus pipistrellus", get=_FakeGet({"features": [feature]}))


def test_a_feature_with_no_geometry_is_skipped() -> None:
    get = _FakeGet({"features": [_feature(None)]})
    assert fetch_range("X y", get=get) == []
```

- [ ] **Step 2: Run to verify failure**

Run: `hatch test tests/test_iucn_fetch.py`
Expected: FAIL (`ModuleNotFoundError: fledermap.services.iucn_ranges`).

- [ ] **Step 3: Implement** — `src/fledermap/services/iucn_ranges.py`

```python
"""IUCN Red List range plausibility check (design spec
2026-10-04-fledermap-iucn-range-check-design.md).

Fetching and cache-writing run in the worker; the web process only calls `range_status` /
`attach_ranges`, which read the cache. The polygons are IUCN data under non-commercial terms
that forbid redistribution (T&C v3.1 §4), so nothing here hands a polygon to a caller outside
this module: `range_status` reduces them to a verdict and a distance.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import requests
from shapely.geometry import MultiPolygon, shape

from fledermap.domain.ranges import KEPT_PRESENCE

LAYER_QUERY_URL = (
    "https://data-gis.unep-wcmc.org/server/rest/services/Hosted/IUCN_Red_List/MapServer/0/query"
)
# Server-side simplification tolerance in degrees (~2 km): far below the "near" threshold, so
# it cannot change a verdict, and it keeps the download and the stored rows small.
_SIMPLIFY_DEGREES = 0.02
_REQUEST_TIMEOUT_S = 60
_USER_AGENT = "fledermap-iucn-range-check (non-commercial hobby project)"


class IucnFetchError(Exception):
    """The service answered, but not with a usable range -- distinct from "no such species"
    (an empty feature list), which is a legitimate answer that gets cached."""


@dataclass(frozen=True)
class RangePolygon:
    presence: int
    origin: int
    wkt: str  # MULTIPOLYGON, EPSG:4326


def _int_property(properties: dict[str, Any], key: str, query_name: str) -> int:
    try:
        return int(properties[key])
    except (KeyError, TypeError, ValueError) as exc:
        msg = f"IUCN feature for {query_name!r} has no usable {key!r}"
        raise IucnFetchError(msg) from exc


def _parse(payload: Any, query_name: str) -> list[RangePolygon]:
    # ArcGIS reports a rejected query as HTTP 200 with an "error" object. Reading that as "no
    # features" would cache a transient failure as `not_found` and never retry it.
    if not isinstance(payload, dict) or "error" in payload:
        msg = f"IUCN service rejected the query for {query_name!r}: {str(payload)[:200]}"
        raise IucnFetchError(msg)
    features = payload.get("features")
    if not isinstance(features, list):
        msg = f"IUCN reply for {query_name!r} has no 'features' list"
        raise IucnFetchError(msg)
    nested = payload.get("properties")
    truncated = payload.get("exceededTransferLimit") or (
        isinstance(nested, dict) and nested.get("exceededTransferLimit")
    )
    if truncated:
        # A partial range would make real records look implausible; we do not paginate because
        # one species never approaches the 2,000-record limit.
        msg = f"IUCN reply for {query_name!r} was truncated"
        raise IucnFetchError(msg)

    polygons: list[RangePolygon] = []
    for feature in features:
        geometry = feature.get("geometry")
        if geometry is None:
            continue
        properties = feature.get("properties") or {}
        presence = _int_property(properties, "presence", query_name)
        origin = _int_property(properties, "origin", query_name)
        geom = shape(geometry)
        if geom.is_empty:
            continue
        if geom.geom_type == "Polygon":
            geom = MultiPolygon([geom])
        elif geom.geom_type != "MultiPolygon":
            msg = f"IUCN reply for {query_name!r} has unexpected geometry {geom.geom_type!r}"
            raise IucnFetchError(msg)
        polygons.append(RangePolygon(presence, origin, geom.wkt))
    return polygons


def fetch_range(
    query_name: str,
    *,
    get: Callable[..., Any] = requests.get,
) -> list[RangePolygon]:
    """The kept range polygons for one IUCN species name; `[]` when the service has none.
    `get` is injectable so tests never touch the network."""
    quoted = query_name.replace("'", "''")
    presence = ",".join(str(p) for p in KEPT_PRESENCE)
    response = get(
        LAYER_QUERY_URL,
        params={
            "where": f"sci_name = '{quoted}' AND presence IN ({presence})",
            "outFields": "presence,origin",
            "returnGeometry": "true",
            "outSR": "4326",
            "maxAllowableOffset": str(_SIMPLIFY_DEGREES),
            "geometryPrecision": "3",
            "f": "geojson",
        },
        headers={"User-Agent": _USER_AGENT},
        timeout=_REQUEST_TIMEOUT_S,
    )
    response.raise_for_status()
    return _parse(response.json(), query_name)
```

- [ ] **Step 4: Run tests**

Run: `hatch test tests/test_iucn_fetch.py`
Expected: PASS.

- [ ] **Step 5: Live smoke check (manual; output not saved)**

Run (with `allowed_domains: ["data-gis.unep-wcmc.org"]`), from a throwaway file in the scratchpad (not in the repo; `hatch run` brace-substitutes `-c` arguments):
```python
from fledermap.services.iucn_ranges import fetch_range
polys = fetch_range("Pipistrellus pipistrellus")
print(len(polys), {(p.presence, p.origin) for p in polys}, len(polys[0].wkt))
print(fetch_range("Eptesicus serotinus") != [])
```
Run it with `hatch run python <scratchpad>/smoke.py`. Expected: a small positive polygon count, presence codes within `{1,2,3,6}`, and `True`. This also confirms geometry arrives in WGS84 and `f=geojson` puts `presence`/`origin` under `properties` (spec open items). If `properties` come back empty or named differently, fix `_parse` and `outFields` before continuing.

- [ ] **Step 6: Format, type-check, commit**

Run: `hatch fmt` then `hatch run types:check`.

```bash
git add src/fledermap/services/iucn_ranges.py tests/test_iucn_fetch.py
git commit -m "feat: fetch and parse IUCN species range polygons"
```

---

### Task 5: Pending set, storage and `fetch_pending`

**Files:**
- Modify: `src/fledermap/services/iucn_ranges.py` (append)
- Test: `tests/test_iucn_ranges_service.py` (db; created here, extended in Tasks 6–7)

**Interfaces:**
- Consumes: `fetch_range`, `RangePolygon` (Task 4); `SpeciesRange`, `SpeciesRangeFetch` (Task 2); `RANGE_CODE_SOURCE`, `IUCN_RELEASE`, `FETCH_FOUND`, `FETCH_NOT_FOUND`.
- Produces: `pending_taxa(session) -> list[tuple[int, str]]` (taxon_id, query_name), `store_range(session, taxon_id: int, query_name: str, polygons: Sequence[RangePolygon], *, now: datetime | None = None) -> None`, `fetch_pending(session, *, fetch: Callable[[str], list[RangePolygon]] | None = None) -> int` (`None` means the module's `fetch_range`, resolved at call time; commits after each species).

- [ ] **Step 1: Write the failing tests** — create `tests/test_iucn_ranges_service.py`

```python
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from geoalchemy2.elements import WKTElement
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import IdSource, Verdict
from fledermap.domain.ranges import RANGE_CODE_SOURCE
from fledermap.services import iucn_ranges
from fledermap.services.iucn_ranges import RangePolygon
from fledermap.store.models import (
    ClassifierDetection,
    Identification,
    Recording,
    SpeciesRange,
    SpeciesRangeFetch,
    Taxon,
    TaxonCode,
)

pytestmark = pytest.mark.db

SQUARE_WKT = "MULTIPOLYGON(((0 0, 0 1, 1 1, 1 0, 0 0)))"


def _taxon(
    session: OrmSession,
    name: str = "Pipistrellus pipistrellus",
    *,
    code: str | None = "Pipistrellus pipistrellus",
    rank: str = "species",
) -> Taxon:
    taxon = Taxon(rank=rank, scientific_name=name)
    session.add(taxon)
    session.flush()
    if code is not None:
        session.add(TaxonCode(source=RANGE_CODE_SOURCE, code=code, taxon_id=taxon.id))
    session.flush()
    return taxon


def _recording(
    session: OrmSession,
    audio_hash: str = "a" * 64,
    lon: float | None = 0.5,
    lat: float | None = 0.5,
) -> Recording:
    recording = Recording(
        audio_hash=audio_hash,
        path=f"{audio_hash}.wav",
        recorded_at=datetime(2026, 10, 4, tzinfo=UTC),
        geom=None if lon is None else WKTElement(f"POINT({lon} {lat})", srid=4326),
    )
    session.add(recording)
    session.flush()
    return recording


def _identify(session: OrmSession, recording: Recording, taxon: Taxon) -> None:
    session.add(
        Identification(
            recording_id=recording.id,
            source=IdSource.BATDETECT2,
            verdict=Verdict.SPECIES,
            taxon_id=taxon.id,
            first_seen_at=datetime(2026, 10, 4, tzinfo=UTC),
        ),
    )
    session.flush()


def test_a_species_that_occurs_in_an_identification_is_pending(engine: Engine) -> None:
    with OrmSession(engine) as session:
        taxon = _taxon(session)
        _identify(session, _recording(session), taxon)
        session.commit()

        assert iucn_ranges.pending_taxa(session) == [
            (taxon.id, "Pipistrellus pipistrellus"),
        ]


def test_a_species_seen_only_by_a_classifier_detection_is_pending(
    engine: Engine,
) -> None:
    with OrmSession(engine) as session:
        taxon = _taxon(session)
        recording = _recording(session)
        session.add(
            ClassifierDetection(
                recording_id=recording.id,
                source=IdSource.BATTYBIRDNET,
                taxon_id=taxon.id,
                raw_label="Pipistrellus pipistrellus",
                confidence=0.4,
            ),
        )
        session.commit()

        assert [t for t, _ in iucn_ranges.pending_taxa(session)] == [taxon.id]


def test_a_species_never_seen_is_not_pending(engine: Engine) -> None:
    with OrmSession(engine) as session:
        _taxon(session)
        session.commit()

        assert iucn_ranges.pending_taxa(session) == []


def test_a_species_without_an_iucn_code_or_a_group_taxon_is_not_pending(
    engine: Engine,
) -> None:
    with OrmSession(engine) as session:
        recording = _recording(session)
        no_code = _taxon(session, "Plecotus auritus", code=None)
        group = _taxon(session, "Myotis", code="Myotis", rank="genus")
        _identify(session, recording, no_code)
        session.add(
            Identification(
                recording_id=recording.id,
                source=IdSource.EMT_GUANO,
                verdict=Verdict.SPECIES,
                taxon_id=group.id,
                first_seen_at=datetime(2026, 10, 4, tzinfo=UTC),
            ),
        )
        session.commit()

        assert iucn_ranges.pending_taxa(session) == []


def test_store_range_replaces_polygons_and_records_the_fetch(engine: Engine) -> None:
    with OrmSession(engine) as session:
        taxon = _taxon(session)
        polygon = RangePolygon(presence=1, origin=1, wkt=SQUARE_WKT)
        iucn_ranges.store_range(session, taxon.id, "Pipistrellus pipistrellus", [polygon])
        iucn_ranges.store_range(
            session,
            taxon.id,
            "Pipistrellus pipistrellus",
            [polygon, RangePolygon(presence=2, origin=4, wkt=SQUARE_WKT)],
        )
        session.commit()

        assert session.scalar(select(func.count()).select_from(SpeciesRange)) == 2
        fetch_row = session.get(SpeciesRangeFetch, taxon.id)
        assert fetch_row is not None
        assert fetch_row.status == "found"
        assert fetch_row.source_version == "2022-2"


def test_store_range_with_no_polygons_records_not_found(engine: Engine) -> None:
    with OrmSession(engine) as session:
        taxon = _taxon(session)
        iucn_ranges.store_range(session, taxon.id, "Pipistrellus pipistrellus", [])
        session.commit()

        fetch_row = session.get(SpeciesRangeFetch, taxon.id)
        assert fetch_row is not None
        assert fetch_row.status == "not_found"
        assert session.scalar(select(func.count()).select_from(SpeciesRange)) == 0


def test_an_invalid_self_intersecting_polygon_is_repaired_not_rejected(
    engine: Engine,
) -> None:
    bow_tie = "MULTIPOLYGON(((0 0, 1 1, 1 0, 0 1, 0 0)))"
    with OrmSession(engine) as session:
        taxon = _taxon(session)
        iucn_ranges.store_range(
            session,
            taxon.id,
            "Pipistrellus pipistrellus",
            [RangePolygon(presence=1, origin=1, wkt=bow_tie)],
        )
        session.commit()

        valid = session.scalar(select(func.ST_IsValid(SpeciesRange.geom)))
        assert valid is True


def test_not_found_is_never_fetched_again_but_a_changed_code_is(engine: Engine) -> None:
    with OrmSession(engine) as session:
        taxon = _taxon(session)
        _identify(session, _recording(session), taxon)
        iucn_ranges.store_range(session, taxon.id, "Pipistrellus pipistrellus", [])
        session.commit()
        assert iucn_ranges.pending_taxa(session) == []

        code = session.scalars(select(TaxonCode)).one()
        code.code = "Pipistrellus pipistrellus s.str."
        session.commit()

        assert iucn_ranges.pending_taxa(session) == [
            (taxon.id, "Pipistrellus pipistrellus s.str."),
        ]


def test_fetch_pending_fetches_stores_and_commits_each_species(engine: Engine) -> None:
    asked: list[str] = []

    def fake_fetch(name: str) -> list[RangePolygon]:
        asked.append(name)
        return [RangePolygon(presence=1, origin=1, wkt=SQUARE_WKT)]

    with OrmSession(engine) as session:
        a = _taxon(session, "Pipistrellus pipistrellus")
        b = _taxon(session, "Nyctalus noctula", code="Nyctalus noctula")
        recording = _recording(session)
        _identify(session, recording, a)
        _identify(session, recording, b)
        # `uq_identification_source_claim` allows one row per (recording, source, taxon).
        session.commit()

        assert iucn_ranges.fetch_pending(session, fetch=fake_fetch) == 2
        assert iucn_ranges.fetch_pending(session, fetch=fake_fetch) == 0

    assert sorted(asked) == ["Nyctalus noctula", "Pipistrellus pipistrellus"]


def test_a_failure_on_a_later_species_keeps_the_earlier_ones(engine: Engine) -> None:
    def flaky_fetch(name: str) -> list[RangePolygon]:
        if name == "Nyctalus noctula":
            msg = "boom"
            raise RuntimeError(msg)
        return [RangePolygon(presence=1, origin=1, wkt=SQUARE_WKT)]

    with OrmSession(engine) as session:
        a = _taxon(session, "Pipistrellus pipistrellus")
        b = _taxon(session, "Nyctalus noctula", code="Nyctalus noctula")
        recording = _recording(session)
        _identify(session, recording, a)
        _identify(session, recording, b)
        session.commit()

        with pytest.raises(RuntimeError, match="boom"):
            iucn_ranges.fetch_pending(session, fetch=flaky_fetch)

    with OrmSession(engine) as fresh:
        assert fresh.get(SpeciesRangeFetch, a.id) is not None
        assert fresh.get(SpeciesRangeFetch, b.id) is None
```

- [ ] **Step 2: Run to verify failure**

Run (unsandboxed): `hatch test tests/test_iucn_ranges_service.py -m db`
Expected: FAIL (`AttributeError: module 'fledermap.services.iucn_ranges' has no attribute 'pending_taxa'`).

- [ ] **Step 3: Implement** — extend `src/fledermap/services/iucn_ranges.py`

Replace the import block at the top with:
```python
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import requests
from shapely.geometry import MultiPolygon, shape
from sqlalchemy import delete, or_, select, text
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.ranges import (
    FETCH_FOUND,
    FETCH_NOT_FOUND,
    IUCN_RELEASE,
    KEPT_PRESENCE,
    RANGE_CODE_SOURCE,
)
from fledermap.store.models import (
    ClassifierDetection,
    Identification,
    SpeciesRange,
    SpeciesRangeFetch,
    Taxon,
    TaxonCode,
)
```
and append after `fetch_range`:

```python
# Repair, then simplify, then force a MULTIPOLYGON in PostGIS rather than in shapely: the
# service's own simplification can leave a self-intersecting ring, and `ST_MakeValid` is the
# one place that repairs it without a second geometry library. The geography cast must come
# last -- the geometry functions above do not exist for it.
_INSERT_POLYGON = text(
    """
    INSERT INTO species_range (taxon_id, presence, origin, geom)
    SELECT :taxon_id, :presence, :origin,
           ST_Multi(
               ST_CollectionExtract(
                   ST_SimplifyPreserveTopology(
                       ST_MakeValid(ST_GeomFromText(:wkt, 4326)), :tolerance
                   ),
                   3
               )
           )::geography
    """,
)


def pending_taxa(session: OrmSession) -> list[tuple[int, str]]:
    """(taxon_id, IUCN name) for every species that occurs in this database -- as an
    identification or a raw classifier detection -- has an `iucn2022` code, and has not been
    fetched under that code. A changed code makes the taxon pending again; a `not_found` fetch
    does not, so an unknown species is asked about exactly once."""
    used = or_(
        TaxonCode.taxon_id.in_(
            select(Identification.taxon_id).where(Identification.taxon_id.is_not(None)),
        ),
        TaxonCode.taxon_id.in_(
            select(ClassifierDetection.taxon_id).where(
                ClassifierDetection.taxon_id.is_not(None),
            ),
        ),
    )
    rows = session.execute(
        select(TaxonCode.taxon_id, TaxonCode.code)
        .join(Taxon, Taxon.id == TaxonCode.taxon_id)
        .outerjoin(
            SpeciesRangeFetch,
            SpeciesRangeFetch.taxon_id == TaxonCode.taxon_id,
        )
        .where(
            TaxonCode.source == RANGE_CODE_SOURCE,
            Taxon.rank == "species",
            used,
            or_(
                SpeciesRangeFetch.taxon_id.is_(None),
                SpeciesRangeFetch.query_name != TaxonCode.code,
            ),
        )
        .order_by(TaxonCode.taxon_id),
    ).all()
    return [(row.taxon_id, row.code) for row in rows]


def store_range(
    session: OrmSession,
    taxon_id: int,
    query_name: str,
    polygons: Sequence[RangePolygon],
    *,
    now: datetime | None = None,
) -> None:
    """Replace a taxon's cached polygons with `polygons` and record the fetch. An empty list
    is recorded as `not_found`, so the species is not asked about again."""
    session.execute(delete(SpeciesRange).where(SpeciesRange.taxon_id == taxon_id))
    for polygon in polygons:
        session.execute(
            _INSERT_POLYGON,
            {
                "taxon_id": taxon_id,
                "presence": polygon.presence,
                "origin": polygon.origin,
                "wkt": polygon.wkt,
                "tolerance": _SIMPLIFY_DEGREES,
            },
        )
    session.merge(
        SpeciesRangeFetch(
            taxon_id=taxon_id,
            query_name=query_name,
            status=FETCH_FOUND if polygons else FETCH_NOT_FOUND,
            source_version=IUCN_RELEASE,
            fetched_at=now or datetime.now(UTC),
        ),
    )
    session.flush()


def fetch_pending(
    session: OrmSession,
    *,
    fetch: Callable[[str], list[RangePolygon]] | None = None,
) -> int:
    """Fetch and store every pending species, one at a time. Commits after each so a failure
    on a later species (the job then retries) keeps the earlier ones. Returns how many were
    stored."""
    # Looked up here, not bound as a default argument: a default would freeze the original
    # function object at definition time and make `fetch_range` impossible to replace for the
    # job-level tests, which cannot pass an argument through the task.
    fetcher = fetch if fetch is not None else fetch_range
    stored = 0
    for taxon_id, query_name in pending_taxa(session):
        store_range(session, taxon_id, query_name, fetcher(query_name))
        session.commit()
        stored += 1
    return stored
```
(The replacement import block above supersedes the whole old one from Task 4, so no import is left duplicated.)

- [ ] **Step 4: Run tests**

Run (unsandboxed): `hatch test tests/test_iucn_ranges_service.py tests/test_iucn_fetch.py`
Expected: PASS. (`test_a_species_that_occurs_in_an_identification_is_pending` etc. use real PostGIS.)

- [ ] **Step 5: Format, type-check, commit**

Run: `hatch fmt` then `hatch run types:check`.

```bash
git add src/fledermap/services/iucn_ranges.py tests/test_iucn_ranges_service.py
git commit -m "feat: cache IUCN species ranges for the species in use"
```

---

### Task 6: `range_status` — inside / near / far / no data

**Files:**
- Modify: `src/fledermap/services/iucn_ranges.py` (append)
- Test: `tests/test_iucn_ranges_service.py` (append)

**Interfaces:**
- Consumes: `store_range`, models, `RangeStatus`/`RangeKind`/`NoDataReason`, `VAGRANT_ORIGIN`.
- Produces: `range_status(session, taxon_id: int, recording: Recording, *, edge_km: float) -> RangeStatus`. `recording` must be persisted (has an `id`).

The test geometry: ranges are degree squares near the equator (1° ≈ 111 km), so distances are easy to reason about: a point 0.5° east of the square's edge is ~55 km out; 2° out is ~222 km.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_iucn_ranges_service.py`

```python
from fledermap.domain.ranges import NoDataReason, RangeKind  # noqa: E402

NATIVE_SQUARE = RangePolygon(presence=1, origin=1, wkt=SQUARE_WKT)
VAGRANT_SQUARE = RangePolygon(
    presence=1,
    origin=4,
    wkt="MULTIPOLYGON(((10 0, 10 1, 11 1, 11 0, 10 0)))",
)


def _status(
    session: OrmSession,
    taxon: Taxon,
    recording: Recording,
    edge_km: float = 100.0,
) -> iucn_ranges.RangeStatus:
    return iucn_ranges.range_status(session, taxon.id, recording, edge_km=edge_km)


def _with_range(
    session: OrmSession,
    polygons: list[RangePolygon],
    *,
    lon: float | None,
    lat: float | None = 0.5,
) -> tuple[Taxon, Recording]:
    taxon = _taxon(session)
    recording = _recording(session, lon=lon, lat=lat)
    iucn_ranges.store_range(session, taxon.id, "Pipistrellus pipistrellus", polygons)
    session.commit()
    return taxon, recording


def test_a_point_inside_the_range_is_inside(engine: Engine) -> None:
    with OrmSession(engine) as session:
        taxon, recording = _with_range(session, [NATIVE_SQUARE], lon=0.5)

        status = _status(session, taxon, recording)

    assert status.kind is RangeKind.INSIDE


def test_a_point_just_outside_is_near_with_its_distance(engine: Engine) -> None:
    with OrmSession(engine) as session:
        taxon, recording = _with_range(session, [NATIVE_SQUARE], lon=1.5)

        status = _status(session, taxon, recording)

    assert status.kind is RangeKind.NEAR
    assert status.distance_km is not None
    assert 50 < status.distance_km < 60


def test_a_point_beyond_the_edge_distance_is_far(engine: Engine) -> None:
    with OrmSession(engine) as session:
        taxon, recording = _with_range(session, [NATIVE_SQUARE], lon=3.0)

        far = _status(session, taxon, recording)
        generous = _status(session, taxon, recording, edge_km=500.0)

    assert far.kind is RangeKind.FAR
    assert far.distance_km is not None
    assert far.distance_km > 200
    assert generous.kind is RangeKind.NEAR


def test_a_point_in_a_hole_of_the_range_is_outside_not_inside(engine: Engine) -> None:
    donut = RangePolygon(
        presence=1,
        origin=1,
        wkt="MULTIPOLYGON(((0 0, 0 3, 3 3, 3 0, 0 0), (1 1, 2 1, 2 2, 1 2, 1 1)))",
    )
    with OrmSession(engine) as session:
        taxon, recording = _with_range(session, [donut], lon=1.5, lat=1.5)

        status = _status(session, taxon, recording)

    assert status.kind is RangeKind.NEAR


def test_a_point_only_inside_a_vagrant_polygon_is_near_not_inside(
    engine: Engine,
) -> None:
    with OrmSession(engine) as session:
        taxon, recording = _with_range(
            session,
            [NATIVE_SQUARE, VAGRANT_SQUARE],
            lon=10.5,
        )

        status = _status(session, taxon, recording)

    assert status.kind is RangeKind.NEAR
    assert status.vagrant is True


def test_a_species_with_only_vagrant_polygons_is_near_inside_one_and_far_away(
    engine: Engine,
) -> None:
    with OrmSession(engine) as session:
        taxon, inside = _with_range(session, [VAGRANT_SQUARE], lon=10.5)
        elsewhere = _recording(session, "b" * 64, lon=50.0, lat=0.5)
        session.commit()

        in_vagrant = _status(session, taxon, inside)
        far_away = _status(session, taxon, elsewhere)

    assert in_vagrant.kind is RangeKind.NEAR
    assert in_vagrant.vagrant is True
    assert far_away.kind is RangeKind.FAR


def test_a_recording_without_a_location_has_no_data(engine: Engine) -> None:
    with OrmSession(engine) as session:
        taxon, recording = _with_range(session, [NATIVE_SQUARE], lon=None)

        status = _status(session, taxon, recording)

    assert status.kind is RangeKind.NO_DATA
    assert status.reason is NoDataReason.NO_LOCATION


def test_a_taxon_without_an_iucn_code_has_no_data(engine: Engine) -> None:
    with OrmSession(engine) as session:
        taxon = _taxon(session, "Plecotus auritus", code=None)
        recording = _recording(session)
        session.commit()

        status = _status(session, taxon, recording)

    assert status.reason is NoDataReason.NO_CODE


def test_a_taxon_not_fetched_yet_has_no_data(engine: Engine) -> None:
    with OrmSession(engine) as session:
        taxon = _taxon(session)
        recording = _recording(session)
        session.commit()

        status = _status(session, taxon, recording)

    assert status.reason is NoDataReason.NOT_FETCHED


def test_a_species_the_service_does_not_know_has_no_data(engine: Engine) -> None:
    with OrmSession(engine) as session:
        taxon, recording = _with_range(session, [], lon=0.5)

        status = _status(session, taxon, recording)

    assert status.reason is NoDataReason.NOT_FOUND


def test_a_fetch_under_an_outdated_code_counts_as_not_fetched(engine: Engine) -> None:
    with OrmSession(engine) as session:
        taxon, recording = _with_range(session, [NATIVE_SQUARE], lon=0.5)
        session.scalars(select(TaxonCode)).one().code = "Pipistrellus renamed"
        session.commit()

        status = _status(session, taxon, recording)

    assert status.reason is NoDataReason.NOT_FETCHED
```
(Move the `from fledermap.domain.ranges import NoDataReason, RangeKind` into the module's top import block when pasting; the `# noqa` line above is only there to keep this snippet self-contained.)

- [ ] **Step 2: Run to verify failure**

Run (unsandboxed): `hatch test tests/test_iucn_ranges_service.py -m db -k "range or point or species_with_only or outdated or location or iucn_code or not_fetched or does_not_know"`
Expected: FAIL (`AttributeError: ... has no attribute 'range_status'`).

- [ ] **Step 3: Implement** — append to `src/fledermap/services/iucn_ranges.py`; extend the imports

Add to the imports: `from sqlalchemy import func` (merge into the existing `from sqlalchemy import delete, or_, select, text` line), `from fledermap.domain.ranges import NoDataReason, RangeKind, RangeStatus, VAGRANT_ORIGIN` (merge into the existing domain import), and `Recording` to the models import. Then append:

```python
def range_status(
    session: OrmSession,
    taxon_id: int,
    recording: Recording,
    *,
    edge_km: float,
) -> RangeStatus:
    """Where `recording` lies relative to the taxon's cached IUCN range. Reads the cache only;
    returns a verdict and a distance, never a polygon (T&C §4). `recording` must be persisted."""
    if recording.geom is None:
        return RangeStatus(RangeKind.NO_DATA, reason=NoDataReason.NO_LOCATION)
    code = session.scalar(
        select(TaxonCode.code).where(
            TaxonCode.taxon_id == taxon_id,
            TaxonCode.source == RANGE_CODE_SOURCE,
        ),
    )
    if code is None:
        return RangeStatus(RangeKind.NO_DATA, reason=NoDataReason.NO_CODE)
    fetched = session.get(SpeciesRangeFetch, taxon_id)
    if fetched is None or fetched.query_name != code:
        return RangeStatus(RangeKind.NO_DATA, reason=NoDataReason.NOT_FETCHED)
    if fetched.status != FETCH_FOUND:
        return RangeStatus(RangeKind.NO_DATA, reason=NoDataReason.NOT_FOUND)

    distance = func.ST_Distance(SpeciesRange.geom, Recording.geom)
    native_m, any_m, in_vagrant = session.execute(
        select(
            func.min(distance).filter(SpeciesRange.origin != VAGRANT_ORIGIN),
            func.min(distance),
            func.bool_or(func.ST_Intersects(SpeciesRange.geom, Recording.geom)).filter(
                SpeciesRange.origin == VAGRANT_ORIGIN,
            ),
        )
        .select_from(SpeciesRange)
        # An explicit JOIN, not a second FROM element: SQLAlchemy's cartesian-product linter
        # warns about two FROMs the WHERE clause does not connect, and a warning is a defect.
        .join(Recording, Recording.id == recording.id)
        .where(SpeciesRange.taxon_id == taxon_id),
    ).one()

    if any_m is None:
        return RangeStatus(RangeKind.NO_DATA, reason=NoDataReason.NOT_FOUND)
    if native_m is not None and native_m == 0:
        return RangeStatus(RangeKind.INSIDE, distance_km=0.0)
    # Distance is measured to the regular (non-vagrant) range when there is one: a vagrant
    # polygon next to the point must not make a far-from-range record look close.
    distance_km = float(native_m if native_m is not None else any_m) / 1000.0
    if in_vagrant:
        return RangeStatus(RangeKind.NEAR, distance_km=distance_km, vagrant=True)
    kind = RangeKind.NEAR if distance_km <= edge_km else RangeKind.FAR
    return RangeStatus(kind, distance_km=distance_km)
```

- [ ] **Step 4: Run tests**

Run (unsandboxed): `hatch test tests/test_iucn_ranges_service.py`
Expected: PASS, and no warnings in the output (the explicit JOIN avoids SQLAlchemy's cartesian-product warning; if one appears, fix the query rather than ignoring it).

- [ ] **Step 5: Format, type-check, commit**

Run: `hatch fmt` then `hatch run types:check`.

```bash
git add src/fledermap/services/iucn_ranges.py tests/test_iucn_ranges_service.py
git commit -m "feat: compute inside/near/far range status for a recording"
```

---

### Task 7: The fetch job and the ingest-cycle hook

**Files:**
- Modify: `src/fledermap/jobs/tasks.py` (lock constant, task, ingest-cycle hook)
- Modify: `src/fledermap/services/iucn_ranges.py` (append `enqueue_range_fetch`)
- Test: `tests/test_iucn_ranges_service.py` (append), `tests/test_jobs_tasks.py` (append)

**Interfaces:**
- Consumes: `fetch_pending`, `pending_taxa` (Task 5), `Config.iucn_ranges_enabled` (Task 1).
- Produces: `_FETCH_IUCN_RANGES_LOCK: str`, `fetch_iucn_ranges_task(context)`, `enqueue_range_fetch(session, engine, *, enabled: bool) -> bool`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_iucn_ranges_service.py` (add `from pathlib import Path`, `from types import SimpleNamespace`, `from typing import cast`, `import procrastinate`, `from fledermap.config import Config`, `from fledermap.jobs.app import ensure_schema`, `from fledermap.jobs.tasks import app as jobs_app`, `from fledermap.jobs.tasks import fetch_iucn_ranges_task` to the top imports):

```python
def _task_context(
    engine: Engine,
    tmp_path: Path,
    *,
    enabled: bool,
) -> procrastinate.JobContext:
    config = Config(
        database_url="postgresql://unused/unused",
        archive_roots=(tmp_path / "archive",),
        media_root=tmp_path / "media",
        iucn_ranges_enabled=enabled,
    )
    return cast(
        "procrastinate.JobContext",
        SimpleNamespace(additional_context={"config": config, "engine": engine}),
    )


def test_enqueue_is_a_no_op_when_disabled(engine: Engine) -> None:
    with OrmSession(engine) as session:
        _identify(session, _recording(session), _taxon(session))
        session.commit()

        assert iucn_ranges.enqueue_range_fetch(session, engine, enabled=False) is False


def test_enqueue_is_a_no_op_when_nothing_is_pending(engine: Engine) -> None:
    jobs_app.open(engine)
    ensure_schema(jobs_app, engine)
    with OrmSession(engine) as session:
        assert iucn_ranges.enqueue_range_fetch(session, engine, enabled=True) is False


def test_enqueue_defers_one_job_and_dedupes_the_second(engine: Engine) -> None:
    jobs_app.open(engine)
    ensure_schema(jobs_app, engine)
    with OrmSession(engine) as session:
        _identify(session, _recording(session), _taxon(session))
        session.commit()

        assert iucn_ranges.enqueue_range_fetch(session, engine, enabled=True) is True
        assert iucn_ranges.enqueue_range_fetch(session, engine, enabled=True) is False


def test_the_task_does_nothing_when_the_flag_was_turned_off_after_deferral(
    engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    called: list[str] = []
    monkeypatch.setattr(
        iucn_ranges,
        "fetch_range",
        lambda name, **_: called.append(name) or [],
    )
    with OrmSession(engine) as session:
        _identify(session, _recording(session), _taxon(session))
        session.commit()

    fetch_iucn_ranges_task(_task_context(engine, tmp_path, enabled=False))

    assert called == []
    with OrmSession(engine) as fresh:
        assert fresh.scalars(select(SpeciesRangeFetch)).all() == []


def test_the_task_fetches_pending_species_and_commits(
    engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        iucn_ranges,
        "fetch_range",
        lambda name, **_: [RangePolygon(presence=1, origin=1, wkt=SQUARE_WKT)],
    )
    with OrmSession(engine) as session:
        taxon = _taxon(session)
        _identify(session, _recording(session), taxon)
        session.commit()

    fetch_iucn_ranges_task(_task_context(engine, tmp_path, enabled=True))

    with OrmSession(engine) as fresh:
        assert fresh.get(SpeciesRangeFetch, taxon.id) is not None
        assert fresh.scalar(select(func.count()).select_from(SpeciesRange)) == 1
```

Append to `tests/test_jobs_tasks.py` (reuse that file's existing `_make_config`, `_run_worker`, `_INGEST_CYCLE_LOCK`, `jobs_app`, `ensure_schema`, `run_ingest_cycle`, `replace`, `time`, `text`, `Engine`, `Path` imports; add `from fledermap.domain.codes import IdSource, Verdict`, `from fledermap.domain.ranges import RANGE_CODE_SOURCE` and `Identification, Recording, Taxon, TaxonCode` model imports if not already present, plus `from datetime import UTC, datetime` and `OrmSession`):

```python
def test_run_ingest_cycle_defers_range_fetch_only_when_enabled_and_pending(
    engine: Engine,
    tmp_path: Path,
) -> None:
    """The ingest cycle is what makes the first fetch happen after the flag is turned on."""
    with OrmSession(engine) as session:
        taxon = Taxon(rank="species", scientific_name="Pipistrellus pipistrellus")
        session.add(taxon)
        session.flush()
        session.add(
            TaxonCode(
                source=RANGE_CODE_SOURCE,
                code="Pipistrellus pipistrellus",
                taxon_id=taxon.id,
            ),
        )
        recording = Recording(
            audio_hash="c" * 64,
            path="c.wav",
            recorded_at=datetime(2026, 10, 4, tzinfo=UTC),
        )
        session.add(recording)
        session.flush()
        session.add(
            Identification(
                recording_id=recording.id,
                source=IdSource.BATDETECT2,
                verdict=Verdict.SPECIES,
                taxon_id=taxon.id,
                first_seen_at=datetime(2026, 10, 4, tzinfo=UTC),
            ),
        )
        session.commit()

    jobs_app.open(engine)
    ensure_schema(jobs_app, engine)
    base = _make_config(tmp_path)
    count_sql = text(
        "SELECT count(*) FROM procrastinate_jobs "
        "WHERE task_name = 'fledermap.jobs.tasks.fetch_iucn_ranges_task' "
        "AND status = 'todo'",
    )

    for config, expected in ((base, 0), (replace(base, iucn_ranges_enabled=True), 1)):
        run_ingest_cycle.configure(
            lock=_INGEST_CYCLE_LOCK,
            queueing_lock=_INGEST_CYCLE_LOCK,
        ).defer(timestamp=int(time.time()))
        _run_worker(
            engine,
            wait=False,
            install_signal_handlers=False,
            listen_notify=False,
            queues=["ingest"],
            additional_context={"config": config, "engine": engine},
        )
        with engine.connect() as conn:
            assert conn.execute(count_sql).scalar() == expected
```

- [ ] **Step 2: Run to verify failure**

Run (unsandboxed): `hatch test tests/test_iucn_ranges_service.py tests/test_jobs_tasks.py -m db -k "enqueue or task or range_fetch"`
Expected: FAIL (`ImportError: cannot import name 'fetch_iucn_ranges_task'`).

- [ ] **Step 3: Implement**

In `src/fledermap/services/iucn_ranges.py` append `enqueue_range_fetch`:

```python
def enqueue_range_fetch(
    session: OrmSession,
    engine: Engine,
    *,
    enabled: bool,
) -> bool:
    """Defer one fetch job when the flag is on and some species is pending. Unlike region
    resolution, nothing here changes without a new species or code, so an idle ingest cycle must
    not queue a job. The queueing lock drops a duplicate while one is already waiting."""
    if not enabled or not pending_taxa(session):
        return False
    try:
        jobs_app.open(engine)
    except NotImplementedError:
        pass  # already open inside a running worker -- see enqueue_media's docstring
    try:
        fetch_iucn_ranges_task.configure(
            lock=_FETCH_IUCN_RANGES_LOCK,
            queueing_lock=_FETCH_IUCN_RANGES_LOCK,
        ).defer()
    except procrastinate.exceptions.AlreadyEnqueued:
        return False
    return True
```
with these added imports at the top: `import procrastinate`, `from sqlalchemy.engine import Engine`, `from fledermap.jobs.tasks import _FETCH_IUCN_RANGES_LOCK, fetch_iucn_ranges_task`, `from fledermap.jobs.tasks import app as jobs_app` (same top-level circular-import arrangement as `services/regions.py`; the task imports this module lazily).

In `src/fledermap/jobs/tasks.py`, next to `_RESOLVE_REGIONS_LOCK = "poiidx-resolve-regions"` add:
```python
_FETCH_IUCN_RANGES_LOCK = "iucn-fetch-ranges"
```
and after `resolve_regions_task` add:
```python
@app.task(queue="geo", pass_context=True, retry=_RETRY)
def fetch_iucn_ranges_task(context: procrastinate.JobContext) -> None:
    """Fetch IUCN range polygons for every species that needs one (design spec
    2026-10-04-fledermap-iucn-range-check-design.md). One job per cycle, sequential requests,
    one commit per species inside `fetch_pending`."""
    # Local import for the same circularity reason as `resolve_regions_task`'s.
    from fledermap.services import iucn_ranges

    config: Config = context.additional_context["config"]
    engine = context.additional_context["engine"]
    if not config.iucn_ranges_enabled:
        # Config changed after this job was deferred -- nothing to do.
        return
    with OrmSession(engine) as session:
        iucn_ranges.fetch_pending(session)
```
and in `run_ingest_cycle`, directly after the `enqueue_region_resolution(engine, poiidx_database_url=config.poiidx_database_url,)` call (still inside the `with OrmSession(...)` block), add:
```python

        from fledermap.services.iucn_ranges import enqueue_range_fetch

        enqueue_range_fetch(session, engine, enabled=config.iucn_ranges_enabled)
```
Read the surrounding lines first and keep the new call at the same indentation as `enqueue_region_resolution` (CLAUDE.md: every session use stays inside its `with`).

- [ ] **Step 4: Run tests**

Run (unsandboxed): `hatch test tests/test_iucn_ranges_service.py tests/test_jobs_tasks.py`
Expected: PASS.

- [ ] **Step 5: Format, type-check, commit**

Run: `hatch fmt` then `hatch run types:check`.

```bash
git add src/fledermap/jobs/tasks.py src/fledermap/services/iucn_ranges.py tests/test_iucn_ranges_service.py tests/test_jobs_tasks.py
git commit -m "feat: fetch IUCN ranges from the worker when enabled"
```

---

### Task 8: Attach ranges to the matrix and wire the setting through the app

**Files:**
- Modify: `src/fledermap/services/current_best.py` (`MatrixRow`, `IdentificationMatrix`)
- Modify: `src/fledermap/services/iucn_ranges.py` (append `attach_ranges`)
- Modify: `src/fledermap/web/app.py` (`create_app` parameters + `app.config`)
- Modify: `src/fledermap/cli/main.py` (`serve`'s `create_app(...)` call)
- Modify: `src/fledermap/web/views/map.py` (~line 139), `src/fledermap/web/views/recording_detail.py` (`_current_classification`)
- Test: `tests/test_iucn_ranges_service.py` (append), `tests/test_current_best.py` (append)

**Interfaces:**
- Consumes: `range_status` (Task 6), `RangeStatus`, `IUCN_CITATION`.
- Produces: `MatrixRow.iucn_range: RangeStatus | None = None`; `IdentificationMatrix.show_range: bool = False`, `IdentificationMatrix.range_citation: str = ""`; `attach_ranges(matrix, session, recording, *, edge_km) -> IdentificationMatrix`; `create_app(..., iucn_ranges_enabled: bool = False, iucn_range_edge_km: float = 100.0)` setting `app.config["IUCN_RANGES_ENABLED"]` / `["IUCN_RANGE_EDGE_KM"]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_current_best.py`:

```python
def test_a_matrix_has_no_range_column_by_default() -> None:
    r = _recording(_ident(IdSource.BATDETECT2))
    best = current_best_identification(r)

    matrix = identification_matrix(r.identifications, best, combine_emt=True)

    assert matrix.show_range is False
    assert matrix.range_citation == ""
    assert all(row.iucn_range is None for row in matrix.rows)
```

Append to `tests/test_iucn_ranges_service.py` (add `from fledermap.services.current_best import current_best_identification, identification_matrix` and `from fledermap.domain.ranges import IUCN_CITATION` to the top imports):

```python
def _matrix(session: OrmSession, recording: Recording) -> iucn_ranges.IdentificationMatrix:
    session.refresh(recording)
    best = current_best_identification(recording)
    return identification_matrix(recording.identifications, best, combine_emt=True)


def test_attach_ranges_fills_species_rows_and_marks_the_matrix(engine: Engine) -> None:
    with OrmSession(engine) as session:
        taxon, recording = _with_range(session, [NATIVE_SQUARE], lon=0.5)
        _identify(session, recording, taxon)
        session.commit()

        matrix = iucn_ranges.attach_ranges(
            _matrix(session, recording),
            session,
            recording,
            edge_km=100.0,
        )

    assert matrix.show_range is True
    assert matrix.range_citation == IUCN_CITATION
    [row] = matrix.rows
    assert row.iucn_range is not None
    assert row.iucn_range.kind is RangeKind.INSIDE


def test_attach_ranges_leaves_group_and_unmapped_rows_blank(engine: Engine) -> None:
    with OrmSession(engine) as session:
        recording = _recording(session)
        genus = _taxon(session, "Myotis", code="Myotis", rank="genus")
        _identify(session, recording, genus)
        session.add(
            Identification(
                recording_id=recording.id,
                source=IdSource.EMT_GUANO,
                verdict=Verdict.SPECIES,
                taxon_id=None,
                raw_label="UNKNWN",
                first_seen_at=datetime(2026, 10, 4, tzinfo=UTC),
            ),
        )
        session.commit()

        matrix = iucn_ranges.attach_ranges(
            _matrix(session, recording),
            session,
            recording,
            edge_km=100.0,
        )

    assert matrix.show_range is True
    assert [row.iucn_range for row in matrix.rows] == [None, None]
```
(`iucn_ranges.IdentificationMatrix` is re-exported by the import below in Step 3.)

- [ ] **Step 2: Run to verify failure**

Run (unsandboxed): `hatch test tests/test_current_best.py tests/test_iucn_ranges_service.py -m "not db or db" -k "range"`
Expected: FAIL (`AttributeError: 'IdentificationMatrix' object has no attribute 'show_range'`).

- [ ] **Step 3: Implement**

In `src/fledermap/services/current_best.py`: add `from fledermap.domain.ranges import RangeStatus` to the imports; in `MatrixRow` add as the last field (after `cells: dict[str, MatrixCell]`):
```python
    # Filled by `services/iucn_ranges.py`'s `attach_ranges` when the range check is enabled;
    # None means "not applicable" (a genus/group row, an unmapped or sentinel claim) and renders
    # as a blank cell, distinct from a RangeStatus of kind NO_DATA.
    iucn_range: RangeStatus | None = None
```
and in `IdentificationMatrix` add:
```python
    show_range: bool = False
    range_citation: str = ""
```
(Both dataclasses are frozen; the defaults keep every existing positional construction valid.)

Append to `src/fledermap/services/iucn_ranges.py` (add `import dataclasses`, `IUCN_CITATION` to the domain import, and `from fledermap.services.current_best import IdentificationMatrix`):
```python
def attach_ranges(
    matrix: IdentificationMatrix,
    session: OrmSession,
    recording: Recording,
    *,
    edge_km: float,
) -> IdentificationMatrix:
    """The matrix with an `iucn` range status on each species row. Kept out of
    `identification_matrix` so that stays a pure function of the identifications; this needs a
    session. Rows that are not a species with a resolved taxon keep `iucn_range=None` (blank cell)."""
    rows = [
        dataclasses.replace(
            row,
            iucn_range=range_status(session, row.taxon.id, recording, edge_km=edge_km),
        )
        if row.taxon is not None and row.taxon.rank == "species"
        else row
        for row in matrix.rows
    ]
    return dataclasses.replace(
        matrix,
        rows=rows,
        show_range=True,
        range_citation=IUCN_CITATION,
    )
```

In `src/fledermap/web/app.py`: add parameters after `regions_enabled: bool = False,`:
```python
    iucn_ranges_enabled: bool = False,
    iucn_range_edge_km: float = 100.0,
```
extend the docstring with one line (`iucn_ranges_enabled`/`iucn_range_edge_km` are `Config.iucn_ranges_enabled`/`.iucn_range_edge_km`, exposed on `app.config` for the matrix views), and after `app.config["REGIONS_ENABLED"] = regions_enabled` add:
```python
    app.config["IUCN_RANGES_ENABLED"] = iucn_ranges_enabled
    app.config["IUCN_RANGE_EDGE_KM"] = iucn_range_edge_km
```
In `src/fledermap/cli/main.py`, in `serve`'s `create_app(...)` call, after `config.poiidx_database_url is not None,` add:
```python
        config.iucn_ranges_enabled,
        config.iucn_range_edge_km,
```

In `src/fledermap/web/views/recording_detail.py`, `_current_classification`: after the `matrix = identification_matrix(...)` statement add
```python
    if flask.current_app.config["IUCN_RANGES_ENABLED"]:
        matrix = attach_ranges(
            matrix,
            session,
            recording,
            edge_km=flask.current_app.config["IUCN_RANGE_EDGE_KM"],
        )
```
and import `from fledermap.services.iucn_ranges import attach_ranges`. In `src/fledermap/web/views/map.py`, read lines ~120-160 first; directly after the `matrix = identification_matrix(...)` statement (still inside its `with OrmSession(...)` block, same indentation) add the same `if flask.current_app.config["IUCN_RANGES_ENABLED"]:` block (using the `recording` and `session` already in scope there) and the same import.

- [ ] **Step 4: Run tests**

Run (unsandboxed): `hatch test tests/test_current_best.py tests/test_iucn_ranges_service.py tests/test_map_view.py tests/test_recording_detail_view.py tests/test_cli.py`
Expected: PASS.

- [ ] **Step 5: Format, type-check, commit**

Run: `hatch fmt` then `hatch run types:check`.

```bash
git add src/fledermap/services/current_best.py src/fledermap/services/iucn_ranges.py src/fledermap/web/app.py src/fledermap/cli/main.py src/fledermap/web/views/map.py src/fledermap/web/views/recording_detail.py tests/test_current_best.py tests/test_iucn_ranges_service.py
git commit -m "feat: attach IUCN range status to the identifications matrix"
```

---

### Task 9: The `iucn` column — template, icons, colour token, style guide

**Files:**
- Modify: `src/fledermap/web/templates/_identification_matrix.html`
- Modify: `src/fledermap/web/static/app.css` (`--color-danger` in `:root` and both dark blocks; matrix range rules)
- Modify: `src/fledermap/services/vendor_assets.py` (three new Tabler icons; `alert-triangle` is already vendored)
- Modify: `docs/style-guide.md`
- Test: `tests/test_iucn_matrix_view.py` (db), `tests/test_css_tokens.py` (no DB; check the file name does not already exist, else append to the existing CSS test file)

**Interfaces:**
- Consumes: `IdentificationMatrix.show_range`, `.range_citation`, `MatrixRow.iucn_range`, `RangeStatus.describe()`, `RangeKind` values.
- Produces: markup contract — header `th.range-column` (text `iucn`, `title` = citation); per-row `td.identification-cell.range-cell` with class `range-<kind>` (`range-inside|range-near|range-far|range-no_data`) containing `<span role="img" title=… aria-label=…>` + the icon, blank when `row.iucn_range` is None; a `<p class="range-caption">` carrying the citation under the table.

- [ ] **Step 1: Write the failing tests**

`tests/test_iucn_matrix_view.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import flask
import pytest
from geoalchemy2.elements import WKTElement
from sqlalchemy import Engine
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import IdSource, Verdict
from fledermap.domain.ranges import RANGE_CODE_SOURCE
from fledermap.services import iucn_ranges
from fledermap.services.iucn_ranges import RangePolygon
from fledermap.store.models import Identification, Recording, Taxon, TaxonCode
from fledermap.web.app import create_app

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("_vendor_icons")]

SQUARE = RangePolygon(
    presence=1,
    origin=1,
    wkt="MULTIPOLYGON(((0 0, 0 1, 1 1, 1 0, 0 0)))",
)


def _seed(engine: Engine, *, lon: float) -> str:
    audio_hash = "ab" * 32
    with OrmSession(engine) as session:
        taxon = Taxon(rank="species", scientific_name="Pipistrellus pipistrellus")
        session.add(taxon)
        session.flush()
        session.add(
            TaxonCode(
                source=RANGE_CODE_SOURCE,
                code="Pipistrellus pipistrellus",
                taxon_id=taxon.id,
            ),
        )
        recording = Recording(
            audio_hash=audio_hash,
            path="a.wav",
            recorded_at=datetime(2026, 10, 4, tzinfo=UTC),
            duration_s=0.5,
            samplerate_hz=256_000,
            geom=WKTElement(f"POINT({lon} 0.5)", srid=4326),
        )
        session.add(recording)
        session.flush()
        session.add(
            Identification(
                recording_id=recording.id,
                source=IdSource.BATDETECT2,
                verdict=Verdict.SPECIES,
                taxon_id=taxon.id,
                first_seen_at=datetime(2026, 10, 4, tzinfo=UTC),
            ),
        )
        iucn_ranges.store_range(session, taxon.id, "Pipistrellus pipistrellus", [SQUARE])
        session.commit()
    return audio_hash


def _page(
    engine: Engine,
    tmp_path: Path,
    audio_hash: str,
    *,
    iucn_ranges_enabled: bool = False,
) -> str:
    app = create_app(
        engine,
        tmp_path / "static",
        tmp_path / "media",
        iucn_ranges_enabled=iucn_ranges_enabled,
    )
    response = app.test_client().get(f"/recordings/{audio_hash}")
    assert response.status_code == 200
    return response.get_data(as_text=True)


@pytest.mark.parametrize(
    ("lon", "css_class", "text"),
    [
        (0.5, "range-inside", "Inside the IUCN range"),
        (1.5, "range-near", "km outside the IUCN range"),
        (6.0, "range-far", "km outside the IUCN range"),
    ],
)
def test_details_page_shows_the_range_cell_for_each_state(
    engine: Engine, tmp_path: Path, lon: float, css_class: str, text: str
) -> None:
    audio_hash = _seed(engine, lon=lon)

    html = _page(engine, tmp_path, audio_hash, iucn_ranges_enabled=True)

    assert "range-column" in html
    assert css_class in html
    assert text in html
    assert "release 2022-2" in html
    assert "non-commercial" in html


def test_the_iucn_column_is_absent_when_the_flag_is_off(
    engine: Engine, tmp_path: Path
) -> None:
    audio_hash = _seed(engine, lon=0.5)

    html = _page(engine, tmp_path, audio_hash)

    assert "range-column" not in html
    assert "range-cell" not in html
    assert "release 2022-2" not in html


def test_no_range_geometry_reaches_the_page(engine: Engine, tmp_path: Path) -> None:
    """IUCN T&C §4: the page may carry a verdict and a distance, never a polygon."""
    audio_hash = _seed(engine, lon=0.5)

    html = _page(engine, tmp_path, audio_hash, iucn_ranges_enabled=True)

    assert "MULTIPOLYGON" not in html.upper()
    assert "0 0, 0 1, 1 1" not in html  # the synthetic square's vertices


def test_a_species_without_data_gets_the_no_data_cell(
    engine: Engine, tmp_path: Path
) -> None:
    audio_hash = _seed(engine, lon=0.5)
    with OrmSession(engine) as session:
        from sqlalchemy import delete

        from fledermap.store.models import SpeciesRangeFetch

        session.execute(delete(SpeciesRangeFetch))
        session.commit()

    html = _page(engine, tmp_path, audio_hash, iucn_ranges_enabled=True)

    assert "range-no_data" in html
    assert "IUCN range not fetched yet" in html


def test_the_drawer_panel_shows_the_column_too(engine: Engine, tmp_path: Path) -> None:
    """Drawer/detail parity: both views include the same matrix partial."""
    audio_hash = _seed(engine, lon=0.5)
    app: flask.Flask = create_app(
        engine, tmp_path / "static", tmp_path / "media", iucn_ranges_enabled=True
    )

    response = app.test_client().get(f"/recordings/{audio_hash}/panel")

    assert response.status_code == 200
    assert "range-inside" in response.get_data(as_text=True)
```
(`/recordings/<audio_hash>/panel` is the drawer route, `views_bp.get` in `web/views/map.py`. If it needs filter query parameters to find the recording, pass the same ones the existing `tests/test_map_view.py` panel tests do.)

`tests/test_css_tokens.py`:

```python
from __future__ import annotations

import re
from pathlib import Path

APP_CSS = Path(__file__).parent.parent / "src" / "fledermap" / "web" / "static" / "app.css"


def test_color_danger_is_defined_in_light_and_both_dark_blocks() -> None:
    css = APP_CSS.read_text(encoding="utf-8")
    assert len(re.findall(r"--color-danger:\s*#[0-9a-fA-F]{6};", css)) == 3
```

- [ ] **Step 2: Run to verify failure**

Run (unsandboxed): `hatch test tests/test_iucn_matrix_view.py tests/test_css_tokens.py`
Expected: FAIL.

- [ ] **Step 3: Add the icons to `vendor_assets.py`**

First `grep -n "circle-check\|icons/outline/ban\|help-circle\|alert-triangle" src/fledermap/services/vendor_assets.py`. `alert-triangle` is already present (verify its sha256 is `92a951d8e90c1b1d986449e74a8b2e1c52982dc3dbe09b141ac11990420408f4`). For each of the other three that is absent, add (next to the existing Tabler entries, same shape, hashes computed 2026-10-04 from the pinned 3.46.0 files):

```python
    VendorAsset(
        url="https://unpkg.com/@tabler/icons@3.46.0/icons/outline/circle-check.svg",
        sha256="321fb0a26939c8bb1674e8a62c90d0538db23cbb9c46d4c1cce25e0ea313d5bd",
        relative_path="icons/outline/circle-check.svg",
    ),
    VendorAsset(
        url="https://unpkg.com/@tabler/icons@3.46.0/icons/outline/ban.svg",
        sha256="6397bee6c13fc647fa58eb4767318d695cc15c96dcadb978e99446b1cd5ac06c",
        relative_path="icons/outline/ban.svg",
    ),
    VendorAsset(
        url="https://unpkg.com/@tabler/icons@3.46.0/icons/outline/help-circle.svg",
        sha256="74ae26c8029b6aa824a688207a5c3cd54e08b611dc35e3faa593165cab48517b",
        relative_path="icons/outline/help-circle.svg",
    ),
```
(Re-verify the three hashes before trusting them: `curl -s https://unpkg.com/@tabler/icons@3.46.0/icons/outline/ban.svg | sha256sum`, etc.)

- [ ] **Step 4: Template** — `src/fledermap/web/templates/_identification_matrix.html`

Replace the `<thead>` row and the per-row cell loop's end, and add the caption, so the file's body becomes (keep the existing leading `{# … #}` comment, and append one sentence to it: *"The optional `iucn` column (services/iucn_ranges.py's `attach_ranges`) is a species-row property at the recording's location, not a classifier claim, so it is visually separated and never uses a check mark."*):

```jinja
{% set range_icons = {
  "inside": "circle-check",
  "near": "alert-triangle",
  "far": "ban",
  "no_data": "help-circle",
} %}
<table class="identification-matrix">
  <thead>
    <tr>
      <th scope="col"></th>
      {% for column in matrix.columns %}
      <th scope="col" title="{{ column.key }}">{{ column.label }}</th>
      {% endfor %}
      {% if matrix.show_range %}
      <th scope="col" class="range-column" title="{{ matrix.range_citation }}">iucn</th>
      {% endif %}
    </tr>
  </thead>
  <tbody>
    {% for row in matrix.rows %}
    <tr>
      <th scope="row" class="{{ 'identification-current' if row.is_current else '' }}">
        {% if row.taxon %}<a href="/species/{{ row.taxon.id }}">{{ row.label }}</a>{% else %}{{ row.label }}{% endif %}
      </th>
      {% for column in matrix.columns %}
      {% set cell = row.cells[column.key] %}
      <td class="identification-cell {{ 'identification-' ~ cell.status if cell.status else '' }}">
        {% if cell.ident %}{{ icon("check") }}{% endif %}
      </td>
      {% endfor %}
      {% if matrix.show_range %}
      <td class="identification-cell range-cell{{ (' range-' ~ row.iucn_range.kind.value) if row.iucn_range else '' }}">
        {% if row.iucn_range %}
        {% set description = row.iucn_range.describe() %}
        <span role="img" title="{{ description }}" aria-label="{{ description }}">{{ icon(range_icons[row.iucn_range.kind.value]) }}</span>
        {% endif %}
      </td>
      {% endif %}
    </tr>
    {% endfor %}
  </tbody>
</table>
{% if matrix.show_range %}
<p class="detail-meta range-caption">Range: {{ matrix.range_citation }}.</p>
{% endif %}
```

- [ ] **Step 5: CSS** — `src/fledermap/web/static/app.css`

Add `--color-danger: #c53030;` to `:root` (after `--color-success: #2f855a;`) and `--color-danger: #fc8181;` to **both** dark blocks (after their `--color-success: #48bb78;`). After the `.identification-cell.identification-passive .icon, … { color: var(--color-muted); }` rule add:

```css
/* The optional `iucn` column: a species-row property at the recording's location, not a
   classifier claim. The heavier left border separates it from the claim columns so its icons
   cannot be read as "this source says so". Meaning is carried by icon shape; colour only
   reinforces it. `.identification-cell .icon` above is (0,2,0), so these are (0,3,0). */
.identification-matrix th.range-column,
.identification-matrix td.range-cell { border-left: 2px solid var(--color-border); }
.identification-cell.range-inside .icon { color: var(--color-success); }
.identification-cell.range-near .icon { color: var(--color-warning); }
.identification-cell.range-far .icon { color: var(--color-danger); }
.identification-cell.range-no_data .icon { color: var(--color-muted); }
.range-caption { font-size: 0.75rem; margin: 0.3rem 0 0; }
```

- [ ] **Step 6: Style guide** — `docs/style-guide.md`

Add a row to the colour-token table: `| \`--color-danger\` | \`#c53030\` | \`#fc8181\` | The "far outside the IUCN range" forbid icon in the matrix's \`iucn\` column — see "Identifications matrix" below. No existing destructive control uses it |`. Add a new subsection under "Shared classes":

```markdown
### Identifications matrix: the `iucn` range column

`_identification_matrix.html` (shared by the drawer and the details page) can carry one extra,
rightmost column, `iucn`, when `FLEDERMAP_IUCN_RANGES_ENABLED` is on. It is a property of the
**species row at the recording's location**, not a classifier claim, so: it is separated from the
claim columns by a heavier left border, it never uses a check mark, and it never changes the
current-row highlighting. Four states, each by icon *shape* (colour only reinforces it):
`circle-check` inside (`--color-success`), `alert-triangle` near (`--color-warning`), `ban` far
(`--color-danger`), `help-circle` no data (`--color-muted`). A **blank** cell means not
applicable (genus/group rows, unmapped or NoID/noise rows) and is deliberately different from
`help-circle`, which means a species row the check could not answer; its tooltip says why. The
tooltip and a caption under the table carry the IUCN citation (release 2022-2, non-commercial
use only, T&C v3.1 §§3, 6). A map-pin icon family was tried and rejected: at the matrix's real
size (~13.6 px) the pin badges were unreadable and the states differed only by colour.
```

- [ ] **Step 7: Run tests**

Run (unsandboxed): `hatch test tests/test_iucn_matrix_view.py tests/test_css_tokens.py tests/test_vendor_assets.py tests/test_entities_view.py tests/test_map_view.py tests/test_recording_detail_view.py tests/test_layout.py tests/test_cli.py`
Expected: PASS. `test_vendor_assets.py` checks that every icon a template names is vendored and that `ASSETS` has no duplicates; the four-state view tests above also fail loudly with `IconNotFoundError` if one of the four range icons is missing from `ASSETS`, because the `_vendor_icons` fixture writes icons from that inventory.

- [ ] **Step 8: Live legibility check (headless Chrome; mandatory for UI per project rules)**

Start a throwaway instance against a database with a recording that has identifications, with `FLEDERMAP_IUCN_RANGES_ENABLED=true` (seed one `species_range` row via a scratchpad script using `store_range` with a synthetic polygon), e.g. `FLEDERMAP_PORT=5099 hatch run fledermap serve` (unsandboxed). Drive headless Chrome (see the project memory note on the live-verification technique; `puppeteer-core`, `executablePath: '/usr/bin/google-chrome'`, fresh `userDataDir`, `setCacheEnabled(false)`) and confirm, in light **and** dark (`data-theme`), on the details page and in the map drawer: the four icons are distinguishable at the rendered size, the `iucn` column sits right of the classifier columns with its separator, the blank cell for a genus row is blank, tooltips read correctly, and no horizontal scrollbar appears. Screenshot to the scratchpad and read it back. Stop the instance afterwards.

- [ ] **Step 9: Format, type-check, commit**

Run: `hatch fmt` then `hatch run types:check`.

```bash
git add src/fledermap/web/templates/_identification_matrix.html src/fledermap/web/static/app.css src/fledermap/services/vendor_assets.py docs/style-guide.md tests/test_iucn_matrix_view.py tests/test_css_tokens.py
git commit -m "feat: show the IUCN range check as an iucn column in the matrix"
```

---

### Task 10: Reference docs and final verification

**Files:**
- Modify: `docs/references.md`, `CLAUDE.md`
- Modify: `docs/superpowers/specs/2026-10-04-fledermap-iucn-range-check-design.md` (status line)

- [ ] **Step 1: `docs/references.md`**

Add to the "Species codes and names" table a row, and a short subsection below it:

```markdown
| [IUCN Red List species range layer (UNEP-WCMC-hosted, release 2022-2)](https://data-gis.unep-wcmc.org/server/rest/services/Hosted/IUCN_Red_List/MapServer/0) | The `iucn2022` names in `taxa_eu.yaml`/`taxa_na.yaml` and the range polygons behind the optional range check. | Layer `IUCN_RL_2022_2_Species_Ranges`; queried by `sci_name`. IUCN still files the three Old World *Cnephaeus* species under *Eptesicus*. `presence` 1/2/3/6 kept (legend: Extant / Probably Extant / Possibly Extant / Presence Uncertain); `origin` 4 = vagrant; `seasonal` is 1 (resident) for all our bats bar two polygons with 5, so ignored. Metadata carries no licence — IUCN's own terms apply: [Terms and Conditions of Use v3.1](https://www.iucnredlist.org/terms/terms-of-use) (non-commercial only §3, no redistribution §4, cite the version §6). Never commit a response from it. Checked 2026-10-04. |
```

- [ ] **Step 2: `CLAUDE.md`**

In the "Species codes" section add a paragraph: *"`taxa_eu.yaml`/`taxa_na.yaml` species also carry an `iucn2022` code — the name IUCN's 2022-2 range layer files the species under (see `docs/references.md`), used only by the optional range check. It is not a code anyone is identified by: `services/entities.py` and `recording_detail.py`'s search index filter `RANGE_CODE_SOURCE` out, and any new reader of `taxon_code` that shows or searches codes must do the same."* In "Architecture" extend the `services/` bullet with `iucn_ranges.py` (worker-side fetch + cache of IUCN range polygons; `range_status` reduces them to a verdict and distance — geometry never reaches the browser; off by default for licensing reasons, spec 2026-10-04-fledermap-iucn-range-check-design.md).

- [ ] **Step 3: Update the spec status**

Change `**Status:** draft, awaiting review` to `**Status:** implemented`.

- [ ] **Step 4: Full verification (controlling session)**

Run, unsandboxed where noted:
- `hatch fmt` → clean.
- `hatch run types:check` → clean.
- `node --test tests/js/` → unchanged (no JS touched).
- `hatch test` (full, includes `-m db`; unsandboxed, ~2–3 min) → all pass, **no warnings in the output**.
- `hatch build -t wheel` then `python3 -m zipfile -l dist/*.whl | grep -i "species_range\|iucn"` → the migration and `services/iucn_ranges.py` are in the wheel; **no `.geojson`/polygon data files** are (nothing from IUCN ships).
- `git status` → only intended paths; nothing under `tests/data/` that came from the live service.

- [ ] **Step 5: Commit**

```bash
git add docs/references.md CLAUDE.md docs/superpowers/specs/2026-10-04-fledermap-iucn-range-check-design.md
git commit -m "docs: document the IUCN range layer and its code source"
```

- [ ] **Step 6: Deployment note (do not run without being asked)**

Before pointing this at the live database: `scripts/db-backup.sh`, then `pipx install --force .` and restart `fledermap.target` per the systemd-install memory note. Enabling it is a deliberate operator step: set `FLEDERMAP_IUCN_RANGES_ENABLED=true`, and the next ingest cycle queues the first fetch.
