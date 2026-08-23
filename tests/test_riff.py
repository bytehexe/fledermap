from __future__ import annotations

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
