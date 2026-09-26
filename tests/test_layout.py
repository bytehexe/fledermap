from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import Engine

from fledermap.web.app import create_app

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("_vendor_icons")]


def test_htmx_is_loaded_on_a_page_that_previously_had_no_htmx_of_its_own(
    engine: Engine,
    tmp_path: Path,
) -> None:
    """statistics.html never had its own htmx.min.js <script> tag -- this only passes once
    _layout.html loads it globally, the same way it already does for alpine.min.js."""
    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    html = client.get("/statistics").get_data(as_text=True)

    assert "vendor/htmx.min.js" in html


def test_map_page_loads_htmx_exactly_once(engine: Engine, tmp_path: Path) -> None:
    """Guards against the fix leaving a duplicate <script> tag on pages that used to load
    htmx themselves."""
    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    html = client.get("/").get_data(as_text=True)

    assert html.count("vendor/htmx.min.js") == 1


def test_rtc_widget_container_is_present_when_enabled(
    engine: Engine, tmp_path: Path
) -> None:
    app = create_app(engine, tmp_path / "static", tmp_path / "media", rtc_enabled=True)
    client = app.test_client()

    html = client.get("/statistics").get_data(as_text=True)

    assert 'id="rtc-widget"' in html


def test_rtc_widget_container_is_absent_when_disabled(
    engine: Engine, tmp_path: Path
) -> None:
    """When disabled, the widget container must not be rendered at all -- not just return 404
    from the route while a dead container sits in the DOM. A dead container would still trigger
    the browser's initial hx-get on page load (just failing to a 404 instead of a 204)."""
    app = create_app(engine, tmp_path / "static", tmp_path / "media", rtc_enabled=False)
    client = app.test_client()

    html = client.get("/statistics").get_data(as_text=True)

    assert 'id="rtc-widget"' not in html
