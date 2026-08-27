from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import MergeResolution, SessionKind
from fledermap.services.sessions import (
    count_open_proposals,
    filtered_sessions,
    list_detectors,
    open_proposal_session_ids,
    session_detail,
)
from fledermap.store.models import Recording, Session, SessionMergeProposal

pytestmark = pytest.mark.db


def _session(detector_key: str, started: datetime, ended: datetime) -> Session:
    return Session(
        started_at=started,
        ended_at=ended,
        kind=SessionKind.STATIONARY,
        detector_key=detector_key,
    )


def test_filtered_sessions_orders_newest_first(engine: Engine) -> None:
    with OrmSession(engine) as session:
        base = datetime(2026, 8, 20, tzinfo=UTC)
        session.add(_session("EMT\x1f1", base, base))
        session.add(_session("EMT\x1f1", base.replace(day=25), base.replace(day=25)))
        session.commit()

        rows = filtered_sessions(session)
        assert [row.session.started_at.day for row in rows] == [25, 20]


def test_filtered_sessions_by_detector_substring(engine: Engine) -> None:
    with OrmSession(engine) as session:
        base = datetime(2026, 8, 20, tzinfo=UTC)
        session.add(_session("EMT\x1f1", base, base))
        session.add(_session("Kaleidoscope\x1f2", base, base))
        session.commit()

        rows = filtered_sessions(session, detector="EMT")
        assert len(rows) == 1
        assert rows[0].session.detector_key == "EMT\x1f1"


def test_filtered_sessions_detector_percent_is_escaped_not_a_wildcard(
    engine: Engine,
) -> None:
    """Fix (Minor D): an unescaped `%`/`_` in the detector filter is
    interpreted as an ILIKE wildcard rather than a literal character -- a
    bare `%` would otherwise match every session. None of the fixture's
    `detector_key` values contain a literal `%`, so a working escape means
    this filter returns nothing."""
    with OrmSession(engine) as session:
        base = datetime(2026, 8, 20, tzinfo=UTC)
        session.add(_session("EMT\x1f1", base, base))
        session.add(_session("Kaleidoscope\x1f2", base, base))
        session.commit()

        rows = filtered_sessions(session, detector="%")
        assert rows == []


def test_filtered_sessions_by_date_range(engine: Engine) -> None:
    with OrmSession(engine) as session:
        base = datetime(2026, 8, 20, tzinfo=UTC)
        session.add(_session("EMT\x1f1", base, base))
        session.add(_session("EMT\x1f1", base.replace(day=1), base.replace(day=1)))
        session.commit()

        rows = filtered_sessions(session, date_from=base.replace(day=10))
        assert len(rows) == 1
        assert rows[0].session.started_at.day == 20


def test_filtered_sessions_reports_recording_count(engine: Engine) -> None:
    with OrmSession(engine) as session:
        base = datetime(2026, 8, 20, tzinfo=UTC)
        s = _session("EMT\x1f1", base, base)
        session.add(s)
        session.flush()
        session.add(
            Recording(
                audio_hash="a".rjust(64, "0"),
                path="a.wav",
                recorded_at=base,
                session_id=s.id,
            ),
        )
        session.add(
            Recording(
                audio_hash="b".rjust(64, "0"),
                path="b.wav",
                recorded_at=base,
                session_id=s.id,
            ),
        )
        session.commit()

        rows = filtered_sessions(session)
        assert rows[0].recording_count == 2


def test_filtered_sessions_recording_count_zero_when_none(engine: Engine) -> None:
    with OrmSession(engine) as session:
        base = datetime(2026, 8, 20, tzinfo=UTC)
        session.add(_session("EMT\x1f1", base, base))
        session.commit()

        rows = filtered_sessions(session)
        assert rows[0].recording_count == 0


def test_open_proposals_only_filters_to_sessions_with_an_open_proposal(
    engine: Engine,
) -> None:
    with OrmSession(engine) as session:
        base = datetime(2026, 8, 20, tzinfo=UTC)
        a = _session("EMT\x1f1", base, base)
        b = _session("EMT\x1f1", base.replace(day=21), base.replace(day=21))
        untouched = _session("EMT\x1f2", base, base)
        session.add_all([a, b, untouched])
        session.flush()
        session.add(
            Recording(
                audio_hash="x".rjust(64, "0"),
                path="x.wav",
                recorded_at=base,
                session_id=a.id,
            ),
        )
        session.flush()
        bridging = session.scalars(select(Recording)).one()
        session.add(
            SessionMergeProposal(
                session_a_id=a.id,
                session_b_id=b.id,
                bridging_recording_id=bridging.id,
                detected_at=base,
            ),
        )
        session.commit()

        rows = filtered_sessions(session, open_proposals_only=True)
        assert {row.session.id for row in rows} == {a.id, b.id}


def test_count_open_proposals_counts_proposals_not_sessions(engine: Engine) -> None:
    """Distinct from `open_proposal_session_ids`'s set of touched session
    ids: two DIFFERENT open proposals sharing no sessions must count as 2,
    not conflate with the number of sessions involved."""
    with OrmSession(engine) as session:
        base = datetime(2026, 8, 20, tzinfo=UTC)
        a, b = (
            _session("EMT\x1f1", base, base),
            _session("EMT\x1f1", base.replace(day=21), base.replace(day=21)),
        )
        c, d = (
            _session("EMT\x1f2", base, base),
            _session("EMT\x1f2", base.replace(day=21), base.replace(day=21)),
        )
        session.add_all([a, b, c, d])
        session.flush()
        session.add_all(
            [
                Recording(
                    audio_hash="x".rjust(64, "0"),
                    path="x.wav",
                    recorded_at=base,
                    session_id=a.id,
                ),
                Recording(
                    audio_hash="y".rjust(64, "0"),
                    path="y.wav",
                    recorded_at=base,
                    session_id=c.id,
                ),
            ],
        )
        session.flush()
        bridging_a = session.scalars(
            select(Recording).where(Recording.session_id == a.id),
        ).one()
        bridging_c = session.scalars(
            select(Recording).where(Recording.session_id == c.id),
        ).one()
        session.add_all(
            [
                SessionMergeProposal(
                    session_a_id=a.id,
                    session_b_id=b.id,
                    bridging_recording_id=bridging_a.id,
                    detected_at=base,
                ),
                SessionMergeProposal(
                    session_a_id=c.id,
                    session_b_id=d.id,
                    bridging_recording_id=bridging_c.id,
                    detected_at=base,
                ),
            ],
        )
        session.commit()

        assert count_open_proposals(session) == 2


def test_count_open_proposals_excludes_resolved(engine: Engine) -> None:
    with OrmSession(engine) as session:
        base = datetime(2026, 8, 20, tzinfo=UTC)
        a = _session("EMT\x1f1", base, base)
        b = _session("EMT\x1f1", base.replace(day=21), base.replace(day=21))
        session.add_all([a, b])
        session.flush()
        session.add(
            Recording(
                audio_hash="x".rjust(64, "0"),
                path="x.wav",
                recorded_at=base,
                session_id=a.id,
            ),
        )
        session.flush()
        bridging = session.scalars(select(Recording)).one()
        session.add(
            SessionMergeProposal(
                session_a_id=a.id,
                session_b_id=b.id,
                bridging_recording_id=bridging.id,
                detected_at=base,
                resolution=MergeResolution.REJECTED,
                resolved_at=base,
            ),
        )
        session.commit()

        assert count_open_proposals(session) == 0


def test_list_detectors_returns_distinct_sorted_keys(engine: Engine) -> None:
    with OrmSession(engine) as session:
        base = datetime(2026, 8, 20, tzinfo=UTC)
        session.add_all(
            [
                _session("Kaleidoscope\x1f2", base, base),
                _session("EMT\x1f1", base, base),
                _session("EMT\x1f1", base.replace(day=21), base.replace(day=21)),
            ],
        )
        session.commit()

        assert list_detectors(session) == ["EMT\x1f1", "Kaleidoscope\x1f2"]


def test_list_detectors_excludes_the_all_blank_key(engine: Engine) -> None:
    with OrmSession(engine) as session:
        base = datetime(2026, 8, 20, tzinfo=UTC)
        session.add_all(
            [
                _session("EMT\x1f1", base, base),
                _session("\x1f", base, base),
            ],
        )
        session.commit()

        assert list_detectors(session) == ["EMT\x1f1"]


def test_resolved_proposal_does_not_count_as_open(engine: Engine) -> None:
    with OrmSession(engine) as session:
        base = datetime(2026, 8, 20, tzinfo=UTC)
        a = _session("EMT\x1f1", base, base)
        b = _session("EMT\x1f1", base.replace(day=21), base.replace(day=21))
        session.add_all([a, b])
        session.flush()
        session.add(
            Recording(
                audio_hash="x".rjust(64, "0"),
                path="x.wav",
                recorded_at=base,
                session_id=a.id,
            ),
        )
        session.flush()
        bridging = session.scalars(select(Recording)).one()
        session.add(
            SessionMergeProposal(
                session_a_id=a.id,
                session_b_id=b.id,
                bridging_recording_id=bridging.id,
                detected_at=base,
                resolution=MergeResolution.REJECTED,
                resolved_at=base,
            ),
        )
        session.commit()

        assert open_proposal_session_ids(session) == set()


def test_filtered_sessions_runs_with_multiple_rows(engine: Engine) -> None:
    """Not a test of the MAX_SESSIONS=200 cap itself -- exercising that
    boundary would mean seeding 200+ rows for a check the design spec
    explicitly deprioritized ("revisit if a real dataset ever approaches the
    cap"). This just confirms the unfiltered query returns every matching
    row, as a sanity check that .limit() didn't get applied too early."""
    with OrmSession(engine) as session:
        base = datetime(2026, 8, 20, tzinfo=UTC)
        for day in range(1, 3):
            session.add(
                _session("EMT\x1f1", base.replace(day=day), base.replace(day=day))
            )
        session.commit()

        rows = filtered_sessions(session)
        assert len(rows) == 2


def test_session_detail_none_when_not_found(engine: Engine) -> None:
    with OrmSession(engine) as session:
        assert session_detail(session, 999) is None


def test_session_detail_lists_recordings_oldest_first(engine: Engine) -> None:
    with OrmSession(engine) as session:
        base = datetime(2026, 8, 20, tzinfo=UTC)
        s = _session("EMT\x1f1", base, base)
        session.add(s)
        session.flush()
        session.add(
            Recording(
                audio_hash="b".rjust(64, "0"),
                path="b.wav",
                recorded_at=base.replace(hour=23),
                session_id=s.id,
            ),
        )
        session.add(
            Recording(
                audio_hash="a".rjust(64, "0"),
                path="a.wav",
                recorded_at=base.replace(hour=20),
                session_id=s.id,
            ),
        )
        session.commit()

        detail = session_detail(session, s.id)
        assert detail is not None
        assert [r.audio_hash[-1] for r in detail.recordings] == ["a", "b"]


def test_session_detail_no_open_proposals(engine: Engine) -> None:
    with OrmSession(engine) as session:
        base = datetime(2026, 8, 20, tzinfo=UTC)
        s = _session("EMT\x1f1", base, base)
        session.add(s)
        session.commit()

        detail = session_detail(session, s.id)
        assert detail is not None
        assert detail.open_proposals == []


def test_session_detail_shows_open_proposal_from_either_side(engine: Engine) -> None:
    with OrmSession(engine) as session:
        base = datetime(2026, 8, 20, tzinfo=UTC)
        a = _session("EMT\x1f1", base, base)
        b = _session("EMT\x1f1", base.replace(day=21), base.replace(day=21))
        session.add_all([a, b])
        session.flush()
        session.add(
            Recording(
                audio_hash="x".rjust(64, "0"),
                path="x.wav",
                recorded_at=base,
                session_id=a.id,
            ),
        )
        session.flush()
        bridging = session.scalars(select(Recording)).one()
        session.add(
            SessionMergeProposal(
                session_a_id=a.id,
                session_b_id=b.id,
                bridging_recording_id=bridging.id,
                detected_at=base,
            ),
        )
        session.commit()

        detail_a = session_detail(session, a.id)
        assert detail_a is not None
        assert len(detail_a.open_proposals) == 1
        assert detail_a.open_proposals[0].counterpart.id == b.id

        detail_b = session_detail(session, b.id)
        assert detail_b is not None
        assert len(detail_b.open_proposals) == 1
        assert detail_b.open_proposals[0].counterpart.id == a.id


def test_session_detail_shows_multiple_open_proposals(engine: Engine) -> None:
    with OrmSession(engine) as session:
        base = datetime(2026, 8, 20, tzinfo=UTC)
        a = _session("EMT\x1f1", base, base)
        b = _session("EMT\x1f1", base.replace(day=21), base.replace(day=21))
        c = _session("EMT\x1f1", base.replace(day=22), base.replace(day=22))
        session.add_all([a, b, c])
        session.flush()
        session.add(
            Recording(
                audio_hash="x".rjust(64, "0"),
                path="x.wav",
                recorded_at=base,
                session_id=a.id,
            ),
        )
        session.add(
            Recording(
                audio_hash="y".rjust(64, "0"),
                path="y.wav",
                recorded_at=base.replace(day=22),
                session_id=c.id,
            ),
        )
        session.flush()
        bridging_ab = session.scalars(
            select(Recording).where(Recording.session_id == a.id),
        ).one()
        bridging_bc = session.scalars(
            select(Recording).where(Recording.session_id == c.id),
        ).one()
        session.add(
            SessionMergeProposal(
                session_a_id=a.id,
                session_b_id=b.id,
                bridging_recording_id=bridging_ab.id,
                detected_at=base,
            ),
        )
        session.add(
            SessionMergeProposal(
                session_a_id=b.id,
                session_b_id=c.id,
                bridging_recording_id=bridging_bc.id,
                detected_at=base,
            ),
        )
        session.commit()

        detail_b = session_detail(session, b.id)
        assert detail_b is not None
        assert len(detail_b.open_proposals) == 2
        counterpart_ids = {op.counterpart.id for op in detail_b.open_proposals}
        assert counterpart_ids == {a.id, c.id}


def test_session_detail_excludes_resolved_proposals(engine: Engine) -> None:
    with OrmSession(engine) as session:
        base = datetime(2026, 8, 20, tzinfo=UTC)
        a = _session("EMT\x1f1", base, base)
        b = _session("EMT\x1f1", base.replace(day=21), base.replace(day=21))
        session.add_all([a, b])
        session.flush()
        session.add(
            Recording(
                audio_hash="x".rjust(64, "0"),
                path="x.wav",
                recorded_at=base,
                session_id=a.id,
            ),
        )
        session.flush()
        bridging = session.scalars(
            select(Recording).where(Recording.session_id == a.id),
        ).one()
        session.add(
            SessionMergeProposal(
                session_a_id=a.id,
                session_b_id=b.id,
                bridging_recording_id=bridging.id,
                detected_at=base,
                resolution=MergeResolution.REJECTED,
                resolved_at=base,
            ),
        )
        session.commit()

        detail_a = session_detail(session, a.id)
        assert detail_a is not None
        assert detail_a.open_proposals == []
