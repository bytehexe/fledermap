# Fledermap — classify-box likely-suggestions design, part (b): mined implications

Status: proposed. Scope: a new `classify_implication_rule` table + migration, a new
`services/classify_implications.py` (mining), an extension to
`services/classify_suggestions.py` (consuming mined rules), a new `Config` pair, a new
periodic job + CLI command in `jobs/tasks.py`/`cli/main.py`, and a startup hook in
`cli/main.py`'s `_run_worker_async`.

This is **part (b)** of the classify-suggestions feature: mining historical
MANUAL-vs-automatic-classifier patterns for implication-style suggestions ("if the
automatic classifiers found species {X, Y}, the user has historically picked Z"),
building on part (a)'s two fixed heuristics
(`docs/superpowers/specs/2026-09-25-fledermap-classify-suggestions-design.md`) and its
already-generalized candidate-list schema (`ClassifySuggestion`, priority ordering, the
two-cap mechanism) — this plugs in as one more candidate-generating function, per that
spec's §4.

## 1. Overview

A rule is Horn-clause shaped: `{antecedent atom, ...} → conclusion`, where an **antecedent
atom** is "species X was claimed by some automatic (non-manual) classifier on this
recording" and a **conclusion** is a single MANUAL outcome — a specific species, or the
NO_ID/NOISE sentinel. Rules are mined offline from history, stored, and looked up (cheap
indexed reads, not a live aggregate) when suggestions are generated.

Two independent decisions shape this, both made deliberately simple given the real risk
here: sparse data (this project's own real-world dataset is modest) makes an
overexpressive model prone to overfitting. Since a wrong suggestion costs a user one
ignored click rather than a bad classification (nothing auto-applies), a lower
statistical bar than a real classifier would need is fine — but the mining method itself
should still be a standard, well-understood one (association-rule mining / market-basket
analysis), not a bespoke scheme:

- **Antecedent atoms are species-only, source-agnostic.** Not "BatDetect2 claimed X" —
  just "X was claimed." A source-specific vocabulary would multiply the atom space
  (species × source) for a benefit (capturing per-classifier reliability quirks) that's
  speculative before we've seen real mined output. Additive to extend later if warranted.
- **Antecedent size is capped at 2 atoms** (`{X}→Z` and `{X,Y}→Z`, nothing larger). A
  recording rarely has more than a handful of automatic claims to begin with; capping
  keeps mining's itemset enumeration cheap and every rule interpretable, with no
  configurable knob nobody will tune.

## 2. Training-example decomposition

For every recording holding at least one MANUAL claim: an antecedent atom set is every
distinct `taxon_id` among that recording's automatic (non-`is_manual`) `Identification`
rows with `verdict == SPECIES`. A NOISE/NO_ID automatic claim contributes no antecedent
atom — that's not species evidence, it's what part (a)'s SNR-driven Noise heuristic
already covers.

**Multi-species MANUAL claims decompose into one training example per conclusion.** If
the automatic atoms are `{X, Y}` and the MANUAL claims are `{A, B}` (a genuine
multi-species human call), that yields two independent training rows: antecedent
`{X, Y}` → conclusion `A`, and antecedent `{X, Y}` → conclusion `B`. This keeps every
mined rule single-conclusion (the Horn-clause property), and treats a multi-species
ground truth as two pieces of evidence rather than inventing a multi-conclusion rule
shape.

A MANUAL claim of NO_ID or NOISE decomposes the same way, as a single training row with
that sentinel as the conclusion (no taxon).

## 3. Mining algorithm

For each training example (antecedent atom set, one conclusion), enumerate every size-1
and size-2 **subset** of the antecedent atom set (not just the full set) and, for each
subset, increment two running counters: `co_occurrence[(subset, conclusion)]` and
`antecedent_total[subset]`. After processing all examples:

```
confidence(subset, conclusion) = co_occurrence[(subset, conclusion)] / antecedent_total[subset]
```

A candidate rule survives into the stored table only if it clears both:

- **`min_support`**: `co_occurrence[(subset, conclusion)] >= Config.classify_implication_min_support` (default **3**)
- **`min_confidence`**: `confidence(subset, conclusion) >= Config.classify_implication_min_confidence` (default **0.6**)

Both are `Config` fields (new, following the existing `batdetect2_threshold`/
`noise_snr_threshold_db` pattern — env var or TOML, no code change to retune once real
mined output exists to judge them against).

## 4. Storage

New table `classify_implication_rule` (SQLAlchemy model `ClassifyImplicationRule`,
migration required):

```python
class ClassifyImplicationRule(Base):
    __tablename__ = "classify_implication_rule"
    __table_args__ = (
        UniqueConstraint("antecedent_key", "conclusion_verdict", "conclusion_taxon_id"),
        Index("ix_classify_implication_rule_antecedent_key", "antecedent_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    antecedent_key: Mapped[str] = mapped_column(String(64))  # sorted taxon_ids, comma-joined
    conclusion_verdict: Mapped[Verdict] = mapped_column(...)  # SAEnum, same convention as Identification.verdict
    conclusion_taxon_id: Mapped[int | None] = mapped_column(ForeignKey("taxon.id"))  # set only when conclusion_verdict == SPECIES
    support: Mapped[int] = mapped_column(Integer)
    confidence: Mapped[float] = mapped_column(Float)
```

`antecedent_key` is the canonical form of a size-1/size-2 atom subset: sorted `taxon_id`s
joined by `,` (e.g. `"1"` or `"1,24"`) — a plain indexed text lookup, no array/JSON
column needed given the size-2 cap. `conclusion_verdict` reuses the existing `Verdict`
enum (SPECIES/NO_ID/NOISE) and `conclusion_taxon_id`, the same (verdict, taxon_id) shape
`Identification` already uses — not a new bespoke conclusion-kind enum.

**Rebuilt from scratch on every mining run** — no incremental updates, no dirty-tracking.
See §6 for why a full recompute is cheap enough that this is the simpler and more robust
choice over tracking what changed since the last run.

## 5. Suggestion-time lookup

For the current recording's own automatic-species atom set, generate the same size-1/
size-2 subset keys used at mining time and look up matching rows by exact
`antecedent_key` (a handful of indexed point lookups — the expensive computation already
happened at mining time, not here). Rank matches by `confidence` descending (ties by
`support` descending).

**Priority**: mined candidates always sort below both of part (a)'s fixed heuristics,
regardless of confidence — `priority = confidence * 0.5`, giving a ceiling of 0.5, safely
under `_PRIORITY_NOISE`'s 1.0. This matches part (a)'s own ordering rule ("A and B always
sort first... any part-(b) candidates append after them, sorted by their own likelihood
score descending").

Mined candidates feed into the same `classify_suggestions()` entrypoint and the same
server-side pool cap / client-side visible cap / "adds nothing" filtering part (a)
already established — no schema or client-side change needed for this to work (the
forward-compatibility part (a) was built for).

## 6. Refresh: daily cron + unconditional run at worker startup

**Why not incremental / change-tracked recompute:** the mining job scans recordings that
hold a MANUAL claim — a bounded, modest set at this project's real scale (a hobby-scale
dataset, likely hundreds to low thousands of manually-classified recordings) — and does
O(examples × small constant) work per example (enumerating size-≤2 subsets of a handful
of atoms). This is cheap, nothing like the PostGIS-testcontainer-scale costs this project
already treats as expensive. Hooking a recompute trigger into every event that could make
the mined data stale (a classifier threshold changed, a classifier toggled on/off, new
MANUAL data landed) is real plumbing — new triggers, staleness tracking, missed-trigger
edge cases — bought for a saving that doesn't matter here. A full unconditional recompute
is simpler and self-healing by construction: any drift source, known or not, is gone
after the next run.

**Why not cron alone:** this machine doesn't run continuously (see
`project-fledermap-systemd-install-on-this-machine`-style deployment reality) — a bare
daily cron risks silently never firing if the machine is off at the scheduled tick, and
Procrastinate's own periodic-deferrer catch-up only covers a narrow `max_delay` window
(10 minutes; the same mechanism that makes `run_ingest_cycle` "run at startup" for its
5-minute cron, per CLAUDE.md's Procrastinate notes) — nowhere near enough for a daily
job on a machine that might be off for a day.

**Design**: two mechanisms, not one:

1. A daily periodic task, same pattern as `run_ingest_cycle`/`run_clean_media_cycle`
   (`jobs/tasks.py`):
   ```python
   _CLASSIFY_IMPLICATION_MINING_CRON = "0 3 * * *"
   _CLASSIFY_IMPLICATION_MINING_LOCK = "classify_implication_mining"

   @app.periodic(
       cron=_CLASSIFY_IMPLICATION_MINING_CRON,
       lock=_CLASSIFY_IMPLICATION_MINING_LOCK,
       queueing_lock=_CLASSIFY_IMPLICATION_MINING_LOCK,
   )
   @app.task(queue="maintenance", pass_context=True)
   def run_classify_implication_mining_cycle(context, timestamp: int) -> None: ...
   ```
   On the `"maintenance"` queue, sharing it with `run_clean_media_cycle` — both are
   low-priority, non-destructive-to-recording-data background jobs with no reason to be
   isolated the way `clean_media`'s file deletion was isolated from the `"media"` queue.
2. An **explicit, unconditional** deferral once at every `fledermap worker` startup —
   NOT routed through Procrastinate's periodic/catch-up machinery at all (which is
   exactly the mechanism §6 just established isn't good enough here). A plain
   `run_classify_implication_mining_cycle.defer_async(timestamp=...)` call in
   `cli/main.py`'s `_run_worker_async`, alongside the existing `requeue_stalled_jobs`
   call, before the watcher/worker loop starts — always fires when the machine comes up,
   regardless of how long it was off.

Also exposed as an on-demand CLI command (`fledermap mine-classify-implications`), same
pairing as `clean-media`'s periodic-task/CLI-command precedent.

## 7. The "Noise, if there's room" fallback

After building the full candidate pool (part (a)'s A and B, plus any mined rules) and
applying the existing priority sort + `pool_cap` truncation: if the result has fewer than
`pool_cap` entries **and** none of them is the Noise sentinel, append one Noise candidate
unconditionally at the very end (lowest priority, no SNR/evidence requirement at all —
distinct from part (a)'s evidence-driven Noise heuristic). Rationale: Noise is a cheap,
always-plausible option for the Classify box's constrained sentinel vocabulary; once
every other slot in the visible cap is otherwise unused, offering it costs nothing and
saves a search-and-click for the common "clearly nothing here" case that happened not to
trip the SNR/nothing-found heuristic.

This is a pool-assembly step, not a new heuristic with its own priority score — it only
ever fills genuinely spare capacity.

## 8. Testing

- **Python**: unit tests for the mining function (decomposition of a multi-species
  MANUAL claim into separate training rows; subset enumeration correctness for a 3-atom
  recording; min-support/min-confidence thresholds actually excluding under-supported/
  under-confident rules; a full rebuild replacing stale rows from a prior run).
- **Python**: unit tests for suggestion-time lookup (a recording's atoms correctly
  generate the right subset keys; priority ceiling never exceeds part (a)'s Noise
  priority; the "room left, add Noise" fallback fires only when genuinely under cap and
  absent, never overrides an evidence-driven Noise entry already present).
- **`db`-marked integration test**: seed real `Recording`/`Identification` rows across
  several recordings with MANUAL claims, run the mining job, assert the resulting
  `classify_implication_rule` rows match hand-computed expected support/confidence.
- **Startup-hook test**: `fledermap worker` (or its async runner directly) defers the
  mining task unconditionally on startup, independent of the daily cron's own schedule —
  mirrors whatever test already pins `run_ingest_cycle`'s startup-catch-up behavior
  (`tests/test_cli.py`), but asserts on the new task's explicit defer call rather than
  relying on Procrastinate's periodic catch-up.
