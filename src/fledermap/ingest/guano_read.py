"""Reader for the standard GUANO metadata chunk.

GUANO is UTF-8 text in a `guan` sub-chunk: newline-separated `Key: Value`
pairs, the first being `GUANO|Version`. Parsed directly rather than through
guano-py's file API so that reading costs one chunk read rather than a second
full open, and so a malformed chunk degrades instead of raising.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from fledermap.ingest.riff import NotARiffFileError, iter_chunks, read_chunk


@dataclass(frozen=True)
class GuanoMetadata:
    """Modelled GUANO fields, plus every key verbatim in `raw`."""

    model: str | None = None
    make: str | None = None
    serial: str | None = None
    app_version: str | None = None
    timestamp: datetime | None = None
    latitude: float | None = None
    longitude: float | None = None
    elevation_m: float | None = None
    loc_accuracy_m: float | None = None
    samplerate_hz: int | None = None
    te_factor: int | None = None
    note: str | None = None
    auto_id: str | None = None
    manual_id: str | None = None
    raw: dict[str, str] = field(default_factory=dict)


def _as_float(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value.strip())
    except ValueError:
        return None


def _as_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(float(value.strip()))
    except ValueError:
        return None


def _parse_position(value: str | None) -> tuple[float | None, float | None]:
    """`Loc Position` is two whitespace-separated floats: latitude longitude."""
    if value is None:
        return None, None
    parts = value.split()
    if len(parts) < 2:
        return None, None
    return _as_float(parts[0]), _as_float(parts[1])


def _parse_timestamp(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value.strip())
    except ValueError:
        return None


def _unescape(value: str) -> str:
    """Undo GUANO's escaping: `\\n` is a newline, `\\\\` a literal backslash.

    A single pass, so a literal backslash followed by `n` survives intact
    instead of being mistaken for an escaped newline.
    """
    out: list[str] = []
    i = 0
    while i < len(value):
        if value[i] == "\\" and i + 1 < len(value):
            nxt = value[i + 1]
            if nxt == "n":
                out.append("\n")
                i += 2
                continue
            if nxt == "\\":
                out.append("\\")
                i += 2
                continue
        out.append(value[i])
        i += 1
    return "".join(out)


def parse_guano(path: Path) -> GuanoMetadata | None:
    """Return parsed GUANO metadata, or None when the file has no `guan` chunk."""
    try:
        chunk = next((c for c in iter_chunks(path) if c.chunk_id == "guan"), None)
    except NotARiffFileError:
        return None
    if chunk is None:
        return None

    text = read_chunk(path, chunk).decode("utf-8", errors="replace")
    raw: dict[str, str] = {}
    last_key: str | None = None
    for line in text.splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            last_key = key.strip()
            raw[last_key] = _unescape(value.strip())
        elif last_key is not None:
            # A physical newline inside a value. Keep it rather than drop it.
            raw[last_key] += "\n" + _unescape(line)

    lat, lon = _parse_position(raw.get("Loc Position"))
    return GuanoMetadata(
        model=raw.get("Model"),
        make=raw.get("Make"),
        serial=raw.get("Serial"),
        app_version=raw.get("Firmware Version"),
        timestamp=_parse_timestamp(raw.get("Timestamp")),
        latitude=lat,
        longitude=lon,
        elevation_m=_as_float(raw.get("Loc Elevation")),
        loc_accuracy_m=_as_float(raw.get("Loc Accuracy")),
        samplerate_hz=_as_int(raw.get("Samplerate")),
        te_factor=_as_int(raw.get("TE")),
        note=raw.get("Note"),
        auto_id=raw.get("Species Auto ID") or None,
        manual_id=raw.get("Species Manual ID") or None,
        raw=raw,
    )
