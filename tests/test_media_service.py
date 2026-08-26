from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session as OrmSession

from fledermap.jobs.app import ensure_schema
from fledermap.jobs.tasks import app as jobs_app
from fledermap.media.paths import oscillogram_path, preview_path, spectrogram_path
from fledermap.services.media import backfill_media, enqueue_media
from fledermap.store.models import Recording

pytestmark = pytest.mark.db


def _make_recording(session: OrmSession, *, audio_hash: str, path: str) -> Recording:
    r = Recording(
        audio_hash=audio_hash,
        path=path,
        recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
    )
    session.add(r)
    session.flush()
    return r


def _todo_job_count(engine: Engine, audio_hash: str) -> int:
    # Scoped to `audio_hash`'s own lock rather than a bare table-wide count,
    # so a test that enqueues several hashes can still assert about one of
    # them. (The `engine` fixture empties `procrastinate_jobs` per test, so
    # this is precision, not isolation -- isolation lives in conftest.)
    with engine.connect() as conn:
        count = conn.execute(
            text(
                "SELECT count(*) FROM procrastinate_jobs "
                "WHERE status = 'todo' AND lock LIKE :pattern",
            ),
            {"pattern": f"%{audio_hash}%"},
        ).scalar()
    return int(count) if count is not None else 0


def test_enqueue_media_defers_three_jobs_per_hash(engine: Engine) -> None:
    jobs_app.open(engine)
    ensure_schema(jobs_app, engine)

    enqueue_media(["h1" * 32], engine)

    assert (
        _todo_job_count(engine, "h1" * 32) == 3
    )  # one spectrogram job, one oscillogram job, one preview job


def test_enqueue_media_ignores_a_duplicate_for_the_same_hash(engine: Engine) -> None:
    jobs_app.open(engine)
    ensure_schema(jobs_app, engine)

    enqueue_media(["h2" * 32], engine)
    enqueue_media(["h2" * 32], engine)  # must not raise, must not double the queue

    assert (
        _todo_job_count(engine, "h2" * 32) == 3
    )  # still just one spectrogram + one oscillogram + one preview job


def test_backfill_media_enqueues_recordings_with_no_media_on_disk(
    engine: Engine,
    tmp_path: Path,
) -> None:
    jobs_app.open(engine)
    ensure_schema(jobs_app, engine)
    media_root = tmp_path / "media"

    with OrmSession(engine) as session:
        _make_recording(session, audio_hash="h3" * 32, path="a.wav")
        session.commit()

        count = backfill_media(session, media_root)

    assert count == 1


def test_backfill_media_skips_a_recording_with_existing_media(
    engine: Engine,
    tmp_path: Path,
) -> None:
    jobs_app.open(engine)
    ensure_schema(jobs_app, engine)
    media_root = tmp_path / "media"

    with OrmSession(engine) as session:
        recording = _make_recording(session, audio_hash="h4" * 32, path="b.wav")
        session.commit()

        # Built through the SAME helpers the tasks write through, not
        # hand-assembled: a hand-built path here would drift in lockstep with
        # whichever formula it was copied from and hide exactly the
        # divergence this test exists to catch.
        for path in (
            spectrogram_path(media_root, recording.audio_hash),
            oscillogram_path(media_root, recording.audio_hash),
            preview_path(media_root, recording.audio_hash),
        ):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"x")

        count = backfill_media(session, media_root)

    assert count == 0


def test_backfill_media_skips_a_recording_flagged_missing(
    engine: Engine,
    tmp_path: Path,
) -> None:
    """A recording with `missing_since` set has no source file, so
    `jobs.tasks._resolve_recording` would raise `FileNotFoundError` on every
    attempt. Enqueueing it only burns retries."""
    jobs_app.open(engine)
    ensure_schema(jobs_app, engine)
    media_root = tmp_path / "media"

    present_hash = "h5" * 32
    gone_hash = "h6" * 32
    with OrmSession(engine) as session:
        _make_recording(session, audio_hash=present_hash, path="present.wav")
        gone = _make_recording(session, audio_hash=gone_hash, path="gone.wav")
        gone.missing_since = datetime(2026, 8, 25, tzinfo=UTC)
        session.commit()

        count = backfill_media(session, media_root)

    # Only the present recording; nothing deferred for the missing one.
    assert count == 1
    assert _todo_job_count(engine, present_hash) == 3
    assert _todo_job_count(engine, gone_hash) == 0
