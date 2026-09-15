from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import IdSource, Verdict
from fledermap.services.batdetect2 import (
    DetectionInput,
    MaterializeReport,
    PurgeReport,
    enqueue_batdetect2,
    materialize_claims,
    purge_source,
    record_detections,
)
from fledermap.store.models import ClassifierDetection, Identification, Recording, Taxon

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


def test_record_detections_replaces_a_source_s_existing_rows(engine: Engine) -> None:
    with OrmSession(engine) as session:
        recording = _recording(session, "a" * 64)
        taxon = _taxon(session, "Pipistrellus pipistrellus")

        record_detections(
            session,
            recording,
            IdSource.BATDETECT2,
            [
                DetectionInput(
                    taxon_id=taxon.id, raw_label=taxon.scientific_name, confidence=0.4
                )
            ],
        )
        session.commit()

        record_detections(
            session,
            recording,
            IdSource.BATDETECT2,
            [
                DetectionInput(
                    taxon_id=taxon.id, raw_label=taxon.scientific_name, confidence=0.9
                )
            ],
        )
        session.commit()

        rows = session.scalars(
            select(ClassifierDetection).where(
                ClassifierDetection.recording_id == recording.id,
                ClassifierDetection.source == IdSource.BATDETECT2,
            ),
        ).all()
        assert len(rows) == 1
        assert rows[0].confidence == 0.9


def test_record_detections_with_no_candidates_clears_prior_rows(engine: Engine) -> None:
    with OrmSession(engine) as session:
        recording = _recording(session, "b" * 64)
        taxon = _taxon(session, "Nyctalus noctula")

        record_detections(
            session,
            recording,
            IdSource.BATDETECT2,
            [
                DetectionInput(
                    taxon_id=taxon.id, raw_label=taxon.scientific_name, confidence=0.7
                )
            ],
        )
        session.commit()

        record_detections(session, recording, IdSource.BATDETECT2, [])
        session.commit()

        rows = session.scalars(
            select(ClassifierDetection).where(
                ClassifierDetection.recording_id == recording.id,
            ),
        ).all()
        assert rows == []


def test_materialize_claims_writes_species_claim_above_threshold(
    engine: Engine,
) -> None:
    with OrmSession(engine) as session:
        recording = _recording(session, "c" * 64)
        taxon = _taxon(session, "Pipistrellus pygmaeus")
        record_detections(
            session,
            recording,
            IdSource.BATDETECT2,
            [
                DetectionInput(
                    taxon_id=taxon.id, raw_label=taxon.scientific_name, confidence=0.9
                )
            ],
        )
        session.commit()

        report = materialize_claims(session, IdSource.BATDETECT2, threshold=0.8)
        session.commit()

        assert report == MaterializeReport(recordings_updated=1)
        idents = session.scalars(
            select(Identification).where(
                Identification.recording_id == recording.id,
                Identification.source == IdSource.BATDETECT2,
            ),
        ).all()
        assert len(idents) == 1
        assert idents[0].verdict == Verdict.SPECIES
        assert idents[0].taxon_id == taxon.id


def test_materialize_claims_writes_an_unmapped_species_claim_not_no_id(
    engine: Engine,
) -> None:
    """A detection clearing the threshold whose label has no taxon mapping is
    an "unmapped species" claim -- taxon_id None, raw_label preserved -- not a
    NO_ID and not silently dropped. Dropping it would be unrecoverable: NO_ID
    reads as a confident answer, and `enqueue_batdetect2` never re-runs the
    model for a recording that already has detection rows, so nothing would
    ever revisit it once a code mapping was added."""
    with OrmSession(engine) as session:
        recording = _recording(session, "j" * 64)
        record_detections(
            session,
            recording,
            IdSource.BATDETECT2,
            [
                DetectionInput(
                    taxon_id=None,
                    raw_label="Myotis alcathoe",
                    confidence=0.9,
                ),
            ],
        )
        session.commit()

        materialize_claims(session, IdSource.BATDETECT2, threshold=0.8)
        session.commit()

        ident = session.scalars(
            select(Identification).where(
                Identification.recording_id == recording.id,
                Identification.source == IdSource.BATDETECT2,
            ),
        ).one()
        assert ident.verdict == Verdict.SPECIES
        assert ident.taxon_id is None
        assert ident.raw_label == "Myotis alcathoe"


def test_materialize_claims_keeps_the_best_label_for_competing_unmapped_ones(
    engine: Engine,
) -> None:
    """Several unmapped labels can clear the threshold at once, and only one
    row per (recording, source) may have a NULL taxon_id
    (`uq_identification_source_claim`, `postgresql_nulls_not_distinct`). The
    survivor is the highest-confidence one, deterministically -- not whichever
    the row order happened to put last."""
    with OrmSession(engine) as session:
        recording = _recording(session, "k" * 64)
        record_detections(
            session,
            recording,
            IdSource.BATDETECT2,
            [
                DetectionInput(
                    taxon_id=None, raw_label="Myotis alcathoe", confidence=0.85
                ),
                DetectionInput(
                    taxon_id=None, raw_label="Myotis bechsteinii", confidence=0.95
                ),
                DetectionInput(
                    taxon_id=None, raw_label="Plecotus austriacus", confidence=0.9
                ),
            ],
        )
        session.commit()

        materialize_claims(session, IdSource.BATDETECT2, threshold=0.8)
        session.commit()

        ident = session.scalars(
            select(Identification).where(
                Identification.recording_id == recording.id,
                Identification.source == IdSource.BATDETECT2,
            ),
        ).one()
        assert ident.raw_label == "Myotis bechsteinii"


def test_materialize_claims_writes_no_id_when_nothing_clears_threshold(
    engine: Engine,
) -> None:
    with OrmSession(engine) as session:
        recording = _recording(session, "d" * 64)
        taxon = _taxon(session, "Eptesicus serotinus")
        record_detections(
            session,
            recording,
            IdSource.BATDETECT2,
            [
                DetectionInput(
                    taxon_id=taxon.id, raw_label=taxon.scientific_name, confidence=0.3
                )
            ],
        )
        session.commit()

        materialize_claims(session, IdSource.BATDETECT2, threshold=0.8)
        session.commit()

        idents = session.scalars(
            select(Identification).where(
                Identification.recording_id == recording.id,
                Identification.source == IdSource.BATDETECT2,
            ),
        ).all()
        assert len(idents) == 1
        assert idents[0].verdict == Verdict.NO_ID
        assert idents[0].taxon_id is None


def test_materialize_claims_is_idempotent_and_recomputes_on_threshold_change(
    engine: Engine,
) -> None:
    with OrmSession(engine) as session:
        recording = _recording(session, "e" * 64)
        taxon = _taxon(session, "Barbastella barbastellus")
        record_detections(
            session,
            recording,
            IdSource.BATDETECT2,
            [
                DetectionInput(
                    taxon_id=taxon.id, raw_label=taxon.scientific_name, confidence=0.6
                )
            ],
        )
        session.commit()

        materialize_claims(session, IdSource.BATDETECT2, threshold=0.8)
        session.commit()
        first = session.scalars(
            select(Identification).where(Identification.recording_id == recording.id),
        ).one()
        assert first.verdict == Verdict.NO_ID

        # Lower the threshold -- no re-run of the classifier, same stored raw score.
        materialize_claims(session, IdSource.BATDETECT2, threshold=0.5)
        session.commit()
        second = session.scalars(
            select(Identification).where(Identification.recording_id == recording.id),
        ).one()
        assert second.verdict == Verdict.SPECIES
        assert second.taxon_id == taxon.id


def test_materialize_claims_skips_recordings_with_no_raw_detections(
    engine: Engine,
) -> None:
    with OrmSession(engine) as session:
        _recording(session, "f" * 64)  # never processed by batdetect2 at all
        report = materialize_claims(session, IdSource.BATDETECT2, threshold=0.8)
        assert report == MaterializeReport(recordings_updated=0)


def test_purge_source_removes_detections_and_identifications_for_that_source_only(
    engine: Engine,
) -> None:
    with OrmSession(engine) as session:
        recording = _recording(session, "g" * 64)
        taxon = _taxon(session, "Rhinolophus ferrumequinum")
        record_detections(
            session,
            recording,
            IdSource.BATDETECT2,
            [
                DetectionInput(
                    taxon_id=taxon.id, raw_label=taxon.scientific_name, confidence=0.9
                )
            ],
        )
        session.commit()
        materialize_claims(session, IdSource.BATDETECT2, threshold=0.8)
        session.commit()
        # A second, unrelated source's claim on the same recording must survive.
        session.add(
            Identification(
                recording_id=recording.id,
                source=IdSource.EMT_GUANO,
                verdict=Verdict.SPECIES,
                taxon_id=taxon.id,
            ),
        )
        session.commit()

        report = purge_source(session, IdSource.BATDETECT2)
        session.commit()

        assert report == PurgeReport(detections_removed=1, identifications_removed=1)
        assert (
            session.scalars(
                select(ClassifierDetection).where(
                    ClassifierDetection.recording_id == recording.id,
                ),
            ).all()
            == []
        )
        remaining = session.scalars(
            select(Identification).where(Identification.recording_id == recording.id),
        ).all()
        assert len(remaining) == 1
        assert remaining[0].source == IdSource.EMT_GUANO


def test_purge_source_is_idempotent(engine: Engine) -> None:
    with OrmSession(engine) as session:
        report = purge_source(session, IdSource.BATDETECT2)
        assert report == PurgeReport(detections_removed=0, identifications_removed=0)


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
