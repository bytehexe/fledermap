# tests/test_statistics_query.py
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from geoalchemy2.elements import WKTElement
from sqlalchemy import Engine
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import IdSource, Verdict
from fledermap.services.statistics import totals
from fledermap.store.models import Identification, Recording, Site, Taxon

pytestmark = pytest.mark.db


def _recording(
    session: OrmSession,
    *,
    audio_hash: str,
    taxon_id: int | None = None,
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
    if verdict is not None:
        session.add(
            Identification(
                recording_id=r.id,
                source=IdSource.EMT_GUANO,
                verdict=verdict,
                taxon_id=taxon_id,
                first_seen_at=recorded_at,
            ),
        )
    session.flush()
    return r


def test_totals_global_counts_all_recordings_species_and_sites(engine: Engine) -> None:
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
        _recording(session, audio_hash="a" * 64, taxon_id=taxon.id, site_id=site.id)
        _recording(session, audio_hash="b" * 64, verdict=Verdict.NOISE)
        session.commit()

    with OrmSession(engine) as session:
        result = totals(session)

    # total_recordings counts EVERY non-missing recording, species or not --
    # the donut deliberately doesn't sum to this (spec's inclusion rules).
    assert result.total_recordings == 2
    assert result.total_species == 1
    assert result.total_sites == 1


def test_totals_excludes_missing_recordings(engine: Engine) -> None:
    with OrmSession(engine) as session:
        _recording(session, audio_hash="a" * 64, missing=True)
        session.commit()

    with OrmSession(engine) as session:
        result = totals(session)

    assert result.total_recordings == 0


def test_totals_site_scoped_counts_only_that_site(engine: Engine) -> None:
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
        _recording(session, audio_hash="a" * 64, site_id=site.id)
        _recording(session, audio_hash="b" * 64, site_id=None)
        session.commit()
        site_id = site.id

    with OrmSession(engine) as session:
        result = totals(session, site_id=site_id)

    assert result.total_recordings == 1
    assert result.total_species is None
    assert result.total_sites is None


def test_totals_species_scoped_counts_by_membership(engine: Engine) -> None:
    """A multi-species recording containing the filtered taxon counts, even
    though it's not that recording's sole result (spec: same membership rule
    as recording_counts_by_site)."""
    with OrmSession(engine) as session:
        taxon_a = Taxon(rank="species", scientific_name="Eptesicus serotinus")
        taxon_b = Taxon(rank="species", scientific_name="Pipistrellus pipistrellus")
        site = Site(
            centroid=WKTElement("POINT(10 50)", srid=4326),
            radius_m=50.0,
            recording_count=1,
            first_at=datetime(2026, 8, 25, tzinfo=UTC),
            last_at=datetime(2026, 8, 25, tzinfo=UTC),
        )
        session.add_all([taxon_a, taxon_b, site])
        session.flush()
        r = Recording(
            audio_hash="a" * 64,
            path="a.wav",
            recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
            site_id=site.id,
        )
        session.add(r)
        session.flush()
        session.add_all(
            [
                Identification(
                    recording_id=r.id,
                    source=IdSource.EMT_MANUAL,
                    verdict=Verdict.SPECIES,
                    taxon_id=taxon_a.id,
                    first_seen_at=r.recorded_at,
                ),
                Identification(
                    recording_id=r.id,
                    source=IdSource.EMT_MANUAL,
                    verdict=Verdict.SPECIES,
                    taxon_id=taxon_b.id,
                    first_seen_at=r.recorded_at,
                ),
            ],
        )
        session.commit()
        taxon_a_id = taxon_a.id

    with OrmSession(engine) as session:
        result = totals(session, taxon_id=taxon_a_id)

    assert result.total_recordings == 1
    assert result.total_sites == 1
    assert result.total_species is None
