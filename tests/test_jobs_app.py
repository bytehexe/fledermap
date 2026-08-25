from __future__ import annotations

from collections.abc import Iterator

import procrastinate
import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from fledermap.jobs.app import (
    _worker_conninfo,
    ensure_schema,
    make_job_app,
    make_worker_connector,
)
from fledermap.store.db import make_engine


@pytest.fixture
def blank_engine(postgis_url: str) -> Iterator[Engine]:
    """An engine on a genuinely empty schema -- no ORM tables, and crucially
    no Procrastinate schema either.

    The shared `engine` fixture cannot serve this: it empties
    `procrastinate_jobs` but leaves the Procrastinate SCHEMA in place, since
    `ensure_schema` applies it once per session by design. A test asserting
    that `ensure_schema` CREATES those tables is tautological against that --
    both the call and the assertion short-circuit on a schema some earlier
    test applied. Wiping `public` is the same approach
    `tests/test_cli.py`'s `clean_database_url` already takes, and the `engine`
    fixture rebuilds everything it needs (extension included) on its next use.
    """
    eng = make_engine(postgis_url)
    with eng.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    yield eng
    eng.dispose()


def test_make_job_app_constructs_without_an_engine() -> None:
    app = make_job_app()

    assert app is not None  # constructed without raising; not yet opened


@pytest.mark.db
def test_ensure_schema_creates_the_procrastinate_tables(blank_engine: Engine) -> None:
    app = make_job_app()
    app.open(blank_engine)

    with blank_engine.connect() as conn:
        before = conn.execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                "WHERE table_name = 'procrastinate_jobs')",
            ),
        ).scalar()
    assert before is False  # the fixture really did leave nothing behind

    ensure_schema(app, blank_engine)

    with blank_engine.connect() as conn:
        exists = conn.execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                "WHERE table_name = 'procrastinate_jobs')",
            ),
        ).scalar()
    assert exists is True


@pytest.mark.db
def test_ensure_schema_is_safe_to_call_twice(engine: Engine) -> None:
    """Procrastinate's own schema-apply is NOT idempotent by itself (it
    errors if already applied, confirmed against real Postgres) --
    ensure_schema must guard that, matching _run_migrations's own "safe to
    run every time" property."""
    app = make_job_app()
    app.open(engine)

    ensure_schema(app, engine)
    ensure_schema(app, engine)  # must not raise


def test_make_worker_connector_returns_an_async_connector() -> None:
    connector = make_worker_connector("postgresql://localhost/does_not_matter")

    assert isinstance(connector, procrastinate.PsycopgConnector)


def test_worker_conninfo_strips_the_sqlalchemy_driver_suffix() -> None:
    """libpq's conninfo parser rejects SQLAlchemy's `+driver` syntax, and
    `str(URL)` would mask the password as the literal `***` -- so this
    asserts BOTH the suffix going away and the password surviving intact."""
    assert (
        _worker_conninfo("postgresql+psycopg2://u:p@h:5432/d")
        == "postgresql://u:p@h:5432/d"
    )


def test_worker_conninfo_leaves_a_bare_url_unchanged() -> None:
    assert _worker_conninfo("postgresql://u:p@h:5432/d") == "postgresql://u:p@h:5432/d"
