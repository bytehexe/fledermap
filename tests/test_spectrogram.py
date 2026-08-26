from __future__ import annotations

import math
import struct
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from fledermap.media.spectrogram import (
    SpectrogramParams,
    effective_max_freq_hz,
    render_spectrogram,
)


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


def test_renders_rgb_not_grayscale(tmp_path: Path) -> None:
    """The palette maps normalised power to colour, not a single grey
    channel -- a loud tone must produce a pixel with unequal R/G/B (e.g. the
    palette's yellow/red end), not a neutral grey triple."""
    wav_path = tmp_path / "call.wav"
    _sine_wav(wav_path)
    out_path = tmp_path / "spectrogram.webp"

    render_spectrogram(wav_path, out_path)

    with Image.open(out_path) as img:
        assert img.mode == "RGB"
        pixels = np.asarray(img)
        # At least one pixel must be distinctly non-grey (the loud tone's
        # peak, coloured by the palette) -- proves colour mapping actually
        # happened rather than R==G==B everywhere (a grey image saved as RGB).
        spread = pixels.max(axis=-1).astype(int) - pixels.min(axis=-1).astype(int)
        assert spread.max() > 20


def test_quiet_background_is_not_washed_out_to_near_white(tmp_path: Path) -> None:
    """Regression test for the log1p bug: a signal with a loud transient
    tone against an otherwise-quiet background must render the quiet
    background near the palette's floor colour (black), not washed toward
    the bright end -- `log1p` on power values well below 1.0 barely
    distinguishes them from the loud tone once min-max normalised, so most
    of the image used to render either near-uniformly bright or with the
    entire noise floor crushed into a razor-thin band at the very bottom of
    the range. A proper dB-relative-to-peak scale with a fixed dynamic-range
    floor keeps the noise floor visibly dark."""
    wav_path = tmp_path / "call.wav"
    _sine_wav(wav_path, duration_s=0.2)
    out_path = tmp_path / "spectrogram.webp"

    render_spectrogram(wav_path, out_path)

    with Image.open(out_path) as img:
        pixels = np.asarray(img).astype(int)
        brightness = pixels.sum(axis=-1)  # 0 (black) .. 765 (white)
        # The tone occupies a narrow frequency band; most of the image-time
        # rows are far from it and must render dark (near the palette floor).
        dark_fraction = (brightness < 100).mean()
        assert dark_fraction > 0.5, (
            f"only {dark_fraction:.0%} of pixels near-black -- background "
            "isn't staying near the palette floor"
        )


def test_silence_renders_solid_black(tmp_path: Path) -> None:
    """All-zero PCM must not divide by zero (peak power is 0) and must
    render as the palette's floor colour throughout."""
    wav_path = tmp_path / "silence.wav"
    channels, bits, samplerate = 1, 16, 256_000
    n_samples = int(samplerate * 0.05)
    pcm = b"\x00\x00" * n_samples
    byte_rate = samplerate * channels * bits // 8
    block_align = channels * bits // 8
    fmt_payload = struct.pack(
        "<HHIIHH", 1, channels, samplerate, byte_rate, block_align, bits
    )

    def chunk(chunk_id: bytes, payload: bytes) -> bytes:
        out = chunk_id + struct.pack("<I", len(payload)) + payload
        if len(payload) % 2:
            out += b"\x00"
        return out

    body = b"WAVE" + chunk(b"fmt ", fmt_payload) + chunk(b"data", pcm)
    wav_path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    out_path = tmp_path / "spectrogram.webp"

    render_spectrogram(wav_path, out_path)  # must not raise (no div-by-zero)

    with Image.open(out_path) as img:
        pixels = np.asarray(img)
        assert pixels.max() == 0


def test_dynamic_range_db_participates_in_params_hash() -> None:
    base = SpectrogramParams()
    changed = SpectrogramParams(dynamic_range_db=base.dynamic_range_db + 1)

    assert base.params_hash != changed.params_hash


def test_palette_participates_in_params_hash() -> None:
    base = SpectrogramParams()
    changed = SpectrogramParams(palette="some_other_palette")

    assert base.params_hash != changed.params_hash


def test_effective_max_freq_hz_clamps_to_nyquist() -> None:
    assert effective_max_freq_hz(44_100, SpectrogramParams()) == 22_050.0


def test_effective_max_freq_hz_respects_the_params_ceiling() -> None:
    assert effective_max_freq_hz(256_000, SpectrogramParams()) == 128_000.0


def test_writes_atomically_leaving_no_temp_file_behind(tmp_path: Path) -> None:
    wav_path = tmp_path / "call.wav"
    _sine_wav(wav_path)
    out_path = tmp_path / "spectrogram.webp"

    render_spectrogram(wav_path, out_path)

    leftover = [p for p in tmp_path.iterdir() if p != wav_path and p != out_path]
    assert leftover == []
