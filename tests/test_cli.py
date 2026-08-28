from __future__ import annotations

import asyncio
import contextlib
import os
import stat
import sys
import time
from collections.abc import Awaitable, Callable, Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import click
import flask
import pytest
from click.testing import CliRunner
from geoalchemy2.elements import WKTElement
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session as OrmSession

import fledermap.cli.main as cli_main
from fledermap.cli.main import (
    EXIT_SWEEP_REFUSED,
    _fetch_missing_vendor_assets_or_die,
    _run_migrations,
    cli,
)
from fledermap.config import Config
from fledermap.jobs.app import ensure_schema
from fledermap.jobs.watch import start_watching as _real_start_watching
from fledermap.services.vendor_assets import ASSETS, IntegrityError, VendorAsset
from fledermap.store.db import make_engine
from fledermap.store.models import Recording, Site
from tests.fixtures import build_wav, fmt_payload, wamd_payload

if TYPE_CHECKING:
    from watchdog.observers.api import BaseObserver


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


def test_ingest_rejects_a_nonexistent_archive_root(tmp_path: Path) -> None:
    """Important 2: `_parse_archive_roots` (config.py) never checks a root
    exists, so without this check `ingest` would either scan nothing (an
    existing-but-empty mountpoint) or, for a genuinely missing path, still
    proceed into `scan_all_roots` (`Path.rglob` on a nonexistent directory
    doesn't raise -- confirmed in `test_jobs_tasks.py`). The check runs
    before any database work, so a real Postgres container isn't needed
    here."""
    missing_root = tmp_path / "does-not-exist"
    result = CliRunner().invoke(
        cli,
        ["ingest"],
        env={
            "FLEDERMAP_DATABASE_URL": "postgresql://unused/unused",
            "FLEDERMAP_ARCHIVE_ROOTS": str(missing_root),
            "FLEDERMAP_MEDIA_ROOT": str(tmp_path / "media"),
        },
    )

    assert result.exit_code == 1, result.output
    assert "archive root(s) do not exist" in result.output
    assert str(missing_root) in result.output


def test_worker_rejects_a_nonexistent_archive_root(tmp_path: Path) -> None:
    """Important 2, `worker` side: without this check, `Observer.start()`
    inside `start_watching` raises a raw unhandled `OSError(ENOTDIR)`
    instead of a clean `ClickException` -- confirmed against watchdog's
    `InotifyEmitter` directly. `--no-wait` keeps this test from needing to
    manage a `--wait` worker's lifecycle just to prove config validation
    happens before the worker (or any watcher) ever starts."""
    missing_root = tmp_path / "does-not-exist"
    result = CliRunner().invoke(
        cli,
        ["worker", "--no-wait"],
        env={
            "FLEDERMAP_DATABASE_URL": "postgresql://unused/unused",
            "FLEDERMAP_ARCHIVE_ROOTS": str(missing_root),
            "FLEDERMAP_MEDIA_ROOT": str(tmp_path / "media"),
        },
    )

    assert result.exit_code == 1, result.output
    assert "archive root(s) do not exist" in result.output
    assert str(missing_root) in result.output


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
    invocation via the watchdog/debounce path specifically -- not via
    Procrastinate's periodic-cron startup catch-up, which is deliberately
    starved here: the archive is empty when the worker starts, so any
    catch-up cycle finds nothing and Procrastinate won't fire the cron again
    for 5 minutes, well outside this test's short polling window. The
    debounce window is shortened (via monkeypatching the name
    `fledermap.cli.main.start_watching`, not by changing the production
    default) so the watcher path itself completes quickly; production still
    always uses DEFAULT_SETTLE_SECONDS.

    Drives `cli_main._run_worker_async` directly as a cancellable
    `asyncio.Task` on this test's own event loop, rather than through
    `CliRunner` on a background thread the old version of this test never
    cleanly stopped. That leaked worker listened on ALL queues against the
    same session-scoped Postgres container every later `test_cli.py` test
    shares, competing for jobs and re-deferring `run_ingest_cycle` itself --
    a real risk the final review flagged, not a cosmetic one. `task.cancel()`
    + `await task` in a `finally` below gives this test full, deterministic
    ownership of the worker's lifecycle instead."""

    def _short_debounce_start_watching(
        archive_roots: Sequence[Path],
        loop: asyncio.AbstractEventLoop,
        defer: Callable[[], Awaitable[None]],
        *,
        debounce_seconds: float = 0.5,
    ) -> BaseObserver:
        # Ignores whatever `debounce_seconds` it was called with (production
        # never overrides it) and forces a short one instead, so this test's
        # polling window can be comfortably shorter than 5 minutes while
        # still exercising the real `start_watching` -- same archive_roots,
        # same loop, same `_defer_ingest_cycle` closure `cli/main.py` wires
        # in for real.
        return _real_start_watching(archive_roots, loop, defer, debounce_seconds=0.5)

    monkeypatch.setattr(cli_main, "start_watching", _short_debounce_start_watching)

    archive = _archive_with_n_files(tmp_path, 0)  # empty, settled archive dir
    config = Config(
        database_url=clean_database_url,
        archive_roots=(archive,),
        media_root=tmp_path / "media",
    )
    # Built directly against `clean_database_url`, not `engine` (the
    # `create_all`-backed fixture used everywhere else) -- the CLI builds its
    # own schema via `alembic upgrade head`, same reason `clean_database_url`
    # itself exists (see its own fixture docstring above).
    engine = make_engine(clean_database_url)

    # Same sequence `worker`'s command body runs (cli/main.py, ~line 259-290):
    # migrations, then `ensure_schema` -- BEFORE the worker task starts.
    cli_main._run_migrations(config.database_url)
    ensure_schema(cli_main.jobs_app, engine)

    async def _drive() -> bool:
        task = asyncio.create_task(
            cli_main._run_worker_async(config, engine, wait=True),
        )
        try:
            await asyncio.sleep(2.0)  # let the worker start AND let any cron
            # startup catch-up run against the still-empty archive, so its
            # only remaining opportunity to fire `run_ingest_cycle` again is
            # the next scheduled tick, 5 minutes away -- well outside this
            # test's polling window below.

            path = archive / "EPTSER_20150610_215446.wav"
            path.write_bytes(
                build_wav([(b"fmt ", fmt_payload()), (b"data", b"\x01\x02" * 32)]),
            )
            old = time.time() - 3600
            os.utime(path, (old, old))

            deadline = time.time() + 5  # comfortably above the 0.5s debounce,
            # comfortably below the 5-minute next cron tick
            found = False
            while time.time() < deadline:
                with OrmSession(engine) as session:
                    if session.scalar(select(func.count()).select_from(Recording)):
                        found = True
                        break
                await asyncio.sleep(0.2)
            return found
        finally:
            # Deterministic teardown: cancel the worker task and wait for it
            # to actually finish unwinding (`_run_worker_async`'s own
            # `finally` stops and joins the watchdog Observer) before this
            # test -- and the event loop underneath it -- goes away. No
            # reliance on process exit or a later test's side effect.
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    try:
        found = asyncio.run(_drive())
    finally:
        engine.dispose()

    assert found, (
        "recording was not ingested within the timeout -- the watcher path did not fire"
    )


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


def test_backfill_site_names_command_reports_zero_with_no_sites(
    clean_database_url: str,
    tmp_path: Path,
) -> None:
    """No sites exist yet -- the command must still succeed and report
    "enqueued 0", whether or not poiidx is configured at all (it isn't,
    here)."""
    env = {
        "FLEDERMAP_DATABASE_URL": clean_database_url,
        "FLEDERMAP_ARCHIVE_ROOTS": str(tmp_path / "archive"),
    }
    runner = CliRunner()

    result = runner.invoke(cli, ["backfill-site-names"], env=env)

    assert result.exit_code == 0, result.output
    assert "enqueued 0" in result.output


def test_backfill_site_names_command_enqueues_an_unnamed_site(
    clean_database_url: str,
    tmp_path: Path,
) -> None:
    engine = make_engine(clean_database_url)
    _run_migrations(clean_database_url)
    with OrmSession(engine) as session:
        session.add(
            Site(
                centroid=WKTElement("POINT(13.405 52.520)", srid=4326),
                radius_m=50.0,
                recording_count=1,
                first_at=datetime(2026, 8, 28, tzinfo=UTC),
                last_at=datetime(2026, 8, 28, tzinfo=UTC),
            ),
        )
        session.commit()
    engine.dispose()

    env = {
        "FLEDERMAP_DATABASE_URL": clean_database_url,
        "FLEDERMAP_ARCHIVE_ROOTS": str(tmp_path / "archive"),
        "FLEDERMAP_POIIDX_DATABASE_URL": "postgresql://u:p@localhost/poiidx_bats_db",
    }
    runner = CliRunner()

    result = runner.invoke(cli, ["backfill-site-names"], env=env)

    assert result.exit_code == 0, result.output
    assert "enqueued 1" in result.output


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
