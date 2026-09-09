# Flagged for Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a human flag a recording for review, compute "needs review" automatically from
rarity/misattribution rules, and provide a dedicated Reviews workflow (list page + scoped
prev/next on the recording details page) to work through flagged recordings and reclassify them.

**Architecture:** One new boolean column (`Recording.flagged_for_review`, mirroring the existing
`favourite` pattern) plus a new pure computation module (`services/review_flags.py`) that derives
"needs review" reasons from existing `Identification`/`Taxon`/`Site` data. Both feed a new
`needs_review` filter dimension in `services/map_query.py`'s `filtered_recordings`, which the
already-generic `neighbor_recordings` then uses for free. A new `/reviews` page is the entry point;
the recording details page gains prev/next (previously drawer-only) plus a review-mode banner when
navigation is scoped to `needs_review`.

**Tech Stack:** Flask, SQLAlchemy 2.0 ORM (Postgres/PostGIS), Jinja2 templates, htmx, Alpine.js,
vanilla JS (`node:test`), pytest (`db`-marked tests use a real PostGIS testcontainer), Alembic.

**Spec:** `docs/superpowers/specs/2026-09-09-fledermap-flagged-for-review-design.md`

## Global Constraints

- Never hand-craft an Alembic revision ID — generate the file with `hatch run alembic revision -m
  "<message>"` against the current head, then edit its `upgrade()`/`downgrade()` by hand (this
  schema change needs no autogenerate diff).
- `hatch test -m "not db"` is the fast pre-commit subset; any test touching `Recording`,
  `Identification`, `Taxon`, or `Site` via a real session needs `pytestmark = pytest.mark.db` and
  the `engine: Engine` fixture (see `tests/test_map_query.py`), and must be run with
  `dangerouslyDisableSandbox: true` (Docker is blocked by the command sandbox otherwise).
- `hatch run types:check` covers `tests/` too — bind `X | None`, assert not-None, then dereference;
  never `# type: ignore`.
- Computed review criteria are **never dismissible directly** — no "resolve"/"clear" action is
  built for them anywhere in this plan. Only the manual `flagged_for_review` boolean has a toggle.
- Misattribution accounting excludes a `MANUAL` verdict of `NO_ID` entirely (neither numerator nor
  denominator) — see spec's Goals section for the exact per-verdict table.
- Every new template/JS change touching the DOM needs the mandatory headless-Chrome
  live-verification pass (CLAUDE.md's JavaScript tooling section) before the task is considered
  done — not optional, skipping it has shipped Critical bugs here before.
- Drawer/details parity (CLAUDE.md): whatever the drawer shows for the flag (badge, reasons,
  toggle) must also appear on the details page, via independent templates/routes, not shared
  markup.

---

## Task 1: `Recording.flagged_for_review` column + migration

**Files:**
- Modify: `src/fledermap/store/models.py` (`Recording` class, near `favourite` at line ~84)
- Create: `src/fledermap/alembic/versions/<generated>_add_recording_flagged_for_review.py`
- Test: `tests/test_migrations.py` (existing drift test covers this automatically — no new test
  file needed, just confirm it passes)

**Interfaces:**
- Produces: `Recording.flagged_for_review: bool` (default `False`), readable/writable by every
  later task.

- [ ] **Step 1: Add the column to the model**

In `src/fledermap/store/models.py`, right after the existing `favourite` line:

```python
    favourite: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    flagged_for_review: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False,
    )
```

- [ ] **Step 2: Generate the migration file**

Run: `hatch run alembic revision -m "add recording flagged for review"`

This creates a new file in `src/fledermap/alembic/versions/` with a fresh revision ID and
`down_revision = "ef85421bf57d"` (the current head — confirm with `hatch run alembic heads` first
in case another migration landed since this plan was written).

- [ ] **Step 3: Fill in the migration body**

Edit the generated file's `upgrade`/`downgrade`, matching
`0bc3a164ef9c_add_recording_favourite.py`'s exact shape:

```python
def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "recording",
        sa.Column(
            "flagged_for_review", sa.Boolean(), nullable=False, server_default="false",
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("recording", "flagged_for_review")
```

- [ ] **Step 4: Run the migration drift test**

Run: `hatch test tests/test_migrations.py -m db` with `dangerouslyDisableSandbox: true`
Expected: PASS — `compare_metadata` finds no drift between the model and the migrated schema.

- [ ] **Step 5: Run the fast test subset and type-check**

Run: `hatch test -m "not db"` and `hatch run types:check`
Expected: both PASS.

- [ ] **Step 6: Commit**

```bash
git add src/fledermap/store/models.py src/fledermap/alembic/versions/
git commit -m "feat: add Recording.flagged_for_review column"
```

---

## Task 2: `services/review_flags.py` — rarity criterion

**Files:**
- Create: `src/fledermap/services/review_flags.py`
- Test: `tests/test_review_flags.py`

**Interfaces:**
- Consumes: `Recording`, `Identification`, `Taxon` (`store/models.py`);
  `current_best_identification` (`services/current_best.py`).
- Produces: `_taxon_counts(session: OrmSession) -> tuple[dict[int, int], dict[tuple[int, int], int]]`
  — `(dataset_counts: {taxon_id: count}, site_counts: {(site_id, taxon_id): count})`, consumed by
  Task 4's `ReviewContext.build`.
  `_rarity_reason(dataset_counts, site_counts, site_id, taxon_id, taxon_label) -> str | None`,
  consumed by Task 4's `review_reasons`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_review_flags.py
from __future__ import annotations

from fledermap.services.review_flags import _rarity_reason, _taxon_counts


def test_rarity_reason_none_when_common() -> None:
    dataset_counts = {1: 50}
    site_counts = {(10, 1): 20}
    assert _rarity_reason(dataset_counts, site_counts, 10, 1, "Pipistrellus pipistrellus") is None


def test_rarity_reason_fires_at_site_threshold() -> None:
    dataset_counts = {1: 50}
    site_counts = {(10, 1): 2}
    reason = _rarity_reason(dataset_counts, site_counts, 10, 1, "Pipistrellus pipistrellus")
    assert reason is not None
    assert "2" in reason and "site" in reason


def test_rarity_reason_fires_at_dataset_threshold() -> None:
    dataset_counts = {1: 5}
    site_counts = {(10, 1): 30}
    reason = _rarity_reason(dataset_counts, site_counts, 10, 1, "Pipistrellus pipistrellus")
    assert reason is not None
    assert "5" in reason and "dataset" in reason


def test_rarity_reason_handles_no_site() -> None:
    # recording.site_id is None -- must not raise, and must still fall
    # back to the dataset-wide check.
    dataset_counts = {1: 3}
    site_counts = {}
    reason = _rarity_reason(dataset_counts, site_counts, None, 1, "Pipistrellus pipistrellus")
    assert reason is not None
```

Then a `db`-marked test for the aggregate builder itself:

```python
# appended to tests/test_review_flags.py
import pytest
from geoalchemy2.elements import WKTElement
from sqlalchemy import Engine
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import Verdict
from fledermap.store.models import Identification, Recording, Site, Taxon

pytestmark = pytest.mark.db


def _site(session: OrmSession, *, lon: float = 10.0, lat: float = 50.0) -> Site:
    from datetime import UTC, datetime

    site = Site(
        centroid=WKTElement(f"POINT({lon} {lat})", srid=4326),
        radius_m=50.0,
        recording_count=0,
        first_at=datetime(2026, 8, 1, tzinfo=UTC),
        last_at=datetime(2026, 8, 25, tzinfo=UTC),
    )
    session.add(site)
    session.flush()
    return site


def test_taxon_counts_tallies_by_site_and_dataset(engine: Engine) -> None:
    from datetime import UTC, datetime

    with OrmSession(engine) as session:
        taxon = Taxon(rank="species", scientific_name="Pipistrellus pipistrellus")
        session.add(taxon)
        session.flush()
        site = _site(session)
        for i in range(3):
            r = Recording(
                audio_hash=f"{i:064x}",
                path=f"{i}.wav",
                recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
                site_id=site.id,
            )
            session.add(r)
            session.flush()
            session.add(
                Identification(
                    recording_id=r.id,
                    source="emt.guano",
                    verdict=Verdict.SPECIES,
                    taxon_id=taxon.id,
                    first_seen_at=r.recorded_at,
                ),
            )
        session.commit()

        dataset_counts, site_counts = _taxon_counts(session)

    assert dataset_counts[taxon.id] == 3
    assert site_counts[(site.id, taxon.id)] == 3
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `hatch test tests/test_review_flags.py -m "not db" -v` (pure-Python ones) and
`hatch test tests/test_review_flags.py -m db -v` (the aggregate one, `dangerouslyDisableSandbox:
true`)
Expected: FAIL — `fledermap.services.review_flags` doesn't exist yet.

- [ ] **Step 3: Write the implementation**

```python
# src/fledermap/services/review_flags.py
"""Computed "needs review" criteria for a recording's assigned species
(docs/superpowers/specs/2026-09-09-fledermap-flagged-for-review-design.md).
Nothing here is stored -- every reason is recomputed live and cleared only
by fixing the underlying data (mapping a species code, adding a manual
classification), the same philosophy the unmapped-species review queue
already uses. See services/manual_classification.py's current_manual_state
and services/current_best.py's current_best_identification, which this
module builds on rather than duplicates."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from fledermap.services.current_best import current_best_identification
from fledermap.store.models import Recording

# Fixed, explainable thresholds (spec: "fixed low count dataset-wide" over a
# percentile -- doesn't shift as more data comes in, and stays simple to
# reason about at this project's self-hosted scale).
SITE_RARITY_MAX = 2
DATASET_RARITY_MAX = 5


def _taxon_counts(
    session: OrmSession,
) -> tuple[dict[int, int], dict[tuple[int, int], int]]:
    """(dataset-wide taxon_id -> count, (site_id, taxon_id) -> count), tallied
    from every non-missing recording's CURRENT-BEST taxon set -- same "walk
    every recording, recompute current-best in Python" style as
    services/statistics.py's totals/richness functions, since there's no SQL
    equivalent of current_best_identification's precedence walk. A
    multi-species MANUAL result contributes to every one of its taxa, not
    just one."""
    recordings = session.scalars(
        select(Recording).where(Recording.missing_since.is_(None)),
    ).all()
    dataset_counts: dict[int, int] = {}
    site_counts: dict[tuple[int, int], int] = {}
    for r in recordings:
        best = current_best_identification(r)
        if best is None:
            continue
        for taxon_id in best.taxon_ids:
            dataset_counts[taxon_id] = dataset_counts.get(taxon_id, 0) + 1
            if r.site_id is not None:
                key = (r.site_id, taxon_id)
                site_counts[key] = site_counts.get(key, 0) + 1
    return dataset_counts, site_counts


def _rarity_reason(
    dataset_counts: dict[int, int],
    site_counts: dict[tuple[int, int], int],
    site_id: int | None,
    taxon_id: int,
    taxon_label: str,
) -> str | None:
    """Either threshold is enough to flag -- a species can be locally rare at
    one site while common dataset-wide (or vice versa for a site with very
    little coverage), and both are independently interesting to a
    reviewer."""
    if site_id is not None:
        site_count = site_counts.get((site_id, taxon_id), 0)
        if site_count <= SITE_RARITY_MAX:
            plural = "" if site_count == 1 else "s"
            return f"rare species ({site_count} recording{plural} at this site)"
    dataset_count = dataset_counts.get(taxon_id, 0)
    if dataset_count <= DATASET_RARITY_MAX:
        plural = "" if dataset_count == 1 else "s"
        return f"rare species ({dataset_count} recording{plural} dataset-wide)"
    return None
```

Note: `taxon_label` is accepted but unused by `_rarity_reason` itself in this task — it's
threaded through in Task 4 where the reason string needs the actual species name, not just
`taxon_id`. Update the signature now so Task 4 doesn't need to touch this function again:

```python
def _rarity_reason(
    dataset_counts: dict[int, int],
    site_counts: dict[tuple[int, int], int],
    site_id: int | None,
    taxon_id: int,
    taxon_label: str,
) -> str | None:
    if site_id is not None:
        site_count = site_counts.get((site_id, taxon_id), 0)
        if site_count <= SITE_RARITY_MAX:
            plural = "" if site_count == 1 else "s"
            return f"{taxon_label}: rare ({site_count} recording{plural} at this site)"
    dataset_count = dataset_counts.get(taxon_id, 0)
    if dataset_count <= DATASET_RARITY_MAX:
        plural = "" if dataset_count == 1 else "s"
        return f"{taxon_label}: rare ({dataset_count} recording{plural} dataset-wide)"
    return None
```

Update the test file's assertions accordingly (`"Pipistrellus pipistrellus"` now appears in the
reason string, e.g. `assert "Pipistrellus pipistrellus" in reason and "2" in reason`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `hatch test tests/test_review_flags.py -v` (both marker sets, `db` ones with
`dangerouslyDisableSandbox: true`)
Expected: PASS

- [ ] **Step 5: Type-check**

Run: `hatch run types:check`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/fledermap/services/review_flags.py tests/test_review_flags.py
git commit -m "feat: add rarity criterion for flagged-for-review"
```

---

## Task 3: `services/review_flags.py` — misattribution criterion

**Files:**
- Modify: `src/fledermap/services/review_flags.py`
- Test: `tests/test_review_flags.py`

**Interfaces:**
- Consumes: `Recording.identifications` (loaded via `selectin`, `store/models.py`); `IdSource`,
  `Verdict` (`domain/codes.py`).
- Produces:
  `_misattribution_rates(session: OrmSession) -> dict[tuple[IdSource, int], tuple[int, int]]`
  (`(source, taxon_id) -> (disagreements, total_with_manual_verdict)`), consumed by Task 4.
  `_misattribution_reason(rates, source, taxon_id, taxon_label) -> str | None`, consumed by Task 4.

- [ ] **Step 1: Write the failing tests**

```python
# appended to tests/test_review_flags.py
from fledermap.domain.codes import IdSource
from fledermap.services.review_flags import _misattribution_rates, _misattribution_reason


def test_misattribution_reason_none_below_minimum_count() -> None:
    rates = {(IdSource.EMT_GUANO, 1): (2, 2)}  # 2/2 wrong, but under the min. of 3
    assert _misattribution_reason(rates, IdSource.EMT_GUANO, 1, "Myotis daubentonii") is None


def test_misattribution_reason_none_below_majority() -> None:
    rates = {(IdSource.EMT_GUANO, 1): (3, 7)}  # 3/7 wrong -- not a majority
    assert _misattribution_reason(rates, IdSource.EMT_GUANO, 1, "Myotis daubentonii") is None


def test_misattribution_reason_fires_at_majority_and_minimum() -> None:
    rates = {(IdSource.EMT_GUANO, 1): (3, 5)}  # 3/5 wrong, >=3 disagreements
    reason = _misattribution_reason(rates, IdSource.EMT_GUANO, 1, "Myotis daubentonii")
    assert reason is not None
    assert "Myotis daubentonii" in reason
    assert IdSource.EMT_GUANO.value in reason


def test_misattribution_reason_absent_pair_is_none() -> None:
    assert _misattribution_reason({}, IdSource.EMT_GUANO, 1, "Myotis daubentonii") is None
```

And the `db`-marked aggregate builder, verifying the exact per-verdict table from the spec:

```python
# appended to tests/test_review_flags.py (still under pytestmark = pytest.mark.db from Task 2)
from datetime import UTC, datetime

from fledermap.domain.codes import IdSource, Verdict


def _recording_with_claims(
    session: OrmSession,
    *,
    audio_hash: str,
    classifier_taxon_id: int,
    manual_verdict: Verdict | None,
    manual_taxon_ids: tuple[int, ...] = (),
) -> Recording:
    r = Recording(
        audio_hash=audio_hash,
        path=f"{audio_hash}.wav",
        recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
    )
    session.add(r)
    session.flush()
    session.add(
        Identification(
            recording_id=r.id,
            source=IdSource.EMT_GUANO,
            verdict=Verdict.SPECIES,
            taxon_id=classifier_taxon_id,
            first_seen_at=r.recorded_at,
        ),
    )
    if manual_verdict == Verdict.SPECIES:
        for taxon_id in manual_taxon_ids:
            session.add(
                Identification(
                    recording_id=r.id,
                    source=IdSource.MANUAL,
                    verdict=Verdict.SPECIES,
                    taxon_id=taxon_id,
                    first_seen_at=r.recorded_at,
                ),
            )
    elif manual_verdict is not None:
        session.add(
            Identification(
                recording_id=r.id,
                source=IdSource.MANUAL,
                verdict=manual_verdict,
                taxon_id=None,
                first_seen_at=r.recorded_at,
            ),
        )
    session.flush()
    return r


def test_misattribution_rates_matches_the_spec_table(engine: Engine) -> None:
    with OrmSession(engine) as session:
        x = Taxon(rank="species", scientific_name="X species")
        y = Taxon(rank="species", scientific_name="Y species")
        session.add_all([x, y])
        session.flush()

        # C: X, H: X -> correct
        _recording_with_claims(
            session, audio_hash="a" * 64, classifier_taxon_id=x.id,
            manual_verdict=Verdict.SPECIES, manual_taxon_ids=(x.id,),
        )
        # C: X, H: Y -> misattribution
        _recording_with_claims(
            session, audio_hash="b" * 64, classifier_taxon_id=x.id,
            manual_verdict=Verdict.SPECIES, manual_taxon_ids=(y.id,),
        )
        # C: X, H: {X, Y} -> correct
        _recording_with_claims(
            session, audio_hash="c" * 64, classifier_taxon_id=x.id,
            manual_verdict=Verdict.SPECIES, manual_taxon_ids=(x.id, y.id),
        )
        # C: X, H: {Y, Z} -> misattribution (using just Y here, two-taxon
        # case already covered above)
        z = Taxon(rank="species", scientific_name="Z species")
        session.add(z)
        session.flush()
        _recording_with_claims(
            session, audio_hash="d" * 64, classifier_taxon_id=x.id,
            manual_verdict=Verdict.SPECIES, manual_taxon_ids=(y.id, z.id),
        )
        # C: X, H: Noise -> misattribution
        _recording_with_claims(
            session, audio_hash="e" * 64, classifier_taxon_id=x.id,
            manual_verdict=Verdict.NOISE,
        )
        # C: X, H: NoID -> ignored entirely
        _recording_with_claims(
            session, audio_hash="f" * 64, classifier_taxon_id=x.id,
            manual_verdict=Verdict.NO_ID,
        )
        session.commit()

        rates = _misattribution_rates(session)

    # 3 misattributions (b, d, e) out of 5 counted recordings (a, b, c, d, e)
    # -- f is excluded entirely, matching the "NO_ID ignored" rule.
    assert rates[(IdSource.EMT_GUANO, x.id)] == (3, 5)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `hatch test tests/test_review_flags.py -v` (both marker sets)
Expected: FAIL — `_misattribution_rates`/`_misattribution_reason` don't exist yet.

- [ ] **Step 3: Write the implementation**

Append to `src/fledermap/services/review_flags.py`:

```python
from fledermap.domain.codes import IdSource, Verdict

# Minimum disagreement count before a source/taxon pair is ever flagged --
# avoids treating one correction as proof of a pattern.
MISATTRIBUTION_MIN_DISAGREEMENTS = 3


def _misattribution_rates(
    session: OrmSession,
) -> dict[tuple[IdSource, int], tuple[int, int]]:
    """(source, taxon_id) -> (misattribution_count, total_counted) across
    every non-missing recording that has both a claim of that taxon from
    that source and SOME standing MANUAL verdict.

    Per-recording accounting (spec's Goals section has the full table):
    - MANUAL SPECIES whose taxon set contains the classifier's taxon:
      correct (not counted as a disagreement) -- a classifier only ever
      names one species, so a broader human multi-species call still
      confirms it.
    - MANUAL SPECIES whose taxon set does NOT contain it, or MANUAL NOISE:
      counted as a disagreement.
    - MANUAL NO_ID: excluded entirely, from both numerator and denominator
      -- a human declining to call it isn't evidence the classifier was
      wrong.

    Deliberately no taxonomic-hierarchy awareness: a manual genus/group
    claim (e.g. Myotis/MYSP) and an automatic species-level claim underneath
    it (e.g. Myotis daubentonii) are compared by plain taxon_id equality,
    same as any other pair -- see the design spec's Non-goals section for
    why a Taxon.parent_id walk isn't worth it here.
    """
    recordings = session.scalars(
        select(Recording).where(Recording.missing_since.is_(None)),
    ).all()
    totals: dict[tuple[IdSource, int], int] = {}
    disagreements: dict[tuple[IdSource, int], int] = {}
    for r in recordings:
        manual_claims = [i for i in r.identifications if i.source == IdSource.MANUAL]
        if not manual_claims:
            continue
        manual_verdict = manual_claims[0].verdict
        if manual_verdict == Verdict.NO_ID:
            continue
        manual_taxon_ids = frozenset(
            i.taxon_id for i in manual_claims if i.taxon_id is not None
        )
        for ident in r.identifications:
            if ident.source == IdSource.MANUAL or ident.taxon_id is None:
                continue
            key = (ident.source, ident.taxon_id)
            totals[key] = totals.get(key, 0) + 1
            is_correct = (
                manual_verdict == Verdict.SPECIES
                and ident.taxon_id in manual_taxon_ids
            )
            if not is_correct:
                disagreements[key] = disagreements.get(key, 0) + 1
    return {
        key: (disagreements.get(key, 0), total) for key, total in totals.items()
    }


def _misattribution_reason(
    rates: dict[tuple[IdSource, int], tuple[int, int]],
    source: IdSource,
    taxon_id: int,
    taxon_label: str,
) -> str | None:
    disagreements, total = rates.get((source, taxon_id), (0, 0))
    if (
        disagreements >= MISATTRIBUTION_MIN_DISAGREEMENTS
        and disagreements * 2 > total
    ):
        return f"{source.value} is often wrong about {taxon_label}"
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `hatch test tests/test_review_flags.py -v` (both marker sets, `db` ones with
`dangerouslyDisableSandbox: true`)
Expected: PASS

- [ ] **Step 5: Type-check**

Run: `hatch run types:check`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/fledermap/services/review_flags.py tests/test_review_flags.py
git commit -m "feat: add misattribution criterion for flagged-for-review"
```

---

## Task 4: `review_reasons` + `ReviewContext` — compose and gate

**Files:**
- Modify: `src/fledermap/services/review_flags.py`
- Test: `tests/test_review_flags.py`

**Interfaces:**
- Consumes: `current_manual_state` (`services/manual_classification.py`);
  `current_best_identification`, `CurrentIdentification` (`services/current_best.py`); `Verdict`
  (`domain/codes.py`); Task 2/3's `_taxon_counts`, `_rarity_reason`, `_misattribution_rates`,
  `_misattribution_reason`.
- Produces:
  `ReviewContext` dataclass with `.build(session) -> ReviewContext` classmethod, consumed by
  Task 5 (`map_query.py`) and Task 9 (Reviews page).
  `review_reasons(recording: Recording, context: ReviewContext) -> list[str]`, consumed by
  Task 5, Task 7 (templates), and Task 9.

- [ ] **Step 1: Write the failing tests**

```python
# appended to tests/test_review_flags.py
from fledermap.services.review_flags import ReviewContext, review_reasons


def test_review_reasons_empty_when_not_species_verdict(engine: Engine) -> None:
    with OrmSession(engine) as session:
        r = Recording(
            audio_hash="a" * 64, path="a.wav",
            recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
        )
        session.add(r)
        session.flush()
        session.add(
            Identification(
                recording_id=r.id, source=IdSource.EMT_GUANO,
                verdict=Verdict.NOISE, taxon_id=None, first_seen_at=r.recorded_at,
            ),
        )
        session.commit()
        context = ReviewContext.build(session)

        assert review_reasons(r, context) == []


def test_review_reasons_empty_when_manually_classified(engine: Engine) -> None:
    with OrmSession(engine) as session:
        taxon = Taxon(rank="species", scientific_name="Pipistrellus pipistrellus")
        session.add(taxon)
        session.flush()
        r = Recording(
            audio_hash="a" * 64, path="a.wav",
            recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
        )
        session.add(r)
        session.flush()
        session.add_all([
            Identification(
                recording_id=r.id, source=IdSource.EMT_GUANO,
                verdict=Verdict.SPECIES, taxon_id=taxon.id, first_seen_at=r.recorded_at,
            ),
            Identification(
                recording_id=r.id, source=IdSource.MANUAL,
                verdict=Verdict.SPECIES, taxon_id=taxon.id, first_seen_at=r.recorded_at,
            ),
        ])
        session.commit()
        context = ReviewContext.build(session)

        # Already reviewed by a human -- a rare-species match must not fire.
        assert review_reasons(r, context) == []


def test_review_reasons_includes_rarity_match(engine: Engine) -> None:
    with OrmSession(engine) as session:
        taxon = Taxon(rank="species", scientific_name="Pipistrellus pipistrellus")
        session.add(taxon)
        session.flush()
        r = Recording(
            audio_hash="a" * 64, path="a.wav",
            recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
        )
        session.add(r)
        session.flush()
        session.add(
            Identification(
                recording_id=r.id, source=IdSource.EMT_GUANO,
                verdict=Verdict.SPECIES, taxon_id=taxon.id, first_seen_at=r.recorded_at,
            ),
        )
        session.commit()
        context = ReviewContext.build(session)

        reasons = review_reasons(r, context)

    assert len(reasons) == 1
    assert "Pipistrellus pipistrellus" in reasons[0]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `hatch test tests/test_review_flags.py -m db -v` (`dangerouslyDisableSandbox: true`)
Expected: FAIL — `ReviewContext`/`review_reasons` don't exist yet.

- [ ] **Step 3: Write the implementation**

Append to `src/fledermap/services/review_flags.py`:

```python
from dataclasses import dataclass

from fledermap.services.current_best import current_best_identification
from fledermap.services.manual_classification import current_manual_state
from fledermap.store.models import Taxon


@dataclass(frozen=True)
class ReviewContext:
    """Aggregates shared across every `review_reasons` call in one request --
    computing these per-recording would be an N+1 query pattern across a
    whole filtered set (the Reviews page, the map's `needs_review` filter).
    Build once per request, pass into every `review_reasons` call."""

    dataset_counts: dict[int, int]
    site_counts: dict[tuple[int, int], int]
    misattribution_rates: dict[tuple[IdSource, int], tuple[int, int]]

    @classmethod
    def build(cls, session: OrmSession) -> ReviewContext:
        dataset_counts, site_counts = _taxon_counts(session)
        return cls(
            dataset_counts=dataset_counts,
            site_counts=site_counts,
            misattribution_rates=_misattribution_rates(session),
        )


def _taxon_label(session: OrmSession, taxon_id: int) -> str:
    taxon = session.get(Taxon, taxon_id)
    return taxon.scientific_name if taxon is not None else f"taxon #{taxon_id}"


def review_reasons(recording: Recording, context: ReviewContext) -> list[str]:
    """Every computed reason this recording's assigned species should be
    reviewed -- empty if none apply. Scoped to a SPECIES verdict with no
    standing MANUAL claim (a human classifying it already counts as
    reviewed); see the module docstring and the design spec's Goals section
    for why nothing here is stored or dismissible."""
    best = current_best_identification(recording)
    if best is None or best.verdict != Verdict.SPECIES:
        return []
    manual_verdict, _manual_taxon_ids = current_manual_state(recording)
    if manual_verdict is not None:
        return []

    reasons: list[str] = []
    for claim in best.claims:
        if claim.taxon_id is None:
            continue
        label = claim.taxon.scientific_name if claim.taxon else f"taxon #{claim.taxon_id}"
        rarity = _rarity_reason(
            context.dataset_counts, context.site_counts,
            recording.site_id, claim.taxon_id, label,
        )
        if rarity is not None:
            reasons.append(rarity)
        misattribution = _misattribution_reason(
            context.misattribution_rates, claim.source, claim.taxon_id, label,
        )
        if misattribution is not None:
            reasons.append(misattribution)
    return reasons
```

Remove the now-unused standalone `_taxon_label` helper if `claim.taxon.scientific_name` inline is
used instead (it is, above) — don't leave dead code.

- [ ] **Step 4: Run tests to verify they pass**

Run: `hatch test tests/test_review_flags.py -v` (both marker sets, `db` with
`dangerouslyDisableSandbox: true`)
Expected: PASS

- [ ] **Step 5: Type-check and full fast suite**

Run: `hatch run types:check` and `hatch test -m "not db"`
Expected: both PASS

- [ ] **Step 6: Commit**

```bash
git add src/fledermap/services/review_flags.py tests/test_review_flags.py
git commit -m "feat: compose review_reasons from rarity and misattribution criteria"
```

---

## Task 5: `needs_review` filter in `map_query.filtered_recordings`

**Files:**
- Modify: `src/fledermap/services/map_query.py`
- Test: `tests/test_map_query.py`

**Interfaces:**
- Consumes: Task 4's `ReviewContext`, `review_reasons`.
- Produces: `filtered_recordings(..., needs_review: bool = False)` — when `True`, keeps only
  recordings where `Recording.flagged_for_review` is set OR `review_reasons(...)` is non-empty.
  Consumed by Task 6 (route), Task 8 (map filter bar / GeoJSON API), Task 9 (Reviews page), Task
  10 (details-page prev/next).

- [ ] **Step 1: Write the failing tests**

```python
# appended to tests/test_map_query.py
def test_needs_review_keeps_manually_flagged_recordings(engine: Engine) -> None:
    from fledermap.store.models import Recording as RecordingModel

    with OrmSession(engine) as session:
        flagged = _recording(session, audio_hash="a" * 64, verdict=None)
        session.get(RecordingModel, flagged.id).flagged_for_review = True
        plain = _recording(session, audio_hash="b" * 64, verdict=None)
        session.commit()

        results = filtered_recordings(session, needs_review=True, verdict="all")

    assert [r.id for r in results] == [flagged.id]


def test_needs_review_keeps_computed_rarity_matches(engine: Engine) -> None:
    with OrmSession(engine) as session:
        taxon = Taxon(rank="species", scientific_name="Pipistrellus pipistrellus")
        session.add(taxon)
        session.flush()
        rare = _recording(session, audio_hash="a" * 64, taxon_id=taxon.id)
        common_taxon = Taxon(rank="species", scientific_name="Eptesicus serotinus")
        session.add(common_taxon)
        session.flush()
        for i in range(10):
            _recording(
                session, audio_hash=f"{i:064x}", taxon_id=common_taxon.id,
                recorded_at=datetime(2026, 8, 20 + i % 5, tzinfo=UTC),
            )
        session.commit()

        results = filtered_recordings(session, needs_review=True)

    assert rare.id in {r.id for r in results}
    assert not any(r.id == r2.id for r in results for r2 in [])  # placeholder removed below
```

Replace that last placeholder assertion — it was left in accidentally while drafting; use this
instead:

```python
    result_ids = {r.id for r in results}
    assert rare.id in result_ids
    # the common taxon's 10 recordings must NOT show up as needing review
    common_ids = {
        r.id for r in filtered_recordings(session, verdict="all")
        if r.id != rare.id
    }
    assert result_ids.isdisjoint(common_ids)
```

(Fold this into the test body above rather than leaving two separate blocks — the second snippet
replaces the last two lines of the first.)

```python
def test_needs_review_false_has_no_effect(engine: Engine) -> None:
    with OrmSession(engine) as session:
        plain = _recording(session, audio_hash="a" * 64)
        session.commit()

        results = filtered_recordings(session, needs_review=False)

    assert plain.id in {r.id for r in results}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `hatch test tests/test_map_query.py -m db -v -k needs_review`
(`dangerouslyDisableSandbox: true`)
Expected: FAIL — `filtered_recordings` has no `needs_review` parameter yet.

- [ ] **Step 3: Write the implementation**

In `src/fledermap/services/map_query.py`, add the import and parameter:

```python
from fledermap.services.review_flags import ReviewContext, review_reasons
```

```python
def filtered_recordings(
    session: OrmSession,
    *,
    bbox: BBox | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    taxon_id: int | Literal["unmapped"] | None = None,
    taxon_exclude: bool = False,
    verdict: Verdict | Literal["all", "unidentified"] | None = None,
    session_id: int | None = None,
    site_id: int | None = None,
    source: IdSource | None = None,
    favourite_only: bool = False,
    needs_review: bool = False,
) -> Sequence[Recording]:
```

After the existing `favourite_only` SQL clause:

```python
    if favourite_only:
        stmt = stmt.where(Recording.favourite.is_(True))
```

leave that as-is, and add the `needs_review` post-fetch filter alongside the existing
verdict/taxon Python-side loop (it needs `ReviewContext`, an aggregate over ALL recordings, so it
can't be a per-row SQL predicate):

```python
    recordings = list(session.scalars(stmt).all())

    if bbox is not None:
        recordings = [r for r in recordings if _within_bbox(decode_point(r.geom), bbox)]

    review_context = ReviewContext.build(session) if needs_review else None

    results = []
    for r in recordings:
        best = current_best_identification(r)
        if not _passes_verdict_filter(best, verdict):
            continue
        if needs_review and review_context is not None:
            if not (r.flagged_for_review or review_reasons(r, review_context)):
                continue
        if taxon_id is not None:
            ...  # unchanged
```

(The `taxon_id` block below is unchanged — only the `needs_review` check is new, inserted before
it in the same loop.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `hatch test tests/test_map_query.py -m db -v -k needs_review`
(`dangerouslyDisableSandbox: true`)
Expected: PASS

- [ ] **Step 5: Run the full map_query test file and type-check**

Run: `hatch test tests/test_map_query.py -m db` (`dangerouslyDisableSandbox: true`) and
`hatch run types:check`
Expected: both PASS

- [ ] **Step 6: Commit**

```bash
git add src/fledermap/services/map_query.py tests/test_map_query.py
git commit -m "feat: add needs_review filter to filtered_recordings"
```

---

## Task 6: Manual flag toggle endpoint

**Files:**
- Modify: `src/fledermap/web/views/map.py`
- Test: `tests/test_web_map.py` (or the existing file covering `toggle_favourite` — confirm exact
  filename with `grep -rn "toggle_favourite" tests/`)

**Interfaces:**
- Consumes: `Recording.flagged_for_review` (Task 1).
- Produces: `POST /recordings/<audio_hash>/flag-for-review` (branches on `?panel=detail` exactly
  like `toggle_favourite`), consumed by Task 7's templates.

- [ ] **Step 1: Locate the existing `toggle_favourite` test**

Run: `grep -rn "toggle_favourite\|/favourite" tests/*.py`

Use whatever file that finds as the home for the new test (matching existing project layout
rather than guessing a name).

- [ ] **Step 2: Write the failing test**

Add alongside the existing favourite-toggle test, following its exact shape (read it first to
match fixtures/helpers used there):

```python
def test_toggle_flag_for_review_flips_the_flag(client, ...) -> None:  # match existing fixture args
    # Arrange a recording via whatever helper the favourite test already uses.
    ...
    response = client.post(f"/recordings/{audio_hash}/flag-for-review")
    assert response.status_code == 200
    # Assert the DB row's flagged_for_review is now True (re-fetch via session).
    ...
    response2 = client.post(f"/recordings/{audio_hash}/flag-for-review")
    # Assert it's back to False.
```

- [ ] **Step 3: Run test to verify it fails**

Run whatever command the existing favourite test uses (likely `hatch test tests/<file>.py -m db -v
-k flag_for_review`, `dangerouslyDisableSandbox: true`)
Expected: FAIL — 404, route doesn't exist.

- [ ] **Step 4: Write the implementation**

In `src/fledermap/web/views/map.py`, right after `toggle_favourite`:

```python
@views_bp.post("/recordings/<audio_hash>/flag-for-review")
def toggle_flag_for_review(audio_hash: str) -> flask.Response:
    engine = flask.current_app.config["ENGINE"]
    with OrmSession(engine) as session:
        recording = session.scalars(
            select(Recording).where(Recording.audio_hash == audio_hash),
        ).one_or_none()
        if recording is None:
            return flask.make_response(("Recording not found.", 404))
        recording.flagged_for_review = not recording.flagged_for_review
        session.commit()

        if flask.request.args.get("panel") == "detail":
            html = flask.render_template(
                "_detail_flag_button.html",
                recording=recording,
            )
            return flask.make_response(html)

    response, _point = _render_recording_panel(audio_hash)
    return response
```

(`_detail_flag_button.html` is created in Task 7; this route references it now so the two tasks'
diffs land together logically, but Task 6's own test only needs the drawer branch — the
`?panel=detail` path is exercised once Task 7 adds the template.)

- [ ] **Step 5: Run test to verify it passes**

Same command as Step 3.
Expected: PASS

- [ ] **Step 6: Type-check**

Run: `hatch run types:check`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/fledermap/web/views/map.py tests/
git commit -m "feat: add flag-for-review toggle endpoint"
```

---

## Task 7: Flag badge, reasons, and toggle button in drawer + details templates

**Files:**
- Modify: `src/fledermap/web/views/map.py` (`_render_recording_panel`, thread `review_reasons`
  through)
- Modify: `src/fledermap/web/views/recording_detail.py` (`recording_details_page`, same)
- Modify: `src/fledermap/web/templates/_recording_panel.html`
- Create: `src/fledermap/web/templates/_detail_flag_button.html`
- Modify: `src/fledermap/web/templates/recording_details.html`
- Test: headless-Chrome live-verification (CLAUDE.md JS mandate) — no `node:test` file, since this
  is template/DOM rendering, not pure JS logic.

**Interfaces:**
- Consumes: Task 4's `ReviewContext.build`, `review_reasons`; Task 6's toggle endpoint.
- Produces: both templates render a flag badge + reason list + toggle button whenever
  `recording.flagged_for_review or reasons` is truthy — the shape Task 9/10 rely on for visual
  parity.

- [ ] **Step 1: Thread reasons into the drawer panel route**

In `_render_recording_panel` (`web/views/map.py`), after `best = current_best_identification(recording)`:

```python
        from fledermap.services.review_flags import ReviewContext, review_reasons

        review_context = ReviewContext.build(session)
        reasons = review_reasons(recording, review_context)
```

(Move the import to the top of the file with the other `services` imports rather than inline —
inline shown here only to mark exactly where the new lines go.)

Add `reasons=reasons` to the `flask.render_template("_recording_panel.html", ...)` call's kwargs.

- [ ] **Step 2: Thread reasons into the details page route**

Same pattern in `recording_details_page` (`web/views/recording_detail.py`): import
`ReviewContext`/`review_reasons` from `fledermap.services.review_flags`, compute `reasons =
review_reasons(recording, ReviewContext.build(session))` right after `best =
current_best_identification(recording)`, and add `reasons=reasons` to the
`flask.render_template("recording_details.html", ...)` call.

- [ ] **Step 3: Add the badge/toggle to `_recording_panel.html`**

In the `.panel-header` block, right after the existing favourite-toggle `<button>`:

```html
  <button
    type="button"
    class="flag-toggle"
    hx-post="/recordings/{{ recording.audio_hash }}/flag-for-review?{{ filter_qs }}"
    hx-target="#drawer-body"
    aria-label="{{ 'Unflag for review' if recording.flagged_for_review else 'Flag for review' }}"
    aria-pressed="{{ 'true' if recording.flagged_for_review else 'false' }}"
  >{{ "🚩" if recording.flagged_for_review else "⚑" }}</button>
```

And, right after the closing `</div>` of `.panel-header`, a reasons list shown whenever there's
anything to show:

```html
{% if recording.flagged_for_review or reasons %}
<div class="review-reasons">
  <strong>Flagged for review</strong>
  {% if reasons %}
  <ul>
    {% for reason in reasons %}
    <li>{{ reason }}</li>
    {% endfor %}
  </ul>
  {% endif %}
</div>
{% endif %}
```

- [ ] **Step 4: Create `_detail_flag_button.html`**

Mirror `_detail_favourite_button.html` exactly:

```html
{# src/fledermap/web/templates/_detail_flag_button.html
   Standalone recording-detail page's flag-for-review toggle -- its own small
   fragment, same pattern as _detail_favourite_button.html, so
   toggle_flag_for_review's `?panel=detail` branch can swap just this button. #}
<button
  type="button"
  id="detail-flag"
  class="flag-toggle"
  hx-post="/recordings/{{ recording.audio_hash }}/flag-for-review?panel=detail"
  hx-target="#detail-flag"
  hx-swap="outerHTML"
  aria-label="{{ 'Unflag for review' if recording.flagged_for_review else 'Flag for review' }}"
  aria-pressed="{{ 'true' if recording.flagged_for_review else 'false' }}"
>{{ "🚩" if recording.flagged_for_review else "⚑" }}</button>
```

- [ ] **Step 5: Wire it into `recording_details.html`**

Near `{% set title_prefix %}{% include "_detail_favourite_button.html" %}{% endset %}`, add the
flag button and a reasons block:

```html
    {% set title_prefix %}{% include "_detail_favourite_button.html" %}{% include "_detail_flag_button.html" %}{% endset %}
```

And after `{% include "_entity_header.html" %}`:

```html
    {% if recording.flagged_for_review or reasons %}
    <div class="review-reasons">
      <strong>Flagged for review</strong>
      {% if reasons %}
      <ul>
        {% for reason in reasons %}
        <li>{{ reason }}</li>
        {% endfor %}
      </ul>
      {% endif %}
    </div>
    {% endif %}
```

- [ ] **Step 6: Add minimal CSS for `.flag-toggle`/`.review-reasons`**

Check `src/fledermap/web/static/app.css` for the existing `.favourite-toggle` rule and add a
sibling `.flag-toggle` rule with the same button reset, plus a simple `.review-reasons` block
style (small font, a subtle warning-colored left border) — match this project's existing color
token usage (`fledermap-style-guide` skill covers the convention; consult it before hand-picking
colors).

- [ ] **Step 7: Headless-Chrome live-verification**

Per CLAUDE.md's JS tooling section, this is mandatory for any DOM-touching change. Load the memory
`reference-headless-chrome-live-verification-technique` for the exact driving technique, then:
open the map, click into a recording known to have a rare-species match (from real or seeded
data), confirm the flag badge/reasons render, click the flag toggle, confirm it flips and persists
across a panel reload, then repeat on that recording's standalone details page and confirm the
same state is shown (parity).

- [ ] **Step 8: Run the fast test suite and type-check**

Run: `hatch test -m "not db"` and `hatch run types:check`
Expected: both PASS

- [ ] **Step 9: Commit**

```bash
git add src/fledermap/web/views/map.py src/fledermap/web/views/recording_detail.py \
        src/fledermap/web/templates/_recording_panel.html \
        src/fledermap/web/templates/_detail_flag_button.html \
        src/fledermap/web/templates/recording_details.html \
        src/fledermap/web/static/app.css
git commit -m "feat: show flag badge, reasons, and toggle on drawer and details page"
```

---

## Task 8: Map filter bar `needs_review_only` checkbox + GeoJSON API

**Files:**
- Modify: `src/fledermap/web/api/geojson.py`
- Modify: `src/fledermap/web/templates/map.html`
- Modify: `src/fledermap/web/static/app.js`
- Test: `tests/test_geojson_api.py` (confirm exact filename via `grep -rn "favourite_only"
  tests/*.py`); headless-Chrome live-verification for the checkbox itself.

**Interfaces:**
- Consumes: Task 5's `filtered_recordings(..., needs_review=...)`.
- Produces: `needs_review_only` query param recognized end-to-end (checkbox → `app.js` → GeoJSON
  API → `filtered_recordings`), and by the drawer panel route (`_render_recording_panel` already
  reads `flask.request.args` generically via the panel/prev-next flow — extend its explicit
  parameter list the same way `favourite_only` is read there).

- [ ] **Step 1: Write the failing API test**

Following the exact shape of the existing `favourite_only` GeoJSON API test (read it first):

```python
def test_recordings_geojson_needs_review_only_filters(client, ...) -> None:
    # Arrange one flagged_for_review=True recording and one plain one via
    # whatever helper the favourite_only test already uses.
    ...
    response = client.get("/recordings.geojson?needs_review_only=1")
    ids = {f["properties"]["id"] for f in response.json["features"]}
    assert ids == {flagged_id}
```

- [ ] **Step 2: Run test to verify it fails**

Run the same command style used for the existing `favourite_only` API test, with
`dangerouslyDisableSandbox: true`.
Expected: FAIL — param not read yet.

- [ ] **Step 3: Wire the param into the GeoJSON API**

In `src/fledermap/web/api/geojson.py`'s `recordings_geojson`, alongside the existing
`favourite_only = parse_bool(...)` line:

```python
        needs_review_only = parse_bool(flask.request.args.get("needs_review_only"))
```

and pass `needs_review=needs_review_only` into the `filtered_recordings(...)` call.

- [ ] **Step 4: Wire the same param into the drawer panel route**

In `_render_recording_panel` (`web/views/map.py`), alongside the existing `favourite_only =
parse_bool(...)` line:

```python
        needs_review_only = parse_bool(flask.request.args.get("needs_review_only"))
```

and pass `needs_review=needs_review_only` into that function's `filtered_recordings(...)` call
too — so prev/next inside the drawer stays consistent with whatever the map's checkbox currently
shows.

- [ ] **Step 5: Run test to verify it passes**

Same command as Step 2.
Expected: PASS

- [ ] **Step 6: Add the checkbox to `map.html`**

Right next to the existing `favourite_only` checkbox (`map.html:62`):

```html
        <label><input type="checkbox" name="needs_review_only" value="1" x-model="needs_review_only"> Needs review</label>
```

(Match whatever surrounding markup/label convention the `favourite_only` checkbox actually uses —
read the few lines around it first rather than assuming a bare `<input>`.)

- [ ] **Step 7: Wire it into `app.js`'s filter-state parsing**

Alongside `app.js:30`'s `favourite_only: params.get("favourite_only") === "1",` inside the same
Alpine `x-data` object:

```javascript
    needs_review_only: params.get("needs_review_only") === "1",
```

And in the `query()` function (`app.js:62-84`) wherever `favourite_only` is appended to the
outgoing `FormData`/query params, add the same handling for `needs_review_only` (read that
function's exact shape first — it likely only appends the param when truthy, matching
`favourite_only`'s pattern).

- [ ] **Step 8: Headless-Chrome live-verification**

Per CLAUDE.md's JS mandate: load the map, toggle "Needs review" on, confirm only flagged/computed
recordings remain plotted (compare feature count against the `/reviews` page's count once Task 9
exists — for now, confirm against a manually-seeded flagged recording), toggle off, confirm the
full set returns, and confirm the URL query string round-trips through a page reload (`buildUrl`/
`popstate` per `app.js`'s documented URL-sync scheme).

- [ ] **Step 9: Run the fast test suite and type-check**

Run: `hatch test -m "not db"` and `hatch run types:check`
Expected: both PASS

- [ ] **Step 10: Commit**

```bash
git add src/fledermap/web/api/geojson.py src/fledermap/web/views/map.py \
        src/fledermap/web/templates/map.html src/fledermap/web/static/app.js tests/
git commit -m "feat: add needs_review_only filter to the map"
```

---

## Task 9: Reviews page (nav item, blueprint, list + start-review entry point)

**Files:**
- Create: `src/fledermap/web/views/reviews.py`
- Create: `src/fledermap/web/templates/reviews.html`
- Modify: `src/fledermap/web/templates/_nav.html`
- Modify: `src/fledermap/web/app.py` (register the new blueprint)
- Test: `tests/test_reviews.py`

**Interfaces:**
- Consumes: Task 5's `filtered_recordings(..., needs_review=True)`, Task 4's `ReviewContext`/
  `review_reasons` (for the per-row reason display), `current_best_identification`
  (`services/current_best.py`).
- Produces: `GET /reviews`, rendering `reviews.html` with `count: int`, `rows: list[dict]` (one per
  flagged recording: `audio_hash`, `site_label`, `recorded_at`, `species_label`, `reasons`), and
  `first_audio_hash: str | None` for the "Start reviewing" link's target. Consumed by Task 10 (the
  details page's "no more flagged recordings" link target, and the query-string convention this
  page's links establish).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_reviews.py
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import IdSource, Verdict
from fledermap.store.models import Identification, Recording, Taxon
from fledermap.web.app import create_app

pytestmark = pytest.mark.db


def test_reviews_page_lists_flagged_recordings(engine: Engine, tmp_path) -> None:
    with OrmSession(engine) as session:
        taxon = Taxon(rank="species", scientific_name="Pipistrellus pipistrellus")
        session.add(taxon)
        session.flush()
        r = Recording(
            audio_hash="a" * 64, path="a.wav",
            recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
        )
        session.add(r)
        session.flush()
        session.add(
            Identification(
                recording_id=r.id, source=IdSource.EMT_GUANO,
                verdict=Verdict.SPECIES, taxon_id=taxon.id, first_seen_at=r.recorded_at,
            ),
        )
        session.commit()

    app = create_app(engine=engine, media_root=tmp_path)  # match whatever create_app's
                                                            # actual signature/fixture is --
                                                            # check tests/conftest.py or an
                                                            # existing view test for the
                                                            # real construction pattern
    client = app.test_client()
    response = client.get("/reviews")

    assert response.status_code == 200
    assert b"Pipistrellus pipistrellus" in response.data
    assert b"a" * 64 in response.data or b"aaaaaaaa" in response.data


def test_reviews_page_zero_flagged(engine: Engine, tmp_path) -> None:
    app = create_app(engine=engine, media_root=tmp_path)
    client = app.test_client()
    response = client.get("/reviews")

    assert response.status_code == 200
    assert b"0" in response.data
```

Before writing these, run `grep -rn "def client\|test_client\|create_app" tests/conftest.py
tests/test_web_map.py` to find the project's actual Flask test-client fixture/pattern and rewrite
the above to match it exactly rather than guessing `create_app`'s signature.

- [ ] **Step 2: Run test to verify it fails**

Run: `hatch test tests/test_reviews.py -m db -v` (`dangerouslyDisableSandbox: true`)
Expected: FAIL — `/reviews` doesn't exist (404).

- [ ] **Step 3: Write the route**

```python
# src/fledermap/web/views/reviews.py
"""The Reviews page (docs/superpowers/specs/2026-09-09-fledermap-flagged-
for-review-design.md): the dedicated entry point into the flagged-for-review
workflow -- a count + "Start reviewing" link into the first flagged
recording's details page, plus a table to jump into any individual one
directly."""

from __future__ import annotations

import flask
from sqlalchemy.orm import Session as OrmSession

from fledermap.services.current_best import current_best_identification
from fledermap.services.map_query import filtered_recordings
from fledermap.services.review_flags import ReviewContext, review_reasons
from fledermap.store.geo import decode_point
from fledermap.store.models import Site
from fledermap.web.params import fallback_site_label

reviews_bp = flask.Blueprint("reviews", __name__, template_folder="../templates")


@reviews_bp.get("/reviews")
def reviews_page() -> flask.Response:
    engine = flask.current_app.config["ENGINE"]
    with OrmSession(engine) as session:
        recordings = filtered_recordings(session, needs_review=True)
        context = ReviewContext.build(session)

        rows = []
        for r in recordings:
            best = current_best_identification(r)
            species_label = (
                best.primary.taxon.scientific_name
                if best is not None and not best.is_multi and best.primary.taxon
                else "unmapped species"
            )
            site = session.get(Site, r.site_id) if r.site_id else None
            site_label = (
                site.name if site and site.name
                else fallback_site_label(decode_point(site.centroid)) if site
                else None
            )
            reasons = review_reasons(r, context)
            if r.flagged_for_review:
                reasons = [*reasons, "manually flagged"]
            rows.append(
                {
                    "audio_hash": r.audio_hash,
                    "site_label": site_label,
                    "recorded_at": r.recorded_at,
                    "species_label": species_label,
                    "reasons": reasons,
                },
            )

        html = flask.render_template(
            "reviews.html",
            count=len(rows),
            rows=rows,
            first_audio_hash=rows[0]["audio_hash"] if rows else None,
        )
    return flask.make_response(html)
```

- [ ] **Step 4: Write the template**

```html
{# src/fledermap/web/templates/reviews.html #}
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  {% include "_theme_init.html" %}
  {% include "_favicon.html" %}
  <title>Fledermap — Reviews</title>
  <link rel="stylesheet" href="{{ url_for('static', filename='app.css') }}">
</head>
<body>
  {% include "_nav.html" %}
  <main class="main-content">
    <h1>Reviews</h1>
    <p>{{ count }} recording{{ "" if count == 1 else "s" }} flagged for review.</p>
    {% if first_audio_hash %}
    <a class="button" href="/recordings/{{ first_audio_hash }}?needs_review_only=1">Start reviewing ({{ count }})</a>
    {% endif %}
    {% if rows %}
    <p>...or choose one below:</p>
    <table>
      <thead>
        <tr><th>Site</th><th>Time</th><th>Species</th><th>Reasons</th></tr>
      </thead>
      <tbody>
        {% for row in rows %}
        <tr>
          <td>{{ row.site_label or "No site" }}</td>
          <td>{{ row.recorded_at.strftime('%Y-%m-%d %H:%M') }}</td>
          <td><a href="/recordings/{{ row.audio_hash }}?needs_review_only=1">{{ row.species_label }}</a></td>
          <td>{{ row.reasons | join(", ") }}</td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
    {% endif %}
  </main>
</body>
</html>
```

- [ ] **Step 5: Register the blueprint and nav item**

In `src/fledermap/web/app.py`, alongside the existing blueprint registrations:

```python
    app.register_blueprint(reviews_bp)
```

(add the corresponding import at the top of the file, matching the existing import style for
`statistics_bp` etc.)

In `src/fledermap/web/templates/_nav.html`, add after the `Statistics` link:

```html
  <a class="sidebar-link" href="/reviews"><span class="label">Reviews</span></a>
```

- [ ] **Step 6: Run test to verify it passes**

Run: `hatch test tests/test_reviews.py -m db -v` (`dangerouslyDisableSandbox: true`)
Expected: PASS

- [ ] **Step 7: Headless-Chrome live-verification**

Confirm the nav item appears and links to `/reviews`, the page renders the count/table correctly
against seeded data, and "Start reviewing" navigates to the first flagged recording's details page
with `needs_review_only=1` in the URL.

- [ ] **Step 8: Run the fast test suite and type-check**

Run: `hatch test -m "not db"` and `hatch run types:check`
Expected: both PASS

- [ ] **Step 9: Commit**

```bash
git add src/fledermap/web/views/reviews.py src/fledermap/web/templates/reviews.html \
        src/fledermap/web/templates/_nav.html src/fledermap/web/app.py tests/test_reviews.py
git commit -m "feat: add Reviews page"
```

---

## Task 10: Details-page prev/next + review-mode banner

**Files:**
- Modify: `src/fledermap/web/views/recording_detail.py`
- Modify: `src/fledermap/web/templates/recording_details.html`
- Test: `tests/test_recording_detail.py` (confirm exact filename via `grep -rln
  "recording_details_page" tests/*.py`)

**Interfaces:**
- Consumes: Task 5's `filtered_recordings`/`neighbor_recordings` (`services/map_query.py`);
  Task 9's `/reviews` as the "no more flagged recordings" link target.
- Produces: `recording_details_page` reads the same filter query-string params
  `_render_recording_panel` already reads, computes `previous`/`next` via `neighbor_recordings`
  when any such param is present, and passes `previous`, `next`, `filter_qs`, and
  `is_review_session: bool` (True iff `needs_review_only` was in the query string) plus
  `review_position: tuple[int, int] | None` (`(index, total)`, 1-based) to the template.

- [ ] **Step 1: Write the failing tests**

```python
# appended to tests/test_recording_detail.py (or wherever the existing route
# tests for this page live -- confirm filename first)
def test_details_page_has_no_prevnext_with_no_filter_context(client_and_engine) -> None:
    # Arrange two plain recordings via whatever helper this test file already uses.
    ...
    response = client.get(f"/recordings/{audio_hash}")
    assert b"Previous" not in response.data
    assert b"Next" not in response.data


def test_details_page_shows_prevnext_with_filter_context(client_and_engine) -> None:
    # Arrange two recordings sharing a session_id, matching `_recording`'s
    # session_id kwarg used elsewhere.
    ...
    response = client.get(f"/recordings/{first_hash}?session={session_id}")
    assert b"Next" in response.data


def test_details_page_shows_review_banner_for_needs_review_navigation(
    client_and_engine,
) -> None:
    # Arrange one flagged recording.
    ...
    response = client.get(f"/recordings/{audio_hash}?needs_review_only=1")
    assert b"Reviewing flagged recordings" in response.data
    assert b"1 of 1" in response.data or b"1 of" in response.data


def test_details_page_no_review_banner_for_ordinary_filtered_navigation(
    client_and_engine,
) -> None:
    # Arrange two recordings sharing a session_id.
    ...
    response = client.get(f"/recordings/{first_hash}?session={session_id}")
    assert b"Reviewing flagged recordings" not in response.data
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `hatch test tests/test_recording_detail.py -m db -v -k "prevnext or review_banner"`
(`dangerouslyDisableSandbox: true`)
Expected: FAIL — no prev/next or banner logic exists yet on this page.

- [ ] **Step 3: Write the implementation**

In `src/fledermap/web/views/recording_detail.py`, add imports:

```python
from fledermap.services.map_query import filtered_recordings, neighbor_recordings
from fledermap.web.params import (
    parse_bool,
    parse_datetime,
    parse_int,
    parse_taxon_filter,
    parse_verdict,
)
```

Inside `recording_details_page`, after the recording is loaded and before rendering, compute the
filter-scoped neighbors — but only when at least one recognized filter param is present in the
query string (matching the "no filter context, no prev/next" non-goal):

```python
        filter_qs = flask.request.query_string.decode()
        previous = following = None
        is_review_session = False
        review_position: tuple[int, int] | None = None
        if filter_qs:
            date_from = parse_datetime(flask.request.args.get("from"))
            date_to = parse_datetime(flask.request.args.get("to"), end_of_day=True)
            taxon_id = parse_taxon_filter(flask.request.args.get("taxon"))
            taxon_exclude = parse_bool(flask.request.args.get("taxon_exclude"))
            verdict = parse_verdict(flask.request.args.get("verdict"))
            session_id = parse_int(flask.request.args.get("session"))
            site_id = parse_int(flask.request.args.get("site"))
            source_raw = flask.request.args.get("source")
            source = IdSource(source_raw) if source_raw else None
            favourite_only = parse_bool(flask.request.args.get("favourite_only"))
            needs_review_only = parse_bool(flask.request.args.get("needs_review_only"))

            filtered = filtered_recordings(
                session,
                date_from=date_from,
                date_to=date_to,
                taxon_id=taxon_id,
                taxon_exclude=taxon_exclude,
                verdict=verdict,
                session_id=session_id,
                site_id=site_id,
                source=source,
                favourite_only=favourite_only,
                needs_review=needs_review_only,
            )
            neighbors = neighbor_recordings(filtered, audio_hash)
            if neighbors is not None:
                previous, following = neighbors
                is_review_session = needs_review_only
                if is_review_session:
                    ordered = sorted(filtered, key=lambda r: r.recorded_at)
                    index = next(
                        i for i, r in enumerate(ordered) if r.audio_hash == audio_hash
                    )
                    review_position = (index + 1, len(ordered))
```

Add the needed `IdSource` import at the top of the file (`from fledermap.domain.codes import
IdSource`) if not already present — check first, `recording_detail.py`'s current imports don't
include it.

Add `previous`, `next=following`, `filter_qs`, `is_review_session`, `review_position` to the
`flask.render_template("recording_details.html", ...)` call's kwargs.

- [ ] **Step 4: Add prev/next and the review banner to `recording_details.html`**

Near the top of `<main>`, right after `{% include "_entity_header.html" %}` (and after the review
reasons block added in Task 7, so the banner reads as "here's why, here's where you are in the
queue"):

```html
    {% if is_review_session and review_position %}
    <div class="review-banner">
      <strong>Reviewing flagged recordings</strong>
      <span>{{ review_position[0] }} of {{ review_position[1] }}</span>
      <a href="/reviews">Exit review</a>
    </div>
    {% endif %}
    {% if previous or next %}
    <nav class="detail-prevnext">
      {% if previous %}
      <a href="/recordings/{{ previous.audio_hash }}?{{ filter_qs }}">← Previous</a>
      {% endif %}
      {% if next %}
      <a href="/recordings/{{ next.audio_hash }}?{{ filter_qs }}">Next →</a>
      {% endif %}
    </nav>
    {% elif is_review_session %}
    <p class="review-banner">No more flagged recordings. <a href="/reviews">Back to Reviews</a></p>
    {% endif %}
```

- [ ] **Step 5: Add minimal CSS**

In `app.css`, add `.detail-prevnext` and `.review-banner` rules consistent with the project's
existing color tokens (check `fledermap-style-guide` skill before hand-picking colors) — a
distinct background/border for `.review-banner` so it visually reads as different from an ordinary
prev/next row, per the spec's explicit requirement that review-mode navigation looks different
from an ordinary filtered browse.

- [ ] **Step 6: Run tests to verify they pass**

Run: `hatch test tests/test_recording_detail.py -m db -v` (`dangerouslyDisableSandbox: true`)
Expected: PASS

- [ ] **Step 7: Headless-Chrome live-verification**

Confirm: a bare link to a details page shows no prev/next; a session-filtered link shows plain
prev/next with no banner; a `needs_review_only=1` link (e.g. from the Reviews page's "Start
reviewing" button) shows the distinct banner with correct position, and clicking Next advances
through the flagged set; reaching the end shows "No more flagged recordings" with a working link
back to `/reviews`.

- [ ] **Step 8: Run the full fast test suite and type-check**

Run: `hatch test -m "not db"` and `hatch run types:check`
Expected: both PASS

- [ ] **Step 9: Commit**

```bash
git add src/fledermap/web/views/recording_detail.py \
        src/fledermap/web/templates/recording_details.html \
        src/fledermap/web/static/app.css tests/
git commit -m "feat: add prev/next and review-mode banner to recording details page"
```

---

## Task 11: Full suite verification and manual smoke test

**Files:** none (verification-only task).

- [ ] **Step 1: Run the complete test suite**

Run: `hatch test` (includes `db`-marked tests, `dangerouslyDisableSandbox: true`)
Expected: PASS, zero warnings (CLAUDE.md: "a warning is a defect").

- [ ] **Step 2: Run mypy and ruff**

Run: `hatch run types:check` and `hatch fmt`
Expected: both clean.

- [ ] **Step 3: Run the JS test suite**

Run: `node --test tests/js/`
Expected: PASS (no new pure-logic JS was introduced by this plan, so this should be unchanged from
before this work started — confirms nothing broke).

- [ ] **Step 4: End-to-end manual walkthrough against real/seeded data**

Per superpowers:verification-before-completion — don't just trust the per-task headless-Chrome
passes in isolation. With `hatch run fledermap serve` running against real data (or the bundled
sample recordings, noting their known non-representativeness per CLAUDE.md's "Sample data"
section): flag a recording manually from the map drawer, confirm it appears on `/reviews`, click
"Start reviewing", classify it via the existing classifier box, hit Next, confirm the
just-classified recording no longer reappears in a fresh `/reviews` visit (since a MANUAL claim
now excludes it — Goals section), and confirm the details page's plain prev/next (via a
session-filtered link) still works exactly as before this plan started.

- [ ] **Step 5: Update the Obsidian backlog**

Per the `feedback-ui-bugs-diagnose-live-then-batch-in-obsidian` memory's spirit (diagnose live,
record findings) — mark "Add 'flagged for review'" done in `~/Obsidian/Default/Fledermap.md`'s
"Improve data quality" section, noting the date and that criteria 1/2 (classifier disagreement,
likely-multi-species) remain deferred pending the batdetect2/noise classifier items.

- [ ] **Step 6: Final commit**

```bash
git add -A
git commit -m "chore: flagged-for-review feature complete"
```

(Only if Step 5's Obsidian edit or any leftover formatting fix from `hatch fmt` produced a diff —
otherwise there's nothing to commit here and this step is a no-op.)
