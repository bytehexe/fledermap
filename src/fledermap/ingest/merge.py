"""Combine filename, GUANO, and wamd into one RecordingMetadata.

Timestamp precedence is deliberately configurable and both candidates are
retained (spec D17). The only available evidence is synthetic and disagrees
with itself by twelve hours, so the default is provisional, not a finding.
"""

from __future__ import annotations

from datetime import UTC, datetime, tzinfo
from typing import TypeVar

from fledermap.domain.codes import IdSource, TimestampSource, Verdict
from fledermap.domain.metadata import ParsedIdentification, RecordingMetadata
from fledermap.ingest.filename import FilenameParse
from fledermap.ingest.guano_read import GuanoMetadata
from fledermap.ingest.wamd import WamdMetadata


class NoTimestampError(Exception):
    """Neither the filename nor the embedded metadata yields a timestamp."""


class InvalidTimestampSourceError(ValueError):
    """`timestamp_source` isn't a recognised `TimestampSource` value.

    `merge_metadata` is a library function (spec D3): a future caller (a
    watcher, a web upload) may call it directly, without going through
    `Config.from_env`'s own validation — so this validates explicitly rather
    than silently treating anything that isn't `TimestampSource.FILENAME` as
    `TimestampSource.METADATA`.
    """

    def __init__(self, value: object) -> None:
        self.value = value
        valid = ", ".join(s.value for s in TimestampSource)
        super().__init__(
            f"{value!r} is not a valid timestamp source. Valid options: {valid}.",
        )


_T = TypeVar("_T")


def _first(*values: _T | None) -> _T | None:
    """First non-None value, preserving its type so no `type: ignore` is needed."""
    return next((v for v in values if v is not None), None)


def _disagreement_seconds(
    filename_at: datetime | None,
    metadata_at: datetime | None,
) -> float | None:
    """Compare the two candidate timestamps.

    Tolerates a naive input for robustness, but in practice `merge_metadata`
    now always passes already-aware values (via `_borrow_offset`), so the
    naive branches below are not exercised from that call site.
    """
    if filename_at is None or metadata_at is None:
        return None
    a, b = filename_at, metadata_at
    if a.tzinfo is None and b.tzinfo is not None:
        a = a.replace(tzinfo=b.tzinfo)
    elif b.tzinfo is None and a.tzinfo is not None:
        b = b.replace(tzinfo=a.tzinfo)
    return abs((a - b).total_seconds())


def _borrow_offset(
    value: datetime | None,
    other: datetime | None,
    default_timezone: tzinfo,
) -> datetime | None:
    """Make `value` timezone-aware, borrowing `other`'s offset when it has one.

    `filename_at` and `metadata_at` are stored in `DateTime(timezone=True)`
    columns (`Recording.filename_at`, `Recording.metadata_at`), so the value
    read back from the database is always aware — and Python's naive-vs-aware
    comparison is unconditionally unequal (`!=` is always `True`, never
    raises). Leaving either field naive here would therefore make every
    change-detection comparison in `_apply_metadata` report a difference on
    every scan, forever, which is exactly the idempotency defect this fixes
    (task-11 fix round 1, priority 1). So the ambiguity is resolved here,
    deliberately, the same way `recorded_at` resolves it below: borrow the
    other source's offset when it has one, and fall back to
    `default_timezone` only when NEITHER source carries any offset evidence
    at all — a documented fabrication, not a derived value.
    """
    if value is None or value.tzinfo is not None:
        return value
    borrowed = other.tzinfo if other is not None else None
    return value.replace(tzinfo=borrowed or default_timezone)


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
                    # Re-derived from the file on every scan, so it must be
                    # superseded like the EMT's other claims when the operator
                    # changes it on the device — `IdSource.MANUAL` is reserved
                    # for a future UI entry that is never re-derived (task-11
                    # fix round 1, priority 4).
                    source=IdSource.EMT_MANUAL,
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
    timestamp_source: TimestampSource = TimestampSource.FILENAME,
    default_timezone: tzinfo = UTC,
) -> RecordingMetadata:
    """Merge every available source. Raises NoTimestampError if none yields a time.

    Raises InvalidTimestampSourceError if `timestamp_source` isn't a
    recognised `TimestampSource` value.

    `filename_at`, `metadata_at`, and `recorded_at` are all made aware by
    `_borrow_offset`: `default_timezone` is used only when NEITHER candidate
    carries an offset — i.e. there is no evidence at all for what the offset
    should be, so it is a documented fabrication, not a derived value.
    Whenever the other candidate DOES carry an offset, that offset is
    borrowed instead. See `_borrow_offset` for the full reasoning (task-11 fix
    round 1, priority 1).
    """
    try:
        source = TimestampSource(timestamp_source)
    except ValueError as exc:
        raise InvalidTimestampSourceError(timestamp_source) from exc

    raw_filename_at = filename.timestamp if filename else None
    raw_metadata_at = _first(
        getattr(guano, "timestamp", None),
        getattr(wamd, "timestamp", None),
    )

    # Both stored columns are timezone-aware (task-11 fix round 1, priority 1):
    # make each candidate aware here, borrowing from the OTHER RAW candidate
    # (before either has been touched) so neither borrow can pick up an offset
    # the other only has because it was itself just fabricated.
    filename_at = _borrow_offset(raw_filename_at, raw_metadata_at, default_timezone)
    metadata_at = _borrow_offset(raw_metadata_at, raw_filename_at, default_timezone)

    preferred, fallback = (
        (filename_at, metadata_at)
        if source is TimestampSource.FILENAME
        else (metadata_at, filename_at)
    )
    recorded_at = preferred if preferred is not None else fallback
    if recorded_at is None:
        msg = "no timestamp available from filename or embedded metadata"
        raise NoTimestampError(msg)
    # No naive-handling needed here: `filename_at` and `metadata_at` are both
    # already aware (or None) by construction above, so whichever of them was
    # chosen as `recorded_at` is already aware too.

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
