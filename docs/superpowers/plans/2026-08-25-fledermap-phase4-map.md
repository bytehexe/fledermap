# Fledermap Phase 4 (Map) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Serve a Leaflet map (`fledermap serve`) showing Recordings and Sites as two
independently toggleable layers, narrowed by server-side filters, with noise hidden by
default — Phase 4 exactly as scoped in the parent spec's phasing table.

**Architecture:** A new `src/fledermap/web/` package (Flask, Jinja) sitting on top of two
new pure `services/` functions and the existing SQLAlchemy models — `web/api` and
`web/views` both call `services`, never `store` directly. Static JS/CSS (Leaflet,
markercluster, HTMX, Alpine) are fetched by a pinned-version, hash-verified script into a
configurable location, not committed to git and not loaded from a CDN at runtime. Filters
update the existing Leaflet layers in place via `fetch()`, never via an HTMX swap that
touches the map itself.

**Tech Stack:** Flask, Jinja2, `platformdirs`, Leaflet 1.9.4 + Leaflet.markercluster 1.5.3
(fetched, not a Python dependency), HTMX 2.0.3 + Alpine.js 3.14.8 (same), the existing
SQLAlchemy 2.0 + GeoAlchemy2 + Postgres stack.

**Spec:** `docs/superpowers/specs/2026-08-25-fledermap-phase4-map-design.md` (and the
parent `docs/superpowers/specs/2026-08-23-fledermap-design.md` §9/§10 it implements).

## Global Constraints

- **`hatch` only.** Never `pip`, never bare `python`/`python3`, never `PYTHONPATH`.
  `hatch test`, `hatch run ruff:ruff check .` / `hatch run ruff:ruff format --check
  --diff .` (NOT bare `hatch fmt`), `hatch run types:check`.
- **Test output must be pristine** — a warning is a defect, fix the cause, never
  `filterwarnings`.
- **`hatch run types:check` covers `tests/` and `scripts/` too** — test code must
  type-check for real.
- **New third-party imports mypy can't resolve go in `[tool.hatch.envs.types]`'s
  `extra-dependencies`**, or a scoped `[[tool.mypy.overrides]]` if the package ships
  no types at all — never a global `ignore_missing_imports`.
- **`db`-marked tests need Docker, which the command sandbox blocks** — run with
  `dangerouslyDisableSandbox: true`.
- **`web/api` and `web/views` call `services/`, never `store/` directly** (design
  spec §3/§4) — the SPA-migration escape hatch depends on this boundary holding.
- **`media/`'s purity rule does not apply to `web/`** — `web/` is expected to import
  the ORM models directly for read-only display purposes; only `media/` stays free
  of DB/queue awareness.
- **The map (`L.map(...)`) is constructed exactly once per page load and never
  swapped or destroyed** — filters update its existing layers via `fetch()`, never
  via `hx-swap` on any element containing the map (design spec §7, directly
  targeting parent spec §10's tripwire #1).
- **`current_best_identification` is a `services` function, not a stored column**
  (parent spec §5) — recomputed on every call so the precedence order can change
  without a migration.
- **`FLEDERMAP_STATIC_ROOT` is optional**, unlike `FLEDERMAP_MEDIA_ROOT` — defaults
  via `platformdirs.user_cache_dir("fledermap")` when unset (design spec §5, P4-5).
- **`verdict IN ('noise', 'no_id')` is excluded by default** on both GeoJSON
  endpoints, unless the caller explicitly asks for `verdict=noise`, `verdict=no_id`,
  or `verdict=all` (design spec §6).
- **GeoJSON responses cap at 2000 features**, reporting `truncated: true` rather than
  a partial-and-silent result (design spec §6, P4-7).
- **No JS test framework, no npm, no vendored-in-git assets.** Vendor JS/CSS is
  fetched by `scripts/fetch_vendor_assets.py` at pinned versions with SHA-256
  verification (design spec §5, P4-4) — network access is a setup-time concern, not
  a test-time one.

---

## Task 1: `Config.static_root` and `resolve_static_root()`

**Files:**
- Modify: `src/fledermap/config.py`
- Test: `tests/test_config.py` (extend)

**Interfaces:**
- Consumes: nothing new.
- Produces: `resolve_static_root() -> Path` (standalone function, importable
  independently of `Config`) and `Config.static_root: Path`, used by Task 3
  (`scripts/fetch_vendor_assets.py`) and Task 5 (`web/app.py`'s `create_app`).

- [ ] **Step 1: Add `platformdirs` dependency**

Add `"platformdirs"` to `[project] dependencies` in `pyproject.toml`, appended
after `"scikit-learn"`.

- [ ] **Step 2: Write the failing tests**

```python
# add to tests/test_config.py

def test_static_root_defaults_via_platformdirs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_MEDIA_ROOT, str(tmp_path / "media"))
    monkeypatch.delenv(ENV_STATIC_ROOT, raising=False)

    config = Config.from_env(tmp_path)

    import platformdirs

    assert config.static_root == Path(platformdirs.user_cache_dir("fledermap")).resolve()


def test_static_root_respects_explicit_env_var(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_MEDIA_ROOT, str(tmp_path / "media"))
    explicit = tmp_path / "static"
    monkeypatch.setenv(ENV_STATIC_ROOT, str(explicit))

    config = Config.from_env(tmp_path)

    assert config.static_root == explicit.resolve()


def test_resolve_static_root_matches_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """scripts/fetch_vendor_assets.py (Task 3) calls resolve_static_root()
    directly, without building a full Config -- this pins that it agrees with
    what Config.static_root resolves to, given the same environment."""
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_MEDIA_ROOT, str(tmp_path / "media"))
    monkeypatch.setenv(ENV_STATIC_ROOT, str(tmp_path / "static"))

    config = Config.from_env(tmp_path)

    assert resolve_static_root() == config.static_root
```

Add `ENV_STATIC_ROOT` and `resolve_static_root` to the existing import block at
the top of `tests/test_config.py`.

- [ ] **Step 3: Run tests to verify they fail**

Run: `hatch test tests/test_config.py -v`
Expected: FAIL with `ImportError: cannot import name 'ENV_STATIC_ROOT'`

- [ ] **Step 4: Implement**

Add to `src/fledermap/config.py`, alongside the other `ENV_*` constants:

```python
ENV_STATIC_ROOT = "FLEDERMAP_STATIC_ROOT"
```

Add near the top of the module, after the imports (needs `import platformdirs`
added to the import block):

```python
def resolve_static_root() -> Path:
    """Where fetched vendor JS/CSS assets (Leaflet, HTMX, Alpine -- see
    scripts/fetch_vendor_assets.py) live. Optional, unlike `media_root`: these
    are small, regenerable, non-precious cache-like files, not an operator's
    deliberate data-placement decision, so a `platformdirs` cache-dir default
    is the right fit here in a way it wasn't for `media_root` (design spec
    P4-5). A standalone function, not a `Config` method, so the fetch script
    can call it without building a full `Config` (which requires
    `archive_root`, irrelevant to fetching static assets)."""
    raw = os.environ.get(ENV_STATIC_ROOT)
    if raw:
        return Path(raw).resolve()
    return Path(platformdirs.user_cache_dir("fledermap")).resolve()
```

Add the field to the `Config` dataclass, after `media_root`:

```python
    # Optional (see resolve_static_root's docstring for why this differs from
    # media_root's required-with-a-sentinel shape). default_factory, not a
    # plain default, so it's actually called at construction time and picks
    # up whatever FLEDERMAP_STATIC_ROOT is set to at that moment -- including
    # in tests that monkeypatch the env var before calling `from_env`.
    static_root: Path = field(default_factory=resolve_static_root)
```

No change needed to `from_env`'s body: because `static_root` uses
`default_factory`, it's computed automatically whenever `cls(...)` is called
without an explicit `static_root=` argument, and `from_env` doesn't pass one.

- [ ] **Step 5: Run tests to verify they pass**

Run: `hatch test tests/test_config.py -v`
Expected: PASS, including the 3 new tests.

- [ ] **Step 6: Full verification**

Run: `hatch run types:check`, `hatch run ruff:ruff check .`, `hatch run
ruff:ruff format --check --diff .` — expect clean.
Run: `hatch test` (Docker, unsandboxed) — full suite, expect all passing,
pristine.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml src/fledermap/config.py tests/test_config.py
git commit -m "feat: Config.static_root (FLEDERMAP_STATIC_ROOT, optional, platformdirs default)"
```

---

## Task 2: `services/current_best.py`

**Files:**
- Create: `src/fledermap/services/current_best.py`
- Test: `tests/test_current_best.py`

**Interfaces:**
- Consumes: `Recording`, `Identification`, `IdSource`, `Verdict` (existing models).
- Produces: `current_best_identification(recording: Recording) -> Identification |
  None`, used by Task 4 (`services/map_query.py`) and Task 6
  (`web/api/geojson.py`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_current_best.py
from __future__ import annotations

from datetime import UTC, datetime

from fledermap.domain.codes import IdSource, Verdict
from fledermap.services.current_best import current_best_identification
from fledermap.store.models import Identification, Recording


def _recording(*identifications: Identification) -> Recording:
    r = Recording(
        audio_hash="a" * 64,
        path="x.wav",
        recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
    )
    r.identifications = list(identifications)
    return r


def _ident(
    source: IdSource,
    *,
    taxon_id: int | None = 1,
    verdict: Verdict = Verdict.SPECIES,
    superseded: bool = False,
    first_seen_at: datetime = datetime(2026, 8, 25, tzinfo=UTC),
) -> Identification:
    return Identification(
        source=source,
        verdict=verdict,
        taxon_id=taxon_id,
        first_seen_at=first_seen_at,
        superseded_at=datetime(2026, 8, 26, tzinfo=UTC) if superseded else None,
    )


def test_manual_wins_over_every_other_source() -> None:
    r = _recording(
        _ident(IdSource.EMT_GUANO),
        _ident(IdSource.MANUAL, taxon_id=2),
    )

    best = current_best_identification(r)

    assert best is not None
    assert best.source == IdSource.MANUAL
    assert best.taxon_id == 2


def test_emt_guano_beats_emt_wamd_beats_emt_filename() -> None:
    r = _recording(
        _ident(IdSource.EMT_FILENAME, taxon_id=1),
        _ident(IdSource.EMT_WAMD, taxon_id=2),
        _ident(IdSource.EMT_GUANO, taxon_id=3),
    )

    best = current_best_identification(r)

    assert best is not None
    assert best.source == IdSource.EMT_GUANO
    assert best.taxon_id == 3


def test_superseded_identifications_are_ignored() -> None:
    r = _recording(
        _ident(IdSource.MANUAL, superseded=True),
        _ident(IdSource.EMT_GUANO, taxon_id=5),
    )

    best = current_best_identification(r)

    assert best is not None
    assert best.source == IdSource.EMT_GUANO
    assert best.taxon_id == 5


def test_no_identifications_returns_none() -> None:
    r = _recording()

    assert current_best_identification(r) is None


def test_all_superseded_returns_none() -> None:
    r = _recording(_ident(IdSource.EMT_GUANO, superseded=True))

    assert current_best_identification(r) is None


def test_two_non_superseded_claims_from_the_same_source_break_on_recency() -> None:
    """Possible but rare: a source re-reports under a different
    source_version/raw_label before the earlier claim is superseded. Not
    arbitrary dict-iteration-order behaviour -- the most recently first-seen
    claim wins."""
    r = _recording(
        _ident(IdSource.EMT_GUANO, taxon_id=1, first_seen_at=datetime(2026, 1, 1, tzinfo=UTC)),
        _ident(IdSource.EMT_GUANO, taxon_id=2, first_seen_at=datetime(2026, 6, 1, tzinfo=UTC)),
    )

    best = current_best_identification(r)

    assert best is not None
    assert best.taxon_id == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `hatch test tests/test_current_best.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'fledermap.services.current_best'`

- [ ] **Step 3: Implement**

```python
# src/fledermap/services/current_best.py
"""'Current best' identification -- design spec P4-2, resolving the parent
spec's (section 5) explicit but never-implemented rule: "manual wins, else
highest-priority non-superseded source by configured order." Not a stored
column -- recomputed on every call, so the order below can change without a
migration."""

from __future__ import annotations

from datetime import UTC, datetime

from fledermap.domain.codes import IdSource
from fledermap.store.models import Identification, Recording

# The configured order (design spec P4-2). BATDETECT2/BATTYBIRDNET/KALEIDOSCOPE
# are deliberately absent: no source in this codebase produces them yet (v2),
# so their eventual position is unobserved and revisable without migration --
# they simply never match any candidate today.
_PRECEDENCE: tuple[IdSource, ...] = (
    IdSource.MANUAL,
    IdSource.EMT_MANUAL,
    IdSource.EMT_GUANO,
    IdSource.EMT_WAMD,
    IdSource.EMT_FILENAME,
)

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def current_best_identification(recording: Recording) -> Identification | None:
    """Manual wins, else the highest-priority non-superseded source. Ties
    within one source (two non-superseded claims differing only in
    `source_version`/`raw_label` -- possible but rare, since a rescan normally
    supersedes a source's prior claim before adding a new one) break on the
    most recently first-seen claim, not on dict/list iteration order."""
    candidates = [i for i in recording.identifications if i.superseded_at is None]
    for source in _PRECEDENCE:
        matches = [i for i in candidates if i.source == source]
        if matches:
            return max(matches, key=lambda i: i.first_seen_at or _EPOCH)
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `hatch test tests/test_current_best.py -v`
Expected: PASS (6/6)

- [ ] **Step 5: Full verification**

Run: `hatch run types:check`, `hatch run ruff:ruff check .`, `hatch run
ruff:ruff format --check --diff .` — expect clean.
Run: `hatch test` (Docker, unsandboxed) — full suite, expect all passing,
pristine.

- [ ] **Step 6: Commit**

```bash
git add src/fledermap/services/current_best.py tests/test_current_best.py
git commit -m "feat: current_best_identification -- manual wins, else configured source order"
```

---

## Task 3: `scripts/fetch_vendor_assets.py`

**Files:**
- Create: `scripts/fetch_vendor_assets.py`
- Test: `tests/test_fetch_vendor_assets.py`

**Interfaces:**
- Consumes: `resolve_static_root` (Task 1).
- Produces: a `<static_root>/vendor/` directory populated with Leaflet,
  Leaflet.markercluster, HTMX, and Alpine at the pinned versions below, used by
  Task 5 (`web/app.py`'s vendor `Blueprint`) and Task 7 (templates referencing
  `url_for("vendor.static", filename=...)`).

Every URL and SHA-256 below was fetched and computed directly (not invented)
before this plan was written: `leaflet@1.9.4`/`leaflet.markercluster@1.5.3`/
`htmx.org@2.0.3`/`alpinejs@3.14.8` from `unpkg.com`, verified with `sha256sum`.
Leaflet's own CSS references three relative image files
(`images/marker-icon.png`, `images/layers.png`, `images/layers-2x.png`), and its
JS additionally needs `marker-icon-2x.png`/`marker-shadow.png` for its default
marker icon (`L.Icon.Default`, a well-known Leaflet-without-a-bundler gotcha) --
all five are fetched alongside the two Leaflet files.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_fetch_vendor_assets.py
from __future__ import annotations

import hashlib
import urllib.request
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from scripts.fetch_vendor_assets import IntegrityError, VendorAsset, fetch_all, verify


def test_verify_accepts_matching_hash() -> None:
    data = b"hello world"
    expected = hashlib.sha256(data).hexdigest()

    verify(data, expected)  # must not raise


def test_verify_rejects_mismatched_hash() -> None:
    with pytest.raises(IntegrityError, match="expected sha256"):
        verify(b"hello world", "0" * 64)


def test_fetch_all_writes_verified_assets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercises the full fetch-verify-write path against a fake network
    response -- no live network call. This is a case where mocking the
    external dependency (the network) is appropriate: hitting a real CDN in a
    test run is exactly what this project's test suite avoids elsewhere."""
    payload = b"pretend this is leaflet.js"
    digest = hashlib.sha256(payload).hexdigest()
    fake_asset = VendorAsset(
        url="https://example.invalid/fake.js",
        sha256=digest,
        relative_path="fake.js",
    )

    fake_response = MagicMock()
    fake_response.read.return_value = payload
    fake_response.__enter__.return_value = fake_response
    fake_response.__exit__.return_value = False
    monkeypatch.setattr(urllib.request, "urlopen", lambda url: fake_response)  # noqa: ARG005

    fetch_all(tmp_path, assets=(fake_asset,))

    written = (tmp_path / "fake.js").read_bytes()
    assert written == payload


def test_fetch_all_refuses_a_tampered_response(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_asset = VendorAsset(
        url="https://example.invalid/fake.js",
        sha256="0" * 64,  # deliberately wrong
        relative_path="fake.js",
    )

    fake_response = MagicMock()
    fake_response.read.return_value = b"anything"
    fake_response.__enter__.return_value = fake_response
    fake_response.__exit__.return_value = False
    monkeypatch.setattr(urllib.request, "urlopen", lambda url: fake_response)  # noqa: ARG005

    with pytest.raises(IntegrityError):
        fetch_all(tmp_path, assets=(fake_asset,))

    assert not (tmp_path / "fake.js").exists()
```

No `scripts/__init__.py` is needed. `tests/test_check_yaml.py` invokes
`check_yaml.py` as a subprocess rather than importing it, but that pattern
doesn't fit here: this task's tests need to monkeypatch
`urllib.request.urlopen`, which only works in-process, not across a
subprocess boundary. Confirmed empirically before writing this task: `tests/`
has its own `__init__.py`, so pytest's default ("prepend") import mode walks
up to the first `__init__.py`-less directory -- the repo root -- and adds
*that* to `sys.path`, making `scripts` importable as a namespace package (no
`__init__.py` required, Python 3.3+) with no other change needed. A
throwaway test (`from scripts.check_yaml import check_file`, dropped
immediately after) confirmed this against this exact repo rather than
assuming it.

- [ ] **Step 2: Run tests to verify they fail**

Run: `hatch test tests/test_fetch_vendor_assets.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.fetch_vendor_assets'`
(or an import-style error, matching whatever `check_yaml.py`'s own tests
revealed in Step 1's check above -- adjust the test file's import line to
match, then re-run).

- [ ] **Step 3: Implement**

```python
# scripts/fetch_vendor_assets.py
"""Fetch pinned-version JS/CSS assets into the configured static root
(design spec section 5, decision P4-4).

Run manually at setup/deploy time (documented in CLAUDE.md's Environment
gotchas) -- needs network access, so it is NOT part of the test suite's own
execution path (tests/test_fetch_vendor_assets.py exercises the
verify/fetch/write logic against a fake response instead). Each asset's
SHA-256 is checked against the downloaded bytes before anything is written;
a mismatch means the CDN served something other than what was pinned when
this script was last updated, and nothing is written for that asset.
"""

from __future__ import annotations

import hashlib
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from fledermap.config import resolve_static_root


@dataclass(frozen=True)
class VendorAsset:
    url: str
    sha256: str
    relative_path: str  # where it lands under <static_root>/vendor/


# Fetched and hashed directly against unpkg.com before this plan was written
# -- not invented. Leaflet's own images/ files are needed because leaflet.css
# references three of them by relative URL, and L.Icon.Default (Leaflet's
# default marker) needs the other two -- a well-known gotcha for anyone
# serving Leaflet without its own build/CDN setup.
ASSETS: tuple[VendorAsset, ...] = (
    VendorAsset(
        url="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js",
        sha256="db49d009c841f5ca34a888c96511ae936fd9f5533e90d8b2c4d57596f4e5641a",
        relative_path="leaflet.js",
    ),
    VendorAsset(
        url="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css",
        sha256="a7837102824184820dfa198d1ebcd109ff6d0ff9a2672a074b9a1b4d147d04c6",
        relative_path="leaflet.css",
    ),
    VendorAsset(
        url="https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png",
        sha256="574c3a5cca85f4114085b6841596d62f00d7c892c7b03f28cbfa301deb1dc437",
        relative_path="images/marker-icon.png",
    ),
    VendorAsset(
        url="https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon-2x.png",
        sha256="00179c4c1ee830d3a108412ae0d294f55776cfeb085c60129a39aa6fc4ae2528",
        relative_path="images/marker-icon-2x.png",
    ),
    VendorAsset(
        url="https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png",
        sha256="264f5c640339f042dd729062cfc04c17f8ea0f29882b538e3848ed8f10edb4da",
        relative_path="images/marker-shadow.png",
    ),
    VendorAsset(
        url="https://unpkg.com/leaflet@1.9.4/dist/images/layers.png",
        sha256="1dbbe9d028e292f36fcba8f8b3a28d5e8932754fc2215b9ac69e4cdecf5107c6",
        relative_path="images/layers.png",
    ),
    VendorAsset(
        url="https://unpkg.com/leaflet@1.9.4/dist/images/layers-2x.png",
        sha256="066daca850d8ffbef007af00b06eac0015728dee279c51f3cb6c716df7c42edf",
        relative_path="images/layers-2x.png",
    ),
    VendorAsset(
        url="https://unpkg.com/leaflet.markercluster@1.5.3/dist/leaflet.markercluster.js",
        sha256="1e4e1d22972a3926f48598e0caf14e3fe7049835d428a344fed4f9e3665b3508",
        relative_path="leaflet.markercluster.js",
    ),
    VendorAsset(
        url="https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.css",
        sha256="614dea0a98ff3f4ead74f04918f6b1d1b9ba435c25b5fc23b21a394d1e3e4d87",
        relative_path="MarkerCluster.css",
    ),
    VendorAsset(
        url="https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.Default.css",
        sha256="61258232d98d64dc2a7b1e02130d67421bc5b9bda5994eef70228ff97570c170",
        relative_path="MarkerCluster.Default.css",
    ),
    VendorAsset(
        url="https://unpkg.com/htmx.org@2.0.3/dist/htmx.min.js",
        sha256="491955cd1810747d7d7b9ccb936400afb760e06d25d53e4572b64b6563b2784e",
        relative_path="htmx.min.js",
    ),
    VendorAsset(
        url="https://unpkg.com/alpinejs@3.14.8/dist/cdn.min.js",
        sha256="b600e363d99d95444db54acbfb2deffec9ae792aa99a09229bcda078e5b55643",
        relative_path="alpine.min.js",
    ),
)


class IntegrityError(Exception):
    """A downloaded asset's SHA-256 didn't match what was pinned."""


def verify(data: bytes, expected_sha256: str) -> None:
    digest = hashlib.sha256(data).hexdigest()
    if digest != expected_sha256:
        msg = f"expected sha256 {expected_sha256}, got {digest}"
        raise IntegrityError(msg)


def fetch_all(vendor_dir: Path, assets: tuple[VendorAsset, ...] = ASSETS) -> None:
    for asset in assets:
        with urllib.request.urlopen(asset.url) as response:
            data = response.read()
        verify(data, asset.sha256)
        dest = vendor_dir / asset.relative_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)


def main() -> int:
    vendor_dir = resolve_static_root() / "vendor"
    fetch_all(vendor_dir)
    print(f"fetched {len(ASSETS)} assets into {vendor_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `hatch test tests/test_fetch_vendor_assets.py -v`
Expected: PASS (4/4)

- [ ] **Step 5: Actually run the script once, for real, and confirm it works**

Run: `hatch run python scripts/fetch_vendor_assets.py` (needs real network
access -- if your harness sandboxes network, use `dangerouslyDisableSandbox:
true`). Expected: `fetched 12 assets into <path>`, and `ls -R
$(hatch run python -c "from fledermap.config import resolve_static_root;
print(resolve_static_root())")/vendor` shows all 12 files under the expected
relative paths. This is the one point in this task where a real network call
is appropriate and necessary -- confirming the pinned URLs and hashes are
still correct today, not just that the test's fake-response path works.

- [ ] **Step 6: Full verification**

Run: `hatch run types:check`, `hatch run ruff:ruff check .`, `hatch run
ruff:ruff format --check --diff .` — expect clean.
Run: `hatch test` (Docker, unsandboxed) — full suite, expect all passing,
pristine.

- [ ] **Step 7: Commit**

```bash
git add scripts/fetch_vendor_assets.py tests/test_fetch_vendor_assets.py
git commit -m "feat: fetch_vendor_assets -- pinned-version, hash-verified JS/CSS fetch"
```

Do NOT commit anything under the resolved static root itself (it's outside the
repo entirely, under `platformdirs`' cache dir or wherever
`FLEDERMAP_STATIC_ROOT` points — nothing to `.gitignore` here, unlike
`archive/`/`media/`, since it was never inside the repo tree to begin with).

---

## Task 4: `services/map_query.py`

**Files:**
- Create: `src/fledermap/services/map_query.py`
- Test: `tests/test_map_query.py`

**Interfaces:**
- Consumes: `current_best_identification` (Task 2); `Recording`, `Site`,
  `Identification`, `IdSource`, `Verdict` (existing models); `decode_point`
  (existing, `store/geo.py`).
- Produces: `MAX_FEATURES: int`, `filtered_recordings(session, *, bbox,
  date_from, date_to, taxon_id, verdict, session_id, source) ->
  Sequence[Recording]`, `filtered_sites(session, *, bbox, date_from, date_to)
  -> Sequence[Site]`, used by Task 6 (`web/api/geojson.py`).

**Decision made while writing this task, not previously in the design doc
(P4-9, add to the design doc's decisions table when this task is reviewed):**
a recording with `current_best_identification(recording) is None` (no
non-superseded identification at all) is treated as equivalent to
`Verdict.NO_ID` for filtering purposes — both mean "we don't know what this
is," and the parent spec's "hide noise by default" framing is about exactly
that uncertainty, not literally about the three stored `Verdict` values.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_map_query.py
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from geoalchemy2.elements import WKTElement
from sqlalchemy import Engine
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import IdSource, Verdict
from fledermap.services.map_query import filtered_recordings, filtered_sites
from fledermap.store.models import Identification, Recording, Site

pytestmark = pytest.mark.db


def _recording(
    session: OrmSession,
    *,
    audio_hash: str,
    lon: float = 10.0,
    lat: float = 50.0,
    recorded_at: datetime = datetime(2026, 8, 25, tzinfo=UTC),
    verdict: Verdict | None = Verdict.SPECIES,
    taxon_id: int | None = None,
    source: IdSource = IdSource.EMT_GUANO,
    session_id: int | None = None,
    missing: bool = False,
) -> Recording:
    r = Recording(
        audio_hash=audio_hash,
        path=f"{audio_hash}.wav",
        recorded_at=recorded_at,
        geom=WKTElement(f"POINT({lon} {lat})", srid=4326),
        session_id=session_id,
        missing_since=datetime(2026, 8, 25, tzinfo=UTC) if missing else None,
    )
    session.add(r)
    session.flush()
    if verdict is not None:
        session.add(
            Identification(
                recording_id=r.id,
                source=source,
                verdict=verdict,
                taxon_id=taxon_id,
                first_seen_at=recorded_at,
            ),
        )
    session.flush()
    return r


def test_excludes_missing_recordings(engine: Engine) -> None:
    with OrmSession(engine) as session:
        _recording(session, audio_hash="a" * 64, missing=True)
        session.commit()

        results = filtered_recordings(session)

    assert results == []


def test_date_range_filters_recordings(engine: Engine) -> None:
    with OrmSession(engine) as session:
        early = _recording(
            session, audio_hash="a" * 64, recorded_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        _recording(session, audio_hash="b" * 64, recorded_at=datetime(2026, 8, 1, tzinfo=UTC))
        session.commit()

        results = filtered_recordings(
            session,
            date_from=datetime(2026, 1, 1, tzinfo=UTC),
            date_to=datetime(2026, 2, 1, tzinfo=UTC),
        )

    assert [r.id for r in results] == [early.id]


def test_bbox_filters_by_the_recordings_current_position(engine: Engine) -> None:
    with OrmSession(engine) as session:
        inside = _recording(session, audio_hash="a" * 64, lon=10.0, lat=50.0)
        _recording(session, audio_hash="b" * 64, lon=100.0, lat=50.0)
        session.commit()

        results = filtered_recordings(session, bbox=(0.0, 40.0, 20.0, 60.0))

    assert [r.id for r in results] == [inside.id]


def test_default_verdict_excludes_noise_and_no_id(engine: Engine) -> None:
    with OrmSession(engine) as session:
        species = _recording(session, audio_hash="a" * 64, verdict=Verdict.SPECIES)
        _recording(session, audio_hash="b" * 64, verdict=Verdict.NOISE)
        _recording(session, audio_hash="c" * 64, verdict=Verdict.NO_ID)
        _recording(session, audio_hash="d" * 64, verdict=None)  # no identification at all
        session.commit()

        results = filtered_recordings(session)

    assert [r.id for r in results] == [species.id]


def test_verdict_all_includes_everything(engine: Engine) -> None:
    with OrmSession(engine) as session:
        _recording(session, audio_hash="a" * 64, verdict=Verdict.SPECIES)
        _recording(session, audio_hash="b" * 64, verdict=Verdict.NOISE)
        _recording(session, audio_hash="c" * 64, verdict=None)
        session.commit()

        results = filtered_recordings(session, verdict="all")

    assert len(results) == 3


def test_explicit_verdict_filters_to_only_that_verdict(engine: Engine) -> None:
    with OrmSession(engine) as session:
        noise = _recording(session, audio_hash="a" * 64, verdict=Verdict.NOISE)
        _recording(session, audio_hash="b" * 64, verdict=Verdict.SPECIES)
        session.commit()

        results = filtered_recordings(session, verdict=Verdict.NOISE)

    assert [r.id for r in results] == [noise.id]


def test_taxon_filters_by_current_best_taxon(engine: Engine) -> None:
    with OrmSession(engine) as session:
        matching = _recording(session, audio_hash="a" * 64, taxon_id=5)
        _recording(session, audio_hash="b" * 64, taxon_id=6)
        session.commit()

        results = filtered_recordings(session, taxon_id=5)

    assert [r.id for r in results] == [matching.id]


def test_session_id_filters_recordings(engine: Engine) -> None:
    with OrmSession(engine) as session:
        matching = _recording(session, audio_hash="a" * 64, session_id=1)
        _recording(session, audio_hash="b" * 64, session_id=2)
        session.commit()

        results = filtered_recordings(session, session_id=1)

    assert [r.id for r in results] == [matching.id]


def test_source_filters_by_a_non_superseded_identification_from_that_source(
    engine: Engine,
) -> None:
    with OrmSession(engine) as session:
        matching = _recording(session, audio_hash="a" * 64, source=IdSource.EMT_WAMD)
        _recording(session, audio_hash="b" * 64, source=IdSource.EMT_GUANO)
        session.commit()

        results = filtered_recordings(session, source=IdSource.EMT_WAMD)

    assert [r.id for r in results] == [matching.id]


def test_filtered_sites_by_bbox_and_date(engine: Engine) -> None:
    with OrmSession(engine) as session:
        inside = Site(
            centroid=WKTElement("POINT(10 50)", srid=4326),
            radius_m=100.0,
            recording_count=3,
            first_at=datetime(2026, 1, 1, tzinfo=UTC),
            last_at=datetime(2026, 1, 2, tzinfo=UTC),
        )
        outside = Site(
            centroid=WKTElement("POINT(100 50)", srid=4326),
            radius_m=100.0,
            recording_count=1,
            first_at=datetime(2026, 1, 1, tzinfo=UTC),
            last_at=datetime(2026, 1, 2, tzinfo=UTC),
        )
        session.add_all([inside, outside])
        session.commit()

        results = filtered_sites(session, bbox=(0.0, 40.0, 20.0, 60.0))

    assert [s.id for s in results] == [inside.id]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `hatch test tests/test_map_query.py -v` (Docker, unsandboxed)
Expected: FAIL with `ModuleNotFoundError: No module named 'fledermap.services.map_query'`

- [ ] **Step 3: Implement**

```python
# src/fledermap/services/map_query.py
"""Filtered Recording/Site queries shared by both GeoJSON endpoints (design
spec section 8) -- one definition of what the active filters mean, not
duplicated per endpoint.

Filters on fields stored directly on `Recording`/`Site` (date range, session,
missing-file status, `source`) run in SQL. `bbox` and taxon/verdict filtering
run in Python after that SQL prefilter: `bbox` because comparing against a
decoded `(lon, lat)` is simpler than a PostGIS bbox operator at this project's
established scale (`services/ingest.py`'s `sweep_missing` docstring: "fine at
journal scale, tens to low thousands"); taxon/verdict because they must be
evaluated against each recording's CURRENT-BEST identification (design spec
P4-2), not "any non-superseded identification" -- computing that per
candidate is exactly what `current_best_identification` does, and pushing
that logic into SQL would duplicate it.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import IdSource, Verdict
from fledermap.services.current_best import current_best_identification
from fledermap.store.geo import decode_point
from fledermap.store.models import Identification, Recording, Site

# This project's own established "tens to low thousands" scale assumption
# (see module docstring) makes true server-side, zoom-aware clustering
# unnecessary -- Leaflet.markercluster already declutters client-side (design
# spec section 6/P4-7). Over the cap, callers report `truncated: True` rather
# than a partial-and-silent result.
MAX_FEATURES = 2000

BBox = tuple[float, float, float, float]  # (min_lon, min_lat, max_lon, max_lat)


def _within_bbox(point: tuple[float, float] | None, bbox: BBox) -> bool:
    if point is None:
        return False
    lon, lat = point
    min_lon, min_lat, max_lon, max_lat = bbox
    return min_lon <= lon <= max_lon and min_lat <= lat <= max_lat


def _passes_verdict_filter(
    best: Identification | None,
    verdict: Verdict | Literal["all"] | None,
) -> bool:
    """A recording with no non-superseded identification at all (`best is
    None`) is treated as equivalent to `Verdict.NO_ID` for this purpose --
    both mean "we don't know what this is," which is exactly what "hide noise
    by default" is protecting the map from (decision P4-9)."""
    if verdict == "all":
        return True
    effective = best.verdict if best is not None else Verdict.NO_ID
    if verdict is None:
        return effective == Verdict.SPECIES
    return effective == verdict


def filtered_recordings(
    session: OrmSession,
    *,
    bbox: BBox | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    taxon_id: int | None = None,
    verdict: Verdict | Literal["all"] | None = None,
    session_id: int | None = None,
    source: IdSource | None = None,
) -> Sequence[Recording]:
    stmt = select(Recording).where(Recording.missing_since.is_(None))
    if date_from is not None:
        stmt = stmt.where(Recording.recorded_at >= date_from)
    if date_to is not None:
        stmt = stmt.where(Recording.recorded_at <= date_to)
    if session_id is not None:
        stmt = stmt.where(Recording.session_id == session_id)
    if source is not None:
        stmt = stmt.where(
            Recording.identifications.any(
                (Identification.source == source)
                & (Identification.superseded_at.is_(None)),
            ),
        )

    recordings = list(session.scalars(stmt).all())

    if bbox is not None:
        recordings = [
            r for r in recordings if _within_bbox(decode_point(r.geom), bbox)
        ]

    results = []
    for r in recordings:
        best = current_best_identification(r)
        if not _passes_verdict_filter(best, verdict):
            continue
        if taxon_id is not None and (best is None or best.taxon_id != taxon_id):
            continue
        results.append(r)
    return results


def filtered_sites(
    session: OrmSession,
    *,
    bbox: BBox | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> Sequence[Site]:
    stmt = select(Site)
    if date_from is not None:
        stmt = stmt.where(Site.last_at >= date_from)
    if date_to is not None:
        stmt = stmt.where(Site.first_at <= date_to)

    sites = list(session.scalars(stmt).all())

    if bbox is not None:
        sites = [s for s in sites if _within_bbox(decode_point(s.centroid), bbox)]
    return sites
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `hatch test tests/test_map_query.py -v` (Docker, unsandboxed)
Expected: PASS (10/10)

- [ ] **Step 5: Full verification**

Run: `hatch run types:check`, `hatch run ruff:ruff check .`, `hatch run
ruff:ruff format --check --diff .` — expect clean.
Run: `hatch test` (Docker, unsandboxed) — full suite, expect all passing,
pristine.

- [ ] **Step 6: Commit**

```bash
git add src/fledermap/services/map_query.py tests/test_map_query.py
git commit -m "feat: filtered_recordings/filtered_sites -- shared map-filter queries"
```

---

## Task 5: `web/app.py` — Flask app factory and `fledermap serve`

**Files:**
- Create: `src/fledermap/web/__init__.py` (empty)
- Create: `src/fledermap/web/app.py`
- Modify: `src/fledermap/cli/main.py` (add `serve` command)
- Modify: `pyproject.toml` — add `flask` to `dependencies`
- Test: `tests/test_web_app.py`
- Test: `tests/test_cli.py` (extend)

**Interfaces:**
- Consumes: `Config.static_root` (Task 1).
- Produces: `create_app(engine: Engine, static_root: Path) -> flask.Flask`
  (registers a `vendor` Blueprint serving `<static_root>/vendor/` at
  `/static/vendor/...`, plus placeholder registration points for the `api` and
  `views` Blueprints Tasks 6/7 add), and the `fledermap serve` CLI command.
  `app.config["ENGINE"]` holds the engine every request handler reads.

**Note for the implementer:** at the time this task is written, Tasks 6 and 7
(the `api`/`views` Blueprints `create_app` registers) don't exist yet. Write
`create_app` to import and register them now, matching the exact names given
below (`fledermap.web.api.geojson.api_bp`, `fledermap.web.views.map.views_bp`)
— this task's own tests will therefore need those two modules to exist as
empty-but-real Blueprints for `create_app`'s tests to import successfully.
Create minimal stub modules for both if Tasks 6/7 haven't landed yet
(`api_bp = flask.Blueprint("api", __name__, url_prefix="/api")` with no
routes, `views_bp = flask.Blueprint("views", __name__)` with no routes) —
Tasks 6/7 will fill in real routes on the same Blueprint objects. Do not
invent routes on these stubs; that's Tasks 6/7's job.

- [ ] **Step 1: Add `flask` dependency**

Add `"flask"` to `[project] dependencies` in `pyproject.toml`, appended after
`"platformdirs"` (from Task 1).

Run: `hatch run types:check`. Flask ships inline types; this should already be
clean. If not, add `flask` to `[tool.hatch.envs.types]`'s `extra-dependencies`.

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_web_app.py
from __future__ import annotations

from pathlib import Path

from sqlalchemy import Engine

from fledermap.web.app import create_app


def test_create_app_registers_vendor_static_blueprint(
    tmp_path: Path, engine: Engine,
) -> None:
    vendor_dir = tmp_path / "static" / "vendor"
    vendor_dir.mkdir(parents=True)
    (vendor_dir / "leaflet.js").write_text("/* fake */")

    app = create_app(engine, tmp_path / "static")
    client = app.test_client()

    response = client.get("/static/vendor/leaflet.js")

    assert response.status_code == 200
    assert response.data == b"/* fake */"


def test_create_app_stores_the_engine_on_config(tmp_path: Path, engine: Engine) -> None:
    app = create_app(engine, tmp_path / "static")

    assert app.config["ENGINE"] is engine
```

Add to `tests/test_cli.py`:

```python
def test_serve_command_starts_without_error(
    clean_database_url: str,
    tmp_path: Path,
) -> None:
    """Doesn't actually start listening (app.run() blocks) -- confirms the
    command builds a real Flask app and exits cleanly when given --help,
    proving Config/engine/create_app wiring doesn't raise before that point."""
    runner = CliRunner()
    env = {
        "FLEDERMAP_DATABASE_URL": clean_database_url,
        "FLEDERMAP_MEDIA_ROOT": str(tmp_path / "media"),
        "FLEDERMAP_STATIC_ROOT": str(tmp_path / "static"),
    }

    result = runner.invoke(cli, ["serve", "--help"], env=env)

    assert result.exit_code == 0, result.output
    assert "--host" in result.output
    assert "--port" in result.output
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `hatch test tests/test_web_app.py -v` (Docker, unsandboxed)
Expected: FAIL with `ModuleNotFoundError: No module named 'fledermap.web'`

- [ ] **Step 4: Implement**

```python
# src/fledermap/web/app.py
"""Flask app factory (design spec section 3/4). `web/api` and `web/views`
both call `services/`, never `store/` directly -- the SPA-migration escape
hatch the parent spec's section 4 documents depends on that boundary holding.
"""

from __future__ import annotations

from pathlib import Path

import flask
from sqlalchemy import Engine

from fledermap.web.api.geojson import api_bp
from fledermap.web.views.map import views_bp


def create_app(engine: Engine, static_root: Path) -> flask.Flask:
    """`static_root` is `Config.static_root` -- where
    `scripts/fetch_vendor_assets.py` (Task 3) wrote Leaflet/HTMX/Alpine.
    Served from a dedicated `vendor` Blueprint (its own `static_folder`),
    kept separate from the app's own default static folder (which serves
    this package's own committed `app.js`/`app.css` -- Task 7) so the two
    genuinely different kinds of static content (fetched-at-setup-time vs.
    committed-with-the-code) never share one directory or one config knob.
    """
    app = flask.Flask(__name__)
    app.config["ENGINE"] = engine

    vendor_bp = flask.Blueprint(
        "vendor",
        __name__,
        static_folder=str(static_root / "vendor"),
        static_url_path="/static/vendor",
    )
    app.register_blueprint(vendor_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(views_bp)
    return app
```

If `fledermap.web.api.geojson`/`fledermap.web.views.map` don't exist yet
(Tasks 6/7 not started), create the minimal stub Blueprints described above
so this file imports successfully:

```python
# src/fledermap/web/api/__init__.py (empty)
# src/fledermap/web/api/geojson.py (temporary stub, Task 6 replaces this)
import flask

api_bp = flask.Blueprint("api", __name__, url_prefix="/api")
```

```python
# src/fledermap/web/views/__init__.py (empty)
# src/fledermap/web/views/map.py (temporary stub, Task 7 replaces this)
import flask

views_bp = flask.Blueprint("views", __name__)
```

Add to `src/fledermap/cli/main.py`, following `derive`'s existing
`Path.cwd()` pattern (this command doesn't read `archive_root` any more than
`derive` does):

```python
@cli.command()
@click.option("--host", default="127.0.0.1", help="Interface to bind.")
@click.option("--port", default=5000, type=int, help="Port to listen on.")
def serve(host: str, port: int) -> None:
    """Run the web map. Reads FLEDERMAP_DATABASE_URL and (optionally)
    FLEDERMAP_STATIC_ROOT/FLEDERMAP_MEDIA_ROOT."""
    try:
        config = Config.from_env(Path.cwd())
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc

    engine = make_engine(config.database_url)
    _run_migrations(config.database_url)
    app = create_app(engine, config.static_root)
    app.run(host=host, port=port)
```

Add `from fledermap.web.app import create_app` to `cli/main.py`'s import
block.

- [ ] **Step 5: Run tests to verify they pass**

Run: `hatch test tests/test_web_app.py tests/test_cli.py -v` (Docker,
unsandboxed)
Expected: PASS.

- [ ] **Step 6: Full verification**

Run: `hatch run types:check`, `hatch run ruff:ruff check .`, `hatch run
ruff:ruff format --check --diff .` — expect clean.
Run: `hatch test` (Docker, unsandboxed) — full suite, expect all passing,
pristine.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml src/fledermap/web/ src/fledermap/cli/main.py tests/test_web_app.py tests/test_cli.py
git commit -m "feat: Flask app factory, vendor static Blueprint, fledermap serve"
```

---

## Task 6: `web/api/geojson.py` — GeoJSON endpoints

**Files:**
- Modify: `src/fledermap/web/api/geojson.py` (replace Task 5's stub)
- Test: `tests/test_geojson_api.py`

**Interfaces:**
- Consumes: `filtered_recordings`, `filtered_sites`, `MAX_FEATURES` (Task 4);
  `current_best_identification` (Task 2); `decode_point` (existing); `api_bp`
  (Task 5's stub, filled in here).
- Produces: `GET /api/recordings.geojson`, `GET /api/sites.geojson`, both
  returning a GeoJSON `FeatureCollection` with a `truncated: bool` key, used
  by Task 7 (`static/app.js`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_geojson_api.py
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from geoalchemy2.elements import WKTElement
from sqlalchemy import Engine
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import IdSource, Verdict
from fledermap.store.models import Identification, Recording, Site
from fledermap.web.app import create_app

pytestmark = pytest.mark.db


def _app_client(engine: Engine, tmp_path: Path):
    app = create_app(engine, tmp_path / "static")
    return app.test_client()


def test_recordings_geojson_excludes_noise_by_default(
    engine: Engine, tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        shown = Recording(
            audio_hash="a" * 64,
            path="a.wav",
            recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
            geom=WKTElement("POINT(10 50)", srid=4326),
        )
        hidden = Recording(
            audio_hash="b" * 64,
            path="b.wav",
            recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
            geom=WKTElement("POINT(11 51)", srid=4326),
        )
        session.add_all([shown, hidden])
        session.flush()
        session.add(
            Identification(
                recording_id=shown.id,
                source=IdSource.EMT_GUANO,
                verdict=Verdict.SPECIES,
                taxon_id=1,
                first_seen_at=datetime(2026, 8, 25, tzinfo=UTC),
            ),
        )
        session.add(
            Identification(
                recording_id=hidden.id,
                source=IdSource.EMT_GUANO,
                verdict=Verdict.NOISE,
                first_seen_at=datetime(2026, 8, 25, tzinfo=UTC),
            ),
        )
        session.commit()
        shown_hash = shown.audio_hash

    client = _app_client(engine, tmp_path)
    response = client.get("/api/recordings.geojson")

    assert response.status_code == 200
    body = response.get_json()
    assert body["type"] == "FeatureCollection"
    hashes = [f["properties"]["audio_hash"] for f in body["features"]]
    assert hashes == [shown_hash]


def test_recordings_geojson_verdict_all_shows_everything(
    engine: Engine, tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        r = Recording(
            audio_hash="c" * 64,
            path="c.wav",
            recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
            geom=WKTElement("POINT(10 50)", srid=4326),
        )
        session.add(r)
        session.flush()
        session.add(
            Identification(
                recording_id=r.id,
                source=IdSource.EMT_GUANO,
                verdict=Verdict.NOISE,
                first_seen_at=datetime(2026, 8, 25, tzinfo=UTC),
            ),
        )
        session.commit()

    client = _app_client(engine, tmp_path)
    response = client.get("/api/recordings.geojson?verdict=all")

    assert len(response.get_json()["features"]) == 1


def test_recordings_geojson_feature_geometry_is_lon_lat(
    engine: Engine, tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        r = Recording(
            audio_hash="d" * 64,
            path="d.wav",
            recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
            geom=WKTElement("POINT(13.4 52.5)", srid=4326),
        )
        session.add(r)
        session.flush()
        session.add(
            Identification(
                recording_id=r.id,
                source=IdSource.EMT_GUANO,
                verdict=Verdict.SPECIES,
                taxon_id=1,
                first_seen_at=datetime(2026, 8, 25, tzinfo=UTC),
            ),
        )
        session.commit()

    client = _app_client(engine, tmp_path)
    response = client.get("/api/recordings.geojson")

    feature = response.get_json()["features"][0]
    assert feature["geometry"] == {"type": "Point", "coordinates": [13.4, 52.5]}


def test_sites_geojson_falls_back_to_coordinates_when_unnamed(
    engine: Engine, tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        site = Site(
            centroid=WKTElement("POINT(13.4 52.5)", srid=4326),
            radius_m=50.0,
            recording_count=4,
            first_at=datetime(2026, 8, 25, tzinfo=UTC),
            last_at=datetime(2026, 8, 26, tzinfo=UTC),
        )
        session.add(site)
        session.commit()

    client = _app_client(engine, tmp_path)
    response = client.get("/api/sites.geojson")

    feature = response.get_json()["features"][0]
    # P4-1: Site.name is unpopulated until poiidx naming ships -- fall back
    # to a rounded-coordinate label.
    assert feature["properties"]["name"] == "52.5000, 13.4000"


def test_invalid_bbox_returns_400(engine: Engine, tmp_path: Path) -> None:
    client = _app_client(engine, tmp_path)

    response = client.get("/api/recordings.geojson?bbox=not,four,numbers")

    assert response.status_code == 400
    assert "bbox" in response.get_json()["error"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `hatch test tests/test_geojson_api.py -v` (Docker, unsandboxed)
Expected: FAIL — `test_recordings_geojson_excludes_noise_by_default` etc. get
a 404 (Task 5's stub `api_bp` has no routes yet).

- [ ] **Step 3: Implement**

```python
# src/fledermap/web/api/geojson.py
"""GeoJSON endpoints for the map (design spec section 6)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

import flask
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import IdSource, Verdict
from fledermap.services.current_best import current_best_identification
from fledermap.services.map_query import (
    MAX_FEATURES,
    BBox,
    filtered_recordings,
    filtered_sites,
)
from fledermap.store.geo import decode_point
from fledermap.store.models import Recording, Site

api_bp = flask.Blueprint("api", __name__, url_prefix="/api")


def _parse_bbox(raw: str | None) -> BBox | None:
    if raw is None:
        return None
    parts = raw.split(",")
    if len(parts) != 4:
        msg = "bbox must be 4 comma-separated numbers: min_lon,min_lat,max_lon,max_lat"
        raise ValueError(msg)
    min_lon, min_lat, max_lon, max_lat = (float(p) for p in parts)
    return (min_lon, min_lat, max_lon, max_lat)


def _parse_datetime(raw: str | None) -> datetime | None:
    return datetime.fromisoformat(raw) if raw else None


def _parse_verdict(raw: str | None) -> Verdict | Literal["all"] | None:
    if raw is None:
        return None
    if raw == "all":
        return "all"
    return Verdict(raw)


def _parse_int(raw: str | None) -> int | None:
    return int(raw) if raw else None


def _fallback_site_label(point: tuple[float, float] | None) -> str:
    """P4-1: Site.name is unpopulated until poiidx naming ships as its own
    task -- fall back to a rounded-coordinate label rather than block this
    phase on that unrelated integration."""
    if point is None:
        return "Site"
    lon, lat = point
    return f"{lat:.4f}, {lon:.4f}"


def _recording_feature(recording: Recording) -> dict[str, object]:
    point = decode_point(recording.geom)
    best = current_best_identification(recording)
    return {
        "type": "Feature",
        "geometry": (
            {"type": "Point", "coordinates": [point[0], point[1]]}
            if point is not None
            else None
        ),
        "properties": {
            "audio_hash": recording.audio_hash,
            "recorded_at": recording.recorded_at.isoformat(),
            "taxon_id": best.taxon_id if best is not None else None,
            "verdict": best.verdict.value if best is not None else None,
            "source": best.source.value if best is not None else None,
        },
    }


def _site_feature(site: Site) -> dict[str, object]:
    point = decode_point(site.centroid)
    return {
        "type": "Feature",
        "geometry": (
            {"type": "Point", "coordinates": [point[0], point[1]]}
            if point is not None
            else None
        ),
        "properties": {
            "id": site.id,
            "name": site.name if site.name else _fallback_site_label(point),
            "radius_m": site.radius_m,
            "recording_count": site.recording_count,
        },
    }


@api_bp.get("/recordings.geojson")
def recordings_geojson() -> flask.Response:
    try:
        bbox = _parse_bbox(flask.request.args.get("bbox"))
        source_raw = flask.request.args.get("source")
        source = IdSource(source_raw) if source_raw else None
    except ValueError as exc:
        return flask.jsonify({"error": str(exc)}), 400

    engine = flask.current_app.config["ENGINE"]
    with OrmSession(engine) as session:
        recordings = filtered_recordings(
            session,
            bbox=bbox,
            date_from=_parse_datetime(flask.request.args.get("from")),
            date_to=_parse_datetime(flask.request.args.get("to")),
            taxon_id=_parse_int(flask.request.args.get("taxon")),
            verdict=_parse_verdict(flask.request.args.get("verdict")),
            session_id=_parse_int(flask.request.args.get("session")),
            source=source,
        )
        truncated = len(recordings) > MAX_FEATURES
        features = [_recording_feature(r) for r in recordings[:MAX_FEATURES]]

    return flask.jsonify(
        {"type": "FeatureCollection", "features": features, "truncated": truncated},
    )


@api_bp.get("/sites.geojson")
def sites_geojson() -> flask.Response:
    try:
        bbox = _parse_bbox(flask.request.args.get("bbox"))
    except ValueError as exc:
        return flask.jsonify({"error": str(exc)}), 400

    engine = flask.current_app.config["ENGINE"]
    with OrmSession(engine) as session:
        sites = filtered_sites(
            session,
            bbox=bbox,
            date_from=_parse_datetime(flask.request.args.get("from")),
            date_to=_parse_datetime(flask.request.args.get("to")),
        )
        truncated = len(sites) > MAX_FEATURES
        features = [_site_feature(s) for s in sites[:MAX_FEATURES]]

    return flask.jsonify(
        {"type": "FeatureCollection", "features": features, "truncated": truncated},
    )
```

`BBox` needs exporting from `services/map_query.py` — confirm it's accessible
as `from fledermap.services.map_query import BBox` (Task 4 defines it as a
module-level type alias; if it was written as a "private" `_BBox` there
instead, rename it to `BBox` in Task 4's file to make it importable here,
since a leading underscore would signal it's not meant for external use).

- [ ] **Step 4: Run tests to verify they pass**

Run: `hatch test tests/test_geojson_api.py -v` (Docker, unsandboxed)
Expected: PASS (all).

- [ ] **Step 5: Full verification**

Run: `hatch run types:check`, `hatch run ruff:ruff check .`, `hatch run
ruff:ruff format --check --diff .` — expect clean.
Run: `hatch test` (Docker, unsandboxed) — full suite, expect all passing,
pristine.

- [ ] **Step 6: Commit**

```bash
git add src/fledermap/web/api/geojson.py tests/test_geojson_api.py
git commit -m "feat: /api/recordings.geojson and /api/sites.geojson"
```

---

## Task 7: `web/views/map.py` — the map page

**Files:**
- Modify: `src/fledermap/web/views/map.py` (replace Task 5's stub)
- Create: `src/fledermap/web/templates/map.html`
- Create: `src/fledermap/web/static/app.js`
- Create: `src/fledermap/web/static/app.css`
- Test: `tests/test_map_view.py`

**Interfaces:**
- Consumes: `views_bp` (Task 5's stub, filled in here).
- Produces: `GET /` — renders the map shell. Nothing later depends on this
  task; it's the phase's user-visible deliverable.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_map_view.py
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import Engine

from fledermap.web.app import create_app

pytestmark = pytest.mark.db


def test_map_page_renders_the_leaflet_shell(engine: Engine, tmp_path: Path) -> None:
    app = create_app(engine, tmp_path / "static")
    client = app.test_client()

    response = client.get("/")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert '<div id="map">' in html
    assert 'vendor/leaflet.js' in html
    assert 'vendor/leaflet.markercluster.js' in html
    assert 'vendor/htmx.min.js' in html
    assert 'vendor/alpine.min.js' in html


def test_map_page_includes_the_filter_form(engine: Engine, tmp_path: Path) -> None:
    app = create_app(engine, tmp_path / "static")
    client = app.test_client()

    response = client.get("/")

    html = response.get_data(as_text=True)
    assert 'name="verdict"' in html
    assert 'name="taxon"' in html
    assert 'name="from"' in html
    assert 'name="to"' in html
    assert 'name="session"' in html
    assert 'name="source"' in html
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `hatch test tests/test_map_view.py -v` (Docker, unsandboxed)
Expected: FAIL with a 404 (Task 5's stub `views_bp` has no routes yet).

- [ ] **Step 3: Implement**

```python
# src/fledermap/web/views/map.py
"""The map page (design spec section 3/9)."""

from __future__ import annotations

import flask

views_bp = flask.Blueprint("views", __name__, template_folder="../templates")


@views_bp.get("/")
def map_page() -> str:
    return flask.render_template("map.html")
```

```html
<!-- src/fledermap/web/templates/map.html -->
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Fledermap</title>
  <link rel="stylesheet" href="{{ url_for('vendor.static', filename='leaflet.css') }}">
  <link rel="stylesheet" href="{{ url_for('vendor.static', filename='MarkerCluster.css') }}">
  <link rel="stylesheet" href="{{ url_for('vendor.static', filename='MarkerCluster.Default.css') }}">
  <link rel="stylesheet" href="{{ url_for('static', filename='app.css') }}">
</head>
<body x-data="filterForm()">
  <form id="filters">
    <label>From <input type="date" name="from" x-model="from"></label>
    <label>To <input type="date" name="to" x-model="to"></label>
    <label>Taxon <input type="text" name="taxon" x-model="taxon"></label>
    <label>Session <input type="text" name="session" x-model="session"></label>
    <label>Source
      <select name="source" x-model="source">
        <option value="">Any</option>
        <option value="emt.guano">EMT GUANO</option>
        <option value="emt.wamd">EMT WAMD</option>
        <option value="emt.filename">EMT filename</option>
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
  </form>

  <div id="map"></div>

  <script src="{{ url_for('vendor.static', filename='leaflet.js') }}"></script>
  <script src="{{ url_for('vendor.static', filename='leaflet.markercluster.js') }}"></script>
  <script src="{{ url_for('vendor.static', filename='htmx.min.js') }}"></script>
  <script src="{{ url_for('vendor.static', filename='alpine.min.js') }}" defer></script>
  <script src="{{ url_for('static', filename='app.js') }}"></script>
</body>
</html>
```

```javascript
// src/fledermap/web/static/app.js
// The map is constructed once and never swapped or destroyed (design spec
// section 7, targeting parent spec section 10's tripwire #1 directly).
// Filters update its existing layers in place via fetch() -- there is no
// hx-swap anywhere near #map.

function filterForm() {
  return {
    from: "", to: "", taxon: "", session: "", source: "", verdict: "",
  };
}

document.addEventListener("DOMContentLoaded", () => {
  const map = L.map("map").setView([51.0, 10.0], 6);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "© OpenStreetMap contributors",
  }).addTo(map);

  const recordingsLayer = L.markerClusterGroup().addTo(map);
  const sitesLayer = L.layerGroup().addTo(map);

  function query() {
    const form = document.getElementById("filters");
    const params = new URLSearchParams(new FormData(form));
    for (const [key, value] of [...params.entries()]) {
      if (!value) params.delete(key);
    }
    return params;
  }

  function colorForVerdict(props) {
    if (props.verdict === "noise") return "gray";
    if (props.verdict === "no_id") return "orange";
    return "green";
  }

  async function refresh() {
    const params = query();

    const recordingsResponse = await fetch(`/api/recordings.geojson?${params}`);
    const recordingsData = await recordingsResponse.json();
    recordingsLayer.clearLayers();
    L.geoJSON(recordingsData, {
      pointToLayer: (feature, latlng) =>
        L.circleMarker(latlng, { color: colorForVerdict(feature.properties) })
          .bindPopup(
            `${feature.properties.verdict ?? "unknown"} ` +
            `(${feature.properties.source ?? "no source"})<br>` +
            feature.properties.recorded_at,
          ),
    }).eachLayer((layer) => recordingsLayer.addLayer(layer));

    const sitesResponse = await fetch(`/api/sites.geojson?${params}`);
    const sitesData = await sitesResponse.json();
    sitesLayer.clearLayers();
    L.geoJSON(sitesData, {
      pointToLayer: (feature, latlng) =>
        L.circle(latlng, { radius: feature.properties.radius_m, color: "blue" })
          .bindPopup(
            `${feature.properties.name}<br>` +
            `${feature.properties.recording_count} recordings`,
          ),
    }).eachLayer((layer) => sitesLayer.addLayer(layer));
  }

  document.getElementById("filters").addEventListener("input", refresh);
  refresh();
});
```

```css
/* src/fledermap/web/static/app.css */
html, body { height: 100%; margin: 0; }
#map { height: calc(100vh - 4rem); width: 100%; }
#filters { display: flex; gap: 1rem; padding: 0.5rem 1rem; flex-wrap: wrap; }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `hatch test tests/test_map_view.py -v` (Docker, unsandboxed)
Expected: PASS (2/2)

- [ ] **Step 5: Full verification**

Run: `hatch run types:check`, `hatch run ruff:ruff check .`, `hatch run
ruff:ruff format --check --diff .` — expect clean (ruff does not lint `.js`
files -- confirm it isn't configured to try, e.g. via `[tool.ruff]`'s
`extend-include`, since these are not Python).
Run: `hatch test` (Docker, unsandboxed) — full suite, expect all passing,
pristine. This is Phase 4's exit gate.

- [ ] **Step 6: Manual smoke test (not automatable — record the result in your report)**

Run `hatch run python scripts/fetch_vendor_assets.py` (Task 3), then
`fledermap serve` against a real database with some ingested recordings, and
open `http://127.0.0.1:5000/` in a browser. Confirm: the map renders with a
basemap tile layer, recordings show as clustered markers, sites show as
circles, changing a filter updates the map without a page reload or any
visible flicker/reset of pan/zoom position. This is the one point in this
plan where genuine manual verification matters more than an automated
assertion — Leaflet's actual rendering isn't exercised by the Flask test
client.

- [ ] **Step 7: Commit**

```bash
git add src/fledermap/web/views/map.py src/fledermap/web/templates/map.html src/fledermap/web/static/app.js src/fledermap/web/static/app.css tests/test_map_view.py
git commit -m "feat: the map page -- Leaflet shell, filter form, in-place layer updates"
```

---

## Self-Review Notes

**Spec coverage:** design doc §1 (scope: map + filters, no drawer) — enforced
by every task's interface list touching only map-rendering concerns. §2/P4-1
(site naming fallback) — Task 6's `_fallback_site_label`, tested explicitly.
§2/P4-2 (current-best precedence) — Task 2, fully. §3 (module layout) —
matches Tasks 1-7 exactly. §4 (Flask) — Task 5. §5 (static assets, fetch
script, `FLEDERMAP_STATIC_ROOT`) — Tasks 1 and 3. §6 (GeoJSON API, verdict
default, feature cap) — Tasks 4 and 6. §7 (filter interaction, tripwire #1) —
Task 7's `app.js`, no `hx-swap` anywhere near `#map`. §8 (services code) —
Tasks 2 and 4. §9 (testing approach) — every task's own test section, plus
Task 3's fixture-based hash test and Task 7's explicit manual-smoke-test step
for what automation genuinely can't cover. §10 (out of scope) — no task
touches a drawer, `/sessions`, `/recordings/{hash}`, `/taxa`, job status, or
the `geo` queue.

**Placeholder scan:** an initial draft of Task 6's test file left
`test_invalid_bbox_returns_400` as an unfinished stub; caught during this
self-review and replaced with a complete, real test before this plan was
finalized — not left as an instruction for the implementer to fill in later.
Every code block in this plan is now complete, runnable code, including exact
URLs and real SHA-256 hashes fetched and computed before this plan was
written (Task 3) rather than invented.

**Type consistency check performed:** `resolve_static_root() -> Path` (Task
1) called unchanged by Task 3's `main()`. `Config.static_root: Path` (Task 1)
consumed unchanged by Task 5's `serve` command. `current_best_identification`
(Task 2) called unchanged by Tasks 4 and 6. `filtered_recordings`/
`filtered_sites`/`MAX_FEATURES`/`BBox` (Task 4) called unchanged by Task 6 —
verified `BBox` is exported without a leading underscore so Task 6's import
succeeds. `create_app(engine, static_root)` (Task 5) called unchanged by
Tasks 6 and 7's tests. `api_bp`/`views_bp` (Task 5's stubs) are the same
Blueprint objects Tasks 6/7 add routes to, not new ones.

**Known judgment calls, surfaced inline, not hidden:** P4-9 (a recording with
no non-superseded identification at all is treated as `NO_ID` for filtering)
was decided while writing Task 4, not previously in the design doc — flagged
there with a note to add it to the design doc's decisions table during
review, rather than silently deciding and moving on. The tie-break rule for
two non-superseded claims from the same source (Task 2, break on most recent
`first_seen_at`) is a real but narrow edge case the design doc didn't
address; resolved explicitly rather than left to an accidental dict/list
iteration order. Task 5's note to the implementer about creating temporary
stub Blueprints for Tasks 6/7 to fill in is a deliberate sequencing choice
(each task stays independently testable and reviewable), not a placeholder —
the stubs contain zero invented behavior, only real, empty `Blueprint`
objects with names Tasks 6/7 are told to reuse exactly.
