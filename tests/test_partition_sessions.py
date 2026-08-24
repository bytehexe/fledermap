from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session as OrmSession

from fledermap.derive.sessions import partition_sessions
from fledermap.domain.codes import SessionKind
from fledermap.store.models import Recording, Session, SessionMergeProposal

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
            _recording(
                "a", datetime(2026, 8, 21, 21, tzinfo=UTC), make="EMT", serial="1"
            ),
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
        assert len(session.scalars(select(Session)).all()) == 2


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


def test_two_recordings_backward_extending_the_same_session_both_land_inside_it(
    engine: Engine,
) -> None:
    """Regression: a stale bisect cache could silently narrow started_at past
    an earlier recording's own timestamp after a SECOND backward-extend in
    the same run (Task 6 review, Important 1)."""
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
        # Y (2h before later.started_at) then X (1h before) — both within
        # the 6h gap, processed in ascending recorded_at order.
        y = _recording("y", base - timedelta(hours=2), make="EMT", serial="1")
        x = _recording("x", base - timedelta(hours=1), make="EMT", serial="1")
        session.add_all([y, x])
        session.commit()

        report = partition_sessions(session, session_gap=timedelta(hours=6))
        session.commit()

        assert report.created == 0
        assert report.extended == 2
        assert len(session.scalars(select(Session)).all()) == 1
        session.refresh(later)
        session.refresh(y)
        assert later.started_at <= y.recorded_at <= later.ended_at


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


def test_recording_between_two_sessions_within_gap_of_both_raises_a_proposal(
    engine: Engine,
) -> None:
    with OrmSession(engine) as session:
        base = datetime(2026, 8, 21, 21, tzinfo=UTC)
        early = Session(
            started_at=base,
            ended_at=base,
            kind=SessionKind.STATIONARY,
            detector_key="EMT\x1f1",
        )
        late = Session(
            started_at=base + timedelta(hours=8),
            ended_at=base + timedelta(hours=8),
            kind=SessionKind.STATIONARY,
            detector_key="EMT\x1f1",
        )
        session.add_all([early, late])
        session.flush()
        # 4h after `early`, 4h before `late` — within a 6h gap of both.
        bridging = _recording(
            "a",
            base + timedelta(hours=4),
            make="EMT",
            serial="1",
        )
        session.add(bridging)
        session.commit()

        report = partition_sessions(session, session_gap=timedelta(hours=6))
        session.commit()

        assert report.merge_proposals == 1
        assert len(session.scalars(select(Session)).all()) == 2  # no third session
        session.refresh(bridging)
        assert bridging.session_id == early.id  # joins the earlier of the two

        proposal = session.scalars(select(SessionMergeProposal)).one()
        assert proposal.session_a_id == early.id
        assert proposal.session_b_id == late.id
        assert proposal.bridging_recording_id == bridging.id
        assert proposal.resolved_at is None
        assert proposal.resolution is None


def test_two_bridging_recordings_raise_only_one_proposal_for_the_pair(
    engine: Engine,
) -> None:
    """Regression (whole-branch review): two recordings in the SAME run can each
    land between the same still-adjacent pair — the second bisects against a
    `prev_session.ended_at` the first already widened — and each raised its own
    proposal for the identical pair, differing only in the bridging recording.
    Not corruption, but duplicate noise in the human review queue."""
    with OrmSession(engine) as session:
        base = datetime(2026, 8, 21, 21, tzinfo=UTC)
        early = Session(
            started_at=base,
            ended_at=base,
            kind=SessionKind.STATIONARY,
            detector_key="EMT\x1f1",
        )
        late = Session(
            started_at=base + timedelta(hours=11),
            ended_at=base + timedelta(hours=11),
            kind=SessionKind.STATIONARY,
            detector_key="EMT\x1f1",
        )
        session.add_all([early, late])
        session.flush()
        # First: 5h after `early`, 6h before `late` — within a 6h gap of both.
        first = _recording("a", base + timedelta(hours=5), make="EMT", serial="1")
        # Second: 1h after `early`'s widened ended_at, 5h before `late` —
        # bridges the same still-adjacent pair all over again.
        second = _recording("b", base + timedelta(hours=6), make="EMT", serial="1")
        session.add_all([first, second])
        session.commit()

        report = partition_sessions(session, session_gap=timedelta(hours=6))
        session.commit()

        assert report.extended == 2
        assert report.created == 0
        assert report.merge_proposals == 1
        proposal = session.scalars(select(SessionMergeProposal)).one()
        assert proposal.session_a_id == early.id
        assert proposal.session_b_id == late.id
        # The FIRST bridging recording is the one recorded.
        assert proposal.bridging_recording_id == first.id


def test_recording_close_to_only_one_neighbor_does_not_raise_a_proposal(
    engine: Engine,
) -> None:
    with OrmSession(engine) as session:
        base = datetime(2026, 8, 21, 21, tzinfo=UTC)
        early = Session(
            started_at=base,
            ended_at=base,
            kind=SessionKind.STATIONARY,
            detector_key="EMT\x1f1",
        )
        late = Session(
            started_at=base + timedelta(hours=20),
            ended_at=base + timedelta(hours=20),
            kind=SessionKind.STATIONARY,
            detector_key="EMT\x1f1",
        )
        session.add_all([early, late])
        session.flush()
        # 1h after `early`, 19h before `late` — within gap of only `early`.
        session.add(_recording("a", base + timedelta(hours=1), make="EMT", serial="1"))
        session.commit()

        report = partition_sessions(session, session_gap=timedelta(hours=6))
        session.commit()

        assert report.merge_proposals == 0
        assert session.scalars(select(SessionMergeProposal)).all() == []
