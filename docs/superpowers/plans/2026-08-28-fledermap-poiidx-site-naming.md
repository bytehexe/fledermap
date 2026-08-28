# Fledermap poiidx Site Naming Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Populate `Site.name`/`Site.admin_path` via poiidx (the owner's PostGIS OSM POI index,
published on PyPI as `poiidx`), as an optional integration that leaves today's behavior
(coordinate-fallback labels) completely unchanged when unconfigured.

**Architecture:** A new `services/site_naming.py` owns every poiidx interaction: connecting
(parsing `FLEDERMAP_POIIDX_DATABASE_URL` into poiidx's discrete connect kwargs), the query
(`name_site`, cache-first through `SiteNameCache`, preferring the lowest-`rank` nearby POI, falling
back to the administrative hierarchy string), and enqueueing (`enqueue_site_naming`, cache-first
so `derive_sites`'s wholesale rebuild — which resets every site's name to `NULL` on every run —
never turns into a repeat-work storm). A new `geo`-queue Procrastinate task
(`jobs/tasks.py::name_site_task`) does the actual poiidx work off the request path, serialized via
a single static Procrastinate `lock` value. Both `derive_sites` call sites (the `fledermap derive`
CLI command, and `run_ingest_cycle`'s periodic worker task) get one new line calling
`enqueue_site_naming` right after their existing `derive_sites()` + `session.commit()`. A new
`fledermap backfill-site-names` CLI command covers sites that predate this feature or whose job
failed past its retry budget.

**Tech Stack:** `poiidx>=0.0.9` (PyPI), SQLAlchemy 2.0, Procrastinate (existing `geo` queue name,
reserved since Phase 3), Click (existing CLI), shapely (already a dependency).

**Spec:** `docs/superpowers/specs/2026-08-28-fledermap-poiidx-site-naming-design.md`

## Global Constraints

- **The integration is optional.** `FLEDERMAP_POIIDX_DATABASE_URL` unset means `enqueue_site_naming`
  is a true no-op — no jobs, no errors, no behavior change from today. Every task must preserve
  this.
- **Never call poiidx from a request handler.** Only `jobs/tasks.py::name_site_task` and the
  `backfill-site-names` CLI command may import/call `services/site_naming.py`'s poiidx-touching
  functions. `web/` stays untouched by this whole plan.
- **`derive_sites` is not modified.** No task in this plan touches `services/derive.py`'s
  clustering logic or its wholesale-rebuild behavior (design spec Non-goals, decision SN-4).
- **The connection-safety comment must be replicated** at the new poiidx connection site in
  `services/site_naming.py`, matching `store/db.py`'s existing warning: this connection must point
  at `poiidx_bats_db`, never `poiidx_db` (the owner's real index) or `bats_db` (Fledermap's own
  storage).
- **Fledermap's own filter config (not poiidx's shipped default) is used**, and must stay
  byte-identical across every `poiidx.init()` call — poiidx hashes it together with its schema and
  drops/recreates all tables on any drift (design spec §2).
- **`hatch fmt --check`, `hatch run types:check`, and `hatch test` must stay green after every
  task** — run with `dangerouslyDisableSandbox: true` (Docker-backed tests and `git` both need it
  in this repo, per `CLAUDE.md`). Never use `git commit --no-verify`; if the pre-commit hook fails
  for a reason that looks unrelated, stop and report it rather than bypassing it.
- **No new stub package guessing.** `poiidx` ships its own `py.typed` marker (confirmed) — it does
  NOT need an entry in `[tool.hatch.envs.types]`'s `extra-dependencies`.
- Every new `FLEDERMAP_*` setting needs a row in `docs/setup.md`'s settings table —
  `tests/test_setup_docs.py` enforces this automatically (a substring check against
  `Config._KNOWN_FILE_KEYS`) and fails the whole suite if it's missing.
- A new `Config.from_env` field needs a test asserting the constructed `Config`'s attribute, not
  just that parsing didn't raise (this project's own documented gotcha: `port` was silently
  dropped once despite being parsed and validated).

---

### Task 1: `Config` fields, dependency, and setup docs

**Files:**
- Modify: `pyproject.toml` (add `poiidx>=0.0.9` to `dependencies`)
- Modify: `src/fledermap/config.py` (new env constants, two new `Config` fields, `from_env`
  parsing, `_KNOWN_FILE_KEYS`)
- Modify: `docs/setup.md` (two new settings-table rows)
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `Config.poiidx_database_url: str | None` (default `None` — unset means the feature is
  off), `Config.site_naming_radius_m: float` (default `300.0`). Every later task reads these two
  fields off a `Config` instance; no other task touches `config.py`.

- [ ] **Step 1: Add the dependency**

In `pyproject.toml`, inside the `dependencies = [...]` list (after `"watchdog",`), add:

```toml
  "poiidx>=0.0.9",
```

- [ ] **Step 2: Write the failing tests**

Add these imports to `tests/test_config.py`'s existing `from fledermap.config import (...)` block
(insert alphabetically among the existing `ENV_*` names):

```python
    ENV_POIIDX_DATABASE_URL,
    ENV_SITE_NAMING_RADIUS_M,
```

Append these tests to the end of `tests/test_config.py`:

```python
def test_default_poiidx_database_url_is_none(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.delenv(ENV_POIIDX_DATABASE_URL, raising=False)
    config = Config.from_env()
    assert config.poiidx_database_url is None


def test_poiidx_database_url_is_configurable_via_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(
        ENV_POIIDX_DATABASE_URL,
        "postgresql://poiidx_user:pw@localhost:5432/poiidx_bats_db",
    )
    config = Config.from_env()
    assert (
        config.poiidx_database_url
        == "postgresql://poiidx_user:pw@localhost:5432/poiidx_bats_db"
    )


def test_default_site_naming_radius(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.delenv(ENV_SITE_NAMING_RADIUS_M, raising=False)
    config = Config.from_env()
    assert config.site_naming_radius_m == 300.0


def test_site_naming_radius_is_configurable_via_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_SITE_NAMING_RADIUS_M, "500")
    config = Config.from_env()
    assert config.site_naming_radius_m == 500.0


def test_zero_site_naming_radius_raises_config_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_SITE_NAMING_RADIUS_M, "0")
    with pytest.raises(ConfigError, match=ENV_SITE_NAMING_RADIUS_M):
        Config.from_env()


def test_negative_site_naming_radius_raises_config_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_SITE_NAMING_RADIUS_M, "-1")
    with pytest.raises(ConfigError, match=ENV_SITE_NAMING_RADIUS_M):
        Config.from_env()


def test_invalid_site_naming_radius_raises_config_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_SITE_NAMING_RADIUS_M, "not-a-number")
    with pytest.raises(ConfigError, match="not-a-number"):
        Config.from_env()
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `hatch test tests/test_config.py -k poiidx_database_url or site_naming_radius -v`
Expected: FAIL — `ImportError: cannot import name 'ENV_POIIDX_DATABASE_URL'`

- [ ] **Step 4: Add the env constants**

In `src/fledermap/config.py`, after the existing `ENV_TRANSECT_DISTANCE_M = "FLEDERMAP_TRANSECT_DISTANCE_M"`
line, add:

```python
ENV_POIIDX_DATABASE_URL = "FLEDERMAP_POIIDX_DATABASE_URL"
ENV_SITE_NAMING_RADIUS_M = "FLEDERMAP_SITE_NAMING_RADIUS_M"
```

- [ ] **Step 5: Add both keys to `_KNOWN_FILE_KEYS`**

In the `_KNOWN_FILE_KEYS = frozenset({...})` literal, after `"transect_distance_m",`, add:

```python
        "poiidx_database_url",
        "site_naming_radius_m",
```

- [ ] **Step 6: Add the two `Config` fields**

In the `@dataclass(frozen=True) class Config:` body, after the existing
`transect_distance_m: float = 150.0` field (and its comment), add:

```python
    # Optional (design spec 2026-08-28-fledermap-poiidx-site-naming-design.md,
    # decision SN-2): unset means the site-naming integration is off entirely
    # -- sites keep today's coordinate-fallback label, nothing errors, nothing
    # blocks. When set, this must point at a dedicated poiidx_bats_db, never
    # poiidx_db (the owner's real index) or bats_db (Fledermap's own storage)
    # -- see services/site_naming.py's connection-safety comment.
    poiidx_database_url: str | None = None
    # How far (metres) to search for a nearby named POI before falling back
    # to the administrative hierarchy string. Picked by analogy to
    # site_eps_m/transect_distance_m's defaults, not from parent-spec
    # guidance -- this task owns the default the same way P2-5 owned
    # site_min_points's.
    site_naming_radius_m: float = 300.0
```

- [ ] **Step 7: Parse both fields in `from_env`**

In `Config.from_env`, immediately before the final `return cls(` call, add:

```python
        poiidx_database_url_raw = _lookup(
            ENV_POIIDX_DATABASE_URL,
            "poiidx_database_url",
            file_values,
        )
        poiidx_database_url = (
            _as_str(
                poiidx_database_url_raw,
                _source_label(
                    ENV_POIIDX_DATABASE_URL,
                    "poiidx_database_url",
                    config_path,
                ),
            )
            if poiidx_database_url_raw is not None
            else None
        )

        site_naming_radius_raw = _lookup(
            ENV_SITE_NAMING_RADIUS_M,
            "site_naming_radius_m",
            file_values,
        )
        if site_naming_radius_raw is None:
            site_naming_radius_m = 300.0
        else:
            label = _source_label(
                ENV_SITE_NAMING_RADIUS_M,
                "site_naming_radius_m",
                config_path,
            )
            if isinstance(site_naming_radius_raw, bool):  # see session_gap_hours above
                msg = f"{label}={site_naming_radius_raw!r} is not a number of metres."
                raise ConfigError(msg)
            try:
                site_naming_radius_m = float(site_naming_radius_raw)
            except (TypeError, ValueError) as exc:
                msg = f"{label}={site_naming_radius_raw!r} is not a number of metres."
                raise ConfigError(msg) from exc
            if not site_naming_radius_m > 0:  # also rejects nan; see site_eps_m above
                msg = (
                    f"{label}={site_naming_radius_raw!r} is not a positive "
                    "number of metres."
                )
                raise ConfigError(msg)
```

Then add both new fields to the `return cls(...)` call, after `transect_distance_m=transect_distance_m,`:

```python
            poiidx_database_url=poiidx_database_url,
            site_naming_radius_m=site_naming_radius_m,
```

- [ ] **Step 8: Document both settings in `docs/setup.md`**

In the settings table (the one with `| Setting | Env var | Config file key | Required? | Default |`
header), after the `Session-kind GPS-spread threshold` row, add:

```markdown
| poiidx database connection | `FLEDERMAP_POIIDX_DATABASE_URL` | `poiidx_database_url` | no | unset — site naming disabled |
| Site-naming search radius (metres) | `FLEDERMAP_SITE_NAMING_RADIUS_M` | `site_naming_radius_m` | no | `300.0` |
```

Immediately after the table's closing paragraph (before the `## 3.` or next section heading —
check the actual next heading in the file), add a short paragraph:

```markdown
`poiidx_database_url` connects Fledermap to a *separate* poiidx instance
(`../poiidx` on this machine, published on PyPI as `poiidx`) used to name derived sites. It must
point at a dedicated `poiidx_bats_db` database — never `poiidx_db` (a pre-existing, unrelated
poiidx index) or `bats_db` (Fledermap's own storage). poiidx hashes its own schema and filter
config on init and **drops and recreates all its tables** on any mismatch, the same hazard the
database section above already warns about for `bats_db`. See
`docs/superpowers/specs/2026-08-28-fledermap-poiidx-site-naming-design.md` for the full design.
```

- [ ] **Step 9: Run the tests to verify they pass**

Run: `hatch test tests/test_config.py tests/test_setup_docs.py -v`
Expected: PASS (all new tests, plus `test_every_known_config_file_key_is_documented_in_setup_md`)

- [ ] **Step 10: Run the full check suite**

Run (all with `dangerouslyDisableSandbox: true`): `hatch fmt --check && hatch run types:check && hatch test`
Expected: all green.

- [ ] **Step 11: Commit**

```bash
git add pyproject.toml src/fledermap/config.py docs/setup.md tests/test_config.py
git commit -m "feat: add poiidx_database_url and site_naming_radius_m config"
```

---

### Task 2: `services/site_naming.py` — filter config and poiidx connection

**Files:**
- Create: `src/fledermap/services/data/poiidx_filter_config.yaml`
- Create: `src/fledermap/services/site_naming.py`
- Test: `tests/test_site_naming.py`

**Interfaces:**
- Consumes: nothing from Task 1 directly (this task's tests construct kwargs/URLs by hand — the
  wiring to a real `Config` happens in Task 4).
- Produces: `_poiidx_connection_kwargs(database_url: str) -> dict[str, Any]` (raises `ValueError`
  on a malformed URL), `_load_filter_config() -> list[dict[str, Any]]`, `ensure_connected(poiidx_database_url: str) -> None`.
  Later tasks import `ensure_connected` from this module; nothing outside this module needs the
  other two.

- [ ] **Step 1: Write the filter config**

Create `src/fledermap/services/data/poiidx_filter_config.yaml`:

```yaml
# Fledermap's own poiidx filter config (design spec
# 2026-08-28-fledermap-poiidx-site-naming-design.md, decision SN-3).
#
# Curated for TOPONYMY -- naming a coordinate -- not for general POI
# browsing the way poiidx's own shipped default (poi_filter_config.yaml)
# is. Everything here must stay byte-identical across every poiidx.init()
# call: poiidx hashes this file's content together with its schema, and
# ANY drift drops and recreates every table in poiidx_bats_db.
#
# list=OR, items in a list=AND (poiidx's own filter_config convention).

# == Places ==
- symbol: "city"
  description: "City or large town"
  filters:
    - place: city
- symbol: "town"
  description: "Town"
  filters:
    - place: town
- symbol: "village"
  description: "Village"
  filters:
    - place: village
    - place: hamlet
- symbol: "suburb"
  description: "Suburb, quarter, or neighbourhood"
  filters:
    - place: suburb
    - place: quarter
    - place: neighbourhood

# == Nature ==
- symbol: forest_or_park
  description: "Forest or park"
  filters:
    - landuse: forest
    - leisure: forest
    - leisure: park
    - natural: forest
    - natural: wood
    - leisure: nature_reserve
- symbol: "water_body"
  description: "Named lake, pond, reservoir, or river"
  filters:
    - natural: water
    - waterway: river
```

- [ ] **Step 2: Write the failing connection tests**

Create `tests/test_site_naming.py`:

```python
from __future__ import annotations

import pytest

from fledermap.services import site_naming


def test_poiidx_connection_kwargs_parses_a_well_formed_url() -> None:
    kwargs = site_naming._poiidx_connection_kwargs(
        "postgresql://poiidx_user:s3cret@localhost:5432/poiidx_bats_db",
    )
    assert kwargs == {
        "host": "localhost",
        "port": 5432,
        "user": "poiidx_user",
        "password": "s3cret",
        "database": "poiidx_bats_db",
    }


def test_poiidx_connection_kwargs_defaults_port_to_5432() -> None:
    kwargs = site_naming._poiidx_connection_kwargs(
        "postgresql://poiidx_user:s3cret@localhost/poiidx_bats_db",
    )
    assert kwargs["port"] == 5432


def test_poiidx_connection_kwargs_rejects_a_url_with_no_password() -> None:
    with pytest.raises(ValueError, match="FLEDERMAP_POIIDX_DATABASE_URL"):
        site_naming._poiidx_connection_kwargs(
            "postgresql://poiidx_user@localhost/poiidx_bats_db",
        )


def test_poiidx_connection_kwargs_rejects_a_url_with_no_database() -> None:
    with pytest.raises(ValueError, match="FLEDERMAP_POIIDX_DATABASE_URL"):
        site_naming._poiidx_connection_kwargs(
            "postgresql://poiidx_user:s3cret@localhost/",
        )


def test_load_filter_config_returns_the_expected_symbols() -> None:
    config = site_naming._load_filter_config()
    symbols = {entry["symbol"] for entry in config}
    assert symbols == {
        "city",
        "town",
        "village",
        "suburb",
        "forest_or_park",
        "water_body",
    }


def test_ensure_connected_calls_poiidx_init_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(site_naming, "_connected", False)
    calls: list[dict[str, object]] = []

    def fake_init(*, filter_config: object, **kwargs: object) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(site_naming.poiidx, "init", fake_init)

    site_naming.ensure_connected(
        "postgresql://poiidx_user:s3cret@localhost/poiidx_bats_db",
    )
    site_naming.ensure_connected(
        "postgresql://poiidx_user:s3cret@localhost/poiidx_bats_db",
    )

    assert len(calls) == 1
    assert calls[0]["database"] == "poiidx_bats_db"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `hatch test tests/test_site_naming.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'fledermap.services.site_naming'`

- [ ] **Step 4: Write `services/site_naming.py`**

Create `src/fledermap/services/site_naming.py`:

```python
"""poiidx query and job-enqueue logic for site naming (design spec
2026-08-28-fledermap-poiidx-site-naming-design.md).

WARNING: the connection built here must never point at `poiidx_db` (the
owner's own, separate, pre-existing POI index) or `bats_db` (Fledermap's own
real storage -- see the warning at the top of `store/db.py`). It must point
at `poiidx_bats_db`, a third, dedicated database this integration owns
exclusively. poiidx hashes its own schema and filter config on `init()` and
DROPS AND RECREATES ALL TABLES on any mismatch -- same hazard, different
database.
"""

from __future__ import annotations

from importlib.resources import files
from typing import Any
from urllib.parse import urlsplit

import poiidx
import yaml

_FILTER_CONFIG_FILE = "poiidx_filter_config.yaml"


def _poiidx_connection_kwargs(database_url: str) -> dict[str, Any]:
    """Parse FLEDERMAP_POIIDX_DATABASE_URL into poiidx.init()'s discrete
    connect kwargs. poiidx.connect() forwards straight to peewee's
    PostgresqlDatabase.init(), which takes host/port/user/password/database
    separately -- confirmed against poiidx's own README and example.py, not
    a single connection-string argument."""
    parsed = urlsplit(database_url)
    database = parsed.path.lstrip("/")
    if not (parsed.hostname and parsed.username and parsed.password and database):
        msg = (
            "FLEDERMAP_POIIDX_DATABASE_URL must be a "
            "postgresql://user:password@host:port/database URL, got "
            f"{database_url!r}"
        )
        raise ValueError(msg)
    return {
        "host": parsed.hostname,
        "port": parsed.port or 5432,
        "user": parsed.username,
        "password": parsed.password,
        "database": database,
    }


def _load_filter_config() -> list[dict[str, Any]]:
    """Fledermap's own toponymy-focused filter config (design spec §2), NOT
    poiidx's shipped default -- must stay byte-identical across every
    poiidx.init() call, or poiidx drops and recreates every table in
    poiidx_bats_db."""
    raw = (
        files("fledermap.services.data")
        .joinpath(_FILTER_CONFIG_FILE)
        .read_text(encoding="utf-8")
    )
    return yaml.safe_load(raw)


_connected = False


def ensure_connected(poiidx_database_url: str) -> None:
    """Idempotent within a process. poiidx.init() re-hashes the filter config
    against what's already stored on every call -- harmless to call more
    than once with the SAME config, but there's no reason to pay that
    comparison on every single job when a module-level flag can skip it
    after the first call in this process. Never reset except by tests."""
    global _connected
    if _connected:
        return
    poiidx.init(
        filter_config=_load_filter_config(),
        **_poiidx_connection_kwargs(poiidx_database_url),
    )
    _connected = True
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `hatch test tests/test_site_naming.py -v`
Expected: PASS

- [ ] **Step 6: Run the full check suite**

Run (all with `dangerouslyDisableSandbox: true`): `hatch fmt --check && hatch run types:check && hatch test`
Expected: all green. (`poiidx` ships its own `py.typed` marker — `types:check` needs no extra
stub-package entry for it.)

- [ ] **Step 7: Commit**

```bash
git add src/fledermap/services/data/poiidx_filter_config.yaml \
        src/fledermap/services/site_naming.py tests/test_site_naming.py
git commit -m "feat: poiidx filter config and connection helpers"
```

---

### Task 3: `name_site` — the cache-first query

**Files:**
- Modify: `src/fledermap/services/site_naming.py`
- Test: `tests/test_site_naming.py`

**Interfaces:**
- Consumes: `SiteNameCache` (`store/models.py`: `geohash: str`, `name: str`,
  `admin_path: str | None`, `fetched_at: datetime`); `poiidx.get_nearest_pois(shape, *,
  max_distance, limit) -> list[dict[str, Any]]` (each dict has `"name"` and `"rank"` keys, lower
  rank = more important — confirmed against poiidx's own example script);
  `poiidx.get_administrative_hierarchy_string(shape) -> str`.
- Produces: `name_site(db_session: OrmSession, lon: float, lat: float, *, radius_m: float) ->
  tuple[str, str | None] | None`. `None` means poiidx could not resolve anything at all (no nearby
  POI and no administrative hierarchy) — the caller (Task 4) leaves `Site.name` as `NULL` so the
  existing coordinate fallback still applies, and this result is deliberately NOT cached, so a
  later run can retry once poiidx's underlying region data improves.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_site_naming.py` (add `from datetime import UTC, datetime` and `from
sqlalchemy.orm import Session as OrmSession` to the top imports, alongside a `pytestmark =
pytest.mark.db` line and `from sqlalchemy.engine import Engine` — this file now needs the `engine`
fixture like every other `db`-marked test module):

```python
from datetime import UTC, datetime
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session as OrmSession

from fledermap.store.models import SiteNameCache

pytestmark = pytest.mark.db


def test_name_site_returns_the_cached_value_without_calling_poiidx(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("poiidx must not be called on a cache hit")

    monkeypatch.setattr(site_naming.poiidx, "get_nearest_pois", fail)
    monkeypatch.setattr(site_naming.poiidx, "get_administrative_hierarchy_string", fail)

    with OrmSession(engine) as session:
        session.add(
            SiteNameCache(
                geohash=site_naming._cache_key(13.405, 52.520),
                name="Tiergarten",
                admin_path="Berlin > Mitte",
                fetched_at=datetime(2026, 8, 28, tzinfo=UTC),
            ),
        )
        session.commit()

        result = site_naming.name_site(session, 13.405, 52.520, radius_m=300.0)

    assert result == ("Tiergarten", "Berlin > Mitte")


def test_name_site_prefers_the_lowest_rank_poi_over_the_nearest(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        site_naming.poiidx,
        "get_nearest_pois",
        lambda *a, **k: [
            {"name": "Nearby Bench", "rank": 23},
            {"name": "Tiergarten", "rank": 16},
        ],
    )
    monkeypatch.setattr(
        site_naming.poiidx,
        "get_administrative_hierarchy_string",
        lambda *a, **k: "Berlin > Mitte",
    )

    with OrmSession(engine) as session:
        result = site_naming.name_site(session, 13.405, 52.520, radius_m=300.0)

    assert result == ("Tiergarten", "Berlin > Mitte")


def test_name_site_falls_back_to_administrative_hierarchy_when_no_poi_found(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(site_naming.poiidx, "get_nearest_pois", lambda *a, **k: [])
    monkeypatch.setattr(
        site_naming.poiidx,
        "get_administrative_hierarchy_string",
        lambda *a, **k: "Berlin > Mitte",
    )

    with OrmSession(engine) as session:
        result = site_naming.name_site(session, 13.405, 52.520, radius_m=300.0)

    assert result == ("Berlin > Mitte", "Berlin > Mitte")


def test_name_site_returns_none_when_poiidx_resolves_nothing(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(site_naming.poiidx, "get_nearest_pois", lambda *a, **k: [])
    monkeypatch.setattr(
        site_naming.poiidx,
        "get_administrative_hierarchy_string",
        lambda *a, **k: "",
    )

    with OrmSession(engine) as session:
        result = site_naming.name_site(session, 13.405, 52.520, radius_m=300.0)
        cached = session.scalar(
            select(SiteNameCache).where(
                SiteNameCache.geohash == site_naming._cache_key(13.405, 52.520),
            ),
        )

    assert result is None
    assert cached is None  # deliberately not cached -- see name_site's docstring


def test_name_site_writes_through_the_cache_on_a_miss(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        site_naming.poiidx,
        "get_nearest_pois",
        lambda *a, **k: [{"name": "Tiergarten", "rank": 16}],
    )
    monkeypatch.setattr(
        site_naming.poiidx,
        "get_administrative_hierarchy_string",
        lambda *a, **k: "Berlin > Mitte",
    )

    with OrmSession(engine) as session:
        site_naming.name_site(session, 13.405, 52.520, radius_m=300.0)
        session.commit()

        cached = session.scalar(
            select(SiteNameCache).where(
                SiteNameCache.geohash == site_naming._cache_key(13.405, 52.520),
            ),
        )

    assert cached is not None
    assert cached.name == "Tiergarten"
    assert cached.admin_path == "Berlin > Mitte"
```

Add `from sqlalchemy import select` to the top imports too.

- [ ] **Step 2: Run tests to verify they fail**

Run: `hatch test tests/test_site_naming.py -k test_name_site -v`
Expected: FAIL — `AttributeError: module 'fledermap.services.site_naming' has no attribute
'name_site'` (and no `_cache_key`).

- [ ] **Step 3: Implement `_cache_key` and `name_site`**

Add these imports to the top of `src/fledermap/services/site_naming.py` (alongside the existing
ones):

```python
from datetime import UTC, datetime

from shapely.geometry import Point
from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from fledermap.store.models import SiteNameCache
```

Append to the end of `src/fledermap/services/site_naming.py`:

```python
_CANDIDATE_LIMIT = 5


def _cache_key(lon: float, lat: float) -> str:
    """Rounded-coordinate cache key. `SiteNameCache.geohash`'s own docstring
    (and the parent design spec) says "keyed on rounded coordinates" -- not
    the standard geohash algorithm, despite the column's name. 3 decimal
    degrees is roughly 111m at the equator, the same ballpark as
    site_eps_m's default clustering radius: coarse enough that a site's
    recomputed centroid landing a few metres away on a later derive_sites
    rebuild still hits the same cache entry, fine enough not to conflate two
    genuinely different nearby sites. Fits SiteNameCache.geohash's
    String(16) column: "52.520,13.405" is 13 characters."""
    return f"{lat:.3f},{lon:.3f}"


def name_site(
    db_session: OrmSession,
    lon: float,
    lat: float,
    *,
    radius_m: float,
) -> tuple[str, str | None] | None:
    """Resolve (name, admin_path) for a coordinate, cache-first through
    SiteNameCache. Returns None if poiidx could not resolve anything at all
    (no nearby POI AND no administrative hierarchy) -- the caller leaves
    Site.name as NULL so the existing coordinate fallback still applies.
    Deliberately NOT cached in that case, unlike a real resolution, so a
    later run can retry once poiidx's underlying region data improves.

    Caller must have already called `ensure_connected` this process --
    this function never calls poiidx.init() itself."""
    key = _cache_key(lon, lat)
    cached = db_session.scalar(
        select(SiteNameCache).where(SiteNameCache.geohash == key),
    )
    if cached is not None:
        return cached.name, cached.admin_path

    point = Point(lon, lat)
    pois = poiidx.get_nearest_pois(point, max_distance=radius_m, limit=_CANDIDATE_LIMIT)
    admin_path = poiidx.get_administrative_hierarchy_string(point) or None

    name: str | None
    if pois:
        # Lowest rank wins, not merely nearest (poiidx: lower rank = more
        # important) -- a well-known suburb further away should out-rank an
        # untagged/minor POI that happens to be closer.
        best = min(pois, key=lambda poi: poi["rank"])
        name = best["name"]
    else:
        name = admin_path

    if name is None:
        return None

    db_session.add(
        SiteNameCache(
            geohash=key,
            name=name,
            admin_path=admin_path,
            fetched_at=datetime.now(UTC),
        ),
    )
    return name, admin_path
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `hatch test tests/test_site_naming.py -v`
Expected: PASS

- [ ] **Step 5: Run the full check suite**

Run (all with `dangerouslyDisableSandbox: true`): `hatch fmt --check && hatch run types:check && hatch test`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add src/fledermap/services/site_naming.py tests/test_site_naming.py
git commit -m "feat: name_site cache-first poiidx query"
```

---

### Task 4: `name_site_task` — the `geo`-queue Procrastinate task

**Files:**
- Modify: `src/fledermap/jobs/tasks.py`
- Test: `tests/test_jobs_tasks.py`

**Interfaces:**
- Consumes: `site_naming.ensure_connected(poiidx_database_url: str) -> None`,
  `site_naming.name_site(db_session, lon, lat, *, radius_m) -> tuple[str, str | None] | None`
  (Task 3); `store.geo.decode_point(elem: object | None) -> tuple[float, float] | None` (existing);
  `context.additional_context["config"]: Config` and `context.additional_context["engine"]:
  Engine` (existing worker wiring, `cli/main.py::_run_worker_async`).
- Produces: `name_site_task` (a Procrastinate task object, `queue="geo"`, takes `site_id: int`),
  `_NAME_SITE_LOCK: str` (the shared execution lock — later used by `enqueue_site_naming` in Task
  5), `name_site_queueing_lock(site_id: int) -> str` (per-site dedup key — also used by Task 5).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_jobs_tasks.py`'s existing `from fledermap.jobs.tasks import (...)` block:

```python
    name_site_queueing_lock,
    name_site_task,
```

Add `from fledermap.services import site_naming` and `from geoalchemy2.elements import
WKTElement` to the top imports (`Config`, `Site`, `datetime`, `UTC`, `Path`, `jobs_app`,
`ensure_schema` are all already imported there — confirmed directly against the file; only
`site_naming` and `WKTElement` are new).

Append this test. It uses the SAME real-worker pattern as the existing media-task tests in this
file (`_run_worker`, defined near the top of this file — see e.g.
`test_render_spectrogram_task_writes_a_file`), not a hand-built fake context: Procrastinate's own
`JobContext` construction is real code worth exercising, and this project's own established
convention for testing a task's business logic already runs it through an actual (synchronous,
`wait=False`) worker pass rather than calling the task function directly. `queues=["geo"]` keeps
`run_ingest_cycle`'s periodic registration (which starts on ANY worker run against the shared
`jobs_app`, per this file's own `_run_worker` docstring) from also firing and needing
`additional_context` this test doesn't set up:

```python
def test_name_site_task_writes_the_resolved_name_onto_the_site(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(site_naming, "ensure_connected", lambda url: None)
    monkeypatch.setattr(
        site_naming,
        "name_site",
        lambda session, lon, lat, *, radius_m: ("Tiergarten", "Berlin > Mitte"),
    )
    jobs_app.open(engine)
    ensure_schema(jobs_app, engine)

    with OrmSession(engine) as session:
        site = Site(
            centroid=WKTElement("POINT(13.405 52.520)", srid=4326),
            radius_m=50.0,
            recording_count=1,
            first_at=datetime(2026, 8, 28, tzinfo=UTC),
            last_at=datetime(2026, 8, 28, tzinfo=UTC),
        )
        session.add(site)
        session.commit()
        site_id = site.id

    config = Config(
        database_url="postgresql://x/y",
        archive_roots=(Path("/archive"),),
        poiidx_database_url="postgresql://u:p@localhost/poiidx_bats_db",
        site_naming_radius_m=300.0,
    )
    name_site_task.configure(
        lock=_NAME_SITE_LOCK,
        queueing_lock=name_site_queueing_lock(site_id),
    ).defer(site_id=site_id)
    _run_worker(
        engine,
        wait=False,
        install_signal_handlers=False,
        listen_notify=False,
        queues=["geo"],
        additional_context={"config": config, "engine": engine},
    )

    with OrmSession(engine) as session:
        refreshed = session.get(Site, site_id)
        assert refreshed is not None
        assert refreshed.name == "Tiergarten"
        assert refreshed.admin_path == "Berlin > Mitte"


def test_name_site_task_leaves_the_site_unnamed_when_poiidx_resolves_nothing(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(site_naming, "ensure_connected", lambda url: None)
    monkeypatch.setattr(
        site_naming,
        "name_site",
        lambda session, lon, lat, *, radius_m: None,
    )
    jobs_app.open(engine)
    ensure_schema(jobs_app, engine)

    with OrmSession(engine) as session:
        site = Site(
            centroid=WKTElement("POINT(13.405 52.520)", srid=4326),
            radius_m=50.0,
            recording_count=1,
            first_at=datetime(2026, 8, 28, tzinfo=UTC),
            last_at=datetime(2026, 8, 28, tzinfo=UTC),
        )
        session.add(site)
        session.commit()
        site_id = site.id

    config = Config(
        database_url="postgresql://x/y",
        archive_roots=(Path("/archive"),),
        poiidx_database_url="postgresql://u:p@localhost/poiidx_bats_db",
        site_naming_radius_m=300.0,
    )
    name_site_task.configure(
        lock=_NAME_SITE_LOCK,
        queueing_lock=name_site_queueing_lock(site_id),
    ).defer(site_id=site_id)
    _run_worker(
        engine,
        wait=False,
        install_signal_handlers=False,
        listen_notify=False,
        queues=["geo"],
        additional_context={"config": config, "engine": engine},
    )

    with OrmSession(engine) as session:
        refreshed = session.get(Site, site_id)
        assert refreshed is not None
        assert refreshed.name is None


def test_name_site_queueing_lock_is_per_site() -> None:
    assert name_site_queueing_lock(1) != name_site_queueing_lock(2)
```

Add `_NAME_SITE_LOCK` to the same `from fledermap.jobs.tasks import (...)` block as
`name_site_queueing_lock`/`name_site_task` above (it's a plain string constant, fine to import
directly in a test file — no circularity concern for test modules, which nothing else imports).

- [ ] **Step 2: Run tests to verify they fail**

Run: `hatch test tests/test_jobs_tasks.py -k name_site -v`
Expected: FAIL — `ImportError: cannot import name 'name_site_task'`

- [ ] **Step 3: Add the task**

Add `from fledermap.store.geo import decode_point` and `from fledermap.store.models import Site`
to the top imports of `src/fledermap/jobs/tasks.py` (only add `Site` if not already imported —
check first; `Recording` is already imported there per the earlier grep, `Site` may not be).

**Do NOT add a top-level `from fledermap.services import site_naming` import.** Task 5 makes
`services/site_naming.py` import `name_site_task`/`app`/`name_site_queueing_lock`/`_NAME_SITE_LOCK`
FROM this module (`jobs.tasks`) at ITS top level — a top-level import in the other direction here
would be circular. This is the exact same reason `run_ingest_cycle` (a few lines below where this
task is added) already imports `enqueue_media` locally, inside its own function body, instead of
at the top of the file — its docstring explains it: "`services.media` imports task objects FROM
this module at ITS top level, so a top-level import here would be circular." `site_naming` gets
imported the same way, locally, inside `name_site_task`'s own body below.

After the existing `preview_lock_key` function (near the other `*_lock_key` helpers, before the
`@app.periodic` block), add:

```python
_NAME_SITE_LOCK = "poiidx-name-site"


def name_site_queueing_lock(site_id: int) -> str:
    return f"name_site:{site_id}"


@app.task(queue="geo", pass_context=True, retry=_RETRY)
def name_site_task(context: procrastinate.JobContext, site_id: int) -> None:
    """Resolve one Site's name via poiidx, off the request path entirely
    (design spec Goals: "never a web handler"). `_NAME_SITE_LOCK` -- a
    single static value shared by every name_site job, applied at defer
    time by `enqueue_site_naming` -- serializes execution across all of
    them, so two never-before-touched-region downloads can never race each
    other (design spec §3's corrected performance note)."""
    # Local import: see the note above Step 3's code block -- `site_naming`
    # imports FROM this module at ITS top level, so a top-level import here
    # would be circular. Safe here because by the time this function
    # actually runs, module import has long finished (same reasoning as
    # `run_ingest_cycle`'s own local `enqueue_media` import).
    from fledermap.services import site_naming

    config: Config = context.additional_context["config"]
    engine = context.additional_context["engine"]
    if config.poiidx_database_url is None:
        # Can only happen if a job was deferred, then the config changed
        # before it ran -- nothing to do, and nothing to retry usefully.
        return
    site_naming.ensure_connected(config.poiidx_database_url)

    with OrmSession(engine) as session:
        site = session.get(Site, site_id)
        if site is None:
            # derive_sites rebuilt again since this job was enqueued and
            # this row no longer exists -- not an error.
            return
        point = decode_point(site.centroid)
        if point is None:
            return
        lon, lat = point
        resolved = site_naming.name_site(
            session,
            lon,
            lat,
            radius_m=config.site_naming_radius_m,
        )
        if resolved is not None:
            site.name, site.admin_path = resolved
        session.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `hatch test tests/test_jobs_tasks.py -v`
Expected: PASS (all tests, including the pre-existing ones — this task adds imports at the top of
a file with many other tests, so a full-file run is the real check).

- [ ] **Step 5: Run the full check suite**

Run (all with `dangerouslyDisableSandbox: true`): `hatch fmt --check && hatch run types:check && hatch test`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add src/fledermap/jobs/tasks.py tests/test_jobs_tasks.py
git commit -m "feat: name_site_task on the geo queue"
```

---

### Task 5: `enqueue_site_naming` — cache-first enqueue, wired into both derive call sites

**Files:**
- Modify: `src/fledermap/services/site_naming.py`
- Modify: `src/fledermap/cli/main.py` (the `derive` command)
- Modify: `src/fledermap/jobs/tasks.py` (`run_ingest_cycle`)
- Test: `tests/test_site_naming.py`

**Interfaces:**
- Consumes: `name_site_task`, `_NAME_SITE_LOCK`, `name_site_queueing_lock` (Task 4);
  `store.geo.decode_point` (existing); `Site.name: Mapped[str | None]` and
  `Site.admin_path: Mapped[str | None]` (existing, confirmed already nullable `Text` columns —
  Phase 2 created them as schema, `store/models.py:255-256`; no migration needed).
- Produces: `enqueue_site_naming(db_session: OrmSession, engine: Engine, *, poiidx_database_url:
  str | None, radius_m: float) -> int` — the number of `name_site_task` jobs actually deferred
  (cache hits are resolved directly and not counted; unconfigured poiidx returns `0` with no
  side effects at all). Task 6's CLI command calls this too.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_site_naming.py` (add `from geoalchemy2.elements import WKTElement` and
`from fledermap.store.models import Site` to the top imports, alongside `from
fledermap.jobs.app import ensure_schema` and `from fledermap.jobs.tasks import app as jobs_app`):

```python
from geoalchemy2.elements import WKTElement

from fledermap.jobs.app import ensure_schema
from fledermap.jobs.tasks import app as jobs_app
from fledermap.store.models import Site


def _unnamed_site(lon: float, lat: float) -> Site:
    return Site(
        centroid=WKTElement(f"POINT({lon} {lat})", srid=4326),
        radius_m=50.0,
        recording_count=1,
        first_at=datetime(2026, 8, 28, tzinfo=UTC),
        last_at=datetime(2026, 8, 28, tzinfo=UTC),
    )


def test_enqueue_site_naming_is_a_noop_when_poiidx_is_unconfigured(
    engine: Engine,
) -> None:
    jobs_app.open(engine)
    ensure_schema(jobs_app, engine)

    with OrmSession(engine) as session:
        session.add(_unnamed_site(13.405, 52.520))
        session.commit()

        count = site_naming.enqueue_site_naming(
            session,
            engine,
            poiidx_database_url=None,
            radius_m=300.0,
        )

    assert count == 0


def test_enqueue_site_naming_resolves_a_cache_hit_directly_without_a_job(
    engine: Engine,
) -> None:
    jobs_app.open(engine)
    ensure_schema(jobs_app, engine)

    with OrmSession(engine) as session:
        session.add(
            SiteNameCache(
                geohash=site_naming._cache_key(13.405, 52.520),
                name="Tiergarten",
                admin_path="Berlin > Mitte",
                fetched_at=datetime(2026, 8, 28, tzinfo=UTC),
            ),
        )
        site = _unnamed_site(13.405, 52.520)
        session.add(site)
        session.commit()
        site_id = site.id

        count = site_naming.enqueue_site_naming(
            session,
            engine,
            poiidx_database_url="postgresql://u:p@localhost/poiidx_bats_db",
            radius_m=300.0,
        )
        session.commit()

    assert count == 0
    with OrmSession(engine) as session:
        refreshed = session.get(Site, site_id)
        assert refreshed is not None
        assert refreshed.name == "Tiergarten"
        assert refreshed.admin_path == "Berlin > Mitte"


def test_enqueue_site_naming_defers_a_job_on_a_cache_miss(engine: Engine) -> None:
    jobs_app.open(engine)
    ensure_schema(jobs_app, engine)

    with OrmSession(engine) as session:
        session.add(_unnamed_site(13.405, 52.520))
        session.commit()

        count = site_naming.enqueue_site_naming(
            session,
            engine,
            poiidx_database_url="postgresql://u:p@localhost/poiidx_bats_db",
            radius_m=300.0,
        )

    assert count == 1


def test_enqueue_site_naming_ignores_a_site_that_already_has_a_name(
    engine: Engine,
) -> None:
    jobs_app.open(engine)
    ensure_schema(jobs_app, engine)

    with OrmSession(engine) as session:
        named = _unnamed_site(13.405, 52.520)
        named.name = "Already Named"
        session.add(named)
        session.commit()

        count = site_naming.enqueue_site_naming(
            session,
            engine,
            poiidx_database_url="postgresql://u:p@localhost/poiidx_bats_db",
            radius_m=300.0,
        )

    assert count == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `hatch test tests/test_site_naming.py -k enqueue_site_naming -v`
Expected: FAIL — `AttributeError: module 'fledermap.services.site_naming' has no attribute
'enqueue_site_naming'`

- [ ] **Step 3: Implement `enqueue_site_naming`**

Add these imports to the top of `src/fledermap/services/site_naming.py` (this module importing
FROM `jobs.tasks` at top level is the safe direction — see Task 4's note on why the reverse
would be circular):

```python
import procrastinate
from sqlalchemy.engine import Engine

from fledermap.jobs.tasks import (
    _NAME_SITE_LOCK,
    name_site_queueing_lock,
    name_site_task,
)
from fledermap.jobs.tasks import app as jobs_app
from fledermap.store.geo import decode_point
from fledermap.store.models import Site
```

Append to the end of `src/fledermap/services/site_naming.py`:

```python
def enqueue_site_naming(
    db_session: OrmSession,
    engine: Engine,
    *,
    poiidx_database_url: str | None,
    radius_m: float,
) -> int:
    """Cache-first resolution for every Site still missing a name. Called
    right after derive_sites()+commit() -- its wholesale rebuild resets
    every site's name to NULL on every run (design spec §4) -- and from the
    `backfill-site-names` CLI command. Returns the number of name_site jobs
    actually deferred; a SiteNameCache hit is resolved directly onto the
    Site row instead and does not count.

    A true no-op when poiidx isn't configured at all -- the "optional
    integration, current behaviour preserved" goal (design spec Goals)."""
    if not poiidx_database_url:
        return 0

    try:
        jobs_app.open(engine)
    except NotImplementedError:
        pass  # already open inside a running worker -- see enqueue_media's docstring

    unnamed = db_session.scalars(select(Site).where(Site.name.is_(None))).all()

    enqueued = 0
    for site in unnamed:
        point = decode_point(site.centroid)
        if point is None:
            continue
        lon, lat = point
        key = _cache_key(lon, lat)
        cached = db_session.scalar(
            select(SiteNameCache).where(SiteNameCache.geohash == key),
        )
        if cached is not None:
            site.name = cached.name
            site.admin_path = cached.admin_path
            continue
        try:
            name_site_task.configure(
                lock=_NAME_SITE_LOCK,
                queueing_lock=name_site_queueing_lock(site.id),
            ).defer(site_id=site.id)
        except procrastinate.exceptions.AlreadyEnqueued:
            continue
        enqueued += 1
    return enqueued
```

- [ ] **Step 4: Wire into the `derive` CLI command**

In `src/fledermap/cli/main.py`, inside the `derive` command, immediately after the existing:

```python
        site_report = derive_sites(
            session,
            eps_m=config.site_eps_m,
            min_points=config.site_min_points,
        )
        session.commit()
```

add:

```python
        named_count = enqueue_site_naming(
            session,
            engine,
            poiidx_database_url=config.poiidx_database_url,
            radius_m=config.site_naming_radius_m,
        )
        session.commit()
```

Add `enqueue_site_naming` to the existing `from fledermap.services.derive import derive_sites`
import line's neighborhood — add a new import line right after it:

```python
from fledermap.services.site_naming import enqueue_site_naming
```

Update the existing `click.echo` line that prints `site_report` to also report naming:

```python
        click.echo(
            f"sites: {site_report.site_count}  unclustered {site_report.unclustered}  "
            f"naming jobs enqueued {named_count}",
        )
```

- [ ] **Step 5: Wire into `run_ingest_cycle`**

In `src/fledermap/jobs/tasks.py`'s `run_ingest_cycle`, immediately after the existing:

```python
        site_report = derive_sites(
            session,
            eps_m=config.site_eps_m,
            min_points=config.site_min_points,
        )
        session.commit()
```

add (using a local import, matching the existing local `from fledermap.services.media import
enqueue_media` a few lines above it in this same function, for the same circular-import reason —
`services/site_naming.py` imports FROM `jobs.tasks` at its own top level, so `jobs/tasks.py`
cannot import it back at ITS top level):

```python
        from fledermap.services.site_naming import enqueue_site_naming

        enqueue_site_naming(
            session,
            engine,
            poiidx_database_url=config.poiidx_database_url,
            radius_m=config.site_naming_radius_m,
        )
        session.commit()
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `hatch test tests/test_site_naming.py tests/test_cli.py tests/test_jobs_tasks.py -v`
Expected: PASS

- [ ] **Step 7: Run the full check suite**

Run (all with `dangerouslyDisableSandbox: true`): `hatch fmt --check && hatch run types:check && hatch test`
Expected: all green. If `ruff check` flags the two-step import consolidation in Step 3 as unsorted,
let `hatch fmt` (not `--check`) auto-fix it, then re-run `--check`.

- [ ] **Step 8: Commit**

```bash
git add src/fledermap/services/site_naming.py src/fledermap/cli/main.py \
        src/fledermap/jobs/tasks.py tests/test_site_naming.py
git commit -m "feat: enqueue_site_naming, wired into both derive call sites"
```

---

### Task 6: `fledermap backfill-site-names` CLI command, and closing the P4-1 deviation note

**Files:**
- Modify: `src/fledermap/cli/main.py` (new command)
- Modify: `docs/superpowers/specs/2026-08-25-fledermap-phase4-map-design.md` (append a closing
  note to P4-1 — do not rewrite its original text, matching this project's own "dated deviation
  note" convention)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `enqueue_site_naming` (Task 5).
- Produces: nothing further consumes this — it's the last piece of the feature.

- [ ] **Step 1: Write the failing tests**

This project's `test_enqueue_media_command_reports_disk_gap_but_avoids_duplicate_jobs`
(`tests/test_cli.py`) is the sibling precedent this mirrors: a `clean_database_url` fixture, a
`CliRunner`, and an `env` dict of `FLEDERMAP_*` variables passed to `runner.invoke`. Append these
two tests to `tests/test_cli.py`:

```python
def test_backfill_site_names_command_reports_zero_with_no_sites(
    clean_database_url: str,
    tmp_path: Path,
) -> None:
    """No sites exist yet -- the command must still succeed and report
    "enqueued 0", whether or not poiidx is configured at all (it isn't,
    here)."""
    env = {
        "FLEDERMAP_DATABASE_URL": clean_database_url,
        "FLEDERMAP_ARCHIVE_ROOTS": str(tmp_path / "archive"),
    }
    runner = CliRunner()

    result = runner.invoke(cli, ["backfill-site-names"], env=env)

    assert result.exit_code == 0, result.output
    assert "enqueued 0" in result.output


def test_backfill_site_names_command_enqueues_an_unnamed_site(
    clean_database_url: str,
    tmp_path: Path,
) -> None:
    engine = make_engine(clean_database_url)
    _run_migrations(clean_database_url)
    with OrmSession(engine) as session:
        session.add(
            Site(
                centroid=WKTElement("POINT(13.405 52.520)", srid=4326),
                radius_m=50.0,
                recording_count=1,
                first_at=datetime(2026, 8, 28, tzinfo=UTC),
                last_at=datetime(2026, 8, 28, tzinfo=UTC),
            ),
        )
        session.commit()
    engine.dispose()

    env = {
        "FLEDERMAP_DATABASE_URL": clean_database_url,
        "FLEDERMAP_ARCHIVE_ROOTS": str(tmp_path / "archive"),
        "FLEDERMAP_POIIDX_DATABASE_URL": "postgresql://u:p@localhost/poiidx_bats_db",
    }
    runner = CliRunner()

    result = runner.invoke(cli, ["backfill-site-names"], env=env)

    assert result.exit_code == 0, result.output
    assert "enqueued 1" in result.output
```

Check the top of `tests/test_cli.py` for `WKTElement`, `Site`, `datetime`, `UTC`, `OrmSession`,
`make_engine`, `_run_migrations`, `cli`, `CliRunner` — add whichever of these imports aren't
already present (`from geoalchemy2.elements import WKTElement`, `from fledermap.store.models
import Site`, `from datetime import UTC, datetime`; the rest are near-certainly already imported
given the sibling `enqueue-media` test above uses `make_engine`/`cli`/`CliRunner` already).

- [ ] **Step 2: Run the test to verify it fails**

Run: `hatch test tests/test_cli.py -k backfill_site_names -v`
Expected: FAIL — the command doesn't exist yet (a `click` "No such command" failure, or an
`AttributeError` importing it, depending on how the test invokes it).

- [ ] **Step 3: Add the CLI command**

In `src/fledermap/cli/main.py`, add `enqueue_site_naming` to the imports if Task 5 didn't already
place it usably (it did — reuse the same import), then add this command after the existing
`enqueue_media_command`:

```python
@cli.command(name="backfill-site-names")
def backfill_site_names_command() -> None:
    """Resolve names for any Site still missing one via poiidx -- for sites
    that predate this feature, or whose name_site job failed past its retry
    budget. A no-op (reports "enqueued 0") if FLEDERMAP_POIIDX_DATABASE_URL
    isn't configured."""
    try:
        config = Config.from_env()
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc

    engine = make_engine(config.database_url)
    _run_migrations(config.database_url)
    ensure_schema(jobs_app, engine)

    with OrmSession(engine) as session:
        count = enqueue_site_naming(
            session,
            engine,
            poiidx_database_url=config.poiidx_database_url,
            radius_m=config.site_naming_radius_m,
        )
        session.commit()

    click.echo(f"enqueued {count}")
```

- [ ] **Step 4: Close the P4-1 deviation note**

In `docs/superpowers/specs/2026-08-25-fledermap-phase4-map-design.md`, find the P4-1 section
(search for `**P4-1:`). Immediately after its existing `**Resolution:**` paragraph (do not edit
the original text above it), append:

```markdown

**Closed 2026-08-28:** built as its own task after all —
`docs/superpowers/specs/2026-08-28-fledermap-poiidx-site-naming-design.md`. The `geo` queue now
has `name_site_task`; `SiteNameCache` is read/written; the rounded-coordinate fallback above
still applies whenever poiidx isn't configured or hasn't resolved a site yet.
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `hatch test tests/test_cli.py -v`
Expected: PASS

- [ ] **Step 6: Run the full check suite**

Run (all with `dangerouslyDisableSandbox: true`): `hatch fmt --check && hatch run types:check && hatch test`
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add src/fledermap/cli/main.py tests/test_cli.py \
        docs/superpowers/specs/2026-08-25-fledermap-phase4-map-design.md
git commit -m "feat: backfill-site-names CLI command, close P4-1"
```

---

## Final Verification

After all six tasks:

```bash
hatch fmt --check
hatch run types:check
hatch test
```

All green, no warnings. Then a manual smoke check against a real (or throwaway) `poiidx_bats_db`:
set `FLEDERMAP_POIIDX_DATABASE_URL`, run `fledermap derive` against a database with at least one
unclustered site's worth of stationary recordings, run `fledermap worker --no-wait` once to drain
the `geo` queue, and confirm the site's `name`/`admin_path` are populated (`fledermap`'s own
`sessions`/map pages, or a direct `SELECT name, admin_path FROM site`). Confirm a second
`fledermap derive` run resolves the same site's name from `SiteNameCache` without poiidx blocking
again (check the `geo` queue stays empty or resolves near-instantly the second time).
