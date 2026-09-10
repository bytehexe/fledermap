from __future__ import annotations

import pytest
from geoalchemy2.elements import WKTElement
from sqlalchemy import Engine
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import Verdict
from fledermap.services.review_flags import _rarity_reason, _taxon_counts
from fledermap.store.models import Identification, Recording, Site, Taxon


def test_rarity_reason_none_when_common() -> None:
    dataset_counts = {1: 50}
    site_counts = {(10, 1): 20}
    assert (
        _rarity_reason(dataset_counts, site_counts, 10, 1, "Pipistrellus pipistrellus")
        is None
    )


def test_rarity_reason_fires_at_site_threshold() -> None:
    dataset_counts = {1: 50}
    site_counts = {(10, 1): 2}
    reason = _rarity_reason(
        dataset_counts, site_counts, 10, 1, "Pipistrellus pipistrellus"
    )
    assert reason is not None
    assert "2" in reason and "site" in reason and "Pipistrellus pipistrellus" in reason


def test_rarity_reason_fires_at_dataset_threshold() -> None:
    dataset_counts = {1: 5}
    site_counts = {(10, 1): 30}
    reason = _rarity_reason(
        dataset_counts, site_counts, 10, 1, "Pipistrellus pipistrellus"
    )
    assert reason is not None
    assert (
        "5" in reason and "dataset" in reason and "Pipistrellus pipistrellus" in reason
    )


def test_rarity_reason_handles_no_site() -> None:
    # recording.site_id is None -- must not raise, and must still fall
    # back to the dataset-wide check.
    dataset_counts = {1: 3}
    site_counts: dict[tuple[int, int], int] = {}
    reason = _rarity_reason(
        dataset_counts, site_counts, None, 1, "Pipistrellus pipistrellus"
    )
    assert reason is not None


# DB-marked tests follow


def _site(session: OrmSession, *, lon: float = 10.0, lat: float = 50.0) -> Site:
    from datetime import UTC, datetime

    site = Site(
        centroid=WKTElement(f"POINT({lon} {lat})", srid=4326),
        radius_m=50.0,
        recording_count=0,
        first_at=datetime(2026, 8, 1, tzinfo=UTC),
        last_at=datetime(2026, 8, 25, tzinfo=UTC),
    )
    session.add(site)
    session.flush()
    return site


@pytest.mark.db
def test_taxon_counts_tallies_by_site_and_dataset(engine: Engine) -> None:
    from datetime import UTC, datetime

    with OrmSession(engine) as session:
        taxon = Taxon(rank="species", scientific_name="Pipistrellus pipistrellus")
        session.add(taxon)
        session.flush()
        site = _site(session)
        for i in range(3):
            r = Recording(
                audio_hash=f"{i:064x}",
                path=f"{i}.wav",
                recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
                site_id=site.id,
            )
            session.add(r)
            session.flush()
            session.add(
                Identification(
                    recording_id=r.id,
                    source="emt.guano",
                    verdict=Verdict.SPECIES,
                    taxon_id=taxon.id,
                    first_seen_at=r.recorded_at,
                ),
            )
        session.commit()

        dataset_counts, site_counts = _taxon_counts(session)

        # Capture IDs while session is still open
        taxon_id = taxon.id
        site_id = site.id

    assert dataset_counts[taxon_id] == 3
    assert site_counts[(site_id, taxon_id)] == 3
