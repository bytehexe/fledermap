"""Session partitioning. Incremental, never renumbered (spec D7, section 7)."""

from __future__ import annotations

import bisect
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm import raiseload

from fledermap.store.models import Recording, Session, SessionMergeProposal


@dataclass
class SessionPartitionReport:
    created: int = 0
    extended: int = 0
    merge_proposals: int = 0


def _detector_key(make: str | None, serial: str | None) -> str:
    """Recordings sharing (make, serial) belong to the same detector.

    Both can be absent — not every source carries full metadata — so they're
    grouped together under one key rather than raising; a session still needs a
    home. `\\x1f` (ASCII Unit Separator — historically designed for exactly this,
    a field separator unlikely to appear in real text) separates the two
    fields: it cannot appear in either (both are plain text from GUANO/wamd/
    filename parsing), unlike a printable separator such as ":" which
    conceivably could appear inside a free-text `make`. NOT `\\x00`: Postgres
    (via psycopg2) unconditionally rejects a NUL byte in a text value —
    `ValueError: A string literal cannot contain NUL (0x00) characters.` —
    which would crash on essentially every session ever created, since
    `detector_key` is written to a real `text` column. Caught during Task 5's
    implementation (its own test needed a `detector_key` value and hit this
    immediately); verified directly against a live Postgres before fixing
    this plan. `\\x1f` round-trips through Postgres text with no issue.
    """
    return f"{make or ''}\x1f{serial or ''}"


def partition_sessions(
    db_session: OrmSession,
    *,
    session_gap: timedelta,
) -> SessionPartitionReport:
    """Assign every session_id-less recording to a session, per detector.

    Only rows with `session_id IS NULL` are touched; an existing session's
    `started_at`/`ended_at` may widen but its `id` never changes.

    A recording can extend an existing, already-persisted session from an
    earlier `derive` run in either direction — forward (the common case, a new
    recording after a session's `ended_at`) or backward (a late-arriving older
    recording, close only to what is currently the earliest known session).
    """
    report = SessionPartitionReport()
    # One proposal per session pair per run. Two different recordings in the
    # same run can each land between the same still-adjacent pair — the second
    # one bisects against `prev_session.ended_at` already widened by the first
    # — and would otherwise raise a duplicate proposal for the identical pair,
    # differing only in `bridging_recording_id`. Not corruption, but noise in
    # the human review queue. Declared once, outside the per-detector loop:
    # session ids are globally unique, so cross-detector collision is
    # impossible anyway. Cross-RUN duplication is already prevented by only
    # ever reprocessing `session_id IS NULL` recordings.
    proposed_pairs: set[tuple[int, int]] = set()

    unsessioned = db_session.scalars(
        select(Recording)
        # `Recording.identifications` is `lazy="selectin"` and unused here;
        # without an override every run eagerly loads every identification of
        # every unsessioned recording. `raiseload` so a future accidental
        # access fails loudly instead of becoming a silent N+1.
        .options(raiseload(Recording.identifications))
        .where(Recording.session_id.is_(None))
        .order_by(Recording.recorded_at),
    ).all()

    by_detector: dict[str, list[Recording]] = defaultdict(list)
    for recording in unsessioned:
        by_detector[_detector_key(recording.make, recording.serial)].append(recording)

    for key, recordings in by_detector.items():
        existing = list(
            db_session.scalars(
                select(Session)
                .where(Session.detector_key == key)
                .order_by(Session.started_at),
            ),
        )
        starts = [s.started_at for s in existing]

        for recording in recordings:
            idx = bisect.bisect_right(starts, recording.recorded_at)
            prev_session = existing[idx - 1] if idx > 0 else None
            next_session = existing[idx] if idx < len(existing) else None

            if (
                prev_session is not None
                and prev_session.ended_at >= recording.recorded_at
            ):
                recording.session_id = prev_session.id
                report.extended += 1
                continue

            gap_to_prev = (
                recording.recorded_at - prev_session.ended_at
                if prev_session is not None
                else None
            )
            gap_to_next = (
                next_session.started_at - recording.recorded_at
                if next_session is not None
                else None
            )

            joins_prev = gap_to_prev is not None and gap_to_prev <= session_gap
            joins_next = gap_to_next is not None and gap_to_next <= session_gap

            if joins_prev:
                assert prev_session is not None  # joins_prev implies this
                prev_session.ended_at = recording.recorded_at
                recording.session_id = prev_session.id
                report.extended += 1
                if joins_next:
                    assert next_session is not None  # joins_next implies this
                    pair = (prev_session.id, next_session.id)
                    if pair not in proposed_pairs:
                        proposed_pairs.add(pair)
                        db_session.add(
                            SessionMergeProposal(
                                session_a_id=prev_session.id,
                                session_b_id=next_session.id,
                                bridging_recording_id=recording.id,
                                detected_at=datetime.now(UTC),
                            ),
                        )
                        report.merge_proposals += 1
            elif joins_next:
                assert next_session is not None  # joins_next implies this
                next_session.started_at = recording.recorded_at
                # Keep the bisect cache in sync: `starts[idx]` mirrors
                # `next_session.started_at` (next_session IS existing[idx]).
                # Without this, a second recording in the same run that also
                # backward-extends this same session bisects against the
                # stale value.
                starts[idx] = next_session.started_at
                recording.session_id = next_session.id
                report.extended += 1
            else:
                new_session = Session(
                    started_at=recording.recorded_at,
                    ended_at=recording.recorded_at,
                    detector_key=key,
                )
                db_session.add(new_session)
                db_session.flush()
                recording.session_id = new_session.id
                report.created += 1

                insert_at = bisect.bisect_right(starts, new_session.started_at)
                existing.insert(insert_at, new_session)
                starts.insert(insert_at, new_session.started_at)

    return report
