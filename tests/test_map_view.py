from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import Engine

from fledermap.web.app import create_app

pytestmark = pytest.mark.db


def test_map_page_renders_the_leaflet_shell(engine: Engine, tmp_path: Path) -> None:
    app = create_app(engine, tmp_path / "static")
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
    app = create_app(engine, tmp_path / "static")
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
