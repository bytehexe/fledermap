from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session as OrmSession

from fledermap.media.paths import preview_path, spectrogram_path
from fledermap.store.models import Recording
from fledermap.web.app import create_app

pytestmark = pytest.mark.db


def test_spectrogram_serves_existing_file(engine: Engine, tmp_path: Path) -> None:
    media_root = tmp_path / "media"
    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="a" * 64,
                path="a.wav",
                recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
            ),
        )
        session.commit()

    path = spectrogram_path(media_root, "a" * 64)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"fake-webp-bytes")

    app = create_app(engine, tmp_path / "static", media_root)
    client = app.test_client()
    response = client.get(f"/media/{'a' * 64}/spectrogram.webp")

    assert response.status_code == 200
    assert response.data == b"fake-webp-bytes"


def test_spectrogram_404s_when_not_yet_rendered(engine: Engine, tmp_path: Path) -> None:
    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="b" * 64,
                path="b.wav",
                recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
            ),
        )
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().get(f"/media/{'b' * 64}/spectrogram.webp")

    assert response.status_code == 404


def test_spectrogram_404s_for_unknown_hash(engine: Engine, tmp_path: Path) -> None:
    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().get(f"/media/{'c' * 64}/spectrogram.webp")

    assert response.status_code == 404


def test_preview_serves_existing_file(engine: Engine, tmp_path: Path) -> None:
    media_root = tmp_path / "media"
    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="d" * 64,
                path="d.wav",
                recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
            ),
        )
        session.commit()

    path = preview_path(media_root, "d" * 64)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"fake-opus-bytes")

    app = create_app(engine, tmp_path / "static", media_root)
    response = app.test_client().get(f"/media/{'d' * 64}/preview.opus")

    assert response.status_code == 200
    assert response.data == b"fake-opus-bytes"
