from __future__ import annotations

import logging
import os
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from geoalchemy2.elements import WKTElement
from sqlalchemy import func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session as OrmSession

from fledermap.config import Config
from fledermap.jobs.app import ensure_schema, make_worker_connector
from fledermap.jobs.tasks import (
    _INGEST_CYCLE_LOCK,
    _NAME_SITE_LOCK,
    make_preview_task,
    name_site_queueing_lock,
    name_site_task,
    oscillogram_lock_key,
    preview_lock_key,
    render_oscillogram_task,
    render_spectrogram_task,
    run_ingest_cycle,
    spectrogram_lock_key,
)
from fledermap.jobs.tasks import (
    app as jobs_app,
)
from fledermap.services import site_naming
from fledermap.store.models import Recording, Site
from fledermap.store.models import Session as SessionModel
from tests.fixtures import build_wav, fmt_payload, wamd_payload

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

    Now that `run_ingest_cycle` is registered `@app.periodic(...)`,
    Procrastinate's `_start_side_tasks` starts the periodic deferrer on ANY
    worker run against the shared `jobs_app`, regardless of `wait`/`queues` --
    so every call below that only exercises the media tasks scopes itself to
    `queues=["media"]` to keep a stray periodic `run_ingest_cycle` job (which
    would need `additional_context["config"]`, absent here) from executing
    mid-test. This task's own tests deliberately pass `queues=["ingest"]`
    instead.
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
        queues=["media"],
        additional_context={
            "archive_roots": (archive_root,),
            "media_root": media_root,
            "engine": engine,
        },
    )

    produced = list(
        media_root.glob(f"{audio_hash[:2]}/{audio_hash}/spectrogram-*.webp")
    )
    assert len(produced) == 1


def test_render_oscillogram_task_writes_a_file(
    engine: Engine,
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archive"
    media_root = tmp_path / "media"
    _write_wav(archive_root, "o.wav")
    jobs_app.open(engine)
    ensure_schema(jobs_app, engine)

    with OrmSession(engine) as session:
        recording = _make_recording(session, audio_hash="h9" * 32, path="o.wav")
        session.commit()
        audio_hash = recording.audio_hash

    render_oscillogram_task.configure(
        lock=oscillogram_lock_key(audio_hash),
        queueing_lock=oscillogram_lock_key(audio_hash),
    ).defer(audio_hash=audio_hash)
    _run_worker(
        engine,
        wait=False,
        install_signal_handlers=False,
        listen_notify=False,
        queues=["media"],
        additional_context={
            "archive_roots": (archive_root,),
            "media_root": media_root,
            "engine": engine,
        },
    )

    produced = list(
        media_root.glob(f"{audio_hash[:2]}/{audio_hash}/oscillogram-*.webp")
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
        queues=["media"],
        additional_context={
            "archive_roots": (archive_root,),
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
        queues=["media"],
        additional_context={
            "archive_roots": (tmp_path / "archive",),
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


def test_task_fails_permanently_for_an_out_of_range_archive_root_index(
    engine: Engine,
    tmp_path: Path,
) -> None:
    """The same failure propagates all the way through a real task run and
    fails the job, exactly like `test_task_fails_permanently_for_a_missing_source_file`
    above does for the `missing_since` case -- not just an isolated check of
    the helper."""
    jobs_app.open(engine)
    ensure_schema(jobs_app, engine)
    audio_hash = "h7" * 32

    with OrmSession(engine) as session:
        recording = _make_recording(
            session,
            audio_hash=audio_hash,
            path="never_written.wav",
        )
        recording.archive_root_index = 5  # only one root is ever configured
        session.commit()

    job_id = render_spectrogram_task.configure(
        lock=spectrogram_lock_key(audio_hash),
        queueing_lock=spectrogram_lock_key(audio_hash),
    ).defer(audio_hash=audio_hash)

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


def _make_config(
    tmp_path: Path,
    *,
    archive_roots: tuple[Path, ...] | None = None,
    site_min_points: int = 3,
) -> Config:
    roots = archive_roots if archive_roots is not None else (tmp_path / "archive",)
    for root in roots:
        root.mkdir(parents=True, exist_ok=True)
    return Config(
        database_url="postgresql://unused/unused",  # never read by run_ingest_cycle itself
        archive_roots=roots,
        media_root=tmp_path / "media",
        site_min_points=site_min_points,
    )


def test_run_ingest_cycle_creates_a_recording_and_derives(
    engine: Engine,
    tmp_path: Path,
) -> None:
    """Also proves `run_ingest_cycle` drove the whole pipeline end to end --
    not just `commit_scan` -- by asserting on `partition_sessions`,
    `derive_sites`, AND `enqueue_media`'s effects, all of which are
    committed to the database only AFTER the bare `Recording` row is (see
    Important 1's fix). A weaker assertion here is exactly why the missing
    `partition_sessions`/`derive_sites` call on a refused sweep survived four
    prior task reviews."""
    archive_root = tmp_path / "archive"
    path = archive_root / "EPTSER_20150610_215446.wav"
    path.parent.mkdir(parents=True, exist_ok=True)
    audio = bytes(range(256)) * 8  # real, non-trivial PCM content
    path.write_bytes(
        build_wav(
            [
                (b"fmt ", fmt_payload()),
                (b"data", audio),
                # A GPS-bearing `wamd` chunk -- needed so the recording has a
                # `geom` at all, which `derive_sites` requires to consider it
                # for clustering.
                (b"wamd", wamd_payload()),
            ],
        ),
    )
    old = time.time() - 3600
    os.utime(path, (old, old))
    # A single recording is DBSCAN noise (unclustered) under the default
    # `site_min_points=3` -- lowered to 1 so this one recording's GPS
    # position clusters into a real `Site` row, proving `derive_sites` ran.
    config = _make_config(tmp_path, archive_roots=(archive_root,), site_min_points=1)
    jobs_app.open(engine)
    ensure_schema(jobs_app, engine)

    run_ingest_cycle.configure(
        lock=_INGEST_CYCLE_LOCK,
        queueing_lock=_INGEST_CYCLE_LOCK,
    ).defer(timestamp=int(time.time()))
    _run_worker(
        engine,
        wait=False,
        install_signal_handlers=False,
        listen_notify=False,
        queues=["ingest"],
        additional_context={"config": config, "engine": engine},
    )

    with OrmSession(engine) as session:
        recording_count = session.scalar(select(func.count()).select_from(Recording))
        session_count = session.scalar(select(func.count()).select_from(SessionModel))
        site_count = session.scalar(select(func.count()).select_from(Site))
    assert recording_count == 1
    assert session_count == 1  # partition_sessions ran
    assert site_count == 1  # derive_sites ran

    # enqueue_media defers 3 jobs per created recording (spectrogram,
    # oscillogram, preview -- see `services/media.py`'s `enqueue_media`).
    # This worker only drains `queues=["ingest"]` (see `_run_worker`'s
    # docstring above), so the media jobs it deferred are left `todo`,
    # never `succeeded` -- still proof `enqueue_media` actually ran.
    with engine.connect() as conn:
        media_todo_count = conn.execute(
            text(
                "SELECT count(*) FROM procrastinate_jobs "
                "WHERE queue_name = 'media' AND status = 'todo'",
            ),
        ).scalar()
    assert media_todo_count == 3


def test_run_ingest_cycle_logs_and_continues_on_incomplete_scan(
    engine: Engine,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A file too young to have settled makes the sweep refuse
    (IncompleteScanError) -- the cycle must log it and return normally, not
    raise (design spec §6): the job must still show 'succeeded', not
    'failed', in procrastinate_jobs."""
    archive_root = tmp_path / "archive"
    _write_wav(archive_root, "fresh.wav")  # NOT backdated -- still "unsettled"
    config = _make_config(tmp_path, archive_roots=(archive_root,))
    jobs_app.open(engine)
    ensure_schema(jobs_app, engine)

    job_id = run_ingest_cycle.configure(
        lock=_INGEST_CYCLE_LOCK,
        queueing_lock=_INGEST_CYCLE_LOCK,
    ).defer(timestamp=int(time.time()))
    with caplog.at_level(logging.ERROR):
        _run_worker(
            engine,
            wait=False,
            install_signal_handlers=False,
            listen_notify=False,
            queues=["ingest"],
            additional_context={"config": config, "engine": engine},
        )

    assert "refusing to sweep" in caplog.text
    with engine.connect() as conn:
        status = conn.execute(
            text("SELECT status FROM procrastinate_jobs WHERE id = :id"),
            {"id": job_id},
        ).scalar()
    assert status == "succeeded"


def test_run_ingest_cycle_fails_the_job_on_an_unexpected_error(
    engine: Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Distinct from the known-refusal path above: an exception that is
    NEITHER MassDisappearanceError NOR IncompleteScanError must propagate so
    Procrastinate marks the job failed (design spec §6), not get swallowed
    the same way the two known refusal types are. `Path.rglob()` on a
    nonexistent directory does NOT raise (confirmed directly -- it just
    yields nothing), so a bad `archive_roots` entry can't be used to trigger
    this path; monkeypatching `seed_taxonomy` (the first thing the task body
    calls) to raise is deterministic and portable, unlike a permission-based
    approach that would behave differently under a root test runner."""
    import fledermap.jobs.tasks as tasks_module

    def _boom(*_args: object, **_kwargs: object) -> None:
        msg = "synthetic failure for test coverage"
        raise RuntimeError(msg)

    monkeypatch.setattr(tasks_module, "seed_taxonomy", _boom)
    config = _make_config(tmp_path)
    jobs_app.open(engine)
    ensure_schema(jobs_app, engine)

    job_id = run_ingest_cycle.configure(
        lock=_INGEST_CYCLE_LOCK,
        queueing_lock=_INGEST_CYCLE_LOCK,
    ).defer(timestamp=int(time.time()))
    _run_worker(
        engine,
        wait=False,
        install_signal_handlers=False,
        listen_notify=False,
        queues=["ingest"],
        additional_context={"config": config, "engine": engine},
    )

    with engine.connect() as conn:
        status = conn.execute(
            text("SELECT status FROM procrastinate_jobs WHERE id = :id"),
            {"id": job_id},
        ).scalar()
    assert status == "failed"


def test_name_site_task_writes_the_resolved_name_onto_the_site(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(site_naming, "ensure_connected", lambda url: None)
    monkeypatch.setattr(
        site_naming,
        "name_site",
        lambda session, lon, lat, *, radius_m, site_radius_m, force=False: (
            "Tiergarten",
            "Berlin > Mitte",
        ),
    )
    jobs_app.open(engine)
    ensure_schema(jobs_app, engine)

    with OrmSession(engine) as session:
        site = Site(
            centroid=WKTElement("POINT(13.405 52.520)", srid=4326),
            radius_m=50.0,
            recording_count=1,
            first_at=datetime(2026, 8, 28, tzinfo=UTC),
            last_at=datetime(2026, 8, 28, tzinfo=UTC),
        )
        session.add(site)
        session.commit()
        site_id = site.id

    config = Config(
        database_url="postgresql://x/y",
        archive_roots=(Path("/archive"),),
        poiidx_database_url="postgresql://u:p@localhost/poiidx_bats_db",
        site_naming_radius_m=300.0,
    )
    name_site_task.configure(
        lock=_NAME_SITE_LOCK,
        queueing_lock=name_site_queueing_lock(str(site_id)),
    ).defer(site_id=site_id)
    _run_worker(
        engine,
        wait=False,
        install_signal_handlers=False,
        listen_notify=False,
        queues=["geo"],
        additional_context={"config": config, "engine": engine},
    )

    with OrmSession(engine) as session:
        refreshed = session.get(Site, site_id)
        assert refreshed is not None
        assert refreshed.name == "Tiergarten"
        assert refreshed.admin_path == "Berlin > Mitte"


def test_name_site_task_leaves_the_site_unnamed_when_poiidx_resolves_nothing(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(site_naming, "ensure_connected", lambda url: None)
    monkeypatch.setattr(
        site_naming,
        "name_site",
        lambda session, lon, lat, *, radius_m, site_radius_m, force=False: None,
    )
    jobs_app.open(engine)
    ensure_schema(jobs_app, engine)

    with OrmSession(engine) as session:
        site = Site(
            centroid=WKTElement("POINT(13.405 52.520)", srid=4326),
            radius_m=50.0,
            recording_count=1,
            first_at=datetime(2026, 8, 28, tzinfo=UTC),
            last_at=datetime(2026, 8, 28, tzinfo=UTC),
        )
        session.add(site)
        session.commit()
        site_id = site.id

    config = Config(
        database_url="postgresql://x/y",
        archive_roots=(Path("/archive"),),
        poiidx_database_url="postgresql://u:p@localhost/poiidx_bats_db",
        site_naming_radius_m=300.0,
    )
    name_site_task.configure(
        lock=_NAME_SITE_LOCK,
        queueing_lock=name_site_queueing_lock(str(site_id)),
    ).defer(site_id=site_id)
    _run_worker(
        engine,
        wait=False,
        install_signal_handlers=False,
        listen_notify=False,
        queues=["geo"],
        additional_context={"config": config, "engine": engine},
    )

    with OrmSession(engine) as session:
        refreshed = session.get(Site, site_id)
        assert refreshed is not None
        assert refreshed.name is None


def test_name_site_task_passes_the_sites_own_radius_to_name_site(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """site.radius_m must reach name_site -- it drives both the search
    radius and the rank-target selection (services/site_naming.py)."""
    monkeypatch.setattr(site_naming, "ensure_connected", lambda url: None)
    captured: dict[str, object] = {}

    def fake_name_site(
        session: object,
        lon: float,
        lat: float,
        *,
        radius_m: float,
        site_radius_m: float,
        force: bool = False,
    ) -> tuple[str, str | None] | None:
        captured["site_radius_m"] = site_radius_m
        return None

    monkeypatch.setattr(site_naming, "name_site", fake_name_site)
    jobs_app.open(engine)
    ensure_schema(jobs_app, engine)

    with OrmSession(engine) as session:
        site = Site(
            centroid=WKTElement("POINT(13.405 52.520)", srid=4326),
            radius_m=123.5,
            recording_count=1,
            first_at=datetime(2026, 8, 28, tzinfo=UTC),
            last_at=datetime(2026, 8, 28, tzinfo=UTC),
        )
        session.add(site)
        session.commit()
        site_id = site.id

    config = Config(
        database_url="postgresql://x/y",
        archive_roots=(Path("/archive"),),
        poiidx_database_url="postgresql://u:p@localhost/poiidx_bats_db",
        site_naming_radius_m=300.0,
    )
    name_site_task.configure(
        lock=_NAME_SITE_LOCK,
        queueing_lock=name_site_queueing_lock(str(site_id)),
    ).defer(site_id=site_id)
    _run_worker(
        engine,
        wait=False,
        install_signal_handlers=False,
        listen_notify=False,
        queues=["geo"],
        additional_context={"config": config, "engine": engine},
    )

    assert captured["site_radius_m"] == 123.5


def test_name_site_task_forwards_force_to_name_site(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(site_naming, "ensure_connected", lambda url: None)
    captured: dict[str, object] = {}

    def fake_name_site(
        session: object,
        lon: float,
        lat: float,
        *,
        radius_m: float,
        site_radius_m: float,
        force: bool = False,
    ) -> tuple[str, str | None] | None:
        captured["force"] = force
        return None

    monkeypatch.setattr(site_naming, "name_site", fake_name_site)
    jobs_app.open(engine)
    ensure_schema(jobs_app, engine)

    with OrmSession(engine) as session:
        site = Site(
            centroid=WKTElement("POINT(13.405 52.520)", srid=4326),
            radius_m=50.0,
            recording_count=1,
            first_at=datetime(2026, 8, 28, tzinfo=UTC),
            last_at=datetime(2026, 8, 28, tzinfo=UTC),
        )
        session.add(site)
        session.commit()
        site_id = site.id

    config = Config(
        database_url="postgresql://x/y",
        archive_roots=(Path("/archive"),),
        poiidx_database_url="postgresql://u:p@localhost/poiidx_bats_db",
        site_naming_radius_m=300.0,
    )
    name_site_task.configure(
        lock=_NAME_SITE_LOCK,
        queueing_lock=name_site_queueing_lock(str(site_id), force=True),
    ).defer(site_id=site_id, force=True)
    _run_worker(
        engine,
        wait=False,
        install_signal_handlers=False,
        listen_notify=False,
        queues=["geo"],
        additional_context={"config": config, "engine": engine},
    )

    assert captured["force"] is True


def test_name_site_queueing_lock_is_per_site() -> None:
    assert name_site_queueing_lock(str(1)) != name_site_queueing_lock(str(2))


def test_name_site_queueing_lock_is_distinct_for_force() -> None:
    assert name_site_queueing_lock("k") != name_site_queueing_lock("k", force=True)
