from __future__ import annotations

from datetime import UTC, datetime

import pytest
from geoalchemy2.elements import WKTElement
from sqlalchemy import Engine, delete, func, select
from sqlalchemy.orm import Session as OrmSession

from fledermap.store.models import SpeciesRange, SpeciesRangeFetch, Taxon

pytestmark = pytest.mark.db

SQUARE = "MULTIPOLYGON(((0 0, 0 1, 1 1, 1 0, 0 0)))"


def _taxon(session: OrmSession) -> Taxon:
    taxon = Taxon(rank="species", scientific_name="Pipistrellus pipistrellus")
    session.add(taxon)
    session.flush()
    return taxon


def test_species_range_round_trips(engine: Engine) -> None:
    with OrmSession(engine) as session:
        taxon = _taxon(session)
        session.add(
            SpeciesRange(
                taxon_id=taxon.id,
                presence=1,
                origin=1,
                geom=WKTElement(SQUARE, srid=4326),
            ),
        )
        session.commit()
        row = session.scalars(select(SpeciesRange)).one()
        assert (row.presence, row.origin) == (1, 1)
        area = session.scalar(select(func.ST_Area(SpeciesRange.geom)))
        assert area is not None
        assert area > 0


def test_deleting_a_taxon_cascades_to_its_ranges_and_fetch_row(
    engine: Engine,
) -> None:
    with OrmSession(engine) as session:
        taxon = _taxon(session)
        session.add(
            SpeciesRange(
                taxon_id=taxon.id,
                presence=1,
                origin=1,
                geom=WKTElement(SQUARE, srid=4326),
            ),
        )
        session.add(
            SpeciesRangeFetch(
                taxon_id=taxon.id,
                query_name="Pipistrellus pipistrellus",
                status="found",
                source_version="2022-2",
                fetched_at=datetime(2026, 10, 4, tzinfo=UTC),
            ),
        )
        session.commit()

        session.execute(delete(Taxon))
        session.commit()

        assert session.scalars(select(SpeciesRange)).all() == []
        assert session.scalars(select(SpeciesRangeFetch)).all() == []
