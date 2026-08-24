from __future__ import annotations

import os
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from click.testing import CliRunner
from sqlalchemy import text

from fledermap.cli.main import EXIT_SWEEP_REFUSED, cli
from fledermap.store.db import make_engine
from tests.fixtures import build_wav, fmt_payload, wamd_payload

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
        ["ingest", str(archive)],
        env={"FLEDERMAP_DATABASE_URL": clean_database_url},
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
    env = {"FLEDERMAP_DATABASE_URL": clean_database_url}

    first = runner.invoke(cli, ["ingest", str(archive)], env=env)
    assert first.exit_code == 0, first.output

    result = runner.invoke(cli, ["ingest", str(archive)], env=env)

    assert result.exit_code == 0, result.output
    assert "created 0" in result.output
    assert "unchanged 2" in result.output


def test_missing_database_url_fails_clearly(tmp_path: Path) -> None:
    # `env={}` would only OVERLAY the current environment (Click merges, it
    # does not replace), so this would silently pass or fail depending on
    # whatever FLEDERMAP_DATABASE_URL happens to be set to in the ambient
    # shell. Deleting the key explicitly is what actually exercises "unset"
    # (task-13, defect 5).
    result = CliRunner().invoke(
        cli,
        ["ingest", str(_archive(tmp_path))],
        env={"FLEDERMAP_DATABASE_URL": None},
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
        ["ingest", str(archive)],
        env={"FLEDERMAP_DATABASE_URL": clean_database_url},
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
        ["ingest", str(archive)],
        env={"FLEDERMAP_DATABASE_URL": clean_database_url},
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
    env = {"FLEDERMAP_DATABASE_URL": clean_database_url}

    first = runner.invoke(cli, ["ingest", str(archive)], env=env)
    assert first.exit_code == 0, first.output
    assert "created 12" in first.output

    # Remove enough files that the newly-absent fraction (4/12 = 0.33) clears
    # the default 10% mass-disappearance threshold.
    session_dir = archive / "Session_20130401_053030"
    for i in range(4):
        (session_dir / f"NoID_20150610_{215400 + i:06d}.wav").unlink()

    result = runner.invoke(cli, ["ingest", str(archive)], env=env)

    assert result.exit_code == EXIT_SWEEP_REFUSED, result.output
    assert "recordings absent" in result.output
    assert "skipped during scan" not in result.output


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
    env = {"FLEDERMAP_DATABASE_URL": clean_database_url}

    first = runner.invoke(cli, ["ingest", str(archive)], env=env)
    assert first.exit_code == 0, first.output

    session_dir = archive / "Session_20130401_053030"
    for i in range(4):
        (session_dir / f"NoID_20150610_{215400 + i:06d}.wav").unlink()

    result = runner.invoke(cli, ["ingest", "--no-sweep", str(archive)], env=env)

    assert result.exit_code == 0, result.output
    assert "recordings absent" not in result.output
    assert "flagged" not in result.output
