# Fledermap Phase 2 (Derivation) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `fledermap derive` populates `session` (incremental, gap-based) and a new `site`
table (wholesale DBSCAN rebuild) from what `fledermap ingest` already stored. Still
headless — no web, no site naming.

**Architecture:** `derive/sessions.py` walks unsessioned recordings per detector
`(make, serial)` and assigns/extends sessions by a time-gap rule, flagging
already-persisted sessions a late recording would bridge. `derive/sites.py` clusters
stationary, GPS-bearing recordings with `sklearn.cluster.DBSCAN` over coordinates
projected into a local metric CRS by a ported `LocalProjection`; `services/derive.py`
wholesale-rebuilds the `site` table from the result, using a ported `GeoCluster` to
summarise each cluster.

**Tech Stack:** SQLAlchemy 2.0 + GeoAlchemy2 + Alembic (existing), `scikit-learn`,
`numpy`, `scipy`, `shapely`, `pyproj` (new — DBSCAN, `LocalProjection`, `GeoCluster`).

**Spec:** `docs/superpowers/specs/2026-08-24-fledermap-phase2-derivation-design.md`
(this phase's design — read this first); binding parent decisions in
`docs/superpowers/specs/2026-08-23-fledermap-design.md` sections 4, 7, 12, 15, 16.

## Global Constraints

- **Ingest is strictly read-only on the archive (parent spec D16).** Nothing in this
  plan touches archive files; derivation only reads and writes `bats_db`.
- **`hatch` only.** Never `pip`, never a bare `python`/`python3`, never `PYTHONPATH`.
  `hatch test`, `hatch fmt`, `hatch run types:check`.
- **Test output must be pristine** — a warning is a defect, fix the cause, never
  `filterwarnings`.
- **`hatch run types:check` covers `tests/` too** — test code must type-check for real
  (bind `X | None`, assert not None, dereference — never `# type: ignore`).
- **New third-party imports mypy can't resolve go in `[tool.hatch.envs.types]`'s
  `extra-dependencies`** — never a global `ignore_missing_imports`.
- **`db`-marked tests need Docker, which the command sandbox blocks** — run with
  `dangerouslyDisableSandbox: true`. Failure looks like
  `requests.exceptions.ConnectionError: PermissionError(1, 'Operation not permitted')`
  out of `docker.from_env()`, not an obvious sandbox message.
- **A closed vocabulary gets a `StrEnum` in `domain/codes.py` and a CHECK constraint**
  (`native_enum=False, create_constraint=True, values_callable=lambda e: [m.value for m
  in e]`) — matches `Verdict`'s existing pattern. An open, still-growing vocabulary
  (more classifiers expected) stays a plain column with `create_constraint=False`,
  matching `Identification.source`.
- **Every new `create_constraint=True` enum column needs its own pair of migration
  tests** — one proving the CHECK rejects a bogus value, one proving it accepts every
  current enum member — mirroring `test_migrated_verdict_check_is_enforced` /
  `test_migrated_verdict_check_accepts_every_verdict` in `tests/test_migrations.py`.
  `_comparable`'s exclusion there is already generic (keyed by `col.type.name`, not a
  hardcoded list) — no change needed to that function itself for a new enum, but the
  per-column enforcement tests are not automatic and must be added explicitly.
- **A wholesale rebuild of `site` uses `DELETE FROM site`, never `TRUNCATE`.** Postgres
  `TRUNCATE` does not fire `ON DELETE SET NULL` the way `DELETE` does — it either
  errors on the referencing `recording.site_id` FK or, with `CASCADE`, truncates
  `recording` too, which must never happen.
- **`eps` for DBSCAN must be in metres, projected via `LocalProjection` first** — raw
  EPSG:4326 coordinates are degrees, and a 75 m radius silently becomes ~8 km if
  clustered directly (parent spec section 7's pinned pitfall).
- **Prefer a well-tested library over hand-rolled code** wherever one fits — this
  phase leans on `numpy`/`scikit-learn`/`shapely`/`pyproj` throughout rather than
  reimplementing projection, clustering, or geometry decoding.

---

### Task 1: Shared geometry decoding — `store/geo.py`

Phase 1's `services/ingest.py` hand-decodes WKB into `(lon, lat)` specifically to avoid
needing `geoalchemy2.shape.to_shape` (which needs shapely) — its own docstring says
"shapely... which nothing else in this project requires." This phase makes shapely a
hard dependency regardless (`LocalProjection`/`GeoCluster`, Tasks 3–4), so that
rationale no longer holds. Promote the decoding to a shared module and switch it to
`to_shape` now, before other tasks need it, so only Phase-1 code is touched by this one
change (not tangled into a new Phase-2 file's diff, per user decision on 2026-08-24).

**Files:**
- Create: `src/fledermap/store/geo.py`
- Modify: `src/fledermap/services/ingest.py` (remove `_decode_point`,
  `_WKB_SRID_FLAG`, `import struct`, the `WKBElement` import; use the shared helper)
- Test: `tests/test_store_geo.py`

**Interfaces:**
- Produces: `decode_point(elem: object | None) -> tuple[float, float] | None` in
  `fledermap.store.geo` — decodes a stored `Geography` Point into `(lon, lat)`, or
  `None` if `elem` isn't a `WKBElement`. Used by Task 1's own refactor and by Task 9's
  `services/derive.py`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_store_geo.py
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from geoalchemy2.elements import WKTElement
from sqlalchemy import Engine
from sqlalchemy.orm import Session as OrmSession

from fledermap.store.geo import decode_point
from fledermap.store.models import Recording

pytestmark = pytest.mark.db


def test_decode_point_round_trips_through_the_database(engine: Engine) -> None:
    with OrmSession(engine) as session:
        recording = Recording(
            audio_hash="d" * 64,
            path="x.wav",
            recorded_at=datetime(2026, 8, 21, 21, tzinfo=UTC),
            geom=WKTElement("POINT(13.4 52.5)", srid=4326),
        )
        session.add(recording)
        session.commit()
        session.refresh(recording)

        decoded = decode_point(recording.geom)

        assert decoded is not None
        lon, lat = decoded
        assert lon == pytest.approx(13.4)
        assert lat == pytest.approx(52.5)


def test_decode_point_returns_none_for_no_geometry() -> None:
    assert decode_point(None) is None


def test_decode_point_returns_none_for_a_non_wkb_value() -> None:
    assert decode_point("not a point") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `hatch test tests/test_store_geo.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'fledermap.store.geo'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/fledermap/store/geo.py
"""Shared geometry decoding, used by both `services/ingest.py` and
`services/derive.py`.

Uses `geoalchemy2`'s own `to_shape` (needs shapely) rather than hand-parsing WKB.
Phase 1's `services/ingest.py` avoided this specifically because shapely was not
otherwise a dependency; Phase 2 makes it one regardless (`LocalProjection`,
`GeoCluster`), so that reason no longer applies.
"""

from __future__ import annotations

from geoalchemy2.elements import WKBElement
from geoalchemy2.shape import to_shape


def decode_point(elem: object | None) -> tuple[float, float] | None:
    """Decode a stored geography Point into (lon, lat), or None if absent."""
    if not isinstance(elem, WKBElement):
        return None
    point = to_shape(elem)
    return (point.x, point.y)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `hatch test tests/test_store_geo.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Refactor `services/ingest.py` to use the shared helper**

In `src/fledermap/services/ingest.py`:
- Remove `import struct` (no longer used anywhere else in the file — confirm with
  `grep -n struct src/fledermap/services/ingest.py` before deleting; it should show
  only the import line and the two `struct.unpack_from` calls being removed).
- Remove `_WKB_SRID_FLAG = 0x20000000`.
- Remove the whole `_decode_point` function.
- Change `from geoalchemy2.elements import WKBElement, WKTElement` to
  `from geoalchemy2.elements import WKTElement` (WKBElement is no longer referenced
  as code anywhere in this file — it's only mentioned in a docstring prose sentence
  in `_apply_metadata`, which stays as prose).
- Add `from fledermap.store.geo import decode_point` to the imports.
- In `_position_changed`, change `_decode_point(recording.geom)` to
  `decode_point(recording.geom)`.

- [ ] **Step 6: Run the full existing ingest test suite to confirm nothing broke**

Run: `hatch test tests/test_ingest_service.py -v`
Expected: PASS, same count as before the refactor — this is a pure rename/relocation,
no behavior change.

- [ ] **Step 7: Type-check and lint**

Run: `hatch run types:check`
Run: `hatch fmt`
Expected: both clean.

- [ ] **Step 8: Commit**

```bash
git add src/fledermap/store/geo.py src/fledermap/services/ingest.py tests/test_store_geo.py
git commit -m "refactor: share geometry decoding via store/geo.py, switch to to_shape

Phase 1's hand-rolled WKB decoder existed to avoid a shapely dependency that
Phase 2 (LocalProjection, GeoCluster) makes unavoidable anyway."
```

---

### Task 2: Config additions — `session_gap_hours`, `site_eps_m`, `site_min_points`

**Files:**
- Modify: `src/fledermap/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `Config.session_gap_hours: float` (default `6.0`), `Config.site_eps_m: float`
  (default `75.0`), `Config.site_min_points: int` (default `3`) — read by `Config.from_env`
  from `FLEDERMAP_SESSION_GAP_HOURS`, `FLEDERMAP_SITE_EPS_M`, `FLEDERMAP_SITE_MIN_POINTS`.
  Consumed by Task 10's CLI wiring.

- [ ] **Step 1: Write the failing tests**

```python
# add to tests/test_config.py

def test_default_session_gap_is_six_hours(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.delenv(ENV_SESSION_GAP_HOURS, raising=False)
    config = Config.from_env(tmp_path)
    assert config.session_gap_hours == 6.0


def test_session_gap_is_configurable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_SESSION_GAP_HOURS, "4.5")
    config = Config.from_env(tmp_path)
    assert config.session_gap_hours == 4.5


def test_invalid_session_gap_raises_config_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_SESSION_GAP_HOURS, "not-a-number")
    with pytest.raises(ConfigError, match="not-a-number"):
        Config.from_env(tmp_path)


def test_default_site_eps_and_min_points(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.delenv(ENV_SITE_EPS_M, raising=False)
    monkeypatch.delenv(ENV_SITE_MIN_POINTS, raising=False)
    config = Config.from_env(tmp_path)
    assert config.site_eps_m == 75.0
    assert config.site_min_points == 3


def test_site_eps_and_min_points_are_configurable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_SITE_EPS_M, "50")
    monkeypatch.setenv(ENV_SITE_MIN_POINTS, "5")
    config = Config.from_env(tmp_path)
    assert config.site_eps_m == 50.0
    assert config.site_min_points == 5


def test_invalid_site_min_points_raises_config_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_SITE_MIN_POINTS, "not-an-int")
    with pytest.raises(ConfigError, match="not-an-int"):
        Config.from_env(tmp_path)
```

Add the three new env var names to the existing import line at the top of the test file:

```python
from fledermap.config import (
    ENV_DATABASE_URL,
    ENV_DEFAULT_TIMEZONE,
    ENV_SESSION_GAP_HOURS,
    ENV_SITE_EPS_M,
    ENV_SITE_MIN_POINTS,
    ENV_TIMESTAMP_SOURCE,
    Config,
    ConfigError,
)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `hatch test tests/test_config.py -v`
Expected: FAIL — `ImportError: cannot import name 'ENV_SESSION_GAP_HOURS'`

- [ ] **Step 3: Implement**

In `src/fledermap/config.py`, add the three env var names alongside the existing ones:

```python
ENV_SESSION_GAP_HOURS = "FLEDERMAP_SESSION_GAP_HOURS"
ENV_SITE_EPS_M = "FLEDERMAP_SITE_EPS_M"
ENV_SITE_MIN_POINTS = "FLEDERMAP_SITE_MIN_POINTS"
```

Add three fields to `Config` (after `default_timezone`):

```python
    # Spec section 7: "tuning is free" — eps, minpoints and session_gap are config,
    # and site rebuilding is idempotent.
    session_gap_hours: float = 6.0
    site_eps_m: float = 75.0
    site_min_points: int = 3
```

In `Config.from_env`, add parsing for all three, following the existing
`default_timezone` pattern of "absent -> default, present -> parse, parse failure ->
`ConfigError` naming the bad value":

```python
        session_gap_raw = os.environ.get(ENV_SESSION_GAP_HOURS)
        if session_gap_raw is None:
            session_gap_hours = 6.0
        else:
            try:
                session_gap_hours = float(session_gap_raw)
            except ValueError as exc:
                msg = (
                    f"{ENV_SESSION_GAP_HOURS}={session_gap_raw!r} is not a number "
                    "of hours."
                )
                raise ConfigError(msg) from exc

        site_eps_raw = os.environ.get(ENV_SITE_EPS_M)
        if site_eps_raw is None:
            site_eps_m = 75.0
        else:
            try:
                site_eps_m = float(site_eps_raw)
            except ValueError as exc:
                msg = f"{ENV_SITE_EPS_M}={site_eps_raw!r} is not a number of metres."
                raise ConfigError(msg) from exc

        site_min_points_raw = os.environ.get(ENV_SITE_MIN_POINTS)
        if site_min_points_raw is None:
            site_min_points = 3
        else:
            try:
                site_min_points = int(site_min_points_raw)
            except ValueError as exc:
                msg = f"{ENV_SITE_MIN_POINTS}={site_min_points_raw!r} is not an integer."
                raise ConfigError(msg) from exc
```

Update the existing `return cls(...)` call at the end of `from_env` to pass all three
through, alongside the fields already there:

```python
        return cls(
            database_url=url,
            archive_root=archive_root.resolve(),
            timestamp_source=timestamp_source,
            default_timezone=default_timezone,
            session_gap_hours=session_gap_hours,
            site_eps_m=site_eps_m,
            site_min_points=site_min_points,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `hatch test tests/test_config.py -v`
Expected: PASS (all tests, old and new)

- [ ] **Step 5: Type-check and lint**

Run: `hatch run types:check && hatch fmt`

- [ ] **Step 6: Commit**

```bash
git add src/fledermap/config.py tests/test_config.py
git commit -m "feat: add session_gap_hours, site_eps_m, site_min_points to Config"
```

---

### Task 3: Port `LocalProjection` — `util/projection.py`

Ported from `../mkmapdiary/src/mkmapdiary/util/projection.py`, per parent spec section
16 (relicensing MIT is already settled — the owner holds copyright to both projects).
Adds one small addition absent from the original: a public `crs` property, needed to
test which UTM/UPS zone got picked (the original has no way to observe this from
outside the class).

**Files:**
- Create: `src/fledermap/util/__init__.py` (empty)
- Create: `src/fledermap/util/projection.py`
- Test: `tests/test_projection.py`
- Modify: `pyproject.toml` — add `numpy`, `shapely`, `pyproj` to `dependencies`; add the
  same three to `[tool.hatch.envs.types]`'s `extra-dependencies`

**Interfaces:**
- Produces: `fledermap.util.projection.LocalProjection` — constructor takes any object
  with a `.centroid` shapely-style point (a `Point`, `MultiPoint`, etc.); methods
  `to_local_np(np.ndarray) -> np.ndarray`, `to_wgs_np(np.ndarray) -> np.ndarray`,
  `to_local(shape) -> shape`, `to_wgs(shape) -> shape`; property `crs -> CRS`. Consumed
  by Task 4's `GeoCluster` and Task 8's `cluster_points`.

- [ ] **Step 1: Add the new dependencies**

In `pyproject.toml`, add to `dependencies`:

```toml
dependencies = [
  "sqlalchemy>=2.0",
  "geoalchemy2",
  "alembic",
  "psycopg2-binary",
  "click",
  "pyyaml",
  "numpy",
  "shapely",
  "pyproj",
]
```

And to `[tool.hatch.envs.types]`'s `extra-dependencies` (numpy and shapely ship inline
types; pyproj's stubs may or may not resolve — if `hatch run types:check` still fails
to resolve `pyproj` after adding it here, that's the expected trigger to add
`types-pyproj` too, per project convention: never a global `ignore_missing_imports`):

```toml
extra-dependencies = [
  "mypy>=1.0.0",
  "pytest>=7.0.0",
  "testcontainers>=4.15.0",
  "numpy",
  "shapely",
  "pyproj",
]
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_projection.py
from __future__ import annotations

import numpy as np
import pytest
from shapely.geometry import Point

from fledermap.util.projection import LocalProjection


def test_picks_northern_utm_zone_for_berlin() -> None:
    proj = LocalProjection(Point(13.4, 52.5))
    assert proj.crs.to_epsg() == 32633  # WGS84 / UTM zone 33N


def test_picks_southern_utm_zone_for_southern_hemisphere() -> None:
    # Santiago, Chile
    proj = LocalProjection(Point(-70.6, -33.4))
    assert proj.crs.to_epsg() == 32719  # WGS84 / UTM zone 19S


def test_picks_ups_north_above_84_degrees() -> None:
    proj = LocalProjection(Point(0.0, 85.0))
    assert proj.crs.to_epsg() == 32661  # WGS84 / UPS North


def test_picks_ups_south_below_minus_80_degrees() -> None:
    proj = LocalProjection(Point(0.0, -85.0))
    assert proj.crs.to_epsg() == 32761  # WGS84 / UPS South


def test_round_trip_recovers_the_original_coordinates() -> None:
    proj = LocalProjection(Point(13.4, 52.5))
    original = np.array([[13.4, 52.5], [13.41, 52.51]])

    local = proj.to_local_np(original)
    recovered = proj.to_wgs_np(local)

    assert recovered == pytest.approx(original, abs=1e-9)


def test_eps_in_local_metres_is_not_eps_in_degrees() -> None:
    """The pitfall parent spec section 7 pins: raw EPSG:4326 coordinates are
    degrees, not metres. Two points ~75m apart at Berlin's latitude must be
    within 75 units of each other in the LOCAL projection but nowhere near 75
    units apart in raw lon/lat (where 75 would be ~8000km)."""
    proj = LocalProjection(Point(13.4, 52.5))
    points = np.array([[13.4, 52.5], [13.401, 52.5]])  # ~68m apart at this latitude

    local = proj.to_local_np(points)
    local_distance = np.linalg.norm(local[0] - local[1])
    raw_distance = np.linalg.norm(points[0] - points[1])

    assert local_distance == pytest.approx(68.0, abs=5.0)
    assert raw_distance < 0.01  # far under 1 in raw degree units
```

- [ ] **Step 3: Run test to verify it fails**

Run: `hatch test tests/test_projection.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'fledermap.util'`

- [ ] **Step 4: Write the implementation**

```python
# src/fledermap/util/projection.py
"""Local-metre map projection.

Ported from mkmapdiary (`util/projection.py`), MIT-relicensed for this project
(parent spec section 16 — the owner holds copyright to both, so this is settled,
not re-decided here). One addition beyond the original: the `crs` property, so
callers (and tests) can observe which UTM/UPS zone got picked.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from pyproj import CRS, Transformer
from shapely.ops import transform


class LocalProjection:
    @staticmethod
    def __get_local_projection(lon: float, lat: float) -> CRS:
        # Interface uses (lon, lat) format for consistency with GeoJSON and web
        # standards.
        # UPS zones
        if lat >= 84:
            return CRS.from_epsg(32661)  # UPS North
        if lat <= -80:
            return CRS.from_epsg(32761)  # UPS South

        # UTM zones
        zone = int(math.floor((lon + 180) / 6) + 1)
        hemisphere = "north" if lat >= 0 else "south"
        epsg_code = 32600 + zone if hemisphere == "north" else 32700 + zone
        return CRS.from_epsg(epsg_code)

    def __init__(self, shape: Any) -> None:
        centroid = shape.centroid

        # centroid.x is longitude, centroid.y is latitude (shapely uses (x=lon, y=lat)).
        self.__crs_proj = self.__get_local_projection(centroid.x, centroid.y)
        self.__crs_wgs = "EPSG:4326"

        self.__transformer_to_proj = Transformer.from_crs(
            self.__crs_wgs,
            self.__crs_proj,
            always_xy=True,
        )
        self.__transformer_to_wgs = Transformer.from_crs(
            self.__crs_proj,
            self.__crs_wgs,
            always_xy=True,
        )

    @property
    def crs(self) -> CRS:
        return self.__crs_proj

    def to_local_np(self, lonlat_array: np.ndarray) -> np.ndarray:
        x_array, y_array = self.__transformer_to_proj.transform(
            lonlat_array[:, 0],
            lonlat_array[:, 1],
        )
        return np.column_stack((x_array, y_array))

    def to_wgs_np(self, lonlat_array: np.ndarray) -> np.ndarray:
        lon_array, lat_array = self.__transformer_to_wgs.transform(
            lonlat_array[:, 0],
            lonlat_array[:, 1],
        )
        return np.column_stack((lon_array, lat_array))

    def to_local(self, shape: Any) -> Any:
        return transform(self.__transformer_to_proj.transform, shape)

    def to_wgs(self, shape: Any) -> Any:
        return transform(self.__transformer_to_wgs.transform, shape)
```

```python
# src/fledermap/util/__init__.py
```
(empty file)

- [ ] **Step 5: Run test to verify it passes**

Run: `hatch test tests/test_projection.py -v`
Expected: PASS (6 tests)

- [ ] **Step 6: Type-check and lint**

Run: `hatch run types:check && hatch fmt`

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml src/fledermap/util/ tests/test_projection.py
git commit -m "feat: port LocalProjection from mkmapdiary

MIT-relicensed per parent spec section 16. Adds a public crs property (absent
from the original) so zone selection is observable from outside the class."
```

---

### Task 4: Port `GeoCluster` — `derive/geo_cluster.py`

Ported from `../mkmapdiary/src/mkmapdiary/lib/geoCluster.py`, unchanged except its
import of `LocalProjection`. Lives in `derive/`, not `util/`, because it summarises a
cluster (Task 8's `derive/sites.py` is its only caller) — it does not itself cluster
(parent spec section 7: "`GeoCluster` does not cluster despite its name").

**Files:**
- Create: `src/fledermap/derive/__init__.py` (empty)
- Create: `src/fledermap/derive/geo_cluster.py`
- Test: `tests/test_geo_cluster.py`
- Modify: `pyproject.toml` — add `scipy` to `dependencies` and to
  `[tool.hatch.envs.types]`'s `extra-dependencies`

**Interfaces:**
- Consumes: `fledermap.util.projection.LocalProjection` (Task 3).
- Produces: `fledermap.derive.geo_cluster.GeoCluster` — constructor takes
  `locations: list[tuple[float, float]]` (lon, lat pairs); properties `locations`,
  `separation_degrees`, `separation_meters`, `midpoint`, `shape`, `mass_point`,
  `radius`, `zoom_level`; static methods `_greatcircle_angle`, `_greatcircle_midpoint`.
  Consumed by Task 9's `services/derive.py`.

- [ ] **Step 1: Add the new dependency**

In `pyproject.toml`, add `"scipy"` to both `dependencies` and
`[tool.hatch.envs.types]`'s `extra-dependencies` (same pattern as Task 3).

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_geo_cluster.py
"""Ported from mkmapdiary/tests/test_geo_cluster_math.py (great-circle math,
unchanged) plus new tests for the properties that math feeds into."""

from __future__ import annotations

import math

import pytest

from fledermap.derive.geo_cluster import GeoCluster


class TestGeoClusterMathematicalFunctions:
    def test_greatcircle_angle_same_point(self) -> None:
        lat1 = lon1 = lat2 = lon2 = math.radians(45.0)
        angle = GeoCluster._greatcircle_angle(lat1, lon1, lat2, lon2)
        assert abs(angle) < 1e-10

    def test_greatcircle_angle_antipodal_points(self) -> None:
        lat1, lon1 = math.radians(0.0), math.radians(0.0)
        lat2, lon2 = math.radians(0.0), math.radians(180.0)
        angle = GeoCluster._greatcircle_angle(lat1, lon1, lat2, lon2)
        assert abs(angle - math.pi) < 1e-10

    def test_greatcircle_angle_quarter_circle(self) -> None:
        lat1, lon1 = math.radians(90.0), math.radians(0.0)
        lat2, lon2 = math.radians(0.0), math.radians(0.0)
        angle = GeoCluster._greatcircle_angle(lat1, lon1, lat2, lon2)
        assert abs(angle - math.pi / 2) < 1e-10

    def test_greatcircle_angle_known_cities(self) -> None:
        nyc_lat, nyc_lon = math.radians(40.7128), math.radians(-74.0060)
        london_lat, london_lon = math.radians(51.5074), math.radians(-0.1278)
        angle = GeoCluster._greatcircle_angle(nyc_lat, nyc_lon, london_lat, london_lon)
        assert 49.0 < math.degrees(angle) < 51.0

    def test_greatcircle_angle_symmetry(self) -> None:
        lat1, lon1 = math.radians(40.0), math.radians(-74.0)
        lat2, lon2 = math.radians(51.0), math.radians(0.0)
        angle1 = GeoCluster._greatcircle_angle(lat1, lon1, lat2, lon2)
        angle2 = GeoCluster._greatcircle_angle(lat2, lon2, lat1, lon1)
        assert abs(angle1 - angle2) < 1e-10

    def test_greatcircle_midpoint_equator_points(self) -> None:
        lat1, lon1 = math.radians(0.0), math.radians(0.0)
        lat2, lon2 = math.radians(0.0), math.radians(90.0)
        mid_lat, mid_lon = GeoCluster._greatcircle_midpoint(lat1, lon1, lat2, lon2)
        assert abs(mid_lat) < 1e-10
        assert abs(mid_lon - math.radians(45.0)) < 1e-10

    def test_greatcircle_midpoint_symmetry(self) -> None:
        lat1, lon1 = math.radians(40.0), math.radians(-74.0)
        lat2, lon2 = math.radians(51.0), math.radians(0.0)
        mid1 = GeoCluster._greatcircle_midpoint(lat1, lon1, lat2, lon2)
        mid2 = GeoCluster._greatcircle_midpoint(lat2, lon2, lat1, lon1)
        assert mid1 == pytest.approx(mid2, abs=1e-10)

    def test_mathematical_consistency(self) -> None:
        lat1, lon1 = math.radians(40.0), math.radians(-74.0)
        lat2, lon2 = math.radians(51.0), math.radians(0.0)
        full_angle = GeoCluster._greatcircle_angle(lat1, lon1, lat2, lon2)
        mid_lat, mid_lon = GeoCluster._greatcircle_midpoint(lat1, lon1, lat2, lon2)
        half_angle = GeoCluster._greatcircle_angle(lat1, lon1, mid_lat, mid_lon)
        assert abs(2 * half_angle - full_angle) < 1e-8


class TestGeoClusterProperties:
    def test_mass_point_of_two_points_is_between_them(self) -> None:
        cluster = GeoCluster([(13.0, 52.0), (13.02, 52.0)])
        lon, lat = cluster.mass_point
        assert lon == pytest.approx(13.01, abs=1e-6)
        assert lat == pytest.approx(52.0, abs=1e-6)

    def test_radius_is_half_separation_meters(self) -> None:
        cluster = GeoCluster([(13.0, 52.0), (13.02, 52.0)])
        assert cluster.radius == pytest.approx(cluster.separation_meters / 2)

    def test_zoom_level_is_max_for_empty_locations(self) -> None:
        cluster = GeoCluster([])
        assert cluster.zoom_level == 18

    def test_mass_point_is_none_for_empty_locations(self) -> None:
        cluster = GeoCluster([])
        assert cluster.mass_point == (None, None)

    def test_outlier_is_removed_with_four_or_more_points(self) -> None:
        tight = [(13.0, 52.0), (13.0001, 52.0), (13.0002, 52.0001), (13.0001, 52.0002)]
        far_outlier = (20.0, 52.0)  # far east, same latitude
        cluster = GeoCluster([*tight, far_outlier])
        assert far_outlier not in cluster.locations
        assert len(cluster.locations) == len(tight)

    def test_no_outlier_removal_below_four_points(self) -> None:
        # Below 4 points, `GeoCluster.__remove_outliers` deliberately leaves the
        # set untouched — not enough points to determine an outlier.
        points = [(13.0, 52.0), (20.0, 52.0), (13.0001, 52.0)]
        cluster = GeoCluster(points)
        assert len(cluster.locations) == len(points)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `hatch test tests/test_geo_cluster.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'fledermap.derive'`

- [ ] **Step 4: Write the implementation**

```python
# src/fledermap/derive/geo_cluster.py
"""Per-site summariser.

Ported from mkmapdiary (`lib/geoCluster.py`), MIT-relicensed for this project
(parent spec section 16). Unchanged except the `LocalProjection` import. Does NOT
cluster despite its name — DBSCAN (`derive/sites.py`) partitions; this describes
one already-formed point set (parent spec section 7).
"""

from __future__ import annotations

import copy

import numpy as np
from scipy import stats
from scipy.spatial import ConvexHull
from shapely.geometry import MultiPoint

from fledermap.util.projection import LocalProjection


class GeoCluster:
    def __init__(self, locations: list[tuple[float, float]]) -> None:
        # Interface expects locations as (lon, lat) tuples for consistency with
        # GeoJSON.
        self.__locations = locations
        self.__remove_outliers()

        self.__degrees, self.__distance, self.__midpoint = (
            self.__longest_greatcircle_separation()
        )

    EARTH_RADIUS_M = 6371008.8  # mean Earth radius in meters

    def __remove_outliers(self) -> None:
        if len(self.__locations) < 4:
            return  # Not enough points to determine outliers

        proj = LocalProjection(self.shape)
        local_locations = proj.to_local_np(np.array(self.__locations))

        threshold = 1
        z_scores = np.abs(stats.zscore(local_locations))
        filtered_data = local_locations[(z_scores < threshold).all(axis=1)]
        self.__locations = proj.to_wgs_np(filtered_data).tolist()

    @property
    def locations(self) -> list[tuple[float, float]]:
        return copy.deepcopy(self.__locations)

    @property
    def separation_degrees(self) -> float:
        return self.__degrees

    @property
    def separation_meters(self) -> float:
        return self.__distance

    @property
    def midpoint(self) -> tuple[float | None, float | None]:
        return copy.deepcopy(self.__midpoint)

    @property
    def shape(self) -> MultiPoint:
        return MultiPoint(self.__locations)

    @property
    def mass_point(self) -> tuple[float | None, float | None]:
        if len(self.__locations) == 0:
            return (None, None)

        pts = np.array(self.__locations)
        lon = np.radians(pts[:, 0])
        lat = np.radians(pts[:, 1])

        x = np.cos(lat) * np.cos(lon)
        y = np.cos(lat) * np.sin(lon)
        z = np.sin(lat)

        x_mean = np.mean(x)
        y_mean = np.mean(y)
        z_mean = np.mean(z)

        lon_mean = np.arctan2(y_mean, x_mean)
        hyp = np.sqrt(x_mean * x_mean + y_mean * y_mean)
        lat_mean = np.arctan2(z_mean, hyp)

        return (
            (np.degrees(lon_mean) + 540) % 360 - 180,
            np.degrees(lat_mean),
        )

    @property
    def radius(self) -> float:
        return self.__distance / 2

    @property
    def zoom_level(self) -> int:
        if len(self.__locations) == 0:
            return 18
        if self.__degrees == 0:
            return 18

        adjustment_factor = 2
        level = int(round(np.log2(360.0 / self.__degrees * adjustment_factor)))
        return max(min(level, 18), 3)

    @staticmethod
    def _greatcircle_angle(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        return np.arccos(
            np.clip(
                np.sin(lat1) * np.sin(lat2)
                + np.cos(lat1) * np.cos(lat2) * np.cos(lon1 - lon2),
                -1,
                1,
            ),
        )

    @staticmethod
    def _greatcircle_midpoint(
        lat1: float, lon1: float, lat2: float, lon2: float
    ) -> tuple[float, float]:
        dlon = lon2 - lon1
        bx = np.cos(lat2) * np.cos(dlon)
        by = np.cos(lat2) * np.sin(dlon)
        lat3 = np.arctan2(
            np.sin(lat1) + np.sin(lat2),
            np.sqrt((np.cos(lat1) + bx) ** 2 + by**2),
        )
        lon3 = lon1 + np.arctan2(by, np.cos(lat1) + bx)
        return lat3, lon3

    def __longest_greatcircle_separation(
        self,
    ) -> tuple[float, float, tuple[float | None, float | None]]:
        pts = np.array(self.__locations)
        n = len(pts)

        if n < 2:
            return 0.0, 0.0, (None, None)

        if n == 2:
            lon1, lat1 = np.radians(pts[0])
            lon2, lat2 = np.radians(pts[1])
            ang = self._greatcircle_angle(lat1, lon1, lat2, lon2)
            mid_lat, mid_lon = self._greatcircle_midpoint(lat1, lon1, lat2, lon2)
            separation_m = ang * self.EARTH_RADIUS_M
            return (
                np.degrees(ang),
                separation_m,
                ((np.degrees(mid_lon) + 540) % 360 - 180, np.degrees(mid_lat)),
            )

        try:
            hull = ConvexHull(pts)
            hull_pts = pts[hull.vertices]
        except Exception:  # noqa: BLE001 — degenerate hull (collinear points) falls
            # back to comparing every point, matching the ported original.
            hull_pts = pts

        lon = np.radians(hull_pts[:, 0])
        lat = np.radians(hull_pts[:, 1])

        sin_lat = np.sin(lat)
        cos_lat = np.cos(lat)
        dlon = lon[:, None] - lon[None, :]
        central_angle = np.arccos(
            np.clip(
                sin_lat[:, None] * sin_lat[None, :]
                + cos_lat[:, None] * cos_lat[None, :] * np.cos(dlon),
                -1,
                1,
            ),
        )

        i, j = np.unravel_index(np.argmax(central_angle), central_angle.shape)
        ang = central_angle[i, j]
        separation_m = ang * self.EARTH_RADIUS_M

        mid_lat, mid_lon = self._greatcircle_midpoint(lat[i], lon[i], lat[j], lon[j])
        mid_lat_deg = np.degrees(mid_lat)
        mid_lon_deg = (np.degrees(mid_lon) + 540) % 360 - 180

        return np.degrees(ang), separation_m, (mid_lon_deg, mid_lat_deg)
```

```python
# src/fledermap/derive/__init__.py
```
(empty file)

- [ ] **Step 5: Run tests to verify they pass**

Run: `hatch test tests/test_geo_cluster.py -v`
Expected: PASS (all tests)

If `test_outlier_is_removed_with_four_or_more_points` is flaky against the exact
z-score threshold, adjust the outlier's distance further from the tight cluster (not
the assertion) — the ported `__remove_outliers` threshold (`z_scores < 1`) is fixed
code being tested as-is, not something this task tunes.

- [ ] **Step 6: Type-check and lint**

Run: `hatch run types:check && hatch fmt`

The ported `except Exception:` in `__longest_greatcircle_separation` needs a `# noqa:
BLE001` (already included above) to pass ruff's blind-except rule — this exception
handling is inherited from the original, not something to narrow as part of a port.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml src/fledermap/derive/ tests/test_geo_cluster.py
git commit -m "feat: port GeoCluster from mkmapdiary

MIT-relicensed per parent spec section 16. A summariser over an already-formed
point set, not a clusterer despite the name."
```

---

### Task 5: Schema — `site`, `site_name_cache`, `session_merge_proposal`, `recording.site_id`, `session.weather`/`effort`

Also closes a small pre-existing gap found while touching `session`: `kind` is a
closed two-value vocabulary (`'stationary'` | `'transect'`, parent spec section 9) but
currently has no CHECK constraint, unlike `Verdict`'s identical situation. Since this
task already migrates the `session` table, fixing it here costs one more enum instead
of a future dedicated migration.

**Files:**
- Modify: `src/fledermap/domain/codes.py` — add `SessionKind`, `MergeResolution`
- Modify: `src/fledermap/store/models.py` — add `Site`, `SiteNameCache`,
  `SessionMergeProposal`; add `Session.weather`, `Session.effort`; change
  `Session.kind` to the new enum; add `Recording.site_id`
- Create: `alembic/versions/<hash>_phase2_derivation_schema.py`
- Test: `tests/test_migrations.py` — four new tests (two enums × enforced/accepts-all)
- Test: `tests/test_models.py` — one new test, mirroring its existing
  `test_enum_columns_round_trip_to_python_type` (written for `Identification.source`/
  `verdict`) for the now-newly-enum `Session.kind`

**Interfaces:**
- Produces: `fledermap.domain.codes.SessionKind` (`STATIONARY = "stationary"`,
  `TRANSECT = "transect"`), `fledermap.domain.codes.MergeResolution`
  (`MERGED = "merged"`, `REJECTED = "rejected"`); `fledermap.store.models.Site`,
  `SiteNameCache`, `SessionMergeProposal`; `Recording.site_id: int | None`. Consumed by
  Task 6 (`Session`, `SessionKind`), Task 7 (`SessionMergeProposal`), Task 9 (`Site`).

- [ ] **Step 1: Add the two new enums to `domain/codes.py`**

```python
class SessionKind(StrEnum):
    """Whether a session was stationary monitoring or a walked transect.

    User-set (parent spec section 9); every session derived without a UI to set
    it defaults to STATIONARY. A closed, two-member vocabulary — CHECK-enforced,
    like `Verdict`.
    """

    STATIONARY = "stationary"
    TRANSECT = "transect"


class MergeResolution(StrEnum):
    """How a human resolved a `SessionMergeProposal`. NULL means still open."""

    MERGED = "merged"
    REJECTED = "rejected"
```

- [ ] **Step 2: Update `models.py`**

Change the `Session` class:

```python
from fledermap.domain.codes import IdSource, MergeResolution, SessionKind, Verdict

# ...

class Session(Base):
    """The durable annotation layer. Incremental, never renumbered (spec D7)."""

    __tablename__ = "session"

    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
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
    detector_key: Mapped[str | None] = mapped_column(String(160), index=True)
    note: Mapped[str | None] = mapped_column(Text)
    weather: Mapped[str | None] = mapped_column(Text)
    effort: Mapped[str | None] = mapped_column(Text)
```

Add three new classes at the end of the file:

```python
class Site(Base):
    """A derived cluster of stationary recordings — a projection, not an entity.

    Truncated and rebuilt wholesale by `services.derive.derive_sites` (spec
    section 7). `name`/`admin_path` are schema now, populated by Phase 3's poiidx
    naming job — this phase never writes them.
    """

    __tablename__ = "site"

    id: Mapped[int] = mapped_column(primary_key=True)
    centroid: Mapped[object] = mapped_column(
        Geography(geometry_type="POINT", srid=4326),
    )
    radius_m: Mapped[float] = mapped_column(Float)
    recording_count: Mapped[int] = mapped_column(Integer)
    first_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    name: Mapped[str | None] = mapped_column(Text)
    admin_path: Mapped[str | None] = mapped_column(Text)


class SiteNameCache(Base):
    """Keyed on rounded coordinates; survives site rebuilds so re-derivation
    never re-triggers a Geofabrik download (spec section 7). Unused until
    Phase 3."""

    __tablename__ = "site_name_cache"

    id: Mapped[int] = mapped_column(primary_key=True)
    geohash: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    name: Mapped[str] = mapped_column(Text)
    admin_path: Mapped[str | None] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SessionMergeProposal(Base):
    """A bridging recording connected two already-persisted sessions.

    Never auto-merged (spec section 7) — this row is what a future UI (Phase 5)
    surfaces for a human to accept or reject.
    """

    __tablename__ = "session_merge_proposal"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_a_id: Mapped[int] = mapped_column(ForeignKey("session.id"))
    session_b_id: Mapped[int] = mapped_column(ForeignKey("session.id"))
    bridging_recording_id: Mapped[int] = mapped_column(ForeignKey("recording.id"))
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution: Mapped[MergeResolution | None] = mapped_column(
        SAEnum(
            MergeResolution,
            native_enum=False,
            length=16,
            create_constraint=True,
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
        ),
    )
```

Add `site_id` to `Recording` (after `session_id`):

```python
    site_id: Mapped[int | None] = mapped_column(
        ForeignKey("site.id", ondelete="SET NULL"),
    )
```

- [ ] **Step 3: Generate the migration**

Follow the same process `0001_initial.py` came from: bring a throwaway PostGIS
database to the current `head` (via `alembic upgrade head`), then autogenerate the
diff against the now-updated `models.py`. This needs Docker, same as any `db`-marked
test — run with `dangerouslyDisableSandbox: true`.

Save as a throwaway script (not committed — e.g. `/tmp/generate_migration.py`) and run
it with `hatch run python /tmp/generate_migration.py` (a real file, not `-c`: CLAUDE.md
notes `hatch run python -c "..."` brace-substitutes and breaks on `{`/`}` in the code):

```python
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import create_engine, text
from testcontainers.community.postgres import PostgresContainer

with PostgresContainer("postgis/postgis:16-3.4") as pg:
    url = pg.get_connection_url()
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
    engine.dispose()

    cfg = AlembicConfig()
    cfg.set_main_option("script_location", "alembic")
    cfg.set_main_option("sqlalchemy.url", url)

    command.upgrade(cfg, "head")  # brings the container's schema to 0001
    command.revision(cfg, autogenerate=True, message="phase 2 derivation schema")
    # The generated file lands in alembic/versions/ — the container is torn
    # down when the `with` block exits, but the file on disk survives that.
```

Verify and, where needed, hand-clean the generated file against `0001_initial.py`'s
style (explicit `postgresql_nulls_not_distinct`/`create_constraint` comments where
relevant, a real `downgrade()` that mirrors `upgrade()` in reverse, `op.f(...)` for
index names). Confirm `down_revision` is set to `0001`'s revision id
(`"7a46d3ce855f"`).

- [ ] **Step 4: Run the existing migration-drift test**

Run: `hatch test tests/test_migrations.py::test_migration_matches_the_models -v`
(needs `dangerouslyDisableSandbox: true` — Docker)

Expected: PASS with no hand-editing needed beyond Step 3's style pass — this test is
what actually proves the migration matches `models.py`, not manual inspection.

- [ ] **Step 5: Add the two new enum CHECK tests**

```python
# add to tests/test_migrations.py

def test_migrated_kind_check_is_enforced(migrated_engine: Engine) -> None:
    """`kind` is a closed two-value vocabulary (phase 2, task 5) — the one
    constraint `_comparable` excludes from the drift comparison."""
    with pytest.raises(IntegrityError), migrated_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO session (started_at, ended_at, kind)"
                " VALUES (now(), now(), 'not_a_kind')"
            )
        )


def test_migrated_kind_check_accepts_every_kind(migrated_engine: Engine) -> None:
    with migrated_engine.begin() as conn:
        for kind in SessionKind:
            conn.execute(
                text(
                    "INSERT INTO session (started_at, ended_at, kind)"
                    " VALUES (now(), now(), :kind)"
                ),
                {"kind": kind.value},
            )
        stored = conn.scalar(text("SELECT count(*) FROM session"))

    assert stored == len(SessionKind)


def test_migrated_resolution_check_is_enforced(migrated_engine: Engine) -> None:
    with migrated_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO session (started_at, ended_at, kind)"
                " VALUES (now(), now(), 'stationary')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO session (started_at, ended_at, kind)"
                " VALUES (now(), now(), 'stationary')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO recording (audio_hash, path, recorded_at, guano_raw)"
                " VALUES ('r' || repeat('0', 63), 'x.wav', now(), '{}'::jsonb)"
            )
        )

    with pytest.raises(IntegrityError), migrated_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO session_merge_proposal"
                " (session_a_id, session_b_id, bridging_recording_id, detected_at,"
                "  resolution)"
                " SELECT s1.id, s2.id, r.id, now(), 'not_a_resolution'"
                " FROM session s1, session s2, recording r"
                " WHERE s1.id != s2.id LIMIT 1"
            )
        )


def test_migrated_resolution_check_accepts_every_resolution(
    migrated_engine: Engine,
) -> None:
    with migrated_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO session (started_at, ended_at, kind)"
                " VALUES (now(), now(), 'stationary')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO session (started_at, ended_at, kind)"
                " VALUES (now(), now(), 'stationary')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO recording (audio_hash, path, recorded_at, guano_raw)"
                " VALUES ('s' || repeat('0', 63), 'x.wav', now(), '{}'::jsonb)"
            )
        )
        for resolution in MergeResolution:
            conn.execute(
                text(
                    "INSERT INTO session_merge_proposal"
                    " (session_a_id, session_b_id, bridging_recording_id,"
                    "  detected_at, resolution)"
                    " SELECT s1.id, s2.id, r.id, now(), :resolution"
                    " FROM session s1, session s2, recording r"
                    " WHERE s1.id != s2.id LIMIT 1"
                ),
                {"resolution": resolution.value},
            )
        stored = conn.scalar(text("SELECT count(*) FROM session_merge_proposal"))
        # NULL resolution (still open) must also be accepted by the CHECK.
        conn.execute(
            text(
                "INSERT INTO session_merge_proposal"
                " (session_a_id, session_b_id, bridging_recording_id, detected_at)"
                " SELECT s1.id, s2.id, r.id, now()"
                " FROM session s1, session s2, recording r"
                " WHERE s1.id != s2.id LIMIT 1"
            )
        )

    assert stored == len(MergeResolution)
```

Add `SessionKind, MergeResolution` to the existing
`from fledermap.domain.codes import IdSource, Verdict` import line.

- [ ] **Step 6: Run the four new tests, and mutation-test them**

Run: `hatch test tests/test_migrations.py -v` (Docker, unsandboxed) — expect all PASS.

Mutation-test per CLAUDE.md's convention: temporarily change one of the new CHECK
constraints' `create_constraint` to `False` in `models.py` (do NOT touch the
migration), confirm `test_migration_matches_the_models` now fails (drift the enum
column's CHECK constraint no longer masks), then revert.

- [ ] **Step 7: Add the `Session.kind` round-trip test**

```python
# add to tests/test_models.py
from fledermap.domain.codes import IdSource, SessionKind, Verdict  # extend existing import
from fledermap.store.models import Identification, Recording, Session  # extend existing import


def test_session_kind_round_trips_to_python_type(engine: Engine) -> None:
    """A plain String column would come back as `str`, not the enum — mirrors
    `test_enum_columns_round_trip_to_python_type` above, written before `kind`
    became an enum (phase 2, task 5)."""
    with OrmSession(engine) as session:
        session.add(
            Session(
                started_at=datetime(2026, 8, 21, 21, tzinfo=UTC),
                ended_at=datetime(2026, 8, 21, 23, tzinfo=UTC),
                kind=SessionKind.STATIONARY,
                detector_key="EMT\x1f1",
            ),
        )
        session.commit()

    with OrmSession(engine) as session:
        loaded = session.scalars(select(Session)).one()
        assert isinstance(loaded.kind, SessionKind)
        assert loaded.kind is SessionKind.STATIONARY
```

Run: `hatch test tests/test_models.py -v` (Docker, unsandboxed)
Expected: PASS (this test and every existing one)

- [ ] **Step 8: Type-check and lint**

Run: `hatch run types:check && hatch fmt`

- [ ] **Step 9: Commit**

```bash
git add src/fledermap/domain/codes.py src/fledermap/store/models.py \
        alembic/versions/ tests/test_migrations.py tests/test_models.py
git commit -m "feat: add site, site_name_cache, session_merge_proposal schema

Also CHECK-constrains Session.kind (closed two-value vocabulary, shipped in
phase 1 without one — same pattern Verdict already uses)."
```

---

### Task 6: `partition_sessions()` — gap-based grouping (no bridging yet)

**Files:**
- Create: `src/fledermap/derive/sessions.py`
- Test: `tests/test_partition_sessions.py`

**Interfaces:**
- Consumes: `fledermap.store.models.Recording`, `Session` (Task 5).
- Produces: `fledermap.derive.sessions.SessionPartitionReport` (dataclass:
  `created: int = 0`, `extended: int = 0`),
  `fledermap.derive.sessions.partition_sessions(db_session: OrmSession, *,
  session_gap: timedelta) -> SessionPartitionReport`. Task 7 extends this same
  function; Task 10's CLI calls it.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_partition_sessions.py
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session as OrmSession

from fledermap.derive.sessions import partition_sessions
from fledermap.domain.codes import SessionKind
from fledermap.store.models import Recording, Session

pytestmark = pytest.mark.db


def _recording(hash_suffix: str, recorded_at: datetime, **kwargs: object) -> Recording:
    return Recording(
        audio_hash=hash_suffix.rjust(64, "0"),
        path=f"{hash_suffix}.wav",
        recorded_at=recorded_at,
        **kwargs,
    )


def test_first_recording_creates_a_new_session(engine: Engine) -> None:
    with OrmSession(engine) as session:
        session.add(
            _recording("a", datetime(2026, 8, 21, 21, tzinfo=UTC), make="EMT", serial="1"),
        )
        session.commit()

        report = partition_sessions(session, session_gap=timedelta(hours=6))
        session.commit()

        assert report.created == 1
        assert report.extended == 0
        recording = session.scalars(select(Recording)).one()
        assert recording.session_id is not None
        created_session = session.get(Session, recording.session_id)
        assert created_session is not None
        assert created_session.kind == SessionKind.STATIONARY


def test_second_recording_within_gap_extends_the_session(engine: Engine) -> None:
    with OrmSession(engine) as session:
        base = datetime(2026, 8, 21, 21, tzinfo=UTC)
        session.add(_recording("a", base, make="EMT", serial="1"))
        session.add(_recording("b", base + timedelta(hours=1), make="EMT", serial="1"))
        session.commit()

        report = partition_sessions(session, session_gap=timedelta(hours=6))
        session.commit()

        assert report.created == 1
        assert report.extended == 1
        sessions = session.scalars(select(Session)).all()
        assert len(sessions) == 1
        assert sessions[0].ended_at == base + timedelta(hours=1)


def test_recording_beyond_gap_starts_a_new_session(engine: Engine) -> None:
    with OrmSession(engine) as session:
        base = datetime(2026, 8, 21, 21, tzinfo=UTC)
        session.add(_recording("a", base, make="EMT", serial="1"))
        session.add(_recording("b", base + timedelta(hours=7), make="EMT", serial="1"))
        session.commit()

        report = partition_sessions(session, session_gap=timedelta(hours=6))
        session.commit()

        assert report.created == 2
        assert report.extended == 0
        assert session.scalars(select(Session)).all().__len__() == 2


def test_different_detectors_get_separate_sessions(engine: Engine) -> None:
    with OrmSession(engine) as session:
        base = datetime(2026, 8, 21, 21, tzinfo=UTC)
        session.add(_recording("a", base, make="EMT", serial="1"))
        session.add(_recording("b", base, make="EMT", serial="2"))
        session.commit()

        report = partition_sessions(session, session_gap=timedelta(hours=6))
        session.commit()

        assert report.created == 2
        assert len(session.scalars(select(Session)).all()) == 2


def test_missing_make_and_serial_still_get_a_session(engine: Engine) -> None:
    with OrmSession(engine) as session:
        session.add(_recording("a", datetime(2026, 8, 21, 21, tzinfo=UTC)))
        session.commit()

        report = partition_sessions(session, session_gap=timedelta(hours=6))
        session.commit()

        assert report.created == 1
        recording = session.scalars(select(Recording)).one()
        assert recording.session_id is not None


def test_recording_extends_an_existing_session_from_a_previous_run(
    engine: Engine,
) -> None:
    with OrmSession(engine) as session:
        base = datetime(2026, 8, 21, 21, tzinfo=UTC)
        existing = Session(
            started_at=base,
            ended_at=base,
            kind=SessionKind.STATIONARY,
            detector_key="EMT\x1f1",
        )
        session.add(existing)
        session.flush()
        session.add(_recording("a", base + timedelta(hours=2), make="EMT", serial="1"))
        session.commit()

        report = partition_sessions(session, session_gap=timedelta(hours=6))
        session.commit()

        assert report.created == 0
        assert report.extended == 1
        assert len(session.scalars(select(Session)).all()) == 1
        session.refresh(existing)
        assert existing.ended_at == base + timedelta(hours=2)


def test_old_recording_close_only_to_a_later_existing_session_joins_it_backward(
    engine: Engine,
) -> None:
    with OrmSession(engine) as session:
        base = datetime(2026, 8, 21, 21, tzinfo=UTC)
        later = Session(
            started_at=base,
            ended_at=base,
            kind=SessionKind.STATIONARY,
            detector_key="EMT\x1f1",
        )
        session.add(later)
        session.flush()
        # Arrives late, timestamped BEFORE `later`, within the gap of only it.
        session.add(_recording("a", base - timedelta(hours=1), make="EMT", serial="1"))
        session.commit()

        report = partition_sessions(session, session_gap=timedelta(hours=6))
        session.commit()

        assert report.created == 0
        assert report.extended == 1
        session.refresh(later)
        assert later.started_at == base - timedelta(hours=1)


def test_already_sessioned_recordings_are_untouched(engine: Engine) -> None:
    with OrmSession(engine) as session:
        existing = Session(
            started_at=datetime(2026, 8, 21, 21, tzinfo=UTC),
            ended_at=datetime(2026, 8, 21, 21, tzinfo=UTC),
            kind=SessionKind.STATIONARY,
            detector_key="EMT\x1f1",
        )
        session.add(existing)
        session.flush()
        session.add(
            _recording(
                "a",
                datetime(2026, 8, 21, 21, tzinfo=UTC),
                make="EMT",
                serial="1",
                session_id=existing.id,
            ),
        )
        session.commit()

        report = partition_sessions(session, session_gap=timedelta(hours=6))
        session.commit()

        assert report.created == 0
        assert report.extended == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `hatch test tests/test_partition_sessions.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

```python
# src/fledermap/derive/sessions.py
"""Session partitioning. Incremental, never renumbered (spec D7, section 7)."""

from __future__ import annotations

import bisect
from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import SessionKind
from fledermap.store.models import Recording, Session


@dataclass
class SessionPartitionReport:
    created: int = 0
    extended: int = 0


def _detector_key(make: str | None, serial: str | None) -> str:
    """Recordings sharing (make, serial) belong to the same detector.

    Both can be absent — not every source carries full metadata — so they're
    grouped together under one key rather than raising; a session still needs a
    home. `\\x1f` (ASCII Unit Separator — historically designed for exactly this,
    a field separator unlikely to appear in real text) separates the two
    fields: it cannot appear in either (both are plain text from GUANO/wamd/
    filename parsing), unlike a printable separator such as ":" which
    conceivably could appear inside a free-text `make`. NOT `\\x00`: Postgres
    (via psycopg2) unconditionally rejects a NUL byte in a text value —
    `ValueError: A string literal cannot contain NUL (0x00) characters.` —
    which would crash on essentially every session ever created, since
    `detector_key` is written to a real `text` column. Caught during Task 5's
    implementation (its own test needed a `detector_key` value and hit this
    immediately); verified directly against a live Postgres before fixing
    this plan. `\\x1f` round-trips through Postgres text with no issue.
    """
    return f"{make or ''}\x1f{serial or ''}"


def partition_sessions(
    db_session: OrmSession,
    *,
    session_gap: timedelta,
) -> SessionPartitionReport:
    """Assign every session_id-less recording to a session, per detector.

    Only rows with `session_id IS NULL` are touched; an existing session's
    `started_at`/`ended_at` may widen but its `id` never changes.

    A recording can extend an existing, already-persisted session from an
    earlier `derive` run in either direction — forward (the common case, a new
    recording after a session's `ended_at`) or backward (a late-arriving older
    recording, close only to what is currently the earliest known session).
    """
    report = SessionPartitionReport()

    unsessioned = db_session.scalars(
        select(Recording)
        .where(Recording.session_id.is_(None))
        .order_by(Recording.recorded_at),
    ).all()

    by_detector: dict[str, list[Recording]] = defaultdict(list)
    for recording in unsessioned:
        by_detector[_detector_key(recording.make, recording.serial)].append(recording)

    for key, recordings in by_detector.items():
        existing = list(
            db_session.scalars(
                select(Session)
                .where(Session.detector_key == key)
                .order_by(Session.started_at),
            ),
        )
        starts = [s.started_at for s in existing]

        for recording in recordings:
            idx = bisect.bisect_right(starts, recording.recorded_at)
            prev_session = existing[idx - 1] if idx > 0 else None
            next_session = existing[idx] if idx < len(existing) else None

            if prev_session is not None and prev_session.ended_at >= recording.recorded_at:
                recording.session_id = prev_session.id
                report.extended += 1
                continue

            gap_to_prev = (
                recording.recorded_at - prev_session.ended_at
                if prev_session is not None
                else None
            )
            gap_to_next = (
                next_session.started_at - recording.recorded_at
                if next_session is not None
                else None
            )

            joins_prev = gap_to_prev is not None and gap_to_prev <= session_gap
            joins_next = gap_to_next is not None and gap_to_next <= session_gap

            if joins_prev:
                assert prev_session is not None  # joins_prev implies this
                prev_session.ended_at = recording.recorded_at
                recording.session_id = prev_session.id
                report.extended += 1
            elif joins_next:
                assert next_session is not None  # joins_next implies this
                next_session.started_at = recording.recorded_at
                # Keep the bisect cache in sync: `starts[idx]` mirrors
                # `next_session.started_at` (next_session IS existing[idx]).
                # Without this, a SECOND recording in the same run that also
                # backward-extends this same session bisects against the
                # stale value and can overwrite started_at with something
                # that leaves an earlier recording of this run outside the
                # session's own persisted bounds — reproduced against a real
                # Postgres instance, caught in Task 6's review.
                starts[idx] = next_session.started_at
                recording.session_id = next_session.id
                report.extended += 1
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
                report.created += 1

                insert_at = bisect.bisect_right(starts, new_session.started_at)
                existing.insert(insert_at, new_session)
                starts.insert(insert_at, new_session.started_at)

    return report
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `hatch test tests/test_partition_sessions.py -v` (Docker, unsandboxed)
Expected: PASS (all 8 tests)

- [ ] **Step 5: Type-check and lint**

Run: `hatch run types:check && hatch fmt`

- [ ] **Step 6: Commit**

```bash
git add src/fledermap/derive/sessions.py tests/test_partition_sessions.py
git commit -m "feat: gap-based session partitioning"
```

---

### Task 7: Bridging detection — `session_merge_proposal`

Layers exactly one new behavior onto Task 6's `partition_sessions`: when a recording
is within `session_gap` of *both* its neighboring sessions (not just the one it joins),
that's the bridging case (spec section 7) — record a proposal instead of silently
extending as if there were no ambiguity.

**Files:**
- Modify: `src/fledermap/derive/sessions.py`
- Test: `tests/test_partition_sessions.py`

**Interfaces:**
- Consumes: `fledermap.store.models.SessionMergeProposal` (Task 5).
- Produces: `SessionPartitionReport` gains `merge_proposals: int = 0`.

- [ ] **Step 1: Write the failing tests**

```python
# add to tests/test_partition_sessions.py
from fledermap.store.models import SessionMergeProposal  # add to existing imports


def test_recording_between_two_sessions_within_gap_of_both_raises_a_proposal(
    engine: Engine,
) -> None:
    with OrmSession(engine) as session:
        base = datetime(2026, 8, 21, 21, tzinfo=UTC)
        early = Session(
            started_at=base,
            ended_at=base,
            kind=SessionKind.STATIONARY,
            detector_key="EMT\x1f1",
        )
        late = Session(
            started_at=base + timedelta(hours=8),
            ended_at=base + timedelta(hours=8),
            kind=SessionKind.STATIONARY,
            detector_key="EMT\x1f1",
        )
        session.add_all([early, late])
        session.flush()
        # 4h after `early`, 4h before `late` — within a 6h gap of both.
        bridging = _recording(
            "a", base + timedelta(hours=4), make="EMT", serial="1",
        )
        session.add(bridging)
        session.commit()

        report = partition_sessions(session, session_gap=timedelta(hours=6))
        session.commit()

        assert report.merge_proposals == 1
        assert len(session.scalars(select(Session)).all()) == 2  # no third session
        session.refresh(bridging)
        assert bridging.session_id == early.id  # joins the earlier of the two

        proposal = session.scalars(select(SessionMergeProposal)).one()
        assert proposal.session_a_id == early.id
        assert proposal.session_b_id == late.id
        assert proposal.bridging_recording_id == bridging.id
        assert proposal.resolved_at is None
        assert proposal.resolution is None


def test_recording_close_to_only_one_neighbor_does_not_raise_a_proposal(
    engine: Engine,
) -> None:
    with OrmSession(engine) as session:
        base = datetime(2026, 8, 21, 21, tzinfo=UTC)
        early = Session(
            started_at=base,
            ended_at=base,
            kind=SessionKind.STATIONARY,
            detector_key="EMT\x1f1",
        )
        late = Session(
            started_at=base + timedelta(hours=20),
            ended_at=base + timedelta(hours=20),
            kind=SessionKind.STATIONARY,
            detector_key="EMT\x1f1",
        )
        session.add_all([early, late])
        session.flush()
        # 1h after `early`, 19h before `late` — within gap of only `early`.
        session.add(_recording("a", base + timedelta(hours=1), make="EMT", serial="1"))
        session.commit()

        report = partition_sessions(session, session_gap=timedelta(hours=6))
        session.commit()

        assert report.merge_proposals == 0
        assert session.scalars(select(SessionMergeProposal)).all() == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `hatch test tests/test_partition_sessions.py -v`
Expected: the two new tests FAIL (`report.merge_proposals` — `AttributeError`), the
rest still PASS.

- [ ] **Step 3: Implement**

In `src/fledermap/derive/sessions.py`:

Add to imports: `from datetime import UTC, datetime, timedelta` (add `UTC, datetime`)
and `from fledermap.store.models import Recording, Session, SessionMergeProposal`.

Add `merge_proposals: int = 0` to `SessionPartitionReport`.

Change the `if joins_prev:` branch:

```python
            if joins_prev:
                assert prev_session is not None  # joins_prev implies this
                prev_session.ended_at = recording.recorded_at
                recording.session_id = prev_session.id
                report.extended += 1
                if joins_next:
                    assert next_session is not None  # joins_next implies this
                    db_session.add(
                        SessionMergeProposal(
                            session_a_id=prev_session.id,
                            session_b_id=next_session.id,
                            bridging_recording_id=recording.id,
                            detected_at=datetime.now(UTC),
                        ),
                    )
                    report.merge_proposals += 1
            elif joins_next:
```

(`recording.id` needs the recording already flushed/has a PK — recordings loaded via
`select(Recording)` in this same session already have one; no extra flush needed.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `hatch test tests/test_partition_sessions.py -v` (Docker, unsandboxed)
Expected: PASS (all 10 tests)

- [ ] **Step 5: Type-check and lint**

Run: `hatch run types:check && hatch fmt`

- [ ] **Step 6: Commit**

```bash
git add src/fledermap/derive/sessions.py tests/test_partition_sessions.py
git commit -m "feat: raise a merge proposal for bridging recordings

Never auto-merges (spec section 7) — the recording still joins the earlier
session so it has a home; the proposal is what a future UI surfaces."
```

---

### Task 8: `cluster_points()` — DBSCAN over projected coordinates

**Files:**
- Create: `src/fledermap/derive/sites.py`
- Test: `tests/test_cluster_points.py`
- Modify: `pyproject.toml` — add `scikit-learn` to `dependencies` and to
  `[tool.hatch.envs.types]`'s `extra-dependencies`

**Interfaces:**
- Consumes: `fledermap.util.projection.LocalProjection` (Task 3).
- Produces: `fledermap.derive.sites.cluster_points(lonlat: np.ndarray, *, eps_m: float,
  min_points: int) -> np.ndarray` — one integer label per input row, `-1` for noise.
  Consumed by Task 9's `services/derive.py`.

- [ ] **Step 1: Add the new dependency**

Add `"scikit-learn"` to `pyproject.toml`'s `dependencies` and to
`[tool.hatch.envs.types]`'s `extra-dependencies` (same pattern as Tasks 3–4;
scikit-learn does not ship inline types, so if `hatch run types:check` can't resolve
it even from `extra-dependencies`, that's the trigger for `types-scikit-learn` —
never a global ignore).

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_cluster_points.py
from __future__ import annotations

import numpy as np

from fledermap.derive.sites import cluster_points


def test_two_nearby_points_form_one_cluster() -> None:
    # ~11m apart at this latitude.
    points = np.array([[13.4000, 52.5000], [13.4001, 52.5000]])

    labels = cluster_points(points, eps_m=75.0, min_points=2)

    assert labels[0] == labels[1]
    assert labels[0] != -1


def test_two_far_points_are_noise() -> None:
    # Berlin and Munich — hundreds of km apart.
    points = np.array([[13.4, 52.5], [11.58, 48.14]])

    labels = cluster_points(points, eps_m=75.0, min_points=2)

    assert list(labels) == [-1, -1]


def test_empty_input_returns_empty_array() -> None:
    labels = cluster_points(np.empty((0, 2)), eps_m=75.0, min_points=2)
    assert len(labels) == 0


def test_eps_is_metres_not_degrees() -> None:
    """Regression for the pitfall parent spec section 7 pins: eps must be
    metres. Two points ~68m apart at Berlin's latitude: noise under a 30m eps,
    one cluster under a 100m eps."""
    points = np.array([[13.4000, 52.5000], [13.4010, 52.5000]])

    tight = cluster_points(points, eps_m=30.0, min_points=2)
    loose = cluster_points(points, eps_m=100.0, min_points=2)

    assert list(tight) == [-1, -1]
    assert loose[0] == loose[1]
    assert loose[0] != -1
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `hatch test tests/test_cluster_points.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'fledermap.derive.sites'`

- [ ] **Step 4: Write the implementation**

```python
# src/fledermap/derive/sites.py
"""Site clustering. DBSCAN partitions; `GeoCluster` (geo_cluster.py) describes
what it finds (spec section 7)."""

from __future__ import annotations

import numpy as np
from shapely.geometry import MultiPoint
from sklearn.cluster import DBSCAN

from fledermap.util.projection import LocalProjection


def cluster_points(
    lonlat: np.ndarray,
    *,
    eps_m: float,
    min_points: int,
) -> np.ndarray:
    """DBSCAN over `lonlat` (shape (n, 2), columns lon/lat); `eps_m` is metres.

    Projects through `LocalProjection` first so `eps_m` means metres, not
    degrees — parent spec section 7's pinned pitfall. Returns one integer
    cluster label per input row; -1 marks noise (a one-off spot, not an error).
    """
    if lonlat.shape[0] == 0:
        return np.array([], dtype=int)

    projection = LocalProjection(MultiPoint(lonlat.tolist()))
    local = projection.to_local_np(lonlat)
    return DBSCAN(eps=eps_m, min_samples=min_points).fit_predict(local)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `hatch test tests/test_cluster_points.py -v`
Expected: PASS (4 tests)

- [ ] **Step 6: Type-check and lint**

Run: `hatch run types:check && hatch fmt`

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml src/fledermap/derive/sites.py tests/test_cluster_points.py
git commit -m "feat: cluster_points — DBSCAN over LocalProjection-projected coordinates"
```

---

### Task 9: `derive_sites()` — wholesale rebuild persistence

**Files:**
- Create: `src/fledermap/services/derive.py`
- Test: `tests/test_derive_sites.py`

**Interfaces:**
- Consumes: `fledermap.derive.sites.cluster_points` (Task 8),
  `fledermap.derive.geo_cluster.GeoCluster` (Task 4), `fledermap.store.geo.decode_point`
  (Task 1), `fledermap.store.models.Site`, `Session`, `Recording` (Task 5).
- Produces: `fledermap.services.derive.SiteDeriveReport` (dataclass: `site_count: int =
  0`, `unclustered: int = 0`), `fledermap.services.derive.derive_sites(db_session:
  OrmSession, *, eps_m: float, min_points: int) -> SiteDeriveReport`. Consumed by
  Task 10's CLI and Task 11's regression test.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_derive_sites.py
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from geoalchemy2.elements import WKTElement
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import SessionKind
from fledermap.services.derive import derive_sites
from fledermap.store.models import Recording, Session, Site

pytestmark = pytest.mark.db


def _stationary_session(db_session: OrmSession) -> Session:
    s = Session(
        started_at=datetime(2026, 8, 21, 21, tzinfo=UTC),
        ended_at=datetime(2026, 8, 21, 23, tzinfo=UTC),
        kind=SessionKind.STATIONARY,
        detector_key="EMT\x1f1",
    )
    db_session.add(s)
    db_session.flush()
    return s


def _recording(
    hash_suffix: str,
    db_session: OrmSession,
    session: Session,
    lon: float,
    lat: float,
) -> Recording:
    r = Recording(
        audio_hash=hash_suffix.rjust(64, "0"),
        path=f"{hash_suffix}.wav",
        recorded_at=datetime(2026, 8, 21, 21, tzinfo=UTC),
        session_id=session.id,
        geom=WKTElement(f"POINT({lon} {lat})", srid=4326),
    )
    db_session.add(r)
    return r


def test_a_cluster_of_nearby_recordings_becomes_one_site(engine: Engine) -> None:
    with OrmSession(engine) as session:
        stationary = _stationary_session(session)
        _recording("a", session, stationary, 13.4000, 52.5000)
        _recording("b", session, stationary, 13.4001, 52.5000)
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
        stationary = _stationary_session(session)
        _recording("a", session, stationary, 13.4000, 52.5000)
        session.commit()

        report = derive_sites(session, eps_m=75.0, min_points=2)
        session.commit()

        assert report.site_count == 0
        assert report.unclustered == 1
        recording = session.scalars(select(Recording)).one()
        assert recording.site_id is None


def test_transect_recordings_are_excluded(engine: Engine) -> None:
    with OrmSession(engine) as session:
        transect = Session(
            started_at=datetime(2026, 8, 21, 21, tzinfo=UTC),
            ended_at=datetime(2026, 8, 21, 23, tzinfo=UTC),
            kind=SessionKind.TRANSECT,
            detector_key="EMT\x1f1",
        )
        session.add(transect)
        session.flush()
        _recording("a", session, transect, 13.4000, 52.5000)
        _recording("b", session, transect, 13.4001, 52.5000)
        session.commit()

        report = derive_sites(session, eps_m=75.0, min_points=2)
        session.commit()

        assert report.site_count == 0
        assert report.unclustered == 0


def test_recordings_without_gps_are_excluded(engine: Engine) -> None:
    with OrmSession(engine) as session:
        stationary = _stationary_session(session)
        session.add(
            Recording(
                audio_hash="c" * 64,
                path="c.wav",
                recorded_at=datetime(2026, 8, 21, 21, tzinfo=UTC),
                session_id=stationary.id,
                geom=None,
            ),
        )
        session.commit()

        report = derive_sites(session, eps_m=75.0, min_points=2)
        session.commit()

        assert report.site_count == 0
        assert report.unclustered == 0


def test_rebuild_is_wholesale_and_idempotent(engine: Engine) -> None:
    """Re-running with the same data doesn't duplicate sites; a recording that
    drops out of the archive between runs loses its site cleanly."""
    with OrmSession(engine) as session:
        stationary = _stationary_session(session)
        _recording("a", session, stationary, 13.4000, 52.5000)
        _recording("b", session, stationary, 13.4001, 52.5000)
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

- [ ] **Step 2: Run tests to verify they fail**

Run: `hatch test tests/test_derive_sites.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'fledermap.services.derive'`

- [ ] **Step 3: Write the implementation**

```python
# src/fledermap/services/derive.py
"""Site derivation use-case layer. See spec section 7."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from geoalchemy2.elements import WKTElement
from sqlalchemy import delete, select
from sqlalchemy.orm import Session as OrmSession

from fledermap.derive.geo_cluster import GeoCluster
from fledermap.derive.sites import cluster_points
from fledermap.domain.codes import SessionKind
from fledermap.store.geo import decode_point
from fledermap.store.models import Recording, Session, Site


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
    """Wholesale rebuild of `site` from stationary, GPS-bearing recordings.

    Idempotent — safe to re-run at any time (spec section 7: "tuning is free").
    A recording left with `site_id = NULL` is a one-off spot, not an error.

    `DELETE FROM site`, never `TRUNCATE`: Postgres `TRUNCATE` does not fire
    `ON DELETE SET NULL` the way `DELETE` does — it would either error on the
    referencing `recording.site_id` FK or, with CASCADE, truncate `recording`
    too.
    """
    recordings = list(
        db_session.scalars(
            select(Recording)
            .join(Session, Recording.session_id == Session.id)
            .where(
                Session.kind == SessionKind.STATIONARY,
                Recording.geom.is_not(None),
            ),
        ),
    )

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
        locations = [decode_point(r.geom) for r in members]
        cluster = GeoCluster(locations)  # type: ignore[arg-type]  # decode_point
        # never returns None here: every `members` recording came from the
        # `Recording.geom.is_not(None)` query above.
        lon, lat = cluster.mass_point

        site = Site(
            centroid=WKTElement(f"POINT({lon} {lat})", srid=4326),
            radius_m=cluster.radius,
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

Note the `# type: ignore[arg-type]` on `GeoCluster(locations)`: `decode_point`'s
return type is `tuple[float, float] | None`, but every element here is provably
non-`None` by construction (the query excludes `geom IS NULL`). Per this project's
mypy convention (bind, assert not None, dereference — never `# type: ignore`), prefer
an explicit assertion instead if `hatch run types:check` actually flags this line —
try first without the ignore:

```python
        locations: list[tuple[float, float]] = []
        for r in members:
            point = decode_point(r.geom)
            assert point is not None, "excluded by the geom IS NOT NULL query above"
            locations.append(point)
        cluster = GeoCluster(locations)
```

Use whichever form `hatch run types:check` actually requires — the assertion form is
the project's stated preference if mypy does flag the list-comprehension version.

- [ ] **Step 4: Run tests to verify they pass**

Run: `hatch test tests/test_derive_sites.py -v` (Docker, unsandboxed)
Expected: PASS (5 tests)

- [ ] **Step 5: Type-check and lint**

Run: `hatch run types:check && hatch fmt`

- [ ] **Step 6: Commit**

```bash
git add src/fledermap/services/derive.py tests/test_derive_sites.py
git commit -m "feat: derive_sites — wholesale site rebuild from DBSCAN clusters"
```

---

### Task 10: CLI — `fledermap derive`

**Files:**
- Modify: `src/fledermap/cli/main.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `fledermap.derive.sessions.partition_sessions` (Tasks 6–7),
  `fledermap.services.derive.derive_sites` (Task 9), `Config.session_gap_hours`,
  `Config.site_eps_m`, `Config.site_min_points` (Task 2).

**Fixture note:** `tests/test_cli.py`'s `_archive` fixture builds two recordings from
`wamd_payload()`'s defaults — same `position` (`42.346973,-76.48760`) on both files, no
`make`/`serial` (wamd carries no such field; both come back `None`, so both recordings
share one detector key), and filename timestamps ~19 minutes apart. Under
`site_min_points`'s *default* of 3, two identically-located recordings are correctly
noise (DBSCAN needs 3 for a cluster), not a site — so this test asserts on that
realistic default-config outcome (one session, zero sites) rather than contriving a
third recording just to force a site to form; sites forming correctly is already
covered end-to-end by Task 9 and Task 11.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_cli.py

def test_derive_command_reports_sessions_and_sites(
    clean_database_url: str,
    tmp_path: Path,
) -> None:
    archive = _archive(tmp_path)
    runner = CliRunner()
    env = {"FLEDERMAP_DATABASE_URL": clean_database_url}

    ingest_result = runner.invoke(cli, ["ingest", str(archive)], env=env)
    assert ingest_result.exit_code == 0, ingest_result.output

    result = runner.invoke(cli, ["derive"], env=env)

    assert result.exit_code == 0, result.output
    # Both recordings share one (absent) detector key and land within the
    # default 6h session gap of each other (filenames ~19 minutes apart) -> one
    # new session.
    assert "sessions: created 1  extended 1  merge proposals 0" in result.output
    # Identical GPS position but only 2 points, below the default
    # site_min_points=3 -> correctly noise, not a site.
    assert "sites: 0  unclustered 2" in result.output


def test_derive_command_is_idempotent(
    clean_database_url: str,
    tmp_path: Path,
) -> None:
    archive = _archive(tmp_path)
    runner = CliRunner()
    env = {"FLEDERMAP_DATABASE_URL": clean_database_url}
    runner.invoke(cli, ["ingest", str(archive)], env=env)
    runner.invoke(cli, ["derive"], env=env)

    result = runner.invoke(cli, ["derive"], env=env)

    assert result.exit_code == 0, result.output
    # Second run: nothing left unsessioned to partition, no new sessions.
    assert "sessions: created 0  extended 0  merge proposals 0" in result.output
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `hatch test tests/test_cli.py -v`
Expected: FAIL — `Error: No such command 'derive'`

- [ ] **Step 3: Implement**

In `src/fledermap/cli/main.py`, add imports:

```python
from datetime import timedelta

from fledermap.derive.sessions import partition_sessions
from fledermap.services.derive import derive_sites
```

Add the command:

```python
@cli.command()
def derive() -> None:
    """Partition sessions and rebuild sites from what `ingest` has stored.

    Headless — no web, no site naming (that's a later phase's job queue).
    Safe to re-run at any time: session partitioning only touches unsessioned
    recordings, and site rebuilding is idempotent.
    """
    try:
        config = Config.from_env(Path.cwd())
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc

    engine = make_engine(config.database_url)
    _run_migrations(config.database_url)

    with OrmSession(engine) as session:
        session_report = partition_sessions(
            session,
            session_gap=timedelta(hours=config.session_gap_hours),
        )
        session.commit()

        site_report = derive_sites(
            session,
            eps_m=config.site_eps_m,
            min_points=config.site_min_points,
        )
        session.commit()

        click.echo(
            f"sessions: created {session_report.created}  "
            f"extended {session_report.extended}  "
            f"merge proposals {session_report.merge_proposals}",
        )
        click.echo(
            f"sites: {site_report.site_count}  unclustered {site_report.unclustered}",
        )
```

`derive` has no `ARCHIVE` argument (unlike `ingest`) — it operates entirely on
`bats_db`, not the filesystem, so `Config.from_env` is called with `Path.cwd()` as a
placeholder `archive_root` that this command never reads. Confirm
`Config.from_env`'s signature doesn't do anything with `archive_root` that would make
an arbitrary path here problematic (it currently only calls `.resolve()` on it) before
relying on this.

- [ ] **Step 4: Run tests to verify they pass**

Run: `hatch test tests/test_cli.py -v` (Docker, unsandboxed if any `db`-marked test)
Expected: PASS

- [ ] **Step 5: Type-check and lint**

Run: `hatch run types:check && hatch fmt`

- [ ] **Step 6: Commit**

```bash
git add src/fledermap/cli/main.py tests/test_cli.py
git commit -m "feat: fledermap derive CLI command"
```

---

### Task 11: Phase-exit regression test — clustering at two latitudes

The exit criterion parent spec section 15 names for this phase: "clustering
regression test passes at both latitudes." Guards the exact pitfall section 7 pins —
a broken UTM-zone pick or a degrees-vs-metres `eps` bug would pass every other test in
this plan (all fixtures so far live near Berlin) yet silently misbehave somewhere
equatorial or in the opposite hemisphere.

**Files:**
- Test: `tests/test_derive_sites.py` (extend)

**Interfaces:** none new — exercises Task 9's `derive_sites` end to end.

- [ ] **Step 1: Write the test**

```python
# add to tests/test_derive_sites.py

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
        stationary = _stationary_session(session)
        # Two points ~15m apart (well inside a 75m eps); one far outlier that
        # must stay unclustered regardless of latitude.
        _recording(f"{label}-a", session, stationary, lon, lat)
        _recording(f"{label}-b", session, stationary, lon + 0.0002, lat)
        _recording(f"{label}-far", session, stationary, lon + 5.0, lat)
        session.commit()

        report = derive_sites(session, eps_m=75.0, min_points=2)
        session.commit()

        assert report.site_count == 1
        assert report.unclustered == 1
        recordings = {
            r.path: r for r in session.scalars(select(Recording)).all()
        }
        assert recordings[f"{label}-a.wav"].site_id is not None
        assert (
            recordings[f"{label}-a.wav"].site_id
            == recordings[f"{label}-b.wav"].site_id
        )
        assert recordings[f"{label}-far.wav"].site_id is None
```

- [ ] **Step 2: Run test to verify it fails without the fix**

This is a regression test for already-implemented behavior, so instead of a
"write failing, then implement" cycle: temporarily break the projection to prove this
test actually catches the pitfall it's named for. In `src/fledermap/derive/sites.py`,
temporarily change `cluster_points` to skip the projection:

```python
    # TEMPORARY, for mutation-testing this task's regression test:
    local = lonlat  # skip LocalProjection entirely
```

Run: `hatch test tests/test_derive_sites.py::test_clustering_regression_at_both_latitudes -v`
Expected: FAIL for at least one of the two latitudes (raw-degree `eps=75.0` is either
far too large or far too small in degree-space, misclustering the outlier or splitting
the tight pair) — confirming the test has teeth. Then revert the temporary change.

- [ ] **Step 3: Run the real test to verify it passes**

Run: `hatch test tests/test_derive_sites.py -v` (Docker, unsandboxed)
Expected: PASS (all tests, including both parametrized latitudes)

- [ ] **Step 4: Run the full test suite**

Run: `hatch test` (Docker, unsandboxed)
Run: `hatch run types:check`
Run: `hatch fmt`
Expected: all clean — this is Phase 2's exit gate.

- [ ] **Step 5: Commit**

```bash
git add tests/test_derive_sites.py
git commit -m "test: clustering regression at both latitudes (phase 2 exit criterion)"
```

---

## Self-Review Notes

**Spec coverage:** partition_sessions (Task 6) + bridging (Task 7) cover session
derivation; cluster_points (Task 8) + derive_sites (Task 9) cover site derivation;
schema (Task 5) covers every table the design doc lists; CLI (Task 10) covers the
`fledermap derive` entry point; the two-latitude regression (Task 11) covers the named
phase-exit criterion. Out-of-scope items (site naming, transect `kind` assignment, any
web surface) are deliberately untouched, matching the design doc's section 9.

**Type consistency check performed:** `SessionPartitionReport` (Task 6, extended
Task 7), `SiteDeriveReport` (Task 9), `cluster_points`'s signature (Task 8, called
unchanged from Task 9), `decode_point`'s signature (Task 1, called unchanged from
Task 9) — all consistent across every task that references them.

**Design-doc correction made during planning:** the design doc's §8 originally
promised a "pure-function, no database" test for `partition_sessions()`'s gap
arithmetic. The algorithm this plan actually specifies (Task 6) bisects against real,
possibly-just-flushed `Session` rows — a new session created mid-run must be visible
to the next recording's bisect — so it is inherently DB-coupled, the same reason
Phase 1's `sweep_missing` is DB-backed rather than split into a pure core. Fixed the
design doc to say so rather than leave it silently contradicted by this plan.

**Known judgment calls surfaced inline, not hidden:** the `site_min_points=3` default
(confirmed with the user during design review), the tie-break rule in bridging
detection (always joins the earlier session), and `_detector_key`'s `\x1f` separator
for recordings missing `make`/`serial`.
