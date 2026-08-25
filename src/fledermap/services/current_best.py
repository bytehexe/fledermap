"""'Current best' identification -- design spec P4-2, resolving the parent
spec's (section 5) explicit but never-implemented rule: "manual wins, else
highest-priority non-superseded source by configured order." Not a stored
column -- recomputed on every call, so the order below can change without a
migration."""

from __future__ import annotations

from datetime import UTC, datetime

from fledermap.domain.codes import IdSource
from fledermap.store.models import Identification, Recording

# The configured order (design spec P4-2). BATDETECT2/BATTYBIRDNET/KALEIDOSCOPE
# are deliberately absent: no source in this codebase produces them yet (v2),
# so their eventual position is unobserved and revisable without migration --
# they simply never match any candidate today.
_PRECEDENCE: tuple[IdSource, ...] = (
    IdSource.MANUAL,
    IdSource.EMT_MANUAL,
    IdSource.EMT_GUANO,
    IdSource.EMT_WAMD,
    IdSource.EMT_FILENAME,
)

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def current_best_identification(recording: Recording) -> Identification | None:
    """Manual wins, else the highest-priority non-superseded source. Ties
    within one source (two non-superseded claims differing only in
    `source_version`/`raw_label` -- possible but rare, since a rescan normally
    supersedes a source's prior claim before adding a new one) break on the
    most recently first-seen claim, not on dict/list iteration order."""
    candidates = [i for i in recording.identifications if i.superseded_at is None]
    for source in _PRECEDENCE:
        matches = [i for i in candidates if i.source == source]
        if matches:
            return max(matches, key=lambda i: i.first_seen_at or _EPOCH)
    return None
