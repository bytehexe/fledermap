"""Seed the taxonomy from bundled YAML. Idempotent: safe to run on every startup."""

from __future__ import annotations

from importlib.resources import files
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from fledermap.store.models import Taxon, TaxonCode

_DATA = "taxa_eu.yaml"


def _load() -> list[dict[str, Any]]:
    raw = files("fledermap.store.data").joinpath(_DATA).read_text(encoding="utf-8")
    return yaml.safe_load(raw)["taxa"]


def seed_taxonomy(session: OrmSession) -> int:
    """Insert any missing taxa and codes. Returns the number of taxa created."""
    created = 0
    for entry in _load():
        taxon = session.scalars(
            select(Taxon).where(Taxon.scientific_name == entry["scientific_name"]),
        ).one_or_none()
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

        for source, code in (entry.get("codes") or {}).items():
            exists = session.scalars(
                select(TaxonCode).where(
                    TaxonCode.source == source,
                    TaxonCode.code == code,
                ),
            ).one_or_none()
            if exists is None:
                session.add(TaxonCode(source=source, code=code, taxon_id=taxon.id))

    return created


def resolve_code(session: OrmSession, source: str, code: str) -> Taxon | None:
    """Map a source-specific label to a taxon, or None when unmapped."""
    return session.scalars(
        select(Taxon)
        .join(TaxonCode, TaxonCode.taxon_id == Taxon.id)
        .where(TaxonCode.source == source, TaxonCode.code == code),
    ).one_or_none()
