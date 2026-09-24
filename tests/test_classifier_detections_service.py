from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import AnnotationSource, IdSource, Verdict
from fledermap.services.annotations import create_comment_annotation
from fledermap.services.classifier_detections import (
    DetectionInput,
    MaterializeReport,
    PurgeReport,
    materialize_claims,
    purge_source,
    record_detections,
)
from fledermap.store.models import (
    Annotation,
    ClassifierDetection,
    Identification,
    Recording,
    Taxon,
    TaxonCode,
)

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


def test_record_detections_with_no_candidates_replaces_prior_rows_with_a_noid_marker(
    engine: Engine,
) -> None:
    """A run that finds nothing is still a completed classification: it leaves one explicit NoID
    row (not zero rows), which is what tells `enqueue_*` the recording is done. Zero rows reads as
    "never ran" and re-queues the recording every ingest cycle, forever."""
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
        assert len(rows) == 1  # the prior species row is gone, replaced by the marker
        assert rows[0].taxon_id is None
        assert rows[0].raw_label == "NoID"
        assert rows[0].confidence == 0.0


@pytest.mark.parametrize("source", [IdSource.BATDETECT2, IdSource.BATTYBIRDNET])
def test_a_recording_a_classifier_found_nothing_in_is_not_re_enqueued_and_reads_as_no_id(
    engine: Engine, source: IdSource
) -> None:
    with OrmSession(engine) as session:
        recording = _recording(session, "9" * 64)
        record_detections(session, recording, source, [])
        session.commit()

        already_classified = session.scalar(
            select(ClassifierDetection.id).where(
                ClassifierDetection.recording_id == recording.id,
                ClassifierDetection.source == source,
            )
        )
        assert already_classified is not None  # what the enqueue anti-join looks for

        materialize_claims(session, source, threshold=0.8)
        session.commit()
        idents = session.scalars(
            select(Identification).where(
                Identification.recording_id == recording.id,
                Identification.source == source,
            ),
        ).all()
        assert [(i.verdict, i.taxon_id, i.raw_label) for i in idents] == [
            (Verdict.NO_ID, None, None)
        ]


def test_the_noid_marker_still_reads_as_no_id_at_a_zero_threshold(
    engine: Engine,
) -> None:
    """Threshold 0 lets the 0.0-confidence marker through as a passing detection; it must resolve
    through `sentinel_verdict` to the same NO_ID claim, not an unmapped-species one."""
    with OrmSession(engine) as session:
        recording = _recording(session, "8" * 64)
        record_detections(session, recording, IdSource.BATDETECT2, [])
        session.commit()

        materialize_claims(session, IdSource.BATDETECT2, threshold=0.0)
        session.commit()
        idents = session.scalars(
            select(Identification).where(Identification.recording_id == recording.id),
        ).all()
        assert [(i.verdict, i.taxon_id, i.raw_label) for i in idents] == [
            (Verdict.NO_ID, None, None)
        ]


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


def test_materialize_claims_reconnects_a_cached_unmapped_label_once_a_code_is_added(
    engine: Engine,
) -> None:
    """A code mapping added to the taxonomy AFTER the classification job
    already cached a detection as unmapped must still reach it:
    `enqueue_batdetect2` never re-runs the model for a recording that
    already has detection rows, so `materialize_claims` re-resolving
    `raw_label` itself, every cycle, is the only path a later-added mapping
    has to an already-cached, already-unmapped detection."""
    with OrmSession(engine) as session:
        recording = _recording(session, "m" * 64)
        record_detections(
            session,
            recording,
            IdSource.BATDETECT2,
            [
                DetectionInput(
                    taxon_id=None, raw_label="Myotis alcathoe", confidence=0.9
                )
            ],
        )
        session.commit()

        materialize_claims(session, IdSource.BATDETECT2, threshold=0.8)
        session.commit()

        before = session.scalars(
            select(Identification).where(
                Identification.recording_id == recording.id,
                Identification.source == IdSource.BATDETECT2,
            ),
        ).one()
        assert before.taxon_id is None

        # Simulate a taxonomy mapping added after the job already ran --
        # `classifier_detection.taxon_id` for this row is still None, and
        # nothing ever re-runs the model to refresh it.
        taxon = _taxon(session, "Myotis alcathoe")
        session.add(
            TaxonCode(source="batdetect2", code="Myotis alcathoe", taxon_id=taxon.id)
        )
        session.commit()

        materialize_claims(session, IdSource.BATDETECT2, threshold=0.8)
        session.commit()

        after = session.scalars(
            select(Identification).where(
                Identification.recording_id == recording.id,
                Identification.source == IdSource.BATDETECT2,
            ),
        ).one()
        assert after.taxon_id == taxon.id
        assert after.verdict == Verdict.SPECIES


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

        assert report == PurgeReport(
            detections_removed=1, identifications_removed=1, annotations_removed=0
        )
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
        assert report == PurgeReport(
            detections_removed=0, identifications_removed=0, annotations_removed=0
        )


def test_purge_source_removes_annotations_for_that_source_only(engine: Engine) -> None:
    with OrmSession(engine) as session:
        recording = Recording(
            audio_hash="ps" * 32,
            path="ps.wav",
            recorded_at=datetime(2026, 9, 24, tzinfo=UTC),
        )
        session.add(recording)
        session.commit()

        record_detections(
            session,
            recording,
            IdSource.BATDETECT2,
            [
                DetectionInput(
                    taxon_id=None,
                    raw_label="x",
                    confidence=0.9,
                    time_start_s=0.0,
                    time_end_s=0.01,
                    freq_min_hz=10000.0,
                    freq_max_hz=20000.0,
                ),
            ],
        )
        create_comment_annotation(
            session,
            recording,
            time_start_s=2.0,
            time_end_s=2.0,
            freq_min_hz=None,
            freq_max_hz=None,
            taxon_id=None,
            literal="keep me",
        )
        session.commit()

        report = purge_source(session, IdSource.BATDETECT2)
        session.commit()

        assert report.annotations_removed == 1
        remaining = session.scalars(select(Annotation)).all()
        assert len(remaining) == 1
        assert remaining[0].source == AnnotationSource.COMMENT


def test_materialize_claims_writes_a_noise_verdict_for_a_sentinel_label(
    engine: Engine,
) -> None:
    """A detection whose raw_label is a known sentinel (e.g. "NOISE") must
    become a NOISE-verdict claim, not an unmapped-species claim -- proves
    materialize_claims checks sentinel_verdict before resolve_code."""
    with OrmSession(engine) as session:
        recording = _recording(session, "n" * 64)
        record_detections(
            session,
            recording,
            IdSource.BATDETECT2,
            [DetectionInput(taxon_id=None, raw_label="NOISE", confidence=0.9)],
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
        assert ident.verdict == Verdict.NOISE
        assert ident.taxon_id is None


def test_materialize_claims_lets_noise_win_the_null_taxon_slot_by_confidence(
    engine: Engine,
) -> None:
    """A NOISE sentinel and an unmapped-species label both resolve to
    taxon_id=None, and `uq_identification_source_claim` allows only one
    (recording, source, taxon_id=NULL) row -- so the two candidates compete
    for that single slot, and the highest-confidence one must win. Here
    NOISE has the higher confidence, so the surviving claim must be
    Verdict.NOISE, and the unmapped-species candidate must produce no claim
    of its own."""
    with OrmSession(engine) as session:
        recording = _recording(session, "o" * 64)
        record_detections(
            session,
            recording,
            IdSource.BATDETECT2,
            [
                DetectionInput(taxon_id=None, raw_label="NOISE", confidence=0.9),
                DetectionInput(
                    taxon_id=None, raw_label="Not A Real Species", confidence=0.85
                ),
            ],
        )
        session.commit()

        materialize_claims(session, IdSource.BATDETECT2, threshold=0.8)
        session.commit()

        claims = session.scalars(
            select(Identification).where(
                Identification.recording_id == recording.id,
                Identification.source == IdSource.BATDETECT2,
            ),
        ).all()
        assert len(claims) == 1
        assert claims[0].verdict == Verdict.NOISE
        assert claims[0].taxon_id is None


def test_materialize_claims_lets_an_unmapped_species_win_the_null_taxon_slot_by_confidence(
    engine: Engine,
) -> None:
    """Same three-way NULL-taxon-slot competition as
    `test_materialize_claims_lets_noise_win_the_null_taxon_slot_by_confidence`,
    but with the unmapped-species candidate at the higher confidence -- the
    surviving claim must be Verdict.SPECIES with taxon_id=None (not
    Verdict.NOISE), proving the dedup is purely confidence-based rather than
    favoring one verdict over the other."""
    with OrmSession(engine) as session:
        recording = _recording(session, "p" * 64)
        record_detections(
            session,
            recording,
            IdSource.BATDETECT2,
            [
                DetectionInput(taxon_id=None, raw_label="NOISE", confidence=0.82),
                DetectionInput(
                    taxon_id=None, raw_label="Not A Real Species", confidence=0.95
                ),
            ],
        )
        session.commit()

        materialize_claims(session, IdSource.BATDETECT2, threshold=0.8)
        session.commit()

        claims = session.scalars(
            select(Identification).where(
                Identification.recording_id == recording.id,
                Identification.source == IdSource.BATDETECT2,
            ),
        ).all()
        assert len(claims) == 1
        assert claims[0].verdict == Verdict.SPECIES
        assert claims[0].taxon_id is None
        assert claims[0].raw_label == "Not A Real Species"


def test_record_detections_writes_an_annotation_for_a_geometry_bearing_detection(
    engine: Engine,
) -> None:
    with OrmSession(engine) as session:
        recording = Recording(
            audio_hash="bd" * 32,
            path="bd.wav",
            recorded_at=datetime(2026, 9, 24, tzinfo=UTC),
        )
        session.add(recording)
        session.commit()

        record_detections(
            session,
            recording,
            IdSource.BATDETECT2,
            [
                DetectionInput(
                    taxon_id=None,
                    raw_label="Pipistrellus pipistrellus",
                    confidence=0.85,
                    time_start_s=0.1,
                    time_end_s=0.13,
                    freq_min_hz=40000.0,
                    freq_max_hz=55000.0,
                ),
            ],
        )
        session.commit()

        annotations = session.scalars(select(Annotation)).all()
        assert len(annotations) == 1
        annotation = annotations[0]
        assert annotation.source == AnnotationSource.BATDETECT2
        assert annotation.recording_id == recording.id
        assert annotation.time_start_s == 0.1
        assert annotation.time_end_s == 0.13
        assert annotation.freq_min_hz == 40000.0
        assert annotation.freq_max_hz == 55000.0
        assert annotation.confidence == 0.85
        assert annotation.literal is None


def test_record_detections_clamps_geometry_past_the_recording_s_duration(
    engine: Engine,
) -> None:
    with OrmSession(engine) as session:
        recording = Recording(
            audio_hash="cl" * 32,
            path="cl.wav",
            recorded_at=datetime(2026, 9, 24, tzinfo=UTC),
            duration_s=21.303,
        )
        session.add(recording)
        session.commit()

        record_detections(
            session,
            recording,
            IdSource.BATDETECT2,
            [
                DetectionInput(
                    taxon_id=None,
                    raw_label="Pipistrellus pipistrellus",
                    confidence=0.06,
                    time_start_s=21.28,
                    time_end_s=21.35,
                    freq_min_hz=40000.0,
                    freq_max_hz=55000.0,
                ),
            ],
        )
        session.commit()

        annotation = session.scalars(select(Annotation)).one()
        assert annotation.time_start_s == 21.28
        assert annotation.time_end_s == 21.303


def test_record_detections_leaves_geometry_unclamped_when_duration_is_unknown(
    engine: Engine,
) -> None:
    with OrmSession(engine) as session:
        recording = Recording(
            audio_hash="un" * 32,
            path="un.wav",
            recorded_at=datetime(2026, 9, 24, tzinfo=UTC),
        )
        session.add(recording)
        session.commit()

        record_detections(
            session,
            recording,
            IdSource.BATDETECT2,
            [
                DetectionInput(
                    taxon_id=None,
                    raw_label="Pipistrellus pipistrellus",
                    confidence=0.06,
                    time_start_s=21.28,
                    time_end_s=21.35,
                    freq_min_hz=40000.0,
                    freq_max_hz=55000.0,
                ),
            ],
        )
        session.commit()

        annotation = session.scalars(select(Annotation)).one()
        assert annotation.time_start_s == 21.28
        assert annotation.time_end_s == 21.35


def test_record_detections_writes_no_annotation_for_a_detection_without_geometry(
    engine: Engine,
) -> None:
    with OrmSession(engine) as session:
        recording = Recording(
            audio_hash="bn" * 32,
            path="bn.wav",
            recorded_at=datetime(2026, 9, 24, tzinfo=UTC),
        )
        session.add(recording)
        session.commit()

        record_detections(
            session,
            recording,
            IdSource.BATTYBIRDNET,
            [
                DetectionInput(
                    taxon_id=None, raw_label="Pipistrellus pipistrellus", confidence=0.7
                )
            ],
        )
        session.commit()

        assert session.scalars(select(Annotation)).all() == []


def test_record_detections_writes_no_annotation_for_the_empty_run_noid_marker(
    engine: Engine,
) -> None:
    with OrmSession(engine) as session:
        recording = Recording(
            audio_hash="nm" * 32,
            path="nm.wav",
            recorded_at=datetime(2026, 9, 24, tzinfo=UTC),
        )
        session.add(recording)
        session.commit()

        record_detections(session, recording, IdSource.BATDETECT2, [])
        session.commit()

        assert session.scalars(select(Annotation)).all() == []


def test_record_detections_replaces_prior_annotation_rows_on_rerun(
    engine: Engine,
) -> None:
    with OrmSession(engine) as session:
        recording = Recording(
            audio_hash="rr" * 32,
            path="rr.wav",
            recorded_at=datetime(2026, 9, 24, tzinfo=UTC),
        )
        session.add(recording)
        session.commit()

        record_detections(
            session,
            recording,
            IdSource.BATDETECT2,
            [
                DetectionInput(
                    taxon_id=None,
                    raw_label="old",
                    confidence=0.5,
                    time_start_s=0.0,
                    time_end_s=0.01,
                    freq_min_hz=10000.0,
                    freq_max_hz=20000.0,
                ),
            ],
        )
        session.commit()

        record_detections(
            session,
            recording,
            IdSource.BATDETECT2,
            [
                DetectionInput(
                    taxon_id=None,
                    raw_label="new",
                    confidence=0.9,
                    time_start_s=1.0,
                    time_end_s=1.01,
                    freq_min_hz=30000.0,
                    freq_max_hz=40000.0,
                ),
            ],
        )
        session.commit()

        annotations = session.scalars(select(Annotation)).all()
        assert len(annotations) == 1
        assert annotations[0].literal is None
        assert annotations[0].time_start_s == 1.0


def test_record_detections_does_not_touch_other_recordings_or_sources_annotations(
    engine: Engine,
) -> None:
    with OrmSession(engine) as session:
        target = Recording(
            audio_hash="rt" * 32,
            path="rt.wav",
            recorded_at=datetime(2026, 9, 24, tzinfo=UTC),
        )
        other = Recording(
            audio_hash="ro" * 32,
            path="ro.wav",
            recorded_at=datetime(2026, 9, 24, tzinfo=UTC),
        )
        session.add_all([target, other])
        session.commit()

        # A comment on the SAME recording as the batdetect2 run.
        create_comment_annotation(
            session,
            target,
            time_start_s=0.5,
            time_end_s=0.5,
            freq_min_hz=None,
            freq_max_hz=None,
            taxon_id=None,
            literal="do not delete me",
        )
        # A batdetect2 annotation on a DIFFERENT recording.
        record_detections(
            session,
            other,
            IdSource.BATDETECT2,
            [
                DetectionInput(
                    taxon_id=None,
                    raw_label="other recording's call",
                    confidence=0.9,
                    time_start_s=0.0,
                    time_end_s=0.01,
                    freq_min_hz=10000.0,
                    freq_max_hz=20000.0,
                ),
            ],
        )
        session.commit()

        # Now run batdetect2 on the target recording -- this must not touch either row above.
        record_detections(
            session,
            target,
            IdSource.BATDETECT2,
            [
                DetectionInput(
                    taxon_id=None,
                    raw_label="target recording's call",
                    confidence=0.8,
                    time_start_s=1.0,
                    time_end_s=1.01,
                    freq_min_hz=15000.0,
                    freq_max_hz=25000.0,
                ),
            ],
        )
        session.commit()

        all_annotations = session.scalars(select(Annotation)).all()
        assert len(all_annotations) == 3
        by_source_and_recording = {
            (a.source, a.recording_id): a for a in all_annotations
        }
        assert (AnnotationSource.COMMENT, target.id) in by_source_and_recording
        assert (
            by_source_and_recording[(AnnotationSource.COMMENT, target.id)].literal
            == "do not delete me"
        )
        assert (AnnotationSource.BATDETECT2, other.id) in by_source_and_recording
        assert (
            by_source_and_recording[(AnnotationSource.BATDETECT2, other.id)].confidence
            == 0.9
        )
        assert (AnnotationSource.BATDETECT2, target.id) in by_source_and_recording
        assert (
            by_source_and_recording[(AnnotationSource.BATDETECT2, target.id)].confidence
            == 0.8
        )
