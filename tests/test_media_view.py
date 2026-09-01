from __future__ import annotations

import io
import math
import struct
from datetime import UTC, datetime
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy import Engine
from sqlalchemy.orm import Session as OrmSession

from fledermap.media.paths import oscillogram_path, preview_path, spectrogram_path
from fledermap.services.recording_detail import DETAIL_PX_PER_MS
from fledermap.store.models import Recording
from fledermap.web.app import create_app
from tests.fixtures import build_wav, fmt_payload

pytestmark = pytest.mark.db


def _sine_pcm(
    *, freq_hz: float = 45_000.0, samplerate: int = 256_000, duration_s: float
) -> bytes:
    n_samples = int(samplerate * duration_s)
    samples = [
        int(32000 * math.sin(2 * math.pi * freq_hz * i / samplerate))
        for i in range(n_samples)
    ]
    return struct.pack(f"<{n_samples}h", *samples)


def _write_wav(path: Path, *, duration_s: float, samplerate: int = 256_000) -> None:
    path.write_bytes(
        build_wav(
            [
                (b"fmt ", fmt_payload(samplerate)),
                (b"data", _sine_pcm(samplerate=samplerate, duration_s=duration_s)),
            ],
        ),
    )


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


def test_oscillogram_serves_existing_file(engine: Engine, tmp_path: Path) -> None:
    media_root = tmp_path / "media"
    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="e" * 64,
                path="e.wav",
                recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
            ),
        )
        session.commit()

    path = oscillogram_path(media_root, "e" * 64)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"fake-webp-bytes")

    app = create_app(engine, tmp_path / "static", media_root)
    response = app.test_client().get(f"/media/{'e' * 64}/oscillogram.webp")

    assert response.status_code == 200
    assert response.data == b"fake-webp-bytes"


def test_oscillogram_404s_when_not_yet_rendered(engine: Engine, tmp_path: Path) -> None:
    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="f" * 64,
                path="f.wav",
                recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
            ),
        )
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().get(f"/media/{'f' * 64}/oscillogram.webp")

    assert response.status_code == 404


def test_oscillogram_404s_for_unknown_hash(engine: Engine, tmp_path: Path) -> None:
    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().get(f"/media/{'g' * 64}/oscillogram.webp")

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


def test_detail_spectrogram_renders_at_the_locked_scale(
    engine: Engine,
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archive"
    archive_root.mkdir()
    duration_s = 0.02
    _write_wav(archive_root / "a.wav", duration_s=duration_s)

    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="d1" * 32,
                path="a.wav",
                recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
                duration_s=duration_s,
                samplerate_hz=256_000,
            ),
        )
        session.commit()

    app = create_app(
        engine,
        tmp_path / "static",
        tmp_path / "media",
        archive_roots=(archive_root,),
    )
    response = app.test_client().get(f"/recordings/{'d1' * 32}/detail-spectrogram.webp")

    assert response.status_code == 200
    assert response.mimetype == "image/webp"
    image = Image.open(io.BytesIO(response.data))
    assert image.width == round(duration_s * 1000 * DETAIL_PX_PER_MS)


def test_detail_oscillogram_shares_the_spectrogram_s_width(
    engine: Engine,
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archive"
    archive_root.mkdir()
    duration_s = 0.02
    _write_wav(archive_root / "a.wav", duration_s=duration_s)

    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="d2" * 32,
                path="a.wav",
                recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
                duration_s=duration_s,
                samplerate_hz=256_000,
            ),
        )
        session.commit()

    app = create_app(
        engine,
        tmp_path / "static",
        tmp_path / "media",
        archive_roots=(archive_root,),
    )
    spectrogram_response = app.test_client().get(
        f"/recordings/{'d2' * 32}/detail-spectrogram.webp",
    )
    oscillogram_response = app.test_client().get(
        f"/recordings/{'d2' * 32}/detail-oscillogram.webp",
    )

    spectrogram_image = Image.open(io.BytesIO(spectrogram_response.data))
    oscillogram_image = Image.open(io.BytesIO(oscillogram_response.data))
    assert oscillogram_image.width == spectrogram_image.width


def test_detail_spectrogram_404s_for_an_unknown_hash(
    engine: Engine,
    tmp_path: Path,
) -> None:
    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().get(f"/recordings/{'e1' * 32}/detail-spectrogram.webp")

    assert response.status_code == 404


def test_detail_spectrogram_404s_when_duration_is_missing(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="e2" * 32,
                path="a.wav",
                recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
                # duration_s and samplerate_hz left unset (None)
            ),
        )
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().get(f"/recordings/{'e2' * 32}/detail-spectrogram.webp")

    assert response.status_code == 404
