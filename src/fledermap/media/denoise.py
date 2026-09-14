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
