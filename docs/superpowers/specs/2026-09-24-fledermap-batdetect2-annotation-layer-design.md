# Fledermap — batdetect2 per-call annotation layer design

Status: proposed. Scope: teach batdetect2's classification job to also write per-call
`Annotation` rows (`AnnotationSource.BATDETECT2`, reserved by
`docs/superpowers/specs/2026-09-23-fledermap-annotations-design.md` §2 for exactly this), so the
recording-detail page's existing Layers/annotation-overlay mechanism shows batdetect2's detected
calls as boxes, labeled with species + confidence. No schema migration: the `annotation` table's
geometry columns are already nullable, and `AnnotationSource` has no CHECK constraint (spec D9
precedent). Obsidian backlog's "Recording detail page (DAW view): overlays for batdetect2 call
boxes" item is the trigger.

## 1. Problem

batdetect2's own model output is already per-call: `api.process_file`'s
`result["pred_dict"]["annotation"]` is a list of one dict per detected echolocation event, each
carrying `class`/`class_prob`/`det_prob` **and** `start_time`/`end_time`/`low_freq`/`high_freq`
(confirmed against the real package via `tests/test_batdetect2_real_api.py`'s fixture, and against
upstream docs). `jobs/tasks.py`'s `annotations_to_detections` already reads the first three into
`DetectionInput` for the `classifier_detection` cache; the geometry fields are parsed by nothing
and go nowhere.

The annotations spec built the exact shared mechanism this needs — a generic
time/frequency/species/confidence marker with per-source display and a Layers toggle — and
explicitly reserved `AnnotationSource.BATDETECT2` for it, deferring the producer to "a separate
future spec." This is that spec.

BattyBirdNET is explicitly **not** covered: its detections are one per fixed-width analysis
window (§ of `docs/superpowers/specs/2026-09-16-fledermap-battybirdnet-classifier-design.md`), not
one per call, so a window is not a call box. Giving it real per-call boxes needs merging adjacent
same-verdict windows first — a separate piece of work, left as a follow-up. The mechanism built
here is written so that work is the *only* thing still needed: once `results_to_detections`
populates the same geometry fields on `DetectionInput`, BattyBirdNET annotations appear for free.

## 2. Data flow

`DetectionInput` (`services/classifier_detections.py`) gains four new optional fields, all
defaulting to `None`:

| Field | Type | Source |
|---|---|---|
| `time_start_s` | `float \| None` | batdetect2 `start_time` |
| `time_end_s` | `float \| None` | batdetect2 `end_time` |
| `freq_min_hz` | `float \| None` | batdetect2 `low_freq` |
| `freq_max_hz` | `float \| None` | batdetect2 `high_freq` |

`annotations_to_detections` (`jobs/tasks.py`) sets all four from each annotation dict.
`results_to_detections` (BattyBirdNET) leaves them `None` — out of scope here, see §1.

`record_detections` (`services/classifier_detections.py`) is the one function both classifier
tasks already call, in the same job run right after the model produces its output, to cache raw
detections. It gains a second responsibility: alongside its existing full delete-then-insert of
`classifier_detection` rows for `(recording, source)`, it does the equivalent full
delete-then-insert of `Annotation` rows with `source=AnnotationSource(source.value)` (the two
enums share literal string values for `batdetect2`/`battybirdnet`, so this is a direct
construction, not a lookup table), for every `DetectionInput` in the batch that has
`time_start_s is not None` — skipping the synthesized empty-run `NoID` marker row, which has no
geometry and therefore produces no annotation. One write path, one call site per classifier task,
shared by both sources without either needing its own annotation-writing code.

Each written `Annotation` row: `time_start_s`/`time_end_s`/`freq_min_hz`/`freq_max_hz` copied
directly, `taxon_id` copied from the `DetectionInput` (same resolution `classifier_detection`
already went through), `confidence=detection.confidence` (batdetect2's `class_prob`, matching what
`DetectionInput.confidence` already holds today), `literal=None`.

No new endpoint: `GET /recordings/<audio_hash>/annotations` (`services/annotations.py`'s
`list_annotations`, already source-agnostic) and the existing embedded `data-annotations` JSON on
page load already return every source's rows.

## 3. Gating — which detections become boxes

Store every candidate above the existing `_BATDETECT2_FLOOR` (0.05 on `det_prob`, "is there a call
here at all") — the same floor `classifier_detection` already uses, not the configurable species
threshold (`config.batdetect2_threshold`, default 0.8). Showing only calls that clear the identification
threshold would hide real calls the model detected but couldn't confidently name.

Instead, boxes below the species threshold are drawn **visually distinct** (dashed border, see
§5) rather than being hidden or omitted from storage. This is a live judgment call, not settled
science — if the floor-gated view turns out too noisy or confusing in practice, a follow-up may
switch the gate itself to the species threshold, and decide then whether that means storage or
display only. Not decided here; flagged as a likely revisit.

## 4. Serialization and threshold exposure

`_serialize_annotation` (`web/views/recording_detail.py`) currently omits `confidence` entirely
(comment rows never set it). Add it:

```python
"confidence": a.confidence,
```

The recording-detail view also exposes the two configured classifier thresholds to the page, the
same way `COMBINE_EMT_DISPLAY` is exposed today (`app.config[...]` set in `web/app.py`, read in the
view, passed to the template):

```python
app.config["CLASSIFIER_THRESHOLDS"] = {
    "batdetect2": config.batdetect2_threshold,
    "battybirdnet": config.battybirdnet_threshold,
}
```

embedded as a JSON data attribute on `#detail-toolbar`, e.g.
`data-classifier-thresholds='{"batdetect2": 0.8, "battybirdnet": 0.6}'` — generic over source, so
a future BattyBirdNET annotation producer needs no new attribute.

## 5. Frontend (`recording_detail.js`, `app.css`)

- **Label**: species line already goes through `abbreviateScientificName(taxon.scientific_name)`
  for every source (comments included today) — no change needed there. Append confidence when
  present: `"P. pipistrellus 82%"` (`Math.round(confidence * 100)`). Falls back to the raw label
  for an unmapped taxon, same as today.
- **Below-threshold styling**: read `data-classifier-thresholds` once at startup into a plain
  object. In `renderAnnotations`, when `annotation.confidence !== null` and a threshold exists for
  `annotation.source` and `confidence < threshold`, add a `below-threshold` class to the element.
  New CSS:
  ```css
  .annotation-box.below-threshold { border-style: dashed; }
  .annotation-point.below-threshold { border-style: dashed; }
  ```
- **No click/edit handling** for batdetect2 rows — already the default; only `source === "comment"`
  gets mousedown/click listeners today.
- **Layer menu order**: `rebuildLayersMenu` currently sorts sources alphabetically
  (`"batdetect2" < "comment"`), which would put Batdetect2 above Comments. Replace with an
  explicit priority list — `["comment", "batdetect2", "battybirdnet"]`, any other source appended
  alphabetically after — so Comments always leads and Batdetect2 sits directly below it. A source
  still only appears in the menu when the recording actually has rows for it (existing
  `[...new Set(annotations.map(a => a.source))]` logic, unchanged).
- **Paint/hit-test order**: comment annotations must render on top of batdetect2 boxes on overlap.
  Today `annotations`' array order is DB insertion order (`Annotation.id` ascending), which no
  longer implies "comments last" once batdetect2 rows exist for the same recording.
  `renderAnnotations` and `annotationAtPoint` both need comment-source entries sorted after
  batdetect2 in the array they iterate — a stable sort keyed by `source === "comment"` (false
  before true) applied once when `annotations` is (re)loaded, not re-sorted per render call.
  `annotationAtPoint`'s existing end-to-start hit-test then naturally picks the topmost (comment,
  if present) annotation first, consistent with what's painted on top.

## 6. Enable/disable mirroring and production backfill

`purge_source` (`services/classifier_detections.py`) currently deletes `classifier_detection` and
`Identification` rows for a source when it's disabled, every ingest cycle, idempotently. Extend it
to also delete `Annotation` rows for that source:

```python
annotations_removed = cast(
    "CursorResult[tuple[()]]",
    session.execute(
        delete(Annotation).where(Annotation.source == AnnotationSource(source.value))
    ),
).rowcount
```

(added to `PurgeReport`). This makes disabling batdetect2 remove its cache, its claims, **and**
its annotation boxes together — no stale boxes left behind for a classifier the operator turned
off — and re-enabling reprocesses from scratch via `enqueue_batdetect2`'s existing
NOT-EXISTS-on-`classifier_detection` anti-join, regenerating annotations through the now
geometry-aware `record_detections`.

This is also the production backfill mechanism for recordings batdetect2 already classified before
this feature existed (their cached `classifier_detection` rows have no geometry to backfill from —
the raw model dict was never kept). No new CLI command or script: flip
`FLEDERMAP_BATDETECT2_ENABLED` off, let one ingest cycle's `purge_source` run, flip it back on, let
it reprocess every recording. Documented as a deploy step for this change, not built as
application code.

## 7. Testing

- `annotations_to_detections`: assert the four geometry fields are read from `start_time`/
  `end_time`/`low_freq`/`high_freq` and passed through on `DetectionInput`.
- `tests/test_batdetect2_real_api.py`: extend the existing real-output assertions to also check
  the four geometry keys are present on every real annotation (mirrors its existing
  fields-the-parser-reads test) — same "test against the real package, not a mock of it" rationale
  already governing that file.
- `record_detections`: a detection with geometry produces a matching `Annotation` row
  (`source`/`taxon_id`/`confidence`/geometry copied); a detection without geometry
  (BattyBirdNET-shaped, or the synthesized `NoID` marker) produces none; a re-run replaces rows
  wholesale (old boxes gone, new ones present) rather than accumulating.
- `purge_source`: disabling removes `Annotation` rows for the source, leaves other sources' rows
  untouched.
- JS (`node --test tests/js/`): confidence formatting in the label string; `below-threshold` class
  applied/not applied at the boundary; comment-last sort ordering. Layer menu priority order and
  the live dashed-border rendering need the headless-Chrome live-verification pass per
  CLAUDE.md's JavaScript tooling section — this is a change to axis-adjacent overlay rendering the
  same way the existing annotation work was.

## Non-goals

- BattyBirdNET per-call geometry (needs adjacent-window merging first — separate future work; the
  mechanism here already supports it once that lands).
- Switching the gate from the fixed floor to the species threshold (§3) — deliberately left open,
  pending live experience with the floor-gated + dashed-below-threshold display.
- A dedicated backfill CLI command (§6) — the disable/enable cycle already does it.
- Any change to `classifier_detection` itself — it stays geometry-free; `Annotation` is the only
  place per-call boxes live.
