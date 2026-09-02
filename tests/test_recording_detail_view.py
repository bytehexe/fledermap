from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session as OrmSession

from fledermap.store.models import Recording
from fledermap.web.app import create_app

pytestmark = pytest.mark.db


def test_recording_details_page_404s_for_an_unknown_hash(
    engine: Engine,
    tmp_path: Path,
) -> None:
    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().get(f"/recordings/{'f1' * 32}")

    assert response.status_code == 404


def test_recording_details_page_renders_the_recording(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="f2" * 32,
                path="a.wav",
                recorded_at=datetime(2026, 8, 25, 21, 0, tzinfo=UTC),
                make="Wildlife Acoustics",
                model="Echo Meter Touch 2",
                duration_s=0.5,
                samplerate_hz=256_000,
            ),
        )
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().get(f"/recordings/{'f2' * 32}")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Echo Meter Touch 2" in html
    assert f"/recordings/{'f2' * 32}/detail-spectrogram/0.webp" in html
    assert f"/recordings/{'f2' * 32}/detail-oscillogram/0.webp" in html


def test_recording_details_page_bakes_in_the_preview_time_expansion_factor(
    engine: Engine,
    tmp_path: Path,
) -> None:
    """The <audio> element plays the same x10 time-expanded preview
    `media/preview.py` renders everywhere else -- `audio.currentTime` is on
    that expanded timeline, not the spectrogram's native-real-time locked
    scale, so the JS needs this factor to convert between them (cursor sync
    and click-to-play were both wrong without it: the cursor crawled at 1/10
    speed and scrolled off the image with ~90% of playback still left)."""
    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="f5" * 32,
                path="a.wav",
                recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
                duration_s=0.5,
                samplerate_hz=256_000,
            ),
        )
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().get(f"/recordings/{'f5' * 32}")

    html = response.get_data(as_text=True)
    assert 'data-time-expansion-factor="10"' in html


def test_recording_details_page_renders_one_img_per_tile(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="f4" * 32,
                path="a.wav",
                recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
                duration_s=1.0,  # needs 3 tiles at DETAIL_MAX_TILE_WIDTH_PX=8000, DETAIL_PX_PER_MS=19.0
                samplerate_hz=256_000,
            ),
        )
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().get(f"/recordings/{'f4' * 32}")

    html = response.get_data(as_text=True)
    assert html.count('class="detail-spectrogram-tile"') == 3
    assert html.count('class="detail-oscillogram-tile"') == 3
    assert "/detail-spectrogram/0.webp" in html
    assert "/detail-spectrogram/1.webp" in html
    assert "/detail-spectrogram/2.webp" in html
    assert "/detail-oscillogram/0.webp" in html
    assert "/detail-oscillogram/1.webp" in html
    assert "/detail-oscillogram/2.webp" in html


def test_recording_details_page_explains_missing_metadata(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="f3" * 32,
                path="a.wav",
                recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
                # duration_s / samplerate_hz left unset
            ),
        )
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().get(f"/recordings/{'f3' * 32}")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "cannot render" in html.lower()
    assert "detail-spectrogram/0.webp" not in html
