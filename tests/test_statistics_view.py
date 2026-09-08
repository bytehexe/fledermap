# tests/test_statistics_view.py
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from geoalchemy2.elements import WKTElement
from sqlalchemy import Engine
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import IdSource, Verdict
from fledermap.store.models import Identification, Recording, Site, Taxon
from fledermap.web.app import create_app

pytestmark = pytest.mark.db


def test_statistics_global_page_renders_stat_tiles_and_chart_data(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        taxon = Taxon(rank="species", scientific_name="Eptesicus serotinus")
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
    response = app.test_client().get("/statistics")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "1" in html  # total recordings stat tile
    assert "Eptesicus serotinus" in html  # embedded in the donut's JSON data
    assert "stats-band" in html
    assert "chart.js" in html  # vendored script tag


def test_statistics_site_page_renders_and_404s_for_unknown_site(
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

    response = client.get(f"/statistics/sites/{site_id}")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Old Barn" in html
    assert "Diversity index" in html

    missing_response = client.get("/statistics/sites/999999")
    assert missing_response.status_code == 404


def test_statistics_species_page_renders_and_404s_for_unknown_taxon(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        taxon = Taxon(rank="species", scientific_name="Eptesicus serotinus")
        session.add(taxon)
        session.commit()
        taxon_id = taxon.id

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.get(f"/statistics/species/{taxon_id}")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Eptesicus serotinus" in html

    missing_response = client.get("/statistics/species/999999")
    assert missing_response.status_code == 404
