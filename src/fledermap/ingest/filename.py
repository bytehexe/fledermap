"""Parser for the Echo Meter Touch filename convention.

    ID_YYYYMMDD_HHMMSS.WAV

`ID` is a six-letter genus+species code, or the literals `NoID` and `NOISE`.
The filename is a genuinely independent source of both timestamp and
identification, which is what makes it a useful cross-check on the embedded
metadata (spec section 11).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePath

from fledermap.domain.codes import Verdict, sentinel_verdict


@dataclass(frozen=True)
class FilenameParse:
    """What the filename alone tells us. `timestamp` is naive: no offset is encoded."""

    code: str | None
    verdict: Verdict
    timestamp: datetime


def parse_emt_filename(name: str) -> FilenameParse | None:
    """Parse an EMT filename, or return None if it does not match the convention.

    Deliberately does NOT validate the file extension. Callers establish that a
    file is a recording by probing its RIFF content (see `ingest.scan`), which
    is stronger than a suffix check and does not reject a correctly-named file
    stored as `.wave` or with no extension at all. This function's contract is
    the NAME PATTERN only: identifier, date, time.
    """
    stem = PurePath(name).stem
    parts = stem.rsplit("_", 2)
    if len(parts) != 3:
        return None

    ident, date_part, time_part = parts
    try:
        timestamp = datetime.strptime(f"{date_part}{time_part}", "%Y%m%d%H%M%S")
    except ValueError:
        return None

    sentinel = sentinel_verdict(ident)
    if sentinel is not None:
        return FilenameParse(code=None, verdict=sentinel, timestamp=timestamp)
    if not ident:
        return None
    return FilenameParse(
        code=ident.upper(),
        verdict=Verdict.SPECIES,
        timestamp=timestamp,
    )
