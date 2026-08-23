"""Walk an archive directory and emit one ScannedFile per readable recording.

Pure with respect to the archive: opens files read-only and never writes,
moves, renames, or deletes. See spec section 6.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from dataclasses import replace
from enum import StrEnum
from pathlib import Path

from fledermap.domain.metadata import ScannedFile
from fledermap.ingest.filename import parse_emt_filename
from fledermap.ingest.guano_read import parse_guano
from fledermap.ingest.merge import (
    TIMESTAMP_SOURCE_FILENAME,
    NoTimestampError,
    merge_metadata,
)
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
    """Why a file present in the archive produced no ScannedFile."""

    NOT_A_WAV = "not_a_wav"
    UNSETTLED = "unsettled"
    UNPARSEABLE = "unparseable"


def _is_settled(path: Path, root: Path, settle_seconds: float, now: float) -> bool:
    """A file still being written by Syncthing or rsync must not be read yet.

    Hidden components are checked RELATIVE to the archive root: an archive that
    itself lives under a dotted directory must still be scannable, while hidden
    subdirectories inside it (Syncthing's `.stfolder`) stay skipped.
    """
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        parts = (path.name,)
    if any(part.startswith(".") for part in parts):
        return False
    if path.name.endswith(_TEMP_SUFFIXES):
        return False
    return (now - path.stat().st_mtime) >= settle_seconds


def _scan_one(path: Path, timestamp_source: str) -> ScannedFile | SkipReason:
    try:
        digest = audio_hash(path)
    except (NotARiffFileError, MissingAudioChunkError, OSError):
        return SkipReason.NOT_A_WAV

    wamd = None
    try:
        chunk = next((c for c in iter_chunks(path) if c.chunk_id == "wamd"), None)
        if chunk is not None:
            wamd = parse_wamd(read_chunk(path, chunk))
    except (NotARiffFileError, OSError):
        wamd = None

    try:
        guano = parse_guano(path)
    except OSError:
        return SkipReason.NOT_A_WAV

    try:
        metadata = merge_metadata(
            guano=guano,
            wamd=wamd,
            filename=parse_emt_filename(path.name),
            timestamp_source=timestamp_source,
        )
    except NoTimestampError:
        logger.warning("no timestamp for %s; skipping", path)
        return SkipReason.UNPARSEABLE

    # Samplerate and duration come from the container itself, which is more
    # reliable than any metadata field and present even when metadata is not.
    try:
        fmt = read_format(path)
    except (NotARiffFileError, MissingAudioChunkError, OSError):
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
    timestamp_source: str = TIMESTAMP_SOURCE_FILENAME,
    settle_seconds: float = DEFAULT_SETTLE_SECONDS,
    now: float | None = None,
) -> Iterator[ScannedFile]:
    """Yield a ScannedFile for every readable, settled recording under `root`."""
    for scanned in scan_with_skips(
        root,
        timestamp_source=timestamp_source,
        settle_seconds=settle_seconds,
        now=now,
    ):
        if isinstance(scanned, ScannedFile):
            yield scanned


def scan_with_skips(
    root: Path,
    *,
    timestamp_source: str = TIMESTAMP_SOURCE_FILENAME,
    settle_seconds: float = DEFAULT_SETTLE_SECONDS,
    now: float | None = None,
) -> Iterator[ScannedFile | tuple[Path, SkipReason]]:
    """As `scan`, but also reports why files were skipped, for CLI summaries."""
    clock = time.time() if now is None else now
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if not _is_settled(path, root, settle_seconds, clock):
            yield (path, SkipReason.UNSETTLED)
            continue
        result = _scan_one(path, timestamp_source)
        yield result if isinstance(result, ScannedFile) else (path, result)
