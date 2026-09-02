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
from fledermap.services.recording_detail import (
    DETAIL_MAX_TILE_WIDTH_PX,
    DETAIL_PX_PER_MS,
)
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
    response = app.test_client().get(
        f"/recordings/{'d1' * 32}/detail-spectrogram/0.webp"
    )

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
        f"/recordings/{'d2' * 32}/detail-spectrogram/0.webp",
    )
    oscillogram_response = app.test_client().get(
        f"/recordings/{'d2' * 32}/detail-oscillogram/0.webp",
    )

    spectrogram_image = Image.open(io.BytesIO(spectrogram_response.data))
    oscillogram_image = Image.open(io.BytesIO(oscillogram_response.data))
    assert oscillogram_image.width == spectrogram_image.width


def test_detail_spectrogram_404s_for_an_unknown_hash(
    engine: Engine,
    tmp_path: Path,
) -> None:
    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().get(
        f"/recordings/{'e1' * 32}/detail-spectrogram/0.webp"
    )

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
    response = app.test_client().get(
        f"/recordings/{'e2' * 32}/detail-spectrogram/0.webp"
    )

    assert response.status_code == 404


def test_detail_spectrogram_tile_renders_at_the_tile_s_own_width(
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
                audio_hash="d3" * 32,
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
    response = app.test_client().get(
        f"/recordings/{'d3' * 32}/detail-spectrogram/0.webp"
    )

    assert response.status_code == 200
    image = Image.open(io.BytesIO(response.data))
    assert image.width == round(
        duration_s * 1000 * DETAIL_PX_PER_MS
    )  # single tile: whole width


def test_detail_spectrogram_404s_for_an_out_of_range_tile_index(
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
                audio_hash="d4" * 32,
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
    response = app.test_client().get(
        f"/recordings/{'d4' * 32}/detail-spectrogram/1.webp"
    )

    assert response.status_code == 404


def test_detail_spectrogram_renders_multiple_tiles_for_a_long_recording(
    engine: Engine,
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archive"
    archive_root.mkdir()
    # DETAIL_MAX_TILE_WIDTH_PX (8000) / DETAIL_PX_PER_MS (19.0) / 1000 ~= 0.421s per tile --
    # 1.0s needs 3 tiles (8000 + 8000 + 3000 = 19000px total width).
    duration_s = 1.0
    _write_wav(archive_root / "long.wav", duration_s=duration_s)

    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="d5" * 32,
                path="long.wav",
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
    client = app.test_client()

    tile_0 = client.get(f"/recordings/{'d5' * 32}/detail-spectrogram/0.webp")
    tile_1 = client.get(f"/recordings/{'d5' * 32}/detail-spectrogram/1.webp")
    tile_2 = client.get(f"/recordings/{'d5' * 32}/detail-spectrogram/2.webp")
    tile_3 = client.get(f"/recordings/{'d5' * 32}/detail-spectrogram/3.webp")

    assert tile_0.status_code == tile_1.status_code == tile_2.status_code == 200
    assert tile_3.status_code == 404  # only 3 tiles exist for this duration
    image_0 = Image.open(io.BytesIO(tile_0.data))
    image_2 = Image.open(io.BytesIO(tile_2.data))
    assert image_0.width == DETAIL_MAX_TILE_WIDTH_PX
    assert (
        image_2.width
        == round(duration_s * 1000 * DETAIL_PX_PER_MS) - 2 * DETAIL_MAX_TILE_WIDTH_PX
    )


def test_detail_spectrogram_404s_when_the_source_file_is_missing_from_disk(
    engine: Engine,
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archive"
    archive_root.mkdir()
    # No file written at archive_root / "gone.wav" -- missing_since is NOT set (that's a
    # different, already-covered case); this is the "the file just isn't there" case.

    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="d6" * 32,
                path="gone.wav",
                recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
                duration_s=0.02,
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
    response = app.test_client().get(
        f"/recordings/{'d6' * 32}/detail-spectrogram/0.webp"
    )

    assert response.status_code == 404


def test_detail_spectrogram_404s_for_a_truncated_source_file(
    engine: Engine,
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archive"
    archive_root.mkdir()
    wav_bytes = build_wav(
        [(b"fmt ", fmt_payload(256_000)), (b"data", _sine_pcm(duration_s=0.02))],
    )
    # Drop an odd number of trailing bytes: the header still claims the original data length,
    # but the file itself ends mid-sample -- `wave.readframes` returns the (odd-length) bytes
    # actually present rather than raising, so this only fails downstream in
    # `np.frombuffer(raw, dtype=np.int16)`. This is the real corrupt/truncated-file shape, not a
    # synthetic exception.
    (archive_root / "truncated.wav").write_bytes(wav_bytes[:-501])

    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="e7" * 32,
                path="truncated.wav",
                recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
                duration_s=0.02,
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
    response = app.test_client().get(
        f"/recordings/{'e7' * 32}/detail-spectrogram/0.webp"
    )
    assert response.status_code == 404


def test_detail_spectrogram_404s_for_a_header_only_source_file(
    engine: Engine,
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archive"
    archive_root.mkdir()
    # Truncated right after the header: a valid fmt chunk, but an empty data chunk --
    # zero PCM bytes, no wave.Error/EOFError/ValueError from decoding itself.
    wav_bytes = build_wav([(b"fmt ", fmt_payload(256_000)), (b"data", b"")])
    (archive_root / "header_only.wav").write_bytes(wav_bytes)

    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="e9" * 32,
                path="header_only.wav",
                recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
                duration_s=0.02,
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
    response = app.test_client().get(
        f"/recordings/{'e9' * 32}/detail-spectrogram/0.webp"
    )
    assert response.status_code == 404


def test_detail_oscillogram_404s_for_a_truncated_source_file(
    engine: Engine,
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archive"
    archive_root.mkdir()
    wav_bytes = build_wav(
        [(b"fmt ", fmt_payload(256_000)), (b"data", _sine_pcm(duration_s=0.02))],
    )
    (archive_root / "truncated.wav").write_bytes(wav_bytes[:-501])

    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="e8" * 32,
                path="truncated.wav",
                recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
                duration_s=0.02,
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
    response = app.test_client().get(
        f"/recordings/{'e8' * 32}/detail-oscillogram/0.webp"
    )
    assert response.status_code == 404
