# Fledermap Statistics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the `/statistics`, `/statistics/sites/<id>`, `/statistics/species/<id>` dashboard pages — a live-SQL-aggregation query layer, Chart.js-rendered donut/bar/line charts plus ranked lists and stat tiles, laid out as bands-of-cards rather than a plain scroll of sections.

**Architecture:** One new query module (`services/statistics.py`) computing plain dataclasses from live DB reads (no cache), a new Flask blueprint (`views/statistics.py`) with three routes rendering three templates, a vendored Chart.js loaded as a plain `<script>` tag (this project's existing no-build-step convention), and a small DOM-free JS module reshaping query results into Chart.js dataset shape.

**Tech Stack:** Python/SQLAlchemy (query layer), Flask/Jinja2 (routes/templates), Chart.js 4.4.6 vendored via `services/vendor_assets.py`, vanilla JS (`node:test` for pure logic, headless-Chrome live-verification for chart mounting — mandatory per `CLAUDE.md`'s JavaScript tooling section).

**Spec:** `docs/superpowers/specs/2026-09-05-fledermap-statistics-design.md`

## Global Constraints

- **No cache, no precomputation** — every query function does live SQL/Python aggregation on each request (spec "Data layer").
- **No date-range filtering, no per-session scope, no gauge widgets, no exports** — spec "Non-goals".
- **Species-breakdown inclusion rules are per-function, not uniform** — re-read the spec's "Species-breakdown inclusion rules" section before touching any query function; `recording_counts_by_taxon` and `rarest_species` deliberately handle multi-species/unmapped differently.
- **Color vocabulary**: every taxon's chart color is `colorForTaxon(taxonId)` from `marker_colors.js` (already vendored via `<script>` include, not Chart.js's own palette). `"Other"` gets a fixed neutral gray (pick one not already reserved — see `marker_colors.js`'s reserved-colors comment before choosing).
- **Page layout**: bands (`.stats-band`, tinted, no border) for macro-structure; cards (`.stats-panel`, bordered) only where a band holds 2+ widgets; see spec's "Page layout: bands + selective cards" section for the exact band/card assignment per page — do not invent a different grouping.
- **Every chart needs a hover/tooltip** (Chart.js's built-in tooltips satisfy this) and **every chart section needs its info-affordance caption** (`<details><summary>ⓘ</summary>...</details>`, no custom JS) per spec's "Info affordance".
- **JS DOM-split convention**: pure reshaping logic goes in a file with no top-level `document`/`window` access (`statistics_charts.js`), loaded before the file that mounts charts (`statistics.js`) — see `CLAUDE.md`'s "A file with a top-level `document.addEventListener(...)`..." bullet.
- **TDD throughout** — RED then GREEN then REFACTOR for every task below; run the stated test command and confirm the failure reason before implementing.

---

## Task 1: Vendor Chart.js

**Files:**
- Modify: `src/fledermap/services/vendor_assets.py`
- Test: `tests/test_vendor_assets.py`

**Interfaces:**
- Produces: a new `VendorAsset` entry in the `ASSETS` tuple with `relative_path="chart.js"`, fetchable at `<static_root>/vendor/chart.js` and loadable via `url_for('vendor.static', filename='chart.js')`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_vendor_assets.py` (follow the existing test style in that file — check an existing test asserting on `ASSETS` for the exact assertion pattern used, e.g. checking a specific `relative_path` appears):

```python
def test_assets_includes_chart_js() -> None:
    relative_paths = {asset.relative_path for asset in ASSETS}
    assert "chart.js" in relative_paths
```

- [ ] **Step 2: Run test to verify it fails**

Run: `hatch test tests/test_vendor_assets.py -k test_assets_includes_chart_js -v`
Expected: FAIL — `chart.js` not in `relative_paths`.

- [ ] **Step 3: Add the vendor asset**

The real pinned sha256 for Chart.js 4.4.6's UMD build (fetched and hashed directly against unpkg.com when this plan was written — same convention as every existing `ASSETS` entry, do not re-fetch or substitute a different hash):

```python
VendorAsset(
    url="https://unpkg.com/chart.js@4.4.6/dist/chart.umd.js",
    sha256="3850656abbdc319141e6e8ce8eacde2622fc767c30e20d81704af2bf3159f92d",
    relative_path="chart.js",
),
```

Add this as a new entry in the `ASSETS` tuple in `src/fledermap/services/vendor_assets.py`, alongside the existing Leaflet/htmx/Alpine entries, with a one-line comment noting it's for the statistics feature.

- [ ] **Step 4: Run test to verify it passes**

Run: `hatch test tests/test_vendor_assets.py -k test_assets_includes_chart_js -v`
Expected: PASS

- [ ] **Step 5: Run the full vendor_assets test file to confirm no regression**

Run: `hatch test tests/test_vendor_assets.py -v`
Expected: all PASS (existing tests fetch/verify assets against real hashes — this needs real network access, matching how those tests already run).

- [ ] **Step 6: Commit**

```bash
git add src/fledermap/services/vendor_assets.py tests/test_vendor_assets.py
git commit -m "feat: vendor Chart.js for the statistics feature"
```

---

## Task 2: `services/statistics.py` scaffolding + `totals()`

**Files:**
- Create: `src/fledermap/services/statistics.py`
- Test: Create `tests/test_statistics_query.py`

**Interfaces:**
- Produces: `_scoped_recordings(session, *, site_id=None) -> list[Recording]` (internal helper, non-missing recordings, optionally scoped to a site — every later query function in this plan reuses this). `Totals` dataclass. `totals(session, *, site_id=None, taxon_id=None) -> Totals`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_statistics_query.py`, mirroring `tests/test_entities_query.py`'s fixture style (a local `_recording` helper, `pytestmark = pytest.mark.db`):

```python
# tests/test_statistics_query.py
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from geoalchemy2.elements import WKTElement
from sqlalchemy import Engine
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import IdSource, Verdict
from fledermap.services.statistics import totals
from fledermap.store.models import Identification, Recording, Site, Taxon

pytestmark = pytest.mark.db


def _recording(
    session: OrmSession,
    *,
    audio_hash: str,
    taxon_id: int | None = None,
    verdict: Verdict | None = Verdict.SPECIES,
    recorded_at: datetime = datetime(2026, 8, 25, tzinfo=UTC),
    site_id: int | None = None,
    missing: bool = False,
) -> Recording:
    r = Recording(
        audio_hash=audio_hash,
        path=f"{audio_hash}.wav",
        recorded_at=recorded_at,
        site_id=site_id,
        missing_since=datetime(2026, 8, 25, tzinfo=UTC) if missing else None,
    )
    session.add(r)
    session.flush()
    if verdict is not None:
        session.add(
            Identification(
                recording_id=r.id,
                source=IdSource.EMT_GUANO,
                verdict=verdict,
                taxon_id=taxon_id,
                first_seen_at=recorded_at,
            ),
        )
    session.flush()
    return r


def test_totals_global_counts_all_recordings_species_and_sites(engine: Engine) -> None:
    with OrmSession(engine) as session:
        taxon = Taxon(rank="species", scientific_name="Eptesicus serotinus")
        site = Site(
            centroid=WKTElement("POINT(10 50)", srid=4326),
            radius_m=50.0,
            recording_count=1,
            first_at=datetime(2026, 8, 25, tzinfo=UTC),
            last_at=datetime(2026, 8, 25, tzinfo=UTC),
        )
        session.add_all([taxon, site])
        session.flush()
        _recording(session, audio_hash="a" * 64, taxon_id=taxon.id, site_id=site.id)
        _recording(session, audio_hash="b" * 64, verdict=Verdict.NOISE)
        session.commit()

    with OrmSession(engine) as session:
        result = totals(session)

    # total_recordings counts EVERY non-missing recording, species or not --
    # the donut deliberately doesn't sum to this (spec's inclusion rules).
    assert result.total_recordings == 2
    assert result.total_species == 1
    assert result.total_sites == 1


def test_totals_excludes_missing_recordings(engine: Engine) -> None:
    with OrmSession(engine) as session:
        _recording(session, audio_hash="a" * 64, missing=True)
        session.commit()

    with OrmSession(engine) as session:
        result = totals(session)

    assert result.total_recordings == 0


def test_totals_site_scoped_counts_only_that_site(engine: Engine) -> None:
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
        _recording(session, audio_hash="a" * 64, site_id=site.id)
        _recording(session, audio_hash="b" * 64, site_id=None)
        session.commit()
        site_id = site.id

    with OrmSession(engine) as session:
        result = totals(session, site_id=site_id)

    assert result.total_recordings == 1
    assert result.total_species is None
    assert result.total_sites is None


def test_totals_species_scoped_counts_by_membership(engine: Engine) -> None:
    """A multi-species recording containing the filtered taxon counts, even
    though it's not that recording's sole result (spec: same membership rule
    as recording_counts_by_site)."""
    with OrmSession(engine) as session:
        taxon_a = Taxon(rank="species", scientific_name="Eptesicus serotinus")
        taxon_b = Taxon(rank="species", scientific_name="Pipistrellus pipistrellus")
        site = Site(
            centroid=WKTElement("POINT(10 50)", srid=4326),
            radius_m=50.0,
            recording_count=1,
            first_at=datetime(2026, 8, 25, tzinfo=UTC),
            last_at=datetime(2026, 8, 25, tzinfo=UTC),
        )
        session.add_all([taxon_a, taxon_b, site])
        session.flush()
        r = Recording(
            audio_hash="a" * 64,
            path="a.wav",
            recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
            site_id=site.id,
        )
        session.add(r)
        session.flush()
        session.add_all(
            [
                Identification(
                    recording_id=r.id,
                    source=IdSource.EMT_MANUAL,
                    verdict=Verdict.SPECIES,
                    taxon_id=taxon_a.id,
                    first_seen_at=r.recorded_at,
                ),
                Identification(
                    recording_id=r.id,
                    source=IdSource.EMT_MANUAL,
                    verdict=Verdict.SPECIES,
                    taxon_id=taxon_b.id,
                    first_seen_at=r.recorded_at,
                ),
            ],
        )
        session.commit()
        taxon_a_id = taxon_a.id

    with OrmSession(engine) as session:
        result = totals(session, taxon_id=taxon_a_id)

    assert result.total_recordings == 1
    assert result.total_sites == 1
    assert result.total_species is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `hatch test tests/test_statistics_query.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'fledermap.services.statistics'`.

- [ ] **Step 3: Write the minimal implementation**

Create `src/fledermap/services/statistics.py`:

```python
# src/fledermap/services/statistics.py
"""Live-SQL-aggregation query layer for the /statistics dashboard pages
(docs/superpowers/specs/2026-09-05-fledermap-statistics-design.md). No cache,
no precomputation -- every function here re-reads the database on every call,
a deliberate choice for this project's self-hosted single-user/small-group
scale (see the spec's "Data layer" section). Sibling to map_query.py and
entities.py, kept separate: this module answers "what does the whole archive
look like," not "what does the map's active filter set mean" or "browse
everything.\""""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session as OrmSession

from fledermap.services.current_best import current_best_identification
from fledermap.store.models import Recording, Site

DEFAULT_TOP_N = 8


def _scoped_recordings(
    session: OrmSession,
    *,
    site_id: int | None = None,
) -> list[Recording]:
    """Every non-missing recording, optionally scoped to one site. Shared by
    every query function below -- same "recompute current-best in Python per
    recording" style entities.py's site_detail/species_detail already use,
    not a SQL-side aggregate (current_best_identification's precedence logic
    has no SQL equivalent)."""
    stmt = select(Recording).where(Recording.missing_since.is_(None))
    if site_id is not None:
        stmt = stmt.where(Recording.site_id == site_id)
    return list(session.scalars(stmt).all())


@dataclass(frozen=True)
class Totals:
    """Global fills every field; site-scoped fills only total_recordings;
    species-scoped fills total_recordings + total_sites. See `totals`'s
    docstring for why each scope leaves the others unset."""

    total_recordings: int
    total_species: int | None = None
    total_sites: int | None = None


def totals(
    session: OrmSession,
    *,
    site_id: int | None = None,
    taxon_id: int | None = None,
) -> Totals:
    """Stat-tile numbers. `total_recordings` always counts EVERY non-missing
    recording in scope, species or not -- the donut chart deliberately does
    NOT sum to this (spec's "Species-breakdown inclusion rules": noise/no_id/
    unidentified recordings have no species-breakdown slice, but they still
    count here). `taxon_id` scope uses the same membership rule as
    `recording_counts_by_site` (a multi-species recording counts if the
    filtered taxon is ANY of its current-best taxa, not the sole one)."""
    if taxon_id is not None:
        recordings = _scoped_recordings(session)
        matching = []
        site_ids: set[int] = set()
        for r in recordings:
            best = current_best_identification(r)
            if best is None or taxon_id not in best.taxon_ids:
                continue
            matching.append(r)
            if r.site_id is not None:
                site_ids.add(r.site_id)
        return Totals(total_recordings=len(matching), total_sites=len(site_ids))

    if site_id is not None:
        return Totals(total_recordings=len(_scoped_recordings(session, site_id=site_id)))

    recordings = _scoped_recordings(session)
    species_ids: set[int] = set()
    for r in recordings:
        best = current_best_identification(r)
        if best is not None:
            species_ids |= best.taxon_ids
    total_sites = session.scalar(select(func.count()).select_from(Site)) or 0
    return Totals(
        total_recordings=len(recordings),
        total_species=len(species_ids),
        total_sites=total_sites,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `hatch test tests/test_statistics_query.py -v`
Expected: all 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/fledermap/services/statistics.py tests/test_statistics_query.py
git commit -m "feat: statistics query layer -- totals()"
```

---

## Task 3: `recording_counts_by_taxon` (donut data)

**Files:**
- Modify: `src/fledermap/services/statistics.py`
- Test: Modify `tests/test_statistics_query.py`

**Interfaces:**
- Consumes: `_scoped_recordings` (Task 2).
- Produces: `TaxonCount(taxon: Taxon, count: int)`, `TaxonBreakdown(entries: list[TaxonCount], other_count: int, unmapped_count: int, multi_species_count: int)`, `recording_counts_by_taxon(session, *, site_id=None, top_n=DEFAULT_TOP_N) -> TaxonBreakdown`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_statistics_query.py`:

```python
from fledermap.services.statistics import recording_counts_by_taxon


def test_recording_counts_by_taxon_excludes_noise_no_id_and_unidentified(
    engine: Engine,
) -> None:
    with OrmSession(engine) as session:
        taxon = Taxon(rank="species", scientific_name="Eptesicus serotinus")
        session.add(taxon)
        session.flush()
        _recording(session, audio_hash="a" * 64, taxon_id=taxon.id)
        _recording(session, audio_hash="b" * 64, verdict=Verdict.NOISE)
        _recording(session, audio_hash="c" * 64, verdict=Verdict.NO_ID)
        _recording(session, audio_hash="d" * 64, verdict=None)
        session.commit()

    with OrmSession(engine) as session:
        result = recording_counts_by_taxon(session)

    assert [(e.taxon.scientific_name, e.count) for e in result.entries] == [
        ("Eptesicus serotinus", 1),
    ]
    assert result.other_count == 0
    assert result.unmapped_count == 0
    assert result.multi_species_count == 0


def test_recording_counts_by_taxon_unmapped_species_gets_own_bucket(
    engine: Engine,
) -> None:
    with OrmSession(engine) as session:
        _recording(session, audio_hash="a" * 64, taxon_id=None, verdict=Verdict.SPECIES)
        session.commit()

    with OrmSession(engine) as session:
        result = recording_counts_by_taxon(session)

    assert result.entries == []
    assert result.unmapped_count == 1


def test_recording_counts_by_taxon_multi_species_gets_own_bucket_not_per_taxon(
    engine: Engine,
) -> None:
    with OrmSession(engine) as session:
        taxon_a = Taxon(rank="species", scientific_name="Eptesicus serotinus")
        taxon_b = Taxon(rank="species", scientific_name="Pipistrellus pipistrellus")
        session.add_all([taxon_a, taxon_b])
        session.flush()
        r = Recording(
            audio_hash="a" * 64,
            path="a.wav",
            recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
        )
        session.add(r)
        session.flush()
        session.add_all(
            [
                Identification(
                    recording_id=r.id,
                    source=IdSource.EMT_MANUAL,
                    verdict=Verdict.SPECIES,
                    taxon_id=taxon_a.id,
                    first_seen_at=r.recorded_at,
                ),
                Identification(
                    recording_id=r.id,
                    source=IdSource.EMT_MANUAL,
                    verdict=Verdict.SPECIES,
                    taxon_id=taxon_b.id,
                    first_seen_at=r.recorded_at,
                ),
            ],
        )
        session.commit()

    with OrmSession(engine) as session:
        result = recording_counts_by_taxon(session)

    # ONE recording, ONE "Multiple Species" bucket -- NOT one count per taxon
    # (that's rarest_species's job, not this function's -- see Task 6).
    assert result.entries == []
    assert result.multi_species_count == 1


def test_recording_counts_by_taxon_folds_past_top_n_into_other(
    engine: Engine,
) -> None:
    with OrmSession(engine) as session:
        taxa = [
            Taxon(rank="species", scientific_name=f"Species {i}") for i in range(3)
        ]
        session.add_all(taxa)
        session.flush()
        # Species 0: 3 recordings, Species 1: 2, Species 2: 1 -- top_n=2 keeps
        # Species 0 and 1, folds Species 2's single recording into Other.
        for i, count in enumerate([3, 2, 1]):
            for n in range(count):
                _recording(
                    session,
                    audio_hash=f"{i}{n}".rjust(64, "0"),
                    taxon_id=taxa[i].id,
                )
        session.commit()

    with OrmSession(engine) as session:
        result = recording_counts_by_taxon(session, top_n=2)

    assert [(e.taxon.scientific_name, e.count) for e in result.entries] == [
        ("Species 0", 3),
        ("Species 1", 2),
    ]
    assert result.other_count == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `hatch test tests/test_statistics_query.py -k recording_counts_by_taxon -v`
Expected: FAIL — `ImportError: cannot import name 'recording_counts_by_taxon'`.

- [ ] **Step 3: Write the minimal implementation**

Add to `src/fledermap/services/statistics.py` (needs `from fledermap.domain.codes import Verdict` and `from fledermap.store.models import Taxon` added to the existing imports):

```python
@dataclass(frozen=True)
class TaxonCount:
    taxon: Taxon
    count: int


@dataclass(frozen=True)
class TaxonBreakdown:
    """Species-composition donut data. `other_count`/`unmapped_count`/
    `multi_species_count` are each their own donut slice -- see the spec's
    "Species-breakdown inclusion rules" for why noise/no_id/unidentified
    recordings appear in none of them."""

    entries: list[TaxonCount]
    other_count: int
    unmapped_count: int
    multi_species_count: int


def recording_counts_by_taxon(
    session: OrmSession,
    *,
    site_id: int | None = None,
    top_n: int = DEFAULT_TOP_N,
) -> TaxonBreakdown:
    recordings = _scoped_recordings(session, site_id=site_id)
    counts: dict[int, int] = {}
    unmapped = 0
    multi = 0
    for r in recordings:
        best = current_best_identification(r)
        if best is None or best.verdict in (Verdict.NOISE, Verdict.NO_ID):
            continue
        if best.is_multi:
            multi += 1
            continue
        taxon_id = best.primary.taxon_id
        if taxon_id is None:
            unmapped += 1
            continue
        counts[taxon_id] = counts.get(taxon_id, 0) + 1

    ranked = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    top = ranked[:top_n]
    other_count = sum(c for _, c in ranked[top_n:])

    taxa_by_id: dict[int, Taxon] = {}
    if top:
        taxa_by_id = {
            t.id: t
            for t in session.scalars(
                select(Taxon).where(Taxon.id.in_([tid for tid, _ in top])),
            )
        }
    entries = [
        TaxonCount(taxon=taxa_by_id[tid], count=c)
        for tid, c in top
        if tid in taxa_by_id
    ]
    return TaxonBreakdown(
        entries=entries,
        other_count=other_count,
        unmapped_count=unmapped,
        multi_species_count=multi,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `hatch test tests/test_statistics_query.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/fledermap/services/statistics.py tests/test_statistics_query.py
git commit -m "feat: statistics query layer -- recording_counts_by_taxon (donut)"
```

---

## Task 4: `rarest_species` and `rarest_unmapped_codes`

**Files:**
- Modify: `src/fledermap/services/statistics.py`
- Test: Modify `tests/test_statistics_query.py`

**Interfaces:**
- Consumes: `_scoped_recordings`, `TaxonCount`, `TaxonBreakdown` (Tasks 2-3).
- Produces: `rarest_species(session, *, bottom_n=DEFAULT_TOP_N) -> TaxonBreakdown` (reuses the `TaxonBreakdown` shape with `other_count`/`unmapped_count`/`multi_species_count` always 0). `CodeCount(code: str, count: int)`, `CodeBreakdown(entries: list[CodeCount])`, `rarest_unmapped_codes(session, *, bottom_n=DEFAULT_TOP_N) -> CodeBreakdown`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_statistics_query.py`:

```python
from fledermap.services.statistics import rarest_species, rarest_unmapped_codes


def test_rarest_species_excludes_taxa_with_zero_recordings(engine: Engine) -> None:
    with OrmSession(engine) as session:
        found = Taxon(rank="species", scientific_name="Eptesicus serotinus")
        never_found = Taxon(rank="species", scientific_name="Myotis daubentonii")
        session.add_all([found, never_found])
        session.flush()
        _recording(session, audio_hash="a" * 64, taxon_id=found.id)
        session.commit()

    with OrmSession(engine) as session:
        result = rarest_species(session)

    assert [e.taxon.scientific_name for e in result.entries] == ["Eptesicus serotinus"]


def test_rarest_species_sorts_ascending_by_count(engine: Engine) -> None:
    with OrmSession(engine) as session:
        common = Taxon(rank="species", scientific_name="Common")
        rare = Taxon(rank="species", scientific_name="Rare")
        session.add_all([common, rare])
        session.flush()
        for n in range(3):
            _recording(session, audio_hash=f"c{n}".rjust(64, "0"), taxon_id=common.id)
        _recording(session, audio_hash="r0".rjust(64, "0"), taxon_id=rare.id)
        session.commit()

    with OrmSession(engine) as session:
        result = rarest_species(session)

    assert [(e.taxon.scientific_name, e.count) for e in result.entries] == [
        ("Rare", 1),
        ("Common", 3),
    ]


def test_rarest_species_multi_species_recording_counts_toward_every_taxon(
    engine: Engine,
) -> None:
    """Deliberately DIFFERENT from recording_counts_by_taxon's own multi-
    species handling (Task 3) -- this list is "which species are seldom
    detected," so a multi-species recording adds to EVERY taxon it contains,
    not one combined bucket."""
    with OrmSession(engine) as session:
        taxon_a = Taxon(rank="species", scientific_name="Eptesicus serotinus")
        taxon_b = Taxon(rank="species", scientific_name="Pipistrellus pipistrellus")
        session.add_all([taxon_a, taxon_b])
        session.flush()
        r = Recording(
            audio_hash="a" * 64,
            path="a.wav",
            recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
        )
        session.add(r)
        session.flush()
        session.add_all(
            [
                Identification(
                    recording_id=r.id,
                    source=IdSource.EMT_MANUAL,
                    verdict=Verdict.SPECIES,
                    taxon_id=taxon_a.id,
                    first_seen_at=r.recorded_at,
                ),
                Identification(
                    recording_id=r.id,
                    source=IdSource.EMT_MANUAL,
                    verdict=Verdict.SPECIES,
                    taxon_id=taxon_b.id,
                    first_seen_at=r.recorded_at,
                ),
            ],
        )
        session.commit()

    with OrmSession(engine) as session:
        result = rarest_species(session)

    assert {(e.taxon.scientific_name, e.count) for e in result.entries} == {
        ("Eptesicus serotinus", 1),
        ("Pipistrellus pipistrellus", 1),
    }


def test_rarest_unmapped_codes_groups_by_raw_label(engine: Engine) -> None:
    with OrmSession(engine) as session:
        r1 = Recording(
            audio_hash="a" * 64,
            path="a.wav",
            recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
        )
        r2 = Recording(
            audio_hash="b" * 64,
            path="b.wav",
            recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
        )
        r3 = Recording(
            audio_hash="c" * 64,
            path="c.wav",
            recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
        )
        session.add_all([r1, r2, r3])
        session.flush()
        session.add_all(
            [
                Identification(
                    recording_id=r1.id,
                    source=IdSource.EMT_GUANO,
                    verdict=Verdict.SPECIES,
                    taxon_id=None,
                    raw_label="WEIRDCODE",
                    first_seen_at=r1.recorded_at,
                ),
                Identification(
                    recording_id=r2.id,
                    source=IdSource.EMT_GUANO,
                    verdict=Verdict.SPECIES,
                    taxon_id=None,
                    raw_label="COMMONCODE",
                    first_seen_at=r2.recorded_at,
                ),
                Identification(
                    recording_id=r3.id,
                    source=IdSource.EMT_GUANO,
                    verdict=Verdict.SPECIES,
                    taxon_id=None,
                    raw_label="COMMONCODE",
                    first_seen_at=r3.recorded_at,
                ),
            ],
        )
        session.commit()

    with OrmSession(engine) as session:
        result = rarest_unmapped_codes(session)

    assert [(e.code, e.count) for e in result.entries] == [
        ("WEIRDCODE", 1),
        ("COMMONCODE", 2),
    ]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `hatch test tests/test_statistics_query.py -k "rarest_species or rarest_unmapped_codes" -v`
Expected: FAIL — `ImportError`.

- [ ] **Step 3: Write the minimal implementation**

Add to `src/fledermap/services/statistics.py` (needs `from fledermap.store.models import Identification` added to imports):

```python
def rarest_species(session: OrmSession, *, bottom_n: int = DEFAULT_TOP_N) -> TaxonBreakdown:
    """Bottom-N current-best taxa by recording count, excluding taxa with
    zero recordings (a taxon never appears in `counts` unless something maps
    to it). Unlike `recording_counts_by_taxon`, a multi-species recording
    counts toward EVERY taxon it contains (see this function's own test for
    why), and unmapped-species results have no taxon to appear under at all
    -- both deliberate divergences documented in the spec."""
    recordings = _scoped_recordings(session)
    counts: dict[int, int] = {}
    for r in recordings:
        best = current_best_identification(r)
        if best is None or best.verdict in (Verdict.NOISE, Verdict.NO_ID):
            continue
        for taxon_id in best.taxon_ids:
            counts[taxon_id] = counts.get(taxon_id, 0) + 1

    ranked = sorted(counts.items(), key=lambda kv: kv[1])
    bottom = ranked[:bottom_n]
    taxa_by_id: dict[int, Taxon] = {}
    if bottom:
        taxa_by_id = {
            t.id: t
            for t in session.scalars(
                select(Taxon).where(Taxon.id.in_([tid for tid, _ in bottom])),
            )
        }
    entries = [
        TaxonCount(taxon=taxa_by_id[tid], count=c)
        for tid, c in bottom
        if tid in taxa_by_id
    ]
    return TaxonBreakdown(
        entries=entries,
        other_count=0,
        unmapped_count=0,
        multi_species_count=0,
    )


@dataclass(frozen=True)
class CodeCount:
    code: str
    count: int


@dataclass(frozen=True)
class CodeBreakdown:
    entries: list[CodeCount]


def rarest_unmapped_codes(
    session: OrmSession,
    *,
    bottom_n: int = DEFAULT_TOP_N,
) -> CodeBreakdown:
    """Bottom-N raw code strings among unmapped SPECIES-verdict claims,
    across every source's live claims (not just current-best) -- this is a
    review-queue-style surface ("which unmapped codes exist at all, and how
    rare are they"), not a per-recording current-best breakdown."""
    stmt = select(Identification.raw_label).where(
        Identification.taxon_id.is_(None),
        Identification.verdict == Verdict.SPECIES,
    )
    counts: dict[str, int] = {}
    for (raw_label,) in session.execute(stmt):
        label = raw_label or "(no code)"
        counts[label] = counts.get(label, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: kv[1])
    return CodeBreakdown(
        entries=[CodeCount(code=c, count=n) for c, n in ranked[:bottom_n]],
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `hatch test tests/test_statistics_query.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/fledermap/services/statistics.py tests/test_statistics_query.py
git commit -m "feat: statistics query layer -- rarest_species, rarest_unmapped_codes"
```

---

## Task 5: `recording_counts_by_site` (species page's site-ranking bar chart)

**Files:**
- Modify: `src/fledermap/services/statistics.py`
- Test: Modify `tests/test_statistics_query.py`

**Interfaces:**
- Consumes: `_scoped_recordings` (Task 2).
- Produces: `SiteCount(site: Site, count: int)`, `SiteBreakdown(entries: list[SiteCount])`, `recording_counts_by_site(session, *, taxon_id) -> SiteBreakdown`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_statistics_query.py`:

```python
from fledermap.services.statistics import recording_counts_by_site


def test_recording_counts_by_site_ranks_by_count_descending(engine: Engine) -> None:
    with OrmSession(engine) as session:
        taxon = Taxon(rank="species", scientific_name="Eptesicus serotinus")
        busy = Site(
            centroid=WKTElement("POINT(10 50)", srid=4326),
            radius_m=50.0,
            recording_count=2,
            first_at=datetime(2026, 8, 25, tzinfo=UTC),
            last_at=datetime(2026, 8, 25, tzinfo=UTC),
        )
        quiet = Site(
            centroid=WKTElement("POINT(11 51)", srid=4326),
            radius_m=50.0,
            recording_count=1,
            first_at=datetime(2026, 8, 25, tzinfo=UTC),
            last_at=datetime(2026, 8, 25, tzinfo=UTC),
        )
        session.add_all([taxon, busy, quiet])
        session.flush()
        _recording(session, audio_hash="a" * 64, taxon_id=taxon.id, site_id=busy.id)
        _recording(session, audio_hash="b" * 64, taxon_id=taxon.id, site_id=busy.id)
        _recording(session, audio_hash="c" * 64, taxon_id=taxon.id, site_id=quiet.id)
        session.commit()
        taxon_id = taxon.id

    with OrmSession(engine) as session:
        result = recording_counts_by_site(session, taxon_id=taxon_id)

    assert [(e.site.recording_count, e.count) for e in result.entries] == [(2, 2), (1, 1)]


def test_recording_counts_by_site_matches_by_membership_not_equality(
    engine: Engine,
) -> None:
    with OrmSession(engine) as session:
        taxon_a = Taxon(rank="species", scientific_name="Eptesicus serotinus")
        taxon_b = Taxon(rank="species", scientific_name="Pipistrellus pipistrellus")
        site = Site(
            centroid=WKTElement("POINT(10 50)", srid=4326),
            radius_m=50.0,
            recording_count=1,
            first_at=datetime(2026, 8, 25, tzinfo=UTC),
            last_at=datetime(2026, 8, 25, tzinfo=UTC),
        )
        session.add_all([taxon_a, taxon_b, site])
        session.flush()
        r = Recording(
            audio_hash="a" * 64,
            path="a.wav",
            recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
            site_id=site.id,
        )
        session.add(r)
        session.flush()
        session.add_all(
            [
                Identification(
                    recording_id=r.id,
                    source=IdSource.EMT_MANUAL,
                    verdict=Verdict.SPECIES,
                    taxon_id=taxon_a.id,
                    first_seen_at=r.recorded_at,
                ),
                Identification(
                    recording_id=r.id,
                    source=IdSource.EMT_MANUAL,
                    verdict=Verdict.SPECIES,
                    taxon_id=taxon_b.id,
                    first_seen_at=r.recorded_at,
                ),
            ],
        )
        session.commit()
        taxon_a_id = taxon_a.id

    with OrmSession(engine) as session:
        result = recording_counts_by_site(session, taxon_id=taxon_a_id)

    assert len(result.entries) == 1
    assert result.entries[0].count == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `hatch test tests/test_statistics_query.py -k recording_counts_by_site -v`
Expected: FAIL — `ImportError`.

- [ ] **Step 3: Write the minimal implementation**

Add to `src/fledermap/services/statistics.py`:

```python
@dataclass(frozen=True)
class SiteCount:
    site: Site
    count: int


@dataclass(frozen=True)
class SiteBreakdown:
    entries: list[SiteCount]


def recording_counts_by_site(session: OrmSession, *, taxon_id: int) -> SiteBreakdown:
    """Ranked site counts for one species -- the per-species page's "which
    sites" bar chart. `taxon_id` matches by MEMBERSHIP in a recording's
    current-best taxon set, not equality (same rule `totals`'s species scope
    uses) -- a multi-species recording containing the filtered species still
    counts, even though it's not that recording's sole result."""
    recordings = _scoped_recordings(session)
    counts: dict[int, int] = {}
    for r in recordings:
        if r.site_id is None:
            continue
        best = current_best_identification(r)
        if best is None or taxon_id not in best.taxon_ids:
            continue
        counts[r.site_id] = counts.get(r.site_id, 0) + 1

    sites_by_id: dict[int, Site] = {}
    if counts:
        sites_by_id = {
            s.id: s for s in session.scalars(select(Site).where(Site.id.in_(counts)))
        }
    entries = sorted(
        (
            SiteCount(site=sites_by_id[sid], count=c)
            for sid, c in counts.items()
            if sid in sites_by_id
        ),
        key=lambda e: e.count,
        reverse=True,
    )
    return SiteBreakdown(entries=entries)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `hatch test tests/test_statistics_query.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/fledermap/services/statistics.py tests/test_statistics_query.py
git commit -m "feat: statistics query layer -- recording_counts_by_site"
```

---

## Task 6: `site_diversity` (richness + Shannon)

**Files:**
- Modify: `src/fledermap/services/statistics.py`
- Test: Modify `tests/test_statistics_query.py`

**Interfaces:**
- Consumes: `_scoped_recordings` (Task 2).
- Produces: `SiteDiversity(site: Site, richness: int, shannon: float)`, `SiteDiversityBreakdown(entries: list[SiteDiversity])`, `site_diversity(session, *, site_id=None, sort_by="richness", top_n=DEFAULT_TOP_N) -> SiteDiversityBreakdown`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_statistics_query.py`:

```python
import math

from fledermap.services.statistics import site_diversity


def test_site_diversity_single_site_richness_and_shannon(engine: Engine) -> None:
    with OrmSession(engine) as session:
        site = Site(
            centroid=WKTElement("POINT(10 50)", srid=4326),
            radius_m=50.0,
            recording_count=2,
            first_at=datetime(2026, 8, 25, tzinfo=UTC),
            last_at=datetime(2026, 8, 25, tzinfo=UTC),
        )
        taxon_a = Taxon(rank="species", scientific_name="A")
        taxon_b = Taxon(rank="species", scientific_name="B")
        session.add_all([site, taxon_a, taxon_b])
        session.flush()
        _recording(session, audio_hash="a" * 64, taxon_id=taxon_a.id, site_id=site.id)
        _recording(session, audio_hash="b" * 64, taxon_id=taxon_b.id, site_id=site.id)
        session.commit()
        site_id = site.id

    with OrmSession(engine) as session:
        result = site_diversity(session, site_id=site_id)

    assert len(result.entries) == 1
    entry = result.entries[0]
    assert entry.richness == 2
    # Two equally-common species: H = -sum(p*ln(p)) for p=[0.5, 0.5] = ln(2)
    assert entry.shannon == pytest.approx(math.log(2))


def test_site_diversity_unknown_site_id_returns_empty(engine: Engine) -> None:
    with OrmSession(engine) as session:
        result = site_diversity(session, site_id=999999)

    assert result.entries == []


def test_site_diversity_ranks_top_n_sites_by_richness(engine: Engine) -> None:
    with OrmSession(engine) as session:
        rich = Site(
            centroid=WKTElement("POINT(10 50)", srid=4326),
            radius_m=50.0,
            recording_count=2,
            first_at=datetime(2026, 8, 25, tzinfo=UTC),
            last_at=datetime(2026, 8, 25, tzinfo=UTC),
        )
        poor = Site(
            centroid=WKTElement("POINT(11 51)", srid=4326),
            radius_m=50.0,
            recording_count=1,
            first_at=datetime(2026, 8, 25, tzinfo=UTC),
            last_at=datetime(2026, 8, 25, tzinfo=UTC),
        )
        taxon_a = Taxon(rank="species", scientific_name="A")
        taxon_b = Taxon(rank="species", scientific_name="B")
        session.add_all([rich, poor, taxon_a, taxon_b])
        session.flush()
        _recording(session, audio_hash="a" * 64, taxon_id=taxon_a.id, site_id=rich.id)
        _recording(session, audio_hash="b" * 64, taxon_id=taxon_b.id, site_id=rich.id)
        _recording(session, audio_hash="c" * 64, taxon_id=taxon_a.id, site_id=poor.id)
        session.commit()

    with OrmSession(engine) as session:
        result = site_diversity(session, sort_by="richness")

    assert [e.richness for e in result.entries] == [2, 1]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `hatch test tests/test_statistics_query.py -k site_diversity -v`
Expected: FAIL — `ImportError`.

- [ ] **Step 3: Write the minimal implementation**

Add to `src/fledermap/services/statistics.py` (add `import math` at the top with the other stdlib imports):

```python
def _shannon(taxon_counts: dict[int, int]) -> float:
    total = sum(taxon_counts.values())
    if total == 0:
        return 0.0
    return -sum(
        (c / total) * math.log(c / total) for c in taxon_counts.values() if c > 0
    )


@dataclass(frozen=True)
class SiteDiversity:
    site: Site
    richness: int
    shannon: float


@dataclass(frozen=True)
class SiteDiversityBreakdown:
    entries: list[SiteDiversity]


def _taxon_counts_at_site(recordings: Sequence[Recording]) -> dict[int, int]:
    """Same inclusion rules as `rarest_species`: noise/no_id/unidentified
    excluded, a multi-species recording increments every taxon it contains."""
    counts: dict[int, int] = {}
    for r in recordings:
        best = current_best_identification(r)
        if best is None or best.verdict in (Verdict.NOISE, Verdict.NO_ID):
            continue
        for taxon_id in best.taxon_ids:
            counts[taxon_id] = counts.get(taxon_id, 0) + 1
    return counts


def site_diversity(
    session: OrmSession,
    *,
    site_id: int | None = None,
    sort_by: str = "richness",
    top_n: int = DEFAULT_TOP_N,
) -> SiteDiversityBreakdown:
    """`site_id` set: the single-row breakdown for one site's two stat
    tiles. `site_id` unset: top-N sites ranked by `sort_by` ("richness" or
    "shannon") -- the global page calls this twice, once per sort key, for
    its two separate ranked lists."""
    if site_id is not None:
        site = session.get(Site, site_id)
        if site is None:
            return SiteDiversityBreakdown(entries=[])
        counts = _taxon_counts_at_site(_scoped_recordings(session, site_id=site_id))
        return SiteDiversityBreakdown(
            entries=[
                SiteDiversity(site=site, richness=len(counts), shannon=_shannon(counts)),
            ],
        )

    by_site: dict[int, dict[int, int]] = {}
    for r in _scoped_recordings(session):
        if r.site_id is None:
            continue
        best = current_best_identification(r)
        if best is None or best.verdict in (Verdict.NOISE, Verdict.NO_ID):
            continue
        site_counts = by_site.setdefault(r.site_id, {})
        for taxon_id in best.taxon_ids:
            site_counts[taxon_id] = site_counts.get(taxon_id, 0) + 1

    if not by_site:
        return SiteDiversityBreakdown(entries=[])

    sites_by_id = {
        s.id: s for s in session.scalars(select(Site).where(Site.id.in_(by_site)))
    }
    entries = [
        SiteDiversity(
            site=sites_by_id[sid],
            richness=len(counts),
            shannon=_shannon(counts),
        )
        for sid, counts in by_site.items()
        if sid in sites_by_id
    ]
    key = (lambda e: e.richness) if sort_by == "richness" else (lambda e: e.shannon)
    entries.sort(key=key, reverse=True)
    return SiteDiversityBreakdown(entries=entries[:top_n])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `hatch test tests/test_statistics_query.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/fledermap/services/statistics.py tests/test_statistics_query.py
git commit -m "feat: statistics query layer -- site_diversity (richness + Shannon)"
```

---

## Task 7: `recording_counts_by_month` and `recording_counts_by_hour`

**Files:**
- Modify: `src/fledermap/services/statistics.py`
- Test: Modify `tests/test_statistics_query.py`

**Interfaces:**
- Consumes: `_scoped_recordings` (Task 2).
- Produces: `SeriesByMonth(labels: list[str], taxa: list[Taxon], other_included: bool, single_species: bool, buckets: list[dict[int | None, int]])`, `SeriesByHour` (same shape), `recording_counts_by_month(session, *, site_id=None, taxon_id=None, top_n=DEFAULT_TOP_N) -> SeriesByMonth`, `recording_counts_by_hour(session, *, site_id=None, taxon_id=None, top_n=DEFAULT_TOP_N) -> SeriesByHour`.
- A documented scope decision this task makes explicit (not fully pinned by the spec): a multi-species or unmapped-species recording is excluded from both month/hour series in top-N-grouped mode (no `taxon_id` filter) — the same "noise/no_id/none excluded" rule from the spec's inclusion-rules section extends to these two cases here, since a per-species line series has no room for a "Multiple Species"/"Unmapped" line the way the donut has room for a slice. When `taxon_id` IS set, membership (not exclusion) applies, same as every other taxon_id-scoped function in this module.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_statistics_query.py`:

```python
from fledermap.services.statistics import recording_counts_by_hour, recording_counts_by_month


def test_recording_counts_by_month_buckets_by_calendar_month(engine: Engine) -> None:
    with OrmSession(engine) as session:
        taxon = Taxon(rank="species", scientific_name="Eptesicus serotinus")
        session.add(taxon)
        session.flush()
        _recording(
            session,
            audio_hash="a" * 64,
            taxon_id=taxon.id,
            recorded_at=datetime(2026, 1, 15, tzinfo=UTC),
        )
        _recording(
            session,
            audio_hash="b" * 64,
            taxon_id=taxon.id,
            recorded_at=datetime(2026, 8, 15, tzinfo=UTC),
        )
        session.commit()
        taxon_id = taxon.id

    with OrmSession(engine) as session:
        result = recording_counts_by_month(session)

    assert result.labels[0] == "Jan"
    assert result.buckets[0] == {taxon_id: 1}
    assert result.buckets[7] == {taxon_id: 1}  # August = index 7
    assert result.buckets[1] == {}
    assert result.single_species is False


def test_recording_counts_by_hour_buckets_by_clock_hour(engine: Engine) -> None:
    with OrmSession(engine) as session:
        taxon = Taxon(rank="species", scientific_name="Eptesicus serotinus")
        session.add(taxon)
        session.flush()
        _recording(
            session,
            audio_hash="a" * 64,
            taxon_id=taxon.id,
            recorded_at=datetime(2026, 8, 25, 22, 30, tzinfo=UTC),
        )
        session.commit()
        taxon_id = taxon.id

    with OrmSession(engine) as session:
        result = recording_counts_by_hour(session)

    assert len(result.buckets) == 24
    assert result.buckets[22] == {taxon_id: 1}


def test_recording_counts_by_month_taxon_scoped_is_a_single_unlabeled_series(
    engine: Engine,
) -> None:
    with OrmSession(engine) as session:
        taxon = Taxon(rank="species", scientific_name="Eptesicus serotinus")
        session.add(taxon)
        session.flush()
        _recording(
            session,
            audio_hash="a" * 64,
            taxon_id=taxon.id,
            recorded_at=datetime(2026, 1, 15, tzinfo=UTC),
        )
        session.commit()
        taxon_id = taxon.id

    with OrmSession(engine) as session:
        result = recording_counts_by_month(session, taxon_id=taxon_id)

    assert result.single_species is True
    assert result.taxa == []
    assert result.buckets[0] == {None: 1}


def test_recording_counts_by_month_folds_past_top_n_into_other(engine: Engine) -> None:
    with OrmSession(engine) as session:
        taxa = [Taxon(rank="species", scientific_name=f"Species {i}") for i in range(3)]
        session.add_all(taxa)
        session.flush()
        for i, count in enumerate([3, 2, 1]):
            for n in range(count):
                _recording(
                    session,
                    audio_hash=f"{i}{n}".rjust(64, "0"),
                    taxon_id=taxa[i].id,
                    recorded_at=datetime(2026, 1, 15, tzinfo=UTC),
                )
        session.commit()

    with OrmSession(engine) as session:
        result = recording_counts_by_month(session, top_n=2)

    assert result.other_included is True
    assert result.buckets[0][None] == 1  # Species 2's single recording folded into Other
    assert len(result.taxa) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `hatch test tests/test_statistics_query.py -k "recording_counts_by_month or recording_counts_by_hour" -v`
Expected: FAIL — `ImportError`.

- [ ] **Step 3: Write the minimal implementation**

Add to `src/fledermap/services/statistics.py` (needs `from collections.abc import Callable` added to imports):

```python
MONTH_LABELS: tuple[str, ...] = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)
HOUR_LABELS: tuple[str, ...] = tuple(str(h) for h in range(24))


@dataclass(frozen=True)
class SeriesByMonth:
    labels: tuple[str, ...]
    taxa: list[Taxon]
    other_included: bool
    single_species: bool
    buckets: list[dict[int | None, int]]


@dataclass(frozen=True)
class SeriesByHour:
    labels: tuple[str, ...]
    taxa: list[Taxon]
    other_included: bool
    single_species: bool
    buckets: list[dict[int | None, int]]


def _bucketed_series(
    session: OrmSession,
    *,
    site_id: int | None,
    taxon_id: int | None,
    top_n: int,
    bucket_count: int,
    bucket_of: Callable[[Recording], int],
) -> tuple[list[Taxon], bool, bool, list[dict[int | None, int]]]:
    """Shared by `recording_counts_by_month`/`recording_counts_by_hour` --
    the bucketing/Other-folding logic exists in exactly one place (spec's
    "Data layer" section). Returns (taxa, other_included, single_species,
    buckets)."""
    recordings = _scoped_recordings(session, site_id=site_id)
    buckets: list[dict[int | None, int]] = [{} for _ in range(bucket_count)]

    if taxon_id is not None:
        for r in recordings:
            best = current_best_identification(r)
            if best is None or taxon_id not in best.taxon_ids:
                continue
            b = bucket_of(r)
            buckets[b][None] = buckets[b].get(None, 0) + 1
        return [], False, True, buckets

    per_bucket_counts: list[dict[int, int]] = [{} for _ in range(bucket_count)]
    grand_totals: dict[int, int] = {}
    for r in recordings:
        best = current_best_identification(r)
        if best is None or best.verdict in (Verdict.NOISE, Verdict.NO_ID):
            continue
        if best.is_multi or best.primary.taxon_id is None:
            # A per-species line series has no room for a "Multiple
            # Species"/"Unmapped" line the way the donut has a slice for
            # each -- documented scope decision, see this task's docstring.
            continue
        tid = best.primary.taxon_id
        b = bucket_of(r)
        per_bucket_counts[b][tid] = per_bucket_counts[b].get(tid, 0) + 1
        grand_totals[tid] = grand_totals.get(tid, 0) + 1

    ranked = sorted(grand_totals.items(), key=lambda kv: kv[1], reverse=True)
    top_ids = [tid for tid, _ in ranked[:top_n]]
    other_ids = {tid for tid, _ in ranked[top_n:]}
    other_included = bool(other_ids)

    for b in range(bucket_count):
        folded: dict[int | None, int] = {
            tid: per_bucket_counts[b][tid]
            for tid in top_ids
            if tid in per_bucket_counts[b]
        }
        other_total = sum(
            c for tid, c in per_bucket_counts[b].items() if tid in other_ids
        )
        if other_total:
            folded[None] = other_total
        buckets[b] = folded

    taxa_by_id: dict[int, Taxon] = {}
    if top_ids:
        taxa_by_id = {
            t.id: t for t in session.scalars(select(Taxon).where(Taxon.id.in_(top_ids)))
        }
    taxa = [taxa_by_id[tid] for tid in top_ids if tid in taxa_by_id]
    return taxa, other_included, False, buckets


def recording_counts_by_month(
    session: OrmSession,
    *,
    site_id: int | None = None,
    taxon_id: int | None = None,
    top_n: int = DEFAULT_TOP_N,
) -> SeriesByMonth:
    taxa, other_included, single, buckets = _bucketed_series(
        session,
        site_id=site_id,
        taxon_id=taxon_id,
        top_n=top_n,
        bucket_count=12,
        bucket_of=lambda r: r.recorded_at.month - 1,
    )
    return SeriesByMonth(
        labels=MONTH_LABELS,
        taxa=taxa,
        other_included=other_included,
        single_species=single,
        buckets=buckets,
    )


def recording_counts_by_hour(
    session: OrmSession,
    *,
    site_id: int | None = None,
    taxon_id: int | None = None,
    top_n: int = DEFAULT_TOP_N,
) -> SeriesByHour:
    taxa, other_included, single, buckets = _bucketed_series(
        session,
        site_id=site_id,
        taxon_id=taxon_id,
        top_n=top_n,
        bucket_count=24,
        bucket_of=lambda r: r.recorded_at.hour,
    )
    return SeriesByHour(
        labels=HOUR_LABELS,
        taxa=taxa,
        other_included=other_included,
        single_species=single,
        buckets=buckets,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `hatch test tests/test_statistics_query.py -v`
Expected: all PASS.

- [ ] **Step 5: Run mypy on the new module**

Run: `hatch run types:check`
Expected: `Success: no issues found`.

- [ ] **Step 6: Commit**

```bash
git add src/fledermap/services/statistics.py tests/test_statistics_query.py
git commit -m "feat: statistics query layer -- recording_counts_by_month/hour"
```

---

## Task 8: JS reshaping helper (`statistics_charts.js`)

**Files:**
- Create: `src/fledermap/web/static/statistics_charts.js`
- Create: `tests/js/statistics_charts.test.js`

**Interfaces:**
- Produces: `taxonBreakdownToChartData(breakdown, otherColor)` — turns a `TaxonBreakdown`-shaped plain object (as it will be JSON-serialized by the view: `{entries: [{taxon: {id, scientific_name}, count}], other_count, unmapped_count, multi_species_count}`) into `{labels: string[], data: number[], colors: string[]}` for a Chart.js doughnut dataset. `seriesToChartData(series, otherColor)` — turns a `SeriesByMonth`/`SeriesByHour`-shaped object into `{labels: string[], datasets: [{label, data: number[], borderColor}]}` for a Chart.js line dataset. Both are pure functions, no DOM access — depend on `colorForTaxon` from `marker_colors.js` (loaded before this file, per this project's existing JS-split convention).

- [ ] **Step 1: Write the failing tests**

Create `tests/js/statistics_charts.test.js`:

```javascript
const { test } = require("node:test");
const assert = require("node:assert/strict");
const { taxonBreakdownToChartData, seriesToChartData } = require(
  "../../src/fledermap/web/static/statistics_charts.js",
);

test("taxonBreakdownToChartData maps entries to labels/data/colors", () => {
  const breakdown = {
    entries: [
      { taxon: { id: 1, scientific_name: "Eptesicus serotinus" }, count: 5 },
      { taxon: { id: 2, scientific_name: "Pipistrellus pipistrellus" }, count: 3 },
    ],
    other_count: 0,
    unmapped_count: 0,
    multi_species_count: 0,
  };
  const result = taxonBreakdownToChartData(breakdown, "#999999");
  assert.deepEqual(result.labels, [
    "Eptesicus serotinus",
    "Pipistrellus pipistrellus",
  ]);
  assert.deepEqual(result.data, [5, 3]);
  assert.equal(result.colors.length, 2);
});

test("taxonBreakdownToChartData appends Other/Unmapped/Multiple Species slices when present", () => {
  const breakdown = {
    entries: [],
    other_count: 4,
    unmapped_count: 2,
    multi_species_count: 1,
  };
  const result = taxonBreakdownToChartData(breakdown, "#999999");
  assert.deepEqual(result.labels, ["Other", "Unmapped species", "Multiple Species"]);
  assert.deepEqual(result.data, [4, 2, 1]);
  assert.equal(result.colors[0], "#999999");
});

test("taxonBreakdownToChartData omits zero-count extra slices", () => {
  const breakdown = { entries: [], other_count: 0, unmapped_count: 0, multi_species_count: 0 };
  const result = taxonBreakdownToChartData(breakdown, "#999999");
  assert.deepEqual(result.labels, []);
  assert.deepEqual(result.data, []);
});

test("seriesToChartData builds one dataset per taxon plus Other when included", () => {
  const series = {
    labels: ["Jan", "Feb"],
    taxa: [{ id: 1, scientific_name: "Eptesicus serotinus" }],
    other_included: true,
    single_species: false,
    buckets: [{ 1: 3, null: 1 }, {}],
  };
  const result = seriesToChartData(series, "#999999");
  assert.deepEqual(result.labels, ["Jan", "Feb"]);
  assert.equal(result.datasets.length, 2);
  assert.equal(result.datasets[0].label, "Eptesicus serotinus");
  assert.deepEqual(result.datasets[0].data, [3, 0]);
  assert.equal(result.datasets[1].label, "Other");
  assert.deepEqual(result.datasets[1].data, [1, 0]);
});

test("seriesToChartData builds a single unlabeled dataset for a single-species series", () => {
  const series = {
    labels: ["Jan", "Feb"],
    taxa: [],
    other_included: false,
    single_species: true,
    buckets: [{ null: 2 }, { null: 0 }],
  };
  const result = seriesToChartData(series, "#999999");
  assert.equal(result.datasets.length, 1);
  assert.deepEqual(result.datasets[0].data, [2, 0]);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test tests/js/statistics_charts.test.js`
Expected: FAIL — cannot find module `statistics_charts.js`.

- [ ] **Step 3: Write the minimal implementation**

Create `src/fledermap/web/static/statistics_charts.js`:

```javascript
// src/fledermap/web/static/statistics_charts.js -- pure reshaping logic, no
// DOM access, following the split app.js/marker_colors.js already
// established (CLAUDE.md's JavaScript tooling section): turns the JSON a
// statistics view embeds into the exact shape Chart.js's dataset API wants.
// Loaded via its own <script> tag, before statistics.js (which mounts the
// actual canvases) and after marker_colors.js (colorForTaxon).

function taxonBreakdownToChartData(breakdown, otherColor) {
  const labels = [];
  const data = [];
  const colors = [];
  for (const entry of breakdown.entries) {
    labels.push(entry.taxon.scientific_name);
    data.push(entry.count);
    colors.push(colorForTaxon(entry.taxon.id));
  }
  if (breakdown.other_count > 0) {
    labels.push("Other");
    data.push(breakdown.other_count);
    colors.push(otherColor);
  }
  if (breakdown.unmapped_count > 0) {
    labels.push("Unmapped species");
    data.push(breakdown.unmapped_count);
    colors.push("#333333");
  }
  if (breakdown.multi_species_count > 0) {
    labels.push("Multiple Species");
    data.push(breakdown.multi_species_count);
    colors.push(MULTI_SPECIES_COLOR);
  }
  return { labels, data, colors };
}

function seriesToChartData(series, otherColor) {
  const datasets = series.taxa.map((taxon) => ({
    label: taxon.scientific_name,
    data: series.buckets.map((bucket) => bucket[taxon.id] || 0),
    borderColor: colorForTaxon(taxon.id),
  }));
  if (series.other_included) {
    datasets.push({
      label: "Other",
      data: series.buckets.map((bucket) => bucket[null] || 0),
      borderColor: otherColor,
    });
  }
  if (series.single_species) {
    datasets.push({
      label: "Recordings",
      data: series.buckets.map((bucket) => bucket[null] || 0),
      borderColor: otherColor,
    });
  }
  return { labels: series.labels, datasets };
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { taxonBreakdownToChartData, seriesToChartData };
}
```

**Note:** this file calls `colorForTaxon`/`MULTI_SPECIES_COLOR` as bare globals (matching `marker_colors.js`'s own established pattern of being loaded before `app.js` and called as globals) — under `node --test`, `require()`-ing this file directly will throw `ReferenceError: colorForTaxon is not defined` for any test that reaches those code paths, UNLESS the test file also requires `marker_colors.js` first. Fix this in the test file itself:

```javascript
// Add near the top of tests/js/statistics_charts.test.js, before the other require:
global.colorForTaxon = require("../../src/fledermap/web/static/marker_colors.js").colorForTaxon;
global.MULTI_SPECIES_COLOR = require("../../src/fledermap/web/static/marker_colors.js").MULTI_SPECIES_COLOR;
```

- [ ] **Step 4: Run test to verify it passes**

Run: `node --test tests/js/statistics_charts.test.js`
Expected: all PASS.

- [ ] **Step 5: Run the full JS test suite to confirm no regression**

Run: `node --test tests/js/`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/fledermap/web/static/statistics_charts.js tests/js/statistics_charts.test.js
git commit -m "feat: statistics chart-data reshaping helpers (pure JS, node:test-covered)"
```

---

## Task 9: Shared CSS (`.stats-band`/`.band-label`/`.stats-panel`/`.stats-tile`) + style guide

**Files:**
- Modify: `src/fledermap/web/static/app.css`
- Modify: `docs/style-guide.md`

**Interfaces:**
- Produces: the four CSS classes named in the spec's "Page layout" section, ready for the page templates in Tasks 10-12 to use.

- [ ] **Step 1: Add the CSS**

Add to `src/fledermap/web/static/app.css` (near `.entity-list`/`.panel-columns`, matching this project's existing dark-mode-token convention — every color via `var(--color-*)`, never a literal hex, so it works in both themes automatically):

```css
/* Statistics dashboard: bands (macro-structure, tinted, no border) holding
   cards (bordered, own header) only where 2+ widgets compare side by side --
   see docs/superpowers/specs/2026-09-05-fledermap-statistics-design.md's
   "Page layout" section for the exact band/card assignment per page. */
.stats-band { background: var(--color-bg-subtle); border-radius: 6px; padding: 1rem; margin-bottom: 0.75rem; }
.band-label { font-size: 0.7rem; font-weight: 700; letter-spacing: 0.05em; color: var(--color-muted); text-transform: uppercase; margin-bottom: 0.6rem; }
.stats-panel-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 0.75rem; }
.stats-panel { background: var(--color-bg); border: 1px solid var(--color-border); border-radius: 6px; padding: 0.75rem; }
.stats-panel-header { font-size: 0.85rem; font-weight: 600; margin-bottom: 0.5rem; padding-bottom: 0.4rem; border-bottom: 1px solid var(--color-border); }
.stats-tiles { display: flex; gap: 0.75rem; flex-wrap: wrap; }
.stats-tile { flex: 1 1 140px; background: var(--color-bg); border: 1px solid var(--color-border); border-radius: 6px; padding: 0.75rem; text-align: center; }
.stats-tile .num { font-size: 1.4rem; font-weight: 700; color: var(--color-accent); }
.stats-tile .label { font-size: 0.75rem; color: var(--color-muted); }
.stats-caption { font-size: 0.8rem; color: var(--color-muted); margin: 0.4rem 0; }
.stats-caption summary { cursor: pointer; }
```

- [ ] **Step 2: Add the style guide entry**

Add to `docs/style-guide.md`'s "Shared classes" section (alongside `.entity-list`):

```markdown
### `.stats-band` / `.stats-panel` / `.stats-tile`

Statistics dashboard layout (see the statistics design spec's "Page layout" section for the full
rationale). `.stats-band` (tinted background, no border) groups a page's content into a handful
of macro-sections, each labeled with `.band-label` (small bold/uppercase caption) rather than a
full `<h2>`. `.stats-panel` (bordered, own `.stats-panel-header`) wraps an individual chart/list
ONLY where a band holds 2+ widgets meant to be compared side by side — laid out via
`.stats-panel-grid`'s responsive `auto-fit` grid, which adapts to however many cards a band
actually has rather than assuming a fixed count. A band holding exactly one widget renders it
directly (no redundant nested card). `.stats-tile` is a single stat number (a count), kept
distinct from `.entity-list`'s table rows since it's one number, not a row of comparable fields.
Never nest a bordered `.stats-panel` inside another `.stats-panel` — a card only ever nests
inside a tinted, borderless band.
```

- [ ] **Step 3: Confirm no test regression**

Run: `hatch fmt --check`
Expected: `All checks passed` / `files already formatted` (CSS/Markdown aren't ruff-checked, but this confirms nothing else broke).

- [ ] **Step 4: Commit**

```bash
git add src/fledermap/web/static/app.css docs/style-guide.md
git commit -m "feat: statistics dashboard CSS (bands + selective cards)"
```

---

## Task 10: Global `/statistics` page

**Files:**
- Create: `src/fledermap/web/views/statistics.py`
- Create: `src/fledermap/web/templates/statistics_global.html`
- Create: `src/fledermap/web/static/statistics.js`
- Modify: `src/fledermap/web/app.py`
- Test: Create `tests/test_statistics_view.py`

**Interfaces:**
- Consumes: every function from `services/statistics.py` (Tasks 2-7), `statistics_charts.js`/`marker_colors.js` (Task 8), `.stats-*` CSS (Task 9).
- Produces: `statistics_bp` Flask blueprint with `GET /statistics`, registered in `app.py`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_statistics_view.py`, following `tests/test_entities_view.py`'s style:

```python
# tests/test_statistics_view.py
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import IdSource, Verdict
from fledermap.store.models import Identification, Recording, Taxon
from fledermap.web.app import create_app

pytestmark = pytest.mark.db


def test_statistics_global_page_renders_stat_tiles_and_chart_data(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        taxon = Taxon(rank="species", scientific_name="Eptesicus serotinus")
        session.add(taxon)
        session.flush()
        r = Recording(
            audio_hash="a" * 64,
            path="a.wav",
            recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
        )
        session.add(r)
        session.flush()
        session.add(
            Identification(
                recording_id=r.id,
                source=IdSource.EMT_GUANO,
                verdict=Verdict.SPECIES,
                taxon_id=taxon.id,
                first_seen_at=r.recorded_at,
            ),
        )
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().get("/statistics")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "1" in html  # total recordings stat tile
    assert "Eptesicus serotinus" in html  # embedded in the donut's JSON data
    assert "stats-band" in html
    assert "chart.js" in html  # vendored script tag
```

- [ ] **Step 2: Run test to verify it fails**

Run: `hatch test tests/test_statistics_view.py -v`
Expected: FAIL — 404 (`/statistics` route doesn't exist).

- [ ] **Step 3: Write the minimal implementation**

Create `src/fledermap/web/views/statistics.py`:

```python
"""Statistics dashboard pages (docs/superpowers/specs/2026-09-05-fledermap-
statistics-design.md). Full standalone pages, same precedent as
sessions.py/recording_detail.py/entities.py."""

from __future__ import annotations

import dataclasses
import json

import flask
from sqlalchemy.orm import Session as OrmSession

from fledermap.services.statistics import (
    rarest_species,
    rarest_unmapped_codes,
    recording_counts_by_hour,
    recording_counts_by_month,
    recording_counts_by_taxon,
    site_diversity,
    totals,
)

statistics_bp = flask.Blueprint(
    "statistics",
    __name__,
    template_folder="../templates",
)


def _taxon_json(taxon: object) -> dict[str, object]:
    return {"id": taxon.id, "scientific_name": taxon.scientific_name}  # type: ignore[attr-defined]


def _breakdown_json(breakdown: object) -> dict[str, object]:
    return {
        "entries": [
            {"taxon": _taxon_json(e.taxon), "count": e.count}
            for e in breakdown.entries  # type: ignore[attr-defined]
        ],
        "other_count": breakdown.other_count,  # type: ignore[attr-defined]
        "unmapped_count": breakdown.unmapped_count,  # type: ignore[attr-defined]
        "multi_species_count": breakdown.multi_species_count,  # type: ignore[attr-defined]
    }


def _series_json(series: object) -> dict[str, object]:
    return {
        "labels": list(series.labels),  # type: ignore[attr-defined]
        "taxa": [_taxon_json(t) for t in series.taxa],  # type: ignore[attr-defined]
        "other_included": series.other_included,  # type: ignore[attr-defined]
        "single_species": series.single_species,  # type: ignore[attr-defined]
        "buckets": [
            {("null" if k is None else k): v for k, v in bucket.items()}
            for bucket in series.buckets  # type: ignore[attr-defined]
        ],
    }


@statistics_bp.get("/statistics")
def global_statistics_page() -> flask.Response:
    engine = flask.current_app.config["ENGINE"]
    with OrmSession(engine) as session:
        global_totals = totals(session)
        donut = recording_counts_by_taxon(session)
        rarest = rarest_species(session)
        rarest_codes = rarest_unmapped_codes(session)
        richest_sites = site_diversity(session, sort_by="richness")
        diverse_sites = site_diversity(session, sort_by="shannon")
        month_series = recording_counts_by_month(session)
        hour_series = recording_counts_by_hour(session)

        html = flask.render_template(
            "statistics_global.html",
            totals=global_totals,
            donut_json=json.dumps(_breakdown_json(donut)),
            rarest=rarest,
            rarest_codes=rarest_codes,
            richest_sites=richest_sites.entries,
            diverse_sites=diverse_sites.entries,
            month_json=json.dumps(_series_json(month_series)),
            hour_json=json.dumps(_series_json(hour_series)),
        )
    return flask.make_response(html)
```

Register in `src/fledermap/web/app.py` (same pattern as every other blueprint):

```python
from fledermap.web.views.statistics import statistics_bp
# ...
app.register_blueprint(statistics_bp)
```

Create `src/fledermap/web/templates/statistics_global.html`:

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  {% include "_theme_init.html" %}
  {% include "_favicon.html" %}
  <title>Fledermap — Statistics</title>
  <link rel="stylesheet" href="{{ url_for('static', filename='app.css') }}">
</head>
<body>
  {% include "_nav.html" %}
  <main class="main-content">
    <h1>Statistics</h1>

    <div class="stats-band">
      <div class="band-label">Overview</div>
      <div class="stats-tiles">
        <div class="stats-tile"><div class="num">{{ totals.total_recordings }}</div><div class="label">Recordings</div></div>
        <div class="stats-tile"><div class="num">{{ totals.total_species }}</div><div class="label">Species</div></div>
        <div class="stats-tile"><div class="num">{{ totals.total_sites }}</div><div class="label">Sites</div></div>
      </div>
    </div>

    <div class="stats-band">
      <div class="band-label">Species</div>
      <div class="stats-panel-grid">
        <div class="stats-panel">
          <div class="stats-panel-header">Species composition</div>
          <canvas id="species-donut" data-chart="donut"></canvas>
          <details class="stats-caption"><summary>ⓘ</summary>Only recordings with a species-level result are counted; noise/no-ID/unidentified recordings are excluded, so this doesn't sum to the total recordings tile above.</details>
        </div>
        <div class="stats-panel">
          <div class="stats-panel-header">Rarest species</div>
          <table class="entity-list">
            <tbody>
              {% for entry in rarest.entries %}
              <tr><td><a href="/species/{{ entry.taxon.id }}">{{ entry.taxon.scientific_name }}</a></td><td>{{ entry.count }}</td></tr>
              {% else %}
              <tr><td>No species detected yet.</td></tr>
              {% endfor %}
            </tbody>
          </table>
        </div>
        <div class="stats-panel">
          <div class="stats-panel-header">Rarest unmapped codes</div>
          <table class="entity-list">
            <tbody>
              {% for entry in rarest_codes.entries %}
              <tr><td>{{ entry.code }}</td><td>{{ entry.count }}</td></tr>
              {% else %}
              <tr><td>No unmapped codes.</td></tr>
              {% endfor %}
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <div class="stats-band">
      <div class="band-label">Sites</div>
      <div class="stats-panel-grid">
        <div class="stats-panel">
          <div class="stats-panel-header">Most species-rich sites</div>
          <table class="entity-list">
            <tbody>
              {% for entry in richest_sites %}
              <tr><td><a href="/sites/{{ entry.site.id }}">{{ entry.site.name or "Site #" ~ entry.site.id }}</a></td><td>richness {{ entry.richness }}, H {{ "%.2f"|format(entry.shannon) }}</td></tr>
              {% else %}
              <tr><td>No sites yet.</td></tr>
              {% endfor %}
            </tbody>
          </table>
        </div>
        <div class="stats-panel">
          <div class="stats-panel-header">Most diverse sites (Shannon H)</div>
          <table class="entity-list">
            <tbody>
              {% for entry in diverse_sites %}
              <tr><td><a href="/sites/{{ entry.site.id }}">{{ entry.site.name or "Site #" ~ entry.site.id }}</a></td><td>H {{ "%.2f"|format(entry.shannon) }}, richness {{ entry.richness }}</td></tr>
              {% else %}
              <tr><td>No sites yet.</td></tr>
              {% endfor %}
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <div class="stats-band">
      <div class="band-label">Activity over time</div>
      <div class="stats-panel-grid">
        <div class="stats-panel">
          <div class="stats-panel-header">Recordings per month</div>
          <canvas id="month-line" data-chart="line"></canvas>
          <details class="stats-caption"><summary>ⓘ</summary>Bucketed by calendar month, server time (UTC) -- not adjusted to any local timezone.</details>
        </div>
        <div class="stats-panel">
          <div class="stats-panel-header">Recordings per hour of day</div>
          <canvas id="hour-line" data-chart="line"></canvas>
          <details class="stats-caption"><summary>ⓘ</summary>Hour of day, server time (UTC) -- not sunset-relative (see the backlogged "H:MM before/after sunset" feature).</details>
        </div>
      </div>
    </div>

    <script id="species-donut-data" type="application/json">{{ donut_json | safe }}</script>
    <script id="month-line-data" type="application/json">{{ month_json | safe }}</script>
    <script id="hour-line-data" type="application/json">{{ hour_json | safe }}</script>
  </main>

  <script src="{{ url_for('vendor.static', filename='chart.js') }}"></script>
  <script src="{{ url_for('static', filename='marker_colors.js') }}"></script>
  <script src="{{ url_for('static', filename='statistics_charts.js') }}"></script>
  <script src="{{ url_for('static', filename='statistics.js') }}"></script>
</body>
</html>
```

Create `src/fledermap/web/static/statistics.js` (DOM-touching -- mounts the actual `<canvas>` elements; NOT node:test-covered per this project's convention, live-verified with headless Chrome in Task 13 instead):

```javascript
// src/fledermap/web/static/statistics.js -- mounts Chart.js canvases from the
// embedded JSON <script type="application/json"> blocks each statistics page
// writes. Pure reshaping lives in statistics_charts.js (node:test-covered);
// this file is the DOM-touching half, per CLAUDE.md's JS-split convention.

const OTHER_COLOR = "#9e9e9e"; // neutral gray, not handed out by colorForTaxon -- see marker_colors.js's reserved-colors comment.

function readEmbeddedJson(id) {
  const el = document.getElementById(id);
  if (!el) return null;
  return JSON.parse(el.textContent);
}

function mountDonut(canvasId, dataId) {
  const canvas = document.getElementById(canvasId);
  const raw = readEmbeddedJson(dataId);
  if (!canvas || !raw) return;
  const chartData = taxonBreakdownToChartData(raw, OTHER_COLOR);
  new Chart(canvas, {
    type: "doughnut",
    data: {
      labels: chartData.labels,
      datasets: [{ data: chartData.data, backgroundColor: chartData.colors }],
    },
    options: { plugins: { legend: { position: "right" } } },
  });
}

function mountLine(canvasId, dataId) {
  const canvas = document.getElementById(canvasId);
  const raw = readEmbeddedJson(dataId);
  if (!canvas || !raw) return;
  const chartData = seriesToChartData(raw, OTHER_COLOR);
  new Chart(canvas, {
    type: "line",
    data: {
      labels: chartData.labels,
      datasets: chartData.datasets.map((ds) => ({ ...ds, fill: false, tension: 0.2 })),
    },
    options: { plugins: { legend: { display: chartData.datasets.length > 1 } } },
  });
}

document.addEventListener("DOMContentLoaded", () => {
  mountDonut("species-donut", "species-donut-data");
  mountLine("month-line", "month-line-data");
  mountLine("hour-line", "hour-line-data");
});
```

- [ ] **Step 4: Run test to verify it passes**

Run: `hatch test tests/test_statistics_view.py -v`
Expected: PASS.

- [ ] **Step 5: Run mypy**

Run: `hatch run types:check`
Expected: `Success: no issues found`.

- [ ] **Step 6: Commit**

```bash
git add src/fledermap/web/views/statistics.py src/fledermap/web/templates/statistics_global.html \
  src/fledermap/web/static/statistics.js src/fledermap/web/app.py tests/test_statistics_view.py
git commit -m "feat: global /statistics dashboard page"
```

---

## Task 11: Per-site `/statistics/sites/<id>` page

**Files:**
- Modify: `src/fledermap/web/views/statistics.py`
- Create: `src/fledermap/web/templates/statistics_site.html`
- Modify: `src/fledermap/web/static/statistics.js`
- Test: Modify `tests/test_statistics_view.py`

**Interfaces:**
- Consumes: `totals(session, site_id=...)`, `recording_counts_by_taxon(session, site_id=...)`, `site_diversity(session, site_id=...)`, `recording_counts_by_month/hour(session, site_id=...)` (all already support `site_id` from Tasks 2-7).
- Produces: `GET /statistics/sites/<int:site_id>`, 404 for an unknown site (same pattern as `views/entities.py`'s `site_detail_page`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_statistics_view.py`:

```python
from geoalchemy2.elements import WKTElement

from fledermap.store.models import Site


def test_statistics_site_page_renders_and_404s_for_unknown_site(
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

    response = client.get(f"/statistics/sites/{site_id}")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Old Barn" in html
    assert "Diversity index" in html

    missing_response = client.get("/statistics/sites/999999")
    assert missing_response.status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `hatch test tests/test_statistics_view.py -k statistics_site_page -v`
Expected: FAIL — 404 (route doesn't exist).

- [ ] **Step 3: Write the minimal implementation**

Add to `src/fledermap/web/views/statistics.py` (needs `from fledermap.services.map_query import site_detail` and `from fledermap.web.params import fallback_site_label` and `from fledermap.store.geo import decode_point` added to imports):

```python
@statistics_bp.get("/statistics/sites/<int:site_id>")
def site_statistics_page(site_id: int) -> flask.Response:
    engine = flask.current_app.config["ENGINE"]
    with OrmSession(engine) as session:
        site_info = site_detail(session, site_id)
        if site_info is None:
            flask.abort(404)
        label = (
            site_info.site.name
            if site_info.site.name
            else fallback_site_label(decode_point(site_info.site.centroid))
        )
        site_totals = totals(session, site_id=site_id)
        diversity = site_diversity(session, site_id=site_id).entries[0]
        donut = recording_counts_by_taxon(session, site_id=site_id)
        month_series = recording_counts_by_month(session, site_id=site_id)
        hour_series = recording_counts_by_hour(session, site_id=site_id)

        html = flask.render_template(
            "statistics_site.html",
            site=site_info.site,
            label=label,
            totals=site_totals,
            diversity=diversity,
            donut_json=json.dumps(_breakdown_json(donut)),
            month_json=json.dumps(_series_json(month_series)),
            hour_json=json.dumps(_series_json(hour_series)),
        )
    return flask.make_response(html)
```

Create `src/fledermap/web/templates/statistics_site.html` (same shell/script-tag pattern as `statistics_global.html`; band assignment per the spec: Overview has no cards, Species is a single un-carded donut, Activity over time has 2 cards):

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  {% include "_theme_init.html" %}
  {% include "_favicon.html" %}
  <title>Fledermap — {{ label }} statistics</title>
  <link rel="stylesheet" href="{{ url_for('static', filename='app.css') }}">
</head>
<body>
  {% include "_nav.html" %}
  <main class="main-content">
    <p><a href="/sites/{{ site.id }}">← {{ label }}</a></p>
    <h1>{{ label }} — Statistics</h1>

    <div class="stats-band">
      <div class="band-label">Overview</div>
      <div class="stats-tiles">
        <div class="stats-tile"><div class="num">{{ totals.total_recordings }}</div><div class="label">Recordings</div></div>
        <div class="stats-tile"><div class="num">{{ diversity.richness }}</div><div class="label">Species richness</div></div>
        <div class="stats-tile"><div class="num">{{ "%.2f"|format(diversity.shannon) }}</div><div class="label">Diversity index (Shannon H)</div></div>
      </div>
    </div>

    <div class="stats-band">
      <div class="band-label">Species</div>
      <canvas id="species-donut" data-chart="donut"></canvas>
    </div>

    <div class="stats-band">
      <div class="band-label">Activity over time</div>
      <div class="stats-panel-grid">
        <div class="stats-panel">
          <div class="stats-panel-header">Recordings per month</div>
          <canvas id="month-line" data-chart="line"></canvas>
          <details class="stats-caption"><summary>ⓘ</summary>Bucketed by calendar month, server time (UTC) -- not adjusted to any local timezone.</details>
        </div>
        <div class="stats-panel">
          <div class="stats-panel-header">Recordings per hour of day</div>
          <canvas id="hour-line" data-chart="line"></canvas>
          <details class="stats-caption"><summary>ⓘ</summary>Hour of day, server time (UTC) -- not sunset-relative (see the backlogged "H:MM before/after sunset" feature).</details>
        </div>
      </div>
    </div>

    <script id="species-donut-data" type="application/json">{{ donut_json | safe }}</script>
    <script id="month-line-data" type="application/json">{{ month_json | safe }}</script>
    <script id="hour-line-data" type="application/json">{{ hour_json | safe }}</script>
  </main>

  <script src="{{ url_for('vendor.static', filename='chart.js') }}"></script>
  <script src="{{ url_for('static', filename='marker_colors.js') }}"></script>
  <script src="{{ url_for('static', filename='statistics_charts.js') }}"></script>
  <script src="{{ url_for('static', filename='statistics.js') }}"></script>
</body>
</html>
```

Add the same donut exclusion caption used on the global page (Task 10) right after `<canvas id="species-donut">`:

```html
<details class="stats-caption"><summary>ⓘ</summary>Only recordings with a species-level result are counted; noise/no-ID/unidentified recordings are excluded.</details>
```

`statistics.js`'s `DOMContentLoaded` listener already mounts by fixed element ids (`species-donut`, `month-line`, `hour-line`) — no change needed there, since this page reuses the same ids as the global page (they're never both on screen at once).

- [ ] **Step 4: Run test to verify it passes**

Run: `hatch test tests/test_statistics_view.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/fledermap/web/views/statistics.py src/fledermap/web/templates/statistics_site.html tests/test_statistics_view.py
git commit -m "feat: per-site /statistics/sites/<id> page"
```

---

## Task 12: Per-species `/statistics/species/<id>` page

**Files:**
- Modify: `src/fledermap/web/views/statistics.py`
- Create: `src/fledermap/web/templates/statistics_species.html`
- Modify: `src/fledermap/web/static/statistics.js`
- Test: Modify `tests/test_statistics_view.py`

**Interfaces:**
- Consumes: `totals(session, taxon_id=...)`, `recording_counts_by_site(session, taxon_id=...)`, `recording_counts_by_month/hour(session, taxon_id=...)` (Tasks 2, 5, 7).
- Produces: `GET /statistics/species/<int:taxon_id>`, 404 for an unknown taxon. `statistics.js` gains a bar-chart mounting function (the site-ranking chart, new chart type not used on the other two pages).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_statistics_view.py`:

```python
def test_statistics_species_page_renders_and_404s_for_unknown_taxon(
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

    response = client.get(f"/statistics/species/{taxon_id}")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Eptesicus serotinus" in html

    missing_response = client.get("/statistics/species/999999")
    assert missing_response.status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `hatch test tests/test_statistics_view.py -k statistics_species_page -v`
Expected: FAIL — 404 (route doesn't exist).

- [ ] **Step 3: Write the minimal implementation**

Add to `src/fledermap/web/views/statistics.py` (needs `from fledermap.store.models import Taxon` added to imports):

```python
def _site_breakdown_json(breakdown: object) -> dict[str, object]:
    return {
        "entries": [
            {
                "site": {"id": e.site.id, "name": e.site.name or f"Site #{e.site.id}"},
                "count": e.count,
                "statistics_url": f"/statistics/sites/{e.site.id}",  # type: ignore[attr-defined]
            }
            for e in breakdown.entries  # type: ignore[attr-defined]
        ],
    }


@statistics_bp.get("/statistics/species/<int:taxon_id>")
def species_statistics_page(taxon_id: int) -> flask.Response:
    engine = flask.current_app.config["ENGINE"]
    with OrmSession(engine) as session:
        taxon = session.get(Taxon, taxon_id)
        if taxon is None:
            flask.abort(404)
        species_totals = totals(session, taxon_id=taxon_id)
        site_breakdown = recording_counts_by_site(session, taxon_id=taxon_id)
        month_series = recording_counts_by_month(session, taxon_id=taxon_id)
        hour_series = recording_counts_by_hour(session, taxon_id=taxon_id)

        html = flask.render_template(
            "statistics_species.html",
            taxon=taxon,
            totals=species_totals,
            sites_json=json.dumps(_site_breakdown_json(site_breakdown)),
            month_json=json.dumps(_series_json(month_series)),
            hour_json=json.dumps(_series_json(hour_series)),
        )
    return flask.make_response(html)
```

Create `src/fledermap/web/templates/statistics_species.html`:

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  {% include "_theme_init.html" %}
  {% include "_favicon.html" %}
  <title>Fledermap — {{ taxon.scientific_name }} statistics</title>
  <link rel="stylesheet" href="{{ url_for('static', filename='app.css') }}">
</head>
<body>
  {% include "_nav.html" %}
  <main class="main-content">
    <p><a href="/species/{{ taxon.id }}">← {{ taxon.scientific_name }}</a></p>
    <h1>{{ taxon.scientific_name }} — Statistics</h1>

    <div class="stats-band">
      <div class="band-label">Overview</div>
      <div class="stats-tiles">
        <div class="stats-tile"><div class="num">{{ totals.total_recordings }}</div><div class="label">Recordings</div></div>
        <div class="stats-tile"><div class="num">{{ totals.total_sites }}</div><div class="label">Sites detected at</div></div>
      </div>
    </div>

    <div class="stats-band">
      <div class="band-label">Sites</div>
      <canvas id="sites-bar" data-chart="bar"></canvas>
    </div>

    <div class="stats-band">
      <div class="band-label">Activity over time</div>
      <div class="stats-panel-grid">
        <div class="stats-panel">
          <div class="stats-panel-header">Recordings per month</div>
          <canvas id="month-line" data-chart="line"></canvas>
          <details class="stats-caption"><summary>ⓘ</summary>Bucketed by calendar month, server time (UTC) -- not adjusted to any local timezone.</details>
        </div>
        <div class="stats-panel">
          <div class="stats-panel-header">Recordings per hour of day</div>
          <canvas id="hour-line" data-chart="line"></canvas>
          <details class="stats-caption"><summary>ⓘ</summary>Hour of day, server time (UTC) -- not sunset-relative (see the backlogged "H:MM before/after sunset" feature).</details>
        </div>
      </div>
    </div>

    <script id="sites-bar-data" type="application/json">{{ sites_json | safe }}</script>
    <script id="month-line-data" type="application/json">{{ month_json | safe }}</script>
    <script id="hour-line-data" type="application/json">{{ hour_json | safe }}</script>
  </main>

  <script src="{{ url_for('vendor.static', filename='chart.js') }}"></script>
  <script src="{{ url_for('static', filename='marker_colors.js') }}"></script>
  <script src="{{ url_for('static', filename='statistics_charts.js') }}"></script>
  <script src="{{ url_for('static', filename='statistics.js') }}"></script>
</body>
</html>
```

Add a bar-chart mount function to `src/fledermap/web/static/statistics.js` (this page's site-ranking chart is a new chart type, not covered by `mountDonut`/`mountLine`):

```javascript
function mountSiteBar(canvasId, dataId) {
  const canvas = document.getElementById(canvasId);
  const raw = readEmbeddedJson(dataId);
  if (!canvas || !raw) return;
  new Chart(canvas, {
    type: "bar",
    data: {
      labels: raw.entries.map((e) => e.site.name),
      datasets: [{ data: raw.entries.map((e) => e.count), backgroundColor: OTHER_COLOR }],
    },
    options: {
      indexAxis: "y",
      plugins: { legend: { display: false } },
      // A bar in the site-ranking chart click-throughs to that site's own
      // statistics sub-page -- same "chart element -> stats sub-page"
      // behavior as the global page's donut slices (spec's "Name and label
      // linking" section).
      onClick: (_event, elements) => {
        if (!elements.length) return;
        const entry = raw.entries[elements[0].index];
        if (entry && entry.statistics_url) {
          window.location.href = entry.statistics_url;
        }
      },
    },
  });
}
```

Update the `DOMContentLoaded` listener at the bottom of `statistics.js` to call every mount function that finds its element (each is already a no-op via the `if (!canvas || !raw) return;` guard when the page doesn't have that element, so it's safe to call all three unconditionally on every statistics page):

```javascript
document.addEventListener("DOMContentLoaded", () => {
  mountDonut("species-donut", "species-donut-data");
  mountLine("month-line", "month-line-data");
  mountLine("hour-line", "hour-line-data");
  mountSiteBar("sites-bar", "sites-bar-data");
});
```

- [ ] **Step 4: Run test to verify it passes**

Run: `hatch test tests/test_statistics_view.py -v`
Expected: all PASS.

- [ ] **Step 5: Run the full Python test suite (fast subset) for regressions**

Run: `hatch test -m "not db"`
Expected: all PASS, no regressions.

- [ ] **Step 6: Commit**

```bash
git add src/fledermap/web/views/statistics.py src/fledermap/web/templates/statistics_species.html \
  src/fledermap/web/static/statistics.js tests/test_statistics_view.py
git commit -m "feat: per-species /statistics/species/<id> page"
```

---

## Task 13: Interlinking — detail-page "Statistics" links, donut/bar click-throughs

**Files:**
- Modify: `src/fledermap/web/templates/species_detail.html`
- Modify: `src/fledermap/web/templates/site_detail.html`
- Modify: `src/fledermap/web/static/statistics.js`
- Modify: `src/fledermap/web/templates/statistics_global.html`
- Test: Modify `tests/test_entities_view.py`, `tests/test_statistics_view.py`

**Interfaces:**
- Produces: a "Statistics" link on both entity detail pages into their statistics sub-page (spec: "The species detail page and site detail page each get a 'Statistics' link into their respective sub-page"). The global page's donut slices and site-ranking-adjacent lists already link by name (rarest-species list, site lists) per the existing templates from Tasks 10-12 — this task adds the donut's own click-through to a species' statistics sub-page (spec: "The global page's donut slices... are click-throughs to the matching per-species/per-site page").

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_entities_view.py` (species/site detail tests):

```python
def test_species_detail_page_links_to_its_statistics_subpage(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        taxon = Taxon(rank="species", scientific_name="Eptesicus serotinus")
        session.add(taxon)
        session.commit()
        taxon_id = taxon.id

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    html = app.test_client().get(f"/species/{taxon_id}").get_data(as_text=True)

    assert f'href="/statistics/species/{taxon_id}"' in html


def test_site_detail_page_links_to_its_statistics_subpage(
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
        )
        session.add(site)
        session.commit()
        site_id = site.id

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    html = app.test_client().get(f"/sites/{site_id}").get_data(as_text=True)

    assert f'href="/statistics/sites/{site_id}"' in html
```

Add to `tests/test_statistics_view.py`:

```python
def test_statistics_global_page_donut_slices_are_click_throughs_to_species_pages(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        taxon = Taxon(rank="species", scientific_name="Eptesicus serotinus")
        session.add(taxon)
        session.flush()
        r = Recording(
            audio_hash="a" * 64,
            path="a.wav",
            recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
        )
        session.add(r)
        session.flush()
        session.add(
            Identification(
                recording_id=r.id,
                source=IdSource.EMT_GUANO,
                verdict=Verdict.SPECIES,
                taxon_id=taxon.id,
                first_seen_at=r.recorded_at,
            ),
        )
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    html = app.test_client().get("/statistics").get_data(as_text=True)

    # The donut mounts client-side from embedded JSON -- what the server-
    # rendered page must supply is a per-taxon statistics URL alongside each
    # entry, which statistics.js's onClick handler reads.
    assert '"statistics_url": "/statistics/species/' in html or '"statistics_url":"/statistics/species/' in html
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `hatch test tests/test_entities_view.py tests/test_statistics_view.py -k "statistics_subpage or click_throughs" -v`
Expected: FAIL — links/JSON field don't exist yet.

- [ ] **Step 3: Add the "Statistics" links to the detail pages**

In `src/fledermap/web/templates/species_detail.html`, add near the top (after the `← All species` back-link):

```html
<p><a href="/statistics/species/{{ detail.taxon.id }}">Statistics</a></p>
```

In `src/fledermap/web/templates/site_detail.html`, add inside the existing `.panel-header` div, alongside "Show on map":

```html
<a href="/statistics/sites/{{ detail.site.id }}">Statistics</a>
```

- [ ] **Step 4: Add `statistics_url` to the donut's embedded JSON and wire up the click-through**

In `src/fledermap/web/views/statistics.py`, extend `_breakdown_json`'s entry shape:

```python
def _breakdown_json(breakdown: object) -> dict[str, object]:
    return {
        "entries": [
            {
                "taxon": _taxon_json(e.taxon),
                "count": e.count,
                "statistics_url": f"/statistics/species/{e.taxon.id}",  # type: ignore[attr-defined]
            }
            for e in breakdown.entries  # type: ignore[attr-defined]
        ],
        "other_count": breakdown.other_count,  # type: ignore[attr-defined]
        "unmapped_count": breakdown.unmapped_count,  # type: ignore[attr-defined]
        "multi_species_count": breakdown.multi_species_count,  # type: ignore[attr-defined]
    }
```

In `src/fledermap/web/static/statistics.js`, extend `mountDonut` to navigate on slice click (Chart.js's `onClick` handler receives the clicked element's index, which maps back to `raw.entries` in the same order `taxonBreakdownToChartData` iterated them — extra slices (Other/Unmapped/Multiple Species) have no `statistics_url` and are simply not clickable):

```javascript
function mountDonut(canvasId, dataId) {
  const canvas = document.getElementById(canvasId);
  const raw = readEmbeddedJson(dataId);
  if (!canvas || !raw) return;
  const chartData = taxonBreakdownToChartData(raw, OTHER_COLOR);
  new Chart(canvas, {
    type: "doughnut",
    data: {
      labels: chartData.labels,
      datasets: [{ data: chartData.data, backgroundColor: chartData.colors }],
    },
    options: {
      plugins: { legend: { position: "right" } },
      onClick: (_event, elements) => {
        if (!elements.length) return;
        const entry = raw.entries[elements[0].index];
        if (entry && entry.statistics_url) {
          window.location.href = entry.statistics_url;
        }
      },
    },
  });
}
```

- [ ] **Step 5: Wire up legend-text click-through to the entity's own detail page**

Spec's "Name and label linking" section requires two DIFFERENT click destinations on visually
similar chart parts: a donut slice/bar → statistics sub-page (Step 4 above), but a
legend swatch/label → the entity's plain detail page (`/species/<id>`, `/sites/<id>`) instead.
Chart.js's `options.plugins.legend.onClick` callback receives the clicked legend item's `index`,
which maps 1:1 back to the same-order array the chart's labels were built from — no extra JSON
field needed, since every entry/taxon/site object already carries its own `id`.

In `src/fledermap/web/static/statistics.js`, add a shared helper and use it in `mountDonut` and
`mountLine`:

```javascript
function navigateOnLegendClick(getDetailUrl) {
  return (_event, legendItem) => {
    const url = getDetailUrl(legendItem.index);
    if (url) window.location.href = url;
  };
}
```

Extend `mountDonut`'s `options.plugins.legend` (added alongside the existing `position: "right"`):

```javascript
legend: {
  position: "right",
  onClick: navigateOnLegendClick((index) => {
    const entry = raw.entries[index];
    return entry ? `/species/${entry.taxon.id}` : null;
  }),
},
```

Extend `mountLine` similarly — its legend items map to `raw.taxa` in order (the "Other"/single-
species trailing entry has no taxon, so `getDetailUrl` returns `null` for it and the helper is a
no-op):

```javascript
legend: {
  display: chartData.datasets.length > 1,
  onClick: navigateOnLegendClick((index) => {
    const taxon = raw.taxa[index];
    return taxon ? `/species/${taxon.id}` : null;
  }),
},
```

`mountSiteBar` (Task 12) has its legend hidden (`display: false`, one dataset only), so it has no
legend to wire up — its only click target is the bar itself, already handled in Task 12.

- [ ] **Step 6: Run tests to verify they pass**

Run: `hatch test tests/test_entities_view.py tests/test_statistics_view.py -v`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add src/fledermap/web/templates/species_detail.html src/fledermap/web/templates/site_detail.html \
  src/fledermap/web/static/statistics.js src/fledermap/web/views/statistics.py \
  tests/test_entities_view.py tests/test_statistics_view.py
git commit -m "feat: statistics interlinking -- detail-page links, chart click-throughs, legend links"
```

---

## Task 14: Full-suite verification + mandatory live-verification pass

**Files:** none (verification only).

- [ ] **Step 1: Run the full Python test suite**

Run: `hatch test` (needs `dangerouslyDisableSandbox: true` for the `db`-marked tests per `CLAUDE.md`'s Docker gotcha)
Expected: all PASS, pristine output (no warnings).

- [ ] **Step 2: Run the full JS test suite**

Run: `node --test tests/js/`
Expected: all PASS.

- [ ] **Step 3: Run mypy and ruff**

Run: `hatch run types:check` then `hatch fmt --check`
Expected: both clean.

- [ ] **Step 4: Mandatory headless-Chrome live-verification**

Per `CLAUDE.md`'s JavaScript tooling section, this is **not optional** — Task 5a's manual-classification feature shipped two Critical bugs specifically because this step was skipped twice. Follow the project's established technique (`reference-headless-chrome-live-verification-technique` memory / earlier sessions' `.mjs` scripts): spin up a throwaway PostGIS container, seed it with recordings/identifications/sites spanning several species and at least one multi-species and one unmapped-species recording (to exercise every donut slice type), run `fledermap serve` against it, and drive real headless Chrome to:

- Load `/statistics` and confirm all three canvases (`species-donut`, `month-line`, `hour-line`) actually rendered pixels (not blank/erroring) — check `canvas.toDataURL()` isn't a blank-image data URL, or screenshot and visually confirm.
- Hover a donut slice and confirm a tooltip appears (Chart.js's built-in tooltip DOM node becomes visible).
- Click a donut slice and confirm navigation to `/statistics/species/<id>`.
- Load `/statistics/sites/<id>` and `/statistics/species/<id>` for real seeded entities and confirm their charts render too (the bar chart in particular — a new chart type not exercised on the global page).
- Confirm the `<details><summary>ⓘ</summary>` info affordances expand on click.
- Screenshot all three pages at a real viewport width and visually confirm the bands+cards layout reads as intended (matches the approved mockup's spirit — tinted bands, bordered cards only in multi-widget bands, responsive card grid).

Clean up the throwaway container/server afterward, per this project's established discipline.

- [ ] **Step 5: Report results**

If any check fails, fix before proceeding — do not mark this task done with a known-failing check.
