"""Reader for Wildlife Acoustics' proprietary `wamd` metadata chunk.

Structure decoded from real Echo Meter Touch sample files during the phase-0
spike (spec section 11, R1): repeated entries of

    uint16 type_id · uint32 size · payload[size]

Type IDs are those observed. Unknown types are skipped rather than treated as
errors, so a firmware update that adds a field does not break ingest.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from datetime import datetime

_TYPE_MODEL = 0x01
_TYPE_APP_VERSION = 0x03
_TYPE_DEVICE = 0x04
_TYPE_TIMESTAMP = 0x05
_TYPE_POSITION = 0x06
_TYPE_AUTO_ID = 0x0B
_TYPE_MANUAL_ID = 0x0C

_ENTRY_HEADER = 6
_NULL = "(null)"


@dataclass(frozen=True)
class WamdMetadata:
    """Everything Fledermap uses from a `wamd` chunk. Absent fields are None."""

    model: str | None = None
    app_version: str | None = None
    device: str | None = None
    timestamp: datetime | None = None
    latitude: float | None = None
    longitude: float | None = None
    elevation_m: float | None = None
    auto_id: str | None = None
    manual_id: str | None = None


def _parse_timestamp(value: str) -> datetime | None:
    for fmt in ("%Y-%m-%d %H:%M:%S%z", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value.strip(), fmt)
        except ValueError:
            continue
    return None


def _parse_position(value: str) -> tuple[float | None, float | None, float | None]:
    """Parse `WGS84,<lat>,<lon>,<elevation>`; elevation may be the string `(null)`."""
    parts = [p.strip() for p in value.split(",")]
    if len(parts) < 3:
        return None, None, None

    def _num(raw: str) -> float | None:
        if not raw or raw == _NULL:
            return None
        try:
            return float(raw)
        except ValueError:
            return None

    lat, lon = _num(parts[1]), _num(parts[2])
    elevation = _num(parts[3]) if len(parts) > 3 else None
    return lat, lon, elevation


def parse_wamd(payload: bytes) -> WamdMetadata:
    """Parse a `wamd` chunk payload. Never raises on malformed input."""
    fields: dict[str, object] = {}
    pos = 0
    while pos + _ENTRY_HEADER <= len(payload):
        type_id, size = struct.unpack_from("<HI", payload, pos)
        pos += _ENTRY_HEADER
        if pos + size > len(payload):
            break
        raw = payload[pos : pos + size]
        pos += size

        if type_id == _TYPE_MODEL:
            fields["model"] = raw.decode("utf-8", errors="replace")
        elif type_id == _TYPE_APP_VERSION:
            fields["app_version"] = raw.decode("utf-8", errors="replace")
        elif type_id == _TYPE_DEVICE:
            fields["device"] = raw.decode("utf-8", errors="replace")
        elif type_id == _TYPE_TIMESTAMP:
            fields["timestamp"] = _parse_timestamp(
                raw.decode("utf-8", errors="replace"),
            )
        elif type_id == _TYPE_POSITION:
            lat, lon, elev = _parse_position(raw.decode("utf-8", errors="replace"))
            fields["latitude"], fields["longitude"] = lat, lon
            fields["elevation_m"] = elev
        elif type_id == _TYPE_AUTO_ID:
            fields["auto_id"] = raw.decode("utf-8", errors="replace") or None
        elif type_id == _TYPE_MANUAL_ID:
            fields["manual_id"] = raw.decode("utf-8", errors="replace") or None

    return WamdMetadata(**fields)  # type: ignore[arg-type]
