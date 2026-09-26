from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import Engine

from fledermap.services.sun import (
    MoonInfo,
    MoonPhaseBucket,
    NextTransition,
    RtcSnapshot,
    RtcSunState,
)
from fledermap.web.app import create_app
from fledermap.web.views.rtc import _render

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
    """Server-configured location always takes precedence, even when a different, validly-formed
    lat/lon is passed in the query string. This ensures a server that pins a location can't be
    overridden by client-side parameters."""
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


# Direct unit tests for _render(), covering all states without needing live clock or db.
# These tests use a minimal Flask app context (not the full test client setup with db),
# because render_template needs an app but _render is a pure function -- the DOM and
# template rendering are side effects only, not logic we're testing. The http-layer
# concerns (status codes, config precedence) are covered by the view tests above.
def test_render_day_state(engine: Engine, tmp_path: Path) -> None:
    """DAY state with a next transition (sunset)."""
    app = create_app(engine, tmp_path / "static", tmp_path / "media", rtc_enabled=True)
    with app.app_context():
        tomorrow = datetime(2024, 6, 22, 12, 0, tzinfo=ZoneInfo("Europe/London"))
        snapshot = RtcSnapshot(
            state=RtcSunState.DAY,
            next_transition=NextTransition("Sunset", tomorrow),
            moon=MoonInfo(phase=MoonPhaseBucket.FULL, illumination_pct=100),
            extreme_latitude=None,
        )
        html = _render(snapshot)
        assert "Daylight" in html
        assert "Sunset 12:00" in html
        assert "rtc-widget-content" in html


def test_render_dawn_state(engine: Engine, tmp_path: Path) -> None:
    """DAWN state with a next transition (sunrise)."""
    app = create_app(engine, tmp_path / "static", tmp_path / "media", rtc_enabled=True)
    with app.app_context():
        tomorrow = datetime(2024, 6, 22, 6, 0, tzinfo=ZoneInfo("Europe/London"))
        snapshot = RtcSnapshot(
            state=RtcSunState.DAWN,
            next_transition=NextTransition("Sunrise", tomorrow),
            moon=MoonInfo(phase=MoonPhaseBucket.FULL, illumination_pct=100),
            extreme_latitude=None,
        )
        html = _render(snapshot)
        assert "Dawn" in html
        assert "Sunrise 06:00" in html


def test_render_dusk_state(engine: Engine, tmp_path: Path) -> None:
    """DUSK state with a next transition."""
    app = create_app(engine, tmp_path / "static", tmp_path / "media", rtc_enabled=True)
    with app.app_context():
        later = datetime(2024, 6, 22, 22, 30, tzinfo=ZoneInfo("Europe/London"))
        snapshot = RtcSnapshot(
            state=RtcSunState.DUSK,
            next_transition=NextTransition("Night from", later),
            moon=MoonInfo(phase=MoonPhaseBucket.FULL, illumination_pct=100),
            extreme_latitude=None,
        )
        html = _render(snapshot)
        assert "Dusk" in html
        assert "Night from 22:30" in html


def test_render_night_state_with_moon(engine: Engine, tmp_path: Path) -> None:
    """NIGHT state always shows moon phase."""
    app = create_app(engine, tmp_path / "static", tmp_path / "media", rtc_enabled=True)
    with app.app_context():
        tomorrow = datetime(2024, 6, 22, 1, 58, tzinfo=ZoneInfo("Europe/London"))
        snapshot = RtcSnapshot(
            state=RtcSunState.NIGHT,
            next_transition=NextTransition("Dawn", tomorrow),
            moon=MoonInfo(phase=MoonPhaseBucket.WANING_CRESCENT, illumination_pct=20),
            extreme_latitude=None,
        )
        html = _render(snapshot)
        assert "Night" in html
        assert "Dawn 01:58" in html
        # Moon phase icon should be present for night state
        assert "waning-crescent" in html or "moon" in html.lower()


def test_render_always_day(engine: Engine, tmp_path: Path) -> None:
    """ALWAYS_DAY extreme latitude state (polar day)."""
    app = create_app(engine, tmp_path / "static", tmp_path / "media", rtc_enabled=True)
    with app.app_context():
        snapshot = RtcSnapshot(
            state=None,
            next_transition=None,
            moon=None,
            extreme_latitude="always_day",
        )
        html = _render(snapshot)
        assert "Polar day" in html
        assert "rtc-widget-content" in html


def test_render_always_night(engine: Engine, tmp_path: Path) -> None:
    """ALWAYS_NIGHT extreme latitude state (polar night)."""
    app = create_app(engine, tmp_path / "static", tmp_path / "media", rtc_enabled=True)
    with app.app_context():
        snapshot = RtcSnapshot(
            state=None,
            next_transition=None,
            moon=MoonInfo(phase=MoonPhaseBucket.NEW, illumination_pct=1),
            extreme_latitude="always_night",
        )
        html = _render(snapshot)
        assert "Polar night" in html
        # Moon phase icon should be present for polar night
        assert "new" in html or "moon" in html.lower()
