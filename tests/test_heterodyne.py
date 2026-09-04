from __future__ import annotations

import math
import struct
import subprocess
import wave
from pathlib import Path

import numpy as np
import pytest

from fledermap.media.heterodyne import (
    compute_peak_frequency_hz,
    render_heterodyne_preview,
)
from fledermap.media.wav_pcm import UnreadableWavError
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


def test_compute_peak_frequency_hz_raises_for_a_samplerate_below_the_search_window(
    tmp_path: Path,
) -> None:
    wav_path = tmp_path / "low_samplerate.wav"
    # samplerate/2 (Nyquist) is well under _PEAK_SEARCH_MIN_HZ (10kHz), so the
    # whole windowed PSD is empty.
    _sine_wav(wav_path, freq_hz=1_000.0, samplerate=8_000)

    with pytest.raises(UnreadableWavError):
        compute_peak_frequency_hz(wav_path)


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


def _persistent_tone_plus_brief_burst_wav(
    path: Path,
    *,
    persistent_freq_hz: float,
    persistent_amplitude: float,
    burst_freq_hz: float,
    burst_amplitude: float,
    burst_fraction: float,
    samplerate: int = 256_000,
    duration_s: float = 0.3,
) -> None:
    """A quiet tone present for the WHOLE file, plus a brief, louder burst
    near the end -- the shape of a real recording with persistent background
    noise and a short, loud bat call (Janna, 2026-09-04, found against a
    real field recording: a near-continuous 10-11kHz noise floor across
    ~70% of the file outweighed a brief, loud ~45kHz Pipistrellus call under
    a time-averaged PSD). `burst_fraction` controls how much of the file the
    burst covers, unlike `_two_tone_wav` above where both tones run the
    full duration."""
    n_samples = int(samplerate * duration_s)
    burst_start = int(n_samples * (1 - burst_fraction))
    samples = []
    for i in range(n_samples):
        value = persistent_amplitude * math.sin(
            2 * math.pi * persistent_freq_hz * i / samplerate
        )
        if i >= burst_start:
            value += burst_amplitude * math.sin(
                2 * math.pi * burst_freq_hz * i / samplerate
            )
        samples.append(int(value))
    pcm = struct.pack(f"<{n_samples}h", *samples)
    path.write_bytes(build_wav([(b"fmt ", fmt_payload(samplerate)), (b"data", pcm)]))


def test_compute_peak_frequency_hz_prefers_a_brief_loud_call_over_persistent_quiet_noise(
    tmp_path: Path,
) -> None:
    """The failure mode found live (2026-09-04) against a real recording: a
    persistent low-level noise floor beats a brief, loud call under a
    time-averaged PSD, simply by being present for nearly the whole file.
    Reproduces the same shape synthetically -- a quiet tone for the entire
    duration, a much louder tone for only the last 2% -- and asserts the
    peak lands on the brief loud call, not the persistent quiet noise."""
    wav_path = tmp_path / "tone.wav"
    _persistent_tone_plus_brief_burst_wav(
        wav_path,
        persistent_freq_hz=11_000.0,
        persistent_amplitude=3_000.0,
        burst_freq_hz=45_000.0,
        burst_amplitude=20_000.0,
        burst_fraction=0.02,
    )

    peak = compute_peak_frequency_hz(wav_path)

    assert 43_000.0 < peak < 47_000.0


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


def test_render_heterodyne_preview_raises_for_a_samplerate_too_low_for_the_lowpass(
    tmp_path: Path,
) -> None:
    wav_path = tmp_path / "low_samplerate.wav"
    # 20kHz samplerate: Nyquist (10kHz) is below the 20kHz lowpass cutoff.
    _sine_wav(wav_path, freq_hz=1_000.0, samplerate=20_000)
    out_path = tmp_path / "het.opus"

    with pytest.raises(UnreadableWavError):
        render_heterodyne_preview(wav_path, out_path, tune_freq_hz=1_000.0)
