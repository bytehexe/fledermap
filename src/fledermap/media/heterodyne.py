"""Heterodyne (HET) preview generation and the "peak frequency" helper that
gives HET mode a sensible starting tune value. Pure: reads a WAV file, writes
an Opus file / returns a float. No DB, no queue awareness, matching
`preview.py`'s module shape (design spec
2026-09-04-fledermap-het-playback-design.md section 1)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from scipy import signal

from fledermap.media.opus_pipeline import encode_pcm_as_opus
from fledermap.media.wav_pcm import read_pcm

# Bounds the peak-frequency search window -- NOT a real bandpass filter (a
# bigger, separate design question the already-planned `fledermap.noise`
# classifier/denoising backlog items exist for). Reuses the same low-end
# reasoning this codebase already documents (project CLAUDE.md's "Noise"
# backlog notes: real recordings' low end, below ~10kHz, often carries
# handling/wind noise that would otherwise dominate a naive argmax and mask
# the actual call) and the spectrogram's own 128kHz display ceiling
# (`SpectrogramParams.max_freq_hz`) as the high end, so "peak frequency" and
# "what the spectrogram actually shows" never silently disagree about range.
_PEAK_SEARCH_MIN_HZ = 10_000.0
_PEAK_SEARCH_MAX_HZ = 128_000.0


def compute_peak_frequency_hz(wav_path: Path) -> float:
    """Welch power spectral density over the whole file, returning the
    frequency of maximum power WITHIN the bounded search window above --
    deliberately independent of `SpectrogramParams`/
    `render_full_spectrogram_image`'s own STFT: changing the spectrogram's
    display tuning (window/overlap, chosen for visualization) must never
    silently change what HET calls "the peak frequency" (chosen for
    audibility). The result is directly visible to the user (pre-filled
    into the frequency spinner in HET mode), so a wrong pick is easy to
    catch by ear against real recordings."""
    samples, samplerate = read_pcm(wav_path)
    freqs, psd = signal.welch(samples, fs=samplerate)
    in_window = (freqs >= _PEAK_SEARCH_MIN_HZ) & (freqs <= _PEAK_SEARCH_MAX_HZ)
    windowed_freqs = freqs[in_window]
    windowed_psd = psd[in_window]
    return float(windowed_freqs[np.argmax(windowed_psd)])


# Rejects the near-`2*tune_freq_hz` sum-frequency component the mix also
# produces, keeping only the audible difference-frequency component. 20kHz
# comfortably covers human hearing while staying well below any plausible
# sum-frequency artifact given the tune frequencies this feature targets
# (bat calls, tens of kHz and up).
_LOWPASS_CUTOFF_HZ = 20_000.0
_LOWPASS_ORDER = 8
_OUTPUT_SAMPLERATE_HZ = 48_000


def render_heterodyne_preview(
    wav_path: Path,
    out_path: Path,
    *,
    tune_freq_hz: float,
) -> None:
    """Mix `wav_path`'s audio down to audible range around `tune_freq_hz`
    (classic heterodyne technique) and render it to `out_path` as Opus."""
    samples, samplerate = read_pcm(wav_path)
    t = np.arange(len(samples)) / samplerate
    local_oscillator = np.cos(2 * np.pi * tune_freq_hz * t)
    mixed = samples * local_oscillator

    sos = signal.butter(
        _LOWPASS_ORDER,
        _LOWPASS_CUTOFF_HZ,
        btype="low",
        fs=samplerate,
        output="sos",
    )
    filtered = signal.sosfiltfilt(sos, mixed)

    resampled = signal.resample_poly(filtered, _OUTPUT_SAMPLERATE_HZ, samplerate)
    # Normalise to int16 range headroom-safe -- the mix + filter can produce
    # values outside the original PCM's amplitude range.
    peak = np.max(np.abs(resampled))
    if peak > 0:
        resampled = resampled / peak * 32000
    pcm_int16 = resampled.astype(np.int16)

    encode_pcm_as_opus(
        frames=pcm_int16.tobytes(),
        nchannels=1,
        sampwidth=2,
        framerate=_OUTPUT_SAMPLERATE_HZ,
        out_path=out_path,
    )
