"""`services.noise.classify_noise` -- the fledermap.noise classifier (Step 2
of the Obsidian backlog "Improve data quality" item). Lowest precedence,
recomputed from each recording's own `snr_db` and the currently configured
threshold every time it runs -- see current_best.py's `_PRECEDENCE` for how
that's enforced."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import IdSource, Verdict
from fledermap.services.noise import classify_noise
from fledermap.store.models import Identification, Recording

pytestmark = pytest.mark.db


def _recording(*, audio_hash: str, snr_db: float | None) -> Recording:
    return Recording(
        audio_hash=audio_hash,
        path="a.wav",
        recorded_at=datetime(2026, 8, 25, 21, 0, tzinfo=UTC),
        snr_db=snr_db,
    )


def test_classify_noise_marks_a_low_snr_recording_as_noise(engine: Engine) -> None:
    with OrmSession(engine) as session:
        recording = _recording(audio_hash="a" * 64, snr_db=1.0)
        session.add(recording)
        session.commit()

        report = classify_noise(session, threshold_db=2.0)
        session.commit()

        ident = session.scalars(
            select(Identification).where(
                Identification.recording_id == recording.id,
                Identification.source == IdSource.FLEDERMAP_NOISE,
            ),
        ).one()
        assert ident.verdict == Verdict.NOISE
        assert ident.taxon_id is None
        assert report.classified == 1


def test_classify_noise_marks_a_high_snr_recording_as_no_id(engine: Engine) -> None:
    with OrmSession(engine) as session:
        recording = _recording(audio_hash="b" * 64, snr_db=5.0)
        session.add(recording)
        session.commit()

        classify_noise(session, threshold_db=2.0)
        session.commit()

        ident = session.scalars(
            select(Identification).where(
                Identification.recording_id == recording.id,
                Identification.source == IdSource.FLEDERMAP_NOISE,
            ),
        ).one()
        assert ident.verdict == Verdict.NO_ID


def test_classify_noise_skips_a_recording_with_no_snr_yet(engine: Engine) -> None:
    with OrmSession(engine) as session:
        recording = _recording(audio_hash="c" * 64, snr_db=None)
        session.add(recording)
        session.commit()

        report = classify_noise(session, threshold_db=2.0)

        idents = session.scalars(
            select(Identification).where(
                Identification.recording_id == recording.id,
            ),
        ).all()
        assert idents == []
        assert report.classified == 0


def test_classify_noise_updates_an_existing_claim_when_the_threshold_changes(
    engine: Engine,
) -> None:
    with OrmSession(engine) as session:
        recording = _recording(audio_hash="d" * 64, snr_db=3.0)
        session.add(recording)
        session.commit()

        classify_noise(session, threshold_db=2.0)  # 3.0 >= 2.0 -> NO_ID
        session.commit()

        classify_noise(session, threshold_db=4.0)  # 3.0 < 4.0 -> NOISE now
        session.commit()

        idents = session.scalars(
            select(Identification).where(
                Identification.recording_id == recording.id,
                Identification.source == IdSource.FLEDERMAP_NOISE,
            ),
        ).all()
        assert len(idents) == 1
        assert idents[0].verdict == Verdict.NOISE
