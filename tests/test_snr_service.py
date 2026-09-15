"""`services.snr.backfill_snr` -- fills in `Recording.snr_db` for recordings
ingested before SNR computation shipped."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session as OrmSession

from fledermap.services.snr import backfill_snr
from fledermap.store.models import Recording
from tests.fixtures import build_wav, fmt_payload

pytestmark = pytest.mark.db


def _write_recording(
    session: OrmSession,
    archive_root: Path,
    *,
    audio_hash: str = "a" * 64,
    snr_db: float | None = None,
) -> Recording:
    path = archive_root / "rec.wav"
    n_samples = 25_600
    audio = bytes(2 * n_samples)
    path.write_bytes(build_wav([(b"fmt ", fmt_payload(256000)), (b"data", audio)]))

    recording = Recording(
        audio_hash=audio_hash,
        path="rec.wav",
        archive_root_index=0,
        recorded_at=datetime(2015, 6, 10, 21, 54, 46, tzinfo=UTC),
        samplerate_hz=256000,
        snr_db=snr_db,
    )
    session.add(recording)
    session.flush()
    return recording


def test_backfill_snr_fills_in_a_missing_value(engine: Engine, tmp_path: Path) -> None:
    with OrmSession(engine) as session:
        recording = _write_recording(session, tmp_path)
        session.commit()

        count = backfill_snr(session, (tmp_path,))
        session.commit()

        session.refresh(recording)
        assert count == 1
        assert recording.snr_db is not None


def test_backfill_snr_skips_a_recording_that_already_has_a_value(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        _write_recording(session, tmp_path, snr_db=9.9)
        session.commit()

        count = backfill_snr(session, (tmp_path,))

        assert count == 0
