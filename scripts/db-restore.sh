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
pg_restore --clean --if-exists --no-owner --dbname="$DATABASE_URL" "$DUMP_FILE"
echo "Done."
