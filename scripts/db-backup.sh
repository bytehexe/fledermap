#!/usr/bin/env bash
set -euo pipefail

# Dev-only backup of the fledermap Postgres database via pg_dump, into a
# gitignored .db-backups/ dir at the repo root. Not part of the shipped
# package (scripts/ never ships in the wheel -- see CLAUDE.md's "scripts/"
# bullet). We're developing against live data (CLAUDE.md's Obsidian backlog
# item), so this must be run -- by hand, or by Claude -- before any schema
# change/migration or other operation that risks data loss. See
# db-restore.sh to restore a dump this writes.
#
# Resolves the database URL the same way the `fledermap` CLI does --
# FLEDERMAP_DATABASE_URL env var, falling back to the TOML config file --
# via `Config.from_env()`, rather than re-parsing that precedence here.
# Everything after that is the standard `pg_dump` tool, no project code.
#
# `Config.database_url` may carry SQLAlchemy's `+psycopg2` dialect suffix
# (e.g. from a `postgresql+psycopg2://...` config value) -- `pg_dump`
# speaks plain libpq URIs and silently falls back to a local socket
# connection if handed that suffix instead of erroring, so it's stripped
# here the same way `jobs/app.py`'s `_worker_conninfo` already does for
# psycopg's own async connector.

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Overridable so tests/test_db_backup_restore.py can point this at an
# isolated tmp_path instead of the real backup directory -- a test run
# must never leave its throwaway dumps mixed in with real ones.
BACKUP_DIR="${DB_BACKUP_DIR:-$REPO_ROOT/.db-backups}"

DATABASE_URL="$(cd "$REPO_ROOT" && hatch run python -c \
  "from fledermap.config import Config
from sqlalchemy.engine import make_url
url = make_url(Config.from_env().database_url).set(drivername='postgresql')
print(url.render_as_string(hide_password=False))")"

mkdir -p "$BACKUP_DIR"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT_FILE="$BACKUP_DIR/bats_db-$TIMESTAMP.dump"

echo "Backing up $DATABASE_URL -> $OUT_FILE"
pg_dump -Fc "$DATABASE_URL" -f "$OUT_FILE"
echo "Done: $OUT_FILE"
