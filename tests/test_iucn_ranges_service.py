from __future__ import annotations

from datetime import UTC, datetime

import pytest
from geoalchemy2 import Geometry
from geoalchemy2.elements import WKTElement
from sqlalchemy import Engine, cast, func, select
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import IdSource, Verdict
from fledermap.domain.ranges import RANGE_CODE_SOURCE
from fledermap.services import iucn_ranges
from fledermap.services.iucn_ranges import RangePolygon
from fledermap.store.models import (
    ClassifierDetection,
    Identification,
    Recording,
    SpeciesRange,
    SpeciesRangeFetch,
    Taxon,
    TaxonCode,
)

pytestmark = pytest.mark.db

SQUARE_WKT = "MULTIPOLYGON(((0 0, 0 1, 1 1, 1 0, 0 0)))"


def _taxon(
    session: OrmSession,
    name: str = "Pipistrellus pipistrellus",
    *,
    code: str | None = "Pipistrellus pipistrellus",
    rank: str = "species",
) -> Taxon:
    taxon = Taxon(rank=rank, scientific_name=name)
    session.add(taxon)
    session.flush()
    if code is not None:
        session.add(TaxonCode(source=RANGE_CODE_SOURCE, code=code, taxon_id=taxon.id))
    session.flush()
    return taxon


def _recording(
    session: OrmSession,
    audio_hash: str = "a" * 64,
    lon: float | None = 0.5,
    lat: float | None = 0.5,
) -> Recording:
    recording = Recording(
        audio_hash=audio_hash,
        path=f"{audio_hash}.wav",
        recorded_at=datetime(2026, 10, 4, tzinfo=UTC),
        geom=None if lon is None else WKTElement(f"POINT({lon} {lat})", srid=4326),
    )
    session.add(recording)
    session.flush()
    return recording


def _identify(session: OrmSession, recording: Recording, taxon: Taxon) -> None:
    session.add(
        Identification(
            recording_id=recording.id,
            source=IdSource.BATDETECT2,
            verdict=Verdict.SPECIES,
            taxon_id=taxon.id,
            first_seen_at=datetime(2026, 10, 4, tzinfo=UTC),
        ),
    )
    session.flush()


def test_a_species_that_occurs_in_an_identification_is_pending(engine: Engine) -> None:
    with OrmSession(engine) as session:
        taxon = _taxon(session)
        _identify(session, _recording(session), taxon)
        session.commit()

        assert iucn_ranges.pending_taxa(session) == [
            (taxon.id, "Pipistrellus pipistrellus"),
        ]


def test_a_species_seen_only_by_a_classifier_detection_is_pending(
    engine: Engine,
) -> None:
    with OrmSession(engine) as session:
        taxon = _taxon(session)
        recording = _recording(session)
        session.add(
            ClassifierDetection(
                recording_id=recording.id,
                source=IdSource.BATTYBIRDNET,
                taxon_id=taxon.id,
                raw_label="Pipistrellus pipistrellus",
                confidence=0.4,
            ),
        )
        session.commit()

        assert [t for t, _ in iucn_ranges.pending_taxa(session)] == [taxon.id]


def test_a_species_never_seen_is_not_pending(engine: Engine) -> None:
    with OrmSession(engine) as session:
        _taxon(session)
        session.commit()

        assert iucn_ranges.pending_taxa(session) == []


def test_a_species_without_an_iucn_code_or_a_group_taxon_is_not_pending(
    engine: Engine,
) -> None:
    with OrmSession(engine) as session:
        recording = _recording(session)
        no_code = _taxon(session, "Plecotus auritus", code=None)
        group = _taxon(session, "Myotis", code="Myotis", rank="genus")
        _identify(session, recording, no_code)
        session.add(
            Identification(
                recording_id=recording.id,
                source=IdSource.EMT_GUANO,
                verdict=Verdict.SPECIES,
                taxon_id=group.id,
                first_seen_at=datetime(2026, 10, 4, tzinfo=UTC),
            ),
        )
        session.commit()

        assert iucn_ranges.pending_taxa(session) == []


def test_store_range_replaces_polygons_and_records_the_fetch(engine: Engine) -> None:
    with OrmSession(engine) as session:
        taxon = _taxon(session)
        polygon = RangePolygon(presence=1, origin=1, wkt=SQUARE_WKT)
        iucn_ranges.store_range(
            session, taxon.id, "Pipistrellus pipistrellus", [polygon]
        )
        iucn_ranges.store_range(
            session,
            taxon.id,
            "Pipistrellus pipistrellus",
            [polygon, RangePolygon(presence=2, origin=4, wkt=SQUARE_WKT)],
        )
        session.commit()

        assert session.scalar(select(func.count()).select_from(SpeciesRange)) == 2
        fetch_row = session.get(SpeciesRangeFetch, taxon.id)
        assert fetch_row is not None
        assert fetch_row.status == "found"
        assert fetch_row.source_version == "2022-2"


def test_store_range_with_no_polygons_records_not_found(engine: Engine) -> None:
    with OrmSession(engine) as session:
        taxon = _taxon(session)
        iucn_ranges.store_range(session, taxon.id, "Pipistrellus pipistrellus", [])
        session.commit()

        fetch_row = session.get(SpeciesRangeFetch, taxon.id)
        assert fetch_row is not None
        assert fetch_row.status == "not_found"
        assert session.scalar(select(func.count()).select_from(SpeciesRange)) == 0


def test_an_invalid_self_intersecting_polygon_is_repaired_not_rejected(
    engine: Engine,
) -> None:
    bow_tie = "MULTIPOLYGON(((0 0, 1 1, 1 0, 0 1, 0 0)))"
    with OrmSession(engine) as session:
        taxon = _taxon(session)
        iucn_ranges.store_range(
            session,
            taxon.id,
            "Pipistrellus pipistrellus",
            [RangePolygon(presence=1, origin=1, wkt=bow_tie)],
        )
        session.commit()

        # ST_IsValid exists for geometry only, so the geography column is cast for the check.
        valid = session.scalar(
            select(func.ST_IsValid(cast(SpeciesRange.geom, Geometry))),
        )
        assert valid is True


def test_not_found_is_never_fetched_again_but_a_changed_code_is(engine: Engine) -> None:
    with OrmSession(engine) as session:
        taxon = _taxon(session)
        _identify(session, _recording(session), taxon)
        iucn_ranges.store_range(session, taxon.id, "Pipistrellus pipistrellus", [])
        session.commit()
        assert iucn_ranges.pending_taxa(session) == []

        code = session.scalars(select(TaxonCode)).one()
        code.code = "Pipistrellus pipistrellus s.str."
        session.commit()

        assert iucn_ranges.pending_taxa(session) == [
            (taxon.id, "Pipistrellus pipistrellus s.str."),
        ]


def test_fetch_pending_fetches_stores_and_commits_each_species(engine: Engine) -> None:
    asked: list[str] = []

    def fake_fetch(name: str) -> list[RangePolygon]:
        asked.append(name)
        return [RangePolygon(presence=1, origin=1, wkt=SQUARE_WKT)]

    with OrmSession(engine) as session:
        a = _taxon(session, "Pipistrellus pipistrellus")
        b = _taxon(session, "Nyctalus noctula", code="Nyctalus noctula")
        recording = _recording(session)
        _identify(session, recording, a)
        _identify(session, recording, b)
        session.commit()

        assert iucn_ranges.fetch_pending(session, fetch=fake_fetch) == 2
        assert iucn_ranges.fetch_pending(session, fetch=fake_fetch) == 0

    assert sorted(asked) == ["Nyctalus noctula", "Pipistrellus pipistrellus"]


def test_a_failure_on_a_later_species_keeps_the_earlier_ones(engine: Engine) -> None:
    def flaky_fetch(name: str) -> list[RangePolygon]:
        if name == "Nyctalus noctula":
            msg = "boom"
            raise RuntimeError(msg)
        return [RangePolygon(presence=1, origin=1, wkt=SQUARE_WKT)]

    with OrmSession(engine) as session:
        a = _taxon(session, "Pipistrellus pipistrellus")
        b = _taxon(session, "Nyctalus noctula", code="Nyctalus noctula")
        recording = _recording(session)
        _identify(session, recording, a)
        _identify(session, recording, b)
        session.commit()
        # Ids read now: after the failed fetch the session is closed and `a`/`b` are expired.
        a_id, b_id = a.id, b.id

        with pytest.raises(RuntimeError, match="boom"):
            iucn_ranges.fetch_pending(session, fetch=flaky_fetch)

    with OrmSession(engine) as fresh:
        assert fresh.get(SpeciesRangeFetch, a_id) is not None
        assert fresh.get(SpeciesRangeFetch, b_id) is None
