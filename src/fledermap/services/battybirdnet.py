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
