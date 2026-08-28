from __future__ import annotations

import os
import stat
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import click
import flask
import pytest
from click.testing import CliRunner
from sqlalchemy import func, select, text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session as OrmSession

import fledermap.cli.main as cli_main
from fledermap.cli.main import (
    EXIT_SWEEP_REFUSED,
    _fetch_missing_vendor_assets_or_die,
    cli,
)
from fledermap.services.vendor_assets import ASSETS, IntegrityError, VendorAsset
from fledermap.store.db import make_engine
from fledermap.store.models import Recording
from tests.fixtures import build_wav, fmt_payload, wamd_payload


def _populate_vendor_cache(static_root: Path) -> None:
    """Pre-warm `serve`'s vendor cache so its automatic
    `ensure_vendor_assets` call finds nothing missing and never touches the
    network -- real network access in a test run is exactly what this
    project's test suite avoids everywhere else."""
    vendor_dir = static_root / "vendor"
    for asset in ASSETS:
        dest = vendor_dir / asset.relative_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"")


pytestmark = pytest.mark.db


@pytest.fixture
def clean_database_url(postgis_url: str) -> Iterator[str]:
    """A database URL backed by a genuinely empty schema.

    Unlike the `engine` fixture used everywhere else in this project, this
    does NOT call `create_all` — the CLI under test builds its own schema via
    `alembic upgrade head` (task-13, defect 2), and running that migration
    against a schema `create_all` already populated fails outright with
    `DuplicateTable` (verified by hand while writing this fixture). Each test
    that drives the CLI therefore needs a schema wiped down to nothing, the
    same way `tests/test_migrations.py`'s `migrated_engine` fixture does,
    so that two CLI tests sharing the session-scoped `postgis_url` container
    don't see each other's rows regardless of the order pytest-randomly picks
    (task-13, defect 3).
    """
    eng = make_engine(postgis_url)
    with eng.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    eng.dispose()
    yield postgis_url


def _archive(tmp_path: Path) -> Path:
    root = tmp_path / "archive" / "Session_20130401_053030"
    root.mkdir(parents=True)
    for name, audio in (
        ("EPTSER_20150610_215446.wav", b"\x01\x02" * 32),
        ("MYODAU_20150623_213547.wav", b"\x09\x08" * 32),
    ):
        path = root / name
        path.write_bytes(
            build_wav(
                [
                    (b"fmt ", fmt_payload()),
                    (b"data", audio),
                    (b"wamd", wamd_payload(auto_id=name[:6])),
                ],
            ),
        )
        old = time.time() - 3600
        os.utime(path, (old, old))
    return tmp_path / "archive"


def _archive_with_n_files(tmp_path: Path, n: int) -> Path:
    """`n` distinct, settled recordings, no metadata beyond fmt/data.

    Filenames use `NoID` so no taxon resolution is involved; each file's
    audio payload and timestamp are unique so each gets its own audio_hash.
    """
    root = tmp_path / "archive" / "Session_20130401_053030"
    root.mkdir(parents=True)
    for i in range(n):
        name = f"NoID_20150610_{215400 + i:06d}.wav"
        audio = bytes([i % 256, (i * 7) % 256]) * 32
        path = root / name
        path.write_bytes(build_wav([(b"fmt ", fmt_payload()), (b"data", audio)]))
        old = time.time() - 3600
        os.utime(path, (old, old))
    return tmp_path / "archive"


def test_ingest_reports_created_recordings(
    clean_database_url: str,
    tmp_path: Path,
) -> None:
    archive = _archive(tmp_path)
    result = CliRunner().invoke(
        cli,
        ["ingest"],
        env={
            "FLEDERMAP_DATABASE_URL": clean_database_url,
            "FLEDERMAP_ARCHIVE_ROOTS": str(archive),
            "FLEDERMAP_MEDIA_ROOT": str(tmp_path / "media"),
        },
    )

    assert result.exit_code == 0, result.output
    assert "created 2" in result.output
    # Defect 6: the report line must cover every IngestReport outcome, not
    # just the five the brief's plan-era code knew about.
    assert "replaced 0" in result.output
    assert "duplicates 0" in result.output
    assert "identifications added 4" in result.output
    assert "superseded 0" in result.output


def test_second_run_creates_nothing(clean_database_url: str, tmp_path: Path) -> None:
    archive = _archive(tmp_path)
    runner = CliRunner()
    env = {
        "FLEDERMAP_DATABASE_URL": clean_database_url,
        "FLEDERMAP_ARCHIVE_ROOTS": str(archive),
        "FLEDERMAP_MEDIA_ROOT": str(tmp_path / "media"),
    }

    first = runner.invoke(cli, ["ingest"], env=env)
    assert first.exit_code == 0, first.output

    result = runner.invoke(cli, ["ingest"], env=env)

    assert result.exit_code == 0, result.output
    assert "created 0" in result.output
    assert "unchanged 2" in result.output


def test_missing_database_url_fails_clearly(tmp_path: Path) -> None:
    # `env={}` would only OVERLAY the current environment (Click merges, it
    # does not replace), so this would silently pass or fail depending on
    # whatever FLEDERMAP_DATABASE_URL happens to be set to in the ambient
    # shell. Deleting the key explicitly is what actually exercises "unset"
    # (task-13, defect 5).
    archive = _archive(tmp_path)
    result = CliRunner().invoke(
        cli,
        ["ingest"],
        env={
            "FLEDERMAP_DATABASE_URL": None,
            "FLEDERMAP_ARCHIVE_ROOTS": str(archive),
        },
    )

    assert result.exit_code != 0
    assert "FLEDERMAP_DATABASE_URL" in result.output


def test_migration_populates_alembic_version(
    clean_database_url: str,
    tmp_path: Path,
) -> None:
    """The CLI must build its schema via `alembic upgrade head`, not
    `create_all` (task-13, defect 2). `create_all` never touches the
    `alembic_version` table; only a real migration run does, so its presence
    (and a single populated row) is a direct, functional proof of which path
    ran — not an assumption about the CLI's internals."""
    archive = _archive(tmp_path)
    result = CliRunner().invoke(
        cli,
        ["ingest"],
        env={
            "FLEDERMAP_DATABASE_URL": clean_database_url,
            "FLEDERMAP_ARCHIVE_ROOTS": str(archive),
            "FLEDERMAP_MEDIA_ROOT": str(tmp_path / "media"),
        },
    )
    assert result.exit_code == 0, result.output

    engine = make_engine(clean_database_url)
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT version_num FROM alembic_version")).all()
    engine.dispose()

    assert len(rows) == 1
    assert rows[0][0]


def test_unsettled_file_refuses_sweep_with_a_distinct_message(
    clean_database_url: str,
    tmp_path: Path,
) -> None:
    """A file skipped as unsettled means `seen_hashes` can't be trusted as
    complete, so the sweep must refuse rather than flag real files as missing
    (task-13, defect 1). The ingest itself must still succeed, and the
    message must be visibly different from a mass-disappearance refusal."""
    archive = _archive(tmp_path)
    # A third file, freshly written (default mtime = now), inside the
    # settle window (< 30s old) and therefore skipped as UNSETTLED.
    fresh = archive / "Session_20130401_053030" / "PIPPIP_20150610_215500.wav"
    fresh.write_bytes(
        build_wav([(b"fmt ", fmt_payload()), (b"data", b"\x03\x04" * 32)]),
    )

    result = CliRunner().invoke(
        cli,
        ["ingest"],
        env={
            "FLEDERMAP_DATABASE_URL": clean_database_url,
            "FLEDERMAP_ARCHIVE_ROOTS": str(archive),
            "FLEDERMAP_MEDIA_ROOT": str(tmp_path / "media"),
        },
    )

    assert result.exit_code == EXIT_SWEEP_REFUSED, result.output
    assert "created 2" in result.output
    assert "skipped 1" in result.output
    assert "skipped during scan" in result.output
    # Distinct from MassDisappearanceError's wording — an operator reading
    # the output must be able to tell the two refusals apart.
    assert "recordings absent" not in result.output


def test_mass_disappearance_refuses_sweep_with_a_distinct_message(
    clean_database_url: str,
    tmp_path: Path,
) -> None:
    """Enough recordings vanishing at once must refuse the sweep rather than
    flag them all — and must be distinguishable from an incomplete-scan
    refusal (task-13, defect 1)."""
    archive = _archive_with_n_files(tmp_path, 12)
    runner = CliRunner()
    env = {
        "FLEDERMAP_DATABASE_URL": clean_database_url,
        "FLEDERMAP_ARCHIVE_ROOTS": str(archive),
        "FLEDERMAP_MEDIA_ROOT": str(tmp_path / "media"),
    }

    first = runner.invoke(cli, ["ingest"], env=env)
    assert first.exit_code == 0, first.output
    assert "created 12" in first.output

    # Remove enough files that the newly-absent fraction (4/12 = 0.33) clears
    # the default 10% mass-disappearance threshold.
    session_dir = archive / "Session_20130401_053030"
    for i in range(4):
        (session_dir / f"NoID_20150610_{215400 + i:06d}.wav").unlink()

    result = runner.invoke(cli, ["ingest"], env=env)

    assert result.exit_code == EXIT_SWEEP_REFUSED, result.output
    assert "recordings absent" in result.output
    assert "skipped during scan" not in result.output


def test_excluded_files_do_not_refuse_the_sweep(
    clean_database_url: str,
    tmp_path: Path,
) -> None:
    """The whole-branch review's headline finding, reproduced and fixed:
    an archive shaped the way spec section 6 itself describes (a Syncthing
    `.stfolder` marker, a `.stignore` file, and an ordinary non-recording
    file alongside real recordings) must not make the mass-disappearance
    guard refuse — those are deliberate, permanent exclusions, not an
    incomplete picture of what's present (Priority 1)."""
    archive = _archive(tmp_path)
    session_dir = archive / "Session_20130401_053030"
    (archive / ".stfolder").write_bytes(b"")
    (session_dir / ".stignore").write_text("*.tmp\n")
    readme = session_dir / "readme.txt"
    readme.write_text("not a recording")
    old = time.time() - 3600
    os.utime(readme, (old, old))

    result = CliRunner().invoke(
        cli,
        ["ingest"],
        env={
            "FLEDERMAP_DATABASE_URL": clean_database_url,
            "FLEDERMAP_ARCHIVE_ROOTS": str(archive),
            "FLEDERMAP_MEDIA_ROOT": str(tmp_path / "media"),
        },
    )

    assert result.exit_code == 0, result.output
    assert "created 2" in result.output


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits only")
@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root ignores permission bits",
)
def test_unreadable_file_still_refuses_the_sweep(
    clean_database_url: str,
    tmp_path: Path,
) -> None:
    """Refining what counts as an incomplete scan must not turn the guard off
    entirely: a genuinely unreadable file (SkipReason.UNREADABLE) is a
    genuine unknown and must still refuse the sweep (Priority 1)."""
    archive = _archive(tmp_path)
    session_dir = archive / "Session_20130401_053030"
    unreadable = session_dir / "PIPPIP_20150610_215500.wav"
    unreadable.write_bytes(
        build_wav([(b"fmt ", fmt_payload()), (b"data", b"\x03\x04" * 32)]),
    )
    old = time.time() - 3600
    os.utime(unreadable, (old, old))
    unreadable.chmod(0o000)

    try:
        result = CliRunner().invoke(
            cli,
            ["ingest"],
            env={
                "FLEDERMAP_DATABASE_URL": clean_database_url,
                "FLEDERMAP_ARCHIVE_ROOTS": str(archive),
                "FLEDERMAP_MEDIA_ROOT": str(tmp_path / "media"),
            },
        )
    finally:
        unreadable.chmod(stat.S_IRUSR | stat.S_IWUSR)

    assert result.exit_code == EXIT_SWEEP_REFUSED, result.output
    assert "created 2" in result.output
    assert "skipped 1" in result.output
    assert "skipped during scan" in result.output


def test_no_sweep_flag_bypasses_the_refusal_entirely(
    clean_database_url: str,
    tmp_path: Path,
) -> None:
    """`--no-sweep` must skip `sweep_missing` altogether, not just suppress its
    warning — the same archive state that trips
    `EXIT_SWEEP_REFUSED` above must exit 0 here, with the ingest itself still
    landing (task-13, review, minor: `--no-sweep` was previously untested)."""
    archive = _archive_with_n_files(tmp_path, 12)
    runner = CliRunner()
    env = {
        "FLEDERMAP_DATABASE_URL": clean_database_url,
        "FLEDERMAP_ARCHIVE_ROOTS": str(archive),
        "FLEDERMAP_MEDIA_ROOT": str(tmp_path / "media"),
    }

    first = runner.invoke(cli, ["ingest"], env=env)
    assert first.exit_code == 0, first.output

    session_dir = archive / "Session_20130401_053030"
    for i in range(4):
        (session_dir / f"NoID_20150610_{215400 + i:06d}.wav").unlink()

    result = runner.invoke(cli, ["ingest", "--no-sweep"], env=env)

    assert result.exit_code == 0, result.output
    assert "recordings absent" not in result.output
    assert "flagged" not in result.output


def _snapshot_tree(root: Path) -> dict[str, tuple[float, int, bool]]:
    """Every path under `root`, plus enough stat info to notice any change:
    mtime, size, and whether it's a directory. A dict keyed by relative path
    also catches a new file appearing or an existing one disappearing, not
    just a modification to a file already known about."""
    return {
        str(p.relative_to(root)): (p.stat().st_mtime, p.stat().st_size, p.is_dir())
        for p in root.rglob("*")
    }


def test_cli_ingest_does_not_modify_the_archive_tree(
    clean_database_url: str,
    tmp_path: Path,
) -> None:
    """D16: ingest must be strictly read-only on the archive. The existing
    scan-level test only checks one file's mtime/bytes through `scan()`
    alone, and wouldn't notice a new file created elsewhere in the tree, or
    anything written during an error path. This snapshots the ENTIRE archive
    tree before and after a full CLI `ingest` run (whole-branch review,
    Minor F)."""
    archive = _archive(tmp_path)
    before = _snapshot_tree(archive)

    result = CliRunner().invoke(
        cli,
        ["ingest"],
        env={
            "FLEDERMAP_DATABASE_URL": clean_database_url,
            "FLEDERMAP_ARCHIVE_ROOTS": str(archive),
            "FLEDERMAP_MEDIA_ROOT": str(tmp_path / "media"),
        },
    )
    assert result.exit_code == 0, result.output

    after = _snapshot_tree(archive)
    assert after == before


def test_gps_less_recording_ingests_with_null_geom(
    clean_database_url: str,
    tmp_path: Path,
) -> None:
    """Phase exit criterion: 'recordings without GPS ingest successfully with
    geom IS NULL' — the mechanism is correct and each half is tested
    separately elsewhere, but this proves the conjunction through the actual
    CLI (whole-branch review, Minor E)."""
    archive = _archive_with_n_files(tmp_path, 1)  # fmt/data only: no GUANO, no wamd

    result = CliRunner().invoke(
        cli,
        ["ingest"],
        env={
            "FLEDERMAP_DATABASE_URL": clean_database_url,
            "FLEDERMAP_ARCHIVE_ROOTS": str(archive),
            "FLEDERMAP_MEDIA_ROOT": str(tmp_path / "media"),
        },
    )
    assert result.exit_code == 0, result.output
    assert "created 1" in result.output

    engine = make_engine(clean_database_url)
    with OrmSession(engine) as session:
        recording = session.scalars(select(Recording)).one()
        assert recording.geom is None
    engine.dispose()


def test_derive_command_reports_sessions_and_sites(
    clean_database_url: str,
    tmp_path: Path,
) -> None:
    archive = _archive(tmp_path)
    runner = CliRunner()
    env = {
        "FLEDERMAP_DATABASE_URL": clean_database_url,
        "FLEDERMAP_ARCHIVE_ROOTS": str(archive),
        "FLEDERMAP_MEDIA_ROOT": str(tmp_path / "media"),
    }

    ingest_result = runner.invoke(cli, ["ingest"], env=env)
    assert ingest_result.exit_code == 0, ingest_result.output

    result = runner.invoke(cli, ["derive"], env=env)

    assert result.exit_code == 0, result.output
    # Both recordings share one (absent) detector key, but their filename
    # dates are 13 days apart (2015-06-10 vs 2015-06-23; only their
    # times-of-day are ~19 minutes apart) -> well past the default 6h
    # session gap, so each lands in its own new session.
    assert "sessions: created 2  extended 0  merge proposals 0" in result.output
    # Identical GPS position but only 2 points, below the default
    # site_min_points=3 -> correctly noise, not a site.
    assert "sites: 0  unclustered 2" in result.output


def test_derive_command_is_idempotent(
    clean_database_url: str,
    tmp_path: Path,
) -> None:
    archive = _archive(tmp_path)
    runner = CliRunner()
    env = {
        "FLEDERMAP_DATABASE_URL": clean_database_url,
        "FLEDERMAP_ARCHIVE_ROOTS": str(archive),
        "FLEDERMAP_MEDIA_ROOT": str(tmp_path / "media"),
    }
    runner.invoke(cli, ["ingest"], env=env)
    runner.invoke(cli, ["derive"], env=env)

    result = runner.invoke(cli, ["derive"], env=env)

    assert result.exit_code == 0, result.output
    # Second run: nothing left unsessioned to partition, no new sessions.
    assert "sessions: created 0  extended 0  merge proposals 0" in result.output


def test_ingest_enqueues_media_jobs_for_created_recordings(
    clean_database_url: str,
    tmp_path: Path,
) -> None:
    archive = _archive(tmp_path)
    env = {
        "FLEDERMAP_DATABASE_URL": clean_database_url,
        "FLEDERMAP_ARCHIVE_ROOTS": str(archive),
        "FLEDERMAP_MEDIA_ROOT": str(tmp_path / "media"),
    }
    runner = CliRunner()

    result = runner.invoke(cli, ["ingest"], env=env)

    assert result.exit_code == 0, result.output

    engine = make_engine(clean_database_url)
    with engine.connect() as conn:
        count = conn.execute(
            text("SELECT count(*) FROM procrastinate_jobs WHERE status = 'todo'"),
        ).scalar()
    engine.dispose()
    # _archive() writes 2 distinct recordings -> 2 hashes -> 6 jobs
    # (spectrogram + oscillogram + preview each).
    assert count == 6


def test_worker_no_wait_processes_queued_jobs_and_writes_media(
    clean_database_url: str,
    tmp_path: Path,
) -> None:
    archive = _archive(tmp_path)
    media_root = tmp_path / "media"
    env = {
        "FLEDERMAP_DATABASE_URL": clean_database_url,
        "FLEDERMAP_ARCHIVE_ROOTS": str(archive),
        "FLEDERMAP_MEDIA_ROOT": str(media_root),
    }
    runner = CliRunner()
    runner.invoke(cli, ["ingest"], env=env)

    result = runner.invoke(cli, ["worker", "--no-wait"], env=env)

    assert result.exit_code == 0, result.output
    spectrograms = list(media_root.glob("*/*/spectrogram-*.webp"))
    oscillograms = list(media_root.glob("*/*/oscillogram-*.webp"))
    previews = list(media_root.glob("*/*/preview-*.opus"))
    assert len(spectrograms) == 2
    assert len(oscillograms) == 2
    assert len(previews) == 2


def test_worker_wait_mode_picks_up_a_file_dropped_in_after_startup(
    clean_database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: `worker` (no --no-wait) is already running, a WAV appears
    in the watched archive, and it gets ingested without a second `ingest`
    invocation -- the actual behavior this whole phase exists to add."""
    import threading

    archive = _archive_with_n_files(tmp_path, 0)  # empty, settled archive dir
    # Deliberately NOT `runner.invoke(cli, ["worker"], env={...})`: Click's
    # `CliRunner.invoke` patches `os.environ` inside `with self.isolation(...)`
    # and only reverts it in that block's `finally`, which runs when `invoke`
    # RETURNS -- and this `--wait` invocation, run on a background thread we
    # never cleanly stop (see the comment below), never returns during this
    # test's lifetime. Under a real invocation that would leak these three
    # FLEDERMAP_* env vars into the real process environment for the rest of
    # the (xdist worker) process, corrupting any later test that relies on
    # them being unset -- confirmed by reproducing exactly that against
    # `test_config.py`'s archive_roots validation test. `monkeypatch.setenv`
    # reverts unconditionally at THIS test's teardown instead, independent of
    # whether the invoked command ever returns.
    monkeypatch.setenv("FLEDERMAP_DATABASE_URL", clean_database_url)
    monkeypatch.setenv("FLEDERMAP_MEDIA_ROOT", str(tmp_path / "media"))
    monkeypatch.setenv("FLEDERMAP_ARCHIVE_ROOTS", str(archive))
    runner = CliRunner()
    result_holder: list[object] = []

    def _run() -> None:
        result_holder.append(runner.invoke(cli, ["worker"]))

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    time.sleep(0.5)  # let the worker/observer actually start

    path = archive / "EPTSER_20150610_215446.wav"
    path.write_bytes(
        build_wav([(b"fmt ", fmt_payload()), (b"data", b"\x01\x02" * 32)]),
    )
    old = time.time() - 3600
    os.utime(path, (old, old))

    deadline = time.time() + 10
    engine = make_engine(clean_database_url)
    found = False
    while time.time() < deadline:
        try:
            with OrmSession(engine) as session:
                if session.scalar(select(func.count()).select_from(Recording)):
                    found = True
                    break
        except ProgrammingError:
            # The daemon thread's `worker` invocation runs its own
            # `_run_migrations` before the `recording` table exists -- this
            # main-thread polling loop can legitimately start querying
            # before that finishes. Not a real failure: keep polling.
            pass
        time.sleep(0.2)

    # No clean way to stop a CliRunner-invoked `--wait` worker from here --
    # this test process exiting ends the daemon thread. Not attempting a
    # graceful shutdown call; document that limitation rather than papering
    # over it with a fragile signal-based workaround.
    assert found, "recording was not ingested within the timeout"


def test_enqueue_media_command_reports_disk_gap_but_avoids_duplicate_jobs(
    clean_database_url: str,
    tmp_path: Path,
) -> None:
    """`ingest` then `enqueue-media`, with no worker in between, reports
    "enqueued 2" while creating no new job rows at all. Both halves are
    correct, because two independent mechanisms are answering two different
    questions.

    `backfill_media`'s count comes from DISK state (design spec P3-6:
    Procrastinate's job history is not a reliable durable record, so disk is
    the source of truth for "has this been rendered"). No worker has run, so
    nothing is rendered, so both recordings count as missing -- and that
    count is computed BEFORE `enqueue_media` is called, so it cannot reflect
    what happens to those hashes afterwards.

    `queueing_lock` answers "is a job already in flight for this?" and
    refuses all 6 duplicate `defer()` attempts with `AlreadyEnqueued` (caught
    and ignored), because `ingest`'s own jobs are still `todo`. Hence the
    row-count assertion below: still 6, not 12.

    So the reported count can overstate work already in flight. That is the
    accepted cost of P3-6's choice, not a bug -- asserted explicitly here so
    a future reader doesn't "fix" it."""
    archive = _archive(tmp_path)
    env = {
        "FLEDERMAP_DATABASE_URL": clean_database_url,
        "FLEDERMAP_ARCHIVE_ROOTS": str(archive),
        "FLEDERMAP_MEDIA_ROOT": str(tmp_path / "media"),
    }
    runner = CliRunner()
    runner.invoke(cli, ["ingest"], env=env)

    result = runner.invoke(cli, ["enqueue-media"], env=env)

    assert result.exit_code == 0, result.output
    assert "enqueued 2" in result.output

    engine = make_engine(clean_database_url)
    with engine.connect() as conn:
        count = conn.execute(
            text("SELECT count(*) FROM procrastinate_jobs WHERE status = 'todo'"),
        ).scalar()
    engine.dispose()
    # Still exactly the 6 original ingest-triggered jobs -- enqueue-media's
    # own defer attempts were all refused by queueing_lock, even though the
    # reported count above doesn't reflect that.
    assert count == 6


def test_serve_command_starts_without_error(
    clean_database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`--help` is an eager Click option: it prints help and exits *before*
    the command callback runs, so invoking with `--help` alone would prove
    nothing about `Config.from_env`/`make_engine`/`_run_migrations`/
    `create_app` actually completing. Instead this patches `Flask.run` to a
    non-blocking recorder and invokes `serve` for real, so the whole wiring
    up to (but not including) actually listening runs and can be asserted
    on."""
    calls: list[tuple[str | None, int | None]] = []

    def fake_run(
        self: flask.Flask,
        host: str | None = None,
        port: int | None = None,
        **_kwargs: object,
    ) -> None:
        calls.append((host, port))

    monkeypatch.setattr(flask.Flask, "run", fake_run)
    _populate_vendor_cache(tmp_path / "static")

    runner = CliRunner()
    env = {
        "FLEDERMAP_DATABASE_URL": clean_database_url,
        "FLEDERMAP_ARCHIVE_ROOTS": str(tmp_path / "archive"),
        "FLEDERMAP_MEDIA_ROOT": str(tmp_path / "media"),
        "FLEDERMAP_STATIC_ROOT": str(tmp_path / "static"),
    }

    result = runner.invoke(
        cli, ["serve", "--host", "0.0.0.0", "--port", "5001"], env=env
    )

    assert result.exit_code == 0, result.output
    assert calls == [("0.0.0.0", 5001)]


def test_serve_command_uses_configured_host_and_port_when_flags_omitted(
    clean_database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FLEDERMAP_HOST/FLEDERMAP_PORT (or the config file's `host`/`port`)
    supply the defaults when `--host`/`--port` aren't given on the command
    line -- explicit flags, as in `test_serve_command_starts_without_error`
    above, still override them."""
    calls: list[tuple[str | None, int | None]] = []

    def fake_run(
        self: flask.Flask,
        host: str | None = None,
        port: int | None = None,
        **_kwargs: object,
    ) -> None:
        calls.append((host, port))

    monkeypatch.setattr(flask.Flask, "run", fake_run)
    _populate_vendor_cache(tmp_path / "static")

    runner = CliRunner()
    env = {
        "FLEDERMAP_DATABASE_URL": clean_database_url,
        "FLEDERMAP_ARCHIVE_ROOTS": str(tmp_path / "archive"),
        "FLEDERMAP_MEDIA_ROOT": str(tmp_path / "media"),
        "FLEDERMAP_STATIC_ROOT": str(tmp_path / "static"),
        "FLEDERMAP_PORT": "9090",
        "FLEDERMAP_HOST": "0.0.0.0",
    }

    result = runner.invoke(cli, ["serve"], env=env)

    assert result.exit_code == 0, result.output
    assert calls == [("0.0.0.0", 9090)]


def test_fetch_missing_vendor_assets_or_die_reports_what_it_fetched(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fetched = (
        VendorAsset(url="https://x/a.js", sha256="0" * 64, relative_path="a.js"),
    )
    monkeypatch.setattr(cli_main, "ensure_vendor_assets", lambda _vendor_dir: fetched)

    _fetch_missing_vendor_assets_or_die(tmp_path / "vendor")

    assert "fetched 1 vendor asset(s)" in capsys.readouterr().out


def test_fetch_missing_vendor_assets_or_die_is_silent_when_cache_is_warm(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli_main, "ensure_vendor_assets", lambda _vendor_dir: ())

    _fetch_missing_vendor_assets_or_die(tmp_path / "vendor")

    assert capsys.readouterr().out == ""


def test_fetch_missing_vendor_assets_or_die_wraps_integrity_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(_vendor_dir: Path) -> tuple[VendorAsset, ...]:
        raise IntegrityError("bad hash")

    monkeypatch.setattr(cli_main, "ensure_vendor_assets", boom)

    with pytest.raises(click.ClickException, match="fledermap fetch-assets"):
        _fetch_missing_vendor_assets_or_die(tmp_path / "vendor")


def test_fetch_assets_command_does_not_require_database_or_media_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: an earlier version of this command went through
    `Config.from_env`, which demanded FLEDERMAP_DATABASE_URL/FLEDERMAP_MEDIA_ROOT
    even though `fetch-assets` touches neither -- exactly the friction this
    command exists to avoid for someone pre-warming a deployment ahead of
    setting up a database at all."""
    calls: list[Path] = []
    monkeypatch.setattr(
        cli_main,
        "fetch_all_vendor_assets",
        lambda vendor_dir: calls.append(vendor_dir),
    )

    runner = CliRunner()
    env = {
        "FLEDERMAP_DATABASE_URL": None,
        "FLEDERMAP_MEDIA_ROOT": None,
        "FLEDERMAP_STATIC_ROOT": str(tmp_path / "static"),
    }

    result = runner.invoke(cli, ["fetch-assets"], env=env)

    assert result.exit_code == 0, result.output
    assert calls == [tmp_path / "static" / "vendor"]
    assert f"fetched {len(ASSETS)} vendor asset(s)" in result.output


def test_fetch_assets_command_reports_integrity_failure_cleanly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(_vendor_dir: Path) -> None:
        raise IntegrityError("bad hash")

    monkeypatch.setattr(cli_main, "fetch_all_vendor_assets", boom)

    runner = CliRunner()
    env = {"FLEDERMAP_STATIC_ROOT": str(tmp_path / "static")}

    result = runner.invoke(cli, ["fetch-assets"], env=env)

    assert result.exit_code != 0
    assert "bad hash" in result.output
