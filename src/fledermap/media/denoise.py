"""Highpass filtering and spectrogram-only background-noise subtraction for
the "Denoise" toggle (design spec
docs/superpowers/specs/2026-09-14-fledermap-denoise-highpass-design.md). Pure:
no DB, no queue awareness, matching every other `media/` module.

`DEFAULT_CUTOFF_HZ` is the one shared constant every caller (SpectrogramParams,
OscillogramParams, the TE/HET routes) defaults to -- a single definition so the
cutoff can't drift between callers."""

from __future__ import annotations

import numpy as np
from scipy import signal

DEFAULT_CUTOFF_HZ = 5000.0

# A conventional order balancing rolloff steepness against filter-design edge
# cases at a low cutoff-to-Nyquist ratio (this project's recordings run at
# 256kHz+ sample rates, so a 5kHz cutoff is a small fraction of Nyquist).
_HIGHPASS_ORDER = 4


def highpass_filter(
    samples: np.ndarray,
    samplerate_hz: float,
    cutoff_hz: float,
) -> np.ndarray:
    """Zero-phase Butterworth highpass (`sosfiltfilt`, not `sosfilt` -- a
    causal filter would time-shift a sharp transient, desyncing the filtered
    audio from the unfiltered spectrogram/oscillogram's own time axis).
    Mirrors `heterodyne.py`'s existing `butter` + `sosfiltfilt` lowpass
    idiom, the same established pattern in this codebase."""
    sos = signal.butter(
        _HIGHPASS_ORDER,
        cutoff_hz,
        btype="high",
        fs=samplerate_hz,
        output="sos",
    )
    result: np.ndarray = signal.sosfiltfilt(sos, samples)
    return result


def subtract_background_noise(sxx: np.ndarray, percentile: float = 10.0) -> np.ndarray:
    """Per frequency-bin (per row) background-noise subtraction: estimate
    each bin's noise floor as its own `percentile`-th percentile power
    across all time columns, subtract it from every column in that row,
    clamp at zero. Operates on the STFT's linear power matrix, before any
    dB conversion -- spectrogram-image-only (see the design spec's "Why
    highpass and background-subtraction use different techniques": this
    is not reconstructed back to audio, so no phase/COLA concern applies)."""
    noise_floor = np.percentile(sxx, percentile, axis=1, keepdims=True)
    return np.maximum(sxx - noise_floor, 0.0)
