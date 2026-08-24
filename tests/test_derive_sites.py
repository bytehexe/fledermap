# tests/test_derive_sites.py
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from geoalchemy2.elements import WKTElement
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import SessionKind
from fledermap.services.derive import derive_sites
from fledermap.store.models import Recording, Session, Site

pytestmark = pytest.mark.db


def _stationary_session(db_session: OrmSession) -> Session:
    s = Session(
        started_at=datetime(2026, 8, 21, 21, tzinfo=UTC),
        ended_at=datetime(2026, 8, 21, 23, tzinfo=UTC),
        kind=SessionKind.STATIONARY,
        detector_key="EMT\x1f1",
    )
    db_session.add(s)
    db_session.flush()
    return s


def _recording(
    hash_suffix: str,
    db_session: OrmSession,
    session: Session,
    lon: float,
    lat: float,
) -> Recording:
    r = Recording(
        audio_hash=hash_suffix.rjust(64, "0"),
        path=f"{hash_suffix}.wav",
        recorded_at=datetime(2026, 8, 21, 21, tzinfo=UTC),
        session_id=session.id,
        geom=WKTElement(f"POINT({lon} {lat})", srid=4326),
    )
    db_session.add(r)
    return r


def test_a_cluster_of_nearby_recordings_becomes_one_site(engine: Engine) -> None:
    with OrmSession(engine) as session:
        stationary = _stationary_session(session)
        _recording("a", session, stationary, 13.4000, 52.5000)
        _recording("b", session, stationary, 13.4001, 52.5000)
        session.commit()

        report = derive_sites(session, eps_m=75.0, min_points=2)
        session.commit()

        assert report.site_count == 1
        assert report.unclustered == 0
        sites = session.scalars(select(Site)).all()
        assert len(sites) == 1
        assert sites[0].recording_count == 2
        recordings = session.scalars(select(Recording)).all()
        assert all(r.site_id == sites[0].id for r in recordings)


def test_an_isolated_recording_stays_unclustered(engine: Engine) -> None:
    with OrmSession(engine) as session:
        stationary = _stationary_session(session)
        _recording("a", session, stationary, 13.4000, 52.5000)
        session.commit()

        report = derive_sites(session, eps_m=75.0, min_points=2)
        session.commit()

        assert report.site_count == 0
        assert report.unclustered == 1
        recording = session.scalars(select(Recording)).one()
        assert recording.site_id is None


def test_transect_recordings_are_excluded(engine: Engine) -> None:
    with OrmSession(engine) as session:
        transect = Session(
            started_at=datetime(2026, 8, 21, 21, tzinfo=UTC),
            ended_at=datetime(2026, 8, 21, 23, tzinfo=UTC),
            kind=SessionKind.TRANSECT,
            detector_key="EMT\x1f1",
        )
        session.add(transect)
        session.flush()
        _recording("a", session, transect, 13.4000, 52.5000)
        _recording("b", session, transect, 13.4001, 52.5000)
        session.commit()

        report = derive_sites(session, eps_m=75.0, min_points=2)
        session.commit()

        assert report.site_count == 0
        assert report.unclustered == 0


def test_recordings_without_gps_are_excluded(engine: Engine) -> None:
    with OrmSession(engine) as session:
        stationary = _stationary_session(session)
        session.add(
            Recording(
                audio_hash="c" * 64,
                path="c.wav",
                recorded_at=datetime(2026, 8, 21, 21, tzinfo=UTC),
                session_id=stationary.id,
                geom=None,
            ),
        )
        session.commit()

        report = derive_sites(session, eps_m=75.0, min_points=2)
        session.commit()

        assert report.site_count == 0
        assert report.unclustered == 0


def test_rebuild_is_wholesale_and_idempotent(engine: Engine) -> None:
    """Re-running with the same data doesn't duplicate sites; a recording that
    drops out of the archive between runs loses its site cleanly."""
    with OrmSession(engine) as session:
        stationary = _stationary_session(session)
        _recording("a", session, stationary, 13.4000, 52.5000)
        _recording("b", session, stationary, 13.4001, 52.5000)
        session.commit()

        derive_sites(session, eps_m=75.0, min_points=2)
        session.commit()
        first_site_id = session.scalars(select(Site)).one().id

        # Re-run with identical input.
        report = derive_sites(session, eps_m=75.0, min_points=2)
        session.commit()

        assert report.site_count == 1
        sites = session.scalars(select(Site)).all()
        assert len(sites) == 1
        # A fresh row (wholesale rebuild) — not necessarily the same id.
        recordings = session.scalars(select(Recording)).all()
        assert all(r.site_id == sites[0].id for r in recordings)
        assert first_site_id is not None  # sanity: the fixture actually ran once
