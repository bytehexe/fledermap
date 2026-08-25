from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, text
from testcontainers.community.postgres import PostgresContainer

from fledermap.store import db
from fledermap.store.db import make_engine
from fledermap.store.models import Base


@pytest.fixture(scope="session")
def postgis_url() -> Iterator[str]:
    """A throwaway PostGIS instance. Mirrors poiidx's testing approach."""
    with PostgresContainer("postgis/postgis:16-3.4") as container:
        yield container.get_connection_url()


@pytest.fixture
def engine(postgis_url: str) -> Iterator[Engine]:
    """A per-test-clean database inside the shared session-scoped container.

    The Procrastinate reset below is NOT covered by `drop_all`/`create_all`:
    those only know this project's own ORM tables, while Procrastinate's
    schema is applied by `jobs.app.ensure_schema`, which is idempotent by
    design and so applies once per session and never again. Without an
    explicit reset, deferred jobs outlive the test that made them -- a test
    that never runs a worker leaves its rows `todo` forever, and the next
    test deferring the same `queueing_lock` dies with `AlreadyEnqueued`.
    Lock keys are derived from `audio_hash`, and independent test modules
    reach for the same short literal hashes, so this fires purely on test
    ORDER (pytest-randomly picks it) with nothing wrong in the code under
    test. The existence guard is needed because most tests never touch the
    queue, so `ensure_schema` may not have run in this container yet.
    """
    eng = make_engine(postgis_url)
    with eng.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        queue_present = conn.execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                "WHERE table_name = 'procrastinate_jobs')",
            ),
        ).scalar()
        if queue_present:
            conn.execute(text("DELETE FROM procrastinate_jobs"))
    Base.metadata.drop_all(eng)
    db.create_all(eng)
    yield eng
    eng.dispose()
