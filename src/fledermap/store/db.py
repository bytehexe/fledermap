"""Engine and session construction.

WARNING: the URL here must never point at poiidx's database. poiidx hashes its
own schema and filter config on init and DROPS AND RECREATES ALL TABLES on any
mismatch, which would destroy Fledermap's data. Fledermap uses `bats_db`;
poiidx uses `poiidx_bats_db`. They are separate databases by design (spec D11).
"""

from __future__ import annotations

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import sessionmaker

from fledermap.store.models import Base


def make_engine(url: str, *, echo: bool = False) -> Engine:
    """Create an engine for Fledermap's own database."""
    return create_engine(url, echo=echo, future=True)


def session_factory(engine: Engine) -> sessionmaker:
    return sessionmaker(bind=engine, expire_on_commit=False)


def create_all(engine: Engine) -> None:
    """Test and development convenience only; production schema comes from Alembic."""
    Base.metadata.create_all(engine)
