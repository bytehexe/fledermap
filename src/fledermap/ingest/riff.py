"""Streaming RIFF/WAVE chunk parsing.

Never loads a whole file into memory: recordings run to hundreds of megabytes.
"""

from __future__ import annotations

import struct
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

_HEADER = 8


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
