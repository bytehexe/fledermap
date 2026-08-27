"""Sessions list/detail query and mutation logic (design spec
2026-08-27-fledermap-phase5b-sessions-design.md), mirroring
`services/map_query.py`'s existing shape: filtering runs in SQL where the
field lives directly on `Session`, assembled results are dataclasses so
templates never make N+1 lookups."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session as OrmSession

from fledermap.store.models import Recording, SessionMergeProposal
from fledermap.store.models import Session as AnnotationSession

# Design spec section 3: a capped LIMIT stands in for real pagination at this
# project's established real-deployment scale (a handful of detectors).
MAX_SESSIONS = 200


@dataclass(frozen=True)
class SessionListRow:
    """One row of the `/sessions` list -- `Session` plus its recording count,
    assembled here rather than via a `relationship()` (none exists; adding
    one purely to satisfy a list-page count would let template code drive an
    N+1 query per row)."""

    session: AnnotationSession
    recording_count: int


def open_proposal_session_ids(db_session: OrmSession) -> set[int]:
    """Every session id currently part of at least one unresolved
    `SessionMergeProposal`, either side."""
    a_ids = db_session.scalars(
        select(SessionMergeProposal.session_a_id).where(
            SessionMergeProposal.resolution.is_(None),
        ),
    )
    b_ids = db_session.scalars(
        select(SessionMergeProposal.session_b_id).where(
            SessionMergeProposal.resolution.is_(None),
        ),
    )
    return set(a_ids) | set(b_ids)


def filtered_sessions(
    db_session: OrmSession,
    *,
    detector: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    open_proposals_only: bool = False,
) -> Sequence[SessionListRow]:
    stmt = select(AnnotationSession).order_by(AnnotationSession.started_at.desc())
    if detector:
        stmt = stmt.where(AnnotationSession.detector_key.ilike(f"%{detector}%"))
    if date_from is not None:
        stmt = stmt.where(AnnotationSession.started_at >= date_from)
    if date_to is not None:
        stmt = stmt.where(AnnotationSession.started_at <= date_to)
    if open_proposals_only:
        open_ids = open_proposal_session_ids(db_session)
        if not open_ids:
            return []
        stmt = stmt.where(AnnotationSession.id.in_(open_ids))
    stmt = stmt.limit(MAX_SESSIONS)

    sessions = list(db_session.scalars(stmt).all())
    if not sessions:
        return []

    session_ids = [s.id for s in sessions]
    counts: dict[int, int] = {}
    for session_id, count in db_session.execute(
        select(Recording.session_id, func.count(Recording.id))
        .where(Recording.session_id.in_(session_ids))
        .group_by(Recording.session_id),
    ):
        if session_id is not None:
            counts[session_id] = count
    return [
        SessionListRow(session=s, recording_count=counts.get(s.id, 0)) for s in sessions
    ]
