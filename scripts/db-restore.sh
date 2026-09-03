#!/usr/bin/env bash
set -euo pipefail

# Dev-only restore of a fledermap Postgres database dump made by
# db-backup.sh, via pg_restore. DESTRUCTIVE: replaces the target database's
# contents. Not part of the shipped package -- see CLAUDE.md's "scripts/"
# bullet, and its rule that Claude must ask for explicit human approval
# before ever invoking this with --dangerously-restore -- narrating the
# intent is not asking.
#
# No interactive confirmation prompt, on purpose: a TTY prompt is not a
# real gate against an agent that can pipe an answer straight past it. The
# actual gate is this required flag (named after this harness's own
# `dangerouslyDisableSandbox` convention) plus the CLAUDE.md rule above.

usage() {
  echo "Usage: $0 --dangerously-restore <dump-file>" >&2
  exit 1
}

if [ "$#" -ne 2 ] || [ "$1" != "--dangerously-restore" ]; then
  usage
fi
DUMP_FILE="$2"

if [ ! -f "$DUMP_FILE" ]; then
  echo "No such dump file: $DUMP_FILE" >&2
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# See db-backup.sh's matching comment: strip SQLAlchemy's `+psycopg2`
# dialect suffix, which `pg_restore` (plain libpq) does not understand.
DATABASE_URL="$(cd "$REPO_ROOT" && hatch run python -c \
  "from fledermap.config import Config
from sqlalchemy.engine import make_url
url = make_url(Config.from_env().database_url).set(drivername='postgresql')
print(url.render_as_string(hide_password=False))")"

echo "Restoring $DUMP_FILE -> $DATABASE_URL"
echo "THIS REPLACES THE CURRENT CONTENTS OF THAT DATABASE."

# Confirmed live against a real bats_db (2026-09-03): pg_dump's --clean
# includes DROP EXTENSION IF EXISTS postgis (and, on a fuller postgis
# install, its sibling extensions/schemas -- fuzzystrmatch,
# postgis_tiger_geocoder, postgis_topology, the tiger/tiger_data/topology
# schemas -- plus data tables those own, e.g. spatial_ref_sys), but the
# role this connects as is not the extension owner -- only whoever ran
# `CREATE EXTENSION postgis` (superuser, once, at DB setup) can drop it.
# Restoring hits `must be owner of extension postgis` / `permission
# denied for table spatial_ref_sys` (and the tiger/topology equivalents,
# reproduced against a real Postgres in
# tests/test_db_backup_restore.py). None of this is "our data" -- it's
# infrastructure fledermap's own migration already guarantees exists on
# any target (`CREATE EXTENSION IF NOT EXISTS postgis`, 0001_initial.py)
# -- so it's excluded from the restore via a filtered TOC list rather
# than restored (pg_dump has no `--exclude-extension` flag to skip it at
# dump time instead). Matched by keyword rather than parsing the TOC
# list's columns: safe here because none of fledermap's own schema
# objects happen to share these names.
#
# Also exclude every `COMMENT - SCHEMA` entry (the `public` one
# included): Postgres 15+ owns a fresh `public` schema via the dynamic
# `pg_database_owner` pseudo-role, which tracks whoever owns the
# database -- but `pg_dump` still records whatever role literally set the
# comment, so restoring it as a *different* role than that (plausible any
# time DB ownership was reassigned, e.g. after a migration to a new
# hosting setup) fails with `must be owner of schema public`. Comments
# are cosmetic metadata only, never read by application code, so this is
# always safe to skip rather than chase every ownership edge case that
# could produce it.
TOC_LIST="$(mktemp)"
trap 'rm -f "$TOC_LIST"' EXIT
pg_restore --list "$DUMP_FILE" \
  | grep -viE '\bEXTENSION\b|\btiger\b|\btiger_data\b|\btopology\b|\bfuzzystrmatch\b|\bspatial_ref_sys\b|\bgeometry_columns\b|\bgeography_columns\b|COMMENT - SCHEMA' \
  > "$TOC_LIST"

pg_restore --clean --if-exists --no-owner -L "$TOC_LIST" \
  --dbname="$DATABASE_URL" "$DUMP_FILE"
echo "Done."
