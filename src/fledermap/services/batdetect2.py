"""Raw batdetect2 (and, later, any other open-vocabulary classifier) output
storage, and materializing it into `Identification` claims. See
docs/superpowers/specs/2026-09-15-fledermap-batdetect2-classifier-design.md §2.

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

import procrastinate
from sqlalchemy import CursorResult, delete, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import IdSource, Verdict
from fledermap.services.identifications import ClaimInput, replace_claims
from fledermap.store.models import ClassifierDetection, Identification, Recording


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

    A detection that clears `threshold` but whose label has no taxon mapping
    still becomes a SPECIES claim, with `taxon_id` None and `raw_label` set --
    NOT a NO_ID, and not dropped. An unmapped label is a review-queue item by
    design (design spec §3, CLAUDE.md "Species codes"), and it is the same
    shape `services/ingest.py`'s `_apply_identifications` produces for an
    unmapped EMT code, so the existing "unmapped species" UI and
    `reresolve_unmapped_identifications` pick it up for free once a mapping is
    added. Dropping it instead would be unrecoverable here: `NO_ID` is a
    confident-looking answer, and `enqueue_batdetect2` never re-runs the model
    for a recording that already has detection rows.
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
        # One claim per distinct taxon_id, keeping the highest-confidence
        # detection's label for each. `replace_claims` keys a claim's identity
        # on taxon_id alone, and `uq_identification_source_claim` (with
        # `postgresql_nulls_not_distinct`) allows only one row per
        # (recording, source, taxon_id) -- including one single NULL-taxon row
        # -- so several detections sharing a taxon_id MUST collapse to one
        # claim. Collapsing by highest confidence makes which label survives
        # deterministic instead of dependent on row order; a real model run
        # emits the same class dozens of times per file, and several distinct
        # unmapped labels can compete for the one NULL-taxon slot.
        best_by_taxon: dict[int | None, ClassifierDetection] = {}
        for d in passing:
            previous = best_by_taxon.get(d.taxon_id)
            if previous is None or d.confidence > previous.confidence:
                best_by_taxon[d.taxon_id] = d
        desired = (
            [
                ClaimInput(
                    taxon_id=d.taxon_id,
                    verdict=Verdict.SPECIES,
                    raw_label=d.raw_label,
                )
                for d in best_by_taxon.values()
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

    Used when the classifier is disabled after having run (spec §5): both
    the raw cache and the materialized claims from a disabled source must
    disappear, or a stale claim would keep winning `current_best.py`'s
    precedence ordering forever. Idempotent -- called every ingest cycle
    while disabled, so it must be a no-op on rows that don't exist.
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


def enqueue_batdetect2(session: OrmSession, engine: Engine) -> int:
    """Enqueue `classify_batdetect2_task` for every recording lacking a
    `classifier_detection` row for `IdSource.BATDETECT2` yet. Re-run every
    ingest cycle while the classifier is enabled (spec §4/§5) -- this is
    what makes enabling the classifier self-backfilling with no separate
    one-shot "just enabled" transition to detect: the very next cycle after
    flipping the config flag finds every existing recording still missing a
    row and enqueues it, same as any recording ingested afterward.

    Returns the number of recordings still awaiting a first classification --
    which is not the number of jobs actually created, since one already
    pending from an earlier cycle is deduplicated by `queueing_lock` below.
    """
    # Local import, and it must stay local: `jobs.tasks` imports
    # `DetectionInput` from this module at ITS top level, so a top-level
    # import back the other way is a genuine cycle. Enqueueing services and
    # `jobs.tasks` are mutually dependent in general (the task needs the
    # service's types, the service needs the task to defer it); one of the two
    # directions always has to be deferred to call time, and here it is this
    # one.
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
    # `queueing_lock` alongside `lock`, and swallowing AlreadyEnqueued, is the
    # same pattern `services/media.py`'s `enqueue_media` uses, and it is load-
    # bearing here: `lock` only serializes EXECUTION. Without `queueing_lock`,
    # every 5-minute cycle would defer a fresh duplicate job for every
    # recording still waiting on its first classification -- which during a
    # backfill is all of them -- and each duplicate is another full torch
    # forward pass over a file already being classified.
    for audio_hash in hashes:
        try:
            classify_batdetect2_task.configure(
                lock=batdetect2_lock_key(audio_hash),
                queueing_lock=batdetect2_lock_key(audio_hash),
            ).defer(audio_hash=audio_hash)
        except procrastinate.exceptions.AlreadyEnqueued:
            pass
    return len(hashes)
