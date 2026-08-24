"""Resolve scanned files against the database. See spec section 6.

Idempotent by construction: identity is `audio_hash`, so re-running ingest over
an unchanged archive produces no writes. See the task-11 report for how each
field is guarded against a spurious write (the geography column in particular
does not get this for free from SQLAlchemy the way a JSONB column does).
"""

from __future__ import annotations

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
# same file. See task-11 report.
_EMT_SOURCES = frozenset({IdSource.EMT_GUANO, IdSource.EMT_WAMD, IdSource.EMT_FILENAME})

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
        return self.created + self.unchanged + self.updated + self.moved + self.replaced


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
        changed = True

    return changed


def _decode_point(elem: object | None) -> tuple[float, float] | None:
    """Decode a stored geography Point's raw WKB payload into (lon, lat).

    `geoalchemy2`'s own `to_shape` needs shapely, which nothing else in this
    project requires — see task-11 report for that trade-off. A Point's WKB
    layout (order byte, type word, an optional SRID word, then two doubles in
    the record's own endianness) is a stable OGC-specified format, not an
    implementation detail of this driver, so decoding it directly is safe.
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

    for item in scanned:
        rel = _relative(item.path, archive_root)
        existing = session.scalars(
            select(Recording).where(Recording.audio_hash == item.audio_hash),
        ).one_or_none()

        if existing is None:
            replaced = session.scalars(
                select(Recording).where(Recording.path == rel),
            ).one_or_none()
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
