"""Heterodyne (HET) preview generation and the "peak frequency" helper that
gives HET mode a sensible starting tune value. Pure: reads a WAV file, writes
an Opus file / returns a float. No DB, no queue awareness, matching
`preview.py`'s module shape (design spec
2026-09-04-fledermap-het-playback-design.md section 1)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from scipy import signal

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
