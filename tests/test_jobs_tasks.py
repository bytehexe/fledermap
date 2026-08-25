from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session as OrmSession

from fledermap.jobs.app import ensure_schema, make_worker_connector
from fledermap.jobs.tasks import (
    app as jobs_app,
)
from fledermap.jobs.tasks import (
    make_preview_task,
    preview_lock_key,
    render_spectrogram_task,
    spectrogram_lock_key,
)
from fledermap.store.models import Recording
from tests.fixtures import build_wav, fmt_payload

pytestmark = pytest.mark.db


def _make_recording(
    session: OrmSession,
    *,
    audio_hash: str,
    path: str,
) -> Recording:
    r = Recording(
        audio_hash=audio_hash,
        path=path,
        recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
    )
    session.add(r)
    session.flush()
    return r


def _write_wav(root: Path, rel: str) -> None:
    full = root / rel
    full.parent.mkdir(parents=True, exist_ok=True)
    audio = bytes(range(256)) * 8  # real, non-trivial PCM content
    full.write_bytes(build_wav([(b"fmt ", fmt_payload()), (b"data", audio)]))


def _run_worker(engine: Engine, **kwargs: object) -> None:
    """`run_worker` needs an async-capable connector: `App.run_worker` always
    calls `open_async()` internally, and `SQLAlchemyPsycopg2Connector` (bound
    via `jobs_app.open(engine)` for defer-side use) has no `open_async`
    override, so the base connector's `open_async` raises
    `SyncConnectorConfigurationError` -- confirmed by direct execution against
    a real Postgres container, and exactly what `fledermap.jobs.app`'s module
    docstring already documents. Swap in the async `PsycopgConnector` for the
    duration of the
    worker run only, via `replace_connector` as a context manager, per that
    module's documented pattern -- `engine`'s URL is reduced to a bare
    `postgresql://` DSN (stripping the `+psycopg2` driver suffix SQLAlchemy's
    engine carries) since `make_worker_connector`/`PsycopgConnector` expect
    that form.
    """
    database_url = engine.url.set(drivername="postgresql").render_as_string(
        hide_password=False,
    )
    with jobs_app.replace_connector(make_worker_connector(database_url)) as worker_app:
        worker_app.run_worker(**kwargs)


def test_render_spectrogram_task_writes_a_file(
    engine: Engine,
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archive"
    media_root = tmp_path / "media"
    _write_wav(archive_root, "a.wav")
    jobs_app.open(engine)
    ensure_schema(jobs_app, engine)

    with OrmSession(engine) as session:
        recording = _make_recording(session, audio_hash="h1" * 32, path="a.wav")
        session.commit()
        audio_hash = recording.audio_hash

    render_spectrogram_task.configure(
        lock=spectrogram_lock_key(audio_hash),
        queueing_lock=spectrogram_lock_key(audio_hash),
    ).defer(audio_hash=audio_hash)
    _run_worker(
        engine,
        wait=False,
        install_signal_handlers=False,
        listen_notify=False,
        additional_context={
            "archive_root": archive_root,
            "media_root": media_root,
            "engine": engine,
        },
    )

    produced = list(
        media_root.glob(f"{audio_hash[:2]}/{audio_hash}/spectrogram-*.webp")
    )
    assert len(produced) == 1


def test_make_preview_task_writes_a_file(engine: Engine, tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    media_root = tmp_path / "media"
    _write_wav(archive_root, "b.wav")
    jobs_app.open(engine)
    ensure_schema(jobs_app, engine)

    with OrmSession(engine) as session:
        recording = _make_recording(session, audio_hash="h2" * 32, path="b.wav")
        session.commit()
        audio_hash = recording.audio_hash

    make_preview_task.configure(
        lock=preview_lock_key(audio_hash),
        queueing_lock=preview_lock_key(audio_hash),
    ).defer(audio_hash=audio_hash)
    _run_worker(
        engine,
        wait=False,
        install_signal_handlers=False,
        listen_notify=False,
        additional_context={
            "archive_root": archive_root,
            "media_root": media_root,
            "engine": engine,
        },
    )

    produced = list(media_root.glob(f"{audio_hash[:2]}/{audio_hash}/preview-*.opus"))
    assert len(produced) == 1


def _defer_doomed_spectrogram_job(engine: Engine, audio_hash: str) -> int:
    """A recording flagged missing, with no file on disk, deferred for
    rendering -- guaranteed to raise `FileNotFoundError` on every attempt."""
    jobs_app.open(engine)
    ensure_schema(jobs_app, engine)

    with OrmSession(engine) as session:
        recording = _make_recording(
            session,
            audio_hash=audio_hash,
            path="never_written.wav",
        )
        recording.missing_since = datetime(2026, 8, 25, tzinfo=UTC)
        session.commit()

    return render_spectrogram_task.configure(
        lock=spectrogram_lock_key(audio_hash),
        queueing_lock=spectrogram_lock_key(audio_hash),
    ).defer(audio_hash=audio_hash)


def _drain_once(engine: Engine, tmp_path: Path) -> None:
    """One `wait=False` worker pass: process whatever is due right now, then
    exit. A job scheduled into the future by the retry backoff is NOT due,
    so it is left untouched."""
    _run_worker(
        engine,
        wait=False,
        install_signal_handlers=False,
        listen_notify=False,
        additional_context={
            "archive_root": tmp_path / "archive",
            "media_root": tmp_path / "media",
            "engine": engine,
        },
    )


def _job_row(engine: Engine, job_id: int) -> tuple[str, int, bool]:
    """(status, attempts, is_scheduled_in_the_future) for one job."""
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT status, attempts, "
                "coalesce(scheduled_at > now(), false) AS deferred "
                "FROM procrastinate_jobs WHERE id = :job_id",
            ),
            {"job_id": job_id},
        ).one()
    return str(row.status), int(row.attempts), bool(row.deferred)


def test_task_retry_backs_off_instead_of_firing_immediately(
    engine: Engine,
    tmp_path: Path,
) -> None:
    """Design spec §7 asks for exponential backoff. A bare `retry=3` would
    leave every wait parameter at 0, so every attempt would burn inside this
    single worker pass; with `exponential_wait` set, the first failure
    reschedules the job into the future instead."""
    job_id = _defer_doomed_spectrogram_job(engine, "h3" * 32)

    _drain_once(engine, tmp_path)

    status, attempts, deferred = _job_row(engine, job_id)
    assert status == "todo"  # retrying, not finished
    assert attempts == 1  # exactly one attempt was made, not all three
    assert deferred is True  # and the next one is not due yet


def test_task_fails_permanently_for_a_missing_source_file(
    engine: Engine,
    tmp_path: Path,
) -> None:
    """It still ends `failed`, not retried forever. The backoff is skipped
    over by pulling `scheduled_at` back to now between passes rather than
    sleeping out the real 2s + 4s + 8s, which would put fourteen idle seconds
    into the suite to prove something the test above already proves."""
    job_id = _defer_doomed_spectrogram_job(engine, "h5" * 32)

    for _ in range(6):  # generous bound; the run below needs 4 passes
        _drain_once(engine, tmp_path)
        status, _, _ = _job_row(engine, job_id)
        if status != "todo":
            break
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE procrastinate_jobs SET scheduled_at = now() "
                    "WHERE id = :job_id",
                ),
                {"job_id": job_id},
            )

    status, attempts, _ = _job_row(engine, job_id)
    assert status == "failed"
    # Bounded, not an unbounded retry loop. 4, not 3, because Procrastinate's
    # `max_attempts` counts RETRIES scheduled, not runs performed: `attempts`
    # is incremented by `procrastinate_retry_job` and again by
    # `procrastinate_finish_job`, so a `max_attempts=3` job runs the original
    # plus 3 retries and lands on 4.
    assert attempts == 4


def test_duplicate_defer_with_the_same_queueing_lock_is_refused(
    engine: Engine,
) -> None:
    import procrastinate

    jobs_app.open(engine)
    ensure_schema(jobs_app, engine)
    audio_hash = "h4" * 32

    render_spectrogram_task.configure(
        lock=spectrogram_lock_key(audio_hash),
        queueing_lock=spectrogram_lock_key(audio_hash),
    ).defer(audio_hash=audio_hash)

    with pytest.raises(procrastinate.exceptions.AlreadyEnqueued):
        render_spectrogram_task.configure(
            lock=spectrogram_lock_key(audio_hash),
            queueing_lock=spectrogram_lock_key(audio_hash),
        ).defer(audio_hash=audio_hash)
