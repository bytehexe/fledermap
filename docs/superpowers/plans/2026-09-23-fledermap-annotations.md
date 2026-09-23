# Annotations (data model + comment tool) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the generic `Annotation` data model + display/layer-toggle mechanism on the
recording-detail page, and implement the comment tool (draw a box or point, attach text and/or
a species, edit, delete) as its first consumer.

**Architecture:** A new `annotation` table (nullable time/freq bound columns, an
`AnnotationSource` enum) behind a small CRUD service restricted to `comment`-source rows. The
recording-detail page embeds the current annotation list at page-load (same pattern as
`taxon_search_index`), renders boxes/points as absolutely-positioned HTML elements via the
existing `plot_coords.js` mapping, and adds two toolbar controls: a Comment tool (a fourth
tool-switch button, drag=rectangle/click=point) and a Layers menu (per-source visibility
checkboxes, vanilla JS, `localStorage`-persisted). Mutations go through a small JSON API and
update the DOM in place — no page reload.

**Tech Stack:** Flask + SQLAlchemy + Alembic (existing), vanilla JS (existing
`recording_detail.js`/`plot_coords.js`/`marker_colors.js`/`classifier_logic.js` conventions, no
Alpine on this page), `node:test` for pure-logic JS units, headless-Chrome live verification.

**Spec:** `docs/superpowers/specs/2026-09-23-fledermap-annotations-design.md`

## Global Constraints

- No DB-level guard restricting comment-only writes — enforced by `services/annotations.py`
  asserting/raising, not a CHECK/trigger (spec §3).
- `Annotation.literal`/`Annotation.taxon_id`: for a `comment` row, at least one must be set —
  enforced by the service, not a DB constraint (spec §2/§4.5).
- Every annotation DOM element has `pointer-events: none` except `comment`-source elements while
  the Comment tool is the active tool (spec §4.3) — no exceptions, no hover/cursor change under
  any other tool.
- Vanilla JS only for the Layers menu and Comment tool — no Alpine.js on this page (spec §4.2).
- `AnnotationSource` is a separate enum from `IdSource` (spec §2) — never reuse `IdSource` values
  for annotations.
- Annotation geometry is immutable after creation — editing changes only `taxon_id`/`literal`;
  redrawing is delete-and-recreate (spec §3).
- The Layers menu lists only sources with ≥1 annotation on the current recording, and the button
  itself is disabled when there are zero annotations of any source (spec §4.2).
- `localStorage` keys for layer visibility are per-source and read/written independently of which
  sources the current recording happens to have (spec §4.2).
- API responses drive an in-place DOM update — never a full page reload (spec §5).

## Review Focus

- **Stored free text rendered unescaped.** `literal` is user-authored text round-tripped through
  the DOM (box label, popup textarea). A reasonable person expects `<script>`-shaped text in a
  comment to display as literal text, not execute — every place `literal` is written into the
  DOM must use `.textContent`, never `innerHTML`/template-string HTML injection. (Task 8, live
  verification.)
- **Deleting a recording must not leave orphaned annotation rows.** A person expects `annotation`
  rows tied to a deleted recording to disappear with it, same as `classifier_detection` already
  does. (Task 1, db test on the FK's `ondelete="CASCADE"`.)
- **Dragging a rectangle in any direction (not just top-left → bottom-right) must produce a valid
  box.** A person drawing bottom-right to top-left expects the same result as drawing the other
  way, not an inverted/negative-size box. (Task 4, pure-logic test.)
- **The comment-only service functions must reject a non-`comment` row**, so the day a future
  batdetect2 producer's rows exist, a stray call from the comment UI/API can't silently mutate or
  delete them. (Task 2, db test — written now, before any other producer exists, so the guard
  doesn't rot un-exercised.)
- **A recording with annotations from more than one source must show one independent checkbox per
  source**, each with its own stored visibility and its own force-visibility exemption (only
  `comment` is ever force-visible). A person toggling "Comments" off must not also hide a future
  "Batdetect2" layer, or vice versa. (Task 4/6, pure-logic + live verification, using two
  synthetic sources even though only `comment` is produced today.)

---

## File structure

- **Modify** `src/fledermap/domain/codes.py` — add `AnnotationSource` enum.
- **Modify** `src/fledermap/store/models.py` — add `Annotation` model.
- **Create** `src/fledermap/alembic/versions/<rev>_add_annotation_table.py` — migration.
- **Create** `src/fledermap/services/annotations.py` — CRUD service (comment-kind rows only).
- **Modify** `src/fledermap/services/vendor_assets.py` — add the Tabler `stack-2` outline icon.
- **Modify** `src/fledermap/web/views/recording_detail.py` — embed the annotation list in the
  page context; add the JSON create/update/delete routes.
- **Create** `src/fledermap/web/static/annotation_geometry.js` — pure geometry/shape logic (no
  DOM), mirroring `plot_coords.js`/`marker_colors.js`.
- **Modify** `src/fledermap/web/static/recording_detail.js` — Comment tool, Layers menu,
  annotation rendering, popup wiring.
- **Modify** `src/fledermap/web/templates/recording_details.html` — toolbar buttons (Comment,
  Layers), the Layers dropdown markup, the comment popup markup, new `<script>` tags.
- **Modify** `src/fledermap/web/static/app.css` — `.annotation-box`/`.annotation-point`/
  `.layers-menu`/`.comment-popup` styles.
- **Create** `tests/test_annotations_service.py` — db-marked service tests.
- **Modify** `tests/test_recording_detail_view.py` — API route tests, toolbar markup tests.
- **Create** `tests/js/annotation_geometry.test.js` — pure-logic unit tests.

---

## Task 1: Data model — `AnnotationSource` enum, `Annotation` table, migration

**Files:**
- Modify: `src/fledermap/domain/codes.py`
- Modify: `src/fledermap/store/models.py`
- Create: `src/fledermap/alembic/versions/<rev>_add_annotation_table.py`
- Test: `tests/test_annotations_service.py` (new file, CHECK-constraint + CASCADE tests only in
  this task; CRUD tests are Task 2), `tests/test_migrations.py` (existing, run unmodified to
  confirm no drift)

**Interfaces:**
- Produces: `AnnotationSource` enum (`domain/codes.py`) with members `COMMENT`, `BATDETECT2`,
  `BATTYBIRDNET`. `Annotation` model (`store/models.py`) with columns `id`, `recording_id`,
  `source`, `time_start_s`, `time_end_s`, `freq_min_hz`, `freq_max_hz`, `taxon_id`, `confidence`,
  `literal`, `created_at`.

- [ ] **Step 1: Add `AnnotationSource` to `domain/codes.py`**

Add after the existing `IdSource` class (which ends around line 66):

```python
class AnnotationSource(StrEnum):
    """Where an annotation came from -- deliberately separate from IdSource: `comment` has no
    meaning as an identification-claim source, and IdSource carries precedence/disagreement
    semantics (current_best.py, review_flags.py) that annotations don't participate in. See
    docs/superpowers/specs/2026-09-23-fledermap-annotations-design.md §2."""

    COMMENT = "comment"
    # Reserved for later specs, not produced by any code yet.
    BATDETECT2 = "batdetect2"
    BATTYBIRDNET = "battybirdnet"
```

- [ ] **Step 2: Write the failing CHECK-constraint test**

Create `tests/test_annotations_service.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import AnnotationSource
from fledermap.store.models import Annotation, Recording

pytestmark = pytest.mark.db


def _recording(session: OrmSession, audio_hash: str) -> Recording:
    r = Recording(
        audio_hash=audio_hash,
        path=f"{audio_hash}.wav",
        recorded_at=datetime(2026, 9, 23, tzinfo=UTC),
    )
    session.add(r)
    session.commit()
    return r


def test_annotation_with_no_time_or_freq_bound_is_rejected(engine: Engine) -> None:
    with OrmSession(engine) as session:
        recording = _recording(session, "a" * 64)
        session.add(
            Annotation(
                recording_id=recording.id,
                source=AnnotationSource.COMMENT,
                literal="orphan note",
                created_at=datetime.now(UTC),
            ),
        )
        with pytest.raises(IntegrityError, match="ck_annotation_has_bound"):
            session.commit()


def test_deleting_a_recording_cascades_to_its_annotations(engine: Engine) -> None:
    with OrmSession(engine) as session:
        recording = _recording(session, "b" * 64)
        session.add(
            Annotation(
                recording_id=recording.id,
                source=AnnotationSource.COMMENT,
                time_start_s=1.0,
                time_end_s=1.0,
                literal="a call",
                created_at=datetime.now(UTC),
            ),
        )
        session.commit()

        session.delete(recording)
        session.commit()

        assert session.get(Annotation, 1) is None
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `hatch test tests/test_annotations_service.py -v` (needs `dangerouslyDisableSandbox: true`
— see CLAUDE.md's Docker/testcontainers note)
Expected: FAIL — `Annotation`/`AnnotationSource` don't exist yet (`ImportError`).

- [ ] **Step 4: Add the `Annotation` model to `store/models.py`**

Add `CheckConstraint` to the existing `sqlalchemy` import block at the top of the file (alongside
`Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint`), and add
`AnnotationSource` to the `from fledermap.domain.codes import (...)` block. Then add the model
after `ClassifierDetection` (before `class Taxon`):

```python
class Annotation(Base):
    """A user- or classifier-produced marker pointing at a place in a recording -- a time range,
    a frequency band, a point, or a rectangle of both. See docs/superpowers/specs/2026-09-23-
    fledermap-annotations-design.md.

    `source` has no CHECK constraint, mirroring `Identification.source`/
    `ClassifierDetection.source`: more producers are coming, a CHECK would force a migration per
    one.

    Geometry is four independently-nullable bound columns rather than a separate shape/kind
    column -- a point is `time_start_s == time_end_s` (and/or `freq_min_hz == freq_max_hz`); no
    read code infers intent from which columns are null vs. which are equal, both are meaningful.
    `ck_annotation_has_bound` rejects the one truly invalid state: every bound null, an annotation
    pointing at nothing.

    `taxon_id`/`confidence`/`literal` are all nullable on every source. For a `comment` row,
    `services/annotations.py`'s `create_comment_annotation`/`update_comment_annotation` enforce
    that at least one of `taxon_id`/`literal` is set (an annotation naming neither is pointless
    data) -- not a DB constraint, since a future non-comment source may legitimately have
    neither. `confidence` is classifier-only; nothing writes it for a `comment` row.
    """

    __tablename__ = "annotation"
    __table_args__ = (
        CheckConstraint(
            "time_start_s IS NOT NULL OR freq_min_hz IS NOT NULL",
            name="ck_annotation_has_bound",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    recording_id: Mapped[int] = mapped_column(
        ForeignKey("recording.id", ondelete="CASCADE"),
        index=True,
    )
    source: Mapped[AnnotationSource] = mapped_column(
        SAEnum(
            AnnotationSource,
            native_enum=False,
            length=32,
            create_constraint=False,
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
        ),
        index=True,
    )
    time_start_s: Mapped[float | None] = mapped_column(Float)
    time_end_s: Mapped[float | None] = mapped_column(Float)
    freq_min_hz: Mapped[float | None] = mapped_column(Float)
    freq_max_hz: Mapped[float | None] = mapped_column(Float)
    taxon_id: Mapped[int | None] = mapped_column(ForeignKey("taxon.id"))
    confidence: Mapped[float | None] = mapped_column(Float)
    literal: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    recording: Mapped[Recording] = relationship()
    taxon: Mapped[Taxon | None] = relationship()
```

- [ ] **Step 5: Generate the migration**

Run (unsandboxed — see CLAUDE.md "run git unsandboxed"/general alembic note; this needs a real
DB connection, same `dangerouslyDisableSandbox: true` requirement as any `db`-marked test):

```bash
hatch run alembic revision --autogenerate -m "add annotation table"
```

Open the generated file under `src/fledermap/alembic/versions/`. Confirm `down_revision =
"642705569fe8"` (today's head — if a prior task in this plan has since added a migration, use
whatever the actual current head is instead). Confirm the generated `op.create_table("annotation",
...)` includes `sa.CheckConstraint("time_start_s IS NOT NULL OR freq_min_hz IS NOT NULL",
name="ck_annotation_has_bound")` and both indexes (`recording_id`, `source`) — autogenerate
should produce all of this correctly since this is a brand-new table (the "CHECK on an existing
enum column is invisible to autogenerate" gotcha in CLAUDE.md's Migrations section doesn't apply
here). If the CHECK constraint is missing from the generated file, add it by hand to
`op.create_table`'s argument list and to `downgrade()`'s implicit table drop (a CHECK is dropped
automatically with `op.drop_table`, nothing extra needed there). Add a one-line module docstring
matching the existing migrations' style (see `0e45f9fc01af_add_classifier_detection_table.py`
for the shape).

- [ ] **Step 6: Run the migration drift test**

Run: `hatch test tests/test_migrations.py -v` (`dangerouslyDisableSandbox: true`)
Expected: PASS — `compare_metadata` finds no drift between the new migration and the model.

- [ ] **Step 7: Run this task's tests to verify they pass**

Run: `hatch test tests/test_annotations_service.py -v` (`dangerouslyDisableSandbox: true`)
Expected: PASS.

- [ ] **Step 8: Run the full non-db suite + types + lint**

Run: `hatch test -m "not db" && hatch run types:check && hatch fmt`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add src/fledermap/domain/codes.py src/fledermap/store/models.py \
  src/fledermap/alembic/versions/ tests/test_annotations_service.py
git commit -m "feat: add Annotation data model and migration"
```

---

## Task 2: `services/annotations.py` CRUD

**Files:**
- Create: `src/fledermap/services/annotations.py`
- Test: `tests/test_annotations_service.py` (extend from Task 1)

**Interfaces:**
- Consumes: `Annotation`, `Recording`, `Taxon` (`store/models.py`), `AnnotationSource`
  (`domain/codes.py`).
- Produces: `list_annotations(session: OrmSession, recording_id: int) -> Sequence[Annotation]`;
  `create_comment_annotation(session: OrmSession, recording: Recording, *, time_start_s:
  float | None, time_end_s: float | None, freq_min_hz: float | None, freq_max_hz: float | None,
  taxon_id: int | None, literal: str | None) -> Annotation`; `update_comment_annotation(session:
  OrmSession, annotation: Annotation, *, taxon_id: int | None, literal: str | None) -> None`;
  `delete_comment_annotation(session: OrmSession, annotation: Annotation) -> None`. All raise
  `ValueError` on a comment-shape violation (neither taxon_id nor literal) or a non-`comment`
  `annotation.source` passed to update/delete.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_annotations_service.py`:

```python
from fledermap.services.annotations import (
    create_comment_annotation,
    delete_comment_annotation,
    list_annotations,
    update_comment_annotation,
)
from fledermap.store.models import Taxon


def test_create_comment_annotation_requires_text_or_species(engine: Engine) -> None:
    with OrmSession(engine) as session:
        recording = _recording(session, "c" * 64)
        with pytest.raises(ValueError, match="literal|taxon_id"):
            create_comment_annotation(
                session,
                recording,
                time_start_s=1.0,
                time_end_s=1.0,
                freq_min_hz=None,
                freq_max_hz=None,
                taxon_id=None,
                literal=None,
            )


def test_create_comment_annotation_persists_a_point(engine: Engine) -> None:
    with OrmSession(engine) as session:
        recording = _recording(session, "d" * 64)

        created = create_comment_annotation(
            session,
            recording,
            time_start_s=2.5,
            time_end_s=2.5,
            freq_min_hz=40_000.0,
            freq_max_hz=40_000.0,
            taxon_id=None,
            literal="buzz here",
        )

        rows = list_annotations(session, recording.id)
        assert len(rows) == 1
        assert rows[0].id == created.id
        assert rows[0].source == AnnotationSource.COMMENT
        assert rows[0].literal == "buzz here"
        assert rows[0].taxon_id is None


def test_update_comment_annotation_changes_text_and_species(engine: Engine) -> None:
    with OrmSession(engine) as session:
        taxon = Taxon(rank="species", scientific_name="Pipistrellus pipistrellus")
        session.add(taxon)
        session.flush()
        recording = _recording(session, "e" * 64)
        created = create_comment_annotation(
            session,
            recording,
            time_start_s=1.0,
            time_end_s=2.0,
            freq_min_hz=20_000.0,
            freq_max_hz=50_000.0,
            taxon_id=None,
            literal="social call?",
        )

        update_comment_annotation(
            session, created, taxon_id=taxon.id, literal="confirmed social call",
        )

        rows = list_annotations(session, recording.id)
        assert rows[0].taxon_id == taxon.id
        assert rows[0].literal == "confirmed social call"


def test_update_comment_annotation_rejects_clearing_both_fields(engine: Engine) -> None:
    with OrmSession(engine) as session:
        recording = _recording(session, "f" * 64)
        created = create_comment_annotation(
            session,
            recording,
            time_start_s=1.0,
            time_end_s=1.0,
            freq_min_hz=None,
            freq_max_hz=None,
            taxon_id=None,
            literal="a note",
        )

        with pytest.raises(ValueError, match="literal|taxon_id"):
            update_comment_annotation(session, created, taxon_id=None, literal=None)


def test_delete_comment_annotation_removes_the_row(engine: Engine) -> None:
    with OrmSession(engine) as session:
        recording = _recording(session, "g" * 64)
        created = create_comment_annotation(
            session,
            recording,
            time_start_s=1.0,
            time_end_s=1.0,
            freq_min_hz=None,
            freq_max_hz=None,
            taxon_id=None,
            literal="delete me",
        )

        delete_comment_annotation(session, created)

        assert list_annotations(session, recording.id) == []


def test_update_comment_annotation_rejects_a_non_comment_source(engine: Engine) -> None:
    """Written before any non-comment producer exists, so this guard is exercised now
    rather than only once a future batdetect2 producer's rows are at risk."""
    with OrmSession(engine) as session:
        recording = _recording(session, "h" * 64)
        detection = Annotation(
            recording_id=recording.id,
            source=AnnotationSource.BATDETECT2,
            time_start_s=1.0,
            time_end_s=1.5,
            freq_min_hz=30_000.0,
            freq_max_hz=45_000.0,
            confidence=0.8,
            created_at=datetime.now(UTC),
        )
        session.add(detection)
        session.commit()

        with pytest.raises(ValueError, match="comment"):
            update_comment_annotation(session, detection, taxon_id=None, literal="hijacked")
        with pytest.raises(ValueError, match="comment"):
            delete_comment_annotation(session, detection)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `hatch test tests/test_annotations_service.py -v` (`dangerouslyDisableSandbox: true`)
Expected: FAIL — `fledermap.services.annotations` doesn't exist yet.

- [ ] **Step 3: Implement `services/annotations.py`**

```python
"""Comment-annotation CRUD (design spec docs/superpowers/specs/2026-09-23-
fledermap-annotations-design.md §3). The only place `AnnotationSource.COMMENT` rows are ever
written or removed -- a future batdetect2/battybirdnet annotation producer writes/deletes its
own rows directly against the `Annotation` model, never through this module, mirroring how
`services/manual_classification.py` and `services/identifications.py`'s `replace_claims` split
user-written from classifier-written `Identification` rows."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import AnnotationSource
from fledermap.store.models import Annotation, Recording


def list_annotations(session: OrmSession, recording_id: int) -> Sequence[Annotation]:
    return session.scalars(
        select(Annotation)
        .where(Annotation.recording_id == recording_id)
        .order_by(Annotation.id),
    ).all()


def _require_comment_shape(taxon_id: int | None, literal: str | None) -> None:
    if taxon_id is None and not literal:
        msg = "a comment annotation needs at least one of taxon_id or literal"
        raise ValueError(msg)


def _require_comment_source(annotation: Annotation) -> None:
    if annotation.source != AnnotationSource.COMMENT:
        msg = f"annotation {annotation.id} is source={annotation.source!r}, not comment"
        raise ValueError(msg)


def create_comment_annotation(
    session: OrmSession,
    recording: Recording,
    *,
    time_start_s: float | None,
    time_end_s: float | None,
    freq_min_hz: float | None,
    freq_max_hz: float | None,
    taxon_id: int | None,
    literal: str | None,
) -> Annotation:
    _require_comment_shape(taxon_id, literal)
    annotation = Annotation(
        recording_id=recording.id,
        source=AnnotationSource.COMMENT,
        time_start_s=time_start_s,
        time_end_s=time_end_s,
        freq_min_hz=freq_min_hz,
        freq_max_hz=freq_max_hz,
        taxon_id=taxon_id,
        literal=literal,
        created_at=datetime.now(UTC),
    )
    session.add(annotation)
    session.commit()
    return annotation


def update_comment_annotation(
    session: OrmSession,
    annotation: Annotation,
    *,
    taxon_id: int | None,
    literal: str | None,
) -> None:
    _require_comment_source(annotation)
    _require_comment_shape(taxon_id, literal)
    annotation.taxon_id = taxon_id
    annotation.literal = literal
    session.commit()


def delete_comment_annotation(session: OrmSession, annotation: Annotation) -> None:
    _require_comment_source(annotation)
    session.delete(annotation)
    session.commit()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `hatch test tests/test_annotations_service.py -v` (`dangerouslyDisableSandbox: true`)
Expected: PASS.

- [ ] **Step 5: Run the full non-db suite + types + lint**

Run: `hatch test -m "not db" && hatch run types:check && hatch fmt`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/fledermap/services/annotations.py tests/test_annotations_service.py
git commit -m "feat: add annotations CRUD service"
```

---

## Task 3: JSON API routes + embed annotations in the page

**Files:**
- Modify: `src/fledermap/web/views/recording_detail.py`
- Test: `tests/test_recording_detail_view.py`

**Interfaces:**
- Consumes: `list_annotations`, `create_comment_annotation`, `update_comment_annotation`,
  `delete_comment_annotation` (Task 2's `services/annotations.py`).
- Produces: page context variable `annotations: list[dict]` (JSON-serializable, embedded as
  `data-annotations` on `#detail-toolbar`, same idiom as `_classifier_box.html`'s
  `data-taxon-search-index`) each shaped `{id, source, time_start_s, time_end_s, freq_min_hz,
  freq_max_hz, taxon_id, literal}`. Routes: `POST /recordings/<audio_hash>/annotations`,
  `PATCH /recordings/<audio_hash>/annotations/<int:annotation_id>`,
  `DELETE /recordings/<audio_hash>/annotations/<int:annotation_id>` on `recording_detail_bp`,
  each returning `flask.jsonify(...)` — the created/updated annotation's serialized dict on
  success, `{"error": "..."}` with a 400/404 status on failure.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_recording_detail_view.py` (reuse `_create_app_with_one_recording`):

```python
def test_annotations_are_embedded_in_the_page(engine: Engine, tmp_path: Path) -> None:
    app, audio_hash = _create_app_with_one_recording(engine, tmp_path, "an" * 32)
    with OrmSession(engine) as session:
        recording = session.scalars(select(Recording)).one()
        create_comment_annotation(
            session,
            recording,
            time_start_s=1.0,
            time_end_s=1.0,
            freq_min_hz=None,
            freq_max_hz=None,
            taxon_id=None,
            literal="embedded note",
        )

    response = app.test_client().get(f"/recordings/{audio_hash}")
    html = response.get_data(as_text=True)
    assert "embedded note" in html
    assert 'data-annotations=' in html


def test_post_annotation_creates_a_comment(engine: Engine, tmp_path: Path) -> None:
    app, audio_hash = _create_app_with_one_recording(engine, tmp_path, "ap" * 32)

    response = app.test_client().post(
        f"/recordings/{audio_hash}/annotations",
        json={
            "time_start_s": 1.0,
            "time_end_s": 1.0,
            "freq_min_hz": None,
            "freq_max_hz": None,
            "taxon_id": None,
            "literal": "new comment",
        },
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["literal"] == "new comment"
    assert body["source"] == "comment"


def test_post_annotation_without_text_or_species_is_rejected(
    engine: Engine, tmp_path: Path,
) -> None:
    app, audio_hash = _create_app_with_one_recording(engine, tmp_path, "aq" * 32)

    response = app.test_client().post(
        f"/recordings/{audio_hash}/annotations",
        json={
            "time_start_s": 1.0,
            "time_end_s": 1.0,
            "freq_min_hz": None,
            "freq_max_hz": None,
            "taxon_id": None,
            "literal": None,
        },
    )

    assert response.status_code == 400


def test_patch_annotation_updates_text(engine: Engine, tmp_path: Path) -> None:
    app, audio_hash = _create_app_with_one_recording(engine, tmp_path, "ar" * 32)
    with OrmSession(engine) as session:
        recording = session.scalars(select(Recording)).one()
        created = create_comment_annotation(
            session,
            recording,
            time_start_s=1.0,
            time_end_s=1.0,
            freq_min_hz=None,
            freq_max_hz=None,
            taxon_id=None,
            literal="before",
        )
        annotation_id = created.id

    response = app.test_client().patch(
        f"/recordings/{audio_hash}/annotations/{annotation_id}",
        json={"taxon_id": None, "literal": "after"},
    )

    assert response.status_code == 200
    assert response.get_json()["literal"] == "after"


def test_delete_annotation_removes_it(engine: Engine, tmp_path: Path) -> None:
    app, audio_hash = _create_app_with_one_recording(engine, tmp_path, "as" * 32)
    with OrmSession(engine) as session:
        recording = session.scalars(select(Recording)).one()
        created = create_comment_annotation(
            session,
            recording,
            time_start_s=1.0,
            time_end_s=1.0,
            freq_min_hz=None,
            freq_max_hz=None,
            taxon_id=None,
            literal="gone soon",
        )
        annotation_id = created.id

    response = app.test_client().delete(
        f"/recordings/{audio_hash}/annotations/{annotation_id}",
    )

    assert response.status_code == 200
    with OrmSession(engine) as session:
        assert list_annotations(session, recording.id) == []
```

Add the needed imports at the top of the test file:
`from fledermap.services.annotations import create_comment_annotation, list_annotations`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `hatch test tests/test_recording_detail_view.py -v` (`dangerouslyDisableSandbox: true`)
Expected: FAIL — routes don't exist yet (404s), embedding assertions fail.

- [ ] **Step 3: Implement the routes and embedding**

In `src/fledermap/web/views/recording_detail.py`, add imports:

```python
from fledermap.services.annotations import (
    create_comment_annotation,
    delete_comment_annotation,
    list_annotations,
    update_comment_annotation,
)
from fledermap.store.models import Annotation
```

Add a new import line near the top of the file (this file has no existing
`fledermap.domain.codes` import to extend): `from fledermap.domain.codes import AnnotationSource`.
Note: the file already has `from fledermap.store.models import Session as AnnotationSession` (an
existing, unrelated alias for the recording-session model, aliased only to avoid colliding with
`sqlalchemy.orm.Session as OrmSession`) — `AnnotationSource`/`Annotation` are different, real
symbols from this task, not related to that alias despite the similar spelling.

Add a serializer near `_taxon_search_index`:

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
        "literal": a.literal,
    }
```

In `recording_details_page`, after `taxon_search_index = _taxon_search_index(session)`:

```python
        annotations = [
            _serialize_annotation(a) for a in list_annotations(session, recording.id)
        ]
```

...and pass `annotations=annotations` in the `flask.render_template(...)` call.

Add the three JSON routes after `recording_details_page`:

```python
def _find_recording_or_404(session: OrmSession, audio_hash: str) -> Recording:
    recording = session.scalars(
        select(Recording).where(Recording.audio_hash == audio_hash),
    ).one_or_none()
    if recording is None:
        flask.abort(404)
    return recording


def _find_comment_annotation_or_404(
    session: OrmSession, recording: Recording, annotation_id: int,
) -> Annotation:
    annotation = session.get(Annotation, annotation_id)
    if annotation is None or annotation.recording_id != recording.id:
        flask.abort(404)
    if annotation.source != AnnotationSource.COMMENT:
        flask.abort(404)
    return annotation


@recording_detail_bp.post("/recordings/<audio_hash>/annotations")
def post_annotation(audio_hash: str) -> flask.Response:
    engine = flask.current_app.config["ENGINE"]
    body = flask.request.get_json(silent=True) or {}
    with OrmSession(engine) as session:
        recording = _find_recording_or_404(session, audio_hash)
        try:
            annotation = create_comment_annotation(
                session,
                recording,
                time_start_s=body.get("time_start_s"),
                time_end_s=body.get("time_end_s"),
                freq_min_hz=body.get("freq_min_hz"),
                freq_max_hz=body.get("freq_max_hz"),
                taxon_id=body.get("taxon_id"),
                literal=body.get("literal"),
            )
        except ValueError as exc:
            return flask.jsonify({"error": str(exc)}), 400
        return flask.jsonify(_serialize_annotation(annotation))


@recording_detail_bp.patch("/recordings/<audio_hash>/annotations/<int:annotation_id>")
def patch_annotation(audio_hash: str, annotation_id: int) -> flask.Response:
    engine = flask.current_app.config["ENGINE"]
    body = flask.request.get_json(silent=True) or {}
    with OrmSession(engine) as session:
        recording = _find_recording_or_404(session, audio_hash)
        annotation = _find_comment_annotation_or_404(session, recording, annotation_id)
        try:
            update_comment_annotation(
                session,
                annotation,
                taxon_id=body.get("taxon_id"),
                literal=body.get("literal"),
            )
        except ValueError as exc:
            return flask.jsonify({"error": str(exc)}), 400
        return flask.jsonify(_serialize_annotation(annotation))


@recording_detail_bp.delete("/recordings/<audio_hash>/annotations/<int:annotation_id>")
def delete_annotation(audio_hash: str, annotation_id: int) -> flask.Response:
    engine = flask.current_app.config["ENGINE"]
    with OrmSession(engine) as session:
        recording = _find_recording_or_404(session, audio_hash)
        annotation = _find_comment_annotation_or_404(session, recording, annotation_id)
        delete_comment_annotation(session, annotation)
        return flask.jsonify({"ok": True})
```

In `recording_details.html`, add `data-annotations='{{ annotations | tojson }}'` as an attribute
on `#detail-toolbar` (the `data-taxon-search-index` idiom, but scoped to the toolbar element
since that's where the Layers menu and Comment tool both need to read it from).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `hatch test tests/test_recording_detail_view.py -v` (`dangerouslyDisableSandbox: true`)
Expected: PASS.

- [ ] **Step 5: Run the full non-db suite + types + lint**

Run: `hatch test -m "not db" && hatch run types:check && hatch fmt`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/fledermap/web/views/recording_detail.py src/fledermap/web/templates/recording_details.html \
  tests/test_recording_detail_view.py
git commit -m "feat: add annotation JSON API and embed annotations in the recording-detail page"
```

---

## Task 4: Pure JS geometry module

**Files:**
- Create: `src/fledermap/web/static/annotation_geometry.js`
- Test: `tests/js/annotation_geometry.test.js`

**Interfaces:**
- Consumes: nothing (pure).
- Produces (globals via `<script>`, `module.exports` for tests — same idiom as `plot_coords.js`):
  `normalizeDragBounds(startTimeS, endTimeS, startFreqHz, endFreqHz)`,
  `annotationShape(annotation)`, `abbreviateScientificName(name)`,
  `annotationBoxPx(annotation, coords)`, `annotationPointPx(annotation, coords)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/js/annotation_geometry.test.js`:

```javascript
const test = require("node:test");
const assert = require("node:assert/strict");
const {
  normalizeDragBounds,
  annotationShape,
  abbreviateScientificName,
  annotationBoxPx,
  annotationPointPx,
} = require("../../src/fledermap/web/static/annotation_geometry.js");

test("normalizeDragBounds orders start<=end regardless of drag direction", () => {
  const forward = normalizeDragBounds(1.0, 2.0, 20000, 50000);
  const backward = normalizeDragBounds(2.0, 1.0, 50000, 20000);
  assert.deepEqual(forward, backward);
  assert.deepEqual(forward, {
    time_start_s: 1.0,
    time_end_s: 2.0,
    freq_min_hz: 20000,
    freq_max_hz: 50000,
  });
});

test("annotationShape is a point when both dimensions collapse", () => {
  assert.equal(
    annotationShape({ time_start_s: 1, time_end_s: 1, freq_min_hz: 20000, freq_max_hz: 20000 }),
    "point",
  );
});

test("annotationShape is a rect when either dimension has a real range", () => {
  assert.equal(
    annotationShape({ time_start_s: 1, time_end_s: 2, freq_min_hz: 20000, freq_max_hz: 20000 }),
    "rect",
  );
});

test("abbreviateScientificName shortens the genus", () => {
  assert.equal(abbreviateScientificName("Pipistrellus pipistrellus"), "P. pipistrellus");
});

test("abbreviateScientificName leaves a single-word name alone", () => {
  assert.equal(abbreviateScientificName("Myotis"), "Myotis");
});

test("annotationBoxPx converts a rect to native pixel geometry", () => {
  const coords = {
    sToX: (s) => s * 100,
    khzToY: (khz) => 480 - khz * 10,
  };
  const box = annotationBoxPx(
    { time_start_s: 1, time_end_s: 2, freq_min_hz: 20000, freq_max_hz: 30000 },
    coords,
  );
  // left = sToX(1) = 100, width = sToX(2)-sToX(1) = 100
  // top = khzToY(30) = 180 (higher freq -> smaller y), height = khzToY(20)-khzToY(30) = 100
  assert.deepEqual(box, { left: 100, top: 180, width: 100, height: 100 });
});

test("annotationPointPx converts a point to native pixel coordinates", () => {
  const coords = {
    sToX: (s) => s * 100,
    khzToY: (khz) => 480 - khz * 10,
  };
  const point = annotationPointPx(
    { time_start_s: 1.5, time_end_s: 1.5, freq_min_hz: 25000, freq_max_hz: 25000 },
    coords,
  );
  assert.deepEqual(point, { x: 150, y: 230 });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `node --test tests/js/annotation_geometry.test.js`
Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Implement `annotation_geometry.js`**

```javascript
// src/fledermap/web/static/annotation_geometry.js -- pure annotation geometry/shape logic, no
// DOM access (same split as plot_coords.js/marker_colors.js -- see CLAUDE.md's "JavaScript
// tooling"). Loaded before recording_detail.js.

// A drawn rectangle's drag can start from any corner; DB rows and every downstream consumer
// (rendering, the CHECK constraint) expect start<=end / min<=max regardless of drag direction.
function normalizeDragBounds(startTimeS, endTimeS, startFreqHz, endFreqHz) {
  return {
    time_start_s: Math.min(startTimeS, endTimeS),
    time_end_s: Math.max(startTimeS, endTimeS),
    freq_min_hz: Math.min(startFreqHz, endFreqHz),
    freq_max_hz: Math.max(startFreqHz, endFreqHz),
  };
}

// A point is where BOTH dimensions collapse to a single value (or are absent) -- a real range in
// either dimension makes it a rect. Today's Comment tool always sets all four bounds together
// (spec's Non-goals: the narrower single-dimension shape modes are a later follow-up), but this
// treats a null bound as "collapsed" too so a future partially-bound annotation doesn't crash.
function annotationShape(annotation) {
  const timeIsPoint =
    annotation.time_start_s === null ||
    annotation.time_start_s === annotation.time_end_s;
  const freqIsPoint =
    annotation.freq_min_hz === null ||
    annotation.freq_min_hz === annotation.freq_max_hz;
  return timeIsPoint && freqIsPoint ? "point" : "rect";
}

// "Pipistrellus pipistrellus" -> "P. pipistrellus", per the Obsidian sketch's genus-abbreviation
// convention (also used for the map's own species labels). A one-word name (a genus/group taxon
// like "Myotis") is returned unchanged -- there's no second word to abbreviate against.
function abbreviateScientificName(name) {
  const parts = name.split(" ");
  if (parts.length < 2) return name;
  return `${parts[0][0]}. ${parts.slice(1).join(" ")}`;
}

// `coords` is a plot_coords.js `makePlotCoords()` result -- `sToX`/`khzToY` take seconds/kHz, so
// callers pass Hz-valued annotation fields divided by 1000.
function annotationBoxPx(annotation, coords) {
  const left = coords.sToX(annotation.time_start_s);
  const width = coords.sToX(annotation.time_end_s) - left;
  const top = coords.khzToY(annotation.freq_max_hz / 1000);
  const height = coords.khzToY(annotation.freq_min_hz / 1000) - top;
  return { left, top, width, height };
}

function annotationPointPx(annotation, coords) {
  return {
    x: coords.sToX(annotation.time_start_s),
    y: coords.khzToY(annotation.freq_min_hz / 1000),
  };
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    normalizeDragBounds,
    annotationShape,
    abbreviateScientificName,
    annotationBoxPx,
    annotationPointPx,
  };
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `node --test tests/js/annotation_geometry.test.js`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/fledermap/web/static/annotation_geometry.js tests/js/annotation_geometry.test.js
git commit -m "feat: add pure annotation geometry helpers"
```

---

## Task 5: Icon, toolbar markup, popup markup, CSS

**Files:**
- Modify: `src/fledermap/services/vendor_assets.py`
- Modify: `src/fledermap/web/templates/recording_details.html`
- Modify: `src/fledermap/web/static/app.css`
- Test: `tests/test_recording_detail_view.py` (markup presence assertions)

**Interfaces:**
- Produces: icon name `stack-2` (outline) available via `icon("stack-2")`; toolbar elements with
  ids `comment-tool-button` (actually `data-tool="comment"` on a `.tool-button`, consistent with
  the others — no separate id needed), `layers-button`, `layers-menu`, `comment-popup` (and its
  children `comment-popup-species-input`, `comment-popup-species-suggestions`,
  `comment-popup-text`, `comment-popup-delete`, `comment-popup-cancel`, `comment-popup-save`) for
  Task 7 to wire up.

- [ ] **Step 1: Write the failing markup tests**

Add to `tests/test_recording_detail_view.py`:

```python
def test_comment_tool_button_is_present(engine: Engine, tmp_path: Path) -> None:
    app, audio_hash = _create_app_with_one_recording(engine, tmp_path, "at" * 32)
    html = app.test_client().get(f"/recordings/{audio_hash}").get_data(as_text=True)
    assert 'data-tool="comment"' in html


def test_layers_button_and_menu_are_present(engine: Engine, tmp_path: Path) -> None:
    app, audio_hash = _create_app_with_one_recording(engine, tmp_path, "au" * 32)
    html = app.test_client().get(f"/recordings/{audio_hash}").get_data(as_text=True)
    assert 'id="layers-button"' in html
    assert 'id="layers-menu"' in html


def test_comment_popup_markup_is_present(engine: Engine, tmp_path: Path) -> None:
    app, audio_hash = _create_app_with_one_recording(engine, tmp_path, "av" * 32)
    html = app.test_client().get(f"/recordings/{audio_hash}").get_data(as_text=True)
    assert 'id="comment-popup"' in html
    assert 'id="comment-popup-save"' in html
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `hatch test tests/test_recording_detail_view.py -v -k "comment_tool_button or layers_ or comment_popup"`
(`dangerouslyDisableSandbox: true`)
Expected: FAIL.

- [ ] **Step 3: Add the vendored icon**

In `src/fledermap/services/vendor_assets.py`, add to `ASSETS` (near the other Tabler entries):

```python
    # The Layers menu button's icon (design spec docs/superpowers/specs/
    # 2026-09-23-fledermap-annotations-design.md §4.1) -- no filled variant needed, the button
    # has no pressed/toggled state of its own (it opens a menu; the menu's checkboxes carry the
    # real on/off state). Fetched and hashed directly against unpkg.com before this plan was
    # written -- not invented.
    VendorAsset(
        url="https://unpkg.com/@tabler/icons@3.46.0/icons/outline/stack-2.svg",
        sha256="729d3e264f4e2e9670cd86f4531141baa78eb94e975198228a8167c8a1eb0572",
        relative_path="icons/outline/stack-2.svg",
    ),
```

- [ ] **Step 4: Add the toolbar buttons and Layers menu markup**

In `recording_details.html`, inside `.toolbar-zone-tools`, after the existing Select button and
before the `view-lock-toggle` button:

```html
        <button type="button" class="tool-button playback-only" data-tool="comment" aria-pressed="false">Comment</button>
        <div class="layers-control playback-only">
          <button type="button" id="layers-button" title="Show/hide annotation layers" disabled>
            {{ icon("stack-2") }}
          </button>
          <div id="layers-menu" class="layers-menu" hidden></div>
        </div>
```

(`layers-menu`'s checkbox list is built by JS in Task 6, since it depends on which sources are
present in the embedded `annotations` data — nothing static to render server-side beyond the
empty container.)

- [ ] **Step 5: Add the comment popup markup**

Near the existing `#ruler-readout`/`#crosshair-readout` floating elements (end of the body,
outside `.detail-toolbar`):

```html
  <div id="comment-popup" class="floating-readout comment-popup" hidden>
    <div class="comment-popup-species">
      <input type="text" id="comment-popup-species-input" placeholder="Species (optional)" autocomplete="off">
      <div id="comment-popup-species-suggestions" class="comment-popup-suggestions" hidden></div>
    </div>
    <textarea id="comment-popup-text" placeholder="Note (optional)"></textarea>
    <div class="comment-popup-actions">
      <button type="button" id="comment-popup-delete" hidden>Delete</button>
      <button type="button" id="comment-popup-cancel">Cancel</button>
      <button type="button" id="comment-popup-save" disabled>Save</button>
    </div>
  </div>
```

- [ ] **Step 6: Add CSS**

In `src/fledermap/web/static/app.css`, add a `tool-comment` cursor rule next to the existing
`.detail-spectrogram-wrap.tool-ruler`/`.tool-select` rules (same crosshair-while-drawing cursor):

```css
.detail-spectrogram-wrap.tool-comment { cursor: crosshair; }
```

Then, near `.ruler-box`/`.ruler-readout`:

```css
/* Annotation overlay -- boxes/points are children of `wrap` (same as .ruler-box), positioned in
   native px via annotation_geometry.js's annotationBoxPx/annotationPointPx. pointer-events is
   `none` by default: an annotation must never intercept Default's click-to-play/drag-to-pan or
   Ruler's drag-to-measure, and must not even change the cursor under any tool but Comment
   (recording_detail.js toggles a `comment-interactive` class on `wrap` while the Comment tool is
   active, which re-enables pointer-events on .annotation-box/.annotation-point). */
.annotation-box, .annotation-point {
  position: absolute;
  pointer-events: none;
  z-index: 2; /* same reason as .ruler-box: clears .detail-axis-freq's sticky z-index: 1 */
}
.annotation-box {
  border: 1px solid var(--color-border);
  background: color-mix(in srgb, var(--color-accent) 12%, transparent);
}
.annotation-point {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  transform: translate(-50%, -50%);
  background: var(--color-accent);
  border: 1px solid var(--color-bg);
}
.comment-interactive .annotation-box,
.comment-interactive .annotation-point {
  pointer-events: auto;
  cursor: pointer;
}
.annotation-label {
  position: absolute;
  top: 0;
  left: 0;
  transform: translateY(-100%);
  font-size: 0.65rem;
  line-height: 1.2;
  white-space: nowrap;
  color: var(--color-text);
  background: color-mix(in srgb, var(--color-bg) 85%, transparent);
  padding: 0 2px;
}

.layers-control { position: relative; }
.layers-menu {
  position: absolute;
  top: 100%;
  right: 0;
  z-index: 1001; /* matches .crosshair-readout/.ruler-readout */
  background: var(--color-bg);
  border: 1px solid var(--color-border);
  border-radius: 4px;
  padding: 0.4rem;
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
  white-space: nowrap;
}
.layers-menu[hidden] { display: none; }

.comment-popup {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
  width: 220px;
}
.comment-popup textarea {
  resize: vertical;
  min-height: 3em;
  font: inherit;
}
.comment-popup-suggestions {
  position: absolute;
  background: var(--color-bg);
  border: 1px solid var(--color-border);
  border-radius: 4px;
  max-height: 8em;
  overflow-y: auto;
  z-index: 1;
}
.comment-popup-suggestions[hidden] { display: none; }
.comment-popup-actions {
  display: flex;
  justify-content: flex-end;
  gap: 0.3rem;
}
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `hatch test tests/test_recording_detail_view.py -v -k "comment_tool_button or layers_ or comment_popup"`
(`dangerouslyDisableSandbox: true`)
Expected: PASS.

- [ ] **Step 8: Run the full non-db suite + types + lint**

Run: `hatch test -m "not db" && hatch run types:check && hatch fmt`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add src/fledermap/services/vendor_assets.py src/fledermap/web/templates/recording_details.html \
  src/fledermap/web/static/app.css tests/test_recording_detail_view.py
git commit -m "feat: add Layers/Comment toolbar markup, comment popup markup, and CSS"
```

---

## Task 6: Render annotations + Layers menu (display only)

**Files:**
- Modify: `src/fledermap/web/static/recording_detail.js`
- Modify: `src/fledermap/web/templates/recording_details.html` (add `<script>` tag)

**Interfaces:**
- Consumes: `annotation_geometry.js`'s `annotationShape`, `abbreviateScientificName`,
  `annotationBoxPx`, `annotationPointPx`; `marker_colors.js`'s `colorForTaxon`; `coords`
  (existing `makePlotCoords()` result, already in scope in `recording_detail.js`); the
  `#detail-toolbar`'s `data-annotations` JSON (Task 3) and the `#classifier-box`'s
  `data-taxon-search-index` JSON (existing, for species names by id).
- Produces (module-scope functions in `recording_detail.js`, no exports needed — this file is
  never `require()`-d): `renderAnnotations()`, `isLayerVisible(source)`,
  `setLayerVisible(source, visible)`. Later tasks (7) call these.

This task has no DB/pytest component — it's pure JS wiring, verified with the JS unit tests
already written (Task 4) plus this task's own manual smoke check (Step 4) and Task 8's full
headless-Chrome pass.

- [ ] **Step 1: Add the `<script>` tag**

In `recording_details.html`, add `<script src="{{ url_for('static', filename='annotation_geometry.js') }}"></script>`
immediately before the existing `plot_coords.js` `<script>` tag (load order: pure modules first,
same as `marker_colors.js`/`classifier_logic.js` today).

- [ ] **Step 2: Add annotation state + Layers menu to `recording_detail.js`**

Add near the top of the file's setup (after `const coords = makePlotCoords(...)` at line ~217,
before tool-switching code around line 605):

```javascript
  // Annotations (design spec docs/superpowers/specs/2026-09-23-fledermap-annotations-design.md).
  // Deliberately reads #detail-toolbar directly here rather than via the `toolbar` const --
  // that const isn't declared until the tool-switching section further down (~line 812), and
  // this block runs earlier (right after `coords`); introducing a second `const toolbar` there
  // would be a duplicate-declaration SyntaxError in the same scope.
  let annotations = JSON.parse(
    document.getElementById("detail-toolbar").dataset.annotations || "[]",
  );
  const taxonSearchIndex = JSON.parse(
    document.getElementById("classifier-box")?.dataset.taxonSearchIndex || "[]",
  );
  const taxonById = new Map(taxonSearchIndex.map((t) => [t.id, t]));

  const LAYER_STORAGE_PREFIX = "fledermap-annotation-layer-";

  function isLayerVisible(source) {
    return localStorage.getItem(LAYER_STORAGE_PREFIX + source) === "true";
  }

  function setLayerVisible(source, visible) {
    localStorage.setItem(LAYER_STORAGE_PREFIX + source, visible ? "true" : "false");
    renderAnnotations();
  }

  const layersButton = document.getElementById("layers-button");
  const layersMenu = document.getElementById("layers-menu");

  function rebuildLayersMenu() {
    const sources = [...new Set(annotations.map((a) => a.source))].sort();
    layersMenu.innerHTML = "";
    layersButton.disabled = sources.length === 0;
    for (const source of sources) {
      const label = document.createElement("label");
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.checked = isLayerVisible(source);
      checkbox.addEventListener("change", () => setLayerVisible(source, checkbox.checked));
      label.appendChild(checkbox);
      label.append(` ${source[0].toUpperCase()}${source.slice(1)}`);
      layersMenu.appendChild(label);
    }
  }

  layersButton.addEventListener("click", (event) => {
    event.stopPropagation();
    layersMenu.hidden = !layersMenu.hidden;
  });
  document.addEventListener("click", (event) => {
    if (!layersMenu.hidden && !layersMenu.contains(event.target) && event.target !== layersButton) {
      layersMenu.hidden = true;
    }
  });

  const annotationLayer = document.createElement("div");
  annotationLayer.className = "annotation-layer";
  wrap.appendChild(annotationLayer);

  function renderAnnotations() {
    annotationLayer.innerHTML = "";
    for (const annotation of annotations) {
      const forceVisible = annotation.source === "comment" && activeTool === "comment";
      if (!forceVisible && !isLayerVisible(annotation.source)) continue;

      const shape = annotationShape(annotation);
      const el = document.createElement("div");
      el.dataset.annotationId = String(annotation.id);
      if (shape === "point") {
        const { x, y } = annotationPointPx(annotation, coords);
        el.className = "annotation-point";
        el.style.left = `${x}px`;
        el.style.top = `${y}px`;
        el.style.background =
          annotation.taxon_id !== null ? colorForTaxon(annotation.taxon_id) : "";
      } else {
        const box = annotationBoxPx(annotation, coords);
        el.className = "annotation-box";
        el.style.left = `${box.left}px`;
        el.style.top = `${box.top}px`;
        el.style.width = `${box.width}px`;
        el.style.height = `${box.height}px`;
        if (annotation.taxon_id !== null) {
          el.style.borderColor = colorForTaxon(annotation.taxon_id);
        }
      }

      const taxon = annotation.taxon_id !== null ? taxonById.get(annotation.taxon_id) : null;
      if (taxon || annotation.literal) {
        const label = document.createElement("span");
        label.className = "annotation-label";
        const lines = [];
        if (taxon) lines.push(abbreviateScientificName(taxon.scientific_name));
        if (annotation.literal) lines.push(annotation.literal);
        // textContent, never innerHTML: `literal` is user-authored free text (Review Focus).
        label.textContent = lines.join("\n");
        label.style.whiteSpace = "pre-line";
        el.appendChild(label);
      }

      annotationLayer.appendChild(el);
    }
  }

  rebuildLayersMenu();
  // No initial renderAnnotations() call here: it reads `activeTool`, which is a `let` declared
  // later in the tool-switching section (~line 814) and not yet initialized at this point in the
  // script's top-to-bottom execution -- calling it now would throw "Cannot access 'activeTool'
  // before initialization". Step 3 makes `setActiveTool("default")` (already called once at
  // ~line 835, after `activeTool` exists) call renderAnnotations() as part of every tool switch,
  // which covers the first render for free -- no separate bootstrap call needed.
```

- [ ] **Step 3: Force-visibility + re-render on tool switch**

In `setActiveTool` (around line 816), add a call to `renderAnnotations()` and toggle the
interactive class:

```javascript
  function setActiveTool(tool) {
    activeTool = tool;
    toolButtons.forEach((btn) => {
      const isActive = btn.dataset.tool === tool;
      btn.setAttribute("aria-pressed", isActive ? "true" : "false");
    });
    wrap.classList.toggle("tool-default", tool === "default");
    wrap.classList.toggle("tool-ruler", tool === "ruler");
    wrap.classList.toggle("tool-select", tool === "select");
    wrap.classList.toggle("tool-comment", tool === "comment");
    wrap.classList.toggle("comment-interactive", tool === "comment");
    clearRulerBox();
    if (tool !== "select") clearSelectionBox();
    renderAnnotations();
  }
```

- [ ] **Step 4: Manual smoke check**

Run the app (`hatch run fledermap serve` against a dev DB with at least one comment annotation
inserted via a Python shell calling `create_comment_annotation` directly, or defer this check to
Task 8's full headless-Chrome pass if none exists yet) and confirm in a real browser: the Layers
button is disabled with zero annotations, enabled and toggling visibility with one present, and
the box/point renders at the correct spectrogram position. This step is a checkpoint, not a
blocker — Task 8 covers it formally; note here if anything looks wrong so it's fixed before Task
7 builds on top of it.

- [ ] **Step 5: Run the JS unit suite (regression check)**

Run: `node --test tests/js/`
Expected: PASS (no existing test should have broken).

- [ ] **Step 6: Run types + lint**

Run: `hatch run types:check && hatch fmt`
Expected: PASS. (No Python changed in this task beyond none — this step mainly re-confirms
nothing else regressed; `hatch test -m "not db"` too.)

- [ ] **Step 7: Commit**

```bash
git add src/fledermap/web/static/recording_detail.js src/fledermap/web/templates/recording_details.html
git commit -m "feat: render annotation overlay and Layers menu"
```

---

## Task 7: Comment tool — drawing, popup, CRUD wiring

**Files:**
- Modify: `src/fledermap/web/static/recording_detail.js`

**Interfaces:**
- Consumes: `normalizeDragBounds` (Task 4), `renderAnnotations`/`annotations`/`taxonById`/
  `taxonSearchIndex` (Task 6), `matchesQuery` (existing `classifier_logic.js`, already loaded on
  this page), `tools[activeTool]` pattern (existing), `coords` (existing).
- Produces: a fifth entry in the `tools` object (`comment`), popup show/hide/save/cancel/delete
  wiring, click-to-edit on existing comment annotations.

- [ ] **Step 1: Add popup element refs and state**

Near the annotation state added in Task 6:

```javascript
  const commentPopup = document.getElementById("comment-popup");
  const commentSpeciesInput = document.getElementById("comment-popup-species-input");
  const commentSpeciesSuggestions = document.getElementById("comment-popup-species-suggestions");
  const commentText = document.getElementById("comment-popup-text");
  const commentDeleteBtn = document.getElementById("comment-popup-delete");
  const commentCancelBtn = document.getElementById("comment-popup-cancel");
  const commentSaveBtn = document.getElementById("comment-popup-save");

  let pendingDraft = null; // { time_start_s, time_end_s, freq_min_hz, freq_max_hz } while creating
  let editingAnnotationId = null; // set while editing an existing annotation, else null
  let selectedTaxonId = null;

  function updateSaveEnabled() {
    commentSaveBtn.disabled = !selectedTaxonId && commentText.value.trim() === "";
  }
  commentText.addEventListener("input", updateSaveEnabled);

  function closeCommentPopup() {
    commentPopup.hidden = true;
    pendingDraft = null;
    editingAnnotationId = null;
    selectedTaxonId = null;
    commentSpeciesInput.value = "";
    commentText.value = "";
    commentSpeciesSuggestions.hidden = true;
  }

  function positionCommentPopup(clientX, clientY) {
    commentPopup.style.left = `${clientX}px`;
    commentPopup.style.top = `${clientY}px`;
    commentPopup.hidden = false;
  }

  commentSpeciesInput.addEventListener("input", () => {
    selectedTaxonId = null;
    updateSaveEnabled();
    const query = commentSpeciesInput.value.trim();
    commentSpeciesSuggestions.innerHTML = "";
    if (query === "") {
      commentSpeciesSuggestions.hidden = true;
      return;
    }
    const matches = taxonSearchIndex.filter((t) => matchesQuery(t, query)).slice(0, 8);
    commentSpeciesSuggestions.hidden = matches.length === 0;
    for (const taxon of matches) {
      const item = document.createElement("div");
      item.textContent = taxon.scientific_name;
      item.addEventListener("click", () => {
        selectedTaxonId = taxon.id;
        commentSpeciesInput.value = taxon.scientific_name;
        commentSpeciesSuggestions.hidden = true;
        updateSaveEnabled();
      });
      commentSpeciesSuggestions.appendChild(item);
    }
  });

  commentCancelBtn.addEventListener("click", closeCommentPopup);

  commentDeleteBtn.addEventListener("click", () => {
    if (editingAnnotationId === null) return;
    fetch(`${recordingAnnotationsUrl}/${editingAnnotationId}`, { method: "DELETE" })
      .then((response) => {
        if (!response.ok) throw new Error(`delete failed: ${response.status}`);
        annotations = annotations.filter((a) => a.id !== editingAnnotationId);
        rebuildLayersMenu();
        renderAnnotations();
        closeCommentPopup();
      });
  });

  commentSaveBtn.addEventListener("click", () => {
    const literal = commentText.value.trim() || null;
    const taxonId = selectedTaxonId;
    if (editingAnnotationId !== null) {
      fetch(`${recordingAnnotationsUrl}/${editingAnnotationId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ taxon_id: taxonId, literal }),
      })
        .then((response) => response.json())
        .then((updated) => {
          annotations = annotations.map((a) => (a.id === updated.id ? updated : a));
          renderAnnotations();
          closeCommentPopup();
        });
    } else {
      fetch(recordingAnnotationsUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...pendingDraft, taxon_id: taxonId, literal }),
      })
        .then((response) => response.json())
        .then((created) => {
          annotations = [...annotations, created];
          rebuildLayersMenu();
          renderAnnotations();
          closeCommentPopup();
        });
    }
  });

  function openCommentPopupForCreate(draft, clientX, clientY) {
    editingAnnotationId = null;
    pendingDraft = draft;
    commentDeleteBtn.hidden = true;
    updateSaveEnabled();
    positionCommentPopup(clientX, clientY);
  }

  function openCommentPopupForEdit(annotation, clientX, clientY) {
    editingAnnotationId = annotation.id;
    pendingDraft = null;
    const taxon = annotation.taxon_id !== null ? taxonById.get(annotation.taxon_id) : null;
    selectedTaxonId = annotation.taxon_id;
    commentSpeciesInput.value = taxon ? taxon.scientific_name : "";
    commentText.value = annotation.literal || "";
    commentDeleteBtn.hidden = false;
    updateSaveEnabled();
    positionCommentPopup(clientX, clientY);
  }
```

Add, in the same early block as Task 6's `annotations`/`taxonSearchIndex` (same
`document.getElementById("detail-toolbar")` reasoning as there — not the later `toolbar` const):

```javascript
  const recordingAnnotationsUrl = document.getElementById("detail-toolbar").dataset.annotationsUrl;
```

and add `data-annotations-url="{{ url_for('recording_detail.post_annotation', audio_hash=recording.audio_hash) }}"`
to `#detail-toolbar` in `recording_details.html` (same element that already carries
`data-annotations`).

- [ ] **Step 2: Wire clicking an existing comment annotation to open edit mode**

In `renderAnnotations()` (Task 6), after `annotationLayer.appendChild(el)`, add (only reachable
when `comment-interactive` makes the element clickable at all — see CSS from Task 5):

```javascript
      if (annotation.source === "comment") {
        el.addEventListener("mousedown", (event) => {
          if (activeTool !== "comment") return;
          event.stopPropagation();
        });
        el.addEventListener("click", (event) => {
          if (activeTool !== "comment") return;
          event.stopPropagation();
          openCommentPopupForEdit(annotation, event.clientX, event.clientY);
        });
      }
```

(`stopPropagation` on `mousedown` keeps `wrap`'s own mousedown→drag-vs-click state machine from
also starting a new-annotation drag/click when the gesture actually landed on an existing box.)

- [ ] **Step 3: Add the `comment` tool to the `tools` object**

In the `tools` object (around line 951, after `select`):

```javascript
    comment: {
      onClick(event) {
        const rect = wrap.getBoundingClientRect();
        const xPx = (event.clientX - rect.left) / currentScale;
        const yPx = (event.clientY - rect.top) / currentScale;
        const timeS = coords.xToS(xPx);
        const freqHz = coords.yToKhz(yPx) * 1000;
        openCommentPopupForCreate(
          { time_start_s: timeS, time_end_s: timeS, freq_min_hz: freqHz, freq_max_hz: freqHz },
          event.clientX,
          event.clientY,
        );
      },
      onDrag(event, dragStart) {
        updateCommentDragBox(dragStart, event);
      },
      onDragEnd(event) {
        if (!pendingDragBox) return;
        openCommentPopupForCreate(pendingDragBox.bounds, event.clientX, event.clientY);
        pendingDragBox.el.remove();
        pendingDragBox = null;
      },
    },
```

- [ ] **Step 4: Live drag-rectangle preview**

Add near `updateSelectionBox`/`updateRulerBox`:

```javascript
  let pendingDragBox = null;

  function updateCommentDragBox(dragStart, event) {
    const rect = wrap.getBoundingClientRect();
    const scrollDriftPx = scrollEl.scrollLeft - dragStart.scrollLeft;
    const startX = (dragStart.x - scrollDriftPx - rect.left) / currentScale;
    const startY = (dragStart.y - rect.top) / currentScale;
    const curX = (event.clientX - rect.left) / currentScale;
    const curY = (event.clientY - rect.top) / currentScale;

    if (!pendingDragBox) {
      const el = document.createElement("div");
      el.className = "annotation-box";
      wrap.appendChild(el);
      pendingDragBox = { el, bounds: null };
    }
    const left = Math.min(startX, curX);
    const top = Math.min(startY, curY);
    pendingDragBox.el.style.left = `${left}px`;
    pendingDragBox.el.style.top = `${top}px`;
    pendingDragBox.el.style.width = `${Math.abs(curX - startX)}px`;
    pendingDragBox.el.style.height = `${Math.abs(curY - startY)}px`;

    pendingDragBox.bounds = normalizeDragBounds(
      coords.xToS(startX),
      coords.xToS(curX),
      coords.yToKhz(startY) * 1000,
      coords.yToKhz(curY) * 1000,
    );
  }
```

- [ ] **Step 5: Cancel an in-progress drag draft on popup Cancel**

In `closeCommentPopup` (Step 1), no drag-box cleanup is needed for the click case (nothing was
ever added to `wrap`), but a cancelled drag-created box must also be removable if the popup opens
and is then cancelled: `openCommentPopupForCreate` already runs after `pendingDragBox.el.remove()`
in `onDragEnd`, so by the time the popup is open there's no lingering preview element — no
further change needed here. Note this explicitly so a reviewer doesn't need to re-derive it.

- [ ] **Step 6: Manual smoke check**

Run the app and, in a real browser: switch to the Comment tool, click to create a point, drag to
create a rectangle, save each with text only / species only / both, cancel a draft, edit an
existing one, delete one. Confirm Save stays disabled with both fields empty. This is a
checkpoint before Task 8's formal pass — fix anything wrong here first.

- [ ] **Step 7: Run the JS unit suite + full non-db suite + types + lint**

Run: `node --test tests/js/ && hatch test -m "not db" && hatch run types:check && hatch fmt`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/fledermap/web/static/recording_detail.js src/fledermap/web/templates/recording_details.html
git commit -m "feat: add Comment tool drawing, popup, and CRUD wiring"
```

---

## Task 8: Live verification (headless Chrome) + post-implementation UX check

**Files:** none (verification only; fixes to prior tasks' files if something's wrong).

This task has no automated test of its own — it's the project's mandatory live-verification pass
for any JS change (CLAUDE.md's JavaScript tooling section), plus the spec's required
post-implementation UX check on the comment popup (spec §4.5).

- [ ] **Step 1: Start the app and open a recording with no annotations**

Confirm: Layers button is disabled. Comment tool button is present and selectable.

- [ ] **Step 2: Draw and save a point and a rectangle**

Switch to Comment tool. Click once (point, both text and species empty → Save disabled). Type a
note → Save enables → Save. Confirm the point renders at the exact click location (cross-check
against `plot_coords.js`'s own tick math — same technique this project's axis-tick verification
already uses). Drag a rectangle, add a species via the typeahead, Save. Confirm the box renders
at the dragged bounds.

- [ ] **Step 3: Switch tools and confirm force-visibility + non-interference**

Switch to Default tool: confirm the just-created annotations are hidden (Comments layer
checkbox is still unchecked by default) and clicking/dragging over their former location plays
audio / pans, with the cursor never changing shape. Switch to Ruler: confirm the same (cursor
stays crosshair from Ruler itself, not affected by annotations). Switch back to Comment: confirm
both annotations reappear (force-visible) and the cursor is `pointer` when hovering one of them.

- [ ] **Step 4: Layers menu — visibility and persistence**

Under any non-Comment tool, open the Layers menu, check "Comments" — confirm both annotations
now render. Reload the page: confirm the checkbox is still checked and annotations still render
(localStorage persistence). Uncheck it, confirm they hide.

- [ ] **Step 5: Edit and delete**

With Comment tool active and Comments visible, click the point annotation: confirm the popup
opens pre-filled with its text/species and a Delete button. Change the text, Save: confirm the
label updates immediately (no reload). Click the rectangle annotation, Delete: confirm it
disappears immediately and, if the recording now has zero comment annotations, the Layers button
becomes disabled again after a reload (or immediately, if Task 6/7's `rebuildLayersMenu` call
after delete already handles the zero-annotations case — verify it does; if the button doesn't
re-disable, fix `rebuildLayersMenu`/the delete handler in Task 7's file before proceeding).

- [ ] **Step 6: XSS check (Review Focus)**

Create a comment whose text is literally `<img src=x onerror=alert(1)>`. Confirm it renders as
plain visible text in both the annotation label and the popup's textarea when reopened for edit
— no alert fires, no broken layout from unescaped HTML. If it does execute or render as markup,
find every place `literal`/species text is written into the DOM in `recording_detail.js` and fix
it to use `.textContent` instead of `innerHTML`/template-string HTML.

- [ ] **Step 7: Post-implementation UX check (spec §4.5, required)**

Using the anchored popup as an actual user would (not just clicking through the happy path):
judge whether it feels comfortable — is it too easy to misclick outside and lose an in-progress
draft, does it feel cramped, does it fight with the spectrogram's own scroll/zoom or the
drag-to-resize drawer edge, is the anchor position sometimes awkward (e.g. right at a screen
edge)? Record a clear yes/no judgment. If the anchored popup doesn't feel comfortable, switch it
to an actual modal/dialog overlay (centered, not anchored) per the spec's explicit fallback —
this would be a follow-up change to Task 5/6/7's popup markup and positioning logic, not
something to leave unresolved.

- [ ] **Step 8: Multi-source Layers menu (synthetic check for Review Focus)**

Since no real second producer exists yet, manually insert one `AnnotationSource.BATDETECT2` row
via a Python shell (`hatch run python` — a throwaway script, not a project file) against the dev
DB for the same recording used above, reload the page, and confirm: the Layers menu now shows
two independent checkboxes ("Comments", "Batdetect2"), toggling one doesn't affect the other's
checked state or visibility, and the synthetic row is never force-visible under the Comment tool
(only `comment`-source rows get that exemption). Delete the synthetic row afterward (or just
discard the dev DB state — it was only for this check).

- [ ] **Step 9: Final full verification and commit**

Run: `hatch test && hatch run types:check && hatch fmt && node --test tests/js/`
(`dangerouslyDisableSandbox: true` for the `db`-marked tests in the full `hatch test`.)
Expected: everything PASS.

If Step 7 required switching to a modal, or any other step required a fix, commit those fixes
now:

```bash
git add -A -- src/fledermap tests
git commit -m "fix: address live-verification findings for the annotations feature"
```

If nothing needed fixing, no commit is needed for this task.
