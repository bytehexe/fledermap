from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from fledermap.ingest.guano_read import parse_guano
from tests.fixtures import build_wav, fmt_payload

GUANO = (
    "GUANO|Version: 1.0\n"
    "Timestamp: 2026-08-21T21:45:32+02:00\n"
    "Make: Wildlife Acoustics, Inc.\n"
    "Model: Echo Meter Touch 2\n"
    "Samplerate: 256000\n"
    "Loc Position: 52.519400 13.401200\n"
    "Loc Elevation: 34.5\n"
    "Loc Accuracy: 6.0\n"
    "Species Auto ID: PIPPIP\n"
    "Species Manual ID: PIPPYG\n"
    "Note: field note\n"
)


def _wav(tmp_path: Path, guano: str | None) -> Path:
    chunks = [(b"fmt ", fmt_payload()), (b"data", b"\x00" * 8)]
    if guano is not None:
        chunks.append((b"guan", guano.encode("utf-8")))
    path = tmp_path / "a.wav"
    path.write_bytes(build_wav(chunks))
    return path


def test_returns_none_when_no_guan_chunk(tmp_path: Path) -> None:
    """The real EMT samples are exactly this case."""
    assert parse_guano(_wav(tmp_path, None)) is None


def test_parses_standard_fields(tmp_path: Path) -> None:
    meta = parse_guano(_wav(tmp_path, GUANO))

    assert meta is not None
    assert meta.model == "Echo Meter Touch 2"
    assert meta.timestamp == datetime(
        2026,
        8,
        21,
        21,
        45,
        32,
        tzinfo=timezone(timedelta(hours=2)),
    )
    assert meta.latitude == 52.519400
    assert meta.longitude == 13.401200
    assert meta.elevation_m == 34.5
    assert meta.loc_accuracy_m == 6.0
    assert meta.auto_id == "PIPPIP"
    assert meta.manual_id == "PIPPYG"


def test_raw_keeps_every_key(tmp_path: Path) -> None:
    """Unmodelled keys must survive into `guano_raw` (spec section 5)."""
    meta = parse_guano(_wav(tmp_path, GUANO))

    assert meta is not None
    assert meta.raw["Note"] == "field note"
    assert "GUANO|Version" in meta.raw


def test_missing_position_is_none(tmp_path: Path) -> None:
    guano = "GUANO|Version: 1.0\nTimestamp: 2026-08-21T21:45:32+02:00\n"
    meta = parse_guano(_wav(tmp_path, guano))

    assert meta is not None
    assert meta.latitude is None
    assert meta.longitude is None
