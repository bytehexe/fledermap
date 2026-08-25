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
    docstring already documents (Task 4, confirmed against a real Postgres
    container). Swap in the async `PsycopgConnector` for the duration of the
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


def test_task_fails_permanently_for_a_missing_source_file(
    engine: Engine,
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archive"
    media_root = tmp_path / "media"
    # No file written -- recording.path points nowhere.
    jobs_app.open(engine)
    ensure_schema(jobs_app, engine)

    with OrmSession(engine) as session:
        recording = _make_recording(
            session,
            audio_hash="h3" * 32,
            path="never_written.wav",
        )
        recording.missing_since = datetime(2026, 8, 25, tzinfo=UTC)
        session.commit()
        audio_hash = recording.audio_hash

    job_id = render_spectrogram_task.configure(
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

    # `procrastinate_jobs` lives in the same (session-scoped) Postgres
    # container as every other test in this module -- it is NOT recreated by
    # the per-test `engine` fixture (that only drops/creates this project's
    # own ORM tables), so filtering by `job_id` (not `task_name`) is required
    # to avoid picking up another test's row for the same task.
    with engine.connect() as conn:
        status = conn.execute(
            text("SELECT status FROM procrastinate_jobs WHERE id = :job_id"),
            {"job_id": job_id},
        ).scalar()
    assert status == "failed"


def test_duplicate_defer_with_the_same_queueing_lock_is_refused(
    engine: Engine,
) -> None:
    import procrastinate

    jobs_app.open(engine)
    ensure_schema(jobs_app, engine)
    audio_hash = "h4" * 32

    render_spectrogram_task.configure(
        queueing_lock=spectrogram_lock_key(audio_hash),
    ).defer(audio_hash=audio_hash)

    with pytest.raises(procrastinate.exceptions.AlreadyEnqueued):
        render_spectrogram_task.configure(
            queueing_lock=spectrogram_lock_key(audio_hash),
        ).defer(audio_hash=audio_hash)
