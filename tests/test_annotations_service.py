from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import AnnotationSource
from fledermap.store.models import Annotation, Recording

pytestmark = pytest.mark.db


def _recording(session: OrmSession, audio_hash: str) -> Recording:
    r = Recording(
        audio_hash=audio_hash,
        path=f"{audio_hash}.wav",
        recorded_at=datetime(2026, 9, 23, tzinfo=UTC),
    )
    session.add(r)
    session.commit()
    return r


def test_annotation_with_no_time_or_freq_bound_is_rejected(engine: Engine) -> None:
    with OrmSession(engine) as session:
        recording = _recording(session, "a" * 64)
        session.add(
            Annotation(
                recording_id=recording.id,
                source=AnnotationSource.COMMENT,
                literal="orphan note",
                created_at=datetime.now(UTC),
            ),
        )
        with pytest.raises(IntegrityError, match="ck_annotation_has_bound"):
            session.commit()


def test_deleting_a_recording_cascades_to_its_annotations(engine: Engine) -> None:
    with OrmSession(engine) as session:
        recording = _recording(session, "b" * 64)
        session.add(
            Annotation(
                recording_id=recording.id,
                source=AnnotationSource.COMMENT,
                time_start_s=1.0,
                time_end_s=1.0,
                literal="a call",
                created_at=datetime.now(UTC),
            ),
        )
        session.commit()

        session.delete(recording)
        session.commit()

        assert session.get(Annotation, 1) is None
