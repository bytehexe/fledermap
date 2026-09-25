from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session as OrmSession

from fledermap.store.models import Recording
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
