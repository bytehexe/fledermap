# tests/test_statistics_query.py
from __future__ import annotations

import math
from datetime import UTC, datetime

import pytest
from geoalchemy2.elements import WKTElement
from sqlalchemy import Engine
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import IdSource, Verdict
from fledermap.services.statistics import (
    rarest_species,
    rarest_unmapped_codes,
    recording_counts_by_site,
    recording_counts_by_taxon,
    site_diversity,
    totals,
)
from fledermap.store.models import Identification, Recording, Site, Taxon

pytestmark = pytest.mark.db


def _recording(
    session: OrmSession,
    *,
    audio_hash: str,
    taxon_id: int | None = None,
    verdict: Verdict | None = Verdict.SPECIES,
    recorded_at: datetime = datetime(2026, 8, 25, tzinfo=UTC),
    site_id: int | None = None,
    missing: bool = False,
) -> Recording:
    r = Recording(
        audio_hash=audio_hash,
        path=f"{audio_hash}.wav",
        recorded_at=recorded_at,
        site_id=site_id,
        missing_since=datetime(2026, 8, 25, tzinfo=UTC) if missing else None,
    )
    session.add(r)
    session.flush()
    if verdict is not None:
        session.add(
            Identification(
                recording_id=r.id,
                source=IdSource.EMT_GUANO,
                verdict=verdict,
                taxon_id=taxon_id,
                first_seen_at=recorded_at,
            ),
        )
    session.flush()
    return r


def test_totals_global_counts_all_recordings_species_and_sites(engine: Engine) -> None:
    with OrmSession(engine) as session:
        taxon = Taxon(rank="species", scientific_name="Eptesicus serotinus")
        site = Site(
            centroid=WKTElement("POINT(10 50)", srid=4326),
            radius_m=50.0,
            recording_count=1,
            first_at=datetime(2026, 8, 25, tzinfo=UTC),
            last_at=datetime(2026, 8, 25, tzinfo=UTC),
        )
        session.add_all([taxon, site])
        session.flush()
        _recording(session, audio_hash="a" * 64, taxon_id=taxon.id, site_id=site.id)
        _recording(session, audio_hash="b" * 64, verdict=Verdict.NOISE)
        session.commit()

    with OrmSession(engine) as session:
        result = totals(session)

    # total_recordings counts EVERY non-missing recording, species or not --
    # the donut deliberately doesn't sum to this (spec's inclusion rules).
    assert result.total_recordings == 2
    assert result.total_species == 1
    assert result.total_sites == 1


def test_totals_excludes_missing_recordings(engine: Engine) -> None:
    with OrmSession(engine) as session:
        _recording(session, audio_hash="a" * 64, missing=True)
        session.commit()

    with OrmSession(engine) as session:
        result = totals(session)

    assert result.total_recordings == 0


def test_totals_site_scoped_counts_only_that_site(engine: Engine) -> None:
    with OrmSession(engine) as session:
        site = Site(
            centroid=WKTElement("POINT(10 50)", srid=4326),
            radius_m=50.0,
            recording_count=1,
            first_at=datetime(2026, 8, 25, tzinfo=UTC),
            last_at=datetime(2026, 8, 25, tzinfo=UTC),
        )
        session.add(site)
        session.flush()
        _recording(session, audio_hash="a" * 64, site_id=site.id)
        _recording(session, audio_hash="b" * 64, site_id=None)
        session.commit()
        site_id = site.id

    with OrmSession(engine) as session:
        result = totals(session, site_id=site_id)

    assert result.total_recordings == 1
    assert result.total_species is None
    assert result.total_sites is None


def test_totals_species_scoped_counts_by_membership(engine: Engine) -> None:
    """A multi-species recording containing the filtered taxon counts, even
    though it's not that recording's sole result (spec: same membership rule
    as recording_counts_by_site)."""
    with OrmSession(engine) as session:
        taxon_a = Taxon(rank="species", scientific_name="Eptesicus serotinus")
        taxon_b = Taxon(rank="species", scientific_name="Pipistrellus pipistrellus")
        site = Site(
            centroid=WKTElement("POINT(10 50)", srid=4326),
            radius_m=50.0,
            recording_count=1,
            first_at=datetime(2026, 8, 25, tzinfo=UTC),
            last_at=datetime(2026, 8, 25, tzinfo=UTC),
        )
        session.add_all([taxon_a, taxon_b, site])
        session.flush()
        r = Recording(
            audio_hash="a" * 64,
            path="a.wav",
            recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
            site_id=site.id,
        )
        session.add(r)
        session.flush()
        session.add_all(
            [
                Identification(
                    recording_id=r.id,
                    source=IdSource.EMT_MANUAL,
                    verdict=Verdict.SPECIES,
                    taxon_id=taxon_a.id,
                    first_seen_at=r.recorded_at,
                ),
                Identification(
                    recording_id=r.id,
                    source=IdSource.EMT_MANUAL,
                    verdict=Verdict.SPECIES,
                    taxon_id=taxon_b.id,
                    first_seen_at=r.recorded_at,
                ),
            ],
        )
        session.commit()
        taxon_a_id = taxon_a.id

    with OrmSession(engine) as session:
        result = totals(session, taxon_id=taxon_a_id)

    assert result.total_recordings == 1
    assert result.total_sites == 1
    assert result.total_species is None


def test_recording_counts_by_taxon_excludes_noise_no_id_and_unidentified(
    engine: Engine,
) -> None:
    with OrmSession(engine) as session:
        taxon = Taxon(rank="species", scientific_name="Eptesicus serotinus")
        session.add(taxon)
        session.flush()
        _recording(session, audio_hash="a" * 64, taxon_id=taxon.id)
        _recording(session, audio_hash="b" * 64, verdict=Verdict.NOISE)
        _recording(session, audio_hash="c" * 64, verdict=Verdict.NO_ID)
        _recording(session, audio_hash="d" * 64, verdict=None)
        session.commit()

    with OrmSession(engine) as session:
        result = recording_counts_by_taxon(session)

    assert [(e.taxon.scientific_name, e.count) for e in result.entries] == [
        ("Eptesicus serotinus", 1),
    ]
    assert result.other_count == 0
    assert result.unmapped_count == 0
    assert result.multi_species_count == 0


def test_recording_counts_by_taxon_unmapped_species_gets_own_bucket(
    engine: Engine,
) -> None:
    with OrmSession(engine) as session:
        _recording(session, audio_hash="a" * 64, taxon_id=None, verdict=Verdict.SPECIES)
        session.commit()

    with OrmSession(engine) as session:
        result = recording_counts_by_taxon(session)

    assert result.entries == []
    assert result.unmapped_count == 1


def test_recording_counts_by_taxon_multi_species_gets_own_bucket_not_per_taxon(
    engine: Engine,
) -> None:
    with OrmSession(engine) as session:
        taxon_a = Taxon(rank="species", scientific_name="Eptesicus serotinus")
        taxon_b = Taxon(rank="species", scientific_name="Pipistrellus pipistrellus")
        session.add_all([taxon_a, taxon_b])
        session.flush()
        r = Recording(
            audio_hash="a" * 64,
            path="a.wav",
            recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
        )
        session.add(r)
        session.flush()
        session.add_all(
            [
                Identification(
                    recording_id=r.id,
                    source=IdSource.EMT_MANUAL,
                    verdict=Verdict.SPECIES,
                    taxon_id=taxon_a.id,
                    first_seen_at=r.recorded_at,
                ),
                Identification(
                    recording_id=r.id,
                    source=IdSource.EMT_MANUAL,
                    verdict=Verdict.SPECIES,
                    taxon_id=taxon_b.id,
                    first_seen_at=r.recorded_at,
                ),
            ],
        )
        session.commit()

    with OrmSession(engine) as session:
        result = recording_counts_by_taxon(session)

    # ONE recording, ONE "Multiple Species" bucket -- NOT one count per taxon
    # (that's rarest_species's job, not this function's -- see Task 6).
    assert result.entries == []
    assert result.multi_species_count == 1


def test_recording_counts_by_taxon_folds_past_top_n_into_other(
    engine: Engine,
) -> None:
    with OrmSession(engine) as session:
        taxa = [Taxon(rank="species", scientific_name=f"Species {i}") for i in range(3)]
        session.add_all(taxa)
        session.flush()
        # Species 0: 3 recordings, Species 1: 2, Species 2: 1 -- top_n=2 keeps
        # Species 0 and 1, folds Species 2's single recording into Other.
        for i, count in enumerate([3, 2, 1]):
            for n in range(count):
                _recording(
                    session,
                    audio_hash=f"{i}{n}".rjust(64, "0"),
                    taxon_id=taxa[i].id,
                )
        session.commit()

    with OrmSession(engine) as session:
        result = recording_counts_by_taxon(session, top_n=2)

    assert [(e.taxon.scientific_name, e.count) for e in result.entries] == [
        ("Species 0", 3),
        ("Species 1", 2),
    ]
    assert result.other_count == 1


def test_rarest_species_excludes_taxa_with_zero_recordings(engine: Engine) -> None:
    with OrmSession(engine) as session:
        found = Taxon(rank="species", scientific_name="Eptesicus serotinus")
        never_found = Taxon(rank="species", scientific_name="Myotis daubentonii")
        session.add_all([found, never_found])
        session.flush()
        _recording(session, audio_hash="a" * 64, taxon_id=found.id)
        session.commit()

    with OrmSession(engine) as session:
        result = rarest_species(session)

    assert [e.taxon.scientific_name for e in result.entries] == ["Eptesicus serotinus"]


def test_rarest_species_sorts_ascending_by_count(engine: Engine) -> None:
    with OrmSession(engine) as session:
        common = Taxon(rank="species", scientific_name="Common")
        rare = Taxon(rank="species", scientific_name="Rare")
        session.add_all([common, rare])
        session.flush()
        for n in range(3):
            _recording(session, audio_hash=f"c{n}".rjust(64, "0"), taxon_id=common.id)
        _recording(session, audio_hash="r0".rjust(64, "0"), taxon_id=rare.id)
        session.commit()

    with OrmSession(engine) as session:
        result = rarest_species(session)

    assert [(e.taxon.scientific_name, e.count) for e in result.entries] == [
        ("Rare", 1),
        ("Common", 3),
    ]


def test_rarest_species_multi_species_recording_counts_toward_every_taxon(
    engine: Engine,
) -> None:
    """Deliberately DIFFERENT from recording_counts_by_taxon's own multi-
    species handling (Task 3) -- this list is "which species are seldom
    detected," so a multi-species recording adds to EVERY taxon it contains,
    not one combined bucket."""
    with OrmSession(engine) as session:
        taxon_a = Taxon(rank="species", scientific_name="Eptesicus serotinus")
        taxon_b = Taxon(rank="species", scientific_name="Pipistrellus pipistrellus")
        session.add_all([taxon_a, taxon_b])
        session.flush()
        r = Recording(
            audio_hash="a" * 64,
            path="a.wav",
            recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
        )
        session.add(r)
        session.flush()
        session.add_all(
            [
                Identification(
                    recording_id=r.id,
                    source=IdSource.EMT_MANUAL,
                    verdict=Verdict.SPECIES,
                    taxon_id=taxon_a.id,
                    first_seen_at=r.recorded_at,
                ),
                Identification(
                    recording_id=r.id,
                    source=IdSource.EMT_MANUAL,
                    verdict=Verdict.SPECIES,
                    taxon_id=taxon_b.id,
                    first_seen_at=r.recorded_at,
                ),
            ],
        )
        session.commit()

    with OrmSession(engine) as session:
        result = rarest_species(session)

    assert {(e.taxon.scientific_name, e.count) for e in result.entries} == {
        ("Eptesicus serotinus", 1),
        ("Pipistrellus pipistrellus", 1),
    }


def test_rarest_unmapped_codes_groups_by_raw_label(engine: Engine) -> None:
    with OrmSession(engine) as session:
        r1 = Recording(
            audio_hash="a" * 64,
            path="a.wav",
            recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
        )
        r2 = Recording(
            audio_hash="b" * 64,
            path="b.wav",
            recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
        )
        r3 = Recording(
            audio_hash="c" * 64,
            path="c.wav",
            recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
        )
        session.add_all([r1, r2, r3])
        session.flush()
        session.add_all(
            [
                Identification(
                    recording_id=r1.id,
                    source=IdSource.EMT_GUANO,
                    verdict=Verdict.SPECIES,
                    taxon_id=None,
                    raw_label="WEIRDCODE",
                    first_seen_at=r1.recorded_at,
                ),
                Identification(
                    recording_id=r2.id,
                    source=IdSource.EMT_GUANO,
                    verdict=Verdict.SPECIES,
                    taxon_id=None,
                    raw_label="COMMONCODE",
                    first_seen_at=r2.recorded_at,
                ),
                Identification(
                    recording_id=r3.id,
                    source=IdSource.EMT_GUANO,
                    verdict=Verdict.SPECIES,
                    taxon_id=None,
                    raw_label="COMMONCODE",
                    first_seen_at=r3.recorded_at,
                ),
            ],
        )
        session.commit()

    with OrmSession(engine) as session:
        result = rarest_unmapped_codes(session)

    assert [(e.code, e.count) for e in result.entries] == [
        ("WEIRDCODE", 1),
        ("COMMONCODE", 2),
    ]


def test_recording_counts_by_site_ranks_by_count_descending(engine: Engine) -> None:
    with OrmSession(engine) as session:
        taxon = Taxon(rank="species", scientific_name="Eptesicus serotinus")
        busy = Site(
            centroid=WKTElement("POINT(10 50)", srid=4326),
            radius_m=50.0,
            recording_count=2,
            first_at=datetime(2026, 8, 25, tzinfo=UTC),
            last_at=datetime(2026, 8, 25, tzinfo=UTC),
        )
        quiet = Site(
            centroid=WKTElement("POINT(11 51)", srid=4326),
            radius_m=50.0,
            recording_count=1,
            first_at=datetime(2026, 8, 25, tzinfo=UTC),
            last_at=datetime(2026, 8, 25, tzinfo=UTC),
        )
        session.add_all([taxon, busy, quiet])
        session.flush()
        _recording(session, audio_hash="a" * 64, taxon_id=taxon.id, site_id=busy.id)
        _recording(session, audio_hash="b" * 64, taxon_id=taxon.id, site_id=busy.id)
        _recording(session, audio_hash="c" * 64, taxon_id=taxon.id, site_id=quiet.id)
        session.commit()
        taxon_id = taxon.id

    with OrmSession(engine) as session:
        result = recording_counts_by_site(session, taxon_id=taxon_id)

    assert [(e.site.recording_count, e.count) for e in result.entries] == [
        (2, 2),
        (1, 1),
    ]


def test_recording_counts_by_site_matches_by_membership_not_equality(
    engine: Engine,
) -> None:
    with OrmSession(engine) as session:
        taxon_a = Taxon(rank="species", scientific_name="Eptesicus serotinus")
        taxon_b = Taxon(rank="species", scientific_name="Pipistrellus pipistrellus")
        site = Site(
            centroid=WKTElement("POINT(10 50)", srid=4326),
            radius_m=50.0,
            recording_count=1,
            first_at=datetime(2026, 8, 25, tzinfo=UTC),
            last_at=datetime(2026, 8, 25, tzinfo=UTC),
        )
        session.add_all([taxon_a, taxon_b, site])
        session.flush()
        r = Recording(
            audio_hash="a" * 64,
            path="a.wav",
            recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
            site_id=site.id,
        )
        session.add(r)
        session.flush()
        session.add_all(
            [
                Identification(
                    recording_id=r.id,
                    source=IdSource.EMT_MANUAL,
                    verdict=Verdict.SPECIES,
                    taxon_id=taxon_a.id,
                    first_seen_at=r.recorded_at,
                ),
                Identification(
                    recording_id=r.id,
                    source=IdSource.EMT_MANUAL,
                    verdict=Verdict.SPECIES,
                    taxon_id=taxon_b.id,
                    first_seen_at=r.recorded_at,
                ),
            ],
        )
        session.commit()
        taxon_a_id = taxon_a.id

    with OrmSession(engine) as session:
        result = recording_counts_by_site(session, taxon_id=taxon_a_id)

    assert len(result.entries) == 1
    assert result.entries[0].count == 1


def test_site_diversity_single_site_richness_and_shannon(engine: Engine) -> None:
    with OrmSession(engine) as session:
        site = Site(
            centroid=WKTElement("POINT(10 50)", srid=4326),
            radius_m=50.0,
            recording_count=2,
            first_at=datetime(2026, 8, 25, tzinfo=UTC),
            last_at=datetime(2026, 8, 25, tzinfo=UTC),
        )
        taxon_a = Taxon(rank="species", scientific_name="A")
        taxon_b = Taxon(rank="species", scientific_name="B")
        session.add_all([site, taxon_a, taxon_b])
        session.flush()
        _recording(session, audio_hash="a" * 64, taxon_id=taxon_a.id, site_id=site.id)
        _recording(session, audio_hash="b" * 64, taxon_id=taxon_b.id, site_id=site.id)
        session.commit()
        site_id = site.id

    with OrmSession(engine) as session:
        result = site_diversity(session, site_id=site_id)

    assert len(result.entries) == 1
    entry = result.entries[0]
    assert entry.richness == 2
    # Two equally-common species: H = -sum(p*ln(p)) for p=[0.5, 0.5] = ln(2)
    assert entry.shannon == pytest.approx(math.log(2))


def test_site_diversity_unknown_site_id_returns_empty(engine: Engine) -> None:
    with OrmSession(engine) as session:
        result = site_diversity(session, site_id=999999)

    assert result.entries == []


def test_site_diversity_ranks_top_n_sites_by_richness(engine: Engine) -> None:
    with OrmSession(engine) as session:
        rich = Site(
            centroid=WKTElement("POINT(10 50)", srid=4326),
            radius_m=50.0,
            recording_count=2,
            first_at=datetime(2026, 8, 25, tzinfo=UTC),
            last_at=datetime(2026, 8, 25, tzinfo=UTC),
        )
        poor = Site(
            centroid=WKTElement("POINT(11 51)", srid=4326),
            radius_m=50.0,
            recording_count=1,
            first_at=datetime(2026, 8, 25, tzinfo=UTC),
            last_at=datetime(2026, 8, 25, tzinfo=UTC),
        )
        taxon_a = Taxon(rank="species", scientific_name="A")
        taxon_b = Taxon(rank="species", scientific_name="B")
        session.add_all([rich, poor, taxon_a, taxon_b])
        session.flush()
        _recording(session, audio_hash="a" * 64, taxon_id=taxon_a.id, site_id=rich.id)
        _recording(session, audio_hash="b" * 64, taxon_id=taxon_b.id, site_id=rich.id)
        _recording(session, audio_hash="c" * 64, taxon_id=taxon_a.id, site_id=poor.id)
        session.commit()

    with OrmSession(engine) as session:
        result = site_diversity(session, sort_by="richness")

    assert [e.richness for e in result.entries] == [2, 1]
