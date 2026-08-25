from __future__ import annotations

import math
import struct
from pathlib import Path

import pytest
from PIL import Image

from fledermap.media.spectrogram import SpectrogramParams, render_spectrogram


def _sine_wav(
    path: Path,
    *,
    freq_hz: float = 45_000.0,
    samplerate: int = 256_000,
    duration_s: float = 0.05,
) -> None:
    """A real, non-silent 16-bit mono PCM WAV -- a synthesized bat-call-range
    tone, not all-zero bytes, so the STFT has real structure to render."""
    n_samples = int(samplerate * duration_s)
    samples = [
        int(32000 * math.sin(2 * math.pi * freq_hz * i / samplerate))
        for i in range(n_samples)
    ]
    pcm = struct.pack(f"<{n_samples}h", *samples)

    channels, bits = 1, 16
    byte_rate = samplerate * channels * bits // 8
    block_align = channels * bits // 8
    fmt_payload = struct.pack(
        "<HHIIHH",
        1,
        channels,
        samplerate,
        byte_rate,
        block_align,
        bits,
    )

    def chunk(chunk_id: bytes, payload: bytes) -> bytes:
        out = chunk_id + struct.pack("<I", len(payload)) + payload
        if len(payload) % 2:
            out += b"\x00"
        return out

    body = b"WAVE" + chunk(b"fmt ", fmt_payload) + chunk(b"data", pcm)
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)


def test_renders_a_webp_of_the_configured_dimensions(tmp_path: Path) -> None:
    wav_path = tmp_path / "call.wav"
    _sine_wav(wav_path)
    out_path = tmp_path / "spectrogram.webp"
    params = SpectrogramParams(width_px=256, height_px=128)

    render_spectrogram(wav_path, out_path, params=params)

    with Image.open(out_path) as img:
        assert img.format == "WEBP"
        assert img.size == (256, 128)


def test_default_params_produce_the_default_dimensions(tmp_path: Path) -> None:
    wav_path = tmp_path / "call.wav"
    _sine_wav(wav_path)
    out_path = tmp_path / "spectrogram.webp"

    render_spectrogram(wav_path, out_path)

    with Image.open(out_path) as img:
        assert img.size == (
            SpectrogramParams().width_px,
            SpectrogramParams().height_px,
        )


def test_params_hash_changes_when_any_field_changes() -> None:
    base = SpectrogramParams()
    changed = SpectrogramParams(width_px=base.width_px + 1)

    assert base.params_hash != changed.params_hash


def test_params_hash_is_stable_for_equal_params() -> None:
    a = SpectrogramParams(width_px=999)
    b = SpectrogramParams(width_px=999)

    assert a.params_hash == b.params_hash


def test_clamps_max_freq_to_the_recordings_own_nyquist_limit(tmp_path: Path) -> None:
    """A recording at 44.1 kHz (an ordinary, non-ultrasonic sample rate) has a
    Nyquist limit of 22.05 kHz -- far below the 128 kHz default. This must not
    crash or silently render garbage for the requested-but-nonexistent upper
    frequency range; it must render successfully, using its own real limit."""
    wav_path = tmp_path / "low_rate.wav"
    _sine_wav(wav_path, freq_hz=8_000.0, samplerate=44_100, duration_s=0.1)
    out_path = tmp_path / "spectrogram.webp"

    render_spectrogram(wav_path, out_path)  # must not raise

    with Image.open(out_path) as img:
        assert img.size == (
            SpectrogramParams().width_px,
            SpectrogramParams().height_px,
        )


def test_a_very_short_recording_renders_without_warning(
    tmp_path: Path,
    recwarn: pytest.WarningsRecorder,
) -> None:
    """32 samples at 256 kHz (0.125 ms) -- shorter than even one default
    3 ms analysis window. This is the exact shape of the CLI's own shared
    `_archive()` test fixture (tests/test_cli.py), which writes recordings
    this short. Without clamping nperseg to the signal's own length,
    scipy.signal.spectrogram silently shrinks it but raises a UserWarning
    doing so -- a defect under this project's pristine-test-output rule."""
    wav_path = tmp_path / "tiny.wav"
    _sine_wav(wav_path, samplerate=256_000, duration_s=32 / 256_000)
    out_path = tmp_path / "spectrogram.webp"

    render_spectrogram(wav_path, out_path)  # must not raise

    assert len(recwarn) == 0, [str(w.message) for w in recwarn]


def test_writes_atomically_leaving_no_temp_file_behind(tmp_path: Path) -> None:
    wav_path = tmp_path / "call.wav"
    _sine_wav(wav_path)
    out_path = tmp_path / "spectrogram.webp"

    render_spectrogram(wav_path, out_path)

    leftover = [p for p in tmp_path.iterdir() if p != wav_path and p != out_path]
    assert leftover == []
