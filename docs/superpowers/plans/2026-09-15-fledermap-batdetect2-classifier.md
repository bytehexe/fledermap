# batdetect2 Classifier Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire batdetect2 in as an optional, off-by-default acoustic classifier: raw model output cached in a new `classifier_detection` table, `Identification` claims materialized from it against a configurable threshold every ingest cycle, and the whole feature gated behind an opt-in `pyproject.toml` extra because of its CC-BY-NC-4.0 license.

**Architecture:** A new async Procrastinate job (`classify_batdetect2_task`) runs batdetect2 once per recording and writes every candidate above a fixed low floor into `classifier_detection`. A separate, idempotent step re-run every `run_ingest_cycle` reads the current `FLEDERMAP_BATDETECT2_THRESHOLD`, filters those raw rows, and calls the existing `replace_claims` to keep `Identification` in sync — so threshold changes never require re-running the model. Enable/disable is likewise re-evaluated every cycle rather than tracked as a one-time transition: enabled means "enqueue what's missing, materialize," disabled means "purge this source's rows" — both idempotent, both self-healing against races.

**Tech Stack:** SQLAlchemy/Alembic (existing), Procrastinate (existing), `batdetect2` (new optional PyPI dependency, PyTorch-based).

**Spec:** `docs/superpowers/specs/2026-09-15-fledermap-batdetect2-classifier-design.md`

## Global Constraints

- `batdetect2` is CC-BY-NC-4.0-licensed (non-commercial use only) — it must be its own named `pyproject.toml` optional-dependencies group, never a base dependency, with a comment on that group stating the license restriction directly in `pyproject.toml`. Off by default (`FLEDERMAP_BATDETECT2_ENABLED` defaults to `false`).
- Never vendor batdetect2's code or weights into this repo or the built wheel.
- Every `jobs/tasks.py` import of anything from `batdetect2` must be a **local import inside the task function body**, never a top-level import in `jobs/tasks.py` — a missing optional install must only break `classify_batdetect2_task` itself when it actually runs, never crash the whole worker process on module load (which would break every other unrelated job).
- No config-time "is batdetect2 installed" check. Match this project's existing precedent for a configured-but-broken optional integration (`services/site_naming.py`'s `ensure_connected`): let the job fail naturally, retried by the existing `_RETRY = RetryStrategy(max_attempts=3, exponential_wait=2)` policy and logged by Procrastinate, no global disable, no UI surfacing. (Spec §5, §7.)
- Never hand-enter a species code. The exact `codes.batdetect2` label strings must come from actually loading the real checkpoint via `batdetect2.api`, not from research/memory (spec §3).
- `classifier_detection.source` and reuse of `IdSource` must have **no CHECK constraint**, mirroring `Identification.source` (spec D9 reasoning): further classifiers are coming and a CHECK would force a migration per one.
- `_PRECEDENCE` order (highest to lowest): `MANUAL, EMT_MANUAL, EMT_GUANO, EMT_WAMD, EMT_FILENAME, BATDETECT2, FLEDERMAP_NOISE`.
- All DB-touching tests are `@pytest.mark.db` and must be run with `dangerouslyDisableSandbox: true` (Docker-backed PostGIS testcontainer) — see CLAUDE.md "Environment gotchas." Run `hatch test -m "not db"` for everything else.
- Tests that actually import/call `batdetect2.api` must guard with `pytest.importorskip("batdetect2")` so the base test suite (which never installs the optional extra) still passes.

---

### Task 1: `pyproject.toml` — batdetect2 as an opt-in extra

**Files:**
- Modify: `pyproject.toml`

**Interfaces:**
- Produces: a `[project.optional-dependencies]` table with a `batdetect2` key; a `[tool.hatch.envs.batdetect2]` env for exercising it locally.

- [ ] **Step 1: Add the optional-dependencies group with an inline license comment**

Add after the existing `dependencies = [...]` block (before `[project.scripts]`):

```toml
# CC-BY-NC-4.0-licensed, non-commercial use only (https://pypi.org/project/batdetect2/).
# Deliberately NOT a base dependency: a plain `pip install fledermap` must never pull in a
# restrictively-licensed model. See docs/superpowers/specs/2026-09-15-fledermap-batdetect2-
# classifier-design.md §1. Enabling FLEDERMAP_BATDETECT2_ENABLED without this extra installed
# fails the classification job at run time (see jobs/tasks.py's classify_batdetect2_task) --
# it does not block the rest of the application.
[project.optional-dependencies]
batdetect2 = ["batdetect2"]
```

- [ ] **Step 2: Add a dedicated hatch env for local batdetect2 work**

Add near the other `[tool.hatch.envs.*]` blocks:

```toml
[tool.hatch.envs.batdetect2]
features = ["batdetect2"]
```

- [ ] **Step 3: Verify the extra installs cleanly**

Run: `hatch run batdetect2:python -c "import batdetect2; print(batdetect2.__file__)"`
Expected: prints a real file path, no error. (First run downloads torch/lightning/etc. — expect it to take a while.)

- [ ] **Step 4: Verify the base env is untouched**

Run: `hatch run python -c "import batdetect2"`
Expected: `ModuleNotFoundError: No module named 'batdetect2'` — confirms the base/default env never pulled it in.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml
git commit -m "build: add batdetect2 as an opt-in, off-by-default extra (CC-BY-NC-4.0)"
```

---

### Task 2: Config — `FLEDERMAP_BATDETECT2_ENABLED` / `FLEDERMAP_BATDETECT2_THRESHOLD`

**Files:**
- Modify: `src/fledermap/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `Config.batdetect2_enabled: bool` (default `False`), `Config.batdetect2_threshold: float` (default `0.8`), env vars `FLEDERMAP_BATDETECT2_ENABLED` / `FLEDERMAP_BATDETECT2_THRESHOLD`, TOML keys `batdetect2_enabled` / `batdetect2_threshold`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_config.py` (following the file's existing style — see `test_default_noise_snr_threshold_is_two`/`test_noise_snr_threshold_is_configurable`/`test_invalid_noise_snr_threshold_raises_config_error` for the pattern):

```python
def test_batdetect2_defaults_to_disabled_with_threshold_point_eight(
    monkeypatch: pytest.MonkeyPatch,
    minimal_env: dict[str, str],
) -> None:
    for key, value in minimal_env.items():
        monkeypatch.setenv(key, value)
    config = Config.from_env()
    assert config.batdetect2_enabled is False
    assert config.batdetect2_threshold == 0.8


def test_batdetect2_enabled_and_threshold_are_configurable(
    monkeypatch: pytest.MonkeyPatch,
    minimal_env: dict[str, str],
) -> None:
    for key, value in minimal_env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("FLEDERMAP_BATDETECT2_ENABLED", "true")
    monkeypatch.setenv("FLEDERMAP_BATDETECT2_THRESHOLD", "0.65")
    config = Config.from_env()
    assert config.batdetect2_enabled is True
    assert config.batdetect2_threshold == 0.65


def test_invalid_batdetect2_enabled_raises_config_error(
    monkeypatch: pytest.MonkeyPatch,
    minimal_env: dict[str, str],
) -> None:
    for key, value in minimal_env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("FLEDERMAP_BATDETECT2_ENABLED", "maybe")
    with pytest.raises(ConfigError, match="not a boolean"):
        Config.from_env()


def test_batdetect2_threshold_out_of_range_raises_config_error(
    monkeypatch: pytest.MonkeyPatch,
    minimal_env: dict[str, str],
) -> None:
    for key, value in minimal_env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("FLEDERMAP_BATDETECT2_THRESHOLD", "1.5")
    with pytest.raises(ConfigError, match="between 0 and 1"):
        Config.from_env()
```

Check the top of `tests/test_config.py` for how `minimal_env` (or equivalent — the fixture supplying `FLEDERMAP_DATABASE_URL`/`FLEDERMAP_ARCHIVE_ROOTS` for every other test in this file) is actually named and provided before writing these; use whatever the file's existing tests already use to satisfy required env vars, matching their exact pattern rather than inventing a new fixture name.

- [ ] **Step 2: Run tests to verify they fail**

Run: `hatch test tests/test_config.py -k batdetect2 -v`
Expected: FAIL — `AttributeError: 'Config' object has no attribute 'batdetect2_enabled'` (or similar).

- [ ] **Step 3: Add the env var constants and known file keys**

In `src/fledermap/config.py`, near the other `ENV_*` constants:

```python
ENV_BATDETECT2_ENABLED = "FLEDERMAP_BATDETECT2_ENABLED"
ENV_BATDETECT2_THRESHOLD = "FLEDERMAP_BATDETECT2_THRESHOLD"
```

Add `"batdetect2_enabled"` and `"batdetect2_threshold"` to `_KNOWN_FILE_KEYS`.

- [ ] **Step 4: Add the dataclass fields**

In the `Config` dataclass body, near `noise_snr_threshold_db`:

```python
# Off by default: batdetect2 is CC-BY-NC-4.0-licensed (non-commercial use only) and an
# opt-in `pyproject.toml` extra -- see docs/superpowers/specs/2026-09-15-fledermap-
# batdetect2-classifier-design.md §1. Enabling this without `pip install
# fledermap[batdetect2]` fails the classification job at run time, not at config load.
batdetect2_enabled: bool = False
# Re-applied every `run_ingest_cycle`, not baked in at classification time -- a changed
# threshold takes effect on stored raw scores with no model re-run needed (design spec §2.2).
# 0.8 is this project's own conservative starting point; batdetect2 itself documents no
# default for its `--detection-threshold` CLI argument.
batdetect2_threshold: float = 0.8
```

- [ ] **Step 5: Add the parsing logic in `from_env`**

Insert after the `site_min_points` block (before the `port_raw = ...` line):

```python
batdetect2_enabled_raw = _lookup(
    ENV_BATDETECT2_ENABLED,
    "batdetect2_enabled",
    file_values,
)
if batdetect2_enabled_raw is None:
    batdetect2_enabled = False
elif isinstance(batdetect2_enabled_raw, bool):
    batdetect2_enabled = batdetect2_enabled_raw
else:
    label = _source_label(
        ENV_BATDETECT2_ENABLED,
        "batdetect2_enabled",
        config_path,
    )
    text = _as_str(batdetect2_enabled_raw, label).strip().lower()
    if text in ("true", "1", "yes"):
        batdetect2_enabled = True
    elif text in ("false", "0", "no"):
        batdetect2_enabled = False
    else:
        msg = f"{label}={batdetect2_enabled_raw!r} is not a boolean (true/false)."
        raise ConfigError(msg)

batdetect2_threshold_raw = _lookup(
    ENV_BATDETECT2_THRESHOLD,
    "batdetect2_threshold",
    file_values,
)
if batdetect2_threshold_raw is None:
    batdetect2_threshold = 0.8
else:
    label = _source_label(
        ENV_BATDETECT2_THRESHOLD,
        "batdetect2_threshold",
        config_path,
    )
    if isinstance(batdetect2_threshold_raw, bool):  # see session_gap_hours above
        msg = f"{label}={batdetect2_threshold_raw!r} is not a number."
        raise ConfigError(msg)
    try:
        batdetect2_threshold = float(batdetect2_threshold_raw)
    except (TypeError, ValueError) as exc:
        msg = f"{label}={batdetect2_threshold_raw!r} is not a number."
        raise ConfigError(msg) from exc
    if not 0.0 <= batdetect2_threshold <= 1.0:
        msg = f"{label}={batdetect2_threshold_raw!r} must be between 0 and 1."
        raise ConfigError(msg)
```

- [ ] **Step 6: Pass the new fields into the final `cls(...)` call**

In the `return cls(...)` block, add:

```python
batdetect2_enabled=batdetect2_enabled,
batdetect2_threshold=batdetect2_threshold,
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `hatch test tests/test_config.py -v`
Expected: PASS, including the four new tests and every pre-existing one in the file.

- [ ] **Step 8: Run mypy**

Run: `hatch run types:check`
Expected: no new errors.

- [ ] **Step 9: Commit**

```bash
git add src/fledermap/config.py tests/test_config.py
git commit -m "feat: add FLEDERMAP_BATDETECT2_ENABLED/_THRESHOLD config"
```

---

### Task 3: `classifier_detection` table — migration and model

**Files:**
- Modify: `src/fledermap/store/models.py`
- Create: `src/fledermap/alembic/versions/<new_revision>_add_classifier_detection.py`
- Test: `tests/test_migrations.py`

**Interfaces:**
- Produces: `ClassifierDetection` model with columns `id`, `recording_id` (FK to `recording.id`, `ondelete="CASCADE"`), `source` (`IdSource` enum, `create_constraint=False`), `taxon_id` (FK to `taxon.id`, nullable), `confidence` (`Float`).

- [ ] **Step 1: Add the model**

In `src/fledermap/store/models.py`, near `Identification`:

```python
class ClassifierDetection(Base):
    """Raw classifier output, independent of the currently configured verdict
    threshold -- the durable record of "what did the model actually say."
    `Identification` rows are materialized from this table by filtering on the
    current threshold and calling `services/identifications.py`'s
    `replace_claims`; this table itself is never read by `current_best.py` or
    any other identification consumer. See docs/superpowers/specs/2026-09-15-
    fledermap-batdetect2-classifier-design.md §2.

    `source` has no CHECK constraint, mirroring `Identification.source`
    (spec D9): further classifiers are coming, and a CHECK would force a
    migration per one. This table is deliberately generic (not
    `batdetect2_detection`) so a future classifier reuses it without a new
    migration.

    Holds every candidate above a fixed, non-configurable, low sanity floor
    (see jobs/tasks.py's `_BATDETECT2_FLOOR`) -- not the user-configured
    verdict threshold. Exposing the floor as config would let someone set it
    above their own threshold and silently break threshold-change recompute.
    """

    __tablename__ = "classifier_detection"

    id: Mapped[int] = mapped_column(primary_key=True)
    recording_id: Mapped[int] = mapped_column(
        ForeignKey("recording.id", ondelete="CASCADE"),
        index=True,
    )
    source: Mapped[IdSource] = mapped_column(
        SAEnum(
            IdSource,
            native_enum=False,
            length=32,
            create_constraint=False,
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
        ),
        index=True,
    )
    taxon_id: Mapped[int | None] = mapped_column(ForeignKey("taxon.id"))
    confidence: Mapped[float] = mapped_column(Float)

    recording: Mapped[Recording] = relationship()
    taxon: Mapped[Taxon | None] = relationship()
```

(Check the top of `models.py` for the exact existing import names — `Float`, `SAEnum`, `ForeignKey`, `Mapped`, `mapped_column`, `relationship`, `IdSource` are all already imported for `Identification`, so this needs no new imports.)

- [ ] **Step 2: Generate the migration**

Run: `hatch run alembic revision --autogenerate -m "add classifier_detection table"`

Inspect the generated file under `src/fledermap/alembic/versions/`. It should contain one `op.create_table("classifier_detection", ...)` with columns matching the model above, and nothing else. Rewrite its module docstring (the autogenerated one is empty) to match this project's convention — see `37a2d2fc79bf_add_recording_snr_db.py` for the style — explaining what the table is for and pointing at the design spec, e.g.:

```python
"""add classifier_detection table

Revision ID: <generated>
Revises: 37a2d2fc79bf
Create Date: <generated>

Raw classifier output, independent of the currently configured verdict threshold --
see docs/superpowers/specs/2026-09-15-fledermap-batdetect2-classifier-design.md §2.
`source` has no CHECK constraint, mirroring `identification.source` (spec D9):
further classifiers are coming and a CHECK would force a migration per one.
"""
```

Confirm `down_revision` reads `"37a2d2fc79bf"` (this repo's current head at plan-writing time — re-check with `hatch run alembic heads` if other migrations have landed since).

- [ ] **Step 3: Write the failing migration-drift test**

There is no dedicated new test needed beyond the suite's existing blanket check —
`tests/test_migrations.py`'s `test_migration_matches_the_models` (or equivalently named
existing test using `compare_metadata`) already asserts zero drift for the whole schema.
Confirm this by running it now, before generating the migration would have made it pass,
to see the real failure mode:

Run: `hatch test tests/test_migrations.py -v` (needs `dangerouslyDisableSandbox: true` — Docker/PostGIS)
Expected at this point (model added, migration not yet generated — if you did Step 2 first, temporarily `git stash` the migration file to reproduce this): FAIL, `compare_metadata` reports a missing table.

- [ ] **Step 4: Run the full migration test suite to verify it passes**

Run: `hatch test tests/test_migrations.py -v` (`dangerouslyDisableSandbox: true`)
Expected: PASS — including `test_migration_matches_the_models`, and every existing enum/CHECK-specific test in the file (these should be untouched, since `classifier_detection.source` reuses the exact same `IdSource` enum values, needing no new literal-list test — `test_migration_idsource_literal_matches_the_model`, if that's this file's real name, already covers `IdSource` wherever it's used).

- [ ] **Step 5: Run the full non-db suite and mypy**

Run: `hatch test -m "not db"` and `hatch run types:check`
Expected: PASS, no new errors.

- [ ] **Step 6: Commit**

```bash
git add src/fledermap/store/models.py src/fledermap/alembic/versions/
git commit -m "feat: add classifier_detection table for raw classifier output"
```

---

### Task 4: `services/batdetect2.py` — raw-detection storage and claim materialization

**Files:**
- Create: `src/fledermap/services/batdetect2.py`
- Test: `tests/test_batdetect2_service.py`

**Interfaces:**
- Consumes: `ClaimInput`, `replace_claims` (`services/identifications.py`); `ClassifierDetection`, `Recording` (`store/models.py`); `IdSource`, `Verdict` (`domain/codes.py`).
- Produces:
  - `DetectionInput` (frozen dataclass: `taxon_id: int | None`, `confidence: float`)
  - `record_detections(session: OrmSession, recording: Recording, source: IdSource, detections: Sequence[DetectionInput]) -> None`
  - `materialize_claims(session: OrmSession, source: IdSource, threshold: float) -> MaterializeReport` (frozen dataclass: `recordings_updated: int`)
  - `purge_source(session: OrmSession, source: IdSource) -> PurgeReport` (frozen dataclass: `detections_removed: int`, `identifications_removed: int`)

- [ ] **Step 1: Write the failing tests for `record_detections`**

```python
"""tests/test_batdetect2_service.py"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import IdSource, Verdict
from fledermap.services.batdetect2 import (
    DetectionInput,
    MaterializeReport,
    PurgeReport,
    materialize_claims,
    purge_source,
    record_detections,
)
from fledermap.store.models import ClassifierDetection, Identification, Recording, Taxon

pytestmark = pytest.mark.db


def _make_recording(session: OrmSession, audio_hash: str) -> Recording:
    recording = Recording(audio_hash=audio_hash, path=f"{audio_hash}.wav", archive_root_index=0)
    session.add(recording)
    session.flush()
    return recording


def _make_taxon(session: OrmSession, scientific_name: str) -> Taxon:
    taxon = Taxon(rank="species", scientific_name=scientific_name)
    session.add(taxon)
    session.flush()
    return taxon


def test_record_detections_replaces_a_source_s_existing_rows(session: OrmSession) -> None:
    recording = _make_recording(session, "hash1")
    taxon = _make_taxon(session, "Pipistrellus pipistrellus")
    record_detections(
        session,
        recording,
        IdSource.BATDETECT2,
        [DetectionInput(taxon_id=taxon.id, confidence=0.4)],
    )
    session.flush()

    record_detections(
        session,
        recording,
        IdSource.BATDETECT2,
        [DetectionInput(taxon_id=taxon.id, confidence=0.9)],
    )
    session.flush()

    rows = session.scalars(
        select(ClassifierDetection).where(
            ClassifierDetection.recording_id == recording.id,
            ClassifierDetection.source == IdSource.BATDETECT2,
        ),
    ).all()
    assert len(rows) == 1
    assert rows[0].confidence == 0.9


def test_record_detections_with_no_candidates_clears_prior_rows(
    session: OrmSession,
) -> None:
    recording = _make_recording(session, "hash2")
    taxon = _make_taxon(session, "Nyctalus noctula")
    record_detections(
        session,
        recording,
        IdSource.BATDETECT2,
        [DetectionInput(taxon_id=taxon.id, confidence=0.7)],
    )
    session.flush()

    record_detections(session, recording, IdSource.BATDETECT2, [])
    session.flush()

    rows = session.scalars(
        select(ClassifierDetection).where(
            ClassifierDetection.recording_id == recording.id,
        ),
    ).all()
    assert rows == []
```

- [ ] **Step 2: Run to verify failure**

Run: `hatch test tests/test_batdetect2_service.py -v` (needs `dangerouslyDisableSandbox: true`)
Expected: FAIL — `ModuleNotFoundError: No module named 'fledermap.services.batdetect2'`.

- [ ] **Step 3: Implement `DetectionInput` and `record_detections`**

```python
"""Raw batdetect2 (and, later, any other open-vocabulary classifier) output
storage, and materializing it into `Identification` claims. See
docs/superpowers/specs/2026-09-15-fledermap-batdetect2-classifier-design.md §2.

`record_detections` is a full replace, not an incremental sync, unlike
`services/identifications.py`'s `replace_claims`: `classifier_detection` rows
aren't claims with independent identity across runs, they're a cache of "the
last time this source's model ran over this recording, here's everything it
said" -- so a re-run simply discards the previous cache wholesale and writes
the new one.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import IdSource, Verdict
from fledermap.services.identifications import ClaimInput, replace_claims
from fledermap.store.models import ClassifierDetection, Recording


@dataclass(frozen=True)
class DetectionInput:
    taxon_id: int | None
    confidence: float


def record_detections(
    session: OrmSession,
    recording: Recording,
    source: IdSource,
    detections: Sequence[DetectionInput],
) -> None:
    """Replace every existing `classifier_detection` row for
    `(recording, source)` with `detections`."""
    session.execute(
        ClassifierDetection.__table__.delete().where(
            ClassifierDetection.recording_id == recording.id,
            ClassifierDetection.source == source,
        ),
    )
    for detection in detections:
        session.add(
            ClassifierDetection(
                recording_id=recording.id,
                source=source,
                taxon_id=detection.taxon_id,
                confidence=detection.confidence,
            ),
        )
```

- [ ] **Step 4: Run to verify the `record_detections` tests pass**

Run: `hatch test tests/test_batdetect2_service.py -k record_detections -v` (`dangerouslyDisableSandbox: true`)
Expected: PASS.

- [ ] **Step 5: Write the failing tests for `materialize_claims`**

```python
def test_materialize_claims_writes_species_claim_above_threshold(
    session: OrmSession,
) -> None:
    recording = _make_recording(session, "hash3")
    taxon = _make_taxon(session, "Pipistrellus pygmaeus")
    record_detections(
        session,
        recording,
        IdSource.BATDETECT2,
        [DetectionInput(taxon_id=taxon.id, confidence=0.9)],
    )
    session.flush()

    report = materialize_claims(session, IdSource.BATDETECT2, threshold=0.8)
    session.flush()

    assert report == MaterializeReport(recordings_updated=1)
    idents = session.scalars(
        select(Identification).where(
            Identification.recording_id == recording.id,
            Identification.source == IdSource.BATDETECT2,
        ),
    ).all()
    assert len(idents) == 1
    assert idents[0].verdict == Verdict.SPECIES
    assert idents[0].taxon_id == taxon.id


def test_materialize_claims_writes_no_id_when_nothing_clears_threshold(
    session: OrmSession,
) -> None:
    recording = _make_recording(session, "hash4")
    taxon = _make_taxon(session, "Eptesicus serotinus")
    record_detections(
        session,
        recording,
        IdSource.BATDETECT2,
        [DetectionInput(taxon_id=taxon.id, confidence=0.3)],
    )
    session.flush()

    materialize_claims(session, IdSource.BATDETECT2, threshold=0.8)
    session.flush()

    idents = session.scalars(
        select(Identification).where(
            Identification.recording_id == recording.id,
            Identification.source == IdSource.BATDETECT2,
        ),
    ).all()
    assert len(idents) == 1
    assert idents[0].verdict == Verdict.NO_ID
    assert idents[0].taxon_id is None


def test_materialize_claims_is_idempotent_and_recomputes_on_threshold_change(
    session: OrmSession,
) -> None:
    recording = _make_recording(session, "hash5")
    taxon = _make_taxon(session, "Barbastella barbastellus")
    record_detections(
        session,
        recording,
        IdSource.BATDETECT2,
        [DetectionInput(taxon_id=taxon.id, confidence=0.6)],
    )
    session.flush()

    materialize_claims(session, IdSource.BATDETECT2, threshold=0.8)
    session.flush()
    first = session.scalars(
        select(Identification).where(Identification.recording_id == recording.id),
    ).one()
    assert first.verdict == Verdict.NO_ID

    # Lower the threshold -- no re-run of the classifier, same stored raw score.
    materialize_claims(session, IdSource.BATDETECT2, threshold=0.5)
    session.flush()
    second = session.scalars(
        select(Identification).where(Identification.recording_id == recording.id),
    ).one()
    assert second.verdict == Verdict.SPECIES
    assert second.taxon_id == taxon.id


def test_materialize_claims_skips_recordings_with_no_raw_detections(
    session: OrmSession,
) -> None:
    _make_recording(session, "hash6")  # never processed by batdetect2 at all
    report = materialize_claims(session, IdSource.BATDETECT2, threshold=0.8)
    assert report == MaterializeReport(recordings_updated=0)
```

- [ ] **Step 6: Run to verify failure**

Run: `hatch test tests/test_batdetect2_service.py -k materialize_claims -v` (`dangerouslyDisableSandbox: true`)
Expected: FAIL — `ImportError: cannot import name 'materialize_claims'`.

- [ ] **Step 7: Implement `materialize_claims`**

Append to `src/fledermap/services/batdetect2.py`:

```python
@dataclass(frozen=True)
class MaterializeReport:
    recordings_updated: int


def materialize_claims(
    session: OrmSession,
    source: IdSource,
    threshold: float,
) -> MaterializeReport:
    """Sync `Identification` claims from `source` with what `classifier_detection`
    currently holds, filtered by `threshold`. Idempotent and re-run every ingest
    cycle (spec §4) -- `replace_claims` already no-ops when nothing changed,
    same as `services/noise.py`'s `classify_noise`, so a changed threshold takes
    effect automatically with no separate "did it change" tracking needed."""
    recording_ids = session.scalars(
        select(ClassifierDetection.recording_id)
        .where(ClassifierDetection.source == source)
        .distinct(),
    ).all()

    now = datetime.now(UTC)
    updated = 0
    for recording_id in recording_ids:
        recording = session.get(Recording, recording_id)
        assert recording is not None  # FK guarantees this
        detections = session.scalars(
            select(ClassifierDetection).where(
                ClassifierDetection.recording_id == recording_id,
                ClassifierDetection.source == source,
            ),
        ).all()
        passing = [d for d in detections if d.confidence >= threshold and d.taxon_id is not None]
        if passing:
            desired = [
                ClaimInput(taxon_id=d.taxon_id, verdict=Verdict.SPECIES) for d in passing
            ]
        else:
            desired = [ClaimInput(taxon_id=None, verdict=Verdict.NO_ID)]
        replace_claims(session, recording, source, desired, now)
        updated += 1

    return MaterializeReport(recordings_updated=updated)
```

- [ ] **Step 8: Run to verify the `materialize_claims` tests pass**

Run: `hatch test tests/test_batdetect2_service.py -k materialize_claims -v` (`dangerouslyDisableSandbox: true`)
Expected: PASS.

- [ ] **Step 9: Write the failing tests for `purge_source`**

```python
def test_purge_source_removes_detections_and_identifications_for_that_source_only(
    session: OrmSession,
) -> None:
    recording = _make_recording(session, "hash7")
    taxon = _make_taxon(session, "Rhinolophus ferrumequinum")
    record_detections(
        session,
        recording,
        IdSource.BATDETECT2,
        [DetectionInput(taxon_id=taxon.id, confidence=0.9)],
    )
    session.flush()
    materialize_claims(session, IdSource.BATDETECT2, threshold=0.8)
    session.flush()
    # A second, unrelated source's claim on the same recording must survive.
    from fledermap.domain.codes import IdSource as _IdSource

    session.add(
        Identification(
            recording_id=recording.id,
            source=_IdSource.EMT_GUANO,
            verdict=Verdict.SPECIES,
            taxon_id=taxon.id,
        ),
    )
    session.flush()

    report = purge_source(session, IdSource.BATDETECT2)
    session.flush()

    assert report == PurgeReport(detections_removed=1, identifications_removed=1)
    assert (
        session.scalars(
            select(ClassifierDetection).where(
                ClassifierDetection.recording_id == recording.id,
            ),
        ).all()
        == []
    )
    remaining = session.scalars(
        select(Identification).where(Identification.recording_id == recording.id),
    ).all()
    assert len(remaining) == 1
    assert remaining[0].source == _IdSource.EMT_GUANO


def test_purge_source_is_idempotent(session: OrmSession) -> None:
    report = purge_source(session, IdSource.BATDETECT2)
    assert report == PurgeReport(detections_removed=0, identifications_removed=0)
```

- [ ] **Step 10: Run to verify failure**

Run: `hatch test tests/test_batdetect2_service.py -k purge_source -v` (`dangerouslyDisableSandbox: true`)
Expected: FAIL — `ImportError: cannot import name 'purge_source'`.

- [ ] **Step 11: Implement `purge_source`**

Append to `src/fledermap/services/batdetect2.py` (add `Identification` to the `store.models` import):

```python
@dataclass(frozen=True)
class PurgeReport:
    detections_removed: int
    identifications_removed: int


def purge_source(session: OrmSession, source: IdSource) -> PurgeReport:
    """Delete every `classifier_detection` and `Identification` row for
    `source`, scoped precisely to that source -- never touches any other
    source's rows, and never cascades into recording/session/site. Idempotent:
    called every ingest cycle while the classifier is disabled (spec §5), so
    it must be a no-op on rows that don't exist."""
    detections_removed = session.execute(
        ClassifierDetection.__table__.delete().where(
            ClassifierDetection.source == source,
        ),
    ).rowcount
    identifications_removed = session.execute(
        Identification.__table__.delete().where(
            Identification.source == source,
        ),
    ).rowcount
    return PurgeReport(
        detections_removed=detections_removed,
        identifications_removed=identifications_removed,
    )
```

- [ ] **Step 12: Run the full test file, then the full non-db suite and mypy**

Run: `hatch test tests/test_batdetect2_service.py -v` (`dangerouslyDisableSandbox: true`) — expect PASS, all tests.
Run: `hatch test -m "not db"` and `hatch run types:check` — expect PASS, no new errors.

- [ ] **Step 13: Commit**

```bash
git add src/fledermap/services/batdetect2.py tests/test_batdetect2_service.py
git commit -m "feat: add batdetect2 raw-detection storage and claim materialization"
```

---

### Task 5: `classify_batdetect2_task` — the classification job

**Files:**
- Modify: `src/fledermap/jobs/tasks.py`
- Test: `tests/test_jobs_tasks_batdetect2.py`

**Interfaces:**
- Consumes: `record_detections`, `DetectionInput` (`services/batdetect2.py`); `resolve_recording`, `resolve_wav_path` (`services/media.py`); `resolve_code` (`store/seed.py`).
- Produces: `batdetect2_lock_key(audio_hash: str) -> str`; `classify_batdetect2_task` (a Procrastinate task, queue `"classify"`, `retry=_RETRY`).

- [ ] **Step 1: Write the failing test**

`batdetect2.api.process_file` is mocked here — this test exercises the task's own logic (path resolution, floor filtering, taxon-code resolution, calling `record_detections`), not the real model, and must run in the base test env (no `batdetect2` install required):

```python
"""tests/test_jobs_tasks_batdetect2.py"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import IdSource
from fledermap.jobs.tasks import batdetect2_lock_key, classify_batdetect2_task
from fledermap.store.models import ClassifierDetection, Recording, Taxon, TaxonCode

pytestmark = pytest.mark.db


def test_batdetect2_lock_key_is_scoped_to_the_audio_hash() -> None:
    assert batdetect2_lock_key("abc123") == "batdetect2:abc123"


def _fake_detection(class_name: str, score: float) -> SimpleNamespace:
    return SimpleNamespace(
        detection_score=score,
        class_scores={class_name: score},
    )


def test_classify_batdetect2_task_stores_detections_above_the_floor_only(
    session: OrmSession,
    engine,
    tmp_path: Path,
) -> None:
    recording = Recording(
        audio_hash="hashA",
        path="a.wav",
        archive_root_index=0,
    )
    session.add(recording)
    taxon = Taxon(rank="species", scientific_name="Pipistrellus pipistrellus")
    session.add(taxon)
    session.flush()
    session.add(
        TaxonCode(source="batdetect2", code="Pipistrellus pipistrellus", taxon_id=taxon.id),
    )
    wav_path = tmp_path / "a.wav"
    wav_path.write_bytes(b"")
    session.commit()

    fake_api_module = SimpleNamespace(
        process_file=lambda path: SimpleNamespace(
            detections=[
                _fake_detection("Pipistrellus pipistrellus", 0.9),
                _fake_detection("Pipistrellus pipistrellus", 0.02),  # below floor, dropped
            ],
        ),
    )

    context = SimpleNamespace(
        additional_context={
            "archive_roots": (tmp_path,),
            "engine": engine,
        },
    )

    with patch.dict("sys.modules", {"batdetect2.api": fake_api_module}):
        classify_batdetect2_task(context, "hashA")

    rows = session.scalars(
        select(ClassifierDetection).where(ClassifierDetection.recording_id == recording.id),
    ).all()
    assert len(rows) == 1
    assert rows[0].confidence == 0.9
    assert rows[0].taxon_id == taxon.id
    assert rows[0].source == IdSource.BATDETECT2
```

Check `tests/conftest.py` for how other job-task tests in this codebase (if any exist, e.g. a `test_jobs_tasks.py` for the media tasks) construct a fake `procrastinate.JobContext` — reuse that exact pattern instead of the plain `SimpleNamespace` above if one already exists, since `context.additional_context` access must match what real Procrastinate hands the task.

- [ ] **Step 2: Run to verify failure**

Run: `hatch test tests/test_jobs_tasks_batdetect2.py -v` (`dangerouslyDisableSandbox: true`)
Expected: FAIL — `ImportError: cannot import name 'classify_batdetect2_task'`.

- [ ] **Step 3: Implement the task**

Add to `src/fledermap/jobs/tasks.py`, near the other per-recording media tasks:

```python
_BATDETECT2_FLOOR = 0.05


def batdetect2_lock_key(audio_hash: str) -> str:
    return f"batdetect2:{audio_hash}"


@app.task(queue="classify", pass_context=True, retry=_RETRY)
def classify_batdetect2_task(
    context: procrastinate.JobContext,
    audio_hash: str,
) -> None:
    """Run batdetect2 once over one recording and cache every candidate above
    `_BATDETECT2_FLOOR` in `classifier_detection` -- never writes
    `Identification` directly (see `services/batdetect2.py`'s
    `materialize_claims`, run separately every ingest cycle).

    Local import of `batdetect2.api`, not a top-level one: batdetect2 is an
    optional extra (`pip install fledermap[batdetect2]`), and a top-level
    import here would crash this entire module -- and therefore every other
    job in the app -- on worker startup if it isn't installed. Matches this
    project's existing precedent for a configured-but-broken optional
    integration (`services/site_naming.py`'s `ensure_connected`): let this
    job fail naturally and retry via `_RETRY`, no config-time check, no
    global disable (design spec §5, §7).
    """
    # Local import: see docstring above, and `render_spectrogram_task`'s own
    # local `services.media` import for the (different) circular-import
    # reason every other job task in this module already follows.
    from batdetect2 import api as batdetect2_api

    from fledermap.services.batdetect2 import DetectionInput, record_detections
    from fledermap.services.media import resolve_recording, resolve_wav_path
    from fledermap.store.seed import resolve_code

    archive_roots: tuple[Path, ...] = context.additional_context["archive_roots"]
    engine = context.additional_context["engine"]

    with OrmSession(engine) as session:
        recording = resolve_recording(session, audio_hash)
        wav_path = resolve_wav_path(archive_roots, recording)

        result = batdetect2_api.process_file(str(wav_path))

        detections: list[DetectionInput] = []
        for detection in result.detections:
            if detection.detection_score < _BATDETECT2_FLOOR:
                continue
            top_class = max(detection.class_scores, key=detection.class_scores.get)
            taxon = resolve_code(session, "batdetect2", top_class)
            detections.append(
                DetectionInput(
                    taxon_id=taxon.id if taxon is not None else None,
                    confidence=detection.detection_score,
                ),
            )

        record_detections(session, recording, IdSource.BATDETECT2, detections)
        session.commit()
```

(Add `from fledermap.domain.codes import IdSource` to `jobs/tasks.py`'s existing imports if it isn't already imported there — check first, since other job tasks may already need it.)

- [ ] **Step 4: Run to verify the test passes**

Run: `hatch test tests/test_jobs_tasks_batdetect2.py -v` (`dangerouslyDisableSandbox: true`)
Expected: PASS.

- [ ] **Step 5: Run the full non-db suite and mypy**

Run: `hatch test -m "not db"` and `hatch run types:check`
Expected: PASS, no new errors. (mypy runs over `jobs/tasks.py` without `batdetect2` installed in the base/types env — the local `from batdetect2 import api as batdetect2_api` import inside the function body will need `batdetect2` in `pyproject.toml`'s `[tool.hatch.envs.types]` `extra-dependencies`, OR a `# type: ignore[import-not-found]` on that one line with a comment explaining why. Check `hatch run types:check`'s actual output before picking one — prefer adding it to the types env's dependencies if mypy treats it as a hard error, since a global `ignore_missing_imports` has already been rejected twice in this project.)

- [ ] **Step 6: Commit**

```bash
git add src/fledermap/jobs/tasks.py tests/test_jobs_tasks_batdetect2.py
git commit -m "feat: add classify_batdetect2_task classification job"
```

---

### Task 6: Wire enqueue / materialize / purge into `run_ingest_cycle`

**Files:**
- Modify: `src/fledermap/jobs/tasks.py`
- Test: `tests/test_jobs_tasks_batdetect2.py` (or a new `tests/test_run_ingest_cycle_batdetect2.py` if `run_ingest_cycle`'s existing tests already live in a large dedicated file — check first and follow whichever convention already exists for `run_ingest_cycle` tests)

**Interfaces:**
- Consumes: `materialize_claims`, `purge_source` (`services/batdetect2.py`); `classify_batdetect2_task`, `batdetect2_lock_key` (Task 5); `Config.batdetect2_enabled`, `Config.batdetect2_threshold` (Task 2).
- Produces: `enqueue_batdetect2(session: OrmSession, engine: Engine) -> int` in `services/batdetect2.py` (enqueue count, mirroring `services/media.py`'s `backfill_media` return shape).

- [ ] **Step 1: Write the failing test for `enqueue_batdetect2`**

Add to `tests/test_batdetect2_service.py`:

```python
def test_enqueue_batdetect2_only_enqueues_recordings_missing_a_detection_row(
    session: OrmSession,
    engine,
) -> None:
    from fledermap.services.batdetect2 import enqueue_batdetect2

    with_detections = _make_recording(session, "hashE1")
    without_detections = _make_recording(session, "hashE2")
    taxon = _make_taxon(session, "Myotis daubentonii")
    record_detections(
        session,
        with_detections,
        IdSource.BATDETECT2,
        [DetectionInput(taxon_id=taxon.id, confidence=0.5)],
    )
    session.commit()

    count = enqueue_batdetect2(session, engine)

    assert count == 1  # only `without_detections`
```

Check `services/media.py`'s `enqueue_media`/`backfill_media` for the exact `jobs_app.open(engine)` / `try: ... except NotImplementedError: pass` pattern before writing `enqueue_batdetect2` — this test just checks the *count*, not that a real Procrastinate job was actually enqueued (that would need a live Procrastinate connector, out of scope for a unit test here — `backfill_media`'s own tests, if any, show whether this project tests deferral itself or just trusts Procrastinate's `configure(lock=...).defer_async(...)` call was made, e.g. via mocking).

- [ ] **Step 2: Run to verify failure**

Run: `hatch test tests/test_batdetect2_service.py -k enqueue_batdetect2 -v` (`dangerouslyDisableSandbox: true`)
Expected: FAIL — `ImportError`.

- [ ] **Step 3: Implement `enqueue_batdetect2`**

Append to `src/fledermap/services/batdetect2.py`:

```python
def enqueue_batdetect2(session: OrmSession, engine: Engine) -> int:
    """Enqueue `classify_batdetect2_task` for every recording lacking a
    `classifier_detection` row for `IdSource.BATDETECT2` yet. Re-run every
    ingest cycle while the classifier is enabled (spec §4/§5) -- this is
    what makes enabling the classifier self-backfilling with no separate
    one-shot "just enabled" transition to detect: the very next cycle after
    flipping the config flag finds every existing recording still missing a
    row and enqueues it, same as any recording ingested afterward."""
    # Local import: `jobs.tasks` imports FROM this module's sibling
    # `services.media` at ITS top level -- same circular-import shape,
    # avoided the same way (see `services/media.py`'s own `jobs.tasks`
    # import for precedent).
    from fledermap.jobs.tasks import batdetect2_lock_key, classify_batdetect2_task
    from fledermap.jobs.tasks import app as jobs_app

    try:
        jobs_app.open(engine)
    except NotImplementedError:
        pass

    already_processed = select(ClassifierDetection.recording_id).where(
        ClassifierDetection.source == IdSource.BATDETECT2,
    )
    hashes = session.scalars(
        select(Recording.audio_hash).where(
            Recording.missing_since.is_(None),
            Recording.id.not_in(already_processed),
        ),
    ).all()
    for audio_hash in hashes:
        classify_batdetect2_task.configure(
            lock=batdetect2_lock_key(audio_hash),
        ).defer(audio_hash=audio_hash)
    return len(hashes)
```

(Add `from sqlalchemy.engine import Engine` to the file's imports.)

- [ ] **Step 4: Run to verify it passes**

Run: `hatch test tests/test_batdetect2_service.py -k enqueue_batdetect2 -v` (`dangerouslyDisableSandbox: true`)
Expected: PASS.

- [ ] **Step 5: Wire all three into `run_ingest_cycle`**

In `src/fledermap/jobs/tasks.py`'s `run_ingest_cycle`, add after the existing `classify_noise(session, config.noise_snr_threshold_db)` / `session.commit()` block:

```python
from fledermap.services.batdetect2 import (
    enqueue_batdetect2,
    materialize_claims,
    purge_source,
)

if config.batdetect2_enabled:
    enqueue_batdetect2(session, engine)
    materialize_claims(session, IdSource.BATDETECT2, config.batdetect2_threshold)
else:
    purge_source(session, IdSource.BATDETECT2)
session.commit()
```

(This local import goes with the function's other local imports near its top, matching the existing style — not literally inline at this point in the function body; check exactly where `from fledermap.services.media import enqueue_media` and `from fledermap.services.site_naming import enqueue_site_naming` are placed in the current function and follow the same placement convention.)

- [ ] **Step 6: Write an integration test for the enable/disable cycle behavior**

Add to whichever test file already covers `run_ingest_cycle` end-to-end (find it via `grep -rn "run_ingest_cycle" tests/`), following that file's existing fixtures/setup for a minimal `Config` and a seeded recording:

```python
def test_run_ingest_cycle_purges_batdetect2_rows_when_disabled(
    session: OrmSession,
    engine,
    minimal_config: Config,  # adjust to whatever fixture name this test file already uses
) -> None:
    from fledermap.services.batdetect2 import DetectionInput, record_detections

    recording = _make_recording(session, "hashD1")  # or this file's existing recording helper
    taxon = _make_taxon(session, "Plecotus auritus")
    record_detections(
        session,
        recording,
        IdSource.BATDETECT2,
        [DetectionInput(taxon_id=taxon.id, confidence=0.9)],
    )
    session.commit()

    config = replace(minimal_config, batdetect2_enabled=False)
    context = SimpleNamespace(additional_context={"config": config, "engine": engine})
    run_ingest_cycle(context, 0)

    remaining = session.scalars(
        select(ClassifierDetection).where(ClassifierDetection.recording_id == recording.id),
    ).all()
    assert remaining == []
```

Adjust this test to match whatever `run_ingest_cycle`'s existing tests already do for archive roots / scan setup — `run_ingest_cycle` also calls `scan_all_roots` and other steps that need a real (even if empty) archive directory; copy the minimal setup an existing `run_ingest_cycle` test already uses rather than reconstructing it from scratch.

- [ ] **Step 7: Run to verify it passes**

Run the specific new test file (`dangerouslyDisableSandbox: true`).
Expected: PASS.

- [ ] **Step 8: Run the full db suite, non-db suite, and mypy**

Run: `hatch test -m db` (`dangerouslyDisableSandbox: true`, ~120s), `hatch test -m "not db"`, `hatch run types:check`.
Expected: all PASS, no new errors.

- [ ] **Step 9: Commit**

```bash
git add src/fledermap/services/batdetect2.py src/fledermap/jobs/tasks.py tests/
git commit -m "feat: wire batdetect2 enqueue/materialize/purge into run_ingest_cycle"
```

---

### Task 7: Precedence — wire `BATDETECT2` into `current_best.py`

**Files:**
- Modify: `src/fledermap/services/current_best.py`
- Modify: `tests/test_current_best.py`

**Interfaces:**
- Consumes: none new.
- Produces: updated `_PRECEDENCE` tuple.

- [ ] **Step 1: Update the existing placeholder test first (it currently asserts the wrong future state)**

In `tests/test_current_best.py`, `test_sort_identifications_by_precedence_puts_an_unranked_source_last` currently uses `IdSource.BATDETECT2` as its "still unranked" example. Change it to `IdSource.BATTYBIRDNET` (which remains genuinely unranked — out of scope per this spec's §7):

```python
def test_sort_identifications_by_precedence_puts_an_unranked_source_last() -> None:
    """A source defined in `IdSource` but not yet wired into `_PRECEDENCE`
    (a future classifier, e.g. BATTYBIRDNET) must not crash the sort."""
    manual = _ident(IdSource.MANUAL)
    future = _ident(IdSource.BATTYBIRDNET)

    assert sort_identifications_by_precedence([future, manual]) == [manual, future]
```

- [ ] **Step 2: Write a new test asserting `BATDETECT2`'s actual position**

```python
def test_batdetect2_outranks_fledermap_noise_but_not_emt_sources() -> None:
    manual = _ident(IdSource.MANUAL)
    emt_filename = _ident(IdSource.EMT_FILENAME)
    batdetect2 = _ident(IdSource.BATDETECT2)
    noise = _ident(IdSource.FLEDERMAP_NOISE)

    assert sort_identifications_by_precedence(
        [noise, batdetect2, emt_filename, manual],
    ) == [manual, emt_filename, batdetect2, noise]
```

- [ ] **Step 3: Run to verify both tests fail correctly**

Run: `hatch test tests/test_current_best.py -v`
Expected: the updated placeholder test still PASSes (no code change needed for it yet), the new test FAILs (`BATDETECT2` currently sorts last, same as `FLEDERMAP_NOISE`, so the assertion order doesn't hold — confirm by reading the actual failure, don't assume).

- [ ] **Step 4: Update `_PRECEDENCE`**

In `src/fledermap/services/current_best.py`:

```python
_PRECEDENCE: tuple[IdSource, ...] = (
    IdSource.MANUAL,
    IdSource.EMT_MANUAL,
    IdSource.EMT_GUANO,
    IdSource.EMT_WAMD,
    IdSource.EMT_FILENAME,
    IdSource.BATDETECT2,
    IdSource.FLEDERMAP_NOISE,
)
```

- [ ] **Step 5: Run to verify both tests pass**

Run: `hatch test tests/test_current_best.py -v`
Expected: PASS, entire file.

- [ ] **Step 6: Run the full non-db suite and mypy**

Run: `hatch test -m "not db"` and `hatch run types:check`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/fledermap/services/current_best.py tests/test_current_best.py
git commit -m "feat: add BATDETECT2 to current_best.py precedence order"
```

---

### Task 8: Real species-code mapping in `taxa_eu.yaml`

**Files:**
- Modify: `src/fledermap/store/data/taxa_eu.yaml`

**Interfaces:** none (data-only change; `store/seed.py` already handles any `codes.<source>` key generically, per Task 3's global-constraints note — confirmed by reading `seed_taxonomy`, no code change needed here).

- [ ] **Step 1: Inspect the real bundled checkpoint's class list**

Using the `batdetect2` hatch env from Task 1, write a throwaway script (per this project's "put throwaway code in a file, not a `hatch run python -c`" rule for anything beyond a one-liner) at `scripts/inspect_batdetect2_classes.py`:

```python
from batdetect2 import api

model_api = api.load_model()  # check batdetect2's actual API surface at
                               # implementation time -- `api.from_checkpoint()`
                               # was reported in this project's own research,
                               # but confirm the real current function name and
                               # signature against the installed package
                               # (`python -c "from batdetect2 import api; help(api)"`)
                               # rather than trusting that name blindly.
print(sorted(model_api.class_names))
```

Run: `hatch run batdetect2:python scripts/inspect_batdetect2_classes.py`

Record the exact printed list. Delete `scripts/inspect_batdetect2_classes.py` afterward (or keep it if the plan's own re-check step below is expected to be re-run later — either way, this file is dev-only inspection tooling, not part of the shipped feature, so it does not belong under `src/`).

- [ ] **Step 2: Cross-check the printed list against the 17-species scoping from the design spec**

Confirm the printed list has the expected count and that every name plausibly corresponds to one of: greater/lesser horseshoe, Alcathoe, Bechstein's, Brandt's, Daubenton's, Natterer's, whiskered, noctule, Leisler's, common/soprano/Nathusius' pipistrelle, serotine, barbastelle, brown/grey long-eared bat. If the real list differs meaningfully from this (extra/missing species, unexpected naming), stop and flag it rather than proceeding — this is exactly the kind of surprise the spec called out as needing real verification rather than research-based assumption.

- [ ] **Step 3: Add `batdetect2:` codes to `taxa_eu.yaml`**

For each of the confirmed species, add a `batdetect2:` key to that taxon's existing `codes:` dict, using the **exact string printed in Step 1**, e.g.:

```yaml
  - scientific_name: Pipistrellus pipistrellus
    rank: species
    common_name_de: Zwergfledermaus
    common_name_en: Common pipistrelle
    codes: {emt: PIPPIP, batdetect2: "Pipistrellus pipistrellus"}
```

Do this for all ~17 matched taxa. If any printed class name doesn't correspond to an existing taxon in `taxa_eu.yaml` (e.g. a naming convention mismatch), do not invent or force a match — leave it unmapped and note it, matching this project's "an unmapped label is not a failure" convention (spec section 5 / this feature's spec §3).

- [ ] **Step 4: Write a test asserting the codes resolve**

Add to whichever test file already covers `resolve_code`/`seed_taxonomy` against the real bundled YAML (e.g. `tests/test_seed.py` — check first):

```python
def test_batdetect2_common_pipistrelle_code_resolves(session: OrmSession) -> None:
    from fledermap.store.seed import resolve_code, seed_taxonomy

    seed_taxonomy(session)
    taxon = resolve_code(session, "batdetect2", "Pipistrellus pipistrellus")
    assert taxon is not None
    assert taxon.scientific_name == "Pipistrellus pipistrellus"
```

(Adjust the literal code string to whatever Step 1 actually printed for this species, if it differs from the plain scientific name.)

- [ ] **Step 5: Run to verify it passes**

Run: `hatch test tests/test_seed.py -v` (or the correct file found in Step 4) (`dangerouslyDisableSandbox: true` if it's DB-marked; check the file's existing `pytestmark`)
Expected: PASS.

- [ ] **Step 6: Run the full non-db suite, db suite, and mypy**

Run: `hatch test -m "not db"`, `hatch test -m db` (`dangerouslyDisableSandbox: true`), `hatch run types:check`, and the yaml-syntax pre-commit hook (`hatch run` whatever `check_yaml.py` invokes, or just let the commit's pre-commit hook catch it).
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add src/fledermap/store/data/taxa_eu.yaml tests/test_seed.py
git commit -m "feat: map batdetect2's real class names to existing EU taxa"
```

---

### Task 9: Documentation — licensing notice and config reference

**Files:**
- Modify: `README.md` (or wherever this project documents optional features — check first)
- Modify: `docs/reference/configuration.md`

**Interfaces:** none.

- [ ] **Step 1: Add the config reference entries**

In `docs/reference/configuration.md`, following its existing per-setting format (check `noise_snr_threshold_db`'s entry for the exact structure/heading style), add entries for `FLEDERMAP_BATDETECT2_ENABLED` and `FLEDERMAP_BATDETECT2_THRESHOLD`, each stating: default value, valid range, and — for `_ENABLED` specifically — a clear statement that turning this on installs and runs a CC-BY-NC-4.0-licensed model restricted to non-commercial use, with a pointer to `pip install fledermap[batdetect2]` being required separately.

- [ ] **Step 2: Add a licensing callout wherever this project documents optional features/extras**

Find the right existing section (search `README.md` and `docs/` for how `poiidx`/`FLEDERMAP_STATIC_ROOT`-style optional integrations are already introduced to a reader) and add a short paragraph: batdetect2 support is optional (`pip install fledermap[batdetect2]`), off by default, and licensed CC-BY-NC-4.0 (non-commercial use only) — attributing the batdetect2 project and linking its repository.

- [ ] **Step 3: Commit**

```bash
git add README.md docs/reference/configuration.md
git commit -m "docs: document FLEDERMAP_BATDETECT2_ENABLED/_THRESHOLD and its license"
```

---

## Notes for the implementer on things this plan deliberately leaves to judgment

- Exact fixture names (`session`, `engine`, `minimal_env`, `minimal_config`, whatever helper builds a fake `Recording`) are called out throughout as "check the existing file first" rather than invented, because this codebase already has established conventions in `tests/conftest.py` and each test file — copy those exactly rather than introducing a second convention.
- The `batdetect2.api` surface described in Tasks 5 and 8 (`process_file`, `.detections`, `.detection_score`, `.class_scores`, `load_model`/`from_checkpoint`) is based on this feature's design-spec research, not a guarantee — the actual installed package's real API must be checked (`python -c "from batdetect2 import api; help(api)"` in the `batdetect2` hatch env) before writing the real implementation, and adjusted if it differs.
- Task 5's queue name `"classify"` for `classify_batdetect2_task` is new (existing tasks use `"media"`, `"geo"`, `"ingest"`, `"maintenance"`). Confirmed no wiring is needed for it: `cli/main.py`'s `_run_worker_async` calls `worker_app.run_worker_async(...)` with no `queues=` argument, so Procrastinate listens on every queue by default — a new queue name needs no registration anywhere.
