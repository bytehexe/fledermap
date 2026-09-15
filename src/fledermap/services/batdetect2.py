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

from sqlalchemy import CursorResult, delete, select
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import IdSource, Verdict
from fledermap.services.identifications import ClaimInput, replace_claims
from fledermap.store.models import ClassifierDetection, Identification, Recording


@dataclass(frozen=True)
class DetectionInput:
    """One raw candidate the model emitted for a recording, before any
    threshold filtering."""

    taxon_id: int | None
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
        passing = [
            d
            for d in detections
            if d.taxon_id is not None and d.confidence >= threshold
        ]
        desired = (
            [ClaimInput(taxon_id=d.taxon_id, verdict=Verdict.SPECIES) for d in passing]
            if passing
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
