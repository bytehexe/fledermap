"""Writes IdSource.MANUAL identification rows from the recording-details
page's classifier box (design spec 2026-09-05-fledermap-manual-classification-
design.md, §4). The only place in the codebase that writes IdSource.MANUAL --
every other source is written by services/ingest.py's commit_scan during a
scan, never here."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import IdSource, Verdict
from fledermap.store.models import Identification, Recording


def set_manual_classification(
    session: OrmSession,
    recording: Recording,
    *,
    verdict: Verdict | None,
    taxon_ids: Sequence[int] = (),
) -> None:
    """Always supersedes every standing MANUAL claim first, then inserts
    exactly what the new state calls for -- the classifier box always
    submits its full current state rather than a diff, and this function is
    the safe way to apply that (matching services/ingest.py's
    _apply_identifications' own key-based supersede-then-insert shape).

    `verdict=None` means "clear to no manual opinion" -- a real, explicit
    input, not an implicit consequence of SPECIES with an empty taxon_ids
    (that combination raises instead, so a client bug sending an empty list
    by accident fails loudly rather than silently clearing a classification).
    """
    if verdict == Verdict.SPECIES and not taxon_ids:
        msg = "verdict=SPECIES requires at least one taxon_id"
        raise ValueError(msg)
    if verdict != Verdict.SPECIES and taxon_ids:
        msg = "taxon_ids is only meaningful for verdict=SPECIES"
        raise ValueError(msg)

    now = datetime.now(UTC)
    existing = [
        i
        for i in recording.identifications
        if i.source == IdSource.MANUAL and i.superseded_at is None
    ]
    for ident in existing:
        ident.superseded_at = now

    if verdict in (Verdict.NO_ID, Verdict.NOISE):
        recording.identifications.append(
            Identification(source=IdSource.MANUAL, verdict=verdict, first_seen_at=now),
        )
    elif verdict == Verdict.SPECIES:
        for taxon_id in taxon_ids:
            recording.identifications.append(
                Identification(
                    source=IdSource.MANUAL,
                    verdict=Verdict.SPECIES,
                    taxon_id=taxon_id,
                    first_seen_at=now,
                ),
            )
    # verdict is None: "clear" -- existing MANUAL rows already superseded
    # above, nothing new inserted.
    session.commit()
