"""Domain records produced by ingest. No I/O, no ORM, no framework."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from fledermap.domain.codes import IdSource, Verdict


@dataclass(frozen=True)
class ParsedIdentification:
    """One source's claim about a recording. Sources coexist; none overwrites another."""

    source: IdSource
    source_version: str | None
    verdict: Verdict
    raw_label: str | None


@dataclass(frozen=True)
class RecordingMetadata:
    """Everything ingest knows about one file, before it reaches the database."""

    recorded_at: datetime
    filename_at: datetime | None = None
    metadata_at: datetime | None = None
    timestamp_disagreement_s: float | None = None
    latitude: float | None = None
    longitude: float | None = None
    elevation_m: float | None = None
    loc_accuracy_m: float | None = None
    samplerate_hz: int | None = None
    duration_s: float | None = None
    snr_db: float | None = None
    te_factor: int | None = None
    make: str | None = None
    model: str | None = None
    serial: str | None = None
    device: str | None = None  # host phone, from wamd; NOT the detector
    device_note: str | None = None
    guano_raw: dict[str, str] = field(default_factory=dict)
    identifications: tuple[ParsedIdentification, ...] = ()


@dataclass(frozen=True)
class ScannedFile:
    """One file as found on disk, ready to be committed to the database."""

    audio_hash: str
    path: Path
    size_bytes: int
    mtime: float
    metadata: RecordingMetadata


@dataclass(frozen=True)
class UnchangedFile:
    """A file whose (path, size, mtime) matches what was already recorded for
    it -- ingest can trust the already-known audio_hash without re-reading,
    re-hashing, or re-computing SNR for its content."""

    audio_hash: str
