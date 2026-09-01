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
    oscillogram_lock_key,
    preview_lock_key,
    render_oscillogram_task,
    render_spectrogram_task,
    spectrogram_lock_key,
)
from fledermap.media.paths import oscillogram_path, preview_path, spectrogram_path
from fledermap.store.models import Recording


def resolve_recording(session: OrmSession, audio_hash: str) -> Recording:
    """Moved here from `jobs/tasks.py` -- a second legitimate consumer (the
    recording-details page's serving routes, `web/views/media.py`) is what
    promotes a private helper to a shared, public one (design spec
    Decisions)."""
    recording = session.scalars(
        select(Recording).where(Recording.audio_hash == audio_hash),
    ).one()
    if recording.missing_since is not None:
        msg = f"recording {audio_hash} has no source file (missing_since set)"
        raise FileNotFoundError(msg)
    return recording


def resolve_wav_path(archive_roots: tuple[Path, ...], recording: Recording) -> Path:
    """`archive_root_index` out of range means a root list shrank after some
    recordings were tagged with a since-removed index -- fail clearly the
    same way `resolve_recording` does above, rather than a bare
    `IndexError`."""
    try:
        root = archive_roots[recording.archive_root_index]
    except IndexError as exc:
        msg = (
            f"recording {recording.audio_hash} references archive_root_index "
            f"{recording.archive_root_index}, but only {len(archive_roots)} "
            f"root(s) are configured"
        )
        raise FileNotFoundError(msg) from exc
    return root / recording.path


def enqueue_media(created_hashes: list[str], engine: Engine) -> None:
    """Defer all three tasks for each hash, locked/queueing-locked per design spec
    §7. Called from `cli/main.py`'s `ingest` command AFTER `session.commit()`
    succeeds -- not from inside `commit_scan`, which does not commit, so
    nothing can be picked up by a worker for a row that isn't durably
    committed yet. Opens `jobs_app` against `engine` itself -- callers do NOT
    need to pre-open it -- since both `backfill_media` and the CLI `ingest`
    command call this, and each would otherwise have to duplicate that
    step.

    `jobs.tasks.run_ingest_cycle` is now a THIRD caller, and it runs from
    *inside* an already-running Procrastinate worker -- `jobs_app`'s
    connector at that point is whatever async connector `replace_connector`
    swapped in for the worker (see `jobs/app.py`), already opened via
    `open_async()` by the worker machinery itself. Calling the sync
    `App.open()` again in that state doesn't no-op: `BaseAsyncConnector`
    doesn't override sync `open()`, so it always raises `NotImplementedError`
    (confirmed directly against a real worker run) regardless of whether the
    connector already has a live async pool. That's a signal to catch, not an
    error to propagate -- it means a connector is already active and there is
    nothing left for this call to do.
    """
    try:
        jobs_app.open(engine)
    except NotImplementedError:
        pass
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
        try:
            render_oscillogram_task.configure(
                lock=oscillogram_lock_key(audio_hash),
                queueing_lock=oscillogram_lock_key(audio_hash),
            ).defer(audio_hash=audio_hash)
        except procrastinate.exceptions.AlreadyEnqueued:
            pass


def _has_media(media_root: Path, audio_hash: str) -> bool:
    """Disk existence, not a Procrastinate job-history query (design spec
    §8, decision P3-6): the job table isn't a reliable durable record
    (Procrastinate can be configured to delete completed jobs), and disk
    state is what actually determines whether a recording needs work.

    Both paths come from `media.paths`, the same helpers the tasks write
    through -- this check is only meaningful while it agrees with them
    exactly."""
    return (
        spectrogram_path(media_root, audio_hash).exists()
        and oscillogram_path(media_root, audio_hash).exists()
        and preview_path(media_root, audio_hash).exists()
    )


def backfill_media(db_session: OrmSession, media_root: Path) -> int:
    """Enqueue media for every recording that doesn't already have all three
    files on disk at the current params. Returns the count enqueued.

    Recordings flagged missing are excluded: `resolve_recording` above
    raises `FileNotFoundError` for anything with `missing_since` set, so
    without this filter a `sweep_missing` that flags N recordings would make
    the next backfill defer 3N jobs guaranteed to fail every retry."""
    engine = db_session.get_bind()
    assert isinstance(engine, Engine), "db_session must be bound to an Engine"
    hashes = db_session.scalars(
        select(Recording.audio_hash).where(Recording.missing_since.is_(None)),
    ).all()
    missing = [h for h in hashes if not _has_media(media_root, h)]
    enqueue_media(missing, engine)
    return len(missing)
