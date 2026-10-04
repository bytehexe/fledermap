from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import procrastinate
import pytest
from geoalchemy2 import Geometry
from geoalchemy2.elements import WKTElement
from sqlalchemy import Engine, func, select
from sqlalchemy import cast as sa_cast
from sqlalchemy.orm import Session as OrmSession

from fledermap.config import Config
from fledermap.domain.codes import IdSource, Verdict
from fledermap.domain.ranges import RANGE_CODE_SOURCE, NoDataReason, RangeKind
from fledermap.jobs.app import ensure_schema
from fledermap.jobs.tasks import app as jobs_app
from fledermap.jobs.tasks import fetch_iucn_ranges_task
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
            select(func.ST_IsValid(sa_cast(SpeciesRange.geom, Geometry))),
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


NATIVE_SQUARE = RangePolygon(presence=1, origin=1, wkt=SQUARE_WKT)
VAGRANT_SQUARE = RangePolygon(
    presence=1,
    origin=4,
    wkt="MULTIPOLYGON(((10 0, 10 1, 11 1, 11 0, 10 0)))",
)


def _status(
    session: OrmSession,
    taxon: Taxon,
    recording: Recording,
    edge_km: float = 100.0,
) -> iucn_ranges.RangeStatus:
    return iucn_ranges.range_status(session, taxon.id, recording, edge_km=edge_km)


def _with_range(
    session: OrmSession,
    polygons: list[RangePolygon],
    *,
    lon: float | None,
    lat: float | None = 0.5,
) -> tuple[Taxon, Recording]:
    taxon = _taxon(session)
    recording = _recording(session, lon=lon, lat=lat)
    iucn_ranges.store_range(session, taxon.id, "Pipistrellus pipistrellus", polygons)
    session.commit()
    return taxon, recording


def test_a_point_inside_the_range_is_inside(engine: Engine) -> None:
    with OrmSession(engine) as session:
        taxon, recording = _with_range(session, [NATIVE_SQUARE], lon=0.5)

        status = _status(session, taxon, recording)

    assert status.kind is RangeKind.INSIDE


def test_a_point_just_outside_is_near_with_its_distance(engine: Engine) -> None:
    with OrmSession(engine) as session:
        taxon, recording = _with_range(session, [NATIVE_SQUARE], lon=1.5)

        status = _status(session, taxon, recording)

    assert status.kind is RangeKind.NEAR
    assert status.distance_km is not None
    assert 50 < status.distance_km < 60


def test_a_point_beyond_the_edge_distance_is_far(engine: Engine) -> None:
    with OrmSession(engine) as session:
        taxon, recording = _with_range(session, [NATIVE_SQUARE], lon=3.0)

        far = _status(session, taxon, recording)
        generous = _status(session, taxon, recording, edge_km=500.0)

    assert far.kind is RangeKind.FAR
    assert far.distance_km is not None
    assert far.distance_km > 200
    assert generous.kind is RangeKind.NEAR


def test_a_point_in_a_hole_of_the_range_is_outside_not_inside(engine: Engine) -> None:
    donut = RangePolygon(
        presence=1,
        origin=1,
        wkt="MULTIPOLYGON(((0 0, 0 3, 3 3, 3 0, 0 0), (1 1, 2 1, 2 2, 1 2, 1 1)))",
    )
    with OrmSession(engine) as session:
        taxon, recording = _with_range(session, [donut], lon=1.5, lat=1.5)

        status = _status(session, taxon, recording)

    assert status.kind is RangeKind.NEAR


def test_a_point_only_inside_a_vagrant_polygon_is_near_not_inside(
    engine: Engine,
) -> None:
    with OrmSession(engine) as session:
        taxon, recording = _with_range(
            session,
            [NATIVE_SQUARE, VAGRANT_SQUARE],
            lon=10.5,
        )

        status = _status(session, taxon, recording)

    assert status.kind is RangeKind.NEAR
    assert status.vagrant is True


def test_a_species_with_only_vagrant_polygons_is_near_inside_one_and_far_away(
    engine: Engine,
) -> None:
    with OrmSession(engine) as session:
        taxon, inside = _with_range(session, [VAGRANT_SQUARE], lon=10.5)
        elsewhere = _recording(session, "b" * 64, lon=50.0, lat=0.5)
        session.commit()

        in_vagrant = _status(session, taxon, inside)
        far_away = _status(session, taxon, elsewhere)

    assert in_vagrant.kind is RangeKind.NEAR
    assert in_vagrant.vagrant is True
    assert far_away.kind is RangeKind.FAR


def test_a_recording_without_a_location_has_no_data(engine: Engine) -> None:
    with OrmSession(engine) as session:
        taxon, recording = _with_range(session, [NATIVE_SQUARE], lon=None)

        status = _status(session, taxon, recording)

    assert status.kind is RangeKind.NO_DATA
    assert status.reason is NoDataReason.NO_LOCATION


def test_a_taxon_without_an_iucn_code_has_no_data(engine: Engine) -> None:
    with OrmSession(engine) as session:
        taxon = _taxon(session, "Plecotus auritus", code=None)
        recording = _recording(session)
        session.commit()

        status = _status(session, taxon, recording)

    assert status.reason is NoDataReason.NO_CODE


def test_a_taxon_not_fetched_yet_has_no_data(engine: Engine) -> None:
    with OrmSession(engine) as session:
        taxon = _taxon(session)
        recording = _recording(session)
        session.commit()

        status = _status(session, taxon, recording)

    assert status.reason is NoDataReason.NOT_FETCHED


def test_a_species_the_service_does_not_know_has_no_data(engine: Engine) -> None:
    with OrmSession(engine) as session:
        taxon, recording = _with_range(session, [], lon=0.5)

        status = _status(session, taxon, recording)

    assert status.reason is NoDataReason.NOT_FOUND


def test_a_fetch_under_an_outdated_code_counts_as_not_fetched(engine: Engine) -> None:
    with OrmSession(engine) as session:
        taxon, recording = _with_range(session, [NATIVE_SQUARE], lon=0.5)
        session.scalars(select(TaxonCode)).one().code = "Pipistrellus renamed"
        session.commit()

        status = _status(session, taxon, recording)

    assert status.reason is NoDataReason.NOT_FETCHED


def _task_context(
    engine: Engine,
    tmp_path: Path,
    *,
    enabled: bool,
) -> procrastinate.JobContext:
    config = Config(
        database_url="postgresql://unused/unused",
        archive_roots=(tmp_path / "archive",),
        media_root=tmp_path / "media",
        iucn_ranges_enabled=enabled,
    )
    return cast(
        "procrastinate.JobContext",
        SimpleNamespace(additional_context={"config": config, "engine": engine}),
    )


def test_enqueue_is_a_no_op_when_disabled(engine: Engine) -> None:
    with OrmSession(engine) as session:
        _identify(session, _recording(session), _taxon(session))
        session.commit()

        assert iucn_ranges.enqueue_range_fetch(session, engine, enabled=False) is False


def test_enqueue_is_a_no_op_when_nothing_is_pending(engine: Engine) -> None:
    jobs_app.open(engine)
    ensure_schema(jobs_app, engine)
    with OrmSession(engine) as session:
        assert iucn_ranges.enqueue_range_fetch(session, engine, enabled=True) is False


def test_enqueue_defers_one_job_and_dedupes_the_second(engine: Engine) -> None:
    jobs_app.open(engine)
    ensure_schema(jobs_app, engine)
    with OrmSession(engine) as session:
        _identify(session, _recording(session), _taxon(session))
        session.commit()

        assert iucn_ranges.enqueue_range_fetch(session, engine, enabled=True) is True
        assert iucn_ranges.enqueue_range_fetch(session, engine, enabled=True) is False


def test_the_task_does_nothing_when_the_flag_was_turned_off_after_deferral(
    engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    called: list[str] = []

    def spy(name: str, **_: object) -> list[RangePolygon]:
        called.append(name)
        return []

    monkeypatch.setattr(iucn_ranges, "fetch_range", spy)
    with OrmSession(engine) as session:
        _identify(session, _recording(session), _taxon(session))
        session.commit()

    fetch_iucn_ranges_task(_task_context(engine, tmp_path, enabled=False))

    assert called == []
    with OrmSession(engine) as fresh:
        assert fresh.scalars(select(SpeciesRangeFetch)).all() == []


def test_the_task_fetches_pending_species_and_commits(
    engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake(name: str, **_: object) -> list[RangePolygon]:
        return [RangePolygon(presence=1, origin=1, wkt=SQUARE_WKT)]

    monkeypatch.setattr(iucn_ranges, "fetch_range", fake)
    with OrmSession(engine) as session:
        taxon = _taxon(session)
        _identify(session, _recording(session), taxon)
        session.commit()
        taxon_id = taxon.id

    fetch_iucn_ranges_task(_task_context(engine, tmp_path, enabled=True))

    with OrmSession(engine) as fresh:
        assert fresh.get(SpeciesRangeFetch, taxon_id) is not None
        assert fresh.scalar(select(func.count()).select_from(SpeciesRange)) == 1
