"""Enqueueing derived-media jobs. The only place `commit_scan`'s result and
a backfill sweep turn into actual Procrastinate deferrals (design spec §8)."""

from __future__ import annotations

from pathlib import Path

import procrastinate
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session as OrmSession

from fledermap.jobs.tasks import (
    app as jobs_app,
)
from fledermap.jobs.tasks import (
    make_preview_task,
    preview_lock_key,
    render_spectrogram_task,
    spectrogram_lock_key,
)
from fledermap.media.spectrogram import SpectrogramParams
from fledermap.store.models import Recording

_SPECTROGRAM_PARAMS_HASH = SpectrogramParams().params_hash


def enqueue_media(created_hashes: list[str], engine: Engine) -> None:
    """Defer both tasks for each hash, locked/queueing-locked per design spec
    §7. Called from `cli/main.py`'s `ingest` command AFTER `session.commit()`
    succeeds -- not from inside `commit_scan`, which does not commit, so
    nothing can be picked up by a worker for a row that isn't durably
    committed yet. Opens `jobs_app` against `engine` itself -- callers do NOT
    need to pre-open it -- since both `backfill_media` and the CLI `ingest`
    command call this, and each would otherwise have to duplicate that
    step."""
    jobs_app.open(engine)
    for audio_hash in created_hashes:
        try:
            render_spectrogram_task.configure(
                lock=spectrogram_lock_key(audio_hash),
                queueing_lock=spectrogram_lock_key(audio_hash),
            ).defer(audio_hash=audio_hash)
        except procrastinate.exceptions.AlreadyEnqueued:
            pass
        try:
            make_preview_task.configure(
                lock=preview_lock_key(audio_hash),
                queueing_lock=preview_lock_key(audio_hash),
            ).defer(audio_hash=audio_hash)
        except procrastinate.exceptions.AlreadyEnqueued:
            pass


def _has_media(media_root: Path, audio_hash: str) -> bool:
    """Disk existence, not a Procrastinate job-history query (design spec
    §8, decision P3-6): the job table isn't a reliable durable record
    (Procrastinate can be configured to delete completed jobs), and disk
    state is what actually determines whether a recording needs work."""
    recording_dir = media_root / audio_hash[:2] / audio_hash
    spectrogram = recording_dir / f"spectrogram-{_SPECTROGRAM_PARAMS_HASH}.webp"
    preview = recording_dir / "preview-v1.opus"
    return spectrogram.exists() and preview.exists()


def backfill_media(db_session: OrmSession, media_root: Path) -> int:
    """Enqueue media for every recording that doesn't already have both
    files on disk at the current params. Returns the count enqueued."""
    engine = db_session.get_bind()
    assert isinstance(engine, Engine), "db_session must be bound to an Engine"
    hashes = db_session.scalars(select(Recording.audio_hash)).all()
    missing = [h for h in hashes if not _has_media(media_root, h)]
    enqueue_media(missing, engine)
    return len(missing)
