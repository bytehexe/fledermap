from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from geoalchemy2.elements import WKTElement
from sqlalchemy import Engine
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import IdSource, Verdict
from fledermap.store.models import Identification, Recording, Site, Taxon, TaxonCode
from fledermap.web.app import create_app

pytestmark = pytest.mark.db


def test_species_list_renders_a_species_row(engine: Engine, tmp_path: Path) -> None:
    with OrmSession(engine) as session:
        taxon = Taxon(
            rank="species",
            scientific_name="Eptesicus serotinus",
            common_name_en="Serotine bat",
            common_name_de="Breitflügelfledermaus",
        )
        session.add(taxon)
        session.flush()
        r = Recording(
            audio_hash="a" * 64,
            path="a.wav",
            recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
        )
        session.add(r)
        session.flush()
        session.add(
            Identification(
                recording_id=r.id,
                source=IdSource.EMT_GUANO,
                verdict=Verdict.SPECIES,
                taxon_id=taxon.id,
                first_seen_at=r.recorded_at,
            ),
        )
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.get("/species")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Eptesicus serotinus" in html


def test_species_detail_renders_common_names_and_404s_for_unknown_taxon(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        taxon = Taxon(
            rank="species",
            scientific_name="Eptesicus serotinus",
            common_name_en="Serotine bat",
            common_name_de="Breitflügelfledermaus",
        )
        session.add(taxon)
        session.flush()
        session.add(TaxonCode(source="wa", code="EPTSER", taxon_id=taxon.id))
        r = Recording(
            audio_hash="a" * 64,
            path="a.wav",
            recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
        )
        session.add(r)
        session.flush()
        session.add(
            Identification(
                recording_id=r.id,
                source=IdSource.EMT_GUANO,
                verdict=Verdict.SPECIES,
                taxon_id=taxon.id,
                first_seen_at=r.recorded_at,
            ),
        )
        session.commit()
        taxon_id = taxon.id

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.get(f"/species/{taxon_id}")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Serotine bat" in html
    assert "Breitflügelfledermaus" in html
    assert "EPTSER" in html
    assert "<table" in html  # sites + recent recordings render as tables, not lists
    assert (
        "panel-columns" not in html
    )  # side-by-side column boxes dropped on this full page

    missing_response = client.get("/species/999999")
    assert missing_response.status_code == 404


def test_sites_list_renders_a_site_row(engine: Engine, tmp_path: Path) -> None:
    with OrmSession(engine) as session:
        session.add(
            Site(
                centroid=WKTElement("POINT(10 50)", srid=4326),
                radius_m=50.0,
                recording_count=3,
                first_at=datetime(2026, 8, 25, tzinfo=UTC),
                last_at=datetime(2026, 8, 26, tzinfo=UTC),
                name="Old Barn",
            ),
        )
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.get("/sites")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Old Barn" in html


def test_site_detail_page_renders_and_404s_for_unknown_site(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        site = Site(
            centroid=WKTElement("POINT(10 50)", srid=4326),
            radius_m=50.0,
            recording_count=1,
            first_at=datetime(2026, 8, 25, tzinfo=UTC),
            last_at=datetime(2026, 8, 25, tzinfo=UTC),
            name="Old Barn",
        )
        session.add(site)
        session.commit()
        site_id = site.id

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.get(f"/sites/{site_id}")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Old Barn" in html
    assert "<table" in html  # species breakdown + sessions render as tables, not lists
    assert (
        "panel-columns" not in html
    )  # side-by-side column boxes dropped on this full page

    missing_response = client.get("/sites/999999")
    assert missing_response.status_code == 404
