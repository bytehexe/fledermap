# tests/test_entities_query.py
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from geoalchemy2.elements import WKTElement
from sqlalchemy import Engine
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import IdSource, Verdict
from fledermap.services.entities import list_sites, list_species, species_detail
from fledermap.store.models import Identification, Recording, Site, Taxon, TaxonCode

pytestmark = pytest.mark.db


def _recording(
    session: OrmSession,
    *,
    audio_hash: str,
    taxon_id: int | None = None,
    taxon_ids: list[int] | None = None,
    verdict: Verdict | None = Verdict.SPECIES,
    recorded_at: datetime = datetime(2026, 8, 25, tzinfo=UTC),
    site_id: int | None = None,
    missing: bool = False,
) -> Recording:
    r = Recording(
        audio_hash=audio_hash,
        path=f"{audio_hash}.wav",
        recorded_at=recorded_at,
        site_id=site_id,
        missing_since=datetime(2026, 8, 25, tzinfo=UTC) if missing else None,
    )
    session.add(r)
    session.flush()
    for tid in (
        taxon_ids
        if taxon_ids is not None
        else ([taxon_id] if taxon_id is not None or verdict is not None else [])
    ):
        if verdict is None:
            continue
        session.add(
            Identification(
                recording_id=r.id,
                source=IdSource.EMT_GUANO,
                verdict=verdict,
                taxon_id=tid,
                first_seen_at=recorded_at,
            ),
        )
    session.flush()
    return r


def test_list_species_excludes_taxa_with_no_recordings(engine: Engine) -> None:
    with OrmSession(engine) as session:
        found = Taxon(rank="species", scientific_name="Eptesicus serotinus")
        unrecorded = Taxon(rank="species", scientific_name="Myotis daubentonii")
        session.add_all([found, unrecorded])
        session.flush()
        _recording(session, audio_hash="a" * 64, taxon_id=found.id)
        session.commit()

        rows = list_species(session)

    assert [row.taxon.scientific_name for row in rows] == ["Eptesicus serotinus"]


def test_list_species_orders_alphabetically_and_counts_recordings(
    engine: Engine,
) -> None:
    with OrmSession(engine) as session:
        b = Taxon(rank="species", scientific_name="Myotis daubentonii")
        a = Taxon(rank="species", scientific_name="Eptesicus serotinus")
        session.add_all([a, b])
        session.flush()
        _recording(session, audio_hash="a" * 64, taxon_id=a.id)
        _recording(session, audio_hash="b" * 64, taxon_id=b.id)
        _recording(session, audio_hash="c" * 64, taxon_id=b.id)
        session.commit()

        rows = list_species(session)

    assert [(row.taxon.scientific_name, row.recording_count) for row in rows] == [
        ("Eptesicus serotinus", 1),
        ("Myotis daubentonii", 2),
    ]


def test_list_species_includes_a_code_when_one_exists(engine: Engine) -> None:
    with OrmSession(engine) as session:
        taxon = Taxon(rank="species", scientific_name="Eptesicus serotinus")
        session.add(taxon)
        session.flush()
        session.add(TaxonCode(source="wa", code="EPTSER", taxon_id=taxon.id))
        _recording(session, audio_hash="a" * 64, taxon_id=taxon.id)
        session.commit()

        rows = list_species(session)

    assert rows[0].code == "EPTSER"


def test_list_species_multi_species_recording_counts_toward_every_taxon(
    engine: Engine,
) -> None:
    with OrmSession(engine) as session:
        a = Taxon(rank="species", scientific_name="Eptesicus serotinus")
        b = Taxon(rank="species", scientific_name="Pipistrellus pipistrellus")
        session.add_all([a, b])
        session.flush()
        r = Recording(
            audio_hash="a" * 64,
            path="a.wav",
            recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
        )
        session.add(r)
        session.flush()
        session.add_all(
            [
                Identification(
                    recording_id=r.id,
                    source=IdSource.EMT_MANUAL,
                    verdict=Verdict.SPECIES,
                    taxon_id=a.id,
                    first_seen_at=r.recorded_at,
                ),
                Identification(
                    recording_id=r.id,
                    source=IdSource.EMT_MANUAL,
                    verdict=Verdict.SPECIES,
                    taxon_id=b.id,
                    first_seen_at=r.recorded_at,
                ),
            ],
        )
        session.commit()

        rows = list_species(session)

    assert {(row.taxon.scientific_name, row.recording_count) for row in rows} == {
        ("Eptesicus serotinus", 1),
        ("Pipistrellus pipistrellus", 1),
    }


def test_species_detail_returns_none_for_unknown_taxon(engine: Engine) -> None:
    with OrmSession(engine) as session:
        assert species_detail(session, 999999) is None


def test_species_detail_lists_sites_and_recent_recordings(engine: Engine) -> None:
    with OrmSession(engine) as session:
        taxon = Taxon(rank="species", scientific_name="Eptesicus serotinus")
        site = Site(
            centroid=WKTElement("POINT(10 50)", srid=4326),
            radius_m=50.0,
            recording_count=1,
            first_at=datetime(2026, 8, 25, tzinfo=UTC),
            last_at=datetime(2026, 8, 25, tzinfo=UTC),
        )
        session.add_all([taxon, site])
        session.flush()
        _recording(
            session,
            audio_hash="a" * 64,
            taxon_id=taxon.id,
            site_id=site.id,
            recorded_at=datetime(2026, 8, 25, 20, 0, tzinfo=UTC),
        )
        # a different species at the same site must not appear in the tally
        other = Taxon(rank="species", scientific_name="Myotis daubentonii")
        session.add(other)
        session.flush()
        _recording(
            session,
            audio_hash="b" * 64,
            taxon_id=other.id,
            site_id=site.id,
            recorded_at=datetime(2026, 8, 25, 21, 0, tzinfo=UTC),
        )
        session.commit()

        detail = species_detail(session, taxon.id)

    assert detail is not None
    assert detail.taxon.id == taxon.id
    assert [(s.id, count) for s, count in detail.sites] == [(site.id, 1)]
    assert [r.audio_hash for r in detail.recent_recordings] == ["a" * 64]


def test_species_detail_recent_recordings_are_most_recent_first(
    engine: Engine,
) -> None:
    with OrmSession(engine) as session:
        taxon = Taxon(rank="species", scientific_name="Eptesicus serotinus")
        session.add(taxon)
        session.flush()
        _recording(
            session,
            audio_hash="a" * 64,
            taxon_id=taxon.id,
            recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
        )
        _recording(
            session,
            audio_hash="b" * 64,
            taxon_id=taxon.id,
            recorded_at=datetime(2026, 8, 26, tzinfo=UTC),
        )
        session.commit()

        detail = species_detail(session, taxon.id)

    assert detail is not None
    assert [r.audio_hash for r in detail.recent_recordings] == ["b" * 64, "a" * 64]


def test_list_sites_orders_most_recently_active_first(engine: Engine) -> None:
    with OrmSession(engine) as session:
        older = Site(
            centroid=WKTElement("POINT(10 50)", srid=4326),
            radius_m=50.0,
            recording_count=1,
            first_at=datetime(2026, 1, 1, tzinfo=UTC),
            last_at=datetime(2026, 1, 2, tzinfo=UTC),
        )
        newer = Site(
            centroid=WKTElement("POINT(11 51)", srid=4326),
            radius_m=50.0,
            recording_count=1,
            first_at=datetime(2026, 8, 1, tzinfo=UTC),
            last_at=datetime(2026, 8, 2, tzinfo=UTC),
        )
        session.add_all([older, newer])
        session.commit()

        results = list_sites(session)

    assert [s.id for s in results] == [newer.id, older.id]
