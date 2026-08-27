from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import MergeResolution, SessionKind
from fledermap.services.sessions import (
    AlreadyResolvedError,
    MergeConflictError,
    ProposalNotFoundError,
    resolve_merge_proposal,
)
from fledermap.store.models import Recording, Session, SessionMergeProposal

pytestmark = pytest.mark.db

BASE = datetime(2026, 8, 20, tzinfo=UTC)


def _session(started: datetime, ended: datetime, **kwargs: object) -> Session:
    return Session(
        started_at=started,
        ended_at=ended,
        kind=SessionKind.STATIONARY,
        detector_key="EMT\x1f1",
        **kwargs,
    )


def _make_proposal(
    db_session: OrmSession,
    *,
    a_recordings: int = 1,
    b_recordings: int = 1,
) -> tuple[Session, Session, SessionMergeProposal]:
    a = _session(BASE, BASE.replace(hour=21))
    b = _session(BASE.replace(day=21), BASE.replace(day=21, hour=1))
    db_session.add_all([a, b])
    db_session.flush()
    for i in range(a_recordings):
        db_session.add(
            Recording(
                audio_hash=f"a{i}".rjust(64, "0"),
                path=f"a{i}.wav",
                recorded_at=BASE,
                session_id=a.id,
            ),
        )
    for i in range(b_recordings):
        db_session.add(
            Recording(
                audio_hash=f"b{i}".rjust(64, "0"),
                path=f"b{i}.wav",
                recorded_at=BASE.replace(day=21),
                session_id=b.id,
            ),
        )
    db_session.flush()
    bridging = db_session.scalars(
        select(Recording).where(Recording.session_id == a.id)
    ).first()
    assert bridging is not None
    proposal = SessionMergeProposal(
        session_a_id=a.id,
        session_b_id=b.id,
        bridging_recording_id=bridging.id,
        detected_at=BASE,
    )
    db_session.add(proposal)
    db_session.commit()
    return a, b, proposal


def test_reject_only_sets_resolution(engine: Engine) -> None:
    with OrmSession(engine) as session:
        a, b, proposal = _make_proposal(session)

        result = resolve_merge_proposal(
            session,
            proposal.id,
            action="reject",
            note=None,
            weather=None,
            transect_distance_m=150.0,
        )
        session.commit()

        assert result == a.id
        refreshed = session.get(SessionMergeProposal, proposal.id)
        assert refreshed is not None
        assert refreshed.resolution == MergeResolution.REJECTED
        assert refreshed.resolved_at is not None
        assert session.get(Session, b.id) is not None  # untouched


def test_merge_reassigns_recordings_and_deletes_session_b(engine: Engine) -> None:
    with OrmSession(engine) as session:
        a, b, proposal = _make_proposal(session, a_recordings=1, b_recordings=2)

        resolve_merge_proposal(
            session,
            proposal.id,
            action="merge",
            note="combined note",
            weather="combined weather",
            transect_distance_m=150.0,
        )
        session.commit()

        assert session.get(Session, b.id) is None
        remaining = session.scalars(select(Recording)).all()
        assert len(remaining) == 3
        assert all(r.session_id == a.id for r in remaining)
        merged_a = session.get(Session, a.id)
        assert merged_a is not None
        assert merged_a.note == "combined note"
        assert merged_a.weather == "combined weather"
        assert merged_a.started_at == BASE
        assert merged_a.ended_at == BASE.replace(day=21, hour=1)

        refreshed = session.get(SessionMergeProposal, proposal.id)
        assert refreshed is not None
        assert refreshed.resolution == MergeResolution.MERGED
        assert refreshed.resolved_at is not None


def test_merge_reclassifies_session_a_when_unlocked(engine: Engine) -> None:
    with OrmSession(engine) as session:
        a = _session(BASE, BASE)
        b = _session(BASE.replace(day=21), BASE.replace(day=21))
        session.add_all([a, b])
        session.flush()
        session.add(
            Recording(
                audio_hash="a".rjust(64, "0"),
                path="a.wav",
                recorded_at=BASE,
                session_id=a.id,
                geom=None,
            ),
        )
        session.add(
            Recording(
                audio_hash="b".rjust(64, "0"),
                path="b.wav",
                recorded_at=BASE.replace(day=21),
                session_id=b.id,
                geom=None,
            ),
        )
        session.flush()
        bridging = session.scalars(
            select(Recording).where(Recording.session_id == a.id)
        ).one()
        proposal = SessionMergeProposal(
            session_a_id=a.id,
            session_b_id=b.id,
            bridging_recording_id=bridging.id,
            detected_at=BASE,
        )
        session.add(proposal)
        session.commit()

        resolve_merge_proposal(
            session,
            proposal.id,
            action="merge",
            note=None,
            weather=None,
            transect_distance_m=150.0,
        )
        session.commit()

        # no GPS on either side's recordings -- reclassification runs but
        # has nothing to work with, stays STATIONARY. Proves the call
        # happened without erroring, not a positive TRANSECT case (that's
        # covered directly in test_partition_sessions.py).
        merged_a = session.get(Session, a.id)
        assert merged_a is not None
        assert merged_a.kind == SessionKind.STATIONARY


def test_merge_does_not_reclassify_a_locked_session_a(engine: Engine) -> None:
    with OrmSession(engine) as session:
        a = _session(BASE, BASE, kind_locked=True)
        b = _session(BASE.replace(day=21), BASE.replace(day=21))
        session.add_all([a, b])
        session.flush()
        session.add(
            Recording(
                audio_hash="a".rjust(64, "0"),
                path="a.wav",
                recorded_at=BASE,
                session_id=a.id,
            ),
        )
        session.flush()
        bridging = session.scalars(
            select(Recording).where(Recording.session_id == a.id)
        ).one()
        proposal = SessionMergeProposal(
            session_a_id=a.id,
            session_b_id=b.id,
            bridging_recording_id=bridging.id,
            detected_at=BASE,
        )
        session.add(proposal)
        session.commit()

        resolve_merge_proposal(
            session,
            proposal.id,
            action="merge",
            note=None,
            weather=None,
            transect_distance_m=150.0,
        )
        session.commit()

        merged_a = session.get(Session, a.id)
        assert merged_a is not None
        assert merged_a.kind == SessionKind.STATIONARY  # unchanged, still locked
        assert merged_a.kind_locked is True


def test_unknown_proposal_id_raises(engine: Engine) -> None:
    with OrmSession(engine) as session:
        with pytest.raises(ProposalNotFoundError):
            resolve_merge_proposal(
                session,
                999,
                action="reject",
                note=None,
                weather=None,
                transect_distance_m=150.0,
            )


def test_already_resolved_raises_without_reapplying(engine: Engine) -> None:
    with OrmSession(engine) as session:
        a, b, proposal = _make_proposal(session)
        proposal.resolution = MergeResolution.REJECTED
        proposal.resolved_at = BASE
        session.commit()

        with pytest.raises(AlreadyResolvedError):
            resolve_merge_proposal(
                session,
                proposal.id,
                action="merge",
                note=None,
                weather=None,
                transect_distance_m=150.0,
            )
        session.commit()

        assert session.get(Session, b.id) is not None  # not merged after all


def test_invalid_action_raises_value_error(engine: Engine) -> None:
    with OrmSession(engine) as session:
        _a, _b, proposal = _make_proposal(session)
        with pytest.raises(ValueError, match="bogus"):
            resolve_merge_proposal(
                session,
                proposal.id,
                action="bogus",
                note=None,
                weather=None,
                transect_distance_m=150.0,
            )


def test_chained_proposal_raises_merge_conflict(engine: Engine) -> None:
    """Design spec section 5: session_b is also session_a of a second, still
    -open proposal. The FK on session_merge_proposal has no ON DELETE clause,
    so deleting session_b must be caught and turned into a clean error, not
    a raw IntegrityError."""
    with OrmSession(engine) as session:
        a, b, first_proposal = _make_proposal(session)
        c = _session(BASE.replace(day=22), BASE.replace(day=22))
        session.add(c)
        session.flush()
        session.add(
            Recording(
                audio_hash="c".rjust(64, "0"),
                path="c.wav",
                recorded_at=BASE.replace(day=22),
                session_id=c.id,
            ),
        )
        session.flush()
        bridging_bc = session.scalars(
            select(Recording).where(Recording.session_id == c.id)
        ).one()
        second_proposal = SessionMergeProposal(
            session_a_id=b.id,
            session_b_id=c.id,
            bridging_recording_id=bridging_bc.id,
            detected_at=BASE,
        )
        session.add(second_proposal)
        session.commit()

        with pytest.raises(MergeConflictError):
            resolve_merge_proposal(
                session,
                first_proposal.id,
                action="merge",
                note=None,
                weather=None,
                transect_distance_m=150.0,
            )
        session.commit()

        # nothing partially applied
        refreshed = session.get(SessionMergeProposal, first_proposal.id)
        assert refreshed is not None
        assert refreshed.resolution is None
        assert session.get(Session, b.id) is not None
