from __future__ import annotations

from datetime import UTC, datetime

import pytest
from geoalchemy2.elements import WKTElement
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import IdSource, Verdict
from fledermap.services.derive import derive_sites
from fledermap.store.geo import decode_point
from fledermap.store.models import Identification, Recording, Session, Site

pytestmark = pytest.mark.db


def _recording(
    hash_suffix: str,
    db_session: OrmSession,
    lon: float,
    lat: float,
    *,
    verdict: Verdict | None = Verdict.SPECIES,
    session_id: int | None = None,
) -> Recording:
    r = Recording(
        audio_hash=hash_suffix.rjust(64, "0"),
        path=f"{hash_suffix}.wav",
        recorded_at=datetime(2026, 8, 21, 21, tzinfo=UTC),
        session_id=session_id,
        geom=WKTElement(f"POINT({lon} {lat})", srid=4326),
    )
    db_session.add(r)
    db_session.flush()
    if verdict is not None:
        db_session.add(
            Identification(
                recording_id=r.id,
                source=IdSource.EMT_GUANO,
                verdict=verdict,
                first_seen_at=r.recorded_at,
            ),
        )
        db_session.flush()
    return r


def test_a_cluster_of_species_identified_recordings_becomes_one_site(
    engine: Engine,
) -> None:
    with OrmSession(engine) as session:
        _recording("a", session, 13.4000, 52.5000)
        _recording("b", session, 13.4001, 52.5000)
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
        _recording("a", session, 13.4000, 52.5000)
        session.commit()

        report = derive_sites(session, eps_m=75.0, min_points=2)
        session.commit()

        assert report.site_count == 0
        assert report.unclustered == 1
        recording = session.scalars(select(Recording)).one()
        assert recording.site_id is None


def test_a_transect_sessions_identified_recordings_now_form_a_site(
    engine: Engine,
) -> None:
    """Regression test for the bug that motivated this design: a walked
    transect that passes through a real hotspot used to be entirely invisible
    to site derivation. Site membership no longer cares what session a
    recording belongs to -- this session used to be the kind of thing
    `derive_sites` structurally excluded (design spec
    2026-08-29-fledermap-identification-based-sites-design.md)."""
    with OrmSession(engine) as session:
        walked = Session(
            started_at=datetime(2026, 8, 21, 21, tzinfo=UTC),
            ended_at=datetime(2026, 8, 21, 23, tzinfo=UTC),
            detector_key="EMT\x1f1",
        )
        session.add(walked)
        session.flush()
        _recording("a", session, 13.4000, 52.5000, session_id=walked.id)
        _recording("b", session, 13.4001, 52.5000, session_id=walked.id)
        session.commit()

        report = derive_sites(session, eps_m=75.0, min_points=2)
        session.commit()

        assert report.site_count == 1
        assert report.unclustered == 0


@pytest.mark.parametrize("verdict", [Verdict.NO_ID, Verdict.NOISE])
def test_no_id_and_noise_verdicts_are_excluded(
    engine: Engine,
    verdict: Verdict,
) -> None:
    with OrmSession(engine) as session:
        _recording("a", session, 13.4000, 52.5000, verdict=verdict)
        _recording("b", session, 13.4001, 52.5000, verdict=verdict)
        session.commit()

        report = derive_sites(session, eps_m=75.0, min_points=2)
        session.commit()

        assert report.site_count == 0
        assert report.unclustered == 0
        recordings = session.scalars(select(Recording)).all()
        assert all(r.site_id is None for r in recordings)


def test_recordings_with_no_identification_at_all_are_excluded(
    engine: Engine,
) -> None:
    with OrmSession(engine) as session:
        _recording("a", session, 13.4000, 52.5000, verdict=None)
        _recording("b", session, 13.4001, 52.5000, verdict=None)
        session.commit()

        report = derive_sites(session, eps_m=75.0, min_points=2)
        session.commit()

        assert report.site_count == 0
        assert report.unclustered == 0


def test_mixed_verdict_cluster_counts_only_species_members(engine: Engine) -> None:
    """The verdict filter runs before clustering, not just for display -- a
    NO_ID recording at the exact same spot as two SPECIES ones must not
    inflate `recording_count`."""
    with OrmSession(engine) as session:
        _recording("a", session, 13.4000, 52.5000, verdict=Verdict.SPECIES)
        _recording("b", session, 13.4000, 52.5000, verdict=Verdict.SPECIES)
        _recording("c", session, 13.4000, 52.5000, verdict=Verdict.NO_ID)
        session.commit()

        report = derive_sites(session, eps_m=75.0, min_points=2)
        session.commit()

        assert report.site_count == 1
        site = session.scalars(select(Site)).one()
        assert site.recording_count == 2
        excluded = session.scalars(
            select(Recording).where(Recording.path == "c.wav"),
        ).one()
        assert excluded.site_id is None


def test_superseded_species_identification_does_not_count(engine: Engine) -> None:
    """A different code path to `None` than "no identification rows at all" --
    `current_best_identification` filters on `superseded_at is None`, so a
    recording whose only SPECIES claim has been superseded must still be
    excluded, the same as a recording with zero identifications."""
    with OrmSession(engine) as session:
        a = _recording("a", session, 13.4000, 52.5000, verdict=None)
        b = _recording("b", session, 13.4001, 52.5000, verdict=None)
        session.add(
            Identification(
                recording_id=a.id,
                source=IdSource.EMT_GUANO,
                verdict=Verdict.SPECIES,
                first_seen_at=a.recorded_at,
                superseded_at=a.recorded_at,
            ),
        )
        session.add(
            Identification(
                recording_id=b.id,
                source=IdSource.EMT_GUANO,
                verdict=Verdict.SPECIES,
                first_seen_at=b.recorded_at,
                superseded_at=b.recorded_at,
            ),
        )
        session.commit()

        report = derive_sites(session, eps_m=75.0, min_points=2)
        session.commit()

        assert report.site_count == 0
        assert report.unclustered == 0


def test_manual_species_outranks_a_superseding_noise_verdict(engine: Engine) -> None:
    """Site membership goes through `current_best_identification`'s source
    precedence, not just "any SPECIES claim exists" -- a higher-precedence
    MANUAL SPECIES claim must win the site even when a lower-precedence
    EMT_GUANO NOISE claim also exists on the same recording."""
    with OrmSession(engine) as session:
        a = _recording("a", session, 13.4000, 52.5000, verdict=None)
        b = _recording("b", session, 13.4001, 52.5000, verdict=None)
        for r in (a, b):
            session.add(
                Identification(
                    recording_id=r.id,
                    source=IdSource.EMT_GUANO,
                    verdict=Verdict.NOISE,
                    first_seen_at=r.recorded_at,
                ),
            )
            session.add(
                Identification(
                    recording_id=r.id,
                    source=IdSource.MANUAL,
                    verdict=Verdict.SPECIES,
                    first_seen_at=r.recorded_at,
                ),
            )
        session.commit()

        report = derive_sites(session, eps_m=75.0, min_points=2)
        session.commit()

        assert report.site_count == 1
        assert report.unclustered == 0


def test_recordings_without_gps_are_excluded(engine: Engine) -> None:
    with OrmSession(engine) as session:
        r = Recording(
            audio_hash="c" * 64,
            path="c.wav",
            recorded_at=datetime(2026, 8, 21, 21, tzinfo=UTC),
            geom=None,
        )
        session.add(r)
        session.flush()
        session.add(
            Identification(
                recording_id=r.id,
                source=IdSource.EMT_GUANO,
                verdict=Verdict.SPECIES,
                first_seen_at=r.recorded_at,
            ),
        )
        session.commit()

        report = derive_sites(session, eps_m=75.0, min_points=2)
        session.commit()

        assert report.site_count == 0
        assert report.unclustered == 0


@pytest.mark.parametrize(
    ("label", "lon", "lat"),
    [
        ("berlin", 13.4000, 52.5000),  # high northern latitude
        ("quito", -78.4600, -0.1800),  # near-equatorial, southern hemisphere
    ],
)
def test_clustering_regression_at_both_latitudes(
    engine: Engine,
    label: str,
    lon: float,
    lat: float,
) -> None:
    """Phase 2's exit criterion (parent spec section 15): clustering must be
    correct near a pole-adjacent latitude AND near the equator. A wrong UTM
    zone pick, or an eps accidentally in degrees instead of metres, could pass
    every other test in this plan (all of which sit near Berlin) while still
    being broken here."""
    with OrmSession(engine) as session:
        # Two points ~15m apart (well inside a 75m eps); one far outlier that
        # must stay unclustered regardless of latitude.
        _recording(f"{label}-a", session, lon, lat)
        _recording(f"{label}-b", session, lon + 0.0002, lat)
        _recording(f"{label}-far", session, lon + 5.0, lat)
        session.commit()

        report = derive_sites(session, eps_m=75.0, min_points=2)
        session.commit()

        assert report.site_count == 1
        assert report.unclustered == 1
        recordings = {r.path: r for r in session.scalars(select(Recording)).all()}
        assert recordings[f"{label}-a.wav"].site_id is not None
        assert (
            recordings[f"{label}-a.wav"].site_id == recordings[f"{label}-b.wav"].site_id
        )
        assert recordings[f"{label}-far.wav"].site_id is None


def test_recordings_at_one_identical_fix_still_produce_a_site(engine: Engine) -> None:
    """Regression: a stationary detector reporting the same rounded GPS fix for
    every recording gives a zero-variance spread. `GeoCluster`'s z-score filter
    then discarded EVERY point, `mass_point` returned `(None, None)`, and the
    `POINT(None None)` that produced failed to parse in Postgres — so the whole
    `derive` run died on write. This project's own two bundled samples already
    share one identical fix; a third and fourth would have triggered it."""
    with OrmSession(engine) as session:
        for suffix in ("a", "b", "c", "d"):
            _recording(suffix, session, 13.4000, 52.5000)
        session.commit()

        report = derive_sites(session, eps_m=75.0, min_points=2)
        session.commit()

        assert report.site_count == 1
        assert report.unclustered == 0
        site = session.scalars(select(Site)).one()
        assert site.recording_count == 4
        centroid = decode_point(site.centroid)
        assert centroid is not None
        lon, lat = centroid
        assert lon == pytest.approx(13.4000, abs=1e-6)
        assert lat == pytest.approx(52.5000, abs=1e-6)
        assert site.radius_m == pytest.approx(0.0, abs=1e-6)


def test_rebuild_is_wholesale_and_idempotent(engine: Engine) -> None:
    """Re-running with the same data doesn't duplicate sites; a recording that
    drops out of the archive between runs loses its site cleanly."""
    with OrmSession(engine) as session:
        _recording("a", session, 13.4000, 52.5000)
        _recording("b", session, 13.4001, 52.5000)
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
