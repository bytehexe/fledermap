from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session as OrmSession

from fledermap.store.models import Recording, Site
from fledermap.web.app import create_app

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("_vendor_icons")]


def test_search_page_renders_with_no_query(engine: Engine, tmp_path: Path) -> None:
    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.get("/search")

    assert response.status_code == 200
    assert "Search syntax" in response.get_data(as_text=True)


def test_search_page_finds_a_recording_by_filename(
    engine: Engine, tmp_path: Path
) -> None:
    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="a" * 64,
                path="PIPPIP_20260914_220508.wav",
                recorded_at=datetime(2026, 9, 14, tzinfo=UTC),
            ),
        )
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.get("/search?q=PIPPIP")

    html = response.get_data(as_text=True)
    assert "PIPPIP_20260914_220508.wav" in html


def test_search_page_shows_a_warning_for_an_unknown_field(
    engine: Engine, tmp_path: Path
) -> None:
    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.get("/search?q=spceies:PIPPIP")

    html = response.get_data(as_text=True)
    assert (
        "Unknown field &#39;spceies&#39;" in html or "Unknown field 'spceies'" in html
    )


def test_search_warning_is_inside_the_htmx_swap_target(
    engine: Engine, tmp_path: Path
) -> None:
    """Regression test: the form's `hx-target`/`hx-select` both point at
    `#search-results-wrapper`, so a live (keyup-triggered) search only re-renders that div's
    contents. A warnings block placed outside it would never update on a live search -- it
    would linger after the query is corrected, and never appear for a query typed after page
    load. Assert the warning appears strictly between the wrapper's opening tag and its
    matching close, not merely somewhere on the page."""
    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.get("/search?q=spceies:PIPPIP")
    html = response.get_data(as_text=True)

    wrapper_start = html.index('id="search-results-wrapper"')
    warning_pos = next(
        html.index(needle)
        for needle in ("Unknown field &#39;spceies&#39;", "Unknown field 'spceies'")
        if needle in html
    )
    # The results table (unambiguously inside the wrapper) marks a safe upper bound for
    # "still inside the wrapper" without needing to hand-parse the matching </div>.
    table_pos = html.index('class="entity-list"')

    assert wrapper_start < warning_pos < table_pos


def test_search_page_shows_the_sites_name_not_a_bare_id(
    engine: Engine, tmp_path: Path
) -> None:
    from geoalchemy2.elements import WKTElement

    with OrmSession(engine) as session:
        site = Site(
            centroid=WKTElement("POINT(10 50)", srid=4326),
            radius_m=50.0,
            recording_count=1,
            first_at=datetime(2026, 9, 14, tzinfo=UTC),
            last_at=datetime(2026, 9, 14, tzinfo=UTC),
            name="Old Barn",
        )
        session.add(site)
        session.flush()
        session.add(
            Recording(
                audio_hash="a" * 64,
                path="PIPPIP_20260914_220508.wav",
                recorded_at=datetime(2026, 9, 14, tzinfo=UTC),
                site_id=site.id,
            ),
        )
        session.commit()
        site_id = site.id

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.get("/search?q=PIPPIP")
    html = response.get_data(as_text=True)

    assert f'<a href="/sites/{site_id}">Old Barn</a>' in html
    assert f"Site {site_id}<" not in html
