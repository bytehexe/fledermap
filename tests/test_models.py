from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import IdSource, SessionKind, Verdict
from fledermap.store.models import Identification, Recording, Session

pytestmark = pytest.mark.db


def _recording(
    digest: str = "a" * 64, path: str = "s/EPTSER_20150610_215446.wav"
) -> Recording:
    return Recording(
        audio_hash=digest,
        path=path,
        recorded_at=datetime(2015, 6, 10, 21, 54, 46, tzinfo=UTC),
        samplerate_hz=256000,
    )


def test_recording_round_trips(engine: Engine) -> None:
    with OrmSession(engine) as session:
        session.add(_recording())
        session.commit()

    with OrmSession(engine) as session:
        found = session.scalars(select(Recording)).one()
        assert found.samplerate_hz == 256000
        assert found.missing_since is None


def test_audio_hash_is_unique(engine: Engine) -> None:
    """Identity is the audio; the same payload twice is the same recording."""
    with OrmSession(engine) as session:
        session.add(_recording(path="a.wav"))
        session.add(_recording(path="b.wav"))
        with pytest.raises(IntegrityError):
            session.commit()


def test_geometry_may_be_null(engine: Engine) -> None:
    """Recordings without GPS are first-class, not errors."""
    with OrmSession(engine) as session:
        rec = _recording()
        rec.geom = None
        session.add(rec)
        session.commit()
        assert session.scalars(select(Recording)).one().geom is None


def test_identifications_cascade_from_recording(engine: Engine) -> None:
    """`identification.recording_id` is declared `ondelete="CASCADE"`
    (models.py): deleting a Recording row must delete its Identification
    rows with it, at the database level. Deletes via raw SQL rather than the
    ORM session — the ORM's OWN `cascade="all, delete-orphan"` would make
    this pass even if the FK's `ondelete="CASCADE"` were missing, which is
    not what this test's name promises to prove (whole-branch review,
    Minor G)."""
    with OrmSession(engine) as session:
        rec = _recording()
        rec.identifications = [
            Identification(
                source=IdSource.EMT_WAMD,
                source_version="App 3.1.10",
                verdict=Verdict.SPECIES,
                raw_label="EPTSER",
            ),
            Identification(
                source=IdSource.EMT_FILENAME,
                verdict=Verdict.SPECIES,
                raw_label="EPTSER",
            ),
        ]
        session.add(rec)
        session.commit()
        recording_id = rec.id

    assert recording_id is not None
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM recording WHERE id = :id"), {"id": recording_id})

    with OrmSession(engine) as session:
        assert session.scalars(select(Recording)).all() == []
        assert session.scalars(select(Identification)).all() == []


def test_both_timestamp_columns_persist(engine: Engine) -> None:
    """Spec D17: neither candidate may be dropped as 'unused'."""
    with OrmSession(engine) as session:
        rec = _recording()
        rec.filename_at = datetime(2015, 6, 10, 21, 54, 46, tzinfo=UTC)
        rec.metadata_at = datetime(2015, 6, 10, 9, 54, 54, tzinfo=UTC)
        rec.timestamp_disagreement_s = 43192.0
        session.add(rec)
        session.commit()

    with OrmSession(engine) as session:
        found = session.scalars(select(Recording)).one()
        assert found.filename_at != found.metadata_at
        assert found.timestamp_disagreement_s == pytest.approx(43192.0)


def test_enum_columns_round_trip_to_python_type(engine: Engine) -> None:
    """A plain String column would come back as `str`, not the enum (review item 1)."""
    with OrmSession(engine) as session:
        rec = _recording()
        rec.identifications = [
            Identification(
                source=IdSource.EMT_WAMD, verdict=Verdict.SPECIES, raw_label="EPTSER"
            ),
        ]
        session.add(rec)
        session.commit()

    with OrmSession(engine) as session:
        loaded = session.scalars(select(Identification)).one()
        assert isinstance(loaded.source, IdSource)
        assert isinstance(loaded.verdict, Verdict)
        assert loaded.source is IdSource.EMT_WAMD
        assert loaded.verdict is Verdict.SPECIES


def test_session_kind_round_trips_to_python_type(engine: Engine) -> None:
    """A plain String column would come back as `str`, not the enum — mirrors
    `test_enum_columns_round_trip_to_python_type` above, written before `kind`
    became an enum (phase 2, task 5)."""
    with OrmSession(engine) as session:
        session.add(
            Session(
                started_at=datetime(2026, 8, 21, 21, tzinfo=UTC),
                ended_at=datetime(2026, 8, 21, 23, tzinfo=UTC),
                kind=SessionKind.STATIONARY,
                # A plain value: this test's subject is `kind`'s round-trip,
                # not `detector_key`'s content. The real separator is `\x1f`
                # (ASCII Unit Separator) — see `derive.sessions._detector_key`,
                # which explains why it is not `\x00`.
                detector_key="EMT1",
            ),
        )
        session.commit()

    with OrmSession(engine) as session:
        loaded = session.scalars(select(Session)).one()
        assert isinstance(loaded.kind, SessionKind)
        assert loaded.kind is SessionKind.STATIONARY


def test_duplicate_claim_with_null_source_version_rejected(engine: Engine) -> None:
    """Postgres treats NULLs as distinct by default; `source_version` is nullable
    for exactly the sources (filename IDs, manual annotations) that most need the
    constraint to hold (review item 2)."""
    with OrmSession(engine) as session:
        rec = _recording()
        session.add(rec)
        session.commit()
        recording_id = rec.id

    with OrmSession(engine) as session:
        session.add(
            Identification(
                recording_id=recording_id,
                source=IdSource.MANUAL,
                verdict=Verdict.SPECIES,
                raw_label="EPTSER",
            ),
        )
        session.add(
            Identification(
                recording_id=recording_id,
                source=IdSource.MANUAL,
                verdict=Verdict.SPECIES,
                raw_label="EPTSER",
            ),
        )
        with pytest.raises(IntegrityError):
            session.commit()
