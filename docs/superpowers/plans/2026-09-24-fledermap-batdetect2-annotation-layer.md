# Batdetect2 Per-Call Annotation Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make batdetect2's already-per-call model output show up as boxes (species + confidence,
dashed below the configured species threshold) on the recording-detail page's existing
Comment/Layers annotation overlay, using the `AnnotationSource.BATDETECT2` producer slot that spec
`2026-09-23-fledermap-annotations-design.md` reserved but never filled.

**Architecture:** Extend the one function both classifier tasks already call after running their
model (`record_detections`) to also write matching `Annotation` rows when a `DetectionInput`
carries time/frequency geometry — batdetect2's parser starts populating that geometry, BattyBirdNET
continues leaving it `None`. Extend `purge_source` (already the disable-time cleanup path for
`classifier_detection`/`Identification`) to purge `Annotation` rows the same way, so enable/disable
mirrors all three. No schema migration: the `annotation` table's geometry columns are already
nullable and `AnnotationSource.BATDETECT2` already exists with no CHECK constraint.

**Tech Stack:** Python/SQLAlchemy (backend), vanilla JS + `node:test` (frontend), pytest
(`-m db` tests need a real PostGIS testcontainer).

**Spec:** `docs/superpowers/specs/2026-09-24-fledermap-batdetect2-annotation-layer-design.md`
(and its parent, `docs/superpowers/specs/2026-09-23-fledermap-annotations-design.md`)

## Global Constraints

- No database migration in this plan — the `annotation` table already has nullable
  `time_start_s`/`time_end_s`/`freq_min_hz`/`freq_max_hz` columns and `AnnotationSource` has
  `create_constraint=False` (spec §title / D9 precedent).
- `record_detections` stays a full delete-then-insert for both `classifier_detection` and (new)
  `Annotation` rows per `(recording, source)` — no incremental diffing, matching its existing
  documented rationale.
- BattyBirdNET's `results_to_detections` is **not** touched in this plan — it must keep passing
  `None` for all four geometry fields (§1/Non-goals of the spec).
- Gate on `_BATDETECT2_FLOOR` (existing constant, `det_prob >= 0.05`) for which detections get
  written at all; the configured species threshold (`config.batdetect2_threshold`) only affects
  *display* (dashed border), never storage, in this plan (spec §3).
- `hatch test -m "not db"` must stay green after every task; any task touching `record_detections`/
  `purge_source`/the recording-detail view needs a `-m db` run too (`dangerouslyDisableSandbox:
  true`, ~120s) before its commit — see CLAUDE.md's "Environment gotchas".
- `hatch run types:check` must stay clean (covers `tests/` too).
- JS changes go through the existing pure-logic/DOM-touching split: new pure logic lives in
  `annotation_geometry.js` (node-testable), wiring into `recording_detail.js` gets the mandatory
  headless-Chrome live-verification pass (CLAUDE.md's JavaScript tooling section) before its
  commit — not optional for this change.

## Review Focus

- **A batdetect2-classified recording that predates this feature has cached `classifier_detection`
  rows but no stored geometry to backfill from** — the raw model dict was never kept, so nothing
  in this plan can retroactively add boxes for it without a re-run. Task 2's tests must prove a
  *re-run* (not a stale cache) is what produces rows; the deploy note at the end of this plan
  covers forcing that re-run in production.
- **The synthesized empty-run `NoID` marker row must never produce an `Annotation` row** — it has
  `taxon_id=None`/no geometry by construction, and a naive "write an annotation per detection"
  loop that doesn't check for geometry first would otherwise draw a bogus point at time 0 for
  every recording with zero real detections. Covered in Task 2.
- **Disabling batdetect2 after it has run must remove its annotation boxes, not just its claims** —
  a reasonable person reading "the classifier is off" expects nothing of its output left on
  screen. Covered in Task 3.
- **A recording with only comment annotations (no batdetect2 rows) must not show a "Batdetect2"
  layer entry, and a recording with only batdetect2 rows (no comments yet) must not crash the
  Layers menu or the paint-order sort** — the source list is derived from whatever's actually
  present, and the new priority-ordering/sort logic must degr0e correctly on both edge cases, not
  just the two-source case the design was written against. Covered in Task 5/6.
- **A `confidence` of exactly the configured threshold is NOT below it** — `<` not `<=`, matching
  `materialize_claims`'s own "clears the threshold" semantics elsewhere in the codebase (a
  boundary-value test, not just an obviously-above/obviously-below one). Covered in Task 5.

---

## File Structure

- **`src/fledermap/services/classifier_detections.py`** — `DetectionInput` gains four optional
  geometry fields; `record_detections` gains the parallel `Annotation` write; `purge_source` gains
  the parallel `Annotation` delete. (Modify)
- **`src/fledermap/jobs/tasks.py`** — `annotations_to_detections` (batdetect2) populates the new
  geometry fields from the raw annotation dict. `results_to_detections` (BattyBirdNET) untouched.
  (Modify)
- **`src/fledermap/web/app.py`** — `create_app` gains two optional threshold params, stored on
  `app.config`. (Modify)
- **`src/fledermap/cli/main.py`** — `serve` command's `create_app(...)` call passes the two
  thresholds from `Config`. (Modify)
- **`src/fledermap/web/views/recording_detail.py`** — `_serialize_annotation` adds `confidence`;
  the view passes the threshold dict to the template. (Modify)
- **`src/fledermap/web/templates/recording_details.html`** — `#detail-toolbar` gains
  `data-classifier-thresholds`. (Modify)
- **`src/fledermap/web/static/annotation_geometry.js`** — new pure helpers: confidence-aware label
  line, below-threshold check, layer-menu priority ordering, paint-order sort. (Modify)
- **`src/fledermap/web/static/recording_detail.js`** — wires the new helpers into label building,
  `rebuildLayersMenu`, `renderAnnotations`, `annotationAtPoint`. (Modify)
- **`src/fledermap/web/static/app.css`** — `below-threshold` dashed-border rule. (Modify)
- **`tests/test_classifier_detections_service.py`** — new tests for geometry-aware
  `record_detections`/`purge_source`. (Modify)
- **`tests/test_jobs_tasks_batdetect2.py`** — new tests for `annotations_to_detections` geometry
  parsing. (Modify)
- **`tests/test_batdetect2_real_api.py`** — extend the real-output shape assertion to the four
  geometry keys. (Modify)
- **`tests/test_web_app.py`** — new test for the threshold config wiring. (Modify)
- **`tests/test_recording_detail_view.py`** — new tests for `confidence` in serialized output and
  the threshold data attribute. (Modify)
- **`tests/js/annotation_geometry.test.js`** — new tests for the four new pure helpers. (Modify)

---

### Task 1: batdetect2 parsing captures per-call geometry

**Files:**
- Modify: `src/fledermap/services/classifier_detections.py:43-54` (`DetectionInput`)
- Modify: `src/fledermap/jobs/tasks.py:306-351` (`annotations_to_detections`)
- Test: `tests/test_jobs_tasks_batdetect2.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `DetectionInput` now has `time_start_s: float | None = None`,
  `time_end_s: float | None = None`, `freq_min_hz: float | None = None`,
  `freq_max_hz: float | None = None` (all keyword-defaulted so every existing call site — including
  `results_to_detections` and every hand-written test `DetectionInput(...)` call — keeps compiling
  unchanged). Task 2 consumes these four fields.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_jobs_tasks_batdetect2.py` (this file already has an `_annotation()` helper at
the top that builds a real-shaped dict including `start_time`/`end_time`/`low_freq`/`high_freq` —
reuse it):

```python
def test_annotations_to_detections_keeps_the_call_geometry() -> None:
    from fledermap.jobs.tasks import annotations_to_detections

    annotations = [_annotation("Pipistrellus pipistrellus", det_prob=0.9, class_prob=0.85)]

    detections = annotations_to_detections(annotations, resolve_label=lambda _label: None)

    assert len(detections) == 1
    detection = detections[0]
    assert detection.time_start_s == 0.0015
    assert detection.time_end_s == 0.0339
    assert detection.freq_min_hz == 68437
    assert detection.freq_max_hz == 80137
```

This test needs no `db` marker and no database — `annotations_to_detections` takes a plain
callable, per its own docstring. Place it near the other `annotations_to_detections`-adjacent
tests in the file (it currently has none directly, only via `classify_batdetect2_task` — this is
the first test to call the parser function directly, matching the project's stated "parsing bug
shipped because nothing checked the parsing against the real package's actual output shape"
precedent).

- [ ] **Step 2: Run test to verify it fails**

Run: `hatch test tests/test_jobs_tasks_batdetect2.py::test_annotations_to_detections_keeps_the_call_geometry -v`
Expected: FAIL with `AttributeError: 'DetectionInput' object has no attribute 'time_start_s'`

- [ ] **Step 3: Add the four fields to `DetectionInput` and populate them in the parser**

In `src/fledermap/services/classifier_detections.py`, change:

```python
@dataclass(frozen=True)
class DetectionInput:
    """One raw candidate the model emitted for a recording, before any
    threshold filtering.

    `raw_label` is the model's own label for the candidate and is always set,
    including when `taxon_id` is None because the label has no code mapping
    yet -- see `ClassifierDetection.raw_label`.

    The four geometry fields are populated only by a source whose model output is
    genuinely per-call (batdetect2); a source that only has fixed-width analysis
    windows (BattyBirdNET) leaves them `None` -- a window is not a call box. See
    docs/superpowers/specs/2026-09-24-fledermap-batdetect2-annotation-layer-design.md §2.
    `record_detections` uses their presence to decide whether to also write a matching
    `Annotation` row.
    """

    taxon_id: int | None
    raw_label: str | None
    confidence: float
    time_start_s: float | None = None
    time_end_s: float | None = None
    freq_min_hz: float | None = None
    freq_max_hz: float | None = None
```

In `src/fledermap/jobs/tasks.py`'s `annotations_to_detections`, change the `DetectionInput(...)`
construction to:

```python
        detections.append(
            DetectionInput(
                taxon_id=resolved[raw_label],
                raw_label=raw_label,
                confidence=float(annotation["class_prob"]),
                time_start_s=float(annotation["start_time"]),
                time_end_s=float(annotation["end_time"]),
                freq_min_hz=float(annotation["low_freq"]),
                freq_max_hz=float(annotation["high_freq"]),
            ),
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `hatch test tests/test_jobs_tasks_batdetect2.py -v`
Expected: PASS, including the two pre-existing tests in this file (they construct
`ClassifierDetection` rows, unaffected by the new optional fields).

- [ ] **Step 5: Run the fast suite and mypy**

Run: `hatch test -m "not db"` and `hatch run types:check`
Expected: both clean.

- [ ] **Step 6: Commit**

```bash
git add src/fledermap/services/classifier_detections.py src/fledermap/jobs/tasks.py tests/test_jobs_tasks_batdetect2.py
git commit -m "feat: capture per-call time/frequency geometry from batdetect2 output"
```

---

### Task 2: `record_detections` writes matching `Annotation` rows

**Files:**
- Modify: `src/fledermap/services/classifier_detections.py:57-93` (`record_detections`)
- Test: `tests/test_classifier_detections_service.py`

**Interfaces:**
- Consumes: `DetectionInput`'s four geometry fields (Task 1). `AnnotationSource` from
  `fledermap.domain.codes` (already used elsewhere in the codebase with the exact same
  `IdSource.BATDETECT2.value == AnnotationSource.BATDETECT2.value == "batdetect2"` identity —
  confirmed in `src/fledermap/domain/codes.py`). `Annotation` from `fledermap.store.models`.
- Produces: `record_detections(session, recording, source, detections)` now also leaves the
  `annotation` table holding exactly one row per `detections` entry that has
  `time_start_s is not None`, for `(recording, AnnotationSource(source.value))`, replacing
  whatever was there before. Task 3 (`purge_source`) and Task 4 (serialization) consume the
  resulting rows.

This task needs a real database (`pytest.mark.db` is already set at module level in
`tests/test_classifier_detections_service.py` — confirm with `grep -n pytestmark
tests/test_classifier_detections_service.py` if unsure). Run its tests with
`dangerouslyDisableSandbox: true`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_classifier_detections_service.py` (it already imports `record_detections`,
`DetectionInput`, `IdSource`, and has a `Recording`-creating pattern used by the existing tests
around line 47 — follow that pattern):

```python
from fledermap.domain.codes import AnnotationSource
from fledermap.store.models import Annotation


def test_record_detections_writes_an_annotation_for_a_geometry_bearing_detection(
    engine: Engine,
) -> None:
    with OrmSession(engine) as session:
        recording = Recording(
            audio_hash="bd" * 32,
            path="bd.wav",
            recorded_at=datetime(2026, 9, 24, tzinfo=UTC),
        )
        session.add(recording)
        session.commit()

        record_detections(
            session,
            recording,
            IdSource.BATDETECT2,
            [
                DetectionInput(
                    taxon_id=None,
                    raw_label="Pipistrellus pipistrellus",
                    confidence=0.85,
                    time_start_s=0.1,
                    time_end_s=0.13,
                    freq_min_hz=40000.0,
                    freq_max_hz=55000.0,
                ),
            ],
        )
        session.commit()

        annotations = session.scalars(select(Annotation)).all()
        assert len(annotations) == 1
        annotation = annotations[0]
        assert annotation.source == AnnotationSource.BATDETECT2
        assert annotation.recording_id == recording.id
        assert annotation.time_start_s == 0.1
        assert annotation.time_end_s == 0.13
        assert annotation.freq_min_hz == 40000.0
        assert annotation.freq_max_hz == 55000.0
        assert annotation.confidence == 0.85
        assert annotation.literal is None


def test_record_detections_writes_no_annotation_for_a_detection_without_geometry(
    engine: Engine,
) -> None:
    with OrmSession(engine) as session:
        recording = Recording(
            audio_hash="bn" * 32,
            path="bn.wav",
            recorded_at=datetime(2026, 9, 24, tzinfo=UTC),
        )
        session.add(recording)
        session.commit()

        record_detections(
            session,
            recording,
            IdSource.BATTYBIRDNET,
            [DetectionInput(taxon_id=None, raw_label="Pipistrellus pipistrellus", confidence=0.7)],
        )
        session.commit()

        assert session.scalars(select(Annotation)).all() == []


def test_record_detections_writes_no_annotation_for_the_empty_run_noid_marker(
    engine: Engine,
) -> None:
    with OrmSession(engine) as session:
        recording = Recording(
            audio_hash="nm" * 32,
            path="nm.wav",
            recorded_at=datetime(2026, 9, 24, tzinfo=UTC),
        )
        session.add(recording)
        session.commit()

        record_detections(session, recording, IdSource.BATDETECT2, [])
        session.commit()

        assert session.scalars(select(Annotation)).all() == []


def test_record_detections_replaces_prior_annotation_rows_on_rerun(engine: Engine) -> None:
    with OrmSession(engine) as session:
        recording = Recording(
            audio_hash="rr" * 32,
            path="rr.wav",
            recorded_at=datetime(2026, 9, 24, tzinfo=UTC),
        )
        session.add(recording)
        session.commit()

        record_detections(
            session,
            recording,
            IdSource.BATDETECT2,
            [
                DetectionInput(
                    taxon_id=None,
                    raw_label="old",
                    confidence=0.5,
                    time_start_s=0.0,
                    time_end_s=0.01,
                    freq_min_hz=10000.0,
                    freq_max_hz=20000.0,
                ),
            ],
        )
        session.commit()

        record_detections(
            session,
            recording,
            IdSource.BATDETECT2,
            [
                DetectionInput(
                    taxon_id=None,
                    raw_label="new",
                    confidence=0.9,
                    time_start_s=1.0,
                    time_end_s=1.01,
                    freq_min_hz=30000.0,
                    freq_max_hz=40000.0,
                ),
            ],
        )
        session.commit()

        annotations = session.scalars(select(Annotation)).all()
        assert len(annotations) == 1
        assert annotations[0].literal is None
        assert annotations[0].time_start_s == 1.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `hatch test tests/test_classifier_detections_service.py -v` (invoke the Bash call with
`dangerouslyDisableSandbox: true` — this file's tests are `db`-marked and need real Docker/PostGIS,
see Global Constraints; `hatch test` with no `-m` filter runs `db`-marked tests by default, so no
`-m` flag is needed when targeting this file directly)
Expected: FAIL — no `Annotation` rows are written yet, so `len(annotations) == 1` assertions fail
with `0`.

- [ ] **Step 3: Implement the write in `record_detections`**

```python
def record_detections(
    session: OrmSession,
    recording: Recording,
    source: IdSource,
    detections: Sequence[DetectionInput],
) -> None:
    """... (existing docstring, plus:)

    A `DetectionInput` that carries call geometry (`time_start_s is not None` --
    batdetect2 today, see its own docstring) also gets a matching `Annotation` row,
    replaced the same full-delete-then-insert way as the `classifier_detection` rows
    above it -- one write path shared by every geometry-bearing classifier source, so a
    future BattyBirdNET producer needs no code here, only geometry on its own
    `DetectionInput`s. `AnnotationSource(source.value)` is a direct construction, not a
    lookup: `IdSource`/`AnnotationSource` deliberately share the same literal string
    values for `batdetect2`/`battybirdnet` (domain/codes.py). The synthesized empty-run
    NoID marker (see below) has no geometry and therefore produces no annotation --
    "found nothing" must not draw a fake box.
    """
    session.execute(
        delete(ClassifierDetection).where(
            ClassifierDetection.recording_id == recording.id,
            ClassifierDetection.source == source,
        ),
    )
    session.execute(
        delete(Annotation).where(
            Annotation.recording_id == recording.id,
            Annotation.source == AnnotationSource(source.value),
        ),
    )
    rows = detections or [
        DetectionInput(taxon_id=None, raw_label=NO_DETECTION_LABEL, confidence=0.0)
    ]
    for detection in rows:
        session.add(
            ClassifierDetection(
                recording_id=recording.id,
                source=source,
                taxon_id=detection.taxon_id,
                raw_label=detection.raw_label,
                confidence=detection.confidence,
            ),
        )
        if detection.time_start_s is not None:
            session.add(
                Annotation(
                    recording_id=recording.id,
                    source=AnnotationSource(source.value),
                    time_start_s=detection.time_start_s,
                    time_end_s=detection.time_end_s,
                    freq_min_hz=detection.freq_min_hz,
                    freq_max_hz=detection.freq_max_hz,
                    taxon_id=detection.taxon_id,
                    confidence=detection.confidence,
                    literal=None,
                    created_at=datetime.now(UTC),
                ),
            )
```

Add the two new imports at the top of `src/fledermap/services/classifier_detections.py`:

```python
from fledermap.domain.codes import AnnotationSource, IdSource, Verdict, sentinel_verdict
from fledermap.store.models import Annotation, ClassifierDetection, Identification, Recording
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `hatch test tests/test_classifier_detections_service.py` (with `dangerouslyDisableSandbox:
true`)
Expected: PASS — all four new tests, and every pre-existing test in the file (they never assert
`Annotation` is absent, so the new rows don't break them; the pre-existing
`test_record_detections_replaces_a_source_s_existing_rows` and
`test_record_detections_with_no_candidates_replaces_prior_rows_with_a_noid_marker` tests use
`DetectionInput`s with no geometry, so they produce zero `Annotation` rows and stay unaffected).

- [ ] **Step 5: Run the full suite**

Run: `hatch test` (with `dangerouslyDisableSandbox: true` — this is the full `-m db` run) and
`hatch run types:check`
Expected: both clean.

- [ ] **Step 6: Commit**

```bash
git add src/fledermap/services/classifier_detections.py tests/test_classifier_detections_service.py
git commit -m "feat: write per-call Annotation rows alongside the classifier_detection cache"
```

---

### Task 3: Disabling a classifier purges its annotation boxes too

**Files:**
- Modify: `src/fledermap/services/classifier_detections.py:260-289` (`PurgeReport`, `purge_source`)
- Test: `tests/test_classifier_detections_service.py`

**Interfaces:**
- Consumes: `AnnotationSource`, `Annotation` (already imported by Task 2).
- Produces: `PurgeReport` gains `annotations_removed: int`. `purge_source(session, source)` now
  also deletes every `Annotation` row for `AnnotationSource(source.value)`.

- [ ] **Step 1: Write the failing test**

```python
def test_purge_source_removes_annotations_for_that_source_only(engine: Engine) -> None:
    with OrmSession(engine) as session:
        recording = Recording(
            audio_hash="ps" * 32,
            path="ps.wav",
            recorded_at=datetime(2026, 9, 24, tzinfo=UTC),
        )
        session.add(recording)
        session.commit()

        record_detections(
            session,
            recording,
            IdSource.BATDETECT2,
            [
                DetectionInput(
                    taxon_id=None,
                    raw_label="x",
                    confidence=0.9,
                    time_start_s=0.0,
                    time_end_s=0.01,
                    freq_min_hz=10000.0,
                    freq_max_hz=20000.0,
                ),
            ],
        )
        create_comment_annotation(
            session,
            recording,
            time_start_s=2.0,
            time_end_s=2.0,
            freq_min_hz=None,
            freq_max_hz=None,
            taxon_id=None,
            literal="keep me",
        )
        session.commit()

        report = purge_source(session, IdSource.BATDETECT2)
        session.commit()

        assert report.annotations_removed == 1
        remaining = session.scalars(select(Annotation)).all()
        assert len(remaining) == 1
        assert remaining[0].source == AnnotationSource.COMMENT
```

This needs `from fledermap.services.annotations import create_comment_annotation` added to the
test file's imports if not already present — check with
`grep -n "^from fledermap.services.annotations" tests/test_classifier_detections_service.py`
first; add it if missing.

- [ ] **Step 2: Run test to verify it fails**

Run: `hatch test tests/test_classifier_detections_service.py::test_purge_source_removes_annotations_for_that_source_only -v`
(with `dangerouslyDisableSandbox: true`)
Expected: FAIL with `AttributeError: 'PurgeReport' object has no attribute 'annotations_removed'`

- [ ] **Step 3: Implement**

```python
@dataclass(frozen=True)
class PurgeReport:
    detections_removed: int
    identifications_removed: int
    annotations_removed: int


def purge_source(session: OrmSession, source: IdSource) -> PurgeReport:
    """Delete every `classifier_detection`, `Identification`, and (geometry-bearing
    producer's) `Annotation` row for `source` -- scoped precisely to that source, never
    touches any other source's rows, and never cascades into recording/session/site.

    Used when a classifier is disabled after having run: the raw cache, the materialized
    claims, AND any annotation boxes from a disabled source must all disappear together --
    a stale claim would keep winning `current_best.py`'s precedence ordering forever, and a
    stale box would keep showing a call from a classifier the operator turned off.
    Idempotent -- called every ingest cycle while disabled, so it must be a no-op on rows
    that don't exist.
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
    annotations_removed = cast(
        "CursorResult[tuple[()]]",
        session.execute(
            delete(Annotation).where(Annotation.source == AnnotationSource(source.value))
        ),
    ).rowcount
    return PurgeReport(
        detections_removed=detections_removed,
        identifications_removed=identifications_removed,
        annotations_removed=annotations_removed,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `hatch test tests/test_classifier_detections_service.py` (with `dangerouslyDisableSandbox:
true`)
Expected: PASS, including the pre-existing `test_purge_source_removes_detections_and_identifications_for_that_source_only`
and `test_purge_source_is_idempotent` (neither asserts on `annotations_removed`'s absence, and
`PurgeReport` gaining a field doesn't break their existing assertions on the other two fields).

- [ ] **Step 5: Run the full suite**

Run: `hatch test` (with `dangerouslyDisableSandbox: true`) and `hatch run types:check`
Expected: both clean.

- [ ] **Step 6: Commit**

```bash
git add src/fledermap/services/classifier_detections.py tests/test_classifier_detections_service.py
git commit -m "feat: purge a disabled classifier's annotation boxes along with its claims"
```

---

### Task 4: Expose confidence and configured thresholds to the recording-detail page

**Files:**
- Modify: `src/fledermap/web/views/recording_detail.py:72-81` (`_serialize_annotation`), the view
  function that builds the template context (search for `annotations=annotations,` — the `flask.
  render_template("recording_details.html", ...)` call)
- Modify: `src/fledermap/web/app.py:53-99` (`create_app`)
- Modify: `src/fledermap/cli/main.py` (the `serve` command's `create_app(...)` call)
- Modify: `src/fledermap/web/templates/recording_details.html:46` (`#detail-toolbar`)
- Test: `tests/test_web_app.py`, `tests/test_recording_detail_view.py`

**Interfaces:**
- Consumes: `Annotation.confidence` (already a model column). `Config.batdetect2_threshold`/
  `Config.battybirdnet_threshold` (already exist, defaults 0.8/0.6, `src/fledermap/config.py:286`
  and `:301`).
- Produces: `_serialize_annotation(...)` dicts now include `"confidence": float | None`.
  `create_app(..., batdetect2_threshold: float = 0.8, battybirdnet_threshold: float = 0.6)` stores
  `app.config["CLASSIFIER_THRESHOLDS"] = {"batdetect2": ..., "battybirdnet": ...}`. The recording-
  detail page's `#detail-toolbar` element gains `data-classifier-thresholds='{"batdetect2": 0.8,
  "battybirdnet": 0.6}'` (JSON). Task 6 (JS) consumes both.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_web_app.py` (follow the file's existing `create_app(engine, tmp_path / "static",
tmp_path / "media")` pattern seen in its other tests):

```python
def test_create_app_stores_classifier_thresholds_on_config(
    tmp_path: Path, engine: Engine
) -> None:
    app = create_app(
        engine,
        tmp_path / "static",
        tmp_path / "media",
        batdetect2_threshold=0.75,
        battybirdnet_threshold=0.55,
    )
    assert app.config["CLASSIFIER_THRESHOLDS"] == {"batdetect2": 0.75, "battybirdnet": 0.55}


def test_create_app_defaults_classifier_thresholds(tmp_path: Path, engine: Engine) -> None:
    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    assert app.config["CLASSIFIER_THRESHOLDS"] == {"batdetect2": 0.8, "battybirdnet": 0.6}
```

Add to `tests/test_recording_detail_view.py`, near `test_annotations_are_embedded_in_the_page`:

```python
def test_serialized_annotation_includes_confidence(engine: Engine, tmp_path: Path) -> None:
    app, audio_hash = _create_app_with_one_recording(engine, tmp_path, "cf" * 32)
    with OrmSession(engine) as session:
        recording = session.scalars(select(Recording)).one()
        record_detections(
            session,
            recording,
            IdSource.BATDETECT2,
            [
                DetectionInput(
                    taxon_id=None,
                    raw_label="x",
                    confidence=0.77,
                    time_start_s=0.0,
                    time_end_s=0.01,
                    freq_min_hz=10000.0,
                    freq_max_hz=20000.0,
                ),
            ],
        )
        session.commit()

    response = app.test_client().get(f"/recordings/{audio_hash}")
    html = response.get_data(as_text=True)
    assert '"confidence": 0.77' in html


def test_classifier_thresholds_are_embedded_in_the_page(engine: Engine, tmp_path: Path) -> None:
    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="th" * 32,
                path="a.wav",
                recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
            ),
        )
        session.commit()
    app = create_app(
        engine,
        tmp_path / "static",
        tmp_path / "media",
        batdetect2_threshold=0.75,
        battybirdnet_threshold=0.55,
    )

    response = app.test_client().get(f"/recordings/{'th' * 32}")
    html = response.get_data(as_text=True)
    assert "data-classifier-thresholds=" in html
    assert "0.75" in html
```

These need `from fledermap.services.classifier_detections import DetectionInput, record_detections`
and `from fledermap.domain.codes import IdSource` added to `tests/test_recording_detail_view.py`'s
imports if not already present.

- [ ] **Step 2: Run tests to verify they fail**

Run:
`hatch test tests/test_web_app.py tests/test_recording_detail_view.py -k "threshold or confidence" -v`
(with `dangerouslyDisableSandbox: true`)
Expected: FAIL — `KeyError: 'CLASSIFIER_THRESHOLDS'` / `"confidence"` absent from the JSON /
`data-classifier-thresholds` absent from the HTML.

- [ ] **Step 3: Implement**

In `src/fledermap/web/app.py`, add two parameters to `create_app` (after `combine_emt_display`,
keeping every existing positional call site valid) and store them:

```python
def create_app(
    engine: Engine,
    static_root: Path,
    media_root: Path,
    archive_roots: tuple[Path, ...] = (),
    batdetect2_enabled: bool = False,
    battybirdnet_enabled: bool = False,
    combine_emt_display: bool = True,
    batdetect2_threshold: float = 0.8,
    battybirdnet_threshold: float = 0.6,
) -> flask.Flask:
    """... (existing docstring, plus:)

    `batdetect2_threshold`/`battybirdnet_threshold` are `Config.batdetect2_threshold`/
    `Config.battybirdnet_threshold` -- exposed on `app.config["CLASSIFIER_THRESHOLDS"]` so the
    recording-detail page can draw a batdetect2/battybirdnet annotation box's border dashed when
    its confidence falls below the operator's configured species-confidence threshold (design spec
    2026-09-24-fledermap-batdetect2-annotation-layer-design.md §4). Defaults match `Config`'s own
    defaults, same reasoning as `combine_emt_display`'s default above.
    """
    app = flask.Flask(__name__)
    app.config["ENGINE"] = engine
    app.config["MEDIA_ROOT"] = media_root
    app.config["ARCHIVE_ROOTS"] = archive_roots
    app.config["BATDETECT2_ENABLED"] = batdetect2_enabled
    app.config["BATTYBIRDNET_ENABLED"] = battybirdnet_enabled
    app.config["COMBINE_EMT_DISPLAY"] = combine_emt_display
    app.config["CLASSIFIER_THRESHOLDS"] = {
        "batdetect2": batdetect2_threshold,
        "battybirdnet": battybirdnet_threshold,
    }
```

In `src/fledermap/cli/main.py`, extend the `serve` command's `create_app(...)` call:

```python
    app = create_app(
        engine,
        config.static_root,
        config.media_root,
        config.archive_roots,
        config.batdetect2_enabled,
        config.battybirdnet_enabled,
        config.combine_emt_display,
        config.batdetect2_threshold,
        config.battybirdnet_threshold,
    )
```

In `src/fledermap/web/views/recording_detail.py`, add the field to `_serialize_annotation`:

```python
def _serialize_annotation(a: Annotation) -> dict[str, object]:
    return {
        "id": a.id,
        "source": a.source.value,
        "time_start_s": a.time_start_s,
        "time_end_s": a.time_end_s,
        "freq_min_hz": a.freq_min_hz,
        "freq_max_hz": a.freq_max_hz,
        "taxon_id": a.taxon_id,
        "confidence": a.confidence,
        "literal": a.literal,
    }
```

and pass the thresholds into the template context — find the `flask.render_template(
"recording_details.html", ...)` call in the same file's view function and add:

```python
            classifier_thresholds=flask.current_app.config["CLASSIFIER_THRESHOLDS"],
```

alongside the existing `annotations=annotations,` line.

In `src/fledermap/web/templates/recording_details.html`, change:

```html
    <div class="detail-toolbar" id="detail-toolbar" data-annotations='{{ annotations | tojson }}' data-annotations-url="{{ url_for('recording_detail.post_annotation', audio_hash=recording.audio_hash) }}">
```

to:

```html
    <div class="detail-toolbar" id="detail-toolbar" data-annotations='{{ annotations | tojson }}' data-annotations-url="{{ url_for('recording_detail.post_annotation', audio_hash=recording.audio_hash) }}" data-classifier-thresholds='{{ classifier_thresholds | tojson }}'>
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `hatch test tests/test_web_app.py tests/test_recording_detail_view.py -v` (with
`dangerouslyDisableSandbox: true`)
Expected: PASS, including every pre-existing test that calls `create_app(...)` positionally or
checks `data-annotations=` (the new attribute is additive, doesn't change existing ones).

- [ ] **Step 5: Run the full suite**

Run: `hatch test` (with `dangerouslyDisableSandbox: true`) and `hatch run types:check`
Expected: both clean.

- [ ] **Step 6: Commit**

```bash
git add src/fledermap/web/app.py src/fledermap/cli/main.py src/fledermap/web/views/recording_detail.py src/fledermap/web/templates/recording_details.html tests/test_web_app.py tests/test_recording_detail_view.py
git commit -m "feat: expose annotation confidence and classifier thresholds to the detail page"
```

---

### Task 5: Pure JS helpers — confidence label, below-threshold check, ordering

**Files:**
- Modify: `src/fledermap/web/static/annotation_geometry.js`
- Test: `tests/js/annotation_geometry.test.js`

**Interfaces:**
- Consumes: nothing new (pure functions over plain objects, matching every other helper in this
  file).
- Produces:
  - `annotationLabelLines(annotation, taxon)` → `string[]` — the lines `recording_detail.js`
    should join for an annotation's label (species line with confidence, then `literal` if
    present).
  - `isBelowThreshold(annotation, thresholds)` → `boolean`.
  - `sortAnnotationsForPaint(annotations)` → new array, comment-source entries moved after every
    other source, relative order otherwise preserved.
  - `layerMenuSources(sources)` → `string[]` — `sources` (already deduped) reordered with
    `"comment"` first, `"batdetect2"` second, `"battybirdnet"` third, anything else alphabetically
    after.

  Task 6 wires all four into `recording_detail.js`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/js/annotation_geometry.test.js` (extend the existing `require(...)` destructuring at
the top with the four new names):

```javascript
const {
  normalizeDragBounds,
  annotationShape,
  abbreviateScientificName,
  annotationBoxPx,
  annotationPointPx,
  clampToPlot,
  annotationLabelLines,
  isBelowThreshold,
  sortAnnotationsForPaint,
  layerMenuSources,
} = require("../../src/fledermap/web/static/annotation_geometry.js");

test("annotationLabelLines shows the abbreviated species and rounded confidence", () => {
  const annotation = { confidence: 0.823, literal: null };
  const taxon = { scientific_name: "Pipistrellus pipistrellus" };
  assert.deepEqual(annotationLabelLines(annotation, taxon), ["P. pipistrellus 82%"]);
});

test("annotationLabelLines omits confidence when the annotation has none (a comment)", () => {
  const annotation = { confidence: null, literal: null };
  const taxon = { scientific_name: "Pipistrellus pipistrellus" };
  assert.deepEqual(annotationLabelLines(annotation, taxon), ["P. pipistrellus"]);
});

test("annotationLabelLines includes the literal note as its own line", () => {
  const annotation = { confidence: null, literal: "roosting nearby" };
  assert.deepEqual(annotationLabelLines(annotation, null), ["roosting nearby"]);
});

test("annotationLabelLines falls back to the raw label when the species is unmapped", () => {
  const annotation = { confidence: 0.5, literal: null, raw_label: "Unknown call" };
  assert.deepEqual(annotationLabelLines(annotation, null), ["Unknown call 50%"]);
});

test("isBelowThreshold is true strictly below the source's configured threshold", () => {
  const thresholds = { batdetect2: 0.8 };
  assert.equal(isBelowThreshold({ source: "batdetect2", confidence: 0.79 }, thresholds), true);
});

test("isBelowThreshold is false exactly at the threshold", () => {
  const thresholds = { batdetect2: 0.8 };
  assert.equal(isBelowThreshold({ source: "batdetect2", confidence: 0.8 }, thresholds), false);
});

test("isBelowThreshold is false when the source has no configured threshold (e.g. comment)", () => {
  const thresholds = { batdetect2: 0.8 };
  assert.equal(isBelowThreshold({ source: "comment", confidence: 0.1 }, thresholds), false);
});

test("isBelowThreshold is false when the annotation has no confidence", () => {
  const thresholds = { batdetect2: 0.8 };
  assert.equal(isBelowThreshold({ source: "batdetect2", confidence: null }, thresholds), false);
});

test("sortAnnotationsForPaint moves comment entries after every other source", () => {
  const annotations = [
    { id: 1, source: "comment" },
    { id: 2, source: "batdetect2" },
    { id: 3, source: "batdetect2" },
    { id: 4, source: "comment" },
  ];
  const sorted = sortAnnotationsForPaint(annotations);
  assert.deepEqual(sorted.map((a) => a.id), [2, 3, 1, 4]);
});

test("sortAnnotationsForPaint leaves a single-source list unchanged", () => {
  const annotations = [{ id: 1, source: "batdetect2" }, { id: 2, source: "batdetect2" }];
  assert.deepEqual(sortAnnotationsForPaint(annotations).map((a) => a.id), [1, 2]);
});

test("layerMenuSources puts comment first, batdetect2 next, others alphabetically after", () => {
  assert.deepEqual(
    layerMenuSources(["battybirdnet", "batdetect2", "comment"]),
    ["comment", "batdetect2", "battybirdnet"],
  );
});

test("layerMenuSources handles a subset of sources", () => {
  assert.deepEqual(layerMenuSources(["batdetect2"]), ["batdetect2"]);
  assert.deepEqual(layerMenuSources(["comment"]), ["comment"]);
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `node --test tests/js/annotation_geometry.test.js`
Expected: FAIL — `TypeError: annotationLabelLines is not a function` (and similarly for the other
three).

- [ ] **Step 3: Implement in `annotation_geometry.js`**

Add before the `module.exports` block:

```javascript
// The species line, abbreviated the same way as every annotation source, with confidence
// appended for any source that carries one (comment annotations never set confidence, so their
// species line has no percentage). `taxon` is the recording-detail page's own lookup-by-id result
// (null for an unmapped species, where `raw_label` -- if the annotation carries one -- is the only
// name available); `literal` is a separate, free-text line, never merged into the species line.
function annotationLabelLines(annotation, taxon) {
  const lines = [];
  const name = taxon
    ? abbreviateScientificName(taxon.scientific_name)
    : annotation.raw_label || null;
  if (name) {
    const confidence =
      annotation.confidence !== null && annotation.confidence !== undefined
        ? ` ${Math.round(annotation.confidence * 100)}%`
        : "";
    lines.push(`${name}${confidence}`);
  }
  if (annotation.literal) lines.push(annotation.literal);
  return lines;
}

// Strictly below (`<`, not `<=`) the configured threshold for the annotation's own source --
// matching `materialize_claims`'s own "clears the threshold" semantics elsewhere in this
// codebase. A source with no configured threshold (comment) or an annotation with no confidence
// (also comment) is never below-threshold; there is nothing to compare.
function isBelowThreshold(annotation, thresholds) {
  const threshold = thresholds[annotation.source];
  if (threshold === undefined) return false;
  if (annotation.confidence === null || annotation.confidence === undefined) return false;
  return annotation.confidence < threshold;
}

// A stable partition, not a sort by source name: every non-comment annotation keeps its original
// relative order, followed by every comment annotation in its original relative order. Comments
// must paint (and hit-test, since `annotationAtPoint` walks from the end) on top of any other
// source, since only comments are ever user-editable via the popup.
function sortAnnotationsForPaint(annotations) {
  const others = annotations.filter((a) => a.source !== "comment");
  const comments = annotations.filter((a) => a.source === "comment");
  return [...others, ...comments];
}

// Comments lead the Layers menu, batdetect2 next, battybirdnet after that, anything else
// (a future source) alphabetically at the end -- not a plain alphabetical sort, which would put
// "batdetect2" ahead of "comment".
const _LAYER_MENU_PRIORITY = ["comment", "batdetect2", "battybirdnet"];
function layerMenuSources(sources) {
  return [...sources].sort((a, b) => {
    const ia = _LAYER_MENU_PRIORITY.indexOf(a);
    const ib = _LAYER_MENU_PRIORITY.indexOf(b);
    if (ia !== -1 || ib !== -1) {
      return (ia === -1 ? _LAYER_MENU_PRIORITY.length : ia) -
        (ib === -1 ? _LAYER_MENU_PRIORITY.length : ib);
    }
    return a.localeCompare(b);
  });
}
```

Add the four names to the existing `module.exports` object at the bottom of the file.

- [ ] **Step 4: Run tests to verify they pass**

Run: `node --test tests/js/annotation_geometry.test.js`
Expected: PASS, all tests including the pre-existing ones in the file.

- [ ] **Step 5: Commit**

```bash
git add src/fledermap/web/static/annotation_geometry.js tests/js/annotation_geometry.test.js
git commit -m "feat: add pure helpers for annotation confidence labels and layer ordering"
```

---

### Task 6: Wire the new helpers into the recording-detail page, live-verified

**Files:**
- Modify: `src/fledermap/web/static/recording_detail.js:229-405` (annotations section)
- Modify: `src/fledermap/web/static/app.css` (near the existing `.annotation-box`/`.annotation-point`
  rules, `app.css:1040-1066`)

**Interfaces:**
- Consumes: `annotationLabelLines`, `isBelowThreshold`, `sortAnnotationsForPaint`,
  `layerMenuSources` (Task 5) — already loaded as globals via `annotation_geometry.js`'s
  `<script>` tag, which loads before `recording_detail.js` (confirmed:
  `recording_details.html:240` loads `annotation_geometry.js` before the closing scripts block;
  `recording_detail.js` already calls `abbreviateScientificName`/`annotationShape`/etc. as bare
  globals from the same file today). `data-classifier-thresholds` (Task 4).
- Produces: no new exports — this is the DOM-touching integration layer, verified live rather than
  unit tested (per CLAUDE.md's JavaScript tooling section: this file has top-level
  `document.addEventListener`/DOM access and cannot be `require()`-d in `node:test`).

This task has no automated test of its own; it is verified via the mandatory headless-Chrome
live-verification pass (CLAUDE.md's "Headless Chrome live-verification technique" memory / the
existing `.superpowers` precedent from the original annotations feature). Follow that technique
against a running `fledermap serve` (or `hatch run fledermap serve`) instance pointed at a
recording with at least one seeded batdetect2 `Annotation` row (seed one directly via
`record_detections` in a throwaway script, or via the DB, the same way earlier live-verification
passes in this project's history seeded fixture state) and at least one comment annotation
overlapping it.

- [ ] **Step 1: Read the confidence-and-threshold data once, alongside the existing `annotations`/
  `taxonById` reads**

In `src/fledermap/web/static/recording_detail.js`, right after the existing block:

```javascript
  let annotations = JSON.parse(
    document.getElementById("detail-toolbar").dataset.annotations || "[]",
  );
```

add:

```javascript
  const classifierThresholds = JSON.parse(
    document.getElementById("detail-toolbar").dataset.classifierThresholds || "{}",
  );
```

(`dataset.classifierThresholds` — the browser camelCases the `data-classifier-thresholds`
attribute automatically, same as the existing `dataset.annotationsUrl` reading
`data-annotations-url`.)

- [ ] **Step 2: Sort annotations for paint order wherever they're iterated for rendering/hit-testing**

In `annotationAtPoint`, change:

```javascript
  function annotationAtPoint(xPx, yPx) {
    // Later (later-rendered, i.e. later in the array) annotations paint on top, so hit-test from
    // the end -- an overlapping pair should highlight whichever one is actually visible on top.
    for (let i = annotations.length - 1; i >= 0; i--) {
      const annotation = annotations[i];
```

to:

```javascript
  function annotationAtPoint(xPx, yPx) {
    // Later (later-rendered, i.e. later in the array) annotations paint on top, so hit-test from
    // the end -- an overlapping pair should highlight whichever one is actually visible on top.
    // sortAnnotationsForPaint puts comments last regardless of DB insertion order, since only
    // comments are user-editable and must win an overlap.
    const ordered = sortAnnotationsForPaint(annotations);
    for (let i = ordered.length - 1; i >= 0; i--) {
      const annotation = ordered[i];
```

and in `renderAnnotations`, change:

```javascript
  function renderAnnotations() {
    annotationLayer.innerHTML = "";
    // Elements are rebuilt from scratch below, so any element the current hover class was on is
    // gone -- reset the tracked id rather than leaving it pointing at a detached node; the next
    // mousemove naturally re-applies the class if the cursor is still over the same annotation.
    hoveredAnnotationId = null;
    for (const annotation of annotations) {
```

to:

```javascript
  function renderAnnotations() {
    annotationLayer.innerHTML = "";
    // Elements are rebuilt from scratch below, so any element the current hover class was on is
    // gone -- reset the tracked id rather than leaving it pointing at a detached node; the next
    // mousemove naturally re-applies the class if the cursor is still over the same annotation.
    hoveredAnnotationId = null;
    for (const annotation of sortAnnotationsForPaint(annotations)) {
```

- [ ] **Step 3: Apply the below-threshold class and build the label via the new helper**

In `renderAnnotations`, right after the existing:

```javascript
      if (annotation.taxon_id !== null) {
        el.style.setProperty("--annotation-color", colorForTaxon(annotation.taxon_id));
      }
```

add:

```javascript
      if (isBelowThreshold(annotation, classifierThresholds)) {
        el.classList.add("below-threshold");
      }
```

Then replace the existing label-building block:

```javascript
      const taxon = annotation.taxon_id !== null ? taxonById.get(annotation.taxon_id) : null;
      if (taxon || annotation.literal) {
        const label = document.createElement("span");
        label.className = "annotation-label";
        const lines = [];
        if (taxon) lines.push(abbreviateScientificName(taxon.scientific_name));
        if (annotation.literal) lines.push(annotation.literal);
        // textContent, never innerHTML: `literal` is user-authored free text.
        label.textContent = lines.join("\n");
        label.style.whiteSpace = "pre-line";
        el.appendChild(label);
      }
```

with:

```javascript
      const taxon = annotation.taxon_id !== null ? taxonById.get(annotation.taxon_id) : null;
      const lines = annotationLabelLines(annotation, taxon);
      if (lines.length > 0) {
        const label = document.createElement("span");
        label.className = "annotation-label";
        // textContent, never innerHTML: `literal` is user-authored free text, and `raw_label`
        // (the unmapped-species fallback) is the model's own string, neither sanitized as HTML.
        label.textContent = lines.join("\n");
        label.style.whiteSpace = "pre-line";
        el.appendChild(label);
      }
```

- [ ] **Step 4: Reorder the Layers menu**

In `rebuildLayersMenu`, change:

```javascript
  function rebuildLayersMenu() {
    const sources = [...new Set(annotations.map((a) => a.source))].sort();
```

to:

```javascript
  function rebuildLayersMenu() {
    const sources = layerMenuSources([...new Set(annotations.map((a) => a.source))]);
```

- [ ] **Step 5: Add the dashed-border CSS**

In `src/fledermap/web/static/app.css`, right after the existing:

```css
.annotation-box {
  border: 1px solid var(--annotation-color, var(--color-annotation));
  background: color-mix(in srgb, var(--annotation-color, var(--color-annotation)) 12%, transparent);
}
```

add:

```css
/* A classifier detection below the operator's configured species-confidence threshold is still
   shown (the model DID detect a call there), but visually marked provisional -- the call is real,
   the species guess is the part that's unconfirmed. Comment annotations never get this class
   (isBelowThreshold is always false when a source has no configured threshold). */
.annotation-box.below-threshold {
  border-style: dashed;
}
```

and right after `.annotation-point`'s existing border rule, add the point equivalent:

```css
.annotation-point.below-threshold {
  border-style: dashed;
}
```

- [ ] **Step 6: Live-verify**

Follow this project's headless-Chrome live-verification technique. At minimum, confirm all of:

1. A recording with a seeded batdetect2 annotation above the configured threshold shows a
   **solid**-border box labeled `"P. pipistrellus 91%"` (or whatever species/confidence was
   seeded) — abbreviated form, percentage present, no "Unknown"/`undefined` in the label.
2. A seeded batdetect2 annotation below the threshold shows a **dashed**-border box.
3. The Layers menu lists "Comments" before "Batdetect2" when both are present, and lists only
   "Batdetect2" (no "Comments" row) for a recording with batdetect2 rows and zero comments.
4. A comment box placed overlapping a batdetect2 box paints on top and remains clickable/editable
   via the Comment tool (click opens the edit popup for the comment, not the batdetect2 box
   underneath).
5. Hovering a batdetect2 box (any tool active) still applies the existing hover highlight — the
   below-threshold class must not interfere with `.annotation-hover`'s existing selector.
6. Toggling the "Batdetect2" layer checkbox off hides its boxes without affecting the comment
   layer's visibility, and vice versa (pre-existing behavior — confirm it still holds).

Record any deviation and fix before proceeding; do not commit on a failed live check.

- [ ] **Step 7: Run the fast Python suite and JS tests as a final sanity check**

Run: `hatch test -m "not db"`, `node --test tests/js/`, `hatch run types:check`
Expected: all clean (this task touches no Python).

- [ ] **Step 8: Commit**

```bash
git add src/fledermap/web/static/recording_detail.js src/fledermap/web/static/app.css
git commit -m "feat: render batdetect2 annotation boxes with confidence labels and threshold styling"
```

---

### Task 7: Pin the real-package geometry field assumption

**Files:**
- Modify: `tests/test_batdetect2_real_api.py`

**Interfaces:**
- Consumes: `annotations_to_detections` (Task 1 already changed what it reads; this task adds
  coverage against the real package, mirroring the file's own stated precedent that a parsing
  assumption "has to be checked against the package itself or it isn't checked at all").
- Produces: nothing new consumed elsewhere — this is a pin, not a new capability.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_batdetect2_real_api.py`, next to
`test_every_real_annotation_has_the_fields_the_parser_reads`:

```python
def test_every_real_annotation_has_the_geometry_fields_the_parser_reads(
    real_annotations: list[dict],
) -> None:
    for annotation in real_annotations:
        assert isinstance(annotation["start_time"], float)
        assert isinstance(annotation["end_time"], float)
        assert isinstance(annotation["low_freq"], (int, float))
        assert isinstance(annotation["high_freq"], (int, float))
        assert annotation["end_time"] >= annotation["start_time"]
        assert annotation["high_freq"] >= annotation["low_freq"]


def test_parsing_real_output_keeps_the_call_geometry(real_annotations: list[dict]) -> None:
    detections = annotations_to_detections(real_annotations, lambda _label: None)
    by_det_prob = [a for a in real_annotations if a["det_prob"] >= _BATDETECT2_FLOOR]
    for detection, annotation in zip(detections, by_det_prob, strict=True):
        assert detection.time_start_s == annotation["start_time"]
        assert detection.time_end_s == annotation["end_time"]
        assert detection.freq_min_hz == annotation["low_freq"]
        assert detection.freq_max_hz == annotation["high_freq"]
```

- [ ] **Step 2: Run test to verify it fails**

This test module is skipped unless the `batdetect2` extra is installed
(`pytest.importorskip("batdetect2")` at the top of the file) — run it inside the `batdetect2`
hatch environment: `hatch run batdetect2:pytest tests/test_batdetect2_real_api.py -v`
Expected: before Task 1's changes this would fail; since Task 1 already landed, this should
actually PASS immediately — this step exists to confirm the test can run at all in this
environment (network access to install the `batdetect2` extra may be required first; if the
`batdetect2` hatch env isn't already provisioned in this environment, note that as a blocker rather
than skipping the test file's assertions unverified).

- [ ] **Step 3: Run test to verify it (still) passes**

Run: `hatch run batdetect2:pytest tests/test_batdetect2_real_api.py -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/test_batdetect2_real_api.py
git commit -m "test: pin batdetect2's real per-call geometry fields against the installed package"
```

---

## Deployment note (not a code task)

After this branch is merged and deployed (see `project-fledermap-systemd-install-on-this-machine`
memory: `pipx install --force .` then a `systemctl --user restart fledermap.target`), existing
batdetect2-classified recordings will show no annotation boxes until batdetect2 re-runs over them —
their cached `classifier_detection` rows predate this feature and hold no geometry to backfill
from (spec §6). To force the re-run: set `FLEDERMAP_BATDETECT2_ENABLED=false`, let one ingest
cycle pass (`purge_source` clears the cache, claims, and any annotations — there should be none
yet), then set it back to `true` and let the worker's next cycle reprocess every recording from
scratch via `enqueue_batdetect2`'s now-empty-cache anti-join. **Back up `bats_db` before this**
(already done for this session — `.db-backups/bats_db-20260924T055331Z.dump`), since disabling
purges every existing batdetect2 `Identification` claim, not just the new annotation rows, and
`--dangerously-restore` from that backup is the recovery path if something looks wrong afterward.
