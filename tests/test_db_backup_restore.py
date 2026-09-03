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
from sqlalchemy.engine import make_url

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
    too, even though these scripts never touch it.

    `DB_BACKUP_DIR` points db-backup.sh at an isolated tmp_path instead of
    the real `.db-backups/` at the repo root -- a test run must never
    leave throwaway dumps mixed in with real ones there."""
    return {
        **os.environ,
        "FLEDERMAP_DATABASE_URL": database_url,
        "FLEDERMAP_ARCHIVE_ROOTS": str(tmp_path),
        "DB_BACKUP_DIR": str(tmp_path / "db-backups"),
    }


def test_backup_then_restore_round_trips_a_row(
    engine: Engine,
    postgis_url: str,
    tmp_path: Path,
) -> None:
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS db_backup_smoke"))
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

    dump_dir = Path(env["DB_BACKUP_DIR"])
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


def test_backup_then_restore_succeeds_when_role_does_not_own_postgis(
    engine: Engine,
    postgis_url: str,
    tmp_path: Path,
) -> None:
    """Reproduces the real-deployment failure directly, not hypothesised:
    confirmed live against the real `bats_db` on 2026-09-03. `pg_dump
    --clean` includes `DROP EXTENSION IF EXISTS postgis` and its data
    (`spatial_ref_sys`, a postgis "extension configuration table" pg_dump
    includes by default) regardless of what data we actually care about --
    and a role that never ran `CREATE EXTENSION postgis` itself (normal
    production shape: a superuser creates it once at DB setup, the app's
    own role never does -- `alembic/versions/0001_initial.py` only ever
    runs `CREATE EXTENSION IF NOT EXISTS`, which is a no-op once it
    already exists) cannot drop it on restore: `must be owner of extension
    postgis` / `permission denied for table spatial_ref_sys`. The `engine`
    fixture's role owns everything it creates (including postgis, via its
    own `CREATE EXTENSION IF NOT EXISTS`), so it does NOT reproduce this on
    its own -- a second, restricted role that owns its own table but not
    postgis is created here specifically to match."""
    url = make_url(postgis_url)
    restricted_role = "fledermap_test_restricted"
    restricted_password = "test-only-password"  # noqa: S105 -- test-only, throwaway

    with engine.begin() as conn:
        # A role that owns/was granted privileges cannot simply be
        # DROP ROLE'd -- DROP OWNED BY first revokes those and drops
        # anything it owns (a previous failed run's leftovers, here).
        conn.execute(
            text(
                f"""
                DO $$
                BEGIN
                  IF EXISTS (
                    SELECT 1 FROM pg_roles WHERE rolname = '{restricted_role}'
                  ) THEN
                    EXECUTE 'ALTER DATABASE {url.database} OWNER TO {url.username}';
                    EXECUTE 'DROP OWNED BY {restricted_role}';
                    EXECUTE 'DROP ROLE {restricted_role}';
                  END IF;
                END $$;
                """,
            ),
        )
        conn.execute(
            text(
                f"CREATE ROLE {restricted_role} LOGIN PASSWORD '{restricted_password}'",
            ),
        )
        conn.execute(
            text(f"GRANT CONNECT ON DATABASE {url.database} TO {restricted_role}"),
        )
        # Real bats_db shape, confirmed live (2026-09-03): `fledermap` owns
        # the DATABASE itself (`pg_get_userbyid(datdba)` = 'fledermap'),
        # which -- Postgres 15+'s `pg_database_owner` pseudo-role -- makes
        # it transitively own `public` too, with no separate `ALTER SCHEMA`
        # needed. Without this, `--clean`'s `COMMENT ON SCHEMA public`
        # spuriously fails here in a way it never does against the real
        # deployment.
        conn.execute(text(f"ALTER DATABASE {url.database} OWNER TO {restricted_role}"))
        conn.execute(text(f"GRANT USAGE, CREATE ON SCHEMA public TO {restricted_role}"))
        conn.execute(
            text(f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {restricted_role}"),
        )
        conn.execute(
            text(
                f"GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO {restricted_role}"
            ),
        )
        # The postgis/postgis Docker image (unlike real bats_db, which has
        # only bare `postgis`) bundles the full extension family --
        # postgis_tiger_geocoder/postgis_topology and their tiger/
        # tiger_data/topology schemas. A role can read (dump) these
        # without owning (dropping/recreating) them, same split as
        # `postgis` itself -- so read access is granted here to let the
        # dump step succeed; ownership/DROP EXTENSION rights are
        # deliberately never granted, since that's the real gap this test
        # exists to reproduce.
        for schema in ("tiger", "tiger_data", "topology"):
            conn.execute(text(f"GRANT USAGE ON SCHEMA {schema} TO {restricted_role}"))
            conn.execute(
                text(
                    f"GRANT SELECT ON ALL TABLES IN SCHEMA {schema} "
                    f"TO {restricted_role}",
                ),
            )
        conn.execute(text("DROP TABLE IF EXISTS db_backup_smoke"))
        conn.execute(text("CREATE TABLE db_backup_smoke (value text)"))
        conn.execute(text(f"ALTER TABLE db_backup_smoke OWNER TO {restricted_role}"))
        conn.execute(
            text("INSERT INTO db_backup_smoke (value) VALUES ('before-backup')"),
        )
        # Real production shape: `fledermap`'s role owns everything it
        # migrated into existence itself (`recording`, `site`, ... via
        # `engine`'s own `create_all`, owned here by `postgis_url`'s admin
        # role since that's what ran it) -- transfer that ownership so
        # `--clean`'s DROP/ALTER TABLE statements for OUR OWN tables don't
        # spuriously fail too, leaving only postgis genuinely foreign-owned
        # (excluding `spatial_ref_sys`, which stays with the extension).
        conn.execute(
            text(
                f"""
                DO $$
                DECLARE r RECORD;
                BEGIN
                  FOR r IN SELECT tablename FROM pg_tables
                    WHERE schemaname = 'public' AND tablename <> 'spatial_ref_sys'
                  LOOP
                    EXECUTE format(
                      'ALTER TABLE public.%I OWNER TO {restricted_role}', r.tablename
                    );
                  END LOOP;
                  FOR r IN SELECT sequencename FROM pg_sequences
                    WHERE schemaname = 'public'
                  LOOP
                    EXECUTE format(
                      'ALTER SEQUENCE public.%I OWNER TO {restricted_role}',
                      r.sequencename
                    );
                  END LOOP;
                END $$;
                """,
            ),
        )
        # postgis extension (and spatial_ref_sys) stays owned by
        # `postgis_url`'s own role -- the real-deployment shape this test
        # exists to reproduce.

    try:
        restricted_url = url.set(
            username=restricted_role,
            password=restricted_password,
        ).render_as_string(hide_password=False)

        env = _run_script_env(restricted_url, tmp_path)
        backup = subprocess.run(
            [str(_BACKUP_SCRIPT)],
            cwd=_REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert backup.returncode == 0, backup.stderr

        dump_dir = Path(env["DB_BACKUP_DIR"])
        dumps = sorted(
            dump_dir.glob("bats_db-*.dump"),
            key=lambda p: p.stat().st_mtime,
        )
        assert dumps, backup.stdout
        dump_file = dumps[-1]

        restore = subprocess.run(
            [str(_RESTORE_SCRIPT), "--dangerously-restore", str(dump_file)],
            cwd=_REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert restore.returncode == 0, restore.stderr
        assert "must be owner of extension" not in restore.stderr
        assert "permission denied" not in restore.stderr

        dump_file.unlink()
    finally:
        with engine.begin() as conn:
            # Reclaim DB ownership (superuser can always do this,
            # regardless of current owner) before DROP OWNED BY/DROP
            # ROLE, which otherwise fail while the role still owns the
            # database itself.
            conn.execute(
                text(f"ALTER DATABASE {url.database} OWNER TO {url.username}"),
            )
            conn.execute(text(f"DROP OWNED BY {restricted_role}"))
            conn.execute(text(f"DROP ROLE IF EXISTS {restricted_role}"))


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
