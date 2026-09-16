from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import IdSource
from fledermap.services.batdetect2 import DetectionInput, enqueue_batdetect2
from fledermap.services.classifier_detections import record_detections
from fledermap.store.models import Recording, Taxon

pytestmark = pytest.mark.db


def _recording(session: OrmSession, audio_hash: str) -> Recording:
    r = Recording(
        audio_hash=audio_hash,
        path=f"{audio_hash}.wav",
        recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
    )
    session.add(r)
    session.commit()
    return r


def _taxon(session: OrmSession, scientific_name: str) -> Taxon:
    taxon = Taxon(rank="species", scientific_name=scientific_name)
    session.add(taxon)
    session.commit()
    return taxon


def test_enqueue_batdetect2_only_enqueues_recordings_missing_a_detection_row(
    engine: Engine,
) -> None:
    from fledermap.jobs.app import ensure_schema
    from fledermap.jobs.tasks import app as jobs_app

    jobs_app.open(engine)
    ensure_schema(jobs_app, engine)

    with OrmSession(engine) as session:
        with_detections = _recording(session, "h" * 64)
        _recording(session, "i" * 64)  # left without a detection row
        taxon = _taxon(session, "Myotis daubentonii")
        record_detections(
            session,
            with_detections,
            IdSource.BATDETECT2,
            [
                DetectionInput(
                    taxon_id=taxon.id, raw_label=taxon.scientific_name, confidence=0.5
                )
            ],
        )
        session.commit()

        count = enqueue_batdetect2(session, engine)

        assert count == 1
