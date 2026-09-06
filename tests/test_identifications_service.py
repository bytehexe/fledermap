from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import IdSource, Verdict
from fledermap.services.identifications import ClaimInput, ReplaceResult, replace_claims
from fledermap.store.models import Identification, Recording, Taxon

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


def _ensure_taxa(session: OrmSession, count: int) -> None:
    """Ensure at least `count` taxa exist in the database."""
    existing = session.query(Taxon).count()
    for i in range(existing + 1, count + 1):
        taxon = Taxon(rank="species", scientific_name=f"Species{i}")
        session.add(taxon)
    session.commit()


def _claims(
    session: OrmSession, recording_id: int, source: IdSource
) -> list[Identification]:
    return list(
        session.scalars(
            select(Identification).where(
                Identification.recording_id == recording_id,
                Identification.source == source,
            ),
        ).all(),
    )


def test_inserting_into_an_empty_set_adds_a_row(engine: Engine) -> None:
    with OrmSession(engine) as session:
        _ensure_taxa(session, 1)
        recording = _recording(session, "a" * 64)
        now = datetime(2026, 9, 6, tzinfo=UTC)

        result = replace_claims(
            session,
            recording,
            IdSource.EMT_GUANO,
            [ClaimInput(taxon_id=1, verdict=Verdict.SPECIES, raw_label="EPTSER")],
            now,
        )
        session.commit()

        assert result.added == 1
        assert result.updated == 0
        assert result.removed == 0
        claims = _claims(session, recording.id, IdSource.EMT_GUANO)
        assert len(claims) == 1
        assert claims[0].taxon_id == 1
        assert claims[0].raw_label == "EPTSER"
        assert claims[0].first_seen_at == now


def test_same_taxon_id_with_changed_fields_updates_in_place(engine: Engine) -> None:
    with OrmSession(engine) as session:
        _ensure_taxa(session, 1)
        recording = _recording(session, "b" * 64)
        first_seen = datetime(2026, 9, 1, tzinfo=UTC)
        replace_claims(
            session,
            recording,
            IdSource.EMT_GUANO,
            [
                ClaimInput(
                    taxon_id=1,
                    verdict=Verdict.SPECIES,
                    raw_label="EPTSER",
                    source_version="1.0",
                )
            ],
            first_seen,
        )
        session.commit()
        original_id = _claims(session, recording.id, IdSource.EMT_GUANO)[0].id

        result = replace_claims(
            session,
            recording,
            IdSource.EMT_GUANO,
            [
                ClaimInput(
                    taxon_id=1,
                    verdict=Verdict.SPECIES,
                    raw_label="EPTSER",
                    source_version="2.0",
                )
            ],
            datetime(2026, 9, 6, tzinfo=UTC),
        )
        session.commit()

        assert result.added == 0
        assert result.updated == 1
        assert result.removed == 0
        claims = _claims(session, recording.id, IdSource.EMT_GUANO)
        assert len(claims) == 1
        assert claims[0].id == original_id  # same row, not delete+reinsert
        assert claims[0].source_version == "2.0"
        assert claims[0].first_seen_at == first_seen  # untouched by the update


def test_identical_resubmission_is_a_true_no_op(engine: Engine) -> None:
    """No field differs -- must not even count as `updated`, matching this
    project's ingest idempotency principle (services/ingest.py's module
    docstring)."""
    with OrmSession(engine) as session:
        _ensure_taxa(session, 1)
        recording = _recording(session, "c" * 64)
        claim = ClaimInput(taxon_id=1, verdict=Verdict.SPECIES, raw_label="EPTSER")
        replace_claims(
            session,
            recording,
            IdSource.EMT_GUANO,
            [claim],
            datetime(2026, 9, 1, tzinfo=UTC),
        )
        session.commit()

        result = replace_claims(
            session,
            recording,
            IdSource.EMT_GUANO,
            [claim],
            datetime(2026, 9, 6, tzinfo=UTC),
        )
        session.commit()

        assert result == ReplaceResult(added=0, updated=0, removed=0)


def test_a_taxon_id_missing_from_desired_is_deleted_outright(engine: Engine) -> None:
    with OrmSession(engine) as session:
        _ensure_taxa(session, 1)
        recording = _recording(session, "d" * 64)
        replace_claims(
            session,
            recording,
            IdSource.EMT_GUANO,
            [ClaimInput(taxon_id=1, verdict=Verdict.SPECIES, raw_label="EPTSER")],
            datetime(2026, 9, 1, tzinfo=UTC),
        )
        session.commit()

        result = replace_claims(
            session, recording, IdSource.EMT_GUANO, [], datetime(2026, 9, 6, tzinfo=UTC)
        )
        session.commit()

        assert result.added == 0
        assert result.updated == 0
        assert result.removed == 1
        assert _claims(session, recording.id, IdSource.EMT_GUANO) == []
        # The row is gone from the table entirely -- not soft-marked.
        assert session.scalars(select(Identification)).all() == []


def test_a_different_taxon_id_deletes_the_old_and_adds_the_new(engine: Engine) -> None:
    with OrmSession(engine) as session:
        _ensure_taxa(session, 2)
        recording = _recording(session, "e" * 64)
        replace_claims(
            session,
            recording,
            IdSource.EMT_GUANO,
            [ClaimInput(taxon_id=1, verdict=Verdict.SPECIES, raw_label="MYODAU")],
            datetime(2026, 9, 1, tzinfo=UTC),
        )
        session.commit()

        result = replace_claims(
            session,
            recording,
            IdSource.EMT_GUANO,
            [ClaimInput(taxon_id=2, verdict=Verdict.SPECIES, raw_label="EPTSER")],
            datetime(2026, 9, 6, tzinfo=UTC),
        )
        session.commit()

        assert result.added == 1
        assert result.updated == 0
        assert result.removed == 1
        claims = _claims(session, recording.id, IdSource.EMT_GUANO)
        assert len(claims) == 1
        assert claims[0].taxon_id == 2
        assert claims[0].raw_label == "EPTSER"


def test_multiple_taxon_ids_in_desired_all_get_inserted(engine: Engine) -> None:
    """The MANUAL multi-species case: a source can claim more than one
    taxon_id at once."""
    with OrmSession(engine) as session:
        _ensure_taxa(session, 2)
        recording = _recording(session, "f" * 64)

        result = replace_claims(
            session,
            recording,
            IdSource.MANUAL,
            [
                ClaimInput(taxon_id=1, verdict=Verdict.SPECIES),
                ClaimInput(taxon_id=2, verdict=Verdict.SPECIES),
            ],
            datetime(2026, 9, 6, tzinfo=UTC),
        )
        session.commit()

        assert result.added == 2
        claims = _claims(session, recording.id, IdSource.MANUAL)
        assert {c.taxon_id for c in claims} == {1, 2}


def test_only_the_named_source_is_touched(engine: Engine) -> None:
    """A claim from a different source on the same recording must survive
    untouched."""
    with OrmSession(engine) as session:
        _ensure_taxa(session, 1)
        recording = _recording(session, "g" * 64)
        replace_claims(
            session,
            recording,
            IdSource.EMT_FILENAME,
            [ClaimInput(taxon_id=1, verdict=Verdict.SPECIES, raw_label="EPTSER")],
            datetime(2026, 9, 1, tzinfo=UTC),
        )
        session.commit()

        replace_claims(
            session, recording, IdSource.EMT_GUANO, [], datetime(2026, 9, 6, tzinfo=UTC)
        )
        session.commit()

        assert len(_claims(session, recording.id, IdSource.EMT_FILENAME)) == 1
