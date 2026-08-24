"""Walk an archive directory and emit one ScannedFile per readable recording.

Pure with respect to the archive: opens files read-only and never writes,
moves, renames, or deletes. See spec section 6.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, tzinfo
from enum import StrEnum
from pathlib import Path

from fledermap.domain.codes import TimestampSource
from fledermap.domain.metadata import ScannedFile
from fledermap.ingest.filename import parse_emt_filename
from fledermap.ingest.guano_read import parse_guano
from fledermap.ingest.merge import NoTimestampError, merge_metadata
from fledermap.ingest.riff import (
    MissingAudioChunkError,
    NotARiffFileError,
    audio_hash,
    iter_chunks,
    read_chunk,
    read_format,
)
from fledermap.ingest.wamd import parse_wamd

logger = logging.getLogger(__name__)

DEFAULT_SETTLE_SECONDS = 30.0
_TEMP_SUFFIXES = (".tmp", ".part", ".syncthing", ".!sync")


class SkipReason(StrEnum):
    """Why a file present in the archive produced no ScannedFile.

    Two entirely different kinds of "no ScannedFile was produced":

    - Deliberate, permanent exclusion — will never resolve no matter how long
      you wait: EXCLUDED (a hidden path component or a sync tool's temp
      suffix) and NOT_A_WAV (content that plainly isn't RIFF/WAV audio). A
      live, syncing archive (spec section 6) will ALWAYS contain some of
      these — Syncthing's own `.stfolder`/`.stignore`, at minimum — so they
      must never make the mass-disappearance guard refuse.
    - Genuine unknown — the picture is incomplete and a retry might resolve
      it: UNSETTLED (too young by mtime), UNREADABLE (an OSError was hit
      anywhere while trying to read the candidate), and UNPARSEABLE (parsed
      as RIFF/WAV but no timestamp was derivable). See
      `INCOMPLETE_SCAN_REASONS` below — only these should ever refuse the
      sweep.
    """

    EXCLUDED = "excluded"
    NOT_A_WAV = "not_a_wav"
    UNSETTLED = "unsettled"
    UNREADABLE = "unreadable"
    UNPARSEABLE = "unparseable"


# The single source of truth for "does this skip reason mean the scan's
# picture of what's present is incomplete?" `cli/main.py` consults this
# rather than re-deciding the classification itself. Deliberate exclusions
# (EXCLUDED, NOT_A_WAV) are never in here: they will never resolve, so they
# carry no information the mass-disappearance guard needs to be cautious
# about.
INCOMPLETE_SCAN_REASONS = frozenset(
    {SkipReason.UNSETTLED, SkipReason.UNREADABLE, SkipReason.UNPARSEABLE},
)


def _settle_check(
    path: Path,
    root: Path,
    settle_seconds: float,
    now: float,
) -> SkipReason | None:
    """Classify a file before any read of its content is attempted.

    Returns the reason to skip it, or None if it's a candidate ready for
    `_scan_one`. Hidden components are checked RELATIVE to the archive root:
    an archive that itself lives under a dotted directory must still be
    scannable, while hidden subdirectories inside it (Syncthing's
    `.stfolder`) stay skipped.
    """
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        parts = (path.name,)
    if any(part.startswith(".") for part in parts):
        return SkipReason.EXCLUDED
    if path.name.endswith(_TEMP_SUFFIXES):
        return SkipReason.EXCLUDED
    try:
        age = now - path.stat().st_mtime
    except OSError:
        return SkipReason.UNREADABLE
    if age < settle_seconds:
        return SkipReason.UNSETTLED
    return None


def _scan_one(
    path: Path,
    timestamp_source: TimestampSource,
    default_timezone: tzinfo,
) -> ScannedFile | SkipReason:
    try:
        digest = audio_hash(path)
    except OSError:
        return SkipReason.UNREADABLE
    except (NotARiffFileError, MissingAudioChunkError):
        return SkipReason.NOT_A_WAV

    wamd = None
    try:
        chunk = next((c for c in iter_chunks(path) if c.chunk_id == "wamd"), None)
        if chunk is not None:
            wamd = parse_wamd(read_chunk(path, chunk))
    except OSError:
        return SkipReason.UNREADABLE
    except NotARiffFileError:
        wamd = None

    try:
        guano = parse_guano(path)
    except OSError:
        return SkipReason.UNREADABLE

    try:
        metadata = merge_metadata(
            guano=guano,
            wamd=wamd,
            filename=parse_emt_filename(path.name),
            timestamp_source=timestamp_source,
            default_timezone=default_timezone,
        )
    except NoTimestampError:
        logger.warning("no timestamp for %s; skipping", path)
        return SkipReason.UNPARSEABLE

    # Samplerate and duration come from the container itself, which is more
    # reliable than any metadata field and present even when metadata is not.
    try:
        fmt = read_format(path)
    except OSError:
        return SkipReason.UNREADABLE
    except (NotARiffFileError, MissingAudioChunkError):
        return SkipReason.NOT_A_WAV
    metadata = replace(
        metadata,
        samplerate_hz=metadata.samplerate_hz or fmt.samplerate_hz,
        duration_s=fmt.duration_s,
    )

    return ScannedFile(audio_hash=digest, path=path, metadata=metadata)


def scan(
    root: Path,
    *,
    timestamp_source: TimestampSource = TimestampSource.FILENAME,
    settle_seconds: float = DEFAULT_SETTLE_SECONDS,
    now: float | None = None,
    default_timezone: tzinfo = UTC,
) -> Iterator[ScannedFile]:
    """Yield a ScannedFile for every readable, settled recording under `root`."""
    for scanned in scan_with_skips(
        root,
        timestamp_source=timestamp_source,
        settle_seconds=settle_seconds,
        now=now,
        default_timezone=default_timezone,
    ):
        if isinstance(scanned, ScannedFile):
            yield scanned


def scan_with_skips(
    root: Path,
    *,
    timestamp_source: TimestampSource = TimestampSource.FILENAME,
    settle_seconds: float = DEFAULT_SETTLE_SECONDS,
    now: float | None = None,
    default_timezone: tzinfo = UTC,
) -> Iterator[ScannedFile | tuple[Path, SkipReason]]:
    """As `scan`, but also reports why files were skipped, for CLI summaries."""
    clock = time.time() if now is None else now
    for path in sorted(root.rglob("*")):
        try:
            is_file = path.is_file()
        except OSError:
            yield (path, SkipReason.UNREADABLE)
            continue
        if not is_file:
            continue
        reason = _settle_check(path, root, settle_seconds, clock)
        if reason is not None:
            yield (path, reason)
            continue
        result = _scan_one(path, timestamp_source, default_timezone)
        yield result if isinstance(result, ScannedFile) else (path, result)
