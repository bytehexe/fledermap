from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import IdSource, Verdict
from fledermap.services.manual_classification import set_manual_classification
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


def _manual_claims(session: OrmSession, recording_id: int) -> list[Identification]:
    return list(
        session.scalars(
            select(Identification).where(
                Identification.recording_id == recording_id,
                Identification.source == IdSource.MANUAL,
                Identification.superseded_at.is_(None),
            ),
        ).all(),
    )


def test_species_verdict_requires_at_least_one_taxon_id(engine: Engine) -> None:
    with OrmSession(engine) as session:
        recording = _recording(session, "a" * 64)

        with pytest.raises(ValueError, match="taxon_id"):
            set_manual_classification(
                session, recording, verdict=Verdict.SPECIES, taxon_ids=[]
            )


def test_non_species_verdict_rejects_taxon_ids(engine: Engine) -> None:
    with OrmSession(engine) as session:
        recording = _recording(session, "b" * 64)

        with pytest.raises(ValueError, match="taxon_ids"):
            set_manual_classification(
                session,
                recording,
                verdict=Verdict.NO_ID,
                taxon_ids=[1],
            )


def test_setting_a_species_claim_creates_a_manual_identification(
    engine: Engine,
) -> None:
    with OrmSession(engine) as session:
        taxon = Taxon(rank="species", scientific_name="Pipistrellus pipistrellus")
        session.add(taxon)
        session.flush()
        recording = _recording(session, "c" * 64)

        set_manual_classification(
            session,
            recording,
            verdict=Verdict.SPECIES,
            taxon_ids=[taxon.id],
        )

        claims = _manual_claims(session, recording.id)
        assert len(claims) == 1
        assert claims[0].taxon_id == taxon.id
        assert claims[0].verdict == Verdict.SPECIES


def test_setting_multiple_species_claims_inserts_one_row_each(engine: Engine) -> None:
    with OrmSession(engine) as session:
        taxon_a = Taxon(rank="species", scientific_name="Pipistrellus pipistrellus")
        taxon_b = Taxon(rank="genus", scientific_name="Myotis")
        session.add_all([taxon_a, taxon_b])
        session.flush()
        recording = _recording(session, "d" * 64)

        set_manual_classification(
            session,
            recording,
            verdict=Verdict.SPECIES,
            taxon_ids=[taxon_a.id, taxon_b.id],
        )

        claims = _manual_claims(session, recording.id)
        assert {c.taxon_id for c in claims} == {taxon_a.id, taxon_b.id}


def test_setting_no_id_supersedes_a_prior_species_claim(engine: Engine) -> None:
    with OrmSession(engine) as session:
        taxon = Taxon(rank="species", scientific_name="Pipistrellus pipistrellus")
        session.add(taxon)
        session.flush()
        recording = _recording(session, "e" * 64)
        set_manual_classification(
            session,
            recording,
            verdict=Verdict.SPECIES,
            taxon_ids=[taxon.id],
        )

        set_manual_classification(session, recording, verdict=Verdict.NO_ID)

        claims = _manual_claims(session, recording.id)
        assert len(claims) == 1
        assert claims[0].verdict == Verdict.NO_ID


def test_setting_a_species_claim_supersedes_a_prior_no_id(engine: Engine) -> None:
    with OrmSession(engine) as session:
        taxon = Taxon(rank="species", scientific_name="Pipistrellus pipistrellus")
        session.add(taxon)
        session.flush()
        recording = _recording(session, "f" * 64)
        set_manual_classification(session, recording, verdict=Verdict.NO_ID)

        set_manual_classification(
            session,
            recording,
            verdict=Verdict.SPECIES,
            taxon_ids=[taxon.id],
        )

        claims = _manual_claims(session, recording.id)
        assert len(claims) == 1
        assert claims[0].verdict == Verdict.SPECIES


def test_clearing_supersedes_every_standing_manual_claim(engine: Engine) -> None:
    with OrmSession(engine) as session:
        taxon = Taxon(rank="species", scientific_name="Pipistrellus pipistrellus")
        session.add(taxon)
        session.flush()
        recording = _recording(session, "g" * 64)
        set_manual_classification(
            session,
            recording,
            verdict=Verdict.SPECIES,
            taxon_ids=[taxon.id],
        )

        set_manual_classification(session, recording, verdict=None)

        assert _manual_claims(session, recording.id) == []


def test_clearing_when_nothing_is_set_is_a_safe_no_op(engine: Engine) -> None:
    with OrmSession(engine) as session:
        recording = _recording(session, "h" * 64)

        set_manual_classification(session, recording, verdict=None)

        assert _manual_claims(session, recording.id) == []


def test_two_manual_species_rows_insert_cleanly_under_the_unique_constraint(
    engine: Engine,
) -> None:
    """Regression: uq_identification_source_claim is (recording_id, source,
    source_version, raw_label, taxon_id) as of Task 2's migration
    f3a1c9d2e4b7 -- taxon_id IS part of it, which is exactly why two manual
    SPECIES rows differing only in taxon_id don't collide even though
    source_version/raw_label are both NULL for every manual row and the
    constraint uses postgresql_nulls_not_distinct=True."""
    with OrmSession(engine) as session:
        taxon_a = Taxon(rank="species", scientific_name="Pipistrellus pipistrellus")
        taxon_b = Taxon(rank="genus", scientific_name="Myotis")
        session.add_all([taxon_a, taxon_b])
        session.flush()
        recording = _recording(session, "i" * 64)

        set_manual_classification(
            session,
            recording,
            verdict=Verdict.SPECIES,
            taxon_ids=[taxon_a.id, taxon_b.id],
        )
        session.commit()  # would raise IntegrityError if the constraint collided

        assert len(_manual_claims(session, recording.id)) == 2
