"""One shared algorithm for keeping a recording's claim set from one source in
sync with what that source currently asserts -- used by both
services/ingest.py's _apply_identifications (automatic/EMT sources) and
services/manual_classification.py's set_manual_classification (IdSource.MANUAL).

No row is ever soft-deleted. A claim identity within one (recording, source)
is its taxon_id (None meaning the NO_ID/NOISE sentinel claim); replace_claims
deletes a live row whose taxon_id the caller no longer wants, updates a live
row's other fields in place when its taxon_id is still wanted but something
about it changed, and inserts a brand new row for a taxon_id with no existing
live claim. See docs/superpowers/specs/2026-09-06-fledermap-drop-
identification-supersede-design.md, Design section 2, for why identity is
taxon_id rather than the old (source, source_version, raw_label, taxon_id)
tuple."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import IdSource, Verdict
from fledermap.store.models import Identification, Recording


@dataclass(frozen=True)
class ClaimInput:
    """What one desired live claim looks like, before it's compared against
    what's actually stored."""

    taxon_id: int | None
    verdict: Verdict
    raw_label: str | None = None
    source_version: str | None = None


@dataclass(frozen=True)
class ReplaceResult:
    added: int
    updated: int
    removed: int


def replace_claims(
    session: OrmSession,
    recording: Recording,
    source: IdSource,
    desired: Sequence[ClaimInput],
    now: datetime,
) -> ReplaceResult:
    """Bring `recording`'s live claims from `source` in line with `desired`."""
    existing_by_taxon = {
        i.taxon_id: i for i in recording.identifications if i.source == source
    }
    desired_by_taxon = {c.taxon_id: c for c in desired}

    added = updated = removed = 0

    for taxon_id, ident in list(existing_by_taxon.items()):
        if taxon_id not in desired_by_taxon:
            recording.identifications.remove(ident)  # cascade="all, delete-orphan"
            removed += 1

    for taxon_id, claim in desired_by_taxon.items():
        existing_ident = existing_by_taxon.get(taxon_id)
        if existing_ident is None:
            recording.identifications.append(
                Identification(
                    source=source,
                    verdict=claim.verdict,
                    taxon_id=claim.taxon_id,
                    raw_label=claim.raw_label,
                    source_version=claim.source_version,
                    first_seen_at=now,
                ),
            )
            added += 1
        else:
            if (
                existing_ident.verdict != claim.verdict
                or existing_ident.raw_label != claim.raw_label
                or existing_ident.source_version != claim.source_version
            ):
                existing_ident.verdict = claim.verdict
                existing_ident.raw_label = claim.raw_label
                existing_ident.source_version = claim.source_version
                updated += 1

    return ReplaceResult(added=added, updated=updated, removed=removed)
