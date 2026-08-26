from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session as OrmSession

from fledermap.store.models import Session as AnnotationSession
from fledermap.store.models import Taxon
from fledermap.web.app import create_app

pytestmark = pytest.mark.db


def test_map_page_renders_the_leaflet_shell(engine: Engine, tmp_path: Path) -> None:
    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.get("/")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert '<div id="map">' in html
    assert "vendor/leaflet.js" in html
    assert "vendor/leaflet.markercluster.js" in html
    assert "vendor/htmx.min.js" in html
    assert "vendor/alpine.min.js" in html


def test_map_page_includes_the_filter_form(engine: Engine, tmp_path: Path) -> None:
    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.get("/")

    html = response.get_data(as_text=True)
    assert 'name="verdict"' in html
    assert 'name="taxon"' in html
    assert 'name="from"' in html
    assert 'name="to"' in html
    assert 'name="session"' in html
    assert 'name="source"' in html
    # finding 7: emt.manual has real, produced data today and must be
    # selectable, unlike `manual`, which nothing produces yet.
    assert 'value="emt.manual"' in html
    assert 'value="manual"' not in html


def test_taxon_filter_is_a_dropdown_of_real_taxa(
    engine: Engine,
    tmp_path: Path,
) -> None:
    """A numeric ID input is not something a person can use -- feedback on
    the first UI pass. Taxon has a small, fixed set of real names, so it
    becomes a <select>, unlike Session below."""
    with OrmSession(engine) as session:
        taxon = Taxon(rank="species", scientific_name="Pipistrellus pipistrellus")
        session.add(taxon)
        session.commit()
        taxon_id = taxon.id

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.get("/")

    html = response.get_data(as_text=True)
    assert '<select name="taxon"' in html
    assert f'<option value="{taxon_id}">Pipistrellus pipistrellus</option>' in html


def test_taxon_option_includes_common_name_when_present(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        taxon = Taxon(
            rank="species",
            scientific_name="Eptesicus serotinus",
            common_name_en="Serotine bat",
        )
        session.add(taxon)
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    html = client.get("/").get_data(as_text=True)
    assert "Eptesicus serotinus — Serotine bat" in html


def test_session_filter_is_a_dropdown_labelled_by_date_range_and_detector(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        annotation_session = AnnotationSession(
            started_at=datetime(2026, 8, 1, 22, 0, tzinfo=UTC),
            ended_at=datetime(2026, 8, 1, 23, 15, tzinfo=UTC),
            detector_key="ABC123",
        )
        session.add(annotation_session)
        session.commit()
        session_id = annotation_session.id

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    html = client.get("/").get_data(as_text=True)
    assert '<select name="session"' in html
    assert (
        f'<option value="{session_id}">2026-08-01 22:00–23:15 (ABC123)</option>' in html
    )


def test_session_option_falls_back_when_detector_key_is_missing(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        annotation_session = AnnotationSession(
            started_at=datetime(2026, 8, 1, 22, 0, tzinfo=UTC),
            ended_at=datetime(2026, 8, 1, 23, 15, tzinfo=UTC),
        )
        session.add(annotation_session)
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    html = client.get("/").get_data(as_text=True)
    assert "2026-08-01 22:00–23:15 (unknown detector)" in html
