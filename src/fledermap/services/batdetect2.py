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

    already_processed = (
        select(ClassifierDetection.id)
        .where(
            ClassifierDetection.recording_id == Recording.id,
            ClassifierDetection.source == IdSource.BATDETECT2,
        )
        .exists()
    )
    hashes = session.scalars(
        select(Recording.audio_hash).where(
            Recording.missing_since.is_(None),
            ~already_processed,
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
