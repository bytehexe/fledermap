"""Round-trip test for `scripts/db-backup.sh` / `scripts/db-restore.sh`.

Dev tooling, not project code (see the scripts' own docstrings and
CLAUDE.md's "scripts/" bullet) -- but it touches a real database and is
trivially easy to get subtly wrong (a bad flag order, a URL resolved
against the wrong environment), so it gets the same real-Postgres coverage
as everything else that talks to `bats_db`'s schema.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import Engine, text

pytestmark = pytest.mark.db

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BACKUP_SCRIPT = _REPO_ROOT / "scripts" / "db-backup.sh"
_RESTORE_SCRIPT = _REPO_ROOT / "scripts" / "db-restore.sh"


def _run_script_env(database_url: str, tmp_path: Path) -> dict[str, str]:
    """The scripts resolve the DB URL via a fresh `hatch run python -c
    ...` subprocess, which does NOT inherit this test process's
    monkeypatched config-file redirection (`conftest.py`'s
    `_isolate_fledermap_config_file`) -- only env vars cross that
    boundary. `FLEDERMAP_DATABASE_URL` wins over any config file regardless
    (`Config.from_env`'s own precedence), so setting it here is enough on
    its own; `FLEDERMAP_ARCHIVE_ROOTS` is required by `Config.from_env`
    too, even though these scripts never touch it."""
    return {
        **os.environ,
        "FLEDERMAP_DATABASE_URL": database_url,
        "FLEDERMAP_ARCHIVE_ROOTS": str(tmp_path),
    }


def test_backup_then_restore_round_trips_a_row(
    engine: Engine,
    postgis_url: str,
    tmp_path: Path,
) -> None:
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE db_backup_smoke (value text)"))
        conn.execute(
            text("INSERT INTO db_backup_smoke (value) VALUES ('before-backup')"),
        )

    env = _run_script_env(postgis_url, tmp_path)
    backup = subprocess.run(
        [str(_BACKUP_SCRIPT)],
        cwd=_REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert backup.returncode == 0, backup.stderr

    dump_dir = _REPO_ROOT / ".db-backups"
    dumps = sorted(dump_dir.glob("bats_db-*.dump"), key=lambda p: p.stat().st_mtime)
    assert dumps, backup.stdout
    dump_file = dumps[-1]

    with engine.begin() as conn:
        conn.execute(text("UPDATE db_backup_smoke SET value = 'mutated-after-backup'"))

    restore = subprocess.run(
        [str(_RESTORE_SCRIPT), "--dangerously-restore", str(dump_file)],
        cwd=_REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert restore.returncode == 0, restore.stderr

    with engine.connect() as conn:
        value = conn.execute(text("SELECT value FROM db_backup_smoke")).scalar_one()
    assert value == "before-backup"

    dump_file.unlink()


def test_restore_refuses_without_the_dangerous_flag(tmp_path: Path) -> None:
    """No flag, or the wrong flag, must never reach `pg_restore` -- this is
    the one thing standing between an ordinary invocation and replacing a
    real database's contents."""
    dump_file = tmp_path / "fake.dump"
    dump_file.write_bytes(b"not a real dump -- must never be read")

    result = subprocess.run(
        [str(_RESTORE_SCRIPT), str(dump_file)],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "--dangerously-restore" in result.stderr
