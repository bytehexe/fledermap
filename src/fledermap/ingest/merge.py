"""Combine filename, GUANO, and wamd into one RecordingMetadata.

Timestamp precedence is deliberately configurable and both candidates are
retained (spec D17). The only available evidence is synthetic and disagrees
with itself by twelve hours, so the default is provisional, not a finding.
"""

from __future__ import annotations

from datetime import UTC, datetime, tzinfo
from typing import TypeVar

from fledermap.domain.codes import IdSource, Verdict
from fledermap.domain.metadata import ParsedIdentification, RecordingMetadata
from fledermap.ingest.filename import FilenameParse
from fledermap.ingest.guano_read import GuanoMetadata
from fledermap.ingest.wamd import WamdMetadata

TIMESTAMP_SOURCE_FILENAME = "filename"
TIMESTAMP_SOURCE_METADATA = "metadata"


class NoTimestampError(Exception):
    """Neither the filename nor the embedded metadata yields a timestamp."""


_T = TypeVar("_T")


def _first(*values: _T | None) -> _T | None:
    """First non-None value, preserving its type so no `type: ignore` is needed."""
    return next((v for v in values if v is not None), None)


def _disagreement_seconds(
    filename_at: datetime | None,
    metadata_at: datetime | None,
) -> float | None:
    """Compare the two candidate timestamps, tolerating one being naive."""
    if filename_at is None or metadata_at is None:
        return None
    a, b = filename_at, metadata_at
    if a.tzinfo is None and b.tzinfo is not None:
        a = a.replace(tzinfo=b.tzinfo)
    elif b.tzinfo is None and a.tzinfo is not None:
        b = b.replace(tzinfo=a.tzinfo)
    return abs((a - b).total_seconds())


def _identifications(
    guano: GuanoMetadata | None,
    wamd: WamdMetadata | None,
    filename: FilenameParse | None,
) -> tuple[ParsedIdentification, ...]:
    out: list[ParsedIdentification] = []

    if filename is not None:
        out.append(
            ParsedIdentification(
                source=IdSource.EMT_FILENAME,
                source_version=None,
                verdict=filename.verdict,
                raw_label=filename.code,
            ),
        )

    for meta, source, version in (
        (guano, IdSource.EMT_GUANO, getattr(guano, "app_version", None)),
        (wamd, IdSource.EMT_WAMD, getattr(wamd, "app_version", None)),
    ):
        if meta is None:
            continue
        if meta.auto_id:
            out.append(
                ParsedIdentification(
                    source=source,
                    source_version=version,
                    verdict=Verdict.SPECIES,
                    raw_label=meta.auto_id,
                ),
            )
        if meta.manual_id:
            out.append(
                ParsedIdentification(
                    source=IdSource.MANUAL,
                    source_version=None,
                    verdict=Verdict.SPECIES,
                    raw_label=meta.manual_id,
                ),
            )

    return tuple(out)


def merge_metadata(
    *,
    guano: GuanoMetadata | None,
    wamd: WamdMetadata | None,
    filename: FilenameParse | None,
    timestamp_source: str = TIMESTAMP_SOURCE_FILENAME,
    default_timezone: tzinfo = UTC,
) -> RecordingMetadata:
    """Merge every available source. Raises NoTimestampError if none yields a time.

    `default_timezone` is used only when the chosen `recorded_at` is naive AND
    the other candidate is also naive (or absent) — i.e. when NOTHING among the
    sources carries an offset. In that case there is no evidence at all for
    what the offset should be, so `default_timezone` is a fabrication, not a
    derived value. Whenever the other candidate DOES carry an offset, that
    offset is borrowed instead, matching how `_disagreement_seconds` already
    treats a naive/aware pair — so the two computations agree on what the
    naive reading means.
    """
    filename_at = filename.timestamp if filename else None
    metadata_at = _first(
        getattr(guano, "timestamp", None),
        getattr(wamd, "timestamp", None),
    )

    preferred, fallback = (
        (filename_at, metadata_at)
        if timestamp_source == TIMESTAMP_SOURCE_FILENAME
        else (metadata_at, filename_at)
    )
    recorded_at = preferred if preferred is not None else fallback
    if recorded_at is None:
        msg = "no timestamp available from filename or embedded metadata"
        raise NoTimestampError(msg)

    if recorded_at.tzinfo is None:
        # The filename encodes a wall-clock reading with no offset. Borrow the one
        # the other source asserts rather than inventing UTC — it is the only
        # evidence present, and `_disagreement_seconds` already normalises this way.
        # Assuming UTC here would make the two computations contradict each other.
        borrowed = fallback.tzinfo if fallback is not None else None
        recorded_at = recorded_at.replace(tzinfo=borrowed or default_timezone)

    return RecordingMetadata(
        recorded_at=recorded_at,
        filename_at=filename_at,
        metadata_at=metadata_at,
        timestamp_disagreement_s=_disagreement_seconds(filename_at, metadata_at),
        latitude=_first(
            getattr(guano, "latitude", None),
            getattr(wamd, "latitude", None),
        ),
        longitude=_first(
            getattr(guano, "longitude", None),
            getattr(wamd, "longitude", None),
        ),
        elevation_m=_first(
            getattr(guano, "elevation_m", None),
            getattr(wamd, "elevation_m", None),
        ),
        loc_accuracy_m=getattr(guano, "loc_accuracy_m", None),
        samplerate_hz=getattr(guano, "samplerate_hz", None),
        te_factor=getattr(guano, "te_factor", None),
        make=getattr(guano, "make", None),
        model=_first(
            getattr(guano, "model", None),
            getattr(wamd, "model", None),
        ),
        serial=getattr(guano, "serial", None),
        device=getattr(wamd, "device", None),
        note=getattr(guano, "note", None),
        guano_raw=dict(getattr(guano, "raw", {}) or {}),
        identifications=_identifications(guano, wamd, filename),
    )
