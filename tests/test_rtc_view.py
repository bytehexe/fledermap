from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import Engine

from fledermap.web.app import create_app

# `icon()` reads real files off disk for the "day"/"dawn"/"dusk"/"always_day" states'
# sun/sunrise/sunset icons -- see conftest.py's `_vendor_icons` docstring for why this suite
# needs it opted in, same as every other view test that renders an icon-bearing template.
pytestmark = [pytest.mark.db, pytest.mark.usefixtures("_vendor_icons")]


def test_rtc_widget_returns_404_when_disabled(engine: Engine, tmp_path: Path) -> None:
    app = create_app(engine, tmp_path / "static", tmp_path / "media", rtc_enabled=False)
    client = app.test_client()

    response = client.get("/rtc-widget")

    assert response.status_code == 404


def test_rtc_widget_returns_204_when_no_location_available(
    engine: Engine, tmp_path: Path
) -> None:
    app = create_app(engine, tmp_path / "static", tmp_path / "media", rtc_enabled=True)
    client = app.test_client()

    response = client.get("/rtc-widget")

    assert response.status_code == 204


def test_rtc_widget_uses_query_params_when_no_server_location_configured(
    engine: Engine, tmp_path: Path
) -> None:
    app = create_app(engine, tmp_path / "static", tmp_path / "media", rtc_enabled=True)
    client = app.test_client()

    response = client.get("/rtc-widget?lat=51.5074&lon=-0.1278")

    assert response.status_code == 200
    assert "rtc-widget-content" in response.get_data(as_text=True)


def test_rtc_widget_ignores_query_params_when_server_location_is_configured(
    engine: Engine, tmp_path: Path
) -> None:
    """Review Focus case: server config always wins, even when a different, validly-formed
    lat/lon is passed in the query string."""
    app = create_app(
        engine,
        tmp_path / "static",
        tmp_path / "media",
        rtc_enabled=True,
        latitude=51.5074,
        longitude=-0.1278,
    )
    client = app.test_client()

    configured = client.get("/rtc-widget").get_data(as_text=True)
    with_bogus_query_params = client.get(
        "/rtc-widget?lat=-33.8688&lon=151.2093"  # Sydney -- very different result
    ).get_data(as_text=True)

    assert configured == with_bogus_query_params


def test_rtc_widget_extreme_latitude_polar_day(engine: Engine, tmp_path: Path) -> None:
    app = create_app(engine, tmp_path / "static", tmp_path / "media", rtc_enabled=True)
    client = app.test_client()

    # Tromso in summer -- polar day. The view's own "now" is real wall-clock time, so this
    # test only makes sense if run in a month where that's actually true; instead exercise it
    # through query params against a date astral will treat consistently: this endpoint always
    # computes for "today", so this test asserts the mechanism (a valid response, not a 500)
    # rather than pinning a specific season-dependent state.
    response = client.get("/rtc-widget?lat=69.6492&lon=18.9553")

    assert response.status_code == 200
