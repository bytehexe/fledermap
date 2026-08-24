"""Resolve scanned files against the database. See spec section 6.

Idempotent by construction: identity is `audio_hash`, so re-running ingest over
an unchanged archive produces no writes. See the task-11 report for how each
field is guarded against a spurious write (the geography column in particular
does not get this for free from SQLAlchemy the way a JSONB column does).
"""

from __future__ import annotations

import math
import struct
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import assert_never

from geoalchemy2.elements import WKBElement, WKTElement
from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import IdSource
from fledermap.domain.metadata import (
    ParsedIdentification,
    RecordingMetadata,
    ScannedFile,
)
from fledermap.store.models import Identification, Recording
from fledermap.store.seed import resolve_code

# Sources whose claims are re-derived from the scanned file on every scan, so a
# claim that stops appearing can safely be treated as withdrawn (superseded).
# Deliberately EXCLUDES `MANUAL`: a manual identification entered later through
# fledermap itself (not embedded in the file) would never appear in `parsed`,
# and including it here would silently supersede it on the next re-scan of the
# same file. See task-11 report. INCLUDES `EMT_MANUAL` (task-11 fix round 1,
# priority 4): the on-device manual correction IS re-derived from the file on
# every scan, so when the operator changes it on the EMT the stale claim must
# be superseded — unlike `MANUAL`, which is never re-derived.
_EMT_SOURCES = frozenset(
    {IdSource.EMT_GUANO, IdSource.EMT_WAMD, IdSource.EMT_FILENAME, IdSource.EMT_MANUAL},
)

# Sources that use the Wildlife Acoustics code vocabulary for taxon resolution.
# Manual IDs entered on the EMT itself use the same codes as its auto-ID
# (task-11 amendments, defect 5) — a different concern from `_EMT_SOURCES`
# above, which is about supersession, not vocabulary.
_EMT_VOCABULARY_SOURCES = _EMT_SOURCES | {IdSource.MANUAL}

# Fields `RecordingMetadata` and `Recording` share by name. Comparing (and only
# writing) through one list means a field added to both later is covered by
# construction rather than needing two lists kept in sync (task-11 amendments,
# defect 4). `guano_raw` (needs a fresh dict) and the position (needs geometry
# decoding) are handled separately below.
_METADATA_FIELDS = (
    "recorded_at",
    "filename_at",
    "metadata_at",
    "timestamp_disagreement_s",
    "elevation_m",
    "loc_accuracy_m",
    "samplerate_hz",
    "duration_s",
    "te_factor",
    "make",
    "model",
    "serial",
    "device",
    "note",
)

_WKB_SRID_FLAG = 0x20000000


class IngestOutcome(StrEnum):
    CREATED = "created"
    UNCHANGED = "unchanged"
    UPDATED = "updated"
    MOVED = "moved"
    REPLACED = "replaced"


@dataclass
class IngestReport:
    created: int = 0
    unchanged: int = 0
    updated: int = 0
    moved: int = 0
    replaced: int = 0
    # Two copies of one recording (same audio_hash) sighted within a single
    # commit_scan call — a real archive condition (backup folder, re-filed
    # session) the operator should see, not something silently absorbed into
    # `unchanged`. Does NOT participate in `total`: a duplicate sighting isn't
    # one of the five (hash, path) outcomes above, it's a second sighting of
    # one that already got one (task-11 fix round 1, priority 3).
    duplicates: int = 0
    # Orthogonal to the five outcomes above (in particular to MOVED, which by
    # spec section 6 stays a single outcome keyed on (hash, path) and does not
    # get its own MOVED_AND_UPDATED variant): how many identification claims
    # changed, independent of whether the file also moved. Both are computed
    # inside `_apply_identifications` (task-11 fix round 1, priority 5).
    identifications_added: int = 0
    identifications_superseded: int = 0
    unmapped_labels: set[str] = field(default_factory=set)

    def record(self, outcome: IngestOutcome) -> None:
        # An explicit match, not `setattr(self, outcome.value, ...)`: the
        # setattr form only works because every enum value happens to equal a
        # field name, with nothing enforcing that stays true. `assert_never`
        # makes mypy flag it if a new `IngestOutcome` member is ever added
        # without a matching counter (task-11 amendments, judgement calls).
        match outcome:
            case IngestOutcome.CREATED:
                self.created += 1
            case IngestOutcome.UNCHANGED:
                self.unchanged += 1
            case IngestOutcome.UPDATED:
                self.updated += 1
            case IngestOutcome.MOVED:
                self.moved += 1
            case IngestOutcome.REPLACED:
                self.replaced += 1
            case _:
                assert_never(outcome)

    @property
    def total(self) -> int:
        # Derived from `IngestOutcome` itself, not re-listed as a fourth edit
        # site alongside the enum, `record`'s match statement, and (soon) a
        # test — adding a member here is covered automatically as long as its
        # `.value` names a field on this dataclass, same as `record` already
        # requires via `assert_never` (task-11 fix round 1, priority 6).
        return sum(getattr(self, outcome.value) for outcome in IngestOutcome)


def _relative(path: Path, archive_root: Path) -> str:
    """Path relative to `archive_root`, for storage.

    A path outside `archive_root` means `scan()` and `commit_scan()` were
    called with mismatched roots — a configuration or wiring bug, not a
    situation ingest should paper over. Falling back to an absolute path would
    silently violate the stored-paths-are-relative invariant the rest of the
    system relies on, so this raises instead (task-11 amendments, judgement
    calls).
    """
    try:
        return str(path.relative_to(archive_root))
    except ValueError as exc:
        msg = f"{path} is not under archive_root {archive_root}"
        raise ValueError(msg) from exc


def _code_source(source: IdSource) -> str:
    """All Echo Meter Touch sources, including its manual corrections, share
    the Wildlife Acoustics vocabulary."""
    return "emt" if source in _EMT_VOCABULARY_SOURCES else source


def _apply_identifications(
    session: OrmSession,
    recording: Recording,
    parsed: tuple[ParsedIdentification, ...],
    report: IngestReport,
    now: datetime,
) -> bool:
    """Add new claims and supersede ones this source no longer makes.

    `incoming` is keyed by the same `(source, source_version, raw_label)` tuple
    the database's unique constraint uses, and built as a dict (not the
    original set-plus-list combination) so that two identical claims in
    `parsed` — GUANO and wamd both reporting the same `manual_id`, the normal
    case since the EMT writes both — collapse into one candidate instead of
    both being inserted and hitting `uq_identification_source_claim` (task-11
    amendments, defect 2).
    """
    changed = False
    incoming = {(p.source, p.source_version, p.raw_label): p for p in parsed}
    existing = {
        (i.source, i.source_version, i.raw_label): i
        for i in recording.identifications
        if i.superseded_at is None
    }

    for key, ident in existing.items():
        if key not in incoming and ident.source in _EMT_SOURCES:
            ident.superseded_at = now
            report.identifications_superseded += 1
            changed = True

    for key, p in incoming.items():
        if key in existing:
            continue
        taxon = None
        if p.raw_label:
            taxon = resolve_code(session, _code_source(p.source), p.raw_label)
            if taxon is None:
                report.unmapped_labels.add(p.raw_label)
        recording.identifications.append(
            Identification(
                source=p.source,
                source_version=p.source_version,
                verdict=p.verdict,
                taxon_id=taxon.id if taxon else None,
                raw_label=p.raw_label,
                first_seen_at=now,
            ),
        )
        report.identifications_added += 1
        changed = True

    return changed


def _decode_point(elem: object | None) -> tuple[float, float] | None:
    """Decode a stored geography Point's raw WKB payload into (lon, lat).

    `geoalchemy2`'s own `to_shape` needs shapely, which nothing else in this
    project requires — see task-11 report for that trade-off. A Point's WKB
    layout (order byte, type word, an optional SRID word, then two doubles in
    the record's own endianness) is a stable OGC-specified format, not an
    implementation detail of this driver, so decoding it directly is safe.
    Skipping the geometry-type check on the type word is safe specifically
    BECAUSE `Recording.geom` (models.py) is declared `geometry_type="POINT"`:
    the column's typmod constrains every value that can ever reach this
    decoder to a Point, so there is no other WKB shape this could be handed
    (task-11 fix round 1, priority 6).
    """
    if not isinstance(elem, WKBElement):
        return None
    data = elem.data
    raw = bytes.fromhex(data) if isinstance(data, str) else bytes(data)
    endian = "<" if raw[0] == 1 else ">"
    (geom_type,) = struct.unpack_from(endian + "I", raw, 1)
    offset = 9 if geom_type & _WKB_SRID_FLAG else 5
    lon, lat = struct.unpack_from(endian + "dd", raw, offset)
    return (lon, lat)


def _position_changed(recording: Recording, m: RecordingMetadata) -> bool:
    # Keep-last-known-position rule: an incoming `None` position (metadata that
    # failed to parse, or a source that never carries one) is reported as
    # unchanged rather than clearing a previously recorded position. This is
    # the one field in `_apply_metadata` that does NOT let `None` overwrite a
    # value — deliberately: this is a location journal, and a metadata hiccup
    # must not erase a real recorded position. Every other field writes
    # `None` over a prior value because for those fields "disappeared from
    # this scan" and "genuinely absent" are the same fact; for position they
    # are not (task-11 fix round 1, priority 6).
    if m.latitude is None or m.longitude is None:
        return False
    return _decode_point(recording.geom) != (m.longitude, m.latitude)


def _apply_metadata(recording: Recording, scanned: ScannedFile) -> bool:
    """Write only the fields that actually changed; return whether anything did.

    Every field is compared before assignment rather than blind-assigned, so
    that a clean second scan touches no attribute at all and SQLAlchemy has
    nothing to flush. This can't be left to SQLAlchemy's own equal-value
    suppression: that suppression exists and does cover `guano_raw` (a fresh
    but equal dict reassigned to a JSONB column emits no UPDATE, confirmed
    empirically for the task-11 report), but NOT `geom` — a freshly built
    `WKTElement` never compares equal to the `WKBElement` already loaded from
    the database, even encoding the identical point, so reassigning it
    unconditionally emits a real UPDATE on every single scan (also confirmed
    empirically). Hence `_position_changed` decodes the stored point instead of
    trusting `!=` on the two element types directly.
    """
    m = scanned.metadata
    changed = False
    for attr in _METADATA_FIELDS:
        new = getattr(m, attr)
        if getattr(recording, attr) != new:
            setattr(recording, attr, new)
            changed = True

    new_guano = dict(m.guano_raw)
    if recording.guano_raw != new_guano:
        recording.guano_raw = new_guano
        changed = True

    if _position_changed(recording, m):
        recording.geom = WKTElement(f"POINT({m.longitude} {m.latitude})", srid=4326)
        changed = True

    return changed


def commit_scan(
    session: OrmSession,
    scanned: Iterable[ScannedFile],
    *,
    archive_root: Path,
) -> IngestReport:
    """Write scanned files to the database, resolving each by `audio_hash`.

    Implements the four-row resolution table in spec section 6:
    unknown hash -> CREATED; known hash + same path -> UNCHANGED/UPDATED;
    known hash + new path -> MOVED; same path + new hash -> REPLACED (the old
    row is never deleted — spec is explicit that deleting it would destroy
    manually entered identifications).
    """
    report = IngestReport()
    now = datetime.now(tz=UTC)
    # Hashes already handled earlier in THIS call. Two `ScannedFile`s sharing
    # one audio_hash are duplicate copies of one recording on disk (a backup
    # folder, a re-filed session) — same audio, different paths. Without this,
    # the second copy is resolved against the row the first copy just created,
    # sees a different path, and gets reported (and written) as MOVED — which
    # then flips back on every subsequent scan as the two paths alternate
    # being "current". First path sighted wins; every later sighting in this
    # call is a duplicate, not a move (task-11 fix round 1, priority 3).
    seen_hashes: set[str] = set()

    for item in scanned:
        rel = _relative(item.path, archive_root)

        if item.audio_hash in seen_hashes:
            report.duplicates += 1
            continue
        seen_hashes.add(item.audio_hash)

        existing = session.scalars(
            select(Recording).where(Recording.audio_hash == item.audio_hash),
        ).one_or_none()

        if existing is None:
            # `Recording.path` is indexed but NOT unique, and a REPLACED row's
            # `path` is left intact (see below) — so after one replacement two
            # rows can share a path, and plain `.one_or_none()` here raises
            # `MultipleResultsFound` on the next one. The invariant that
            # actually holds is narrower: a LIVE row's path is where its file
            # currently is, so at most one non-missing row can occupy a path.
            # Filtering on `missing_since.is_(None)` restores that invariant;
            # `order_by`+`limit(1)` is only there so a corrupted state (which
            # should be unreachable given the filter) degrades to one
            # deterministic row instead of aborting the whole ingest
            # (task-11 fix round 1, priority 2).
            #
            # Accepted consequence: a file already swept to `missing_since`
            # and later succeeded by a *different* file at the same path now
            # counts CREATED, not REPLACED. That is more accurate — nothing
            # was replaced, the old file was already gone.
            replaced = session.scalars(
                select(Recording)
                .where(Recording.path == rel, Recording.missing_since.is_(None))
                .order_by(Recording.id.desc())
                .limit(1),
            ).first()
            if replaced is not None:
                replaced.missing_since = now

            recording = Recording(audio_hash=item.audio_hash, path=rel, ingested_at=now)
            _apply_metadata(recording, item)
            session.add(recording)
            session.flush()
            _apply_identifications(
                session,
                recording,
                item.metadata.identifications,
                report,
                now,
            )
            report.record(
                IngestOutcome.REPLACED
                if replaced is not None
                else IngestOutcome.CREATED,
            )
            continue

        moved = existing.path != rel
        existing.path = rel
        # `sweep_missing` (below) clears `missing_since` the same way when a
        # known hash reappears in a scan. Two functions now share this
        # responsibility — keep them in sync if the reappearance rule changes.
        existing.missing_since = None
        metadata_changed = _apply_metadata(existing, item)
        ids_changed = _apply_identifications(
            session,
            existing,
            item.metadata.identifications,
            report,
            now,
        )

        if moved:
            report.record(IngestOutcome.MOVED)
        elif metadata_changed or ids_changed:
            report.record(IngestOutcome.UPDATED)
        else:
            report.record(IngestOutcome.UNCHANGED)

    return report


DEFAULT_MISSING_THRESHOLD = 0.10


class MassDisappearanceError(Exception):
    """Too many recordings vanished at once to be believable.

    An unmounted archive or a mid-sync Syncthing makes every file look deleted.
    Flagging them all would be silent, wide damage, so the sweep refuses.
    """

    def __init__(self, missing: int, known: int) -> None:
        self.missing = missing
        self.known = known
        super().__init__(
            f"{missing} of {known} recordings absent — refusing to flag. "
            "Is the archive mounted and finished syncing?",
        )


class IncompleteScanError(Exception):
    """The scan skipped files, so `seen_hashes` cannot be trusted as complete.

    Sweeping on incomplete information is exactly what the mass-disappearance
    guard exists to prevent — it would just be arriving through a different
    route (a settling file, a transient I/O error) instead of an unmounted
    drive. Refuse rather than risk flagging a file that is simply still being
    written.
    """

    def __init__(self, skipped: int) -> None:
        self.skipped = skipped
        super().__init__(
            f"{skipped} file(s) were skipped during scan — refusing to sweep "
            "on an incomplete picture of what's present.",
        )


def sweep_missing(
    session: OrmSession,
    seen_hashes: set[str],
    *,
    threshold: float = DEFAULT_MISSING_THRESHOLD,
    skipped: int = 0,
) -> int:
    """Flag recordings whose source file was not seen. Never deletes rows.

    Two guards refuse the whole sweep, before any row is touched, rather than
    risk a false mass-flagging:

    - `IncompleteScanError` if the caller's scan skipped any files. A skipped
      file (settling, or a transient I/O error — see `scan_with_skips`) never
      makes it into `seen_hashes`, so it would otherwise look identical to a
      genuine deletion. This is the same failure the ratio guard below exists
      to catch, just arriving through a route that guard can't see, so it's
      caught here explicitly instead.
    - `MassDisappearanceError` if too large a fraction of recordings newly
      went missing THIS sweep. The ratio is computed over *newly* absent rows
      (`missing_since is None`) only — rows an earlier sweep already flagged
      don't count again, otherwise a real, permanently-deleted file would
      eventually push the cumulative absent count over threshold and disable
      the guard (and thus this function) forever. Below `min_known_for_guard`
      recordings, the ratio can't distinguish "one file" from "mass
      disappearance" (one file already meets the threshold fraction), so the
      guard is skipped entirely and the sweep proceeds normally.

    `missing_since` is also cleared here when a known hash reappears — the
    same responsibility `commit_scan` (above) takes on a freshly-(re)matched
    row. Keep the two in sync if the reappearance rule ever changes.

    Loads every `Recording` row into memory; fine at journal scale (tens to
    low thousands), not optimized further.
    """
    if skipped > 0:
        raise IncompleteScanError(skipped)

    known = session.scalars(select(Recording)).all()
    if not known:
        return 0

    absent = [r for r in known if r.audio_hash not in seen_hashes]
    newly_absent = [r for r in absent if r.missing_since is None]

    # ceil, not round: the floor must be the SMALLEST n with n * threshold >= 1,
    # so that a single loss at exactly the floor never itself exceeds the ratio
    # (1/n <= threshold). round() can undershoot that n whenever 1/threshold's
    # fractional part is < 0.5 — e.g. threshold=0.19 gives round(5.26)=5, and a
    # single loss out of 5 known (1 > 5*0.19=0.95) still trips the guard right
    # at the floor, reproducing the exact "one file in a small archive" defect
    # this two-stage guard exists to prevent. ceil(5.26)=6 avoids it: 1 >
    # 6*0.19=1.14 is False. Verified for the shipped default (threshold=0.10)
    # this is unchanged: ceil(10.0) == round(10.0) == 10.
    min_known_for_guard = max(1, math.ceil(1 / threshold))
    if len(known) >= min_known_for_guard and len(newly_absent) > len(known) * threshold:
        raise MassDisappearanceError(missing=len(newly_absent), known=len(known))

    now = datetime.now(tz=UTC)
    flagged = 0
    for recording in known:
        if recording.audio_hash in seen_hashes:
            recording.missing_since = None
        elif recording.missing_since is None:
            recording.missing_since = now
            flagged += 1

    return flagged
