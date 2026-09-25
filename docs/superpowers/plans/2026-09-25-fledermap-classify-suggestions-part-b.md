# Classify-box likely-suggestions (part b): mined implications Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Mine historical MANUAL-vs-automatic-classifier patterns into Horn-clause implication
rules ("if the automatic classifiers found species {X, Y}, the user has historically picked Z"),
store them, and surface them as one more candidate-generating source in the Classify box's
existing "likely suggestions" pool from part (a) — refreshed daily and at every worker startup.

**Architecture:** A new `classify_implication_rule` table holds rules mined offline by
`services/classify_implications.py`'s `mine_classify_implications` (full rebuild every run —
no incremental updates). The same module's `lookup_mined_suggestions` does the cheap indexed
read at suggestion time. `services/classify_suggestions.py`'s existing `classify_suggestions()`
entrypoint (part a) gains an optional `mined` parameter to merge these in, plus part (b)'s
"Noise, if there's room" pool-assembly fallback. A daily Procrastinate periodic task and an
explicit unconditional defer at `fledermap worker` startup both drive the same mining job — the
same daily-cron-plus-startup-catchup shape `run_ingest_cycle` already uses, except the startup
side is an explicit `defer_async()` call rather than relying on Procrastinate's own periodic
catch-up (too short a window — 10 minutes — for a daily job on a machine that isn't always on).

**Tech Stack:** SQLAlchemy + Alembic (migration), Flask/Jinja (unchanged view wiring), pytest
(`db`-marked tests need a real PostGIS testcontainer), Procrastinate (periodic task).

**Spec:** `docs/superpowers/specs/2026-09-25-fledermap-classify-suggestions-part-b-design.md`
(part b). Builds on part (a)'s already-implemented
`docs/superpowers/specs/2026-09-25-fledermap-classify-suggestions-design.md` and its plan,
`docs/superpowers/plans/2026-09-25-fledermap-classify-suggestions.md` (both already merged to
`main`).

## Global Constraints

- Antecedent atoms are species-only, source-agnostic (spec §1): "X was claimed by some automatic
  classifier," never "which classifier claimed X."
- Antecedent size is capped at 2 atoms — every mining/lookup subset enumeration only ever
  produces size-1 and size-2 subsets, nothing larger, no configurable cap.
- A candidate rule must clear all three gates to be stored: `min_support`, `min_confidence`, AND
  `min_lift` (spec §3) — `min_lift` exists specifically to reject a rule that only looks
  confident because its conclusion is common everywhere, not because the antecedent is
  informative. Never store a rule that fails any one of the three.
- `classify_implication_rule` is rebuilt from scratch on every mining run (spec §4/§6) — no
  incremental/dirty-tracked updates, no partial upserts.
- Mined candidates always sort strictly below part (a)'s two fixed heuristics regardless of
  their own confidence: `priority = confidence * 0.5`, giving a ceiling of 0.5, safely under
  `_PRIORITY_NOISE`'s 1.0 (spec §5).
- The "Noise, if there's room" fallback (spec §7) is a pool-assembly step applied AFTER priority
  sort + `pool_cap` truncation, not a new heuristic with its own priority score — it only fires
  when the capped pool has spare room AND no Noise candidate is already present.
- Every `Config` field added here follows the existing env-var-or-TOML-file pattern
  (`_KNOWN_FILE_KEYS`, `_lookup`, `_source_label`) — no field is env-var-only.
- The daily periodic task and the explicit worker-startup defer are two independent mechanisms,
  both required (spec §6) — do not collapse them into "cron with catch-up" the way
  `run_ingest_cycle` gets away with, because a daily cron's `max_delay` catch-up window (10
  minutes) is nowhere near enough for a machine that might be off for a day.

## Review Focus

- A training example whose automatic atom set has 3+ distinct taxa must still only ever produce
  size-1/size-2 antecedent subsets — never a size-3 key — verified in Task 3.
- A conclusion that is common across most training examples regardless of antecedent (the
  base-rate-dominant-species case the spec's `min_lift` gate exists to catch) must be excluded
  from the stored rule set even though it clears `min_confidence` — verified in Task 3.
- Re-running the mining job after new MANUAL data lands must fully replace the previous rule
  set, including removing a rule that no longer qualifies — never leave a stale row behind —
  verified in Task 3.
- A recording whose automatic atom set matches no stored rule must get an empty mined-candidate
  list, not an error or a fallback to an unrelated rule — verified in Task 4.
- The "Noise, if there's room" fallback must never override or duplicate an evidence-driven
  Noise entry already produced by part (a)'s own heuristic — verified in Task 5.
- `fledermap worker` startup must defer the mining task exactly once per startup, independent of
  whatever the daily cron's own schedule would otherwise do — verified in Task 6.

---

## Task 1: `ClassifyImplicationRule` model + migration

**Files:**
- Modify: `src/fledermap/store/models.py` (new model, appended after `SessionMergeProposal`)
- Create: `src/fledermap/alembic/versions/<generated>_add_classify_implication_rule_table.py`
  (via `alembic revision --autogenerate`)
- Modify: `tests/test_migrations.py` (CHECK-constraint behavioral test pair for
  `conclusion_verdict`, mirroring the existing `seen_visually` pair)

**Interfaces:**
- Produces: `ClassifyImplicationRule` ORM model (`id`, `antecedent_key: str`,
  `conclusion_verdict: Verdict`, `conclusion_taxon_id: int | None`, `support: int`,
  `confidence: float`, `lift: float`) — Task 3's mining function writes rows shaped exactly
  like this; Task 4's lookup function reads them.

- [ ] **Step 1: Add the model to `store/models.py`**

Append after the `SessionMergeProposal` class (end of file):

```python
class ClassifyImplicationRule(Base):
    """One mined association rule for the Classify box's "likely suggestions" feature, part
    (b) (docs/superpowers/specs/2026-09-25-fledermap-classify-suggestions-part-b-design.md).
    Rebuilt from scratch by services/classify_implications.py's mine_classify_implications on
    every mining run -- never incrementally updated (spec §4/§6)."""

    __tablename__ = "classify_implication_rule"
    __table_args__ = (
        # Postgres treats NULLs as distinct by default -- without this, a NO_ID/NOISE
        # conclusion (conclusion_taxon_id IS NULL) for the same antecedent_key could get
        # duplicate rows across two mining runs that raced, same gotcha as
        # Identification.uq_identification_source_claim.
        UniqueConstraint(
            "antecedent_key",
            "conclusion_verdict",
            "conclusion_taxon_id",
            name="uq_classify_implication_rule_antecedent_conclusion",
            postgresql_nulls_not_distinct=True,
        ),
        Index("ix_classify_implication_rule_antecedent_key", "antecedent_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # Sorted taxon_ids, comma-joined (e.g. "1" or "1,24") -- see
    # services/classify_implications.py's antecedent_key() for the canonical form.
    # String(64) is generous headroom for the size-2 cap: two 9-digit ids plus a
    # separator is 19 chars.
    antecedent_key: Mapped[str] = mapped_column(String(64))
    conclusion_verdict: Mapped[Verdict] = mapped_column(
        SAEnum(
            Verdict,
            native_enum=False,
            length=16,
            create_constraint=True,
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
        ),
    )
    # Set only when conclusion_verdict == SPECIES.
    conclusion_taxon_id: Mapped[int | None] = mapped_column(ForeignKey("taxon.id"))
    support: Mapped[int] = mapped_column(Integer)
    confidence: Mapped[float] = mapped_column(Float)
    lift: Mapped[float] = mapped_column(Float)
```

- [ ] **Step 2: Generate the migration**

Run (unsandboxed — see CLAUDE.md's "Environment gotchas" on `git`/DB commands):

```bash
hatch run alembic revision --autogenerate -m "add classify implication rule table"
```

Open the generated file under `src/fledermap/alembic/versions/`. Its `down_revision` must be
`"486bd52dcc0b"` (the current head, `add_annotation_table.py` — confirmed by `grep -L
down_revision` finding no migration that already claims it as a parent). Verify the generated
`upgrade()` body matches this shape (field order doesn't matter, but every column/constraint/
index below must be present) — this is the same "new table" shape as
`486bd52dcc0b_add_annotation_table.py`:

```python
def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "classify_implication_rule",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("antecedent_key", sa.String(length=64), nullable=False),
        sa.Column(
            "conclusion_verdict",
            sa.Enum(
                "species",
                "no_id",
                "noise",
                name="verdict",
                native_enum=False,
                length=16,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("conclusion_taxon_id", sa.Integer(), nullable=True),
        sa.Column("support", sa.Integer(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("lift", sa.Float(), nullable=False),
        sa.CheckConstraint(
            "conclusion_verdict IN ('species', 'no_id', 'noise')",
            name=op.f("ck_classify_implication_rule_verdict"),
        ),
        sa.ForeignKeyConstraint(["conclusion_taxon_id"], ["taxon.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "antecedent_key",
            "conclusion_verdict",
            "conclusion_taxon_id",
            name="uq_classify_implication_rule_antecedent_conclusion",
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_index(
        op.f("ix_classify_implication_rule_antecedent_key"),
        "classify_implication_rule",
        ["antecedent_key"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        op.f("ix_classify_implication_rule_antecedent_key"),
        table_name="classify_implication_rule",
    )
    op.drop_table("classify_implication_rule")
```

The exact CHECK constraint name/clause and whether autogenerate names it something slightly
different is fine — `tests/test_migrations.py`'s `_enum_check_constraints()` excludes it from
drift comparison generically by table+type name, not by a hardcoded constraint name. If
autogenerate did NOT emit a CHECK constraint for `conclusion_verdict` at all, add
`op.create_check_constraint(...)` by hand (CLAUDE.md's "Adding a CHECK to an EXISTING enum
column must be hand-written" note doesn't apply here — this is a new column on a new table, so
autogenerate should emit it — but verify).

- [ ] **Step 3: Add the CHECK-constraint behavioral test pair**

`tests/test_migrations.py`'s `_comparable()`/`_enum_check_constraints()` already exclude this
new CHECK generically (it scans `Base.metadata` for every non-native `Enum` with
`create_constraint=True`), so `test_migration_matches_the_models` needs no change. But per
CLAUDE.md's migrations rules, the CHECK itself still needs its own behavioral proof — add,
alongside the existing `test_migrated_verdict_check_is_enforced`/
`test_migrated_verdict_check_accepts_every_verdict` pair:

```python
def test_migrated_classify_implication_rule_conclusion_verdict_check_is_enforced(
    migrated_engine: Engine,
) -> None:
    """conclusion_verdict reuses Verdict's closed vocabulary -- same CHECK-exclusion shape as
    Identification.verdict above, so it needs the same direct proof."""
    with pytest.raises(IntegrityError), migrated_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO classify_implication_rule"
                " (antecedent_key, conclusion_verdict, support, confidence, lift)"
                " VALUES ('1', 'not_a_verdict', 3, 0.7, 1.5)"
            )
        )


def test_migrated_classify_implication_rule_conclusion_verdict_check_accepts_every_verdict(
    migrated_engine: Engine,
) -> None:
    with migrated_engine.begin() as conn:
        for i, verdict in enumerate(Verdict):
            conn.execute(
                text(
                    "INSERT INTO classify_implication_rule"
                    " (antecedent_key, conclusion_verdict, support, confidence, lift)"
                    " VALUES (:key, :verdict, 3, 0.7, 1.5)"
                ),
                {"key": str(i), "verdict": verdict.value},
            )
        stored = conn.scalar(text("SELECT count(*) FROM classify_implication_rule"))
    assert stored == len(Verdict)
```

- [ ] **Step 4: Run the migration test suite**

Run (unsandboxed — needs Docker/PostGIS, see CLAUDE.md's "Environment gotchas"):
`hatch test tests/test_migrations.py -m db -v`
Expected: all PASS, including the two new tests and the unchanged `test_migration_matches_the_models`.

- [ ] **Step 5: Commit**

```bash
git add src/fledermap/store/models.py src/fledermap/alembic/versions/ tests/test_migrations.py
git commit -m "feat: add classify_implication_rule table for mined suggestions"
```

---

## Task 2: `Config` fields for the three mining thresholds

**Files:**
- Modify: `src/fledermap/config.py`
- Modify: `docs/reference/configuration.md`
- Modify: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `Config.classify_implication_min_support: int` (default 3),
  `Config.classify_implication_min_confidence: float` (default 0.6),
  `Config.classify_implication_min_lift: float` (default 1.3) — Task 3's mining function reads
  all three from the `Config` it's given.

- [ ] **Step 1: Add the three env var constants**

In `config.py`, after `ENV_COMBINE_EMT_DISPLAY`:

```python
ENV_CLASSIFY_IMPLICATION_MIN_SUPPORT = "FLEDERMAP_CLASSIFY_IMPLICATION_MIN_SUPPORT"
ENV_CLASSIFY_IMPLICATION_MIN_CONFIDENCE = "FLEDERMAP_CLASSIFY_IMPLICATION_MIN_CONFIDENCE"
ENV_CLASSIFY_IMPLICATION_MIN_LIFT = "FLEDERMAP_CLASSIFY_IMPLICATION_MIN_LIFT"
```

- [ ] **Step 2: Add the three keys to `_KNOWN_FILE_KEYS`**

```python
        "classify_implication_min_support",
        "classify_implication_min_confidence",
        "classify_implication_min_lift",
```

(inserted into the existing `frozenset({...})` literal, anywhere — order doesn't matter.)

- [ ] **Step 3: Add the three `Config` dataclass fields**

After `combine_emt_display: bool = True`:

```python
    # Association-rule mining thresholds for the Classify box's mined "likely suggestions"
    # (design spec 2026-09-25-fledermap-classify-suggestions-part-b-design.md §3). A rule
    # must clear all three to be stored.
    classify_implication_min_support: int = 3
    classify_implication_min_confidence: float = 0.6
    # Rejects a rule that only looks confident because its conclusion is common everywhere
    # (the base-rate-dominant-species failure mode part b's spec §1 walks through), not
    # because the antecedent is actually informative about it. lift = confidence / base_rate;
    # 1.3 means "at least 30% more likely than picking this conclusion blind."
    classify_implication_min_lift: float = 1.3
```

- [ ] **Step 4: Add the three parsing blocks in `from_env`**

After the `combine_emt_display_raw = _lookup(...)` block's `combine_emt_display` resolution,
before `battybirdnet_url_raw = _lookup(...)`:

```python
        classify_implication_min_support_raw = _lookup(
            ENV_CLASSIFY_IMPLICATION_MIN_SUPPORT,
            "classify_implication_min_support",
            file_values,
        )
        if classify_implication_min_support_raw is None:
            classify_implication_min_support = 3
        else:
            label = _source_label(
                ENV_CLASSIFY_IMPLICATION_MIN_SUPPORT,
                "classify_implication_min_support",
                config_path,
            )
            if isinstance(classify_implication_min_support_raw, bool) or isinstance(
                classify_implication_min_support_raw,
                float,
            ):
                msg = f"{label}={classify_implication_min_support_raw!r} is not an integer."
                raise ConfigError(msg)
            try:
                classify_implication_min_support = int(classify_implication_min_support_raw)
            except (TypeError, ValueError) as exc:
                msg = f"{label}={classify_implication_min_support_raw!r} is not an integer."
                raise ConfigError(msg) from exc
            if classify_implication_min_support < 1:
                msg = (
                    f"{label}={classify_implication_min_support_raw!r} is not an "
                    "integer of 1 or more."
                )
                raise ConfigError(msg)

        classify_implication_min_confidence_raw = _lookup(
            ENV_CLASSIFY_IMPLICATION_MIN_CONFIDENCE,
            "classify_implication_min_confidence",
            file_values,
        )
        if classify_implication_min_confidence_raw is None:
            classify_implication_min_confidence = 0.6
        else:
            label = _source_label(
                ENV_CLASSIFY_IMPLICATION_MIN_CONFIDENCE,
                "classify_implication_min_confidence",
                config_path,
            )
            if isinstance(classify_implication_min_confidence_raw, bool):
                msg = f"{label}={classify_implication_min_confidence_raw!r} is not a number."
                raise ConfigError(msg)
            try:
                classify_implication_min_confidence = float(
                    classify_implication_min_confidence_raw
                )
            except (TypeError, ValueError) as exc:
                msg = f"{label}={classify_implication_min_confidence_raw!r} is not a number."
                raise ConfigError(msg) from exc
            if not 0.0 <= classify_implication_min_confidence <= 1.0:
                msg = (
                    f"{label}={classify_implication_min_confidence_raw!r} "
                    "must be between 0 and 1."
                )
                raise ConfigError(msg)

        classify_implication_min_lift_raw = _lookup(
            ENV_CLASSIFY_IMPLICATION_MIN_LIFT,
            "classify_implication_min_lift",
            file_values,
        )
        if classify_implication_min_lift_raw is None:
            classify_implication_min_lift = 1.3
        else:
            label = _source_label(
                ENV_CLASSIFY_IMPLICATION_MIN_LIFT,
                "classify_implication_min_lift",
                config_path,
            )
            if isinstance(classify_implication_min_lift_raw, bool):
                msg = f"{label}={classify_implication_min_lift_raw!r} is not a number."
                raise ConfigError(msg)
            try:
                classify_implication_min_lift = float(classify_implication_min_lift_raw)
            except (TypeError, ValueError) as exc:
                msg = f"{label}={classify_implication_min_lift_raw!r} is not a number."
                raise ConfigError(msg) from exc
            # No upper bound: unlike confidence, lift has no natural ceiling (an antecedent
            # that's a near-perfect predictor of a rare conclusion has arbitrarily high
            # lift). Only reject non-positive values -- lift <= 0 is meaningless (confidence
            # and base_rate are both >= 0).
            if not classify_implication_min_lift > 0:
                msg = (
                    f"{label}={classify_implication_min_lift_raw!r} is not a "
                    "positive number."
                )
                raise ConfigError(msg)
```

- [ ] **Step 5: Wire the three fields into the final `cls(...)` call**

Add to the `return cls(...)` block in `from_env` (anywhere among the keyword arguments):

```python
            classify_implication_min_support=classify_implication_min_support,
            classify_implication_min_confidence=classify_implication_min_confidence,
            classify_implication_min_lift=classify_implication_min_lift,
```

- [ ] **Step 6: Add the three doc rows to `docs/reference/configuration.md`**

Add to the settings table (after the `combine_emt_display` row):

```markdown
| Mined-suggestion minimum support | `classify_implication_min_support` | `FLEDERMAP_CLASSIFY_IMPLICATION_MIN_SUPPORT` | no | `3` |
| Mined-suggestion minimum confidence | `classify_implication_min_confidence` | `FLEDERMAP_CLASSIFY_IMPLICATION_MIN_CONFIDENCE` | no | `0.6` |
| Mined-suggestion minimum lift | `classify_implication_min_lift` | `FLEDERMAP_CLASSIFY_IMPLICATION_MIN_LIFT` | no | `1.3` |
```

Also add commented-out example lines to the "Example config file" section, matching the style
of the existing `# noise_snr_threshold_db = 2.0` / `# site_min_points = 3` lines:

```toml
# classify_implication_min_support = 3
# classify_implication_min_confidence = 0.6
# classify_implication_min_lift = 1.3
```

- [ ] **Step 7: Write the three tests**

Add to `tests/test_config.py`, following `test_port_is_configurable_via_env`'s exact shape
(`monkeypatch.setenv` for `ENV_DATABASE_URL`/`ENV_ARCHIVE_ROOTS`/`ENV_MEDIA_ROOT` first, same
as every other env-var test in that file):

```python
def test_classify_implication_min_support_is_configurable_via_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_MEDIA_ROOT, str(tmp_path / "media"))
    monkeypatch.setenv(ENV_CLASSIFY_IMPLICATION_MIN_SUPPORT, "5")
    config = Config.from_env()
    assert config.classify_implication_min_support == 5


def test_classify_implication_min_confidence_is_configurable_via_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_MEDIA_ROOT, str(tmp_path / "media"))
    monkeypatch.setenv(ENV_CLASSIFY_IMPLICATION_MIN_CONFIDENCE, "0.75")
    config = Config.from_env()
    assert config.classify_implication_min_confidence == 0.75


def test_classify_implication_min_lift_is_configurable_via_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))
    monkeypatch.setenv(ENV_MEDIA_ROOT, str(tmp_path / "media"))
    monkeypatch.setenv(ENV_CLASSIFY_IMPLICATION_MIN_LIFT, "2.0")
    config = Config.from_env()
    assert config.classify_implication_min_lift == 2.0
```

Add the matching imports (`ENV_CLASSIFY_IMPLICATION_MIN_SUPPORT`,
`ENV_CLASSIFY_IMPLICATION_MIN_CONFIDENCE`, `ENV_CLASSIFY_IMPLICATION_MIN_LIFT`) to
`test_config.py`'s existing `from fledermap.config import (...)` block.

- [ ] **Step 8: Run the test suite**

Run: `hatch test tests/test_config.py tests/test_configuration_docs.py -v`
Expected: all PASS (the `_KNOWN_FILE_KEYS`-vs-docs guard test included).

- [ ] **Step 9: Commit**

```bash
git add src/fledermap/config.py docs/reference/configuration.md tests/test_config.py
git commit -m "feat: add classify-implication mining threshold config fields"
```

---

## Task 3: Mining algorithm — `services/classify_implications.py`

**Files:**
- Create: `src/fledermap/services/classify_implications.py`
- Create: `tests/test_classify_implications.py`

**Interfaces:**
- Consumes: `fledermap.domain.codes.{IdSource, Verdict, is_manual}`,
  `fledermap.store.models.{ClassifyImplicationRule, Identification, Recording}`,
  `Config.classify_implication_min_support/min_confidence/min_lift` (Task 2).
- Produces: `antecedent_key(taxon_ids: Iterable[int]) -> str`,
  `subset_keys(taxon_ids: Iterable[int]) -> list[str]` (both pure, reused by Task 4's lookup),
  `mine_classify_implications(session: OrmSession, *, min_support: int, min_confidence: float,
  min_lift: float) -> int` (returns the number of rules stored, for the CLI/log line in Task 6).

- [ ] **Step 1: Write the failing tests for the pure subset-key helpers**

Create `tests/test_classify_implications.py`:

```python
from __future__ import annotations

from fledermap.services.classify_implications import antecedent_key, subset_keys


def test_antecedent_key_sorts_and_joins() -> None:
    assert antecedent_key([24, 1]) == "1,24"


def test_antecedent_key_single_taxon() -> None:
    assert antecedent_key([7]) == "7"


def test_subset_keys_of_one_atom_is_itself() -> None:
    assert subset_keys([5]) == ["5"]


def test_subset_keys_of_two_atoms_includes_both_singles_and_the_pair() -> None:
    keys = subset_keys([1, 2])
    assert set(keys) == {"1", "2", "1,2"}


def test_subset_keys_of_three_atoms_never_produces_a_size_three_key() -> None:
    """Antecedent size is capped at 2 (spec §1) -- every subset above size 2 must be
    excluded even when the recording's own atom set is larger."""
    keys = subset_keys([1, 2, 3])
    assert set(keys) == {"1", "2", "3", "1,2", "1,3", "2,3"}
    assert "1,2,3" not in keys


def test_subset_keys_of_empty_atoms_is_empty() -> None:
    assert subset_keys([]) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `hatch test tests/test_classify_implications.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'fledermap.services.classify_implications'`

- [ ] **Step 3: Implement the pure subset-key helpers**

Create `src/fledermap/services/classify_implications.py`:

```python
"""Mines the Classify box's "likely suggestions" implication rules from historical
MANUAL-vs-automatic-classifier patterns, part (b) of the classify-suggestions feature
(docs/superpowers/specs/2026-09-25-fledermap-classify-suggestions-part-b-design.md).

`mine_classify_implications` (offline, full rebuild every run -- spec §4/§6) and
`lookup_mined_suggestions` (suggestion-time, cheap indexed reads -- spec §5) share
`antecedent_key`/`subset_keys`: the canonical form a size-1/size-2 taxon-id subset maps to,
and the enumeration of every such subset from a recording's automatic-species atom set.
Antecedent size is capped at 2 (spec §1) -- neither function ever produces or looks up a
larger subset, regardless of how many atoms the recording actually has."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from itertools import combinations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import IdSource, Verdict, is_manual
from fledermap.services.classify_suggestions import ClassifySuggestion, SuggestedTaxon
from fledermap.store.models import ClassifyImplicationRule, Identification, Recording


def antecedent_key(taxon_ids: Iterable[int]) -> str:
    """Canonical form of a taxon-id subset: sorted, comma-joined. Shared by mining
    (storage key) and lookup (query key) -- they must agree exactly, or a mined rule
    becomes unreachable at suggestion time."""
    return ",".join(str(i) for i in sorted(set(taxon_ids)))


def subset_keys(taxon_ids: Iterable[int]) -> list[str]:
    """Every size-1 and size-2 subset of `taxon_ids`, as canonical keys -- never larger,
    regardless of how many distinct atoms `taxon_ids` holds (spec §1's antecedent-size
    cap). Order is not meaningful; callers that need determinism sort or set() this."""
    atoms = sorted(set(taxon_ids))
    keys: list[str] = []
    for size in (1, 2):
        for combo in combinations(atoms, size):
            keys.append(antecedent_key(combo))
    return keys
```

- [ ] **Step 4: Run the subset-key tests to verify they pass**

Run: `hatch test tests/test_classify_implications.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Write the failing tests for training-example decomposition and mining thresholds**

These tests need a real database (`db`-marked). Add `import pytest` and `pytestmark =
pytest.mark.db` near the top of `tests/test_classify_implications.py` (this marks every test
in the file, including the pure subset-key tests from Step 1 above, which is harmless — they
just don't happen to use the database), and consolidate every import used below into the
file's top-level imports rather than repeating inline `from ... import ...` lines inside each
test function (shown inline below only for clarity of which test needs what): `from datetime
import UTC, datetime`; `from sqlalchemy import delete, select`; `from sqlalchemy.orm import
Session as OrmSession`; `from fledermap.store.models import (ClassifyImplicationRule,
Identification, Recording, Taxon)`.

Append to `tests/test_classify_implications.py`:

```python
from fledermap.services.classify_implications import mine_classify_implications


def _taxon(session, name: str) -> Taxon:
    t = Taxon(rank="species", scientific_name=name)
    session.add(t)
    session.flush()
    return t


def _recording(session, audio_hash: str) -> Recording:
    r = Recording(
        audio_hash=audio_hash,
        path=f"{audio_hash}.wav",
        recorded_at=datetime(2026, 9, 1, tzinfo=UTC),
    )
    session.add(r)
    session.flush()
    return r


def _ident(
    session,
    recording: Recording,
    source: IdSource,
    *,
    verdict: Verdict = Verdict.SPECIES,
    taxon: Taxon | None = None,
) -> None:
    session.add(
        Identification(
            recording_id=recording.id,
            source=source,
            verdict=verdict,
            taxon_id=taxon.id if taxon is not None else None,
        ),
    )


def test_mining_ignores_recordings_with_no_manual_claim(engine) -> None:
    from sqlalchemy.orm import Session as OrmSession

    with OrmSession(engine) as session:
        pip = _taxon(session, "Pipistrellus pipistrellus")
        r = _recording(session, "a" * 64)
        _ident(session, r, IdSource.EMT_GUANO, taxon=pip)
        session.commit()

        count = mine_classify_implications(
            session, min_support=1, min_confidence=0.5, min_lift=1.0
        )
        session.commit()

        assert count == 0
        assert session.scalar(select(ClassifyImplicationRule).limit(1)) is None


def test_mining_stores_a_qualifying_single_atom_rule(engine) -> None:
    from sqlalchemy.orm import Session as OrmSession

    with OrmSession(engine) as session:
        pip = _taxon(session, "Pipistrellus pipistrellus")
        noc = _taxon(session, "Nyctalus noctula")
        # Three recordings: automatic claims Pip -> MANUAL always confirms Pip. A fourth,
        # unrelated recording keeps Pip's base rate from being 100% (so lift is finite and
        # meaningfully > 1, not a degenerate infinite/undefined case).
        for i in range(3):
            r = _recording(session, f"{i}" * 64)
            _ident(session, r, IdSource.EMT_GUANO, taxon=pip)
            _ident(session, r, IdSource.MANUAL, taxon=pip)
        r4 = _recording(session, "f" * 64)
        _ident(session, r4, IdSource.EMT_GUANO, taxon=noc)
        _ident(session, r4, IdSource.MANUAL, taxon=noc)
        session.commit()

        count = mine_classify_implications(
            session, min_support=3, min_confidence=0.6, min_lift=1.0
        )
        session.commit()

        assert count == 1
        rule = session.scalar(select(ClassifyImplicationRule))
        assert rule.antecedent_key == antecedent_key([pip.id])
        assert rule.conclusion_verdict == Verdict.SPECIES
        assert rule.conclusion_taxon_id == pip.id
        assert rule.support == 3
        assert rule.confidence == 1.0


def test_mining_excludes_rules_below_min_support(engine) -> None:
    from sqlalchemy.orm import Session as OrmSession

    with OrmSession(engine) as session:
        pip = _taxon(session, "Pipistrellus pipistrellus")
        r = _recording(session, "a" * 64)
        _ident(session, r, IdSource.EMT_GUANO, taxon=pip)
        _ident(session, r, IdSource.MANUAL, taxon=pip)
        session.commit()

        count = mine_classify_implications(
            session, min_support=3, min_confidence=0.6, min_lift=1.0
        )
        session.commit()

        assert count == 0


def test_mining_excludes_a_base_rate_dominant_conclusion_via_min_lift(engine) -> None:
    """The scenario spec §1's min_lift gate exists to catch: species D is the MANUAL pick
    in most examples regardless of antecedent, so an unrelated antecedent {X} still clears
    min_confidence for {X}->D purely from D's prevalence. A genuinely predictive rule in the
    same dataset (antecedent {Y}, whose conclusion rate is well above D's base rate) must
    still survive."""
    from sqlalchemy.orm import Session as OrmSession

    with OrmSession(engine) as session:
        d = _taxon(session, "Species D")
        x = _taxon(session, "Species X")
        y = _taxon(session, "Species Y")
        e = _taxon(session, "Species E")

        # 8 recordings total. D is the MANUAL pick in 6 of them (base rate 0.75) --
        # regardless of automatic antecedent. {X} co-occurs with D by pure prevalence
        # (3 of 4 {X} recordings conclude D, same as D's overall rate -- lift ~= 1).
        for i in range(3):
            r = _recording(session, f"x{i}" + "0" * 63)
            _ident(session, r, IdSource.EMT_GUANO, taxon=x)
            _ident(session, r, IdSource.MANUAL, taxon=d)
        r_x_e = _recording(session, "x" + "3" + "0" * 62)
        _ident(session, r_x_e, IdSource.EMT_GUANO, taxon=x)
        _ident(session, r_x_e, IdSource.MANUAL, taxon=e)

        # {Y} is genuinely predictive of D: every {Y} recording concludes D, well above
        # D's 0.75 overall base rate (this alone still wouldn't prove informativeness if D
        # were dominant everywhere -- lift measures the gap against the SAME base rate).
        for i in range(3):
            r = _recording(session, f"y{i}" + "0" * 63)
            _ident(session, r, IdSource.EMT_GUANO, taxon=y)
            _ident(session, r, IdSource.MANUAL, taxon=d)

        # One more D conclusion with no automatic claim at all, to keep D's base rate at
        # 6/8 = 0.75 rather than 6/7.
        r_plain = _recording(session, "p" * 64)
        _ident(session, r_plain, IdSource.MANUAL, taxon=d)

        session.commit()

        mine_classify_implications(session, min_support=1, min_confidence=0.6, min_lift=1.3)
        session.commit()

        rules = session.scalars(select(ClassifyImplicationRule)).all()
        keys_to_conclusions = {
            (r.antecedent_key, r.conclusion_taxon_id) for r in rules
        }
        # {X} -> D must be excluded: confidence 0.75 clears min_confidence, but lift
        # ~= 0.75 / 0.75 = 1.0, below min_lift=1.3.
        assert (antecedent_key([x.id]), d.id) not in keys_to_conclusions
        # {Y} -> D must survive: confidence 1.0, lift = 1.0 / 0.75 = 1.33... >= 1.3.
        assert (antecedent_key([y.id]), d.id) in keys_to_conclusions


def test_mining_rebuilds_from_scratch_removing_stale_rows(engine) -> None:
    """A rule that qualified on a previous run but no longer does must be gone after a
    fresh mining run -- no incremental updates, no dirty-tracking (spec §4)."""
    from sqlalchemy.orm import Session as OrmSession

    with OrmSession(engine) as session:
        pip = _taxon(session, "Pipistrellus pipistrellus")
        noc = _taxon(session, "Nyctalus noctula")
        for i in range(3):
            r = _recording(session, f"{i}" * 64)
            _ident(session, r, IdSource.EMT_GUANO, taxon=pip)
            _ident(session, r, IdSource.MANUAL, taxon=pip)
        session.commit()
        mine_classify_implications(session, min_support=3, min_confidence=0.6, min_lift=1.0)
        session.commit()
        assert session.scalar(select(ClassifyImplicationRule)) is not None

        # Wipe every Pip/MANUAL pairing and replace with disagreeing data, so the old
        # rule no longer qualifies.
        session.execute(delete(Identification))
        for i in range(3):
            r = _recording(session, f"g{i}" + "0" * 62)
            _ident(session, r, IdSource.EMT_GUANO, taxon=pip)
            _ident(session, r, IdSource.MANUAL, taxon=noc)
        session.commit()

        mine_classify_implications(session, min_support=3, min_confidence=0.6, min_lift=1.0)
        session.commit()

        rules = session.scalars(select(ClassifyImplicationRule)).all()
        assert len(rules) == 1
        assert rules[0].conclusion_taxon_id == noc.id


def test_multi_species_manual_claim_decomposes_into_one_rule_per_conclusion(engine) -> None:
    """Spec §2: automatic atoms {X, Y}, MANUAL claims {A, B} -> two training rows, {X,Y}->A
    and {X,Y}->B -- never a single multi-conclusion rule."""
    from sqlalchemy.orm import Session as OrmSession

    with OrmSession(engine) as session:
        x = _taxon(session, "Species X")
        y = _taxon(session, "Species Y")
        a = _taxon(session, "Species A")
        b = _taxon(session, "Species B")
        for i in range(3):
            r = _recording(session, f"{i}" * 64)
            _ident(session, r, IdSource.EMT_GUANO, taxon=x)
            _ident(session, r, IdSource.BATDETECT2, taxon=y)
            _ident(session, r, IdSource.MANUAL, taxon=a)
            _ident(session, r, IdSource.MANUAL, taxon=b)
        session.commit()

        mine_classify_implications(session, min_support=3, min_confidence=0.6, min_lift=1.0)
        session.commit()

        conclusions = {
            r.conclusion_taxon_id
            for r in session.scalars(select(ClassifyImplicationRule)).all()
            if r.antecedent_key == antecedent_key([x.id, y.id])
        }
        assert conclusions == {a.id, b.id}
```

(`engine` here is the module's existing `db`-marked fixture, per this project's
`tests/conftest.py` convention -- add `import pytest` and `pytestmark = pytest.mark.db` at the
top of the test file, matching `tests/test_migrations.py`'s own pattern, since every test above
needs a real database.)

- [ ] **Step 6: Run the mining tests to verify they fail**

Run: `hatch test tests/test_classify_implications.py -m db -v` (unsandboxed — Docker/PostGIS)
Expected: FAIL with `ImportError: cannot import name 'mine_classify_implications'`

- [ ] **Step 7: Implement the training-example decomposition and mining algorithm**

Append to `src/fledermap/services/classify_implications.py`:

```python
def _automatic_atoms(recording: Recording) -> frozenset[int]:
    """Every distinct taxon_id among this recording's automatic (non-`is_manual`) SPECIES
    claims (spec §2) -- an antecedent atom, source-agnostic (Global Constraints: never
    "which classifier claimed X")."""
    return frozenset(
        ident.taxon_id
        for ident in recording.identifications
        if not is_manual(ident.source)
        and ident.verdict == Verdict.SPECIES
        and ident.taxon_id is not None
    )


def _manual_conclusions(recording: Recording) -> list[tuple[Verdict, int | None]]:
    """Every MANUAL/EMT_MANUAL claim on this recording, as a (verdict, taxon_id) conclusion
    -- a genuine multi-species MANUAL call yields one entry per taxon, decomposed into
    independent training rows (spec §2), never a single multi-conclusion rule. A SPECIES
    claim with no resolved taxon_id is excluded -- there's nothing to conclude."""
    return [
        (ident.verdict, ident.taxon_id)
        for ident in recording.identifications
        if is_manual(ident.source)
        and (ident.verdict != Verdict.SPECIES or ident.taxon_id is not None)
    ]


@dataclass(frozen=True)
class _Counters:
    co_occurrence: dict[tuple[str, Verdict, int | None], int]
    antecedent_total: dict[str, int]
    conclusion_total: dict[tuple[Verdict, int | None], int]
    example_total: int


def _count_training_examples(recordings: Sequence[Recording]) -> _Counters:
    co_occurrence: dict[tuple[str, Verdict, int | None], int] = defaultdict(int)
    antecedent_total: dict[str, int] = defaultdict(int)
    conclusion_total: dict[tuple[Verdict, int | None], int] = defaultdict(int)
    example_total = 0

    for recording in recordings:
        atoms = _automatic_atoms(recording)
        keys = subset_keys(atoms)
        for verdict, taxon_id in _manual_conclusions(recording):
            example_total += 1
            conclusion_total[(verdict, taxon_id)] += 1
            for key in keys:
                co_occurrence[(key, verdict, taxon_id)] += 1
                antecedent_total[key] += 1

    return _Counters(
        co_occurrence=dict(co_occurrence),
        antecedent_total=dict(antecedent_total),
        conclusion_total=dict(conclusion_total),
        example_total=example_total,
    )


def mine_classify_implications(
    session: OrmSession,
    *,
    min_support: int,
    min_confidence: float,
    min_lift: float,
) -> int:
    """Full rebuild of `classify_implication_rule` (spec §4/§6: no incremental updates).
    Returns the number of rules stored."""
    manual_sources = (IdSource.MANUAL, IdSource.EMT_MANUAL)
    recordings = (
        session.scalars(
            select(Recording)
            .join(Identification, Identification.recording_id == Recording.id)
            .where(Identification.source.in_(manual_sources))
            .distinct(),
        )
        .unique()
        .all()
    )

    counters = _count_training_examples(recordings)

    session.execute(delete(ClassifyImplicationRule))

    stored = 0
    for (key, verdict, taxon_id), support in counters.co_occurrence.items():
        if support < min_support:
            continue
        confidence = support / counters.antecedent_total[key]
        if confidence < min_confidence:
            continue
        base_rate = counters.conclusion_total[(verdict, taxon_id)] / counters.example_total
        lift = confidence / base_rate
        if lift < min_lift:
            continue
        session.add(
            ClassifyImplicationRule(
                antecedent_key=key,
                conclusion_verdict=verdict,
                conclusion_taxon_id=taxon_id,
                support=support,
                confidence=confidence,
                lift=lift,
            ),
        )
        stored += 1

    return stored
```

- [ ] **Step 8: Run the mining tests to verify they pass**

Run: `hatch test tests/test_classify_implications.py -m db -v` (unsandboxed)
Expected: PASS (7 tests)

- [ ] **Step 9: Run mypy**

Run: `hatch run types:check`
Expected: no errors in the new file or its test.

- [ ] **Step 10: Commit**

```bash
git add src/fledermap/services/classify_implications.py tests/test_classify_implications.py
git commit -m "feat: mine classify-box implication rules from MANUAL/automatic history"
```

---

## Task 4: Suggestion-time lookup — `lookup_mined_suggestions`

**Files:**
- Modify: `src/fledermap/services/classify_implications.py`
- Modify: `tests/test_classify_implications.py`

**Interfaces:**
- Consumes: `subset_keys` (Task 3, same file), `ClassifyImplicationRule` (Task 1),
  `ClassifySuggestion`/`SuggestedTaxon` (`services/classify_suggestions.py`, part a — already
  imported at the top of the file from Task 3's Step 3).
- Produces: `lookup_mined_suggestions(session: OrmSession, recording: Recording) ->
  list[ClassifySuggestion]` — Task 5's view wiring calls this once per render, same as part
  (a)'s `classify_suggestions()` call.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_classify_implications.py`:

```python
from fledermap.services.classify_implications import lookup_mined_suggestions


def test_lookup_returns_nothing_when_no_rule_matches(engine) -> None:
    from sqlalchemy.orm import Session as OrmSession

    with OrmSession(engine) as session:
        pip = _taxon(session, "Pipistrellus pipistrellus")
        r = _recording(session, "a" * 64)
        _ident(session, r, IdSource.EMT_GUANO, taxon=pip)
        session.commit()

        assert lookup_mined_suggestions(session, r) == []


def test_lookup_finds_a_matching_single_atom_rule_and_scales_priority(engine) -> None:
    from sqlalchemy.orm import Session as OrmSession

    with OrmSession(engine) as session:
        pip = _taxon(session, "Pipistrellus pipistrellus")
        r = _recording(session, "a" * 64)
        _ident(session, r, IdSource.EMT_GUANO, taxon=pip)
        session.add(
            ClassifyImplicationRule(
                antecedent_key=antecedent_key([pip.id]),
                conclusion_verdict=Verdict.SPECIES,
                conclusion_taxon_id=pip.id,
                support=3,
                confidence=0.8,
                lift=1.5,
            ),
        )
        session.commit()

        suggestions = lookup_mined_suggestions(session, r)

        assert len(suggestions) == 1
        assert suggestions[0].is_noise is False
        assert suggestions[0].taxa == (
            SuggestedTaxon(id=pip.id, scientific_name="Pipistrellus pipistrellus"),
        )
        # priority = confidence * 0.5, ceiling 0.5 -- always below part (a)'s Noise (1.0).
        assert suggestions[0].priority == 0.4


def test_lookup_maps_a_noise_conclusion_to_the_noise_sentinel(engine) -> None:
    from sqlalchemy.orm import Session as OrmSession

    with OrmSession(engine) as session:
        pip = _taxon(session, "Pipistrellus pipistrellus")
        r = _recording(session, "a" * 64)
        _ident(session, r, IdSource.EMT_GUANO, taxon=pip)
        session.add(
            ClassifyImplicationRule(
                antecedent_key=antecedent_key([pip.id]),
                conclusion_verdict=Verdict.NOISE,
                conclusion_taxon_id=None,
                support=3,
                confidence=0.7,
                lift=1.4,
            ),
        )
        session.commit()

        suggestions = lookup_mined_suggestions(session, r)

        assert len(suggestions) == 1
        assert suggestions[0].is_noise is True
        assert suggestions[0].taxa == ()
        assert suggestions[0].label == "Noise"


def test_lookup_ranks_by_confidence_descending(engine) -> None:
    from sqlalchemy.orm import Session as OrmSession

    with OrmSession(engine) as session:
        pip = _taxon(session, "Pipistrellus pipistrellus")
        noc = _taxon(session, "Nyctalus noctula")
        r = _recording(session, "a" * 64)
        _ident(session, r, IdSource.EMT_GUANO, taxon=pip)
        session.add(
            ClassifyImplicationRule(
                antecedent_key=antecedent_key([pip.id]),
                conclusion_verdict=Verdict.SPECIES,
                conclusion_taxon_id=noc.id,
                support=3,
                confidence=0.6,
                lift=1.3,
            ),
        )
        session.add(
            ClassifyImplicationRule(
                antecedent_key=antecedent_key([pip.id]),
                conclusion_verdict=Verdict.SPECIES,
                conclusion_taxon_id=pip.id,
                support=5,
                confidence=0.9,
                lift=1.6,
            ),
        )
        session.commit()

        suggestions = lookup_mined_suggestions(session, r)

        assert [s.taxa[0].id for s in suggestions] == [pip.id, noc.id]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `hatch test tests/test_classify_implications.py -m db -v`
Expected: FAIL with `ImportError: cannot import name 'lookup_mined_suggestions'`

- [ ] **Step 3: Implement `lookup_mined_suggestions`**

Append to `src/fledermap/services/classify_implications.py`:

```python
_MINED_PRIORITY_SCALE = 0.5


def lookup_mined_suggestions(
    session: OrmSession,
    recording: Recording,
) -> list[ClassifySuggestion]:
    """Cheap indexed reads against already-mined rules (spec §5) -- the expensive
    computation happened at mining time (`mine_classify_implications`), not here. Ranked by
    confidence descending; every stored row already cleared `min_lift` at mining time, so no
    further lift filtering happens here."""
    atoms = _automatic_atoms(recording)
    keys = subset_keys(atoms)
    if not keys:
        return []

    rules = session.scalars(
        select(ClassifyImplicationRule)
        .where(ClassifyImplicationRule.antecedent_key.in_(keys))
        .order_by(
            ClassifyImplicationRule.confidence.desc(),
            ClassifyImplicationRule.support.desc(),
        ),
    ).all()

    suggestions: list[ClassifySuggestion] = []
    for rule in rules:
        priority = rule.confidence * _MINED_PRIORITY_SCALE
        if rule.conclusion_verdict != Verdict.SPECIES or rule.conclusion_taxon_id is None:
            suggestions.append(
                ClassifySuggestion(taxa=(), is_noise=True, label="Noise", priority=priority),
            )
            continue
        taxon = session.get(Taxon, rule.conclusion_taxon_id)
        if taxon is None:
            continue
        suggestions.append(
            ClassifySuggestion(
                taxa=(SuggestedTaxon(id=taxon.id, scientific_name=taxon.scientific_name),),
                is_noise=False,
                label=taxon.scientific_name,
                priority=priority,
            ),
        )
    return suggestions
```

Add `Taxon` to the existing `from fledermap.store.models import (...)` line at the top of the
file (Task 3's Step 3 already imports `ClassifyImplicationRule, Identification, Recording` from
there — extend that import to include `Taxon`).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `hatch test tests/test_classify_implications.py -m db -v`
Expected: PASS (11 tests total in the file)

- [ ] **Step 5: Run mypy**

Run: `hatch run types:check`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/fledermap/services/classify_implications.py tests/test_classify_implications.py
git commit -m "feat: look up mined classify-implication rules at suggestion time"
```

---

## Task 5: Extend `classify_suggestions()` with mined candidates + Noise-if-room fallback

**Files:**
- Modify: `src/fledermap/services/classify_suggestions.py`
- Modify: `tests/test_classify_suggestions.py`
- Modify: `src/fledermap/web/views/recording_detail.py`
- Modify: `src/fledermap/web/views/map.py`

**Interfaces:**
- Consumes: `lookup_mined_suggestions` (Task 4).
- Produces: `classify_suggestions(recording, *, noise_snr_threshold_db, pool_cap=8,
  mined=())` — a new keyword-only `mined: Sequence[ClassifySuggestion] = ()` parameter, and
  the "Noise, if there's room" fallback (spec §7) applied after the existing sort+cap.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_classify_suggestions.py`:

```python
from fledermap.services.classify_suggestions import ClassifySuggestion


def test_mined_candidates_are_merged_in_and_sorted_by_their_own_priority() -> None:
    r = _recording()
    mined = [
        ClassifySuggestion(
            taxa=(SuggestedTaxon(id=1, scientific_name="Myotis mystacinus"),),
            is_noise=False,
            label="Myotis mystacinus",
            priority=0.4,
        ),
        ClassifySuggestion(
            taxa=(SuggestedTaxon(id=2, scientific_name="Nyctalus noctula"),),
            is_noise=False,
            label="Nyctalus noctula",
            priority=0.2,
        ),
    ]

    suggestions = classify_suggestions(r, noise_snr_threshold_db=2.0, mined=mined)

    # Noise (part a's evidence-driven heuristic, priority 1.0) sorts first, then the two
    # mined candidates by their own priority.
    assert [s.label for s in suggestions] == ["Noise", "Myotis mystacinus", "Nyctalus noctula"]


def test_mined_candidates_never_outrank_part_a_heuristics() -> None:
    pip = _taxon(1, "Pipistrellus pipistrellus")
    r = _recording(_ident(IdSource.EMT_GUANO, taxon=pip))
    # A mined candidate with an implausibly high raw confidence still can't outrank "Agree
    # with classifiers" (priority 2.0): the *0.5 scale caps it at 0.5.
    mined = [
        ClassifySuggestion(
            taxa=(SuggestedTaxon(id=9, scientific_name="Species Z"),),
            is_noise=False,
            label="Species Z",
            priority=0.5,
        ),
    ]

    suggestions = classify_suggestions(r, noise_snr_threshold_db=2.0, mined=mined)

    assert suggestions[0].label == "Pipistrellus pipistrellus"


def test_noise_is_added_when_room_left_and_not_already_present() -> None:
    """Spec §7: after part (a)'s heuristics and mined candidates fill fewer than pool_cap
    slots, and none of them is already Noise, append one Noise candidate unconditionally."""
    pip = _taxon(1, "Pipistrellus pipistrellus")
    r = _recording(_ident(IdSource.EMT_GUANO, taxon=pip))  # no low SNR, something found
    mined = [
        ClassifySuggestion(
            taxa=(SuggestedTaxon(id=2, scientific_name="Myotis mystacinus"),),
            is_noise=False,
            label="Myotis mystacinus",
            priority=0.3,
        ),
    ]

    suggestions = classify_suggestions(
        r, noise_snr_threshold_db=2.0, mined=mined, pool_cap=8
    )

    assert any(s.is_noise for s in suggestions)
    assert suggestions[-1].is_noise  # lowest priority -> sorts last


def test_noise_fallback_never_duplicates_an_existing_noise_entry() -> None:
    r = _recording(snr_db=1.0)  # low SNR -- part (a)'s own Noise heuristic already fires

    suggestions = classify_suggestions(r, noise_snr_threshold_db=2.0, pool_cap=8)

    assert sum(1 for s in suggestions if s.is_noise) == 1


def test_noise_fallback_does_not_fire_when_pool_is_already_at_cap() -> None:
    pip = _taxon(1, "Pipistrellus pipistrellus")
    r = _recording(_ident(IdSource.EMT_GUANO, taxon=pip))
    mined = [
        ClassifySuggestion(
            taxa=(SuggestedTaxon(id=i, scientific_name=f"Species {i}"),),
            is_noise=False,
            label=f"Species {i}",
            priority=0.1,
        )
        for i in range(2, 3)
    ]

    suggestions = classify_suggestions(
        r, noise_snr_threshold_db=2.0, mined=mined, pool_cap=1
    )

    assert len(suggestions) == 1
    assert not suggestions[0].is_noise
```

Add `SuggestedTaxon` (already imported? check the existing top-of-file import — extend it) and
`ClassifySuggestion` to `tests/test_classify_suggestions.py`'s import line if not already there.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `hatch test tests/test_classify_suggestions.py -v`
Expected: FAIL — `classify_suggestions() got an unexpected keyword argument 'mined'`

- [ ] **Step 3: Implement the `mined` parameter and Noise-if-room fallback**

In `src/fledermap/services/classify_suggestions.py`, change the `classify_suggestions`
signature and body:

```python
def classify_suggestions(
    recording: Recording,
    *,
    noise_snr_threshold_db: float,
    pool_cap: int = 8,
    mined: Sequence[ClassifySuggestion] = (),
) -> list[ClassifySuggestion]:
    automatic_best = automatic_best_identification(recording)
    candidates: list[ClassifySuggestion] = []

    if automatic_best is not None and automatic_best.verdict == Verdict.SPECIES:
        taxa = sorted(
            (
                SuggestedTaxon(
                    id=claim.taxon_id, scientific_name=claim.taxon.scientific_name
                )
                for claim in automatic_best.claims
                if claim.taxon_id is not None and claim.taxon is not None
            ),
            key=lambda t: t.scientific_name,
        )
        if taxa:
            label = _label_for_taxa(taxa)
            candidates.append(
                ClassifySuggestion(
                    taxa=tuple(taxa),
                    is_noise=False,
                    label=label,
                    priority=_PRIORITY_AGREE_WITH_CLASSIFIERS,
                ),
            )

    low_snr = recording.snr_db is not None and recording.snr_db < noise_snr_threshold_db
    nothing_found = automatic_best is None or automatic_best.verdict == Verdict.NOISE
    if low_snr or nothing_found:
        candidates.append(
            ClassifySuggestion(
                taxa=(), is_noise=True, label="Noise", priority=_PRIORITY_NOISE
            ),
        )

    candidates.extend(mined)
    candidates.sort(key=lambda c: c.priority, reverse=True)
    candidates = candidates[:pool_cap]

    # Part (b) spec §7: a pool-assembly step, not a new heuristic -- fires only when the
    # capped pool has spare room AND doesn't already carry a Noise candidate.
    if len(candidates) < pool_cap and not any(c.is_noise for c in candidates):
        candidates.append(
            ClassifySuggestion(
                taxa=(), is_noise=True, label="Noise", priority=0.0
            ),
        )

    return candidates
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `hatch test tests/test_classify_suggestions.py -v`
Expected: PASS (all tests, existing + new)

- [ ] **Step 5: Wire mined suggestions into the two view call sites**

In `src/fledermap/web/views/recording_detail.py`, change `_classify_suggestions_data` to take
`session` and call `lookup_mined_suggestions`:

```python
from fledermap.services.classify_implications import lookup_mined_suggestions
```

(add to the existing import block, alphabetically ordered)

```python
def _classify_suggestions_data(
    session: OrmSession, recording: Recording
) -> list[dict[str, object]]:
    """Shared by the details page's initial render and the manual-classification save's
    `_classifier_box.html` fragment re-render (`web/views/map.py`'s
    `post_manual_classification`) -- both must embed the same suggestion pool."""
    mined = lookup_mined_suggestions(session, recording)
    suggestions = classify_suggestions(
        recording,
        noise_snr_threshold_db=flask.current_app.config["NOISE_SNR_THRESHOLD_DB"],
        mined=mined,
    )
    return [_serialize_classify_suggestion(s) for s in suggestions]
```

Update its call site inside `recording_details_page` (already inside a `with OrmSession(engine)
as session:` block):

```python
        suggestions = _classify_suggestions_data(session, recording)
```

- [ ] **Step 6: Update `map.py`'s call site**

In `src/fledermap/web/views/map.py`, change:

```python
                suggestions=_classify_suggestions_data(recording),
```

to:

```python
                suggestions=_classify_suggestions_data(session, recording),
```

(`session` is already in scope there — confirmed in `post_manual_classification`, same block
that builds `current_taxa`.)

- [ ] **Step 7: Run the full web test suite (excl. db) plus the db-marked classify/recording-detail tests**

Run: `hatch test -m "not db" -v`
Expected: PASS

Run: `hatch test tests/test_recording_detail_view.py tests/test_web_app.py -m db -v`
(unsandboxed — Docker/PostGIS)
Expected: PASS

- [ ] **Step 8: Run mypy**

Run: `hatch run types:check`
Expected: no errors.

- [ ] **Step 9: Commit**

```bash
git add src/fledermap/services/classify_suggestions.py tests/test_classify_suggestions.py \
  src/fledermap/web/views/recording_detail.py src/fledermap/web/views/map.py
git commit -m "feat: merge mined implication candidates into classify_suggestions"
```

---

## Task 6: Periodic mining task + CLI command + worker-startup hook

**Files:**
- Modify: `src/fledermap/jobs/tasks.py`
- Modify: `src/fledermap/cli/main.py`
- Modify: `tests/test_jobs_tasks.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Consumes: `mine_classify_implications` (Task 3), `Config.classify_implication_min_support/
  min_confidence/min_lift` (Task 2).
- Produces: `run_classify_implication_mining_cycle` (Procrastinate task, `"maintenance"`
  queue), `fledermap mine-classify-implications` (CLI command), an unconditional
  `defer_async()` call inside `_run_worker_async`.

- [ ] **Step 1: Add the periodic task**

In `src/fledermap/jobs/tasks.py`, add near the other lock/cron constants (after
`_CLEAN_MEDIA_CYCLE_CRON`):

```python
# Daily, off-peak, same reasoning as _CLEAN_MEDIA_CYCLE_CRON above -- mining is disk/CPU
# hygiene work with no reason to run more often than once a day at this project's real
# scale (spec §6).
_CLASSIFY_IMPLICATION_MINING_LOCK = "classify_implication_mining"
_CLASSIFY_IMPLICATION_MINING_CRON = "0 3 * * *"
```

Add the task function after `run_clean_media_cycle` (end of file):

```python
@app.periodic(
    cron=_CLASSIFY_IMPLICATION_MINING_CRON,
    lock=_CLASSIFY_IMPLICATION_MINING_LOCK,
    queueing_lock=_CLASSIFY_IMPLICATION_MINING_LOCK,
)
@app.task(queue="maintenance", pass_context=True)
def run_classify_implication_mining_cycle(
    context: procrastinate.JobContext, timestamp: int
) -> None:
    """The periodic half of the classify-implication mining feature (part b spec §6;
    `timestamp` unused -- see `run_ingest_cycle`'s docstring for why Procrastinate's
    periodic machinery requires it as the first parameter regardless).
    `fledermap mine-classify-implications` (`cli/main.py`) is the same work, on demand. Also
    deferred unconditionally at every `fledermap worker` startup (`cli/main.py`'s
    `_run_worker_async`) -- NOT relying on Procrastinate's own periodic catch-up, whose
    max_delay (10 minutes) is nowhere near enough for a daily job on a machine that isn't
    always on (spec §6).

    Shares the `"maintenance"` queue with `run_clean_media_cycle` -- both are low-priority,
    non-destructive-to-recording-data background jobs, same reasoning as that task's own
    docstring."""
    config: Config = context.additional_context["config"]
    engine = context.additional_context["engine"]

    from fledermap.services.classify_implications import mine_classify_implications

    with OrmSession(engine) as session:
        count = mine_classify_implications(
            session,
            min_support=config.classify_implication_min_support,
            min_confidence=config.classify_implication_min_confidence,
            min_lift=config.classify_implication_min_lift,
        )
        session.commit()

    logger.info("classify-implication mining cycle: stored %d rule(s)", count)
```

(Local import, same circularity reasoning as `run_ingest_cycle`'s/`run_clean_media_cycle`'s own
local imports — `services/classify_implications.py` does not import from `jobs/tasks.py`, but
following the established local-import convention for every `services.*` import inside a task
function keeps the pattern consistent and avoids ever having to reason about import order here
again.)

- [ ] **Step 2: Write the failing db-marked test for the periodic task**

Append to `tests/test_jobs_tasks.py`, following `test_run_clean_media_cycle_removes_an_orphaned_
directory`'s exact `.configure(...).defer(...)` + `_run_worker(...)` shape:

```python
def test_run_classify_implication_mining_cycle_stores_rules(engine: Engine) -> None:
    with OrmSession(engine) as session:
        pip = Taxon(rank="species", scientific_name="Pipistrellus pipistrellus")
        session.add(pip)
        session.flush()
        for i in range(3):
            r = Recording(
                audio_hash=f"{i}" * 64,
                path=f"{i}.wav",
                recorded_at=datetime(2026, 9, 1, tzinfo=UTC),
            )
            session.add(r)
            session.flush()
            session.add(
                Identification(
                    recording_id=r.id,
                    source=IdSource.EMT_GUANO,
                    verdict=Verdict.SPECIES,
                    taxon_id=pip.id,
                ),
            )
            session.add(
                Identification(
                    recording_id=r.id,
                    source=IdSource.MANUAL,
                    verdict=Verdict.SPECIES,
                    taxon_id=pip.id,
                ),
            )
        session.commit()

    jobs_app.open(engine)
    ensure_schema(jobs_app, engine)

    run_classify_implication_mining_cycle.configure(
        lock=_CLASSIFY_IMPLICATION_MINING_LOCK,
        queueing_lock=_CLASSIFY_IMPLICATION_MINING_LOCK,
    ).defer(timestamp=int(time.time()))
    config = Config(
        database_url="postgresql://unused/unused",  # never read by this task itself
        archive_roots=(),
        classify_implication_min_support=3,
        classify_implication_min_confidence=0.6,
        classify_implication_min_lift=1.0,
    )
    _run_worker(
        engine,
        wait=False,
        install_signal_handlers=False,
        listen_notify=False,
        queues=["maintenance"],
        additional_context={"config": config, "engine": engine},
    )

    with OrmSession(engine) as session:
        stored = session.scalar(select(func.count()).select_from(ClassifyImplicationRule))
    assert stored == 1
```

Add the needed imports to `tests/test_jobs_tasks.py`'s existing import block:
`run_classify_implication_mining_cycle`, `_CLASSIFY_IMPLICATION_MINING_LOCK` (from
`fledermap.jobs.tasks`), `ClassifyImplicationRule`, `Taxon` (from `fledermap.store.models`, if
not already imported), `Verdict` (from `fledermap.domain.codes`, if not already imported).
`archive_roots=()` mirrors how other tests in this file construct a minimal `Config` for a task
that doesn't touch the archive (check an existing `Config(...)` construction in this file for
the exact required-field shape and reuse it rather than guessing).

- [ ] **Step 3: Run the test to verify it fails**

Run: `hatch test tests/test_jobs_tasks.py::test_run_classify_implication_mining_cycle_stores_rules -m db -v`
(unsandboxed)
Expected: FAIL with `ImportError` or `AttributeError` (task doesn't exist yet)

- [ ] **Step 4: Confirm Step 1's task passes the test**

Run: `hatch test tests/test_jobs_tasks.py::test_run_classify_implication_mining_cycle_stores_rules -m db -v`
(unsandboxed)
Expected: PASS

- [ ] **Step 5: Add the CLI command**

In `src/fledermap/cli/main.py`, add after `clean_media_command`:

```python
@cli.command(name="mine-classify-implications")
def mine_classify_implications_command() -> None:
    """Rebuild `classify_implication_rule` from current MANUAL-vs-automatic-classifier
    history (design spec 2026-09-25-fledermap-classify-suggestions-part-b-design.md). Also
    runs on its own daily periodic cron inside `worker`, plus once at every `worker`
    startup (see jobs/tasks.py's run_classify_implication_mining_cycle) -- this command is
    for an on-demand run."""
    try:
        config = Config.from_env()
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc

    engine = make_engine(config.database_url)
    _run_migrations(config.database_url)

    from fledermap.services.classify_implications import mine_classify_implications

    with OrmSession(engine) as session:
        count = mine_classify_implications(
            session,
            min_support=config.classify_implication_min_support,
            min_confidence=config.classify_implication_min_confidence,
            min_lift=config.classify_implication_min_lift,
        )
        session.commit()

    click.echo(f"stored {count} implication rule(s)")
```

- [ ] **Step 6: Add the explicit unconditional startup defer**

In `src/fledermap/cli/main.py`'s `_run_worker_async`, add the import at the top of the file
(next to the existing `from fledermap.jobs.tasks import _INGEST_CYCLE_LOCK, run_ingest_cycle`
line):

```python
from fledermap.jobs.tasks import (
    _CLASSIFY_IMPLICATION_MINING_LOCK,
    _INGEST_CYCLE_LOCK,
    run_classify_implication_mining_cycle,
    run_ingest_cycle,
)
```

Then, inside `_run_worker_async`, right after the existing
`await requeue_stalled_jobs(worker_app)` line and before `loop = asyncio.get_running_loop()`:

```python
            # Unconditional, every startup -- NOT routed through Procrastinate's own
            # periodic/catch-up machinery, whose max_delay (10 minutes) is nowhere near
            # enough for a daily job on a machine that isn't always on (part b spec §6).
            await run_classify_implication_mining_cycle.configure(
                lock=_CLASSIFY_IMPLICATION_MINING_LOCK,
                queueing_lock=_CLASSIFY_IMPLICATION_MINING_LOCK,
            ).defer_async(timestamp=int(time.time()))
```

- [ ] **Step 7: Write the failing startup-hook test**

Append to `tests/test_cli.py`, following the file's existing
`test_worker_ingests_a_pre_existing_file_at_startup_without_waiting_for_the_cron_tick` for
structure, but asserting on the new task's explicit defer rather than Procrastinate's own
catch-up (this task has no cron catch-up to rely on for a fast assertion — the defer itself is
the thing under test):

```python
def test_worker_defers_the_classify_implication_mining_task_unconditionally_at_startup(
    clean_database_url: str,
    tmp_path: Path,
) -> None:
    """The explicit `defer_async()` call added to `_run_worker_async` (part b spec §6) fires
    on every startup, independent of the daily cron's own schedule -- unlike
    `run_ingest_cycle`'s startup behaviour (covered by
    `test_worker_ingests_a_pre_existing_file_at_startup_without_waiting_for_the_cron_tick`),
    this isn't Procrastinate's periodic catch-up at all, so this test asserts on the
    deferred job row directly rather than on an observable side effect."""
    archive = tmp_path / "archive"
    archive.mkdir()
    config = Config(
        database_url=clean_database_url,
        archive_roots=(archive,),
        media_root=tmp_path / "media",
    )
    engine = make_engine(clean_database_url)

    cli_main._run_migrations(config.database_url)
    ensure_schema(cli_main.jobs_app, engine)

    async def _drive() -> int | None:
        task = asyncio.create_task(
            cli_main._run_worker_async(config, engine, wait=True),
        )
        try:
            deadline = time.time() + 15
            count = None
            while time.time() < deadline:
                with engine.connect() as conn:
                    count = conn.execute(
                        text(
                            "SELECT count(*) FROM procrastinate_jobs WHERE task_name = "
                            "'fledermap.jobs.tasks.run_classify_implication_mining_cycle'",
                        ),
                    ).scalar()
                if count:
                    break
                await asyncio.sleep(0.2)
            return count
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    try:
        count = asyncio.run(_drive())
    finally:
        engine.dispose()

    assert count == 1
```

- [ ] **Step 8: Run the test to verify it fails**

Run: `hatch test tests/test_cli.py::test_worker_defers_the_classify_implication_mining_task_unconditionally_at_startup -m db -v`
(unsandboxed)
Expected: FAIL (task_name never appears — no defer call exists yet). Temporarily comment out
Step 6's addition to confirm the test actually catches its absence, then restore it.

- [ ] **Step 9: Run the test to verify it passes**

Run: `hatch test tests/test_cli.py::test_worker_defers_the_classify_implication_mining_task_unconditionally_at_startup -m db -v`
(unsandboxed)
Expected: PASS

- [ ] **Step 10: Write a basic CLI command test**

Append to `tests/test_cli.py`, matching the style of other `CliRunner`-based command tests in
this file (e.g. `test_ingest_enqueues_media_jobs_for_created_recordings`):

```python
def test_mine_classify_implications_command_reports_a_count(
    clean_database_url: str,
    tmp_path: Path,
) -> None:
    env = {
        "FLEDERMAP_DATABASE_URL": clean_database_url,
        "FLEDERMAP_ARCHIVE_ROOTS": str(tmp_path / "archive"),
        "FLEDERMAP_MEDIA_ROOT": str(tmp_path / "media"),
    }
    runner = CliRunner()

    result = runner.invoke(cli, ["mine-classify-implications"], env=env)

    assert result.exit_code == 0, result.output
    assert "stored 0 implication rule(s)" in result.output
```

- [ ] **Step 11: Run the full test suite**

Run: `hatch test -m "not db" -v`
Expected: PASS

Run: `hatch test -m db -v` (unsandboxed — full DB suite, ~120s, see CLAUDE.md's
"Environment gotchas")
Expected: PASS

- [ ] **Step 12: Run mypy**

Run: `hatch run types:check`
Expected: no errors.

- [ ] **Step 13: Run `hatch fmt`**

Run: `hatch fmt`
Expected: no changes needed, or auto-fixed formatting only.

- [ ] **Step 14: Commit**

```bash
git add src/fledermap/jobs/tasks.py src/fledermap/cli/main.py tests/test_jobs_tasks.py \
  tests/test_cli.py
git commit -m "feat: add classify-implication mining periodic task, CLI command, startup hook"
```

---

## Final Checklist

After all 6 tasks:

- [ ] `hatch test` (full suite, incl. `-m db`, unsandboxed) passes with zero failures and zero
  warnings.
- [ ] `hatch run types:check` passes with zero errors.
- [ ] `hatch fmt` reports no changes needed.
- [ ] A manual smoke test: run `fledermap mine-classify-implications` against the real dev
  database (if any MANUAL classifications exist) and confirm it reports a sane count, then load
  a recording-details page for a recording whose automatic atoms match a stored rule and confirm
  a mined suggestion chip appears, sorted below any part (a) chips present.
