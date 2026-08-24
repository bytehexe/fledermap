from __future__ import annotations

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as OrmSession

from fledermap.store.models import Taxon, TaxonCode
from fledermap.store.seed import resolve_code, seed_taxonomy

pytestmark = pytest.mark.db


def test_seeding_creates_taxa_and_codes(engine: Engine) -> None:
    with OrmSession(engine) as session:
        created = seed_taxonomy(session)
        session.commit()

        assert created > 0
        code_count = session.scalar(select(func.count()).select_from(TaxonCode))
        assert code_count is not None
        assert code_count > 0


def test_seeding_is_idempotent(engine: Engine) -> None:
    with OrmSession(engine) as session:
        seed_taxonomy(session)
        session.commit()
        first_taxa = session.scalar(select(func.count()).select_from(Taxon))
        first_codes = session.scalar(select(func.count()).select_from(TaxonCode))

        seed_taxonomy(session)
        session.commit()

        assert session.scalar(select(func.count()).select_from(Taxon)) == first_taxa
        assert (
            session.scalar(select(func.count()).select_from(TaxonCode)) == first_codes
        )


def test_second_seeding_reports_nothing_created(engine: Engine) -> None:
    """The return value is the docstring's promise; counts alone do not test it."""
    with OrmSession(engine) as session:
        assert seed_taxonomy(session) > 0
        session.commit()

        assert seed_taxonomy(session) == 0


def test_a_code_reassigned_in_the_yaml_follows_the_taxon(engine: Engine) -> None:
    """`uq_taxon_code` keeps a code unique but cannot keep it pointing at the
    right taxon. A stale mapping would make resolve_code confidently wrong."""
    with OrmSession(engine) as session:
        seed_taxonomy(session)
        session.commit()

        wrong = session.scalars(
            select(Taxon).where(Taxon.scientific_name == "Nyctalus noctula"),
        ).one()
        code = session.scalars(
            select(TaxonCode).where(
                TaxonCode.source == "emt", TaxonCode.code == "EPTSER"
            ),
        ).one()
        code.taxon_id = wrong.id
        session.commit()

        seed_taxonomy(session)
        session.commit()

        repointed = resolve_code(session, "emt", "EPTSER")
        assert repointed is not None
        assert repointed.scientific_name == "Eptesicus serotinus"


def test_resolves_a_known_code(engine: Engine) -> None:
    with OrmSession(engine) as session:
        seed_taxonomy(session)
        session.commit()

        taxon = resolve_code(session, "emt", "EPTSER")

        assert taxon is not None
        assert taxon.scientific_name == "Eptesicus serotinus"
        assert taxon.common_name_de == "Breitflügelfledermaus"


def test_group_and_genus_ranks_are_representable(engine: Engine) -> None:
    """Myotis is a genus, Nyctaloid is a phonic group. Neither is a species.

    Both are looked up by name, not by code: the Wildlife Acoustics list is
    species-level only, so neither carries one.
    """
    with OrmSession(engine) as session:
        seed_taxonomy(session)
        session.commit()

        genus = session.scalars(
            select(Taxon).where(Taxon.scientific_name == "Myotis"),
        ).one()
        assert genus.rank == "genus"

        group = session.scalars(
            select(Taxon).where(Taxon.scientific_name == "Nyctaloid"),
        ).one()
        assert group.rank == "group"


def test_unknown_code_resolves_to_none(engine: Engine) -> None:
    """Unmapped labels must not raise — they become a review queue (spec section 5)."""
    with OrmSession(engine) as session:
        seed_taxonomy(session)
        session.commit()

        assert resolve_code(session, "emt", "ZZZZZZ") is None


def test_one_taxon_may_carry_several_codes_from_one_source(engine: Engine) -> None:
    """NABat publishes BOTH a four- and a six-letter code for every species, so a
    taxon must be able to carry more than one code under a single source.

    `uq_taxon_code` is (source, code), not (source, taxon_id), which permits this
    while still forbidding the thing that must stay forbidden: one code meaning
    two different taxa within a source. Tightening it to one-code-per-source
    would make NABat unrepresentable.

    The codes below are illustrative of the two-form shape, not reference data —
    Eptesicus serotinus is European and does not appear in NABat's list.
    """
    with OrmSession(engine) as session:
        seed_taxonomy(session)
        session.commit()

        taxon = session.scalars(
            select(Taxon).where(Taxon.scientific_name == "Eptesicus serotinus"),
        ).one()
        session.add_all(
            [
                TaxonCode(source="illustrative", code="EPSE", taxon_id=taxon.id),
                TaxonCode(source="illustrative", code="EPTSER", taxon_id=taxon.id),
            ],
        )
        session.commit()

        short = resolve_code(session, "illustrative", "EPSE")
        long_ = resolve_code(session, "illustrative", "EPTSER")

        assert short is not None
        assert long_ is not None
        assert short.id == long_.id == taxon.id


def test_one_code_may_not_mean_two_taxa_within_a_source(engine: Engine) -> None:
    """The other half of uq_taxon_code, and the half that must hold."""
    with OrmSession(engine) as session:
        seed_taxonomy(session)
        session.commit()

        a, b = session.scalars(select(Taxon).limit(2)).all()
        session.add_all(
            [
                TaxonCode(source="illustrative", code="DUPDUP", taxon_id=a.id),
                TaxonCode(source="illustrative", code="DUPDUP", taxon_id=b.id),
            ],
        )
        with pytest.raises(IntegrityError):
            session.commit()
