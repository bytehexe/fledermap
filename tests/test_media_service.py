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
from fledermap.services.media import (
    backfill_media,
    clean_media,
    enqueue_media,
    resolve_recording,
    resolve_wav_path,
)
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
    `resolve_recording` would raise `FileNotFoundError` on every attempt.
    Enqueueing it only burns retries."""
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


def test_clean_media_removes_a_directory_for_a_hash_not_in_the_recording_table(
    engine: Engine,
    tmp_path: Path,
) -> None:
    media_root = tmp_path / "media"
    orphan_hash = "o1" * 32
    orphan_path = spectrogram_path(media_root, orphan_hash)
    orphan_path.parent.mkdir(parents=True, exist_ok=True)
    orphan_path.write_bytes(b"orphan")

    with OrmSession(engine) as session:
        result = clean_media(session, media_root)

    assert result.dirs_removed == 1
    assert not orphan_path.parent.exists()


def test_clean_media_removes_a_stale_render_but_keeps_the_current_one(
    engine: Engine,
    tmp_path: Path,
) -> None:
    media_root = tmp_path / "media"
    with OrmSession(engine) as session:
        recording = _make_recording(session, audio_hash="s1" * 32, path="s.wav")
        session.commit()
        audio_hash = recording.audio_hash

    current_path = spectrogram_path(media_root, audio_hash)
    current_path.parent.mkdir(parents=True, exist_ok=True)
    current_path.write_bytes(b"current")
    stale_path = current_path.parent / "spectrogram-oldparamshash.webp"
    stale_path.write_bytes(b"stale")

    with OrmSession(engine) as session:
        result = clean_media(session, media_root)

    assert result.files_removed == 1
    assert not stale_path.exists()
    assert current_path.exists()


def test_clean_media_leaves_a_flagged_missing_recordings_media_alone(
    engine: Engine,
    tmp_path: Path,
) -> None:
    # A missing_since recording still has a real `recording` row -- its
    # existing media isn't orphaned, just not being re-rendered (the file
    # might come back).
    media_root = tmp_path / "media"
    with OrmSession(engine) as session:
        recording = _make_recording(session, audio_hash="m1" * 32, path="m.wav")
        recording.missing_since = datetime(2026, 8, 25, tzinfo=UTC)
        session.commit()
        audio_hash = recording.audio_hash

    current_path = spectrogram_path(media_root, audio_hash)
    current_path.parent.mkdir(parents=True, exist_ok=True)
    current_path.write_bytes(b"current")

    with OrmSession(engine) as session:
        result = clean_media(session, media_root)

    assert result.dirs_removed == 0
    assert result.files_removed == 0
    assert current_path.exists()


def test_clean_media_prunes_an_empty_shard_directory(
    engine: Engine,
    tmp_path: Path,
) -> None:
    media_root = tmp_path / "media"
    orphan_hash = "o2" * 32
    orphan_path = spectrogram_path(media_root, orphan_hash)
    orphan_path.parent.mkdir(parents=True, exist_ok=True)
    orphan_path.write_bytes(b"orphan")
    shard_dir = orphan_path.parent.parent

    with OrmSession(engine) as session:
        clean_media(session, media_root)

    assert not shard_dir.exists()


def test_clean_media_reports_bytes_freed(
    engine: Engine,
    tmp_path: Path,
) -> None:
    media_root = tmp_path / "media"
    orphan_hash = "o3" * 32
    orphan_path = spectrogram_path(media_root, orphan_hash)
    orphan_path.parent.mkdir(parents=True, exist_ok=True)
    orphan_path.write_bytes(b"12345")

    with OrmSession(engine) as session:
        result = clean_media(session, media_root)

    assert result.bytes_freed == 5


def test_clean_media_on_a_nonexistent_media_root_removes_nothing(
    engine: Engine,
    tmp_path: Path,
) -> None:
    media_root = tmp_path / "does-not-exist"

    with OrmSession(engine) as session:
        result = clean_media(session, media_root)

    assert result == (0, 0, 0)


def test_resolve_wav_path_raises_filenotfounderror_for_out_of_range_index() -> None:
    """A root list shrunk after some recordings were tagged with a
    since-removed index must fail clearly, the same way `resolve_recording`
    already does for a missing source file (design spec section 3)."""
    recording = Recording(
        audio_hash="h6" * 32,
        path="a.wav",
        recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
        archive_root_index=2,
    )

    with pytest.raises(FileNotFoundError) as excinfo:
        resolve_wav_path((Path("/one"), Path("/two")), recording)

    message = str(excinfo.value)
    assert "archive_root_index 2" in message
    assert "only 2 root" in message


def test_resolve_recording_raises_filenotfounderror_when_missing_since_is_set(
    engine: Engine,
) -> None:
    with OrmSession(engine) as session:
        recording = _make_recording(session, audio_hash="h7" * 32, path="c.wav")
        recording.missing_since = datetime(2026, 8, 25, tzinfo=UTC)
        session.commit()

        with pytest.raises(FileNotFoundError, match="h7" * 32):
            resolve_recording(session, "h7" * 32)
