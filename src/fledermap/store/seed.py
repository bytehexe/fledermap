"""Seed the taxonomy from bundled YAML. Idempotent: safe to run on every startup."""

from __future__ import annotations

from importlib.resources import files
from typing import Any

import yaml
from sqlalchemy import delete, select
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.ranges import RANGE_CODE_SOURCE
from fledermap.store.models import Taxon, TaxonCode

_DATA = ["taxa_eu.yaml", "taxa_na.yaml", "taxa_groups.yaml"]


def _load() -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for name in _DATA:
        raw = files("fledermap.store.data").joinpath(name).read_text(encoding="utf-8")
        entries += yaml.safe_load(raw)["taxa"]
    return entries


def seed_taxonomy(session: OrmSession) -> int:
    """Insert any missing taxa and codes. Returns the number of taxa created."""
    created = 0
    range_codes: list[TaxonCode] = []
    for entry in _load():
        taxon = session.scalars(
            select(Taxon).where(Taxon.scientific_name == entry["scientific_name"]),
        ).one_or_none()

        renamed = False
        if taxon is None:
            # No row has this scientific_name yet -- before treating it as a brand
            # new taxon, check whether one of this entry's own codes already
            # points somewhere: a taxonomic rename in the YAML (e.g. the 2023
            # Eptesicus/Cnephaeus split) changes scientific_name but not the
            # codes, and codes are the actual stable identity a source's data
            # keys on. Without this, a rename forked a second Taxon row and
            # stranded every existing Identification on the old, now-codeless
            # one -- found 2026-09-25 via 183 Identification rows split across
            # dead "Eptesicus serotinus"/"Eptesicus nilssonii" rows and their
            # live "Cnephaeus" replacements.
            for source, code in (entry.get("codes") or {}).items():
                existing_code = session.scalars(
                    select(TaxonCode).where(
                        TaxonCode.source == source,
                        TaxonCode.code == code,
                    ),
                ).one_or_none()
                if existing_code is not None:
                    taxon = session.get(Taxon, existing_code.taxon_id)
                    renamed = True
                    break

        if taxon is None:
            taxon = Taxon(
                rank=entry["rank"],
                scientific_name=entry["scientific_name"],
                common_name_de=entry.get("common_name_de"),
                common_name_en=entry.get("common_name_en"),
            )
            session.add(taxon)
            session.flush()
            created += 1
        elif renamed:
            taxon.scientific_name = entry["scientific_name"]
            taxon.common_name_de = entry.get("common_name_de")
            taxon.common_name_en = entry.get("common_name_en")

        for source, code in (entry.get("codes") or {}).items():
            exists = session.scalars(
                select(TaxonCode).where(
                    TaxonCode.source == source,
                    TaxonCode.code == code,
                ),
            ).one_or_none()
            if exists is None:
                exists = TaxonCode(source=source, code=code, taxon_id=taxon.id)
                session.add(exists)
            elif exists.taxon_id != taxon.id:
                # The bundled YAML is the source of truth for reference data, so
                # a code moved between taxa (a corrected mapping) must follow.
                # `uq_taxon_code` guarantees the code is unique, NOT that it
                # still points where the current YAML says — without this the
                # stale row survives silently and resolve_code keeps returning
                # the old taxon.
                exists.taxon_id = taxon.id
            if source == RANGE_CODE_SOURCE:
                range_codes.append(exists)

    # Unlike every other source, a taxon's IUCN range name is looked up as "the" code for the
    # taxon, so a name changed in the YAML must REPLACE the old one: left behind, the taxon would
    # hold two and the range fetch job would ask about both on every cycle, each overwriting the
    # other's polygons.
    session.flush()
    session.execute(
        delete(TaxonCode).where(
            TaxonCode.source == RANGE_CODE_SOURCE,
            TaxonCode.id.not_in([code.id for code in range_codes]),
        ),
    )

    return created


def resolve_code(session: OrmSession, source: str, code: str) -> Taxon | None:
    """Map a source-specific label to a taxon, or None when unmapped."""
    return session.scalars(
        select(Taxon)
        .join(TaxonCode, TaxonCode.taxon_id == Taxon.id)
        .where(TaxonCode.source == source, TaxonCode.code == code),
    ).one_or_none()
