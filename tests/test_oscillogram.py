from __future__ import annotations

import struct
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from fledermap.media.oscillogram import OscillogramParams, render_oscillogram
from tests.fixtures import build_wav, fmt_payload


def _sine_wav(
    path: Path,
    *,
    freq_hz: float = 45_000.0,
    samplerate: int = 256_000,
    duration_s: float = 0.05,
    amplitude: int = 32000,
) -> None:
    import math

    n_samples = int(samplerate * duration_s)
    samples = [
        int(amplitude * math.sin(2 * math.pi * freq_hz * i / samplerate))
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
    out_path = tmp_path / "oscillogram.webp"
    params = OscillogramParams(width_px=200, height_px=60)

    render_oscillogram(wav_path, out_path, params=params)

    with Image.open(out_path) as img:
        assert img.format == "WEBP"
        assert img.size == (200, 60)


def test_default_params_produce_the_default_dimensions(tmp_path: Path) -> None:
    wav_path = tmp_path / "call.wav"
    _sine_wav(wav_path)
    out_path = tmp_path / "oscillogram.webp"

    render_oscillogram(wav_path, out_path)

    with Image.open(out_path) as img:
        assert img.size == (
            OscillogramParams().width_px,
            OscillogramParams().height_px,
        )


def test_amplitude_is_normalised_to_the_recordings_own_peak(tmp_path: Path) -> None:
    """A near-silent recording must still be VISIBLE -- normalised to its
    own peak, not a fixed int16-range reference, or a quiet call would
    render as a flat, invisible line even though it's the whole point of
    looking at this recording. A loud and a quiet (but non-silent) signal
    should therefore use a comparably wide vertical span, both close to the
    full available height."""
    loud_path = tmp_path / "loud.wav"
    quiet_path = tmp_path / "quiet.wav"
    _sine_wav(loud_path, amplitude=32000)
    _sine_wav(quiet_path, amplitude=500)

    loud_out = tmp_path / "loud.webp"
    quiet_out = tmp_path / "quiet.webp"
    params = OscillogramParams(height_px=48)
    render_oscillogram(loud_path, loud_out, params=params)
    render_oscillogram(quiet_path, quiet_out, params=params)

    def lit_row_span(path: Path) -> int:
        with Image.open(path) as img:
            pixels = np.asarray(img.convert("L"))
        # The line is drawn dark against a light background -- "lit" rows
        # are the ones containing at least one dark (line) pixel.
        lit_rows = np.where(pixels.min(axis=1) < 200)[0]
        return int(lit_rows.max() - lit_rows.min()) if lit_rows.size else 0

    # Both use most of the 48px height (a full-scale sine peaks at +-1, so
    # the theoretical max span is close to but under the full height).
    assert lit_row_span(loud_out) > 40
    assert lit_row_span(quiet_out) > 40


def test_silence_renders_a_flat_centre_line(tmp_path: Path) -> None:
    wav_path = tmp_path / "silence.wav"
    channels, bits, samplerate = 1, 16, 256_000
    n_samples = int(samplerate * 0.05)
    pcm = b"\x00\x00" * n_samples
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
    wav_path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    out_path = tmp_path / "oscillogram.webp"

    render_oscillogram(wav_path, out_path)  # must not raise (no div-by-zero)

    with Image.open(out_path) as img:
        pixels = np.asarray(img.convert("L"))
        lit_rows = np.where(pixels.min(axis=1) < 200)[0]
        # Only the flat centre line itself may be lit, nothing above/below it.
        assert lit_rows.size > 0
        assert (lit_rows.max() - lit_rows.min()) <= 2


def test_params_hash_changes_when_any_field_changes() -> None:
    base = OscillogramParams()
    changed = OscillogramParams(width_px=base.width_px + 1)

    assert base.params_hash != changed.params_hash


def test_params_hash_is_stable_for_equal_params() -> None:
    a = OscillogramParams(width_px=999)
    b = OscillogramParams(width_px=999)

    assert a.params_hash == b.params_hash


def test_a_very_short_recording_renders_without_warning(
    tmp_path: Path,
    recwarn: pytest.WarningsRecorder,
) -> None:
    wav_path = tmp_path / "tiny.wav"
    _sine_wav(wav_path, samplerate=256_000, duration_s=32 / 256_000)
    out_path = tmp_path / "oscillogram.webp"

    render_oscillogram(wav_path, out_path)  # must not raise

    assert len(recwarn) == 0, [str(w.message) for w in recwarn]


def test_writes_atomically_leaving_no_temp_file_behind(tmp_path: Path) -> None:
    wav_path = tmp_path / "call.wav"
    _sine_wav(wav_path)
    out_path = tmp_path / "oscillogram.webp"

    render_oscillogram(wav_path, out_path)

    leftover = [p for p in tmp_path.iterdir() if p != wav_path and p != out_path]
    assert leftover == []


def _constant_amplitude_wav(
    path: Path,
    *,
    amplitude: int,
    samplerate: int = 256_000,
    duration_s: float = 0.05,
) -> None:
    """A flat-amplitude square-ish wave (alternating +amplitude/-amplitude) -- simpler than a
    sine tone and sufficient for testing peak-normalization width, not spectral content."""
    n_samples = int(samplerate * duration_s)
    samples = [amplitude if i % 2 == 0 else -amplitude for i in range(n_samples)]
    pcm = struct.pack(f"<{n_samples}h", *samples)
    fmt = fmt_payload(samplerate)
    path.write_bytes(build_wav([(b"fmt ", fmt), (b"data", pcm)]))


def _two_amplitude_wav(
    path: Path,
    *,
    quiet_amplitude: int = 2_000,
    loud_amplitude: int = 32_000,
    samplerate: int = 256_000,
    half_duration_s: float = 0.05,
) -> None:
    n_half = int(samplerate * half_duration_s)
    quiet = [quiet_amplitude if i % 2 == 0 else -quiet_amplitude for i in range(n_half)]
    loud = [loud_amplitude if i % 2 == 0 else -loud_amplitude for i in range(n_half)]
    samples = quiet + loud
    pcm = struct.pack(f"<{len(samples)}h", *samples)
    fmt = fmt_payload(samplerate)
    path.write_bytes(build_wav([(b"fmt ", fmt), (b"data", pcm)]))


def test_render_oscillogram_time_range_produces_the_requested_pixel_width(
    tmp_path: Path,
) -> None:
    wav_path = tmp_path / "call.wav"
    _constant_amplitude_wav(wav_path, amplitude=20_000, duration_s=0.1)
    out_path = tmp_path / "tile.webp"

    render_oscillogram(
        wav_path,
        out_path,
        params=OscillogramParams(width_px=64, height_px=20),
        time_range_s=(0.0, 0.05),
    )

    image = Image.open(out_path)
    assert image.size == (64, 20)


def test_render_oscillogram_time_range_normalizes_to_the_whole_file_peak(
    tmp_path: Path,
) -> None:
    combined_path = tmp_path / "combined.wav"
    _two_amplitude_wav(combined_path)

    quiet_only_path = tmp_path / "quiet_only.wav"
    _two_amplitude_wav(
        quiet_only_path, loud_amplitude=2_000
    )  # both halves quiet in this file

    sliced_out = tmp_path / "sliced.webp"
    render_oscillogram(
        combined_path,
        sliced_out,
        params=OscillogramParams(width_px=50, height_px=20),
        time_range_s=(0.0, 0.05),
    )

    standalone_out = tmp_path / "standalone.webp"
    render_oscillogram(
        quiet_only_path,
        standalone_out,
        params=OscillogramParams(width_px=50, height_px=20),
        time_range_s=(0.0, 0.05),
    )

    sliced_pixels = np.array(Image.open(sliced_out).convert("L"), dtype=np.float64)
    standalone_pixels = np.array(
        Image.open(standalone_out).convert("L"), dtype=np.float64
    )

    # Default line_color is black (0,0,0), background is white (255,255,255). A waveform
    # normalized to a much louder whole-file peak draws a SMALLER excursion from the midline --
    # more background (light) pixels remain, so mean brightness is HIGHER than the same quiet
    # content normalized to its own (equally quiet) peak.
    assert sliced_pixels.mean() > standalone_pixels.mean()
