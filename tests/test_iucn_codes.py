from __future__ import annotations

from importlib.resources import files
from typing import Any

import pytest
import yaml
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.ranges import RANGE_CODE_SOURCE
from fledermap.store import seed as seed_module
from fledermap.store.models import Taxon, TaxonCode
from fledermap.store.seed import resolve_code, seed_taxonomy

# Where IUCN 2022-2's name differs from our `scientific_name` (current taxonomy). Explicit, so a
# typo in a code cannot hide behind a lenient "close enough" comparison.
RENAMED_IN_IUCN = {
    "Cnephaeus serotinus": "Eptesicus serotinus",
    "Cnephaeus isabellinus": "Eptesicus isabellinus",
    "Cnephaeus nilssonii": "Eptesicus nilssonii",
}


def _entries(name: str) -> list[dict[str, Any]]:
    raw = files("fledermap.store.data").joinpath(name).read_text(encoding="utf-8")
    entries: list[dict[str, Any]] = yaml.safe_load(raw)["taxa"]
    return entries


def _species() -> list[dict[str, Any]]:
    return [
        e
        for name in ("taxa_eu.yaml", "taxa_na.yaml")
        for e in _entries(name)
        if e["rank"] == "species"
    ]


def test_every_species_has_its_iucn2022_code() -> None:
    for entry in _species():
        name = entry["scientific_name"]
        expected = RENAMED_IN_IUCN.get(name, name)
        assert entry["codes"].get(RANGE_CODE_SOURCE) == expected, name


def test_iucn2022_codes_are_unique_across_species() -> None:
    codes = [e["codes"][RANGE_CODE_SOURCE] for e in _species()]
    assert len(codes) == len(set(codes))


def test_group_rank_taxa_carry_no_iucn2022_code() -> None:
    for entry in _entries("taxa_groups.yaml"):
        assert RANGE_CODE_SOURCE not in (entry.get("codes") or {}), entry[
            "scientific_name"
        ]


@pytest.mark.db
def test_seeding_stores_the_iucn2022_codes(engine: Engine) -> None:
    with OrmSession(engine) as session:
        seed_taxonomy(session)
        session.commit()

        taxon = resolve_code(session, RANGE_CODE_SOURCE, "Eptesicus serotinus")

    assert taxon is not None
    assert taxon.scientific_name == "Cnephaeus serotinus"


@pytest.mark.db
def test_reseeding_with_a_changed_iucn2022_code_replaces_the_old_one(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The seed only ever added codes, so a range name edited in the YAML (IUCN adopting a
    new genus) left two `iucn2022` rows on the taxon and the fetch job asked about both of them
    on every cycle, each overwriting the other."""
    with OrmSession(engine) as session:
        seed_taxonomy(session)
        session.commit()

        entries = seed_module._load()
        for entry in entries:
            if entry["scientific_name"] == "Cnephaeus serotinus":
                entry["codes"] = {
                    **entry["codes"],
                    RANGE_CODE_SOURCE: "Cnephaeus serotinus",
                }
        monkeypatch.setattr(seed_module, "_load", lambda: entries)
        seed_taxonomy(session)
        session.commit()

        codes = session.scalars(
            select(TaxonCode.code)
            .join(Taxon, Taxon.id == TaxonCode.taxon_id)
            .where(
                Taxon.scientific_name == "Cnephaeus serotinus",
                TaxonCode.source == RANGE_CODE_SOURCE,
            ),
        ).all()

    assert codes == ["Cnephaeus serotinus"]
