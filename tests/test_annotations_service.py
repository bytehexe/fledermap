from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import AnnotationSource
from fledermap.services.annotations import (
    create_label_annotation,
    delete_label_annotation,
    list_annotations,
    update_label_annotation,
)
from fledermap.store.models import Annotation, Recording, Taxon

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
                source=AnnotationSource.LABEL,
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
                source=AnnotationSource.LABEL,
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


def test_create_label_annotation_requires_text_or_species(engine: Engine) -> None:
    with OrmSession(engine) as session:
        recording = _recording(session, "c" * 64)
        with pytest.raises(ValueError, match="literal|taxon_id"):
            create_label_annotation(
                session,
                recording,
                time_start_s=1.0,
                time_end_s=1.0,
                freq_min_hz=None,
                freq_max_hz=None,
                taxon_id=None,
                literal=None,
            )


def test_create_label_annotation_persists_a_point(engine: Engine) -> None:
    with OrmSession(engine) as session:
        recording = _recording(session, "d" * 64)

        created = create_label_annotation(
            session,
            recording,
            time_start_s=2.5,
            time_end_s=2.5,
            freq_min_hz=40_000.0,
            freq_max_hz=40_000.0,
            taxon_id=None,
            literal="buzz here",
        )

        rows = list_annotations(session, recording.id)
        assert len(rows) == 1
        assert rows[0].id == created.id
        assert rows[0].source == AnnotationSource.LABEL
        assert rows[0].literal == "buzz here"
        assert rows[0].taxon_id is None


def test_update_label_annotation_changes_text_and_species(engine: Engine) -> None:
    with OrmSession(engine) as session:
        taxon = Taxon(rank="species", scientific_name="Pipistrellus pipistrellus")
        session.add(taxon)
        session.flush()
        recording = _recording(session, "e" * 64)
        created = create_label_annotation(
            session,
            recording,
            time_start_s=1.0,
            time_end_s=2.0,
            freq_min_hz=20_000.0,
            freq_max_hz=50_000.0,
            taxon_id=None,
            literal="social call?",
        )

        update_label_annotation(
            session,
            created,
            taxon_id=taxon.id,
            literal="confirmed social call",
        )

        rows = list_annotations(session, recording.id)
        assert rows[0].taxon_id == taxon.id
        assert rows[0].literal == "confirmed social call"


def test_update_label_annotation_rejects_clearing_both_fields(engine: Engine) -> None:
    with OrmSession(engine) as session:
        recording = _recording(session, "f" * 64)
        created = create_label_annotation(
            session,
            recording,
            time_start_s=1.0,
            time_end_s=1.0,
            freq_min_hz=None,
            freq_max_hz=None,
            taxon_id=None,
            literal="a note",
        )

        with pytest.raises(ValueError, match="literal|taxon_id"):
            update_label_annotation(session, created, taxon_id=None, literal=None)


def test_delete_label_annotation_removes_the_row(engine: Engine) -> None:
    with OrmSession(engine) as session:
        recording = _recording(session, "g" * 64)
        created = create_label_annotation(
            session,
            recording,
            time_start_s=1.0,
            time_end_s=1.0,
            freq_min_hz=None,
            freq_max_hz=None,
            taxon_id=None,
            literal="delete me",
        )

        delete_label_annotation(session, created)

        assert list_annotations(session, recording.id) == []


def test_update_label_annotation_rejects_a_non_label_source(engine: Engine) -> None:
    """Written before any non-label producer exists, so this guard is exercised now
    rather than only once a future batdetect2 producer's rows are at risk."""
    with OrmSession(engine) as session:
        recording = _recording(session, "h" * 64)
        detection = Annotation(
            recording_id=recording.id,
            source=AnnotationSource.BATDETECT2,
            time_start_s=1.0,
            time_end_s=1.5,
            freq_min_hz=30_000.0,
            freq_max_hz=45_000.0,
            confidence=0.8,
            created_at=datetime.now(UTC),
        )
        session.add(detection)
        session.commit()

        with pytest.raises(ValueError, match="label"):
            update_label_annotation(
                session, detection, taxon_id=None, literal="hijacked"
            )
        with pytest.raises(ValueError, match="label"):
            delete_label_annotation(session, detection)
