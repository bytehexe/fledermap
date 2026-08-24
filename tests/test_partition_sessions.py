from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session as OrmSession

from fledermap.derive.sessions import partition_sessions
from fledermap.domain.codes import SessionKind
from fledermap.store.models import Recording, Session

pytestmark = pytest.mark.db


def _recording(hash_suffix: str, recorded_at: datetime, **kwargs: object) -> Recording:
    return Recording(
        audio_hash=hash_suffix.rjust(64, "0"),
        path=f"{hash_suffix}.wav",
        recorded_at=recorded_at,
        **kwargs,
    )


def test_first_recording_creates_a_new_session(engine: Engine) -> None:
    with OrmSession(engine) as session:
        session.add(
            _recording("a", datetime(2026, 8, 21, 21, tzinfo=UTC), make="EMT", serial="1"),
        )
        session.commit()

        report = partition_sessions(session, session_gap=timedelta(hours=6))
        session.commit()

        assert report.created == 1
        assert report.extended == 0
        recording = session.scalars(select(Recording)).one()
        assert recording.session_id is not None
        created_session = session.get(Session, recording.session_id)
        assert created_session is not None
        assert created_session.kind == SessionKind.STATIONARY


def test_second_recording_within_gap_extends_the_session(engine: Engine) -> None:
    with OrmSession(engine) as session:
        base = datetime(2026, 8, 21, 21, tzinfo=UTC)
        session.add(_recording("a", base, make="EMT", serial="1"))
        session.add(_recording("b", base + timedelta(hours=1), make="EMT", serial="1"))
        session.commit()

        report = partition_sessions(session, session_gap=timedelta(hours=6))
        session.commit()

        assert report.created == 1
        assert report.extended == 1
        sessions = session.scalars(select(Session)).all()
        assert len(sessions) == 1
        assert sessions[0].ended_at == base + timedelta(hours=1)


def test_recording_beyond_gap_starts_a_new_session(engine: Engine) -> None:
    with OrmSession(engine) as session:
        base = datetime(2026, 8, 21, 21, tzinfo=UTC)
        session.add(_recording("a", base, make="EMT", serial="1"))
        session.add(_recording("b", base + timedelta(hours=7), make="EMT", serial="1"))
        session.commit()

        report = partition_sessions(session, session_gap=timedelta(hours=6))
        session.commit()

        assert report.created == 2
        assert report.extended == 0
        assert session.scalars(select(Session)).all().__len__() == 2


def test_different_detectors_get_separate_sessions(engine: Engine) -> None:
    with OrmSession(engine) as session:
        base = datetime(2026, 8, 21, 21, tzinfo=UTC)
        session.add(_recording("a", base, make="EMT", serial="1"))
        session.add(_recording("b", base, make="EMT", serial="2"))
        session.commit()

        report = partition_sessions(session, session_gap=timedelta(hours=6))
        session.commit()

        assert report.created == 2
        assert len(session.scalars(select(Session)).all()) == 2


def test_missing_make_and_serial_still_get_a_session(engine: Engine) -> None:
    with OrmSession(engine) as session:
        session.add(_recording("a", datetime(2026, 8, 21, 21, tzinfo=UTC)))
        session.commit()

        report = partition_sessions(session, session_gap=timedelta(hours=6))
        session.commit()

        assert report.created == 1
        recording = session.scalars(select(Recording)).one()
        assert recording.session_id is not None


def test_recording_extends_an_existing_session_from_a_previous_run(
    engine: Engine,
) -> None:
    with OrmSession(engine) as session:
        base = datetime(2026, 8, 21, 21, tzinfo=UTC)
        existing = Session(
            started_at=base,
            ended_at=base,
            kind=SessionKind.STATIONARY,
            detector_key="EMT\x1f1",
        )
        session.add(existing)
        session.flush()
        session.add(_recording("a", base + timedelta(hours=2), make="EMT", serial="1"))
        session.commit()

        report = partition_sessions(session, session_gap=timedelta(hours=6))
        session.commit()

        assert report.created == 0
        assert report.extended == 1
        assert len(session.scalars(select(Session)).all()) == 1
        session.refresh(existing)
        assert existing.ended_at == base + timedelta(hours=2)


def test_old_recording_close_only_to_a_later_existing_session_joins_it_backward(
    engine: Engine,
) -> None:
    with OrmSession(engine) as session:
        base = datetime(2026, 8, 21, 21, tzinfo=UTC)
        later = Session(
            started_at=base,
            ended_at=base,
            kind=SessionKind.STATIONARY,
            detector_key="EMT\x1f1",
        )
        session.add(later)
        session.flush()
        # Arrives late, timestamped BEFORE `later`, within the gap of only it.
        session.add(_recording("a", base - timedelta(hours=1), make="EMT", serial="1"))
        session.commit()

        report = partition_sessions(session, session_gap=timedelta(hours=6))
        session.commit()

        assert report.created == 0
        assert report.extended == 1
        session.refresh(later)
        assert later.started_at == base - timedelta(hours=1)


def test_already_sessioned_recordings_are_untouched(engine: Engine) -> None:
    with OrmSession(engine) as session:
        existing = Session(
            started_at=datetime(2026, 8, 21, 21, tzinfo=UTC),
            ended_at=datetime(2026, 8, 21, 21, tzinfo=UTC),
            kind=SessionKind.STATIONARY,
            detector_key="EMT\x1f1",
        )
        session.add(existing)
        session.flush()
        session.add(
            _recording(
                "a",
                datetime(2026, 8, 21, 21, tzinfo=UTC),
                make="EMT",
                serial="1",
                session_id=existing.id,
            ),
        )
        session.commit()

        report = partition_sessions(session, session_gap=timedelta(hours=6))
        session.commit()

        assert report.created == 0
        assert report.extended == 0
