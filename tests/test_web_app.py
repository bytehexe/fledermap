from __future__ import annotations

import zoneinfo
from pathlib import Path

import pytest
from sqlalchemy import Engine

from fledermap.web.app import create_app

pytestmark = pytest.mark.db


def test_create_app_registers_vendor_static_blueprint(
    tmp_path: Path,
    engine: Engine,
) -> None:
    vendor_dir = tmp_path / "static" / "vendor"
    vendor_dir.mkdir(parents=True)
    (vendor_dir / "leaflet.js").write_text("/* fake */")

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.get("/static/vendor/leaflet.js")

    assert response.status_code == 200
    assert response.data == b"/* fake */"


def test_create_app_stores_the_engine_on_config(tmp_path: Path, engine: Engine) -> None:
    app = create_app(engine, tmp_path / "static", tmp_path / "media")

    assert app.config["ENGINE"] is engine


def test_create_app_discovers_the_display_timezone_from_postgres(
    tmp_path: Path,
    engine: Engine,
) -> None:
    app = create_app(engine, tmp_path / "static", tmp_path / "media")

    # The postgis testcontainer image defaults its cluster TimeZone to UTC
    # (no override is configured anywhere in this test suite) -- asserting
    # the *type* and that it round-trips through ZoneInfo is what matters
    # here, not a hardcoded zone name that would only hold on this one image.
    assert isinstance(app.config["DISPLAY_TIMEZONE_NAME"], str)
    assert app.config["DISPLAY_TIMEZONE"] == zoneinfo.ZoneInfo(
        app.config["DISPLAY_TIMEZONE_NAME"]
    )


def test_create_app_registers_local_datetime_and_local_date_filters(
    tmp_path: Path,
    engine: Engine,
) -> None:
    app = create_app(engine, tmp_path / "static", tmp_path / "media")

    assert "local_datetime" in app.jinja_env.filters
    assert "local_date" in app.jinja_env.filters
