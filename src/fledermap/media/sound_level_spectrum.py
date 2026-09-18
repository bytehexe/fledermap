"""On-demand sound-level spectrum for the recording-details page's Analysis view (design spec
docs/superpowers/specs/2026-09-18-fledermap-analysis-view-design.md §5). Pure, no I/O -- unlike
`spectrogram.py`/`oscillogram.py`, this returns data for a JSON endpoint to serialize, not a
rendered image, since it's computed fresh per request rather than cached derived media."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.signal import welch


@dataclass(frozen=True)
class SoundLevelSpectrum:
    freqs_hz: np.ndarray
    db: np.ndarray  # relative to this spectrum's own peak -- 0 dB at the peak, matching
    # spectrogram.py's self-relative convention (CLAUDE.md's "Derived media rendering").
    peak_freq_hz: float


def compute_sound_level_spectrum(
    samples: np.ndarray,
    samplerate_hz: float,
    dynamic_range_db: float = 80.0,
) -> SoundLevelSpectrum:
    # `nperseg` capped at the sample count: scipy.signal.welch's own default (256) raises for a
    # shorter input (e.g. a narrow Select-tool span), and Welch's method needs at least one full
    # segment to produce any output at all.
    nperseg = min(256, len(samples))
    freqs_hz, psd = welch(samples, fs=samplerate_hz, nperseg=nperseg)
    peak_index = int(np.argmax(psd))
    peak_freq_hz = float(freqs_hz[peak_index])
    db = 10 * np.log10(np.maximum(psd, 1e-300) / psd[peak_index])
    db = np.clip(db, -dynamic_range_db, 0.0)
    return SoundLevelSpectrum(freqs_hz=freqs_hz, db=db, peak_freq_hz=peak_freq_hz)
