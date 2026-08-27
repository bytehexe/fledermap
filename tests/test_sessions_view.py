from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import SessionKind
from fledermap.store.models import Session as AnnotationSession
from fledermap.web.app import create_app

pytestmark = pytest.mark.db


def test_sessions_list_renders_a_session_row(engine: Engine, tmp_path: Path) -> None:
    with OrmSession(engine) as session:
        session.add(
            AnnotationSession(
                started_at=datetime(2026, 8, 21, 21, tzinfo=UTC),
                ended_at=datetime(2026, 8, 21, 23, tzinfo=UTC),
                kind=SessionKind.STATIONARY,
                detector_key="EMT\x1f1",
            ),
        )
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.get("/sessions")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "EMT" in html
    assert "stationary" in html


def test_sessions_list_filters_by_detector(engine: Engine, tmp_path: Path) -> None:
    with OrmSession(engine) as session:
        session.add(
            AnnotationSession(
                started_at=datetime(2026, 8, 21, tzinfo=UTC),
                ended_at=datetime(2026, 8, 21, tzinfo=UTC),
                kind=SessionKind.STATIONARY,
                detector_key="EMT\x1f1",
            ),
        )
        session.add(
            AnnotationSession(
                started_at=datetime(2026, 8, 21, tzinfo=UTC),
                ended_at=datetime(2026, 8, 21, tzinfo=UTC),
                kind=SessionKind.STATIONARY,
                detector_key="Kaleidoscope\x1f2",
            ),
        )
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.get("/sessions?detector=EMT")

    html = response.get_data(as_text=True)
    assert "EMT" in html
    assert "Kaleidoscope" not in html


def test_sessions_list_empty_state(engine: Engine, tmp_path: Path) -> None:
    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.get("/sessions")

    assert response.status_code == 200
    assert "No sessions match" in response.get_data(as_text=True)


def test_sessions_list_bad_date_returns_400(engine: Engine, tmp_path: Path) -> None:
    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.get("/sessions?from=not-a-date")

    assert response.status_code == 400
