from __future__ import annotations

import struct
from pathlib import Path

import pytest

from fledermap.ingest.riff import NotARiffFileError, iter_chunks, read_chunk
from tests.fixtures import build_wav, fmt_payload, minimal_wav


def test_iter_chunks_finds_fmt_and_data(tmp_path: Path) -> None:
    path = tmp_path / "a.wav"
    path.write_bytes(minimal_wav(audio=b"\x01\x00\x02\x00"))

    chunks = {c.chunk_id: c for c in iter_chunks(path)}

    assert set(chunks) == {"fmt ", "data"}
    assert chunks["data"].size == 4


def test_iter_chunks_reads_trailing_wamd(tmp_path: Path) -> None:
    """The real EMT samples put `wamd` after `data`, at the end of the file."""
    path = tmp_path / "b.wav"
    path.write_bytes(
        build_wav(
            [
                (b"fmt ", fmt_payload()),
                (b"data", b"\x00" * 10),
                (b"wamd", b"\x01\x02\x03"),
            ],
        ),
    )

    assert [c.chunk_id for c in iter_chunks(path)] == ["fmt ", "data", "wamd"]


def test_odd_sized_chunk_is_padded(tmp_path: Path) -> None:
    """A 3-byte chunk is followed by a pad byte; the next chunk must still be found."""
    path = tmp_path / "c.wav"
    path.write_bytes(
        build_wav(
            [(b"fmt ", fmt_payload()), (b"guan", b"abc"), (b"data", b"\x00\x00")],
        ),
    )

    assert [c.chunk_id for c in iter_chunks(path)] == ["fmt ", "guan", "data"]


def test_odd_sized_chunk_without_pad_byte_is_still_found(tmp_path: Path) -> None:
    """Real Echo Meter Touch 2 (Android) output does not write the RIFF pad
    byte after an odd-sized chunk -- confirmed against real field recordings,
    2026-08-26 (a `guan` chunk of size 605 is immediately followed by the
    literal bytes `wamd`, no `\\x00` in between). Unlike
    `test_odd_sized_chunk_is_padded` above, this file has no pad byte at all;
    the next chunk's header starts right after the odd-sized payload."""
    body = b"WAVE"
    body += b"fmt " + struct.pack("<I", len(fmt_payload())) + fmt_payload()
    body += b"guan" + struct.pack("<I", 3) + b"abc"  # odd size, no pad byte
    body += b"data" + struct.pack("<I", 2) + b"\x00\x00"
    path = tmp_path / "unpadded.wav"
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)

    chunks = list(iter_chunks(path))

    assert [c.chunk_id for c in chunks] == ["fmt ", "guan", "data"]
    guan = next(c for c in chunks if c.chunk_id == "guan")
    assert read_chunk(path, guan) == b"abc"
    data = next(c for c in chunks if c.chunk_id == "data")
    assert read_chunk(path, data) == b"\x00\x00"


def test_read_chunk_returns_payload(tmp_path: Path) -> None:
    path = tmp_path / "d.wav"
    path.write_bytes(
        build_wav([(b"fmt ", fmt_payload()), (b"data", b"\xde\xad\xbe\xef")]),
    )

    data = next(c for c in iter_chunks(path) if c.chunk_id == "data")

    assert read_chunk(path, data) == b"\xde\xad\xbe\xef"


def test_non_riff_file_raises(tmp_path: Path) -> None:
    path = tmp_path / "not.wav"
    path.write_bytes(b"this is not a RIFF file at all")

    with pytest.raises(NotARiffFileError):
        list(iter_chunks(path))
