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

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

from sqlalchemy import CursorResult, delete, select
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import AnnotationSource, IdSource, Verdict, sentinel_verdict
from fledermap.services.identifications import ClaimInput, replace_claims
from fledermap.store.models import (
    Annotation,
    ClassifierDetection,
    Identification,
    Recording,
)
from fledermap.store.seed import resolve_code

# Stored when a classifier ran and found nothing above its floor. `sentinel_verdict` reads it as
# NO_ID, the same "found nothing" label the EMT and both classifiers already use.
NO_DETECTION_LABEL = "NoID"


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

    An empty `detections` is stored as ONE explicit NoID marker row (`NO_DETECTION_LABEL`,
    no taxon, confidence 0.0), never as zero rows. A finished run that found nothing must stay
    distinguishable from a recording the model has not run on yet -- `enqueue_batdetect2`/
    `enqueue_battybirdnet` select recordings with no row for the source, so zero rows made every
    such recording look unclassified and re-queued it on every ingest cycle, forever. The marker
    is confidence 0.0 so it never clears a threshold as a detection; `materialize_claims` turns it
    (or, at threshold 0, its sentinel label) into the same NO_ID claim as any other recording
    whose detections all fall below the threshold.

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
    "never ran through this classifier at all". A run that found nothing at all is recorded as a
    NoID marker row by `record_detections`, so it takes this same path.

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

    if not recording_ids:
        return MaterializeReport(recordings_updated=0)

    # Batched instead of one SELECT + one GET per recording (was 2 queries x
    # every recording this source has ever touched, every cycle -- see
    # docs/superpowers/specs/2026-09-16-fledermap-worker-scaling-design.md
    # §3.5). The recompute-every-cycle behavior above is unchanged, only how
    # the starting state is READ.
    recordings_by_id = {
        recording.id: recording
        for recording in session.scalars(
            select(Recording).where(Recording.id.in_(recording_ids)),
        )
    }
    detections_by_recording: dict[int, list[ClassifierDetection]] = defaultdict(list)
    for detection in session.scalars(
        select(ClassifierDetection).where(
            ClassifierDetection.source == source,
            ClassifierDetection.recording_id.in_(recording_ids),
        ),
    ):
        detections_by_recording[detection.recording_id].append(detection)

    now = datetime.now(UTC)
    updated = 0
    for recording_id in recording_ids:
        recording = recordings_by_id[recording_id]  # FK guarantees this exists
        detections = detections_by_recording.get(recording_id, [])
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
            sentinel = (
                sentinel_verdict(d.raw_label) if d.raw_label is not None else None
            )
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
