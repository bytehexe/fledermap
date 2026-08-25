"""Procrastinate App construction, idempotent schema setup, and the worker's
connector.

One `App` per process (design spec §6): `jobs/tasks.py`'s module-level `app`
(built here, via `make_job_app`) is shared by BOTH defer-side code
(`SQLAlchemyPsycopg2Connector`, opened against this project's own SQLAlchemy
engine -- no second connection pool for that side) and the worker, which
swaps in `make_worker_connector(...)` only for the duration of `run_worker`
by using `replace_connector` as a CONTEXT MANAGER, not a bare call --
`replace_connector` is `@contextlib.contextmanager`, so its connector-swap
only happens on `__enter__` and is undone on exit:

    with app.replace_connector(make_worker_connector(database_url)) as worker_app:
        worker_app.run_worker(...)

A bare `app.replace_connector(make_worker_connector(...))` statement (no
`with`) constructs the generator and immediately discards it -- the swap
never runs, silently. `replace_connector` also does not open or connect the
connector it's given; the connector passed in (here, a `PsycopgConnector`
constructed with `conninfo=...`) must already be ready to use as
constructed, same as `SQLAlchemyPsycopg2Connector` is opened separately via
`app.open(engine)` on the defer side. Tasks are bound to the App object
they're declared against, not to a specific connector, which is what makes
sharing one App across both roles possible.

Two things below were CONFIRMED, not assumed, against a real Postgres 16
container before this plan was written (design spec §2 has the full
investigation):

1. `app.schema_manager.apply_schema()` -- Procrastinate's own documented
   schema-apply method -- fails against a real database with
   `psycopg2.errors.SyntaxError: too many parameters specified for RAISE`.
   Root cause: `apply_schema()` runs `schema_sql.replace("%", "%%")` before
   executing, which corrupts the schema's own legitimate
   `RAISE '...(job id: %)', job_id` statements (PL/pgSQL's own, unrelated use
   of `%` as a format placeholder). The fix below executes the UNESCAPED
   schema via a raw psycopg2 cursor with NO params argument at all --
   confirmed this is the one call shape that avoids both the escaping bug
   AND a second, different failure from SQLAlchemy's own `exec_driver_sql`
   (which still implicitly supplies an empty params structure that
   re-triggers `%`-parsing).
2. `run_worker()` requires an async-capable connector.
   `SQLAlchemyPsycopg2Connector` raises `SyncConnectorConfigurationError` if
   you try. `make_worker_connector` returns a `PsycopgConnector` (psycopg 3)
   for exactly this reason.
"""

from __future__ import annotations

import procrastinate
from procrastinate.contrib.sqlalchemy import SQLAlchemyPsycopg2Connector
from sqlalchemy import text
from sqlalchemy.engine import Engine, make_url


def make_job_app() -> procrastinate.App:
    """Construct the App with its connector, but do NOT open it against an
    engine yet -- mirrors `SQLAlchemyPsycopg2Connector()`'s own documented
    pattern (constructed with no DSN/engine, opened separately once the real
    one is known). Every caller must either `app.open(engine)` (defer-side)
    or, worker-side, use `replace_connector` as a context manager (it is
    `@contextlib.contextmanager`; a bare call without `with` never swaps the
    connector):

        with app.replace_connector(make_worker_connector(database_url)) as worker_app:
            worker_app.run_worker(...)

    `replace_connector` does not open/connect the connector it's given --
    pass it one that is already ready to use as constructed, e.g.
    `make_worker_connector`'s `PsycopgConnector(conninfo=...)`."""
    return procrastinate.App(connector=SQLAlchemyPsycopg2Connector())


def ensure_schema(app: procrastinate.App, engine: Engine) -> None:
    """Create Procrastinate's schema if it doesn't already exist. Idempotent
    (safe to call on every startup, matching `_run_migrations`'s own
    property) -- Procrastinate's own apply methods are NOT idempotent by
    themselves. Uses `engine` directly for both the existence check and the
    actual apply, independent of whatever connector `app` currently has --
    `app` is only used here to read the schema text via `app.schema_manager`.

    APPLY only -- there is no upgrade path here. If the schema already
    exists this returns immediately without checking which Procrastinate
    version wrote it, so upgrading the `procrastinate` dependency requires
    running Procrastinate's own migrations against existing databases by
    hand. That is why `pyproject.toml` pins the dependency to a minor range.
    """
    with engine.connect() as conn:
        already_applied = conn.execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                "WHERE table_name = 'procrastinate_jobs')",
            ),
        ).scalar()
    if already_applied:
        return

    schema_sql = app.schema_manager.get_schema()
    raw_conn = engine.raw_connection()
    try:
        cursor = raw_conn.cursor()
        cursor.execute(schema_sql)  # NO params argument -- see module docstring
        raw_conn.commit()
    finally:
        raw_conn.close()


def _worker_conninfo(database_url: str) -> str:
    """Normalise a database URL into a libpq conninfo string.

    Two independent reasons this cannot just pass `database_url` through:

    1. `PsycopgConnector.conninfo` is a libpq DSN/URI. libpq understands
       plain `postgresql://` but NOT SQLAlchemy's `+driver` dialect syntax,
       and a `postgresql+psycopg2://...` URL really does reach here --
       `testcontainers`' `get_connection_url()` returns that shape and
       `config.py` never rewrites what it is handed. Unstripped, `psycopg`
       fails with `missing "=" after "postgresql+psycopg2://..." in
       connection info string`. SQLAlchemy's own `create_engine()` accepts
       the suffix happily, so the mismatch is invisible everywhere else
       `database_url` is used. `.set(drivername="postgresql")` normalises
       either shape; a bare `postgresql://...` URL round-trips unchanged.
    2. `str(URL)` masks the password as the literal `***`. Without
       `hide_password=False` every worker would authenticate with that
       literal string and fail.
    """
    return (
        make_url(database_url)
        .set(drivername="postgresql")
        .render_as_string(hide_password=False)
    )


def make_worker_connector(database_url: str) -> procrastinate.PsycopgConnector:
    """An async-capable connector for running the worker -- see module
    docstring point 2. `database_url` is normalised by `_worker_conninfo`."""
    return procrastinate.PsycopgConnector(conninfo=_worker_conninfo(database_url))
