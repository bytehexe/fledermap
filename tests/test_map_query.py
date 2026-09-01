# tests/test_map_query.py
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from geoalchemy2.elements import WKTElement
from sqlalchemy import Engine
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import IdSource, Verdict
from fledermap.services.map_query import (
    filtered_recordings,
    filtered_sites,
    list_sessions,
    list_taxa,
    neighbor_recordings,
    site_detail,
)
from fledermap.store.models import Identification, Recording, Site, Taxon
from fledermap.store.models import Session as AnnotationSession

pytestmark = pytest.mark.db


def _recording(
    session: OrmSession,
    *,
    audio_hash: str,
    lon: float = 10.0,
    lat: float = 50.0,
    recorded_at: datetime = datetime(2026, 8, 25, tzinfo=UTC),
    verdict: Verdict | None = Verdict.SPECIES,
    taxon_id: int | None = None,
    source: IdSource = IdSource.EMT_GUANO,
    session_id: int | None = None,
    missing: bool = False,
) -> Recording:
    r = Recording(
        audio_hash=audio_hash,
        path=f"{audio_hash}.wav",
        recorded_at=recorded_at,
        geom=WKTElement(f"POINT({lon} {lat})", srid=4326),
        session_id=session_id,
        missing_since=datetime(2026, 8, 25, tzinfo=UTC) if missing else None,
    )
    session.add(r)
    session.flush()
    if verdict is not None:
        session.add(
            Identification(
                recording_id=r.id,
                source=source,
                verdict=verdict,
                taxon_id=taxon_id,
                first_seen_at=recorded_at,
            ),
        )
    session.flush()
    return r


def test_excludes_missing_recordings(engine: Engine) -> None:
    with OrmSession(engine) as session:
        _recording(session, audio_hash="a" * 64, missing=True)
        session.commit()

        results = filtered_recordings(session)

    assert results == []


def test_date_range_filters_recordings(engine: Engine) -> None:
    with OrmSession(engine) as session:
        early = _recording(
            session,
            audio_hash="a" * 64,
            recorded_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        _recording(
            session, audio_hash="b" * 64, recorded_at=datetime(2026, 8, 1, tzinfo=UTC)
        )
        session.commit()

        results = filtered_recordings(
            session,
            date_from=datetime(2026, 1, 1, tzinfo=UTC),
            date_to=datetime(2026, 2, 1, tzinfo=UTC),
        )

    assert [r.id for r in results] == [early.id]


def test_bbox_filters_by_the_recordings_current_position(engine: Engine) -> None:
    with OrmSession(engine) as session:
        inside = _recording(session, audio_hash="a" * 64, lon=10.0, lat=50.0)
        _recording(session, audio_hash="b" * 64, lon=100.0, lat=50.0)
        session.commit()

        results = filtered_recordings(session, bbox=(0.0, 40.0, 20.0, 60.0))

    assert [r.id for r in results] == [inside.id]


def test_default_verdict_excludes_noise_and_no_id(engine: Engine) -> None:
    with OrmSession(engine) as session:
        species = _recording(session, audio_hash="a" * 64, verdict=Verdict.SPECIES)
        _recording(session, audio_hash="b" * 64, verdict=Verdict.NOISE)
        _recording(session, audio_hash="c" * 64, verdict=Verdict.NO_ID)
        _recording(
            session, audio_hash="d" * 64, verdict=None
        )  # no identification at all
        session.commit()

        results = filtered_recordings(session)

    assert [r.id for r in results] == [species.id]


def test_verdict_all_includes_everything(engine: Engine) -> None:
    with OrmSession(engine) as session:
        _recording(session, audio_hash="a" * 64, verdict=Verdict.SPECIES)
        _recording(session, audio_hash="b" * 64, verdict=Verdict.NOISE)
        _recording(session, audio_hash="c" * 64, verdict=None)
        session.commit()

        results = filtered_recordings(session, verdict="all")

    assert len(results) == 3


def test_explicit_verdict_filters_to_only_that_verdict(engine: Engine) -> None:
    with OrmSession(engine) as session:
        noise = _recording(session, audio_hash="a" * 64, verdict=Verdict.NOISE)
        _recording(session, audio_hash="b" * 64, verdict=Verdict.SPECIES)
        session.commit()

        results = filtered_recordings(session, verdict=Verdict.NOISE)

    assert [r.id for r in results] == [noise.id]


def test_taxon_filters_by_current_best_taxon(engine: Engine) -> None:
    with OrmSession(engine) as session:
        wanted = Taxon(rank="species", scientific_name="Pipistrellus pipistrellus")
        other = Taxon(rank="species", scientific_name="Eptesicus serotinus")
        session.add_all([wanted, other])
        session.flush()
        matching = _recording(session, audio_hash="a" * 64, taxon_id=wanted.id)
        _recording(session, audio_hash="b" * 64, taxon_id=other.id)
        session.commit()

        results = filtered_recordings(session, taxon_id=wanted.id)

    assert [r.id for r in results] == [matching.id]


def test_taxon_exclude_keeps_everything_but_the_named_taxon(engine: Engine) -> None:
    with OrmSession(engine) as session:
        excluded = Taxon(rank="species", scientific_name="Pipistrellus pipistrellus")
        other = Taxon(rank="species", scientific_name="Eptesicus serotinus")
        session.add_all([excluded, other])
        session.flush()
        different_species = _recording(session, audio_hash="a" * 64, taxon_id=other.id)
        no_id = _recording(session, audio_hash="b" * 64, verdict=Verdict.NO_ID)
        _recording(session, audio_hash="c" * 64, taxon_id=excluded.id)
        session.commit()

        results = filtered_recordings(
            session,
            verdict="all",
            taxon_id=excluded.id,
            taxon_exclude=True,
        )

    assert {r.id for r in results} == {different_species.id, no_id.id}


def test_taxon_exclude_without_a_taxon_id_has_no_effect(engine: Engine) -> None:
    with OrmSession(engine) as session:
        taxon = Taxon(rank="species", scientific_name="Pipistrellus pipistrellus")
        session.add(taxon)
        session.flush()
        recording = _recording(session, audio_hash="a" * 64, taxon_id=taxon.id)
        session.commit()

        results = filtered_recordings(session, taxon_exclude=True)

    assert [r.id for r in results] == [recording.id]


def test_session_id_filters_recordings(engine: Engine) -> None:
    with OrmSession(engine) as session:
        wanted = AnnotationSession(
            started_at=datetime(2026, 1, 1, tzinfo=UTC),
            ended_at=datetime(2026, 1, 2, tzinfo=UTC),
        )
        other = AnnotationSession(
            started_at=datetime(2026, 1, 1, tzinfo=UTC),
            ended_at=datetime(2026, 1, 2, tzinfo=UTC),
        )
        session.add_all([wanted, other])
        session.flush()
        matching = _recording(session, audio_hash="a" * 64, session_id=wanted.id)
        _recording(session, audio_hash="b" * 64, session_id=other.id)
        session.commit()

        results = filtered_recordings(session, session_id=wanted.id)

    assert [r.id for r in results] == [matching.id]


def test_source_filters_by_a_non_superseded_identification_from_that_source(
    engine: Engine,
) -> None:
    with OrmSession(engine) as session:
        matching = _recording(session, audio_hash="a" * 64, source=IdSource.EMT_WAMD)
        _recording(session, audio_hash="b" * 64, source=IdSource.EMT_GUANO)
        session.commit()

        results = filtered_recordings(session, source=IdSource.EMT_WAMD)

    assert [r.id for r in results] == [matching.id]


def test_filtered_sites_by_bbox_and_date(engine: Engine) -> None:
    with OrmSession(engine) as session:
        inside = Site(
            centroid=WKTElement("POINT(10 50)", srid=4326),
            radius_m=100.0,
            recording_count=3,
            first_at=datetime(2026, 1, 1, tzinfo=UTC),
            last_at=datetime(2026, 1, 2, tzinfo=UTC),
        )
        outside = Site(
            centroid=WKTElement("POINT(100 50)", srid=4326),
            radius_m=100.0,
            recording_count=1,
            first_at=datetime(2026, 1, 1, tzinfo=UTC),
            last_at=datetime(2026, 1, 2, tzinfo=UTC),
        )
        session.add_all([inside, outside])
        session.commit()

        results = filtered_sites(session, bbox=(0.0, 40.0, 20.0, 60.0))

    assert [s.id for s in results] == [inside.id]


def test_list_taxa_orders_alphabetically_by_scientific_name(engine: Engine) -> None:
    with OrmSession(engine) as session:
        session.add_all(
            [
                Taxon(rank="species", scientific_name="Pipistrellus pipistrellus"),
                Taxon(rank="species", scientific_name="Eptesicus serotinus"),
            ],
        )
        session.commit()

        results = list_taxa(session)

    assert [t.scientific_name for t in results] == [
        "Eptesicus serotinus",
        "Pipistrellus pipistrellus",
    ]


def test_list_sessions_orders_most_recent_first(engine: Engine) -> None:
    with OrmSession(engine) as session:
        older = AnnotationSession(
            started_at=datetime(2026, 1, 1, tzinfo=UTC),
            ended_at=datetime(2026, 1, 2, tzinfo=UTC),
        )
        newer = AnnotationSession(
            started_at=datetime(2026, 6, 1, tzinfo=UTC),
            ended_at=datetime(2026, 6, 2, tzinfo=UTC),
        )
        session.add_all([older, newer])
        session.commit()

        results = list_sessions(session)

    assert [s.id for s in results] == [newer.id, older.id]


def test_filtered_recordings_by_site(engine: Engine) -> None:
    with OrmSession(engine) as session:
        site = Site(
            centroid=WKTElement("POINT(10 50)", srid=4326),
            radius_m=50.0,
            recording_count=1,
            first_at=datetime(2026, 8, 25, tzinfo=UTC),
            last_at=datetime(2026, 8, 25, tzinfo=UTC),
        )
        session.add(site)
        session.flush()
        at_site = _recording(session, audio_hash="a" * 64)
        at_site.site_id = site.id
        elsewhere = _recording(session, audio_hash="b" * 64)
        session.add_all([at_site, elsewhere])
        session.commit()
        site_id = site.id

        results = filtered_recordings(session, site_id=site_id, verdict="all")

        assert {r.audio_hash for r in results} == {"a" * 64}


def test_filtered_sites_by_id(engine: Engine) -> None:
    with OrmSession(engine) as session:
        wanted = Site(
            centroid=WKTElement("POINT(10 50)", srid=4326),
            radius_m=50.0,
            recording_count=1,
            first_at=datetime(2026, 8, 25, tzinfo=UTC),
            last_at=datetime(2026, 8, 25, tzinfo=UTC),
        )
        other = Site(
            centroid=WKTElement("POINT(11 51)", srid=4326),
            radius_m=50.0,
            recording_count=1,
            first_at=datetime(2026, 8, 25, tzinfo=UTC),
            last_at=datetime(2026, 8, 25, tzinfo=UTC),
        )
        session.add_all([wanted, other])
        session.commit()
        wanted_id = wanted.id

        results = filtered_sites(session, site_id=wanted_id)

        assert [s.id for s in results] == [wanted_id]


# Tests for neighbor_recordings -- no database needed, pure function tests
def _bare_recording(audio_hash: str, recorded_at: datetime) -> Recording:
    """A Recording that's never touched a session -- neighbor_recordings only
    reads audio_hash/recorded_at, so no DB round trip is needed to test it."""
    return Recording(
        audio_hash=audio_hash, path=f"{audio_hash}.wav", recorded_at=recorded_at
    )


def test_neighbor_recordings_finds_both_sides() -> None:
    early = _bare_recording("a" * 64, datetime(2026, 8, 25, 20, 0, tzinfo=UTC))
    middle = _bare_recording("b" * 64, datetime(2026, 8, 25, 21, 0, tzinfo=UTC))
    late = _bare_recording("c" * 64, datetime(2026, 8, 25, 22, 0, tzinfo=UTC))

    result = neighbor_recordings([late, early, middle], "b" * 64)

    assert result is not None
    previous, next_ = result
    assert previous is not None and previous.audio_hash == "a" * 64
    assert next_ is not None and next_.audio_hash == "c" * 64


def test_neighbor_recordings_stops_at_the_start() -> None:
    early = _bare_recording("a" * 64, datetime(2026, 8, 25, 20, 0, tzinfo=UTC))
    late = _bare_recording("b" * 64, datetime(2026, 8, 25, 21, 0, tzinfo=UTC))

    result = neighbor_recordings([early, late], "a" * 64)

    assert result == (None, late)


def test_neighbor_recordings_stops_at_the_end() -> None:
    early = _bare_recording("a" * 64, datetime(2026, 8, 25, 20, 0, tzinfo=UTC))
    late = _bare_recording("b" * 64, datetime(2026, 8, 25, 21, 0, tzinfo=UTC))

    result = neighbor_recordings([early, late], "b" * 64)

    assert result == (early, None)


def test_neighbor_recordings_none_when_hash_not_in_set() -> None:
    present = _bare_recording("a" * 64, datetime(2026, 8, 25, 20, 0, tzinfo=UTC))

    assert neighbor_recordings([present], "z" * 64) is None


def test_site_detail_breaks_down_species_and_lists_sessions(engine: Engine) -> None:
    with OrmSession(engine) as session:
        site = Site(
            centroid=WKTElement("POINT(10 50)", srid=4326),
            radius_m=50.0,
            recording_count=2,
            first_at=datetime(2026, 8, 25, tzinfo=UTC),
            last_at=datetime(2026, 8, 25, tzinfo=UTC),
        )
        taxon = Taxon(rank="species", scientific_name="Eptesicus serotinus")
        annotation_session = AnnotationSession(
            started_at=datetime(2026, 8, 25, 20, 0, tzinfo=UTC),
            ended_at=datetime(2026, 8, 25, 23, 0, tzinfo=UTC),
        )
        session.add_all([site, taxon, annotation_session])
        session.flush()

        r1 = _recording(
            session,
            audio_hash="a" * 64,
            taxon_id=taxon.id,
            session_id=annotation_session.id,
        )
        r1.site_id = site.id
        r2 = _recording(
            session,
            audio_hash="b" * 64,
            taxon_id=taxon.id,
            session_id=annotation_session.id,
        )
        r2.site_id = site.id
        session.add_all([r1, r2])
        session.commit()
        site_id, taxon_id, session_id = site.id, taxon.id, annotation_session.id

        detail = site_detail(session, site_id)

        assert detail is not None
        assert detail.site.id == site_id
        assert detail.species_counts == [(session.get(Taxon, taxon_id), 2)]
        assert [s.id for s in detail.sessions] == [session_id]


def test_site_detail_returns_none_for_unknown_site(engine: Engine) -> None:
    with OrmSession(engine) as session:
        assert site_detail(session, 999999) is None
