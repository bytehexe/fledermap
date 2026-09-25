from __future__ import annotations

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session as OrmSession

from fledermap.services.search import _resolve_species_value
from fledermap.store.models import Taxon, TaxonCode

pytestmark = pytest.mark.db


def _taxon(session: OrmSession, *, scientific_name: str) -> Taxon:
    taxon = Taxon(rank="species", scientific_name=scientific_name)
    session.add(taxon)
    session.flush()
    return taxon


def test_resolves_by_code(engine: Engine) -> None:
    with OrmSession(engine) as session:
        taxon = _taxon(session, scientific_name="Pipistrellus pipistrellus")
        session.add(TaxonCode(source="wa", code="PIPPIP", taxon_id=taxon.id))
        session.commit()

        assert _resolve_species_value(session, "PIPPIP") == taxon.id


def test_resolves_by_full_scientific_name_case_insensitive(engine: Engine) -> None:
    with OrmSession(engine) as session:
        taxon = _taxon(session, scientific_name="Pipistrellus pipistrellus")
        session.commit()

        assert _resolve_species_value(session, "pipistrellus PIPISTRELLUS") == taxon.id


def test_resolves_by_abbreviated_genus_form(engine: Engine) -> None:
    with OrmSession(engine) as session:
        taxon = _taxon(session, scientific_name="Pipistrellus pipistrellus")
        session.commit()

        assert _resolve_species_value(session, "P. pipistrellus") == taxon.id
        assert _resolve_species_value(session, "p. PIPISTRELLUS") == taxon.id


def test_ambiguous_abbreviated_form_resolves_to_none(engine: Engine) -> None:
    with OrmSession(engine) as session:
        _taxon(session, scientific_name="Pipistrellus pygmaeus")
        _taxon(session, scientific_name="Plecotus pygmaeus")
        session.commit()

        assert _resolve_species_value(session, "P. pygmaeus") is None


def test_unrecognized_value_resolves_to_none(engine: Engine) -> None:
    with OrmSession(engine) as session:
        session.commit()
        assert _resolve_species_value(session, "not a real species") is None


def test_code_shared_by_two_taxa_across_sources_resolves_to_none(
    engine: Engine,
) -> None:
    """Review Focus item: a code spelled identically under two different code sources, pointing
    at two different taxa, must not silently pick one."""
    with OrmSession(engine) as session:
        a = _taxon(session, scientific_name="Pipistrellus pipistrellus")
        b = _taxon(session, scientific_name="Plecotus auritus")
        session.add(TaxonCode(source="wa", code="AMBIG", taxon_id=a.id))
        session.add(TaxonCode(source="nabat", code="AMBIG", taxon_id=b.id))
        session.commit()

        assert _resolve_species_value(session, "AMBIG") is None
