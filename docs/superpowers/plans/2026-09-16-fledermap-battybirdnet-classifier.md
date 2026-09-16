# BattyBirdNET-Analyzer classifier integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add BattyBirdNET-Analyzer as a second acoustic classifier, alongside batdetect2, by
having Fledermap talk plain HTTP to a server the operator runs themselves (e.g.
https://github.com/bytehexe/battybirdnet-docker) — never importing, vendoring, or bundling any
BattyBirdNET code.

**Architecture:** `services/batdetect2.py`'s generic `record_detections`/`materialize_claims`/
`purge_source` functions move to a new `services/classifier_detections.py` (they were already
generic over `IdSource`, just misnamed). A new `services/battybirdnet.py` and a new
`classify_battybirdnet_task` in `jobs/tasks.py` reuse those functions, adding only the
classifier-specific parts: an HTTP POST instead of an in-process model call, and response
parsing that resolves each candidate's scientific-name prefix via `resolve_code` (splitting off
the locale-dependent common-name half) or, for the literal `NOISE` label, via the
already-existing `sentinel_verdict()` helper.

**Tech Stack:** `requests` (new base dependency, no licensing entanglement — a generic HTTP
client, not BattyBirdNET code), SQLAlchemy, Procrastinate.

**Spec:** docs/superpowers/specs/2026-09-16-fledermap-battybirdnet-classifier-design.md

## Global Constraints

- Fledermap never imports, vendors, patches, or bundles any BattyBirdNET-Analyzer code — HTTP
  client only, against a URL the operator configures (spec §1).
- `requests` is an ordinary base dependency in `pyproject.toml`'s `dependencies` list, not an
  opt-in extra — it has no licensing entanglement with BattyBirdNET at all (spec §1/§5).
- `_BATTYBIRDNET_FLOOR = 0.05`, matching `_BATDETECT2_FLOOR` exactly — a low sanity floor, not
  the user's configured threshold, so a later threshold change stays free of any model re-run
  (spec §3/§7).
- `battybirdnet_threshold` defaults to `0.6` (the upstream tool's own suggested confidence bar),
  valid range `0.0`–`1.0` (spec §5).
- `battybirdnet_enabled` defaults to `false`; `Config.from_env()` raises `ConfigError` if it's
  `true` but `battybirdnet_url` is unset (spec §5).
- `current_best.py`'s `_PRECEDENCE`: `BATDETECT2` above `BATTYBIRDNET`, both below every EMT
  source (spec §6).
- `codes.battybirdnet` YAML entries store only the scientific-name prefix of each label (split on
  the first `"_"`), never the full `"ScientificName_CommonName"` string — the common-name half is
  locale-dependent (spec §4).
- The literal label `NOISE` is resolved via `domain/codes.py`'s existing `sentinel_verdict()`
  (already maps `"noise"`, case/whitespace-insensitive, to `Verdict.NOISE`), never via
  `resolve_code` — no new code in `domain/codes.py` (spec §3/§4).
- Disabling the classifier deletes all its `classifier_detection`/`Identification` rows via
  `purge_source`; re-enabling regenerates from scratch — same contract as batdetect2 (spec §1/§5).
- Every existing `from fledermap.services.batdetect2 import record_detections / materialize_claims
  / purge_source` call site must move to `fledermap.services.classifier_detections` — nothing may
  still import those three names from `services.batdetect2` when this plan is done (spec §2).

---

### Task 1: Rename the generic classifier-detection functions into their own module

**Files:**
- Create: `src/fledermap/services/classifier_detections.py`
- Modify: `src/fledermap/services/batdetect2.py`
- Modify: `src/fledermap/jobs/tasks.py:38` (top-level import), `src/fledermap/jobs/tasks.py:196`
  (local import inside `classify_batdetect2_task`), `src/fledermap/jobs/tasks.py:385-389` (local
  import inside `run_ingest_cycle`)
- Test: `tests/test_classifier_detections_service.py` (new — generic-function tests moved here)
- Test: `tests/test_batdetect2_service.py` (slimmed — only the `enqueue_batdetect2`-specific test
  stays)

**Interfaces:**
- Consumes: nothing new — this is a pure move of existing code.
- Produces: `fledermap.services.classifier_detections.DetectionInput` (frozen dataclass:
  `taxon_id: int | None`, `raw_label: str | None`, `confidence: float`),
  `fledermap.services.classifier_detections.record_detections(session: OrmSession, recording:
  Recording, source: IdSource, detections: Sequence[DetectionInput]) -> None`,
  `fledermap.services.classifier_detections.materialize_claims(session: OrmSession, source:
  IdSource, threshold: float) -> MaterializeReport`, `fledermap.services.classifier_detections.
  purge_source(session: OrmSession, source: IdSource) -> PurgeReport`. Every later task imports
  these three functions and `DetectionInput` from `services.classifier_detections`, never from
  `services.batdetect2`.

- [ ] **Step 1: Move the generic functions and their dataclasses**

Read the full current content of `src/fledermap/services/batdetect2.py` first (it has grown a
`sentinel_verdict` check added by this step below). Create
`src/fledermap/services/classifier_detections.py` with this content — everything from the
current `batdetect2.py` EXCEPT `enqueue_batdetect2`, plus one addition (the `sentinel_verdict`
check inside `materialize_claims`, marked below):

```python
"""Raw classifier output storage, and materializing it into `Identification`
claims. Generic over `IdSource` -- shared by every classifier that follows
this cache-then-materialize shape (batdetect2, battybirdnet), not specific to
either. See docs/superpowers/specs/2026-09-15-fledermap-batdetect2-
classifier-design.md §2 and docs/superpowers/specs/2026-09-16-fledermap-
battybirdnet-classifier-design.md §2/§3.

`record_detections` is a full replace, not an incremental sync, unlike
`services/identifications.py`'s `replace_claims`: `classifier_detection` rows
aren't claims with independent identity across runs, they're a cache of "the
last time this source's model ran over this recording, here's everything it
said" -- so a re-run simply discards the previous cache wholesale and writes
the new one. `materialize_claims` is the layer that turns that cache into
actual claims, filtered by the currently configured threshold, so a
threshold change takes effect on the next materialize call with no re-run of
the model and no separate "did the threshold change" bookkeeping -- the same
recompute-every-cycle idiom `services/noise.py`'s `classify_noise` already
uses for the same reason.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

from sqlalchemy import CursorResult, delete, select
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import IdSource, Verdict, sentinel_verdict
from fledermap.services.identifications import ClaimInput, replace_claims
from fledermap.store.models import ClassifierDetection, Identification, Recording
from fledermap.store.seed import resolve_code


@dataclass(frozen=True)
class DetectionInput:
    """One raw candidate the model emitted for a recording, before any
    threshold filtering.

    `raw_label` is the model's own label for the candidate and is always set,
    including when `taxon_id` is None because the label has no code mapping
    yet -- see `ClassifierDetection.raw_label`.
    """

    taxon_id: int | None
    raw_label: str | None
    confidence: float


def record_detections(
    session: OrmSession,
    recording: Recording,
    source: IdSource,
    detections: Sequence[DetectionInput],
) -> None:
    """Replace every existing `classifier_detection` row for
    `(recording, source)` with `detections`.

    A full delete-then-insert, not `replace_claims`'s diff-and-patch: unlike
    an `Identification` claim, a `classifier_detection` row has no identity
    across runs worth preserving (no `first_seen_at` to keep, nothing else
    reads it incrementally) -- it is only ever read back wholesale by
    `materialize_claims`, so there is nothing to gain from updating rows in
    place and it would only add complexity.
    """
    session.execute(
        delete(ClassifierDetection).where(
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
                raw_label=detection.raw_label,
                confidence=detection.confidence,
            ),
        )


@dataclass(frozen=True)
class MaterializeReport:
    recordings_updated: int


def materialize_claims(
    session: OrmSession,
    source: IdSource,
    threshold: float,
) -> MaterializeReport:
    """Sync `Identification` claims from `source` with what
    `classifier_detection` currently holds, filtered by `threshold`.

    Recomputes every recording `source` has a cached detection for, not just
    ones whose claim looks stale -- `replace_claims` already no-ops when
    nothing changed (same idiom as `services/noise.py`'s `classify_noise`),
    so this is cheap, and it's what makes a changed threshold take effect on
    the very next call with no separate "did the threshold change" tracking.
    A recording with cached detections but none clearing `threshold` gets an
    explicit NO_ID claim (not an absent claim), the same "no ID" distinction
    every other source makes -- silence would be indistinguishable from
    "never ran through this classifier at all".

    A detection whose `raw_label` is a known non-species sentinel (checked
    via `domain/codes.py`'s `sentinel_verdict` -- e.g. battybirdnet's literal
    `NOISE` label) becomes a claim with that sentinel's `Verdict` and
    `taxon_id=None`, never routed through `resolve_code`: a sentinel is not
    an unmapped species, it's an explicit "this is not a species" signal
    from the model itself, and treating it as an unmapped species would
    wrongly put it in the review queue.

    A detection that clears `threshold` but whose label has no taxon mapping
    (and is not a sentinel) still becomes a SPECIES claim, with `taxon_id`
    None and `raw_label` set -- NOT a NO_ID, and not dropped. An unmapped
    label is a review-queue item by design (design spec §3, CLAUDE.md
    "Species codes"), and it renders through the same "unmapped species" UI
    `services/ingest.py`'s `_apply_identifications` already produces for an
    unmapped EMT code. Dropping it instead would be unrecoverable here:
    `NO_ID` is a confident-looking answer, and neither `enqueue_batdetect2`
    nor `enqueue_battybirdnet` re-runs the model for a recording that
    already has detection rows.

    `classifier_detection.taxon_id` is only ever resolved once, when the
    classification job first ran -- it is a cache. A code mapping added to
    the taxonomy YAML *after* that run would therefore never reach a cached
    `taxon_id is None` row through `services/ingest.py`'s
    `reresolve_unmapped_identifications` alone: that function patches the
    `Identification` row directly, but this function rebuilds
    `Identification` from `classifier_detection` every cycle regardless,
    which would silently undo the patch on the very next run. So this
    function re-attempts `resolve_code` against `raw_label` itself, every
    cycle, for every still-unmapped detection -- the resolution is cheap (an
    indexed lookup) and idempotent, and it is what actually lets a later
    mapping reach an already-cached, already-unmapped detection, rather than
    only a not-yet-classified one.
    """
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
        passing = [d for d in detections if d.confidence >= threshold]
        # One claim per distinct EFFECTIVE taxon_id (or per sentinel
        # verdict), keeping the highest-confidence detection's label for
        # each. "Effective" because a detection cached as unmapped
        # (taxon_id is None) is re-resolved against `raw_label` here, every
        # cycle -- see this function's docstring for why that re-resolution
        # is what makes a taxonomy mapping added after the model already ran
        # actually take effect.
        #
        # `replace_claims` keys a claim's identity on taxon_id alone, and
        # `uq_identification_source_claim` (with `postgresql_nulls_not_distinct`)
        # allows only one row per (recording, source, taxon_id) -- including
        # one single NULL-taxon row -- so several detections sharing an
        # effective taxon_id (or a mix of a sentinel and unmapped species)
        # MUST collapse to one claim. Collapsing by highest confidence makes
        # which one survives deterministic instead of dependent on row
        # order.
        # Keyed by `effective_taxon_id` ALONE, not `(effective_taxon_id,
        # verdict)` -- `uq_identification_source_claim` is
        # `(recording, source, taxon_id)`, with no verdict column at all, so
        # a sentinel-verdict detection (taxon_id=None) and an
        # unmapped-species detection (also taxon_id=None) MUST compete for
        # the same slot and produce exactly one claim between them, or
        # `replace_claims` would try to insert two rows sharing taxon_id=None
        # and hit that constraint. This is the mechanism behind the "NOISE
        # can outrank an unmapped species, or vice versa, by raw confidence"
        # known limitation the design spec's §7 documents.
        best_by_taxon: dict[
            int | None, tuple[ClassifierDetection, int | None, Verdict]
        ] = {}
        for d in passing:
            sentinel = sentinel_verdict(d.raw_label) if d.raw_label is not None else None
            if sentinel is not None:
                effective_taxon_id: int | None = None
                verdict = sentinel
            else:
                effective_taxon_id = d.taxon_id
                if effective_taxon_id is None and d.raw_label is not None:
                    taxon = resolve_code(session, source, d.raw_label)
                    if taxon is not None:
                        effective_taxon_id = taxon.id
                verdict = Verdict.SPECIES
            previous = best_by_taxon.get(effective_taxon_id)
            if previous is None or d.confidence > previous[0].confidence:
                best_by_taxon[effective_taxon_id] = (d, effective_taxon_id, verdict)
        desired = (
            [
                ClaimInput(
                    taxon_id=effective_taxon_id,
                    verdict=verdict,
                    raw_label=d.raw_label if verdict == Verdict.SPECIES else None,
                )
                for d, effective_taxon_id, verdict in best_by_taxon.values()
            ]
            if best_by_taxon
            else [ClaimInput(taxon_id=None, verdict=Verdict.NO_ID)]
        )
        replace_claims(session, recording, source, desired, now)
        updated += 1

    return MaterializeReport(recordings_updated=updated)


@dataclass(frozen=True)
class PurgeReport:
    detections_removed: int
    identifications_removed: int


def purge_source(session: OrmSession, source: IdSource) -> PurgeReport:
    """Delete every `classifier_detection` and `Identification` row for
    `source`, scoped precisely to that source -- never touches any other
    source's rows, and never cascades into recording/session/site.

    Used when a classifier is disabled after having run: both the raw cache
    and the materialized claims from a disabled source must disappear, or a
    stale claim would keep winning `current_best.py`'s precedence ordering
    forever. Idempotent -- called every ingest cycle while disabled, so it
    must be a no-op on rows that don't exist.
    """
    detections_removed = cast(
        "CursorResult[tuple[()]]",
        session.execute(
            delete(ClassifierDetection).where(ClassifierDetection.source == source)
        ),
    ).rowcount
    identifications_removed = cast(
        "CursorResult[tuple[()]]",
        session.execute(delete(Identification).where(Identification.source == source)),
    ).rowcount
    return PurgeReport(
        detections_removed=detections_removed,
        identifications_removed=identifications_removed,
    )
```

Note the `key`/dedup line is intentionally explicit rather than clever — a sentinel-verdict
detection and an unmapped-species detection both key on `(None, <their own verdict>)`, so they
still compete for one slot (as the spec's §7 known-limitation documents) but never silently
collide into the wrong verdict.

- [ ] **Step 2: Slim `batdetect2.py` down to only its own enqueue logic**

Replace `src/fledermap/services/batdetect2.py`'s content with:

```python
"""batdetect2-specific enqueue logic. The generic cache-then-materialize
functions (`record_detections`, `materialize_claims`, `purge_source`,
`DetectionInput`) live in `services/classifier_detections.py` -- they are
shared with battybirdnet and any future classifier that follows the same
shape.
"""

from __future__ import annotations

import procrastinate
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import IdSource
from fledermap.services.classifier_detections import DetectionInput as DetectionInput
from fledermap.services.classifier_detections import (
    MaterializeReport as MaterializeReport,
)
from fledermap.services.classifier_detections import PurgeReport as PurgeReport
from fledermap.services.classifier_detections import (
    materialize_claims as materialize_claims,
)
from fledermap.services.classifier_detections import purge_source as purge_source
from fledermap.services.classifier_detections import (
    record_detections as record_detections,
)
from fledermap.store.models import ClassifierDetection, Recording


def enqueue_batdetect2(session: OrmSession, engine: Engine) -> int:
    """Enqueue `classify_batdetect2_task` for every recording lacking a
    `classifier_detection` row for `IdSource.BATDETECT2` yet. Re-run every
    ingest cycle while the classifier is enabled -- this is what makes
    enabling the classifier self-backfilling with no separate one-shot
    "just enabled" transition to detect: the very next cycle after flipping
    the config flag finds every existing recording still missing a row and
    enqueues it, same as any recording ingested afterward.

    Returns the number of recordings still awaiting a first classification --
    which is not the number of jobs actually created, since one already
    pending from an earlier cycle is deduplicated by `queueing_lock` below.
    """
    # Local import, and it must stay local: `jobs.tasks` imports
    # `DetectionInput` from `services.classifier_detections` at ITS top
    # level, so a top-level import back the other way here would be a
    # genuine cycle. Enqueueing services and `jobs.tasks` are mutually
    # dependent in general (the task needs the service's types, the service
    # needs the task to defer it); one of the two directions always has to
    # be deferred to call time, and here it is this one.
    from fledermap.jobs.tasks import app as jobs_app
    from fledermap.jobs.tasks import batdetect2_lock_key, classify_batdetect2_task

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
        try:
            classify_batdetect2_task.configure(
                lock=batdetect2_lock_key(audio_hash),
                queueing_lock=batdetect2_lock_key(audio_hash),
            ).defer(audio_hash=audio_hash)
        except procrastinate.exceptions.AlreadyEnqueued:
            pass
    return len(hashes)
```

The `as X` re-exports of `DetectionInput`/`MaterializeReport`/`PurgeReport`/`record_detections`/
`materialize_claims`/`purge_source` exist ONLY so `tests/test_batdetect2_service.py` (Step 3
below) keeps working without touching its imports in this step — Step 3 removes that need by
updating the test file's imports directly, so double check after Step 3 whether these re-exports
are still used anywhere; if not, delete them (mypy's unused-import check under strict settings
would otherwise flag them, and `hatch run types:check` running clean is how you'll find out).

- [ ] **Step 3: Split the test file**

Create `tests/test_classifier_detections_service.py` by moving every test EXCEPT
`test_enqueue_batdetect2_only_enqueues_recordings_missing_a_detection_row` out of
`tests/test_batdetect2_service.py` — i.e. move `test_record_detections_replaces_a_source_s_
existing_rows`, `test_record_detections_with_no_candidates_clears_prior_rows`,
`test_materialize_claims_writes_species_claim_above_threshold`,
`test_materialize_claims_writes_an_unmapped_species_claim_not_no_id`,
`test_materialize_claims_keeps_the_best_label_for_competing_unmapped_ones`,
`test_materialize_claims_reconnects_a_cached_unmapped_label_once_a_code_is_added`,
`test_materialize_claims_writes_no_id_when_nothing_clears_threshold`,
`test_materialize_claims_is_idempotent_and_recomputes_on_threshold_change`,
`test_materialize_claims_skips_recordings_with_no_raw_detections`,
`test_purge_source_removes_detections_and_identifications_for_that_source_only`,
`test_purge_source_is_idempotent`, along with the file's `_recording`/`_taxon` helpers and this
import block:

```python
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import IdSource, Verdict
from fledermap.services.classifier_detections import (
    DetectionInput,
    MaterializeReport,
    PurgeReport,
    materialize_claims,
    purge_source,
    record_detections,
)
from fledermap.store.models import (
    ClassifierDetection,
    Identification,
    Recording,
    Taxon,
    TaxonCode,
)

pytestmark = pytest.mark.db
```

`tests/test_batdetect2_service.py` keeps only its `_recording`/`_taxon` helpers,
`test_enqueue_batdetect2_only_enqueues_recordings_missing_a_detection_row`, and this import
block:

```python
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Engine
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import IdSource
from fledermap.services.batdetect2 import DetectionInput, enqueue_batdetect2
from fledermap.services.classifier_detections import record_detections
from fledermap.store.models import Recording, Taxon

pytestmark = pytest.mark.db
```

(`pytest` itself must still be imported for `pytestmark = pytest.mark.db` — add `import pytest`
to that block.)

- [ ] **Step 4: Write a new test proving the sentinel-verdict behavior**

Add this test to `tests/test_classifier_detections_service.py`:

```python
def test_materialize_claims_writes_a_noise_verdict_for_a_sentinel_label(
    engine: Engine,
) -> None:
    """A detection whose raw_label is a known sentinel (e.g. "NOISE") must
    become a NOISE-verdict claim, not an unmapped-species claim -- proves
    materialize_claims checks sentinel_verdict before resolve_code."""
    with OrmSession(engine) as session:
        recording = _recording(session, "n" * 64)
        record_detections(
            session,
            recording,
            IdSource.BATDETECT2,
            [DetectionInput(taxon_id=None, raw_label="NOISE", confidence=0.9)],
        )
        session.commit()

        materialize_claims(session, IdSource.BATDETECT2, threshold=0.8)
        session.commit()

        ident = session.scalars(
            select(Identification).where(
                Identification.recording_id == recording.id,
                Identification.source == IdSource.BATDETECT2,
            ),
        ).one()
        assert ident.verdict == Verdict.NOISE
        assert ident.taxon_id is None
```

- [ ] **Step 5: Update `jobs/tasks.py`'s imports**

`src/fledermap/jobs/tasks.py:38` currently reads:

```python
from fledermap.services.batdetect2 import DetectionInput
```

Change to:

```python
from fledermap.services.classifier_detections import DetectionInput
```

`src/fledermap/jobs/tasks.py:196` (inside `classify_batdetect2_task`) currently reads:

```python
    from fledermap.services.batdetect2 import record_detections
```

Change to:

```python
    from fledermap.services.classifier_detections import record_detections
```

`src/fledermap/jobs/tasks.py:385-389` (inside `run_ingest_cycle`) currently reads:

```python
    from fledermap.services.batdetect2 import (
        enqueue_batdetect2,
        materialize_claims,
        purge_source,
    )
```

Change to:

```python
    from fledermap.services.batdetect2 import enqueue_batdetect2
    from fledermap.services.classifier_detections import materialize_claims, purge_source
```

- [ ] **Step 6: Run the fast test suite and mypy**

Run: `hatch test -m "not db"` and `hatch run types:check`
Expected: both clean (no new failures; this step touches no `pytest.mark.db` test files' logic,
only imports, but see Step 7).

- [ ] **Step 7: Run the affected db-marked tests yourself**

Per this project's convention (CLAUDE.md's Environment gotchas: the controller runs `-m db`
tests itself, never delegates to a subagent): run
`hatch test -m db tests/test_classifier_detections_service.py tests/test_batdetect2_service.py
tests/test_jobs_tasks.py tests/test_jobs_tasks_batdetect2.py` with `dangerouslyDisableSandbox:
true`.
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add src/fledermap/services/classifier_detections.py src/fledermap/services/batdetect2.py \
  src/fledermap/jobs/tasks.py tests/test_classifier_detections_service.py \
  tests/test_batdetect2_service.py
git commit -m "refactor: extract generic classifier-detection functions into their own module"
```

---

### Task 2: Config fields and `requests` dependency

**Files:**
- Modify: `src/fledermap/config.py`
- Modify: `pyproject.toml`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: `Config.battybirdnet_enabled: bool`, `Config.battybirdnet_url: str | None`,
  `Config.battybirdnet_threshold: float` — Task 6/7 read these three fields.

- [ ] **Step 1: Add `requests` as a base dependency**

In `pyproject.toml`'s `dependencies` list (currently ending `"poiidx>=0.0.9,<0.1",`), add:

```toml
  # A plain HTTP client for the (optional, off-by-default) BattyBirdNET-Analyzer
  # classifier integration -- Fledermap only ever POSTs to a server the operator
  # runs and builds themselves; no BattyBirdNET code is imported, vendored, or
  # bundled, so this carries none of batdetect2's CC-BY-NC extras-group ceremony
  # (design spec 2026-09-16-fledermap-battybirdnet-classifier-design.md §1).
  "requests",
```

In `[tool.hatch.envs.types]`'s `extra-dependencies` list, add `"types-requests"` (requests does
not ship inline types).

- [ ] **Step 2: Add the ENV var constants**

In `src/fledermap/config.py`, after the existing `ENV_BATDETECT2_THRESHOLD = ...` line, add:

```python
ENV_BATTYBIRDNET_ENABLED = "FLEDERMAP_BATTYBIRDNET_ENABLED"
ENV_BATTYBIRDNET_URL = "FLEDERMAP_BATTYBIRDNET_URL"
ENV_BATTYBIRDNET_THRESHOLD = "FLEDERMAP_BATTYBIRDNET_THRESHOLD"
```

In `_KNOWN_FILE_KEYS`, after the existing `"batdetect2_threshold",` line, add:

```python
        "battybirdnet_enabled",
        "battybirdnet_url",
        "battybirdnet_threshold",
```

- [ ] **Step 3: Add the three `Config` dataclass fields**

After the existing `batdetect2_threshold: float = 0.8` field, add:

```python
    # Off by default, same disable=delete/enable=regenerate contract as
    # batdetect2 -- see docs/superpowers/specs/2026-09-16-fledermap-
    # battybirdnet-classifier-design.md §1/§5. Unlike batdetect2, there is no
    # optional-dependency extra to install: this only ever talks HTTP to a
    # server the operator runs and builds themselves.
    battybirdnet_enabled: bool = False
    # Base URL of the operator's own BattyBirdNET-Analyzer server (e.g.
    # http://localhost:7667), REQUIRED when battybirdnet_enabled is true --
    # validated below. None means "not configured", distinct from an empty
    # string.
    battybirdnet_url: str | None = None
    # Matches the upstream tool's own suggested default confidence bar
    # (config.py's MIN_CONFIDENCE = 0.6), even though that constant is never
    # actually enforced server-side for the /analyze API path (spec §3/§5).
    battybirdnet_threshold: float = 0.6
```

- [ ] **Step 4: Add the parsing/validation logic**

After the existing `batdetect2_threshold` parsing block (the one ending
`raise ConfigError(msg)` for the `0.0 <= batdetect2_threshold <= 1.0` check), add:

```python
        battybirdnet_enabled_raw = _lookup(
            ENV_BATTYBIRDNET_ENABLED,
            "battybirdnet_enabled",
            file_values,
        )
        if battybirdnet_enabled_raw is None:
            battybirdnet_enabled = False
        elif isinstance(battybirdnet_enabled_raw, bool):
            battybirdnet_enabled = battybirdnet_enabled_raw
        else:
            label = _source_label(
                ENV_BATTYBIRDNET_ENABLED,
                "battybirdnet_enabled",
                config_path,
            )
            text = _as_str(battybirdnet_enabled_raw, label).strip().lower()
            if text == "true":
                battybirdnet_enabled = True
            elif text == "false":
                battybirdnet_enabled = False
            else:
                msg = f"{label}={battybirdnet_enabled_raw!r} is not a boolean (true/false)."
                raise ConfigError(msg)

        battybirdnet_url_raw = _lookup(
            ENV_BATTYBIRDNET_URL,
            "battybirdnet_url",
            file_values,
        )
        if battybirdnet_url_raw is None:
            battybirdnet_url = None
        else:
            label = _source_label(
                ENV_BATTYBIRDNET_URL,
                "battybirdnet_url",
                config_path,
            )
            battybirdnet_url = _as_str(battybirdnet_url_raw, label)
            if not battybirdnet_url:
                msg = f"{label} is set but empty. Unset it entirely to disable battybirdnet."
                raise ConfigError(msg)

        if battybirdnet_enabled and battybirdnet_url is None:
            msg = (
                f"{_source_label(ENV_BATTYBIRDNET_ENABLED, 'battybirdnet_enabled', config_path)}"
                " is true, but battybirdnet_url is not set. Set "
                f"{ENV_BATTYBIRDNET_URL} (or battybirdnet_url in the config file) to the "
                "operator's BattyBirdNET-Analyzer server URL."
            )
            raise ConfigError(msg)

        battybirdnet_threshold_raw = _lookup(
            ENV_BATTYBIRDNET_THRESHOLD,
            "battybirdnet_threshold",
            file_values,
        )
        if battybirdnet_threshold_raw is None:
            battybirdnet_threshold = 0.6
        else:
            label = _source_label(
                ENV_BATTYBIRDNET_THRESHOLD,
                "battybirdnet_threshold",
                config_path,
            )
            if isinstance(battybirdnet_threshold_raw, bool):
                msg = f"{label}={battybirdnet_threshold_raw!r} is not a number."
                raise ConfigError(msg)
            try:
                battybirdnet_threshold = float(battybirdnet_threshold_raw)
            except (TypeError, ValueError):
                msg = f"{label}={battybirdnet_threshold_raw!r} is not a number."
                raise ConfigError(msg)
            if not 0.0 <= battybirdnet_threshold <= 1.0:
                msg = f"{label}={battybirdnet_threshold_raw!r} must be between 0 and 1."
                raise ConfigError(msg)
```

- [ ] **Step 5: Pass the three fields into the final `cls(...)` call**

Find the `return cls(` call at the end of `Config.from_env` (it currently includes
`batdetect2_enabled=batdetect2_enabled, batdetect2_threshold=batdetect2_threshold,`). Add
directly after those two lines:

```python
            battybirdnet_enabled=battybirdnet_enabled,
            battybirdnet_url=battybirdnet_url,
            battybirdnet_threshold=battybirdnet_threshold,
```

- [ ] **Step 6: Write the failing tests**

Add to `tests/test_config.py` (matching the file's existing style for `batdetect2_enabled`/
`batdetect2_threshold` tests — find them first and mirror their exact fixture/env-setting
pattern):

```python
def test_battybirdnet_enabled_defaults_to_false(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_fledermap_env(monkeypatch)
    monkeypatch.setenv("FLEDERMAP_DATABASE_URL", "postgresql://x/y")
    config = Config.from_env()
    assert config.battybirdnet_enabled is False
    assert config.battybirdnet_url is None
    assert config.battybirdnet_threshold == 0.6


def test_battybirdnet_enabled_requires_url(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_fledermap_env(monkeypatch)
    monkeypatch.setenv("FLEDERMAP_DATABASE_URL", "postgresql://x/y")
    monkeypatch.setenv("FLEDERMAP_BATTYBIRDNET_ENABLED", "true")
    with pytest.raises(ConfigError, match="battybirdnet_url"):
        Config.from_env()


def test_battybirdnet_url_and_threshold_are_configurable_via_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_fledermap_env(monkeypatch)
    monkeypatch.setenv("FLEDERMAP_DATABASE_URL", "postgresql://x/y")
    monkeypatch.setenv("FLEDERMAP_BATTYBIRDNET_ENABLED", "true")
    monkeypatch.setenv("FLEDERMAP_BATTYBIRDNET_URL", "http://localhost:7667")
    monkeypatch.setenv("FLEDERMAP_BATTYBIRDNET_THRESHOLD", "0.75")
    config = Config.from_env()
    assert config.battybirdnet_enabled is True
    assert config.battybirdnet_url == "http://localhost:7667"
    assert config.battybirdnet_threshold == 0.75
```

`_clear_fledermap_env` is this test file's existing helper that unsets every `FLEDERMAP_*`
variable before each test — find its exact name/signature in `tests/test_config.py` before
writing these tests, and use whatever it's actually called if it differs from this guess.

- [ ] **Step 2 (verify): Run the tests to confirm they fail first, then pass after Steps 1-5**

Run: `hatch test tests/test_config.py -k battybirdnet -v`
Expected: FAIL before Steps 1-5 (no such config fields), PASS after.

- [ ] **Step 7: Run the fast suite and mypy**

Run: `hatch test -m "not db"` and `hatch run types:check`
Expected: both clean.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml src/fledermap/config.py tests/test_config.py
git commit -m "feat: add battybirdnet config fields and the requests dependency"
```

---

### Task 3: Species-code mapping data

**Files:**
- Modify: `src/fledermap/store/data/taxa_eu.yaml`
- Modify: `src/fledermap/store/data/taxa_groups.yaml`
- Test: `tests/test_seed.py`

**Interfaces:**
- Consumes: nothing from Tasks 1-2.
- Produces: `codes.battybirdnet` entries `resolve_code(session, IdSource.BATTYBIRDNET, "<name>")`
  resolves against in Task 6.

- [ ] **Step 1: Confirm the label list against the operator's actual running server**

Before editing any YAML, POST a short WAV to the operator's configured
`FLEDERMAP_BATTYBIRDNET_URL`'s `/analyze` endpoint (or ask the person running this task for
access to one) and inspect the real `results` JSON's label strings. The list below was read
directly from `rdz-oss/BattyBirdNET-Analyzer`'s `checkpoints/bats/v1.0/BattyBirdNET-EU-256kHz_
Labels.txt` at its `main` ref on 2026-09-16 — a real, pinned, verbatim source, but confirm the
operator's server build's `REPO_REF` (in their `battybirdnet-docker` `.env`) still points at a
ref with this same label file before trusting it as current.

- [ ] **Step 2: Add `codes.battybirdnet` entries to `taxa_eu.yaml`**

For each of these 28 species (the EU checkpoint's label file, minus the bare `NOISE` sentinel
and the genus-level `Plecotus spec.` entry, both handled separately below), find the matching
`scientific_name:` entry in `src/fledermap/store/data/taxa_eu.yaml` and add a `battybirdnet` key
to its existing `codes:` map (following the file's existing style, e.g. `codes: {emt: PIPPIP,
batdetect2: "Pipistrellus pipistrellus", battybirdnet: "Pipistrellus pipistrellus"}`):

```
Barbastella barbastellus, Eptesicus nilssonii, Eptesicus serotinus, Hypsugo savii,
Miniopterus schreibersii, Myotis alcathoe, Myotis bechsteinii, Myotis brandtii,
Myotis capaccinii, Myotis dasycneme, Myotis daubentonii, Myotis emarginatus,
Myotis myotis, Myotis mystacinus, Myotis nattereri, Nyctalus lasiopterus,
Nyctalus leisleri, Nyctalus noctula, Pipistrellus kuhlii, Pipistrellus maderensis,
Pipistrellus nathusii, Pipistrellus pipistrellus, Pipistrellus pygmaeus,
Rhinolophus blasii, Rhinolophus ferrumequinum, Rhinolophus hipposideros,
Tadarida teniotis, Vespertilio murinus
```

Each `battybirdnet` code value is exactly the scientific name as written above (the prefix before
`"_"` in the real label, e.g. `"Myotis daubentonii_Daubentons bat"` → code `"Myotis daubentonii"`)
— NOT the full label with the common name. If a scientific name in this list has no matching
`taxa_eu.yaml` entry (a species this checkpoint covers that Fledermap's taxonomy doesn't), stop
and flag it rather than inventing a new taxon entry — this plan assumes every name above already
exists in `taxa_eu.yaml`, verified by the person writing this plan on 2026-09-16, but re-verify
before committing since the file may have changed since.

- [ ] **Step 3: Map the genus-level `Plecotus spec.` label**

In `src/fledermap/store/data/taxa_groups.yaml`, find the existing genus-level entry with
`scientific_name: Plecotus`. Add `battybirdnet: "Plecotus"` to its `codes:` map (its `codes:` is
currently `{}` — replace with `{battybirdnet: "Plecotus"}`).

- [ ] **Step 4: Write a failing test**

Add to `tests/test_seed.py` (matching its existing style — find an existing `resolve_code`-based
test for a `batdetect2` code and mirror its exact pattern):

```python
def test_battybirdnet_code_resolves_myotis_daubentonii(engine: Engine) -> None:
    with OrmSession(engine) as session:
        seed_taxonomy(session)
        session.commit()

        taxon = resolve_code(session, IdSource.BATTYBIRDNET, "Myotis daubentonii")

        assert taxon is not None
        assert taxon.scientific_name == "Myotis daubentonii"


def test_battybirdnet_code_resolves_the_plecotus_genus_group(engine: Engine) -> None:
    with OrmSession(engine) as session:
        seed_taxonomy(session)
        session.commit()

        taxon = resolve_code(session, IdSource.BATTYBIRDNET, "Plecotus")

        assert taxon is not None
        assert taxon.scientific_name == "Plecotus"
        assert taxon.rank == "genus"
```

- [ ] **Step 5: Run the db-marked seed tests yourself**

Run: `hatch test -m db tests/test_seed.py` with `dangerouslyDisableSandbox: true`.
Expected: all pass, including the two new ones.

- [ ] **Step 6: Commit**

```bash
git add src/fledermap/store/data/taxa_eu.yaml src/fledermap/store/data/taxa_groups.yaml \
  tests/test_seed.py
git commit -m "feat: add battybirdnet species-code mappings"
```

---

### Task 4: Precedence

**Files:**
- Modify: `src/fledermap/services/current_best.py`
- Test: `tests/test_current_best.py`

**Interfaces:**
- Consumes: `IdSource.BATTYBIRDNET` (already exists in `domain/codes.py`).
- Produces: `BATTYBIRDNET` slotted into `_PRECEDENCE`, immediately after `BATDETECT2`.

- [ ] **Step 1: Update `_PRECEDENCE`**

In `src/fledermap/services/current_best.py`, change:

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

to:

```python
_PRECEDENCE: tuple[IdSource, ...] = (
    IdSource.MANUAL,
    IdSource.EMT_MANUAL,
    IdSource.EMT_GUANO,
    IdSource.EMT_WAMD,
    IdSource.EMT_FILENAME,
    IdSource.BATDETECT2,
    IdSource.BATTYBIRDNET,
    IdSource.FLEDERMAP_NOISE,
)
```

- [ ] **Step 2: Fix the now-stale "unranked source" test**

`tests/test_current_best.py`'s `test_sort_identifications_by_precedence_puts_an_unranked_source_
last` currently uses `IdSource.BATTYBIRDNET` as its example of a source NOT yet in `_PRECEDENCE`
— after Step 1, that's no longer true, and the test would silently start testing something
different from what its docstring says. Change:

```python
def test_sort_identifications_by_precedence_puts_an_unranked_source_last() -> None:
    """A source defined in `IdSource` but not yet wired into `_PRECEDENCE`
    (a future classifier, e.g. BATTYBIRDNET) must not crash the sort."""
    manual = _ident(IdSource.MANUAL)
    future = _ident(IdSource.BATTYBIRDNET)

    assert sort_identifications_by_precedence([future, manual]) == [manual, future]
```

to:

```python
def test_sort_identifications_by_precedence_puts_an_unranked_source_last() -> None:
    """A source defined in `IdSource` but not yet wired into `_PRECEDENCE`
    (e.g. KALEIDOSCOPE, which nothing writes yet) must not crash the sort."""
    manual = _ident(IdSource.MANUAL)
    future = _ident(IdSource.KALEIDOSCOPE)

    assert sort_identifications_by_precedence([future, manual]) == [manual, future]
```

- [ ] **Step 3: Write a new precedence test**

Add to `tests/test_current_best.py`, right after the existing
`test_batdetect2_outranks_fledermap_noise_but_not_emt_sources`:

```python
def test_batdetect2_outranks_battybirdnet_which_outranks_fledermap_noise() -> None:
    manual = _ident(IdSource.MANUAL)
    emt_filename = _ident(IdSource.EMT_FILENAME)
    batdetect2 = _ident(IdSource.BATDETECT2)
    battybirdnet = _ident(IdSource.BATTYBIRDNET)
    noise = _ident(IdSource.FLEDERMAP_NOISE)

    assert sort_identifications_by_precedence(
        [noise, battybirdnet, batdetect2, emt_filename, manual],
    ) == [manual, emt_filename, batdetect2, battybirdnet, noise]
```

- [ ] **Step 4: Run the db-marked test yourself**

Run: `hatch test -m db tests/test_current_best.py` with `dangerouslyDisableSandbox: true`.
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/fledermap/services/current_best.py tests/test_current_best.py
git commit -m "feat: rank battybirdnet below batdetect2, above fledermap.noise"
```

---

### Task 5: `enqueue_battybirdnet`

**Files:**
- Create: `src/fledermap/services/battybirdnet.py`
- Test: `tests/test_battybirdnet_service.py`

**Interfaces:**
- Consumes: `fledermap.services.classifier_detections.record_detections` (Task 1, for the test's
  setup only — `enqueue_battybirdnet` itself doesn't call it), `IdSource.BATTYBIRDNET`.
- Produces: `fledermap.services.battybirdnet.enqueue_battybirdnet(session: OrmSession, engine:
  Engine) -> int`. Task 7's `run_ingest_cycle` wiring calls this.

- [ ] **Step 1: Write the failing test**

Create `tests/test_battybirdnet_service.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import IdSource
from fledermap.services.classifier_detections import DetectionInput, record_detections
from fledermap.store.models import Recording, Taxon

pytestmark = pytest.mark.db


def _recording(session: OrmSession, audio_hash: str) -> Recording:
    r = Recording(
        audio_hash=audio_hash,
        path=f"{audio_hash}.wav",
        recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
    )
    session.add(r)
    session.commit()
    return r


def _taxon(session: OrmSession, scientific_name: str) -> Taxon:
    taxon = Taxon(rank="species", scientific_name=scientific_name)
    session.add(taxon)
    session.commit()
    return taxon


def test_enqueue_battybirdnet_only_enqueues_recordings_missing_a_detection_row(
    engine: Engine,
) -> None:
    from fledermap.jobs.app import ensure_schema
    from fledermap.jobs.tasks import app as jobs_app
    from fledermap.services.battybirdnet import enqueue_battybirdnet

    jobs_app.open(engine)
    ensure_schema(jobs_app, engine)

    with OrmSession(engine) as session:
        with_detections = _recording(session, "j" * 64)
        _recording(session, "k" * 64)  # left without a detection row
        taxon = _taxon(session, "Myotis daubentonii")
        record_detections(
            session,
            with_detections,
            IdSource.BATTYBIRDNET,
            [
                DetectionInput(
                    taxon_id=taxon.id, raw_label=taxon.scientific_name, confidence=0.7
                )
            ],
        )
        session.commit()

        count = enqueue_battybirdnet(session, engine)

        assert count == 1
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `hatch test -m db tests/test_battybirdnet_service.py` with `dangerouslyDisableSandbox:
true`.
Expected: FAIL with `ModuleNotFoundError: No module named 'fledermap.services.battybirdnet'`.

- [ ] **Step 3: Write `services/battybirdnet.py`**

```python
"""battybirdnet-specific enqueue logic. The generic cache-then-materialize
functions live in `services/classifier_detections.py`, shared with
batdetect2.
"""

from __future__ import annotations

import procrastinate
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import IdSource
from fledermap.store.models import ClassifierDetection, Recording


def enqueue_battybirdnet(session: OrmSession, engine: Engine) -> int:
    """Enqueue `classify_battybirdnet_task` for every recording lacking a
    `classifier_detection` row for `IdSource.BATTYBIRDNET` yet. Mirrors
    `services/batdetect2.py`'s `enqueue_batdetect2` exactly -- not shared
    code, since the two dispatch different task functions with different
    lock-key namespaces, but the same self-backfilling shape: re-run every
    ingest cycle while enabled, so enabling the classifier finds every
    existing recording still missing a row on the very next cycle.

    Returns the number of recordings still awaiting a first classification --
    not the number of jobs actually created, since one already pending from
    an earlier cycle is deduplicated by `queueing_lock` below.
    """
    # Local import, and it must stay local: `jobs.tasks` needs this module's
    # types in general (mutual dependency between enqueueing services and
    # `jobs.tasks`), so one direction has to be deferred to call time.
    from fledermap.jobs.tasks import app as jobs_app
    from fledermap.jobs.tasks import battybirdnet_lock_key, classify_battybirdnet_task

    try:
        jobs_app.open(engine)
    except NotImplementedError:
        pass

    already_processed = select(ClassifierDetection.recording_id).where(
        ClassifierDetection.source == IdSource.BATTYBIRDNET,
    )
    hashes = session.scalars(
        select(Recording.audio_hash).where(
            Recording.missing_since.is_(None),
            Recording.id.not_in(already_processed),
        ),
    ).all()
    for audio_hash in hashes:
        try:
            classify_battybirdnet_task.configure(
                lock=battybirdnet_lock_key(audio_hash),
                queueing_lock=battybirdnet_lock_key(audio_hash),
            ).defer(audio_hash=audio_hash)
        except procrastinate.exceptions.AlreadyEnqueued:
            pass
    return len(hashes)
```

This references `battybirdnet_lock_key` and `classify_battybirdnet_task`, which don't exist
until Task 6 — that's expected; this task's own test (Step 1) never calls `enqueue_battybirdnet`
in a way that reaches those names (it only checks the returned count against a database state,
and the local import inside the function is never executed for a recording with zero hashes to
enqueue... actually it IS executed here, since `count == 1` means one hash needs enqueueing, so
the import DOES run). Because of this, Task 5 and Task 6 are NOT independently testable in
isolation — do Task 6 immediately after this one, before running Step 4 below, or write a
temporary stub `battybirdnet_lock_key`/`classify_battybirdnet_task` in `jobs/tasks.py` first.
The simpler path: implement this task's Step 3 above, then immediately proceed to Task 6's Step
1-3 (which add those two names to `jobs/tasks.py`), THEN come back and run this task's Step 4.

- [ ] **Step 4: Run the test to verify it passes (after Task 6's Step 3)**

Run: `hatch test -m db tests/test_battybirdnet_service.py` with `dangerouslyDisableSandbox:
true`.
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/fledermap/services/battybirdnet.py tests/test_battybirdnet_service.py
git commit -m "feat: add enqueue_battybirdnet"
```

(Commit this together with Task 6 in one commit if it's simpler given the interleaving noted in
Step 3 — a fresh implementer subagent may find it clearer to treat Tasks 5 and 6 as one combined
unit for exactly this reason; note that decision in the review package if so.)

---

### Task 6: `classify_battybirdnet_task`

**Files:**
- Modify: `src/fledermap/jobs/tasks.py`
- Test: `tests/test_jobs_tasks_battybirdnet.py`

**Interfaces:**
- Consumes: `fledermap.services.classifier_detections.DetectionInput`/`record_detections`
  (Task 1), `Config.battybirdnet_url`/`battybirdnet_threshold` (Task 2, read by
  `run_ingest_cycle` in Task 7, NOT by this task directly — this task's own `battybirdnet_url` is
  read from `context.additional_context["config"]`, same pattern `classify_batdetect2_task`
  would use if it needed config, matching `name_site_task`'s existing `config: Config =
  context.additional_context["config"]` pattern).
- Produces: `battybirdnet_lock_key(audio_hash: str) -> str`,
  `results_to_detections(results: dict[str, list[list[str]]]) -> list[DetectionInput]` (the pure
  parsing function, analogous to `annotations_to_detections`), `classify_battybirdnet_task`
  (a Procrastinate task). Task 5's `enqueue_battybirdnet` calls
  `classify_battybirdnet_task.configure(...).defer(...)`.

- [ ] **Step 1: Write the failing parsing test**

Add to a new `tests/test_jobs_tasks_battybirdnet.py`:

```python
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import IdSource, Verdict
from fledermap.jobs.tasks import (
    _BATTYBIRDNET_FLOOR,
    battybirdnet_lock_key,
    classify_battybirdnet_task,
    results_to_detections,
)
from fledermap.store.models import ClassifierDetection, Identification, Recording, Taxon

pytestmark = pytest.mark.db


def test_battybirdnet_lock_key_is_namespaced_by_audio_hash() -> None:
    assert battybirdnet_lock_key("abc") == "battybirdnet:abc"


def test_results_to_detections_flattens_every_segment_above_the_floor() -> None:
    resolved = {"Myotis daubentonii": 7, "NOISE": None}
    results = {
        "0-3": [
            ["Myotis daubentonii_Daubentons bat", "0.9"],
            ["NOISE", "0.02"],
        ],
        "3-6": [
            ["NOISE", "0.85"],
            ["Myotis daubentonii_Daubentons bat", "0.01"],
        ],
    }

    detections = results_to_detections(results, lambda label: resolved.get(label.split("_")[0]))

    confidences = {(d.raw_label, round(d.confidence, 2)) for d in detections}
    assert ("Myotis daubentonii_Daubentons bat", 0.9) in confidences
    assert ("NOISE", 0.85) in confidences
    # Below _BATTYBIRDNET_FLOOR, dropped:
    assert not any(c == 0.02 or c == 0.01 for _, c in confidences)


def test_results_to_detections_resolves_the_scientific_name_prefix_only() -> None:
    def resolve_label(label: str) -> int | None:
        scientific_name = label.split("_", 1)[0]
        return 42 if scientific_name == "Myotis daubentonii" else None

    results = {"0-3": [["Myotis daubentonii_Daubentons bat", "0.9"]]}

    detections = results_to_detections(results, resolve_label)

    assert detections[0].taxon_id == 42
    assert detections[0].raw_label == "Myotis daubentonii_Daubentons bat"


def test_results_to_detections_marks_noise_as_unresolved_via_sentinel() -> None:
    """results_to_detections itself never calls sentinel_verdict -- that
    happens downstream in materialize_claims (Task 1). This test only
    proves a bare "NOISE" label is preserved verbatim as raw_label with
    taxon_id from whatever resolve_label returns for it (materialize_claims
    is what turns it into a NOISE verdict, not this parsing function)."""
    results = {"0-3": [["NOISE", "0.9"]]}

    detections = results_to_detections(results, lambda label: None)

    assert detections[0].raw_label == "NOISE"
    assert detections[0].taxon_id is None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `hatch test -m db tests/test_jobs_tasks_battybirdnet.py -k results_to_detections`
Expected: FAIL with `ImportError` (no such names in `jobs.tasks` yet).

- [ ] **Step 3: Implement in `jobs/tasks.py`**

After the existing `def batdetect2_lock_key(audio_hash: str) -> str:` function and its following
`_BATDETECT2_FLOOR` block, add:

```python
def battybirdnet_lock_key(audio_hash: str) -> str:
    return f"battybirdnet:{audio_hash}"


# A fixed, non-configurable sanity floor, same reasoning as _BATDETECT2_FLOOR
# above: the operator's server returns EVERY class's score for EVERY
# 3-second segment, unfiltered (confirmed against analyze.py's actual
# analyzeFile()/saveResultFile() split -- MIN_CONFIDENCE is only ever
# applied inside saveResultFile(), a CLI-only file-export function the
# /analyze route never calls). Without this floor, classifier_detection
# would fill up with near-zero scores for every one of the label
# vocabulary's ~30 classes, for every segment, in every recording.
_BATTYBIRDNET_FLOOR = 0.05


def results_to_detections(
    results: dict[str, list[list[str]]],
    resolve_label: Callable[[str], int | None],
) -> list[DetectionInput]:
    """Turn a BattyBirdNET-Analyzer `/analyze` response's `results` field
    into `DetectionInput`s, dropping anything below `_BATTYBIRDNET_FLOOR`.

    `results` is `{"{segment_start}-{segment_end}": [[label, score_str],
    ...], ...}` -- one entry per 3-second analysis window, every class in
    the label vocabulary scored (confirmed from analyze.py's `analyzeFile`,
    which `server.py`'s `/analyze` route returns verbatim). Flattened across
    every window into one stream: the same label can legitimately appear
    many times across different windows of the same recording, each a
    distinct candidate (mirrors `jobs/tasks.py`'s own
    `annotations_to_detections` for batdetect2, which does the same
    flattening across a per-call-window annotation list).

    Separate from the task, and taking a plain label-to-taxon-id callable
    rather than a `Session`, so this parsing can be tested directly against
    a real captured response with no database anywhere in reach -- same
    reasoning as `annotations_to_detections`'s own docstring: batdetect2's
    parsing bug (reading attributes that never existed) shipped because
    nothing checked the parsing against the real package's actual output
    shape, and a hand-written mock of the same wrong shape passed happily.
    """
    resolved: dict[str, int | None] = {}
    detections: list[DetectionInput] = []
    for candidates in results.values():
        for label, score_str in candidates:
            confidence = float(score_str)
            if confidence < _BATTYBIRDNET_FLOOR:
                continue
            if label not in resolved:
                # A label repeats across every window of a recording, so the
                # same lookup would otherwise run once per window instead of
                # once per distinct label. Scoped to one call, like
                # annotations_to_detections's identical cache: a longer-lived
                # one would go stale the moment a code mapping is added.
                resolved[label] = resolve_label(label)
            detections.append(
                DetectionInput(
                    taxon_id=resolved[label],
                    raw_label=label,
                    confidence=confidence,
                ),
            )
    return detections


@app.task(queue="classify", pass_context=True, retry=_RETRY)
def classify_battybirdnet_task(
    context: procrastinate.JobContext,
    audio_hash: str,
) -> None:
    """POST one recording's WAV to the operator's configured BattyBirdNET-
    Analyzer server and cache every candidate above `_BATTYBIRDNET_FLOOR` in
    `classifier_detection` -- never writes `Identification` directly (see
    `services/classifier_detections.py`'s `materialize_claims`, run
    separately every ingest cycle).

    On any request failure, or a response the server itself reports as
    unsuccessful, this raises and lets Procrastinate's `_RETRY` policy
    handle it -- no healthcheck step, no circuit breaker, matching
    batdetect2's own precedent (design spec 2026-09-16-fledermap-
    battybirdnet-classifier-design.md §3).
    """
    import requests

    from fledermap.services.classifier_detections import record_detections
    from fledermap.services.media import resolve_recording, resolve_wav_path
    from fledermap.store.seed import resolve_code

    archive_roots: tuple[Path, ...] = context.additional_context["archive_roots"]
    engine = context.additional_context["engine"]
    config: Config = context.additional_context["config"]

    # Three phases, and the session is deliberately CLOSED across the middle
    # one: an HTTP round-trip to an external server is slow, and holding a
    # connection open across it leaves it `idle in transaction` for that
    # whole time, holding locks that can block unrelated DDL indefinitely
    # (CLAUDE.md's Database section) -- same reasoning as
    # `classify_batdetect2_task`.
    with OrmSession(engine) as session:
        recording = resolve_recording(session, audio_hash)
        wav_path = resolve_wav_path(archive_roots, recording)

    with open(wav_path, "rb") as audio_file:
        response = requests.post(
            f"{config.battybirdnet_url}/analyze",
            files={"audio": audio_file},
            timeout=300,
        )
    response.raise_for_status()
    payload = response.json()
    if payload.get("msg") != "success":
        msg = f"battybirdnet server returned {payload.get('msg')!r} for {audio_hash}"
        raise RuntimeError(msg)
    results: dict[str, list[list[str]]] = payload["results"]

    with OrmSession(engine) as session:
        recording = resolve_recording(session, audio_hash)

        def resolve_label(label: str) -> int | None:
            scientific_name = label.split("_", 1)[0]
            taxon = resolve_code(session, IdSource.BATTYBIRDNET, scientific_name)
            return taxon.id if taxon is not None else None

        detections = results_to_detections(results, resolve_label)
        record_detections(session, recording, IdSource.BATTYBIRDNET, detections)
        session.commit()
```

Note `resolve_label` here always splits on `"_"` before calling `resolve_code` — including for
`"NOISE"`, which has no `"_"` at all, so `"NOISE".split("_", 1)[0]` is just `"NOISE"` itself,
`resolve_code` finds no match (there is no `codes.battybirdnet: "NOISE"` entry, by Task 3's
design), and `resolve_label` correctly returns `None` for it. `materialize_claims` (Task 1) is
what turns that `taxon_id=None, raw_label="NOISE"` detection into a `Verdict.NOISE` claim via
`sentinel_verdict`, not this function.

- [ ] **Step 4: Run the parsing tests to verify they pass**

Run: `hatch test -m db tests/test_jobs_tasks_battybirdnet.py -k "results_to_detections or
lock_key"` with `dangerouslyDisableSandbox: true`.
Expected: PASS.

- [ ] **Step 5: Now return to Task 5's Step 4** and confirm
`tests/test_battybirdnet_service.py` passes.

- [ ] **Step 6: Write a failing test for the task itself, mocking the HTTP call**

`classify_batdetect2_task`'s own tests (`tests/test_jobs_tasks_batdetect2.py`) construct a plain
`SimpleNamespace` stand-in for `procrastinate.JobContext`, cast to that type, and call the task
function directly (`classify_batdetect2_task(context, audio_hash)`) — no real Procrastinate
worker involved. `classify_battybirdnet_task` needs the same shape, plus a `"config"` key in
`additional_context` (it reads `context.additional_context["config"]` for `battybirdnet_url`).
Since `requests` is always installed (unlike batdetect2's optional package), there's no need for
`test_jobs_tasks_batdetect2.py`'s `sys.modules` patching trick — patch
`fledermap.jobs.tasks.requests.post` directly instead.

Add to `tests/test_jobs_tasks_battybirdnet.py` (extending the imports at the top of the file to
add `from datetime import UTC, datetime`, `from pathlib import Path`, `from types import
SimpleNamespace`, `from typing import cast`, `from unittest.mock import MagicMock, patch`,
`import procrastinate`, `from fledermap.config import Config`, `from fledermap.store.models
import TaxonCode` alongside the ones already listed in Step 1):

```python
def test_classify_battybirdnet_task_caches_every_detection_above_the_floor(
    engine: Engine, tmp_path: Path
) -> None:
    with OrmSession(engine) as session:
        recording = Recording(
            audio_hash="p" * 64,
            path="clip.wav",
            recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
        )
        session.add(recording)
        taxon = Taxon(rank="species", scientific_name="Myotis daubentonii")
        session.add(taxon)
        session.flush()
        session.add(
            TaxonCode(
                source="battybirdnet",
                code="Myotis daubentonii",
                taxon_id=taxon.id,
            ),
        )
        (tmp_path / "clip.wav").write_bytes(b"")
        session.commit()
        audio_hash = recording.audio_hash

    config = Config(
        database_url="postgresql://x/y",
        archive_roots=(tmp_path,),
        battybirdnet_url="http://battybirdnet.example",
    )

    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = {
        "msg": "success",
        "results": {
            "0-3": [
                ["Myotis daubentonii_Daubentons bat", "0.9"],
                # Above _BATTYBIRDNET_FLOOR (0.05), so this must be cached
                # too -- proves NOISE isn't specially dropped by this
                # function (materialize_claims, not this task, is what
                # turns it into a NOISE verdict).
                ["NOISE", "0.85"],
                # Below the floor, dropped.
                ["Myotis daubentonii_Daubentons bat", "0.01"],
            ],
        },
    }

    context = cast(
        "procrastinate.JobContext",
        SimpleNamespace(
            additional_context={
                "archive_roots": (tmp_path,),
                "engine": engine,
                "config": config,
            },
        ),
    )

    with patch("fledermap.jobs.tasks.requests.post", return_value=mock_response):
        classify_battybirdnet_task(context, audio_hash)

    with OrmSession(engine) as session:
        recording = session.scalars(
            select(Recording).where(Recording.audio_hash == audio_hash),
        ).one()
        rows = session.scalars(
            select(ClassifierDetection).where(
                ClassifierDetection.recording_id == recording.id,
            ),
        ).all()
        assert len(rows) == 2
        by_label = {r.raw_label: r for r in rows}
        assert by_label["Myotis daubentonii_Daubentons bat"].confidence == pytest.approx(0.9)
        assert by_label["Myotis daubentonii_Daubentons bat"].taxon_id == taxon.id
        assert by_label["NOISE"].confidence == pytest.approx(0.85)
        assert by_label["NOISE"].taxon_id is None
        assert all(r.source == IdSource.BATTYBIRDNET for r in rows)
```

Check `Config`'s dataclass field list in `src/fledermap/config.py` before running this: if any
field with no default besides `database_url` exists, add it to the `Config(...)` call above with
a real value (a dev database URL string is fine even though this test never connects with it —
`classify_battybirdnet_task` never reads `config.database_url`).

- [ ] **Step 7: Run the fast suite and mypy**

Run: `hatch test -m "not db"` and `hatch run types:check`
Expected: both clean.

- [ ] **Step 8: Run every affected db-marked test file yourself**

Run: `hatch test -m db tests/test_jobs_tasks_battybirdnet.py tests/test_battybirdnet_service.py`
with `dangerouslyDisableSandbox: true`.
Expected: all pass.

- [ ] **Step 9: Commit**

```bash
git add src/fledermap/jobs/tasks.py tests/test_jobs_tasks_battybirdnet.py \
  src/fledermap/services/battybirdnet.py tests/test_battybirdnet_service.py
git commit -m "feat: add classify_battybirdnet_task"
```

---

### Task 7: Wire into `run_ingest_cycle`

**Files:**
- Modify: `src/fledermap/jobs/tasks.py:378-455` (inside `run_ingest_cycle`)
- Test: `tests/test_jobs_tasks.py`

**Interfaces:**
- Consumes: `Config.battybirdnet_enabled`/`battybirdnet_threshold` (Task 2),
  `enqueue_battybirdnet` (Task 5), `materialize_claims`/`purge_source` (Task 1).
- Produces: nothing new for later tasks — this is the final wiring point.

- [ ] **Step 1: Add the battybirdnet block**

In `src/fledermap/jobs/tasks.py`'s `run_ingest_cycle`, immediately after the existing:

```python
        if config.batdetect2_enabled:
            enqueue_batdetect2(session, engine)
            materialize_claims(
                session, IdSource.BATDETECT2, config.batdetect2_threshold
            )
        else:
            purge_source(session, IdSource.BATDETECT2)
        session.commit()
```

add:

```python
        if config.battybirdnet_enabled:
            enqueue_battybirdnet(session, engine)
            materialize_claims(
                session, IdSource.BATTYBIRDNET, config.battybirdnet_threshold
            )
        else:
            purge_source(session, IdSource.BATTYBIRDNET)
        session.commit()
```

Also add `enqueue_battybirdnet` to the existing local-import block in `run_ingest_cycle` (the one
already importing `enqueue_batdetect2`, `materialize_claims`, `purge_source` — see Task 1 Step 5
for its post-Task-1 shape):

```python
    from fledermap.services.battybirdnet import enqueue_battybirdnet
    from fledermap.services.batdetect2 import enqueue_batdetect2
    from fledermap.services.classifier_detections import materialize_claims, purge_source
```

- [ ] **Step 2: Write a failing test**

Find `tests/test_jobs_tasks.py`'s existing test for the batdetect2 block inside
`run_ingest_cycle` (search for `batdetect2_enabled` in that file) and copy its exact pattern,
substituting battybirdnet's names, config fields, and `IdSource.BATTYBIRDNET`. Since
`run_ingest_cycle`'s battybirdnet branch calls `enqueue_battybirdnet`, which in turn (when there
IS something to enqueue) needs a real HTTP call inside `classify_battybirdnet_task` — this test
should follow whatever the existing batdetect2-in-run_ingest_cycle test does to avoid actually
running the classification job itself (likely: assert the job was enqueued/deferred, without
letting the worker actually execute it, or assert `purge_source` ran when disabled). Match that
file's exact existing approach rather than inventing a new one.

- [ ] **Step 3: Run the fast suite and mypy**

Run: `hatch test -m "not db"` and `hatch run types:check`
Expected: both clean.

- [ ] **Step 4: Run the affected db-marked test yourself**

Run: `hatch test -m db tests/test_jobs_tasks.py` with `dangerouslyDisableSandbox: true`.
Expected: all pass, including the new battybirdnet test and every pre-existing test (no
regression in the batdetect2 branch).

- [ ] **Step 5: Commit**

```bash
git add src/fledermap/jobs/tasks.py tests/test_jobs_tasks.py
git commit -m "feat: wire battybirdnet into run_ingest_cycle"
```

---

### Task 8: Migration drift check

**Files:**
- Test: `tests/test_migrations.py` (verify only — no expected code change)

**Interfaces:**
- Consumes: nothing new.
- Produces: nothing new — this task exists to CONFIRM no migration is needed, not to write one.

- [ ] **Step 1: Confirm `IdSource.BATTYBIRDNET` is already in every migration's literal list**

Run: `grep -n "battybirdnet" src/fledermap/alembic/versions/*.py`
Expected: it already appears in both `0001_initial.py` and
`0e45f9fc01af_add_classifier_detection_table.py`'s literal lists — `IdSource.BATTYBIRDNET` was
added to the enum before this feature, so no migration file needs editing. `ClassifierDetection`/
`Identification`/`TaxonCode` all use plain, unconstrained columns (`source`/`code` as `String`,
no CHECK) for exactly this reason (spec D9) — this feature needs zero schema changes.

- [ ] **Step 2: Run the full migration test suite yourself to confirm zero drift**

Run: `hatch test -m db tests/test_migrations.py` with `dangerouslyDisableSandbox: true`.
Expected: all pass, unchanged from before this plan — if anything here fails, STOP and
investigate before continuing; it would mean an earlier task introduced an unexpected model
change.

- [ ] **Step 3: No commit needed** — this task is a verification checkpoint, not a code change.
If Step 2 reveals drift, fix it as its own small commit and re-run Step 2.

---

### Task 9: Documentation

**Files:**
- Modify: `README.md`
- Modify: `docs/reference/configuration.md`
- Modify: `docs/explanation/classifier-model.md`

**Interfaces:**
- Consumes: nothing — pure documentation, no code interfaces.

- [ ] **Step 1: Update `README.md`**

Find the existing batdetect2 paragraph (search for `BatDetect2` in `README.md`) and add a
parallel paragraph immediately after it:

```markdown
A second, independently enable/disable-able classifier,
[BattyBirdNET-Analyzer](https://github.com/rdz-oss/BattyBirdNET-Analyzer) (by R.D. Zinck,
CC-BY-NC-SA-4.0), can also be enabled. Unlike batdetect2, Fledermap never imports, vendors, or
bundles any of its code -- it only talks HTTP to a server you run yourself (see
https://github.com/bytehexe/battybirdnet-docker for one way to run it), configured via
`FLEDERMAP_BATTYBIRDNET_URL`. Set `FLEDERMAP_BATTYBIRDNET_ENABLED=true` and
`FLEDERMAP_BATTYBIRDNET_URL` to your server's base URL to turn it on. Use of the server you point
Fledermap at is non-commercial only, per BattyBirdNET-Analyzer's own license -- that obligation
is yours as the operator running it, separate from Fledermap's own MIT license.
```

- [ ] **Step 2: Update `docs/reference/configuration.md`**

Find the existing `batdetect2_enabled`/`batdetect2_threshold` table rows (search for
`batdetect2_enabled` in that file) and add matching rows immediately after:

```markdown
| battybirdnet classifier enabled | `battybirdnet_enabled` | `FLEDERMAP_BATTYBIRDNET_ENABLED` | no | `false` |
| battybirdnet server URL | `battybirdnet_url` | `FLEDERMAP_BATTYBIRDNET_URL` | required if enabled | (none) |
| battybirdnet detection threshold | `battybirdnet_threshold` | `FLEDERMAP_BATTYBIRDNET_THRESHOLD` | no | `0.6` |
```

Find that file's existing prose paragraph explaining `batdetect2_enabled`/`batdetect2_threshold`
(search for the same string) and add a parallel paragraph:

```markdown
- **`battybirdnet_enabled`/`battybirdnet_url`/`battybirdnet_threshold`** turn on a second,
  optional species classifier ([BattyBirdNET-Analyzer](https://github.com/rdz-oss/
  BattyBirdNET-Analyzer)). Unlike batdetect2, this runs as a separate HTTP server you build and
  run yourself (e.g. via https://github.com/bytehexe/battybirdnet-docker) -- Fledermap never
  imports or bundles any of its code, only POSTs recordings to whatever `battybirdnet_url` names.
  `battybirdnet_enabled` defaults to `false`; setting it to `true` without `battybirdnet_url` set
  is a config error at startup. `battybirdnet_threshold` (default `0.6`, valid range `0.0`-`1.0`)
  works exactly like `batdetect2_threshold`: a changed value takes effect on the next ingest
  cycle with no need to re-run the classifier itself. Disabling the classifier deletes every
  identification it produced; re-enabling regenerates them from scratch.
```

- [ ] **Step 3: Update `docs/explanation/classifier-model.md`**

Find this file's existing precedence walkthrough (it should already mention batdetect2's rank
relative to EMT sources and `fledermap.noise`, from the batdetect2 feature's own Task 9). Add
battybirdnet into that same walkthrough, ranked immediately below batdetect2, above
`fledermap.noise` — match the file's existing prose style rather than introducing a different
one.

- [ ] **Step 4: Run the fast test suite**

Run: `hatch test -m "not db"`
Expected: clean — `tests/test_configuration_docs.py` (if it exists, checking the config
reference table's completeness against `Config`'s actual field list) must still pass; if it does
NOT exist, skip this concern.

- [ ] **Step 5: Commit**

```bash
git add README.md docs/reference/configuration.md docs/explanation/classifier-model.md
git commit -m "docs: document the battybirdnet classifier integration"
```
