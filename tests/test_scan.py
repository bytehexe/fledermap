from __future__ import annotations

import os
import time
from pathlib import Path

from fledermap.ingest.scan import scan
from tests.fixtures import build_wav, fmt_payload, wamd_payload


def _emt_file(directory: Path, name: str, *, audio: bytes = b"\x01\x02" * 32) -> Path:
    path = directory / name
    path.write_bytes(
        build_wav(
            [
                (b"fmt ", fmt_payload()),
                (b"data", audio),
                (b"wamd", wamd_payload()),
            ],
        ),
    )
    old = time.time() - 3600
    os.utime(path, (old, old))
    return path


def test_scans_nested_directories(tmp_path: Path) -> None:
    session = tmp_path / "Session_20130401_053030"
    session.mkdir()
    _emt_file(session, "EPTSER_20150610_215446.wav")
    _emt_file(session, "MYODAU_20150623_213547.wav", audio=b"\x09\x08" * 32)

    results = list(scan(tmp_path))

    assert len(results) == 2
    assert {r.path.name for r in results} == {
        "EPTSER_20150610_215446.wav",
        "MYODAU_20150623_213547.wav",
    }


def test_non_wav_files_are_skipped(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("not a recording")
    (tmp_path / "fake.wav").write_bytes(b"definitely not RIFF")
    _emt_file(tmp_path, "EPTSER_20150610_215446.wav")

    assert len(list(scan(tmp_path))) == 1


def test_recently_written_files_are_skipped(tmp_path: Path) -> None:
    """The settle rule: a file mid-sync must not be ingested truncated."""
    path = tmp_path / "EPTSER_20150610_215446.wav"
    path.write_bytes(
        build_wav(
            [
                (b"fmt ", fmt_payload()),
                (b"data", b"\x01\x02"),
                (b"wamd", wamd_payload()),
            ]
        ),
    )
    os.utime(path, None)  # mtime = now

    assert list(scan(tmp_path, settle_seconds=30.0)) == []


def test_settled_files_are_included(tmp_path: Path) -> None:
    _emt_file(tmp_path, "EPTSER_20150610_215446.wav")

    assert len(list(scan(tmp_path, settle_seconds=30.0))) == 1


def test_scan_populates_hash_and_metadata(tmp_path: Path) -> None:
    _emt_file(tmp_path, "EPTSER_20150610_215446.wav")

    result = next(iter(scan(tmp_path)))

    assert len(result.audio_hash) == 64
    assert result.metadata.recorded_at.hour == 21
    assert result.metadata.latitude == 42.346973
    assert result.metadata.samplerate_hz == 256000
    assert result.metadata.duration_s is not None


def test_scan_does_not_modify_the_archive(tmp_path: Path) -> None:
    """Ingest is strictly read-only on archive_root."""
    path = _emt_file(tmp_path, "EPTSER_20150610_215446.wav")
    before = (path.stat().st_mtime, path.read_bytes())

    list(scan(tmp_path))

    assert (path.stat().st_mtime, path.read_bytes()) == before
