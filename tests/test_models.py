from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import IdSource, Verdict
from fledermap.store.models import Identification, Recording

pytestmark = pytest.mark.db


def _recording(
    digest: str = "a" * 64, path: str = "s/EPTSER_20150610_215446.wav"
) -> Recording:
    return Recording(
        audio_hash=digest,
        path=path,
        recorded_at=datetime(2015, 6, 10, 21, 54, 46, tzinfo=UTC),
        samplerate_hz=256000,
    )


def test_recording_round_trips(engine: Engine) -> None:
    with OrmSession(engine) as session:
        session.add(_recording())
        session.commit()

    with OrmSession(engine) as session:
        found = session.scalars(select(Recording)).one()
        assert found.samplerate_hz == 256000
        assert found.missing_since is None


def test_recording_favourite_defaults_to_false(engine: Engine) -> None:
    with OrmSession(engine) as session:
        session.add(_recording())
        session.commit()

    with OrmSession(engine) as session:
        assert session.scalars(select(Recording)).one().favourite is False


def test_audio_hash_is_unique(engine: Engine) -> None:
    """Identity is the audio; the same payload twice is the same recording."""
    with OrmSession(engine) as session:
        session.add(_recording(path="a.wav"))
        session.add(_recording(path="b.wav"))
        with pytest.raises(IntegrityError):
            session.commit()


def test_geometry_may_be_null(engine: Engine) -> None:
    """Recordings without GPS are first-class, not errors."""
    with OrmSession(engine) as session:
        rec = _recording()
        rec.geom = None
        session.add(rec)
        session.commit()
        assert session.scalars(select(Recording)).one().geom is None


def test_identifications_cascade_from_recording(engine: Engine) -> None:
    """`identification.recording_id` is declared `ondelete="CASCADE"`
    (models.py): deleting a Recording row must delete its Identification
    rows with it, at the database level. Deletes via raw SQL rather than the
    ORM session — the ORM's OWN `cascade="all, delete-orphan"` would make
    this pass even if the FK's `ondelete="CASCADE"` were missing, which is
    not what this test's name promises to prove (whole-branch review,
    Minor G)."""
    with OrmSession(engine) as session:
        rec = _recording()
        rec.identifications = [
            Identification(
                source=IdSource.EMT_WAMD,
                source_version="App 3.1.10",
                verdict=Verdict.SPECIES,
                raw_label="EPTSER",
            ),
            Identification(
                source=IdSource.EMT_FILENAME,
                verdict=Verdict.SPECIES,
                raw_label="EPTSER",
            ),
        ]
        session.add(rec)
        session.commit()
        recording_id = rec.id

    assert recording_id is not None
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM recording WHERE id = :id"), {"id": recording_id})

    with OrmSession(engine) as session:
        assert session.scalars(select(Recording)).all() == []
        assert session.scalars(select(Identification)).all() == []


def test_both_timestamp_columns_persist(engine: Engine) -> None:
    """Spec D17: neither candidate may be dropped as 'unused'."""
    with OrmSession(engine) as session:
        rec = _recording()
        rec.filename_at = datetime(2015, 6, 10, 21, 54, 46, tzinfo=UTC)
        rec.metadata_at = datetime(2015, 6, 10, 9, 54, 54, tzinfo=UTC)
        rec.timestamp_disagreement_s = 43192.0
        session.add(rec)
        session.commit()

    with OrmSession(engine) as session:
        found = session.scalars(select(Recording)).one()
        assert found.filename_at != found.metadata_at
        assert found.timestamp_disagreement_s == pytest.approx(43192.0)


def test_enum_columns_round_trip_to_python_type(engine: Engine) -> None:
    """A plain String column would come back as `str`, not the enum (review item 1)."""
    with OrmSession(engine) as session:
        rec = _recording()
        rec.identifications = [
            Identification(
                source=IdSource.EMT_WAMD, verdict=Verdict.SPECIES, raw_label="EPTSER"
            ),
        ]
        session.add(rec)
        session.commit()

    with OrmSession(engine) as session:
        loaded = session.scalars(select(Identification)).one()
        assert isinstance(loaded.source, IdSource)
        assert isinstance(loaded.verdict, Verdict)
        assert loaded.source is IdSource.EMT_WAMD
        assert loaded.verdict is Verdict.SPECIES


def test_duplicate_claim_with_null_source_version_rejected(engine: Engine) -> None:
    """Postgres treats NULLs as distinct by default; `source_version` is nullable
    for exactly the sources (filename IDs, manual annotations) that most need the
    constraint to hold (review item 2)."""
    with OrmSession(engine) as session:
        rec = _recording()
        session.add(rec)
        session.commit()
        recording_id = rec.id

    with OrmSession(engine) as session:
        session.add(
            Identification(
                recording_id=recording_id,
                source=IdSource.MANUAL,
                verdict=Verdict.SPECIES,
                raw_label="EPTSER",
            ),
        )
        session.add(
            Identification(
                recording_id=recording_id,
                source=IdSource.MANUAL,
                verdict=Verdict.SPECIES,
                raw_label="EPTSER",
            ),
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_duplicate_manual_no_id_claims_are_rejected(engine: Engine) -> None:
    """`uq_identification_source_claim` was widened to include `taxon_id`
    (fledermap-manual-classification, 2026-09-05) so that two MANUAL SPECIES
    rows differing only in taxon_id can coexist (a genuine multi-species
    file) -- see test_map_view.py's
    test_taxon_filter_finds_either_of_two_manual_species_claims and
    test_geojson_api.py's test_recordings_geojson_marks_multi_species_recordings
    for that positive case. MC-3 (design spec) still requires NO_ID/NOISE to
    stay a singleton within MANUAL: two MANUAL NO_ID rows both have
    taxon_id=None, so they still collide on the constraint exactly as before
    the widening."""
    with OrmSession(engine) as session:
        rec = _recording()
        session.add(rec)
        session.commit()
        recording_id = rec.id

    with OrmSession(engine) as session:
        session.add(
            Identification(
                recording_id=recording_id,
                source=IdSource.MANUAL,
                verdict=Verdict.NO_ID,
                taxon_id=None,
            ),
        )
        session.add(
            Identification(
                recording_id=recording_id,
                source=IdSource.MANUAL,
                verdict=Verdict.NO_ID,
                taxon_id=None,
            ),
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_two_live_claims_with_the_same_key_tuple_still_collide(engine: Engine) -> None:
    """The scoping must not become a no-op: two NON-superseded rows sharing the exact
    same key tuple must still be rejected -- this is what MC-3's NO_ID/NOISE singleton
    rule (and the general anti-duplicate-claim purpose of the constraint) depends on."""
    with OrmSession(engine) as session:
        recording = Recording(
            audio_hash="n" * 64,
            path="n.wav",
            recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
        )
        session.add(recording)
        session.flush()

        session.add(
            Identification(
                recording_id=recording.id,
                source=IdSource.MANUAL,
                verdict=Verdict.NO_ID,
            ),
        )
        session.commit()

        session.add(
            Identification(
                recording_id=recording.id,
                source=IdSource.MANUAL,
                verdict=Verdict.NO_ID,
            ),
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_rows_differing_only_in_raw_label_or_source_version_now_collide(
    engine: Engine,
) -> None:
    """The narrowed (recording_id, source, taxon_id) constraint (this branch's
    drop of superseded_at) must reject a pair that was legally distinct under
    the OLD 5-column key (recording_id, source, source_version, raw_label,
    taxon_id) -- this is exactly the shape that made the migration's
    dedup-before-narrowing step (ef85421bf57d) necessary in the first place."""
    with OrmSession(engine) as session:
        recording = Recording(
            audio_hash="q" * 64,
            path="q.wav",
            recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
        )
        session.add(recording)
        session.flush()

        session.add(
            Identification(
                recording_id=recording.id,
                source=IdSource.MANUAL,
                verdict=Verdict.NO_ID,
                raw_label="No ID",
                source_version="1.0",
                taxon_id=None,
            ),
        )
        session.commit()

        session.add(
            Identification(
                recording_id=recording.id,
                source=IdSource.MANUAL,
                verdict=Verdict.NO_ID,
                raw_label="Noise",
                source_version="2.0",
                taxon_id=None,
            ),
        )
        with pytest.raises(IntegrityError):
            session.commit()
