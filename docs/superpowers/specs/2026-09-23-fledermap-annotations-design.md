# Fledermap — annotations design

Status: proposed. Scope: a new `Annotation` table + `AnnotationSource` enum
(`store/models.py`), a `services/annotations.py` CRUD service (comment-kind rows only), a
Layers toolbar menu + Comment tool on the recording-detail page (`recording_details.html`,
`recording_detail.js`, `web/views/recording_detail.py`). Obsidian backlog's `#Annotations`
section (plus the related "Comment tool" and "Recording detail page (DAW view): overlays for
batdetect2 call boxes" items) is the trigger.

## 1. Problem

Three planned features — a user-facing comment tool, a future batdetect2 per-call overlay, and
a future call-distance graph — all need the same underlying thing: a way to record "something
at this place in this recording" (a time range, a frequency band, a point, or a rectangle of
both) with an optional species and an optional note, and a way to display it on the recording-
detail page's spectrogram/oscillogram with per-source visibility control. Building the comment
tool without that shared model first would either hardcode "comment" into a table meant to hold
more than that, or duplicate a batdetect2 has-a-time/freq-box-and-species shape into a second
table later when that classifier gains per-call output.

This spec designs that shared model and mechanism, and implements the comment tool as its first
concrete producer/consumer. Batdetect2 per-call boxes and the call-distance graph are **not**
built here — see Non-goals.

## 2. Data model

New table `annotation`:

| Column | Type | Notes |
|---|---|---|
| `id` | PK | |
| `recording_id` | FK → `recording.id`, `ondelete="CASCADE"`, indexed | Same shape as `ClassifierDetection.recording_id`. |
| `source` | `AnnotationSource` (new enum, see below) | `create_constraint=False`, indexed — mirrors `IdSource`/`ClassifierDetection.source`: more producers are coming, a CHECK would force a migration per one. |
| `time_start_s` | `float \| None` | |
| `time_end_s` | `float \| None` | A point-in-time is `time_start_s == time_end_s`; no separate shape/kind column. |
| `freq_min_hz` | `float \| None` | |
| `freq_max_hz` | `float \| None` | A single-frequency mark is `freq_min_hz == freq_max_hz`. |
| `taxon_id` | `int \| None`, FK → `taxon.id` | Optional on every source, including `comment`. |
| `confidence` | `float \| None` | Classifier-only; always `NULL` for `comment` rows — nothing in this spec writes it. |
| `literal` | `str \| None` | Free-text note. For a `comment` row, at least one of `literal`/`taxon_id` is always set (enforced by `create_comment_annotation`, §3/§4.5 — not a DB constraint, since a future non-comment source may legitimately have neither). |
| `created_at` | `datetime`, server default `now()` | |

CHECK constraint `ck_annotation_has_bound`: `time_start_s IS NOT NULL OR freq_min_hz IS NOT
NULL` — rejects a fully-unbound row (nothing for it to point at). This is the same kind of
type-bound-adjacent constraint `tests/test_migrations.py`'s `_comparable` filter already has to
reason about for enum CHECKs; unlike those, this one is a plain non-enum CHECK, so it needs no
addition to that exclusion list — confirm with a `db`-marked test that inserting an all-NULL-
bounds row raises `IntegrityError`.

New enum `AnnotationSource` (`domain/codes.py`, next to `IdSource`, same `StrEnum` idiom):

```python
class AnnotationSource(StrEnum):
    COMMENT = "comment"
    # Reserved for later specs, not produced by any code yet:
    BATDETECT2 = "batdetect2"
    BATTYBIRDNET = "battybirdnet"
```

Deliberately a separate enum from `IdSource`, not a reused/extended one: `comment` has no
meaning as an identification-claim source, and `IdSource` already carries device/classifier
semantics (`current_best.py` precedence, `review_flags.py` disagreement voting) that annotation
sources don't participate in at all.

Migration: standard Alembic autogenerate + `tests/test_migrations.py`'s drift check, same as
any other new table. No CHECK-on-existing-column case (§ "Adding a CHECK to an EXISTING enum
column" in `CLAUDE.md`) applies here — this is a new table, so autogenerate should produce the
CHECK correctly; verify against the drift test rather than assuming.

## 3. Service layer (`services/annotations.py`)

- `list_annotations(session, recording_id) -> Sequence[Annotation]` — every annotation on a
  recording, all sources, for the display fetch. Grouping by source (for the Layers menu) is a
  presentation concern, done by the caller (`web/views/recording_detail.py` or the JS), not the
  service.
- `create_comment_annotation(session, recording, *, time_start_s, time_end_s, freq_min_hz,
  freq_max_hz, taxon_id, literal) -> Annotation` — raises `ValueError` if both `taxon_id` and
  `literal` are `None` (§4.5: at least one is required).
- `update_comment_annotation(session, annotation, *, taxon_id, literal) -> None` — same
  at-least-one-of validation as create. Geometry is immutable after creation (redrawing is
  delete-and-recreate, matching how the Ruler/Select boxes already work — there's no "resize an
  existing box" interaction anywhere on this page today).
- `delete_comment_annotation(session, annotation) -> None`

All four are the **only** way `comment`-source rows are ever written or removed — enforced by
convention, not a DB guard (confirmed choice): every function here asserts
`annotation.source == AnnotationSource.COMMENT` (or, for create, always sets it), the same
separation `services/identifications.py`'s `replace_claims` (classifier-written) and
`services/manual_classification.py`'s `set_manual_classification` (user-written) already have
for `Identification` rows. A future batdetect2-annotation producer writes/deletes its own rows
directly against the `Annotation` model, never through this module.

## 4. Comment tool (recording-detail page)

### 4.1 Toolbar placement

Mockup (approved):

```
toolbar-zone-tools (playback-only):
┌────────┬───────┬────────┬─────────┬──────────┬─────────────────────┐
│ Default│ Ruler │ Select │ Comment │  Layers ▾│  🔒 Lock view        │
└────────┴───────┴────────┴─────────┴──────────┴─────────────────────┘
```

- **Comment** joins the existing tool-switch cluster (`Default`/`Ruler`/`Select`) as a fourth
  `.tool-button.playback-only`, `data-tool="comment"` — same activation mechanism
  (`setActiveTool`) as the other three.
- **Layers** is a new button immediately after the tool cluster, before `Lock view` — a stacked-
  squares icon (new Tabler icon added to `web/icons.py`, matching the icon style you referenced
  from Leaflet's own layer control) that opens a dropdown menu, not a tool switch itself.
  `playback-only` — Analysis view shows different graphs (sound-level spectrum, future call-
  distance graph), not the spectrogram/oscillogram raster annotations overlay, so a Layers menu
  there would control nothing.

### 4.2 Layers menu

- Lists one checkbox per `AnnotationSource` that has **≥1 annotation on the current recording**
  — derived from `list_annotations`'s result, not a static list of every enum value. Today
  that's just "Comments"; a "Batdetect2" row appears automatically once a future spec's producer
  starts writing rows, no config to update here.
- Each checkbox's state persists independently in `localStorage`, keyed per source (e.g.
  `fledermap-annotation-layer-comment`), so toggling one source doesn't affect another — matches
  the "off by default or last selection" behavior from the Obsidian sketch. This is the first
  toggle on this page to persist (Denoise/View Lock/tool selection are all session-only today);
  it's a deliberate, per-viewer-convenience use of `localStorage`, not a precedent that changes
  those other toggles.
- Unchecked by default the very first time a source appears (no stored value yet).
- The Layers button itself is disabled (not just an empty menu) when the recording has zero
  annotations of any source — nothing to toggle, so no reason to invite a click.
- `localStorage` keys are per-source and never pruned to "sources present on this recording" —
  a value set for a source while viewing one recording must still apply the next time that
  source's checkbox appears on a different recording, even though a recording with no
  annotations of that source never rendered the checkbox in between. The stored value is read
  independently of which checkboxes the current page happens to show.
- Implemented as vanilla JS (toggle `hidden` on the dropdown `<div>`, a `document` click listener
  to close it on outside-click, each checkbox's `change` handler updating `localStorage` and
  visibility), matching every other toggle already on this page (View Lock, Denoise, tool
  switching) — deliberately not Alpine.js. Alpine is loaded site-wide and used elsewhere (nav's
  theme toggle, `map.html`'s filter form) but has zero presence on `recording_details.html`
  today; that page's JS is structured for `node:test` coverage of DOM-independent logic plus
  mandatory headless-Chrome verification for the rest (§6), and introducing Alpine for just this
  one control would be a new, inconsistent pattern rather than following that.

### 4.3 Rendering

- Annotation boxes/points are HTML elements positioned via `plot_coords.js`'s existing
  pixel↔time/freq mapping — the same approach `ruler-box`/`selection-box` already use, not baked
  into the spectrogram raster. No `geometry_version` bump, no render-cache involvement.
- A rectangle (`time_start_s != time_end_s` and `freq_min_hz != freq_max_hz`, or one pair null
  and the other a real range) renders as a bordered box, light transparent fill, per the
  Obsidian sketch. A point (`time_start_s == time_end_s` and `freq_min_hz == freq_max_hz`, per
  §4.4's click behavior) renders as a small marker/dot instead of a zero-size box.
- Species-tagged annotations use that taxon's existing marker color (`marker_colors.js`);
  untagged annotations use a neutral color. Label at the box's top-left: abbreviated genus
  (`P. pipistrellus`) if a species is set, the `literal` text in a second row if both are
  present, per the Obsidian sketch.
- **Every annotation element has `pointer-events: none` by default**, regardless of which
  source's checkbox is on — visible annotations must never intercept Default's click-to-play/
  drag-to-pan or Ruler's drag-to-measure, and must not even change the cursor under any tool but
  Comment (confirmed: no hover affordance at all elsewhere — pure decoration). `pointer-events`
  is only re-enabled on `comment`-source elements while the Comment tool itself is the active
  tool (§4.5); while active, hovering an existing comment annotation shows `cursor: pointer`,
  the same affordance used for any other clickable element in the app, signaling it can be
  clicked to edit.
- While the Comment tool is active, `comment`-source annotations are force-visible regardless of
  the Comments checkbox — you can't author a comment you can't see. The checkbox's stored value
  is untouched by this (it still governs display under every other tool); switching away from
  the Comment tool reverts to whatever the checkbox says. Same override shape as View Lock's
  clamp being suppressed while a Select-tool selection exists.

### 4.4 Drawing

- Only while the Comment tool is active: a drag on the spectrogram/oscillogram draws a live
  rectangle (mirrors the Ruler tool's `updateRulerBox` interaction); a plain click (no drag)
  drops a point at the exact click location — both a time value and a frequency value are
  captured from the click, i.e. `time_start_s = time_end_s = <clicked time>`,
  `freq_min_hz = freq_max_hz = <clicked frequency>`.
- On drag-release or click, the popup (§4.6) opens anchored to the new shape, in "create" mode
  (no Delete button, nothing persisted yet). Cancel discards the shape with no server call; Save
  calls `create_comment_annotation` via the JSON API (§5) and replaces the placeholder with the
  persisted annotation's rendering.
- Only the Comment tool can click an existing `comment` annotation to reopen it in "edit" mode
  (Delete shown). No other tool makes annotations clickable at all (§4.3's `pointer-events`
  rule) — confirmed scope, not deferred.

### 4.5 Popup

Mockup (approved), modeled on the existing `ruler-readout`/`crosshair-readout` floating-readout
pattern (`position: fixed`, flips above/below the anchor when there's no room below):

```
┌───────────────────────────────────────┐
│ [species search/typeahead          ▾] │
│ ┌─────────────────────────────────┐   │
│ │ comment text...                  │   │
│ └─────────────────────────────────┘   │
│           [Delete]  [Cancel]  [Save]  │
└───────────────────────────────────────┘
```

- Species field: a single-select subset of the Classify box's existing taxon search/typeahead
  (`classifier_box.js`'s search index) — no multi-species chip UI, that's the Classify box's own
  concern. Optional.
- Text field: free-form. Species and text are each individually optional, but **at least one of
  the two must be set** to Save — an annotation with neither is pointless data (a shape marking
  nothing in particular). Save is disabled until that's satisfied; `create_comment_annotation`
  (§3) enforces the same rule server-side (raises rather than trusting the client). This matches
  §2's claim that `literal` is "always non-null for a comment row in practice" — read that as
  "unless a species was set instead," not "always."
- Delete only rendered in edit mode (an existing annotation being reopened), never in create
  mode.
- **Post-implementation UX check, required before this task is considered done:** once built,
  actually use the popup (draw a box, add species + text, edit an existing one) and judge
  whether the anchored-floating-popup interaction feels comfortable, not just functional. If it
  doesn't — too easy to misclick outside and lose the draft, too cramped, fights with the
  spectrogram's own scroll/zoom — switch it to an actual modal/dialog overlay (centered,
  `<dialog>` or an app.css modal pattern) instead of the anchored popup. Not "like the Classify
  box": that box is an inline panel in the page's own flow (below the meta line, above the
  toolbar), not a modal at all — if a modal is what this needs, it's a new pattern for this
  page, not a reuse of an existing one.

## 5. API

JSON endpoints under the recording-detail blueprint, not htmx/full-page-reload like the Classify
box (`POST /recordings/<audio_hash>/manual-classification`) — a live-drawing tool losing scroll
position/zoom/active-tool state on every save would be disruptive, so the client does an in-place
DOM update from the JSON response instead:

- `GET /recordings/<audio_hash>/annotations` — list, for the initial page load and Layers-menu
  population. (Could also be embedded server-side in the initial page render like other
  recording data; left as an explicit decision for the implementation plan, not fixed here —
  either is compatible with this spec's data model and CRUD service.)
- `POST /recordings/<audio_hash>/annotations` — create a comment annotation.
- `PATCH /recordings/<audio_hash>/annotations/<id>` — update a comment annotation's text/species.
- `DELETE /recordings/<audio_hash>/annotations/<id>` — delete a comment annotation.

All four call straight into `services/annotations.py`; a request naming a non-`comment` `id`
(once other sources exist) is rejected (404 or 403), since this API is comment-only by design.

## 6. Testing

- `db`-marked tests: the CHECK constraint (an all-null-bounds insert raises `IntegrityError`),
  each `services/annotations.py` function, and the migration drift test
  (`tests/test_migrations.py`).
- JS unit tests (`node --test tests/js/`) for any pure geometry/positioning logic split out of
  `recording_detail.js` into its own DOM-independent module, following the project's existing
  `marker_colors.js`/`classifier_logic.js` extraction pattern.
- Mandatory headless-Chrome live verification (this project's standard for any JS change) of:
  drawing a rectangle and a point, editing and deleting an existing comment, the Layers checkbox
  toggling visibility and persisting across a reload, the Comment tool's force-visibility
  override, and confirming annotations do not intercept clicks/drags under the Default and Ruler
  tools.

## 7. Non-goals

- **Batdetect2 per-call box production.** The current `ClassifierDetection` table has no
  time/frequency columns; teaching batdetect2 to emit per-call boxes into the `annotation` table
  is a separate future spec. `AnnotationSource.BATDETECT2`/`BATTYBIRDNET` are reserved here so
  that spec doesn't need a migration to add the enum member, but nothing in this spec writes
  those rows.
- **Call-distance graph.** Explicitly depends on batdetect2 per-call boxes existing first (per
  the Obsidian note); out of scope until that producer exists.
- **The narrower shape modes** (frequency-band-only, time-range-only, point-in-time/full-height
  marker) from the Obsidian sketch's six listed modes. The schema (independent nullable bound
  columns) already supports them without a migration; adding the draw gestures for them is a
  later, purely-UI follow-up.
- **Drawer panel (`_recording_panel.html`) display or authoring.** The drawer's
  spectrogram/oscillogram are static overview images with no toolbar/tool-switching at all today
  (Ruler/Select/View Lock are already detail-page-only) — not a parity gap under the project's
  drawer/detail-page rule, just the toolbar's existing scope boundary.
- **A custom minimap-style scrollbar** showing where annotations sit along the recording
  (Obsidian's "Future (maybe)" idea under `#Annotations`) — unrelated to this spec's data
  model/CRUD scope, left as a future idea.
- **Editing an annotation's geometry** (resizing/moving an existing box) — delete-and-recreate
  is the only supported "edit the shape" path in this spec.
- **A DB-level guard restricting comment-only writes** — application-layer convention only, per
  §3.
