from __future__ import annotations

import procrastinate
import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from fledermap.jobs.app import ensure_schema, make_job_app, make_worker_connector

pytestmark = pytest.mark.db


def test_make_job_app_constructs_without_an_engine() -> None:
    app = make_job_app()

    assert app is not None  # constructed without raising; not yet opened


def test_ensure_schema_creates_the_procrastinate_tables(engine: Engine) -> None:
    app = make_job_app()
    app.open(engine)

    ensure_schema(app, engine)

    with engine.connect() as conn:
        exists = conn.execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                "WHERE table_name = 'procrastinate_jobs')",
            ),
        ).scalar()
    assert exists is True


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
