from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session as OrmSession

from fledermap.jobs.app import ensure_schema
from fledermap.jobs.tasks import app as jobs_app
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
    # Scoped to `audio_hash`'s own lock, not a bare table-wide count: the
    # `engine` fixture resets the ORM (`Base.metadata`) tables per test but
    # NOT the Procrastinate schema, which `ensure_schema` applies once and
    # leaves in place for the rest of the session -- and nothing here ever
    # runs a worker to drain a job out of `todo`. A table-wide count would
    # therefore accumulate every prior test's still-`todo` rows in this same
    # file, making whichever of the two count-based tests below runs second
    # (pytest-randomly order is not fixed) see the other's leftover rows and
    # fail regardless of its own correctness. Filtering by this test's own
    # `lock` isolates the assertion from that shared, session-lived state.
    with engine.connect() as conn:
        count = conn.execute(
            text(
                "SELECT count(*) FROM procrastinate_jobs "
                "WHERE status = 'todo' AND lock LIKE :pattern",
            ),
            {"pattern": f"%{audio_hash}%"},
        ).scalar()
    return int(count) if count is not None else 0


def test_enqueue_media_defers_two_jobs_per_hash(engine: Engine) -> None:
    jobs_app.open(engine)
    ensure_schema(jobs_app, engine)

    enqueue_media(["h1" * 32], engine)

    assert (
        _todo_job_count(engine, "h1" * 32) == 2
    )  # one spectrogram job, one preview job


def test_enqueue_media_ignores_a_duplicate_for_the_same_hash(engine: Engine) -> None:
    jobs_app.open(engine)
    ensure_schema(jobs_app, engine)

    enqueue_media(["h2" * 32], engine)
    enqueue_media(["h2" * 32], engine)  # must not raise, must not double the queue

    assert (
        _todo_job_count(engine, "h2" * 32) == 2
    )  # still just one spectrogram + one preview job


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

        from fledermap.media.spectrogram import SpectrogramParams

        params_hash = SpectrogramParams().params_hash
        existing_dir = media_root / recording.audio_hash[:2] / recording.audio_hash
        existing_dir.mkdir(parents=True)
        (existing_dir / f"spectrogram-{params_hash}.webp").write_bytes(b"x")
        (existing_dir / "preview-v1.opus").write_bytes(b"x")

        count = backfill_media(session, media_root)

    assert count == 0
