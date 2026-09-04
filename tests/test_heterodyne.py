from __future__ import annotations

import math
import struct
import subprocess
import wave
from pathlib import Path

import numpy as np

from fledermap.media.heterodyne import (
    compute_peak_frequency_hz,
    render_heterodyne_preview,
)
from tests.fixtures import build_wav, fmt_payload


def _sine_wav(
    path: Path, *, freq_hz: float, samplerate: int = 256_000, duration_s: float = 0.05
) -> None:
    # cos(), not sin(): a real (non-quadrature) heterodyne mixer's DC output
    # is proportional to cos(phase_signal - phase_LO). render_heterodyne_preview's
    # local oscillator is a cos() at the tune frequency (see heterodyne.py), so a
    # sin()-phased test tone sits exactly 90 degrees off it whenever
    # tune_freq_hz == freq_hz -- the wanted beat term collapses to
    # sin(0) == 0 identically (a trig identity, not a tolerance issue), and the
    # "correctly tuned" test would deterministically fail on numerical noise
    # instead of the real near-DC signal. Matching phase with cos() here keeps
    # this a real behavioural test rather than an artifact of the two
    # generators' independent phase choice.
    n_samples = int(samplerate * duration_s)
    pcm = struct.pack(
        f"<{n_samples}h",
        *(
            int(20000 * math.cos(2 * math.pi * freq_hz * i / samplerate))
            for i in range(n_samples)
        ),
    )
    path.write_bytes(
        build_wav([(b"fmt ", fmt_payload(samplerate)), (b"data", pcm)]),
    )


def test_compute_peak_frequency_hz_finds_a_known_single_tone(tmp_path: Path) -> None:
    wav_path = tmp_path / "tone.wav"
    _sine_wav(wav_path, freq_hz=40_000.0)

    peak = compute_peak_frequency_hz(wav_path)

    # Welch's PSD has finite frequency resolution -- close, not exact.
    assert 38_000.0 < peak < 42_000.0


def _two_tone_wav(
    path: Path,
    *,
    loud_freq_hz: float,
    loud_amplitude: float,
    quiet_freq_hz: float,
    quiet_amplitude: float,
    samplerate: int = 256_000,
    duration_s: float = 0.05,
) -> None:
    """Mixes two sine tones into ONE file (summed samples, not two separate
    writes -- writing `_sine_wav` twice at the same path would overwrite
    rather than mix)."""

    n_samples = int(samplerate * duration_s)
    samples = [
        int(
            loud_amplitude * math.sin(2 * math.pi * loud_freq_hz * i / samplerate)
            + quiet_amplitude * math.sin(2 * math.pi * quiet_freq_hz * i / samplerate)
        )
        for i in range(n_samples)
    ]
    pcm = struct.pack(f"<{n_samples}h", *samples)
    path.write_bytes(build_wav([(b"fmt ", fmt_payload(samplerate)), (b"data", pcm)]))


def test_compute_peak_frequency_hz_ignores_a_louder_tone_below_the_search_window(
    tmp_path: Path,
) -> None:
    """A real recording's low end (below ~10kHz) can carry handling/wind noise loud enough to
    dominate a raw argmax -- the bounded search window (spec §1) must reject it even when it's
    the objectively loudest component in the file. Mixes a quiet 40kHz tone (the "real call",
    in-window) with a much louder 2kHz tone (the "noise", below the window) into one file --
    without the window bound, the 2kHz tone's far greater amplitude would dominate a raw argmax
    and get reported as the peak instead."""
    wav_path = tmp_path / "tone.wav"
    _two_tone_wav(
        wav_path,
        loud_freq_hz=2_000.0,
        loud_amplitude=30_000.0,
        quiet_freq_hz=40_000.0,
        quiet_amplitude=3_000.0,
    )

    peak = compute_peak_frequency_hz(wav_path)

    assert 38_000.0 < peak < 42_000.0


def _read_opus_as_mono_float(path: Path) -> tuple[np.ndarray, int]:
    """Decode an Opus file back to raw PCM via ffmpeg for FFT analysis in
    tests -- there's no pure-Python opus decoder already in this project's
    dependencies, and shelling out to ffmpeg is exactly what production code
    already does the other direction."""
    raw_wav = path.with_suffix(".decoded.wav")
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(path), "-ac", "1", str(raw_wav)],
        check=True,
        capture_output=True,
    )
    with wave.open(str(raw_wav), "rb") as wav:
        samplerate = wav.getframerate()
        frames = wav.readframes(wav.getnframes())
    samples = np.frombuffer(frames, dtype=np.int16).astype(np.float64)
    return samples, samplerate


def _dominant_frequency_hz(samples: np.ndarray, samplerate: int) -> float:
    windowed = samples * np.hanning(len(samples))
    spectrum = np.abs(np.fft.rfft(windowed))
    freqs = np.fft.rfftfreq(len(samples), d=1.0 / samplerate)
    return float(freqs[np.argmax(spectrum)])


def test_render_heterodyne_preview_correctly_tuned_produces_a_near_dc_beat(
    tmp_path: Path,
) -> None:
    tone_freq_hz = 40_000.0
    wav_path = tmp_path / "tone.wav"
    _sine_wav(wav_path, freq_hz=tone_freq_hz, samplerate=256_000, duration_s=0.1)
    out_path = tmp_path / "het.opus"

    render_heterodyne_preview(wav_path, out_path, tune_freq_hz=tone_freq_hz)

    samples, samplerate = _read_opus_as_mono_float(out_path)
    dominant = _dominant_frequency_hz(samples, samplerate)
    # A correctly-tuned heterodyne mix produces a near-DC beat -- allow a
    # few hundred Hz of slack for FFT bin width and low-pass filter roll-off.
    assert dominant < 500.0


def test_render_heterodyne_preview_mistuned_produces_a_beat_near_the_offset(
    tmp_path: Path,
) -> None:
    tone_freq_hz = 40_000.0
    offset_hz = 3_000.0
    wav_path = tmp_path / "tone.wav"
    _sine_wav(wav_path, freq_hz=tone_freq_hz, samplerate=256_000, duration_s=0.1)
    out_path = tmp_path / "het.opus"

    render_heterodyne_preview(wav_path, out_path, tune_freq_hz=tone_freq_hz - offset_hz)

    samples, samplerate = _read_opus_as_mono_float(out_path)
    dominant = _dominant_frequency_hz(samples, samplerate)
    assert abs(dominant - offset_hz) < 500.0


def test_render_heterodyne_preview_output_is_a_real_nonempty_opus_file(
    tmp_path: Path,
) -> None:
    wav_path = tmp_path / "tone.wav"
    _sine_wav(wav_path, freq_hz=40_000.0)
    out_path = tmp_path / "het.opus"

    render_heterodyne_preview(wav_path, out_path, tune_freq_hz=40_000.0)

    assert out_path.exists()
    assert out_path.stat().st_size > 0
