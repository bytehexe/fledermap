"""Sessions list/detail query and mutation logic (design spec
2026-08-27-fledermap-phase5b-sessions-design.md), mirroring
`services/map_query.py`'s existing shape: filtering runs in SQL where the
field lives directly on `Session`, assembled results are dataclasses so
templates never make N+1 lookups."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as OrmSession

from fledermap.derive.sessions import reclassify_session
from fledermap.domain.codes import MergeResolution
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
    open_ids: set[int] | None = None,
) -> Sequence[SessionListRow]:
    stmt = select(AnnotationSession).order_by(AnnotationSession.started_at.desc())
    if detector:
        # A literal `%`/`_` in user input is otherwise interpreted as an
        # ILIKE wildcard rather than a literal character -- e.g. a bare `%`
        # would match every session. Postgres's default ILIKE escape
        # character is backslash, so no ESCAPE clause override is needed.
        escaped = detector.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        stmt = stmt.where(AnnotationSession.detector_key.ilike(f"%{escaped}%"))
    if date_from is not None:
        stmt = stmt.where(AnnotationSession.started_at >= date_from)
    if date_to is not None:
        stmt = stmt.where(AnnotationSession.started_at <= date_to)
    if open_proposals_only:
        if open_ids is None:
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


@dataclass(frozen=True)
class OpenProposal:
    """One unresolved `SessionMergeProposal` touching the session being
    viewed, paired with the *other* session it names -- the detail page
    banner never needs to re-derive which side is which."""

    proposal: SessionMergeProposal
    counterpart: AnnotationSession


@dataclass(frozen=True)
class SessionDetail:
    session: AnnotationSession
    recordings: Sequence[Recording]
    open_proposals: Sequence[OpenProposal]


def session_detail(db_session: OrmSession, session_id: int) -> SessionDetail | None:
    """Assemble session detail for `/sessions/{id}`: the session, every
    recording currently assigned to it (oldest first), and every unresolved
    merge proposal naming it from either side (design spec section 5's
    chained-proposal case: a session can appear in more than one)."""
    session_obj = db_session.get(AnnotationSession, session_id)
    if session_obj is None:
        return None

    recordings = db_session.scalars(
        select(Recording)
        .where(Recording.session_id == session_id)
        .order_by(Recording.recorded_at),
    ).all()

    proposals = db_session.scalars(
        select(SessionMergeProposal).where(
            SessionMergeProposal.resolution.is_(None),
            (SessionMergeProposal.session_a_id == session_id)
            | (SessionMergeProposal.session_b_id == session_id),
        ),
    ).all()
    open_proposals = []
    for proposal in proposals:
        counterpart_id = (
            proposal.session_b_id
            if proposal.session_a_id == session_id
            else proposal.session_a_id
        )
        counterpart = db_session.get(AnnotationSession, counterpart_id)
        assert counterpart is not None, "FK guarantees the counterpart session exists"
        open_proposals.append(OpenProposal(proposal=proposal, counterpart=counterpart))

    return SessionDetail(
        session=session_obj,
        recordings=recordings,
        open_proposals=open_proposals,
    )


class ProposalNotFoundError(Exception):
    """No `SessionMergeProposal` exists with the given id."""


class AlreadyResolvedError(Exception):
    """The proposal was already resolved (merged or rejected), by this
    request or a concurrent one -- design spec section 10's "already
    resolved by someone else" case. Checked before any mutation, so this
    never partially re-applies a merge."""


class MergeConflictError(Exception):
    """`session_b` is still referenced by another open merge proposal, so it
    can't be deleted until that one is resolved first (design spec section
    5's chained-proposal edge case) -- `session_merge_proposal`'s FK columns
    have no `ON DELETE`, so Postgres raises `IntegrityError` rather than
    silently orphaning the other proposal; this turns that into a clean,
    named error instead of a raw 500."""


def resolve_merge_proposal(
    db_session: OrmSession,
    proposal_id: int,
    *,
    action: str,
    note: str | None,
    weather: str | None,
    transect_distance_m: float,
) -> int:
    """Accept ("merge") or reject a `SessionMergeProposal`. Returns
    `session_a_id` -- always safe to redirect to afterward, since
    `session_a` is never deleted (only `session_b` is, and only on a
    successful merge). Does not commit; the caller controls the transaction
    boundary, matching every other `services/` function in this project."""
    if action not in ("merge", "reject"):
        msg = f"{action!r} is not a valid action (expected 'merge' or 'reject')."
        raise ValueError(msg)

    proposal = db_session.get(SessionMergeProposal, proposal_id)
    if proposal is None:
        raise ProposalNotFoundError(proposal_id)
    if proposal.resolution is not None:
        msg = "This merge proposal was already resolved."
        raise AlreadyResolvedError(msg)

    if action == "reject":
        proposal.resolution = MergeResolution.REJECTED
        proposal.resolved_at = datetime.now(UTC)
        return proposal.session_a_id

    session_a = db_session.get(AnnotationSession, proposal.session_a_id)
    session_b = db_session.get(AnnotationSession, proposal.session_b_id)
    assert session_a is not None, "FK guarantees this row exists"
    assert session_b is not None, "FK guarantees this row exists"

    db_session.execute(
        update(Recording)
        .where(Recording.session_id == session_b.id)
        .values(session_id=session_a.id),
    )
    session_a.started_at = min(session_a.started_at, session_b.started_at)
    session_a.ended_at = max(session_a.ended_at, session_b.ended_at)
    if note is not None:
        session_a.note = note
    elif session_b.note:
        session_a.note = (
            f"{session_a.note}\n---\n{session_b.note}"
            if session_a.note
            else session_b.note
        )
    if weather is not None:
        session_a.weather = weather
    elif session_b.weather:
        session_a.weather = (
            f"{session_a.weather}\n---\n{session_b.weather}"
            if session_a.weather
            else session_b.weather
        )
    reclassify_session(db_session, session_a, transect_distance_m=transect_distance_m)

    # This proposal's own `session_b_id` still points at `session_b`, and
    # the FK has no `ON DELETE` clause -- deleting `session_b` below would
    # be rejected by this row alone, even with no chained proposal in the
    # picture. Repoint it at `session_a` first so only a genuinely OTHER
    # open proposal (design spec section 5's chained case) can still block
    # the delete and surface as `MergeConflictError`.
    proposal.session_b_id = session_a.id

    # Any OTHER proposal -- from a past run, possibly already resolved --
    # that also names `session_b` on either side has the same problem as
    # this proposal's own `session_b_id` above: the FK has no `ON DELETE`,
    # so it would block the delete below regardless of whether it's still
    # open. An open one must keep blocking (design spec section 5's
    # chained-proposal case, still correctly surfaced as
    # `MergeConflictError`) -- but an ALREADY-RESOLVED one (REJECTED or
    # MERGED) can never be un-resolved through the UI, so leaving its
    # dangling reference to `session_b` would permanently block every
    # future merge of `session_b` with an error message telling the user to
    # resolve something that no longer appears anywhere. Repoint those the
    # same way `session_b` itself no longer exists once merged.
    other_proposals = db_session.scalars(
        select(SessionMergeProposal).where(
            SessionMergeProposal.id != proposal.id,
            or_(
                SessionMergeProposal.session_a_id == session_b.id,
                SessionMergeProposal.session_b_id == session_b.id,
            ),
        ),
    ).all()
    for other in other_proposals:
        if other.resolution is None:
            continue
        if other.session_a_id == session_b.id:
            other.session_a_id = session_a.id
        if other.session_b_id == session_b.id:
            other.session_b_id = session_a.id

    db_session.delete(session_b)
    try:
        db_session.flush()
    except IntegrityError as exc:
        db_session.rollback()
        msg = (
            "Can't complete this merge: the other session is still part of "
            "another pending merge proposal. Resolve that one first."
        )
        raise MergeConflictError(msg) from exc

    proposal.resolution = MergeResolution.MERGED
    proposal.resolved_at = datetime.now(UTC)
    return session_a.id
