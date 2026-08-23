from __future__ import annotations

import os
import stat
import sys
import time
from pathlib import Path

import pytest

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


def test_dotted_archive_root_is_still_scannable(tmp_path: Path) -> None:
    """The hidden-component check is relative to root, not to the filesystem root.

    An archive living under a dot-prefixed directory (e.g. under `~/.local/...`)
    must not be blackholed: `path.parts` on an absolute path would otherwise
    include every dotted ancestor, not just dotted components inside the archive.
    """
    root = tmp_path / ".hidden_archive"
    root.mkdir()
    _emt_file(root, "EPTSER_20150610_215446.wav")

    assert len(list(scan(root))) == 1


def test_hidden_subdirectory_inside_archive_is_skipped(tmp_path: Path) -> None:
    """Syncthing's `.stfolder` (and similar) must still be skipped."""
    hidden = tmp_path / ".stfolder"
    hidden.mkdir()
    _emt_file(hidden, "EPTSER_20150610_215446.wav")
    _emt_file(tmp_path, "MYODAU_20150623_213547.wav", audio=b"\x09\x08" * 32)

    results = list(scan(tmp_path))

    assert len(results) == 1
    assert results[0].path.name == "MYODAU_20150623_213547.wav"


def test_sync_temp_suffix_is_skipped(tmp_path: Path) -> None:
    path = _emt_file(tmp_path, "EPTSER_20150610_215446.wav.syncthing")

    assert list(scan(tmp_path)) == []
    assert path.exists()  # sanity: it was created, just skipped


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits only")
@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0, reason="root ignores permission bits"
)
def test_unreadable_file_does_not_abort_the_whole_scan(tmp_path: Path) -> None:
    """One flaky/unreadable file must not stop the scan from reaching the rest."""
    bad = _emt_file(tmp_path, "EPTSER_20150610_215446.wav")
    _emt_file(tmp_path, "MYODAU_20150623_213547.wav", audio=b"\x09\x08" * 32)
    bad.chmod(0o000)

    try:
        results = list(scan(tmp_path))
    finally:
        bad.chmod(stat.S_IRUSR | stat.S_IWUSR)

    assert len(results) == 1
    assert results[0].path.name == "MYODAU_20150623_213547.wav"
