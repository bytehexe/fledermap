"""Enqueueing derived-media jobs. The only place `commit_scan`'s result and
a backfill sweep turn into actual Procrastinate deferrals (design spec §8)."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import NamedTuple

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


class CleanMediaResult(NamedTuple):
    dirs_removed: int
    files_removed: int
    bytes_freed: int


def clean_media(db_session: OrmSession, media_root: Path) -> CleanMediaResult:
    """Prune orphaned derived-media on disk (backlog: "clean-media"). Derived
    media is fully regenerable from the read-only archive (D16), so this is
    always safe -- `backfill_media`/the next ingest cycle re-renders
    whatever a viewer actually needs again.

    Two orphan categories, per the design note this closes:

    - A whole `<hash[:2]>/<hash>/` directory whose hash has NO row in
      `recording` at all (not even one with `missing_since` set -- that
      still has a row, and its media stays, since the file might come
      back). Nothing in this codebase deletes a `Recording` row today, but
      the layout makes no promise that stays true forever.
    - An individual file inside a still-valid recording's directory that
      isn't one of the CURRENT `spectrogram_path`/`oscillogram_path`/
      `preview_path` names -- left behind by an old params/preview-version
      bump (see `media/paths.py`'s `PREVIEW_VERSION` comment: nothing reads
      or deletes those automatically).

    Uses the same three path helpers `_has_media`/the render tasks write
    through, not a hand-built glob, for the same reason `_has_media` does:
    a hand-built pattern would drift out of step with a real bump instead
    of tracking it.
    """
    if not media_root.is_dir():
        return CleanMediaResult(0, 0, 0)

    hashes_in_db = set(db_session.scalars(select(Recording.audio_hash)).all())

    dirs_removed = 0
    files_removed = 0
    bytes_freed = 0

    for shard_dir in media_root.iterdir():
        if not shard_dir.is_dir():
            continue
        for hash_dir in shard_dir.iterdir():
            if not hash_dir.is_dir():
                continue
            audio_hash = hash_dir.name
            if audio_hash not in hashes_in_db:
                bytes_freed += sum(
                    f.stat().st_size for f in hash_dir.rglob("*") if f.is_file()
                )
                shutil.rmtree(hash_dir)
                dirs_removed += 1
                continue

            current_names = {
                spectrogram_path(media_root, audio_hash).name,
                oscillogram_path(media_root, audio_hash).name,
                preview_path(media_root, audio_hash).name,
            }
            for file_path in hash_dir.iterdir():
                if file_path.is_file() and file_path.name not in current_names:
                    bytes_freed += file_path.stat().st_size
                    file_path.unlink()
                    files_removed += 1

        if not any(shard_dir.iterdir()):
            shard_dir.rmdir()

    return CleanMediaResult(dirs_removed, files_removed, bytes_freed)
