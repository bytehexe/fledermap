"""Streaming RIFF/WAVE chunk parsing.

Never loads a whole file into memory: recordings run to hundreds of megabytes.
"""

from __future__ import annotations

import hashlib
import struct
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

_HEADER = 8
_BLOCK = 1024 * 1024


class NotARiffFileError(Exception):
    """The file is not a RIFF/WAVE container."""


@dataclass(frozen=True)
class Chunk:
    """One RIFF sub-chunk. `offset` points at the payload, not the header."""

    chunk_id: str
    offset: int
    size: int


def iter_chunks(path: Path) -> Iterator[Chunk]:
    """Yield every sub-chunk in file order."""
    with path.open("rb") as fh:
        header = fh.read(12)
        if len(header) < 12 or header[0:4] != b"RIFF" or header[8:12] != b"WAVE":
            msg = f"{path} is not a RIFF/WAVE file"
            raise NotARiffFileError(msg)

        while True:
            raw = fh.read(_HEADER)
            if len(raw) < _HEADER:
                return
            chunk_id = raw[0:4].decode("ascii", errors="replace")
            (size,) = struct.unpack("<I", raw[4:8])
            yield Chunk(chunk_id=chunk_id, offset=fh.tell(), size=size)
            fh.seek(size + (size % 2), 1)


def read_chunk(path: Path, chunk: Chunk) -> bytes:
    """Read one chunk's payload."""
    with path.open("rb") as fh:
        fh.seek(chunk.offset)
        return fh.read(chunk.size)


class MissingAudioChunkError(Exception):
    """The file lacks a `fmt ` or `data` chunk and cannot be identified."""


def audio_hash(path: Path) -> str:
    """Identity of a recording: sha256 over the `fmt ` and `data` payloads only.

    Deliberately excludes every metadata chunk. The Echo Meter Touch renames
    files and rewrites its metadata when auto-ID is re-run; hashing the audio
    payload means that is recognised as the *same* recording rather than a
    duplicate. See spec D8.
    """
    chunks = {c.chunk_id: c for c in iter_chunks(path)}
    try:
        fmt_chunk, data_chunk = chunks["fmt "], chunks["data"]
    except KeyError as exc:
        msg = f"{path} has no {exc.args[0]!r} chunk"
        raise MissingAudioChunkError(msg) from exc

    digest = hashlib.sha256()
    with path.open("rb") as fh:
        fh.seek(fmt_chunk.offset)
        digest.update(fh.read(fmt_chunk.size))

        fh.seek(data_chunk.offset)
        remaining = data_chunk.size
        while remaining > 0:
            block = fh.read(min(_BLOCK, remaining))
            if not block:
                break
            digest.update(block)
            remaining -= len(block)

    return digest.hexdigest()


@dataclass(frozen=True)
class AudioFormat:
    """PCM parameters plus the duration implied by the `data` chunk size."""

    samplerate_hz: int
    channels: int
    bits: int
    duration_s: float


def read_format(path: Path) -> AudioFormat:
    """Read `fmt ` and derive duration from the `data` chunk size.

    Duration comes from byte counts rather than any metadata field, so it is
    correct even when the detector writes none.
    """
    chunks = {c.chunk_id: c for c in iter_chunks(path)}
    try:
        fmt_chunk, data_chunk = chunks["fmt "], chunks["data"]
    except KeyError as exc:
        msg = f"{path} has no {exc.args[0]!r} chunk"
        raise MissingAudioChunkError(msg) from exc

    payload = read_chunk(path, fmt_chunk)
    _, channels, samplerate, byte_rate, _, bits = struct.unpack_from("<HHIIHH", payload)
    duration = data_chunk.size / byte_rate if byte_rate else 0.0
    return AudioFormat(
        samplerate_hz=samplerate,
        channels=channels,
        bits=bits,
        duration_s=duration,
    )
