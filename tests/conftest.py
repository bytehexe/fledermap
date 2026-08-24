from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, text
from testcontainers.community.postgres import PostgresContainer

from fledermap.store.db import make_engine
from fledermap.store.models import Base


@pytest.fixture(scope="session")
def postgis_url() -> Iterator[str]:
    """A throwaway PostGIS instance. Mirrors poiidx's testing approach."""
    with PostgresContainer("postgis/postgis:16-3.4") as container:
        yield container.get_connection_url()


@pytest.fixture
def engine(postgis_url: str) -> Iterator[Engine]:
    eng = make_engine(postgis_url)
    with eng.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()
