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
