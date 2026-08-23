from __future__ import annotations

from pathlib import Path

import pytest

from fledermap.ingest.riff import (
    MissingAudioChunkError,
    audio_hash,
    read_format,
)
from tests.fixtures import build_wav, fmt_payload


def _write(path: Path, chunks: list[tuple[bytes, bytes]]) -> Path:
    path.write_bytes(build_wav(chunks))
    return path


def test_hash_is_stable_and_hex(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "a.wav",
        [(b"fmt ", fmt_payload()), (b"data", b"\x01\x02\x03\x04")],
    )

    digest = audio_hash(path)

    assert len(digest) == 64
    assert digest == audio_hash(path)


def test_metadata_change_does_not_change_hash(tmp_path: Path) -> None:
    """THE load-bearing test: re-ID rewrites metadata, identity must survive."""
    audio = b"\x11\x22\x33\x44" * 64
    before = _write(
        tmp_path / "NoID_20260821_214532.wav",
        [
            (b"fmt ", fmt_payload()),
            (b"data", audio),
            (b"guan", b"GUANO|Version: 1.0\nSpecies Auto ID: \n"),
        ],
    )
    after = _write(
        tmp_path / "PIPPIP_20260821_214532.wav",
        [
            (b"fmt ", fmt_payload()),
            (b"data", audio),
            (b"guan", b"GUANO|Version: 1.0\nSpecies Auto ID: PIPPIP\nNote: re-run\n"),
        ],
    )

    assert audio_hash(before) == audio_hash(after)


def test_chunk_order_does_not_change_hash(tmp_path: Path) -> None:
    """GUANO may sit anywhere in the container; ordering must not matter."""
    audio = b"\xaa\xbb" * 32
    a = _write(
        tmp_path / "a.wav",
        [(b"fmt ", fmt_payload()), (b"data", audio), (b"guan", b"x")],
    )
    b = _write(
        tmp_path / "b.wav",
        [(b"fmt ", fmt_payload()), (b"guan", b"x"), (b"data", audio)],
    )

    assert audio_hash(a) == audio_hash(b)


def test_different_audio_changes_hash(tmp_path: Path) -> None:
    a = _write(tmp_path / "a.wav", [(b"fmt ", fmt_payload()), (b"data", b"\x01\x02")])
    b = _write(tmp_path / "b.wav", [(b"fmt ", fmt_payload()), (b"data", b"\x03\x04")])

    assert audio_hash(a) != audio_hash(b)


def test_different_samplerate_changes_hash(tmp_path: Path) -> None:
    """`fmt ` is hashed too, so identical payloads at different rates differ."""
    audio = b"\x01\x02\x03\x04"
    a = _write(
        tmp_path / "a.wav",
        [(b"fmt ", fmt_payload(samplerate=256000)), (b"data", audio)],
    )
    b = _write(
        tmp_path / "b.wav",
        [(b"fmt ", fmt_payload(samplerate=384000)), (b"data", audio)],
    )

    assert audio_hash(a) != audio_hash(b)


def test_missing_data_chunk_raises(tmp_path: Path) -> None:
    path = _write(tmp_path / "a.wav", [(b"fmt ", fmt_payload())])

    with pytest.raises(MissingAudioChunkError):
        audio_hash(path)


def test_read_format_reports_rate_and_duration(tmp_path: Path) -> None:
    """One second of 256 kHz 16-bit mono is 512000 bytes of `data`."""
    path = _write(
        tmp_path / "a.wav",
        [(b"fmt ", fmt_payload(samplerate=256000)), (b"data", b"\x00" * 512000)],
    )

    fmt = read_format(path)

    assert fmt.samplerate_hz == 256000
    assert fmt.channels == 1
    assert fmt.bits == 16
    assert fmt.duration_s == pytest.approx(1.0)
